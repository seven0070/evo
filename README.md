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


## Governed Evolution Orchestrator

Phase 9 adds the **Governed Evolution Orchestrator**, a persistent conductor over the existing Experience, Evaluation, Flexibility, Evolver, Metamorphosis, Sandbox, Benchmark, Promotion, and Rollback engines. It observes persisted experience, detects evidence-backed opportunities, classifies them deterministically, selects the smallest effective path, and records every lifecycle decision. The orchestrator coordinates specialized authorities; it does not replace or override them.

The routing policy is conservative. A context-specific failure is routed to runtime-only Flexibility first. Repeated evidence can route to the proposal-only Controlled Evolver. An architectural, component, or capability limitation can route to the Phase 8 Metamorphosis Engine. Uncertain evidence becomes `INCONCLUSIVE`, and a non-justified change becomes `NO_CHANGE`; neither path creates a proposal.

Each opportunity becomes a persistent `EvolutionWorkItem` with source evidence, source and architecture versions, selected path, proposal/experiment/benchmark/evidence/promotion lineage, attempt count, cooldown, state, and last error. Valid state transitions are enforced in code. No work item can jump from proposal, benchmark, or evidence directly to production. Only the existing Phase 7 PromotionEngine can activate a candidate, and it still requires its independent promotion approval, integrity checks, health verification, and native rollback.

The orchestrator uses the existing SQLite database for `evolution_opportunities`, `evolution_work_items`, `orchestration_events`, `approval_requests`, `experiment_queue`, `promotion_queue`, and `evolution_cooldowns`. Approval requests distinguish evolution approval, metamorphosis approval, and promotion approval. The orchestrator may create and persist requests, but it cannot approve them itself. Promotion queue admission occurs only after `BETTER` evidence and a separate human promotion approval are recorded.

Execution is bounded by a conservative `OrchestrationPolicy`, including maximum work items, experiments, promotions, failed attempts, same-opportunity attempts, and cooldown intervals. `evo --run-orchestrator` performs one observe/detect/classify/route/process cycle and stops. It does not start an uncontrolled daemon or infinite self-improvement loop. A file-backed exclusive lock serializes concurrent orchestrator processes, while version and architecture revalidation blocks stale work before sandbox or promotion execution.

Restart recovery reloads work items, queue records, experiments, evidence, promotion records, and the actual active version. Interrupted sandbox or benchmark work is never blindly retried; incomplete state is safely marked failed or inconclusive unless an authoritative persisted result permits resumption. Interrupted promotion state is reconciled through Phase 7’s actual active-version and rollback authority.

Phase 9 commands are:

```bash
evo --list-opportunities --workspace ./workspace --source-root .
evo --show-opportunity OPPORTUNITY_ID --workspace ./workspace --source-root .
evo --list-work-items --workspace ./workspace --source-root .
evo --show-work-item WORK_ITEM_ID --workspace ./workspace --source-root .
evo --list-approval-requests --workspace ./workspace --source-root .
evo --run-orchestrator --workspace ./workspace --source-root .
evo --resume-work-item WORK_ITEM_ID --workspace ./workspace --source-root .
evo --approve-orchestration WORK_ITEM_ID --approval-type evolution_approval --approval-decision approve --proposal-reason "Authorize bounded evaluation" --approval-actor human --workspace ./workspace --source-root .
```

> **Orchestration coordinates evolution. Evolution improves existing behavior. Metamorphosis changes declared structure. Governance controls what may change.**

The protected-core boundary remains immutable: governance, permission enforcement, approval authority, sandbox isolation, verification authority, rollback authority, audit integrity, kill switch, trust boundaries, and promotion authorization cannot be modified, disabled, bypassed, or routed around by the orchestrator. No autonomous approval, promotion, deployment, arbitrary generated-code execution, production mutation, or uncontrolled self-modification is introduced.


## Cognitive Intelligence Layer

Phase 10 adds a **Cognitive Intelligence Layer** that turns a natural-language goal into a bounded, inspectable execution lifecycle. `CognitiveOrchestrator` coordinates goal understanding, intent extraction, measurable success criteria, dependency-aware task decomposition, plan generation and selection, capability checks, Kernel execution, observation, verification, bounded diagnosis and replanning, and learning through the existing Experience/Evaluation system.

The cognitive layer distinguishes explicit, inferred, and unknown requirements. Clear goals receive deterministic normalization and criteria. Critical ambiguity, such as `Build me an app`, is persisted as `WAITING_FOR_INPUT` with the missing requirements exposed; the layer never silently invents critical requirements. Non-critical ambiguity is also represented rather than treated as success.

Complex goals are represented by a persisted `TaskGraph`. Nodes carry dependencies, required capabilities, expected outputs, success criteria, risk, tool hints, status, attempts, and result lineage. The executor orders nodes only after dependencies succeed. Candidate plans are scored deterministically by tool availability, risk, and cost, with a conservative limit on plan candidates and reasoning iterations.

Every executable subtask is delegated to the existing `AgentKernel`. The Cognitive Layer does not implement a shell runner or filesystem executor. Kernel workspace confinement, tool registry lookup, shell allowlisting, approval callbacks, timeouts, checkpoints, rollback, and step verification remain authoritative. A successful process result without a successful Kernel verification event is rejected as false success. Medium- and high-risk actions remain blocked or approval-gated exactly as in the Kernel.

After each subtask, bounded observations record tool, output, status, errors, artifacts, duration, side-effect notes, and verification hints. Failure diagnosis distinguishes tool, input, permission, environment, planning, strategy, capability, verification, and unknown failures with confidence. The existing Kernel FlexibilityEngine is consulted for runtime adaptation, while `ReplanningEngine` preserves successful subtasks and limits retries/replans. Partial completion is reported as `PARTIAL`, never as `SUCCESS`.

Before execution, each subtask is checked against the Phase 8 capability registry. Missing capabilities are explicit `CapabilityGap` records. Ordinary gaps create an `EVOLUTION` opportunity in the existing Phase 9 EvolutionOrchestrator; structural gaps create a `METAMORPHOSIS` opportunity there. The Cognitive Layer does not create a second evolution pipeline, approve a proposal, modify itself, alter governance, or fabricate a capability.

Cognitive goals, intents, plans, task graphs, task steps, states, observations, decisions, and verification reports are stored in the existing SQLite database. Restart loads the persisted bundle and safely resumes only when replay is safe; an interrupted executing task is not blindly replayed. Resource ceilings cover subtasks, plans, reasoning iterations, replans, execution time, context size, and tool calls. There is no permanent autonomous daemon.

Phase 10 commands are:

```bash
evo --run-goal "list every text file, count the lines in each file, and create a report" --workspace ./workspace
evo --show-goal GOAL_ID --workspace ./workspace
evo --show-plan PLAN_ID --workspace ./workspace
evo --show-task TASK_ID --workspace ./workspace
evo --show-cognitive-state GOAL_ID --workspace ./workspace
```

> **Cognition understands and plans. The Kernel executes safely. Flexibility adapts execution. Evolution improves behavior. Metamorphosis changes structure. Governance controls what may change.**

The Cognitive Layer cannot bypass permission enforcement, approval gates, sandboxing, benchmarking, verification, promotion, rollback, or protected-core enforcement. It may observe, reason, plan, queue, execute authorized Kernel tasks, recover within limits, and route genuine gaps to Phase 9; it may not approve evolution, metamorphosis, or promotion, directly modify production, execute unrestricted commands, or claim completion without goal-level verification.

## Persistent Memory and Knowledge Intelligence Layer

Phase 11 adds a durable, structured **Memory and Knowledge Layer** on the existing SQLite database. Memory is retained information; knowledge is validated or conservatively generalized information; Experience records what happened; Learning derives useful patterns; Evolution remains governed modification. The memory layer informs cognition but never becomes an alternate authority.

The `MemoryManager` coordinates bounded working memory, episodic experience memory, semantic knowledge, procedural memory, explicit user memory, deterministic retrieval, provenance, consolidation, feedback, forgetting, and integrity validation. Working memory is task-associated, size-bounded, and cleared at task end. Episodic memories reference actual Experience, Observation, and Evaluation records. Semantic consolidation requires repeated evidence and labels derived knowledge as an observed fact, inference, or generalization. Repeated failures remain conservative recurring-failure evidence rather than universal rules.

Every durable record carries type, content, summary, source and source ID, provenance chain, confidence, importance, relevance, version, agent and architecture versions, temporal validity, environment context, status, and occurrence history. Contradictory records are retained as `CONFLICT`; updates create versioned history and supersession links rather than silently overwriting evidence. Deterministic deduplication preserves first and last seen timestamps, occurrence counts, and source IDs.

Retrieval is transparent and bounded by memory count, serialized context bytes, and retrieval time. Topic, task, tool, capability, strategy, recency, importance, confidence, environment, and architecture-version dimensions contribute to an inspectable score. Expired, invalid, incompatible, low-confidence, contradictory, or injection-like records are filtered or downgraded. Retrieved text is data only: stored content cannot execute commands, alter permissions, approve changes, disable safety, or become policy. Current user instructions, task requirements, security policy, permissions, approvals, capabilities, sandbox rules, governance, and verification always outrank historical memory.

Procedural memories store reusable workflows only as candidate strategies. The current capability registry, tool availability, architecture version, environment, policy, and task constraints are revalidated before reuse. Explicit user memory is marked `USER_INPUT`, remains non-executable, and can be archived only through an explicit auditable deletion action. Retention uses `ACTIVE`, `ARCHIVED`, `EXPIRED`, `SUPERSEDED`, and `CONFLICT` states; provenance is retained when information is archived or superseded.

Cognitive planning retrieves a small `CognitiveMemoryContext` before plan selection. Relevant episodic and semantic evidence, candidate procedures, conflicts, and warnings are recorded in the selected plan rationale. Failure handling retrieves historical evidence before consulting the existing Flexibility Engine. Completed Cognitive goals capture Experience, Evaluation, Observation, verification, strategy, tool, and recovery evidence into the same memory database. Memory may strengthen future evidence for Phase 9 opportunity detection, but it cannot approve, promote, modify production, modify protected components, or create a parallel evolution path.

Phase 11 memory inspection commands are:

```bash
evo --list-memory --workspace ./workspace
evo --show-memory MEMORY_ID --workspace ./workspace
evo --search-memory "text file processing" --workspace ./workspace
evo --memory-history MEMORY_ID --workspace ./workspace
evo --memory-provenance MEMORY_ID --workspace ./workspace
evo --list-procedures --workspace ./workspace
evo --show-procedure PROCEDURE_ID --workspace ./workspace
evo --memory-stats --workspace ./workspace
evo --memory-integrity --workspace ./workspace
```

The memory subsystem has no second database, vector-database requirement, unrestricted context dump, autonomous daemon, executable memory, or governance authority. Integrity validation fails safely on missing schema, malformed payloads, missing provenance, or invalid records rather than silently rebuilding corrupted data.

> **Memory retains information. Knowledge is validated or conservatively derived information. Experience records outcomes. Learning identifies useful patterns. Evolution changes the agent only through the governed pipeline.**

## Capability and Tool Intelligence Layer

Phase 12 adds a structured **Capability & Tool Intelligence Layer** above the existing Kernel. A capability describes **what Evo can accomplish**; a tool describes **how that capability can be performed**; selection describes **which currently registered method is appropriate**; governance determines **whether it is allowed**; and the Kernel remains responsible for **how execution actually occurs**.

The layer reuses the existing Phase 8 structural capability registry and the existing Kernel `ToolRegistry`. It adds rich, provenance-bearing capability and tool descriptors, deterministic taxonomy categories, explicit `CapabilityRequirement` records, compatibility checks, health and reliability tracking, bounded discovery caching, explainable selection results, fallback ranking, capability and tool dependency graphs, and advisory capability composition. User/provider/evolution metadata is inspectable but cannot authorize execution or become policy.

Capability discovery evaluates availability, lifecycle state, declared dependencies, environment, architecture version, input/output compatibility, health, resource descriptions, and the existing security policy. Tool selection is deterministic and risk-aware. Historical Phase 11 memory may influence ranking through prior tool outcomes, procedures, failures, environment, and verification evidence, but current availability, permissions, approvals, timeouts, resource ceilings, verification, and Kernel authority always win. The registry records source, source version, lineage, agent version, and registry version; stale or incompatible records are rejected or downgraded rather than silently reused.

The existing Kernel remains the only execution authority. Phase 12 never executes a registry entry directly, installs external plugins, downloads code, grants permissions, approves risky actions, disables timeouts, changes governance, modifies protected components or production, or promotes evolution. Before execution, Kernel-owned planning applies the existing policy and approval chain after Phase 12 schema and selection evidence. Malformed inputs or outputs become rejected/failed evidence and cannot silently enter cognition as trusted facts. Every discovery, candidate, selection, rejection, permission assessment, execution lifecycle, fallback, health change, capability satisfaction, and capability gap is recorded using the existing SQLite event stream.

When a selected tool fails, the bounded `FallbackEngine` identifies compatible alternatives and supplies them to the existing Flexibility Engine for a bounded replan. It does not create an execution loop or bypass retry/replan limits. Genuine ordinary capability gaps are evidence for the existing Phase 9 EvolutionOrchestrator; structural gaps are routed to the existing Phase 8 MetamorphosisEngine. Phase 12 creates neither proposal nor approval, and does not directly mutate the architecture.

Phase 12 inspection commands are:

```bash
evo --list-capabilities --workspace ./workspace
evo --show-capability CAPABILITY_ID --workspace ./workspace
evo --find-capability "report generation" --workspace ./workspace
evo --list-tools --workspace ./workspace
evo --show-tool TOOL_ID --workspace ./workspace
evo --find-tools "text processing" --workspace ./workspace
evo --analyze-capability-gap "vision processing" --workspace ./workspace
evo --analyze-tool-selection "generate a verified report" --workspace ./workspace
evo --capability-stats --workspace ./workspace
evo --tool-health --workspace ./workspace
```

Capability and tool descriptors are persisted in the same `<workspace>/.evo/agent.sqlite3` database. Restart recovery preserves versions, provenance, lifecycle state, health counters, reliability, and selection behavior. A bounded in-memory discovery cache is keyed by requirement, registry inputs, architecture, and environment, has a TTL and maximum size, and revalidates candidates on reuse.

> **Capability intelligence recommends. Governance authorizes. The Kernel executes. Verification decides whether the capability was actually satisfied.**

The initial implementation intentionally does not add a vector database or LLM semantic selector, arbitrary external plugin installation, a continuous capability daemon, or automatic capability promotion. Provider/model capability descriptors can be added later behind the existing adapter and governed registries.


## World and Environment Intelligence Layer

Phase 13 adds a bounded **World & Environment Intelligence Layer**. Environment observation describes where Evo is operating and what the local execution context currently exposes. World state is the task-relevant state derived from those observations. Memory remains historical evidence, inference remains reasoning about evidence, Verification remains authoritative confirmation, Governance decides whether an action is permitted, and the Kernel remains the only execution authority.

`EnvironmentState` records a stable environment identity and version together with operating system, architecture, runtime, Python and agent versions, architecture version, allowlisted workspace, bounded filesystem state, registered Phase 12 capabilities and tools, resource observations, policy-only network state, provider metadata without credentials, current-process scope, configuration policy, constraints, permissions, health, observations, provenance, and non-sensitive metadata. `EnvironmentObserver` collects only bounded local information: it never recursively scans the host, performs unrestricted network discovery, reads credentials, or treats file metadata as trusted file content.

`WorldObservation` distinguishes `FACT`, `INFERENCE`, `ASSUMPTION`, and `UNKNOWN`, and carries source, timestamp, confidence, reliability, environment identity, provenance, trust level, expiry, and metadata. Observations have bounded freshness states: `FRESH`, `AGING`, `STALE`, `EXPIRED`, or `UNKNOWN`. Untrusted or expired observations are not silently promoted to current facts. `WorldAssumption` records statements that must be validated before critical execution, while `WorldConflictDetector` preserves conflicting historical and current evidence and reports that current authoritative state wins.

Environment snapshots are immutable, provenance-bearing records with an observation hash, schema version, environment version, and integrity hash. The `EnvironmentDiffEngine` classifies added, removed, changed, unchanged, and unknown state. Corrupted snapshots fail closed and compare as `UNKNOWN`; they are not silently reconstructed. Filesystem observations are workspace-confined and bounded, and `FilesystemChangeDetector` reports relevant creation, deletion, and modification events.

`EnvironmentContext` contains only task-relevant filesystem, capability, tool, resource, network, provider, constraint, permission, and observation data. Cognitive planning obtains this context before Phase 12 capability analysis and persists the environment identity, context hash, and observation IDs in the plan. Before a persisted plan resumes, the current environment is re-observed and the plan is rejected or marked for bounded replanning when relevant tools, capabilities, workspace state, runtime compatibility, or resource/policy assumptions have changed.

The world layer observes resources and policy but cannot increase resource limits. `ResourceIntelligence` may recommend smaller bounded batches; the Kernel still enforces limits and timeouts. Provider state is metadata-only and `ProviderFailoverEngine` selects only explicitly authorized, available, healthy providers. Network state records policy-relevant restrictions only. No API keys, tokens, secrets, or inferred credentials are persisted.

After meaningful Kernel actions, the current world is refreshed, observations and immutable snapshots are persisted, predicted consequences can be compared with actual observations, and surprises are recorded. Predictions never satisfy Verification. World evidence is captured through the existing Phase 11 MemoryManager as episodic historical evidence, and relevant environment identity is included in Experience and Evaluation. Current observations and current governance always outrank memory, inference, assumptions, and predictions.

Phase 13 uses the existing SQLite database and audit event stream. Persistence includes environment snapshots, world observations, assumptions, conflicts, diffs, refresh requirements, and provider states. Restart recovery restores valid historical snapshots and observations, then revalidates current state rather than blindly trusting the last snapshot. Bounded refresh requests allow Cognitive or CLI callers to refresh only the necessary environment subject instead of rescanning everything.

Phase 13 inspection commands are:

```bash
evo --show-environment --workspace ./workspace
evo --environment-snapshot --workspace ./workspace
evo --environment-diff --workspace ./workspace
evo --show-world-state --workspace ./workspace
evo --show-observations --workspace ./workspace
evo --show-environment-changes --workspace ./workspace
evo --refresh-environment --workspace ./workspace
evo --environment-stats --workspace ./workspace
```

The World & Environment Intelligence Layer is observational and advisory. It cannot bypass permissions, grant permissions, approve actions, disable sandboxing, increase resource limits, disable timeouts, execute external code, interpret observed text as commands, modify protected core or production, override Governance, approve Evolution or Metamorphosis, promote changes, or replace the existing Verifier. Observed prompt-injection text is retained as observed content and never becomes an instruction.

> **The World Layer observes current conditions. Memory preserves history. Capability intelligence recommends methods. Governance authorizes. The Kernel executes. Verification confirms what actually happened.**

The initial implementation intentionally remains local-first. It does not add unrestricted external-world ingestion, a general AGI world model, autonomous continuous sensing, or persistent autonomous operation.


## Persistent Autonomous Agent Runtime

Phase 14 adds an **AgentRuntime** that coordinates persistent lifecycle management without becoming a second Kernel. Runtime state, queued tasks, schedules, approvals, heartbeats, recovery markers, and bounded metrics are stored in the existing `<workspace>/.evo/agent.sqlite3` database. The runtime can remain active across multiple bounded cycles, but a process must be explicitly started; importing the package never starts a daemon.

The lifecycle state machine is deterministic: `STARTING`, `READY`, `OBSERVING`, `PLANNING`, `WAITING_APPROVAL`, `EXECUTING`, `VERIFYING`, `LEARNING`, `RECOVERING`, `PAUSED`, `DEGRADED`, `STOPPING`, `STOPPED`, and `FAILED`. Invalid transitions are rejected. Startup validates the database and architecture, observes the current Phase 13 environment, marks interrupted work for revalidation, restores durable queue state, and reaches `READY` only after those checks. A previous `RUNNING` task is never assumed complete and is not blindly replayed.

`TaskQueue` persists bounded tasks with goal, source, priority, dependencies, deadline, resource budget, approval requirement, retry budget, attempt, plan, environment version, progress, and a deterministic deduplication fingerprint. The `Scheduler` supports one-shot, interval, and deterministic workspace condition schedules, dependency ordering, priority with age/deadline fairness, task expiration, and bounded backpressure. Condition evaluation supports only safe workspace-relative file existence or absence and bounded `and`/`or` composition; arbitrary expressions, shell commands, Python, and observed text are never evaluated.

`HeartbeatManager` checks runtime state, database accessibility, queue depth, resource pressure, and environment freshness without executing work. `RuntimeResourceManager` enforces hard ceilings for concurrent tasks, task duration, total runtime, retries, recovery cycles, replans, memory/storage pressure, queue size, and tasks per cycle. The runtime defaults to a single active execution and one bounded task per cycle. It never increases or overrides Kernel limits.

Runtime execution delegates goals to `CognitiveOrchestrator`, which continues to use Phase 13 World observation, Phase 11 Memory, Phase 12 Capability Intelligence, the existing Flexibility Engine, and the authoritative Kernel. Verified completion is required before a task becomes `COMPLETED`; tool execution alone is never treated as proof. Failure classification permits only bounded retries for eligible transient, environment, resource, tool, or verification failures. Permission, approval, governance, protected-core, and known destructive failures are not automatically retried. Repeated failures enter a persisted circuit breaker and pause the task rather than creating an infinite loop. Evolution analysis may be invoked through the existing Phase 9 `EvolutionOrchestrator`, but the runtime cannot approve evolution, metamorphosis, promotion, or governance changes.

Runtime pause/resume preserves queue, plans, memory, environment evidence, and approvals. Resume re-observes the environment and revalidates state. Safe mode permits observation, inspection, planning, verification, and reporting while deferring side-effecting autonomous execution. Degraded mode reduces work and preserves state after database, environment, tool, resource, verification, or repeated-failure instability. The independent kill switch stops acceptance of new work, persists state, safely stops future work, and enters `STOPPED`; it cannot be cleared through normal runtime evolution or task planning.

Phase 14 controls include:

```bash
evo --runtime-start --workspace ./workspace
evo --runtime-status --workspace ./workspace
evo --runtime-submit "list the files" --workspace ./workspace
evo --runtime-cycle --workspace ./workspace
evo --runtime-heartbeat --workspace ./workspace
evo --runtime-health --workspace ./workspace
evo --runtime-list-tasks --workspace ./workspace
evo --runtime-show-task TASK_ID --workspace ./workspace
evo --runtime-pause-task TASK_ID --workspace ./workspace
evo --runtime-resume-task TASK_ID --workspace ./workspace
evo --runtime-cancel-task TASK_ID --workspace ./workspace
evo --runtime-pause --workspace ./workspace
evo --runtime-resume --workspace ./workspace
evo --runtime-safe-mode --workspace ./workspace
evo --runtime-kill-switch --workspace ./workspace
evo --runtime-stop --workspace ./workspace
```

> **Persistent operation means repeated bounded observation, scheduling, planning, authorized Kernel execution, verification, learning, recovery, and memory recording. It does not mean automatic approval, promotion, governance modification, protected-core modification, credential acquisition, arbitrary code execution, or unrestricted self-modification.**

The runtime intentionally does not implement Phase 15, arbitrary webhook execution, arbitrary external provider installation, uncontrolled polling, unrestricted external-world ingestion, automatic approval or promotion, or a second execution, governance, verification, evolution, or rollback authority.


## Phase 15 — External Integration & Communication Intelligence Layer

Phase 15 adds a governed, provider-neutral External Integration & Communication Intelligence Layer. External work is modeled as a persistent, auditable, bounded operation rather than as an implicit network capability.

```text
Cognitive Layer
      ↓
Memory / Capability / World Intelligence
      ↓
Integration Intelligence
      ↓
Governance + Permission + Approval
      ↓
Kernel
      ↓
Approved External Connector
      ↓
External Observation
      ↓
Verification
      ↓
Experience / Evaluation
      ↓
Runtime / Memory
```

### Integration registry and connector model

The persistent registry models `Integration`, `IntegrationType`, `IntegrationCapability`, `IntegrationCredentialMetadata`, `IntegrationPermission`, `IntegrationHealth`, `IntegrationVersion`, `IntegrationEnvironment`, and `IntegrationProvenance`. Integrations carry an identity, provider, version, capability and operation declarations, required permissions, risk classification, environment compatibility, health, lifecycle, architecture version, and explicit enabled state.

Provider-neutral connector abstractions currently include HTTP/API, email, file/document, and generic webhook connector classes. A connector can validate availability and execute only a previously modeled operation. The Kernel remains the execution boundary; adding a future provider does not require changing Kernel authority.

Credential values are never stored in SQLite, events, plans, memory, observations, experiences, or status output. Only bounded metadata such as a reference, credential names, storage location, presence flag, and validation time may be retained. External request payloads and evidence are redacted and bounded before persistence.

### External access policy and approvals

`ExternalAccessPolicy` is default-deny. It explicitly controls allowlisted domains and endpoints, HTTP methods, operations, timeouts, request and response sizes, rate limits, retry limits, redirect behavior, authentication requirements, data classifications, and environment restrictions. There is no arbitrary URL access, network scanning, arbitrary outbound communication, or arbitrary webhook execution.

External operations are classified as `READ_ONLY`, `LOW_RISK_WRITE`, `HIGH_RISK_WRITE`, `DESTRUCTIVE`, or `COMMUNICATION`. High-risk, destructive, and communication operations require explicit human approval unless an already existing policy explicitly authorizes the exact operation. The agent, runtime, cognitive layer, connector, and external service cannot approve their own operation. Approval scope covers integration, operation, target, request fingerprint, and permissions; stale or mismatched scopes are rejected.

### Idempotency, observation, and data safety

Each external operation persists an operation ID, integration ID, operation, target, request fingerprint, idempotency key, status, timeout and resource limits, and bounded request metadata. Duplicate successful, running, or unknown operations are prevented from executing again. A mutating operation with an unknown external outcome is classified as `UNKNOWN` and is not blindly retried.

`ExternalObservation`, `ExternalObservationProvenance`, `ExternalResourceState`, and `ExternalChange` extend the World Intelligence boundary for external resources. Observations retain source, integration, timestamp, freshness, trust classification, observation ID, resource identity, version/ETag, content hash, and provenance. Historical observations are never automatically treated as current truth; freshness and explicit re-observation are required. Changes are classified as added, removed, changed, unchanged, or unknown.

All external content is `UNTRUSTED` data by default. API responses, email content, documents, webhook payloads, and external text fields cannot modify governance, permissions, protected core, tools, plugins, evolution, metamorphosis, promotion, system instructions, or code execution. External evidence may enter Memory only through the existing bounded ingestion facade, which records metadata and provenance without automatically storing arbitrary content.

### Intelligence and runtime integration

Cognitive planning can identify an external capability requirement and discover registered compatible integrations, but it does not call connectors. Capability Intelligence receives external capability and tool descriptors for compatibility, health, reliability, risk-aware ranking, and fallback analysis. Historical reliability remains advisory and cannot override permission, governance, approval, network policy, verification, or safety constraints.

External failure results can be passed to the existing Flexibility Engine for bounded retry, approved fallback, refresh, or replan recommendations. The recommendation layer never changes policy. Experience and Evaluation retain external operation, latency, approval, duplicate-prevention, failure-class, unknown-outcome, and verification evidence. Runtime queues external operations through the existing Phase 14 TaskQueue, enforcing dependencies, deadlines, resource limits, safe mode, degraded mode, bounded retries, circuit breakers, kill switch, and shutdown. Safe mode permits explicitly allowed observation while blocking side-effecting external work; the kill switch prevents new external work.

### CLI inspection and controlled operations

The following inspection and controlled-operation forms are available:

```bash
evo --list-integrations --workspace ./workspace
evo --show-integration INTEGRATION_ID --workspace ./workspace
evo --list-integration-capabilities --workspace ./workspace
evo --integration-health INTEGRATION_ID --workspace ./workspace
evo --list-external-policies --workspace ./workspace
evo --show-external-policy POLICY_ID --workspace ./workspace
evo --show-external-observations --workspace ./workspace
evo --external-diff --workspace ./workspace
evo --test-integration INTEGRATION_ID --workspace ./workspace
evo --list-external-operations --workspace ./workspace
evo --show-external-operation OPERATION_ID --workspace ./workspace
evo --external-submit INTEGRATION_ID --external-operation read --external-target RESOURCE --workspace ./workspace
evo --external-enqueue OPERATION_ID --workspace ./workspace
evo --approve-external-operation OPERATION_ID --external-approval-scope SCOPE_HASH --workspace ./workspace
```

These commands inspect or create modeled records only. Actual external execution requires a registered connector, the same access policy and approval path, Kernel ownership, bounded resource controls, and observable result handling.

> **External systems can provide data or perform explicitly authorized actions. They cannot become authorities.**

Phase 15 does not implement arbitrary plugin installation, unrestricted external access, autonomous approval or promotion, arbitrary webhook execution, unrestricted generated-code execution, or Phase 16 functionality.


## Phase 16: Multi-Agent & Specialist Intelligence Layer

Phase 16 adds a bounded specialist and multi-agent intelligence layer beneath the sovereign Evo agent. Specialists are registered subordinate roles, not independent agents with authority. The persistent `SpecialistRegistry` records specialist identity, purpose, type, capabilities, risk classification, model metadata, version lineage, architecture version, provenance, health, lifecycle, and bounded filesystem scope. Built-in provider-neutral roles include research, planning, coding, analysis, verification, documentation, and data.

A parent task may create a `SpecialistTaskContract` that fixes the subordinate goal, scope, allowed capabilities, tools, integrations, workspace scope, output schema, success criteria, resource limits, timeout, deadline, dependencies, approval requirements, prohibited actions, verification requirements, risk, architecture version, and immutable scope hash. Context is constructed by `ContextIsolation` using least privilege and bounded memory, environment, capability, external-observation, and parent-constraint evidence. Specialists never receive the full SQLite store, credentials, governance state, approval authority, production source, or arbitrary executable context.

`SpecialistDelegationEngine` supports bounded task execution, optional bounded parallelism, deterministic specialist selection, structured internal messages, cancellation, restart recovery, retry budgets, circuit breakers, specialist health, resource ceilings, evidence extraction, evidence fusion, conflict preservation, and conflict resolution requests. Specialist output is data. It is not a Kernel result, permission, approval, verification, promotion decision, governance change, or evidence of success until the existing verification authority confirms it.

Cognitive planning may record advisory specialist discovery for complex goals. It does not execute specialists directly. Explicit specialist tasks can enter the existing Phase 14 runtime queue through `AgentRuntime.enqueue_specialist_task`, where Runtime remains responsible for lifecycle, dependencies, deadlines, resource limits, safe mode, kill switch, recovery, and shutdown. Any tools or external integrations required by a specialist remain subject to the existing Phase 12 Capability Intelligence, Phase 15 External Access Policy, Kernel-owned execution, approval, and verification paths.

Specialist outputs are classified as claims, observations, evidence, or inferences and retain trust and provenance metadata. `EvidenceFusionEngine` compares independent claims, records conflicts instead of silently selecting a winner, and produces supported claims, unsupported claims, uncertainty, confidence, and verification requirements. Historical MemoryManager capture is metadata-only and cannot become policy or executable instructions. Experience and Evaluation include specialist-task, delegation, evidence, and conflict metrics.

Phase 16 deliberately does not create autonomous approval, autonomous promotion, specialist governance, specialist tool execution, specialist external access outside registered integrations, arbitrary code execution, unrestricted filesystem access, protected-core mutation, production mutation, independent kill-switch removal, or uncontrolled self-replication. The authority chain remains: **Sovereign Evo and Governance authorize; Runtime bounds and schedules; the Kernel executes; Verification confirms; specialists advise and provide bounded evidence.**

Useful inspection commands include:

```bash
evo --list-specialists --workspace ./workspace
evo --show-specialist specialist_analysis --workspace ./workspace
evo --specialist-health specialist_analysis --workspace ./workspace
evo --specialist-stats --workspace ./workspace
evo --specialist-task "analyze the bounded workspace records" --specialist-id specialist_analysis --workspace ./workspace
evo --list-specialist-tasks --workspace ./workspace
evo --list-delegations --workspace ./workspace
evo --list-specialist-evidence --workspace ./workspace
evo --list-specialist-conflicts --workspace ./workspace
evo --queue-specialist-task SPECIALIST_TASK_ID --workspace ./workspace
evo --cancel-specialist-task SPECIALIST_TASK_ID --workspace ./workspace
```


## Model and Learning Intelligence Layer

Phase 17 adds a governed **Model & Learning Intelligence Layer**. Its flow is:

```text
Cognitive Goal
    ↓
Task Requirements
    ↓
Model Intelligence
    ↓
Model Selection
    ↓
Bounded Inference
    ↓
Verification
    ↓
Experience / Evaluation
    ↓
Learning Evidence
    ↓
Bounded Routing Adjustment
    ↓
Future Evolution Proposal
```

The persistent `ModelRegistry` records provider-neutral model metadata, capabilities, context and modality limits, structured-output and tool-use support, cost and latency metadata, performance, health, lifecycle, architecture version, policy, and provenance. Credential values are never stored; only non-secret credential references and metadata may be retained. Provider adapters are bounded interfaces for availability, discovery, inference, structured output, tool calls, streaming, and health checks. Deterministic test and local adapters work without a live external service. OpenAI-compatible and Anthropic-compatible adapters remain behind the provider-neutral boundary.

`ModelRouter` produces deterministic ranked candidates, a selection explanation, confidence, and bounded fallbacks from task requirements, capability match, context limits, risk, health, reliability, latency, cost, specialist preferences, and historical evidence. Historical performance and learned scores can influence ranking only inside configured bounds; they cannot override Governance, permissions, approvals, network policy, Runtime limits, safe mode, the kill switch, or the Verifier. A model never becomes an authority and cannot select itself as authoritative.

`ModelEvaluationEngine` runs versioned deterministic benchmark trials under identical bounded conditions and records task success, verification, output validity, latency, resource usage, failure category, retries, and reproducibility metadata. Comparative results are classified conservatively as `BETTER`, `NO_CHANGE`, `WORSE`, or `INCONCLUSIVE`. The existing Verifier remains authoritative for actual completion; model confidence is not treated as proof.

The governed `LearningEngine` persists observations, outcomes, evidence, policies, and adjustments. Adjustments require minimum evidence, confidence thresholds, maximum deltas, cooldowns, version compatibility, provenance, rollback values, and audit records. They are reversible and decayed over time. Exploration is deterministic and bounded by eligibility, probability, seed, risk, and resource policy. Learning can change only bounded model/strategy/tool/specialist selection evidence. It cannot modify the protected core, governance, permissions, approval logic, verifier, sandbox, promotion, rollback, kill switch, provider adapters, routing architecture, or learning algorithm.

Model context is explicitly bounded by byte and output budgets. Truncation preserves content hashes and provenance. Prompts, complete responses, credentials, and executable provider content are not automatically stored in Memory, Experience, Evaluation, logs, or model metadata. Provider and model outputs remain untrusted data until they pass the existing governed pipeline.

Phase 17 integrates advisory model selection with Cognitive planning, specialist-aware routing, metadata-only Memory capture, Experience/Evaluation metrics, Flexibility recommendations, Evolution evidence, Metamorphosis boundaries, Phase 15 external-provider governance, and a bounded Runtime model-inference queue. Runtime deadlines, resource limits, concurrency, retries, safe mode, degraded mode, shutdown, and kill-switch behavior remain authoritative.

Phase 17 inspection commands include:

```bash
evo --list-models --workspace ./workspace
evo --show-model MODEL_ID --workspace ./workspace
evo --model-health --workspace ./workspace
evo --find-models "coding" --workspace ./workspace
evo --analyze-model-selection "complex coding task" --workspace ./workspace
evo --model-evaluation MODEL_ID --workspace ./workspace
evo --compare-models MODEL_A MODEL_B --workspace ./workspace
evo --list-learning --workspace ./workspace
evo --show-learning LEARNING_ID --workspace ./workspace
evo --learning-stats --workspace ./workspace
evo --model-routing-report --workspace ./workspace
```

> **Models provide intelligence; they do not become authorities.**

Phase 17 intentionally does not add autonomous model training, unrestricted weight modification, arbitrary provider or plugin installation, unrestricted endpoint access, credential storage, silent learning, irreversible routing changes, autonomous approval, autonomous promotion, protected-core mutation, production mutation, or Phase 18 functionality.


## Continuous Learning & Adaptive Intelligence Layer

Phase 18 adds a persistent, bounded **Continuous Learning & Adaptive Intelligence Layer** above the verified Phase 1–17 systems. Its lifecycle is:

```text
Experience → Evaluation → Learning Observation → Pattern Detection
→ Hypothesis → Adjustment Candidate → Evidence/Risk/Policy Gates
→ Controlled Application → Outcome Monitoring → Learning Evaluation
→ Keep / Decay / Rollback
```

The `AdaptiveLearningEngine` consumes structured experience, evaluation, model, tool, capability, specialist, environment, recovery, fallback, verification, and explicitly supplied user-feedback evidence. It detects recurring positive and negative patterns, creates explicit hypotheses, evaluates evidence quality, creates bounded adjustment candidates, records rollback checkpoints, and applies only low-risk, policy-compatible changes. Historical evidence, model outputs, external content, specialist messages, memory text, user text, and tool output remain data rather than executable instructions.

Adaptive policies may influence model, fallback, tool, capability, specialist, strategy, recovery, context, decomposition, and resource recommendations. They cannot modify Kernel authority, Verifier authority, Governance, permissions, approval requirements, Sandbox isolation, protected core, kill switch, rollback authority, promotion authority, or security boundaries. **Learning adapts decisions; it does not become an authority.**

Phase 18 supports explicit user feedback as evidence, bounded historical counterfactual evaluation, deterministic exploration with a risk ceiling and budget, explainable baseline-versus-adapted decisions, confidence decay, contradictory-evidence conflicts, automatic bounded rollback after harmful evaluation, and evidence-only routing into the existing Phase 9 EvolutionOrchestrator or Phase 8/9 Metamorphosis pipeline. It never executes arbitrary counterfactual actions against production and never creates a second evolution system.

Learning cycles are finite, persistent, restart-safe, pausable, cancellable through Runtime, resource-limited, safe-mode-aware, and kill-switch-aware. They reuse the existing AgentRuntime queue and lifecycle; no uncontrolled daemon is created. Phase 18 extends the single SQLiteStore with `learning_patterns`, `learning_hypotheses`, `adaptive_policies`, `adaptive_adjustments`, `adjustment_evaluations`, `learning_feedback`, `counterfactual_evaluations`, `learning_conflicts`, `learning_rollbacks`, and `learning_cycles`.

Phase 18 inspection commands include `--learning-status`, `--learning-cycle`, `--list-learning-patterns`, `--show-learning-pattern`, `--list-learning-hypotheses`, `--show-learning-hypothesis`, `--list-adaptive-policies`, `--show-adaptive-policy`, `--list-adjustments`, `--show-adjustment`, `--learning-evaluate`, `--learning-rollback`, `--learning-feedback`, and `--learning-stats`.

Phase 18 intentionally excludes autonomous approval, promotion, deployment, governance or security modification, unrestricted training or weight modification, arbitrary code or plugin execution, credential storage, unrestricted network access, protected-core or production mutation, self-replication, uncontrolled agent spawning, and uncontrolled learning daemons. The Kernel remains the sole execution authority and the Verifier remains the sole authority for determining whether requested outcomes actually occurred.


## Self-Model and Meta-Cognition Intelligence Layer

Phase 19 adds a bounded, inspectable **Self-Model and Meta-Cognition Intelligence Layer**. Self-modeling describes Evo's current capabilities, limitations, reliability, uncertainty, assumptions, readiness, freshness, and operating constraints from authoritative registries and verified evidence. Meta-reasoning evaluates whether a goal is ready for bounded execution, requires clarification, needs human approval, has insufficient evidence, or should be refused. Neither subsystem is an authority.

`SelfModelEngine` persists claims, snapshots, limitations, assumptions, uncertainty, conflicts, reliability summaries, freshness, and provenance in the existing SQLite database. Claims are generated from the existing Capability, Tool, Model, Specialist, External Integration, World, Runtime, Learning, Governance, Kernel, and Verifier surfaces. Current state is revalidated on refresh; stale, expired, missing, contradictory, or untrusted information is labeled rather than fabricated. Environment, version, capability, model, specialist, and architecture drift can invalidate relevant self-model claims.

`MetaReasoningEngine` provides bounded decision readiness, clarification intelligence, human-escalation recommendations, confidence calibration, uncertainty disclosure, safer alternatives, and refusal recommendations. Its output is advisory evidence only. It cannot execute work, grant permission, satisfy approval, replace the Verifier, clear the kill switch, modify Governance, promote itself, change the protected core, access credentials, or override Runtime, Sandbox, Evolution, Metamorphosis, Promotion, or Rollback.

Self-diagnostics query existing authorities for database, Runtime, model, tool, capability, specialist, integration, environment, learning, evolution-queue, and rollback health. The layer does not create a duplicate health system. Structured post-task reflection records what was attempted, succeeded, failed, assumed, selected, verified, remembered, and whether bounded Learning or Evolution evidence should be generated. Self-critique checks unsupported completion, success, verification, availability, and safety claims against authoritative evidence; confidence is never verification.

Persistent records include `self_model_claims`, `self_model_snapshots`, `self_model_limitations`, `self_model_assumptions`, `self_model_uncertainty`, `self_model_conflicts`, `decision_readiness`, `meta_reasoning_records`, `confidence_calibration`, `self_reflections`, and `self_diagnostics`. Each record includes provenance, timestamps, architecture and environment identity, lifecycle or status, and bounded evidence references. Sensitive prompts, responses, credentials, arbitrary payloads, and executable content are excluded from metadata-only Memory capture.

Phase 19 operations can run through the existing bounded AgentRuntime queue as finite, resource-limited, restart-safe, cancellable, pausable, safe-mode-aware, and kill-switch-aware operations. No uncontrolled daemon or autonomous reflection loop is started by importing Evo. Cognitive may receive meta-reasoning and self-model references for advisory annotations, while the existing Cognitive, Kernel, Runtime, Governance, and Verifier authorities remain unchanged.

Phase 19 controls include:

```bash
evo --self-model --workspace ./workspace
evo --self-model-refresh --workspace ./workspace
evo --self-model-status --workspace ./workspace
evo --self-model-claims --workspace ./workspace
evo --self-model-limitations --workspace ./workspace
evo --self-model-assumptions --workspace ./workspace
evo --self-model-uncertainty --workspace ./workspace
evo --self-model-conflicts --workspace ./workspace
evo --decision-readiness "prepare a verified report" --workspace ./workspace
evo --meta-reason "prepare a verified report" --workspace ./workspace
evo --self-diagnostics --workspace ./workspace
evo --self-reflect TASK_ID --workspace ./workspace
evo --confidence-report --workspace ./workspace
```

Persistent limitations are converted into evidence for the existing Phase 9 EvolutionOrchestrator or, for structural limitations, the existing Phase 8/9 Metamorphosis pipeline. Self-modeling never mutates source, architecture, production, governance, permissions, security, or protected authorities directly.

> **Self-modeling informs decisions; it does not become an authority.**


## Goal and Strategic Autonomy Intelligence Layer

Phase 20 adds a persistent **Goal & Strategic Autonomy Intelligence Layer** above the verified Phase 1–19 system. It maintains rich strategic goals, milestones, subgoals, bounded task graphs, dependencies, blockers, strategies, alternatives, priorities, resource recommendations, progress, conflicts, decisions, reassessments, and goal-level verification history in the existing SQLite database.

The strategic layer is an advisory coordinator, not a new execution authority. Governance and human approval decide what is allowed; the Runtime decides when work may run and enforces limits; the Kernel executes through the existing tools and security policy; the Verifier decides what actually happened. Strategic records can recommend bounded work through Runtime and can provide evidence to Cognitive, Flexibility, Capability, Model, Specialist, Adaptive Learning, Self-Model, Experience, Evaluation, and the existing governed Evolution/Metamorphosis paths, but they cannot bypass any of them.

Goals are created with provenance, architecture/environment context, confidence, uncertainty, assumptions, risk, resource bounds, and explicit success criteria. Human priority is authoritative over inferred priority. Priority and resource allocation are deterministic recommendations, and allocations are capped by supplied Runtime ceilings; they never change those ceilings. Strategies and alternatives remain advisory, include evidence and rollback information, and do not execute tools, providers, code, external actions, evolution, promotion, deployment, or production mutations.

Progress and completion are deliberately separate. Queued, started, or completed Runtime tasks are not sufficient evidence of strategic success. `GoalVerifier` aggregates authoritative verified milestone and task evidence and reports `UNVERIFIED`, `PARTIAL`, `VERIFIED`, `FAILED`, or `CONFLICTED`. Only satisfied criteria backed by verification can produce `VERIFIED`; unresolved dependency, approval, permission, resource, environment, or evidence blockers remain visible and can trigger a human escalation recommendation. Conflicting goals are persisted without silently selecting a winner.

Strategic execution is finite. A Runtime strategic task runs one bounded observe/prioritize/dependency/reassessment cycle and terminates. Safe mode, the Runtime kill switch, lifecycle transitions, deadlines, dependency checks, resource limits, retry ceilings, pause/cancel/shutdown/restart recovery, and all existing authorities remain in force. No background planning daemon, recursive loop, replication, unrestricted network, credential access, arbitrary plugin/provider, self-granted permission, governance modification, protected-core modification, verification bypass, autonomous approval, promotion, deployment, or Phase 21 behavior is introduced.

Phase 20 inspection commands include:

```bash
evo --goal-create "prepare a verified local report" --goal-human-priority 90 --workspace ./workspace
evo --goal-list --workspace ./workspace
evo --goal-show GOAL_ID --workspace ./workspace
evo --goal-prioritize --workspace ./workspace
evo --goal-plan GOAL_ID --workspace ./workspace
evo --goal-progress GOAL_ID --workspace ./workspace
evo --goal-blockers GOAL_ID --goal-context '{"capabilities":[]}' --workspace ./workspace
evo --goal-strategy GOAL_ID --workspace ./workspace
evo --goal-alternatives GOAL_ID --workspace ./workspace
evo --goal-reassess GOAL_ID --workspace ./workspace
evo --goal-conflicts --workspace ./workspace
evo --goal-decisions GOAL_ID --workspace ./workspace
evo --goal-verify GOAL_ID --goal-evidence '[{"task_id":"t1","verified":true}]' --workspace ./workspace
```

> **Phase 20 invariant:** Strategic Autonomy may understand, decompose, prioritize, allocate within existing ceilings, recommend, coordinate, persist, reassess, and request bounded Runtime work. It may never become the authority that approves, executes, verifies, promotes, deploys, mutates production, modifies the protected core, changes governance, bypasses security, or creates uncontrolled autonomy.


## Evo V1 hardening and release

Evo V1 freezes the existing Phase 1–20 capability set. The V1 milestone adds no new intelligence layer or authority; it verifies and hardens the complete chain from Cognitive planning through Runtime, Kernel execution, Verification, Experience/Evaluation, Memory, Self-Model, and governed Evolution/Metamorphosis, Promotion, and Rollback.

Startup validates SQLite integrity and persisted structured payloads before overwriting recovery state. Runtime recovery is bounded and conservative: interrupted work is revalidated, requeued only when safe, or marked failed/inconclusive. Safe mode blocks side-effecting execution, the kill switch remains active across restart, and exact task/environment approvals are invalidated when context changes. Event payloads are bounded to prevent unbounded audit-record growth while preserving a content hash and field summary for oversized records.

Run the reproducible release validator from the repository root:

```bash
python scripts/validate_v1.py
PYTHONPATH=. pytest -q
python3 -m compileall -q evo_agent tests scripts
git diff --check
git diff --cached --check
```

Operational documentation is available in [`docs/CLI.md`](docs/CLI.md), [`docs/CONFIGURATION.md`](docs/CONFIGURATION.md), [`docs/RECOVERY.md`](docs/RECOVERY.md), [`docs/PILOT.md`](docs/PILOT.md), [`docs/READINESS.md`](docs/READINESS.md), and [`docs/RELEASE.md`](docs/RELEASE.md). The local-user intake and results templates are [`pilot/v1_user_pilot_template.json`](pilot/v1_user_pilot_template.json) and [`pilot/v1_user_results_template.md`](pilot/v1_user_results_template.md). These documents cover clean installation, configuration, CLI consistency, pilot operation, readiness validation, recovery, rollback, security boundaries, V1 limitations, and the release gate.

Run the expanded local readiness matrix with `PYTHONPATH=. python3 scripts/run_v1_readiness.py --output /tmp/evo_v1_readiness_report.json`. It executes the 7-case offline pilot plus 12 bounded operational checks; it never calls external providers or authorizes production changes.

The stable package version is **1.0.0**. Offline operation remains the default and requires no provider credentials or network access. No V1 operation can approve itself, promote itself, bypass Governance or Verification, mutate production, execute arbitrary generated code, acquire credentials, disable the kill switch, grant permissions, or create an uncontrolled planning loop.
