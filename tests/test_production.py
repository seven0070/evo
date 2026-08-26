from __future__ import annotations

import json
from pathlib import Path

import pytest

from evo_agent import AgentRuntime, RuleBasedAdapter
from evo_agent.production import BackupManager, CrashReporter, OperationalJournal, ProductionConfig, ProductionSchemaManager, ProductionSupervisor


def make_runtime(tmp_path: Path) -> AgentRuntime:
    return AgentRuntime(tmp_path, model=RuleBasedAdapter())


def test_production_config_is_strict_and_cannot_expand_scope(tmp_path: Path) -> None:
    config_path = tmp_path / "production.json"
    config_path.write_text(json.dumps({"max_cycles_per_run": 2, "backup_retention": 2}), encoding="utf-8")
    config = ProductionConfig.load(tmp_path, config_path)
    assert config.max_cycles_per_run == 2
    assert config.backup_retention == 2

    config_path.write_text(json.dumps({"OPENAI_API_KEY": "secret"}), encoding="utf-8")
    with pytest.raises(ValueError, match="credential"):
        ProductionConfig.load(tmp_path, config_path)

    config_path.write_text(json.dumps({"backup_directory": "../outside"}), encoding="utf-8")
    with pytest.raises(ValueError, match="inside the workspace"):
        ProductionConfig.load(tmp_path, config_path)

    config_path.write_text(json.dumps({"unknown_setting": True}), encoding="utf-8")
    with pytest.raises(ValueError, match="unknown"):
        ProductionConfig.load(tmp_path, config_path)


def test_schema_journal_and_restart_recovery_are_persisted(tmp_path: Path) -> None:
    runtime = make_runtime(tmp_path)
    ProductionSchemaManager(runtime.store)
    journal = OperationalJournal(runtime.store)
    journal.start_run("run-interrupted", 1, {"credential": "must not persist"})
    assert journal.recover_interrupted() == 1
    rows = journal.runs()
    assert rows[0]["status"] == "interrupted"
    assert "must not persist" not in rows[0]["payload"]
    assert {"production_schema", "production_runs", "production_metrics"}.issubset(
        {row[0] for row in runtime.store._connect().execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    )


def test_supervisor_executes_only_bounded_runtime_cycles_and_backups_state(tmp_path: Path) -> None:
    runtime = make_runtime(tmp_path)
    runtime.enqueue_task("list the files")
    supervisor = ProductionSupervisor(runtime, ProductionConfig(max_cycles_per_run=1, backup_retention=2))
    report = supervisor.run(backup=True)
    assert report.status == "completed"
    assert report.cycles_completed == 1
    assert report.tasks_completed == 1
    assert report.backup is not None
    assert supervisor.health.check()["status"] in {"healthy", "stopped"}
    backup_report = supervisor.backups.validate(Path(report.backup))
    assert backup_report["ok"] is True
    assert supervisor.journal.metrics()["cycles_completed"] == 1
    assert runtime.kill_switch_active is False


def test_supervisor_respects_kill_switch_and_does_not_clear_it(tmp_path: Path) -> None:
    runtime = make_runtime(tmp_path)
    runtime.kill_switch("production test stop")
    supervisor = ProductionSupervisor(runtime)
    with pytest.raises(RuntimeError, match="kill switch"):
        supervisor.run()
    assert runtime.kill_switch_active is True
    incidents = supervisor.crash_reports.list()
    assert len(incidents) == 1
    incident = json.loads(incidents[0].read_text(encoding="utf-8"))
    assert incident["error_type"] == "RuntimeError"
    assert "kill switch" in incident["error"].lower()


def test_crash_reporter_is_local_redacted_atomic_and_bounded(tmp_path: Path) -> None:
    reporter = CrashReporter(tmp_path, max_reports=2)
    paths = [reporter.record("runtime", RuntimeError("authorization=top-secret Bearer abc123"), {"api_key": "hidden", "task_id": "bounded"}) for _ in range(3)]
    assert paths[-1].exists()
    assert len(reporter.list()) == 2
    payload = json.loads(paths[-1].read_text(encoding="utf-8"))
    rendered = json.dumps(payload, sort_keys=True)
    assert "top-secret" not in rendered and "abc123" not in rendered and "hidden" not in rendered
    assert payload["error_type"] == "RuntimeError"
    assert payload["context"]["api_key"] == "[REDACTED]"
    assert not list((tmp_path / ".evo" / "incidents").glob("*.tmp"))


def test_backup_retention_is_bounded(tmp_path: Path) -> None:
    runtime = make_runtime(tmp_path)
    manager = BackupManager(runtime.store, tmp_path, retention=2)
    paths = [manager.create(f"test-{index}") for index in range(4)]
    assert paths[-1].exists()
    assert len(manager.list()) == 2
    assert all(manager.validate(path)["ok"] for path in manager.list())
