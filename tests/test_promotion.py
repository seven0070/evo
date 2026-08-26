from __future__ import annotations

import json
from pathlib import Path

import pytest

from evo_agent.benchmark import EvolutionEvidence
from evo_agent.evolver import EvolutionProposal
from evo_agent.models import ComparisonClass, ExperimentStatus, ProposalRisk, ProposalStatus, PromotionApprovalStatus, PromotionStatus, VersionStatus
from evo_agent.promotion import PromotionEngine
from evo_agent.sandbox import SandboxEngine
from evo_agent.storage import SQLiteStore


def make_proposal(proposal_id: str = "proposal_promotion") -> EvolutionProposal:
    return EvolutionProposal(
        proposal_id,
        "2026-09-01T00:00:00+00:00",
        ["exp"],
        ["eval"],
        "0.3.0",
        "strategy-selection",
        "Repeated strategy failure is visible in historical evidence.",
        [{"experience_id": "exp", "evaluation_id": "eval", "outcome": "failure"}],
        "Prefer the recovery strategy for comparable tasks.",
        "Improve verified completion.",
        ["Regression risk."],
        ["strategy_selection"],
        [],
        0.8,
        "Compare success and verification rates.",
        "Restore the previous strategy preference.",
        ProposalStatus.APPROVED,
        ProposalRisk.LOW,
        "0.3.0",
    )


def setup_candidate(tmp_path: Path, health_checker=None) -> tuple[PromotionEngine, SQLiteStore, object, object, Path]:
    production = tmp_path / "production"
    (production / "evo_agent").mkdir(parents=True)
    for relative in ("kernel.py", "security.py", "verifier.py", "storage.py", "sandbox.py"):
        (production / "evo_agent" / relative).write_text("# controlled test source\n", encoding="utf-8")
    store = SQLiteStore(tmp_path / "workspace" / ".evo" / "agent.sqlite3")
    proposal = make_proposal()
    store.save_proposal(proposal)
    sandbox = SandboxEngine(store, production, tmp_path / "sandbox", timeout_seconds=5)
    experiment = sandbox.run_experiment(proposal.proposal_id, retain_sandbox=True)
    assert experiment.status is ExperimentStatus.PASSED, experiment.errors
    evidence = EvolutionEvidence(
        evidence_id="evidence_promotion",
        experiment_id=experiment.experiment_id,
        proposal_id=proposal.proposal_id,
        benchmark_id="benchmark-test",
        baseline_version=experiment.baseline_version,
        candidate_version=experiment.candidate_version,
        trial_count=6,
        baseline_metrics={"success_rate": 0.5, "verification_rate": 0.5},
        candidate_metrics={"success_rate": 1.0, "verification_rate": 1.0},
        metric_differences={"success_rate": 0.5},
        regression_results={"functional_regressions": [], "verification_regressions": [], "timeout_regressions": [], "efficiency_regressions": [], "safety_regressions": []},
        safety_results={"production_unchanged": True, "candidate_isolated": True, "network_denied": True, "host_secrets_absent": True, "bounded_commands": True, "candidate_safety_ok": True},
        target_improvement=True,
        decision=ComparisonClass.BETTER,
        decision_reason=["target success rate improved"],
        benchmark_version="benchmark-v1",
        evaluator_version="benchmark-evaluator-v1",
        created_at="2026-09-01T00:00:00+00:00",
        reproducibility_metadata={"deterministic_seed": 0},
    )
    store.save_evolution_evidence(evidence)
    engine = PromotionEngine(store, production, tmp_path / "production-registry", health_checker=health_checker)
    candidate = engine.register_candidate(experiment.experiment_id, evidence.evidence_id)
    return engine, store, experiment, candidate, production


def test_eligibility_requires_better_evidence_and_explicit_promotion_approval(tmp_path: Path):
    engine, store, experiment, candidate, production = setup_candidate(tmp_path)
    request = engine.request_promotion(candidate.version_id, "evidence_promotion", requested_by="reviewer")
    assert request.eligibility_status.value == "eligible"
    assert request.approval_status is PromotionApprovalStatus.PENDING
    assert request.status is PromotionStatus.REQUESTED
    with pytest.raises(PermissionError):
        engine.promote(request.promotion_id)
    assert engine._active_version().version_id == "v0"
    approved = engine.approve_promotion(request.promotion_id, "Promote verified candidate")
    assert approved.approval_status is PromotionApprovalStatus.APPROVED
    assert store.promotion_request_by_id(request.promotion_id)


def test_successful_promotion_is_atomic_lineaged_and_reversible(tmp_path: Path):
    engine, store, experiment, candidate, production = setup_candidate(tmp_path)
    before = engine._manifest_hash(production)
    request = engine.request_promotion(candidate.version_id, "evidence_promotion")
    engine.approve_promotion(request.promotion_id, "Explicit human promotion approval")
    record = engine.promote(request.promotion_id)
    assert record.final_status is PromotionStatus.ACTIVE
    assert engine._active_version().version_id == candidate.version_id
    assert engine.active_link.resolve() == Path(engine.get_version(candidate.version_id).version_path).resolve()
    assert engine._previous_version(candidate.version_id).version_id == "v0"
    assert len(engine.store.find_versions(status=VersionStatus.ACTIVE.value)) == 1
    assert engine._manifest_hash(production) == before
    assert record.checkpoint_id
    assert store.promotion_record_by_id(request.promotion_id)
    events = {event["event_type"] for event in store.events_for_task("promotion")}
    assert {"promotion_requested", "promotion_eligibility_checked", "promotion_approved", "promotion_checkpoint_created", "candidate_staged", "candidate_integrity_verified", "promotion_started", "production_version_activated", "post_promotion_health_check", "promotion_completed"}.issubset(events)


def test_health_failure_triggers_native_rollback_and_preserves_failed_version(tmp_path: Path):
    engine, store, experiment, candidate, production = setup_candidate(tmp_path, health_checker=lambda path: {"healthy": False, "reason": "controlled health failure", "smoke_test": {"passed": False}})
    request = engine.request_promotion(candidate.version_id, "evidence_promotion")
    engine.approve_promotion(request.promotion_id, "Explicit human approval for controlled health test")
    record = engine.promote(request.promotion_id)
    assert record.final_status is PromotionStatus.ROLLED_BACK
    assert engine._active_version().version_id == "v0"
    assert engine.get_version(candidate.version_id).status is VersionStatus.ROLLED_BACK
    assert Path(engine.get_version(candidate.version_id).version_path).is_dir()
    rollback_rows = store.find_rollback_records()
    assert rollback_rows and rollback_rows[0]["status"] == "completed"
    assert store.promotion_record_by_id(request.promotion_id)
    events = {event["event_type"] for event in store.events_for_task("promotion")}
    assert {"rollback_started", "rollback_checkpoint_restored", "rollback_verified", "rollback_completed"}.issubset(events)


def test_manual_rollback_restores_previous_active_version(tmp_path: Path):
    engine, _, _, candidate, _ = setup_candidate(tmp_path)
    request = engine.request_promotion(candidate.version_id, "evidence_promotion")
    engine.approve_promotion(request.promotion_id, "Explicit promotion approval")
    record = engine.promote(request.promotion_id)
    rollback = engine.rollback(candidate.version_id, "Post-promotion regression", request.promotion_id)
    assert record.final_status is PromotionStatus.ACTIVE
    assert rollback.status == "completed"
    assert rollback.to_version == "v0"
    assert engine._active_version().version_id == "v0"
    assert engine.get_version(candidate.version_id).status is VersionStatus.ROLLED_BACK
    assert Path(engine.get_version(candidate.version_id).version_path).is_dir()


def test_integrity_mismatch_rejects_promotion_before_activation(tmp_path: Path):
    engine, _, _, candidate, _ = setup_candidate(tmp_path)
    candidate_source = Path(candidate.metadata["candidate_source_path"])
    (candidate_source / "tampered.txt").write_text("changed after benchmark", encoding="utf-8")
    request = engine.request_promotion(candidate.version_id, "evidence_promotion")
    assert request.status is PromotionStatus.REJECTED
    assert request.eligibility_status.value == "rejected"
    assert engine._active_version().version_id == "v0"
    with pytest.raises(PermissionError):
        engine.promote(request.promotion_id)


def test_toctou_integrity_check_rejects_candidate_changed_after_approval(tmp_path: Path):
    engine, _, _, candidate, _ = setup_candidate(tmp_path)
    request = engine.request_promotion(candidate.version_id, "evidence_promotion")
    engine.approve_promotion(request.promotion_id, "Approve only the exact benchmarked candidate")
    source = Path(candidate.metadata["candidate_source_path"])
    (source / "changed-after-approval.txt").write_text("TOCTOU", encoding="utf-8")
    with pytest.raises(PermissionError):
        engine.promote(request.promotion_id)
    assert engine._active_version().version_id == "v0"


def test_bad_decision_and_missing_approval_cannot_activate(tmp_path: Path):
    engine, store, experiment, candidate, _ = setup_candidate(tmp_path)
    bad_evidence = json.loads(store.evidence_by_id("evidence_promotion")["payload"])
    bad_evidence["evidence_id"] = "evidence_worse"
    bad_evidence["decision"] = "worse"
    class Evidence:
        evidence_id = "evidence_worse"
        experiment_id = experiment.experiment_id
        decision = ComparisonClass.WORSE
        proposal_id = experiment.proposal_id
        benchmark_id = "benchmark-test"
        baseline_version = experiment.baseline_version
        candidate_version = experiment.candidate_version
        created_at = "2026-09-01T00:00:00+00:00"
        def to_dict(self): return bad_evidence
    store.save_evolution_evidence(Evidence())
    rejected = engine.request_promotion(candidate.version_id, "evidence_worse")
    assert rejected.status is PromotionStatus.REJECTED
    assert engine._active_version().version_id == "v0"


def test_registry_bootstrap_and_lineage_have_single_active_version(tmp_path: Path):
    engine, _, _, candidate, _ = setup_candidate(tmp_path)
    versions = engine.list_versions()
    assert any(version.version_id == "v0" and version.status is VersionStatus.ACTIVE for version in versions)
    assert candidate.parent_version == "v0"
    request = engine.request_promotion(candidate.version_id, "evidence_promotion")
    engine.approve_promotion(request.promotion_id, "Approve candidate")
    engine.promote(request.promotion_id)
    active = engine.list_versions(status=VersionStatus.ACTIVE.value) if hasattr(engine, "list_versions") else [engine._active_version()]
    assert len(active) == 1
