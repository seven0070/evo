from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
import hashlib
import json
import random
import threading
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from .models import Event, EventType, RiskLevel, new_id, utc_now
from .storage import SQLiteStore

ADAPTIVE_ARCHITECTURE_VERSION = "adaptive-learning-v1"
_PROTECTED_TERMS = {
    "governance", "permission", "permissions", "approval", "approval_logic", "verifier",
    "sandbox", "promotion", "rollback_authority", "kill_switch", "protected_core",
    "security_boundary", "security_boundaries", "architecture", "provider_adapters",
    "learning_algorithm", "credential", "credentials", "production", "arbitrary_code",
    "unrestricted_network", "self_replication", "agent_spawning",
}


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def _hash(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode()).hexdigest()


def _safe(value: Any, limit: int = 12000) -> Any:
    secret_terms = ("api_key", "access_token", "secret", "password", "private_key", "credential")
    if isinstance(value, Mapping):
        return {str(k): ("[REDACTED]" if any(t in str(k).lower() for t in secret_terms) else _safe(v, limit)) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_safe(v, limit) for v in list(value)[:100]]
    if isinstance(value, str) and len(value.encode()) > limit:
        return {"truncated": True, "content_hash": _hash(value), "excerpt": value[:limit]}
    return value


def _now_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None


def _payload(item: Any) -> dict[str, Any]:
    if hasattr(item, "to_dict"):
        item = item.to_dict()
    if isinstance(item, Mapping):
        return dict(item.get("payload", item)) if isinstance(item.get("payload", item), Mapping) else {"payload": item.get("payload")}
    return {}


def _text_has_protected(value: Any) -> bool:
    text = _canonical(value).lower()
    return any(term in text for term in _PROTECTED_TERMS)


class PatternType(str, Enum):
    REPEATED_TASK_FAILURE = "repeated_task_failure"
    REPEATED_SUCCESSFUL_STRATEGY = "repeated_successful_strategy"
    REPEATED_RECOVERY = "repeated_recovery"
    TOOL_RELIABILITY = "tool_reliability"
    MODEL_RELIABILITY = "model_reliability"
    SPECIALIST_RELIABILITY = "specialist_reliability"
    ENVIRONMENT_FAILURE = "environment_failure"
    RESOURCE_PATTERN = "resource_pattern"
    APPROVAL_INTERVENTION = "approval_intervention"
    REPLAN_PATTERN = "replan_pattern"
    FALLBACK_PATTERN = "fallback_pattern"
    VERIFICATION_FAILURE = "verification_failure"
    USER_CORRECTION = "user_correction"
    CAPABILITY_GAP = "capability_gap"
    MODEL_SELECTION_MISTAKE = "model_selection_mistake"


class HypothesisStatus(str, Enum):
    PROPOSED = "proposed"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    EXPIRED = "expired"
    SUPERSEDED = "superseded"


class AdjustmentStatus(str, Enum):
    CANDIDATE = "candidate"
    BLOCKED = "blocked"
    APPLIED = "applied"
    DECAYED = "decayed"
    ROLLED_BACK = "rolled_back"
    EXPIRED = "expired"


class LearningDecision(str, Enum):
    BETTER = "better"
    NO_CHANGE = "no_change"
    WORSE = "worse"
    INCONCLUSIVE = "inconclusive"


class FeedbackType(str, Enum):
    CORRECT = "correct"
    INCORRECT = "incorrect"
    USEFUL = "useful"
    NOT_USEFUL = "not_useful"
    PREFERRED_RESULT = "preferred_result"
    REJECTED_RESULT = "rejected_result"
    CORRECTION = "correction"
    RATING = "rating"


class CycleStatus(str, Enum):
    RUNNING = "running"
    COMPLETED = "completed"
    BLOCKED = "blocked"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class AdaptivePolicyLimits:
    minimum_evidence: int = 3
    confidence_threshold: float = .70
    maximum_adjustment: float = .10
    cooldown_seconds: float = 60.0
    decay: float = .95
    max_records_per_cycle: int = 200
    max_patterns_per_cycle: int = 40
    max_hypotheses_per_cycle: int = 40
    max_candidates_per_cycle: int = 16
    max_adjustments_per_hour: int = 8
    exploration_rate: float = 0.0
    exploration_budget: int = 0
    max_exploration_risk: str = RiskLevel.LOW.value
    auto_apply: bool = True
    max_pending_adjustments: int = 32
    task_type_allowlist: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class LearningPattern:
    pattern_id: str
    pattern_type: PatternType
    evidence_ids: list[str]
    frequency: int
    confidence: float
    window_start: str
    window_end: str
    affected_task_types: list[str] = field(default_factory=list)
    affected_versions: list[str] = field(default_factory=list)
    affected_environments: list[str] = field(default_factory=list)
    affected_capabilities: list[str] = field(default_factory=list)
    affected_models: list[str] = field(default_factory=list)
    affected_tools: list[str] = field(default_factory=list)
    affected_specialists: list[str] = field(default_factory=list)
    provenance: dict[str, Any] = field(default_factory=lambda: {"source": "experience_evaluation"})
    trust_classification: str = "observational_evidence"
    lifecycle_state: str = "active"
    architecture_version: str = ADAPTIVE_ARCHITECTURE_VERSION
    created_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self); data["pattern_type"] = self.pattern_type.value; return _safe(data)


@dataclass
class LearningHypothesis:
    hypothesis_id: str
    pattern_id: str
    evidence_ids: list[str]
    expected_effect: str
    confidence: float
    uncertainty: list[str]
    affected_decision: str
    proposed_adjustment: dict[str, Any]
    risk: str
    evaluation_criteria: dict[str, Any]
    rollback_value: Any
    expiration: str | None = None
    cooldown_seconds: float = 60.0
    source_versions: list[str] = field(default_factory=list)
    status: HypothesisStatus = HypothesisStatus.PROPOSED
    provenance: dict[str, Any] = field(default_factory=lambda: {"source": "pattern_detector"})
    architecture_version: str = ADAPTIVE_ARCHITECTURE_VERSION
    created_at: str = field(default_factory=utc_now)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self); data["status"] = self.status.value; return _safe(data)


@dataclass
class AdaptivePolicy:
    policy_id: str
    name: str
    version: str = "1.0"
    enabled: bool = True
    lifecycle_state: str = "active"
    allowed_decisions: list[str] = field(default_factory=lambda: ["model", "fallback", "tool", "capability", "specialist", "strategy", "recovery", "context", "resource_recommendation"])
    limits: AdaptivePolicyLimits = field(default_factory=AdaptivePolicyLimits)
    architecture_version: str = ADAPTIVE_ARCHITECTURE_VERSION
    provenance: dict[str, Any] = field(default_factory=lambda: {"source": "system"})
    created_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)

    def validate(self) -> list[str]:
        errors: list[str] = []
        if not self.policy_id or not self.name or not self.version: errors.append("policy identity and version are required")
        if _text_has_protected(self.allowed_decisions) or _text_has_protected(self.name): errors.append("adaptive policy targets a protected authority")
        if self.limits.minimum_evidence < 1 or self.limits.maximum_adjustment <= 0 or self.limits.maximum_adjustment > 1: errors.append("adaptive policy limits are invalid")
        if self.limits.confidence_threshold < 0 or self.limits.confidence_threshold > 1: errors.append("confidence threshold is invalid")
        return errors

    def to_dict(self) -> dict[str, Any]:
        return _safe(asdict(self))


@dataclass
class AdaptiveAdjustmentCandidate:
    adjustment_id: str
    hypothesis_id: str
    policy_id: str
    affected_component: str
    parameter: str
    baseline_value: float
    proposed_value: float
    adjustment_magnitude: float
    reason: str
    expected_benefit: str
    confidence: float
    risk: str
    source_evidence: list[str]
    evaluator_version: str
    architecture_version: str = ADAPTIVE_ARCHITECTURE_VERSION
    rollback_value: float | None = None
    status: AdjustmentStatus = AdjustmentStatus.CANDIDATE
    expiration: str | None = None
    provenance: dict[str, Any] = field(default_factory=lambda: {"source": "learning_hypothesis"})
    integrity_hash: str = ""
    created_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        if not self.integrity_hash:
            self.integrity_hash = _hash({"id": self.adjustment_id, "component": self.affected_component, "parameter": self.parameter, "baseline": self.baseline_value, "proposed": self.proposed_value, "evidence": self.source_evidence, "architecture": self.architecture_version})

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self); data["status"] = self.status.value; return _safe(data)


@dataclass
class AdjustmentEvaluation:
    evaluation_id: str
    adjustment_id: str
    decision: LearningDecision
    baseline_metrics: dict[str, Any]
    adapted_metrics: dict[str, Any]
    metric_deltas: dict[str, Any]
    safety_results: dict[str, Any]
    explanation: list[str]
    confidence: float
    rollback_triggered: bool = False
    evaluator_version: str = "adaptive-evaluator-v1"
    architecture_version: str = ADAPTIVE_ARCHITECTURE_VERSION
    created_at: str = field(default_factory=utc_now)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self); data["decision"] = self.decision.value; return _safe(data)


@dataclass
class LearningFeedback:
    feedback_id: str
    task_id: str
    feedback_type: FeedbackType
    source: str
    value: Any
    confidence: float
    target_result_id: str | None = None
    correction: str = ""
    related_evaluation_id: str | None = None
    conflicts_with_verification: bool = False
    provenance: dict[str, Any] = field(default_factory=lambda: {"source": "explicit_user_feedback"})
    created_at: str = field(default_factory=utc_now)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self); data["feedback_type"] = self.feedback_type.value; return _safe(data)


@dataclass
class CounterfactualEvaluation:
    counterfactual_id: str
    task_id: str
    alternative_type: str
    baseline_reference: str
    alternative_reference: str
    decision: LearningDecision
    baseline_metrics: dict[str, Any]
    alternative_metrics: dict[str, Any]
    evidence_ids: list[str]
    explanation: list[str]
    executed: bool = False
    advisory: bool = True
    provenance: dict[str, Any] = field(default_factory=lambda: {"source": "historical_evidence"})
    created_at: str = field(default_factory=utc_now)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self); data["decision"] = self.decision.value; return _safe(data)


@dataclass
class LearningConflict:
    conflict_id: str
    target_type: str
    target_id: str
    learned_value: Any
    authoritative_value: Any
    source_evidence: list[str]
    reason: str
    status: str = "authoritative_state_wins"
    provenance: dict[str, Any] = field(default_factory=lambda: {"source": "conflict_detector"})
    created_at: str = field(default_factory=utc_now)

    def to_dict(self) -> dict[str, Any]: return _safe(asdict(self))


@dataclass
class LearningRollback:
    rollback_id: str
    adjustment_id: str
    previous_value: Any
    applied_value: Any
    status: str = "pending"
    reason: str = ""
    source_evidence: list[str] = field(default_factory=list)
    affected_component: str = ""
    application_timestamp: str = field(default_factory=utc_now)
    rollback_timestamp: str | None = None
    provenance: dict[str, Any] = field(default_factory=lambda: {"source": "adaptive_learning"})
    created_at: str = field(default_factory=utc_now)

    def to_dict(self) -> dict[str, Any]: return _safe(asdict(self))


@dataclass
class LearningCycle:
    cycle_id: str
    status: CycleStatus
    started_at: str = field(default_factory=utc_now)
    completed_at: str | None = None
    patterns_detected: int = 0
    hypotheses_created: int = 0
    candidates_created: int = 0
    adjustments_applied: int = 0
    adjustments_rolled_back: int = 0
    records_consumed: int = 0
    reason: str = ""
    resource_usage: dict[str, Any] = field(default_factory=dict)
    architecture_version: str = ADAPTIVE_ARCHITECTURE_VERSION
    provenance: dict[str, Any] = field(default_factory=lambda: {"source": "bounded_learning_cycle"})

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self); data["status"] = self.status.value; return _safe(data)


class AdaptiveLearningEngine:
    """Persistent, deterministic, bounded learning; it adapts decisions but never becomes an authority."""

    PROTECTED_COMPONENTS = _PROTECTED_TERMS

    def __init__(self, store: SQLiteStore, workspace: Path | None = None, policy: AdaptivePolicy | None = None, model_intelligence: Any | None = None, memory: Any | None = None, evolution_orchestrator: Any | None = None, metamorphosis: Any | None = None):
        self.store = store
        self.workspace = Path(workspace).resolve() if workspace else None
        self.policy = policy or AdaptivePolicy("adaptive-policy-v1", "bounded decision adaptation")
        errors = self.policy.validate()
        if errors: raise ValueError("Invalid adaptive policy: " + "; ".join(errors))
        self.model_intelligence = model_intelligence
        self.memory = memory
        self.evolution_orchestrator = evolution_orchestrator
        self.metamorphosis = metamorphosis
        self.values: dict[tuple[str, str], float] = {}
        self.safe_mode = False
        self.kill_switch = False
        self._lock = threading.RLock()
        self._load_applied_values()
        self.store.save_adaptive_policy(self.policy)

    def status(self) -> dict[str, Any]:
        cycles = self.store.find_learning_cycles(limit=20)
        running = [item for item in cycles if item.get("status") == CycleStatus.RUNNING.value]
        return {"enabled": self.policy.enabled, "safe_mode": self.safe_mode, "kill_switch": self.kill_switch, "running_cycles": len(running), "policy": self.policy.to_dict(), "patterns": len(self.store.find_learning_patterns(limit=1000)), "hypotheses": len(self.store.find_learning_hypotheses(limit=1000)), "adjustments": len(self.store.find_adaptive_adjustments(limit=1000))}

    def record_evidence(self, evidence: Any) -> list[LearningPattern]:
        return self.detect_patterns([evidence])

    def detect_patterns(self, records: Iterable[Any] | None = None) -> list[LearningPattern]:
        if records is None:
            records = self.store.find_experiences(limit=self.policy.limits.max_records_per_cycle)
        bounded = [_payload(item) for item in list(records)[: self.policy.limits.max_records_per_cycle]]
        groups: dict[tuple[PatternType, str], list[dict[str, Any]]] = {}
        for record in bounded:
            for pattern_type, key in self._pattern_keys(record):
                groups.setdefault((pattern_type, key), []).append(record)
        result: list[LearningPattern] = []
        for (pattern_type, key), evidence in sorted(groups.items(), key=lambda pair: (pair[0][0].value, pair[0][1]))[: self.policy.limits.max_patterns_per_cycle]:
            if len(evidence) < self.policy.limits.minimum_evidence: continue
            ids = [str(item.get("experience_id") or item.get("evaluation_id") or item.get("task_id") or _hash(item)[:12]) for item in evidence[:64]]
            times = [item.get("timestamp") or item.get("created_at") for item in evidence if item.get("timestamp") or item.get("created_at")]
            pattern = LearningPattern(new_id("pattern"), pattern_type, list(dict.fromkeys(ids)), len(evidence), min(.99, .45 + len(evidence) * .08), min(times) if times else utc_now(), max(times) if times else utc_now(), sorted({str(item.get("task_type", "general")) for item in evidence}), sorted({str(item.get("agent_version", "")) for item in evidence if item.get("agent_version")}), sorted({str(item.get("environment_version", item.get("environment", ""))) for item in evidence if item.get("environment_version") or item.get("environment")}), sorted({str(x) for item in evidence for x in (item.get("capabilities", []) if isinstance(item.get("capabilities", []), list) else [])}), sorted({str(item.get("model_identifier", item.get("model_id", ""))) for item in evidence if item.get("model_identifier") or item.get("model_id")}), sorted({str(x) for item in evidence for x in (item.get("selected_tools", []) if isinstance(item.get("selected_tools", []), list) else [])}), sorted({str(item.get("specialist_id", "")) for item in evidence if item.get("specialist_id")}), {"source": "bounded_experience_evaluation", "key": key}, "observational_evidence")
            self.store.save_learning_pattern(pattern); self._emit(EventType.LEARNING_PATTERN_DETECTED, {"pattern": pattern.to_dict()})
            result.append(pattern)
        return result

    def _pattern_keys(self, record: dict[str, Any]) -> list[tuple[PatternType, str]]:
        outcome = str(record.get("final_outcome", record.get("outcome", ""))).lower()
        task_type = str(record.get("task_type", "general")); strategy = str(record.get("selected_strategy", record.get("strategy", "")))
        keys: list[tuple[PatternType, str]] = []
        if outcome in {"failure", "failed", "timeout", "aborted", "blocked"}: keys.append((PatternType.REPEATED_TASK_FAILURE, task_type))
        if outcome in {"success", "succeeded", "partial_success"} and strategy: keys.append((PatternType.REPEATED_SUCCESSFUL_STRATEGY, f"{task_type}:{strategy}"))
        if record.get("recovery_attempts"): keys.append((PatternType.REPEATED_RECOVERY, task_type))
        if record.get("model_failures") or record.get("model_fallbacks"): keys.append((PatternType.MODEL_RELIABILITY, str(record.get("model_identifier", "unknown"))))
        if record.get("specialist_conflicts") or record.get("specialist_tasks"): keys.append((PatternType.SPECIALIST_RELIABILITY, task_type))
        tools = record.get("selected_tools", [])
        if tools and (record.get("failures") or outcome in {"failure", "timeout"}): keys.extend((PatternType.TOOL_RELIABILITY, str(tool)) for tool in tools[:8])
        failures = _canonical(record.get("failures", [])).lower()
        if any(token in failures for token in ("environment", "workspace", "filesystem")): keys.append((PatternType.ENVIRONMENT_FAILURE, task_type))
        if any(token in failures for token in ("timeout", "resource", "memory", "cpu")): keys.append((PatternType.RESOURCE_PATTERN, task_type))
        if record.get("approval_events"): keys.append((PatternType.APPROVAL_INTERVENTION, task_type))
        if any(str(item.get("action", "")) == "replan" for item in record.get("recovery_attempts", []) if isinstance(item, Mapping)): keys.append((PatternType.REPLAN_PATTERN, task_type))
        if record.get("model_fallbacks"): keys.append((PatternType.FALLBACK_PATTERN, task_type))
        verification = record.get("verification_result")
        if isinstance(verification, Mapping) and verification and not bool(verification.get("success", False)): keys.append((PatternType.VERIFICATION_FAILURE, task_type))
        if record.get("user_feedback") or record.get("corrections"): keys.append((PatternType.USER_CORRECTION, task_type))
        capability = record.get("capability_selection", [])
        if any(isinstance(item, Mapping) and item.get("availability") not in {"capability_available", "capability_partial"} for item in capability): keys.append((PatternType.CAPABILITY_GAP, task_type))
        if record.get("model_selections") and record.get("model_failures"): keys.append((PatternType.MODEL_SELECTION_MISTAKE, task_type))
        return keys

    def generate_hypotheses(self, patterns: Sequence[LearningPattern] | None = None) -> list[LearningHypothesis]:
        patterns = list(patterns if patterns is not None else self.detect_patterns())[: self.policy.limits.max_hypotheses_per_cycle]
        result: list[LearningHypothesis] = []
        for pattern in patterns:
            decision = self._decision_for_pattern(pattern)
            adjustment = {"decision": decision, "direction": "increase" if pattern.pattern_type is PatternType.REPEATED_SUCCESSFUL_STRATEGY else "decrease", "magnitude": min(self.policy.limits.maximum_adjustment, .05), "pattern_type": pattern.pattern_type.value}
            expiration = None if self.policy.limits.cooldown_seconds <= 0 else (datetime.now(timezone.utc) + timedelta(seconds=self.policy.limits.cooldown_seconds * 4)).isoformat()
            hypothesis = LearningHypothesis(new_id("hypothesis"), pattern.pattern_id, pattern.evidence_ids, self._expected_effect(pattern), max(.0, pattern.confidence - .05), ["correlation does not prove causation", "current health, policy, and verification remain authoritative"], decision, adjustment, RiskLevel.LOW.value, {"minimum_success_delta": .05, "minimum_verification_rate": 0.8, "same_benchmark": True}, 0.0, expiration, self.policy.limits.cooldown_seconds, pattern.affected_versions, provenance={"source": "pattern_detector", "pattern_id": pattern.pattern_id})
            self.store.save_learning_hypothesis(hypothesis); self._emit(EventType.LEARNING_HYPOTHESIS_CREATED, {"hypothesis": hypothesis.to_dict()}); result.append(hypothesis)
        return result

    @staticmethod
    def _decision_for_pattern(pattern: LearningPattern) -> str:
        return {PatternType.REPEATED_SUCCESSFUL_STRATEGY: "strategy", PatternType.MODEL_RELIABILITY: "model", PatternType.MODEL_SELECTION_MISTAKE: "model", PatternType.TOOL_RELIABILITY: "tool", PatternType.SPECIALIST_RELIABILITY: "specialist", PatternType.CAPABILITY_GAP: "capability", PatternType.REPEATED_RECOVERY: "recovery"}.get(pattern.pattern_type, "strategy")

    @staticmethod
    def _expected_effect(pattern: LearningPattern) -> str:
        if pattern.pattern_type is PatternType.REPEATED_SUCCESSFUL_STRATEGY: return "The repeated successful decision should receive a small bounded preference increase."
        return "The repeated negative evidence should reduce preference or route the issue to bounded recovery without changing safety policy."

    def propose_adjustment(self, hypothesis: LearningHypothesis, baseline_value: float = 0.0, proposed_value: float | None = None, parameter: str = "preference") -> AdaptiveAdjustmentCandidate:
        if _text_has_protected(hypothesis.affected_decision) or _text_has_protected(hypothesis.proposed_adjustment):
            return self._blocked_candidate(hypothesis, baseline_value, proposed_value or baseline_value, parameter, "protected authority cannot be adaptively changed")
        magnitude = min(self.policy.limits.maximum_adjustment, float(hypothesis.proposed_adjustment.get("magnitude", .05)))
        direction = 1 if hypothesis.proposed_adjustment.get("direction") == "increase" else -1
        proposed = baseline_value + direction * magnitude if proposed_value is None else float(proposed_value)
        if abs(proposed - baseline_value) > self.policy.limits.maximum_adjustment or hypothesis.confidence < self.policy.limits.confidence_threshold or len(hypothesis.evidence_ids) < self.policy.limits.minimum_evidence or hypothesis.risk in {RiskLevel.HIGH.value, RiskLevel.CRITICAL.value}:
            return self._blocked_candidate(hypothesis, baseline_value, proposed, parameter, "adaptive evidence threshold, confidence, magnitude, or risk gate failed")
        component = f"{hypothesis.affected_decision}:{hypothesis.pattern_id}"
        candidate = AdaptiveAdjustmentCandidate(new_id("adaptive_adjustment"), hypothesis.hypothesis_id, self.policy.policy_id, component, parameter, baseline_value, proposed, abs(proposed - baseline_value), hypothesis.expected_effect, "improve measured decision quality without changing authority", hypothesis.confidence, hypothesis.risk, hypothesis.evidence_ids[:64], "adaptive-evaluator-v1", rollback_value=baseline_value, expiration=hypothesis.expiration)
        self.store.save_adaptive_adjustment(candidate); self._emit(EventType.ADAPTIVE_ADJUSTMENT_PROPOSED, {"adjustment": candidate.to_dict()}); return candidate

    def _blocked_candidate(self, hypothesis: LearningHypothesis, baseline: float, proposed: float, parameter: str, reason: str) -> AdaptiveAdjustmentCandidate:
        candidate = AdaptiveAdjustmentCandidate(new_id("adaptive_adjustment"), hypothesis.hypothesis_id, self.policy.policy_id, hypothesis.affected_decision, parameter, baseline, proposed, abs(proposed - baseline), reason, "none", hypothesis.confidence, hypothesis.risk, hypothesis.evidence_ids[:64], "adaptive-evaluator-v1", rollback_value=baseline, status=AdjustmentStatus.BLOCKED, expiration=hypothesis.expiration)
        self.store.save_adaptive_adjustment(candidate); self._emit(EventType.ADAPTIVE_ADJUSTMENT_BLOCKED, {"adjustment": candidate.to_dict()}); return candidate

    def apply_adjustment(self, candidate: AdaptiveAdjustmentCandidate | str) -> AdaptiveAdjustmentCandidate:
        with self._lock:
            if isinstance(candidate, str): candidate = self._candidate_from_row(self.store.adaptive_adjustment_by_id(candidate))
            if candidate is None: raise KeyError("adaptive adjustment")
            if candidate.status is not AdjustmentStatus.CANDIDATE: return candidate
            blocked = self._application_block_reason(candidate)
            if blocked:
                candidate.status = AdjustmentStatus.BLOCKED; candidate.reason = blocked; candidate.updated_at = utc_now(); self.store.save_adaptive_adjustment(candidate); self._emit(EventType.ADAPTIVE_ADJUSTMENT_BLOCKED, {"adjustment": candidate.to_dict()}); return candidate
            candidate.status = AdjustmentStatus.APPLIED; candidate.updated_at = utc_now(); self.values[(candidate.affected_component, candidate.parameter)] = candidate.proposed_value; self.store.save_adaptive_adjustment(candidate)
            rollback = LearningRollback(new_id("learning_rollback"), candidate.adjustment_id, candidate.rollback_value, candidate.proposed_value, "active", "automatic checkpoint before adaptive application", candidate.source_evidence, candidate.affected_component)
            self.store.save_learning_rollback(rollback); self._emit(EventType.ADAPTIVE_ADJUSTMENT_APPLIED, {"adjustment": candidate.to_dict(), "rollback_id": rollback.rollback_id})
            if self.memory and hasattr(self.memory, "capture_learning"):
                try: self.memory.capture_learning({"adjustment_id": candidate.adjustment_id, "affected_component": candidate.affected_component, "parameter": candidate.parameter, "status": candidate.status.value, "confidence": candidate.confidence, "risk": candidate.risk, "evidence_ids": candidate.source_evidence[:32], "bounded": True, "executable": False})
                except Exception: pass
            if self.model_intelligence and candidate.affected_component.startswith("model:"):
                model_id = candidate.affected_component.split(":", 1)[1]
                self.model_intelligence.learning.values[(f"model:{model_id}", candidate.parameter)] = candidate.proposed_value
            return candidate

    def _application_block_reason(self, candidate: AdaptiveAdjustmentCandidate) -> str:
        if self.kill_switch: return "learning application blocked by kill switch"
        if self.safe_mode: return "learning application is paused in safe mode"
        if not self.policy.enabled: return "adaptive policy is disabled"
        if _text_has_protected(candidate.affected_component + " " + candidate.parameter): return "protected authority cannot be adaptively changed"
        expires = _now_dt(candidate.expiration)
        if expires and datetime.now(timezone.utc) >= expires: return "adaptive adjustment candidate has expired"
        if abs(candidate.proposed_value - candidate.baseline_value) > self.policy.limits.maximum_adjustment: return "adjustment exceeds maximum magnitude"
        if candidate.risk in {RiskLevel.HIGH.value, RiskLevel.CRITICAL.value}: return "high-risk learning requires explicit governed review"
        cutoff = datetime.now(timezone.utc) - timedelta(hours=1)
        recent = [item for item in self.store.find_adaptive_adjustments(status=AdjustmentStatus.APPLIED.value, limit=self.policy.limits.max_pending_adjustments) if (_now_dt(item.get("created_at")) or datetime.min.replace(tzinfo=timezone.utc)) >= cutoff]
        if len(recent) >= self.policy.limits.max_adjustments_per_hour: return "adaptive adjustment rate limit reached"
        return ""

    def evaluate_adjustment(self, candidate: AdaptiveAdjustmentCandidate | str, baseline_metrics: Mapping[str, Any], adapted_metrics: Mapping[str, Any], safety_results: Mapping[str, Any] | None = None, feedback_metrics: Mapping[str, Any] | None = None) -> AdjustmentEvaluation:
        if isinstance(candidate, str): candidate = self._candidate_from_row(self.store.adaptive_adjustment_by_id(candidate))
        if candidate is None: raise KeyError("adaptive adjustment")
        baseline = dict(baseline_metrics); adapted = dict(adapted_metrics); safety = dict(safety_results or {})
        deltas = {key: float(adapted.get(key, 0)) - float(baseline.get(key, 0)) for key in set(baseline) | set(adapted) if isinstance(adapted.get(key, baseline.get(key, 0)), (int, float)) and isinstance(baseline.get(key, adapted.get(key, 0)), (int, float))}
        score_delta = sum(deltas.get(key, 0.0) for key in ("success_rate", "verification_rate", "evaluation_score", "reliability")) / 4
        safety_ok = all(bool(value) for value in safety.values()) if safety else True
        if not safety_ok: decision = LearningDecision.WORSE
        elif score_delta >= .05: decision = LearningDecision.BETTER
        elif score_delta <= -.05: decision = LearningDecision.WORSE
        elif not deltas: decision = LearningDecision.INCONCLUSIVE
        else: decision = LearningDecision.NO_CHANGE
        explanation = [f"aggregate quality delta={score_delta:.3f}", f"safety={'passed' if safety_ok else 'failed'}", "system verification and governance remain authoritative"]
        if feedback_metrics: explanation.append("explicit feedback is treated as supporting evidence only")
        evaluation = AdjustmentEvaluation(new_id("adjustment_evaluation"), candidate.adjustment_id, decision, baseline, adapted, deltas, safety, explanation, min(1.0, .55 + len(candidate.source_evidence) * .05), decision is LearningDecision.WORSE)
        self.store.save_adjustment_evaluation(evaluation); self._emit(EventType.ADJUSTMENT_EVALUATED, {"evaluation": evaluation.to_dict()})
        if decision is LearningDecision.WORSE: self.rollback(candidate, "adapted performance was worse or safety failed")
        elif decision is LearningDecision.NO_CHANGE: self.decay_adjustment(candidate)
        return evaluation

    def rollback(self, candidate: AdaptiveAdjustmentCandidate | str, reason: str = "explicit governed rollback") -> LearningRollback:
        with self._lock:
            if isinstance(candidate, str): candidate = self._candidate_from_row(self.store.adaptive_adjustment_by_id(candidate))
            if candidate is None: raise KeyError("adaptive adjustment")
            self._emit(EventType.LEARNING_ROLLBACK_STARTED, {"adjustment_id": candidate.adjustment_id, "reason": reason})
            self.values[(candidate.affected_component, candidate.parameter)] = float(candidate.rollback_value if candidate.rollback_value is not None else candidate.baseline_value)
            candidate.status = AdjustmentStatus.ROLLED_BACK; candidate.updated_at = utc_now(); self.store.save_adaptive_adjustment(candidate)
            row = next((item for item in self.store.find_learning_rollbacks(candidate.adjustment_id, limit=10) if item.get("status") == "active"), None)
            rollback = LearningRollback(row["payload"]["rollback_id"] if row else new_id("learning_rollback"), candidate.adjustment_id, candidate.rollback_value, candidate.proposed_value, "completed", reason, candidate.source_evidence, candidate.affected_component, rollback_timestamp=utc_now())
            self.store.save_learning_rollback(rollback); self._emit(EventType.LEARNING_ROLLBACK_COMPLETED, {"rollback": rollback.to_dict()}); return rollback

    rollback_adjustment = rollback

    def decay_adjustment(self, candidate: AdaptiveAdjustmentCandidate | str) -> AdaptiveAdjustmentCandidate:
        if isinstance(candidate, str): candidate = self._candidate_from_row(self.store.adaptive_adjustment_by_id(candidate))
        if candidate is None: raise KeyError("adaptive adjustment")
        current = self.values.get((candidate.affected_component, candidate.parameter), candidate.proposed_value)
        self.values[(candidate.affected_component, candidate.parameter)] = current * self.policy.limits.decay
        candidate.status = AdjustmentStatus.DECAYED; candidate.updated_at = utc_now(); self.store.save_adaptive_adjustment(candidate); self._emit(EventType.LEARNING_DECAY_APPLIED, {"adjustment": candidate.to_dict(), "decay": self.policy.limits.decay}); return candidate

    def decay(self) -> int:
        for key in list(self.values): self.values[key] *= max(0.0, min(1.0, self.policy.limits.decay))
        self._emit(EventType.LEARNING_DECAY_APPLIED, {"count": len(self.values), "decay": self.policy.limits.decay}); return len(self.values)

    def score(self, component: str, parameter: str = "preference") -> float:
        return self.values.get((component, parameter), 0.0)

    def adaptive_decision(self, decision_type: str, baseline: Any, candidates: Sequence[Any], evidence_ids: Sequence[str] = (), task_type: str = "general", risk: RiskLevel | str = RiskLevel.LOW, fallback: Any = None) -> dict[str, Any]:
        decision_type = str(decision_type).lower(); risk_value = getattr(risk, "value", str(risk))
        if _text_has_protected(decision_type) or _text_has_protected(candidates):
            return {"decision_type": decision_type, "baseline_decision": baseline, "selected_decision": baseline, "learned_adjustment": 0.0, "evidence": list(evidence_ids)[:64], "confidence": 0.0, "policy": self.policy.policy_id, "adjustment_magnitude": 0.0, "reason": "protected authority or unsafe learned content was rejected", "fallback": fallback, "bounded": True, "governance_required": True}
        if not candidates:
            return {"decision_type": decision_type, "baseline_decision": baseline, "selected_decision": baseline, "learned_adjustment": 0.0, "evidence": list(evidence_ids)[:64], "confidence": 0.0, "policy": self.policy.policy_id, "adjustment_magnitude": 0.0, "reason": "no eligible candidate; baseline retained", "fallback": fallback, "bounded": True, "governance_required": True}
        ranked = []
        for candidate in list(candidates)[:16]:
            identity = str(candidate.get("id", candidate.get("model_id", candidate.get("name", candidate))) if isinstance(candidate, Mapping) else candidate)
            ranked.append((self.score(f"{decision_type}:{identity}", "preference"), identity, candidate))
        ranked.sort(key=lambda item: (-item[0], item[1]))
        selected = ranked[0][2] if ranked[0][0] > 0 else baseline
        adjustment = ranked[0][0] if selected is not baseline else 0.0
        return {"decision_type": decision_type, "task_type": task_type, "risk": risk_value, "baseline_decision": baseline, "selected_decision": selected, "learned_adjustment": adjustment, "evidence": list(evidence_ids)[:64], "confidence": min(1.0, .5 + len(evidence_ids) * .05) if evidence_ids else 0.0, "policy": self.policy.policy_id, "adjustment_magnitude": abs(adjustment), "reason": "bounded persisted preference used only for eligible decision ranking" if adjustment else "baseline retained because no positive learned preference was eligible", "fallback": fallback, "bounded": True, "governance_required": True, "execution_authority": "kernel"}

    route_adaptively = adaptive_decision
    select_adaptively = adaptive_decision

    def record_feedback(self, task_id: str, feedback_type: FeedbackType | str, value: Any, source: str = "user", confidence: float = .7, target_result_id: str | None = None, correction: str = "", related_evaluation_id: str | None = None, verification_value: bool | None = None) -> LearningFeedback:
        feedback_type = FeedbackType(feedback_type); conflicts = verification_value is not None and feedback_type in {FeedbackType.CORRECT, FeedbackType.USEFUL} and not verification_value
        feedback = LearningFeedback(new_id("learning_feedback"), task_id, feedback_type, source, value, max(0.0, min(1.0, confidence)), target_result_id, correction[:2000], related_evaluation_id, conflicts)
        self.store.save_learning_feedback(feedback); self._emit(EventType.LEARNING_FEEDBACK_RECORDED, {"feedback": feedback.to_dict()})
        if conflicts: self.record_conflict("feedback", feedback.feedback_id, value, verification_value, [task_id], "current verification conflicts with user feedback")
        return feedback

    def counterfactual(self, task_id: str, alternative_type: str, baseline: Mapping[str, Any], alternative: Mapping[str, Any], evidence_ids: Sequence[str] = (), baseline_reference: str = "historical_baseline", alternative_reference: str = "historical_alternative") -> CounterfactualEvaluation:
        if _text_has_protected(alternative_type) or _text_has_protected(alternative):
            decision = LearningDecision.INCONCLUSIVE; explanation = ["protected or executable counterfactual is not evaluated"]
        else:
            base_score = self._quality_score(baseline); alt_score = self._quality_score(alternative); delta = alt_score - base_score
            decision = LearningDecision.BETTER if delta >= .05 else LearningDecision.WORSE if delta <= -.05 else LearningDecision.NO_CHANGE if delta == 0 else LearningDecision.INCONCLUSIVE
            explanation = [f"historical quality delta={delta:.3f}", "no production counterfactual action was executed", "result remains advisory"]
        record = CounterfactualEvaluation(new_id("counterfactual"), task_id, alternative_type, baseline_reference, alternative_reference, decision, dict(baseline), dict(alternative), list(evidence_ids)[:64], explanation, False, True)
        self.store.save_counterfactual_evaluation(record); self._emit(EventType.COUNTERFACTUAL_EVALUATED, {"counterfactual": record.to_dict()}); return record

    @staticmethod
    def _quality_score(metrics: Mapping[str, Any]) -> float:
        return sum(float(metrics.get(key, 0.0)) for key in ("success_rate", "verification_rate", "evaluation_score", "reliability")) / 4

    def record_conflict(self, target_type: str, target_id: str, learned_value: Any, authoritative_value: Any, source_evidence: Sequence[str], reason: str) -> LearningConflict:
        conflict = LearningConflict(new_id("learning_conflict"), target_type, target_id, learned_value, authoritative_value, list(source_evidence)[:64], reason)
        self.store.save_learning_conflict(conflict); self._emit(EventType.LEARNING_CONFLICT_DETECTED, {"conflict": conflict.to_dict()})
        if self.memory and hasattr(self.memory, "capture_learning"):
            try: self.memory.capture_learning({"conflict_id": conflict.conflict_id, "target_type": target_type, "target_id": target_id, "status": conflict.status, "reason": reason, "bounded": True, "executable": False})
            except Exception: pass
        return conflict

    def explore(self, task_id: str, task_type: str, risk: RiskLevel | str, eligible_alternatives: Sequence[str], seed: int = 0) -> dict[str, Any]:
        risk_value = getattr(risk, "value", str(risk)); eligible = bool(eligible_alternatives) and risk_value in {RiskLevel.LOW.value, RiskLevel.MEDIUM.value} and self.policy.limits.exploration_rate > 0 and (not self.policy.limits.task_type_allowlist or task_type in self.policy.limits.task_type_allowlist)
        rng = random.Random(seed + sum(ord(c) for c in task_id)); selected = eligible and rng.random() < self.policy.limits.exploration_rate and len(eligible_alternatives) > 1
        result = {"task_id": task_id, "task_type": task_type, "eligible": eligible, "explore": selected, "selected_alternative": eligible_alternatives[1] if selected else (eligible_alternatives[0] if eligible_alternatives else None), "budget": self.policy.limits.exploration_budget, "seed": seed, "bounded": True, "governance_required": True}
        self._emit(EventType.MODEL_EXPLORATION_RECORDED, {"exploration": result}); return result

    def run_cycle(self, records: Iterable[Any] | None = None, resource_budget: int | None = None) -> LearningCycle:
        with self._lock:
            active = [row for row in self.store.find_learning_cycles(status=CycleStatus.RUNNING.value, limit=20) if not row.get("completed_at")]
            if active:
                cycle = LearningCycle(new_id("learning_cycle"), CycleStatus.BLOCKED, reason="another learning cycle is already running"); self.store.save_learning_cycle(cycle); self._emit(EventType.LEARNING_CYCLE_BLOCKED, {"cycle": cycle.to_dict()}); return cycle
            cycle = LearningCycle(new_id("learning_cycle"), CycleStatus.RUNNING); self.store.save_learning_cycle(cycle); self._emit(EventType.LEARNING_CYCLE_STARTED, {"cycle": cycle.to_dict()})
            try:
                bounded_records = list(records)[: min(resource_budget or self.policy.limits.max_records_per_cycle, self.policy.limits.max_records_per_cycle)] if records is not None else list(self.store.find_experiences(limit=self.policy.limits.max_records_per_cycle))
                cycle.records_consumed = len(bounded_records)
                patterns = self.detect_patterns(bounded_records); cycle.patterns_detected = len(patterns)
                hypotheses = self.generate_hypotheses(patterns); cycle.hypotheses_created = len(hypotheses)
                candidates = [self.propose_adjustment(item) for item in hypotheses[: self.policy.limits.max_candidates_per_cycle]]; cycle.candidates_created = len(candidates)
                if self.policy.limits.auto_apply and not self.safe_mode and not self.kill_switch:
                    for candidate in candidates:
                        applied = self.apply_adjustment(candidate)
                        cycle.adjustments_applied += int(applied.status is AdjustmentStatus.APPLIED)
                cycle.status = CycleStatus.COMPLETED; cycle.completed_at = utc_now(); cycle.resource_usage = {"records": cycle.records_consumed, "patterns": cycle.patterns_detected, "hypotheses": cycle.hypotheses_created, "candidates": cycle.candidates_created}; cycle.reason = "bounded observation, pattern detection, hypothesis generation, and governed application"
                self.store.save_learning_cycle(cycle); self._emit(EventType.LEARNING_CYCLE_COMPLETED, {"cycle": cycle.to_dict()}); return cycle
            except Exception as exc:
                cycle.status = CycleStatus.FAILED; cycle.completed_at = utc_now(); cycle.reason = f"{type(exc).__name__}: {exc}"; self.store.save_learning_cycle(cycle); self._emit(EventType.LEARNING_CYCLE_COMPLETED, {"cycle": cycle.to_dict()}); return cycle

    cycle = run_cycle

    def bridge_to_evolution(self, reason: str, affected_component: str, evidence_ids: Sequence[str], structural: bool = False) -> Any:
        if _text_has_protected(affected_component + " " + reason): return {"status": "blocked", "reason": "protected authority cannot be bridged"}
        from .orchestrator import EvolutionOpportunity, OrchestrationPath
        path = OrchestrationPath.METAMORPHOSIS if structural else OrchestrationPath.EVOLUTION
        metadata = {"learning_evidence": list(evidence_ids)[:64], "learning_bridge": True, "governance_required": True, "structural": structural}
        if self.evolution_orchestrator is None:
            return {"status": "evidence_only", "path": path.value, "reason": reason, "metadata": metadata}
        opportunity = EvolutionOpportunity(new_id("opportunity"), list(evidence_ids)[:64], [], reason, max(1, len(evidence_ids)), "medium", ["adaptive_learning"], [affected_component], [], "moderate", path, .65, architecture_version=ADAPTIVE_ARCHITECTURE_VERSION, metadata=metadata)
        item = self.evolution_orchestrator.create_work_item(opportunity); self._emit(EventType.LEARNING_EVOLUTION_EVIDENCE, {"opportunity_id": opportunity.opportunity_id, "path": path.value, "evidence": list(evidence_ids)[:64]}); return item

    learning_to_evolution = bridge_to_evolution

    def set_safe_mode(self, enabled: bool) -> None: self.safe_mode = bool(enabled)
    def activate_kill_switch(self) -> None: self.kill_switch = True
    def clear_kill_switch(self, actor: str = "human") -> None:
        if actor in {"model", "learning", "autonomous", "agent"}: raise PermissionError("adaptive learning cannot clear kill switch")
        self.kill_switch = False

    def _load_applied_values(self) -> None:
        for row in reversed(self.store.find_adaptive_adjustments(status=AdjustmentStatus.APPLIED.value, limit=1000)):
            try:
                payload = row.get("payload", {}); self.values[(payload["affected_component"], payload["parameter"])] = float(payload["proposed_value"])
            except (KeyError, TypeError, ValueError): continue

    @staticmethod
    def _candidate_from_row(row: dict[str, Any] | None) -> AdaptiveAdjustmentCandidate | None:
        if not row: return None
        data = dict(row.get("payload", row)); data["status"] = AdjustmentStatus(data.get("status", AdjustmentStatus.CANDIDATE.value)); return AdaptiveAdjustmentCandidate(**{key: data[key] for key in AdaptiveAdjustmentCandidate.__dataclass_fields__ if key in data})

    def _emit(self, event_type: EventType, payload: dict[str, Any]) -> None:
        try: self.store.append_event(Event("adaptive-learning", event_type, _safe(payload)))
        except Exception: pass


ContinuousLearningEngine = AdaptiveLearningEngine
AdaptiveIntelligence = AdaptiveLearningEngine
