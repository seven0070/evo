from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from .checkpoints import CheckpointManager
from .flexibility import FlexibilityContext, FlexibilityEngine, Strategy
from .model_adapter import ModelAdapter
from .models import Event, EventType, Goal, Plan, PlanStep, TaskOutcome, TaskStatus, ToolCall, utc_now
from .security import SecurityPolicy
from .storage import SQLiteStore
from .tools import ToolRegistry
from .verifier import Verifier

ApprovalCallback = Callable[[ToolCall, str], bool]


class AgentKernel:
    def __init__(
        self,
        workspace: Path,
        model: ModelAdapter,
        store: SQLiteStore | None = None,
        approval_callback: ApprovalCallback | None = None,
        max_steps: int = 12,
        max_retries: int = 1,
        max_adaptations: int = 1,
        flexibility: FlexibilityEngine | None = None,
    ):
        self.workspace = Path(workspace).expanduser().resolve()
        self.workspace.mkdir(parents=True, exist_ok=True)
        self.store = store or SQLiteStore(self.workspace / ".evo" / "agent.sqlite3")
        self.policy = SecurityPolicy(self.workspace)
        self.tools = ToolRegistry(self.policy)
        self.model = model
        self.verifier = Verifier(self.policy)
        self.checkpoints = CheckpointManager(self.workspace, self.store)
        self.flexibility = flexibility or FlexibilityEngine(model, self.tools)
        self.approval_callback = approval_callback or (lambda call, reason: False)
        self.max_steps = max_steps
        self.max_retries = max_retries
        self.max_adaptations = max_adaptations

    def run(self, request: str) -> TaskOutcome:
        goal = Goal(request)
        self.store.create_task(goal)
        events: list[Event] = []

        def record(event_type: EventType, payload: dict[str, Any]) -> None:
            event = Event(goal.task_id, event_type, payload)
            events.append(event)
            self.store.append_event(event)

        self.store.update_task(goal.task_id, TaskStatus.PLANNING)
        record(EventType.TASK_CREATED, {"goal": goal.text})
        try:
            checkpoint = self.checkpoints.create(goal.task_id, "before-task")
            memories = self.store.recent_memories()
            context = FlexibilityContext(
                goal=goal,
                permissions={"approval_required_for": [level.value for level in self.policy.approval_required_for]},
                constraints={"tool_schemas": self.tools.schemas(), "max_steps": self.max_steps, "max_retries": self.max_retries},
            )
            context.assessment = self.flexibility.assess(goal, context)
            record(EventType.PLAN_CREATED, {"checkpoint": str(checkpoint), "assessment": context.assessment.to_dict(), "memories": memories})
            recommendations = self.flexibility.select_tools(goal)
            for recommendation in recommendations:
                record(EventType.TOOL_RECOMMENDED, recommendation.to_dict())
            strategy = self.flexibility.select_strategy(context.assessment, context)
            record(EventType.STRATEGY_SELECTED, {"strategy": strategy.describe(), "assessment": context.assessment.to_dict()})
            plan = self.flexibility.plan(strategy, context)
            context.current_plan = plan
            record(EventType.PLAN_CREATED, {"strategy": strategy.name, "rationale": plan.rationale, "steps": [step.__dict__ for step in plan.steps]})
        except Exception as exc:
            message = f"Planning failed: {exc}"
            self.store.update_task(goal.task_id, TaskStatus.FAILED, message)
            record(EventType.TASK_COMPLETED, {"status": TaskStatus.FAILED.value, "error": message})
            return TaskOutcome(goal.task_id, TaskStatus.FAILED, message, 0, events, message)

        self.store.update_task(goal.task_id, TaskStatus.RUNNING)
        queue = list(plan.steps)
        completed = 0
        executed_steps = 0
        adaptations = 0

        while queue and executed_steps < self.max_steps:
            step = queue.pop(0)
            executed_steps += 1
            if not step.tool_name:
                completed += 1
                continue
            try:
                spec = self.tools.get(step.tool_name)
                call = ToolCall(task_id=goal.task_id, step_id=step.step_id, tool_name=step.tool_name, arguments=step.arguments, risk=spec.risk)
                record(EventType.TOOL_REQUESTED, {"step": executed_steps, "tool": call.tool_name, "arguments": call.arguments, "risk": call.risk.value, "strategy": strategy.name})
                if self.policy.requires_approval(call):
                    reason = f"'{call.tool_name}' is classified as {call.risk.value}-risk"
                    record(EventType.APPROVAL_REQUESTED, {"tool": call.tool_name, "reason": reason})
                    if not self.approval_callback(call, reason):
                        record(EventType.APPROVAL_DENIED, {"tool": call.tool_name, "reason": reason})
                        message = f"Task blocked: approval denied for {call.tool_name}"
                        self.store.update_task(goal.task_id, TaskStatus.BLOCKED, message)
                        return TaskOutcome(goal.task_id, TaskStatus.BLOCKED, message, completed, events, message)
                    call.approved = True
                    record(EventType.APPROVAL_GRANTED, {"tool": call.tool_name})

                retries = 0
                step_finished = False
                while not step_finished:
                    result = self.tools.execute(call)
                    if result.success:
                        record(EventType.TOOL_COMPLETED, {"tool": result.tool_name, "output": result.output, "metadata": result.metadata})
                    else:
                        record(EventType.TOOL_FAILED, {"tool": result.tool_name, "error": result.error, "output": result.output})
                        record(EventType.STRATEGY_FAILED, {"strategy": strategy.name, "step": step.description, "error": result.error})
                    verification = self.verifier.verify(step, result)
                    context.observations.append(result.output[-1000:] if result.output else (result.error or "no output"))
                    context.verification_results.append({"step": step.description, "success": verification.success, "checks": verification.checks})
                    record(EventType.VERIFICATION, {"step": step.description, "success": verification.success, "summary": verification.summary, "checks": verification.checks})
                    if verification.success:
                        completed += 1
                        step_finished = True
                        continue

                    context.failures.append({"step": step.description, "tool": step.tool_name, "error": result.error, "verification": verification.summary})
                    decision = self.flexibility.recommend_next_action(context, step, result)
                    record(EventType.ADAPTATION_TRIGGERED, {"decision": decision.to_dict(), "failure": context.failures[-1], "attempt": context.attempt})
                    if decision.action == "retry" and retries < self.max_retries:
                        retries += 1
                        record(EventType.RECOVERY_ATTEMPTED, {"strategy": strategy.name, "action": "retry", "retry": retries})
                        continue
                    if decision.replan and adaptations < self.max_adaptations:
                        adaptations += 1
                        context.attempt = adaptations
                        old_strategy = strategy
                        strategy = self.flexibility.strategies.get(decision.strategy_name, self.flexibility.strategies["recovery"])
                        record(EventType.RECOVERY_ATTEMPTED, {"strategy": strategy.name, "action": "replan", "adaptation": adaptations})
                        record(EventType.STRATEGY_CHANGED, {"from": old_strategy.name, "to": strategy.name, "reason": decision.reason})
                        context.current_plan = None
                        replacement = self.flexibility.plan(strategy, context)
                        context.current_plan = replacement
                        queue = list(replacement.steps)
                        record(EventType.REPLAN_TRIGGERED, {"strategy": strategy.name, "rationale": replacement.rationale, "steps": [item.__dict__ for item in replacement.steps]})
                        step_finished = True
                        continue

                    recovery = self.model.choose_recovery(goal, step, result)
                    record(EventType.RECOVERY, {"step": step.description, "strategy": strategy.name, "recommendation": recovery, "retries": retries, "adaptations": adaptations})
                    message = f"Task failed at step {executed_steps}: {verification.summary}"
                    self.store.update_task(goal.task_id, TaskStatus.FAILED, message)
                    record(EventType.TASK_COMPLETED, {"status": TaskStatus.FAILED.value, "error": message})
                    return TaskOutcome(goal.task_id, TaskStatus.FAILED, message, completed, events, message)
            except Exception as exc:
                message = f"Execution failed at step {executed_steps}: {exc}"
                record(EventType.TOOL_FAILED, {"step": executed_steps, "error": str(exc)})
                self.store.update_task(goal.task_id, TaskStatus.FAILED, message)
                record(EventType.TASK_COMPLETED, {"status": TaskStatus.FAILED.value, "error": message})
                return TaskOutcome(goal.task_id, TaskStatus.FAILED, message, completed, events, message)

        summary = f"Completed {completed} step(s) after {adaptations} adaptation(s)"
        self.store.update_task(goal.task_id, TaskStatus.SUCCEEDED, summary)
        self.store.add_memory("experience", f"Goal: {goal.text}; outcome: {summary}; strategy: {strategy.name}", utc_now())
        record(EventType.TASK_COMPLETED, {"status": TaskStatus.SUCCEEDED.value, "summary": summary, "strategy": strategy.name, "adaptations": adaptations})
        return TaskOutcome(goal.task_id, TaskStatus.SUCCEEDED, summary, completed, events)
