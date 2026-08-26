from __future__ import annotations

import json
from pathlib import Path
import shutil

import pytest

from evo_agent.evolver import EvolutionProposal
from evo_agent.models import CandidateStatus, ComparisonClass, ExperimentStatus, ProposalRisk, ProposalStatus
from evo_agent.sandbox import SandboxEngine
from evo_agent.storage import SQLiteStore


def make_proposal(proposal_id: str = "proposal_test", status: ProposalStatus = ProposalStatus.APPROVED, target: str = "strategy-selection") -> EvolutionProposal:
    return EvolutionProposal(
        proposal_id=proposal_id,
        created_at="2026-05-01T00:00:00+00:00",
        source_experiences=["exp_test"],
        source_evaluations=["eval_test"],
        agent_version="0.3.0",
        target_component=target,
        observed_problem="Repeated strategy failure is visible in historical evidence.",
        evidence=[{"experience_id": "exp_test", "evaluation_id": "eval_test", "outcome": "failure"}],
        proposed_change="Prefer the recovery strategy for this task type.",
        expected_benefit="Improve verified completion on comparable tasks.",
        risks=["Regression on unseen task variants."],
        affected_capabilities=["strategy_selection"],
        affected_permissions=[],
        confidence=0.8,
        evaluation_method="Compare verified success rate against the baseline.",
        rollback_plan="Restore the previous strategy preference.",
        status=status,
        risk=ProposalRisk.LOW if target != "verification" else ProposalRisk.PROTECTED,
        evolver_version="0.3.0",
    )


def setup_engine(tmp_path: Path, proposal: EvolutionProposal | None = None, test_content: str | None = None) -> tuple[SandboxEngine, SQLiteStore, Path]:
    production = tmp_path / "production"
    production.mkdir(parents=True)
    content = test_content or "def test_candidate_passes():\n    assert True\n"
    (production / "test_candidate.py").write_text(content, encoding="utf-8")
    (production / "test_sandbox_controlled.py").write_text(content, encoding="utf-8")
    (production / "production_marker.txt").write_text("unchanged", encoding="utf-8")
    store = SQLiteStore(production / ".evo" / "agent.sqlite3")
    if proposal:
        store.save_proposal(proposal)
    engine = SandboxEngine(store, production, tmp_path / "sandbox", timeout_seconds=5)
    return engine, store, production


def test_unapproved_proposals_cannot_enter_sandbox(tmp_path: Path):
    for status in (ProposalStatus.GENERATED, ProposalStatus.PENDING_REVIEW, ProposalStatus.REJECTED):
        engine, _, _ = setup_engine(tmp_path / status.value, make_proposal(f"proposal_{status.value}", status))
        with pytest.raises(PermissionError):
            engine.create_sandbox(f"proposal_{status.value}")


def test_approved_proposal_creates_isolated_candidate_and_applies_structured_change(tmp_path: Path):
    engine, _, production = setup_engine(tmp_path, make_proposal())
    experiment, proposal, baseline, candidate_dir = engine.create_sandbox("proposal_test")
    assert Path(experiment.sandbox_location) != production
    assert baseline != production
    assert candidate_dir != production
    candidate = engine.prepare_candidate(experiment, candidate_dir)
    engine.apply_approved_proposal(proposal, candidate)
    config = json.loads((candidate_dir / "evolution_config.json").read_text(encoding="utf-8"))
    assert config["target_component"] == "strategy-selection"
    assert config["executable_code_generated"] is False
    assert (production / "evolution_config.json").exists() is False
    engine.destroy_sandbox(experiment)
    assert not Path(experiment.sandbox_location).exists()


def test_protected_and_unsupported_targets_fail_closed(tmp_path: Path):
    engine, _, _ = setup_engine(tmp_path, make_proposal("proposal_protected", ProposalStatus.APPROVED, "verification"))
    with pytest.raises(PermissionError):
        engine.create_sandbox("proposal_protected")
    engine, _, _ = setup_engine(tmp_path / "unsupported", make_proposal("proposal_unsupported", ProposalStatus.APPROVED, "arbitrary-source-code"))
    with pytest.raises(PermissionError):
        engine.create_sandbox("proposal_unsupported")


def test_candidate_execution_is_sanitized_and_captures_output(tmp_path: Path):
    engine, _, _ = setup_engine(tmp_path, make_proposal())
    experiment, proposal, baseline, candidate_dir = engine.create_sandbox("proposal_test")
    candidate = engine.prepare_candidate(experiment, candidate_dir)
    engine.apply_approved_proposal(proposal, candidate)
    result = engine.execute_candidate(experiment, candidate_dir, "candidate")
    assert result.completed is True
    assert result.return_code == 0, result.error or result.output[-2000:]
    assert result.tests_passed == 1
    assert result.timeout is False
    assert "PATH" in result.environment_keys
    assert "OPENAI_API_KEY" not in result.environment_keys
    assert "EVO_NETWORK_POLICY" in result.environment_keys
    engine.destroy_sandbox(experiment)


def test_production_remains_unchanged_after_candidate_execution(tmp_path: Path):
    engine, _, production = setup_engine(tmp_path, make_proposal())
    before = (production / "production_marker.txt").read_bytes()
    experiment, proposal, baseline, candidate_dir = engine.create_sandbox("proposal_test")
    candidate = engine.prepare_candidate(experiment, candidate_dir)
    engine.apply_approved_proposal(proposal, candidate)
    result = engine.execute_candidate(experiment, candidate_dir, "candidate")
    assert result.return_code == 0, result.error or result.output[-2000:]
    assert (production / "production_marker.txt").read_bytes() == before
    engine.destroy_sandbox(experiment)


def test_candidate_cannot_modify_production_inside_namespace(tmp_path: Path):
    engine, _, production = setup_engine(tmp_path, make_proposal())
    experiment, proposal, _, candidate_dir = engine.create_sandbox("proposal_test")
    candidate = engine.prepare_candidate(experiment, candidate_dir)
    engine.apply_approved_proposal(proposal, candidate)
    attack = f"from pathlib import Path\\nPath({str(production / 'production_marker.txt')!r}).write_text('tampered')\\n"
    (candidate_dir / "test_out_of_bound.py").write_text(f"def test_out_of_bound():\\n    exec({attack!r})\\n", encoding="utf-8")
    result = engine.execute_candidate(experiment, candidate_dir, "candidate")
    assert result.return_code != 0
    assert (production / "production_marker.txt").read_text(encoding="utf-8") == "unchanged"
    engine.destroy_sandbox(experiment)


def test_timeout_is_terminated_and_recorded(tmp_path: Path):
    engine, _, _ = setup_engine(tmp_path, make_proposal())
    experiment, proposal, baseline, candidate_dir = engine.create_sandbox("proposal_test")
    candidate = engine.prepare_candidate(experiment, candidate_dir)
    engine.apply_approved_proposal(proposal, candidate)
    (candidate_dir / "test_timeout.py").write_text("import time\ndef test_timeout():\n    time.sleep(30)\n", encoding="utf-8")
    result = engine.execute_candidate(experiment, candidate_dir, "candidate")
    assert result.timeout is True, result.error or result.output[-2000:]
    assert result.completed is False
    assert result.error
    engine.destroy_sandbox(experiment)


def test_comparison_classifies_better_worse_no_change_and_inconclusive(tmp_path: Path):
    engine, _, _ = setup_engine(tmp_path, make_proposal())
    base = {"return_code": 1, "timeout": False}
    good = {"return_code": 0, "timeout": False}
    bad = {"return_code": 1, "timeout": False}
    timeout = {"return_code": None, "timeout": True}
    assert engine.compare_with_baseline(base, good).classification is ComparisonClass.BETTER
    assert engine.compare_with_baseline(good, bad).classification is ComparisonClass.WORSE
    assert engine.compare_with_baseline(good, good).classification is ComparisonClass.NO_CHANGE
    assert engine.compare_with_baseline(good, timeout).classification is ComparisonClass.INCONCLUSIVE


def test_full_experiment_persists_and_cleans_up_without_promotion(tmp_path: Path):
    engine, store, production = setup_engine(tmp_path, make_proposal())
    before = (production / "production_marker.txt").read_bytes()
    experiment = engine.run_experiment("proposal_test")
    assert experiment.status is ExperimentStatus.PASSED, experiment.errors
    assert experiment.cleanup_status == "destroyed"
    assert experiment.comparison is not None
    assert experiment.comparison["classification"] in {"better", "no_change", "worse", "inconclusive"}
    assert experiment.candidate["status"] == CandidateStatus.DESTROYED.value
    persisted = engine.get_experiment(experiment.experiment_id)
    assert persisted is not None
    assert persisted.status is ExperimentStatus.PASSED
    assert not Path(experiment.sandbox_location).exists()
    assert (production / "production_marker.txt").read_bytes() == before
    proposal = store.proposal_by_id("proposal_test")
    assert json.loads(proposal["payload"])["status"] == ProposalStatus.APPROVED.value


def test_failed_experiment_cleans_up_and_records_failure(tmp_path: Path):
    engine, _, _ = setup_engine(tmp_path, make_proposal(), test_content="def test_candidate_fails():\n    assert False\n")
    experiment = engine.run_experiment("proposal_test")
    assert experiment.cleanup_status == "destroyed"
    assert experiment.status is ExperimentStatus.FAILED
    assert experiment.errors
    assert not Path(experiment.sandbox_location).exists()


def test_full_timeout_experiment_records_timeout_and_cleans_up(tmp_path: Path):
    engine, _, _ = setup_engine(tmp_path, make_proposal(), test_content="import time\ndef test_timeout():\n    time.sleep(20)\n")
    engine.timeout_seconds = 1
    experiment = engine.run_experiment("proposal_test")
    assert experiment.status is ExperimentStatus.TIMEOUT
    assert experiment.timeout is True
    assert experiment.cleanup_status == "destroyed"
    assert not Path(experiment.sandbox_location).exists()


def test_cleanup_failure_is_recorded(tmp_path: Path):
    engine, _, _ = setup_engine(tmp_path, make_proposal())
    experiment, _, _, candidate_dir = engine.create_sandbox("proposal_test")
    sandbox_path = Path(experiment.sandbox_location)
    shutil_target = sandbox_path / "not-a-directory"
    shutil_target.write_text("file", encoding="utf-8")
    experiment.sandbox_location = str(shutil_target)
    destroyed = engine.destroy_sandbox(experiment)
    assert destroyed.cleanup_status.startswith("failed:")
    assert destroyed.errors


def test_out_of_boundary_execution_and_invalid_commands_fail_closed(tmp_path: Path):
    engine, _, _ = setup_engine(tmp_path, make_proposal())
    experiment, _, _, candidate_dir = engine.create_sandbox("proposal_test")
    with pytest.raises(PermissionError):
        engine.execute_candidate(experiment, Path(experiment.sandbox_location), "candidate")
    with pytest.raises(PermissionError):
        engine.execute_candidate(experiment, candidate_dir, "candidate", command=("python3", "-c", "print('unsafe')"))
    engine.destroy_sandbox(experiment)


def test_sandbox_events_are_auditable(tmp_path: Path):
    engine, store, _ = setup_engine(tmp_path, make_proposal())
    experiment = engine.run_experiment("proposal_test")
    events = store.events_for_task("sandbox")
    event_types = {event["event_type"] for event in events}
    assert {"sandbox_created", "baseline_snapshot_created", "candidate_created", "proposal_applied", "candidate_started", "candidate_test_started", "candidate_test_completed", "sandbox_cleanup_started", "sandbox_destroyed"}.issubset(event_types)
    assert all(event["payload"]["experiment_id"] == experiment.experiment_id for event in events)


def test_bwrap_command_preserves_working_directory_and_private_writable_state(tmp_path: Path, monkeypatch):
    engine, _, _ = setup_engine(tmp_path, make_proposal())
    experiment, _, _, candidate_dir = engine.create_sandbox("proposal_test")
    monkeypatch.setattr("evo_agent.sandbox.shutil.which", lambda name: "/usr/bin/bwrap" if name == "bwrap" else None)
    command = engine._isolated_command(candidate_dir, ["python3", "-m", "pytest", "-q"])
    assert command[0] == "bwrap"
    assert {"--unshare-user", "--unshare-net", "--unshare-pid"}.issubset(command)
    assert command[command.index("--bind") + 1] == str(candidate_dir)
    assert command[command.index("--chdir") + 1] == str(candidate_dir)
    assert ["--bind", str(Path(experiment.sandbox_location) / "results")] == command[command.index("--bind", command.index("--bind") + 1):command.index("--bind", command.index("--bind") + 1) + 2]
    assert (Path(experiment.sandbox_location) / "metadata" / "home").is_dir()
    engine.destroy_sandbox(experiment)


def test_isolation_fallback_keeps_namespace_controls_when_bwrap_unavailable(tmp_path: Path, monkeypatch):
    engine, _, _ = setup_engine(tmp_path, make_proposal())
    experiment, _, _, candidate_dir = engine.create_sandbox("proposal_test")
    monkeypatch.setattr("evo_agent.sandbox.shutil.which", lambda _: None)
    command = engine._isolated_command(candidate_dir, ["python3", "-m", "pytest", "-q"])
    assert command[:3] == ["unshare", "--user", "--map-root-user"]
    assert {"--mount", "--net", "--pid", "--fork"}.issubset(command)
    assert str(candidate_dir) in command
    engine.destroy_sandbox(experiment)


def test_sanitized_environment_uses_managed_home_and_tmpdir(tmp_path: Path):
    engine, _, _ = setup_engine(tmp_path, make_proposal())
    experiment, _, _, _ = engine.create_sandbox("proposal_test")
    environment = engine._sanitized_environment(experiment)
    assert environment["HOME"] == str(Path(experiment.sandbox_location) / "metadata" / "home")
    assert environment["TMPDIR"] == str(Path(experiment.sandbox_location) / "results")
    assert environment["EVO_NETWORK_POLICY"] == "denied"
    assert "OPENAI_API_KEY" not in environment
    engine.destroy_sandbox(experiment)


@pytest.mark.parametrize("backend", ["bwrap", "unshare"])
def test_benchmark_backend_contract_is_portable(tmp_path: Path, monkeypatch, backend: str):
    from evo_agent.benchmark import BenchmarkEngine

    engine = BenchmarkEngine(SQLiteStore(tmp_path / "store.sqlite3"), tmp_path)
    experiment = {"sandbox_location": str(tmp_path / "experiment"), "experiment_id": "experiment_test"}
    (tmp_path / "experiment" / "metadata" / "home").mkdir(parents=True)
    (tmp_path / "experiment" / "results").mkdir()
    location = tmp_path / "experiment" / "candidate"
    location.mkdir()
    if backend == "bwrap":
        monkeypatch.setattr("evo_agent.benchmark.shutil.which", lambda name: "/usr/bin/bwrap" if name == "bwrap" else None)
        command = engine._isolated_command(location, ["python3", "-m", "pytest", "-q"])
        assert command[0] == "bwrap"
        assert ["--bind", str(location), str(location)] == command[command.index("--bind"):command.index("--bind") + 3]
    else:
        monkeypatch.setattr("evo_agent.benchmark.shutil.which", lambda _: None)
        command = engine._isolated_command(location, ["python3", "-m", "pytest", "-q"])
        assert command[0] == "unshare"
    environment = engine._sanitized_environment(experiment)
    assert environment["TMPDIR"] == str(tmp_path / "experiment" / "results")
    assert environment["EVO_NETWORK_POLICY"] == "denied"
