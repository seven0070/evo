from __future__ import annotations

from pathlib import Path

from evo_agent.evaluation import EvaluationEngine
from evo_agent.experience import ExperienceEngine
from evo_agent.kernel import AgentKernel
from evo_agent.model_adapter import ModelAdapter, RuleBasedAdapter
from evo_agent.models import OutcomeType, Plan, PlanStep, RiskLevel, TaskStatus, new_id


class FailThenRecoverAdapter(ModelAdapter):
    def __init__(self, always_fail: bool = False):
        self.calls = 0
        self.always_fail = always_fail

    def create_plan(self, goal, tool_schemas, context=""):
        self.calls += 1
        if self.calls == 1 or self.always_fail:
            return Plan(goal.task_id, [PlanStep(new_id("step"), "Run rejected command", "shell", {"command": "false"}, RiskLevel.HIGH, "result is non-empty")], "initial strategy")
        return Plan(goal.task_id, [PlanStep(new_id("step"), "Inspect workspace after recovery", "workspace_list", {"path": "."}, RiskLevel.LOW, "result is valid JSON")], "recovery strategy")

    def choose_recovery(self, goal, failed_step, result):
        return "bounded recovery complete"


def test_success_creates_persisted_experience_and_evaluation(tmp_path: Path):
    kernel = AgentKernel(tmp_path, RuleBasedAdapter())
    outcome = kernel.run("list the files")
    assert outcome.status is TaskStatus.SUCCEEDED
    experiences = kernel.experience_engine.retrieve(task_type="workspace_inspection")
    assert len(experiences) == 1
    experience = experiences[0]
    assert experience.final_outcome is OutcomeType.SUCCESS
    assert experience.selected_strategy == "direct"
    assert experience.agent_version == "0.4.0"
    assert experience.evaluation_id
    evaluation = kernel.store.evaluation_by_id(experience.evaluation_id)
    assert evaluation is not None
    assert evaluation["evaluator_version"] == "evaluation-v1"
    assert json_payload(evaluation)["success_score"] > 0


def test_failed_recovery_records_strategy_change_and_cost(tmp_path: Path):
    adapter = FailThenRecoverAdapter()
    kernel = AgentKernel(tmp_path, adapter, approval_callback=lambda call, reason: True, max_retries=0, max_adaptations=1)
    outcome = kernel.run("run a shell command")
    assert outcome.status is TaskStatus.SUCCEEDED
    experience = kernel.experience_engine.retrieve(limit=1)[0]
    assert experience.recovery_attempts
    assert experience.strategy_changes
    evaluation = kernel.evaluation_engine.evaluate(experience)
    assert evaluation.recovery_metrics["recovery_succeeded"] is True
    assert evaluation.replan_count >= 1
    assert evaluation.success_score < 100
    assert any("recovery" in line for line in evaluation.explanation)


def test_unsuccessful_task_is_not_marked_success(tmp_path: Path):
    adapter = FailThenRecoverAdapter(always_fail=True)
    kernel = AgentKernel(tmp_path, adapter, approval_callback=lambda call, reason: True, max_retries=0, max_adaptations=1)
    outcome = kernel.run("run a shell command")
    assert outcome.status is TaskStatus.FAILED
    experience = kernel.experience_engine.retrieve(limit=1)[0]
    assert experience.final_outcome is OutcomeType.FAILURE
    evaluation = kernel.evaluation_engine.evaluate(experience)
    assert evaluation.verified is False
    assert evaluation.success_score == 0


def test_blocked_task_has_blocked_outcome(tmp_path: Path):
    kernel = AgentKernel(tmp_path, RuleBasedAdapter(), approval_callback=lambda call, reason: False)
    outcome = kernel.run("write a file")
    assert outcome.status is TaskStatus.BLOCKED
    experience = kernel.experience_engine.retrieve(outcome=OutcomeType.BLOCKED, limit=1)[0]
    assert experience.final_outcome is OutcomeType.BLOCKED
    assert experience.approval_events


def test_retrieval_filters_and_recent_order(tmp_path: Path):
    kernel = AgentKernel(tmp_path, RuleBasedAdapter())
    kernel.run("list the files")
    kernel.run("read the file")
    assert kernel.experience_engine.retrieve(task_type="workspace_inspection")
    assert kernel.experience_engine.retrieve(strategy="direct")
    assert kernel.experience_engine.retrieve(outcome=OutcomeType.SUCCESS)
    assert kernel.experience_engine.retrieve(tool="workspace_list")
    assert len(kernel.experience_engine.retrieve(limit=1)) == 1


def test_experience_informs_next_strategy_without_self_modification(tmp_path: Path):
    failing = FailThenRecoverAdapter(always_fail=True)
    first_kernel = AgentKernel(tmp_path, failing, approval_callback=lambda call, reason: True, max_retries=0, max_adaptations=0)
    first = first_kernel.run("list the files")
    assert first.status is TaskStatus.FAILED

    second_kernel = AgentKernel(tmp_path, RuleBasedAdapter(), approval_callback=lambda call, reason: False)
    second = second_kernel.run("list the files")
    assert second.status is TaskStatus.SUCCEEDED
    strategy_events = [event for event in second.events if event.event_type.value == "strategy_selected"]
    assert strategy_events[-1].payload["strategy"]["name"] == "plan-first"
    assert any(event.event_type.value == "experience_retrieved" for event in second.events)


def test_evaluation_is_deterministic(tmp_path: Path):
    kernel = AgentKernel(tmp_path, RuleBasedAdapter())
    kernel.run("list the files")
    experience = kernel.experience_engine.retrieve(limit=1)[0]
    first = kernel.evaluation_engine.evaluate(experience).to_dict()
    second = kernel.evaluation_engine.evaluate(experience).to_dict()
    assert first == second


def json_payload(row: dict) -> dict:
    import json
    return json.loads(row["payload"])
