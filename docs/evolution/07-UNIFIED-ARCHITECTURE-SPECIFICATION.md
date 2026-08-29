# Unified Evo Architecture — Complete Integration Specification

**Status:** specification only. No source file in `evo_agent/`, `tests/`, `scripts/`, `desktop/`, or `web/` is modified by this document set.
**Normative language:** MUST = blocking acceptance criterion; SHOULD = deviation must be recorded with a reason; MAY = optional.
Companions: `00` audit · `01` integration maps · `02` target architecture · `03` migration/tests/metamorphosis · `04` governance/plan · `05` grounding corrections · `06` comparative analysis.

---

## 1. Purpose and governing rules

Produce **one** Evo Agent: local-first, permissioned, self-improving, whose sovereign core is unchanged in trust terms, with DeerFlow- and DeepSeek-Harness-derived capabilities absorbed as internal, governed, promotable components.

**R1 — Sovereignty.** `SecurityPolicy`, approval authority, verification authority, audit log, rollback authority, emergency shutdown, and evolution rules are the sole authorities. No integrated component may read, weaken, bypass, or substitute for them.
**R2 — One loop.** Exactly one Evo agent loop (`AgentRuntime.run_cycle` → `CognitiveOrchestrator.run_goal` → turn engine). Backends implement `run_turn`; they MUST NOT expose `run_task`, `stream_goal`, or any loop-shaped entry point.
**R3 — One store.** `SQLiteStore` is the only persistence authority; bridges touch it only through Evo APIs. No second DB, no LangGraph checkpointers, no DSH session store.
**R4 — No vendoring.** Neither repository's code is imported or copied. Integration is (a) pattern ports into dependency-free Evo modules, (b) optional extras behind Evo-owned Protocols, (c) subprocess delegation into Evo's sandbox.
**R5 — Derive, don't duplicate.** Anything reconstructible from the append-only `events` stream MUST be derived at assembly time, not stored (receipts, turn context, prompt reconstruction).
**R6 — Clamped, not validated.** Every governance-relevant bound is clamped to a floor/ceiling in `__post_init__`, so no profile, env var, candidate, or plugin can widen it.
**R7 — Fail closed.** Absent moderation model, unparseable verdict, unavailable sandbox, unreadable manifest, stale digest → refuse. No degraded progress without an explicit operator override that emits a permanent event.
**R8 — Additive contracts.** Every port method and optional field carries a default, so adding a member never orphans an installed skill or plugin.
**R9 — Inert by policy.** Every new subsystem is reachable but inert under `safe_mode` / `kill_switch`; asserted, not reviewed.
**R10 — Promotable ⇒ benchmarkable.** If a capability is a metamorphosis target (§4) it MUST have a benchmark suite that can detect its regression; otherwise it is not eligible.

---

## 2. Final unified architecture

```
USER ── evo CLI ─┐
DESKTOP ─ bridge ─┼─► EVO AGENT API (JSON-RPC, loopback + token)
WEB ── rpc client ┘
        │
        ▼
┌────────────────────────────────────────────────────────────────────────────────────┐
│ SOVEREIGN CORE   evo_agent/sovereign/*    PROTECTED.manifest, hash-locked          │
│  governance · approval_authority · verification_authority · audit ·                │
│  rollback_authority · shutdown · invariant_registry · policy_projection            │
│  OWNS EXCLUSIVELY: the `satisfied` verdict · approval mint · rollback path ·       │
│                    kill switch · protected set · clamp ceilings                    │
└──────▲──────────────────────────────────────────────▲──────────────────────────────┘
       │ durable state, hashes, tombstones             │ verdicts/refusals (one-way)
┌──────┴──────────────────────────────────────────────┴──────────────────────────────┐
│ EVO AGENT LOOP  (the only loop)                                                    │
│  AgentRuntime.run_cycle → CognitiveOrchestrator.run_goal → TurnEngine              │
│  Goal → Intent → StrategyArtifact → [pipeline per turn] → Observe → Verify         │
│  MAINTENANCE phase: compaction · gap analysis · skill scan · learning cycle        │
│  Pipeline stages, ordered by DECLARED PLACEMENT + REASON:                          │
│   input_sanitize → token_budget → loop_guard → repeat_guard →                      │
│   deferred_tool_filter → read_before_write → policy_filter → DISPATCH →            │
│   tool_result_sanitize → output_budget → RECEIPTS (outermost, tool edge) →         │
│   error_handling → compaction → inbox(step-boundary)                               │
└──────▲──────────────────────────────────────────────▲──────────────────────────────┘
       │ receipts, usage, artifacts                   │ capability requests
┌──────┴──────────────────────────────────────────────┴──────────────────────────────┐
│ RUNTIME ADAPTERS  evo_agent/backends/*            (pluggable; never authoritative) │
│   native      default · sync · offline-deterministic                               │
│   lead_agent  [BRIDGE] optional extra, isolated venv, sync façade over async       │
│   dsh         [PROCESS] subprocess inside SandboxProvider; output = untrusted data │
│   code_exec   [PORT] agent-authored code runs only in sandbox, never host          │
└──────▲──────────────────────────────────────────────▲──────────────────────────────┘
┌──────┴──────────────────────────────────────────────┴──────────────────────────────┐
│ CAPABILITY SUBSTRATE (Evo-owned; extensible by skills/plugins)                     │
│ ToolCatalog(canonical + aliases) · skills(SKILL.md; candidates→installed) ·        │
│ mcp(namespaced, allowlisted) · research(stdlib fetch + providers) · context ·      │
│ delegation(contracts + executors) · memory(scoped; backends) ·                     │
│ verification(receipts + checklist + strict evaluator) · artifacts · feedback ·     │
│ invariants(live checks) · plugins(inventory + lifecycle) · modes(plan|execute)     │
└──────▲──────────────────────────────────────────────▲──────────────────────────────┘
┌──────┴──────────────────────────────────────────────┴──────────────────────────────┐
│ ISOLATION  evo_agent/sandbox_providers/*                                           │
│   local_bwrap (default; existing logic) · unshare · landlock(external binary) ·    │
│   windows_acl · docker · e2b      ALL tool execution routes here — not only        │
│   evolution candidates.  probe → prefer → degrade only via operator override       │
└──────▲──────────────────────────────────────────────▲──────────────────────────────┘
┌──────┴─────────────────────────────────────────────────────────────────────────────┐
│ PERSISTENCE  SQLiteStore — 120 tables → 127 (§5); append-only `events` is truth    │
└──────▲─────────────────────────────────────────────────────────────────────────────┘
┌──────┴─────────────────────────────────────────────────────────────────────────────┐
│ EVOLUTION SPINE (outside the loop, wrapping the stack)                             │
│ Evidence → Propose → VALIDATE → MATERIALIZE → ISOLATE → BENCHMARK → VERIFY →       │
│   BETTER|WORSE|INCONCLUSIVE → PROMOTE → OBSERVE → (auto-)ROLLBACK → QUARANTINE     │
└────────────────────────────────────────────────────────────────────────────────────┘
```

`active_version.py` resolves the allow-listed overlay (`capabilities/**`, `mcp/servers.json`, provider config, stage selection) from `versions/active/` at cycle start. **This one indirection converts the spine from bookkeeping into causation** (`00-AUDIT.md` §B.3).

### 2.1 Responsibility matrix — one owner per concern

| Concern | Owner | Consumers | Forbidden |
|---|---|---|---|
| Permission / approval | `sovereign/approval_authority` | `policy_filter`, runtime gate, MCP, skills | any other mint |
| Verdict ("done") | `sovereign/verification_authority` | loop, benchmark, promotion | backends — `TurnResult` has no `success` |
| Capability availability | `ToolCatalog` + active overlay | discovery, plan prompt, sandbox mount set | descriptor without handler |
| Isolation | `sandbox_providers` | all tool exec, candidates, delegation | host exec without override |
| Memory read/write | `memory.MemoryManager` (+`scope_key`) | planning context, retrieval, consolidation | `store.recent_memories()` in hot path |
| Persistence | `storage.SQLiteStore` | everything | bridge-owned DB |
| Evolution rules | `sovereign/governance` | evolver, metamorphosis, promotion | self-approval |
| Audit | `sovereign/audit` | all layers | any UPDATE/DELETE path on `events` |
| Extension inventory | `plugins/inventory` | CLI, RPC, status | plugins claiming authority |
| Pipeline order | `pipeline/engine` + declared placement | loop, verifiers, guards | index-based ordering without a reason |

---

## 3. Consolidated disposition register

Modes: **REUSE** Evo as-is · **EXTEND** Evo + seam · **PORT** re-implement pattern · **BRIDGE** optional extra · **PROCESS** sandboxed subprocess · **SKILL**/**PLUGIN** capability package · **EXTERNAL** stays outside · **REJECT** not integrated.

| Capability | Mode | Target location | Authority | Ref |
|---|---|---|---|---|
| Sovereignty, approvals, audit, rollback, shutdown | REUSE + EXTRACT | `sovereign/` | Evo | 00 §B.4 |
| Persistence, events, experience, evaluation | REUSE | `storage.py` et al. | Evo | 00 §B.11 |
| Architecture manifest, compatibility, impact analysis | REUSE | `metamorphosis.py` → `sovereign/governance` | Evo | 00 §B.6 |
| Candidate isolation (bwrap/unshare + probe) | REUSE as default provider | `sandbox_providers/local_bwrap.py` | Evo | 00 §B.7 |
| Benchmark + promotion machinery (corpus replaced) | REUSE | `benchmark.py`, `promotion.py` | Evo | 00 §B.8 |
| Turn pipeline / middlewares | PORT (14-stage subset) | `pipeline/` | Evo | 06 §2.1 |
| Placement + documented ordering rationale | PORT | `pipeline/engine.py` | Evo | 06 §14 L10 |
| Loop / repeat / timeout guards | PORT (DF + DSH converged) | `pipeline/stages/` | Evo | 06 §3.5 |
| Context compaction, spill, token meter | PORT (DF + DSH converged) | `context.py` | Evo | 06 §12.3 |
| Input + tool-result sanitization | PORT | `pipeline/stages/sanitization.py` | Evo | 06 §11 |
| Receipts | PORT improved (append-id bound) | `verification/receipts.py` | Evo | 05 §1.1 |
| Strict completion evaluator | PORT | `sovereign/verification_authority.py` | Evo | 05 §1.1 |
| Runtime invariants that abort | PORT | `invariants/`, installed by engine | Evo | 06 §3.1 |
| Skills: format, catalog, activation, progressive disclosure | PORT | `skills/` | Evo | 06 §2.2 |
| Skill installer hardening + fail-closed scan | PORT | `skills/{installer,security}.py` | Evo | 05 §1.2 |
| Enabled-only mount projection | PORT | `skills/projection.py` → providers | Evo | 06 §13.5 |
| `secrets-autonomous` credential autonomy | PORT + **new in Evo** | `skills/`, `security.py` | Evo | 06 §2.2 |
| Memory backends (deermem patterns; mem0/honcho optional) | PORT patterns / BRIDGE rest | `memory_backends/` | Evo | 06 §2.5 |
| Memory scope isolation | EXTEND (+`scope_key`) | `memory.py` | Evo | 06 §12.5 |
| Sandbox provider abstraction + docker/e2b/windows_acl | PORT | `sandbox_providers/` | Evo | 06 §2.3 |
| Landlock launcher | **EXTERNAL** binary, probed | `sandbox_providers/landlock.py` | provider | 06 §3.3 |
| MCP (policy first, transport via extra) | PORT + BRIDGE | `mcp/` | Evo | 06 §2.6 |
| Research / fetch / crawl / browse | PORT stdlib; BRIDGE providers | `research/` | Evo | 06 §2.4 |
| Sub-agent capacity, clamps, spend metering | PORT | `delegation/limits.py` | Evo | 06 §2.7 |
| Delegation executors (llm / sandbox / dsh / ACP) | PORT + PROCESS | `delegation/executors/` | Evo | 06 §2.4 |
| Two-layer authz, one principal builder | PORT as rule + test | `security.py` | Evo | 06 §2.8 |
| Guardrails with provider slots | PORT | `pipeline/stages/`, `guardrails.py` | Evo | 06 §2 |
| Artifacts, uploads, feedback | PORT (minimal) | `artifacts.py`, `feedback.py` | Evo | 06 §2.11 |
| Hooks (user-extensible events) | PORT minimal | `hooks/` | Evo | 06 §3.7 |
| Plugin inventory + lifecycle machine | PORT | `plugins/inventory.py` | Evo | 06 §3.8 |
| Adversarial plugin fixtures | PORT | `tests/fixtures/plugins/` | Evo | 05 §2.2 |
| Plan mode (read-only phase) | PORT | `modes.py` | Evo | 06 §3.6 |
| Step-boundary inbox | PORT, deferred until receipts | `pipeline/stages/inbox.py` | Evo | 06 §14 L6 |
| Parallel tool calls | EXTEND, default off | `pipeline/config` | Evo | 06 §11.5 |
| DeerFlow lead-agent graph | BRIDGE, opt-in, isolated venv | `backends/lead_agent.py` | Evo | 06 §10 D1/D3 |
| `dsh` CLI | PROCESS, opt-in | `backends/dsh.py` | Evo | 06 §8.2 |
| `evo serve` JSON-RPC | NEW | `serve/` | Evo | 06 §11.8 |
| Model routing / providers | REUSE | `model_intelligence.py` | Evo | 00 §B.11 |
| LangGraph runtime, checkpointers, Studio | **REJECT** | — | — | 06 §9 |
| FastAPI gateway, JWT/OIDC/CSRF, RBAC, multi-user | **REJECT** | — | — | 06 §9 |
| IM channels (Slack/Telegram/Feishu/DingTalk/WeChat/buzz) | **REJECT** (adapter at most, future) | — | — | 06 §9 |
| Postgres / Redis | **REJECT** | — | — | 06 §9 |
| DF `persistence/`, `workspace_changes/` | **REJECT** — duplicates | — | — | 06 §4 |
| DF `models/patched_*.py`, title, suggestions, input-polish, assistants-compat | **REJECT** | — | — | 06 §9 |
| DSH `client/ui-*`, `typert`, themes/skins, Cordis runtime | **REJECT** | — | — | 06 §9 |
| DSH `session-persistence-sqlite`, `session-query` | **REJECT** — duplicate store | — | — | 06 §4 |
| LLM judge as a **verdict source** | **REJECT in v2** (advisory MAY later) | — | — | 05 §1.1 |
| Vendoring either repository | **REJECT** | — | — | 06 §9 |

---

## 4. Evolutionary Metamorphosis eligibility

Eligibility must be **explicit in both directions**: a capability is either a target with a detectable benchmark signal, or it is out with a stated reason. Targets are the four materialization kinds of `02` §E.3 (`skill`, `tool_binding`, `provider_config`, `pipeline_stage`) extended with `strategy_params` and `memory_policy`.

| Capability | Eligible | Target kind | Payload = what actually changes | Benchmark signal (R10) | Reason when not |
|---|---|---|---|---|---|
| Skills: install / enable / disable / version | **Yes** | `skill` | overlay set `capabilities/skills/installed/<name>/` | `skill-acquisition`, `core-local` | — |
| Tool ↔ capability ↔ permission bindings | **Yes** | `tool_binding` | catalog rows: risk floor, permissions, aliases, fallback order | `tool-selection`, `core-local` | — |
| `ToolCallKind` side-effect map | **Yes** (upward only) | `tool_binding` | per-tool kind annotation; may raise approval needs | `recovery`, `core-local` | Lowering a floor is protected |
| MCP server registration + caps | **Yes** | `provider_config` | `mcp/servers.json`, allowlist, size caps | `mcp-behaviour` | — |
| Research provider set + model routing | **Yes** | `provider_config` | providers + per-role routing | `research`, `cost-latency` | — |
| Sandbox provider selection | **Yes**, upward only | `provider_config` | provider name + caps from allow-list | `isolation-attestation` | A downgrade below `local_bwrap` is not a valid candidate (R7) |
| Backend selection (native / lead_agent / dsh) | **Yes** | `provider_config` | per-task-class preference + availability gate | `core-local`, `cost-latency`, `isolation-attestation` | Enabling an unavailable extra is inert, not an error |
| Pipeline stage set + parameters | **Yes** | `pipeline_stage` | stage name + params from a **reviewed allow-list**; order from declared placement | `regression`, `guard-effectiveness` | Stage **source** never changes — only selection/params |
| Strategy / planner parameters | **Yes** | `strategy_params` | budgets, retry/recovery, thresholds, parallelism (clamped) | `recovery`, `cost-latency` | — |
| Context compaction / summarization policy | **Yes** | `memory_policy` | regions, budgets, spill thresholds | `long-horizon` (incl. "what was discarded" metric) | — |
| Memory extraction / retention / retrieval policy | **Yes** | `memory_policy` | confidence thresholds, decay, staleness cadence, retrieval weights | `memory-recall` | — |
| Delegation fan-out / depth / budget values | **Yes**, clamped values | `strategy_params` | bounds that respect clamps | `delegation` | Widening past a ceiling is not representable |
| Verification **checklist templates** | **Yes** | `pipeline_stage` / `provider_config` | expected-output schema, receipt requirements | `verification-quality` | — |
| Verification authority & verdict semantics | **No — protected** | — | — | — | K.1; plugins tighten, never loosen |
| Approval authority / policy / trust boundary | **No — protected** | — | — | — | K.1 |
| Audit integrity & event schema | **No — protected** | — | — | — | K.1 |
| Rollback authority & mechanism | **No — protected** | — | — | — | K.1 |
| Emergency shutdown / safe mode | **No — protected** | — | — | — | K.1 |
| Evolution rules, protected set, clamp ceilings | **No — protected** | — | — | — | K.1 |
| Agent-loop **control flow** | **No — protected** | — | — | — | R2 / 06 §14 L9; loop composition is human-release-only |
| Evo's own **source code** | **No — structurally impossible** | — | — | — | No target kind accepts source. Replaces "reject the string `generated_code`" with "the capability does not exist" |
| Memory **contents** | **No** | — | — | — | Contents are evidence, not capability; in-place mutation corrupts the audit basis (06 §12.6) |
| Skill / plugin **executable code** packages | **Deferred, gated** | (future `plugin`) | allow-listed entry point into `plugins/`, never `sovereign/` | would require `plugin-isolation` suite | v2 keeps the no-source rule; scan alone ≠ governance (05 §1.2) |

**Eligibility decision rule (normative).** For each integrated capability, exactly one of these MUST hold, and the choice MUST be recorded in the capability's own document:
1. **Promotable** — it is a target kind in the table above with a payload, a benchmark signal, and a rollback rehearsal (E5).
2. **Parameter-only promotable** — its *configuration* is a target while its *mechanism* is protected (e.g. stage selection vs stage source; memory policy vs memory contents).
3. **External** — it stays outside the promotion surface because the provider owns the risk (e.g. `landlock-run`, Docker, E2B, model gateways); its Evo-side **selection and caps** are still promotable within the allow-list.
4. **Protected / ineligible** — sovereignty, audit, verdicts, approval, rollback, shutdown, evolution rules, loop control flow, source code; ineligible by construction, with the reason stated (never by omission).
Silence is not a valid classification: a capability with no row in the table is a specification defect.

**Eligibility invariants.**
**E1** payload touching a protected path → invalid **before** sandboxing. **E2** no detectable signal → not a target (R10). **E3** monotonicity: security-relevant fields move only upward (risk floors, permission sets, caps) — `test_monotonic_hardening.py`. **E4** `INCONCLUSIVE` means no change. **E5** rollback rehearsal in-sandbox before promotion is offered. **E6** one experiment in flight; cooldown in `sovereign/governance`. **E7** every eligible target is invocable through the single `evo undergo-metamorphosis` operation and observable through `evo metamorphosis explain`.

---

## 5. Data model (additive: 7 new tables → 127; +1 optional cache → 128)

| Object | Purpose | Notes |
|---|---|---|
| `skill_packages` | installed/candidate skills: version, digest, provenance, scan verdict, enabled | `status IN (candidate, installed, deprecated, quarantined)`; only `installed` enters the overlay |
| `skill_grants` | skill → canonical tool + permission intersection | materializes "narrow, never widen" |
| `mcp_servers`, `mcp_tools` | registrations, caps, namespaced tools | MCP names prefixed `mcp:<server>:`; cannot claim canonical names |
| `tool_catalog` | canonical name, aliases, `ToolCallKind`, risk floor, `parallel_safe`, mount requirement | single usability authority (06 §13.2) |
| `invariant_results` | live check outcomes + violation codes | feeds `evo status` and promotion evidence |
| `backends` | probe results, last error, effective backend per task class | read-only surface |
| `memory_records` **+ `scope_key`** | scoping column + index | back-fill `'local'`; retrieval filters by scope |
| `memories` | **deprecated** write-only mirror, one release | folded away by migration (06 §12.1) |
| `receipts` *(optional, 128th)* | materialized cache only | MUST be recomputable from `events` (R5); absent by default |

New `EventType`s appended to the existing 282 (values never reused, never renumbered): `PIPELINE_STAGE_EXECUTED`, `RECEIPT_ISSUED`, `INVARIANT_VIOLATION`, `SKILL_CANDIDATE_CREATED`, `SKILL_SCAN_BLOCKED`, `SKILL_PROMOTED`, `MCP_TOOL_REFUSED`, `TOOL_NAME_CONFLICT`, `SECURITY_DEGRADED`, `MEMORY_CANDIDATE_EXTRACTED`, `MEMORY_FORGOTTEN`, `MEMORY_RETRIEVED`, `RUNTIME_BACKEND_SELECTED`, `OVERLAY_RESOLVED`, `ACTIVE_CAPABILITIES_DIGEST`, `METAMORPHOSIS_REQUEST_CLASSIFIED`, `MAINTENANCE_CYCLE`.

Schema policy: additive-only `ALTER TABLE ADD COLUMN` guarded by `schema_version` + `ProductionSchemaManager`; `scripts/migrate_memory_consolidation.py` is idempotent and verified by a row-conservation assertion.

---

## 6. Interfaces (normative)

```python
# evo_agent/ports/execution_backend.py — the R2 boundary
class ExecutionBackend(Protocol):
    name: str
    def probe(self) -> BackendAvailability: ...                     # never raises; sync
    def plan_capability(self, req: CapabilityRequest) -> BackendPlan: ...
    def run_turn(self, ctx: TurnContext, sink: EventSink) -> TurnResult: ...  # SYNC (06 §11.1)
    def cancel(self, turn_id: str, reason: CancelReason) -> CancelAck: ...
    def export_receipts(self, turn_id: str) -> list[Receipt]: ...
# TurnResult(status, artifacts, receipts, usage, notes)  — NO `success` field (R1)

class TurnEngine(Protocol):                        # R2: the loop, pluggable
    def next_turn(self, ctx: TurnContext) -> TurnDecision: ...   # tool_calls | final | request_approval | abstain
    def compact(self, ctx: TurnContext, budget: int) -> TurnContext: ...

class SandboxProvider(Protocol):                   # one authority, many backends
    def probe(self) -> ProviderAvailability: ...                  # unusable → fall closed (R7)
    def mount_set(self, task: TaskHandle, overlay: ActiveOverlay) -> tuple[Mount, ...]: ...
    def run(self, req: ExecRequest, on_event: Callable[[ExecEvent], None]) -> ExecResult: ...
    def terminate(self, handle: ExecHandle, grace: float) -> TerminationAck: ...

class VerifierPlugin(Protocol):                     # tighten-only (E3)
    def expects(self, step: PlanStep) -> list[CheckSpec]: ...
    def assess(self, step: PlanStep, result: ToolResult,
               receipts: Sequence[Receipt]) -> AdvisoryVerdict: ...
    # AdvisoryVerdict cannot set `satisfied`; only verification_authority can.

class Materializer(Protocol):                       # metamorphosis targets
    target_kind: str
    def validate(self, payload: dict) -> list[str]: ...           # schema, size, monotonicity
    def write_candidate(self, payload: dict, dest: Path) -> OverlayFragment: ...
    def digest(self, fragment: OverlayFragment) -> str: ...

class InvariantCheck(Protocol):                     # DSH-style, live, aborting
    owner: str
    code: str
    reason_if_absent: str | None                                  # required if no check
    def __call__(self, obs: TurnObservation) -> None: ...        # raises InvariantError
```

Contract rules: all ports satisfy R8 (a decorator enforces defaults at import); bridges convert provider types at their own edge — no LangChain/LangGraph object crosses into `evo_agent` (`test_import_purity.py`); `probe()` is side-effect free and non-raising (R9); `SandboxProvider.run` is the **only** path to subprocess execution anywhere in the codebase (enforced by an AST scan in `test_no_direct_subprocess.py`).

---

## 7. Configuration — single source, clamped, validated

```toml
# evo.toml — unknown keys are an ERROR, not ignored; all bounds clamped (R6)
[agent]        loop = "native"                 # native | cognitive   (flip in P4)
               turn_budget = 24                # clamp [4,64]
               max_parallel_tool_calls = 1      # clamp [1,10]; requires receipts
[sandbox]      provider = "auto"               # auto|local_bwrap|unshare|landlock|windows_acl|docker|e2b
               allow_host_execution = false     # true ⇒ permanent SECURITY_DEGRADED event
[skills]       evolution_enabled = false        # candidate writes refused entirely when false
               scan_fail_closed = true          # unparseable scan verdict ⇒ block
[research]     allow_schemes = ["https"]        # file:// never permitted
               max_bytes = 2000000              # clamped
               timeout_seconds = 20             # clamped
[mcp]          servers = []                     # allowlist only
               max_tools_per_server = 40        # clamped
[delegation]   max_fanout = 3                    # clamped
               max_depth = 1                    # clamped; recursion refused
               budget_inherit = true
[verification] receipts_enabled = true           # cannot be disabled on the promotion path
               strict_evaluator = true
[evolution]    max_candidates_per_cycle = 3
               max_concurrent_experiments = 1
               cooldown_hours = 24
[backends]     deerflow = { enabled = false, venv = null }
               dsh       = { enabled = false, binary = "dsh" }
[profiles]     active = "local"                 # minimal | local | research | full
```

`config/governance.json` (protected) holds the protected path set, eligible target-kind allow-list, monotonic field list, and clamp ceilings. It is read-only to the agent: no tool, skill, plugin, or candidate may write it (`04` §K.2).

---

## 8. Migration (refines `03` §H; P-phases are the release unit)

| Phase | Adds | Key acceptance test | Est. |
|---|---|---|---|
| **P0 Baseline** | `PROTECTED.manifest`; `verify_sovereign_digest.py` (report-only); 4 xfail(strict) characterisation tests; gate PROTECTED list 7 → 12 files; `test_architecture_single_loop.py` | gate green; xfail ledger registered | 2d |
| **P1 Sovereign extraction** | `sovereign/*` facades (address-only move); startup digest check, fail-closed; `PROTECTED_CORE` relocated to `sovereign/governance` as the single definition | tamper ⇒ refuse start | 4d |
| **P2 Dead links closed** | kernel → `MemoryManager.retrieve(RetrievalQuery)`; real `_architecture_version()`; public `active_version()`; **memory-table consolidation migration** + `scope_key` | `test_memory_used_at_plan_time`; `test_architecture_version_propagates`; `test_memory_scope_isolation` | 3d |
| **P3 Materialization** | `active_version.py`; `ports/evolution_target.py`; 4+2 `Materializer`s; `SandboxEngine.run_experiment(candidate_overlay=…)`; `active_capabilities_digest` health check wired to `_rollback_after_failure` | **`test_metamorphosis_closed_loop.py`** — promote changes observed behaviour, rollback restores it | 6d |
| **P4 Loop, pipeline, isolation** | `ports/*` incl. `additive`; `TurnEngine`; `pipeline/` (14 stages, placement+reason, invariant installation); `context.py`; `ToolCatalog` + canonical aliases; `sandbox_providers/*` incl. landlock probe; **all tool execution via providers**; `shell=False`; Windows/colon/ADS path rules; clamped bounds everywhere | `test_no_async_leak`; `test_pipeline_ordering_rationale`; `test_tool_usability_requires_all_three`; `test_python_file_execution_confined`; `test_monotonic_hardening` | 8d |
| **P5 Capabilities** | `skills/` (catalog → hardening trio → projection/mount + `secrets-autonomous`); `hooks/`; `mcp/` (policy before transport); `research/`; `delegation/` (limits + first executor together); `verification/` (receipts + checklist + strict evaluator); `plugins/inventory` + adversarial fixtures; `modes.py` plan-mode; `backends/{lead_agent,dsh}` inert by default | `test_skill_install_fail_closed`; `test_mcp_refusals`; `test_delegation_depth`; `test_adversarial_plugins`; `test_degradation_matrix` | 10d |
| **P6 Evolution completion + benchmark v2** | benchmark v2 (7 suites incl. `hold-out` + `isolation-attestation`); `metamorphosis/operator.py` (`undergo`, `--dry-run`); `METAMORPHOSIS_REQUEST` intent (never self-approvable); invariant registry complete; `evo serve`; `evo <noun> <verb>` with legacy-parity test; desktop bridge 8 → ~14 commands; web RPC | `test_promotion_blocked_on_inconclusive`; `test_metamorphosis_stress_100`; `test_cli_parity`; `test_invariant_coverage_matrix` | 6d |
| **P7 Release** | `evo.toml` + `config/governance.json` finalized; `ARCHITECTURE.md` rewritten to describe what *is*; docs set (`GOVERNANCE`, `SKILLS`, `MCP`, `DELEGATION`, `METAMORPHOSIS`, `SECURITY`); CHANGELOG 2.0.0; version-sync gate; nightly soak | production gate + 3-night soak clean | 2d |

≈ **41 focused days.** Ordering is dependency-forced and non-negotiable: **P2 first** (otherwise every later measurement is attributed to the wrong architecture); P3 before P5 (a skill needs a target to be promotable); P4 isolation before P5 network and delegation; receipts before parallel calls and before step-boundary inbox; MCP policy before any transport; `ToolCatalog` before MCP registration (otherwise name conflicts cannot be arbitrated).

---

## 9. Verification model (final)

1. **Per-step deterministic checks** — `Verifier` extended to a `CheckSpec` set: `json_schema`, `regex`, `non_empty`, `file_exists`, `content_hash`, `line_count`, `exit_code`, `citation_resolved`. The silent default-open branch becomes an explicit failure ("expectation not recognized") behind a one-release shim.
2. **Receipts** — derived from `events` at assembly time (R5), bound to **append ids**, carrying tool canonical name, `ToolCallKind`, arg hash, `output_sha256` of the **raw** return, duration, refusal/sanitization markers.
3. **Strict completion evaluator** — DF `goal.py` shape: cheap non-thinking model, *"using ONLY the visible conversation evidence"*, `missing_evidence → not satisfied`, typed blockers (`none`, `needs_user_input`, `run_failed`, `external_wait`, `goal_not_met_yet`, `missing_evidence`). Invoked by the sovereign authority after turns, never inside a backend.
4. **Runtime invariants** — installed checks that raise (`INVARIANT_VIOLATION`); per-layer `NO_RUNTIME_INVARIANT = "<reason>"`; selectable by allow/block list; non-disableable when promotion depends on them.
5. **Advisory signals** — `citation_resolved`, `supported`, checklist coverage; may tighten, never loosen; the vocabulary firewall is enforced by lint, not convention.
6. **Verdict** — `PASS | FAIL | PARTIAL | BLOCKED | INCONCLUSIVE`, produced only by `verification_authority`, hash-chained into the audit.
7. **Benchmark gate** — promotion requires `better` on the target suite **and** no regression on `regression`/`hold-out` **and** unchanged `isolation-attestation` **and** monotonicity satisfied.

---

## 10. Security delta

| # | Fix | Phase | Mechanism |
|---|---|---|---|
| S1 | Runtime tool execution runs on host (00 §B.7) | P4 | every `ExecRequest` through `SandboxProvider`; `allow_host_execution = false` default |
| S2 | `python3 evil.py` bypass (verified in this tree) | P4 | interpreter-executed files must be in the task write-set or explicitly approved; `shell=False`; sandbox is the boundary, argv rules advisory |
| S3 | 5 governance-adjacent modules outside the release gate | P0 | digest list 7 → 12 files |
| S4 | Verifier default-open | P4 | `CheckSpec`; unrecognized ⇒ failure |
| S5 | Injection via newly-possible network input | P5 | sanitization on every external result; data-never-instructions; adversarial injected-page benchmark case |
| S6 | Credential autonomy unmodelled | P5 | `secrets-autonomous`; risk floor for credential use without an approving turn |
| S7 | Injection via retrieved memory | P2/P5 | retrieval after policy filter, scoped, `MEMORY_RETRIEVED` recorded; extraction candidates untrusted until verified |
| S8 | Config can widen a bound | P4 | R6 clamping across policy, limits, delegation, budgets |
| S9 | Self-approval risk | P1/P6 | `ApprovalAuthority.mint()` needs an operator-verified channel; `ask_user` is a tool, not a privilege |
| S10 | Windows path semantics (repo ships Windows desktop + qualification) | P4 | colon/UNC/ADS rejection; `windows_acl` provider; qualification-job tests |
| S11 | Overlay could shadow defaults silently | P3 | `OVERLAY_RESOLVED` + `ACTIVE_CAPABILITIES_DIGEST` events; mismatch ⇒ refuse to serve |

Reaffirmed non-goal: none of this makes Evo a security boundary for untrusted third-party workloads. Both upstreams explicitly disclaim that for themselves; Evo inherits their limits where it borrows their patterns, and must say so in `docs/SECURITY.md`.

---

## 11. Decisions required from you

| # | Question | My recommendation |
|---|---|---|
| Q1 | Raise `requires-python` to **3.12** (DeerFlow's floor; Evo is 3.11) so a `deerflow` extra is even installable? | **No in 2.0.** Ship the adapter seam only; keep 3.11 and the zero-dep guarantee; revisit if the bridge is actually wanted (06 §10 D1) |
| Q2 | Ship the DeerFlow `lead_agent` **bridge** in 2.0, or only the seam? | **Seam only.** Most value is in the ports; the bridge imports the dependency and async conflicts wholesale |
| Q3 | Keep `dsh` as a **PROCESS** backend at all, given its own "not a security control" notice? | **Yes but opt-in, disabled by default**, output strictly data, receipts mandatory — and documented as convenience, not capability escalation (`01-INTEGRATION-MAPS.md` §2.1) |
| Q4 | Sandboxing **all** tool execution (S1) costs latency and changes approval UX for read-only work. Accept? | **Yes**, with a read-only fast path + plan mode; the current asymmetry (candidates sandboxed, real tools not) is indefensible once network + delegation exist |
| Q5 | Retire `AgentKernel.run` / `--legacy-kernel` in 2.0 or one release later? | **One release later** with a `DeprecationWarning`; delete in 2.1 |
| Q6 | Add `scope_key` to memory now (migration cost) for a single-operator product? | **Yes** — delegation and candidate overlays make scoping load-bearing in P5; retrofit is worse |
| Q7 | Land `skill`/`plugin` **code** packages as a future target kind? | **Deferred to 2.1**, and only with a `plugin-isolation` benchmark suite; keeping "no source-code target" in 2.0 preserves the hard line you asked for |
| Q8 | Begin P0–P2 now? | P0–P2 are the two lowest-risk, highest-leverage phases (ratchet tests + repair the three dead links + memory consolidation) |

---

## 12. Definition of done — one unified Evo agent

1. `evo "…"` is the only user-facing entry point; DeerFlow and DSH appear **only** as rows in `evo backends status`, never as agents.
2. `grep -c "async def" evo_agent/*.py` stays **0** outside `serve/`; `dependencies = []` stays true; `pytest -q` stays deterministic and offline.
3. `test_metamorphosis_closed_loop.py` passes: a promoted candidate **changes observed behaviour**, and rollback restores it.
4. All 12 protected files are digest-verified at startup, after promotion, and across the production gate; `config/governance.json` is unwritable through every tool path.
5. Every layer in the invariant registry has a live aborting check or a stated reason (`test_invariant_coverage_matrix.py`).
6. No concern in `06` §4 has two owners: MCP, skills, compaction, guards, artifacts each exist exactly once in Evo.
7. Every §4-eligible target is reachable via `evo undergo-metamorphosis`, has a benchmark signal, and `INCONCLUSIVE` demonstrably blocks promotion.
8. **Zero lines of either upstream repository exist in this tree.** Both influence it only as ported patterns, one optional extra (deferred), and an opt-in subprocess backend.
