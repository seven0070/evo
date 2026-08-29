from __future__ import annotations

from dataclasses import dataclass
import json
import os
import shlex
from typing import Any, Callable

from .models import RiskLevel, ToolCall, ToolResult
from .ports.contracts import ExecRequest
from .security import SecurityPolicy
from .sovereign.mediation import ApprovalMediator


@dataclass
class ToolSpec:
    name: str
    description: str
    risk: RiskLevel
    arguments: dict[str, Any]
    handler: Callable[[ToolCall], ToolResult]

#: Risk ordering, declared here rather than derived from ``RiskLevel``'s declaration order: the enum is
#: a value type that other modules compare for equality, and "which level is stricter" is a policy of the
#: registry that enforces it. An overlay raising a floor must never be able to *lower* one, and that rule
#: needs one authoritative ranking rather than two that happen to agree today.
RISK_ORDER: tuple[RiskLevel, ...] = (RiskLevel.LOW, RiskLevel.MEDIUM, RiskLevel.HIGH, RiskLevel.CRITICAL)


#: Environment variables a confined command may inherit. Only non-secret navigation variables:
#: the child that needs a credential should be given one deliberately, not find the parent's.
INHERITED_ENVIRONMENT = ("PATH", "HOME", "LANG", "LC_ALL", "TMPDIR", "PYTHONPATH")


class ToolRegistry:
    def __init__(
        self,
        policy: SecurityPolicy,
        *,
        approver: Callable[[str, dict[str, Any]], bool] | None = None,
        on_event: Callable[[str, dict[str, Any]], None] | None = None,
        mediator: ApprovalMediator | None = None,
    ):
        self.policy = policy
        self._tools: dict[str, ToolSpec] = {}
        #: Every executable path goes through the mediator, which is also the only place that
        #: decides. ``registry.execute`` never spawns anything itself, so an integrated harness
        #: cannot get a weaker gate by calling a different entry point.
        self.mediator = mediator or ApprovalMediator(policy, approver=approver, on_event=on_event)
        self.register_defaults()
        #: Registration order, captured once. An overlay may reorder the tools; only this makes the
        #: reordering reversible without a restart.
        self._default_order: tuple[str, ...] = tuple(self._tools)
        #: Registration risk floors, captured once for the same reason: an overlay raises a floor and a
        #: rollback has to bring the old one back without restarting the process.
        self._default_risks: dict[str, RiskLevel] = {name: spec.risk for name, spec in self._tools.items()}

    def register(self, spec: ToolSpec) -> None:
        self._tools[spec.name] = spec

    def get(self, name: str) -> ToolSpec:
        if name not in self._tools:
            raise KeyError(f"Unknown tool: {name}")
        return self._tools[name]

    def risk_floors(self) -> dict[str, str]:
        """The current per-tool risk floor, as names - the view an overlay's effect is reported from."""
        return {name: spec.risk.value for name, spec in self._tools.items()}

    def plan_risk_uplift(self, uplift: dict[str, int] | None) -> tuple[dict[str, dict[str, str]], list[str]]:
        """Decide what each tool's floor should be, without writing anything. ``(changes, refusals)``.

        The target value for every registered tool is ``uplift[name]`` if the overlay names it and the
        **registered** floor otherwise - a merge over the shipped baseline, exactly like the resource-limit
        leg. That single choice buys both properties that matter here: applying the same overlay twice
        cannot drift, and a rollback (which names nothing) restores each raised floor, so a candidate's
        value cannot outlive it.

        Refusals are whole-leg refusals, and only two kinds: a name the registry does not have, and an
        uplift that would put a tool *below* its registered floor. Ranking is compared against the
        registered value rather than the current one so that a second application of the same overlay still
        reads as "no change" instead of as an attempted downgrade.
        """
        requested = dict(uplift or {})
        desired: dict[str, RiskLevel] = {}
        refused: list[str] = []
        for name in requested:
            if str(name) not in self._tools:
                refused.append(f"{name}: not a registered tool")
        for name, spec in self._tools.items():
            baseline = self._default_risks.get(name, spec.risk)
            if name not in requested:
                desired[name] = baseline
                continue
            try:
                index = int(requested[name])
            except (TypeError, ValueError):
                refused.append(f"{name}: {requested[name]!r} is not a risk rank")
                desired[name] = baseline
                continue
            if index < 1 or index >= len(RISK_ORDER):
                refused.append(f"{name}: rank {index} is outside 1..{len(RISK_ORDER) - 1}")
                desired[name] = baseline
                continue
            target = RISK_ORDER[index]
            if RISK_ORDER.index(target) < RISK_ORDER.index(baseline):
                refused.append(
                    f"{name}: {target.value} is below the registered floor {baseline.value}; a risk floor may only rise"
                )
                desired[name] = baseline
                continue
            desired[name] = target
        if refused:
            return {}, refused
        changes = {
            name: {"from": self._tools[name].risk.value, "to": level.value}
            for name, level in desired.items()
            if self._tools[name].risk is not level
        }
        return changes, []

    def apply_risk_uplift(self, decisions: dict[str, dict[str, str]]) -> None:
        for name, move in (decisions or {}).items():
            if name in self._tools:
                self._tools[name].risk = RiskLevel(move["to"])

    def reset_risk_floors(self) -> list[str]:
        """Restore every floor to its registered value. Returns the tools that moved."""
        restored: list[str] = []
        for name, level in self._default_risks.items():
            spec = self._tools.get(name)
            if spec is not None and spec.risk is not level:
                restored.append(name)
                spec.risk = level
        return restored

    def plan_preference(self, preference: list[str] | tuple[str, ...] | None) -> tuple[list[str], list[str]]:
        """Which names would be honoured, and which are unknown. Pure; see :meth:`reorder`."""
        wanted = [str(name) for name in (preference or ()) if str(name) in self._tools]
        unknown = [str(name) for name in (preference or ()) if str(name) not in self._tools]
        return list(dict.fromkeys(wanted)), unknown

    def reorder(self, preference: list[str] | tuple[str, ...] | None = None) -> list[str]:
        """Put preferred tools first in the order a model is shown them, dropping nothing.

        An overlay may express a *preference* among the tools that already exist (07 §4,
        "capability-to-tool preference order within the permission set already granted"). It may not
        add, remove, or rename a tool: unknown names are reported and ignored rather than failing the
        cycle, and the tools nobody mentioned keep their registration order at the end, so a
        preference can only ever change which tool is offered first - never which tools are offered.
        """
        if preference is None:
            # Restore, not shuffle: the default order is recorded once at registration, so a rollback
            # returns the exact view the agent shipped with rather than an unspecified one.
            if self._default_order and set(self._default_order) == set(self._tools):
                self._tools = {name: self._tools[name] for name in self._default_order}
            return []
        wanted = [name for name in (preference or ()) if name in self._tools]
        ordered = {name: self._tools[name] for name in wanted}
        ordered.update({name: spec for name, spec in self._tools.items() if name not in ordered})
        unknown = [name for name in (preference or ()) if name not in self._tools]
        self._tools = ordered
        return unknown

    def order(self) -> list[str]:
        return list(self._tools)

    def schemas(self) -> list[dict[str, Any]]:
        return [
            {
                "type": "function",
                "function": {
                    "name": spec.name,
                    "description": spec.description,
                    "parameters": spec.arguments,
                },
            }
            for spec in self._tools.values()
        ]

    def execute(self, call: ToolCall) -> ToolResult:
        spec = self.get(call.tool_name)
        if call.risk != spec.risk:
            call.risk = spec.risk
        return spec.handler(call)

    def register_defaults(self) -> None:
        self.register(ToolSpec(
            name="workspace_list",
            description="List files and directories inside the allowlisted workspace.",
            risk=RiskLevel.LOW,
            arguments={"type": "object", "properties": {"path": {"type": "string"}}, "additionalProperties": False},
            handler=self._workspace_list,
        ))
        self.register(ToolSpec(
            name="workspace_read",
            description="Read a UTF-8 text file inside the allowlisted workspace.",
            risk=RiskLevel.LOW,
            arguments={"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"], "additionalProperties": False},
            handler=self._workspace_read,
        ))
        self.register(ToolSpec(
            name="workspace_write",
            description="Write a UTF-8 text file inside the allowlisted workspace.",
            risk=RiskLevel.MEDIUM,
            arguments={"type": "object", "properties": {"path": {"type": "string"}, "content": {"type": "string"}}, "required": ["path", "content"], "additionalProperties": False},
            handler=self._workspace_write,
        ))
        self.register(ToolSpec(
            name="shell",
            description="Run one allowlisted shell command with its working directory fixed to the workspace.",
            risk=RiskLevel.HIGH,
            arguments={"type": "object", "properties": {"command": {"type": "string"}}, "required": ["command"], "additionalProperties": False},
            handler=self._shell,
        ))

    def _workspace_list(self, call: ToolCall) -> ToolResult:
        try:
            path = self.policy.resolve_workspace_path(call.arguments.get("path", "."))
            entries = sorted(str(item.relative_to(self.policy.workspace)) for item in path.iterdir())
            return ToolResult(call.call_id, call.tool_name, True, json.dumps(entries))
        except Exception as exc:
            return ToolResult(call.call_id, call.tool_name, False, error=str(exc))

    def _workspace_read(self, call: ToolCall) -> ToolResult:
        try:
            path = self.policy.resolve_workspace_path(call.arguments["path"])
            return ToolResult(call.call_id, call.tool_name, True, path.read_text(encoding="utf-8"))
        except Exception as exc:
            return ToolResult(call.call_id, call.tool_name, False, error=str(exc))

    def _workspace_write(self, call: ToolCall) -> ToolResult:
        try:
            path = self.policy.resolve_workspace_path(call.arguments["path"])
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(call.arguments["content"], encoding="utf-8")
            return ToolResult(call.call_id, call.tool_name, True, f"Wrote {path.relative_to(self.policy.workspace)}")
        except Exception as exc:
            return ToolResult(call.call_id, call.tool_name, False, error=str(exc))

    def _shell(self, call: ToolCall) -> ToolResult:
        """Run one allowlisted command inside the selected isolation provider.

        There is deliberately no shell here. The command line is tokenised and handed to the
        provider as argv, so chaining and expansion are not merely disallowed by a pattern list -
        there is nothing that would interpret them. Confinement, timeout, and output bounding are
        the provider's, not this function's, because the same limits must apply to a bridge request
        and to this tool, and duplicated limits drift.
        """
        try:
            command = str(call.arguments["command"])
        except KeyError as exc:
            return ToolResult(call.call_id, call.tool_name, False, error=f"Missing required argument: {exc}")
        if len(command) > 4096:
            return ToolResult(call.call_id, call.tool_name, False, error="Command is invalid or exceeds the bounded size")
        try:
            argv = tuple(shlex.split(command))
        except ValueError as exc:
            return ToolResult(call.call_id, call.tool_name, False, error=f"Invalid shell syntax: {exc}")
        if not argv:
            return ToolResult(call.call_id, call.tool_name, False, error="Command is empty")
        request = ExecRequest(
            argv=argv,
            cwd=self.policy.workspace,
            timeout_seconds=self.policy.max_command_seconds,
            max_output_bytes=self.policy.max_output_bytes,
            env={key: value for key in INHERITED_ENVIRONMENT if (value := os.environ.get(key))},
            label="tool.shell",
        )
        result = self.mediator.execute(
            request,
            tool_name="shell",
            arguments=dict(call.arguments),
            risk=call.risk,
            approved=call.approved,
        )
        metadata = {
            "returncode": result.returncode,
            "provider": result.provider,
            "isolated": result.isolated,
            "enforcement": getattr(self.policy, "sandbox_enforcement", "auto"),
        }
        if result.notes:
            metadata["notes"] = list(result.notes)
        if result.refusal:
            metadata["denied"] = True
            return ToolResult(call.call_id, call.tool_name, False, output=result.output, error=result.refusal, metadata=metadata)
        if result.truncated:
            metadata["truncated"] = True
        if "terminated after" in " ".join(result.notes):
            return ToolResult(
                call.call_id,
                call.tool_name,
                False,
                error=f"Command exceeded {self.policy.max_command_seconds}s timeout",
                metadata=metadata,
            )
        if result.returncode:
            return ToolResult(call.call_id, call.tool_name, False, result.output, f"Command exited with {result.returncode}", metadata)
        return ToolResult(call.call_id, call.tool_name, True, result.output, metadata=metadata)

#: Spellings a model, a skill, or an external harness may use for Evo's canonical tools.
#:
#: An alias is a *spelling*, never a capability. Resolution happens once, at the edge, and every
#: decision downstream - risk floor, approval rule, sandbox mount set, receipt label - is looked up
#: under the canonical name only. That ordering is the whole reason the table exists: a harness that
#: calls ``write`` and is mediated as ``shell`` produces an audit record that is false in a way
#: nobody can spot by reading it.
#:
#: Two rules make the table safe rather than merely convenient:
#:
#: * **Ambiguity resolves to nothing.** ``read`` genuinely means "list a directory" in one harness
#:   and "open a file" in another, so it is absent here. Adding it would let a child ask for the
#:   weaker name and receive the stronger tool - a mediation bypass wearing a synonym.
#: * **An unlisted name is not permission.** It resolves to nothing and the caller refuses; "unknown
#:   ⇒ assume the risky one" is how a name-conflict policy becomes decoration (07 §8: ``ToolCatalog``
#:   before any registry that could collide with it).
CANONICAL_ALIASES: dict[str, tuple[str, ...]] = {
    "workspace_list": ("list", "ls", "list_files"),
    "workspace_read": ("read_file", "cat", "view"),
    "workspace_write": ("write_file", "edit", "str_replace"),
    "shell": ("execute", "run_command", "bash"),
}

#: Tools whose execution is a confined process. The isolation leg of usability means something for
#: these and something else for the in-process file tools, where the boundary is the workspace path
#: rather than a namespace.
PROCESS_TOOLS: frozenset[str] = frozenset({"shell"})


@dataclass(frozen=True)
class NameResolution:
    """What a requested name actually means. ``canonical == ""`` means "nothing may run"."""

    requested: str
    canonical: str = ""
    status: str = "resolved"
    reason: str = ""

    @property
    def resolved(self) -> bool:
        return bool(self.canonical) and self.status in {"canonical", "alias"}

    def to_dict(self) -> dict[str, Any]:
        return {
            "requested": self.requested,
            "canonical": self.canonical,
            "status": self.status,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class Usability:
    """The three-way answer to "can this tool actually be used". One leg is not enough (07 §8).

    A descriptor without a handler is a lie the prompt tells the model; a handler the operator has not
    permitted is a refusal waiting to happen mid-turn; a permitted handler that would run outside the
    sandbox is the defect this build exists to remove. All three must hold, and the reasons are
    returned so a status report can say *which* leg failed instead of "unavailable".
    """

    name: str
    registered: bool = False
    permitted: bool = False
    confined: bool = False
    reasons: tuple[str, ...] = ()

    @property
    def usable(self) -> bool:
        return bool(self.registered and self.permitted and self.confined)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "registered": self.registered,
            "permitted": self.permitted,
            "confined": self.confined,
            "usable": self.usable,
            "reasons": list(self.reasons),
        }


def canonical_tool_name(
    name: Any,
    registry_names: Iterable[str] | None = None,
    aliases: dict[str, tuple[str, ...]] | None = None,
) -> tuple[str, str]:
    """Resolve one requested name to a canonical tool. ``(canonical, reason)``; ``""`` refuses.

    Pure and importable from a seam: :mod:`evo_agent.backends` may not reach the registry, but it must
    still resolve a child's spelling against the same table the runtime uses, or the two edges would
    disagree about what ``edit`` means.
    """
    text = str(name or "").strip()
    if not text:
        return "", "empty tool name"
    key = text.lower().replace("-", "_").replace(" ", "_")
    known = set(registry_names) if registry_names is not None else None
    table = dict(CANONICAL_ALIASES if aliases is None else aliases)
    if known is not None:
        aliases = {
            canonical: tuple(alias for alias in spellings if alias != canonical)
            for canonical, spellings in CANONICAL_ALIASES.items()
            if canonical in known
        }
    if key in table or (known is not None and key in known):
        canonical = key
    else:
        matches = [
            canonical
            for canonical, spellings in table.items()
            if key in spellings or key == canonical.replace("_", "")
        ]
        if len(matches) > 1:
            return "", (
                f"'{text}' is ambiguous between {', '.join(sorted(matches))}; name the canonical tool"
            )
        if not matches:
            return "", f"'{text}' is not a canonical tool or a reviewed alias of one"
        canonical = matches[0]
    if known is not None and canonical not in known:
        return "", f"'{canonical}' is not registered; an alias cannot invent a tool"
    if canonical in table and canonical not in (known or {canonical}) and known is not None:
        return "", f"'{canonical}' is not registered; an alias cannot invent a tool"
    return canonical, ""


class ToolCatalog:
    """The canonical-name and usability view of a :class:`ToolRegistry`. It executes nothing.

    Kept beside the registry rather than inside it because the two answer different questions: the
    registry answers "what happens when this runs", the catalog answers "what may be offered, under
    what name, and is it usable right now". A model-facing list built straight from ``_tools`` was how
    a descriptor with no handler could reach a prompt (07 §4, capability-availability row).
    """

    def __init__(
        self,
        registry: "ToolRegistry",
        *,
        mediator: Any | None = None,
        aliases: dict[str, tuple[str, ...]] | None = None,
    ) -> None:
        self.registry = registry
        self.mediator = mediator if mediator is not None else getattr(registry, "mediator", None)
        self.aliases = dict(CANONICAL_ALIASES if aliases is None else aliases)

    # -- names ---------------------------------------------------------------
    @property
    def names(self) -> tuple[str, ...]:
        return tuple(self.registry.order())

    def resolve(self, name: Any) -> NameResolution:
        """One requested spelling, resolved against aliases then the registry."""
        text = str(name or "").strip()
        canonical, reason = canonical_tool_name(text, self.names, self.aliases)
        if not canonical:
            return NameResolution(text, "", "refused", reason)
        status = "canonical" if text.lower().replace("-", "_") == canonical else "alias"
        return NameResolution(text, canonical, status, reason or f"'{canonical}' as declared")

    def usable_name(self, name: Any) -> str:
        """The canonical name, or ``""``. The form a seam should call."""
        return self.resolve(name).canonical

    # -- usability -----------------------------------------------------------
    def usability(self, name: Any) -> Usability:
        resolution = self.resolve(name)
        if not resolution.resolved:
            return Usability(str(name), False, False, False, (resolution.reason or "unresolved name",))
        canonical = resolution.canonical
        reasons: list[str] = []
        spec = self.registry._tools.get(canonical)
        registered = spec is not None and callable(getattr(spec, "handler", None))
        if not registered:
            reasons.append("registered: no handler behind the descriptor")
        policy = getattr(self.registry, "policy", None)
        permitted = True
        if spec is not None and policy is not None:
            required = set(getattr(policy, "approval_required_for", set()) or set())
            if spec.risk in required:
                permitted = False
                reasons.append(
                    f"permitted: {spec.risk.value}-risk needs operator approval before it may be offered"
                )
        confined, why = self._confined(canonical)
        if not confined:
            reasons.append(f"confined: {why}")
        return Usability(canonical, registered, permitted, confined, tuple(reasons))

    def _confined(self, canonical: str) -> tuple[bool, str]:
        """Whether this tool's execution is inside a boundary the sandbox enforces.

        Asked of the mediator, never computed here: the provider selection that decides whether a
        launch is confined is the mediator's own, and a catalog that re-ran it would be free to
        disagree with the authority it is meant to report on.
        """
        policy = getattr(self.registry, "policy", None)
        if canonical not in PROCESS_TOOLS:
            try:
                root = policy.resolve_workspace_path(".")
            except Exception as exc:  # a workspace that will not resolve is not a boundary
                return False, f"the workspace boundary does not resolve ({exc})"
            return True, f"confined to {root}"
        state = call_mediator(self.mediator, "isolation_state") if self.mediator is not None else None
        if state is None:
            return False, "no ApprovalMediator wired, so a process tool would run unconfined"
        try:
            confined, detail = state
        except (TypeError, ValueError):
            return False, f"the mediator returned an unusable isolation state ({state!r})"
        return bool(confined), str(detail) or "the mediator reported no provider"

    # -- views ---------------------------------------------------------------
    def offered(self) -> list[dict[str, Any]]:
        """The schemas a model may be shown: only tools that pass all three legs.

        This is the enforcement point for the availability rule. ``ToolRegistry.schemas()`` still
        describes everything, because the registry is also what *executes* and must know its own
        handlers; a prompt must never be built from it directly.
        """
        offered: list[dict[str, Any]] = []
        for schema in self.registry.schemas():
            name = str(schema["function"]["name"])
            if self.usability(name).usable:
                offered.append(schema)
        return offered

    def view(self) -> dict[str, list[dict[str, Any]]]:
        rows = [self.usability(name).to_dict() for name in self.names]
        return {
            "usable": [row for row in rows if row["usable"]],
            "unusable": [row for row in rows if not row["usable"]],
        }


def call_mediator(mediator: Any, method: str) -> Any:
    """Call a zero-argument mediator method, or return ``None`` when the object does not have it.

    Duck-typed on purpose: the catalog is built from whatever mediation authority the runtime holds,
    including a stand-in in tests, and a missing method must read as "not confined" rather than raise.
    """
    handler = getattr(mediator, method, None)
    if handler is None:
        return None
    try:
        return handler()
    except Exception:
        return None
