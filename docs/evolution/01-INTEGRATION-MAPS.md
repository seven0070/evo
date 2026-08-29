# Integration Maps — DeerFlow and DeepSeek Harness

Ground truth for both upstreams was taken from their repository trees and source files at audit time
(`bytedance/deer-flow@main`, 2,434 blobs; `deepseek-ai/deepseek-harness@master`, 8,953 blobs), not from blog posts.

---

## 0. The premise that decides everything: neither project can be *vendored*

Evo declares `dependencies = []` and is offline-deterministic. The upstreams are not:

| | Evo | DeerFlow | DeepSeek Harness |
|---|---|---|---|
| Language | Python 3.11 | Python 3.12 | **TypeScript** (pnpm monorepo) |
| Runtime deps | **0** | langgraph, langchain, fastapi, pydantic, uvicorn, redis, chromadb, playwright, docker-py… | node ≥ 20, Cordis runtime, ~60 workspace packages |
| Persistence | SQLite only | SQLite **and** Postgres, Redis | SQLite session store |
| Model access | `ModelAdapter` (2 methods) | provider factories + patched SDK clients (`models/patched_*.py`) | `packages/llm/*` (llm-pi-ai, llm-deepseek), token-meter |
| Scale | 22k LOC | 2.4k files, 114,536 LOC harness, 14,427 LOC middlewares | 8.9k files, 58 package roots |
| Posture | local-first, single-user, offline-testable | multi-user service, gateway, authz, IM channels | developer-preview CLI/Web, "everything is a plugin" |

**DeerFlow cannot be imported** without dragging LangGraph/FastAPI/pydantic into a zero-dependency kernel, and its agent runtime is a *second* supervisor loop — which the brief explicitly forbids.
**DeepSeek Harness cannot be imported at all** — it is TypeScript; and it is itself a user-facing agent product, so running it as a peer agent would create exactly the "three competing agent loops" failure mode.

Both also self-declare as untrusted for security purposes. DSH's `SAFETY.md`: *"experimental developer-preview software … has not undergone a security audit and must not be treated as secure or production-ready … Do not rely on DeepSeek Harness as the sole security control for untrusted workloads."*

Therefore the integration rule for this repo is:

> **Port the architecture as Evo-native, dependency-free code. Bridge the real thing only as an optional, policy-gated backend. Never let either become an authority.**

Three delivery modes, used below for every component:

- **`PORT`** — re-implement the pattern inside `evo_agent/`, stdlib-only, under Evo's authorities. This is the default and the majority.
- **`BRIDGE`** — optional `[project.optional-dependencies]` extra with a real adapter behind an Evo-owned Protocol interface; absent-by-default, capability-gated, and *never* on the promotion/verification path.
- **`PROCESS`** — drive an external binary in Evo's sandbox as a delegated executor; results are untrusted data that must pass Evo's verifier.
- **`SKIP`** — do not bring it in; record why.

---

## 1. DeerFlow → Evo (Map C)

DeerFlow today is not "a deep-research framework"; per its own description it is a *"long-horizon SuperAgent harness that researches, codes, and creates. With the help of sandboxes, memories, tools, skill, subagents and message gateway"*. That is precisely the capability set Evo lacks (G1–G6, G11–G13), which is why the mapping is mostly `PORT`.

> **[grounded]** Rows below reflect the source as read from the downloaded tree, not from file names. See `05-GROUNDING-CORRECTIONS.md` §1 for the claims this replaced — including one upstream feature (`verification.judge`) that is declared in config and never implemented, and two upstream bugs Evo must not inherit (receipt renumbering after compaction; positional middleware ordering).

### 1.1 Capability → Evo component mapping

| DeerFlow component (path under `backend/packages/harness/deerflow/`) | Capability it supplies | Evo gap | Mode | Lands in |
|---|---|---|---|---|
| `agents/middlewares/*` (~45 modules: `loop_detection`, `dangling_tool_call`, `tool_error_handling`, `tool_output_budget`, `tool_result_sanitization`, `input_sanitization`, `read_before_write`, `deferred_tool_filter`, `token_budget`, `summarization`, `title`, `todo`, `safety_finish_reason`, `safety_termination_detectors`, `model_length_*`) | A **turn pipeline**: composable, ordered hooks around each model/tool step, with safety and budget enforcement | G1, G12 | **PORT** (subset) | `evo_agent/pipeline/` |
| `agents/lead_agent/agent.py` + `prompt.py`, `agents/factory.py`, `assembly_descriptor.py`, `features.py`, `goal_state.py`, `human_input.py` | Lead-agent loop with assembly-time feature flags and injectable goal state | G1 | **PORT (contract only)** — the *loop shape*, not LangGraph | `evo_agent/backends/lead_agent.py` (BRIDGE-optional) |
| `agents/memory/manager.py` + `backends/{deermem,mem0,honcho,openviking,noop}` + `summarization_hook.py`, `tools.py` | **Pluggable memory backends** behind one manager protocol; deermem has fact extraction, consolidation, staleness review, eviction, message-pattern classification (identity/goal/decision/preference/correction/reinforcement YAML) | G10, and a real upgrade to Evo's memory | **PORT** the backend-protocol idea + pattern-driven capture; **BRIDGE** mem0/honcho | `evo_agent/memory.py` (extend), `evo_agent/memory_backends/` |
| `sandbox/sandbox.py`, `sandbox_provider.py`, `local/`, `middleware.py`, `security.py`, `env_policy.py`, `path_patterns.py`, `file_operation_lock.py`, `search.py`, `overwrite.py`; `community/{e2b_sandbox,boxlite,opensandbox,tenki,aio_sandbox}` | A **SandboxProvider protocol** with multiple interchangeable backends (local, Docker, E2B, remote), env policy, path patterns, per-file locking | G11 | **PORT** the provider protocol; wire it under Evo's existing bwrap engine | `evo_agent/sandbox_providers/` |
| `skills/{catalog,parser,frontmatter,installer(351),permissions,projection(609),validation,tool_policy,package_paths,storage/*,slash,describe}.py`, `config/skills_config.py`, `skill_scan_config.py`; 29 shipped `skills/public/*/SKILL.md` | **Skills as files**, and *enablement by mount*: `projection.py` materializes **enabled-only** skill trees into the sandbox filesystem, so visibility is physical, not a runtime check. Frontmatter carries `required-secrets` + `secrets-autonomous` (credential use without a live user turn) | G3 | **PORT**; seed the 29 upstream packages as Evo builtins | `evo_agent/skills/` |
| `skills/security_scanner.py`(176), `security_static_scanner.py`, `skillscan/*`, `skills/review/*`, `config/skill_evolution_config.py`; installer hardening | **Gated self-modification of capabilities**: `enabled=False` default, `_resolve_fail_closed()` **defaults True when config is unavailable**, unparseable scanner output → `block`, executable content → `block` always (fail-open degrades only *non-executable* content to `warn`); installer rejects `..`, colons, absolute paths, **zip bombs (512 MB running total) and member-count bombs**, and non-contained members via resolve-then-`is_relative_to` | G2, G3 | **PORT — highest-value import; copy the failure semantics exactly** | `evo_agent/skills/{installer,security}.py`, `evo_agent/capability_acquisition.py` |
| `agents/middlewares/tool_receipt.py`(150) + `tool_receipt_middleware.py`(156) + `tool_result_meta.py`, `delegation_ledger.py`; the real gate in `runtime/goal.py::evaluate_goal_completion`. **NB:** `config/verification_config.py`'s `judge_enabled`/`judge_model_name` are *declared but never read anywhere* (verified by grep across the whole backend) — there is no judge to port | **Two-layer verification**: deterministic *message-derived* receipts (zero-LLM), plus a strict non-thinking completion evaluator that fails closed (`missing_evidence`) with a typed blocker taxonomy and a vocabulary firewall — only the hard gate may set `satisfied` | G7 | **PORT** (both layers) | `evo_agent/verification/receipts.py`, `evo_agent/sovereign/verification_authority.py` |
| `mcp/{client,session_pool,interceptors,oauth,cache,headers,context_headers,user_scoped_auth,tasks/*,tools}.py`, `app/mcp_tasks/service.py`, `app/gateway/routers/mcp.py` | **MCP client**: pooled sessions, OAuth, caching, header/context injection, task-shaped tool calls, server→tool registration | G4 | **PORT** the registry/policy layer; **BRIDGE** stdio transport behind an optional extra | `evo_agent/mcp/` |
| `tools/` + `community/{tavily,brave,ddg_search,searxng,jina_ai,firecrawl,crawl4ai,browserless,browser_automation,exa,serper,serply,infoquest,ragflow,image_search,tencent_wsa,groundroute,fastcrw}` | **Research toolset**: search, crawl, extract, browse, RAG, image search across ~18 providers | G5 | **PORT** protocol + 2 stdlib-only providers (fetch via `urllib`, duckduckgo html); **BRIDGE** the rest via config | `evo_agent/providers/research/` |
| `subagents/` (3,104 LOC: `registry,executor,runtime,batch_runtime,batch_service,capacity,status_contract,step_events,token_collector,builtins`) + `middlewares/subagent_limit_middleware.py` | Sub-agent fan-out with **clamped** bounds — `clamp_subagent_concurrency`/`clamp_total_subagents_per_run` mean operator config cannot configure itself out of the limit; per-delegation `token_collector` metering; typed `status_contract` | G6 | **PORT** onto Evo's existing `SpecialistOrchestrator`; adopt clamping as a global governance primitive | `evo_agent/delegation/limits.py` |
| `authz/{adapter,enforcement,principal,provider,rbac,runtime,sandbox_authz,tool_filter}.py` | Principal→permission enforcement points, RBAC, tool filtering by policy | safety | **PORT** (narrowed) | `evo_agent/security.py` (extend) |
| `guardrails/{builtin,middleware,provider}.py` | Content/behaviour guardrails as a middleware with provider plug-points | — | **PORT** as pipeline stages | `evo_agent/pipeline/stages/` |
| `runtime/{checkpoint_mode,checkpoint_cache/*}.py`, `checkpoint_patches.py` | Checkpoint cache providers (memory/redis), checkpoint modes for resume | G12-adjacent | **PORT** concept: turn-level resume; skip Redis | `evo_agent/checkpoints.py` (extend) |
| `extensions/{loader,manager,registry,stack,policy,isolation,ordering,injection,anchors,gateway,notify}.py` + `packages/extension-api/…/contracts.py` | **Versioned host↔extension contract**: every Protocol method has a default so additions stay *additive*; every optional dataclass field has a default; `HostPolicySnapshot` exposes a *narrow projection* of host config instead of the whole `AppConfig`, "because exposing AppConfig would pin every extension to the harness release cadence" | G2 | **PORT — adopt verbatim as a design rule** | `evo_agent/contracts/` |
| `reflection/resolvers.py`, `config/reload_boundary.py` | Late resolution + an explicit *reload boundary* | G2 | **PORT** | `evo_agent/runtime.py` |
| `persistence/`, `config/{postgres_schema,database_config}.py`, `scheduler/service.py`, `app/gateway/routers/{scheduled_tasks,uploads,artifacts,feedback,console,features,suggestions}.py` | Artifacts, uploads, feedback, scheduled runs, console | G9, G13 | **PORT** artifacts + feedback only | `evo_agent/artifacts.py`, `evo_agent/feedback.py` |
| `app/channels/*` (slack, discord, telegram, feishu, dingtalk, wechat, wecom, buzz/nostr, github, message_bus, dedupe_store, run_policy) | Message gateway / IM ingress | — | **SKIP** (out of scope; Evo is single-operator local-first; revisit only as `evo_agent/channels/` plugins) | — |
| `app/gateway/auth/*` (jwt, oidc, session cookies, csrf, password, user provisioning), `app/gateway/*` FastAPI app, `langgraph_studio.py` | Multi-user service: authn/z, tenants, CSRF, LangGraph Studio | — | **SKIP** — conflicts with local-first; would add a second authority | — |
| `frontend/*`, `web/`, TUI, themes, `title_middleware` | UI | — | **SKIP**; Evo keeps Tauri + `evo serve` | — |
| `config/{token_budget,tool_output,loop_detection,read_before_write,safety_finish_reason}_config.py` | Sensible operational knobs | G12 | **PORT** | `evo_agent/config.py` |
| `agents/middlewares/loop_detection_middleware.py` (Layer 1 + **Layer 2 windowed per-tool-type frequency**, warned-tools dedup set per thread) | Repetition detection distinct from step budgeting | G1 | **PORT** | `evo_agent/pipeline/stages/loop_guard.py` |
| `packages/extension-api/deerflow_extension_api/placement.py` (`MODEL_LOGICAL`/`MODEL_PHYSICAL`/`TOOL_VISIBLE`) | **Semantic placement**: declare a guarantee ("observe the raw tool return"), not a stack index — so "the host is free to restructure its stack" | G2 | **PORT — prerequisite for `pipeline_stage` metamorphosis** | `evo_agent/pipeline/engine.py` |
| `authz/adapter.py` + `authz/principal.py` | Layer 1 (tool assembly) and Layer 2 (tool-call execution) **share one identity builder**, so exposure and authorization cannot diverge | safety | **PORT as rule + test** | `evo_agent/security.py`, `tests/test_capability_exposure_matches_execution_decision.py` |
| `workspace_changes/{api,diff,recorder,scanner,types}.py` | Workspace snapshot/diff with `created/modified/deleted/symlink_created/additions` + `WorkspaceChangeLimits` | **overlap** — Evo's `world.py` already owns this (rule 15) | **SKIP except 3 deltas**: symlink taxonomy, limits object, "changed **output** paths" view | `evo_agent/world.py` (extend) |

### 1.2 The three ideas from DeerFlow worth more than any single module

1. **Skills are the unit of capability acquisition, and they are *gated by default*.** `SkillEvolutionConfig.enabled = False` with `security_fail_closed = True` — i.e. self-extension is an explicitly opted-in mode, and if the moderation model is unavailable the system **blocks** rather than allows. This is the correct answer to Evo's G2 and it aligns exactly with the user's "do not allow unrestricted self-modification".
2. **A middleware pipeline, not a monolithic loop.** ~45 small, named, ordered, individually-tested stages give DeerFlow its flexibility. Evo's kernel currently has these concerns welded together inside `kernel.run()`; extracting a pipeline is what makes Evo's loop *replaceable by candidate versions* (see J below).
3. **Additive contracts + narrow projections.** Defaults on every protocol method and field, and `HostPolicySnapshot` instead of `AppConfig`, are what let a host evolve without breaking plugins. Evo needs precisely this to make components metamorphosable subjects.

---

## 2. DeepSeek Harness → Evo (Map D)

DSH is `"Everything is a Plugin"` (MIT, TS, ~58 package roots, 8,953 blobs): 60 `packages/{acp,api,attachment,boot,bundle,client,code-runtime,compaction,context,core,credentials,e2b,examples,experimental,extensions,feedback,fs,goal,guard,hooks,host,identity,interaction,jobs,llm,lsp,mcp,plan,preset,runtime-diagnostics,sandbox,schedule,sdk,session,session-query,settings,shell,skill,spill,storage,subagent,subprocess,terminal,test-support,todo,typert,util,web,webhook,workflow,workspace}`, plus `apps/{cli,web}`.

> **[grounded]** Read from source. The single biggest correction is §2.2-1: DSH invariants are *runtime* mechanisms that abort, not test conventions — which upgrades how Evo must implement its own protected core (`05-GROUNDING-CORRECTIONS.md` §2.1).

As a *codebase* it is unusable to Evo (different language, different product shape). As an *architecture* it is the most relevant reference in this space, and two of its conventions are directly adoptable.

### 2.1 Capability → Evo component mapping

| DSH package | Capability it supplies | Evo gap | Mode | Lands in |
|---|---|---|---|---|
| `packages/host/plugin-inventory/{index,invariant,types}.ts` | Plugin inventory as a **read-only snapshot** exposed to trusted clients: `PluginInventoryEntry{entryId (branded type), moduleName, enabled (effective, incl. disabled ancestor groups), fiberPhase: pending / loading / active / failed / unloading / null}` | G2, G3 | **PORT** the shape exactly | `evo_agent/plugins/inventory.py` |
| **247 `invariant.ts` files** + `packages/runtime-diagnostics/invariants/` service | **Invariants are runtime-enforced companion plugins, and violations throw** (`InvariantFailure = (message) => never`, `InvariantError{code:'INVARIANT', packageName}`). Installed with `ctx.on('llm/stream', …)` and **prepended** so a short-circuiting replay listener cannot silence them; selectable via `Config{enabled, package_allowlist, package_blocklist}`; "ordinary package entrypoints stay independent of diagnostics". A layer may declare **"No runtime invariant" with a stated reason** | G2, K | **PORT the mechanism** (installed checks), not just the test convention | `evo_agent/invariants/`, invoked from `pipeline/engine.py` |
| `packages/sdk/*` + `packages/bundle/{base,headless,sdk-app,sdk-minimal,web-app,acp-app}` | **Composable bundles**: an app is a selection of packages; `sdk-minimal` vs `headless` vs `web-app` | G9 | **PORT** the idea (Evo "profiles": minimal / local / research / full) | `evo_agent/profiles/` |
| `packages/extensions/cordis-{host,client}-runner/{sandbox,guard,lifecycle,registry,inspect-registry}.ts` | **Sandboxed plugin host runner** with a guard, lifecycle, and inspection registry — plugin code executes in a controlled runtime, not in-process trust | G2, G11 | **PORT** (policy layer); **SKIP** Cordis runtime itself | `evo_agent/plugins/isolation.py` |
| `packages/guard/{repeat-tool-reminder,timeout-policy}` (+ invariants) | Runtime **loop guards**: repeat-tool detection, timeout policy | G1 | **PORT** as pipeline stages | `evo_agent/pipeline/stages/` |
| `packages/hooks/hook-protocol/{events,codec,matcher,detached}` | **User-extensible hooks** with event codec and pattern matching, incl. detached execution | G2 | **PORT** (minimal: pre/post tool, pre/post exec, on-verify) | `evo_agent/hooks/` |
| `packages/plan/plan-mode` | **Plan mode**: read-only exploration phase with an explicit approval transition into execution | G1, safety | **PORT** — maps perfectly onto Evo's approval gate | `evo_agent/modes.py` |
| `packages/goal/{goal,command-goal,goal-round-driver}` | Goal loop with round driver (iterate until satisfied, bounded) | G1 | **PORT** as the driver around Evo's existing `strategic_autonomy` | `evo_agent/runtime.py` |
| `packages/compaction/{compaction-basic/{config,region},command-compact}` | **Context compaction** with region selection | G12 | **PORT** | `evo_agent/context.py` |
| `packages/todo/tool-todo`, `packages/feedback/{message-feedback,command-feedback}` | User-visible task ledger; **per-message feedback as a learning signal** | G8, G13 | **PORT** | `evo_agent/feedback.py`, `evo_agent/tools.py` |
| `packages/sandbox/{sandbox-local,sandbox-windows-acl}` | Per-OS sandbox backends incl. **Windows ACL-based** isolation | G11 | **PORT** Windows ACL provider — directly valuable: this repo already ships a Windows desktop + qualification pipeline | `evo_agent/sandbox_providers/` |
| `packages/credentials/{credentials,credentials-local,authorization}`, `packages/identity/anonymous-user-id` | Local credential store separated from config; authorization types | G5 | **PORT** (narrow) | `evo_agent/credentials.py` |
| `packages/subagent/{subagent,subagent-in-process-driver,subagent-acp,subagent-claude-code,tool-subagent}`, `packages/experimental/agent-team` | **Multiple subagent drivers**, including delegating to *external* agents (`subagent-claude-code` shells out to another CLI) | G6 | **PORT** driver abstraction; **PROCESS** for external agents | `evo_agent/delegation/` |
| `packages/acp/acp` | **Agent Client Protocol** — editor/IDE clients can drive the agent | G9 | **BRIDGE** later (optional) | — |
| `packages/workflow/{tool-workflow,tool-ralph,workflow-worker-thread}` | Declarative multi-step workflow tool; worker threads | G1 | **PORT** (workflow-as-skill only) | `evo_agent/skills/workflow.py` |
| `packages/session/session-persistence-sqlite` (82 files), `session-query/{session-query,session-log-export}` | Durable session store + a **query/export layer** over it | — | **SKIP** — Evo owns persistence (SQLite store, 120 tables); duplicating it would violate rule 15 | — |
| `packages/mcp`, `packages/lsp/*`, `packages/fs/tool-fs`, `packages/shell`, `packages/code-runtime`, `packages/web/tool-web`, `packages/attachment`, `packages/spill`, `packages/workspace`, `packages/schedule`, `packages/jobs`, `packages/storage`, `packages/settings`, `packages/context/agent-instructions` | MCP wiring, LSP, fs/shell/code tools, attachment handling, spill-to-file for large outputs, workspace scoping, scheduling, settings, **`AGENTS.md`-style instruction files with digest+render** | G4, G5 | **PORT selectively**: spill, agent-instructions, settings; MCP already covered via DeerFlow (do not build twice) | `evo_agent/context.py`, `evo_agent/config.py` |
| `packages/client/ui-*` (~50), `typert/*`, `packages/preset/agent-presets`, `packages/e2b`, `packages/terminal`, `packages/terminal`, themes/skins, `apps/web` | TUI/Web UI, rendering, presets | — | **SKIP** (Evo has Tauri + will have `evo serve`) | — |
| `BENCHMARK.md`, `SAFETY.md`, `AGENTS.md` at root | Repo-level benchmark doc, explicit non-security-boundary stance, agent contributor contract | G8 | **PORT the practice** | `docs/` |
| `packages/preset/agent-presets` + `tests/fixtures/plugins/{contribute,global-service,late-service,needs-missing,self-dispose,throws}.js` | Preset/agent-definition system, and a **test corpus of deliberately misbehaving plugins** (missing dependency, late service, self-dispose, throws) | G2 | **PORT both** — copy this adversarial fixture idea into Evo's plugin tests | `tests/fixtures/plugins/` |

### 2.2 The two ideas from DSH worth more than any package

1. **`everything is a plugin` + `invariant.ts` everywhere (247 of them).** DSH's extensibility is not a feature; it is the shape of the system — and each extension point carries a *runtime-enforced* invariant companion whose violation throws, bound to the owning package name, ordered so it cannot be silenced by an earlier listener, and independently selectable by allow/block list. One `agent-loop` companion even asserts request-reconstruction: the outbound `messages` must equal `session.deriveMessages()` exactly, and the session log must contain the `step/start` and `request/header` events backing it — "the prompt you sent is provably the prompt the log says you sent", enforced, not documented. Evo's protected core today is a `frozenset` of strings in `metamorphosis.py:236` plus seven hard-coded paths in `scripts/run_production_gate.py:12-20`. Replacing name-string-matching with installed checks is what lets metamorphosis *reason about* boundaries — and what stops a *promoted candidate* from violating one at runtime, which no pytest file can do.
2. **Adversarial plugin fixtures.** DSH tests its plugin host by loading plugins that throw, arrive late, self-dispose, and depend on missing services. Evo's evolution pipeline has no equivalent: nothing proves that a *broken or hostile candidate* is rejected. See Phase 5.8 of `04-GOVERNANCE-AND-PLAN.md` for the fixture set Evo must have.

### 2.3 What `PROCESS` mode means concretely (the only way DSH runs inside Evo)

`dsh` may be used as an **external delegated executor**, exactly as DSH itself delegates to `subagent-claude-code`:

```
Evo Verifier/Policy → DelegationEngine → DshBackend (PROCESS)
   ├─ builds a bounded task spec + allowed-tool list
   ├─ launches `dsh` inside EvoSandboxProvider (net per policy, cwd = task workdir)
   ├─ captures stdout/session-log as *untrusted data*
   └─ returns SpecialistOutput → Evo Verifier → Experience → (maybe) Evolution
```
Rules: `dsh` output is never a `ToolResult.success` by itself; `dsh` never sees Evo's credentials, governance DB handle, or promotion authority; the delegation consumes a `SpecialistTaskContract` so scope/limits/prohibited-actions are enforced by Evo before launch. Available only when the operator explicitly configures `backends.dsh.enabled = true` and a binary path.

---

## 3. Where both upstreams say the same thing (converge once)

| Concern | DeerFlow | DSH | Single Evo component |
|---|---|---|---|
| Loop guards | `loop_detection_middleware`, `safety_termination_detectors`, `tool_error_handling` | `guard/repeat-tool-reminder`, `guard/timeout-policy` | `evo_agent/pipeline/stages/guards.py` |
| Context budget | `token_budget_middleware`, `tool_output_budget_middleware`, `summarization_middleware`, `tool_output_synopsis` | `compaction/compaction-basic`, `spill` | `evo_agent/context.py` |
| Skills | `skills/*` + `skill_evolution_config` | `skill/{skill,skill-filesystem,skill-badge}` | `evo_agent/skills/` |
| Plugin/extension contract | `extension-api/contracts.py` (additive defaults) + `extensions/*` | `plugin-inventory`, `sdk`, `bundle/*` | `evo_agent/contracts/` + `evo_agent/plugins/` |
| Sandbox as a provider | `sandbox_provider.py`, `community/{e2b,boxlite,…}` | `sandbox/{sandbox-local,sandbox-windows-acl}` | `evo_agent/sandbox_providers/` |
| Subagent delegation | `subagents/`, `subagent_limit_middleware`, `delegation_ledger` | `subagent/*` | `evo_agent/delegation/` (over existing `specialist.py`) |
| Tool receipt / verification | `tool_receipt`, `verification_config` (receipts + judge) | `session-query` + code-mode result completeness | `evo_agent/verification/` |
| Hooks | `middlewares/*` (implicit) | `hooks/hook-protocol` (explicit) | `evo_agent/hooks/` |

**Do not implement any of these twice.** Each appears once in the target tree (see F in `02-TARGET-ARCHITECTURE.md`) and each has exactly one owner module and one authority.
