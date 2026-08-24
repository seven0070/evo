from __future__ import annotations

from pathlib import Path

import pytest

from evo_agent.cognitive import (
    AmbiguityStatus,
    CapabilityAssessment,
    CognitiveOrchestrator,
    CognitiveOutcome,
    CognitiveState,
    ConfidenceLevel,
    FailureDiagnosisEngine,
    FailureKind,
    GoalUnderstandingEngine,
    PlanningEngine,
    ReplanningEngine,
    RequirementKind,
    SubtaskStatus,
    TaskDecompositionEngine,
    TaskGraphEngine,
)
from evo_agent.kernel import AgentKernel
from evo_agent.model_adapter import RuleBasedAdapter
from evo_agent.models import Event, EventType, TaskOutcome, TaskStatus
from evo_agent.orchestrator import EvolutionOrchestrator, OrchestrationPath
from evo_agent.storage import SQLiteStore


def build_agent(tmp_path: Path) -> tuple[CognitiveOrchestrator, SQLiteStore]:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "README.md").write_text("one\ntwo\n", encoding="utf-8")
    store = SQLiteStore(workspace / ".evo" / "agent.sqlite3")
    kernel = AgentKernel(workspace, RuleBasedAdapter(), store=store, approval_callback=lambda call, reason: True)
    return CognitiveOrchestrator(workspace, store=store, kernel=kernel), store


def test_goal_parsing_normalization_constraints_and_ambiguity():
    engine = GoalUnderstandingEngine()
    goal = engine.understand("  Create a report within the workspace without deleting inputs.  ")
    assert goal.normalized_goal == "create a report within the workspace without deleting inputs."
    assert goal.ambiguity is AmbiguityStatus.CLEAR
    assert any(item.kind is RequirementKind.EXPLICIT for item in goal.constraints)
    assert goal.success_criteria
    ambiguous = engine.understand("Build me an app")
    assert ambiguous.ambiguity is AmbiguityStatus.CRITICAL
    assert ambiguous.missing_requirements


def test_task_decomposition_graph_order_and_cycle_rejection():
    goal = GoalUnderstandingEngine().understand("list every text file, count lines, and create a report")
    intent = __import__("evo_agent.cognitive", fromlist=["IntentEngine"]).IntentEngine().build(goal)
    graph = TaskDecompositionEngine().decompose(goal, intent)
    assert len(graph.nodes) == 4
    assert TaskGraphEngine().validate(graph) == []
    ordered = TaskGraphEngine().order(graph)
    assert [node.task_id for node in ordered] == [node.task_id for node in graph.nodes]
    graph.edges.append((graph.nodes[-1].task_id, graph.nodes[0].task_id))
    assert any("cycle" in error for error in TaskGraphEngine().validate(graph))


def test_plan_generation_and_low_risk_selection():
    goal = GoalUnderstandingEngine().understand("list files")
    intent = __import__("evo_agent.cognitive", fromlist=["IntentEngine"]).IntentEngine().build(goal)
    graph = TaskDecompositionEngine().decompose(goal, intent)
    plans = PlanningEngine().generate(goal, graph, "architecture-v1")
    selected = __import__("evo_agent.cognitive", fromlist=["ReasoningEngine"]).ReasoningEngine().select_plan(plans, ["workspace_list"])
    assert selected.selected
    assert selected.architecture_version == "architecture-v1"
    assert selected.required_tools == ["workspace_list"]


def test_capability_gap_is_explicit_and_not_fabricated(tmp_path: Path):
    agent, store = build_agent(tmp_path)
    result = agent.run_goal("generate a quantum model")
    assert result.outcome is CognitiveOutcome.BLOCKED
    assert result.state.state is CognitiveState.BLOCKED
    assert result.capability_gaps[0].assessment is CapabilityAssessment.UNAVAILABLE
    assert result.capability_gaps[0].opportunity_id
    assert store.find_work_items()
    assert store.find_proposals()


def test_structural_capability_gap_routes_metamorphosis_without_approval(tmp_path: Path):
    agent, store = build_agent(tmp_path)
    result = agent.run_goal("design a structural architecture component requiring multimedia capability")
    assert result.outcome is CognitiveOutcome.BLOCKED
    assert result.capability_gaps[0].structural
    assert result.capability_gaps[0].opportunity_id
    items = store.find_work_items()
    assert items and items[0]["change_type"] == OrchestrationPath.METAMORPHOSIS.value
    proposals = store.find_metamorphosis_proposals()
    assert proposals and proposals[0]["status"] == "pending_approval"


def test_ambiguous_goal_waits_for_input_and_persists(tmp_path: Path):
    agent, store = build_agent(tmp_path)
    result = agent.run_goal("Build me an app")
    assert result.outcome is CognitiveOutcome.WAITING_FOR_INPUT
    assert result.state.state is CognitiveState.WAITING_FOR_INPUT
    assert store.cognitive_goal_by_id(result.goal.goal_id)
    assert not store.find_cognitive_task_graphs(result.goal.goal_id) if hasattr(store, "find_cognitive_task_graphs") else True


def test_real_kernel_complex_multistep_goal_is_verified(tmp_path: Path):
    agent, _ = build_agent(tmp_path)
    result = agent.run_goal("list every text file, count the lines in each file, and create a report")
    assert result.outcome is CognitiveOutcome.SUCCESS
    assert result.state.state is CognitiveState.COMPLETED
    assert result.graph and len(result.graph.nodes) == 4
    assert result.verification and result.verification.success
    assert result.experience_id and result.evaluation_id
    assert all(node.status is SubtaskStatus.SUCCEEDED for node in result.graph.nodes)


def test_false_success_is_rejected_without_kernel_verification(tmp_path: Path):
    agent, _ = build_agent(tmp_path)
    goal = GoalUnderstandingEngine().understand("list files")
    intent = __import__("evo_agent.cognitive", fromlist=["IntentEngine"]).IntentEngine().build(goal)
    graph = TaskDecompositionEngine().decompose(goal, intent)
    plan = PlanningEngine().generate(goal, graph)[0]
    fake = TaskOutcome("fake", TaskStatus.SUCCEEDED, "claimed success", 1, [Event("fake", EventType.TASK_CREATED, {"goal": goal.original_text})])
    agent._run_kernel = lambda node, parent, recovery=False: fake
    result = agent._execute(goal, intent, graph, plan, __import__("evo_agent.cognitive", fromlist=["CognitiveStateRecord"]).CognitiveStateRecord(goal.goal_id, CognitiveState.EXECUTING), [], __import__("time").monotonic())
    assert result.outcome in {CognitiveOutcome.FAILED, CognitiveOutcome.INCONCLUSIVE}
    assert not result.verification.success


def test_failure_diagnosis_and_bounded_replanning():
    task = __import__("evo_agent.cognitive", fromlist=["CognitiveTask"]).CognitiveTask("task", "goal", None, "run a command", [], [], [], [], ["shell"])
    blocked = TaskOutcome("task", TaskStatus.BLOCKED, "Task blocked: approval denied for shell", 0, [], "approval denied")
    diagnosis = FailureDiagnosisEngine().diagnose(task, blocked)
    assert diagnosis.kind is FailureKind.PERMISSION_FAILURE
    assert diagnosis.confidence is ConfidenceLevel.CONFIDENT
    graph = __import__("evo_agent.cognitive", fromlist=["TaskGraph"]).TaskGraph("graph", "goal", __import__("evo_agent.cognitive", fromlist=["TaskGraphType"]).TaskGraphType.SEQUENTIAL, [task], [])
    _, replanned, _ = ReplanningEngine(max_replans=1).replan(graph, task, diagnosis, 0)
    assert not replanned
    retryable = __import__("evo_agent.cognitive", fromlist=["FailureDiagnosis"]).FailureDiagnosis("task", FailureKind.STRATEGY_FAILURE, ConfidenceLevel.PROBABLE, "strategy failed", True)
    _, replanned, _ = ReplanningEngine(max_replans=1).replan(graph, task, retryable, 0)
    assert replanned
    _, replanned, _ = ReplanningEngine(max_replans=1).replan(graph, task, retryable, 1)
    assert not replanned


def test_failure_then_flexibility_replan_uses_kernel_recovery(tmp_path: Path):
    agent, store = build_agent(tmp_path)
    calls: list[str] = []
    original = agent.kernel.run
    def run(request: str):
        calls.append(request)
        if len(calls) == 1:
            return TaskOutcome("failed", TaskStatus.FAILED, "strategy failed", 0, [Event("failed", EventType.VERIFICATION, {"success": False, "summary": "incomplete"})], "verification incomplete")
        return original(request)
    agent.kernel.run = run
    result = agent.run_goal("list files")
    assert result.replans == 1
    assert result.outcome is CognitiveOutcome.SUCCESS
    assert len(calls) == 2
    decisions = store.find_cognitive_decisions(result.goal.goal_id)
    assert any(item["decision_type"] == "flexibility" and item["decision"] == "replan" for item in decisions)
    assert any(item["decision_type"] == "replan" and item["decision"] == "replan" for item in decisions)


def test_partial_success_is_not_reported_as_success(tmp_path: Path):
    agent, _ = build_agent(tmp_path)
    goal = GoalUnderstandingEngine().understand("list every text file and create a report")
    intent = __import__("evo_agent.cognitive", fromlist=["IntentEngine"]).IntentEngine().build(goal)
    graph = TaskDecompositionEngine().decompose(goal, intent)
    plan = PlanningEngine().generate(goal, graph)[0]
    count = {"n": 0}
    def run(node, parent, recovery=False):
        count["n"] += 1
        if count["n"] == 1:
            return agent.kernel.run("list files")
        return TaskOutcome("failed", TaskStatus.FAILED, "failed", 1, [Event("failed", EventType.VERIFICATION, {"success": False, "summary": "incomplete"})], "incomplete")
    agent._run_kernel = run
    result = agent._execute(goal, intent, graph, plan, __import__("evo_agent.cognitive", fromlist=["CognitiveStateRecord"]).CognitiveStateRecord(goal.goal_id, CognitiveState.EXECUTING), [], __import__("time").monotonic())
    assert result.outcome is CognitiveOutcome.PARTIAL
    assert result.verification and not result.verification.success
    assert result.verification.completed_count < result.verification.required_count


def test_persistence_and_restart_recovery(tmp_path: Path):
    agent, store = build_agent(tmp_path)
    result = agent.run_goal("list files")
    resumed = CognitiveOrchestrator(agent.workspace, store=store, kernel=agent.kernel).resume(result.goal.goal_id)
    assert resumed.goal.goal_id == result.goal.goal_id
    assert resumed.outcome is CognitiveOutcome.SUCCESS
    assert store.cognitive_state_by_goal(result.goal.goal_id)
    assert store.find_cognitive_observations(result.goal.goal_id)


def test_resource_limits_fail_safely(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    store = SQLiteStore(workspace / ".evo" / "agent.sqlite3")
    kernel = AgentKernel(workspace, RuleBasedAdapter(), store=store, max_steps=1)
    agent = CognitiveOrchestrator(workspace, store=store, kernel=kernel, policy={"max_tool_calls": 0})
    result = agent.run_goal("list files")
    assert result.outcome is CognitiveOutcome.INCONCLUSIVE
    assert result.state.state is CognitiveState.FAILED


def test_cognitive_does_not_bypass_kernel_approval(tmp_path: Path):
    agent, _ = build_agent(tmp_path)
    agent.kernel.approval_callback = lambda call, reason: False
    result = agent.run_goal("run a shell command")
    assert result.outcome is CognitiveOutcome.WAITING_FOR_APPROVAL
    assert result.state.state is CognitiveState.WAITING_FOR_APPROVAL
    assert result.failures and result.failures[0].requires_approval


def test_evolution_orchestrator_receives_cognitive_experience(tmp_path: Path):
    agent, store = build_agent(tmp_path)
    result = agent.run_goal("list files")
    experiences = store.find_experiences(goal="list files")
    assert experiences
    evolution = EvolutionOrchestrator(store, Path(__file__).resolve().parents[1])
    opportunities = evolution.detector.detect(experiences, evolution.metamorphosis.get_architecture().architecture_version)
    assert isinstance(opportunities, list)
    assert result.experience_id == experiences[0]["experience_id"]


def test_restricted_capability_is_distinguished_from_unavailable():
    from evo_agent.cognitive import CapabilityGapDetector, CognitiveTask
    task = CognitiveTask("task", "goal", None, "use restricted web research", [], [], [], [], ["web_research"])
    gap = CapabilityGapDetector().check("goal", task, ["web_research"])
    assert gap and gap.assessment is CapabilityAssessment.RESTRICTED


def test_clarification_reopens_critical_goal_safely(tmp_path: Path):
    agent, _ = build_agent(tmp_path)
    waiting = agent.run_goal("Build me an app")
    assert waiting.outcome is CognitiveOutcome.WAITING_FOR_INPUT
    result = agent.clarify(waiting.goal.goal_id, "a workspace report listing files")
    assert result.goal.goal_id == waiting.goal.goal_id
    assert result.outcome is CognitiveOutcome.SUCCESS


def test_partial_verification_accounts_for_eight_of_ten_tasks(tmp_path: Path):
    from evo_agent.cognitive import CognitiveGoal, CognitiveStateRecord, CognitiveTask, IntentEngine, TaskGraph, TaskGraphType
    goal = GoalUnderstandingEngine().understand("process ten files")
    nodes = [CognitiveTask(f"task-{index}", goal.goal_id, None, f"task {index}", [], [], [], [], ["planning"], status=SubtaskStatus.SUCCEEDED if index < 8 else SubtaskStatus.FAILED) for index in range(10)]
    graph = TaskGraph("graph", goal.goal_id, TaskGraphType.SEQUENTIAL, nodes, [])
    report = __import__("evo_agent.cognitive", fromlist=["CognitiveVerifier"]).CognitiveVerifier(tmp_path).verify(goal, graph, [], [])
    assert report.outcome is CognitiveOutcome.PARTIAL
    assert report.completed_count == 8
    assert report.failed_count == 2
    assert not report.success


def test_repeated_cognitive_failures_become_phase9_opportunity(tmp_path: Path):
    agent, store = build_agent(tmp_path)
    agent.kernel.approval_callback = lambda call, reason: True
    original = agent.kernel.run
    def fail(request: str):
        if "list" in request.lower():
            return TaskOutcome("failed", TaskStatus.FAILED, "strategy failed", 0, [Event("failed", EventType.VERIFICATION, {"success": False, "summary": "incomplete"})], "strategy failed")
        return original(request)
    agent.kernel.run = fail
    first = agent.run_goal("list files")
    second = agent.run_goal("list files")
    assert first.outcome in {CognitiveOutcome.FAILED, CognitiveOutcome.PARTIAL}
    assert second.outcome in {CognitiveOutcome.FAILED, CognitiveOutcome.PARTIAL}
    evolution = EvolutionOrchestrator(store, Path(__file__).resolve().parents[1])
    experiences = evolution.observe(limit=20)
    opportunities = evolution.detector.detect(experiences, evolution.metamorphosis.get_architecture().architecture_version)
    assert any(opportunity.recommended_change_type in {OrchestrationPath.EVOLUTION, OrchestrationPath.FLEXIBILITY} for opportunity in opportunities)


def test_interrupted_read_only_task_is_safely_requeued_on_restart(tmp_path: Path):
    agent, store = build_agent(tmp_path)
    original = agent.kernel.run
    def interrupt(request: str):
        raise KeyboardInterrupt("simulated process interruption")
    agent.kernel.run = interrupt
    with pytest.raises(KeyboardInterrupt):
        agent.run_goal("list files")
    state_rows = store.find_cognitive_goals()
    assert state_rows
    goal_id = state_rows[0]["goal_id"]
    persisted = store.cognitive_state_by_goal(goal_id)
    assert persisted and "executing" in persisted["state"]
    agent.kernel.run = original
    resumed = CognitiveOrchestrator(agent.workspace, store=store, kernel=agent.kernel).resume(goal_id)
    assert resumed.outcome is CognitiveOutcome.SUCCESS
    assert resumed.state.state is CognitiveState.COMPLETED
