from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
import hashlib
import json
import random
import time
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

from .models import Event, EventType, RiskLevel, new_id, utc_now
from .storage import SQLiteStore


MODEL_ARCHITECTURE_VERSION = "model-intelligence-v1"
_MAX_METADATA_BYTES = 12000
_MAX_CONTEXT_BYTES = 24000
_MAX_OUTPUT_BYTES = 24000
_PROTECTED_TERMS = {"protected_core", "governance", "approval_logic", "permissions", "verifier", "sandbox", "promotion", "rollback", "kill_switch", "metamorphosis", "evolver", "credentials", "production"}
_SECRET_TERMS = ("api_key", "access_token", "secret", "password", "private_key", "credential_value")


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def _hash(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _safe(value: Any, maximum: int = _MAX_METADATA_BYTES) -> Any:
    if isinstance(value, Mapping):
        result = {}
        for key, item in value.items():
            lowered = str(key).lower()
            result[str(key)] = "[REDACTED]" if any(term in lowered for term in _SECRET_TERMS) else _safe(item, maximum)
        return result
    if isinstance(value, (list, tuple)):
        return [_safe(item, maximum) for item in value[:100]]
    if isinstance(value, str) and len(value.encode("utf-8")) > maximum:
        return {"truncated": True, "content_hash": _hash(value), "excerpt": value[:maximum]}
    return value


def _now_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None


def _schema_accepts(schema: dict[str, Any] | None, value: Any) -> bool:
    if not schema:
        return True
    expected = schema.get("type")
    if expected == "object":
        if not isinstance(value, dict):
            return False
        required = schema.get("required", [])
        if any(key not in value for key in required):
            return False
        if schema.get("additionalProperties") is False and any(key not in schema.get("properties", {}) for key in value):
            return False
        return all(_schema_accepts(spec, value[key]) for key, spec in schema.get("properties", {}).items() if key in value)
    if expected == "array":
        return isinstance(value, list) and all(_schema_accepts(schema.get("items"), item) for item in value)
    if expected == "string": return isinstance(value, str)
    if expected == "integer": return isinstance(value, int) and not isinstance(value, bool)
    if expected == "number": return isinstance(value, (int, float)) and not isinstance(value, bool)
    if expected == "boolean": return isinstance(value, bool)
    return True


class ProviderType(str, Enum):
    OPENAI_COMPATIBLE = "openai_compatible"
    ANTHROPIC_COMPATIBLE = "anthropic_compatible"
    LOCAL = "local"
    DETERMINISTIC_TEST = "deterministic_test"


class ProviderLifecycle(str, Enum):
    REGISTERED = "registered"
    ACTIVE = "active"
    DEGRADED = "degraded"
    DISABLED = "disabled"
    REMOVED = "removed"


class ModelLifecycle(str, Enum):
    REGISTERED = "registered"
    ACTIVE = "active"
    DEGRADED = "degraded"
    DISABLED = "disabled"
    DEPRECATED = "deprecated"
    REMOVED = "removed"


class ModelHealthState(str, Enum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNAVAILABLE = "unavailable"
    DISABLED = "disabled"


class InferenceStatus(str, Enum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    TIMEOUT = "timeout"
    BLOCKED = "blocked"
    INVALID = "invalid"
    UNKNOWN = "unknown"


class LearningAdjustmentStatus(str, Enum):
    PROPOSED = "proposed"
    APPLIED = "applied"
    ROLLED_BACK = "rolled_back"
    BLOCKED = "blocked"
    EXPIRED = "expired"


class LearningEvidenceKind(str, Enum):
    EXPERIENCE = "experience"
    EVALUATION = "evaluation"
    BENCHMARK = "benchmark"
    MODEL_PERFORMANCE = "model_performance"
    TOOL_PERFORMANCE = "tool_performance"
    SPECIALIST_PERFORMANCE = "specialist_performance"
    FLEXIBILITY = "flexibility"
    ENVIRONMENT = "environment"


class ModelComparisonDecision(str, Enum):
    BETTER = "better"
    NO_CHANGE = "no_change"
    WORSE = "worse"
    INCONCLUSIVE = "inconclusive"


@dataclass
class ModelProvider:
    provider_id: str
    name: str
    provider_type: ProviderType
    endpoint: str = ""
    credential_reference: str = ""
    enabled: bool = True
    lifecycle_state: ProviderLifecycle = ProviderLifecycle.REGISTERED
    allowed_models: list[str] = field(default_factory=list)
    network_policy: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)
    architecture_version: str = MODEL_ARCHITECTURE_VERSION
    provenance: dict[str, Any] = field(default_factory=lambda: {"source": "user_registered"})
    created_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)

    def validate(self) -> list[str]:
        errors: list[str] = []
        if not self.provider_id or not self.name:
            errors.append("provider identity is required")
        if not isinstance(self.provider_type, ProviderType):
            errors.append("provider type is invalid")
        if any(term in (self.provider_id + " " + self.name).lower() for term in _PROTECTED_TERMS):
            errors.append("provider cannot target a protected authority")
        if any(any(term in str(key).lower() for term in _SECRET_TERMS) for key in self.metadata) or any(term in self.endpoint.lower() for term in ("api_key=", "access_token=", "secret=")):
            errors.append("provider metadata or endpoint contains a secret-bearing value")
        return errors

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self); data["provider_type"] = self.provider_type.value; data["lifecycle_state"] = self.lifecycle_state.value
        return _safe(data)


@dataclass
class ModelVersion:
    version: str = "1.0"
    parent_version: str | None = None
    lineage: list[str] = field(default_factory=list)
    architecture_version: str = MODEL_ARCHITECTURE_VERSION

    def to_dict(self) -> dict[str, Any]: return asdict(self)


@dataclass
class ModelCapability:
    capability_id: str
    name: str
    quality: float = 0.5
    supported: bool = True
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=utc_now)

    def validate(self) -> list[str]:
        return ["model capability identity is required"] if not self.capability_id or not self.name else []

    def to_dict(self) -> dict[str, Any]: return _safe(asdict(self))


@dataclass
class ModelContextProfile:
    max_context_tokens: int = 8192
    max_input_tokens: int = 8192
    max_output_tokens: int = 2048
    supports_long_context: bool = False
    truncation_policy: str = "bounded_relevance_preserving"

    def to_dict(self) -> dict[str, Any]: return asdict(self)


@dataclass
class ModelCostProfile:
    input_cost_per_million: float = 0.0
    output_cost_per_million: float = 0.0
    currency: str = "USD"
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]: return asdict(self)


@dataclass
class ModelPerformanceProfile:
    task_success_rate: float = 0.5
    verification_success_rate: float = 0.5
    reliability: float = 0.5
    average_latency_ms: float = 0.0
    output_validity_rate: float = 0.5
    tool_call_reliability: float = 0.5
    sample_count: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]: return asdict(self)


@dataclass
class ModelHealth:
    state: ModelHealthState = ModelHealthState.HEALTHY
    availability: bool = True
    success_count: int = 0
    failure_count: int = 0
    timeout_count: int = 0
    structured_output_failures: int = 0
    tool_call_failures: int = 0
    provider_errors: int = 0
    verification_success_count: int = 0
    average_latency_ms: float = 0.0
    last_error: str = ""
    last_checked: str | None = None
    circuit_open_until: str | None = None

    @property
    def failure_rate(self) -> float:
        total = self.success_count + self.failure_count
        return self.failure_count / total if total else 0.0

    @property
    def reliability(self) -> float:
        total = self.success_count + self.failure_count
        return self.success_count / total if total else 0.5

    def circuit_open(self) -> bool:
        expiry = _now_dt(self.circuit_open_until)
        return self.state is ModelHealthState.UNAVAILABLE or (expiry is not None and expiry > datetime.now(timezone.utc))

    def record(self, success: bool, latency_ms: float = 0.0, timeout: bool = False, structured_valid: bool = True, tool_call_valid: bool = True, verified: bool = False, error: str = "", circuit_failures: int = 3) -> None:
        self.last_checked = utc_now()
        if success: self.success_count += 1
        else:
            self.failure_count += 1; self.last_error = error[:512]
        if timeout: self.timeout_count += 1
        if not structured_valid: self.structured_output_failures += 1
        if not tool_call_valid: self.tool_call_failures += 1
        if verified: self.verification_success_count += 1
        total = self.success_count + self.failure_count
        self.average_latency_ms = ((self.average_latency_ms * max(total - 1, 0)) + max(0.0, latency_ms)) / max(1, total)
        if self.failure_count >= circuit_failures and self.reliability < 0.5:
            self.state = ModelHealthState.UNAVAILABLE
            self.availability = False
            self.circuit_open_until = (datetime.now(timezone.utc).replace(microsecond=0)).isoformat()
        elif success:
            self.state = ModelHealthState.HEALTHY if self.failure_rate < 0.25 else ModelHealthState.DEGRADED
            self.availability = True
        else:
            self.state = ModelHealthState.DEGRADED

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self); data["state"] = self.state.value; data["failure_rate"] = self.failure_rate; data["reliability"] = self.reliability; return data


@dataclass
class ModelProvenance:
    source: str = "user_registered"
    source_id: str = ""
    actor: str = "system"
    created_at: str = field(default_factory=utc_now)
    lineage: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]: return asdict(self)


@dataclass
class ModelPolicy:
    allowed: bool = True
    approval_required: bool = False
    network_required: bool = False
    allowed_integrations: list[str] = field(default_factory=list)
    permitted_risk: list[str] = field(default_factory=lambda: [item.value for item in RiskLevel])
    max_retries: int = 1
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]: return _safe(asdict(self))


@dataclass
class Model:
    model_id: str
    provider_id: str
    name: str
    version: str = "1.0"
    capabilities: list[ModelCapability] = field(default_factory=list)
    context_profile: ModelContextProfile = field(default_factory=ModelContextProfile)
    modality_support: list[str] = field(default_factory=lambda: ["text"])
    structured_output_support: bool = False
    tool_use_support: bool = False
    reasoning_classes: list[str] = field(default_factory=list)
    latency_characteristics: dict[str, Any] = field(default_factory=dict)
    cost_profile: ModelCostProfile = field(default_factory=ModelCostProfile)
    performance_profile: ModelPerformanceProfile = field(default_factory=ModelPerformanceProfile)
    environment_compatibility: dict[str, Any] = field(default_factory=dict)
    enabled: bool = True
    available: bool = True
    health: ModelHealth = field(default_factory=ModelHealth)
    architecture_version: str = MODEL_ARCHITECTURE_VERSION
    provenance: ModelProvenance = field(default_factory=ModelProvenance)
    lifecycle_state: ModelLifecycle = ModelLifecycle.ACTIVE
    policy: ModelPolicy = field(default_factory=ModelPolicy)
    version_lineage: ModelVersion = field(default_factory=ModelVersion)
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)

    def validate(self) -> list[str]:
        errors: list[str] = []
        if not self.model_id or not self.provider_id or not self.name or not self.version: errors.append("model identity and version are required")
        if self.context_profile.max_context_tokens <= 0 or self.context_profile.max_output_tokens <= 0: errors.append("model context limits must be positive")
        if any(term in (self.model_id + " " + self.name).lower() for term in _PROTECTED_TERMS): errors.append("model cannot target a protected authority")
        if any(any(term in str(key).lower() for term in _SECRET_TERMS) for key in self.metadata): errors.append("model metadata contains a secret-bearing key")
        for capability in self.capabilities: errors.extend(capability.validate())
        return errors

    def capability_names(self) -> set[str]: return {item.name for item in self.capabilities if item.supported} | {item.capability_id for item in self.capabilities if item.supported}

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["capabilities"] = [item.to_dict() for item in self.capabilities]
        data["context_profile"] = self.context_profile.to_dict(); data["cost_profile"] = self.cost_profile.to_dict(); data["performance_profile"] = self.performance_profile.to_dict(); data["health"] = self.health.to_dict(); data["provenance"] = self.provenance.to_dict(); data["policy"] = self.policy.to_dict(); data["version_lineage"] = self.version_lineage.to_dict(); data["lifecycle_state"] = self.lifecycle_state.value
        return _safe(data)


@dataclass
class InferenceRequest:
    model_id: str
    provider_id: str
    task_id: str
    purpose: str
    input_classification: str
    input: Any
    output_schema: dict[str, Any] | None = None
    timeout_seconds: float = 30.0
    resource_limits: dict[str, Any] = field(default_factory=lambda: {"max_output_bytes": _MAX_OUTPUT_BYTES, "max_tokens": 2048})
    permission_context: dict[str, Any] = field(default_factory=dict)
    correlation_id: str = field(default_factory=lambda: new_id("correlation"))
    risk: str = RiskLevel.LOW.value
    structured_output: bool = False
    tool_schema: list[dict[str, Any]] = field(default_factory=list)
    created_at: str = field(default_factory=utc_now)

    def validate(self) -> list[str]:
        errors: list[str] = []
        if not self.model_id or not self.provider_id or not self.task_id or not self.purpose or not self.input_classification or not self.correlation_id: errors.append("inference identity and purpose are required")
        if self.timeout_seconds <= 0 or self.timeout_seconds > 3600: errors.append("inference timeout is outside bounded limits")
        if int(self.resource_limits.get("max_output_bytes", _MAX_OUTPUT_BYTES)) <= 0 or int(self.resource_limits.get("max_tokens", 1)) <= 0: errors.append("inference resource limits must be positive")
        if self.structured_output and not isinstance(self.output_schema, dict): errors.append("structured inference requires an output schema")
        if any(term in self.purpose.lower() for term in _PROTECTED_TERMS): errors.append("inference purpose targets a protected authority")
        return errors

    def to_dict(self) -> dict[str, Any]: return _safe(asdict(self))


@dataclass
class ModelResponse:
    model_id: str
    task_id: str
    status: InferenceStatus = InferenceStatus.SUCCEEDED
    output: Any = None
    output_schema_valid: bool = False
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    confidence: float = 0.0
    latency_ms: float = 0.0
    resource_usage: dict[str, Any] = field(default_factory=dict)
    error: str = ""
    provenance: dict[str, Any] = field(default_factory=dict)
    verified: bool = False
    created_at: str = field(default_factory=utc_now)

    @property
    def success(self) -> bool: return self.status is InferenceStatus.SUCCEEDED

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self); data["status"] = self.status.value; data["output"] = _safe(data["output"], _MAX_OUTPUT_BYTES); return data


@dataclass
class ModelCandidate:
    model: Model
    score: float
    reasons: list[str] = field(default_factory=list)
    rejected: bool = False
    rejection_reason: str = ""

    def to_dict(self) -> dict[str, Any]: return {"model": self.model.to_dict(), "score": self.score, "reasons": self.reasons, "rejected": self.rejected, "rejection_reason": self.rejection_reason}


@dataclass
class ModelSelection:
    selection_id: str
    task_id: str
    selected_model_id: str | None
    ranked_candidates: list[ModelCandidate]
    explanation: str
    confidence: float
    fallback_model_ids: list[str] = field(default_factory=list)
    requirements: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=utc_now)

    def to_dict(self) -> dict[str, Any]: return {"selection_id": self.selection_id, "task_id": self.task_id, "selected_model_id": self.selected_model_id, "ranked_candidates": [item.to_dict() for item in self.ranked_candidates], "explanation": self.explanation, "confidence": self.confidence, "fallback_model_ids": self.fallback_model_ids, "requirements": _safe(self.requirements), "created_at": self.created_at}


@dataclass
class ModelFallbackPlan:
    primary_model_id: str | None
    fallback_model_ids: list[str]
    max_attempts: int = 2
    reasons: list[str] = field(default_factory=list)
    bounded: bool = True

    def to_dict(self) -> dict[str, Any]: return asdict(self)


@dataclass
class ModelContext:
    task_id: str
    goal: str
    input: Any
    memory_evidence: list[dict[str, Any]] = field(default_factory=list)
    environment_evidence: list[dict[str, Any]] = field(default_factory=list)
    specialist_context: list[dict[str, Any]] = field(default_factory=list)
    external_observations: list[dict[str, Any]] = field(default_factory=list)
    output_budget: int = 2048
    context_budget: int = _MAX_CONTEXT_BYTES
    truncation: list[dict[str, Any]] = field(default_factory=list)
    provenance: dict[str, Any] = field(default_factory=dict)
    context_hash: str = ""

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self); data["input"] = _safe(data["input"]); data["memory_evidence"] = _safe(data["memory_evidence"]); data["environment_evidence"] = _safe(data["environment_evidence"]); data["specialist_context"] = _safe(data["specialist_context"]); data["external_observations"] = _safe(data["external_observations"]); return data


@dataclass
class ModelSelectionRecord:
    selection_id: str
    task_id: str
    selected_model_id: str
    alternatives: list[str]
    reason: str
    confidence: float
    fallback_plan: dict[str, Any]
    specialist_id: str | None = None
    created_at: str = field(default_factory=utc_now)

    def to_dict(self) -> dict[str, Any]: return asdict(self)


@dataclass
class ModelTrial:
    trial_id: str
    evaluation_id: str
    model_id: str
    benchmark_id: str
    trial_number: int
    success: bool
    verified: bool
    output_valid: bool
    score: float
    latency_ms: float
    resource_usage: dict[str, Any] = field(default_factory=dict)
    retries: int = 0
    failure_category: str = ""
    output_hash: str = ""
    reproducibility: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=utc_now)

    def to_dict(self) -> dict[str, Any]: return asdict(self)


@dataclass
class ModelEvaluation:
    evaluation_id: str
    model_id: str
    benchmark_id: str
    benchmark_version: str
    trial_count: int
    metrics: dict[str, Any]
    trials: list[ModelTrial]
    decision: ModelComparisonDecision = ModelComparisonDecision.INCONCLUSIVE
    decision_reason: list[str] = field(default_factory=list)
    evaluator_version: str = "model-evaluator-v1"
    created_at: str = field(default_factory=utc_now)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self); data["trials"] = [item.to_dict() for item in self.trials]; data["decision"] = self.decision.value; return data


@dataclass
class ModelComparison:
    comparison_id: str
    benchmark_id: str
    evaluations: dict[str, ModelEvaluation]
    decision: ModelComparisonDecision
    ranking: list[str]
    reason: list[str]
    reproducibility_metadata: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=utc_now)

    def to_dict(self) -> dict[str, Any]: return {"comparison_id": self.comparison_id, "benchmark_id": self.benchmark_id, "evaluations": {key: value.to_dict() for key, value in self.evaluations.items()}, "decision": self.decision.value, "ranking": self.ranking, "reason": self.reason, "reproducibility_metadata": self.reproducibility_metadata, "created_at": self.created_at}


@dataclass
class ModelBenchmark:
    benchmark_id: str
    version: str
    tasks: list[dict[str, Any]]
    trial_count: int = 3
    deterministic_seed: int = 0
    timeout_seconds: float = 30.0
    success_criteria: dict[str, Any] = field(default_factory=lambda: {"minimum_verification_rate": 1.0})

    def to_dict(self) -> dict[str, Any]: return asdict(self)

    def validate(self) -> list[str]:
        errors = []
        if not self.benchmark_id or not self.version or not self.tasks: errors.append("benchmark identity and tasks are required")
        if self.trial_count < 1 or self.trial_count > 100: errors.append("benchmark trial_count must be between 1 and 100")
        if self.timeout_seconds <= 0 or self.timeout_seconds > 3600: errors.append("benchmark timeout is outside bounded limits")
        for task in self.tasks:
            if not isinstance(task, dict) or not task.get("task_id") or "input" not in task: errors.append("benchmark task must contain task_id and input")
            if any(term in _canonical(task).lower() for term in ("disable governance", "modify production", "bypass verification")): errors.append("benchmark contains a protected behavior")
        return errors


@dataclass
class LearningObservation:
    observation_id: str
    task_id: str
    model_id: str
    task_category: str
    success: bool
    verified: bool
    output_valid: bool = True
    latency_ms: float = 0.0
    retries: int = 0
    fallback: bool = False
    failure_category: str = ""
    specialist_id: str | None = None
    strategy: str = ""
    tool: str = ""
    environment: dict[str, Any] = field(default_factory=dict)
    evidence_ids: list[str] = field(default_factory=list)
    provenance: dict[str, Any] = field(default_factory=lambda: {"source": "model_execution"})
    created_at: str = field(default_factory=utc_now)

    def to_dict(self) -> dict[str, Any]: return _safe(asdict(self))


@dataclass
class LearningOutcome:
    outcome_id: str
    observation_id: str
    value: float
    quality: float
    confidence: float
    evidence_kind: LearningEvidenceKind
    evidence_ids: list[str] = field(default_factory=list)
    reason: str = ""
    created_at: str = field(default_factory=utc_now)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self); data["evidence_kind"] = self.evidence_kind.value; return _safe(data)


@dataclass
class LearningEvidence:
    evidence_id: str
    source: str
    source_id: str
    value: Any
    confidence: float = 0.0
    created_at: str = field(default_factory=utc_now)

    def to_dict(self) -> dict[str, Any]: return _safe(asdict(self))


@dataclass
class LearningAdjustment:
    adjustment_id: str
    affected_component: str
    parameter: str
    previous_value: float
    proposed_value: float
    reason: str
    expected_benefit: str
    confidence: float
    risk: str
    source_evidence: list[str]
    evaluator_version: str
    architecture_version: str = MODEL_ARCHITECTURE_VERSION
    rollback_value: float | None = None
    status: LearningAdjustmentStatus = LearningAdjustmentStatus.PROPOSED
    created_at: str = field(default_factory=utc_now)
    applied_at: str | None = None
    rolled_back_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self); data["status"] = self.status.value; return _safe(data)


@dataclass
class LearningPolicy:
    policy_id: str = "learning-policy-v1"
    version: str = "1.0"
    enabled: bool = True
    minimum_evidence: int = 3
    maximum_adjustment: float = 0.10
    confidence_threshold: float = 0.70
    cooldown_seconds: float = 60.0
    decay: float = 0.95
    exploration_rate: float = 0.0
    exploration_seed: int = 0
    max_exploration_risk: str = RiskLevel.LOW.value
    max_pending_adjustments: int = 32
    created_at: str = field(default_factory=utc_now)

    def to_dict(self) -> dict[str, Any]: return asdict(self)


@dataclass
class ExplorationDecision:
    eligible: bool
    explore: bool
    selected_model_id: str | None
    reason: str
    seed: int

    def to_dict(self) -> dict[str, Any]: return asdict(self)


class ProviderAdapter:
    """Provider-neutral bounded adapter. It never owns Kernel authority."""

    provider_type = ProviderType.DETERMINISTIC_TEST

    def availability(self) -> dict[str, Any]: return {"available": True, "adapter": self.__class__.__name__}
    def discover_models(self) -> list[dict[str, Any]]: return []
    def health_check(self) -> dict[str, Any]: return self.availability()
    def infer(self, request: InferenceRequest) -> ModelResponse: raise NotImplementedError
    def structured_output(self, request: InferenceRequest) -> ModelResponse: return self.infer(request)
    def tool_call(self, request: InferenceRequest) -> ModelResponse: return self.infer(request)
    def stream(self, request: InferenceRequest) -> Iterable[dict[str, Any]]: yield self.infer(request).to_dict()


class DeterministicTestAdapter(ProviderAdapter):
    provider_type = ProviderType.DETERMINISTIC_TEST

    def __init__(self, model_id: str = "deterministic-test-model", responder: Callable[[InferenceRequest], Any] | None = None, responses: Mapping[str, Any] | None = None, fail: bool = False, delay_seconds: float = 0.0):
        self.model_id = model_id; self.responder = responder; self.responses = dict(responses or {}); self.fail = fail; self.delay_seconds = delay_seconds; self.calls = 0

    def discover_models(self) -> list[dict[str, Any]]:
        return [{"model_id": self.model_id, "name": self.model_id, "provider_type": self.provider_type.value, "capabilities": ["reasoning", "planning", "analysis", "structured_output"], "structured_output_support": True}]

    def infer(self, request: InferenceRequest) -> ModelResponse:
        self.calls += 1
        if self.delay_seconds: time.sleep(self.delay_seconds)
        if self.fail: raise RuntimeError("deterministic provider failure")
        value = self.responder(request) if self.responder else self.responses.get(request.task_id, self.responses.get("default", {"answer": request.input}))
        if isinstance(value, ModelResponse): return value
        if isinstance(value, Exception): raise value
        valid = _schema_accepts(request.output_schema, value)
        return ModelResponse(request.model_id, request.task_id, InferenceStatus.SUCCEEDED if valid or not request.structured_output else InferenceStatus.INVALID, value, valid, latency_ms=0.0, provenance={"adapter": "deterministic_test", "trusted": False})


class OpenAICompatibleModelAdapter(ProviderAdapter):
    provider_type = ProviderType.OPENAI_COMPATIBLE

    def __init__(self, model_id: str, client: Any | None = None, base_url: str | None = None, api_key: str | None = None):
        self.model_id = model_id; self._client = client
        if self._client is None:
            try:
                from openai import OpenAI
                kwargs: dict[str, Any] = {}
                if base_url: kwargs["base_url"] = base_url
                if api_key: kwargs["api_key"] = api_key
                self._client = OpenAI(**kwargs)
            except ImportError:
                self._client = None

    def infer(self, request: InferenceRequest) -> ModelResponse:
        if self._client is None: raise RuntimeError("OpenAI-compatible client is unavailable")
        kwargs: dict[str, Any] = {"model": request.model_id, "messages": [{"role": "user", "content": str(request.input)}], "timeout": request.timeout_seconds}
        if request.structured_output and request.output_schema:
            kwargs["response_format"] = {"type": "json_schema", "json_schema": {"name": "evo_output", "strict": True, "schema": {**request.output_schema, "additionalProperties": request.output_schema.get("additionalProperties", False)}}}
        started = time.monotonic(); response = self._client.chat.completions.create(**kwargs); content = response.choices[0].message.content
        try: value = json.loads(content) if request.structured_output else content
        except (TypeError, ValueError): value = content
        valid = _schema_accepts(request.output_schema, value)
        return ModelResponse(request.model_id, request.task_id, InferenceStatus.SUCCEEDED if valid or not request.structured_output else InferenceStatus.INVALID, value, valid, latency_ms=(time.monotonic() - started) * 1000, provenance={"provider": self.provider_type.value, "trusted": False})


class AnthropicCompatibleModelAdapter(OpenAICompatibleModelAdapter):
    provider_type = ProviderType.ANTHROPIC_COMPATIBLE


class LocalModelAdapter(DeterministicTestAdapter):
    provider_type = ProviderType.LOCAL


class ModelRegistry:
    REGISTRY_VERSION = MODEL_ARCHITECTURE_VERSION

    def __init__(self, store: SQLiteStore, workspace: Path | None = None, architecture_version: str = MODEL_ARCHITECTURE_VERSION, seed_defaults: bool = True):
        self.store = store; self.workspace = Path(workspace).expanduser().resolve() if workspace else None; self.architecture_version = architecture_version
        if seed_defaults: self._seed_defaults()

    def _seed_defaults(self) -> None:
        if not self.store.find_model_providers(limit=1):
            self.register_provider(ModelProvider("provider_deterministic", "Deterministic Test Provider", ProviderType.DETERMINISTIC_TEST, enabled=True, lifecycle_state=ProviderLifecycle.ACTIVE, provenance={"source": "built_in"}))
        if not self.store.find_models(limit=1):
            self.register(Model("model_deterministic", "provider_deterministic", "Deterministic Test Model", "1.0", [ModelCapability("reasoning", "reasoning", .8), ModelCapability("planning", "planning", .8), ModelCapability("analysis", "analysis", .8), ModelCapability("structured_output", "structured_output", .8)], ModelContextProfile(8192, 8192, 2048, True), ["text"], True, True, ["deterministic", "analysis"], provenance=ModelProvenance("built_in", "model_deterministic"), metadata={"offline": True}))

    def register_provider(self, provider: ModelProvider, actor: str = "system") -> ModelProvider:
        if actor in {"provider", "model", "specialist", "autonomous"}: raise PermissionError("provider self-registration is forbidden")
        errors = provider.validate()
        if errors: raise ValueError("Invalid provider: " + "; ".join(errors))
        provider.updated_at = utc_now(); self.store.save_model_provider(provider); self._emit(EventType.MODEL_PROVIDER_REGISTERED, {"provider": provider.to_dict()})
        return provider

    def register(self, model: Model, actor: str = "system") -> Model:
        if actor in {"model", "provider", "autonomous"}: raise PermissionError("model self-registration is forbidden")
        errors = model.validate()
        if errors: raise ValueError("Invalid model: " + "; ".join(errors))
        provider = self.get_provider(model.provider_id)
        if provider is None: raise ValueError("model provider is not registered")
        if not provider.enabled or provider.lifecycle_state in {ProviderLifecycle.DISABLED, ProviderLifecycle.REMOVED}: raise PermissionError("model provider is not enabled")
        model.updated_at = utc_now(); self.store.save_model(model); self._emit(EventType.MODEL_REGISTERED, {"model": model.to_dict()})
        return model

    def get_provider(self, provider_id: str) -> ModelProvider | None:
        row = self.store.model_provider_by_id(provider_id); return self._provider_from_payload(row["payload"]) if row else None

    def list_providers(self, enabled: bool | None = None, limit: int = 100) -> list[ModelProvider]: return [self._provider_from_payload(row["payload"]) for row in self.store.find_model_providers(enabled, limit)]

    def get(self, model_id: str) -> Model | None:
        row = self.store.model_by_id(model_id); return self._model_from_payload(row["payload"]) if row else None

    def list(self, provider_id: str | None = None, enabled: bool | None = None, limit: int = 200) -> list[Model]: return [self._model_from_payload(row["payload"]) for row in self.store.find_models(provider_id, enabled, limit)]

    def set_enabled(self, model_id: str, enabled: bool, actor: str = "system") -> Model:
        if actor in {"model", "provider", "autonomous"}: raise PermissionError("model self-modification is forbidden")
        model = self.get(model_id)
        if not model: raise KeyError(model_id)
        model.available = enabled; model.lifecycle_state = ModelLifecycle.ACTIVE if enabled else ModelLifecycle.DISABLED; model.health.state = ModelHealthState.HEALTHY if enabled else ModelHealthState.DISABLED; self.store.save_model(model); return model

    def record_outcome(self, model_id: str, success: bool, latency_ms: float = 0.0, timeout: bool = False, structured_valid: bool = True, tool_call_valid: bool = True, verified: bool = False, error: str = "") -> Model:
        model = self.get(model_id)
        if not model: raise KeyError(model_id)
        model.health.record(success, latency_ms, timeout, structured_valid, tool_call_valid, verified, error)
        model.performance_profile.sample_count += 1; model.performance_profile.reliability = model.health.reliability; model.performance_profile.average_latency_ms = model.health.average_latency_ms; model.performance_profile.output_validity_rate = ((model.performance_profile.output_validity_rate * max(model.performance_profile.sample_count - 1, 0)) + float(structured_valid)) / model.performance_profile.sample_count
        model.updated_at = utc_now(); self.store.save_model(model); self.store.save_model_health(model_id, model.health); self._emit(EventType.MODEL_HEALTH_CHANGED, {"model_id": model_id, "health": model.health.to_dict()})
        if model.health.state is ModelHealthState.UNAVAILABLE: self._emit(EventType.MODEL_CIRCUIT_OPENED, {"model_id": model_id, "reason": "bounded repeated failure"})
        return model

    def discover(self, adapters: Iterable[ProviderAdapter]) -> list[Model]:
        discovered: list[Model] = []
        for adapter in adapters:
            for data in adapter.discover_models()[:50]:
                model_id = str(data.get("model_id", "")); provider_id = str(data.get("provider_id", "")) or f"provider_{adapter.provider_type.value}"
                if not model_id: continue
                if self.get_provider(provider_id) is None:
                    try: self.register_provider(ModelProvider(provider_id, provider_id, adapter.provider_type, enabled=True, lifecycle_state=ProviderLifecycle.ACTIVE, provenance={"source": "adapter_discovery", "trusted": False}))
                    except (ValueError, PermissionError): continue
                existing = self.get(model_id)
                if existing: discovered.append(existing); continue
                caps = [ModelCapability(f"{name}", str(name), .5) for name in data.get("capabilities", [])]
                model = Model(model_id, provider_id, str(data.get("name", model_id)), str(data.get("version", "1.0")), caps, structured_output_support=bool(data.get("structured_output_support", False)), tool_use_support=bool(data.get("tool_use_support", False)), provenance=ModelProvenance("adapter_discovery", model_id, "adapter"), metadata={"discovered": True, "untrusted_metadata": True})
                try: discovered.append(self.register(model, actor="system"))
                except (ValueError, PermissionError): pass
                self._emit(EventType.MODEL_DISCOVERED, {"model_id": model_id, "provider_id": provider_id, "trusted": False})
        return discovered

    @staticmethod
    def _provider_from_payload(data: dict[str, Any]) -> ModelProvider:
        data = dict(data); data["provider_type"] = ProviderType(data["provider_type"]); data["lifecycle_state"] = ProviderLifecycle(data.get("lifecycle_state", ProviderLifecycle.REGISTERED.value)); return ModelProvider(**{key: data[key] for key in ModelProvider.__dataclass_fields__ if key in data})

    @staticmethod
    def _model_from_payload(data: dict[str, Any]) -> Model:
        data = dict(data); data["capabilities"] = [ModelCapability(**item) for item in data.get("capabilities", [])]; data["context_profile"] = ModelContextProfile(**data.get("context_profile", {})); data["cost_profile"] = ModelCostProfile(**data.get("cost_profile", {})); data["performance_profile"] = ModelPerformanceProfile(**data.get("performance_profile", {})); health = data.get("health", {}); health["state"] = ModelHealthState(health.get("state", ModelHealthState.HEALTHY.value)); data["health"] = ModelHealth(**{key: health[key] for key in ModelHealth.__dataclass_fields__ if key in health}); data["provenance"] = ModelProvenance(**data.get("provenance", {})); policy = data.get("policy", {}); data["policy"] = ModelPolicy(**{key: policy[key] for key in ModelPolicy.__dataclass_fields__ if key in policy}); data["version_lineage"] = ModelVersion(**data.get("version_lineage", {})); data["lifecycle_state"] = ModelLifecycle(data.get("lifecycle_state", ModelLifecycle.ACTIVE.value)); return Model(**{key: data[key] for key in Model.__dataclass_fields__ if key in data})

    def _emit(self, event_type: EventType, payload: dict[str, Any], task_id: str = "model-registry") -> None:
        try: self.store.append_event(Event(task_id, event_type, _safe(payload)))
        except Exception: pass


class ModelContextManager:
    def __init__(self, max_context_bytes: int = _MAX_CONTEXT_BYTES, max_output_tokens: int = 2048): self.max_context_bytes = max(1000, max_context_bytes); self.max_output_tokens = max(1, max_output_tokens)

    def build(self, task_id: str, goal: str, input: Any, memory_evidence: Sequence[dict[str, Any]] = (), environment_evidence: Sequence[dict[str, Any]] = (), specialist_context: Sequence[dict[str, Any]] = (), external_observations: Sequence[dict[str, Any]] = (), context_budget: int | None = None, output_budget: int | None = None) -> ModelContext:
        budget = min(self.max_context_bytes, max(1000, int(context_budget or self.max_context_bytes))); truncation: list[dict[str, Any]] = []
        groups = {"memory": list(memory_evidence)[:20], "environment": list(environment_evidence)[:20], "specialist": list(specialist_context)[:20], "external": list(external_observations)[:20]}
        data = {"task_id": task_id, "goal": goal, "input": input, **{f"{key}_evidence": value for key, value in groups.items()}}
        encoded = _canonical(data).encode("utf-8")
        if len(encoded) > budget:
            for key in ("external_evidence", "specialist_evidence", "environment_evidence", "memory_evidence"):
                values = data.get(key, [])
                while values and len(_canonical(data).encode("utf-8")) > budget: values.pop()
                if values: truncation.append({"field": key, "reason": "context_budget", "preserved_provenance": True})
        if len(_canonical(data).encode("utf-8")) > budget:
            original = str(data["input"]); data["input"] = {"truncated": True, "content_hash": _hash(original), "excerpt": original[: max(100, budget // 4)]}; truncation.append({"field": "input", "reason": "context_budget", "preserved_provenance": True})
        if len(_canonical(data).encode("utf-8")) > budget:
            data["goal"] = {"truncated": True, "content_hash": _hash(goal), "excerpt": goal[: max(80, budget // 8)]}; truncation.append({"field": "goal", "reason": "context_budget", "preserved_provenance": True})
        if len(_canonical(data).encode("utf-8")) > budget:
            for key in ("external_evidence", "specialist_evidence", "environment_evidence", "memory_evidence"):
                if data.get(key):
                    data[key] = [{"truncated": True, "count": len(data[key]), "content_hash": _hash(data[key]), "provenance_preserved": True}]
                    truncation.append({"field": key, "reason": "context_budget", "preserved_provenance": True})
        effective_goal = data["goal"] if isinstance(data["goal"], str) else json.dumps(data["goal"], sort_keys=True)
        context = ModelContext(task_id, effective_goal, data["input"], data.get("memory_evidence", []), data.get("environment_evidence", []), data.get("specialist_evidence", []), data.get("external_evidence", []), min(self.max_output_tokens, int(output_budget or self.max_output_tokens)), budget, truncation, {"source": "context_manager", "bounded": True})
        if len(_canonical(context.to_dict()).encode("utf-8")) > budget:
            context.goal = {"truncated": True, "content_hash": _hash(goal)}
            context.input = {"truncated": True, "content_hash": _hash(input_value)}
            context.memory_evidence = context.environment_evidence = context.specialist_context = context.external_observations = []
            context.truncation.append({"field": "context", "reason": "final_serialized_budget", "preserved_provenance": True})
            context.provenance = {"source": "context_manager", "bounded": True, "minimal": True}
        context.context_hash = _hash(context.to_dict())
        if len(_canonical(context.to_dict()).encode("utf-8")) > budget:
            context.provenance = {"bounded": True}
            context.truncation = [{"field": "context", "reason": "hard_budget", "preserved_provenance": True}]
            context.context_hash = _hash(context.to_dict())
        return context


class ModelRouter:
    def __init__(self, registry: ModelRegistry, learning: Any | None = None, store: SQLiteStore | None = None): self.registry = registry; self.learning = learning; self.store = store or registry.store

    def route(self, task_id: str, goal: str = "", task: Mapping[str, Any] | None = None, capability_requirements: Iterable[str] = (), context_requirements: Mapping[str, Any] | None = None, risk: RiskLevel | str = RiskLevel.LOW, resource_constraints: Mapping[str, Any] | None = None, available_models: Sequence[Model] | None = None, historical_evidence: Sequence[Mapping[str, Any]] = (), specialist: Any | None = None) -> ModelSelection:
        task = dict(task or {}); requirements = list(dict.fromkeys(str(item) for item in capability_requirements)); context_requirements = dict(context_requirements or {}); resource_constraints = dict(resource_constraints or {}); risk_value = getattr(risk, "value", str(risk));
        if specialist is not None:
            metadata = getattr(specialist, "model_metadata", {}) or {}; requirements.extend(str(item) for item in metadata.get("preferred_capabilities", [])); context_requirements.update(metadata.get("context_requirements", {})); requirements = list(dict.fromkeys(requirements))
        models = list(available_models) if available_models is not None else self.registry.list(enabled=True)
        candidates: list[ModelCandidate] = []
        for model in sorted(models, key=lambda item: (item.name, item.version, item.model_id)):
            reasons: list[str] = []; rejected = False; rejection = ""
            provider = self.registry.get_provider(model.provider_id)
            if not provider or not provider.enabled or provider.lifecycle_state in {ProviderLifecycle.DISABLED, ProviderLifecycle.REMOVED} or provider.network_policy.get("enabled") is False: rejected = True; rejection = "provider is unavailable or disallowed by network policy"
            elif not model.available or model.lifecycle_state in {ModelLifecycle.DISABLED, ModelLifecycle.REMOVED, ModelLifecycle.DEPRECATED} or model.health.circuit_open(): rejected = True; rejection = "model health or lifecycle blocks selection"
            elif risk_value not in model.policy.permitted_risk: rejected = True; rejection = "model policy does not permit this risk class"
            elif model.policy.approval_required and risk_value in {RiskLevel.HIGH.value, RiskLevel.CRITICAL.value}: rejected = True; rejection = "model policy requires approval"
            elif context_requirements.get("min_context_tokens", 0) > model.context_profile.max_context_tokens: rejected = True; rejection = "context requirement exceeds model limit"
            elif resource_constraints.get("max_latency_ms") is not None and model.performance_profile.average_latency_ms and model.performance_profile.average_latency_ms > float(resource_constraints["max_latency_ms"]): rejected = True; rejection = "latency exceeds task ceiling"
            matched = len(set(requirements) & model.capability_names()); missing = sorted(set(requirements) - model.capability_names())
            if missing and requirements: reasons.append("missing capabilities: " + ", ".join(missing))
            else: reasons.append(f"matched {matched}/{len(requirements)} required capabilities")
            if task.get("structured_output") and not model.structured_output_support: rejected = True; rejection = "structured output is unsupported"
            if task.get("tool_use") and not model.tool_use_support: rejected = True; rejection = "tool use is unsupported"
            historical = self._historical(model.model_id, historical_evidence)
            learning_bonus = float(self.learning.score(model.model_id, requirements) if self.learning and hasattr(self.learning, "score") else 0.0)
            adaptive_learning = getattr(self, "adaptive_learning", None)
            adaptive_bonus = float(adaptive_learning.score(f"model:{model.model_id}", "preference")) if adaptive_learning and hasattr(adaptive_learning, "score") else 0.0
            if adaptive_bonus: reasons.append(f"bounded adaptive preference adjustment {adaptive_bonus:+.4f} from persisted evidence")
            capability_score = matched / max(1, len(requirements)); quality = sum(item.quality for item in model.capabilities if item.name in requirements) / max(1, matched)
            health_score = model.health.reliability; latency_penalty = min(0.25, (model.performance_profile.average_latency_ms / 10000.0) if model.performance_profile.average_latency_ms else 0.0); cost_penalty = min(0.15, (model.cost_profile.input_cost_per_million + model.cost_profile.output_cost_per_million) / 100.0)
            score = round(0.40 * capability_score + 0.20 * quality + 0.18 * model.performance_profile.reliability + 0.12 * health_score + 0.08 * historical + learning_bonus + adaptive_bonus - latency_penalty - cost_penalty, 6)
            candidates.append(ModelCandidate(model, score, reasons, rejected, rejection))
        accepted = [item for item in candidates if not item.rejected]
        accepted.sort(key=lambda item: (-item.score, item.model.name, item.model.version, item.model.model_id))
        rejected_items = [item for item in candidates if item.rejected]
        ranked = accepted + rejected_items; selected = accepted[0].model.model_id if accepted else None; fallback = [item.model.model_id for item in accepted[1:4]]
        confidence = round(max(0.0, min(1.0, accepted[0].score if accepted else 0.0)), 6)
        explanation = "No policy-compatible model is available." if not selected else f"Selected {selected} using deterministic capability, health, reliability, latency, cost, historical, and bounded-learning evidence."
        selection = ModelSelection(new_id("model_selection"), task_id, selected, ranked, explanation, confidence, fallback, {"goal": goal[:2000], "capability_requirements": requirements, "context_requirements": context_requirements, "risk": risk_value, "resource_constraints": resource_constraints})
        self.store.save_model_selection(selection); self._emit(EventType.MODEL_SELECTION_RECORDED, {"selection": selection.to_dict()}, task_id); return selection

    select = route

    def _historical(self, model_id: str, evidence: Sequence[Mapping[str, Any]]) -> float:
        records = [item for item in evidence if str(item.get("model_id", "")) == model_id]
        if not records: return 0.5
        return sum(1.0 if item.get("verified", item.get("success", False)) else 0.0 for item in records) / len(records)

    def _emit(self, event_type: EventType, payload: dict[str, Any], task_id: str) -> None:
        try: self.store.append_event(Event(task_id, event_type, _safe(payload)))
        except Exception: pass


class ModelFallbackEngine:
    def __init__(self, router: ModelRouter, max_attempts: int = 2): self.router = router; self.max_attempts = max(1, min(4, max_attempts))

    def plan(self, selection: ModelSelection) -> ModelFallbackPlan: return ModelFallbackPlan(selection.selected_model_id, selection.fallback_model_ids[: self.max_attempts - 1], self.max_attempts, ["fallback is bounded and uses the same task contract", "policy and health are rechecked before every attempt"])
    def alternatives(self, selection: ModelSelection) -> list[str]: return self.plan(selection).fallback_model_ids


class ModelContextLimitError(ValueError): pass


class ModelEvaluationEngine:
    def __init__(self, registry: ModelRegistry, store: SQLiteStore | None = None, evaluator_version: str = "model-evaluator-v1", verifier: Callable[[InferenceRequest, ModelResponse], bool] | None = None): self.registry = registry; self.store = store or registry.store; self.evaluator_version = evaluator_version; self.verifier = verifier

    def evaluate(self, model_id: str, benchmark: ModelBenchmark | Sequence[Mapping[str, Any]] | None = None, adapter: ProviderAdapter | None = None, trial_count: int | None = None) -> ModelEvaluation:
        model = self.registry.get(model_id)
        if not model: raise KeyError(model_id)
        benchmark = benchmark or ModelBenchmark("model-benchmark-v1", "1.0", [{"task_id": "default", "input": "deterministic evaluation"}], 3, 0)
        if not isinstance(benchmark, ModelBenchmark): benchmark = ModelBenchmark("model-benchmark-v1", "1.0", [dict(item) for item in benchmark], trial_count or 3, 0)
        errors = benchmark.validate()
        if errors: raise ValueError("Invalid model benchmark: " + "; ".join(errors))
        adapter = adapter or DeterministicTestAdapter(model_id)
        evaluation_id = new_id("model_evaluation"); trials: list[ModelTrial] = []; total = max(1, min(100, trial_count or benchmark.trial_count)); self._emit(EventType.MODEL_EVALUATION_STARTED, {"evaluation_id": evaluation_id, "model_id": model_id, "benchmark_id": benchmark.benchmark_id})
        for case in benchmark.tasks:
            for number in range(1, total + 1):
                task_id = f"{benchmark.benchmark_id}:{case['task_id']}:{number}"; request = InferenceRequest(model_id, model.provider_id, task_id, str(case.get("purpose", "benchmark evaluation")), str(case.get("input_classification", "benchmark")), case.get("input"), case.get("output_schema"), benchmark.timeout_seconds, {"max_output_bytes": _MAX_OUTPUT_BYTES, "max_tokens": 2048}, {"source": "benchmark", "approval": False}, risk=RiskLevel.LOW.value, structured_output=bool(case.get("output_schema")))
                started = time.monotonic(); response: ModelResponse | None = None; failure = ""; retries = 0
                try: response = adapter.infer(request); response.latency_ms = response.latency_ms or (time.monotonic() - started) * 1000
                except TimeoutError as exc: failure = str(exc); response = ModelResponse(model_id, task_id, InferenceStatus.TIMEOUT, error=failure, latency_ms=(time.monotonic() - started) * 1000)
                except Exception as exc: failure = f"{type(exc).__name__}: {exc}"; response = ModelResponse(model_id, task_id, InferenceStatus.FAILED, error=failure, latency_ms=(time.monotonic() - started) * 1000)
                valid = bool(response.output_schema_valid or not request.structured_output); verified = bool(self.verifier(request, response)) if self.verifier and response.success else bool(case.get("verified", response.success and valid))
                success = response.success and valid and not any(term in _canonical(response.output).lower() for term in _PROTECTED_TERMS)
                score = round((float(success) * .5) + (float(verified) * .3) + (float(valid) * .2), 6)
                trial = ModelTrial(new_id("model_trial"), evaluation_id, model_id, benchmark.benchmark_id, number, success, verified, valid, score, response.latency_ms, response.resource_usage, retries, failure or response.error, _hash(response.output), {"seed": benchmark.deterministic_seed, "benchmark_version": benchmark.version, "task_id": task_id})
                trials.append(trial); self.store.save_model_trial(trial); self._emit(EventType.MODEL_TRIAL_COMPLETED, {"trial": trial.to_dict()})
                self.registry.record_outcome(model_id, success, response.latency_ms, response.status is InferenceStatus.TIMEOUT, valid, not response.tool_calls or model.tool_use_support, verified, response.error)
        metrics = self._metrics(trials); minimum_verification = float(benchmark.success_criteria.get("minimum_verification_rate", 0.0)); decision = ModelComparisonDecision.BETTER if metrics["verification_success_rate"] >= minimum_verification and metrics["success_rate"] >= .5 else ModelComparisonDecision.INCONCLUSIVE; evaluation = ModelEvaluation(evaluation_id, model_id, benchmark.benchmark_id, benchmark.version, len(trials), metrics, trials, decision, ["Verifier result is authoritative where configured.", "Self-reported confidence was not used as the success criterion."], self.evaluator_version); self.store.save_model_evaluation(evaluation); self._emit(EventType.MODEL_EVALUATION_COMPLETED, {"evaluation": evaluation.to_dict()}); return evaluation

    def compare(self, model_ids: Sequence[str], benchmark: ModelBenchmark | Sequence[Mapping[str, Any]], adapters: Mapping[str, ProviderAdapter] | None = None) -> ModelComparison:
        if len(model_ids) < 2: raise ValueError("comparative evaluation requires at least two models")
        if not isinstance(benchmark, ModelBenchmark): benchmark = ModelBenchmark("model-benchmark-v1", "1.0", [dict(item) for item in benchmark], 3, 0)
        evaluations = {model_id: self.evaluate(model_id, benchmark, (adapters or {}).get(model_id)) for model_id in model_ids}; ordered = sorted(model_ids, key=lambda key: (-float(evaluations[key].metrics.get("verification_success_rate", 0.0)), -float(evaluations[key].metrics.get("success_rate", 0.0)), int(float(evaluations[key].metrics.get("mean_latency_ms", 0.0)) // 10), key)); top = evaluations[ordered[0]].metrics; second = evaluations[ordered[1]].metrics; delta = float(top.get("success_rate", 0.0)) - float(second.get("success_rate", 0.0)); verification_delta = float(top.get("verification_success_rate", 0.0)) - float(second.get("verification_success_rate", 0.0)); decision = ModelComparisonDecision.BETTER if delta >= .10 or verification_delta >= .10 else ModelComparisonDecision.WORSE if delta <= -.10 or verification_delta <= -.10 else ModelComparisonDecision.NO_CHANGE if delta == 0 and verification_delta == 0 else ModelComparisonDecision.INCONCLUSIVE; result = ModelComparison(new_id("model_comparison"), benchmark.benchmark_id, evaluations, decision, ordered, [f"Compared {len(model_ids)} models under benchmark {benchmark.version}.", f"Top-vs-second success delta={delta:.3f}; verification delta={verification_delta:.3f}."], {"seed": benchmark.deterministic_seed, "trial_count": benchmark.trial_count, "benchmark_version": benchmark.version}); self._emit(EventType.MODEL_COMPARISON_COMPLETED, result.to_dict()); return result

    compare_models = compare
    evaluate_model = evaluate
    run = evaluate

    @staticmethod
    def _metrics(trials: Sequence[ModelTrial]) -> dict[str, Any]:
        if not trials: return {"success_rate": 0.0, "verification_success_rate": 0.0, "output_validity_rate": 0.0, "failure_rate": 1.0, "mean_latency_ms": 0.0, "mean_score": 0.0, "reasoning_quality": 0.0, "reliability": 0.0, "resource_usage": {}, "retries": 0, "tool_call_correctness": 0.0, "specialist_task_performance": 0.0}
        count = len(trials); return {"success_rate": sum(item.success for item in trials) / count, "verification_success_rate": sum(item.verified for item in trials) / count, "output_validity_rate": sum(item.output_valid for item in trials) / count, "failure_rate": sum(not item.success for item in trials) / count, "mean_latency_ms": sum(item.latency_ms for item in trials) / count, "mean_score": sum(item.score for item in trials) / count, "reasoning_quality": sum(item.score for item in trials) / count, "reliability": sum(item.success for item in trials) / count, "resource_usage": {"mean_latency_ms": sum(item.latency_ms for item in trials) / count, "max_output_bytes": max((len(item.output_hash) for item in trials), default=0)}, "retries": sum(item.retries for item in trials), "tool_call_correctness": sum(item.output_valid for item in trials) / count, "specialist_task_performance": sum(item.success for item in trials) / count}

    def _emit(self, event_type: EventType, payload: dict[str, Any], task_id: str = "model-evaluation") -> None:
        try: self.store.append_event(Event(task_id, event_type, _safe(payload)))
        except Exception: pass


class LearningEngine:
    PROTECTED_COMPONENTS = _PROTECTED_TERMS | {"model_registry_schema", "routing_architecture", "provider_adapters", "learning_algorithm", "promotion_rules"}

    def __init__(self, store: SQLiteStore, policy: LearningPolicy | None = None, memory: Any | None = None):
        self.store = store; self.policy = policy or LearningPolicy(); self.memory = memory; self.values: dict[tuple[str, str], float] = {}; self._load_values(); self.store.save_learning_policy(self.policy)

    def observe(self, observation: LearningObservation) -> LearningOutcome:
        self.store.save_learning_observation(observation); self._emit(EventType.LEARNING_OBSERVATION_RECORDED, {"observation": observation.to_dict()}, observation.task_id)
        value = (float(observation.success) * .55 + float(observation.verified) * .35 + float(observation.output_valid) * .10) - min(.20, observation.retries * .03) - (.05 if observation.fallback else 0.0); quality = max(0.0, min(1.0, value)); outcome = LearningOutcome(new_id("learning_outcome"), observation.observation_id, quality, quality, .9 if observation.verified else .55, LearningEvidenceKind.MODEL_PERFORMANCE, observation.evidence_ids, "Bounded execution and verification evidence; confidence fields are not authoritative"); self.store.save_learning_outcome(outcome); self._emit(EventType.LEARNING_OUTCOME_RECORDED, {"outcome": outcome.to_dict()}, observation.task_id)
        if self.memory and hasattr(self.memory, "capture_model_performance"):
            try: self.memory.capture_model_performance({"model_id": observation.model_id, "task_category": observation.task_category, "success": observation.success, "verified": observation.verified, "latency_ms": observation.latency_ms, "failure_category": observation.failure_category})
            except Exception: pass
        return outcome

    record_observation = observe

    def score(self, model_id: str, capabilities: Iterable[str] = ()) -> float:
        bonus = 0.0
        for capability in capabilities: bonus += self.values.get((f"model:{model_id}", str(capability)), 0.0)
        return max(-self.policy.maximum_adjustment, min(self.policy.maximum_adjustment, bonus))

    def propose_adjustment(self, affected_component: str, parameter: str, previous_value: float, proposed_value: float, source_evidence: Sequence[str], reason: str, expected_benefit: str = "", confidence: float = 0.0, risk: str = RiskLevel.LOW.value, evaluator_version: str = "learning-evaluator-v1") -> LearningAdjustment:
        forbidden = (affected_component + " " + parameter).lower()
        if any(term in forbidden for term in self.PROTECTED_COMPONENTS): return self._blocked_adjustment(affected_component, parameter, previous_value, proposed_value, source_evidence, "protected authority cannot be learned", confidence, risk, evaluator_version)
        delta = proposed_value - previous_value
        if abs(delta) > self.policy.maximum_adjustment or confidence < self.policy.confidence_threshold or len(source_evidence) < self.policy.minimum_evidence or risk in {RiskLevel.HIGH.value, RiskLevel.CRITICAL.value}:
            reason = "adjustment exceeded a bounded learning gate" if reason == "" else reason
            return self._blocked_adjustment(affected_component, parameter, previous_value, proposed_value, source_evidence, reason, confidence, risk, evaluator_version)
        recent = [item for item in self.store.find_learning_adjustments(affected_component, limit=20) if item.get("status") == LearningAdjustmentStatus.APPLIED.value]
        if recent and self.policy.cooldown_seconds > 0:
            created = _now_dt(recent[0].get("created_at"));
            if created and (datetime.now(timezone.utc) - created).total_seconds() < self.policy.cooldown_seconds: return self._blocked_adjustment(affected_component, parameter, previous_value, proposed_value, source_evidence, "learning cooldown is active", confidence, risk, evaluator_version)
        adjustment = LearningAdjustment(new_id("learning_adjustment"), affected_component, parameter, previous_value, proposed_value, reason, expected_benefit, confidence, risk, list(source_evidence)[:32], evaluator_version, rollback_value=previous_value); self.store.save_learning_adjustment(adjustment); self._emit(EventType.LEARNING_ADJUSTMENT_PROPOSED, {"adjustment": adjustment.to_dict()}); return adjustment

    def apply(self, adjustment: LearningAdjustment | str) -> LearningAdjustment:
        if isinstance(adjustment, str):
            row = self.store.learning_adjustment_by_id(adjustment)
            if not row: raise KeyError(adjustment)
            adjustment = self._adjustment_from_payload(row["payload"])
        if adjustment.status is not LearningAdjustmentStatus.PROPOSED: return adjustment
        if any(term in (adjustment.affected_component + " " + adjustment.parameter).lower() for term in self.PROTECTED_COMPONENTS): adjustment.status = LearningAdjustmentStatus.BLOCKED; self.store.save_learning_adjustment(adjustment); return adjustment
        self.values[(adjustment.affected_component, adjustment.parameter)] = adjustment.proposed_value; adjustment.status = LearningAdjustmentStatus.APPLIED; adjustment.applied_at = utc_now(); self.store.save_learning_adjustment(adjustment); self._emit(EventType.LEARNING_ADJUSTMENT_APPLIED, {"adjustment": adjustment.to_dict()}); return adjustment

    apply_adjustment = apply

    def rollback(self, adjustment: LearningAdjustment | str) -> LearningAdjustment:
        if isinstance(adjustment, str):
            row = self.store.learning_adjustment_by_id(adjustment)
            if not row: raise KeyError(adjustment)
            adjustment = self._adjustment_from_payload(row["payload"])
        value = adjustment.rollback_value if adjustment.rollback_value is not None else adjustment.previous_value; self.values[(adjustment.affected_component, adjustment.parameter)] = value; adjustment.status = LearningAdjustmentStatus.ROLLED_BACK; adjustment.rolled_back_at = utc_now(); self.store.save_learning_adjustment(adjustment); self._emit(EventType.LEARNING_ADJUSTMENT_ROLLED_BACK, {"adjustment": adjustment.to_dict()}); return adjustment

    rollback_adjustment = rollback

    def decay(self) -> None:
        for key, value in list(self.values.items()): self.values[key] = value * max(0.0, min(1.0, self.policy.decay))

    def explore(self, task_id: str, risk: RiskLevel | str, eligible_model_ids: Sequence[str], seed: int | None = None) -> ExplorationDecision:
        seed = self.policy.exploration_seed if seed is None else seed; risk_value = getattr(risk, "value", str(risk)); eligible = bool(eligible_model_ids) and risk_value in {RiskLevel.LOW.value, RiskLevel.MEDIUM.value} and self.policy.exploration_rate > 0; rng = random.Random(seed + sum(ord(char) for char in task_id)); do = eligible and rng.random() < self.policy.exploration_rate; chosen = eligible_model_ids[1] if do and len(eligible_model_ids) > 1 else (eligible_model_ids[0] if eligible_model_ids else None); result = ExplorationDecision(eligible, do, chosen, "bounded deterministic exploration" if do else "exploration not selected or not eligible", seed); self._emit(EventType.MODEL_EXPLORATION_RECORDED, {"task_id": task_id, "decision": result.to_dict()}, task_id); return result

    def statistics(self) -> dict[str, Any]:
        return {"observation_count": len(self.store.find_learning_observations()), "outcome_count": len(self.store.find_learning_outcomes()), "adjustment_count": len(self.store.find_learning_adjustments()), "applied_adjustments": sum(row.get("status") == LearningAdjustmentStatus.APPLIED.value for row in self.store.find_learning_adjustments()), "policy": self.policy.to_dict(), "learned_value_count": len(self.values)}

    def _load_values(self) -> None:
        for row in reversed(self.store.find_learning_adjustments(limit=1000)):
            try:
                adjustment = self._adjustment_from_payload(row["payload"])
                if adjustment.status is LearningAdjustmentStatus.APPLIED: self.values[(adjustment.affected_component, adjustment.parameter)] = adjustment.proposed_value
            except (TypeError, ValueError, KeyError): pass

    def _blocked_adjustment(self, component: str, parameter: str, previous: float, proposed: float, evidence: Sequence[str], reason: str, confidence: float, risk: str, evaluator: str) -> LearningAdjustment:
        adjustment = LearningAdjustment(new_id("learning_adjustment"), component, parameter, previous, proposed, reason, "", confidence, risk, list(evidence)[:32], evaluator, rollback_value=previous, status=LearningAdjustmentStatus.BLOCKED); self.store.save_learning_adjustment(adjustment); self._emit(EventType.LEARNING_ADJUSTMENT_BLOCKED, {"adjustment": adjustment.to_dict()}); return adjustment

    @staticmethod
    def _adjustment_from_payload(data: dict[str, Any]) -> LearningAdjustment:
        data = dict(data); data["status"] = LearningAdjustmentStatus(data.get("status", LearningAdjustmentStatus.PROPOSED.value)); return LearningAdjustment(**{key: data[key] for key in LearningAdjustment.__dataclass_fields__ if key in data})

    def _emit(self, event_type: EventType, payload: dict[str, Any], task_id: str = "learning") -> None:
        try: self.store.append_event(Event(task_id, event_type, _safe(payload)))
        except Exception: pass


class ModelIntelligence:
    """Sovereign Evo facade: model intelligence advises; Kernel and Verifier remain authoritative."""
    def __init__(self, store: SQLiteStore, workspace: Path | None = None, registry: ModelRegistry | None = None, adapters: Mapping[str, ProviderAdapter] | None = None, memory: Any | None = None, policy: LearningPolicy | None = None, verifier: Callable[[InferenceRequest, ModelResponse], bool] | None = None, external_integrations: Any | None = None, evolution_orchestrator: Any | None = None, adaptive_learning: Any | None = None):
        self.store = store; self.workspace = workspace; self.registry = registry or ModelRegistry(store, workspace); self.adapters = dict(adapters or {}); self.learning = LearningEngine(store, policy, memory); self.adaptive_learning = adaptive_learning; self.router = ModelRouter(self.registry, self.learning, store); self.router.adaptive_learning = adaptive_learning; self.fallback = ModelFallbackEngine(self.router); self.context = ModelContextManager(); self.evaluator = ModelEvaluationEngine(self.registry, store, verifier=verifier); self.safe_mode = False; self.kill_switch = False; self.verifier = verifier; self.external_integrations = external_integrations; self.evolution_orchestrator = evolution_orchestrator
        self.model_registry = self.registry; self.model_router = self.router; self.learning_engine = self.learning; self.model_evaluator = self.evaluator

    def register_provider(self, provider: ModelProvider, actor: str = "system") -> ModelProvider: return self.registry.register_provider(provider, actor)
    def register_adapter(self, model_id: str, adapter: ProviderAdapter) -> ProviderAdapter:
        if not self.registry.get(model_id): raise KeyError(model_id)
        self.adapters[model_id] = adapter
        return adapter
    def register_model(self, model: Model, adapter: ProviderAdapter | None = None, actor: str = "system") -> Model:
        result = self.registry.register(model, actor)
        if adapter: self.adapters[model.model_id] = adapter
        return result
    def discover(self) -> list[Model]: return self.registry.discover(self.adapters.values())
    discover_models = discover
    def list_models(self) -> list[Model]: return self.registry.list()
    def model_health(self, model_id: str | None = None) -> list[dict[str, Any]]:
        return self.store.find_model_health(model_id, 200) if model_id else [{"model_id": item.model_id, "health": item.health.to_dict()} for item in self.registry.list()]
    def select_model(self, *args: Any, **kwargs: Any) -> ModelSelection: return self.router.route(*args, **kwargs)
    route = select_model

    def infer(self, request: InferenceRequest, adapter: ProviderAdapter | None = None, fallback: bool = True, verifier: Callable[[InferenceRequest, ModelResponse], bool] | None = None) -> ModelResponse:
        errors = request.validate()
        if errors: self._emit(EventType.MODEL_REQUEST_BLOCKED, {"request": request.to_dict(), "errors": errors}, request.task_id); return ModelResponse(request.model_id, request.task_id, InferenceStatus.BLOCKED, error="; ".join(errors))
        model = self.registry.get(request.model_id)
        if not model or model.provider_id != request.provider_id: return ModelResponse(request.model_id, request.task_id, InferenceStatus.BLOCKED, error="model/provider identity is not registered")
        if self.kill_switch: return ModelResponse(request.model_id, request.task_id, InferenceStatus.BLOCKED, error="model inference is blocked by kill switch")
        if self.safe_mode and request.risk in {RiskLevel.MEDIUM.value, RiskLevel.HIGH.value, RiskLevel.CRITICAL.value}: return ModelResponse(request.model_id, request.task_id, InferenceStatus.BLOCKED, error="side-effecting model workflow is blocked in safe mode")
        if model.policy.approval_required and request.permission_context.get("approval_status") != "approved": return ModelResponse(request.model_id, request.task_id, InferenceStatus.BLOCKED, error="model policy requires explicit approval")
        if model.policy.network_required and not request.permission_context.get("network_access", False): return ModelResponse(request.model_id, request.task_id, InferenceStatus.BLOCKED, error="network access is not approved by the model permission context")
        if model.policy.network_required and self.external_integrations is None: return ModelResponse(request.model_id, request.task_id, InferenceStatus.BLOCKED, error="network model requires the governed external integration boundary")
        if request.tool_schema and not model.tool_use_support: return ModelResponse(request.model_id, request.task_id, InferenceStatus.BLOCKED, error="model does not support declared tool calls")
        if len(_canonical(request.input).encode("utf-8")) > int(request.resource_limits.get("max_input_bytes", _MAX_CONTEXT_BYTES)): return ModelResponse(request.model_id, request.task_id, InferenceStatus.BLOCKED, error="input exceeds bounded context limit")
        adapter = adapter or self.adapters.get(request.model_id)
        if adapter is None: return ModelResponse(request.model_id, request.task_id, InferenceStatus.BLOCKED, error="no approved provider adapter is bound")
        self._emit(EventType.MODEL_REQUEST_VALIDATED, {"request": request.to_dict()}, request.task_id); self._emit(EventType.MODEL_INFERENCE_STARTED, {"model_id": request.model_id, "correlation_id": request.correlation_id}, request.task_id); started = time.monotonic()
        response: ModelResponse
        tool_valid = True
        try:
            response = adapter.tool_call(request) if request.tool_schema else (adapter.structured_output(request) if request.structured_output else adapter.infer(request))
            response.latency_ms = response.latency_ms or (time.monotonic() - started) * 1000
            if response.latency_ms > request.timeout_seconds * 1000:
                response.status = InferenceStatus.TIMEOUT
                response.error = "model inference exceeded the bounded timeout"
            if response.output is not None and len(_canonical(response.output).encode("utf-8")) > int(request.resource_limits.get("max_output_bytes", _MAX_OUTPUT_BYTES)): response = ModelResponse(request.model_id, request.task_id, InferenceStatus.INVALID, error="model output exceeds bounded size", latency_ms=response.latency_ms)
            response.output_schema_valid = _schema_accepts(request.output_schema, response.output)
            tool_valid = self._validate_tool_calls(request.tool_schema, response)
            if request.structured_output and not response.output_schema_valid: response.status = InferenceStatus.INVALID; response.error = "structured model output failed the declared schema"
            if request.tool_schema and not tool_valid: response.status = InferenceStatus.INVALID; response.error = "model tool-call output failed the declared tool schema"
            if any(term in _canonical(response.output).lower() for term in _PROTECTED_TERMS) or any(term in _canonical(response.tool_calls).lower() for term in _PROTECTED_TERMS): response.provenance["authority_injection_detected"] = True; response.verified = False
            callback = verifier or self.verifier
            response.verified = bool(callback(request, response)) if callback and response.success else False
            self.registry.record_outcome(request.model_id, response.success, response.latency_ms, response.status is InferenceStatus.TIMEOUT, response.output_schema_valid, tool_valid, response.verified, response.error)
            observation = LearningObservation(request.task_id, request.model_id, request.input_classification, response.success, response.verified, response.output_schema_valid, response.latency_ms, 0, False, response.error, environment=request.permission_context)
            self.learning.observe(observation); self._emit(EventType.MODEL_INFERENCE_COMPLETED if response.success else EventType.MODEL_INFERENCE_FAILED, {"response": response.to_dict()}, request.task_id); return response
        except TimeoutError as exc:
            self.registry.record_outcome(request.model_id, False, (time.monotonic() - started) * 1000, True, False, True, False, str(exc)); response = ModelResponse(request.model_id, request.task_id, InferenceStatus.TIMEOUT, error=str(exc), latency_ms=(time.monotonic() - started) * 1000)
        except Exception as exc:
            self.registry.record_outcome(request.model_id, False, (time.monotonic() - started) * 1000, False, False, True, False, f"{type(exc).__name__}: {exc}"); response = ModelResponse(request.model_id, request.task_id, InferenceStatus.FAILED, error=f"{type(exc).__name__}: {exc}", latency_ms=(time.monotonic() - started) * 1000)
        alternatives: list[str] = []
        if fallback:
            alternatives = [item.model_id for item in self.registry.list(enabled=True) if item.model_id != request.model_id and item.provider_id != request.provider_id][: self.fallback.max_attempts - 1]
            for model_id in alternatives:
                alternate = self.registry.get(model_id)
                bound = self.adapters.get(model_id)
                if not alternate or not bound or alternate.health.circuit_open(): continue
                retry = InferenceRequest(model_id, alternate.provider_id, request.task_id, request.purpose, request.input_classification, request.input, request.output_schema, request.timeout_seconds, request.resource_limits, request.permission_context, request.correlation_id, request.risk, request.structured_output, request.tool_schema)
                self._emit(EventType.MODEL_FALLBACK_SELECTED, {"from_model_id": request.model_id, "to_model_id": model_id}, request.task_id); return self.infer(retry, bound, fallback=False, verifier=verifier)
        self.learning.observe(LearningObservation(request.task_id, request.model_id, request.input_classification, False, False, False, response.latency_ms, 0, bool(alternatives), response.error, environment=request.permission_context))
        self._emit(EventType.MODEL_INFERENCE_FAILED, {"response": response.to_dict()}, request.task_id); return response

    @staticmethod
    def _validate_tool_calls(schema: list[dict[str, Any]], response: ModelResponse) -> bool:
        if not schema: return True
        allowed = {str(item.get("name")) for item in schema if isinstance(item, dict) and item.get("name")}
        calls = response.tool_calls or ([] if response.output is None else (response.output.get("tool_calls", []) if isinstance(response.output, dict) else []))
        return isinstance(calls, list) and bool(calls) and all(isinstance(call, dict) and str(call.get("name")) in allowed and isinstance(call.get("arguments", {}), dict) for call in calls)

    execute = infer
    inference = infer
    tool_calls = infer
    execute_tool_call = infer

    def route_specialist_task(self, specialist: Any, task_id: str, goal: str, capabilities: Iterable[str] = (), risk: RiskLevel | str = RiskLevel.LOW) -> ModelSelection:
        return self.router.route(task_id, goal, capability_requirements=capabilities, risk=risk, specialist=specialist)

    def flexibility_recommendation(self, request: InferenceRequest, response: ModelResponse | None = None) -> dict[str, Any]:
        failure = str(getattr(response, "error", "") or getattr(response, "status", "")).lower()
        adaptations = ["bounded_retry"]
        if "timeout" in failure: adaptations = ["alternate_model", "context_reduction", "bounded_retry"]
        elif "structured" in failure or "schema" in failure: adaptations = ["alternate_model", "output_schema_review", "bounded_retry"]
        elif "provider" in failure or "unavailable" in failure: adaptations = ["alternate_provider", "alternate_model"]
        return {"task_id": request.task_id, "model_id": request.model_id, "failure": failure, "adaptations": adaptations[:3], "bounded": True, "governance_required": True, "execution_authority": "kernel", "replan_authority": "flexibility_engine"}

    adapt_after_failure = flexibility_recommendation
    observe = lambda self, observation: self.learning.observe(observation)
    apply_learning_adjustment = lambda self, adjustment: self.learning.apply(adjustment)
    rollback_learning_adjustment = lambda self, adjustment: self.learning.rollback(adjustment)

    def evolution_evidence(self, limit: int = 20) -> list[dict[str, Any]]:
        return [{"model_id": item.model_id, "performance": item.performance_profile.to_dict(), "health": item.health.to_dict(), "source": "model_intelligence", "governance_required": True} for item in self.registry.list(limit=limit)]

    def generate_evolution_opportunity(self, model_id: str, reason: str = "model-performance evidence warrants governed review") -> Any:
        evidence = self.registry.get(model_id)
        if not evidence: raise KeyError(model_id)
        if self.evolution_orchestrator is None:
            return {"model_id": model_id, "reason": reason, "evidence": self.evolution_evidence(), "status": "evidence_only", "governance_required": True}
        from .orchestrator import EvolutionOpportunity, OrchestrationPath
        opportunity = EvolutionOpportunity(new_id("opportunity"), [], [], reason, max(1, evidence.performance_profile.sample_count), "medium", ["model_routing"], ["model_intelligence"], ["model:" + model_id], "moderate", OrchestrationPath.EVOLUTION, .65, architecture_version=evidence.architecture_version, metadata={"model_evidence": self.evolution_evidence(), "governance_required": True})
        return self.evolution_orchestrator.create_work_item(opportunity)

    def statistics(self) -> dict[str, Any]: return {"model_count": len(self.registry.list()), "provider_count": len(self.registry.list_providers()), "healthy_models": sum(item.health.state is ModelHealthState.HEALTHY for item in self.registry.list()), "degraded_models": sum(item.health.state is ModelHealthState.DEGRADED for item in self.registry.list()), "unavailable_models": sum(item.health.state is ModelHealthState.UNAVAILABLE for item in self.registry.list()), "selection_count": len(self.store.find_model_selections()), "evaluation_count": len(self.store.find_model_evaluations()), "learning": self.learning.statistics()}

    def set_safe_mode(self, enabled: bool) -> None: self.safe_mode = bool(enabled)
    def activate_kill_switch(self) -> None: self.kill_switch = True
    def clear_kill_switch(self, actor: str = "system") -> None:
        if actor in {"model", "autonomous", "agent"}: raise PermissionError("model layer cannot clear kill switch")
        self.kill_switch = False

    def _emit(self, event_type: EventType, payload: dict[str, Any], task_id: str) -> None:
        try: self.store.append_event(Event(task_id, event_type, _safe(payload)))
        except Exception: pass


# Compatibility and discoverability aliases.
ModelAdapter = ProviderAdapter
ModelProviderAdapter = ProviderAdapter
DeterministicModelAdapter = DeterministicTestAdapter
ModelOrchestrator = ModelIntelligence
ModelEvaluation = ModelEvaluation
LearningAdjustmentRecord = LearningAdjustment
