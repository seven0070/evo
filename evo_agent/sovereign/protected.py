"""The protected byte set: which files the agent may never change, and proof that it did not.

Three facts are recorded here and nowhere else, so that the release gate, the runtime
startup check, the desktop bridge and the tests cannot drift apart:

1. **what is protected** — :data:`PROTECTED_PATHS`, paths relative to the ``evo_agent``
   package root;
2. **what "unchanged" means** — SHA-256 of file bytes (:data:`ALGORITHM`), never mtime,
   size, or a git comparison, because those are all gameable by a self-modifying agent;
3. **what a mismatch means** — :class:`SovereignDrift`, raised by :func:`enforce`.

This module is deliberately dependency-free and side-effect-free apart from reading
files. It must stay importable when the rest of the agent is broken: the recovery
path is exactly when you want to know whether the protected set was modified.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
import os
from pathlib import Path
from typing import Any

ALGORITHM = "sha256"
MANIFEST_NAME = "sovereign.manifest.json"
PACKAGE_ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = Path(__file__).resolve().with_name(MANIFEST_NAME)
DRIFT_ENVIRONMENT_VARIABLE = "EVO_ALLOW_SOVEREIGN_DRIFT"

#: Files whose bytes define Evo's trust and evolution authorities. The set is the union
#: of the release gate's historical seven (07 §10 S3), the five governance-adjacent
#: modules the audit found unprotected, and this package itself — a check that lives
#: outside the thing it checks can simply be deleted by the code it checks.
#:
#: Adding a file here is hardening and is always allowed. Removing one, or editing a
#: protected file, requires an explicit re-publication of the manifest
#: (``python scripts/verify_sovereign_digest.py --write``) in the same reviewed change,
#: with the reason in the commit message. That is the whole point: the reviewed act of
#: re-publishing is the human approval, and the digest is its evidence.
#: The explicit list. Everything under ``sovereign/`` is added below by directory walk, so
#: a new guard module is protected the moment it exists - protection cannot be dodged by
#: putting a file in the package that is supposed to be immune to editing.
_EXPLICIT_PROTECTED_PATHS: tuple[str, ...] = (
    # Existing authorities.
    "kernel.py",
    "memory.py",
    "orchestrator.py",
    "promotion.py",
    "runtime.py",
    "sandbox.py",
    "storage.py",
    "security.py",
    "verifier.py",
    # Evolution rules that were previously outside every gate (00-AUDIT §B.6/B.8).
    "benchmark.py",
    "evolver.py",
    "metamorphosis.py",
    # The guard rails themselves (also covered by the walk; listed for readability).
    "sovereign/__init__.py",
    "sovereign/eligibility.py",
    "sovereign/invariants.py",
    "sovereign/protected.py",
    "sovereign/architecture.py",
)


def _sovereign_package_paths() -> tuple[str, ...]:
    here = Path(__file__).resolve().parent
    return tuple(
        f"sovereign/{path.name}"
        for path in sorted(here.glob("*.py"))
        if path.is_file() and "__pycache__" not in path.parts
    )


#: The protected byte set: the explicit authorities plus every module in this package.
PROTECTED_PATHS: tuple[str, ...] = tuple(sorted(set(_EXPLICIT_PROTECTED_PATHS) | set(_sovereign_package_paths())))


class SovereignDrift(RuntimeError):
    """The protected byte set does not match the published manifest."""

    def __init__(self, report: "ProtectionReport") -> None:
        self.report = report
        super().__init__(report.summary())


@dataclass(frozen=True)
class ProtectionReport:
    """Outcome of comparing the tree against the manifest.

    ``ok`` is True only when the manifest exists, every protected file exists, and every
    digest matches. Silence is never a pass: a missing manifest is reported as
    ``manifest_present=False`` with ``ok=False``.
    """

    ok: bool
    manifest_present: bool
    algorithm: str = ALGORITHM
    matched: tuple[str, ...] = ()
    mismatched: tuple[tuple[str, str, str], ...] = ()
    missing_files: tuple[str, ...] = ()
    missing_digests: tuple[str, ...] = ()
    digests: dict[str, str] = field(default_factory=dict)
    package_root: str = ""

    def summary(self) -> str:
        if self.ok:
            return f"protected set verified ({len(self.matched)} files, {self.algorithm})"
        if not self.manifest_present:
            return (
                f"no sovereign manifest at {MANIFEST_PATH.name}; run "
                f"'python scripts/verify_sovereign_digest.py --write' to publish one"
            )
        parts: list[str] = []
        if self.mismatched:
            parts.append(f"{len(self.mismatched)} changed: " + ", ".join(path for path, _, _ in self.mismatched))
        if self.missing_files:
            parts.append(f"{len(self.missing_files)} missing: " + ", ".join(self.missing_files))
        if self.missing_digests:
            parts.append(f"{len(self.missing_digests)} not in manifest: " + ", ".join(self.missing_digests))
        return "sovereign digest mismatch — " + "; ".join(parts)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "manifest_present": self.manifest_present,
            "algorithm": self.algorithm,
            "protected_files": list(PROTECTED_PATHS),
            "matched": list(self.matched),
            "mismatched": [{"path": p, "expected": e, "actual": a} for p, e, a in self.mismatched],
            "missing_files": list(self.missing_files),
            "missing_digests": list(self.missing_digests),
            "digests": dict(self.digests),
            "package_root": self.package_root,
        }


def file_digest(path: Path) -> str:
    """SHA-256 of a file's bytes, streamed so that large files cannot be a memory hazard."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 256), b""):
            digest.update(chunk)
    return digest.hexdigest()


def compute_digests(package_root: Path | None = None) -> dict[str, str]:
    """Digests for every protected file that exists; absent files are simply not listed."""
    root = Path(package_root) if package_root is not None else PACKAGE_ROOT
    digests: dict[str, str] = {}
    for relative in PROTECTED_PATHS:
        candidate = root / relative
        if candidate.is_file():
            digests[relative] = file_digest(candidate)
    return digests


def load_manifest(path: Path | None = None) -> dict[str, str] | None:
    """Return the published digests, or None when the manifest is absent.

    Raises ValueError on a malformed manifest rather than treating it as "no protection":
    a corrupt manifest must not be indistinguishable from an unprotected tree (R7).
    """
    target = Path(path) if path is not None else MANIFEST_PATH
    if not target.is_file():
        return None
    payload = json.loads(target.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or payload.get("algorithm") != ALGORITHM:
        raise ValueError(f"sovereign manifest at {target} is malformed or uses an unexpected algorithm")
    files = payload.get("files")
    if not isinstance(files, dict) or not files:
        raise ValueError(f"sovereign manifest at {target} lists no protected files")
    return {str(name): str(value) for name, value in files.items()}


def write_manifest(package_root: Path | None = None, path: Path | None = None) -> dict[str, str]:
    """Publish the manifest for the tree as it stands. The reviewed act of calling this is
    the human approval that a protected file may change."""
    root = Path(package_root) if package_root is not None else PACKAGE_ROOT
    target = Path(path) if path is not None else MANIFEST_PATH
    digests = compute_digests(root)
    missing = [relative for relative in PROTECTED_PATHS if relative not in digests]
    if missing:
        raise FileNotFoundError(f"cannot publish a manifest; protected files are missing: {', '.join(missing)}")
    payload = {
        "algorithm": ALGORITHM,
        "format": 1,
        "package": "evo_agent",
        "note": "Digests of files the agent may not modify. Regenerate with 'python scripts/verify_sovereign_digest.py --write'.",
        "files": digests,
    }
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(target.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, target)
    return digests


def verify(package_root: Path | None = None, manifest_path: Path | None = None) -> ProtectionReport:
    """Compare the tree to the manifest. Pure read; never raises for a mismatch."""
    root = Path(package_root) if package_root is not None else PACKAGE_ROOT
    try:
        published = load_manifest(manifest_path)
    except (ValueError, json.JSONDecodeError, OSError) as exc:
        # A corrupt manifest is reported as a mismatch against the literal name
        # "<manifest>" so that no caller can mistake it for "nothing to check".
        return ProtectionReport(
            ok=False,
            manifest_present=True,
            mismatched=(("<manifest>", "a readable manifest", f"{type(exc).__name__}: {exc}"),),
            package_root=str(root),
        )
    if published is None:
        return ProtectionReport(ok=False, manifest_present=False, package_root=str(root), digests=compute_digests(root))
    actual = compute_digests(root)
    matched = tuple(sorted(name for name, value in published.items() if actual.get(name) == value))
    mismatched = tuple(sorted((name, value, actual[name]) for name, value in published.items() if name in actual and actual[name] != value))
    missing_files = tuple(sorted(name for name in published if name not in actual))
    # A protected path that the manifest does not cover is a gap, not an accident.
    missing_digests = tuple(sorted(name for name in PROTECTED_PATHS if name not in published))
    return ProtectionReport(
        ok=not (mismatched or missing_files or missing_digests),
        manifest_present=True,
        matched=matched,
        mismatched=mismatched,
        missing_files=missing_files,
        missing_digests=missing_digests,
        digests=actual,
        package_root=str(root),
    )


def enforce(package_root: Path | None = None, manifest_path: Path | None = None, *, allow_drift: bool | None = None) -> ProtectionReport:
    """Verify and refuse to continue on drift (R1 + R7).

    ``allow_drift`` exists for development on a branch where the protected set is being
    deliberately edited. It defaults to the ``EVO_ALLOW_SOVEREIGN_DRIFT`` environment
    variable and callers must record its use; an override that leaves no evidence is not
    an override, it is a bypass.
    """
    report = verify(package_root, manifest_path)
    if report.ok:
        return report
    if allow_drift is None:
        allow_drift = os.environ.get(DRIFT_ENVIRONMENT_VARIABLE, "").strip().lower() in {"1", "true", "yes"}
    if allow_drift:
        return report
    raise SovereignDrift(report)
