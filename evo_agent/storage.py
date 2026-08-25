from __future__ import annotations

from datetime import datetime, timezone
import json
import sqlite3
from pathlib import Path
from typing import Any

from .models import Event, Goal, TaskStatus, new_id


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
                CREATE TABLE IF NOT EXISTS intelligence_tools (
                    tool_id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    version TEXT NOT NULL,
                    provider TEXT NOT NULL,
                    risk_level TEXT NOT NULL,
                    status TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_intelligence_tools_name ON intelligence_tools(name);
                CREATE INDEX IF NOT EXISTS idx_intelligence_tools_status ON intelligence_tools(status);
                CREATE TABLE IF NOT EXISTS environment_snapshots (
                    snapshot_id TEXT PRIMARY KEY,
                    environment_id TEXT NOT NULL,
                    timestamp TEXT NOT NULL,
                    environment_version TEXT NOT NULL,
                    agent_version TEXT NOT NULL,
                    architecture_version TEXT NOT NULL,
                    observation_hash TEXT NOT NULL,
                    observation_summary TEXT NOT NULL,
                    provenance TEXT NOT NULL,
                    schema_version TEXT NOT NULL,
                    immutable_hash TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_environment_snapshots_environment ON environment_snapshots(environment_id, timestamp);
                CREATE TABLE IF NOT EXISTS world_observations (
                    observation_id TEXT PRIMARY KEY,
                    environment_id TEXT NOT NULL,
                    observation_type TEXT NOT NULL,
                    source TEXT NOT NULL,
                    timestamp TEXT NOT NULL,
                    value TEXT NOT NULL,
                    confidence REAL NOT NULL,
                    reliability REAL NOT NULL,
                    provenance TEXT NOT NULL,
                    trust_level TEXT NOT NULL,
                    expiry TEXT,
                    metadata TEXT NOT NULL,
                    observation_hash TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_world_observations_environment ON world_observations(environment_id, timestamp);
                CREATE TABLE IF NOT EXISTS world_assumptions (
                    assumption_id TEXT PRIMARY KEY,
                    statement TEXT NOT NULL,
                    source TEXT NOT NULL,
                    confidence REAL NOT NULL,
                    created_at TEXT NOT NULL,
                    expiry TEXT,
                    validation_state TEXT NOT NULL,
                    environment_id TEXT NOT NULL,
                    metadata TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS world_conflicts (
                    conflict_id TEXT PRIMARY KEY,
                    subject TEXT NOT NULL,
                    current_value TEXT NOT NULL,
                    historical_value TEXT NOT NULL,
                    current_source TEXT NOT NULL,
                    historical_source TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    resolution TEXT NOT NULL,
                    metadata TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS environment_diffs (
                    diff_id TEXT PRIMARY KEY,
                    before_snapshot_id TEXT NOT NULL,
                    after_snapshot_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    payload TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS world_refresh_requirements (
                    refresh_id TEXT PRIMARY KEY,
                    kind TEXT NOT NULL,
                    subject TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    requested_at TEXT NOT NULL,
                    ttl_seconds INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    metadata TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS world_provider_states (
                    provider_state_id TEXT PRIMARY KEY,
                    provider TEXT NOT NULL,
                    observed_at TEXT NOT NULL,
                    payload TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS runtime_states (
                    runtime_id TEXT PRIMARY KEY,
                    runtime_version TEXT NOT NULL,
                    agent_version TEXT NOT NULL,
                    architecture_version TEXT NOT NULL,
                    state TEXT NOT NULL,
                    started_at TEXT,
                    last_heartbeat TEXT,
                    last_observation TEXT,
                    current_task TEXT,
                    current_plan TEXT,
                    current_environment TEXT,
                    current_world_snapshot TEXT,
                    shutdown_reason TEXT,
                    failure_reason TEXT,
                    restart_count INTEGER NOT NULL,
                    metadata TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS runtime_tasks (
                    task_id TEXT PRIMARY KEY,
                    goal TEXT NOT NULL,
                    priority TEXT NOT NULL,
                    source TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    dependencies TEXT NOT NULL,
                    deadline TEXT,
                    resource_budget TEXT NOT NULL,
                    approval_requirement TEXT NOT NULL,
                    retry_budget INTEGER NOT NULL,
                    current_attempt INTEGER NOT NULL,
                    plan_id TEXT,
                    environment_version TEXT,
                    agent_version TEXT NOT NULL,
                    fingerprint TEXT NOT NULL UNIQUE,
                    progress TEXT NOT NULL,
                    last_error TEXT,
                    metadata TEXT NOT NULL,
                    payload TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_runtime_tasks_status ON runtime_tasks(status);
                CREATE INDEX IF NOT EXISTS idx_runtime_tasks_priority ON runtime_tasks(priority, created_at);
                CREATE INDEX IF NOT EXISTS idx_runtime_tasks_fingerprint ON runtime_tasks(fingerprint);
                CREATE TABLE IF NOT EXISTS runtime_schedules (
                    schedule_id TEXT PRIMARY KEY,
                    goal TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    priority TEXT NOT NULL,
                    source TEXT NOT NULL,
                    run_at TEXT,
                    interval_seconds INTEGER,
                    condition TEXT NOT NULL,
                    dependencies TEXT NOT NULL,
                    deadline_seconds INTEGER,
                    resource_budget TEXT NOT NULL,
                    approval_requirement TEXT NOT NULL,
                    enabled INTEGER NOT NULL,
                    next_run_at TEXT,
                    last_enqueued_at TEXT,
                    run_count INTEGER NOT NULL,
                    max_runs INTEGER,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    metadata TEXT NOT NULL,
                    payload TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_runtime_schedules_due ON runtime_schedules(enabled, next_run_at);
                CREATE TABLE IF NOT EXISTS runtime_approvals (
                    approval_id TEXT PRIMARY KEY,
                    task_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    actor TEXT NOT NULL,
                    scope_hash TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    metadata TEXT NOT NULL,
                    payload TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_runtime_approvals_task ON runtime_approvals(task_id, status);
                CREATE INDEX IF NOT EXISTS idx_runtime_approvals_status ON runtime_approvals(status);
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
                CREATE TABLE IF NOT EXISTS cognitive_goals (
                    goal_id TEXT PRIMARY KEY,
                    original_text TEXT NOT NULL,
                    normalized_goal TEXT NOT NULL,
                    objective TEXT NOT NULL,
                    constraints TEXT NOT NULL,
                    resources TEXT NOT NULL,
                    expected_outputs TEXT NOT NULL,
                    success_criteria TEXT NOT NULL,
                    risks TEXT NOT NULL,
                    ambiguity TEXT NOT NULL,
                    confidence REAL NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    payload TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS cognitive_intents (
                    goal_id TEXT PRIMARY KEY,
                    payload TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS cognitive_plans (
                    plan_id TEXT PRIMARY KEY,
                    goal_id TEXT NOT NULL,
                    plan_version TEXT NOT NULL,
                    agent_version TEXT NOT NULL,
                    architecture_version TEXT NOT NULL,
                    steps TEXT NOT NULL,
                    dependencies TEXT NOT NULL,
                    required_tools TEXT NOT NULL,
                    required_capabilities TEXT NOT NULL,
                    estimated_cost REAL NOT NULL,
                    estimated_risk TEXT NOT NULL,
                    expected_result TEXT NOT NULL,
                    selected INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    payload TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_cognitive_plans_goal ON cognitive_plans(goal_id);
                CREATE TABLE IF NOT EXISTS cognitive_task_graphs (
                    graph_id TEXT PRIMARY KEY,
                    goal_id TEXT NOT NULL,
                    graph_type TEXT NOT NULL,
                    nodes TEXT NOT NULL,
                    edges TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    payload TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS cognitive_states (
                    goal_id TEXT PRIMARY KEY,
                    state TEXT NOT NULL,
                    current_task_id TEXT,
                    replan_count INTEGER NOT NULL,
                    tool_call_count INTEGER NOT NULL,
                    last_error TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    payload TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS cognitive_task_steps (
                    task_id TEXT PRIMARY KEY,
                    goal_id TEXT NOT NULL,
                    parent_task_id TEXT,
                    description TEXT NOT NULL,
                    dependencies TEXT NOT NULL,
                    inputs TEXT NOT NULL,
                    expected_outputs TEXT NOT NULL,
                    success_criteria TEXT NOT NULL,
                    required_capabilities TEXT NOT NULL,
                    risk TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    payload TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_cognitive_steps_goal ON cognitive_task_steps(goal_id);
                CREATE TABLE IF NOT EXISTS cognitive_observations (
                    observation_id TEXT PRIMARY KEY,
                    goal_id TEXT NOT NULL,
                    task_id TEXT NOT NULL,
                    tool TEXT,
                    output TEXT NOT NULL,
                    status TEXT NOT NULL,
                    errors TEXT NOT NULL,
                    artifacts TEXT NOT NULL,
                    duration REAL NOT NULL,
                    side_effects TEXT NOT NULL,
                    verification_hints TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    payload TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_cognitive_observations_goal ON cognitive_observations(goal_id);
                CREATE TABLE IF NOT EXISTS cognitive_decisions (
                    decision_id TEXT PRIMARY KEY,
                    goal_id TEXT NOT NULL,
                    task_id TEXT,
                    decision_type TEXT NOT NULL,
                    decision TEXT NOT NULL,
                    confidence TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    payload TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_cognitive_decisions_goal ON cognitive_decisions(goal_id);
                CREATE TABLE IF NOT EXISTS cognitive_verification_results (
                    verification_id TEXT PRIMARY KEY,
                    goal_id TEXT NOT NULL,
                    task_id TEXT,
                    success INTEGER NOT NULL,
                    outcome TEXT NOT NULL,
                    summary TEXT NOT NULL,
                    checks TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    payload TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_cognitive_verification_goal ON cognitive_verification_results(goal_id);
                CREATE TABLE IF NOT EXISTS memory_records (
                    memory_id TEXT PRIMARY KEY,
                    memory_type TEXT NOT NULL,
                    content TEXT NOT NULL,
                    summary TEXT NOT NULL,
                    source TEXT NOT NULL,
                    source_id TEXT NOT NULL,
                    provenance TEXT NOT NULL,
                    confidence TEXT NOT NULL,
                    confidence_score REAL NOT NULL,
                    importance REAL NOT NULL,
                    relevance REAL NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    last_accessed_at TEXT,
                    access_count INTEGER NOT NULL,
                    version INTEGER NOT NULL,
                    memory_version TEXT NOT NULL,
                    status TEXT NOT NULL,
                    expiration TEXT,
                    valid_from TEXT,
                    valid_until TEXT,
                    agent_version TEXT NOT NULL,
                    architecture_version TEXT NOT NULL,
                    source_version TEXT NOT NULL,
                    environment TEXT NOT NULL,
                    knowledge_kind TEXT,
                    fingerprint TEXT NOT NULL UNIQUE,
                    memory_key TEXT NOT NULL,
                    source_ids TEXT NOT NULL,
                    first_seen TEXT NOT NULL,
                    last_seen TEXT NOT NULL,
                    occurrence_count INTEGER NOT NULL,
                    metadata TEXT NOT NULL,
                    payload TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_memory_type_status ON memory_records(memory_type, status);
                CREATE INDEX IF NOT EXISTS idx_memory_source ON memory_records(source, source_id);
                CREATE INDEX IF NOT EXISTS idx_memory_key ON memory_records(memory_key);
                CREATE TABLE IF NOT EXISTS memory_history (
                    history_id TEXT PRIMARY KEY,
                    memory_id TEXT NOT NULL,
                    version INTEGER NOT NULL,
                    snapshot TEXT NOT NULL,
                    changed_at TEXT NOT NULL,
                    reason TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_memory_history_memory ON memory_history(memory_id, version);
                CREATE TABLE IF NOT EXISTS memory_links (
                    link_id TEXT PRIMARY KEY,
                    memory_id TEXT NOT NULL,
                    parent_id TEXT NOT NULL,
                    relation TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_memory_links_memory ON memory_links(memory_id);
                CREATE TABLE IF NOT EXISTS memory_procedures (
                    procedure_id TEXT PRIMARY KEY,
                    task_type TEXT NOT NULL,
                    name TEXT NOT NULL,
                    steps TEXT NOT NULL,
                    required_capabilities TEXT NOT NULL,
                    required_tools TEXT NOT NULL,
                    constraints TEXT NOT NULL,
                    success_history INTEGER NOT NULL,
                    failure_history INTEGER NOT NULL,
                    confidence TEXT NOT NULL,
                    confidence_score REAL NOT NULL,
                    source_experiences TEXT NOT NULL,
                    version INTEGER NOT NULL,
                    agent_version TEXT NOT NULL,
                    architecture_version TEXT NOT NULL,
                    environment TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    metadata TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_memory_procedures_type ON memory_procedures(task_type, status);
                CREATE TABLE IF NOT EXISTS memory_feedback (
                    feedback_id TEXT PRIMARY KEY,
                    memory_id TEXT NOT NULL,
                    feedback TEXT NOT NULL,
                    note TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS memory_events (
                    event_id TEXT PRIMARY KEY,
                    memory_id TEXT NOT NULL,
                    event_name TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                """
            )
            columns = {row["name"] for row in db.execute("PRAGMA table_info(memory_records)").fetchall()}
            if columns and "payload" not in columns:
                db.execute("ALTER TABLE memory_records ADD COLUMN payload TEXT NOT NULL DEFAULT '{}' ")

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

    def count_events(self, event_type: str) -> int:
        with self._connect() as db:
            row = db.execute("SELECT COUNT(*) AS count FROM events WHERE event_type = ?", (event_type,)).fetchone()
        return int(row["count"]) if row else 0

    def add_memory(self, kind: str, content: str, created_at: str) -> None:
        with self._connect() as db:
            db.execute("INSERT INTO memories(kind, content, created_at) VALUES (?, ?, ?)", (kind, content, created_at))

    def recent_memories(self, limit: int = 20) -> list[dict[str, Any]]:
        with self._connect() as db:
            rows = db.execute("SELECT kind, content, created_at FROM memories ORDER BY memory_id DESC LIMIT ?", (limit,)).fetchall()
        return [dict(row) for row in rows]

    def save_memory(self, record: Any) -> None:
        payload = record.to_dict()
        with self._connect() as db:
            existing = db.execute("SELECT payload, version, fingerprint, content, status FROM memory_records WHERE memory_id = ?", (record.memory_id,)).fetchone()
            if existing and (existing["content"] != record.content or existing["status"] != record.status.value or existing["version"] != record.version):
                db.execute("INSERT INTO memory_history(history_id, memory_id, version, snapshot, changed_at, reason) VALUES (?, ?, ?, ?, ?, ?)", (new_id("memory_history"), record.memory_id, existing["version"], existing["payload"], datetime.now(timezone.utc).isoformat(), str(record.metadata.get("update_reason", "versioned memory update"))))
            fingerprint = record.metadata.get("fingerprint", "")
            if not fingerprint:
                import hashlib
                fingerprint = hashlib.sha256(json.dumps({"type": record.type.value, "key": record.key, "content": record.content.strip().lower()}, sort_keys=True).encode()).hexdigest()
                record.metadata["fingerprint"] = fingerprint
                payload = record.to_dict()
            environment = json.dumps(record.environment.to_dict())
            db.execute("INSERT OR REPLACE INTO memory_records(memory_id, memory_type, content, summary, source, source_id, provenance, confidence, confidence_score, importance, relevance, created_at, updated_at, last_accessed_at, access_count, version, memory_version, status, expiration, valid_from, valid_until, agent_version, architecture_version, source_version, environment, knowledge_kind, fingerprint, memory_key, source_ids, first_seen, last_seen, occurrence_count, metadata, payload) VALUES (?,?,?,?,?,?,?,?,?,? ,?,?,?,?,?,?,?,?,?,? ,?,?,?,?,?,?,?,?,?,? ,?,?,?,?)", (record.memory_id, record.type.value, record.content, record.summary, record.source.value, record.source_id, json.dumps(record.provenance.to_dict()), record.confidence.value, record.confidence_score, record.importance, record.relevance, record.created_at, record.updated_at, record.last_accessed_at, record.access_count, record.version, record.memory_version, record.status.value, record.expiration, record.valid_from, record.valid_until, record.agent_version, record.architecture_version, record.source_version, environment, record.knowledge_kind.value if record.knowledge_kind else None, fingerprint, record.key, json.dumps(record.source_ids), record.first_seen or record.created_at, record.last_seen or record.updated_at, record.occurrence_count, json.dumps(record.metadata), json.dumps(payload)))

    def memory_by_id(self, memory_id: str) -> dict[str, Any] | None:
        with self._connect() as db:
            row = db.execute("SELECT * FROM memory_records WHERE memory_id = ?", (memory_id,)).fetchone()
        return dict(row) if row else None

    def memory_by_fingerprint(self, fingerprint: str) -> dict[str, Any] | None:
        with self._connect() as db:
            row = db.execute("SELECT * FROM memory_records WHERE fingerprint = ?", (fingerprint,)).fetchone()
        return dict(row) if row else None

    def memories_by_key(self, memory_key: str, limit: int = 100) -> list[dict[str, Any]]:
        with self._connect() as db:
            rows = db.execute("SELECT * FROM memory_records WHERE memory_key = ? ORDER BY updated_at DESC LIMIT ?", (memory_key, limit)).fetchall()
        return [dict(row) for row in rows]

    def find_memory_conflicts(self, memory_key: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        clauses = ["status = 'conflict'"]
        values: list[Any] = []
        if memory_key:
            clauses.append("memory_key = ?")
            values.append(memory_key)
        with self._connect() as db:
            rows = db.execute(f"SELECT * FROM memory_records WHERE {' AND '.join(clauses)} ORDER BY updated_at DESC LIMIT ?", (*values, limit)).fetchall()
        return [dict(row) for row in rows]

    def find_memories(self, memory_type: str | None = None, status: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        clauses: list[str] = []
        values: list[Any] = []
        if memory_type:
            clauses.append("memory_type = ?")
            values.append(memory_type)
        if status:
            clauses.append("status = ?")
            values.append(status)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with self._connect() as db:
            rows = db.execute(f"SELECT * FROM memory_records {where} ORDER BY updated_at DESC LIMIT ?", (*values, limit)).fetchall()
        return [dict(row) for row in rows]

    def memory_history(self, memory_id: str) -> list[dict[str, Any]]:
        with self._connect() as db:
            rows = db.execute("SELECT snapshot FROM memory_history WHERE memory_id = ? ORDER BY version DESC", (memory_id,)).fetchall()
        return [{"payload": row["snapshot"]} for row in rows]

    def save_memory_link(self, memory_id: str, parent_id: str, relation: str) -> None:
        with self._connect() as db:
            db.execute("INSERT INTO memory_links(link_id, memory_id, parent_id, relation, created_at) VALUES (?, ?, ?, ?, ?)", (new_id("memory_link"), memory_id, parent_id, relation, datetime.now(timezone.utc).isoformat()))

    def memory_links(self, memory_id: str) -> list[dict[str, Any]]:
        with self._connect() as db:
            rows = db.execute("SELECT * FROM memory_links WHERE memory_id = ? OR parent_id = ? ORDER BY created_at", (memory_id, memory_id)).fetchall()
        return [dict(row) for row in rows]

    def save_memory_event(self, memory_id: str, event_name: str, payload: dict[str, Any]) -> None:
        with self._connect() as db:
            db.execute("INSERT INTO memory_events(event_id, memory_id, event_name, payload, created_at) VALUES (?, ?, ?, ?, ?)", (new_id("memory_event"), memory_id, event_name, json.dumps(payload), datetime.now(timezone.utc).isoformat()))

    def save_procedure(self, procedure: Any) -> None:
        with self._connect() as db:
            db.execute("INSERT OR REPLACE INTO memory_procedures(procedure_id, task_type, name, steps, required_capabilities, required_tools, constraints, success_history, failure_history, confidence, confidence_score, source_experiences, version, agent_version, architecture_version, environment, status, created_at, updated_at, metadata) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", (procedure.procedure_id, procedure.task_type, procedure.name, json.dumps(procedure.steps), json.dumps(procedure.required_capabilities), json.dumps(procedure.required_tools), json.dumps(procedure.constraints), procedure.success_history, procedure.failure_history, procedure.confidence.value, procedure.confidence_score, json.dumps(procedure.source_experiences), procedure.version, procedure.agent_version, procedure.architecture_version, json.dumps(procedure.environment.to_dict()), procedure.status.value, procedure.created_at, procedure.updated_at, json.dumps(procedure.metadata)))

    def find_procedures(self, task_type: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        with self._connect() as db:
            if task_type:
                rows = db.execute("SELECT * FROM memory_procedures WHERE task_type = ? ORDER BY confidence_score DESC, updated_at DESC LIMIT ?", (task_type, limit)).fetchall()
            else:
                rows = db.execute("SELECT * FROM memory_procedures ORDER BY confidence_score DESC, updated_at DESC LIMIT ?", (limit,)).fetchall()
        return [dict(row) for row in rows]

    def save_memory_feedback(self, memory_id: str, feedback: str, note: str = "") -> None:
        with self._connect() as db:
            db.execute("INSERT INTO memory_feedback(feedback_id, memory_id, feedback, note, created_at) VALUES (?, ?, ?, ?, ?)", (new_id("memory_feedback"), memory_id, feedback, note, datetime.now(timezone.utc).isoformat()))

    def memory_statistics(self) -> dict[str, Any]:
        with self._connect() as db:
            total = db.execute("SELECT COUNT(*) AS n FROM memory_records").fetchone()["n"]
            type_rows = db.execute("SELECT memory_type, COUNT(*) AS n FROM memory_records GROUP BY memory_type").fetchall()
            status_rows = db.execute("SELECT status, COUNT(*) AS n FROM memory_records GROUP BY status").fetchall()
            duplicates = db.execute("SELECT COALESCE(SUM(occurrence_count - 1), 0) AS n FROM memory_records").fetchone()["n"]
        result = {"total_memories": total, "working_memories": 0, "episodic_memories": 0, "semantic_memories": 0, "procedural_memories": 0, "user_memories": 0, "archived_memories": 0, "expired_memories": 0, "conflicts": 0, "duplicates": int(duplicates or 0)}
        for row in type_rows:
            result[f"{row['memory_type']}_memories"] = row["n"]
        for row in status_rows:
            if row["status"] in {"archived", "expired"}:
                result[f"{row['status']}_memories"] = row["n"]
        return result

    def average_memory_score(self) -> float:
        with self._connect() as db:
            row = db.execute("SELECT AVG(relevance) AS score FROM memory_records").fetchone()
        return float(row["score"] or 0.0)

    def memory_schema_valid(self) -> bool:
        required = {"memory_records", "memory_history", "memory_links", "memory_procedures", "memory_feedback", "memory_events"}
        required_columns = {"memory_id", "provenance", "fingerprint", "metadata", "payload"}
        with self._connect() as db:
            rows = db.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
            tables = {row["name"] for row in rows}
            if not required.issubset(tables):
                return False
            columns = {row["name"] for row in db.execute("PRAGMA table_info(memory_records)").fetchall()}
        return required_columns.issubset(columns)

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

    def save_intelligence_tool(self, tool: Any) -> None:
        payload = tool.to_dict()
        with self._connect() as db:
            db.execute("INSERT OR REPLACE INTO intelligence_tools(tool_id, name, version, provider, risk_level, status, payload, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", (tool.tool_id, tool.name, tool.version, tool.provider, tool.risk_level.value, tool.status.value, json.dumps(payload), tool.created_at, tool.updated_at))

    def intelligence_tool_by_id(self, tool_id: str) -> dict[str, Any] | None:
        with self._connect() as db:
            row = db.execute("SELECT * FROM intelligence_tools WHERE tool_id = ?", (tool_id,)).fetchone()
        return dict(row) if row else None

    def find_intelligence_tools(self, limit: int = 1000) -> list[dict[str, Any]]:
        with self._connect() as db:
            rows = db.execute("SELECT * FROM intelligence_tools ORDER BY name, tool_id LIMIT ?", (limit,)).fetchall()
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

    def save_cognitive_goal(self, goal: Any) -> None:
        payload = goal.to_dict()
        with self._connect() as db:
            db.execute("INSERT OR REPLACE INTO cognitive_goals(goal_id, original_text, normalized_goal, objective, constraints, resources, expected_outputs, success_criteria, risks, ambiguity, confidence, status, created_at, updated_at, payload) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", (goal.goal_id, goal.original_text, goal.normalized_goal, goal.objective, json.dumps([item.to_dict() for item in goal.constraints]), json.dumps(goal.resources), json.dumps(goal.expected_outputs), json.dumps([item.to_dict() for item in goal.success_criteria]), json.dumps(goal.risks), goal.ambiguity.value, goal.confidence, goal.status.value, goal.created_at, goal.updated_at, json.dumps(payload)))

    def cognitive_goal_by_id(self, goal_id: str) -> dict[str, Any] | None:
        with self._connect() as db:
            row = db.execute("SELECT * FROM cognitive_goals WHERE goal_id = ?", (goal_id,)).fetchone()
        return dict(row) if row else None

    def find_cognitive_goals(self, limit: int = 50) -> list[dict[str, Any]]:
        with self._connect() as db:
            rows = db.execute("SELECT * FROM cognitive_goals ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
        return [dict(row) for row in rows]

    def save_cognitive_intent(self, intent: Any) -> None:
        payload = intent.to_dict()
        with self._connect() as db:
            db.execute("INSERT OR REPLACE INTO cognitive_intents(goal_id, payload, created_at) VALUES (?, ?, ?)", (intent.goal_id, json.dumps(payload), datetime.now(timezone.utc).isoformat()))

    def cognitive_intent_by_goal(self, goal_id: str) -> dict[str, Any] | None:
        with self._connect() as db:
            row = db.execute("SELECT * FROM cognitive_intents WHERE goal_id = ?", (goal_id,)).fetchone()
        return dict(row) if row else None

    def save_cognitive_plan(self, plan: Any) -> None:
        payload = plan.to_dict()
        with self._connect() as db:
            db.execute("INSERT OR REPLACE INTO cognitive_plans(plan_id, goal_id, plan_version, agent_version, architecture_version, steps, dependencies, required_tools, required_capabilities, estimated_cost, estimated_risk, expected_result, selected, created_at, payload) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", (plan.plan_id, plan.goal_id, plan.plan_version, plan.agent_version, plan.architecture_version, json.dumps(plan.steps), json.dumps(plan.dependencies), json.dumps(plan.required_tools), json.dumps(plan.required_capabilities), plan.estimated_cost, plan.estimated_risk.value, plan.expected_result, int(plan.selected), plan.created_at, json.dumps(payload)))

    def cognitive_plan_by_id(self, plan_id: str) -> dict[str, Any] | None:
        with self._connect() as db:
            row = db.execute("SELECT * FROM cognitive_plans WHERE plan_id = ?", (plan_id,)).fetchone()
        return dict(row) if row else None

    def find_cognitive_plans(self, goal_id: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        with self._connect() as db:
            if goal_id:
                rows = db.execute("SELECT * FROM cognitive_plans WHERE goal_id = ? ORDER BY created_at DESC LIMIT ?", (goal_id, limit)).fetchall()
            else:
                rows = db.execute("SELECT * FROM cognitive_plans ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
        return [dict(row) for row in rows]

    def cognitive_plan_by_goal(self, goal_id: str, selected: bool | None = None) -> dict[str, Any] | None:
        query = "SELECT * FROM cognitive_plans WHERE goal_id = ?"
        values: tuple[Any, ...] = (goal_id,)
        if selected is not None:
            query += " AND selected = ?"
            values += (int(selected),)
        query += " ORDER BY created_at DESC LIMIT 1"
        with self._connect() as db:
            row = db.execute(query, values).fetchone()
        return dict(row) if row else None

    def save_cognitive_task_graph(self, graph: Any) -> None:
        payload = graph.to_dict()
        with self._connect() as db:
            db.execute("INSERT OR REPLACE INTO cognitive_task_graphs(graph_id, goal_id, graph_type, nodes, edges, created_at, payload) VALUES (?, ?, ?, ?, ?, ?, ?)", (graph.graph_id, graph.goal_id, graph.graph_type.value, json.dumps([node.task_id for node in graph.nodes]), json.dumps([list(edge) for edge in graph.edges]), graph.created_at, json.dumps(payload)))

    def find_cognitive_task_graphs(self, goal_id: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        with self._connect() as db:
            if goal_id:
                rows = db.execute("SELECT * FROM cognitive_task_graphs WHERE goal_id = ? ORDER BY created_at DESC LIMIT ?", (goal_id, limit)).fetchall()
            else:
                rows = db.execute("SELECT * FROM cognitive_task_graphs ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
        return [dict(row) for row in rows]

    def cognitive_task_graph_by_goal(self, goal_id: str) -> dict[str, Any] | None:
        with self._connect() as db:
            row = db.execute("SELECT * FROM cognitive_task_graphs WHERE goal_id = ? ORDER BY created_at DESC LIMIT 1", (goal_id,)).fetchone()
        return dict(row) if row else None

    def save_cognitive_state(self, state: Any) -> None:
        payload = state.to_dict()
        with self._connect() as db:
            db.execute("INSERT OR REPLACE INTO cognitive_states(goal_id, state, current_task_id, replan_count, tool_call_count, last_error, created_at, updated_at, payload) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", (state.goal_id, state.state.value, state.current_task_id, state.replan_count, state.tool_call_count, state.last_error, state.created_at, state.updated_at, json.dumps(payload)))

    def cognitive_state_by_goal(self, goal_id: str) -> dict[str, Any] | None:
        with self._connect() as db:
            row = db.execute("SELECT * FROM cognitive_states WHERE goal_id = ?", (goal_id,)).fetchone()
        return dict(row) if row else None

    def save_cognitive_task(self, task: Any) -> None:
        payload = task.to_dict()
        with self._connect() as db:
            db.execute("INSERT OR REPLACE INTO cognitive_task_steps(task_id, goal_id, parent_task_id, description, dependencies, inputs, expected_outputs, success_criteria, required_capabilities, risk, status, created_at, updated_at, payload) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", (task.task_id, task.goal_id, task.parent_task_id, task.description, json.dumps(task.dependencies), json.dumps(task.inputs), json.dumps(task.expected_outputs), json.dumps(task.success_criteria), json.dumps(task.required_capabilities), task.risk.value, task.status.value, task.created_at, task.updated_at, json.dumps(payload)))

    def cognitive_task_by_id(self, task_id: str) -> dict[str, Any] | None:
        with self._connect() as db:
            row = db.execute("SELECT * FROM cognitive_task_steps WHERE task_id = ?", (task_id,)).fetchone()
        return dict(row) if row else None

    def find_cognitive_tasks(self, goal_id: str, limit: int = 100) -> list[dict[str, Any]]:
        with self._connect() as db:
            rows = db.execute("SELECT * FROM cognitive_task_steps WHERE goal_id = ? ORDER BY created_at LIMIT ?", (goal_id, limit)).fetchall()
        return [dict(row) for row in rows]

    def save_cognitive_observation(self, observation: Any) -> None:
        payload = observation.to_dict()
        with self._connect() as db:
            db.execute("INSERT OR REPLACE INTO cognitive_observations(observation_id, goal_id, task_id, tool, output, status, errors, artifacts, duration, side_effects, verification_hints, created_at, payload) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", (observation.observation_id, observation.goal_id, observation.task_id, observation.tool, observation.output, observation.status, json.dumps(observation.errors), json.dumps(observation.artifacts), observation.duration, json.dumps(observation.side_effects), json.dumps(observation.verification_hints), observation.created_at, json.dumps(payload)))

    def find_cognitive_observations(self, goal_id: str, limit: int = 500) -> list[dict[str, Any]]:
        with self._connect() as db:
            rows = db.execute("SELECT * FROM cognitive_observations WHERE goal_id = ? ORDER BY created_at LIMIT ?", (goal_id, limit)).fetchall()
        return [dict(row) for row in rows]

    def save_cognitive_decision(self, decision: dict[str, Any]) -> None:
        with self._connect() as db:
            db.execute("INSERT OR REPLACE INTO cognitive_decisions(decision_id, goal_id, task_id, decision_type, decision, confidence, reason, created_at, payload) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", (decision.get("decision_id", new_id("decision")), decision["goal_id"], decision.get("task_id"), decision.get("decision_type", "unknown"), decision.get("decision", ""), decision.get("confidence", "uncertain"), decision.get("reason", ""), decision.get("created_at", datetime.now(timezone.utc).isoformat()), json.dumps(decision)))

    def find_cognitive_decisions(self, goal_id: str, limit: int = 500) -> list[dict[str, Any]]:
        with self._connect() as db:
            rows = db.execute("SELECT * FROM cognitive_decisions WHERE goal_id = ? ORDER BY created_at LIMIT ?", (goal_id, limit)).fetchall()
        return [dict(row) for row in rows]

    def save_cognitive_verification(self, verification: Any) -> None:
        payload = verification.to_dict()
        with self._connect() as db:
            db.execute("INSERT OR REPLACE INTO cognitive_verification_results(verification_id, goal_id, task_id, success, outcome, summary, checks, created_at, payload) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", (payload.get("verification_id", new_id("verification")), verification.goal_id, payload.get("task_id"), int(verification.success), verification.outcome.value, verification.summary, json.dumps(verification.checks), datetime.now(timezone.utc).isoformat(), json.dumps(payload)))

    def cognitive_verification_by_goal(self, goal_id: str) -> dict[str, Any] | None:
        with self._connect() as db:
            row = db.execute("SELECT * FROM cognitive_verification_results WHERE goal_id = ? ORDER BY created_at DESC LIMIT 1", (goal_id,)).fetchone()
        return dict(row) if row else None

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

    def save_environment_snapshot(self, snapshot: Any) -> None:
        payload = snapshot.to_dict()
        with self._connect() as db:
            existing = db.execute("SELECT immutable_hash, observation_hash FROM environment_snapshots WHERE snapshot_id = ?", (snapshot.snapshot_id,)).fetchone()
            if existing:
                if existing["immutable_hash"] != snapshot.immutable_hash or existing["observation_hash"] != snapshot.observation_hash:
                    raise ValueError("environment snapshots are immutable")
                return
            db.execute("INSERT INTO environment_snapshots(snapshot_id, environment_id, timestamp, environment_version, agent_version, architecture_version, observation_hash, observation_summary, provenance, schema_version, immutable_hash) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", (snapshot.snapshot_id, snapshot.environment_id, snapshot.timestamp, snapshot.environment_version, snapshot.agent_version, snapshot.architecture_version, snapshot.observation_hash, json.dumps(snapshot.observation_summary), json.dumps(snapshot.provenance), snapshot.schema_version, snapshot.immutable_hash))

    def environment_snapshot_by_id(self, snapshot_id: str) -> Any | None:
        from .world import EnvironmentSnapshot
        with self._connect() as db:
            row = db.execute("SELECT * FROM environment_snapshots WHERE snapshot_id = ?", (snapshot_id,)).fetchone()
        if not row:
            return None
        try:
            snapshot = EnvironmentSnapshot(row["snapshot_id"], row["environment_id"], row["timestamp"], row["environment_version"], row["agent_version"], row["architecture_version"], row["observation_hash"], json.loads(row["observation_summary"]), json.loads(row["provenance"]), row["schema_version"], row["immutable_hash"])
            return snapshot if snapshot.verify() else None
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            return None

    def list_environment_snapshots(self, limit: int = 100) -> list[Any]:
        with self._connect() as db:
            rows = db.execute("SELECT * FROM environment_snapshots ORDER BY timestamp DESC LIMIT ?", (limit,)).fetchall()
        result: list[Any] = []
        for row in rows:
            snapshot = self.environment_snapshot_by_id(row["snapshot_id"])
            if snapshot:
                result.append(snapshot)
        return result

    def save_world_observation(self, observation: Any) -> None:
        payload = observation.to_dict()
        with self._connect() as db:
            db.execute("INSERT OR IGNORE INTO world_observations(observation_id, environment_id, observation_type, source, timestamp, value, confidence, reliability, provenance, trust_level, expiry, metadata, observation_hash) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", (observation.observation_id, observation.environment_id, observation.type.value, observation.source.value, observation.timestamp, json.dumps(observation.value), observation.confidence, observation.reliability, json.dumps(observation.provenance), observation.trust_level.value, observation.expiry, json.dumps(observation.metadata), observation.observation_hash))

    def list_world_observations(self, environment_id: str | None = None, limit: int = 1000) -> list[dict[str, Any]]:
        query = "SELECT * FROM world_observations"
        values: tuple[Any, ...] = ()
        if environment_id:
            query += " WHERE environment_id = ?"
            values = (environment_id,)
        query += " ORDER BY timestamp DESC LIMIT ?"
        values += (limit,)
        with self._connect() as db:
            rows = db.execute(query, values).fetchall()
        result: list[dict[str, Any]] = []
        for row in rows:
            try:
                result.append({"observation_id": row["observation_id"], "environment_id": row["environment_id"], "type": row["observation_type"], "source": row["source"], "timestamp": row["timestamp"], "value": json.loads(row["value"]), "confidence": row["confidence"], "reliability": row["reliability"], "provenance": json.loads(row["provenance"]), "trust_level": row["trust_level"], "expiry": row["expiry"], "metadata": json.loads(row["metadata"]), "observation_hash": row["observation_hash"]})
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
        return result

    def save_world_assumption(self, assumption: Any) -> None:
        payload = assumption.to_dict()
        with self._connect() as db:
            db.execute("INSERT OR REPLACE INTO world_assumptions(assumption_id, statement, source, confidence, created_at, expiry, validation_state, environment_id, metadata) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", (assumption.assumption_id, assumption.statement, payload["source"], assumption.confidence, assumption.created_at, assumption.expiry, payload["validation_state"], assumption.environment_id, json.dumps(assumption.metadata)))

    def list_world_assumptions(self, limit: int = 1000) -> list[dict[str, Any]]:
        with self._connect() as db:
            rows = db.execute("SELECT * FROM world_assumptions ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
        return [{**dict(row), "metadata": json.loads(row["metadata"])} for row in rows]

    def save_world_conflict(self, conflict: Any) -> None:
        payload = conflict.to_dict()
        with self._connect() as db:
            db.execute("INSERT OR IGNORE INTO world_conflicts(conflict_id, subject, current_value, historical_value, current_source, historical_source, reason, created_at, resolution, metadata) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", (conflict.conflict_id, conflict.subject, json.dumps(conflict.current_value), json.dumps(conflict.historical_value), payload["current_source"], payload["historical_source"], conflict.reason, conflict.created_at, conflict.resolution, json.dumps(conflict.metadata)))

    def list_world_conflicts(self, limit: int = 1000) -> list[dict[str, Any]]:
        with self._connect() as db:
            rows = db.execute("SELECT * FROM world_conflicts ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
        result: list[dict[str, Any]] = []
        for row in rows:
            try:
                result.append({**dict(row), "current_value": json.loads(row["current_value"]), "historical_value": json.loads(row["historical_value"]), "metadata": json.loads(row["metadata"])})
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
        return result

    def save_environment_diff(self, diff: Any) -> None:
        with self._connect() as db:
            db.execute("INSERT OR IGNORE INTO environment_diffs(diff_id, before_snapshot_id, after_snapshot_id, created_at, payload) VALUES (?, ?, ?, ?, ?)", (diff.diff_id, diff.before_snapshot_id, diff.after_snapshot_id, diff.created_at, json.dumps(diff.to_dict())))

    def list_environment_diffs(self, limit: int = 100) -> list[dict[str, Any]]:
        with self._connect() as db:
            rows = db.execute("SELECT * FROM environment_diffs ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
        return [{**dict(row), "payload": json.loads(row["payload"])} for row in rows]

    def save_world_refresh(self, requirement: Any) -> None:
        payload = requirement.to_dict()
        with self._connect() as db:
            db.execute("INSERT OR REPLACE INTO world_refresh_requirements(refresh_id, kind, subject, reason, requested_at, ttl_seconds, status, metadata) VALUES (?, ?, ?, ?, ?, ?, ?, ?)", (requirement.refresh_id, requirement.kind, requirement.subject, requirement.reason, requirement.requested_at, requirement.ttl_seconds, requirement.status, json.dumps(requirement.metadata)))

    def update_world_refresh(self, requirement: Any) -> None:
        self.save_world_refresh(requirement)

    def list_world_refresh(self, status: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        query = "SELECT * FROM world_refresh_requirements"
        values: tuple[Any, ...] = ()
        if status:
            query += " WHERE status = ?"
            values = (status,)
        query += " ORDER BY requested_at DESC LIMIT ?"
        values += (limit,)
        with self._connect() as db:
            rows = db.execute(query, values).fetchall()
        return [{**dict(row), "metadata": json.loads(row["metadata"])} for row in rows]

    def save_world_provider_state(self, provider: str, payload: dict[str, Any], observed_at: str | None = None) -> None:
        with self._connect() as db:
            db.execute("INSERT INTO world_provider_states(provider_state_id, provider, observed_at, payload) VALUES (?, ?, ?, ?)", (new_id("provider-state"), provider, observed_at or datetime.now(timezone.utc).isoformat(), json.dumps(payload)))

    def list_world_provider_states(self, limit: int = 1000) -> list[dict[str, Any]]:
        with self._connect() as db:
            rows = db.execute("SELECT * FROM world_provider_states ORDER BY observed_at DESC LIMIT ?", (limit,)).fetchall()
        return [{**dict(row), "payload": json.loads(row["payload"])} for row in rows]

    def save_runtime_state(self, record: Any) -> None:
        payload = record.to_dict()
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as db:
            db.execute(
                """INSERT OR REPLACE INTO runtime_states(
                    runtime_id, runtime_version, agent_version, architecture_version, state,
                    started_at, last_heartbeat, last_observation, current_task, current_plan,
                    current_environment, current_world_snapshot, shutdown_reason, failure_reason,
                    restart_count, metadata, payload, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    record.runtime_id, record.runtime_version, record.agent_version,
                    record.architecture_version, record.state.value, record.started_at,
                    record.last_heartbeat, record.last_observation, record.current_task,
                    record.current_plan, record.current_environment,
                    record.current_world_snapshot, record.shutdown_reason,
                    record.failure_reason, record.restart_count,
                    json.dumps(record.metadata), json.dumps(payload), now,
                ),
            )

    def runtime_state_by_id(self, runtime_id: str) -> dict[str, Any] | None:
        with self._connect() as db:
            row = db.execute("SELECT * FROM runtime_states WHERE runtime_id = ?", (runtime_id,)).fetchone()
        return dict(row) if row else None

    def find_runtime_states(self, limit: int = 100) -> list[dict[str, Any]]:
        with self._connect() as db:
            rows = db.execute("SELECT * FROM runtime_states ORDER BY updated_at DESC LIMIT ?", (limit,)).fetchall()
        return [dict(row) for row in rows]

    def save_runtime_task(self, task: Any) -> None:
        payload = task.to_dict()
        with self._connect() as db:
            db.execute(
                """INSERT OR REPLACE INTO runtime_tasks(
                    task_id, goal, priority, source, status, created_at, updated_at,
                    dependencies, deadline, resource_budget, approval_requirement,
                    retry_budget, current_attempt, plan_id, environment_version,
                    agent_version, fingerprint, progress, last_error, metadata, payload
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    task.task_id, task.goal, task.priority.value, task.source.value,
                    task.status.value, task.created_at, task.updated_at,
                    json.dumps(task.dependencies), task.deadline,
                    json.dumps(task.resource_budget), json.dumps(task.approval_requirement),
                    task.retry_budget, task.current_attempt, task.plan_id,
                    task.environment_version, task.agent_version, task.fingerprint,
                    task.progress, task.last_error, json.dumps(task.metadata),
                    json.dumps(payload),
                ),
            )

    def runtime_task_by_id(self, task_id: str) -> dict[str, Any] | None:
        with self._connect() as db:
            row = db.execute("SELECT * FROM runtime_tasks WHERE task_id = ?", (task_id,)).fetchone()
        return dict(row) if row else None

    def runtime_task_by_fingerprint(self, fingerprint: str) -> dict[str, Any] | None:
        with self._connect() as db:
            row = db.execute("SELECT * FROM runtime_tasks WHERE fingerprint = ?", (fingerprint,)).fetchone()
        return dict(row) if row else None

    def find_runtime_tasks(self, status: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        with self._connect() as db:
            if status:
                rows = db.execute("SELECT * FROM runtime_tasks WHERE status = ? ORDER BY updated_at DESC LIMIT ?", (status, limit)).fetchall()
            else:
                rows = db.execute("SELECT * FROM runtime_tasks ORDER BY updated_at DESC LIMIT ?", (limit,)).fetchall()
        return [dict(row) for row in rows]

    def save_runtime_schedule(self, schedule: Any) -> None:
        payload = schedule.to_dict()
        with self._connect() as db:
            db.execute(
                """INSERT OR REPLACE INTO runtime_schedules(
                    schedule_id, goal, kind, priority, source, run_at, interval_seconds,
                    condition, dependencies, deadline_seconds, resource_budget,
                    approval_requirement, enabled, next_run_at, last_enqueued_at,
                    run_count, max_runs, created_at, updated_at, metadata, payload
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    schedule.schedule_id, schedule.goal, schedule.kind.value,
                    schedule.priority.value, schedule.source.value, schedule.run_at,
                    schedule.interval_seconds, json.dumps(schedule.condition),
                    json.dumps(schedule.dependencies), schedule.deadline_seconds,
                    json.dumps(schedule.resource_budget),
                    json.dumps(schedule.approval_requirement), int(schedule.enabled),
                    schedule.next_run_at, schedule.last_enqueued_at, schedule.run_count,
                    schedule.max_runs, schedule.created_at, schedule.updated_at,
                    json.dumps(schedule.metadata), json.dumps(payload),
                ),
            )

    def runtime_schedule_by_id(self, schedule_id: str) -> dict[str, Any] | None:
        with self._connect() as db:
            row = db.execute("SELECT * FROM runtime_schedules WHERE schedule_id = ?", (schedule_id,)).fetchone()
        return dict(row) if row else None

    def find_runtime_schedules(self, limit: int = 100) -> list[dict[str, Any]]:
        with self._connect() as db:
            rows = db.execute("SELECT * FROM runtime_schedules ORDER BY updated_at DESC LIMIT ?", (limit,)).fetchall()
        return [dict(row) for row in rows]

    def save_runtime_approval(self, approval: Any) -> None:
        payload = approval.to_dict()
        with self._connect() as db:
            db.execute(
                """INSERT OR REPLACE INTO runtime_approvals(
                    approval_id, task_id, status, actor, scope_hash, reason,
                    created_at, updated_at, metadata, payload
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    approval.approval_id, approval.task_id, approval.status,
                    approval.actor, approval.scope_hash, approval.reason,
                    approval.created_at, approval.updated_at,
                    json.dumps(approval.metadata), json.dumps(payload),
                ),
            )

    def runtime_approval_by_id(self, approval_id: str) -> dict[str, Any] | None:
        with self._connect() as db:
            row = db.execute("SELECT * FROM runtime_approvals WHERE approval_id = ?", (approval_id,)).fetchone()
        return dict(row) if row else None

    def find_runtime_approvals(self, task_id: str | None = None, status: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        clauses: list[str] = []
        values: list[Any] = []
        if task_id:
            clauses.append("task_id = ?")
            values.append(task_id)
        if status:
            clauses.append("status = ?")
            values.append(status)
        where = "WHERE " + " AND ".join(clauses) if clauses else ""
        with self._connect() as db:
            rows = db.execute(f"SELECT * FROM runtime_approvals {where} ORDER BY updated_at DESC LIMIT ?", (*values, limit)).fetchall()
        return [dict(row) for row in rows]

    def total_event_count(self) -> int:
        with self._connect() as db:
            row = db.execute("SELECT COUNT(*) AS count FROM events").fetchone()
        return int(row["count"]) if row else 0
