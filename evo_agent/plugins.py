"""The extension inventory: what is installed, what it claimed, and what it is allowed to do (07 §3, :99).

DeerFlow keeps skills and tools in one catalog and lets a ``tool_policy`` clamp a request down to canonical
names. Evo refuses instead of clamping, because the inventory's job here is not to resolve names - it is to
answer a governance question with a record: *who approved this, and what did it claim it would do*. So this
module holds the lifecycle machine and the refusals, and it deliberately holds no importer.

The load-bearing asymmetry, which the fixtures under ``tests/fixtures/plugins/`` each embody:

* A verification plugin may only **tighten**. That rule is not implemented here - it lives in
  :meth:`evo_agent.verifier.Verifier._finish`, the one place a verdict is assembled, and the inventory
  refuses a plugin that *claims* the other direction rather than offering a knob to try.
* ``executable_code`` is **deferred, gated** (07 :189, Q7): the entry point is allowed to name a module under
  ``plugins/`` and never under ``sovereign/``, and registering it does not import it. Nothing in this build
  turns a registered name into executed code, so the risk the deferral guards against cannot be realised by a
  bug in this module.
* No persistence table. A plugin's authority is its registration in the config an operator reviewed; the
  inventory is rebuilt from that at every boot. A row saying ``active`` in the database would only ever be a
  stale copy of that truth, and stale copies of authority are how a retired extension keeps working.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Iterable, Sequence


class PluginLifecycle(str, Enum):
    CANDIDATE = "candidate"
    ACTIVE = "active"
    QUARANTINED = "quarantined"
    RETIRED = "retired"


class PluginKind(str, Enum):
    HOOK = "hook"
    VERIFICATION = "verification"
    RESEARCH = "research"
    #: Registered for the record only. See ``CODE_REGISTRATION_REFUSAL``: naming a module is an inventory
    #: entry in this build, not a call.
    EXECUTABLE_CODE = "executable_code"


#: Verbatim reason for the deferral, kept next to the code that enforces it so the two cannot drift.
CODE_REGISTRATION_REFUSAL = (
    "executable plugin code is deferred to 2.1 and gated behind a plugin-isolation benchmark suite "
    "(07 :189, Q7); the entry point is recorded for review and never imported by this build"
)

#: Claims that would make a plugin an authority. A plugin is allowed to *ask for* a stricter verdict; the
#: moment one declares it can grant, waive, or write governance, the registration is refused - not clamped,
#: because clamping an authority claim would silently produce a plugin whose recorded intent differs from the
#: one the operator approved.
_AUTHORITY_CLAIMS = (
    "authority",
    "auto_approve",
    "can_approve",
    "bypass_approval",
    "override_verdict",
    "loosen_verdict",
    "skip_verification",
    "grant_permission",
    "writes_governance",
    "protected_write",
    "self_register",
)

_NAME_SHAPE_OK = set("abcdefghijklmnopqrstuvwxyz0123456789_.-")
#: The only roots an entry point may name. ``sovereign/`` is excluded by construction: it is not in the list,
#: so a path written as ``evo_agent/../evo_agent/sovereign/x`` has to be resolved and re-checked to be caught,
#: which :func:`_entry_point_problem` does before comparing.
_ALLOWED_ROOTS = ("plugins/", "evo_agent/plugins/", "tests/fixtures/plugins/")


def _digest(record: "PluginRecord") -> str:
    canonical = json.dumps(
        {
            "name": record.name,
            "kind": record.kind.value,
            "source": record.source,
            "entry_point": record.entry_point,
            "provides": sorted(record.provides),
            "claims": sorted(record.claims),
        },
        sort_keys=True,
    ).encode()
    return hashlib.sha256(canonical).hexdigest()[:16]


def _entry_point_problem(entry_point: str) -> str:
    text = str(entry_point or "").strip().replace("\\", "/")
    if not text:
        return "an entry point must be recorded; a plugin with no location is a name with no referent"
    parts = [part for part in text.split("/") if part not in ("", ".")]
    if ".." in parts:
        return f"entry point '{text}' escapes its root after resolution; refuse it rather than normalise it"
    normalised = "/".join(parts)
    if not any(normalised.startswith(root.rstrip("/") + "/") or normalised == root.rstrip("/") for root in _ALLOWED_ROOTS):
        return (
            f"entry point '{text}' is outside the allow-listed roots "
            f"({', '.join(sorted(_ALLOWED_ROOTS))}); 'sovereign/' is not one of them and never will be"
        )
    return ""


@dataclass
class PluginRecord:
    """One extension as the operator described it. Everything here is a *claim*; enforcement is elsewhere.

    ``approved_by`` is not metadata for a report. It is the field that separates "reviewed" from "present",
    and :meth:`PluginInventory.activate` refuses to move a candidate forward without it - the same rule the
    skill mount enforces, for the same reason: an extension nobody named cannot be uninstalled blamelessly.
    """

    name: str
    kind: PluginKind = PluginKind.HOOK
    source: str = ""
    entry_point: str = ""
    provides: tuple[str, ...] = ()
    claims: tuple[str, ...] = ()
    approved_by: str = ""
    lifecycle: PluginLifecycle = PluginLifecycle.CANDIDATE
    digest: str = ""
    notes: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.name = str(self.name or "").strip()
        self.kind = self.kind if isinstance(self.kind, PluginKind) else _kind(self.kind)
        self.lifecycle = self.lifecycle if isinstance(self.lifecycle, PluginLifecycle) else _lifecycle(self.lifecycle)
        self.provides = tuple(str(item) for item in (self.provides or ()))
        self.claims = tuple(str(item) for item in (self.claims or ()))
        if not self.digest:
            self.digest = _digest(self)

    def assess(self, payload: dict[str, Any], result: Any = None, checks: Sequence[dict[str, Any]] = ()) -> dict[str, Any]:
        """The verifier-facing entry point: ``Verifier._consult`` calls exactly this shape.

        Unbound is a failure, not an abstention. If it were silent, the inventory could advertise a
        verification plugin that the ``Verifier`` then ignored, and the deployment would read as "two checks
        ran" when one of them was a name. Binding is the operator saying the plugin is real, so its absence
        has to show up in the verdict.
        """
        handler = self.metadata.get("assess")
        if not callable(handler):
            return {
                "name": self.name,
                "passed": False,
                "detail": (
                    f"verification plugin '{self.name}' is registered but not bound; the inventory does not "
                    "import entry points, so an unbound plugin is a refusal rather than a skipped check"
                ),
            }
        try:
            verdict = dict(handler(dict(payload), result, tuple(checks)) or {})
        except Exception as exc:  # noqa: BLE001 - a raising plugin is a failed check (mirrors _consult)
            return {"name": self.name, "passed": False, "detail": f"plugin '{self.name}' raised {type(exc).__name__}: {exc}"}
        passed = verdict.get("passed")
        if not isinstance(passed, bool):
            # "Maybe" has not tightened anything, and ambiguity must not be read as agreement.
            return {"name": self.name, "passed": False, "detail": f"plugin '{self.name}' returned passed={passed!r}, which is not a verdict"}
        return {"name": self.name, "passed": passed, "detail": str(verdict.get("detail", ""))}

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "kind": self.kind.value,
            "source": self.source,
            "entry_point": self.entry_point,
            "provides": list(self.provides),
            "claims": list(self.claims),
            "approved_by": self.approved_by,
            "lifecycle": self.lifecycle.value,
            "digest": self.digest,
            "notes": self.notes,
        }


def _kind(value: Any) -> PluginKind:
    try:
        return PluginKind(str(value))
    except ValueError:
        # Unknown kind is treated as the most restricted one rather than the most convenient: an inventory
        # that guesses "hook" for a typo would hand a plugin a slot in the event path.
        return PluginKind.EXECUTABLE_CODE


def _lifecycle(value: Any) -> PluginLifecycle:
    try:
        return PluginLifecycle(str(value))
    except ValueError:
        return PluginLifecycle.QUARANTINED


class PluginInventory:
    """The single registry of extensions, with the refusals that make the inventory worth reading.

    Mirrors :class:`evo_agent.mcp.MCPRegistry` in shape on purpose: policy-only, refuses rather than clamps
    an authority claim, and reports the enforcement it *declines* to provide (dynamic import) as plainly as
    the enforcement it does.
    """

    #: The tighten-only rule, quoted by :meth:`report` so a reader can see where it is actually enforced.
    TIGHTEN_ONLY = "a verification plugin may fail a step, never pass one: evo_agent/verifier.py::_finish"

    def __init__(self, *, policy: Any = None, builtins: Iterable[str] = (), allow_import: bool = False) -> None:
        self._records: dict[str, PluginRecord] = {}
        self.policy = policy
        #: Names this build already answers to. An extension may not take one, so ``hook`` cannot shadow a
        #: real handler by registering under a name the dispatcher would prefer.
        self.builtins = frozenset(str(name) for name in builtins)
        # Not wired to any caller: kept so that "the inventory can import" is a change someone has to make
        # deliberately, in one place, with a flag in the signature that a reviewer will see.
        self.allow_import = bool(allow_import)

    # -- registration ---------------------------------------------------------
    def register(self, record: Any, *, now: str = "") -> tuple[PluginRecord | None, list[str]]:
        """Validate and record one plugin. Returns ``(record, problems)``; problems mean nothing was stored.

        A rejected registration is stored nowhere - not even as a quarantined row. An inventory that lists
        refused entries invites a reader to ask "which of these are live?", and the answer would have to be a
        second lookup. Refusals belong in the event log and in this call's return value.
        """
        problems: list[str] = []
        if not isinstance(record, PluginRecord):
            if isinstance(record, dict):
                record = PluginRecord(**record)
            else:
                return None, ["a plugin must be a PluginRecord or a mapping of its fields; an opaque object cannot be reviewed"]
        if not record.name or any(char not in _NAME_SHAPE_OK for char in record.name):
            problems.append(
                f"'{record.name}' is not a usable plugin name; use lowercase letters, digits, '.', '_' or '-'"
            )
        if record.kind is PluginKind.EXECUTABLE_CODE:
            problems.append(f"{record.name or 'plugin'}: {CODE_REGISTRATION_REFUSAL}")
        if not str(record.source or "").strip():
            problems.append(f"{record.name or 'plugin'}: a source is required; an unattributed extension cannot be reviewed or rolled back")
        entry_problem = _entry_point_problem(record.entry_point)
        if entry_problem:
            problems.append(f"{record.name or 'plugin'}: {entry_problem}")
        authority = sorted(name for name in record.claims if str(name).strip().lower() in _AUTHORITY_CLAIMS)
        if authority:
            problems.append(
                f"{record.name or 'plugin'}: claims authority it cannot have ({', '.join(authority)}); "
                f"a plugin may tighten checks - {self.TIGHTEN_ONLY}"
            )
        collisions = sorted(name for name in record.provides if str(name).strip() in self.builtins)
        if collisions:
            problems.append(
                f"{record.name or 'plugin'}: provides {collisions}, which this build already answers to; "
                "an extension adds a name, it does not take one over"
            )
        if record.name in self._records:
            existing = self._records[record.name]
            if existing.digest == record.digest:
                # Same bytes, same claims: a re-read of the config is not a conflict, and refusing it would
                # make every restart of a reviewed deployment an error someone learns to silence.
                return existing, []
            problems.append(
                f"'{record.name}' is already registered with different content (digest {existing.digest} vs "
                f"{record.digest}); an extension is replaced by retiring the old one, not by overwriting it"
            )
        if problems:
            return None, problems
        stored = PluginRecord(**{**record.to_dict(), "metadata": dict(record.metadata)})
        stored.digest = record.digest
        self._records[stored.name] = stored
        return stored, []

    def register_many(self, records: Iterable[Any], *, now: str = "") -> dict[str, Any]:
        accepted: list[str] = []
        refused: dict[str, list[str]] = {}
        for record in records:
            stored, problems = self.register(record, now=now)
            if problems:
                key = str(record.get("name") if isinstance(record, dict) else getattr(record, "name", "")) or "plugin"
                refused[key] = problems
            else:
                accepted.append(str(stored.name))
        return {"ok": not refused, "accepted": accepted, "refused": refused, "accepted_count": len(accepted), "refused_count": len(refused)}

    # -- lifecycle ------------------------------------------------------------
    def activate(self, name: str, *, approved_by: str = "", now: str = "") -> tuple[bool, str]:
        """Move a candidate to active. Only an operator identity does it, and the refusal says why."""
        record = self._records.get(str(name))
        if record is None:
            return False, f"'{name}' is not registered; activation is not a way to install"
        if record.lifecycle is PluginLifecycle.ACTIVE:
            return True, ""
        if record.lifecycle is PluginLifecycle.QUARANTINED:
            return False, f"'{name}' is quarantined and stays quarantined until it is retired and re-registered as a candidate"
        if record.lifecycle is PluginLifecycle.RETIRED:
            return False, f"'{name}' is retired; a retired extension is re-registered as a new candidate rather than revived"
        if not str(approved_by or "").strip():
            return False, (
                f"'{name}' needs an approving operator identity to activate; registration is a claim, "
                "activation is a decision"
            )
        if approved_by == record.name:
            return False, f"'{name}' cannot approve itself; the approver must be a different identity"
        from .modes import is_plan_mode

        if is_plan_mode(self.policy):
            return False, "plan mode is a read-only phase: enabling an extension changes state and is refused"
        record.approved_by = str(approved_by)
        record.lifecycle = PluginLifecycle.ACTIVE
        record.notes = f"activated at {now}" if now else record.notes
        return True, ""

    def quarantine(self, name: str, reason: str = "") -> bool:
        record = self._records.get(str(name))
        if record is None:
            return False
        record.lifecycle = PluginLifecycle.QUARANTINED
        record.notes = reason or record.notes
        return True

    def retire(self, name: str, reason: str = "") -> bool:
        record = self._records.get(str(name))
        if record is None:
            return False
        record.lifecycle = PluginLifecycle.RETIRED
        record.notes = reason or record.notes
        return True

    def bind(self, name: str, *, approved_by: str = "", assess: Any = None, handler: Any = None) -> tuple[bool, str]:
        """Attach already-loaded code to a registered record. Refused without an approver, and in plan mode.

        This is the seam the deferral at :189 leaves open on purpose, and it is narrower than it looks: the
        inventory never reads a file, never imports a module, and never accepts a callable from a config
        document (``register`` builds its record from JSON-shaped fields, so a callable cannot arrive that
        way). The *embedding application* - the thing that decided to ship this plugin - hands over an object
        it already has. Who touches the filesystem is the whole distinction: not this module.
        """
        record = self._records.get(str(name))
        if record is None:
            return False, f"'{name}' is not registered; binding is not a way to install"
        if record.kind is PluginKind.EXECUTABLE_CODE:
            return False, f"'{name}' is registered as executable code, which is deferred: {CODE_REGISTRATION_REFUSAL}"
        from .modes import is_plan_mode

        if is_plan_mode(self.policy):
            return False, "plan mode is a read-only phase: binding code into the verification path changes state"
        if not str(approved_by or "").strip():
            return False, f"'{name}' cannot be bound without an approving operator identity"
        bound = 0
        if callable(assess):
            record.metadata["assess"] = assess
            bound += 1
        if callable(handler):
            record.metadata["handler"] = handler
            bound += 1
        if not bound:
            return False, f"'{name}': nothing to bind; pass the assessment callable (verification) or the handler (hook)"
        record.approved_by = str(approved_by)
        return True, ""

    # -- consumers ------------------------------------------------------------
    def list(self, *, kind: PluginKind | str | None = None, lifecycle: PluginLifecycle | str | None = None) -> list[PluginRecord]:
        wanted_kind = None if kind is None else (kind if isinstance(kind, PluginKind) else _kind(kind))
        wanted_state = None if lifecycle is None else (lifecycle if isinstance(lifecycle, PluginLifecycle) else _lifecycle(lifecycle))
        records = []
        for record in self._records.values():
            if wanted_kind is not None and record.kind is not wanted_kind:
                continue
            if wanted_state is not None and record.lifecycle is not wanted_state:
                continue
            records.append(record)
        return sorted(records, key=lambda item: item.name)

    def active(self, kind: PluginKind | str | None = None) -> list[PluginRecord]:
        return self.list(kind=kind, lifecycle=PluginLifecycle.ACTIVE)

    def verification_plugins(self) -> tuple[Any, ...]:
        """What ``Verifier(plugins=...)`` is handed: active, verification-kind records only.

        The candidate set is excluded because a candidate has not been approved, and the other kinds are
        excluded because the verifier must not be handed a research provider and told to judge with it.
        """
        return tuple(record for record in self.active(PluginKind.VERIFICATION))

    def hooks(self) -> tuple[Any, ...]:
        return tuple(record for record in self.active(PluginKind.HOOK))

    def dispatch_hook(self, name: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Run one hook for its side effects, refusing to make it a decision.

        No hook may change a verdict, an approval, or the protected set. The return value is the *record* of
        what happened - which handler ran, whether it raised - so a failure in the event path is visible in
        the audit trail instead of surfacing as a step that silently never happened.
        """
        record = self._records.get(str(name))
        if record is None:
            return {"ok": False, "refusal": f"hook '{name}' is not registered"}
        if record.lifecycle is not PluginLifecycle.ACTIVE:
            return {"ok": False, "refusal": f"hook '{name}' is {record.lifecycle.value}; only active hooks are dispatched"}
        handler = record.metadata.get("handler")
        if not callable(handler):
            return {
                "ok": False,
                "refusal": (
                    f"hook '{name}' has no bound handler and this inventory does not import one; "
                    + CODE_REGISTRATION_REFUSAL
                ),
            }
        try:
            handler(dict(payload))
        except Exception as exc:  # noqa: BLE001 - an event-path failure is recorded, never propagated
            return {"ok": True, "handler_failed": f"{type(exc).__name__}: {exc}", "name": name}
        return {"ok": True, "name": name}

    def assess(self, name: str, payload: dict[str, Any], *, result: Any = None, checks: Sequence[dict[str, Any]] = ()) -> dict[str, Any]:
        """One plugin's verdict, in the shape ``Verifier._consult`` expects: tighten-only, fail-closed.

        Kept here so the fixtures can be driven without a full ``Verifier``, and deliberately a delegation to
        :meth:`PluginRecord.assess` rather than a second implementation - two copies of "a raising plugin
        fails" is how the two paths start disagreeing about what a non-boolean verdict means.
        """
        record = self._records.get(str(name))
        if record is None:
            return {"name": str(name), "passed": False, "detail": "plugin is not registered; an absent check is not a passing one"}
        if record.lifecycle is not PluginLifecycle.ACTIVE:
            return {"name": str(name), "passed": False, "detail": f"plugin is {record.lifecycle.value}; an absent check is not a passing one"}
        return record.assess(payload, result, checks)

    # -- reporting ------------------------------------------------------------
    def report(self) -> dict[str, Any]:
        from .modes import is_plan_mode

        return {
            "schema": "plugin-inventory-v1",
            "records": [record.to_dict() for record in self.list()],
            "active": [record.name for record in self.active()],
            "counts": {
                state.value: len(self.list(lifecycle=state)) for state in PluginLifecycle
            },
            "authority_claims_refused": sorted(_AUTHORITY_CLAIMS),
            "allowed_entry_roots": sorted(_ALLOWED_ROOTS),
            "tighten_only": self.TIGHTEN_ONLY,
            "dynamic_import": "refused" if not self.allow_import else "explicitly enabled by the caller",
            "plan_mode": is_plan_mode(self.policy),
        }


__all__ = [
    "CODE_REGISTRATION_REFUSAL",
    "PluginInventory",
    "PluginKind",
    "PluginLifecycle",
    "PluginRecord",
]
