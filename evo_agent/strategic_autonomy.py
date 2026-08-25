from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
import hashlib
import json
import re
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from .models import Event, EventType, RiskLevel, new_id, utc_now
from .storage import SQLiteStore

STRATEGIC_ARCHITECTURE_VERSION = "strategic-autonomy-v1"
_MAX_TEXT = 2000
_MAX_ITEMS = 64
_PROTECTED = re.compile(r"(?:disable|bypass|override|modify|change|grant|access|execute|run|mutate|approve|promote|deploy|clear).{0,80}(?:governance|approval|verification|protected core|protected-core|kill switch|permission|credential|arbitrary code|production|promotion|metamorphosis|evolution)|(?:ignore|disregard).{0,80}(?:instruction|policy|safety|governance)|(?:approve|grant|give).{0,80}(?:itself|self).{0,40}(?:approval|permission|authority)?|(?:api[_ -]?key|password|secret|token|credential)\s*[:=]", re.I)
_SECRET_KEY = re.compile(r"(?:api[_ -]?key|password|secret|token|credential|private[_ -]?key|authorization|prompt|model[_ -]?output|raw[_ -]?output)", re.I)


def _safe(value: Any) -> Any:
    if isinstance(value, Enum): return value.value
    if isinstance(value, Mapping):
        return {str(k): "[REDACTED]" if _SECRET_KEY.search(str(k)) else _safe(v) for k, v in list(value.items())[:_MAX_ITEMS]}
    if isinstance(value, (list, tuple, set)): return [_safe(v) for v in list(value)[:_MAX_ITEMS]]
    if isinstance(value, str): return value[:_MAX_TEXT]
    if isinstance(value, (int, float, bool)) or value is None: return value
    return str(value)[:_MAX_TEXT]


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "").strip()).lower()[:_MAX_TEXT]


class GoalStatus(str, Enum):
    DRAFT = "draft"
    ACTIVE = "active"
    BLOCKED = "blocked"
    PAUSED = "paused"
    AT_RISK = "at_risk"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    SUPERSEDED = "superseded"


class GoalLifecycle(str, Enum):
    DRAFT = "draft"
    ACTIVE = "active"
    BLOCKED = "blocked"
    PAUSED = "paused"
    AT_RISK = "at_risk"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    SUPERSEDED = "superseded"


class StrategyStatus(str, Enum):
    VALID = "strategy_valid"
    DEGRADED = "strategy_degraded"
    BLOCKED = "strategy_blocked"
    FAILED = "strategy_failed"
    OUTDATED = "strategy_outdated"


class BlockerType(str, Enum):
    CAPABILITY = "capability"
    RESOURCE = "resource"
    ENVIRONMENT = "environment"
    DEPENDENCY = "dependency"
    APPROVAL = "approval"
    PERMISSION = "permission"
    VERIFICATION = "verification"
    STRATEGY = "strategy"
    EXTERNAL = "external"
    HUMAN = "human"
    UNKNOWN = "unknown"


class GoalConflictStatus(str, Enum):
    RESOLVED = "resolved"
    DEFERRED = "deferred"
    ESCALATED = "escalated"
    INCOMPATIBLE = "incompatible"
    REQUIRES_CLARIFICATION = "requires_clarification"


class ReassessmentRecommendation(str, Enum):
    CONTINUE = "continue"
    ADAPT = "adapt"
    REPLAN = "replan"
    PAUSE = "pause"
    ESCALATE = "escalate"
    ABORT = "abort"


class GoalVerificationState(str, Enum):
    UNVERIFIED = "unverified"
    PARTIAL = "partial"
    VERIFIED = "verified"
    FAILED = "failed"
    CONFLICTED = "conflicted"


@dataclass
class Goal:
    goal_id: str
    parent_goal_id: str | None
    title: str
    objective: str
    normalized_objective: str
    owner: str
    priority: int
    importance: float
    urgency: float
    strategic_value: float
    risk: RiskLevel
    status: GoalStatus = GoalStatus.DRAFT
    lifecycle: GoalLifecycle = GoalLifecycle.DRAFT
    created_at: str = field(default_factory=utc_now)
    deadline: str | None = None
    dependencies: list[str] = field(default_factory=list)
    constraints: list[str] = field(default_factory=list)
    success_criteria: list[str] = field(default_factory=list)
    measurable_metrics: dict[str, Any] = field(default_factory=dict)
    resource_budget: dict[str, float] = field(default_factory=dict)
    required_capabilities: list[str] = field(default_factory=list)
    required_models: list[str] = field(default_factory=list)
    required_specialists: list[str] = field(default_factory=list)
    required_integrations: list[str] = field(default_factory=list)
    architecture_version: str = STRATEGIC_ARCHITECTURE_VERSION
    provenance: dict[str, Any] = field(default_factory=dict)
    confidence: float = 0.5
    uncertainty: list[str] = field(default_factory=list)
    assumption_refs: list[str] = field(default_factory=list)
    current_strategy: str | None = None
    current_milestone: str | None = None
    progress_state: dict[str, Any] = field(default_factory=dict)
    verification_requirements: list[str] = field(default_factory=list)
    human_priority: int | None = None
    updated_at: str = field(default_factory=utc_now)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        data = _safe(asdict(self))
        data["risk"] = self.risk.value
        data["status"] = self.status.value
        data["lifecycle"] = self.lifecycle.value
        return data


StrategicGoal = Goal
GoalRecord = Goal


@dataclass
class Milestone:
    milestone_id: str
    goal_id: str
    title: str
    objective: str
    sequence: int
    dependencies: list[str] = field(default_factory=list)
    prerequisites: list[str] = field(default_factory=list)
    success_criteria: list[str] = field(default_factory=list)
    required_capabilities: list[str] = field(default_factory=list)
    expected_resources: dict[str, float] = field(default_factory=dict)
    estimated_difficulty: float = 0.5
    risk: RiskLevel = RiskLevel.LOW
    deadline: str | None = None
    verification_requirements: list[str] = field(default_factory=list)
    status: str = "pending"
    provenance: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)

    def to_dict(self) -> dict[str, Any]:
        data = _safe(asdict(self)); data["risk"] = self.risk.value; return data


@dataclass
class StrategicPlan:
    plan_id: str
    goal_id: str
    objective: str
    milestones: list[Milestone]
    subgoals: list[dict[str, Any]]
    tasks: list[dict[str, Any]]
    dependencies: list[dict[str, Any]]
    bounded: bool = True
    max_depth: int = 3
    provenance: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=utc_now)

    def to_dict(self) -> dict[str, Any]:
        data = _safe(asdict(self)); data["milestones"] = [m.to_dict() for m in self.milestones]; return data


@dataclass
class GoalDependency:
    dependency_id: str
    goal_id: str
    depends_on_id: str
    dependency_type: str
    status: str = "pending"
    provenance: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=utc_now)

    def to_dict(self) -> dict[str, Any]: return _safe(asdict(self))


@dataclass
class GoalBlocker:
    blocker_id: str
    goal_id: str
    blocker_type: BlockerType
    description: str
    severity: str
    status: str = "open"
    evidence: list[str] = field(default_factory=list)
    provenance: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)

    def to_dict(self) -> dict[str, Any]:
        data = _safe(asdict(self)); data["blocker_type"] = self.blocker_type.value; return data


@dataclass
class StrategyRecord:
    strategy_id: str
    goal_id: str
    name: str
    status: StrategyStatus
    assumptions: list[str]
    expected_outcome: str
    alternatives: list[str]
    evidence: list[str]
    confidence: float
    historical_performance: dict[str, Any]
    failure_history: list[str]
    resource_requirements: dict[str, float]
    risk_profile: str
    provenance: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)

    def to_dict(self) -> dict[str, Any]:
        data = _safe(asdict(self)); data["status"] = self.status.value; return data


@dataclass
class StrategicAlternative:
    alternative_id: str
    goal_id: str
    name: str
    rationale: str
    expected_benefit: str
    expected_cost: dict[str, float]
    risk: RiskLevel
    dependencies: list[str]
    required_capabilities: list[str]
    confidence: float
    evidence: list[str]
    rollback_approach: str
    status: str = "advisory"
    provenance: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=utc_now)

    def to_dict(self) -> dict[str, Any]:
        data = _safe(asdict(self)); data["risk"] = self.risk.value; return data


@dataclass
class PriorityResult:
    goal_id: str
    score: float
    explanation: str
    confidence: float
    blocking_factors: list[str]
    recommended_order: int
    human_priority_authoritative: bool
    provenance: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=utc_now)

    def to_dict(self) -> dict[str, Any]: return _safe(asdict(self))


@dataclass
class ResourceAllocation:
    allocation_id: str
    goal_id: str
    resource_type: str
    fraction: float
    amount: float
    ceiling: float
    rationale: str
    bounded: bool = True
    approval_required: bool = False
    provenance: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=utc_now)

    def to_dict(self) -> dict[str, Any]: return _safe(asdict(self))


@dataclass
class GoalProgress:
    progress_id: str
    goal_id: str
    milestone_completion: dict[str, float]
    subgoal_completion: dict[str, float]
    task_completion: dict[str, float]
    verified_outcomes: list[str]
    remaining_work: list[str]
    blocked_work: list[str]
    failed_work: list[str]
    recovery_attempts: int
    strategy_changes: int
    resource_consumption: dict[str, float]
    deadline_risk: str
    expected_completion: str | None
    confidence: float
    completion: float
    verified_state: GoalVerificationState
    evidence: list[str] = field(default_factory=list)
    provenance: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=utc_now)

    def to_dict(self) -> dict[str, Any]:
        data = _safe(asdict(self)); data["verified_state"] = self.verified_state.value; return data


@dataclass
class GoalConflict:
    conflict_id: str
    goal_a_id: str
    goal_b_id: str
    conflict_type: str
    description: str
    status: GoalConflictStatus
    resolution: str | None
    human_priority_applied: bool
    evidence: list[str] = field(default_factory=list)
    provenance: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=utc_now)

    def to_dict(self) -> dict[str, Any]:
        data = _safe(asdict(self)); data["status"] = self.status.value; return data


@dataclass
class GoalDecision:
    decision_id: str
    goal_id: str
    decision_type: str
    alternatives_considered: list[str]
    selected_strategy: str | None
    rejected_alternatives: list[str]
    evidence: list[str]
    assumptions: list[str]
    confidence: float
    uncertainty: list[str]
    expected_outcome: str
    actual_outcome: str | None
    human_intervention: dict[str, Any]
    verification_status: str
    rollback_information: dict[str, Any]
    architecture_version: str = STRATEGIC_ARCHITECTURE_VERSION
    model_metadata: dict[str, Any] = field(default_factory=dict)
    provenance: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=utc_now)

    def to_dict(self) -> dict[str, Any]: return _safe(asdict(self))


@dataclass
class GoalReassessment:
    reassessment_id: str
    goal_id: str
    recommendation: ReassessmentRecommendation
    trigger: str
    reasoning: str
    evidence: list[str]
    confidence: float
    uncertainty: list[str]
    human_required: bool
    created_at: str = field(default_factory=utc_now)

    def to_dict(self) -> dict[str, Any]:
        data = _safe(asdict(self)); data["recommendation"] = self.recommendation.value; return data


@dataclass
class GoalVerification:
    verification_id: str
    goal_id: str
    state: GoalVerificationState
    verified: bool
    satisfied_criteria: list[str]
    unsatisfied_criteria: list[str]
    evidence: list[str]
    authoritative_verifier: str
    confidence: float
    created_at: str = field(default_factory=utc_now)

    def to_dict(self) -> dict[str, Any]:
        data = _safe(asdict(self)); data["state"] = self.state.value; return data


class GoalRegistry:
    def __init__(self, store: SQLiteStore, architecture_version: str = STRATEGIC_ARCHITECTURE_VERSION):
        self.store = store; self.architecture_version = architecture_version

    def create_goal(self, objective: str, title: str | None = None, **kwargs: Any) -> Goal:
        text = str(objective or "").strip()
        if not text: raise ValueError("goal objective must not be empty")
        risk = kwargs.pop("risk", RiskLevel.LOW); risk = risk if isinstance(risk, RiskLevel) else RiskLevel(str(risk))
        human_priority = kwargs.pop("human_priority", None)
        priority = int(kwargs.pop("priority", human_priority if human_priority is not None else 50))
        source = str(kwargs.pop("source", "user"))[:80]
        unsafe = bool(_PROTECTED.search(text))
        requested_status = kwargs.pop("status", GoalStatus.ACTIVE)
        status = GoalStatus.BLOCKED if unsafe or source.lower() in {"model", "agent", "strategic_autonomy", "autonomous"} else requested_status
        status = status if isinstance(status, GoalStatus) else GoalStatus(str(status))
        goal = Goal(new_id("goal"), kwargs.pop("parent_goal_id", None),
 (title or text[:120]).strip()[:_MAX_TEXT], text[:_MAX_TEXT], _norm(text), kwargs.pop("owner", "user"), priority, float(kwargs.pop("importance", .5)), float(kwargs.pop("urgency", .5)), float(kwargs.pop("strategic_value", .5)), risk, status, GoalLifecycle(status), deadline=kwargs.pop("deadline", None), dependencies=list(kwargs.pop("dependencies", []))[:_MAX_ITEMS], constraints=list(kwargs.pop("constraints", []))[:_MAX_ITEMS], success_criteria=list(kwargs.pop("success_criteria", ["all required milestones are verified"]))[:_MAX_ITEMS], measurable_metrics=dict(kwargs.pop("measurable_metrics", {})), resource_budget={str(k): float(v) for k, v in dict(kwargs.pop("resource_budget", {})).items()}, required_capabilities=list(kwargs.pop("required_capabilities", []))[:_MAX_ITEMS], required_models=list(kwargs.pop("required_models", []))[:_MAX_ITEMS], required_specialists=list(kwargs.pop("required_specialists", []))[:_MAX_ITEMS], required_integrations=list(kwargs.pop("required_integrations", []))[:_MAX_ITEMS], architecture_version=self.architecture_version, provenance={"source": source, "created_by": "goal_registry", "authority": "human_or_governance" if source.lower() in {"user", "human", "system"} else "advisory"}, confidence=float(kwargs.pop("confidence", .5)), uncertainty=list(kwargs.pop("uncertainty", []))[:_MAX_ITEMS], assumption_refs=list(kwargs.pop("assumption_refs", []))[:_MAX_ITEMS], verification_requirements=list(kwargs.pop("verification_requirements", []))[:_MAX_ITEMS], human_priority=human_priority, metadata={"unsafe_content": unsafe, "non_authoritative_source": source.lower() not in {"user", "human", "system"}, **dict(kwargs)})
        self.save(goal); return goal

    create = create_goal

    def save(self, goal: Goal) -> Goal:
        self.store.save_strategic_goal(goal); self._emit(EventType.GOAL_REGISTERED if goal.status is GoalStatus.DRAFT else EventType.GOAL_UPDATED, {"goal": goal.to_dict()}, goal.goal_id); return goal

    def get(self, goal_id: str) -> Goal | None:
        row = self.store.strategic_goal_by_id(goal_id); return self._from_row(row) if row else None

    def list(self, status: GoalStatus | str | None = None, limit: int = 200) -> list[Goal]:
        value = status.value if isinstance(status, GoalStatus) else status; return [self._from_row(row) for row in self.store.find_strategic_goals(value, limit)]

    def update_status(self, goal_id: str, status: GoalStatus | str, reason: str = "") -> Goal:
        goal = self.get(goal_id)
        if not goal: raise KeyError(goal_id)
        goal.status = status if isinstance(status, GoalStatus) else GoalStatus(str(status)); goal.lifecycle = GoalLifecycle(goal.status); goal.updated_at = utc_now(); goal.metadata["status_reason"] = reason[:_MAX_TEXT]; self.save(goal); self._emit(EventType.GOAL_STATUS_CHANGED, {"goal_id": goal_id, "status": goal.status.value, "reason": reason}, goal_id); return goal

    @staticmethod
    def _from_row(row: dict[str, Any]) -> Goal:
        payload = row.get("payload", row); payload = payload if isinstance(payload, dict) else json.loads(payload)
        payload["risk"] = RiskLevel(payload.get("risk", RiskLevel.LOW.value)); payload["status"] = GoalStatus(payload.get("status", GoalStatus.DRAFT.value)); payload["lifecycle"] = GoalLifecycle(payload.get("lifecycle", payload["status"].value)); return Goal(**{k: payload[k] for k in Goal.__dataclass_fields__ if k in payload})

    def _emit(self, event_type: EventType, payload: dict[str, Any], task_id: str) -> None:
        try: self.store.append_event(Event(new_id("event"), task_id, event_type, _safe(payload)))
        except Exception: pass


class StrategicPlanner:
    def __init__(self, store: SQLiteStore, max_depth: int = 3, max_nodes: int = 12): self.store = store; self.max_depth = max(1, min(5, int(max_depth))); self.max_nodes = max(1, min(32, int(max_nodes)))

    def plan(self, goal: Goal) -> StrategicPlan:
        clauses = [item.strip() for item in re.split(r"\bthen\b|;|\band\b", goal.normalized_objective) if item.strip()][:self.max_nodes]
        if not clauses: clauses = [goal.normalized_objective]
        milestones: list[Milestone] = []; previous: str | None = None
        if goal.goal_id in goal.dependencies:
            raise ValueError("goal dependency graph cannot contain a self-cycle")
        for index, clause in enumerate(clauses):
            milestone = Milestone(new_id("milestone"), goal.goal_id, clause[:120], clause[:_MAX_TEXT], index, [previous] if previous else [], [previous] if previous else [], [goal.success_criteria[index] if index < len(goal.success_criteria) else "milestone outcome is verified"], list(goal.required_capabilities), dict(goal.resource_budget), min(1.0, .35 + .1 * index), goal.risk, goal.deadline, list(goal.verification_requirements), "pending", {"source": "strategic_planner", "goal_id": goal.goal_id})
            milestones.append(milestone); self.store.save_goal_milestone(milestone); previous = milestone.milestone_id
        tasks = [{"task_id": new_id("strategic_task"), "milestone_id": item.milestone_id, "description": item.objective, "status": "pending", "execution_authority": "runtime_kernel", "verification_required": True} for item in milestones]
        for milestone in milestones: self.store.save_goal_decision_metadata(goal.goal_id, {"milestone_id": milestone.milestone_id, "objective": milestone.objective, "type": "milestone"})
        plan = StrategicPlan(new_id("strategic_plan"), goal.goal_id, goal.objective, milestones, [{"goal_id": goal.goal_id, "objective": goal.objective}], tasks, [{"from": item.milestone_id, "to": dep} for item in milestones for dep in item.dependencies], True, self.max_depth, {"source": "strategic_planner", "architecture_version": goal.architecture_version})
        goal.current_milestone = milestones[0].milestone_id if milestones else None; goal.progress_state = {"milestones": [item.milestone_id for item in milestones], "plan_id": plan.plan_id}; goal.updated_at = utc_now()
        try: self.store.save_strategic_goal(goal)
        except Exception: pass
        return plan

    decompose = plan
    strategic_plan = plan


class GoalPrioritizer:
    def __init__(self, store: SQLiteStore): self.store = store

    def prioritize(self, goals: Sequence[Goal]) -> list[PriorityResult]:
        scored = [self.score(goal) for goal in goals]; scored.sort(key=lambda item: (-item.score, item.goal_id))
        for index, item in enumerate(scored, 1): item.recommended_order = index; self.store.save_goal_decision_metadata(item.goal_id, item.to_dict())
        return scored

    def score(self, goal: Goal) -> PriorityResult:
        if goal.human_priority is not None:
            score = float(max(0, min(100, goal.human_priority))); human = True; explanation = "Explicit human priority is authoritative."
        else:
            deadline_factor = 0.0
            if goal.deadline:
                try: deadline_factor = max(0.0, min(1.0, 1.0 - (datetime.fromisoformat(goal.deadline) - datetime.now(timezone.utc)).total_seconds() / (30 * 86400)))
                except Exception: deadline_factor = 0.0
            score = 100 * (.25 * goal.importance + .2 * goal.urgency + .25 * goal.strategic_value + .15 * deadline_factor + .1 * max(0.0, 1.0 - (goal.risk is RiskLevel.CRITICAL) * .5) + .05 * min(1.0, len(goal.dependencies) / 4))
            human = False; explanation = "Deterministic score combines importance, urgency, strategic value, deadline proximity, risk, and dependency impact."
        return PriorityResult(goal.goal_id, round(score, 4), explanation, .8 if human else .65, [], 0, human, {"source": "goal_prioritizer", "architecture_version": goal.architecture_version})

    prioritize_goals = prioritize


class ResourceAllocator:
    def __init__(self, store: SQLiteStore, max_fraction: float = 1.0): self.store = store; self.max_fraction = max(0.0, min(1.0, float(max_fraction)))

    def allocate(self, goals: Sequence[Goal], available: Mapping[str, float] | None = None) -> list[ResourceAllocation]:
        available = {str(k): max(0.0, float(v)) for k, v in dict(available or {"time": 1.0, "compute": 1.0, "memory": 1.0, "storage": 1.0}).items()}
        weights = [max(.01, (g.priority if g.human_priority is not None else 0) + 100 * (g.importance + g.urgency + g.strategic_value)) for g in goals]; total = sum(weights) or 1.0; allocations: list[ResourceAllocation] = []
        for goal, weight in zip(goals, weights):
            fraction = min(self.max_fraction, weight / total)
            for resource, ceiling in available.items():
                allocation = ResourceAllocation(new_id("allocation"), goal.goal_id, resource, round(fraction, 6), round(ceiling * fraction, 6), ceiling, "Weighted bounded allocation; Runtime and Kernel ceilings remain authoritative.", True, goal.risk in {RiskLevel.HIGH, RiskLevel.CRITICAL}, {"source": "resource_allocator", "architecture_version": goal.architecture_version})
                self.store.save_goal_resource_allocation(allocation); allocations.append(allocation)
        return allocations

    allocate_resources = allocate


class StrategyEngine:
    def __init__(self, store: SQLiteStore, adaptive_learning: Any | None = None, self_model: Any | None = None): self.store = store; self.adaptive_learning = adaptive_learning; self.self_model = self_model

    def assess(self, goal: Goal, evidence: Mapping[str, Any] | None = None) -> StrategyRecord:
        evidence = dict(evidence or {}); failures = list(evidence.get("failures", []))[:_MAX_ITEMS]; verified = bool(evidence.get("verified", False)); status = StrategyStatus.VALID if verified or not failures else StrategyStatus.DEGRADED if len(failures) < 3 else StrategyStatus.FAILED
        if evidence.get("environment_changed") or evidence.get("version_mismatch"): status = StrategyStatus.OUTDATED
        if evidence.get("blocked"): status = StrategyStatus.BLOCKED
        name = goal.current_strategy or "bounded-sequential"
        record = StrategyRecord(new_id("strategy"), goal.goal_id, name, status, list(goal.uncertainty)[:_MAX_ITEMS], "verified milestone progress", [], list(evidence.get("evidence", []))[:_MAX_ITEMS], .75 if status is StrategyStatus.VALID else .35, dict(evidence.get("historical_performance", {})), failures, dict(goal.resource_budget), goal.risk.value, {"source": "strategy_engine", "architecture_version": goal.architecture_version})
        self.store.save_goal_strategy(record); return record

    select = assess
    evaluate = assess


class AlternativeStrategyEngine:
    def __init__(self, store: SQLiteStore, capability_intelligence: Any | None = None, model_intelligence: Any | None = None, specialist_intelligence: Any | None = None, world_intelligence: Any | None = None, memory: Any | None = None, evaluation: Any | None = None, adaptive_learning: Any | None = None, max_alternatives: int = 3): self.store = store; self.max_alternatives = max(1, min(8, int(max_alternatives))); self.dependencies = [capability_intelligence, model_intelligence, specialist_intelligence, world_intelligence, memory, evaluation, adaptive_learning]

    def generate(self, goal: Goal, strategy: StrategyRecord | None = None) -> list[StrategicAlternative]:
        names = ["evidence-first", "resource-conserving", "specialist-assisted"][:self.max_alternatives]; alternatives: list[StrategicAlternative] = []
        for name in names:
            strategy_evidence = strategy.evidence if hasattr(strategy, "evidence") else (strategy.get("payload", {}).get("evidence", []) if isinstance(strategy, dict) else [])
            alt = StrategicAlternative(new_id("alternative"), goal.goal_id, name, f"Use {name} to address the current strategic objective while preserving verification.", "Improve verified progress without increasing authority", {"time": .2 if name == "resource-conserving" else .35, "compute": .2}, goal.risk, list(goal.dependencies), list(goal.required_capabilities), .55, list(strategy_evidence)[:_MAX_ITEMS], "Restore the previous strategy and preserve prior evidence.", provenance={"source": "alternative_strategy_engine", "architecture_version": goal.architecture_version})
            self.store.save_goal_alternative(alt); alternatives.append(alt)
        return alternatives

    alternatives = generate


class ProgressTracker:
    def __init__(self, store: SQLiteStore): self.store = store

    def update(self, goal: Goal, milestones: Sequence[Milestone] = (), evidence: Sequence[Mapping[str, Any]] = (), resource_consumption: Mapping[str, float] | None = None) -> GoalProgress:
        milestones = list(milestones); evidence = list(evidence); verified = [str(item.get("evidence_id", item.get("task_id", new_id("evidence")))) for item in evidence if bool(item.get("verified", item.get("verification_success", False)))]
        completed_m = {m.milestone_id: 1.0 for m in milestones if m.status == "verified"}; blocked = [m.objective for m in milestones if m.status == "blocked"]; failed = [m.objective for m in milestones if m.status == "failed"]; remaining = [m.objective for m in milestones if m.milestone_id not in completed_m]
        completion = len(completed_m) / max(1, len(milestones)); state = GoalVerificationState.VERIFIED if milestones and completion == 1.0 and not failed and not blocked and all(verified or not evidence for _ in [0]) else GoalVerificationState.PARTIAL if completion > 0 else GoalVerificationState.UNVERIFIED
        if failed and not completed_m: state = GoalVerificationState.FAILED
        progress = GoalProgress(new_id("progress"), goal.goal_id, completed_m, {}, {"tasks": completion}, verified, remaining, blocked, failed, int(goal.progress_state.get("recovery_attempts", 0)), int(goal.progress_state.get("strategy_changes", 0)), dict(resource_consumption or {}), "high" if goal.deadline and remaining else "low", goal.deadline if remaining else utc_now(), .8 if verified else .4, completion, state, verified, {"source": "progress_tracker", "architecture_version": goal.architecture_version})
        self.store.save_goal_progress(progress); goal.progress_state.update({"completion": completion, "verified_state": state.value}); return progress

    track = update


class DependencyEngine:
    def __init__(self, store: SQLiteStore, capability_intelligence: Any | None = None, model_intelligence: Any | None = None, specialist_intelligence: Any | None = None, external_integrations: Any | None = None, self_model: Any | None = None): self.store = store; self.capability_intelligence = capability_intelligence; self.model_intelligence = model_intelligence; self.specialist_intelligence = specialist_intelligence; self.external_integrations = external_integrations; self.self_model = self_model

    def analyze(self, goal: Goal, context: Mapping[str, Any] | None = None) -> list[GoalBlocker]:
        context = dict(context or {}); blockers: list[GoalBlocker] = []
        for dependency_id in list(goal.dependencies)[:_MAX_ITEMS]:
            dependency = self.store.strategic_goal_by_id(str(dependency_id))
            dependency_status = str((dependency or {}).get("status", "missing"))
            self.store.save_goal_dependency(GoalDependency(new_id("dependency"), goal.goal_id, str(dependency_id), "goal_prerequisite", dependency_status, {"source": "dependency_engine", "architecture_version": goal.architecture_version}))
            if dependency is None or dependency_status in {GoalStatus.FAILED.value, GoalStatus.BLOCKED.value, GoalStatus.CANCELLED.value}:
                blockers.append(GoalBlocker(new_id("blocker"), goal.goal_id, BlockerType.DEPENDENCY, f"Prerequisite goal is unavailable or not successful: {dependency_id}", "high", evidence=[str(dependency_id)], provenance={"source": "dependency_engine", "architecture_version": goal.architecture_version}))
        for required, key, kind in ((goal.required_capabilities, "capabilities", BlockerType.CAPABILITY), (goal.required_models, "models", BlockerType.ENVIRONMENT), (goal.required_specialists, "specialists", BlockerType.DEPENDENCY), (goal.required_integrations, "integrations", BlockerType.EXTERNAL)):
            available = set(str(item) for item in context.get(key, []));
            for item in required:
                if key in context and item not in available: blockers.append(GoalBlocker(new_id("blocker"), goal.goal_id, kind, f"Required {key[:-1]} is unavailable: {item}", "high", evidence=[f"missing:{item}"], provenance={"source": "dependency_engine", "architecture_version": goal.architecture_version}))
        if context.get("approval_required"): blockers.append(GoalBlocker(new_id("blocker"), goal.goal_id, BlockerType.APPROVAL, "Human approval is required before strategic action.", "high", provenance={"source": "dependency_engine", "architecture_version": goal.architecture_version}))
        if context.get("resource_exhausted"): blockers.append(GoalBlocker(new_id("blocker"), goal.goal_id, BlockerType.RESOURCE, "Available resource budget is exhausted.", "high", provenance={"source": "dependency_engine", "architecture_version": goal.architecture_version}))
        if context.get("environment_changed"): blockers.append(GoalBlocker(new_id("blocker"), goal.goal_id, BlockerType.ENVIRONMENT, "Current environment changed since the strategic plan.", "medium", provenance={"source": "dependency_engine", "architecture_version": goal.architecture_version}))
        if context.get("verification_failed"): blockers.append(GoalBlocker(new_id("blocker"), goal.goal_id, BlockerType.VERIFICATION, "Required outcome verification failed.", "high", provenance={"source": "dependency_engine", "architecture_version": goal.architecture_version}))
        if context.get("prerequisite_failures"): blockers.append(GoalBlocker(new_id("blocker"), goal.goal_id, BlockerType.DEPENDENCY, "A prerequisite goal or milestone failed.", "high", evidence=[str(item) for item in list(context.get("prerequisite_failures", []))[:_MAX_ITEMS]], provenance={"source": "dependency_engine", "architecture_version": goal.architecture_version}))
        if context.get("stale_plan"): blockers.append(GoalBlocker(new_id("blocker"), goal.goal_id, BlockerType.STRATEGY, "The strategic plan is stale and requires revalidation.", "medium", provenance={"source": "dependency_engine", "architecture_version": goal.architecture_version}))
        if context.get("architecture_version") and str(context.get("architecture_version")) != goal.architecture_version: blockers.append(GoalBlocker(new_id("blocker"), goal.goal_id, BlockerType.ENVIRONMENT, "Architecture version differs from the strategic record.", "high", provenance={"source": "dependency_engine", "architecture_version": goal.architecture_version}))
        if context.get("environment_version") and str(context.get("environment_version")) != str(goal.metadata.get("environment_version", context.get("environment_version"))): blockers.append(GoalBlocker(new_id("blocker"), goal.goal_id, BlockerType.ENVIRONMENT, "Environment version requires plan revalidation.", "medium", provenance={"source": "dependency_engine", "architecture_version": goal.architecture_version}))
        for blocker in blockers: self.store.save_goal_blocker(blocker)
        return blockers

    detect = analyze


class GoalConflictEngine:
    def __init__(self, store: SQLiteStore): self.store = store

    def detect(self, goals: Sequence[Goal]) -> list[GoalConflict]:
        results: list[GoalConflict] = []
        for index, left in enumerate(goals):
            for right in list(goals)[index + 1:]:
                common = set(left.resource_budget) & set(right.resource_budget); conflict_type = "resource" if common else "priority" if left.human_priority is not None and right.human_priority is not None and left.human_priority == right.human_priority else "state" if ("preserve" in left.normalized_objective and "delete" in right.normalized_objective) or ("delete" in left.normalized_objective and "preserve" in right.normalized_objective) else ""
                if not conflict_type: continue
                human = left.human_priority is not None or right.human_priority is not None; status = GoalConflictStatus.REQUIRES_CLARIFICATION if not human else GoalConflictStatus.DEFERRED
                resolution = "explicit human priority retained; other goal deferred" if human else None
                conflict = GoalConflict(new_id("goal_conflict"), left.goal_id, right.goal_id, conflict_type, f"Goals have competing {conflict_type} requirements.", status, resolution, human, [left.goal_id, right.goal_id], {"source": "goal_conflict_engine", "architecture_version": left.architecture_version})
                self.store.save_goal_conflict(conflict); results.append(conflict)
        return results

    resolve = detect


class StrategicReassessmentEngine:
    def __init__(self, store: SQLiteStore, strategy_engine: StrategyEngine, dependency_engine: DependencyEngine): self.store = store; self.strategy_engine = strategy_engine; self.dependency_engine = dependency_engine

    def reassess(self, goal: Goal, trigger: str = "new_evidence", context: Mapping[str, Any] | None = None) -> GoalReassessment:
        context = dict(context or {}); blockers = self.dependency_engine.analyze(goal, context); strategy = self.strategy_engine.assess(goal, context)
        if blockers: recommendation = ReassessmentRecommendation.ESCALATE if any(b.blocker_type in {BlockerType.APPROVAL, BlockerType.HUMAN, BlockerType.PERMISSION} for b in blockers) else ReassessmentRecommendation.PAUSE
        elif goal.risk in {RiskLevel.HIGH, RiskLevel.CRITICAL} or context.get("irreversible_external_effect") or context.get("protected_system_impact") or context.get("material_intent_change"): recommendation = ReassessmentRecommendation.ESCALATE
        elif strategy.status in {StrategyStatus.FAILED, StrategyStatus.OUTDATED}: recommendation = ReassessmentRecommendation.REPLAN
        elif strategy.status is StrategyStatus.DEGRADED: recommendation = ReassessmentRecommendation.ADAPT
        else: recommendation = ReassessmentRecommendation.CONTINUE
        rec = GoalReassessment(new_id("reassessment"), goal.goal_id, recommendation, trigger, f"{len(blockers)} blocker(s); strategy={strategy.status.value}; recommendation remains advisory.", [b.blocker_id for b in blockers] + strategy.evidence, .8 if not blockers else .55, list(goal.uncertainty), recommendation in {ReassessmentRecommendation.ESCALATE, ReassessmentRecommendation.ABORT},); self.store.save_goal_reassessment(rec); return rec

    reassess_goal = reassess


class GoalVerifier:
    def __init__(self, store: SQLiteStore, verifier: Any | None = None): self.store = store; self.verifier = verifier

    def verify(self, goal: Goal, milestones: Sequence[Milestone] = (), task_outcomes: Sequence[Mapping[str, Any]] = ()) -> GoalVerification:
        milestones = list(milestones); outcomes = list(task_outcomes); satisfied: list[str] = []; unsatisfied: list[str] = []; evidence: list[str] = []
        for criterion in goal.success_criteria:
            matching = [item for item in outcomes if item.get("criterion") == criterion or item.get("description") == criterion]
            if matching and all(bool(item.get("verified", item.get("verification_success", False))) for item in matching): satisfied.append(criterion); evidence.extend(str(item.get("evidence_id", item.get("task_id", ""))) for item in matching)
            elif criterion == "all required milestones are verified" and milestones and all(m.status == "verified" for m in milestones): satisfied.append(criterion); evidence.extend(m.milestone_id for m in milestones)
            else: unsatisfied.append(criterion)
        if any(item.get("conflict") for item in outcomes): state = GoalVerificationState.CONFLICTED
        elif satisfied and not unsatisfied: state = GoalVerificationState.VERIFIED
        elif satisfied: state = GoalVerificationState.PARTIAL
        elif any(item.get("failed") for item in outcomes): state = GoalVerificationState.FAILED
        else: state = GoalVerificationState.UNVERIFIED
        result = GoalVerification(new_id("goal_verification"), goal.goal_id, state, state is GoalVerificationState.VERIFIED, satisfied, unsatisfied, evidence, "existing_verifier", .95 if state is GoalVerificationState.VERIFIED else .35); self.store.save_goal_verification(result); return result

    verify_goal = verify


class StrategicAutonomy:
    """Advisory strategic coordinator. Governance, Runtime, Kernel, and Verifier remain authoritative."""
    def __init__(self, store: SQLiteStore, workspace: Path | None = None, capability_intelligence: Any | None = None, model_intelligence: Any | None = None, specialist_intelligence: Any | None = None, external_integrations: Any | None = None, memory: Any | None = None, adaptive_learning: Any | None = None, self_model: Any | None = None, runtime: Any | None = None, evolution_orchestrator: Any | None = None, max_cycle_goals: int = 3, cognitive: Any | None = None):
        self.store = store; self.workspace = Path(workspace).resolve() if workspace else None; self.runtime = runtime; self.cognitive = cognitive; self.evolution_orchestrator = evolution_orchestrator; self.max_cycle_goals = max(1, min(8, int(max_cycle_goals))); self.registry = GoalRegistry(store); self.planner = StrategicPlanner(store); self.prioritizer = GoalPrioritizer(store); self.allocator = ResourceAllocator(store); self.strategy = StrategyEngine(store, adaptive_learning, self_model); self.alternatives_engine = AlternativeStrategyEngine(store, capability_intelligence, model_intelligence, specialist_intelligence, None, memory, None, adaptive_learning); self.dependencies = DependencyEngine(store, capability_intelligence, model_intelligence, specialist_intelligence, external_integrations, self_model); self.conflicts_engine = GoalConflictEngine(store); self.reassessment = StrategicReassessmentEngine(store, self.strategy, self.dependencies); self.progress_tracker = ProgressTracker(store); self.verifier = GoalVerifier(store); self.safe_mode = False; self.kill_switch = False

    def create_goal(self, objective: str, **kwargs: Any) -> Goal: return self.registry.create_goal(objective, **kwargs)
    register_goal = create_goal
    def plan_goal(self, goal_id: str) -> StrategicPlan:
        goal = self.registry.get(goal_id)
        if not goal: raise KeyError(goal_id)
        return self.planner.plan(goal)
    strategic_plan = plan_goal
    def recommend_next_actions(self, goal_id: str, max_actions: int = 3) -> dict[str, Any]:
        goal = self.registry.get(goal_id)
        if not goal: raise KeyError(goal_id)
        plan = self.planner.plan(goal)
        actions = [task for task in plan.tasks[:max(1, min(8, int(max_actions)))] if task.get("status") == "pending"]
        return {"goal_id": goal_id, "actions": actions, "bounded": True, "advisory": True, "cognitive_authority": "cognitive_planner", "execution_authority": "runtime_kernel", "verification_authority": "verifier", "cognitive_available": self.cognitive is not None}

    next_actions = recommend_next_actions
    def prioritize_goals(self, goals: Sequence[Goal] | None = None) -> list[PriorityResult]: return self.prioritizer.prioritize(list(goals) if goals is not None else self.registry.list(GoalStatus.ACTIVE))
    def allocate_resources(self, available: Mapping[str, float] | None = None, goals: Sequence[Goal] | None = None) -> list[ResourceAllocation]: return self.allocator.allocate(list(goals) if goals is not None else self.registry.list(GoalStatus.ACTIVE), available)
    def select_strategy(self, goal_id: str, evidence: Mapping[str, Any] | None = None) -> StrategyRecord:
        goal = self.registry.get(goal_id)
        if not goal: raise KeyError(goal_id)
        record = self.strategy.select(goal, evidence); goal.current_strategy = record.name; self.registry.save(goal); return record
    def generate_alternatives(self, goal_id: str) -> list[StrategicAlternative]:
        goal = self.registry.get(goal_id)
        if not goal: raise KeyError(goal_id)
        return self.alternatives_engine.generate(goal, self.store.latest_goal_strategy(goal_id) if hasattr(self.store, "latest_goal_strategy") else None)
    def update_progress(self, goal_id: str, milestones: Sequence[Milestone] = (), evidence: Sequence[Mapping[str, Any]] = ()) -> GoalProgress:
        goal = self.registry.get(goal_id)
        if not goal: raise KeyError(goal_id)
        return self.progress_tracker.update(goal, milestones, evidence)
    def find_blockers(self, goal_id: str, context: Mapping[str, Any] | None = None) -> list[GoalBlocker]:
        goal = self.registry.get(goal_id)
        if not goal: raise KeyError(goal_id)
        return self.dependencies.analyze(goal, context)
    def find_conflicts(self, goals: Sequence[Goal] | None = None) -> list[GoalConflict]: return self.conflicts_engine.detect(list(goals) if goals is not None else self.registry.list(GoalStatus.ACTIVE))
    def reassess_goal(self, goal_id: str, trigger: str = "new_evidence", context: Mapping[str, Any] | None = None) -> GoalReassessment:
        goal = self.registry.get(goal_id)
        if not goal: raise KeyError(goal_id)
        return self.reassessment.reassess(goal, trigger, context)
    def verify_goal(self, goal_id: str, milestones: Sequence[Milestone] = (), task_outcomes: Sequence[Mapping[str, Any]] = ()) -> GoalVerification:
        goal = self.registry.get(goal_id)
        if not goal: raise KeyError(goal_id)
        return self.verifier.verify(goal, milestones, task_outcomes)
    def strategic_cycle(self, goal_ids: Sequence[str] | None = None, context: Mapping[str, Any] | None = None) -> dict[str, Any]:
        if self.kill_switch: return {"status": "blocked", "reason": "strategic kill switch active", "bounded": True}
        if self.safe_mode: return {"status": "paused", "reason": "strategic autonomy paused in safe mode", "bounded": True}
        goals = [self.registry.get(item) for item in list(goal_ids or [])[:self.max_cycle_goals]] if goal_ids else self.registry.list(GoalStatus.ACTIVE)[:self.max_cycle_goals]; goals = [item for item in goals if item]
        ranked = self.prioritizer.prioritize(goals); decisions: list[dict[str, Any]] = []
        for rank in ranked[:self.max_cycle_goals]:
            goal = self.registry.get(rank.goal_id); reassessment = self.reassessment.reassess(goal, "strategic_cycle", context) if goal else None
            if goal and reassessment:
                decision = GoalDecision(new_id("decision"), goal.goal_id, "strategic_reassessment", [], goal.current_strategy, [], reassessment.evidence, goal.assumption_refs, reassessment.confidence, reassessment.uncertainty, reassessment.reasoning, None, {"required": reassessment.human_required}, "unverified", {"reversible": True}, provenance={"source": "strategic_autonomy", "architecture_version": goal.architecture_version}); self.store.save_goal_decision(decision); decisions.append(decision.to_dict())
        return {"status": "completed", "goal_count": len(goals), "priorities": [item.to_dict() for item in ranked], "decisions": decisions, "bounded": True, "execution_authority": "runtime_kernel", "verification_authority": "verifier"}

    run_cycle = strategic_cycle
    cycle = strategic_cycle

    def route_capability_gap(self, goal_id: str, capability: str, structural: bool = False, evidence_ids: Sequence[str] = ()) -> Any:
        goal = self.registry.get(goal_id)
        if not goal: raise KeyError(goal_id)
        capability = str(capability or "unknown")[:_MAX_TEXT]
        from .orchestrator import EvolutionOpportunity, OrchestrationPath
        path = OrchestrationPath.METAMORPHOSIS if structural else OrchestrationPath.EVOLUTION
        problem = f"Strategic capability gap for goal {goal.goal_id}: {capability}"
        if _PROTECTED.search(capability + " " + problem) or capability.lower() in {"governance", "approval", "verification", "protected core", "kill switch", "credentials"}:
            return {"status": "blocked", "reason": "protected authority cannot be routed by strategic autonomy", "path": path.value}
        source_ids = [str(item)[:120] for item in list(evidence_ids)[:_MAX_ITEMS]]
        metadata = {"strategic_goal_id": goal.goal_id, "capability_gap": capability, "structural": bool(structural), "cognitive_capability_gap": not bool(structural), "governance_required": True, "advisory": True}
        if self.evolution_orchestrator is None:
            return {"status": "evidence_only", "path": path.value, "problem": problem, "metadata": metadata}
        fingerprint = hashlib.sha256(json.dumps({"goal": goal.goal_id, "capability": capability, "structural": bool(structural), "architecture": goal.architecture_version}, sort_keys=True).encode()).hexdigest()
        opportunity = EvolutionOpportunity(new_id("opportunity"), source_ids, [], problem, 1, "medium", ["strategic_goal"], ["architecture"] if structural else ["strategic_planner"], [capability], "moderate", path, .65, architecture_version=goal.architecture_version, fingerprint=fingerprint, metadata=metadata)
        item = self.evolution_orchestrator.create_work_item(opportunity)
        return item.to_dict() if hasattr(item, "to_dict") else item

    bridge_to_evolution = route_capability_gap
    route_gap = route_capability_gap
    def set_safe_mode(self, enabled: bool) -> None: self.safe_mode = bool(enabled)
    def activate_kill_switch(self) -> None: self.kill_switch = True
    def clear_kill_switch(self, actor: str = "human") -> None:
        if str(actor).lower() in {"model", "strategic_autonomy", "autonomous", "agent"}: raise PermissionError("strategic autonomy cannot clear kill switch")
        self.kill_switch = False


GoalStrategicAutonomy = StrategicAutonomy
StrategicAutonomyEngine = StrategicAutonomy
GoalMilestone = Milestone
GoalStrategy = StrategyRecord
GoalAlternative = StrategicAlternative
GoalResourceAllocation = ResourceAllocation
GoalReassessment = GoalReassessment
GoalDecisionRecord = GoalDecision
GoalVerificationRecord = GoalVerification
GoalManager = GoalRegistry
