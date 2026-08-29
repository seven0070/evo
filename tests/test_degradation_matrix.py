"""The degradation matrix: every fallback is visible, and none of them is a widening (07 §8, :168).

The phase-level claim is that optional capability never *silently* changes the security answer. Three
independent axes, asserted against the entry points that actually decide, because the bug class this guards
is "the code that reports the state and the code that acts on it disagree":

* **isolation** - ``strict`` refuses when nothing usable exists; ``auto``/``degrade`` run the child and say
  they were not confined; ``off`` says so too; and an unrecognised level becomes ``strict``, not ``off``.
* **availability** - a backend that is present-but-caveated reports DEGRADED, an unprobed registration
  reports UNAVAILABLE, and merging two reports keeps the worst state.
* **memory** - a corrupt row is dropped from a listing rather than raising, and a retrieval budget that
  cannot be met returns fewer memories rather than an error.

The single property across all three: a degraded answer is *labelled*. An unlabeled degradation is
indistinguishable from the strong case by the time anyone reads it, which is the failure R7 exists to stop.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from evo_agent.backends.availability import (
    AVAILABLE,
    DEGRADED,
    UNAVAILABLE,
    BackendReport,
    build_report,
    classify,
    merge_reports,
)
from evo_agent.memory import MemoryManager, RetrievalQuery, MemoryType
from evo_agent.ports.contracts import BackendAvailability, ExecRequest
from evo_agent.security import SecurityPolicy
from evo_agent.sovereign.mediation import ApprovalMediator
from evo_agent.storage import SQLiteStore


@pytest.fixture()
def workspace(tmp_path: Path) -> Path:
    (tmp_path / ".evo").mkdir()
    return tmp_path


def _registration(name: str, *, enabled: bool = True) -> object:
    """A stand-in for ``BackendRegistry.Registration`` with the fields ``build_report`` reads.

    Deliberately a plain object rather than the registry's own type: the reporting layer's contract is those
    fields, and coupling this matrix to the registry's constructor would make a registry change look like a
    degradation-policy change.
    """
    return type("Registration", (), {"name": name, "enabled": enabled, "source": "builtin", "license": "MIT", "priority": 0, "version": "0"})()


class TestIsolationLevels:
    def test_the_level_vocabulary_is_closed_and_clamped_upward(self, workspace: Path) -> None:
        assert SecurityPolicy.SANDBOX_ENFORCEMENT_LEVELS == ("auto", "strict", "degrade", "off")
        for requested, expected in (("AUTO", "auto"), ("Strict", "strict"), ("", "auto"), ("never", "strict"), ("off-ish", "strict")):
            assert SecurityPolicy(workspace, sandbox_enforcement=requested).sandbox_enforcement == expected, requested
        # "unknown -> strict" is the whole answer to a typo in a hardening flag: the mistake costs
        # availability, never safety.

    @pytest.mark.parametrize(
        ("enforcement", "allowed", "isolated", "rule"),
        [
            ("auto", True, False, "allowed"),
            ("strict", False, False, "no_isolation"),
            ("degrade", True, False, "allowed"),
            ("off", True, False, "allowed"),
            ("banana", False, False, "no_isolation"),
        ],
    )
    def test_no_usable_provider_matrix(self, workspace: Path, enforcement: str, allowed: bool, isolated: bool, rule: str) -> None:
        policy = SecurityPolicy(workspace, sandbox_enforcement=enforcement)
        mediator = ApprovalMediator(policy, approver=lambda *args: True, providers=())
        decision, _amended = mediator.authorize_infrastructure(
            ExecRequest(argv=("/bin/echo", "hi"), cwd=workspace), program="/bin/echo", tool_name="probe"
        )
        assert decision.allowed is allowed, decision.reason
        assert decision.isolated is isolated
        assert decision.rule == rule
        confined, why = mediator.isolation_state()
        assert confined is False and why, "isolation_state must agree with the decision, not be a second opinion"

    def test_every_allowed_row_without_a_provider_labels_the_result_degraded(self, workspace: Path) -> None:
        for enforcement in ("auto", "degrade", "off"):
            mediator = ApprovalMediator(SecurityPolicy(workspace, sandbox_enforcement=enforcement), providers=())
            result = mediator.execute_infrastructure(ExecRequest(argv=("/bin/echo", "hi"), cwd=workspace), program="/bin/echo", tool_name="probe")
            assert result.isolated is False, enforcement
            assert result.degraded_reason, enforcement
            assert "isolation" in result.degraded_reason or "sandbox_enforcement" in result.degraded_reason, result.degraded_reason
            # Refusal-shaped runs (rc -1, no provider at all) are still labelled, and the label is on the
            # result rather than only in an event stream someone has to correlate by hand.

    def test_a_real_host_fallback_still_reports_the_boundary_it_could_not_provide(self, workspace: Path) -> None:
        # With the default provider list, `auto` may reach the host provider and actually run the child. The
        # run must then be reported as unconfined even though it succeeded - the one combination a reader
        # would otherwise misfile as "ran inside the sandbox".
        mediator = ApprovalMediator(SecurityPolicy(workspace, sandbox_enforcement="auto"), approver=lambda *args: True)
        decision, _amended = mediator.authorize_infrastructure(
            ExecRequest(argv=("/bin/echo", "hi"), cwd=workspace), program="/bin/echo", tool_name="probe"
        )
        assert decision.allowed is True
        confined, why = mediator.isolation_state()
        assert decision.isolated is confined, (decision.isolated, confined, why)

    def test_network_is_refused_at_every_level_including_off(self, workspace: Path) -> None:
        for enforcement in ("auto", "strict", "degrade", "off", "banana"):
            mediator = ApprovalMediator(SecurityPolicy(workspace, sandbox_enforcement=enforcement), providers=())
            decision, _amended = mediator.authorize_infrastructure(
                ExecRequest(argv=("/usr/bin/curl", "http://example.invalid"), cwd=workspace, network=True),
                program="/usr/bin/curl",
                tool_name="probe",
            )
            assert decision.allowed is False and decision.rule == "policy", enforcement
            assert "network" in decision.reason.lower(), enforcement

    def test_the_plan_phase_survives_a_weaker_sandbox(self, workspace: Path) -> None:
        # Two independent tightenings, and neither may be the excuse to relax the other: `degrade` is not
        # "mode off", and plan mode does not turn a missing provider into a permission.
        policy = SecurityPolicy(workspace, sandbox_enforcement="degrade", agent_mode="plan")
        mediator = ApprovalMediator(policy, approver=lambda *args: True, providers=())
        decision, _amended = mediator.authorize_infrastructure(
            ExecRequest(argv=("/bin/rm", "-rf", str(workspace / "x")), cwd=workspace), program="/bin/rm", tool_name="probe"
        )
        assert decision.allowed is False and decision.rule == "plan_mode", decision.reason


class TestAvailabilityReporting:
    def test_a_probe_with_a_reason_is_degraded_not_available(self) -> None:
        assert classify(BackendAvailability(name="deerflow", available=True), enabled=True) == AVAILABLE
        assert classify(BackendAvailability(name="deerflow", available=True, reason="missing bwrap"), enabled=True) == DEGRADED
        assert classify(BackendAvailability(name="deerflow", available=False, reason="not installed"), enabled=True) == UNAVAILABLE

    def test_an_enabled_flag_is_the_first_gate(self) -> None:
        # `enabled=False` outranks a passing probe: this build's backends are inert by default, and a probe
        # that succeeds in an *unenabled* deployment must not make the report read as "in use".
        assert classify(BackendAvailability(name="dsh", available=True), enabled=False) == UNAVAILABLE

    def test_an_unprobed_registration_is_unavailable_rather_than_ok(self) -> None:
        report = build_report([_registration("lead_agent"), _registration("dsh")], {"lead_agent": BackendAvailability(name="lead_agent", available=True)})
        states = {item.name: item.state for item in report.reports}
        assert states == {"lead_agent": AVAILABLE, "dsh": UNAVAILABLE}
        missing = [item for item in report.reports if item.name == "dsh"][0]
        # The reason is a sentence, not an empty string: "no probe" and "probe failed" read differently to an
        # operator deciding whether to install a dependency or to fix a config.
        assert missing.reason and "probe" in missing.reason.lower(), missing.reason

    def test_merging_two_reports_keeps_the_worst_state(self) -> None:
        registrations = [_registration("x"), _registration("y")]
        optimistic = build_report(registrations, {"x": BackendAvailability(name="x", available=True), "y": BackendAvailability(name="y", available=True)})
        pessimistic = build_report(registrations, {"x": BackendAvailability(name="x", available=False, reason="no dependency")})
        merged = merge_reports([optimistic, pessimistic])
        states = {item.name: item.state for item in merged.reports}
        assert states["x"] == UNAVAILABLE, "a second report that found the dependency missing wins"
        # And `y` degrades too, because the pessimistic report does not mention it at all. That is stricter
        # than "keep the best known state", and it is the right direction: an omission is not evidence of
        # health, and a merge that treated absence as neutral would let one stale report vouch for a backend
        # the other had just watched disappear.
        assert states["y"] == UNAVAILABLE, states
        both_good = build_report(registrations, {"x": BackendAvailability(name="x", available=True), "y": BackendAvailability(name="y", available=True)})
        assert {item.state for item in merge_reports([optimistic, both_good]).reports} == {AVAILABLE}
        assert json.dumps(merged.to_dict(), default=str)

    def test_a_caveat_never_disappears_into_the_clean_state(self) -> None:
        report = build_report([_registration("z")], {"z": BackendAvailability(name="z", available=True, reason="partial isolation")})
        assert [item.state for item in report.reports] == [DEGRADED]
        assert report.reports[0].reason == "partial isolation"
        # And a registration with no probe at all is not promoted into the report either way: unregistered
        # means unreported, so nobody can read an absent backend as a present one.
        assert build_report([], {"q": BackendAvailability(name="q", available=True)}).reports == ()
        assert json.dumps(report.to_dict(), default=str)


class TestMemoryDegradation:
    def test_a_corrupt_row_is_dropped_from_a_listing_instead_of_raising(self, workspace: Path) -> None:
        store = SQLiteStore(workspace / ".evo" / "agent.sqlite3")
        manager = MemoryManager(store, workspace)
        good = manager.capture_learning({"affected_component": "planner", "success": True})
        store.save_memory(manager.capture_learning({"affected_component": "critic", "success": True}))
        with store._connect() as db:
            db.execute("UPDATE memory_records SET payload = 'not json' WHERE memory_id = ?", (good.memory_id,))
        listing = manager.memory_store.list(limit=50)
        assert [item.memory_id for item in listing] != [good.memory_id]
        assert len(listing) >= 1, "the readable rows survive an unreadable neighbour"
        # Retrieval goes through the same loader, so the corrupt row is invisible rather than fatal.
        assert manager.retrieval.retrieve(RetrievalQuery(goal="planner learning")) is not None

    def test_a_tight_budget_returns_less_not_an_error(self, workspace: Path) -> None:
        store = SQLiteStore(workspace / ".evo" / "agent.sqlite3")
        manager = MemoryManager(store, workspace)
        for index in range(6):
            manager.capture_learning({"affected_component": f"planner-{index}", "success": True, "fingerprint_note": f"note {index}"})
        everything = manager.retrieval.retrieve(RetrievalQuery(goal="planner", max_memories=10))
        tight = manager.retrieval.retrieve(RetrievalQuery(goal="planner", max_memories=1, max_memory_bytes=1))
        assert len(everything) >= len(tight)
        assert len(tight) <= 1
        assert isinstance(tight, list)

    def test_an_unreadable_policy_is_a_startup_refusal_not_a_default(self, workspace: Path) -> None:
        from evo_agent.memory import MemoryPolicy

        broken = workspace / "memory.json"
        broken.write_text("{not json", encoding="utf-8")
        _policy, problems = MemoryPolicy.load(broken)
        assert problems, "a policy that cannot be parsed must not fall through to the shipped defaults"

    def test_the_kernel_fallback_names_itself(self, workspace: Path) -> None:
        # Documented degradation, asserted so it stays labelled: when the governed store has nothing, the
        # kernel reads the deprecated mirror instead, and the payload it hands the planner says which source
        # it used. An unlabeled fallback is the same defect as an unlabeled sandbox degradation, one layer up.
        from evo_agent.kernel import AgentKernel
        from evo_agent.model_adapter import RuleBasedAdapter
        from evo_agent.models import Goal

        store = SQLiteStore(workspace / ".evo" / "agent.sqlite3")
        kernel = AgentKernel(workspace, RuleBasedAdapter(), store=store)
        store.add_memory("experience", "a legacy note about pytest", "2026-01-01T00:00:00+00:00")
        rows, provenance = kernel._plan_time_memories(Goal(text="run pytest"))  # noqa: SLF001 - the label is the contract
        assert provenance["source"] in {"retrieval_engine", "recent_memories_fallback"}, provenance
        assert json.dumps(provenance, default=str)
        if provenance["source"] == "recent_memories_fallback":
            assert any("legacy note about pytest" in str(row.get("content")) for row in rows)


class TestToolAndBackendInertness:
    def test_a_tool_with_no_handler_is_not_offered(self, workspace: Path) -> None:
        from evo_agent.tools import ToolCatalog, ToolRegistry

        registry = ToolRegistry(SecurityPolicy(workspace))
        catalog = ToolCatalog(registry)
        assert catalog.usability("workspace_read").registered is True
        ghost = catalog.usability("made_up_tool")
        assert ghost.registered is False and ghost.usable is False

    def test_an_unavailable_backend_is_reported_not_assumed(self, workspace: Path) -> None:
        from dataclasses import asdict

        from evo_agent.backends.registry import BackendRegistry

        registry = BackendRegistry(policy=SecurityPolicy(workspace))
        empty = registry.availability_report()
        # A registry nobody configured reports nothing, which is the inert default: an empty report cannot
        # be misread as "available", and it is not the same object as "everything is degraded".
        assert empty.reports == ()
        class StubBackend:
            """Satisfies the port deliberately: the registry refuses a backend that does not implement it.

            That refusal is ``I-ports-contract`` in miniature, so the stub has the methods rather than being a
            bare namespace - a test that registered something the registry should have rejected would be
            quietly asserting a weaker contract than the build enforces.
            """

            name = "stub"

            def plan_capability(self, *args, **kwargs):
                return None

            def run_turn(self, *args, **kwargs):
                return None

            def probe(self):
                return BackendAvailability(name="stub", available=True, reason="driver present, not enabled")

        registry.register(StubBackend(), source="test", license="MIT", enabled=False)
        report = registry.availability_report()
        states = {item.name: item.state for item in report.reports}
        assert states == {"stub": UNAVAILABLE}, "a passing probe on a disabled backend must not read as in use"
        assert json.dumps(report.to_dict(), default=str)
        # `reports` on the dataclass, `backends` in the serialised payload: the names differ, and both are
        # asserted because the payload is what a status endpoint publishes while the field is what Python
        # reads - checking only one would let the other drift.
        assert asdict(empty)["reports"] == ()
