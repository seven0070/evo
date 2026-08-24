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

## Deferred evolution system

The future sandbox phase should create candidate versions in isolated directories, run reproducible benchmark tasks, compare candidate metrics with the active baseline, require explicit promotion approval, register the new version, and retain a rollback target. Candidate code and configuration must never receive broader permissions than the active agent.
