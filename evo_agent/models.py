from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any
import uuid


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


class RiskLevel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class TaskStatus(str, Enum):
    CREATED = "created"
    PLANNING = "planning"
    RUNNING = "running"
    VERIFYING = "verifying"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    BLOCKED = "blocked"
    CANCELLED = "cancelled"


class OutcomeType(str, Enum):
    SUCCESS = "success"
    PARTIAL_SUCCESS = "partial_success"
    FAILURE = "failure"
    ABORTED = "aborted"
    TIMEOUT = "timeout"
    BLOCKED = "blocked"


class ProposalStatus(str, Enum):
    GENERATED = "generated"
    PENDING_REVIEW = "pending_review"
    APPROVED = "approved"
    REJECTED = "rejected"


class ProposalRisk(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    PROTECTED = "protected"


class EventType(str, Enum):
    TASK_CREATED = "task_created"
    PLAN_CREATED = "plan_created"
    TOOL_REQUESTED = "tool_requested"
    APPROVAL_REQUESTED = "approval_requested"
    APPROVAL_GRANTED = "approval_granted"
    APPROVAL_DENIED = "approval_denied"
    TOOL_COMPLETED = "tool_completed"
    TOOL_FAILED = "tool_failed"
    VERIFICATION = "verification"
    RECOVERY = "recovery"
    TASK_COMPLETED = "task_completed"
    STRATEGY_SELECTED = "strategy_selected"
    TOOL_RECOMMENDED = "tool_recommended"
    STRATEGY_FAILED = "strategy_failed"
    ADAPTATION_TRIGGERED = "adaptation_triggered"
    STRATEGY_CHANGED = "strategy_changed"
    REPLAN_TRIGGERED = "replan_triggered"
    RECOVERY_ATTEMPTED = "recovery_attempted"
    EXPERIENCE_CREATED = "experience_created"
    EXPERIENCE_RETRIEVED = "experience_retrieved"
    EVALUATION_STARTED = "evaluation_started"
    EVALUATION_COMPLETED = "evaluation_completed"
    EVALUATION_FAILED = "evaluation_failed"
    EVOLUTION_ANALYSIS_STARTED = "evolution_analysis_started"
    WEAKNESS_DETECTED = "weakness_detected"
    EVOLUTION_OPPORTUNITY_DETECTED = "evolution_opportunity_detected"
    PROPOSAL_GENERATED = "proposal_generated"
    PROPOSAL_VALIDATED = "proposal_validated"
    PROPOSAL_REJECTED = "proposal_rejected"
    PROPOSAL_APPROVED = "proposal_approved"


@dataclass
class Goal:
    text: str
    task_id: str = field(default_factory=lambda: new_id("task"))
    created_at: str = field(default_factory=utc_now)


@dataclass
class PlanStep:
    step_id: str
    description: str
    tool_name: str | None = None
    arguments: dict[str, Any] = field(default_factory=dict)
    risk: RiskLevel = RiskLevel.LOW
    verification: str | None = None
    status: str = "pending"


@dataclass
class Plan:
    task_id: str
    steps: list[PlanStep]
    rationale: str = ""
    created_at: str = field(default_factory=utc_now)


@dataclass
class ToolCall:
    call_id: str = field(default_factory=lambda: new_id("call"))
    task_id: str = ""
    step_id: str = ""
    tool_name: str = ""
    arguments: dict[str, Any] = field(default_factory=dict)
    risk: RiskLevel = RiskLevel.LOW
    approved: bool = False


@dataclass
class ToolResult:
    call_id: str
    tool_name: str
    success: bool
    output: str = ""
    error: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class VerificationResult:
    success: bool
    summary: str
    checks: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class Event:
    task_id: str
    event_type: EventType
    payload: dict[str, Any] = field(default_factory=dict)
    event_id: str = field(default_factory=lambda: new_id("evt"))
    created_at: str = field(default_factory=utc_now)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["event_type"] = self.event_type.value
        return data


@dataclass
class TaskOutcome:
    task_id: str
    status: TaskStatus
    summary: str
    steps_completed: int
    events: list[Event] = field(default_factory=list)
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "status": self.status.value,
            "summary": self.summary,
            "steps_completed": self.steps_completed,
            "error": self.error,
            "events": [event.to_dict() for event in self.events],
        }
