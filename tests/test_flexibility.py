from __future__ import annotations

from pathlib import Path

from evo_agent.flexibility import FlexibilityContext, FlexibilityEngine
from evo_agent.kernel import AgentKernel
from evo_agent.model_adapter import ModelAdapter, RuleBasedAdapter
from evo_agent.models import Goal, Plan, PlanStep, RiskLevel, TaskStatus, ToolResult, new_id
from evo_agent.security import SecurityPolicy
from evo_agent.tools import ToolRegistry


class FailThenRecoverAdapter(ModelAdapter):
    def __init__(self):
        self.plan_calls = 0

    def create_plan(self, goal, tool_schemas, context=""):
        self.plan_calls += 1
        if self.plan_calls == 1:
            return Plan(goal.task_id, [PlanStep(new_id("step"), "Run a deliberately rejected command", "shell", {"command": "false"}, RiskLevel.HIGH, "result is non-empty")], "initial risky approach")
        return Plan(goal.task_id, [PlanStep(new_id("step"), "Inspect the workspace after recovery", "workspace_list", {"path": "."}, RiskLevel.LOW, "result is valid JSON")], "recovery approach")

    def choose_recovery(self, goal, failed_step, result):
        return "Recovery plan was generated from the recorded failure."


def make_engine(tmp_path: Path) -> FlexibilityEngine:
    policy = SecurityPolicy(tmp_path)
    return FlexibilityEngine(RuleBasedAdapter(), ToolRegistry(policy))


def test_assessment_is_structured(tmp_path: Path):
    engine = make_engine(tmp_path)
    assessment = engine.assess(Goal("list the files"))
    assert assessment.complexity == "simple"
    assert assessment.deterministic is True
    assert assessment.expected_steps == 1
    assert assessment.risk is RiskLevel.LOW
    assert assessment.available_tool_count >= 4


def test_strategy_selection_direct_plan_first_recovery_and_approval(tmp_path: Path):
    engine = make_engine(tmp_path)
    direct_context = FlexibilityContext(Goal("list files"))
    direct_context.assessment = engine.assess(direct_context.goal, direct_context)
    assert engine.select_strategy(direct_context.assessment, direct_context).name == "direct"

    multi_context = FlexibilityContext(Goal("research and compare approaches"))
    multi_context.assessment = engine.assess(multi_context.goal, multi_context)
    assert engine.select_strategy(multi_context.assessment, multi_context).name == "plan-first"

    risky_context = FlexibilityContext(Goal("run a shell command"))
    risky_context.assessment = engine.assess(risky_context.goal, risky_context)
    assert engine.select_strategy(risky_context.assessment, risky_context).name == "approval-aware"

    failed_context = FlexibilityContext(Goal("list files"), assessment=direct_context.assessment, failures=[{"error": "failed"}])
    assert engine.select_strategy(failed_context.assessment, failed_context).name == "recovery"


def test_historical_failure_influences_strategy_without_bypassing_policy(tmp_path: Path):
    engine = make_engine(tmp_path)
    context = FlexibilityContext(Goal("list files"), constraints={"historical_experiences": [{"final_outcome": "failure"}]})
    assessment = engine.assess(context.goal, context)
    context.assessment = assessment
    assert engine.select_strategy(assessment, context).name == "plan-first"


def test_tool_recommendation_uses_registry(tmp_path: Path):
    engine = make_engine(tmp_path)
    recommendations = engine.select_tools(Goal("list the files in the workspace"))
    assert recommendations
    assert recommendations[0].tool_name == "workspace_list"
    assert all(item.tool_name in {spec["function"]["name"] for spec in engine.registry.schemas()} for item in recommendations)


def test_failure_triggers_strategy_switch_and_replan(tmp_path: Path):
    adapter = FailThenRecoverAdapter()
    kernel = AgentKernel(tmp_path, adapter, approval_callback=lambda call, reason: True, max_retries=0, max_adaptations=1)
    outcome = kernel.run("run a shell command")
    assert outcome.status is TaskStatus.SUCCEEDED
    assert adapter.plan_calls == 2
    event_types = {event.event_type.value for event in outcome.events}
    assert {"strategy_failed", "adaptation_triggered", "strategy_changed", "replan_triggered", "recovery_attempted"}.issubset(event_types)
    assert any(event.payload.get("to") == "recovery" for event in outcome.events if event.event_type.value == "strategy_changed")
    persisted_types = {event["event_type"] for event in kernel.store.events_for_task(outcome.task_id)}
    assert "adaptation_triggered" in persisted_types
    assert "replan_triggered" in persisted_types


def test_adaptation_limit_stops_repetition(tmp_path: Path):
    class AlwaysFailAdapter(FailThenRecoverAdapter):
        def create_plan(self, goal, tool_schemas, context=""):
            self.plan_calls += 1
            return Plan(goal.task_id, [PlanStep(new_id("step"), "Run a rejected command", "shell", {"command": "false"}, RiskLevel.HIGH, "result is non-empty")], "always fails")

    adapter = AlwaysFailAdapter()
    kernel = AgentKernel(tmp_path, adapter, approval_callback=lambda call, reason: True, max_retries=0, max_adaptations=1)
    outcome = kernel.run("run a shell command")
    assert outcome.status is TaskStatus.FAILED
    assert adapter.plan_calls == 2
    assert "failed" in outcome.summary.lower()


def test_flexibility_cannot_bypass_approval(tmp_path: Path):
    kernel = AgentKernel(tmp_path, RuleBasedAdapter(), approval_callback=lambda call, reason: False)
    outcome = kernel.run("write a file")
    assert outcome.status is TaskStatus.BLOCKED
    assert not (tmp_path / "agent_goal.txt").exists()
