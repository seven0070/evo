"""The registry that decides who may serve a capability - and says why (07 §5).

Selection is *declared*, not emergent: a request is planned against every enabled backend, and the
result says which ones would serve, which refused, and on what grounds. That is what makes an
integrated harness reviewable. If the answer to "why did the lead agent run this?" is "because it
was imported first", the system has no policy, only an accident of module order.

Three rules give the registry its teeth:

* a backend that does not satisfy the ``ExecutionBackend`` port is rejected **at registration**, not
  at first use - discovering a missing obligation halfway through a turn means an irreversible
  action was already taken;
* an *external* backend must record its license, source, and accepting operator before it may
  register, so vendored capability cannot arrive as an anonymous import;
* a name that is not registered is an error, never a silent fall back to native. Falling back
  silently is how "we integrated DeerFlow" quietly becomes "we did not".
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any, Callable, Iterable, Sequence

from ..ports.contracts import (
    BackendAvailability,
    BackendPlan,
    CapabilityRequest,
    Receipt,
    TurnContext,
    TurnResult,
    ExecutionBackend,
    validate_implementation,
)
from .availability import AvailabilityReport, build_report


#: A backend whose port obligations are unmet is a contract violation, not a runtime failure mode.
class BackendContractError(RuntimeError):
    """A backend was rejected before it could run, because it does not honour its port."""

    def __init__(self, name: str, missing: Sequence[str]) -> None:
        self.name = name
        self.missing = tuple(missing)
        super().__init__(f"backend '{name}' does not implement its port; missing: {', '.join(self.missing) or 'unknown'}")


class BackendConflict(RuntimeError):
    """Two backends claimed one name, or an external backend arrived without provenance.

    Names are unique because everything downstream - the audit trail, a status report, a
    promotion decision - addresses backends by name. Two objects answering to one name means the
    record no longer says what ran.
    """


#: Provenance an external backend must supply. ``source == "builtin"`` is exempt: it is this repo,
#: already covered by the repo's own license and review.
REQUIRED_PROVENANCE = ("license", "source_url", "accepted_by")


@dataclass(frozen=True)
class Registration:
    """One backend plus the record of why it is allowed to be here."""

    backend: Any
    name: str
    source: str = "builtin"
    license: str = ""
    source_url: str = ""
    accepted_by: str = ""
    priority: int = 0
    enabled: bool = True
    version: str = ""
    notes: tuple[str, ...] = ()
    capabilities: tuple[str, ...] = ()
    review: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "source": self.source,
            "license": self.license,
            "source_url": self.source_url,
            "accepted_by": self.accepted_by,
            "priority": self.priority,
            "enabled": self.enabled,
            "version": self.version,
            "notes": list(self.notes),
            "capabilities": list(self.capabilities),
        }


class BackendRegistry:
    """Holds the backends, plans requests against them, and forwards turns to one."""

    def __init__(
        self,
        *,
        on_event: Callable[[str, dict[str, Any]], None] | None = None,
        policy: Any | None = None,
        mediator: Any | None = None,
    ) -> None:
        self._registrations: dict[str, Registration] = {}
        self.on_event = on_event
        self.policy = policy
        self.mediator = mediator

    # -- registration ------------------------------------------------------
    def register(
        self,
        backend: Any,
        *,
        source: str = "builtin",
        license: str = "",
        source_url: str = "",
        accepted_by: str = "",
        priority: int = 0,
        enabled: bool = True,
        version: str = "",
        notes: Iterable[str] = (),
        capabilities: Iterable[str] = (),
    ) -> Registration:
        """Add a backend. Raises rather than accepting an incomplete or unattributed one.

        ``priority`` is a *preference*, applied only among backends that can serve the request; a
        higher number does not make an unusable backend usable. Ties resolve to ``native`` first,
        because Evo's own loop is the one whose memory, verification, and rollback authorities are
        already wired, and an equal-cost tie should not silently move work outside them.
        """
        name = getattr(backend, "name", None) or type(backend).__name__
        missing = validate_implementation(backend, ExecutionBackend)
        if missing:
            raise BackendContractError(str(name), missing)
        if name in self._registrations:
            raise BackendConflict(f"backend '{name}' is already registered; unregister it first")
        supplied = {"license": license, "source_url": source_url, "accepted_by": accepted_by}
        absent = [key for key in REQUIRED_PROVENANCE if not str(supplied.get(key) or "").strip()]
        if source != "builtin" and absent:
            if enabled:
                raise BackendConflict(
                    f"external backend '{name}' must record {', '.join(absent)} before it may be "
                    "enabled: an integrated runtime is a supply-chain decision, and the record is "
                    "where that decision stays reviewable"
                )
            # A disabled backend may be *recorded* before it is signed off, so that "we have the
            # adapter and it is waiting on review" is a state the system can express. Enabling it
            # re-runs this check, in set_enabled, so the gap cannot be closed by forgetting.
            notes = (*tuple(notes), f"provenance incomplete: {', '.join(absent)}")
        registration = Registration(
            backend=backend,
            name=str(name),
            source=source,
            license=license,
            source_url=source_url,
            accepted_by=accepted_by,
            priority=int(priority),
            enabled=bool(enabled) and not bool(getattr(backend, "disabled", False)),
            version=version,
            notes=tuple(notes),
            capabilities=tuple(capabilities),
            review={"registered_at": __import__("time").time()},
        )
        self._registrations[registration.name] = registration
        self._emit(
            "backend_registered",
            {
                "name": registration.name,
                "source": registration.source,
                "license": registration.license,
                "enabled": registration.enabled,
                "priority": registration.priority,
            },
        )
        return registration

    def unregister(self, name: str) -> bool:
        if name not in self._registrations:
            return False
        del self._registrations[name]
        self._emit("backend_unregistered", {"name": name})
        return True

    def set_enabled(self, name: str, enabled: bool) -> Registration:
        """Flip a backend without losing its provenance record.

        A separate operation rather than re-registering because re-registering with ``enabled=False``
        would drop the version and review metadata, and a disabled backend still has to be
        explainable ("disabled on 2026-08-28 after its probe started failing", not "gone").
        """
        registration = self.get(name)
        if enabled and registration.source != "builtin":
            supplied = {"license": registration.license, "source_url": registration.source_url, "accepted_by": registration.accepted_by}
            absent = [key for key in REQUIRED_PROVENANCE if not str(supplied.get(key) or "").strip()]
            if absent:
                raise BackendConflict(
                    f"cannot enable '{name}': an external backend needs {', '.join(absent)} recorded first"
                )
        updated = replace(registration, enabled=bool(enabled))
        self._registrations[name] = updated
        self._emit("backend_enabled_changed", {"name": name, "enabled": bool(enabled)})
        return updated

    # -- inspection --------------------------------------------------------
    @property
    def names(self) -> tuple[str, ...]:
        return tuple(sorted(self._registrations))

    def registrations(self) -> tuple[Registration, ...]:
        return tuple(self._registrations[key] for key in sorted(self._registrations))

    def get(self, name: str) -> Registration:
        try:
            return self._registrations[name]
        except KeyError as exc:
            known = ", ".join(self.names) or "none"
            raise KeyError(f"unknown backend '{name}'; registered: {known}") from exc

    def __contains__(self, name: object) -> bool:
        return str(name) in self._registrations

    def __len__(self) -> int:
        return len(self._registrations)

    def describe(self) -> list[dict[str, Any]]:
        """Machine-readable inventory, asserted by the documentation-integrity tests."""
        return [registration.to_dict() for registration in self.registrations()]

    def probe(self, name: str) -> BackendAvailability:
        registration = self.get(name)
        try:
            availability = registration.backend.probe()
        except Exception as exc:  # a broken backend is a finding, not a crash (R9)
            return BackendAvailability(registration.name, False, f"probe raised {type(exc).__name__}: {exc}")
        if not availability.name:
            availability = BackendAvailability(
                registration.name, availability.available, availability.reason, dict(availability.detail)
            )
        return availability

    def availability_report(self) -> AvailabilityReport:
        registrations = self.registrations()
        return build_report(registrations, {item.name: self.probe(item.name) for item in registrations})

    def states(self) -> dict[str, str]:
        return {report.name: report.state for report in self.availability_report().reports}

    # -- planning ----------------------------------------------------------
    def candidates(self, request: CapabilityRequest) -> list[tuple[Registration, BackendAvailability, BackendPlan]]:
        """Every enabled, available backend that would serve ``request``, best first.

        Probing is part of planning on purpose: a backend that reports "yes" from a stale cache is
        how an uninstalled optional runtime turns into a mid-turn exception.
        """
        serving: list[tuple[Registration, BackendAvailability, BackendPlan]] = []
        for registration in self.registrations():
            if not registration.enabled:
                self._emit("backend_skipped", {"name": registration.name, "reason": "disabled"})
                continue
            availability = self.probe(registration.name)
            if not availability.available:
                self._emit("backend_skipped", {"name": registration.name, "reason": availability.reason})
                continue
            try:
                plan = registration.backend.plan_capability(request)
            except Exception as exc:
                self._emit("backend_plan_failed", {"name": registration.name, "error": f"{type(exc).__name__}: {exc}"})
                continue
            if not plan.can_serve:
                self._emit("backend_declined", {"name": registration.name, "reason": plan.reason, "degradation": plan.degradation})
                continue
            serving.append((registration, availability, plan))
        serving.sort(key=lambda item: (item[0].name != "native", -item[0].priority, item[0].name))
        return serving

    def plan(self, request: CapabilityRequest) -> dict[str, Any]:
        """Full selection record: who would serve, who declined, and who was unusable."""
        serving = self.candidates(request)
        declined: list[dict[str, Any]] = []
        unavailable: list[dict[str, Any]] = []
        for registration in self.registrations():
            if any(item[0] is registration for item in serving):
                continue
            if not registration.enabled:
                unavailable.append({"name": registration.name, "reason": "disabled"})
                continue
            availability = self.probe(registration.name)
            if not availability.available:
                unavailable.append({"name": registration.name, "reason": availability.reason})
                continue
            plan = registration.backend.plan_capability(request)
            declined.append({"name": registration.name, "reason": plan.reason, "degradation": plan.degradation})
        return {
            "goal": request.goal,
            "needed": list(request.needed),
            "serving": [
                {
                    "name": registration.name,
                    "priority": registration.priority,
                    "reason": plan.reason,
                    "estimated_turns": plan.estimated_turns,
                    "requires_approval_for": list(plan.requires_approval_for),
                    "degradation": plan.degradation,
                }
                for registration, _availability, plan in serving
            ],
            "declined": declined,
            "unavailable": unavailable,
            "selected": serving[0][0].name if serving else "",
        }

    def select(self, request: CapabilityRequest) -> Registration | None:
        serving = self.candidates(request)
        return serving[0][0] if serving else None

    # -- execution ---------------------------------------------------------
    def run_turn(self, name: str, context: TurnContext, sink: Any = None) -> TurnResult:
        """Forward one turn to a named backend. Refusals are returned, never raised.

        The disabled/unknown checks live here as well as in :meth:`candidates`, because a caller may
        name a backend directly (the CLI's ``--backend`` flag does), and a direct name must not be a
        way to bypass the enabled flag.
        """
        try:
            registration = self.get(name)
        except KeyError:
            return TurnResult(status="refused", text=f"unknown backend '{name}'", notes=("no such backend; refusing to fall back silently",), origin=name)
        if not registration.enabled:
            return TurnResult(status="refused", text=f"backend '{name}' is disabled", notes=("constructed with enabled=False; the runtime assembles the registry in evo_agent/backends/__init__.py",), origin=name)
        try:
            result = registration.backend.run_turn(context, sink)
        except Exception as exc:
            self._emit("backend_turn_failed", {"name": name, "error": f"{type(exc).__name__}: {exc}", "turn_id": context.turn_id})
            return TurnResult(status="failed", text=f"{type(exc).__name__}: {exc}", origin=name)
        if not isinstance(result, TurnResult):
            # A backend returning a bare string or a dict is a contract violation caught here rather
            # than trusted: the verifier and the receipt ledger both index into TurnResult fields,
            # and letting a legacy shape through moves the failure to somewhere less obvious.
            return TurnResult(
                status="failed",
                text=f"backend '{name}' returned {type(result).__name__}, not a TurnResult",
                notes=("port contract violated at run time",),
                origin=name,
            )
        self._emit(
            "backend_turn_routed",
            {"name": name, "turn_id": context.turn_id, "status": result.status, "receipts": len(result.receipts)},
        )
        return result

    def cancel(self, name: str, turn_id: str, reason: str = "operator") -> bool:
        """Ask a backend to stop the turn it holds. Reports whether it *said* it would.

        ``cancel`` is optional in the port, so an absent method is a ``False`` here rather than an
        ``AttributeError``: the runtime's kill path must be able to try every backend and collect which
        ones can be interrupted, and a cancellation that raises has the same effect as none at all.
        """
        registration = self.get(name)
        from ..ports.contracts import call_optional

        cancelled = bool(call_optional(registration.backend, "cancel", turn_id, reason, default=False))
        self._emit("backend_turn_cancelled", {"name": name, "turn_id": turn_id, "reason": reason, "cancelled": cancelled})
        return cancelled

    def export_receipts(self, name: str, turn_id: str) -> tuple[Receipt, ...]:
        """Collect a backend's receipts, tolerating backends that own none (the port's additive rule)."""
        registration = self.get(name)
        from ..ports.contracts import call_optional

        return tuple(call_optional(registration.backend, "export_receipts", turn_id, default=()) or ())

    def _emit(self, event: str, payload: dict[str, Any]) -> None:
        if self.on_event is None:
            return
        try:
            self.on_event(event, payload)
        except Exception:
            pass
