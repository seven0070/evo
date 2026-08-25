from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import shlex

from .models import RiskLevel, ToolCall


@dataclass
class SecurityPolicy:
    workspace: Path
    allowed_commands: set[str] = field(default_factory=lambda: {"pwd", "ls", "find", "cat", "head", "tail", "grep", "printf", "echo", "python3", "pytest"})
    approval_required_for: set[RiskLevel] = field(default_factory=lambda: {RiskLevel.MEDIUM, RiskLevel.HIGH, RiskLevel.CRITICAL})
    max_command_seconds: int = 30

    def __post_init__(self) -> None:
        self.workspace = self.workspace.expanduser().resolve()
        self.workspace.mkdir(parents=True, exist_ok=True)

    def resolve_workspace_path(self, raw_path: str) -> Path:
        candidate = (self.workspace / raw_path).resolve() if not Path(raw_path).is_absolute() else Path(raw_path).expanduser().resolve()
        try:
            candidate.relative_to(self.workspace)
        except ValueError as exc:
            raise PermissionError(f"Path is outside the allowlisted workspace: {raw_path}") from exc
        return candidate

    def validate_command(self, command: str) -> tuple[bool, str]:
        if not isinstance(command, str) or len(command) > 4096:
            return False, "Command is invalid or exceeds the bounded size"
        shell_metacharacters = (";", "&&", "||", "|", ">", "<", "`", "$(", "${", "\\n", "\\r")
        if any(marker in command for marker in shell_metacharacters):
            return False, "Shell chaining, expansion, redirection, and control operators are restricted"
        try:
            parts = shlex.split(command)
        except ValueError as exc:
            return False, f"Invalid shell syntax: {exc}"
        if not parts:
            return False, "Command is empty"
        executable = Path(parts[0]).name
        if executable not in self.allowed_commands:
            return False, f"Command '{executable}' is not allowlisted"
        dangerous_tokens = {"sudo", "rm", "rmdir", "chmod", "chown", "mkfs", "shutdown", "reboot", "curl", "wget", "git", "ssh"}
        if dangerous_tokens.intersection(parts):
            return False, "Command contains a restricted operation"
        if executable in {"python3", "pytest"} and any(token in {"-c", "-m", "-"} for token in parts[1:]):
            return False, "Interpreter code and module execution flags are restricted"
        for token in parts[1:]:
            if token.startswith("-"):
                continue
            if token.startswith("/") or token.startswith("~") or ".." in Path(token).parts:
                try:
                    candidate = (self.workspace / token).expanduser().resolve()
                    candidate.relative_to(self.workspace)
                except (OSError, ValueError):
                    return False, "Command references a path outside the allowlisted workspace"
        return True, "Command allowed"

    def requires_approval(self, tool_call: ToolCall) -> bool:
        return tool_call.risk in self.approval_required_for
