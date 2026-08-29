"""What Evolutionary Metamorphosis may legally change, and what proves a change worked.

This is a *declaration*, not a mechanism: it is the table that the governance layer, the
benchmark gate, and the tests all read, so that "eligible", "needs a benchmark", and
"protected in every sense" mean one thing across the codebase (07 §4).

The honest state of the world at P0 is recorded rather than smoothed over. Every target
kind that ``SandboxEngine.SUPPORTED_TARGETS`` already accepts is a **configuration**
target whose payload nothing loads yet (00-AUDIT §B.3) — which is exactly why the
materialization phase exists. A kind marked ``loadable=False`` therefore cannot be
promoted to "this changed behaviour" no matter what a benchmark says, and no code path
may claim otherwise.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


ELIGIBILITY_VERSION = "metamorphosis-eligibility-v2"

#: Payload shapes that are structurally impossible targets. Evo does not evolve by
#: editing its own source: there is no materializer for it (07 §4, "No — structurally
#: impossible"). This is a stronger guarantee than the string blocklist that
#: ``Evolver.PROTECTED_TERMS`` implements, because a blocklist only rejects what it
#: anticipates.
FORBIDDEN_PAYLOADS: frozenset[str] = frozenset({
    "source_code",
    "generated_code",
    "manifest_self_edit",
    "protected_digest_update",
    "policy_widening",
})

#: Field names that may only ever move in the protective direction (07 §4 E3).
MONOTONIC_FIELDS: frozenset[str] = frozenset({
    "max_command_seconds",
    "turn_budget",
    "max_candidates_per_cycle",
    "max_concurrent_experiments",
    "cooldown_hours",
})


@dataclass(frozen=True)
class TargetKind:
    """One thing the evolution spine is allowed to change."""

    name: str
    payload: str
    #: True only when a promoted payload is actually loaded by the runtime. False means
    #: the spine can benchmark and record it, but promotion cannot change behaviour yet.
    loadable: bool
    benchmark_suites: tuple[str, ...]
    phase: str
    notes: str = ""
    #: True when ``SandboxEngine.SUPPORTED_TARGETS`` accepts this name today. The
    #: invariant registry compares the two sets, so a planned kind must say so.
    sandbox_accepted: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "payload": self.payload,
            "loadable": self.loadable,
            "benchmark_suites": list(self.benchmark_suites),
            "phase": self.phase,
            "notes": self.notes,
            "sandbox_accepted": self.sandbox_accepted,
        }


@dataclass(frozen=True)
class ProtectedComponent:
    """An authority that is never a target, with the reason stated (never implied)."""

    name: str
    reason: str
    owner_module: str = ""
    enforced_by: tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "reason": self.reason,
            "owner_module": self.owner_module,
            "enforced_by": list(self.enforced_by),
        }


# --- eligible target kinds -------------------------------------------------------------
# The first eight names are exactly what SandboxEngine.SUPPORTED_TARGETS accepts today,
# so that the registry cannot silently disagree with the engine that enforces it. The
# remainder are the kinds introduced by the materialization phase.
TARGET_KINDS: tuple[TargetKind, ...] = (
    TargetKind(
        name="strategy-selection",
        payload="strategy artifact selection: which planner/heuristic set a task class uses",
        loadable=False,
        benchmark_suites=("core-local", "regression"),
        phase="P4",
        notes="the overlay can carry a preference list; the selector that consumes it is P4 work",
        sandbox_accepted=True,
    ),
    TargetKind(
        name="strategy parameters",
        payload="budgets, thresholds, retry counts and recovery knobs (clamped by R6)",
        loadable=True,
        benchmark_suites=("recovery", "cost-latency"),
        phase="P3",
        notes="loaded by AgentRuntime.run_cycle from overlay/config/runtime.json; unlisted keys revert to the shipped default on rollback",
        sandbox_accepted=True,
    ),
    TargetKind(
        name="tool-selection",
        payload="capability-to-tool preference order within the permission set already granted",
        loadable=True,
        benchmark_suites=("tool-selection", "core-local"),
        phase="P3",
        notes="loaded by ToolRegistry.reorder from overlay/config/tools.json; order only, never the permission set",
        sandbox_accepted=True,
    ),
    TargetKind(
        name="retry/recovery configuration",
        payload="retry policy and recovery ladder parameters",
        loadable=True,
        benchmark_suites=("recovery",),
        phase="P3",
        notes="max_retry_count and max_recovery_cycles come from the same document's limits",
        sandbox_accepted=True,
    ),
    TargetKind(
        name="recovery-policy",
        payload="which recovery class a failure family maps to",
        loadable=True,
        benchmark_suites=("recovery",),
        phase="P3",
        notes="overlay/config/runtime.json.recovery.never_retry, applied by RecoveryManager.apply_overlay; the set may only grow. Also ProposalRisk.HIGH by name (evolver.py:348); high risk is not the same as protected",
        sandbox_accepted=True,
    ),
    TargetKind(
        name="planning configuration",
        payload="planner shape: step caps, verification strictness within the allow-list",
        loadable=True,
        benchmark_suites=("core-local", "verification-quality"),
        phase="P3",
        notes="overlay/config/cognitive_policy.json, applied by CognitiveOrchestrator.apply_policy, which re-binds the engines that captured a cap at construction",
        sandbox_accepted=True,
    ),
    TargetKind(
        name="planning-heuristics",
        payload="heuristic weights and ordering functions supplied as data",
        loadable=False,
        benchmark_suites=("core-local",),
        phase="P4",
        notes="weights are data an overlay can carry, but nothing reweights a planner from a document yet",
        sandbox_accepted=True,
    ),
    TargetKind(
        name="prompt/configuration parameters",
        payload="prompt templates and provider settings from the reviewed allow-list",
        loadable=False,
        benchmark_suites=("core-local", "research"),
        phase="P5",
        notes="there is no prompt registry to load them, so the materializer refuses rather than writing a file nothing reads",
        sandbox_accepted=True,
    ),
    TargetKind(
        name="skill",
        payload="install / enable / disable / version a skill directory under the active overlay",
        loadable=False,
        benchmark_suites=("skill-acquisition", "core-local"),
        phase="P5",
        notes="SkillMaterializer already validates name shape, frontmatter, size and executable markers; it refuses to write until the catalog exists",
    ),
    TargetKind(
        name="tool_binding",
        payload="tool catalog rows: risk floor, permissions, aliases, fallback order",
        loadable=True,
        benchmark_suites=("tool-selection", "core-local"),
        phase="P3",
        notes="the preference-order half is loaded today; risk floors may only move upward (MONOTONIC_FIELDS + E3) and permission sets are not overlay-writable at all",
    ),
    TargetKind(
        name="provider_config",
        payload="MCP servers, research providers, sandbox provider and backend selection within an allow-list",
        loadable=False,
        benchmark_suites=("mcp-behaviour", "research", "cost-latency", "isolation-attestation"),
        phase="P5",
        notes="a downgrade below the default isolation provider is not a valid candidate (R7), enforced in ProviderConfigMaterializer as well as the schema",
    ),
    TargetKind(
        name="pipeline_stage",
        payload="stage selection and stage parameters from a reviewed allow-list; never stage source",
        loadable=True,
        benchmark_suites=("regression", "guard-effectiveness"),
        phase="P3",
        notes="stage *parameters* are loaded (cognitive policy); stage *selection* is not, until P4",
    ),
    TargetKind(
        name="memory_policy",
        payload="extraction, retention, staleness, retrieval weights, compaction budget",
        loadable=False,
        benchmark_suites=("memory-recall", "long-horizon"),
        phase="P4",
        notes="policies only. Memory contents are evidence, never a target (see PROTECTED_COMPONENTS); the retrieval path that would read these weights is P4",
    ),
)

# --- protected components --------------------------------------------------------------
PROTECTED_COMPONENTS: tuple[ProtectedComponent, ...] = (
    ProtectedComponent(
        name="governance",
        reason="the rules that decide what may change cannot themselves be the change",
        owner_module="evo_agent/evolver.py",
        enforced_by=("PROTECTED_TERMS", "protected byte set", "invariant registry"),
    ),
    ProtectedComponent(
        name="permission enforcement",
        reason="a capability that could widen its own permissions has none",
        owner_module="evo_agent/security.py",
        enforced_by=("SecurityPolicy", "protected byte set"),
    ),
    ProtectedComponent(
        name="approval authority",
        reason="self-approval is the failure mode every other control assumes away",
        owner_module="evo_agent/runtime.py",
        enforced_by=("RuntimeApproval", "approval scope digest"),
    ),
    ProtectedComponent(
        name="sandbox isolation",
        reason="an agent that can edit its own confinement does not have confinement",
        owner_module="evo_agent/sandbox.py",
        enforced_by=("protected byte set", "I-exec-isolation"),
    ),
    ProtectedComponent(
        name="verification authority",
        reason="the entity that decides 'done' must not be tuned by the thing being graded",
        owner_module="evo_agent/verifier.py",
        enforced_by=("Verifier", "protected byte set"),
    ),
    ProtectedComponent(
        name="rollback authority",
        reason="the escape hatch must be outside the system it escapes from",
        owner_module="evo_agent/promotion.py",
        enforced_by=("PromotionEngine.rollback", "protected byte set"),
    ),
    ProtectedComponent(
        name="audit integrity",
        reason="an editable ledger proves nothing; events are append-only by construction",
        owner_module="evo_agent/storage.py",
        enforced_by=("append_event", "protected byte set"),
    ),
    ProtectedComponent(
        name="kill switch",
        reason="emergency shutdown that can be disabled by the disabled thing is not a shutdown",
        owner_module="evo_agent/runtime.py",
        enforced_by=("ShutdownManager", "protected byte set"),
    ),
    ProtectedComponent(
        name="trust boundary",
        reason="what counts as an operator decision is the definition of autonomy's limit",
        owner_module="evo_agent/security.py",
        enforced_by=("requires_approval", "protected byte set"),
    ),
    ProtectedComponent(
        name="promotion authorization",
        reason="the gate between 'measured better' and 'running now' stays human-side",
        owner_module="evo_agent/promotion.py",
        enforced_by=("PromotionRequest", "protected byte set"),
    ),
    ProtectedComponent(
        name="memory contents",
        reason="stored experience is evidence for evaluation, not a capability surface; "
              "in-place mutation would corrupt the basis of every past verdict (06 §12.6)",
        owner_module="evo_agent/memory.py",
        enforced_by=("MemoryStore versioned writes", "protected byte set"),
    ),
    ProtectedComponent(
        name="agent loop control flow",
        reason="one loop is the integration constraint; letting candidates re-shape it "
              "re-creates the three-loop failure (06 §14 L9)",
        owner_module="evo_agent/runtime.py",
        enforced_by=("I-single-loop", "protected byte set"),
    ),
)


def eligible_target_kinds(*, loadable_only: bool = False) -> tuple[TargetKind, ...]:
    kinds = TARGET_KINDS if not loadable_only else tuple(kind for kind in TARGET_KINDS if kind.loadable)
    return tuple(kind for kind in kinds if kind.payload not in FORBIDDEN_PAYLOADS)


def protected_components() -> tuple[ProtectedComponent, ...]:
    return PROTECTED_COMPONENTS


def is_protected(name: str) -> bool:
    needle = name.strip().lower()
    if any(needle == component.name.lower() for component in PROTECTED_COMPONENTS):
        return True
    # Substring match, mirroring Evolver.classify_risk, so that a target of
    # "sandbox isolation tuning" is caught rather than reworded past.
    return any(component.name.lower() in needle for component in PROTECTED_COMPONENTS)


def validate_registry() -> list[str]:
    """Structural self-consistency of the eligibility table.

    Returns a list of defects; empty means the table is trustworthy enough to gate on.
    Kept separate from the invariant registry so it can be called from tests, from the
    release gate, and from any future ``evo undergo-metamorphosis`` pre-flight.
    """
    defects: list[str] = []
    names: set[str] = set()
    for kind in TARGET_KINDS:
        if kind.name in names:
            defects.append(f"duplicate target kind: {kind.name}")
        names.add(kind.name)
        if not kind.benchmark_suites:
            defects.append(f"{kind.name}: promotable without a benchmark suite (violates R10)")
        if kind.payload in FORBIDDEN_PAYLOADS:
            defects.append(f"{kind.name}: forbidden payload {kind.payload}")
        if not kind.phase:
            defects.append(f"{kind.name}: no phase recorded for the loadable gap")
        if is_protected(kind.name):
            defects.append(f"{kind.name}: eligible target that is also a protected component")
    protected_names = {component.name for component in PROTECTED_COMPONENTS}
    if len(protected_names) != len(PROTECTED_COMPONENTS):
        defects.append("duplicate protected component entry")
    for component in PROTECTED_COMPONENTS:
        if not component.reason.strip():
            defects.append(f"{component.name}: protected without a stated reason")
        if not component.enforced_by:
            defects.append(f"{component.name}: protected without an enforcement mechanism")
    return defects


def registry_report() -> dict[str, Any]:
    return {
        "eligibility_version": ELIGIBILITY_VERSION,
        "target_kinds": [kind.to_dict() for kind in TARGET_KINDS],
        "loadable_target_kinds": [kind.name for kind in TARGET_KINDS if kind.loadable],
        "protected_components": [component.to_dict() for component in PROTECTED_COMPONENTS],
        "sandbox_accepted_target_kinds": [kind.name for kind in TARGET_KINDS if kind.sandbox_accepted],
        "forbidden_payloads": sorted(FORBIDDEN_PAYLOADS),
        "monotonic_fields": sorted(MONOTONIC_FIELDS),
        "defects": validate_registry(),
    }


def consistency_with_sandbox() -> list[str]:
    """Compare the registry against what the sandbox engine will actually accept.

    Imported lazily: this module is read by tooling that must work even when the rest of
    the agent cannot be constructed, and an import cycle here would hide the very
    mismatch it is meant to report.
    """
    try:
        from ..sandbox import SandboxEngine
    except Exception as exc:  # pragma: no cover - only on a broken tree
        return [f"cannot import SandboxEngine to compare targets: {type(exc).__name__}: {exc}"]
    engine_targets = set(SandboxEngine.SUPPORTED_TARGETS)
    registry_targets = {kind.name for kind in TARGET_KINDS}
    defects: list[str] = []
    for missing in sorted(engine_targets - registry_targets):
        defects.append(f"SandboxEngine accepts '{missing}' but the eligibility registry does not declare it")
    declared = {kind.name for kind in TARGET_KINDS if kind.sandbox_accepted}
    for extra in sorted(declared - engine_targets):
        defects.append(f"registry marks '{extra}' as sandbox-accepted but SandboxEngine will not accept it")
    for kind in TARGET_KINDS:
        if not kind.sandbox_accepted or not kind.phase.startswith("P"):
            continue
        try:
            scheduled = int(kind.phase[1:])
        except ValueError:
            defects.append(f"'{kind.name}' declares an unparsable phase {kind.phase!r}")
            continue
        if kind.loadable and scheduled > 3:
            # A kind that the runtime can already load must not claim a later phase needs to deliver
            # it: that would be a loader without a schedule, i.e. an unaudited capability.
            defects.append(f"'{kind.name}' is loadable but still scheduled for {kind.phase}")
        if not kind.loadable and scheduled > 3:
            # The state P3 deliberately left in place for four target names: the sandbox will accept a
            # proposal for them, and the payload stays descriptive until a loader exists. Tolerated
            # only with a stated reason, because "accepted but inert" is exactly the condition that a
            # reader otherwise mistakes for "working".
            if not kind.notes.strip():
                defects.append(f"'{kind.name}' is sandbox-accepted, not loadable, and scheduled for {kind.phase} with no reason stated")
            continue
        if not kind.loadable and scheduled <= 3 and kind.phase == "P3":
            defects.append(f"'{kind.name}' is scheduled for P3 but nothing loads it: the phase that promised it is not finished")
    return defects
