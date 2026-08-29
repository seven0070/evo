"""Stage declarations, the ordering rules over them, and the turn engine that applies them.

Read this module as a contract with three parts. The ``PIPELINE`` tuple is the order the runtime
promises to follow, each stage carrying the placement it affects and the reason it exists at that
position. ``validate_order`` is the refusal that keeps that promise non-rotten: a reordered chain -
an overlay, a plugin, a future refactor - is rejected with reasons instead of quietly changing which
sanitizer sees a payload first. ``TurnPipeline`` is the object the runtime actually consults, and it
implements the :class:`~evo_agent.ports.contracts.TurnEngine` port, which is how the loop and the
backend seam stop being two ideas that never met.

``next_turn`` deliberately does not plan, judge, or verify. It turns a *proposal* (whatever produced
it: the model, the rule-based fallback, a bridge's child process) into one bounded decision, and it
refuses when the guards say the turn has already spent its allowance. The verdict on whether a goal
was met belongs to the verifier; the authority to allow an action belongs to the mediator; the
bytes that get written belong to the sandbox. A pipeline that also held one of those would not be a
pipeline, it would be a second runtime with nicer diagrams.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
import json
from pathlib import Path
import re
from typing import Any, Iterable, Sequence

from ..context import CompactReport, compact_text, meter, spill_text
from ..ports.contracts import TurnContext, TurnDecision, call_optional


class Placement:
    """What a stage is allowed to affect. Four values, because four questions exist.

    ``MODEL_LOGICAL`` is what the model is *told* exists; ``MODEL_PHYSICAL`` is what is actually in
    the prompt. The two differ under compaction and deferral, and pretending they are one number is
    how a system ends up describing a tool the model can never see. ``TOOL_VISIBLE`` is what the
    handler receives. ``RUNTIME`` is a stage whose whole effect is server-side - a guard that refuses
    is invisible to the model, which is correct, because a model that can see a guard can negotiate
    with it.
    """

    MODEL_LOGICAL = "model_logical"
    MODEL_PHYSICAL = "model_physical"
    TOOL_VISIBLE = "tool_visible"
    RUNTIME = "runtime"


PLACEMENTS: tuple[str, ...] = (
    Placement.MODEL_LOGICAL,
    Placement.MODEL_PHYSICAL,
    Placement.TOOL_VISIBLE,
    Placement.RUNTIME,
)

#: The turn edge is ordered; the tool edge is *nested*, and the nesting depth is what decides who
#: sees the result first on the way out. Zero is outermost.
EDGE_TURN = "turn"
EDGE_TOOL = "tool"


@dataclass(frozen=True)
class Stage:
    """One declared position in the order, with the reason it holds it."""

    name: str
    placement: str
    reason: str
    edge: str = EDGE_TURN
    #: Tool-edge nesting: 0 is outermost. ``-1`` for stages that are not on the tool edge.
    depth: int = -1
    #: The guarantee this stage installs, named so a check can assert it exists rather than infer it.
    invariant: str = ""
    #: Names from the heuristics allow-list this stage reads. Empty means "not tunable by data".
    params: tuple[str, ...] = ()
    #: A stage that may not be turned off by configuration, whatever a candidate proposes.
    mandatory: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "placement": self.placement,
            "reason": self.reason,
            "edge": self.edge,
            "depth": self.depth,
            "invariant": self.invariant,
            "params": list(self.params),
            "mandatory": self.mandatory,
        }


@dataclass(frozen=True)
class StageAction:
    """A concrete effect a stage has on one turn: a bound applied, a name dropped, a refusal."""

    name: str
    value: Any = None
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "value": self.value, "reason": self.reason}


@dataclass(frozen=True)
class PipelineDecision:
    """What one stage decided for this turn. Ordered, and the order is the record."""

    stage: str
    placement: str
    reason: str
    enabled: bool = True
    actions: tuple[StageAction, ...] = ()
    refused: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "stage": self.stage,
            "placement": self.placement,
            "enabled": self.enabled,
            "actions": [item.to_dict() for item in self.actions],
            "refused": self.refused,
            "reason": self.reason,
        }


#: The order, once. Positions are fixed by ``validate_order`` below; a candidate may tune a stage's
#: parameters (``config/heuristics.json``) but may not move, remove, or disable a mandatory one,
#: because 07 §4 allows evolution to reach "stage selection and stage parameters from a reviewed
#: allow-list; never stage source".
PIPELINE: tuple[Stage, ...] = (
    Stage(
        "input_sanitize",
        Placement.RUNTIME,
        "the goal is caller-supplied text; it is scrubbed before anything interprets it, so no "
        "downstream stage ever sees an unscrubbed control sequence",
        invariant="no-untrusted-input-passthrough",
        mandatory=True,
    ),
    Stage(
        "token_budget",
        Placement.MODEL_PHYSICAL,
        "history is bounded before it is assembled, because a budget applied after assembly is a "
        "budget that was already spent",
        params=("history_entries", "token_budget"),
    ),
    Stage(
        "loop_guard",
        Placement.RUNTIME,
        "the turn allowance is the runtime's, never this module's: the guard compares turns already "
        "spent against the budget the context carries, so there is one number to review and a "
        "candidate cannot enlarge the ceiling it is measured against",
        invariant="bounded-turns",
        mandatory=True,
    ),
    Stage(
        "repeat_guard",
        Placement.RUNTIME,
        "identical calls repeated are the signature of a loop that believes it is making progress; "
        "detected before dispatch so the repetition is not paid for again",
        params=("repeat_limit", "repeat_window"),
        mandatory=True,
    ),
    Stage(
        "deferred_tool_filter",
        Placement.MODEL_LOGICAL,
        "tools the mode has not unlocked are withheld from the model rather than refused at call "
        "time: a refusal the model can see is an invitation to try again",
    ),
    Stage(
        "read_before_write",
        Placement.RUNTIME,
        "an overwrite of content nobody read is data loss with a success code attached; the check "
        "belongs before dispatch because after dispatch there is nothing left to prevent. P4 records "
        "the decision; enforcement is the write-set's, and per-turn read tracking is P5 work (08).",
        invariant="no-blind-overwrite",
        params=("read_before_write",),
    ),
    Stage(
        "policy_filter",
        Placement.MODEL_PHYSICAL,
        "the permission decision is made once, on canonical names, before any tool is offered - a "
        "tool that is not in the granted set must not reach the model as a temptation",
        invariant="single-permission-authority",
        mandatory=True,
    ),
    Stage(
        "DISPATCH",
        Placement.TOOL_VISIBLE,
        "the loop's one tool dispatch; the pipeline declares where it sits and never performs it",
        mandatory=True,
    ),
    Stage(
        "tool_result_sanitize",
        Placement.TOOL_VISIBLE,
        "output crosses a trust boundary on the way back; sanitizing inside the budget layer keeps "
        "secrets out of prompts, and it runs before truncation so nothing is hidden by a cut",
        edge=EDGE_TOOL,
        depth=2,
        mandatory=True,
    ),
    Stage(
        "output_budget",
        Placement.MODEL_PHYSICAL,
        "truncation is applied to already-sanitized bytes, and the marker says so, so a reader "
        "never mistakes a clipped payload for the whole payload",
        edge=EDGE_TOOL,
        depth=1,
        params=("model_output_budget_bytes", "spill_threshold_bytes"),
    ),
    Stage(
        "RECEIPTS",
        Placement.RUNTIME,
        "outermost on the tool edge: a receipt must attest to the bytes the caller actually "
        "received, which is nothing until every inner stage has finished with them",
        edge=EDGE_TOOL,
        depth=0,
        invariant="receipt-attests-delivered-bytes",
        mandatory=True,
    ),
    Stage(
        "error_handling",
        Placement.TOOL_VISIBLE,
        "a failure is classified before it is reported, because 'the tool failed' and 'the operator "
        "denied the tool' need opposite responses from the loop",
        edge=EDGE_TOOL,
        depth=3,
    ),
    Stage(
        "compaction",
        Placement.MODEL_PHYSICAL,
        "compaction corrects the meter, so it follows it; running first would make the accounting "
        "describe bytes that were already dropped",
        params=("compaction_ratio", "history_entries"),
    ),
    Stage(
        "inbox",
        Placement.RUNTIME,
        "operator input arriving at a step boundary is drained last: earlier, a mid-flight "
        "intervention could rewrite the very turn whose receipts it is reacting to",
    ),
)

STAGE_NAMES: tuple[str, ...] = tuple(stage.name for stage in PIPELINE)


class PipelineOrderingError(ValueError):
    """A stage chain that violates the declared order. Refused, never clamped into place."""


#: Tunable knobs, with the bounds a payload must fall inside. These are exactly the names
#: ``config/heuristics.json`` may carry: a knob nothing reads would be dead config, and a name the
#: loader does not know is refused rather than ignored.
HEURISTIC_PARAMS: dict[str, tuple[int, int]] = {
    "history_entries": (1, 256),
    "token_budget": (256, 262_144),
    "repeat_limit": (1, 16),
    "repeat_window": (2, 64),
    "model_output_budget_bytes": (1024, 262_144),
    "spill_threshold_bytes": (1024, 1_048_576),
    "compaction_ratio": (10, 95),
    "read_before_write": (0, 1),
}

#: Stage names that may be turned off by data. Everything else is mandatory: the rule is not
#: "these knobs are nice" but "these are the only ones an overlay may reach at all".
TOGGLEABLE_STAGES: tuple[str, ...] = (
    "token_budget",
    "deferred_tool_filter",
    "read_before_write",
    "compaction",
    "output_budget",
    "inbox",
    "error_handling",
)

#: The only strategies the pipeline can order. Mirrors ``STRATEGY_NAMES`` in
#: :mod:`evo_agent.active_version`, which is the allow-list an overlay is validated against; the
#: duplication is checked, not assumed (see ``tests/test_pipeline_engine.py``).
PIPELINE_STRATEGIES: tuple[str, ...] = ("cognitive-bounded",)


def stage_named(name: str) -> Stage:
    for stage in PIPELINE:
        if stage.name == name:
            return stage
    raise KeyError(f"unknown pipeline stage '{name}'; declared stages: {', '.join(STAGE_NAMES)}")


def validate_order(stages: Sequence[Stage]) -> list[str]:
    """Every ordering rule the pipeline promises, as a returned list of problems.

    Kept as data-against-data rather than asserted in a test only, because the order is also read
    back from configuration by :meth:`TurnPipeline.from_overlay`, and a check that lives in the test
    suite cannot stop a bad chain at load time.
    """
    names = [stage.name for stage in stages]
    problems: list[str] = []
    if len(names) != len(set(names)):
        problems.append("a stage appears twice; the order is not a multiset")
    position = {name: index for index, name in enumerate(names)}

    def first(a: str, b: str, why: str) -> None:
        if a in position and b in position and position[a] > position[b]:
            problems.append(f"{a} must precede {b}: {why}")

    if "input_sanitize" in position and position["input_sanitize"] != 0:
        problems.append("input_sanitize must be first: unsanitized input may reach no other stage")
    first("token_budget", "compaction", "the meter exists before the correction")
    first("loop_guard", "DISPATCH", "a turn ceiling is checked before the turn is paid for")
    first("repeat_guard", "DISPATCH", "a repetition guard that runs after dispatch is a bill")
    first("policy_filter", "DISPATCH", "the permission decision precedes the action it permits")
    first("read_before_write", "DISPATCH", "an overwrite cannot be un-done by a later stage")
    first("DISPATCH", "tool_result_sanitize", "there is no result before the result exists")
    first("tool_result_sanitize", "output_budget", "truncation applies to sanitized bytes")

    missing = [name for name in STAGE_NAMES if name not in names]
    extra = [name for name in names if name not in STAGE_NAMES]
    if missing:
        problems.append("declared stage(s) absent: " + ", ".join(missing))
    if extra:
        problems.append("stage(s) this build does not declare: " + ", ".join(extra))

    tool_edge = [stage for stage in stages if stage.edge == EDGE_TOOL]
    if tool_edge:
        outermost = sorted(tool_edge, key=lambda stage: stage.depth)[0]
        if outermost.name != "RECEIPTS":
            problems.append(
                f"RECEIPTS must be outermost on the tool edge, found {outermost.name} at depth {outermost.depth}"
            )
        depths = [stage.depth for stage in tool_edge]
        if len(depths) != len(set(depths)):
            problems.append("two tool-edge stages claim the same nesting depth")
    for stage in stages:
        if stage.placement not in PLACEMENTS:
            problems.append(f"{stage.name}: placement '{stage.placement}' is not one of {', '.join(PLACEMENTS)}")
        if not stage.reason.strip():
            problems.append(f"{stage.name}: a stage without a reason is a stage nobody can defend")
        for param in stage.params:
            if param not in HEURISTIC_PARAMS:
                problems.append(f"{stage.name}: parameter '{param}' is not on the reviewed allow-list")
        if stage.name == "DISPATCH" and stage.depth != -1:
            problems.append("DISPATCH is a position in the turn order, not a tool-edge wrapper")
        if stage.edge == EDGE_TURN and stage.depth != -1:
            problems.append(f"{stage.name}: a turn-edge stage has no nesting depth to declare")
        if stage.mandatory and stage.name in TOGGLEABLE_STAGES:
            problems.append(f"{stage.name}: mandatory and toggleable at once; pick one")
    return problems


def declared_invariants() -> dict[str, str]:
    """``invariant -> stage`` for every guarantee a stage installs."""
    return {stage.invariant: stage.name for stage in PIPELINE if stage.invariant}


def _weights_from(payload: Any) -> tuple[dict[str, int], list[str]]:
    """Read ``{"weights": {...}}`` against the allow-list. ``(usable, refusals)``.

    Out-of-range and unknown names are refused *individually*: a payload with one bad knob does not
    silently lose the rest, and it is never clamped into the nearest legal value. That is the same
    rule :class:`~evo_agent.active_version.Field` applies, for the same reason - a candidate that
    learns its numbers were adjusted has learned that the numbers are advisory.
    """
    body = payload.get("weights") if isinstance(payload, dict) else None
    if not isinstance(body, dict):
        return {}, ["config/heuristics.json: expected an object with a 'weights' map"]
    usable: dict[str, int] = {}
    refused: list[str] = []
    for key, value in body.items():
        name = str(key)
        if name not in HEURISTIC_PARAMS:
            refused.append(f"heuristics.weights.{name}: not a knob this build reads")
            continue
        if isinstance(value, bool) or not isinstance(value, int):
            refused.append(f"heuristics.weights.{name}: expected an integer")
            continue
        low, high = HEURISTIC_PARAMS[name]
        if value < low or value > high:
            refused.append(f"heuristics.weights.{name}: {value} is outside [{low}, {high}]")
            continue
        usable[name] = value
    return usable, refused


def _strategies_from(payload: Any) -> tuple[tuple[str, ...], tuple[str, ...], list[str]]:
    """Read ``config/strategy.json``: a preference order and a fallback order, names all reviewed."""
    if not isinstance(payload, dict):
        return (), (), ["config/strategy.json: expected an object"]
    refused: list[str] = []
    orders: list[tuple[str, ...]] = []
    for key in ("preferred_strategies", "fallback_strategies"):
        raw = payload.get(key, [])
        if raw is None:
            raw = []
        if not isinstance(raw, (list, tuple)):
            refused.append(f"strategy.{key}: expected a list of names")
            orders.append(())
            continue
        names: list[str] = []
        for item in raw:
            name = str(item)
            if name not in PIPELINE_STRATEGIES:
                refused.append(f"strategy.{key}: '{name}' is not a strategy this build can run")
                continue
            if name not in names:
                names.append(name)
        orders.append(tuple(names))
    return orders[0], orders[1], refused


class TurnPipeline:
    """The per-turn decision record, built from the declared order and the active overlay.

    The runtime asks it three things: ``prepare`` before a turn (what the model gets, and whether the
    turn may run at all), ``finish`` after a turn (what is recorded, and what is truncated), and
    ``next_turn`` for the single bounded decision the :class:`TurnEngine` port promises. ``compact``
    completes that port. Nothing here loops, dispatches, or decides that a goal was met.
    """

    def __init__(
        self,
        *,
        weights: dict[str, int] | None = None,
        disabled: Iterable[str] | None = None,
        preferred_strategies: Sequence[str] = (),
        fallback_strategies: Sequence[str] = (),
        stages: Sequence[Stage] = PIPELINE,
        granted_tools: Sequence[str] = (),
        gated_tools: Sequence[str] = (),
        ledger: Any = None,
        spill_root: Path | None = None,
        on_event: Any = None,
    ) -> None:
        self.stages: tuple[Stage, ...] = tuple(stages)
        self.weights: dict[str, int] = {**_default_weights(), **(weights or {})}
        self.disabled: frozenset[str] = frozenset(disabled or ())
        self.preferred_strategies = tuple(preferred_strategies)
        self.fallback_strategies = tuple(fallback_strategies)
        self.granted_tools = tuple(granted_tools)
        #: Names that exist but may not run until the operator says so. They are withheld from the
        #: offered view rather than refused at call time: a model that can see a gated tool spends
        #: turns negotiating with it, and the refusal it receives looks like a tool defect.
        self.gated_tools = tuple(gated_tools)
        self.ledger = ledger
        self.spill_root = spill_root
        self.on_event = on_event
        problems = validate_order(self.stages)
        if problems:
            raise PipelineOrderingError("; ".join(problems))

    # -- construction from data --------------------------------------------
    @classmethod
    def from_overlay(
        cls,
        overlay: Any,
        *,
        granted_tools: Sequence[str] = (),
        gated_tools: Sequence[str] = (),
        ledger: Any = None,
        spill_root: Path | None = None,
        on_event: Any = None,
    ) -> "TurnPipeline":
        """Build from an :class:`~evo_agent.active_version.ActiveOverlay`'s documents.

        Absent documents are the common case and mean defaults; a *present* document that the loader
        cannot honour is a startup failure. That asymmetry is deliberate: an overlay is allowed not to
        mention a stage, and is not allowed to mention one wrongly.
        """
        weights: dict[str, int] = {}
        disabled: list[str] = []
        preferred: tuple[str, ...] = ()
        fallback: tuple[str, ...] = ()
        problems: list[str] = []
        documents = getattr(overlay, "documents", None) or {}
        heuristics = _read_document(documents, "config/heuristics.json")
        if heuristics is not None:
            usable, refused = _weights_from(heuristics)
            weights.update(usable)
            problems.extend(refused)
            if isinstance(heuristics, dict) and isinstance(heuristics.get("stages"), dict):
                for name, enabled in heuristics["stages"].items():
                    stage_name = str(name)
                    if stage_name not in STAGE_NAMES:
                        problems.append(f"heuristics.stages.{stage_name}: not a declared stage")
                        continue
                    if stage_name not in TOGGLEABLE_STAGES and not bool(enabled):
                        problems.append(
                            f"heuristics.stages.{stage_name}: mandatory, it cannot be disabled by data"
                        )
                        continue
                    if not bool(enabled):
                        disabled.append(stage_name)
        strategies = _read_document(documents, "config/strategy.json")
        if strategies is not None:
            preferred, fallback, refused = _strategies_from(strategies)
            problems.extend(refused)
        if problems:
            raise PipelineOrderingError("; ".join(problems))
        return cls(
            weights=weights,
            disabled=disabled,
            preferred_strategies=preferred,
            fallback_strategies=fallback,
            granted_tools=granted_tools,
            gated_tools=gated_tools,
            ledger=ledger,
            spill_root=spill_root,
            on_event=on_event,
        )

    # -- the order itself ----------------------------------------------------
    def plan(self, context: TurnContext | None = None) -> tuple[PipelineDecision, ...]:
        """One decision per declared stage, in declared order. The record an auditor reads."""
        history = tuple(getattr(context, "history", ()) or ()) if context is not None else ()
        turn_count = int(getattr(context, "budget_turns", 1) or 1) if context is not None else 1
        requested = tuple(
            str(name)
            for name in (getattr(context, "available_tools", ()) if context is not None else ()) or ()
        )
        spent_turns = int((getattr(context, "metadata", None) or {}).get("turns_spent", 0) or 0) if context is not None else 0
        repeated = _repeat_count(history, int(self.weights["repeat_window"]))
        decisions: list[PipelineDecision] = []
        for stage in self.stages:
            enabled = stage.name not in self.disabled
            actions: list[StageAction] = []
            refused = ""
            if not enabled:
                decisions.append(
                    PipelineDecision(stage.name, stage.placement, "disabled by the active overlay", enabled=False)
                )
                continue
            budget_for_stage = int(self.weights["model_output_budget_bytes"])
            if stage.name == "input_sanitize":
                cleaned, notes = compact_text(getattr(context, "goal", "") if context is not None else "", limit=4096)
                actions.append(StageAction("goal_sanitized", len(cleaned), "; ".join(notes) or "control sequences removed"))
            elif stage.name == "token_budget":
                limit = int(self.weights["history_entries"])
                ceiling = int(self.weights["token_budget"])
                kept = max(0, min(len(history), limit))
                actions.append(StageAction("history_entries", kept, f"bounded to {limit} entries"))
                actions.append(StageAction("token_budget", ceiling, "the meter's ceiling for this turn"))
            elif stage.name == "loop_guard":
                spent = int(spent_turns)
                allowance = max(1, int(turn_count))
                if spent >= allowance:
                    refused = (
                        f"{spent} turn(s) already spent against an allowance of {allowance}; the "
                        "budget belongs to the runtime, so no overlay can raise it from here"
                    )
                actions.append(StageAction("turn_allowance", allowance, "from TurnContext.budget_turns"))
                actions.append(StageAction("turns_spent", spent, "counted by the runtime"))
            elif stage.name == "repeat_guard":
                ceiling = int(self.weights["repeat_limit"])
                if repeated >= ceiling:
                    refused = (
                        f"the last {repeated} recorded steps are identical; a turn that repeats "
                        f"{ceiling} times has stopped making progress"
                    )
                actions.append(StageAction("repeat_count", repeated, "identical trailing entries"))
            elif stage.name == "deferred_tool_filter":
                outside = [name for name in requested if self.granted_tools and name not in self.granted_tools]
                withheld = [name for name in requested if name in self.gated_tools] + outside
                actions.append(
                    StageAction(
                        "withheld",
                        sorted(set(withheld)),
                        "gated on operator approval, or not unlocked for this mode",
                    )
                )
            elif stage.name == "read_before_write":
                if int(self.weights["read_before_write"]):
                    unread = [name for name in requested if name == "workspace_write" and not self._has_read(history)]
                    if unread:
                        actions.append(StageAction("flagged", unread, "a write with no recorded read of that path"))
                else:
                    actions.append(StageAction("off", 0, "disabled by the active overlay"))
            elif stage.name == "policy_filter":
                allowed = [
                    name
                    for name in requested
                    if (not self.granted_tools or name in self.granted_tools) and name not in self.gated_tools
                ]
                actions.append(StageAction("offered", allowed, "canonical names inside the granted set"))
            elif stage.name == "DISPATCH":
                actions.append(StageAction("dispatch_owner", "kernel.run", "the loop, not the pipeline"))
            elif stage.name == "tool_result_sanitize":
                actions.append(StageAction("applied_to", "tool output", "before the output budget"))
            elif stage.name == "output_budget":
                actions.append(
                    StageAction(
                        "model_output_budget_bytes",
                        budget_for_stage,
                        "truncation of already-sanitized bytes; SecurityPolicy.max_output_bytes "
                        "still bounds what the provider captures, which is the other boundary",
                    )
                )
            elif stage.name == "RECEIPTS":
                actions.append(
                    StageAction(
                        "attests",
                        "delivered bytes",
                        "outermost on the tool edge, so the digest is of what the caller received",
                    )
                )
            elif stage.name == "error_handling":
                actions.append(StageAction("classifies", "failure before report", "denial and failure differ"))
            elif stage.name == "compaction":
                ratio = int(self.weights["compaction_ratio"])
                actions.append(StageAction("compaction_ratio", ratio, "only consulted once the meter is over budget"))
            elif stage.name == "inbox":
                actions.append(StageAction("drains", "step boundary", "last, so it cannot rewrite the turn it reacts to"))
            decisions.append(
                PipelineDecision(stage.name, stage.placement, stage.reason, True, tuple(actions), refused)
            )
        return tuple(decisions)

    def prepare(self, context: TurnContext) -> tuple[TurnContext, tuple[PipelineDecision, ...], str]:
        """Apply the turn-edge prefix. Returns ``(context for the model, decisions, refusal)``.

        A non-empty refusal is the whole answer: the turn does not run, and the reason came from a
        declared guard rather than from a timeout nobody configured.
        """
        decisions = self.plan(context)
        goal, _ = compact_text(context.goal, limit=4096)
        entries = int(self.weights["history_entries"])
        history, report = self.compact_history(context.history, entries)
        amended = replace(context, goal=goal, history=history)
        refusal = ""
        for decision in decisions:
            if decision.refused:
                refusal = f"{decision.stage}: {decision.refused}"
                break
        if report.dropped:
            amended = replace(
                amended,
                metadata={**dict(amended.metadata), "compaction": report.to_dict()},
            )
        return amended, decisions, refusal

    def finish(
        self,
        result: Any,
        *,
        turn_id: str = "",
        origin: str = "",
    ) -> tuple[Any, list[dict[str, Any]], list[dict[str, Any]]]:
        """Apply the tool-edge suffix to a ``TurnResult``: sanitize, budget, then attest.

        Returns ``(amended_result, receipts, spill_records)``. The result is amended rather than
        replaced so the backend's own accounting stays attached to it.
        """
        budget = int(self.weights["model_output_budget_bytes"])
        spill_at = int(self.weights["spill_threshold_bytes"])
        text = str(getattr(result, "text", "") or "")
        cleaned, notes = compact_text(text, limit=budget)
        receipts: list[dict[str, Any]] = []
        spills: list[dict[str, Any]] = []
        if len(text.encode("utf-8", "replace")) > spill_at and self.spill_root is not None:
            record = spill_text(cleaned, root=self.spill_root, turn_id=turn_id)
            spills.append(record.to_dict())
        if notes:
            receipts.append({"kind": "note", "turn_id": turn_id, "notes": notes})
        amended = replace(result, text=cleaned, notes=(*tuple(getattr(result, "notes", ()) or ()), *[f"output_budget: {note}" for note in notes]))
        if self.ledger is not None:
            for receipt in tuple(getattr(amended, "receipts", ()) or ()):
                call_optional(self.ledger, "record", receipt)
        return amended, receipts, spills

    def next_turn(self, context: TurnContext) -> TurnDecision:
        """The one step the engine chooses. Never a verdict, never a plan."""
        _amended, decisions, refusal = self.prepare(context)
        if refusal:
            stage, _, detail = refusal.partition(": ")
            return TurnDecision(kind="abstain", text=detail, reason=f"{stage} refused the turn")
        proposal = dict((context.metadata or {}).get("proposal") or {})
        wants_approval = bool(proposal.get("approval_required")) or str(proposal.get("kind") or "") == "request_approval"
        if wants_approval:
            return TurnDecision(
                kind="request_approval",
                approval=dict(proposal.get("approval") or {}),
                reason=str(proposal.get("reason") or "the proposed action needs the operator"),
            )
        calls = tuple(proposal.get("tool_calls") or ())
        if calls:
            return TurnDecision(
                kind="tool_calls",
                tool_calls=calls,
                reason="the loop's proposal is inside the declared guards and the granted tool set",
            )
        return TurnDecision(
            kind="final",
            text=str(proposal.get("text") or ""),
            reason="no further tool call proposed; the turn is finished, not yet verified",
        )

    def compact(self, context: TurnContext, budget: int) -> TurnContext:
        """The port's comp obligation: fewer bytes in, same receipts out."""
        history, _report = self.compact_history(context.history, max(0, int(budget)))
        return replace(context, history=history)

    # -- helpers -------------------------------------------------------------
    def compact_history(self, history: Sequence[dict[str, Any]], entries: int) -> tuple[tuple[dict[str, Any], ...], CompactReport]:
        return compact_history(history, entries)

    def strategy_order(self) -> tuple[str, ...]:
        """Preferred first, then fallback, then the strategy this build always has."""
        ordered = list(dict.fromkeys([*self.preferred_strategies, *self.fallback_strategies]))
        for name in PIPELINE_STRATEGIES:
            if name not in ordered:
                ordered.append(name)
        return tuple(ordered)

    def meter(self, context: TurnContext) -> dict[str, int]:
        return meter(context.goal, context.history).to_dict()

    def _has_read(self, history: Sequence[dict[str, Any]]) -> bool:
        return any(str(item.get("tool") or item.get("kind") or "") in {"workspace_read", "read"} for item in history)

    def to_dict(self) -> dict[str, Any]:
        return {
            "stages": [stage.to_dict() for stage in self.stages],
            "weights": dict(self.weights),
            "disabled": sorted(self.disabled),
            "strategies": {
                "preferred": list(self.preferred_strategies),
                "fallback": list(self.fallback_strategies),
                "available": list(PIPELINE_STRATEGIES),
            },
            "granted_tools": list(self.granted_tools),
            "gated_tools": list(self.gated_tools),
            "invariants": declared_invariants(),
        }


#: A leading name on a history entry marks it pinned: ``[keep]`` survives compaction. The prefix is
#: a convention the runtime uses when it assembles history, not a trust signal - a backend that
#: pinned its own context would be editing the audit trail, which is what the append-only rule is for.
_PINNED = re.compile(r"^\s*\[keep\]")


def compact_history(
    history: Sequence[dict[str, Any]],
    entries: int,
) -> tuple[tuple[dict[str, Any], ...], CompactReport]:
    """Keep the newest ``entries`` entries, plus anything pinned, in original order.

    Ids are never renumbered. DeerFlow's receipts are addressed positionally and its own
    documentation says compaction renumbers them, which turns a citation recorded before compaction
    into a citation of a different call (05 §1.1); Evo's ledger is append-only, so the entry list
    shrinks while the ids stay. That is the whole reason this function may drop entries at all.
    """
    ordered = list(history or ())
    if entries < 0:
        entries = 0
    if len(ordered) <= entries:
        return tuple(ordered), CompactReport(0, 0, len(ordered))
    pinned = [item for item in ordered if _PINNED.match(str(item.get("content") or item.get("text") or ""))]
    tail = ordered[-entries:] if entries else []
    kept: list[dict[str, Any]] = []
    for item in ordered:
        if item in tail or item in pinned:
            if not any(item is existing for existing in kept):
                kept.append(item)
    return tuple(kept), CompactReport(len(ordered) - len(kept), len(pinned), len(kept))


def _default_weights() -> dict[str, int]:
    return {
        "history_entries": 32,
        "token_budget": 16_384,
        "repeat_limit": 3,
        "repeat_window": 8,
        "model_output_budget_bytes": 65_536,
        "spill_threshold_bytes": 32_768,
        "compaction_ratio": 80,
        "read_before_write": 1,
    }


def _read_document(documents: Any, relpath: str) -> Any:
    """Pull one overlay document's body, tolerating str and mapping bodies alike."""
    try:
        body = documents.get(relpath)
    except AttributeError:
        return None
    if body is None:
        return None
    if isinstance(body, (str, bytes)):
        try:
            return json.loads(body)
        except ValueError:
            return None
    return body


def _repeat_count(history: Sequence[dict[str, Any]], window: int = 8) -> int:
    """How many trailing entries are identical, within a bounded look-back window.

    The window is what DeerFlow's two-layer loop detection is for: an unbounded comparison is a
    memory cost that grows with the run, and a guard that costs more the longer it has been running
    eventually becomes the thing that has to be disabled.
    """
    span = max(2, min(int(window), len(history)))
    payload = [json.dumps(item, sort_keys=True, default=str) for item in list(history)[-span:]]
    if len(payload) < 2:
        return 0
    run = 1
    for index in range(len(payload) - 1, 0, -1):
        if payload[index] == payload[index - 1]:
            run += 1
        else:
            break
    return run


__all__ = [
    "HEURISTIC_PARAMS",
    "PIPELINE",
    "PIPELINE_STRATEGIES",
    "PLACEMENTS",
    "STAGE_NAMES",
    "TOGGLEABLE_STAGES",
    "PipelineDecision",
    "PipelineOrderingError",
    "Placement",
    "Stage",
    "StageAction",
    "TurnPipeline",
    "compact_history",
    "declared_invariants",
    "stage_named",
    "validate_order",
]
