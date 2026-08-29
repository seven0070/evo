"""The closed loop, end to end: propose → materialize → measure → promote → *behave differently*.

This file is P3's definition of done. Everything else in the phase is infrastructure whose only claim is
that it changes what the agent does, and the only test worth having for that claim is one that walks the
whole spine and asserts the behaviour moved - then moved back.

Three properties are checked here, in this order, because each is what an earlier phase was missing:

1. **Causality.** Activating a version changes ``max_tasks_per_cycle`` in the running process, without a
   restart, on the next cycle. Before P3 the promoted payload was a string in a database row.
2. **Falsifiability.** The digest the experiment measured is the digest the runtime loads, and an edit
   anywhere in between is caught - by promotion if the candidate was edited before staging, by the
   runtime on the next cycle if the *activated* version is edited afterwards.
3. **Reversibility.** Rolling back restores the earlier behaviour in the same process, including the keys
   the withdrawn overlay no longer mentions.

The loop is driven through ``PromotionEngine.promote`` rather than by writing an overlay directory by
hand. A test that poked the files directly would prove the resolver works while leaving every claim about
the promotion path - staging, the manifest hash, the activation record - untested, and the promotion path
is exactly where an overlay can be lost.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from evo_agent import (
    AgentRuntime,
    EvolutionProposal,
    ProposalRisk,
    ProposalStatus,
    RuntimeResourceLimits,
    RuntimeState,
)
from evo_agent.active_version import ACTIVATION_RECORD, OVERLAY_DIRNAME, resolve, verify_activation
from evo_agent.benchmark import EvolutionEvidence
from evo_agent.materialization import materialize
from evo_agent.models import (
    ComparisonClass,
    ExperimentStatus,
    PromotionApprovalStatus,
    PromotionStatus,
    VersionStatus,
)
from evo_agent.promotion import PromotionEngine
from evo_agent.sandbox import SandboxEngine
from evo_agent.storage import SQLiteStore

SOURCE_STUBS = ("kernel.py", "security.py", "verifier.py", "storage.py", "sandbox.py", "runtime.py")

#: Goals the runtime classifies as read-only, so a cycle's task count is set by the *budget* under test
#: rather than by which of them happens to need a human approval first.
READY_GOALS = (
    "list the files",
    "list the files in the workspace",
    "list the files in the workspace root",
    "list the files in the workspace again",
    "list the files in the workspace root again",
)
RUNTIME_OVERLAY = {"config/runtime.json": {"resource_limits": {"max_tasks_per_cycle": 3}}}


@dataclass
class Loop:
    """One fully wired agent: its own production tree, ledger, version registry and sandbox root."""

    production: Path
    registry: Path
    store: SQLiteStore
    engine: PromotionEngine
    runtime: AgentRuntime
    sandbox: SandboxEngine
    proposal: EvolutionProposal
    experiment: Any = None

    def overlay(self) -> Any:
        return resolve(self.registry, source_root=self.production)

    def cycle(self) -> Any:
        return self.runtime.run_cycle()

    def staged(self, version_id: str) -> Path:
        return self.registry / "versions" / version_id


def proposal_for(target: str = "strategy parameters") -> EvolutionProposal:
    """A proposal whose payload is a real budget change, aimed at a kind P3 can materialize.

    ``strategy-selection`` and ``tool_calling`` are accepted by the engine but would materialize nothing
    for this payload; the loop would then "succeed" while measuring an overlay identical to the baseline,
    which is the shape of test that passes for the wrong reason.
    """
    return EvolutionProposal(
        "proposal_closed_loop",
        "2026-09-01T00:00:00+00:00",
        ["exp"],
        ["eval"],
        "0.3.0",
        target,
        "Raise the per-cycle task budget so throughput is measured rather than assumed.",
        [{"experience_id": "exp", "evaluation_id": "eval", "outcome": "failure"}],
        "Set max_tasks_per_cycle to three for verified throughput.",
        "Improve verified completion.",
        ["Regression risk."],
        ["strategy_params"],
        [],
        0.8,
        "Compare tasks started per cycle.",
        "Restore the previous per-cycle budget.",
        ProposalStatus.APPROVED,
        ProposalRisk.LOW,
        "0.3.0",
    )


def build_loop(tmp_path: Path, *, overlay_payload: dict | None = None, target: str = "strategy parameters") -> Loop:
    """Construct the agent, then benchmark one approved proposal with ``overlay_payload`` on the candidate.

    The payload is passed through ``run_experiment``, so it is materialized by the same call that
    benchmarks: there is no version of this fixture where the payload reaches disk by a path the
    experiment did not measure.
    """
    production = tmp_path / "production"
    (production / "evo_agent").mkdir(parents=True)
    for name in SOURCE_STUBS:
        (production / "evo_agent" / name).write_text("# controlled test source\n", encoding="utf-8")
    registry = tmp_path / "registry"
    store = SQLiteStore(tmp_path / "workspace" / ".evo" / "agent.sqlite3")
    proposal = proposal_for(target)
    store.save_proposal(proposal)
    sandbox = SandboxEngine(store, production, tmp_path / "sandbox", timeout_seconds=10)
    engine = PromotionEngine(store, production, registry)
    runtime = AgentRuntime(
        tmp_path / "workspace",
        limits=RuntimeResourceLimits(max_total_runtime=120, max_tasks_per_cycle=1, max_queue_size=16),
        source_root=production,
        versions_root=registry,
    )
    loop = Loop(
        production=production,
        registry=registry,
        store=store,
        engine=engine,
        runtime=runtime,
        sandbox=sandbox,
        proposal=proposal,
    )
    loop.experiment = sandbox.run_experiment(
        proposal.proposal_id,
        command=("python3", "-m", "pytest", "-q", "test_sandbox_controlled.py"),
        retain_sandbox=True,
        candidate_overlay=overlay_payload,
    )
    return loop


def evidence_for(loop: Loop, evidence_id: str = "evidence_closed_loop") -> str:
    """Fabricate the benchmark verdict, as the other promotion tests do.

    Only the *evidence* is fabricated: it is the input the promotion gate trusts, and building it by hand
    keeps this file about the spine rather than about the statistics in ``compare_with_baseline``.
    """
    experiment = loop.experiment
    overlay = (experiment.resource_information or {}).get("overlay") or {}
    loop.store.save_evolution_evidence(
        EvolutionEvidence(
            evidence_id=evidence_id,
            experiment_id=experiment.experiment_id,
            proposal_id=loop.proposal.proposal_id,
            benchmark_id="benchmark-test",
            baseline_version=experiment.baseline_version,
            candidate_version=experiment.candidate_version,
            trial_count=6,
            baseline_metrics={"success_rate": 0.5, "verification_rate": 0.5},
            candidate_metrics={"success_rate": 1.0, "verification_rate": 1.0},
            metric_differences={"success_rate": 0.5},
            regression_results={key: [] for key in ("functional_regressions", "verification_regressions", "timeout_regressions", "efficiency_regressions", "safety_regressions")},
            safety_results={key: True for key in ("production_unchanged", "candidate_isolated", "network_denied", "host_secrets_absent", "bounded_commands", "candidate_safety_ok")},
            target_improvement=True,
            decision=ComparisonClass.BETTER,
            decision_reason=["per-cycle task throughput improved"],
            benchmark_version="benchmark-v1",
            evaluator_version="benchmark-evaluator-v1",
            created_at="2026-09-01T00:00:00+00:00",
            reproducibility_metadata={"deterministic_seed": 0, "overlay": overlay},
        )
    )
    return evidence_id


def promote_loop(loop: Loop, *, evidence_id: str | None = None) -> tuple[Any, str]:
    """Evidence → registration → request → approval → activation, through the real gates in order."""
    evidence_id = evidence_id or evidence_for(loop)
    candidate = loop.engine.register_candidate(loop.experiment.experiment_id, evidence_id)
    request = loop.engine.request_promotion(candidate.version_id, evidence_id, requested_by="reviewer")
    assert request.approval_status is PromotionApprovalStatus.PENDING
    # Expressed as "unchanged" rather than "still repo-default", because this helper runs for the second
    # and third version too, and on those the current state *is* an overlay. The invariant is that a
    # request nobody approved cannot move it.
    before = loop.overlay()
    with pytest.raises(PermissionError):
        loop.engine.promote(request.promotion_id)
    after = loop.overlay()
    assert (after.digest, after.source, after.version_id) == (before.digest, before.source, before.version_id), (
        "an unapproved request changed what the agent loads"
    )
    if before.source == "repo-default":
        # The registry always has a link: bootstrapping records v0 as the baseline precisely so that a
        # rollback has something to return to. So "not promoted" means "still v0", not "nothing active".
        assert loop.engine.active_version().version_id == "v0"
    loop.engine.approve_promotion(request.promotion_id, "Promote the measured candidate")
    return loop.engine.promote(request.promotion_id), candidate.version_id



def effective_state(runtime: AgentRuntime) -> dict[str, Any]:
    """Everything a capability overlay is allowed to move, as one comparable value.

    A snapshot rather than a spot check: "rollback restored A" is only meaningful if it covers *all* the
    state an overlay can reach - the limits table, the orchestrator's policy, the caps bound onto its
    components, the tool registry's order and floors, and the recovery set. Comparing a subset is how a
    test passes while one leg of the overlay quietly outlives its version.
    """
    tools = getattr(runtime.kernel, "tools", None) if runtime.kernel is not None else None
    cognitive = runtime.cognitive
    return {
        "limits": runtime.limits.to_dict(),
        "policy": dict(getattr(cognitive, "policy", {}) or {}),
        "bound": {
            "decomposer.max_subtasks": getattr(getattr(cognitive, "decomposer", None), "max_subtasks", None),
            "replanner.max_replans": getattr(getattr(cognitive, "replanner", None), "max_replans", None),
        },
        "tool_order": list(tools.order()) if tools is not None else [],
        "risk_floors": dict(tools.risk_floors()) if tools is not None and hasattr(tools, "risk_floors") else {},
        "never_retry": sorted(item.value for item in runtime.recovery.never_retry_classes),
        "overlay_digest": runtime.active_overlay.digest if runtime.active_overlay is not None else "",
        "overlay_source": runtime.active_overlay.source if runtime.active_overlay is not None else "",
    }


def promote_payload(loop: Loop, payload: dict, evidence_id: str, target: str = "strategy parameters"):
    """Benchmark, evidence, approve, activate one payload - the whole spine for a single overlay.

    A second and third call produce successive versions of the same agent, which is what the transition
    tests need: each goes through its own experiment and its own approval, because an overlay that skipped
    benchmarking would not be testing the thing that is supposed to catch it.
    """
    proposal = proposal_for(target)
    object.__setattr__(proposal, "proposal_id", f"proposal_{evidence_id}")
    loop.store.save_proposal(proposal)
    loop.proposal = proposal
    loop.experiment = loop.sandbox.run_experiment(
        proposal.proposal_id,
        command=("python3", "-m", "pytest", "-q", "test_sandbox_controlled.py"),
        retain_sandbox=True,
        candidate_overlay=payload,
    )
    assert loop.experiment.status is ExperimentStatus.PASSED, loop.experiment.errors
    evidence_for(loop, evidence_id)
    promoted, version_id = promote_loop(loop, evidence_id=evidence_id)
    return promoted, version_id


# --- causality ---------------------------------------------------------------------------


def test_promotion_changes_what_the_next_cycle_does(tmp_path: Path):
    """Activate a version, and the same live process takes three tasks next cycle.

    No restart and no re-construction of the runtime: the loop re-resolves at cycle start, which is what
    makes ``promote`` causal and - symmetrically - what makes a rollback causal. An implementation that
    read the overlay once in ``__init__`` would pass every other test in this file.
    """
    loop = build_loop(tmp_path, overlay_payload=RUNTIME_OVERLAY)
    assert loop.experiment.status is ExperimentStatus.PASSED, loop.experiment.errors
    for goal in READY_GOALS:
        loop.runtime.enqueue_task(goal)

    first = loop.cycle()
    assert first.tasks_started == 1, "the baseline runtime takes one task per cycle"
    assert loop.runtime.limits.max_tasks_per_cycle == 1
    assert loop.overlay().source == "repo-default"

    record, version_id = promote_loop(loop)
    assert record.final_status is PromotionStatus.ACTIVE
    assert loop.runtime.limits.max_tasks_per_cycle == 1, "promotion must not reach into a running process"

    second = loop.cycle()
    assert second.tasks_started == 3
    assert loop.runtime.limits.max_tasks_per_cycle == 3
    overlay = loop.overlay()
    assert overlay.source == "active" and overlay.version_id == version_id
    assert overlay.resource_limit_overrides() == {"max_tasks_per_cycle": 3}
    assert loop.runtime.overlay_report["applied"]["resource_limits"]["max_tasks_per_cycle"] == {"from": 1, "to": 3}
    assert loop.runtime.overlay_report["consistent"] is True


def test_the_experiment_and_the_agent_agree_on_one_digest(tmp_path: Path):
    """What was measured is what is loaded, as one number three components produced.

    The digest in the materialization result, the digest the experiment recorded for the candidate, the
    digest in the staged version's manifest, and the digest the runtime resolved for its cycle are four
    values that must be equal. Three write paths computing one digest is the design; this is the test
    that fails the day anyone grows a fourth.
    """
    loop = build_loop(tmp_path, overlay_payload=RUNTIME_OVERLAY)
    candidate_dir = Path(loop.experiment.sandbox_location) / "candidate"
    materialized = json.loads((candidate_dir / OVERLAY_DIRNAME / "manifest.json").read_text(encoding="utf-8"))  # noqa: E501
    measured = loop.experiment.resource_information["overlay"]["candidate_digest"]
    assert materialized["digest"] == measured
    assert loop.experiment.resource_information["overlay"]["changed"] is True

    record, version_id = promote_loop(loop)
    health = record.health_result or {}
    assert health["overlay"]["consistent"] is True
    assert health["overlay"]["expected_digest"] == measured
    assert health["overlay"]["actual_digest"] == measured
    assert loop.store.version_by_id(version_id)["status"] == VersionStatus.ACTIVE.value

    loop.cycle()
    assert loop.runtime.active_overlay.digest == measured


def test_the_activation_is_audited_per_cycle(tmp_path: Path):
    """Every cycle records which capability set it ran under, in the ledger it already writes.

    Per cycle rather than per promotion, because the useful question is "what was the agent doing at
    03:00" and a promotion record answers it only if nothing else moved in between. The payload carries
    names and digests, never the values: thresholds and prompt text are exactly what a wider-read audit
    log should not accumulate.
    """
    loop = build_loop(tmp_path, overlay_payload=RUNTIME_OVERLAY)
    loop.cycle()  # before activation, so the ledger holds both states and the test can compare them
    _, version_id = promote_loop(loop)
    loop.cycle()
    events = loop.store.events_for_task(loop.runtime.runtime_id)
    resolved = [row for row in events if row["event_type"] == "overlay_resolved"]
    digests = [row for row in events if row["event_type"] == "active_capabilities_digest"]
    assert resolved and digests, "the cycle must say what it loaded, not merely that it ran"
    payload = resolved[-1]["payload"]
    assert payload["overlay"]["digest"]
    assert payload["overlay"]["documents"] == ["config/runtime.json"]
    assert payload["overlay"]["version_id"] == version_id
    assert payload["refused"] is False
    assert "max_tasks_per_cycle" not in json.dumps(payload)
    assert digests[-1]["payload"]["consistent"] is True
    assert len(digests[-1]["payload"]["digest"]) == 64
    # and the earlier cycle is in the same ledger honestly reporting "nothing overlaid"
    assert resolved[0]["payload"]["overlay"]["source"] == "repo-default"
    assert resolved[0]["payload"]["overlay"]["digest"] == digests[0]["payload"]["digest"]
    assert resolved[-1]["payload"]["overlay"]["digest"] != resolved[0]["payload"]["overlay"]["digest"]


# --- falsifiability ----------------------------------------------------------------------


def test_a_candidate_restaged_after_the_experiment_is_refused_by_promotion(tmp_path: Path):
    """The retained candidate is untrusted between benchmark and activation.

    Re-running the materializer over the retained candidate moves both the document and its manifest, so
    the staged copy is internally consistent - and *still* refused, because the number the experiment
    recorded is a different number. The staged-hash check cannot see this at all: the copy and its source
    agree. This is the only comparison that can.
    """
    loop = build_loop(tmp_path, overlay_payload=RUNTIME_OVERLAY)
    candidate_dir = Path(loop.experiment.sandbox_location) / "candidate"
    restaged = materialize("strategy parameters", {"config/runtime.json": {"resource_limits": {"max_tasks_per_cycle": 500}}}, candidate_dir)
    assert restaged.ok and restaged.digest != loop.experiment.resource_information["overlay"]["candidate_digest"]

    record, version_id = promote_loop(loop)
    assert record.final_status is not PromotionStatus.ACTIVE
    reason = (record.health_result or {}).get("reason", "")
    assert "differs from what the experiment measured" in reason, reason
    assert loop.runtime.limits.max_tasks_per_cycle == 1, "a refused activation must not change behaviour"
    assert loop.engine.active_version().version_id == "v0", "a refused promotion must leave the baseline active"
    assert loop.overlay().source == "repo-default"
    assert loop.store.version_by_id(version_id)["status"] == VersionStatus.ROLLED_BACK.value


def test_an_overlay_file_edited_inside_the_candidate_is_caught_by_its_manifest(tmp_path: Path):
    """The second, independent detection: the files no longer match the manifest beside them.

    Two checks rather than one because they fail for different reasons and catch different windows - this
    one fires even if someone forges a manifest, and the digest-of-files comparison fires even if the
    manifest and the experiment record were both rewritten to agree with each other (in which case the
    runtime's own activation-record check is what remains).
    """
    loop = build_loop(tmp_path, overlay_payload=RUNTIME_OVERLAY)
    document = Path(loop.experiment.sandbox_location) / "candidate" / OVERLAY_DIRNAME / "config" / "runtime.json"
    measured = loop.experiment.resource_information["overlay"]["candidate_digest"]
    assert document.is_file()
    document.chmod(0o600)
    document.write_text(json.dumps({"resource_limits": {"max_tasks_per_cycle": 500}}), encoding="utf-8")
    # the manifest beside it still says what was measured, which is exactly what makes this detectable
    assert (document.parent.parent / "manifest.json").read_text(encoding="utf-8").count(measured) == 1

    record, version_id = promote_loop(loop)
    assert record.final_status is not PromotionStatus.ACTIVE
    reason = (record.health_result or {}).get("reason", "")
    assert "does not match the one the candidate was benchmarked with" in reason, reason
    assert loop.engine.active_version().version_id == "v0", "a refused promotion must leave the baseline active"
    assert loop.runtime.limits.max_tasks_per_cycle == 1, "a refused activation must not change behaviour"
    assert loop.overlay().source == "repo-default"
    assert loop.store.version_by_id(version_id)["status"] == VersionStatus.ROLLED_BACK.value


def test_tampering_an_activated_version_stops_the_agent_from_serving(tmp_path: Path):
    """A mismatch is a refusal to serve, not a degraded service.

    After a verified activation the overlay file itself is rewritten. The agent cannot be trusted to keep
    running on bytes nobody benchmarked, and it cannot be trusted to *repair* them either, because repair
    means deciding which of two disagreeing copies is authoritative. Continuing while recording the
    mismatch would put the ledger in the position of describing a run that did not happen.
    """
    loop = build_loop(tmp_path, overlay_payload=RUNTIME_OVERLAY)
    loop.runtime.enqueue_task(READY_GOALS[0])
    _, version_id = promote_loop(loop)
    assert loop.cycle().tasks_started == 1

    document = loop.staged(version_id) / OVERLAY_DIRNAME / "config" / "runtime.json"
    original = document.read_text(encoding="utf-8")
    document.chmod(0o600)
    document.write_text(json.dumps({"resource_limits": {"max_tasks_per_cycle": 42}}), encoding="utf-8")

    assert verify_activation(loop.registry, loop.overlay())["consistent"] is False
    result = loop.cycle()
    assert result.stopped_reason == "overlay_digest_mismatch"
    assert result.tasks_started == 0, "a refused cycle must not also do the work"
    assert loop.runtime.state is RuntimeState.DEGRADED
    assert "activation record" in result.failures[0]
    report = loop.runtime.overlay_report
    assert report["consistent"] is False and report["applied"]["resource_limits"] == {}
    assert report["not_applied"] is True
    assert report["expected_digest"] != report["actual_digest"]
    # The tampered value was never applied: the refusal comes before the apply step, so the process keeps
    # the last *verified* configuration rather than adopting the corrupt one or reverting to a third
    # state that nobody benchmarked either.
    assert "42" not in json.dumps(report)
    assert loop.runtime.limits.max_tasks_per_cycle == 3

    # Fixing the cause is sufficient - nothing is cached across cycles - but leaving DEGRADED is the
    # operator's call, through the existing startup-revalidation path. P3 deliberately adds no automatic
    # self-recovery: an agent that halts on an unverified overlay and then un-halts itself has decided
    # that its own check was unimportant.
    again = loop.cycle()
    assert again.stopped_reason == "overlay_digest_mismatch", "the refusal repeats; it does not decay into a generic failure"
    assert again.tasks_started == 0
    document.chmod(0o600)
    document.write_text(original, encoding="utf-8")
    loop.runtime.start()
    loop.runtime.enqueue_task("list the files once more")
    restored = loop.cycle()
    assert restored.stopped_reason != "overlay_digest_mismatch", restored.failures
    assert restored.tasks_started == 1, "service resumed: the agent takes work again on the verified bytes"
    assert loop.runtime.limits.max_tasks_per_cycle == 3
    assert loop.runtime.overlay_report["consistent"] is True


def test_a_payload_the_schema_refuses_stops_the_experiment_rather_than_passing_quietly(tmp_path: Path):
    """A candidate that could not materialize must not produce evidence for "no change".

    ``run_experiment`` raises, the experiment is recorded ABORTED, and registration refuses it. The
    alternative - recording a successful experiment whose candidate is byte-identical to the baseline -
    would let an unmeasured payload walk through the one gate that trusts experiment results completely.
    """
    loop = build_loop(tmp_path, overlay_payload={"config/runtime.json": {"resource_limits": {"max_memory_bytes": 1}}})
    assert loop.experiment.status is ExperimentStatus.ABORTED
    assert "max_memory_bytes" in " ".join(loop.experiment.errors)
    evidence_id = evidence_for(loop)
    with pytest.raises(ValueError, match="Only a passed sandbox experiment"):
        loop.engine.register_candidate(loop.experiment.experiment_id, evidence_id)


# --- reversibility -----------------------------------------------------------------------


def test_rollback_returns_the_running_agent_to_the_previous_behaviour(tmp_path: Path):
    """Rollback is not a restart and not a promise: the next cycle behaves like the old version.

    The second half is the part an implementation typically loses. Once the overlay file is gone, a
    runtime that only ever *raised* a value when an overlay was present still holds the raised value in
    its own object, so withdrawal requires the apply step to be a merge over the shipped defaults. That
    is what ``reset`` reports, and what this test pins.
    """
    loop = build_loop(tmp_path, overlay_payload=RUNTIME_OVERLAY)
    for goal in READY_GOALS:
        loop.runtime.enqueue_task(goal)
    assert loop.cycle().tasks_started == 1
    _, version_id = promote_loop(loop)
    assert loop.cycle().tasks_started == 3

    rollback = loop.engine.rollback(version_id, "per-cycle throughput regressed in production")
    assert rollback.from_version == version_id
    assert rollback.status in {"rolled_back", "verified", "completed"}

    result = loop.cycle()
    assert loop.runtime.limits.max_tasks_per_cycle == 1
    assert result.tasks_started == 1
    assert result.stopped_reason != "overlay_digest_mismatch"
    applied = loop.runtime.overlay_report["applied"]
    assert applied["reset"] == ["max_tasks_per_cycle"]
    assert applied["resource_limits"]["max_tasks_per_cycle"] == {"from": 3, "to": 1}
    assert loop.overlay().source == "repo-default"
    assert verify_activation(loop.registry, loop.overlay())["consistent"] is True


def test_a_rollback_does_not_leave_the_restoration_unverified(tmp_path: Path):
    """The restored version gets its own activation record, or every later cycle refuses to serve.

    This is a failure mode the rollback path introduces by being correct: the link moves to a version
    with no overlay, and a verifier that still expects the promoted digest would report a mismatch and
    halt an agent that just did the right thing. Re-recording is the fix, and the test is that the agent
    keeps running.
    """
    loop = build_loop(tmp_path, overlay_payload=RUNTIME_OVERLAY)
    _, version_id = promote_loop(loop)
    loop.cycle()
    record_path = loop.registry / ACTIVATION_RECORD
    assert record_path.is_file()
    promoted = json.loads(record_path.read_text(encoding="utf-8"))
    assert promoted["version_id"] == version_id

    loop.engine.rollback(version_id, "exercise the rollback")
    restored = json.loads(record_path.read_text(encoding="utf-8"))
    assert restored["version_id"] != version_id
    assert restored["digest"] == resolve(loop.registry, source_root=loop.production).digest
    assert loop.cycle().stopped_reason != "overlay_digest_mismatch"
    assert loop.store.count_events("overlay_resolved") >= 3


# --- the boundary that makes the rest safe ------------------------------------------------


def test_an_overlay_never_touches_the_production_tree(tmp_path: Path):
    """The invariant every other claim here stands on: the loop cannot edit itself.

    ``max_tasks_per_cycle`` moved in the running process, so the *source* must not have moved at all.
    Checked by digest of the production tree before and after, plus the absence of any overlay directory
    inside it. A capability change achievable by writing a ``.py`` file would make every other rule in
    this file a detail.
    """
    loop = build_loop(tmp_path, overlay_payload=RUNTIME_OVERLAY)
    before = loop.engine._manifest_hash(loop.production)
    _, version_id = promote_loop(loop)
    loop.cycle()
    assert loop.runtime.limits.max_tasks_per_cycle == 3
    assert loop.engine._manifest_hash(loop.production) == before
    assert not (loop.production / OVERLAY_DIRNAME).exists()
    assert (loop.production / "evo_agent" / "runtime.py").read_text() == "# controlled test source\n"
    assert (loop.staged(version_id) / OVERLAY_DIRNAME / "config" / "runtime.json").is_file()


def test_an_overlay_nobody_measured_cannot_be_promoted(tmp_path: Path):
    """An overlay that appears in a candidate without an experiment is refused at activation.

    The resolver will happily read a hand-written overlay directory - it has to, because that is what a
    staged candidate looks like - so the protection is not "untrusted files are invisible". It is that the
    digest recorded by the experiment has to match the digest of the files that just became active. Here
    the experiment recorded no overlay at all, and a forged manifest claiming one is caught by both
    comparisons.
    """
    loop = build_loop(tmp_path, overlay_payload=None)
    assert loop.experiment.status is ExperimentStatus.PASSED, loop.experiment.errors
    assert loop.experiment.resource_information["overlay"]["changed"] is False
    candidate = Path(loop.experiment.sandbox_location) / "candidate"
    (candidate / OVERLAY_DIRNAME / "config").mkdir(parents=True)
    (candidate / OVERLAY_DIRNAME / "config" / "runtime.json").write_text(
        json.dumps({"resource_limits": {"max_tasks_per_cycle": 77}}), encoding="utf-8"
    )
    (candidate / OVERLAY_DIRNAME / "manifest.json").write_text(
        json.dumps({"digest": "0" * 64, "fragments": []}), encoding="utf-8"
    )

    for goal in READY_GOALS:
        loop.runtime.enqueue_task(goal)
    assert loop.cycle().tasks_started == 1, "a scratch candidate directory is not what the agent loads"
    record, version_id = promote_loop(loop)
    assert record.final_status is not PromotionStatus.ACTIVE
    reason = (record.health_result or {}).get("reason", "")
    # the experiment recorded an overlay (an empty one), and the forged manifest promises another
    assert "differs from what the experiment measured" in reason, reason
    assert loop.runtime.limits.max_tasks_per_cycle == 1
    assert loop.store.version_by_id(version_id)["status"] == VersionStatus.ROLLED_BACK.value
    assert loop.cycle().tasks_started == 1
    assert resolve(overlay_dir=candidate).resource_limit_overrides() == {"max_tasks_per_cycle": 77}, (
        "the resolver is not what protects the agent; it reads whatever it is given, which is why the "
        "activation check above has to be the thing that refuses"
    )

# --- consecutive transitions -------------------------------------------------------------


def test_rollback_restores_the_complete_previous_effective_state(tmp_path: Path):
    """A → B → C → rollback → B → rollback → A, compared as whole states.

    Each hop is a real promotion of a real experiment, and each state is the full snapshot above rather
    than the one field the overlay happened to name. That is the difference between "the link moved back"
    and "the agent behaves as it did": B changes budgets and the never-retry set, C changes the tool view
    and nothing else, so rolling back C must *re-adopt* B's additions while withdrawing C's - a merge
    over the shipped defaults is the only rule that gets both right at once, and it is the property a
    per-field apply silently fails.

    The equality assertions are exact dict comparisons on purpose. Asserting "3 != 2" would pass on a
    half-restored state; comparing every field the overlay can reach cannot.
    """
    loop = build_loop(tmp_path, overlay_payload=None)
    for goal in READY_GOALS:
        loop.runtime.enqueue_task(goal)
    assert loop.cycle().tasks_started == 1
    state_a = effective_state(loop.runtime)
    assert state_a["overlay_source"] == "repo-default", "A is the shipped state: nothing resolved, nothing overlaid"

    # B: budgets and a never-retry addition, both in the one document ``strategy parameters`` owns.
    payload_b = {
        "config/runtime.json": {
            "resource_limits": {"max_tasks_per_cycle": 3, "max_retry_count": 4},
            "recovery": {"never_retry": ["permission", "approval", "resource"]},
        }
    }
    record_b, version_b = promote_payload(loop, payload_b, "evidence_b")
    assert record_b.final_status is PromotionStatus.ACTIVE
    loop.cycle()
    state_b = effective_state(loop.runtime)
    assert loop.runtime.limits.max_tasks_per_cycle == 3
    assert "resource" in state_b["never_retry"], "an overlay leg that only ever grows is not reversible"
    assert state_b != state_a

    # C: a different document, a different consumer, and no mention of anything B set.
    payload_c = {"config/tools.json": {"preference": ["shell", "workspace_read"], "risk_floor_uplift": {"workspace_read": 2}}}
    record_c, version_c = promote_payload(loop, payload_c, "evidence_c", target="tool-selection")
    assert record_c.final_status is PromotionStatus.ACTIVE
    loop.cycle()
    state_c = effective_state(loop.runtime)
    assert state_c["tool_order"][:2] == ["shell", "workspace_read"]
    assert state_c["risk_floors"]["workspace_read"] == "high"
    # A version is a whole snapshot, not a patch on whatever is active: C was staged from the production
    # tree and carries only C's overlay, so B's budgets and B's never-retry addition are gone while C is
    # active. Asserted here rather than explained later because it is the property an operator is most
    # likely to assume the other way about, and it is the reason each activation runs its own experiment.
    assert state_c["limits"] == state_a["limits"], "activating C did not replace B's budgets"
    assert state_c["never_retry"] == state_a["never_retry"]

    # Roll back C: B's state returns *exactly* - B's budgets and never-retry addition re-adopted, C's tool
    # view withdrawn, and no residue of either in the other direction.
    loop.engine.rollback(version_c, "tool view regressed")
    result = loop.cycle()
    assert result.stopped_reason != "overlay_digest_mismatch", result.failures
    assert effective_state(loop.runtime) == state_b, "rollback to B did not restore B"

    # Roll back B: the shipped state returns, and the recovery addition goes with it.
    loop.engine.rollback(version_b, "budget change regressed")
    result = loop.cycle()
    assert result.stopped_reason != "overlay_digest_mismatch", result.failures
    assert effective_state(loop.runtime) == state_a, "rollback to A left something behind"
    assert loop.runtime.limits.max_retry_count == state_a["limits"]["max_retry_count"]
    assert "resource" not in effective_state(loop.runtime)["never_retry"]
    # and the refusal path is still reachable after all that: the registry's own record says A is active
    assert verify_activation(loop.registry, loop.overlay())["consistent"] is True


def test_rollback_restores_the_operators_baseline_not_the_class_default(tmp_path: Path):
    """The baseline a rollback returns to is *this process's* starting state.

    An agent launched with a raised queue budget and a customised cognitive policy must come back to those
    numbers when a version is withdrawn. Restoring the values the source tree ships with would be a second,
    surprising behaviour change hidden inside the recovery path - and the policy case is the sharper one,
    because the default agent's orchestrator is constructed with caps mirrored from the limits, so
    ``DEFAULT_POLICY`` and the running state genuinely differ.
    """
    loop = build_loop(tmp_path, overlay_payload=None)
    loop.runtime.limits.max_tasks_per_cycle = 5
    loop.runtime.limits.max_queue_size = 40
    loop.runtime._limits_defaults = loop.runtime.limits.to_dict()
    loop.runtime.cognitive.policy["max_replans"] = 3
    loop.runtime.cognitive.replanner.max_replans = 3
    loop.runtime._policy_defaults = dict(loop.runtime.cognitive.policy)
    baseline = effective_state(loop.runtime)

    payload = {"config/runtime.json": {"resource_limits": {"max_tasks_per_cycle": 2}},
               "config/cognitive_policy.json": {"policy": {"max_subtasks": 5}}}
    # one proposal can only carry the documents its own target owns, so the operator-baseline case needs
    # two promotions to move both consumers; the assertions below are about what survives their rollback.
    _, version_limits = promote_payload(loop, {"config/runtime.json": {"resource_limits": {"max_tasks_per_cycle": 2}}}, "evidence_limits")
    loop.cycle()
    assert loop.runtime.limits.max_tasks_per_cycle == 2
    assert loop.runtime.limits.max_queue_size == 40, "an untouched field was rewritten to the class default"
    assert loop.runtime.cognitive.policy["max_replans"] == 3, "the policy leg clobbered a cap no overlay named"
    assert "policy.max_replans" not in loop.runtime.overlay_report["applied"]["reset"]

    _, version_policy = promote_payload(
        loop,
        {"config/cognitive_policy.json": {"policy": {"max_subtasks": 5}}},
        "evidence_policy",
        target="planning configuration",
    )
    loop.cycle()
    assert loop.runtime.cognitive.decomposer.max_subtasks == 5
    assert loop.runtime.cognitive.policy["max_replans"] == 3

    loop.engine.rollback(version_policy, "restore")
    loop.cycle()
    assert effective_state(loop.runtime)["policy"]["max_subtasks"] == baseline["policy"]["max_subtasks"]
    assert loop.runtime.cognitive.replanner.max_replans == 3
    loop.engine.rollback(version_limits, "restore")
    result = loop.cycle()
    assert result.stopped_reason != "overlay_digest_mismatch", result.failures
    snapshot = effective_state(loop.runtime)
    for key in ("limits", "policy", "bound", "tool_order", "risk_floors", "never_retry"):
        assert snapshot[key] == baseline[key], f"{key} did not return to the state this agent started in"

