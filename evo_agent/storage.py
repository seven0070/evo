from __future__ import annotations

from datetime import datetime, timezone
import json
import sqlite3
from pathlib import Path
from typing import Any

from .models import Event, Goal, TaskStatus, new_id, utc_now


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
                CREATE TABLE IF NOT EXISTS integrations (
                    integration_id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    provider TEXT NOT NULL,
                    integration_type TEXT NOT NULL,
                    version TEXT NOT NULL,
                    enabled INTEGER NOT NULL,
                    lifecycle_state TEXT NOT NULL,
                    architecture_version TEXT NOT NULL,
                    health_state TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    payload TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_integrations_enabled ON integrations(enabled, lifecycle_state);
                CREATE TABLE IF NOT EXISTS integration_capabilities (
                    capability_id TEXT PRIMARY KEY,
                    integration_id TEXT NOT NULL,
                    name TEXT NOT NULL,
                    risk TEXT NOT NULL,
                    supported_operations TEXT NOT NULL,
                    payload TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_integration_capabilities_integration ON integration_capabilities(integration_id);
                CREATE TABLE IF NOT EXISTS integration_operations (
                    operation_id TEXT PRIMARY KEY,
                    integration_id TEXT NOT NULL,
                    operation TEXT NOT NULL,
                    target TEXT NOT NULL,
                    request_fingerprint TEXT NOT NULL,
                    status TEXT NOT NULL,
                    requested_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    request_payload TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_integration_operations_fingerprint ON integration_operations(request_fingerprint);
                CREATE INDEX IF NOT EXISTS idx_integration_operations_status ON integration_operations(status);
                CREATE TABLE IF NOT EXISTS external_access_policies (
                    policy_id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    version TEXT NOT NULL,
                    enabled INTEGER NOT NULL,
                    payload TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_external_policies_enabled ON external_access_policies(enabled);
                CREATE TABLE IF NOT EXISTS external_observations (
                    observation_id TEXT PRIMARY KEY,
                    integration_id TEXT NOT NULL,
                    resource_identity TEXT NOT NULL,
                    timestamp TEXT NOT NULL,
                    freshness TEXT NOT NULL,
                    trust_level TEXT NOT NULL,
                    content_hash TEXT NOT NULL,
                    payload TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_external_observations_resource ON external_observations(integration_id, resource_identity, timestamp);
                CREATE TABLE IF NOT EXISTS external_resources (
                    resource_id TEXT PRIMARY KEY,
                    integration_id TEXT NOT NULL,
                    resource_identity TEXT NOT NULL,
                    version TEXT NOT NULL,
                    etag TEXT NOT NULL,
                    content_hash TEXT NOT NULL,
                    exists_flag INTEGER NOT NULL,
                    observed_at TEXT NOT NULL,
                    payload TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_external_resources_identity ON external_resources(integration_id, resource_identity, observed_at);
                CREATE TABLE IF NOT EXISTS external_changes (
                    change_id TEXT PRIMARY KEY,
                    integration_id TEXT NOT NULL,
                    resource_identity TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    payload TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_external_changes_resource ON external_changes(integration_id, resource_identity, created_at);
                CREATE TABLE IF NOT EXISTS external_operation_results (
                    result_id TEXT PRIMARY KEY,
                    operation_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    failure_class TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    payload TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_external_results_operation ON external_operation_results(operation_id, created_at);
                CREATE TABLE IF NOT EXISTS connector_health (
                    health_id TEXT PRIMARY KEY,
                    integration_id TEXT NOT NULL,
                    observed_at TEXT NOT NULL,
                    state TEXT NOT NULL,
                    payload TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_connector_health_integration ON connector_health(integration_id, observed_at);
                CREATE TABLE IF NOT EXISTS communication_records (
                    communication_id TEXT PRIMARY KEY,
                    operation_id TEXT NOT NULL,
                    integration_id TEXT NOT NULL,
                    channel TEXT NOT NULL,
                    target TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    payload TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_communication_records_operation ON communication_records(operation_id, created_at);
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
                CREATE TABLE IF NOT EXISTS specialists (
                    specialist_id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    specialist_type TEXT NOT NULL,
                    version TEXT NOT NULL,
                    lifecycle_state TEXT NOT NULL,
                    enabled INTEGER NOT NULL,
                    risk_classification TEXT NOT NULL,
                    architecture_version TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_specialists_type_state ON specialists(specialist_type, lifecycle_state);
                CREATE TABLE IF NOT EXISTS specialist_capabilities (
                    capability_id TEXT PRIMARY KEY,
                    specialist_id TEXT NOT NULL,
                    name TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS specialist_tasks (
                    specialist_task_id TEXT PRIMARY KEY,
                    parent_task_id TEXT NOT NULL,
                    specialist_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    goal TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    payload TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_specialist_tasks_parent_status ON specialist_tasks(parent_task_id, status);
                CREATE TABLE IF NOT EXISTS specialist_contracts (
                    contract_id TEXT PRIMARY KEY,
                    specialist_task_id TEXT NOT NULL UNIQUE,
                    specialist_id TEXT NOT NULL,
                    parent_task_id TEXT NOT NULL,
                    scope_hash TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS specialist_messages (
                    message_id TEXT PRIMARY KEY,
                    parent_task_id TEXT NOT NULL,
                    sender TEXT NOT NULL,
                    recipient TEXT NOT NULL,
                    message_type TEXT NOT NULL,
                    correlation_id TEXT NOT NULL,
                    trust_level TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_specialist_messages_parent ON specialist_messages(parent_task_id, created_at);
                CREATE TABLE IF NOT EXISTS specialist_results (
                    result_id TEXT PRIMARY KEY,
                    specialist_task_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    verified INTEGER NOT NULL,
                    payload TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS specialist_evidence (
                    evidence_id TEXT PRIMARY KEY,
                    result_id TEXT NOT NULL,
                    specialist_task_id TEXT NOT NULL,
                    evidence_kind TEXT NOT NULL,
                    trust_level TEXT NOT NULL,
                    verification_status TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS delegation_runs (
                    delegation_id TEXT PRIMARY KEY,
                    parent_task_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    active_specialists INTEGER NOT NULL,
                    payload TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_delegation_runs_parent ON delegation_runs(parent_task_id, created_at);
                CREATE TABLE IF NOT EXISTS evidence_fusions (
                    fusion_id TEXT PRIMARY KEY,
                    parent_task_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS evidence_conflicts (
                    conflict_id TEXT PRIMARY KEY,
                    parent_task_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS specialist_health (
                    health_id TEXT PRIMARY KEY,
                    specialist_id TEXT NOT NULL,
                    observed_at TEXT NOT NULL,
                    state TEXT NOT NULL,
                    payload TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_specialist_health_specialist ON specialist_health(specialist_id, observed_at);
                CREATE TABLE IF NOT EXISTS model_providers (
                    provider_id TEXT PRIMARY KEY, name TEXT NOT NULL, provider_type TEXT NOT NULL,
                    lifecycle_state TEXT NOT NULL, enabled INTEGER NOT NULL, payload TEXT NOT NULL,
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS models (
                    model_id TEXT PRIMARY KEY, provider_id TEXT NOT NULL, name TEXT NOT NULL,
                    version TEXT NOT NULL, lifecycle_state TEXT NOT NULL, enabled INTEGER NOT NULL,
                    health_state TEXT NOT NULL, architecture_version TEXT NOT NULL, payload TEXT NOT NULL,
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_models_provider_state ON models(provider_id, lifecycle_state);
                CREATE TABLE IF NOT EXISTS model_capabilities (
                    capability_id TEXT PRIMARY KEY, model_id TEXT NOT NULL, name TEXT NOT NULL,
                    payload TEXT NOT NULL, created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS model_health (
                    health_id TEXT PRIMARY KEY, model_id TEXT NOT NULL, observed_at TEXT NOT NULL,
                    state TEXT NOT NULL, payload TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_model_health_model ON model_health(model_id, observed_at);
                CREATE TABLE IF NOT EXISTS model_evaluations (
                    evaluation_id TEXT PRIMARY KEY, model_id TEXT NOT NULL, benchmark_id TEXT NOT NULL,
                    decision TEXT NOT NULL, payload TEXT NOT NULL, created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS model_trials (
                    trial_id TEXT PRIMARY KEY, evaluation_id TEXT NOT NULL, model_id TEXT NOT NULL,
                    benchmark_id TEXT NOT NULL, trial_number INTEGER NOT NULL, success INTEGER NOT NULL,
                    verified INTEGER NOT NULL, payload TEXT NOT NULL, created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS model_selection_records (
                    selection_id TEXT PRIMARY KEY, task_id TEXT NOT NULL, model_id TEXT NOT NULL,
                    payload TEXT NOT NULL, created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_model_selection_task ON model_selection_records(task_id, created_at);
                CREATE TABLE IF NOT EXISTS learning_observations (
                    observation_id TEXT PRIMARY KEY, task_id TEXT NOT NULL, payload TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS learning_outcomes (
                    outcome_id TEXT PRIMARY KEY, observation_id TEXT NOT NULL, payload TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS learning_adjustments (
                    adjustment_id TEXT PRIMARY KEY, status TEXT NOT NULL, affected_component TEXT NOT NULL,
                    payload TEXT NOT NULL, created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_learning_adjustments_component ON learning_adjustments(affected_component, created_at);
                CREATE TABLE IF NOT EXISTS learning_policies (
                    policy_id TEXT PRIMARY KEY, version TEXT NOT NULL, enabled INTEGER NOT NULL,
                    payload TEXT NOT NULL, created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS learning_patterns (
                    pattern_id TEXT PRIMARY KEY, pattern_type TEXT NOT NULL, frequency INTEGER NOT NULL,
                    confidence REAL NOT NULL, lifecycle_state TEXT NOT NULL, architecture_version TEXT NOT NULL,
                    payload TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_learning_patterns_type ON learning_patterns(pattern_type, created_at);
                CREATE TABLE IF NOT EXISTS learning_hypotheses (
                    hypothesis_id TEXT PRIMARY KEY, pattern_id TEXT NOT NULL, status TEXT NOT NULL,
                    risk TEXT NOT NULL, confidence REAL NOT NULL, payload TEXT NOT NULL, created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_learning_hypotheses_status ON learning_hypotheses(status, created_at);
                CREATE TABLE IF NOT EXISTS adaptive_policies (
                    policy_id TEXT PRIMARY KEY, version TEXT NOT NULL, enabled INTEGER NOT NULL,
                    lifecycle_state TEXT NOT NULL, architecture_version TEXT NOT NULL, payload TEXT NOT NULL,
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS adaptive_adjustments (
                    adjustment_id TEXT PRIMARY KEY, policy_id TEXT, status TEXT NOT NULL,
                    affected_component TEXT NOT NULL, risk TEXT NOT NULL, payload TEXT NOT NULL,
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_adaptive_adjustments_status ON adaptive_adjustments(status, created_at);
                CREATE TABLE IF NOT EXISTS adjustment_evaluations (
                    evaluation_id TEXT PRIMARY KEY, adjustment_id TEXT NOT NULL, decision TEXT NOT NULL,
                    payload TEXT NOT NULL, created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS learning_feedback (
                    feedback_id TEXT PRIMARY KEY, task_id TEXT NOT NULL, feedback_type TEXT NOT NULL,
                    payload TEXT NOT NULL, created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS counterfactual_evaluations (
                    counterfactual_id TEXT PRIMARY KEY, task_id TEXT NOT NULL, alternative_type TEXT NOT NULL,
                    decision TEXT NOT NULL, payload TEXT NOT NULL, created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS learning_conflicts (
                    conflict_id TEXT PRIMARY KEY, target_type TEXT NOT NULL, target_id TEXT NOT NULL,
                    status TEXT NOT NULL, payload TEXT NOT NULL, created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS learning_rollbacks (
                    rollback_id TEXT PRIMARY KEY, adjustment_id TEXT NOT NULL, status TEXT NOT NULL,
                    payload TEXT NOT NULL, created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS learning_cycles (
                    cycle_id TEXT PRIMARY KEY, status TEXT NOT NULL, started_at TEXT NOT NULL,
                    completed_at TEXT, payload TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_learning_cycles_status ON learning_cycles(status, started_at);
                CREATE TABLE IF NOT EXISTS self_model_claims (
                    claim_id TEXT PRIMARY KEY, category TEXT NOT NULL, subject TEXT NOT NULL,
                    confidence REAL NOT NULL, lifecycle_state TEXT NOT NULL, architecture_version TEXT NOT NULL,
                    environment_id TEXT NOT NULL, evidence_ids TEXT NOT NULL, provenance TEXT NOT NULL,
                    payload TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_self_model_claims_subject ON self_model_claims(subject, lifecycle_state);
                CREATE TABLE IF NOT EXISTS self_model_snapshots (
                    snapshot_id TEXT PRIMARY KEY, active_version TEXT NOT NULL, architecture_version TEXT NOT NULL,
                    environment_id TEXT NOT NULL, status TEXT NOT NULL, payload TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_self_model_snapshots_created ON self_model_snapshots(created_at);
                CREATE TABLE IF NOT EXISTS self_model_limitations (
                    limitation_id TEXT PRIMARY KEY, limitation_type TEXT NOT NULL, severity TEXT NOT NULL,
                    frequency INTEGER NOT NULL, confidence REAL NOT NULL, lifecycle_state TEXT NOT NULL,
                    architecture_version TEXT NOT NULL, environment_id TEXT NOT NULL, evidence_ids TEXT NOT NULL,
                    payload TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_self_model_limitations_type ON self_model_limitations(limitation_type, lifecycle_state);
                CREATE TABLE IF NOT EXISTS self_model_assumptions (
                    assumption_id TEXT PRIMARY KEY, statement TEXT NOT NULL, confidence REAL NOT NULL,
                    validation_status TEXT NOT NULL, dependent_task TEXT NOT NULL, invalidation_condition TEXT NOT NULL,
                    architecture_version TEXT NOT NULL, environment_id TEXT NOT NULL, payload TEXT NOT NULL,
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_self_model_assumptions_status ON self_model_assumptions(validation_status, updated_at);
                CREATE TABLE IF NOT EXISTS self_model_uncertainty (
                    uncertainty_id TEXT PRIMARY KEY, uncertainty_type TEXT NOT NULL, severity TEXT NOT NULL,
                    confidence REAL NOT NULL, lifecycle_state TEXT NOT NULL, architecture_version TEXT NOT NULL,
                    environment_id TEXT NOT NULL, evidence_ids TEXT NOT NULL, payload TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS self_model_conflicts (
                    conflict_id TEXT PRIMARY KEY, subject TEXT NOT NULL, status TEXT NOT NULL,
                    architecture_version TEXT NOT NULL, environment_id TEXT NOT NULL, payload TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS decision_readiness (
                    readiness_id TEXT PRIMARY KEY, goal_id TEXT NOT NULL, state TEXT NOT NULL,
                    confidence REAL NOT NULL, architecture_version TEXT NOT NULL, environment_id TEXT NOT NULL,
                    payload TEXT NOT NULL, created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_decision_readiness_goal ON decision_readiness(goal_id, created_at);
                CREATE TABLE IF NOT EXISTS meta_reasoning_records (
                    record_id TEXT PRIMARY KEY, goal_id TEXT NOT NULL, recommendation TEXT NOT NULL,
                    confidence REAL NOT NULL, architecture_version TEXT NOT NULL, environment_id TEXT NOT NULL,
                    payload TEXT NOT NULL, created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS confidence_calibration (
                    calibration_id TEXT PRIMARY KEY, subject TEXT NOT NULL, predicted_confidence REAL NOT NULL,
                    actual_verified INTEGER NOT NULL, calibration_state TEXT NOT NULL, error REAL NOT NULL,
                    architecture_version TEXT NOT NULL, environment_id TEXT NOT NULL, payload TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS self_reflections (
                    reflection_id TEXT PRIMARY KEY, task_id TEXT NOT NULL, outcome TEXT NOT NULL,
                    verified INTEGER NOT NULL, architecture_version TEXT NOT NULL, environment_id TEXT NOT NULL,
                    payload TEXT NOT NULL, created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS self_diagnostics (
                    diagnostic_id TEXT PRIMARY KEY, status TEXT NOT NULL, architecture_version TEXT NOT NULL,
                    environment_id TEXT NOT NULL, payload TEXT NOT NULL, created_at TEXT NOT NULL
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

    def save_integration(self, integration: Any) -> None:
        payload = integration.to_dict()
        with self._connect() as db:
            db.execute("INSERT OR REPLACE INTO integrations(integration_id, name, provider, integration_type, version, enabled, lifecycle_state, architecture_version, health_state, created_at, updated_at, payload) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", (integration.integration_id, integration.name, integration.provider, integration.integration_type.value, integration.version, int(integration.enabled), integration.lifecycle_state.value, integration.architecture_version, integration.health.state.value, integration.created_at, integration.updated_at, json.dumps(payload)))
            db.execute("DELETE FROM integration_capabilities WHERE integration_id = ?", (integration.integration_id,))
            for capability in integration.capabilities:
                db.execute("INSERT OR REPLACE INTO integration_capabilities(capability_id, integration_id, name, risk, supported_operations, payload) VALUES (?, ?, ?, ?, ?, ?)", (capability.capability_id, integration.integration_id, capability.name, capability.risk.value, json.dumps(capability.supported_operations), json.dumps(capability.to_dict())))

    def integration_by_id(self, integration_id: str) -> dict[str, Any] | None:
        with self._connect() as db:
            row = db.execute("SELECT * FROM integrations WHERE integration_id = ?", (integration_id,)).fetchone()
        return dict(row) if row else None

    def find_integrations(self, limit: int = 100) -> list[dict[str, Any]]:
        with self._connect() as db:
            rows = db.execute("SELECT * FROM integrations ORDER BY name, integration_id LIMIT ?", (limit,)).fetchall()
        return [dict(row) for row in rows]

    def find_integration_capabilities(self, integration_id: str | None = None, limit: int = 500) -> list[dict[str, Any]]:
        with self._connect() as db:
            if integration_id:
                rows = db.execute("SELECT * FROM integration_capabilities WHERE integration_id = ? ORDER BY name LIMIT ?", (integration_id, limit)).fetchall()
            else:
                rows = db.execute("SELECT * FROM integration_capabilities ORDER BY name LIMIT ?", (limit,)).fetchall()
        return [dict(row) for row in rows]

    def save_external_access_policy(self, policy: Any) -> None:
        payload = policy.to_dict()
        with self._connect() as db:
            db.execute("INSERT OR REPLACE INTO external_access_policies(policy_id, name, version, enabled, payload, created_at) VALUES (?, ?, ?, ?, ?, ?)", (policy.policy_id, policy.name, policy.version, int(policy.enabled), json.dumps(payload), policy.provenance.created_at))

    def external_access_policy_by_id(self, policy_id: str) -> dict[str, Any] | None:
        with self._connect() as db:
            row = db.execute("SELECT * FROM external_access_policies WHERE policy_id = ?", (policy_id,)).fetchone()
        return dict(row) if row else None

    def find_external_access_policies(self, limit: int = 100) -> list[dict[str, Any]]:
        with self._connect() as db:
            rows = db.execute("SELECT * FROM external_access_policies ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
        return [dict(row) for row in rows]

    def save_integration_operation(self, operation: Any, request_payload: dict[str, Any] | None = None) -> None:
        payload = {"operation": operation.to_dict(), "request_payload": request_payload or {}}
        with self._connect() as db:
            db.execute("INSERT OR REPLACE INTO integration_operations(operation_id, integration_id, operation, target, request_fingerprint, status, requested_at, updated_at, payload, request_payload) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", (operation.operation_id, operation.integration_id, operation.operation, operation.target, operation.request_fingerprint, operation.status.value, operation.created_at, operation.updated_at, json.dumps(payload), json.dumps(request_payload or {})))

    def integration_operation_by_id(self, operation_id: str) -> dict[str, Any] | None:
        with self._connect() as db:
            row = db.execute("SELECT * FROM integration_operations WHERE operation_id = ?", (operation_id,)).fetchone()
        return dict(row) if row else None

    def find_integration_operations(self, integration_id: str | None = None, status: str | None = None, limit: int = 200) -> list[dict[str, Any]]:
        clauses: list[str] = []
        values: list[Any] = []
        if integration_id:
            clauses.append("integration_id = ?"); values.append(integration_id)
        if status:
            clauses.append("status = ?"); values.append(status)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with self._connect() as db:
            rows = db.execute(f"SELECT * FROM integration_operations {where} ORDER BY requested_at DESC LIMIT ?", (*values, limit)).fetchall()
        return [dict(row) for row in rows]

    def save_external_operation_result(self, result: Any) -> None:
        payload = result.to_dict()
        with self._connect() as db:
            db.execute("INSERT INTO external_operation_results(result_id, operation_id, status, failure_class, created_at, payload) VALUES (?, ?, ?, ?, ?, ?)", (new_id("external_result"), result.operation_id, result.status.value, result.failure_class.value, result.created_at, json.dumps(payload)))

    def external_operation_by_fingerprint(self, fingerprint: str) -> dict[str, Any] | None:
        with self._connect() as db:
            row = db.execute("SELECT r.* FROM external_operation_results r JOIN integration_operations o ON o.operation_id = r.operation_id WHERE o.request_fingerprint = ? ORDER BY r.created_at DESC LIMIT 1", (fingerprint,)).fetchone()
        return dict(row) if row else None

    def find_external_operation_results(self, operation_id: str | None = None, limit: int = 200) -> list[dict[str, Any]]:
        with self._connect() as db:
            if operation_id:
                rows = db.execute("SELECT * FROM external_operation_results WHERE operation_id = ? ORDER BY created_at DESC LIMIT ?", (operation_id, limit)).fetchall()
            else:
                rows = db.execute("SELECT * FROM external_operation_results ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
        return [dict(row) for row in rows]

    def save_external_observation(self, observation: Any) -> None:
        payload = observation.to_dict()
        with self._connect() as db:
            db.execute("INSERT OR REPLACE INTO external_observations(observation_id, integration_id, resource_identity, timestamp, freshness, trust_level, content_hash, payload) VALUES (?, ?, ?, ?, ?, ?, ?, ?)", (observation.observation_id, observation.integration_id, observation.resource_identity, observation.timestamp, observation.freshness.value, observation.trust_level.value, observation.content_hash, json.dumps(payload)))

    def external_observation_by_id(self, observation_id: str) -> dict[str, Any] | None:
        with self._connect() as db:
            row = db.execute("SELECT * FROM external_observations WHERE observation_id = ?", (observation_id,)).fetchone()
        return dict(row) if row else None

    def find_external_observations(self, integration_id: str | None = None, resource_identity: str | None = None, limit: int = 200) -> list[dict[str, Any]]:
        clauses: list[str] = []
        values: list[Any] = []
        if integration_id:
            clauses.append("integration_id = ?"); values.append(integration_id)
        if resource_identity:
            clauses.append("resource_identity = ?"); values.append(resource_identity)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with self._connect() as db:
            rows = db.execute(f"SELECT * FROM external_observations {where} ORDER BY timestamp DESC LIMIT ?", (*values, limit)).fetchall()
        return [dict(row) for row in rows]

    def save_external_resource(self, resource: Any) -> None:
        payload = resource.to_dict()
        with self._connect() as db:
            db.execute("INSERT OR REPLACE INTO external_resources(resource_id, integration_id, resource_identity, version, etag, content_hash, exists_flag, observed_at, payload) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", (resource.resource_id, resource.integration_id, resource.resource_identity, resource.version, resource.etag, resource.content_hash, int(resource.exists), resource.observed_at, json.dumps(payload)))

    def find_external_resources(self, integration_id: str | None = None, resource_identity: str | None = None, limit: int = 200) -> list[dict[str, Any]]:
        clauses: list[str] = []
        values: list[Any] = []
        if integration_id:
            clauses.append("integration_id = ?"); values.append(integration_id)
        if resource_identity:
            clauses.append("resource_identity = ?"); values.append(resource_identity)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with self._connect() as db:
            rows = db.execute(f"SELECT * FROM external_resources {where} ORDER BY observed_at DESC LIMIT ?", (*values, limit)).fetchall()
        return [dict(row) for row in rows]

    def save_external_change(self, change: Any) -> None:
        with self._connect() as db:
            db.execute("INSERT OR REPLACE INTO external_changes(change_id, integration_id, resource_identity, kind, created_at, payload) VALUES (?, ?, ?, ?, ?, ?)", (change.change_id, change.integration_id, change.resource_identity, change.kind.value, change.created_at, json.dumps(change.to_dict())))

    def find_external_changes(self, integration_id: str | None = None, limit: int = 200) -> list[dict[str, Any]]:
        with self._connect() as db:
            if integration_id:
                rows = db.execute("SELECT * FROM external_changes WHERE integration_id = ? ORDER BY created_at DESC LIMIT ?", (integration_id, limit)).fetchall()
            else:
                rows = db.execute("SELECT * FROM external_changes ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
        return [dict(row) for row in rows]

    def save_connector_health(self, integration_id: str, health: Any) -> None:
        payload = health.to_dict()
        with self._connect() as db:
            db.execute("INSERT INTO connector_health(health_id, integration_id, observed_at, state, payload) VALUES (?, ?, ?, ?, ?)", (new_id("connector_health"), integration_id, utc_now(), health.state.value, json.dumps(payload)))

    def find_connector_health(self, integration_id: str | None = None, limit: int = 200) -> list[dict[str, Any]]:
        with self._connect() as db:
            if integration_id:
                rows = db.execute("SELECT * FROM connector_health WHERE integration_id = ? ORDER BY observed_at DESC LIMIT ?", (integration_id, limit)).fetchall()
            else:
                rows = db.execute("SELECT * FROM connector_health ORDER BY observed_at DESC LIMIT ?", (limit,)).fetchall()
        return [dict(row) for row in rows]

    def save_communication_record(self, record: Any) -> None:
        with self._connect() as db:
            db.execute("INSERT OR REPLACE INTO communication_records(communication_id, operation_id, integration_id, channel, target, status, created_at, payload) VALUES (?, ?, ?, ?, ?, ?, ?, ?)", (record.communication_id, record.operation_id, record.integration_id, record.channel, record.target, record.status.value, record.created_at, json.dumps(record.to_dict())))

    def find_communication_records(self, integration_id: str | None = None, limit: int = 200) -> list[dict[str, Any]]:
        with self._connect() as db:
            if integration_id:
                rows = db.execute("SELECT * FROM communication_records WHERE integration_id = ? ORDER BY created_at DESC LIMIT ?", (integration_id, limit)).fetchall()
            else:
                rows = db.execute("SELECT * FROM communication_records ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
        return [dict(row) for row in rows]

    def save_specialist(self, specialist: Any) -> None:
        payload = specialist.to_dict()
        with self._connect() as db:
            db.execute("INSERT OR REPLACE INTO specialists(specialist_id, name, specialist_type, version, lifecycle_state, enabled, risk_classification, architecture_version, payload, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", (specialist.specialist_id, specialist.name, specialist.specialist_type.value, specialist.version_lineage.version, specialist.lifecycle_state.value, int(specialist.enabled), specialist.risk_classification.value, specialist.architecture_version, json.dumps(payload), specialist.created_at, specialist.updated_at))
            db.execute("DELETE FROM specialist_capabilities WHERE specialist_id = ?", (specialist.specialist_id,))
            for capability in specialist.capabilities:
                db.execute("INSERT OR REPLACE INTO specialist_capabilities(capability_id, specialist_id, name, payload, created_at) VALUES (?, ?, ?, ?, ?)", (capability.capability_id, specialist.specialist_id, capability.name, json.dumps(capability.to_dict()), capability.created_at))

    def specialist_by_id(self, specialist_id: str) -> dict[str, Any] | None:
        with self._connect() as db:
            row = db.execute("SELECT * FROM specialists WHERE specialist_id = ?", (specialist_id,)).fetchone()
        if not row:
            return None
        result = dict(row)
        result["payload"] = json.loads(result["payload"])
        return result

    def find_specialists(self, specialist_type: str | None = None, enabled: bool | None = None, limit: int = 100) -> list[dict[str, Any]]:
        clauses: list[str] = []
        values: list[Any] = []
        if specialist_type:
            clauses.append("specialist_type = ?")
            values.append(specialist_type)
        if enabled is not None:
            clauses.append("enabled = ?")
            values.append(int(enabled))
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        with self._connect() as db:
            rows = db.execute(f"SELECT * FROM specialists{where} ORDER BY name, specialist_id LIMIT ?", (*values, limit)).fetchall()
        return [{**dict(row), "payload": json.loads(row["payload"])} for row in rows]

    def save_specialist_task(self, task: Any) -> None:
        payload = task.to_dict()
        with self._connect() as db:
            db.execute("INSERT OR REPLACE INTO specialist_tasks(specialist_task_id, parent_task_id, specialist_id, status, goal, created_at, updated_at, payload) VALUES (?, ?, ?, ?, ?, ?, ?, ?)", (task.specialist_task_id, task.parent_task_id, task.specialist_id, task.status.value, task.goal, task.created_at, task.updated_at, json.dumps(payload)))

    def specialist_task_by_id(self, specialist_task_id: str) -> dict[str, Any] | None:
        with self._connect() as db:
            row = db.execute("SELECT * FROM specialist_tasks WHERE specialist_task_id = ?", (specialist_task_id,)).fetchone()
        if not row:
            return None
        result = dict(row)
        result["payload"] = json.loads(result["payload"])
        return result

    def find_specialist_tasks(self, parent_task_id: str | None = None, status: str | None = None, limit: int = 200) -> list[dict[str, Any]]:
        clauses: list[str] = []
        values: list[Any] = []
        if parent_task_id:
            clauses.append("parent_task_id = ?")
            values.append(parent_task_id)
        if status:
            clauses.append("status = ?")
            values.append(status)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        with self._connect() as db:
            rows = db.execute(f"SELECT * FROM specialist_tasks{where} ORDER BY created_at LIMIT ?", (*values, limit)).fetchall()
        return [{**dict(row), "payload": json.loads(row["payload"])} for row in rows]

    def save_specialist_contract(self, contract: Any) -> None:
        payload = contract.to_dict()
        with self._connect() as db:
            db.execute("INSERT OR REPLACE INTO specialist_contracts(contract_id, specialist_task_id, specialist_id, parent_task_id, scope_hash, payload, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)", (contract.contract_id, contract.specialist_task_id, contract.specialist_id, contract.parent_task_id, contract.scope_hash, json.dumps(payload), contract.created_at))

    def specialist_contract_by_task(self, specialist_task_id: str) -> dict[str, Any] | None:
        with self._connect() as db:
            row = db.execute("SELECT * FROM specialist_contracts WHERE specialist_task_id = ?", (specialist_task_id,)).fetchone()
        if not row:
            return None
        result = dict(row)
        result["payload"] = json.loads(result["payload"])
        return result

    def save_specialist_message(self, message: Any) -> None:
        with self._connect() as db:
            db.execute("INSERT OR REPLACE INTO specialist_messages(message_id, parent_task_id, sender, recipient, message_type, correlation_id, trust_level, payload, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", (message.message_id, message.parent_task_id, message.sender, message.recipient, message.message_type.value, message.correlation_id, message.trust_level.value, json.dumps(message.to_dict()), message.created_at))

    def find_specialist_messages(self, parent_task_id: str, limit: int = 200) -> list[dict[str, Any]]:
        with self._connect() as db:
            rows = db.execute("SELECT * FROM specialist_messages WHERE parent_task_id = ? ORDER BY created_at LIMIT ?", (parent_task_id, limit)).fetchall()
        return [{**dict(row), "payload": json.loads(row["payload"])} for row in rows]

    def save_specialist_result(self, result: Any) -> None:
        with self._connect() as db:
            db.execute("INSERT OR REPLACE INTO specialist_results(result_id, specialist_task_id, status, verified, payload, created_at) VALUES (?, ?, ?, ?, ?, ?)", (result.result_id, result.specialist_task_id, result.status.value, int(result.verified), json.dumps(result.to_dict()), result.created_at))

    def specialist_result_by_task(self, specialist_task_id: str) -> dict[str, Any] | None:
        with self._connect() as db:
            row = db.execute("SELECT * FROM specialist_results WHERE specialist_task_id = ? ORDER BY created_at DESC LIMIT 1", (specialist_task_id,)).fetchone()
        if not row:
            return None
        result = dict(row)
        result["payload"] = json.loads(result["payload"])
        return result

    def save_specialist_evidence(self, evidence: Any) -> None:
        with self._connect() as db:
            db.execute("INSERT OR REPLACE INTO specialist_evidence(evidence_id, result_id, specialist_task_id, evidence_kind, trust_level, verification_status, payload, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)", (evidence.evidence_id, evidence.result_id, evidence.specialist_task_id, evidence.evidence_kind.value, evidence.trust_level.value, evidence.verification_status.value, json.dumps(evidence.to_dict()), evidence.created_at))

    def find_specialist_evidence(self, parent_task_id: str | None = None, specialist_task_id: str | None = None, limit: int = 200) -> list[dict[str, Any]]:
        query = "SELECT e.* FROM specialist_evidence e"
        values: list[Any] = []
        if parent_task_id:
            query += " JOIN specialist_tasks t ON t.specialist_task_id = e.specialist_task_id WHERE t.parent_task_id = ?"
            values.append(parent_task_id)
        elif specialist_task_id:
            query += " WHERE e.specialist_task_id = ?"
            values.append(specialist_task_id)
        query += " ORDER BY e.created_at LIMIT ?"
        values.append(limit)
        with self._connect() as db:
            rows = db.execute(query, tuple(values)).fetchall()
        return [{**dict(row), "payload": json.loads(row["payload"])} for row in rows]

    def save_delegation_run(self, run: Any) -> None:
        with self._connect() as db:
            db.execute("INSERT OR REPLACE INTO delegation_runs(delegation_id, parent_task_id, status, active_specialists, payload, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)", (run.delegation_id, run.parent_task_id, run.status.value, run.active_specialists, json.dumps(run.to_dict()), run.created_at, run.updated_at))

    def delegation_by_id(self, delegation_id: str) -> dict[str, Any] | None:
        with self._connect() as db:
            row = db.execute("SELECT * FROM delegation_runs WHERE delegation_id = ?", (delegation_id,)).fetchone()
        if not row:
            return None
        result = dict(row)
        result["payload"] = json.loads(result["payload"])
        return result

    def find_delegation_runs(self, parent_task_id: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        with self._connect() as db:
            if parent_task_id:
                rows = db.execute("SELECT * FROM delegation_runs WHERE parent_task_id = ? ORDER BY created_at DESC LIMIT ?", (parent_task_id, limit)).fetchall()
            else:
                rows = db.execute("SELECT * FROM delegation_runs ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
        return [{**dict(row), "payload": json.loads(row["payload"])} for row in rows]

    def save_evidence_fusion(self, fusion: Any) -> None:
        with self._connect() as db:
            db.execute("INSERT OR REPLACE INTO evidence_fusions(fusion_id, parent_task_id, status, payload, created_at) VALUES (?, ?, ?, ?, ?)", (fusion.fusion_id, fusion.parent_task_id, getattr(fusion.status, "value", fusion.status), json.dumps(fusion.to_dict()), fusion.created_at))

    def find_evidence_fusions(self, parent_task_id: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        with self._connect() as db:
            if parent_task_id:
                rows = db.execute("SELECT * FROM evidence_fusions WHERE parent_task_id = ? ORDER BY created_at DESC LIMIT ?", (parent_task_id, limit)).fetchall()
            else:
                rows = db.execute("SELECT * FROM evidence_fusions ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
        return [{**dict(row), "payload": json.loads(row["payload"])} for row in rows]

    def save_evidence_conflict(self, conflict: Any) -> None:
        with self._connect() as db:
            db.execute("INSERT OR REPLACE INTO evidence_conflicts(conflict_id, parent_task_id, status, payload, created_at) VALUES (?, ?, ?, ?, ?)", (conflict.conflict_id, conflict.parent_task_id, getattr(conflict.status, "value", conflict.status), json.dumps(conflict.to_dict()), conflict.created_at))

    def find_evidence_conflicts(self, parent_task_id: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        with self._connect() as db:
            if parent_task_id:
                rows = db.execute("SELECT * FROM evidence_conflicts WHERE parent_task_id = ? ORDER BY created_at DESC LIMIT ?", (parent_task_id, limit)).fetchall()
            else:
                rows = db.execute("SELECT * FROM evidence_conflicts ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
        return [{**dict(row), "payload": json.loads(row["payload"])} for row in rows]

    def save_specialist_health(self, specialist_id: str, health: Any) -> None:
        with self._connect() as db:
            db.execute("INSERT INTO specialist_health(health_id, specialist_id, observed_at, state, payload) VALUES (?, ?, ?, ?, ?)", (new_id("specialist_health"), specialist_id, utc_now(), health.state.value, json.dumps(health.to_dict())))

    def find_specialist_health(self, specialist_id: str | None = None, limit: int = 200) -> list[dict[str, Any]]:
        with self._connect() as db:
            if specialist_id:
                rows = db.execute("SELECT * FROM specialist_health WHERE specialist_id = ? ORDER BY observed_at DESC LIMIT ?", (specialist_id, limit)).fetchall()
            else:
                rows = db.execute("SELECT * FROM specialist_health ORDER BY observed_at DESC LIMIT ?", (limit,)).fetchall()
        return [{**dict(row), "payload": json.loads(row["payload"])} for row in rows]

    # Phase 17 Model & Learning Intelligence persistence.
    def save_model_provider(self, provider: Any) -> None:
        payload = provider.to_dict()
        with self._connect() as db:
            db.execute("INSERT OR REPLACE INTO model_providers(provider_id, name, provider_type, lifecycle_state, enabled, payload, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)", (provider.provider_id, provider.name, provider.provider_type.value, provider.lifecycle_state.value, int(provider.enabled), json.dumps(payload), provider.created_at, provider.updated_at))

    def model_provider_by_id(self, provider_id: str) -> dict[str, Any] | None:
        with self._connect() as db:
            row = db.execute("SELECT * FROM model_providers WHERE provider_id = ?", (provider_id,)).fetchone()
        if not row:
            return None
        result = dict(row); result["payload"] = json.loads(result["payload"]); return result

    def find_model_providers(self, enabled: bool | None = None, limit: int = 100) -> list[dict[str, Any]]:
        query = "SELECT * FROM model_providers"; values: list[Any] = []
        if enabled is not None:
            query += " WHERE enabled = ?"; values.append(int(enabled))
        query += " ORDER BY name, provider_id LIMIT ?"; values.append(limit)
        with self._connect() as db:
            rows = db.execute(query, tuple(values)).fetchall()
        return [{**dict(row), "payload": json.loads(row["payload"])} for row in rows]

    def save_model(self, model: Any) -> None:
        payload = model.to_dict()
        with self._connect() as db:
            db.execute("INSERT OR REPLACE INTO models(model_id, provider_id, name, version, lifecycle_state, enabled, health_state, architecture_version, payload, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", (model.model_id, model.provider_id, model.name, model.version, model.lifecycle_state.value, int(model.enabled), model.health.state.value, model.architecture_version, json.dumps(payload), model.created_at, model.updated_at))
            db.execute("DELETE FROM model_capabilities WHERE model_id = ?", (model.model_id,))
            for capability in model.capabilities:
                db.execute("INSERT OR REPLACE INTO model_capabilities(capability_id, model_id, name, payload, created_at) VALUES (?, ?, ?, ?, ?)", (capability.capability_id, model.model_id, capability.name, json.dumps(capability.to_dict()), capability.created_at))

    def model_by_id(self, model_id: str) -> dict[str, Any] | None:
        with self._connect() as db:
            row = db.execute("SELECT * FROM models WHERE model_id = ?", (model_id,)).fetchone()
        if not row:
            return None
        result = dict(row); result["payload"] = json.loads(result["payload"]); return result

    def find_models(self, provider_id: str | None = None, enabled: bool | None = None, limit: int = 200) -> list[dict[str, Any]]:
        clauses: list[str] = []; values: list[Any] = []
        if provider_id: clauses.append("provider_id = ?"); values.append(provider_id)
        if enabled is not None: clauses.append("enabled = ?"); values.append(int(enabled))
        query = "SELECT * FROM models" + ((" WHERE " + " AND ".join(clauses)) if clauses else "") + " ORDER BY name, version, model_id LIMIT ?"; values.append(limit)
        with self._connect() as db:
            rows = db.execute(query, tuple(values)).fetchall()
        return [{**dict(row), "payload": json.loads(row["payload"])} for row in rows]

    def save_model_health(self, model_id: str, health: Any) -> None:
        with self._connect() as db:
            db.execute("INSERT INTO model_health(health_id, model_id, observed_at, state, payload) VALUES (?, ?, ?, ?, ?)", (new_id("model_health"), model_id, utc_now(), health.state.value, json.dumps(health.to_dict())))

    def find_model_health(self, model_id: str | None = None, limit: int = 200) -> list[dict[str, Any]]:
        query = "SELECT * FROM model_health"; values: list[Any] = []
        if model_id: query += " WHERE model_id = ?"; values.append(model_id)
        query += " ORDER BY observed_at DESC LIMIT ?"; values.append(limit)
        with self._connect() as db:
            rows = db.execute(query, tuple(values)).fetchall()
        return [{**dict(row), "payload": json.loads(row["payload"])} for row in rows]

    def save_model_evaluation(self, evaluation: Any) -> None:
        with self._connect() as db:
            db.execute("INSERT OR REPLACE INTO model_evaluations(evaluation_id, model_id, benchmark_id, decision, payload, created_at) VALUES (?, ?, ?, ?, ?, ?)", (evaluation.evaluation_id, evaluation.model_id, evaluation.benchmark_id, evaluation.decision.value, json.dumps(evaluation.to_dict()), evaluation.created_at))

    def model_evaluation_by_id(self, evaluation_id: str) -> dict[str, Any] | None:
        with self._connect() as db:
            row = db.execute("SELECT * FROM model_evaluations WHERE evaluation_id = ?", (evaluation_id,)).fetchone()
        if not row: return None
        result = dict(row); result["payload"] = json.loads(result["payload"]); return result

    def find_model_evaluations(self, model_id: str | None = None, limit: int = 200) -> list[dict[str, Any]]:
        query = "SELECT * FROM model_evaluations"; values: list[Any] = []
        if model_id: query += " WHERE model_id = ?"; values.append(model_id)
        query += " ORDER BY created_at DESC LIMIT ?"; values.append(limit)
        with self._connect() as db:
            rows = db.execute(query, tuple(values)).fetchall()
        return [{**dict(row), "payload": json.loads(row["payload"])} for row in rows]

    def save_model_trial(self, trial: Any) -> None:
        with self._connect() as db:
            db.execute("INSERT OR REPLACE INTO model_trials(trial_id, evaluation_id, model_id, benchmark_id, trial_number, success, verified, payload, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", (trial.trial_id, trial.evaluation_id, trial.model_id, trial.benchmark_id, trial.trial_number, int(trial.success), int(trial.verified), json.dumps(trial.to_dict()), trial.created_at))

    def find_model_trials(self, evaluation_id: str | None = None, model_id: str | None = None, limit: int = 500) -> list[dict[str, Any]]:
        clauses: list[str] = []; values: list[Any] = []
        if evaluation_id: clauses.append("evaluation_id = ?"); values.append(evaluation_id)
        if model_id: clauses.append("model_id = ?"); values.append(model_id)
        query = "SELECT * FROM model_trials" + ((" WHERE " + " AND ".join(clauses)) if clauses else "") + " ORDER BY trial_number, created_at LIMIT ?"; values.append(limit)
        with self._connect() as db:
            rows = db.execute(query, tuple(values)).fetchall()
        return [{**dict(row), "payload": json.loads(row["payload"])} for row in rows]

    def save_model_selection(self, selection: Any) -> None:
        with self._connect() as db:
            db.execute("INSERT OR REPLACE INTO model_selection_records(selection_id, task_id, model_id, payload, created_at) VALUES (?, ?, ?, ?, ?)", (selection.selection_id, selection.task_id, selection.selected_model_id or "", json.dumps(selection.to_dict()), selection.created_at))

    def find_model_selections(self, task_id: str | None = None, limit: int = 200) -> list[dict[str, Any]]:
        query = "SELECT * FROM model_selection_records"; values: list[Any] = []
        if task_id: query += " WHERE task_id = ?"; values.append(task_id)
        query += " ORDER BY created_at DESC LIMIT ?"; values.append(limit)
        with self._connect() as db:
            rows = db.execute(query, tuple(values)).fetchall()
        return [{**dict(row), "payload": json.loads(row["payload"])} for row in rows]

    def save_learning_observation(self, observation: Any) -> None:
        with self._connect() as db:
            db.execute("INSERT OR REPLACE INTO learning_observations(observation_id, task_id, payload, created_at) VALUES (?, ?, ?, ?)", (observation.observation_id, observation.task_id, json.dumps(observation.to_dict()), observation.created_at))

    def find_learning_observations(self, limit: int = 200) -> list[dict[str, Any]]:
        with self._connect() as db:
            rows = db.execute("SELECT * FROM learning_observations ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
        return [{**dict(row), "payload": json.loads(row["payload"])} for row in rows]

    def save_learning_outcome(self, outcome: Any) -> None:
        with self._connect() as db:
            db.execute("INSERT OR REPLACE INTO learning_outcomes(outcome_id, observation_id, payload, created_at) VALUES (?, ?, ?, ?)", (outcome.outcome_id, outcome.observation_id, json.dumps(outcome.to_dict()), outcome.created_at))

    def find_learning_outcomes(self, limit: int = 200) -> list[dict[str, Any]]:
        with self._connect() as db:
            rows = db.execute("SELECT * FROM learning_outcomes ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
        return [{**dict(row), "payload": json.loads(row["payload"])} for row in rows]

    def save_learning_adjustment(self, adjustment: Any) -> None:
        with self._connect() as db:
            db.execute("INSERT OR REPLACE INTO learning_adjustments(adjustment_id, status, affected_component, payload, created_at) VALUES (?, ?, ?, ?, ?)", (adjustment.adjustment_id, adjustment.status.value, adjustment.affected_component, json.dumps(adjustment.to_dict()), adjustment.created_at))

    def learning_adjustment_by_id(self, adjustment_id: str) -> dict[str, Any] | None:
        with self._connect() as db:
            row = db.execute("SELECT * FROM learning_adjustments WHERE adjustment_id = ?", (adjustment_id,)).fetchone()
        if not row: return None
        result = dict(row); result["payload"] = json.loads(result["payload"]); return result

    def find_learning_adjustments(self, affected_component: str | None = None, limit: int = 200) -> list[dict[str, Any]]:
        query = "SELECT * FROM learning_adjustments"; values: list[Any] = []
        if affected_component: query += " WHERE affected_component = ?"; values.append(affected_component)
        query += " ORDER BY created_at DESC LIMIT ?"; values.append(limit)
        with self._connect() as db:
            rows = db.execute(query, tuple(values)).fetchall()
        return [{**dict(row), "payload": json.loads(row["payload"])} for row in rows]

    def save_learning_policy(self, policy: Any) -> None:
        with self._connect() as db:
            db.execute("INSERT OR REPLACE INTO learning_policies(policy_id, version, enabled, payload, created_at) VALUES (?, ?, ?, ?, ?)", (policy.policy_id, policy.version, int(policy.enabled), json.dumps(policy.to_dict()), policy.created_at))

    def find_learning_policies(self, limit: int = 100) -> list[dict[str, Any]]:
        with self._connect() as db:
            rows = db.execute("SELECT * FROM learning_policies ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
        return [{**dict(row), "payload": json.loads(row["payload"])} for row in rows]

    def count_models(self) -> int:
        with self._connect() as db:
            row = db.execute("SELECT COUNT(*) AS count FROM models").fetchone()
        return int(row["count"]) if row else 0

    # Phase 18 Continuous Learning & Adaptive Intelligence persistence.
    def _save_phase18_record(self, table: str, columns: list[str], values: list[Any]) -> None:
        placeholders = ", ".join("?" for _ in columns)
        names = ", ".join(columns)
        with self._connect() as db:
            db.execute(f"INSERT OR REPLACE INTO {table}({names}) VALUES ({placeholders})", tuple(values))

    @staticmethod
    def _phase18_rows(rows: list[Any]) -> list[dict[str, Any]]:
        result = []
        for row in rows:
            item = dict(row)
            if isinstance(item.get("payload"), str):
                try: item["payload"] = json.loads(item["payload"])
                except (TypeError, ValueError): item["payload"] = {"malformed": True}
            result.append(item)
        return result

    def save_learning_pattern(self, record: Any) -> None:
        payload = record.to_dict()
        self._save_phase18_record("learning_patterns", ["pattern_id", "pattern_type", "frequency", "confidence", "lifecycle_state", "architecture_version", "payload", "created_at", "updated_at"], [record.pattern_id, record.pattern_type.value, record.frequency, record.confidence, record.lifecycle_state, record.architecture_version, json.dumps(payload), record.created_at, record.updated_at])

    def learning_pattern_by_id(self, pattern_id: str) -> dict[str, Any] | None:
        with self._connect() as db: row = db.execute("SELECT * FROM learning_patterns WHERE pattern_id = ?", (pattern_id,)).fetchone()
        return self._phase18_rows([row])[0] if row else None

    def find_learning_patterns(self, pattern_type: str | None = None, limit: int = 200) -> list[dict[str, Any]]:
        query = "SELECT * FROM learning_patterns"; values: list[Any] = []
        if pattern_type: query += " WHERE pattern_type = ?"; values.append(pattern_type)
        query += " ORDER BY created_at DESC LIMIT ?"; values.append(limit)
        with self._connect() as db: rows = db.execute(query, tuple(values)).fetchall()
        return self._phase18_rows(rows)

    def save_learning_hypothesis(self, record: Any) -> None:
        self._save_phase18_record("learning_hypotheses", ["hypothesis_id", "pattern_id", "status", "risk", "confidence", "payload", "created_at"], [record.hypothesis_id, record.pattern_id, record.status.value, record.risk, record.confidence, json.dumps(record.to_dict()), record.created_at])

    def learning_hypothesis_by_id(self, hypothesis_id: str) -> dict[str, Any] | None:
        with self._connect() as db: row = db.execute("SELECT * FROM learning_hypotheses WHERE hypothesis_id = ?", (hypothesis_id,)).fetchone()
        return self._phase18_rows([row])[0] if row else None

    def find_learning_hypotheses(self, status: str | None = None, limit: int = 200) -> list[dict[str, Any]]:
        query = "SELECT * FROM learning_hypotheses"; values: list[Any] = []
        if status: query += " WHERE status = ?"; values.append(status)
        query += " ORDER BY created_at DESC LIMIT ?"; values.append(limit)
        with self._connect() as db: rows = db.execute(query, tuple(values)).fetchall()
        return self._phase18_rows(rows)

    def save_adaptive_policy(self, record: Any) -> None:
        self._save_phase18_record("adaptive_policies", ["policy_id", "version", "enabled", "lifecycle_state", "architecture_version", "payload", "created_at", "updated_at"], [record.policy_id, record.version, int(record.enabled), record.lifecycle_state, record.architecture_version, json.dumps(record.to_dict()), record.created_at, record.updated_at])

    def adaptive_policy_by_id(self, policy_id: str) -> dict[str, Any] | None:
        with self._connect() as db: row = db.execute("SELECT * FROM adaptive_policies WHERE policy_id = ?", (policy_id,)).fetchone()
        return self._phase18_rows([row])[0] if row else None

    def find_adaptive_policies(self, enabled: bool | None = None, limit: int = 200) -> list[dict[str, Any]]:
        query = "SELECT * FROM adaptive_policies"; values: list[Any] = []
        if enabled is not None: query += " WHERE enabled = ?"; values.append(int(enabled))
        query += " ORDER BY created_at DESC LIMIT ?"; values.append(limit)
        with self._connect() as db: rows = db.execute(query, tuple(values)).fetchall()
        return self._phase18_rows(rows)

    def save_adaptive_adjustment(self, record: Any) -> None:
        self._save_phase18_record("adaptive_adjustments", ["adjustment_id", "policy_id", "status", "affected_component", "risk", "payload", "created_at", "updated_at"], [record.adjustment_id, record.policy_id, record.status.value, record.affected_component, record.risk, json.dumps(record.to_dict()), record.created_at, record.updated_at])

    def adaptive_adjustment_by_id(self, adjustment_id: str) -> dict[str, Any] | None:
        with self._connect() as db: row = db.execute("SELECT * FROM adaptive_adjustments WHERE adjustment_id = ?", (adjustment_id,)).fetchone()
        return self._phase18_rows([row])[0] if row else None

    def find_adaptive_adjustments(self, status: str | None = None, limit: int = 200) -> list[dict[str, Any]]:
        query = "SELECT * FROM adaptive_adjustments"; values: list[Any] = []
        if status: query += " WHERE status = ?"; values.append(status)
        query += " ORDER BY created_at DESC LIMIT ?"; values.append(limit)
        with self._connect() as db: rows = db.execute(query, tuple(values)).fetchall()
        return self._phase18_rows(rows)

    def save_adjustment_evaluation(self, record: Any) -> None:
        self._save_phase18_record("adjustment_evaluations", ["evaluation_id", "adjustment_id", "decision", "payload", "created_at"], [record.evaluation_id, record.adjustment_id, record.decision.value, json.dumps(record.to_dict()), record.created_at])

    def find_adjustment_evaluations(self, adjustment_id: str | None = None, limit: int = 200) -> list[dict[str, Any]]:
        query = "SELECT * FROM adjustment_evaluations"; values: list[Any] = []
        if adjustment_id: query += " WHERE adjustment_id = ?"; values.append(adjustment_id)
        query += " ORDER BY created_at DESC LIMIT ?"; values.append(limit)
        with self._connect() as db: rows = db.execute(query, tuple(values)).fetchall()
        return self._phase18_rows(rows)

    def save_learning_feedback(self, record: Any) -> None:
        self._save_phase18_record("learning_feedback", ["feedback_id", "task_id", "feedback_type", "payload", "created_at"], [record.feedback_id, record.task_id, record.feedback_type.value, json.dumps(record.to_dict()), record.created_at])

    def find_learning_feedback(self, task_id: str | None = None, limit: int = 200) -> list[dict[str, Any]]:
        query = "SELECT * FROM learning_feedback"; values: list[Any] = []
        if task_id: query += " WHERE task_id = ?"; values.append(task_id)
        query += " ORDER BY created_at DESC LIMIT ?"; values.append(limit)
        with self._connect() as db: rows = db.execute(query, tuple(values)).fetchall()
        return self._phase18_rows(rows)

    def save_counterfactual_evaluation(self, record: Any) -> None:
        self._save_phase18_record("counterfactual_evaluations", ["counterfactual_id", "task_id", "alternative_type", "decision", "payload", "created_at"], [record.counterfactual_id, record.task_id, record.alternative_type, record.decision.value, json.dumps(record.to_dict()), record.created_at])

    def find_counterfactual_evaluations(self, task_id: str | None = None, limit: int = 200) -> list[dict[str, Any]]:
        query = "SELECT * FROM counterfactual_evaluations"; values: list[Any] = []
        if task_id: query += " WHERE task_id = ?"; values.append(task_id)
        query += " ORDER BY created_at DESC LIMIT ?"; values.append(limit)
        with self._connect() as db: rows = db.execute(query, tuple(values)).fetchall()
        return self._phase18_rows(rows)

    def save_learning_conflict(self, record: Any) -> None:
        self._save_phase18_record("learning_conflicts", ["conflict_id", "target_type", "target_id", "status", "payload", "created_at"], [record.conflict_id, record.target_type, record.target_id, record.status, json.dumps(record.to_dict()), record.created_at])

    def find_learning_conflicts(self, target_id: str | None = None, limit: int = 200) -> list[dict[str, Any]]:
        query = "SELECT * FROM learning_conflicts"; values: list[Any] = []
        if target_id: query += " WHERE target_id = ?"; values.append(target_id)
        query += " ORDER BY created_at DESC LIMIT ?"; values.append(limit)
        with self._connect() as db: rows = db.execute(query, tuple(values)).fetchall()
        return self._phase18_rows(rows)

    def save_learning_rollback(self, record: Any) -> None:
        self._save_phase18_record("learning_rollbacks", ["rollback_id", "adjustment_id", "status", "payload", "created_at"], [record.rollback_id, record.adjustment_id, record.status, json.dumps(record.to_dict()), record.created_at])

    def find_learning_rollbacks(self, adjustment_id: str | None = None, limit: int = 200) -> list[dict[str, Any]]:
        query = "SELECT * FROM learning_rollbacks"; values: list[Any] = []
        if adjustment_id: query += " WHERE adjustment_id = ?"; values.append(adjustment_id)
        query += " ORDER BY created_at DESC LIMIT ?"; values.append(limit)
        with self._connect() as db: rows = db.execute(query, tuple(values)).fetchall()
        return self._phase18_rows(rows)

    def save_learning_cycle(self, record: Any) -> None:
        self._save_phase18_record("learning_cycles", ["cycle_id", "status", "started_at", "completed_at", "payload"], [record.cycle_id, record.status.value, record.started_at, record.completed_at, json.dumps(record.to_dict())])

    def learning_cycle_by_id(self, cycle_id: str) -> dict[str, Any] | None:
        with self._connect() as db: row = db.execute("SELECT * FROM learning_cycles WHERE cycle_id = ?", (cycle_id,)).fetchone()
        return self._phase18_rows([row])[0] if row else None

    def find_learning_cycles(self, status: str | None = None, limit: int = 200) -> list[dict[str, Any]]:
        query = "SELECT * FROM learning_cycles"; values: list[Any] = []
        if status: query += " WHERE status = ?"; values.append(status)
        query += " ORDER BY started_at DESC LIMIT ?"; values.append(limit)
        with self._connect() as db: rows = db.execute(query, tuple(values)).fetchall()
        return self._phase18_rows(rows)

    # Phase 19 Self-Model & Meta-Cognition persistence.
    @staticmethod
    def _phase19_rows(rows: Iterable[Any]) -> list[dict[str, Any]]:
        result = []
        for row in rows:
            if not row:
                continue
            item = dict(row)
            if isinstance(item.get("payload"), str):
                try:
                    item["payload"] = json.loads(item["payload"])
                except (TypeError, ValueError):
                    pass
            result.append(item)
        return result

    def _save_phase19(self, table: str, columns: list[str], values: list[Any]) -> None:
        names = ", ".join(columns)
        placeholders = ", ".join("?" for _ in columns)
        with self._connect() as db:
            db.execute(f"INSERT OR REPLACE INTO {table}({names}) VALUES ({placeholders})", tuple(values))

    def save_self_model_claim(self, record: Any) -> None:
        self._save_phase19("self_model_claims", ["claim_id", "category", "subject", "confidence", "lifecycle_state", "architecture_version", "environment_id", "evidence_ids", "provenance", "payload", "created_at", "updated_at"], [record.claim_id, record.category.value, record.subject, record.confidence, record.lifecycle_state, record.architecture_version, record.environment_id, json.dumps(record.evidence_ids), json.dumps(record.provenance), json.dumps(record.to_dict()), record.created_at, record.updated_at])

    def self_model_claim_by_id(self, claim_id: str) -> dict[str, Any] | None:
        with self._connect() as db: row = db.execute("SELECT * FROM self_model_claims WHERE claim_id = ?", (claim_id,)).fetchone()
        return self._phase19_rows([row])[0] if row else None

    def find_self_model_claims(self, category: str | None = None, subject: str | None = None, limit: int = 500) -> list[dict[str, Any]]:
        clauses: list[str] = []; values: list[Any] = []
        if category: clauses.append("category = ?"); values.append(category)
        if subject: clauses.append("subject = ?"); values.append(subject)
        query = "SELECT * FROM self_model_claims" + ((" WHERE " + " AND ".join(clauses)) if clauses else "") + " ORDER BY updated_at DESC LIMIT ?"; values.append(limit)
        with self._connect() as db: rows = db.execute(query, tuple(values)).fetchall()
        return self._phase19_rows(rows)

    def save_self_model_snapshot(self, record: Any) -> None:
        self._save_phase19("self_model_snapshots", ["snapshot_id", "active_version", "architecture_version", "environment_id", "status", "payload", "created_at"], [record.snapshot_id, record.active_version, record.architecture_version, record.environment_id, record.status, json.dumps(record.to_dict()), record.created_at])

    def self_model_snapshot_by_id(self, snapshot_id: str) -> dict[str, Any] | None:
        with self._connect() as db: row = db.execute("SELECT * FROM self_model_snapshots WHERE snapshot_id = ?", (snapshot_id,)).fetchone()
        return self._phase19_rows([row])[0] if row else None

    def latest_self_model_snapshot(self) -> dict[str, Any] | None:
        with self._connect() as db: row = db.execute("SELECT * FROM self_model_snapshots ORDER BY created_at DESC LIMIT 1").fetchone()
        return self._phase19_rows([row])[0] if row else None

    def find_self_model_snapshots(self, limit: int = 100) -> list[dict[str, Any]]:
        with self._connect() as db: rows = db.execute("SELECT * FROM self_model_snapshots ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
        return self._phase19_rows(rows)

    def save_self_model_limitation(self, record: Any) -> None:
        self._save_phase19("self_model_limitations", ["limitation_id", "limitation_type", "severity", "frequency", "confidence", "lifecycle_state", "architecture_version", "environment_id", "evidence_ids", "payload", "created_at", "updated_at"], [record.limitation_id, record.limitation_type.value, record.severity, record.frequency, record.confidence, record.lifecycle_state, record.architecture_version, record.environment_id, json.dumps(record.evidence_ids), json.dumps(record.to_dict()), record.created_at, record.updated_at])

    def self_model_limitation_by_id(self, limitation_id: str) -> dict[str, Any] | None:
        with self._connect() as db: row = db.execute("SELECT * FROM self_model_limitations WHERE limitation_id = ?", (limitation_id,)).fetchone()
        return self._phase19_rows([row])[0] if row else None

    def find_self_model_limitations(self, limitation_type: str | None = None, lifecycle_state: str | None = None, limit: int = 300) -> list[dict[str, Any]]:
        clauses: list[str] = []; values: list[Any] = []
        if limitation_type: clauses.append("limitation_type = ?"); values.append(limitation_type)
        if lifecycle_state: clauses.append("lifecycle_state = ?"); values.append(lifecycle_state)
        query = "SELECT * FROM self_model_limitations" + ((" WHERE " + " AND ".join(clauses)) if clauses else "") + " ORDER BY updated_at DESC LIMIT ?"; values.append(limit)
        with self._connect() as db: rows = db.execute(query, tuple(values)).fetchall()
        return self._phase19_rows(rows)

    def save_self_model_assumption(self, record: Any) -> None:
        self._save_phase19("self_model_assumptions", ["assumption_id", "statement", "confidence", "validation_status", "dependent_task", "invalidation_condition", "architecture_version", "environment_id", "payload", "created_at", "updated_at"], [record.assumption_id, record.statement, record.confidence, record.validation_status.value, record.dependent_task, record.invalidation_condition, record.architecture_version, record.environment_id, json.dumps(record.to_dict()), record.created_at, record.updated_at])

    def self_model_assumption_by_id(self, assumption_id: str) -> dict[str, Any] | None:
        with self._connect() as db: row = db.execute("SELECT * FROM self_model_assumptions WHERE assumption_id = ?", (assumption_id,)).fetchone()
        return self._phase19_rows([row])[0] if row else None

    def find_self_model_assumptions(self, validation_status: str | None = None, dependent_task: str | None = None, limit: int = 300) -> list[dict[str, Any]]:
        clauses: list[str] = []; values: list[Any] = []
        if validation_status: clauses.append("validation_status = ?"); values.append(validation_status)
        if dependent_task: clauses.append("dependent_task = ?"); values.append(dependent_task)
        query = "SELECT * FROM self_model_assumptions" + ((" WHERE " + " AND ".join(clauses)) if clauses else "") + " ORDER BY updated_at DESC LIMIT ?"; values.append(limit)
        with self._connect() as db: rows = db.execute(query, tuple(values)).fetchall()
        return self._phase19_rows(rows)

    def save_self_model_uncertainty(self, record: Any) -> None:
        self._save_phase19("self_model_uncertainty", ["uncertainty_id", "uncertainty_type", "severity", "confidence", "lifecycle_state", "architecture_version", "environment_id", "evidence_ids", "payload", "created_at"], [record.uncertainty_id, record.uncertainty_type, record.severity, record.confidence, record.lifecycle_state, record.architecture_version, record.environment_id, json.dumps(record.evidence_ids), json.dumps(record.to_dict()), record.created_at])

    def find_self_model_uncertainty(self, uncertainty_type: str | None = None, limit: int = 300) -> list[dict[str, Any]]:
        query = "SELECT * FROM self_model_uncertainty"; values: list[Any] = []
        if uncertainty_type: query += " WHERE uncertainty_type = ?"; values.append(uncertainty_type)
        query += " ORDER BY created_at DESC LIMIT ?"; values.append(limit)
        with self._connect() as db: rows = db.execute(query, tuple(values)).fetchall()
        return self._phase19_rows(rows)

    def save_self_model_conflict(self, record: Any) -> None:
        self._save_phase19("self_model_conflicts", ["conflict_id", "subject", "status", "architecture_version", "environment_id", "payload", "created_at"], [record.conflict_id, record.subject, record.status, record.architecture_version, record.environment_id, json.dumps(record.to_dict()), record.created_at])

    def find_self_model_conflicts(self, subject: str | None = None, limit: int = 300) -> list[dict[str, Any]]:
        query = "SELECT * FROM self_model_conflicts"; values: list[Any] = []
        if subject: query += " WHERE subject = ?"; values.append(subject)
        query += " ORDER BY created_at DESC LIMIT ?"; values.append(limit)
        with self._connect() as db: rows = db.execute(query, tuple(values)).fetchall()
        return self._phase19_rows(rows)

    def save_decision_readiness(self, record: Any) -> None:
        self._save_phase19("decision_readiness", ["readiness_id", "goal_id", "state", "confidence", "architecture_version", "environment_id", "payload", "created_at"], [record.readiness_id, record.goal_id, record.state.value, record.confidence, record.architecture_version, record.environment_id, json.dumps(record.to_dict()), record.created_at])

    def find_decision_readiness(self, goal_id: str | None = None, limit: int = 200) -> list[dict[str, Any]]:
        query = "SELECT * FROM decision_readiness"; values: list[Any] = []
        if goal_id: query += " WHERE goal_id = ?"; values.append(goal_id)
        query += " ORDER BY created_at DESC LIMIT ?"; values.append(limit)
        with self._connect() as db: rows = db.execute(query, tuple(values)).fetchall()
        return self._phase19_rows(rows)

    def save_meta_reasoning(self, record: Any) -> None:
        self._save_phase19("meta_reasoning_records", ["record_id", "goal_id", "recommendation", "confidence", "architecture_version", "environment_id", "payload", "created_at"], [record.record_id, record.goal_id, record.recommendation, record.confidence, record.architecture_version, record.environment_id, json.dumps(record.to_dict()), record.created_at])

    def find_meta_reasoning(self, goal_id: str | None = None, limit: int = 200) -> list[dict[str, Any]]:
        query = "SELECT * FROM meta_reasoning_records"; values: list[Any] = []
        if goal_id: query += " WHERE goal_id = ?"; values.append(goal_id)
        query += " ORDER BY created_at DESC LIMIT ?"; values.append(limit)
        with self._connect() as db: rows = db.execute(query, tuple(values)).fetchall()
        return self._phase19_rows(rows)

    def save_confidence_calibration(self, record: Any) -> None:
        self._save_phase19("confidence_calibration", ["calibration_id", "subject", "predicted_confidence", "actual_verified", "calibration_state", "error", "architecture_version", "environment_id", "payload", "created_at"], [record.calibration_id, record.subject, record.predicted_confidence, int(record.actual_verified), record.calibration_state.value, record.error, record.architecture_version, record.environment_id, json.dumps(record.to_dict()), record.created_at])

    def find_confidence_calibration(self, subject: str | None = None, limit: int = 300) -> list[dict[str, Any]]:
        query = "SELECT * FROM confidence_calibration"; values: list[Any] = []
        if subject: query += " WHERE subject = ?"; values.append(subject)
        query += " ORDER BY created_at DESC LIMIT ?"; values.append(limit)
        with self._connect() as db: rows = db.execute(query, tuple(values)).fetchall()
        return self._phase19_rows(rows)

    def save_self_reflection(self, record: Any) -> None:
        outcome = getattr(record, "outcome", "verified" if getattr(record, "actual_verified", False) else "not_verified")
        verified = getattr(record, "verified", getattr(record, "actual_verified", False))
        self._save_phase19("self_reflections", ["reflection_id", "task_id", "outcome", "verified", "architecture_version", "environment_id", "payload", "created_at"], [record.reflection_id, record.task_id, outcome, int(bool(verified)), record.architecture_version, record.environment_id, json.dumps(record.to_dict()), getattr(record, "created_at", getattr(record, "timestamp", utc_now()))])

    def find_self_reflections(self, task_id: str | None = None, limit: int = 200) -> list[dict[str, Any]]:
        query = "SELECT * FROM self_reflections"; values: list[Any] = []
        if task_id: query += " WHERE task_id = ?"; values.append(task_id)
        query += " ORDER BY created_at DESC LIMIT ?"; values.append(limit)
        with self._connect() as db: rows = db.execute(query, tuple(values)).fetchall()
        return self._phase19_rows(rows)

    def save_self_diagnostics(self, record: Any) -> None:
        self._save_phase19("self_diagnostics", ["diagnostic_id", "status", "architecture_version", "environment_id", "payload", "created_at"], [record.diagnostic_id, record.status, record.architecture_version, record.environment_id, json.dumps(record.to_dict()), record.created_at])

    def find_self_diagnostics(self, limit: int = 200) -> list[dict[str, Any]]:
        with self._connect() as db: rows = db.execute("SELECT * FROM self_diagnostics ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
        return self._phase19_rows(rows)
