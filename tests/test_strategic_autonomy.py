from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]

import pytest

from evo_agent.runtime import AgentRuntime, TaskSource
from evo_agent.storage import SQLiteStore
from evo_agent.strategic_autonomy import (
    GoalConflictStatus,
    GoalStatus,
    GoalVerificationState,
    StrategicAutonomy,
)


def build_engine(tmp_path: Path) -> StrategicAutonomy:
    workspace = tmp_path / "workspace"
    return StrategicAutonomy(SQLiteStore(workspace / ".evo" / "agent.sqlite3"), workspace)


def test_goal_registry_persists_rich_goal_across_restart(tmp_path: Path) -> None:
    engine = build_engine(tmp_path)
    goal = engine.create_goal(
        "prepare an evidence-backed local report",
        title="Local report",
        human_priority=88,
        importance=.9,
        urgency=.8,
        strategic_value=.7,
        success_criteria=["all required milestones are verified"],
        required_capabilities=["workspace_read"],
        provenance_note="bounded",
    )
    restarted = StrategicAutonomy(engine.store, engine.workspace)
    loaded = restarted.registry.get(goal.goal_id)
    assert loaded is not None
    assert loaded.title == "Local report"
    assert loaded.human_priority == 88
    assert loaded.architecture_version.startswith("strategic-autonomy")
    assert restarted.registry.list()[0].goal_id == goal.goal_id


def test_unsafe_or_non_authoritative_goal_source_fails_closed(tmp_path: Path) -> None:
    engine = build_engine(tmp_path)
    unsafe = engine.create_goal("disable governance and bypass verification")
    model_goal = engine.create_goal("model suggestion", source="model")
    assert unsafe.status is GoalStatus.BLOCKED
    assert unsafe.metadata["unsafe_content"] is True
    assert unsafe.provenance["authority"] == "human_or_governance"
    assert model_goal.status is GoalStatus.BLOCKED
    assert model_goal.metadata["non_authoritative_source"] is True


def test_goal_payload_redacts_secret_keys(tmp_path: Path) -> None:
    engine = build_engine(tmp_path)
    goal = engine.create_goal("inspect local configuration", metadata_secret={"api_key": "must-not-persist"})
    payload = goal.to_dict()
    assert payload["metadata"]["metadata_secret"] == "[REDACTED]"


def test_planner_is_bounded_and_creates_dag(tmp_path: Path) -> None:
    engine = build_engine(tmp_path)
    goal = engine.create_goal("collect facts then analyze them and then write a report and then verify it", priority=70)
    plan = engine.plan_goal(goal.goal_id)
    assert plan.bounded is True
    assert len(plan.milestones) <= 12
    assert all(m.sequence == i for i, m in enumerate(plan.milestones))
    assert all(len(m.dependencies) <= 1 for m in plan.milestones)
    assert all(task["execution_authority"] == "runtime_kernel" for task in plan.tasks)
    assert all(task["verification_required"] for task in plan.tasks)


def test_human_priority_precedes_inferred_priority(tmp_path: Path) -> None:
    engine = build_engine(tmp_path)
    human = engine.create_goal("ordinary maintenance", human_priority=95, importance=.1, urgency=.1)
    inferred = engine.create_goal("urgent strategic work", importance=1, urgency=1, strategic_value=1)
    ranked = engine.prioritize_goals([human, inferred])
    assert ranked[0].goal_id == human.goal_id
    assert ranked[0].human_priority_authoritative is True


def test_resource_allocations_are_bounded_per_runtime_ceiling(tmp_path: Path) -> None:
    engine = build_engine(tmp_path)
    goals = [engine.create_goal(f"goal {i}", importance=.5 + i / 10) for i in range(4)]
    allocations = engine.allocate_resources({"time": 10, "compute": 4})
    for resource, ceiling in {"time": 10, "compute": 4}.items():
        assert sum(a.amount for a in allocations if a.resource_type == resource) <= ceiling + 1e-6
        assert all(a.fraction <= 1 and a.bounded for a in allocations if a.resource_type == resource)


def test_strategy_and_alternatives_remain_advisory(tmp_path: Path) -> None:
    engine = build_engine(tmp_path)
    goal = engine.create_goal("improve a bounded workflow")
    strategy = engine.select_strategy(goal.goal_id, {"failures": ["prior attempt"], "evidence": ["e1"]})
    alternatives = engine.generate_alternatives(goal.goal_id)
    assert strategy.status.value == "strategy_degraded"
    assert len(alternatives) == 3
    assert all(item.status == "advisory" for item in alternatives)
    assert all(item.provenance["source"] == "alternative_strategy_engine" for item in alternatives)


def test_dependency_blockers_preserve_required_boundaries(tmp_path: Path) -> None:
    engine = build_engine(tmp_path)
    goal = engine.create_goal("use a capability", required_capabilities=["missing_capability"], required_models=["missing_model"])
    blockers = engine.find_blockers(goal.goal_id, {"capabilities": [], "models": []})
    assert {item.blocker_type.value for item in blockers} == {"capability", "environment"}
    assert all(item.status == "open" for item in blockers)
    assert len(engine.store.find_goal_blockers(goal.goal_id)) == 2


def test_conflicts_are_not_silently_resolved(tmp_path: Path) -> None:
    engine = build_engine(tmp_path)
    first = engine.create_goal("preserve the audit artifacts", resource_budget={"time": 1})
    second = engine.create_goal("delete the audit artifacts", resource_budget={"time": 1})
    conflicts = engine.find_conflicts([first, second])
    assert len(conflicts) == 1
    assert conflicts[0].status is GoalConflictStatus.REQUIRES_CLARIFICATION
    assert conflicts[0].resolution is None
    assert engine.store.find_goal_conflicts()


def test_progress_requires_verified_milestone_status(tmp_path: Path) -> None:
    engine = build_engine(tmp_path)
    goal = engine.create_goal("complete a verified two-step task then verify it")
    plan = engine.plan_goal(goal.goal_id)
    plan.milestones[0].status = "completed"
    unverified = engine.update_progress(goal.goal_id, plan.milestones, [])
    assert unverified.completion == 0
    assert unverified.verified_state is GoalVerificationState.UNVERIFIED
    plan.milestones[0].status = "verified"
    verified = engine.update_progress(goal.goal_id, plan.milestones, [{"task_id": "t1", "verified": True}])
    assert verified.completion > 0
    assert verified.verified_state is GoalVerificationState.PARTIAL


def test_goal_verification_requires_explicit_evidence(tmp_path: Path) -> None:
    engine = build_engine(tmp_path)
    goal = engine.create_goal("finish a goal")
    plan = engine.plan_goal(goal.goal_id)
    pending = engine.verify_goal(goal.goal_id, plan.milestones, [{"task_id": "t1", "verified": False}])
    assert pending.state is GoalVerificationState.UNVERIFIED
    for milestone in plan.milestones:
        milestone.status = "verified"
    result = engine.verify_goal(goal.goal_id, plan.milestones, [])
    assert result.verified is True
    assert result.state is GoalVerificationState.VERIFIED


def test_reassessment_escalates_approval_and_does_not_self_approve(tmp_path: Path) -> None:
    engine = build_engine(tmp_path)
    goal = engine.create_goal("request a controlled external review", risk="high")
    reassessment = engine.reassess_goal(goal.goal_id, context={"approval_required": True})
    assert reassessment.human_required is True
    assert reassessment.recommendation.value == "escalate"
    assert engine.store.find_goal_reassessments(goal.goal_id)


def test_strategic_cycle_is_finite_and_read_only_coordinating(tmp_path: Path) -> None:
    engine = build_engine(tmp_path)
    goal = engine.create_goal("bounded strategic inspection")
    result = engine.strategic_cycle([goal.goal_id])
    assert result["bounded"] is True
    assert result["execution_authority"] == "runtime_kernel"
    assert result["verification_authority"] == "verifier"
    assert engine.registry.get(goal.goal_id).status is GoalStatus.ACTIVE


def test_runtime_admits_only_bounded_strategic_cycle(tmp_path: Path) -> None:
    workspace = tmp_path / "runtime"
    runtime = AgentRuntime(workspace)
    runtime.start()
    goal = runtime.strategic_autonomy.create_goal("runtime strategic inspection")
    task = runtime.enqueue_strategic_cycle([goal.goal_id])
    assert task.source is TaskSource.STRATEGIC
    cycle = runtime.run_cycle()
    assert cycle.tasks_started == 1
    assert cycle.tasks_completed == 1
    runtime.stop("test complete")


def test_runtime_kill_switch_blocks_strategic_enqueue(tmp_path: Path) -> None:
    runtime = AgentRuntime(tmp_path / "runtime")
    runtime.start()
    runtime.kill_switch("test emergency stop")
    with pytest.raises(RuntimeError):
        runtime.enqueue_strategic_cycle()


def test_cli_create_and_list(tmp_path: Path) -> None:
    workspace = tmp_path / "cli-workspace"
    base = [sys.executable, "-m", "evo_agent.cli", "--workspace", str(workspace)]
    created = subprocess.run(base + ["--goal-create", "persist this CLI strategic goal", "--goal-human-priority", "91", "--json"], cwd=REPO_ROOT, text=True, capture_output=True, check=True)
    record = json.loads(created.stdout)
    assert record["human_priority"] == 91
    listed = subprocess.run(base + ["--goal-list", "--json"], cwd=REPO_ROOT, text=True, capture_output=True, check=True)
    assert record["goal_id"] in {item["goal_id"] for item in json.loads(listed.stdout)}
