from __future__ import annotations

from pathlib import Path

from evo_agent.evolver import EvolutionProposal, Evolver
from evo_agent.experience import Experience
from evo_agent.models import OutcomeType, ProposalRisk, ProposalStatus
from evo_agent.storage import SQLiteStore


def make_experience(
    index: int,
    *,
    task_type: str = "workspace_inspection",
    strategy: str = "direct",
    outcome: OutcomeType = OutcomeType.SUCCESS,
    score: int = 100,
    failures: list[dict] | None = None,
    retries: int = 0,
    replans: int = 0,
    strategy_changes: int = 0,
    tools: list[str] | None = None,
) -> Experience:
    experience_id = f"exp_fixture_{index}"
    evaluation_id = f"eval_fixture_{index}"
    return Experience(
        experience_id=experience_id,
        task_id=f"task_fixture_{index}",
        original_goal=f"fixture goal {task_type} {index}",
        task_type=task_type,
        task_complexity="simple",
        selected_strategy=strategy,
        selected_tools=tools or ["workspace_list"],
        execution_steps=[{"tool": (tools or ["workspace_list"])[0]}],
        observations=["recorded observation"],
        failures=failures or [],
        recovery_attempts=([{"action": "retry"}] * retries) + ([{"action": "replan"}] * replans),
        strategy_changes=([{"from": strategy, "to": "recovery"}] * strategy_changes),
        verification_result={"success": outcome is OutcomeType.SUCCESS},
        final_outcome=outcome,
        duration_ms=10,
        resource_information={"event_count": 5},
        approval_events=[],
        timestamp=f"2026-01-01T00:00:{index:02d}+00:00",
        agent_version="0.3.0",
        model_identifier="fixture",
        evaluation_id=evaluation_id,
        evaluation_result={
            "success_score": score,
            "retry_count": retries,
            "replan_count": replans,
            "strategy_changes": strategy_changes,
        },
    )


def setup_evolver(tmp_path: Path, experiences: list[Experience]) -> Evolver:
    store = SQLiteStore(tmp_path / ".evo" / "agent.sqlite3")
    for experience in experiences:
        store.save_experience(experience)
    return Evolver(store)


def test_detect_repeated_failures_low_performance_and_inefficiency(tmp_path: Path):
    records = [
        make_experience(1, outcome=OutcomeType.FAILURE, score=0, failures=[{"tool": "workspace_list", "error": "failed"}]),
        make_experience(2, outcome=OutcomeType.FAILURE, score=0, failures=[{"tool": "workspace_list", "error": "failed"}]),
        make_experience(3, outcome=OutcomeType.SUCCESS, score=50, retries=1, replans=1, strategy_changes=1),
        make_experience(4, outcome=OutcomeType.SUCCESS, score=50, retries=1, replans=1, strategy_changes=1),
    ]
    findings = setup_evolver(tmp_path, records).analyze_experiences(records)
    finding_types = {finding.finding_type for finding in findings}
    assert "repeated_failure" in finding_types
    assert "low_performance" in finding_types
    assert "inefficient_execution" in finding_types
    assert "tool_problem" in finding_types


def test_detect_successful_alternative_strategy(tmp_path: Path):
    records = [
        make_experience(1, task_type="research_or_analysis", strategy="strategy-a", outcome=OutcomeType.FAILURE, score=0, failures=[{"step": "research"}]),
        make_experience(2, task_type="research_or_analysis", strategy="strategy-a", outcome=OutcomeType.FAILURE, score=0, failures=[{"step": "research"}]),
        make_experience(3, task_type="research_or_analysis", strategy="strategy-b", outcome=OutcomeType.SUCCESS, score=100),
        make_experience(4, task_type="research_or_analysis", strategy="strategy-b", outcome=OutcomeType.SUCCESS, score=100),
    ]
    findings = setup_evolver(tmp_path, records).identify_opportunities(records)
    assert any(finding.finding_type == "successful_alternative" for finding in findings)
    assert any("strategy-b" in finding.proposed_change for finding in findings)


def test_valid_proposal_contains_evidence_and_bounded_rollback(tmp_path: Path):
    records = [make_experience(1, outcome=OutcomeType.FAILURE, score=0, failures=[{"error": "failed"}]), make_experience(2, outcome=OutcomeType.FAILURE, score=0, failures=[{"error": "failed"}])]
    evolver = setup_evolver(tmp_path, records)
    proposals = evolver.analyze_and_persist(records)
    assert proposals
    proposal = proposals[0]
    assert proposal.status is ProposalStatus.PENDING_REVIEW
    assert proposal.source_experiences
    assert proposal.source_evaluations
    assert proposal.evidence
    assert proposal.rollback_plan
    assert proposal.risk is not ProposalRisk.PROTECTED
    assert 0 < proposal.confidence <= 1


def test_incomplete_and_vague_proposals_are_rejected(tmp_path: Path):
    evolver = setup_evolver(tmp_path, [])
    incomplete = EvolutionProposal(
        proposal_id="proposal_incomplete", created_at="2026-01-01T00:00:00+00:00", source_experiences=[], source_evaluations=[], agent_version="0.3.0", target_component="strategy", observed_problem="", evidence=[], proposed_change="Make the agent smarter.", expected_benefit="", risks=[], affected_capabilities=[], affected_permissions=[], confidence=0, evaluation_method="", rollback_plan="", evolver_version="0.3.0",
    )
    validation = evolver.evaluate_proposal(incomplete)
    assert validation.valid is False
    assert any("evidence" in error for error in validation.errors)
    assert any("vague" in error for error in validation.errors)


def test_protected_target_is_rejected_and_not_executable(tmp_path: Path):
    evolver = setup_evolver(tmp_path, [])
    proposal = EvolutionProposal(
        proposal_id="proposal_protected", created_at="2026-01-01T00:00:00+00:00", source_experiences=["exp_1"], source_evaluations=["eval_1"], agent_version="0.3.0", target_component="permissions", observed_problem="Permission policy is inconvenient for one task type.", evidence=[{"experience_id": "exp_1", "evaluation_id": "eval_1"}], proposed_change="Change permission policy to permit the operation.", expected_benefit="Fewer blocked tasks.", risks=["Security regression"], affected_capabilities=["permissions"], affected_permissions=["workspace"], confidence=0.8, evaluation_method="Compare blocked-task rate.", rollback_plan="Restore the prior permission policy.", evolver_version="0.3.0",
    )
    validation = evolver.evaluate_proposal(proposal)
    assert validation.valid is False
    assert validation.risk is ProposalRisk.PROTECTED
    evolver.persist_proposal(proposal)
    stored = evolver.get_proposal(proposal.proposal_id)
    assert stored is not None
    assert stored.status is ProposalStatus.REJECTED
    assert not (tmp_path / "production.py").exists()


def test_confidence_is_transparent_and_deterministic(tmp_path: Path):
    evolver = setup_evolver(tmp_path, [])
    evidence = [{"experience_id": "a", "task_type": "x", "outcome": "failure"}, {"experience_id": "b", "task_type": "x", "outcome": "failure"}]
    assert evolver.calculate_confidence(evidence) == evolver.calculate_confidence(evidence)
    assert 0 < evolver.calculate_confidence(evidence) <= 1


def test_proposal_persistence_history_and_approval_rejection(tmp_path: Path):
    records = [make_experience(1, outcome=OutcomeType.FAILURE, score=0, failures=[{"error": "failed"}]), make_experience(2, outcome=OutcomeType.FAILURE, score=0, failures=[{"error": "failed"}])]
    evolver = setup_evolver(tmp_path, records)
    first, second = evolver.analyze_and_persist(records)[:2]
    approved = evolver.approve(first.proposal_id, "Authorized for a future isolated sandbox only")
    rejected = evolver.reject(second.proposal_id, "Evidence is not sufficient for this proposal")
    assert approved.status is ProposalStatus.APPROVED
    assert approved.approval_decision == "approved_for_future_sandbox"
    assert rejected.status is ProposalStatus.REJECTED
    assert len(evolver.list_proposals()) >= 2
    event_types = {row["event_type"] for row in evolver.store.events_for_task("evolver")}
    assert {"evolution_analysis_started", "weakness_detected", "proposal_generated", "proposal_validated", "proposal_approved", "proposal_rejected"}.issubset(event_types)


def test_evolver_does_not_modify_or_execute_generated_code(tmp_path: Path):
    marker = tmp_path / "production.txt"
    marker.write_text("unchanged", encoding="utf-8")
    records = [make_experience(1, outcome=OutcomeType.FAILURE, score=0, failures=[{"error": "failed"}]), make_experience(2, outcome=OutcomeType.FAILURE, score=0, failures=[{"error": "failed"}])]
    evolver = setup_evolver(tmp_path, records)
    evolver.analyze_and_persist(records)
    assert marker.read_text(encoding="utf-8") == "unchanged"
    assert not (tmp_path / "generated.py").exists()
