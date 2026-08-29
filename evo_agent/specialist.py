from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
import threading
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
import hashlib
import json
from pathlib import Path
from typing import Any, Callable, Iterable

from .models import Event, EventType, RiskLevel, new_id, utc_now
from .storage import SQLiteStore
from .version import __version__


SPECIALIST_SCHEMA_VERSION = "specialist-v1"
_MAX_CONTEXT_BYTES = 12000

#: Executions currently in flight, keyed by the SQLite file they write through. Deliberately module-level
#: rather than per-engine: the ceiling is on *nesting*, and a subagent that builds its own
#: :class:`SpecialistDelegationEngine` over the same database is still a subagent. A per-instance set would
#: be trivially escaped by exactly that - which is the one move the limit exists to stop. Guarded by a
#: lock because the pool threads are the writers, and keyed on the store path because two workspaces in one
#: process must not block each other.
_IN_FLIGHT: dict[str, set[str]] = {}
_IN_FLIGHT_LOCK = threading.Lock()
_MAX_OUTPUT_BYTES = 24000
_PROTECTED_TERMS = {"governance", "approval", "promotion", "rollback", "protected_core", "metamorphosis", "evolver", "credentials", "production", "kill_switch"}


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def _hash(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _bounded(value: Any, maximum: int = _MAX_CONTEXT_BYTES) -> Any:
    if isinstance(value, str):
        return value[:maximum]
    encoded = _canonical(value)
    if len(encoded.encode("utf-8")) <= maximum:
        return value
    return {"truncated": True, "content_hash": _hash(value), "excerpt": encoded[:maximum]}


def _safe_payload(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): "[REDACTED]" if any(term in str(k).lower() for term in ("token", "secret", "password", "credential", "private_key")) else _safe_payload(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_safe_payload(item) for item in value]
    return _bounded(value, _MAX_OUTPUT_BYTES)


class SpecialistType(str, Enum):
    RESEARCH = "research"
    PLANNING = "planning"
    CODING = "coding"
    ANALYSIS = "analysis"
    VERIFICATION = "verification"
    DOCUMENTATION = "documentation"
    DATA = "data"


class SpecialistLifecycle(str, Enum):
    REGISTERED = "registered"
    ACTIVE = "active"
    DEGRADED = "degraded"
    DISABLED = "disabled"
    DEPRECATED = "deprecated"
    REMOVED = "removed"


class SpecialistHealthState(str, Enum):
    UNKNOWN = "unknown"
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    CIRCUIT_OPEN = "circuit_open"
    DISABLED = "disabled"


class SpecialistTaskStatus(str, Enum):
    CREATED = "created"
    QUEUED = "queued"
    RUNNING = "running"
    WAITING = "waiting"
    COMPLETED = "completed"
    FAILED = "failed"
    BLOCKED = "blocked"
    CANCELLED = "cancelled"
    EXPIRED = "expired"
    INCONCLUSIVE = "inconclusive"
    VERIFIED = "verified"


class SpecialistRisk(str, Enum):
    READ_ONLY = "read_only"
    LOW_RISK_WRITE = "low_risk_write"
    HIGH_RISK_WRITE = "high_risk_write"
    DESTRUCTIVE = "destructive"
    COMMUNICATION = "communication"

    @property
    def requires_approval(self) -> bool:
        return self in {self.HIGH_RISK_WRITE, self.DESTRUCTIVE, self.COMMUNICATION}


class SpecialistMessageType(str, Enum):
    TASK = "task"
    RESULT = "result"
    QUESTION = "question"
    CLARIFICATION = "clarification"
    EVIDENCE = "evidence"
    ERROR = "error"
    STATUS = "status"
    HANDOFF = "handoff"
    CANCEL = "cancel"


class SpecialistTrustLevel(str, Enum):
    UNTRUSTED = "untrusted"
    OBSERVED = "observed"
    VERIFIED = "verified"
    UNKNOWN = "unknown"


class EvidenceKind(str, Enum):
    CLAIM = "claim"
    OBSERVATION = "observation"
    EVIDENCE = "evidence"
    INFERENCE = "inference"


class VerificationStatus(str, Enum):
    UNVERIFIED = "unverified"
    VERIFIED = "verified"
    FAILED = "failed"
    INCONCLUSIVE = "inconclusive"


class DelegationStatus(str, Enum):
    CREATED = "created"
    RUNNING = "running"
    COMPLETED = "completed"
    PARTIAL = "partial"
    FAILED = "failed"
    CANCELLED = "cancelled"
    INCONCLUSIVE = "inconclusive"


@dataclass
class SpecialistVersion:
    version: str = "1.0"
    architecture_version: str = ""
    parent_version: str | None = None
    lineage: list[str] = field(default_factory=list)
    created_at: str = field(default_factory=utc_now)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class SpecialistProvenance:
    source: str = "built_in"
    source_id: str = ""
    actor: str = "system"
    created_at: str = field(default_factory=utc_now)
    lineage: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class SpecialistCapability:
    capability_id: str
    name: str
    description: str
    risk: SpecialistRisk = SpecialistRisk.READ_ONLY
    required_tools: list[str] = field(default_factory=list)
    required_integrations: list[str] = field(default_factory=list)
    data_classification: str = "internal"
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=utc_now)

    def validate(self) -> list[str]:
        errors: list[str] = []
        if not self.capability_id or not self.name:
            errors.append("specialist capability identity is required")
        if any(term in self.name.lower() for term in _PROTECTED_TERMS):
            errors.append("specialist capability cannot target protected authority")
        return errors

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["risk"] = self.risk.value
        return _safe_payload(data)


@dataclass
class SpecialistPermission:
    permission_id: str
    name: str
    scope: str
    approval_required: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return _safe_payload(asdict(self))


@dataclass
class SpecialistPolicy:
    allowed_tools: list[str] = field(default_factory=list)
    allowed_integrations: list[str] = field(default_factory=list)
    allowed_filesystem_scope: str = ""
    prohibited_actions: list[str] = field(default_factory=lambda: sorted(_PROTECTED_TERMS | {"execute_arbitrary_code", "install_plugins", "modify_registry", "self_approve"}))
    resource_limits: dict[str, Any] = field(default_factory=lambda: {"timeout_seconds": 30, "max_output_bytes": _MAX_OUTPUT_BYTES, "max_tool_calls": 8, "max_external_operations": 4})
    permissions: list[SpecialistPermission] = field(default_factory=list)
    approval_required: bool = False

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["permissions"] = [item.to_dict() for item in self.permissions]
        return _safe_payload(data)


@dataclass
class SpecialistHealth:
    state: SpecialistHealthState = SpecialistHealthState.UNKNOWN
    success_count: int = 0
    failure_count: int = 0
    timeout_count: int = 0
    conflict_count: int = 0
    average_duration: float = 0.0
    reliability: float = 0.5
    last_error: str = ""
    last_run: str | None = None

    def record(self, success: bool, duration: float = 0.0, failure: str = "", timeout: bool = False) -> None:
        self.last_run = utc_now()
        if success:
            self.success_count += 1
        else:
            self.failure_count += 1
            self.last_error = failure[:512]
        if timeout:
            self.timeout_count += 1
        total = self.success_count + self.failure_count
        self.reliability = self.success_count / total if total else 0.5
        self.average_duration = ((self.average_duration * max(0, total - 1)) + max(0.0, duration)) / max(1, total)
        self.state = SpecialistHealthState.HEALTHY if success and self.failure_count < 3 else SpecialistHealthState.CIRCUIT_OPEN if self.failure_count >= 3 and self.reliability < 0.5 else SpecialistHealthState.DEGRADED

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["state"] = self.state.value
        return data


@dataclass
class Specialist:
    specialist_id: str
    name: str
    purpose: str
    specialist_type: SpecialistType
    capabilities: list[SpecialistCapability] = field(default_factory=list)
    allowed_tools: list[str] = field(default_factory=list)
    allowed_integrations: list[str] = field(default_factory=list)
    allowed_filesystem_scope: str = ""
    risk_classification: SpecialistRisk = SpecialistRisk.READ_ONLY
    resource_limits: dict[str, Any] = field(default_factory=dict)
    model_metadata: dict[str, Any] = field(default_factory=dict)
    architecture_version: str = ""
    version_lineage: SpecialistVersion = field(default_factory=SpecialistVersion)
    lifecycle_state: SpecialistLifecycle = SpecialistLifecycle.REGISTERED
    provenance: SpecialistProvenance = field(default_factory=SpecialistProvenance)
    health: SpecialistHealth = field(default_factory=SpecialistHealth)
    enabled: bool = True
    created_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)

    def validate(self) -> list[str]:
        errors: list[str] = []
        if not self.specialist_id or not self.name or not self.purpose:
            errors.append("specialist identity and purpose are required")
        if not isinstance(self.specialist_type, SpecialistType):
            errors.append("specialist type is invalid")
        for capability in self.capabilities:
            errors.extend(capability.validate())
        if any(term in (self.name + " " + self.purpose).lower() for term in _PROTECTED_TERMS):
            errors.append("specialist cannot target protected authority")
        if not self.allowed_filesystem_scope:
            errors.append("specialist filesystem scope is required")
        if self.lifecycle_state is SpecialistLifecycle.ACTIVE and not self.enabled:
            errors.append("disabled specialist cannot be active")
        return errors

    def capability_names(self) -> set[str]:
        return {item.name for item in self.capabilities} | {item.capability_id for item in self.capabilities}

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["specialist_type"] = self.specialist_type.value
        data["risk_classification"] = self.risk_classification.value
        data["lifecycle_state"] = self.lifecycle_state.value
        data["capabilities"] = [item.to_dict() for item in self.capabilities]
        data["health"] = self.health.to_dict()
        data["version_lineage"] = self.version_lineage.to_dict()
        data["provenance"] = self.provenance.to_dict()
        return _safe_payload(data)


@dataclass
class SpecialistInput:
    goal: str
    data: dict[str, Any] = field(default_factory=dict)
    provenance: dict[str, Any] = field(default_factory=dict)
    trust_level: SpecialistTrustLevel = SpecialistTrustLevel.UNTRUSTED
    relevance: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["trust_level"] = self.trust_level.value
        data["data"] = _safe_payload(self.data)
        return data


@dataclass
class SpecialistTaskContract:
    contract_id: str
    specialist_task_id: str
    parent_task_id: str
    specialist_id: str
    goal: str
    scope: str
    allowed_capabilities: list[str] = field(default_factory=list)
    allowed_tools: list[str] = field(default_factory=list)
    allowed_integrations: list[str] = field(default_factory=list)
    workspace_scope: str = ""
    expected_output_schema: dict[str, Any] = field(default_factory=lambda: {"type": "object"})
    success_criteria: list[str] = field(default_factory=list)
    resource_limits: dict[str, Any] = field(default_factory=dict)
    timeout_seconds: float = 30.0
    deadline: str | None = None
    approval_requirements: list[str] = field(default_factory=list)
    prohibited_actions: list[str] = field(default_factory=lambda: sorted(_PROTECTED_TERMS | {"execute_arbitrary_code", "install_plugins", "self_approve"}))
    verification_requirements: list[str] = field(default_factory=lambda: ["central_verifier_required"])
    dependencies: list[str] = field(default_factory=list)
    risk: SpecialistRisk = SpecialistRisk.READ_ONLY
    architecture_version: str = ""
    scope_hash: str = ""
    created_at: str = field(default_factory=utc_now)

    def immutable_view(self) -> dict[str, Any]:
        return {key: getattr(self, key) for key in ("parent_task_id", "specialist_id", "goal", "scope", "allowed_capabilities", "allowed_tools", "allowed_integrations", "workspace_scope", "expected_output_schema", "success_criteria", "resource_limits", "timeout_seconds", "deadline", "approval_requirements", "prohibited_actions", "verification_requirements", "dependencies", "risk", "architecture_version")}

    def refresh_hash(self) -> str:
        view = self.immutable_view()
        view["risk"] = self.risk.value
        self.scope_hash = _hash(view)
        return self.scope_hash

    def validate(self, workspace: Path | None = None) -> list[str]:
        errors: list[str] = []
        if not self.contract_id or not self.specialist_task_id or not self.parent_task_id or not self.specialist_id:
            errors.append("contract identities are required")
        if not self.goal or not self.scope:
            errors.append("contract goal and scope are required")
        if not self.workspace_scope:
            errors.append("contract workspace scope is required")
        if workspace:
            try:
                scope = Path(self.workspace_scope).expanduser().resolve()
                root = Path(workspace).expanduser().resolve()
                scope.relative_to(root)
            except ValueError:
                errors.append("contract workspace scope escapes the configured workspace")
        if any(term in self.allowed_tools + self.allowed_integrations for term in _PROTECTED_TERMS):
            errors.append("contract requests a protected authority")
        if not self.scope_hash or self.scope_hash != _hash({**self.immutable_view(), "risk": self.risk.value}):
            errors.append("contract scope hash is invalid")
        if self.timeout_seconds <= 0:
            errors.append("contract timeout must be positive")
        return errors

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["risk"] = self.risk.value
        return _safe_payload(data)


@dataclass
class SpecialistOutput:
    specialist_task_id: str
    claim: Any = None
    observations: list[Any] = field(default_factory=list)
    evidence: list[dict[str, Any]] = field(default_factory=list)
    inference: Any = None
    confidence: float = 0.0
    verification_status: VerificationStatus = VerificationStatus.UNVERIFIED
    success: bool = True
    error: str = ""
    resource_usage: dict[str, Any] = field(default_factory=dict)
    provenance: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["verification_status"] = self.verification_status.value
        data = _safe_payload(data)
        data["specialist_task_id"] = self.specialist_task_id
        return data


@dataclass
class SpecialistEvidence:
    evidence_id: str
    result_id: str
    specialist_task_id: str
    parent_task_id: str
    evidence_kind: EvidenceKind
    claim: Any
    source: str
    confidence: float = 0.0
    trust_level: SpecialistTrustLevel = SpecialistTrustLevel.UNTRUSTED
    verification_status: VerificationStatus = VerificationStatus.UNVERIFIED
    provenance: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=utc_now)

    def authority_score(self) -> tuple[int, float]:
        source_score = {"governance": 5, "kernel": 5, "verifier": 5, "current_observation": 4, "specialist": 2, "memory": 1, "inference": 0}.get(self.source, 1)
        verification_score = {VerificationStatus.VERIFIED: 3, VerificationStatus.INCONCLUSIVE: 1, VerificationStatus.UNVERIFIED: 0, VerificationStatus.FAILED: -1}[self.verification_status]
        return source_score + verification_score, self.confidence

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["evidence_kind"] = self.evidence_kind.value
        data["trust_level"] = self.trust_level.value
        data["verification_status"] = self.verification_status.value
        data["claim"] = _bounded(_safe_payload(self.claim), _MAX_OUTPUT_BYTES)
        return data


@dataclass
class SpecialistTask:
    specialist_task_id: str
    parent_task_id: str
    specialist_id: str
    goal: str
    status: SpecialistTaskStatus = SpecialistTaskStatus.CREATED
    contract_id: str = ""
    attempt_count: int = 0
    retry_budget: int = 0
    deadline: str | None = None
    progress: str = "not_started"
    failure_class: str = ""
    last_error: str = ""
    result_id: str | None = None
    evidence_ids: list[str] = field(default_factory=list)
    resource_usage: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["status"] = self.status.value
        return _safe_payload(data)


@dataclass
class SpecialistMessage:
    message_id: str
    sender: str
    recipient: str
    parent_task_id: str
    message_type: SpecialistMessageType
    payload: dict[str, Any]
    correlation_id: str
    trust_level: SpecialistTrustLevel = SpecialistTrustLevel.UNTRUSTED
    provenance: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=utc_now)

    def validate(self, contract: SpecialistTaskContract | None = None) -> list[str]:
        errors: list[str] = []
        if not self.sender or not self.recipient or not self.parent_task_id or not self.correlation_id:
            errors.append("message identity is required")
        if any(term in _canonical(self.payload).lower() for term in ("approve promotion", "disable governance", "modify protected core", "execute arbitrary code")):
            errors.append("message contains prohibited authority-injection content")
        if contract and self.parent_task_id != contract.parent_task_id:
            errors.append("message parent task does not match contract")
        return errors

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["message_type"] = self.message_type.value
        data["trust_level"] = self.trust_level.value
        data["payload"] = _safe_payload(self.payload)
        return data


@dataclass
class DelegationRun:
    delegation_id: str
    parent_task_id: str
    status: DelegationStatus = DelegationStatus.CREATED
    specialist_task_ids: list[str] = field(default_factory=list)
    active_specialists: int = 0
    completed_specialists: int = 0
    failed_specialists: int = 0
    retries: int = 0
    conflicts: int = 0
    fusion_id: str | None = None
    resource_usage: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["status"] = self.status.value
        return _safe_payload(data)


@dataclass
class EvidenceConflict:
    conflict_id: str
    parent_task_id: str
    subject: str
    evidence_ids: list[str]
    values: list[Any]
    status: str = "unresolved"
    resolution: str = "verification_required"
    created_at: str = field(default_factory=utc_now)

    def to_dict(self) -> dict[str, Any]:
        return _safe_payload(asdict(self))


@dataclass
class EvidenceFusion:
    fusion_id: str
    parent_task_id: str
    evidence_ids: list[str]
    supported_claims: list[dict[str, Any]] = field(default_factory=list)
    conflicts: list[EvidenceConflict] = field(default_factory=list)
    unsupported_claims: list[Any] = field(default_factory=list)
    confidence: float = 0.0
    status: str = "inconclusive"
    uncertainty: list[str] = field(default_factory=list)
    created_at: str = field(default_factory=utc_now)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["conflicts"] = [item.to_dict() for item in self.conflicts]
        return _safe_payload(data)


@dataclass
class SpecialistContext:
    parent_task_id: str
    specialist_task_id: str
    specialist_id: str
    goal: str
    contract: dict[str, Any]
    memory_evidence: list[dict[str, Any]] = field(default_factory=list)
    environment: dict[str, Any] = field(default_factory=dict)
    capabilities: list[dict[str, Any]] = field(default_factory=list)
    external_observations: list[dict[str, Any]] = field(default_factory=list)
    parent_constraints: dict[str, Any] = field(default_factory=dict)
    provenance: dict[str, Any] = field(default_factory=dict)
    trust_level: SpecialistTrustLevel = SpecialistTrustLevel.UNTRUSTED
    relevance: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["trust_level"] = self.trust_level.value
        data["memory_evidence"] = [_bounded(_safe_payload(item), 2000) for item in self.memory_evidence[:8]]
        data["environment"] = _bounded(_safe_payload(self.environment), 3000)
        data["capabilities"] = [_bounded(_safe_payload(item), 1500) for item in self.capabilities[:16]]
        data["external_observations"] = [_bounded(_safe_payload(item), 2000) for item in self.external_observations[:8]]
        data["parent_constraints"] = _bounded(_safe_payload(self.parent_constraints), 2000)
        data["contract"] = _bounded(_safe_payload(self.contract), 4000)
        return data


@dataclass
class SpecialistLimits:
    max_concurrent_specialists: int = 3
    max_specialists_per_delegation: int = 8
    max_task_duration: float = 30.0
    max_parent_duration: float = 120.0
    max_retries: int = 1
    circuit_breaker_threshold: int = 3
    max_context_bytes: int = _MAX_CONTEXT_BYTES
    max_output_bytes: int = _MAX_OUTPUT_BYTES
    #: How far below the Evo loop a delegated task may delegate again. One, and it is not a throughput
    #: setting: a tree of subagents is a tree of context windows, and every isolation guarantee in this
    #: module (bounded prompt, no sibling memory, no inherited authority, single-verifier attribution) has
    #: to be re-proved at each level. The kernel is the only thing that turns a result into state, so a
    #: second level multiplies the paths into it while adding no capability.
    max_delegation_depth: int = 1
    #: The turn ceiling an executor may be given. The engine cannot count an executor's turns - the
    #: executor owns its loop - so what the engine enforces is the ceiling *in the contract it hands over*:
    #: a contract asking for more is clamped down on the way out (E3, tighten only).
    max_turns_per_specialist: int = 8

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class ContextIsolation:
    """Builds least-privilege, bounded specialist context; no full-store access is exposed."""

    def __init__(self, workspace: Path, max_context_bytes: int = _MAX_CONTEXT_BYTES):
        self.workspace = Path(workspace).expanduser().resolve()
        self.max_context_bytes = max_context_bytes

    def build(self, task: SpecialistTask, contract: SpecialistTaskContract, specialist: Specialist, memory_evidence: Iterable[dict[str, Any]] = (), environment: dict[str, Any] | None = None, capabilities: Iterable[dict[str, Any]] = (), external_observations: Iterable[dict[str, Any]] = (), parent_constraints: dict[str, Any] | None = None) -> SpecialistContext:
        permitted_memory = [_bounded(_safe_payload(item), 2000) for item in list(memory_evidence)[:8]]
        permitted_environment = _safe_payload(dict(environment or {}))
        permitted_capabilities = [_safe_payload(item) for item in list(capabilities)[:16] if str(item.get("name", item.get("capability_id", ""))) in set(contract.allowed_capabilities) or not contract.allowed_capabilities]
        permitted_external = [_safe_payload(item) for item in list(external_observations)[:8] if str(item.get("integration_id", "")) in set(contract.allowed_integrations) or not contract.allowed_integrations]
        context = SpecialistContext(task.parent_task_id, task.specialist_task_id, specialist.specialist_id, contract.goal, contract.to_dict(), permitted_memory, permitted_environment, permitted_capabilities, permitted_external, _safe_payload(dict(parent_constraints or {})), {"source": "specialist_context_builder", "contract_id": contract.contract_id, "created_at": utc_now()}, SpecialistTrustLevel.UNTRUSTED, {"memory_count": len(permitted_memory), "capability_count": len(permitted_capabilities), "external_observation_count": len(permitted_external)})
        if len(_canonical(context.to_dict()).encode("utf-8")) > self.max_context_bytes:
            context.memory_evidence = context.memory_evidence[:2]
            context.external_observations = context.external_observations[:2]
            context.parent_constraints = {"truncated": True, "reason": "context ceiling"}
        return context


class SpecialistRegistry:
    REGISTRY_VERSION = "specialist-registry-v1"

    def __init__(self, store: SQLiteStore, workspace: Path, architecture_version: str = "", seed_defaults: bool = True):
        self.store = store
        self.workspace = Path(workspace).expanduser().resolve()
        self.architecture_version = architecture_version
        if seed_defaults:
            self._seed_defaults()

    def register(self, specialist: Specialist, actor: str = "system") -> Specialist:
        if actor == specialist.specialist_id:
            raise PermissionError("specialists cannot modify their own registry entry")
        if specialist.architecture_version == "":
            specialist.architecture_version = self.architecture_version
        errors = specialist.validate()
        if errors:
            raise ValueError("; ".join(errors))
        if self.store.specialist_by_id(specialist.specialist_id):
            raise ValueError("specialist ID already exists")
        specialist.lifecycle_state = SpecialistLifecycle.ACTIVE if specialist.enabled else SpecialistLifecycle.DISABLED
        specialist.updated_at = utc_now()
        self.store.save_specialist(specialist)
        return specialist

    register_specialist = register

    def get(self, specialist_id: str) -> Specialist | None:
        row = self.store.specialist_by_id(specialist_id)
        return specialist_from_row(row) if row else None

    def list(self, specialist_type: SpecialistType | str | None = None, enabled: bool | None = None, limit: int = 100) -> list[Specialist]:
        value = specialist_type.value if isinstance(specialist_type, SpecialistType) else specialist_type
        return [specialist_from_row(row) for row in self.store.find_specialists(value, enabled, limit)]

    list_specialists = list

    def select(self, required_capabilities: Iterable[str], allowed_specialists: Iterable[str] | None = None, risk: SpecialistRisk = SpecialistRisk.READ_ONLY, limit: int = 1) -> list[Specialist]:
        required = set(required_capabilities)
        allowed = set(allowed_specialists or [])
        candidates = []
        for specialist in self.list(enabled=True):
            if allowed and specialist.specialist_id not in allowed:
                continue
            if specialist.lifecycle_state is not SpecialistLifecycle.ACTIVE or specialist.health.state is SpecialistHealthState.CIRCUIT_OPEN:
                continue
            if risk.requires_approval and specialist.risk_classification is SpecialistRisk.READ_ONLY:
                continue
            overlap = len(required & specialist.capability_names())
            if required and overlap < len(required):
                continue
            candidates.append((overlap, specialist.health.reliability, specialist))
        return [item[2] for item in sorted(candidates, key=lambda item: (-item[0], -item[1], item[2].specialist_id))[:limit]]

    def health(self, specialist_id: str) -> SpecialistHealth:
        specialist = self.get(specialist_id)
        if not specialist:
            raise KeyError(specialist_id)
        return specialist.health

    def record_outcome(self, specialist_id: str, success: bool, duration: float = 0.0, failure: str = "", timeout: bool = False) -> Specialist:
        specialist = self.get(specialist_id)
        if not specialist:
            raise KeyError(specialist_id)
        specialist.health.record(success, duration, failure, timeout)
        specialist.lifecycle_state = SpecialistLifecycle.DEGRADED if specialist.health.state is not SpecialistHealthState.HEALTHY else SpecialistLifecycle.ACTIVE
        specialist.updated_at = utc_now()
        self.store.save_specialist(specialist)
        self.store.save_specialist_health(specialist_id, specialist.health)
        return specialist

    def _seed_defaults(self) -> None:
        if self.store.find_specialists(limit=1):
            return
        roles = [(SpecialistType.RESEARCH, "Research Specialist", "Collect bounded research evidence", ["research", "external_read"]), (SpecialistType.PLANNING, "Planning Specialist", "Decompose and sequence bounded work", ["planning", "decomposition"]), (SpecialistType.CODING, "Coding Specialist", "Propose bounded implementation work", ["coding", "filesystem_write"]), (SpecialistType.ANALYSIS, "Analysis Specialist", "Analyze bounded structured evidence", ["analysis", "data"]), (SpecialistType.VERIFICATION, "Verification Specialist", "Propose verification checks", ["verification"]), (SpecialistType.DOCUMENTATION, "Documentation Specialist", "Draft bounded documentation", ["documentation", "text"]), (SpecialistType.DATA, "Data Specialist", "Transform bounded structured data", ["data", "analysis"])]
        for role, name, purpose, capabilities in roles:
            specialist = Specialist("specialist_" + role.value, name, purpose, role, [SpecialistCapability("cap_" + item, item, purpose) for item in capabilities], [], [], str(self.workspace), SpecialistRisk.READ_ONLY, {"timeout_seconds": 30, "max_output_bytes": _MAX_OUTPUT_BYTES}, {"provider": "evo", "model": "bounded-role"}, self.architecture_version, SpecialistVersion("1.0", self.architecture_version), SpecialistLifecycle.ACTIVE, SpecialistProvenance("built_in", role.value), SpecialistHealth(SpecialistHealthState.HEALTHY), True)
            self.store.save_specialist(specialist)


class EvidenceFusionEngine:
    def __init__(self, store: SQLiteStore):
        self.store = store

    def fuse(self, parent_task_id: str, evidence: Iterable[SpecialistEvidence]) -> EvidenceFusion:
        items = list(evidence)
        grouped: dict[str, list[SpecialistEvidence]] = {}
        for item in items:
            key = str(item.metadata.get("subject", item.metadata.get("claim_key", "claim")))
            grouped.setdefault(key, []).append(item)
        supported: list[dict[str, Any]] = []
        conflicts: list[EvidenceConflict] = []
        uncertainty: list[str] = []
        for subject, group in grouped.items():
            values = [_canonical(item.claim) for item in group]
            unique = list(dict.fromkeys(values))
            ranked = sorted(group, key=lambda item: item.authority_score(), reverse=True)
            if len(unique) > 1:
                conflict = EvidenceConflict(new_id("specialist_conflict"), parent_task_id, subject, [item.evidence_id for item in group], [item.claim for item in group])
                conflicts.append(conflict)
                uncertainty.append(f"conflicting values for {subject}")
                self.store.save_evidence_conflict(conflict)
            else:
                winner = ranked[0]
                supported.append({"subject": subject, "claim": winner.claim, "evidence_ids": [item.evidence_id for item in group], "authority": winner.authority_score()})
        confidence = sum(max(0.0, item.confidence) for item in items) / len(items) if items else 0.0
        status = "conflicted" if conflicts else "supported" if supported else "inconclusive"
        fusion = EvidenceFusion(new_id("fusion"), parent_task_id, [item.evidence_id for item in items], supported, conflicts, [item.claim for item in items if item.evidence_kind is EvidenceKind.INFERENCE and item.verification_status is not VerificationStatus.VERIFIED], confidence, status, uncertainty)
        self.store.save_evidence_fusion(fusion)
        return fusion


class ConflictResolver:
    def __init__(self, store: SQLiteStore):
        self.store = store

    def resolve(self, conflict: EvidenceConflict, strategy: str = "verification") -> EvidenceConflict:
        allowed = {"clarification", "additional_evidence", "verification", "rerun", "escalate", "unresolved"}
        if strategy not in allowed:
            raise ValueError("unsupported conflict resolution strategy")
        conflict.status = "resolved" if strategy in {"verification", "additional_evidence", "rerun"} else "escalated" if strategy == "escalate" else "unresolved"
        conflict.resolution = strategy
        self.store.save_evidence_conflict(conflict)
        return conflict


class SpecialistDelegationEngine:
    """Sovereign-owned, bounded delegation. Specialists are never authorities."""

    ENGINE_VERSION = "delegation-v1"

    def __init__(self, store: SQLiteStore, workspace: Path, registry: SpecialistRegistry | None = None, memory: Any | None = None, capability_intelligence: Any | None = None, flexibility: Any | None = None, external_integrations: Any | None = None, runtime: Any | None = None, verifier: Callable[[SpecialistTaskContract, SpecialistOutput], bool | VerificationStatus] | None = None, limits: SpecialistLimits | None = None, executor: Callable[[SpecialistTaskContract, SpecialistContext], Any] | None = None, adaptive_learning: Any | None = None):
        self.store = store
        self.workspace = Path(workspace).expanduser().resolve()
        self.registry = registry or SpecialistRegistry(store, self.workspace)
        self.memory = memory
        self.capability_intelligence = capability_intelligence
        self.flexibility = flexibility
        self.external_integrations = external_integrations
        self.adaptive_learning = adaptive_learning
        self.runtime = runtime
        self.verifier = verifier
        self.default_executor = executor
        self.limits = limits or SpecialistLimits()
        self.context_builder = ContextIsolation(self.workspace, self.limits.max_context_bytes)
        self.fusion_engine = EvidenceFusionEngine(store)
        self.conflict_resolver = ConflictResolver(store)
        self._ledger = self._ledger_key()

    def _emit(self, event_type: EventType, payload: dict[str, Any], task_id: str) -> None:
        self.store.append_event(Event(task_id, event_type, _safe_payload(payload)))

    def is_complex_goal(self, goal: str, expected_steps: int = 1) -> bool:
        text = str(goal).lower()
        markers = ("research", "compare", "analyze", "analyse", "workflow", "multiple", "then", "and")
        return expected_steps > 1 or sum(text.count(marker) for marker in markers) > 0

    def discover_for_goal(self, goal: str, required_capabilities: Iterable[str] | None = None, limit: int = 3) -> list[Specialist]:
        requirements = list(required_capabilities or (["research"] if any(token in str(goal).lower() for token in ("research", "investigate", "external")) else ["analysis"]))
        candidates = self.registry.select(requirements, limit=max(limit, 8))
        if self.adaptive_learning and hasattr(self.adaptive_learning, "score"):
            candidates.sort(key=lambda item: (-float(self.adaptive_learning.score(f"specialist:{item.specialist_id}", "preference")), item.specialist_id))
        return candidates[:limit]

    def create_contract(self, parent_task_id: str, goal: str, specialist_id: str, scope: str | None = None, allowed_capabilities: Iterable[str] | None = None, allowed_tools: Iterable[str] | None = None, allowed_integrations: Iterable[str] | None = None, expected_output_schema: dict[str, Any] | None = None, success_criteria: Iterable[str] | None = None, resource_limits: dict[str, Any] | None = None, timeout_seconds: float | None = None, deadline: str | None = None, approval_requirements: Iterable[str] | None = None, dependencies: Iterable[str] | None = None, risk: SpecialistRisk = SpecialistRisk.READ_ONLY) -> tuple[SpecialistTask, SpecialistTaskContract]:
        specialist = self.registry.get(specialist_id)
        if not specialist or not specialist.enabled or specialist.lifecycle_state in {SpecialistLifecycle.DISABLED, SpecialistLifecycle.DEPRECATED, SpecialistLifecycle.REMOVED} or specialist.health.state is SpecialistHealthState.CIRCUIT_OPEN:
            raise PermissionError("specialist is not active")
        capabilities = list(allowed_capabilities or specialist.capability_names())
        if not set(capabilities).issubset(specialist.capability_names()):
            raise PermissionError("contract requests a capability outside specialist registration")
        tools = list(allowed_tools or [])
        integrations = list(allowed_integrations or [])
        if any(term in _canonical(tools + integrations).lower() for term in _PROTECTED_TERMS):
            raise ValueError("contract requests a protected authority")
        if not set(tools).issubset(set(specialist.allowed_tools)):
            raise PermissionError("contract requests a tool outside specialist registration")
        if not set(integrations).issubset(set(specialist.allowed_integrations)):
            raise PermissionError("contract requests an integration outside specialist registration")
        scope_path = Path(specialist.allowed_filesystem_scope or self.workspace).expanduser().resolve()
        scope_path.relative_to(self.workspace)
        task_id = new_id("specialist_task")
        contract = SpecialistTaskContract(new_id("contract"), task_id, parent_task_id, specialist_id, goal, scope or goal, capabilities, tools, integrations, str(scope_path), expected_output_schema or {"type": "object"}, list(success_criteria or ["output_schema_valid", "central_verification"]), dict(resource_limits or specialist.resource_limits), float(timeout_seconds or specialist.resource_limits.get("timeout_seconds", self.limits.max_task_duration)), deadline, list(approval_requirements or (["human"] if risk.requires_approval else [])), sorted(set(_PROTECTED_TERMS | {"execute_arbitrary_code", "install_plugins", "self_approve"})), ["central_verifier_required"], list(dependencies or []), risk, specialist.architecture_version)
        contract.refresh_hash()
        errors = contract.validate(self.workspace)
        if errors:
            raise ValueError("; ".join(errors))
        task = SpecialistTask(task_id, parent_task_id, specialist_id, goal, SpecialistTaskStatus.QUEUED, contract.contract_id, 0, min(self.limits.max_retries, int(contract.resource_limits.get("max_retries", self.limits.max_retries))), deadline, "queued", metadata={"approval_status": "pending" if contract.risk.requires_approval else "not_required", "approval_scope_hash": contract.scope_hash if contract.risk.requires_approval else ""})
        self.store.save_specialist_task(task)
        self.store.save_specialist_contract(contract)
        self._emit(EventType.SPECIALIST_TASK_CONTRACT_CREATED, {"task": task.to_dict(), "contract": contract.to_dict()}, parent_task_id)
        return task, contract

    create_task_contract = create_contract

    def build_context(self, task: SpecialistTask, contract: SpecialistTaskContract, memory_evidence: Iterable[dict[str, Any]] = (), environment: dict[str, Any] | None = None, capabilities: Iterable[dict[str, Any]] = (), external_observations: Iterable[dict[str, Any]] = (), parent_constraints: dict[str, Any] | None = None) -> SpecialistContext:
        specialist = self.registry.get(task.specialist_id)
        if not specialist:
            raise KeyError(task.specialist_id)
        return self.context_builder.build(task, contract, specialist, memory_evidence, environment, capabilities, external_observations, parent_constraints)

    def send_message(self, message: SpecialistMessage, contract: SpecialistTaskContract | None = None) -> SpecialistMessage:
        errors = message.validate(contract)
        if errors:
            raise ValueError("; ".join(errors))
        self.store.save_specialist_message(message)
        self._emit(EventType.SPECIALIST_MESSAGE_SENT, {"message": message.to_dict()}, message.parent_task_id)
        return message

    def _validate_output(self, contract: SpecialistTaskContract, output: SpecialistOutput, *, max_tool_calls: int | None = None) -> list[str]:
        errors: list[str] = []
        if max_tool_calls is not None:
            # The engine cannot count an executor's turns, and it will not pretend to. What it *can* do is
            # refuse an output that reports having used more than the ceiling it handed down - which is what
            # turns the clamp from a note into a bound.
            try:
                used = int((output.resource_usage or {}).get("tool_calls", 0) or 0)
            except (TypeError, ValueError):
                used = max_tool_calls + 1  # an unparseable usage report is not evidence of compliance
            if used > max_tool_calls:
                errors.append(f"specialist reported {used} tool calls, above the enforced ceiling of {max_tool_calls}")
        if output.specialist_task_id != contract.specialist_task_id:
            errors.append("specialist output task does not match contract")
        if len(_canonical(output.to_dict()).encode("utf-8")) > int(contract.resource_limits.get("max_output_bytes", self.limits.max_output_bytes)):
            errors.append("specialist output exceeds contract resource limit")
        expected = contract.expected_output_schema.get("type")
        value = output.claim
        if expected == "object" and value is not None and not isinstance(value, dict):
            errors.append("specialist claim does not match object output schema")
        if expected == "array" and value is not None and not isinstance(value, list):
            errors.append("specialist claim does not match array output schema")
        if any(term in _canonical(output.to_dict()).lower() for term in ("disable governance", "approve promotion", "modify protected core", "execute arbitrary code")):
            errors.append("specialist output contains prohibited authority instruction")
        return errors

    def _normalize_output(self, task_id: str, raw: Any) -> SpecialistOutput:
        if isinstance(raw, SpecialistOutput):
            return raw
        if isinstance(raw, dict) and {"claim", "success"} & set(raw):
            return SpecialistOutput(task_id, raw.get("claim"), list(raw.get("observations", [])), list(raw.get("evidence", [])), raw.get("inference"), float(raw.get("confidence", 0.0)), VerificationStatus(raw.get("verification_status", VerificationStatus.UNVERIFIED.value)), bool(raw.get("success", True)), str(raw.get("error", "")), dict(raw.get("resource_usage", {})), dict(raw.get("provenance", {})))
        return SpecialistOutput(task_id, raw, confidence=0.5, provenance={"source": "specialist_executor"})

    def _ledger_key(self) -> str:
        return str(Path(getattr(self.store, "path", "specialist")).resolve())

    def _execution_depth(self) -> int:
        """How many specialist executions are in flight for this database right now."""
        with _IN_FLIGHT_LOCK:
            return len(_IN_FLIGHT.get(self._ledger_key(), set()))

    def execute_task(self, task: SpecialistTask | str, context: SpecialistContext | None = None, executor: Callable[[SpecialistTaskContract, SpecialistContext], Any] | None = None) -> SpecialistOutput:
        # The depth is measured by the engine, not declared by the caller, and it is recorded in a ledger
        # rather than in thread state: the executor is dispatched onto its own thread so that its wall-clock
        # limit can be enforced, and anything stored on a thread would be invisible to the one call - the
        # nested ``delegate`` - that has to be caught.
        task_id = task if isinstance(task, str) else getattr(task, "specialist_task_id", "unknown")
        with _IN_FLIGHT_LOCK:
            _IN_FLIGHT.setdefault(self._ledger_key(), set()).add(str(task_id))
        try:
            return self._execute_task(task, context, executor)
        finally:
            with _IN_FLIGHT_LOCK:
                _IN_FLIGHT.get(self._ledger_key(), set()).discard(str(task_id))

    def _turn_ceiling(self, contract: SpecialistTaskContract) -> tuple[int, int, bool]:
        """``(requested, effective, clamped)`` for the tool-call ceiling an executor is held to.

        Deliberately *not* a mutation of ``contract.resource_limits``: the contract's ``scope_hash`` covers
        that mapping, so rewriting it - even to tighten it - makes the signed document fail
        ``contract.validate()`` with "contract scope hash is invalid". An approved contract is immutable here;
        the ceiling travels to the executor as a parent constraint and to the verifier as an argument.
        """
        ceiling = int(self.limits.max_turns_per_specialist)
        try:
            asked = int((contract.resource_limits or {}).get("max_tool_calls", ceiling) or ceiling)
        except (TypeError, ValueError):
            asked = ceiling
        effective = min(asked, ceiling) if asked > 0 else ceiling
        return asked, effective, effective != asked

    def _constrained_context(self, context: SpecialistContext | None, *, requested: int, effective: int) -> SpecialistContext | None:
        if context is None or requested == effective:
            return context
        from dataclasses import replace

        return replace(
            context,
            parent_constraints={
                **context.parent_constraints,
                "max_tool_calls": effective,
                "max_tool_calls_requested": requested,
                "max_tool_calls_clamped_by": f"SpecialistLimits.max_turns_per_specialist={self.limits.max_turns_per_specialist}",
            },
        )

    def _execute_task(self, task: SpecialistTask | str, context: SpecialistContext | None = None, executor: Callable[[SpecialistTaskContract, SpecialistContext], Any] | None = None) -> SpecialistOutput:
        if isinstance(task, SpecialistTask):
            persisted = self.store.specialist_task_by_id(task.specialist_task_id)
            task_obj = specialist_task_from_row(persisted) if persisted else task
        else:
            task_obj = specialist_task_from_row(self.store.specialist_task_by_id(task))
        row = self.store.specialist_contract_by_task(task_obj.specialist_task_id)
        if not row:
            raise KeyError("specialist contract not found")
        contract = specialist_contract_from_row(row)
        specialist = self.registry.get(task_obj.specialist_id)
        if not specialist:
            raise KeyError(task_obj.specialist_id)
        if self.runtime is not None and (getattr(self.runtime, "safe_mode", False) and contract.risk is not SpecialistRisk.READ_ONLY or getattr(self.runtime, "kill_switch_active", False)):
            task_obj.status = SpecialistTaskStatus.WAITING if not getattr(self.runtime, "kill_switch_active", False) else SpecialistTaskStatus.BLOCKED
            task_obj.last_error = "runtime safety mode blocks specialist side effects" if not getattr(self.runtime, "kill_switch_active", False) else "runtime kill switch blocks specialist execution"
            self.store.save_specialist_task(task_obj)
            self._emit(EventType.SPECIALIST_TASK_BLOCKED, {"task_id": task_obj.specialist_task_id, "reason": task_obj.last_error}, task_obj.parent_task_id)
            return SpecialistOutput(task_obj.specialist_task_id, success=False, error=task_obj.last_error)
        errors = contract.validate(self.workspace)
        if errors:
            task_obj.status = SpecialistTaskStatus.BLOCKED
            task_obj.failure_class = "contract_violation"
            task_obj.last_error = "; ".join(errors)
            self.store.save_specialist_task(task_obj)
            self._emit(EventType.SPECIALIST_TASK_BLOCKED, {"task_id": task_obj.specialist_task_id, "errors": errors}, task_obj.parent_task_id)
            return SpecialistOutput(task_obj.specialist_task_id, success=False, error=task_obj.last_error)
        if contract.risk.requires_approval and task_obj.metadata.get("approval_status") != "approved":
            task_obj.status = SpecialistTaskStatus.WAITING
            task_obj.last_error = "exact human approval is required before specialist execution"
            self.store.save_specialist_task(task_obj)
            self._emit(EventType.SPECIALIST_TASK_BLOCKED, {"task_id": task_obj.specialist_task_id, "reason": task_obj.last_error, "scope_hash": contract.scope_hash}, task_obj.parent_task_id)
            return SpecialistOutput(task_obj.specialist_task_id, success=False, error=task_obj.last_error)
        if context is None:
            context = self.build_context(task_obj, contract)
        if task_obj.deadline:
            try:
                deadline = datetime.fromisoformat(task_obj.deadline.replace("Z", "+00:00"))
                if deadline <= datetime.now(timezone.utc):
                    task_obj.status = SpecialistTaskStatus.EXPIRED
                    task_obj.last_error = "specialist task deadline expired before execution"
                    self.store.save_specialist_task(task_obj)
                    self._emit(EventType.SPECIALIST_TASK_BLOCKED, {"task_id": task_obj.specialist_task_id, "reason": task_obj.last_error}, task_obj.parent_task_id)
                    return SpecialistOutput(task_obj.specialist_task_id, success=False, error=task_obj.last_error)
            except ValueError:
                task_obj.status = SpecialistTaskStatus.BLOCKED
                task_obj.last_error = "specialist task deadline is malformed"
                self.store.save_specialist_task(task_obj)
                return SpecialistOutput(task_obj.specialist_task_id, success=False, error=task_obj.last_error)
        task_obj.status = SpecialistTaskStatus.RUNNING
        task_obj.attempt_count += 1
        task_obj.progress = "executing_within_contract"
        self.store.save_specialist_task(task_obj)
        self._emit(EventType.SPECIALIST_TASK_STARTED, {"task_id": task_obj.specialist_task_id, "specialist_id": task_obj.specialist_id, "contract_hash": contract.scope_hash}, task_obj.parent_task_id)
        executor = executor or self.default_executor
        if executor is None:
            output = SpecialistOutput(task_obj.specialist_task_id, success=False, error="no subordinate executor configured")
        else:
            started = datetime.now(timezone.utc)
            requested_turns, effective_turns, turns_clamped = self._turn_ceiling(contract)
            if turns_clamped:
                # The clamp is recorded on the *task*, not smuggled into the signed contract, so an auditor
                # can see both numbers: what the contract asked for and what the engine allowed.
                task_obj.resource_usage["max_tool_calls_requested"] = requested_turns
                task_obj.resource_usage["max_tool_calls_enforced"] = effective_turns
                task_obj.resource_usage["max_tool_calls_clamped_by"] = f"SpecialistLimits.max_turns_per_specialist={self.limits.max_turns_per_specialist}"
                context = self._constrained_context(context, requested=requested_turns, effective=effective_turns)
            try:
                pool = ThreadPoolExecutor(max_workers=1)
                future = pool.submit(executor, contract, context)
                try:
                    output = self._normalize_output(task_obj.specialist_task_id, future.result(timeout=min(contract.timeout_seconds, self.limits.max_task_duration)))
                finally:
                    pool.shutdown(wait=False, cancel_futures=True)
                task_obj.resource_usage["duration_seconds"] = max(0.0, (datetime.now(timezone.utc) - started).total_seconds())
            except FutureTimeout:
                output = SpecialistOutput(task_obj.specialist_task_id, success=False, error="specialist task timed out")
                task_obj.failure_class = "timeout"
            except PermissionError as exc:
                output = SpecialistOutput(task_obj.specialist_task_id, success=False, error=str(exc))
                task_obj.failure_class = "permission_failure"
            except Exception as exc:
                output = SpecialistOutput(task_obj.specialist_task_id, success=False, error=f"{type(exc).__name__}: {exc}")
                task_obj.failure_class = "reasoning_failure"
        output_errors = self._validate_output(contract, output, max_tool_calls=effective_turns)
        if output_errors:
            output.success = False
            output.error = "; ".join(output_errors)
            task_obj.failure_class = "contract_violation"
        verified = False
        if output.success and self.verifier is not None:
            decision = self.verifier(contract, output)
            verified = decision is True or decision is VerificationStatus.VERIFIED
            output.verification_status = VerificationStatus.VERIFIED if verified else VerificationStatus.INCONCLUSIVE
        elif output.success:
            output.verification_status = VerificationStatus.UNVERIFIED
        output_id = new_id("specialist_result")
        evidence = self._evidence_from_output(output, output_id, task_obj, contract, verified)
        task_obj.result_id = output_id
        task_obj.evidence_ids = [item.evidence_id for item in evidence]
        task_obj.status = SpecialistTaskStatus.VERIFIED if verified else SpecialistTaskStatus.COMPLETED if output.success else SpecialistTaskStatus.FAILED
        task_obj.progress = task_obj.status.value
        task_obj.last_error = output.error
        self.store.save_specialist_task(task_obj)
        result_record = SpecialistResultRecord(output_id, task_obj.specialist_task_id, task_obj.status, verified, output)
        self.store.save_specialist_result(result_record)
        for item in evidence:
            self.store.save_specialist_evidence(item)
        self.registry.record_outcome(task_obj.specialist_id, output.success, float(task_obj.resource_usage.get("duration_seconds", 0.0)), output.error, "timeout" in output.error.lower())
        self._capture_memory(task_obj, output, evidence)
        self._emit(EventType.SPECIALIST_RESULT_COLLECTED, {"result": output.to_dict(), "verified": verified, "evidence_ids": task_obj.evidence_ids}, task_obj.parent_task_id)
        self._emit(EventType.SPECIALIST_TASK_COMPLETED if output.success else EventType.SPECIALIST_TASK_FAILED, {"task_id": task_obj.specialist_task_id, "status": task_obj.status.value, "verified": verified, "failure_class": task_obj.failure_class}, task_obj.parent_task_id)
        return output

    execute = execute_task

    def approve_task(self, specialist_task_id: str, actor: str = "human", scope_hash: str | None = None, reason: str = "") -> SpecialistTask:
        if actor.lower() in {"specialist", "agent", "system", "runtime", "autonomous", "orchestrator"}:
            raise PermissionError("specialist cannot self-approve")
        row = self.store.specialist_task_by_id(specialist_task_id)
        contract_row = self.store.specialist_contract_by_task(specialist_task_id)
        if not row or not contract_row:
            raise KeyError(specialist_task_id)
        task = specialist_task_from_row(row)
        contract = specialist_contract_from_row(contract_row)
        supplied = scope_hash or contract.scope_hash
        if supplied != contract.scope_hash:
            raise PermissionError("specialist approval scope is stale or does not match the contract")
        task.metadata["approval_status"] = "approved"
        task.metadata["approval_actor"] = actor
        task.metadata["approval_reason"] = reason or "explicit human specialist approval"
        task.metadata["approval_scope_hash"] = supplied
        if task.status is SpecialistTaskStatus.WAITING:
            task.status = SpecialistTaskStatus.QUEUED
        task.last_error = ""
        self.store.save_specialist_task(task)
        self._emit(EventType.SPECIALIST_PERMISSION_CHECKED, {"task_id": specialist_task_id, "approval": "approved", "actor": actor, "scope_hash": supplied}, task.parent_task_id)
        return task

    approve_specialist_task = approve_task

    def _evidence_from_output(self, output: SpecialistOutput, result_id: str, task: SpecialistTask, contract: SpecialistTaskContract, verified: bool) -> list[SpecialistEvidence]:
        evidence: list[SpecialistEvidence] = []
        status = VerificationStatus.VERIFIED if verified else VerificationStatus.UNVERIFIED
        if output.claim is not None:
            evidence.append(SpecialistEvidence(new_id("specialist_evidence"), result_id, task.specialist_task_id, task.parent_task_id, EvidenceKind.CLAIM, output.claim, "specialist", output.confidence, SpecialistTrustLevel.VERIFIED if verified else SpecialistTrustLevel.UNTRUSTED, status, {"contract_id": contract.contract_id, "scope_hash": contract.scope_hash}, {"subject": output.provenance.get("subject", "claim")}))
        for observation in output.observations[:8]:
            evidence.append(SpecialistEvidence(new_id("specialist_evidence"), result_id, task.specialist_task_id, task.parent_task_id, EvidenceKind.OBSERVATION, observation, "specialist", output.confidence, SpecialistTrustLevel.OBSERVED, status, {"contract_id": contract.contract_id}, {"subject": "observation"}))
        for claim in output.evidence[:8]:
            evidence.append(SpecialistEvidence(new_id("specialist_evidence"), result_id, task.specialist_task_id, task.parent_task_id, EvidenceKind.EVIDENCE, claim, str(claim.get("source", "specialist")), float(claim.get("confidence", output.confidence)), SpecialistTrustLevel.OBSERVED, status, {"contract_id": contract.contract_id}, {"subject": claim.get("subject", "evidence")}))
        if output.inference is not None:
            evidence.append(SpecialistEvidence(new_id("specialist_evidence"), result_id, task.specialist_task_id, task.parent_task_id, EvidenceKind.INFERENCE, output.inference, "inference", output.confidence, SpecialistTrustLevel.UNTRUSTED, status, {"contract_id": contract.contract_id}, {"subject": "inference"}))
        return evidence

    def delegate(self, parent_task_id: str, assignments: Iterable[tuple[SpecialistTask, SpecialistTaskContract, SpecialistContext | None]], executor: Callable[[SpecialistTaskContract, SpecialistContext], Any], parallel: bool = True) -> tuple[DelegationRun, list[SpecialistOutput], EvidenceFusion]:
        items = list(assignments)
        if not items:
            raise ValueError("at least one specialist assignment is required")
        if len(items) > self.limits.max_specialists_per_delegation:
            raise ResourceWarning("specialist delegation ceiling exceeded")
        # Refused before the run row exists, so an over-deep request leaves no delegation that would be
        # audited as though it had started.
        depth = self._execution_depth()
        if depth + 1 > self.limits.max_delegation_depth:
            raise ResourceWarning(
                f"delegation depth {depth + 1} exceeds the ceiling of {self.limits.max_delegation_depth}: "
                "a delegated specialist may not delegate again in this build"
            )
        run = DelegationRun(new_id("delegation"), parent_task_id, DelegationStatus.RUNNING, [task.specialist_task_id for task, _, _ in items], min(len(items), self.limits.max_concurrent_specialists))
        self.store.save_delegation_run(run)
        self._emit(EventType.DELEGATION_STARTED, {"delegation": run.to_dict()}, parent_task_id)
        outputs: list[SpecialistOutput] = []
        if parallel and len(items) > 1:
            with ThreadPoolExecutor(max_workers=self.limits.max_concurrent_specialists) as pool:
                futures = [pool.submit(self.execute_task, task, context, executor) for task, _, context in items]
                for future in futures:
                    try:
                        outputs.append(future.result(timeout=self.limits.max_parent_duration))
                    except Exception as exc:
                        outputs.append(SpecialistOutput(items[len(outputs)][0].specialist_task_id, success=False, error=f"delegation failure: {type(exc).__name__}: {exc}"))
        else:
            for task, _, context in items:
                outputs.append(self.execute_task(task, context, executor))
        all_evidence: list[SpecialistEvidence] = []
        for output in outputs:
            row = self.store.specialist_task_by_id(output.specialist_task_id)
            if row:
                all_evidence.extend(specialist_evidence_from_row(item) for item in self.store.find_specialist_evidence(specialist_task_id=output.specialist_task_id))
        fusion = self.fusion_engine.fuse(parent_task_id, all_evidence)
        run.completed_specialists = sum(item.success for item in outputs)
        run.failed_specialists = len(outputs) - run.completed_specialists
        run.conflicts = len(fusion.conflicts)
        run.fusion_id = fusion.fusion_id
        run.status = DelegationStatus.COMPLETED if run.failed_specialists == 0 and not fusion.conflicts else DelegationStatus.PARTIAL if run.completed_specialists else DelegationStatus.INCONCLUSIVE
        run.active_specialists = 0
        run.updated_at = utc_now()
        self.store.save_delegation_run(run)
        self._emit(EventType.DELEGATION_COMPLETED, {"delegation": run.to_dict(), "fusion": fusion.to_dict()}, parent_task_id)
        return run, outputs, fusion

    run_delegation = delegate

    def cancel_task(self, specialist_task_id: str, reason: str = "cancelled by sovereign") -> SpecialistTask:
        row = self.store.specialist_task_by_id(specialist_task_id)
        if not row:
            raise KeyError(specialist_task_id)
        task = specialist_task_from_row(row)
        task.status = SpecialistTaskStatus.CANCELLED
        task.last_error = reason
        self.store.save_specialist_task(task)
        self._emit(EventType.SPECIALIST_TASK_CANCELLED, {"task_id": specialist_task_id, "reason": reason}, task.parent_task_id)
        return task

    def recover(self) -> list[SpecialistTask]:
        recovered: list[SpecialistTask] = []
        for row in self.store.find_specialist_tasks(limit=500):
            task = specialist_task_from_row(row)
            if task.status is SpecialistTaskStatus.RUNNING:
                if task.attempt_count <= task.retry_budget:
                    task.status = SpecialistTaskStatus.QUEUED
                    task.progress = "recovered_after_restart"
                    task.last_error = "recovered interrupted specialist task"
                else:
                    task.status = SpecialistTaskStatus.FAILED
                    task.last_error = "retry budget exhausted during restart recovery"
                self.store.save_specialist_task(task)
                recovered.append(task)
                self._emit(EventType.SPECIALIST_TASK_RECOVERED, {"task_id": task.specialist_task_id, "status": task.status.value}, task.parent_task_id)
        return recovered

    def stats(self) -> dict[str, Any]:
        specialists = self.registry.list()
        tasks = self.store.find_specialist_tasks(limit=10000)
        delegations = self.store.find_delegation_runs(limit=10000)
        return {"engine_version": self.ENGINE_VERSION, "specialist_count": len(specialists), "active_specialists": sum(item.lifecycle_state is SpecialistLifecycle.ACTIVE for item in specialists), "task_count": len(tasks), "delegation_count": len(delegations), "completed_tasks": sum(item["status"] in {SpecialistTaskStatus.COMPLETED.value, SpecialistTaskStatus.VERIFIED.value} for item in tasks), "failed_tasks": sum(item["status"] == SpecialistTaskStatus.FAILED.value for item in tasks), "conflict_count": len(self.store.find_evidence_conflicts(limit=10000)), "limits": self.limits.to_dict(), "authority": "sovereign_evo_kernel_governance_verifier"}

    def _capture_memory(self, task: SpecialistTask, output: SpecialistOutput, evidence: list[SpecialistEvidence]) -> None:
        capture = getattr(self.memory, "capture_specialist", None) if self.memory is not None else None
        if capture:
            capture({"specialist_id": task.specialist_id, "specialist_task_id": task.specialist_task_id, "parent_task_id": task.parent_task_id, "success": output.success, "verified": output.verification_status.value == VerificationStatus.VERIFIED.value, "quality_score": output.confidence, "failure": output.error, "resource_usage": task.resource_usage, "evidence_quality": len(evidence), "provenance": {"source": "specialist", "source_id": task.specialist_task_id}})


@dataclass
class SpecialistResultRecord:
    result_id: str
    specialist_task_id: str
    status: SpecialistTaskStatus
    verified: bool
    output: SpecialistOutput
    created_at: str = field(default_factory=utc_now)

    def to_dict(self) -> dict[str, Any]:
        return {"result_id": self.result_id, "specialist_task_id": self.specialist_task_id, "status": self.status.value, "verified": self.verified, "output": self.output.to_dict(), "created_at": self.created_at}


def specialist_from_row(row: dict[str, Any]) -> Specialist:
    payload = row.get("payload", row)
    if isinstance(payload, str):
        payload = json.loads(payload)
    capabilities = []
    for item in payload.get("capabilities", []):
        item = dict(item)
        item["risk"] = SpecialistRisk(item.get("risk", SpecialistRisk.READ_ONLY.value))
        capabilities.append(SpecialistCapability(**{key: value for key, value in item.items() if key in SpecialistCapability.__dataclass_fields__}))
    health_data = payload.get("health", {})
    version_data = payload.get("version_lineage", {})
    provenance_data = payload.get("provenance", {})
    return Specialist(row["specialist_id"], row["name"], payload.get("purpose", row["name"]), SpecialistType(row["specialist_type"]), capabilities, list(payload.get("allowed_tools", [])), list(payload.get("allowed_integrations", [])), payload.get("allowed_filesystem_scope", ""), SpecialistRisk(row["risk_classification"]), dict(payload.get("resource_limits", {})), dict(payload.get("model_metadata", {})), row.get("architecture_version", payload.get("architecture_version", "")), SpecialistVersion(**{key: value for key, value in version_data.items() if key in SpecialistVersion.__dataclass_fields__}), SpecialistLifecycle(row["lifecycle_state"]), SpecialistProvenance(**{key: value for key, value in provenance_data.items() if key in SpecialistProvenance.__dataclass_fields__}), SpecialistHealth(SpecialistHealthState(health_data.get("state", SpecialistHealthState.UNKNOWN.value)), int(health_data.get("success_count", 0)), int(health_data.get("failure_count", 0)), int(health_data.get("timeout_count", 0)), int(health_data.get("conflict_count", 0)), float(health_data.get("average_duration", 0.0)), float(health_data.get("reliability", 0.5)), health_data.get("last_error", ""), health_data.get("last_run")), bool(row.get("enabled", payload.get("enabled", True))), row.get("created_at", payload.get("created_at", utc_now())), row.get("updated_at", payload.get("updated_at", utc_now())))


def specialist_task_from_row(row: dict[str, Any] | None) -> SpecialistTask:
    if not row:
        raise KeyError("specialist task not found")
    payload = row.get("payload", row)
    if isinstance(payload, str):
        payload = json.loads(payload)
    payload["status"] = SpecialistTaskStatus(payload.get("status", row.get("status", SpecialistTaskStatus.CREATED.value)))
    return SpecialistTask(**{key: value for key, value in payload.items() if key in SpecialistTask.__dataclass_fields__})


def specialist_contract_from_row(row: dict[str, Any]) -> SpecialistTaskContract:
    payload = row.get("payload", row)
    if isinstance(payload, str):
        payload = json.loads(payload)
    payload["risk"] = SpecialistRisk(payload.get("risk", SpecialistRisk.READ_ONLY.value))
    return SpecialistTaskContract(**{key: value for key, value in payload.items() if key in SpecialistTaskContract.__dataclass_fields__})


def specialist_evidence_from_row(row: dict[str, Any]) -> SpecialistEvidence:
    payload = row.get("payload", row)
    if isinstance(payload, str):
        payload = json.loads(payload)
    payload["evidence_kind"] = EvidenceKind(payload.get("evidence_kind", EvidenceKind.CLAIM.value))
    payload["trust_level"] = SpecialistTrustLevel(payload.get("trust_level", SpecialistTrustLevel.UNTRUSTED.value))
    payload["verification_status"] = VerificationStatus(payload.get("verification_status", VerificationStatus.UNVERIFIED.value))
    return SpecialistEvidence(**{key: value for key, value in payload.items() if key in SpecialistEvidence.__dataclass_fields__})


# Compatibility names for the single sovereign-owned delegation authority.
SpecialistOrchestrator = SpecialistDelegationEngine
DelegationEngine = SpecialistDelegationEngine
ResultFusionEngine = EvidenceFusionEngine
