from __future__ import annotations

import json
from pathlib import Path

import pytest

from evo_agent.benchmark import AggregateMetrics, Benchmark, BenchmarkEngine, EvolutionEvidence, RegressionResult, TaskCase, TrialResult
from evo_agent.evolver import EvolutionProposal
from evo_agent.models import ComparisonClass, ExperimentStatus, ProposalRisk, ProposalStatus
from evo_agent.sandbox import SandboxEngine
from evo_agent.storage import SQLiteStore


def proposal() -> EvolutionProposal:
    return EvolutionProposal(
        "proposal_benchmark",
        "2026-08-01T00:00:00+00:00",
        ["exp"],
        ["eval"],
        "0.3.0",
        "strategy-selection",
        "Repeated strategy failure is visible in historical evidence.",
        [{"experience_id": "exp", "evaluation_id": "eval", "outcome": "failure"}],
        "Prefer the recovery strategy for comparable tasks.",
        "Improve verified completion.",
        ["Regression risk."],
        ["strategy_selection"],
        [],
        0.8,
        "Compare success and verification rates.",
        "Restore the prior strategy preference.",
        ProposalStatus.APPROVED,
        ProposalRisk.LOW,
        "0.3.0",
    )


def setup_experiment(tmp_path: Path) -> tuple[BenchmarkEngine, SQLiteStore, dict]:
    production = tmp_path / "production"
    production.mkdir(parents=True)
    (production / "test_candidate.py").write_text("def test_candidate_passes():\n    assert True\n", encoding="utf-8")
    store = SQLiteStore(tmp_path / "workspace" / ".evo" / "agent.sqlite3")
    store.save_proposal(proposal())
    sandbox = SandboxEngine(store, production, tmp_path / "sandboxes", timeout_seconds=5)
    experiment = sandbox.run_experiment("proposal_benchmark", retain_sandbox=True)
    assert experiment.status is ExperimentStatus.PASSED
    return BenchmarkEngine(store, production), store, experiment.to_dict()


def test_benchmark_validation_and_versioning(tmp_path: Path):
    engine, store, _ = setup_experiment(tmp_path)
    benchmark = BenchmarkEngine.default_benchmark("benchmark_test")
    assert engine.validate_benchmark(benchmark) == []
    engine.save_benchmark(benchmark)
    loaded = engine.load_benchmark("benchmark_test")
    assert loaded is not None
    assert loaded.benchmark_version == "benchmark-v1"
    invalid = Benchmark("", "", "", "", [], {}, [], 0, 0, None, "")
    assert engine.validate_benchmark(invalid)
    tables = {row[0] for row in store._connect().execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "benchmarks" in tables


def test_repeated_trials_and_metrics_are_recorded(tmp_path: Path):
    engine, store, experiment = setup_experiment(tmp_path)
    benchmark = BenchmarkEngine.default_benchmark("benchmark_metrics")
    benchmark.trial_count = 2
    engine.save_benchmark(benchmark)
    evidence = engine.run(benchmark.benchmark_id, experiment["experiment_id"])
    trials = store.find_benchmark_trials(benchmark_id=benchmark.benchmark_id, experiment_id=experiment["experiment_id"])
    assert len(trials) == len(benchmark.task_cases) * benchmark.trial_count * 2
    assert evidence.baseline_metrics["total_trials"] == len(benchmark.task_cases) * benchmark.trial_count
    assert evidence.candidate_metrics["total_trials"] == len(benchmark.task_cases) * benchmark.trial_count
    assert evidence.baseline_metrics["success_rate"] == 0.6667
    assert evidence.candidate_metrics["success_rate"] == 1.0
    assert evidence.trial_count == len(trials)


def test_candidate_improvement_is_better_and_evidence_is_persisted(tmp_path: Path):
    engine, store, experiment = setup_experiment(tmp_path)
    benchmark = BenchmarkEngine.default_benchmark("benchmark_better")
    benchmark.trial_count = 2
    engine.save_benchmark(benchmark)
    evidence = engine.run(benchmark.benchmark_id, experiment["experiment_id"])
    assert evidence.decision is ComparisonClass.BETTER
    assert evidence.target_improvement is True
    assert evidence.reproducibility_metadata["deterministic_seed"] == 0
    stored = store.evidence_by_id(evidence.evidence_id)
    assert stored is not None
    assert json.loads(stored["payload"])["decision"] == "better"


def test_no_change_and_worse_regression_decisions(tmp_path: Path):
    engine = BenchmarkEngine(SQLiteStore(tmp_path / "store.sqlite3"), tmp_path)
    benchmark = BenchmarkEngine.default_benchmark("benchmark_decisions")
    benchmark.trial_count = 1
    benchmark.task_cases = [TaskCase("same", "same", "", "pass", "pass", ["pytest"], 5, "always_pass")]
    baseline = TrialResult("b1", "b", "e", "baseline", "same", 1, "s", "e", True, True, 100, False, "", "", 10, 1, 0, 0, 0, 0, True, {"network_policy": "denied", "environment_keys": [], "fixed_runner": True})
    candidate = TrialResult("c1", "b", "e", "candidate", "same", 1, "s", "e", True, True, 100, False, "", "", 10, 1, 0, 0, 0, 0, True, {"network_policy": "denied", "environment_keys": [], "fixed_runner": True})
    regressions = engine.detect_regressions([baseline], [candidate])
    safety = engine.evaluate_safety([baseline], [candidate], {"sandbox_location": "/tmp/e", "candidate_id": "c"}, True)
    evidence = engine.produce_evidence(benchmark, {"experiment_id": "e", "proposal_id": "p", "candidate_id": "c", "baseline_version": "b", "candidate_version": "c", "isolation_policy": {}}, {"proposal_id": "p"}, engine.aggregate_results([baseline]).to_dict(), engine.aggregate_results([candidate]).to_dict(), regressions, safety, [baseline], [candidate])
    assert evidence.decision is ComparisonClass.NO_CHANGE

    candidate_failed = TrialResult("c2", "b", "e", "candidate", "same", 1, "s", "e", False, False, 0, False, "failure", "", 10, 1, 0, 0, 0, 0, True, {"network_policy": "denied", "environment_keys": [], "fixed_runner": True})
    regressions = engine.detect_regressions([baseline], [candidate_failed])
    assert regressions.functional_regressions
    safety = engine.evaluate_safety([baseline], [candidate_failed], {"sandbox_location": "/tmp/e", "candidate_id": "c"}, True)
    worse = engine.produce_evidence(benchmark, {"experiment_id": "e2", "proposal_id": "p", "candidate_id": "c", "baseline_version": "b", "candidate_version": "c", "isolation_policy": {}}, {"proposal_id": "p"}, engine.aggregate_results([baseline]).to_dict(), engine.aggregate_results([candidate_failed]).to_dict(), regressions, safety, [baseline], [candidate_failed])
    assert worse.decision is ComparisonClass.WORSE


def test_safety_failure_is_hard_gate(tmp_path: Path):
    engine = BenchmarkEngine(SQLiteStore(tmp_path / "store.sqlite3"), tmp_path)
    benchmark = BenchmarkEngine.default_benchmark("benchmark_safety")
    trial = TrialResult("t", "b", "e", "candidate", "simple-environment", 1, "s", "e", True, True, 100, False, "", "", 10, 1, 0, 0, 0, 0, False, {"network_policy": "allowed", "environment_keys": ["OPENAI_API_KEY"], "fixed_runner": False})
    safety = {"production_unchanged": False, "candidate_isolated": True, "network_denied": False, "host_secrets_absent": False, "bounded_commands": False, "candidate_safety_ok": False}
    evidence = engine.produce_evidence(benchmark, {"experiment_id": "e", "proposal_id": "p", "candidate_id": "c", "baseline_version": "b", "candidate_version": "c", "isolation_policy": {}}, {"proposal_id": "p"}, engine.aggregate_results([trial]).to_dict(), engine.aggregate_results([trial]).to_dict(), RegressionResult([], [], [], [], [{"reason": "unsafe"}]), safety, [trial], [trial])
    assert evidence.decision is ComparisonClass.WORSE
    assert "safety gate failed" in " ".join(evidence.decision_reason)


def test_ineligible_experiment_produces_inconclusive_evidence(tmp_path: Path):
    engine, store, experiment = setup_experiment(tmp_path)
    benchmark = BenchmarkEngine.default_benchmark("benchmark_ineligible")
    engine.save_benchmark(benchmark)
    with store._connect() as db:
        db.execute("UPDATE evolution_experiments SET status='failed' WHERE experiment_id=?", (experiment["experiment_id"],))
    evidence = engine.run(benchmark.benchmark_id, experiment["experiment_id"])
    assert evidence.decision is ComparisonClass.INCONCLUSIVE
    assert evidence.reproducibility_metadata["eligibility_errors"]


def test_reproducibility_metadata_contains_versions_and_rules(tmp_path: Path):
    engine, _, experiment = setup_experiment(tmp_path)
    benchmark = BenchmarkEngine.default_benchmark("benchmark_repro")
    benchmark.trial_count = 1
    engine.save_benchmark(benchmark)
    evidence = engine.run(benchmark.benchmark_id, experiment["experiment_id"])
    metadata = evidence.reproducibility_metadata
    assert metadata["benchmark_id"] == benchmark.benchmark_id
    assert metadata["benchmark_version"] == benchmark.version
    assert metadata["baseline_version"] == experiment["baseline_version"]
    assert metadata["candidate_version"] == experiment["candidate_version"]
    assert metadata["evaluation_rules"] == benchmark.evaluation_metrics
