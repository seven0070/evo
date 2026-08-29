# Comparative Architectural Analysis — Evo vs DeerFlow vs DeepSeek Harness

Evidence base: all three read from source in this workspace. Evo = the checked-out tree. DeerFlow = `bytedance-deer-flow-bf3e792`
(23.9 MB tarball; harness package **114,536** LOC Python; `agents/middlewares` 14,427, `persistence` 10,032, `skills` 6,217,
`sandbox` 5,065, `subagents` 3,104). DeepSeek Harness = `deepseek-ai-deepseek-harness-cd5ef81` (16.6 MB; **58 package roots**,
**247 `invariant.ts` files**, **23 `tool-*` packages**, **259 distinct package deps**, native C11 Landlock launcher).

Load-bearing claims carry `path:line`.

---

## 0. Structural comparison

| Dimension | **Evo** | **DeerFlow** | **DeepSeek Harness** |
|---|---|---|---|
| Language / floor | Python `>=3.11` (`pyproject.toml`); runs 3.11.2 here | Python `>=3.12`; `.python-version` = 3.12 | TypeScript/Node, `engines.node` = `^22.19.0 \|\| >=24.0.0` |
| Dependencies | **0** runtime | ~25 direct + `[postgres,redis,discord,buzz,browser,memory-zh,monocle]` extras | 259 across workspace; non-UI runtime ≈ zod(27), commander, ws, koffi (FFI), `@modelcontextprotocol/sdk`, `@agentclientprotocol/sdk`; lexical/react (UI) |
| Concurrency model | **`async def`: 0 occurrences in `evo_agent/`** — fully synchronous; `threading` in 4 modules (runtime lock, supervisor) | **async-first**: LangGraph runtime, `await model.ainvoke`, `acquire_async` → `asyncio.to_thread` "so those blocking operations run in a worker thread instead of stalling the event loop" | Node event loop; `Phase = idle \| maintenance \| running`; `DEFAULT_MAX_PARALLEL_TOOL_CALLS = 10` |
| Truth source | SQLite, **120 tables**, append-only `events` (282 `EventType`s), 311 store methods | LangGraph checkpointers + `persistence/` (10k LOC) + run events + thread state; SQLite **or** Postgres; Redis for capacity/caches | **Event-sourced session log** — agent-loop docstring: *"Every request is derived from the session log"*; `session-persistence-sqlite` (82 files) |
| Agent loop | `AgentRuntime.run_cycle` → `CognitiveOrchestrator.run_goal` → `AgentKernel.run` (plan-then-execute, bounded replan) | Lead-agent graph + ~45 middlewares + sub-agent fan-out | `core/agent-loop` driver over **queued turns and step-boundary input** (`PreStepDecision`) |
| Capability unit | `ToolSpec` (4 hardcoded) + `Capability`/`Tool` rows (descriptors only) | 29 `SKILL.md` skills + 12 builtin tools + MCP + ~18 providers | 23 `tool-*` packages + `skill` + plugins (Cordis fibers) |
| Verification | `Verifier`, **37 LOC**, 4 string heuristics | `tool_receipt`(150)+middleware(156) + `runtime/goal.py` strict evaluator (**fail-closed**) | 247 runtime invariants (**violations throw**); feedback |
| Isolation | bwrap/unshare **for evolution candidates only**; tools run on host | `SandboxProvider` ABC + 7 backends; mount projection; env policy; clamps | `sandbox-local`, `sandbox-windows-acl`, **native Landlock launcher**, `e2b` |
| Self-modification | Governance-complete, **behaviourally inert** (`00-AUDIT.md` §B.3) | `tools/skill_manage_tool.py`: agent may write skills, gated by **security scan** only (`decision=="block" → raise`) + `skill_evolution.enabled=False` | Plugin/preset system; **no promotion/rollback/benchmarking of own behaviour** |
| Evolve → benchmark → promote → rollback | **Yes — unique among the three** | No | No (`BENCHMARK.md` is project perf, not agent evolution) |
| Multi-user / authn | Single operator (explicit non-goal) | FastAPI gateway + JWT/OIDC/CSRF/RBAC + IM channels | `identity/anonymous-user-id`, `credentials`, Web/TUI |
| Maturity posture | v1 frozen, release-gated, Windows-qualified | Production service, 81k★ | *"developer preview… **THERE WILL BE COMPATIBILITY-BREAKING CHANGES**"*; `SAFETY.md`: not a security control |

**The asymmetry that decides the integration.** Evo is the only one of the three with a *sovereignty and evolution* spine (approval authority, audit spine, isolated candidates, comparative evidence, promotion, rollback, kill switch). DeerFlow is the only one with a *rich execution substrate* (providers, sandbox backends, skills, MCP, sub-agents, research). DSH is the only one with a *disciplined extensibility grammar* (plugin inventory, runtime invariants, per-OS sandbox backends, event-sourced derivation). None can supply another's core; each supplies exactly what the other two lack.

---

## 1. Functionality Evo already has (upstream must not shadow it)

1. **Governance & sovereignty** — `SecurityPolicy` as sole approval authority; risk→approval mapping; approvals never inferred from model output; exact task+environment approval scope invalidated when `environment_version` changes (`runtime.py:1177-1198`).
2. **Audit spine** — append-only typed events, 282 `EventType`s, bounded payloads, one stream shared by every layer.
3. **Persistence authority** — 120 tables, integrity PRAGMAs, fail-closed on corruption, 311 typed accessors.
4. **Candidate isolation** — bwrap **probed before use** (`_bwrap_usable`), `--unshare-net`, RO bind of production, writable candidate/results/home only, sanitized env, command allowlist, process-group kill, immutability verified by manifest hash, `_isolation_policy()` recorded in evidence. Its *probe-then-degrade* reasoning is more explicit than either upstream's.
5. **Comparative benchmark → evidence → promotion → rollback** — `EvolutionEvidence` integrity hash, `validate_eligibility` re-checked **at time of use**, atomic symlink switch, `_rollback_after_failure` on post-activation health failure. **Absent in both upstreams.**
6. **Architecture self-model** — manifest with components/dependencies/interfaces/capabilities/protected list, per-component `integrity_hash`, reverse-dependency `affected_subgraph`, `classify_change → "protected"`.
7. **Runtime lifecycle** — start/pause/resume/stop/kill, safe mode, heartbeats, scheduler with dependencies + condition satisfaction, resource pressure, `FailureClass` recovery taxonomy, event-loop wake, process lock, crash reporter, integrity-checked backups, supervisor.
8. **Adaptation during execution** — assess → strategy → recommend → plan → verify → bounded retry/replan, fully evented (`flexibility.py`).
9. **Experience/evaluation separation** — deterministic `evaluation-v1`, reproducible per evaluator version, independent of the verifier's authority.
10. **Model intelligence** — registry, router, fallback plan, per-task selection records, health/trials/cost profiles (1,186 LOC).
11. **Advisory cognition** — self-model + meta-cognition + calibration + decision readiness (741), strategic autonomy (742), adaptive learning with counterfactuals and **learning rollback** (677).
12. **Multi-agent contract envelope** — `SpecialistTaskContract` scope/allowlists/limits/provenance/**prohibited_actions**, evidence fusion + conflict detection.
13. **Persistent memory with provenance** — types, decay, supersession, links, procedures, feedback, archive/restore/expiry, integrity report.
14. **World/environment intelligence** — snapshots, diffs, change detection, freshness, plan invalidation, provider failover, surprise detection (920 LOC).
15. **External integration governance** — `ExternalAccessPolicy`, credential *metadata* only, idempotency, operation risk classes, observation provenance, content safety, failure taxonomy, approvals.
16. **Surfaces & release engineering** — 222-flag CLI, Tauri desktop with an **8-command** bridge that refuses all else, pilot corpus + acceptance docs, production gate with protected-file digest sandwich, Windows qualification/release, winget.

## 2. Functionality DeerFlow adds

1. **A real turn pipeline** — ~45 middlewares with *per-edge documented ordering rationale* (§14 L10) covering input/tool-result sanitization, output budgets, receipts, dangling-tool-call repair, error handling, loop detection (2-layer, windowed), read-before-write, skill activation, subagent limits, todo, summarization, deferred-tool filtering.
2. **Skills as a governed capability format** — frontmatter allowlist (`validation.py`), `required-secrets` / `secrets-autonomous` (`deerflow:frontmatter.py:21-22`, `deerflow:parser.py:137-174`), installer hardening (traversal, colon/absolute, **zip-bomb 512 MB running total**, member-count, executable refusal, resolve-then-`is_relative_to`), **fail-closed scan** (unparseable verdict ⇒ `block`, `deerflow:security_scanner.py:170`), `describe`/`projection` progressive disclosure, review/analyzer/resource-graph tooling, per-`(user_id, skill_name)` write lock. 29 shipped skill packages = a portable corpus.
3. **Sandbox as a provider abstraction** — `SandboxProvider` ABC (`acquire/acquire_async/get/release/reset`), `Sandbox` objects, env policy, path patterns, file-op locks, **enabled-only mount projection** (`skills/projection.py`, 609 LOC), virtual→actual thread path mapping and disabled-skill path dropping inside tool resolution (`deerflow:sandbox/tools.py:156-291`), per-tool `upper_bound` clamps, error sanitization.
4. **Research/execution toolset** — 12 builtins (`task`, `batch_task`, `background_tasks`, `clarification`, `tool_search`, `present_file`, `view_image`, `list_uploaded_files`, `invoke_acp_agent`, `review_skill_package`, `setup_agent`, `update_agent`) + ~18 provider integrations.
5. **Pluggable memory backends** — `manager.py` + deermem (extraction, consolidation, staleness review, eviction, 7-pattern message taxonomy), mem0, honcho, openviking, noop.
6. **MCP as a subsystem** — session pool, OAuth, header/context injection, cache, interceptors, user-scoped auth, task-shaped calls, server→tool registration (3,309 LOC).
7. **Sub-agent capacity system** — 3,104 LOC with `capacity.py`, batch runtime, **clamped** limits (`clamp_subagent_concurrency`, `clamp_total_subagents_per_run`), `token_collector`, `status_contract`, step events.
8. **Two-layer authorization** — assembly-time tool filtering + execution-time guardrail enforcement sharing **one principal builder** (`deerflow:authz/adapter.py:13-15`, `deerflow:principal.py:3`).
9. **Receipt-based verification + strict completion gate** — message-derived deterministic receipts with a **vocabulary firewall** (only the hard gate owns `satisfied`; advisory layers must say `supported`/`citation_resolved`), and `runtime/goal.py::evaluate_goal_completion` — *"using ONLY the visible conversation evidence"*, fail-closed `missing_evidence`, typed blockers.
10. **Extension contract discipline** — additive-by-default Protocol methods/fields, narrow `HostPolicySnapshot` projection, **semantic placement** (`MODEL_LOGICAL`/`MODEL_PHYSICAL`/`TOOL_VISIBLE`) so "the host [is] free to restructure its stack".
11. **Operational surfaces** — artifact/upload/feedback/scheduled-task/memory/skill/subagent/MCP/model routers; TUI; tracing attribution for standalone model calls.

## 3. Functionality DeepSeek Harness adds

1. **A runtime invariant mechanism** — `runtime-diagnostics/invariants`: package-owned companion plugins; `InvariantFailure = (message) => never` (**violations throw**); `InvariantError{code:'INVARIANT', packageName}`; listeners **prepended** "prevent[ing] a short-circuiting replay listener from silencing the check"; selectable via `Config{enabled, package_allowlist, package_blocklist}`; *"ordinary package entrypoints stay independent of diagnostics"*; and the discipline of declaring **"No runtime invariant" with a stated reason** (`plugin-inventory`, `repeat-tool-reminder`).
2. **Request-reconstruction as an enforced property** — `core/agent-loop/src/invariant.ts`: outbound request must be frozen, carry a live session id, its `messages` must equal `session.deriveMessages()` exactly, and the log must contain `step/start` + `request/header`. "The prompt you sent is provably the prompt the log says you sent", enforced at runtime.
3. **Per-OS sandbox backends** — `sandbox-local`; **`sandbox-windows-acl`** (Win32 ACLs via `koffi` FFI); **`native/landlock-run`**: *"a [Landlock] self-restrict-then-exec launcher… ~300 lines of C11 over the raw kernel UAPI, statically linked against musl… the ruleset is inherited across `execve`, so the command and every process it spawns run confined while the invoking process stays unrestricted. **Fail-closed: if the kernel cannot enforce, it exits without running the command**"* — with **deliberately no install-time build fallback** ("the probe reports `unusable`, and the consumer falls closed").
4. **Event-sourced derivation + snapshot testing** — every request derived from the session log; `session-query`/`session-log-export` as a read layer; `vitest` configs for snapshot / expected / replay / perf / stress.
5. **Loop guards + step-boundary input** — `guard/repeat-tool-reminder`, `guard/timeout-policy`, `PreStepDecision`, `wakeRequested`, maintenance phase, `DEFAULT_MAX_PARALLEL_TOOL_CALLS`.
6. **Plan mode** as a distinct read-only phase with an explicit transition to execution.
7. **Hooks as a public protocol** — `hook-protocol/{events,codec,matcher,detached}`.
8. **Everything-is-a-plugin composition** — `plugin-inventory` (branded ids, effective enablement incl. disabled ancestors, fiber phase machine), bundles (`base/headless/web-app/sdk-app/sdk-minimal/acp-app`), presets, plugin-owned settings surfaces.
9. **Adversarial plugin corpus** — fixtures that throw, arrive late, self-dispose, require missing services.
10. **Context/compaction/spill** — `compaction-basic/{config,region}`, `command-compact`, `spill`, `context/agent-instructions` (AGENTS.md discovery + digest + render), `token-meter`.
11. **Small governed packages** — goal, todo, feedback, attachment, credentials, schedule, workflow, each with its own invariant.
12. **ACP + external-agent delegation** (`subagent-acp`, `subagent-claude-code`) — the template for treating another agent binary as a bounded executor.

## 4. Duplicate functionality (implement once, in Evo)

| Concern | Evo | DeerFlow | DSH | **Owner** |
|---|---|---|---|---|
| Persistence authority | `storage.py`, 120 tables | `persistence/` 10k + checkpointers + PG/Redis | `session-persistence-sqlite` | **Evo** |
| Append-only event log | `events`, 282 types | `run_events`, `step_events` | session log (+*derivation*) | **Evo** (adopt the derivation idea) |
| Memory | `memory.py` + 6 tables | `agents/memory/*` backends | context/compaction only | **Evo**; upstream = *backends*, not replacement |
| Tool registry | `tools.py` + `ToolIntelligenceRegistry` | sandbox+provider+MCP tools | 23 `tool-*` | **Evo** |
| Capability registry | `capability.py` (1,073) | skills/tools/`features.py` | skills/presets/plugins | **Evo** |
| Sandbox isolation | `SandboxEngine` | `SandboxProvider` + 7 backends | local/acl/landlock/e2b | **Evo** authority; backends pluggable |
| Approval / permission | `SecurityPolicy` | guardrails + authz RBAC | credentials/authorization | **Evo** |
| Scheduling | `runtime.Scheduler` | `scheduler/service.py` | `schedule/schedule` | **Evo** |
| Model routing | `model_intelligence.py` | `models/*` | `llm/*` | **Evo** |
| Delegation | `specialist.py` | `subagents/*` | `subagent/*` | **Evo** envelope + ported executors |
| Verification | `verifier.py` | receipts + goal gate | invariants | **Evo**, fusing all three (§13.9, §14) |
| Benchmark / promote / rollback | 3 engines | — | — | **Evo** |
| Self-model / learning | 3 layers | memory consolidation | feedback | **Evo** |
| Workspace diff | `world.py` | `workspace_changes/*` | fs + session diff | **Evo** (3 deltas only) |
| MCP | — | `mcp/*` | `mcp` | **build once** in Evo |
| Skills | — | `skills/*` | `skill/*` | **build once** in Evo |
| Web fetch/search | — | ~18 providers | `web/tool-web` | **build once** in Evo |
| Context compaction | ad-hoc truncation | summarization | compaction/spill | **build once** in Evo |
| Loop guards | — | `loop_detection` | `guard/*` | **build once** in Evo |
| **⚠ Inside Evo** | `memories` (4-col legacy) **and** `memory_records` (rich), both live — `storage.py:88` vs `:817` | — | — | **consolidate; the kernel reads the wrong one** |

## 5. Components to reuse (Evo-side, as-is or extended)

`storage.py` (all) · `security.py` · `checkpoints.py` · `runtime.py` (lifecycle/scheduler/recovery/heartbeat/shutdown) · `orchestrator.py` (work items, queues, cooldowns, approvals) · `promotion.py` · `sandbox.py` (its bwrap/unshare/probe becomes the default `SandboxProvider`) · `benchmark.py` **machinery** (keep `TaskCase`/`AggregateMetrics`/`detect_regressions`/`EvolutionEvidence`; replace only the corpus) · `evolver.py` · `metamorphosis.py` (validate/compat/impact are sound; replace the inert materialization) · `capability.py` · `memory.py` · `world.py` · `experience.py`/`evaluation.py` · `model_intelligence.py` · `self_model.py` · `strategic_autonomy.py` · `adaptive_learning.py` · `external.py` (policy/provenance/idempotency, **not** its fake connectors) · `specialist.py` (envelope) · `production.py` · desktop bridge (narrow-surface template) · `run_production_gate.py` (digest sandwich).

Rule: **reuse = "this is the implementation"; extension = "add a seam here".** Reuse never means importing upstream code.

## 6. Components to wrap through adapters

| Adapter | Wraps | Why wrap, not absorb |
|---|---|---|
| `ExecutionBackend` | native loop · lead-agent (DF extra) · `dsh` (subprocess) · `code_exec` | Keeps one loop; makes the loop a metamorphosis **subject** |
| `TurnEngine` | OpenAI tool-calling · Anthropic · offline rule-based | `ModelAdapter` demoted to one impl of a wider port — no breakage |
| `SandboxProvider` | bwrap/unshare (existing) · **landlock-run** · docker · e2b · windows-acl | OS specifics must not leak into the kernel |
| `MemoryBackend` | Evo default · deermem patterns · mem0/honcho | Store stays the authority; backends are strategies over it |
| `ToolProvider` | native · skills · MCP · plugins | Registration is a governed act in Evo |
| `ResearchProvider` | stdlib fetch · DDG/Tavily/Jina/… · crawl4ai | Network is the privilege boundary |
| `VerifierPlugin` | receipts · checklist · strict evaluator · invariant checks | Verdict authority stays sovereign |
| `CredentialStore` | local file / keyring (DSH shape) | Evo keeps *metadata* in SQLite; secrets stay out |
| `DelegationExecutor` | llm · sandbox · `dsh` · ACP agent | Reuses Evo's contract envelope instead of a new agent framework |

## 7. Components that become Evo skills or plugins

**Skills** (declarative, benchmarkable, promotable): port `deep-research`, `data-analysis`, `chart-visualization`, `academic-paper-review`, `code-documentation`, `consulting-analysis`, `find-skills`, `bootstrap`; Evo-native `cited-report`, `workspace-audit`, `evolution-explain`, `skill-author`, `run-qualification`.

**Plugins** (code behind allow-listed entry points, isolated failure, **no new authority**): MCP servers; research providers; sandbox providers; pipeline stages from a reviewed set; delegation executors; verifier plugins.

**Never a skill or plugin:** governance, approval authority, verification authority, audit, rollback, shutdown, promotion rules.

## 8. Components that remain external services

1. **`landlock-run`** — invoke, never vendor (C11 + musl static binary, fail-closed probe). Ideal external shape: absent ⇒ `unusable` ⇒ **fall closed**.
2. **`dsh` CLI** — PROCESS-mode delegated executor inside Evo's sandbox; output untrusted; never an authority.
3. **MCP servers** — separate processes by definition; governed by `mcp/policy.py`.
4. **Model providers** — HTTP, behind `model_intelligence`.
5. **Docker / E2B backends** — opt-in; candidate execution and delegated code runs only.
6. **Optional Python extras** (`langgraph`, `mcp`, `bs4`) — never in the base install; `dependencies = []` is a guarantee, not an aesthetic.
7. **IM channels / webhook ingress** — external; if ever wired, an adapter feeding the runtime queue, never a parallel agent.
8. **DeerFlow gateway** — external at most; Evo exposes its own stdlib RPC and delegates no authority to it.

## 9. Components that must NOT be integrated

| Rejected | Reason |
|---|---|
| LangGraph StateGraph / `langgraph.json` / checkpointers | Second loop + second state authority |
| FastAPI gateway, JWT/OIDC/CSRF, RBAC multi-user authz | Conflicts with local-first single operator; creates a competing authority surface |
| `app/channels/*` (Slack/Discord/Telegram/Feishu/DingTalk/WeChat/buzz) | Scope creep + attack surface, no sovereignty benefit |
| Postgres / Redis | Evo's SQLite is the authority; clamped in-process counters replace DF's Redis capacity cache |
| `models/patched_*.py` provider hacks | Upstream-version-specific; Evo owns routing |
| DSH `client/ui-*` (~50 packages), `typert`, themes, skins | Evo has Tauri + thin web shell |
| DSH Cordis runtime / fibers | Different programming model; port *inventory + lifecycle* shape only |
| DSH `session-persistence-sqlite` / `session-query` | Duplicate persistence authority |
| DF's private `starlette._utils.get_route_path` import | Their own comment concedes a Starlette bump is "a security-relevant change" — never inherit a private symbol at a boundary |
| DF `persistence/` (10,032 LOC) · `workspace_changes/` | Duplicates of `storage.py` · `world.py` |
| `title_middleware`, `suggestions`, `input_polish`, `assistants_compat`, LangGraph Studio | Chat-product features, not agent capabilities |
| Any judge as a **verdict source** | Advisory only; `satisfied` is sovereign — and DF's `judge_enabled` knob is declared and never read |
| Vendoring either repository | MIT licensing is fine; *coherence* is not. Either would fork Evo's identity |

## 10. Dependencies and compatibility conflicts

| # | Conflict | Sev | Resolution |
|---|---|---|---|
| D1 | **Python floor: Evo `>=3.11` vs DeerFlow `>=3.12`** (this workspace runs 3.11.2) — `pip install .[deerflow]` is *rejected* on Evo's own floor | High | Raise Evo's floor to 3.12 **in the same release as the bridge extra**; keep core importable on 3.11 until then. CI already uses 3.12 |
| D2 | Zero-dep guarantee vs DF's ~25 direct deps (fastapi, bcrypt, pyjwt, e2b-code-interpreter, slack-sdk, lark-oapi, dingtalk-stream…) | High | Extras only; `backends/availability.probe()`; `test_degradation_matrix.py`; base install verified in the clean-venv gate step |
| D3 | DF pins `starlette>=1.3.1,<2` **with an upper bound** and imports a private symbol at a security boundary | Med | Never inherit. If the bridge is used, resolve DF's pins in an **isolated venv** (`evo backends install deerflow --venv <path>`), so Evo's interpreter never carries them |
| D4 | DSH needs `node ^22.19.0 \|\| >=24.0.0`, `pnpm@11.7.0`, prebuilt native packages | Med | `PROCESS` mode: requires `dsh` on PATH; absent ⇒ backend `unusable` ⇒ not selectable. Zero npm involvement from Evo |
| D5 | `landlock-run` needs Landlock (Linux ≥5.13) **and** a platform package; no build fallback by design | Low | `probe()` → `unusable` → fall closed to `local_bwrap`; if that is unusable too, host execution needs explicit operator override + permanent `SECURITY_DEGRADED` event |
| D6 | Windows path semantics (DF colon/drive handling, DSH windows-acl) vs Evo's POSIX `shlex` assumptions in `security.py` | **High for this repo** (Windows desktop + qualification pipeline ship) | Per-OS `SandboxProvider`; Evo path checks gain colon/UNC/ADS (`file.txt:stream`) rejection via `Path.parts`; Windows path tests in the qualification job |
| D7 | SQLite WAL single writer (Evo) vs Postgres/Redis (DF) vs own SQLite (DSH) | Med | Bridges read/write **only through Evo APIs**; no shared DB files |
| D8 | LangChain/LangGraph message objects leaking into Evo's data model | High | Ports exchange only Evo dataclasses; `test_import_purity.py` asserts no `langchain`/`langgraph` import anywhere in `evo_agent/**` except `backends/lead_agent.py` |
| D9 | DF uses 3.12 idioms (`StrEnum`) vs Evo's 3.11-safe style | Low | Permitted once D1 lands; core stays 3.11-compatible meanwhile |
| D10 | Evo `version.py` is a bare constant; DF guards version sync with a `verify-versions` CI job | Low | Add a gate step asserting `version.py` ↔ `pyproject.toml` ↔ `PROTECTED.manifest` agreement |
| D11 | DF dev group uses `blockbuster` (blocking-IO detection) and `hypothesis` | Low (informative) | Adopt **snapshot/replay testing** (DSH-style) and property tests for ports in the `dev` extra only; never in the base install |

## 11. Runtime conflicts

1. **Sync/async inversion — the single biggest one.** `evo_agent/` has **zero `async def`**; Evo is a blocking, subprocess-driven kernel with a `threading.RLock`. DeerFlow is async-first and *documents* that blocking work must be moved off the loop. Consequences: never call an async backend from the kernel; a bridge must own its loop in a dedicated thread/process behind a **synchronous** façade; async stays confined to `serve/`. Resolution: `ExecutionBackend.run_turn(ctx, sink)` is **synchronous by contract**; `backends/lead_agent.py` may use `ThreadPoolExecutor` + `asyncio.run` internally, never in Evo's thread. Enforced by `test_no_async_leak.py`.
2. **Loop-count conflict.** Evo already runs 2 loops + 3 cycles (`AgentKernel.run`, `CognitiveOrchestrator.run_goal`, `AgentRuntime.run_cycle`, `run_forever`, `EvolutionOrchestrator.run_cycle`). Adding DF's graph and DSH's driver would make five. Resolution: one-loop registry + CI test; each extra loop lives **inside a subprocess or a single `run_turn` call**, never concurrently over the same task.
3. **Poll loop vs wake-driven loop.** DSH: `Phase` machine + `wakeRequested` + a dedicated `maintenance` phase. Evo: `run_forever(sleep_seconds=0.25)` + `EventLoop.wake()` (already present). Resolution: keep Evo's (simpler, restart-safe, tested); port the **maintenance phase** as `RuntimeCycleKind.MAINTENANCE` inside `run_cycle`, so compaction/gap-analysis/skill-scan don't occupy execution slots.
4. **Cancellation semantics.** DSH `AbortController` + `CancelOptions{cause}`; DF LangGraph interrupt; Evo `cancel_task` + `SIGTERM→SIGKILL` process group. Resolution: one Evo `CancelReason` propagated to queue → subprocess group → `backend.cancel(turn_id)`; a cancel must never leave a `RUNNING` row (already true for tasks via `_recover_interrupted_tasks`; extend the assertion to backends).
5. **Parallel tool calls.** DSH defaults to 10 parallel-safe calls per step; Evo is strictly sequential and its tests assume that. Resolution: `max_parallel_tool_calls` default **1**, clamped [1,10], gated on `ToolSpec.parallel_safe`, enabled **only after** receipts land — a gapped ledger is worse than a slow run.
6. **Single SQLite writer.** 120 tables + WAL. Resolution: writes serialized through `SQLiteStore._connect()`; backends write only via store APIs; no extra threads opening the DB; benchmark trials keep separate workdirs (already true).
7. **Import graph / startup cost.** Evo lazily imports inside methods to dodge cycles (`from .capability import …` in `AgentKernel.__init__`). Optional extras must never be imported at package import time or the zero-dep guarantee dies. Resolution: `importlib.util.find_spec` probes only; `test_import_purity.py`.
8. **Port and stdio ownership.** DSH binds `127.0.0.1:3080`; DF's gateway binds its own; Evo's desktop bridge is stdio JSON-per-line. Resolution: Evo services bind loopback + token, **port 0 = ephemeral** by default; no backend may bind a port in Evo's process space.
9. **Blocking-IO discipline.** Any adapter wrapping an async library must not block Evo's thread. Resolution: every backend call carries a monotonic deadline; overrun ⇒ cancel + `FailureClass.TIMEOUT` + partial receipts retained.

## 12. Memory conflicts

1. **Two memory authorities inside Evo.** `memories` (legacy `kind/content/created_at`, integer PK — `storage.py:88`) and `memory_records` (rich, provenance/confidence/importance/supersession — `storage.py:817`). The kernel reads the **legacy** table (`storage.py:1288`), bypassing 890 LOC of memory intelligence. Resolution: `memory_records` becomes the sole read path; `memories` kept one release as a write-only mirror; migration folds legacy rows in with `source='legacy_memories'` provenance.
2. **Store-of-truth direction.** DF receipts are *derived from the message stream and never stored separately* so "rendering for the model and harvesting for the parent agent always agree"; DSH derives *every request* from the log; Evo persists derived artifacts, so copies can drift. Resolution: **Evo derives from `events` at assembly time and stores only derivation inputs** — no stored copy of "the prompt", therefore nothing to drift, and no positional ids to renumber (repairs the DF caveat recorded as V10).
3. **Context-window ownership.** DF: summarization + output budget + token budget middlewares. DSH: `compaction-basic/{config,region}` + `spill`. Evo: `cognitive.py:679-684` pops oldest observations wholesale when a JSON blob exceeds a size cap, and `kernel.py` keeps `result.output[-1000:]`. Resolution: `context.py` with explicit **regions** (instructions, goal, strategy, recent turns, retrieved memories, receipts index), spill-to-file for oversized outputs, and every summarization recording its inputs **and dropped record ids** into the audit stream, so a promotion can be judged on what it discarded.
4. **Extraction vs capture.** DF deermem extracts facts via LLM with a 7-pattern message taxonomy + `staleness_review`; Evo captures whole experiences. Not a conflict — a policy. Resolution: `MemoryBackend.extract()` returns **candidates** that enter as `CANDIDATE` with provenance and are not retrievable into planning context until a verification tick or an age/confidence threshold — blocking the "hallucination becomes permanent memory" failure.
5. **Scope isolation.** `memory.py` has no scope field; DF user-scopes storage and locks; DSH scopes by session/fiber. With delegation + profiles sharing one DB, an unscoped memory store leaks. Resolution: `scope_key` on `memory_records` (+ index, default `'local'`), `RetrievalQuery.scopes`, delegation **narrows never widens**, `test_memory_scope_isolation.py`.
6. **Memory contents are not a metamorphosis target** — memory is evidence, not capability. The **policies over** memory (extraction, retention, retrieval weights, staleness) are. See the eligibility table in `07-UNIFIED-ARCHITECTURE-SPECIFICATION.md` §4.
7. **Forget outranks evidence.** A user-initiated delete must be immediate and unconditional, taking precedence over promotion-evidence retention: tombstone in `memory_history`, honoured by retrieval **even inside a candidate overlay**.
8. **Retrieval must not bypass approvals.** Retrieved memories can contain content from tasks that touched external systems; retrieval therefore runs after the policy filter and records `MEMORY_RETRIEVED` with the scope used.

## 13. Tool / skill conflicts

1. **Name collisions are guaranteed.** Evo: `workspace_list/read/write`, `shell`. DF: `bash`, sandbox read/write/edit/ls, `web_search`, `crawl`, `python_repl`, `task`, `present_file`. DSH: `bash`, `skill`, Read/Write/Edit, Glob/Grep, TodoWrite, WebFetch — four vocabularies for the same primitives. Resolution: Evo owns a **canonical namespace** `<domain>.<verb>` (`fs.read`, `fs.write`, `fs.list`, `exec.shell`, `exec.python`, `net.fetch`, `search.web`, `agent.delegate`, `skill.install`, `report.compose`); upstream names arrive as **aliases**. Risk is assigned by the canonical tool, never the alias — so a provider cannot name itself benign to inherit a lower risk.
2. **Descriptor vs executability.** `Capability.register_tool` writes a row with no handler; `ToolRegistry.register` attaches a handler with no row. "Adding a tool" today adds only a descriptor. Resolution: one **`ToolCatalog`** authority — usable iff (a) handler registered, (b) `Tool` row with schema/risk/permissions, (c) present in the active overlay's mount/projection set. A tool in only two states is a **startup error**, not a runtime surprise.
3. **Risk classification authority.** DF assigns per-tool config; DSH by kind (`ToolCallKind = read|edit|delete|move|search|execute|fetch|other`, `dsh:core/tools/src/presentation.ts:15`); Evo by `RiskLevel`. Resolution: `SecurityPolicy` is the only classifier; adapters may **propose a floor**, never lower one; `test_risk_floor_monotonic.py`.
4. **Scan is not governance.** DF's `skill_manage_tool` gates a live skill write by security scan alone (`decision == "block" → raise`, `StaticScanBlockedError`). Evo must not equate them: `skill.install_candidate` may write **only** into `capabilities/skills/candidates/`, and leaving candidates requires scan **and** isolation **and** benchmark **and** human approval.
5. **Enablement should be physical, not checked.** DF `skills/projection.py` materializes enabled-only trees, and `deerflow:sandbox/tools.py:201-258` *drops disabled-skill paths during path resolution*. Evo's `requires_approval` string check is far weaker. Resolution: enablement = mount/projection set.
6. **Dynamic discovery vs reproducible enumeration.** DF ships `tool_search` (runtime discovery); Evo enumerates `tools.schemas()` into the plan, which is what makes a plan reproducible and audit-able. Resolution: enumeration stays the authority snapshot; discovery is **read-only**, may *propose*, and its proposals pass through the same selection path. `tool.search` results never execute.
7. **MCP scale pressure.** With 4 tools Evo never hits schema limits; with MCP + ~18 providers, schemas alone could dominate context. Resolution: port `deferred_tool_filter` **early** (Phase 4, not 5), clamp `max_tools_in_prompt`, require explicit activation for the tail.
8. **MCP tool shadowing.** An MCP server could expose `fs.write`. Resolution: MCP tools are `mcp:<server>:<tool>` and **cannot claim a canonical name**; conflict ⇒ refused registration with `TOOL_NAME_CONFLICT`, surfaced in `evo mcp status`.
9. **Skills may narrow, never widen.** DF frontmatter declares `allowed-tools`; effective permissions in Evo = intersection(skill grant, profile, policy). Widening attempts fail install validation, and for promotable skills fail metamorphosis validation.
10. **Adopt `ToolCallKind`, don't invent again.** The 8-value side-effect taxonomy is the right hook for plan mode, approval routing, verifier expectations, and mount confinement. One taxonomy, three uses.

## 14. Agent-loop conflicts

| # | Conflict | Detail | Resolution |
|---|---|---|---|
| L1 | **Loop paradigm** | Evo = plan-then-execute with bounded replan; DF/DSH = iterative tool-call turns until no more calls. Naive fusion = plan inside turn inside cycle = three nested authority-bearing loops | **One loop at turn level.** Evo's "plan" becomes a *strategy artifact the loop consults*, not a container. `TurnDecision ::= tool_calls \| final \| request_approval \| abstain` |
| L2 | **Who decides "done"** | Evo: plan exhausted / `max_steps`; DF: strict goal evaluator; DSH: `TurnEndReason` + `PreStepDecision` | Evo's **verifier owns completion**; DF-style evaluator is one *input*; `max_steps` becomes `turn_budget` and can no longer by itself imply success |
| L3 | **Human-in-the-loop location** | DF: LangGraph interrupt + plan approval; DSH: plan-mode gate + `tool-ask-user`; Evo: exact-scope approval at the tool call | Evo's gate is the **only** authority point; `ask_user` becomes a **tool** returning `RequestApproval`, never a loop primitive |
| L4 | **Replanning authority** | Evo replans ≤1; DF replans via graph; DSH keeps taking turns | **Continue, don't replan.** Adaptation = more turns, needing no new authority; the *strategy artifact* changes only via benchmark + promotion |
| L5 | **Checkpoint vs event-sourcing** | DF resumes from LangGraph checkpoints; DSH rebuilds requests from the log; Evo replays `events` + workspace checkpoints | Derive conversation from the log (DSH); keep `CheckpointManager` for **file-tree** truth. Different objects, both needed. LangGraph checkpointers: rejected |
| L6 | **Step-boundary input injection** | DSH consumes new user messages between tool steps | Adopt as a pipeline stage **only after** receipts land, so injected content is provably ordered relative to tool results |
| L7 | **Parallel tool calls** | DSH: 10; Evo: strictly 1 | Default 1, clamped [1,10], gated on `parallel_safe` + receipts (§11.5) |
| L8 | **Does the backend own the loop?** | If `lead_agent` runs a graph, it *has* a loop; if it runs one turn, Evo does | **Backend runs one turn.** Its internal graph is its own business. Enforced structurally: `ExecutionBackend` has `run_turn`, never `run_task`/`stream_goal` |
| L9 | **Loop as metamorphosis subject** | Changing the loop changes every semantic above | Only `pipeline_stage` + `strategy_params` targets; **loop control flow is protected**; a candidate may not add a stage able to veto verification |
| L10 | **Ordering rationale is the real dependency** | DF's stack is not sorted middlewares, it is per-edge ordering with stated reasons: sanitization outermost so *"sanitised messages are what every inner middleware sees"*; tool-result sanitization deliberately **inner of** output budget so raw output is neutralized *before* truncation; receipts **outermost on the tool edge** because guardrail/audit short-circuits and *rebuilds* would "silently gap the ledger"; authorization reuses `GuardrailMiddleware` so "deny, audit, and fail-closed handling stay in one proven implementation" (`deerflow:tool_error_handling_middleware.py:155-235`) | `pipeline/engine.py` stores ordering as **declared edges with reasons**; `test_pipeline_ordering_rationale.py` fails on any stage without a declared position **and** reason. The most valuable non-functional import from either repo |
