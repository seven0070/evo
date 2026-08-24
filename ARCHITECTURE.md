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

## Deferred evolution system

The next milestone should not edit the running kernel in place. It should create candidate versions in isolated directories, run reproducible benchmark tasks, compare candidate metrics with the active baseline, require explicit promotion approval, register the new version, and retain a rollback target. Candidate code and configuration must never receive broader permissions than the active agent.
