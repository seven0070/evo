from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
import hashlib
import json
from pathlib import Path
import sys
import threading
import time
from typing import Any, Callable, Iterable

from .cognitive import CognitiveOutcome, CognitiveOrchestrator, CognitiveResult
from .models import Event, EventType, utc_now, new_id
from .model_adapter import RuleBasedAdapter
from .model_intelligence import InferenceStatus
from .adaptive_learning import CycleStatus
from .orchestrator import EvolutionOrchestrator
from .storage import SQLiteStore
from .version import __version__


class RuntimeState(str, Enum):
    STARTING = "starting"
    READY = "ready"
    OBSERVING = "observing"
    PLANNING = "planning"
    WAITING_APPROVAL = "waiting_approval"
    EXECUTING = "executing"
    VERIFYING = "verifying"
    LEARNING = "learning"
    RECOVERING = "recovering"
    PAUSED = "paused"
    DEGRADED = "degraded"
    STOPPING = "stopping"
    STOPPED = "stopped"
    FAILED = "failed"


class RuntimeTaskStatus(str, Enum):
    QUEUED = "queued"
    READY = "ready"
    RUNNING = "running"
    WAITING = "waiting"
    BLOCKED = "blocked"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    EXPIRED = "expired"
    PAUSED = "paused"


# Friendly aliases for callers that import the runtime module directly.
TaskStatus = RuntimeTaskStatus


class TaskPriority(str, Enum):
    CRITICAL = "critical"
    HIGH = "high"
    NORMAL = "normal"
    LOW = "low"
    BACKGROUND = "background"


class TaskSource(str, Enum):
    USER = "user"
    RECOVERY = "recovery"
    SCHEDULE = "schedule"
    SYSTEM = "system"
    EVOLUTION = "evolution"
    MEMORY = "memory"
    ENVIRONMENT = "environment"
    INTERNAL = "internal"
    EXTERNAL = "external"
    SPECIALIST = "specialist"
    MODEL = "model"
    LEARNING = "learning"
    SELF_MODEL = "self_model"
    STRATEGIC = "strategic"


class RuntimeHealthStatus(str, Enum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    FAILED = "failed"


class FailureClass(str, Enum):
    TRANSIENT = "transient"
    ENVIRONMENT = "environment"
    RESOURCE = "resource"
    TOOL = "tool"
    PERMISSION = "permission"
    APPROVAL = "approval"
    LOGIC = "logic"
    VERIFICATION = "verification"
    UNKNOWN = "unknown"


class ScheduleKind(str, Enum):
    ONCE = "once"
    INTERVAL = "interval"
    CONDITION = "condition"


@dataclass
class RuntimeRecord:
    runtime_id: str
    runtime_version: str
    agent_version: str
    architecture_version: str
    state: RuntimeState = RuntimeState.STOPPED
    started_at: str | None = None
    last_heartbeat: str | None = None
    last_observation: str | None = None
    current_task: str | None = None
    current_plan: str | None = None
    current_environment: str | None = None
    current_world_snapshot: str | None = None
    shutdown_reason: str | None = None
    failure_reason: str | None = None
    restart_count: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["state"] = self.state.value
        return value


@dataclass
class RuntimeTask:
    task_id: str
    goal: str
    priority: TaskPriority = TaskPriority.NORMAL
    source: TaskSource = TaskSource.USER
    status: RuntimeTaskStatus = RuntimeTaskStatus.QUEUED
    created_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)
    dependencies: list[str] = field(default_factory=list)
    deadline: str | None = None
    resource_budget: dict[str, Any] = field(default_factory=dict)
    approval_requirement: bool | str = False
    retry_budget: int = 1
    current_attempt: int = 0
    plan_id: str | None = None
    environment_version: str | None = None
    agent_version: str = __version__
    metadata: dict[str, Any] = field(default_factory=dict)
    fingerprint: str = ""
    progress: str = "not_started"
    last_error: str | None = None

    def __post_init__(self) -> None:
        if isinstance(self.priority, str):
            self.priority = TaskPriority(self.priority)
        if isinstance(self.source, str):
            self.source = TaskSource(self.source)
        if isinstance(self.status, str):
            self.status = RuntimeTaskStatus(self.status)
        self.retry_budget = max(0, int(self.retry_budget))
        if not self.fingerprint:
            self.fingerprint = self.compute_fingerprint()

    def compute_fingerprint(self) -> str:
        body = {
            "goal": self.goal.strip(),
            "source": self.source.value if isinstance(self.source, TaskSource) else str(self.source),
            "priority": self.priority.value if isinstance(self.priority, TaskPriority) else str(self.priority),
            "dependencies": sorted(self.dependencies),
            "deadline": self.deadline,
            "resource_budget": self.resource_budget,
            "approval_requirement": self.approval_requirement,
            "metadata": {key: self.metadata[key] for key in sorted(self.metadata) if key not in {"goal_id", "plan_id", "last_result", "recovery_required"}},
        }
        return hashlib.sha256(json.dumps(body, sort_keys=True, default=str).encode()).hexdigest()

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["priority"] = self.priority.value
        value["source"] = self.source.value
        value["status"] = self.status.value
        return value


@dataclass
class RuntimeSchedule:
    schedule_id: str
    goal: str
    kind: ScheduleKind = ScheduleKind.ONCE
    priority: TaskPriority = TaskPriority.NORMAL
    source: TaskSource = TaskSource.SCHEDULE
    run_at: str | None = None
    interval_seconds: int | None = None
    condition: dict[str, Any] = field(default_factory=dict)
    dependencies: list[str] = field(default_factory=list)
    deadline_seconds: int | None = None
    resource_budget: dict[str, Any] = field(default_factory=dict)
    approval_requirement: bool | str = False
    enabled: bool = True
    next_run_at: str | None = None
    last_enqueued_at: str | None = None
    run_count: int = 0
    max_runs: int | None = None
    created_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if isinstance(self.kind, str):
            self.kind = ScheduleKind(self.kind)
        if isinstance(self.priority, str):
            self.priority = TaskPriority(self.priority)
        if isinstance(self.source, str):
            self.source = TaskSource(self.source)
        if self.interval_seconds is not None:
            self.interval_seconds = int(self.interval_seconds)
        if self.next_run_at is None:
            self.next_run_at = self.run_at or utc_now()
        self.validate()

    def validate(self) -> None:
        if not self.goal.strip():
            raise ValueError("scheduled goal must not be empty")
        if self.kind is ScheduleKind.INTERVAL and (self.interval_seconds is None or self.interval_seconds <= 0):
            raise ValueError("interval schedules require a positive interval_seconds")
        if self.kind is ScheduleKind.ONCE and not self.run_at:
            raise ValueError("one-shot schedules require run_at")
        if self.kind is ScheduleKind.CONDITION and not self.condition:
            raise ValueError("condition schedules require a deterministic condition")
        if self.interval_seconds is not None and self.interval_seconds > 31 * 24 * 3600:
            raise ValueError("schedule interval exceeds bounded maximum")
        if self.max_runs is not None and int(self.max_runs) < 1:
            raise ValueError("max_runs must be positive")

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["kind"] = self.kind.value
        value["priority"] = self.priority.value
        value["source"] = self.source.value
        return value


@dataclass
class RuntimeApproval:
    approval_id: str
    task_id: str
    status: str = "pending"
    actor: str = "runtime"
    scope_hash: str = ""
    reason: str = ""
    created_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class RuntimeResourceLimits:
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

    def __post_init__(self) -> None:
        for field_name in self.__dataclass_fields__:
            value = int(getattr(self, field_name))
            if value < 1:
                raise ValueError(f"resource limit {field_name} must be positive")
            setattr(self, field_name, value)

    def to_dict(self) -> dict[str, int]:
        return asdict(self)


@dataclass
class RuntimeHealth:
    status: RuntimeHealthStatus
    checked_at: str
    uptime_seconds: float
    heartbeat_age_seconds: float | None
    queue_depth: int
    active_tasks: int
    resource_pressure: float
    environment_fresh: bool
    database_accessible: bool
    tool_available: bool
    metrics: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["status"] = self.status.value
        return value


@dataclass
class RuntimeCycleResult:
    cycle_id: str
    state: str
    tasks_considered: int = 0
    tasks_started: int = 0
    tasks_completed: int = 0
    tasks_failed: int = 0
    tasks_waiting: int = 0
    tasks_blocked: int = 0
    tasks_recovered: int = 0
    environment_changed: bool = False
    evolution_opportunities: int = 0
    failures: list[str] = field(default_factory=list)
    stopped_reason: str = "bounded_cycle_complete"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class LifecycleManager:
    TRANSITIONS: dict[RuntimeState, set[RuntimeState]] = {
        RuntimeState.STARTING: {RuntimeState.READY, RuntimeState.DEGRADED, RuntimeState.FAILED, RuntimeState.STOPPING},
        RuntimeState.READY: {RuntimeState.OBSERVING, RuntimeState.PLANNING, RuntimeState.PAUSED, RuntimeState.DEGRADED, RuntimeState.STOPPING},
        RuntimeState.OBSERVING: {RuntimeState.READY, RuntimeState.PLANNING, RuntimeState.DEGRADED, RuntimeState.FAILED},
        RuntimeState.PLANNING: {RuntimeState.READY, RuntimeState.WAITING_APPROVAL, RuntimeState.EXECUTING, RuntimeState.DEGRADED, RuntimeState.PAUSED, RuntimeState.FAILED},
        RuntimeState.WAITING_APPROVAL: {RuntimeState.OBSERVING, RuntimeState.PLANNING, RuntimeState.EXECUTING, RuntimeState.READY, RuntimeState.PAUSED, RuntimeState.DEGRADED, RuntimeState.STOPPING},
        RuntimeState.EXECUTING: {RuntimeState.VERIFYING, RuntimeState.LEARNING, RuntimeState.RECOVERING, RuntimeState.WAITING_APPROVAL, RuntimeState.READY, RuntimeState.PAUSED, RuntimeState.DEGRADED, RuntimeState.FAILED, RuntimeState.STOPPING},
        RuntimeState.VERIFYING: {RuntimeState.LEARNING, RuntimeState.RECOVERING, RuntimeState.READY, RuntimeState.DEGRADED, RuntimeState.FAILED},
        RuntimeState.LEARNING: {RuntimeState.READY, RuntimeState.PLANNING, RuntimeState.EXECUTING, RuntimeState.RECOVERING, RuntimeState.DEGRADED, RuntimeState.FAILED},
        RuntimeState.RECOVERING: {RuntimeState.READY, RuntimeState.PLANNING, RuntimeState.PAUSED, RuntimeState.DEGRADED, RuntimeState.FAILED},
        RuntimeState.PAUSED: {RuntimeState.STARTING, RuntimeState.READY, RuntimeState.OBSERVING, RuntimeState.STOPPING},
        RuntimeState.DEGRADED: {RuntimeState.STARTING, RuntimeState.READY, RuntimeState.RECOVERING, RuntimeState.PAUSED, RuntimeState.STOPPING, RuntimeState.FAILED},
        RuntimeState.STOPPING: {RuntimeState.STOPPED, RuntimeState.FAILED},
        RuntimeState.STOPPED: {RuntimeState.STARTING},
        RuntimeState.FAILED: {RuntimeState.STARTING, RuntimeState.STOPPING, RuntimeState.STOPPED},
    }

    @classmethod
    def validate(cls, current: RuntimeState, target: RuntimeState) -> None:
        if target not in cls.TRANSITIONS.get(current, set()):
            raise ValueError(f"invalid runtime transition {current.value} -> {target.value}")

    @classmethod
    def transition(cls, record: RuntimeRecord, target: RuntimeState, reason: str = "") -> RuntimeRecord:
        cls.validate(record.state, target)
        record.state = target
        record.metadata["last_transition_reason"] = reason
        return record


class TaskQueue:
    def __init__(self, store: SQLiteStore, limits: RuntimeResourceLimits | None = None):
        self.store = store
        self.limits = limits or RuntimeResourceLimits()

    def enqueue(self, task: RuntimeTask) -> RuntimeTask:
        if not task.goal.strip():
            raise ValueError("task goal must not be empty")
        existing = self.store.runtime_task_by_fingerprint(task.fingerprint)
        if existing and existing.get("status") not in {RuntimeTaskStatus.CANCELLED.value, RuntimeTaskStatus.EXPIRED.value, RuntimeTaskStatus.FAILED.value}:
            return runtime_task_from_row(existing)
        if self.depth() >= self.limits.max_queue_size:
            raise OverflowError("runtime task queue backpressure limit reached")
        task.updated_at = utc_now()
        self.store.save_runtime_task(task)
        return task

    def get(self, task_id: str) -> RuntimeTask | None:
        row = self.store.runtime_task_by_id(task_id)
        return runtime_task_from_row(row) if row else None

    def list(self, status: RuntimeTaskStatus | str | None = None, limit: int = 100) -> list[RuntimeTask]:
        value = status.value if isinstance(status, RuntimeTaskStatus) else status
        return [runtime_task_from_row(row) for row in self.store.find_runtime_tasks(value, limit)]

    def update(self, task: RuntimeTask) -> RuntimeTask:
        task.updated_at = utc_now()
        self.store.save_runtime_task(task)
        return task

    def depth(self) -> int:
        return sum(1 for task in self.list(limit=self.limits.max_queue_size + 1) if task.status not in {RuntimeTaskStatus.COMPLETED, RuntimeTaskStatus.CANCELLED, RuntimeTaskStatus.EXPIRED})

    def cancel(self, task_id: str, reason: str = "Cancelled by user") -> RuntimeTask:
        task = self.get(task_id)
        if not task:
            raise KeyError(task_id)
        if task.status in {RuntimeTaskStatus.COMPLETED, RuntimeTaskStatus.CANCELLED, RuntimeTaskStatus.EXPIRED}:
            return task
        task.status = RuntimeTaskStatus.CANCELLED
        task.last_error = reason
        task.metadata["cancelled_reason"] = reason
        return self.update(task)

    def pause(self, task_id: str, reason: str = "Paused by user") -> RuntimeTask:
        task = self.get(task_id)
        if not task:
            raise KeyError(task_id)
        if task.status in {RuntimeTaskStatus.COMPLETED, RuntimeTaskStatus.CANCELLED, RuntimeTaskStatus.EXPIRED}:
            return task
        task.status = RuntimeTaskStatus.PAUSED
        task.metadata["paused_reason"] = reason
        return self.update(task)

    def resume(self, task_id: str) -> RuntimeTask:
        task = self.get(task_id)
        if not task:
            raise KeyError(task_id)
        if task.status is RuntimeTaskStatus.PAUSED:
            task.status = RuntimeTaskStatus.READY
            task.metadata.pop("paused_reason", None)
            return self.update(task)
        return task


class Scheduler:
    PRIORITY_WEIGHT = {
        TaskPriority.CRITICAL: 500,
        TaskPriority.HIGH: 400,
        TaskPriority.NORMAL: 300,
        TaskPriority.LOW: 200,
        TaskPriority.BACKGROUND: 100,
    }

    def __init__(self, queue: TaskQueue, workspace: Path, store: SQLiteStore):
        self.queue = queue
        self.workspace = Path(workspace).resolve()
        self.store = store

    def register(self, schedule: RuntimeSchedule) -> RuntimeSchedule:
        schedule.validate()
        self.store.save_runtime_schedule(schedule)
        return schedule

    def get_schedule(self, schedule_id: str) -> RuntimeSchedule | None:
        row = self.store.runtime_schedule_by_id(schedule_id)
        return runtime_schedule_from_row(row) if row else None

    def list_schedules(self, limit: int = 100) -> list[RuntimeSchedule]:
        return [runtime_schedule_from_row(row) for row in self.store.find_runtime_schedules(limit=limit)]

    def cancel_schedule(self, schedule_id: str) -> RuntimeSchedule:
        schedule = self.get_schedule(schedule_id)
        if not schedule:
            raise KeyError(schedule_id)
        schedule.enabled = False
        schedule.updated_at = utc_now()
        self.store.save_runtime_schedule(schedule)
        return schedule

    def tick(self, now: str | None = None) -> list[RuntimeTask]:
        current = parse_time(now or utc_now())
        created: list[RuntimeTask] = []
        for schedule in self.list_schedules(limit=1000):
            if not schedule.enabled or (schedule.max_runs is not None and schedule.run_count >= schedule.max_runs):
                continue
            if schedule.kind is ScheduleKind.CONDITION:
                if not self.condition_satisfied(schedule.condition):
                    continue
            elif not schedule.next_run_at or parse_time(schedule.next_run_at) > current:
                continue
            deadline = (current + timedelta(seconds=schedule.deadline_seconds)).isoformat() if schedule.deadline_seconds else None
            task = RuntimeTask(new_id("rtask"), schedule.goal, schedule.priority, schedule.source, dependencies=list(schedule.dependencies), deadline=deadline, resource_budget=dict(schedule.resource_budget), approval_requirement=schedule.approval_requirement, metadata={"schedule_id": schedule.schedule_id})
            try:
                accepted = self.queue.enqueue(task)
            except OverflowError:
                continue
            if accepted.task_id == task.task_id:
                created.append(accepted)
                schedule.last_enqueued_at = current.isoformat()
                schedule.run_count += 1
            if schedule.kind is ScheduleKind.INTERVAL:
                schedule.next_run_at = (current + timedelta(seconds=int(schedule.interval_seconds or 1))).isoformat()
            else:
                schedule.enabled = False
            schedule.updated_at = current.isoformat()
            self.store.save_runtime_schedule(schedule)
        return created

    def ready_tasks(self, now: str | None = None) -> list[RuntimeTask]:
        current = parse_time(now or utc_now())
        candidates: list[tuple[float, RuntimeTask]] = []
        for task in self.queue.list(limit=self.queue.limits.max_queue_size + 1):
            if task.status not in {RuntimeTaskStatus.QUEUED, RuntimeTaskStatus.READY}:
                continue
            if task.deadline and parse_time(task.deadline) <= current:
                task.status = RuntimeTaskStatus.EXPIRED
                task.last_error = "Task deadline expired before execution."
                self.queue.update(task)
                continue
            if not self.dependencies_satisfied(task):
                continue
            if task.metadata.get("retry_at") and parse_time(str(task.metadata["retry_at"])) > current:
                continue
            task.status = RuntimeTaskStatus.READY
            self.queue.update(task)
            age = max(0.0, (current - parse_time(task.created_at)).total_seconds())
            deadline_bonus = 0.0
            if task.deadline:
                remaining = max(1.0, (parse_time(task.deadline) - current).total_seconds())
                deadline_bonus = min(100.0, 10000.0 / remaining)
            score = self.PRIORITY_WEIGHT[task.priority] + min(200.0, age / 10.0) + deadline_bonus
            candidates.append((score, task))
        candidates.sort(key=lambda item: (-item[0], item[1].created_at, item[1].task_id))
        return [task for _, task in candidates]

    def dependencies_satisfied(self, task: RuntimeTask) -> bool:
        if not task.dependencies:
            return True
        for dependency_id in task.dependencies:
            dependency = self.queue.get(dependency_id)
            if not dependency or dependency.status is not RuntimeTaskStatus.COMPLETED:
                return False
        return True

    def condition_satisfied(self, condition: dict[str, Any]) -> bool:
        if not condition:
            return True
        kind = str(condition.get("type", "")).lower()
        if kind == "file_exists":
            try:
                path = self._safe_path(str(condition.get("path", "")))
                return path.exists() and path.is_file()
            except (OSError, ValueError):
                return False
        if kind == "file_missing":
            try:
                return not self._safe_path(str(condition.get("path", ""))).exists()
            except (OSError, ValueError):
                return False
        if kind == "and":
            values = condition.get("conditions", [])
            return isinstance(values, list) and all(self.condition_satisfied(item) for item in values if isinstance(item, dict))
        if kind == "or":
            values = condition.get("conditions", [])
            return isinstance(values, list) and any(self.condition_satisfied(item) for item in values if isinstance(item, dict))
        # No arbitrary expression, shell, Python, or observed text is evaluated.
        return False

    def _safe_path(self, value: str) -> Path:
        if not value:
            raise ValueError("condition path is empty")
        path = (self.workspace / value).resolve() if not Path(value).is_absolute() else Path(value).resolve()
        path.relative_to(self.workspace)
        return path


class RuntimeResourceManager:
    def __init__(self, runtime: AgentRuntime, limits: RuntimeResourceLimits):
        self.runtime = runtime
        self.limits = limits

    def can_accept(self) -> tuple[bool, str]:
        if self.runtime.queue.depth() >= self.limits.max_queue_size:
            return False, "queue limit reached"
        if self.storage_bytes() >= self.limits.max_storage_bytes:
            return False, "storage limit reached"
        if self.runtime.store.total_event_count() >= self.limits.max_event_growth:
            return False, "event growth limit reached"
        return True, "available"

    def can_run(self, task: RuntimeTask) -> tuple[bool, str]:
        active = len(self.runtime.queue.list(RuntimeTaskStatus.RUNNING, limit=self.limits.max_concurrent_tasks + 1))
        if active >= self.limits.max_concurrent_tasks:
            return False, "concurrency limit reached"
        if task.resource_budget.get("memory_bytes", 0) > self.limits.max_memory_bytes:
            return False, "task memory budget exceeds runtime limit"
        if task.resource_budget.get("duration_seconds", 0) > self.limits.max_task_duration:
            return False, "task duration budget exceeds runtime limit"
        started = self.runtime.runtime_record.started_at
        if started and (datetime.now(timezone.utc) - parse_time(started)).total_seconds() >= self.limits.max_total_runtime:
            return False, "total runtime limit reached"
        return True, "available"

    def storage_bytes(self) -> int:
        try:
            return int(self.runtime.store.path.stat().st_size)
        except OSError:
            return 0

    def pressure(self) -> float:
        queue_pressure = self.runtime.queue.depth() / max(1, self.limits.max_queue_size)
        storage_pressure = self.storage_bytes() / max(1, self.limits.max_storage_bytes)
        return min(1.0, max(queue_pressure, storage_pressure))


class EventLoop:
    """Bounded wakeup broker; it never executes task payloads itself."""

    def __init__(self, runtime: AgentRuntime, max_events: int = 256):
        self.runtime = runtime
        self.max_events = max(1, int(max_events))
        self._events: list[dict[str, Any]] = []

    def wake(self, reason: str, payload: dict[str, Any] | None = None) -> bool:
        if len(self._events) >= self.max_events:
            return False
        self._events.append({"reason": str(reason), "payload": dict(payload or {}), "created_at": utc_now()})
        return True

    def drain(self, limit: int = 32) -> list[dict[str, Any]]:
        amount = max(0, min(int(limit), self.max_events))
        items = self._events[:amount]
        del self._events[:amount]
        return items

    def pending(self) -> int:
        return len(self._events)


class ShutdownManager:
    def __init__(self, runtime: AgentRuntime):
        self.runtime = runtime

    def shutdown(self, reason: str = "graceful shutdown") -> RuntimeRecord:
        return self.runtime.stop(reason)

    def kill(self, reason: str = "emergency stop") -> RuntimeRecord:
        return self.runtime.kill_switch(reason)


class HeartbeatManager:
    def __init__(self, runtime: AgentRuntime):
        self.runtime = runtime

    def beat(self) -> RuntimeHealth:
        now = utc_now()
        record = self.runtime.runtime_record
        record.last_heartbeat = now
        self.runtime._persist_record()
        self.runtime._emit(EventType.RUNTIME_HEARTBEAT, {"runtime_id": self.runtime.runtime_id, "state": record.state.value, "active_task": record.current_task}, self.runtime.runtime_id)
        return self.check()

    def check(self) -> RuntimeHealth:
        now = datetime.now(timezone.utc)
        record = self.runtime.runtime_record
        heartbeat_age = None
        if record.last_heartbeat:
            heartbeat_age = max(0.0, (now - parse_time(record.last_heartbeat)).total_seconds())
        uptime = 0.0
        if record.started_at:
            uptime = max(0.0, (now - parse_time(record.started_at)).total_seconds())
        database_accessible = True
        try:
            with self.runtime.store._connect() as db:
                db.execute("SELECT 1").fetchone()
        except Exception:
            database_accessible = False
        environment_fresh = bool(record.current_environment and record.last_observation)
        queue_depth = self.runtime.queue.depth()
        pressure = self.runtime.resources.pressure()
        status = RuntimeHealthStatus.HEALTHY
        if not database_accessible or record.state is RuntimeState.FAILED:
            status = RuntimeHealthStatus.FAILED
        elif record.state in {RuntimeState.DEGRADED, RuntimeState.PAUSED} or pressure >= 0.9 or not environment_fresh:
            status = RuntimeHealthStatus.DEGRADED
        return RuntimeHealth(status, utc_now(), uptime, heartbeat_age, queue_depth, len(self.runtime.queue.list(RuntimeTaskStatus.RUNNING)), pressure, environment_fresh, database_accessible, True, dict(record.metadata.get("metrics", {})))


class RecoveryManager:
    RETRYABLE = {FailureClass.TRANSIENT, FailureClass.ENVIRONMENT, FailureClass.RESOURCE, FailureClass.TOOL, FailureClass.VERIFICATION}
    NEVER_RETRY = {FailureClass.PERMISSION, FailureClass.APPROVAL}

    def __init__(self, runtime: AgentRuntime):
        self.runtime = runtime

    def classify(self, result: CognitiveResult | None = None, error: str = "") -> FailureClass:
        text = error.lower()
        if result:
            text += " " + result.summary.lower() + " " + (result.state.last_error or "").lower()
        if any(item in text for item in ("permission", "governance", "protected-core")):
            return FailureClass.PERMISSION
        if "approval" in text:
            return FailureClass.APPROVAL
        if "environment" in text or "stale plan" in text:
            return FailureClass.ENVIRONMENT
        if "resource" in text or "limit" in text:
            return FailureClass.RESOURCE
        if "verification" in text or "did not verify" in text:
            return FailureClass.VERIFICATION
        if "tool" in text:
            return FailureClass.TOOL
        if "timeout" in text or "tempor" in text:
            return FailureClass.TRANSIENT
        return FailureClass.UNKNOWN

    def recover(self, task: RuntimeTask, failure: FailureClass, reason: str) -> str:
        task.last_error = reason
        task.metadata["failure_class"] = failure.value
        task.metadata["last_failure_at"] = utc_now()
        task.metadata["consecutive_failures"] = int(task.metadata.get("consecutive_failures", 0)) + 1
        if task.metadata.get("action_state_unknown") or failure in self.NEVER_RETRY:
            task.status = RuntimeTaskStatus.BLOCKED if failure in self.NEVER_RETRY else RuntimeTaskStatus.FAILED
            task.metadata["recovery_required"] = True
            self.runtime.queue.update(task)
            return task.status.value
        if task.metadata["consecutive_failures"] >= self.runtime.circuit_breaker_threshold:
            task.status = RuntimeTaskStatus.PAUSED
            task.metadata["circuit_breaker"] = True
            self.runtime.queue.update(task)
            self.runtime._emit(EventType.RUNTIME_CIRCUIT_BREAKER, {"task_id": task.task_id, "reason": reason, "threshold": self.runtime.circuit_breaker_threshold}, task.task_id)
            return task.status.value
        recovery_cycles = int(self.runtime.runtime_record.metadata.get("recovery_cycles", 0))
        if recovery_cycles >= self.runtime.resources.limits.max_recovery_cycles:
            task.status = RuntimeTaskStatus.PAUSED
            task.metadata["recovery_limit"] = True
            self.runtime.queue.update(task)
            self.runtime._emit(EventType.RUNTIME_DEGRADED, {"task_id": task.task_id, "reason": "recovery cycle limit reached"}, task.task_id)
            return task.status.value
        if failure in self.RETRYABLE and task.current_attempt < min(task.retry_budget, self.runtime.resources.limits.max_retry_count):
            self.runtime.runtime_record.metadata["recovery_cycles"] = recovery_cycles + 1
            task.current_attempt += 1
            task.status = RuntimeTaskStatus.READY
            task.metadata["retry_at"] = (datetime.now(timezone.utc) + timedelta(seconds=min(60, 2 ** task.current_attempt))).isoformat()
            self.runtime.queue.update(task)
            self.runtime._emit(EventType.RUNTIME_RECOVERY, {"task_id": task.task_id, "failure_class": failure.value, "attempt": task.current_attempt, "reason": reason}, task.task_id)
            return task.status.value
        task.status = RuntimeTaskStatus.FAILED
        self.runtime.queue.update(task)
        return task.status.value


def _sovereign_drift_override() -> tuple[bool, str]:
    """The one developer override, read from the environment, never from a config file.

    Kept out of ``evo.toml`` deliberately: a config file is something the agent can be asked to
    write, and a switch that disables provenance verification must not be reachable that way.
    Returns ``(accepted, variable_name)`` so the audit record can name what was set.
    """
    import os

    from .sovereign.protected import DRIFT_ENVIRONMENT_VARIABLE as name

    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes"}, name


class AgentRuntime:
    RUNTIME_VERSION = "runtime-v1"
    CIRCUIT_BREAKER_THRESHOLD = 3

    def __init__(self, workspace: Path, model: Any | None = None, store: SQLiteStore | None = None, kernel: Any | None = None, cognitive: CognitiveOrchestrator | None = None, evolution_orchestrator: EvolutionOrchestrator | None = None, source_root: Path | None = None, runtime_id: str | None = None, limits: RuntimeResourceLimits | None = None, approval_callback: Callable[[Any, str], bool] | None = None, safe_mode: bool = False, external_integrations: Any | None = None, specialist_delegation: Any | None = None, model_intelligence: Any | None = None, adaptive_learning: Any | None = None, self_model: Any | None = None, meta_reasoning: Any | None = None, strategic_autonomy: Any | None = None, security_policy: Any | None = None):
        self.workspace = Path(workspace).expanduser().resolve()
        self.workspace.mkdir(parents=True, exist_ok=True)
        self.store = store or SQLiteStore(self.workspace / ".evo" / "agent.sqlite3")
        self.model = model or RuleBasedAdapter()
        self.source_root = Path(source_root or Path(__file__).resolve().parent.parent).expanduser().resolve()
        self.runtime_id = runtime_id or "runtime_" + hashlib.sha256(str(self.workspace).encode()).hexdigest()[:12]
        self.limits = limits or RuntimeResourceLimits()
        self.runtime_record = self._load_record()
        self.queue = TaskQueue(self.store, self.limits)
        self.evolution = evolution_orchestrator or EvolutionOrchestrator(self.store, self.source_root)
        self.external_integrations = external_integrations
        self.specialist_delegation = specialist_delegation
        self.model_intelligence = model_intelligence
        self.adaptive_learning = adaptive_learning
        self.self_model = self_model
        self.meta_reasoning = meta_reasoning
        self.strategic_autonomy = strategic_autonomy
        self._model_requests: dict[str, Any] = {}
        if self.specialist_delegation is not None:
            self.specialist_delegation.runtime = self
        if self.self_model is not None:
            self.self_model.runtime = self
        self.kernel = kernel
        self.cognitive = cognitive
        if self.cognitive is None:
            from .kernel import AgentKernel
            self.kernel = self.kernel or AgentKernel(self.workspace, self.model, store=self.store, approval_callback=approval_callback, external_integrations=self.external_integrations, security_policy=security_policy)
            self.cognitive = CognitiveOrchestrator(self.workspace, model=self.model, store=self.store, kernel=self.kernel, evolution_orchestrator=self.evolution, policy={"max_replans": self.limits.max_replans, "max_execution_time": self.limits.max_task_duration}, external_integrations=self.external_integrations, specialist_delegation=self.specialist_delegation, model_intelligence=self.model_intelligence, self_model=self.self_model, meta_reasoning=self.meta_reasoning)
        elif self.kernel is None:
            self.kernel = getattr(self.cognitive, "kernel", None)
        if self.specialist_delegation is not None and getattr(self.cognitive, "specialist_delegation", None) is None:
            self.cognitive.specialist_delegation = self.specialist_delegation
        if self.external_integrations is not None:
            self.external_integrations.memory = getattr(self.external_integrations, "memory", None) or getattr(self.cognitive, "memory", None)
            self.external_integrations.capability_intelligence = getattr(self.kernel, "capability_intelligence", None)
            if self.kernel is not None:
                self.kernel.external_integrations = self.external_integrations
                self.external_integrations.flexibility = getattr(self.kernel, "flexibility", None)
        if self.strategic_autonomy is None:
            from .strategic_autonomy import StrategicAutonomy
            self.strategic_autonomy = StrategicAutonomy(self.store, self.workspace, capability_intelligence=getattr(self.kernel, "capability_intelligence", None), model_intelligence=self.model_intelligence, specialist_intelligence=self.specialist_delegation, external_integrations=self.external_integrations, memory=getattr(self.cognitive, "memory", None), adaptive_learning=self.adaptive_learning, self_model=self.self_model, runtime=self, evolution_orchestrator=self.evolution, cognitive=self.cognitive)
        self.scheduler = Scheduler(self.queue, self.workspace, self.store)
        self.resources = RuntimeResourceManager(self, self.limits)
        self.heartbeat = HeartbeatManager(self)
        self.recovery = RecoveryManager(self)
        self.event_loop = EventLoop(self)
        self.shutdown_manager = ShutdownManager(self)
        self.lifecycle = LifecycleManager()
        self.safe_mode = bool(safe_mode or self.runtime_record.metadata.get("safe_mode", False))
        self.accepting_work = self.runtime_record.state in {RuntimeState.READY, RuntimeState.OBSERVING, RuntimeState.PLANNING, RuntimeState.EXECUTING, RuntimeState.WAITING_APPROVAL}
        self.kill_switch_active = bool(self.runtime_record.metadata.get("kill_switch", False))
        self.circuit_breaker_threshold = self.CIRCUIT_BREAKER_THRESHOLD
        self._lock = threading.RLock()
        self._stop_event = threading.Event()
        self._started_in_process = False

    @property
    def state(self) -> RuntimeState:
        return self.runtime_record.state

    @property
    def runtime_version(self) -> str:
        return self.runtime_record.runtime_version

    def start(self) -> RuntimeRecord:
        with self._lock:
            if self._started_in_process and self.state is RuntimeState.READY:
                return self.runtime_record
            if self.kill_switch_active:
                raise RuntimeError("runtime kill switch is active")
            # Provenance before anything else: an agent that cannot show which bytes it is
            # running must not start making decisions. This is a pure read of the installed
            # package, and it is deliberately ahead of every state mutation so that a
            # mismatch leaves the persisted runtime record untouched and still inspectable.
            self._validate_sovereign_boundary()
            previous_state = self.state
            if previous_state is not RuntimeState.STARTING:
                # A fresh process may load READY/EXECUTING/etc. from a prior process.
                # Reinitialization is a startup recovery operation, not a public
                # lifecycle transition and therefore is persisted explicitly.
                self.runtime_record.metadata["previous_runtime_state"] = previous_state.value
                self.runtime_record.metadata["startup_recovery"] = previous_state is not RuntimeState.STOPPED
                self.runtime_record.state = RuntimeState.STARTING
                # Validate persisted state before writing a startup-recovery record; a corrupt
                # database must remain observable and fail closed rather than be overwritten.
                self.store.validate_database_integrity()
                self._persist_record()
                if previous_state not in {RuntimeState.STOPPED, RuntimeState.STARTING, RuntimeState.READY}:
                    self._emit(EventType.RUNTIME_CRASH_RECOVERY, {"runtime_id": self.runtime_id, "previous_state": previous_state.value, "action": "startup_revalidation"}, self.runtime_id)
            self._stop_event.clear()
            self.accepting_work = True
            self._emit(EventType.RUNTIME_STARTED, {"runtime_id": self.runtime_id, "runtime_version": self.RUNTIME_VERSION}, self.runtime_id)
            self.runtime_record.runtime_version = self.RUNTIME_VERSION
            self.runtime_record.agent_version = __version__
            self.runtime_record.architecture_version = self._architecture_version()
            self.runtime_record.started_at = utc_now()
            self.runtime_record.shutdown_reason = None
            self.runtime_record.failure_reason = None
            self.runtime_record.restart_count += 1 if self.runtime_record.metadata.get("started_before") else 0
            self.runtime_record.metadata["started_before"] = True
            self._persist_record()
            try:
                self._validate_database()
                self._validate_architecture()
                environment = self._observe("runtime startup")
                self.runtime_record.current_environment = environment.environment.environment_version
                self.runtime_record.last_observation = utc_now()
                self._recover_interrupted_tasks()
                self._transition(RuntimeState.READY, "startup validation and recovery complete")
                self.runtime_record.metadata["process_active"] = True
                self._started_in_process = True
                self._persist_record()
                self._emit(EventType.RUNTIME_READY, {"runtime_id": self.runtime_id, "environment_version": self.runtime_record.current_environment}, self.runtime_id)
                self.heartbeat.beat()
                return self.runtime_record
            except Exception as exc:
                self.runtime_record.failure_reason = f"startup failed: {type(exc).__name__}: {exc}"
                self._persist_record()
                if self.state is RuntimeState.STARTING:
                    self._transition(RuntimeState.FAILED, self.runtime_record.failure_reason)
                self._emit(EventType.RUNTIME_DEGRADED, {"reason": self.runtime_record.failure_reason}, self.runtime_id)
                raise

    def stop(self, reason: str = "graceful shutdown") -> RuntimeRecord:
        with self._lock:
            if self.state is RuntimeState.STOPPED:
                self.accepting_work = False
                self.runtime_record.shutdown_reason = reason
                self.runtime_record.metadata["process_active"] = False
                self._persist_record()
                return self.runtime_record
            self.accepting_work = False
            self._stop_event.set()
            if self.state is not RuntimeState.STOPPING:
                self._transition(RuntimeState.STOPPING, reason)
            self.runtime_record.shutdown_reason = reason
            self.runtime_record.metadata["process_active"] = False
            self._persist_record()
            self._emit(EventType.RUNTIME_SHUTDOWN, {"runtime_id": self.runtime_id, "reason": reason}, self.runtime_id)
            self._transition(RuntimeState.STOPPED, "final state persisted")
            self._started_in_process = False
            return self.runtime_record

    shutdown = stop

    def kill_switch(self, reason: str = "emergency stop") -> RuntimeRecord:
        self.kill_switch_active = True
        self.runtime_record.metadata["kill_switch"] = True
        self._emit(EventType.RUNTIME_KILL_SWITCH, {"runtime_id": self.runtime_id, "reason": reason}, self.runtime_id)
        return self.stop(reason)

    def clear_kill_switch(self) -> None:
        raise PermissionError("kill switch cannot be removed through normal runtime operation")

    def pause(self, reason: str = "runtime paused") -> RuntimeRecord:
        with self._lock:
            if self.state is RuntimeState.PAUSED:
                return self.runtime_record
            self._transition(RuntimeState.PAUSED, reason)
            self.accepting_work = False
            self._emit(EventType.RUNTIME_STATE_CHANGED, {"state": RuntimeState.PAUSED.value, "reason": reason}, self.runtime_id)
            return self.runtime_record

    def resume(self) -> RuntimeRecord:
        with self._lock:
            if self.kill_switch_active:
                raise RuntimeError("runtime kill switch is active")
            if self.state is not RuntimeState.PAUSED:
                return self.runtime_record
            self._transition(RuntimeState.STARTING, "resume requires environment revalidation")
            self.accepting_work = True
            self.runtime_record.metadata.pop("pause_reason", None)
            self._persist_record()
            return self.start()

    def set_safe_mode(self, enabled: bool = True, reason: str = "operator selected safe mode") -> RuntimeRecord:
        self.safe_mode = bool(enabled)
        self.runtime_record.metadata["safe_mode"] = self.safe_mode
        self._emit(EventType.RUNTIME_SAFE_MODE, {"enabled": self.safe_mode, "reason": reason}, self.runtime_id)
        self._persist_record()
        return self.runtime_record

    def enqueue_task(self, goal: str, priority: TaskPriority | str = TaskPriority.NORMAL, source: TaskSource | str = TaskSource.USER, dependencies: Iterable[str] | None = None, deadline: str | None = None, resource_budget: dict[str, Any] | None = None, approval_requirement: bool | str = False, retry_budget: int | None = None, metadata: dict[str, Any] | None = None) -> RuntimeTask:
        with self._lock:
            if self.kill_switch_active:
                raise RuntimeError("runtime kill switch is active")
            if not self.accepting_work and self.state not in {RuntimeState.STOPPED, RuntimeState.STARTING}:
                raise RuntimeError("runtime is not accepting new work")
            task = RuntimeTask(new_id("rtask"), goal, TaskPriority(priority), TaskSource(source), dependencies=list(dependencies or []), deadline=deadline, resource_budget=dict(resource_budget or {}), approval_requirement=approval_requirement, retry_budget=self.limits.max_retry_count if retry_budget is None else retry_budget, agent_version=__version__, metadata=dict(metadata or {}))
            if self.runtime_record.current_environment:
                task.environment_version = self.runtime_record.current_environment
            task = self.queue.enqueue(task)
            self.event_loop.wake("task_arrived", {"task_id": task.task_id})
            self._emit(EventType.RUNTIME_TASK_QUEUED, {"task": task.to_dict()}, task.task_id)
            return task

    queue_task = enqueue_task

    def enqueue_external_operation(self, operation_id: str, priority: TaskPriority | str = TaskPriority.NORMAL, dependencies: Iterable[str] | None = None, deadline: str | None = None, resource_budget: dict[str, Any] | None = None) -> RuntimeTask:
        if self.external_integrations is None:
            raise RuntimeError("external integration manager is not configured")
        row = self.store.integration_operation_by_id(operation_id)
        if not row:
            raise KeyError(operation_id)
        from .external import integration_operation_from_row
        operation, _ = integration_operation_from_row(row)
        return self.enqueue_task(f"external operation {operation.operation} for integration {operation.integration_id}", priority=priority, source=TaskSource.EXTERNAL, dependencies=dependencies, deadline=deadline, resource_budget=resource_budget, metadata={"external_operation_id": operation.operation_id})

    queue_external_operation = enqueue_external_operation

    def enqueue_specialist_task(self, specialist_task_id: str, priority: TaskPriority | str = TaskPriority.NORMAL, dependencies: Iterable[str] | None = None, deadline: str | None = None, resource_budget: dict[str, Any] | None = None) -> RuntimeTask:
        if self.specialist_delegation is None:
            raise RuntimeError("specialist delegation engine is not configured")
        row = self.store.specialist_task_by_id(specialist_task_id)
        if not row:
            raise KeyError(specialist_task_id)
        from .specialist import specialist_task_from_row
        specialist_task = specialist_task_from_row(row)
        contract_row = self.store.specialist_contract_by_task(specialist_task_id)
        if not contract_row:
            raise KeyError("specialist task contract not found")
        from .specialist import SpecialistRisk, specialist_contract_from_row
        contract = specialist_contract_from_row(contract_row)
        if self.kill_switch_active:
            raise RuntimeError("runtime kill switch is active")
        task = self.enqueue_task(specialist_task.goal, priority=priority, source=TaskSource.SPECIALIST, dependencies=dependencies or contract.dependencies, deadline=deadline or contract.deadline, resource_budget=resource_budget or contract.resource_limits, approval_requirement=contract.risk.requires_approval, metadata={"specialist_task_id": specialist_task_id, "specialist_id": specialist_task.specialist_id, "read_only": contract.risk is SpecialistRisk.READ_ONLY})
        self._emit(EventType.SPECIALIST_TASK_QUEUED, {"runtime_task_id": task.task_id, "specialist_task_id": specialist_task_id, "parent_task_id": specialist_task.parent_task_id}, task.task_id)
        return task

    queue_specialist_task = enqueue_specialist_task

    def enqueue_model_inference(self, request: Any, priority: TaskPriority | str = TaskPriority.NORMAL, dependencies: Iterable[str] | None = None, deadline: str | None = None, resource_budget: dict[str, Any] | None = None) -> RuntimeTask:
        if self.model_intelligence is None:
            raise RuntimeError("model intelligence is not configured")
        request_id = str(getattr(request, "correlation_id", ""))
        if not request_id:
            raise ValueError("model inference request requires a correlation ID")
        errors = request.validate() if hasattr(request, "validate") else ["model inference request is invalid"]
        if errors:
            raise ValueError("; ".join(errors))
        self._model_requests[request_id] = request
        task = self.enqueue_task("model inference for " + str(getattr(request, "purpose", "bounded task")), priority=priority, source=TaskSource.MODEL, dependencies=dependencies, deadline=deadline, resource_budget=resource_budget or getattr(request, "resource_limits", {}), metadata={"model_request_id": request_id, "model_id": getattr(request, "model_id", ""), "read_only": str(getattr(request, "risk", "low")) == "low"})
        self._emit(EventType.MODEL_REQUEST_VALIDATED, {"runtime_task_id": task.task_id, "request_id": request_id, "model_id": getattr(request, "model_id", "")}, task.task_id)
        return task

    queue_model_inference = enqueue_model_inference

    def enqueue_learning_cycle(self, priority: TaskPriority | str = TaskPriority.BACKGROUND, resource_budget: dict[str, Any] | None = None) -> RuntimeTask:
        if self.adaptive_learning is None:
            raise RuntimeError("adaptive learning is not configured")
        return self.enqueue_task("bounded adaptive learning cycle", priority=priority, source=TaskSource.LEARNING, resource_budget=resource_budget or {"max_records": 200}, metadata={"learning_cycle": True, "read_only": True})

    queue_learning_cycle = enqueue_learning_cycle

    def enqueue_self_model_operation(self, operation: str, goal: str = "bounded self-model operation", payload: dict[str, Any] | None = None, priority: TaskPriority | str = TaskPriority.BACKGROUND, resource_budget: dict[str, Any] | None = None) -> RuntimeTask:
        if self.self_model is None:
            raise RuntimeError("self-model is not configured")
        metadata = {"self_model_operation": str(operation), "self_model_payload": dict(payload or {}), "read_only": True}
        return self.enqueue_task(goal, priority=priority, source=TaskSource.SELF_MODEL, resource_budget=resource_budget, metadata=metadata)

    queue_self_model_operation = enqueue_self_model_operation
    def enqueue_strategic_cycle(self, goal_ids: Iterable[str] | None = None, priority: TaskPriority | str = TaskPriority.BACKGROUND, resource_budget: dict[str, Any] | None = None) -> RuntimeTask:
        if self.strategic_autonomy is None:
            raise RuntimeError("strategic autonomy is not configured")
        return self.enqueue_task("bounded strategic autonomy cycle", priority=priority, source=TaskSource.STRATEGIC, resource_budget=resource_budget or {"max_goals": 3}, metadata={"strategic_cycle": True, "goal_ids": list(goal_ids or [])[:8], "read_only": True})

    queue_strategic_cycle = enqueue_strategic_cycle
    enqueue_self_model_refresh = lambda self, **kwargs: self.enqueue_self_model_operation("refresh", "bounded self-model refresh", **kwargs)
    enqueue_self_diagnostics = lambda self, **kwargs: self.enqueue_self_model_operation("diagnostics", "bounded self-diagnostics", **kwargs)
    enqueue_self_consistency = lambda self, **kwargs: self.enqueue_self_model_operation("consistency", "bounded self-model consistency check", **kwargs)
    enqueue_meta_reasoning = lambda self, goal, **kwargs: self.enqueue_self_model_operation("meta_reason", goal, payload={"goal": goal, **dict(kwargs.pop("payload", {}) or {})}, **kwargs)

    def run_learning_cycle(self, resource_budget: int | None = None) -> Any:
        if self.adaptive_learning is None:
            raise RuntimeError("adaptive learning is not configured")
        if self.kill_switch_active:
            raise RuntimeError("runtime kill switch is active")
        if hasattr(self.adaptive_learning, "set_safe_mode"): self.adaptive_learning.set_safe_mode(self.safe_mode)
        if hasattr(self.adaptive_learning, "kill_switch"): self.adaptive_learning.kill_switch = self.kill_switch_active
        return self.adaptive_learning.run_cycle(resource_budget=resource_budget)

    def resume_external_operation(self, operation_id: str) -> list[RuntimeTask]:
        resumed: list[RuntimeTask] = []
        for task in self.tasks():
            if task.metadata.get("external_operation_id") != operation_id or task.status is not RuntimeTaskStatus.WAITING:
                continue
            task.status = RuntimeTaskStatus.READY
            task.last_error = None
            task.progress = "approval_received"
            self.queue.update(task)
            self.event_loop.wake("external_approval", {"task_id": task.task_id, "operation_id": operation_id})
            self._emit(EventType.RUNTIME_TASK_RESUMED, {"task_id": task.task_id, "external_operation_id": operation_id, "reason": "external approval received"}, task.task_id)
            resumed.append(task)
        return resumed

    def schedule_task(self, schedule: RuntimeSchedule) -> RuntimeSchedule:
        result = self.scheduler.register(schedule)
        self._emit(EventType.RUNTIME_TASK_QUEUED, {"schedule_id": schedule.schedule_id, "scheduled": True}, self.runtime_id)
        return result

    def cancel_task(self, task_id: str, reason: str = "Cancelled by user") -> RuntimeTask:
        task = self.queue.cancel(task_id, reason)
        self._emit(EventType.RUNTIME_TASK_CANCELLED, {"task_id": task_id, "reason": reason}, task_id)
        return task

    def pause_task(self, task_id: str, reason: str = "Paused by user") -> RuntimeTask:
        task = self.queue.pause(task_id, reason)
        self._emit(EventType.RUNTIME_TASK_PAUSED, {"task_id": task_id, "reason": reason}, task_id)
        return task

    def resume_task(self, task_id: str) -> RuntimeTask:
        task = self.queue.resume(task_id)
        self._emit(EventType.RUNTIME_TASK_RESUMED, {"task_id": task_id}, task_id)
        return task

    def approve_task(self, task_id: str, actor: str = "human", scope_hash: str | None = None, reason: str = "") -> RuntimeApproval:
        if actor.lower() in {"runtime", "system", "agent", "autonomous", "orchestrator"}:
            raise PermissionError("runtime cannot self-approve a task")
        task = self.queue.get(task_id)
        if not task:
            raise KeyError(task_id)
        if not task.approval_requirement:
            raise ValueError("task does not require runtime approval")
        expected = str(task.metadata.get("approval_context_hash", ""))
        actual = self._approval_scope(task)
        supplied = scope_hash or actual
        if expected and supplied != expected:
            raise PermissionError("approval context is stale; reauthorization is required")
        if supplied != actual:
            raise PermissionError("approval context does not match current task and environment")
        approval = RuntimeApproval(new_id("rtapproval"), task.task_id, "approved", actor, supplied, reason or "Explicit human approval", metadata={"goal": task.goal, "environment_version": self.runtime_record.current_environment})
        self.store.save_runtime_approval(approval)
        task.metadata["approval_status"] = "approved"
        task.metadata["approval_context_hash"] = supplied
        task.status = RuntimeTaskStatus.READY
        self.queue.update(task)
        self._emit(EventType.RUNTIME_APPROVAL_RECEIVED, {"task_id": task_id, "approval_id": approval.approval_id, "actor": actor}, task_id)
        if self.state is RuntimeState.WAITING_APPROVAL:
            self._transition(RuntimeState.READY, "exact task approval received")
        return approval

    def run_cycle(self, now: str | None = None) -> RuntimeCycleResult:
        with self._lock:
            if self.state is RuntimeState.STOPPED:
                self.start()
            if self.state in {RuntimeState.PAUSED, RuntimeState.FAILED, RuntimeState.STOPPING}:
                return RuntimeCycleResult(new_id("rtcycle"), self.state.value, stopped_reason="runtime_not_runnable")
            result = RuntimeCycleResult(new_id("rtcycle"), self.state.value)
            try:
                self.event_loop.drain(self.event_loop.pending())
                self.heartbeat.beat()
                self.scheduler.tick(now)
                self._transition(RuntimeState.OBSERVING, "cycle environment observation")
                before = self.runtime_record.current_environment
                environment = self._observe("runtime cycle")
                self.runtime_record.current_environment = environment.environment.environment_version
                result.environment_changed = bool(before and before != self.runtime_record.current_environment)
                if result.environment_changed:
                    self._emit(EventType.ENVIRONMENT_CHANGED, {"before": before, "after": self.runtime_record.current_environment, "reason": "runtime cycle observation"}, self.runtime_id)
                self._transition(RuntimeState.PLANNING, "select bounded next task")
                ready = self.scheduler.ready_tasks(now)
                result.tasks_considered = len(ready)
                for task in ready[: self.limits.max_tasks_per_cycle]:
                    if self.kill_switch_active or self.state in {RuntimeState.PAUSED, RuntimeState.STOPPING}:
                        result.stopped_reason = "operator_stop"
                        break
                    outcome = self._process_task(task)
                    if outcome == "completed":
                        result.tasks_completed += 1
                    elif outcome == "failed":
                        result.tasks_failed += 1
                    elif outcome == "waiting":
                        result.tasks_waiting += 1
                    elif outcome == "blocked":
                        result.tasks_blocked += 1
                    elif outcome == "recovered":
                        result.tasks_recovered += 1
                    if outcome in {"started", "completed", "failed", "waiting", "blocked", "recovered"}:
                        result.tasks_started += 1
                if self.state not in {RuntimeState.WAITING_APPROVAL, RuntimeState.PAUSED, RuntimeState.DEGRADED, RuntimeState.STOPPING}:
                    self._transition(RuntimeState.READY, "bounded cycle complete")
                self._persist_record()
                health = self.heartbeat.beat()
                if health.status is RuntimeHealthStatus.FAILED:
                    self._transition(RuntimeState.DEGRADED, "heartbeat detected failed runtime health")
                    result.stopped_reason = "health_degraded"
                result.state = self.state.value
                return result
            except Exception as exc:
                result.failures.append(f"{type(exc).__name__}: {exc}")
                self.runtime_record.failure_reason = result.failures[-1]
                self._persist_record()
                if self.state not in {RuntimeState.STOPPING, RuntimeState.STOPPED, RuntimeState.FAILED}:
                    self._transition(RuntimeState.DEGRADED, "runtime cycle failed safely")
                result.state = self.state.value
                result.stopped_reason = "cycle_failure"
                return result

    run_once = run_cycle

    def run_forever(self, max_cycles: int | None = None, sleep_seconds: float = 0.25) -> list[RuntimeCycleResult]:
        self.start()
        results: list[RuntimeCycleResult] = []
        started = time.monotonic()
        cycles = 0
        while not self._stop_event.is_set() and self.state not in {RuntimeState.STOPPED, RuntimeState.FAILED}:
            if max_cycles is not None and cycles >= max_cycles:
                break
            if time.monotonic() - started >= self.limits.max_total_runtime:
                break
            results.append(self.run_cycle())
            cycles += 1
            if self.state in {RuntimeState.PAUSED, RuntimeState.DEGRADED}:
                break
            if sleep_seconds > 0:
                time.sleep(min(float(sleep_seconds), 5.0))
        return results

    def status(self) -> dict[str, Any]:
        health = self.heartbeat.check()
        tasks = self.queue.list(limit=self.limits.max_queue_size + 1)
        pending_approvals = self.store.find_runtime_approvals(status="pending", limit=100)
        return {"runtime": self.runtime_record.to_dict(), "uptime": health.uptime_seconds, "current_task": self.runtime_record.current_task, "queue_depth": self.queue.depth(), "heartbeat": health.to_dict(), "health": health.status.value, "safe_mode": self.safe_mode, "resource_state": {"limits": self.limits.to_dict(), "pressure": health.resource_pressure}, "pending_wakeups": self.event_loop.pending(), "environment_version": self.runtime_record.current_environment, "agent_version": self.runtime_record.agent_version, "pending_approvals": len(pending_approvals), "blocked_tasks": sum(1 for task in tasks if task.status is RuntimeTaskStatus.BLOCKED), "recent_failures": [task.last_error for task in tasks if task.last_error][-10:]}

    def health(self) -> RuntimeHealth:
        return self.heartbeat.check()

    def tasks(self, status: RuntimeTaskStatus | str | None = None, limit: int = 100) -> list[RuntimeTask]:
        return self.queue.list(status, limit)

    def task(self, task_id: str) -> RuntimeTask | None:
        return self.queue.get(task_id)

    def _process_task(self, task: RuntimeTask) -> str:
        if task.status in {RuntimeTaskStatus.CANCELLED, RuntimeTaskStatus.EXPIRED, RuntimeTaskStatus.COMPLETED}:
            return task.status.value
        if task.deadline and parse_time(task.deadline) <= datetime.now(timezone.utc):
            task.status = RuntimeTaskStatus.EXPIRED
            task.last_error = "Task deadline expired before execution."
            self.queue.update(task)
            return "expired"
        allowed, reason = self.resources.can_run(task)
        if not allowed:
            task.status = RuntimeTaskStatus.WAITING
            task.last_error = reason
            self.queue.update(task)
            self._emit(EventType.RUNTIME_TASK_WAITING, {"task_id": task.task_id, "reason": reason}, task.task_id)
            return "waiting"
        if task.environment_version and task.environment_version != self.runtime_record.current_environment:
            task.status = RuntimeTaskStatus.READY
            task.last_error = "environment changed; plan and any approval must be revalidated"
            task.metadata["environment_invalidated"] = True
            if task.metadata.get("approval_status") == "approved":
                task.metadata["approval_status"] = "pending"
                task.metadata.pop("approval_context_hash", None)
            task.environment_version = self.runtime_record.current_environment
            self.queue.update(task)
            self._emit(EventType.RUNTIME_REPLAN, {"task_id": task.task_id, "reason": task.last_error, "approval_invalidated": bool(task.approval_requirement)}, task.task_id)
        if task.approval_requirement and task.metadata.get("approval_status") != "approved":
            task.status = RuntimeTaskStatus.WAITING
            task.metadata["approval_context_hash"] = self._approval_scope(task)
            task.metadata["approval_status"] = "pending"
            self.queue.update(task)
            approval = RuntimeApproval(new_id("rtapproval"), task.task_id, "pending", "runtime", str(task.metadata["approval_context_hash"]), "Exact task/context approval required", metadata={"goal": task.goal, "environment_version": self.runtime_record.current_environment})
            self.store.save_runtime_approval(approval)
            self._emit(EventType.RUNTIME_TASK_WAITING, {"task_id": task.task_id, "approval_id": approval.approval_id, "reason": "approval required", "scope_hash": approval.scope_hash}, task.task_id)
            self._transition(RuntimeState.WAITING_APPROVAL, "task requires human approval")
            return "waiting"
        if task.metadata.get("strategic_cycle"):
            if self.strategic_autonomy is None:
                task.status = RuntimeTaskStatus.BLOCKED; task.last_error = "strategic autonomy is not configured"; self.queue.update(task); return "blocked"
            if self.kill_switch_active or self.safe_mode:
                task.status = RuntimeTaskStatus.BLOCKED if self.kill_switch_active else RuntimeTaskStatus.WAITING; task.last_error = "strategic cycle blocked by runtime safety state"; self.queue.update(task); return "blocked" if self.kill_switch_active else "waiting"
            self._transition(RuntimeState.EXECUTING, "bounded strategic cycle admitted by Runtime")
            try:
                result = self.strategic_autonomy.strategic_cycle(task.metadata.get("goal_ids") or None, {"runtime_id": self.runtime_id, "runtime_limits": {"max_goals": int(task.resource_budget.get("max_goals", 3))}})
                task.metadata["strategic_cycle_result"] = result
                if result.get("status") == "completed":
                    task.status = RuntimeTaskStatus.COMPLETED; task.progress = "completed"; self.queue.update(task); self._emit(EventType.STRATEGIC_CYCLE_COMPLETED, {"task_id": task.task_id, "goal_count": result.get("goal_count", 0)}, task.task_id); return "completed"
                task.status = RuntimeTaskStatus.BLOCKED; task.progress = "blocked"; task.last_error = str(result.get("reason", "strategic cycle blocked")); self.queue.update(task); self._emit(EventType.STRATEGIC_CYCLE_BLOCKED, {"task_id": task.task_id, "reason": task.last_error}, task.task_id); return "blocked"
            except Exception as exc:
                task.status = RuntimeTaskStatus.FAILED; task.progress = "failed"; task.last_error = f"{type(exc).__name__}: {exc}"; self.queue.update(task); return "failed"
        if task.metadata.get("self_model_operation"):
            if self.self_model is None:
                task.status = RuntimeTaskStatus.BLOCKED; task.last_error = "self-model is not configured"; self.queue.update(task); return "blocked"
            if self.kill_switch_active:
                task.status = RuntimeTaskStatus.BLOCKED; task.last_error = "self-model operation blocked by runtime kill switch"; self.queue.update(task); return "blocked"
            if hasattr(self.self_model, "set_safe_mode"): self.self_model.set_safe_mode(self.safe_mode)
            self.self_model.kill_switch = self.kill_switch_active
            self._transition(RuntimeState.EXECUTING, "bounded self-model operation admitted by Runtime")
            self._transition(RuntimeState.LEARNING, "bounded self-model operation")
            operation = str(task.metadata.get("self_model_operation")); payload = dict(task.metadata.get("self_model_payload", {}))
            try:
                if operation == "refresh": result = self.self_model.refresh("Runtime bounded refresh")
                elif operation == "diagnostics": result = self.self_model.diagnostics()
                elif operation == "consistency": result = self.self_model.consistency_check()
                elif operation == "meta_reason" and self.meta_reasoning is not None: result = self.meta_reasoning.reason(str(payload.get("goal", task.goal)), payload)
                else: raise RuntimeError("unsupported self-model operation")
                task.metadata["self_model_result"] = result.to_dict() if hasattr(result, "to_dict") else result
                task.status = RuntimeTaskStatus.COMPLETED; task.progress = "completed"; self.queue.update(task); self._emit(EventType.RUNTIME_TASK_COMPLETED, {"task_id": task.task_id, "self_model_operation": operation}, task.task_id); return "completed"
            except Exception as exc:
                task.status = RuntimeTaskStatus.FAILED; task.progress = "failed"; task.last_error = f"{type(exc).__name__}: {exc}"; self.queue.update(task); self._emit(EventType.RUNTIME_TASK_FAILED, {"task_id": task.task_id, "error": task.last_error}, task.task_id); return "failed"
        if task.metadata.get("learning_cycle"):
            if self.adaptive_learning is None:
                task.status = RuntimeTaskStatus.BLOCKED; task.last_error = "adaptive learning is not configured"; self.queue.update(task); return "blocked"
            if self.kill_switch_active:
                task.status = RuntimeTaskStatus.BLOCKED; task.last_error = "learning cycle blocked by runtime kill switch"; self.queue.update(task); return "blocked"
            if hasattr(self.adaptive_learning, "set_safe_mode"): self.adaptive_learning.set_safe_mode(self.safe_mode)
            if hasattr(self.adaptive_learning, "kill_switch"): self.adaptive_learning.kill_switch = self.kill_switch_active
            self._transition(RuntimeState.EXECUTING, "bounded learning cycle admitted by Runtime")
            self._transition(RuntimeState.LEARNING, "bounded adaptive learning cycle")
            cycle = self.adaptive_learning.run_cycle(resource_budget=int(task.resource_budget.get("max_records", 200)))
            task.metadata["learning_cycle"] = cycle.to_dict()
            if cycle.status is CycleStatus.COMPLETED:
                task.status = RuntimeTaskStatus.COMPLETED; task.progress = "completed"; self.queue.update(task); self._emit(EventType.RUNTIME_TASK_COMPLETED, {"task_id": task.task_id, "learning_cycle_id": cycle.cycle_id}, task.task_id); return "completed"
            task.status = RuntimeTaskStatus.BLOCKED if cycle.status is CycleStatus.BLOCKED else RuntimeTaskStatus.FAILED; task.progress = "blocked" if task.status is RuntimeTaskStatus.BLOCKED else "failed"; task.last_error = cycle.reason; self.queue.update(task); return "blocked" if task.status is RuntimeTaskStatus.BLOCKED else "failed"
        model_request_id = task.metadata.get("model_request_id")
        if model_request_id:
            request = self._model_requests.get(str(model_request_id))
            if self.model_intelligence is None or request is None:
                task.status = RuntimeTaskStatus.BLOCKED
                task.last_error = "model inference request is unavailable after restart or model intelligence is not configured"
                self.queue.update(task)
                self._emit(EventType.MODEL_REQUEST_BLOCKED, {"task_id": task.task_id, "reason": task.last_error}, task.task_id)
                return "blocked"
            if self.safe_mode and not task.metadata.get("read_only", False):
                task.status = RuntimeTaskStatus.WAITING
                task.last_error = "safe mode blocks side-effecting model workflow"
                self.queue.update(task)
                return "waiting"
            self._transition(RuntimeState.EXECUTING, "bounded model inference through ModelIntelligence")
            response = self.model_intelligence.infer(request)
            task.metadata["model_response"] = response.to_dict()
            task.metadata["verified"] = bool(response.verified)
            task.metadata["verification_required"] = not bool(response.verified)
            self._model_requests.pop(str(model_request_id), None)
            if response.success:
                task.status = RuntimeTaskStatus.COMPLETED
                task.progress = "completed"
                self.queue.update(task)
                self._emit(EventType.RUNTIME_TASK_COMPLETED, {"task_id": task.task_id, "model_id": request.model_id, "verified": response.verified}, task.task_id)
                return "completed"
            task.status = RuntimeTaskStatus.BLOCKED if response.status is InferenceStatus.BLOCKED else RuntimeTaskStatus.FAILED
            task.progress = "blocked" if task.status is RuntimeTaskStatus.BLOCKED else "failed"
            task.last_error = response.error or response.status.value
            self.queue.update(task)
            self._emit(EventType.RUNTIME_TASK_FAILED, {"task_id": task.task_id, "model_id": request.model_id, "error": task.last_error}, task.task_id)
            return "failed"
        specialist_task_id = task.metadata.get("specialist_task_id")
        if specialist_task_id:
            if self.specialist_delegation is None:
                task.status = RuntimeTaskStatus.BLOCKED
                task.last_error = "specialist delegation engine is not configured"
                self.queue.update(task)
                return "blocked"
            if self.safe_mode and task.metadata.get("read_only") is not True:
                task.status = RuntimeTaskStatus.WAITING
                task.last_error = "safe mode blocks side-effecting specialist execution"
                self.queue.update(task)
                self._emit(EventType.RUNTIME_TASK_WAITING, {"task_id": task.task_id, "reason": task.last_error, "specialist_task_id": specialist_task_id}, task.task_id)
                return "waiting"
            task.progress = "specialist_in_progress"
            self.queue.update(task)
            self._transition(RuntimeState.EXECUTING, "delegate specialist through bounded specialist engine")
            specialist_output = self.specialist_delegation.execute_task(str(specialist_task_id))
            task.metadata["specialist_result"] = specialist_output.to_dict()
            task.metadata["verified"] = specialist_output.verification_status.value == "verified"
            task.metadata["verification_required"] = not task.metadata["verified"]
            if specialist_output.success:
                task.status = RuntimeTaskStatus.COMPLETED
                task.progress = "completed"
                self.queue.update(task)
                self._emit(EventType.RUNTIME_TASK_COMPLETED, {"task_id": task.task_id, "specialist_task_id": specialist_task_id, "verified": task.metadata["verified"]}, task.task_id)
                return "completed"
            task.status = RuntimeTaskStatus.FAILED
            task.progress = "failed"
            task.last_error = specialist_output.error or "specialist task failed"
            self.queue.update(task)
            self._emit(EventType.RUNTIME_TASK_FAILED, {"task_id": task.task_id, "specialist_task_id": specialist_task_id, "failure": task.last_error}, task.task_id)
            return "failed"
        external_operation_id = task.metadata.get("external_operation_id")
        if external_operation_id:
            if self.external_integrations is None:
                return self._block_external_task(task, "external integration manager is not configured")
            from .external import ExternalOperationRisk, ExternalOperationStatus, integration_operation_from_row
            operation_row = self.store.integration_operation_by_id(str(external_operation_id))
            if not operation_row:
                return self._block_external_task(task, "external operation record is missing")
            external_operation, _ = integration_operation_from_row(operation_row)
            if self.safe_mode and external_operation.risk_level is not ExternalOperationRisk.READ_ONLY:
                return self._block_external_task(task, "safe mode blocks side-effecting external operations", waiting=True)
            task.progress = "in_progress"
            self.queue.update(task)
            self._transition(RuntimeState.EXECUTING, "delegate external operation through Kernel gateway")
            result = self.kernel.run_external_operation(str(external_operation_id))
            task.metadata["external_result"] = result.to_dict()
            if result.status is ExternalOperationStatus.WAITING_APPROVAL:
                task.status = RuntimeTaskStatus.WAITING
                task.progress = "blocked"
                task.last_error = result.error
                self.queue.update(task)
                self._emit(EventType.RUNTIME_TASK_WAITING, {"task_id": task.task_id, "reason": result.error, "external_operation_id": external_operation_id}, task.task_id)
                return "waiting"
            if result.status is ExternalOperationStatus.SUCCEEDED and result.output_schema_valid:
                task.status = RuntimeTaskStatus.COMPLETED
                task.progress = "completed"
                task.metadata["verified"] = False
                task.metadata["verification_required"] = True
                self.queue.update(task)
                self._emit(EventType.RUNTIME_TASK_COMPLETED, {"task_id": task.task_id, "external_operation_id": external_operation_id, "verified": False, "schema_valid": True}, task.task_id)
                return "completed"
            task.status = RuntimeTaskStatus.FAILED if result.status not in {ExternalOperationStatus.BLOCKED, ExternalOperationStatus.UNKNOWN} else RuntimeTaskStatus.BLOCKED
            task.progress = "failed" if task.status is RuntimeTaskStatus.FAILED else "blocked"
            task.last_error = result.error or result.failure_class.value
            self.queue.update(task)
            self._emit(EventType.RUNTIME_TASK_FAILED, {"task_id": task.task_id, "external_operation_id": external_operation_id, "status": result.status.value, "failure_class": result.failure_class.value}, task.task_id)
            return "failed"
        if self.safe_mode and not bool(task.metadata.get("read_only", False)):
            task.status = RuntimeTaskStatus.WAITING
            task.last_error = "safe mode restricts side-effecting autonomous execution"
            task.metadata["safe_mode"] = True
            self.queue.update(task)
            self._emit(EventType.RUNTIME_TASK_WAITING, {"task_id": task.task_id, "reason": task.last_error}, task.task_id)
            return "waiting"
        if task.environment_version and task.environment_version != self.runtime_record.current_environment:
            task.status = RuntimeTaskStatus.READY
            task.last_error = "environment changed; plan validity must be revalidated"
            task.metadata["environment_invalidated"] = True
            task.environment_version = self.runtime_record.current_environment
            self.queue.update(task)
            self._emit(EventType.RUNTIME_REPLAN, {"task_id": task.task_id, "reason": task.last_error}, task.task_id)
        task.progress = "in_progress"

        task.last_error = None
        self.queue.update(task)
        self.runtime_record.current_task = task.task_id
        self._metric("tasks_started")
        self._emit(EventType.RUNTIME_TASK_STARTED, {"task_id": task.task_id, "attempt": task.current_attempt, "source": task.source.value}, task.task_id)

        started = time.monotonic()
        try:
            self._transition(RuntimeState.EXECUTING, "delegate task to Cognitive and Kernel authorities")
            if task.metadata.get("goal_id") and task.metadata.get("recovery_required"):
                cognitive_result = self.cognitive.resume(str(task.metadata["goal_id"]))
            else:
                cognitive_result = self.cognitive.run_goal(task.goal, goal_id=task.metadata.get("goal_id"))
            task.metadata["goal_id"] = cognitive_result.goal.goal_id
            if cognitive_result.plan:
                task.plan_id = cognitive_result.plan.plan_id
                self.runtime_record.current_plan = cognitive_result.plan.plan_id
            task.metadata["last_result"] = cognitive_result.to_dict()
            task.metadata["duration_seconds"] = time.monotonic() - started
            task.environment_version = self.runtime_record.current_environment
            mapped = self._record_cognitive_result(task, cognitive_result)
            self._maybe_evolution(task, cognitive_result)
            return mapped
        except Exception as exc:
            failure = self.recovery.classify(error=str(exc))
            mapped = self.recovery.recover(task, failure, f"runtime task execution error: {exc}")
            self._emit(EventType.RUNTIME_TASK_FAILED, {"task_id": task.task_id, "failure_class": failure.value, "error": str(exc), "status": mapped}, task.task_id)
            return "recovered" if mapped in {RuntimeTaskStatus.READY.value, RuntimeTaskStatus.PAUSED.value} else "failed"
        finally:
            self.runtime_record.current_task = None
            self._persist_record()

    def _block_external_task(self, task: RuntimeTask, reason: str, waiting: bool = False) -> str:
        task.status = RuntimeTaskStatus.WAITING if waiting else RuntimeTaskStatus.BLOCKED
        task.progress = "blocked"
        task.last_error = reason
        self.queue.update(task)
        self._emit(EventType.RUNTIME_TASK_WAITING if waiting else EventType.RUNTIME_TASK_FAILED, {"task_id": task.task_id, "reason": reason, "external": True}, task.task_id)
        return "waiting" if waiting else "blocked"

    def _record_cognitive_result(self, task: RuntimeTask, result: CognitiveResult) -> str:
        if result.outcome is CognitiveOutcome.SUCCESS:
            self._transition(RuntimeState.VERIFYING, "Cognitive result requires verification authority")
            self._transition(RuntimeState.LEARNING, "record verified outcome through existing experience and memory")
            task.status = RuntimeTaskStatus.COMPLETED
            task.progress = "completed"
            task.metadata["verified"] = True
            self.queue.update(task)
            self._metric("tasks_completed")
            self._emit(EventType.RUNTIME_TASK_COMPLETED, {"task_id": task.task_id, "goal_id": result.goal.goal_id, "summary": result.summary, "verified": True}, task.task_id)
            return "completed"
        if result.outcome in {CognitiveOutcome.WAITING_FOR_INPUT, CognitiveOutcome.WAITING_FOR_APPROVAL}:
            task.status = RuntimeTaskStatus.WAITING
            task.progress = "blocked"
            task.last_error = result.summary
            self.queue.update(task)
            if result.outcome is CognitiveOutcome.WAITING_FOR_APPROVAL:
                self._transition(RuntimeState.WAITING_APPROVAL, "Cognitive layer is waiting for exact approval")
            self._metric("approval_waits" if result.outcome is CognitiveOutcome.WAITING_FOR_APPROVAL else "tasks_waiting")
            self._emit(EventType.RUNTIME_TASK_WAITING, {"task_id": task.task_id, "outcome": result.outcome.value, "summary": result.summary}, task.task_id)
            return "waiting"
        if result.outcome is CognitiveOutcome.BLOCKED:
            task.status = RuntimeTaskStatus.BLOCKED
            task.progress = "blocked"
            task.last_error = result.summary
            self.queue.update(task)
            self._emit(EventType.RUNTIME_TASK_FAILED, {"task_id": task.task_id, "outcome": result.outcome.value, "summary": result.summary, "status": "blocked"}, task.task_id)
            return "blocked"
        task.progress = "partial" if result.outcome is CognitiveOutcome.PARTIAL else "failed"
        failure = self.recovery.classify(result=result)
        mapped = self.recovery.recover(task, failure, result.summary)
        self._metric("tasks_recovered" if mapped in {RuntimeTaskStatus.READY.value, RuntimeTaskStatus.PAUSED.value} else "tasks_failed")
        self._emit(EventType.RUNTIME_TASK_FAILED, {"task_id": task.task_id, "outcome": result.outcome.value, "failure_class": failure.value, "status": mapped}, task.task_id)
        return "recovered" if mapped in {RuntimeTaskStatus.READY.value, RuntimeTaskStatus.PAUSED.value} else "failed"

    def _maybe_evolution(self, task: RuntimeTask, result: CognitiveResult) -> None:
        if result.outcome not in {CognitiveOutcome.FAILED, CognitiveOutcome.INCONCLUSIVE, CognitiveOutcome.BLOCKED}:
            return
        if task.source is TaskSource.EVOLUTION or task.metadata.get("evolution_checked"):
            return
        task.metadata["evolution_checked"] = True
        self.queue.update(task)
        try:
            cycle = self.evolution.run_cycle(limit=1)
            self.runtime_record.metadata["last_evolution_cycle"] = cycle.to_dict()
        except Exception as exc:
            self.runtime_record.metadata.setdefault("evolution_errors", []).append(type(exc).__name__)

    def _recover_interrupted_tasks(self) -> None:
        for task in self.queue.list(RuntimeTaskStatus.RUNNING, limit=self.limits.max_queue_size + 1):
            task.status = RuntimeTaskStatus.WAITING
            task.last_error = "Previous runtime stopped during execution; actual state requires revalidation."
            task.metadata["recovery_required"] = True
            task.metadata["recovery_reason"] = "stale runtime heartbeat"
            self.queue.update(task)
            self._emit(EventType.RUNTIME_CRASH_RECOVERY, {"task_id": task.task_id, "action": "waiting_for_safe_revalidation"}, task.task_id)

    def _observe(self, reason: str) -> Any:
        world = self.cognitive._get_world_intelligence()
        model = world.observe(reason)
        snapshot = world.create_snapshot(model)
        world.save_observations(model)
        self.runtime_record.current_world_snapshot = snapshot.snapshot_id
        self.runtime_record.last_observation = utc_now()
        self._emit(EventType.ENVIRONMENT_OBSERVED, {"environment_id": model.environment.environment_id, "environment_version": model.environment.environment_version, "snapshot_id": snapshot.snapshot_id, "reason": reason}, self.runtime_id)
        return model

    def _approval_scope(self, task: RuntimeTask) -> str:
        body = {"task_id": task.task_id, "goal": task.goal, "plan_id": task.plan_id, "environment_version": self.runtime_record.current_environment, "agent_version": self.runtime_record.agent_version, "approval_requirement": task.approval_requirement}
        return hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()

    def _architecture_version(self) -> str:
        """Delegated to the shared resolver so both loop paths report one definition (08 P1)."""
        from .sovereign import resolve_architecture_version

        engine = getattr(self.evolution, "metamorphosis", None)
        return resolve_architecture_version(self.store, self.source_root, agent_version=__version__, engine=engine)

    def _validate_database(self) -> None:
        report = self.store.validate_database_integrity()
        self.runtime_record.metadata["database_integrity"] = {"sqlite_integrity": report["sqlite_integrity"], "checked_payload_rows": report["checked_payload_rows"], "validated_at": utc_now()}
        with self.store._connect() as db:
            db.execute("SELECT COUNT(*) FROM runtime_states").fetchone()
            db.execute("SELECT COUNT(*) FROM runtime_tasks").fetchone()

    def _validate_sovereign_boundary(self) -> dict[str, Any]:
        """Verify the protected byte set and the cheap live invariants (07 §1 R1, R7).

        Imported lazily so that the sovereign check is reachable even when the rest of the
        runtime is not, and so that a startup path stays free of the expensive registry.
        """
        from .sovereign import run_invariants

        results = run_invariants(cheap_only=True)
        failures = [item for item in results if not item.ok]
        summary = {
            "checked_at": utc_now(),
            "checks": [item.code for item in results],
            "protected_files": len(results[0].evidence.get("digests", {})) if results else 0,
            "failures": [{"code": item.code, "detail": item.detail} for item in failures],
        }
        if failures:
            self._emit(EventType.SOVEREIGN_DRIFT_DETECTED, {"detail": summary["failures"], "runtime_id": self.runtime_id}, self.runtime_id)
            detail = "; ".join(f"{item.code}: {item.detail}" for item in failures)
            accepted, override_name = _sovereign_drift_override()
            if accepted:
                # A developer editing the protected set legitimately would otherwise be unable
                # to run the suite at all, and the predictable response to that is to delete the
                # check. So the override exists, it is loud, and it is permanently in the audit:
                # it can never be mistaken for a clean start.
                summary["drift_accepted"] = True
                self.runtime_record.metadata["sovereign_boundary"] = summary
                self._emit(
                    EventType.SOVEREIGN_DRIFT_ACCEPTED,
                    {"detail": detail, "override": override_name, "warning": "protected files differ from the published manifest; this runtime is NOT a verified build"},
                    self.runtime_id,
                )
                print(f"evo: sovereign drift accepted via {override_name}: {detail}", file=sys.stderr)
                return summary
            raise RuntimeError(f"sovereign boundary check failed: {detail}")
        self.runtime_record.metadata["sovereign_boundary"] = summary
        self._emit(EventType.SOVEREIGN_VERIFIED, {"protected_files": summary["protected_files"], "checks": summary["checks"]}, self.runtime_id)
        return summary

    def sovereign_report(self, *, full: bool = False) -> dict[str, Any]:
        """The protected-set and invariant state, for ``evo status`` and the desktop bridge.

        ``full=True`` runs the whole registry (source scans), so it is a diagnostic call,
        not a hot path.
        """
        from .sovereign import run_invariants

        results = run_invariants(cheap_only=not full)
        return {
            "cached": dict(self.runtime_record.metadata.get("sovereign_boundary", {})),
            "ok": all(item.ok for item in results),
            "results": [item.to_dict() for item in results],
        }

    def _validate_architecture(self) -> None:
        if not self.source_root.is_dir():
            raise FileNotFoundError(self.source_root)
        self._architecture_version()

    def _load_record(self) -> RuntimeRecord:
        row = self.store.runtime_state_by_id(self.runtime_id)
        if not row:
            return RuntimeRecord(self.runtime_id, self.RUNTIME_VERSION, __version__, "")
        try:
            payload = json.loads(row.get("payload", "{}")) if isinstance(row.get("payload"), str) else dict(row)
            payload["state"] = RuntimeState(payload.get("state", row.get("state", RuntimeState.STOPPED.value)))
            payload.setdefault("metadata", {})
            return RuntimeRecord(**{key: payload[key] for key in RuntimeRecord.__dataclass_fields__ if key in payload})
        except (TypeError, ValueError, json.JSONDecodeError, KeyError):
            return RuntimeRecord(self.runtime_id, self.RUNTIME_VERSION, __version__, "", state=RuntimeState.FAILED, failure_reason="corrupt persisted runtime record")

    def _persist_record(self) -> None:
        self.store.save_runtime_state(self.runtime_record)

    def _transition(self, target: RuntimeState, reason: str) -> None:
        current = self.state
        if current is target:
            self.runtime_record.metadata["last_transition_reason"] = reason
            self._persist_record()
            return
        LifecycleManager.validate(current, target)
        self.runtime_record.state = target
        self.runtime_record.metadata["last_transition_reason"] = reason
        self._persist_record()
        self._emit(EventType.RUNTIME_STATE_CHANGED, {"from": current.value, "to": target.value, "reason": reason}, self.runtime_id)

    def _metric(self, name: str, amount: int = 1) -> None:
        metrics = self.runtime_record.metadata.setdefault("metrics", {})
        metrics[name] = int(metrics.get(name, 0)) + int(amount)
        self._persist_record()

    def _emit(self, event_type: EventType, payload: dict[str, Any], task_id: str | None = None) -> None:
        try:
            self.store.append_event(Event(task_id or self.runtime_id, event_type, payload))
        except Exception:
            # Event persistence failure must not create a second execution authority.
            self.runtime_record.metadata.setdefault("audit_errors", []).append(event_type.value)


def parse_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def runtime_task_from_row(row: dict[str, Any]) -> RuntimeTask:
    payload = row.get("payload", {})
    if isinstance(payload, str):
        payload = json.loads(payload)
    data = dict(payload or row)
    data.setdefault("task_id", row.get("task_id"))
    data.setdefault("goal", row.get("goal", ""))
    data.setdefault("fingerprint", row.get("fingerprint", ""))
    data.setdefault("status", row.get("status", RuntimeTaskStatus.QUEUED.value))
    data.setdefault("priority", row.get("priority", TaskPriority.NORMAL.value))
    data.setdefault("source", row.get("source", TaskSource.USER.value))
    data["status"] = RuntimeTaskStatus(data["status"])
    data["priority"] = TaskPriority(data["priority"])
    data["source"] = TaskSource(data["source"])
    for key in ("dependencies", "resource_budget", "metadata"):
        if isinstance(data.get(key), str):
            data[key] = json.loads(data[key])
    data.setdefault("dependencies", [])
    data.setdefault("resource_budget", {})
    data.setdefault("metadata", {})
    return RuntimeTask(**{key: data[key] for key in RuntimeTask.__dataclass_fields__ if key in data})


def runtime_schedule_from_row(row: dict[str, Any]) -> RuntimeSchedule:
    payload = row.get("payload", {})
    if isinstance(payload, str):
        payload = json.loads(payload)
    data = dict(payload or row)
    data.setdefault("schedule_id", row.get("schedule_id"))
    data.setdefault("goal", row.get("goal", ""))
    data.setdefault("kind", row.get("kind", ScheduleKind.ONCE.value))
    data.setdefault("priority", row.get("priority", TaskPriority.NORMAL.value))
    data.setdefault("source", row.get("source", TaskSource.SCHEDULE.value))
    for key in ("condition", "dependencies", "resource_budget", "metadata"):
        if isinstance(data.get(key), str):
            data[key] = json.loads(data[key])
    data["kind"] = ScheduleKind(data["kind"])
    data["priority"] = TaskPriority(data["priority"])
    data["source"] = TaskSource(data["source"])
    return RuntimeSchedule(**{key: data[key] for key in RuntimeSchedule.__dataclass_fields__ if key in data})


def runtime_approval_from_row(row: dict[str, Any]) -> RuntimeApproval:
    payload = row.get("payload", {})
    if isinstance(payload, str):
        payload = json.loads(payload)
    data = dict(payload or row)
    for key in ("metadata",):
        if isinstance(data.get(key), str):
            data[key] = json.loads(data[key])
    return RuntimeApproval(**{key: data[key] for key in RuntimeApproval.__dataclass_fields__ if key in data})


__all__ = [
    "AgentRuntime", "EventLoop", "FailureClass", "HeartbeatManager", "LifecycleManager", "RecoveryManager", "ResourceManager", "RuntimeApproval", "RuntimeCycleResult", "RuntimeHealth", "RuntimeHealthStatus", "RuntimeRecord", "RuntimeResourceLimits", "RuntimeSchedule", "RuntimeState", "RuntimeTask", "RuntimeTaskStatus", "ScheduleKind", "Scheduler", "ShutdownManager", "TaskPriority", "TaskQueue", "TaskSource", "TaskStatus",
]


if __name__ == "__main__":
    raise SystemExit("Use the evo CLI or import AgentRuntime; no implicit daemon is started.")


# Keep the forward references above explicit for static type checkers.
AgentRuntime.__annotations__["_runtime_type"] = "AgentRuntime"
_RUNTIME_UNUSED = FailureClass.UNKNOWN
ResourceManager = RuntimeResourceManager
