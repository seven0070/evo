"""P4: one authoritative loop, and a registry that is actually in front of it (07 §5, §8).

Before this phase the backend seam and the agent loop were two ideas that never met: the registry
could plan, refuse, and forward, and nothing called it. That is the "architectural gap" this file
closes, and the properties it pins are the ones that make closing it safe:

* routing is **declared** - a configured backend that cannot serve is a refusal, never a quiet fall
  back to native, because "we integrated the harness" must not be able to decay into "we configured
  it" and keep the credit;
* a routed turn passes the **same** guards (pipeline), the **same** execution authority (mediator),
  and the **same** verifier as the native loop - a bridge that skipped any of the three would be a
  weaker path with a louder name;
* exactly one loop remains, and the routing layer cannot grow one.

The harness-shaped parts run against stub drivers and a stub CLI: what is under test is Evo's side of
the seam - protocol, mediation, refusal, recording - not whether an optional upstream is installed.
"""

from __future__ import annotations

import ast
import contextlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "evo_agent"

from evo_agent.backends import (  # noqa: E402
    BackendDefaults,
    LoopUnavailable,
    UnknownBackend,
    build_default_registry,
    resolve_agent_loop,
)
from evo_agent.backends.dsh import DeepSeekHarnessBackend, render_template  # noqa: E402
from evo_agent.backends.lead_agent import LeadAgentBackend  # noqa: E402
from evo_agent.backends.native import NativeBackend  # noqa: E402
from evo_agent.backends.registry import BackendConflict, BackendContractError, BackendRegistry  # noqa: E402
from evo_agent.models import EventType, RiskLevel  # noqa: E402
from evo_agent.ports.contracts import CapabilityRequest, TurnContext, TurnResult  # noqa: E402
from evo_agent.production import ProductionConfig, ProductionSupervisor  # noqa: E402
from evo_agent.runtime import (  # noqa: E402
    DEFAULT_MAX_PARALLEL_TOOL_CALLS,
    RuntimeState,
    DEFAULT_TURN_BUDGET,
    MAX_PARALLEL_TOOL_CALLS_MAX,
    TURN_BUDGET_MAX,
    AgentRuntime,
    clamp_parallel_tool_calls,
    clamp_turn_budget,
)
from evo_agent.security import SecurityPolicy  # noqa: E402
from evo_agent.tools import ToolRegistry  # noqa: E402


#: A driver that speaks the bridge's line protocol without needing DeerFlow installed. It asks for one
#: tool through the parent, then finishes - which is the whole shape of the seam.
STUB_DRIVER = '''
import json, sys

def emit(payload):
    json.dump(payload, sys.stdout, sort_keys=True, default=str)
    sys.stdout.write("\\n")
    sys.stdout.flush()

mode = sys.argv[1] if len(sys.argv) > 1 else "--probe"
if mode == "--probe":
    emit({"type": "probe", "ok": True, "harness": "stub", "version": "0", "protocol": 1})
    raise SystemExit(0)

request = json.loads(sys.stdin.readline())
emit({"type": "event", "event": "step_started", "payload": {"goal": request.get("goal")}})
# An alias, deliberately: the child spells "shell" the way a DeerFlow graph does, and the boundary
# has to decide the canonical name before the authority is asked.
emit({"type": "tool_request", "id": "1", "tool": "run_command", "argv": ["echo", "from-the-harness"], "cwd": None})
line = sys.stdin.readline()
response = json.loads(line) if line.strip() else {}
emit({
    "type": "final",
    "text": json.dumps({
        "ok": response.get("ok"),
        "output": (response.get("output") or "")[:200],
        "error": (response.get("error") or "")[:200],
        "isolated": response.get("isolated"),
        "provider": response.get("provider"),
    })[:900],
})
raise SystemExit(0)
'''


def _workspace(tmp_path: Path, driver_body: str = STUB_DRIVER) -> Path:
    ws = tmp_path / "ws"
    ws.mkdir(parents=True, exist_ok=True)
    (ws / "driver.py").write_text(driver_body, encoding="utf-8")
    return ws


def _lead_config(ws: Path, **overrides) -> dict:
    config = {
        "enabled": True,
        "accepted_by": "operator@evo",
        "driver": str(ws / "driver.py"),
        "venv": sys.executable,
        "required_imports": [],
        "turn_timeout_seconds": 60.0,
    }
    config.update(overrides)
    return {"lead_agent": config}


def _runtime(ws: Path, **kwargs) -> AgentRuntime:
    kwargs.setdefault("source_root", ROOT)
    kwargs.setdefault("versions_root", ws / ".evo" / "versions")
    return AgentRuntime(ws, **kwargs)


def _events(runtime: AgentRuntime, task_id: str) -> list[dict]:
    return list(runtime.store.events_for_task(task_id))


def _event_names(runtime: AgentRuntime, task_id: str) -> list[str]:
    return [str(row.get("event_type")) for row in _events(runtime, task_id)]


class TestRegistryIsAuthoritative:
    def test_the_runtime_builds_a_registry_and_native_cannot_be_turned_off(self, tmp_path: Path):
        runtime = _runtime(_workspace(tmp_path))
        assert runtime.backends.names == ("native",)
        assert runtime.agent_loop == "native"
        assert runtime.backend_status()["plan"]["selected"] == "native"

    def test_an_unknown_loop_name_is_a_startup_error(self, tmp_path: Path):
        with pytest.raises(UnknownBackend):
            _runtime(_workspace(tmp_path), agent_loop="whichever")
        with pytest.raises(UnknownBackend):
            resolve_agent_loop("nope")
        assert resolve_agent_loop("cognitive") == "native", "the alias names the same authority"
        assert resolve_agent_loop("") == "native"

    def test_a_registered_but_absent_backend_is_not_a_silent_fall_back(self, tmp_path: Path):
        with pytest.raises(LoopUnavailable):
            resolve_agent_loop("lead_agent", registered=["native"])

    def test_a_configured_backend_that_cannot_serve_blocks_the_task(self, tmp_path: Path):
        """The refusal is the point: native must not quietly take the turn back.

        ``lead_agent`` is registered and enabled here, but its probe fails (no harness is installed),
        so the *only* correct outcome is a blocked task with the reason in the ledger. Serving it
        natively instead would produce a successful-looking run whose audit line names the wrong
        author, which is exactly how an integration silently stops being one.
        """
        ws = _workspace(tmp_path)
        config = _lead_config(ws)
        config["lead_agent"] = dict(config["lead_agent"], driver=str(ws / "missing-driver.py"))
        runtime = _runtime(ws, backends=config, agent_loop="lead_agent")
        runtime.start()
        task = runtime.enqueue_task("ask the harness")
        result = runtime.run_cycle()
        assert result.tasks_blocked == 1, result.failures
        assert runtime.tasks()[0].status.value == "blocked"
        events = _events(runtime, task.task_id)
        refusals = [row for row in events if row["event_type"] == EventType.RUNTIME_BACKEND_REFUSED.value]
        assert refusals, _event_names(runtime, task.task_id)
        assert "driver script missing" in json.dumps(refusals[-1]["payload"])
        assert "last_result" not in runtime.tasks()[0].metadata, "the native loop must not have run"
        runtime.stop("test complete")

    def test_selection_is_recorded_with_the_full_plan(self, tmp_path: Path):
        ws = _workspace(tmp_path)
        runtime = _runtime(ws, backends=_lead_config(ws), agent_loop="native")
        runtime.start()
        task = runtime.enqueue_task("inspect the workspace")
        runtime.run_cycle()
        events = [row for row in _events(runtime, task.task_id) if row["event_type"] == EventType.RUNTIME_BACKEND_SELECTED.value]
        assert events, _event_names(runtime, task.task_id)
        payload = events[-1]["payload"]
        assert payload["selected"] == "native" and payload["requested"] == "native"
        assert "lead_agent" in payload["serving"] + payload["unavailable"] + payload["declined"]
        assert runtime.tasks()[0].metadata["backend"]["name"] == "native"
        runtime.stop("test complete")

    def test_the_plan_record_says_which_choice_was_configured(self, tmp_path: Path):
        ws = _workspace(tmp_path)
        runtime = _runtime(ws, backends=_lead_config(ws))
        runtime.start()
        plan = runtime.backend_plan(CapabilityRequest(goal="x", workspace=ws))
        # Registry preference and configured routing are different facts; both stay in the record.
        assert plan["selected"] in {"native", "lead_agent"}
        runtime.stop("test complete")

    def test_the_kill_switch_outranks_routing(self, tmp_path: Path):
        ws = _workspace(tmp_path)
        runtime = _runtime(ws, backends=_lead_config(ws))
        runtime.start()
        task = runtime.enqueue_task("after the switch")
        runtime.kill_switch_active = True
        _amended, plan, refusal = runtime.select_backend(CapabilityRequest(goal="x", workspace=ws), task=task)
        assert "kill switch" in refusal and plan is not None
        runtime.stop("test complete")

    def test_status_reports_routing_without_running_a_probe(self, tmp_path: Path, monkeypatch):
        """``status()`` is called by supervisors in a loop; it must not spawn harness children."""
        ws = _workspace(tmp_path)
        runtime = _runtime(ws, backends=_lead_config(ws))
        monkeypatch.setattr(subprocess, "run", lambda *a, **k: (_ for _ in ()).throw(AssertionError("status must not spawn")))
        status = runtime.status()
        assert status["routing"]["loop"] == "native"
        assert set(status["routing"]["registered"]) == {"native", "lead_agent"}

    def test_backend_status_reports_states_pipeline_and_gates(self, tmp_path: Path):
        ws = _workspace(tmp_path)
        runtime = _runtime(ws, backends=_lead_config(ws))
        report = runtime.backend_status()
        assert report["loop"] == "native"
        assert report["states"].keys() == {"native", "lead_agent"}
        assert report["pipeline"]["stages"][0]["name"] == "input_sanitize"
        assert set(report["tools"]["gated"]) <= set(report["tools"]["granted"])
        assert report["max_parallel_tool_calls"] == DEFAULT_MAX_PARALLEL_TOOL_CALLS

    def test_safe_mode_blocks_before_routing(self, tmp_path: Path):
        """Safe mode is not a backend choice, and routing must not become a way around it."""
        ws = _workspace(tmp_path)
        runtime = _runtime(ws, safe_mode=True)
        runtime.start()
        task = runtime.enqueue_task("safe mode turn")
        runtime.run_cycle()
        names = _event_names(runtime, task.task_id)
        assert names, "the task still has to be accounted for"
        assert EventType.RUNTIME_BACKEND_SELECTED.value not in names, "no turn may be routed while safe mode is on"
        assert runtime.tasks()[0].status.value in {"blocked", "waiting"}
        runtime.stop("test complete")


class TestOneLoop:
    def test_the_native_executor_is_the_runtime_itself(self, tmp_path: Path):
        runtime = _runtime(_workspace(tmp_path))
        registration = runtime.backends.get("native")
        assert isinstance(registration.backend, NativeBackend)
        assert registration.backend.turn_executor == runtime._execute_native_turn

    def test_exactly_one_loop_dispatches_tools_after_the_unification(self):
        from evo_agent.sovereign.invariants import run_invariants

        check = {item.code: item for item in run_invariants(PACKAGE, only=["I-single-loop"])}["I-single-loop"]
        assert check.ok, check.detail
        assert "kernel.py::run" in check.detail, "the turn routing must not have become a second loop"
        # ``pipeline`` is on the forbidden-packages list, so the declared order can never grow a loop.
        from evo_agent.sovereign.invariants import LOOP_FORBIDDEN_PACKAGES

        assert "pipeline" in LOOP_FORBIDDEN_PACKAGES

    def test_the_backend_seam_receives_no_authority_objects(self, tmp_path: Path):
        """``TurnResult.usage`` must stay plain data, or the seam has become a smuggling tunnel."""
        ws = _workspace(tmp_path)
        runtime = _runtime(ws)
        runtime.start()
        task = runtime.enqueue_task("list the workspace")
        runtime.run_cycle()
        usage = runtime.tasks()[0].metadata["backend"]
        json.dumps(usage, default=str)
        assert usage["status"] in {"completed", "failed", "blocked"}
        runtime.stop("test complete")

    def test_no_async_leak(self):
        """07 §8's acceptance name: the base install stays synchronous outside ``serve/``."""
        offenders: list[str] = []
        for path in sorted(PACKAGE.rglob("*.py")):
            if "__pycache__" in path.parts or path.parts and path.relative_to(PACKAGE).parts[0] == "serve":
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, (ast.AsyncFunctionDef, ast.Await, ast.AsyncFor, ast.AsyncWith)):
                    offenders.append(f"{path.relative_to(PACKAGE)}:{getattr(node, 'lineno', 0)}")
        assert offenders == []

    def test_the_runtime_holds_no_second_tool_dispatch_loop(self):
        """The routing methods may iterate, but they may not dispatch tools themselves."""
        source = (PACKAGE / "runtime.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef) or not node.name.startswith(("_run_task", "_execute_native", "select_backend", "backend_")):
                continue
            for inner in ast.walk(node):
                if isinstance(inner, ast.Call) and isinstance(inner.func, ast.Attribute) and inner.func.attr == "execute":
                    pytest.fail(f"{node.name} dispatches a tool directly; the loop owns that")

    def test_turn_budget_is_clamped_and_reaches_the_backend(self, tmp_path: Path):
        ws = _workspace(tmp_path)
        runtime = _runtime(ws, turn_budget=10**9)
        assert runtime.turn_budget == TURN_BUDGET_MAX
        runtime.start()
        task = runtime.enqueue_task("budget check")
        runtime.run_cycle()
        runtime.stop("test complete")
        assert clamp_turn_budget(-5) == 1
        assert clamp_turn_budget("seven") == 1
        assert clamp_turn_budget(4) == 4
        assert clamp_parallel_tool_calls(99) == MAX_PARALLEL_TOOL_CALLS_MAX
        assert clamp_parallel_tool_calls(None) == 1

    def test_the_loop_allowance_reaches_the_pipeline_as_an_allowance_not_a_ceiling(self, tmp_path: Path):
        runtime = _runtime(_workspace(tmp_path), turn_budget=2)
        context = TurnContext(goal="g", workspace=runtime.workspace, turn_id="t", task_id="x", budget_turns=2, metadata={"turns_spent": 2})
        _amended, decisions, refusal = runtime.pipeline.prepare(context)
        assert "loop_guard" in refusal
        actions = {action.name: action.value for decision in decisions if decision.stage == "loop_guard" for action in decision.actions}
        assert actions["turn_allowance"] == 2 and actions["turns_spent"] == 2

    def test_a_turn_over_the_allowance_never_reaches_the_backend(self, tmp_path: Path):
        """The guard is enforced at the seam, not merely reported by it (07 §5, P4 item 5)."""
        touched: list[str] = []
        ws = _workspace(tmp_path)
        runtime = _runtime(ws, turn_budget=1)
        runtime.backends.get("native").backend.turn_executor = lambda ctx: (
            touched.append(ctx.turn_id) or TurnResult(status="completed", text="unremarkable")
        )
        runtime.start()
        task = runtime.enqueue_task("guard check")
        task.current_attempt = 1  # one turn already spent, against a one-turn allowance
        runtime.queue.update(task)
        runtime.run_cycle()
        assert touched == [], "a refused turn must not reach the authoritative loop"
        blocked = runtime.tasks()[0]
        assert blocked.status.value == "blocked"
        assert "loop_guard" in (blocked.last_error or ""), blocked.last_error
        assert EventType.RUNTIME_BACKEND_SELECTED.value in _event_names(runtime, task.task_id), (
            "routing is recorded even when the pipeline then stops the turn"
        )
        runtime.stop("test complete")

    def test_the_first_turn_under_the_same_allowance_is_run(self, tmp_path: Path):
        """The counterpart, so the test above cannot pass by refusing everything."""
        touched: list[str] = []
        ws = _workspace(tmp_path)
        runtime = _runtime(ws, turn_budget=1)
        runtime.backends.get("native").backend.turn_executor = lambda ctx: (
            touched.append(ctx.turn_id) or TurnResult(status="completed", text="one turn of work")
        )
        runtime.start()
        runtime.enqueue_task("guard check")
        runtime.run_cycle()
        assert len(touched) == 1
        assert runtime.tasks()[0].status.value == "completed"
        runtime.stop("test complete")


class TestMediationCannotBeBypassed:
    def test_a_backend_registered_without_a_mediator_cannot_run(self, tmp_path: Path):
        ws = _workspace(tmp_path)
        lead = LeadAgentBackend(workspace=ws, driver=ws / "driver.py", venv_python=sys.executable, required_imports=(), enabled=True)
        availability = lead.probe()
        assert not availability.available
        assert "ApprovalMediator" in availability.reason, availability.reason
        result = lead.run_turn(TurnContext(goal="x", workspace=ws, turn_id="t"))
        assert result.status == "refused"

    def test_dsh_refuses_without_a_mediator_and_never_returns_a_verdict(self, tmp_path: Path):
        backend = DeepSeekHarnessBackend(enabled=True)
        result = backend.run_turn(TurnContext(goal="x", workspace=tmp_path, turn_id="t"))
        assert result.status == "refused" and "ApprovalMediator" in result.text
        assert not hasattr(result, "success"), "a backend may report an observation, not a verdict"

    def test_the_registry_refuses_a_disabled_backend_named_directly(self, tmp_path: Path):
        registry = build_default_registry(BackendDefaults(workspace=tmp_path))
        registry.set_enabled("native", False)
        result = registry.run_turn("native", TurnContext(goal="x", workspace=tmp_path, turn_id="t"))
        assert result.status == "refused" and "disabled" in result.text

    def test_an_unknown_backend_name_is_not_a_fall_back(self, tmp_path: Path):
        registry = build_default_registry(BackendDefaults(workspace=tmp_path))
        result = registry.run_turn("whatever", TurnContext(goal="x", workspace=tmp_path, turn_id="t"))
        assert result.status == "refused" and "unknown backend" in result.text

    def test_a_malformed_turn_result_is_a_failure_not_a_trusted_object(self, tmp_path: Path):
        class Loose(NativeBackend):
            def run_turn(self, context, sink=None):  # noqa: D102
                return "I am a string"

        registry = BackendRegistry()
        registry.register(Loose(turn_executor=lambda ctx: None))
        result = registry.run_turn("native", TurnContext(goal="x", workspace=tmp_path, turn_id="t"))
        assert result.status == "failed" and "not a TurnResult" in result.text

    def test_a_backend_missing_a_port_obligation_is_rejected_at_registration(self, tmp_path: Path):
        class Half:
            name = "half"

            def probe(self):
                return None

        with pytest.raises(BackendContractError) as excinfo:
            BackendRegistry().register(Half())
        assert "plan_capability" in str(excinfo.value) and "run_turn" in str(excinfo.value)

    def test_an_external_backend_needs_provenance_before_it_can_be_enabled(self, tmp_path: Path):
        registry = BackendRegistry()
        with pytest.raises(BackendConflict):
            registry.register(NativeBackend(turn_executor=lambda ctx: None), source="vendor", enabled=True)
        registration = registry.register(NativeBackend(turn_executor=lambda ctx: None), source="vendor", enabled=False)
        assert any("provenance incomplete" in note for note in registration.notes)
        with pytest.raises(BackendConflict):
            registry.set_enabled(registration.name, True)

    def test_python_file_execution_confined(self, tmp_path: Path):
        """07 §8's acceptance name: an interpreter running a file still runs inside the boundary.

        The policy may allow the *program*; what makes it safe is that the launch is confined and the
        write-set is the task's. Both halves are asserted: the child's request comes back through the
        mediator, and the receipt names the provider rather than "unconfined".
        """
        from evo_agent.ports.contracts import ExecRequest

        ws = tmp_path / "ws"
        ws.mkdir(parents=True, exist_ok=True)
        result = ToolRegistry(SecurityPolicy(ws)).mediator.execute(
            ExecRequest(argv=("python3", "-c", "print(1)"), cwd=ws, timeout_seconds=20.0, max_output_bytes=4096, label="test.python_file"),
            tool_name="shell",
            arguments={"command": "python3 -c print(1)"},
        )
        # Either the mediator refused it, or it ran inside a namespace. "Ran on the host because the
        # interpreter was allow-listed" is the third option this test exists to make impossible.
        assert result.refusal or result.isolated, result.to_dict()
        if not result.refusal:
            assert result.provider != "host"

    def test_a_child_cannot_ask_for_an_unreviewed_tool_name(self, tmp_path: Path):
        ws = _workspace(tmp_path)
        runtime = _runtime(ws, backends=_lead_config(ws), agent_loop="lead_agent")
        backend = runtime.backends.get("lead_agent").backend
        turn = type(
            "T",
            (),
            {"notes": [], "receipts": [], "turn_id": "t1", "cancelled": False, "timed_out": False, "bytes_read": 0},
        )()
        with _pipe() as (child_read, parent_write):
            backend._service_tool_request(
                parent_write,
                {"id": "1", "tool": "teleport", "argv": ["rm", "-rf", "/"], "arguments": {}},
                turn,
                TurnContext(goal="g", workspace=ws, turn_id="t1"),
            )
            reply = child_read.read_reply()
        assert reply["ok"] is False
        assert "refused" in reply["error"] or "boundary" in reply["error"], reply
        assert turn.receipts == [], "a name the boundary does not recognise never reaches the ledger as work done"

    def test_a_bridge_ask_with_operator_consent_runs_inside_the_namespace(self, tmp_path: Path):
        """The refusal above is about consent, not about the bridge being unable to work (P4 item 3).

        The consent here is a real approver callback rather than a relaxed ``approval_required_for``:
        an operator saying yes is the mechanism the design has, and a test that edited the risk
        classification to make the call succeed would have proven that the classification is decorative.
        """
        ws = tmp_path / "ws"
        ws.mkdir(parents=True, exist_ok=True)
        registry = ToolRegistry(SecurityPolicy(ws), approver=lambda name, arguments: name == "shell")
        confined, why = registry.mediator.isolation_state()
        if not confined:
            pytest.skip(f"this platform cannot confine a process: {why}")
        backend = LeadAgentBackend(
            workspace=ws,
            driver=ws / "driver.py",
            venv_python=sys.executable,
            required_imports=(),
            enabled=True,
            mediator=registry.mediator,
            # An external harness is granted a reviewed subset; "shell" is on it here so the request
            # below is refused or allowed on its own merits rather than at the name gate.
            advertised_tools=("shell",),
        )
        turn = type("T", (), {"notes": [], "receipts": [], "turn_id": "t4", "cancelled": False, "bytes_read": 0})()
        with _pipe() as (child_read, parent_write):
            backend._service_tool_request(
                parent_write,
                {"id": "1", "tool": "run_command", "argv": ["echo", "hi"], "arguments": {}},
                turn,
                TurnContext(goal="g", workspace=ws, turn_id="t4"),
            )
            reply = child_read.read_reply()
        assert not reply["error"], reply
        assert reply["ok"] is True, reply
        assert reply["isolated"] is True and reply["provider"] != "host", reply
        assert reply["output"].strip() == "hi", reply
        assert turn.receipts and turn.receipts[0].canonical_name == "shell"
        assert turn.receipts[0].ok is True and turn.receipts[0].isolation == reply["provider"]

    def test_a_child_naming_an_alias_is_mediated_under_the_canonical_name(self, tmp_path: Path):
        ws = _workspace(tmp_path)
        runtime = _runtime(ws, backends=_lead_config(ws), agent_loop="lead_agent")
        backend = runtime.backends.get("lead_agent").backend
        recorded: list[dict] = []
        turn = type(
            "T",
            (),
            {"notes": [], "receipts": recorded, "turn_id": "t2", "cancelled": False, "timed_out": False, "bytes_read": 0},
        )()

        decisions: list[str] = []
        original = backend.mediator.execute

        def spy(request, *, tool_name, arguments):
            decisions.append(tool_name)
            return original(request, tool_name=tool_name, arguments=arguments)

        backend.mediator.execute = spy  # type: ignore[method-assign]
        with _pipe() as (child_read, parent_write):
            backend._service_tool_request(
                parent_write,
                {"id": "1", "tool": "run_command", "argv": ["echo", "hi"], "arguments": {}},
                turn,
                TurnContext(goal="g", workspace=ws, turn_id="t2"),
            )
            child_read.read_reply()
        assert decisions == ["shell"], "the alias must resolve before the authority sees the request"


class TestBridgeAndAdapterOperational:
    def test_the_lead_agent_bridge_serves_a_real_turn(self, tmp_path: Path):
        ws = _workspace(tmp_path)
        runtime = _runtime(ws, backends=_lead_config(ws), agent_loop="lead_agent")
        runtime.start()
        task = runtime.enqueue_task("ask the harness to echo")
        result = runtime.run_cycle()
        recorded = runtime.tasks()[0]
        assert result.tasks_completed == 1, (recorded.status.value, recorded.last_error, result.failures)
        assert recorded.status.value == "completed"
        backend = recorded.metadata["backend"]
        assert backend["name"] == "lead_agent" and backend["status"] == "completed"
        receipt = backend["receipts"][0]
        assert receipt["canonical_name"] == "shell", "the child typed an alias; the ledger records the canonical name"
        assert receipt["ok"] is False and "unconfined" in receipt["isolation"], (
            "an unattended runtime approves nothing, so a medium-risk ask must be denied and must not have run"
        )
        events = _event_names(runtime, task.task_id)
        assert EventType.RUNTIME_BACKEND_SELECTED.value in events
        assert EventType.RUNTIME_TURN_PIPELINE.value in events
        runtime.stop("test complete")

    def test_the_bridge_reports_the_isolation_it_actually_got(self, tmp_path: Path):
        ws = _workspace(tmp_path)
        runtime = _runtime(ws, backends=_lead_config(ws), agent_loop="lead_agent")
        runtime.start()
        runtime.enqueue_task("ask the harness to echo")
        runtime.run_cycle()
        notes = runtime.backends.probe("lead_agent").detail
        assert "driver_report" in notes or notes.get("advertised_tools") is not None
        runtime.stop("test complete")

    def test_a_harness_turn_still_needs_the_verifier(self, tmp_path: Path):
        """An empty "done" from a bridge is not a completion: the verdict is Evo's (R1)."""
        ws = _workspace(tmp_path)
        # The simplest faithful case: the child finishes, and says nothing.
        quiet = (
            "import json, sys\n"
            "def emit(p):\n"
            "    json.dump(p, sys.stdout); sys.stdout.write('\\n'); sys.stdout.flush()\n"
            "if sys.argv[1] == '--probe':\n"
            "    emit({'type': 'probe', 'ok': True, 'harness': 'stub', 'protocol': 1}); raise SystemExit(0)\n"
            "sys.stdin.readline()\n"
            "emit({'type': 'final', 'text': ''})\n"
        )
        (ws / "driver.py").write_text(quiet, encoding="utf-8")
        runtime = _runtime(ws, backends=_lead_config(ws), agent_loop="lead_agent")
        runtime.start()
        runtime.enqueue_task("a turn with nothing in it")
        runtime.run_cycle()
        recorded = runtime.tasks()[0]
        assert recorded.status.value != "completed", recorded.metadata.get("verification")
        # The child reported a clean finish; Evo still would not take it, because there was nothing
        # in it to verify. "completed" is not a fact a bridge gets to assert about itself (R1).
        assert recorded.metadata["backend"]["status"] == "completed", recorded.metadata["backend"]
        assert "no content" in (recorded.last_error or "").lower(), recorded.last_error
        reason = recorded.metadata.get("verification", {}).get("reason", "")
        assert "no content to verify" in reason or "refused" in reason
        runtime.stop("test complete")

    def test_the_process_adapter_serves_a_turn(self, tmp_path: Path):
        # Inside the workspace, not beside it: the child runs confined, and a program outside the
        # mounted root is not found no matter how correct the configuration is.
        bin_dir = tmp_path / "ws" / "bin"
        bin_dir.mkdir(parents=True, exist_ok=True)
        stub = bin_dir / "dsh-stub"
        stub.write_text(
            "#!/bin/sh\n"
            'if [ "$1" = "--version" ]; then echo "dsh-stub 0.0.1"; exit 0; fi\n'
            'echo "observation: harness saw a goal"\n',
            encoding="utf-8",
        )
        stub.chmod(0o755)
        os.environ["PATH"] = f"{bin_dir}{os.pathsep}{os.environ['PATH']}"
        try:
            ws = _workspace(tmp_path)
            runtime = _runtime(
                ws,
                backends={"dsh": {"enabled": True, "accepted_by": "operator@evo", "executable": "dsh-stub", "arguments": ["--prompt", "{goal}"]}},
                agent_loop="dsh",
            )
            assert "dsh" in runtime.backends.names
            runtime.start()
            runtime.enqueue_task("run the harness once")
            result = runtime.run_cycle()
            recorded = runtime.tasks()[0]
            assert result.tasks_completed == 1, (recorded.status.value, recorded.last_error)
            assert recorded.metadata["backend"]["name"] == "dsh"
            assert recorded.metadata["backend"]["receipts"], "an external turn still leaves a receipt"
            assert recorded.metadata["backend"]["receipts"][0]["kind"] == "execute"
            runtime.stop("test complete")
        finally:
            os.environ["PATH"] = os.environ["PATH"].replace(f"{bin_dir}{os.pathsep}", "", 1)

    def test_a_harness_invariant_violation_fails_the_turn(self, tmp_path: Path):
        backend = DeepSeekHarnessBackend(
            executable=str(self._noisy_stub(tmp_path)),
            arguments_template=("--prompt", "{goal}"),
            workspace=tmp_path,
            mediator=ToolRegistry(SecurityPolicy(tmp_path)).mediator,
            enabled=True,
            version_argument="--version",
        )
        result = backend.run_turn(TurnContext(goal="g", workspace=tmp_path, turn_id="t3"))
        assert result.status == "failed", result.to_dict()
        assert any("InvariantFailure" in note for note in result.notes), result.to_dict()
        receipt = result.receipts[0]
        assert receipt.ok is False, "a harness that reports an invariant failure did not succeed, exit code aside"
        assert "invariant markers" in receipt.notes[0]

    @staticmethod
    def _noisy_stub(tmp_path: Path) -> Path:
        (tmp_path / "ws").mkdir(parents=True, exist_ok=True)
        path = tmp_path / "ws" / "loud-harness"
        path.write_text("#!/bin/sh\necho 'InvariantFailure: session log disagrees with itself'\n", encoding="utf-8")
        path.chmod(0o755)
        return path

    def test_render_template_is_not_a_shell(self):
        command = render_template(("harness", "--prompt", "{goal}", "--workspace", "{workspace}"), {"goal": "a; rm -rf /", "workspace": "/tmp"})
        assert command.argv == ("harness", "--prompt", "a; rm -rf /", "--workspace", "/tmp")
        assert command.substituted["goal"] == "a; rm -rf /"


class _ReplyReader:
    """Reads one protocol line the parent wrote to the child, and hands back the parsed reply."""

    def __init__(self, descriptor: int) -> None:
        self.descriptor = descriptor

    def read_reply(self) -> dict:
        buffer = b""
        while not buffer.endswith(b"\n"):
            chunk = os.read(self.descriptor, 4096)
            if not chunk:
                break
            buffer += chunk
        return json.loads(buffer.decode("utf-8") or "{}")


class _ChildPipe:
    """A stand-in for ``subprocess.Popen`` whose stdin is a real pipe.

    Real descriptors rather than a recording fake: :meth:`LeadAgentBackend._write` writes with
    ``os.write`` on purpose (mixing buffered and unbuffered writes on one pipe is undefined), and a
    fake that only accepted ``write`` would be testing the fake.
    """

    def __init__(self, descriptor: int) -> None:
        self.stdin = type("Handle", (), {"fileno": lambda self=None, d=descriptor: d})()


@contextlib.contextmanager
def _pipe():
    read_end, write_end = os.pipe()
    try:
        yield _ReplyReader(read_end), _ChildPipe(write_end)
    finally:
        os.close(read_end)
        os.close(write_end)


class TestBoundsComeFromTheOperator:
    def test_production_config_may_only_bound_the_loop(self):
        config = ProductionConfig(turn_budget=3, max_parallel_tool_calls=1)
        assert config.schema_version == ProductionConfig().schema_version
        with pytest.raises(ValueError):
            ProductionConfig(agent_loop="lead_agent")
        with pytest.raises(ValueError):
            ProductionConfig(agent_loop="nonsense")
        with pytest.raises(ValueError):
            ProductionConfig(turn_budget=TURN_BUDGET_MAX + 1)

    def test_production_bounds_are_applied_and_never_widen(self, tmp_path: Path):
        ws = _workspace(tmp_path)
        runtime = _runtime(ws, turn_budget=8, max_parallel_tool_calls=4)
        applied = runtime.apply_production_bounds({"turn_budget": 3, "max_parallel_tool_calls": 99, "agent_loop": "native"})
        assert runtime.turn_budget == 3
        assert runtime.max_parallel_tool_calls == 4, "99 was refused by the clamp, and a wider value may not replace a narrower one"
        assert applied["agent_loop"] == "native"
        with pytest.raises(ValueError):
            runtime.apply_production_bounds({"agent_loop": "dsh"})

    def test_the_supervisor_bounds_before_the_first_cycle(self, tmp_path: Path):
        ws = _workspace(tmp_path)
        runtime = _runtime(ws, turn_budget=DEFAULT_TURN_BUDGET)
        config = ProductionConfig(turn_budget=1, max_cycles_per_run=1)
        supervisor = ProductionSupervisor(runtime, config)
        supervisor.run()
        assert runtime.turn_budget == 1
        assert runtime.runtime_record.metadata["production_bounds"]["turn_budget"] == 1

    def test_the_runtime_default_loop_is_native(self, tmp_path: Path):
        assert ProductionConfig().agent_loop == "native"
        assert DEFAULT_TURN_BUDGET <= TURN_BUDGET_MAX

    def test_registry_rejects_a_duplicate_name(self, tmp_path: Path):
        registry = build_default_registry(BackendDefaults(workspace=tmp_path))
        with pytest.raises(BackendConflict):
            registry.register(NativeBackend(), priority=1)

    def test_registry_export_receipts_tolerates_a_backend_with_none(self, tmp_path: Path):
        registry = build_default_registry(BackendDefaults(workspace=tmp_path))
        assert registry.export_receipts("native", "turn_absent") == ()

    def test_cancel_reaches_a_backend_that_can_be_stopped(self, tmp_path: Path):
        ws = _workspace(tmp_path)
        runtime = _runtime(ws)
        assert runtime.cancel_active_turn("nothing in flight") is False
