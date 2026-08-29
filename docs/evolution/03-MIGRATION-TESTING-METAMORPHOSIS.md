# Migration Plan, Test Strategy, Metamorphosis Design

---

## H. Migration plan

Guiding constraint: **the repository must stay green at every step.** 353 passing tests plus the production gate are Evo's real asset; each phase below ends with the gate passing and a tagged checkpoint. Nothing is a rewrite; everything is an addition behind a seam, then a switch.

### M0 — Freeze and measure (no behaviour change)

1. Tag baseline `pre-integration` at current commit; record `pytest` counts and the seven protected-file digests.
2. Add `scripts/verify_sovereign_digest.py` reporting digests for the current PROTECTED set **plus** `evo_agent/metamorphosis.py`, `evo_agent/evolver.py`, `evo_agent/orchestrator.py`, `evo_agent/benchmark.py`, `evo_agent/memory.py` (today unprotected but governance-adjacent).
3. Add failing-by-design characterisation tests that encode the four audit defects, each marked `xfail(strict=True)`:
   - `test_memory_not_used_at_plan_time` (kernel uses `recent_memories()` raw query)
   - `test_kernel_architecture_version_empty` (`kernel.py:255`)
   - `test_promotion_does_not_change_execution` (no loader of `versions/active`)
   - `test_evolution_config_unconsumed` (write with no read)
   These become the acceptance list for M1–M3 and turn "we found problems" into "problems are gated".
4. Convert `cli.py`'s 200 flags into a **generated** flag registry (introspection only, same parser) so a later subcommand migration can prove 1:1 parity.

Exit: gate green (4 xfailed allowed and documented), digests published.

### M1 — Sovereign Core extraction (make the boundary physical)

Move policy/verification/audit/shutdown *authorities* into `evo_agent/sovereign/` as thin modules that delegate to the existing `SecurityPolicy`/`Verifier`/event log, then have `security.py`, `verifier.py`, `checkpoints.py` re-export them. No logic moves in M1 — only the **address** of authority. `PROTECTED.manifest` ships and is verified at `AgentRuntime.start()` (fail-closed: mismatch → refuse to start, unless `--allow-sovereign-drift` for developers, which itself emits `SOVEREIGN_DRIFT_ACCEPTED`).

Why not move logic now: the production gate hard-codes seven file paths, `tests/` import `evo_agent.security`, and metamorphosis hashes `source_reference`. Address first, logic later, so every diff stays reviewable.

Exit: same behaviour; new test proves tampering with `sovereign/*` refuses startup.

### M2 — Close the dead links (fix G10 and the two B.10 defects)

- Kernel `_architecture_version()` → resolve from `MetamorphosisEngine.get_architecture()` like the runtime already does; delete the duplicate by moving resolution into `sovereign/` and having both call it.
- Kernel plan-time memory → `MemoryManager.retrieve(RetrievalQuery(goal, kinds, max_age, min_confidence, limit=6))`, with the raw `recent_memories()` call kept as the fallback when the memory table is empty (preserves determinism of existing tests).
- Add public `PromotionEngine.active_version()`; replace `orchestrator.py:764,927` private `_active_version()` reaches.
- Remove the `xfail(strict=True)` markers for those tests.

Exit: `test_memory_used_at_plan_time` and `test_architecture_version_propagates` pass; pilot corpus results unchanged or better (measured, not assumed).

### M3 — Materialization: make evolution causal (fix G2 — the keystone)

1. `evo_agent/active_version.py`: resolve allow-listed subpaths from `versions/active/` → `capabilities/skills/installed/`, `mcp/servers.json`, `provider overlays`, `pipeline stage selection`. Fall back to repo defaults when no active version exists (fresh install).
2. `MetamorphosisEngine.materialize_candidate()` writes **real payload files** into `versions/candidates/<version_id>/` for the four target kinds (never source code).
3. `SandboxEngine.run_experiment()` gains `candidate_overlay=…`: the candidate tree is built as `defaults + overlay`, so the sandboxed pytest run and a **behavioural probe run** execute against the overlaid capability set.
4. `PromotionEngine.promote()` — unchanged in governance terms — now has an observable effect because step 1 exists. Post-activation health check gains `active_capabilities_digest` so a mismatch is detectable and triggers the existing `_rollback_after_failure`.
5. **New closed-loop test** `tests/test_metamorphosis_closed_loop.py`: propose a skill → materialize → sandbox → benchmark BETTER → promote → assert a real task now behaves differently → rollback → assert behaviour returns. This single test is the definition of done for "self-extension".

Exit: the `test_promotion_does_not_change_execution` xfail is deleted and inverted into a positive assertion.

### M4 — Ports, backends, pipeline (fix G1, G12)

Add `ports/*`, `pipeline/*`, `backends/native.py`. `RuleBasedAdapter` becomes a `TurnEngine` implementation. `CognitiveOrchestrator` gains a `TurnEngine`-driven path behind `config.agent.loop = "native"` (default `legacy` first, flip in M4 final commit, keep `--legacy-kernel` as the documented escape hatch for one release).

DeerFlow's middleware *set* is ported selectively and in dependency-free form; each stage has its own invariant file. `loop_guard`, `repeat_guard`, `tool_output_budget`, `compaction`, `sanitization` ship first; `deferred_tool_filter` and `uploads` wait until there are more than 12 tools.

### M5 — Capabilities: skills, MCP, research, delegation (fix G3–G6)

Order matters: **skills before MCP before delegation.**
- `skills/` with `SkillEvolutionConfig` analogue (`enabled=False`, `security_fail_closed=True`) — self-extension is opt-in and fail-closed from its first commit, matching DeerFlow's own default.
- `mcp/` — single implementation shared by both upstreams (rule 15), with `policy.py` caps before any transport lands.
- `research/` — `fetch_stdlib.py` first (zero deps), `report.py` producing cited markdown; then optional provider extras.
- `delegation/` — give `SpecialistOrchestrator` real executors. `delegation/limits.py` must land in the same commit as the first executor: fan-out cap, depth cap, `no_recursive_delegation`, budget inheritance.

### M6 — Surface unification (fix G9)

`evo serve` (stdlib JSON-RPC, loopback + token) + `evo <noun> <verb>` subcommands with a **parity test** asserting every legacy flag still resolves to the same handler. `desktop/bridge` grows from 7 → ~14 commands (skills, MCP health, metamorphosis status, verify, rollback) and the Web UI finally gets a client for the same RPC. One agent, three thin views; the desktop bridge's existing "delegate to the same authorities, expose no new authority" discipline is the template.

### M7 — Governance hardening + benchmark v2 (fix G8)

Benchmark v2 over a real task corpus (see I.3), `governance` config file, invariant registry complete, adversarial plugin fixtures, degradation matrix, and a **metamorphosis stress test** (100 candidate cycles) as a release gate.

### Sequencing rationale and risk table

| Phase | Reversibility | Main risk | Mitigation |
|---|---|---|---|
| M0 | trivial | none | — |
| M1 | high | import cycles | thin delegating modules, no logic move |
| M2 | high | nondeterminism in existing tests | keep fallback path; measure pilot before/after |
| M3 | medium | overlay shadowing defaults | allow-list + digest verification + health check |
| M4 | **medium-high** | loop swap changes behaviour | dual-loop behind config, one release; A/B benchmark |
| M5 | medium | unsafe new capability surface | fail-closed defaults, MCP caps before transport |
| M6 | low | API becomes a 2nd authority | token-gated loopback; RPC handlers are CLI handlers, no new authorities |
| M7 | low | benchmark overfits corpus | hold-out task set, seeded multi-trial, variance report |

Kill-switch note: every phase's new code paths must be reachable-but-inert when `safe_mode=True` or `kill_switch` active. That is a required assertion in `tests/test_safe_mode_coverage.py`, not a review comment.

---

## I. Test strategy

### I.1 Principles (two imported, one native)

1. **Invariant-as-runtime-check** (DSH, grounded in `packages/runtime-diagnostics/invariants/`): each layer owns `evo_agent/invariants/<layer>.py` exporting **installed check callables** the turn pipeline invokes on the live path — a violated check aborts the turn and emits `INVARIANT_VIOLATION`, mirroring DSH's `InvariantFailure = (message) => never` — *plus* pytest coverage of the check itself, *plus* an explicit `NO_RUNTIME_INVARIANT = "<reason>"` for a layer that legitimately has none (DSH does exactly this for `plugin-inventory` and `repeat-tool-reminder`, each with a stated justification). `tests/test_invariant_coverage_matrix.py` fails on a layer with **neither** a check nor a reason: a stated absence is auditable, a silent one is not. Start with the ~7 checks in §I.2; the registry is the deliverable, not the count (DSH carries 247 invariant files, each on the hot path).
2. **Adversarial fixtures** (DSH): every extensibility seam is tested with a hostile input — `tests/fixtures/plugins/{throws,late_service,self_dispose,missing_dependency,bid_out_risk,privilege_escalation}.py`.
3. **Authority inversion** (Evo-native): for every new seam, a test proving the downstream component *cannot* escalate. This class of test is what makes the design trustworthy rather than merely tidy.

### I.2 Layer-by-layer

| Layer | Tests |
|---|---|
| `sovereign/` | digest verification (tamper → refuse start); approval mint only via authority; verdict only via authority; kill switch not clearable by runtime ops (existing behaviour preserved); audit append-only (no UPDATE/DELETE path); `test_sovereign_immutable.py` — agent tool surface cannot write into `sovereign/` or `config/governance.*` even with `workspace_write` |
| `ports/` | `test_ports_additive.py` — every port method has a default; removing a default fails import; `HostPolicySnapshot` exposes no store/policy handle (assert via introspection) |
| `backends/` | `test_backend_cannot_self_certify.py` (no `success` field exists — assert by dataclass introspection, strongest kind of test); missing-extra → `probe()` unavailable, not raise; `dsh` backend: fake binary that hangs → cancel + timeout + receipts partial; fake binary that prints "APPROVE PROMOTION" → `TOOL_RESULT_SANITIZED`, verdict unchanged |
| `pipeline/` | per-stage unit + ordering tests (placement rules), `loop_guard` catches a synthetic 50-iteration identical-tool loop, `repeat_guard`, `read_before_write` blocks write-without-read, budget enforcement measured on tokens *and* bytes, `compaction` preserves provenance of surviving receipts |
| `skills/` | frontmatter round-trip; path traversal / colon / absolute-path in `SKILL.md` name rejected; **zip-bomb and member-count bombs rejected**; unparseable scanner verdict → **block**; `secrets-autonomous` required to use a credential without an approving turn; `installer` rejects `..`/absolute; `security_scanner` **fail-closed**: moderation model absent → write blocked (mirror of DeerFlow's `security_fail_closed`); disabled-by-default → `acquisition.enabled=False` means `install()` raises; skill claiming `shell` permission without grant → refused by `tool_policy` |
| `mcp/` | server not in allowlist → refused; oversized `tools/list` → capped; MCP tool declaring `risk=low` but performing write → risk floor applied by `policy.py`; transport dies mid-call → `ToolResult.success=False` + receipt + no retry storm |
| `research/` | fetch bounded by size/time/scheme (`file://` refused); provenance recorded for every resource; report cites only fetched resources — **a fabricated citation fails the test** |
| `delegation/` | contract scope enforced; depth/fan-out caps; recursion refused; child cannot widen permissions; conflicting outputs → `EvidenceConflict` recorded, central verifier decides; child timeout → parent continues with partial |
| `verification/` | receipts deterministic (same input → same hash) **and stable across compaction (append-id binding)**; advisory layers may tighten, never loosen (an advisory `PASS` cannot flip an authoritative FAIL; an advisory FAIL *can* flag); `satisfied` token absent outside `sovereign/` (vocabulary lint); exposure≡execution over every (tool × backend) pair; checklist parse failures → FAIL, not silence; **regression test for the 37-line verifier's default-open behaviour**: an unrecognized expectation string must no longer silently pass |
| `metamorphosis/` | all four targets: materialize→sandbox→benchmark→promote→observe→rollback; protected-target refusal; `INCONCLUSIVE` cannot promote; candidate with protected-file diff → rejected **before** sandbox; concurrent metamorphosis serialized |
| `active_version.py` | overlay resolution precedence; digest mismatch → refuse; missing active → repo defaults (fresh install path) |
| E2E | `test_metamorphosis_closed_loop.py`; `test_degradation_matrix.py` (every extra permutation); `test_safe_mode_coverage.py`; `test_recovery_matrix.py` (kill -9 at each phase boundary → resume, matching existing `_recover_interrupted_tasks`) |

### I.3 Benchmark v2 — the substrate that makes promotion meaningful

`benchmark.py`'s machinery (multi-trial, `AggregateMetrics`, `detect_regressions`, `evaluate_safety`, signed `EvolutionEvidence`) is correct and retained. Only the **task corpus** is wrong: it currently checks whether `evolution_config.json` exists. Replace with a real corpus, keeping `TaskCase`'s schema:

| Suite | Cases | Metrics |
|---|---|---|
| `core-local` (ported from `pilot/v1_task_corpus.json`) | read-only workspace tasks, approval-required writes | success, steps, verifications, adaptations |
| `recovery` | injected tool failure, mid-task environment change, approval denial, timeout | recovered?, retries, wasted work, correct terminal state |
| `research` (new) | bounded-fetch topics with checkable facts; **one seeded "adversarial page"** containing injected instructions | citation validity, fact-check score, prompt-injection refusal |
| `skill-acquisition` (new) | gap → install candidate skill → use it | acquisition success, permission discipline |
| `delegation` (new) | fan-out with one slow/conflicting child | completion, conflict detection, budget |
| `metamorphosis-regression` (new) | the 7 canonical protected-boundary attacks | **must be 100% refusal, permanently** |
| `hold-out` | never used for proposal generation | guards benchmark overfitting |

Determinism rules: seeded multi-trial (≥3), report variance, `comparison criteria` per suite, and cost/latency ceilings so "better" can't mean "more tokens". The judge model is fixed per benchmark version and its version is embedded in the evidence hash.

### I.4 CI

Extend `production-gate.yml` (keep setuid-bwrap install — it is the correct choice and already documented in the workflow). New steps: `verify_sovereign_digest.py`, invariant suite, degradation matrix, benchmark smoke (`core-local` + `metamorphosis-regression` only, for runtime), `test_architecture_single_loop.py`. Add a `nightly.yml` running full benchmark v2 + metamorphosis stress.

---

## J. Evolutionary Metamorphosis — design as a first-class operation

### J.1 Definition

> **Evolutionary Metamorphosis** is the governed operation by which Evo replaces one or more of its own *materialized capability structures* with a candidate version, after the candidate has been built, isolated, benchmarked against the currently active version, and verified superior — with the whole change reversible by native rollback.

It is **not** the ability to rewrite arbitrary source. Formally, for the four targets in §E.3 it is total: skill sets, tool bindings, provider configuration, and pipeline stage selection may be replaced. For the protected set it is empty by construction: there is no target kind that accepts source, so "modify governance" is not a proposal Evo can represent. Today's `validate_proposal` rejects proposals containing the *string* `generated_code` (`metamorphosis.py:357`); the new design removes the need for that kind of check by removing the capability it polices.

### J.2 Invocation

```
evo undergo-metamorphosis                        # full cycle, one bounded pass
evo undergo-metamorphosis --focus "task failures on research goals"
evo undergo-metamorphosis --candidates 3 --dry-run          # propose + validate only
evo undergo-metamorphosis --auto-sandbox --no-auto-promote # default: no auto-promote
evo metamorphosis status / explain <id> / evidence <id> / rollback <id>
```
Also invocable in-loop: the phrase "Undergo Evolutionary Metamorphosis." arriving as a *goal* is classified by `IntentModel` as `METAMORPHOSIS_REQUEST`, which **cannot** be self-approved — it enqueues a `EvolutionWorkItem(kind=metamorphosis, approval=human)` on the existing orchestrator queue. A model cannot be talked into promoting itself by being told to.

### J.3 The cycle (state machine)

```
IDLE
 └─(invoke)→ INSPECT        manifest, experience aggregates, evaluation metrics, failure
                            classes, self-model claims/limitations, open opportunities,
                            unused capabilities, capability gaps, benchmark history
             PROPOSE        N candidates, each: target kind + payload diff + expected benefit
                            + risks + rollback plan + success criteria + evaluation method
             VALIDATE       protected-target check · reversibility · compatibility ·
                            required components/capabilities present · schema-valid payload
                            · size caps · determinism of payload
             ── fail → REJECTED (reason persisted, cooldown recorded)
             MATERIALIZE    write real candidate payload → versions/candidates/<vid>/
                            compute active_capabilities_digest(candidate)
             ISOLATE        SandboxProvider(local_bwrap): network denied, prod RO,
                            baseline = defaults+active-overlay, candidate = defaults+new-overlay
                            run: unit subset + behavioural probes + benchmark smoke
             BENCHMARK      benchmark v2 suites, seeded ≥3 trials, side-by-side
             VERIFY         regressions + safety + evidence integrity + protected-core
                            immutability + invariants(all layers) + judge sample
             ── BETTER | WORSE | INCONCLUSIVE
             PROMOTE        existing PromotionEngine: register → eligibility (time-of-use
                            re-check) → human approval → checkpoint → stage → integrity →
                            atomic switch → health(active_capabilities_digest)
             OBSERVE        K cycles of live metrics vs predicted benefit
                            ── breach → AUTO-ROLLBACK (native) → QUARANTINE candidate+evidence
```
`INCONCLUSIVE` is a first-class terminal state that means **do nothing** — never "promote anyway". `EvolutionEvidence` already distinguishes it; the design's job is to make the orchestrator treat it as blocking (it currently can, via `validate_eligibility`, and this must be asserted by test rather than trusted).

### J.4 Candidate generation — where proposals come from

Real inputs, not invention:
1. **Failure mining** (existing `Evolver.identify_weaknesses`) → `provider_config`/`pipeline_stage` tweaks (budgets, retry/recovery, guard params).
2. **Capability gaps** (`CapabilityGapDetector` → `UNAVAILABLE`) → `skill` or `tool_binding` proposals; a `skill` proposal may only reference a catalogued, scanned skill package — the candidate payload is a *selection*, not authored content.
3. **Model routing evidence** (`model_intelligence` trials/evaluations) → `provider_config` routing changes.
4. **Tool health** (`ToolHealth.success_rate`, timeouts) → `tool_binding` fallback-order changes.
5. **Memory-derived** procedures (`memory_procedures`) → `skill` promotion of a proven procedure (this is the one genuinely novel loop: procedural memory becomes a candidate skill, then must survive the same gauntlet).
6. **Upstream availability** (`backends/availability.probe()`) → `provider_config` enabling a backend that is present but off.

Explicitly out of scope: any candidate whose payload is code, prompt text authored by the agent to change its own guardrails, or a change to a protected file. `identify_structural_opportunity()`'s current hardcoded `structured_context` guess (`metamorphosis.py:282`) should be replaced by the above inputs — it is the one part of the existing engine that is a placeholder rather than a mechanism.

### J.5 Verification requirements (non-negotiable, and each is testable)

| # | Requirement | Mechanism |
|---|---|---|
| V1 | Protected set byte-identical | digest comparison across `PROTECTED.manifest`; refuse before sandbox |
| V2 | Candidate isolated | `SandboxProvider` only; refusal to run unsandboxed; `_isolation_policy()` recorded in evidence |
| V3 | Real benchmark, real margin | suite-specific criteria + variance; no promotion on `INCONCLUSIVE` |
| V4 | Reversibility proven | rollback rehearsal in-sandbox *before* promotion is offered |
| V5 | Independent authority | human approval for promotion, always; `MetamorphosisStatus.APPROVED` cannot be self-minted |
| V6 | Invariants pass | every file in `evo_agent/invariants/` executed against candidate overlay |
| V7 | Runtime confirmation after promotion | `active_capabilities_digest` recompute + N-cycle observation + auto-rollback on breach |
| V8 | Audit complete | every state transition emits into the existing append-only event stream; evidence hash chain verified |
| V9 | Bounded frequency | existing `evolution_cooldowns` + max candidates/cycle + max concurrent experiments (1) |
| V10 | Receipt integrity survives compaction | Evo receipts bind to **append-ids, never positional display indices** — DeerFlow's own docs record that compaction renumbers `r1..rN`, so a pre-compaction `[r3]` can resolve to a different call afterwards; Evo must not import that bug |
| V11 | Limits cannot be configured away | every governance-relevant bound is **clamped** to floor/ceiling (DeerFlow `clamp_subagent_concurrency`), so no candidate, profile, or env var can widen it |

### J.6 What metamorphosis can *become* later

Once M3+M4 land, the same operation can be widened by *adding a target kind* — e.g. `context_strategy`, `report_template`, `research_provider_set`, or a candidate that swaps in the `lead_agent` backend — without touching governance code. That is the payoff of the port/target design: capability growth does not require widening the protected boundary.
