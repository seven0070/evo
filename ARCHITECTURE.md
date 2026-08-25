# MVP Architecture

## Boundary

The agent is a local process that receives one text goal at a time and operates only inside an explicitly configured workspace. The kernel does not directly know provider-specific SDK details, filesystem implementation details, or shell policy details. Those concerns are isolated behind adapters and registries.

## Control flow

```text
Goal
  -> create task + append event
  -> create workspace checkpoint
  -> ModelAdapter.create_plan()
  -> ToolRegistry schemas
  -> for each plan step:
       classify risk
       request approval when required
       execute through ToolRegistry
       append result event
       run deterministic Verifier
       retry once or record recovery and fail
  -> persist experience memory
  -> append completion event
```

## Kernel interfaces

| Interface | Contract |
|---|---|
| `ModelAdapter` | Creates a typed plan from a goal and tool schemas, and supplies a recovery recommendation after failure. |
| `ToolRegistry` | Owns registered tools, exposes provider-facing schemas, and executes only registered handlers. |
| `SecurityPolicy` | Resolves paths inside the workspace, validates shell commands, classifies approval requirements, and enforces timeouts. |
| `Verifier` | Checks deterministic postconditions such as tool success, valid JSON, non-empty output, and file existence. |
| `SQLiteStore` | Persists tasks, append-only events, memories, and checkpoint metadata. |
| `CheckpointManager` | Snapshots the workspace and restores a managed snapshot. |

## Trust boundaries

The model is untrusted planning input. It may propose a tool call, but it cannot bypass the registry, workspace resolver, shell allowlist, timeout, or approval callback. Tool outputs are treated as data and are recorded for observability. An approval is a separate control decision and is never inferred from the model’s output.

## Model providers

The kernel-facing adapter is provider-neutral. The repository includes an offline deterministic adapter for development and an optional OpenAI-compatible adapter for hosted or local gateways. Additional Anthropic, Ollama, or other adapters can implement the same interface without changing the kernel.

## Flexibility Engine

The Flexibility Engine is a separate runtime subsystem. It receives a structured `FlexibilityContext` containing the goal, assessment, current plan, observations, failures, verification results, permissions, and execution constraints. It produces an assessment, selects an extensible strategy, recommends tools through the existing `ToolRegistry`, and returns bounded adaptation decisions after failures.

The initial strategies are `direct`, `plan-first`, `approval-aware`, and `recovery`. The kernel records strategy selection, tool recommendation, strategy failure, adaptation, strategy change, replanning, and recovery events in the same SQLite event stream used by Phase 1. The engine can request a replan once, but the kernel remains authoritative for execution limits, approvals, verification, checkpointing, and rollback.

```text
Goal
  -> assess
  -> select strategy
  -> recommend tools
  -> plan
  -> execute through existing policy
  -> observe + verify
  -> if failure: diagnose -> bounded replan -> verify
```

> **Flexibility is runtime adaptation. Evolution is long-term controlled improvement.**

The Flexibility Engine cannot modify source code, mutate the repository, create permanent capabilities, promote itself, or declare task success. Those responsibilities belong to a future controlled Evolver and must remain isolated from runtime adaptation.

## Experience and Evaluation Foundation

After the kernel has an observable outcome, `ExperienceEngine` extracts a structured record from the existing task events. It stores the original goal, task type, complexity, strategy, tools, execution steps, observations, failures, recovery attempts, strategy changes, verification result, outcome, approvals, duration, agent version, and model identifier. The record is persisted in the existing SQLite database.

`EvaluationEngine` is separate from `Verifier`. The verifier remains authoritative about whether the requested result happened. The evaluator measures performance using deterministic `evaluation-v1` scoring and records explicit success, verification, efficiency, recovery, reliability, retry, replan, strategy-change, and human-intervention metrics. Re-evaluating the same experience with the same evaluator version produces the same result.

Experiences can be retrieved using structured SQLite filters. The Flexibility Engine may receive relevant historical experiences as context and use prior failures to choose a more deliberate runtime strategy. Historical evidence never changes permission policy or bypasses execution controls.

```text
Execute -> Observe -> Verify -> Outcome
                              |
                              v
                        Experience
                              |
                              v
                         Evaluator
                              |
                              v
                    Performance Evidence
```

> **Verifier: did it actually succeed? Evaluator: how well did it perform?**

## Controlled Evolver

The Controlled Evolver consumes accumulated `Experience` and `EvaluationResult` records and stops at a human-reviewable `EvolutionProposal`. Its pipeline is `analyze -> identify weakness/opportunity -> generate proposal -> validate proposal -> persist -> await human review`. It does not edit source code, execute generated code, mutate the production workspace, change permissions or governance, deploy, promote, merge branches, or perform Metamorphosis.

A proposal records source experience and evaluation IDs, source agent version, target component, observed problem, evidence, proposed change, expected benefit, risks, affected capabilities, affected permissions, deterministic confidence, evaluation method, rollback plan, risk classification, status, approval decision, and Evolver version. Valid proposals move to `PENDING_REVIEW`; human review may record `APPROVED` for a future isolated sandbox or `REJECTED`. Approval does not apply the proposal.

The first analysis rules detect repeated failures, low evaluation scores, inefficient successful executions, successful alternative strategies, and repeated tool failures. Confidence is calculated from evidence count, outcome consistency, and evidence diversity. Protected targets include permissions, security, approval, sandbox, rollback, verification, governance, kill-switch, and trust-boundary components; such proposals are rejected and never converted into executable modifications.

```text
Experience + Evaluation
          |
          v
      Controlled Evolver
          |
          v
  Evidence-backed Proposal
          |
          v
     Human Approval
          |
          v
  STOP — no modification
```

> **No proposal becomes an agent modification without passing the future controlled evolution pipeline.**

## Isolated Evolution Sandbox

The `SandboxEngine` is the first controlled execution stage after human approval. It independently loads the proposal from SQLite and permits entry only when status is `APPROVED`, the proposal is valid, the risk is not `PROTECTED`, and the target belongs to the explicitly supported evolvable-component set. Pending, rejected, missing, invalid, protected, or unsupported proposals fail closed.

Each experiment receives a unique directory outside the production source root with separate `baseline`, `candidate`, `logs`, `results`, and `metadata` areas. The production source is copied into a read-only baseline and a separate candidate directory; production databases, memory, checkpoints, credentials, and Git metadata are excluded from the copies. Candidate changes are represented as validated `evolution_config.json` data. The Phase 5 sandbox does not rewrite arbitrary source code or execute generated code.

Candidate tests run through a fixed pytest command inside an unprivileged user, mount, network, and PID namespace. The production source is bind-mounted read-only inside the child namespace, the network namespace is private, the environment is sanitized, host secret variables are not passed through, output and errors are captured, and the process group is terminated on timeout. Experiment results include baseline and candidate execution records, comparison classification, logs, isolation policy, network policy, cleanup status, and version metadata.

The sandbox distinguishes successful candidate execution from proven improvement. `PASSED` means only that the candidate test command completed successfully; it is not promotion or production approval. Every experiment is persisted before cleanup, and cleanup is recorded explicitly. No automatic promotion, deployment, production mutation, branch merge, or Metamorphosis exists in this phase.

```text
Approved Proposal
       |
       v
  Isolated Experiment
   /              \\
Baseline        Candidate
   |              |
Fixed tests    Structured change + fixed tests
   \\            /
      Comparison
          |
          v
 Persist -> Cleanup -> STOP
```

> **Candidate may experiment. Production remains untouched.**

## Evolution Benchmark and Comparative Evaluation

The `BenchmarkEngine` evaluates only an eligible sandbox experiment: an approved proposal, a valid experiment, candidate metadata, and completed sandbox execution. It loads a versioned deterministic benchmark, validates task cases and probes, prepares the same cases for both baseline and candidate, runs repeated trials with the same inputs, timeout, fixed runner, verification rules, and environment policy, aggregates transparent metrics, detects regressions, evaluates hard safety gates, and persists an `EvolutionEvidence` package.

The initial benchmark suite is deliberately small and deterministic. It covers an isolated environment probe, candidate configuration presence, and verification-preserving behavior. The benchmark representation includes benchmark ID, name, version, task cases, success criteria, metrics, trial count, timeout, deterministic seed, and benchmark version. Each trial records side, version, task case, timing, success, verification, score, timeout, output, errors, and controlled-resource metadata.

The decision policy is deterministic and conservative. `BETTER` requires the configured target metric to improve by the required delta, complete verification to remain above its minimum, and no functional, verification, timeout, efficiency, or safety regression to be present. `NO_CHANGE` means the target metric is within tolerance. `WORSE` is used for regressions, target decline, insufficient verification, or any safety-gate failure. `INCONCLUSIVE` is used when the experiment is ineligible or the evidence cannot support a deterministic decision. No statistical significance is claimed; this phase reports descriptive count, mean, rate, and difference metrics only.

Safety is a hard gate. The benchmark records production immutability, candidate isolation, denied network policy, absence of host secret variables, fixed runner use, and candidate safety status. Any failed safety condition prevents `BETTER`. Evidence includes machine-readable metrics and regression objects plus human-readable decision reasons and reproducibility metadata: benchmark and evaluator versions, trial count, seed, versions, timeout, evaluation rules, source commit where available, and sandbox policy.

```text
Sandbox Experiment
        |
        v
Same Benchmark + Same Trials
        |
        +--> Baseline Metrics
        +--> Candidate Metrics
        |
        v
Regression + Safety Gates
        |
        v
Evidence Package -> BETTER / NO_CHANGE / WORSE / INCONCLUSIVE
        |
        v
STOP — no promotion
```

> **Sandbox asks whether a candidate can execute safely. Benchmark asks whether it performs better. Promotion is a later phase.**

## Controlled Promotion and Native Rollback

The `PromotionEngine` is separate from the Kernel, Flexibility, Experience, Evaluation, Evolver, Sandbox, and Benchmark subsystems. It coordinates only the final controlled transition from verified candidate to active immutable version. It does not redefine security policy and never infers approval from proposal approval or benchmark success.

Two approvals remain distinct: evolution approval authorizes a proposal to enter the sandbox; promotion approval authorizes a benchmark-proven candidate to become active production. A `PromotionRequest` is eligible only when the proposal is approved, the sandbox experiment is passed and retained, evidence is valid with decision `BETTER`, safety results contain no failure, candidate integrity matches the registered candidate, and the candidate is in the version registry. A separate explicit human promotion approval is required immediately before activation.

The version registry preserves source commit, parent version, proposal, experiment, evidence, manifest hash, immutable version path, status, and metadata. `v0` is the initial known-good version. Only one registry entry may be `ACTIVE`; the previous known-good version remains `PREVIOUS` until retention policy permits removal. Candidate, active, previous, rolled-back, retired, and invalid states preserve lineage and investigation history.

Promotion is staged atomically: validate eligibility, create a checkpoint, copy the exact candidate into the version registry, verify identity and manifest again to protect against time-of-check/time-of-use changes, atomically replace the `active` symlink, update registry state, run health and smoke verification, and commit the promotion record. The old active path is never destructively overwritten. A failed health check invokes native rollback to the checkpointed previous version and preserves the failed candidate as `ROLLED_BACK`.

Rollback restores the active version pointer and verifies the actual active path and manifest, rather than relying only on database status. Manual rollback and health-triggered rollback both preserve promotion, experiment, benchmark, evidence, and candidate records. Promotion and rollback operations have dedicated durable records and auditable events with relevant IDs and version lineage.

```text
BETTER Evidence
      +
Explicit Promotion Approval
      |
      v
Checkpoint -> Stage -> Integrity Check -> Atomic Activate
                                      |
                                      v
                              Health + Smoke Check
                                /             \\
                             PASS             FAIL
                              |                |
                            ACTIVE       Native Rollback
                                              |
                                              v
                                    Previous Known-Good
```

> **Only an explicitly approved, benchmark-proven, integrity-verified candidate may become active. Every promotion remains reversible.**

## Governed Metamorphosis Engine

Phase 8 adds a governed structural-change layer above the existing Evolver, Sandbox, Benchmark, and Promotion subsystems. It models the current architecture as a persisted `ArchitectureManifest` containing component records, capability records, dependency edges, interface declarations, protected-component declarations, configuration, and a content integrity hash. `ComponentRegistry` and `CapabilityRegistry` persist the active and candidate registry state in the same SQLite store; they do not create a second control plane.

Metamorphosis accepts only eight enumerated change types: add/remove/replace/upgrade component, add/remove capability, rewire dependency, and change configuration. A `MetamorphosisProposal` is descriptive and proposal-only. It includes current and proposed manifests, affected components, dependency/capability deltas, deterministic risk class, reversible migration and rollback steps, compatibility requirements, benchmark requirements, evidence rationale, and an explicit status. Arbitrary source rewriting, generated-code execution, self-replication, production mutation, or unsupported change types are outside the contract.

The dependency graph is analyzed in reverse from the declared affected roots so that direct dependents are included in the affected subgraph. Compatibility is deterministic and fail-closed. The engine checks required components and capabilities, dependency availability, interface declarations, configuration shape, database schema compatibility fields, event compatibility fields, protected-core equality, and security-policy compatibility. Removing a required capability or introducing an unavailable dependency is incompatible and cannot reach the sandbox.

The protected core is represented both as explicit component records and as a hard-coded authority boundary. Governance, permission enforcement, approval authority, sandbox isolation, verification authority, rollback authority, audit integrity, kill switch, trust boundaries, and promotion authorization cannot be removed, replaced, rewired, disabled, or modified through metamorphosis. The engine compares protected declarations and protected component integrity hashes before any candidate is created.

```text
Architecture Manifest + Registries
              |
              v
       Structural Opportunity
              |
              v
       Metamorphosis Proposal
              |
              v
  Deterministic Compatibility Gates
              |
        explicit metamorphosis approval
              |
              v
  Existing SandboxEngine (manifest/config only)
              |
              v
  Existing BenchmarkEngine + Evidence
              |
        BETTER + structural gates
              |
              v
  Existing PromotionEngine
        + separate promotion approval
              |
              v
     Atomic activation + native rollback
```

The approval states are intentionally independent. Evolution approval authorizes the ordinary Phase 5 proposal path. Metamorphosis approval authorizes only structural experimentation. Promotion approval remains a separate Phase 7 decision. None of these approvals is inferred from another, and no state transition can autonomously approve, promote, deploy, or mutate production.

Structural candidates are created through a thin adapter around the existing `SandboxEngine`. This reuses its unique directories outside the production source root, read-only baseline, candidate copy, namespace isolation, sanitized environment, fixed runner, timeout handling, cleanup, and production immutability checks. The adapter writes only structured architecture/configuration manifests; it does not execute generated code or alter production source. Comparative evaluation is delegated to the existing `BenchmarkEngine`; any capability or structural safety regression forces a non-`BETTER` result. Only a `BETTER` structural experiment may be handed to `PromotionEngine`, which retains its own integrity checks, explicit human approval, atomic active symlink switch, health verification, and native rollback.

Metamorphosis events—including proposal, validation, compatibility analysis, structural candidate creation, capability regression, evaluation, promotion handoff, and rollback—are written to the append-only event stream. The CLI exposes component and architecture inspection plus metamorphosis proposal inspection and approval; downstream sandbox, benchmark, promotion, and rollback commands remain the existing commands rather than parallel implementations.

> **Metamorphosis changes declared structure under governance; it never changes who governs, verifies, isolates, approves, promotes, audits, or rolls back the agent.**


## Governed Evolution Orchestrator

Phase 9 introduces `EvolutionOrchestrator` as a lifecycle-control layer above the existing Phase 1–8 engines. Its responsibility is to answer **which existing mechanism should handle an evidence-backed opportunity**. The detector and classifier are deterministic and rule-first; the orchestrator does not use an unrestricted model decision to select a more powerful change path.

```text
Observe -> Experience -> Evaluation -> OpportunityDetector
                                      |
                                      v
                               ChangeClassifier
                         /          |           \
                 Flexibility    Evolver    Metamorphosis
                         \          |           /
                                      v
                              approval request
                                      |
                                      v
                         existing SandboxEngine
                                      |
                                      v
                   existing BenchmarkEngine + Evidence
                                      |
                           Decision: reject / better
                                      |
                                      v
                    separate Promotion approval -> Phase 7
                                      |
                          health verification / rollback
```

The smallest-effective-change policy is ordered `FLEXIBILITY`, `EVOLUTION`, then `METAMORPHOSIS`. Flexibility remains runtime-only and may produce a new Experience. Evolution continues to use the proposal-only `Evolver`, its existing approval and Phase 5 sandbox contract, and its existing benchmark/evidence path. Metamorphosis continues to use Phase 8’s compatibility and manifest-only structural adapter. No path is permitted to bypass the verifier, security policy, approval authority, sandbox, benchmark, evidence, integrity, health verification, promotion, or rollback authorities.

### Persistent lifecycle

`EvolutionOpportunity` stores source experience/evaluation identifiers, problem, frequency, severity, affected task types/components/capabilities, evidence strength, recommended path, confidence, status, architecture version, and a deterministic fingerprint. The fingerprint covers the target/problem/evidence/selected path/version context. An equivalent active opportunity is not recreated.

`EvolutionWorkItem` stores the selected path, source lineage, source and architecture versions, proposal/experiment/benchmark/evidence/promotion identifiers, candidate version, attempt count, cooldown, state, timestamps, and error information. The explicit state machine is:

| State group | States and boundary |
|---|---|
| Intake | `DETECTED -> ANALYZING -> CLASSIFIED -> QUEUED` |
| Proposal | `QUEUED -> PROPOSED -> AWAITING_APPROVAL -> APPROVED` |
| Experiment | `APPROVED -> SANDBOXING -> BENCHMARKING -> EVALUATING -> DECIDED` |
| Positive result | `DECIDED -> BETTER -> AWAITING_PROMOTION_APPROVAL -> PROMOTION_APPROVED -> PROMOTING -> HEALTH_CHECK -> COMPLETED` |
| Safe terminal outcomes | `REJECTED`, `INCONCLUSIVE`, `FAILED`, `ROLLED_BACK`, `BLOCKED`, `CANCELLED` |

Transitions are persisted and invalid transitions fail. Direct paths from `PROPOSED`, `BENCHMARKING`, `BETTER`, or `AWAITING_APPROVAL` to production are not represented in the transition table.

### Approval and execution queues

The SQLite store contains separate approval, experiment, and promotion queue records. Approval requests explicitly identify `EVOLUTION`, `METAMORPHOSIS`, or `PROMOTION`; one approval never implies another. The orchestrator can create requests and record an external human decision, but autonomous actors are rejected by code. Experiment queue entries are admitted only for explicitly approved work and execute through the existing bounded sandbox. Promotion queue entries are admitted only after valid `BETTER` evidence, safety/integrity eligibility, and separate human promotion approval; activation still occurs only through `PromotionEngine`.

### Recovery and concurrency

Every transition writes a structured orchestration audit event containing timestamp, work item, opportunity, prior/current states, path, component, version, actor, reason, and result. On restart, persisted work and queue records are rehydrated. An interrupted sandbox or benchmark is not blindly retried: a persisted authoritative result may advance the item, otherwise it is marked safely inconclusive or failed. Interrupted promotion is reconciled against the actual active version and the existing Phase 7 promotion/rollback records.

A process lock serializes orchestrator cycles and prevents conflicting work-item mutations across processes. Version and architecture hashes are revalidated immediately before experiment and promotion execution. A mismatch blocks the work item and requires revalidation. Cooldown records and configurable attempt ceilings prevent repeated failure loops; a single failure cannot autonomously escalate a problem into structural change.

### Bounded autonomy

`run_cycle()` performs one bounded cycle: observe persisted experience, detect and deduplicate opportunities, classify and route new work, process only already-authorized experiments, collect pending evidence, record decisions, and stop. Conservative limits cover work items, experiments, promotions, failed attempts, same-opportunity attempts, stale items, and cooldown intervals. There is no default continuous daemon and no autonomous approval, promotion, production mutation, governance change, arbitrary generated-code execution, or protected-core modification.

```text
Orchestration = coordinates evolution
Evolution     = improves existing behavior
Metamorphosis = changes declared structure
Governance    = controls what may change
```

The orchestrator’s protected-core policy is intentionally redundant with the underlying engines. It rejects opportunities targeting governance, permission enforcement, approval authority, sandbox isolation, verification authority, rollback authority, audit integrity, kill switch, trust boundaries, or promotion authorization. The downstream Evolver, MetamorphosisEngine, SandboxEngine, BenchmarkEngine, and PromotionEngine independently retain their own fail-closed checks.


## Cognitive Intelligence Layer

Phase 10 adds `CognitiveOrchestrator` as the brain of a single bounded natural-language goal, while retaining the Phase 1–9 hierarchy. Cognition understands and plans; the Kernel executes safely; Flexibility adapts runtime strategy; Experience and Evaluation produce evidence; Phase 9 coordinates governed behavior changes; Metamorphosis changes declared structure; Governance controls what may change.

```text
Natural-language goal
        |
        v
GoalUnderstanding -> Intent -> Success Criteria
        |
        v
TaskDecomposition -> TaskGraph -> Planning -> Reasoning
        |                                |
        +------------------------------->+
                                         v
                              Capability + Tool Selection
                                         |
                                         v
                              Existing AgentKernel.run()
                                         |
                          +--------------+--------------+
                          v                             v
                     Observation                     Verification
                          |                             |
                          +---------- failure ---------+
                                         |
                                         v
                             Diagnosis -> Flexibility
                                         |
                                         v
                                  Bounded Replanning
                                         |
                              capability gap / outcome
                                         |
                          +--------------+--------------+
                          v                             v
                    Experience/Evaluation       Phase 9 Orchestrator
                                                        |
                                               Evolution / Metamorphosis
```

### Goal understanding and planning

`GoalUnderstandingEngine` normalizes natural-language input and records explicit, inferred, and unknown requirements. It marks critical ambiguity as `WAITING_FOR_INPUT` instead of fabricating requirements. `IntentModel` captures the primary and secondary objectives, constraints, preferences, required outputs, success definition, risk, and resource information. `SuccessCriteriaEngine` creates measurable checks before execution; process exit alone is never a goal-success definition.

`TaskDecompositionEngine` creates bounded `CognitiveTask` nodes with dependencies, inputs, expected outputs, required capabilities, risk, and status. `TaskGraphEngine` rejects missing dependencies, duplicate IDs, self-dependencies, and cycles; its topological ordering ensures a subtask never executes before its prerequisites. `PlanningEngine` produces bounded candidate plans, and `ReasoningEngine` selects the lowest-risk viable plan using tool availability, risk, and cost. Plan records contain plan, agent, and architecture versions.

### Kernel-owned execution and observation

The Cognitive Layer calls `AgentKernel.run()` for each executable subtask. It does not create a second shell executor, filesystem executor, verifier, approval system, or checkpoint manager. The Kernel’s existing `ToolRegistry`, `SecurityPolicy`, workspace confinement, shell allowlist, approval callback, timeouts, checkpoints, rollback, and step verifier remain authoritative. A Kernel result counts as a completed cognitive subtask only when the outcome is successful **and** a successful authoritative verification event is present.

`Observation` records are derived from persisted Kernel events and retain tool, output, status, errors, artifacts, duration, side-effect notes, and verification hints. `CognitiveVerifier` evaluates the complete goal criteria over the graph and observations. It distinguishes `SUCCESS`, `PARTIAL`, `FAILED`, `INCONCLUSIVE`, `BLOCKED`, `WAITING_FOR_INPUT`, and `WAITING_FOR_APPROVAL`. A successful subtask count with unmet required criteria cannot be reported as success.

### Flexibility, diagnosis, and gaps

After a failure, `FailureDiagnosisEngine` assigns a bounded, confidence-labelled category such as tool failure, permission failure, strategy failure, verification failure, or capability gap. The existing Kernel FlexibilityEngine is consulted for adaptation; `ReplanningEngine` preserves completed nodes and limits replans. Permission or approval failures produce `WAITING_FOR_APPROVAL` and are never retried around the Kernel gate.

Before execution, required capabilities are checked against the Phase 8 capability registry. An unavailable capability becomes a persisted `CapabilityGap`. Ordinary gaps create an `EVOLUTION` `EvolutionOpportunity` in the existing Phase 9 EvolutionOrchestrator. Explicitly structural gaps create a `METAMORPHOSIS` opportunity there. The Cognitive Layer never invents a capability, creates a parallel evolution pipeline, approves a proposal, or changes its own source.

### Persistence and recovery

The existing SQLite store persists cognitive goals and intents, plans, task graphs, task steps, states, observations, decisions, and verification reports. Cognitive state records include the current task, replan count, tool-call count, last error, and timestamps. Restart reloads this state and the selected plan. If a task was executing when the process stopped, the layer refuses to assume that replay is safe and records an inconclusive failure unless an external authoritative result permits safe continuation.

The cognitive policy applies ceilings to subtasks, plan candidates, reasoning iterations, replans, execution time, context size, and tool calls. Context observations are bounded by size. A single `--run-goal` command executes one bounded goal lifecycle and terminates; no uncontrolled background loop is created.

### Authority and safety contract

```text
Cognitive Layer: understand, plan, select, observe, diagnose, and request
Agent Kernel: execute through registered tools and enforce permissions
Verifier: determine whether requested conditions actually hold
Phase 9: coordinate evidence-backed evolution work
Sandbox/Benchmark: isolate and evaluate candidates
Promotion: require separate approval and activate atomically
Rollback: restore the previous known-good version
```

Cognitive reasoning cannot bypass the Kernel, permissions, approvals, sandbox, benchmark, verification, promotion, rollback, or protected core. It cannot directly modify production or its own source, approve evolution/metamorphosis/promotion, disable governance, execute unrestricted commands, create unlimited reasoning loops, or fabricate task completion.


## Persistent Memory and Knowledge Intelligence Layer

Phase 11 adds a single SQLite-backed `MemoryManager` between Cognitive planning and the existing Experience/Evaluation evidence systems. It is a knowledge-support layer, not a governance layer.

```text
Current authoritative state
          |
          v
Cognitive Layer <---- bounded CognitiveMemoryContext
          |                         ^
          v                         |
      MemoryManager                 |
  /       |       |       |         |
Working Episodic Semantic Procedural User
  \       |       |       |         /
   Retrieval -> Consolidation -> Forgetting
          |
          v
Agent Kernel -> Observation -> Verification
          |
          v
Experience / Evaluation -> Phase 9 Evolution Orchestrator
```

### Memory records and provenance

`MemoryRecord` is structured rather than an opaque history blob. It carries memory type, content, summary, source, source ID, provenance chain, confidence, importance, relevance, timestamps, access counters, version, temporal validity, agent and architecture versions, environment snapshot, lifecycle status, occurrence counts, source IDs, and metadata. `ProvenanceManager` rejects durable records that cannot answer where their information came from. Derived semantic records retain the source memory chain and are explicitly labelled as observed fact, inference, or generalization.

Working memory contains the current goal, constraints, selected plan, and recent task context. It is bounded by item count and serialized bytes, evicts the least useful non-critical records deterministically, and preserves critical constraints. It is task-associated and cleared when the goal ends. Episodic memory captures actual Experience, Evaluation, and Observation evidence. Semantic memory is created only from repeated evidence; recurring failures produce conservative inference rather than universal policy. Procedural memory stores candidate workflows and is never blindly replayed. User memory is explicit `USER_INPUT`, non-executable, and requires an explicit auditable deletion action.

### Retrieval and knowledge use

`RetrievalEngine` uses deterministic, inspectable scoring across topic, task, failure, tool, capability, strategy, recency, importance, confidence, environment, and architecture-version compatibility. Retrieval is bounded by maximum records, serialized bytes, and time. Expired, invalid, incompatible, low-confidence, contradictory, environment-mismatched, version-mismatched, or injection-like records are filtered or warned. Conflicts retain both records and provenance; no record is silently overwritten.

Before plan selection, CognitiveOrchestrator requests a bounded `CognitiveMemoryContext`. The selected plan records the IDs and warnings of historical evidence considered. Before runtime adaptation, failure retrieval is passed as evidence into the existing Flexibility context. Memory is evidence, not instruction: current user requirements, task state, security policy, permissions, approvals, capability restrictions, sandbox rules, governance, verification, and the existing Phase 9–7 authorities always win.

### Lifecycle, updates, retention, and integrity

The durable lifecycle is `CAPTURE -> VALIDATE -> CLASSIFY -> STORE -> RETRIEVE -> REUSE -> UPDATE -> CONSOLIDATE -> ARCHIVE/EXPIRE`. Deterministic fingerprints collapse duplicate observations while preserving first/last seen, occurrence count, and source IDs. Updates mark the old record `SUPERSEDED`, create a new version, preserve snapshots and supersession links, and retain provenance. Records can be `ACTIVE`, `ARCHIVED`, `EXPIRED`, `SUPERSEDED`, or `CONFLICT`; forgetting changes status rather than destroying important provenance.

The existing SQLite database contains the Phase 11 memory record, history, link, procedure, feedback, and audit-event tables. `MemoryIntegrityReport` checks required schema, parseable structured payloads, mandatory IDs/content, and provenance. Corrupt or malformed memory is reported as an invalid record and is not silently rebuilt into trusted knowledge. Restart creates a new `MemoryManager`, reloads durable records, validates versions and environments during retrieval/procedure selection, and does not blindly restore stale working context.

### Authority and safety

```text
Memory: retained data and evidence
Knowledge: validated or conservatively derived evidence
Experience: what actually happened
Learning: useful patterns derived from experience
Cognition: understands and plans
Kernel: executes through policy and tools
Evolution: governed behavioral modification
Metamorphosis: governed structural modification
Governance: controls what may change
```

Memory cannot execute stored content, change permissions, approve evolution/metamorphosis/promotion, modify protected components, bypass the Kernel, bypass sandbox or benchmark, disable verification or rollback, alter governance, modify production, or create autonomous loops. Prompt-injection-like text remains untrusted data with `executable = false` and is never converted into a tool call or policy. Procedures are candidate strategies subject to current capability, environment, policy, approval, and verification checks.

### Feedback loop

```text
Task -> Cognition -> Kernel execution -> Observation -> Verification
  -> Experience / Evaluation -> Episodic memory
  -> Conservative consolidation -> Retrieval
  -> Memory-informed planning and Flexibility
  -> Evidence-backed Phase 9 opportunity
  -> Existing governed Evolution / Metamorphosis pipeline
```

The memory layer does not create a second evolution system. It supplies bounded historical evidence to the existing Cognitive and Phase 9 paths; only the existing sandbox, benchmark, promotion, health verification, and rollback authorities can evaluate or activate governed changes.


## Capability and Tool Intelligence Layer

Phase 12 introduces `CapabilityIntelligence` as an advisory intelligence layer between Cognitive planning and the existing AgentKernel. It does not replace the structural Phase 8 registry, the runtime Phase 1 `ToolRegistry`, or any governance authority. The layer answers the structured questions of what capability is required, which registered tools can provide it, whether each candidate is compatible and healthy, and which permitted method is the deterministic best candidate.

```text
Natural-language goal
        |
        v
Cognitive requirements / TaskGraph
        |
        v
CapabilityRequirement
        |
        v
Capability registry + CapabilityGraph
        |
        v
Tool discovery -> compatibility -> policy description
        |
        v
Deterministic ToolSelection + bounded fallback
        |
        v
Existing SecurityPolicy -> approval -> AgentKernel
        |
        v
ToolRegistry execution -> observation -> Verifier
        |
        v
Experience / Evaluation -> Phase 11 Memory
        |
        +--> Phase 9 Evolution evidence
        +--> Phase 8 Metamorphosis evidence
```

### Capability and tool models

A `Capability` describes an outcome or function, such as `filesystem_read`, `text_processing`, `report_generation`, `verification`, or `memory_retrieval`. It carries category, version, lifecycle, provider, implementation, dependencies, constraints, environment requirements, reliability, risk, availability, compatibility, and provenance. A `Tool` describes an implementation method and carries capability mappings, input/output schemas, declared permissions, risk, timeout and resource descriptions, environment requirements, health counters, reliability, lifecycle, version, provider, implementation reference, and provenance. Tool metadata is descriptive only; it cannot authorize the tool.

The Phase 12 registry facade extends the existing persisted structural capability registry with rich inspection and lifecycle operations. Runtime tool descriptors are persisted in the same SQLite store, alongside existing tasks, events, memory, experience, evaluation, evolution, metamorphosis, sandbox, benchmark, promotion, and rollback records. Built-in descriptors are synchronized from the existing runtime `ToolRegistry`; advisory descriptors for planning, verification, and memory retrieval are explicitly non-executable.

### Requirements, discovery, and compatibility

Cognitive subtasks are converted into `CapabilityRequirement` records with an ID, capability, explanation, priority, input/output requirements, constraints, provenance, and status. `ToolDiscoveryEngine` considers only registered descriptors and filters by capability mapping, lifecycle, availability, declared dependencies, input/output schemas, environment, architecture version, health, and the current policy description. A bounded TTL cache is keyed by requirement, registry inputs, environment, and architecture version; cache hits are revalidated, and cache size is capped.

`CompatibilityEngine` returns `COMPATIBLE`, `PARTIAL`, `INCOMPATIBLE`, or `UNKNOWN` with structured checks and reasons. The compatibility result covers capability mapping, schemas, environment, architecture, dependencies, and current tool health. Resource limits and timeouts remain descriptions evaluated by intelligence; enforcement remains with the existing Kernel and SecurityPolicy. The capability and tool graphs expose deterministic dependency order and fail closed on missing dependencies and cycles. Composite capabilities preserve component provenance and remain advisory until the governed evolution pipeline makes any permanent change.

### Selection, health, and fallback

`ToolSelectionEngine` ranks compatible candidates deterministically using compatibility, reliability, health, historical Phase 11 evidence, and risk. Results include all candidates, scores, rejection reasons, policy/approval metadata, compatibility checks, concise rationale, and memory evidence IDs. Safety and current policy dominate reliability: a high-risk tool may be selected as a recommendation, but its `approval_required` result is visible and the Kernel must still receive explicit approval before execution.

Health records preserve success, failure, timeout, last-success, last-failure, average-duration, and recent-failure data. Repeated failures transition a tool through degraded/failed health; explicit disable/deprecate operations make it unavailable without deleting historical records. `FallbackEngine` excludes failed tools, ranks at most a configured number of alternatives, and passes alternatives to the existing FlexibilityEngine for bounded replanning. It never retries indefinitely, executes a registry handler, or changes retry/replan limits.

### Kernel and evidence boundaries

Cognitive plans now persist capability requirements and selection evidence. The Kernel records capability analysis, permission checks, and tool execution lifecycle events, validates planned input schemas before execution, and rejects malformed outputs before they become trusted verification evidence. The actual call still follows the existing `SecurityPolicy`, approval callback, runtime `ToolRegistry`, timeout, checkpoint, observation, and `Verifier` path. Phase 12 has no `execute`, `approve`, `promote`, or governance mutation API.

Experience records include capability selection and candidate evidence, while Evaluation records expose selection count, satisfied requirements, gaps, and selected tools in addition to the existing deterministic performance metrics. Phase 11 memory stores capability/tool context and historical outcomes as evidence. It may influence ranking but cannot override current permissions, approvals, availability, compatibility, verification, governance, or user instructions.

A missing capability is classified as `AVAILABLE`, `UNAVAILABLE`, `PARTIAL`, `INCOMPATIBLE`, `BLOCKED`, or `UNKNOWN`. The layer reports evidence to the existing Cognitive gap detector and Phase 9 EvolutionOrchestrator. A structural classification is routed to the existing Phase 8 MetamorphosisEngine. The capability layer does not generate, approve, sandbox, benchmark, promote, or roll back a proposal.

### Environment, provenance, and security

Runtime context contains only relevant non-secret properties such as operating system, Python runtime, workspace classification, dependency declarations, and policy metadata. Capability and tool records preserve registry version, agent version, architecture/source version, provider, source, source version, actor, and lineage. Old architecture records are not silently considered compatible. Tool metadata containing instructions such as permission overrides remains inert data and is never interpreted as authorization.

The immutable authority chain is:

```text
CapabilityIntelligence recommendation
              |
              v
Existing governance and SecurityPolicy
              |
              v
Existing approval authority
              |
              v
AgentKernel execution
              |
              v
Existing observation and Verifier
```

The Phase 12 layer cannot bypass the Kernel, grant permissions, approve risky actions, disable timeouts or resource limits, modify protected components or production, install external code, execute downloaded code, approve evolution/metamorphosis/promotion, turn memory into policy, or create an uncontrolled loop. Production immutability and protected-core enforcement remain the responsibility of the existing governed downstream systems and are independently verified.

### CLI and observability

The CLI exposes rich capability and tool listing, detail, search, capability-gap analysis, tool-selection analysis, capability statistics, and tool-health inspection through `--list-capabilities`, `--show-capability`, `--find-capability`, `--list-tools`, `--show-tool`, `--find-tools`, `--analyze-capability-gap`, `--analyze-tool-selection`, `--capability-stats`, and `--tool-health`. All decisions are structured and inspectable through the existing SQLite event stream, with no hidden chain-of-thought or secret provider data.

> **Capability = what Evo can accomplish. Tool = how it can accomplish it. Selection = which registered method is appropriate. Governance = whether it is allowed. Kernel = how execution actually occurs.**


## Phase 13 — World & Environment Intelligence Layer

Phase 13 inserts a bounded observation and reasoning layer between Cognitive planning and Phase 12 capability/tool selection. It answers **where Evo is operating, what is currently present, what changed, and which task-relevant constraints apply** without becoming an unrestricted control system.

`EnvironmentState` is the current local operating context. It reuses the existing Phase 12 registries and version structures and contains environment identity/version, platform/runtime metadata, the allowlisted workspace, bounded filesystem state, registered capability/tool state, resource observations, policy-only network state, provider metadata, current-process state, configuration policy, constraints, permissions, health, provenance, and observation references. `EnvironmentObserver` reads only required local information. It limits workspace entries and file hashes, excludes internal `.evo` state from task filesystem assumptions, does not scan the host recursively, does not perform arbitrary network discovery, and never collects credentials.

`WorldModel` is task-bounded state derived from the current environment and `WorldObservation` records. A world observation is explicitly a `FACT`, `INFERENCE`, `ASSUMPTION`, or `UNKNOWN`, and includes source, time, confidence, reliability, environment identity, provenance, trust level, expiry, and metadata. `TRUSTED` and `VERIFIED` records are reserved for authoritative factual sources; observed file content remains data, including hostile prompt-like text. Freshness is explicit (`FRESH`, `AGING`, `STALE`, `EXPIRED`, or `UNKNOWN`) and is evaluated using observation-specific expiry. Historical memory never silently becomes current world fact.

Snapshots are immutable records in the existing SQLite database. Each contains environment and architecture versions, an observation summary, provenance, observation hash, schema version, and immutable integrity hash. `EnvironmentDiffEngine` produces deterministic `ADDED`, `REMOVED`, `CHANGED`, `UNCHANGED`, or `UNKNOWN` entries. Invalid or corrupted snapshots fail closed and yield unknown integrity state; the system does not silently reconstruct security-relevant state. `WorldRefreshEngine` persists bounded refresh requirements and refreshes only the requested environment subject. `FilesystemChangeDetector`, `ResourceIntelligence`, `ProviderFailoverEngine`, and `EnvironmentCompatibilityEngine` are advisory helpers; they cannot widen workspace, network, provider, permission, or resource authority.

The environment-aware cognitive path is:

```text
Goal
  → Intent and requirements
  → bounded EnvironmentObserver
  → task-relevant EnvironmentContext
  → Phase 12 capability requirements and tool selection
  → existing Governance and approval checks
  → existing AgentKernel
  → existing Verifier
  → WorldModel update and immutable snapshot
  → Experience/Evaluation
  → Phase 11 Memory
```

Cognitive plans persist environment ID, environment version, context hash, relevant context, and observation IDs. Before a persisted plan resumes, current environment state is re-observed. A plan is not executed when a selected tool disappears, a required capability is missing, or a relevant compatibility/constraint assumption fails. Task-relevant changes are passed through the existing bounded Flexibility path for revalidation and replanning; no second recovery loop is introduced. Direct Kernel flows also record environment observation and post-action world-state events, while all permissions, approvals, execution, timeouts, resource ceilings, and verification remain Kernel-owned.

World predictions describe expected consequences only. After an action, the layer records actual observations and uses `WorldSurpriseDetector` to identify discrepancies. Predictions and inferences cannot satisfy verification. Current workspace state, current policy, current provider authorization, current capability/tool availability, and the existing Verifier outrank memory, assumptions, inferred state, and predicted state.

Environmental evidence is stored through the existing Phase 11 `MemoryManager` as historical episodic evidence, with environment identity/version, bounded tool state, resource conditions, network policy, and relevant filesystem state. Experience records include environment ID/version, architecture version, relevant context hash, tool environment, and resource conditions. Evaluation classifies failures into agent, tool, environment, resource, and external-dependency categories where evidence supports the distinction. Current observations remain authoritative over historical memory.

All Phase 13 events use the existing audit stream, including environment observation, snapshot creation, diff, change, resource/tool/capability/provider/health changes, world observation, world-state update, conflict, assumption creation/invalidation, refresh, surprise, prediction, and plan invalidation. Persistence consists of additional tables in the same SQLite store for snapshots, observations, assumptions, conflicts, diffs, refresh requirements, and provider states. Restart recovery loads only integrity-valid records and then re-observes current state before reusing assumptions or plans.

The trust boundary is strict:

| Layer | Authority |
|---|---|
| Environment Observer | Bounded observation only; it cannot authorize or execute. |
| World Model | Task-relevant representation and inference; it cannot turn observations into commands. |
| Phase 11 Memory | Historical evidence only; it cannot override current state or policy. |
| Phase 12 Capability Intelligence | Advisory compatibility, selection, health, and fallback evidence. |
| Governance/SecurityPolicy | Whether an action is allowed, including permission and approval. |
| Agent Kernel | Sole tool execution, workspace confinement, limits, timeouts, and execution audit. |
| Verifier | Sole authority for confirming requested outcomes. |
| Phase 9/8 authorities | Governed evolution, metamorphosis, sandbox, benchmark, promotion, and rollback. |

The World Layer cannot bypass permissions, grant permissions, approve actions, disable sandboxing or limits, execute external code, ingest arbitrary internet state, interpret observed text as instructions, modify protected core or production, approve Evolution or Metamorphosis, promote changes, or create persistent autonomous operation. It provides the environmental foundation required before any later autonomous-operation phase; Phase 14 functionality is intentionally not implemented.
