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

    def register(self, spec: ToolSpec) -> None:
        self._tools[spec.name] = spec

    def get(self, name: str) -> ToolSpec:
        if name not in self._tools:
            raise KeyError(f"Unknown tool: {name}")
        return self._tools[name]

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
