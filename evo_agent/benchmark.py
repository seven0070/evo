from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import signal
import shutil
import subprocess
from typing import Any, Iterable

from .evolver import Evolver
from .models import ComparisonClass, Event, EventType, ExperimentStatus, ProposalStatus
from .storage import SQLiteStore
from .version import __version__


@dataclass
class TaskCase:
    task_id: str
    goal: str
    input: str
    expected_behavior: str
    verification_method: str
    allowed_tools: list[str]
    timeout: int
    probe: str
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Benchmark:
    benchmark_id: str
    name: str
    version: str
    description: str
    task_cases: list[TaskCase]
    success_criteria: dict[str, Any]
    evaluation_metrics: list[str]
    trial_count: int = 3
    timeout: int = 30
    deterministic_seed: int | None = 0
    benchmark_version: str = "benchmark-v1"

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["task_cases"] = [case.to_dict() for case in self.task_cases]
        return data


@dataclass
class TrialResult:
    trial_id: str
    benchmark_id: str
    experiment_id: str
    side: str
    task_case_id: str
    trial_number: int
    start_time: str
    end_time: str
    success: bool
    verified: bool
    score: float
    timeout: bool
    error: str
    output: str
    duration_ms: int
    steps: int
    retries: int
    replans: int
    strategy_changes: int
    human_interventions: int
    safety_ok: bool
    metrics: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class AggregateMetrics:
    total_trials: int
    successful_trials: int
    verified_trials: int
    failed_trials: int
    timeout_trials: int
    success_rate: float
    verification_rate: float
    failure_rate: float
    timeout_rate: float
    mean_score: float
    mean_duration_ms: float
    mean_steps: float
    mean_retries: float
    mean_replans: float
    mean_strategy_changes: float
    recovery_attempts: int
    successful_recoveries: int
    human_interventions: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class RegressionResult:
    functional_regressions: list[dict[str, Any]]
    verification_regressions: list[dict[str, Any]]
    timeout_regressions: list[dict[str, Any]]
    efficiency_regressions: list[dict[str, Any]]
    safety_regressions: list[dict[str, Any]]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @property
    def any_regression(self) -> bool:
        return any(asdict(self).values())


@dataclass
class EvolutionEvidence:
    evidence_id: str
    experiment_id: str
    proposal_id: str
    benchmark_id: str
    baseline_version: str
    candidate_version: str
    trial_count: int
    baseline_metrics: dict[str, Any]
    candidate_metrics: dict[str, Any]
    metric_differences: dict[str, Any]
    regression_results: dict[str, Any]
    safety_results: dict[str, Any]
    target_improvement: bool | None
    decision: ComparisonClass
    decision_reason: list[str]
    benchmark_version: str
    evaluator_version: str
    created_at: str
    reproducibility_metadata: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["decision"] = self.decision.value
        return data


class BenchmarkEngine:
    """Compares an existing isolated candidate and baseline without promoting either."""

    VALID_PROBES = {"controlled_environment", "candidate_configuration_present", "candidate_configuration_absent", "always_pass", "always_fail"}
    SECRET_KEYS = {"OPENAI_API_KEY", "ANTHROPIC_API_KEY", "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "SSH_AUTH_SOCK"}

    def __init__(self, store: SQLiteStore, source_root: Path, agent_version: str = __version__, evaluator_version: str = "benchmark-evaluator-v1"):
        self.store = store
        self.source_root = Path(source_root).expanduser().resolve()
        self.agent_version = agent_version
        self.evaluator_version = evaluator_version

    @staticmethod
    def default_benchmark(benchmark_id: str = "benchmark-core-v1") -> Benchmark:
        return Benchmark(
            benchmark_id=benchmark_id,
            name="Core isolated-agent benchmark",
            version="1.0.0",
            description="Small deterministic benchmark for candidate isolation, structured configuration, verification, and controlled failure behavior.",
            task_cases=[
                TaskCase("simple-environment", "Run a simple isolated check", "none", "The isolated test environment is usable.", "Fixed controlled pytest probe passes.", ["pytest"], 10, "controlled_environment"),
                TaskCase("candidate-configuration", "Verify a candidate configuration exists", "none", "The candidate receives the approved structured configuration.", "Candidate configuration probe passes.", ["pytest"], 10, "candidate_configuration_present"),
                TaskCase("verification", "Verify equivalent baseline behavior remains valid", "none", "The controlled environment probe passes for both versions.", "Fixed controlled pytest probe passes.", ["pytest"], 10, "controlled_environment"),
            ],
            success_criteria={"target_metric": "success_rate", "improvement_delta": 0.10, "regression_tolerance": 0.0, "minimum_verification_rate": 1.0},
            evaluation_metrics=["success_rate", "verification_rate", "mean_score", "reliability", "efficiency", "recovery", "timeout_rate", "regressions"],
            trial_count=3,
            timeout=30,
        )

    def validate_benchmark(self, benchmark: Benchmark) -> list[str]:
        errors: list[str] = []
        if not benchmark.benchmark_id.strip() or not benchmark.version.strip():
            errors.append("benchmark_id and version are required")
        if benchmark.trial_count < 1 or benchmark.trial_count > 100:
            errors.append("trial_count must be between 1 and 100")
        if benchmark.timeout < 1 or benchmark.timeout > 3600:
            errors.append("timeout must be between 1 and 3600 seconds")
        if not benchmark.task_cases:
            errors.append("at least one task case is required")
        for case in benchmark.task_cases:
            if not case.task_id or not case.goal or not case.expected_behavior or not case.verification_method:
                errors.append(f"task case {case.task_id or '<missing>'} is incomplete")
            if case.probe not in self.VALID_PROBES:
                errors.append(f"task case {case.task_id} uses unsupported probe {case.probe}")
            if case.timeout < 1 or case.timeout > benchmark.timeout:
                errors.append(f"task case {case.task_id} timeout must be within benchmark timeout")
            if any(token in case.goal.lower() for token in ("permission bypass", "disable verification", "escape workspace", "modify production")):
                errors.append(f"task case {case.task_id} requests a protected behavior")
        return errors

    def save_benchmark(self, benchmark: Benchmark) -> None:
        errors = self.validate_benchmark(benchmark)
        if errors:
            raise ValueError("Invalid benchmark: " + "; ".join(errors))
        self.store.save_benchmark(benchmark)
        self.store.append_event(Event("benchmark", EventType.BENCHMARK_CREATED, {"benchmark_id": benchmark.benchmark_id, "benchmark_version": benchmark.benchmark_version}))
        self.store.append_event(Event("benchmark", EventType.BENCHMARK_VALIDATED, {"benchmark_id": benchmark.benchmark_id}))

    def load_benchmark(self, benchmark_id: str) -> Benchmark | None:
        record = self.store.benchmark_by_id(benchmark_id)
        if not record:
            return None
        payload = json.loads(record["payload"]) if isinstance(record.get("payload"), str) else record["payload"]
        payload["task_cases"] = [TaskCase(**case) for case in payload["task_cases"]]
        return Benchmark(**payload)

    def list_benchmarks(self, limit: int = 50) -> list[Benchmark]:
        result: list[Benchmark] = []
        for record in self.store.find_benchmarks(limit):
            payload = json.loads(record["payload"])
            payload["task_cases"] = [TaskCase(**case) for case in payload["task_cases"]]
            result.append(Benchmark(**payload))
        return result

    def run(self, benchmark_id: str, experiment_id: str) -> EvolutionEvidence:
        benchmark = self.load_benchmark(benchmark_id)
        if not benchmark:
            raise KeyError(f"Benchmark not found: {benchmark_id}")
        experiment_record = self.store.experiment_by_id(experiment_id)
        if not experiment_record:
            raise PermissionError("Experiment does not exist")
        experiment = json.loads(experiment_record["payload"])
        experiment["status"] = experiment_record["status"]
        experiment["cleanup_status"] = experiment_record["cleanup_status"]
        proposal_id = str(experiment["proposal_id"])
        proposal_record = self.store.proposal_by_id(proposal_id)
        if not proposal_record:
            raise PermissionError("Experiment proposal does not exist")
        proposal = json.loads(proposal_record["payload"])
        errors = self._validate_eligibility(experiment, proposal, benchmark)
        if errors:
            evidence = self._inconclusive_evidence(benchmark, experiment, proposal, errors)
            self._persist_evidence(evidence)
            return evidence

        sandbox_location = Path(experiment["sandbox_location"]).resolve()
        baseline_dir = sandbox_location / "baseline"
        candidate_dir = sandbox_location / "candidate"
        production_hash_before = self._manifest_hash(self.source_root)
        self.store.append_event(Event("benchmark", EventType.BENCHMARK_STARTED, self._event_context(benchmark, experiment, proposal)))
        baseline_trials: list[TrialResult] = []
        candidate_trials: list[TrialResult] = []
        for case in benchmark.task_cases:
            fixture = self.prepare_trial(benchmark, experiment, case)
            for trial_number in range(1, benchmark.trial_count + 1):
                baseline_trials.append(self.run_baseline(benchmark, experiment, case, fixture, baseline_dir, trial_number))
                candidate_trials.append(self.run_candidate(benchmark, experiment, case, fixture, candidate_dir, trial_number))
        baseline_metrics = self.aggregate_results(baseline_trials).to_dict()
        candidate_metrics = self.aggregate_results(candidate_trials).to_dict()
        regressions = self.detect_regressions(baseline_trials, candidate_trials)
        production_unchanged = production_hash_before == self._manifest_hash(self.source_root)
        safety_results = self.evaluate_safety(baseline_trials, candidate_trials, experiment, production_unchanged)
        for item in regressions.functional_regressions + regressions.verification_regressions + regressions.timeout_regressions + regressions.efficiency_regressions:
            self.store.append_event(Event("benchmark", EventType.REGRESSION_DETECTED, {"benchmark_id": benchmark.benchmark_id, "experiment_id": experiment["experiment_id"], "proposal_id": proposal_id, "candidate_id": experiment["candidate_id"], "regression": item}))
        for item in regressions.safety_regressions:
            self.store.append_event(Event("benchmark", EventType.SAFETY_REGRESSION_DETECTED, {"benchmark_id": benchmark.benchmark_id, "experiment_id": experiment["experiment_id"], "proposal_id": proposal_id, "candidate_id": experiment["candidate_id"], "regression": item}))
        evidence = self.produce_evidence(benchmark, experiment, proposal, baseline_metrics, candidate_metrics, regressions, safety_results, baseline_trials, candidate_trials)
        self._persist_evidence(evidence)
        self.store.append_event(Event("benchmark", EventType.BENCHMARK_COMPLETED, {"benchmark_id": benchmark.benchmark_id, "experiment_id": experiment["experiment_id"], "proposal_id": proposal_id, "candidate_id": experiment["candidate_id"], "decision": evidence.decision.value}))
        return evidence

    def prepare_trial(self, benchmark: Benchmark, experiment: dict[str, Any], case: TaskCase) -> Path:
        path = Path(experiment["sandbox_location"]).resolve() / "metadata" / "benchmark_cases"
        path.mkdir(parents=True, exist_ok=True)
        fixture = path / f"{benchmark.benchmark_id}_{case.task_id}.py"
        fixture.write_text(self._probe_source(case), encoding="utf-8")
        return fixture

    def run_baseline(self, benchmark: Benchmark, experiment: dict[str, Any], case: TaskCase, fixture: Path, baseline_dir: Path, trial_number: int) -> TrialResult:
        return self._run_trial(benchmark, experiment, case, fixture, baseline_dir, "baseline", trial_number)

    def run_candidate(self, benchmark: Benchmark, experiment: dict[str, Any], case: TaskCase, fixture: Path, candidate_dir: Path, trial_number: int) -> TrialResult:
        return self._run_trial(benchmark, experiment, case, fixture, candidate_dir, "candidate", trial_number)

    def collect_results(self, trials: Iterable[TrialResult]) -> dict[str, Any]:
        return {"trials": [trial.to_dict() for trial in trials]}

    def aggregate_results(self, trials: Iterable[TrialResult]) -> AggregateMetrics:
        records = list(trials)
        total = len(records)
        successful = sum(item.success for item in records)
        verified = sum(item.verified for item in records)
        failed = sum(not item.success for item in records)
        timeouts = sum(item.timeout for item in records)
        divisor = max(total, 1)
        return AggregateMetrics(
            total_trials=total,
            successful_trials=successful,
            verified_trials=verified,
            failed_trials=failed,
            timeout_trials=timeouts,
            success_rate=round(successful / divisor, 4),
            verification_rate=round(verified / divisor, 4),
            failure_rate=round(failed / divisor, 4),
            timeout_rate=round(timeouts / divisor, 4),
            mean_score=round(sum(item.score for item in records) / divisor, 4),
            mean_duration_ms=round(sum(item.duration_ms for item in records) / divisor, 4),
            mean_steps=round(sum(item.steps for item in records) / divisor, 4),
            mean_retries=round(sum(item.retries for item in records) / divisor, 4),
            mean_replans=round(sum(item.replans for item in records) / divisor, 4),
            mean_strategy_changes=round(sum(item.strategy_changes for item in records) / divisor, 4),
            recovery_attempts=sum(item.metrics.get("recovery_attempts", 0) for item in records),
            successful_recoveries=sum(item.metrics.get("successful_recoveries", 0) for item in records),
            human_interventions=sum(item.human_interventions for item in records),
        )

    def detect_regressions(self, baseline: list[TrialResult], candidate: list[TrialResult]) -> RegressionResult:
        by_key = lambda items: {(item.task_case_id, item.trial_number): item for item in items}
        base = by_key(baseline)
        cand = by_key(candidate)
        functional: list[dict[str, Any]] = []
        verification: list[dict[str, Any]] = []
        timeout: list[dict[str, Any]] = []
        efficiency: list[dict[str, Any]] = []
        safety: list[dict[str, Any]] = []
        for key, before in base.items():
            after = cand.get(key)
            if not after:
                functional.append({"task_case_id": key[0], "trial_number": key[1], "reason": "candidate result missing"})
                continue
            if before.success and not after.success:
                functional.append({"task_case_id": key[0], "trial_number": key[1], "reason": "baseline success, candidate failure"})
            if before.verified and not after.verified:
                verification.append({"task_case_id": key[0], "trial_number": key[1], "reason": "baseline verified, candidate unverified"})
            if not before.timeout and after.timeout:
                timeout.append({"task_case_id": key[0], "trial_number": key[1], "reason": "baseline within timeout, candidate timeout"})
            if before.success and after.success and after.duration_ms > max(before.duration_ms * 2, before.duration_ms + 1000):
                efficiency.append({"task_case_id": key[0], "trial_number": key[1], "reason": "candidate duration materially increased"})
            if not after.safety_ok:
                safety.append({"task_case_id": key[0], "trial_number": key[1], "reason": "candidate safety check failed"})
        return RegressionResult(functional, verification, timeout, efficiency, safety)

    def evaluate_safety(self, baseline: list[TrialResult], candidate: list[TrialResult], experiment: dict[str, Any], production_unchanged: bool) -> dict[str, Any]:
        candidate_dir = Path(experiment["sandbox_location"]) / "candidate"
        baseline_dir = Path(experiment["sandbox_location"]) / "baseline"
        return {
            "production_unchanged": production_unchanged,
            "candidate_isolated": candidate_dir.resolve() != self.source_root and baseline_dir.resolve() != self.source_root,
            "network_denied": all(item.metrics.get("network_policy") == "denied" for item in candidate + baseline),
            "host_secrets_absent": all(not self.SECRET_KEYS.intersection(set(item.metrics.get("environment_keys", []))) for item in candidate + baseline),
            "bounded_commands": all(item.metrics.get("fixed_runner") is True for item in candidate + baseline),
            "candidate_safety_ok": all(item.safety_ok for item in candidate),
        }

    def compare_with_baseline(self, baseline_metrics: dict[str, Any], candidate_metrics: dict[str, Any], regressions: RegressionResult | None = None, safety_ok: bool = True, criteria: dict[str, Any] | None = None) -> ComparisonClass:
        criteria = criteria or {"target_metric": "success_rate", "improvement_delta": 0.10, "regression_tolerance": 0.0, "minimum_verification_rate": 1.0}
        regressions = regressions or RegressionResult([], [], [], [], [])
        target_metric = str(criteria.get("target_metric", "success_rate"))
        delta = float(criteria.get("improvement_delta", 0.10))
        difference = float(candidate_metrics.get(target_metric, 0)) - float(baseline_metrics.get(target_metric, 0))
        if not safety_ok or regressions.any_regression:
            return ComparisonClass.WORSE
        if candidate_metrics.get("verification_rate", 0) < float(criteria.get("minimum_verification_rate", 1.0)):
            return ComparisonClass.WORSE
        if difference >= delta:
            return ComparisonClass.BETTER
        if abs(difference) <= float(criteria.get("regression_tolerance", 0.0)):
            return ComparisonClass.NO_CHANGE
        if difference < 0:
            return ComparisonClass.WORSE
        return ComparisonClass.INCONCLUSIVE

    def produce_evidence(self, benchmark: Benchmark, experiment: dict[str, Any], proposal: dict[str, Any], baseline_metrics: dict[str, Any], candidate_metrics: dict[str, Any], regressions: RegressionResult, safety_results: dict[str, Any], baseline_trials: list[TrialResult], candidate_trials: list[TrialResult]) -> EvolutionEvidence:
        differences = {key: round(candidate_metrics.get(key, 0) - baseline_metrics.get(key, 0), 4) for key in ("success_rate", "verification_rate", "mean_score", "failure_rate", "timeout_rate", "mean_duration_ms", "mean_retries", "mean_replans", "mean_strategy_changes")}
        criteria = benchmark.success_criteria
        target_metric = str(criteria.get("target_metric", "success_rate"))
        target_delta = float(criteria.get("improvement_delta", 0.10))
        target_improvement = candidate_metrics.get(target_metric, 0) >= baseline_metrics.get(target_metric, 0) + target_delta
        reasons: list[str] = [f"target metric {target_metric} changed from {baseline_metrics.get(target_metric, 0)} to {candidate_metrics.get(target_metric, 0)}"]
        hard_safety_failure = not all(bool(value) for value in safety_results.values())
        decision = self.compare_with_baseline(baseline_metrics, candidate_metrics, regressions, not hard_safety_failure, criteria)
        if hard_safety_failure:
            reasons.append("safety gate failed; safety regressions prevent BETTER")
        elif regressions.any_regression:
            reasons.append("functional, verification, timeout, efficiency, or safety regression detected")
        elif candidate_metrics.get("verification_rate", 0) < float(criteria.get("minimum_verification_rate", 1.0)):
            reasons.append("candidate verification rate is below the required minimum")
        elif target_improvement:
            reasons.append(f"target metric improved by at least {target_delta}")
        elif abs(differences.get(target_metric, 0)) <= float(criteria.get("regression_tolerance", 0.0)):
            reasons.append("target metric is effectively unchanged")
        elif candidate_metrics.get(target_metric, 0) < baseline_metrics.get(target_metric, 0):
            reasons.append("target metric declined")
        else:
            reasons.append("evidence is insufficient for a deterministic improvement decision")
        now = datetime.now(timezone.utc).isoformat()
        return EvolutionEvidence(
            evidence_id=f"evidence_{experiment['experiment_id']}_{benchmark.benchmark_id}",
            experiment_id=experiment["experiment_id"],
            proposal_id=experiment["proposal_id"],
            benchmark_id=benchmark.benchmark_id,
            baseline_version=experiment["baseline_version"],
            candidate_version=experiment["candidate_version"],
            trial_count=len(baseline_trials) + len(candidate_trials),
            baseline_metrics=baseline_metrics,
            candidate_metrics=candidate_metrics,
            metric_differences=differences,
            regression_results=regressions.to_dict(),
            safety_results=safety_results,
            target_improvement=target_improvement,
            decision=decision,
            decision_reason=reasons,
            benchmark_version=benchmark.benchmark_version,
            evaluator_version=self.evaluator_version,
            created_at=now,
            reproducibility_metadata={"benchmark_version": benchmark.version, "benchmark_id": benchmark.benchmark_id, "trial_count_per_side": benchmark.trial_count, "deterministic_seed": benchmark.deterministic_seed, "source_commit": experiment.get("candidate", {}).get("source_commit", "unknown"), "baseline_version": experiment["baseline_version"], "candidate_version": experiment["candidate_version"], "timeout": benchmark.timeout, "evaluation_rules": benchmark.evaluation_metrics, "sandbox_policy": experiment.get("isolation_policy", {})},
        )

    def _run_trial(self, benchmark: Benchmark, experiment: dict[str, Any], case: TaskCase, fixture: Path, location: Path, side: str, trial_number: int) -> TrialResult:
        trial_id = f"trial_{benchmark.benchmark_id}_{experiment['experiment_id']}_{side}_{case.task_id}_{trial_number}"
        start = datetime.now(timezone.utc)
        self.store.append_event(Event("benchmark", EventType.TRIAL_STARTED, {"benchmark_id": benchmark.benchmark_id, "experiment_id": experiment["experiment_id"], "proposal_id": experiment["proposal_id"], "candidate_id": experiment["candidate_id"], "version": experiment["candidate_version"] if side == "candidate" else experiment["baseline_version"], "trial_id": trial_id, "task_case_id": case.task_id, "trial_number": trial_number}))
        command = ["python3", "-m", "pytest", "-q", "-p", "no:cacheprovider", str(fixture)]
        env = self._sanitized_environment(experiment)
        output = ""
        error = ""
        return_code: int | None = None
        timed_out = False
        process = None
        try:
            process = subprocess.Popen(self._isolated_command(location, command), cwd=location, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, start_new_session=True)
            try:
                output, _ = process.communicate(timeout=min(benchmark.timeout, case.timeout))
                return_code = process.returncode
            except subprocess.TimeoutExpired:
                timed_out = True
                error = f"trial exceeded {min(benchmark.timeout, case.timeout)}s timeout"
                self._terminate_process(process)
                output, _ = process.communicate()
                return_code = process.returncode
        except Exception as exc:
            error = str(exc)
        end = datetime.now(timezone.utc)
        duration = max(0, int((end - start).total_seconds() * 1000))
        success = not timed_out and return_code == 0
        verified = success
        safety_ok = not timed_out and not error
        metrics = {"network_policy": "denied", "environment_keys": sorted(env.keys()), "fixed_runner": True, "recovery_attempts": 0, "successful_recoveries": 0}
        trial = TrialResult(trial_id, benchmark.benchmark_id, experiment["experiment_id"], side, case.task_id, trial_number, start.isoformat(), end.isoformat(), success, verified, 100.0 if success else 0.0, timed_out, error, output, duration, 1, 0, 0, 0, 0, safety_ok, metrics)
        self.store.save_benchmark_trial(trial)
        context = {"benchmark_id": benchmark.benchmark_id, "experiment_id": experiment["experiment_id"], "proposal_id": experiment["proposal_id"], "candidate_id": experiment["candidate_id"], "version": experiment["candidate_version"] if side == "candidate" else experiment["baseline_version"], "trial": trial.to_dict()}
        self.store.append_event(Event("benchmark", EventType.TRIAL_COMPLETED, context))
        self.store.append_event(Event("benchmark", EventType.CANDIDATE_COMPLETED if side == "candidate" else EventType.BASELINE_COMPLETED, context))
        return trial

    def _validate_eligibility(self, experiment: dict[str, Any], proposal: dict[str, Any], benchmark: Benchmark) -> list[str]:
        errors: list[str] = []
        if proposal.get("status") != ProposalStatus.APPROVED.value:
            errors.append("proposal is not approved")
        if experiment.get("status") not in {ExperimentStatus.CREATED.value, ExperimentStatus.PREPARED.value, ExperimentStatus.PASSED.value}:
            errors.append("experiment is not eligible")
        sandbox = Path(experiment.get("sandbox_location", ""))
        if not sandbox.is_dir() or not (sandbox / "baseline").is_dir() or not (sandbox / "candidate").is_dir():
            errors.append("sandbox baseline/candidate directories are unavailable")
        if not experiment.get("candidate_id") or not experiment.get("candidate_version"):
            errors.append("candidate version metadata is missing")
        errors.extend(self.validate_benchmark(benchmark))
        return errors

    def _inconclusive_evidence(self, benchmark: Benchmark, experiment: dict[str, Any], proposal: dict[str, Any], errors: list[str]) -> EvolutionEvidence:
        now = datetime.now(timezone.utc).isoformat()
        return EvolutionEvidence(f"evidence_{experiment['experiment_id']}_{benchmark.benchmark_id}", experiment["experiment_id"], experiment["proposal_id"], benchmark.benchmark_id, experiment.get("baseline_version", "unknown"), experiment.get("candidate_version", "unknown"), 0, {}, {}, {}, {"functional_regressions": [], "verification_regressions": [], "timeout_regressions": [], "efficiency_regressions": [], "safety_regressions": []}, {"eligibility": False}, None, ComparisonClass.INCONCLUSIVE, errors, benchmark.benchmark_version, self.evaluator_version, now, {"eligibility_errors": errors, "benchmark_id": benchmark.benchmark_id, "benchmark_version": benchmark.version})

    def _persist_evidence(self, evidence: EvolutionEvidence) -> None:
        self.store.save_evolution_evidence(evidence)
        self.store.append_event(Event("benchmark", EventType.EVIDENCE_GENERATED, {"evidence_id": evidence.evidence_id, "experiment_id": evidence.experiment_id, "proposal_id": evidence.proposal_id, "benchmark_id": evidence.benchmark_id, "decision": evidence.decision.value}))
        self.store.append_event(Event("benchmark", EventType.EVOLUTION_DECISION_MADE, {"evidence_id": evidence.evidence_id, "decision": evidence.decision.value, "reason": evidence.decision_reason}))

    @staticmethod
    def _probe_source(case: TaskCase) -> str:
        if case.probe == "controlled_environment":
            body = "assert Path.cwd().name in {'baseline', 'candidate'}"
        elif case.probe == "candidate_configuration_present":
            body = "assert (Path.cwd() / 'evolution_config.json').exists()"
        elif case.probe == "candidate_configuration_absent":
            body = "assert not (Path.cwd() / 'evolution_config.json').exists()"
        elif case.probe == "always_fail":
            body = "assert False"
        else:
            body = "assert True"
        return "from pathlib import Path\ndef test_benchmark_probe():\n    " + body + "\n"

    @staticmethod
    def _bwrap_executable() -> str | None:
        """The bubblewrap to *run*, or None. The same resolution SandboxEngine uses, for the same reason.

        Probing with the absolute path and then exec'ing the bare name means the second lookup happens
        in the child, against a sanitized ``PATH`` - a different lookup than the parent's. Two engines
        duplicating this method is the smaller risk of the two available: the alternative is a shared
        helper that only one of them uses on a bad day, and then a benchmark would confine a binary
        other than the one the experiment confined.
        """
        executable = shutil.which("bwrap")
        if not executable:
            return None
        try:
            probe = subprocess.run(
                [executable, "--die-with-parent", "--unshare-user-try", "--unshare-net", "--unshare-pid", "--ro-bind", "/", "/", "true"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=3,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            return None
        return executable if probe.returncode == 0 else None

    @classmethod
    def _bwrap_usable(cls) -> bool:
        return cls._bwrap_executable() is not None

    def _isolated_command(self, location: Path, command: list[str]) -> list[str]:
        bwrap = self._bwrap_executable()
        if bwrap:
            experiment_dir = location.parent
            results_dir = experiment_dir / "results"
            home_dir = experiment_dir / "metadata" / "home"
            results_dir.mkdir(parents=True, exist_ok=True)
            home_dir.mkdir(parents=True, exist_ok=True)
            args = [
                bwrap,
                "--die-with-parent",
                "--unshare-user-try",
                "--unshare-net",
                "--unshare-pid",
                "--ro-bind", "/", "/",
                "--dev", "/dev",
                "--proc", "/proc",
                # Host sysfs describes the host's devices and interfaces, which a namespace with no
                # network must not be shown. Same mask SandboxEngine applies, so the benchmark judges
                # the same conditions the experiment measured.
                "--tmpfs", "/sys",
                "--setenv", "HOME", str(home_dir),
                "--setenv", "TMPDIR", str(results_dir),
                "--setenv", "PYTHONNOUSERSITE", "1",
                "--setenv", "PYTHONDONTWRITEBYTECODE", "1",
                "--setenv", "PYTEST_ADDOPTS", "-p no:cacheprovider",
                "--setenv", "NO_PROXY", "*",
                "--setenv", "no_proxy", "*",
                "--setenv", "EVO_NETWORK_POLICY", "denied",
                ("--bind" if location.name == "candidate" else "--ro-bind"), str(location), str(location),
                "--bind", str(results_dir), str(results_dir),
                "--bind", str(home_dir), str(home_dir),
                "--chdir", str(location),
            ]
            return [*args, *command]
        # ``$2`` is the production root, and it was already being passed and then shifted away without
        # anything done to it. Read-only is what SandboxEngine's equivalent branch promises for the same
        # path; a benchmark that lets the candidate write into the tree it is measuring makes the
        # benchmark's own hash check the only thing standing between that write and the next run.
        script = (
            'set -eu; '
            'mount --make-rprivate /; '
            'mount --bind "$2" "$2"; '
            'mount -o remount,bind,ro "$2"; '
            'mount -t sysfs sysfs /sys 2>/dev/null || echo "EVO_SYSFS_NOT_REMOUNTED"; '
            'cd "$1"; shift 2; exec "$@"'
        )
        return ["unshare", "--user", "--map-root-user", "--mount", "--net", "--pid", "--fork", "--mount-proc", "sh", "-c", script, "evo-benchmark", str(location), str(self.source_root), *command]

    @staticmethod
    def _sanitized_environment(experiment: dict[str, Any]) -> dict[str, str]:
        home = Path(experiment["sandbox_location"]) / "metadata" / "home"
        results = Path(experiment["sandbox_location"]) / "results"
        home.mkdir(parents=True, exist_ok=True)
        results.mkdir(parents=True, exist_ok=True)
        return {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": str(home), "TMPDIR": str(results), "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8", "PYTHONNOUSERSITE": "1", "PYTHONDONTWRITEBYTECODE": "1", "PYTEST_ADDOPTS": "-p no:cacheprovider", "NO_PROXY": "*", "no_proxy": "*", "EVO_NETWORK_POLICY": "denied", "EVO_EXPERIMENT_ID": experiment["experiment_id"]}

    @staticmethod
    def _terminate_process(process: subprocess.Popen[str]) -> None:
        try:
            os.killpg(process.pid, signal.SIGTERM)
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass

    @staticmethod
    def _manifest_hash(root: Path) -> str:
        digest = hashlib.sha256()
        for path in sorted(root.rglob("*")):
            if not path.is_file() or any(part in {".git", ".evo", "__pycache__", ".pytest_cache"} for part in path.relative_to(root).parts):
                continue
            digest.update(str(path.relative_to(root)).encode())
            digest.update(path.read_bytes())
        return digest.hexdigest()

    @staticmethod
    def _event_context(benchmark: Benchmark, experiment: dict[str, Any], proposal: dict[str, Any]) -> dict[str, Any]:
        return {"benchmark_id": benchmark.benchmark_id, "experiment_id": experiment["experiment_id"], "proposal_id": proposal["proposal_id"], "candidate_id": experiment["candidate_id"]}
