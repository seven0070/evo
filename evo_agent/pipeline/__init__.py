"""The turn pipeline: what a turn goes through, in what order, and why (07 §4, P4).

Two upstreams converged on the same shape independently. DeerFlow wraps tool execution in an
ordered middleware chain; DeepSeek Harness drives queued turns through fixed phases. Both arrived
at the same conclusion for the same reason: when the *order* of sanitization, budgeting, guarding,
and receipt-stamping is implicit, the system's security properties depend on which decorator
happens to be attached first. An order that is only expressed by code layout cannot be reviewed,
cannot be benchmarked, and - the failure this package exists to prevent - cannot be *refused* when
a candidate tries to change it.

So the order is declared here, once, as data:

* every stage names its **placement** - what the model is told exists, what the model actually
  receives, what the tool sees, and what only the runtime sees;
* every stage carries a **reason**, and the reason is asserted by a test rather than trusted;
* the hard ordering rules (sanitization outside everything, receipts outermost on the tool edge,
  dispatch inside the policy filter) are *validated*, so a reordered chain is a refusal and not a
  silent weakening.

The pipeline never dispatches a tool. ``DISPATCH`` is a declared stage that marks where the loop's
one tool dispatch sits in the order; the loop itself stays in :mod:`evo_agent.kernel`, and
``I-single-loop`` fails the build if this package grows a ``while``. A pipeline that could execute
would be a second agent loop with better documentation, which is worse than no pipeline at all.
"""

from __future__ import annotations

from .engine import (
    HEURISTIC_PARAMS,
    PIPELINE,
    PIPELINE_STRATEGIES,
    PLACEMENTS,
    STAGE_NAMES,
    TOGGLEABLE_STAGES,
    PipelineDecision,
    PipelineOrderingError,
    Placement,
    Stage,
    StageAction,
    TurnPipeline,
    declared_invariants,
    stage_named,
    validate_order,
)

__all__ = [
    "HEURISTIC_PARAMS",
    "PIPELINE",
    "PIPELINE_STRATEGIES",
    "PLACEMENTS",
    "STAGE_NAMES",
    "PipelineDecision",
    "Placement",
    "PipelineOrderingError",
    "Stage",
    "StageAction",
    "TurnPipeline",
    "declared_invariants",
    "stage_named",
    "validate_order",
]
