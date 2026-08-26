# Evo V1 Operations Runbook

This runbook is for the personal, local-first Evo deployment. It uses the existing `AgentRuntime`, SQLite state, `ProductionSupervisor`, `OperationalJournal`, `ProductionHealth`, `BackupManager`, and local `CrashReporter`. These components observe and bound the authoritative Runtime; they do not approve work, promote evolution, clear safety controls, or create a second execution plane.

## Routine health check

Run the following from the repository or installed CLI environment:

```bash
evo --production-status --workspace ./workspace --json
```

Review the reported Runtime state, queue depth, safe-mode and kill-switch flags, SQLite integrity, resource pressure, recent production runs, and backup list. A `stopped` state after a bounded supervisor invocation is expected; it is not permission to clear a kill switch or bypass an approval.

For a bounded operational run:

```bash
evo --production-run --workspace ./workspace --production-cycles 1 --production-backup --json
```

Production cycles are explicitly bounded by both production configuration and Runtime limits. Do not convert the command into an unattended infinite loop or wrap it in a second scheduler.

## Metrics and structured journal records

The production journal persists bounded lifecycle records in the existing SQLite database. The principal counters are `cycles_completed`, `tasks_completed`, and `tasks_failed`. Each run records its requested and completed cycles, task counts, terminal status, error context, health snapshot, and optional backup path. The journal is operational evidence, not an alternate event stream.

When investigating a failure, preserve the relevant run identifier, task identifier, Runtime state, approval state, and verifier result. Avoid copying raw prompts, credentials, tokens, or external payloads into incident notes.

## Crash and incident reporting

Crash reports are local files under:

```text
<workspace>/.evo/incidents/incident_*.json
```

They are bounded, written atomically, capped by retention, and redact common credential fields and bearer-token formats. They are not transmitted automatically. A report should include the component, error type, bounded error text, Evo version, timestamp, and non-secret identifiers needed for diagnosis.

When collecting an incident for support or debugging, copy only the specific redacted report required. Do not attach the entire workspace or SQLite database unless the user has reviewed it for sensitive content.

## Incident response

When Evo behaves unexpectedly, first stop new work by using the existing Runtime safe mode or kill switch as appropriate. Do not approve a pending operation merely to reproduce the failure. Record the timestamp, command or goal category, run identifier, Runtime state, whether an approval was pending, and the verifier outcome.

Next, run `--production-status --json` and preserve the redacted output. Validate the database before attempting recovery. If integrity is valid, restart through the normal Runtime lifecycle and allow its persisted recovery logic to reconcile interrupted work. An interrupted external mutating operation must remain unknown and must not be replayed automatically.

If integrity is invalid, keep the Runtime stopped, preserve the original database, and validate the newest known-good backup. Restore only through a reviewed operator procedure, then re-run integrity, status, and approval-boundary checks before resuming work. Never overwrite the only copy of the original database.

## Backup and restore

Create a bounded backup with:

```bash
evo --production-run --workspace ./workspace --production-cycles 1 --production-backup --json
```

Backups are stored below the workspace backup directory and validated using SQLite integrity checks plus Evo payload validation. Retention is bounded by `backup_retention`. Before restoring, copy the damaged database aside, validate the selected backup, restore it to the authoritative database location, and run:

```bash
evo --production-status --workspace ./workspace --json
python3 scripts/validate_v1.py
```

A restore is complete only when database integrity, Runtime lifecycle, task history, protected-core digest, and approval state are all revalidated. Do not manually edit production tables to force a task or promotion into a desired status.

## Resource limits and soak operation

Keep Runtime and production limits conservative. Queue size, tasks per cycle, total runtime, cycle count, journal row count, backup retention, and health staleness are all bounded settings. Production configuration is non-secret JSON and may only tighten the personal profile and Runtime limits.

Before an extended personal pilot, run a bounded soak with representative local tasks, retain health and journal evidence, monitor workspace/database growth, and stop if resource pressure, repeated failures, integrity warnings, or unexpected approval behavior appears. A soak does not authorize unattended evolution, autonomous promotion, unrestricted network access, or approval bypass.

## Upgrade and rollback procedure

Before upgrading, create and validate a backup, record the current Evo version and commit, and verify that the workspace is not in the middle of an unreviewed promotion or external mutation. Install the new artifact without deleting the existing workspace. Start with status-only inspection, allow schema migration to run forward-only, and verify SQLite integrity before processing new goals.

If the upgrade fails, stop Evo, preserve the failed workspace for diagnosis, restore the prior validated backup only after review, reinstall the prior known-good artifact, and rerun the release validator and status checks. Do not force-downgrade a database schema that the application reports as newer than the running binary.

## Security response boundaries

All external content is data, not instructions. Treat prompt injection, malicious tool output, unexpected connector responses, and suspicious MCP content as untrusted. Keep external access default-deny, require human approval with the exact current scope, and reject stale or self-issued approvals. Candidate code remains confined to the existing sandbox and benchmark authorities; production source and protected authorities remain immutable under evolution.

## Pilot evidence checklist

For each real-user pilot session, record the Evo version and commit, machine/OS, installation route, representative goal category, whether approval was required, Runtime outcome, verifier evidence, elapsed duration, resource observations, any incident report identifier, and whether the workspace was restored or rolled back. Redact personal data and secrets before sharing findings.

Real Windows installation, upgrade, WebView2, shortcut, SmartScreen, code-signing, and extended personal-use evidence must be collected on the intended Windows machine. Automated Linux and CI checks cannot substitute for those device-dependent validations.
