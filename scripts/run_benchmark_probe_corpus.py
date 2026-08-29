#!/usr/bin/env python3
"""Run every benchmark-v2 probe body against the tree it is executed in, and report which ones fail.

`03` §I.4 asks for a nightly full-benchmark step, and the first thing a nightly should be able to say is
whether the *corpus itself* is sound: a probe that fails on an unmodified tree is not a strict test, it is a
broken measurement, and it will report every candidate as a regression. The benchmark engine writes these same
bodies into a sandbox copy of the source and runs them there; this script runs them here, which makes the
corpus reviewable without staging a candidate.

Usage:
    python3 scripts/run_benchmark_probe_corpus.py            # run all 32 bodies
    python3 scripts/run_benchmark_probe_corpus.py --suite core-local
    python3 scripts/run_benchmark_probe_corpus.py --json

Exit code is 0 when every probe passes, 1 otherwise - the same shape the other gates in this directory use.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from evo_agent.benchmark_suites import PROBE_BODIES, SUITES  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--suite", action="append", default=None, help="limit to these suite names (repeatable)")
    parser.add_argument("--json", action="store_true", help="emit a JSON report instead of one line per probe")
    parser.add_argument("--timeout", type=int, default=300, help="seconds allowed per probe process")
    args = parser.parse_args(argv)

    wanted = PROBE_BODIES
    if args.suite:
        wanted = {}
        for name in args.suite:
            if name not in SUITES:
                print(json.dumps({"ok": False, "error": f"unknown suite {name!r}", "suites": sorted(SUITES)}))
                return 2
            for case in SUITES[name].cases:
                if case.probe in PROBE_BODIES:
                    wanted[case.probe] = PROBE_BODIES[case.probe]

    results: list[dict[str, object]] = []
    for probe, body in sorted(wanted.items()):
        directory = Path(tempfile.mkdtemp(prefix="probe-"))
        target = directory / "test_probe.py"
        target.write_text(body, encoding="utf-8")
        try:
            completed = subprocess.run(
                [sys.executable, "-m", "pytest", str(target), "-q", "-p", "no:randomly", "--no-header"],
                cwd=ROOT,
                capture_output=True,
                text=True,
                timeout=args.timeout,
            )
            code, out = completed.returncode, (completed.stdout or "") + (completed.stderr or "")
        except subprocess.TimeoutExpired:
            code, out = 124, "timed out"
        results.append({"probe": probe, "ok": code == 0, "returncode": code, "output_tail": out.strip().splitlines()[-12:]})

    failed = [item for item in results if not item["ok"]]
    if args.json:
        print(json.dumps({"ok": not failed, "total": len(results), "failed": len(failed), "results": results}, indent=2))
    else:
        for item in results:
            print(f"{'ok  ' if item['ok'] else 'FAIL'} {item['probe']}")
            if not item["ok"]:
                for line in item["output_tail"]:  # type: ignore[index]
                    print(f"     {line[:200]}")
        print(f"\n{len(failed)} of {len(results)} probes failed against {ROOT}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
