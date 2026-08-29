"""Promotion is blocked by an inconclusive verdict - and by an incomplete set of suites (07 §9.7).

`07` §8 names this file as P6's acceptance test for the benchmark gate, and the sentence it pins has two
halves that are easy to collapse into one:

* the decision must be `BETTER`, so `INCONCLUSIVE`, `NO_CHANGE`, and `WORSE` are all refusals - and
  `INCONCLUSIVE` in particular must not be read as "no harm found, proceed";
* the verdict must come from the **required suites**, not from whichever suite happened to favour the
  candidate. A `better` on `core-local` with no `hold-out` row is not a measured improvement, it is a
  convenient measurement, and `03` §I.3's hold-out suite exists precisely because a corpus a proposal can
  read is a corpus a proposal can be tuned against.

Both halves are tested against `PromotionEngine.validate_eligibility` rather than against a re-implementation
of it, because the thing under test is the gate the CLI actually calls. The v1-shaped case is in here too, and
it is the one that keeps the new leg honest: evidence written before benchmark v2 existed is *not* retroactively
invalid, since refusing it would make the gate depend on when a row was written rather than on what it shows.

`setup_candidate` is imported from `tests/test_promotion.py` (the shared builder the whole promotion suite
uses) rather than copied, so a change to the promotion fixtures cannot leave this file pinning a scenario that
no longer reaches the gate it claims to test.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from evo_agent.benchmark import EvolutionEvidence  # noqa: E402
from evo_agent.benchmark_suites import REQUIRED_FOR_PROMOTION, coverage, suite_names  # noqa: E402
from evo_agent.models import ComparisonClass  # noqa: E402
from evo_agent.storage import SQLiteStore  # noqa: E402
from test_promotion import setup_candidate  # noqa: E402

_NO_REGRESSIONS = {key: [] for key in ("functional_regressions", "verification_regressions", "timeout_regressions", "efficiency_regressions", "safety_regressions")}


def _row(
    evidence_id: str,
    suite: str,
    *,
    experiment_id: str,
    proposal_id: str,
    baseline: str,
    candidate: str,
    decision: ComparisonClass = ComparisonClass.BETTER,
    regressions: dict[str, Any] | None = None,
    benchmark_version: str = "benchmark-v2",
) -> EvolutionEvidence:
    return EvolutionEvidence(
        evidence_id=evidence_id,
        experiment_id=experiment_id,
        proposal_id=proposal_id,
        benchmark_id=f"benchmark-{suite}-v2",
        baseline_version=baseline,
        candidate_version=candidate,
        trial_count=6,
        baseline_metrics={"success_rate": 0.5, "verification_rate": 0.5, "mean_duration_ms": 100.0, "score_variance": 0.0},
        candidate_metrics={"success_rate": 1.0, "verification_rate": 1.0, "mean_duration_ms": 110.0, "score_variance": 0.0},
        metric_differences={"success_rate": 0.5},
        regression_results=dict(_NO_REGRESSIONS, **(regressions or {})),
        safety_results={"production_unchanged": True, "candidate_isolated": True, "network_denied": True, "host_secrets_absent": True, "bounded_commands": True, "candidate_safety_ok": True},
        target_improvement=decision is ComparisonClass.BETTER,
        decision=decision,
        decision_reason=[f"unit fixture: {suite} -> {decision.value}"],
        benchmark_version=benchmark_version,
        evaluator_version="benchmark-evaluator-v1",
        created_at="2026-09-01T00:00:00+00:00",
        reproducibility_metadata={"suite": suite, "deterministic_seed": 0, "trial_count_per_side": 3},
    )


def _scenario(tmp_path: Path, *, required_decision: ComparisonClass = ComparisonClass.BETTER, drop: tuple[str, ...] = (), primary_version: str = "benchmark-v2", extra_regression: str | None = None):
    """A promotable candidate whose evidence set is controlled case by case."""
    engine, store, experiment, _candidate, _production = setup_candidate(tmp_path)
    rows = []
    target = _row(
        "evidence_v2_target",
        "core-local",
        experiment_id=experiment.experiment_id,
        proposal_id=experiment.proposal_id,
        baseline=experiment.baseline_version,
        candidate=experiment.candidate_version,
        benchmark_version=primary_version,
    )
    rows.append(target)
    for suite in REQUIRED_FOR_PROMOTION:
        if suite in drop:
            continue
        rows.append(
            _row(
                f"evidence_v2_{suite}",
                suite,
                experiment_id=experiment.experiment_id,
                proposal_id=experiment.proposal_id,
                baseline=experiment.baseline_version,
                candidate=experiment.candidate_version,
                decision=required_decision,
                regressions=({"functional_regressions": [{"task_case_id": f"{suite}.x", "trial": 1}]}) if extra_regression == suite else None,
            )
        )
    for row in rows:
        store.save_evolution_evidence(row)
    candidate = engine.register_candidate(experiment.experiment_id, target.evidence_id)
    return engine, store, experiment, candidate, rows


class TestInconclusiveBlocksPromotion:
    @pytest.mark.parametrize("decision", [ComparisonClass.INCONCLUSIVE, ComparisonClass.NO_CHANGE, ComparisonClass.WORSE])
    def test_a_non_better_verdict_is_a_refusal_whatever_the_suite_says(self, tmp_path: Path, decision: ComparisonClass) -> None:
        engine, store, experiment, _candidate, _rows = setup_candidate(tmp_path)
        row = _row(
            "evidence_not_better",
            "core-local",
            experiment_id=experiment.experiment_id,
            proposal_id=experiment.proposal_id,
            baseline=experiment.baseline_version,
            candidate=experiment.candidate_version,
            decision=decision,
        )
        store.save_evolution_evidence(row)
        candidate = engine.register_candidate(experiment.experiment_id, row.evidence_id)
        ok, errors, _context = engine.validate_eligibility(candidate.version_id, row.evidence_id)
        assert not ok, (decision, errors)
        assert any("not BETTER" in item for item in errors), errors

    def test_an_inconclusive_row_is_not_a_passing_verify_evidence(self, tmp_path: Path) -> None:
        engine, store, experiment, _candidate, _rows = setup_candidate(tmp_path)
        row = _row(
            "evidence_inconclusive",
            "core-local",
            experiment_id=experiment.experiment_id,
            proposal_id=experiment.proposal_id,
            baseline=experiment.baseline_version,
            candidate=experiment.candidate_version,
            decision=ComparisonClass.INCONCLUSIVE,
        )
        store.save_evolution_evidence(row)
        report = engine.verify_evidence(row.evidence_id)
        assert report["valid"] is False, report
        assert "not valid for promotion" in report["reason"], report

    def test_an_unstable_measurement_is_inconclusive_at_the_source(self, tmp_path: Path) -> None:
        # The verdict has to be produced before promotion is asked about it, so the ceiling is tested where it
        # is applied: a candidate whose trials disagree beyond the suite's `max_score_variance` never reaches
        # the promotion gate carrying a `better`.
        from evo_agent.benchmark import BenchmarkEngine
        from evo_agent.security import SecurityPolicy
        from evo_agent.benchmark_suites import _criteria

        engine = BenchmarkEngine(SQLiteStore(tmp_path / "e.db"), tmp_path)
        stable = {"success_rate": 0.5, "verification_rate": 1.0, "mean_duration_ms": 100.0, "score_variance": 0.0}
        improved = {"success_rate": 1.0, "verification_rate": 1.0, "mean_duration_ms": 100.0, "score_variance": 0.0}
        assert engine.compare_with_baseline(stable, improved, None, True, _criteria()) is ComparisonClass.BETTER
        unstable = {"success_rate": 1.0, "verification_rate": 1.0, "mean_duration_ms": 100.0, "score_variance": 0.4}
        # The unstable candidate has the *same* headline numbers as the stable one above, and no verdict at
        # all: `BETTER` would credit a jump that the trial spread cannot distinguish from noise.
        assert engine.compare_with_baseline(stable, unstable, None, True, _criteria()) is ComparisonClass.INCONCLUSIVE, "a jump measured inside noise was reported as an improvement"
        # And instability on the baseline side is the same problem: a noisy yardstick cannot certify "no
        # change" either, so the check is on both sides rather than only on the candidate's.
        both_sides = {"success_rate": 1.0, "verification_rate": 1.0, "mean_duration_ms": 100.0, "score_variance": 0.4}
        assert engine.compare_with_baseline(both_sides, both_sides, None, True, _criteria()) is ComparisonClass.INCONCLUSIVE

    def test_spending_more_to_win_is_worse_not_better(self, tmp_path: Path) -> None:
        from evo_agent.benchmark import BenchmarkEngine
        from evo_agent.benchmark_suites import _criteria

        engine = BenchmarkEngine(SQLiteStore(tmp_path / "e.db"), tmp_path)
        baseline = {"success_rate": 0.5, "verification_rate": 1.0, "mean_duration_ms": 100.0, "score_variance": 0.0}
        candidate = {"success_rate": 1.0, "verification_rate": 1.0, "mean_duration_ms": 400.0, "score_variance": 0.0}
        assert engine.compare_with_baseline(baseline, candidate, None, True, _criteria()) is ComparisonClass.WORSE
        # And the ceiling is a property of the suite, not a global: a research suite may legitimately cost
        # more per trial, which is why the key is read from the criteria rather than hard-wired.
        assert engine.compare_with_baseline(baseline, candidate, None, True, _criteria(max_cost_ratio=5.0)) is ComparisonClass.BETTER


class TestSuiteCoverage:
    def test_a_missing_required_suite_is_a_refusal_naming_the_suites(self, tmp_path: Path) -> None:
        engine, _store, _experiment, candidate, _rows = _scenario(tmp_path, drop=tuple(REQUIRED_FOR_PROMOTION))
        ok, errors, _context = engine.validate_eligibility(candidate.version_id, "evidence_v2_target")
        assert not ok, errors
        joined = " ".join(errors)
        assert "coverage is incomplete" in joined, errors
        for suite in REQUIRED_FOR_PROMOTION:
            assert suite in joined, (suite, errors)

    def test_a_worse_verdict_on_a_required_suite_is_a_refusal(self, tmp_path: Path) -> None:
        engine, _store, _experiment, candidate, _rows = _scenario(tmp_path, required_decision=ComparisonClass.WORSE)
        ok, errors, _context = engine.validate_eligibility(candidate.version_id, "evidence_v2_target")
        assert not ok, errors
        assert any("regression on a required benchmark suite" in item for item in errors), errors

    def test_a_regression_recorded_on_a_required_suite_is_a_refusal(self, tmp_path: Path) -> None:
        engine, _store, _experiment, candidate, _rows = _scenario(tmp_path, extra_regression="hold-out")
        ok, errors, _context = engine.validate_eligibility(candidate.version_id, "evidence_v2_target")
        assert not ok, errors
        assert any("hold-out" in item and "regression" in item for item in errors), errors

    def test_the_full_set_of_required_suites_is_eligible(self, tmp_path: Path) -> None:
        engine, _store, _experiment, candidate, _rows = _scenario(tmp_path)
        ok, errors, _context = engine.validate_eligibility(candidate.version_id, "evidence_v2_target")
        assert ok, errors
        assert errors == []

    def test_v1_evidence_is_not_retroactively_invalid(self, tmp_path: Path) -> None:
        # The leg is gated on the evidence carrying v2 metadata, and this is the test that keeps that gate
        # from silently becoming a purge: a candidate measured before the requirement existed stays promotable
        # on the terms it was measured on.
        engine, _store, _experiment, candidate, _rows = _scenario(tmp_path, primary_version="benchmark-v1", drop=tuple(REQUIRED_FOR_PROMOTION))
        ok, errors, _context = engine.validate_eligibility(candidate.version_id, "evidence_v2_target")
        assert ok, errors

    def test_coverage_reports_absence_and_a_result_differently(self) -> None:
        # Both produce a refusal, and an operator reads this to decide whether to benchmark or to reject - so
        # the shape of the answer has to keep the two apart.
        empty = coverage([])
        assert not empty["ok"] and set(empty["missing"]) == set(REQUIRED_FOR_PROMOTION) and empty["regressed"] == []
        worse = coverage([{"decision": "worse", "regression_results": _NO_REGRESSIONS, "reproducibility_metadata": {"suite": "hold-out"}}])
        assert not worse["ok"] and worse["regressed"] == ["hold-out"] and "hold-out" in worse["present"]
        assert worse["missing"] == ["isolation-attestation", "recovery"], worse

    def test_every_suite_the_gate_names_is_a_real_suite(self) -> None:
        assert set(REQUIRED_FOR_PROMOTION) <= set(suite_names()), (REQUIRED_FOR_PROMOTION, suite_names())
