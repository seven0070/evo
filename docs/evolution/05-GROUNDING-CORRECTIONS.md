# Grounding and Corrections — upstream source actually read

Method change: this document supersedes tree-level inference. Both upstreams were downloaded as source tarballs
(`bytedance/deer-flow@main`, 23.9 MB; `deepseek-ai/deepseek-harness@master`, 16.6 MB) and the relevant modules read
directly. DeerFlow's harness package alone is **114,536 LOC of Python**; `agents/middlewares` is 14,427 LOC;
`skills` 6,217; `persistence` 10,032; `subagents` 3,104. DSH ships **247 `invariant.ts` files**.

Every claim below carries a `path:line` reference. Sections of the other documents that this page corrects are
marked inline in those files as **[grounded]**.

---

## 1. Corrections to my earlier integration maps

### 1.1 DeerFlow "verification": I overstated the judge and missed the real gate

What I wrote (01 §1.1): *"Receipts + selective judge … `judge_enabled`, `judge_model_name`"*.

Verified reality:

- **`tool_receipt.py` + `tool_receipt_middleware.py` (150 + 156 LOC) are real and better than described.** They are *"Deterministic tool-call receipts: the zero-LLM verification layer"*, stamped into `additional_kwargs` by middleware, **derived from the message stream and never stored separately**, so *"rendering for the model and harvesting for the parent agent always agree."* The module documents two failure modes explicitly:
  - *Freshness*: receipts capture the **raw** tool return *before* sanitization/truncation rewrites content further along the chain, so `output_sha256` is "a *freshness stamp*, not a re-checkable fingerprint against the persisted message."
  - *Renumbering*: display ids `r1..rN` are positional over the append-only message list; **compaction drops older `ToolMessage`s and renumbers the survivors**, so an `[r3]` cited pre-compaction can point at a different call post-compaction. Their own conclusion: citation verification must resolve `[rN]` "against the ledger as of the citing turn, not the post-compaction ledger."
  - They also enforce a **vocabulary firewall**: the boolean `satisfied` is *exclusive to the runtime hard gate*; advisory layers must use neutral terms (`citation_resolved`, `supported`) *"so the model never conflates evidence with acceptance."*
- **The "selective judge" is not implemented.** `judge_enabled` / `judge_model_name` exist only as fields in `deerflow:config/verification_config.py:19,23`. Grepping the whole backend (excluding tests) for any read of them returns **nothing but the definition**. `deerflow:app_config.py:265` advertises *"receipts, checklist, judge"*; only receipts exists. Do not port a phantom.
- **The real completion gate is elsewhere and is much better:** `runtime/goal.py::evaluate_goal_completion`. System instruction, verbatim: *"You are a strict completion evaluator… Decide whether the active goal is fully satisfied using ONLY the visible conversation evidence. Do not assume files, commands, tests, or external state changed unless the conversation explicitly shows it. If the visible evidence is too weak to prove progress, **fail closed** with blocker `missing_evidence`."* Contract: exactly one JSON object `{"satisfied": boolean, "blocker": string, "reason": string, "evidence_summary": string}` (`deerflow:goal.py:154-156` raises `ValueError` if `satisfied` isn't a bool). Blocker taxonomy: `none | needs_user_input | run_failed | external_wait | goal_not_met_yet | missing_evidence`. Two cheap pre-checks run before any model call (`deerflow:goal.py:289-297`): no visible conversation, or no visible assistant evidence → `satisfied=False, blocker=missing_evidence`. The evaluator model is created with **`thinking_enabled=False`** (`deerflow:goal.py:260`) — deliberately a cheap, non-reasoning model for a yes/no judgement — and executes *after* the main run, outside the graph.

**Design consequence for Evo.** Evo's `Verifier` should be replaced by *two* layers, not one:
1. `verification/receipts.py` — deterministic, message-derived facts, **immune to compaction by construction**: bind each receipt to an immutable append id, not a positional index, so Evo never inherits DeerFlow's renumbering bug.
2. `sovereign/verification_authority.py` — a **hard gate** owning the sole `satisfied` boolean, with `missing_evidence → not satisfied` fail-closed semantics and the typed blocker taxonomy above. Advisory signals (`citation_resolved`, `supported`) are recorded but **cannot** set `satisfied`, and cannot clear a failure.
3. Adopt the vocabulary firewall as a *lint*, not a convention: forbid the token `satisfied` anywhere outside the sovereign module (`tests/test_sovereign_vocabulary.py`).

### 1.2 DeerFlow "skills": far more adversarial than "SKILL.md + frontmatter"

`skills/installer.py` (351 LOC) verified contents:
- Rejects absolute paths, `..` traversal (`deerflow:installer.py:91`), and **colons** — with a specific comment that Windows drive paths (`C:\...`) are "already rejected above as absolute", i.e. the NTFS/colon class is enumerated deliberately (`deerflow:installer.py:66-70`).
- **Zip-bomb defence**: `max_total_size: int = 512 * 1024 * 1024` with a running `total_written` check during extraction, plus an **entry-count** limit, because many small members are *"slow to extract, independent of total size"* (`deerflow:installer.py:136-191`).
- Per-member `if not member_path.resolve().is_relative_to(dest_root)` — resolve-then-contain, the correct order (`deerflow:installer.py:176`).
- Rejects executable binaries outright (`deerflow:installer.py:273` → `SkillSecurityScanError`).

`skills/security_scanner.py` (176 LOC) verified: `_resolve_fail_closed()` **defaults to True when config is unavailable** (`:29-33`); the model is asked for `{"decision":"allow|warn|block","reason"}`; and — the detail worth copying — **"Security scan produced unparseable output" → `block`** (`:170`), **"scan unavailable for executable content" → `block`** (`:172`), fail-open applies only to *non-executable* content and only degrades to `warn` with a logged recommendation (`:175-176`).

Two components I had not credited at all:
- `skills/projection.py` (609 LOC): *"Materialize **enabled-only** skill trees for sandbox filesystem exposure."* Enablement is enforced by **what is physically mounted into the sandbox**, not by a permission check at call time. For Evo this is a strictly stronger primitive than `SecurityPolicy.requires_approval` alone: `SkillStorage → SandboxProvider` mount set. It also gives metamorphosis a clean materialization semantic — enabling a skill *is* changing the mount tree.
- Skills declare **secrets** in frontmatter: `required-secrets` and `secrets-autonomous` (`deerflow:frontmatter.py:21-22`, `deerflow:parser.py:137-174`, with malformed entries warned-and-skipped rather than fatal, and env-var names validated). `secrets-autonomous` is the flag for "this skill may use a credential *without* a live user turn". Evo has `IntegrationCredentialMetadata` but **no autonomy dimension at all** — so `capability`/`tool_binding` candidates that grant a skill credential access would today be indistinguishable from ones that don't.

29 real `skills/public/*/SKILL.md` packages ship upstream (`deep-research`, `data-analysis`, `chart-visualization`, `academic-paper-review`, `consulting-analysis`, `find-skills`, `frontend-design`, `code-documentation`, `bootstrap`, …) — a usable corpus to port as Evo builtins, which is cheaper than authoring one.

**Correction to my Phase 5.1/5.2 sizing:** the frontmatter+catalog part stays ~as estimated, but the *installer/scanner/projection* trio is the real work and needs the `secrets-autonomous` dimension added to Evo's `SecurityPolicy` (new: `RiskLevel` floor for any tool call that consumes a credential without an approving user turn).

### 1.3 DeerFlow `subagents`: I called it "batch lifecycle" — it is a capacity system

3,104 LOC across `subagents/{registry,executor,runtime,batch_runtime,batch_service,capacity,config,status_contract,step_events,token_collector,builtins}`. `agents/middlewares/subagent_limit_middleware.py` enforces *clamped* bounds exported from config (`MIN/MAX_CONCURRENT_SUBAGENT_CALLS`, `DEFAULT/MAX_TOTAL_SUBAGENTS_PER_RUN`, `clamp_subagent_concurrency`, `clamp_total_subagents_per_run`) — i.e. **operator config cannot configure itself out of the limit**, because the value is clamped, not validated. `token_collector.py` meters spend per delegated run; `status_contract.py` is a typed external status.

**Adopt the clamping rule as an Evo-wide governance primitive.** Several Evo knobs today (e.g. `RuntimeResourceLimits.__post_init__`, `SecurityPolicy.max_command_seconds`) accept any value; clamping with floor/ceiling in `__post_init__` and no rejection path is what makes "protected" mean "not configurable away".

### 1.4 `orchestration seam`: semantic placement, not positional

`packages/extension-api/deerflow_extension_api/placement.py` documents the reason precisely: *"Placement is declared as a **semantic guarantee** ("I need to observe the raw tool return") rather than as a structural position ("put me in layer 3"). A middleware occupies one index in the list, but that index only has meaning on the hook chain it actually implements — so "outermost" means different things on the model axis and the tool axis. Declaring by axis-and-end removes that ambiguity and **keeps the host free to restructure its stack**."* Enum: `MODEL_LOGICAL` (outer of retry/error-handling; fires once per logical decision), `MODEL_PHYSICAL` (inner of every request-transformer; fires once per provider call, retries re-enter it), `TOOL_VISIBLE`, …

**This is the mechanism my §E.3 needed and did not have.** For a pipeline stage to be a metamorphosis target, reordering the stack must not break installed skills — which is only true if installed components declare *guarantees* and the host resolves position. `pipeline/engine.py` therefore resolves stage order from declared `Placement` + declared hook axis, and any unresolvable declaration fails validation **at proposal time**, before the sandbox.

### 1.5 Two-layer authority with one identity builder

`authz/adapter.py` presents an `AuthorizationProvider` *as a* `GuardrailProvider`, "no new middleware class required", and — the important half — *"Principal construction delegates to `build_principal_from_context` so **Layer 1 (tool assembly) and Layer 2 (this adapter) share a single identity builder** with consistent `default_role` and `attributes` semantics."* `deerflow:authz/principal.py:3` repeats the invariant from the other side.

**Rule for Evo:** whatever tools exist (assembly) and what each call may do (execution) must derive from the *same* principal construction, or an attacker gets a free "visible but callable" / "invisible but authorized" divergence. Enforce by test: `test_capability_exposure_matches_execution_decision.py` asserts, for every tool and every backend, `is_exposed(t) == may_execute(t)` under a fixed policy.

### 1.6 Overlap I should have flagged as already-solved: `workspace_changes/`

`workspace_changes/{api,diff,recorder,scanner,types}.py` — snapshot capture, `compare_snapshots`, `get_changed_paths`, `get_changed_output_paths`, a summary with `created/modified/deleted/symlink_created/additions`, and `WorkspaceChangeLimits`. Evo already has this: `world.py` (920 LOC) with `EnvironmentObserver`, `EnvironmentDiffEngine`, `FilesystemChangeDetector`, `EnvironmentSnapshot` persistence. **This is a rule-15 overlap: do not port.** Take two deltas only: (a) `symlink_created` in the change taxonomy, (b) an explicit `WorkspaceChangeLimits` bound object, and (c) the "changed **output** paths" view, which Evo's diff lacks and which the report/artifact verifier needs.

### 1.7 `loop_detection`: layered, windowed

`loop_detection_middleware.py` has an explicit two-layer design — Layer 2 is *"per-tool-type frequency (windowed)"* over a deque, with a **per-thread set of tool names already warned about** so a loop produces one warning, not N (`:267`), and a comment noting *"Layer 2's windowed frequency count can never exceed the deque length"* (`:238`) — an invariant stated *about* the algorithm, at the algorithm.

For Evo: port the warning-dedup and the windowed count into `pipeline/stages/loop_guard.py`; the Evo kernel today has **no** loop detection at all — bounded `max_steps` is a step budget, not a repetition detector.

---

## 2. Corrections to what I credited DSH with

### 2.1 Invariants are runtime-enforced and *throw* — not "test files"

My 00/03 text said: *"each layer owns `evo_agent/invariants/<layer>.py` + `tests/test_invariants_<layer>.py`; a rule with no test file does not exist."* That is **weaker than DSH's actual mechanism**, in three ways that matter:

1. **Violations abort.** `dsh:runtime-diagnostics/invariants/src/index.ts:31`: `export type InvariantFailure = (message: string) => never` — documented as *"Throw a package-attributed invariant failure… `never` because reporting a violation throws."* There is an `InvariantError` with a stable `code = 'INVARIANT'` and the owning `packageName`.
2. **They are companion plugins on the live event bus, ordered defensively.** `core/agent-loop/src/invariant.ts` installs `ctx.on('llm/stream', …)` and comments *"**Prepend** prevents a short-circuiting replay listener from silencing the check."* The checks are structural and deep — a loop-built request must be frozen, must carry a session id whose session is live, must have a `step/start` and a `request/header` in its event log, and `options.messages` must **equal `session.deriveMessages()` exactly** (request-reconstruction). So "the prompt you sent is provably the prompt the log says you sent" is an *enforced runtime* property, not an aspiration.
3. **They are selectable and cheap to disable per package** — service `Config { enabled, package_allowlist, package_blocklist }` with regex on package names (`dsh:index.ts:14-23`) — while *"ordinary package entrypoints stay independent of diagnostics"* (module docstring). And a package may legitimately declare **"No runtime invariant" with a stated reason**: `host/plugin-inventory/src/invariant.ts` → *"every snapshot is projected directly from Loader-owned state"*; `guard/repeat-tool-reminder/src/invariant.ts` → *"the repeat chain is private to one post-execute listener and exposes no package-owned event or snapshot that an independent companion can observe."*

**Why this changes Evo's design and not just its prose.** A pytest-only invariant cannot stop a *promoted candidate* from violating a boundary at runtime; an installed companion can. So §I.1 principle 1 becomes: each Evo layer declares invariants in `evo_agent/invariants/<layer>.py` as **installed check callables** that the Turn Pipeline invokes on the live path (fail-closed: a raising check aborts the turn and emits `INVARIANT_VIOLATION`), *plus* pytest coverage for the check's own logic, *plus* — this is the part worth stealing wholesale — **an explicit `NO_RUNTIME_INVARIANT` reason string for every layer that legitimately has none.** `tests/test_invariant_coverage_matrix.py` then fails on: a layer with neither a check nor a reason. A stated absence is auditable; a silent one is not.

Cost honesty: 247 invariant files in DSH is a real maintenance load, and each check sits on the hot path. Evo should start with the ~7 sovereign/pipeline invariants named in §I.2 and make the registry, not the count, the deliverable.

### 2.2 DSH status: "developer preview", explicitly not a security control

Reinforced by `SAFETY.md`: *"experimental developer-preview software… has not undergone a security audit and must not be treated as secure or production-ready… Do not rely on DeepSeek Harness as the sole security control for untrusted workloads."* README: *"**THERE WILL BE COMPATIBILITY-BREAKING CHANGES.**"* This is not a reason to skip DSH — its conventions are the point — but it **is** a reason the `PROCESS` backend must be treated as untrusted-by-default and why no DSH notion may become an Evo authority. My 01 §0 said this; the source confirms it.

---

## 3. Evo-side numbers, corrected

| Claim in 00-AUDIT | Stated | Verified |
|---|---|---|
| SQLite tables | "96 tables" | **120** unique `CREATE TABLE IF NOT EXISTS` names in `storage.py` |
| CLI flags | "~200 flags" | **222** `add_argument` calls in `cli.py` |
| `EventType`s | "~280" | **282** (`models.py:240-524`) |
| store methods | 311 | 311 ✓ |
| `evo_agent` LOC | 22,064 | 22,064 ✓ |

The 96 figure came from reading a truncated grep; it does not change any conclusion (120 tables only strengthens B.4 and the "do not duplicate persistence" rule), but the audit should not carry an unverified number, and the same class of error — confident inference from a partial listing — is exactly what the two sections above correct on the upstream side.

---

## 4. Net effect on the plan

| Doc | What changes |
|---|---|
| `00-AUDIT.md` | numbers corrected (above) |
| `01-INTEGRATION-MAPS.md` | verification row corrected (receipts real / judge absent / goal-evaluator is the gate); skills row gains installer-hardening, projection, secrets-autonomous; `subagent_limit` → clamped bounds; new rows: `placement.py`, `workspace_changes` (overlap), `loop_detection` layering |
| `02-TARGET-ARCHITECTURE.md` | `pipeline/engine.py` resolves order from declared `Placement`; `verification_authority` owns the sole `satisfied`; sovereign vocabulary lint; mount-based skill enablement; exposure≡execution equivalence test |
| `03-MIGRATION-TESTING-METAMORPHOSIS.md` | I.1 principle 1 rewritten (installed checks + stated-absence rule); V-requirements gain "receipt ids immune to compaction"; J.5 gets the clamping rule |
| `04-GOVERNANCE-AND-PLAN.md` | Phase 4.3 gains `placement.py` resolution; Phase 5.1/5.2 split into catalog-trio vs hardening-trio with `secrets-autonomous` added; Phase 5.6 uses clamped limits; K adds "config cannot configure itself out of a bound" |

Nothing here invalidates the architecture: **Sovereign Core → Runtime Adapter → capabilities → Skills/Tools/MCP → Execution → Verification → Evo**, one loop, and materialization as the missing keystone all survive contact with the source. What changes is that three components are *better* than advertised (receipts, skills hardening, invariants), one is *absent* (the judge), and two carry bugs Evo should not import (receipt renumbering; positional placement).
