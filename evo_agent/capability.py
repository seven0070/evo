from __future__ import annotations

from collections import OrderedDict
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
import json
import platform
import time
from pathlib import Path
from typing import Any, Iterable

from .memory import MemoryManager, MemoryType, RetrievalQuery
from .metamorphosis import CapabilityRecord, CapabilityRegistry as StructuralCapabilityRegistry
from .models import CapabilityStatus as StructuralCapabilityStatus
from .models import Event, EventType, RiskLevel, new_id, utc_now
from .security import SecurityPolicy
from .storage import SQLiteStore
from .tools import ToolRegistry as RuntimeToolRegistry
from .version import __version__


class CapabilityCategory(str, Enum):
    COMPUTE = "compute"
    FILESYSTEM = "filesystem"
    SHELL = "shell"
    NETWORK = "network"
    DATA = "data"
    TEXT = "text"
    MEDIA = "media"
    MEMORY = "memory"
    RESEARCH = "research"
    COMMUNICATION = "communication"
    VERIFICATION = "verification"
    DELEGATION = "delegation"
    SYSTEM = "system"
    OTHER = "other"


class CapabilityLifecycle(str, Enum):
    ACTIVE = "active"
    DEGRADED = "degraded"
    DISABLED = "disabled"
    DEPRECATED = "deprecated"
    REMOVED = "removed"


class ToolStatus(str, Enum):
    ACTIVE = "active"
    DEGRADED = "degraded"
    DISABLED = "disabled"
    DEPRECATED = "deprecated"
    REMOVED = "removed"


class HealthStatus(str, Enum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNAVAILABLE = "unavailable"
    FAILED = "failed"
    DISABLED = "disabled"


class ProvenanceSource(str, Enum):
    BUILT_IN = "built_in"
    PROMOTED = "promoted"
    USER_REGISTERED = "user_registered"
    PROVIDER = "provider"
    SYSTEM = "system"
    EVOLUTION = "evolution"
    METAMORPHOSIS = "metamorphosis"


class CapabilityAvailability(str, Enum):
    AVAILABLE = "capability_available"
    UNAVAILABLE = "capability_unavailable"
    PARTIAL = "capability_partial"
    INCOMPATIBLE = "capability_incompatible"
    BLOCKED = "capability_blocked"
    UNKNOWN = "capability_unknown"


class CompatibilityResultStatus(str, Enum):
    COMPATIBLE = "compatible"
    PARTIAL = "partial"
    INCOMPATIBLE = "incompatible"
    UNKNOWN = "unknown"


@dataclass
class Provenance:
    source: ProvenanceSource
    source_id: str = ""
    source_version: str = ""
    lineage: list[str] = field(default_factory=list)
    actor: str = "system"
    created_at: str = field(default_factory=utc_now)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["source"] = self.source.value
        return data


@dataclass
class Capability:
    capability_id: str
    name: str
    description: str
    category: CapabilityCategory
    version: str
    status: CapabilityLifecycle
    provider: str
    implementation: str
    required_tools: list[str] = field(default_factory=list)
    required_permissions: list[str] = field(default_factory=list)
    supported_inputs: list[str] = field(default_factory=list)
    supported_outputs: list[str] = field(default_factory=list)
    constraints: list[str] = field(default_factory=list)
    risk_level: RiskLevel = RiskLevel.LOW
    reliability: float = 1.0
    availability: bool = True
    environment_requirements: dict[str, Any] = field(default_factory=dict)
    dependencies: list[str] = field(default_factory=list)
    compatibility: dict[str, Any] = field(default_factory=dict)
    provenance: Provenance = field(default_factory=lambda: Provenance(ProvenanceSource.SYSTEM))
    created_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)
    metadata: dict[str, Any] = field(default_factory=dict)
    prerequisites: list[str] = field(default_factory=list)
    optional_dependencies: list[str] = field(default_factory=list)

    @property
    def risk(self) -> RiskLevel:
        return self.risk_level

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["category"] = self.category.value
        data["status"] = self.status.value
        data["risk_level"] = self.risk_level.value
        data["provenance"] = self.provenance.to_dict()
        return data


@dataclass
class ToolHealth:
    status: HealthStatus = HealthStatus.HEALTHY
    success_count: int = 0
    failure_count: int = 0
    timeout_count: int = 0
    last_success: str | None = None
    last_failure: str | None = None
    average_duration: float = 0.0
    recent_failures: list[str] = field(default_factory=list)

    @property
    def success_rate(self) -> float:
        total = self.success_count + self.failure_count
        return self.success_count / total if total else 0.5

    @property
    def failure_rate(self) -> float:
        total = self.success_count + self.failure_count
        return self.failure_count / total if total else 0.0

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["status"] = self.status.value
        data["success_rate"] = round(self.success_rate, 6)
        return data


@dataclass
class Tool:
    tool_id: str
    name: str
    description: str
    version: str
    provider: str
    capability_ids: list[str]
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]
    permissions: list[str]
    risk_level: RiskLevel
    timeout: float
    resource_limits: dict[str, Any]
    environment_requirements: dict[str, Any]
    availability: bool
    health: ToolHealth
    reliability: float
    provenance: Provenance
    implementation_reference: str
    status: ToolStatus = ToolStatus.ACTIVE
    metadata: dict[str, Any] = field(default_factory=dict)
    architecture_version: str = ""
    dependencies: list[str] = field(default_factory=list)
    created_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["risk_level"] = self.risk_level.value
        data["status"] = self.status.value
        data["provenance"] = self.provenance.to_dict()
        data["health"] = self.health.to_dict()
        return data

    @property
    def available(self) -> bool:
        return self.availability and self.status not in {ToolStatus.DISABLED, ToolStatus.DEPRECATED, ToolStatus.REMOVED} and self.health.status not in {HealthStatus.UNAVAILABLE, HealthStatus.DISABLED, HealthStatus.FAILED}

    @property
    def risk(self) -> RiskLevel:
        return self.risk_level


@dataclass
class CapabilityRequirement:
    requirement_id: str
    capability_id: str
    description: str
    required: bool = True
    priority: int = 50
    input_requirements: dict[str, Any] = field(default_factory=dict)
    output_requirements: dict[str, Any] = field(default_factory=dict)
    constraints: list[str] = field(default_factory=list)
    provenance: Provenance = field(default_factory=lambda: Provenance(ProvenanceSource.SYSTEM))
    status: str = "pending"

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["provenance"] = self.provenance.to_dict()
        return data


@dataclass
class CapabilityContext:
    goal: str
    task: str = ""
    capability_requirements: list[CapabilityRequirement] = field(default_factory=list)
    current_environment: dict[str, Any] = field(default_factory=dict)
    available_capabilities: list[Capability] = field(default_factory=list)
    available_tools: list[Tool] = field(default_factory=list)
    permissions: dict[str, Any] = field(default_factory=dict)
    approvals: dict[str, Any] = field(default_factory=dict)
    memory_evidence: list[dict[str, Any]] = field(default_factory=list)
    historical_performance: dict[str, Any] = field(default_factory=dict)
    resource_limits: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "goal": self.goal,
            "task": self.task,
            "capability_requirements": [item.to_dict() for item in self.capability_requirements],
            "current_environment": self.current_environment,
            "available_capabilities": [item.to_dict() for item in self.available_capabilities],
            "available_tools": [item.to_dict() for item in self.available_tools],
            "permissions": self.permissions,
            "approvals": self.approvals,
            "memory_evidence": self.memory_evidence,
            "historical_performance": self.historical_performance,
            "resource_limits": self.resource_limits,
        }


@dataclass
class CompatibilityResult:
    status: CompatibilityResultStatus
    checks: dict[str, Any]
    reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["status"] = self.status.value
        return data


@dataclass
class ToolCandidate:
    tool: Tool
    compatibility: CompatibilityResult
    policy_result: dict[str, Any]
    score: float = 0.0
    rejection_reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"tool": self.tool.to_dict(), "compatibility": self.compatibility.to_dict(), "policy_result": self.policy_result, "score": self.score, "rejection_reason": self.rejection_reason}


@dataclass
class ToolSelection:
    requirement_id: str
    selected_tool: Tool | None
    candidate_tools: list[ToolCandidate]
    score: float
    reasoning: str
    rejected_candidates: list[dict[str, Any]]
    policy_result: dict[str, Any]
    compatibility_result: dict[str, Any]
    timestamp: str = field(default_factory=utc_now)
    memory_evidence_ids: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {"requirement_id": self.requirement_id, "selected_tool": self.selected_tool.to_dict() if self.selected_tool else None, "candidate_tools": [item.to_dict() for item in self.candidate_tools], "score": self.score, "reasoning": self.reasoning, "rejected_candidates": self.rejected_candidates, "policy_result": self.policy_result, "compatibility_result": self.compatibility_result, "timestamp": self.timestamp, "memory_evidence_ids": self.memory_evidence_ids}


@dataclass
class CapabilityAnalysis:
    requirement: CapabilityRequirement
    capability: Capability | None
    availability: CapabilityAvailability
    discovery: list[ToolCandidate]
    selection: ToolSelection
    reasons: list[str] = field(default_factory=list)
    structural: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {"requirement": self.requirement.to_dict(), "capability": self.capability.to_dict() if self.capability else None, "availability": self.availability.value, "discovery": [item.to_dict() for item in self.discovery], "selection": self.selection.to_dict(), "reasons": self.reasons, "structural": self.structural}


@dataclass
class GraphValidation:
    valid: bool
    order: list[str]
    errors: list[str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class CapabilityValidator:
    def validate(self, capability: Capability) -> list[str]:
        errors: list[str] = []
        if not capability.capability_id or not capability.name or not capability.version:
            errors.append("capability_id, name, and version are required")
        if not isinstance(capability.category, CapabilityCategory):
            errors.append("category is invalid")
        if not isinstance(capability.status, CapabilityLifecycle):
            errors.append("status is invalid")
        if not isinstance(capability.provenance, Provenance) or not capability.provenance.source:
            errors.append("provenance is required")
        if not 0.0 <= capability.reliability <= 1.0:
            errors.append("reliability must be between 0 and 1")
        if capability.capability_id in capability.dependencies:
            errors.append("capability cannot depend on itself")
        return errors


class ToolValidator:
    def validate(self, tool: Tool) -> list[str]:
        errors: list[str] = []
        if not tool.tool_id or not tool.name or not tool.version:
            errors.append("tool_id, name, and version are required")
        if not isinstance(tool.input_schema, dict) or tool.input_schema.get("type") != "object":
            errors.append("input_schema must be an object schema")
        if not isinstance(tool.output_schema, dict) or "type" not in tool.output_schema:
            errors.append("output_schema must declare a type")
        if not tool.capability_ids:
            errors.append("at least one capability mapping is required")
        if not isinstance(tool.permissions, list):
            errors.append("permissions must be a list")
        if not isinstance(tool.resource_limits, dict):
            errors.append("resource_limits must be an object")
        if not isinstance(tool.provenance, Provenance) or not tool.provenance.source:
            errors.append("provenance is required")
        if tool.timeout <= 0:
            errors.append("timeout must be positive")
        return errors


class CapabilityRegistryFacade:
    """Runtime-rich facade over the existing structural CapabilityRegistry and SQLite table."""

    def __init__(self, store: SQLiteStore):
        self.store = store
        self.structural = StructuralCapabilityRegistry(store)
        self.validator = CapabilityValidator()

    def register_capability(self, capability: Capability) -> Capability:
        errors = self.validator.validate(capability)
        if errors:
            raise ValueError("Invalid capability: " + "; ".join(errors))
        structural = CapabilityRecord(capability.capability_id, capability.name, capability.provider, capability.version, list(capability.dependencies), list(capability.required_permissions), capability.risk_level.value, StructuralCapabilityStatus.ACTIVE if capability.status in {CapabilityLifecycle.ACTIVE, CapabilityLifecycle.DEGRADED} else StructuralCapabilityStatus.DEPRECATED, {**capability.metadata, "phase12": capability.to_dict()})
        self.structural.register(structural)
        return capability

    def _from_row(self, row: dict[str, Any]) -> Capability:
        metadata = json.loads(row.get("metadata", "{}")) if isinstance(row.get("metadata"), str) else dict(row.get("metadata", {}))
        payload = metadata.get("phase12", {})
        provenance_data = payload.get("provenance", {"source": ProvenanceSource.SYSTEM.value})
        return Capability(
            capability_id=row["capability_id"], name=row["name"], description=payload.get("description", row["name"]), category=CapabilityCategory(payload.get("category", self._category_for(row["name"]))), version=row["version"], status=CapabilityLifecycle(payload.get("status", "active" if row.get("status") == "active" else "deprecated")), provider=row.get("provider_component", payload.get("provider", "unknown")), implementation=payload.get("implementation", row.get("provider_component", "unknown")), required_tools=list(payload.get("required_tools", [])), required_permissions=json.loads(row.get("permissions_required", "[]")) if isinstance(row.get("permissions_required"), str) else list(row.get("permissions_required", [])), supported_inputs=list(payload.get("supported_inputs", [])), supported_outputs=list(payload.get("supported_outputs", [])), constraints=list(payload.get("constraints", [])), risk_level=RiskLevel(payload.get("risk_level", row.get("risk_class", "low")) if payload.get("risk_level", row.get("risk_class", "low")) in {item.value for item in RiskLevel} else "low"), reliability=float(payload.get("reliability", 1.0)), availability=bool(payload.get("availability", row.get("status") == "active")), environment_requirements=dict(payload.get("environment_requirements", {})), dependencies=json.loads(row.get("dependencies", "[]")) if isinstance(row.get("dependencies"), str) else list(row.get("dependencies", [])), compatibility=dict(payload.get("compatibility", {})), provenance=Provenance(ProvenanceSource(provenance_data.get("source", "system")), provenance_data.get("source_id", ""), provenance_data.get("source_version", ""), list(provenance_data.get("lineage", [])), provenance_data.get("actor", "system"), provenance_data.get("created_at", row.get("created_at", utc_now()))), created_at=row.get("created_at", utc_now()), updated_at=payload.get("updated_at", row.get("created_at", utc_now())), metadata=metadata)

    @staticmethod
    def _category_for(name: str) -> str:
        if name in {"filesystem", "filesystem_read", "filesystem_write", "file_search", "file_discovery"}:
            return CapabilityCategory.FILESYSTEM.value
        if name in {"shell", "shell_execution"}:
            return CapabilityCategory.SHELL.value
        if name in {"memory", "memory_retrieval"}:
            return CapabilityCategory.MEMORY.value
        if name in {"verification"}:
            return CapabilityCategory.VERIFICATION.value
        if name in {"web_research"}:
            return CapabilityCategory.RESEARCH.value
        if name in {"text_processing", "report_generation"}:
            return CapabilityCategory.TEXT.value
        return CapabilityCategory.OTHER.value

    def get_capability(self, capability_id: str) -> Capability | None:
        row = self.store.capability_by_id(capability_id)
        if not row:
            row = next((item for item in self.store.find_capabilities(limit=1000) if item["name"] == capability_id), None)
        if not row:
            return None
        try:
            return self._from_row(row)
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            return None

    def list_capabilities(self, limit: int = 1000) -> list[Capability]:
        records: list[Capability] = []
        for row in self.store.find_capabilities(limit):
            try:
                records.append(self._from_row(row))
            except (KeyError, TypeError, ValueError, json.JSONDecodeError):
                continue
        return records

    def update_capability(self, capability: Capability) -> Capability:
        return self.register_capability(capability)

    def deprecate_capability(self, capability_id: str) -> Capability:
        capability = self._required(capability_id)
        capability.status = CapabilityLifecycle.DEPRECATED
        capability.availability = False
        capability.updated_at = utc_now()
        return self.register_capability(capability)

    def disable_capability(self, capability_id: str) -> Capability:
        capability = self._required(capability_id)
        capability.status = CapabilityLifecycle.DISABLED
        capability.availability = False
        capability.updated_at = utc_now()
        return self.register_capability(capability)

    def find_capabilities(self, query: str, limit: int = 100) -> list[Capability]:
        query_tokens = set(query.lower().split())
        return [item for item in self.list_capabilities(limit=1000) if query_tokens & set((item.name + " " + item.description + " " + item.category.value).lower().split())][:limit]

    def _required(self, capability_id: str) -> Capability:
        capability = self.get_capability(capability_id)
        if not capability:
            raise KeyError(capability_id)
        return capability


class ToolIntelligenceRegistry:
    def __init__(self, store: SQLiteStore, runtime_registry: RuntimeToolRegistry | None = None):
        self.store = store
        self.runtime_registry = runtime_registry
        self.capabilities: CapabilityRegistryFacade | None = None
        self.invalidate_callback = lambda: None
        self.validator = ToolValidator()

    def register_tool(self, tool: Tool) -> Tool:
        errors = self.validator.validate(tool)
        if self.capabilities:
            known = {item.name for item in self.capabilities.list_capabilities()} | {item.capability_id for item in self.capabilities.list_capabilities()}
            errors.extend(f"unknown capability mapping: {item}" for item in tool.capability_ids if item not in known)
        if errors:
            raise ValueError("Invalid tool: " + "; ".join(errors))
        self.store.save_intelligence_tool(tool)
        self.invalidate_callback()
        return tool

    def _from_row(self, row: dict[str, Any]) -> Tool:
        payload = json.loads(row["payload"]) if isinstance(row.get("payload"), str) else dict(row.get("payload", {}))
        health_data = payload.get("health", {})
        provenance_data = payload.get("provenance", {"source": "system"})
        return Tool(tool_id=row["tool_id"], name=row["name"], description=payload.get("description", row["name"]), version=row["version"], provider=row["provider"], capability_ids=list(payload.get("capability_ids", [])), input_schema=dict(payload.get("input_schema", {"type": "object"})), output_schema=dict(payload.get("output_schema", {"type": "string"})), permissions=list(payload.get("permissions", [])), risk_level=RiskLevel(row["risk_level"]), timeout=float(payload.get("timeout", 30)), resource_limits=dict(payload.get("resource_limits", {})), environment_requirements=dict(payload.get("environment_requirements", {})), availability=bool(payload.get("availability", True)), health=ToolHealth(HealthStatus(health_data.get("status", "healthy")), int(health_data.get("success_count", 0)), int(health_data.get("failure_count", 0)), int(health_data.get("timeout_count", 0)), health_data.get("last_success"), health_data.get("last_failure"), float(health_data.get("average_duration", 0.0)), list(health_data.get("recent_failures", []))), reliability=float(payload.get("reliability", 1.0)), provenance=Provenance(ProvenanceSource(provenance_data.get("source", "system")), provenance_data.get("source_id", ""), provenance_data.get("source_version", ""), list(provenance_data.get("lineage", [])), provenance_data.get("actor", "system"), provenance_data.get("created_at", row.get("created_at", utc_now()))), implementation_reference=payload.get("implementation_reference", row["name"]), status=ToolStatus(payload.get("status", row.get("status", "active"))), metadata=dict(payload.get("metadata", {})), architecture_version=payload.get("architecture_version", ""), dependencies=list(payload.get("dependencies", [])), created_at=row.get("created_at", utc_now()), updated_at=payload.get("updated_at", row.get("created_at", utc_now())))

    def get_tool(self, tool_id: str) -> Tool | None:
        row = self.store.intelligence_tool_by_id(tool_id)
        if not row:
            row = next((item for item in self.store.find_intelligence_tools(limit=1000) if item["name"] == tool_id), None)
        if not row:
            return None
        try:
            return self._from_row(row)
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            return None

    def list_tools(self, limit: int = 1000) -> list[Tool]:
        records: list[Tool] = []
        for row in self.store.find_intelligence_tools(limit):
            try:
                records.append(self._from_row(row))
            except (KeyError, TypeError, ValueError, json.JSONDecodeError):
                continue
        return records

    def update_tool(self, tool: Tool) -> Tool:
        return self.register_tool(tool)

    def disable_tool(self, tool_id: str) -> Tool:
        tool = self._required(tool_id)
        tool.status = ToolStatus.DISABLED
        tool.availability = False
        tool.health.status = HealthStatus.DISABLED
        tool.updated_at = utc_now()
        return self.register_tool(tool)

    def deprecate_tool(self, tool_id: str) -> Tool:
        tool = self._required(tool_id)
        tool.status = ToolStatus.DEPRECATED
        tool.availability = False
        tool.updated_at = utc_now()
        return self.register_tool(tool)

    def find_tools(self, query: str, limit: int = 100) -> list[Tool]:
        query_tokens = set(query.lower().split())
        return [item for item in self.list_tools(limit=1000) if query_tokens & set((item.name + " " + item.description + " " + " ".join(item.capability_ids)).lower().split())][:limit]

    def record_outcome(self, tool_id: str, success: bool, duration: float = 0.0, timeout: bool = False, failure: str = "") -> Tool:
        tool = self._required(tool_id)
        health = tool.health
        total = health.success_count + health.failure_count
        health.success_count += int(success)
        health.failure_count += int(not success)
        health.timeout_count += int(timeout)
        health.average_duration = ((health.average_duration * total) + max(0.0, duration)) / max(1, total + 1)
        if success:
            health.last_success = utc_now()
        else:
            health.last_failure = utc_now()
            if failure:
                health.recent_failures = (health.recent_failures + [failure])[-5:]
        if tool.status is ToolStatus.DISABLED:
            health.status = HealthStatus.DISABLED
        elif health.failure_count >= 3 and health.success_rate < 0.5:
            health.status = HealthStatus.FAILED
            tool.status = ToolStatus.DEGRADED
        elif health.failure_count and health.success_rate < 0.8:
            health.status = HealthStatus.DEGRADED
            tool.status = ToolStatus.DEGRADED
        else:
            health.status = HealthStatus.HEALTHY
            if tool.status is ToolStatus.DEGRADED:
                tool.status = ToolStatus.ACTIVE
        tool.reliability = round(min(1.0, max(0.0, (tool.reliability + health.success_rate) / 2)), 6)
        tool.updated_at = utc_now()
        return self.register_tool(tool)

    def sync_runtime_tools(self) -> list[Tool]:
        if not self.runtime_registry:
            return self.list_tools()
        existing = {item.name: item for item in self.list_tools()}
        result: list[Tool] = []
        mappings = {"workspace_list": ["filesystem", "filesystem_read", "file_search", "file_discovery"], "workspace_read": ["filesystem", "filesystem_read", "text_processing", "csv_processing"], "workspace_write": ["filesystem", "filesystem_write", "report_generation"], "shell": ["shell", "shell_execution"]}
        for spec in self.runtime_registry._tools.values():
            prior = existing.get(spec.name)
            tool = prior or Tool(new_id("tool"), spec.name, spec.description, "1.0", "built_in", mappings.get(spec.name, ["planning"]), spec.arguments, {"type": "string"}, ["workspace"] if spec.name.startswith("workspace") else ["shell"], spec.risk, 30.0, {"execution_time": 30, "output_size": 12000}, {"os": [platform.system()]}, True, ToolHealth(), 1.0, Provenance(ProvenanceSource.BUILT_IN, spec.name, __version__), spec.name)
            tool.description = spec.description
            tool.input_schema = spec.arguments
            tool.risk_level = spec.risk
            tool.updated_at = utc_now()
            result.append(self.register_tool(tool))
        return result

    def _required(self, tool_id: str) -> Tool:
        tool = self.get_tool(tool_id)
        if not tool:
            raise KeyError(tool_id)
        return tool

    def validate_input(self, tool_name: str, arguments: dict[str, Any]) -> list[str]:
        tool = self.get_tool(tool_name)
        if not tool:
            return ["tool is not registered in the intelligence registry"]
        return _validate_schema(tool.input_schema, arguments, "input")

    def validate_output(self, tool_name: str, output: Any) -> list[str]:
        tool = self.get_tool(tool_name)
        if not tool:
            return ["tool is not registered in the intelligence registry"]
        return _validate_schema(tool.output_schema, output, "output")


def _validate_schema(schema: dict[str, Any], value: Any, label: str) -> list[str]:
    errors: list[str] = []
    expected = schema.get("type") if isinstance(schema, dict) else None
    if expected == "object" and not isinstance(value, dict):
        errors.append(f"{label} must be an object")
    elif expected == "string" and not isinstance(value, str):
        errors.append(f"{label} must be a string")
    elif expected == "array" and not isinstance(value, list):
        errors.append(f"{label} must be an array")
    if isinstance(value, dict) and isinstance(schema, dict):
        for key in schema.get("required", []):
            if key not in value:
                errors.append(f"{label} is missing required field: {key}")
        if schema.get("additionalProperties") is False:
            errors.extend(f"{label} contains undeclared field: {key}" for key in value if key not in schema.get("properties", {}))
        for key, property_schema in schema.get("properties", {}).items():
            if key not in value or not isinstance(property_schema, dict):
                continue
            property_type = property_schema.get("type")
            property_value = value[key]
            valid = {"string": isinstance(property_value, str), "integer": isinstance(property_value, int) and not isinstance(property_value, bool), "number": isinstance(property_value, (int, float)) and not isinstance(property_value, bool), "boolean": isinstance(property_value, bool), "object": isinstance(property_value, dict), "array": isinstance(property_value, list)}.get(property_type, True)
            if not valid:
                errors.append(f"{label}.{key} must be {property_type}")
    return errors


class CompatibilityEngine:
    def evaluate(self, tool: Tool, capability: Capability, requirement: CapabilityRequirement, environment: dict[str, Any], architecture_version: str = "") -> CompatibilityResult:
        checks: dict[str, Any] = {}
        reasons: list[str] = []
        if capability.name not in tool.capability_ids and capability.capability_id not in tool.capability_ids:
            checks["capability"] = False
            reasons.append("tool does not declare the required capability")
        else:
            checks["capability"] = True
        checks["input"] = self._schema_compatible(tool.input_schema, requirement.input_requirements)
        if not checks["input"]:
            reasons.append("planned input is incompatible with tool input schema")
        checks["output"] = self._schema_compatible(tool.output_schema, requirement.output_requirements)
        if not checks["output"]:
            reasons.append("expected output is incompatible with tool output schema")
        checks["environment"] = self._environment_compatible(tool.environment_requirements, environment)
        if not checks["environment"]:
            reasons.append("tool environment requirements are not satisfied")
        network = environment.get("network_state", {}) if isinstance(environment, dict) else {}
        if "network" in tool.permissions and network.get("allowed") is False:
            checks["network_policy"] = False
            reasons.append("current environment network policy does not permit this tool")
        else:
            checks["network_policy"] = True
        checks["version"] = not (tool.architecture_version and architecture_version and tool.architecture_version != architecture_version)
        if not checks["version"]:
            reasons.append("tool architecture version is stale")
        checks["dependencies"] = not bool(set(tool.dependencies) - set(capability.dependencies) - set(environment.get("dependencies", [])))
        if not checks["dependencies"]:
            reasons.append("tool dependencies are not satisfied")
        if not tool.available:
            reasons.append("tool is not currently available")
        if any(value is False for value in checks.values()) or not tool.available:
            status = CompatibilityResultStatus.INCOMPATIBLE
        elif checks["input"] is None or checks["output"] is None or checks["environment"] is None:
            status = CompatibilityResultStatus.UNKNOWN
        elif tool.health.status is HealthStatus.DEGRADED or not capability.availability:
            status = CompatibilityResultStatus.PARTIAL
        else:
            status = CompatibilityResultStatus.COMPATIBLE
        return CompatibilityResult(status, checks, reasons)

    @staticmethod
    def _schema_compatible(schema: dict[str, Any], requirements: dict[str, Any]) -> bool | None:
        if not requirements:
            return True
        if not isinstance(schema, dict) or schema.get("type") != requirements.get("type", schema.get("type")):
            return False
        required = set(requirements.get("required", []))
        declared = set(schema.get("required", []))
        return required.issubset(declared) if required else True

    @staticmethod
    def _environment_compatible(requirements: dict[str, Any], environment: dict[str, Any]) -> bool | None:
        if not requirements:
            return True
        os_values = requirements.get("os")
        if os_values and environment.get("os") not in os_values:
            return False
        runtime = requirements.get("runtime")
        if runtime and environment.get("runtime") and runtime != environment["runtime"]:
            return False
        return True


class CapabilityGraph:
    def __init__(self, capabilities: Iterable[Capability]):
        self.capabilities = {item.name: item for item in capabilities}

    def validate(self) -> GraphValidation:
        errors: list[str] = []
        for capability in self.capabilities.values():
            if capability.compatibility.get("incompatible") is True:
                errors.append(f"incompatible capability: {capability.name}")
            for dependency in capability.dependencies:
                if dependency not in self.capabilities:
                    errors.append(f"missing dependency: {capability.name} -> {dependency}")
                elif self.capabilities[dependency].status in {CapabilityLifecycle.DISABLED, CapabilityLifecycle.DEPRECATED, CapabilityLifecycle.REMOVED}:
                    errors.append(f"blocked dependency: {capability.name} -> {dependency}")
        visiting: set[str] = set()
        visited: set[str] = set()
        order: list[str] = []

        def visit(name: str) -> None:
            if name in visiting:
                errors.append(f"cycle detected at {name}")
                return
            if name in visited:
                return
            visiting.add(name)
            for dependency in self.capabilities.get(name, Capability("", "", "", CapabilityCategory.OTHER, "", CapabilityLifecycle.REMOVED, "", "")).dependencies:
                if dependency in self.capabilities:
                    visit(dependency)
            visiting.remove(name)
            visited.add(name)
            order.append(name)

        for name in sorted(self.capabilities):
            visit(name)
        return GraphValidation(not errors, order, sorted(set(errors)))

    def compose(self, name: str, component_names: list[str], provenance: Provenance) -> Capability:
        members = [self.capabilities[item] for item in component_names if item in self.capabilities]
        if len(members) != len(component_names):
            raise ValueError("cannot compose missing capabilities")
        risk = max((item.risk_level for item in members), key=lambda item: list(RiskLevel).index(item))
        return Capability(new_id("capability"), name, "Composite capability; permanent promotion remains governed.", CapabilityCategory.OTHER, "1.0", CapabilityLifecycle.ACTIVE, "composition", "+".join(component_names), sorted({tool for item in members for tool in item.required_tools}), sorted({permission for item in members for permission in item.required_permissions}), sorted({value for item in members for value in item.supported_inputs}), sorted({value for item in members for value in item.supported_outputs}), ["composition is advisory until governed promotion"], risk, min(item.reliability for item in members), all(item.availability for item in members), {}, component_names, {"components": component_names}, provenance)


class ToolGraph:
    def __init__(self, tools: Iterable[Tool]):
        self.tools = {item.name: item for item in tools}

    def validate(self) -> GraphValidation:
        errors: list[str] = []
        visiting: set[str] = set()
        visited: set[str] = set()
        order: list[str] = []

        def visit(name: str) -> None:
            if name in visiting:
                errors.append(f"cycle detected at {name}")
                return
            if name in visited:
                return
            tool = self.tools.get(name)
            if not tool:
                errors.append(f"missing tool dependency: {name}")
                return
            visiting.add(name)
            for dependency in tool.dependencies:
                if dependency not in self.tools:
                    errors.append(f"missing tool dependency: {name} -> {dependency}")
                else:
                    visit(dependency)
            visiting.remove(name)
            visited.add(name)
            order.append(name)

        for name in sorted(self.tools):
            visit(name)
        return GraphValidation(not errors, order, sorted(set(errors)))


class ToolDiscoveryEngine:
    def __init__(self, registry: ToolIntelligenceRegistry, capabilities: CapabilityRegistryFacade, compatibility: CompatibilityEngine, policy: SecurityPolicy):
        self.registry = registry
        self.capabilities = capabilities
        self.compatibility = compatibility
        self.policy = policy
        self.cache: OrderedDict[tuple[Any, ...], tuple[float, list[ToolCandidate]]] = OrderedDict()
        self.cache_ttl = 30.0
        self.cache_max = 64

    def discover(self, requirement: CapabilityRequirement, context: CapabilityContext, architecture_version: str = "") -> list[ToolCandidate]:
        key = (requirement.capability_id, json.dumps(requirement.input_requirements, sort_keys=True), json.dumps(requirement.output_requirements, sort_keys=True), architecture_version, json.dumps(context.current_environment, sort_keys=True), tuple(sorted(item.name for item in context.available_tools)))
        cached = self.cache.get(key)
        if cached and time.monotonic() - cached[0] < self.cache_ttl:
            return [self._revalidate(item, requirement, context, architecture_version) for item in cached[1]]
        capability = self.capabilities.get_capability(requirement.capability_id)
        if not capability:
            return []
        candidates: list[ToolCandidate] = []
        context_tool_names = {item.name for item in context.available_tools}
        current_tools = self.registry.list_tools()
        for tool in sorted((item for item in current_tools if not context_tool_names or item.name in context_tool_names), key=lambda item: (item.name, item.tool_id)):
            if capability.name not in tool.capability_ids and capability.capability_id not in tool.capability_ids:
                continue
            compatibility = self.compatibility.evaluate(tool, capability, requirement, context.current_environment, architecture_version)
            policy_result = self._policy_result(tool)
            rejection = ""
            if compatibility.status not in {CompatibilityResultStatus.COMPATIBLE, CompatibilityResultStatus.PARTIAL}:
                rejection = "; ".join(compatibility.reasons)
            elif not policy_result["selectable"]:
                rejection = policy_result["reason"]
            candidates.append(ToolCandidate(tool, compatibility, policy_result, rejection_reason=rejection))
        self.cache[key] = (time.monotonic(), candidates)
        self.cache.move_to_end(key)
        while len(self.cache) > self.cache_max:
            self.cache.popitem(last=False)
        return candidates

    def invalidate(self) -> None:
        self.cache.clear()

    def _revalidate(self, candidate: ToolCandidate, requirement: CapabilityRequirement, context: CapabilityContext, architecture_version: str) -> ToolCandidate:
        capability = self.capabilities.get_capability(requirement.capability_id)
        if capability:
            candidate.compatibility = self.compatibility.evaluate(candidate.tool, capability, requirement, context.current_environment, architecture_version)
        candidate.policy_result = self._policy_result(candidate.tool)
        candidate.rejection_reason = "; ".join(candidate.compatibility.reasons) if candidate.compatibility.status not in {CompatibilityResultStatus.COMPATIBLE, CompatibilityResultStatus.PARTIAL} else ""
        return candidate

    def _policy_result(self, tool: Tool) -> dict[str, Any]:
        requires_approval = tool.risk_level in self.policy.approval_required_for
        return {"selectable": tool.available, "approval_required": requires_approval, "risk": tool.risk_level.value, "declared_permissions": list(tool.permissions), "reason": "Existing Kernel policy and approval authority remain required." if requires_approval else "Selection does not grant permission; Kernel policy remains authoritative."}


class ToolSelectionEngine:
    def __init__(self, memory: MemoryManager | None = None):
        self.memory = memory
        self.selection_count = 0
        self.total_score = 0.0

    def select(self, requirement: CapabilityRequirement, candidates: list[ToolCandidate], context: CapabilityContext) -> ToolSelection:
        accepted = [item for item in candidates if item.rejection_reason == "" and item.compatibility.status in {CompatibilityResultStatus.COMPATIBLE, CompatibilityResultStatus.PARTIAL} and item.policy_result.get("selectable")]
        evidence: list[str] = []
        for candidate in accepted:
            historical = self._historical_score(context.goal, requirement, candidate.tool, evidence)
            risk_penalty = list(RiskLevel).index(candidate.tool.risk_level) * 0.04
            health_score = candidate.tool.health.success_rate if candidate.tool.health else 0.5
            compatibility_score = 1.0 if candidate.compatibility.status is CompatibilityResultStatus.COMPATIBLE else 0.65
            candidate.score = round(0.45 * compatibility_score + 0.2 * candidate.tool.reliability + 0.18 * historical + 0.12 * health_score - risk_penalty, 6)
        accepted.sort(key=lambda item: (-item.score, -item.tool.reliability, list(RiskLevel).index(item.tool.risk_level), item.tool.name, item.tool.tool_id))
        selected_candidate = accepted[0] if accepted else None
        selected = selected_candidate.tool if selected_candidate else None
        rejected = [{"tool": item.tool.to_dict(), "reason": item.rejection_reason or "lower deterministic selection score", "compatibility": item.compatibility.to_dict(), "policy": item.policy_result} for item in candidates if selected is None or item.tool.tool_id != selected.tool_id]
        reasoning = "No compatible permitted candidate was found." if not selected else f"Selected {selected.name} because it had the highest deterministic compatibility, reliability, health, historical-evidence, and risk-adjusted score."
        self.selection_count += 1 if selected else 0
        self.total_score += selected_candidate.score if selected_candidate else 0.0
        return ToolSelection(requirement.requirement_id, selected, candidates, selected_candidate.score if selected_candidate else 0.0, reasoning, rejected, selected_candidate.policy_result if selected_candidate else {}, selected_candidate.compatibility.to_dict() if selected_candidate else {}, memory_evidence_ids=evidence)

    def _historical_score(self, goal: str, requirement: CapabilityRequirement, tool: Tool, evidence: list[str]) -> float:
        if not self.memory:
            return 0.5
        records = self.memory.retrieve(RetrievalQuery(goal=goal, tool=tool.name, capability=requirement.capability_id, include_types={MemoryType.EPISODIC, MemoryType.SEMANTIC, MemoryType.PROCEDURAL}, max_memories=6, max_memory_bytes=5000))
        if not records:
            return 0.5
        outcomes: list[float] = []
        for item in records:
            evidence.append(item.memory.memory_id)
            outcome = str(item.memory.metadata.get("outcome", "")).lower()
            outcomes.append(1.0 if outcome in {"success", "succeeded"} else 0.0 if outcome in {"failure", "failed", "blocked", "timeout"} else 0.5)
        return sum(outcomes) / len(outcomes)


class CapabilityGapDetector:
    def analyze(self, requirement: CapabilityRequirement, capability: Capability | None, candidates: list[ToolCandidate]) -> tuple[CapabilityAvailability, list[str], bool]:
        if not capability:
            structural = any(token in (requirement.description + " " + requirement.capability_id).lower() for token in ("architecture", "structural", "component", "metamorphosis"))
            return CapabilityAvailability.UNKNOWN, ["capability is not present in the active registry"], structural
        if capability.status in {CapabilityLifecycle.DISABLED, CapabilityLifecycle.DEPRECATED, CapabilityLifecycle.REMOVED} or not capability.availability:
            structural = "architecture" in requirement.description.lower() or "structural" in requirement.description.lower()
            return CapabilityAvailability.BLOCKED, ["capability is registered but not currently available"], structural
        if not candidates:
            structural = "architecture" in requirement.description.lower() or "structural" in requirement.description.lower()
            return CapabilityAvailability.UNAVAILABLE, ["no registered tool can provide the capability"], structural
        if not any(item.rejection_reason == "" for item in candidates):
            structural = "architecture" in requirement.description.lower() or "structural" in requirement.description.lower()
            return CapabilityAvailability.INCOMPATIBLE, ["candidate tools were rejected by compatibility or current policy checks"], structural
        if any(item.compatibility.status is CompatibilityResultStatus.PARTIAL for item in candidates):
            return CapabilityAvailability.PARTIAL, ["at least one candidate is usable with degraded compatibility or health"], False
        return CapabilityAvailability.AVAILABLE, ["active capability and compatible tool candidate found"], False

    def assess(self, requirement: CapabilityRequirement, capability: Capability | None, candidates: list[ToolCandidate]) -> tuple[CapabilityAvailability, list[str], bool]:
        return self.analyze(requirement, capability, candidates)

    def detect(self, requirement: CapabilityRequirement, capability: Capability | None, candidates: list[ToolCandidate]) -> tuple[CapabilityAvailability, list[str], bool]:
        return self.analyze(requirement, capability, candidates)


class FallbackEngine:
    def __init__(self, discovery: ToolDiscoveryEngine, selection: ToolSelectionEngine, max_alternatives: int = 2):
        self.discovery = discovery
        self.selection = selection
        self.max_alternatives = max_alternatives

    def alternatives(self, requirement: CapabilityRequirement, context: CapabilityContext, failed_tool_ids: Iterable[str], architecture_version: str = "") -> list[ToolSelection]:
        failed = set(failed_tool_ids)
        candidates = [item for item in self.discovery.discover(requirement, context, architecture_version) if item.tool.tool_id not in failed and item.tool.name not in failed]
        selections: list[ToolSelection] = []
        remaining = candidates
        for _ in range(self.max_alternatives):
            if not remaining:
                break
            choice = self.selection.select(requirement, remaining, context)
            if not choice.selected_tool:
                break
            selections.append(choice)
            remaining = [item for item in remaining if item.tool.tool_id != choice.selected_tool.tool_id]
        return selections


class CapabilityIntelligence:
    """Advisory capability/tool intelligence. It never executes tools or grants permission."""

    REGISTRY_VERSION = "capability-registry-v1"

    def __init__(self, store: SQLiteStore, workspace: Path | None = None, runtime_tools: RuntimeToolRegistry | None = None, policy: SecurityPolicy | None = None, memory: MemoryManager | None = None, agent_version: str = __version__):
        self.store = store
        self.workspace = Path(workspace).expanduser().resolve() if workspace else None
        self.policy = policy or SecurityPolicy(self.workspace or Path.cwd())
        self.agent_version = agent_version
        self.capabilities = CapabilityRegistryFacade(store)
        self.tools = ToolIntelligenceRegistry(store, runtime_tools)
        self.tools.capabilities = self.capabilities
        self.memory = memory or MemoryManager(store, self.workspace)
        self.compatibility = CompatibilityEngine()
        self.discovery = ToolDiscoveryEngine(self.tools, self.capabilities, self.compatibility, self.policy)
        self.tools.invalidate_callback = self.discovery.invalidate
        self.selection = ToolSelectionEngine(self.memory)
        self.gap_detector = CapabilityGapDetector()
        self.fallback = FallbackEngine(self.discovery, self.selection)
        self._event_number = 0
        self._seed_capabilities()
        self.tools.sync_runtime_tools()
        self._seed_advisory_tools()

    def _seed_capabilities(self) -> None:
        existing = {item.name: item for item in self.capabilities.list_capabilities()}
        specs = [
            ("filesystem", CapabilityCategory.FILESYSTEM, "Read and write files within the allowlisted workspace."),
            ("filesystem_read", CapabilityCategory.FILESYSTEM, "Read files within the allowlisted workspace."),
            ("filesystem_write", CapabilityCategory.FILESYSTEM, "Write files within the allowlisted workspace through Kernel approval."),
            ("file_search", CapabilityCategory.FILESYSTEM, "Discover eligible files within the allowlisted workspace."),
            ("file_discovery", CapabilityCategory.FILESYSTEM, "Discover eligible files within the allowlisted workspace."),
            ("text_processing", CapabilityCategory.TEXT, "Read and process bounded text content."),
            ("csv_processing", CapabilityCategory.DATA, "Process bounded CSV content as data."),
            ("report_generation", CapabilityCategory.TEXT, "Generate a report artifact through an approved workspace tool."),
            ("shell", CapabilityCategory.SHELL, "Use the existing permissioned shell capability."),
            ("shell_execution", CapabilityCategory.SHELL, "Request permissioned shell execution through the Kernel."),
            ("verification", CapabilityCategory.VERIFICATION, "Verify outcomes through the existing Verifier."),
            ("planning", CapabilityCategory.SYSTEM, "Construct bounded plans through Cognitive and Flexibility authorities."),
            ("memory", CapabilityCategory.MEMORY, "Use the existing persistent memory subsystem."),
            ("memory_retrieval", CapabilityCategory.MEMORY, "Retrieve bounded historical memory as non-authoritative evidence."),
            ("web_research", CapabilityCategory.RESEARCH, "Research through a registered provider when available; no external installation is implied."),
            ("code_execution", CapabilityCategory.COMPUTE, "Execute governed candidate work only through existing sandbox authorities."),
            ("specialist_delegation", CapabilityCategory.DELEGATION, "Delegate through an explicitly registered and approved provider."),
        ]
        for name, category, description in specs:
            if name in existing:
                continue
            self.capabilities.register_capability(Capability(f"capability_{name}", name, description, category, "1.0", CapabilityLifecycle.ACTIVE, "built_in", name, risk_level=RiskLevel.HIGH if name == "shell_execution" else RiskLevel.LOW, required_permissions=["shell_approval"] if name == "shell_execution" else [], provenance=Provenance(ProvenanceSource.BUILT_IN, name, __version__), metadata={"registry_version": self.REGISTRY_VERSION, "agent_version": self.agent_version}))

    def _seed_advisory_tools(self) -> None:
        existing = {item.name: item for item in self.tools.list_tools()}
        virtual = [("kernel_verifier", "verification", "Kernel-owned verification surface; advisory descriptor only."), ("cognitive_planner", "planning", "Cognitive planning surface; advisory descriptor only."), ("memory_retrieval", "memory_retrieval", "Phase 11 bounded memory retrieval; advisory descriptor only.")]
        for name, capability_name, description in virtual:
            if name in existing:
                continue
            tool = Tool(new_id("tool"), name, description, "1.0", "evo", [capability_name], {"type": "object"}, {"type": "object"}, [], RiskLevel.LOW, 1.0, {"execution_time": 1, "output_size": 12000}, {}, True, ToolHealth(), 1.0, Provenance(ProvenanceSource.SYSTEM, name, self.agent_version), name, metadata={"non_executable": True, "execution_authority": "existing Evo authority"})
            self.tools.register_tool(tool)

    def refresh(self) -> None:
        self.tools.sync_runtime_tools()
        self._seed_advisory_tools()
        self.discovery.invalidate()

    def register_capability(self, capability: Capability) -> Capability:
        self.discovery.invalidate()
        return self.capabilities.register_capability(capability)

    def register_tool(self, tool: Tool) -> Tool:
        registered = self.tools.register_tool(tool)
        self.discovery.invalidate()
        return registered

    def requirements_for(self, goal: str, task: Any | None = None) -> list[CapabilityRequirement]:
        names = list(getattr(task, "required_capabilities", []) or []) if task else []
        description = str(getattr(task, "description", "") or goal)
        task_tool = str(getattr(task, "tool_name", "") or "") if task else ""
        derived = {"workspace_list": ["filesystem_read", "file_discovery"], "workspace_read": ["filesystem_read", "text_processing"], "workspace_write": ["filesystem_write", "report_generation"], "shell": ["shell_execution"]}
        for derived_name in derived.get(task_tool, []):
            if derived_name not in names:
                names.append(derived_name)
        text = (description + " " + goal).lower()
        if not names:
            if any(token in text for token in ("shell", "command", "execute", "run", "test")):
                names.append("shell_execution")
            if any(token in text for token in ("file", "list", "read", "write", "report", "csv")):
                names.append("filesystem")
            if any(token in text for token in ("report", "text", "csv", "summary")):
                names.append("text_processing")
            if any(token in text for token in ("verify", "validation", "check")):
                names.append("verification")
            if not names:
                names.append("planning")
        result: list[CapabilityRequirement] = []
        for name in dict.fromkeys(names):
            result.append(CapabilityRequirement(new_id("requirement"), self._canonical_name(name), f"Fulfill capability requirement: {name}", True, 80 if name in {"verification", "filesystem"} else 50, provenance=Provenance(ProvenanceSource.SYSTEM, str(getattr(task, "task_id", "goal")), self.agent_version)))
        return result

    @staticmethod
    def _canonical_name(name: str) -> str:
        return {"filesystem_read": "filesystem", "workspace_read": "filesystem", "workspace_write": "filesystem", "shell": "shell_execution", "memory": "memory_retrieval", "multimedia_generation": "media"}.get(name, name)

    def build_context(self, goal: str, task: Any | None = None, requirements: list[CapabilityRequirement] | None = None, permissions: dict[str, Any] | None = None, approvals: dict[str, Any] | None = None, resource_limits: dict[str, Any] | None = None, environment: Any | None = None) -> CapabilityContext:
        current = environment.to_dict() if hasattr(environment, "to_dict") else dict(environment or {})
        environment_data = {"os": current.get("operating_system", current.get("os", platform.system())), "runtime": current.get("runtime", platform.python_version()), "workspace": current.get("workspace", str(self.workspace) if self.workspace else "local"), "dependencies": current.get("dependencies", []), "network_state": current.get("network_state", {}), "resource_state": current.get("resource_state", {}), "environment_id": current.get("environment_id", ""), "environment_version": current.get("environment_version", ""), "constraints": current.get("constraints", []), "permissions": current.get("permissions", {})}
        return CapabilityContext(goal, str(getattr(task, "description", "") or ""), requirements or self.requirements_for(goal, task), environment_data, self.capabilities.list_capabilities(), self.tools.list_tools(), permissions or {}, approvals or {}, [], {}, resource_limits or {})

    def analyze_requirement(self, requirement: CapabilityRequirement, context: CapabilityContext, architecture_version: str = "") -> CapabilityAnalysis:
        capability = self.capabilities.get_capability(requirement.capability_id)
        candidates = self.discovery.discover(requirement, context, architecture_version)
        selection = self.selection.select(requirement, candidates, context)
        availability, reasons, structural = self.gap_detector.analyze(requirement, capability, candidates)
        self._emit(EventType.CAPABILITY_REQUIRED, {"requirement": requirement.to_dict(), "goal": context.goal})
        self._emit(EventType.CAPABILITY_DISCOVERED, {"requirement_id": requirement.requirement_id, "capability": capability.name if capability else None, "availability": availability.value})
        self._emit(EventType.TOOL_CANDIDATE, {"requirement_id": requirement.requirement_id, "candidate_count": len(candidates)})
        if selection.selected_tool:
            self._emit(EventType.TOOL_PERMISSION_CHECKED, {"requirement_id": requirement.requirement_id, "tool": selection.selected_tool.name, "policy": selection.policy_result})
            self._emit(EventType.TOOL_SELECTED, {"requirement_id": requirement.requirement_id, "tool": selection.selected_tool.name, "score": selection.score, "reasoning": selection.reasoning, "memory_evidence_ids": selection.memory_evidence_ids})
        for rejected in selection.rejected_candidates:
            self._emit(EventType.TOOL_REJECTED, {"requirement_id": requirement.requirement_id, "tool": rejected["tool"]["name"], "reason": rejected["reason"]})
        if availability is CapabilityAvailability.AVAILABLE:
            self._emit(EventType.CAPABILITY_SATISFIED, {"requirement_id": requirement.requirement_id, "capability": requirement.capability_id, "tool": selection.selected_tool.name if selection.selected_tool else None})
        else:
            self._emit(EventType.CAPABILITY_GAP_DETECTED, {"requirement": requirement.to_dict(), "availability": availability.value, "structural": structural, "reasons": reasons})
        return CapabilityAnalysis(requirement, capability, availability, candidates, selection, reasons, structural)

    def analyze_goal(self, goal: str, task: Any | None = None, architecture_version: str = "") -> list[CapabilityAnalysis]:
        context = self.build_context(goal, task)
        return [self.analyze_requirement(requirement, context, architecture_version) for requirement in context.capability_requirements]

    def fallback_for(self, goal: str, task: Any, failed_tool_ids: Iterable[str], architecture_version: str = "") -> list[ToolSelection]:
        context = self.build_context(goal, task)
        results: list[ToolSelection] = []
        for requirement in context.capability_requirements:
            results.extend(self.fallback.alternatives(requirement, context, failed_tool_ids, architecture_version))
        for result in results:
            self._emit(EventType.TOOL_FALLBACK, {"requirement_id": result.requirement_id, "selected_tool": result.selected_tool.name if result.selected_tool else None, "reasoning": result.reasoning})
        return results

    def record_tool_outcome(self, tool_id: str, success: bool, duration: float = 0.0, timeout: bool = False, failure: str = "", task_id: str | None = None) -> Tool:
        tool = self.tools.record_outcome(tool_id, success, duration, timeout, failure)
        self.discovery.invalidate()
        self._emit(EventType.TOOL_HEALTH_CHANGED, {"tool_id": tool.tool_id, "tool": tool.name, "health": tool.health.to_dict(), "success": success}, task_id)
        return tool

    def capability_graph(self) -> CapabilityGraph:
        return CapabilityGraph(self.capabilities.list_capabilities())

    def tool_graph(self) -> ToolGraph:
        return ToolGraph(self.tools.list_tools())

    def statistics(self) -> dict[str, Any]:
        capabilities = self.capabilities.list_capabilities()
        tools = self.tools.list_tools()
        failures = sum(item.health.failure_count for item in tools)
        return {"registry_version": self.REGISTRY_VERSION, "agent_version": self.agent_version, "total_capabilities": len(capabilities), "active_capabilities": sum(item.status is CapabilityLifecycle.ACTIVE for item in capabilities), "deprecated_capabilities": sum(item.status is CapabilityLifecycle.DEPRECATED for item in capabilities), "disabled_capabilities": sum(item.status is CapabilityLifecycle.DISABLED for item in capabilities), "total_tools": len(tools), "healthy_tools": sum(item.health.status is HealthStatus.HEALTHY for item in tools), "degraded_tools": sum(item.health.status is HealthStatus.DEGRADED for item in tools), "failed_tools": sum(item.health.status is HealthStatus.FAILED for item in tools), "capability_gaps": self.store.count_events(EventType.CAPABILITY_GAP_DETECTED.value), "tool_failures": failures, "fallback_count": self.store.count_events(EventType.TOOL_FALLBACK.value), "average_selection_score": round(self.selection.total_score / self.selection.selection_count, 6) if self.selection.selection_count else 0.0}

    def _emit(self, event_type: EventType, payload: dict[str, Any], task_id: str | None = None) -> None:
        task = task_id or "capability-intelligence"
        self._event_number += 1
        event = Event(task, event_type, {**payload, "registry_version": self.REGISTRY_VERSION, "event_number": self._event_number})
        try:
            self.store.append_event(event)
        except Exception:
            pass


CapabilityRegistry = CapabilityRegistryFacade
ToolRegistry = ToolIntelligenceRegistry
ToolIntelligence = CapabilityIntelligence
