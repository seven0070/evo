from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import venv


ROOT = Path(__file__).resolve().parents[1]
PROTECTED = (
    ROOT / "evo_agent" / "security.py",
    ROOT / "evo_agent" / "kernel.py",
    ROOT / "evo_agent" / "runtime.py",
    ROOT / "evo_agent" / "verifier.py",
    ROOT / "evo_agent" / "sandbox.py",
    ROOT / "evo_agent" / "promotion.py",
    ROOT / "evo_agent" / "storage.py",
)


def digest() -> dict[str, str]:
    return {str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest() for path in PROTECTED}


def run_step(report: dict[str, object], name: str, command: list[str], cwd: Path = ROOT) -> None:
    completed = subprocess.run(command, cwd=cwd, capture_output=True, text=True, check=False)
    report.setdefault("steps", {})[name] = {"ok": completed.returncode == 0, "returncode": completed.returncode, "stdout": completed.stdout[-4000:], "stderr": completed.stderr[-4000:]}


def clean_install(report: dict[str, object]) -> None:
    with tempfile.TemporaryDirectory(prefix="evo-clean-install-") as raw:
        target = Path(raw) / "venv"
        venv.EnvBuilder(with_pip=True, clear=True).create(target)
        python = target / ("Scripts" if sys.platform == "win32" else "bin") / "python"
        run_step(report, "clean_install", [str(python), "-m", "pip", "install", "--no-deps", str(ROOT)])
        run_step(report, "clean_install_import", [str(python), "-c", "import evo_agent; from evo_agent.version import __version__; assert __version__ == '1.0.0'"])
        executable = target / ("Scripts" if sys.platform == "win32" else "bin") / ("evo.exe" if sys.platform == "win32" else "evo")
        run_step(report, "clean_install_cli", [str(executable), "--help"])


def main() -> int:
    parser = argparse.ArgumentParser(description="Run Evo's local-first production release gate")
    parser.add_argument("--allow-dirty", action="store_true", help="Do not fail only because the repository has uncommitted changes")
    args = parser.parse_args()
    report: dict[str, object] = {"status": "running", "repository": str(ROOT), "python": sys.version, "protected_core_before": digest(), "steps": {}}
    run_step(report, "compileall", [sys.executable, "-m", "compileall", "-q", "evo_agent", "tests", "scripts"])
    run_step(report, "full_tests", [sys.executable, "-m", "pytest", "-q"])
    run_step(report, "release_validator", [sys.executable, "scripts/validate_v1.py"])
    run_step(report, "readiness_matrix", [sys.executable, "scripts/run_v1_readiness.py"])
    run_step(report, "offline_pilot", [sys.executable, "scripts/run_v1_pilot.py"])
    clean_install(report)
    run_step(report, "diff_check", ["git", "diff", "--check"])
    run_step(report, "cached_diff_check", ["git", "diff", "--cached", "--check"])
    report["protected_core_after"] = digest()
    report["protected_core_unchanged"] = report["protected_core_before"] == report["protected_core_after"]
    status = all(bool(item.get("ok")) for item in report["steps"].values()) and bool(report["protected_core_unchanged"])
    if not args.allow_dirty:
        status = status and not bool(subprocess.run(["git", "status", "--porcelain"], cwd=ROOT, capture_output=True, text=True, check=False).stdout.strip())
    report["status"] = "passed" if status else "failed"
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if status else 1


if __name__ == "__main__":
    raise SystemExit(main())
