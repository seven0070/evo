# Evo V1 local pilot runbook

The V1 pilot is a controlled offline exercise over the frozen Phase 1–20 architecture. It is finite, uses a temporary workspace, does not require provider credentials, and does not approve, promote, deploy, or mutate production candidates.

## Prepare a real local-user pilot

Use [`pilot/v1_user_pilot_template.json`](../pilot/v1_user_pilot_template.json) to record 15 candidate cases. The first five are safe deterministic examples; the remaining slots are intentionally marked `pending_user_input` so the operator can supply representative goals without the agent inventing them. Record actual outcomes in [`pilot/v1_user_results_template.md`](../pilot/v1_user_results_template.md). Use a dedicated non-production workspace and remove or protect any copy containing sensitive operator data.

## Run the corpus

From the repository root:

```bash
PYTHONPATH=. python3 scripts/run_v1_pilot.py \
  --corpus pilot/v1_task_corpus.json \
  --output /tmp/evo_v1_pilot_report.json
```

The corpus version is recorded in `pilot/v1_task_corpus.json`. It covers simple read-only execution, case-sensitive multi-step reads, exact human approval, bounded shell execution, memory and experience persistence, safe-mode blocking, restart recovery, backup integrity, sustained Runtime cycles, and protected-core immutability. The user-intake template is separate from the deterministic acceptance corpus so real goals cannot silently change the reproducible baseline.

## Interpret the report

| Metric | Interpretation |
|---|---|
| `status` | The pilot’s expected outcomes and safety invariants passed only when this is `pass`. |
| `records` | Per-case status, verification, approval, and recovery observations. Intermediate `READY` states are acceptable only when bounded follow-up cycles reach the expected terminal outcome. |
| `startup_seconds` or `pilot_seconds` | Local diagnostic timing, not a cross-machine performance claim. |
| `restart_database_integrity` | SQLite and persisted JSON validation after restart. |
| `backup_database_integrity` | Validation of a byte-for-byte copied SQLite backup. |
| `protected_core_immutable` | Digest comparison for Kernel, Security, Verifier, Runtime, Promotion, Metamorphosis, and Orchestrator source files. |
| `bounded` | Confirms that sustained cycles remained within known Runtime lifecycle states. |

A waiting approval, safe-mode waiting task, or recovered interrupted task is not a failure when it matches the case expectation. A task is successful only when the existing Verifier and Kernel evidence say so; process completion or queue completion alone is insufficient.

## Triage protocol

For any failed case, preserve the JSON report and workspace artifacts, then classify the issue as one of **implementation bug**, **policy or approval decision**, **missing capability**, **environment difference**, **evidence/verification problem**, or **operator usability issue**. Reproduce in a fresh temporary workspace before changing code. Never repair a failed run by editing success states into SQLite.

For repeated failures, use the existing Experience, Evaluation, Flexibility, Learning, and governed Evolution evidence paths. Any candidate change must continue through the existing human approval, isolated sandbox, benchmark, promotion, health verification, and rollback gates.

## Cleanup and backup

The runner uses a temporary workspace and removes it on exit. For an operational pilot using a persistent workspace, stop the Runtime before copying `.evo/agent.sqlite3`, validate the copy with `SQLiteStore.validate_database_integrity()`, and retain the original backup read-only. Do not delete checkpoints or audit history as a substitute for triage.
