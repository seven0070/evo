# Target Architecture, Repository Structure, and Interfaces

---

## E. Target Evo architecture

### E.0 The governing rule

> **Evo is the Sovereign Core. DeerFlow and DeepSeek Harness are internal capabilities of Evo, never user-facing agents. There is exactly one Evo agent loop.**

Two corollaries that make the rule enforceable rather than aspirational:

1. **Single-loop test (CI-checkable).** Any `AgentLoop`/`run_cycle`/`run_goal`-shaped entry point must be registered in `evo_agent/sovereign/registry.py`'s `LOOP_ENTRYPOINTS`. `tests/test_architecture_single_loop.py` asserts the set equals `{CognitiveOrchestrator.run_goal}` ∪ `{AgentRuntime.run_cycle}` (the scheduler that *invokes* it) and that no other module defines an autonomous loop. A backend may implement `run_turn()`; it may not implement `run_task()`.
2. **Authority-inversion test.** Every port has a negative test proving the *downstream* component cannot escalate: a DeerFlow/DSH-backed result cannot self-certify success, cannot mint an approval, cannot write governance state, cannot promote.
3. **Exposure ≡ execution.** Whatever tools exist (assembly) and what each call may do (execution) must derive from one principal builder — ported from DeerFlow `authz/{adapter,principal}.py`. `tests/test_capability_exposure_matches_execution_decision.py` asserts `is_exposed(t) == may_execute(t)` for every (tool × backend) pair.
4. **`satisfied` is a sovereign token.** Only `sovereign/verification_authority.py` may set it (DeerFlow's vocabulary firewall, made mechanical): a lint test fails if the token appears in any other module, and advisory signals must use `supported` / `citation_resolved`.
5. **Bounds are clamped, not validated.** Adopted from `subagent_limit_middleware` + `subagents/config.py`: governance-relevant limits are clamped to floor/ceiling in `__post_init__`, so no configuration can widen them (extends `SecurityPolicy.max_command_seconds`, `RuntimeResourceLimits`, delegation fan-out, budget fractions).

### E.1 Layer diagram

```
┌─────────────────────────────────────────────────────────────────────────────┐
│  SOVEREIGN CORE  (immutable in trust terms; changes only via Metamorphosis)  │
│                                                                               │
│  Governance      SecurityPolicy   ApprovalAuthority   VerificationAuthority │
│  AuditLog        EvolutionRules   RollbackAuthority   EmergencyShutdown       │
│                  (evo_agent/sovereign/* — separate trust domain, hash-locked)│
└───────▲────────────────────────────────▲────────────────────────────────────┘
        │ evidence, receipts             │ policy decisions, verdicts (one-way down)
┌───────┴────────────────────────────────┴────────────────────────────────────┐
│  EVO AGENT LOOP  — the only loop                                              │
│  AgentRuntime.run_cycle → CognitiveOrchestrator.run_goal                      │
│     Goal → Intent → Plan → Turn Pipeline → Observe → Verify → Adapt → Report  │
│                                                                               │
│  Turn Pipeline (PORTED from DeerFlow middlewares + DSH guards)                │
│   budget → loop-guard → repeat-guard → read-before-write → policy → dispatch  │
└───────▲────────────────────────────────▲────────────────────────────────────┘
        │ typed results (never trust)     │ capability requests (never bypass)
┌───────┴────────────────────────────────┴────────────────────────────────────┐
│  RUNTIME ADAPTER LAYER  (pluggable; selected per task by CapabilityRouter)    │
│  ExecutionBackend protocol                                                    │
│   ├─ NativeBackend        (Evo's own turn loop; default; offline-deterministic)│
│   ├─ LeadAgentBackend     (DeerFlow graph via optional extra)   [BRIDGE]       │
│   ├─ DshBackend           (deepseek-harness subprocess)         [PROCESS]      │
│   └─ SandboxExecBackend   (Evo-run code in sandbox)             [PORT]         │
└───────▲────────────────────────────────▲────────────────────────────────────┘
┌───────┴────────────────────────────────┴────────────────────────────────────┐
│  CAPABILITY SUBSTRATE  (all Evo-owned, all governed identically)              │
│  Skills (SKILL.md)   ToolRegistry   MCP client   Research providers           │
│  Delegation/Specialists   Memory (backends)   Context/Compaction   Artifacts  │
└───────▲────────────────────────────────▲────────────────────────────────────┘
┌───────┴────────────────────────────────┴────────────────────────────────────┐
│  EXECUTION + ISOLATION   SandboxProvider protocol                             │
│   LocalBwrapProvider (existing) · UnshareProvider (existing) · DockerP · E2BP │
│   WindowsAclProvider (PORTED from DSH)                                         │
└───────▲────────────────────────────────▲────────────────────────────────────┘
        │ durable state, integrity hashes │
┌───────┴────────────────────────────────┴────────────────────────────────────┐
│  PERSISTENCE   SQLiteStore (120 tables, single authority — unchanged)          │
└──────────────────────────────────────────────────────────────────────────────┘
        │
┌───────┴──────────────────────────────────────────────────────────────────────┐
│  EVOLUTION SPINE (wraps the whole stack from outside)                         │
│  Evidence → Propose → Materialize → Isolate → Benchmark → Verify → Promote     │
│                                              ↘ Reject / Rollback               │
│  EvolutionOrchestrator · Evolver · MetamorphosisEngine · Sandbox · Benchmark   │
│  PromotionEngine · Rollback                                                    │
└────────────────────────────────────────────────────────────────────────────────┘
```

Data flow for one task, precisely:

```
1  AgentRuntime.run_cycle()               picks task, checks kill switch/safe mode/resources
2  CapabilityIntelligence.analyze_goal()  requirements → skills/tools/MCP candidates
3  CapabilityRouter.select_backend()      → ExecutionBackend name (policy-checked)
4  Backend.run_turn(ctx)                  pipeline-mediated turns; emits Receipts per tool call
5  Sovereign Verifier.judge(receipts, contract) → verdict  [backend cannot self-certify]
6  Adapt (bounded replan, now with real read-back of memory + architecture version)
7  Experience + Evaluation persisted with architecture_version  ← G2/G10 closed
8  On failure: EvolutionOrchestrator records opportunity
```

### E.2 What changes about "the Evo loop" (the G1 fix)

`ModelAdapter` (2 methods) is **retained but demoted** — it becomes one implementation of a new, richer `TurnEngine` port. Nothing existing breaks; the offline `RuleBasedAdapter` keeps `pytest -q` deterministic and keeps `dependencies = []` true.

```python
# evo_agent/ports/turn_engine.py  (new)
class TurnEngine(Protocol):
    def next_turn(self, ctx: TurnContext) -> TurnDecision: ...   # tool calls | text | done
    def compact(self, ctx: TurnContext, budget: int) -> TurnContext: ...
```
`TurnDecision` is a *data* type: `list[ToolCallRequest] | FinalAnswer | RequestApproval | Abstain`. It is never executed directly — it goes through `SecurityPolicy` → `ApprovalAuthority` → `SandboxProvider` → `Verifier`. That single ordering is the whole safety story.

### E.3 Executable self-extension (the G2/G3 fix — the load-bearing change)

Four **materialization targets**, each a real, loadable thing, each with a version directory, so that promotion actually changes behaviour:

| Target | What changes | How it is loaded | Risk ceiling |
|---|---|---|---|
| `skill` | SKILL.md package under `capabilities/skills/installed/` | `SkillCatalog` scan at turn start | Medium (instructions only) |
| `tool_binding` | Tool ↔ capability ↔ permission ↔ schema mapping | `ToolRegistry` from store rows | Medium |
| `provider_config` | MCP servers, research providers, model routing, thresholds | `evo_agent/config.py` profile overlay | Low |
| `pipeline_stage` | Which guard/budget/compaction stages run, and their params | `StageRegistry` resolution of an allow-listed stage name | **High** — names only, never source |

`pipeline_stage` deliberately changes *which pre-existing, reviewed stage* is enabled and with what parameters. **No target accepts new source code.** Arbitrary code never becomes a promotion target — that is the hard line the brief requires, and it is the line Evo's own `validate_proposal` was already trying to draw (it rejected the *string* `generated_code`; under the new design it is structurally impossible because there is no target that accepts source).

```
MetamorphosisEngine.undergo(user_reason)                       ← see J in 03-MIGRATION-TESTING-METAMORPHOSIS.md
  → inspect: architecture manifest + experience/eval aggregates + failure classes
            + self_model snapshot + open opportunities + candidate source availability
  → propose: N candidates over the 4 targets (never source, never protected set)
  → materialize: write real payload into a CANDIDATE tree under versions/candidates/<v>/
  → isolate:   SandboxEngine with provider=local_bwrap, network denied, prod RO
  → benchmark: EvoBenchmarkSuite v2 (real task corpus) vs active version
  → verify:    compat + integrity + invariants + regression + safety + judge sample
  → verdict:   BETTER | WORSE | INCONCLUSIVE   (INCONCLUSIVE = reject, never promote)
  → promote:   PromotionEngine → version dir + atomic switch + post-activation health
  → observe:   N cycles of live metrics vs prediction; breach → auto-rollback
```

The fix that makes all of this real: **`AgentRuntime` resolves its capability substrate from `versions/active/` at cycle start** (allow-listed subdirectories only). That one line of indirection converts the entire evolution spine from bookkeeping into causation.

### E.4 One user-facing surface

`evo` remains the only product. Internally: one loop, pluggable backends. Users never see "DeerFlow mode" or "DSH mode" as an agent; they see `evo "research X and produce a cited report"`, and `evo agent --backend lead-agent` is an *operator tuning knob*, logged as `RUNTIME_BACKEND_SELECTED` and benchmarkable — not a product identity.

---

## F. Target repository / file structure

Existing 33 modules stay in place at the same import paths (no big-bang refactor). Additions are new packages; only `security.py`, `kernel.py`, `runtime.py`, `verifier.py`, `storage.py`, `sandbox.py`, `promotion.py` (the current gate's PROTECTED list) receive surgical extensions.

```
evo/
├── evo_agent/
│   ├── sovereign/                      ★ NEW TRUST DOMAIN  (protected; hash-locked; not writable by agent)
│   │   ├── governance.py               rules of evolution; protected set; verdict policy
│   │   ├── approval_authority.py       approvals (wraps SecurityPolicy; sole mint)
│   │   ├── verification_authority.py   verdicts (sole source of "verified")
│   │   ├── audit.py                    append-only log API + hash chain + export
│   │   ├── rollback_authority.py       sole rollback path
│   │   ├── shutdown.py                 kill switch / safe mode (single source of truth)
│   │   └── PROTECTED.manifest          sha256 of every file here; verified at startup
│   │
│   ├── ports/                          ★ additive-default Protocols (DeerFlow extension-api rule)
│   │   ├── protocol.py                 `@additive` decorator: defaults mandatory
│   │   ├── turn_engine.py              TurnEngine, TurnContext, TurnDecision
│   │   ├── execution_backend.py        ExecutionBackend (the Runtime Adapter seam)
│   │   ├── sandbox_provider.py         SandboxProvider (PORT: DeerFlow sandbox_provider)
│   │   ├── skill_storage.py            SkillStorage / SkillCatalog
│   │   ├── memory_backend.py           MemoryBackend (PORT: DeerFlow memory manager)
│   │   ├── tool_provider.py            ToolProvider (native | MCP | plugin)
│   │   ├── verifier_plugin.py          VerifierPlugin (receipts, checklist; judge deferred)
│   │   ├── evolution_target.py         Materializer per target kind
│   │   └── policy_projection.py        HostPolicySnapshot analogue (narrow, defaulted)
│   │
│   ├── backends/                       ★ RUNTIME ADAPTER implementations
│   │   ├── native.py                   default; Evo's own turn loop (offline deterministic)
│   │   ├── lead_agent.py               [BRIDGE] DeerFlow; guarded by optional extra
│   │   ├── dsh.py                      [PROCESS] deepseek-harness subprocess in sandbox
│   │   ├── code_exec.py                [PORT] sandboxed code execution for coder steps
│   │   └── availability.py             probes extras/binaries; never raises at import
│   │
│   ├── pipeline/                       ★ turn middleware pipeline (PORT: middlewares + guards)
│   │   ├── engine.py                   order resolved from declared Placement + hook axis (PORT
│   │   │                               from extension-api/placement.py); installs sovereign
│   │   │                               invariants as live checks (DSH style, violations abort); fail-closed
│   │   └── stages/
│   │       ├── token_budget.py         loop_guard.py       repeat_guard.py
│   │       ├── read_before_write.py    sanitization.py     compaction.py
│   │       ├── policy_filter.py        receipts.py         error_handling.py
│   │       └── termination_detect.py   tool_output_budget.py
│   │
│   ├── skills/                         ★ self-extension unit (PORT: DeerFlow skills + DSH skill)
│   │   ├── frontmatter.py  parser.py  catalog.py  projection.py  activation.py
│   │   ├── installer.py    permissions.py  tool_policy.py  validation.py
│   │   ├── security_scanner.py         static + LLM moderation, fail-closed
│   │   └── acquisition.py              SkillEvolutionConfig analogue: enabled=False default
│   │
│   ├── mcp/                            ★ NEW (closes G4; single implementation for both upstreams)
│   │   ├── config.py  registry.py  session_pool.py  transport_stdio.py
│   │   ├── tool_adapter.py             MCP tool → Evo Tool (risk, schema, permissions)
│   │   └── policy.py                   server allowlist, output size caps, redaction
│   │
│   ├── research/                       ★ NEW (closes G5)
│   │   ├── provider.py                 ResearchProvider protocol
│   │   ├── fetch_stdlib.py             urllib-based bounded fetch (no new deps)
│   │   ├── provenance.py               every resource → Provenance record
│   │   └── report.py                   cited markdown report (report_generation impl)
│   │
│   ├── delegation/                     ★ over existing specialist.py (closes G6)
│   │   ├── engine.py                   contract → executor → receipt → verification
│   │   ├── executors/{llm,sandbox,dsh,mcp_agent}.py
│   │   └── limits.py                   fan-out caps, depth, budget, no-recursion rule
│   │
│   ├── verification/                   ★ replaces 37-line verifier as the authority's engine room
│   │   ├── receipts.py                 deterministic per-tool-call receipt
│   │   ├── checklist.py                success criteria → checks
│   │   ├── judge.py                    DEFERRED — see 05 §1.1 (upstream knob unimplemented;
│   │   │                               sovereign gate alone owns `satisfied`)
│   │   └── authority_hook.py           registers into sovereign.verification_authority
│   │
│   ├── plugins/                        ★ PORT DSH plugin-inventory + loader discipline
│   │   ├── inventory.py                entries, effective-enablement, phase machine
│   │   ├── loader.py                   allow-listed entry points, isolated failure
│   │   └── lifecycle.py                pending|loading|active|failed|unloading
│   │
│   ├── invariants/                     ★ PORT DSH convention
│   │   ├── registry.py                 every layer declares invariants here
│   │   ├── sovereignty.py  single_loop.py  capability_integrity.py
│   │   └── memory_isolation.py  promotion_eligibility.py  evidence_integrity.py
│   │
│   ├── metamorphosis/                  ★ engine split (was 578 lines in one class)
│   │   ├── targets/{skill,tool_binding,provider_config,pipeline_stage}.py
│   │   ├── materializer.py             candidate payload writer (real files)
│   │   ├── operator.py                 end-to-end `undergo` orchestration
│   │   └── (metamorphosis.py stays as the compat façade re-exporting this)
│   │
│   ├── active_version.py               ★ the missing causal link (E.3)
│   ├── config.py  modes.py  context.py  artifacts.py  feedback.py  credentials.py
│   └── serve/                          ★ closes G9: stdlib http.server JSON-RPC
│       ├── __main__.py  rpc.py  auth_token.py                     (loopback, token-gated)
│
├── capabilities/                       ★ user-visible capability tree (promotable, inspectable)
│   ├── skills/{builtin/,installed/,candidates/}
│   ├── tools/           mcp/servers.json       profiles/{minimal,local,research,full}.json
├── evo.toml / config/                 governance policy (protected), profiles, provider config
├── tests/
│   ├── test_invariants_*.py            one per invariant file (DSH convention)
│   ├── fixtures/plugins/               ★ adversarial: throws, late, self-dispose, missing-dep
│   ├── test_architecture_single_loop.py
│   ├── test_metamorphosis_closed_loop.py   ★ proves promotion changes behaviour (E.3)
│   └── test_sovereign_immutable.py         ★ proves agent cannot write sovereign/
├── docs/evolution/                     this analysis (A–L)
├── scripts/
│   ├── run_production_gate.py          existing; PROTECTED list extended to sovereign/
│   └── verify_sovereign_digest.py      NEW: startup + CI integrity check
└── desktop/  web/  scripts/  pilot/    unchanged shape; web/ gains real RPC client
```

**Not vendored, ever:** `deer-flow/backend/**`, `deepseek-harness/packages/**`. Integration is by re-implementation of contracts plus optional process/extra bridges.

---

## G. Interfaces and adapters required

### G.1 The single most important contract

```python
# evo_agent/ports/execution_backend.py
class ExecutionBackend(Protocol):
    """A backend executes turns. It never authorizes, verifies, or promotes."""
    name: str
    def probe(self) -> BackendAvailability: ...                 # availability, never raises
    def plan_capability(self, req: CapabilityRequest) -> BackendPlan: ...
    def run_turn(self, ctx: TurnContext, sink: EventSink) -> TurnResult: ...
    def cancel(self, turn_id: str, reason: CancelReason) -> CancelAck: ...
    def export_receipts(self, turn_id: str) -> list[Receipt]: ...  # fed to Verifier
```
Hard rules (each enforced by a test, not by prose):
- `run_turn` returns `TurnResult(status, artifacts, receipts, usage)` — **no `success` field**. Success is only ever produced by `sovereign.verification_authority`.
- Backends receive `HostPolicySnapshot`-style projections (`policy_projection.py`), never the store, policy object, or approval callback.
- A backend must declare `declared_capabilities` and `declared_risks`; the router refuses any backend whose declaration under-states what it did (checked against receipts).

### G.2 Adapter table

| Interface | Implementations | Authority direction |
|---|---|---|
| `TurnEngine` | `RuleBasedAdapter` (existing, now an adapter), `OpenAICompatibleAdapter` (extended to tools), `AnthropicAdapter` | Evo → provider |
| `ExecutionBackend` | `native`, `lead_agent` (DF), `dsh`, `code_exec` | Evo drives; result is data |
| `SandboxProvider` | `local_bwrap` (existing logic extracted), `unshare` (existing), `docker`, `e2b`, `windows_acl` (DSH) | Evo → OS |
| `ToolProvider` | `ToolRegistry` (existing 4, extended), `SkillToolProvider`, `MCPToolProvider`, `ResearchProvider` | registration is Evo-gated |
| `MemoryBackend` | `sqlite_default` (existing), `deermem_pattern` (port), `mem0`/`honcho` (bridge) | retrieval is Evo-side |
| `VerifierPlugin` | `deterministic_postcondition` (existing, upgraded), `receipt_ledger` (bound to append-ids, **not** positional — avoids DeerFlow's post-compaction renumbering bug), `checklist` | **advisory escalation only**; may tighten, never loosen. `satisfied` is owned solely by the sovereign gate, and the token `satisfied` is lint-forbidden elsewhere |
| `Materializer` | `skill`, `tool_binding`, `provider_config`, `pipeline_stage` | candidate payload only |

### G.3 Backward-compatibility requirement

`pyproject.toml` keeps `dependencies = []`. Every DeerFlow/DSH-adjacent capability that needs third-party code is behind:
```toml
[project.optional-dependencies]
research   = ["beautifulsoup4>=4.12"]
mcp        = ["mcp>=1.0"]
deerflow   = ["langgraph>=0.2", "langchain-openai>=0.2"]
```
`backends/availability.py` probes at startup and reports `{"lead_agent": "unavailable: extra not installed"}`. **Absence of an extra must degrade to `native`, never to an error or to a silent downgrade of governance.** `tests/test_degradation_matrix.py` enumerates every missing-extra permutation.

### G.4 Compatibility rules for all ports (adopted from DeerFlow verbatim)

From `packages/extension-api/deerflow_extension_api/contracts.py`:
> *"every Protocol method carries a default implementation, so adding a method later stays additive for already-released extensions; every optional dataclass field carries a default, so adding a field stays additive."*

Evo enforces this structurally: `ports/protocol.py` provides `@additive`, which fails import if any protocol member lacks a default, and `tests/test_ports_additive.py` asserts it across all ports. This is what allows a promoted version to add a port method without orphaning already-installed skills — i.e. without breaking persistence.
