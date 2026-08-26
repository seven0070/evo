from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import tempfile
import threading
import time
from typing import Any, Iterable

from .runtime import AgentRuntime, RuntimeCycleResult
from .storage import SQLiteStore
from .version import __version__


PRODUCTION_SCHEMA_VERSION = 1
_PRODUCTION_TABLES = {"production_schema", "production_runs", "production_metrics"}
_SECRET_TERMS = {"api_key", "apikey", "password", "secret", "token", "credential", "private_key", "authorization"}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def _sha256(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _safe_json(value: Any, depth: int = 0) -> Any:
    if depth > 4:
        return "<bounded>"
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for key, item in list(value.items())[:128]:
            name = str(key)[:120]
            lowered = name.lower()
            result[name] = "[REDACTED]" if any(term in lowered for term in _SECRET_TERMS) else _safe_json(item, depth + 1)
        return result
    if isinstance(value, (list, tuple)):
        return [_safe_json(item, depth + 1) for item in list(value)[:128]]
    if isinstance(value, str):
        return value[:2048]
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return str(value)[:2048]


def _safe_text(value: Any) -> str:
    text = str(value)[:2048]
    patterns = (
        (r"(?i)(api[_-]?key|token|password|secret|authorization|credential)\s*[:=]\s*[^\s,;]+", r"\1=[REDACTED]"),
        (r"(?i)bearer\s+[A-Za-z0-9._~+/=-]+", "Bearer [REDACTED]"),
    )
    for pattern, replacement in patterns:
        text = re.sub(pattern, replacement, text)
    return text


@dataclass(frozen=True)
class ProductionConfig:
    """Strict operational settings that may only bound, never widen, Runtime behavior."""

    max_cycles_per_run: int = 1
    max_total_runtime_seconds: int = 3600
    cycle_sleep_seconds: float = 0.25
    backup_retention: int = 3
    max_journal_rows: int = 5000
    health_stale_seconds: int = 120
    lock_timeout_seconds: float = 0.0
    backup_directory: str = ".evo/backups"
    log_level: str = "INFO"
    schema_version: int = PRODUCTION_SCHEMA_VERSION

    def __post_init__(self) -> None:
        integer_fields = ("max_cycles_per_run", "max_total_runtime_seconds", "backup_retention", "max_journal_rows", "health_stale_seconds", "schema_version")
        for name in integer_fields:
            value = int(getattr(self, name))
            if value < 1:
                raise ValueError(f"production setting {name} must be positive")
        if float(self.cycle_sleep_seconds) < 0 or float(self.cycle_sleep_seconds) > 5:
            raise ValueError("cycle_sleep_seconds must be between 0 and 5")
        if float(self.lock_timeout_seconds) < 0 or float(self.lock_timeout_seconds) > 30:
            raise ValueError("lock_timeout_seconds must be between 0 and 30")
        if self.schema_version > PRODUCTION_SCHEMA_VERSION:
            raise ValueError("production configuration requires a newer schema version")
        if self.log_level.upper() not in {"DEBUG", "INFO", "WARNING", "ERROR"}:
            raise ValueError("log_level is invalid")
        path = Path(self.backup_directory)
        if path.is_absolute() or ".." in path.parts:
            raise ValueError("backup_directory must remain inside the workspace")

    @classmethod
    def load(cls, workspace: Path, path: Path | None = None) -> "ProductionConfig":
        config_path = Path(path) if path else Path(workspace) / ".evo" / "production.json"
        if not config_path.exists():
            return cls()
        payload = json.loads(config_path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("production configuration must be a JSON object")
        if any(any(term in str(key).lower() for term in _SECRET_TERMS) for key in payload):
            raise ValueError("production configuration cannot contain credential settings")
        unknown = set(payload) - set(cls.__dataclass_fields__)
        if unknown:
            raise ValueError("unknown production configuration keys: " + ", ".join(sorted(unknown)))
        return cls(**payload)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def bounded_for(self, runtime: AgentRuntime) -> dict[str, Any]:
        """Return stricter limits for a supervisor invocation, never wider limits."""
        return {
            "max_cycles": min(self.max_cycles_per_run, runtime.limits.max_tasks_per_cycle * self.max_cycles_per_run),
            "max_total_runtime_seconds": min(self.max_total_runtime_seconds, runtime.limits.max_total_runtime),
        }


class ProductionSchemaManager:
    """Forward-only operational metadata schema sharing the Runtime SQLite file."""

    def __init__(self, store: SQLiteStore):
        self.store = store
        self.ensure_current()

    def ensure_current(self) -> int:
        with self.store._connect() as db:
            db.execute("CREATE TABLE IF NOT EXISTS production_schema (schema_name TEXT PRIMARY KEY, schema_version INTEGER NOT NULL, updated_at TEXT NOT NULL)")
            row = db.execute("SELECT schema_version FROM production_schema WHERE schema_name = 'production'").fetchone()
            current = int(row[0]) if row else 0
            if current > PRODUCTION_SCHEMA_VERSION:
                raise RuntimeError("production database schema is newer than this application")
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS production_runs (
                    run_id TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    ended_at TEXT,
                    cycles_requested INTEGER NOT NULL,
                    cycles_completed INTEGER NOT NULL DEFAULT 0,
                    tasks_completed INTEGER NOT NULL DEFAULT 0,
                    tasks_failed INTEGER NOT NULL DEFAULT 0,
                    interrupted INTEGER NOT NULL DEFAULT 0,
                    error TEXT NOT NULL DEFAULT '',
                    payload TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_production_runs_started ON production_runs(started_at);
                CREATE INDEX IF NOT EXISTS idx_production_runs_status ON production_runs(status);
                CREATE TABLE IF NOT EXISTS production_metrics (
                    metric_name TEXT PRIMARY KEY,
                    metric_value REAL NOT NULL,
                    updated_at TEXT NOT NULL,
                    metadata TEXT NOT NULL
                );
                """
            )
            if current == 0:
                db.execute("INSERT OR REPLACE INTO production_schema(schema_name, schema_version, updated_at) VALUES ('production', ?, ?)", (PRODUCTION_SCHEMA_VERSION, _utc_now()))
            elif current < PRODUCTION_SCHEMA_VERSION:
                db.execute("UPDATE production_schema SET schema_version = ?, updated_at = ? WHERE schema_name = 'production'", (PRODUCTION_SCHEMA_VERSION, _utc_now()))
        return PRODUCTION_SCHEMA_VERSION


class OperationalJournal:
    """Compact operational records linked to authoritative Runtime state."""

    def __init__(self, store: SQLiteStore, max_rows: int = 5000):
        self.store = store
        self.max_rows = max(1, int(max_rows))
        ProductionSchemaManager(store)
        self._lock = threading.RLock()

    def recover_interrupted(self) -> int:
        now = _utc_now()
        with self.store._connect() as db:
            cursor = db.execute("UPDATE production_runs SET status = 'interrupted', ended_at = ?, interrupted = 1, error = CASE WHEN error = '' THEN 'process ended before terminal record' ELSE error END WHERE status = 'running'", (now,))
            return cursor.rowcount

    def start_run(self, run_id: str, cycles_requested: int, payload: dict[str, Any] | None = None) -> None:
        with self._lock, self.store._connect() as db:
            db.execute("INSERT INTO production_runs(run_id, status, started_at, cycles_requested, payload) VALUES (?, 'running', ?, ?, ?)", (run_id, _utc_now(), int(cycles_requested), json.dumps(_safe_json(payload or {}), sort_keys=True)))
            self._trim(db)

    def finish_run(self, run_id: str, status: str, cycles_completed: int = 0, tasks_completed: int = 0, tasks_failed: int = 0, error: str = "", interrupted: bool = False, payload: dict[str, Any] | None = None) -> None:
        allowed = {"completed", "failed", "interrupted", "stopped"}
        if status not in allowed:
            raise ValueError("invalid production run status")
        with self._lock, self.store._connect() as db:
            db.execute("UPDATE production_runs SET status = ?, ended_at = ?, cycles_completed = ?, tasks_completed = ?, tasks_failed = ?, interrupted = ?, error = ?, payload = ? WHERE run_id = ?", (status, _utc_now(), int(cycles_completed), int(tasks_completed), int(tasks_failed), int(interrupted), str(error)[:2048], json.dumps(_safe_json(payload or {}), sort_keys=True), run_id))
            self._trim(db)

    def increment(self, name: str, value: float = 1.0, metadata: dict[str, Any] | None = None) -> None:
        if not name or len(name) > 120:
            raise ValueError("metric name is invalid")
        with self._lock, self.store._connect() as db:
            row = db.execute("SELECT metric_value FROM production_metrics WHERE metric_name = ?", (name,)).fetchone()
            total = float(row[0]) + float(value) if row else float(value)
            db.execute("INSERT OR REPLACE INTO production_metrics(metric_name, metric_value, updated_at, metadata) VALUES (?, ?, ?, ?)", (name, total, _utc_now(), json.dumps(_safe_json(metadata or {}), sort_keys=True)))

    def runs(self, limit: int = 50) -> list[dict[str, Any]]:
        with self.store._connect() as db:
            rows = db.execute("SELECT * FROM production_runs ORDER BY started_at DESC LIMIT ?", (max(1, min(int(limit), self.max_rows)),)).fetchall()
        return [dict(row) for row in rows]

    def metrics(self) -> dict[str, float]:
        with self.store._connect() as db:
            rows = db.execute("SELECT metric_name, metric_value FROM production_metrics ORDER BY metric_name").fetchall()
        return {str(row[0]): float(row[1]) for row in rows}

    def _trim(self, db: sqlite3.Connection) -> None:
        db.execute("DELETE FROM production_runs WHERE run_id NOT IN (SELECT run_id FROM production_runs ORDER BY started_at DESC LIMIT ?)", (self.max_rows,))


class CrashReporter:
    """Local, bounded, redacted incident records; never an execution authority."""

    def __init__(self, workspace: Path, max_reports: int = 100):
        self.workspace = Path(workspace).resolve()
        self.directory = self.workspace / ".evo" / "incidents"
        self.directory.mkdir(parents=True, exist_ok=True)
        self.max_reports = max(1, min(int(max_reports), 1000))
        self._lock = threading.RLock()

    def record(self, component: str, error: BaseException | str, context: dict[str, Any] | None = None) -> Path:
        name = _safe_text(component).strip()[:120] or "unknown"
        timestamp = _utc_now()
        incident_id = f"incident_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')}_{os.getpid()}"
        payload = {
            "incident_id": incident_id,
            "recorded_at": timestamp,
            "version": __version__,
            "component": name,
            "error_type": type(error).__name__ if isinstance(error, BaseException) else "Error",
            "error": _safe_text(error),
            "context": _safe_json(context or {}),
        }
        target = self.directory / f"{incident_id}.json"
        with self._lock:
            fd, temporary = tempfile.mkstemp(prefix=".incident-", suffix=".tmp", dir=self.directory)
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as handle:
                    json.dump(payload, handle, sort_keys=True, indent=2)
                    handle.write("\n")
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temporary, target)
                self._trim()
            finally:
                if os.path.exists(temporary):
                    os.unlink(temporary)
        return target

    def list(self, limit: int = 50) -> list[Path]:
        return sorted(self.directory.glob("incident_*.json"), key=lambda path: path.name, reverse=True)[:max(1, min(int(limit), self.max_reports))]

    def _trim(self) -> None:
        paths = sorted(self.directory.glob("incident_*.json"), key=lambda path: path.name, reverse=True)
        for path in paths[self.max_reports:]:
            path.unlink(missing_ok=True)


class BackupManager:
    """Atomic SQLite backup and integrity validation using the authoritative database."""

    def __init__(self, store: SQLiteStore, workspace: Path, retention: int = 3, directory: str = ".evo/backups"):
        self.store = store
        self.workspace = Path(workspace).resolve()
        self.retention = max(1, int(retention))
        backup_dir = Path(directory)
        if backup_dir.is_absolute() or ".." in backup_dir.parts:
            raise ValueError("backup directory must remain inside workspace")
        self.directory = self.workspace / backup_dir
        self.directory.mkdir(parents=True, exist_ok=True)

    def create(self, label: str = "manual") -> Path:
        safe_label = "".join(char if char.isalnum() or char in "-_" else "_" for char in str(label))[:40] or "backup"
        target = self.directory / f"{safe_label}-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.sqlite3"
        temporary = target.with_suffix(target.suffix + ".tmp")
        source = sqlite3.connect(self.store.path)
        destination = sqlite3.connect(temporary)
        try:
            source.backup(destination)
            destination.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            integrity = destination.execute("PRAGMA integrity_check").fetchone()[0]
            if integrity != "ok":
                raise RuntimeError("backup integrity check failed")
            destination.commit()
        finally:
            destination.close()
            source.close()
        os.replace(temporary, target)
        self._trim()
        return target

    def validate(self, path: Path) -> dict[str, Any]:
        candidate = Path(path)
        if not candidate.exists():
            return {"ok": False, "reason": "backup does not exist", "path": str(candidate)}
        try:
            with sqlite3.connect(candidate) as db:
                integrity = str(db.execute("PRAGMA integrity_check").fetchone()[0])
                tables = {str(row[0]) for row in db.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        except sqlite3.DatabaseError as exc:
            return {"ok": False, "reason": f"invalid sqlite database: {type(exc).__name__}", "path": str(candidate), "sha256": _sha256(candidate.read_bytes())}
        return {"ok": integrity == "ok" and "tasks" in tables and "events" in tables, "integrity": integrity, "tables": sorted(tables), "path": str(candidate), "sha256": _sha256(candidate.read_bytes())}

    def list(self) -> list[Path]:
        return sorted(self.directory.glob("*.sqlite3"), key=lambda path: path.stat().st_mtime, reverse=True)

    def _trim(self) -> None:
        for path in self.list()[self.retention :]:
            path.unlink(missing_ok=True)


class ProductionHealth:
    def __init__(self, runtime: AgentRuntime, journal: OperationalJournal, config: ProductionConfig):
        self.runtime = runtime
        self.journal = journal
        self.config = config

    def check(self) -> dict[str, Any]:
        try:
            database = self.runtime.store.database_integrity_report()
        except Exception as exc:
            database = {"ok": False, "reason": f"{type(exc).__name__}: {exc}"}
        runtime_health = self.runtime.health().to_dict()
        status = "healthy"
        if not database.get("ok") or runtime_health.get("status") in {"failed", "degraded"}:
            status = "failed" if not database.get("ok") else "degraded"
        if self.runtime.kill_switch_active or self.runtime.state.value == "stopped":
            status = "stopped"
        return {
            "status": status,
            "checked_at": _utc_now(),
            "database": {"ok": bool(database.get("ok")), "sqlite_integrity": database.get("sqlite_integrity"), "malformed_payloads": len(database.get("malformed_payloads", []))},
            "runtime": {"state": self.runtime.state.value, "health": runtime_health.get("status"), "queue_depth": self.runtime.queue.depth(), "safe_mode": self.runtime.safe_mode, "kill_switch": self.runtime.kill_switch_active},
            "resources": {"pressure": runtime_health.get("resource_pressure", 0.0), "limits": self.runtime.limits.to_dict()},
            "metrics": self.journal.metrics(),
            "version": __version__,
        }


class _ProcessLock:
    def __init__(self, path: Path, timeout: float):
        self.path = path
        self.timeout = max(0.0, float(timeout))
        self.handle: Any = None

    def __enter__(self) -> "_ProcessLock":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.handle = self.path.open("a+", encoding="utf-8")
        deadline = time.monotonic() + self.timeout
        while True:
            try:
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(self.handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(self.handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                return self
            except (BlockingIOError, OSError):
                if time.monotonic() >= deadline:
                    self.handle.close()
                    self.handle = None
                    raise RuntimeError("another Evo production supervisor is already running")
                time.sleep(0.05)

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        if self.handle is None:
            return
        try:
            if os.name == "nt":
                import msvcrt
                self.handle.seek(0)
                msvcrt.locking(self.handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.handle.fileno(), fcntl.LOCK_UN)
        finally:
            self.handle.close()
            self.handle = None


@dataclass
class SupervisorReport:
    run_id: str
    status: str
    cycles_requested: int
    cycles_completed: int = 0
    tasks_completed: int = 0
    tasks_failed: int = 0
    cycle_results: list[dict[str, Any]] = field(default_factory=list)
    health: dict[str, Any] = field(default_factory=dict)
    backup: str | None = None
    error: str = ""
    incident: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class ProductionSupervisor:
    """Explicitly invoked, bounded supervisor delegating execution to AgentRuntime."""

    def __init__(self, runtime: AgentRuntime, config: ProductionConfig | None = None):
        self.runtime = runtime
        self.config = config or ProductionConfig()
        self.journal = OperationalJournal(runtime.store, self.config.max_journal_rows)
        self.health = ProductionHealth(runtime, self.journal, self.config)
        self.backups = BackupManager(runtime.store, runtime.workspace, self.config.backup_retention, self.config.backup_directory)
        self.crash_reports = CrashReporter(runtime.workspace)
        self.lock_path = runtime.workspace / ".evo" / "production-supervisor.lock"

    def run(self, cycles: int | None = None, backup: bool = False) -> SupervisorReport:
        requested = min(max(1, int(cycles if cycles is not None else self.config.max_cycles_per_run)), self.config.max_cycles_per_run)
        run_id = "prod_" + _sha256({"workspace": str(self.runtime.workspace), "started": _utc_now()})[:16]
        report = SupervisorReport(run_id, "running", requested)
        with _ProcessLock(self.lock_path, self.config.lock_timeout_seconds):
            self.journal.recover_interrupted()
            self.journal.start_run(run_id, requested, {"version": __version__})
            started = time.monotonic()
            started_runtime = False
            try:
                self.runtime.start()
                started_runtime = True
                for index in range(requested):
                    if time.monotonic() - started >= min(self.config.max_total_runtime_seconds, self.runtime.limits.max_total_runtime):
                        report.status = "stopped"
                        report.error = "production runtime ceiling reached"
                        break
                    result: RuntimeCycleResult = self.runtime.run_cycle()
                    data = result.to_dict()
                    report.cycle_results.append(data)
                    report.cycles_completed += 1
                    report.tasks_completed += int(result.tasks_completed)
                    report.tasks_failed += int(result.tasks_failed)
                    self.journal.increment("cycles_completed")
                    self.journal.increment("tasks_completed", result.tasks_completed)
                    self.journal.increment("tasks_failed", result.tasks_failed)
                    if result.state in {"failed", "degraded"} or result.stopped_reason in {"health_degraded", "cycle_failure"}:
                        report.status = "failed"
                        report.error = "; ".join(result.failures)[:2048] or result.stopped_reason
                        break
                    if index + 1 < requested and self.config.cycle_sleep_seconds:
                        time.sleep(self.config.cycle_sleep_seconds)
                else:
                    report.status = "completed"
                if started_runtime and self.runtime.state.value not in {"stopped", "failed"}:
                    self.runtime.stop("bounded production supervisor run complete")
                    started_runtime = False
                if backup:
                    report.backup = str(self.backups.create("supervisor"))
                report.health = self.health.check()
                self.journal.finish_run(run_id, report.status, report.cycles_completed, report.tasks_completed, report.tasks_failed, report.error, report.status == "interrupted", {"health": report.health, "backup": report.backup})
                return report
            except Exception as exc:
                report.status = "failed"
                report.error = f"{type(exc).__name__}: {exc}"[:2048]
                try:
                    report.incident = str(self.crash_reports.record("production-supervisor", exc, {"run_id": run_id, "cycles_completed": report.cycles_completed}))
                except Exception:
                    report.incident = None
                report.health = self.health.check()
                self.journal.finish_run(run_id, report.status, report.cycles_completed, report.tasks_completed, report.tasks_failed, report.error, False, {"health": report.health})
                raise
            finally:
                if started_runtime and self.runtime.state.value not in {"stopped", "failed"}:
                    self.runtime.stop("bounded production supervisor run complete")

    def status(self) -> dict[str, Any]:
        return {"config": self.config.to_dict(), "health": self.health.check(), "runs": self.journal.runs(limit=20), "backups": [str(path) for path in self.backups.list()]}
