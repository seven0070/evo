from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from .checkpoints import CheckpointManager
from .model_adapter import ModelAdapter
from .models import Event, EventType, Goal, PlanStep, TaskOutcome, TaskStatus, ToolCall, utc_now
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
    ):
        self.workspace = Path(workspace).expanduser().resolve()
        self.workspace.mkdir(parents=True, exist_ok=True)
        self.store = store or SQLiteStore(self.workspace / ".evo" / "agent.sqlite3")
        self.policy = SecurityPolicy(self.workspace)
        self.tools = ToolRegistry(self.policy)
        self.model = model
        self.verifier = Verifier(self.policy)
        self.checkpoints = CheckpointManager(self.workspace, self.store)
        self.approval_callback = approval_callback or (lambda call, reason: False)
        self.max_steps = max_steps
        self.max_retries = max_retries

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
            record(EventType.PLAN_CREATED, {"checkpoint": str(checkpoint)})
            memories = self.store.recent_memories()
            context = "\n".join(f"{item['kind']}: {item['content']}" for item in memories)
            plan = self.model.create_plan(goal, self.tools.schemas(), context)
            record(EventType.PLAN_CREATED, {"rationale": plan.rationale, "steps": [step.__dict__ for step in plan.steps]})
        except Exception as exc:
            message = f"Planning failed: {exc}"
            self.store.update_task(goal.task_id, TaskStatus.FAILED, message)
            record(EventType.TASK_COMPLETED, {"status": TaskStatus.FAILED.value, "error": message})
            return TaskOutcome(goal.task_id, TaskStatus.FAILED, message, 0, events, message)

        self.store.update_task(goal.task_id, TaskStatus.RUNNING)
        completed = 0
        for index, step in enumerate(plan.steps[: self.max_steps], start=1):
            if not step.tool_name:
                completed += 1
                continue
            try:
                spec = self.tools.get(step.tool_name)
                call = ToolCall(task_id=goal.task_id, step_id=step.step_id, tool_name=step.tool_name, arguments=step.arguments, risk=spec.risk)
                record(EventType.TOOL_REQUESTED, {"step": index, "tool": call.tool_name, "arguments": call.arguments, "risk": call.risk.value})
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
                while True:
                    result = self.tools.execute(call)
                    if result.success:
                        record(EventType.TOOL_COMPLETED, {"tool": result.tool_name, "output": result.output, "metadata": result.metadata})
                    else:
                        record(EventType.TOOL_FAILED, {"tool": result.tool_name, "error": result.error, "output": result.output})
                    verification = self.verifier.verify(step, result)
                    record(EventType.VERIFICATION, {"step": step.description, "success": verification.success, "summary": verification.summary, "checks": verification.checks})
                    if verification.success:
                        completed += 1
                        break
                    if retries >= self.max_retries:
                        recovery = self.model.choose_recovery(goal, step, result)
                        record(EventType.RECOVERY, {"step": step.description, "strategy": recovery, "retries": retries})
                        message = f"Task failed at step {index}: {verification.summary}"
                        self.store.update_task(goal.task_id, TaskStatus.FAILED, message)
                        record(EventType.TASK_COMPLETED, {"status": TaskStatus.FAILED.value, "error": message})
                        return TaskOutcome(goal.task_id, TaskStatus.FAILED, message, completed, events, message)
                    retries += 1
                    record(EventType.RECOVERY, {"step": step.description, "strategy": "Retrying the same call", "retries": retries})
            except Exception as exc:
                message = f"Execution failed at step {index}: {exc}"
                record(EventType.TOOL_FAILED, {"step": index, "error": str(exc)})
                self.store.update_task(goal.task_id, TaskStatus.FAILED, message)
                record(EventType.TASK_COMPLETED, {"status": TaskStatus.FAILED.value, "error": message})
                return TaskOutcome(goal.task_id, TaskStatus.FAILED, message, completed, events, message)

        summary = f"Completed {completed} of {len(plan.steps[: self.max_steps])} planned steps"
        self.store.update_task(goal.task_id, TaskStatus.SUCCEEDED, summary)
        self.store.add_memory("experience", f"Goal: {goal.text}; outcome: {summary}", utc_now())
        record(EventType.TASK_COMPLETED, {"status": TaskStatus.SUCCEEDED.value, "summary": summary})
        return TaskOutcome(goal.task_id, TaskStatus.SUCCEEDED, summary, completed, events)
