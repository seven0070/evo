# Evo Agent

Evo Agent is a local-first, permissioned, provider-neutral AI-agent kernel. The first milestone intentionally focuses on a reliable execution foundation; controlled self-improvement is deferred until the MVP is tested and observable.

## Current MVP

The MVP provides a command-line interface, a stable model-adapter interface, sequential task planning, an allowlisted workspace, safe workspace file tools, a permissioned shell tool, explicit approval for medium- and high-risk actions, deterministic postcondition verification, bounded retry and recovery events, SQLite task/event/memory storage, structured logs, and workspace checkpoints with rollback.

The default `offline` adapter is deterministic and requires no model API. An optional OpenAI-compatible adapter can be selected later with a model ID and provider endpoint. The kernel does not depend on a specific provider.

## Safety boundaries

The agent can access only the configured workspace. Relative and absolute paths are resolved and rejected if they escape that workspace. Shell commands run with the workspace as their working directory, must begin with an allowlisted executable, and are subject to a timeout. Medium-, high-, and critical-risk tools require an approval callback. No unrestricted machine access, automatic external communication, credentials management, production deployment, or self-modification is included in this milestone.

> Evolution is deliberately implemented as a future proposal-and-evaluation workflow, not as uncontrolled runtime self-editing.

## Setup

```bash
cd evo
python3 -m venv .venv
. .venv/bin/activate
pip install -e '.[dev]'
```

To enable an OpenAI-compatible provider adapter:

```bash
pip install -e '.[llm]'
```

## Usage

Run the offline adapter:

```bash
evo --workspace ./workspace 'list the files in the workspace'
```

A task that writes a file will pause for approval:

```bash
evo --workspace ./workspace 'write this goal'
```

For machine-readable output:

```bash
evo --json --workspace ./workspace 'list the files in the workspace'
```

The CLI stores local state under `<workspace>/.evo/agent.sqlite3` and checkpoint snapshots under `<workspace>/.evo/checkpoints/`.

## Project structure

| Component | Responsibility |
|---|---|
| `models.py` | Typed task, plan, tool, event, verification, and outcome records |
| `model_adapter.py` | Provider-neutral interface, offline adapter, and OpenAI-compatible adapter |
| `security.py` | Workspace confinement, shell allowlist, risk classification, and approval policy |
| `tools.py` | Workspace file and permissioned shell tools |
| `kernel.py` | Plan → approve → execute → observe → verify → recover orchestration |
| `storage.py` | SQLite tasks, events, memories, and checkpoint metadata |
| `checkpoints.py` | Snapshot and rollback operations |
| `verifier.py` | Deterministic postcondition checks |
| `cli.py` | Local command-line entry point |

## Verification

```bash
python3 -m pytest -q
```

The current test suite covers workspace traversal protection, shell allowlisting, approval blocking, approved end-to-end execution, memory persistence, and checkpoint rollback.

## Next milestone: controlled evolution

Only after the MVP has broader end-to-end coverage should the project add an experience recorder, benchmark datasets, an evaluator, an evolution proposal format, isolated candidate workspaces, promotion gates, version registry, and rollback governance. Every candidate capability should be reproducible, benchmarked against a baseline, explicitly approved for promotion, and reversible.
