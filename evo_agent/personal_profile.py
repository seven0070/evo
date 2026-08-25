"""Safe personal-use configuration for Evo's bounded independent operation."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
import json
from pathlib import Path
import re
from typing import Any, Mapping

from .models import RiskLevel
from .runtime import RuntimeResourceLimits
from .security import SecurityPolicy


_PROFILE_VERSION = "personal-v1"
_SECRET_PATTERN = re.compile(r"(?:api[_ -]?key|password|secret|token|credential|authorization|private[_ -]?key)", re.I)
_DEFAULT_SHELL_COMMANDS = frozenset({"pwd", "ls", "find", "cat", "head", "tail", "grep", "printf", "echo", "python3", "pytest"})
_ALLOWED_FIELDS = frozenset({
    "profile_version", "profile_id", "model", "safe_mode_default", "allowed_shell_commands",
    "max_command_seconds", "max_concurrent_tasks", "max_task_duration", "max_total_runtime",
    "max_retry_count", "max_recovery_cycles", "max_replans", "max_memory_bytes",
    "max_storage_bytes", "max_queue_size", "max_tasks_per_cycle", "max_event_growth",
    "approval_required_for", "allow_external_actions",
})


class PersonalProfileError(ValueError):
    """Raised when personal configuration would weaken a safety boundary."""


@dataclass(frozen=True)
class PersonalOperatingProfile:
    profile_version: str = _PROFILE_VERSION
    profile_id: str = "personal-default"
    model: str = "offline"
    safe_mode_default: bool = False
    allowed_shell_commands: tuple[str, ...] = tuple(sorted(_DEFAULT_SHELL_COMMANDS))
    max_command_seconds: int = 30
    max_concurrent_tasks: int = 1
    max_task_duration: int = 120
    max_total_runtime: int = 3600
    max_retry_count: int = 2
    max_recovery_cycles: int = 3
    max_replans: int = 1
    max_memory_bytes: int = 8_000_000
    max_storage_bytes: int = 100_000_000
    max_queue_size: int = 100
    max_tasks_per_cycle: int = 1
    max_event_growth: int = 1000
    approval_required_for: tuple[str, ...] = (RiskLevel.MEDIUM.value, RiskLevel.HIGH.value, RiskLevel.CRITICAL.value)
    allow_external_actions: bool = False
    metadata: Mapping[str, Any] = field(default_factory=dict, repr=False, compare=False)

    def __post_init__(self) -> None:
        if self.profile_version != _PROFILE_VERSION:
            raise PersonalProfileError(f"unsupported profile_version: {self.profile_version}")
        if not self.profile_id or len(self.profile_id) > 80 or _SECRET_PATTERN.search(self.profile_id):
            raise PersonalProfileError("profile_id is invalid")
        if not self.model or len(self.model) > 160 or _SECRET_PATTERN.search(self.model):
            raise PersonalProfileError("model identifier is invalid")
        commands = tuple(sorted({str(item) for item in self.allowed_shell_commands}))
        if not commands or not set(commands).issubset(_DEFAULT_SHELL_COMMANDS):
            raise PersonalProfileError("allowed_shell_commands may only tighten the built-in allowlist")
        object.__setattr__(self, "allowed_shell_commands", commands)
        risks = tuple(dict.fromkeys(str(item) for item in self.approval_required_for))
        if RiskLevel.CRITICAL.value not in risks:
            raise PersonalProfileError("critical-risk approval cannot be disabled")
        if any(item not in {level.value for level in RiskLevel} for item in risks):
            raise PersonalProfileError("approval_required_for contains an unknown risk")
        object.__setattr__(self, "approval_required_for", risks)
        if self.allow_external_actions:
            raise PersonalProfileError("external actions remain disabled in the personal profile")
        for name in RuntimeResourceLimits.__dataclass_fields__:
            value = int(getattr(self, name))
            if value < 1:
                raise PersonalProfileError(f"profile limit {name} must be positive")
            if name == "max_memory_bytes" and value > 8_000_000:
                raise PersonalProfileError("personal max_memory_bytes cannot exceed the Runtime ceiling")
            if name == "max_storage_bytes" and value > 100_000_000:
                raise PersonalProfileError("personal max_storage_bytes cannot exceed the Runtime ceiling")
            object.__setattr__(self, name, value)
        if int(self.max_command_seconds) < 1 or int(self.max_command_seconds) > 30:
            raise PersonalProfileError("max_command_seconds must be between 1 and 30")
        object.__setattr__(self, "max_command_seconds", int(self.max_command_seconds))
        if not isinstance(self.metadata, Mapping):
            raise PersonalProfileError("metadata must be an object")
        if any(_SECRET_PATTERN.search(str(key)) for key in self.metadata):
            raise PersonalProfileError("profile metadata contains a secret-bearing key")

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "PersonalOperatingProfile":
        if not isinstance(raw, Mapping):
            raise PersonalProfileError("personal profile must be a JSON object")
        unknown = set(raw) - _ALLOWED_FIELDS
        if unknown:
            raise PersonalProfileError(f"unknown personal profile fields: {sorted(map(str, unknown))}")
        values = dict(raw)
        values["allowed_shell_commands"] = tuple(values.get("allowed_shell_commands", cls.allowed_shell_commands))
        values["approval_required_for"] = tuple(values.get("approval_required_for", cls.approval_required_for))
        return cls(**values)

    @classmethod
    def load(cls, path: Path | None = None, workspace: Path | None = None) -> "PersonalOperatingProfile":
        target = Path(path) if path else (Path(workspace) / ".evo" / "personal_profile.json" if workspace else None)
        if target is None or not target.exists():
            return cls()
        try:
            raw = json.loads(target.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise PersonalProfileError(f"cannot load personal profile: {type(exc).__name__}") from exc
        return cls.from_dict(raw)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["allowed_shell_commands"] = list(self.allowed_shell_commands)
        data["approval_required_for"] = list(self.approval_required_for)
        data["metadata"] = {}
        return data

    def to_runtime_limits(self, base: RuntimeResourceLimits | None = None) -> RuntimeResourceLimits:
        baseline = base or RuntimeResourceLimits()
        values = {name: min(int(getattr(self, name)), int(getattr(baseline, name))) for name in RuntimeResourceLimits.__dataclass_fields__}
        return RuntimeResourceLimits(**values)

    def build_security_policy(self, workspace: Path) -> SecurityPolicy:
        approval = {RiskLevel(item) for item in self.approval_required_for}
        return SecurityPolicy(workspace, allowed_commands=set(self.allowed_shell_commands), approval_required_for=approval, max_command_seconds=self.max_command_seconds)


def default_personal_profile_path(workspace: Path) -> Path:
    return Path(workspace).expanduser().resolve() / ".evo" / "personal_profile.json"
