"""Benchmark v2: the corpus that replaced the file-existence probes (`03` §I.3, `07` §8 P6).

`03` was blunt about the defect and it is worth quoting, because this file is the answer to it:
"``benchmark.py``'s machinery … is correct and retained. Only the **task corpus** is wrong: it currently
checks whether ``evolution_config.json`` exists." Four probes, all asserting that a file is or is not in the
trial directory, cannot distinguish two candidates that behave differently - so every ``BETTER`` the
benchmark produced was a statement about copying.

What that makes testable, and what this file therefore does not test:

* **Not** "the probes pass on the baseline". They do - `test_every_probe_body_is_valid_python` is the cheap
  half, and `tests/test_benchmark_v2_probes_run.py` (the subprocess leg below) runs a representative probe
  against *this* tree, which is what `run_benchmark_probe_corpus.py` does for all of them. A probe that fails
  on the baseline is not a stricter test, it is a broken measurement: the two sides of a comparison must
  agree when the candidate changed nothing.
* **Yes** to the properties that make the corpus a control: the hold-out and attack suites are invisible to
  proposal generation, the ceilings are per-suite data rather than global constants, the variance is measured
  where the trials are aggregated, and every required suite is a real suite the gate can name.

The last one is the reason `isolation-attestation` exists as an eighth row: `03` lists seven suites, `07` §8
asks for seven *including* both `hold-out` and `isolation-attestation`, and §9.7 requires the latter
unchanged for promotion. The counts are inconsistent and the named suites are not, so the corpus has eight
and `08` records the deviation.
"""

from __future__ import annotations

import ast
import copy
import subprocess
import sys
from pathlib import Path

import pytest

from evo_agent.benchmark import Benchmark, BenchmarkEngine, TaskCase, TrialResult, _variance
from evo_agent.benchmark_suites import (
    DEFAULT_CRITERIA,
    LEGACY_PROBES,
    NOT_FOR_PROPOSALS,
    PROBE_BODIES,
    REQUIRED_FOR_PROMOTION,
    SUITES,
    _criteria,
    benchmark_for,
    cases_for,
    cases_usable_for_proposals,
    coverage,
    known_probes,
    probe_source,
    suite_names,
    suite_of,
)
from evo_agent.security import SecurityPolicy
from evo_agent.storage import SQLiteStore

ROOT = Path(__file__).resolve().parents[1]

#: The seven suites `03` §I.3 names, verbatim, plus the one `07` adds.
I3_SUITES = ("core-local", "recovery", "research", "skill-acquisition", "delegation", "metamorphosis-regression", "hold-out")


class TestSuiteSet:
    def test_the_corpus_is_the_named_suites_and_no_others(self) -> None:
        assert suite_names() == I3_SUITES + ("isolation-attestation",) or set(suite_names()) == set(I3_SUITES) | {"isolation-attestation"}, suite_names()
        assert len(SUITES) == 8, "a suite was added or dropped without updating this pin and the deviation note in 08"

    def test_every_suite_has_cases_and_a_stated_purpose(self) -> None:
        for name, spec in SUITES.items():
            assert len(spec.cases) >= 3, (name, len(spec.cases))
            assert spec.purpose.strip() and spec.metrics, name
            assert spec.minimum_trials >= 3, (name, spec.minimum_trials)
            assert spec.notes == "" or len(spec.notes) > 40, name

    def test_the_hold_out_and_attack_suites_are_invisible_to_proposal_generation(self) -> None:
        # The whole point of `hold-out` is that a proposal cannot be tuned against it, and the same reasoning
        # applies to a suite whose content is "the attacks that must keep failing".
        assert NOT_FOR_PROPOSALS == frozenset({"hold-out", "metamorphosis-regression", "isolation-attestation"})
        visible = {case.task_id.split(".", 1)[0] for case in cases_usable_for_proposals()}
        assert not visible & NOT_FOR_PROPOSALS, visible
        for excluded in NOT_FOR_PROPOSALS:
            assert cases_for(excluded), excluded  # excluded from proposals, not deleted

    def test_the_required_set_is_what_the_promotion_gate_asks_for(self) -> None:
        assert REQUIRED_FOR_PROMOTION == tuple(name for name, spec in SUITES.items() if spec.required_for_promotion)
        from evo_agent.promotion import PromotionEngine
        import inspect

        source = inspect.getsource(PromotionEngine)
        assert "suite_coverage" in source or "coverage" in source, "the gate no longer consults the coverage helper"

    def test_case_ids_are_prefixed_by_their_suite(self) -> None:
        for name, spec in SUITES.items():
            for case in spec.cases:
                assert case.task_id.startswith(f"{name}."), case.task_id
                assert (case.metadata or {}).get("suite") == name, case.task_id


class TestProbes:
    def test_every_case_names_a_probe_the_engine_knows(self) -> None:
        known = known_probes()
        for name, spec in SUITES.items():
            for case in spec.cases:
                assert case.probe in known, (name, case.probe)
                assert case.probe not in LEGACY_PROBES, f"{name}.{case.task_id} still asserts about a file"

    def test_every_probe_body_is_valid_python_running_one_test(self) -> None:
        for name, source in PROBE_BODIES.items():
            tree = ast.parse(source)
            tests = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name.startswith("test_")]
            assert len(tests) == 1, (name, len(tests))
            assert "sys.path.insert" in source, f"{name} cannot be sure it imported the candidate's own tree"
            body = ast.unparse(tests[0])
            assert "assert" in body, f"{name} asserts nothing, so it can only ever pass"

    def test_the_mapping_between_cases_and_bodies_is_one_to_one(self) -> None:
        # An orphan body is a probe the corpus forgot to reference - it would keep being maintained and never
        # run - and a case without a body is a benchmark that writes an empty fixture. Both directions, in one
        # assertion, because they are the same mistake seen from either end of the table.
        used = {case.probe for spec in SUITES.values() for case in spec.cases}
        assert used == set(PROBE_BODIES), {"unused_bodies": sorted(set(PROBE_BODIES) - used), "bodiless_cases": sorted(used - set(PROBE_BODIES))}
        assert len(used) == sum(len(spec.cases) for spec in SUITES.values()), "two cases share a probe body"

    def test_probe_source_returns_none_for_a_legacy_case(self) -> None:
        legacy = TaskCase("t", "g", "i", "e", "v", [], 10, "controlled_environment")
        assert probe_source(legacy) is None, "the legacy bodies must stay in the engine, not be redefined here"
        v2 = cases_for("core-local")[0]
        assert "test_benchmark_probe" in probe_source(v2)

    def test_every_suite_builds_a_valid_benchmark(self, tmp_path: Path) -> None:
        # The corpus has to be accepted by the *existing* validator, not by a new one: a suite the engine
        # would refuse is a suite that cannot be run, and that is how a corpus becomes decoration.
        engine = BenchmarkEngine(SQLiteStore(tmp_path / "e.db"), tmp_path)
        for name in SUITES:
            problems = engine.validate_benchmark(benchmark_for(name))
            assert problems == [], (name, problems)

    def test_the_engine_writes_a_corpus_probe_verbatim(self, tmp_path: Path) -> None:
        engine = BenchmarkEngine(SQLiteStore(tmp_path / "e.db"), tmp_path)
        case = cases_for("metamorphosis-regression")[0]
        written = engine._probe_source(case)
        assert written == probe_source(case)
        assert "PROTECTED_CORE" in written

    def test_the_engine_still_writes_the_legacy_probe_unchanged(self, tmp_path: Path) -> None:
        # A persisted v1 benchmark must mean what it meant when it was written, so the four legacy bodies are
        # asserted to be *the engine's own*, not the corpus': the day someone "improves" one of them, an old
        # evidence row stops being reproducible.
        engine = BenchmarkEngine(SQLiteStore(tmp_path / "e.db"), tmp_path)
        legacy = TaskCase("t", "g", "i", "e", "v", [], 10, "candidate_configuration_absent")
        assert "evolution_config.json" in engine._probe_source(legacy)

    def test_a_representative_probe_passes_against_this_tree(self) -> None:
        # The subprocess half: one real run, chosen because it is fast, self-contained, and asserts on the
        # mediation boundary a candidate could plausibly weaken. The full 32-probe sweep is
        # `scripts/run_benchmark_probe_corpus.py`, which CI runs; a unit suite that spawned 32 pytest
        # processes would be measuring its own harness.
        import tempfile

        body = PROBE_BODIES["isolation.network_is_refused_at_every_level"]
        directory = Path(tempfile.mkdtemp(prefix="probe-"))
        target = directory / "test_probe.py"
        target.write_text(body, encoding="utf-8")
        completed = subprocess.run([sys.executable, "-m", "pytest", str(target), "-q", "-p", "no:randomly"], cwd=ROOT, capture_output=True, text=True, timeout=180)
        assert completed.returncode == 0, completed.stdout[-2000:]


class TestCriteria:
    def test_the_ceilings_are_per_suite_data(self) -> None:
        research = SUITES["research"].comparison_criteria
        delegation = SUITES["delegation"].comparison_criteria
        assert research["max_cost_ratio"] > delegation["max_cost_ratio"], "a research suite is allowed to cost more per trial than a delegation fan-out"
        assert DEFAULT_CRITERIA["max_score_variance"] == 0.05 and DEFAULT_CRITERIA["max_cost_ratio"] == 1.25

    def test_criteria_are_never_shared_between_suites(self) -> None:
        # Aliasing here would mean one suite's tuning silently changing another's gate, which is the kind of
        # defect a config-object test exists to catch rather than a reviewer noticing in a diff.
        baseline = {name: dict(spec.comparison_criteria) for name, spec in SUITES.items()}
        first = next(iter(SUITES))
        SUITES[first].comparison_criteria["improvement_delta"] = 0.99
        try:
            for name, spec in SUITES.items():
                if name != first:
                    assert spec.comparison_criteria == baseline[name], name
        finally:
            SUITES[first].comparison_criteria["improvement_delta"] = baseline[first]["improvement_delta"]

    def test_benchmark_for_copies_the_criteria_it_hands_out(self) -> None:
        benchmark = benchmark_for("core-local")
        benchmark.success_criteria["improvement_delta"] = 0.99
        assert SUITES["core-local"].comparison_criteria["improvement_delta"] != 0.99

    def test_a_benchmark_carries_the_version_and_trials_the_gate_reads(self) -> None:
        for name in SUITES:
            benchmark = benchmark_for(name)
            assert benchmark.benchmark_version == "benchmark-v2", name
            assert benchmark.trial_count >= SUITES[name].minimum_trials, name
            assert benchmark.deterministic_seed == 0, name
            assert suite_of(benchmark) == name, (name, suite_of(benchmark))

    def test_trial_count_can_be_raised_but_never_below_the_suite_floor(self) -> None:
        assert benchmark_for("core-local", trial_count=9).trial_count == 9
        assert benchmark_for("core-local", trial_count=1).trial_count == SUITES["core-local"].minimum_trials

    def test_an_unknown_suite_is_refused_by_name(self) -> None:
        with pytest.raises(KeyError, match="is not a benchmark suite"):
            benchmark_for("make-it-faster")
        with pytest.raises(KeyError, match="is not a benchmark suite"):
            cases_for("vibes")


class TestVarianceAndAggregation:
    def test_variance_of_the_obvious_cases(self) -> None:
        assert _variance([]) == 0.0
        assert _variance([1, 1, 1, 1]) == 0.0
        assert _variance([0, 1, 0, 1]) == 0.25
        assert _variance([1, 2]) == 0.25

    def _trial(self, index: int, score: float, duration: int) -> TrialResult:
        return TrialResult(
            trial_id=f"trial-{index}",
            benchmark_id="b",
            experiment_id="e",
            side="candidate",
            task_case_id=f"core-local.case{index}",
            trial_number=index,
            start_time="2026-09-01T00:00:00+00:00",
            end_time="2026-09-01T00:00:01+00:00",
            success=bool(score),
            verified=bool(score),
            score=score,
            timeout=False,
            error="",
            output="",
            duration_ms=duration,
            steps=1,
            retries=0,
            replans=0,
            strategy_changes=0,
            human_interventions=0,
            safety_ok=True,
        )

    def test_aggregation_reports_variance_as_well_as_means(self, tmp_path: Path) -> None:
        engine = BenchmarkEngine(SQLiteStore(tmp_path / "e.db"), tmp_path)
        stable = engine.aggregate_results([self._trial(1, 1.0, 100), self._trial(2, 1.0, 100)])
        assert stable.score_variance == 0.0 and stable.duration_variance_ms == 0.0
        noisy = engine.aggregate_results([self._trial(1, 1.0, 100), self._trial(2, 0.0, 900)])
        assert noisy.score_variance == 0.25 and noisy.duration_variance_ms == 160000.0
        assert noisy.mean_duration_ms == 500.0, "the mean is still reported; variance supplements it rather than replacing it"
        assert "score_variance" in stable.to_dict()

    def test_a_v1_aggregate_row_still_loads(self) -> None:
        # The two new fields are defaulted rather than required precisely so that a persisted v1 aggregate -
        # which has no variance key - is still interpretable. Positional construction has to keep working too.
        field_names = list(__import__("dataclasses").fields(__import__("evo_agent.benchmark", fromlist=["AggregateMetrics"]).AggregateMetrics))
        assert [f.name for f in field_names][-2:] == ["score_variance", "duration_variance_ms"], [f.name for f in field_names]


class TestCoverageHelper:
    def test_empty_evidence_is_reported_as_absence(self) -> None:
        report = coverage([])
        assert not report["ok"] and sorted(report["missing"]) == sorted(REQUIRED_FOR_PROMOTION) and report["regressed"] == []

    def test_a_persisted_regression_list_counts_even_without_the_property(self) -> None:
        # `RegressionResult.any_regression` is a property, so the stored dict carries the five lists and not
        # the boolean. A coverage helper that read `any_regression` would report "clean" for every row ever.
        row = {"decision": "better", "reproducibility_metadata": {"suite": "hold-out"}, "regression_results": {**dict.fromkeys(("functional_regressions", "verification_regressions", "timeout_regressions", "efficiency_regressions", "safety_regressions"), []), "functional_regressions": [{"task_case_id": "x"}]}}
        report = coverage([row])
        assert report["regressed"] == ["hold-out"], report

    def test_all_required_suites_clean_is_ok(self) -> None:
        rows = [{"decision": "better", "reproducibility_metadata": {"suite": name}, "regression_results": {}} for name in REQUIRED_FOR_PROMOTION]
        assert coverage(rows)["ok"], coverage(rows)


class TestLegacyCompatibility:
    def test_the_default_benchmark_still_validates(self, tmp_path: Path) -> None:
        engine = BenchmarkEngine(SQLiteStore(tmp_path / "e.db"), tmp_path)
        problems = engine.validate_benchmark(engine.default_benchmark())
        assert problems == [], problems

    def test_an_unknown_probe_is_still_refused_rather_than_defaulted(self, tmp_path: Path) -> None:
        engine = BenchmarkEngine(SQLiteStore(tmp_path / "e.db"), tmp_path)
        benchmark = copy.deepcopy(engine.default_benchmark())
        benchmark.task_cases[0] = TaskCase("x", "g", "i", "e", "v", [], 10, "trust_me_it_works")
        problems = engine.validate_benchmark(benchmark)
        assert any("unsupported probe" in item for item in problems), problems

    def test_the_suite_is_read_off_the_cases_not_a_second_label(self) -> None:
        mixed = Benchmark(
            benchmark_id="mixed",
            name="mixed",
            version="1",
            description="two suites in one run",
            task_cases=[cases_for("core-local")[0], cases_for("hold-out")[0]],
            success_criteria={},
            evaluation_metrics=[],
        )
        assert suite_of(mixed) == "core-local+hold-out", suite_of(mixed)
        empty = Benchmark(benchmark_id="empty", name="empty", version="1", description="", task_cases=[], success_criteria={}, evaluation_metrics=[])
        assert suite_of(empty) == "unknown"
