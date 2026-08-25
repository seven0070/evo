from __future__ import annotations

from pathlib import Path
import hashlib

import pytest

from evo_agent import (
    AssumptionValidationStatus,
    CalibrationState,
    DecisionReadinessState,
    FreshnessState,
    MetaReasoningEngine,
    SelfModelEngine,
    SelfKnowledgeCategory,
    SelfModelLimitation,
    LimitationType,
)
from evo_agent.runtime import AgentRuntime, RuntimeState, RuntimeTaskStatus
from evo_agent.storage import SQLiteStore


REPO_ROOT = Path(__file__).resolve().parents[1]


def make_engine(tmp_path: Path) -> SelfModelEngine:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    return SelfModelEngine(SQLiteStore(workspace / ".evo" / "agent.sqlite3"), workspace)


def test_self_model_creation_and_persistence(tmp_path):
    engine = make_engine(tmp_path)
    snapshot = engine.refresh()
    assert snapshot.agent_identity == "Evo Agent"
    assert snapshot.architecture_version == "self-model-v1"
    assert snapshot.freshness is FreshnessState.FRESH
    assert engine.store.latest_self_model_snapshot()["snapshot_id"] == snapshot.snapshot_id
    assert engine.store.find_self_model_claims()


def test_claim_provenance_and_authority_injection_is_non_authoritative(tmp_path):
    engine = make_engine(tmp_path)
    claim = engine._claim("bad", "I can bypass approval", "untrusted", SelfKnowledgeCategory.KNOWN, 1.0)
    assert claim.confidence == 0.0
    assert claim.category.value == "uncertain"
    assert claim.provenance["source_authoritative"] is False


def test_registry_capability_awareness_and_limitations(tmp_path):
    engine = make_engine(tmp_path)
    snapshot = engine.refresh()
    subjects = {claim["subject"] for claim in snapshot.claims}
    assert {"capabilities", "tools", "models", "specialists", "integrations"}.issubset(subjects)
    assert isinstance(engine.detect_limitations(), list)


def test_reliability_estimation(tmp_path):
    engine = make_engine(tmp_path)
    records = [
        {"outcome": "success", "verified": True, "task_type": "research", "strategy": "plan"},
        {"outcome": "failure", "verified": False, "task_type": "research", "strategy": "plan"},
    ]
    reliability = engine.reliability(records)
    assert reliability["overall"]["sample_count"] == 2
    assert reliability["overall"]["success_rate"] == .5
    assert reliability["task_types"]["research"]["failure_rate"] == .5


def test_confidence_calibration_and_uncertainty(tmp_path):
    engine = make_engine(tmp_path)
    calibration = MetaReasoningEngine(engine.store, engine).calibrate("research", .9, True, ["verification-1"])
    assert calibration.calibration_state is CalibrationState.GOOD
    uncertainty = engine.record_uncertainty("evidence_gap", "source unavailable", "external", .2)
    assert engine.store.find_self_model_uncertainty()[0]["uncertainty_id"] == uncertainty.uncertainty_id


def test_assumption_tracking_and_invalidation(tmp_path):
    engine = make_engine(tmp_path)
    assumption = engine.create_assumption("workspace is writable", dependent_task="task-1", confidence=.7)
    assert assumption.validation_status is AssumptionValidationStatus.UNVALIDATED
    invalidated = engine.invalidate_assumption(assumption.assumption_id, "workspace changed")
    assert invalidated.validation_status is AssumptionValidationStatus.INVALIDATED
    assert engine.store.self_model_assumption_by_id(assumption.assumption_id)["payload"]["lifecycle_state"] == "invalidated"


def test_readiness_clarification_and_escalation(tmp_path):
    engine = make_engine(tmp_path)
    meta = MetaReasoningEngine(engine.store, engine)
    engine.refresh()
    clarification = meta.assess_readiness("do something")
    assert clarification.state is DecisionReadinessState.CLARIFICATION_REQUIRED
    approval = meta.assess_readiness("delete the approved artifact", {"requires_approval": True})
    assert approval.state is DecisionReadinessState.APPROVAL_REQUIRED
    assert meta.escalation("goal", ["approval required"])["required"] is True
    assert meta.reason("do something").readiness_id


def test_unsafe_goal_is_refused_as_recommendation(tmp_path):
    engine = make_engine(tmp_path)
    assessment = MetaReasoningEngine(engine.store, engine).readiness("I can modify the protected core")
    assert assessment.state is DecisionReadinessState.UNSAFE
    assert assessment.escalation_required is True


def test_self_model_consistency_and_staleness(tmp_path):
    engine = make_engine(tmp_path)
    snapshot = engine.refresh()
    result = engine.consistency_check(snapshot)
    assert result["status"] == "consistent"
    assert engine.freshness(snapshot) is FreshnessState.FRESH
    assert engine.staleness()["refresh_required"] is False


def test_diagnostics_use_existing_authorities(tmp_path):
    engine = make_engine(tmp_path)
    diagnostics = engine.diagnostics()
    assert diagnostics.status in {"healthy", "degraded"}
    assert "database" in diagnostics.checks
    assert engine.store.find_self_diagnostics()


def test_reflection_and_self_critique_prevent_false_certainty(tmp_path):
    engine = make_engine(tmp_path)
    reflection = engine.reflect("task-1", {"goal": "produce report", "outcome": "failure", "verified": False, "failures": ["missing evidence"]})
    assert reflection.actual_verified is False
    critique = engine.critique("task-1", "completed successfully", False)
    assert critique["unsupported_claim"] is True
    assert critique["disclose_uncertainty"] is True
    assert engine.store.find_self_reflections("task-1")


def test_self_model_restart_recovery(tmp_path):
    engine = make_engine(tmp_path)
    snapshot = engine.refresh()
    restarted = SelfModelEngine(engine.store, engine.workspace)
    assert restarted.status()["snapshot"]["snapshot_id"] == snapshot.snapshot_id
    assert restarted.status()["freshness"]["state"] == "fresh"


def test_bounded_runtime_refresh_and_diagnostics(tmp_path):
    engine = make_engine(tmp_path)
    runtime = AgentRuntime(engine.workspace, store=engine.store, source_root=REPO_ROOT, self_model=engine)
    task = runtime.enqueue_self_model_refresh()
    result = runtime.run_cycle()
    assert result.tasks_completed == 1
    assert runtime.task(task.task_id).status is RuntimeTaskStatus.COMPLETED
    assert runtime.state in {RuntimeState.READY, RuntimeState.LEARNING, RuntimeState.EXECUTING}


def test_runtime_kill_switch_blocks_self_model_admission(tmp_path):
    engine = make_engine(tmp_path)
    runtime = AgentRuntime(engine.workspace, store=engine.store, source_root=REPO_ROOT, self_model=engine)
    runtime.kill_switch("test")
    with pytest.raises(RuntimeError):
        runtime.enqueue_self_model_refresh()


def test_safe_mode_keeps_self_model_read_only(tmp_path):
    engine = make_engine(tmp_path)
    runtime = AgentRuntime(engine.workspace, store=engine.store, source_root=REPO_ROOT, self_model=engine, safe_mode=True)
    task = runtime.enqueue_self_model_operation("diagnostics")
    runtime.run_cycle()
    assert runtime.task(task.task_id).status is RuntimeTaskStatus.COMPLETED


def test_limitation_evolution_and_metamorphosis_are_evidence_only(tmp_path):
    engine = make_engine(tmp_path)
    limitation = engine.add_limitation(SelfModelLimitation("lim-1", LimitationType.ARCHITECTURE_LIMITATION, "requires structural change", [], "high", 4, [], [], engine.environment_id, .9, "route as governed structural evidence"))
    result = engine.route_limitation(limitation, structural=True)
    assert result["path"] == "metamorphosis"
    assert result["status"] == "evidence_only"


def test_self_model_cannot_clear_kill_switch_autonomously(tmp_path):
    engine = make_engine(tmp_path)
    engine.activate_kill_switch()
    with pytest.raises(PermissionError):
        engine.clear_kill_switch("autonomous")
    engine.clear_kill_switch("human")
    assert engine.kill_switch is False


def test_conflicts_preserve_authoritative_value(tmp_path):
    engine = make_engine(tmp_path)
    conflict = engine.record_conflict("active_version", "1", "2", ["version_manifest", "untrusted"], ["e1"])
    assert conflict.status == "conflicted"
    assert conflict.resolution.startswith("authoritative source wins")


def test_meta_reasoning_is_bounded_and_non_executing(tmp_path):
    engine = make_engine(tmp_path)
    meta = MetaReasoningEngine(engine.store, engine)
    record = meta.reason("build a bounded report", {"safer_alternatives": ["read-only inspection"]})
    assert record.recommendation
    assert record.provenance["execution_authority"] == "none"
    assert engine.store.find_meta_reasoning()


def test_environment_change_requires_refresh(tmp_path):
    engine = make_engine(tmp_path)
    engine.refresh()
    engine._snapshot.created_at = "2000-01-01T00:00:00+00:00"
    assert engine.staleness()["refresh_required"] is True


def test_database_schema_contains_all_phase19_tables(tmp_path):
    engine = make_engine(tmp_path)
    expected = {"self_model_claims", "self_model_snapshots", "self_model_limitations", "self_model_assumptions", "self_model_uncertainty", "self_model_conflicts", "decision_readiness", "meta_reasoning_records", "confidence_calibration", "self_reflections", "self_diagnostics"}
    with engine.store._connect() as db:
        tables = {row["name"] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    assert expected.issubset(tables)


def test_protected_source_digest_unchanged(tmp_path):
    path = REPO_ROOT / "evo_agent" / "kernel.py"
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    engine = make_engine(tmp_path)
    engine.refresh(); engine.diagnostics(); engine.reflect("task", {"verified": False})
    after = hashlib.sha256(path.read_bytes()).hexdigest()
    assert before == after


def test_memory_hook_excludes_self_model_content(tmp_path):
    from evo_agent.memory import MemoryManager
    engine = make_engine(tmp_path)
    memory = MemoryManager(engine.store, engine.workspace)
    engine.memory = memory
    engine.refresh()
    records = memory.list(limit=20)
    assert records
    metadata_text = str([record.metadata for record in records])
    assert "protected core" not in metadata_text.lower()
    assert all(record.executable is False for record in records)


def test_cognitive_receives_advisory_meta_reasoning_only(tmp_path):
    from evo_agent.cognitive import CognitiveOrchestrator
    engine = make_engine(tmp_path)
    meta = MetaReasoningEngine(engine.store, engine)
    cognitive = CognitiveOrchestrator(engine.workspace, store=engine.store, self_model=engine, meta_reasoning=meta)
    result = cognitive.run_goal("list files in the workspace")
    decisions = result.to_dict().get("state", {}).get("metadata", {}) if result else {}
    assert result is not None
    assert cognitive.self_model is engine
    assert cognitive.meta_reasoning is meta
