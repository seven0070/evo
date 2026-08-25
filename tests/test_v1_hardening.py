from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

import pytest

from evo_agent import AgentRuntime, RuntimeState, RuntimeTaskStatus
from evo_agent.models import Event, EventType
from evo_agent.security import SecurityPolicy
from evo_agent.storage import SQLiteStore
from evo_agent.version import __version__


def test_v1_version_metadata_is_stable():
    assert __version__ == "1.0.0"


def test_fresh_store_reports_complete_integrity(tmp_path: Path):
    store = SQLiteStore(tmp_path / "agent.sqlite3")
    report = store.database_integrity_report()
    assert report["ok"] is True
    assert report["sqlite_integrity"] == "ok"
    assert report["missing_tables"] == []
    assert store.validate_database_integrity()["ok"] is True


def test_event_payload_is_bounded_and_hash_preserving(tmp_path: Path):
    store = SQLiteStore(tmp_path / "agent.sqlite3")
    huge = {"output": "x" * 100_000, "untrusted": "data"}
    store.append_event(Event("hardening", EventType.TASK_COMPLETED, huge))
    row = store.events_for_task("hardening")[0]
    payload = row["payload"]
    assert payload["truncated"] is True
    assert payload["original_bytes"] > 64 * 1024
    assert len(row["payload"].__repr__().encode()) < 2 * 64 * 1024
    assert len(payload["sha256"]) == 64


def test_event_payload_redacts_secret_bearing_keys(tmp_path: Path):
    store = SQLiteStore(tmp_path / "agent.sqlite3")
    store.append_event(Event("hardening", EventType.TASK_COMPLETED, {"api_key": "super-secret", "nested": {"password": "pw"}, "safe": "value"}))
    rendered = json.dumps(store.events_for_task("hardening")[0]["payload"])
    assert "super-secret" not in rendered
    assert '"api_key": "<redacted>"' in rendered
    assert '"password": "<redacted>"' in rendered


def test_corrupt_persisted_payload_is_reported_and_rejected(tmp_path: Path):
    store = SQLiteStore(tmp_path / "agent.sqlite3")
    with store._connect() as db:
                db.execute("INSERT INTO runtime_states(runtime_id, state, runtime_version, agent_version, architecture_version, current_environment, current_task, current_plan, started_at, last_heartbeat, shutdown_reason, failure_reason, restart_count, metadata, payload, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
 ("corrupt", "ready", "1.0", "1.0", "arch", "env", None, None, "now", "now", None, None, 0, "{}", "not-json", "now"))
    report = store.database_integrity_report()
    assert report["ok"] is False
    assert any(item["table"] == "runtime_states" for item in report["malformed_payloads"])
    with pytest.raises(RuntimeError, match="integrity validation failed"):
        store.validate_database_integrity()


def test_runtime_fails_closed_before_using_corrupt_runtime_record(tmp_path: Path):
    workspace = tmp_path / "workspace"
    runtime = AgentRuntime(workspace)
    runtime.start()
    runtime.stop("prepare corruption")
    with runtime.store._connect() as db:
        db.execute("UPDATE runtime_states SET payload = ? WHERE runtime_id = ?", ("not-json", runtime.runtime_id))
    restarted = AgentRuntime(workspace)
    with pytest.raises(RuntimeError, match="integrity validation failed"):
        restarted.start()


def test_security_policy_remains_confined_and_allowlisted(tmp_path: Path):
    policy = SecurityPolicy(tmp_path / "workspace")
    assert policy.resolve_workspace_path("nested/file.txt").is_relative_to(policy.workspace)
    with pytest.raises(PermissionError):
        policy.resolve_workspace_path("../outside.txt")
    assert policy.validate_command("ls")[0] is True
    assert policy.validate_command("rm -rf .")[0] is False
    assert policy.validate_command("curl https://example.com")[0] is False


def test_runtime_kill_switch_survives_restart(tmp_path: Path):
    workspace = tmp_path / "workspace"
    runtime = AgentRuntime(workspace)
    runtime.start()
    runtime.kill_switch("hardening test")
    restarted = AgentRuntime(workspace)
    assert restarted.runtime_record.metadata["kill_switch"] is True
    with pytest.raises(RuntimeError, match="kill switch"):
        restarted.start()


def test_runtime_safe_mode_prevents_side_effect_execution(tmp_path: Path):
    runtime = AgentRuntime(tmp_path / "workspace")
    runtime.start()
    runtime.set_safe_mode(True)
    task = runtime.enqueue_task("write a file")
    cycle = runtime.run_cycle()
    assert cycle.tasks_started == 1
    assert runtime.task(task.task_id).status is RuntimeTaskStatus.WAITING
    assert runtime.state in {RuntimeState.READY, RuntimeState.WAITING_APPROVAL}


def test_cli_fresh_workspace_uses_persistent_store(tmp_path: Path):
    workspace = tmp_path / "workspace"
    completed = subprocess.run([sys.executable, "-m", "evo_agent.cli", "--workspace", str(workspace), "--json", "--goal-create", "inspect a local report"], capture_output=True, text=True, cwd=Path(__file__).resolve().parents[1])
    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout)["goal_id"]
    assert (workspace / ".evo" / "agent.sqlite3").exists()
