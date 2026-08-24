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
                CREATE TABLE IF NOT EXISTS versions (
                    version_id TEXT PRIMARY KEY,
                    source_commit TEXT NOT NULL,
                    parent_version TEXT,
                    proposal_id TEXT NOT NULL,
                    experiment_id TEXT NOT NULL,
                    evidence_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    version_path TEXT NOT NULL,
                    manifest_hash TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    metadata TEXT NOT NULL
                );
                CREATE UNIQUE INDEX IF NOT EXISTS idx_one_active_version ON versions(status) WHERE status = 'active';
                CREATE INDEX IF NOT EXISTS idx_versions_status ON versions(status);
                CREATE TABLE IF NOT EXISTS promotion_requests (
                    promotion_id TEXT PRIMARY KEY,
                    proposal_id TEXT NOT NULL,
                    experiment_id TEXT NOT NULL,
                    evidence_id TEXT NOT NULL,
                    candidate_version TEXT NOT NULL,
                    current_production_version TEXT,
                    requested_at TEXT NOT NULL,
                    requested_by TEXT NOT NULL,
                    approval_status TEXT NOT NULL,
                    approval_reason TEXT,
                    eligibility_status TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    policy_version TEXT NOT NULL,
                    payload TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_promotions_status ON promotion_requests(status);
                CREATE TABLE IF NOT EXISTS promotion_checkpoints (
                    checkpoint_id TEXT PRIMARY KEY,
                    production_version TEXT NOT NULL,
                    source_commit TEXT NOT NULL,
                    configuration TEXT NOT NULL,
                    runtime_state TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    integrity_hash TEXT NOT NULL,
                    active_target TEXT NOT NULL,
                    payload TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS promotion_records (
                    promotion_id TEXT PRIMARY KEY,
                    candidate_version TEXT NOT NULL,
                    previous_version TEXT,
                    proposal_id TEXT NOT NULL,
                    experiment_id TEXT NOT NULL,
                    evidence_id TEXT NOT NULL,
                    checkpoint_id TEXT NOT NULL,
                    final_status TEXT NOT NULL,
                    promoted_at TEXT,
                    rolled_back_at TEXT,
                    rollback_reason TEXT,
                    payload TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_promotion_records_status ON promotion_records(final_status);
                CREATE TABLE IF NOT EXISTS rollback_records (
                    rollback_id TEXT PRIMARY KEY,
                    promotion_id TEXT NOT NULL,
                    from_version TEXT NOT NULL,
                    to_version TEXT NOT NULL,
                    checkpoint_id TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    completed_at TEXT,
                    status TEXT NOT NULL,
                    payload TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_rollbacks_promotion ON rollback_records(promotion_id);
                CREATE TABLE IF NOT EXISTS components (
                    component_id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    version TEXT NOT NULL,
                    component_type TEXT NOT NULL,
                    status TEXT NOT NULL,
                    dependencies TEXT NOT NULL,
                    interfaces TEXT NOT NULL,
                    capabilities TEXT NOT NULL,
                    protected INTEGER NOT NULL,
                    source_reference TEXT NOT NULL,
                    integrity_hash TEXT NOT NULL,
                    metadata TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS capabilities (
                    capability_id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    provider_component TEXT NOT NULL,
                    version TEXT NOT NULL,
                    dependencies TEXT NOT NULL,
                    permissions_required TEXT NOT NULL,
                    risk_class TEXT NOT NULL,
                    status TEXT NOT NULL,
                    metadata TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS architecture_versions (
                    architecture_version TEXT PRIMARY KEY,
                    agent_version TEXT NOT NULL,
                    components TEXT NOT NULL,
                    capabilities TEXT NOT NULL,
                    dependencies TEXT NOT NULL,
                    interfaces TEXT NOT NULL,
                    protected_components TEXT NOT NULL,
                    configuration TEXT NOT NULL,
                    integrity_hash TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    payload TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS metamorphosis_proposals (
                    proposal_id TEXT PRIMARY KEY,
                    change_type TEXT NOT NULL,
                    target_component TEXT NOT NULL,
                    risk_class TEXT NOT NULL,
                    source_version TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    payload TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_metamorphosis_status ON metamorphosis_proposals(status);
                CREATE TABLE IF NOT EXISTS metamorphosis_experiments (
                    experiment_id TEXT PRIMARY KEY,
                    proposal_id TEXT NOT NULL,
                    baseline_architecture TEXT NOT NULL,
                    candidate_architecture TEXT NOT NULL,
                    compatibility_status TEXT NOT NULL,
                    benchmark_evidence_id TEXT,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    payload TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_metamorphosis_experiment_proposal ON metamorphosis_experiments(proposal_id);
                CREATE TABLE IF NOT EXISTS evolution_opportunities (
                    opportunity_id TEXT PRIMARY KEY,
                    fingerprint TEXT NOT NULL UNIQUE,
                    status TEXT NOT NULL,
                    recommended_change_type TEXT NOT NULL,
                    confidence REAL NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    payload TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_opportunities_status ON evolution_opportunities(status);
                CREATE TABLE IF NOT EXISTS evolution_work_items (
                    work_item_id TEXT PRIMARY KEY,
                    opportunity_id TEXT NOT NULL,
                    change_type TEXT NOT NULL,
                    current_state TEXT NOT NULL,
                    target_component TEXT NOT NULL,
                    target_capability TEXT,
                    proposal_id TEXT,
                    experiment_id TEXT,
                    benchmark_id TEXT,
                    evidence_id TEXT,
                    promotion_id TEXT,
                    current_version TEXT NOT NULL,
                    architecture_version TEXT NOT NULL,
                    candidate_version TEXT,
                    attempt_count INTEGER NOT NULL,
                    cooldown_until TEXT,
                    last_error TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    payload TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_work_items_state ON evolution_work_items(current_state);
                CREATE INDEX IF NOT EXISTS idx_work_items_opportunity ON evolution_work_items(opportunity_id);
                CREATE TABLE IF NOT EXISTS orchestration_events (
                    orchestration_event_id TEXT PRIMARY KEY,
                    work_item_id TEXT NOT NULL,
                    opportunity_id TEXT NOT NULL,
                    event_name TEXT NOT NULL,
                    previous_state TEXT,
                    current_state TEXT NOT NULL,
                    change_type TEXT NOT NULL,
                    component TEXT NOT NULL,
                    version TEXT NOT NULL,
                    actor TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    result TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    payload TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_orchestration_events_work_item ON orchestration_events(work_item_id);
                CREATE TABLE IF NOT EXISTS approval_requests (
                    approval_request_id TEXT PRIMARY KEY,
                    work_item_id TEXT NOT NULL,
                    approval_type TEXT NOT NULL,
                    status TEXT NOT NULL,
                    actor TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    payload TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_approval_requests_status ON approval_requests(status);
                CREATE TABLE IF NOT EXISTS experiment_queue (
                    queue_id TEXT PRIMARY KEY,
                    work_item_id TEXT NOT NULL,
                    engine TEXT NOT NULL,
                    experiment_id TEXT,
                    status TEXT NOT NULL,
                    attempt_count INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    payload TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_experiment_queue_status ON experiment_queue(status);
                CREATE TABLE IF NOT EXISTS promotion_queue (
                    queue_id TEXT PRIMARY KEY,
                    work_item_id TEXT NOT NULL,
                    promotion_id TEXT,
                    candidate_version TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    payload TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_promotion_queue_status ON promotion_queue(status);
                CREATE TABLE IF NOT EXISTS evolution_cooldowns (
                    opportunity_key TEXT PRIMARY KEY,
                    opportunity_id TEXT NOT NULL,
                    attempt_count INTEGER NOT NULL,
                    last_attempt TEXT NOT NULL,
                    last_result TEXT NOT NULL,
                    cooldown_until TEXT,
                    payload TEXT NOT NULL
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

    def save_version(self, version: Any) -> None:
        payload = version.to_dict()
        with self._connect() as db:
            db.execute(
                """INSERT OR REPLACE INTO versions(
                    version_id, source_commit, parent_version, proposal_id, experiment_id,
                    evidence_id, status, version_path, manifest_hash, created_at, metadata
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (version.version_id, version.source_commit, version.parent_version, version.proposal_id, version.experiment_id, version.evidence_id, version.status.value, version.version_path, version.manifest_hash, version.created_at, json.dumps(version.metadata)),
            )

    def version_by_id(self, version_id: str) -> dict[str, Any] | None:
        with self._connect() as db:
            row = db.execute("SELECT * FROM versions WHERE version_id = ?", (version_id,)).fetchone()
        return dict(row) if row else None

    def find_versions(self, status: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        with self._connect() as db:
            if status:
                rows = db.execute("SELECT * FROM versions WHERE status = ? ORDER BY created_at DESC LIMIT ?", (status, limit)).fetchall()
            else:
                rows = db.execute("SELECT * FROM versions ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
        return [dict(row) for row in rows]

    def save_promotion_request(self, request: Any) -> None:
        with self._connect() as db:
            db.execute(
                """INSERT OR REPLACE INTO promotion_requests(
                    promotion_id, proposal_id, experiment_id, evidence_id, candidate_version,
                    current_production_version, requested_at, requested_by, approval_status,
                    approval_reason, eligibility_status, status, created_at, policy_version, payload
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (request.promotion_id, request.proposal_id, request.experiment_id, request.evidence_id, request.candidate_version, request.current_production_version, request.requested_at, request.requested_by, request.approval_status.value, request.approval_reason, request.eligibility_status.value, request.status.value, request.created_at, request.promotion_policy_version, json.dumps(request.to_dict())),
            )

    def promotion_request_by_id(self, promotion_id: str) -> dict[str, Any] | None:
        with self._connect() as db:
            row = db.execute("SELECT * FROM promotion_requests WHERE promotion_id = ?", (promotion_id,)).fetchone()
        return dict(row) if row else None

    def find_promotion_requests(self, limit: int = 50) -> list[dict[str, Any]]:
        with self._connect() as db:
            rows = db.execute("SELECT * FROM promotion_requests ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
        return [dict(row) for row in rows]

    def save_promotion_checkpoint(self, checkpoint: Any) -> None:
        with self._connect() as db:
            db.execute(
                """INSERT OR REPLACE INTO promotion_checkpoints(
                    checkpoint_id, production_version, source_commit, configuration,
                    runtime_state, created_at, integrity_hash, active_target, payload
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (checkpoint.checkpoint_id, checkpoint.production_version, checkpoint.source_commit, json.dumps(checkpoint.configuration), json.dumps(checkpoint.runtime_state), checkpoint.created_at, checkpoint.integrity_hash, checkpoint.active_target, json.dumps(checkpoint.to_dict())),
            )

    def checkpoint_by_id(self, checkpoint_id: str) -> dict[str, Any] | None:
        with self._connect() as db:
            row = db.execute("SELECT * FROM promotion_checkpoints WHERE checkpoint_id = ?", (checkpoint_id,)).fetchone()
        return dict(row) if row else None

    def save_promotion_record(self, record: Any) -> None:
        with self._connect() as db:
            db.execute(
                """INSERT OR REPLACE INTO promotion_records(
                    promotion_id, candidate_version, previous_version, proposal_id,
                    experiment_id, evidence_id, checkpoint_id, final_status,
                    promoted_at, rolled_back_at, rollback_reason, payload
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (record.promotion_id, record.candidate_version, record.previous_version, record.proposal_id, record.experiment_id, record.evidence_id, record.checkpoint_id, record.final_status.value, record.promoted_at, record.rolled_back_at, record.rollback_reason, json.dumps(record.to_dict())),
            )

    def promotion_record_by_id(self, promotion_id: str) -> dict[str, Any] | None:
        with self._connect() as db:
            row = db.execute("SELECT * FROM promotion_records WHERE promotion_id = ?", (promotion_id,)).fetchone()
        return dict(row) if row else None

    def save_component(self, component: Any) -> None:
        with self._connect() as db:
            db.execute("""INSERT OR REPLACE INTO components(component_id, name, version, component_type, status, dependencies, interfaces, capabilities, protected, source_reference, integrity_hash, metadata, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""", (component.component_id, component.name, component.version, component.component_type, component.status.value, json.dumps(component.dependencies), json.dumps(component.interfaces), json.dumps(component.capabilities), int(component.protected), component.source_reference, component.integrity_hash, json.dumps(component.metadata), component.created_at))

    def component_by_id(self, component_id: str) -> dict[str, Any] | None:
        with self._connect() as db:
            row = db.execute("SELECT * FROM components WHERE component_id = ?", (component_id,)).fetchone()
        return dict(row) if row else None

    def find_components(self, limit: int = 100) -> list[dict[str, Any]]:
        with self._connect() as db:
            rows = db.execute("SELECT * FROM components ORDER BY name LIMIT ?", (limit,)).fetchall()
        return [dict(row) for row in rows]

    def save_capability(self, capability: Any) -> None:
        with self._connect() as db:
            db.execute("""INSERT OR REPLACE INTO capabilities(capability_id, name, provider_component, version, dependencies, permissions_required, risk_class, status, metadata, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""", (capability.capability_id, capability.name, capability.provider_component, capability.version, json.dumps(capability.dependencies), json.dumps(capability.permissions_required), capability.risk_class, capability.status.value, json.dumps(capability.metadata), capability.created_at))

    def capability_by_id(self, capability_id: str) -> dict[str, Any] | None:
        with self._connect() as db:
            row = db.execute("SELECT * FROM capabilities WHERE capability_id = ?", (capability_id,)).fetchone()
        return dict(row) if row else None

    def find_capabilities(self, limit: int = 100) -> list[dict[str, Any]]:
        with self._connect() as db:
            rows = db.execute("SELECT * FROM capabilities ORDER BY name LIMIT ?", (limit,)).fetchall()
        return [dict(row) for row in rows]

    def save_architecture(self, architecture: Any) -> None:
        with self._connect() as db:
            db.execute("""INSERT OR REPLACE INTO architecture_versions(architecture_version, agent_version, components, capabilities, dependencies, interfaces, protected_components, configuration, integrity_hash, created_at, payload) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""", (architecture.architecture_version, architecture.agent_version, json.dumps(architecture.components), json.dumps(architecture.capabilities), json.dumps(architecture.dependencies), json.dumps(architecture.interfaces), json.dumps(architecture.protected_components), json.dumps(architecture.configuration), architecture.integrity_hash, architecture.created_at, json.dumps(architecture.to_dict())))

    def architecture_by_version(self, architecture_version: str) -> dict[str, Any] | None:
        with self._connect() as db:
            row = db.execute("SELECT * FROM architecture_versions WHERE architecture_version = ?", (architecture_version,)).fetchone()
        return dict(row) if row else None

    def find_architectures(self, limit: int = 50) -> list[dict[str, Any]]:
        with self._connect() as db:
            rows = db.execute("SELECT * FROM architecture_versions ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
        return [dict(row) for row in rows]

    def save_metamorphosis_proposal(self, proposal: Any) -> None:
        with self._connect() as db:
            db.execute("""INSERT OR REPLACE INTO metamorphosis_proposals(proposal_id, change_type, target_component, risk_class, source_version, status, created_at, payload) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""", (proposal.proposal_id, getattr(proposal.change_type, "value", str(proposal.change_type)), proposal.target_component, proposal.risk_class, proposal.source_version, getattr(proposal.status, "value", str(proposal.status)), proposal.created_at, json.dumps(proposal.to_dict())))

    def metamorphosis_proposal_by_id(self, proposal_id: str) -> dict[str, Any] | None:
        with self._connect() as db:
            row = db.execute("SELECT * FROM metamorphosis_proposals WHERE proposal_id = ?", (proposal_id,)).fetchone()
        return dict(row) if row else None

    def find_metamorphosis_proposals(self, limit: int = 50) -> list[dict[str, Any]]:
        with self._connect() as db:
            rows = db.execute("SELECT * FROM metamorphosis_proposals ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
        return [dict(row) for row in rows]

    def save_metamorphosis_experiment(self, experiment: Any) -> None:
        with self._connect() as db:
            db.execute("""INSERT OR REPLACE INTO metamorphosis_experiments(experiment_id, proposal_id, baseline_architecture, candidate_architecture, compatibility_status, benchmark_evidence_id, status, created_at, payload) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""", (experiment.experiment_id, experiment.proposal_id, experiment.baseline_architecture, experiment.candidate_architecture, experiment.compatibility_status.value, experiment.benchmark_evidence_id, experiment.status.value, experiment.created_at, json.dumps(experiment.to_dict())))

    def metamorphosis_experiment_by_id(self, experiment_id: str) -> dict[str, Any] | None:
        with self._connect() as db:
            row = db.execute("SELECT * FROM metamorphosis_experiments WHERE experiment_id = ?", (experiment_id,)).fetchone()
        return dict(row) if row else None

    def find_metamorphosis_experiments(self, limit: int = 50) -> list[dict[str, Any]]:
        with self._connect() as db:
            rows = db.execute("SELECT * FROM metamorphosis_experiments ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
        return [dict(row) for row in rows]

    def save_opportunity(self, opportunity: Any) -> None:
        payload = opportunity.to_dict()
        with self._connect() as db:
            db.execute("INSERT OR REPLACE INTO evolution_opportunities(opportunity_id, fingerprint, status, recommended_change_type, confidence, created_at, updated_at, payload) VALUES (?, ?, ?, ?, ?, ?, ?, ?)", (opportunity.opportunity_id, opportunity.fingerprint, opportunity.status.value, opportunity.recommended_change_type.value, opportunity.confidence, opportunity.created_at, opportunity.updated_at, json.dumps(payload)))

    def opportunity_by_id(self, opportunity_id: str) -> dict[str, Any] | None:
        with self._connect() as db:
            row = db.execute("SELECT * FROM evolution_opportunities WHERE opportunity_id = ?", (opportunity_id,)).fetchone()
        return dict(row) if row else None

    def opportunity_by_fingerprint(self, fingerprint: str) -> dict[str, Any] | None:
        with self._connect() as db:
            row = db.execute("SELECT * FROM evolution_opportunities WHERE fingerprint = ?", (fingerprint,)).fetchone()
        return dict(row) if row else None

    def find_opportunities(self, status: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        with self._connect() as db:
            if status:
                rows = db.execute("SELECT * FROM evolution_opportunities WHERE status = ? ORDER BY created_at DESC LIMIT ?", (status, limit)).fetchall()
            else:
                rows = db.execute("SELECT * FROM evolution_opportunities ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
        return [dict(row) for row in rows]

    def save_work_item(self, work_item: Any) -> None:
        payload = work_item.to_dict()
        with self._connect() as db:
            db.execute("INSERT OR REPLACE INTO evolution_work_items(work_item_id, opportunity_id, change_type, current_state, target_component, target_capability, proposal_id, experiment_id, benchmark_id, evidence_id, promotion_id, current_version, architecture_version, candidate_version, attempt_count, cooldown_until, last_error, created_at, updated_at, payload) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", (work_item.work_item_id, work_item.opportunity_id, work_item.change_type.value, work_item.current_state.value, work_item.target_component, work_item.target_capability, work_item.proposal_id, work_item.experiment_id, work_item.benchmark_id, work_item.evidence_id, work_item.promotion_id, work_item.current_version, work_item.architecture_version, work_item.candidate_version, work_item.attempt_count, work_item.cooldown_until, work_item.last_error, work_item.created_at, work_item.updated_at, json.dumps(payload)))

    def work_item_by_id(self, work_item_id: str) -> dict[str, Any] | None:
        with self._connect() as db:
            row = db.execute("SELECT * FROM evolution_work_items WHERE work_item_id = ?", (work_item_id,)).fetchone()
        return dict(row) if row else None

    def find_work_items(self, state: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        with self._connect() as db:
            if state:
                rows = db.execute("SELECT * FROM evolution_work_items WHERE current_state = ? ORDER BY updated_at DESC LIMIT ?", (state, limit)).fetchall()
            else:
                rows = db.execute("SELECT * FROM evolution_work_items ORDER BY updated_at DESC LIMIT ?", (limit,)).fetchall()
        return [dict(row) for row in rows]

    def save_orchestration_event(self, event: Any) -> None:
        payload = event.to_dict()
        with self._connect() as db:
            db.execute("INSERT OR REPLACE INTO orchestration_events(orchestration_event_id, work_item_id, opportunity_id, event_name, previous_state, current_state, change_type, component, version, actor, reason, result, created_at, payload) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", (event.event_id, event.work_item_id, event.opportunity_id, event.event_name, event.previous_state, event.current_state, event.change_type, event.component, event.version, event.actor, event.reason, event.result, event.created_at, json.dumps(payload)))

    def find_orchestration_events(self, work_item_id: str | None = None, limit: int = 200) -> list[dict[str, Any]]:
        with self._connect() as db:
            if work_item_id:
                rows = db.execute("SELECT * FROM orchestration_events WHERE work_item_id = ? ORDER BY created_at LIMIT ?", (work_item_id, limit)).fetchall()
            else:
                rows = db.execute("SELECT * FROM orchestration_events ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
        return [dict(row) for row in rows]

    def save_approval_request(self, request: Any) -> None:
        payload = request.to_dict()
        with self._connect() as db:
            db.execute("INSERT OR REPLACE INTO approval_requests(approval_request_id, work_item_id, approval_type, status, actor, reason, created_at, updated_at, payload) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", (request.approval_request_id, request.work_item_id, request.approval_type.value, request.status, request.actor, request.reason, request.created_at, request.updated_at, json.dumps(payload)))

    def approval_request_by_id(self, request_id: str) -> dict[str, Any] | None:
        with self._connect() as db:
            row = db.execute("SELECT * FROM approval_requests WHERE approval_request_id = ?", (request_id,)).fetchone()
        return dict(row) if row else None

    def find_approval_requests(self, work_item_id: str | None = None, status: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        clauses: list[str] = []
        values: list[Any] = []
        if work_item_id:
            clauses.append("work_item_id = ?")
            values.append(work_item_id)
        if status:
            clauses.append("status = ?")
            values.append(status)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with self._connect() as db:
            rows = db.execute(f"SELECT * FROM approval_requests {where} ORDER BY created_at DESC LIMIT ?", (*values, limit)).fetchall()
        return [dict(row) for row in rows]

    def save_experiment_queue_item(self, item: Any) -> None:
        payload = item.to_dict()
        with self._connect() as db:
            db.execute("INSERT OR REPLACE INTO experiment_queue(queue_id, work_item_id, engine, experiment_id, status, attempt_count, created_at, updated_at, payload) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", (item.queue_id, item.work_item_id, item.engine, item.experiment_id, item.status.value, item.attempt_count, item.created_at, item.updated_at, json.dumps(payload)))

    def experiment_queue_by_id(self, queue_id: str) -> dict[str, Any] | None:
        with self._connect() as db:
            row = db.execute("SELECT * FROM experiment_queue WHERE queue_id = ?", (queue_id,)).fetchone()
        return dict(row) if row else None

    def find_experiment_queue(self, status: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        with self._connect() as db:
            if status:
                rows = db.execute("SELECT * FROM experiment_queue WHERE status = ? ORDER BY created_at LIMIT ?", (status, limit)).fetchall()
            else:
                rows = db.execute("SELECT * FROM experiment_queue ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
        return [dict(row) for row in rows]

    def save_promotion_queue_item(self, item: Any) -> None:
        payload = item.to_dict()
        with self._connect() as db:
            db.execute("INSERT OR REPLACE INTO promotion_queue(queue_id, work_item_id, promotion_id, candidate_version, status, created_at, updated_at, payload) VALUES (?, ?, ?, ?, ?, ?, ?, ?)", (item.queue_id, item.work_item_id, item.promotion_id, item.candidate_version, item.status.value, item.created_at, item.updated_at, json.dumps(payload)))

    def promotion_queue_by_id(self, queue_id: str) -> dict[str, Any] | None:
        with self._connect() as db:
            row = db.execute("SELECT * FROM promotion_queue WHERE queue_id = ?", (queue_id,)).fetchone()
        return dict(row) if row else None

    def find_promotion_queue(self, status: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        with self._connect() as db:
            if status:
                rows = db.execute("SELECT * FROM promotion_queue WHERE status = ? ORDER BY created_at LIMIT ?", (status, limit)).fetchall()
            else:
                rows = db.execute("SELECT * FROM promotion_queue ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
        return [dict(row) for row in rows]

    def save_cooldown(self, cooldown: Any) -> None:
        payload = cooldown.to_dict()
        with self._connect() as db:
            db.execute("INSERT OR REPLACE INTO evolution_cooldowns(opportunity_key, opportunity_id, attempt_count, last_attempt, last_result, cooldown_until, payload) VALUES (?, ?, ?, ?, ?, ?, ?)", (cooldown.opportunity_key, cooldown.opportunity_id, cooldown.attempt_count, cooldown.last_attempt, cooldown.last_result, cooldown.cooldown_until, json.dumps(payload)))

    def cooldown_by_key(self, opportunity_key: str) -> dict[str, Any] | None:
        with self._connect() as db:
            row = db.execute("SELECT * FROM evolution_cooldowns WHERE opportunity_key = ?", (opportunity_key,)).fetchone()
        return dict(row) if row else None

    def find_cooldowns(self, limit: int = 100) -> list[dict[str, Any]]:
        with self._connect() as db:
            rows = db.execute("SELECT * FROM evolution_cooldowns ORDER BY last_attempt DESC LIMIT ?", (limit,)).fetchall()
        return [dict(row) for row in rows]

    def save_rollback_record(self, rollback: Any) -> None:
        with self._connect() as db:
            db.execute(
                """INSERT OR REPLACE INTO rollback_records(
                    rollback_id, promotion_id, from_version, to_version, checkpoint_id,
                    reason, started_at, completed_at, status, payload
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (rollback.rollback_id, rollback.promotion_id, rollback.from_version, rollback.to_version, rollback.checkpoint_id, rollback.reason, rollback.started_at, rollback.completed_at, rollback.status, json.dumps(rollback.to_dict())),
            )

    def rollback_record_by_id(self, rollback_id: str) -> dict[str, Any] | None:
        with self._connect() as db:
            row = db.execute("SELECT * FROM rollback_records WHERE rollback_id = ?", (rollback_id,)).fetchone()
        return dict(row) if row else None

    def find_rollback_records(self, limit: int = 50) -> list[dict[str, Any]]:
        with self._connect() as db:
            rows = db.execute("SELECT * FROM rollback_records ORDER BY started_at DESC LIMIT ?", (limit,)).fetchall()
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
