from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
BRIDGE = ROOT / "desktop" / "bridge" / "evo_desktop_bridge.py"


def call_bridge(workspace: Path, request: dict) -> dict:
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(ROOT)
    completed = subprocess.run(
        [sys.executable, str(BRIDGE), "--workspace", str(workspace)],
        input=json.dumps(request) + "\n",
        capture_output=True,
        text=True,
        env=environment,
        check=True,
    )
    lines = [line for line in completed.stdout.splitlines() if line.strip()]
    assert len(lines) == 1, completed.stderr
    return json.loads(lines[0])


def value(workspace: Path, request: dict):
    envelope = call_bridge(workspace, request)
    assert envelope["ok"], envelope
    return envelope["value"]


def error(workspace: Path, request: dict) -> str:
    envelope = call_bridge(workspace, request)
    assert not envelope["ok"]
    return envelope["error"]


def test_profile_and_status_are_offline_and_bounded(tmp_path: Path) -> None:
    profile = value(tmp_path, {"command": "get_profile"})
    assert profile["model"] == "offline"
    assert profile["allow_external_actions"] is False
    assert profile["max_tasks_per_cycle"] == 1

    status = value(tmp_path, {"command": "get_status"})
    assert "runtime" in status
    assert isinstance(status["pending_approvals_list"], list)
    assert "OPENAI_API_KEY" not in json.dumps(status)


def test_goal_cycle_and_history_are_verified_without_raw_context(tmp_path: Path) -> None:
    submitted = value(tmp_path, {"command": "submit_goal", "goal": "list the files"})
    assert submitted["status"] == "queued"
    cycle = value(tmp_path, {"command": "run_cycle"})
    assert cycle["tasks_considered"] == 1

    tasks = value(tmp_path, {"command": "list_tasks", "limit": 10})
    assert tasks
    task = tasks[0]
    assert task["goal"] == "list the files"
    assert task["status"] in {"completed", "failed", "blocked"}
    assert set(task) == {
        "task_id",
        "goal",
        "status",
        "progress",
        "last_error",
        "verified",
        "created_at",
        "updated_at",
        "plan_id",
        "metadata",
    }
    assert task["metadata"] == {"verified": task["verified"]}
    assert "last_result" not in json.dumps(task)
    assert "fingerprint" not in task
    assert "resource_budget" not in task


def test_approval_requires_scope_and_records_human_actor(tmp_path: Path) -> None:
    submitted = value(
        tmp_path,
        {
            "command": "submit_goal",
            "goal": "list the files",
            "approvalRequired": True,
        },
    )
    assert submitted["status"] == "queued"
    waiting = value(tmp_path, {"command": "run_cycle"})
    assert waiting["tasks_waiting"] == 1

    status = value(tmp_path, {"command": "get_status"})
    approval = status["pending_approvals_list"][0]
    approved = value(
        tmp_path,
        {
            "command": "approve_task",
            "taskId": approval["task_id"],
            "scopeHash": approval["scope_hash"],
            "actor": "agent",
        },
    )
    assert approved["status"] == "approved"
    assert approved["actor"] == "human"

    completed = value(tmp_path, {"command": "run_cycle"})
    assert completed["tasks_completed"] == 1


def test_unsupported_commands_and_external_operations_are_blocked(tmp_path: Path) -> None:
    message = error(tmp_path, {"command": "run_shell", "commandLine": "whoami"})
    assert "unsupported desktop command" in message
    message = error(tmp_path, {"command": "external_action"})
    assert "unsupported desktop command" in message
    message = error(tmp_path, {"command": "approve_task", "taskId": "missing"})
    assert "KeyError" in message


def test_safe_mode_and_kill_switch_are_operator_controls(tmp_path: Path) -> None:
    safe = value(tmp_path, {"command": "set_safe_mode", "enabled": True, "reason": "test"})
    assert safe["metadata"]["safe_mode"] is True
    status = value(tmp_path, {"command": "get_status"})
    assert status["safe_mode"] is True

    stopped = value(tmp_path, {"command": "kill_switch", "reason": "test emergency"})
    assert stopped["metadata"]["kill_switch"] is True
    message = error(tmp_path, {"command": "submit_goal", "goal": "list the files"})
    assert "kill switch is active" in message
