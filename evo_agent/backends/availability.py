"""Backend availability: what is installed, what works here, and what to do about it (07 §5).

Three states are reported rather than two, because "not installed" and "installed but cannot run
on this kernel" need different remedies and different user-facing text:

* **available** - imported and its self-check passed;
* **degraded** - present, but something it needs (isolation, a credential, a network path) is
  missing, so it may serve with a recorded caveat;
* **unavailable** - not usable here, with the reason and an install hint.

Probing never raises and never mutates. It is called at start-up, from a status report, and from
the invariant tests - sometimes inside the same process a user is watching. A probe that could write
state would make those three views of the system disagree, which is the only reason they are three.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import time
from typing import Any, Iterable, Sequence

from ..ports.contracts import BackendAvailability


#: Tri-state, as data rather than as exceptions, so a report can be rendered by a CLI that has no
#: idea which backends exist.
AVAILABLE = "available"
DEGRADED = "degraded"
UNAVAILABLE = "unavailable"


def classify(availability: BackendAvailability, *, enabled: bool = True) -> str:
    """Map a probe result onto the three reporting states."""
    if not enabled:
        return UNAVAILABLE
    if availability.available and availability.reason:
        # A probe that says "yes, and" is reporting a caveat; hiding it would make the degraded
        # path indistinguishable from the clean one.
        return DEGRADED
    return AVAILABLE if availability.available else UNAVAILABLE


@dataclass(frozen=True)
class BackendReport:
    """One backend's state, in the form a human or a test can assert on."""

    name: str
    state: str
    reason: str = ""
    source: str = "builtin"
    license: str = ""
    priority: int = 0
    enabled: bool = True
    version: str = ""
    detail: dict[str, Any] = field(default_factory=dict)

    @property
    def usable(self) -> bool:
        return self.state in (AVAILABLE, DEGRADED)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "state": self.state,
            "reason": self.reason,
            "source": self.source,
            "license": self.license,
            "priority": self.priority,
            "enabled": self.enabled,
            "version": self.version,
            "usable": self.usable,
            "detail": dict(self.detail),
        }


@dataclass(frozen=True)
class AvailabilityReport:
    """The whole backend surface at one moment, in the form a status report renders.

    There is no ``evo backends`` subcommand yet - see the deviation note in
    ``docs/evolution/08-IMPLEMENTATION-LOG.md`` under P2 - so this is the payload, waiting for the
    command that will print it. Building the report and building the CLI in the same phase would have
    made the report's shape a side-effect of argument parsing.
    """

    reports: tuple[BackendReport, ...] = ()
    probed_at: float = field(default_factory=time.time)

    def by_state(self, state: str) -> tuple[BackendReport, ...]:
        return tuple(report for report in self.reports if report.state == state)

    @property
    def usable(self) -> tuple[BackendReport, ...]:
        return tuple(report for report in self.reports if report.usable)

    @property
    def isolated_required_but_missing(self) -> bool:
        """True when a backend is asking to run and nothing can confine it.

        Surfaced as a property rather than left in the text because the runtime treats it as a
        start-up condition, not a cosmetic one.
        """
        return any(report.state == DEGRADED and "isolat" in report.reason.lower() for report in self.reports)

    def to_dict(self) -> dict[str, Any]:
        return {
            "probed_at": self.probed_at,
            "backends": [report.to_dict() for report in self.reports],
            "counts": {
                AVAILABLE: len(self.by_state(AVAILABLE)),
                DEGRADED: len(self.by_state(DEGRADED)),
                UNAVAILABLE: len(self.by_state(UNAVAILABLE)),
            },
        }

    def text(self) -> str:
        if not self.reports:
            return "no backends registered"
        width = max(len(report.name) for report in self.reports)
        lines = []
        for report in self.reports:
            marker = {AVAILABLE: "ok", DEGRADED: "degraded", UNAVAILABLE: "unavailable"}[report.state]
            suffix = f" - {report.reason}" if report.reason else ""
            lines.append(f"{report.name:<{width}}  {marker:<11} {report.source}/{report.license or 'n/a'}{suffix}")
        return "\n".join(lines)


def build_report(registrations: Iterable[Any], availabilities: dict[str, BackendAvailability]) -> AvailabilityReport:
    """Combine registrations with probe results. Missing probes read as unavailable, not as ok."""
    reports: list[BackendReport] = []
    for registration in registrations:
        availability = availabilities.get(registration.name)
        if availability is None:
            reports.append(
                BackendReport(
                    name=registration.name,
                    state=UNAVAILABLE,
                    reason="probe returned no result",
                    source=registration.source,
                    license=registration.license,
                    priority=registration.priority,
                    enabled=registration.enabled,
                    version=registration.version,
                )
            )
            continue
        reports.append(
            BackendReport(
                name=registration.name,
                state=classify(availability, enabled=registration.enabled),
                reason=availability.reason,
                source=registration.source,
                license=registration.license,
                priority=registration.priority,
                enabled=registration.enabled,
                version=registration.version or "",
                detail=dict(availability.detail),
            )
        )
    return AvailabilityReport(reports=tuple(reports))


def merge_reports(reports: Sequence[AvailabilityReport]) -> AvailabilityReport:
    """Union several reports by name, keeping the worst state.

    Used when one process probes from two registries (the runtime and a dry-run inspector): "worst
    wins" is the only safe merge, since reporting a backend as available because some other
    registry thought so is how a missing dependency turns into a runtime crash later.
    """
    worst = {UNAVAILABLE: 2, DEGRADED: 1, AVAILABLE: 0}
    by_name: dict[str, BackendReport] = {}
    for report in reports:
        for item in report.reports:
            current = by_name.get(item.name)
            if current is None or worst[item.state] > worst[current.state]:
                by_name[item.name] = item
    return AvailabilityReport(reports=tuple(by_name[name] for name in sorted(by_name)))
