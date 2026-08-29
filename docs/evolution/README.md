# Evo → Unified Autonomous Agent: Architecture Audit & Integration Design

Status: `00`–`07` are **analysis and specification** (they changed no source file). **P0, P1, P2 and P3
are implemented**; each phase's changes, measurements, and deviations from `07` are recorded in
`08-IMPLEMENTATION-LOG.md`. P4–P8 are not started.
Approved on the record: **Q2 — ship the DeerFlow lead-agent bridge**; **Q4 — sandbox all tool execution**.
Baseline audited: `main` @ `c84da91`, `__version__ == "1.0.0"`.
Upstreams audited: `bytedance/deer-flow@main`, `deepseek-ai/deepseek-harness@master` (2026-08-28).

## Deliverable index (A–L as requested)

| | Deliverable | Where |
|---|---|---|
| **A** | Current architecture | `00-AUDIT.md` §A |
| | Existing agent loop | `00-AUDIT.md` §A.3 |
| | Existing memory system | `00-AUDIT.md` §B.1 |
| | Existing tool/skill system | `00-AUDIT.md` §B.2 |
| | Existing self-extension mechanisms | `00-AUDIT.md` §B.3 |
| | Existing persistence and recovery | `00-AUDIT.md` §B.4 |
| | Existing verification | `00-AUDIT.md` §B.5 |
| | Existing evolution mechanisms | `00-AUDIT.md` §B.6 |
| | Existing sandboxing | `00-AUDIT.md` §B.7 |
| | Existing testing and benchmarking | `00-AUDIT.md` §B.8 |
| | Existing CLI/API/UI | `00-AUDIT.md` §B.9 |
| **B** | What Evo already implements | `00-AUDIT.md` §B.11 |
| | Missing capabilities (G1–G13) | `00-AUDIT.md` §B.12 |
| **C** | DeerFlow integration map | `01-INTEGRATION-MAPS.md` §1 |
| **D** | DeepSeek Harness integration map | `01-INTEGRATION-MAPS.md` §2 |
| | Overlap — must not be duplicated | `00-AUDIT.md` §B.14 + `01-INTEGRATION-MAPS.md` §3 |
| | Integrate as adapters/plugins | `01-INTEGRATION-MAPS.md` §1–§2 (mode column) |
| | Remain independent | `01-INTEGRATION-MAPS.md` §1.1, §2.1 (SKIP rows) |
| | Subjects of Evolutionary Metamorphosis | `02-TARGET-ARCHITECTURE.md` §E.3; full eligibility matrix in `07-UNIFIED-ARCHITECTURE-SPECIFICATION.md` §4 |
| **E** | Target Evo architecture | `02-TARGET-ARCHITECTURE.md` §E |
| **F** | Repository / file structure | `02-TARGET-ARCHITECTURE.md` §F |
| **G** | Interfaces / adapters required | `02-TARGET-ARCHITECTURE.md` §G |
| **H** | Migration plan | `03-MIGRATION-TESTING-METAMORPHOSIS.md` §H |
| **I** | Test strategy | `03-MIGRATION-TESTING-METAMORPHOSIS.md` §I |
| **J** | Evolutionary Metamorphosis design | `03-MIGRATION-TESTING-METAMORPHOSIS.md` §J |
| **K** | Security / governance boundaries | `04-GOVERNANCE-AND-PLAN.md` §K |
| **L** | Step-by-step implementation plan | `04-GOVERNANCE-AND-PLAN.md` §L |
| **+** | Grounding & corrections — upstream read from source; supersedes tree-level inference | `05-GROUNDING-CORRECTIONS.md` |
| **+** | Comparative analysis (14 points) + conflict resolutions | `06-COMPARATIVE-ANALYSIS.md` |
| **+** | **The unified architecture + complete integration specification** | `07-UNIFIED-ARCHITECTURE-SPECIFICATION.md` |
| **+** | Implementation log — what was actually built, measured, and deviated, per phase | `08-IMPLEMENTATION-LOG.md` |

## Citation convention (mechanically enforced by `tests/test_documentation_integrity.py`)

* An Evo reference is repo-rooted (`evo_agent/runtime.py:1069`) or a bare module name that
  resolves under `evo_agent/`, `tests/`, or `scripts/` (`kernel.py:250`). A line number must be
  within the file.
* An upstream reference is **tagged**: `deerflow:<path>:<line>` is relative to
  `backend/packages/harness/deerflow/` in `bytedance/deer-flow`, and `dsh:<path>:<line>` is
  relative to `packages/` in `deepseek-ai/deepseek-harness`. Untagged paths that do not exist in
  this repository are a defect, because a reader cannot tell whose code is being described - and
  a tagged path that *does* exist here is a defect too, because the tag would hide an Evo bug
  behind an upstream name.
* Markdown tables in this directory must have consistent column counts and no unescaped `|`
  inside a cell, and every `NN-NAME.md` cross-reference must point at a file that exists.

Read order: `00` → `01` → `02` → `03` → `04`, with `05` as the source-grounded correction layer applied across all of them, `06` as the conflict adjudication, and **`07` as the single normative specification that supersedes the earlier documents wherever they differ**.

## The five findings that decide the design

1. **Evo's evolution loop cannot change behaviour.** `SandboxEngine.apply_approved_proposal()` writes only `evolution_config.json`, which **nothing reads**; `PromotionEngine._atomic_switch()` flips `versions/active`, which **nothing loads**; `agent_version` is a constant. Evo currently has a first-class *audit trail for change*, not a mechanism for change. Fix: materialization targets + `active_version.py` (§E.3, Phase 3).
2. **DeepSeek Harness cannot be vendored** — it is TypeScript. Integration is pattern-porting plus an optional sandboxed `PROCESS` backend. `DeerFlow` is Python but drags LangGraph/FastAPI/pydantic into a zero-dependency kernel and contributes a second supervisor loop, which the brief forbids. So: `PORT` the architecture, `BRIDGE`/`PROCESS` the real thing, never as an authority.
3. **The `Verifier` is 37 lines of four string heuristics.** It cannot judge research output, and an unrecognized expectation silently defaults to pass. Sovereign verification must be rebuilt before autonomy widens (§B.5, Phase 5.7).
4. **Runtime tool execution is unsandboxed while evolution experiments are properly sandboxed** — and the shell allowlist is bypassable (`validate_command('python3 evil.py') → allowed`, verified by execution here). The asymmetry must be inverted, not grown (§K.4, Phase 4.7).
5. **Verification should be two layers, and only one of them may say "satisfied".** DeerFlow's receipts are a real zero-LLM layer (150+156 LOC, *message-derived*), and its actual gate is `runtime/goal.py::evaluate_goal_completion` — strict, non-thinking, *"using ONLY the visible conversation evidence"*, fail-closed on `missing_evidence`. Its advertised `judge_enabled` knob is **declared in config and read nowhere** (grep-verified across the whole backend) — so Evo must build receipts + a sovereign hard gate, and skip the phantom. Ported with one deliberate improvement: bind Evo receipts to append-ids, because DeerFlow documents that compaction **renumber**s positional `r1..rN`, letting a pre-compaction `[r3]` resolve to a different call.

6. **DSH's invariants are runtime mechanisms, not test conventions.** 247 `invariant.ts` files; violations **throw** (`InvariantFailure = (message) => never`); checks are prepended so an earlier listener cannot silence them; and a layer with no runtime invariant must state *why*. A pytest file cannot stop a promoted candidate from crossing a boundary; an installed check can.

7. **Capability enablement should be physical.** DeerFlow's `skills/projection.py` (609 LOC) materializes **enabled-only** skill trees into the sandbox filesystem, and its installer defends against zip bombs, member-count bombs, colon/absolute/traversal paths, and executables — with unparseable scanner output ⇒ **block**. Evo's `requires_approval` string-check is a much weaker primitive by comparison.

8. **Memory and architecture version are wired wrong in the hot path**: the kernel retrieves context via a raw `recent_memories()` query instead of the retrieval engine it owns, and `AgentKernel._architecture_version()` returns `""` — so performance cannot be attributed to architecture, which is the exact premise of benchmark-driven promotion (§B.1, §B.10; ~10 lines to fix, Phase 2).

## Deliberate non-goals

No vendoring of either upstream. No second agent loop. No self-modification of source code as a metamorphosis target. No multi-tenant auth. No new persistence authority. `dependencies = []` and offline determinism stay true throughout.

## Implementation status

**`07-UNIFIED-ARCHITECTURE-SPECIFICATION.md` is the normative specification.** The user-approved scope is
the full integration milestone: P0 (ratchet tests + live invariant enforcement), P1 (the three dead links +
documentation integrity), P2 (sovereign→DeerFlow bridge seam, DeepSeek Harness adapters, and the universal
`SandboxProvider` for every executable tool path), P3 (foundational runtime/backend materialisation: the
approved payload becomes files, the sandbox digests them, promotion verifies the digest after the switch, and
the runtime re-resolves every cycle), and **P4 (runtime unification: `BackendRegistry` routes, one loop runs,
the 14-stage pipeline orders, both integrations serve real turns, and `active_version.py` /
`materialization.py` joined the protected set)**. P0–P4 are done and green: **748 tests / 0 failed / 2
skipped** on `python3 -m pytest -o addopts="" -q tests/`, and `verify_sovereign_digest.py --gate` reports
**10/10 invariants ok** over a 20-file protected set. The 2 environmental bwrap failures P2 ended with were
investigated in P3 and are **resolved** - environment-only, no production defect, and the sandbox was not
weakened to get there; the 2 remaining skips are that same bwrap pair. **P5 (capability and verification
completion) is complete**, in two batches, with the second batch recorded as P5b in
`08-IMPLEMENTATION-LOG.md`: the memory-policy and skills capabilities are integrated end to end with real
consumers, `config/prompts.json` / `config/strategy.json` are retired as stated refusals rather than gaps, the
upstream components are pinned with an accepted surface per component, and a new `I-ownership-boundary`
invariant states one owner for each of 23 capabilities. P5b then closed every remaining `07` §8 acceptance
name: plan-mode (`evo_agent/modes.py`, gated on the tool path **and** on the infrastructure/bridge launch
path), MCP policy with a deliberately inert transport (`evo_agent/mcp.py`), delegation depth
(`evo_agent/specialist.py`'s in-flight ledger), adversarial plugin handling (`evo_agent/plugins.py`), the
degradation matrix (`evo_agent/backends/availability.py` and the mediator's honest `isolated` /
`degraded_reason`), memory-scope isolation with an idempotent migration, the CLI surface, and E3's
`test_monotonic_hardening.py`. **976 passed / 2 skipped / 0 failed** (978 collected) on
`python3 -m pytest -o addopts="" -q tests/`, with `verify_sovereign_digest.py --gate` reporting **11/11
invariants ok** over the same 20-file protected set. P6 (the evolution engine) and P7 (metamorphosis as the
controlled lifecycle, never unrestricted self-rewriting) follow, and P5's one recorded wiring gap - the plain
`evo 'goal'` path has no `memory_policy` consumer, though the file is validated for every verb - is listed
under "Blockers carried forward (into P6)".

What P3 delivered that P0–P2 could not: promoting a version now **changes what the agent does**. The
founding finding in `00` §B - an evolution loop whose payload nothing loaded - is closed by
`evo_agent/active_version.py` (the policy table and resolver), `evo_agent/ports/evolution_target.py`
(the fragment shapes, subpath allow-list, mount set, digest rule) and
`evo_agent/materialization.py` (six materializers that refuse rather than repair), wired through
`SandboxEngine.run_experiment(candidate_overlay=…)`, `PromotionEngine._verify_overlay_activated`, and
`AgentRuntime.run_cycle`. `tests/test_metamorphosis_closed_loop.py` is the acceptance test: 1 task per
cycle → activate → 3 → rollback → 1, in one live process, with every step digest-bound.

What P3 did **not** do, on purpose: the runtime still does not route turns through `BackendRegistry`
(loop unification is P4); there is no new approval authority - `candidate_overlay` is bound to the
*experiment*, and the overlay↔approval-CLI binding is deferred; four documents
(`strategy.json`, `heuristics.json`, `memory.json`, `prompts.json`) have schemas and refused writers
until their loaders exist; `active_version.py` is not yet in the protected byte set; and the
`MetamorphosisEngine` façade was left alone. Each is listed with its reason under "Deviations from
`07`" in `08`. No phase is claimed complete unless its acceptance tests pass.
