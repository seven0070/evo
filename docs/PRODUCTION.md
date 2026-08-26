# Evo production hardening

This document defines the approved production path for Evo. The target is a **local-first, self-contained personal agent** that is operationally reliable without introducing a second execution engine or a second authority plane.

## Approved architecture

```text
Windows desktop / CLI / bounded supervisor
                    |
                    v
          Existing AgentRuntime
                    |
       Existing Kernel + Governance + Verifier
                    |
      Existing SQLiteStore in the workspace
                    |
  Operational journal / metrics / migrations / backups
```

The existing `AgentRuntime` remains the only execution scheduler and the existing Kernel remains the only tool execution authority. Governance, approval, workspace confinement, shell restrictions, verification, safe mode, kill switch, Evolution, Sandbox, Benchmark, Promotion, and Rollback remain authoritative. Production operations observe and supervise these components; they do not replace them or infer success independently.

No Temporal, Prefect, Celery, hosted worker, localhost HTTP service, or parallel task database is introduced for the personal production path. Durable execution means that Runtime task state, approval state, evolution state, operational transitions, and recovery evidence are persisted in the existing SQLite database and reconciled on restart. A supervisor may run an explicitly bounded number of Runtime cycles, but it has no default unattended infinite loop.

## Operational components

| Component | Responsibility | Authority boundary |
|---|---|---|
| `ProductionConfig` | Strict, non-secret operational limits and paths | Cannot weaken personal profile or Runtime limits |
| `OperationalJournal` | Structured lifecycle records, counters, and failure evidence | Observability only; stored in the existing SQLite database |
| `ProductionHealth` | Database, Runtime, queue, safe-mode, kill-switch, and resource health | Reports state; cannot clear safety controls |
| `ProductionSupervisor` | Process lock, startup reconciliation, bounded cycle execution, graceful stop | Delegates every task cycle to `AgentRuntime` |
| `BackupManager` | Atomic SQLite backup, integrity validation, retention | Copies authoritative state; never rewrites it |
| `CrashReporter` | Local atomic redacted incident records with bounded retention | Observational only; never executes, approves, promotes, or clears controls |
| `SchemaManager` | Versioned operational metadata and forward-only migrations | Fails closed on unsupported versions |
| `SecurityAudit` tests | Adversarial checks for tool, approval, sandbox, injection, and secret boundaries | Test-only; no runtime authority |

## Durability contract

Every supervisor run records a start record before invoking Runtime and a terminal record after the Runtime result is persisted. An unexpected supervisor exception also creates a bounded, redacted local incident file under `.evo/incidents`; incident reporting is observational and does not change the failure outcome. Startup marks an unclosed prior run as interrupted and relies on Runtime’s existing crash-recovery logic to revalidate tasks. An interrupted external mutating operation remains unknown and is not replayed automatically. An interrupted Evolution or Promotion operation is reconciled through the existing persisted state and downstream integrity checks; no production mutation is inferred from an incomplete record.

Operational writes use the same SQLite file as Runtime and are bounded in size. The journal is not a second event stream for execution semantics; it is a compact operational view linked to the existing Runtime cycle and task identifiers. Full authoritative task, event, evolution, benchmark, promotion, and rollback records remain in their existing tables.

## Configuration contract

Production configuration is JSON or programmatic data only. A safe starting point is [`config/production.example.json`](../config/production.example.json):

```bash
mkdir -p ./workspace/.evo
cp config/production.example.json ./workspace/.evo/production.json
evo --production-status --workspace ./workspace
```

It contains bounded cycle counts, sleep intervals, backup retention, health thresholds, and log settings. It cannot contain credentials, provider secrets, arbitrary shell commands, connector mutation instructions, or authority overrides. Personal profile validation remains the stronger boundary: production limits may only tighten profile-derived Runtime limits.

## Release gates

The Windows packaging script emits `release-manifest.json` beside the NSIS and MSI artifacts. The manifest records the source commit, toolchain versions, artifact sizes, and SHA-256 hashes. It is uploaded with the installers by the Windows workflow.


Production readiness requires the existing full regression suite plus operational tests, adversarial security tests, restart and corruption tests, repeated evolution and rollback tests, bounded soak tests, clean-install and upgrade checks, reproducible artifact checks, and a real Windows pilot. A Linux sandbox can validate source behavior and native packaging, but it cannot substitute for Windows installation, SmartScreen, WebView2, code-signing, or extended real-user workload evidence.

The first production release remains **unsigned** unless a user-supplied Windows code-signing certificate and signing policy are provided. Signing is a release operation, not something the agent may invent or bypass.
