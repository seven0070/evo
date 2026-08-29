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
PACKAGE = ROOT / "evo_agent"
MANIFEST = PACKAGE / "sovereign" / "sovereign.manifest.json"
# Historical minimum. The authoritative list is the published manifest, so the gate cannot
# quietly shrink the protected set by editing this file; it can only be caught doing so.
MINIMUM_PROTECTED_FILES = 16


def protected_paths() -> dict[str, str]:
    """The protected set, straight from the published manifest.

    Deliberately no ``import evo_agent`` here: the gate is the thing that runs when the
    package may be broken, so it reads the manifest as data.
    """
    if not MANIFEST.is_file():
        raise FileNotFoundError(f"missing {MANIFEST}; run scripts/verify_sovereign_digest.py --write")
    payload = json.loads(MANIFEST.read_text(encoding="utf-8"))
    files = payload.get("files")
    if not isinstance(files, dict) or len(files) < MINIMUM_PROTECTED_FILES:
        raise ValueError(f"{MANIFEST.name} covers {len(files) if isinstance(files, dict) else 0} files, expected at least {MINIMUM_PROTECTED_FILES}")
    return {str(name): str(digest) for name, digest in files.items()}


def digest() -> dict[str, str]:
    """Digests of the protected files as they are on disk right now."""
    observed: dict[str, str] = {}
    for name in protected_paths():
        path = PACKAGE / name
        observed[name] = hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else "<missing>"
    return observed


def published_digest() -> dict[str, str]:
    return protected_paths()


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
        # The manifest must survive packaging: an installed agent that cannot verify its own
        # protected set has no protection at all.
        run_step(
            report,
            "clean_install_sovereign_manifest",
            [
                str(python), "-c",
                "from evo_agent.sovereign.protected import load_manifest, verify;"
                "assert load_manifest() is not None, 'manifest not packaged';"
                "assert verify().ok, 'protected set mismatch in the installed package'",
            ],
        )
        executable = target / ("Scripts" if sys.platform == "win32" else "bin") / ("evo.exe" if sys.platform == "win32" else "evo")
        run_step(report, "clean_install_cli", [str(executable), "--help"])


def main() -> int:
    parser = argparse.ArgumentParser(description="Run Evo's local-first production release gate")
    parser.add_argument("--allow-dirty", action="store_true", help="Do not fail only because the repository has uncommitted changes")
    args = parser.parse_args()
    published = published_digest()
    report: dict[str, object] = {
        "status": "running",
        "repository": str(ROOT),
        "python": sys.version,
        "protected_core_published": published,
        "protected_core_before": digest(),
        "protected_core_matches_manifest_before": digest() == published,
        "steps": {},
    }
    run_step(report, "compileall", [sys.executable, "-m", "compileall", "-q", "evo_agent", "tests", "scripts"])
    run_step(report, "full_tests", [sys.executable, "-m", "pytest", "-q"])
    run_step(report, "sovereign_invariants", [sys.executable, "scripts/verify_sovereign_digest.py", "--gate"])
    run_step(report, "release_validator", [sys.executable, "scripts/validate_v1.py"])
    run_step(report, "readiness_matrix", [sys.executable, "scripts/run_v1_readiness.py"])
    run_step(report, "offline_pilot", [sys.executable, "scripts/run_v1_pilot.py"])
    clean_install(report)
    run_step(report, "diff_check", ["git", "diff", "--check"])
    run_step(report, "cached_diff_check", ["git", "diff", "--cached", "--check"])
    report["protected_core_after"] = digest()
    report["protected_core_unchanged"] = report["protected_core_before"] == report["protected_core_after"]
    report["protected_core_matches_manifest_after"] = report["protected_core_after"] == published
    status = (
        all(bool(item.get("ok")) for item in report["steps"].values())
        and bool(report["protected_core_unchanged"])
        and bool(report["protected_core_matches_manifest_before"])
        and bool(report["protected_core_matches_manifest_after"])
    )
    if not args.allow_dirty:
        status = status and not bool(subprocess.run(["git", "status", "--porcelain"], cwd=ROOT, capture_output=True, text=True, check=False).stdout.strip())
    report["status"] = "passed" if status else "failed"
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if status else 1


if __name__ == "__main__":
    raise SystemExit(main())
