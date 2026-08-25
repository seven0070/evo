# Evo V1 release checklist

Evo V1 freezes the Phase 1–20 capability set. Release work hardens and verifies the existing architecture; it does not add Phase 21 or any new intelligence, authority, self-modification, or deployment subsystem.

## Clean installation

Run from a fresh checkout with Python 3.11 or newer:

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e '.[dev]'
python -c 'from evo_agent.version import __version__; assert __version__ == "1.0.0"; print(__version__)'
```

The package must install without runtime dependencies for offline operation. The `evo` console script must be available after activation:

```bash
evo --help
evo --json --workspace /tmp/evo-v1-workspace --goal-create "inspect a local release artifact"
```

## Reproducible validation

From the repository root, run the bounded offline release validator:

```bash
python scripts/validate_v1.py
```

Then run the complete test suite, compilation, and patch checks:

```bash
PYTHONPATH=. pytest -q
python3 -m compileall -q evo_agent tests scripts

git diff --check
git diff --cached --check
```

The release validator checks stable version metadata, fresh SQLite schema integrity, bounded security policy behavior, fresh-workspace CLI persistence, deterministic Runtime startup/shutdown, and protected-core immutability. The test suite additionally covers the full cross-phase integration, failure injection, restart behavior, approval boundaries, false-success prevention, production immutability, and governed evolution/promotion/rollback paths.

## Release gate

A release is eligible only when all tests pass, the fresh-install check passes, CLI smoke checks pass, restart/recovery checks pass, protected-core and production immutability checks pass, no unapproved external or provider operation is required, and the working tree is clean. The release commit must be pushed and fetched so that `HEAD` equals `origin/main`.

> BUILD → VERIFY → HARDEN → RELEASE.

## V1 limitations

V1 is intentionally local-first, bounded, and approval-aware. It does not provide unrestricted machine access, arbitrary code execution, arbitrary plugins or providers, credential acquisition, autonomous approval or promotion, autonomous deployment, uncontrolled agent spawning or reflection loops, unrestricted network access, replication, production mutation, or self-authorized changes to Governance, Verification, Runtime, Kernel, Promotion, Rollback, or other protected authorities. External integrations and non-offline model providers remain opt-in and subject to their existing policy and health gates.
