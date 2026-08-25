# Evo V1 operational readiness

The readiness milestone extends the seven-case offline pilot with twelve bounded operational checks. It is designed to answer whether the frozen V1 architecture is ready for controlled local use, not whether another intelligence layer is needed.

## Run the readiness matrix

```bash
PYTHONPATH=. python3 scripts/run_v1_readiness.py \
  --corpus pilot/v1_task_corpus.json \
  --output /tmp/evo_v1_readiness_report.json
```

The runner executes a **19-case readiness workload**: seven end-to-end corpus cases plus twelve operational checks for malformed goals, deadline expiry, idempotent duplicate admission, queue backpressure, kill-switch persistence, corrupted persistence, oversized event bounding, secret redaction, status hygiene, backup restoration, sustained-cycle resource behavior, and CLI consistency. All work is local, finite, and offline.

## Read the result

| Result | Meaning |
|---|---|
| `status=pass` | Every readiness check and every corpus expectation passed. |
| `passed` / `failed` | Counts for the twelve operational checks plus the aggregate pilot check. |
| `pilot_seconds` | Timing for the temporary pilot workload on the current machine; use it as a local baseline only. |
| `restart_database_integrity` | SQLite and persisted payload validation after restart. |
| `backup_database_integrity` | Validation of the copied SQLite backup. |
| `protected_core_immutable` | Digest comparison across the protected execution and governance surfaces. |

A successful readiness result does not authorize autonomous production use or bypass human approval. It means that the tested local workflows behave as expected under the recorded policy and environment. Real operator workloads should still be piloted with the same report and triage protocol.

## Restore rehearsal

For a persistent workspace, stop Evo before copying `.evo/agent.sqlite3`. Restore the copy into a clean workspace, open it with the same package version, run `SQLiteStore.validate_database_integrity()`, start Runtime, inspect status, and verify that task and audit history remain available. Keep the original backup unchanged until the restore is accepted.

## Release decision

Proceed to a formal V1 tag only when the readiness matrix, complete regression suite, fresh installation, CLI smoke checks, protected-core immutability, production immutability, and backup/restore rehearsal all pass. Any failure is triaged as an implementation bug, policy decision, missing capability, environment difference, evidence-quality issue, or operator-usability issue before changing the architecture.
