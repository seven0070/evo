from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
import hashlib
import json
import os
from pathlib import Path
import platform
import resource
import shutil
import sys
import time
from typing import Any, Iterable

from .models import Event, EventType, new_id, utc_now
from .version import __version__


WORLD_SCHEMA_VERSION = "world-v1"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime | None = None) -> str:
    return (value or _now()).isoformat()


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def _hash(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None


class WorldSource(str, Enum):
    LOCAL_ENVIRONMENT = "local_environment"
    WORKSPACE = "workspace"
    SYSTEM = "system"
    PROVIDER = "provider"
    USER_INPUT = "user_input"
    EXTERNAL_SERVICE = "external_service"


class ObservationType(str, Enum):
    FACT = "fact"
    INFERENCE = "inference"
    ASSUMPTION = "assumption"
    UNKNOWN = "unknown"


class TrustLevel(str, Enum):
    TRUSTED = "trusted"
    VERIFIED = "verified"
    OBSERVED = "observed"
    INFERRED = "inferred"
    UNTRUSTED = "untrusted"
    UNKNOWN = "unknown"


class Freshness(str, Enum):
    FRESH = "fresh"
    AGING = "aging"
    STALE = "stale"
    EXPIRED = "expired"
    UNKNOWN = "unknown"


class ChangeKind(str, Enum):
    ADDED = "added"
    REMOVED = "removed"
    CHANGED = "changed"
    UNCHANGED = "unchanged"
    UNKNOWN = "unknown"


class PlanValidationStatus(str, Enum):
    VALID = "plan_valid"
    STALE = "plan_stale"
    INVALID = "plan_invalid"
    REQUIRES_REPLAN = "plan_requires_replan"


class ValidationState(str, Enum):
    UNKNOWN = "unknown"
    VALID = "valid"
    INVALIDATED = "invalidated"
    EXPIRED = "expired"


@dataclass
class EnvironmentState:
    environment_id: str
    environment_version: str
    timestamp: str
    operating_system: str
    architecture: str
    runtime: str
    python_version: str
    agent_version: str
    architecture_version: str
    workspace: str
    filesystem_state: list[dict[str, Any]] = field(default_factory=list)
    available_capabilities: list[dict[str, Any]] = field(default_factory=list)
    available_tools: list[dict[str, Any]] = field(default_factory=list)
    resource_state: dict[str, Any] = field(default_factory=dict)
    network_state: dict[str, Any] = field(default_factory=dict)
    provider_state: list[dict[str, Any]] = field(default_factory=list)
    process_state: dict[str, Any] = field(default_factory=dict)
    configuration_state: dict[str, Any] = field(default_factory=dict)
    constraints: list[str] = field(default_factory=list)
    permissions: dict[str, Any] = field(default_factory=dict)
    health: dict[str, Any] = field(default_factory=dict)
    observations: list[str] = field(default_factory=list)
    provenance: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def static_view(self) -> dict[str, Any]:
        data = self.to_dict()
        data.pop("timestamp", None)
        data.pop("environment_version", None)
        data.pop("observations", None)
        data.pop("provenance", None)
        data["available_tools"] = [{key: item.get(key) for key in ("name", "version", "provider", "availability", "status") if key in item} for item in data.get("available_tools", [])]
        data["health"] = {name: value.get("status") if isinstance(value, dict) else value for name, value in data.get("health", {}).items()}
        process_state = data.get("process_state", {})
        data["process_state"] = {"process_scope": process_state.get("process_scope", "current_agent_only")}
        resources = dict(data.get("resource_state", {}))
        if isinstance(resources.get("memory_available_bytes"), int):
            resources["memory_available_bucket"] = resources.pop("memory_available_bytes") // (256 * 1024 * 1024)
        data["resource_state"] = resources
        return data

    def validate(self) -> list[str]:
        errors: list[str] = []
        if not self.environment_id or not self.environment_version:
            errors.append("environment identity and version are required")
        if not self.workspace:
            errors.append("workspace is required")
        if not isinstance(self.filesystem_state, list):
            errors.append("filesystem_state must be a list")
        if not isinstance(self.available_tools, list) or not isinstance(self.available_capabilities, list):
            errors.append("capability and tool state must be lists")
        if not isinstance(self.permissions, dict) or not isinstance(self.constraints, list):
            errors.append("permissions and constraints have invalid shapes")
        return errors


@dataclass
class EnvironmentSnapshot:
    snapshot_id: str
    environment_id: str
    timestamp: str
    environment_version: str
    agent_version: str
    architecture_version: str
    observation_hash: str
    observation_summary: dict[str, Any]
    provenance: dict[str, Any]
    schema_version: str = WORLD_SCHEMA_VERSION
    immutable_hash: str = ""

    def __post_init__(self) -> None:
        if not self.observation_hash:
            self.observation_hash = _hash(self.observation_summary)
        if not self.immutable_hash:
            self.immutable_hash = _hash(self._integrity_view())

    def _integrity_view(self) -> dict[str, Any]:
        return {"snapshot_id": self.snapshot_id, "environment_id": self.environment_id, "timestamp": self.timestamp, "environment_version": self.environment_version, "agent_version": self.agent_version, "architecture_version": self.architecture_version, "observation_hash": self.observation_hash, "observation_summary": self.observation_summary, "provenance": self.provenance, "schema_version": self.schema_version}

    def to_dict(self) -> dict[str, Any]:
        return {**self._integrity_view(), "immutable_hash": self.immutable_hash}

    def verify(self) -> bool:
        if _hash(self.observation_summary) != self.observation_hash:
            return False
        return _hash(self._integrity_view()) == self.immutable_hash


@dataclass
class WorldObservation:
    observation_id: str
    type: ObservationType
    source: WorldSource
    timestamp: str
    value: Any
    confidence: float
    reliability: float
    environment_id: str
    provenance: dict[str, Any]
    trust_level: TrustLevel
    expiry: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    observation_hash: str = ""

    def __post_init__(self) -> None:
        self.confidence = max(0.0, min(1.0, float(self.confidence)))
        self.reliability = max(0.0, min(1.0, float(self.reliability)))
        if not self.observation_hash:
            self.observation_hash = _hash({"observation_id": self.observation_id, "type": self.type.value, "source": self.source.value, "timestamp": self.timestamp, "value": self.value, "environment_id": self.environment_id, "provenance": self.provenance})

    def freshness(self, at: datetime | None = None) -> Freshness:
        now = at or _now()
        expires = _parse_time(self.expiry)
        observed = _parse_time(self.timestamp)
        if not observed:
            return Freshness.UNKNOWN
        if expires and now >= expires:
            return Freshness.EXPIRED
        age = max(0.0, (now - observed).total_seconds())
        ttl = max(1.0, (expires - observed).total_seconds()) if expires else 300.0
        if age <= ttl * 0.35:
            return Freshness.FRESH
        if age <= ttl * 0.7:
            return Freshness.AGING
        return Freshness.STALE

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["type"] = self.type.value
        data["source"] = self.source.value
        data["trust_level"] = self.trust_level.value
        data["freshness"] = self.freshness().value
        return data

    def validate(self) -> list[str]:
        errors: list[str] = []
        if not self.observation_id or not self.environment_id:
            errors.append("observation and environment IDs are required")
        if not isinstance(self.provenance, dict) or not self.provenance.get("source"):
            errors.append("observation provenance is required")
        if self.trust_level in {TrustLevel.TRUSTED, TrustLevel.VERIFIED} and self.type is not ObservationType.FACT:
            errors.append("trusted or verified observations must be factual")
        return errors


@dataclass
class WorldAssumption:
    assumption_id: str
    statement: str
    source: WorldSource
    confidence: float
    created_at: str
    expiry: str | None
    validation_state: ValidationState = ValidationState.UNKNOWN
    environment_id: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["source"] = self.source.value
        data["validation_state"] = self.validation_state.value
        return data

    def expired(self, at: datetime | None = None) -> bool:
        value = _parse_time(self.expiry)
        return bool(value and (at or _now()) >= value)


@dataclass
class WorldConflict:
    conflict_id: str
    subject: str
    current_value: Any
    historical_value: Any
    current_source: WorldSource
    historical_source: WorldSource
    reason: str
    created_at: str = field(default_factory=utc_now)
    resolution: str = "current_authoritative_state_wins"
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["current_source"] = self.current_source.value
        data["historical_source"] = self.historical_source.value
        return data


@dataclass
class EnvironmentDiffEntry:
    path: str
    change: ChangeKind
    before: Any = None
    after: Any = None
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["change"] = self.change.value
        return data


@dataclass
class EnvironmentDiff:
    diff_id: str
    before_snapshot_id: str
    after_snapshot_id: str
    created_at: str
    entries: list[EnvironmentDiffEntry]
    valid: bool = True
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {"diff_id": self.diff_id, "before_snapshot_id": self.before_snapshot_id, "after_snapshot_id": self.after_snapshot_id, "created_at": self.created_at, "entries": [item.to_dict() for item in self.entries], "valid": self.valid, "warnings": self.warnings}

    @property
    def changed(self) -> bool:
        return any(item.change in {ChangeKind.ADDED, ChangeKind.REMOVED, ChangeKind.CHANGED, ChangeKind.UNKNOWN} for item in self.entries)


@dataclass
class EnvironmentContext:
    goal: str
    environment_id: str
    environment_version: str
    workspace: str
    relevant_filesystem: list[dict[str, Any]] = field(default_factory=list)
    relevant_capabilities: list[dict[str, Any]] = field(default_factory=list)
    relevant_tools: list[dict[str, Any]] = field(default_factory=list)
    resource_state: dict[str, Any] = field(default_factory=dict)
    network_state: dict[str, Any] = field(default_factory=dict)
    provider_state: list[dict[str, Any]] = field(default_factory=list)
    constraints: list[str] = field(default_factory=list)
    permissions: dict[str, Any] = field(default_factory=dict)
    observations: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    context_hash: str = ""
    operating_system: str = ""
    runtime: str = ""
    python_version: str = ""
    architecture: str = ""

    def __post_init__(self) -> None:
        if not self.context_hash:
            self.context_hash = _hash(self.to_dict(include_hash=False))

    def to_dict(self, include_hash: bool = True) -> dict[str, Any]:
        data = asdict(self)
        if include_hash:
            return data
        data.pop("context_hash", None)
        return data


@dataclass
class PlanValidation:
    status: PlanValidationStatus
    reasons: list[str]
    environment_version: str
    checked_at: str = field(default_factory=utc_now)
    invalidated_fields: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["status"] = self.status.value
        return data


@dataclass
class ActionPrediction:
    prediction_id: str
    action: str
    expected_changes: list[dict[str, Any]]
    created_at: str
    environment_id: str
    confidence: float = 0.5
    verified: bool = False
    discrepancies: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class RefreshRequirement:
    refresh_id: str
    kind: str
    subject: str
    reason: str
    requested_at: str = field(default_factory=utc_now)
    ttl_seconds: int = 300
    status: str = "pending"
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class WorldModel:
    environment: EnvironmentState
    observations: list[WorldObservation] = field(default_factory=list)
    assumptions: list[WorldAssumption] = field(default_factory=list)
    conflicts: list[WorldConflict] = field(default_factory=list)
    changes: list[EnvironmentDiffEntry] = field(default_factory=list)
    relevant_memory: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {"environment": self.environment.to_dict(), "observations": [item.to_dict() for item in self.observations], "assumptions": [item.to_dict() for item in self.assumptions], "conflicts": [item.to_dict() for item in self.conflicts], "changes": [item.to_dict() for item in self.changes], "relevant_memory": self.relevant_memory, "warnings": self.warnings}


class EnvironmentDiffEngine:
    def compare(self, before: EnvironmentSnapshot, after: EnvironmentSnapshot) -> EnvironmentDiff:
        warnings: list[str] = []
        if not before.verify() or not after.verify():
            warnings.append("one or both environment snapshots failed integrity validation")
        entries: list[EnvironmentDiffEntry] = []
        if not before.verify() or not after.verify():
            entries.append(EnvironmentDiffEntry("snapshot_integrity", ChangeKind.UNKNOWN, None, None, "corrupted snapshot prevents safe comparison"))
        before_data = before.observation_summary if before.verify() else {}
        after_data = after.observation_summary if after.verify() else {}
        keys = sorted(set(before_data) | set(after_data))
        for key in keys:
            if key not in before_data:
                change = ChangeKind.ADDED
                entries.append(EnvironmentDiffEntry(key, change, None, after_data[key], "present only in newer snapshot"))
            elif key not in after_data:
                entries.append(EnvironmentDiffEntry(key, ChangeKind.REMOVED, before_data[key], None, "present only in older snapshot"))
            elif before_data[key] == after_data[key]:
                entries.append(EnvironmentDiffEntry(key, ChangeKind.UNCHANGED, before_data[key], after_data[key], "no observed change"))
            elif not before.verify() or not after.verify():
                entries.append(EnvironmentDiffEntry(key, ChangeKind.UNKNOWN, None, None, "corrupted snapshot prevents safe comparison"))
            else:
                entries.append(EnvironmentDiffEntry(key, ChangeKind.CHANGED, before_data[key], after_data[key], "observed value changed"))
        return EnvironmentDiff(new_id("envdiff"), before.snapshot_id, after.snapshot_id, utc_now(), entries, not warnings, warnings)


class EnvironmentObserver:
    def __init__(self, workspace: Path, store: Any | None = None, capability_intelligence: Any | None = None, policy: Any | None = None, agent_version: str = __version__, architecture_version: str = ""):
        self.workspace = Path(workspace).expanduser().resolve()
        self.store = store
        self.capability_intelligence = capability_intelligence
        self.policy = policy
        self.agent_version = agent_version
        self.architecture_version = architecture_version
        self.max_files = 200
        self.max_file_hash_bytes = 65536

    def observe(self, goal: str = "", relevant_paths: Iterable[str] | None = None, task: Any | None = None) -> EnvironmentState:
        timestamp = utc_now()
        filesystem = self._filesystem_state(relevant_paths)
        capabilities, tools, health, providers = self._capability_state()
        resource_state = self._resource_state()
        network_state = {"policy": "restricted", "allowed": False, "provider_checks": "not_performed", "external_scan": False}
        if self.policy:
            network_state["policy"] = "restricted_by_default"
        environment_id = _hash({"workspace": str(self.workspace), "agent_version": self.agent_version})[:20]
        state = EnvironmentState(environment_id, "pending", timestamp, platform.system(), platform.machine(), platform.python_implementation(), platform.python_version(), self.agent_version, self.architecture_version, str(self.workspace), filesystem, capabilities, tools, resource_state, network_state, providers, {"pid": os.getpid(), "process_scope": "current_agent_only"}, {"allowed_commands": sorted(getattr(self.policy, "allowed_commands", set())), "approval_risk_levels": sorted(getattr(item, "value", str(item)) for item in getattr(self.policy, "approval_required_for", set())), "configuration_secrets": "not_collected"}, ["workspace_confinement", "kernel_execution_authority", "verification_authority", "no_unrestricted_host_scan", "no_external_ingestion"], {"workspace_read": self.workspace.is_dir(), "workspace_write": os.access(self.workspace, os.W_OK), "shell_policy_observed": bool(self.policy)}, health, [], {"source": "EnvironmentObserver", "mechanism": "bounded_local_observation", "observed_at": timestamp, "agent_version": self.agent_version}, {"goal": goal[:300], "max_files": self.max_files})
        state.environment_version = _hash(state.static_view())[:20]
        state.observations = [f"environment:{state.environment_version}", f"filesystem:{_hash(filesystem)[:20]}", f"resources:{_hash(resource_state)[:20]}"]
        return state

    def _filesystem_state(self, relevant_paths: Iterable[str] | None) -> list[dict[str, Any]]:
        paths: list[Path] = []
        if relevant_paths:
            for raw in list(relevant_paths)[: self.max_files]:
                try:
                    candidate = (self.workspace / raw).resolve() if not Path(raw).is_absolute() else Path(raw).expanduser().resolve()
                    candidate.relative_to(self.workspace)
                    paths.append(candidate)
                except (ValueError, OSError):
                    continue
        else:
            try:
                paths = sorted(self.workspace.iterdir(), key=lambda item: item.name)[: self.max_files]
            except OSError:
                paths = []
        result: list[dict[str, Any]] = []
        for path in paths:
            if path.name == ".evo":
                continue
            try:
                stat = path.stat()
                item: dict[str, Any] = {"path": str(path.relative_to(self.workspace)), "kind": "directory" if path.is_dir() else "file", "size": stat.st_size, "mtime_ns": stat.st_mtime_ns, "available": True, "readable": os.access(path, os.R_OK), "writable": os.access(path, os.W_OK)}
                if path.is_file() and stat.st_size <= self.max_file_hash_bytes:
                    try:
                        item["content_hash"] = hashlib.sha256(path.read_bytes()).hexdigest()
                    except OSError:
                        item["content_hash"] = None
                result.append(item)
            except OSError:
                result.append({"path": str(path), "available": False, "kind": "unknown"})
        return result

    def _capability_state(self) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any], list[dict[str, Any]]]:
        if not self.capability_intelligence:
            return [], [], {}, []
        try:
            capabilities = [item.to_dict() for item in self.capability_intelligence.capabilities.list_capabilities()[:200]]
            tools = [item.to_dict() for item in self.capability_intelligence.tools.list_tools()[:200]]
            health = {item.name: item.health.to_dict() for item in self.capability_intelligence.tools.list_tools()[:200]}
            providers: dict[str, dict[str, Any]] = {}
            for item in tools:
                providers.setdefault(item.get("provider", "unknown"), {"provider": item.get("provider", "unknown"), "available": False, "tools": 0})
                providers[item.get("provider", "unknown")]["tools"] += 1
                providers[item.get("provider", "unknown")]["available"] = providers[item.get("provider", "unknown")]["available"] or bool(item.get("availability", False))
            return capabilities, tools, health, list(providers.values())
        except Exception as exc:
            return [], [], {"observation_error": type(exc).__name__}, []

    @staticmethod
    def _resource_state() -> dict[str, Any]:
        try:
            limits = {"cpu_seconds": resource.getrlimit(resource.RLIMIT_CPU), "open_files": resource.getrlimit(resource.RLIMIT_NOFILE)}
            limits = {key: [None if value == resource.RLIM_INFINITY else value for value in pair] for key, pair in limits.items()}
        except (ValueError, AttributeError):
            limits = {}
        return {"cpu_count": os.cpu_count(), "memory_available_bytes": EnvironmentObserver._memory_available(), "process_limits": limits, "execution_limits_observed": True, "disk": {}}

    @staticmethod
    def _memory_available() -> int | None:
        try:
            for line in Path("/proc/meminfo").read_text(encoding="utf-8").splitlines():
                if line.startswith("MemAvailable:"):
                    return int(line.split()[1]) * 1024
        except (OSError, ValueError):
            return None
        return None


class WorldRefreshEngine:
    def __init__(self, observer: EnvironmentObserver, store: Any | None = None):
        self.observer = observer
        self.store = store
        self.pending: dict[str, RefreshRequirement] = {}

    def request(self, kind: str, subject: str, reason: str, ttl_seconds: int = 300) -> RefreshRequirement:
        item = RefreshRequirement(new_id("refresh"), kind, subject[:200], reason[:500], ttl_seconds=max(1, min(86400, int(ttl_seconds))))
        self.pending[item.refresh_id] = item
        if self.store:
            self.store.save_world_refresh(item)
        return item

    def refresh(self, requirement: RefreshRequirement, goal: str = "") -> EnvironmentState:
        paths = [requirement.subject] if requirement.kind in {"filesystem", "workspace"} and requirement.subject else None
        state = self.observer.observe(goal=goal, relevant_paths=paths)
        requirement.status = "completed"
        self.pending.pop(requirement.refresh_id, None)
        if self.store:
            self.store.update_world_refresh(requirement)
        return state


class WorldConflictDetector:
    def detect(self, observations: Iterable[WorldObservation], historical: Iterable[dict[str, Any]] | None = None) -> list[WorldConflict]:
        conflicts: list[WorldConflict] = []
        seen: dict[str, WorldObservation] = {}
        for item in observations:
            subject = str(item.metadata.get("subject", item.metadata.get("key", item.observation_id)))
            prior = seen.get(subject)
            if prior and prior.value != item.value:
                conflicts.append(WorldConflict(new_id("conflict"), subject, item.value, prior.value, item.source, prior.source, "current observations disagree; preserve both and prefer the newer authoritative observation"))
            seen[subject] = item
        for item in historical or []:
            subject = str(item.get("subject", item.get("key", item.get("memory_id", "historical"))))
            current = seen.get(subject)
            if current and item.get("value") is not None and item.get("value") != current.value:
                conflicts.append(WorldConflict(new_id("conflict"), subject, current.value, item.get("value"), current.source, WorldSource.SYSTEM, "current observed state conflicts with historical evidence"))
        return conflicts


class WorldSurpriseDetector:
    def compare(self, prediction: ActionPrediction, actual: Iterable[WorldObservation]) -> list[dict[str, Any]]:
        actual_by_subject = {str(item.metadata.get("subject", item.metadata.get("key", ""))): item.value for item in actual}
        discrepancies: list[dict[str, Any]] = []
        for expected in prediction.expected_changes:
            subject = str(expected.get("subject", ""))
            if subject and subject in actual_by_subject and actual_by_subject[subject] != expected.get("value"):
                discrepancies.append({"subject": subject, "expected": expected.get("value"), "actual": actual_by_subject[subject], "reason": "expected state differs from observed state"})
        prediction.discrepancies = discrepancies
        prediction.verified = not discrepancies
        return discrepancies


class PlanInvalidationEngine:
    def validate(self, plan: Any, current: EnvironmentState, capability_intelligence: Any | None = None) -> PlanValidation:
        reasons: list[str] = []
        invalidated: list[str] = []
        plan_version = getattr(plan, "environment_version", "")
        plan_context = getattr(plan, "environment_context", {}) or {}
        prior_files = plan_context.get("relevant_filesystem", []) if isinstance(plan_context, dict) else []
        current_files = getattr(current, "filesystem_state", [])
        prior_tools = plan_context.get("relevant_tools", []) if isinstance(plan_context, dict) else []
        current_available_tools = getattr(current, "available_tools", [])
        current_tools_context = [{key: item.get(key) for key in ("name", "version", "provider", "availability", "status") if key in item} for item in current_available_tools]
        relevant_environment_changed = prior_files != current_files or prior_tools != current_tools_context or plan_context.get("workspace") not in {None, getattr(current, "workspace", None)} or plan_context.get("operating_system") not in {None, "", getattr(current, "operating_system", "")} or plan_context.get("python_version") not in {None, "", getattr(current, "python_version", "")} or plan_context.get("architecture") not in {None, "", getattr(current, "architecture", "")} or plan_context.get("network_state") not in (None, getattr(current, "network_state", {})) or plan_context.get("resource_state") not in (None, getattr(current, "resource_state", {}))
        if plan_version and plan_version != current.environment_version and relevant_environment_changed:
            reasons.append("task-relevant environment changed since plan creation")
            invalidated.append("environment_context")
        selected_names: set[str] = set()
        for item in getattr(plan, "capability_selection", []) or []:
            selected = item.get("selected_tool") if isinstance(item, dict) else None
            if isinstance(selected, dict) and selected.get("name"):
                selected_names.add(selected["name"])
        current_tools = {item.get("name") for item in current.available_tools}
        disappeared = sorted(name for name in selected_names if name not in current_tools)
        if disappeared:
            reasons.append("selected tool disappeared from the current environment: " + ", ".join(disappeared))
            invalidated.append("selected_tools")
        required = set()
        for item in getattr(plan, "capability_requirements", []) or []:
            if isinstance(item, dict) and item.get("capability_id"):
                required.add(item["capability_id"])
        current_caps = {item.get("name") for item in current.available_capabilities} | {item.get("capability_id") for item in current.available_capabilities}
        missing = sorted(item for item in required if item not in current_caps)
        if missing:
            reasons.append("required capability is unavailable: " + ", ".join(missing))
            invalidated.append("capability_requirements")
        if not reasons:
            return PlanValidation(PlanValidationStatus.VALID, ["current environment satisfies the persisted plan assumptions"], current.environment_version)
        status = PlanValidationStatus.INVALID if disappeared or missing else PlanValidationStatus.REQUIRES_REPLAN
        return PlanValidation(status, reasons, current.environment_version, invalidated_fields=invalidated)


@dataclass
class ProviderState:
    provider: str
    version: str = "unknown"
    availability: str = "unknown"
    health: str = "unknown"
    latency_ms: float | None = None
    failure_rate: float | None = None
    configuration_present: bool = False
    compatibility: bool | None = None
    observed_at: str = field(default_factory=utc_now)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data.pop("metadata", None) if "metadata" in data and any(key.lower() in {"api_key", "token", "secret", "credentials"} for key in data.get("metadata", {})) else None
        return data


class FreshnessEngine:
    def classify(self, observation: WorldObservation, at: datetime | None = None) -> Freshness:
        return observation.freshness(at)

    def usable(self, observation: WorldObservation, at: datetime | None = None) -> bool:
        return observation.freshness(at) in {Freshness.FRESH, Freshness.AGING} and observation.trust_level not in {TrustLevel.UNTRUSTED, TrustLevel.UNKNOWN}


class FilesystemChangeDetector:
    def compare(self, before: Iterable[dict[str, Any]], after: Iterable[dict[str, Any]]) -> list[EnvironmentDiffEntry]:
        prior = {str(item.get("path")): item for item in before}
        current = {str(item.get("path")): item for item in after}
        entries: list[EnvironmentDiffEntry] = []
        for path in sorted(set(prior) | set(current)):
            if path not in prior:
                entries.append(EnvironmentDiffEntry(f"filesystem:{path}", ChangeKind.ADDED, None, current[path], "file created"))
            elif path not in current:
                entries.append(EnvironmentDiffEntry(f"filesystem:{path}", ChangeKind.REMOVED, prior[path], None, "file deleted"))
            elif prior[path] == current[path]:
                entries.append(EnvironmentDiffEntry(f"filesystem:{path}", ChangeKind.UNCHANGED, prior[path], current[path], "file unchanged"))
            else:
                entries.append(EnvironmentDiffEntry(f"filesystem:{path}", ChangeKind.CHANGED, prior[path], current[path], "file metadata or content changed"))
        return entries


class ResourceIntelligence:
    def assess(self, state: dict[str, Any], requirements: dict[str, Any] | None = None) -> dict[str, Any]:
        requirements = requirements or {}
        available = state.get("memory_available_bytes")
        required = requirements.get("memory_bytes")
        memory_sufficient = required is None or available is None or available >= required
        return {"sufficient": memory_sufficient, "memory_sufficient": memory_sufficient, "cpu_count": state.get("cpu_count"), "requirements": requirements, "kernel_enforced": True, "reasons": [] if memory_sufficient else ["observed memory availability is below the requested batch requirement"]}

    def strategy_constraints(self, state: dict[str, Any], requirements: dict[str, Any] | None = None) -> list[str]:
        assessment = self.assess(state, requirements)
        return ["use smaller bounded batches"] if not assessment["sufficient"] else []


class ProviderFailoverEngine:
    def select(self, providers: Iterable[ProviderState | dict[str, Any]], authorized: Iterable[str]) -> ProviderState | None:
        allowed = {str(item) for item in authorized}
        candidates: list[ProviderState] = []
        for item in providers:
            data = item.to_dict() if hasattr(item, "to_dict") else dict(item)
            name = str(data.get("provider", ""))
            if name not in allowed or data.get("availability") in {False, "unavailable", "unknown"} or data.get("health") in {"failed", "unhealthy"}:
                continue
            candidates.append(ProviderState(name, str(data.get("version", "unknown")), str(data.get("availability", "available")), str(data.get("health", "unknown")), data.get("latency_ms"), data.get("failure_rate"), bool(data.get("configuration_present", False)), data.get("compatibility"), str(data.get("observed_at", utc_now()))))
        return sorted(candidates, key=lambda item: (item.failure_rate if item.failure_rate is not None else 0.5, item.latency_ms if item.latency_ms is not None else float("inf"), item.provider))[0] if candidates else None


class EnvironmentCompatibilityEngine:
    def evaluate(self, required: dict[str, Any], current: EnvironmentState) -> tuple[bool, list[str]]:
        reasons: list[str] = []
        checks = {"operating_system": current.operating_system, "architecture": current.architecture, "runtime": current.runtime, "python_version": current.python_version, "environment_version": current.environment_version}
        for key, expected in required.items():
            if expected is not None and key in checks and str(checks[key]) != str(expected):
                reasons.append(f"{key} mismatch: required {expected}, current {checks[key]}")
        return not reasons, reasons


class WorldModelEngine:
    def __init__(self, store: Any, observer: EnvironmentObserver, refresh: WorldRefreshEngine):
        self.store = store
        self.observer = observer
        self.refresh_engine = refresh
        self.diff_engine = EnvironmentDiffEngine()
        self.conflict_detector = WorldConflictDetector()
        self.surprise_detector = WorldSurpriseDetector()
        self.plan_invalidator = PlanInvalidationEngine()
        self.current: WorldModel | None = None

    def observe(self, goal: str = "", relevant_paths: Iterable[str] | None = None, task: Any | None = None) -> WorldModel:
        environment = self.observer.observe(goal, relevant_paths, task)
        model = WorldModel(environment)
        for item in environment.filesystem_state:
            model.observations.append(WorldObservation(new_id("observation"), ObservationType.FACT, WorldSource.WORKSPACE, environment.timestamp, item, 0.98, 0.98, environment.environment_id, {"source": "EnvironmentObserver", "mechanism": "bounded_filesystem_stat", "observed_at": environment.timestamp, "verified": False}, TrustLevel.OBSERVED, _iso(_now() + timedelta(seconds=300)), {"subject": f"filesystem:{item.get('path', '')}"}))
        model.observations.append(WorldObservation(new_id("observation"), ObservationType.FACT, WorldSource.LOCAL_ENVIRONMENT, environment.timestamp, {"os": environment.operating_system, "architecture": environment.architecture, "python": environment.python_version}, 1.0, 1.0, environment.environment_id, environment.provenance, TrustLevel.TRUSTED, _iso(_now() + timedelta(days=30)), {"subject": "runtime"}))
        for item in environment.available_tools:
            model.observations.append(WorldObservation(new_id("observation"), ObservationType.FACT, WorldSource.SYSTEM, environment.timestamp, {"name": item.get("name"), "available": item.get("availability"), "health": item.get("health", {}).get("status")}, 0.95, 0.95, environment.environment_id, {"source": "CapabilityIntelligence", "mechanism": "registered_tool_state", "observed_at": environment.timestamp, "verified": False}, TrustLevel.OBSERVED, _iso(_now() + timedelta(seconds=60)), {"subject": f"tool:{item.get('name', '')}"}))
        model.observations.append(WorldObservation(new_id("observation"), ObservationType.FACT, WorldSource.LOCAL_ENVIRONMENT, environment.timestamp, environment.resource_state, 0.9, 0.9, environment.environment_id, {"source": "EnvironmentObserver", "mechanism": "bounded_resource_observation", "observed_at": environment.timestamp, "verified": False}, TrustLevel.OBSERVED, _iso(_now() + timedelta(seconds=60)), {"subject": "resources"}))
        model.observations.append(WorldObservation(new_id("observation"), ObservationType.FACT, WorldSource.LOCAL_ENVIRONMENT, environment.timestamp, environment.network_state, 0.95, 0.95, environment.environment_id, {"source": "SecurityPolicy", "mechanism": "policy_state_only", "observed_at": environment.timestamp, "verified": True}, TrustLevel.TRUSTED, _iso(_now() + timedelta(seconds=60)), {"subject": "network"}))
        for provider in environment.provider_state:
            model.observations.append(WorldObservation(new_id("observation"), ObservationType.FACT, WorldSource.PROVIDER, environment.timestamp, provider, 0.8, 0.8, environment.environment_id, {"source": "registered_provider_metadata", "mechanism": "bounded_provider_state", "observed_at": environment.timestamp, "verified": False}, TrustLevel.OBSERVED, _iso(_now() + timedelta(seconds=60)), {"subject": f"provider:{provider.get('provider', 'unknown')}"}))
            try:
                self.store.save_world_provider_state(provider.get("provider", "unknown"), {key: value for key, value in provider.items() if key not in {"api_key", "token", "secret", "credentials"}}, environment.timestamp)
            except Exception:
                pass
        model.conflicts = self.conflict_detector.detect(model.observations)
        for observation in model.observations:
            try:
                self.store.save_world_observation(observation)
            except Exception:
                pass
        for conflict in model.conflicts:
            try:
                self.store.save_world_conflict(conflict)
            except Exception:
                pass
        self.current = model
        return model

    def context_for_task(self, goal: str, relevant_paths: Iterable[str] | None = None, task: Any | None = None) -> EnvironmentContext:
        model = self.current or self.observe(goal, relevant_paths, task)
        text = goal.lower()
        path_tokens = set(relevant_paths or [])
        filesystem = [item for item in model.environment.filesystem_state if not path_tokens or item.get("path") in path_tokens or any(token in str(item.get("path", "")).lower() for token in text.split() if "." in token)]
        if not filesystem:
            filesystem = model.environment.filesystem_state[:30]
        capability_tokens = {"filesystem", "filesystem_read", "filesystem_write", "file_discovery", "text_processing", "csv_processing", "report_generation", "shell", "shell_execution", "verification", "memory_retrieval", "web_research"}
        if "csv" in text:
            capability_tokens.update({"csv_processing", "text_processing"})
        if any(token in text for token in ("report", "summary", "write", "create")):
            capability_tokens.update({"report_generation", "filesystem_write"})
        if any(token in text for token in ("run", "execute", "shell", "command")):
            capability_tokens.update({"shell", "shell_execution"})
        capabilities = [item for item in model.environment.available_capabilities if item.get("name") in capability_tokens][:50]
        tools = [item for item in model.environment.available_tools if any(cap in capability_tokens for cap in item.get("capability_ids", []))][:50]
        observations = [item.to_dict() for item in model.observations if item.freshness() not in {Freshness.EXPIRED, Freshness.UNKNOWN}][:50]
        warnings = list(model.warnings)
        warnings.extend(f"world conflict: {item.subject}; current observed state remains authoritative" for item in model.conflicts)
        return EnvironmentContext(goal, model.environment.environment_id, model.environment.environment_version, model.environment.workspace, filesystem, capabilities, tools, model.environment.resource_state, model.environment.network_state, model.environment.provider_state, model.environment.constraints, model.environment.permissions, observations, warnings, operating_system=model.environment.operating_system, runtime=model.environment.runtime, python_version=model.environment.python_version, architecture=model.environment.architecture)

    def create_snapshot(self, model: WorldModel | None = None) -> EnvironmentSnapshot:
        model = model or self.current
        if not model:
            model = self.observe()
        summary = model.environment.to_dict()
        snapshot = EnvironmentSnapshot(new_id("snapshot"), model.environment.environment_id, model.environment.timestamp, model.environment.environment_version, model.environment.agent_version, model.environment.architecture_version, _hash(summary), summary, {"source": "WorldModelEngine", "mechanism": "observed_environment", "actor": "system", "created_at": utc_now()})
        self.store.save_environment_snapshot(snapshot)
        self._emit(EventType.ENVIRONMENT_SNAPSHOT_CREATED, {"snapshot_id": snapshot.snapshot_id, "environment_id": snapshot.environment_id, "environment_version": snapshot.environment_version, "observation_hash": snapshot.observation_hash})
        return snapshot

    def save_observations(self, model: WorldModel | None = None) -> list[WorldObservation]:
        model = model or self.current
        if not model:
            model = self.observe()
        for observation in model.observations:
            if not observation.validate():
                self.store.save_world_observation(observation)
                self._emit(EventType.WORLD_OBSERVATION, {"observation_id": observation.observation_id, "type": observation.type.value, "source": observation.source.value, "trust_level": observation.trust_level.value})
        for conflict in model.conflicts:
            self.store.save_world_conflict(conflict)
            self._emit(EventType.WORLD_CONFLICT, conflict.to_dict())
        return model.observations

    def refresh(self, kind: str = "environment", subject: str = "", reason: str = "task requested refresh", goal: str = "") -> WorldModel:
        requirement = self.refresh_engine.request(kind, subject, reason)
        self._emit(EventType.WORLD_REFRESH, requirement.to_dict())
        state = self.refresh_engine.refresh(requirement, goal)
        model = WorldModel(state)
        self.current = model
        self._emit(EventType.ENVIRONMENT_OBSERVED, {"environment_id": state.environment_id, "environment_version": state.environment_version, "goal": goal})
        return model

    def latest_snapshot(self) -> EnvironmentSnapshot | None:
        snapshots = self.store.list_environment_snapshots(limit=1)
        return snapshots[0] if snapshots else None

    def observations(self, environment_id: str | None = None, limit: int = 200) -> list[WorldObservation]:
        result: list[WorldObservation] = []
        for payload in self.store.list_world_observations(environment_id=environment_id, limit=limit):
            try:
                observation = WorldObservation.from_dict(payload)
                if not observation.validate():
                    result.append(observation)
            except (TypeError, ValueError, KeyError):
                continue
        return result

    def changes(self, limit: int = 100) -> list[dict[str, Any]]:
        return self.store.list_environment_diffs(limit=limit)

    def diff(self, before_snapshot_id: str, after_snapshot_id: str) -> EnvironmentDiff:
        before = self.store.environment_snapshot_by_id(before_snapshot_id)
        after = self.store.environment_snapshot_by_id(after_snapshot_id)
        if not before or not after:
            raise KeyError("environment snapshot not found")
        result = self.diff_engine.compare(before, after)
        self.store.save_environment_diff(result)
        self._emit(EventType.ENVIRONMENT_DIFF, result.to_dict())
        for entry in result.entries:
            if entry.change is not ChangeKind.UNCHANGED:
                self._emit(EventType.ENVIRONMENT_CHANGED, entry.to_dict())
        return result

    def validate_plan(self, plan: Any, current: WorldModel | None = None) -> PlanValidation:
        model = current or self.current or self.observe()
        result = self.plan_invalidator.validate(plan, model.environment, getattr(self.observer, "capability_intelligence", None))
        if result.status is not PlanValidationStatus.VALID:
            self._emit(EventType.PLAN_INVALIDATED, result.to_dict())
        return result

    def create_assumption(self, statement: str, source: WorldSource = WorldSource.INFERENCE if hasattr(WorldSource, "INFERENCE") else WorldSource.SYSTEM, confidence: float = 0.5, ttl_seconds: int = 300, environment_id: str | None = None) -> WorldAssumption:
        assumption = WorldAssumption(new_id("assumption"), statement[:500], source, max(0.0, min(1.0, float(confidence))), utc_now(), _iso(_now() + timedelta(seconds=max(1, min(86400, int(ttl_seconds))))), ValidationState.UNKNOWN, environment_id or (self.current.environment.environment_id if self.current else ""))
        self.store.save_world_assumption(assumption)
        self._emit(EventType.WORLD_ASSUMPTION_CREATED, assumption.to_dict())
        return assumption

    def validate_assumption(self, assumption: WorldAssumption, model: WorldModel | None = None) -> WorldAssumption:
        model = model or self.current or self.observe()
        statement = assumption.statement.lower()
        valid = True
        if "file exists" in statement:
            target = statement.split("file exists", 1)[1].strip()
            valid = any(item.get("path") == target and item.get("available") for item in model.environment.filesystem_state)
        elif "network permitted" in statement:
            valid = bool(model.environment.network_state.get("allowed"))
        elif "tool available" in statement:
            target = statement.split("tool available", 1)[1].strip()
            valid = any(item.get("name") == target and item.get("availability") for item in model.environment.available_tools)
        assumption.validation_state = ValidationState.VALID if valid else ValidationState.INVALIDATED
        self.store.save_world_assumption(assumption)
        if not valid:
            self._emit(EventType.WORLD_ASSUMPTION_INVALIDATED, assumption.to_dict())
        return assumption

    def prediction(self, action: str, expected_changes: list[dict[str, Any]], model: WorldModel | None = None) -> ActionPrediction:
        model = model or self.current or self.observe()
        result = ActionPrediction(new_id("prediction"), action[:500], expected_changes[:50], utc_now(), model.environment.environment_id)
        self._emit(EventType.WORLD_PREDICTION, result.to_dict())
        return result

    def update_after_action(self, prediction: ActionPrediction | None = None, goal: str = "", relevant_paths: Iterable[str] | None = None) -> WorldModel:
        model = self.observe(goal, relevant_paths)
        self.create_snapshot(model)
        self.save_observations(model)
        if prediction:
            prediction_actual = model.observations
            self.surprise_detector.compare(prediction, prediction_actual)
            if prediction.discrepancies:
                self._emit(EventType.WORLD_SURPRISE, {"prediction_id": prediction.prediction_id, "discrepancies": prediction.discrepancies})
        self._emit(EventType.WORLD_STATE_UPDATED, {"environment_id": model.environment.environment_id, "environment_version": model.environment.environment_version, "observation_count": len(model.observations)})
        return model

    def stats(self) -> dict[str, Any]:
        observations = self.store.list_world_observations(limit=10000)
        snapshots = self.store.list_environment_snapshots(limit=10000)
        now = _now()
        freshness = [WorldObservation.from_dict(item).freshness(now) for item in observations]
        return {"snapshot_count": len(snapshots), "observation_count": len(observations), "active_observations": sum(item not in {Freshness.EXPIRED, Freshness.UNKNOWN} for item in freshness), "stale_observations": freshness.count(Freshness.STALE), "expired_observations": freshness.count(Freshness.EXPIRED), "environment_changes": self.store.count_events(EventType.ENVIRONMENT_CHANGED.value), "world_conflicts": len(self.store.list_world_conflicts(limit=10000)), "invalid_assumptions": sum(1 for item in self.store.list_world_assumptions(limit=10000) if item.get("validation_state") == ValidationState.INVALIDATED.value), "resource_observations": sum(1 for item in observations if item.get("metadata", {}).get("subject") == "resources"), "provider_states": len(self.store.list_world_provider_states(limit=10000)), "filesystem_changes": self.store.count_events(EventType.FILESYSTEM_STATE_CHANGED.value)}

    def _emit(self, event_type: EventType, payload: dict[str, Any]) -> None:
        try:
            self.store.append_event(Event("world-intelligence", event_type, payload))
        except Exception:
            pass


@classmethod
def _observation_from_dict(cls, value: dict[str, Any]) -> WorldObservation:
    data = dict(value)
    data["type"] = ObservationType(data.get("type", ObservationType.UNKNOWN.value))
    data["source"] = WorldSource(data.get("source", WorldSource.SYSTEM.value))
    data["trust_level"] = TrustLevel(data.get("trust_level", TrustLevel.UNKNOWN.value))
    data.pop("freshness", None)
    return cls(**{key: data[key] for key in ("observation_id", "type", "source", "timestamp", "value", "confidence", "reliability", "environment_id", "provenance", "trust_level", "expiry", "metadata", "observation_hash") if key in data})


WorldObservation.from_dict = _observation_from_dict  # type: ignore[attr-defined]
WorldState = WorldModel
WorldEnvironmentIntelligence = WorldModelEngine
EnvironmentIntelligence = WorldModelEngine
