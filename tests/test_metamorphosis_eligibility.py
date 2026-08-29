"""P0 ratchet: what Evolutionary Metamorphosis may legally change (07 §4).

The registry is declarative on purpose - the enforcement that consumes it arrives with
materialization. What is enforced today is that the table is honest: every promotable kind
has a benchmark that could detect its regression, no protected authority is a target, and
nothing pretends a configuration payload is loaded when nothing loads it yet.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from evo_agent.sovereign.eligibility import (
    ELIGIBILITY_VERSION,
    FORBIDDEN_PAYLOADS,
    MONOTONIC_FIELDS,
    PROTECTED_COMPONENTS,
    TARGET_KINDS,
    consistency_with_sandbox,
    eligible_target_kinds,
    is_protected,
    protected_components,
    registry_report,
    validate_registry,
)


def test_registry_is_self_consistent():
    assert validate_registry() == []


def test_registry_agrees_with_the_sandbox_engine():
    assert consistency_with_sandbox() == []


def test_every_target_kind_declares_a_benchmark_suite():
    """R10: promotable implies benchmarkable, or it is not promotable."""
    for kind in TARGET_KINDS:
        assert kind.benchmark_suites, f"{kind.name} is a target with no way to detect its regression"


def test_a_loadable_claim_names_a_document_the_runtime_reads():
    """The P0 version of this test asserted *nothing* was loadable (00 §B.3). P3 made it a check.

    A blanket "false" was the honest state before materialization existed; keeping it would now be a
    lie in the other direction, so the guard became a correspondence test instead: every ``loadable``
    claim must name a kind that has (a) a document whose spec declares a consumer and (b) a
    materializer that will write it. That is what stops the flag from being re-set to ``True`` for a
    document nothing reads - the exact state this repository spent three phases removing.
    """
    from evo_agent.active_version import DOCUMENTS, TARGET_TO_KIND
    from evo_agent.materialization import materializer_for

    loaded_kinds = {spec.kind for spec in DOCUMENTS.values() if spec.loadable}
    claimed = {TARGET_TO_KIND.get(kind.name, kind.name) for kind in TARGET_KINDS if kind.loadable}
    assert claimed, "P3 exists to make promotion causal; nothing claiming to be loaded is a regression"
    assert claimed <= loaded_kinds, f"claimed loadable without a loader: {sorted(claimed - loaded_kinds)}"
    for kind in TARGET_KINDS:
        if not kind.loadable:
            continue
        mapped = TARGET_TO_KIND.get(kind.name, kind.name)
        materializer = materializer_for(mapped)
        assert materializer is not None, f"{kind.name} is loadable but no materializer owns it"
        assert any(DOCUMENTS[path].loadable for path in materializer.owned_documents if path in DOCUMENTS), (
            f"{kind.name} is loadable but every document its materializer owns is refused as unloaded"
        )


def test_the_unloaded_kinds_say_which_phase_loads_them():
    """A ``False`` with no phase attached is a permanent excuse, so the registry may not carry one."""
    for kind in TARGET_KINDS:
        if kind.loadable:
            continue
        assert kind.phase, f"{kind.name} is not loadable and names no phase to fix it"
        assert kind.notes, f"{kind.name} is not loadable and states no reason"


def test_source_code_is_not_a_target_and_says_so():
    assert "source_code" in FORBIDDEN_PAYLOADS and "generated_code" in FORBIDDEN_PAYLOADS
    for kind in TARGET_KINDS:
        assert kind.payload not in FORBIDDEN_PAYLOADS
        assert "source" not in kind.name and "code" not in kind.name


@pytest.mark.parametrize(
    "name",
    ["governance", "permission enforcement", "approval authority", "sandbox isolation",
     "verification authority", "rollback authority", "audit integrity", "kill switch",
     "trust boundary", "promotion authorization", "memory contents", "agent loop control flow"],
)
def test_protected_authorities_are_protected(name: str):
    assert is_protected(name)
    assert name not in {kind.name for kind in TARGET_KINDS}, f"{name} may never be an eligible target"


def test_protection_rejects_rewording_not_only_exact_names():
    assert is_protected("tune sandbox isolation"), "substring match is what stops 'sandbox isolation tuning'"
    assert is_protected("Verification Authority")


def test_memory_contents_are_never_a_target_but_memory_policy_is():
    assert is_protected("memory contents")
    assert "memory_policy" in {kind.name for kind in TARGET_KINDS}
    policy = next(kind for kind in TARGET_KINDS if kind.name == "memory_policy")
    assert "contents" not in policy.payload


def test_every_protected_component_states_a_reason_and_a_mechanism():
    for component in protected_components():
        assert len(component.reason.strip()) > 20, f"{component.name}: reason too thin to review"
        assert component.enforced_by, f"{component.name}: protected by nothing"
        assert component.owner_module, f"{component.name}: no owner to audit"


def test_monotonic_fields_are_named_so_hardening_can_be_checked():
    assert {"max_command_seconds", "turn_budget", "cooldown_hours"} <= MONOTONIC_FIELDS


def test_eligible_kinds_are_a_view_of_the_registry():
    kinds = eligible_target_kinds()
    assert kinds and len(kinds) == len(TARGET_KINDS)
    loadable = eligible_target_kinds(loadable_only=True)
    assert loadable, "P3: the spine is causal for these kinds, so the view must show them"
    assert {kind.name for kind in loadable} == {kind.name for kind in TARGET_KINDS if kind.loadable}


def test_report_is_json_serialisable_and_versioned():
    payload = registry_report()
    round_tripped = json.loads(json.dumps(payload))
    assert round_tripped["eligibility_version"] == ELIGIBILITY_VERSION
    assert round_tripped["defects"] == []
    assert set(round_tripped["sandbox_accepted_target_kinds"]) <= {kind.name for kind in TARGET_KINDS}
