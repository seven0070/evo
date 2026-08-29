"""MCP: the policy half, because the transport half is what makes it dangerous (07 §5, §8 P5).

The ordering in `07` §8 is explicit - "MCP policy before any transport", and "`ToolCatalog` before MCP
registration (otherwise name conflicts cannot be arbitrated)" - and this module is that ordering honoured:
every rule a server must satisfy is decided *here, before a connection exists*, so the day a transport is
added it inherits a gate rather than growing one.

What is enforced:

* **Names.** An MCP tool is addressed ``mcp:<server>:<tool>``. A registration that resolves - after
  canonicalisation and aliasing - to a tool the registry already has is refused with
  ``TOOL_NAME_CONFLICT``. Prefixing is not the protection; *arbitration against the catalog* is, because a
  server that registers ``shell`` while the built-in ``shell`` exists gives the operator two tools with one
  name and no way to tell which one a model asked for.
* **Caps.** Output size and timeout are clamped *down* to the ceilings in :class:`SecurityPolicy`. A server
  asking for more gets less, and the report says what it got - the same "clamped, not validated" rule the
  policy applies to its own numbers, since refusing a registration is indistinguishable from an operator
  never having tried.
* **Risk floor.** Only upward: a server may declare its mutating tools ``critical`` and the registry will
  not talk it down. ``mutating_allowed`` defaults to false, so a write-capable server has to be approved as
  one.
* **Credentials.** A server gets the *names* it declared and nothing else: no inherited environment, no
  ambient ``PATH``-adjacent secrets, no tokens from the parent process. Refusing is cheap here because
  there is no transport yet to be inconvenienced.
* **Transport.** :meth:`MCPRegistry.invoke` refuses, unconditionally, with a reason that names the phase.
  An inert transport is a deliberate state, not a stub: it means the *policy* can be reviewed, benchmarked
  and promoted before anything on the other end of it can act.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence

NAME_PREFIX = "mcp"
_SERVER_SHAPE = re.compile(r"^[a-z][a-z0-9._-]{0,31}$")
_TOOL_SHAPE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")

#: Ceilings a registration may ask to lower and never to raise. Mirrors ``SecurityPolicy``'s own
#: constants; deliberately literal so that a policy typo cannot widen both at once.
MAX_OUTPUT_BYTES_CEILING = 1_048_576
MAX_TIMEOUT_SECONDS_CEILING = 900


def qualified_name(server: Any, tool: Any) -> tuple[str, str]:
    """``(mcp:<server>:<tool>, problem)``. One function, so two callers cannot disagree about the shape."""
    server_text = str(server or "").strip()
    tool_text = str(tool or "").strip()
    if not _SERVER_SHAPE.match(server_text):
        return "", f"server name {server_text!r} must match [a-z][a-z0-9._-]{{0,31}} and be safe inside a tool name"
    if not _TOOL_SHAPE.match(tool_text):
        return "", f"tool name {tool_text!r} must match [A-Za-z0-9][A-Za-z0-9._-]{{0,63}}"
    return f"{NAME_PREFIX}:{server_text}:{tool_text}", ""


def is_namespaced(name: Any) -> bool:
    return str(name or "").startswith(f"{NAME_PREFIX}:")


@dataclass(frozen=True)
class MCPServerPolicy:
    """One reviewed server: how it may be reached, what it may expose, and what it may touch."""

    server: str
    command: tuple[str, ...] = ()
    allowed_tools: tuple[str, ...] = ()
    max_output_bytes: int = 262_144
    timeout_seconds: int = 30
    mutating_allowed: bool = False
    credential_scope: tuple[str, ...] = ()
    risk_floor: str = "low"
    approved_by: str = ""
    #: Filled in by :meth:`MCPRegistry.register`: what the ceilings actually did, so a reviewer can see the
    #: difference between what a server asked for and what it got.
    clamped: tuple[str, ...] = ()

    @property
    def digest(self) -> str:
        payload = json.dumps(
            {
                "server": self.server,
                "command": list(self.command),
                "allowed_tools": sorted(self.allowed_tools),
                "max_output_bytes": self.max_output_bytes,
                "timeout_seconds": self.timeout_seconds,
                "mutating_allowed": self.mutating_allowed,
                "credential_scope": sorted(self.credential_scope),
                "risk_floor": self.risk_floor,
            },
            sort_keys=True,
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def to_dict(self) -> dict[str, Any]:
        return {
            "server": self.server,
            "command": list(self.command),
            "allowed_tools": list(self.allowed_tools),
            "max_output_bytes": self.max_output_bytes,
            "timeout_seconds": self.timeout_seconds,
            "mutating_allowed": self.mutating_allowed,
            "credential_scope": list(self.credential_scope),
            "risk_floor": self.risk_floor,
            "approved_by": self.approved_by,
            "clamped": list(self.clamped),
            "digest": self.digest,
        }


@dataclass(frozen=True)
class MCPTool:
    fully_qualified: str
    server: str
    tool: str
    risk_floor: str
    requires_approval: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "fully_qualified": self.fully_qualified,
            "server": self.server,
            "tool": self.tool,
            "risk_floor": self.risk_floor,
            "requires_approval": self.requires_approval,
        }


class MCPRegistry:
    """The reviewed servers, and every refusal this phase can make without a socket.

    ``catalog`` is the :class:`evo_agent.tools.ToolCatalog` the arbitrated names are checked against, and it
    is *required*: a registry built without one cannot answer "does ``shell`` already exist", and answering
    that is the entire reason registration is a separate operation from enabling. Pass the catalog, or
    don't register anything.
    """

    RISK_ORDER = ("low", "medium", "high", "critical")

    def __init__(self, catalog: Any, *, policy: Any = None, store: Any = None, on_event: Any = None) -> None:
        if catalog is None:
            raise ValueError("MCPRegistry requires a ToolCatalog; name conflicts cannot be arbitrated without one")
        self.catalog = catalog
        self.policy = policy
        self.store = store
        self.on_event = on_event
        self._servers: dict[str, MCPServerPolicy] = {}
        self._tools: dict[str, MCPTool] = {}

    # -- registration --------------------------------------------------------

    def _raise_floor(self, requested: str) -> str:
        text = str(requested or "low").strip().lower()
        if text not in self.RISK_ORDER:
            # An unknown level is treated as the most dangerous one rather than the least: "I don't
            # recognise this risk" is not evidence that a tool is safe.
            return self.RISK_ORDER[-1]
        index = self.RISK_ORDER.index(text)
        return self.RISK_ORDER[index]

    def register(self, spec: MCPServerPolicy | dict[str, Any], *, now: str = "") -> tuple[MCPServerPolicy | None, list[str]]:
        """Validate and record one server. Returns ``(policy, refusals)``; nothing is half-registered."""
        problems: list[str] = []
        if isinstance(spec, dict):
            data = dict(spec)
            spec = MCPServerPolicy(
                server=str(data.get("server") or ""),
                command=tuple(str(item) for item in (data.get("command") or ())),
                allowed_tools=tuple(str(item) for item in (data.get("allowed_tools") or ())),
                max_output_bytes=int(data.get("max_output_bytes") or 262_144),
                timeout_seconds=int(data.get("timeout_seconds") or 30),
                mutating_allowed=bool(data.get("mutating_allowed")),
                credential_scope=tuple(str(item) for item in (data.get("credential_scope") or ())),
                risk_floor=str(data.get("risk_floor") or "low"),
                approved_by=str(data.get("approved_by") or ""),
            )
        if not _SERVER_SHAPE.match(str(spec.server)):
            problems.append(f"server name {spec.server!r} must match [a-z][a-z0-9._-]{{0,31}}")
        # ``command`` is checked as *content*, not as a length: ``[""]`` and ``["   "]`` are one-element
        # tuples and therefore truthy, and a server whose program is a blank string would pass a
        # ``if not spec.command`` test while being exactly the "allow-list of nothing" the refusal exists to
        # catch. The argv the transport would one day exec is ``command + args``, so a blank program is a
        # blank exec.
        command_parts = tuple(str(item or "").strip() for item in tuple(spec.command or ()))
        if not any(command_parts):
            problems.append(f"{spec.server}: a command must be reviewed and recorded before a server may register; an allow-list of nothing is not an allow-list")
        elif not command_parts[0]:
            problems.append(f"{spec.server}: the first element of 'command' must be the program name, not a blank argument")
        if not spec.allowed_tools:
            problems.append(f"{spec.server}: no tools declared, so there is nothing to arbitrate and nothing to allow")
        if not spec.approved_by:
            problems.append(f"{spec.server}: registration requires an approving operator identity; a self-declared server is not a reviewed one")
        clamped: list[str] = []
        if int(spec.max_output_bytes) > MAX_OUTPUT_BYTES_CEILING:
            clamped.append(f"max_output_bytes {spec.max_output_bytes} -> {MAX_OUTPUT_BYTES_CEILING}")
        if int(spec.timeout_seconds) > MAX_TIMEOUT_SECONDS_CEILING:
            clamped.append(f"timeout_seconds {spec.timeout_seconds} -> {MAX_TIMEOUT_SECONDS_CEILING}")
        allowed_registry = tuple(getattr(self.catalog, "names", ()) or ())
        tools: list[MCPTool] = []
        for tool in spec.allowed_tools:
            qualified, problem = qualified_name(spec.server, tool)
            if problem:
                problems.append(f"{spec.server}: {problem}")
                continue
            # Arbitrated by resolution, not by string comparison: a server cannot shadow a canonical
            # tool by choosing a different spelling of it.
            requested_canonical, _ = self._resolve_against_catalog(tool)
            if requested_canonical:
                problems.append(
                    f"{spec.server}: tool {tool!r} resolves to the canonical tool '{requested_canonical}', which this build already has; "
                    "an MCP server may add a namespaced tool, not redefine an existing one"
                )
                self._emit("tool_name_conflict", {"server": spec.server, "requested": tool, "canonical": requested_canonical})
                continue
            if not spec.mutating_allowed and self._looks_mutating(tool):
                problems.append(
                    f"{spec.server}: {tool!r} looks mutating, and this server was not approved as mutating; "
                    "declare 'mutating_allowed' only with an operator who means it"
                )
                continue
            floor = self._raise_floor(spec.risk_floor)
            tools.append(
                MCPTool(
                    fully_qualified=qualified,
                    server=spec.server,
                    tool=tool,
                    risk_floor=floor,
                    requires_approval=spec.mutating_allowed or floor in {"high", "critical"},
                )
            )
        if problems:
            return None, problems
        from dataclasses import replace as _replace

        recorded = _replace(
            spec,
            max_output_bytes=min(int(spec.max_output_bytes), MAX_OUTPUT_BYTES_CEILING),
            timeout_seconds=min(int(spec.timeout_seconds), MAX_TIMEOUT_SECONDS_CEILING),
            risk_floor=self._raise_floor(spec.risk_floor),
            clamped=tuple(clamped),
        )
        self._servers[recorded.server] = recorded
        for tool in tools:
            self._tools[tool.fully_qualified] = tool
        if self.store is not None:
            self.store.record_mcp_server(
                {
                    "server": recorded.server,
                    "command": " ".join(recorded.command),
                    "allowed_tools": sorted(recorded.allowed_tools),
                    "max_output_bytes": recorded.max_output_bytes,
                    "timeout_seconds": recorded.timeout_seconds,
                    "mutating_allowed": recorded.mutating_allowed,
                    "credential_scope": sorted(recorded.credential_scope),
                    "approved_by": recorded.approved_by,
                    "registered_at": now or "recorded",
                }
            )
            for tool in tools:
                self.store.record_mcp_tool(
                    fully_qualified=tool.fully_qualified,
                    server=tool.server,
                    tool=tool.tool,
                    risk_floor=tool.risk_floor,
                    requires_approval=tool.requires_approval,
                    recorded_at=now or "recorded",
                )
        return recorded, []

    def _resolve_against_catalog(self, requested: str) -> tuple[str, str]:
        from .tools import canonical_tool_name

        return canonical_tool_name(requested, tuple(getattr(self.catalog, "names", ()) or ()))

    @staticmethod
    def _looks_mutating(tool: str) -> bool:
        text = str(tool or "").lower()
        return any(marker in text for marker in ("write", "delete", "create", "update", "patch", "send", "post", "install", "exec", "run"))

    # -- the call-time surface (there is no transport) ---------------------------------------

    def lookup(self, name: Any) -> MCPTool | None:
        return self._tools.get(str(name or ""))

    def servers(self) -> tuple[MCPServerPolicy, ...]:
        return tuple(self._servers[key] for key in sorted(self._servers))

    def tools(self) -> tuple[MCPTool, ...]:
        return tuple(self._tools[key] for key in sorted(self._tools))

    def credential_names(self, server: str) -> tuple[str, ...]:
        recorded = self._servers.get(str(server or ""))
        return tuple(recorded.credential_scope) if recorded else ()

    def resolve_credentials(self, server: str, available: dict[str, str]) -> tuple[dict[str, str], list[str]]:
        """Pick only the declared names out of the caller's environment. Nothing ambient, ever."""
        declared = set(self.credential_names(server))
        if not declared:
            return {}, [f"{server}: declares no credential scope, so it receives no environment"]
        granted = {name: str(value) for name, value in dict(available or {}).items() if name in declared}
        refused = sorted(name for name in dict(available or {}) if name not in declared)
        return granted, [f"{server}: refusing ambient variable {name!r}, which no approval declared" for name in refused]

    def invoke(self, name: Any, *, arguments: dict[str, Any] | None = None, approved: bool = False) -> dict[str, Any]:
        """Refuse, always, and say why in terms a reader can act on.

        The refusal is layered so the reason stays true as the phase advances: an unknown name is refused
        as *unregistered* (that will still be the right sentence when a transport exists), a known one is
        refused as *no transport* (that sentence disappears in P6), and an unapproved mutating call is
        refused first, so an operator who later wires a transport cannot mistake "it was inert" for
        "approval was not needed".
        """
        tool = self.lookup(name)
        if tool is None:
            problem = (
                f"'{name}' is not a registered MCP tool. A name in the '{NAME_PREFIX}:' namespace is only "
                "addressable through a reviewed registration, and no fallback to a bare spelling exists"
            )
            self._emit("mcp_tool_refused", {"tool": str(name), "reason": "unregistered"})
            return {"ok": False, "refusal": problem, "stage": "unregistered"}
        if tool.requires_approval and not approved:
            self._emit("mcp_tool_refused", {"tool": tool.fully_qualified, "reason": "approval"})
            return {
                "ok": False,
                "refusal": (
                    f"'{tool.fully_qualified}' is at risk floor '{tool.risk_floor}' and requires an approving "
                    "turn; a namespaced tool inherits the same consent rule as a built-in one"
                ),
                "stage": "approval",
            }
        self._emit("mcp_tool_refused", {"tool": tool.fully_qualified, "reason": "transport-inert"})
        return {
            "ok": False,
            "refusal": (
                f"'{tool.fully_qualified}' has no transport in this build: MCP policy is implemented and "
                "reviewed, and the client is deliberately inert (07 §8, 'policy before any transport')"
            ),
            "stage": "transport",
            "policy": (self._servers[tool.server].to_dict() if tool.server in self._servers else {}),
        }

    def _emit(self, name: str, payload: dict[str, Any]) -> None:
        emit = self.on_event
        if emit is None:
            return
        try:
            emit(name, payload)
        except Exception:  # a refused call must not be rescued by a broken logger
            return

    def report(self) -> dict[str, Any]:
        return {
            "servers": [item.to_dict() for item in self.servers()],
            "tools": [item.to_dict() for item in self.tools()],
            "transport": "inert",
            "policy_note": "names are namespaced, caps are clamped down, risk floors move only up, credentials are declared by name, and no call reaches a socket before P6",
        }


__all__ = ["MCPRegistry", "MCPServerPolicy", "MCPTool", "NAME_PREFIX", "is_namespaced", "qualified_name"]
