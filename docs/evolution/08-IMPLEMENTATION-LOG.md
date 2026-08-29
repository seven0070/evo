# Implementation Log — P0 to P2 (foundational phases)

Normative source: `07-UNIFIED-ARCHITECTURE-SPECIFICATION.md`. Approved decisions on the record:
**Q2 = ship the DeerFlow lead-agent bridge** (not only the seam), **Q4 = sandbox all tool
execution**. This log records what was actually built, what deviates from the spec and why, and
the measured state of the tests. It is the audit trail for the phases; the design rationale
stays in `07`.

Phase numbering follows the user's instruction (P0 ratchet + invariants, P1 dead links +
documentation integrity, P2 foundational integration seams). Where this differs from `07` §8,
the mapping is: user P0 = `07` P0; user P1 = `07` P2 (dead links); user P2 = `07` P1 (sovereign
boundary) + `07` P4 (isolation) + the bridge/adapter seams of `07` P5, cut down to seams only.

---

## P0 — Ratchet tests and invariant enforcement

**Goal.** Before anything is integrated, Evo must be able to prove that the parts of itself
that decide what is allowed have not changed, and every guarantee the later phases depend on
must be executable rather than documented.

### Built

| File | What it is |
|---|---|
| `evo_agent/sovereign/__init__.py` | New package: the authorities that gate autonomy. Nothing was moved out of its existing module; what is here is the *definition of what must not change* plus the checks. |
| `evo_agent/sovereign/protected.py` | Single source of truth for the protected byte set (16 files), SHA-256 digests, `verify()`, `write_manifest()`, `enforce()` and `SovereignDrift`. |
| `evo_agent/sovereign/sovereign.manifest.json` | The published digests. Regenerated only by an explicit, reviewed `--write`. |
| `evo_agent/sovereign/invariants.py` | Live invariant registry: 9 checks, `InvariantError` that raises, `NO_RUNTIME_INVARIANT` opt-out that requires a reason, `InvariantConfig` allow/block lists with non-blockable cores, prepended `InvariantObserver`, and a **shrink-only ratchet** for tolerated defects (`known_gaps`). |
| `evo_agent/sovereign/eligibility.py` | Metamorphosis eligibility table: 13 target kinds (8 that `SandboxEngine` accepts today + 5 planned), 12 protected components each with a stated reason and an enforcement mechanism, `FORBIDDEN_PAYLOADS` (no source-code target exists), `MONOTONIC_FIELDS`, `validate_registry()`, `consistency_with_sandbox()`. |
| `scripts/verify_sovereign_digest.py` | `--write` / `--report` / `--invariants` / `--gate` / `--json`. Loads `sovereign/protected.py` **by path, not through `evo_agent`**, so the recovery tool does not have to trust the package it is checking. |
| `evo_agent/runtime.py` | `AgentRuntime.start()` now calls `_validate_sovereign_boundary()` before any state mutation; added `sovereign_report()`. New events emitted: `sovereign_verified`, `sovereign_drift_detected`. |
| `evo_agent/models.py` | 5 `EventType` values appended (`sovereign_verified`, `sovereign_drift_detected`, `invariant_violation`, `security_degraded`, `runtime_backend_selected`). No existing value reused or renumbered. |
| `scripts/run_production_gate.py` | The gate's 7-file hardcoded list is replaced by the manifest (16 files), it now fails if the tree does not match the *published* digests before **and** after the run, adds a `sovereign_invariants` step, and the clean-venv step asserts the manifest survived packaging. |
| `pyproject.toml` | `package-data` for the manifest (an installed agent that cannot verify itself has no protection); `xfail_strict = true`. |
| `tests/test_sovereign_invariants.py` | 37 tests: manifest coverage, tamper/deletion/absence detection, every detector proven able to fail, blocklist cannot silence core checks, prepended observers, startup enforcement, gate/script wiring. |
| `tests/test_metamorphosis_eligibility.py` | 23 tests: registry self-consistency, agreement with `SandboxEngine`, "nothing is loadable yet", protected-in-name-and-substring, memory-contents-vs-policy split. |
| `tests/test_audit_defects_characterisation.py` | The xfail ledger: 5 strict xfailed characterisation tests, one per known defect, each annotated with the phase that must delete it. |

### Invariants now enforced at runtime (cheap set, run on every `start()`)

```
I-sovereign-digest    R1  protected byte set matches its published manifest     (fatal)
I-sovereign-coverage  R1  every protected component's owning module is protected (fatal)
```

and in CI / at phase boundaries (full set):

```
I-import-purity  R4  no module-level third-party import; extras are function-local
I-sync-contract  R2  no async syntax outside serve/ — ports stay synchronous by contract
I-single-loop    R2  exactly one tool-dispatch loop (kernel.py::run); no loop in any adapter
I-exec-isolation S1  every spawn site allow-listed and free of shell=True (1 tolerated gap)
I-persistence-authority R3  SQLiteStore remains the only persistence authority
I-eligibility-coherence R10 promotable kinds are benchmarked and match SandboxEngine
I-invariant-coverage    R9  every subpackage is guarded or carries a stated reason
```

### Measured

* `python3 -m pytest -o addopts="" -q` → **413 passed, 5 xfailed, 2 failed** (420 collected).
  The 2 failures are the pre-existing environmental ones: `tests/test_sandbox.py` bwrap cases,
  which need the setuid helper that CI installs (`chmod 4755`) and this sandbox cannot provide.
  Baseline before P0 was 353 passed / 2 failed; **P0 added 65 tests and fixed nothing that was
  passing**.
* `python3 scripts/verify_sovereign_digest.py --gate` → all 9 checks ok, exit 0.
* Fail-closed behaviour proven twice in practice while writing this: editing `runtime.py`
  and `sovereign/invariants.py` made `AgentRuntime.start()` **refuse to start** until the
  manifest was deliberately re-published. That is the intended workflow, not an accident.

### Deviations from `07` (each recorded with a reason, per the header rule)

1. **`PROTECTED_PATHS` is 16, not 12.** `07` §8 counted the historical 7 plus 5 governance
   modules; `models.py` and `pyproject.toml` were left out because they are contract-shaped
   rather than authority-shaped, and `sovereign/*` (4 files) was *added* instead. Net effect:
   strictly more protection than the spec, and the checker protects itself.
2. **The `I-exec-isolation` check tolerates one recorded gap** (`tools.py:shell`). The spec
   assumed isolation was already fixed by P4; the user's phase order puts the fix in P2, so the
   ratchet records the defect, and the ledger entry becomes a *failure* the moment the defect is
   fixed without the entry being removed. The ratchet cannot be satisfied by doing nothing.
3. **`07` §5's 126/127-table data model and the `pipeline/` stage set are not built here** —
   they belong to later phases. P0 adds no tables and no migration.

### P0 exit criteria

| Criterion | State |
|---|---|
| Protected set defined once, consumed by runtime + gate + tests | done |
| Startup verification fails closed and is auditable | done |
| Every invariant executable, not descriptive | done (9 checks, each with a proven failure mode) |
| xfail ledger registered, strictly shrinking, referenced from the plan | done (5 entries) |
| No behaviour change for existing users beyond the (intended) startup check | done — 413 previously-passing tests still pass |
| `dependencies = []`, 0 `async def`, offline determinism | unchanged, and now *enforced* by `I-import-purity` / `I-sync-contract` |

### Blockers carried forward

* **None blocking P1.** Two are worth noting for P2: the tolerated `tools.py:shell` gap must be
  removed *in the same change* that sandboxes tool execution, and `LOOP_FORBIDDEN_PACKAGES`
  already names `backends/`, `ports/`, `sandbox_providers/` — so P2 must add a covering
  invariant for each new subpackage or state a reason, or `I-invariant-coverage` fails.

---

## P1 — Three dead links closed, documentation integrity

**Goal.** The three wiring defects that made measurement meaningless (memory not consulted at
plan time, architecture attribution empty on the kernel path, "what is active" reachable only
privately), plus the doc surface that a reviewer trusts.

### Built / changed

| File | Change |
|---|---|
| `evo_agent/kernel.py` | Owns **one** `MemoryManager` for its store (and hands the same instance to `external_integrations` instead of letting it build a second one). New `_plan_time_memories(goal)` retrieves through `RetrievalEngine` with `RetrievalQuery(goal, agent_version, architecture_version, max_memories=6, min_confidence=0.25)`; `PLAN_CREATED` now carries `memory_provenance` and a new `MEMORY_RETRIEVED` event is recorded. `_architecture_version()` no longer returns `""`. |
| `evo_agent/sovereign/architecture.py` | New: the single resolution of `architecture_version` for both loop paths, with a **content-addressed fallback** (`arch-unregistered:<version>:<sha256(protected digests)[:12]`) so the value is never empty and *changes exactly when the protected core changes*. |
| `evo_agent/runtime.py` | `_architecture_version()` delegates to the shared resolver, reusing its existing `MetamorphosisEngine`. Startup boundary check now honours one developer override (below). |
| `evo_agent/promotion.py` | `active_version()` is public and is the definition; `_active_version()` remains as a delegating alias so nothing else had to change. |
| `evo_agent/orchestrator.py` | Both private reaches (`_active_version()`) replaced with the public accessor. |
| `evo_agent/models.py` | `MEMORY_RETRIEVED`, `SOVEREIGN_DRIFT_ACCEPTED` appended. |
| `evo_agent/sovereign/protected.py` | **`PROTECTED_PATHS` now = explicit authorities ∪ every `sovereign/*.py`.** A new guard module is protected the moment it exists; protection can no longer be dodged by adding a file to the package that is supposed to be immune to editing. 16 → 17 files. |
| `ARCHITECTURE.md` | Added "Implemented tool surface (authoritative list)": the 4 registered tools with risk/approval/confinement, and an explicit statement that `web_research`, `report_generation`, `text_processing`, `multimedia_generation` are capability records **without an executable provider**. The document had no tool enumeration at all, which is how "describes an outcome" got read as "can do the thing". |
| `docs/evolution/README.md` | Status line corrected (analysis → implementation began at P0), `08` indexed, citation convention documented. |
| `docs/evolution/{04,05,06}` | 27 upstream citations that looked like Evo paths (e.g. `deerflow:config/verification_config.py:19`) are now tagged `deerflow:` / `dsh:`; one Evo ref that the mechanical pass had mis-tagged was restored to `scripts/run_production_gate.py:12-20`. |
| `tests/test_dead_links_closed.py` | 13 tests: retrieval-engine provenance, legacy-`memories` fallback (existing installs must not silently lose context), retrieval failure degrades *and records*, per-instance caching, kernel == runtime architecture version, no cross-module private reach, and a guard that P1 did **not** claim P2/P4's defects. |
| `tests/test_documentation_integrity.py` | 40 tests: every `path:line` resolves and is in range; unresolvable refs must be tagged and tagged refs must *not* exist locally; tables well-formed per contiguous block, fence-aware; design-doc cross-refs exist; the ARCHITECTURE tool table equals `ToolRegistry`; every backticked `--flag` in user docs exists in the parser (with a coverage floor so the check cannot go vacuous); the developer override must leave an audit trail. |
| `tests/test_audit_defects_characterisation.py` | The 3 repaired entries **deleted from the xfail ledger** (not marked pass), leaving exactly 2 open defects with named phases. |

### Deliberate design choices worth reviewing

* **Fallback, not removal.** `_plan_time_memories()` falls back to `store.recent_memories()` when
  the governed store has nothing to rank, and on retrieval failure. Deleting the legacy path would
  have silently changed behaviour for installs that only ever used `save_memory`; the fallback is
  reported in `memory_provenance.source`, so a reader can tell which path produced the context.
* **The content-addressed fallback.** A kernel on a fresh workspace has no registered
  architecture manifest, so the honest options were "empty string" (the defect) or a claim to a
  precision that does not exist. Content-addressing the protected byte set is a real answer to
  "which architecture is this", says "unregistered" in its own prefix, and is stable across runs.
* **`SOVEREIGN_DRIFT_ACCEPTED`.** While P1 was in progress, editing `runtime.py`/`kernel.py` made
  every `AgentRuntime.start()` raise — the P0 check working as designed, but it means a developer
  touching the protected set cannot run the suite until the manifest is re-published. The escape
  hatch now exists exactly as `03` §M1 specified: read from the **environment** (`EVO_ALLOW_SOVEREIGN_DRIFT`),
  never from `evo.toml` (a config file is writable by things the agent is asked to fix), prints a
  warning, emits `sovereign_drift_detected` **and** `sovereign_drift_accepted`, sets
  `drift_accepted: true` in the runtime record, and — asserted by test — never emits
  `sovereign_verified`. Normal workflow stays "finish phase, `--write`, run suite", so the
  re-publication *is* the review act.
* **Two `PLAN_CREATED` events.** The kernel records plan-time context and then the plan itself
  under the same type. Not unified here (out of P1's scope, and consumers exist); the test helper
  addresses it explicitly by requiring a key rather than an index, so the ambiguity is visible in
  the code that depends on it.

### Measured

* `python3 -m pytest -o addopts="" -q` → **466 passed, 2 xfailed, 2 failed** in 258s
  (470 collected; P0 ended at 413 passed / 5 xfailed / 2 failed of 420). P1 added 50 tests and
  converted 3 ledger entries into passing tests. The 2 failures are the same pre-existing environmental bwrap
  cases (`tests/test_sandbox.py`), unchanged from the pre-work baseline.
* `python3 scripts/verify_sovereign_digest.py --gate` → 9/9 invariants ok; protected set 17 files.
* No new tables, no migration, no config file, no new dependency, still 0 `async def`, still
  `dependencies = []`.

### Deviations from `07`

1. `07` §8 P2 also listed the **memory-table consolidation migration** (fold the legacy `memories`
   table into `memory_records`, add `scope_key`). **Deferred**: the read path is now behind
   `MemoryManager` (the actual defect), while the migration is a storage-schema change with real
   data-loss risk that `07` itself schedules alongside P4's isolation work. Doing it here would
   have mixed a storage migration into a 3-line hot-path repair.
2. `07` §8 P2 said "`_architecture_version()` real"; implemented as a *shared resolver* (per `03`
   §M2) rather than a kernel-local copy, which is a slightly larger change and the reason
   `sovereign/architecture.py` exists.
3. `I-exec-isolation`'s tolerated gap (`tools.py:shell`) is **still open by design** — it is P2's
   deliverable, and the ratchet will fail the build if P2 fixes the defect without removing the
   entry.

### Blockers carried forward

* None blocking P2. P2 must, by the registry's own rules: (a) remove the `tools.py` entry from
  `EXECUTION_SITE_ALLOWLIST` and its `known_gaps` when tool execution moves behind a provider;
  (b) register a covering invariant (or a stated reason) for each new subpackage
  `ports/`, `backends/`, `sandbox_providers/`, or `I-invariant-coverage` fails; (c) keep
  `LOOP_FORBIDDEN_PACKAGES` honest — no adapter may own a loop.

---

## P2 — Sovereign→external bridge seam, universal execution isolation, DSH adapters

**Goal.** Close the sharpest asymmetry the audit found (candidates confined, the runtime's own
tools on the host), and make integration of DeerFlow / DeepSeek Harness possible *without* making
it possible for them to become second agents. Q4 and Q2 are this phase's two approved decisions.

### Built

| File | Change |
|---|---|
| `evo_agent/ports/contracts.py` | New: the only shapes an integrated runtime may speak through. Dataclasses `CapabilityRequest`, `BackendPlan`, `BackendAvailability`, `Receipt`, `TurnContext`, `TurnDecision`, `ArtifactRef`, `TurnResult`, `ExecRequest`, `ExecResult`, `ProviderAvailability`; Protocols `SandboxProvider`, `ExecutionBackend`, `TurnEngine`, `EventSink`, `VerifierPlugin`. Every port is `@additive`, which is checkable rather than aspirational: `required_members` / `optional_members` read the class body, so a member with a default body may be omitted by an adapter written before it existed and a stub body is an obligation. `TurnResult` has **no `success` field at all**. |
| `evo_agent/sandbox_providers/` | New package, and now the only place in `evo_agent` that starts a process: `base.py` (child-environment sanitiser that *drops* secret-shaped variables and reports the drop, process-group termination, output bounding, `ConfinedLaunch`), `local_bwrap.py`, `unshare.py`, `host.py` (the unconfined fallback, named and refused by default), `registry.py` (`probe_all`, `select`, `run_confined`, `prepare_launch`). |
| `evo_agent/sovereign/mediation.py` | New `ApprovalMediator`: policy rules → approval evidence → isolation, in that order, in one place. `evaluate` (no record) and `authorize`/`execute` (recorded) share a single `_decide`, so the two cannot drift. `MediationDecision.rule` distinguishes `policy` / `unapproved` / `no_isolation` / `policy_error` / `infrastructure_argv_mismatch` / `empty_request`, because "denied" alone invites relaxing the wrong thing. `grant_approval` is the bridge-facing path. |
| `evo_agent/backends/` | New package: `availability.py` (tri-state available/degraded/unavailable, worst-wins merge), `registry.py` (registration-time port validation, provenance gate, declared selection with `plan()`, disabled/unknown backends refuse rather than fall back), `native.py` (Evo's own loop expressed as a backend: accounting, no loop), `lead_agent.py` + `lead_agent_driver.py` (the Q2 bridge), `dsh.py` (the process adapter, off by default). |
| `evo_agent/security.py` | Added `max_output_bytes`, `sandbox_enforcement` (`auto`/`strict`/`degrade`/`off`, unknown → `strict`), `sandbox_provider`, `source_read_only`, `sandbox_read_only_paths`; ceilings clamped in `__post_init__`; `to_dict()` so a run can state its own confinement level. |
| `evo_agent/tools.py` | `_shell` no longer spawns. It tokenises, builds an `ExecRequest`, and asks the mediator; `subprocess` is gone from the module. `ToolRegistry` gained `approver` / `on_event` / `mediator`, and its refusal path returns the policy reason verbatim so existing model-facing text did not change. |
| `evo_agent/kernel.py` | Wires `on_event=self._record_isolation_event` into the tool registry: `security_degraded` / `isolation_unavailable` are appended to the store under the `isolation` bucket, never raising. |
| `evo_agent/sovereign/invariants.py` | `I-exec-isolation`: `tools.py` entry deleted, its `known_gaps` tolerance deleted (the P0 ratchet fired on schedule), the isolation layer + the bridge child added as the sanctioned spawn sites. New `ADAPTER_LOOP_BUDGETS`: the "no loops in adapters" ban stays, and each exception must declare what bounds it, with stale entries failing. New `I-ports-contract`: the seam packages may not import an authority (**including relative imports**, which `_imports` ignores and this check does not), may not own a store, may not pass `shell=`, and must match their port *by signature arity*. 9 → 10 checks. |
| `tests/test_sandbox_providers.py` | 26 tests: secret dropping, selection order, strict refusal vs recorded degrade, `auto` refusing on a platform that should have namespaces, malformed-request rejection, workspace-writable/source-read-only, TMPDIR placement, timeout kill, truncation visible, `prepare_launch`, and **flag parity with `SandboxEngine._isolated_command` on both of its branches** (the engine is forced onto its bwrap branch by a monkeypatched probe so both are compared on a machine with neither). |
| `tests/test_backend_seams.py` | 47 tests: port obligations as data, partial backend rejected at registration with the missing member named, additive members omittable, receipt digests derived from exact bytes and insensitive to key order, mediator rules, the bridge's full round trip against a fake driver child, `verdict`/`satisfied` stripped at the boundary, oversized child line abort, malformed `tool_request` producing no receipt, dsh template guards and `InvariantFailure` handling, registry provenance gate, and **two negative tests proving `I-ports-contract` and the loop-budget ratchet can fail**. |
| `tests/test_mvp.py` | `approved=True` added to the shell call (the mediator now requires approval *evidence*), plus a new test asserting a HIGH-risk call without evidence is refused. |
| `tests/test_audit_defects_characterisation.py` | The isolation entry **deleted and inverted into positive tests** — see deviation 4. |

### Deliberate design choices worth reviewing

* **Default `auto`, not `strict`.** Every executable path now goes through a provider, but when
  nothing is usable `auto` degrades *with a `SECURITY_DEGRADED` event* on a platform that has no
  namespace support at all, and **refuses** on one that should. A blanket "always confine" default
  would have made the suite fail on Windows runners and on locked-down containers, and the
  predictable response to that is deleting the check. `strict` is one policy field away and is what
  a production run should use.
* **Read-only source tree.** The confined child gets Evo's own package directory mounted read-only,
  which turns "self-modification goes through staging, proposal, benchmark, promotion" from a
  convention into a filesystem property. Stated cost: a self-hosting run whose workspace *is* the
  checkout loses that mount (enforcing it would make the task impossible), and `read_only_roots`
  says so rather than silently skipping it.
* **No shell anywhere in the tool path.** `ExecRequest.argv` must be a sequence — a command-line
  string is rejected by the dataclass, not by a pattern list. The `unshare` provider's mount script
  is handed to `sh -c` as argv with paths in positional parameters, so a workspace path containing
  a quote cannot become code.
* **`EVO_MOUNT_FAILURE` + exit 97.** The first version inferred "the namespace failed to set up"
  from whatever the payload printed; that turned a legitimate read-only *denial* (`EROFS` seen by the
  command) into a false "refused to run" and lost the distinction between "blocked" and "failed". The
  script now signals mount failure with a sentinel and a dedicated exit code.
* **The bridge pump reads bytes, not lines.** With `select()` gating `TextIOWrapper.readline()`, a
  child that sent an event and a tool request in one burst deadlocked: the wrapper buffered the second
  line, the socket had nothing left to report, and the parent waited out the whole 60 s while the
  child waited for a tool response. That is why `_pump` does its own splitting on `os.read`, why
  replies go out with `os.write` on the same layer, and why the child's stderr is folded into
  stdout (an unread 64 KiB of traceback would block the writer). The symptom was 249 s for a
  47-test file; the fixed version takes 1.98 s.
* **Infrastructure launches are mediated by identity.** A bridge's own driver is not a
  model-chosen command, so the command allowlist would reject it for the same reason it rejects
  `cat /etc/passwd`. `authorize_infrastructure` replaces the allowlist with something stricter for
  this case: the request must name the configured program and nothing else. `dsh` additionally
  re-renders its argv back against the configured template, so a smuggled flag fails the comparison
  instead of being executed.
* **A bridge cannot grant itself anything.** `verdict`, `satisfied`, `approved`,
  `promotion_allowed` are stripped from every child message and recorded as
  `bridge_overreach_rejected`; `TurnResult` cannot carry a success flag because the field does not
  exist; child `tool_request`s are executed by the mediator, and a refusal is *sent back to the
  child* rather than dropped, so its transcript shows a refusal instead of a parent-side exception.
* **DSH's invariants kept as fatal-by-reporting.** The upstream treats an invariant violation as an
  unrecoverable error in its own process; the adapter reproduces the only part that survives a
  process boundary — an `InvariantFailure` line fails the turn **even when the exit code is 0**.

### Measured

* `python3 -m pytest -o addopts="" -q tests/` → **539 passed, 1 skipped, 1 xfailed, 2 failed** in
  253 s (543 collected; P1 ended at 466/1 skipped-equivalent of 470). P2 added 74 tests
  (47 seams + 26 providers + 1 in `test_mvp.py`) and deleted 1 xfail ledger entry, so
  470 + 74 − 1 = 543 accounts for the change exactly. The 2 failures are the same pre-existing
  environmental bwrap cases in `tests/test_sandbox.py`; the 1 skip is P2's own bwrap-branch parity
  test declining to assert on a machine with no bwrap.
* Per-file: `test_documentation_integrity.py` 40, `test_dead_links_closed.py` 13,
  `test_sovereign_invariants.py` 37, `test_metamorphosis_eligibility.py` 23,
  `test_audit_defects_characterisation.py` 1 (the P4 verifier defect), `test_backend_seams.py` 47,
  `test_sandbox_providers.py` 26.
* `python3 scripts/verify_sovereign_digest.py --gate` → **10/10 invariants ok**, protected set
  17 → **18 files** (`sovereign/mediation.py` joins it automatically: `PROTECTED_PATHS` = explicit
  set ∪ every `sovereign/*.py`). Re-publication was required for `security.py`,
  `sovereign/invariants.py` and `kernel.py` — three protected files changed, which is exactly what
  the digest check is for, and the full suite was run again with **no** `EVO_ALLOW_SOVEREIGN_DRIFT`
  set to prove the tree matches the published manifest.
* Environment, not code: `bwrap` is absent here, `unshare --user --map-root-user --mount --net
  --pid --fork --mount-proc` works (~9 ms), and the sandbox's system interpreter had lost its
  `pytest`, which is what made candidate-sandbox tests fail until it was reinstalled. Worth recording
  because `PYTHONNOUSERSITE=1` in the child environment means a `--user` install is **invisible
  inside the sandbox** — a real property of the isolation layer, and a debugging trap.

### Deviations from `07`

1. **`allow_host_execution` was not added as a second knob.** `07` §7 lists it beside
   `sandbox_enforcement`; two settings that can contradict each other ("host allowed" + "strict")
   would need precedence rules, and precedence rules in security config are where policy goes to
   die. The intent is carried by `sandbox_enforcement` alone: `strict` = never host, `auto`/`degrade`
   = the recorded fallback, and the `host` provider's own `permitted=False` default is the mechanical
   form of "false" for anyone constructing providers directly.
2. **No `evo backends` command and no `[backends.*]` parsing.** The registry, the tri-state report,
   and `build_default_registry(config=...)` exist; the CLI and `evo.toml` plumbing are deliberately
   not in P2 — and every user-facing string that would have named a config key nothing reads was
   rewritten to name the constructor instead. `tests/test_documentation_integrity.py` is why the
   prose cannot drift ahead of the parser.
3. **The runtime still does not route turns through `BackendRegistry`.** `backends` is in
   `LOOP_FORBIDDEN_PACKAGES`, and P2's contract was seams + isolation, not a rewired loop: Q2's
   "ship the bridge" is delivered as a working, tested, mediated bridge whose output can be routed,
   plus the selection record (`plan()`) that makes routing reviewable. Making it the *default* path
   is P4's loop unification.
4. **The P0 xfail that demanded `python3 evil.py` be denied was deleted, not satisfied.** Per `07`
   S2, an interpreter running a file inside the task's own write-set is **allowed but confined**; the
   assertion that replaces it is that the write outside the workspace is denied and the run is
   marked `isolated=True` with the provider recorded. An allowlist that tries to *be* the boundary is
   what produced the original defect.
5. **`ExecRequest` clamps a non-positive timeout instead of rejecting it.** R6's ceiling rule beats
   `07`'s wording here: the value comes from a policy a user may edit, and "0 means forever" is the
   failure this field exists to prevent.
6. **No provider grants network egress.** Requests with `network=True` are refused *before* a
   namespace is built, with a message saying so. `07` schedules egress behind the policy gate; a
   provider that quietly ignored the flag would make "denied" and "not granted" indistinguishable in
   the log, which is the worse outcome.
7. **`requires-python` is unchanged.** Q1 stays open on purpose: because the bridge is a *process*
   boundary over a venv interpreter, DeerFlow's `>=3.12` floor never enters Evo's base install, and
   `dependencies = []` survives (`I-import-purity` ok).

## P3 — Foundational runtime/backend materialisation (the loop can now change behaviour)

`07` §8 line 309 and `04` §Phase 3. This phase closes founding finding (1) from `00`/`06`: an approved
proposal produced a benchmark verdict about a payload nothing loaded, so promotion was a bookkeeping
event. The spine is now *causal* - activating a version changes what the next cycle does, and rolling it
back changes it back - and the change is falsifiable, because one digest rule is computed once and
re-read by three components.

Evolutionary Metamorphosis was **not** implemented in this phase (explicit instruction): no new
autonomous loop, no unrestricted self-modification, no rewiring of the metamorphosis façade (item 3.5
below), and no unrelated refactoring.

### Built

* **3.1 `evo_agent/active_version.py`** (new, 637 lines). `DOCUMENTS` is the policy table - one row per
  overlay document, with `Field` kinds (`int`, `str`, `list_name`, `map_int`, `doc`), bounds, allow-lists,
  and a `loadable` bit naming the loader (`evo_agent.runtime:AgentRuntime.run_cycle`).
  `resolve()` reads only a directory named `overlay` under `versions/active`; `apply_overlays()` is the
  merge over shipped defaults described below; `write_activation_record()` / `verify_activation()` put a
  digest beside the version so "what is running" is a checkable claim rather than a hope;
  `overlay_digest()` is the single digest rule (schema-version-tagged, order-insensitive, content-only).
* **3.2 `evo_agent/ports/evolution_target.py`** (new, 335 lines) + **`evo_agent/materialization.py`**
  (new, 446 lines). The port holds the *shapes* and no policy: `OverlayFragment` (whose `__post_init__`
  makes a source-shaped or escaping path unrepresentable), `ALLOWED_SUBPATHS`,
  `relpath_is_allow_listed`, `MountSet`, `overlay_digest`, `verify_fragment_tree`, and the
  `Materializer` protocol. `materialization.py` holds six materializers - `strategy_params`,
  `pipeline_stage`, `tool_binding`, `provider_config`, `memory_policy`, `skill` - sharing one
  `validate` against `DOCUMENTS`, plus `_loader_gate`, which is where "no loader → no materialization"
  lives. `registry_problems()` is the self-check that keeps the materializer table, the document table,
  and the engine's accepted names from drifting apart.
* **3.3 Staging.** The overlay is written *inside* the candidate/version directory
  (`versions/<id>/overlay/config/…`), so `_stage_candidate`, `_manifest_hash`, `_copy_tree`,
  `_atomic_switch` and rollback carry it with no new state and no new manifest format. A version
  directory stays immutable and read-only; the activation record deliberately lives *beside* it.
* **3.4 Sandbox.** `SandboxEngine.run_experiment(proposal_id, command, retain_sandbox,
  candidate_overlay=…)` materializes the payload after `apply_approved_proposal`, raises on a refusal
  (the experiment is recorded `ABORTED` with the reason rather than "passing" with an empty candidate),
  then digests baseline and candidate from **the files** and stores the pair under
  `resource_information["overlay"]` with `OVERLAY_RESOLVED` + `ACTIVE_CAPABILITIES_DIGEST` events.
  `materialize_overlay()` and `overlay_digests()` are the only two new authorities, and neither can
  promote anything.
* **3.4 (isolation parity, from the baseline commit forward).** `ExecRequest.masked`; `MountSet` with
  `for_execution`/`validate`/`to_dict`; `mount_set_for()` on all three providers; the counted `ro:`/`mask:`
  promise list in `unshare`'s mount script, where a failure is fatal (`MOUNT_FAILURE_MARKER`, exit 97)
  rather than a warning; `/sys` masked by both engines and by `local_bwrap` and freshly remounted by the
  `unshare` fallback behind a cached probe; `benchmark.py` gained the same read-only source bind it was
  missing; the two stale `command[0] == "bwrap"` assertions now name the *probed* path, because probe and
  exec must agree. Both P0 `xfail(strict=True)` markers are deleted - their subjects are fixed.
* **3.6 `tests/test_metamorphosis_closed_loop.py`** (new, 11 tests) — the phase's definition of done,
  driven through `run_experiment(candidate_overlay=…)` → evidence → `register_candidate` →
  `request_promotion` → `approve_promotion` → `promote` → `AgentRuntime.run_cycle`, asserting
  `tasks_started` 1 → 3 → 1 with no restart and no hand-written overlay directories.
* **3.7 Post-activation verification.** `PromotionEngine._verify_overlay_activated` compares three
  digests (the experiment's `candidate_digest`, the staged manifest's, the resolved active one), refuses
  on any mismatch, and routes the refusal through the *same* rollback path as a failed health check, so
  "the overlay did not land" and "the smoke test failed" cannot diverge into two notions of a bad
  activation. `_measured_overlay_digest()` is the new comparison that catches a candidate restaged after
  its benchmark. `rollback()` re-points the link **and** re-writes the activation record for the restored
  version, then the runtime re-verifies on its next cycle.
* **Apply-side atomicity and true withdrawal** (added while writing the acceptance tests, which is where
  it belongs). `active_version.plan_overlays()` computes every leg - limits, cognitive policy, tool
  preference, tool risk floors, never-retry additions - against the *shipped* baseline, and
  `apply_overlays()` commits only if no consumer refused anything. Each consumer plans for itself
  (`RuntimeResourceLimits` via a trial construction, `CognitiveOrchestrator.plan_policy`,
  `ToolRegistry.plan_preference`/`plan_risk_uplift`, `RecoveryManager.plan_overlay`), so no rule is
  duplicated here; a mid-commit failure is unwound through a journal of inverse operations.
  `RecoveryManager.apply_overlay` became an *assignment* over the class floor rather than a union, so a
  never-retry addition is withdrawn by a rollback like everything else, and `ToolRegistry` gained
  `risk_floors`/`plan_risk_uplift`/`apply_risk_uplift`/`reset_risk_floors`, which is what makes the
  `risk_floor_uplift` field in `config/tools.json` a real capability instead of the next dead config key.
* **Runtime load points.** `AgentRuntime` takes `versions_root`; `run_cycle` calls `_resolve_overlay()`
  once per cycle and emits the resolved view before doing any work; `RecoveryManager` gained
  `apply_overlay`/`reset_overlay` (never-retry additions only, floor re-added by the class);
  `CognitiveOrchestrator.apply_policy()` with `DEFAULT_POLICY` extracted as a class attribute so the
  shipped defaults are one readable value; `ToolRegistry.reorder()`/`order()` for `config/tools.json`
  preference, with `reorder(None)` restoring registration order after a rollback.

### Deliberate design choices worth reviewing

1. **`resolve` accepts only a directory named `overlay`.** An earlier version accepted "any directory" and
   duly read a candidate copy's own `config/*.json` as materialized state, producing a digest that looked
   verified. A resolver that trusts its caller's notion of "overlay root" is a confidentiality bug with a
   checksum on it.

2. **The digest covers the files, not the loaded documents.** A payload that was *refused* by the schema
   still changes the digest, so "the overlay was empty" and "the overlay was rejected" remain two
   different audit facts and a silently-ignored file can never masquerade as no change.

3. **The empty overlay has a real digest** (`6c902313…`, not `""`), so the pre-P3 state is comparable and a
   missing manifest cannot be confused with "no capabilities changed".

4. **Refusal, never clamping.** A materializer that clamped `max_tasks_per_cycle: 5000` to `10` would
   measure a payload nobody ran. Out-of-range and unknown keys are errors; the only *directional* rule is
   "may only grow" (`never_retry`, tool risk floors), which is expressed by refusing the smaller set.

5. **`memory_bytes`/`storage_bytes` are not overlay-writable at all.** A candidate that can enlarge them
   can enlarge its own sandbox. Documented in the table's own `notes`, not only in a test.

6. **No `.py` fragment can exist**, enforced by the fragment shape rather than by a blocklist of names -
   `07` §4's "structurally impossible" made literal. Skill payloads are validated in full and *still*
   refused on write (no catalog until P5); `SkillMaterializer.write_candidate` exists to raise, so P5's
   diff is "delete this body", not "invent a class".

7. **Per-cycle resolution rather than construction-time.** Promotion is causal and rollback is causal
   because the loop re-reads what the link points at. This is also what makes a tampered file a *refusal*
   on the next cycle rather than a slow leak.

8. **Apply is a merge over the shipped defaults, and it reports `reset`.** The first implementation set
   only what the overlay named, which meant a rollback left the promoted value in force in the live
   process; the smoke run caught it. Idempotence and reversibility are the same property: targets are
   computed from defaults, never from current values.

9. **P3 adds no new approval authority.** `EvolutionProposal` is unchanged; `candidate_overlay` is an
    argument validated by the materializer and digest-bound on both sides, and production still requires
    `promote()` behind an explicit human approval. The overlay↔approval-CLI binding is P4 (deviation 1).

10. **Event payloads carry shape and digests, never values.** `ActiveOverlay.to_dict()` names documents
    and warnings; thresholds and prompt text stay in the version directory the digest points at, because
    the audit ledger is read more widely than the overlay is.

11. **Nothing is applied when the activation check fails.** The check moved *before* the apply step after
   a test showed a tampered `42` had already been written into the runtime's limits before the refusal.
   The alternative - reset to defaults on mismatch - would replace one unverified state with another.
    Kept as its own rule because a test found it rather than a design: the first version applied and then
    refused, which left a tampered value in the live limits while the ledger said "refused".

12. **A refused leg blocks all legs.** An overlay whose tool preference names a tool this build does not
    have is a governance disagreement, not a candidate to be partially accommodated: adopting the acceptable
    half would leave the agent running a mixture no document describes and no experiment measured. The
    cycle then continues on the last *verified* configuration and records the refusal.

13. **The baseline is the process's own starting state, not the class default.** `AgentRuntime` captures
    `_limits_defaults` and `_policy_defaults` at construction, so an operator who launched the agent with
    raised budgets or a customised cognitive policy gets *those* values back on a rollback. Merging over
    `DEFAULT_POLICY` instead would hide a second behaviour change inside the recovery path - and the
    default agent's orchestrator is constructed with caps mirrored from the limits, so the two baselines
    genuinely differ.

14. **Withdrawal is a property of the algorithm, not of each consumer remembering it.** `apply_overlays`
    merges over the baseline for *every* leg (limits, policy, tool order, risk floors, never-retry
    additions), so "the overlay does not mention it" means "restore the default", uniformly. That single
    rule is what makes `A → B → C → rollback → B → rollback → A` land on B and then on A exactly, which
    `tests/test_metamorphosis_closed_loop.py` asserts as whole-state dictionary equality.

15. **`risk_floor_uplift` is applied, not just validated.** It was declared in the document table, and a
    field nothing reads is the defect this phase exists to close, so the tool registry now carries it
    (may only rise above the *registered* floor) and restores it on withdrawal. The alternative - dropping
    the field from the table - was rejected because the refusal rule already prevents the *write* of any
    key no loader reads, and this one now has one.

### Measured

* `python3 -m pytest -o addopts="" -q tests/` → **633 tests, 0 failures, 0 errors, 3 skipped, 0 xfailed**
  in 329 s (exit 0, with **no** `EVO_ALLOW_SOVEREIGN_DRIFT` set, so the tree matches the published
  manifest). P3 began at 543 collected with 2 failures and 2 xfails; it ends at 633 with none of either.
* Per-file, the parts P3 owns or touched: `test_active_version.py` **33**, `test_materialization.py`
  **29**, `test_metamorphosis_closed_loop.py` **13**, `test_isolation_boundaries.py` **13**,
  `test_metamorphosis_eligibility.py` 23 → **24**, `test_sandbox.py` 21, `test_sandbox_providers.py` 26,
  `test_backend_seams.py` 47, `test_promotion.py` 8 (unchanged: no P3 edit altered its expectations),
  `test_sovereign_invariants.py` 37, `test_documentation_integrity.py` 40, `test_runtime.py` 13.
  `test_runtime.py` (13), `test_cognitive.py` (20), `test_promotion.py` (8) and `test_production*.py` are
  unchanged in count and all green, which is the evidence that the apply-side refactor did not move
  behaviour anyone else depends on. Net growth since P2's 543 collected is +90: 13 isolation-boundary
  tests from the baseline commit, 75 from the three new P3 files, and the eligibility correspondence tests,
  less the 2 P0 xfails that were deleted after their subjects were fixed.
* The 3 skips are all honest and each names its reason: `bwrap` is not installed here, so the
  real-confinement parity test and the bwrap-branch provider test skip (the *branch-selection and
  command-shape* halves are asserted unconditionally against a refusing stub); and
  `test_verifier_refuses_an_expectation_it_cannot_check` remains characterised pending P4.
* `python3 scripts/verify_sovereign_digest.py --gate` → **10/10 invariants ok** over **18** protected
  files. Re-publication was required twice during the phase (once for the `sandbox`/`promotion`/
  `runtime`/`benchmark`/`eligibility` edits, once after the apply-order fix in `runtime.py`), and the
  suite was re-run afterwards to prove the published digests match.
* The two bwrap failures that opened this phase are **resolved - environment-only, no production defect,
  and the sandbox was not weakened**. The tests now assert branch selection and command shape against a
  stub that refuses to execute payloads (`executed_payloads == []`), and claim actual confinement only
  under a real `bwrap`.
* Rollback, as measured by whole-state comparison: `effective_state()` snapshots the limits table, the
  orchestrator's policy, the caps bound onto its components, the tool order, the tool risk floors, the
  never-retry set and the resolved digest. `A → B → C → rollback → B → rollback → A` reproduces `state_b`
  and `state_a` **exactly** (dictionary equality), and a payload naming a tool this build lacks is refused
  with every leg left untouched. `A → B` with the *recovery* leg raising mid-write leaves the recovery set
  at its prior value, which is the undo journal rather than optimism being tested.
* Causality, as measured by the tests rather than by a smoke script: baseline cycle `tasks_started == 1`;
  after `promote()`, the *same* runtime process resolves `max_tasks_per_cycle: 3` and takes 3; after
  `rollback()`, it takes 1 again with `applied["reset"] == ["max_tasks_per_cycle"]`. Four digest sources -
  materializer, experiment record, staged manifest, live resolver - agree on one value, and each of the
  two tamper windows is refused by a *different* check with a different reason string.

### Deviations from `07`

1. **The overlay↔approval binding is deferred to P4.** `07` §8's materialisation expectation is read as
   "an approved human decision is bound to the payload that ships". P3 binds the payload to the
   *experiment* (digest both sides, refuse on mismatch) and leaves `PromotionRequest` as the only place a
   human decision is recorded; threading the overlay digest into the approval CLI's confirmation text and
   into `request_promotion`'s stored approval is P4 work, since it changes what a reviewer is asked to
   sign and therefore belongs with the loop unification that decides who reviews what.
2. **3.5 (replacing `MetamorphosisEngine.create_structural_candidate()` with the materialization façade)
   was not done.** Per instruction, metamorphosis stays out of scope; `metamorphosis.py` remains a
   compatibility façade over the same tables, and the new seam is reachable from the promotion path
   without going through it. The one thing P3 did take from that item is the *refusal* semantics, which
   the façade already documented.
3. **No `Materializer` member was added to `PORTS`.** `07`'s P3 line implies a new port, but
   `tests/test_backend_seams.py:90` pins the exact seam set (`EventSink`, `SandboxProvider`,
   `ExecutionBackend`, `TurnEngine`, `VerifierPlugin`) and `I-ports-contract` counts shapes per port. The
   materializer contract is a `Protocol` in `ports/evolution_target.py`, checked by
   `materializer_obligations()`/`registry_problems()` instead of by registry membership - which keeps the
   invariant's meaning ("no seam carries authority") intact rather than widened to cover a write path.
4. **`consistency_with_sandbox()` was rewritten instead of the registry phases being reverted.** Its old
   rule was "sandbox-accepted ⇒ scheduled no later than P3", which was fine while every accepted kind was
   loadable and is wrong now that four kinds are deliberately accepted-but-inert. The rule now requires a
   sandbox-accepted kind to be loadable-and-≤P3, or to name a later phase **and** state why; a loadable
   kind may never claim a later phase. Relaxing the phases to satisfy the old rule would have been the
   easier change and would have hidden the gap this phase is explicitly leaving open.
5. **`active_version.py` is not in the protected byte set.** It holds the field allow-list, so it is a
   governance file in every sense but `PROTECTED_PATHS`, which `07` defines by *module authority* (kernel,
   promotion, sandbox, `sovereign/*`). Nothing in the agent can write to it - tools are workspace-scoped
   and evolution cannot stage a `.py` - so the risk is human, not loop-shaped. Promoting it (and
   `materialization.py`) into the manifest is a one-line change proposed for P4 alongside the approval
   binding, so that the write path and the review path become immutable together.
6. **`MountSet` is not consulted by `ApprovalMediator`, and the `/sys` default lives in the providers.**
   `07` sketches the mount set as something the mediation layer assembles. Measured against the actual
   code, the mediator's job is *whether* a run may happen; which paths are masked is a property of the
   confinement technology, and a mask that a policy layer could forget is a mask that will be forgotten.
   `MountSet` is therefore a self-description for auditing (`mount_set_for()` diffed against argv), never
   a decision object.
7. **`benchmark.py` keeps its own mount script.** It is a standalone `execvpe` path, not a provider, and
   importing a provider into it would give the benchmark a second execution authority. Parity is instead
   *asserted* (same `--tmpfs /sys`, same read-only source bind, same fatal-mount convention) by
   `tests/test_isolation_boundaries.py`. A shared helper is the better shape; adopting one means moving
   the benchmark onto a provider, which is P4's loop work, not P3's.
8. **A candidate forks from the production root, so overlays do not accumulate across versions.**
   Activating C - which carries only `config/tools.json` - reverts the budgets B's overlay had set, because
   each version is a whole snapshot and `register_candidate` stages from `source_root`, not from the active
   version. P3 leaves that pre-existing staging model untouched and asserts the consequence instead of
   hiding it (`test_rollback_restores_the_complete_previous_effective_state`); making candidates stack is
   P4 work, since it changes what "the candidate" means for the benchmark too.
9. **No storage-schema migration was performed.** Overlay digests ride in existing JSON columns
   (`resource_information`, version `metadata`), consistent with the standing P0–P3 constraint. The
   120-table memory model and the promotion tables are untouched.

### Blockers carried forward (into P4)

* **Approval↔overlay binding** (deviation 1) - the reviewer must see the digest of what they are signing,
  and `approve_promotion` should refuse a request whose staged digest differs from the requested one.
* **`active_version.py` / `materialization.py` protection** (deviation 5) - one-line `PROTECTED_PATHS`
  change plus re-publication; do it in the same commit as the approval binding.
* **`MetamorphosisEngine` façade rewiring** (3.5) - deliberately untouched; P4's loop unification decides
  whether the façade is replaced or deleted.
* **`config/strategy.json`, `config/heuristics.json`, `config/memory.json`, `config/prompts.json`** have
  schema rows and refused writers; each needs its loader (P4 planning/strategy, P5 prompt registry) before
  it can become loadable, and `_loader_gate` will keep refusing until then.
* **`Verifier` expectation-checking** - still characterised as an xfail-turned-skip; P4's "verifier owns
  done" work must delete `tests/test_audit_defects_characterisation.py` by fixing it.
* **Candidate staging forks from `source_root`** (deviation 8). Stacking a candidate on the active version
  would let a promotion carry forward the overlays it was built on, which changes what the benchmark
  compares and therefore belongs with P4's loop work, not here.
* **`ToolRegistry` risk floors are per-process state**, so a version that raises a floor is the only place
  that floor exists; if P4 gives the kernel its own registry per turn, `_resolve_overlay`'s `tools` leg has
  to move with it. The rule itself (may only rise above the registered floor) is in `tools.py` and stays.
* Per-cycle overlay resolution adds three file reads and one JSON parse per cycle. Measured cost is
  unremarkable here (the suite shows no regression), but if a deployment ever makes it hot, the fix is a
  digest-keyed cache **inside `resolve`**, never a return to construction-time resolution - the second
  would undo causality to save microseconds.
