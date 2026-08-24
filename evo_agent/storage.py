from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from .models import Event, Goal, Plan, TaskStatus


class SQLiteStore:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        return connection

    def _init_schema(self) -> None:
        with self._connect() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS tasks (
                    task_id TEXT PRIMARY KEY,
                    goal TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    summary TEXT DEFAULT ''
                );
                CREATE TABLE IF NOT EXISTS events (
                    event_id TEXT PRIMARY KEY,
                    task_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS checkpoints (
                    checkpoint_id TEXT PRIMARY KEY,
                    task_id TEXT NOT NULL,
                    label TEXT NOT NULL,
                    path TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS memories (
                    memory_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    kind TEXT NOT NULL,
                    content TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                """
            )

    def create_task(self, goal: Goal) -> None:
        with self._connect() as db:
            db.execute(
                "INSERT INTO tasks(task_id, goal, status, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
                (goal.task_id, goal.text, TaskStatus.CREATED.value, goal.created_at, goal.created_at),
            )

    def update_task(self, task_id: str, status: TaskStatus, summary: str = "") -> None:
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as db:
            db.execute("UPDATE tasks SET status = ?, summary = ?, updated_at = ? WHERE task_id = ?", (status.value, summary, now, task_id))

    def append_event(self, event: Event) -> None:
        with self._connect() as db:
            db.execute(
                "INSERT INTO events(event_id, task_id, event_type, payload, created_at) VALUES (?, ?, ?, ?, ?)",
                (event.event_id, event.task_id, event.event_type.value, json.dumps(event.payload), event.created_at),
            )

    def events_for_task(self, task_id: str) -> list[dict[str, Any]]:
        with self._connect() as db:
            rows = db.execute("SELECT * FROM events WHERE task_id = ? ORDER BY created_at", (task_id,)).fetchall()
        return [{**dict(row), "payload": json.loads(row["payload"])} for row in rows]

    def add_memory(self, kind: str, content: str, created_at: str) -> None:
        with self._connect() as db:
            db.execute("INSERT INTO memories(kind, content, created_at) VALUES (?, ?, ?)", (kind, content, created_at))

    def recent_memories(self, limit: int = 20) -> list[dict[str, Any]]:
        with self._connect() as db:
            rows = db.execute("SELECT kind, content, created_at FROM memories ORDER BY memory_id DESC LIMIT ?", (limit,)).fetchall()
        return [dict(row) for row in rows]

    def add_checkpoint(self, checkpoint_id: str, task_id: str, label: str, path: str, created_at: str) -> None:
        with self._connect() as db:
            db.execute("INSERT INTO checkpoints(checkpoint_id, task_id, label, path, created_at) VALUES (?, ?, ?, ?, ?)", (checkpoint_id, task_id, label, path, created_at))
