from __future__ import annotations

from pathlib import Path
import threading

import pytest

from evo_agent import AgentRuntime, RuntimeResourceLimits, RuntimeState
from evo_agent.production import BackupManager, OperationalJournal, ProductionConfig, ProductionSupervisor, _ProcessLock
from evo_agent.storage import SQLiteStore


def test_runtime_restart_reconciles_interrupted_state(tmp_path: Path) -> None:
    runtime = AgentRuntime(tmp_path)
    runtime.start()
    runtime.runtime_record.state = RuntimeState.EXECUTING
    runtime.runtime_record.metadata["process_active"] = True
    runtime._persist_record()

    recovered = AgentRuntime(tmp_path)
    recovered.start()
    assert recovered.runtime_record.metadata["startup_recovery"] is True
    assert recovered.runtime_record.metadata["previous_runtime_state"] == RuntimeState.EXECUTING.value
    assert recovered.state is RuntimeState.READY


def test_corrupt_database_fails_closed_and_backup_validation_rejects_corruption(tmp_path: Path) -> None:
    runtime = AgentRuntime(tmp_path)
    runtime.start()
    runtime.stop("prepare corruption test")
    with runtime.store._connect() as db:
        db.execute("UPDATE events SET payload = ? WHERE event_id = (SELECT event_id FROM events LIMIT 1)", ("not-json",))
    with pytest.raises(RuntimeError, match="integrity validation failed"):
        AgentRuntime(tmp_path).start()

    manager = BackupManager(SQLiteStore(tmp_path / "backup.sqlite3"), tmp_path, retention=2)
    corrupted = tmp_path / "corrupt.sqlite3"
    corrupted.write_text("not a database", encoding="utf-8")
    assert manager.validate(corrupted)["ok"] is False


def test_runtime_queue_backpressure_remains_bounded(tmp_path: Path) -> None:
    limits = RuntimeResourceLimits(max_queue_size=2, max_tasks_per_cycle=1)
    runtime = AgentRuntime(tmp_path, limits=limits)
    runtime.enqueue_task("list the files 1")
    runtime.enqueue_task("list the files 2")
    with pytest.raises(OverflowError, match="backpressure"):
        runtime.enqueue_task("list the files 3")


def test_supervisor_lock_blocks_conflicting_process_control(tmp_path: Path) -> None:
    lock_path = tmp_path / ".evo" / "supervisor.lock"
    first = _ProcessLock(lock_path, 0)
    second_error: list[str] = []
    with first:
        def contender() -> None:
            try:
                with _ProcessLock(lock_path, 0):
                    pass
            except RuntimeError as exc:
                second_error.append(str(exc))

        thread = threading.Thread(target=contender)
        thread.start()
        thread.join(timeout=2)
    assert second_error and "already running" in second_error[0]


def test_bounded_multi_cycle_supervisor_stops_at_configured_limit(tmp_path: Path) -> None:
    runtime = AgentRuntime(tmp_path)
    for index in range(4):
        runtime.enqueue_task(f"list the files {index}")
    supervisor = ProductionSupervisor(runtime, ProductionConfig(max_cycles_per_run=3, cycle_sleep_seconds=0))
    report = supervisor.run()
    assert report.status == "completed"
    assert report.cycles_requested == 3
    assert report.cycles_completed == 3
    assert report.tasks_completed == 3
    assert len(report.cycle_results) == 3


def test_journal_recovery_is_idempotent(tmp_path: Path) -> None:
    store = SQLiteStore(tmp_path / ".evo" / "agent.sqlite3")
    journal = OperationalJournal(store)
    journal.start_run("one", 1)
    assert journal.recover_interrupted() == 1
    assert journal.recover_interrupted() == 0
    assert journal.runs()[0]["status"] == "interrupted"
