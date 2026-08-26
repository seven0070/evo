# Evo V1.0 Real-World Pilot and Release Acceptance

This checklist is for the final validation that cannot be performed in the Linux development sandbox. It must be run on a clean Windows machine or VM and, for the pilot section, with representative personal workloads. Use the installer built from the exact commit under review and preserve the existing governance, approval, sandbox, verifier, promotion, rollback, safe-mode, and kill-switch boundaries.

> **Current status: BLOCKED — EXTERNAL DEVICE REQUIRED.** No Windows 11 machine or clean Windows VM is currently bound to this task. The procedures below are prepared but none of the device-dependent checks may be reported as passed until they are executed on the target environment.

## Preconditions — BLOCKED — EXTERNAL DEVICE REQUIRED

Record the machine model or VM image, Windows version, architecture, WebView2 status, available disk space, Evo artifact name, SHA-256, source commit, and whether the artifact is signed. Create a restore point or VM snapshot where appropriate. Do not use production personal data until the pilot operator has reviewed the local workspace and backup locations.

## Fresh installation — BLOCKED — EXTERNAL DEVICE REQUIRED

Install the NSIS package on a clean machine. Confirm the installer displays the Evo evolution icon, completes without unexpected elevation or network behavior, creates the expected Start Menu and desktop entries if configured, and launches the desktop shell. Confirm the UI shows the Evo evolution logo, the bridge starts locally, and no Python installation is required on the target machine.

Run the status-only path first. Create a harmless local goal such as listing files in the permitted workspace. Confirm Runtime state, verifier evidence, and persisted history. Confirm that an external or risky action remains denied or approval-gated. Exercise safe mode and the kill switch, then verify they remain active across restart until explicitly cleared through the existing authority.

## Upgrade and migration — BLOCKED — EXTERNAL DEVICE REQUIRED

Install the prior known-good build, create a small workspace with task history and a validated backup, then install the candidate build without deleting the workspace. Run status-only inspection and confirm schema migration is forward-only, SQLite integrity is valid, task history remains present, and protected-core digests are unchanged. Run one bounded local task and compare the result with the prior build. Do not force a schema downgrade.

## Rollback and update validation — BLOCKED — EXTERNAL DEVICE REQUIRED

If an update or migration fails, preserve the failed workspace and logs. Stop Evo, validate the known-good backup, restore only after review, reinstall the prior build, and repeat status, integrity, approval, and protected-core checks. Confirm an uncertain external mutation is not replayed automatically. If an update channel is later selected, test signature verification, interruption recovery, version rollback, and user-visible failure behavior before enabling it.

## Security acceptance — BLOCKED — EXTERNAL DEVICE REQUIRED

Attempt shell chaining, redirection, command substitution, absolute-path escape, parent-relative escape, unregistered-tool invocation, forged approval, stale approval reuse, self-approval, malicious tool output, prompt injection, credential-like input, and attempts to modify protected files. Each attempt must be rejected, remain approval-gated, or be contained in the sandbox according to the existing policy. Record only redacted evidence.

For MCP or connector tests, use a disposable test account or local fixture. Confirm external access is default-deny, tool scope is allowlisted, human approval is required for mutating operations, exact scope hashes are enforced, untrusted content is not executed as instructions, and credentials never appear in UI, logs, incident reports, or task history.

## Personal workload pilot — BLOCKED — EXTERNAL DEVICE REQUIRED

Run representative goals of increasing complexity: a local information task, a multi-step workspace task, an ambiguous request requiring clarification, a task that fails and replans, a task that partially succeeds, and a task that encounters a capability gap and routes through the governed evolution path. Confirm each outcome with verifier evidence rather than trusting a textual success message.

Run the pilot for multiple sessions over the intended operating period. Record task category, approval requirement, Runtime state, verifier result, elapsed time, resource pressure, database size, backup status, restart behavior, and any incident report identifier. Stop immediately for unexpected authority changes, integrity warnings, repeated unverified success, approval anomalies, data leakage, or resource exhaustion.

## Evidence and exit criteria — BLOCKED — EXTERNAL DEVICE REQUIRED

A real-world release candidate is accepted only when clean installation, launch, upgrade, migration, rollback, security attempts, backup/restore, representative workloads, and extended operation have all been observed on the target Windows environment. Every failure receives a reproducible issue record and is fixed before the affected gate is rerun. The final report must identify the exact artifact hash, source commit, machine, test operator, dates, failures, fixes, and residual limitations.
