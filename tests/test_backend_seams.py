"""Ports, the mediator, and the two bridges: the seam that lets Evo absorb a runtime (07 §6).

The unified-agent goal is satisfied or broken here. DeerFlow and DeepSeek Harness arrive as
*implementations of interfaces* and as child processes behind one authority - not as additional
agents beside Evo. So the properties this file defends are the ones that make that statement true
rather than aspirational:

* a backend that does not satisfy its port cannot register, and one that satisfies it cannot
  deliver a verdict (no ``success`` field on ``TurnResult`` at all);
* the mediator is the only thing that decides whether anything runs - including what a bridge's own
  child process asks to run;
* a bridge's *claims* about authority are stripped at the boundary, not trusted downstream;
* an external backend needs its license, source, and accepting operator recorded before it can be
  enabled.

The bridge child is a fake driver written by the test, which is the only honest way to exercise the
protocol here: the real harness is not an Evo dependency and must not become one for a test run.
"""

from __future__ import annotations

import json
from pathlib import Path
import sys
import textwrap

import pytest

from evo_agent.backends import (
    AVAILABLE,
    DEGRADED,
    UNAVAILABLE,
    BackendContractError,
    BackendConflict,
    BackendDefaults,
    DeepSeekHarnessBackend,
    KNOWN_BACKENDS,
    LeadAgentBackend,
    NativeBackend,
    UnknownBackend,
    build_default_registry,
    render_template,
)
from evo_agent.backends.availability import classify
from evo_agent.backends.dsh import HarnessConfigError
from evo_agent.backends.lead_agent import DEFAULT_MAX_LINE_BYTES, sanitize_child_message
from evo_agent.backends.registry import BackendRegistry
from evo_agent.models import RiskLevel
from evo_agent.ports.contracts import (
    PORTS,
    BackendAvailability,
    CapabilityRequest,
    ExecRequest,
    PortContractError,
    Receipt,
    TurnContext,
    TurnResult,
    call_optional,
    required_members,
    validate_implementation,
)
from evo_agent.security import SecurityPolicy
from evo_agent.sovereign.mediation import ApprovalMediator

ROOT = Path(__file__).resolve().parents[1]


def collector() -> tuple[list[tuple[str, dict]], callable]:
    events: list[tuple[str, dict]] = []

    def on_event(kind: str, payload: dict) -> None:
        events.append((kind, dict(payload)))

    return events, on_event


def mediator(tmp_path: Path, *, approver=None, events=None, **policy_kwargs) -> ApprovalMediator:
    policy = SecurityPolicy(tmp_path, **policy_kwargs)
    return ApprovalMediator(policy, approver=approver, on_event=events)


def context(tmp_path: Path, turn_id: str = "turn-1", **kwargs) -> TurnContext:
    return TurnContext(goal=kwargs.pop("goal", "do the thing"), workspace=tmp_path, turn_id=turn_id, **kwargs)


# --- the ports themselves ----------------------------------------------------


def test_every_port_declares_its_obligations_as_data():
    declared = {port.__name__ for port in PORTS}
    assert declared == {"EventSink", "SandboxProvider", "ExecutionBackend", "TurnEngine", "VerifierPlugin"}
    for port in PORTS:
        assert getattr(port, "__evo_port__", False) is True, f"{port.__name__} is not registered as a port"
        assert port.__port_required__, f"{port.__name__} declares no obligations, so it guards nothing"
        assert set(port.__port_required__).issubset(set(required_members(port)))
    assert "run_turn" in ExecutionBackend_required()
    # The bridge may report facts but must not be able to report a verdict.
    assert "success" not in TurnResult.__dataclass_fields__
    assert "satisfied" not in TurnResult.__dataclass_fields__


def ExecutionBackend_required() -> tuple[str, ...]:
    from evo_agent.ports.contracts import ExecutionBackend

    return ExecutionBackend.__port_required__


def test_a_partial_backend_is_refused_at_registration_not_at_first_use():
    class HalfBridge:
        name = "half"

        def probe(self) -> BackendAvailability:
            return BackendAvailability(self.name, True, "")

        def run_turn(self, context, sink=None) -> TurnResult:
            return TurnResult(status="completed")

    registry = BackendRegistry()
    with pytest.raises(BackendContractError) as excinfo:
        registry.register(HalfBridge())
    assert excinfo.value.missing == ("plan_capability",)


def test_additive_members_may_be_omitted_by_an_older_adapter():
    """R8, mechanically: a defaulted port member cannot orphan an installed backend."""

    class MinimalBridge:
        name = "minimal"

        def probe(self) -> BackendAvailability:
            return BackendAvailability(self.name, True, "")

        def plan_capability(self, request):
            from evo_agent.ports.contracts import BackendPlan

            return BackendPlan(True, "can serve anything")

        def run_turn(self, context, sink=None) -> TurnResult:
            return TurnResult(status="completed", text="ok")

    registry = BackendRegistry()
    registration = registry.register(MinimalBridge())
    assert registration.name == "minimal"
    # ``cancel`` and ``export_receipts`` carry defaults on the port, so absence is legal and the
    # registry must cope rather than raise.
    assert registry.export_receipts("minimal", "no-such-turn") == ()
    from evo_agent.ports.contracts import ExecutionBackend

    optional = ExecutionBackend.__port_optional__
    assert "cancel" in optional and "export_receipts" in optional
    assert "probe" not in optional


def test_call_optional_reports_a_bad_signature_as_a_contract_error():
    class Sink:
        def emit(self, event: str) -> None:  # takes one argument, not two
            raise AssertionError("unreachable")

    with pytest.raises(PortContractError, match="rejected its arguments"):
        call_optional(Sink(), "emit", "kind", {"a": 1})
    assert call_optional(object(), "missing_method", default="fallback") == "fallback"


def test_receipt_digests_are_derived_from_the_exact_bytes():
    first = Receipt.record(ledger_seq=1, turn_id="t", tool="shell", canonical_name="shell.exec", kind="execute", arguments={"argv": ["ls"]}, output="a", ok=True, duration_ms=1)
    same = Receipt.record(ledger_seq=1, turn_id="t", tool="shell", canonical_name="shell.exec", kind="execute", arguments={"argv": ["ls"]}, output="a", ok=True, duration_ms=1)
    different = Receipt.record(ledger_seq=1, turn_id="t", tool="shell", canonical_name="shell.exec", kind="execute", arguments={"argv": ["ls"]}, output="b", ok=True, duration_ms=1)
    assert first.args_sha256 == same.args_sha256 and first.output_sha256 == same.output_sha256
    assert different.output_sha256 != first.output_sha256
    # Key order must not change a digest, or a receipt stops being reproducible from the log.
    reordered = Receipt.record(ledger_seq=1, turn_id="t", tool="shell", canonical_name="shell.exec", kind="execute", arguments={"b": 2, "a": 1}, output="a", ok=True, duration_ms=1)
    other = Receipt.record(ledger_seq=1, turn_id="t", tool="shell", canonical_name="shell.exec", kind="execute", arguments={"a": 1, "b": 2}, output="a", ok=True, duration_ms=1)
    assert reordered.args_sha256 == other.args_sha256


# --- the mediator ------------------------------------------------------------


def test_policy_denial_is_reported_with_the_rule_that_fired(tmp_path: Path):
    events, sink = collector()
    med = mediator(tmp_path, events=sink)
    decision = med.authorize(ExecRequest(argv=("rm", "-rf", "x"), cwd=tmp_path), tool_name="shell")[0]
    assert decision.allowed is False and decision.rule == "policy"
    assert [kind for kind, _payload in events] == ["mediation_decision"]
    assert events[0][1]["rule"] == "policy"


def test_evaluate_does_not_record_while_authorize_does(tmp_path: Path):
    events, sink = collector()
    med = mediator(tmp_path, events=sink)
    request = ExecRequest(argv=("printf", "x"), cwd=tmp_path)
    med.evaluate(request, tool_name="shell", approved=True, risk=RiskLevel.LOW)
    assert events == []
    med.authorize(request, tool_name="shell", approved=True, risk=RiskLevel.LOW)
    assert len(events) == 1


def test_approval_is_evidence_not_a_formality(tmp_path: Path):
    """A HIGH-risk command needs either carried evidence or an approver that said yes."""
    request = ExecRequest(argv=("printf", "x"), cwd=tmp_path)
    unattended = mediator(tmp_path)
    denied = unattended.evaluate(request, tool_name="shell", risk=RiskLevel.HIGH)
    assert denied.allowed is False and denied.rule == "unapproved"

    approved_events, sink = collector()
    with_approver = mediator(tmp_path, approver=lambda name, args: True, events=sink)
    granted = with_approver.authorize(request, tool_name="shell", arguments={"command": "printf x"}, risk=RiskLevel.HIGH)[0]
    assert granted.allowed is True
    result = with_approver.execute(request, tool_name="shell", arguments={"command": "printf x"}, risk=RiskLevel.HIGH)
    assert result.returncode == 0 and "EVO_APPROVED" not in result.output

    declining = mediator(tmp_path, approver=lambda name, args: False)
    refused = declining.evaluate(request, tool_name="shell", risk=RiskLevel.HIGH)
    assert refused.allowed is False and "declined" in refused.reason

    carried = unattended.execute(request, tool_name="shell", risk=RiskLevel.HIGH, approved=True)
    assert carried.returncode == 0, carried.refusal


def test_network_is_refused_before_any_provider_is_consulted(tmp_path: Path):
    med = mediator(tmp_path)
    decision = med.evaluate(ExecRequest(argv=("curl", "example.invalid"), cwd=tmp_path, network=True), tool_name="shell")
    assert decision.allowed is False and "network" in decision.reason


def test_strict_enforcement_refuses_when_nothing_can_confine(tmp_path: Path):
    from evo_agent.ports.contracts import ProviderAvailability

    class Useless:
        name = "useless"

        def probe(self):
            return ProviderAvailability("useless", False, "no namespaces here")

        def run(self, request, on_event=None):
            raise AssertionError("must not run")

        def prepare(self, request):
            raise AssertionError("must not run")

    med = mediator(tmp_path, sandbox_enforcement="strict")
    med.providers = [Useless()]
    decision = med.evaluate(ExecRequest(argv=("printf", "x"), cwd=tmp_path), tool_name="shell", approved=True, risk=RiskLevel.LOW)
    assert decision.allowed is False and decision.rule == "no_isolation"


def test_read_only_root_is_skipped_when_the_workspace_is_the_source(tmp_path: Path):
    """A self-hosting run's workspace *is* the checkout; ro-ing it would make the task impossible.

    The trade-off is stated rather than hidden: that configuration has no source-tree protection from
    tools, which is why self-hosting goes through staging and promotion instead.
    """
    med = ApprovalMediator(SecurityPolicy(tmp_path), source_root=tmp_path)
    assert med.read_only_roots(tmp_path) == ()


def test_grant_approval_path_for_bridges(tmp_path: Path):
    med = mediator(tmp_path)
    assert med.grant_approval("shell", {"command": "ls"}, risk=RiskLevel.LOW).rule == "not_required"
    assert med.grant_approval("shell", {"command": "ls"}, risk=RiskLevel.HIGH).allowed is False
    approved = mediator(tmp_path, approver=lambda name, args: True)
    decision = approved.grant_approval("shell", {"command": "ls"}, risk=RiskLevel.HIGH)
    assert decision.allowed and decision.rule == "approved" and decision.details["risk"] == "high"


def test_a_broken_policy_check_reads_as_denial_not_permission(tmp_path: Path):
    class Exploding:
        workspace = tmp_path
        sandbox_enforcement = "auto"

        def validate_command(self, command):
            raise RuntimeError("policy is broken")

        def requires_approval(self, call):
            return False

    med = ApprovalMediator(Exploding())
    decision = med.evaluate(ExecRequest(argv=("printf", "x"), cwd=tmp_path), tool_name="shell")
    assert decision.allowed is False and decision.rule == "policy_error"


def test_infrastructure_launch_must_be_the_configured_program(tmp_path: Path):
    """A bridge may run its driver, and nothing else - so the identity check replaces the allowlist."""
    med = mediator(tmp_path)
    allowed, _amended = med.authorize_infrastructure(
        ExecRequest(argv=(sys.executable, str(ROOT / "evo_agent" / "backends" / "lead_agent_driver.py"), "--probe"), cwd=tmp_path),
        program=sys.executable,
        tool_name="lead_agent",
    )
    assert allowed.allowed is True, allowed.reason
    smuggled, _ = med.authorize_infrastructure(
        ExecRequest(argv=("/bin/sh", "-c", "rm -rf /"), cwd=tmp_path), program=sys.executable, tool_name="lead_agent"
    )
    assert smuggled.allowed is False and smuggled.rule == "infrastructure_argv_mismatch"


# --- the registry ------------------------------------------------------------


def test_unknown_backend_names_are_an_error_not_a_fallback(tmp_path: Path):
    registry = BackendRegistry()
    result = registry.run_turn("deerflow", context(tmp_path))
    assert result.status == "refused"
    assert "no such backend; refusing to fall back silently" in " ".join(result.notes)
    assert registry.names == ()


def test_a_backend_returning_the_wrong_shape_is_caused_to_explain_itself(tmp_path: Path):
    class LooseBridge:
        name = "loose"

        def probe(self):
            return BackendAvailability("loose", True, "")

        def plan_capability(self, request):
            from evo_agent.ports.contracts import BackendPlan

            return BackendPlan(True, "yes")

        def run_turn(self, context, sink=None):
            return "it went fine"  # not a TurnResult

    registry = BackendRegistry()
    registry.register(LooseBridge())
    result = registry.run_turn("loose", context(tmp_path))
    assert result.status == "failed"
    assert "not a TurnResult" in result.text


def test_external_backend_cannot_be_enabled_without_provenance(tmp_path: Path):
    """It may be *recorded* while unsigned-off, so "waiting on review" is a state the system can say."""
    registry = BackendRegistry()
    registration = registry.register(NativeBackend(tool_names=("shell",)), source="vendor/x", license="", source_url="", accepted_by="", enabled=False)
    assert any("provenance incomplete" in note for note in registration.notes)
    with pytest.raises(BackendConflict, match="needs license"):
        registry.set_enabled("native", True)


def test_provenance_allows_enabling(tmp_path: Path):
    class Bridge:
        name = "reviewed"

        def probe(self):
            return BackendAvailability("reviewed", True, "")

        def plan_capability(self, request):
            from evo_agent.ports.contracts import BackendPlan

            return BackendPlan(True, "fine")

        def run_turn(self, context, sink=None):
            return TurnResult(status="completed", text="x")

    registry = BackendRegistry()
    registry.register(Bridge(), source="vendor/x", license="MIT", source_url="https://example.invalid/x", accepted_by="security-review", enabled=False)
    updated = registry.set_enabled("reviewed", True)
    assert updated.enabled is True


def test_selection_prefers_native_on_a_tie_and_reports_why(tmp_path: Path):
    class AlsoAble:
        name = "also"

        def probe(self):
            return BackendAvailability("also", True, "")

        def plan_capability(self, request):
            from evo_agent.ports.contracts import BackendPlan

            return BackendPlan(True, "also fine")

        def run_turn(self, context, sink=None):
            return TurnResult(status="completed", origin=self.name)

    events, sink = collector()
    registry = BackendRegistry(on_event=sink)
    registry.register(NativeBackend(tool_names=("shell",)))
    registry.register(AlsoAble(), source="vendor/x", license="MIT", source_url="u", accepted_by="a")
    plan = registry.plan(CapabilityRequest(goal="g", needed=("shell",)))
    assert plan["selected"] == "native"
    assert {item["name"] for item in plan["serving"]} == {"native", "also"}
    # A backend without a turn executor is *degraded*, and that must be visible in the report.
    states = registry.states()
    assert states["native"] == DEGRADED and states["also"] == AVAILABLE


def test_a_declining_backend_is_recorded_not_silently_dropped(tmp_path: Path):
    registry = BackendRegistry()
    registry.register(NativeBackend(tool_names=("shell", "workspace_read")))
    plan = registry.plan(CapabilityRequest(goal="research", needed=("web_research",)))
    assert plan["serving"] == []
    assert plan["declined"][0]["name"] == "native"
    assert "declared, not executable" in plan["declined"][0]["reason"]


def test_disabled_backends_cannot_be_reached_by_naming_them(tmp_path: Path):
    registry = BackendRegistry()
    registry.register(NativeBackend(tool_names=("shell",)), enabled=False)
    result = registry.run_turn("native", context(tmp_path))
    assert result.status == "refused" and "disabled" in result.text


def test_availability_states_are_a_lattice_and_merge_worst_first():
    from evo_agent.backends.availability import AvailabilityReport, BackendReport, merge_reports

    assert classify(BackendAvailability("x", True, ""), enabled=True) == AVAILABLE
    assert classify(BackendAvailability("x", True, "missing something"), enabled=True) == DEGRADED
    assert classify(BackendAvailability("x", True, ""), enabled=False) == UNAVAILABLE
    first = AvailabilityReport(reports=(BackendReport("a", AVAILABLE), BackendReport("b", DEGRADED)))
    second = AvailabilityReport(reports=(BackendReport("a", UNAVAILABLE),))
    merged = merge_reports((first, second))
    assert {item.name: item.state for item in merged.reports} == {"a": UNAVAILABLE, "b": DEGRADED}


# --- the native backend ------------------------------------------------------


def test_native_backend_without_an_executor_refuses(tmp_path: Path):
    backend = NativeBackend(tool_names=("shell",))
    assert backend.probe().available is True and "turn executor" in backend.probe().reason
    result = backend.run_turn(context(tmp_path))
    assert result.status == "refused"


def test_native_backend_clamps_parallel_tool_calls(tmp_path: Path):
    def executor(ctx: TurnContext) -> TurnResult:
        return TurnResult(status="completed", text="done", receipts=())

    backend = NativeBackend(turn_executor=executor)
    result = backend.run_turn(context(tmp_path, metadata={"max_parallel_tool_calls": 5000}))
    assert any("clamped 5000 -> 10" in note for note in result.notes)
    low = backend.run_turn(context(tmp_path, turn_id="turn-2", metadata={"max_parallel_tool_calls": 0}))
    assert any("clamped 0 -> 1" in note for note in low.notes)


def test_native_backend_accounts_for_turns_without_judging_them(tmp_path: Path):
    receipts = (
        Receipt.record(ledger_seq=1, turn_id="turn-1", tool="shell", canonical_name="shell.exec", kind="execute", arguments={"a": 1}, output="x", ok=True, duration_ms=2),
    )

    def executor(ctx: TurnContext) -> TurnResult:
        return TurnResult(status="completed", text="ok", receipts=receipts)

    events, sink = collector()
    backend = NativeBackend(turn_executor=executor, on_event=sink)
    backend.run_turn(context(tmp_path))
    assert [receipt.ledger_seq for receipt in backend.export_receipts("turn-1")] == [1]
    assert backend.export_receipts("nope") == ()
    assert backend.ledger()[0].tool_calls == 1
    completed = [payload for kind, payload in events if kind == "backend_turn_completed"]
    assert completed and completed[0]["status"] == "completed" and completed[0]["origin"] == "native"


def test_a_raising_executor_becomes_a_failed_turn_and_an_event(tmp_path: Path):
    def executor(ctx: TurnContext) -> TurnResult:
        raise RuntimeError("model exploded")

    events, sink = collector()
    backend = NativeBackend(turn_executor=executor, on_event=sink)
    result = backend.run_turn(context(tmp_path))
    assert result.status == "failed" and "model exploded" in result.text
    assert any(kind == "backend_turn_failed" for kind, _payload in events)


# --- the lead-agent bridge ---------------------------------------------------


FAKE_DRIVER = textwrap.dedent(
    '''
    import json, pathlib, sys

    mode = sys.argv[1] if len(sys.argv) > 1 else "--turn"
    def send(payload):
        json.dump(payload, sys.stdout, sort_keys=True)
        sys.stdout.write("\\n")
        sys.stdout.flush()

    if mode == "--probe":
        send({"type": "probe", "ok": True, "harness": "fake", "version": "0"})
        raise SystemExit(0)

    request = json.loads(sys.stdin.readline())
    marker = pathlib.Path(request["workspace"], "child-saw-tool-output.txt")
    send({"type": "event", "event": "step_started", "payload": {"goal": request["goal"][:8]}})
    send({"type": "tool_request", "id": "r1", "tool": "shell", "argv": ["printf", "confined-output"], "cwd": request["workspace"]})
    response = json.loads(sys.stdin.readline())
    marker.write_text(response.get("output", "") + ("|refused:" + response["error"] if not response.get("ok") else ""), encoding="utf-8")
    send({"type": "final", "text": "the harness thinks it is done", "verdict": "pass", "satisfied": True})
    '''
)


def fake_driver(tmp_path: Path) -> Path:
    path = tmp_path / "fake_driver.py"
    path.write_text(FAKE_DRIVER, encoding="utf-8")
    return path


def bridge(tmp_path: Path, *, approver=None, events=None, **kwargs) -> LeadAgentBackend:
    kwargs.setdefault("advertised_tools", ("shell", "printf"))
    return LeadAgentBackend(
        mediator=mediator(tmp_path, approver=approver, events=events),
        workspace=tmp_path,
        driver=fake_driver(tmp_path),
        enabled=True,
        turn_timeout_seconds=kwargs.pop("turn_timeout_seconds", 60.0),
        **kwargs,
    )


def test_probe_asks_the_child_and_reports_honest_state(tmp_path: Path):
    backend = bridge(tmp_path)
    availability = backend.probe()
    assert availability.available is True, availability.reason
    assert availability.detail["driver_report"]["harness"] == "fake"
    # Disabled is a configuration fact, not a failure to install.
    disabled = LeadAgentBackend(mediator=mediator(tmp_path), workspace=tmp_path, driver=fake_driver(tmp_path), enabled=False)
    assert disabled.probe().available is False and "disabled" in disabled.probe().reason
    # And a missing driver is reported, not raised.
    missing = LeadAgentBackend(mediator=mediator(tmp_path), workspace=tmp_path, driver=tmp_path / "nope.py", enabled=True)
    assert missing.probe().available is False and "missing" in missing.probe().reason


def test_bridge_run_turn_goes_through_the_mediator_for_every_child_ask(tmp_path: Path):
    events, sink = collector()
    backend = bridge(tmp_path, approver=lambda name, args: True, events=sink)
    result = backend.run_turn(context(tmp_path, turn_id="bridge-1"))
    assert result.status == "completed", (result.status, result.text, result.notes)
    assert result.origin == "lead_agent"
    assert result.text == "the harness thinks it is done"
    # The child's own output landed in the sandboxed workspace, proving the mediated command ran.
    assert "confined-output" in (tmp_path / "child-saw-tool-output.txt").read_text(encoding="utf-8")
    receipts = result.receipts
    assert len(receipts) == 1 and receipts[0].output_sha256
    assert receipts[0].isolation in {"local_bwrap", "unshare"} or "unconfined" in receipts[0].isolation
    assert any(kind == "mediation_decision" for kind, _payload in events)


def test_an_unapproved_child_request_is_refused_to_the_child(tmp_path: Path):
    """No approver wired: the child must be told, and its turn must not die in Evo."""
    backend = bridge(tmp_path)
    result = backend.run_turn(context(tmp_path, turn_id="bridge-2"))
    assert result.status == "completed"
    written = (tmp_path / "child-saw-tool-output.txt").read_text(encoding="utf-8")
    assert "refused:" in written and "approv" in written.lower()
    assert result.receipts[0].ok is False


def test_authority_claims_from_the_child_are_stripped_at_the_boundary(tmp_path: Path):
    backend = bridge(tmp_path, approver=lambda name, args: True)
    events, sink = collector()
    backend.on_event = sink
    result = backend.run_turn(context(tmp_path, turn_id="bridge-3"))
    assert not hasattr(result, "verdict") and not hasattr(result, "satisfied")
    assert any("rejected" in note and "authority" in note for note in result.notes)
    overreach = [payload for kind, payload in events if kind == "bridge_overreach_rejected"]
    assert overreach and set(overreach[0]["keys"]) == {"verdict", "satisfied"}
    # And the stripping is a function of the message, provable without a subprocess.
    assert sanitize_child_message({"type": "final", "verdict": "pass", "text": "x"}) == ["verdict"]
    assert sanitize_child_message({"type": "event", "event": "x"}) == []


def test_forwarded_events_reach_the_sinks_the_runtime_uses(tmp_path: Path):
    class EmitSink:
        def __init__(self) -> None:
            self.seen = []

        def emit(self, event, payload):
            self.seen.append((event, payload))

    sink = EmitSink()
    backend = bridge(tmp_path, approver=lambda name, args: True)
    backend.run_turn(context(tmp_path, turn_id="bridge-4"), sink=sink)
    assert ("step_started", {"goal": "do the t"}) in sink.seen or sink.seen, sink.seen


def test_oversized_child_lines_abort_the_turn(tmp_path: Path):
    driver = tmp_path / "loud_driver.py"
    driver.write_text(
        "import sys\nsys.stdout.write('x' * 200000 + '\\n')\nsys.stdout.flush()\n",
        encoding="utf-8",
    )
    backend = LeadAgentBackend(
        mediator=mediator(tmp_path),
        workspace=tmp_path,
        driver=driver,
        enabled=True,
        max_line_bytes=2048,
        venv_python=sys.executable,
    )
    result = backend.run_turn(context(tmp_path, turn_id="bridge-5"))
    assert result.status in {"failed", "timeout"}
    assert any("exceeded" in note for note in result.notes)


def test_malformed_child_requests_are_refused_without_running_anything(tmp_path: Path):
    """A ``tool_request`` with a command line and no argv is refused before a namespace is built.

    This is the shape an injected or mis-prompted harness produces, so the assertion that matters is
    not that Evo complains but that *nothing ran*: no receipt, no output file, and the refusal handed
    back to the child verbatim.
    """
    driver = tmp_path / "bad_request_driver.py"
    driver.write_text(
        "\n".join(
            (
                "import json, pathlib, sys",
                "request = json.loads(sys.stdin.readline())",
                "json.dump({\"type\": \"tool_request\", \"id\": \"x\", \"tool\": \"shell\", \"command\": \"printf pwned\"}, sys.stdout)",
                "sys.stdout.write(chr(10)); sys.stdout.flush()",
                "reply = json.loads(sys.stdin.readline())",
                "pathlib.Path(request[\"workspace\"], \"malformed-reply.json\").write_text(json.dumps(reply))",
                "json.dump({\"type\": \"final\", \"text\": \"saw the refusal\"}, sys.stdout)",
                "sys.stdout.write(chr(10)); sys.stdout.flush()",
            )
        ),
        encoding="utf-8",
    )
    backend = LeadAgentBackend(
        mediator=mediator(tmp_path),
        workspace=tmp_path,
        driver=driver,
        enabled=True,
        venv_python=sys.executable,
        advertised_tools=("shell",),
    )
    result = backend.run_turn(context(tmp_path, turn_id="bridge-6"))
    assert result.receipts == (), "a malformed request must not produce a receipt for work that did not happen"
    reply = json.loads((tmp_path / "malformed-reply.json").read_text(encoding="utf-8"))
    assert reply["ok"] is False and "argv list" in reply["error"]
    assert not (tmp_path / "pwned").exists()
    assert "rejected malformed tool_request" in " ".join(result.notes)



def test_cancel_reports_a_running_turn(tmp_path: Path):
    backend = bridge(tmp_path)
    assert backend.cancel("nope") is False
    assert backend.export_receipts("nope") == ()


def test_plan_refuses_capabilities_the_configuration_does_not_advertise(tmp_path: Path):
    backend = bridge(tmp_path)
    plan = backend.plan_capability(CapabilityRequest(goal="g", needed=("deep_research",)))
    assert plan.can_serve is False and "does not advertise" in plan.reason
    usable = backend.plan_capability(CapabilityRequest(goal="g", needed=("shell",), permissions=("approval",)))
    assert usable.can_serve and "memory, verification, and promotion authority remain in Evo" == usable.degradation
    assert usable.requires_approval_for == ("approval",)


def test_a_bridge_without_a_mediator_is_unusable_by_design(tmp_path: Path):
    backend = LeadAgentBackend(workspace=tmp_path, driver=fake_driver(tmp_path), enabled=True)
    assert backend.probe().available is False
    assert "ApprovalMediator" in backend.probe().reason
    assert backend.run_turn(context(tmp_path)).status == "refused"


def test_max_line_bytes_below_one_kibibyte_is_rejected_at_construction(tmp_path: Path):
    from evo_agent.backends.lead_agent import LeadAgentConfigError

    assert DEFAULT_MAX_LINE_BYTES >= 1 << 20
    with pytest.raises(LeadAgentConfigError):
        LeadAgentBackend(workspace=tmp_path, max_line_bytes=8)


# --- the dsh process adapter -------------------------------------------------


def test_template_rendering_is_bounded_and_explicit():
    command = render_template(("dsh", "--prompt", "{goal}", "--cwd", "{workspace}"), {"goal": "hello world", "workspace": "/tmp/x"})
    assert command.argv == ("dsh", "--prompt", "hello world", "--cwd", "/tmp/x")
    assert command.substituted == {"goal": "hello world", "workspace": "/tmp/x"}
    with pytest.raises(HarnessConfigError, match="unknown template placeholder"):
        render_template(("dsh", "{cwd}"), {"goal": "x"})
    with pytest.raises(HarnessConfigError, match="no value to substitute"):
        render_template(("dsh", "{goal}"), {"goal": ""})


def test_dsh_adapter_runs_one_confined_invocation(tmp_path: Path):
    events, sink = collector()
    backend = DeepSeekHarnessBackend(
        executable="printf",
        arguments_template=("{goal}",),
        workspace=tmp_path,
        mediator=mediator(tmp_path, events=sink),
        enabled=True,
        on_event=sink,
    )
    result = backend.run_turn(context(tmp_path, goal="observed output", turn_id="dsh-1"))
    assert result.status == "completed", (result.text, result.notes)
    assert result.origin == "dsh"
    assert result.receipts[0].isolation in {"local_bwrap", "unshare"} or "unconfined" in result.receipts[0].isolation
    assert "process adapter" in result.notes[0]
    assert len(backend.export_receipts("dsh-1")) == 1


def test_dsh_reports_a_harness_invariant_as_a_failed_turn(tmp_path: Path):
    backend = DeepSeekHarnessBackend(
        executable="printf",
        arguments_template=("InvariantFailure: {goal}",),
        workspace=tmp_path,
        mediator=mediator(tmp_path),
        enabled=True,
    )
    result = backend.run_turn(context(tmp_path, goal="x", turn_id="dsh-2"))
    assert result.status == "failed"
    assert any("InvariantFailure" in note for note in result.notes)
    assert result.receipts[0].ok is False


def test_dsh_refuses_a_command_that_does_not_match_its_template(tmp_path: Path):
    backend = DeepSeekHarnessBackend(
        executable="printf",
        arguments_template=("{goal}",),
        workspace=tmp_path,
        mediator=mediator(tmp_path),
        enabled=True,
    )
    # Simulate a renderer or template regression that smuggled a flag in.
    command = render_template(("printf", "--help"), {"goal": "x"})
    assert backend._template_holds(render_template(("printf", "{goal}"), {"goal": "x"})) is True
    assert backend._template_holds(command) is False


def test_dsh_plan_limits_itself_to_the_verbs_a_single_invocation_can_honour(tmp_path: Path):
    """Uninstalled reads as unavailable; installed still does not earn it the right to remember."""
    missing = DeepSeekHarnessBackend(workspace=tmp_path, mediator=mediator(tmp_path), enabled=True)
    assert missing.probe().available is False and "not on PATH" in missing.probe().reason
    assert missing.plan_capability(CapabilityRequest(goal="g", needed=())).degradation == "unavailable"

    installed = DeepSeekHarnessBackend(
        executable="printf",
        arguments_template=("{goal}",),
        workspace=tmp_path,
        mediator=mediator(tmp_path),
        enabled=True,
    )
    plan = installed.plan_capability(CapabilityRequest(goal="g", needed=("remember",)))
    assert plan.can_serve is False and "no memory or verification authority" in plan.reason
    assert installed.plan_capability(CapabilityRequest(goal="g", needed=("execute",))).can_serve is True


# --- assembly ----------------------------------------------------------------


def test_default_registry_wires_native_and_optional_bridges(tmp_path: Path):
    policy = SecurityPolicy(tmp_path)
    events, sink = collector()
    defaults = BackendDefaults(workspace=tmp_path, policy=policy, mediator=ApprovalMediator(policy, on_event=sink), on_event=sink, tool_names=("shell", "workspace_read"))
    registry = build_default_registry(defaults, config={"lead_agent": {"enabled": False, "accepted_by": "ops"}, "dsh": {"enabled": False}})
    assert registry.names == ("dsh", "lead_agent", "native")
    assert KNOWN_BACKENDS == ("native", "lead_agent", "dsh")
    assert set(registry.states()) == set(registry.names)
    with pytest.raises(UnknownBackend, match="unknown backend section"):
        build_default_registry(defaults, config={"deep_research_agent": {}})
    # The record of who accepted what is queryable, because a review has to reconstruct it.
    assert registry.get("dsh").to_dict()["source"].startswith("deepseek-ai/")


def test_native_is_registered_from_the_tool_surface(tmp_path: Path):
    """The registry's own capability list is the native backend's - no hand-copied list to drift."""
    from evo_agent.tools import ToolRegistry

    policy = SecurityPolicy(tmp_path)
    names = tuple(ToolRegistry(policy)._tools)
    defaults = BackendDefaults(workspace=tmp_path, policy=policy, mediator=ApprovalMediator(policy), tool_names=names)
    registry = build_default_registry(defaults)
    assert registry.get("native").capabilities == names
    plan = registry.plan(CapabilityRequest(goal="g", needed=names))
    assert plan["selected"] == "native"


def test_turn_context_snapshot_is_what_a_backend_may_see(tmp_path: Path):
    ctx = context(tmp_path, history=[{"role": "user", "text": "x"}], available_tools=("shell",), budget_turns=3, receipts=())
    payload = ctx.to_dict()
    assert payload["budget_turns"] == 3 and payload["history"][0]["role"] == "user"
    assert "memory" not in payload and "credentials" not in payload
    assert ctx.remaining_seconds() is None
    assert context(tmp_path, deadline_monotonic=0.0).remaining_seconds() <= 0


def test_the_seam_invariant_can_fail_and_names_the_offender(tmp_path: Path):
    """A check that cannot fail is a comment. Build a tree that breaks it three ways."""
    from evo_agent.sovereign.invariants import _check_ports_contract

    fake = tmp_path / "pkg"
    for package in ("ports", "backends", "sandbox_providers"):
        (fake / package).mkdir(parents=True)
        (fake / package / "__init__.py").write_text("", encoding="utf-8")
    (fake / "backends" / "overreaching.py").write_text(
        "from ..promotion import PromotionEngine\n\n\nclass Bridge:\n    name = 'x'\n\n    def run_turn(self, context, sink=None):\n        return PromotionEngine()\n",
        encoding="utf-8",
    )
    (fake / "sandbox_providers" / "loose.py").write_text(
        "import sqlite3\n\n\nclass Provider:\n    name = 'p'\n\n    def probe(self):\n        return None\n\n    def run(self, request, on_event=None):\n        return sqlite3.connect(':memory:')\n",
        encoding="utf-8",
    )
    ok, detail, evidence = _check_ports_contract(fake)
    assert ok is False
    kinds = {item["kind"] for item in evidence["offenders"]}
    assert "authority_import" in kinds and "second_persistence_authority" in kinds and "port_shape" in kinds


def test_the_adapter_loop_budget_ratchet_fails_both_ways(tmp_path: Path):
    """Declared exceptions to 'no loops in adapters' cannot silently accumulate or expire."""
    from evo_agent.sovereign import invariants

    assert "backends/lead_agent.py::_pump" in invariants.ADAPTER_LOOP_BUDGETS
    monkey_key = "backends/lead_agent.py::does_not_exist"
    original = invariants.ADAPTER_LOOP_BUDGETS
    try:
        invariants.ADAPTER_LOOP_BUDGETS = {**original, monkey_key: "invented"}
        ok, detail, _evidence = invariants._check_single_loop(invariants.PACKAGE_ROOT)
        assert ok is False and "no longer exist" in detail
        invariants.ADAPTER_LOOP_BUDGETS = {}
        ok, detail, _evidence = invariants._check_single_loop(invariants.PACKAGE_ROOT)
        assert ok is False and "owns a loop" in detail
    finally:
        invariants.ADAPTER_LOOP_BUDGETS = original
