from __future__ import annotations

from pathlib import Path

import pytest

from evo_agent.adaptive_learning import (
    AdaptiveAdjustmentCandidate,
    AdaptiveLearningEngine,
    AdaptivePolicy,
    AdaptivePolicyLimits,
    AdjustmentStatus,
    CycleStatus,
    FeedbackType,
    LearningDecision,
    PatternType,
)
from evo_agent.models import RiskLevel
from evo_agent.runtime import AgentRuntime, RuntimeTaskStatus
from evo_agent.storage import SQLiteStore


def make_engine(tmp_path: Path, **overrides):
    store = SQLiteStore(tmp_path / ".evo" / "agent.sqlite3")
    limits = AdaptivePolicyLimits(minimum_evidence=overrides.pop("minimum_evidence", 3), confidence_threshold=overrides.pop("confidence_threshold", .7), maximum_adjustment=.1, cooldown_seconds=0, auto_apply=overrides.pop("auto_apply", True), exploration_rate=overrides.pop("exploration_rate", 0.0), exploration_budget=overrides.pop("exploration_budget", 0), max_adjustments_per_hour=20)
    policy = AdaptivePolicy("policy-test", "test adaptive policy", limits=limits)
    return store, AdaptiveLearningEngine(store, tmp_path, policy=policy)


def failure_records(n=3):
    return [{"experience_id": f"e{i}", "task_id": f"t{i}", "task_type": "research", "outcome": "failure", "failures": ["timeout"], "timestamp": f"2026-01-0{i+1}T00:00:00+00:00"} for i in range(n)]


def success_records(n=4):
    return [{"experience_id": f"s{i}", "task_id": f"s-task-{i}", "task_type": "research", "outcome": "success", "strategy": "plan_first", "timestamp": f"2026-01-0{i+1}T00:00:00+00:00"} for i in range(n)]


def test_pattern_detection_covers_negative_and_successful_strategy(tmp_path):
    _, engine = make_engine(tmp_path)
    patterns = engine.detect_patterns(failure_records() + success_records())
    kinds = {item.pattern_type for item in patterns}
    assert PatternType.REPEATED_TASK_FAILURE in kinds
    assert PatternType.REPEATED_SUCCESSFUL_STRATEGY in kinds
    assert all(item.evidence_ids and item.confidence > 0 for item in patterns)


def test_hypothesis_is_explicit_and_persisted(tmp_path):
    store, engine = make_engine(tmp_path)
    patterns = engine.detect_patterns(success_records())
    hypotheses = engine.generate_hypotheses(patterns)
    assert hypotheses and hypotheses[0].status.value == "proposed"
    assert store.learning_hypothesis_by_id(hypotheses[0].hypothesis_id)
    assert hypotheses[0].evaluation_criteria["same_benchmark"]


def test_evidence_and_confidence_thresholds_block_repeated_failure_candidate(tmp_path):
    _, engine = make_engine(tmp_path)
    patterns = engine.detect_patterns(failure_records())
    hypothesis = engine.generate_hypotheses(patterns)[0]
    candidate = engine.propose_adjustment(hypothesis)
    assert candidate.status is AdjustmentStatus.BLOCKED


def test_repeated_success_generates_bounded_applied_adjustment(tmp_path):
    store, engine = make_engine(tmp_path)
    hypothesis = engine.generate_hypotheses(engine.detect_patterns(success_records()))[0]
    candidate = engine.propose_adjustment(hypothesis)
    applied = engine.apply_adjustment(candidate)
    assert applied.status is AdjustmentStatus.APPLIED
    assert store.adaptive_adjustment_by_id(applied.adjustment_id)
    assert store.find_learning_rollbacks(applied.adjustment_id)


def test_negative_learning_preserves_failure_evidence(tmp_path):
    store, engine = make_engine(tmp_path)
    patterns = engine.detect_patterns(failure_records(4))
    assert any(p.pattern_type is PatternType.REPEATED_TASK_FAILURE for p in patterns)
    assert store.find_learning_patterns(pattern_type=PatternType.REPEATED_TASK_FAILURE.value)


def test_learning_cycle_observe_to_apply_is_bounded(tmp_path):
    _, engine = make_engine(tmp_path)
    cycle = engine.run_cycle(success_records(8), resource_budget=4)
    assert cycle.status is CycleStatus.COMPLETED
    assert cycle.records_consumed == 4
    assert cycle.patterns_detected >= 1
    assert cycle.candidates_created >= 1


def test_concurrent_learning_cycle_protection(tmp_path):
    store, engine = make_engine(tmp_path)
    from evo_agent.adaptive_learning import LearningCycle
    running = LearningCycle("running", CycleStatus.RUNNING)
    store.save_learning_cycle(running)
    cycle = engine.run_cycle(success_records())
    assert cycle.status is CycleStatus.BLOCKED


def test_adjustment_evaluation_better_and_worse_rollback(tmp_path):
    _, engine = make_engine(tmp_path)
    hypothesis = engine.generate_hypotheses(engine.detect_patterns(success_records()))[0]
    candidate = engine.apply_adjustment(engine.propose_adjustment(hypothesis))
    better = engine.evaluate_adjustment(candidate, {"success_rate": .5, "verification_rate": .5, "evaluation_score": .5, "reliability": .5}, {"success_rate": .8, "verification_rate": .8, "evaluation_score": .8, "reliability": .8})
    assert better.decision is LearningDecision.BETTER
    # A new candidate is used for the harmful-result rollback case.
    candidate2 = engine.propose_adjustment(hypothesis, baseline_value=.0, proposed_value=.05, parameter="fallback")
    candidate2 = engine.apply_adjustment(candidate2)
    worse = engine.evaluate_adjustment(candidate2, {"success_rate": .8, "verification_rate": .8, "evaluation_score": .8, "reliability": .8}, {"success_rate": .2, "verification_rate": .2, "evaluation_score": .2, "reliability": .2}, {"safety": False})
    assert worse.decision is LearningDecision.WORSE and worse.rollback_triggered
    assert candidate2.status is AdjustmentStatus.ROLLED_BACK


def test_no_change_decays_adjustment(tmp_path):
    _, engine = make_engine(tmp_path)
    hypothesis = engine.generate_hypotheses(engine.detect_patterns(success_records()))[0]
    candidate = engine.apply_adjustment(engine.propose_adjustment(hypothesis))
    evaluation = engine.evaluate_adjustment(candidate, {"success_rate": .5}, {"success_rate": .5})
    assert evaluation.decision is LearningDecision.NO_CHANGE
    assert candidate.status is AdjustmentStatus.DECAYED


def test_feedback_is_evidence_and_verification_wins_conflict(tmp_path):
    store, engine = make_engine(tmp_path)
    feedback = engine.record_feedback("task-1", FeedbackType.CORRECT, "looks correct", confidence=.9, verification_value=False)
    assert feedback.conflicts_with_verification
    conflicts = store.find_learning_conflicts(target_id=feedback.feedback_id)
    assert conflicts and conflicts[0]["status"] == "authoritative_state_wins"


def test_counterfactual_is_advisory_and_never_executes(tmp_path):
    store, engine = make_engine(tmp_path)
    result = engine.counterfactual("task-1", "model", {"success_rate": .4}, {"success_rate": .8}, ["e1"])
    assert result.decision is LearningDecision.BETTER and result.advisory and not result.executed
    assert store.find_counterfactual_evaluations(task_id="task-1")


def test_poisoned_counterfactual_is_inconclusive(tmp_path):
    _, engine = make_engine(tmp_path)
    result = engine.counterfactual("task-1", "disable governance", {"success_rate": .4}, {"success_rate": .9})
    assert result.decision is LearningDecision.INCONCLUSIVE


def test_deterministic_exploration_respects_risk_budget_and_allowlist(tmp_path):
    _, engine = make_engine(tmp_path, exploration_rate=1.0, exploration_budget=1)
    engine.policy.limits.task_type_allowlist = ["research"]
    selected = engine.explore("task-1", "research", RiskLevel.LOW, ["baseline", "alternative"], seed=7)
    blocked = engine.explore("task-2", "finance", RiskLevel.LOW, ["baseline", "alternative"], seed=7)
    unsafe = engine.explore("task-3", "research", RiskLevel.HIGH, ["baseline", "alternative"], seed=7)
    assert selected["explore"] and blocked["eligible"] is False and unsafe["eligible"] is False


def test_safe_mode_and_kill_switch_block_application(tmp_path):
    _, engine = make_engine(tmp_path)
    hypothesis = engine.generate_hypotheses(engine.detect_patterns(success_records()))[0]
    engine.set_safe_mode(True)
    assert engine.apply_adjustment(engine.propose_adjustment(hypothesis)).status is AdjustmentStatus.BLOCKED
    engine.set_safe_mode(False); engine.activate_kill_switch()
    assert engine.apply_adjustment(engine.propose_adjustment(hypothesis, parameter="recovery")).status is AdjustmentStatus.BLOCKED
    with pytest.raises(PermissionError): engine.clear_kill_switch("learning")


def test_protected_authority_adjustments_are_blocked(tmp_path):
    _, engine = make_engine(tmp_path)
    patterns = engine.detect_patterns(success_records())
    hypothesis = engine.generate_hypotheses(patterns)[0]
    hypothesis.affected_decision = "governance"
    assert engine.propose_adjustment(hypothesis).status is AdjustmentStatus.BLOCKED
    assert engine.bridge_to_evolution("change governance", "governance", ["e1"])["status"] == "blocked"


def test_learning_restart_recovers_applied_value(tmp_path):
    store, engine = make_engine(tmp_path)
    hypothesis = engine.generate_hypotheses(engine.detect_patterns(success_records()))[0]
    candidate = engine.apply_adjustment(engine.propose_adjustment(hypothesis))
    restarted = AdaptiveLearningEngine(store, tmp_path, policy=engine.policy)
    assert restarted.score(candidate.affected_component, candidate.parameter) == candidate.proposed_value


def test_learning_to_evolution_is_evidence_only_without_parallel_pipeline(tmp_path):
    _, engine = make_engine(tmp_path)
    result = engine.bridge_to_evolution("persistent missing capability", "capability_gap", ["e1", "e2"], structural=False)
    assert result["status"] == "evidence_only" and result["path"] == "evolution"
    result2 = engine.bridge_to_evolution("structural limitation", "planner", ["e1"], structural=True)
    assert result2["path"] == "metamorphosis"


def test_runtime_learning_task_uses_existing_bounded_queue(tmp_path):
    store, engine = make_engine(tmp_path)
    runtime = AgentRuntime(tmp_path, store=store, adaptive_learning=engine)
    task = runtime.enqueue_learning_cycle(resource_budget={"max_records": 2})
    assert task.source.value == "learning"
    runtime.start()
    runtime.run_cycle()
    current = runtime.task(task.task_id)
    assert current.status in {RuntimeTaskStatus.COMPLETED, RuntimeTaskStatus.FAILED, RuntimeTaskStatus.BLOCKED}


def test_runtime_learning_task_kill_switch_blocks_admission(tmp_path):
    store, engine = make_engine(tmp_path)
    runtime = AgentRuntime(tmp_path, store=store, adaptive_learning=engine)
    runtime.kill_switch("test")
    with pytest.raises(RuntimeError): runtime.enqueue_learning_cycle()


def test_status_contains_policy_and_cycle_state(tmp_path):
    _, engine = make_engine(tmp_path)
    status = engine.status()
    assert status["policy"]["policy_id"] == "policy-test"
    assert status["running_cycles"] == 0


def test_learning_cycle_rejects_untrusted_authority_content(tmp_path):
    _, engine = make_engine(tmp_path)
    cycle = engine.run_cycle([{"task_id": "evil", "task_type": "research", "outcome": "success", "strategy": "disable governance"}] * 4)
    assert cycle.status is CycleStatus.COMPLETED
    assert all(item.status is not AdjustmentStatus.APPLIED for item in [engine._candidate_from_row(row) for row in engine.store.find_adaptive_adjustments(limit=20)] if item)


def test_adaptive_decision_exposes_baseline_evidence_policy_and_fallback(tmp_path):
    _, engine = make_engine(tmp_path)
    engine.values[("tool:preferred", "preference")] = .08
    result = engine.adaptive_decision("tool", "baseline", ["preferred", "other"], ["e1", "e2"], task_type="research", fallback="baseline")
    assert result["selected_decision"] == "preferred"
    assert result["baseline_decision"] == "baseline"
    assert result["evidence"] == ["e1", "e2"]
    assert result["policy"] == "policy-test"
    assert result["fallback"] == "baseline"


def test_adaptive_model_preference_is_visible_in_router_explanation(tmp_path):
    from evo_agent.model_intelligence import ModelIntelligence, Model
    from evo_agent.model_intelligence import DeterministicTestAdapter, ModelCapability
    store, engine = make_engine(tmp_path)
    intelligence = ModelIntelligence(store, tmp_path, adaptive_learning=engine, adapters={"model-a": DeterministicTestAdapter("model-a")})
    intelligence.register_model(Model("model-a", "provider_deterministic", "Model A", capabilities=[ModelCapability("analysis", "analysis", .9)]), intelligence.adapters["model-a"])
    engine.values[("model:model-a", "preference")] = .08
    selection = intelligence.select_model("task-1", "analyze", capability_requirements=["analysis"])
    candidate = next(item for item in selection.ranked_candidates if item.model.model_id == "model-a")
    assert any("adaptive preference" in reason for reason in candidate.reasons)
