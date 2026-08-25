from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
import time
from typing import Any

from .checkpoints import CheckpointManager
from .evaluation import EvaluationEngine
from .experience import ExperienceEngine
from .flexibility import FlexibilityContext, FlexibilityEngine
from .model_adapter import ModelAdapter
from .models import Event, EventType, Goal, Plan, PlanStep, RiskLevel, TaskOutcome, TaskStatus, ToolCall, utc_now
from .security import SecurityPolicy
from .storage import SQLiteStore
from .tools import ToolRegistry
from .verifier import Verifier
from .version import __version__

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
        agent_version: str = __version__,
        external_integrations: Any | None = None,
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
        self.experience_engine = ExperienceEngine(self.store)
        self.evaluation_engine = EvaluationEngine()
        self.agent_version = agent_version
        self.model_identifier = str(getattr(model, "model", model.__class__.__name__))
        self.approval_callback = approval_callback or (lambda call, reason: False)
        self.max_steps = max_steps
        self.max_retries = max_retries
        self.max_adaptations = max_adaptations
        from .capability import CapabilityIntelligence
        self.capability_intelligence = CapabilityIntelligence(self.store, self.workspace, self.tools, self.policy)
        self.external_integrations = external_integrations
        if self.external_integrations is not None:
            self.external_integrations.capability_intelligence = self.capability_intelligence
            if getattr(self.external_integrations, "memory", None) is None:
                from .memory import MemoryManager
                self.external_integrations.memory = MemoryManager(self.store, self.workspace)
        self.world_intelligence = None

    def run_external_operation(self, operation_id: str, payload: dict[str, Any] | None = None) -> Any:
        """Execute an already-modeled external operation through the Kernel boundary."""
        if self.external_integrations is None:
            raise RuntimeError("external integration manager is not configured")
        operation_row = self.store.integration_operation_by_id(operation_id)
        if not operation_row:
            raise KeyError(operation_id)
        from .external import ExternalOperationRisk
        # Decode through the manager-owned persistence helper rather than accepting
        # caller-supplied execution authority or connector objects.
        from .external import integration_operation_from_row
        operation, _ = integration_operation_from_row(operation_row)
        risk = RiskLevel.CRITICAL if operation.risk_level is ExternalOperationRisk.DESTRUCTIVE else RiskLevel.HIGH if operation.risk_level.requires_approval else RiskLevel.LOW
        call = ToolCall(task_id=operation_id, step_id=operation.operation, tool_name=f"external:{operation.integration_id}:{operation.operation}", arguments={"target": operation.target, "operation": operation.operation}, risk=risk)
        return self.external_integrations.execute_operation(operation_id, payload=payload, approval_callback=lambda item: self.approval_callback(call, f"External operation {item.operation} requires approval"), actor="kernel")

    execute_external_operation = run_external_operation

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
            world = self._get_world_intelligence()
            world_model = world.observe(goal.text)
            world_snapshot = world.create_snapshot(world_model)
            world.save_observations(world_model)
            record(EventType.ENVIRONMENT_OBSERVED, {"environment_id": world_model.environment.environment_id, "environment_version": world_model.environment.environment_version, "snapshot_id": world_snapshot.snapshot_id})
            context = FlexibilityContext(
                goal=goal,
                permissions={"approval_required_for": [level.value for level in self.policy.approval_required_for]},
                constraints={"tool_schemas": self.tools.schemas(), "max_steps": self.max_steps, "max_retries": self.max_retries},
            )
            context.assessment = self.flexibility.assess(goal, context)
            historical = self.experience_engine.retrieve(goal=goal.text, limit=5)
            context.constraints["historical_experiences"] = [item.to_dict() for item in historical]
            capability_analysis = self.capability_intelligence.analyze_goal(goal.text, architecture_version=self._architecture_version())
            context.constraints["capability_analysis"] = [item.to_dict() for item in capability_analysis]
            for item in capability_analysis:
                record(EventType.CAPABILITY_SELECTED, {"analysis": item.to_dict(), "selected_tool": item.selection.selected_tool.name if item.selection.selected_tool else None})

            if historical:
                record(EventType.EXPERIENCE_RETRIEVED, {"count": len(historical), "experience_ids": [item.experience_id for item in historical]})
            record(EventType.PLAN_CREATED, {"checkpoint": str(checkpoint), "assessment": context.assessment.to_dict(), "memories": memories, "historical_experiences": context.constraints["historical_experiences"]})
            recommendations = self.flexibility.select_tools(goal)
            for recommendation in recommendations:
                record(EventType.TOOL_RECOMMENDED, recommendation.to_dict())
            strategy = self.flexibility.select_strategy(context.assessment, context)
            record(EventType.STRATEGY_SELECTED, {"strategy": strategy.describe(), "assessment": context.assessment.to_dict(), "historical_experience_count": len(historical)})
            plan = self.flexibility.plan(strategy, context)
            context.current_plan = plan
            record(EventType.PLAN_CREATED, {"strategy": strategy.name, "rationale": plan.rationale, "steps": [step.__dict__ for step in plan.steps]})
        except Exception as exc:
            message = f"Planning failed: {exc}"
            self.store.update_task(goal.task_id, TaskStatus.FAILED, message)
            record(EventType.TASK_COMPLETED, {"status": TaskStatus.FAILED.value, "error": message})
            return self._finalize(TaskOutcome(goal.task_id, TaskStatus.FAILED, message, 0, events, message), record)

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
                record(EventType.TOOL_PERMISSION_CHECKED, {"tool": call.tool_name, "declared_permissions": getattr(spec, "permissions", []), "approval_required": self.policy.requires_approval(call), "authority": "Kernel SecurityPolicy"})
                if self.policy.requires_approval(call):
                    reason = f"'{call.tool_name}' is classified as {call.risk.value}-risk"
                    record(EventType.APPROVAL_REQUESTED, {"tool": call.tool_name, "reason": reason})
                    if not self.approval_callback(call, reason):
                        record(EventType.APPROVAL_DENIED, {"tool": call.tool_name, "reason": reason})
                        message = f"Task blocked: approval denied for {call.tool_name}"
                        self.store.update_task(goal.task_id, TaskStatus.BLOCKED, message)
                        return self._finalize(TaskOutcome(goal.task_id, TaskStatus.BLOCKED, message, completed, events, message), record)
                    call.approved = True
                    record(EventType.APPROVAL_GRANTED, {"tool": call.tool_name})

                retries = 0
                step_finished = False
                while not step_finished:
                    input_errors = self.capability_intelligence.tools.validate_input(call.tool_name, call.arguments)
                    if input_errors:
                        record(EventType.TOOL_REJECTED, {"tool": call.tool_name, "reason": "; ".join(input_errors), "stage": "input_schema"})
                        result = ToolResult(call.call_id, call.tool_name, False, error="Tool input rejected: " + "; ".join(input_errors))
                    else:
                        record(EventType.TOOL_EXECUTION_STARTED, {"tool": call.tool_name, "step": executed_steps})
                        started_tool = time.monotonic()
                        result = self.tools.execute(call)
                        if result.success:
                            output_errors = self.capability_intelligence.tools.validate_output(call.tool_name, result.output)
                            if output_errors:
                                result.success = False
                                result.error = "Tool output rejected: " + "; ".join(output_errors)
                                result.metadata["output_schema_errors"] = output_errors
                        self.capability_intelligence.record_tool_outcome(call.tool_name, result.success, time.monotonic() - started_tool, "timeout" in (result.error or "").lower(), result.error or "", goal.task_id)
                    if result.success:
                        record(EventType.TOOL_EXECUTION_COMPLETED, {"tool": call.tool_name, "output_size": len(result.output)})
                        record(EventType.TOOL_COMPLETED, {"tool": result.tool_name, "output": result.output, "metadata": result.metadata})
                    else:
                        record(EventType.TOOL_EXECUTION_FAILED, {"tool": result.tool_name, "error": result.error, "metadata": result.metadata})
                        record(EventType.TOOL_FAILED, {"tool": result.tool_name, "error": result.error, "output": result.output})
                        record(EventType.STRATEGY_FAILED, {"strategy": strategy.name, "step": step.description, "error": result.error})
                    verification = self.verifier.verify(step, result)
                    context.observations.append(result.output[-1000:] if result.output else (result.error or "no output"))
                    context.verification_results.append({"step": step.description, "success": verification.success, "checks": verification.checks})
                    record(EventType.VERIFICATION, {"step": step.description, "success": verification.success, "summary": verification.summary, "checks": verification.checks})
                    try:
                        current_world = self._get_world_intelligence().update_after_action(goal=goal.text)
                        record(EventType.WORLD_STATE_UPDATED, {"environment_id": current_world.environment.environment_id, "environment_version": current_world.environment.environment_version, "observation_count": len(current_world.observations)})
                    except Exception as world_error:
                        record(EventType.WORLD_SURPRISE, {"reason": "world observation failed safely", "error": type(world_error).__name__})
                    if verification.success:
                        completed += 1
                        step_finished = True
                        continue

                    context.failures.append({"step": step.description, "tool": step.tool_name, "error": result.error, "verification": verification.summary})
                    fallback = self.capability_intelligence.fallback_for(goal.text, step, [step.tool_name] if step.tool_name else [], self._architecture_version())
                    context.constraints["capability_fallbacks"] = [item.to_dict() for item in fallback]
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
                    return self._finalize(TaskOutcome(goal.task_id, TaskStatus.FAILED, message, completed, events, message), record)
            except Exception as exc:
                message = f"Execution failed at step {executed_steps}: {exc}"
                record(EventType.TOOL_FAILED, {"step": executed_steps, "error": str(exc)})
                self.store.update_task(goal.task_id, TaskStatus.FAILED, message)
                record(EventType.TASK_COMPLETED, {"status": TaskStatus.FAILED.value, "error": message})
                return self._finalize(TaskOutcome(goal.task_id, TaskStatus.FAILED, message, completed, events, message), record)

        summary = f"Completed {completed} step(s) after {adaptations} adaptation(s)"
        self.store.update_task(goal.task_id, TaskStatus.SUCCEEDED, summary)
        self.store.add_memory("experience", f"Goal: {goal.text}; outcome: {summary}; strategy: {strategy.name}", utc_now())
        record(EventType.TASK_COMPLETED, {"status": TaskStatus.SUCCEEDED.value, "summary": summary, "strategy": strategy.name, "adaptations": adaptations})
        return self._finalize(TaskOutcome(goal.task_id, TaskStatus.SUCCEEDED, summary, completed, events), record)

    def _get_world_intelligence(self) -> Any:
        if self.world_intelligence is None:
            from .world import EnvironmentObserver, WorldModelEngine, WorldRefreshEngine
            observer = EnvironmentObserver(self.workspace, self.store, self.capability_intelligence, self.policy, self.agent_version, self._architecture_version())
            self.world_intelligence = WorldModelEngine(self.store, observer, WorldRefreshEngine(observer, self.store))
        return self.world_intelligence

    def _architecture_version(self) -> str:
        return ""

    def _finalize(self, outcome: TaskOutcome, record: Callable[[EventType, dict[str, Any]], None]) -> TaskOutcome:
        try:
            experience = self.experience_engine.create(outcome, self.agent_version, self.model_identifier)
            world = self._get_world_intelligence()
            if world.current:
                experience.environment_id = world.current.environment.environment_id
                experience.environment_version = world.current.environment.environment_version
                experience.architecture_version = self._architecture_version()
                experience.resource_conditions = dict(world.current.environment.resource_state)
            self.experience_engine.persist(experience)
            record(EventType.EXPERIENCE_CREATED, {"experience_id": experience.experience_id, "outcome": experience.final_outcome.value, "agent_version": experience.agent_version})
            record(EventType.EVALUATION_STARTED, {"experience_id": experience.experience_id})
            evaluation = self.evaluation_engine.evaluate(experience)
            self.store.save_evaluation(evaluation)
            self.store.update_experience_evaluation(experience.experience_id, evaluation.evaluation_id, evaluation.to_dict())
            from .memory import MemoryManager
            memory = MemoryManager(self.store, self.workspace)
            memory.capture_experience(experience)
            memory.capture_evaluation(evaluation)
            if world.current:
                memory.capture_environment(world.current.environment, task_id=outcome.task_id, goal=experience.original_goal, outcome=experience.final_outcome.value)
            for event in outcome.events:
                if event.event_type in {EventType.TOOL_COMPLETED, EventType.TOOL_FAILED}:
                    memory.capture_observation({"observation_id": event.event_id, "task_id": outcome.task_id, "tool": event.payload.get("tool"), "output": event.payload.get("output", ""), "errors": event.payload.get("error", ""), "status": "succeeded" if event.event_type is EventType.TOOL_COMPLETED else "failed"})
            record(EventType.EVALUATION_COMPLETED, {"evaluation_id": evaluation.evaluation_id, "experience_id": experience.experience_id, "success_score": evaluation.success_score, "evaluator_version": evaluation.evaluator_version})
        except Exception as exc:
            record(EventType.EVALUATION_FAILED, {"task_id": outcome.task_id, "error": str(exc)})
        return outcome
