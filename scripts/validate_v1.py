"""Reproducible local validation for the frozen Evo V1 architecture.

This script is intentionally bounded and offline. It exercises existing authorities;
it does not run external providers, approve changes, promote candidates, or mutate
production source.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


def digest_paths(repo: Path) -> dict[str, str]:
    names = ["kernel.py", "security.py", "verifier.py", "runtime.py", "promotion.py", "metamorphosis.py", "orchestrator.py"]
    return {name: hashlib.sha256((repo / "evo_agent" / name).read_bytes()).hexdigest() for name in names}


def run_cli(repo: Path, workspace: Path, *args: str) -> dict:
    command = [sys.executable, "-m", "evo_agent.cli", "--workspace", str(workspace), "--json", *args]
    completed = subprocess.run(command, cwd=repo, capture_output=True, text=True, check=False)
    if completed.returncode != 0:
        raise RuntimeError(f"CLI failed ({completed.returncode}): {completed.stderr.strip()}")
    try:
        return json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"CLI did not return JSON: {completed.stdout[:200]}") from exc


def validate(repo: Path) -> dict[str, object]:
    sys.path.insert(0, str(repo))
    from evo_agent import AgentRuntime, RuntimeState
    from evo_agent.security import SecurityPolicy
    from evo_agent.storage import SQLiteStore
    from evo_agent.version import __version__

    if __version__ != "1.0.0":
        raise RuntimeError(f"unexpected V1 version: {__version__}")
    with tempfile.TemporaryDirectory(prefix="evo-v1-validation-") as raw:
        workspace = Path(raw) / "workspace"
        before = digest_paths(repo)
        store = SQLiteStore(workspace / ".evo" / "agent.sqlite3")
        integrity = store.validate_database_integrity()
        policy = SecurityPolicy(workspace)
        if not policy.validate_command("ls")[0] or policy.validate_command("rm -rf .")[0]:
            raise RuntimeError("shell policy validation failed")
        try:
            policy.resolve_workspace_path("../outside")
        except PermissionError:
            pass
        else:
            raise RuntimeError("workspace confinement validation failed")
        runtime = AgentRuntime(workspace, store=store)
        runtime.start()
        runtime.stop("V1 release validation")
        if runtime.state is not RuntimeState.STOPPED:
            raise RuntimeError("runtime did not stop deterministically")
        goal = run_cli(repo, workspace, "--goal-create", "inspect a local release artifact")
        if not goal.get("goal_id"):
            raise RuntimeError("CLI did not create a persistent goal")
        listed = run_cli(repo, workspace, "--goal-list")
        if not any(item.get("goal_id") == goal["goal_id"] for item in listed):
            raise RuntimeError("CLI goal persistence check failed")
        after = digest_paths(repo)
        if before != after:
            raise RuntimeError("protected core changed during release validation")
    return {"status": "pass", "version": __version__, "database_integrity": integrity, "fresh_workspace": True, "cli": True, "runtime_lifecycle": True, "security_confinement": True, "protected_core_immutable": True}


def main() -> int:
    parser = argparse.ArgumentParser(description="Run bounded offline Evo V1 release validation")
    parser.add_argument("--repo", default=str(Path(__file__).resolve().parents[1]), help="Evo repository root")
    args = parser.parse_args()
    print(json.dumps(validate(Path(args.repo).resolve()), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
