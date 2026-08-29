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
    max_output_bytes: int = 65_536
    #: How hard Evo insists on confinement before it will run anything at all.
    #:
    #: ``auto`` selects the strongest usable provider and, on a platform with no namespace support
    #: at all, degrades with an audit event; ``strict`` refuses rather than run unconfined;
    #: ``degrade`` allows the host fallback wherever nothing usable exists; ``off`` skips isolation
    #: (development only, and still recorded). An unrecognised value becomes ``strict``.
    sandbox_enforcement: str = "auto"
    sandbox_provider: str = "auto"
    #: The source tree is mounted read-only inside every confined command, which is what turns
    #: "self-modification goes through staging and promotion" from a convention into a property.
    source_read_only: bool = True
    sandbox_read_only_paths: tuple[str, ...] = ()

    #: What this process may let a turn *change*. ``plan`` is the read-only phase: writes, process
    #: spawning, and anything high-risk are refused before approval is considered (``evo_agent/modes.py``),
    #: and skill staging and promotion are refused outright. It is a policy field rather than a runtime
    #: argument because the sandbox mediator is the enforcement point, and the mediator reads its rules
    #: from the policy - a mode that lived on the runtime object would be invisible to a bridge turn, which
    #: is precisely the path a read-only phase must not have.
    agent_mode: str = "build"

    #: Skill bundles whose declared secrets may be resolved without a per-use approval.
    #:
    #: A skill may say it needs a credential; whether it may *use* one while nobody is watching is a
    #: separate question, and the answer is never allowed to come from the skill. Empty by default, so an
    #: unaudited bundle that asks for a secret is refused by construction rather than by memory of what
    #: the operator intended.
    skill_autonomous_secrets: tuple[str, ...] = ()

    #: Clamped, not validated: a configuration typo must not widen a ceiling (07 R6).
    MIN_COMMAND_SECONDS = 1
    MAX_COMMAND_SECONDS_CEILING = 900
    MAX_OUTPUT_BYTES_CEILING = 1_048_576
    SANDBOX_ENFORCEMENT_LEVELS = ("auto", "strict", "degrade", "off")

    def __post_init__(self) -> None:
        self.workspace = self.workspace.expanduser().resolve()
        self.workspace.mkdir(parents=True, exist_ok=True)
        self.max_command_seconds = min(max(int(self.max_command_seconds), self.MIN_COMMAND_SECONDS), self.MAX_COMMAND_SECONDS_CEILING)
        self.max_output_bytes = min(max(int(self.max_output_bytes), 1), self.MAX_OUTPUT_BYTES_CEILING)
        enforcement = str(self.sandbox_enforcement or "auto").strip().lower()
        self.sandbox_enforcement = enforcement if enforcement in self.SANDBOX_ENFORCEMENT_LEVELS else "strict"
        self.sandbox_provider = str(self.sandbox_provider or "auto").strip().lower() or "auto"
        self.sandbox_read_only_paths = tuple(str(item) for item in (self.sandbox_read_only_paths or ()))
        self.skill_autonomous_secrets = tuple(str(item) for item in (self.skill_autonomous_secrets or ()))
        from .modes import AgentMode  # local: ``modes`` reads the policy, and the policy names the mode

        self.agent_mode = AgentMode.parse(self.agent_mode).value

    def to_dict(self) -> dict[str, Any]:
        """Self-describing policy, for the audit record and for ``evo security show``.

        The isolation settings are included because a run whose confinement level cannot be
        recovered from its own record cannot be audited afterwards: "what did the agent run
        unconfined" is the first question a review asks.
        """
        return {
            "workspace": str(self.workspace),
            "allowed_commands": sorted(self.allowed_commands),
            "approval_required_for": sorted(level.value for level in self.approval_required_for),
            "max_command_seconds": self.max_command_seconds,
            "max_output_bytes": self.max_output_bytes,
            "sandbox_enforcement": self.sandbox_enforcement,
            "sandbox_provider": self.sandbox_provider,
            "source_read_only": bool(self.source_read_only),
            "sandbox_read_only_paths": list(self.sandbox_read_only_paths),
            "skill_autonomous_secrets": list(self.skill_autonomous_secrets),
            "agent_mode": self.agent_mode,
        }

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
