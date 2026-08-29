#!/usr/bin/env python3
"""Publish and verify Evo's protected byte set, and run the sovereign invariant registry.

Why this is a standalone script rather than only a test: the recovery and release paths
need to answer "was the part of Evo that decides what is allowed, changed?" *before* they
trust anything else, including the package they are checking. So the digest half of this
file loads ``evo_agent/sovereign/protected.py`` by path, without importing ``evo_agent``
and therefore without trusting any of it.

Usage
    python scripts/verify_sovereign_digest.py              # verify; exit 1 on drift
    python scripts/verify_sovereign_digest.py --write      # publish the manifest
    python scripts/verify_sovereign_digest.py --json       # machine-readable report
    python scripts/verify_sovereign_digest.py --report     # per-file digests
    python scripts/verify_sovereign_digest.py --invariants # also run the invariant registry
    python scripts/verify_sovereign_digest.py --gate       # verify + invariants, exit 1 on any failure
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PROTECTED_MODULE = ROOT / "evo_agent" / "sovereign" / "protected.py"


def load_protected_module():
    """Load ``sovereign/protected.py`` directly, bypassing ``evo_agent/__init__``.

    The package's ``__init__`` imports every subsystem, which is the wrong thing to trust
    when the question is whether the tree has been modified. The module is stdlib-only and
    self-contained, so it can be loaded in isolation.
    """
    spec = importlib.util.spec_from_file_location("evo_sovereign_protected", PROTECTED_MODULE)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {PROTECTED_MODULE}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--write", action="store_true", help="publish the manifest for the tree as it stands")
    parser.add_argument("--json", action="store_true", help="emit the report as JSON")
    parser.add_argument("--report", action="store_true", help="list every protected file with its digest")
    parser.add_argument("--invariants", action="store_true", help="also run the sovereign invariant registry")
    parser.add_argument("--gate", action="store_true", help="verify digests and invariants, failing on either")
    arguments = parser.parse_args(argv)

    if not PROTECTED_MODULE.is_file():
        print(f"error: {PROTECTED_MODULE} is missing; the protected-set authority itself is gone", file=sys.stderr)
        return 2
    protected = load_protected_module()

    if arguments.write:
        digests = protected.write_manifest()
        payload = {
            "ok": True,
            "action": "write",
            "algorithm": protected.ALGORITHM,
            "manifest": str(protected.MANIFEST_PATH.relative_to(ROOT)),
            "protected_files": len(digests),
        }
        print(json.dumps(payload, indent=2) if arguments.json else f"published {payload['manifest']} ({len(digests)} files, {protected.ALGORITHM})")
        return 0

    report = protected.verify()
    invariant_results: list[dict[str, object]] = []
    if arguments.invariants or arguments.gate:
        sys.path.insert(0, str(ROOT))
        try:
            from evo_agent.sovereign import run_invariants

            invariant_results = [item.to_dict() for item in run_invariants()]
        except Exception as exc:  # an unimportable package is itself a finding
            invariant_results = [{"code": "I-package-import", "rule": "R1", "ok": False, "detail": f"{type(exc).__name__}: {exc}", "evidence": {}}]

    payload: dict[str, object] = {
        "ok": report.ok and all(bool(item.get("ok")) for item in invariant_results),
        "digests": report.to_dict(),
    }
    if invariant_results:
        payload["invariants"] = invariant_results

    if arguments.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(report.summary())
        if arguments.report:
            width = max((len(name) for name in report.digests), default=4)
            for name, digest in sorted(report.digests.items()):
                mark = "ok" if name in report.matched else ("MISSING" if name in report.missing_files else "CHANGED")
                print(f"  {mark:8}{name.ljust(width)}  {digest[:16]}")
        if invariant_results:
            print("\ninvariants:")
            for item in invariant_results:
                status = "ok" if item.get("ok") else "FAIL"
                print(f"  {status:5}{str(item.get('code')).ljust(24)} {item.get('detail')}")
        if not report.ok:
            print(
                "\nIf this change was intended, re-publish the manifest in the same commit:\n"
                "  python scripts/verify_sovereign_digest.py --write",
                file=sys.stderr,
            )

    if arguments.gate or arguments.invariants:
        if invariant_results and any(not item.get("ok") for item in invariant_results):
            if not arguments.json:
                print("invariant violation", file=sys.stderr)
            return 1
    return 0 if report.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
