from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from evo_agent import (
    AgentRuntime,
    FailureClass,
    LifecycleManager,
    RuntimeResourceLimits,
    RuntimeSchedule,
    RuntimeState,
    RuntimeTaskStatus,
    ScheduleKind,
    TaskPriority,
)


def make_runtime(tmp_path: Path, **kwargs) -> AgentRuntime:
    limits = kwargs.pop("limits", RuntimeResourceLimits(max_total_runtime=120, max_tasks_per_cycle=1))
    return AgentRuntime(tmp_path / "workspace", limits=limits, **kwargs)


def test_lifecycle_state_machine_rejects_invalid_transitions():
    with pytest.raises(ValueError):
        LifecycleManager.validate(RuntimeState.READY, RuntimeState.STOPPED)
    assert RuntimeState.STARTING in LifecycleManager.TRANSITIONS[RuntimeState.STOPPED]


def test_runtime_start_persists_and_restart_recovers_interrupted_task(tmp_path: Path):
    runtime = make_runtime(tmp_path)
    runtime.start()
    task = runtime.enqueue_task("list the files")
    task.status = RuntimeTaskStatus.RUNNING
    runtime.queue.update(task)

    restarted = make_runtime(tmp_path)
    restarted.start()
    recovered = restarted.task(task.task_id)
    assert recovered is not None
    assert recovered.status is RuntimeTaskStatus.WAITING
    assert recovered.metadata["recovery_required"] is True
    assert restarted.runtime_record.restart_count == 1
    assert restarted.store.count_events("runtime_crash_recovery") >= 1


def test_queue_deduplicates_and_enforces_backpressure(tmp_path: Path):
    limits = RuntimeResourceLimits(max_queue_size=1, max_total_runtime=120)
    runtime = make_runtime(tmp_path, limits=limits)
    first = runtime.enqueue_task("same logical goal")
    duplicate = runtime.enqueue_task("same logical goal")
    assert duplicate.task_id == first.task_id
    with pytest.raises(OverflowError):
        runtime.enqueue_task("different goal")


def test_scheduler_priority_and_dependency_ordering(tmp_path: Path):
    limits = RuntimeResourceLimits(max_queue_size=10, max_tasks_per_cycle=1, max_total_runtime=120)
    runtime = make_runtime(tmp_path, limits=limits)
    low = runtime.enqueue_task("low", priority=TaskPriority.LOW)
    high = runtime.enqueue_task("high", priority=TaskPriority.HIGH)
    dependent = runtime.enqueue_task("dependent", dependencies=[high.task_id])
    ready = runtime.scheduler.ready_tasks()
    assert ready[0].task_id == high.task_id
    assert dependent.task_id not in {item.task_id for item in ready}
    high.status = RuntimeTaskStatus.COMPLETED
    runtime.queue.update(high)
    assert dependent.task_id in {item.task_id for item in runtime.scheduler.ready_tasks()}
    assert low.task_id in {item.task_id for item in runtime.scheduler.ready_tasks()}


def test_one_shot_and_condition_schedules_are_bounded(tmp_path: Path):
    runtime = make_runtime(tmp_path)
    now = datetime.now(timezone.utc)
    one_shot = RuntimeSchedule("once", "list the files", ScheduleKind.ONCE, run_at=(now - timedelta(seconds=1)).isoformat())
    runtime.schedule_task(one_shot)
    created = runtime.scheduler.tick(now.isoformat())
    assert len(created) == 1
    assert runtime.scheduler.tick(now.isoformat()) == []
    with pytest.raises(ValueError):
        RuntimeSchedule("bad", "goal", ScheduleKind.INTERVAL, interval_seconds=0)
    condition = RuntimeSchedule("condition", "list the files", ScheduleKind.CONDITION, condition={"type": "file_exists", "path": "ready.txt"})
    runtime.schedule_task(condition)
    assert runtime.scheduler.tick(now.isoformat()) == []
    (runtime.workspace / "ready.txt").write_text("data", encoding="utf-8")
    assert len(runtime.scheduler.tick(now.isoformat())) == 1


def test_exact_task_approval_and_revalidation(tmp_path: Path):
    runtime = make_runtime(tmp_path)
    runtime.start()
    task = runtime.enqueue_task("list the files", approval_requirement=True)
    first = runtime.run_cycle()
    assert first.tasks_waiting == 1
    waiting = runtime.task(task.task_id)
    assert waiting is not None
    scope = waiting.metadata["approval_context_hash"]
    with pytest.raises(PermissionError):
        runtime.approve_task(task.task_id, actor="runtime", scope_hash=scope)
    with pytest.raises(PermissionError):
        runtime.approve_task(task.task_id, actor="human", scope_hash="stale")
    approval = runtime.approve_task(task.task_id, actor="human", scope_hash=scope)
    assert approval.status == "approved"
    assert runtime.task(task.task_id).status is RuntimeTaskStatus.READY


def test_safe_mode_restricts_side_effecting_tasks(tmp_path: Path):
    runtime = make_runtime(tmp_path)
    runtime.start()
    runtime.set_safe_mode(True)
    task = runtime.enqueue_task("write a file")
    result = runtime.run_cycle()
    assert result.tasks_waiting == 1
    assert runtime.task(task.task_id).status is RuntimeTaskStatus.WAITING
    assert "safe mode" in (runtime.task(task.task_id).last_error or "")


def test_recovery_policy_never_retries_permission_and_circuit_breaks(tmp_path: Path):
    runtime = make_runtime(tmp_path)
    task = runtime.enqueue_task("goal", retry_budget=3)
    assert runtime.recovery.recover(task, FailureClass.PERMISSION, "permission denied") == "blocked"
    assert runtime.task(task.task_id).status is RuntimeTaskStatus.BLOCKED
    retry_task = runtime.enqueue_task("retryable", retry_budget=3)
    for _ in range(runtime.circuit_breaker_threshold):
        retry_task = runtime.task(retry_task.task_id)
        assert retry_task is not None
        runtime.recovery.recover(retry_task, FailureClass.TRANSIENT, "temporary timeout")
    assert runtime.task(retry_task.task_id).status is RuntimeTaskStatus.PAUSED
    assert runtime.store.count_events("runtime_circuit_breaker") >= 1


def test_kill_switch_is_independent_and_irremovable(tmp_path: Path):
    runtime = make_runtime(tmp_path)
    runtime.start()
    runtime.enqueue_task("list the files")
    stopped = runtime.kill_switch("operator emergency stop")
    assert stopped.state is RuntimeState.STOPPED
    with pytest.raises(RuntimeError):
        runtime.start()
    with pytest.raises(PermissionError):
        runtime.clear_kill_switch()


def test_multi_cycle_cognitive_world_memory_execution_and_shutdown(tmp_path: Path):
    limits = RuntimeResourceLimits(max_tasks_per_cycle=1, max_total_runtime=120)
    runtime = make_runtime(tmp_path, limits=limits)
    runtime.start()
    first = runtime.enqueue_task("list the files", priority=TaskPriority.HIGH)
    second = runtime.enqueue_task("list the files in the workspace again", priority=TaskPriority.NORMAL)
    cycle_one = runtime.run_cycle()
    assert cycle_one.tasks_started == 1
    assert runtime.task(first.task_id).status is RuntimeTaskStatus.COMPLETED
    cycle_two = runtime.run_cycle()
    assert cycle_two.tasks_started == 1
    assert runtime.task(second.task_id).status is RuntimeTaskStatus.COMPLETED
    assert runtime.store.count_events("environment_observed") >= 2
    assert runtime.store.count_events("runtime_task_completed") >= 2
    assert runtime.health().database_accessible is True
    runtime.stop("test complete")
    assert runtime.state is RuntimeState.STOPPED


def test_long_running_loop_is_explicitly_bounded(tmp_path: Path):
    runtime = make_runtime(tmp_path)
    results = runtime.run_forever(max_cycles=2, sleep_seconds=0)
    assert len(results) == 2
    assert runtime.state is RuntimeState.READY


def test_expired_tasks_are_not_executed(tmp_path: Path):
    runtime = make_runtime(tmp_path)
    expired = runtime.enqueue_task("must not run", deadline=(datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat())
    runtime.start()
    runtime.run_cycle()
    assert runtime.task(expired.task_id).status is RuntimeTaskStatus.EXPIRED


def test_runtime_status_contains_no_secret_values(tmp_path: Path):
    runtime = make_runtime(tmp_path)
    runtime.start()
    status = runtime.status()
    rendered = str(status)
    assert "OPENAI_API_KEY" not in rendered
    assert "ANTHROPIC_API_KEY" not in rendered
    assert "credential" not in rendered.lower()
