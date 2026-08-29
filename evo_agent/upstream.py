"""Who owns what, and which upstream tree was *not* copied.

Two tables, one file, because P5 kept producing the same class of bug from two directions. The first
direction is a component integrated by accident: an upstream repository gets vendored "temporarily", and
the repo ends up with two sandbox providers, two session stores, or two agent loops - the founding
condition of this whole programme (00 §A). The second is a capability with no stated owner: a capability
whose authority is "whoever wrote it last" is a capability an evolution candidate can quietly acquire.

So both tables are machine-readable and both are checked at startup, in
``sovereign/invariants.py:I-ownership-boundary``. A row is only as good as its ``authority`` string,
which is why that string is resolved by import rather than trusted as prose: a claim that
"``evo_agent.verifier`` owns verification" is worth nothing if the module is not importable or the
attribute does not exist, and a boundary that cannot be checked is a comment.

Nothing here imports DeerFlow or DeepSeek Harness. The pins below record *what was reviewed and accepted*
from each, at a ref, so the accepted surface can be re-checked when upstream moves; the review is the
product, not the copy.
"""

from __future__ import annotations

import importlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

#: The capabilities that may never be a candidate's to change, whatever else the table says. Kept as a
#: separate constant so the check below can refuse a *new* row that relaxes one of them by mistake: a
#: table with one honest column and no cross-check is a table that will be edited by the next person in
#: a hurry.
NEVER_CANDIDATE: tuple[str, ...] = (
    "agent-loop",
    "persistence",
    "verification",
    "approval-mediation",
    "isolation-policy",
    "promotion",
    "rollback",
    "audit-record",
    "emergency-shutdown",
    "protected-source",
    "tool-identity",
    "secrets",
)


@dataclass(frozen=True)
class UpstreamComponent:
    """One external repository this build takes something from, and what it took."""

    name: str
    repository: str
    #: A tag is preferred to a branch: ``v2.1.0`` cannot move under a reviewer, ``main`` can, and the
    #: whole value of a pin is that the bytes reviewed are the bytes relied on.
    pinned_ref: str
    ref_kind: str
    licence: str
    #: ``bridge`` = a live process seam at a named entry point; ``adapter`` = our code shaped to their
    #: protocol. Neither means "we have their tree".
    integration: str
    #: The Evo-side modules that hold the accepted surface. A component with no ``accepted_by`` is an
    #: unread download, and a component whose ``accepted_by`` cannot be imported is a stale note.
    accepted_by: tuple[str, ...] = ()
    #: Paths that must not exist, because their existence would mean the component was vendored.
    must_not_exist: tuple[str, ...] = ()
    #: Always False in this build, and :func:`upstream_problems` refuses a row that says otherwise. The
    #: field exists so that "vendored" is a claim someone has to type, rather than a state the directory
    #: tree drifts into.
    vendored: bool = False
    #: Upstream requirements that gate deeper integration, recorded so the next reader does not rediscover
    #: them (06 §DeerFlow). Informational: nothing here is importable at install time.
    blocked_by: tuple[str, ...] = ()
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "repository": self.repository,
            "pinned_ref": self.pinned_ref,
            "ref_kind": self.ref_kind,
            "licence": self.licence,
            "integration": self.integration,
            "accepted_by": list(self.accepted_by),
            "must_not_exist": list(self.must_not_exist),
            "blocked_by": list(self.blocked_by),
            "notes": self.notes,
        }


UPSTREAM: tuple[UpstreamComponent, ...] = (
    UpstreamComponent(
        name="deer-flow",
        repository="bytedance/deer-flow",
        pinned_ref="v2.1.0",
        ref_kind="tag",
        licence="MIT",
        integration="bridge",
        accepted_by=(
            "evo_agent.backends.lead_agent:LeadAgentBackend",
            "evo_agent.skills:SkillCatalog",
            "evo_agent.skills:SkillInstaller",
            "evo_agent.tools:ToolCatalog",
        ),
        must_not_exist=(
            "deer-flow",
            "vendor/deer-flow",
            "third_party/deer-flow",
            "skills/public",
        ),
        blocked_by=(
            "requires-python>=3.12",
            "starlette>=1.3.1,<2 (private starlette._utils import)",
            "langgraph-sdk",
            "e2b-code-interpreter",
        ),
        notes=(
            "Accepted: the lead-agent bridge (one entry point, one exit code contract, no second loop) "
            "and the hardening rules for skill install, enabled-only projection, canonical-name tool "
            "policy and fail-closed scanning. Rejected: their persistence layer and workspace-change "
            "tracker, both of which duplicate an Evo authority that already exists. Their public skill "
            "corpus is not copied; it is a benchmark corpus they own"
        ),
    ),
    UpstreamComponent(
        name="deepseek-harness",
        repository="deepseek-ai/deepseek-harness",
        pinned_ref="master",
        ref_kind="branch",
        licence="MIT",
        integration="adapter",
        accepted_by=(
            "evo_agent.backends.dsh:DeepSeekHarnessBackend",
            "evo_agent.sovereign.invariants:REGISTRY",
            "evo_agent.pipeline.engine:TurnPipeline",
        ),
        must_not_exist=(
            "deepseek-harness",
            "vendor/deepseek-harness",
            "packages/session",
        ),
        blocked_by=(
            "pnpm workspace monorepo (TypeScript; not importable from Python)",
            "259 distinct npm dependencies",
            "landlock-run ships prebuilt binaries only",
        ),
        notes=(
            "Accepted as a *pattern*, run through our own code: invariants that fail the process rather "
            "than log, a package that declares 'no runtime invariant' with a stated reason (the rule "
            "``I-invariant-coverage`` follows), one identity builder for assembly filtering and the "
            "call-time guardrail, and the tool-edge ordering that puts sanitisation outermost. Rejected: "
            "their agent loop as a second loop, and their session store as a second persistence authority. "
            "Their SAFETY.md says the sandbox is not security-audited and must not be the sole control - "
            "which is why ``landlock-run`` is one provider among several behind Evo's mediation, not the "
            "boundary itself"
        ),
    ),
)


@dataclass(frozen=True)
class CapabilityOwner:
    """One capability, the one place its behaviour is decided, and whether a candidate may move it."""

    capability: str
    #: ``sovereign`` means the decision is made by protected code; ``operator`` means it is a startup
    #: choice a candidate cannot reach; ``agent`` means the agent may produce data for it but not decide it.
    owner: str
    #: ``module:attribute``. Resolved by import in :func:`boundary_problems`, so it cannot rot silently.
    authority: str
    #: True when a promoted overlay may change the *values* this capability runs on. Never means the
    #: candidate may change the authority, the checks, or the record of either.
    candidate_may_change: bool
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "capability": self.capability,
            "owner": self.owner,
            "authority": self.authority,
            "candidate_may_change": self.candidate_may_change,
            "reason": self.reason,
        }

    @property
    def authority_module(self) -> str:
        return self.authority.split(":", 1)[0]

    @property
    def authority_attribute(self) -> str:
        return self.authority.split(":", 1)[1] if ":" in self.authority else ""


OWNERSHIP: tuple[CapabilityOwner, ...] = (
    # -- the absolutes: owner sovereign, never a candidate's to move -------------------------------
    CapabilityOwner(
        "agent-loop",
        "sovereign",
        "evo_agent.runtime:AgentRuntime",
        False,
        "one authoritative loop; a backend or a harness may serve a turn, none may own a second loop (R2)",
    ),
    CapabilityOwner(
        "persistence",
        "sovereign",
        "evo_agent.storage:SQLiteStore",
        False,
        "one store; an upstream session layer was rejected rather than reconciled (I-persistence-authority)",
    ),
    CapabilityOwner(
        "verification",
        "sovereign",
        "evo_agent.verifier:Verifier",
        False,
        "one verification authority, so a candidate can never be judged by a judge it chose",
    ),
    CapabilityOwner(
        "approval-mediation",
        "sovereign",
        "evo_agent.sovereign.mediation:ApprovalMediator",
        False,
        "the sole ApprovalMediator; a bridge may raise an ask, never settle one",
    ),
    CapabilityOwner(
        "isolation-policy",
        "sovereign",
        "evo_agent.security:SecurityPolicy",
        False,
        "clamped, not validated: a configuration typo must not widen a ceiling (R6), and a downgrade below the shipped provider is refused (R7)",
    ),
    CapabilityOwner(
        "sandbox-execution",
        "sovereign",
        "evo_agent.sandbox:SandboxEngine",
        False,
        "the engine decides what may be staged and isolated; a candidate may add a *target kind* it can build, not a way around the engine",
    ),
    CapabilityOwner(
        "promotion",
        "sovereign",
        "evo_agent.promotion:PromotionEngine",
        False,
        "promotion is the only activation path; an installer, loader, or hook that could activate would be a second one with fewer readers",
    ),
    CapabilityOwner(
        "rollback",
        "sovereign",
        "evo_agent.active_version:resolve",
        False,
        "rollback re-points the active version and re-verifies the activation record; no consumer is allowed to keep its own copy of 'what is current'",
    ),
    CapabilityOwner(
        "audit-record",
        "sovereign",
        "evo_agent.runtime:AgentRuntime._emit",
        False,
        "every state change is an event in the store; a capability that records elsewhere is a capability that can be denied later",
    ),
    CapabilityOwner(
        "emergency-shutdown",
        "sovereign",
        "evo_agent.runtime:ShutdownManager",
        False,
        "the kill switch stays reachable from outside the loop, which is the only reason it is worth having",
    ),
    CapabilityOwner(
        "protected-source",
        "sovereign",
        "evo_agent.sovereign.protected:PROTECTED_PATHS",
        False,
        "adding a file to the protected set is always allowed; removing one needs a re-published manifest in the same reviewed change",
    ),
    CapabilityOwner(
        "tool-identity",
        "sovereign",
        "evo_agent.tools:ToolCatalog",
        False,
        "canonical names and reviewed aliases only; a skill or a bridge may spell a name differently, not invent one",
    ),
    CapabilityOwner(
        "secrets",
        "sovereign",
        "evo_agent.skills:SkillCatalog",
        False,
        "a skill names the credential it needs; an operator grants autonomous use; a prompt never carries a value",
    ),
    # -- tunable data, decided by protected code ------------------------------------------------------
    CapabilityOwner(
        "memory-policy",
        "sovereign",
        "evo_agent.memory:MemoryPolicy",
        True,
        "ranking weights only; retention and staleness stay operator-only because expiring rows is not reversible",
    ),
    CapabilityOwner(
        "memory-contents",
        "agent",
        "evo_agent.memory:MemoryManager",
        False,
        "the agent writes memory; no evolution payload is a memory row, and no candidate may rewrite one (03 §E)",
    ),
    CapabilityOwner(
        "skill-bundles",
        "sovereign",
        "evo_agent.materialization:SkillMaterializer",
        True,
        "the bundle's text is a promotable document; the loader, the installer's refusals, and the read-only mount are not",
    ),
    CapabilityOwner(
        "planning-heuristics",
        "sovereign",
        "evo_agent.pipeline.engine:TurnPipeline",
        True,
        "eight reviewed numeric knobs; ordering functions and stage source are unmaterializable by construction",
    ),
    CapabilityOwner(
        "runtime-limits",
        "sovereign",
        "evo_agent.active_version:plan_overlays",
        True,
        "budgets are overlay-writable and clamped; a value outside the ceiling is refused before it is applied",
    ),
    CapabilityOwner(
        "strategy-selection",
        "sovereign",
        "evo_agent.active_version:STRATEGY_NAMES",
        True,
        "a closed list of reviewed strategies, not an open registry: a preference may be promoted, a new strategy is a code change",
    ),
    CapabilityOwner(
        "provider-config",
        "sovereign",
        "evo_agent.materialization:ProviderConfigMaterializer",
        False,
        "a permanent refusal for prompt text and an allow-list for provider settings; prompt-authored guardrail changes are out of scope (03 §E)",
    ),
    CapabilityOwner(
        "backend-routing",
        "operator",
        "evo_agent.backends:build_default_registry",
        False,
        "the loop and backend family are a launch decision (``--agent-loop``, ``--backend``); a candidate may not choose them, because that is choosing what the benchmark measured",
    ),
    CapabilityOwner(
        "evolution-targets",
        "sovereign",
        "evo_agent.sovereign.eligibility:TARGET_KINDS",
        False,
        "what may evolve is itself not evolvable by the thing evolving",
    ),
    CapabilityOwner(
        "benchmark-requirements",
        "sovereign",
        "evo_agent.benchmark:BenchmarkEngine",
        False,
        "PROMOTABLE implies BENCHMARKABLE, and the suite list is decided where it is checked",
    ),
)

#: Filled in by :func:`report` when a row's authority resolves to nothing; kept out of the frozen table so
#: a typo shows up as a failure rather than as a missing entry someone has to diff.
_UNRESOLVED = "unresolved"


def authority_exists(authority: str) -> tuple[bool, str]:
    """Whether ``module:attribute`` can actually be imported and read right now.

    The attribute may be dotted (``Class.method``), because a capability is sometimes owned by a *method*
    rather than by a class - "the audit record" is a decision about one function's reachability, and
    pointing at the module instead would let an owner be "the file that happens to contain it".
    """
    module_name, _, attribute = str(authority or "").partition(":")
    if not module_name:
        return False, f"{authority!r} is not a 'module:attribute' reference"
    try:
        found: Any = importlib.import_module(module_name)
    except Exception as exc:  # noqa: BLE001 - ImportError, and anything a module-level raise may be
        return False, f"{module_name} could not be imported ({type(exc).__name__}: {exc})"
    for part in (item for item in attribute.split(".") if item):
        if not hasattr(found, part):
            return False, f"{module_name} has no attribute {part!r} (from {authority!r})"
        found = getattr(found, part)
    return True, ""


def boundary_problems(ownership: tuple[CapabilityOwner, ...] | None = None) -> list[str]:
    """Every way the ownership table is not telling the truth."""
    rows = OWNERSHIP if ownership is None else tuple(ownership)
    protected = protected_module_names()
    dependents = protected_dependents()
    row_notes: dict[str, list[str]] = {}
    problems: list[str] = []
    seen: dict[str, list[CapabilityOwner]] = {}
    for row in rows:
        seen.setdefault(row.capability, []).append(row)
        if row.owner not in {"sovereign", "operator", "agent"}:
            problems.append(f"{row.capability}: unknown owner {row.owner!r}")
        if ":" not in row.authority:
            # A row that names only a file has picked a *neighbourhood*, not an owner; the whole point of
            # the column is that someone can be asked what they guard.
            problems.append(f"{row.capability}: {row.authority!r} is not a 'module:attribute' reference")
        ok, why = authority_exists(row.authority)
        if not ok:
            problems.append(f"{row.capability}: {why}")
        if row.capability in NEVER_CANDIDATE and row.candidate_may_change:
            problems.append(
                f"{row.capability}: a non-negotiable capability is marked candidate-writable; if that is intended, "
                "the protected set and this table have to change together, not this line alone"
            )
        if row.owner == "sovereign":
            guarded, via = _authority_is_guarded(row, protected, dependents)
            if not guarded:
                problems.append(
                    f"{row.capability}: owner is 'sovereign', but {row.authority.split(':', 1)[0]} is neither in the "
                    "protected byte set nor imported by anything in it - so no protected code enforces the claim"
                )
            row_notes.setdefault(row.capability, []).append(via)
    for capability, duplicates in sorted(seen.items()):
        if len(duplicates) > 1:
            problems.append(
                f"{capability}: {len(duplicates)} rows claim one owner; a capability with two authorities has neither"
            )
    missing = sorted(set(NEVER_CANDIDATE) - set(seen))
    if missing:
        problems.append("the following capabilities are non-negotiable but have no row: " + ", ".join(missing))
    return problems


def protected_module_names() -> frozenset[str]:
    """The ``evo_agent.*`` modules in the protected byte set."""
    try:
        from .sovereign.protected import PROTECTED_PATHS
    except Exception:  # pragma: no cover - a build without the guard is itself the failure
        return frozenset()
    return frozenset(f"evo_agent.{item[:-3].replace('/', '.')}" for item in PROTECTED_PATHS if item.endswith(".py"))


def protected_dependents() -> frozenset[str]:
    """Everything a protected module imports, resolved to ``evo_agent.*`` names.

    "Sovereign-owned" cannot mean "the file is read-only", because the protected set is deliberately
    small and a boundary is usually enforced *above* the thing it clamps: ``pipeline/engine.py`` is not
    in the byte set, but the clamps that make it harmless - the eight reviewed knobs, the ceilings in
    :class:`SecurityPolicy`, the materializer that refuses an ordering function - are. What must be true
    is that protected code *reaches* the authority, so the claim in this table is checkable rather than
    rhetorical: if no protected module imports it, nobody enforces it, and the row is a description.
    """
    import ast

    names: set[str] = set()
    try:
        from .sovereign.protected import PACKAGE_ROOT, PROTECTED_PATHS
    except Exception:  # pragma: no cover
        return frozenset()
    for relative in PROTECTED_PATHS:
        path = PACKAGE_ROOT / relative
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (OSError, SyntaxError):
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                if node.level == 0:
                    if node.module and node.module.split(".")[0] == "evo_agent":
                        names.add(node.module)
                    continue
                prefix = "evo_agent" if node.level == 1 else "evo_agent.sovereign"
                if relative.startswith("sovereign/"):
                    prefix = "evo_agent.sovereign" if node.level == 1 else "evo_agent"
                if node.module:
                    names.add(f"{prefix}.{node.module}")
                if node.module is None:
                    # ``from . import sandbox``: here the alias *is* the submodule, and the only case
                    # where reading names rather than the module is the right thing to do.
                    for alias in node.names:
                        names.add(f"{prefix}.{alias.name}")
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.split(".")[0] in {"evo_agent", "evo"}:
                        names.add(alias.name)
    return frozenset(sorted(names))


def _authority_is_guarded(row: CapabilityOwner, protected: frozenset[str], dependents: frozenset[str]) -> tuple[bool, str]:
    name = row.authority_module
    if not name.startswith("evo_agent."):
        return True, ""  # outside the package: nothing here can protect or claim it
    if name in protected:
        return True, ""
    for dependent in sorted(dependents):
        if dependent == name or dependent.startswith(name + ".") or name.startswith(dependent + "."):
            return True, dependent
    return False, ""


def upstream_problems(root: Path | None = None, components: tuple[UpstreamComponent, ...] | None = None) -> list[str]:
    """Every way the pin table is not telling the truth - including that something was vendored."""
    components = UPSTREAM if components is None else tuple(components)
    base = Path(root) if root is not None else Path(__file__).resolve().parents[1]
    problems: list[str] = []
    names: set[str] = set()
    for component in components:
        if component.name in names:
            problems.append(f"{component.name}: recorded twice")
        names.add(component.name)
        if not component.accepted_by:
            problems.append(f"{component.name}: no accepted_by, so nothing here says what the review produced")
        if component.ref_kind not in {"tag", "commit", "branch"}:
            problems.append(f"{component.name}: unknown ref kind {component.ref_kind!r}")
        if component.ref_kind == "branch" and _flag_branch_pins():
            # Off by default, and recorded either way: a branch pin is legitimate while an upstream is
            # pre-release, and the price is only that the reviewed bytes can move. Refusing to start over
            # that would push an operator to delete the check, which is the worse outcome - so the
            # default is a line in the status report, and EVO_REQUIRE_TAG_PINS makes it a problem.
            problems.append(
                f"{component.name}: pinned to a branch ({component.pinned_ref}), so the reviewed surface is not fixed; "
                "re-read it before accepting a candidate that depends on it"
            )
        for module_spec in component.accepted_by:
            ok, why = authority_exists(module_spec)
            if not ok:
                problems.append(f"{component.name}: {why}")
        if component.vendored:
            problems.append(f"{component.name}: marked vendored, which this build does not do")
        for relative in component.must_not_exist:
            if (base / relative).exists():
                problems.append(f"{component.name}: {relative} exists, which means this component was copied rather than adapted")
    return [item for item in problems if item]


def _flag_branch_pins() -> bool:
    """Whether a branch pin is an error rather than a note.

    Off by default: a pre-release upstream genuinely does move, and refusing to start over it would push
    an operator to delete the check. The status report says it either way, which is the part that matters.
    """
    import os

    return os.environ.get("EVO_REQUIRE_TAG_PINS", "") in {"1", "true", "yes"}


def report(root: Path | None = None) -> dict[str, Any]:
    """The two tables, plus the verdicts. What ``evo status`` shows and what the audit records."""
    problems = boundary_problems() + upstream_problems(root)
    return {
        "ok": not problems,
        "problems": problems,
        "components": [component.to_dict() for component in UPSTREAM],
        "ownership": [row.to_dict() for row in OWNERSHIP],
        "never_candidate": list(NEVER_CANDIDATE),
    }


__all__ = [
    "NEVER_CANDIDATE",
    "OWNERSHIP",
    "UPSTREAM",
    "CapabilityOwner",
    "UpstreamComponent",
    "authority_exists",
    "boundary_problems",
    "report",
    "upstream_problems",
]
