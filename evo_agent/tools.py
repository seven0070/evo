from __future__ import annotations

from dataclasses import dataclass
import json
import subprocess
from pathlib import Path
from typing import Any, Callable

from .models import RiskLevel, ToolCall, ToolResult
from .security import SecurityPolicy


@dataclass
class ToolSpec:
    name: str
    description: str
    risk: RiskLevel
    arguments: dict[str, Any]
    handler: Callable[[ToolCall], ToolResult]


class ToolRegistry:
    def __init__(self, policy: SecurityPolicy):
        self.policy = policy
        self._tools: dict[str, ToolSpec] = {}
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
        command = call.arguments["command"]
        allowed, reason = self.policy.validate_command(command)
        if not allowed:
            return ToolResult(call.call_id, call.tool_name, False, error=reason)
        try:
            completed = subprocess.run(
                command,
                cwd=self.policy.workspace,
                shell=True,
                capture_output=True,
                text=True,
                timeout=self.policy.max_command_seconds,
                check=False,
            )
            output = (completed.stdout + completed.stderr).strip()
            metadata = {"returncode": completed.returncode}
            if completed.returncode:
                return ToolResult(call.call_id, call.tool_name, False, output, f"Command exited with {completed.returncode}", metadata)
            return ToolResult(call.call_id, call.tool_name, True, output, metadata=metadata)
        except subprocess.TimeoutExpired:
            return ToolResult(call.call_id, call.tool_name, False, error=f"Command exceeded {self.policy.max_command_seconds}s timeout")
        except Exception as exc:
            return ToolResult(call.call_id, call.tool_name, False, error=str(exc))
