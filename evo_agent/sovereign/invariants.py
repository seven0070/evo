"""Live architectural invariants: checks that run inside the process, not only in pytest.

Ported semantics from DeepSeek Harness's invariant system, which is the one part of that
project worth importing wholesale (06 §3.1):

* a violation is **fatal** by default, not a logged warning — a test that only runs in CI
  cannot stop a promoted candidate from crossing a boundary mid-run;
* observers are **prepended**, so a later short-circuiting listener cannot silence a check;
* a subsystem may opt out of having a runtime invariant, but only by **stating a reason** —
  silence is treated as a coverage gap, not as compliance.

Everything here is source-level and stdlib-only, so it runs at startup before any model,
bridge, or sandbox exists. That is deliberate: the checks must work on a tree that is
currently broken in every other way.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass, field
import sys
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence

from .protected import PACKAGE_ROOT, PROTECTED_PATHS, verify as verify_sovereign_digests
from .eligibility import PROTECTED_COMPONENTS, consistency_with_sandbox, validate_registry


#: A subsystem may opt out of having a runtime invariant, but only in these words, with
#: a reason attached. Copied from DeepSeek Harness, where a package without a check must
#: declare "No runtime invariant" plus why (06 §3.1) - the point is that opting out is a
#: statement on the record, not an absence.
NO_RUNTIME_INVARIANT = "No runtime invariant:"


class InvariantError(RuntimeError):
    """A runtime invariant was crossed. Fatal by construction (``raise`` never returns)."""

    def __init__(self, code: str, detail: str) -> None:
        self.code = code
        self.detail = detail
        super().__init__(f"{code}: {detail}")


def invariant_failure(code: str, detail: str) -> None:
    """Raise :class:`InvariantError`. Modelled on ``InvariantFailure = (message) => never``."""
    raise InvariantError(code, detail)


@dataclass(frozen=True)
class InvariantResult:
    """Outcome of one check. ``ok`` is the only thing callers should branch on."""

    code: str
    rule: str
    ok: bool
    detail: str
    evidence: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"code": self.code, "rule": self.rule, "ok": self.ok, "detail": self.detail, "evidence": dict(self.evidence)}


Check = Callable[[Path], "tuple[bool, str, dict[str, Any]]"]


@dataclass(frozen=True)
class InvariantDef:
    """A check plus the rule it protects and the subsystems it covers."""

    code: str
    rule: str
    description: str
    check: Check | None
    fatal: bool = True
    #: Subpackage names (relative to ``evo_agent``) this check guards; "" is the root.
    covers: tuple[str, ...] = ("",)
    no_invariant_reason: str = ""
    #: Recorded defects that this check currently tolerates, each formatted
    #: ``"<file>:<token>"`` by the check itself. A check fails if it finds an offender
    #: that is not listed here, and also fails if an entry here is no longer needed - so
    #: the list can only shrink, and a fixed defect cannot quietly stay on the books.
    #: Startup may only afford checks that read a handful of files; the full registry runs
    #: in CI and at phase boundaries. A cheap check is still fatal.
    cheap: bool = False

    known_gaps: tuple[str, ...] = ()

    def run(self, root: Path) -> InvariantResult:
        if self.check is None:
            reason = self.no_invariant_reason.strip()
            if not reason:
                return InvariantResult(self.code, self.rule, False, "declared without a check and without a stated reason", {})
            if not reason.startswith(NO_RUNTIME_INVARIANT) or len(reason) <= len(NO_RUNTIME_INVARIANT) + 2:
                return InvariantResult(
                    self.code, self.rule, False,
                    f"opt-out must read '{NO_RUNTIME_INVARIANT} <reason>' (found: {reason!r})", {},
                )
            return InvariantResult(self.code, self.rule, True, f"no runtime invariant: {reason}", {"reason": reason})
        try:
            ok, detail, evidence = self.check(root)
        except Exception as exc:  # a broken check must never read as a pass
            ok, detail, evidence = False, f"check raised {type(exc).__name__}: {exc}", {}
        evidence = dict(evidence)
        if not ok:
            gaps = {str(item) for item in evidence.get("gaps", ())}
            tolerated = set(self.known_gaps)
            unexpected = sorted(gaps - tolerated)
            stale = sorted(tolerated - gaps)
            if not unexpected and not stale and gaps:
                return InvariantResult(
                    self.code, self.rule, True,
                    f"tolerated by recorded gap ({len(gaps)}): {detail}",
                    {**evidence, "known_gaps": sorted(tolerated)},
                )
            if unexpected:
                detail = f"{detail}; unrecorded offender(s): {', '.join(unexpected)}"
            if stale:
                detail = (
                    f"stale ratchet entry: {', '.join(stale)} no longer offends - remove it, "
                    "or the check will stop protecting the thing it fixed"
                )
        return InvariantResult(self.code, self.rule, bool(ok), detail, evidence)


# --- helpers kept private and small ----------------------------------------------------

_STDLIB = set(sys.stdlib_module_names)
#: Third-party modules that may be imported *inside a function*, i.e. only when the user
#: opted into an extra. Module-level imports of these are still a violation (R4).
#: The optional harnesses a *bridge* may import inside a function to discover what is installed.
#: They are allowed only there: a bridge imports its upstream to talk to it, and ``I-ports-contract``
#: is what keeps that import from becoming a dependency in the base install or a path to authority.
OPTIONAL_IMPORT_ALLOWLIST: frozenset[str] = frozenset({"openai", "langgraph", "deerflow", "deepseek_harness"})
#: Files that legitimately spawn processes. Growth of this set is the moment isolation
#: work must happen; the check fails when it grows (S1).
EXECUTION_SITE_ALLOWLIST: dict[str, str] = {
    "sandbox.py": "candidate isolation for the evolution spine",
    "benchmark.py": "isolated baseline/candidate trial execution",
    "promotion.py": "post-switch smoke probe inside an isolated version directory",
    "sandbox_providers/base.py": "provider self-tests; it builds no command from model input",
    "sandbox_providers/local_bwrap.py": "the isolation layer itself - confined spawn",
    "sandbox_providers/unshare.py": "the isolation layer itself - confined spawn",
    "sandbox_providers/host.py": "the explicitly permitted unconfined fallback, and the only place that may be unconfined",
    "backends/lead_agent.py": "one confined child process for a bridge turn; argv identity-checked by the mediator",
}
#: Detection markers, assembled so that this file's own source does not read as a
#: violation of the check it defines.
SQLITE_CONNECT_MARKER = "sqlite3" + ".connect("
DDL_MARKER = "CREATE " + "TABLE"
#: Model-facing methods that count as "asking the model", used by the loop detector.
MODEL_CALL_ATTRS = frozenset({"generate", "chat", "complete", "infer", "create_plan", "propose_action", "choose_recovery", "summarize"})



PERSISTENCE_AUTHORITY_ALLOWLIST: dict[str, str] = {
    "storage.py": "the persistence authority",
    "production.py": "ProductionSchemaManager owns its own schema bookkeeping",
}
#: The functions that may loop while dispatching tools - i.e. the agent loop itself.
#: Today that is exactly one, and the brief forbids a second (R2). Model-driven loops
#: (cognitive.run_goal, model_intelligence.infer) are not agent loops: they neither
#: select nor execute capabilities. The entry is stale the moment P4 unifies the loop
#: into the runtime turn engine, at which point this list must be re-declared in review.
TOOL_DISPATCH_LOOP_ALLOWLIST: dict[str, str] = {
    "kernel.py::run": "legacy plan-then-execute loop; unified into the turn engine by P4",
}
#: Directories that must never contain a loop at all: an adapter plugs into the loop, it
#: does not own one (R2). A new backend that needs iteration is the design error the brief
#: calls out - "do not create three competing agent loops".
#: Directories that must never contain a loop at all (R2). ``pipeline`` is on this list because the turn
#: pipeline is the *declared order* the loop follows; if it grew a loop of its own it would be a second
#: agent loop that also claims to be the specification of the first, which is the worst of both.
LOOP_FORBIDDEN_PACKAGES: tuple[str, ...] = ("backends", "pipeline", "ports", "sandbox_providers", "serve")
#: The exceptions, each with the bound that keeps it from being a second agent loop.
#:
#: What R2 forbids is an adapter that *selects and dispatches capabilities* in its own loop. A
#: bridge that must read its child's line protocol needs iteration to exist at all, and pretending
#: otherwise would only move that loop into a file the rule does not watch. So the ban stays and the
#: exceptions are declared: an undeclared loop fails, and so does a stale entry - a budget cannot
#: outlive the code that justified it.
ADAPTER_LOOP_BUDGETS: dict[str, str] = {
    "backends/lead_agent.py::_pump": "bounded by one turn deadline, a per-line byte ceiling, and the child's own final/error message; it never plans and never judges",
}


def _python_files(root: Path) -> list[Path]:
    return sorted(path for path in root.rglob("*.py") if "__pycache__" not in path.parts)


def _module_name(path: Path, root: Path) -> str:
    return str(path.relative_to(root)).replace("\\", "/")


def _imports(tree: ast.AST) -> list[tuple[str, bool]]:
    """(top-level module, is_module_level) for every import in the tree."""
    found: list[tuple[str, bool]] = []

    def walk(node: ast.AST, top: bool) -> None:
        for child in ast.iter_child_nodes(node):
            nested_top = top and isinstance(child, (ast.Import, ast.ImportFrom, ast.If, ast.Try))
            if isinstance(child, ast.Import):
                for alias in child.names:
                    found.append((alias.name.split(".")[0], nested_top))
            elif isinstance(child, ast.ImportFrom):
                if not child.level and child.module:
                    found.append((child.module.split(".")[0], nested_top))
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.If, ast.Try)):
                walk(child, nested_top)

    walk(tree, True)
    return found


def _check_import_purity(root: Path) -> tuple[bool, str, dict[str, Any]]:
    offenders: list[dict[str, Any]] = []
    for path in _python_files(root):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for module, is_top in _imports(tree):
            if module in _STDLIB or module == "evo_agent" or module.startswith("_"):
                continue
            if is_top or module not in OPTIONAL_IMPORT_ALLOWLIST:
                offenders.append({"file": _module_name(path, root), "module": module, "module_level": is_top})
    if offenders:
        return False, f"{len(offenders)} non-stdlib import(s); base install must stay dependency-free", {"offenders": offenders}
    return True, "no module-level third-party imports; optional extras are function-local only", {}


def _check_no_async(root: Path) -> tuple[bool, str, dict[str, Any]]:
    offenders = []
    for path in _python_files(root):
        if (path.parent / "serve").exists() and "serve" in path.parts:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, (ast.AsyncFunctionDef, ast.AsyncFor, ast.AsyncWith, ast.Await)):
                offenders.append({"file": _module_name(path, root), "node": type(node).__name__, "line": node.lineno})
    if offenders:
        return False, "async syntax outside evo_agent/serve/ would leak into the synchronous contract (06 §11.1)", {"offenders": offenders}
    return True, "evo_agent is fully synchronous outside serve/, so every port stays sync-by-contract", {}


def _dispatches_tools(function: ast.AST) -> bool:
    """True when a function calls a registered tool handler (``self.tools.execute``)."""
    for node in ast.walk(function):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "execute"
            and isinstance(node.func.value, ast.Attribute)
            and node.func.value.attr in {"tools", "registry", "tool_registry"}
        ):
            return True
    return False


def _check_single_loop(root: Path) -> tuple[bool, str, dict[str, Any]]:
    """One agent loop, and no loop at all inside an adapter (R2)."""
    present: set[str] = set()
    adapter_loops: list[dict[str, Any]] = []
    adapter_while_sites: set[str] = set()
    for path in _python_files(root):
        module = _module_name(path, root)
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef):
                continue
            if not any(isinstance(child, (ast.While, ast.For)) for child in ast.walk(node)):
                continue
            if _dispatches_tools(node):
                present.add(f"{module}::{node.name}")
            if module.split("/")[0] in LOOP_FORBIDDEN_PACKAGES and any(
                isinstance(child, ast.While) for child in ast.walk(node)
            ):
                key = f"{module}::{node.name}"
                adapter_while_sites.add(key)
                if key not in ADAPTER_LOOP_BUDGETS:
                    adapter_loops.append({"file": module, "function": node.name, "line": node.lineno})
    unlisted = sorted(present - set(TOOL_DISPATCH_LOOP_ALLOWLIST))
    stale = sorted(set(TOOL_DISPATCH_LOOP_ALLOWLIST) - present)
    problems: list[str] = []
    if unlisted:
        problems.append("a second tool-dispatch loop exists: " + ", ".join(unlisted))
    if stale:
        problems.append("allow-listed loop(s) no longer dispatch tools: " + ", ".join(stale) + " - redeclare the list")
    if adapter_loops:
        problems.append(
            "adapter/port code owns a loop: "
            + ", ".join(f"{item['file']}::{item['function']}" for item in adapter_loops)
        )
    stale_budgets = sorted(name for name in ADAPTER_LOOP_BUDGETS if name not in adapter_while_sites)
    if stale_budgets:
        problems.append(
            "declared adapter loop(s) no longer exist: " + ", ".join(stale_budgets) + " - remove the entry"
        )
    if problems:
        return False, "; ".join(problems), {
            "drivers": sorted(present),
            "unlisted": unlisted,
            "stale": stale,
            "adapter_loops": adapter_loops,
        }
    return True, (
        f"exactly one tool-dispatch loop ({', '.join(sorted(present))}); "
        + (
            f"{len(ADAPTER_LOOP_BUDGETS)} declared adapter loop budget(s), each bounded"
            if ADAPTER_LOOP_BUDGETS
            else "no loop in any adapter package"
        )
    ), {"drivers": sorted(present), "adapter_loop_budgets": dict(ADAPTER_LOOP_BUDGETS)}


def _check_execution_sites(root: Path) -> tuple[bool, str, dict[str, Any]]:
    found: dict[str, int] = {}
    shell_true: list[dict[str, Any]] = []
    for path in _python_files(root):
        module = _module_name(path, root)
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue
            base = node.func.value
            base_name = base.id if isinstance(base, ast.Name) else ""
            if base_name == "subprocess" and node.func.attr in {"run", "Popen", "call", "check_output", "check_call"}:
                found[module] = found.get(module, 0) + 1
                if any(keyword.arg == "shell" for keyword in node.keywords):
                    shell_true.append({"file": module, "line": node.lineno})
    unexpected = sorted(name for name in found if name not in EXECUTION_SITE_ALLOWLIST)
    stale = sorted(name for name in EXECUTION_SITE_ALLOWLIST if name not in found)
    if unexpected:
        return False, f"process spawning outside the allow-list: {', '.join(unexpected)}", {
            "files": found,
            "unexpected": unexpected,
            "gaps": [f"{name}:spawn" for name in unexpected],
        }
    if stale:
        return False, (
            "the execution allow-list is out of date: " + ", ".join(stale) + " no longer spawns processes; "
            "remove the entry so the boundary stays honest"
        ), {"stale": stale, "files": found}
    isolated_only = all(name.startswith("sandbox_providers/") for name in found) or not found
    if not shell_true:
        return True, "every spawn site is allow-listed and none uses the shell", {"files": found}
    if isolated_only:
        return True, "shell usage confined to isolation providers", {"shell_sites": shell_true}
    return False, (
        "a spawn site passes shell=True outside an isolation provider, so the argv rules "
        "are the only boundary (00 §B.7)"
    ), {
        "shell_sites": shell_true,
        "files": found,
        "gaps": [f"{item['file']}:shell" for item in shell_true],
    }


def _check_persistence_authority(root: Path) -> tuple[bool, str, dict[str, Any]]:
    offenders: list[dict[str, Any]] = []
    for path in _python_files(root):
        module = _module_name(path, root)
        text = path.read_text(encoding="utf-8")
        upper = text.upper()
        connects = SQLITE_CONNECT_MARKER in text
        ddl = DDL_MARKER in upper
        if (connects or ddl) and module not in PERSISTENCE_AUTHORITY_ALLOWLIST:
            offenders.append({"file": module, "create_table": ddl, "connects": connects})
    if offenders:
        return False, "a second persistence authority appeared beside SQLiteStore", {"offenders": offenders}
    return True, "SQLiteStore (plus its own schema manager) is the only persistence authority", {}


def _check_protected_digests(root: Path) -> tuple[bool, str, dict[str, Any]]:
    report = verify_sovereign_digests(root)
    return report.ok, report.summary(), report.to_dict()


def _check_protected_set_complete(root: Path) -> tuple[bool, str, dict[str, Any]]:
    named: list[str] = []
    for component in PROTECTED_COMPONENTS:
        owner = component.owner_module.split("/")[-1]
        if owner and owner not in PROTECTED_PATHS:
            named.append(f"{component.name}->{owner}")
    if named:
        return False, "protected components whose owning module is not in the byte set", {"gaps": named}
    return True, f"{len(PROTECTED_PATHS)} protected files cover every declared authority", {}


def _check_eligibility_coherence(root: Path) -> tuple[bool, str, dict[str, Any]]:
    defects = validate_registry() + consistency_with_sandbox()
    if defects:
        return False, "metamorphosis eligibility registry is inconsistent", {"defects": defects}
    return True, "every promotable target kind has a benchmark suite; the registry matches SandboxEngine", {}


#: What each seam package may not import. A bridge that could reach the promotion engine, the
#: memory store, or the sandbox directly would not be a bridge, it would be a second runtime; the
#: only authority any of them may consult is ``sovereign.mediation``.
SEAM_PACKAGES: tuple[str, ...] = ("ports", "backends", "sandbox_providers")
SEAM_FORBIDDEN_IMPORTS: dict[str, tuple[str, ...]] = {
    "ports": (
        "backends",
        "benchmark",
        "kernel",
        "memory",
        "metamorphosis",
        "promotion",
        "runtime",
        "sandbox",
        "security",
        "storage",
        "tools",
        "verification",
    ),
    "backends": ("benchmark", "kernel", "memory", "metamorphosis", "promotion", "runtime", "sandbox", "storage", "verification"),
    # The isolation layer confines; it does not decide. Importing the policy here would let the
    # mechanism and the authority merge, and then "was it allowed" and "was it confined" would be
    # answered by the same 200 lines of code that can be edited by neither.
    "sandbox_providers": ("benchmark", "kernel", "memory", "metamorphosis", "promotion", "runtime", "security", "sovereign", "storage", "tools", "verification"),
}
#: What makes a class an implementation of a seam port, and what must accompany it.
#:
#: Arity, not just presence: the port is the shape a caller uses, so a ``probe(self, name)`` is not
#: a ``probe()`` no matter what it is called. Requiring the *signature* to match is what stops a
#: router that forwards to backends from being mistaken for one of them, which is exactly the kind
#: of near-miss that a name-only check waves through and then trips at first use.
SEAM_SHAPE_RULES: dict[str, tuple[tuple[str, int], tuple[tuple[str, int], ...]]] = {
    "backends": (("run_turn", 1), (("probe", 0), ("plan_capability", 1))),
    "sandbox_providers": (("run", 1), (("probe", 0), ("prepare", 1))),
}


def _seam_imports(tree: ast.AST) -> list[str]:
    """Every imported module name in a file, including relative ones.

    ``_imports`` ignores relative imports because the purity check it serves is about third-party
    packages. Here a relative import is the *likely* form - ``from ..promotion import ...`` is how a
    bridge in ``backends/`` would reach an authority - so ignoring it would leave a door open in the
    one check whose whole job is closing doors.
    """
    found: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.append(node.module)
    return found


def _required_positional_arguments(definition: ast.FunctionDef) -> int:
    """How many arguments a caller must supply, excluding ``self``.``"""
    arguments = definition.args
    positional = list(getattr(arguments, "posonlyargs", [])) + list(arguments.args)
    if positional and positional[0].arg in {"self", "cls"}:
        positional = positional[1:]
    return len(positional) - len(arguments.defaults)


def _check_ports_contract(root: Path) -> tuple[bool, str, dict[str, Any]]:
    """The seams keep their shape: no authorities, no side doors, no second store.

    This is the mechanical form of the integration decision. Docs can say "DeerFlow is a
    capability, not an agent"; only a check that fails when ``backends/`` grows an import of
    ``promotion`` turns that sentence into an invariant that survives the next contributor.
    """
    offenders: list[dict[str, Any]] = []
    for path in _python_files(root):
        module = _module_name(path, root)
        package = module.split("/")[0]
        if package not in SEAM_PACKAGES:
            continue
        text = path.read_text(encoding="utf-8")
        tree = ast.parse(text, filename=str(path))
        forbidden = SEAM_FORBIDDEN_IMPORTS[package]
        for module_name in _seam_imports(tree):
            parts = module_name.split(".")
            if any(part in forbidden for part in parts):
                offenders.append({"file": module, "kind": "authority_import", "found": module_name})
        if "sqlite3" + ".connect(" in text or "CREATE " + "TABLE" in text.upper():
            offenders.append({"file": module, "kind": "second_persistence_authority"})
        if package in ("ports", "sandbox_providers") and "subprocess" in text:
            for node in ast.walk(tree):
                if isinstance(node, ast.Attribute) and node.attr in {"system", "popen", "execv", "execve"}:
                    offenders.append({"file": module, "kind": "raw_spawn", "found": node.attr})
        rule = SEAM_SHAPE_RULES.get(package)
        if rule is None:
            continue
        (trigger, trigger_budget), required = rule
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            methods = {item.name: item for item in node.body if isinstance(item, ast.FunctionDef)}
            candidate = methods.get(trigger)
            if candidate is None or _required_positional_arguments(candidate) > trigger_budget:
                continue
            missing: list[str] = []
            for name, budget in required:
                member = methods.get(name)
                if member is None:
                    missing.append(name)
                elif _required_positional_arguments(member) > budget:
                    missing.append(f"{name}(arity)")
            if missing:
                offenders.append({"file": module, "kind": "port_shape", "class": node.name, "missing": sorted(missing)})
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue
            base = node.func.value
            if isinstance(base, ast.Name) and base.id == "subprocess" and any(
                keyword.arg == "shell" for keyword in node.keywords
            ):
                offenders.append({"file": module, "kind": "shell_keyword", "line": node.lineno})
    if offenders:
        return (
            False,
            f"{len(offenders)} seam violation(s): ports/backends/sandbox_providers must not reach an authority, own a store, or grow a side door",
            {"offenders": offenders},
        )
    return (
        True,
        "seams carry no authority: no promotion/memory/store imports, one shape per port, no shell keyword",
        {"packages": list(SEAM_PACKAGES), "forbidden_imports": {key: list(value) for key, value in SEAM_FORBIDDEN_IMPORTS.items()}},
    )


def _subpackages(root: Path) -> set[str]:
    return {path.name for path in sorted(root.iterdir()) if path.is_dir() and (path / "__init__.py").is_file()}


def _check_invariant_coverage(root: Path) -> tuple[bool, str, dict[str, Any]]:
    """Every subpackage is either guarded by a check or carries a stated reason (DSH rule)."""
    covered: dict[str, list[str]] = {}
    for definition in REGISTRY:
        for target in definition.covers:
            covered.setdefault(target, []).append(definition.code)
        if definition.check is None and definition.no_invariant_reason.startswith(NO_RUNTIME_INVARIANT):
            for target in definition.covers:
                covered.setdefault(target, []).append(f"{definition.code}(reasoned)")
    gaps = sorted(name for name in _subpackages(root) if name not in covered and name != "__pycache__")
    if gaps:
        return False, "subpackages with neither an invariant nor a stated reason", {"gaps": gaps, "coverage": covered}
    return True, f"all {len(_subpackages(root))} subpackages covered or reasoned", {"coverage": covered}


def _check_ownership_boundary(root: Path) -> tuple[bool, str, dict[str, Any]]:
    """The P5 boundary claim: one owner per capability, pinned upstreams, and nothing vendored.

    The table in :mod:`evo_agent.upstream` is the only place this build states who decides each
    capability, and a table nobody checks becomes prose. Three failures are folded into one invariant
    because they are the same failure seen from different sides: a capability with two authorities, a
    "sovereign" row that no protected code enforces, and an upstream component present as a copy rather
    than as an accepted surface (07 §4, §8 P5; ``06`` rejected-tree list).
    """
    from ..upstream import boundary_problems, report as boundary_report, upstream_problems

    problems = boundary_problems() + upstream_problems(root if root.exists() else None)
    if problems:
        return False, "ownership or upstream pins are not coherent", {"problems": problems}
    payload = boundary_report()
    return True, f"{len(payload['ownership'])} capabilities owned, {len(payload['components'])} upstream components pinned", {
        "never_candidate": payload["never_candidate"],
        "components": [
            {"name": item["name"], "ref": item["pinned_ref"], "integration": item["integration"]}
            for item in payload["components"]
        ],
    }


#: The registry. Ordered so that cheap, absolute guarantees are evaluated first;
#: ``enforce_invariants`` reports in this order and never re-sorts.
REGISTRY: tuple[InvariantDef, ...] = (
    InvariantDef(
        code="I-sovereign-digest",
        rule="R1",
        description="the protected byte set matches its published manifest",
        check=_check_protected_digests,
        covers=("sovereign",),
        cheap=True,
    ),
    InvariantDef(
        code="I-sovereign-coverage",
        rule="R1",
        description="every protected component's owning module is itself protected",
        check=_check_protected_set_complete,
        covers=("sovereign",),
        cheap=True,
    ),
    InvariantDef(
        code="I-import-purity",
        rule="R4",
        description="the base install stays dependency-free; extras are function-local",
        check=_check_import_purity,
    ),
    InvariantDef(
        code="I-sync-contract",
        rule="R2",
        description="no async syntax outside serve/, so ports stay synchronous by contract",
        check=_check_no_async,
    ),
    InvariantDef(
        code="I-single-loop",
        rule="R2",
        description="exactly one loop may dispatch tools; adapters never loop",
        check=_check_single_loop,
        covers=("kernel", "pipeline"),
    ),
    InvariantDef(
        code="I-exec-isolation",
        rule="S1",
        description="spawn sites are allow-listed and free of shell=True",
        check=_check_execution_sites,
    ),
    InvariantDef(
        code="I-persistence-authority",
        rule="R3",
        description="SQLiteStore remains the only persistence authority",
        check=_check_persistence_authority,
    ),
    InvariantDef(
        code="I-eligibility-coherence",
        rule="R10",
        description="promotable target kinds are benchmarked and match the engine",
        check=_check_eligibility_coherence,
    ),
    InvariantDef(
        code="I-ownership-boundary",
        rule="R9",
        description="one owner per capability, every claim enforced by protected code, upstream components pinned and not vendored",
        check=_check_ownership_boundary,
        covers=("sovereign", "upstream"),
    ),
    InvariantDef(
        code="I-ports-contract",
        rule="R7",
        description="bridges and providers stay seams: no authority imports, no side doors, port shape intact",
        check=_check_ports_contract,
        covers=("ports", "backends", "sandbox_providers"),
        cheap=True,
    ),
    InvariantDef(
        code="I-invariant-coverage",
        rule="R9",
        description="every subpackage is guarded or carries a stated reason",
        check=_check_invariant_coverage,
    ),
)


@dataclass(frozen=True)
class InvariantConfig:
    """Selection semantics ported from DSH: enabled flag plus allow/block lists.

    A block list may demote a check to non-fatal for debugging, but it may not remove the
    protected-digest or single-loop checks: those are what every other guarantee assumes.
    """

    enabled: bool = True
    allowlist: frozenset[str] = frozenset()
    blocklist: frozenset[str] = frozenset()

    NON_BLOCKABLE: tuple[str, ...] = ("I-sovereign-digest", "I-single-loop", "I-exec-isolation")

    def select(self, definitions: Sequence[InvariantDef]) -> list[InvariantDef]:
        if not self.enabled:
            return []
        selected = list(definitions)
        if self.allowlist:
            selected = [item for item in selected if item.code in self.allowlist or item.code in self.NON_BLOCKABLE]
        if self.blocklist:
            selected = [item for item in selected if item.code not in self.blocklist or item.code in self.NON_BLOCKABLE]
        return selected


@dataclass
class InvariantObserver:
    """A prepended listener for turn/cycle boundaries (DSH's prepend rule).

    Observers registered here run before any consumer-attached handler, so a handler that
    short-circuits cannot silence a check. Keep the callbacks cheap and side-effect free:
    they run on the loop's own thread.
    """

    _hooks: list[Callable[[str, dict[str, Any]], None]] = field(default_factory=list)

    def prepend(self, hook: Callable[[str, dict[str, Any]], None]) -> None:
        self._hooks.insert(0, hook)

    def attach(self, hook: Callable[[str, dict[str, Any]], None]) -> None:
        self._hooks.append(hook)

    def notify(self, event: str, payload: dict[str, Any] | None = None) -> None:
        for hook in list(self._hooks):
            hook(event, dict(payload or {}))

    @property
    def hook_count(self) -> int:
        return len(self._hooks)


def run_invariants(
    root: Path | None = None,
    *,
    config: InvariantConfig | None = None,
    only: Iterable[str] | None = None,
    cheap_only: bool = False,
) -> list[InvariantResult]:
    """Evaluate the registry against ``root`` (default: the installed ``evo_agent``)."""
    target = Path(root) if root is not None else PACKAGE_ROOT
    definitions = (config or InvariantConfig()).select(REGISTRY)
    if cheap_only:
        definitions = [item for item in definitions if item.cheap]
    if only is not None:
        wanted = set(only)
        definitions = [item for item in definitions if item.code in wanted or item.code in (config or InvariantConfig()).NON_BLOCKABLE]
    return [definition.run(target) for definition in definitions]


def enforce_invariants(
    root: Path | None = None,
    *,
    config: InvariantConfig | None = None,
    observer: InvariantObserver | None = None,
    cheap_only: bool = False,
) -> list[InvariantResult]:
    """Run every selected check and raise on the first fatal failure (R7)."""
    results = run_invariants(root, config=config, cheap_only=cheap_only)
    failures = [item for item in results if not item.ok]
    if observer is not None:
        observer.notify("invariants:checked", {"checked": len(results), "failed": len(failures)})
    if failures:
        first = failures[0]
        invariant_failure(first.code, first.detail)
    return results


def format_report(results: Sequence[InvariantResult]) -> str:
    if not results:
        return "invariants: disabled by configuration"
    width = max(len(item.code) for item in results)
    lines = [f"{'code'.ljust(width)}  rule  status  detail"]
    for item in results:
        status = "ok" if item.ok else "FAIL"
        lines.append(f"{item.code.ljust(width)}  {item.rule:5}  {status:5}  {item.detail}")
    failures = sum(1 for item in results if not item.ok)
    lines.append(f"{len(results)} checks, {failures} failing")
    return "\n".join(lines)


def invariant_registry() -> dict[str, Any]:
    return {
        "checks": [
            {
                "code": item.code,
                "rule": item.rule,
                "description": item.description,
                "fatal": item.fatal,
                "covers": list(item.covers),
                "cheap": item.cheap,
                "known_gaps": list(item.known_gaps),
                "live": item.check is not None,
                "no_invariant_reason": item.no_invariant_reason,
            }
            for item in REGISTRY
        ],
        "execution_site_allowlist": dict(EXECUTION_SITE_ALLOWLIST),
        "tool_dispatch_loop_allowlist": dict(TOOL_DISPATCH_LOOP_ALLOWLIST),
    "loop_forbidden_packages": list(LOOP_FORBIDDEN_PACKAGES),
        "persistence_authority_allowlist": dict(PERSISTENCE_AUTHORITY_ALLOWLIST),
        "optional_import_allowlist": sorted(OPTIONAL_IMPORT_ALLOWLIST),
    }
