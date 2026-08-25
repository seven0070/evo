from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
import hashlib
import json
from pathlib import Path
from typing import Any, Callable, Iterable, Protocol
from urllib.parse import urlparse

from .models import Event, EventType, RiskLevel, new_id, utc_now
from .storage import SQLiteStore
from .version import __version__


EXTERNAL_SCHEMA_VERSION = "external-v1"
_MAX_TEXT = 512
_SECRET_WORDS = {"token", "secret", "password", "api_key", "apikey", "authorization", "private_key", "client_secret", "access_token"}
_INJECTION_MARKERS = ("ignore previous instructions", "system message", "developer message", "execute this command", "approve promotion", "disable governance")


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def _hash(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _now_dt(value: str | None = None) -> datetime:
    if value:
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            pass
    return datetime.now(timezone.utc)


def _redact(value: Any, key: str = "") -> Any:
    lowered = key.lower()
    if lowered in _SECRET_WORDS or lowered.endswith("_token") or lowered.endswith("_secret") or lowered.endswith("_api_key"):
        return "[REDACTED]"
    if isinstance(value, dict):
        return {str(k): _redact(v, str(k)) for k, v in value.items()}
    if isinstance(value, list):
        return [_redact(item, key) for item in value]
    if isinstance(value, str) and len(value) > _MAX_TEXT:
        return value[:_MAX_TEXT] + "…"
    return value


class IntegrationType(str, Enum):
    HTTP_API = "http_api"
    EMAIL = "email"
    FILE_DOCUMENT = "file_document"
    WEBHOOK = "webhook"


class IntegrationLifecycle(str, Enum):
    REGISTERED = "registered"
    ACTIVE = "active"
    DEGRADED = "degraded"
    DISABLED = "disabled"
    DEPRECATED = "deprecated"
    REMOVED = "removed"


class IntegrationHealthState(str, Enum):
    UNKNOWN = "unknown"
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNAVAILABLE = "unavailable"
    FAILED = "failed"
    DISABLED = "disabled"


class ExternalOperationRisk(str, Enum):
    READ_ONLY = "read_only"
    LOW_RISK_WRITE = "low_risk_write"
    HIGH_RISK_WRITE = "high_risk_write"
    DESTRUCTIVE = "destructive"
    COMMUNICATION = "communication"

    @property
    def requires_approval(self) -> bool:
        return self in {self.HIGH_RISK_WRITE, self.DESTRUCTIVE, self.COMMUNICATION}

    @property
    def mutating(self) -> bool:
        return self is not self.READ_ONLY


class ExternalOperationStatus(str, Enum):
    REQUESTED = "requested"
    WAITING_APPROVAL = "waiting_approval"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    TIMEOUT = "timeout"
    UNKNOWN = "unknown"
    DUPLICATE = "duplicate"
    BLOCKED = "blocked"
    CANCELLED = "cancelled"


class ExternalFailureClass(str, Enum):
    NONE = "none"
    CONNECTOR = "connector_failure"
    EXTERNAL_SERVICE = "external_service_failure"
    NETWORK_POLICY = "network_policy_failure"
    PERMISSION = "permission_failure"
    AUTHENTICATION = "authentication_failure"
    RATE_LIMIT = "rate_limit"
    TIMEOUT = "timeout"
    SCHEMA_MISMATCH = "schema_mismatch"
    DATA_INCONSISTENCY = "external_data_inconsistency"
    UNKNOWN = "unknown_external_outcome"


class ExternalTrustLevel(str, Enum):
    UNTRUSTED = "untrusted"
    OBSERVED = "observed"
    VERIFIED = "verified"
    UNKNOWN = "unknown"


class ExternalFreshness(str, Enum):
    FRESH = "fresh"
    AGING = "aging"
    STALE = "stale"
    EXPIRED = "expired"
    UNKNOWN = "unknown"


class ExternalChangeKind(str, Enum):
    ADDED = "added"
    REMOVED = "removed"
    CHANGED = "changed"
    UNCHANGED = "unchanged"
    UNKNOWN = "unknown"


class ExternalDataClassification(str, Enum):
    PUBLIC = "public"
    INTERNAL = "internal"
    CONFIDENTIAL = "confidential"
    RESTRICTED = "restricted"


@dataclass
class IntegrationCredentialMetadata:
    reference: str = ""
    credential_names: list[str] = field(default_factory=list)
    storage: str = "external_secret_store"
    present: bool = False
    last_validated: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return _redact(asdict(self))


@dataclass
class IntegrationProvenance:
    source: str = "user_registered"
    source_id: str = ""
    source_version: str = ""
    actor: str = "system"
    created_at: str = field(default_factory=utc_now)
    lineage: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class IntegrationEnvironment:
    workspace_type: str = "local"
    environment_requirements: dict[str, Any] = field(default_factory=dict)
    allowed_environments: list[str] = field(default_factory=list)
    network_required: bool = True
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class IntegrationHealth:
    state: IntegrationHealthState = IntegrationHealthState.UNKNOWN
    success_count: int = 0
    failure_count: int = 0
    timeout_count: int = 0
    rate_limit_count: int = 0
    last_success: str | None = None
    last_failure: str | None = None
    last_error: str = ""
    average_latency: float = 0.0
    reliability: float = 0.5

    def record(self, success: bool, latency: float = 0.0, failure: ExternalFailureClass = ExternalFailureClass.NONE) -> None:
        if success:
            self.success_count += 1
            self.last_success = utc_now()
            self.state = IntegrationHealthState.HEALTHY
        else:
            self.failure_count += 1
            self.last_failure = utc_now()
            self.last_error = failure.value
            if failure is ExternalFailureClass.TIMEOUT:
                self.timeout_count += 1
            if failure is ExternalFailureClass.RATE_LIMIT:
                self.rate_limit_count += 1
            self.state = IntegrationHealthState.DEGRADED
        total = self.success_count + self.failure_count
        self.reliability = self.success_count / total if total else 0.5
        if latency > 0:
            self.average_latency = ((self.average_latency * max(0, total - 1)) + latency) / total

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["state"] = self.state.value
        return data


@dataclass
class IntegrationVersion:
    version: str
    schema_version: str = EXTERNAL_SCHEMA_VERSION
    agent_version: str = __version__
    architecture_version: str = ""
    created_at: str = field(default_factory=utc_now)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class IntegrationCapability:
    capability_id: str
    name: str
    description: str
    supported_operations: list[str] = field(default_factory=list)
    input_schema: dict[str, Any] = field(default_factory=dict)
    output_schema: dict[str, Any] = field(default_factory=dict)
    permissions: list[str] = field(default_factory=list)
    risk: ExternalOperationRisk = ExternalOperationRisk.READ_ONLY
    data_classification: ExternalDataClassification = ExternalDataClassification.INTERNAL
    metadata: dict[str, Any] = field(default_factory=dict)

    def validate(self) -> list[str]:
        errors: list[str] = []
        if not self.capability_id or not self.name:
            errors.append("capability identity is required")
        if not self.supported_operations:
            errors.append("at least one supported operation is required")
        if not isinstance(self.input_schema, dict) or not isinstance(self.output_schema, dict):
            errors.append("input and output schemas must be objects")
        return errors

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["risk"] = self.risk.value
        data["data_classification"] = self.data_classification.value
        return _redact(data)


@dataclass
class IntegrationPermission:
    permission_id: str
    name: str
    description: str
    scope: str
    required_for: list[str] = field(default_factory=list)
    approval_required: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return _redact(asdict(self))


@dataclass
class Integration:
    integration_id: str
    name: str
    provider: str
    integration_type: IntegrationType
    version: str
    capabilities: list[IntegrationCapability] = field(default_factory=list)
    supported_operations: list[str] = field(default_factory=list)
    required_permissions: list[IntegrationPermission] = field(default_factory=list)
    risk_classification: ExternalOperationRisk = ExternalOperationRisk.READ_ONLY
    environment_compatibility: IntegrationEnvironment = field(default_factory=IntegrationEnvironment)
    health: IntegrationHealth = field(default_factory=IntegrationHealth)
    credential_metadata: IntegrationCredentialMetadata = field(default_factory=IntegrationCredentialMetadata)
    provenance: IntegrationProvenance = field(default_factory=IntegrationProvenance)
    enabled: bool = False
    architecture_version: str = ""
    lifecycle_state: IntegrationLifecycle = IntegrationLifecycle.REGISTERED
    endpoint: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)

    def validate(self) -> list[str]:
        errors: list[str] = []
        if not self.integration_id or not self.name or not self.provider or not self.version:
            errors.append("integration id, name, provider, and version are required")
        if not isinstance(self.capabilities, list) or any(item.validate() for item in self.capabilities):
            errors.append("capabilities are malformed")
        if self.integration_type in {IntegrationType.HTTP_API, IntegrationType.WEBHOOK} and self.endpoint:
            parsed = urlparse(self.endpoint)
            if parsed.scheme not in {"http", "https"} or not parsed.hostname:
                errors.append("endpoint must be an HTTP(S) URL")
            if parsed.username or parsed.password or any(word in parsed.query.lower() for word in ("token", "secret", "password", "api_key", "apikey")):
                errors.append("endpoint must not contain credentials or credential-like query parameters")
        if not self.enabled and self.lifecycle_state is IntegrationLifecycle.ACTIVE:
            errors.append("disabled integration cannot be active")
        return errors

    def to_dict(self) -> dict[str, Any]:
        return _redact({
            "integration_id": self.integration_id,
            "name": self.name,
            "provider": self.provider,
            "integration_type": self.integration_type.value,
            "version": self.version,
            "capabilities": [item.to_dict() for item in self.capabilities],
            "supported_operations": list(self.supported_operations),
            "required_permissions": [item.to_dict() for item in self.required_permissions],
            "risk_classification": self.risk_classification.value,
            "environment_compatibility": self.environment_compatibility.to_dict(),
            "health": self.health.to_dict(),
            "credential_metadata": self.credential_metadata.to_dict(),
            "provenance": self.provenance.to_dict(),
            "enabled": self.enabled,
            "architecture_version": self.architecture_version,
            "lifecycle_state": self.lifecycle_state.value,
            "endpoint": self.endpoint,
            "metadata": self.metadata,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        })


@dataclass
class ExternalAccessPolicy:
    policy_id: str = field(default_factory=lambda: new_id("external_policy"))
    name: str = "default-deny"
    allowed_domains: list[str] = field(default_factory=list)
    allowed_endpoints: list[str] = field(default_factory=list)
    allowed_methods: list[str] = field(default_factory=lambda: ["GET"])
    allowed_operations: list[str] = field(default_factory=lambda: ["read", "inspect", "health_check"])
    timeout_seconds: float = 15.0
    max_request_bytes: int = 64_000
    max_response_bytes: int = 256_000
    rate_limit_per_minute: int = 30
    max_retries: int = 1
    allow_redirects: bool = False
    authentication_required: bool = True
    approval_required: bool = True
    allowed_data_classifications: list[ExternalDataClassification] = field(default_factory=lambda: [ExternalDataClassification.PUBLIC, ExternalDataClassification.INTERNAL])
    allowed_environments: list[str] = field(default_factory=lambda: ["local"])
    enabled: bool = True
    version: str = "policy-v1"
    provenance: IntegrationProvenance = field(default_factory=IntegrationProvenance)

    def validate(self) -> list[str]:
        errors: list[str] = []
        if self.timeout_seconds <= 0 or self.timeout_seconds > 300:
            errors.append("timeout must be between 0 and 300 seconds")
        if self.max_request_bytes <= 0 or self.max_response_bytes <= 0:
            errors.append("request and response size limits must be positive")
        if self.rate_limit_per_minute <= 0 or self.max_retries < 0:
            errors.append("rate and retry limits are invalid")
        if not self.allowed_methods or not self.allowed_operations:
            errors.append("allowed methods and operations must be explicit")
        return errors

    def allows(self, endpoint: str, method: str, operation: str, environment: str = "local") -> tuple[bool, str]:
        if not self.enabled:
            return False, "external access policy is disabled"
        parsed = urlparse(endpoint)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            return False, "endpoint must be an HTTP(S) URL"
        normalized_host = parsed.hostname.lower()
        domains = {item.lower().lstrip(".") for item in self.allowed_domains}
        endpoints = set(self.allowed_endpoints)
        if endpoint not in endpoints and not any(normalized_host == domain or normalized_host.endswith("." + domain) for domain in domains):
            return False, "endpoint is not allowlisted"
        if method.upper() not in {item.upper() for item in self.allowed_methods}:
            return False, "HTTP method is not allowlisted"
        if operation not in self.allowed_operations:
            return False, "operation is not allowlisted"
        if self.allowed_environments and environment not in self.allowed_environments:
            return False, "environment is not allowed"
        return True, "allowed"

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["allowed_data_classifications"] = [item.value for item in self.allowed_data_classifications]
        data["provenance"] = self.provenance.to_dict()
        return _redact(data)


@dataclass
class IntegrationOperation:
    operation_id: str
    integration_id: str
    operation: str
    target: str
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]
    permissions_required: list[str]
    risk_level: ExternalOperationRisk
    timeout_seconds: float
    resource_limits: dict[str, Any]
    approval_required: bool
    idempotency_key: str
    request_fingerprint: str
    status: ExternalOperationStatus = ExternalOperationStatus.REQUESTED
    requested_by: str = "cognitive"
    created_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)
    metadata: dict[str, Any] = field(default_factory=dict)

    def validate(self, payload: Any = None) -> list[str]:
        errors: list[str] = []
        if not self.operation_id or not self.integration_id or not self.operation:
            errors.append("operation identity is required")
        if not isinstance(self.input_schema, dict) or not isinstance(self.output_schema, dict):
            errors.append("input and output schemas must be objects")
        if self.timeout_seconds <= 0:
            errors.append("timeout must be positive")
        if not self.idempotency_key or not self.request_fingerprint:
            errors.append("idempotency and request fingerprint are required")
        if payload is not None and len(_canonical(payload).encode("utf-8")) > int(self.resource_limits.get("max_request_bytes", 64_000)):
            errors.append("request exceeds bounded size")
        return errors

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["risk_level"] = self.risk_level.value
        data["status"] = self.status.value
        return _redact(data)


@dataclass
class ExternalOperationResult:
    operation_id: str
    status: ExternalOperationStatus
    failure_class: ExternalFailureClass = ExternalFailureClass.NONE
    response_metadata: dict[str, Any] = field(default_factory=dict)
    output_hash: str = ""
    output_schema_valid: bool = False
    verified: bool = False
    latency_seconds: float = 0.0
    duplicate_of: str | None = None
    error: str = ""
    created_at: str = field(default_factory=utc_now)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["status"] = self.status.value
        data["failure_class"] = self.failure_class.value
        return _redact(data)


@dataclass
class CommunicationRecord:
    communication_id: str
    operation_id: str
    integration_id: str
    channel: str
    target: str
    status: ExternalOperationStatus
    approval_id: str | None = None
    content_hash: str = ""
    created_at: str = field(default_factory=utc_now)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["status"] = self.status.value
        return _redact(data)


@dataclass
class ExternalObservationProvenance:
    source: str
    integration_id: str
    observation_id: str = ""
    captured_at: str = field(default_factory=utc_now)
    connector_version: str = ""
    architecture_version: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return _redact(asdict(self))


@dataclass
class ExternalObservation:
    observation_id: str
    source: str
    integration_id: str
    timestamp: str
    freshness: ExternalFreshness
    trust_level: ExternalTrustLevel
    resource_identity: str
    version: str = ""
    etag: str = ""
    content_hash: str = ""
    content_excerpt: str = ""
    provenance: ExternalObservationProvenance | dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)
    expires_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["freshness"] = self.freshness.value
        data["trust_level"] = self.trust_level.value
        return _redact(data)


class ExternalObservationEngine:
    def classify_freshness(self, observation: ExternalObservation, ttl_seconds: int = 300, aging_seconds: int | None = None, now: datetime | None = None) -> ExternalFreshness:
        current = now or datetime.now(timezone.utc)
        observed = _now_dt(observation.timestamp)
        age = max(0.0, (current - observed).total_seconds())
        expiry = _now_dt(observation.expires_at) if observation.expires_at else None
        if expiry and current >= expiry:
            return ExternalFreshness.EXPIRED
        if age > ttl_seconds:
            return ExternalFreshness.STALE
        if age > (aging_seconds if aging_seconds is not None else ttl_seconds * 0.6):
            return ExternalFreshness.AGING
        return ExternalFreshness.FRESH

    def validate_current(self, observation: ExternalObservation, ttl_seconds: int = 300, now: datetime | None = None) -> bool:
        freshness = self.classify_freshness(observation, ttl_seconds, now=now)
        observation.freshness = freshness
        return freshness is ExternalFreshness.FRESH


@dataclass
class ExternalResourceState:
    resource_id: str
    integration_id: str
    resource_identity: str
    version: str = ""
    etag: str = ""
    content_hash: str = ""
    exists: bool = True
    observed_at: str = field(default_factory=utc_now)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return _redact(asdict(self))


@dataclass
class ExternalChange:
    change_id: str
    integration_id: str
    resource_identity: str
    kind: ExternalChangeKind
    before_observation_id: str | None
    after_observation_id: str | None
    reason: str
    created_at: str = field(default_factory=utc_now)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["kind"] = self.kind.value
        return _redact(data)


class ConnectorError(RuntimeError):
    def __init__(self, message: str, failure_class: ExternalFailureClass = ExternalFailureClass.CONNECTOR, unknown_outcome: bool = False):
        super().__init__(message)
        self.failure_class = failure_class
        self.unknown_outcome = unknown_outcome


class Connector(Protocol):
    integration_id: str

    def validate_availability(self) -> dict[str, Any]: ...
    def execute(self, operation: IntegrationOperation, payload: dict[str, Any]) -> Any: ...


class BaseConnector:
    def __init__(self, integration_id: str):
        self.integration_id = integration_id

    def validate_availability(self) -> dict[str, Any]:
        return {"available": True, "connector": self.__class__.__name__}

    def execute(self, operation: IntegrationOperation, payload: dict[str, Any]) -> Any:
        raise ConnectorError("connector operation is not implemented", ExternalFailureClass.CONNECTOR)


class InMemoryConnector(BaseConnector):
    """Deterministic connector for bounded tests and provider-neutral local adapters."""

    def __init__(self, integration_id: str):
        super().__init__(integration_id)
        self.resources: dict[str, dict[str, Any]] = {}
        self.messages: list[dict[str, Any]] = []

    def seed(self, resource_identity: str, value: dict[str, Any]) -> None:
        self.resources[resource_identity] = dict(value)

    def execute(self, operation: IntegrationOperation, payload: dict[str, Any]) -> Any:
        target = operation.target or str(payload.get("resource_identity", ""))
        if operation.operation in {"read", "inspect", "receive", "health_check"}:
            return dict(self.resources.get(target, {"resource_identity": target, "exists": False}))
        if operation.operation in {"create", "update"}:
            self.resources[target] = dict(payload.get("value", payload))
            return {"resource_identity": target, "exists": True, "value": self.resources[target]}
        if operation.operation == "delete":
            self.resources.pop(target, None)
            return {"resource_identity": target, "exists": False}
        if operation.operation == "send":
            message = {"target": target, "content": str(payload.get("content", ""))}
            self.messages.append(message)
            return {"sent": True, "target": target, "content_hash": _hash(message["content"])}
        raise ConnectorError(f"unsupported operation: {operation.operation}", ExternalFailureClass.CONNECTOR)


class HTTPAPIConnector(BaseConnector):
    def __init__(self, integration_id: str, requester: Callable[[str, str, dict[str, Any], float], Any] | None = None):
        super().__init__(integration_id)
        self.requester = requester

    def execute(self, operation: IntegrationOperation, payload: dict[str, Any]) -> Any:
        if not self.requester:
            raise ConnectorError("HTTP requester is not configured", ExternalFailureClass.EXTERNAL_SERVICE)
        try:
            return self.requester(operation.target, operation.operation, payload, operation.timeout_seconds)
        except TimeoutError as exc:
            raise ConnectorError(str(exc), ExternalFailureClass.TIMEOUT, operation.risk_level.mutating) from exc


class EmailConnector(InMemoryConnector):
    pass


class FileDocumentConnector(InMemoryConnector):
    pass


class WebhookConnector(HTTPAPIConnector):
    pass


class ExternalContentSafety:
    @staticmethod
    def inspect(content: Any) -> dict[str, Any]:
        text = content if isinstance(content, str) else _canonical(content)
        lowered = text.lower()
        markers = [marker for marker in _INJECTION_MARKERS if marker in lowered]
        return {"trust_level": ExternalTrustLevel.UNTRUSTED.value, "executable": False, "injection_like": bool(markers), "markers": markers, "content_hash": _hash(text)}


class ExternalChangeDetector:
    def compare(self, before: ExternalObservation | None, after: ExternalObservation | None) -> ExternalChange:
        integration_id = (after or before).integration_id if (after or before) else ""
        resource_identity = (after or before).resource_identity if (after or before) else ""
        if before is None and after is not None:
            kind, reason = ExternalChangeKind.ADDED, "resource observed for the first time"
        elif before is not None and after is None:
            kind, reason = ExternalChangeKind.REMOVED, "resource is no longer observed"
        elif before is None or after is None:
            kind, reason = ExternalChangeKind.UNKNOWN, "insufficient observations"
        elif bool(before.metadata.get("exists", True)) and not bool(after.metadata.get("exists", True)):
            kind, reason = ExternalChangeKind.REMOVED, "resource is explicitly reported as deleted"
        elif not bool(before.metadata.get("exists", True)) and bool(after.metadata.get("exists", True)):
            kind, reason = ExternalChangeKind.ADDED, "resource is explicitly reported as recreated"
        elif before.content_hash == after.content_hash and before.version == after.version and before.etag == after.etag:
            kind, reason = ExternalChangeKind.UNCHANGED, "version, etag, and content hash unchanged"
        else:
            kind, reason = ExternalChangeKind.CHANGED, "version, etag, or content hash changed"
        return ExternalChange(new_id("external_change"), integration_id, resource_identity, kind, before.observation_id if before else None, after.observation_id if after else None, reason)


class ExternalIntegrationManager:
    MANAGER_VERSION = "integration-manager-v1"

    def __init__(self, workspace: Path, store: SQLiteStore | None = None, policy: ExternalAccessPolicy | None = None, capability_intelligence: Any | None = None, memory: Any | None = None, architecture_version: str = "", flexibility: Any | None = None):
        self.workspace = Path(workspace).expanduser().resolve()
        self.workspace.mkdir(parents=True, exist_ok=True)
        self.store = store or SQLiteStore(self.workspace / ".evo" / "agent.sqlite3")
        self.policy = policy or ExternalAccessPolicy()
        self.capability_intelligence = capability_intelligence
        self.memory = memory
        self.architecture_version = architecture_version
        self.flexibility = flexibility
        self.connectors: dict[str, Connector] = {}
        self.changes = ExternalChangeDetector()
        self.observation_engine = ExternalObservationEngine()
        persisted_policies = self.store.find_external_access_policies(limit=1)
        if persisted_policies and policy is None:
            self.policy = external_policy_from_row(persisted_policies[0])
        else:
            self.store.save_external_access_policy(self.policy)

    def register_integration(self, integration: Integration, connector: Connector | None = None, enable: bool = False) -> Integration:
        integration.enabled = bool(enable)
        integration.lifecycle_state = IntegrationLifecycle.ACTIVE if enable else IntegrationLifecycle.REGISTERED
        integration.updated_at = utc_now()
        errors = integration.validate()
        if errors:
            raise ValueError("; ".join(errors))
        self.store.save_integration(integration)
        if connector is not None:
            if getattr(connector, "integration_id", "") != integration.integration_id:
                raise ValueError("connector integration ID mismatch")
            self.connectors[integration.integration_id] = connector
        self._emit(EventType.EXTERNAL_INTEGRATION_REGISTERED, {"integration": integration.to_dict()})
        if self.capability_intelligence:
            self.register_capabilities(integration)
        return integration

    def enable_integration(self, integration_id: str, enabled: bool = True) -> Integration:
        integration = self.get_integration(integration_id)
        if not integration:
            raise KeyError(integration_id)
        integration.enabled = enabled
        integration.lifecycle_state = IntegrationLifecycle.ACTIVE if enabled else IntegrationLifecycle.DISABLED
        integration.updated_at = utc_now()
        self.store.save_integration(integration)
        return integration

    def get_integration(self, integration_id: str) -> Integration | None:
        row = self.store.integration_by_id(integration_id)
        return integration_from_row(row) if row else None

    def list_integrations(self, limit: int = 100) -> list[Integration]:
        return [integration_from_row(row) for row in self.store.find_integrations(limit=limit)]

    def register_policy(self, policy: ExternalAccessPolicy) -> ExternalAccessPolicy:
        errors = policy.validate()
        if errors:
            raise ValueError("; ".join(errors))
        self.policy = policy
        self.store.save_external_access_policy(policy)
        self._emit(EventType.EXTERNAL_POLICY_UPDATED, {"policy": policy.to_dict()})
        return policy

    def list_policies(self, limit: int = 100) -> list[ExternalAccessPolicy]:
        return [external_policy_from_row(row) for row in self.store.find_external_access_policies(limit=limit)]

    def health(self, integration_id: str) -> dict[str, Any]:
        integration = self.get_integration(integration_id)
        if not integration:
            raise KeyError(integration_id)
        connector = self.connectors.get(integration_id)
        result = connector.validate_availability() if connector else {"available": False, "reason": "connector not bound"}
        if result.get("available"):
            integration.health.state = IntegrationHealthState.HEALTHY
        else:
            integration.health.state = IntegrationHealthState.UNAVAILABLE
        self.store.save_integration(integration)
        self.store.save_connector_health(integration_id, integration.health)
        self._emit(EventType.EXTERNAL_CONNECTOR_HEALTH, {"integration_id": integration_id, "health": integration.health.to_dict()})
        return {"integration_id": integration_id, "health": integration.health.to_dict(), "availability": _redact(result)}

    def bind_flexibility(self, flexibility: Any) -> None:
        self.flexibility = flexibility

    def request_operation(self, integration_id: str, operation: str, target: str = "", payload: dict[str, Any] | None = None, risk_level: ExternalOperationRisk | str | None = None, permissions_required: Iterable[str] = (), input_schema: dict[str, Any] | None = None, output_schema: dict[str, Any] | None = None, timeout_seconds: float | None = None, resource_limits: dict[str, Any] | None = None, idempotency_key: str | None = None, requested_by: str = "cognitive", approval_required: bool | None = None) -> IntegrationOperation:
        integration = self.get_integration(integration_id)
        if not integration:
            raise KeyError(integration_id)
        operation = str(operation)
        payload = dict(payload or {})
        risk = ExternalOperationRisk(risk_level or self._risk_for(integration, operation))
        if operation not in integration.supported_operations:
            raise PermissionError("operation is not registered for integration")
        recent = [item for item in self.store.find_integration_operations(integration_id, limit=max(1, self.policy.rate_limit_per_minute + 1)) if (datetime.now(timezone.utc) - _now_dt(item.get("requested_at"))).total_seconds() < 60]
        if len(recent) >= self.policy.rate_limit_per_minute:
            self._emit(EventType.EXTERNAL_OPERATION_BLOCKED, {"integration_id": integration_id, "operation": operation, "reason": "external policy rate limit exceeded"})
            raise PermissionError("external policy rate limit exceeded")
        endpoint = target or integration.endpoint or "https://local.invalid"
        if integration.integration_type in {IntegrationType.HTTP_API, IntegrationType.WEBHOOK}:
            allowed, reason = self.policy.allows(endpoint, str(payload.get("method", "GET")), operation)
            if not allowed:
                self._emit(EventType.EXTERNAL_OPERATION_BLOCKED, {"integration_id": integration_id, "operation": operation, "reason": reason})
                raise PermissionError(reason)
        if integration.environment_compatibility.allowed_environments and "local" not in integration.environment_compatibility.allowed_environments:
            raise PermissionError("integration is incompatible with the current environment")
        if self.policy.authentication_required and operation not in {"health_check", "inspect"} and not integration.credential_metadata.present:
            raise PermissionError("credential metadata is required by external access policy")
        capability = next((item for item in integration.capabilities if operation in item.supported_operations), None)
        if capability and capability.data_classification not in self.policy.allowed_data_classifications:
            raise PermissionError("operation data classification is not allowed by external policy")
        if capability and not self._valid_input(capability.input_schema, payload):
            raise ValueError("request does not satisfy the registered input schema")
        permissions = list(permissions_required) or (list(capability.permissions) if capability else [])
        permission_approval = any(operation in item.required_for and item.approval_required for item in integration.required_permissions)
        fingerprint = _hash({"integration_id": integration_id, "operation": operation, "target": target, "payload": _redact(payload), "risk": risk.value, "permissions": permissions})
        operation_id = new_id("external_op")
        item = IntegrationOperation(operation_id, integration_id, operation, target, input_schema or (capability.input_schema if capability else {"type": "object"}), output_schema or (capability.output_schema if capability else {"type": "object"}), permissions, risk, timeout_seconds or self.policy.timeout_seconds, dict(resource_limits or {"max_request_bytes": self.policy.max_request_bytes, "max_response_bytes": self.policy.max_response_bytes}), bool((risk.requires_approval or permission_approval) if approval_required is None else approval_required), idempotency_key or fingerprint, fingerprint, requested_by=requested_by)
        errors = item.validate(payload)
        if errors:
            raise ValueError("; ".join(errors))
        self.store.save_integration_operation(item, _redact(payload))
        self._emit(EventType.EXTERNAL_OPERATION_REQUESTED, {"operation": item.to_dict()})
        return item

    def execute_operation(self, operation_id: str | IntegrationOperation, payload: dict[str, Any] | None = None, approval_callback: Callable[[IntegrationOperation], bool] | None = None, actor: str = "kernel") -> ExternalOperationResult:
        operation_id = operation_id.operation_id if isinstance(operation_id, IntegrationOperation) else operation_id
        row = self.store.integration_operation_by_id(operation_id)
        if not row:
            raise KeyError(operation_id)
        operation, stored_payload = integration_operation_from_row(row)
        payload = dict(payload or stored_payload or {})
        existing = self.store.external_operation_by_fingerprint(operation.request_fingerprint)
        if existing and existing.get("operation_id") != operation_id:
            result = external_operation_result_from_row(existing)
            if result.status in {ExternalOperationStatus.SUCCEEDED, ExternalOperationStatus.UNKNOWN, ExternalOperationStatus.RUNNING}:
                duplicate = ExternalOperationResult(operation_id, ExternalOperationStatus.DUPLICATE, duplicate_of=result.operation_id, output_hash=result.output_hash, error="duplicate operation prevented")
                self.store.save_external_operation_result(duplicate)
                self._emit(EventType.EXTERNAL_DUPLICATE_PREVENTED, duplicate.to_dict())
                return duplicate
        integration = self.get_integration(operation.integration_id)
        if not integration or not integration.enabled or integration.lifecycle_state in {IntegrationLifecycle.DISABLED, IntegrationLifecycle.REMOVED}:
            return self._blocked_result(operation, ExternalFailureClass.PERMISSION, "integration is not enabled")
        connector = self.connectors.get(operation.integration_id)
        if connector is None:
            return self._blocked_result(operation, ExternalFailureClass.CONNECTOR, "connector is not bound")
        if operation.approval_required:
            approval = operation.metadata.get("approval", {})
            if approval.get("status") != "approved":
                if actor in {"runtime", "system", "agent", "cognitive"}:
                    return self._waiting_approval(operation, "external operation requires explicit human approval")
                if approval_callback is None or not approval_callback(operation):
                    return self._waiting_approval(operation, "external operation requires explicit human approval")
                operation.metadata["approval"] = {"status": "approved", "actor": actor, "scope_hash": self.approval_scope(operation)}
        operation.status = ExternalOperationStatus.RUNNING
        operation.updated_at = utc_now()
        self.store.save_integration_operation(operation, _redact(payload))
        self._emit(EventType.EXTERNAL_OPERATION_STARTED, {"operation_id": operation_id, "integration_id": operation.integration_id, "operation": operation.operation})
        started = _now_dt()
        try:
            output = connector.execute(operation, payload)
            encoded = _canonical(output).encode("utf-8")
            if len(encoded) > int(operation.resource_limits.get("max_response_bytes", self.policy.max_response_bytes)):
                raise ConnectorError("response exceeds bounded size", ExternalFailureClass.SCHEMA_MISMATCH)
            output_hash = hashlib.sha256(encoded).hexdigest()
            schema_valid = self._valid_output(operation.output_schema, output)
            result = ExternalOperationResult(operation_id, ExternalOperationStatus.SUCCEEDED if schema_valid else ExternalOperationStatus.FAILED, ExternalFailureClass.NONE if schema_valid else ExternalFailureClass.SCHEMA_MISMATCH, response_metadata={"integration_id": operation.integration_id, "operation": operation.operation}, output_hash=output_hash, output_schema_valid=schema_valid, verified=False, latency_seconds=max(0.0, (datetime.now(timezone.utc) - started).total_seconds()), error="" if schema_valid else "connector response failed the declared output schema")
            if not schema_valid:
                result.metadata["flexibility"] = self.flexibility_recommendation(operation, result)
            integration.health.record(schema_valid, result.latency_seconds, result.failure_class)
            operation.status = result.status
            self.store.save_integration_operation(operation, _redact(payload))
            self.store.save_external_operation_result(result)
            self._record_communication(operation, payload, result)
            self.record_memory_evidence(result, operation)
            self._emit(EventType.EXTERNAL_OPERATION_COMPLETED if schema_valid else EventType.EXTERNAL_OPERATION_FAILED, result.to_dict())
            return result
        except ConnectorError as exc:
            failure = exc.failure_class
            status = ExternalOperationStatus.UNKNOWN if exc.unknown_outcome or (failure is ExternalFailureClass.TIMEOUT and operation.risk_level.mutating) else ExternalOperationStatus.TIMEOUT if failure is ExternalFailureClass.TIMEOUT else ExternalOperationStatus.FAILED
            result = ExternalOperationResult(operation_id, status, failure, error=str(exc), latency_seconds=max(0.0, (datetime.now(timezone.utc) - started).total_seconds()))
            result.metadata["flexibility"] = self.flexibility_recommendation(operation, result)
            integration.health.record(False, result.latency_seconds, failure)
            operation.status = status
            self.store.save_integration_operation(operation, _redact(payload))
            self.store.save_external_operation_result(result)
            self._record_communication(operation, payload, result)
            self.record_memory_evidence(result, operation)
            self._emit(EventType.EXTERNAL_OPERATION_FAILED, result.to_dict())
            return result
        except Exception as exc:
            result = ExternalOperationResult(operation_id, ExternalOperationStatus.UNKNOWN if operation.risk_level.mutating else ExternalOperationStatus.FAILED, ExternalFailureClass.EXTERNAL_SERVICE, error=f"{type(exc).__name__}: {exc}")
            result.metadata["flexibility"] = self.flexibility_recommendation(operation, result)
            integration.health.record(False, result.latency_seconds, result.failure_class)
            operation.status = result.status
            self.store.save_integration_operation(operation, _redact(payload))
            self.store.save_external_operation_result(result)
            self.record_memory_evidence(result, operation)
            self._emit(EventType.EXTERNAL_OPERATION_FAILED, result.to_dict())
            return result

    def approve_operation(self, operation_id: str, actor: str, scope_hash: str, reason: str = "") -> IntegrationOperation:
        if actor in {"runtime", "system", "agent", "cognitive", "autonomous"}:
            raise PermissionError("external operation self-approval is forbidden")
        row = self.store.integration_operation_by_id(operation_id)
        if not row:
            raise KeyError(operation_id)
        operation, payload = integration_operation_from_row(row)
        if scope_hash != self.approval_scope(operation):
            raise PermissionError("stale external approval scope")
        operation.metadata["approval"] = {"status": "approved", "actor": actor, "scope_hash": scope_hash, "reason": reason, "approved_at": utc_now()}
        operation.status = ExternalOperationStatus.REQUESTED
        operation.updated_at = utc_now()
        self.store.save_integration_operation(operation, _redact(payload))
        self._emit(EventType.EXTERNAL_APPROVAL_RECEIVED, {"operation_id": operation_id, "actor": actor, "scope_hash": scope_hash})
        return operation

    def approval_scope(self, operation: IntegrationOperation) -> str:
        return _hash({"integration_id": operation.integration_id, "operation": operation.operation, "target": operation.target, "fingerprint": operation.request_fingerprint, "permissions": operation.permissions_required})

    def observe_external(self, integration_id: str, resource_identity: str, content: Any = None, version: str = "", etag: str = "", source: str = "connector", retain_excerpt: bool = False, expires_at: str | None = None, metadata: dict[str, Any] | None = None, exists: bool = True) -> ExternalObservation:
        inspection = ExternalContentSafety.inspect(content)
        text = content if isinstance(content, str) else _canonical(content)
        observation_id = new_id("external_obs")
        observation = ExternalObservation(observation_id, source, integration_id, utc_now(), ExternalFreshness.FRESH, ExternalTrustLevel.UNTRUSTED, resource_identity, version, etag, inspection["content_hash"], text[:_MAX_TEXT] if retain_excerpt else "", ExternalObservationProvenance(source, integration_id, observation_id, architecture_version=self.architecture_version, metadata={"captured_at": utc_now()}), {**inspection, "exists": bool(exists), **dict(metadata or {})}, expires_at)
        self.store.save_external_observation(observation)
        resource = ExternalResourceState(new_id("external_resource"), integration_id, resource_identity, version, etag, observation.content_hash, bool(exists), observation.timestamp, {"observation_id": observation.observation_id})
        self.store.save_external_resource(resource)
        self._emit(EventType.EXTERNAL_OBSERVATION_RECORDED, {"observation": observation.to_dict()})
        if inspection["injection_like"]:
            self._emit(EventType.EXTERNAL_CONTENT_UNTRUSTED, {"observation_id": observation.observation_id, "integration_id": integration_id, "reason": "injection-like external content retained as data only"})
        return observation

    def validate_observation_current(self, observation_id: str, ttl_seconds: int = 300) -> bool:
        row = self.store.external_observation_by_id(observation_id)
        if not row:
            return False
        observation = external_observation_from_row(row)
        current = self.observation_engine.validate_current(observation, ttl_seconds)
        self.store.save_external_observation(observation)
        return current

    def external_observations(self, integration_id: str | None = None, limit: int = 100) -> list[ExternalObservation]:
        return [external_observation_from_row(row) for row in self.store.find_external_observations(integration_id=integration_id, limit=limit)]

    def external_resources(self, integration_id: str | None = None, limit: int = 100) -> list[ExternalResourceState]:
        return [external_resource_from_row(row) for row in self.store.find_external_resources(integration_id=integration_id, limit=limit)]

    def flexibility_recommendation(self, operation: IntegrationOperation, result: ExternalOperationResult) -> dict[str, Any]:
        recommendation = {"action": "stop", "strategy": "external-safe-recovery", "reason": result.error or result.failure_class.value, "replan": False, "policy_preserved": True}
        if result.failure_class in {ExternalFailureClass.TIMEOUT, ExternalFailureClass.RATE_LIMIT, ExternalFailureClass.EXTERNAL_SERVICE, ExternalFailureClass.CONNECTOR}:
            recommendation = {"action": "retry_once_or_fallback", "strategy": "external-safe-recovery", "reason": result.error or result.failure_class.value, "replan": True, "policy_preserved": True}
        if self.flexibility is not None:
            try:
                from .flexibility import FlexibilityContext
                from .models import Goal
                context = FlexibilityContext(Goal(f"external operation {operation.operation}"), failures=[{"failure_class": result.failure_class.value, "error": result.error}], constraints={"external_policy": self.policy.to_dict(), "approved_fallbacks": self.fallback_candidates(operation)})
                decision = self.flexibility.recommend_next_action(context)
                decision_data = decision.to_dict()
                if decision_data.get("action") == "execute":
                    decision_data["action"] = recommendation["action"]
                recommendation.update(decision_data)
                recommendation["policy_preserved"] = True
            except Exception as exc:
                recommendation["consultation_error"] = type(exc).__name__
        return recommendation

    def fallback_candidates(self, operation: IntegrationOperation | str, exclude_integration_id: str | None = None) -> list[dict[str, Any]]:
        if isinstance(operation, str):
            row = self.store.integration_operation_by_id(operation)
            if not row:
                return []
            operation, _ = integration_operation_from_row(row)
        candidates = []
        for integration in self.list_integrations():
            if integration.integration_id == (exclude_integration_id or operation.integration_id) or not integration.enabled:
                continue
            if operation.operation in integration.supported_operations and integration.lifecycle_state is IntegrationLifecycle.ACTIVE:
                candidates.append({"integration_id": integration.integration_id, "provider": integration.provider, "operation": operation.operation, "reliability": integration.health.reliability, "policy_checked": True})
        return sorted(candidates, key=lambda item: (-float(item["reliability"]), item["integration_id"]))

    def retry_operation(self, operation_id: str | IntegrationOperation, actor: str = "kernel") -> ExternalOperationResult:
        operation_id = operation_id.operation_id if isinstance(operation_id, IntegrationOperation) else operation_id
        row = self.store.integration_operation_by_id(operation_id)
        if not row:
            raise KeyError(operation_id)
        operation, payload = integration_operation_from_row(row)
        prior_rows = self.store.find_external_operation_results(operation_id, limit=1)
        if not prior_rows:
            return self.execute_operation(operation_id, actor=actor)
        prior = external_operation_result_from_row(prior_rows[0])
        if prior.status is ExternalOperationStatus.UNKNOWN or (operation.risk_level.mutating and prior.status in {ExternalOperationStatus.TIMEOUT, ExternalOperationStatus.UNKNOWN}):
            return ExternalOperationResult(operation_id, ExternalOperationStatus.UNKNOWN, ExternalFailureClass.UNKNOWN, error="retry blocked because external outcome is unknown")
        retry_count = int(operation.metadata.get("retry_count", 0))
        if retry_count >= self.policy.max_retries or prior.failure_class not in {ExternalFailureClass.TIMEOUT, ExternalFailureClass.RATE_LIMIT, ExternalFailureClass.EXTERNAL_SERVICE, ExternalFailureClass.CONNECTOR}:
            return ExternalOperationResult(operation_id, ExternalOperationStatus.BLOCKED, prior.failure_class, error="bounded external retry limit or failure classification prevents retry")
        operation.metadata["retry_count"] = retry_count + 1
        operation.status = ExternalOperationStatus.REQUESTED
        operation.updated_at = utc_now()
        self.store.save_integration_operation(operation, payload)
        return self.execute_operation(operation_id, actor=actor)

    def external_diff(self, before_observation_id: str, after_observation_id: str) -> ExternalChange:
        before_row = self.store.external_observation_by_id(before_observation_id)
        after_row = self.store.external_observation_by_id(after_observation_id)
        before = external_observation_from_row(before_row) if before_row else None
        after = external_observation_from_row(after_row) if after_row else None
        change = self.changes.compare(before, after)
        self.store.save_external_change(change)
        self._emit(EventType.EXTERNAL_CHANGE_DETECTED, {"change": change.to_dict()})
        return change

    def register_capabilities(self, integration: Integration) -> list[str]:
        if not self.capability_intelligence:
            return []
        from .capability import Capability, CapabilityCategory, CapabilityLifecycle, HealthStatus, Provenance, ProvenanceSource, Tool, ToolHealth, ToolStatus
        ids: list[str] = []
        for item in integration.capabilities:
            capability_id = f"external:{integration.integration_id}:{item.capability_id}"
            capability = Capability(capability_id, item.name, item.description, CapabilityCategory.NETWORK if integration.integration_type in {IntegrationType.HTTP_API, IntegrationType.WEBHOOK} else CapabilityCategory.COMMUNICATION if integration.integration_type is IntegrationType.EMAIL else CapabilityCategory.DATA, integration.version, CapabilityLifecycle.ACTIVE if integration.enabled else CapabilityLifecycle.DISABLED, integration.provider, "external_connector", required_permissions=item.permissions, supported_inputs=["object"], supported_outputs=["object"], risk_level=RiskLevel.HIGH if item.risk.requires_approval else RiskLevel.LOW, reliability=integration.health.reliability, availability=integration.enabled, environment_requirements=integration.environment_compatibility.environment_requirements, provenance=Provenance(ProvenanceSource.USER_REGISTERED, integration.integration_id, integration.version), metadata={"integration_id": integration.integration_id, "external": True})
            self.capability_intelligence.register_capability(capability)
            tool = Tool(f"external-tool:{integration.integration_id}:{item.capability_id}", item.name, item.description, integration.version, integration.provider, [capability_id], item.input_schema, item.output_schema, item.permissions, RiskLevel.HIGH if item.risk.requires_approval else RiskLevel.LOW, integration_health_timeout(integration), {"max_response_bytes": self.policy.max_response_bytes}, integration.environment_compatibility.environment_requirements, integration.enabled, ToolHealth(), integration.health.reliability, Provenance(ProvenanceSource.USER_REGISTERED, integration.integration_id, integration.version), f"external:{integration.integration_id}", status=ToolStatus.ACTIVE if integration.enabled else ToolStatus.DISABLED, metadata={"integration_id": integration.integration_id, "connector_execution": "kernel_only"}, architecture_version=integration.architecture_version)
            self.capability_intelligence.register_tool(tool)
            ids.append(capability_id)
        return ids

    def requirements_for_goal(self, goal: str) -> list[dict[str, Any]]:
        lowered = goal.lower()
        markers = {"external": "read", "api": "read", "email": "send", "message": "send", "webhook": "receive", "service": "read", "document": "read"}
        requirements = []
        for marker, operation in markers.items():
            if marker in lowered:
                requirements.append({"marker": marker, "operation": operation, "source": "goal_text", "trusted": False})
        return requirements[:4]

    def discover_for_goal(self, goal: str) -> list[Integration]:
        required = self.requirements_for_goal(goal)
        if not required:
            return []
        return [item for item in self.list_integrations() if item.enabled and any(req["operation"] in item.supported_operations for req in required)]

    def record_memory_evidence(self, result: ExternalOperationResult, operation: IntegrationOperation) -> None:
        if not self.memory:
            return
        capture = getattr(self.memory, "capture_external", None)
        if capture:
            capture({"operation_id": operation.operation_id, "integration_id": operation.integration_id, "operation": operation.operation, "status": result.status.value, "failure_class": result.failure_class.value, "output_hash": result.output_hash, "provenance": {"source": "external_operation", "source_id": operation.operation_id}})

    def _risk_for(self, integration: Integration, operation: str) -> ExternalOperationRisk:
        if operation in {"send", "receive"}:
            return ExternalOperationRisk.COMMUNICATION
        if operation == "delete":
            return ExternalOperationRisk.DESTRUCTIVE
        if operation in {"create", "update"}:
            return ExternalOperationRisk.LOW_RISK_WRITE
        for item in integration.capabilities:
            if operation in item.supported_operations:
                return item.risk
        return integration.risk_classification

    def _valid_input(self, schema: dict[str, Any], payload: Any) -> bool:
        return self._schema_accepts(schema, payload)

    def _valid_output(self, schema: dict[str, Any], output: Any) -> bool:
        return self._schema_accepts(schema, output)

    @staticmethod
    def _schema_accepts(schema: dict[str, Any], value: Any) -> bool:
        if not isinstance(schema, dict) or not schema:
            return True
        expected = schema.get("type")
        if expected == "object":
            if not isinstance(value, dict):
                return False
            required = schema.get("required", [])
            return all(name in value for name in required)
        if expected == "array":
            return isinstance(value, list)
        if expected == "string":
            return isinstance(value, str)
        if expected == "number":
            return isinstance(value, (int, float)) and not isinstance(value, bool)
        if expected == "boolean":
            return isinstance(value, bool)
        return True

    def _waiting_approval(self, operation: IntegrationOperation, reason: str) -> ExternalOperationResult:
        operation.status = ExternalOperationStatus.WAITING_APPROVAL
        operation.updated_at = utc_now()
        self.store.save_integration_operation(operation, {})
        result = ExternalOperationResult(operation.operation_id, ExternalOperationStatus.WAITING_APPROVAL, ExternalFailureClass.PERMISSION, error=reason)
        self.store.save_external_operation_result(result)
        self._emit(EventType.EXTERNAL_APPROVAL_REQUESTED, {"operation_id": operation.operation_id, "scope_hash": self.approval_scope(operation), "reason": reason})
        return result

    def _blocked_result(self, operation: IntegrationOperation, failure: ExternalFailureClass, error: str) -> ExternalOperationResult:
        operation.status = ExternalOperationStatus.BLOCKED
        self.store.save_integration_operation(operation, {})
        result = ExternalOperationResult(operation.operation_id, ExternalOperationStatus.BLOCKED, failure, error=error)
        self.store.save_external_operation_result(result)
        self._emit(EventType.EXTERNAL_OPERATION_BLOCKED, result.to_dict())
        return result

    def _record_communication(self, operation: IntegrationOperation, payload: dict[str, Any], result: ExternalOperationResult) -> None:
        if operation.risk_level is not ExternalOperationRisk.COMMUNICATION and operation.operation not in {"send", "receive"}:
            return
        record = CommunicationRecord(new_id("communication"), operation.operation_id, operation.integration_id, operation.operation, operation.target, result.status, operation.metadata.get("approval", {}).get("scope_hash"), _hash(payload.get("content", "")) if "content" in payload else "", metadata={"result_status": result.status.value})
        self.store.save_communication_record(record)
        self._emit(EventType.EXTERNAL_COMMUNICATION_RECORDED, {"communication": record.to_dict()})

    def _emit(self, event_type: EventType, payload: dict[str, Any]) -> None:
        try:
            self.store.append_event(Event(new_id("external"), event_type, _redact(payload)))
        except Exception:
            pass


def integration_health_timeout(integration: Integration) -> float:
    return float(integration.metadata.get("timeout_seconds", 15.0))


def integration_from_row(row: dict[str, Any]) -> Integration:
    payload = row.get("payload", row)
    if isinstance(payload, str):
        payload = json.loads(payload)
    capabilities = []
    for item in payload.get("capabilities", []):
        item = dict(item)
        item["risk"] = ExternalOperationRisk(item.get("risk", ExternalOperationRisk.READ_ONLY.value))
        item["data_classification"] = ExternalDataClassification(item.get("data_classification", ExternalDataClassification.INTERNAL.value))
        capabilities.append(IntegrationCapability(**{key: item[key] for key in ("capability_id", "name", "description", "supported_operations", "input_schema", "output_schema", "permissions", "risk", "data_classification", "metadata") if key in item}))
    health = dict(payload.get("health", {})); health["state"] = IntegrationHealthState(health.get("state", IntegrationHealthState.UNKNOWN.value))
    cred = IntegrationCredentialMetadata(**{key: health_value for key, health_value in dict(payload.get("credential_metadata", {})).items() if key in {"reference", "credential_names", "storage", "present", "last_validated", "metadata"}})
    env = IntegrationEnvironment(**{key: value for key, value in dict(payload.get("environment_compatibility", {})).items() if key in {"workspace_type", "environment_requirements", "allowed_environments", "network_required", "metadata"}})
    prov = IntegrationProvenance(**{key: value for key, value in dict(payload.get("provenance", {})).items() if key in {"source", "source_id", "source_version", "actor", "created_at", "lineage"}})
    return Integration(payload["integration_id"], payload["name"], payload["provider"], IntegrationType(payload["integration_type"]), payload["version"], capabilities, list(payload.get("supported_operations", [])), [IntegrationPermission(**{key: value for key, value in item.items() if key in {"permission_id", "name", "description", "scope", "required_for", "approval_required", "metadata"}}) for item in payload.get("required_permissions", [])], ExternalOperationRisk(payload.get("risk_classification", ExternalOperationRisk.READ_ONLY.value)), env, IntegrationHealth(**health), cred, prov, bool(payload.get("enabled", False)), payload.get("architecture_version", ""), IntegrationLifecycle(payload.get("lifecycle_state", IntegrationLifecycle.REGISTERED.value)), payload.get("endpoint", ""), dict(payload.get("metadata", {})), payload.get("created_at", utc_now()), payload.get("updated_at", utc_now()))


def external_policy_from_row(row: dict[str, Any]) -> ExternalAccessPolicy:
    payload = row.get("payload", row)
    if isinstance(payload, str):
        payload = json.loads(payload)
    payload["allowed_data_classifications"] = [ExternalDataClassification(item) for item in payload.get("allowed_data_classifications", [])]
    prov = payload.get("provenance", {})
    payload["provenance"] = IntegrationProvenance(**{key: value for key, value in prov.items() if key in {"source", "source_id", "source_version", "actor", "created_at", "lineage"}})
    return ExternalAccessPolicy(**{key: value for key, value in payload.items() if key in ExternalAccessPolicy.__dataclass_fields__})


def integration_operation_from_row(row: dict[str, Any]) -> tuple[IntegrationOperation, dict[str, Any]]:
    payload = row.get("payload", row)
    if isinstance(payload, str):
        payload = json.loads(payload)
    data = dict(payload.get("operation", payload))
    data["risk_level"] = ExternalOperationRisk(data.get("risk_level", ExternalOperationRisk.READ_ONLY.value))
    data["status"] = ExternalOperationStatus(data.get("status", ExternalOperationStatus.REQUESTED.value))
    item = IntegrationOperation(**{key: value for key, value in data.items() if key in IntegrationOperation.__dataclass_fields__})
    return item, dict(payload.get("request_payload", {}))


def external_operation_result_from_row(row: dict[str, Any]) -> ExternalOperationResult:
    payload = row.get("payload", row)
    if isinstance(payload, str):
        payload = json.loads(payload)
    payload["status"] = ExternalOperationStatus(payload.get("status", ExternalOperationStatus.UNKNOWN.value))
    payload["failure_class"] = ExternalFailureClass(payload.get("failure_class", ExternalFailureClass.UNKNOWN.value))
    return ExternalOperationResult(**{key: value for key, value in payload.items() if key in ExternalOperationResult.__dataclass_fields__})


def external_observation_from_row(row: dict[str, Any]) -> ExternalObservation:
    payload = row.get("payload", row)
    if isinstance(payload, str):
        payload = json.loads(payload)
    payload["freshness"] = ExternalFreshness(payload.get("freshness", ExternalFreshness.UNKNOWN.value))
    payload["trust_level"] = ExternalTrustLevel(payload.get("trust_level", ExternalTrustLevel.UNKNOWN.value))
    provenance = payload.get("provenance", {})
    if isinstance(provenance, dict):
        payload["provenance"] = ExternalObservationProvenance(**{key: value for key, value in provenance.items() if key in ExternalObservationProvenance.__dataclass_fields__})
    return ExternalObservation(**{key: value for key, value in payload.items() if key in ExternalObservation.__dataclass_fields__})


def external_resource_from_row(row: dict[str, Any]) -> ExternalResourceState:
    payload = row.get("payload", row)
    if isinstance(payload, str):
        payload = json.loads(payload)
    return ExternalResourceState(**{key: value for key, value in payload.items() if key in ExternalResourceState.__dataclass_fields__})


__all__ = [name for name in globals() if not name.startswith("_")]
