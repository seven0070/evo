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
| `models.py` | Typed task, plan, tool, event, verification, outcome, architecture, capability, and metamorphosis records |
| `model_adapter.py` | Provider-neutral interface, offline adapter, and OpenAI-compatible adapter |
| `security.py` | Workspace confinement, shell allowlist, risk classification, and approval policy |
| `tools.py` | Workspace file and permissioned shell tools |
| `kernel.py` | Plan → approve → execute → observe → verify → recover orchestration |
| `storage.py` | SQLite tasks, events, memories, checkpoints, architecture manifests, registries, proposals, and experiments |
| `checkpoints.py` | Snapshot and rollback operations |
| `verifier.py` | Deterministic postcondition checks |
| `cli.py` | Local command-line entry point and proposal/experience inspection |
| `evolver.py` | Evidence analysis, proposal generation, validation, persistence, and recorded review |
| `sandbox.py` | Proposal-gated isolated candidate experiments, bounded execution, comparison, and cleanup |
| `benchmark.py` | Versioned benchmark trials, metrics, regression/safety gates, and evidence decisions |
| `promotion.py` | Version registry, explicit promotion approvals, atomic activation, health checks, and native rollback |
| `metamorphosis.py` | Governed structural proposals, component/capability registries, architecture manifests, deterministic compatibility analysis, and pipeline handoff |

## Verification

```bash
python3 -m pytest -q
```

The current test suite covers workspace traversal protection, shell allowlisting, approval blocking, approved end-to-end execution, memory persistence, checkpoint rollback, structured task assessment, direct/plan-first/recovery strategy selection, tool recommendations, strategy switching, bounded replanning, SQLite adaptation-event persistence, Experience/Evaluation lifecycle and deterministic scoring, evidence-backed weakness and opportunity detection, proposal validation, protected-target rejection, proposal approval/rejection, auditable Evolver events, sandbox approval gates, candidate isolation, sanitized environments, network namespace isolation, fixed test commands, timeout termination, comparison classification, cleanup, production immutability, benchmark validation, repeated trials, transparent metrics, deterministic decisions, functional/verification/timeout/efficiency/safety regression gates, evidence persistence, reproducibility metadata, version registry bootstrap and lineage, separate promotion approval, eligibility rejection, candidate integrity and TOCTOU checks, atomic activation, health verification, native rollback, manual rollback, audit records, previous-version retention, governed component and capability registries, deterministic architecture manifests, dependency affected-subgraphs, protected-core rejection, compatibility checks, capability-regression detection, structural candidate isolation, metamorphosis approval separation, Phase 7 handoff, and Phase 1–7 regression behavior.

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

## Evolution Benchmark and Comparative Evaluation

The repository now includes a deterministic `BenchmarkEngine` that evaluates an eligible, retained sandbox experiment against its baseline. It requires an approved proposal, a valid passed sandbox experiment, candidate metadata, and available baseline/candidate directories. It uses the same versioned benchmark, task cases, inputs, fixed runner, timeout, trial count, evaluation rules, and safety policy for both sides.

The initial benchmark suite is intentionally small and repeatable. It checks isolated execution, candidate configuration presence, and verification-preserving behavior. Repeated trials record success, verification, score, timeout, output, errors, duration, step/recovery fields, version, deterministic seed, environment policy, and benchmark version. Aggregated metrics include success rate, verification rate, failure rate, timeout rate, mean score, duration, steps, retries, replans, strategy changes, recovery, and human interventions.

The comparison policy is deterministic and conservative. `BETTER` requires the configured target metric to improve by the configured delta, verification to remain above its minimum, and no functional, verification, timeout, efficiency, or safety regression. `NO_CHANGE` means the target metric is within tolerance. `WORSE` is a hard result for regressions, target decline, insufficient verification, or safety-gate failure. `INCONCLUSIVE` is used for ineligible experiments or insufficient evidence. This phase reports descriptive statistics only and does not claim statistical significance.

CLI commands are:

```bash
evo --list-benchmarks --workspace ./workspace --source-root .
evo --run-benchmark BENCHMARK_ID --experiment EXPERIMENT_ID --workspace ./workspace --source-root .
evo --show-evidence EVIDENCE_ID --workspace ./workspace --source-root .
```

Evidence is persisted in the existing SQLite database using `benchmarks`, `benchmark_trials`, and `evolution_evidence` tables. Every decision includes machine-readable metrics, regression and safety results, a human-readable explanation, version information, benchmark configuration, trial count, seed, timeout, and sandbox policy.

> **Sandbox asks whether a candidate can execute safely. Benchmark asks whether it performs better. Promotion is a later phase.**

## Controlled Promotion and Native Rollback

The repository now includes a separate `PromotionEngine` for the final controlled transition from a benchmark-proven candidate to an immutable active version. Promotion requires all of the following: approved proposal, passed and retained sandbox experiment, valid `BETTER` evidence, no safety regression, matching candidate integrity, registered lineage, and explicit human promotion approval. Proposal approval authorizes experimentation only; it can never substitute for promotion approval.

The version registry preserves source commit, parent version, proposal, experiment, evidence, manifest hash, immutable path, status, and metadata. It bootstraps a known-good `v0`, maintains a single `ACTIVE` version, retains the previous version, and keeps failed or rolled-back candidates available for investigation. Activation uses an atomic `active` symlink switch after staging and a final integrity check, so the previous version is never destructively overwritten.

After activation, the engine verifies required agent files, database opening, safety-control presence, workspace protection, and a bounded smoke test. A failed health check automatically invokes the native rollback path to restore the checkpointed previous version. Manual rollback is also available. Rollback verifies the actual active pointer and restored manifest and preserves all promotion, experiment, benchmark, evidence, candidate, and audit history.

Promotion commands are:

```bash
evo --list-versions --workspace ./workspace --source-root .
evo --show-version VERSION_ID --workspace ./workspace --source-root .
evo --request-promotion CANDIDATE_VERSION --evidence EVIDENCE_ID --workspace ./workspace --source-root .
evo --approve-promotion PROMOTION_ID --proposal-reason "Promote verified candidate" --workspace ./workspace --source-root .
evo --reject-promotion PROMOTION_ID --proposal-reason "Insufficient confidence" --workspace ./workspace --source-root .
evo --promote PROMOTION_ID --workspace ./workspace --source-root .
evo --rollback VERSION_ID --rollback-reason "Post-promotion regression" --workspace ./workspace --source-root .
```

> **Only an explicitly approved, benchmark-proven, integrity-verified candidate may become active. Every promotion remains reversible.**

## Governed Metamorphosis Engine

Phase 8 adds a **Governed Metamorphosis Engine** for bounded structural change. Metamorphosis means changing the declared composition, dependencies, configuration, or capability registry—not unrestricted source rewriting or autonomous self-modification. Every proposal contains a current and proposed architecture manifest, affected components, dependency and capability changes, migration steps, compatibility requirements, benchmark requirements, risks, and a reversible rollback plan.

Only these structural change types are accepted: `ADD_COMPONENT`, `REMOVE_COMPONENT`, `REPLACE_COMPONENT`, `UPGRADE_COMPONENT`, `ADD_CAPABILITY`, `REMOVE_CAPABILITY`, `REWIRE_DEPENDENCY`, and `CHANGE_CONFIGURATION`. The engine computes a reverse dependency affected subgraph and validates required interfaces, dependencies, required capabilities, configuration shape, database/event compatibility fields, and security-policy compatibility. The architecture manifest is content-hashed and persisted in SQLite with component and capability registries.

The protected core is explicit and code-enforced. Governance, permission enforcement, approval authority, sandbox isolation, verification authority, rollback authority, audit integrity, kill switch, trust boundaries, and promotion authorization are immutable from the metamorphosis path. Attempts to remove, replace, rewire, disable, or modify those boundaries fail closed before sandbox execution. Structural candidates are manifest/configuration-only; no generated code is executed and no production source is rewritten.

Approval is deliberately separated into three independent gates. Evolution proposal approval authorizes the ordinary Phase 5 evolution sandbox. Metamorphosis approval authorizes only the structural candidate path. Promotion approval remains a separate Phase 7 decision. A metamorphosis approval never implies evolution approval or production promotion, and no engine autonomously grants any of these approvals.

Structural experimentation reuses the existing `SandboxEngine`, including its outside-source-root candidate directories, read-only baseline, namespace isolation, sanitized environment, fixed test runner, timeout handling, cleanup, and production immutability checks. Comparative evaluation reuses the existing `BenchmarkEngine` and evidence tables. Only a structurally compatible candidate with `BETTER` evidence can be handed to the existing `PromotionEngine`; activation still requires its separate human approval, atomic active-pointer switch, health verification, and native rollback. There is no parallel promotion or rollback system.

Phase 8 inspection commands are:

```bash
evo --list-components --workspace ./workspace --source-root .
evo --show-architecture --workspace ./workspace --source-root .
evo --analyze-metamorphosis "add capability for structured context" --workspace ./workspace --source-root .
evo --list-metamorphosis --workspace ./workspace --source-root .
evo --show-metamorphosis METAMORPHOSIS_ID --workspace ./workspace --source-root .
evo --approve-metamorphosis METAMORPHOSIS_ID --proposal-reason "Authorize structural experimentation" --workspace ./workspace --source-root .
```

> **Metamorphosis may propose and test bounded structural alternatives; it cannot approve itself, mutate production, disable governance, bypass verification, deploy autonomously, self-replicate, or rewrite arbitrary source.**
