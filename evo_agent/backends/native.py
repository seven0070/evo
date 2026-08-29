"""The native backend: Evo's own loop, expressed as a backend (07 §3.1).

``NativeBackend`` is deliberately thin. It owns no loop, no tool dispatch, and no retry policy -
those live in ``kernel.py``, which the ``I-single-loop`` invariant protects. What it does own is
*accounting*: which turn ran, how many tool calls it made, which receipts came out. That is the
minimum needed for the planner in :mod:`evo_agent.backends.registry` to compare "Evo does this"
against "the lead agent does this" on the same axis, and for a receipt export to be able to say
where a fact came from.

The turn callable is injected by the runtime rather than imported from it. Importing ``runtime``
here would make a backend depend on the thing that selects backends, and the cycle would be
resolved by whichever module got imported first - a startup order that no test would catch until a
future refactor changed it.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
import time
from typing import Any, Callable, Sequence

from ..ports.contracts import (
    BackendAvailability,
    BackendPlan,
    CapabilityRequest,
    Receipt,
    TurnContext,
    TurnResult,
)
from ..security import SecurityPolicy


#: ``runtime`` injects a callable that runs exactly one turn of Evo's own loop and returns a
#: :class:`TurnResult`. Returning ``TurnResult`` (rather than Evo's ``TaskOutcome``) is the point:
#: the planner must not have to know which loop produced an answer to decide whether to use it.
TurnExecutor = Callable[[TurnContext], TurnResult]


@dataclass
class TurnAccounting:
    """Per-turn counters. Not a verdict: it never says whether the goal was met."""

    turn_id: str
    started_at: float
    finished_at: float | None = None
    tool_calls: int = 0
    receipts: tuple[Receipt, ...] = ()
    origin: str = "native"
    notes: list[str] = field(default_factory=list)

    @property
    def duration_ms(self) -> float:
        if self.finished_at is None:
            return 0.0
        return (self.finished_at - self.started_at) * 1000.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "turn_id": self.turn_id,
            "tool_calls": self.tool_calls,
            "duration_ms": round(self.duration_ms, 3),
            "receipts": [receipt.to_dict() for receipt in self.receipts],
            "origin": self.origin,
            "notes": list(self.notes),
        }


class NativeBackend:
    """Adapts Evo's own execution to :class:`~evo_agent.ports.contracts.ExecutionBackend`."""

    name = "native"

    def __init__(
        self,
        *,
        policy: SecurityPolicy | None = None,
        turn_executor: TurnExecutor | None = None,
        tool_names: Sequence[str] | None = None,
        model_available: bool | None = None,
        on_event: Callable[[str, dict[str, Any]], None] | None = None,
    ) -> None:
        self.policy = policy
        self.turn_executor = turn_executor
        self.tool_names = tuple(tool_names) if tool_names is not None else ()
        self._model_available = model_available
        self.on_event = on_event
        self._ledger: dict[str, TurnAccounting] = {}

    # -- port: probe -------------------------------------------------------
    def probe(self) -> BackendAvailability:
        """Native is available when a turn executor is wired and a model can be reached.

        Both conditions are reported rather than assumed. A backend that claims availability while
        un-wired turns a configuration mistake into a mid-run refusal, and the operator then has to
        work out which of the two it was.
        """
        detail: dict[str, Any] = {"turn_executor_wired": self.turn_executor is not None, "tools": list(self.tool_names)}
        if self.turn_executor is None:
            return BackendAvailability(
                self.name,
                True,
                "no turn executor injected; run_turn refuses until the runtime wires itself",
                detail=detail,
            )
        if self._model_available is False:
            return BackendAvailability(self.name, True, "no model configured; rule-based fallback only", detail=detail)
        return BackendAvailability(self.name, True, "", detail=detail)

    # -- port: plan --------------------------------------------------------
    def plan_capability(self, request: CapabilityRequest) -> BackendPlan:
        """Native can serve anything the tool registry can, and never needs a capability it lacks.

        The check is against the *registered* tool names, because a request naming a capability that
        exists only as a model description (``web_research`` and friends) must not be answered by a
        confident "yes" here; that is the exact drift the documented tool table in ``ARCHITECTURE.md``
        exists to prevent.
        """
        missing = tuple(name for name in request.needed if self.tool_names and name not in self.tool_names)
        if missing:
            return BackendPlan(
                False,
                f"native tool registry has no {', '.join(missing)}; that capability is declared, not executable",
                estimated_turns=None,
                degradation="capability not implemented",
            )
        approval = self._approval_required_for(request)
        turns = max(1, min(len(request.needed) + 1, 12))
        return BackendPlan(
            True,
            "Evo's own single loop, with full memory, verification, and rollback authority",
            estimated_turns=turns,
            requires_approval_for=approval,
        )

    def _approval_required_for(self, request: CapabilityRequest) -> tuple[str, ...]:
        if self.policy is None or not request.permissions:
            return ()
        from ..models import RiskLevel, ToolCall

        gated: list[str] = []
        for name in request.permissions:
            try:
                risk = RiskLevel(name) if name in {level.value for level in RiskLevel} else RiskLevel.LOW
            except ValueError:
                risk = RiskLevel.LOW
            if self.policy.requires_approval(ToolCall(tool_name=name, risk=risk)):
                gated.append(name)
        return tuple(gated)

    # -- port: run ---------------------------------------------------------
    def run_turn(self, context: TurnContext, sink: Any = None) -> TurnResult:
        """Run one turn through the injected executor, accounting for it. No loop lives here.

        Parallel tool calls are clamped to [1, 10] here rather than trusted from the caller
        (07 R6): a caller asking for 5,000 simultaneous executions is asking for a resource
        exhaustion the backend should not be able to deliver.
        """
        if self.turn_executor is None:
            return TurnResult(
                status="refused",
                notes=("native backend has no turn executor wired; the runtime wires it after construction",),
                origin=self.name,
            )
        accounting = TurnAccounting(turn_id=context.turn_id, started_at=time.monotonic())
        self._ledger[context.turn_id] = accounting
        try:
            result = self.turn_executor(context)
        except Exception as exc:
            accounting.finished_at = time.monotonic()
            accounting.notes.append(f"turn raised {type(exc).__name__}: {exc}")
            self._emit("backend_turn_failed", {"turn_id": context.turn_id, "error": f"{type(exc).__name__}: {exc}"})
            return TurnResult(status="failed", text=f"{type(exc).__name__}: {exc}", origin=self.name, notes=(accounting.notes[-1],))
        accounting.finished_at = time.monotonic()
        receipts = tuple(result.receipts)
        accounting.receipts = receipts
        accounting.tool_calls = len(receipts)
        raw = context.metadata.get("max_parallel_tool_calls", 1)
        try:
            parallel = int(raw)
        except (TypeError, ValueError):
            parallel = 1
            accounting.notes.append(f"max_parallel_tool_calls {raw!r} is not a number; using 1")
        bounded = max(1, min(parallel, 10))
        if bounded != parallel:
            note = f"max_parallel_tool_calls clamped {parallel} -> {bounded}"
            accounting.notes.append(note)
            result = dataclasses.replace(result, notes=(*result.notes, note))
        self._emit(
            "backend_turn_completed",
            {
                "turn_id": context.turn_id,
                "status": result.status,
                "tool_calls": accounting.tool_calls,
                "duration_ms": round(accounting.duration_ms, 3),
                "origin": self.name,
            },
        )
        return result

    def export_receipts(self, turn_id: str) -> Sequence[Receipt]:
        accounting = self._ledger.get(turn_id)
        return tuple(accounting.receipts) if accounting else ()

    def ledger(self) -> tuple[TurnAccounting, ...]:
        return tuple(self._ledger[key] for key in sorted(self._ledger))

    def _emit(self, event: str, payload: dict[str, Any]) -> None:
        if self.on_event is None:
            return
        try:
            self.on_event(event, payload)
        except Exception:
            pass  # R9: accounting never breaks the action it is watching
