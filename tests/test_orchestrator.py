from __future__ import annotations

from pathlib import Path
import json
import threading
import pytest

from evo_agent.benchmark import BenchmarkEngine, EvolutionEvidence
from evo_agent.experience import Experience
from evo_agent.models import ApprovalType, ComparisonClass, OutcomeType, OrchestrationPath, OpportunityStatus, WorkItemState
from evo_agent.promotion import PromotionEngine
from evo_agent.orchestrator import (
    ChangeClassifier,
    EvolutionOpportunity,
    EvolutionOrchestrator,
    OrchestrationPolicy,
    OpportunityDetector,
)
from evo_agent.storage import SQLiteStore


def make_experience(index: int, outcome: OutcomeType = OutcomeType.FAILURE, strategy: str = "direct", goal: str = "list files") -> Experience:
    return Experience(
        experience_id=f"exp-{index}", task_id=f"task-{index}", original_goal=goal, task_type="workspace_inspection", task_complexity="simple", selected_strategy=strategy,
        selected_tools=["workspace_list"], execution_steps=[], observations=[], failures=[{"tool": "workspace_list", "error": "failed"}] if outcome is not OutcomeType.SUCCESS else [], recovery_attempts=[], strategy_changes=[], verification_result={"success": outcome is OutcomeType.SUCCESS}, final_outcome=outcome, duration_ms=10, resource_information={}, approval_events=[], timestamp=f"2026-01-01T00:00:{index:02d}+00:00", agent_version="0.4.0", model_identifier="test", evaluation_id=f"eval-{index}", evaluation_result={"success_score": 20 if outcome is not OutcomeType.SUCCESS else 90},
    )


def setup_orchestrator(tmp_path: Path, policy: OrchestrationPolicy | None = None) -> tuple[EvolutionOrchestrator, SQLiteStore, Path]:
    source = tmp_path / "source"
    (source / "evo_agent").mkdir(parents=True)
    for name in ("kernel.py", "security.py", "verifier.py", "storage.py", "sandbox.py"):
        (source / "evo_agent" / name).write_text("# phase 9 fixture\n", encoding="utf-8")
    store = SQLiteStore(tmp_path / "workspace" / ".evo" / "agent.sqlite3")
    return EvolutionOrchestrator(store, source, policy=policy or OrchestrationPolicy(cooldown_seconds=60)), store, source


def test_opportunity_detector_repeated_failure_and_success_control(tmp_path: Path):
    detector = OpportunityDetector(OrchestrationPolicy(flexibility_repeat_threshold=2, evolution_repeat_threshold=3))
    failures = detector.detect([make_experience(1), make_experience(2)])
    assert any(item.recommended_change_type is OrchestrationPath.FLEXIBILITY for item in failures)
    assert failures[0].status is OpportunityStatus.DETECTED
    assert detector.detect([make_experience(1, OutcomeType.SUCCESS), make_experience(2, OutcomeType.SUCCESS)]) == []
    escalated = detector.detect([make_experience(1), make_experience(2), make_experience(3)])
    assert escalated[0].recommended_change_type is OrchestrationPath.EVOLUTION


def test_detector_persists_and_deduplicates_by_evidence_fingerprint(tmp_path: Path):
    orchestrator, store, _ = setup_orchestrator(tmp_path)
    opportunity = orchestrator.detector.detect([make_experience(1), make_experience(2)])[0]
    item = orchestrator.create_work_item(opportunity)
    assert item is not None
    assert store.opportunity_by_id(opportunity.opportunity_id)
    assert store.work_item_by_id(item.work_item_id)
    same = orchestrator.create_work_item(opportunity)
    assert same.work_item_id == item.work_item_id
    assert len(orchestrator.list_work_items()) == 1


def test_classifier_prefers_smallest_effective_change_and_is_inconclusive_under_uncertainty():
    classifier = ChangeClassifier()
    flexibility = EvolutionOpportunity("o1", [], [], "recurring recovery", 2, "medium", ["general"], ["flexibility"], ["recovery"], "moderate", OrchestrationPath.FLEXIBILITY, 0.7)
    evolution = EvolutionOpportunity("o2", [], [], "known strategy consistently performs poorly", 3, "high", ["general"], ["planning"], ["planning"], "strong", OrchestrationPath.EVOLUTION, 0.8)
    structural = EvolutionOpportunity("o3", [], [], "structural limitation and missing capability", 3, "high", ["general"], ["architecture"], ["capability_composition"], "strong", OrchestrationPath.METAMORPHOSIS, 0.8)
    uncertain = EvolutionOpportunity("o4", [], [], "ambiguous issue", 1, "low", [], [], [], "weak", OrchestrationPath.NO_CHANGE, 0.2)
    protected = EvolutionOpportunity("o5", [], [], "change rollback authority", 5, "critical", [], ["rollback"], [], "strong", OrchestrationPath.METAMORPHOSIS, 0.9)
    assert classifier.classify(flexibility).path is OrchestrationPath.FLEXIBILITY
    assert classifier.classify(evolution).path is OrchestrationPath.EVOLUTION
    assert classifier.classify(structural).path is OrchestrationPath.METAMORPHOSIS
    assert classifier.classify(uncertain).path is OrchestrationPath.INCONCLUSIVE
    result = classifier.classify(protected)
    assert result.protected and result.path is OrchestrationPath.NO_CHANGE


def test_successful_alternative_and_failed_evolution_escalate_from_evidence():
    detector = OpportunityDetector(OrchestrationPolicy(flexibility_repeat_threshold=2, evolution_repeat_threshold=3))
    records = [make_experience(1, OutcomeType.SUCCESS, "robust"), make_experience(2, OutcomeType.SUCCESS, "robust"), make_experience(3, OutcomeType.FAILURE, "direct"), make_experience(4, OutcomeType.FAILURE, "direct")]
    opportunities = detector.detect(records)
    assert any(item.metadata.get("successful_alternative") for item in opportunities)
    escalated = detector.detect([], evolution_records=[{"status": "failed"}, {"status": "failed"}, {"status": "failed"}])
    assert any(item.recommended_change_type is OrchestrationPath.METAMORPHOSIS and item.metadata.get("escalated_from_evolution") for item in escalated)


def test_no_change_and_capability_regression_paths():
    classifier = ChangeClassifier()
    no_change = EvolutionOpportunity("no-change", [], [], "stable behavior", 1, "low", [], [], [], "moderate", OrchestrationPath.NO_CHANGE, 0.8)
    assert classifier.classify(no_change).path is OrchestrationPath.NO_CHANGE
    detector = OpportunityDetector()
    regressed = make_experience(9)
    regressed.evaluation_result = {"success_score": 10, "capability_regression": True}
    opportunities = detector.detect([regressed])
    assert any(item.recommended_change_type is OrchestrationPath.METAMORPHOSIS for item in opportunities)


def test_state_machine_accepts_valid_path_and_rejects_direct_activation(tmp_path: Path):
    orchestrator, _, _ = setup_orchestrator(tmp_path)
    opportunity = orchestrator.detector.detect([make_experience(1), make_experience(2)])[0]
    item = orchestrator.create_work_item(opportunity)
    assert item is not None
    assert orchestrator.transition(item.work_item_id, WorkItemState.ANALYZING).current_state is WorkItemState.ANALYZING
    with pytest.raises(ValueError):
        orchestrator.transition(item.work_item_id, WorkItemState.COMPLETED)
    with pytest.raises(ValueError):
        orchestrator.transition(item.work_item_id, WorkItemState.BETTER)


def test_protected_core_is_ignored_without_rerouting(tmp_path: Path):
    orchestrator, store, _ = setup_orchestrator(tmp_path)
    opportunity = EvolutionOpportunity("protected", [], [], "modify governance and disable rollback", 4, "critical", [], ["governance"], [], "strong", OrchestrationPath.METAMORPHOSIS, 1.0)
    item = orchestrator.create_work_item(opportunity)
    assert item is None
    stored = store.opportunity_by_id(opportunity.opportunity_id)
    assert stored and stored["status"] == "ignored"
    assert orchestrator.list_work_items() == []


def test_evolution_path_requires_human_approval_then_uses_existing_sandbox_and_benchmark(tmp_path: Path):
    orchestrator, store, _ = setup_orchestrator(tmp_path)
    experiences = [make_experience(1), make_experience(2), make_experience(3)]
    for experience in experiences:
        orchestrator.ingest_experience(experience)
    opportunity = orchestrator.detector.detect(experiences)[0]
    item = orchestrator.create_work_item(opportunity)
    assert item is not None
    item = orchestrator.route_to_engine(item.work_item_id)
    assert item.current_state is WorkItemState.AWAITING_APPROVAL
    assert orchestrator.list_approval_requests(item.work_item_id)[0].approval_type is ApprovalType.EVOLUTION
    with pytest.raises(PermissionError):
        orchestrator.manage_approval(item.work_item_id, ApprovalType.EVOLUTION, True, "self approve", actor="orchestrator")
    orchestrator.manage_approval(item.work_item_id, ApprovalType.EVOLUTION, True, "Human authorizes sandbox", actor="human")
    item = orchestrator.manage_experiment(item.work_item_id)
    assert item.current_state is WorkItemState.BENCHMARKING
    assert store.experiment_by_id(item.experiment_id)
    item = orchestrator.collect_evidence(item.work_item_id)
    assert item.current_state is WorkItemState.AWAITING_PROMOTION_APPROVAL
    assert item.evidence_id
    evidence = store.evidence_by_id(item.evidence_id)
    assert evidence and evidence["decision"] == "better"


class BetterBenchmark:
    def __init__(self, store: SQLiteStore):
        self.store = store

    def default_benchmark(self):
        return BenchmarkEngine.default_benchmark("orchestrator-better")

    def save_benchmark(self, benchmark):
        self.store.save_benchmark(benchmark)

    def run(self, benchmark_id: str, experiment_id: str):
        experiment = self.store.experiment_by_id(experiment_id)
        payload = json.loads(experiment["payload"])
        evidence = EvolutionEvidence(
            "evidence_orchestrator_better", experiment_id, experiment["proposal_id"], benchmark_id,
            experiment["baseline_version"], experiment["candidate_version"], 1,
            {"success_rate": 0.5, "verification_rate": 1.0, "mean_score": 50},
            {"success_rate": 1.0, "verification_rate": 1.0, "mean_score": 100},
            {"success_rate": 0.5, "mean_score": 50},
            {"functional_regressions": [], "verification_regressions": [], "timeout_regressions": [], "efficiency_regressions": [], "safety_regressions": []},
            {"production_unchanged": True, "candidate_isolated": True, "network_denied": True, "host_secrets_absent": True, "bounded_commands": True, "candidate_safety_ok": True},
            True, ComparisonClass.BETTER, ["Controlled acceptance fixture provides a measurable improvement."], "benchmark-v1", "benchmark-evaluator-v1", "2026-01-01T00:00:00+00:00",
            {"deterministic_seed": 0, "candidate_id": json.loads(experiment["payload"])["candidate_id"], "source_commit": "unknown", "sandbox_policy": {"network": "denied"}},
        )
        self.store.save_evolution_evidence(evidence)
        return evidence


def test_orchestrator_uses_phase7_promotion_and_native_rollback(tmp_path: Path):
    orchestrator, store, source = setup_orchestrator(tmp_path)
    promotion = PromotionEngine(store, source, tmp_path / "versions", health_checker=lambda path: {"healthy": True, "smoke_test": {"success": True}})
    orchestrator.promotion_engine = promotion
    orchestrator.benchmark_engine = BetterBenchmark(store)
    experiences = [make_experience(1), make_experience(2), make_experience(3)]
    for experience in experiences:
        orchestrator.ingest_experience(experience)
    opportunity = orchestrator.detector.detect(experiences)[0]
    item = orchestrator.route_to_engine(orchestrator.create_work_item(opportunity).work_item_id)
    orchestrator.manage_approval(item.work_item_id, ApprovalType.EVOLUTION, True, "authorize sandbox", actor="human")
    item = orchestrator.manage_experiment(item.work_item_id)
    item = orchestrator.collect_evidence(item.work_item_id)
    assert item.current_state is WorkItemState.AWAITING_PROMOTION_APPROVAL, item.to_dict()
    orchestrator.request_promotion(item.work_item_id)
    orchestrator.manage_approval(item.work_item_id, ApprovalType.PROMOTION, True, "authorize activation", actor="human")
    record = orchestrator.promote(item.work_item_id)
    assert record.final_status.value == "active"
    rolled_back = orchestrator.handle_rollback(item.work_item_id, "acceptance rollback")
    assert rolled_back.current_state is WorkItemState.ROLLED_BACK
    assert promotion._active_version().version_id == "v0"


def test_orchestrator_records_native_health_failure_rollback(tmp_path: Path):
    orchestrator, store, source = setup_orchestrator(tmp_path)
    promotion = PromotionEngine(store, source, tmp_path / "versions", health_checker=lambda path: {"healthy": False, "reason": "acceptance health failure", "smoke_test": {"success": False}})
    orchestrator.promotion_engine = promotion
    orchestrator.benchmark_engine = BetterBenchmark(store)
    experiences = [make_experience(1), make_experience(2), make_experience(3)]
    for experience in experiences:
        orchestrator.ingest_experience(experience)
    opportunity = orchestrator.detector.detect(experiences)[0]
    item = orchestrator.route_to_engine(orchestrator.create_work_item(opportunity).work_item_id)
    orchestrator.manage_approval(item.work_item_id, ApprovalType.EVOLUTION, True, "authorize sandbox", actor="human")
    item = orchestrator.collect_evidence(orchestrator.manage_experiment(item.work_item_id).work_item_id)
    orchestrator.request_promotion(item.work_item_id)
    orchestrator.manage_approval(item.work_item_id, ApprovalType.PROMOTION, True, "authorize activation", actor="human")
    record = orchestrator.promote(item.work_item_id)
    assert record.final_status.value == "rolled_back"
    assert orchestrator.get_work_item(item.work_item_id).current_state is WorkItemState.ROLLED_BACK
    assert promotion._active_version().version_id == "v0"


def test_flexibility_path_is_delegated_and_completes_without_proposal(tmp_path: Path):
    called: list[str] = []
    orchestrator, _, _ = setup_orchestrator(tmp_path, OrchestrationPolicy(flexibility_repeat_threshold=2, evolution_repeat_threshold=4))
    orchestrator.flexibility_handler = lambda opportunity: called.append(opportunity.opportunity_id) or {"success": True, "strategy": "recovery"}
    opportunity = orchestrator.detector.detect([make_experience(1), make_experience(2)])[0]
    item = orchestrator.create_work_item(opportunity)
    assert item is not None
    item = orchestrator.route_to_engine(item.work_item_id)
    assert item.current_state is WorkItemState.COMPLETED
    assert called == [opportunity.opportunity_id]
    assert item.proposal_id is None


def test_metamorphosis_path_requires_separate_approval(tmp_path: Path):
    orchestrator, _, _ = setup_orchestrator(tmp_path)
    experience = make_experience(1, goal="the architecture has a structural limitation and missing capability")
    opportunity = orchestrator.detector.detect([experience])[0]
    assert opportunity.recommended_change_type is OrchestrationPath.METAMORPHOSIS
    item = orchestrator.create_work_item(opportunity)
    assert item is not None
    item = orchestrator.route_to_engine(item.work_item_id)
    assert item.current_state is WorkItemState.AWAITING_APPROVAL
    assert orchestrator.list_approval_requests(item.work_item_id)[0].approval_type is ApprovalType.METAMORPHOSIS
    with pytest.raises(PermissionError):
        orchestrator.manage_experiment(item.work_item_id)


def test_metamorphosis_path_runs_structural_sandbox_and_benchmark(tmp_path: Path):
    orchestrator, _, _ = setup_orchestrator(tmp_path)
    experience = make_experience(1, goal="the architecture has a structural limitation and missing capability")
    orchestrator.ingest_experience(experience)
    opportunity = orchestrator.detector.detect([experience])[0]
    item = orchestrator.route_to_engine(orchestrator.create_work_item(opportunity).work_item_id)
    assert item.current_state is WorkItemState.AWAITING_APPROVAL
    orchestrator.manage_approval(item.work_item_id, ApprovalType.METAMORPHOSIS, True, "approve structural test", actor="human")
    item = orchestrator.manage_experiment(item.work_item_id)
    assert item.current_state is WorkItemState.BENCHMARKING
    item = orchestrator.collect_evidence(item.work_item_id)
    assert item.current_state is WorkItemState.AWAITING_PROMOTION_APPROVAL


def test_approval_experiment_and_promotion_queues_are_persistent(tmp_path: Path):
    orchestrator, store, _ = setup_orchestrator(tmp_path)
    experiences = [make_experience(1), make_experience(2), make_experience(3)]
    for experience in experiences:
        orchestrator.ingest_experience(experience)
    item = orchestrator.create_work_item(orchestrator.detector.detect(experiences)[0])
    item = orchestrator.route_to_engine(item.work_item_id)
    orchestrator.manage_approval(item.work_item_id, ApprovalType.EVOLUTION, True, "approve experiment", actor="human")
    item = orchestrator.manage_experiment(item.work_item_id)
    assert store.find_experiment_queue()
    item = orchestrator.collect_evidence(item.work_item_id)
    assert item.current_state is WorkItemState.AWAITING_PROMOTION_APPROVAL
    promotion_request = orchestrator.request_promotion(item.work_item_id)
    assert promotion_request.approval_status.value == "pending"
    assert store.find_promotion_queue() == []
    assert any(request.approval_type is ApprovalType.PROMOTION for request in orchestrator.list_approval_requests(item.work_item_id))
    orchestrator.manage_approval(item.work_item_id, ApprovalType.PROMOTION, True, "approve promotion separately", actor="human")
    assert orchestrator.get_work_item(item.work_item_id).current_state is WorkItemState.PROMOTION_APPROVED
    assert store.find_promotion_queue()


def test_restart_recovery_rehydrates_and_does_not_retry_interrupted_benchmark(tmp_path: Path):
    orchestrator, store, _ = setup_orchestrator(tmp_path)
    experiences = [make_experience(1), make_experience(2), make_experience(3)]
    for experience in experiences:
        orchestrator.ingest_experience(experience)
    opportunity = orchestrator.detector.detect(experiences)[0]
    item = orchestrator.create_work_item(opportunity)
    item = orchestrator.route_to_engine(item.work_item_id)
    orchestrator.manage_approval(item.work_item_id, ApprovalType.EVOLUTION, True, "approve", actor="human")
    item = orchestrator.manage_experiment(item.work_item_id)
    orchestrator.transition(item.work_item_id, WorkItemState.EVALUATING, "simulate persisted interruption")
    restarted = EvolutionOrchestrator(store, orchestrator.source_root, orchestrator.policy)
    recovered = restarted.resume(item.work_item_id)
    assert recovered.current_state is WorkItemState.INCONCLUSIVE
    assert "no automatic retry" in (recovered.last_error or "")


def test_stale_version_is_blocked_before_experiment(tmp_path: Path):
    orchestrator, _, _ = setup_orchestrator(tmp_path)
    experiences = [make_experience(1), make_experience(2), make_experience(3)]
    for experience in experiences:
        orchestrator.ingest_experience(experience)
    opportunity = orchestrator.detector.detect(experiences)[0]
    item = orchestrator.create_work_item(opportunity)
    item = orchestrator.route_to_engine(item.work_item_id)
    orchestrator.manage_approval(item.work_item_id, ApprovalType.EVOLUTION, True, "approve", actor="human")
    item = orchestrator.get_work_item(item.work_item_id)
    item.current_version = "v-stale"
    orchestrator.store.save_work_item(item)
    with pytest.raises(RuntimeError, match="revalidation required"):
        orchestrator.manage_experiment(item.work_item_id)
    assert orchestrator.get_work_item(item.work_item_id).current_state is WorkItemState.BLOCKED


def test_single_cycle_is_bounded_and_records_audit_events(tmp_path: Path):
    orchestrator, store, _ = setup_orchestrator(tmp_path)
    orchestrator.ingest_experience(make_experience(1))
    orchestrator.ingest_experience(make_experience(2))
    result = orchestrator.run_cycle()
    assert result.stopped_reason in {"bounded_cycle_complete", "safety_ceiling_reached"}
    assert result.experiments_started <= orchestrator.policy.max_experiments_per_cycle
    assert store.find_orchestration_events()


def test_concurrent_work_item_creation_is_serialized(tmp_path: Path):
    orchestrator, _, _ = setup_orchestrator(tmp_path)
    opportunity = orchestrator.detector.detect([make_experience(1), make_experience(2)])[0]
    results: list[str] = []
    def create() -> None:
        item = orchestrator.create_work_item(opportunity)
        if item:
            results.append(item.work_item_id)
    threads = [threading.Thread(target=create) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert len(set(results)) == 1
    assert len(orchestrator.list_work_items()) == 1


def test_cooldown_prevents_immediate_reprocessing(tmp_path: Path):
    orchestrator, store, _ = setup_orchestrator(tmp_path, OrchestrationPolicy(cooldown_seconds=3600))
    opportunity = orchestrator.detector.detect([make_experience(1), make_experience(2)])[0]
    item = orchestrator.create_work_item(opportunity)
    assert item is not None
    orchestrator._transition_terminal(item, WorkItemState.INCONCLUSIVE, "insufficient evidence")
    assert store.cooldown_by_key(opportunity.fingerprint)
    assert orchestrator._cooldown_active(opportunity.fingerprint)
