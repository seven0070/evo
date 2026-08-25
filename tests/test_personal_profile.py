from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

import pytest

from evo_agent import AgentRuntime, PersonalOperatingProfile, PersonalProfileError, RuntimeResourceLimits


def test_default_personal_profile_is_offline_and_bounded():
    profile = PersonalOperatingProfile()
    limits = profile.to_runtime_limits()
    assert profile.model == "offline"
    assert profile.allow_external_actions is False
    assert limits.max_total_runtime <= RuntimeResourceLimits().max_total_runtime
    assert limits.max_queue_size <= RuntimeResourceLimits().max_queue_size
    assert "rm" not in profile.allowed_shell_commands


def test_profile_can_only_tighten_limits_and_shell_allowlist():
    profile = PersonalOperatingProfile.from_dict({
        "profile_id": "private-local",
        "allowed_shell_commands": ["pwd", "ls"],
        "max_total_runtime": 60,
        "max_queue_size": 2,
        "max_command_seconds": 5,
    })
    assert profile.allowed_shell_commands == ("ls", "pwd")
    assert profile.to_runtime_limits().max_total_runtime == 60
    policy = profile.build_security_policy(Path("/tmp/evo-personal-profile-test"))
    assert policy.validate_command("pwd")[0] is True
    assert policy.validate_command("pytest")[0] is False


def test_profile_rejects_authority_weakening_and_secrets():
    with pytest.raises(PersonalProfileError):
        PersonalOperatingProfile.from_dict({"allow_external_actions": True})
    with pytest.raises(PersonalProfileError):
        PersonalOperatingProfile.from_dict({"approval_required_for": ["low"]})
    with pytest.raises(PersonalProfileError):
        PersonalOperatingProfile.from_dict({"metadata": {"api_key": "not persisted"}})
    with pytest.raises(PersonalProfileError):
        PersonalOperatingProfile.from_dict({"allowed_shell_commands": ["curl"]})


def test_runtime_accepts_profile_limits_and_policy(tmp_path: Path):
    profile = PersonalOperatingProfile.from_dict({"max_task_duration": 30, "max_queue_size": 4, "allowed_shell_commands": ["pwd"]})
    runtime = AgentRuntime(tmp_path, limits=profile.to_runtime_limits(), security_policy=profile.build_security_policy(tmp_path))
    assert runtime.limits.max_task_duration == 30
    assert runtime.kernel.policy.validate_command("pwd")[0] is True
    assert runtime.kernel.policy.validate_command("ls")[0] is False


def test_cli_shows_effective_personal_profile_and_blocks_external_action(tmp_path: Path):
    shown = subprocess.run([sys.executable, "-m", "evo_agent.cli", "--workspace", str(tmp_path), "--show-profile"], capture_output=True, text=True, check=False)
    assert shown.returncode == 0
    payload = json.loads(shown.stdout)
    assert payload["model"] == "offline"
    assert payload["allow_external_actions"] is False
    blocked = subprocess.run([sys.executable, "-m", "evo_agent.cli", "--workspace", str(tmp_path), "--external-submit", "missing", "--external-target", "x"], capture_output=True, text=True, check=False)
    assert blocked.returncode == 0
    assert json.loads(blocked.stdout)["status"] == "blocked"
