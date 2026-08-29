"""What the running agent's capabilities actually are, resolved from the active version.

This is the file that closes the audit's first founding finding: the evolution spine could propose,
experiment, benchmark, promote and roll back, and *nothing changed* (00 §B.3, 06 §Finding 1) because
``evolution_config.json`` - the only artifact the sandbox ever wrote for a candidate - was read by
no one except a test asserting that it existed. Promotion is only real if the runtime reads what
promotion switched. So the read side lives here, in one place, and the write side
(:mod:`evo_agent.materialization`) has to agree with it through the shared document table below.

Three rules shape every choice in this module.

**Allow-listed subpaths only.** The overlay is a directory inside a version, and the resolver reads
exactly the subpaths in :data:`evo_agent.ports.evolution_target.ALLOWED_SUBPATHS`. Everything else is
ignored *and recorded as a warning*, because a silently dropped file is how a shadowed default
becomes invisible (07 §8, S11).

**Repo-default fallback.** A fresh install has no ``versions/active`` link at all. That is not an
error and must not be treated as one: the resolver returns an overlay whose source is
``"repo-default"`` and whose digest is the empty-overlay constant, so "nothing has been promoted yet"
and "the overlay is corrupt" stay different states. A runtime that could not start without a version
directory would turn the evolution spine into a boot dependency.

**No loader, no materialization.** A kind whose consumer does not exist yet cannot be materialized
at all: the materializer refuses with the phase named. Writing a file that nothing reads is how the
previous phase ended up with a config nobody loaded, and an honest refusal is cheaper to debug than
a dead document.

The digests are the point of the whole exercise: :func:`active_capabilities_digest` is computed the
same way by the sandbox (candidate vs baseline) and by the promotion engine (after activation), so
"the experiment tested these capabilities" and "the agent is now running these capabilities" are two
comparable numbers rather than two prose sentences.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
import json
from pathlib import Path
from typing import Any

from .ports.evolution_target import (
    ALLOWED_SUBPATHS,
    OVERLAY_DIRNAME,
    OVERLAY_MANIFEST,
    overlay_digest,
    verify_fragment_tree,
)

#: Where the immutable version registry lives, relative to a checkout. ``PromotionEngine`` used to
#: own this default; it now asks here, because a resolver that guessed a different directory would
#: silently resolve to "no overlay" - the exact state that reads as "nothing has been promoted".
PRODUCTION_DIRNAME = ".evo-production"
ACTIVATION_RECORD = "overlay-activation.json"


def default_versions_root(source_root: Path) -> Path:
    return Path(source_root).expanduser().resolve().parent / PRODUCTION_DIRNAME


# --- the document table ----------------------------------------------------------------


@dataclass(frozen=True)
class Field:
    """One allow-listed knob. The shape is deliberately boring: every rule here is a *rejection*.

    A field not named here cannot arrive through an overlay, which is what makes "the payload may
    only widen what is already permitted" (07 §4, E3) a property of the schema rather than a hope.
    """

    kind: str
    minimum: int = 0
    maximum: int = 1_000_000
    #: Names a scalar or a list item must be one of. Empty means "any name of this shape".
    allowed: tuple[str, ...] = ()
    #: Maximum entries for a map or list, so a payload cannot grow a table without limit.
    max_entries: int = 32
    #: A string field's ceiling, in characters.
    max_length: int = 4096
    #: Nested rules for ``kind == "doc"``.
    fields: dict[str, "Field"] = field(default_factory=dict)
    #: Rules for the values of ``kind == "map_int"``.
    value: "Field | None" = None

    def validate(self, path: str, value: Any) -> tuple[Any, list[str]]:
        """Return ``(usable_value, problems)``. A problem means the field is dropped, not clamped.

        Refusing rather than clamping is the difference between a payload that silently becomes
        something else and one that is reported. ``{"max_task_duration": 10**9}`` clamped to the
        ceiling would let a candidate claim it tested a limit the host would never honour; refused,
        it goes back to the proposer with a reason.
        """
        problems: list[str] = []
        if self.kind == "int":
            if isinstance(value, bool) or not isinstance(value, int):
                return None, [f"{path}: expected an integer"]
            if value < self.minimum or value > self.maximum:
                return None, [f"{path}: {value} is outside [{self.minimum}, {self.maximum}]"]
            return value, problems
        if self.kind == "str":
            if not isinstance(value, str) or not value.strip():
                return None, [f"{path}: expected a non-empty string"]
            if len(value) > self.max_length:
                return None, [f"{path}: longer than {self.max_length} characters"]
            return value.strip(), problems
        if self.kind == "name":
            if not isinstance(value, str) or value not in self.allowed:
                return None, [f"{path}: {value!r} is not one of {', '.join(self.allowed) or '(none yet)'}"]
            return value, problems
        if self.kind == "list_name":
            if not isinstance(value, (list, tuple)) or not value:
                return None, [f"{path}: expected a non-empty list"]
            if len(value) > self.max_entries:
                return None, [f"{path}: {len(value)} entries exceed the {self.max_entries} limit"]
            kept: list[str] = []
            for index, item in enumerate(value):
                if not isinstance(item, str) or item not in self.allowed:
                    problems.append(f"{path}[{index}]: {item!r} is not one of {', '.join(self.allowed) or '(none yet)'}")
                    continue
                if item not in kept:
                    kept.append(item)
            if not kept:
                return None, problems
            return kept, problems
        if self.kind == "map_int":
            if not isinstance(value, dict) or not value:
                return None, [f"{path}: expected a non-empty object"]
            if len(value) > self.max_entries:
                return None, [f"{path}: {len(value)} keys exceed the {self.max_entries} limit"]
            rules = self.value or Field(kind="int", minimum=self.minimum, maximum=self.maximum)
            kept_map: dict[str, int] = {}
            for key, item in value.items():
                if self.allowed and key not in self.allowed:
                    problems.append(f"{path}.{key}: not an allow-listed key")
                    continue
                cleaned, inner = rules.validate(f"{path}.{key}", item)
                problems.extend(inner)
                if cleaned is not None:
                    kept_map[str(key)] = cleaned
            if not kept_map:
                return None, problems
            return kept_map, problems
        if self.kind == "doc":
            if not isinstance(value, dict):
                return None, [f"{path}: expected an object"]
            cleaned_doc: dict[str, Any] = {}
            for key, item in value.items():
                rule = self.fields.get(key)
                if rule is None:
                    problems.append(f"{path}.{key}: not an allow-listed field")
                    continue
                cleaned, inner = rule.validate(f"{path}.{key}", item)
                problems.extend(inner)
                if cleaned is not None:
                    cleaned_doc[key] = cleaned
            if not cleaned_doc:
                return None, problems
            return cleaned_doc, problems
        return None, [f"{path}: unknown field kind {self.kind!r}"]


#: Names a recovery class may be remapped to. A policy target can make failures *less* retryable and
#: can add classes to the never-retry set; it can never take one out (R6, protective direction only).
FAILURE_CLASSES: tuple[str, ...] = (
    "transient", "environment", "resource", "tool", "verification", "permission", "approval", "unknown",
)
#: Classes a payload may not remove from the never-retry set, whatever it claims.
IMMUTABLE_NEVER_RETRY: tuple[str, ...] = ("permission", "approval")

#: The four tools Evo exposes (``ToolRegistry.register_defaults``). Names, not aliases: a preference
#: order over a name nothing registers would be a document that quietly does nothing.
TOOL_NAMES: tuple[str, ...] = ("workspace_list", "workspace_read", "workspace_write", "shell")
STRATEGY_NAMES: tuple[str, ...] = ("cognitive-bounded",)
SKILL_NAME_MAX = 64


@dataclass(frozen=True)
class DocumentSpec:
    """One overlay document: what it is for, who loads it, and what it may contain."""

    relpath: str
    kind: str
    risk: str
    #: Dotted consumer, or ``""`` when nothing loads it yet. ``loader_required_to_materialize`` turns
    #: that field into an enforcement point rather than a comment.
    loaded_by: str
    phase: str
    fields: dict[str, Field] = field(default_factory=dict)
    notes: str = ""

    @property
    def loadable(self) -> bool:
        return bool(self.loaded_by)

    def validate(self, payload: Any) -> tuple[dict[str, Any], list[str]]:
        if not isinstance(payload, dict):
            return {}, [f"{self.relpath}: payload must be a JSON object"]
        rule = Field(kind="doc", fields=self.fields)
        return rule.validate(self.relpath, payload)


DOCUMENTS: dict[str, DocumentSpec] = {
    "config/runtime.json": DocumentSpec(
        relpath="config/runtime.json",
        kind="strategy_params",
        risk="Medium",
        loaded_by="evo_agent.runtime:AgentRuntime.run_cycle",
        phase="P3",
        fields={
            "resource_limits": Field(
                kind="map_int",
                max_entries=16,
                allowed=(
                    "max_concurrent_tasks", "max_task_duration", "max_total_runtime", "max_retry_count",
                    "max_recovery_cycles", "max_replans", "max_queue_size", "max_tasks_per_cycle",
                    "max_event_growth",
                ),
                value=Field(kind="int", minimum=1, maximum=10_000),
            ),
            "recovery": Field(
                kind="doc",
                fields={
                    # Only the never-retry set. Per-class retry *budgets* are deliberately absent:
                    # ``RecoveryManager`` already clamps a retry to ``max_retry_count``, so a second
                    # budget knob would be a second authority over the same number, and the two would
                    # disagree in ways nobody could read off a task's status.
                    "never_retry": Field(kind="list_name", allowed=FAILURE_CLASSES, max_entries=8),
                },
            ),
        },
        notes=(
            "memory_bytes/storage_bytes are excluded: those two bound what a task may consume of the "
            "host, and a candidate that can enlarge them can also enlarge its own sandbox"
        ),
    ),
    "config/cognitive_policy.json": DocumentSpec(
        relpath="config/cognitive_policy.json",
        kind="pipeline_stage",
        risk="High",
        loaded_by="evo_agent.runtime:AgentRuntime.run_cycle",
        phase="P3",
        fields={
            "policy": Field(
                kind="map_int",
                max_entries=8,
                allowed=(
                    "max_subtasks", "max_plan_candidates", "max_reasoning_iterations", "max_replans",
                    "max_execution_time", "max_context_size", "max_tool_calls",
                ),
                value=Field(kind="int", minimum=1, maximum=10_000),
            ),
        },
        notes="stage parameters only: stage *source* is not a target in any phase (07 §4)",
    ),
    "config/strategy.json": DocumentSpec(
        relpath="config/strategy.json",
        kind="pipeline_stage",
        risk="Medium",
        loaded_by="",
        phase="P4",
        fields={
            "preferred_strategies": Field(kind="list_name", allowed=STRATEGY_NAMES, max_entries=4),
            "fallback_strategies": Field(kind="list_name", allowed=STRATEGY_NAMES, max_entries=4),
        },
        notes="one strategy exists today, so a preference order over it is a document nothing needs yet",
    ),
    "config/tools.json": DocumentSpec(
        relpath="config/tools.json",
        kind="tool_binding",
        risk="Medium",
        loaded_by="evo_agent.runtime:AgentRuntime.run_cycle",
        phase="P3",
        fields={
            "preference": Field(kind="list_name", allowed=TOOL_NAMES, max_entries=len(TOOL_NAMES)),
            "risk_floor_uplift": Field(kind="map_int", allowed=TOOL_NAMES, value=Field(kind="int", minimum=1, maximum=4), max_entries=len(TOOL_NAMES)),
        },
        notes="preference within the already-granted set; permission sets are not overlay-writable",
    ),
    "config/prompts.json": DocumentSpec(
        relpath="config/prompts.json",
        kind="provider_config",
        risk="Low",
        loaded_by="",
        phase="P5",
        fields={"templates": Field(kind="map_int", max_entries=1)},
        notes="no prompt registry exists yet, so every payload for this document is refused",
    ),
    "config/memory.json": DocumentSpec(
        relpath="config/memory.json",
        kind="memory_policy",
        risk="Medium",
        loaded_by="",
        phase="P4",
        fields={
            "retrieval_weights": Field(kind="map_int", max_entries=8, value=Field(kind="int", minimum=0, maximum=1000)),
            "retention_days": Field(kind="int", minimum=1, maximum=3650),
            "staleness_ratio": Field(kind="int", minimum=1, maximum=100),
        },
        notes="policies only; memory *contents* are evidence, never a target (07 §4 PROTECTED_COMPONENTS)",
    ),
    "config/heuristics.json": DocumentSpec(
        relpath="config/heuristics.json",
        kind="pipeline_stage",
        risk="High",
        loaded_by="",
        phase="P4",
        fields={"weights": Field(kind="map_int", max_entries=12, value=Field(kind="int", minimum=0, maximum=1000))},
        notes="heuristic weights as data; ordering *functions* would be code and are not materializable",
    ),
}


#: Which eligibility kind each sandbox target string materializes. The eight left-hand names are
#: exactly ``SandboxEngine.SUPPORTED_TARGETS``; keeping the table here (rather than deriving it from
#: the engine) means the engine's accepted names and the spine's payload shapes are checked against
#: each other by ``I-eligibility-coherence`` and by tests, not assumed to agree.
TARGET_TO_KIND: dict[str, str] = {
    "strategy parameters": "strategy_params",
    "retry/recovery configuration": "strategy_params",
    "recovery-policy": "strategy_params",
    "planning configuration": "pipeline_stage",
    "planning-heuristics": "pipeline_stage",
    "strategy-selection": "pipeline_stage",
    "tool-selection": "tool_binding",
    "prompt/configuration parameters": "provider_config",
    "skill": "skill",
    "tool_binding": "tool_binding",
    "provider_config": "provider_config",
    "pipeline_stage": "pipeline_stage",
    "memory_policy": "memory_policy",
}


def documents_for_kind(kind: str) -> tuple[DocumentSpec, ...]:
    return tuple(spec for spec in DOCUMENTS.values() if spec.kind == kind)


def loadable_kinds() -> frozenset[str]:
    """Kinds a consumer actually reads. Derived, so a document cannot claim to be loaded by luck."""
    return frozenset(spec.kind for spec in DOCUMENTS.values() if spec.loadable)


def unloadable_kinds() -> frozenset[str]:
    return frozenset(spec.kind for spec in DOCUMENTS.values() if not spec.loadable)


# --- the resolved overlay ----------------------------------------------------------------


@dataclass(frozen=True)
class ActiveOverlay:
    """What the runtime would load, right now, with the digest that identifies it.

    ``documents`` holds *validated* payloads: anything the schema rejected is in ``warnings`` and not
    in ``documents``, so a consumer never has to re-check bounds and cannot be tricked by a value
    that looks present but was refused. That asymmetry - refuse loudly at resolution, read
    trustingly afterwards - is what keeps the load path free of policy while the resolution path is
    nothing but policy.
    """

    source: str
    version_id: str | None
    overlay_root: Path | None
    documents: dict[str, dict[str, Any]] = field(default_factory=dict)
    warnings: tuple[str, ...] = ()
    digest: str = ""
    fragments: tuple[Any, ...] = ()
    #: the activation record's digest, when one existed; compared by :func:`verify_activation`
    activation_digest: str | None = None

    @property
    def is_repo_default(self) -> bool:
        return self.source == "repo-default"

    @property
    def relpaths(self) -> tuple[str, ...]:
        return tuple(sorted(self.documents))

    def document(self, relpath: str) -> dict[str, Any]:
        return dict(self.documents.get(relpath) or {})

    def resource_limit_overrides(self) -> dict[str, int]:
        limits = self.document("config/runtime.json").get("resource_limits") or {}
        return {str(key): int(value) for key, value in limits.items()}

    def cognitive_policy_overrides(self) -> dict[str, int]:
        policy = self.document("config/cognitive_policy.json").get("policy") or {}
        return {str(key): int(value) for key, value in policy.items()}

    def recovery_overrides(self) -> dict[str, Any]:
        """The one recovery knob with a loader: classes that must never be retried (may only grow)."""
        recovery = self.document("config/runtime.json").get("recovery") or {}
        never = recovery.get("never_retry")
        return {"never_retry": [str(item) for item in never]} if isinstance(never, (list, tuple)) else {}

    def to_dict(self) -> dict[str, Any]:
        """The event-safe view: identity and shape, no contents.

        Prompt text and thresholds are what an attacker-with-write-access would want, and an audit
        ledger is read by more people than the overlay directory. The digest is the reference that
        lets a reader pull the content from the version it points at.
        """
        return {
            "source": self.source,
            "version_id": self.version_id,
            "digest": self.digest,
            "documents": list(self.relpaths),
            "loaded": [relpath for relpath in self.relpaths if DOCUMENTS.get(relpath) and DOCUMENTS[relpath].loadable],
            "unloaded": [relpath for relpath in self.relpaths if DOCUMENTS.get(relpath) and not DOCUMENTS[relpath].loadable],
            "warnings": list(self.warnings),
            "activation_digest": self.activation_digest,
        }


def _activation_record(versions_root: Path) -> dict[str, Any]:
    path = Path(versions_root) / ACTIVATION_RECORD
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}


def resolve(
    versions_root: Path | None = None,
    *,
    source_root: Path | None = None,
    overlay_dir: Path | None = None,
) -> ActiveOverlay:
    """Resolve the active overlay, falling back to repo defaults.

    ``overlay_dir`` is the sandbox's escape hatch: an experiment runs against a *candidate* overlay
    that is not active anywhere, and the only way to compare baseline and candidate digests is to
    resolve both through this same function. A separate resolver for candidates would be a second
    opinion about which files matter, and the two would drift.
    """
    root: Path | None = None
    version_id: str | None = None
    source = "repo-default"
    versions_root = Path(versions_root) if versions_root is not None else (
        default_versions_root(source_root) if source_root is not None else None
    )
    if overlay_dir is not None:
        # An overlay is a directory *named* ``overlay``, nothing else. Resolving a candidate sandbox
        # by "walk whatever you were given" would read a copy of the source tree - ``config/*.json``
        # included - as if it were materialized state, and the resulting digest would look like a
        # verified capability set. A caller that passes a version or candidate directory gets the
        # ``overlay`` inside it, or an empty overlay, and never a traversal of somebody's checkout.
        given = Path(overlay_dir).expanduser().resolve()
        nested = given / OVERLAY_DIRNAME
        root = nested if nested.is_dir() else (given if given.name == OVERLAY_DIRNAME else None)
        source = "candidate"
    else:
        if versions_root is None:
            # Neither root given: the checkout this module lives in, not the working directory. A
            # resolver whose answer changed with cwd would let one agent resolve two different
            # capability sets in two shells.
            versions_root = default_versions_root(Path(__file__).resolve().parent.parent)
        active_link = versions_root / "active"
        if active_link.is_symlink() or active_link.is_dir():
            target = active_link.resolve()
            candidate_root = target / OVERLAY_DIRNAME
            if candidate_root.is_dir():
                root, source = candidate_root, "active"
                version_id = target.name
    fragments: list[Any] = []
    warnings: list[str] = []
    if root is not None and root.is_dir():
        fragments, warnings = verify_fragment_tree(root)
    elif overlay_dir is not None:
        # Not a warning about a broken install: an experiment with no overlay is the baseline, and the
        # empty digest says so precisely. Recorded anyway so a candidate that *lost* its overlay is
        # distinguishable from one that never had it.
        warnings.append("no overlay directory for this candidate; resolving as repo defaults")

    documents: dict[str, dict[str, Any]] = {}
    for fragment in fragments:
        spec = DOCUMENTS.get(fragment.relpath)
        if spec is None:
            warnings.append(f"{fragment.relpath}: no document spec covers this path, so nothing will load it")
            continue
        try:
            payload = json.loads(fragment.content)
        except ValueError as exc:
            warnings.append(f"{fragment.relpath}: unreadable JSON ({exc})")
            continue
        cleaned, problems = spec.validate(payload)
        warnings.extend(problems)
        if cleaned:
            documents[spec.relpath] = cleaned
    # A fragment that survived the tree walk but whose document was refused entirely must still be
    # visible: the digest covers the *files*, so an ignored file changes the digest and the activation
    # check reports the mismatch instead of shrugging.
    digest = overlay_digest(tuple(fragments))
    return ActiveOverlay(
        source=source,
        version_id=version_id,
        overlay_root=root,
        documents=documents,
        warnings=tuple(dict.fromkeys(warnings)),
        digest=digest,
        fragments=tuple(fragments),
        activation_digest=_activation_record(versions_root).get("digest") if versions_root is not None else None,
    )


def active_capabilities_digest(overlay_or_root: ActiveOverlay | Path | None) -> str:
    """Digest of an overlay, from an :class:`ActiveOverlay` or a directory. One rule, two callers."""
    if isinstance(overlay_or_root, ActiveOverlay):
        return overlay_or_root.digest
    if overlay_or_root is None:
        return overlay_digest(())
    overlay = resolve(overlay_dir=Path(overlay_or_root))
    return overlay.digest


def verify_activation(versions_root: Path, overlay: ActiveOverlay) -> dict[str, Any]:
    """Whether the runtime would load what promotion says it activated.

    A mismatch is refused rather than repaired (S11). The three ways to get one - a partial copy, an
    edit after activation, and a version directory swapped by hand - all mean "the agent is now
    serving something nobody benchmarked", and continuing to serve while recording the fact would put
    the record ahead of the truth.
    """
    expected = _activation_record(versions_root).get("digest")
    report: dict[str, Any] = {
        "expected_digest": expected,
        "actual_digest": overlay.digest,
        "source": overlay.source,
        "consistent": True,
        "reason": "",
    }
    if not expected:
        report["consistent"] = overlay.is_repo_default or overlay.digest == overlay_digest(())
        report["reason"] = (
            "" if report["consistent"] else "no activation record exists for an overlay that is not empty"
        )
    elif expected != overlay.digest:
        report["consistent"] = False
        report["reason"] = "the active overlay no longer matches what was activated"
    # Warnings are reported, never folded into consistency. Consistency is the digest question and
    # only that; mixing in "a file was ignored" would let a caller satisfy a tamper alarm by fixing
    # the warning, and the ignored file is already in the digest anyway.
    if overlay.warnings:
        report["warnings"] = list(overlay.warnings)
    return report


def write_activation_record(versions_root: Path, overlay: ActiveOverlay, *, promotion_id: str | None = None, version_id: str | None = None) -> dict[str, Any]:
    """Record what was activated, outside the immutable version tree.

    The version directory is chmod-ed read-only on purpose, so the record cannot live there; it lives
    next to the ``active`` link, which is the other half of the same switch, and both are written by
    the promotion engine while it holds the switch.
    """
    versions_root = Path(versions_root)
    versions_root.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema": "evo-overlay-activation-v1",
        "digest": overlay.digest,
        "version_id": version_id or overlay.version_id,
        "documents": list(overlay.relpaths),
        "promotion_id": promotion_id,
        "overlay_relative": OVERLAY_DIRNAME,
        "manifest": OVERLAY_MANIFEST,
    }
    (versions_root / ACTIVATION_RECORD).write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return payload


def plan_overlays(
    overlay: ActiveOverlay,
    *,
    limits: Any = None,
    limits_defaults: dict[str, int] | None = None,
    policy: dict[str, int] | None = None,
    policy_defaults: dict[str, int] | None = None,
    cognitive: Any = None,
    tools: Any = None,
    recovery: Any = None,
) -> tuple[dict[str, Any], list[str]]:
    """Decide what an overlay means for each consumer, touching nothing. Returns ``(plan, refused)``.

    Every leg is computed against the **shipped baseline** the caller supplies, never against the current
    value, for two reasons that are the same reason: it makes the plan idempotent (planning twice from the
    same overlay cannot drift) and it makes withdrawal a property of the algorithm rather than a feature
    someone remembers to add (a key the overlay no longer names is planned *back* to its default).

    Each consumer is asked to plan for itself - ``RuntimeResourceLimits`` through a trial construction,
    :meth:`CognitiveOrchestrator.plan_policy`, ``ToolRegistry.plan_preference``/``plan_risk_uplift``,
    :meth:`RecoveryManager.plan_overlay` - so the rules about what a legal value is stay in exactly one
    place each. This function composes them; it does not re-implement any of them.
    """
    plan: dict[str, Any] = {
        "resource_limits": {},
        "limits_changes": {},
        "policy": {},
        "cognitive_decisions": [],
        "tool_preference": None,
        "tool_unknown": [],
        "risk_decisions": {},
        "recovery_decisions": [],
    }
    refused: list[str] = []
    if limits is not None:
        fields = getattr(type(limits), "__dataclass_fields__", {}) or {}
        overrides = overlay.resource_limit_overrides()
        baseline = dict(limits_defaults or {name: int(getattr(limits, name)) for name in fields})
        desired = {name: int(overrides.get(name, baseline.get(name, getattr(limits, name)))) for name in fields}
        for name in overrides:
            if name not in fields:
                refused.append(f"resource_limits.{name}: not a field of {type(limits).__name__}")
        try:
            trial = replace(limits, **desired)
        except Exception as exc:  # a consumer that cannot answer is a refusal, never a crashed cycle
            # Broad on purpose. The planning step runs inside every runtime cycle, and the failure modes
            # here are structural rather than semantic - a limits object that is not a dataclass, a
            # descriptor that validates on assignment, a field that became read-only. Any of them must
            # produce "this overlay was not adopted", which an operator can read in the ledger, rather
            # than an exception that takes the loop down while the overlay stays exactly as it was.
            refused.append(f"resource_limits: rejected by {type(limits).__name__} ({type(exc).__name__}: {exc})")
            trial = None
        if trial is not None:
            plan["limits_changes"] = {
                name: {"from": int(getattr(limits, name)), "to": desired[name]}
                for name in fields
                if int(getattr(limits, name)) != desired[name]
            }
            plan["resource_limits"] = desired
    if cognitive is not None or policy is not None:
        overrides = overlay.cognitive_policy_overrides()
        if cognitive is not None:
            defaults = dict(policy_defaults or getattr(cognitive, "DEFAULT_POLICY", None) or dict(cognitive.policy))
            decisions, problems = cognitive.plan_policy({**defaults, **overrides})
            refused.extend(f"policy.{item}" for item in problems)
            plan["policy"] = {**defaults, **overrides}
            plan["cognitive_decisions"] = decisions
        else:
            defaults = dict(policy_defaults or policy or {})
            for name in overrides:
                if name not in defaults:
                    refused.append(f"policy.{name}: not a policy field the orchestrator declares")
            plan["policy"] = {**defaults, **overrides}
    if tools is not None:
        document = overlay.document("config/tools.json")
        preference = document.get("preference")
        if preference:
            ordered, unknown = tools.plan_preference(list(preference))
            if unknown:
                # A preference naming a tool this build does not have means the candidate was measured
                # against a different capability set than the one being activated. Ignoring the name would
                # apply the rest, which is a half-overlay; refusing is legible in the ledger.
                refused.append("config/tools.json.preference: unknown tool(s): " + ", ".join(unknown))
            plan["tool_unknown"] = list(unknown)
            plan["tool_preference"] = ordered
        else:
            plan["tool_preference"] = None  # restore registration order
        # Planned even when the overlay names no uplift, for the same reason the limits leg merges over the
        # baseline: a tool left above its registered floor by a version that has just been withdrawn is a
        # capability the rollback did not remove. An empty uplift therefore means "restore every floor".
        uplift = dict(document.get("risk_floor_uplift") or {})
        decisions, problems = tools.plan_risk_uplift(uplift)
        refused.extend(f"config/tools.json.risk_floor_uplift.{item}" for item in problems)
        plan["risk_decisions"] = decisions
    if recovery is not None:
        decisions, problems = recovery.plan_overlay(overlay.recovery_overrides())
        refused.extend(f"recovery.{item}" for item in problems)
        plan["recovery_decisions"] = list(decisions)
    return plan, refused


def apply_overlays(
    overlay: ActiveOverlay,
    *,
    limits: Any = None,
    limits_defaults: dict[str, int] | None = None,
    policy: dict[str, int] | None = None,
    policy_defaults: dict[str, int] | None = None,
    cognitive: Any = None,
    tools: Any = None,
    recovery: Any = None,
) -> dict[str, Any]:
    """Apply the overlay to live objects as a *merge over the shipped defaults*, all legs or none.

    Three properties, each learned from a way the first version of this was wrong.

    **Idempotent.** A cycle applies the same overlay again, and a counter must not drift, so every target
    is computed from the overlay plus the baseline rather than from what is currently set.

    **Reset-capable.** Every default is a key in the merge, so a knob the overlay does not mention is
    written *back* to its default. Without that, a rollback leaves the promoted value in force until the
    process restarts - and "we rolled it back, the next cycle still behaves like the bad version" is
    precisely the failure a rollback exists to make impossible. The same rule is what makes ``A -> B ->
    C -> rollback -> B`` land on B's state rather than on some mixture of the three.

    **Atomic.** The legs are planned first; any refusal means nothing is committed. If a commit raises
    anyway (a consumer changed shape under us, a descriptor refused the write), the journal of inverse
    operations is unwound before returning. A half-applied overlay is the one outcome no later cycle can
    repair, because the next cycle re-plans from the defaults and would report the *other* half.

    Returns the applied view rather than mutating quietly: a cycle that changed its own budgets has to say
    so in the same breath, or the ledger and the behaviour can only be correlated by timestamps.
    """
    plan, refused = plan_overlays(
        overlay,
        limits=limits,
        limits_defaults=limits_defaults,
        policy=policy,
        policy_defaults=policy_defaults,
        cognitive=cognitive,
        tools=tools,
        recovery=recovery,
    )
    report: dict[str, Any] = {
        "resource_limits": {},
        "policy": {},
        "tool_preference": {},
        "risk_floors": {},
        "recovery": {},
        "refused": refused,
        "reset": [],
    }
    if refused:
        report["not_applied"] = True
        report["refused"] = refused
        return report
    undo: list[Any] = []
    try:
        if limits is not None:
            overrides = overlay.resource_limit_overrides()
            fields = getattr(type(limits), "__dataclass_fields__", {}) or {}
            for name in fields:
                desired = plan["resource_limits"][name]
                move = plan["limits_changes"].get(name)
                if move is None:
                    continue
                previous = int(getattr(limits, name))
                undo.append(lambda target=limits, key=name, value=previous: setattr(target, key, value))
                setattr(limits, name, desired)
                report["resource_limits"][name] = move
                if name not in overrides:
                    report["reset"].append(name)
        if cognitive is not None:
            policy_before = dict(getattr(cognitive, "policy", {}) or {})
            undo.append(lambda snapshot=policy_before: cognitive.apply_policy(snapshot))
            result = cognitive.apply_policy(plan["policy"])
            report["cognitive"] = result
            for name, move in (result.get("applied") or {}).items():
                report["policy"][name] = move
                if name not in overlay.cognitive_policy_overrides():
                    report["reset"].append(f"policy.{name}")
            report["refused"].extend(result.get("refused") or [])
        elif policy is not None:
            overrides = overlay.cognitive_policy_overrides()
            for name, desired in plan["policy"].items():
                if int(policy.get(name, desired)) == desired:
                    continue
                previous = policy.get(name)
                undo.append(lambda target=policy, key=name, value=previous: target.__setitem__(key, value))
                report["policy"][name] = {"from": previous, "to": desired}
                policy[name] = desired
                if name not in overrides:
                    report["reset"].append(f"policy.{name}")
        if tools is not None:
            preference = plan["tool_preference"]
            floors_before = dict(tools.risk_floors())
            order_before = list(tools.order())
            undo.append(lambda target=tools, order=order_before, floors=floors_before: _restore_tools(target, order, floors))
            unknown = tools.reorder(preference if preference else None)
            changes = dict(plan["risk_decisions"])
            uplifted = set(overlay.document("config/tools.json").get("risk_floor_uplift") or {})
            if changes:
                tools.apply_risk_uplift(changes)
            report["risk_floors"] = {name: move for name, move in changes.items() if name in uplifted}
            report["reset"].extend(
                f"risk_floor.{name}" for name, move in changes.items() if name not in uplifted
            )
            report["tool_preference"] = {
                "applied": list(preference or []),
                "unknown": list(unknown),
                "order": list(tools.order()),
                "restored": preference is None,
            }
        if recovery is not None:
            snapshot_before = sorted(item.value for item in recovery.never_retry_classes)
            undo.append(lambda target=recovery, values=snapshot_before: target.apply_overlay({"never_retry": values}))
            # No reset call here on purpose: the commit is already an assignment over the class floor, and
            # a "clear then apply" pair would leave the set empty if the apply leg raised in between.
            result = recovery.apply_overlay(overlay.recovery_overrides())
            report["recovery"] = result
            report["refused"].extend(result.get("refused") or [])
            report["reset"].extend(f"recovery.never_retry.{name}" for name in result.get("removed") or [])
    except Exception as exc:  # a consumer refused at write time; leave nothing behind
        for entry in reversed(undo):
            try:
                entry()
            except Exception:
                # An undo that itself fails is reported rather than hidden: the cycle then refuses the
                # overlay, and the residual state is visible in the reason string instead of looking clean.
                report["refused"].append(f"rollback of a partially applied leg also failed: {type(exc).__name__}")
                break
        report["refused"].append(f"application aborted before completion: {type(exc).__name__}: {exc}")
        report["not_applied"] = True
        for key in ("resource_limits", "policy", "tool_preference", "risk_floors", "recovery"):
            report[key] = {}
        report["reset"] = []
    return report


def _restore_tools(tools: Any, order: list[str], floors: dict[str, str]) -> None:
    """Undo a tool leg: the exact order it had, then the exact floors it had.

    The prior order is replayed rather than "restore the registration default", because the registry may
    legitimately have been shaped by something else before this cycle. Undo has to mean "the state I read
    at the start of this commit", not "the state I assume the process began with" - the second is a guess,
    and a wrong guess here is an agent whose tool view no version ever described.
    """
    if order:
        tools.reorder(list(order))
    tools.apply_risk_uplift({name: {"from": level, "to": level} for name, level in floors.items()})


    return report


def overlay_summary_for(overlay: ActiveOverlay) -> dict[str, Any]:
    """Convenience for the event payload; kept separate so a caller cannot pass a non-overlay."""
    return replace(overlay, fragments=()).to_dict()
