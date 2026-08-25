from pathlib import Path
import time

import pytest

from evo_agent import (
    AgentRuntime,
    CognitiveOrchestrator,
    DeterministicTestAdapter,
    InferenceRequest,
    InferenceStatus,
    LearningAdjustmentStatus,
    LearningEngine,
    LearningObservation,
    LearningPolicy,
    Model,
    ModelBenchmark,
    ModelCapability,
    ModelContextManager,
    ModelEvaluationEngine,
    ModelHealthState,
    ModelIntelligence,
    ModelPolicy,
    ModelProvider,
    ModelRegistry,
    ModelRouter,
    ModelComparisonDecision,
    ProviderLifecycle,
    ProviderType,
)
from evo_agent.memory import MemoryManager
from evo_agent.specialist import Specialist, SpecialistCapability, SpecialistType
from evo_agent.storage import SQLiteStore


def make_engine(tmp_path: Path, *, policy=None):
    workspace = tmp_path / "workspace"
    store = SQLiteStore(workspace / ".evo" / "agent.sqlite3")
    adapter = DeterministicTestAdapter("model-a", responses={"default": {"answer": "ok"}})
    intelligence = ModelIntelligence(store, workspace, adapters={"model-a": adapter}, policy=policy)
    model = Model("model-a", "provider_deterministic", "Model A", capabilities=[ModelCapability("analysis", "analysis", .9), ModelCapability("coding", "coding", .8)], structured_output_support=True, tool_use_support=True)
    intelligence.register_model(model, adapter)
    return store, workspace, intelligence, adapter


def test_provider_and_model_registration_credential_isolation(tmp_path: Path):
    store = SQLiteStore(tmp_path / "w" / ".evo" / "agent.sqlite3")
    registry = ModelRegistry(store, tmp_path / "w", seed_defaults=False)
    provider = ModelProvider("p", "Local Provider", ProviderType.LOCAL, credential_reference="env:LOCAL_TOKEN")
    registry.register_provider(provider)
    model = Model("m", "p", "Safe Model", metadata={"credential_reference": "env:LOCAL_TOKEN"})
    registry.register(model)
    assert registry.get("m").metadata["credential_reference"] == "env:LOCAL_TOKEN"
    assert "secret-value" not in str(registry.get("m").to_dict())
    with pytest.raises(ValueError):
        registry.register_provider(ModelProvider("bad", "Bad", ProviderType.LOCAL, metadata={"api_key": "secret-value"}))


def test_malformed_model_and_provider_self_mutation_rejected(tmp_path: Path):
    store = SQLiteStore(tmp_path / "w" / ".evo" / "agent.sqlite3")
    registry = ModelRegistry(store, tmp_path / "w", seed_defaults=False)
    with pytest.raises(ValueError): registry.register(Model("", "missing", ""))
    with pytest.raises(PermissionError): registry.register_provider(ModelProvider("p", "P", ProviderType.LOCAL), actor="provider")


def test_capability_matching_and_deterministic_selection(tmp_path: Path):
    _, _, intelligence, _ = make_engine(tmp_path)
    selected = intelligence.select_model("task", "analyze", capability_requirements=["analysis"])
    selected_again = intelligence.select_model("task", "analyze", capability_requirements=["analysis"])
    assert selected.selected_model_id == "model-a"
    assert selected.to_dict()["ranked_candidates"] == selected_again.to_dict()["ranked_candidates"]


def test_health_degradation_and_circuit_breaker(tmp_path: Path):
    _, _, intelligence, _ = make_engine(tmp_path)
    for _ in range(3): intelligence.registry.record_outcome("model-a", False, error="provider down")
    assert intelligence.registry.get("model-a").health.state is ModelHealthState.UNAVAILABLE
    assert intelligence.router.route("task", "analyze", capability_requirements=["analysis"], available_models=[intelligence.registry.get("model-a")]).selected_model_id is None


def test_request_validation_and_structured_output(tmp_path: Path):
    _, _, intelligence, _ = make_engine(tmp_path)
    intelligence.adapters["model-a"] = DeterministicTestAdapter("model-a", responses={"t": {"wrong": "x"}, "default": {"answer": "ok"}})
    bad = InferenceRequest("model-a", "provider_deterministic", "t", "analysis", "text", "x", {"type": "object", "required": ["answer"]}, structured_output=True)
    response = intelligence.infer(bad)
    assert response.status is InferenceStatus.INVALID
    good = InferenceRequest("model-a", "provider_deterministic", "t2", "analysis", "text", {"answer": "ok"}, {"type": "object", "required": ["answer"]}, structured_output=True)
    assert intelligence.infer(good).success


def test_tool_call_validation(tmp_path: Path):
    _, _, intelligence, _ = make_engine(tmp_path)
    valid_adapter = DeterministicTestAdapter("model-a", responses={"tool-ok": {"tool_calls": [{"name": "read_workspace", "arguments": {"path": "."}}]}})
    request = InferenceRequest("model-a", "provider_deterministic", "tool-ok", "analysis", "text", "input", tool_schema=[{"name": "read_workspace", "arguments": {"type": "object"}}])
    response = intelligence.infer(request, valid_adapter, fallback=False)
    assert response.success
    invalid_adapter = DeterministicTestAdapter("model-a", responses={"tool-bad": {"tool_calls": [{"name": "delete_everything", "arguments": {}}]}})
    bad = intelligence.infer(InferenceRequest("model-a", "provider_deterministic", "tool-bad", "analysis", "text", "input", tool_schema=[{"name": "read_workspace"}]), invalid_adapter, fallback=False)
    assert bad.status is InferenceStatus.INVALID


def test_provider_failure_bounded_fallback(tmp_path: Path):
    store, workspace, intelligence, _ = make_engine(tmp_path)
    adapter_b = DeterministicTestAdapter("model-b", responses={"default": {"answer": "fallback"}})
    intelligence.register_provider(ModelProvider("provider_b", "Provider B", ProviderType.DETERMINISTIC_TEST, lifecycle_state=ProviderLifecycle.ACTIVE))
    intelligence.register_model(Model("model-b", "provider_b", "Model B", capabilities=[ModelCapability("analysis", "analysis", .8)]), adapter_b)
    intelligence.adapters["model-a"] = DeterministicTestAdapter("model-a", fail=True)
    request = InferenceRequest("model-a", "provider_deterministic", "fallback-task", "analysis", "text", "x")
    response = intelligence.infer(request)
    assert response.model_id == "model-b" and response.success
    assert store.count_events("model_fallback_selected") == 1


def test_timeout_is_not_unbounded(tmp_path: Path):
    _, _, intelligence, _ = make_engine(tmp_path)
    intelligence.adapters["model-a"] = DeterministicTestAdapter("model-a", delay_seconds=.01)
    response = intelligence.infer(InferenceRequest("model-a", "provider_deterministic", "timeout", "analysis", "text", "x", timeout_seconds=.001), fallback=False)
    assert response.status is InferenceStatus.TIMEOUT
    assert intelligence.registry.get("model-a").health.failure_count >= 1


def test_context_limit_preserves_provenance(tmp_path: Path):
    manager = ModelContextManager(max_context_bytes=1200)
    context = manager.build("t", "goal", "x" * 5000, memory_evidence=[{"id": i, "provenance": "memory"} for i in range(20)])
    assert context.context_hash and context.truncation
    assert any(item["preserved_provenance"] for item in context.truncation)


def test_model_benchmark_and_repeated_trials(tmp_path: Path):
    _, _, intelligence, adapter = make_engine(tmp_path)
    benchmark = ModelBenchmark("b", "1", [{"task_id": "one", "input": "same"}], trial_count=2, deterministic_seed=7)
    result = intelligence.evaluator.evaluate("model-a", benchmark, adapter)
    assert result.trial_count == 2 and len(result.trials) == 2
    assert all(item.reproducibility["seed"] == 7 for item in result.trials)


def test_comparative_evaluation_decision_is_bounded(tmp_path: Path):
    _, _, intelligence, adapter = make_engine(tmp_path)
    intelligence.register_provider(ModelProvider("provider_b", "Provider B", ProviderType.DETERMINISTIC_TEST, lifecycle_state=ProviderLifecycle.ACTIVE))
    intelligence.register_model(Model("model-b", "provider_b", "Model B", capabilities=[ModelCapability("analysis", "analysis", .8)]), DeterministicTestAdapter("model-b", responses={"default": {"answer": "ok"}}))
    comparison = intelligence.evaluator.compare(["model-a", "model-b"], ModelBenchmark("b2", "1", [{"task_id": "one", "input": "same"}], 1, 9), {"model-a": adapter})
    assert comparison.decision in set(ModelComparisonDecision)
    assert comparison.ranking == ["model-a", "model-b"]


def test_learning_minimum_evidence_confidence_and_adjustment(tmp_path: Path):
    store, _, _, _ = make_engine(tmp_path, policy=LearningPolicy(minimum_evidence=2, confidence_threshold=.7, cooldown_seconds=0))
    learning = LearningEngine(store, LearningPolicy(minimum_evidence=2, confidence_threshold=.7, cooldown_seconds=0))
    observations = [LearningObservation(f"t{i}", f"t{i}", "model-a", "coding", True, True) for i in range(2)]
    outcomes = [learning.observe(item) for item in observations]
    assert len(outcomes) == 2
    blocked = learning.propose_adjustment("model:model-a", "coding", 0.0, .2, ["a", "b"], "too much", confidence=.9)
    assert blocked.status is LearningAdjustmentStatus.BLOCKED
    adjustment = learning.propose_adjustment("model:model-a", "coding", 0.0, .05, ["a", "b"], "repeated success", confidence=.9)
    assert learning.apply(adjustment).status is LearningAdjustmentStatus.APPLIED
    assert learning.rollback(adjustment).status is LearningAdjustmentStatus.ROLLED_BACK


def test_learning_protected_authority_and_poisoning_rejected(tmp_path: Path):
    store, _, _, _ = make_engine(tmp_path)
    learning = LearningEngine(store, LearningPolicy(minimum_evidence=1, confidence_threshold=.1))
    adjustment = learning.propose_adjustment("governance", "approval_logic", 0, .01, ["e"], "poison", confidence=1)
    assert adjustment.status is LearningAdjustmentStatus.BLOCKED
    assert "protected" in adjustment.reason


def test_learning_decay_and_deterministic_exploration(tmp_path: Path):
    store, _, _, _ = make_engine(tmp_path, policy=LearningPolicy(minimum_evidence=1, confidence_threshold=.1, exploration_rate=1.0, exploration_seed=4, decay=.5))
    learning = LearningEngine(store, LearningPolicy(minimum_evidence=1, confidence_threshold=.1, exploration_rate=1.0, exploration_seed=4, decay=.5))
    adjustment = learning.propose_adjustment("model:model-a", "analysis", 0, .05, ["e"], "bounded", confidence=1)
    learning.apply(adjustment)
    first = learning.explore("task", "low", ["model-a", "model-b"])
    second = learning.explore("task", "low", ["model-a", "model-b"])
    assert first.to_dict() == second.to_dict() and first.explore
    learning.decay()
    assert learning.score("model-a", ["analysis"]) <= .05


def test_specialist_model_routing(tmp_path: Path):
    _, _, intelligence, _ = make_engine(tmp_path)
    specialist = Specialist("coding", "Coding", "bounded coding", SpecialistType.CODING, [SpecialistCapability("coding", "coding", "coding")], allowed_filesystem_scope=str(tmp_path))
    selection = intelligence.route_specialist_task(specialist, "st", "code", ["coding"])
    assert selection.selected_model_id == "model-a"


def test_memory_model_evidence_excludes_prompt_and_response(tmp_path: Path):
    store = SQLiteStore(tmp_path / "w" / ".evo" / "agent.sqlite3")
    memory = MemoryManager(store, tmp_path / "w")
    memory.capture_model_performance({"model_id": "m", "task_category": "analysis", "prompt": "secret prompt", "response": "secret response", "success": True})
    assert all("secret prompt" not in item.content and "secret response" not in item.content for item in memory.list())


def test_cognitive_integration_is_advisory(tmp_path: Path):
    store, workspace, intelligence, _ = make_engine(tmp_path)
    cognitive = CognitiveOrchestrator(workspace, store=store, model_intelligence=intelligence)
    result = cognitive.run_goal("list the workspace files")
    rows = store.find_cognitive_decisions(result.goal.goal_id)
    import json
    payloads = [json.loads(row["payload"]) if isinstance(row["payload"], str) else row["payload"] for row in rows]
    assert any(payload.get("decision_type") == "model_selection" for payload in payloads)
    assert result.outcome.value in {"success", "partial", "inconclusive", "failed"}


def test_safe_mode_and_kill_switch(tmp_path: Path):
    _, _, intelligence, _ = make_engine(tmp_path)
    intelligence.set_safe_mode(True)
    blocked = intelligence.infer(InferenceRequest("model-a", "provider_deterministic", "safe", "analysis", "text", "x", risk="high"))
    assert blocked.status is InferenceStatus.BLOCKED
    intelligence.set_safe_mode(False); intelligence.activate_kill_switch()
    assert intelligence.infer(InferenceRequest("model-a", "provider_deterministic", "kill", "analysis", "text", "x")).status is InferenceStatus.BLOCKED
    with pytest.raises(PermissionError): intelligence.clear_kill_switch(actor="model")


def test_runtime_model_queue_preserves_runtime_boundaries(tmp_path: Path):
    store, workspace, intelligence, _ = make_engine(tmp_path)
    runtime = AgentRuntime(workspace, store=store, model_intelligence=intelligence)
    runtime.start()
    request = InferenceRequest("model-a", "provider_deterministic", "runtime-model", "analysis", "text", "input")
    task = runtime.enqueue_model_inference(request)
    cycle = runtime.run_cycle()
    assert cycle.tasks_started == 1
    assert runtime.task(task.task_id).status.value == "completed"
    assert runtime.task(task.task_id).metadata["verification_required"] is True


def test_restart_persistence_and_evolution_evidence(tmp_path: Path):
    store, workspace, intelligence, _ = make_engine(tmp_path)
    intelligence.select_model("persist", "analyze", capability_requirements=["analysis"])
    intelligence.learning.observe(LearningObservation("persist", "persist", "model-a", "analysis", True, True))
    restarted = ModelIntelligence(SQLiteStore(workspace / ".evo" / "agent.sqlite3"), workspace, adapters={"model-a": DeterministicTestAdapter("model-a")})
    assert restarted.registry.get("model-a") and restarted.store.find_model_selections(task_id="persist")
    assert restarted.evolution_evidence()
