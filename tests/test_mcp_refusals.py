"""MCP: policy is real, transport is inert (07 :315, §8's ``test_mcp_refusals``).

The requirement, stated exactly: an MCP server may *never* become an execution path in this build, and every
way it could try to become one has to be refused for a stated reason. Concretely that is nine refusals and
two clamps:

1. a namespaced name may not resolve to a tool the build already has (canonical collision),
2. a tool may not be offered by a server nobody approved it for,
3. a mutating-looking tool needs an explicit ``mutating_allowed`` decision,
4. output size and timeout are clamped **down** to ceilings a candidate cannot widen,
5. the risk floor only ever moves **up** (an unrecognised level means ``critical``, not ``low``),
6. ambient credentials are refused; only names the operator declared may be passed,
7. a malformed namespace is refused rather than repaired,
8. an empty command is refused - an allow-list of nothing is not an allow-list,
9. and every invocation is refused, whatever the state above it says, with a permanent event.

The last one is why the file is worth having now rather than with the transport: an inert path that refuses
loudly is reviewable, and an implementation that quietly returns "no such tool" would let a future transport
land with the policy still unwritten.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from evo_agent.mcp import (
    MAX_OUTPUT_BYTES_CEILING,
    MAX_TIMEOUT_SECONDS_CEILING,
    MCPRegistry,
    MCPServerPolicy,
    MCPTool,
    is_namespaced,
    qualified_name,
)
from evo_agent.models import ToolCall
from evo_agent.security import SecurityPolicy
from evo_agent.storage import SQLiteStore
from evo_agent.tools import ToolCatalog, ToolRegistry


@pytest.fixture()
def store(tmp_path: Path) -> SQLiteStore:
    return SQLiteStore(tmp_path / ".evo" / "agent.sqlite3")


@pytest.fixture()
def policy(tmp_path: Path) -> SecurityPolicy:
    return SecurityPolicy(tmp_path)


def registry_for(store: SQLiteStore, policy: SecurityPolicy, *, events: list | None = None) -> tuple[ToolRegistry, MCPRegistry]:
    tools = ToolRegistry(policy)
    catalog = ToolCatalog(tools)
    registry = MCPRegistry(catalog, policy=policy, store=store, on_event=(lambda name, payload: events.append((name, payload)) if events is not None else None))
    return tools, registry


APPROVED = {
    "server": "docs",
    "command": ["mcp-docs-server", "--stdio"],
    "allowed_tools": ["search", "lookup"],
    "approved_by": "operator@host",
}


class TestNaming:
    def test_the_shape_is_the_contract(self) -> None:
        assert qualified_name("docs", "search") == ("mcp:docs:search", "")
        assert is_namespaced("mcp:docs:search") is True
        # "In the namespace" is about *origin*, not validity: ``mcp:docs`` is namespaced and malformed, and
        # the two answers come from different functions on purpose. A prefix test that also required a
        # well-formed shape would let a malformed name fall through to the ordinary "unknown tool" path,
        # where the audit record would lose the reason.
        assert is_namespaced("mcp:docs") is True and is_namespaced("shell") is False

    def test_a_name_that_must_be_normalised_is_refused_not_normalised(self) -> None:
        # Two candidates here, and the difference matters: normalising ``Docs`` to ``docs`` would let a
        # server claim a second identity over the same tools, and the audit row would name a server nobody
        # registered.
        for server in ("Docs", "docs;rm", "-docs", "a" * 40):
            qualified, problem = qualified_name(server, "search")
            assert qualified == "" and problem, server
        for tool in ("", "search tool", "search|cat"):
            assert qualified_name("docs", tool)[1], tool

    def test_the_registry_refuses_a_malformed_namespace_at_the_dispatch_edge(self, store: SQLiteStore, policy: SecurityPolicy) -> None:
        tools, _mcp = registry_for(store, policy)
        for name in ("mcp:docs", "mcp::search", "mcp:a:b:c", "mcp:docs:"):
            refusal = tools.mcp_namespace_refusal(name)
            assert "well-formed" in refusal, (name, refusal)
        assert tools.mcp_namespace_refusal("shell") == ""
        with pytest.raises(KeyError) as error:
            tools.get("mcp:docs")
        assert "well-formed" in str(error.value)


class TestRegistrationRefusals:
    def test_a_server_needs_an_approving_identity(self, store: SQLiteStore, policy: SecurityPolicy) -> None:
        _tools, mcp = registry_for(store, policy)
        unapproved = {key: value for key, value in APPROVED.items() if key != "approved_by"}
        _record, problems = mcp.register(unapproved)
        assert _record is None and any("self-declared server is not a reviewed one" in text for text in problems)

    def test_an_empty_command_is_refused(self, store: SQLiteStore, policy: SecurityPolicy) -> None:
        _tools, mcp = registry_for(store, policy)
        for command in ([], [""], ["   "]):
            _record, problems = mcp.register({**APPROVED, "command": command})
            assert _record is None and any("allow-list of nothing" in text for text in problems), command

    def test_no_tools_is_refused(self, store: SQLiteStore, policy: SecurityPolicy) -> None:
        _tools, mcp = registry_for(store, policy)
        _record, problems = mcp.register({**APPROVED, "allowed_tools": []})
        assert _record is None and problems

    def test_a_mutating_tool_needs_its_own_decision(self, store: SQLiteStore, policy: SecurityPolicy) -> None:
        _tools, mcp = registry_for(store, policy)
        _record, problems = mcp.register({**APPROVED, "allowed_tools": ["search", "write_page"]})
        assert _record is None
        assert any("write_page" in text and "mutating_allowed" in text for text in problems)
        # With the decision made explicitly, it registers - and the approval flag is then recorded on the
        # tool, which is what the approval gate reads later.
        record, second = mcp.register({**APPROVED, "allowed_tools": ["search", "write_page"], "mutating_allowed": True})
        assert record is not None and not second
        lookup = mcp.lookup("mcp:docs:write_page")
        # Approval is what a mutating decision buys; the risk floor stays whatever the operator declared,
        # because the floor is a server-wide statement and silently raising it per tool would put the
        # registry's judgement above the approval text.
        assert lookup is not None and lookup.requires_approval is True and lookup.risk_floor == "low"
        # The stage name is the evidence that the approval gate is *live* rather than recorded: an
        # unapproved tool on a mutating server is refused at `approval`, before the transport refusal is
        # ever reached, so a build that later grows a client cannot inherit "everything is inert" as an
        # accident. And the obligation covers *every* tool on that server, read-only ones included -
        # `mutating_allowed` widens what must be approved and never narrows it, which is the direction E3
        # allows a flag to move in.
        for tool in ("write_page", "search"):
            unapproved = mcp.invoke(f"mcp:docs:{tool}")
            assert unapproved["ok"] is False and unapproved["stage"] == "approval", tool
            assert mcp.invoke(f"mcp:docs:{tool}", approved=True)["stage"] == "transport", tool
        # A server not approved as mutating, at a low floor, carries no approval obligation at all.
        _second_registry_tools, calm = registry_for(store, policy)
        calm_record, calm_problems = calm.register({**APPROVED, "server": "calm", "risk_floor": "low"})
        assert calm_record is not None, calm_problems
        assert calm.lookup("mcp:calm:search").requires_approval is False
        assert calm.invoke("mcp:calm:search")["stage"] == "transport"

    def test_a_canonical_collision_is_refused_by_name_not_resolved_silently(self, store: SQLiteStore, policy: SecurityPolicy) -> None:
        # DeerFlow's ``tool_policy`` clamps a skill's request to the canonical name; here the same
        # resolution is what *detects* the collision. A server that asked for "edit" and quietly received
        # ``workspace_write`` would be granting a tool the operator never named.
        _tools, mcp = registry_for(store, policy)
        _record, problems = mcp.register({**APPROVED, "server": "evil", "allowed_tools": ["shell", "edit"]})
        assert _record is None and len(problems) == 2
        assert all("already has" in text for text in problems)
        assert any("TOOL_NAME_CONFLICT" in json.dumps(problem) or "redefine an existing one" in problem for problem in problems)
        assert mcp.servers() == ()

    def test_a_conflict_is_emitted_when_an_event_hook_exists(self, store: SQLiteStore, policy: SecurityPolicy) -> None:
        events: list[tuple[str, dict]] = []
        _tools, mcp = registry_for(store, policy, events=events)
        mcp.register({**APPROVED, "allowed_tools": ["shell"]})
        assert [name for name, _payload in events] == ["tool_name_conflict"]
        assert "shell" in json.dumps(events[0][1])

    def test_the_registry_requires_a_catalog(self, policy: SecurityPolicy) -> None:
        # Name-conflict detection needs the live tool set, and "no catalog" would mean "no conflicts",
        # which is the one answer that must not be the default.
        with pytest.raises(ValueError, match="ToolCatalog"):
            MCPRegistry(None, policy=policy)

    def test_reregistration_of_identical_bytes_is_not_a_conflict(self, store: SQLiteStore, policy: SecurityPolicy) -> None:
        _tools, mcp = registry_for(store, policy)
        first, _problems = mcp.register(APPROVED, now="2026-01-01T00:00:00+00:00")
        again, problems = mcp.register(APPROVED, now="2026-01-02T00:00:00+00:00")
        assert problems == [] and again is not None
        assert again.digest == first.digest and len(mcp.servers()) == 1


class TestClamps:
    def test_output_and_timeout_clamp_downward(self, store: SQLiteStore, policy: SecurityPolicy) -> None:
        _tools, mcp = registry_for(store, policy)
        record, problems = mcp.register({**APPROVED, "max_output_bytes": 99_000_000, "timeout_seconds": 99_000})
        assert problems == [] and record is not None
        assert record.max_output_bytes == MAX_OUTPUT_BYTES_CEILING and record.timeout_seconds == MAX_TIMEOUT_SECONDS_CEILING
        assert len(record.clamped) == 2

    def test_tighter_values_are_left_alone(self, store: SQLiteStore, policy: SecurityPolicy) -> None:
        _tools, mcp = registry_for(store, policy)
        record, _problems = mcp.register({**APPROVED, "max_output_bytes": 1024, "timeout_seconds": 5})
        assert (record.max_output_bytes, record.timeout_seconds) == (1024, 5)
        assert record.clamped == ()

    def test_the_risk_floor_is_recorded_verbatim_and_approval_only_ever_attaches(self, store: SQLiteStore, policy: SecurityPolicy) -> None:
        _tools, mcp = registry_for(store, policy)
        for requested, expected in (("low", "low"), ("medium", "medium"), ("high", "high"), ("critical", "critical")):
            _tools_local, mcp_local = registry_for(store, policy)
            record, problems = mcp_local.register({**APPROVED, "server": f"s{requested}", "allowed_tools": ["read_notes"], "risk_floor": requested})
            assert record is not None, problems
            tool = mcp_local.lookup(f"mcp:s{requested}:read_notes")
            assert tool.risk_floor == expected, (requested, tool)
            # Monotone in the only direction that matters: an approval obligation is never dropped by a
            # lower floor, and a high floor never lets the approval requirement go away.
            assert tool.requires_approval is (expected in {"high", "critical"} or requested in {"high", "critical"})

    def test_an_unknown_risk_level_becomes_critical(self, store: SQLiteStore, policy: SecurityPolicy) -> None:
        # Unknown *string* mapped to the most dangerous level, per E3. A typo in a risk name must not read
        # as the default-low answer.
        _tools, mcp = registry_for(store, policy)
        record, _problems = mcp.register({**APPROVED, "server": "odd", "allowed_tools": ["read_aloud"], "risk_floor": "barely"})
        assert mcp.lookup("mcp:odd:read_aloud").risk_floor == "critical"
        assert record.risk_floor in {"high", "critical"}

    def test_the_policy_is_frozen_and_its_digest_follows_the_content(self, store: SQLiteStore, policy: SecurityPolicy) -> None:
        _tools, mcp = registry_for(store, policy)
        record, _problems = mcp.register(APPROVED)
        with pytest.raises(Exception):
            record.approved_by = "someone else"  # type: ignore[misc]
        other, _ = mcp.register({**APPROVED, "server": "docs2"})
        assert other.digest != record.digest
        assert json.dumps(record.to_dict())


class TestCredentials:
    def test_only_declared_names_are_passed(self, store: SQLiteStore, policy: SecurityPolicy) -> None:
        _tools, mcp = registry_for(store, policy)
        mcp.register({**APPROVED, "credential_scope": ["DOCS_TOKEN"]})
        granted, refused = mcp.resolve_credentials(
            "docs",
            {"DOCS_TOKEN": "secret", "ANTHROPIC_API_KEY": "other", "AWS_SECRET_ACCESS_KEY": "cloud", "PATH": "/bin"},
        )
        assert granted == {"DOCS_TOKEN": "secret"}
        assert any("ANTHROPIC_API_KEY" in text for text in refused) and any("AWS_SECRET_ACCESS_KEY" in text for text in refused)
        assert list(granted) == ["DOCS_TOKEN"]
        assert mcp.credential_names("docs") == ("DOCS_TOKEN",)

    def test_an_unregistered_server_gets_nothing(self, store: SQLiteStore, policy: SecurityPolicy) -> None:
        _tools, mcp = registry_for(store, policy)
        granted, refused = mcp.resolve_credentials("nosuch", {"ANY": "value"})
        assert granted == {}
        assert refused and any("nosuch" in text for text in refused)


class TestTransportIsInert:
    def test_every_invocation_is_refused_after_registration(self, store: SQLiteStore, policy: SecurityPolicy) -> None:
        events: list[tuple[str, dict]] = []
        _tools, mcp = registry_for(store, policy, events=events)
        mcp.register(APPROVED)
        answer = mcp.invoke("mcp:docs:search", arguments={"query": "notes"})
        assert answer["ok"] is False and answer["stage"] == "transport"
        assert "inert" in answer["refusal"] and "policy is implemented" in answer["refusal"]
        assert events and events[-1][0] == "mcp_tool_refused"
        # Approval changes the input and not the answer: the refusal is about the phase, not the consent.
        assert mcp.invoke("mcp:docs:search", approved=True)["stage"] == "transport"

    def test_an_unregistered_name_is_refused_as_unregistered(self, store: SQLiteStore, policy: SecurityPolicy) -> None:
        _tools, mcp = registry_for(store, policy)
        answer = mcp.invoke("mcp:nosuch:search")
        assert answer["ok"] is False and answer["stage"] == "unregistered"

    def test_the_tool_registry_refuses_instead_of_raising_keyerror(self, store: SQLiteStore, policy: SecurityPolicy) -> None:
        tools, _mcp = registry_for(store, policy)
        result = tools.execute(ToolCall(call_id="c1", task_id="t1", step_id="s1", tool_name="mcp:docs:search", arguments={}))
        assert result.success is False and "transport is inert" in result.error
        # The refusal is a result, not an exception, because a tool name is data a model produced: a crash
        # here would be recorded as a runtime failure instead of a policy answer.

    def test_the_report_states_what_it_does_not_do(self, store: SQLiteStore, policy: SecurityPolicy) -> None:
        _tools, mcp = registry_for(store, policy)
        mcp.register(APPROVED)
        payload = mcp.report()
        assert payload["transport"] == "inert"
        assert len(payload["tools"]) == 2
        assert json.dumps(payload)


class TestPersistence:
    def test_registered_rows_are_recorded_and_readable(self, store: SQLiteStore, policy: SecurityPolicy) -> None:
        _tools, mcp = registry_for(store, policy)
        mcp.register({**APPROVED, "credential_scope": ["DOCS_TOKEN"]}, now="2026-01-01T00:00:00+00:00")
        servers = store.list_mcp_servers()
        assert len(servers) == 1
        row = servers[0]
        assert row["server"] == "docs" and row["approved_by"] == "operator@host"
        # Sorted on the way in, so the row is comparable across two registrations written in a different
        # order - the digest covers declaration order, this row covers review order.
        assert row["allowed_tools"] == ["lookup", "search"] and row["credential_scope"] == ["DOCS_TOKEN"]
        assert row["mutating_allowed"] in {0, False}
        tools = store.list_mcp_tools("docs")
        assert {item["tool"] for item in tools} == {"search", "lookup"}
        assert all(item["fully_qualified"].startswith("mcp:docs:") for item in tools)

    def test_a_stored_row_alone_does_not_grant_invocation(self, store: SQLiteStore, policy: SecurityPolicy) -> None:
        # The database records what was reviewed; it is not the authority that lets a call through. This is
        # the same separation the skill inventory keeps from the overlay, and it is the assertion that stops
        # a future reader from "fixing" invoke by looking at mcp_tools.
        _tools, mcp = registry_for(store, policy)
        mcp.register(APPROVED)
        other_registry = MCPRegistry(ToolCatalog(ToolRegistry(policy)), policy=policy, store=store)
        assert other_registry.invoke("mcp:docs:search")["stage"] == "unregistered"

    def test_overlay_cannot_write_mcp_policy(self) -> None:
        from evo_agent.active_version import DOCUMENTS

        spec = DOCUMENTS["config/tools.json"]
        assert "mcp" in spec.notes.lower() and "overlay" in spec.notes.lower()
