"""Run the deterministic offline Evo V1 pilot corpus.

The pilot is intentionally finite, local, and advisory. It uses the existing
Runtime, Cognitive, Kernel, Verifier, Memory, and SQLite authorities and never
calls external providers or performs autonomous approval or promotion.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
import tempfile
import time
from pathlib import Path
from typing import Any


def _core_digests(repo: Path) -> dict[str, str]:
    names = ["kernel.py", "security.py", "verifier.py", "runtime.py", "promotion.py", "metamorphosis.py", "orchestrator.py"]
    return {name: hashlib.sha256((repo / "evo_agent" / name).read_bytes()).hexdigest() for name in names}


class _FailThenRecoverAdapter:
    def __init__(self):
        self.calls = 0

    def create_plan(self, goal, _tool_schemas, _context=""):
        from evo_agent.models import Plan, PlanStep, RiskLevel, new_id

        self.calls += 1
        if self.calls == 1:
            return Plan(goal.task_id, [PlanStep(new_id("step"), "Run an intentionally rejected pilot command", "shell", {"command": "false"}, RiskLevel.HIGH, "result is non-empty")], "bounded failure-injection plan")
        return Plan(goal.task_id, [PlanStep(new_id("step"), "Recover by inspecting the workspace", "workspace_list", {"path": "."}, RiskLevel.LOW, "result is valid JSON")], "bounded recovery plan")

    def choose_recovery(self, goal, failed_step, result):
        return "Use the safe bounded recovery plan and do not repeat the rejected command."


def _runtime(workspace: Path, model: Any | None = None):
    from evo_agent import AgentRuntime, RuntimeResourceLimits

    return AgentRuntime(workspace, model=model, limits=RuntimeResourceLimits(max_tasks_per_cycle=1, max_total_runtime=120, max_queue_size=32), approval_callback=lambda _call, _reason: True)


def _run_single_case(root: Path, case: dict[str, Any]) -> dict[str, Any]:
    from evo_agent import RuntimeTaskStatus

    case_workspace = root / str(case["id"])
    case_workspace.mkdir(parents=True, exist_ok=True)
    (case_workspace / "README.md").write_text("Deterministic V1 pilot fixture.\n", encoding="utf-8")
    case_id = str(case["id"])
    runtime = _runtime(case_workspace, _FailThenRecoverAdapter() if case_id == "replan-shell-failure" else None)
    runtime.start()
    goal = str(case["goal"])
    safe_mode = case_id == "safe-mode-write"
    if safe_mode:
        runtime.set_safe_mode(True, "V1 pilot safety case")
    requires_exact_approval = case_id == "approval-write-report"
    task = runtime.enqueue_task(goal, approval_requirement=requires_exact_approval)
    first_cycle = runtime.run_cycle()
    current = runtime.task(task.task_id)
    if current is None:
        raise RuntimeError(f"task disappeared: {task.task_id}")
    record: dict[str, Any] = {"id": case_id, "class": case["class"], "expected": case["expected"], "task_id": task.task_id, "status_after_first_cycle": current.status.value, "cycle_state": first_cycle.state, "tasks_started": first_cycle.tasks_started, "tasks_completed": first_cycle.tasks_completed, "tasks_waiting": first_cycle.tasks_waiting, "tasks_failed": first_cycle.tasks_failed, "verified": bool(current.metadata.get("verified", False))}
    if requires_exact_approval:
        scope = current.metadata.get("approval_context_hash")
        record["approval_waited"] = current.status is RuntimeTaskStatus.WAITING and bool(scope)
        if scope:
            runtime.approve_task(task.task_id, actor="human", scope_hash=str(scope), reason="bounded V1 pilot approval")
            second_cycle = runtime.run_cycle()
            final = runtime.task(task.task_id)
            record["status_after_approval"] = final.status.value if final else "missing"
            record["verified_after_approval"] = bool(final and final.metadata.get("verified", False))
            record["approval_cycle_completed"] = second_cycle.tasks_completed
    if not safe_mode and not requires_exact_approval:
        for _ in range(2):
            current = runtime.task(task.task_id)
            if current is None or current.status is not RuntimeTaskStatus.READY:
                break
            runtime.run_cycle()
        current = runtime.task(task.task_id)
        record["status_after_execution"] = current.status.value if current else "missing"
        record["verified_after_execution"] = bool(current and current.metadata.get("verified", False))
    if safe_mode:
        record["safe_mode_waited"] = current.status is RuntimeTaskStatus.WAITING
        record["side_effect_blocked"] = current.status is RuntimeTaskStatus.WAITING and not (case_workspace / "report.txt").exists()
        runtime.set_safe_mode(False)
    runtime.stop("pilot case complete")
    return record


def _run_memory_case(root: Path) -> dict[str, Any]:
    case_workspace = root / "memory-informed-read"
    case_workspace.mkdir(parents=True, exist_ok=True)
    (case_workspace / "README.md").write_text("Memory fixture for the V1 pilot.\n", encoding="utf-8")
    runtime = _runtime(case_workspace)
    runtime.start()
    first = runtime.enqueue_task("list the files")
    runtime.run_cycle()
    second = runtime.enqueue_task("read file README.md")
    cycle = runtime.run_cycle()
    task = runtime.task(second.task_id)
    experiences = runtime.store.find_experiences(limit=20)
    evaluation_count = sum(1 for item in experiences if item.get("evaluation_id") or (isinstance(item.get("payload"), dict) and item["payload"].get("evaluation_id")))
    result = {"id": "memory-informed-read", "class": "memory_and_experience", "expected": "persisted_experience_and_evaluation", "task_id": second.task_id, "status": task.status.value if task else "missing", "verified": bool(task and task.metadata.get("verified", False)), "experience_count": len(experiences), "evaluation_count": evaluation_count, "cycle_state": cycle.state}
    runtime.stop("memory case complete")
    return result


def _run_restart_case(root: Path) -> dict[str, Any]:
    from evo_agent import RuntimeTaskStatus

    case_workspace = root / "restart-recovery"
    case_workspace.mkdir(parents=True, exist_ok=True)
    runtime = _runtime(case_workspace)
    runtime.start()
    task = runtime.enqueue_task("list the files")
    queued = runtime.task(task.task_id)
    if queued is None:
        raise RuntimeError("restart task disappeared")
    queued.status = RuntimeTaskStatus.RUNNING
    runtime.queue.update(queued)
    restarted = _runtime(case_workspace)
    restarted.start()
    recovered = restarted.task(task.task_id)
    result = {"id": "restart-recovery", "class": "restart_recovery", "expected": "deterministic_recovery", "task_id": task.task_id, "status": recovered.status.value if recovered else "missing", "recovery_required": bool(recovered and recovered.metadata.get("recovery_required", False))}
    restarted.stop("restart case complete")
    return result


def run(repo: Path, corpus_path: Path) -> dict[str, Any]:
    sys.path.insert(0, str(repo))
    from evo_agent import RuntimeTaskStatus
    from evo_agent.storage import SQLiteStore

    corpus = json.loads(corpus_path.read_text(encoding="utf-8"))
    tasks = corpus.get("tasks", [])
    if not isinstance(tasks, list) or not tasks:
        raise ValueError("pilot corpus must contain at least one task")
    with tempfile.TemporaryDirectory(prefix="evo-v1-pilot-") as raw:
        root = Path(raw)
        before = _core_digests(repo)
        started = time.perf_counter()
        records: list[dict[str, Any]] = []
        for case in tasks:
            if case["id"] == "memory-informed-read":
                records.append(_run_memory_case(root))
            elif case["id"] == "restart-recovery":
                records.append(_run_restart_case(root))
            else:
                records.append(_run_single_case(root, case))
        startup_seconds = time.perf_counter() - started
        aggregate_workspace = root / "aggregate"
        aggregate_workspace.mkdir(parents=True, exist_ok=True)
        aggregate = _runtime(aggregate_workspace)
        aggregate.start()
        for _ in range(5):
            aggregate.run_cycle()
        aggregate.stop("aggregate pilot complete")
        database_path = aggregate_workspace / ".evo" / "agent.sqlite3"
        restart_store = SQLiteStore(database_path)
        restart_integrity = restart_store.validate_database_integrity()
        backup_path = root / "backup.sqlite3"
        shutil.copy2(database_path, backup_path)
        backup_report = SQLiteStore(backup_path).validate_database_integrity()
        after = _core_digests(repo)
        if before != after:
            raise RuntimeError("protected core changed during pilot")
        expected_pass = all(
            (item.get("id") in {"readonly-list", "multistep-read-report"} and item.get("status_after_execution", item.get("status_after_first_cycle")) == "completed" and item.get("verified_after_execution", item.get("verified")) is True)
            or (item.get("id") == "approval-write-report" and item.get("approval_waited") is True and item.get("status_after_approval") == "completed" and item.get("verified_after_approval") is True)
            or (item.get("id") == "replan-shell-failure" and item.get("status_after_execution", item.get("status_after_first_cycle")) == "completed" and item.get("verified_after_execution", item.get("verified")) is True)
            or (item.get("id") == "safe-mode-write" and item.get("safe_mode_waited") is True and item.get("side_effect_blocked") is True)
            or (item.get("id") == "memory-informed-read" and item.get("status") == "completed" and item.get("verified") is True and item.get("experience_count", 0) >= 1 and item.get("evaluation_count", 0) >= 1)
            or (item.get("id") == "restart-recovery" and item.get("status") == "waiting" and item.get("recovery_required") is True)
            for item in records
        )
        return {"status": "pass" if expected_pass else "fail", "corpus_version": corpus.get("corpus_version"), "adapter": corpus.get("adapter"), "task_count": len(tasks), "records": records, "pilot_seconds": round(startup_seconds, 6), "restart_database_integrity": restart_integrity, "backup_database_integrity": backup_report, "protected_core_immutable": before == after, "bounded": True, "expected_outcomes_pass": expected_pass}


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the bounded offline Evo V1 pilot")
    parser.add_argument("--repo", default=str(Path(__file__).resolve().parents[1]))
    parser.add_argument("--corpus", default=str(Path(__file__).resolve().parents[1] / "pilot" / "v1_task_corpus.json"))
    parser.add_argument("--output", default="")
    args = parser.parse_args()
    report = run(Path(args.repo).resolve(), Path(args.corpus).resolve())
    rendered = json.dumps(report, indent=2, sort_keys=True)
    print(rendered)
    if args.output:
        Path(args.output).write_text(rendered + "\n", encoding="utf-8")
    return 0 if report["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
