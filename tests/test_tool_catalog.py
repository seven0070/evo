"""The tool catalog's two jobs: canonical names, and an honest answer about usability (07 §4, §8).

``test_tool_usability_requires_all_three`` and ``test_monotonic_hardening`` are 07 §8's P4 acceptance
names. They are kept in one file because they are the same claim seen from two sides: a capability is
what the runtime can *prove* it can do - a handler that exists, a policy that permits it, a boundary
that confines it - and nothing about a capability may move in the permissive direction without an
approval that is itself reviewable.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

from evo_agent import active_version  # noqa: E402
from evo_agent.materialization import MaterializationError, materializer_for  # noqa: E402
from evo_agent.models import RiskLevel  # noqa: E402
from evo_agent.security import SecurityPolicy  # noqa: E402
from evo_agent.sovereign import eligibility  # noqa: E402
from evo_agent.tools import (  # noqa: E402
    CANONICAL_ALIASES,
    ToolCatalog,
    ToolRegistry,
    ToolSpec,
    canonical_tool_name,
)


class _NoMediator:
    """A mediator-shaped object that cannot answer the confinement question.

    Deliberately a stub rather than a real mediator: the point is that a catalog whose authority
    cannot report isolation has no evidence to offer, and ``ToolRegistry`` would otherwise read a
    missing answer as a permission.
    """

    providers = None

    def settings(self):  # noqa: D102 - present but useless on purpose
        return None


def _mediator(tmp_path: Path, *, enforcement: str, providers: list | None = None):
    """A real :class:`ApprovalMediator`, so the confinement answer comes from production code.

    The catalog asks its mediator whether a launch would be confined. A fake that returned a
    hand-written ``(False, "…")`` would test the string, not the selection, and the failure this
    exists to prevent - the catalog and the launcher disagreeing about confinement - would be
    exactly what such a fake hides.
    """
    from evo_agent.sovereign.mediation import ApprovalMediator

    policy = SecurityPolicy(tmp_path)
    policy.sandbox_enforcement = enforcement
    return ApprovalMediator(policy, providers=providers)


def _host_only(tmp_path: Path):
    """The host provider is the only thing on the platform, and ``auto`` allows it."""
    from evo_agent.sandbox_providers.host import HostProvider

    return _mediator(tmp_path, enforcement="auto", providers=[HostProvider(permitted=True, permit_reason="no namespace support")])


def _registry(tmp_path: Path, **kwargs) -> ToolRegistry:
    return ToolRegistry(SecurityPolicy(tmp_path), **kwargs)


class TestCanonicalNames:
    def test_reviewed_aliases_resolve_and_everything_else_refuses(self):
        assert canonical_tool_name("edit", ["workspace_write"])[0] == "workspace_write"
        assert canonical_tool_name("BASH", ["shell"])[0] == "shell"
        assert canonical_tool_name("nope", ["shell"])[0] == ""
        assert canonical_tool_name("", ["shell"])[0] == ""

    def test_an_alias_may_not_invent_a_tool(self):
        canonical, reason = canonical_tool_name("edit", ["workspace_read"])
        assert canonical == ""
        assert "not registered" in reason

    def test_an_ambiguous_spelling_resolves_to_nothing(self):
        """``read`` means two different things in two different harnesses, so it means neither here."""
        table = {"workspace_read": ("read",), "workspace_list": ("read",)}
        canonical, reason = canonical_tool_name("read", ["workspace_read", "workspace_list"], table)
        assert canonical == "" and "ambiguous" in reason

    def test_no_alias_in_the_shipped_table_is_ambiguous(self):
        seen: dict[str, str] = {}
        for canonical, spellings in CANONICAL_ALIASES.items():
            for spelling in spellings:
                assert spelling not in seen, f"'{spelling}' maps to both {seen.get(spelling)} and {canonical}"
                seen[spelling] = canonical

    def test_resolution_needs_no_registry_when_the_caller_has_none(self):
        assert canonical_tool_name("write_file")[0] == "workspace_write"

    def test_catalog_resolve_labels_a_canonical_name_as_canonical(self, tmp_path: Path):
        catalog = ToolCatalog(_registry(tmp_path))
        assert catalog.resolve("shell").status == "canonical"
        assert catalog.resolve("cat").status == "alias"
        assert catalog.resolve("cat").canonical == "workspace_read"
        assert not catalog.resolve("teleport").resolved


class TestUsability:
    def test_tool_usability_requires_all_three(self, tmp_path: Path):
        """Registered, permitted, confined - and one leg is not enough (07 §4 availability row)."""
        # Nothing on the platform can build a namespace at all - the configuration this repo's own
        # sandbox tests use for "no provider".
        registry = _registry(tmp_path, mediator=_mediator(tmp_path, enforcement="strict", providers=[]))
        catalog = ToolCatalog(registry)
        # A file tool: registered and confined, gated on approval.
        write = catalog.usability("workspace_write")
        assert write.registered and write.confined and not write.permitted
        assert not write.usable and any("approval" in reason for reason in write.reasons)
        # A process tool with only the host provider available under strict enforcement.
        shell = catalog.usability("shell")
        assert shell.registered and not shell.confined
        assert not shell.usable and any("no isolation provider is usable" in reason for reason in shell.reasons)
        # A read-only file tool passes all three, and is therefore offered.
        read = catalog.usability("workspace_read")
        assert (read.registered, read.permitted, read.confined) == (True, True, True)
        assert read.usable and read.reasons == ()

    def test_a_descriptor_without_a_handler_is_not_a_capability(self, tmp_path: Path):
        registry = _registry(tmp_path)
        registry.register(ToolSpec(name="ghost", description="claims to exist", risk=RiskLevel.LOW, arguments={}, handler=None))  # type: ignore[arg-type]
        catalog = ToolCatalog(registry)
        usability = catalog.usability("ghost")
        assert not usability.registered and not usability.usable
        assert any("no handler" in reason for reason in usability.reasons)

    def test_no_mediator_means_no_confinement_claim(self, tmp_path: Path):
        """A mediator that cannot report isolation settings is not evidence of confinement."""
        registry = _registry(tmp_path)
        catalog = ToolCatalog(registry, mediator=_NoMediator())
        shell = catalog.usability("shell")
        assert not shell.confined and not shell.usable
        assert "ApprovalMediator" in " ".join(shell.reasons)
        assert registry.get("shell").handler is not None, "the tool exists; only the proof is missing"

    def test_host_only_provider_is_reported_as_unconfined(self, tmp_path: Path):
        registry = _registry(tmp_path)
        catalog = ToolCatalog(registry, mediator=_host_only(tmp_path))
        shell = catalog.usability("shell")
        assert not shell.confined and "unconfined host provider" in " ".join(shell.reasons)

    def test_offered_schemas_are_only_the_usable_tools(self, tmp_path: Path):
        catalog = ToolCatalog(_registry(tmp_path))
        names = [schema["function"]["name"] for schema in catalog.offered()]
        assert set(names) == {"workspace_list", "workspace_read"}
        assert set(schema["function"]["name"] for schema in catalog.registry.schemas()) == {
            "workspace_list",
            "workspace_read",
            "workspace_write",
            "shell",
        }

    def test_view_splits_the_two_lists_for_a_status_report(self, tmp_path: Path):
        view = ToolCatalog(_registry(tmp_path)).view()
        assert {row["name"] for row in view["usable"]} == {"workspace_list", "workspace_read"}
        assert {row["name"] for row in view["unusable"]} == {"workspace_write", "shell"}

    def test_usability_reports_reasons_even_when_usable_is_one_word(self, tmp_path: Path):
        row = ToolCatalog(_registry(tmp_path)).usability("shell").to_dict()
        assert row["usable"] is False
        assert row["reasons"], "an unusable tool with no stated reason is the report defect this fixes"


class TestMonotonicHardening:
    """E3: security-relevant fields move only in the protective direction (07 §4 E3)."""

    def test_risk_floors_may_only_rise(self, tmp_path: Path):
        registry = _registry(tmp_path)
        changes, refused = registry.plan_risk_uplift({"shell": 1})
        assert changes == {} and any("below the registered floor" in item for item in refused)
        raised, problems = registry.plan_risk_uplift({"workspace_read": 2})
        assert problems == [] and raised["workspace_read"] == {"from": "low", "to": "high"}
        # Applying the same overlay twice cannot drift, and cannot lower.
        registry.apply_risk_uplift(raised)
        again, problems_again = registry.plan_risk_uplift({"workspace_read": 2})
        assert again == {} and problems_again == []
        registry.reset_risk_floors()
        assert registry.risk_floors()["workspace_read"] == "low"

    def test_permission_sets_are_not_overlay_writable(self):
        spec = active_version.DOCUMENTS["config/tools.json"]
        assert set(spec.fields) == {"preference", "risk_floor_uplift"}
        materializer = materializer_for("tool_binding")
        problems = materializer.validate(
            {"config/tools.json": {"permissions": ["shell"], "aliases": {"evil": "shell"}}}
        )
        joined = " ".join(problems)
        assert "permissions" in joined and "aliases" in joined, problems

    def test_the_never_retry_set_may_only_grow(self, tmp_path: Path):
        from evo_agent.runtime import FailureClass, RecoveryManager

        assert active_version.IMMUTABLE_NEVER_RETRY == ("permission", "approval")
        manager = RecoveryManager(_runtime_stub())
        desired, refused = manager.plan_overlay({"never_retry": ["transient"]})
        assert refused == []
        # Removal is inexpressible: the plan is the full desired set, and the floor is always in it.
        assert {FailureClass.PERMISSION, FailureClass.APPROVAL} <= desired
        assert FailureClass.TRANSIENT in desired
        unknown, problems = manager.plan_overlay({"never_retry": ["make_it_up"]})
        assert problems and FailureClass.PERMISSION in unknown

    def test_isolation_provider_downgrade_is_refused(self):
        materializer = materializer_for("provider_config")
        assert materializer.extra_checks("config/prompts.json", {"provider": "host"}) != []
        assert materializer.extra_checks("config/prompts.json", {"provider": "unshare"}) == []

    def test_resource_caps_cannot_widen_what_a_task_may_consume(self):
        fields = active_version.DOCUMENTS["config/runtime.json"].fields["resource_limits"]
        assert "memory_bytes" not in fields.allowed and "storage_bytes" not in fields.allowed
        assert fields.value.maximum == 10_000

    def test_the_monotonic_field_list_is_reported_not_just_declared(self):
        report = eligibility.registry_report()
        assert set(report["monotonic_fields"]) == set(eligibility.MONOTONIC_FIELDS)
        assert "turn_budget" in report["monotonic_fields"]

    def test_the_pipeline_cannot_be_loosened_by_the_same_data(self):
        from evo_agent.pipeline import HEURISTIC_PARAMS, PIPELINE

        assert "turn_limit" not in HEURISTIC_PARAMS and "approval_required_for" not in HEURISTIC_PARAMS
        mandatory = {stage.name for stage in PIPELINE if stage.mandatory}
        assert {"input_sanitize", "loop_guard", "repeat_guard", "policy_filter", "RECEIPTS"} <= mandatory
        assert mandatory & {"token_budget", "compaction", "inbox"} == set()


def _runtime_stub():
    """The minimum object ``RecoveryManager`` needs to plan an overlay: a store and a queue shape.

    Kept a stub on purpose - the recovery plan is a pure read of the floor, and building a whole
    runtime to prove a set contains two members would couple this test to every runtime change.
    """

    class _Stub:
        def __init__(self) -> None:
            self.workspace = Path(".")
            self.store = None
            self.runtime_record = type("R", (), {"metadata": {}, "failure_reason": None})()
            self.limits = type("L", (), {"max_retry_count": 2})()
            self._lock = __import__("threading").RLock()

        def _emit(self, *args, **kwargs) -> None:
            return None

        def _metric(self, *args, **kwargs) -> None:
            return None

    return _Stub()


