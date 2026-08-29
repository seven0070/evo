"""Backends: the seams where an external runtime may serve an Evo capability (07 §5).

Nothing in this package owns a loop, a memory store, or a verdict. The registry plans a
capability request across whatever backends are installed and routes a turn to one; each backend
then either delegates back to Evo's own kernel (``native``), drives a confined child process that
must ask permission for every action (``lead_agent``), or runs an external CLI as a single
observation-yielding process (``dsh``).

That variety is the point. Two upstreams with completely different architectures become one
system not by being merged into a common loop but by agreeing to the same *seam*: they may propose
work and describe results, and everything irreversible still goes through Evo's mediator, sandbox,
memory, and verifier. An integration that could not be expressed this way is an integration that
would have needed its own governance - which is what the unified design is meant to end.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from ..ports.contracts import CapabilityRequest, TurnContext
from .availability import AVAILABLE, DEGRADED, UNAVAILABLE, AvailabilityReport, BackendReport
from .dsh import DeepSeekHarnessBackend, HarnessConfigError, render_template
from .lead_agent import LeadAgentBackend, LeadAgentConfigError
from .native import NativeBackend
from .registry import BackendConflict, BackendContractError, BackendRegistry, Registration


#: The names a configuration file may name. Anything else is a startup error rather than a
#: fall-through, because a typo in a backend name reads exactly like a capability being unavailable.
KNOWN_BACKENDS = ("native", "lead_agent", "dsh")


class UnknownBackend(ValueError):
    """A configuration named a backend that does not exist."""


@dataclass(frozen=True)
class BackendDefaults:
    """The wiring a runtime hands to every backend it starts.

    Grouped into one object so that ``build_default_registry`` has a single place to say what a
    backend needs to behave itself: a mediator (the authority), an event callback (the record), and
    a workspace (the boundary). A backend constructed without a mediator is refused at probe time,
    which is why this is not just a bag of keyword arguments.
    """

    workspace: Path
    policy: Any | None = None
    mediator: Any | None = None
    on_event: Callable[[str, dict[str, Any]], None] | None = None
    tool_names: tuple[str, ...] = ()


def build_default_registry(
    defaults: BackendDefaults,
    *,
    config: dict[str, Any] | None = None,
    turn_executor: Any = None,
) -> BackendRegistry:
    """Assemble the registry from ``[backends.*]`` configuration. Unknown keys raise.

    ``native`` is registered first and cannot be turned off by configuration: if it could, a
    mistuned ``[backends]`` section would leave a run with no backend that Evo's own memory and
    verification authorities are wired into, and the failure would surface as a refusal to plan
    anything at all.
    """
    config = dict(config or {})
    unknown = sorted(set(config) - set(KNOWN_BACKENDS))
    if unknown:
        raise UnknownBackend(
            f"unknown backend section(s): {', '.join(unknown)}; this build knows {', '.join(KNOWN_BACKENDS)}"
        )
    registry = BackendRegistry(on_event=defaults.on_event, policy=defaults.policy, mediator=defaults.mediator)
    registry.register(
        NativeBackend(
            policy=defaults.policy,
            turn_executor=turn_executor,
            tool_names=defaults.tool_names,
            on_event=defaults.on_event,
        ),
        capabilities=defaults.tool_names,
        notes=("Evo's own kernel; owns memory, verification, promotion, and rollback",),
    )
    lead_config = dict(config.get("lead_agent") or {})
    if lead_config:
        registry.register(
            LeadAgentBackend(
                mediator=defaults.mediator,
                workspace=defaults.workspace,
                venv_python=lead_config.get("venv"),
                driver=lead_config.get("driver"),
                advertised_tools=tuple(lead_config.get("tools") or defaults.tool_names),
                required_imports=tuple(lead_config.get("required_imports") or ("langgraph",)),
                enabled=bool(lead_config.get("enabled", False)),
                turn_timeout_seconds=float(lead_config.get("turn_timeout_seconds", 300.0)),
                on_event=defaults.on_event,
            ),
            source=str(lead_config.get("source", "bytedance/deer-flow")),
            license=str(lead_config.get("license", "MIT")),
            source_url=str(lead_config.get("source_url", "https://github.com/bytedance/deer-flow")),
            accepted_by=str(lead_config.get("accepted_by", "")),
            enabled=bool(lead_config.get("enabled", False)),
            priority=int(lead_config.get("priority", 10)),
            capabilities=tuple(lead_config.get("tools") or defaults.tool_names),
            notes=("optional harness; every execution still passes the mediator",),
        )
    dsh_config = dict(config.get("dsh") or {})
    if dsh_config:
        registry.register(
            DeepSeekHarnessBackend(
                executable=str(dsh_config.get("executable", "deepseek-harness")),
                arguments_template=tuple(dsh_config.get("arguments") or ("--prompt", "{goal}", "--workspace", "{workspace}")),
                workspace=defaults.workspace,
                mediator=defaults.mediator,
                enabled=bool(dsh_config.get("enabled", False)),
                turn_timeout_seconds=float(dsh_config.get("turn_timeout_seconds", 180.0)),
                on_event=defaults.on_event,
            ),
            source=str(dsh_config.get("source", "deepseek-ai/deepseek-harness")),
            license=str(dsh_config.get("license", "MIT")),
            source_url=str(dsh_config.get("source_url", "https://github.com/deepseek-ai/deepseek-harness")),
            accepted_by=str(dsh_config.get("accepted_by", "")),
            enabled=bool(dsh_config.get("enabled", False)),
            priority=int(dsh_config.get("priority", 5)),
            capabilities=tuple(dsh_config.get("advertise") or ("execute", "read", "write")),
            notes=("process adapter; its session log is not Evo's memory",),
        )
    return registry


__all__ = [
    "AVAILABLE",
    "AvailabilityReport",
    "BackendConflict",
    "HarnessConfigError",
    "BackendContractError",
    "BackendDefaults",
    "BackendReport",
    "BackendRegistry",
    "CapabilityRequest",
    "DEGRADED",
    "DeepSeekHarnessBackend",
    "KNOWN_BACKENDS",
    "LeadAgentBackend",
    "LeadAgentConfigError",
    "NativeBackend",
    "Registration",
    "TurnContext",
    "UNAVAILABLE",
    "UnknownBackend",
    "build_default_registry",
    "render_template",
]
