"""Sovereign core: the authorities that no integrated capability may weaken.

This package is the single home for the definitions that gate Evo's autonomy:
the protected byte set (:mod:`evo_agent.sovereign.protected`), the live
architecture invariants (:mod:`evo_agent.sovereign.invariants`), and the
Evolutionary Metamorphosis eligibility registry
(:mod:`evo_agent.sovereign.eligibility`).

Design rules it enforces (docs/evolution/07-UNIFIED-ARCHITECTURE-SPECIFICATION.md §1):

* **R1** sovereignty — protected modules are digest-verified, and this package
  is itself protected, so the checks cannot be edited away by the code they check.
* **R7** fail closed — a mismatch or an unreadable manifest is an error, not a warning.
* **R9** inert by policy — every enforcement entry point is a pure read; nothing here
  starts a loop, opens a connection, or performs I/O beyond reading this package's files.

Nothing in this package moves existing logic out of its module. The authorities stay
where they are (``security.py``, ``verifier.py``, ``promotion.py``, ``runtime.py``);
what is new here is the *definition of what must not change* and the checks that prove it.
"""

from __future__ import annotations

from .architecture import content_address as content_addressed_architecture_version
from .architecture import resolve_architecture_version
from .eligibility import (
    ELIGIBILITY_VERSION,
    ProtectedComponent,
    TargetKind,
    eligible_target_kinds,
    registry_report,
    protected_components,
    validate_registry,
)
from .invariants import (
    NO_RUNTIME_INVARIANT,
    REGISTRY,
    InvariantConfig,
    InvariantDef,
    InvariantError,
    InvariantObserver,
    InvariantResult,
    enforce_invariants,
    format_report,
    invariant_registry,
    run_invariants,
)
from .protected import (
    ALGORITHM,
    MANIFEST_PATH,
    PROTECTED_PATHS,
    ProtectionReport,
    SovereignDrift,
    compute_digests,
    enforce as enforce_sovereign_digests,
    file_digest,
    load_manifest,
    verify as verify_sovereign_digests,
    write_manifest,
)

__all__ = [
    "ALGORITHM",
    "REGISTRY",
    "ELIGIBILITY_VERSION",
    "MANIFEST_PATH",
    "NO_RUNTIME_INVARIANT",
    "PROTECTED_PATHS",
    "ProtectionReport",
    "SovereignDrift",
    "InvariantConfig",
    "InvariantDef",
    "InvariantError",
    "InvariantObserver",
    "InvariantResult",
    "ProtectedComponent",
    "TargetKind",
    "compute_digests",
    "content_addressed_architecture_version",
    "resolve_architecture_version",
    "eligible_target_kinds",
    "enforce_invariants",
    "enforce_sovereign_digests",
    "file_digest",
    "format_report",
    "invariant_registry",
    "load_manifest",
    "protected_components",
    "registry_report",
    "run_invariants",
    "validate_registry",
    "verify_sovereign_digests",
    "write_manifest",
]
