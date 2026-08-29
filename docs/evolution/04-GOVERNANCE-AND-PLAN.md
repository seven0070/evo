# Security / Governance Boundaries and Step-by-Step Implementation Plan

---

## K. Security and governance boundaries

### K.1 The protected set (authoritatively enumerated)

Immutable by the agent under all operation, including metamorphosis:

| Protected item | Current home | Enforcement today | Enforcement after integration |
|---|---|---|---|
| **Governance** | `metamorphosis.py:236 PROTECTED_CORE`, `orchestrator.py OrchestrationPolicy`, `config/production.example.json` | name string-match | `sovereign/governance.py` + `PROTECTED.manifest` digest; byte-equality check before sandbox (V1) |
| **Permission enforcement** | `security.py` (61 LOC) | allowlist + approval routing | `sovereign/approval_authority.py` sole mint; `authz`-style principal/tool-filter ported from DeerFlow |
| **Approval authority** | `SecurityPolicy.requires_approval` + kernel `approval_callback` | kernel-mediated | approval is a *sovereign* operation; backends cannot observe the callback, only be refused |
| **Verification requirements** | `verifier.py` (37 LOC) | 4 string heuristics | `sovereign/verification_authority.py` + `verification/*` plugins; **plugins may tighten, never loosen** |
| **Audit trail** | `storage.py` `events` (append-only) | convention | `sovereign/audit.py`: append-only API, hash-chained, no update/delete code path exists |
| **Rollback mechanism** | `promotion.py:353 rollback` + `_rollback_after_failure` | works, unused | `sovereign/rollback_authority.py`; public `active_version()`; rollback reachable by operator without any approval |
| **Emergency shutdown** | `runtime.py:873 kill_switch`, `:879 clear_kill_switch` | kill switch not removable via normal ops (verified in docs/tests) | `sovereign/shutdown.py`; every new subsystem must declare and pass a kill-switch gate |
| **Core evolution rules** | `metamorphosis.py` (validate/classify/compat), `sandbox.py` (`PROTECTED_TERMS`, `_validate_structured_target`, `SUPPORTED_TARGETS`), `evolver.py`, `benchmark.py` criteria | partly protected (`metamorphosis/evolver/orchestrator/benchmark` are **not** in `scripts/run_production_gate.py:12-20`) | all four move into the protected manifest; cooldown/serialization in `sovereign/governance.py` |

### K.2 The immutability mechanism — three nested layers

1. **Physical**: files live under `evo_agent/sovereign/` + `config/governance.*`. Installer/loader refuses writes; `workspace_write`/`shell` are confined to the *workspace*, which is not the source root — but note that in a repo-as-workspace deployment (a realistic Evo usage, and how this audit was run) they are the same tree. Therefore:
2. **Authority check**: `ApprovalAuthority` refuses any tool call whose resolved target path intersects the governance root, **before** `SecurityPolicy` path resolution decides anything else, and `MetamorphosisEngine.validate_proposal` refuses any candidate whose diff touches it. Test: `test_sovereign_immutable.py`.
3. **Cryptographic + startup**: `PROTECTED.manifest` sha256 per file, verified at `AgentRuntime.start()`, re-verified after each promotion, and asserted unchanged by the production gate before/after the full run (generalizing `run_production_gate.py`'s existing digest sandwich, which is a good idea worth extending rather than replacing).

Fail-closed at all three: any doubt → refuse to start / refuse to promote, and emit `SOVEREIGN_INTEGRITY_VIOLATION`.

### K.3 Threat model — what actually gets harder after integration

| Threat | Why integration raises it | Control |
|---|---|---|
| **Prompt injection via fetched web content** (new) | G5 gives Evo internet input for the first time | `pipeline/stages/sanitization.py` on every external output; external text is data, never instructions; existing `ExternalContentSafety` + `ExternalTrustLevel` extended; research suite includes an adversarial injected-instruction case (I.3) |
| **Untrusted plugin/skill content** (new) | self-extension accepts capability packages | DeerFlow's model: static + LLM scan, `fail_closed=True`, `enabled=False` default, `tool_policy` permission grant separation, quarantined `candidates/` dir until promoted |
| **MCP tool over-reach** (new) | third-party servers execute real actions | server allowlist, risk floor that can only be raised, output size cap, per-call approval for mutating ops, no ambient credentials (`credentials.py` scoping) |
| **Delegated external harness misuse** (new, DSH/D `PROCESS`) | an external binary does things Evo cannot audit internally | sandbox-first launch, `SpecialistTaskContract` scope, tool allowlist via `tool_filter`, receipts mandatory, verdict owned by Evo; explicit `backends.dsh.enabled=false` default |
| **Second-loop capture** | the stated architectural failure mode | single-loop CI test (§E.0); backends expose `run_turn` only |
| **Self-approval / self-promotion** | autonomy pressure | human approval mandatory for promotion (V5); `specialist.prohibited_actions` already lists `self_approve` — extend the pattern so *no* Evo component can mint its own approval; enforced by making `ApprovalAuthority.mint()` require an operator-verified channel |
| **Benchmark gaming** | promotion depends on the benchmark | hold-out suite never used for proposals; judge model pinned per benchmark version; criteria in signed evidence; `INCONCLUSIVE` blocking |
| **Runaway evolution** | cycles cost and can thrash | existing cooldowns + `max_candidates_per_cycle=3` + `max_concurrent_experiments=1` + `observe` window with auto-rollback |
| **Sandbox escape** | pre-existing, and integration *reduces* it | see K.4 |

### K.4 Pre-existing security findings to fix during integration (found by execution, not inference)

1. **Runtime tools execute on the host.** `ToolRegistry._shell` → `subprocess.run(command, shell=True, cwd=workspace)` (`tools.py:110-118`) with no namespace isolation, while candidate experiments get proper bwrap/unshare isolation. Fix: route **all** tool execution through `SandboxProvider`; `LocalBwrapProvider` already has the recipe; degradation to host execution must require explicit operator config and emit a permanent `SECURITY_DEGRADED` event surfaced in `evo status`.
2. **Allowlist is bypassable.** Verified on this tree: after a MEDIUM-approved `workspace_write` of `evil.py`, `validate_command('python3 evil.py') → (True, 'Command allowed')`. `-c`/`-m` are blocked but file execution is not, and `pytest -p something` (a plugin-loading flag) is also allowed. Fix: treat "interpreters execute files" as HIGH; add a *content*-aware rule (executed script must be inside the task's write-set or explicitly approved), and stop pretending an argv blocklist is a boundary. The namespace sandbox, not the string check, must be the boundary.
3. **`shell=True`** for an argv we already parsed with `shlex.split`. Fix: `shell=False` on the parsed parts — removes a class of parser-differential bugs for free.
4. **Governance-adjacent modules are unprotected by the release gate**: `metamorphosis.py`, `evolver.py`, `orchestrator.py`, `benchmark.py`, `memory.py` are absent from `scripts/run_production_gate.py:12-20`, so a change weakening `validate_proposal` or benchmark criteria does not fail the gate today. Fix: K.1 layer 3.
5. **`_make_readonly` best-effort.** `chmod`-based read-only (`sandbox.py:428`) is advisory for a root-owned or same-uid attacker; the bwrap `--ro-bind` is the real control and should be required, not the fallback, wherever `SandboxProvider` is available.

Clamping rule (from `subagents/config.py` clamps): governance-relevant bounds are clamped in `__post_init__`, never merely validated, so no profile or env var widens them. Evo already protects by enumeration in `scripts/run_production_gate.py:12-20`; §K.2 generalizes the enumeration and clamping protects the knobs.

Items 1–3 are worth fixing **before** M5 widens the tool surface, since M5 adds exactly the capabilities (fetch, MCP, delegation) that make a weak boundary matter.

### K.5 Explicit non-goals (state them so they cannot drift in)

Evo will not, as part of this integration: self-modify its own source code; grant itself approvals; disable or weaken verification, audit, rollback, or shutdown; install packages or plugins from the network autonomously; spawn unbounded agents; run a multi-tenant/multi-user trust boundary; adopt an external framework's loop or persistence as authoritative; or treat either upstream project as a security control (DSH's own `SAFETY.md` forbids that).

---

## L. Step-by-step implementation plan

Each step: small, independently reviewable, ends with `python3 scripts/run_production_gate.py` passing (with the xfail ledger shrinking, never growing), and lands on `arena/01a04937-evo`. Estimates in "focused days", sequential within a phase.

### Phase 0 — Baseline & guardrails (no behaviour change) — 2 days
| # | Task | Files | Done when |
|---|---|---|---|
| 0.1 | Tag `pre-integration`; record pytest counts + digests of all 12 governance-relevant files | `docs/evolution/BASELINE.json` | file committed |
| 0.2 | `scripts/verify_sovereign_digest.py` + `PROTECTED.manifest` (report-only, no enforcement yet) | `scripts/`, `evo_agent/sovereign/PROTECTED.manifest` | prints digests; CI step added, non-blocking |
| 0.3 | Characterisation xfails for the 4 audit defects | `tests/test_integration_acceptance.py` | 4 xfail(strict) exist and are referenced from this doc |
| 0.4 | Fix `run_production_gate.py` PROTECTED list to cover metamorphosis/evolver/orchestrator/benchmark/memory | `scripts/run_production_gate.py` | gate proves these are unchanged by later phases (until intentionally changed with review) |
| 0.5 | `tests/test_architecture_single_loop.py` (assert today's loops are exactly the known ones) | `tests/` | passes; becomes the ratchet for M4/M5 |

### Phase 1 — Sovereign Core extraction — 4 days
1.1 `evo_agent/sovereign/{governance,approval_authority,verification_authority,audit,rollback_authority,shutdown}.py` as thin delegating facades.
1.2 `SecurityPolicy`/`Verifier`/`Checkpoints` re-export from sovereign; keep public import paths intact.
1.3 Startup digest verification, fail-closed, `--allow-sovereign-drift` escape hatch.
1.4 `sovereign/governance.py` owns `PROTECTED_CORE`/`REQUIRED_COMPONENTS` (moved out of `metamorphosis.py:236-238`, which imports them for compatibility) and becomes the sole definition.
1.5 Tests: tamper → refuse start; `test_sovereign_immutable.py`.

### Phase 2 — Close the dead links (highest value per line changed) — 3 days
2.1 Kernel `recent_memories()` → `MemoryManager.retrieve(RetrievalQuery(...))` + empty-DB fallback. *~10 lines, closes G10.*
2.2 Kernel `_architecture_version()` → shared resolution (delete the `return ""`).
2.3 Public `PromotionEngine.active_version()`; fix `orchestrator.py:764,927`.
2.4 Remove 2 xfails; run pilot corpus before/after and record deltas in the PR body.

### Phase 3 — Materialization (the keystone; fixes G2) — 6 days
3.1 `evo_agent/active_version.py` (allow-listed subpaths, repo-default fallback).
3.2 `evo_agent/ports/evolution_target.py` + `metamorphosis/targets/{skill,tool_binding,provider_config,pipeline_stage}.py` (payload schemas + validation).
3.3 `metamorphosis/materializer.py` writes candidate payloads into `versions/candidates/<vid>/`.
3.4 `SandboxEngine.run_experiment(candidate_overlay=…)`; `active_capabilities_digest` on both sides.
3.5 Replace `MetamorphosisEngine.create_structural_candidate()`'s `architecture.json`-only write with real materialization; keep `metamorphosis.py` as a compat façade.
3.6 `tests/test_metamorphosis_closed_loop.py` — **the definition of done**; delete + invert the remaining xfail.
3.7 Post-activation `active_capabilities_digest` health check wired into existing `_rollback_after_failure`.

### Phase 4 — Ports, pipeline, native backend (fixes G1, G12) — 8 days
4.1 `evo_agent/ports/*` incl. `protocol.additive` enforcement and `policy_projection.py`.
4.2 `TurnEngine` port; wrap `RuleBasedAdapter`/`OpenAICompatibleAdapter` as engines; add tool-calling loop to the OpenAI path.
4.3 `evo_agent/pipeline/{engine,stages/*}` — engine resolves order from declared `Placement` + hook axis (PORT from `extension-api/placement.py`) and installs sovereign invariant checks as live aborts; land 8 stages first (`token_budget`, `loop_guard`, `repeat_guard`, `read_before_write`, `sanitization`, `tool_output_budget`, `policy_filter`, `error_handling`), each with its invariant file.
4.4 `backends/{native,availability}.py`; `ExecutionBackend` protocol with **no `success` field** on `TurnResult`.
4.5 `context.py` compaction + `spill` (port DSH idea) for large tool outputs.
4.6 `config.agent.loop = legacy|native` seam; `docs/CLI.md` documents it as temporary.
4.7 **Security in this phase**: `sandbox_providers/{base,local_bwrap,unshare}.py` extracted from `SandboxEngine`, and `ToolRegistry` execution routed through it; `shell=False`; K.4 items 1–3 fixed here.

### Phase 5 — Capabilities (fixes G3, G4, G5, G6) — 10 days
5.1 `skills/{frontmatter,parser,catalog,projection,validation}`; seed `capabilities/skills/builtin/` from the 29 upstream `skills/public/*` packages (`deep-research`, `data-analysis`, `chart-visualization`, `academic-paper-review`, `code-documentation`, `find-skills` first). **`projection.py` is not optional**: enablement changes the sandbox **mount set** (enabled-only trees), per DeerFlow — not merely a boolean.
5.2 Skills **hardening trio** (the real work, not 5.1): `installer.py` (traversal + colon + absolute rejection, resolve-then-`is_relative_to`, zip-bomb **and** member-count limits, executable-binary refusal), `security_scanner.py` (fail-closed default True when config unavailable; **unparseable verdict → block**; fail-open degrades only non-executable content to warn), `permissions`/`tool_policy`/`acquisition` with `enabled=False` + `security_fail_closed=True`. Plus `required-secrets` + **`secrets-autonomous`** in Evo's skill model and a matching `RiskLevel` floor in `SecurityPolicy` for credential use without a live approving turn (Evo has `IntegrationCredentialMetadata` but no autonomy dimension). Adversarial skill fixtures.
5.3 `evo_agent/hooks/` (minimal: pre/post tool, pre/post exec, on-verify) — needed before MCP so MCP servers are hook-visible.
5.4 `mcp/{config,registry,policy,session_pool,transport_stdio,tool_adapter}.py`; caps before transport; `optional-dependencies.mcp`.
5.5 `research/{provider,fetch_stdlib,provenance,report}.py` — zero-dep fetch + cited markdown report; closes the phantom `web_research` capability that `metamorphosis.py`'s bootstrap already declares (`capability_specs` includes `web_research` with no provider behind it).
5.6 `delegation/{engine,limits,executors/llm}` over `specialist.py` — real executors; `limits.py` in the same commit as the first executor.
5.7 `verification/{receipts,checklist,authority_hook}.py` + `VerifierPlugin`: receipts derived from the append-only record and bound to **append-ids** (never positional), `satisfied` owned solely by `sovereign/verification_authority.py` with DeerFlow's fail-closed semantics (`missing_evidence → not satisfied`) + blocker taxonomy; **`judge.py` deferred** — upstream's `judge_enabled` knob is declared and never read (05 §1.1); fix the verifier's silent-default-open case; add `test_sovereign_vocabulary.py` and `test_capability_exposure_matches_execution_decision.py`.
5.8 `plugins/{inventory,loader,lifecycle}.py` (DSH shape) + adversarial plugin fixtures (`throws`, `late_service`, `self_dispose`, `missing_dependency`, `bid_out_risk`, `privilege_escalation`).
5.9 `backends/lead_agent.py` [BRIDGE, extra `deerflow`] and `backends/dsh.py` [PROCESS] behind `availability.py`; both inert by default; `subagent-claude-code`-style receipts mandatory.

### Phase 6 — Evolution completion + benchmark v2 (fixes G8) — 6 days
6.1 `benchmark` corpus v2 per I.3; keep `TaskCase`; deprecate the `evolution_config.json`-existence probes; add `hold-out` suite + seeded trials + variance reporting.
6.2 `metamorphosis/operator.py` — the full `undergo` cycle, incl. `dry-run`, `INCONCLUSIVE`-blocks-promotion test, cooldown integration.
6.3 `EvolutionOrchestrator` gains the `metamorphosis` work-item kind; `METAMORPHOSIS_REQUEST` intent classification; **not self-approvable**.
6.4 `evo_agent/invariants/registry.py` complete + one test file per invariant.
6.5 Metamorphosis stress test (100 cycles) + degradation matrix + safe-mode coverage tests + `test_invariant_coverage_matrix.py` (every layer: a live check or a stated reason).
6.6 `profiles/{minimal,local,research,full}.json`; `serve/` (loopback JSON-RPC, token-gated) + `evo <noun> <verb>` subcommands with legacy-parity test + desktop bridge growth to ~14 commands + `web/` RPC client wiring.

### Phase 7 — Release — 2 days
7.1 `evo.toml`/`config/governance.json` finalized; `docs/{GOVERNANCE,SKILLS,MCP,DELEGATION,METAMORPHOSIS,SECURITY}.md`.
7.2 `ARCHITECTURE.md` rewritten to describe what *is* (the doc-vs-source gaps in `00-AUDIT.md` §B.13 must be closed by fixing the doc).
7.3 `CHANGELOG.md` 2.0.0 entry + version bump; `README.md` capability table; winget manifest refresh path unchanged.
7.4 Gate → nightly benchmark soak → tag `2.0.0`.

**Total: ~41 focused days.** Phase order is dependency-forced: 3 before 5 (a skill needs a materialization target to be promotable), 4.7 before 5.5 (isolation before network/delegation), 2 before everything (so experience data is meaningful while measuring later phases).

### Standing acceptance criteria (all phases)
- `dependencies = []` still true in the base install; `pytest -q` still deterministic and offline.
- Every existing public import path from `evo_agent/` keeps working (compat façades, not renames).
- Production gate green; xfail ledger strictly decreasing.
- Zero duplicate owners for anything in the §3 ("Where both upstreams say the same thing") converge-once table.
- No second agent loop; no new authority outside `sovereign/`.
- Every new capability inert when `safe_mode` or `kill_switch` is active (asserted, not reviewed).
