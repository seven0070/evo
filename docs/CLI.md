# Evo V1 CLI

Evo exposes one local command named `evo`. The default adapter is offline and deterministic, so inspection and read-only workflows do not require credentials or network access. Every operation uses the selected workspace and persists state in `<workspace>/.evo/agent.sqlite3`.

## Basic execution

```bash
evo --workspace ./workspace "list the files in the workspace"
evo --legacy-kernel --workspace ./workspace "list the files in the workspace"
evo --json --workspace ./workspace "list the files in the workspace"
```

The Cognitive path is the default. `--legacy-kernel` is retained for compatibility and uses the original bounded Kernel path. A non-read-only operation may pause for explicit approval; a failed or incomplete result is not converted into success by the CLI.

## Inspection commands

| Concern | Commands |
|---|---|
| Execution evidence | `--list-experiences`, `--show-experience`, `--show-evaluation` |
| Cognitive state | `--run-goal`, `--show-goal`, `--show-plan`, `--show-task`, `--show-cognitive-state`, `--clarify-goal` |
| Memory | `--list-memory`, `--search-memory`, `--memory-history`, `--memory-provenance`, `--memory-integrity`, `--memory-stats` |
| Capabilities and world | `--list-capabilities`, `--find-capability`, `--list-tools`, `--find-tools`, `--show-environment`, `--show-world-state`, `--environment-diff` |
| External integrations | `--list-integrations`, `--external-health`, `--list-external-operations`, `--external-stats` |
| Specialists and models | `--list-specialists`, `--specialist-health`, `--list-models`, `--model-health`, `--find-models` |
| Learning and self-model | `--learning-status`, `--learning-cycle`, `--self-model`, `--self-model-status`, `--self-diagnostics` |
| Governed change | `--list-proposals`, `--list-experiments`, `--list-benchmarks`, `--list-versions`, `--list-metamorphosis`, `--list-work-items` |

## Runtime controls

Runtime operations are bounded and persistent. Use `--runtime-start`, `--runtime-status`, `--runtime-cycle`, `--runtime-pause`, `--runtime-resume`, `--runtime-stop`, `--runtime-safe-mode`, and `--runtime-kill-switch` to inspect or control the lifecycle. A kill switch is intentionally not removable through normal runtime operation. Approval is exact-task and exact-environment scoped; an old approval cannot authorize a changed task or environment.

## Strategic goal inspection

Phase 20 strategic controls are available for persistent goal records and advisory coordination:

```bash
evo --goal-create "prepare a verified local report" --workspace ./workspace
evo --goal-list --workspace ./workspace
evo --goal-show GOAL_ID --workspace ./workspace
evo --goal-prioritize --workspace ./workspace
evo --goal-plan GOAL_ID --workspace ./workspace
evo --goal-progress GOAL_ID --workspace ./workspace
evo --goal-blockers GOAL_ID --workspace ./workspace
evo --goal-strategy GOAL_ID --workspace ./workspace
evo --goal-alternatives GOAL_ID --workspace ./workspace
evo --goal-reassess GOAL_ID --workspace ./workspace
evo --goal-conflicts --workspace ./workspace
evo --goal-decisions GOAL_ID --workspace ./workspace
evo --goal-verify GOAL_ID --workspace ./workspace
```

Strategic commands recommend and record; they do not approve, execute, verify, promote, deploy, or mutate production. Goal verification requires explicit authoritative verified evidence.

## Structured output and errors

Use `--json` for automation and inspection scripts. JSON output is bounded and intended to contain metadata, identifiers, statuses, and evidence summaries rather than credentials, unrestricted prompts, complete model output, or executable content. Non-zero process status indicates that a requested execution did not achieve its authoritative success condition or that a CLI error occurred.

> The CLI is an interface to existing authorities, not an additional authority channel.
