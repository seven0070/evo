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
| `cli.py` | Local command-line entry point and proposal/experience inspection |
| `evolver.py` | Evidence analysis, proposal generation, validation, persistence, and recorded review |
| `sandbox.py` | Proposal-gated isolated candidate experiments, bounded execution, comparison, and cleanup |

## Verification

```bash
python3 -m pytest -q
```

The current test suite covers workspace traversal protection, shell allowlisting, approval blocking, approved end-to-end execution, memory persistence, checkpoint rollback, structured task assessment, direct/plan-first/recovery strategy selection, tool recommendations, strategy switching, bounded replanning, SQLite adaptation-event persistence, Experience/Evaluation lifecycle and deterministic scoring, evidence-backed weakness and opportunity detection, proposal validation, protected-target rejection, proposal approval/rejection, auditable Evolver events, sandbox approval gates, candidate isolation, sanitized environments, network namespace isolation, fixed test commands, timeout termination, comparison classification, cleanup, production immutability, and Phase 1–4 regression behavior.

## Flexibility Engine

The repository now includes a runtime-only Flexibility Engine. It assesses task complexity, expected steps, risk, reversibility, verification difficulty, available tools, and resource needs; selects a direct, plan-first, approval-aware, or recovery strategy; recommends tools through the existing registry; and records adaptation decisions in the existing SQLite event stream.

When an execution fails, the engine can diagnose the failure context, switch to a bounded recovery strategy, and request one replan. The kernel still owns execution limits, permissions, approvals, checkpoints, rollback, and final verification. The Flexibility Engine cannot declare success, modify source code, change permanent capabilities, or mutate the repository.

> **Flexibility = runtime adaptation. Evolution = long-term controlled improvement.**

## Experience and Evaluation Foundation

The repository includes a deterministic Experience Engine and Evaluation Engine. After an observable task outcome exists, the Experience Engine extracts the goal, task type, complexity, strategy, tools, execution events, observations, failures, recovery attempts, strategy changes, verification result, approvals, outcome, duration, agent version, and model identifier into one structured record. The record is stored in the existing SQLite database; no second database is introduced.

The Evaluation Engine is separate from the Verifier. The **Verifier** answers whether the requested result actually happened. The **Evaluator** measures how well the agent performed using an explainable `evaluation-v1` score: task outcome contributes 40 points, verification contributes 30, reliability contributes 20, and efficiency contributes 10. Failed outcomes receive no efficiency credit. Recovery, replanning, retries, strategy changes, approval interventions, and failed tools are recorded as explicit metrics.

Experiences can be retrieved by task type, outcome, strategy, tool, failure text, agent version, or recency. Retrieved historical failures may influence runtime strategy selection, but they cannot override workspace restrictions, shell allowlists, approvals, timeouts, retry limits, checkpoints, rollback, or verification.

The CLI supports `--list-experiences`, `--show-experience EXPERIENCE_ID`, and `--show-evaluation EVALUATION_ID`. Experience and evaluation records are evidence for a future Evolver; they do not modify the agent or create permanent capabilities.

> **Verifier = did it actually succeed? Evaluator = how well did it perform?**

## Controlled Evolver

The repository now includes a proposal-only Controlled Evolver. It analyzes actual historical experiences and evaluations to detect repeated failures, low-performing strategies, inefficient execution, successful alternatives, and recurring tool problems. It generates evidence-backed, risk-classified `EvolutionProposal` records with source IDs, observed problems, bounded proposed changes, expected benefits, risks, measurable evaluation methods, and rollback plans.

The proposal lifecycle is `GENERATED -> PENDING_REVIEW -> APPROVED or REJECTED`. Approval only authorizes a proposal to proceed to a future isolated sandbox phase; it does not apply any change. Protected targets include permissions, security, approval gates, sandbox isolation, rollback, verification, governance, kill switches, and trust boundaries. The Evolver never executes generated code, modifies production files, edits its own source, changes permanent behavior, deploys, promotes, or merges branches.

Proposal inspection and recorded review are available through the CLI:

```bash
evo --list-proposals --workspace ./workspace
evo --show-proposal PROPOSAL_ID --workspace ./workspace
evo --approve-proposal PROPOSAL_ID --proposal-reason "Authorize future sandbox evaluation" --workspace ./workspace
evo --reject-proposal PROPOSAL_ID --proposal-reason "Insufficient evidence" --workspace ./workspace
```

> **No proposal becomes an agent modification without passing the future controlled evolution pipeline.**

## Isolated Evolution Sandbox

The repository now includes a proposal-gated `SandboxEngine`. It independently verifies that a proposal is `APPROVED`, creates a unique experiment outside the production source root, snapshots a read-only baseline, creates a separate candidate copy, applies only a validated structured configuration change, runs a fixed pytest command under bounded isolation, captures output and errors, compares candidate and baseline results, persists the experiment, and cleans up the candidate directory.

The sandbox uses an unprivileged user/mount/PID/network namespace where available, a read-only production bind mount, a sanitized environment, no host API keys or credential variables, a denied-by-default network namespace, a fixed test runner, a process timeout, and process-group termination. It excludes `.git`, runtime databases, checkpoints, caches, and workspace state from candidate copies. Production immutability is checked by a before/after manifest hash as an additional invariant.

Only these structured targets are initially supported: strategy selection, strategy parameters, tool selection, retry/recovery configuration, planning configuration, and prompt/configuration parameters. Protected or unsupported targets fail closed. The candidate writes `evolution_config.json`; it does not execute generated code or rewrite arbitrary source files.

Sandbox commands are:

```bash
evo --sandbox-proposal PROPOSAL_ID --source-root . --sandbox-root ../evo-sandboxes --workspace ./workspace
evo --list-experiments --source-root . --sandbox-root ../evo-sandboxes --workspace ./workspace
evo --show-experiment EXPERIMENT_ID --source-root . --sandbox-root ../evo-sandboxes --workspace ./workspace
```

> `PASSED` means the candidate test command passed in isolation. It does not mean the candidate is better, approved for production, promoted, or deployed.

## Next milestone: benchmark and promotion governance

Later phases may add reproducible benchmark suites, richer candidate patch mechanisms, version registration, explicit promotion gates, and rollback governance. No Phase 5 experiment can modify production, promote a candidate, deploy a change, or perform Metamorphosis.
