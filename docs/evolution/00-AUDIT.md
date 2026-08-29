# Evo Architectural Audit (Pre-Integration Baseline)

Audit target: `seven0070/evo` @ `c84da91` ("Harden Windows qualification toolchain setup"), branch `main`, version `1.0.0`.
Method: full read of `evo_agent/` (34 modules, 22,064 LOC), `tests/` (27 files, 5,691 LOC), `scripts/`, `desktop/`, `web/`, `docs/`. Upstreams were first surveyed by file tree, then **read from source** (both repos downloaded as tarballs; DeerFlow harness alone is 114,536 LOC Python). `05-GROUNDING-CORRECTIONS.md` records every claim that tree-level inference got wrong or understated, and corrects three numbers in this document.
This document is analysis only. **No code was modified.**

---

## A. Current Evo architecture and execution flow

### A.1 Shape of the system

Evo is a deliberately **dependency-free** local-first agent kernel: `pyproject.toml` declares `dependencies = []`, with optional `[llm]` (openai) and `[dev]` (pytest). Python ≥3.11. Everything is stdlib + SQLite.

There is no `evo_agent/agent.py` and no unified façade. The package is a flat list of 33 sibling modules, each owning one "Phase" of a 20-phase roadmap:

| Tier | Modules | Role today |
|---|---|---|
| Execution core | `kernel.py` (280), `cognitive.py` (1410), `runtime.py` (1601), `flexibility.py` (271) | Goal → plan → tool calls → verify → adapt |
| Sovereignty | `security.py` (61), `verifier.py` (37), `checkpoints.py` (52), `production.py` (482) | Policy, approval, postconditions, supervision |
| Persistence | `storage.py` (3301), `memory.py` (890), `experience.py` (227), `evaluation.py` (112) | Single SQLite authority (120 tables, 311 store methods) |
| Evolution | `evolver.py` (410), `sandbox.py` (620), `benchmark.py` (578), `promotion.py` (611), `metamorphosis.py` (578), `orchestrator.py` (1063) | Propose → isolate → measure → promote/rollback |
| Capability | `capability.py` (1073), `tools.py` (132), `external.py` (1178), `specialist.py` (1132), `world.py` (920) | Tool/registry/integration/delegation modelling |
| Cognition | `model_intelligence.py` (1186), `adaptive_learning.py` (677), `self_model.py` (741), `strategic_autonomy.py` (742), `model_adapter.py` (93) | Routing, learning, self-knowledge, goals |
| Surfaces | `cli.py` (835), `desktop/bridge/evo_desktop_bridge.py`, `web/` | Entry points |

### A.2 The actual execution flow (verified by reading, not by README)

Two loops exist, and a third cycle overlaps them:

```
evo "goal"
  └─ cli.py  ──► AgentRuntime.run_cycle()                     [runtime.py:1069]
                  ├─ lifecycle/heartbeat/resource/kill-switch gates
                  ├─ TaskQueue dequeue (priority, deps, deadlines)
                  ├─ exact-scope approval gate  (task+environment hash)
                  └─► CognitiveOrchestrator.run_goal()         [cognitive.py:849]
                        goal → IntentModel → Plan → TaskGraph
                        → per-node: AgentKernel execution path
                        → verification results, decisions, observations persisted
                        → _maybe_evolution() on failure
                              │
        (or, with --legacy-kernel) ──► AgentKernel.run()        [kernel.py:79]
                  Goal → checkpoint → world.observe → flexibility.assess
                  → experience.retrieve → capability.analyze_goal
                  → select_strategy → plan → WHILE queue (max_steps):
                        tools.get → policy.requires_approval → approval_callback
                        → capability.validate_input → tools.execute → validate_output
                        → Verifier.verify → record
                        → on failure: fallback_for → recommend_next_action
                              → retry | bounded replan (max_adaptations) | fail
                  → _finalize: Experience → Evaluation → Memory capture
```

`AgentRuntime._process_task()` additionally dispatches four *special* task kinds before falling through to cognition: `strategic_cycle`, `self_model_operation`, `learning_cycle`, `model_inference`, plus `external_operation` and `specialist_task` resume paths. **This is where a runtime-adapter seam already exists and is the natural insertion point.**

### A.3 Existing agent loop — honest assessment

The loop is a real, well-governed **plan-then-execute** loop with bounded adaptation. It is *not* an iterative tool-calling loop:

- `ModelAdapter` has exactly two methods: `create_plan(goal, tool_schemas, context)` and `choose_recovery(...)`. The provider is asked for a **complete plan as one JSON blob** before anything executes (`model_adapter.py:63-88`). There is no native function-calling round-trip, no streaming, no assistant/tool message history, no context compaction.
- Replanning is capped (`max_retries=1`, `max_adaptations=1`, `max_steps=12`).
- `PlanStep.tool_name` is a single tool per step; there is no parallel fan-out, no per-step sub-agent, no dynamic tool discovery mid-plan.

Consequence for integration: **an LLM-driven multi-turn loop is the single biggest missing runtime capability**, and it is exactly what both upstreams are built around.

---

## B. Subsystem-by-subsystem audit

### B.1 Memory (`memory.py`, 890 LOC) — built, but write-mostly

Present and rich: `MemoryType` taxonomy, `Provenance` chains, confidence + importance decay, `memory_history` supersession, `memory_links`, `memory_procedures` (procedural memory), feedback, archive/restore/expiry, `validate_integrity()`, a `RetrievalEngine` with `RetrievalQuery` scoring.

**Finding (important):** the hot path does not use it. `AgentKernel.run()` retrieves context via `self.store.recent_memories()` — `SELECT kind, content, created_at FROM memories ORDER BY memory_id DESC LIMIT 20` (`storage.py:1288`). The `MemoryManager` is instantiated in `_finalize()` **only to write** (`kernel.py:268-276`). Relevance-scored, provenance-filtered retrieval exists but never informs planning.

### B.2 Tools / skills (`tools.py` 132 LOC, `capability.py` 1073 LOC)

`ToolRegistry` hardcodes **four** tools: `workspace_list`, `workspace_read`, `workspace_write`, `shell` (`tools.py:52-95`). Registration is possible programmatically (`ToolRegistry.register`), and `CapabilityIntelligence.register_tool` records a *descriptor* in SQLite.

There are **no skills**, **no MCP**, and **no plugin/skill file format** anywhere in `evo_agent/` (`grep -rn "MCP" evo_agent/*.py` → 0 hits; `grep -rln "skill" evo_agent/*.py` → 0 hits). "Skill" appears only in `docs/` prose.

`capability.py` is the most mature non-executing layer: `Capability`/`Tool` records with lifecycle and health, `CapabilityGapDetector`, `ToolDiscoveryEngine`, `ToolSelectionEngine` (history-aware scoring), `FallbackEngine`, `CompatibilityEngine`, `ToolIntelligenceRegistry.validate_input/validate_output` (used by the kernel). But registration of a `Tool` record does **not** make an executable handler available; the two registries are not the same object.

### B.3 Self-extension — **does not exist as an executable capability**

This is the central audit finding. The evolution machinery is real governance over **config and manifests**, not over behaviour:

1. `SandboxEngine.apply_approved_proposal()` writes exactly one file into the candidate tree: `evolution_config.json` (`sandbox.py:231-251`). `prepare_candidate` pins `allowed_files: ["evolution_config.json"]` (`sandbox.py:223`).
2. **Nothing in `evo_agent/` ever reads `evolution_config.json`.** The only references are the write, the allowlist, and benchmark probes that assert its *existence* (`benchmark.py:488,490`). It is a dead end.
3. `MetamorphosisEngine.create_structural_candidate()` writes `architecture.json` (current vs proposed manifest) into baseline/candidate dirs and runs `pytest` — the "candidate architecture" is a JSON document, and the only code executed is the unchanged test suite (`metamorphosis.py:455-485`).
4. `PromotionEngine._atomic_switch()` repoints `versions/active` symlink (`promotion.py:506`), but **no runtime path imports, execs, or resolves source from that symlink**. `agent_version` is a constant `"1.0.0"` (`version.py:1`).
5. `SandboxEngine.SUPPORTED_TARGETS` = `{strategy-selection, strategy parameters, tool-selection, retry/recovery configuration, ...}` — i.e. only knobs that no component reads.

Net: Evo can *propose, isolate, benchmark, approve, promote, and roll back* changes with excellent audit discipline, but a promoted change has **zero observable effect on execution**. Today's autonomy ceiling is "record that we would do something differently".

### B.4 Persistence and recovery — strong

`storage.py` (3,301 LOC) is the single SQLite authority: 120 tables, WAL, append-only `events` with bounded payload envelopes, integrity `PRAGMA` checks at startup, fail-closed on corruption. `RuntimeManager` persists `runtime_states`, resumes interrupted tasks (`_recover_interrupted_tasks`), `HeartbeatManager` detects stale state, `RecoveryManager.classify()` maps failures to `FailureClass` and chooses requeue/replan/pause, `EventLoop.wake()`, `ShutdownManager.shutdown()/kill()`, kill switch intentionally not removable by normal operation, exact-scope approvals invalidated when `environment_version` changes (`runtime.py:1177-1198`) — a genuinely good design. `production.py` adds `_ProcessLock`, `OperationalJournal`, `BackupManager` (integrity-checked), `CrashReporter`, `ProductionSupervisor`, `ProductionSchemaManager`.

Gaps: no incremental/managed schema migration versioning beyond `ProductionSchemaManager`; single-writer SQLite limits concurrency; checkpointing is full-workspace `copytree` (`checkpoints.py:20-30`), which will not scale to large worktrees.

### B.5 Verification — the weakest sovereignty component

`verifier.py` is 37 LOC. `Verifier.verify()` does: success flag, then string-matches `step.verification` against four literals — `"valid json"`, `"file exists"`, `"result is empty"`, else "non-empty". Any other expectation string silently degrades to "tool returned a usable result".

There is no structured postcondition language, no schema validation against a declared output contract (that lives separately in `capability.validate_output`), no artifact assertions, no citation/claim checking, no differential or property checks, **no independent judge**, and nothing that could tell a plausible-but-wrong research report from a correct one. Specialist-layer `verification` is a *role name* in a registry, not an executor.

### B.6 Evolution mechanisms — good governance, no closure

`evolver.py` mines `Experience`/`Evaluation` for findings, generates `EvolutionProposal` with risk classification, confidence from evidence, dedup, approve/reject. `orchestrator.py` (1,063 LOC) is the strongest piece: opportunity detection, work items with persistent state machine, deduplication, experiment and promotion queues, cooldowns, approval requests, `CycleResult` accounting — a real background evolution scheduler.
`metamorphosis.py` adds architecture manifests with `integrity_hash`, `PROTECTED_CORE`, `REQUIRED_COMPONENTS`, `classify_change` → `"protected"`, `validate_proposal` (rejects protected targets, requires reversible migration plan, rejects strings like `execute_code`/`generated_code`/`disable_rollback`/`bypass_approval`), `check_compatibility` (required components/capabilities, protected-core unchanged incl. per-component hash, dependency availability, interface availability), `affected_subgraph` reverse-dependency walk, and `handoff_to_promotion` gated on `BETTER`.

This is a solid skeleton for a metamorphosis engine. Its entire missing half is **materialization**: applying a change to a real, loadable component.

### B.7 Sandboxing — good, but scoped to evolution only

`SandboxEngine` is genuinely careful: `_bwrap_usable()` runtime probe before trusting bubblewrap, `--unshare-user-try/--unshare-net/--unshare-pid`, read-only `--ro-bind / /` with only `candidate`, `results`, `home` bound writable, sanitized env, `NO_PROXY=*`, `EVO_NETWORK_POLICY=denied`, `PYTEST_ADDOPTS=-p no:cacheprovider`, `_validate_test_command` allowlist, `unshare` fallback that remounts the production source read-only, `_terminate_process` killing the process group, `_make_readonly`/`_make_writable`, manifest-hash production-immutability verification, `_isolation_policy()` reporting `generated_code_execution: False`. `benchmark.py` reuses the same isolation contract.

**Asymmetry to fix:** the isolation is applied to *candidate experiments*, while ordinary tool execution (`ToolRegistry._shell`) runs `subprocess.run(command, shell=True, cwd=workspace)` **on the host, outside any sandbox** (`tools.py:110-118`).

**Verified bypass of the shell allowlist** (run against this working tree, not a hypothetical):

```
SecurityPolicy(workspace).validate_command('python3 evil.py') → (True, 'Command allowed')
```

`-c`/`-m` are blocked, but `workspace_write` (MEDIUM risk) can create `evil.py` and the allowlisted `python3` will then execute it with full host privileges. The path check only inspects tokens that start with `/`, `~`, or contain `..`, so a bare relative name is unresolvable-but-allowed. This is not a remote-exploit-grade issue — it is behind an approval prompt — but it means the allowlist is a *speed bump*, not a boundary, and the design should not grow more trust in it.

### B.8 Testing and benchmarking

`python3 -m pytest` on this machine: **353 passed, 2 failed** (232s). Both failures are environmental, not defects: `tests/test_sandbox.py:268` and the `[bwrap]` parametrization assert the bwrap code path, and this sandbox has only `unshare`. CI installs setuid `bwrap` first (`.github/workflows/production-gate.yml`) to keep isolation real rather than weakened.

`scripts/run_production_gate.py` is a real gate: `compileall` → full tests → `validate_v1.py` → `run_v1_readiness.py` → `run_v1_pilot.py` → clean-venv install + import + `evo --help` → `git diff --check`, with SHA-256 digests of seven `PROTECTED` files asserted **unchanged before/after**, and a dirty-tree failure mode. That protected-hash discipline is the right seed for governance and should be generalized, not reinvented.

`benchmark.py`: deterministic `TaskCase`s, multi-trial baseline vs candidate, `AggregateMetrics`, `detect_regressions`, `evaluate_safety`, `ComparisonClass`, signed `EvolutionEvidence` consumed by promotion eligibility. **But** the default benchmark's task bodies mostly assert that `evolution_config.json` exists/doesn't exist — it measures whether the sandbox harness ran, not whether the agent got better. There is no task corpus of real agent work, no cost/latency budget enforcement against a target, no long-horizon task, and no quality rubric.

### B.9 CLI / API / UI

- **CLI** (`cli.py`, 835 LOC): a single flat `evo` command with **222 mutually-exclusive flags** and no subcommands (`build_parser` at line 35; a single 1-line boolean expression at `cli.py:272` enumerates ~170 flags to decide "is this an inspection call"). Functionally complete and honest ("The CLI is an interface to existing authorities, not an additional authority channel"), but it will not survive two more layers. It needs `evo <noun> <verb>` before anything else grows.
- **API**: **none.** No HTTP server, no RPC, no sockets in `evo_agent/`. `web/server/index.ts` is a static file server (`express.static` + SPA fallback) with no agent routes, and `web/client/src/pages/Home.tsx` contains no `fetch`/`/api/` call — the README is accurate that it "is an interface prototype only".
- **Desktop**: Tauri (`desktop/src-tauri/`) + a PyInstaller sidecar talking to `desktop/bridge/evo_desktop_bridge.py`, which exposes exactly **7 commands** (`get_profile`, `get_status`, `list_tasks`, `submit_goal`, `run_cycle`, `set_safe_mode`, `kill_switch`, `approve_task`) and refuses anything else. This is the best-designed surface in the repo: a deliberately narrow, authority-respecting protocol. Windows qualification/release/desktop workflows exist under `.github/workflows/`.
- `pilot/v1_task_corpus.json` + `scripts/run_v1_pilot.py` = a real offline pilot corpus. Small, offline-only, but the right shape to grow into the benchmark substrate.

### B.10 Two smaller but load-bearing defects

1. `AgentKernel._architecture_version()` **returns `""` unconditionally** (`kernel.py:255-256`), so every experience written through the kernel path carries an empty `architecture_version`, while `AgentRuntime._architecture_version()` (`runtime.py:1472`) correctly resolves it from the manifest. The kernel's `RUNS_ON`-style correlation between *architecture* and *performance* — the whole basis for benchmark-driven promotion — is silently broken on the legacy path.
2. `EvolutionOrchestrator` reaches into `promotion._active_version()` (private) at `orchestrator.py:764,927`; there is no public authority API for "what is active", which any adapter layer will immediately need.

---

## B.11 What Evo already implements (keep, do not rebuild)

Capability that must be treated as **already solved** and therefore forbidden to duplicate:

1. **Sovereignty model** — `SecurityPolicy` as the single approval/permission authority; approval never inferred from model output; risk enum → approval mapping; exact task+environment approval scope with invalidation on environment change.
2. **Audit spine** — append-only typed `events` with bounded payloads and 282 `EventType`s (`models.py:240-524`), covering `TOOL_PERMISSION_CHECKED`, `APPROVAL_*`, `ADAPTATION_TRIGGERED`, `REPLAN_TRIGGERED`, `METAMORPHOSIS_*`, `PROMOTION_*`, `ROLLED_BACK`, `WORLD_*`. Every layer emits into the same stream.
3. **Immutable persistence authority** — one SQLite store, 120 tables, integrity-checked, fail-closed recovery, versioned payloads.
4. **Checkpoint/rollback** — workspace checkpoints + promotion-level atomic symlink switch with automatic `_rollback_after_failure` on post-promotion health failure.
5. **Candidate isolation** — namespace sandbox with network denial, read-only production baseline, immutability verification, sanitized env, command allowlist, process-group termination.
6. **Comparative evidence chain** — proposal → approval → experiment → benchmark evidence → promotion eligibility (time-of-use re-check) → approval → promotion → health → rollback, each with integrity hashes.
7. **Architecture self-description** — manifest with components, dependencies, interfaces, capabilities, protected list, integrity hash, reverse-dependency impact analysis.
8. **Adaptive-during-execution semantics** — assessment, strategy selection, recommendation, bounded retry, bounded replan, strategy change, all evented.
9. **Background evolution scheduler** — opportunities, work items, queues, cooldowns, dedup, approvals, resumable state machine.
10. **Bounded supervision** — process lock, journal, backups, health, crash reporting, safe mode, kill switch.
11. **Provider-neutral model layer** — `ModelRegistry`, `ModelRouter`, `ModelFallbackEngine`, per-task selection records, model health/evaluation/trials (1,186 LOC) — already the shape of "different models for different roles".
12. **Multi-agent *contracts*** — `SpecialistTaskContract`, context isolation, permission/provenance/health models, evidence fusion and conflict detection — i.e. the *safety envelope* for delegation already exists even though the *execution* does not.
13. **Release engineering** — production gate with protected-file digests, Windows build/qualification, winget manifest, Tauri desktop, pilot corpus and acceptance docs.

## B.12 Gaps that integration must close (numbered G1–G13, used throughout the plan)

| # | Gap | Why it blocks "unified autonomous agent" |
|---|---|---|
| G1 | No iterative LLM tool-calling loop (2-method `ModelAdapter`) | Cannot adapt mid-reasoning; no real agency |
| G2 | **No executable self-extension** — promoted change alters nothing | "Acquire new capabilities" is currently a record-only claim |
| G3 | No skills / declarative capability packages | No safe unit of acquisition or metamorphosis |
| G4 | No MCP | Requested tool/MCP layer does not exist |
| G5 | No web research/fetch/browser capability; `HTTPAPIConnector` needs an injected `requester`, no default | Deep-research class tasks impossible; `web_research` capability is declared with no provider |
| G6 | Specialists are contracts without executors (`execute_task` requires an injected `executor`) | "Multi-agent" is a registry, not delegation |
| G7 | Verification is 4 string heuristics | No way to judge quality of non-trivial output |
| G8 | Benchmark measures the harness, not agent competence | Cannot prove an improvement is an improvement |
| G9 | No API surface at all; ~200-flag CLI; UI unconnected | Not usable as a single product; CLI will not scale |
| G10 | Memory not consulted at plan time; kernel `_architecture_version()` dead | Learned experience and architecture correlation unused/broken |
| G11 | Runtime tool execution unsandboxed; allowlist bypassable via `python3 <written file>` | Autonomy at scale is unsafe on this boundary |
| G12 | Context growth unmanaged (no compaction/summarization; observations tail-1000-chars) | Long-horizon tasks overflow |
| G13 | No streaming/progress, no artifacts, no report generation | No usable product surface for research output |

---

## B.13 Test-suite and doc-trust baseline

- Tests are real and dense (5,691 LOC / 27 files, 355 tests, incl. adversarial `test_production_security_audit.py`, `test_production_resilience.py`, `test_production_evolution_stress.py`). This is a trustworthy safety net to build on.
- **README/ARCHITECTURE.md overstate a few things** and must not be taken as the integration contract: `ARCHITECTURE.md` (99KB) documents the Phase-1 flow as `ModelAdapter.create_plan()` driving execution (it omits that memory retrieval is a raw `recent_memories()` query), and neither doc mentions that `evolution_config.json` is never consumed or that `versions/active` is never loaded. Everything in this audit was confirmed in source; where docs and source disagree, source wins.

---

## B.14 Overlap matrix — what must NOT be duplicated

See `01-INTEGRATION-MAPS.md` for the full mapping. Summary of hard rules:

**Evo owns and upstream must not replace:** `SecurityPolicy`, `Verifier`, `SQLiteStore`/events, `CheckpointManager`, `SandboxEngine` (as authority), `PromotionEngine`, `MetamorphosisEngine`, kill switch/safe mode, `AgentRuntime` lifecycle, `ExperienceEngine`/`EvaluationEngine`, `MemoryManager`.
**Port the pattern, do not vendor the code:** DeerFlow middleware pipeline, memory-backend protocol, sandbox-provider protocol, skill package format + security scanner, receipt/judge verification; DSH plugin inventory + per-package invariants + hooks + guard policies + plan mode + compaction.
**Bridge as optional backend, never as user-facing agent:** real DeerFlow graph execution; `dsh` as a delegated external harness.
**Skip entirely:** DeerFlow `app/channels/*` (IM connectors), `app/gateway/*` auth/JWT/OIDC/CSRF, frontend themes, LangGraph/`langgraph.json`, PostgreSQL; DSH `client/ui-*` (~50 packages), `typert`, themes, ACP client shell, LSP packages.
