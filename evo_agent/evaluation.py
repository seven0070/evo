from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from .experience import Experience
from .models import OutcomeType


EVALUATOR_VERSION = "evaluation-v1"


@dataclass
class EvaluationResult:
    evaluation_id: str
    experience_id: str
    outcome: OutcomeType
    verified: bool
    success_score: int
    efficiency_metrics: dict[str, Any]
    recovery_metrics: dict[str, Any]
    reliability_metrics: dict[str, Any]
    step_count: int
    retry_count: int
    replan_count: int
    strategy_changes: int
    human_interventions: int
    evaluator_version: str
    explanation: list[str]

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["outcome"] = self.outcome.value
        return data


class EvaluationEngine:
    """Deterministic evaluator; it never replaces or overrides the Verifier."""

    def evaluate(self, experience: Experience) -> EvaluationResult:
        events = experience.execution_steps
        retry_count = sum(1 for item in experience.recovery_attempts if item.get("action") == "retry")
        replan_count = sum(1 for item in experience.recovery_attempts if item.get("action") == "replan") + len([item for item in experience.strategy_changes if item.get("to")])
        strategy_changes = len(experience.strategy_changes)
        failed_tool_calls = len([item for item in experience.failures if item.get("tool") or item.get("step")])
        verified = bool(experience.verification_result.get("success", False))
        human_interventions = len([item for item in experience.approval_events if item.get("reason")])

        success_points = {
            OutcomeType.SUCCESS: 40,
            OutcomeType.PARTIAL_SUCCESS: 20,
            OutcomeType.FAILURE: 0,
            OutcomeType.ABORTED: 0,
            OutcomeType.TIMEOUT: 0,
            OutcomeType.BLOCKED: 0,
        }[experience.final_outcome]
        verification_points = 30 if verified else 0
        recovery_succeeded = bool(experience.final_outcome is OutcomeType.SUCCESS and experience.recovery_attempts)
        if failed_tool_calls == 0:
            reliability_points = 20
        elif recovery_succeeded:
            reliability_points = 10
        else:
            reliability_points = 0
        efficiency_points = max(0, 10 - (retry_count * 2) - (replan_count * 3) - (strategy_changes * 2)) if experience.final_outcome in {OutcomeType.SUCCESS, OutcomeType.PARTIAL_SUCCESS} else 0
        score = min(100, success_points + verification_points + reliability_points + efficiency_points)

        explanation = [
            f"task outcome: {experience.final_outcome.value} ({success_points}/40 success points)",
            f"verification: {'confirmed' if verified else 'not confirmed'} ({verification_points}/30 points)",
            f"reliability: {reliability_points}/20 points with {failed_tool_calls} failed tool call(s)",
            f"efficiency: {efficiency_points}/10 points after {retry_count} retry/retry attempts, {replan_count} replan(s), and {strategy_changes} strategy change(s)",
        ]
        if human_interventions:
            explanation.append(f"human intervention: {human_interventions} approval event(s)")
        if experience.recovery_attempts:
            explanation.append(f"recovery: {len(experience.recovery_attempts)} attempt(s); {'recovered successfully' if recovery_succeeded else 'did not produce a successful final outcome'}")

        return EvaluationResult(
            evaluation_id=f"eval_{experience.experience_id.removeprefix('exp_')}",
            experience_id=experience.experience_id,
            outcome=experience.final_outcome,
            verified=verified,
            success_score=score,
            efficiency_metrics={"step_count": len(events), "duration_ms": experience.duration_ms, "tool_count": len(experience.selected_tools)},
            recovery_metrics={"attempt_count": len(experience.recovery_attempts), "recovery_succeeded": recovery_succeeded, "failed_tool_calls": failed_tool_calls},
            reliability_metrics={"outcome": experience.final_outcome.value, "human_interventions": human_interventions, "approval_event_count": len(experience.approval_events)},
            step_count=len(events),
            retry_count=retry_count,
            replan_count=replan_count,
            strategy_changes=strategy_changes,
            human_interventions=human_interventions,
            evaluator_version=EVALUATOR_VERSION,
            explanation=explanation,
        )
