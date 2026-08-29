"""Every process Evo starts, through one place (07 §2 isolation layer; approved decision Q4).

Before this package existed, the boundary was inverted: evolution *candidates* - code the agent
wrote for itself, already quarantined by design - ran inside namespaces, while the *runtime's*
own ``shell`` tool called ``subprocess.run(..., shell=True)`` on the host, guarded only by an argv
allowlist. That is the lesser-risk side of the two getting the stronger control.

This package is where that asymmetry ends. ``evo_agent`` may not spawn a process anywhere else;
the invariant registry checks it, and the tool layer, the adapters, and the candidate/benchmark
runners all go through :func:`run_confined`.
"""

from __future__ import annotations

from .base import BASE_ENVIRONMENT, IsolationUnavailable, sanitized_environment, terminate
from .host import HostProvider
from .local_bwrap import LocalBwrapProvider
from .registry import (
    ENFORCEMENT_LEVELS,
    IsolationSettings,
    default_providers,
    normalize_enforcement,
    prepare_launch,
    probe_all,
    run_confined,
    select,
)
from .unshare import UnshareProvider

PROVIDERS: tuple[type, ...] = (LocalBwrapProvider, UnshareProvider, HostProvider)

__all__ = [
    "BASE_ENVIRONMENT",
    "ENFORCEMENT_LEVELS",
    "HostProvider",
    "IsolationSettings",
    "IsolationUnavailable",
    "LocalBwrapProvider",
    "PROVIDERS",
    "UnshareProvider",
    "default_providers",
    "normalize_enforcement",
    "prepare_launch",
    "probe_all",
    "run_confined",
    "sanitized_environment",
    "select",
    "terminate",
]
