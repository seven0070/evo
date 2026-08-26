from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import tempfile
from typing import Any, Iterable

from .evolver import Evolver, EvolutionProposal
from .models import CandidateStatus, ComparisonClass, Event, EventType, ExperimentStatus, ProposalRisk, ProposalStatus
from .storage import SQLiteStore
from .version import __version__


@dataclass
class CandidateVersion:
    candidate_id: str
    experiment_id: str
    proposal_id: str
    baseline_version: str
    source_commit: str
    candidate_commit: str
    target_component: str
    proposed_change: str
    created_at: str
    sandbox_path: str
    status: CandidateStatus = CandidateStatus.CREATED

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["status"] = self.status.value
        return data


@dataclass
class ExecutionResult:
    location: str
    command: list[str]
    completed: bool
    timeout: bool
    return_code: int | None
    output: str
    error: str
    tests_run: int
    tests_passed: int
    tests_failed: int
    duration_ms: int | None
    environment_keys: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ComparisonResult:
    classification: ComparisonClass
    rationale: str
    baseline: dict[str, Any]
    candidate: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["classification"] = self.classification.value
        return data


@dataclass
class EvolutionExperiment:
    experiment_id: str
    proposal_id: str
    candidate_id: str
    baseline_version: str
    candidate_version: str
    sandbox_location: str
    start_time: str
    end_time: str | None
    status: ExperimentStatus
    tests_run: int
    tests_passed: int
    tests_failed: int
    timeout: bool
    errors: list[str]
    logs: dict[str, str]
    resource_information: dict[str, Any]
    network_policy: str
    isolation_policy: dict[str, Any]
    cleanup_status: str
    baseline_execution: dict[str, Any] | None = None
    candidate_execution: dict[str, Any] | None = None
    comparison: dict[str, Any] | None = None
    candidate: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["status"] = self.status.value
        return data


class SandboxEngine:
    """Runs approved structured proposals in disposable, non-production directories."""

    SANDBOX_TASK_ID = "sandbox"
    SUPPORTED_TARGETS = {
        "strategy-selection",
        "strategy parameters",
        "tool-selection",
        "retry/recovery configuration",
        "recovery-policy",
        "planning configuration",
        "planning-heuristics",
        "prompt/configuration parameters",
    }
    PROTECTED_TERMS = Evolver.PROTECTED_TERMS

    def __init__(
        self,
        store: SQLiteStore,
        source_root: Path,
        sandbox_root: Path | None = None,
        timeout_seconds: int = 30,
        agent_version: str = __version__,
    ):
        self.store = store
        self.source_root = Path(source_root).expanduser().resolve()
        if not self.source_root.is_dir():
            raise FileNotFoundError(f"Source root does not exist: {self.source_root}")
        self.sandbox_root = (sandbox_root or self.source_root.parent / ".evo-sandboxes").expanduser().resolve()
        if self.sandbox_root == self.source_root or self.sandbox_root.is_relative_to(self.source_root):
            raise ValueError("Sandbox root must be outside the production source root")
        self.sandbox_root.mkdir(parents=True, exist_ok=True)
        self.timeout_seconds = timeout_seconds
        self.agent_version = agent_version

    def create_sandbox(self, proposal_id: str) -> tuple[EvolutionExperiment, EvolutionProposal, Path, Path]:
        proposal = self._require_approved_proposal(proposal_id)
        experiment_id = self._new_id("experiment")
        candidate_id = self._new_id("candidate")
        experiment_dir = self.sandbox_root / experiment_id
        baseline_dir = experiment_dir / "baseline"
        candidate_dir = experiment_dir / "candidate"
        for directory in (baseline_dir, candidate_dir, experiment_dir / "logs", experiment_dir / "results", experiment_dir / "metadata"):
            directory.mkdir(parents=True, exist_ok=False)
        start_time = self._now()
        source_commit = self._git_commit()
        baseline_hash = self._manifest_hash(self.source_root)
        metadata = {
            "experiment_id": experiment_id,
            "proposal_id": proposal_id,
            "candidate_id": candidate_id,
            "agent_version": self.agent_version,
            "source_commit": source_commit,
            "baseline_hash": baseline_hash,
            "created_at": start_time,
            "network_policy": "denied",
            "isolation_policy": self._isolation_policy(),
        }
        (experiment_dir / "metadata" / "experiment.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")
        self._copy_production(self.source_root, baseline_dir, readonly=False)
        self._copy_production(self.source_root, candidate_dir, readonly=False)
        controlled_test = "from pathlib import Path\ndef test_controlled_candidate_environment():\n    assert Path.cwd().name in {'baseline', 'candidate'}\n"
        if not (baseline_dir / "test_sandbox_controlled.py").exists():
            (baseline_dir / "test_sandbox_controlled.py").write_text(controlled_test, encoding="utf-8")
        if not (candidate_dir / "test_sandbox_controlled.py").exists():
            (candidate_dir / "test_sandbox_controlled.py").write_text(controlled_test, encoding="utf-8")
        self._make_readonly(baseline_dir)
        (experiment_dir / "metadata" / "baseline.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")
        candidate = CandidateVersion(
            candidate_id=candidate_id,
            experiment_id=experiment_id,
            proposal_id=proposal_id,
            baseline_version=proposal.agent_version,
            source_commit=source_commit,
            candidate_commit=f"structured-change:{proposal_id}",
            target_component=proposal.target_component,
            proposed_change=proposal.proposed_change,
            created_at=start_time,
            sandbox_path=str(candidate_dir),
            status=CandidateStatus.CREATED,
        )
        experiment = EvolutionExperiment(
            experiment_id=experiment_id,
            proposal_id=proposal_id,
            candidate_id=candidate_id,
            baseline_version=proposal.agent_version,
            candidate_version=candidate.candidate_commit,
            sandbox_location=str(experiment_dir),
            start_time=start_time,
            end_time=None,
            status=ExperimentStatus.CREATED,
            tests_run=0,
            tests_passed=0,
            tests_failed=0,
            timeout=False,
            errors=[],
            logs={},
            resource_information={},
            network_policy="denied",
            isolation_policy=self._isolation_policy(),
            cleanup_status="not_started",
            candidate=candidate.to_dict(),
        )
        self.store.save_experiment(experiment)
        self._event(EventType.SANDBOX_CREATED, experiment, {"baseline_hash": baseline_hash})
        self._event(EventType.BASELINE_SNAPSHOT_CREATED, experiment, {"source_commit": source_commit, "baseline_hash": baseline_hash})
        self._event(EventType.CANDIDATE_CREATED, experiment, {"candidate": candidate.to_dict()})
        return experiment, proposal, baseline_dir, candidate_dir

    def prepare_candidate(self, experiment: EvolutionExperiment, candidate_dir: Path) -> CandidateVersion:
        candidate = self._candidate_from_experiment(experiment)
        candidate.status = CandidateStatus.PREPARED
        metadata = {
            "candidate_id": candidate.candidate_id,
            "experiment_id": candidate.experiment_id,
            "proposal_id": candidate.proposal_id,
            "baseline_version": candidate.baseline_version,
            "target_component": candidate.target_component,
            "allowed_files": ["evolution_config.json"],
            "allowed_components": sorted(self.SUPPORTED_TARGETS),
            "network_policy": "denied",
        }
        (candidate_dir / "candidate_metadata.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")
        self._event(EventType.CANDIDATE_CREATED, experiment, {"candidate_status": candidate.status.value, "metadata": metadata})
        return candidate

    def apply_approved_proposal(self, proposal: EvolutionProposal, candidate: CandidateVersion) -> None:
        self._validate_structured_target(proposal)
        candidate_dir = Path(candidate.sandbox_path).resolve()
        experiment_dir = candidate_dir.parent
        if not candidate_dir.is_relative_to(experiment_dir) or experiment_dir.parent != self.sandbox_root:
            raise PermissionError("Candidate path is outside the managed sandbox")
        config_path = candidate_dir / "evolution_config.json"
        config = {
            "proposal_id": proposal.proposal_id,
            "target_component": proposal.target_component,
            "change": proposal.proposed_change,
            "expected_benefit": proposal.expected_benefit,
            "evaluation_method": proposal.evaluation_method,
            "rollback_plan": proposal.rollback_plan,
            "mode": "structured_candidate_configuration",
            "executable_code_generated": False,
        }
        config_path.write_text(json.dumps(config, indent=2), encoding="utf-8")
        candidate.status = CandidateStatus.PREPARED
        experiment = self._load_experiment(candidate.experiment_id)
        self._event(EventType.PROPOSAL_APPLIED, experiment, {"path": str(config_path), "mode": config["mode"], "target_component": proposal.target_component})

    def execute_candidate(self, experiment: EvolutionExperiment, location: Path, label: str, command: Iterable[str] = ("python3", "-m", "pytest", "-q")) -> ExecutionResult:
        command_list = self._validate_test_command(command)
        location = Path(location).resolve()
        experiment_dir = Path(experiment.sandbox_location).resolve()
        if not location.is_relative_to(experiment_dir) or location.name not in {"baseline", "candidate"}:
            raise PermissionError("Execution location is outside the experiment boundary")
        self._event(EventType.CANDIDATE_STARTED if label == "candidate" else EventType.CANDIDATE_TEST_STARTED, experiment, {"label": label, "command": command_list, "location": str(location)})
        started = datetime.now(timezone.utc)
        output = ""
        error = ""
        return_code: int | None = None
        timed_out = False
        completed = False
        env = self._sanitized_environment(experiment)
        process = None
        try:
            isolated_command = self._isolated_command(location, command_list)
            process = subprocess.Popen(isolated_command, cwd=location, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, start_new_session=True)
            try:
                output, _ = process.communicate(timeout=self.timeout_seconds)
                return_code = process.returncode
                completed = True
            except subprocess.TimeoutExpired:
                timed_out = True
                error = f"Process exceeded {self.timeout_seconds}s timeout"
                self._terminate_process(process)
                output, _ = process.communicate()
                return_code = process.returncode
        except Exception as exc:
            error = str(exc)
        duration_ms = max(0, int((datetime.now(timezone.utc) - started).total_seconds() * 1000))
        if not timed_out and return_code not in (0, None) and not error:
            error = f"Candidate command exited with {return_code}"
        tests_run = 1 if completed or timed_out or return_code is not None else 0
        tests_passed = 1 if completed and return_code == 0 else 0
        tests_failed = 1 if tests_run and tests_passed == 0 else 0
        result = ExecutionResult(label, command_list, completed, timed_out, return_code, output, error, tests_run, tests_passed, tests_failed, duration_ms, sorted(env.keys()))
        log_path = experiment_dir / "logs" / f"{label}.log"
        log_path.write_text(output + (f"\nERROR: {error}\n" if error else ""), encoding="utf-8")
        experiment.logs[label] = str(log_path)
        event_type = EventType.CANDIDATE_TEST_COMPLETED if label == "candidate" and result.completed and not result.timeout else EventType.CANDIDATE_FAILED
        self._event(event_type, experiment, {"label": label, "result": result.to_dict()})
        return result

    def collect_results(self, experiment: EvolutionExperiment, baseline: ExecutionResult, candidate: ExecutionResult) -> EvolutionExperiment:
        experiment.baseline_execution = baseline.to_dict()
        experiment.candidate_execution = candidate.to_dict()
        experiment.tests_run = candidate.tests_run
        experiment.tests_passed = candidate.tests_passed
        experiment.tests_failed = candidate.tests_failed
        experiment.timeout = candidate.timeout
        experiment.errors = [item for item in (baseline.error, candidate.error) if item]
        experiment.status = ExperimentStatus.TIMEOUT if candidate.timeout else ExperimentStatus.PASSED if candidate.completed and candidate.return_code == 0 else ExperimentStatus.FAILED
        candidate_record = self._candidate_from_experiment(experiment)
        candidate_record.status = CandidateStatus.PASSED if experiment.status is ExperimentStatus.PASSED else CandidateStatus.FAILED if experiment.status is ExperimentStatus.FAILED else CandidateStatus.ABORTED
        experiment.candidate = candidate_record.to_dict()
        self.store.save_experiment(experiment)
        return experiment

    def compare_with_baseline(self, baseline: ExecutionResult | dict[str, Any], candidate: ExecutionResult | dict[str, Any]) -> ComparisonResult:
        baseline_data = baseline.to_dict() if isinstance(baseline, ExecutionResult) else baseline
        candidate_data = candidate.to_dict() if isinstance(candidate, ExecutionResult) else candidate
        if baseline_data.get("timeout") or candidate_data.get("timeout"):
            classification = ComparisonClass.INCONCLUSIVE
            rationale = "At least one execution timed out; comparison is not fully conclusive."
        elif candidate_data.get("return_code") == 0 and baseline_data.get("return_code") != 0:
            classification, rationale = ComparisonClass.BETTER, "Candidate completed successfully while baseline did not."
        elif candidate_data.get("return_code") != 0 and baseline_data.get("return_code") == 0:
            classification, rationale = ComparisonClass.WORSE, "Baseline completed successfully while candidate did not."
        elif candidate_data.get("return_code") == baseline_data.get("return_code"):
            classification, rationale = ComparisonClass.NO_CHANGE, "Candidate and baseline have the same completion result; no improvement is proven."
        else:
            classification, rationale = ComparisonClass.INCONCLUSIVE, "Execution evidence is insufficient for a reliable comparison."
        return ComparisonResult(classification, rationale, baseline_data, candidate_data)

    def finalize_experiment(self, experiment: EvolutionExperiment, comparison: ComparisonResult) -> EvolutionExperiment:
        experiment.comparison = comparison.to_dict()
        experiment.end_time = self._now()
        self.store.save_experiment(experiment)
        return experiment

    def destroy_sandbox(self, experiment: EvolutionExperiment) -> EvolutionExperiment:
        self._event(EventType.SANDBOX_CLEANUP_STARTED, experiment, {})
        try:
            path = Path(experiment.sandbox_location).resolve()
            if path.parent != self.sandbox_root:
                raise PermissionError("Sandbox path is not managed by this engine")
            if path.exists():
                self._make_writable(path)
                shutil.rmtree(path)
            if experiment.candidate:
                experiment.candidate["status"] = CandidateStatus.DESTROYED.value
            experiment.cleanup_status = "destroyed"
            self._event(EventType.SANDBOX_DESTROYED, experiment, {"path": str(path)})
        except Exception as exc:
            experiment.cleanup_status = f"failed: {exc}"
            experiment.errors.append(f"cleanup: {exc}")
            self._event(EventType.SANDBOX_ABORTED, experiment, {"error": str(exc)})
        self.store.save_experiment(experiment)
        return experiment

    def run_experiment(self, proposal_id: str, command: Iterable[str] = ("python3", "-m", "pytest", "-q", "test_sandbox_controlled.py"), retain_sandbox: bool = False) -> EvolutionExperiment:
        production_hash_before = self._manifest_hash(self.source_root)
        experiment, proposal, baseline_dir, candidate_dir = self.create_sandbox(proposal_id)
        try:
            candidate = self.prepare_candidate(experiment, candidate_dir)
            self.apply_approved_proposal(proposal, candidate)
            experiment.status = ExperimentStatus.RUNNING
            self.store.save_experiment(experiment)
            baseline_result = self.execute_candidate(experiment, baseline_dir, "baseline", command)
            candidate_result = self.execute_candidate(experiment, candidate_dir, "candidate", command)
            experiment = self.collect_results(experiment, baseline_result, candidate_result)
            comparison = self.compare_with_baseline(baseline_result, candidate_result)
            experiment = self.finalize_experiment(experiment, comparison)
            production_hash_after = self._manifest_hash(self.source_root)
            if production_hash_before != production_hash_after:
                experiment.status = ExperimentStatus.FAILED
                experiment.errors.append("production immutability hash changed")
                self._event(EventType.SANDBOX_ABORTED, experiment, {"reason": "production immutability violation"})
                self.store.save_experiment(experiment)
            return experiment
        except Exception as exc:
            experiment.status = ExperimentStatus.ABORTED
            experiment.errors.append(str(exc))
            self._event(EventType.SANDBOX_ABORTED, experiment, {"error": str(exc)})
            self.store.save_experiment(experiment)
            return experiment
        finally:
            if not retain_sandbox:
                self.destroy_sandbox(experiment)

    def list_experiments(self, limit: int = 50) -> list[EvolutionExperiment]:
        return [self._experiment_from_record(record) for record in self.store.find_experiments(limit=limit)]

    def get_experiment(self, experiment_id: str) -> EvolutionExperiment | None:
        record = self.store.experiment_by_id(experiment_id)
        return self._experiment_from_record(record) if record else None

    def _require_approved_proposal(self, proposal_id: str) -> EvolutionProposal:
        record = self.store.proposal_by_id(proposal_id)
        if not record:
            raise PermissionError("Proposal does not exist")
        proposal = Evolver.from_dict(record)
        if proposal.status is not ProposalStatus.APPROVED:
            raise PermissionError(f"Only APPROVED proposals may enter the sandbox; current status is {proposal.status.value}")
        if proposal.risk is ProposalRisk.PROTECTED or proposal.validation_errors:
            raise PermissionError("Invalid or protected proposal cannot enter the sandbox")
        self._validate_structured_target(proposal)
        return proposal

    def _validate_structured_target(self, proposal: EvolutionProposal) -> None:
        target = proposal.target_component.strip().lower()
        if any(term in f"{target} {proposal.proposed_change.lower()}" for term in self.PROTECTED_TERMS):
            raise PermissionError("Protected component modification is rejected")
        if target not in self.SUPPORTED_TARGETS:
            raise PermissionError(f"Unsupported candidate target: {proposal.target_component}")
        if not proposal.proposed_change.strip() or len(proposal.proposed_change.strip()) < 12:
            raise ValueError("Candidate change is insufficiently specific")

    def _copy_production(self, source: Path, destination: Path, readonly: bool) -> None:
        ignored = shutil.ignore_patterns(".git", ".evo", "__pycache__", ".pytest_cache", "*.pyc", "workspace")
        for item in source.iterdir():
            target = destination / item.name
            if item.name in {".git", ".evo", "__pycache__", ".pytest_cache", "workspace"} or item.suffix == ".pyc":
                continue
            if item.is_dir():
                shutil.copytree(item, target, ignore=ignored)
            else:
                shutil.copy2(item, target)
        if readonly:
            self._make_readonly(destination)

    @staticmethod
    def _make_readonly(destination: Path) -> None:
        for path in destination.rglob("*"):
            if path.is_file():
                path.chmod(0o500 if path.suffix in {".py", ".sh"} else 0o400)
            elif path.is_dir():
                path.chmod(0o500)

    @staticmethod
    def _make_writable(destination: Path) -> None:
        if destination.is_file():
            destination.chmod(0o600)
            return
        for path in destination.rglob("*"):
            if path.is_file():
                path.chmod(0o600)
            elif path.is_dir():
                path.chmod(0o700)
        destination.chmod(0o700)

    def _sanitized_environment(self, experiment: EvolutionExperiment) -> dict[str, str]:
        home = Path(experiment.sandbox_location) / "metadata" / "home"
        home.mkdir(parents=True, exist_ok=True)
        return {
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "HOME": str(home),
            "TMPDIR": str(Path(experiment.sandbox_location) / "results"),
            "LANG": "C.UTF-8",
            "LC_ALL": "C.UTF-8",
            "PYTHONNOUSERSITE": "1",
            "NO_PROXY": "*",
            "no_proxy": "*",
            "EVO_NETWORK_POLICY": "denied",
            "EVO_EXPERIMENT_ID": experiment.experiment_id,
        }

    @staticmethod
    def _validate_test_command(command: Iterable[str]) -> list[str]:
        command_list = [str(part) for part in command]
        if not command_list or any(not part or "\x00" in part for part in command_list):
            raise ValueError("Candidate test command is invalid")
        allowed = tuple(command_list) in {
            ("python3", "-m", "pytest", "-q"),
            ("python3", "-m", "pytest", "-q", "test_sandbox_controlled.py"),
            ("python3", "-m", "pytest", "-q", "-p", "no:cacheprovider"),
            ("python3", "-m", "pytest", "-q", "test_sandbox_controlled.py", "-p", "no:cacheprovider"),
            ("pytest", "-q"),
            ("pytest", "-q", "test_sandbox_controlled.py"),
            ("pytest", "-q", "-p", "no:cacheprovider"),
            ("pytest", "-q", "test_sandbox_controlled.py", "-p", "no:cacheprovider"),
        }
        if not allowed:
            raise PermissionError("Only the fixed pytest runner is allowed; arbitrary generated code is not executable")
        return command_list

    def _isolated_command(self, location: Path, command: list[str]) -> list[str]:
        # Bubblewrap is preferred because it provides a portable user/net/PID namespace
        # interface on hosted runners. The unshare path remains a conservative fallback.
        if shutil.which("bwrap"):
            return [
                "bwrap",
                "--die-with-parent",
                "--unshare-user",
                "--unshare-net",
                "--unshare-pid",
                "--ro-bind", "/", "/",
                "--dev", "/dev",
                "--proc", "/proc",
                "--tmpfs", "/tmp",
                "--bind", str(location), str(location),
                "--chdir", str(location),
                *command,
            ]
        # User/mount/PID namespaces isolate mounts and process visibility without requiring host root.
        # The production source is remounted read-only inside the child namespace.
        script = (
            "set -eu; "
            "mount --make-rprivate /; "
            "mount --bind \"$2\" \"$2\"; "
            "mount -o remount,bind,ro \"$2\"; "
            "cd \"$1\"; "
            "shift 2; "
            "exec \"$@\""
        )
        return ["unshare", "--user", "--map-root-user", "--mount", "--net", "--pid", "--fork", "--mount-proc", "sh", "-c", script, "evo-sandbox", str(location), str(self.source_root), *command]

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

    def _candidate_from_experiment(self, experiment: EvolutionExperiment) -> CandidateVersion:
        if not experiment.candidate:
            raise ValueError("Experiment has no candidate record")
        data = dict(experiment.candidate)
        data["status"] = CandidateStatus(data["status"])
        return CandidateVersion(**data)

    def _load_experiment(self, experiment_id: str) -> EvolutionExperiment:
        experiment = self.get_experiment(experiment_id)
        if not experiment:
            raise KeyError(f"Experiment not found: {experiment_id}")
        return experiment

    def _event(self, event_type: EventType, experiment: EvolutionExperiment, payload: dict[str, Any]) -> None:
        self.store.append_event(Event(self.SANDBOX_TASK_ID, event_type, {"experiment_id": experiment.experiment_id, "proposal_id": experiment.proposal_id, "candidate_id": experiment.candidate_id, **payload}))

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    @staticmethod
    def _new_id(prefix: str) -> str:
        import uuid
        return f"{prefix}_{uuid.uuid4().hex[:12]}"

    def _git_commit(self) -> str:
        try:
            result = subprocess.run(["git", "-C", str(self.source_root), "rev-parse", "HEAD"], capture_output=True, text=True, timeout=5, check=False)
            return result.stdout.strip() if result.returncode == 0 else "unknown"
        except Exception:
            return "unknown"

    def _manifest_hash(self, root: Path) -> str:
        digest = hashlib.sha256()
        for path in sorted(root.rglob("*")):
            if not path.is_file() or any(part in {".git", ".evo", "__pycache__", ".pytest_cache"} for part in path.relative_to(root).parts):
                continue
            digest.update(str(path.relative_to(root)).encode())
            digest.update(path.read_bytes())
        return digest.hexdigest()

    @staticmethod
    def _isolation_policy() -> dict[str, Any]:
        return {
            "production_source_read_only_copy": True,
            "separate_working_directory": True,
            "subprocess_start_new_session": True,
            "sanitized_environment": True,
            "network_default": "denied",
            "generated_code_execution": False,
        }

    @staticmethod
    def _experiment_from_record(record: dict[str, Any]) -> EvolutionExperiment:
        payload = record.get("payload", record)
        if isinstance(payload, str):
            payload = json.loads(payload)
        payload = dict(payload)
        payload["status"] = ExperimentStatus(payload["status"])
        return EvolutionExperiment(**payload)
