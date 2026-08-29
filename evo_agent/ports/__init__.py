"""Ports: the only shapes an integrated runtime may speak through (07 §6).

A port here is deliberately weak in power and strong in contract. It can request an approval,
report progress, and hand back evidence; it cannot grant approval, cannot declare a goal met,
and cannot keep state the audit never saw. That asymmetry is the whole integration strategy:
DeerFlow and DeepSeek Harness are worth having *inside* Evo precisely because they arrive as
implementations of these interfaces rather than as authorities beside them.
"""

from __future__ import annotations

from .contracts import (
    ArtifactRef,
    BackendAvailability,
    BackendPlan,
    CapabilityRequest,
    EventSink,
    ExecRequest,
    ExecResult,
    ExecutionBackend,
    PortContractError,
    ProviderAvailability,
    Receipt,
    SandboxProvider,
    TurnContext,
    TurnDecision,
    TurnEngine,
    TurnResult,
    VerifierPlugin,
    additive,
    as_tuple,
    call_optional,
    emit_event,
    PORTS,
    optional_members,
    required_members,
    validate_implementation,
)

__all__ = [
    "PORTS",
    "ArtifactRef",
    "BackendAvailability",
    "BackendPlan",
    "CapabilityRequest",
    "EventSink",
    "ExecRequest",
    "ExecResult",
    "ExecutionBackend",
    "PortContractError",
    "ProviderAvailability",
    "Receipt",
    "SandboxProvider",
    "TurnContext",
    "TurnDecision",
    "TurnEngine",
    "TurnResult",
    "VerifierPlugin",
    "additive",
    "as_tuple",
    "call_optional",
    "emit_event",
    "optional_members",
    "required_members",
    "validate_implementation",
]
