from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from typing import Any
import uuid

from .benchmark import BenchmarkEngine
from .evolver import EvolutionProposal
from .models import CapabilityStatus, CompatibilityStatus, ComponentStatus, Event, EventType, MetamorphosisStatus, ProposalRisk, ProposalStatus, StructuralChangeType
from .promotion import PromotionEngine
from .sandbox import SandboxEngine
from .storage import SQLiteStore
from .version import __version__


@dataclass
class ComponentRecord:
    component_id: str
    name: str
    version: str
    component_type: str
    status: ComponentStatus
    dependencies: list[str]
    interfaces: list[str]
    capabilities: list[str]
    protected: bool
    source_reference: str
    integrity_hash: str
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["status"] = self.status.value
        return data


@dataclass
class CapabilityRecord:
    capability_id: str
    name: str
    provider_component: str
    version: str
    dependencies: list[str]
    permissions_required: list[str]
    risk_class: str
    status: CapabilityStatus
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["status"] = self.status.value
        return data


@dataclass
class ArchitectureManifest:
    architecture_version: str
    agent_version: str
    components: list[dict[str, Any]]
    capabilities: list[dict[str, Any]]
    dependencies: dict[str, list[str]]
    interfaces: dict[str, list[str]]
    protected_components: list[str]
    configuration: dict[str, Any]
    integrity_hash: str
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class StructuralChange:
    change_type: StructuralChangeType
    target_component: str
    affected_components: list[str]
    proposed_value: dict[str, Any]
    rationale: str

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["change_type"] = self.change_type.value if isinstance(self.change_type, StructuralChangeType) else str(self.change_type)
        return data


@dataclass
class MigrationPlan:
    steps: list[str]
    reversible: bool
    rollback_steps: list[str]
    required_checks: list[str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class CompatibilityResult:
    status: CompatibilityStatus
    checks: dict[str, Any]
    reasons: list[str]
    affected_subgraph: list[str]

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["status"] = self.status.value
        return data


@dataclass
class MetamorphosisProposal:
    proposal_id: str
    change_type: StructuralChangeType
    target_component: str
    affected_components: list[str]
    current_architecture: dict[str, Any]
    proposed_architecture: dict[str, Any]
    dependency_changes: dict[str, Any]
    capability_changes: dict[str, Any]
    migration_plan: dict[str, Any]
    expected_benefit: str
    risks: list[str]
    compatibility_requirements: list[str]
    benchmark_requirements: list[str]
    rollback_plan: list[str]
    source_version: str
    metamorphosis_version: str
    risk_class: str
    status: MetamorphosisStatus
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    approval_reason: str = ""
    compatibility: dict[str, Any] | None = None
    change_details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["change_type"] = self.change_type.value if isinstance(self.change_type, StructuralChangeType) else str(self.change_type)
        data["status"] = self.status.value
        return data


@dataclass
class MetamorphosisExperiment:
    experiment_id: str
    proposal_id: str
    baseline_architecture: str
    candidate_architecture: str
    compatibility_status: CompatibilityStatus
    benchmark_evidence_id: str | None
    status: MetamorphosisStatus
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    sandbox_experiment_id: str | None = None
    candidate_version: str | None = None
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["compatibility_status"] = self.compatibility_status.value
        data["status"] = self.status.value
        return data


Component = ComponentRecord
Capability = CapabilityRecord


class ComponentRegistry:
    def __init__(self, store: SQLiteStore):
        self.store = store

    def register(self, component: ComponentRecord) -> ComponentRecord:
        self.store.save_component(component)
        return component

    def list(self) -> list[ComponentRecord]:
        records = []
        for row in self.store.find_components():
            records.append(ComponentRecord(row["component_id"], row["name"], row["version"], row["component_type"], ComponentStatus(row["status"]), json.loads(row["dependencies"]), json.loads(row["interfaces"]), json.loads(row["capabilities"]), bool(row["protected"]), row["source_reference"], row["integrity_hash"], json.loads(row["metadata"]), row["created_at"]))
        return records


class CapabilityRegistry:
    def __init__(self, store: SQLiteStore):
        self.store = store

    def register(self, capability: CapabilityRecord) -> CapabilityRecord:
        self.store.save_capability(capability)
        return capability

    def list(self, limit: int = 100) -> list[CapabilityRecord]:
        records = []
        for row in self.store.find_capabilities(limit=limit):
            records.append(CapabilityRecord(row["capability_id"], row["name"], row["provider_component"], row["version"], json.loads(row["dependencies"]), json.loads(row["permissions_required"]), row["risk_class"], CapabilityStatus(row["status"]), json.loads(row["metadata"]), row["created_at"]))
        return records

    def get(self, capability_id: str) -> CapabilityRecord | None:
        row = self.store.capability_by_id(capability_id)
        if not row:
            return next((item for item in self.list() if item.name == capability_id), None)
        return CapabilityRecord(row["capability_id"], row["name"], row["provider_component"], row["version"], json.loads(row["dependencies"]), json.loads(row["permissions_required"]), row["risk_class"], CapabilityStatus(row["status"]), json.loads(row["metadata"]), row["created_at"])

    def update(self, capability: CapabilityRecord) -> CapabilityRecord:
        self.store.save_capability(capability)
        return capability

    def deprecate(self, capability_id: str) -> CapabilityRecord:
        capability = self.get(capability_id)
        if not capability:
            raise KeyError(capability_id)
        capability.status = CapabilityStatus.DEPRECATED
        return self.update(capability)

    def disable(self, capability_id: str) -> CapabilityRecord:
        capability = self.get(capability_id)
        if not capability:
            raise KeyError(capability_id)
        capability.status = CapabilityStatus.REMOVED
        return self.update(capability)

    def find(self, query: str, limit: int = 100) -> list[CapabilityRecord]:
        tokens = set(query.lower().split())
        return [item for item in self.list(limit=1000) if tokens & set((item.name + " " + item.provider_component + " " + " ".join(item.dependencies)).lower().split())][:limit]


class MetamorphosisEngine:
    METAMORPHOSIS_VERSION = "metamorphosis-v1"
    PROTECTED_CORE = {"governance", "permission enforcement", "approval authority", "sandbox isolation", "verification authority", "rollback authority", "audit integrity", "kill switch", "trust boundary", "promotion authorization", "promotion", "rollback", "sandbox", "verification"}
    REQUIRED_COMPONENTS = {"kernel", "planner", "flexibility", "experience", "evaluation", "evolver", "sandbox", "benchmark", "promotion", "rollback", "tools"}
    REQUIRED_CAPABILITIES = {"filesystem", "planning", "memory", "verification"}
    STRUCTURAL_RISK = {StructuralChangeType.CHANGE_CONFIGURATION: "low", StructuralChangeType.ADD_CAPABILITY: "low", StructuralChangeType.UPGRADE_COMPONENT: "medium", StructuralChangeType.REPLACE_COMPONENT: "medium", StructuralChangeType.REMOVE_CAPABILITY: "medium", StructuralChangeType.REMOVE_COMPONENT: "high", StructuralChangeType.REWIRE_DEPENDENCY: "high", StructuralChangeType.ADD_COMPONENT: "medium"}
    SUPPORTED = set(StructuralChangeType)

    def __init__(self, store: SQLiteStore, source_root: Path, agent_version: str = __version__):
        self.store = store
        self.source_root = Path(source_root).expanduser().resolve()
        if not self.source_root.is_dir():
            raise FileNotFoundError(self.source_root)
        self.agent_version = agent_version
        self.components = ComponentRegistry(store)
        self.capabilities = CapabilityRegistry(store)
        self.bootstrap_architecture()

    def bootstrap_architecture(self) -> ArchitectureManifest:
        existing = self.store.find_architectures(limit=1)
        if existing:
            return self._architecture_from_row(existing[0])
        component_names = ["kernel", "planner", "flexibility", "experience", "evaluation", "evolver", "sandbox", "benchmark", "promotion", "rollback", "tools"]
        protected_component_names = sorted(self.PROTECTED_CORE)
        component_names = component_names + [name for name in protected_component_names if name not in component_names]
        component_records: list[ComponentRecord] = []
        dependencies = {"kernel": ["planner", "flexibility", "experience", "evaluation", "tools"], "planner": ["tools"], "flexibility": ["experience", "evaluation", "tools"], "experience": ["storage"], "evaluation": ["experience", "verifier"], "evolver": ["experience", "evaluation"], "sandbox": ["evolver", "storage"], "benchmark": ["sandbox", "evaluation"], "promotion": ["benchmark", "sandbox", "rollback"], "rollback": ["storage"], "tools": ["security"]}
        interfaces = {name: [f"{name}.to_dict", f"{name}.initialize"] for name in component_names}
        protected_names = sorted(self.PROTECTED_CORE)
        for name in component_names:
            source = self.source_root / "evo_agent" / ("kernel.py" if name == "kernel" else f"{name}.py")
            content = source.read_bytes() if source.is_file() else name.encode()
            component = ComponentRecord(f"component_{name}", name, self.agent_version, "protected_core" if name in self.PROTECTED_CORE else ("core" if name in {"kernel", "sandbox", "promotion", "rollback"} else "service"), ComponentStatus.ACTIVE, dependencies.get(name, []), interfaces[name], [], name in self.PROTECTED_CORE, str(source), hashlib.sha256(content).hexdigest(), {"protected_reason": "governed core" if name in self.PROTECTED_CORE else ""})
            self.components.register(component)
            component_records.append(component)
        capability_specs = [("filesystem", "tools", ["workspace"], ["workspace_allowlist"], "low"), ("shell", "tools", ["permissioned_shell"], ["shell_approval"], "medium"), ("planning", "planner", [], [], "low"), ("memory", "experience", ["sqlite"], [], "low"), ("verification", "evaluation", ["verifier"], [], "protected"), ("code_execution", "sandbox", ["sandbox_isolation"], ["approval"], "high"), ("web_research", "tools", [], ["network"], "medium"), ("specialist_delegation", "kernel", ["approval"], ["delegation"], "high")]
        capability_records = []
        for name, provider, deps, permissions, risk in capability_specs:
            capability = CapabilityRecord(f"capability_{name}", name, provider, "1.0", deps, permissions, risk, CapabilityStatus.ACTIVE, {})
            self.capabilities.register(capability)
            capability_records.append(capability)
        manifest = self._make_manifest(component_records, capability_records, dependencies, interfaces, protected_names, {"agent_version": self.agent_version, "metamorphosis_version": self.METAMORPHOSIS_VERSION})
        self.store.save_architecture(manifest)
        self._event(EventType.ARCHITECTURE_ANALYZED, {"architecture_version": manifest.architecture_version, "integrity_hash": manifest.integrity_hash})
        return manifest

    def analyze_structure(self) -> ArchitectureManifest:
        manifest = self.bootstrap_architecture()
        self._event(EventType.ARCHITECTURE_ANALYZED, {"architecture_version": manifest.architecture_version, "component_count": len(manifest.components), "capability_count": len(manifest.capabilities)})
        return manifest

    def identify_structural_opportunity(self, problem: str, target_component: str = "planner") -> StructuralChange:
        lowered = problem.lower()
        if "capability" in lowered or "filesystem" in lowered:
            change_type = StructuralChangeType.ADD_CAPABILITY
            value = {"name": "structured_context", "provider_component": target_component, "version": "1.0", "dependencies": [], "permissions_required": [], "risk_class": "low"}
        elif "replace" in lowered:
            change_type = StructuralChangeType.REPLACE_COMPONENT
            value = {"replacement": f"{target_component}-candidate", "interfaces": [f"{target_component}.initialize"]}
        else:
            change_type = StructuralChangeType.CHANGE_CONFIGURATION
            value = {"key": "planning.max_steps", "value": 8}
        change = StructuralChange(change_type, target_component, [target_component], value, problem)
        return change

    def classify_change(self, change: StructuralChange) -> str:
        target = change.target_component.lower()
        if target in self.PROTECTED_CORE or any(term in target for term in ("governance", "approval", "permission", "rollback", "sandbox", "audit", "kill")):
            return "protected"
        if not isinstance(change.change_type, StructuralChangeType):
            return "unsupported"
        return self.STRUCTURAL_RISK.get(change.change_type, "unsupported")

    def generate_proposal(self, change: StructuralChange, expected_benefit: str, risks: list[str] | None = None) -> MetamorphosisProposal:
        current = self.analyze_structure()
        proposed = json.loads(json.dumps(current.to_dict()))
        dependency_changes: dict[str, Any] = {}
        capability_changes: dict[str, Any] = {}
        if change.change_type is StructuralChangeType.ADD_CAPABILITY:
            proposed["capabilities"].append({"capability_id": f"candidate_{change.proposed_value.get('name', 'capability')}", "name": change.proposed_value.get("name", "candidate"), "provider_component": change.proposed_value.get("provider_component", change.target_component), "version": change.proposed_value.get("version", "1.0"), "dependencies": change.proposed_value.get("dependencies", []), "permissions_required": change.proposed_value.get("permissions_required", []), "risk_class": change.proposed_value.get("risk_class", "low"), "status": "candidate", "metadata": {}})
            capability_changes = {"add": [change.proposed_value]}
        elif change.change_type is StructuralChangeType.REWIRE_DEPENDENCY:
            proposed["dependencies"].setdefault(change.target_component, []).append(str(change.proposed_value.get("dependency", "unknown")))
            dependency_changes = {"rewire": change.proposed_value}
        elif change.change_type in {StructuralChangeType.REPLACE_COMPONENT, StructuralChangeType.UPGRADE_COMPONENT}:
            replacement = {"component_id": f"candidate_{change.target_component}", "name": change.target_component, "version": str(change.proposed_value.get("version", "candidate")), "component_type": "candidate", "status": "candidate", "dependencies": change.proposed_value.get("dependencies", []), "interfaces": change.proposed_value.get("interfaces", [f"{change.target_component}.initialize"]), "capabilities": change.proposed_value.get("capabilities", []), "protected": False, "source_reference": "structured_candidate", "integrity_hash": "pending", "metadata": {"change_type": change.change_type.value}}
            proposed["components"] = [replacement if item.get("name") == change.target_component else item for item in proposed["components"]]
        elif change.change_type is StructuralChangeType.ADD_COMPONENT:
            proposed["components"].append({"component_id": f"candidate_{change.target_component}", "name": str(change.proposed_value.get("name", change.target_component)), "version": str(change.proposed_value.get("version", "candidate")), "component_type": "candidate", "status": "candidate", "dependencies": change.proposed_value.get("dependencies", []), "interfaces": change.proposed_value.get("interfaces", []), "capabilities": change.proposed_value.get("capabilities", []), "protected": False, "source_reference": "structured_candidate", "integrity_hash": "pending", "metadata": {}})
        elif change.change_type is StructuralChangeType.REMOVE_COMPONENT:
            proposed["components"] = [item for item in proposed["components"] if item.get("name") != change.target_component]
        elif change.change_type is StructuralChangeType.REMOVE_CAPABILITY:
            proposed["capabilities"] = [item for item in proposed["capabilities"] if item.get("name") != change.target_component]
        elif change.change_type is StructuralChangeType.CHANGE_CONFIGURATION:
            key = str(change.proposed_value.get("key", ""))
            if key:
                proposed["configuration"][key] = change.proposed_value.get("value")
        migration = self.build_migration_plan(change)
        proposal = MetamorphosisProposal(self._new_id("metamorphosis"), change.change_type, change.target_component, change.affected_components, current.to_dict(), proposed, dependency_changes, capability_changes, migration.to_dict(), expected_benefit, risks or ["Candidate may introduce compatibility or regression risk."], ["required interfaces remain available", "dependencies resolve", "protected core is unchanged"], ["same Phase 6 benchmark conditions", "capability regression checks", "post-promotion health verification"], migration.rollback_steps, self.agent_version, self.METAMORPHOSIS_VERSION, self.classify_change(change), MetamorphosisStatus.PROPOSED, change_details=change.to_dict())
        self.store.save_metamorphosis_proposal(proposal)
        self._event(EventType.MIGRATION_PLANNED, {"proposal_id": proposal.proposal_id, "steps": migration.steps, "reversible": migration.reversible})
        self._event(EventType.METAMORPHOSIS_PROPOSED, {"proposal_id": proposal.proposal_id, "change_type": proposal.change_type.value if isinstance(proposal.change_type, StructuralChangeType) else str(proposal.change_type), "target_component": proposal.target_component, "risk_class": proposal.risk_class})
        return proposal

    def validate_proposal(self, proposal: MetamorphosisProposal) -> tuple[bool, list[str]]:
        errors: list[str] = []
        if proposal.change_type not in self.SUPPORTED:
            errors.append("unsupported structural change type")
        if proposal.risk_class == "protected" or proposal.target_component.lower() in self.PROTECTED_CORE:
            errors.append("protected component is outside metamorphosis authority")
        if not proposal.migration_plan.get("steps") or not proposal.migration_plan.get("reversible"):
            errors.append("reversible migration plan is required")
        combined_change = json.dumps({"architecture": proposal.proposed_architecture, "change": proposal.change_details}).lower()
        if any(key in combined_change for key in ("execute_code", "generated_code", "arbitrary_source", "disable_rollback", "bypass_approval", "self_replicat")):
            errors.append("unrestricted code or protected-boundary mutation is not allowed")
        current_config = proposal.current_architecture.get("configuration", {})
        proposed_config = proposal.proposed_architecture.get("configuration", {})
        changed_config_keys = set(current_config) | set(proposed_config)
        if any(key in json.dumps({name: proposed_config.get(name) for name in changed_config_keys if current_config.get(name) != proposed_config.get(name)}).lower() for key in ("governance", "permission", "approval", "sandbox", "verification", "rollback", "audit", "kill", "trust", "promotion")):
            errors.append("protected configuration boundary is immutable")
        compatibility = self.check_compatibility(proposal)
        proposal.compatibility = compatibility.to_dict()
        if compatibility.status is CompatibilityStatus.INCOMPATIBLE:
            errors.extend(compatibility.reasons)
        if not proposal.expected_benefit.strip():
            errors.append("expected benefit is required")
        if not proposal.rollback_plan:
            errors.append("rollback plan is required")
        valid = not errors
        proposal.status = MetamorphosisStatus.VALIDATED if valid else MetamorphosisStatus.REJECTED
        self.store.save_metamorphosis_proposal(proposal)
        self._event(EventType.METAMORPHOSIS_VALIDATED if valid else EventType.METAMORPHOSIS_REJECTED, {"proposal_id": proposal.proposal_id, "valid": valid, "errors": errors})
        return valid, errors

    def build_migration_plan(self, change: StructuralChange) -> MigrationPlan:
        return MigrationPlan(["snapshot current architecture", "create structural candidate in isolated sandbox", "migrate only structured manifest/configuration", "validate interfaces and dependencies", "run compatibility checks", "run the existing benchmark", "submit evidence to Phase 7 promotion"], True, ["stop candidate", "restore previous architecture manifest", "retain candidate and evidence for investigation", "use Phase 7 native rollback if promoted"], ["manifest validation", "dependency validation", "interface validation", "capability regression check", "security-policy compatibility"])

    def affected_subgraph(self, manifest: ArchitectureManifest | dict[str, Any], roots: list[str]) -> list[str]:
        dependencies = manifest.dependencies if isinstance(manifest, ArchitectureManifest) else manifest.get("dependencies", {})
        reverse: dict[str, set[str]] = {}
        for component, required in dependencies.items():
            for dependency in required:
                reverse.setdefault(dependency, set()).add(component)
        affected = set(roots)
        pending = list(roots)
        while pending:
            current = pending.pop()
            for dependent in sorted(reverse.get(current, set())):
                if dependent not in affected:
                    affected.add(dependent)
                    pending.append(dependent)
        return sorted(affected)

    def verify_manifest_integrity(self, manifest: ArchitectureManifest) -> bool:
        body = {"agent_version": manifest.agent_version, "components": manifest.components, "capabilities": manifest.capabilities, "dependencies": manifest.dependencies, "interfaces": manifest.interfaces, "protected_components": manifest.protected_components, "configuration": manifest.configuration}
        return hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest() == manifest.integrity_hash

    def check_compatibility(self, proposal: MetamorphosisProposal) -> CompatibilityResult:
        current = proposal.current_architecture
        proposed = proposal.proposed_architecture
        checks: dict[str, Any] = {}
        reasons: list[str] = []
        current_components = {item["name"] for item in current.get("components", [])}
        proposed_components = {item["name"] for item in proposed.get("components", [])}
        current_capabilities = {item["name"] for item in current.get("capabilities", [])}
        proposed_capabilities = {item["name"] for item in proposed.get("capabilities", [])}
        checks["required_components"] = sorted(self.REQUIRED_COMPONENTS - proposed_components)
        checks["required_capabilities"] = sorted(self.REQUIRED_CAPABILITIES - proposed_capabilities)
        current_protected = {item["name"]: item for item in current.get("components", []) if item.get("protected")}
        proposed_by_name = {item["name"]: item for item in proposed.get("components", [])}
        checks["protected_core_unchanged"] = current.get("protected_components", []) == proposed.get("protected_components", []) and all(name in proposed_by_name and proposed_by_name[name].get("protected") and proposed_by_name[name].get("integrity_hash") == item.get("integrity_hash") for name, item in current_protected.items())
        checks["dependencies_available"] = all(dependency in proposed_components or dependency in {"storage", "security", "verifier"} for values in proposed.get("dependencies", {}).values() for dependency in values)
        checks["interfaces_available"] = all(bool(values) for values in proposed.get("interfaces", {}).values())
        checks["configuration_compatible"] = isinstance(proposed.get("configuration", {}), dict)
        checks["database_schema_compatible"] = True
        checks["event_compatible"] = True
        checks["security_policy_compatible"] = checks["protected_core_unchanged"]
        if checks["required_components"]:
            reasons.append(f"required components removed: {checks['required_components']}")
        if checks["required_capabilities"]:
            reasons.append(f"required capabilities removed: {checks['required_capabilities']}")
        if not checks["protected_core_unchanged"] or not checks["security_policy_compatible"]:
            reasons.append("protected security boundary changed")
        if not checks["dependencies_available"]:
            reasons.append("dependency is unavailable")
        if not checks["interfaces_available"]:
            reasons.append("required interface is missing")
        status = CompatibilityStatus.INCOMPATIBLE if reasons else CompatibilityStatus.COMPATIBLE
        affected = self.affected_subgraph(proposed, list(set(proposal.affected_components) | {proposal.target_component}))
        if checks["required_capabilities"]:
            self._event(EventType.CAPABILITY_REGRESSION_DETECTED, {"proposal_id": proposal.proposal_id, "missing_capabilities": checks["required_capabilities"]})
        if reasons and not checks["required_capabilities"]:
            self._event(EventType.STRUCTURAL_REGRESSION_DETECTED, {"proposal_id": proposal.proposal_id, "reasons": reasons})
        result = CompatibilityResult(status, checks, reasons, affected)
        self._event(EventType.COMPATIBILITY_CHECKED, {"proposal_id": proposal.proposal_id, **result.to_dict()})
        return result

    def approve_proposal(self, proposal_id: str, reason: str) -> MetamorphosisProposal:
        proposal = self.get_proposal(proposal_id)
        if not proposal:
            raise KeyError(proposal_id)
        if proposal.status not in {MetamorphosisStatus.PROPOSED, MetamorphosisStatus.VALIDATED}:
            raise PermissionError(f"Cannot approve metamorphosis proposal in state {proposal.status.value}")
        valid, errors = self.validate_proposal(proposal)
        if not valid:
            raise PermissionError("Metamorphosis proposal is not valid: " + "; ".join(errors))
        proposal.status = MetamorphosisStatus.APPROVED
        proposal.approval_reason = reason
        self.store.save_metamorphosis_proposal(proposal)
        self._event(EventType.METAMORPHOSIS_APPROVED, {"proposal_id": proposal_id, "reason": reason})
        return proposal

    def reject_proposal(self, proposal_id: str, reason: str) -> MetamorphosisProposal:
        proposal = self.get_proposal(proposal_id)
        if not proposal:
            raise KeyError(proposal_id)
        if proposal.status in {MetamorphosisStatus.BETTER, MetamorphosisStatus.PROMOTED, MetamorphosisStatus.ROLLED_BACK}:
            raise PermissionError(f"Cannot reject metamorphosis proposal in state {proposal.status.value}")
        proposal.status = MetamorphosisStatus.REJECTED
        proposal.approval_reason = reason
        self.store.save_metamorphosis_proposal(proposal)
        self._event(EventType.METAMORPHOSIS_REJECTED, {"proposal_id": proposal_id, "reason": reason})
        return proposal

    def create_structural_candidate(self, proposal_id: str, retain_sandbox: bool = True) -> MetamorphosisExperiment:
        proposal = self.get_proposal(proposal_id)
        if not proposal:
            raise KeyError(proposal_id)
        if proposal.status is not MetamorphosisStatus.APPROVED:
            raise PermissionError("Metamorphosis approval is required before structural sandboxing")
        compatibility = self.check_compatibility(proposal)
        if compatibility.status is not CompatibilityStatus.COMPATIBLE:
            proposal.status = MetamorphosisStatus.INCOMPATIBLE
            self.store.save_metamorphosis_proposal(proposal)
            raise PermissionError("Structural proposal is incompatible: " + "; ".join(compatibility.reasons))
        bridge_id = f"metamorphosis-bridge:{proposal.proposal_id}"
        bridge = EvolutionProposal(bridge_id, proposal.created_at, [], [], proposal.source_version, "planning configuration", "Governed structural bridge for metamorphosis sandbox", [{"metamorphosis_proposal_id": proposal.proposal_id}], "manifest configuration candidate only; no source rewrite", proposal.expected_benefit, proposal.risks, proposal.affected_components, [], 0.9, "Phase 6 benchmark plus structural compatibility checks", "Use the original architecture and Phase 7 native rollback", ProposalStatus.APPROVED, ProposalRisk.LOW if proposal.risk_class == "low" else ProposalRisk.MEDIUM, self.METAMORPHOSIS_VERSION)
        self.store.save_proposal(bridge)
        sandbox = SandboxEngine(self.store, self.source_root, self.source_root.parent / ".evo-sandboxes-metamorphosis")
        experiment = sandbox.run_experiment(bridge_id, retain_sandbox=retain_sandbox)
        proposal.status = MetamorphosisStatus.SANDBOXED if experiment.status.value == "passed" else MetamorphosisStatus.REJECTED
        self.store.save_metamorphosis_proposal(proposal)
        baseline_path = Path(experiment.sandbox_location) / "baseline"
        candidate_path = Path(experiment.sandbox_location) / "candidate"
        architecture_file = "architecture.json"
        SandboxEngine._make_writable(baseline_path)
        (baseline_path / architecture_file).write_text(json.dumps(proposal.current_architecture, indent=2), encoding="utf-8")
        SandboxEngine._make_readonly(baseline_path)
        (candidate_path / architecture_file).write_text(json.dumps(proposal.proposed_architecture, indent=2), encoding="utf-8")
        metamorphosis_experiment = MetamorphosisExperiment(self._new_id("meta-experiment"), proposal_id, str(baseline_path / architecture_file), str(candidate_path / architecture_file), compatibility.status, None, MetamorphosisStatus.SANDBOXED if experiment.status.value == "passed" else MetamorphosisStatus.INCONCLUSIVE, sandbox_experiment_id=experiment.experiment_id, candidate_version=experiment.candidate_version, errors=experiment.errors)
        self.store.save_metamorphosis_experiment(metamorphosis_experiment)
        self._event(EventType.STRUCTURAL_CANDIDATE_CREATED, {"proposal_id": proposal_id, "experiment_id": metamorphosis_experiment.experiment_id, "sandbox_experiment_id": experiment.experiment_id, "candidate_architecture": metamorphosis_experiment.candidate_architecture})
        self._event(EventType.STRUCTURAL_CANDIDATE_TESTED, {"proposal_id": proposal_id, "experiment_id": metamorphosis_experiment.experiment_id, "sandbox_status": experiment.status.value, "production_immutable": not any("immutability" in error for error in experiment.errors)})
        return metamorphosis_experiment

    def benchmark_structural_candidate(self, experiment_id: str) -> Any:
        record = self.store.metamorphosis_experiment_by_id(experiment_id)
        if not record:
            raise KeyError(experiment_id)
        experiment = self._meta_experiment_from_row(record)
        meta_proposal = self.get_proposal(experiment.proposal_id)
        if not meta_proposal or meta_proposal.status is not MetamorphosisStatus.SANDBOXED:
            raise PermissionError("Structural candidate is not sandboxed")
        if not experiment.sandbox_experiment_id:
            raise ValueError("No reusable sandbox experiment")
        benchmark = BenchmarkEngine(self.store, self.source_root)
        benchmark_record = benchmark.default_benchmark()
        benchmark.save_benchmark(benchmark_record)
        evidence = benchmark.run(benchmark_record.benchmark_id, experiment.sandbox_experiment_id)
        experiment.benchmark_evidence_id = evidence.evidence_id
        experiment.status = MetamorphosisStatus.BETTER if evidence.decision.value == "better" else MetamorphosisStatus.WORSE if evidence.decision.value == "worse" else MetamorphosisStatus.INCONCLUSIVE
        self.store.save_metamorphosis_experiment(experiment)
        meta_proposal.status = MetamorphosisStatus.BETTER if experiment.status is MetamorphosisStatus.BETTER else experiment.status
        self.store.save_metamorphosis_proposal(meta_proposal)
        self._event(EventType.METAMORPHOSIS_EVALUATED, {"proposal_id": meta_proposal.proposal_id, "experiment_id": experiment_id, "evidence_id": evidence.evidence_id, "decision": evidence.decision.value})
        return evidence

    def handoff_to_promotion(self, experiment_id: str, evidence_id: str, promotion: PromotionEngine) -> Any:
        record = self.store.metamorphosis_experiment_by_id(experiment_id)
        if not record:
            raise KeyError(experiment_id)
        experiment = self._meta_experiment_from_row(record)
        if experiment.status is not MetamorphosisStatus.BETTER or experiment.benchmark_evidence_id != evidence_id:
            raise PermissionError("Only a structurally evaluated BETTER candidate may enter Phase 7")
        meta = self.get_proposal(experiment.proposal_id)
        if not meta or meta.status is not MetamorphosisStatus.BETTER:
            raise PermissionError("Metamorphosis proposal is not ready for promotion handoff")
        candidate_version = self._version_for_structural_experiment(experiment)
        promotion.register_candidate(experiment.sandbox_experiment_id or "", evidence_id, candidate_version)
        return promotion.request_promotion(candidate_version, evidence_id, "metamorphosis-handoff")

    def list_proposals(self, limit: int = 50) -> list[MetamorphosisProposal]:
        return [self._proposal_from_row(row) for row in self.store.find_metamorphosis_proposals(limit)]

    def get_proposal(self, proposal_id: str) -> MetamorphosisProposal | None:
        row = self.store.metamorphosis_proposal_by_id(proposal_id)
        return self._proposal_from_row(row) if row else None

    def list_experiments(self, limit: int = 50) -> list[MetamorphosisExperiment]:
        return [self._meta_experiment_from_row(row) for row in self.store.find_metamorphosis_experiments(limit)]

    def get_architecture(self) -> ArchitectureManifest:
        row = self.store.architecture_by_version(self.analyze_structure().architecture_version)
        return self._architecture_from_row(row) if row else self.analyze_structure()

    def list_components(self) -> list[ComponentRecord]:
        return self.components.list()

    def list_capabilities(self) -> list[CapabilityRecord]:
        return self.capabilities.list()

    def _version_for_structural_experiment(self, experiment: MetamorphosisExperiment) -> str:
        return experiment.candidate_version or ""

    def _make_manifest(self, components: list[ComponentRecord], capabilities: list[CapabilityRecord], dependencies: dict[str, list[str]], interfaces: dict[str, list[str]], protected: list[str], configuration: dict[str, Any]) -> ArchitectureManifest:
        body = {"agent_version": self.agent_version, "components": [item.to_dict() for item in components], "capabilities": [item.to_dict() for item in capabilities], "dependencies": dependencies, "interfaces": interfaces, "protected_components": protected, "configuration": configuration}
        integrity = hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()
        return ArchitectureManifest(f"architecture_{uuid.uuid4().hex[:12]}", self.agent_version, body["components"], body["capabilities"], dependencies, interfaces, protected, configuration, integrity)

    def _event(self, event_type: EventType, payload: dict[str, Any]) -> None:
        self.store.append_event(Event("metamorphosis", event_type, payload))

    @staticmethod
    def _new_id(prefix: str) -> str:
        return f"{prefix}_{uuid.uuid4().hex[:12]}"

    @staticmethod
    def _proposal_from_row(row: dict[str, Any]) -> MetamorphosisProposal:
        data = json.loads(row["payload"]) if isinstance(row.get("payload"), str) else row
        try:
            data["change_type"] = StructuralChangeType(data["change_type"])
        except ValueError:
            data["change_type"] = str(data["change_type"])
        data["status"] = MetamorphosisStatus(data["status"])
        data.setdefault("change_details", {})
        return MetamorphosisProposal(**data)

    @staticmethod
    def _meta_experiment_from_row(row: dict[str, Any]) -> MetamorphosisExperiment:
        data = json.loads(row["payload"]) if isinstance(row.get("payload"), str) else row
        data["compatibility_status"] = CompatibilityStatus(data["compatibility_status"])
        data["status"] = MetamorphosisStatus(data["status"])
        return MetamorphosisExperiment(**data)

    @staticmethod
    def _architecture_from_row(row: dict[str, Any]) -> ArchitectureManifest:
        data = json.loads(row["payload"]) if isinstance(row.get("payload"), str) else row
        return ArchitectureManifest(**data)
