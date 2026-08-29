"""Agent modes: what this turn is *allowed to change*.

Plan mode is a read-only phase, ported from the pattern in ``06`` §3.6 rather than copied, and it is
enforced in exactly one place per kind of effect:

* **tool execution** - :meth:`evo_agent.sovereign.mediation.ApprovalMediator._decide` consults
  :func:`refuses_in_plan_mode` before it considers approval, so a plan-mode refusal is not "needs
  approval". Treating it as an approval question would be the classic mistake: the operator says yes once,
  and the mode has silently become build mode.
* **what a model is offered** - :class:`evo_agent.tools.ToolCatalog` drops the refused tools from
  ``offered()``, so a plan-mode turn does not spend its reasoning on a tool it cannot use, and the reason is
  in the usability report rather than in a comment.
* **durable capability changes** - skill staging and promotion are refused while the mode is plan, because
  "read-only phase" that can still install a capability is not read-only in the sense anyone cares about.

The rule is *classificatory*, not a list of names. A list of forbidden tools is a list of the tools the
author knew about; the classifier asks whether the call writes, spawns, or carries a risk level an
approving turn would be needed for, so a tool added next month is refused by the same sentence. When the
classification cannot decide - an unknown tool whose descriptor is not in the registry - plan mode refuses.
That is the whole reason the mode is worth having: it is a *closed* set of effects, not an open list.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any


class AgentMode(str, Enum):
    """The two modes this build implements. There is no ``auto``, on purpose.

    A mode that selects itself is a mode an agent can change by reasoning about it, which is what plan mode
    exists to prevent; the operator picks, the CLI carries the choice, and the runtime refuses to start a
    cycle whose mode nobody set.
    """

    BUILD = "build"
    PLAN = "plan"

    @classmethod
    def parse(cls, value: Any) -> "AgentMode":
        text = str(value or "").strip().lower().replace("-", "_")
        try:
            return cls(text)
        except ValueError:
            # Clamped to the *narrower* mode, which is the same discipline ``SecurityPolicy`` applies to a
            # ceiling it cannot recognise: a mistyped ``"plna"`` in a policy file must not quietly buy back
            # write access. ``evo status`` then shows ``mode: plan`` with its refusal list, which is how the
            # operator finds the typo - louder than a warning line, and it cannot be missed by an agent.
            return cls.PLAN


#: Names with an obvious durable effect. Kept because a name is the strongest evidence available for the
#: tools this build ships; the risk and permission legs below are what cover the rest.
PLAN_FORBIDDEN_TOOLS = frozenset({"workspace_write", "shell", "process_spawn", "file_delete", "apply_patch"})
#: Anything at or above this level needs an approving turn in build mode, so it needs more than a
#: permission slip in a read-only one.
PLAN_FORBIDDEN_RISKS = frozenset({"high", "critical"})
#: Permission substrings that mean "this call can change state".
PLAN_FORBIDDEN_PERMISSIONS = ("write", "edit", "create", "delete", "append", "execute", "spawn", "install")


def is_plan_mode(policy: Any) -> bool:
    return AgentMode.parse(getattr(policy, "agent_mode", AgentMode.BUILD.value)) is AgentMode.PLAN


def refuses_in_plan_mode(tool_name: Any, *, risk: Any = None, permissions: Any = (), known: bool = True) -> tuple[bool, str]:
    """``(refused, reason)``. Never returns ``(True, "")``; a refusal without a reason cannot be audited."""
    name = str(tool_name or "").strip()
    risk_value = getattr(risk, "value", risk)
    risk_text = str(risk_value or "").strip().lower()
    if not known:
        return True, (
            f"'{name}' has no descriptor in this build's registry, and plan mode refuses what it cannot "
            "classify as read-only"
        )
    if name in PLAN_FORBIDDEN_TOOLS:
        return True, f"'{name}' changes state, and plan mode is a read-only phase"
    if risk_text in PLAN_FORBIDDEN_RISKS:
        return True, f"'{name}' is classified '{risk_text}'-risk; a read-only phase does not take approval as a substitute for not needing it"
    for permission in tuple(permissions or ()):
        text = str(permission or "").strip().lower()
        if any(marker in text for marker in PLAN_FORBIDDEN_PERMISSIONS):
            return True, f"'{name}' declares permission '{permission}', which plan mode does not grant"
    return False, ""


@dataclass(frozen=True)
class ModeReport:
    """What the mode decides, and on what evidence. What ``evo status`` shows."""

    mode: str
    enforced: bool
    refusals: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {"mode": self.mode, "enforced": self.enforced, "refusals": list(self.refusals)}


def report(policy: Any, *, mediator: Any = None) -> dict[str, Any]:
    """The mode in force plus the tools it currently refuses, from the live registry."""
    plan = is_plan_mode(policy)
    refused: list[str] = []
    registry = getattr(mediator, "registry", None) if mediator is not None else None
    if registry is None:
        registry = getattr(policy, "registry", None)
    for name, spec in sorted(getattr(registry, "_tools", {}) .items()):
        refused_now, _reason = refuses_in_plan_mode(name, risk=getattr(spec, "risk", None), permissions=getattr(spec, "permissions", ()))
        if refused_now:
            refused.append(name)
    return ModeReport(
        mode=AgentMode.parse(getattr(policy, "agent_mode", "build")).value,
        enforced=plan,
        refusals=tuple(refused) if plan else (),
    ).to_dict()
