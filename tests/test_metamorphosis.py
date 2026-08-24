from __future__ import annotations

import json
from pathlib import Path

import pytest

from evo_agent.metamorphosis import ArchitectureManifest, Capability, CapabilityRecord, Component, ComponentRecord, MetamorphosisEngine, StructuralChange
from evo_agent.models import CapabilityStatus, CompatibilityStatus, ComponentStatus, MetamorphosisStatus, StructuralChangeType
from evo_agent.promotion import PromotionEngine
from evo_agent.storage import SQLiteStore


def setup_engine(tmp_path: Path) -> tuple[MetamorphosisEngine, SQLiteStore, Path]:
    source = tmp_path / "source"
    (source / "evo_agent").mkdir(parents=True)
    for name in ("kernel.py", "security.py", "verifier.py", "storage.py", "sandbox.py"):
        (source / "evo_agent" / name).write_text("# phase 8 fixture\n", encoding="utf-8")
    store = SQLiteStore(tmp_path / "workspace" / ".evo" / "agent.sqlite3")
    return MetamorphosisEngine(store, source), store, source


def test_component_and_capability_registries_persist_and_distinguish_structure(tmp_path: Path):
    engine, store, _ = setup_engine(tmp_path)
    component = ComponentRecord("component_custom", "custom", "1.0", "service", ComponentStatus.ACTIVE, ["kernel"], ["custom.initialize"], ["custom_capability"], False, "custom.py", "hash", {})
    capability = CapabilityRecord("capability_custom", "custom_capability", "custom", "1.0", ["planning"], ["workspace_allowlist"], "low", CapabilityStatus.ACTIVE)
    engine.components.register(component)
    engine.capabilities.register(capability)
    assert any(item.component_id == component.component_id for item in engine.list_components())
    assert any(item.capability_id == capability.capability_id for item in engine.list_capabilities())
    assert store.component_by_id(component.component_id)
    assert store.capability_by_id(capability.capability_id)
    architecture = engine.get_architecture()
    assert architecture.integrity_hash
    assert store.architecture_by_version(architecture.architecture_version)


def test_manifest_integrity_and_dependency_graph_are_deterministic(tmp_path: Path):
    engine, _, _ = setup_engine(tmp_path)
    first = engine.analyze_structure()
    second = engine.analyze_structure()
    assert first.architecture_version == second.architecture_version
    assert first.integrity_hash == second.integrity_hash
    assert engine.verify_manifest_integrity(first)
    assert Component is ComponentRecord
    assert Capability is CapabilityRecord
    assert first.dependencies["promotion"] == ["benchmark", "sandbox", "rollback"]
    assert engine.affected_subgraph(first, ["tools"]) == ["flexibility", "kernel", "planner", "tools"]
    assert "verification" in first.protected_components


def test_unsupported_structural_change_is_rejected_deterministically(tmp_path: Path):
    engine, _, _ = setup_engine(tmp_path)
    change = StructuralChange("rewrite_source", "planner", ["planner"], {"arbitrary_source": "print('unsafe')"}, "Unsupported arbitrary rewrite")
    proposal = engine.generate_proposal(change, "Not permitted")
    valid, errors = engine.validate_proposal(proposal)
    assert not valid
    assert proposal.status is MetamorphosisStatus.REJECTED
    assert "unsupported structural change type" in errors


def test_protected_configuration_mutation_is_rejected(tmp_path: Path):
    engine, _, _ = setup_engine(tmp_path)
    proposal = engine.generate_proposal(StructuralChange(StructuralChangeType.CHANGE_CONFIGURATION, "planner", ["planner"], {"key": "planning.max_steps", "value": 8}, "Change planning configuration"), "Improve planning")
    proposal.proposed_architecture["configuration"]["rollback_enabled"] = False
    valid, errors = engine.validate_proposal(proposal)
    assert not valid
    assert any("protected configuration" in error for error in errors)


def test_supported_structural_proposal_is_validated_with_reversible_migration(tmp_path: Path):
    engine, store, _ = setup_engine(tmp_path)
    change = StructuralChange(StructuralChangeType.ADD_CAPABILITY, "planner", ["planner"], {"name": "structured_context", "provider_component": "planner"}, "Improve structured context handling")
    proposal = engine.generate_proposal(change, "Improve planning context without changing protected controls")
    valid, errors = engine.validate_proposal(proposal)
    assert valid, errors
    assert proposal.status is MetamorphosisStatus.VALIDATED
    assert proposal.risk_class == "low"
    assert proposal.migration_plan["reversible"] is True
    assert proposal.compatibility["status"] == "compatible"
    assert store.metamorphosis_proposal_by_id(proposal.proposal_id)


def test_protected_component_is_rejected_before_sandbox(tmp_path: Path):
    engine, store, _ = setup_engine(tmp_path)
    change = StructuralChange(StructuralChangeType.CHANGE_CONFIGURATION, "rollback authority", ["rollback"], {"key": "rollback.enabled", "value": False}, "Attempt to change rollback")
    proposal = engine.generate_proposal(change, "No benefit")
    valid, errors = engine.validate_proposal(proposal)
    assert not valid
    assert proposal.status is MetamorphosisStatus.REJECTED
    assert any("protected" in error for error in errors)
    with pytest.raises(PermissionError):
        engine.approve_proposal(proposal.proposal_id, "should not approve")
    events = {event["event_type"] for event in store.events_for_task("metamorphosis")}
    assert {"metamorphosis_proposed", "metamorphosis_rejected"}.issubset(events)


def test_capability_regression_and_dependency_failure_are_incompatible(tmp_path: Path):
    engine, _, _ = setup_engine(tmp_path)
    current = engine.analyze_structure().to_dict()
    removed = json.loads(json.dumps(current))
    removed["capabilities"] = [item for item in removed["capabilities"] if item["name"] != "filesystem"]
    proposal = engine.generate_proposal(StructuralChange(StructuralChangeType.REMOVE_CAPABILITY, "filesystem", ["tools"], {}, "Remove required filesystem capability"), "Reduce unused code")
    proposal.current_architecture = current
    proposal.proposed_architecture = removed
    compatibility = engine.check_compatibility(proposal)
    assert compatibility.status is CompatibilityStatus.INCOMPATIBLE
    assert any("required capabilities" in reason for reason in compatibility.reasons)

    bad = engine.generate_proposal(StructuralChange(StructuralChangeType.REWIRE_DEPENDENCY, "planner", ["planner"], {"dependency": "missing-component"}, "Use a missing dependency"), "Improve planning")
    assert engine.check_compatibility(bad).status is CompatibilityStatus.INCOMPATIBLE


def test_metamorphosis_approval_is_separate_and_structural_candidate_isolated(tmp_path: Path):
    engine, store, source = setup_engine(tmp_path)
    proposal = engine.generate_proposal(engine.identify_structural_opportunity("add capability for structured context"), "Improve planning context")
    valid, errors = engine.validate_proposal(proposal)
    assert valid, errors
    with pytest.raises(PermissionError):
        engine.create_structural_candidate(proposal.proposal_id)
    approved = engine.approve_proposal(proposal.proposal_id, "Authorize structural experimentation only")
    assert approved.status is MetamorphosisStatus.APPROVED
    structural = engine.create_structural_candidate(proposal.proposal_id, retain_sandbox=True)
    assert structural.compatibility_status is CompatibilityStatus.COMPATIBLE
    assert structural.status is MetamorphosisStatus.SANDBOXED
    assert Path(structural.baseline_architecture).is_file()
    assert Path(structural.candidate_architecture).is_file()
    assert Path(structural.baseline_architecture).read_text(encoding="utf-8") != Path(structural.candidate_architecture).read_text(encoding="utf-8")
    assert not (source / "architecture.json").exists()
    stored = store.metamorphosis_experiment_by_id(structural.experiment_id)
    assert stored is not None
    assert store.metamorphosis_proposal_by_id(proposal.proposal_id)


def test_structural_benchmark_reuses_phase6_and_handoff_reuses_phase7(tmp_path: Path):
    engine, store, source = setup_engine(tmp_path)
    proposal = engine.generate_proposal(engine.identify_structural_opportunity("add capability for structured context"), "Improve planning context")
    engine.validate_proposal(proposal)
    engine.approve_proposal(proposal.proposal_id, "Authorize structural experimentation")
    structural = engine.create_structural_candidate(proposal.proposal_id, retain_sandbox=True)
    evidence = engine.benchmark_structural_candidate(structural.experiment_id)
    assert evidence.decision.value == "better"
    assert structural.experiment_id
    refreshed = engine.list_experiments()[0]
    assert refreshed.status is MetamorphosisStatus.BETTER
    assert refreshed.benchmark_evidence_id == evidence.evidence_id
    promotion = PromotionEngine(store, source, tmp_path / "production-registry", health_checker=lambda path: {"healthy": True, "smoke_test": {"passed": True}})
    request = engine.handoff_to_promotion(structural.experiment_id, evidence.evidence_id, promotion)
    assert request.approval_status.value == "pending"
    assert request.eligibility_status.value == "eligible"
    assert request.status is not MetamorphosisStatus.PROMOTED


def test_structural_candidate_promotes_and_rolls_back_through_native_engine(tmp_path: Path):
    engine, store, source = setup_engine(tmp_path)
    proposal = engine.generate_proposal(engine.identify_structural_opportunity("add capability for structured context"), "Improve planning context")
    engine.validate_proposal(proposal)
    engine.approve_proposal(proposal.proposal_id, "Authorize structural experimentation")
    structural = engine.create_structural_candidate(proposal.proposal_id, retain_sandbox=True)
    evidence = engine.benchmark_structural_candidate(structural.experiment_id)
    promotion = PromotionEngine(store, source, tmp_path / "production-registry", health_checker=lambda path: {"healthy": True, "smoke_test": {"passed": True}})
    request = engine.handoff_to_promotion(structural.experiment_id, evidence.evidence_id, promotion)
    promotion.approve_promotion(request.promotion_id, "Approve separately for production")
    record = promotion.promote(request.promotion_id)
    assert record.final_status.value == "active"
    assert promotion._active_version().version_id == record.candidate_version
    rollback = promotion.rollback(record.candidate_version, "Rollback structural candidate acceptance test")
    assert rollback.status == "completed"
    assert promotion._active_version().version_id == "v0"


def test_structural_lifecycle_and_audit_lineage_are_persisted(tmp_path: Path):
    engine, store, _ = setup_engine(tmp_path)
    proposal = engine.generate_proposal(engine.identify_structural_opportunity("replace planner component"), "Improve planner component")
    engine.validate_proposal(proposal)
    assert engine.get_proposal(proposal.proposal_id).status is MetamorphosisStatus.VALIDATED
    events = [event["event_type"] for event in store.events_for_task("metamorphosis")]
    assert "architecture_analyzed" in events
    assert "metamorphosis_proposed" in events
    assert "compatibility_checked" in events
    assert "metamorphosis_validated" in events
