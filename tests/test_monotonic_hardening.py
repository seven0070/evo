"""E3, the fields P5b added: a security-relevant value may move only in the tightening direction.

`07`'s E3 line names this file, and it did not exist. Monotonic hardening was *enforced* - the risk-floor
uplift, the resource-limit merge over the shipped baseline, the ceiling clamps in
``SecurityPolicy.__post_init__`` - but it was tested from three places, none of them the one the spec pointed
at. So this file pins the direction property in one spot and covers the fields P5b introduced, which had no
prior home:

* ``agent_mode`` and ``sandbox_enforcement`` have **no overlay path at all** - the strongest form of E3,
  since a field a candidate cannot name cannot be lowered through a payload.
* the sandbox level resolves upward on garbage (an unknown name becomes ``strict``) and so does the phase
  (a mistyped mode becomes the read-only one).
* MCP output caps clamp down and MCP risk floors never come down; the ceilings are module constants, not
  configuration.
* a risk-floor uplift that would *lower* a registered floor is refused, and the comparison is against the
  registered baseline so applying the same overlay twice reads as "no change".
* the plugin inventory has no granting verb at all: tighten-only is a property of the surface, not only of
  the enforcement point.
"""

from __future__ import annotations

import dataclasses
import inspect
from pathlib import Path

import pytest

from evo_agent import mcp as mcp_module
from evo_agent.active_version import DOCUMENTS
from evo_agent.mcp import MAX_OUTPUT_BYTES_CEILING, MAX_TIMEOUT_SECONDS_CEILING, MCPRegistry, MCPServerPolicy
from evo_agent.plugins import PluginInventory
from evo_agent.security import SecurityPolicy
from evo_agent.tools import ToolCatalog, ToolRegistry


def _overlay_field_names() -> set[str]:
    names: set[str] = set()
    for spec in DOCUMENTS.values():
        names.update(spec.fields)
    return names


class TestNoWideningPath:
    def test_the_new_security_fields_are_not_overlay_writable(self) -> None:
        names = _overlay_field_names()
        for forbidden in ("agent_mode", "sandbox_enforcement", "sandbox_provider", "approval_required_for", "skill_autonomous_secrets"):
            assert forbidden not in names, f"{forbidden} is an overlay field, which E3 does not allow"

    def test_no_loadable_document_names_the_mode_or_the_policy_builder(self) -> None:
        # `loaded_by` is what turns a field into a live write path. A document that is refused may look like a
        # policy sink (it will not be read); one that *is* read may not point at the mode.
        for relpath, spec in DOCUMENTS.items():
            if spec.loadable:
                assert "modes" not in spec.loaded_by and "security" not in spec.loaded_by, relpath

    def test_the_policy_carries_clamped_values_and_reentry_is_required_to_change_them(self, tmp_path: Path) -> None:
        policy = SecurityPolicy(tmp_path, agent_mode="PLAN", sandbox_enforcement="STRICT")
        assert (policy.agent_mode, policy.sandbox_enforcement) == ("plan", "strict")
        assert dataclasses.replace(policy, agent_mode="anything").agent_mode == "plan"
        assert dataclasses.replace(policy, sandbox_enforcement="anything").sandbox_enforcement == "strict"
        # Assignment without re-running `__post_init__` is the one way to get an unnormalised value in, and
        # it is why the policy's consumers read the *attribute* through the same helper: an object built by a
        # candidate's merge would otherwise carry a mode nobody validated.
        assert policy.to_dict()["agent_mode"] == "plan" and policy.to_dict()["sandbox_enforcement"] == "strict"


class TestCeilingsAndFloors:
    def test_a_ceiling_can_only_be_cut_down(self, tmp_path: Path) -> None:
        from evo_agent.storage import SQLiteStore

        store = SQLiteStore(tmp_path / "e.db")
        policy = SecurityPolicy(tmp_path)
        registry = MCPRegistry(ToolCatalog(ToolRegistry(policy)), policy=policy, store=store)
        record, problems = registry.register(
            MCPServerPolicy(
                server="tight",
                command=("mcp-tight",),
                allowed_tools=("read_notes",),
                max_output_bytes=2048,
                timeout_seconds=4,
                approved_by="operator",
            )
        )
        assert problems == [] and record.max_output_bytes == 2048 and record.timeout_seconds == 4
        assert record.clamped == (), "a value inside the ceiling is not adjusted; only a value outside it is"
        wide, _problems = registry.register(
            MCPServerPolicy(
                server="wide",
                command=("mcp-wide",),
                allowed_tools=("read_notes",),
                max_output_bytes=MAX_OUTPUT_BYTES_CEILING * 40,
                timeout_seconds=10**6,
                approved_by="operator",
            )
        )
        assert wide.max_output_bytes == MAX_OUTPUT_BYTES_CEILING and wide.timeout_seconds == MAX_TIMEOUT_SECONDS_CEILING
        assert len(wide.clamped) == 2

    def test_the_ceilings_are_constants_not_configuration(self) -> None:
        assert MAX_OUTPUT_BYTES_CEILING == 1_048_576 and MAX_TIMEOUT_SECONDS_CEILING == 900
        source = inspect.getsource(mcp_module)
        for forbidden in ("os.environ", "getenv"):
            assert forbidden not in source, f"the MCP ceilings read their value from {forbidden}"

    def test_an_unknown_risk_level_becomes_the_most_dangerous_one(self, tmp_path: Path) -> None:
        from evo_agent.storage import SQLiteStore

        store = SQLiteStore(tmp_path / "e.db")
        policy = SecurityPolicy(tmp_path)
        registry = MCPRegistry(ToolCatalog(ToolRegistry(policy)), policy=policy, store=store)
        # Declared means are: a known level, an unknown level (the dangerous answer), an empty value (no
        # floor declared, so the tool's own registration governs), and a numeric rank into RISK_ORDER.
        cases = (("low", "low"), ("critical", "critical"), ("minimal", "critical"), ("banana", "critical"), (None, "low"), (3, "critical"))
        for requested, expected in cases:
            server = "s" + (requested if isinstance(requested, str) else {None: "none", 3: "rank3"}[requested])
            record, problems = registry.register(
                {"server": server, "command": ["prog"], "allowed_tools": ["read_notes"], "risk_floor": requested, "approved_by": "op"}
            )
            assert record is not None, (requested, problems)
            assert registry.lookup(f"mcp:{server}:read_notes").risk_floor == expected, (requested, expected)

    def test_an_approval_obligation_is_never_removed(self, tmp_path: Path) -> None:
        # `mutating_allowed` attaches the obligation to every tool on the server, and a *lower* risk floor
        # cannot detach it - which is what makes the approval column in `mcp_tools` safe to read as a grant
        # rather than as a suggestion.
        from evo_agent.storage import SQLiteStore

        store = SQLiteStore(tmp_path / "e.db")
        policy = SecurityPolicy(tmp_path)
        registry = MCPRegistry(ToolCatalog(ToolRegistry(policy)), policy=policy, store=store)
        registry.register({"server": "m", "command": ["prog"], "allowed_tools": ["read_notes"], "mutating_allowed": True, "risk_floor": "low", "approved_by": "op"})
        assert registry.lookup("mcp:m:read_notes").requires_approval is True
        registry.register({"server": "h", "command": ["prog"], "allowed_tools": ["read_notes"], "risk_floor": "high", "approved_by": "op"})
        assert registry.lookup("mcp:h:read_notes").requires_approval is True
        registry.register({"server": "l", "command": ["prog"], "allowed_tools": ["read_notes"], "risk_floor": "low", "approved_by": "op"})
        assert registry.lookup("mcp:l:read_notes").requires_approval is False, "a plain read-only server keeps no obligation"


class TestUpliftDirection:
    def test_a_lowering_uplift_is_refused_and_a_raising_one_applies(self, tmp_path: Path) -> None:
        registry = ToolRegistry(SecurityPolicy(tmp_path))
        _changes, refusals = registry.plan_risk_uplift({"shell": 1})
        assert refusals and "shell" in refusals[0], refusals
        # A rank, not a name: 1..3 indexes RISK_ORDER above LOW, and 4 is out of range and refused rather
        # than wrapped, which is the reason the leg has a numeric domain at all.
        too_wide, refused_wide = registry.plan_risk_uplift({"workspace_read": 4})
        assert refused_wide == ["workspace_read: rank 4 is outside 1..3"] and too_wide == {}
        changes_up, refused_up = registry.plan_risk_uplift({"workspace_read": 3})
        assert refused_up == [] and changes_up["workspace_read"]["to"] == "critical"
        # Applying the same overlay twice is "no change" rather than a second attempted downgrade, because the
        # comparison runs against the registered baseline and not against the current value.
        again, refused_again = registry.plan_risk_uplift({"workspace_read": 3})
        assert refused_again == [] and again["workspace_read"]["to"] == "critical"

    def test_an_unregistered_name_is_refused_rather_than_invented(self, tmp_path: Path) -> None:
        registry = ToolRegistry(SecurityPolicy(tmp_path))
        _changes, refusals = registry.plan_risk_uplift({"not_a_tool": 4})
        assert refusals == ["not_a_tool: not a registered tool"]


class TestExtensionSurfacesExposeNoGrant:
    def test_the_plugin_inventory_has_no_loosening_verb(self) -> None:
        verbs = {name for name, _member in inspect.getmembers(PluginInventory, predicate=inspect.isfunction)}
        forbidden = {
            name
            for name in verbs
            if any(marker in name for marker in ("grant", "approve", "override", "waive", "loosen", "relax", "bypass", "enable_"))
        }
        assert not forbidden, forbidden
        assert {"register", "activate", "bind", "quarantine", "retire", "dispatch_hook", "assess", "report"} <= verbs

    def test_activation_and_binding_require_an_identity_and_neither_takes_a_path(self) -> None:
        # The two names that could hide a widening. A signature that accepted a filename or a module would be
        # the code-package deferral leaking into the inventory.
        for name in ("activate", "bind"):
            parameters = inspect.signature(getattr(PluginInventory, name)).parameters
            assert "approved_by" in parameters, name
            assert not {"path", "module", "entry_point", "source"} & set(parameters), parameters
        source = Path(inspect.getmodule(PluginInventory).__file__).read_text(encoding="utf-8")
        assert "importlib" not in source and "__import__" not in source

    def test_delegation_depth_has_no_setter_and_zero_is_a_tighter_answer(self) -> None:
        from evo_agent.specialist import SpecialistDelegationEngine, SpecialistLimits

        assert not hasattr(SpecialistDelegationEngine, "set_limits")
        assert SpecialistLimits().max_delegation_depth == 1
        # Not clamped upward: a deployment that wants no delegation at all is allowed to say so, and the
        # refusal then applies to the top-level call as well.
        assert SpecialistLimits(max_delegation_depth=0).max_delegation_depth == 0

    @pytest.mark.parametrize("level", ["auto", "strict", "degrade", "off"])
    def test_the_sandbox_level_is_a_closed_set(self, tmp_path: Path, level: str) -> None:
        assert SecurityPolicy(tmp_path, sandbox_enforcement=level).sandbox_enforcement == level
        # And the report a reviewer reads carries the same normalised value rather than the raw input.
        assert SecurityPolicy(tmp_path, sandbox_enforcement=level.upper()).to_dict()["sandbox_enforcement"] == level
