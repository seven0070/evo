from __future__ import annotations

import hashlib
from pathlib import Path

from evo_agent.models import OutcomeType, OrchestrationPath, OpportunityStatus, WorkItemState
from evo_agent.orchestrator import EvolutionOpportunity, EvolutionOrchestrator, OrchestrationPolicy
from evo_agent.experience import Experience
from evo_agent.storage import SQLiteStore


def failure(index: int, goal: str = "repeated workspace failure") -> Experience:
    return Experience(
        experience_id=f"stress-exp-{index}",
        task_id=f"stress-task-{index}",
        original_goal=f"{goal} {index}",
        task_type="workspace_inspection",
        task_complexity="simple",
        selected_strategy="direct",
        selected_tools=["workspace_list"],
        execution_steps=[],
        observations=[],
        failures=[{"tool": "workspace_list", "error": "controlled failure"}],
        recovery_attempts=[],
        strategy_changes=[],
        verification_result={"success": False},
        final_outcome=OutcomeType.FAILURE,
        duration_ms=10,
        resource_information={},
        approval_events=[],
        timestamp=f"2026-01-01T00:00:{index:02d}+00:00",
        agent_version="1.0.0",
        model_identifier="offline",
        evaluation_id=f"stress-eval-{index}",
        evaluation_result={"success_score": 0},
    )


def make_orchestrator(tmp_path: Path) -> tuple[EvolutionOrchestrator, SQLiteStore, Path]:
    source = tmp_path / "source"
    (source / "evo_agent").mkdir(parents=True)
    for name in ("kernel.py", "security.py", "verifier.py", "storage.py", "sandbox.py"):
        (source / "evo_agent" / name).write_text("# protected fixture\n", encoding="utf-8")
    store = SQLiteStore(tmp_path / "workspace" / ".evo" / "agent.sqlite3")
    return EvolutionOrchestrator(store, source, policy=OrchestrationPolicy(cooldown_seconds=60)), store, source


def test_large_protected_proposal_volume_is_rejected_without_work_items(tmp_path: Path) -> None:
    orchestrator, store, source = make_orchestrator(tmp_path)
    before = {path: hashlib.sha256((source / "evo_agent" / path).read_bytes()).hexdigest() for path in ("kernel.py", "security.py", "verifier.py", "storage.py", "sandbox.py")}
    for index in range(50):
        opportunity = EvolutionOpportunity(
            opportunity_id=f"protected-{index}",
            source_experience_ids=[f"exp-{index}"],
            source_evaluation_ids=[f"eval-{index}"],
            problem="disable governance and replace rollback authority",
            frequency=5,
            severity="critical",
            affected_task_types=[],
            affected_components=["governance", "rollback"],
            affected_capabilities=[],
            evidence_strength="strong",
            recommended_change_type=OrchestrationPath.METAMORPHOSIS,
                            confidence=0.99,
                fingerprint=f"protected-fingerprint-{index}",
            )

        assert orchestrator.create_work_item(opportunity) is None
    assert orchestrator.list_work_items() == []
    assert len(store.find_opportunities(limit=100)) == 50
    assert all(row["status"] == OpportunityStatus.IGNORED.value for row in store.find_opportunities(limit=100))
    after = {path: hashlib.sha256((source / "evo_agent" / path).read_bytes()).hexdigest() for path in before}
    assert before == after


def test_repeated_evidence_is_deduplicated_and_cooldown_prevents_work_item_fanout(tmp_path: Path) -> None:
    orchestrator, store, _ = make_orchestrator(tmp_path)
    records = [failure(index) for index in range(12)]
    for record in records:
        orchestrator.ingest_experience(record)
    opportunities = orchestrator.detector.detect(records)
    assert opportunities
    first = orchestrator.create_work_item(opportunities[0])
    assert first is not None
    again = orchestrator.create_work_item(opportunities[0])
    assert again is not None
    assert again.work_item_id == first.work_item_id
    assert len(orchestrator.list_work_items()) == 1
    assert len(store.find_work_items(limit=100)) == 1


def test_bad_candidate_and_direct_activation_paths_remain_terminal(tmp_path: Path) -> None:
    orchestrator, _, _ = make_orchestrator(tmp_path)
    opportunity = EvolutionOpportunity(
        "bad-candidate", [], [], "candidate requests unrestricted generated code execution", 4, "high", [], ["planner"], [], "strong", OrchestrationPath.EVOLUTION, 0.9,
    )
    item = orchestrator.create_work_item(opportunity)
    assert item is not None
    assert orchestrator.transition(item.work_item_id, WorkItemState.ANALYZING).current_state.value == "analyzing"
    try:
        orchestrator.transition(item.work_item_id, WorkItemState.COMPLETED)
    except ValueError:
        pass
    assert orchestrator.get_work_item(item.work_item_id).current_state.value != "completed"
