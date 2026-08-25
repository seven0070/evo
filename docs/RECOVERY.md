# Evo V1 recovery and rollback

Evo treats recovery as a safety operation. Restart does not assume that an interrupted action completed, and no recovery path bypasses the existing Kernel, Verifier, Governance, approval, sandbox, benchmark, promotion, or rollback authority.

## Startup and restart

On startup, Evo opens the existing workspace database, validates SQLite integrity and persisted structured payloads, validates the source root and architecture context, observes the environment, and then recovers only bounded persisted Runtime state. Interrupted Runtime tasks are requeued for revalidation or marked safely failed/inconclusive according to their execution semantics. An interrupted external, specialist, model, learning, or strategic operation is never silently treated as successful.

```bash
evo --runtime-start --workspace ./workspace
evo --runtime-status --workspace ./workspace
evo --runtime-cycle --workspace ./workspace
evo --runtime-list-tasks --workspace ./workspace
```

Malformed persisted payloads cause startup validation to fail closed. Preserve the original database for investigation, copy it before any repair, and restore from a known-good backup rather than manually manufacturing success records.

## Runtime safety states

`PAUSED` stops new execution until the operator resumes after revalidation. Safe mode allows only explicitly safe read-only behavior and leaves side-effecting work waiting. A kill switch stops the Runtime and remains active across restart; normal Runtime operations cannot clear it. Degraded or failed health causes bounded execution to stop and requires operator investigation.

```bash
evo --runtime-pause --workspace ./workspace
evo --runtime-safe-mode --workspace ./workspace
evo --runtime-kill-switch --workspace ./workspace
evo --runtime-health --workspace ./workspace
```

Approval is exact-task and exact-environment scoped. If the environment, goal, resource context, or material intent changes, the old approval is invalidated and a new human decision is required.

## Kernel checkpoints and task recovery

The Kernel records task, tool, observation, verification, and recovery events in SQLite. Workspace checkpoints provide restoration for supported local file operations. Verification is authoritative: a completed process, successful tool return, or queue status does not prove that the requested postcondition holds.

## Governed evolution rollback

Evolution and Metamorphosis candidates remain outside production until the existing sequence is complete:

```text
Proposal -> Human Approval -> Isolated Sandbox -> Benchmark -> Evidence
-> Separate Promotion Approval -> Atomic Promotion -> Health Verification
-> Native Rollback if health fails
```

A failed experiment or benchmark is retained as evidence and is not retried without bounded policy. A failed promotion invokes the existing PromotionEngine rollback path, restores the previous known-good version atomically, verifies the active pointer and manifest, and preserves the complete audit lineage. Strategic and adaptive-learning recommendations cannot promote or roll back candidates themselves.
