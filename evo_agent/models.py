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


class PromotionApprovalStatus(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class PromotionEligibilityStatus(str, Enum):
    UNKNOWN = "unknown"
    ELIGIBLE = "eligible"
    REJECTED = "rejected"


class PromotionStatus(str, Enum):
    REQUESTED = "requested"
    ELIGIBILITY_CHECK = "eligibility_check"
    APPROVED = "approved"
    CHECKPOINT_CREATED = "checkpoint_created"
    STAGED = "staged"
    INTEGRITY_VERIFIED = "integrity_verified"
    ACTIVATING = "activating"
    HEALTH_CHECK = "health_check"
    ACTIVE = "active"
    ROLLING_BACK = "rolling_back"
    ROLLED_BACK = "rolled_back"
    REJECTED = "rejected"
    FAILED = "failed"


class VersionStatus(str, Enum):
    CANDIDATE = "candidate"
    ACTIVE = "active"
    PREVIOUS = "previous"
    ROLLED_BACK = "rolled_back"
    RETIRED = "retired"
    INVALID = "invalid"


class CandidateStatus(str, Enum):
    CREATED = "created"
    PREPARED = "prepared"
    RUNNING = "running"
    PASSED = "passed"
    FAILED = "failed"
    ABORTED = "aborted"
    DESTROYED = "destroyed"


class ExperimentStatus(str, Enum):
    CREATED = "created"
    PREPARED = "prepared"
    RUNNING = "running"
    PASSED = "passed"
    FAILED = "failed"
    TIMEOUT = "timeout"
    ABORTED = "aborted"
    DESTROYED = "destroyed"


class ComparisonClass(str, Enum):
    BETTER = "better"
    NO_CHANGE = "no_change"
    WORSE = "worse"
    INCONCLUSIVE = "inconclusive"


class StructuralChangeType(str, Enum):
    ADD_COMPONENT = "add_component"
    REMOVE_COMPONENT = "remove_component"
    REPLACE_COMPONENT = "replace_component"
    UPGRADE_COMPONENT = "upgrade_component"
    ADD_CAPABILITY = "add_capability"
    REMOVE_CAPABILITY = "remove_capability"
    REWIRE_DEPENDENCY = "rewire_dependency"
    CHANGE_CONFIGURATION = "change_configuration"


class MetamorphosisStatus(str, Enum):
    PROPOSED = "proposed"
    VALIDATED = "validated"
    PENDING_APPROVAL = "pending_approval"
    APPROVED = "approved"
    SANDBOXED = "sandboxed"
    COMPATIBLE = "compatible"
    BENCHMARKED = "benchmarked"
    EVALUATED = "evaluated"
    BETTER = "better"
    PENDING_PROMOTION = "pending_promotion"
    PROMOTED = "promoted"
    REJECTED = "rejected"
    INCOMPATIBLE = "incompatible"
    WORSE = "worse"
    INCONCLUSIVE = "inconclusive"
    ROLLED_BACK = "rolled_back"


class CompatibilityStatus(str, Enum):
    COMPATIBLE = "compatible"
    INCOMPATIBLE = "incompatible"
    INCONCLUSIVE = "inconclusive"


class ComponentStatus(str, Enum):
    ACTIVE = "active"
    CANDIDATE = "candidate"
    DEPRECATED = "deprecated"
    REMOVED = "removed"


class CapabilityStatus(str, Enum):
    ACTIVE = "active"
    CANDIDATE = "candidate"
    DEPRECATED = "deprecated"
    REMOVED = "removed"


class OrchestrationPath(str, Enum):
    NO_CHANGE = "no_change"
    FLEXIBILITY = "flexibility"
    EVOLUTION = "evolution"
    METAMORPHOSIS = "metamorphosis"
    INCONCLUSIVE = "inconclusive"


class OpportunityStatus(str, Enum):
    DETECTED = "detected"
    ANALYZING = "analyzing"
    CLASSIFIED = "classified"
    IGNORED = "ignored"
    QUEUED = "queued"
    PROPOSED = "proposed"
    COMPLETED = "completed"


class WorkItemState(str, Enum):
    DETECTED = "detected"
    ANALYZING = "analyzing"
    CLASSIFIED = "classified"
    QUEUED = "queued"
    PROPOSED = "proposed"
    AWAITING_APPROVAL = "awaiting_approval"
    APPROVED = "approved"
    SANDBOXING = "sandboxing"
    BENCHMARKING = "benchmarking"
    EVALUATING = "evaluating"
    DECIDED = "decided"
    BETTER = "better"
    AWAITING_PROMOTION_APPROVAL = "awaiting_promotion_approval"
    PROMOTION_APPROVED = "promotion_approved"
    PROMOTING = "promoting"
    HEALTH_CHECK = "health_check"
    COMPLETED = "completed"
    REJECTED = "rejected"
    INCONCLUSIVE = "inconclusive"
    FAILED = "failed"
    ROLLED_BACK = "rolled_back"
    BLOCKED = "blocked"
    CANCELLED = "cancelled"


class ApprovalType(str, Enum):
    EVOLUTION = "evolution_approval"
    METAMORPHOSIS = "metamorphosis_approval"
    PROMOTION = "promotion_approval"


class QueueItemStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    TIMED_OUT = "timed_out"


class DeduplicationStatus(str, Enum):
    NEW = "new"
    DUPLICATE = "duplicate"
    SIMILAR = "similar"
    SUPERSEDED = "superseded"


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
    SANDBOX_CREATED = "sandbox_created"
    BASELINE_SNAPSHOT_CREATED = "baseline_snapshot_created"
    CANDIDATE_CREATED = "candidate_created"
    PROPOSAL_APPLIED = "proposal_applied"
    CANDIDATE_STARTED = "candidate_started"
    CANDIDATE_TEST_STARTED = "candidate_test_started"
    CANDIDATE_TEST_COMPLETED = "candidate_test_completed"
    CANDIDATE_FAILED = "candidate_failed"
    CANDIDATE_PASSED = "candidate_passed"
    SANDBOX_CLEANUP_STARTED = "sandbox_cleanup_started"
    SANDBOX_DESTROYED = "sandbox_destroyed"
    SANDBOX_ABORTED = "sandbox_aborted"
    PROMOTION_REQUESTED = "promotion_requested"
    PROMOTION_ELIGIBILITY_CHECKED = "promotion_eligibility_checked"
    PROMOTION_APPROVED = "promotion_approved"
    PROMOTION_REJECTED = "promotion_rejected"
    PROMOTION_CHECKPOINT_CREATED = "promotion_checkpoint_created"
    CANDIDATE_STAGED = "candidate_staged"
    CANDIDATE_INTEGRITY_VERIFIED = "candidate_integrity_verified"
    PROMOTION_STARTED = "promotion_started"
    PRODUCTION_VERSION_ACTIVATED = "production_version_activated"
    POST_PROMOTION_HEALTH_CHECK = "post_promotion_health_check"
    PROMOTION_COMPLETED = "promotion_completed"
    PROMOTION_FAILED = "promotion_failed"
    ROLLBACK_STARTED = "rollback_started"
    ROLLBACK_CHECKPOINT_RESTORED = "rollback_checkpoint_restored"
    ROLLBACK_VERIFIED = "rollback_verified"
    ROLLBACK_COMPLETED = "rollback_completed"
    METAMORPHOSIS_PROPOSED = "metamorphosis_proposed"
    METAMORPHOSIS_VALIDATED = "metamorphosis_validated"
    METAMORPHOSIS_REJECTED = "metamorphosis_rejected"
    METAMORPHOSIS_APPROVED = "metamorphosis_approved"
    ARCHITECTURE_ANALYZED = "architecture_analyzed"
    COMPATIBILITY_CHECKED = "compatibility_checked"
    MIGRATION_PLANNED = "migration_planned"
    STRUCTURAL_CANDIDATE_CREATED = "structural_candidate_created"
    STRUCTURAL_CANDIDATE_TESTED = "structural_candidate_tested"
    CAPABILITY_REGRESSION_DETECTED = "capability_regression_detected"
    STRUCTURAL_REGRESSION_DETECTED = "structural_regression_detected"
    METAMORPHOSIS_EVALUATED = "metamorphosis_evaluated"
    METAMORPHOSIS_PROMOTED = "metamorphosis_promoted"
    METAMORPHOSIS_ROLLED_BACK = "metamorphosis_rolled_back"
    BENCHMARK_CREATED = "benchmark_created"
    BENCHMARK_VALIDATED = "benchmark_validated"
    BENCHMARK_STARTED = "benchmark_started"
    TRIAL_STARTED = "trial_started"
    TRIAL_COMPLETED = "trial_completed"
    BASELINE_COMPLETED = "baseline_completed"
    CANDIDATE_COMPLETED = "candidate_completed"
    REGRESSION_DETECTED = "regression_detected"
    SAFETY_REGRESSION_DETECTED = "safety_regression_detected"
    BENCHMARK_COMPLETED = "benchmark_completed"
    EVIDENCE_GENERATED = "evidence_generated"
    EVOLUTION_DECISION_MADE = "evolution_decision_made"
    OPPORTUNITY_DETECTED = "opportunity_detected"
    OPPORTUNITY_CLASSIFIED = "opportunity_classified"
    CHANGE_PATH_SELECTED = "change_path_selected"
    WORK_ITEM_CREATED = "work_item_created"
    APPROVAL_RECEIVED = "approval_received"
    APPROVAL_REJECTED = "approval_rejected"
    EXPERIMENT_QUEUED = "experiment_queued"
    EXPERIMENT_STARTED = "experiment_started"
    EXPERIMENT_COMPLETED = "experiment_completed"
    BENCHMARK_QUEUED = "benchmark_queued"
    EVIDENCE_RECEIVED = "evidence_received"
    DECISION_RECEIVED = "decision_received"
    PROMOTION_QUEUED = "promotion_queued"
    WORK_ITEM_COMPLETED = "work_item_completed"
    WORK_ITEM_FAILED = "work_item_failed"
    WORK_ITEM_RESUMED = "work_item_resumed"
    WORK_ITEM_CANCELLED = "work_item_cancelled"


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
