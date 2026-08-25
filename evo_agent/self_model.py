from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
import hashlib
import json
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from .models import Event, EventType, OutcomeType, RiskLevel, new_id, utc_now
from .orchestrator import EvolutionOpportunity, OrchestrationPath
from .storage import SQLiteStore
from .version import __version__

SELF_MODEL_ARCHITECTURE_VERSION = "self-model-v1"
_MAX_TEXT = 2000
_MAX_ITEMS = 64
_PROTECTED_TERMS = {"authorized", "bypass approval", "modify governance", "protected core", "protected_core", "disable the kill switch", "kill_switch", "arbitrary code", "promote myself", "approve myself", "credentials", "bypass verification", "modify production", "unrestricted network"}
_SENSITIVE_KEYS = {"prompt", "prompts", "response", "responses", "output", "outputs", "content", "payload", "messages", "secret", "token", "password", "credential", "private_key", "tool_calls"}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_time(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


def _hash(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str, separators=(",", ":")).encode()).hexdigest()


def _protected(value: Any) -> bool:
    text = json.dumps(value, sort_keys=True, default=str).lower()
    return any(term in text for term in _PROTECTED_TERMS)


def _safe(value: Any, maximum: int = _MAX_TEXT) -> Any:
    if isinstance(value, Mapping):
        return {str(k): "[REDACTED]" if any(term in str(k).lower() for term in _SENSITIVE_KEYS) else _safe(v, maximum) for k, v in list(value.items())[:_MAX_ITEMS]}
    if isinstance(value, list):
        return [_safe(item, maximum) for item in value[:_MAX_ITEMS]]
    if isinstance(value, tuple):
        return [_safe(item, maximum) for item in value[:_MAX_ITEMS]]
    if isinstance(value, str):
        return value[:maximum]
    return value


class SelfKnowledgeCategory(str, Enum):
    KNOWN = "known"
    LIKELY = "likely"
    UNCERTAIN = "uncertain"
    UNKNOWN = "unknown"
    BLOCKED = "blocked"
    UNAVAILABLE = "unavailable"
    DEPRECATED = "deprecated"
    CONFLICTING = "conflicting"


class FreshnessState(str, Enum):
    FRESH = "fresh"
    AGING = "aging"
    STALE = "stale"
    EXPIRED = "expired"
    UNKNOWN = "unknown"


class LifecycleState(str, Enum):
    ACTIVE = "active"
    PROPOSED = "proposed"
    INVALIDATED = "invalidated"
    RESOLVED = "resolved"
    STALE = "stale"
    CONFLICTED = "conflicted"
    ARCHIVED = "archived"


class LimitationType(str, Enum):
    MISSING_CAPABILITY = "missing_capability"
    UNAVAILABLE_TOOL = "unavailable_tool"
    UNAVAILABLE_MODEL = "unavailable_model"
    UNAVAILABLE_PROVIDER = "unavailable_provider"
    INSUFFICIENT_PERMISSION = "insufficient_permission"
    APPROVAL_REQUIRED = "approval_required"
    RESOURCE_LIMITATION = "resource_limitation"
    ENVIRONMENT_INCOMPATIBILITY = "environment_incompatibility"
    SPECIALIST_LIMITATION = "specialist_limitation"
    VERIFICATION_LIMITATION = "verification_limitation"
    REPEATED_FAILURE = "repeated_failure"
    ARCHITECTURE_LIMITATION = "architecture_limitation"
    EXTERNAL_DEPENDENCY_FAILURE = "external_dependency_failure"


class AssumptionValidationStatus(str, Enum):
    UNVALIDATED = "unvalidated"
    VALID = "valid"
    INVALIDATED = "invalidated"
    UNCERTAIN = "uncertain"


class DecisionReadinessState(str, Enum):
    READY = "ready"
    CLARIFICATION_REQUIRED = "clarification_required"
    APPROVAL_REQUIRED = "approval_required"
    CAPABILITY_MISSING = "capability_missing"
    TOOL_UNAVAILABLE = "tool_unavailable"
    MODEL_UNAVAILABLE = "model_unavailable"
    SPECIALIST_REQUIRED = "specialist_required"
    ENVIRONMENT_UNCERTAIN = "environment_uncertain"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"
    RESOURCE_LIMITED = "resource_limited"
    CONFLICTED = "conflicted"
    BLOCKED = "blocked"
    UNSAFE = "unsafe"
    IMPOSSIBLE = "impossible"


class CalibrationState(str, Enum):
    OVERCONFIDENT = "overconfident"
    UNDERCONFIDENT = "underconfident"
    GOOD = "good_calibration"
    INSUFFICIENT = "insufficient_evidence"


@dataclass
class SelfModelClaim:
    claim_id: str
    category: SelfKnowledgeCategory
    subject: str
    value: Any
    source: str
    provenance: dict[str, Any] = field(default_factory=dict)
    confidence: float = 0.0
    timestamp: str = field(default_factory=utc_now)
    created_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)
    architecture_version: str = SELF_MODEL_ARCHITECTURE_VERSION
    environment_id: str = "environment-unknown"
    evidence_ids: list[str] = field(default_factory=list)
    lifecycle_state: str = LifecycleState.ACTIVE.value
    freshness: FreshnessState = FreshnessState.FRESH

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self); data["category"] = self.category.value; data["freshness"] = self.freshness.value; data["value"] = _safe(self.value); return data


@dataclass
class SelfModelSnapshot:
    snapshot_id: str
    agent_identity: str
    active_version: str
    architecture_version: str
    environment_id: str
    status: str
    claims: list[dict[str, Any]] = field(default_factory=list)
    limitations: list[dict[str, Any]] = field(default_factory=list)
    assumptions: list[dict[str, Any]] = field(default_factory=list)
    uncertainties: list[dict[str, Any]] = field(default_factory=list)
    conflicts: list[dict[str, Any]] = field(default_factory=list)
    reliability: dict[str, Any] = field(default_factory=dict)
    constraints: dict[str, Any] = field(default_factory=dict)
    pending_changes: dict[str, Any] = field(default_factory=dict)
    recent_verified_outcomes: list[dict[str, Any]] = field(default_factory=list)
    freshness: FreshnessState = FreshnessState.FRESH
    provenance: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=utc_now)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self); data["freshness"] = self.freshness.value; return _safe(data, 8000)


@dataclass
class SelfModelLimitation:
    limitation_id: str
    limitation_type: LimitationType
    description: str
    evidence_ids: list[str]
    severity: str
    frequency: int
    affected_tasks: list[str]
    affected_versions: list[str]
    environment_id: str
    confidence: float
    recommended_response: str
    lifecycle_state: str = LifecycleState.ACTIVE.value
    provenance: dict[str, Any] = field(default_factory=dict)
    architecture_version: str = SELF_MODEL_ARCHITECTURE_VERSION
    created_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self); data["limitation_type"] = self.limitation_type.value; return _safe(data)


@dataclass
class SelfModelAssumption:
    assumption_id: str
    statement: str
    source: str
    confidence: float
    validation_status: AssumptionValidationStatus
    dependent_task: str
    invalidation_condition: str
    timestamp: str = field(default_factory=utc_now)
    created_at: str = field(default_factory=utc_now)
    architecture_version: str = SELF_MODEL_ARCHITECTURE_VERSION
    environment_id: str = "environment-unknown"
    provenance: dict[str, Any] = field(default_factory=dict)
    lifecycle_state: str = LifecycleState.PROPOSED.value
    evidence_ids: list[str] = field(default_factory=list)
    updated_at: str = field(default_factory=utc_now)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self); data["validation_status"] = self.validation_status.value; return _safe(data)


@dataclass
class SelfModelUncertainty:
    uncertainty_id: str
    uncertainty_type: str
    description: str
    source: str
    confidence: float
    severity: str
    evidence_ids: list[str] = field(default_factory=list)
    architecture_version: str = SELF_MODEL_ARCHITECTURE_VERSION
    environment_id: str = "environment-unknown"
    lifecycle_state: str = LifecycleState.ACTIVE.value
    provenance: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=utc_now)

    def to_dict(self) -> dict[str, Any]: return _safe(asdict(self))


@dataclass
class SelfModelConflict:
    conflict_id: str
    subject: str
    authoritative_value: Any
    competing_value: Any
    sources: list[str]
    resolution: str
    status: str = LifecycleState.CONFLICTED.value
    architecture_version: str = SELF_MODEL_ARCHITECTURE_VERSION
    environment_id: str = "environment-unknown"
    evidence_ids: list[str] = field(default_factory=list)
    created_at: str = field(default_factory=utc_now)

    def to_dict(self) -> dict[str, Any]: return _safe(asdict(self))


@dataclass
class DecisionReadinessAssessment:
    readiness_id: str
    goal_id: str
    state: DecisionReadinessState
    recommendation: str
    reason: str
    confidence: float
    known: list[str] = field(default_factory=list)
    unknown: list[str] = field(default_factory=list)
    assumptions: list[str] = field(default_factory=list)
    uncertainties: list[str] = field(default_factory=list)
    conflicts: list[str] = field(default_factory=list)
    evidence_gaps: list[str] = field(default_factory=list)
    clarification_question: str = ""
    escalation_required: bool = False
    approval_required: bool = False
    provenance: dict[str, Any] = field(default_factory=dict)
    architecture_version: str = SELF_MODEL_ARCHITECTURE_VERSION
    environment_id: str = "environment-unknown"
    created_at: str = field(default_factory=utc_now)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self); data["state"] = self.state.value; return _safe(data)


@dataclass
class MetaReasoningRecord:
    record_id: str
    goal_id: str
    recommendation: str
    reasoning: list[str]
    readiness_id: str
    confidence: float
    questions: list[str] = field(default_factory=list)
    escalation: dict[str, Any] = field(default_factory=dict)
    safer_alternatives: list[str] = field(default_factory=list)
    provenance: dict[str, Any] = field(default_factory=dict)
    architecture_version: str = SELF_MODEL_ARCHITECTURE_VERSION
    environment_id: str = "environment-unknown"
    created_at: str = field(default_factory=utc_now)

    def to_dict(self) -> dict[str, Any]: return _safe(asdict(self))


@dataclass
class ConfidenceCalibration:
    calibration_id: str
    subject: str
    predicted_confidence: float
    actual_verified: bool
    calibration_state: CalibrationState
    error: float
    evidence_ids: list[str] = field(default_factory=list)
    explanation: str = ""
    architecture_version: str = SELF_MODEL_ARCHITECTURE_VERSION
    environment_id: str = "environment-unknown"
    created_at: str = field(default_factory=utc_now)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self); data["calibration_state"] = self.calibration_state.value; return _safe(data)


@dataclass
class SelfReflection:
    reflection_id: str
    task_id: str
    attempted: str
    succeeded: list[str]
    failed: list[str]
    failure_reasons: list[str]
    correct_assumptions: list[str]
    wrong_assumptions: list[str]
    strategy: str
    strategy_appropriate: bool | None
    selected_components_appropriate: dict[str, bool | None]
    predicted_confidence: float | None
    actual_verified: bool
    what_to_remember: list[str]
    learning_evidence: bool
    evolution_evidence: bool
    provenance: dict[str, Any] = field(default_factory=dict)
    architecture_version: str = SELF_MODEL_ARCHITECTURE_VERSION
    environment_id: str = "environment-unknown"
    created_at: str = field(default_factory=utc_now)

    def to_dict(self) -> dict[str, Any]: return _safe(asdict(self))


@dataclass
class SelfDiagnostics:
    diagnostic_id: str
    status: str
    checks: dict[str, Any]
    failures: list[str]
    architecture_version: str = SELF_MODEL_ARCHITECTURE_VERSION
    environment_id: str = "environment-unknown"
    provenance: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=utc_now)

    def to_dict(self) -> dict[str, Any]: return _safe(asdict(self))


class SelfModelEngine:
    """Persistent operational self-model. It describes Evo and never grants authority."""

    ENGINE_VERSION = "self-model-engine-v1"

    def __init__(self, store: SQLiteStore, workspace: Path | None = None, capability_intelligence: Any | None = None, model_intelligence: Any | None = None, specialist_delegation: Any | None = None, external_integrations: Any | None = None, world_intelligence: Any | None = None, adaptive_learning: Any | None = None, runtime: Any | None = None, evolution_orchestrator: Any | None = None, memory: Any | None = None, architecture_version: str = ""):
        self.store = store
        self.workspace = Path(workspace).expanduser().resolve() if workspace else None
        self.capability_intelligence = capability_intelligence
        self.model_intelligence = model_intelligence
        self.specialist_delegation = specialist_delegation
        self.external_integrations = external_integrations
        self.world_intelligence = world_intelligence
        self.adaptive_learning = adaptive_learning
        self.runtime = runtime
        self.evolution_orchestrator = evolution_orchestrator
        self.memory = memory
        self.architecture_version = architecture_version or SELF_MODEL_ARCHITECTURE_VERSION
        self.safe_mode = False
        self.kill_switch = False
        self._snapshot: SelfModelSnapshot | None = None
        latest = self.store.latest_self_model_snapshot()
        if latest and isinstance(latest.get("payload"), Mapping):
            self._snapshot = self._snapshot_from_payload(latest["payload"])

    @property
    def environment_id(self) -> str:
        for source in (self.world_intelligence, self.runtime):
            for attr in ("environment_id", "current_environment"):
                value = getattr(source, attr, None) if source else None
                if value: return str(value)
            record = getattr(source, "runtime_record", None) if source else None
            value = getattr(record, "current_environment", None) if record else None
            if value: return str(value)
        return "environment-unknown"

    def _emit(self, event_type: EventType, payload: Mapping[str, Any], task_id: str = "self-model") -> None:
        try: self.store.append_event(Event(task_id, event_type, _safe(dict(payload))))
        except Exception: pass

    @staticmethod
    def _items(source: Any, names: Sequence[str]) -> list[Any]:
        for name in names:
            value = getattr(source, name, None) if source is not None else None
            if value is None: continue
            if callable(value):
                try: value = value()
                except Exception: continue
            if hasattr(value, "list") and callable(value.list):
                try: value = value.list()
                except Exception: continue
            if isinstance(value, (list, tuple, set)): return list(value)[:_MAX_ITEMS]
        return []

    def _registry_snapshot(self) -> dict[str, list[Any]]:
        capabilities = self._items(self.capability_intelligence, ("capabilities", "capability_registry"))
        tools = self._items(self.capability_intelligence, ("tools", "tool_registry"))
        if not tools and self.capability_intelligence is not None:
            tools = self._items(getattr(self.capability_intelligence, "tools", None), ("list_tools", "list"))
        models = self._items(self.model_intelligence, ("list_models", "registry"))
        specialists = self._items(getattr(self.specialist_delegation, "registry", None), ("list",))
        integrations = self._items(self.external_integrations, ("list_integrations", "integrations"))
        return {"capabilities": capabilities, "tools": tools, "specialists": specialists, "models": models, "integrations": integrations}

    @staticmethod
    def _identity(item: Any, default: str = "unknown") -> str:
        if isinstance(item, Mapping):
            for key in ("capability_id", "tool_id", "model_id", "specialist_id", "integration_id", "id", "name"):
                if item.get(key): return str(item[key])
        for attr in ("capability_id", "tool_id", "model_id", "specialist_id", "integration_id", "id", "name"):
            value = getattr(item, attr, None)
            if value: return str(value)
        return default

    @staticmethod
    def _state(item: Any) -> str:
        if isinstance(item, Mapping):
            value = item.get("status", item.get("lifecycle_state", item.get("state", "active")))
        else:
            value = getattr(item, "status", getattr(item, "lifecycle_state", getattr(item, "state", "active")))
        return getattr(value, "value", str(value)).lower()

    def _claim(self, subject: str, value: Any, source: str, category: SelfKnowledgeCategory, confidence: float, evidence_ids: Sequence[str] = (), lifecycle_state: str = LifecycleState.ACTIVE.value) -> SelfModelClaim:
        if _protected(value) and source not in {"governance", "kernel", "verifier"}:
            category = SelfKnowledgeCategory.UNCERTAIN; confidence = 0.0; value = {"non_authoritative": True, "reason": "untrusted self-model content"}
        claim = SelfModelClaim(new_id("self_claim"), category, subject, _safe(value), source, {"engine": self.ENGINE_VERSION, "source_authoritative": source in {"capability_registry", "model_registry", "specialist_registry", "external_registry", "world", "runtime", "governance", "kernel", "verifier"}}, max(0.0, min(1.0, confidence)), evidence_ids=list(evidence_ids)[:_MAX_ITEMS], architecture_version=self.architecture_version, environment_id=self.environment_id, lifecycle_state=lifecycle_state)
        self.store.save_self_model_claim(claim); self._emit(EventType.SELF_MODEL_CLAIM_RECORDED, {"claim": claim.to_dict()}); return claim

    def refresh(self, reason: str = "bounded self-model refresh", evidence: Mapping[str, Any] | None = None) -> SelfModelSnapshot:
        if self.kill_switch:
            raise RuntimeError("self-model refresh blocked by kill switch")
        registries = self._registry_snapshot(); claims: list[SelfModelClaim] = []
        claims.append(self._claim("agent_identity", "Evo Agent", "agent_manifest", SelfKnowledgeCategory.KNOWN, 1.0))
        claims.append(self._claim("active_version", __version__, "version_manifest", SelfKnowledgeCategory.KNOWN, 1.0))
        claims.append(self._claim("architecture_version", self.architecture_version, "architecture_manifest", SelfKnowledgeCategory.KNOWN, 1.0))
        for kind, items in registries.items():
            values = [{"id": self._identity(item), "state": self._state(item)} for item in items]
            category = SelfKnowledgeCategory.KNOWN if items else SelfKnowledgeCategory.UNKNOWN
            claims.append(self._claim(kind, values, f"{kind[:-1]}_registry", category, 1.0 if items else 0.0))
        if self.adaptive_learning is not None:
            policy = getattr(self.adaptive_learning, "policy", None)
            claims.append(self._claim("learned_policies", policy.to_dict() if policy and hasattr(policy, "to_dict") else {"available": True}, "learning", SelfKnowledgeCategory.KNOWN if policy else SelfKnowledgeCategory.UNKNOWN, .9 if policy else 0.0))
            claims.append(self._claim("active_learning_adjustments", getattr(self.adaptive_learning, "status", lambda: {})(), "learning", SelfKnowledgeCategory.LIKELY, .7))
        if self.runtime is not None:
            mode = getattr(getattr(self.runtime, "state", None), "value", getattr(self.runtime, "state", "unknown"))
            claims.append(self._claim("current_operating_mode", mode, "runtime", SelfKnowledgeCategory.KNOWN, .95))
            claims.append(self._claim("resource_limits", getattr(getattr(self.runtime, "limits", None), "to_dict", lambda: {})(), "runtime", SelfKnowledgeCategory.KNOWN, .95))
        if self.world_intelligence is not None:
            world_stats = getattr(self.world_intelligence, "stats", lambda: {})()
            claims.append(self._claim("environment_compatibility", world_stats, "world", SelfKnowledgeCategory.KNOWN, .8))
        limitations = self.detect_limitations(registries, evidence)
        assumptions = list(self.store.find_self_model_assumptions(limit=100))
        uncertainties = list(self.store.find_self_model_uncertainty(limit=100))
        conflicts = list(self.store.find_self_model_conflicts(limit=100))
        recent = []
        try: recent = [row.get("payload", row) for row in self.store.find_experiences(limit=10) if row.get("outcome") in {"success", "partial_success"}]
        except Exception: pass
        reliability = self.reliability()
        status = "conflicted" if conflicts else "fresh"
        snapshot = SelfModelSnapshot(new_id("self_snapshot"), "Evo Agent", __version__, self.architecture_version, self.environment_id, status, [item.to_dict() for item in claims], [item.to_dict() if hasattr(item, "to_dict") else item for item in limitations], assumptions, uncertainties, conflicts, reliability, {"safe_mode": self.safe_mode, "kill_switch": self.kill_switch, "approval_authority": "governance", "execution_authority": "kernel", "verification_authority": "verifier"}, {"evolution": len(getattr(self.evolution_orchestrator, "list_work_items", lambda: [])()) if self.evolution_orchestrator else 0}, recent, FreshnessState.FRESH, {"reason": reason, "engine": self.ENGINE_VERSION, "source": "authoritative_registries_and_evidence"})
        self.store.save_self_model_snapshot(snapshot); self._snapshot = snapshot
        if self.memory is not None and hasattr(self.memory, "capture_self_model"):
            try: self.memory.capture_self_model({"source_id": snapshot.snapshot_id, "subject": "self_model_refresh", "architecture_version": self.architecture_version, "environment_id": self.environment_id, "claim_count": len(claims), "limitation_count": len(limitations), "freshness": snapshot.freshness.value, "status": snapshot.status})
            except Exception: pass
        self._emit(EventType.SELF_MODEL_REFRESHED, {"snapshot": snapshot.to_dict()}); self.consistency_check(snapshot); return snapshot

    refresh_self_model = refresh
    rebuild = refresh

    def detect_limitations(self, registries: Mapping[str, Sequence[Any]] | None = None, evidence: Mapping[str, Any] | None = None) -> list[SelfModelLimitation]:
        registries = registries or self._registry_snapshot(); result: list[SelfModelLimitation] = []
        for kind, items in registries.items():
            for item in items:
                state = self._state(item); identity = self._identity(item)
                limitation_type = {"capabilities": LimitationType.MISSING_CAPABILITY, "tools": LimitationType.UNAVAILABLE_TOOL, "models": LimitationType.UNAVAILABLE_MODEL, "specialists": LimitationType.SPECIALIST_LIMITATION, "integrations": LimitationType.EXTERNAL_DEPENDENCY_FAILURE}.get(kind)
                if limitation_type and state in {"disabled", "unavailable", "removed", "deprecated", "blocked", "circuit_open", "failed"}:
                    limitation = SelfModelLimitation(new_id("limitation"), limitation_type, f"{kind[:-1]} {identity} is currently {state}", [], "medium" if state in {"disabled", "unavailable"} else "high", 1, [], [__version__], self.environment_id, .85, "retain bounded fallback or request clarification/approval", provenance={"source": f"{kind[:-1]}_registry", "authoritative": True}, architecture_version=self.architecture_version)
                    self.store.save_self_model_limitation(limitation); self._emit(EventType.SELF_MODEL_LIMITATION_RECORDED, {"limitation": limitation.to_dict()}); result.append(limitation)
        try:
            failures: dict[str, list[dict[str, Any]]] = {}
            for row in self.store.find_experiences(limit=200):
                if str(row.get("outcome", "")).lower() in {"failure", "failed", "timeout", "blocked"}:
                    task_type = str(row.get("task_type", "general")); failures.setdefault(task_type, []).append(row)
            for task_type, rows in failures.items():
                if len(rows) >= 3:
                    limitation = SelfModelLimitation(new_id("limitation"), LimitationType.REPEATED_FAILURE, f"Repeated verified-task failures for task type {task_type}", [str(row.get("experience_id", row.get("task_id", ""))) for row in rows[:_MAX_ITEMS]], "high", len(rows), [str(row.get("task_id", "")) for row in rows[:_MAX_ITEMS]], [str(row.get("agent_version", __version__)) for row in rows[:_MAX_ITEMS]], self.environment_id, min(.99, .55 + .08 * len(rows)), "ask for clarification, use Flexibility recovery, or route persistent limitation to governed Evolution", provenance={"source": "experience", "task_type": task_type}, architecture_version=self.architecture_version)
                    self.store.save_self_model_limitation(limitation); self._emit(EventType.SELF_MODEL_LIMITATION_RECORDED, {"limitation": limitation.to_dict()}); result.append(limitation)
        except Exception: pass
        return result[:_MAX_ITEMS]

    def add_limitation(self, limitation: SelfModelLimitation) -> SelfModelLimitation:
        if _protected(limitation.description): limitation.lifecycle_state = LifecycleState.CONFLICTED.value; limitation.confidence = 0.0
        limitation.architecture_version = self.architecture_version; limitation.environment_id = self.environment_id; self.store.save_self_model_limitation(limitation); return limitation

    def create_assumption(self, statement: str, source: str = "cognitive", confidence: float = .5, dependent_task: str = "", invalidation_condition: str = "environment or evidence changes", evidence_ids: Sequence[str] = ()) -> SelfModelAssumption:
        if _protected(statement): confidence = 0.0; status = AssumptionValidationStatus.UNCERTAIN
        else: status = AssumptionValidationStatus.UNVALIDATED
        assumption = SelfModelAssumption(new_id("assumption"), statement[:_MAX_TEXT], source, max(0.0, min(1.0, confidence)), status, dependent_task, invalidation_condition, architecture_version=self.architecture_version, environment_id=self.environment_id, provenance={"source": source, "authoritative": source in {"world", "runtime", "capability_registry"}}, evidence_ids=list(evidence_ids)[:_MAX_ITEMS])
        self.store.save_self_model_assumption(assumption); self._emit(EventType.SELF_MODEL_ASSUMPTION_RECORDED, {"assumption": assumption.to_dict()}); return assumption

    track_assumption = create_assumption

    def validate_assumption(self, assumption_id: str, valid: bool | None, reason: str = "", evidence_ids: Sequence[str] = ()) -> SelfModelAssumption:
        row = self.store.self_model_assumption_by_id(assumption_id)
        if not row: raise KeyError(assumption_id)
        assumption = self._assumption_from_payload(row.get("payload", row)); assumption.validation_status = AssumptionValidationStatus.VALID if valid is True else AssumptionValidationStatus.INVALIDATED if valid is False else AssumptionValidationStatus.UNCERTAIN; assumption.lifecycle_state = LifecycleState.ACTIVE.value if valid is not False else LifecycleState.INVALIDATED.value; assumption.updated_at = utc_now(); assumption.provenance["validation_reason"] = reason[:_MAX_TEXT]; assumption.evidence_ids = list(dict.fromkeys(assumption.evidence_ids + list(evidence_ids)))[:_MAX_ITEMS]; self.store.save_self_model_assumption(assumption); self._emit(EventType.SELF_MODEL_ASSUMPTION_INVALIDATED if valid is False else EventType.SELF_MODEL_ASSUMPTION_RECORDED, {"assumption": assumption.to_dict()}); return assumption

    invalidate_assumption = lambda self, assumption_id, reason="environment changed": self.validate_assumption(assumption_id, False, reason)

    def record_uncertainty(self, uncertainty_type: str, description: str, source: str, confidence: float = 0.0, severity: str = "medium", evidence_ids: Sequence[str] = ()) -> SelfModelUncertainty:
        uncertainty = SelfModelUncertainty(new_id("uncertainty"), uncertainty_type, description[:_MAX_TEXT], source, max(0.0, min(1.0, confidence)), severity, list(evidence_ids)[:_MAX_ITEMS], self.architecture_version, self.environment_id, LifecycleState.ACTIVE.value, {"authoritative": source in {"world", "runtime", "verifier", "governance"}})
        self.store.save_self_model_uncertainty(uncertainty); self._emit(EventType.SELF_MODEL_UNCERTAINTY_RECORDED, {"uncertainty": uncertainty.to_dict()}); return uncertainty

    def record_conflict(self, subject: str, authoritative_value: Any, competing_value: Any, sources: Sequence[str], evidence_ids: Sequence[str] = (), resolution: str = "authoritative source wins; preserve conflict evidence") -> SelfModelConflict:
        conflict = SelfModelConflict(new_id("self_conflict"), subject, _safe(authoritative_value), _safe(competing_value), list(sources)[:_MAX_ITEMS], resolution, LifecycleState.CONFLICTED.value, self.architecture_version, self.environment_id, list(evidence_ids)[:_MAX_ITEMS])
        self.store.save_self_model_conflict(conflict); self._emit(EventType.SELF_MODEL_CONFLICT_DETECTED, {"conflict": conflict.to_dict()}); return conflict

    def freshness(self, snapshot: SelfModelSnapshot | None = None) -> FreshnessState:
        snapshot = snapshot or self._snapshot
        if snapshot is None: return FreshnessState.UNKNOWN
        age = (_now() - (_parse_time(snapshot.created_at) or _now())).total_seconds()
        if age < 60: return FreshnessState.FRESH
        if age < 600: return FreshnessState.AGING
        if age < 3600: return FreshnessState.STALE
        return FreshnessState.EXPIRED

    def staleness(self) -> dict[str, Any]:
        state = self.freshness(); self._emit(EventType.SELF_MODEL_STALE, {"freshness": state.value}) if state in {FreshnessState.STALE, FreshnessState.EXPIRED} else None; return {"state": state.value, "refresh_required": state in {FreshnessState.STALE, FreshnessState.EXPIRED, FreshnessState.UNKNOWN}, "last_refresh": self._snapshot.created_at if self._snapshot else None}

    def reliability(self, records: Sequence[Mapping[str, Any]] | None = None) -> dict[str, Any]:
        try: rows = list(records or self.store.find_experiences(limit=300))
        except Exception: rows = []
        result: dict[str, Any] = {"overall": {"sample_count": len(rows), "success_rate": 0.0, "verification_rate": 0.0, "failure_rate": 0.0}, "task_types": {}, "strategies": {}, "models": {}, "tools": {}, "specialists": {}, "capabilities": {}, "integrations": {}, "environments": {}}
        if not rows: return result
        def accumulate(bucket: dict[str, Any], key: str, row: Mapping[str, Any]) -> None:
            item = bucket.setdefault(key, {"sample_count": 0, "successes": 0, "verified": 0, "failures": 0})
            item["sample_count"] += 1; item["successes"] += int(str(row.get("outcome", "")).lower() in {"success", "succeeded", "partial_success"}); item["verified"] += int(bool(row.get("verified", row.get("verification_result", False)))); item["failures"] += int(str(row.get("outcome", "")).lower() in {"failure", "failed", "timeout", "blocked"})
        for row in rows:
            for bucket, field in ((result["task_types"], "task_type"), (result["strategies"], "strategy"), (result["models"], "model_id"), (result["environments"], "environment_version")):
                if row.get(field): accumulate(bucket, str(row[field]), row)
            accumulate(result["overall"].setdefault("_bucket", {}), "overall", row)
        overall = result["overall"].pop("_bucket", {}).get("overall", result["overall"])
        total = max(1, len(rows)); result["overall"] = {"sample_count": len(rows), "success_rate": overall.get("successes", 0) / total, "verification_rate": overall.get("verified", 0) / total, "failure_rate": overall.get("failures", 0) / total}
        for dimension in ("task_types", "strategies", "models", "tools", "specialists", "capabilities", "integrations", "environments"):
            for item in result[dimension].values():
                count = max(1, item["sample_count"]); item.update({"success_rate": item["successes"] / count, "verification_rate": item["verified"] / count, "failure_rate": item["failures"] / count})
        return _safe(result)

    def consistency_check(self, snapshot: SelfModelSnapshot | None = None) -> dict[str, Any]:
        snapshot = snapshot or self._snapshot or self.refresh("consistency baseline")
        registries = self._registry_snapshot(); checks: dict[str, Any] = {}; mismatches: list[str] = []
        for kind, items in registries.items():
            expected = sorted(self._identity(item) for item in items)
            claim = next((item for item in snapshot.claims if item.get("subject") == kind), None)
            actual = sorted(str(item.get("id")) for item in (claim or {}).get("value", []) if isinstance(item, Mapping))
            checks[kind] = {"registry_count": len(expected), "self_model_count": len(actual), "match": expected == actual}
            if expected != actual: mismatches.append(kind)
        checks["architecture_version"] = {"self_model": snapshot.architecture_version, "current": self.architecture_version, "match": snapshot.architecture_version == self.architecture_version}
        if mismatches: snapshot.status = "stale" if not self._snapshot or self.freshness(snapshot) in {FreshnessState.STALE, FreshnessState.EXPIRED} else "conflicted"
        result = {"status": "conflicted" if mismatches else "consistent", "mismatches": mismatches, "checks": checks, "refresh_required": bool(mismatches)}; self._emit(EventType.SELF_MODEL_CONSISTENCY_CHECKED, result); return result

    validate_consistency = consistency_check

    def diagnostics(self) -> SelfDiagnostics:
        checks: dict[str, Any] = {}; failures: list[str] = []
        try: checks["database"] = {"healthy": bool(self.store.path.exists()), "event_count": self.store.total_event_count()}
        except Exception as exc: checks["database"] = {"healthy": False, "error": type(exc).__name__}; failures.append("database")
        for name, source, method in (("runtime", self.runtime, "status"), ("model", self.model_intelligence, "statistics"), ("specialist", self.specialist_delegation, "stats"), ("external", self.external_integrations, "statistics"), ("world", self.world_intelligence, "stats"), ("learning", self.adaptive_learning, "status")):
            if source is None: checks[name] = {"status": "unknown", "reason": "authority not configured"}; continue
            try: checks[name] = getattr(source, method)() if callable(getattr(source, method, None)) else {"status": "available"}
            except Exception as exc: checks[name] = {"status": "degraded", "error": type(exc).__name__}; failures.append(name)
        checks["environment_freshness"] = self.staleness(); checks["evolution_queue"] = {"status": "available" if self.evolution_orchestrator else "unknown", "pending": len(getattr(self.evolution_orchestrator, "list_work_items", lambda: [])()) if self.evolution_orchestrator else 0}; checks["rollback_availability"] = {"available": bool(self.workspace and (self.workspace / ".evo").exists())}
        record = SelfDiagnostics(new_id("diagnostic"), "degraded" if failures else "healthy", checks, failures, self.architecture_version, self.environment_id, {"source": "existing_authorities", "engine": self.ENGINE_VERSION}); self.store.save_self_diagnostics(record); self._emit(EventType.SELF_DIAGNOSTICS_COMPLETED, {"diagnostics": record.to_dict()}); return record

    self_diagnostics = diagnostics

    def reflect(self, task_id: str, outcome: Mapping[str, Any] | None = None) -> SelfReflection:
        data = dict(outcome or {})
        verified = bool(data.get("verified", data.get("verification_success", False))); final = str(data.get("outcome", data.get("final_outcome", "unknown")))
        reflection = SelfReflection(new_id("reflection"), task_id, str(data.get("attempted", data.get("goal", "")))[:_MAX_TEXT], list(data.get("succeeded", []))[:_MAX_ITEMS], list(data.get("failed", data.get("failures", [])))[:_MAX_ITEMS], list(data.get("failure_reasons", []))[:_MAX_ITEMS], list(data.get("correct_assumptions", []))[:_MAX_ITEMS], list(data.get("wrong_assumptions", []))[:_MAX_ITEMS], str(data.get("strategy", "unknown")), data.get("strategy_appropriate"), {str(k): v for k, v in dict(data.get("selected_components_appropriate", {})).items()}, data.get("predicted_confidence"), verified, list(data.get("what_to_remember", []))[:_MAX_ITEMS], bool(data.get("learning_evidence", True)), bool(data.get("evolution_evidence", False)), {"outcome": final, "source": "post_task_reflection"}, self.architecture_version, self.environment_id)
        self.store.save_self_reflection(reflection)
        if self.memory is not None and hasattr(self.memory, "capture_self_model"):
            try: self.memory.capture_self_model({"source_id": reflection.reflection_id, "subject": "self_reflection", "task_id": task_id, "actual_verified": verified, "learning_evidence": reflection.learning_evidence, "evolution_evidence": reflection.evolution_evidence})
            except Exception: pass
        self._emit(EventType.SELF_REFLECTION_RECORDED, {"reflection": reflection.to_dict()}, task_id); return reflection

    post_task_reflection = reflect

    def critique(self, task_id: str, claim: str, verified: bool, evidence: Sequence[str] = (), confidence: float | None = None) -> dict[str, Any]:
        unsupported = bool(claim) and not verified; result = {"task_id": task_id, "claim": claim[:_MAX_TEXT], "verified": bool(verified), "unsupported_claim": unsupported, "disclose_uncertainty": unsupported or confidence is None, "evidence_ids": list(evidence)[:_MAX_ITEMS], "confidence": confidence if not unsupported else 0.0, "reason": "Verifier evidence is required before completion/success/verified claims" if unsupported else "claim is supported by supplied verification evidence", "authority": "verifier"}; self._emit(EventType.SELF_CRITIQUE_COMPLETED, result, task_id); return result

    self_critique = critique

    def route_limitation(self, limitation: SelfModelLimitation | str, structural: bool = False) -> Any:
        if isinstance(limitation, str):
            limitation = next((self._limitation_from_payload(row.get("payload", row)) for row in self.store.find_self_model_limitations(limit=300) if row.get("limitation_id") == limitation), None)
        if limitation is None: raise KeyError("limitation")
        if _protected(limitation.description): return {"status": "blocked", "reason": "protected authority cannot be routed as a mutable limitation"}
        path = "metamorphosis" if structural or limitation.limitation_type is LimitationType.ARCHITECTURE_LIMITATION else "evolution"
        evidence = {"limitation": limitation.to_dict(), "path": path, "governance_required": True, "source": "self_model"}
        if self.evolution_orchestrator is None: return {"status": "evidence_only", "path": path, "evidence": evidence}
        creator = getattr(self.evolution_orchestrator, "create_work_item", None)
        if not callable(creator): return {"status": "evidence_only", "path": path, "evidence": evidence}
        opportunity = EvolutionOpportunity(new_id("opportunity"), list(limitation.evidence_ids)[:_MAX_ITEMS], [], limitation.description[:_MAX_TEXT], max(1, limitation.frequency), limitation.severity, [limitation.limitation_type.value], [limitation.limitation_type.value], [], "strong" if limitation.confidence >= .8 else "moderate", OrchestrationPath.METAMORPHOSIS if path == "metamorphosis" else OrchestrationPath.EVOLUTION, limitation.confidence, architecture_version=self.architecture_version, classification_reason="self-model limitation evidence", metadata={"source": "self_model", "structural": path == "metamorphosis"})
        try: item = creator(opportunity)
        except Exception: return {"status": "evidence_only", "path": path, "evidence": evidence}
        self._emit(EventType.SELF_MODEL_EVOLUTION_EVIDENCE, {"limitation": limitation.to_dict(), "path": path, "opportunity_id": opportunity.opportunity_id}); return {"status": "routed", "path": path, "work_item": getattr(item, "to_dict", lambda: item)() if item is not None else None}

    self_model_to_evolution = route_limitation

    def status(self) -> dict[str, Any]:
        snapshot = self._snapshot.to_dict() if self._snapshot else None
        return {"engine_version": self.ENGINE_VERSION, "architecture_version": self.architecture_version, "environment_id": self.environment_id, "freshness": self.staleness(), "snapshot": snapshot, "claim_count": len(self.store.find_self_model_claims(limit=1000)), "limitation_count": len(self.store.find_self_model_limitations(limit=1000)), "assumption_count": len(self.store.find_self_model_assumptions(limit=1000)), "uncertainty_count": len(self.store.find_self_model_uncertainty(limit=1000)), "conflict_count": len(self.store.find_self_model_conflicts(limit=1000)), "safe_mode": self.safe_mode, "kill_switch": self.kill_switch}

    introspect = status
    capability_awareness = refresh
    limitation_detection = detect_limitations
    refresh_if_stale = lambda self, reason="stale self-model": self.refresh(reason) if self.staleness()["refresh_required"] else (self._snapshot or self.refresh(reason))
    consistency_status = consistency_check
    diagnostic = diagnostics
    reflect_task = reflect
    critique_claim = critique

    def set_safe_mode(self, enabled: bool) -> None: self.safe_mode = bool(enabled)
    def activate_kill_switch(self) -> None: self.kill_switch = True
    def clear_kill_switch(self, actor: str = "human") -> None:
        if str(actor).lower() in {"model", "self_model", "autonomous", "agent"}: raise PermissionError("self-model cannot clear kill switch")
        self.kill_switch = False

    @staticmethod
    def _snapshot_from_payload(payload: Mapping[str, Any]) -> SelfModelSnapshot:
        data = dict(payload); data["freshness"] = FreshnessState(data.get("freshness", FreshnessState.UNKNOWN.value)); return SelfModelSnapshot(**{key: data[key] for key in SelfModelSnapshot.__dataclass_fields__ if key in data})

    @staticmethod
    def _assumption_from_payload(payload: Mapping[str, Any]) -> SelfModelAssumption:
        data = dict(payload); data["validation_status"] = AssumptionValidationStatus(data.get("validation_status", AssumptionValidationStatus.UNVALIDATED.value)); return SelfModelAssumption(**{key: data[key] for key in SelfModelAssumption.__dataclass_fields__ if key in data})

    @staticmethod
    def _limitation_from_payload(payload: Mapping[str, Any]) -> SelfModelLimitation:
        data = dict(payload); data["limitation_type"] = LimitationType(data.get("limitation_type", LimitationType.REPEATED_FAILURE.value)); return SelfModelLimitation(**{key: data[key] for key in SelfModelLimitation.__dataclass_fields__ if key in data})


class MetaReasoningEngine:
    """Bounded recommendation engine. It evaluates readiness and never executes actions."""

    ENGINE_VERSION = "meta-reasoning-v1"

    def __init__(self, store: SQLiteStore, self_model: SelfModelEngine, adaptive_learning: Any | None = None, flexibility: Any | None = None):
        self.store = store; self.self_model = self_model; self.adaptive_learning = adaptive_learning; self.flexibility = flexibility; self.safe_mode = False; self.kill_switch = False

    def _emit(self, event_type: EventType, payload: Mapping[str, Any], task_id: str = "meta-reasoning") -> None:
        try: self.store.append_event(Event(task_id, event_type, _safe(dict(payload))))
        except Exception: pass

    def assess_readiness(self, goal: str, context: Mapping[str, Any] | None = None, goal_id: str | None = None) -> DecisionReadinessAssessment:
        context = dict(context or {}); goal_id = goal_id or new_id("goal"); known: list[str] = []; unknown: list[str] = []; assumptions: list[str] = []; uncertainties: list[str] = []; conflicts: list[str] = []; gaps: list[str] = []
        if _protected(goal): state = DecisionReadinessState.UNSAFE; reason = "goal contains a protected-authority or arbitrary-execution request"; recommendation = "refuse and preserve existing authority boundaries"
        elif context.get("impossible"): state = DecisionReadinessState.IMPOSSIBLE; reason = "authoritative constraints make the goal impossible"; recommendation = "refuse or explain the blocking constraint"
        elif context.get("approval_required") or context.get("requires_approval"): state = DecisionReadinessState.APPROVAL_REQUIRED; reason = "governance requires exact human approval"; recommendation = "request human approval before execution"
        elif context.get("capability_missing") or context.get("missing_capabilities"): state = DecisionReadinessState.CAPABILITY_MISSING; reason = "required capability is absent from the authoritative registry"; recommendation = "ask for a safer alternative or route evidence to Evolution"
        elif context.get("tool_unavailable"): state = DecisionReadinessState.TOOL_UNAVAILABLE; reason = "required tool is unavailable or disallowed"; recommendation = "use a registered fallback or ask for clarification"
        elif context.get("model_unavailable"): state = DecisionReadinessState.MODEL_UNAVAILABLE; reason = "no policy-compatible model is available"; recommendation = "use a bounded fallback or request user guidance"
        elif context.get("specialist_required"): state = DecisionReadinessState.SPECIALIST_REQUIRED; reason = "a subordinate specialist is useful but must be explicitly contracted"; recommendation = "create a bounded specialist contract"
        elif context.get("environment_uncertain") or self.self_model.freshness() in {FreshnessState.STALE, FreshnessState.EXPIRED, FreshnessState.UNKNOWN}: state = DecisionReadinessState.ENVIRONMENT_UNCERTAIN; reason = "environment/self-model freshness is insufficient"; recommendation = "refresh the self-model and revalidate the plan"
        elif context.get("conflicts"): state = DecisionReadinessState.CONFLICTED; conflicts = [str(item) for item in context.get("conflicts", [])][:16]; reason = "authoritative or evidence sources conflict"; recommendation = "preserve the conflict and request resolution"
        elif context.get("insufficient_evidence"): state = DecisionReadinessState.INSUFFICIENT_EVIDENCE; gaps = [str(item) for item in context.get("evidence_gaps", [])][:16]; reason = "there is not enough evidence for a reliable decision"; recommendation = "collect bounded evidence before acting"
        elif context.get("resource_limited"): state = DecisionReadinessState.RESOURCE_LIMITED; reason = "current Runtime resource limits do not fit the task"; recommendation = "reduce scope or request a bounded alternative"
        elif len(str(goal).strip()) < 8 or any(token in str(goal).lower() for token in ("something", "it", "as needed", "whatever")) and not context.get("clarified"): state = DecisionReadinessState.CLARIFICATION_REQUIRED; reason = "a material requirement is ambiguous"; recommendation = "ask a targeted clarification question"
        else: state = DecisionReadinessState.READY; reason = "goal, authorities, evidence, and current constraints are sufficient for a bounded recommendation"; recommendation = "proceed through Cognitive, Flexibility, Runtime, Kernel, and Verification"
        if state is DecisionReadinessState.READY: known.append("goal text is sufficiently specific")
        else: unknown.append(reason)
        assessment = DecisionReadinessAssessment(new_id("readiness"), goal_id, state, recommendation, reason, .9 if state is DecisionReadinessState.READY else .65, known, unknown, assumptions, uncertainties, conflicts, gaps, self.clarification(goal, context)["question"] if state is DecisionReadinessState.CLARIFICATION_REQUIRED else "", state in {DecisionReadinessState.APPROVAL_REQUIRED, DecisionReadinessState.CAPABILITY_MISSING, DecisionReadinessState.CONFLICTED, DecisionReadinessState.UNSAFE, DecisionReadinessState.IMPOSSIBLE}, state is DecisionReadinessState.APPROVAL_REQUIRED, {"source": "self_model_and_context", "execution_authority": "none"}, self.self_model.architecture_version, self.self_model.environment_id)
        self.store.save_decision_readiness(assessment); self._emit(EventType.DECISION_READINESS_ASSESSED, {"assessment": assessment.to_dict()}, goal_id); return assessment

    readiness = assess_readiness

    def clarification(self, goal: str, context: Mapping[str, Any] | None = None) -> dict[str, Any]:
        context = dict(context or {}); missing = list(context.get("missing_requirements", []))[:8]; question = str(context.get("clarification_question", "What exact outcome, scope, and constraints should Evo use?"))[:_MAX_TEXT]
        if missing: question = f"Please clarify: {', '.join(map(str, missing))}."
        result = {"required": bool(missing or len(str(goal).strip()) < 8 or (any(token in str(goal).lower() for token in ("something", "it", "as needed", "whatever")) and not context.get("clarified"))), "missing_requirements": missing, "ambiguity_type": context.get("ambiguity_type", "material_scope_or_outcome"), "candidate_interpretations": list(context.get("candidate_interpretations", []))[:8], "expected_impact": context.get("expected_impact", "incorrect interpretation could produce an incorrect or unsafe result"), "confidence": float(context.get("confidence", .45)), "question": question, "consequence_of_guessing": context.get("consequence_of_guessing", "result may not satisfy the actual goal"), "authority": "recommendation_only"}
        if result["required"]: self._emit(EventType.CLARIFICATION_RECOMMENDED, result)
        return result

    clarification_needed = clarification

    def escalation(self, goal: str, reasons: Iterable[str], context: Mapping[str, Any] | None = None) -> dict[str, Any]:
        reasons = list(dict.fromkeys(str(item) for item in reasons if item))[:16]; result = {"required": bool(reasons), "goal": str(goal)[:_MAX_TEXT], "reasons": reasons, "recommended_action": "request exact human decision or approval; do not self-approve", "confidence": .8 if reasons else .2, "context": _safe(dict(context or {})), "authority": "human_or_governance"};
        if reasons: self._emit(EventType.HUMAN_ESCALATION_RECOMMENDED, result)
        return result

    human_escalation = escalation

    def calibrate(self, subject: str, predicted_confidence: float, actual_verified: bool, evidence_ids: Sequence[str] = ()) -> ConfidenceCalibration:
        predicted = max(0.0, min(1.0, float(predicted_confidence))); actual = bool(actual_verified); target = 1.0 if actual else 0.0; error = abs(predicted - target)
        state = CalibrationState.INSUFFICIENT if not evidence_ids else CalibrationState.GOOD if error <= .15 else CalibrationState.OVERCONFIDENT if predicted > target else CalibrationState.UNDERCONFIDENT
        explanation = "confidence is not proof; calibration compares prediction with current verification evidence"
        record = ConfidenceCalibration(new_id("calibration"), subject, predicted, actual, state, error, list(evidence_ids)[:_MAX_ITEMS], explanation, self.self_model.architecture_version, self.self_model.environment_id); self.store.save_confidence_calibration(record); self._emit(EventType.CONFIDENCE_CALIBRATED, {"calibration": record.to_dict()}); return record

    confidence_calibration = calibrate

    def reason(self, goal: str, context: Mapping[str, Any] | None = None, goal_id: str | None = None) -> MetaReasoningRecord:
        assessment = self.assess_readiness(goal, context, goal_id); clarification = self.clarification(goal, context); reasons = [assessment.reason, "self-model is advisory and current authority gates remain binding"]
        escalation = self.escalation(goal, [assessment.state.value] if assessment.escalation_required else [], context)
        record = MetaReasoningRecord(new_id("meta_reasoning"), assessment.goal_id, assessment.recommendation, reasons, assessment.readiness_id, assessment.confidence, [clarification["question"]] if clarification["required"] else [], escalation, list((context or {}).get("safer_alternatives", []))[:8], {"engine": self.ENGINE_VERSION, "execution_authority": "none", "verification_authority": "verifier"}, self.self_model.architecture_version, self.self_model.environment_id); self.store.save_meta_reasoning(record); self._emit(EventType.META_REASONING_COMPLETED, {"record": record.to_dict()}, assessment.goal_id); return record

    meta_reason = reason
    evaluate = reason
    decision_readiness = assess_readiness
    clarification_intelligence = clarification
    escalation_recommendation = escalation
    confidence_calibration = calibrate
    run_cycle = reason

    def status(self) -> dict[str, Any]: return {"engine_version": self.ENGINE_VERSION, "safe_mode": self.safe_mode, "kill_switch": self.kill_switch, "self_model": self.self_model.status()}


SelfModel = SelfModelEngine
MetaCognitionEngine = MetaReasoningEngine
