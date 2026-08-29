"""Provider selection and the single entry point for confined execution.

``run_confined`` is the function everything else calls. Its contract is what makes the isolation
claim meaningful rather than decorative:

* the *strongest usable* provider wins, in the declared order, and the choice is returned in the
  result rather than assumed by the caller;
* a request that the selected provider cannot honour is a **refusal**, not a silent downgrade -
  network access is the example, since "denied" and "not denied" must never look alike upstream;
* enforcement decides what happens when nothing is usable: ``strict`` refuses, ``auto`` refuses on
  a platform that ought to have namespaces and degrades with an audit event on one that has none,
  ``degrade`` and ``off`` run on the host and say so every time.

The degrade path emits through ``on_event`` so the caller can record ``SECURITY_DEGRADED`` in the
same ledger the rest of the run writes to. A degradation nobody can see is a policy change that
happened without review.
"""

from __future__ import annotations

from dataclasses import dataclass
import sys
from typing import Any, Callable, Sequence

from ..ports.contracts import ExecRequest, ExecResult, ProviderAvailability
from .base import IsolationUnavailable, platform_supports_namespaces
from .host import HostProvider
from .local_bwrap import LocalBwrapProvider
from .unshare import UnshareProvider


ENFORCEMENT_LEVELS = ("auto", "strict", "degrade", "off")


def normalize_enforcement(value: Any) -> str:
    """Clamp, do not validate: an unknown level becomes the safest one (R6)."""
    text = str(value or "auto").strip().lower()
    return text if text in ENFORCEMENT_LEVELS else "strict"


@dataclass
class IsolationSettings:
    """The knobs the registry needs, decoupled from ``SecurityPolicy``'s own shape."""

    enforcement: str = "auto"
    preferred: str = "auto"
    host_permitted: bool = False
    host_permit_reason: str = ""
    order: tuple[str, ...] = ("local_bwrap", "unshare", "host")

    def __post_init__(self) -> None:
        self.enforcement = normalize_enforcement(self.enforcement)
        # Clamped ceilings: an operator may widen *nothing* that weakens isolation.
        self.preferred = str(self.preferred or "auto").strip().lower() or "auto"
        if not self.order:
            self.order = ("local_bwrap", "unshare", "host")
        if "host" not in self.order:
            self.order = (*self.order, "host")


def default_providers(settings: IsolationSettings | None = None) -> tuple[Any, ...]:
    settings = settings or IsolationSettings()
    return (
        LocalBwrapProvider(),
        UnshareProvider(),
        HostProvider(permitted=settings.host_permitted, permit_reason=settings.host_permit_reason),
    )


def probe_all(settings: IsolationSettings | None = None, providers: Sequence[Any] | None = None) -> dict[str, ProviderAvailability]:
    """Every provider's honest current state. Never raises (R9)."""
    result: dict[str, ProviderAvailability] = {}
    for provider in providers or default_providers(settings):
        try:
            result[provider.name] = provider.probe()
        except Exception as exc:  # a broken provider is data, not a crash
            result[provider.name] = ProviderAvailability(provider.name, False, f"probe raised {type(exc).__name__}: {exc}")
    return result


def select(settings: IsolationSettings | None = None, providers: Sequence[Any] | None = None) -> Any:
    """The strongest usable provider. Raises :class:`IsolationUnavailable` if there is none."""
    settings = settings or IsolationSettings()
    ordered = list(providers if providers is not None else default_providers(settings))
    by_name = {provider.name: provider for provider in ordered}
    if settings.preferred != "auto" and settings.preferred in by_name:
        preferred = by_name[settings.preferred]
        ordered = [preferred, *[item for item in ordered if item is not preferred]]
    if settings.enforcement == "off":
        host = by_name.get("host")
        if host is not None:
            host.permitted = True
            host.permit_reason = "sandbox_enforcement='off'"
            return host
    for provider in ordered:
        if provider.name == "host" and not getattr(provider, "permitted", False) and settings.enforcement not in ("degrade",):
            continue
        availability = provider.probe()
        if availability.usable:
            return provider
    # Nothing usable. The enforcement level decides whether that is a refusal or a recorded
    # degradation, and the difference matters most on platforms with no namespaces at all.
    host = by_name.get("host")
    if host is None:
        raise IsolationUnavailable("no isolation provider is available and the host provider is not registered")
    if settings.enforcement == "strict":
        raise IsolationUnavailable("sandbox_enforcement='strict' and no isolation provider is usable")
    if settings.enforcement == "auto" and platform_supports_namespaces() and sys.platform.startswith("linux"):
        raise IsolationUnavailable(
            "this platform has user namespaces but no usable provider (bwrap and unshare both "
            "failed their probes); refusing instead of degrading, because a machine that had "
            "confinement and lost it is a security change, not a convenience issue"
        )
    host.permitted = True
    host.permit_reason = f"degraded: no isolation provider usable under enforcement='{settings.enforcement}'"
    return host


def prepare_launch(
    request: ExecRequest,
    *,
    settings: IsolationSettings | None = None,
    providers: Sequence[Any] | None = None,
    on_event: Callable[[str, dict[str, Any]], None] | None = None,
) -> Any:
    """Wrap a request for a process the *caller* keeps open. Returns a :class:`ConfinedLaunch`.

    Raises :class:`IsolationUnavailable` rather than handing back an unconfined argv, because a
    bridge that forgot to check a flag would then run a harness on the host silently. A caller that
    wants to accept degradation says so explicitly via ``settings.enforcement``.
    """
    settings = settings or IsolationSettings()
    if not isinstance(request, ExecRequest):
        raise TypeError("prepare_launch requires an ExecRequest")
    provider = select(settings, providers)
    launch = provider.prepare(request)
    if not launch.isolated and on_event is not None:
        on_event(
            "security_degraded",
            {"provider": launch.provider, "label": request.label, "reason": launch.degraded_reason, "enforcement": settings.enforcement},
        )
    return launch


def run_confined(
    request: ExecRequest,
    *,
    settings: IsolationSettings | None = None,
    providers: Sequence[Any] | None = None,
    on_event: Callable[[str, dict[str, Any]], None] | None = None,
) -> ExecResult:
    """Run one request through the selected provider. The only sanctioned spawn path (R2)."""
    settings = settings or IsolationSettings()
    if not isinstance(request, ExecRequest):
        raise TypeError("run_confined requires an ExecRequest; a bare command line has no policy")
    try:
        provider = select(settings, providers)
    except IsolationUnavailable as exc:
        if on_event is not None:
            on_event("isolation_unavailable", {"reason": str(exc), "enforcement": settings.enforcement})
        return ExecResult(returncode=-1, provider="none", refusal=str(exc), isolated=False)
    notices: list[str] = []

    def note(message: str) -> None:
        notices.append(message)
        if on_event is not None:
            on_event("provider_note", {"provider": provider.name, "note": message})

    result = provider.run(request, on_event=note)
    if result.refusal and provider.name != "host":
        # A refusal at the provider level (missing capability, spawn error) is final under
        # strict/auto; under degrade/off we may still fall through to the host, once, loudly.
        if settings.enforcement in ("degrade", "off") and on_event is not None:
            on_event("provider_refused", {"provider": provider.name, "refusal": result.refusal})
            host = HostProvider(permitted=True, permit_reason=f"{provider.name} refused: {result.refusal[:120]}")
            result = host.run(request, on_event=note)
    if not result.isolated and on_event is not None:
        on_event(
            "security_degraded",
            {
                "provider": result.provider,
                "argv": list(request.argv)[:4],
                "label": request.label,
                "reason": result.refusal or result.degraded_reason or "selected provider does not isolate",
                "enforcement": settings.enforcement,
            },
        )
    return result
