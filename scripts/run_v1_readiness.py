"""Run the expanded local Evo V1 operational-readiness matrix."""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))
from run_v1_pilot import run as run_pilot


def _check(repo: Path, name: str, category: str, fn) -> dict[str, Any]:
    try:
        details = fn()
        return {"id": name, "category": category, "status": "pass", "details": details if isinstance(details, dict) else {"result": details}}
    except Exception as exc:
        return {"id": name, "category": category, "status": "fail", "details": {"error": f"{type(exc).__name__}: {exc}"}}


def run(repo: Path, corpus: Path) -> dict[str, Any]:
    sys.path.insert(0, str(repo))
    from evo_agent import AgentRuntime, RuntimeResourceLimits, RuntimeTaskStatus
    from evo_agent.models import Event, EventType
    from evo_agent.storage import SQLiteStore

    checks: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory(prefix="evo-v1-readiness-") as raw:
        root = Path(raw)

        def malformed_goal():
            runtime = AgentRuntime(root / "malformed")
            try:
                runtime.enqueue_task("")
            except ValueError as exc:
                return {"rejected": True, "reason": str(exc)}
            raise AssertionError("empty goal was admitted")

        def deadline_expiry():
            workspace = root / "deadline"
            runtime = AgentRuntime(workspace)
            task = runtime.enqueue_task("list the files", deadline=(datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat())
            runtime.start()
            runtime.run_cycle()
            current = runtime.task(task.task_id)
            if not current or current.status is not RuntimeTaskStatus.EXPIRED:
                raise AssertionError(f"unexpected status: {current.status.value if current else 'missing'}")
            return {"status": current.status.value}

        def duplicate_admission():
            runtime = AgentRuntime(root / "duplicate")
            first = runtime.enqueue_task("list the files")
            second = runtime.enqueue_task("list the files")
            if first.task_id != second.task_id:
                raise AssertionError("duplicate logical task was not deduplicated")
            return {"task_id": first.task_id, "deduplicated": True}

        def queue_backpressure():
            runtime = AgentRuntime(root / "backpressure", limits=RuntimeResourceLimits(max_queue_size=1))
            runtime.enqueue_task("first goal")
            try:
                runtime.enqueue_task("second goal")
            except OverflowError as exc:
                return {"bounded": True, "reason": str(exc)}
            raise AssertionError("queue exceeded its configured ceiling")

        def kill_switch_restart():
            workspace = root / "kill-switch"
            runtime = AgentRuntime(workspace)
            runtime.start()
            runtime.kill_switch("readiness check")
            restarted = AgentRuntime(workspace)
            if not restarted.runtime_record.metadata.get("kill_switch"):
                raise AssertionError("kill switch did not persist")
            try:
                restarted.start()
            except RuntimeError as exc:
                return {"persisted": True, "restart_blocked": True, "reason": str(exc)}
            raise AssertionError("restart bypassed kill switch")

        def corrupt_persistence():
            workspace = root / "corrupt"
            runtime = AgentRuntime(workspace)
            runtime.start()
            runtime.stop("prepare corruption")
            with runtime.store._connect() as db:
                db.execute("UPDATE runtime_states SET payload = ? WHERE runtime_id = ?", ("not-json", runtime.runtime_id))
            restarted = AgentRuntime(workspace)
            try:
                restarted.start()
            except RuntimeError as exc:
                return {"fail_closed": True, "reason": str(exc)}
            raise AssertionError("corrupt persisted state was accepted")

        def oversized_event():
            store = SQLiteStore(root / "event-bound")
            store.append_event(Event("readiness", EventType.TASK_COMPLETED, {"output": "x" * 100_000}))
            payload = store.events_for_task("readiness")[0]["payload"]
            if not payload.get("truncated"):
                raise AssertionError("oversized event was not bounded")
            return {"truncated": True, "original_bytes": payload["original_bytes"]}

        def secret_redaction():
            store = SQLiteStore(root / "event-redaction")
            store.append_event(Event("readiness", EventType.TASK_COMPLETED, {"api_key": "secret-value", "nested": {"password": "password-value"}}))
            rendered = json.dumps(store.events_for_task("readiness")[0]["payload"])
            if "secret-value" in rendered or "password-value" in rendered:
                raise AssertionError("secret value persisted")
            return {"redacted": True}

        def status_hygiene():
            runtime = AgentRuntime(root / "status")
            runtime.start()
            rendered = json.dumps(runtime.status())
            forbidden = ["OPENAI_API_KEY", "ANTHROPIC_API_KEY", "credential"]
            if any(item.lower() in rendered.lower() for item in forbidden):
                raise AssertionError("runtime status contains a credential marker")
            return {"secret_free": True}

        def backup_restore():
            source = root / "restore-source"
            runtime = AgentRuntime(source)
            runtime.start()
            task = runtime.enqueue_task("list the files")
            runtime.run_cycle()
            runtime.stop("prepare backup")
            backup = root / "restore-backup.sqlite3"
            shutil.copy2(source / ".evo" / "agent.sqlite3", backup)
            destination = root / "restore-destination"
            shutil.copytree(source / ".evo", destination / ".evo")
            restored = AgentRuntime(destination)
            restored.start()
            restored_task = restored.task(task.task_id)
            integrity = restored.store.validate_database_integrity()
            restored.stop("restore accepted")
            if not integrity["ok"] or restored_task is None:
                raise AssertionError("restored workspace did not recover persisted history")
            return {"restored": True, "task_history_present": True, "database_integrity": integrity["ok"]}

        def sustained_cycles():
            runtime = AgentRuntime(root / "sustained", limits=RuntimeResourceLimits(max_tasks_per_cycle=1, max_total_runtime=120, max_queue_size=8))
            started = time.perf_counter()
            runtime.start()
            for _ in range(20):
                runtime.run_cycle()
            elapsed = time.perf_counter() - started
            health = runtime.health()
            database_bytes = runtime.store.path.stat().st_size
            runtime.stop("sustained-cycle baseline complete")
            if health.status.value != "healthy" or database_bytes > 50 * 1024 * 1024:
                raise AssertionError(f"unexpected sustained baseline: health={health.status.value}, database_bytes={database_bytes}")
            return {"cycles": 20, "elapsed_seconds": round(elapsed, 6), "database_bytes": database_bytes, "health": health.status.value}

        def cli_consistency():
            completed = subprocess.run([sys.executable, "-m", "evo_agent.cli", "--help"], cwd=repo, capture_output=True, text=True, check=False)
            if completed.returncode != 0:
                raise AssertionError(completed.stderr.strip())
            required = ["--workspace", "--json", "--runtime-cycle", "--goal-create", "--goal-verify", "--rollback"]
            missing = [item for item in required if item not in completed.stdout]
            if missing:
                raise AssertionError(f"missing CLI options: {missing}")
            return {"required_options": len(required), "consistent": True}

        checks.extend([
            _check(repo, "malformed-goal", "input-validation", malformed_goal),
            _check(repo, "deadline-expiry", "runtime-bounds", deadline_expiry),
            _check(repo, "duplicate-admission", "idempotency", duplicate_admission),
            _check(repo, "queue-backpressure", "resource-bounds", queue_backpressure),
            _check(repo, "kill-switch-restart", "safety-recovery", kill_switch_restart),
            _check(repo, "corrupt-persistence", "database-recovery", corrupt_persistence),
            _check(repo, "oversized-event", "observability-bounds", oversized_event),
            _check(repo, "secret-redaction", "credential-hygiene", secret_redaction),
            _check(repo, "status-hygiene", "operator-observability", status_hygiene),
            _check(repo, "backup-restore", "backup-recovery", backup_restore),
            _check(repo, "sustained-cycles", "performance-bounds", sustained_cycles),
            _check(repo, "cli-consistency", "operator-usability", cli_consistency),
        ])
        pilot = run_pilot(repo, corpus)
        checks.append({"id": "expanded-pilot-corpus", "category": "end-to-end", "status": pilot["status"], "details": {"task_count": pilot["task_count"], "expected_outcomes_pass": pilot["expected_outcomes_pass"], "protected_core_immutable": pilot["protected_core_immutable"], "restart_database_integrity": pilot["restart_database_integrity"]["ok"], "backup_database_integrity": pilot["backup_database_integrity"]["ok"], "pilot_seconds": pilot["pilot_seconds"]}})
    passed = sum(item["status"] == "pass" for item in checks)
    return {"status": "pass" if passed == len(checks) else "fail", "check_count": len(checks), "passed": passed, "failed": len(checks) - passed, "checks": checks}


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the bounded offline Evo V1 readiness matrix")
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
