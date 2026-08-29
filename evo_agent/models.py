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
    CAPABILITY_REQUIRED = "capability_required"
    CAPABILITY_DISCOVERED = "capability_discovered"
    TOOL_CANDIDATE = "tool_candidate"
    TOOL_SELECTED = "tool_selected"
    TOOL_REJECTED = "tool_rejected"
    TOOL_PERMISSION_CHECKED = "tool_permission_checked"
    TOOL_EXECUTION_STARTED = "tool_execution_started"
    TOOL_EXECUTION_COMPLETED = "tool_execution_completed"
    TOOL_EXECUTION_FAILED = "tool_execution_failed"
    TOOL_HEALTH_CHANGED = "tool_health_changed"
    TOOL_FALLBACK = "tool_fallback"
    CAPABILITY_GAP_DETECTED = "capability_gap_detected"
    CAPABILITY_SATISFIED = "capability_satisfied"
    ENVIRONMENT_OBSERVED = "environment_observed"
    ENVIRONMENT_SNAPSHOT_CREATED = "environment_snapshot_created"
    ENVIRONMENT_DIFF = "environment_diff"
    ENVIRONMENT_CHANGED = "environment_changed"
    ENVIRONMENT_RESOURCE_CHANGED = "environment_resource_changed"
    ENVIRONMENT_TOOL_CHANGED = "environment_tool_changed"
    ENVIRONMENT_CAPABILITY_CHANGED = "environment_capability_changed"
    ENVIRONMENT_PROVIDER_CHANGED = "environment_provider_changed"
    ENVIRONMENT_CONSTRAINT_CHANGED = "environment_constraint_changed"
    ENVIRONMENT_HEALTH_CHANGED = "environment_health_changed"
    WORLD_OBSERVATION = "world_observation"
    WORLD_STATE_UPDATED = "world_state_updated"
    WORLD_CONFLICT = "world_conflict"
    WORLD_ASSUMPTION_CREATED = "world_assumption_created"
    WORLD_ASSUMPTION_INVALIDATED = "world_assumption_invalidated"
    WORLD_REFRESH = "world_refresh"
    WORLD_SURPRISE = "world_surprise"
    WORLD_PREDICTION = "world_prediction"
    PLAN_INVALIDATED = "plan_invalidated"
    PROVIDER_STATE_CHANGED = "provider_state_changed"
    RESOURCE_STATE_CHANGED = "resource_state_changed"
    FILESYSTEM_STATE_CHANGED = "filesystem_state_changed"
    EXTERNAL_INTEGRATION_REGISTERED = "external_integration_registered"
    EXTERNAL_POLICY_UPDATED = "external_policy_updated"
    EXTERNAL_CONNECTOR_HEALTH = "external_connector_health"
    EXTERNAL_OPERATION_REQUESTED = "external_operation_requested"
    EXTERNAL_OPERATION_STARTED = "external_operation_started"
    EXTERNAL_OPERATION_COMPLETED = "external_operation_completed"
    EXTERNAL_OPERATION_FAILED = "external_operation_failed"
    EXTERNAL_OPERATION_BLOCKED = "external_operation_blocked"
    EXTERNAL_APPROVAL_REQUESTED = "external_approval_requested"
    EXTERNAL_APPROVAL_RECEIVED = "external_approval_received"
    EXTERNAL_DUPLICATE_PREVENTED = "external_duplicate_prevented"
    EXTERNAL_OBSERVATION_RECORDED = "external_observation_recorded"
    EXTERNAL_CHANGE_DETECTED = "external_change_detected"
    EXTERNAL_CONTENT_UNTRUSTED = "external_content_untrusted"
    EXTERNAL_COMMUNICATION_RECORDED = "external_communication_recorded"
    CAPABILITY_SELECTED = "capability_selected"
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
    #: The capability substrate an agent actually resolved (P3). A pair, not one event, because
    #: "what is loaded" and "what its digest must be" answer different questions: the first is read
    #: by anyone asking why behaviour changed, the second is compared by anyone checking that
    #: activation delivered what promotion claimed.
    OVERLAY_RESOLVED = "overlay_resolved"
    ACTIVE_CAPABILITIES_DIGEST = "active_capabilities_digest"
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
    RUNTIME_STARTED = "runtime_started"
    RUNTIME_READY = "runtime_ready"
    RUNTIME_STATE_CHANGED = "runtime_state_changed"
    RUNTIME_HEARTBEAT = "runtime_heartbeat"
    RUNTIME_TASK_QUEUED = "runtime_task_queued"
    RUNTIME_TASK_STARTED = "runtime_task_started"
    RUNTIME_TASK_WAITING = "runtime_task_waiting"
    RUNTIME_TASK_COMPLETED = "runtime_task_completed"
    RUNTIME_TASK_FAILED = "runtime_task_failed"
    RUNTIME_TASK_CANCELLED = "runtime_task_cancelled"
    RUNTIME_TASK_PAUSED = "runtime_task_paused"
    RUNTIME_TASK_RESUMED = "runtime_task_resumed"
    RUNTIME_RECOVERY = "runtime_recovery"
    RUNTIME_REPLAN = "runtime_replan"
    RUNTIME_CIRCUIT_BREAKER = "runtime_circuit_breaker"
    RUNTIME_DEGRADED = "runtime_degraded"
    RUNTIME_SAFE_MODE = "runtime_safe_mode"
    RUNTIME_SHUTDOWN = "runtime_shutdown"
    RUNTIME_CRASH_RECOVERY = "runtime_crash_recovery"
    RUNTIME_KILL_SWITCH = "runtime_kill_switch"
    RUNTIME_APPROVAL_RECEIVED = "runtime_approval_received"
    SPECIALIST_REGISTERED = "specialist_registered"
    SPECIALIST_HEALTH_CHANGED = "specialist_health_changed"
    SPECIALIST_TASK_CONTRACT_CREATED = "specialist_task_contract_created"
    SPECIALIST_TASK_QUEUED = "specialist_task_queued"
    SPECIALIST_TASK_STARTED = "specialist_task_started"
    SPECIALIST_TASK_COMPLETED = "specialist_task_completed"
    SPECIALIST_TASK_FAILED = "specialist_task_failed"
    SPECIALIST_TASK_BLOCKED = "specialist_task_blocked"
    SPECIALIST_TASK_CANCELLED = "specialist_task_cancelled"
    SPECIALIST_TASK_RECOVERED = "specialist_task_recovered"
    SPECIALIST_MESSAGE_SENT = "specialist_message_sent"
    SPECIALIST_MESSAGE_REJECTED = "specialist_message_rejected"
    SPECIALIST_RESULT_COLLECTED = "specialist_result_collected"
    SPECIALIST_EVIDENCE_COLLECTED = "specialist_evidence_collected"
    SPECIALIST_EVIDENCE_VERIFIED = "specialist_evidence_verified"
    DELEGATION_STARTED = "delegation_started"
    DELEGATION_COMPLETED = "delegation_completed"
    DELEGATION_FAILED = "delegation_failed"
    DELEGATION_LIMIT_REACHED = "delegation_limit_reached"
    SPECIALIST_CONFLICT_DETECTED = "specialist_conflict_detected"
    SPECIALIST_CONFLICT_RESOLVED = "specialist_conflict_resolved"
    SPECIALIST_VERIFICATION_REQUIRED = "specialist_verification_required"
    SPECIALIST_CONTEXT_BUILT = "specialist_context_built"
    SPECIALIST_CONTEXT_BLOCKED = "specialist_context_blocked"
    SPECIALIST_PERMISSION_CHECKED = "specialist_permission_checked"
    SPECIALIST_EXTERNAL_BLOCKED = "specialist_external_blocked"
    MODEL_PROVIDER_REGISTERED = "model_provider_registered"
    MODEL_REGISTERED = "model_registered"
    MODEL_DISCOVERED = "model_discovered"
    MODEL_REQUEST_VALIDATED = "model_request_validated"
    MODEL_REQUEST_BLOCKED = "model_request_blocked"
    MODEL_INFERENCE_STARTED = "model_inference_started"
    MODEL_INFERENCE_COMPLETED = "model_inference_completed"
    MODEL_INFERENCE_FAILED = "model_inference_failed"
    MODEL_FALLBACK_SELECTED = "model_fallback_selected"
    MODEL_HEALTH_CHANGED = "model_health_changed"
    MODEL_CIRCUIT_OPENED = "model_circuit_opened"
    MODEL_SELECTION_RECORDED = "model_selection_recorded"
    MODEL_EVALUATION_STARTED = "model_evaluation_started"
    MODEL_EVALUATION_COMPLETED = "model_evaluation_completed"
    MODEL_TRIAL_COMPLETED = "model_trial_completed"
    MODEL_COMPARISON_COMPLETED = "model_comparison_completed"
    LEARNING_OBSERVATION_RECORDED = "learning_observation_recorded"
    LEARNING_OUTCOME_RECORDED = "learning_outcome_recorded"
    LEARNING_ADJUSTMENT_PROPOSED = "learning_adjustment_proposed"
    LEARNING_ADJUSTMENT_APPLIED = "learning_adjustment_applied"
    LEARNING_ADJUSTMENT_ROLLED_BACK = "learning_adjustment_rolled_back"
    LEARNING_ADJUSTMENT_BLOCKED = "learning_adjustment_blocked"
    MODEL_CONTEXT_BUILT = "model_context_built"
    MODEL_EXPLORATION_RECORDED = "model_exploration_recorded"
    MODEL_EVOLUTION_EVIDENCE = "model_evolution_evidence"
    LEARNING_CYCLE_STARTED = "learning_cycle_started"
    LEARNING_CYCLE_COMPLETED = "learning_cycle_completed"
    LEARNING_CYCLE_BLOCKED = "learning_cycle_blocked"
    LEARNING_PATTERN_DETECTED = "learning_pattern_detected"
    LEARNING_HYPOTHESIS_CREATED = "learning_hypothesis_created"
    ADAPTIVE_POLICY_CREATED = "adaptive_policy_created"
    ADAPTIVE_POLICY_UPDATED = "adaptive_policy_updated"
    ADAPTIVE_ADJUSTMENT_PROPOSED = "adaptive_adjustment_proposed"
    ADAPTIVE_ADJUSTMENT_APPLIED = "adaptive_adjustment_applied"
    ADAPTIVE_ADJUSTMENT_BLOCKED = "adaptive_adjustment_blocked"
    ADJUSTMENT_EVALUATED = "adjustment_evaluated"
    LEARNING_FEEDBACK_RECORDED = "learning_feedback_recorded"
    COUNTERFACTUAL_EVALUATED = "counterfactual_evaluated"
    LEARNING_CONFLICT_DETECTED = "learning_conflict_detected"
    LEARNING_ROLLBACK_STARTED = "learning_rollback_started"
    LEARNING_ROLLBACK_COMPLETED = "learning_rollback_completed"
    LEARNING_DECAY_APPLIED = "learning_decay_applied"
    LEARNING_EVOLUTION_EVIDENCE = "learning_evolution_evidence"
    SELF_MODEL_REFRESHED = "self_model_refreshed"
    SELF_MODEL_CLAIM_RECORDED = "self_model_claim_recorded"
    SELF_MODEL_LIMITATION_RECORDED = "self_model_limitation_recorded"
    SELF_MODEL_ASSUMPTION_RECORDED = "self_model_assumption_recorded"
    SELF_MODEL_ASSUMPTION_INVALIDATED = "self_model_assumption_invalidated"
    SELF_MODEL_UNCERTAINTY_RECORDED = "self_model_uncertainty_recorded"
    SELF_MODEL_CONFLICT_DETECTED = "self_model_conflict_detected"
    SELF_MODEL_STALE = "self_model_stale"
    SELF_MODEL_CONSISTENCY_CHECKED = "self_model_consistency_checked"
    SELF_DIAGNOSTICS_COMPLETED = "self_diagnostics_completed"
    META_REASONING_COMPLETED = "meta_reasoning_completed"
    DECISION_READINESS_ASSESSED = "decision_readiness_assessed"
    CLARIFICATION_RECOMMENDED = "clarification_recommended"
    HUMAN_ESCALATION_RECOMMENDED = "human_escalation_recommended"
    CONFIDENCE_CALIBRATED = "confidence_calibrated"
    SELF_REFLECTION_RECORDED = "self_reflection_recorded"
    SELF_CRITIQUE_COMPLETED = "self_critique_completed"
    SELF_MODEL_EVOLUTION_EVIDENCE = "self_model_evolution_evidence"
    GOAL_REGISTERED = "goal_registered"
    GOAL_UPDATED = "goal_updated"
    GOAL_STATUS_CHANGED = "goal_status_changed"
    GOAL_MILESTONE_CREATED = "goal_milestone_created"
    GOAL_MILESTONE_UPDATED = "goal_milestone_updated"
    GOAL_DEPENDENCY_RECORDED = "goal_dependency_recorded"
    GOAL_BLOCKER_DETECTED = "goal_blocker_detected"
    GOAL_STRATEGY_SELECTED = "goal_strategy_selected"
    GOAL_ALTERNATIVE_GENERATED = "goal_alternative_generated"
    GOAL_PRIORITY_COMPUTED = "goal_priority_computed"
    GOAL_RESOURCE_ALLOCATED = "goal_resource_allocated"
    GOAL_PROGRESS_RECORDED = "goal_progress_recorded"
    GOAL_CONFLICT_DETECTED = "goal_conflict_detected"
    GOAL_CONFLICT_ESCALATED = "goal_conflict_escalated"
    GOAL_REASSESSED = "goal_reassessed"
    GOAL_DECISION_RECORDED = "goal_decision_recorded"
    GOAL_VERIFICATION_COMPLETED = "goal_verification_completed"
    STRATEGIC_CYCLE_STARTED = "strategic_cycle_started"
    STRATEGIC_CYCLE_COMPLETED = "strategic_cycle_completed"
    STRATEGIC_CYCLE_BLOCKED = "strategic_cycle_blocked"
    STRATEGIC_ACTION_QUEUED = "strategic_action_queued"
    STRATEGIC_ACTION_BLOCKED = "strategic_action_blocked"

    # Sovereign boundary and integrated-capability events (docs/evolution/07 §5).
    # Appended only: existing values are never reused or renumbered, because stored
    # events are the audit record and an old row must keep its meaning.
    SOVEREIGN_VERIFIED = "sovereign_verified"
    SOVEREIGN_DRIFT_DETECTED = "sovereign_drift_detected"
    INVARIANT_VIOLATION = "invariant_violation"
    SECURITY_DEGRADED = "security_degraded"
    RUNTIME_BACKEND_SELECTED = "runtime_backend_selected"
    MEMORY_RETRIEVED = "memory_retrieved"
    SOVEREIGN_DRIFT_ACCEPTED = "sovereign_drift_accepted"


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
