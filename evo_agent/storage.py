from __future__ import annotations

from datetime import datetime, timezone
import json
import sqlite3
from pathlib import Path
from typing import Any

from .models import Event, Goal, TaskStatus


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
                CREATE TABLE IF NOT EXISTS experiences (
                    experience_id TEXT PRIMARY KEY,
                    task_id TEXT NOT NULL UNIQUE,
                    original_goal TEXT NOT NULL,
                    task_type TEXT NOT NULL,
                    outcome TEXT NOT NULL,
                    strategy TEXT,
                    tools TEXT NOT NULL,
                    failure_text TEXT NOT NULL,
                    agent_version TEXT NOT NULL,
                    timestamp TEXT NOT NULL,
                    evaluation_id TEXT,
                    payload TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS evaluations (
                    evaluation_id TEXT PRIMARY KEY,
                    experience_id TEXT NOT NULL UNIQUE,
                    success_score INTEGER NOT NULL,
                    outcome TEXT NOT NULL,
                    evaluator_version TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS evolution_proposals (
                    proposal_id TEXT PRIMARY KEY,
                    target_component TEXT NOT NULL,
                    risk TEXT NOT NULL,
                    status TEXT NOT NULL,
                    agent_version TEXT NOT NULL,
                    evolver_version TEXT NOT NULL,
                    confidence REAL NOT NULL,
                    created_at TEXT NOT NULL,
                    reviewed_at TEXT,
                    approval_decision TEXT,
                    payload TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_proposals_status ON evolution_proposals(status);
                CREATE INDEX IF NOT EXISTS idx_proposals_target ON evolution_proposals(target_component);
                CREATE TABLE IF NOT EXISTS evolution_experiments (
                    experiment_id TEXT PRIMARY KEY,
                    proposal_id TEXT NOT NULL,
                    candidate_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    baseline_version TEXT NOT NULL,
                    candidate_version TEXT NOT NULL,
                    sandbox_location TEXT NOT NULL,
                    start_time TEXT NOT NULL,
                    end_time TEXT,
                    cleanup_status TEXT NOT NULL,
                    payload TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_experiments_proposal ON evolution_experiments(proposal_id);
                CREATE INDEX IF NOT EXISTS idx_experiments_status ON evolution_experiments(status);
                CREATE INDEX IF NOT EXISTS idx_experiences_task_type ON experiences(task_type);
                CREATE INDEX IF NOT EXISTS idx_experiences_outcome ON experiences(outcome);
                CREATE INDEX IF NOT EXISTS idx_experiences_strategy ON experiences(strategy);
                CREATE INDEX IF NOT EXISTS idx_experiences_version ON experiences(agent_version);
                CREATE TABLE IF NOT EXISTS benchmarks (
                    benchmark_id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    version TEXT NOT NULL,
                    benchmark_version TEXT NOT NULL,
                    trial_count INTEGER NOT NULL,
                    payload TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS benchmark_trials (
                    trial_id TEXT PRIMARY KEY,
                    benchmark_id TEXT NOT NULL,
                    experiment_id TEXT NOT NULL,
                    side TEXT NOT NULL,
                    task_case_id TEXT NOT NULL,
                    trial_number INTEGER NOT NULL,
                    success INTEGER NOT NULL,
                    verified INTEGER NOT NULL,
                    score REAL NOT NULL,
                    timeout INTEGER NOT NULL,
                    payload TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_trials_benchmark ON benchmark_trials(benchmark_id);
                CREATE INDEX IF NOT EXISTS idx_trials_experiment ON benchmark_trials(experiment_id);
                CREATE TABLE IF NOT EXISTS evolution_evidence (
                    evidence_id TEXT PRIMARY KEY,
                    experiment_id TEXT NOT NULL,
                    proposal_id TEXT NOT NULL,
                    benchmark_id TEXT NOT NULL,
                    decision TEXT NOT NULL,
                    baseline_version TEXT NOT NULL,
                    candidate_version TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_evidence_experiment ON evolution_evidence(experiment_id);
                CREATE INDEX IF NOT EXISTS idx_evidence_decision ON evolution_evidence(decision);
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

    def save_experience(self, experience: Any) -> None:
        payload = experience.to_dict()
        with self._connect() as db:
            db.execute(
                """INSERT OR REPLACE INTO experiences(
                    experience_id, task_id, original_goal, task_type, outcome, strategy,
                    tools, failure_text, agent_version, timestamp, evaluation_id, payload
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    experience.experience_id,
                    experience.task_id,
                    experience.original_goal,
                    experience.task_type,
                    experience.final_outcome.value,
                    experience.selected_strategy,
                    json.dumps(experience.selected_tools),
                    json.dumps(experience.failures),
                    experience.agent_version,
                    experience.timestamp,
                    experience.evaluation_id,
                    json.dumps(payload),
                ),
            )

    def update_experience_evaluation(self, experience_id: str, evaluation_id: str, evaluation_result: dict[str, Any]) -> None:
        with self._connect() as db:
            row = db.execute("SELECT payload FROM experiences WHERE experience_id = ?", (experience_id,)).fetchone()
            if not row:
                return
            payload = json.loads(row["payload"])
            payload["evaluation_id"] = evaluation_id
            payload["evaluation_result"] = evaluation_result
            db.execute("UPDATE experiences SET evaluation_id = ?, payload = ? WHERE experience_id = ?", (evaluation_id, json.dumps(payload), experience_id))

    def save_evaluation(self, evaluation: Any) -> None:
        payload = evaluation.to_dict()
        with self._connect() as db:
            db.execute(
                """INSERT OR REPLACE INTO evaluations(
                    evaluation_id, experience_id, success_score, outcome, evaluator_version, payload, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    evaluation.evaluation_id,
                    evaluation.experience_id,
                    evaluation.success_score,
                    evaluation.outcome.value,
                    evaluation.evaluator_version,
                    json.dumps(payload),
                    datetime.now(timezone.utc).isoformat(),
                ),
            )

    def save_proposal(self, proposal: Any) -> None:
        payload = proposal.to_dict()
        with self._connect() as db:
            db.execute(
                """INSERT OR REPLACE INTO evolution_proposals(
                    proposal_id, target_component, risk, status, agent_version,
                    evolver_version, confidence, created_at, reviewed_at,
                    approval_decision, payload
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    proposal.proposal_id,
                    proposal.target_component,
                    proposal.risk.value,
                    proposal.status.value,
                    proposal.agent_version,
                    proposal.evolver_version,
                    proposal.confidence,
                    proposal.created_at,
                    proposal.reviewed_at,
                    proposal.approval_decision,
                    json.dumps(payload),
                ),
            )

    def save_experiment(self, experiment: Any) -> None:
        payload = experiment.to_dict()
        with self._connect() as db:
            db.execute(
                """INSERT OR REPLACE INTO evolution_experiments(
                    experiment_id, proposal_id, candidate_id, status, baseline_version,
                    candidate_version, sandbox_location, start_time, end_time,
                    cleanup_status, payload
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    experiment.experiment_id,
                    experiment.proposal_id,
                    experiment.candidate_id,
                    experiment.status.value,
                    experiment.baseline_version,
                    experiment.candidate_version,
                    experiment.sandbox_location,
                    experiment.start_time,
                    experiment.end_time,
                    experiment.cleanup_status,
                    json.dumps(payload),
                ),
            )

    def experiment_by_id(self, experiment_id: str) -> dict[str, Any] | None:
        with self._connect() as db:
            row = db.execute("SELECT * FROM evolution_experiments WHERE experiment_id = ?", (experiment_id,)).fetchone()
        return dict(row) if row else None

    def find_experiments(self, limit: int = 50) -> list[dict[str, Any]]:
        with self._connect() as db:
            rows = db.execute("SELECT * FROM evolution_experiments ORDER BY start_time DESC LIMIT ?", (limit,)).fetchall()
        return [dict(row) for row in rows]

    def save_benchmark(self, benchmark: Any) -> None:
        payload = benchmark.to_dict()
        with self._connect() as db:
            db.execute(
                """INSERT OR REPLACE INTO benchmarks(
                    benchmark_id, name, version, benchmark_version, trial_count, payload, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (benchmark.benchmark_id, benchmark.name, benchmark.version, benchmark.benchmark_version, benchmark.trial_count, json.dumps(payload), datetime.now(timezone.utc).isoformat()),
            )

    def benchmark_by_id(self, benchmark_id: str) -> dict[str, Any] | None:
        with self._connect() as db:
            row = db.execute("SELECT * FROM benchmarks WHERE benchmark_id = ?", (benchmark_id,)).fetchone()
        return dict(row) if row else None

    def find_benchmarks(self, limit: int = 50) -> list[dict[str, Any]]:
        with self._connect() as db:
            rows = db.execute("SELECT * FROM benchmarks ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
        return [dict(row) for row in rows]

    def save_benchmark_trial(self, trial: Any) -> None:
        with self._connect() as db:
            db.execute(
                """INSERT OR REPLACE INTO benchmark_trials(
                    trial_id, benchmark_id, experiment_id, side, task_case_id, trial_number,
                    success, verified, score, timeout, payload, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (trial.trial_id, trial.benchmark_id, trial.experiment_id, trial.side, trial.task_case_id, trial.trial_number, int(trial.success), int(trial.verified), trial.score, int(trial.timeout), json.dumps(trial.to_dict()), trial.start_time),
            )

    def find_benchmark_trials(self, benchmark_id: str | None = None, experiment_id: str | None = None) -> list[dict[str, Any]]:
        clauses: list[str] = []
        values: list[Any] = []
        if benchmark_id:
            clauses.append("benchmark_id = ?")
            values.append(benchmark_id)
        if experiment_id:
            clauses.append("experiment_id = ?")
            values.append(experiment_id)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with self._connect() as db:
            rows = db.execute(f"SELECT * FROM benchmark_trials {where} ORDER BY created_at", values).fetchall()
        return [dict(row) for row in rows]

    def save_evolution_evidence(self, evidence: Any) -> None:
        payload = evidence.to_dict()
        with self._connect() as db:
            db.execute(
                """INSERT OR REPLACE INTO evolution_evidence(
                    evidence_id, experiment_id, proposal_id, benchmark_id, decision,
                    baseline_version, candidate_version, payload, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (evidence.evidence_id, evidence.experiment_id, evidence.proposal_id, evidence.benchmark_id, evidence.decision.value, evidence.baseline_version, evidence.candidate_version, json.dumps(payload), evidence.created_at),
            )

    def evidence_by_id(self, evidence_id: str) -> dict[str, Any] | None:
        with self._connect() as db:
            row = db.execute("SELECT * FROM evolution_evidence WHERE evidence_id = ?", (evidence_id,)).fetchone()
        return dict(row) if row else None

    def find_evidence(self, experiment_id: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        with self._connect() as db:
            if experiment_id:
                rows = db.execute("SELECT * FROM evolution_evidence WHERE experiment_id = ? ORDER BY created_at DESC LIMIT ?", (experiment_id, limit)).fetchall()
            else:
                rows = db.execute("SELECT * FROM evolution_evidence ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
        return [dict(row) for row in rows]

    def proposal_by_id(self, proposal_id: str) -> dict[str, Any] | None:
        with self._connect() as db:
            row = db.execute("SELECT * FROM evolution_proposals WHERE proposal_id = ?", (proposal_id,)).fetchone()
        return dict(row) if row else None

    def find_proposals(self, status: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        if status:
            query = "SELECT * FROM evolution_proposals WHERE status = ? ORDER BY created_at DESC LIMIT ?"
            values = (status, limit)
        else:
            query = "SELECT * FROM evolution_proposals ORDER BY created_at DESC LIMIT ?"
            values = (limit,)
        with self._connect() as db:
            rows = db.execute(query, values).fetchall()
        return [dict(row) for row in rows]

    def experience_by_id(self, experience_id: str) -> dict[str, Any] | None:
        with self._connect() as db:
            row = db.execute("SELECT * FROM experiences WHERE experience_id = ?", (experience_id,)).fetchone()
        return dict(row) if row else None

    def evaluation_by_id(self, evaluation_id: str) -> dict[str, Any] | None:
        with self._connect() as db:
            row = db.execute("SELECT * FROM evaluations WHERE evaluation_id = ?", (evaluation_id,)).fetchone()
        return dict(row) if row else None

    def find_experiences(
        self,
        goal: str | None = None,
        task_type: str | None = None,
        outcome: str | None = None,
        strategy: str | None = None,
        tool: str | None = None,
        failure: str | None = None,
        agent_version: str | None = None,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        values: list[Any] = []
        if goal:
            clauses.append("original_goal LIKE ?")
            values.append(f"%{goal}%")
        if task_type:
            clauses.append("task_type = ?")
            values.append(task_type)
        if outcome:
            clauses.append("outcome = ?")
            values.append(outcome)
        if strategy:
            clauses.append("strategy = ?")
            values.append(strategy)
        if tool:
            clauses.append("tools LIKE ?")
            values.append(f"%{tool}%")
        if failure:
            clauses.append("failure_text LIKE ?")
            values.append(f"%{failure}%")
        if agent_version:
            clauses.append("agent_version = ?")
            values.append(agent_version)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with self._connect() as db:
            rows = db.execute(f"SELECT * FROM experiences {where} ORDER BY timestamp DESC LIMIT ?", (*values, limit)).fetchall()
        return [dict(row) for row in rows]
