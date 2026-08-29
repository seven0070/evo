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
