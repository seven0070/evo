from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from typing import Any

from .model_adapter import ModelAdapter
from .models import Goal, Plan, PlanStep, RiskLevel, ToolResult
from .tools import ToolRegistry


@dataclass
class TaskAssessment:
    complexity: str
    expected_steps: int
    deterministic: bool
    risk: RiskLevel
    reversible: bool
    verification_difficulty: str
    resource_requirement: str
    available_tool_count: int
    reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["risk"] = self.risk.value
        return data


@dataclass
class FlexibilityContext:
    goal: Goal
    assessment: TaskAssessment | None = None
    current_plan: Plan | None = None
    observations: list[str] = field(default_factory=list)
    failures: list[dict[str, Any]] = field(default_factory=list)
    verification_results: list[dict[str, Any]] = field(default_factory=list)
    permissions: dict[str, Any] = field(default_factory=dict)
    constraints: dict[str, Any] = field(default_factory=dict)
    attempt: int = 0


@dataclass
class ToolRecommendation:
    tool_name: str
    score: int
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class AdaptationDecision:
    action: str
    strategy_name: str
    reason: str
    replan: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class Strategy(ABC):
    name: str
    description: str
    strengths: tuple[str, ...]
    weaknesses: tuple[str, ...]

    @abstractmethod
    def applicable(self, assessment: TaskAssessment, context: FlexibilityContext) -> bool:
        raise NotImplementedError

    @abstractmethod
    def create_plan(self, context: FlexibilityContext, model: ModelAdapter) -> Plan:
        raise NotImplementedError

    @abstractmethod
    def recovery_behavior(self, context: FlexibilityContext, failed_step: PlanStep, result: ToolResult) -> AdaptationDecision:
        raise NotImplementedError

    def describe(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "strengths": list(self.strengths),
            "weaknesses": list(self.weaknesses),
        }


class DirectStrategy(Strategy):
    name = "direct"
    description = "Use one straightforward tool action followed by deterministic verification."
    strengths = ("low latency", "simple reasoning", "small execution surface")
    weaknesses = ("poor fit for multi-step work", "limited recovery options")

    def applicable(self, assessment: TaskAssessment, context: FlexibilityContext) -> bool:
        return assessment.deterministic and assessment.expected_steps <= 1 and not context.failures

    def create_plan(self, context: FlexibilityContext, model: ModelAdapter) -> Plan:
        plan = model.create_plan(context.goal, context.constraints.get("tool_schemas", []), self._context_text(context))
        return Plan(plan.task_id, plan.steps[:1], plan.rationale or "Direct strategy selected")

    def recovery_behavior(self, context: FlexibilityContext, failed_step: PlanStep, result: ToolResult) -> AdaptationDecision:
        return AdaptationDecision("retry", self.name, "A direct action may be retried once within the kernel retry limit.")

    @staticmethod
    def _context_text(context: FlexibilityContext) -> str:
        return f"strategy=direct; attempt={context.attempt}; failures={context.failures}"


class PlanFirstStrategy(Strategy):
    name = "plan-first"
    description = "Create and execute a multi-step plan with verification after each step."
    strengths = ("handles multi-step work", "explicit sequencing", "clear observations")
    weaknesses = ("higher latency", "plan can become stale after failures")

    def applicable(self, assessment: TaskAssessment, context: FlexibilityContext) -> bool:
        return assessment.expected_steps > 1 and not context.failures

    def create_plan(self, context: FlexibilityContext, model: ModelAdapter) -> Plan:
        return model.create_plan(context.goal, context.constraints.get("tool_schemas", []), self._context_text(context))

    def recovery_behavior(self, context: FlexibilityContext, failed_step: PlanStep, result: ToolResult) -> AdaptationDecision:
        return AdaptationDecision("replan", self.name, "The plan-first strategy should replan from the latest failure context.", replan=True)

    @staticmethod
    def _context_text(context: FlexibilityContext) -> str:
        return f"strategy=plan-first; attempt={context.attempt}; failures={context.failures}; observations={context.observations}"


class RecoveryStrategy(Strategy):
    name = "recovery"
    description = "Diagnose a failed approach, change the execution strategy, and replan once."
    strengths = ("responds to failure", "avoids blind repetition", "preserves bounded execution")
    weaknesses = ("requires useful failure evidence", "may need a stronger model later")

    def applicable(self, assessment: TaskAssessment, context: FlexibilityContext) -> bool:
        return bool(context.failures)

    def create_plan(self, context: FlexibilityContext, model: ModelAdapter) -> Plan:
        return model.create_plan(context.goal, context.constraints.get("tool_schemas", []), self._context_text(context))

    def recovery_behavior(self, context: FlexibilityContext, failed_step: PlanStep, result: ToolResult) -> AdaptationDecision:
        return AdaptationDecision("replan", self.name, "Switch to a recovery plan once, then stop if the bounded adaptation limit is reached.", replan=True)

    @staticmethod
    def _context_text(context: FlexibilityContext) -> str:
        return f"strategy=recovery; attempt={context.attempt}; diagnose failure before proposing a different plan; failures={context.failures}"


class ApprovalAwareStrategy(PlanFirstStrategy):
    name = "approval-aware"
    description = "Use explicit approval boundaries while executing a plan containing elevated-risk actions."
    strengths = ("makes risk visible", "preserves approval gates", "supports reversible sequencing")
    weaknesses = ("may pause for human decisions", "cannot bypass policy")


class FlexibilityEngine:
    """Runtime decision subsystem. It adapts task execution but never mutates code or policy."""

    def __init__(self, model: ModelAdapter, registry: ToolRegistry):
        self.model = model
        self.registry = registry
        self.strategies: dict[str, Strategy] = {
            strategy.name: strategy
            for strategy in (DirectStrategy(), PlanFirstStrategy(), RecoveryStrategy(), ApprovalAwareStrategy())
        }

    def assess(self, goal: Goal, context: FlexibilityContext | None = None) -> TaskAssessment:
        text = goal.text.lower()
        words = text.split()
        multi_step_markers = (" then ", " and ", "compare", "research", "build", "test", "verify", "workflow")
        risk_markers = ("write", "run", "execute", "shell", "delete", "remove", "send", "deploy", "credential", "financial")
        irreversible_markers = ("delete", "remove", "deploy", "send", "publish")
        deterministic_markers = ("list", "read", "write", "create", "check", "test", "run")
        expected_steps = 1 + sum(text.count(marker) for marker in multi_step_markers)
        expected_steps = max(1, min(expected_steps, 12))
        deterministic = any(marker in text for marker in deterministic_markers) and not any(marker in text for marker in ("research", "analyze", "compare", "decide"))
        risk = RiskLevel.HIGH if any(marker in text for marker in risk_markers) else RiskLevel.LOW
        if any(marker in text for marker in ("credential", "financial", "deploy")):
            risk = RiskLevel.CRITICAL
        reversible = not any(marker in text for marker in irreversible_markers)
        verification_difficulty = "high" if any(marker in text for marker in ("research", "analyze", "decide")) else "low"
        resource_requirement = "high" if len(words) > 80 or any(marker in text for marker in ("large", "many", "batch")) else "low"
        reasons = [f"estimated {expected_steps} step(s)", "deterministic" if deterministic else "judgment or multi-step reasoning likely"]
        if risk is not RiskLevel.LOW:
            reasons.append(f"risk marker detected: {risk.value}")
        if not reversible:
            reasons.append("operation may be difficult to reverse")
        return TaskAssessment(
            complexity="simple" if expected_steps <= 1 else "multi-step",
            expected_steps=expected_steps,
            deterministic=deterministic,
            risk=risk,
            reversible=reversible,
            verification_difficulty=verification_difficulty,
            resource_requirement=resource_requirement,
            available_tool_count=len(self.registry.schemas()),
            reasons=reasons,
        )

    def select_strategy(self, assessment: TaskAssessment, context: FlexibilityContext | None = None) -> Strategy:
        context = context or FlexibilityContext(Goal(""), assessment=assessment)
        if context.failures:
            return self.strategies["recovery"]
        if assessment.risk in {RiskLevel.HIGH, RiskLevel.CRITICAL}:
            return self.strategies["approval-aware"]
        historical = context.constraints.get("historical_experiences", [])
        prior_failures = [item for item in historical if item.get("final_outcome") in {"failure", "blocked", "timeout", "aborted"}]
        if prior_failures and assessment.expected_steps <= 1:
            return self.strategies["plan-first"]
        if self.strategies["direct"].applicable(assessment, context):
            return self.strategies["direct"]
        return self.strategies["plan-first"]

    def select_tools(self, goal: Goal) -> list[ToolRecommendation]:
        text = goal.text.lower()
        recommendations: list[ToolRecommendation] = []
        for schema in self.registry.schemas():
            function = schema["function"]
            name = str(function["name"])
            description = str(function.get("description", "")).lower()
            score = 0
            reason_parts: list[str] = []
            if "list" in text or "files" in text:
                if name == "workspace_list":
                    score += 5
                    reason_parts.append("goal asks to inspect files")
            if "read" in text:
                if name == "workspace_read":
                    score += 5
                    reason_parts.append("goal asks to read content")
            if any(word in text for word in ("write", "create", "save")):
                if name == "workspace_write":
                    score += 5
                    reason_parts.append("goal asks to create or change a file")
            if any(word in text for word in ("shell", "command", "run", "execute", "test")):
                if name == "shell":
                    score += 4
                    reason_parts.append("goal asks for command execution")
            if any(token in description for token in text.split() if len(token) > 3):
                score += 1
            if score:
                recommendations.append(ToolRecommendation(name, score, "; ".join(reason_parts) or "description matched goal"))
        return sorted(recommendations, key=lambda item: (-item.score, item.tool_name))

    def adapt(self, context: FlexibilityContext, failed_step: PlanStep, result: ToolResult) -> AdaptationDecision:
        if context.attempt >= 1:
            return AdaptationDecision("stop", "recovery", "Adaptation limit reached; do not repeat the failed approach.")
        if context.current_plan and context.current_plan.steps:
            current_strategy = self.select_strategy(context.assessment or self.assess(context.goal, context), context)
            decision = current_strategy.recovery_behavior(context, failed_step, result)
            if decision.action == "retry" and failed_step.tool_name == "shell":
                return AdaptationDecision("replan", "recovery", "Shell execution failed; change strategy before retrying.", replan=True)
            return decision
        return AdaptationDecision("replan", "recovery", "No reliable current plan remains; create a recovery plan.", replan=True)

    def recommend_next_action(self, context: FlexibilityContext, failed_step: PlanStep | None = None, result: ToolResult | None = None) -> AdaptationDecision:
        if failed_step is None or result is None:
            strategy = self.select_strategy(context.assessment or self.assess(context.goal, context), context)
            return AdaptationDecision("execute", strategy.name, "Execute the selected strategy.")
        return self.adapt(context, failed_step, result)

    def plan(self, strategy: Strategy, context: FlexibilityContext) -> Plan:
        return strategy.create_plan(context, self.model)
