"""Characterisation tests for the audit's known defects - each one is a promise, not a wish.

This file began as an xfail ledger: one ``xfail(strict=True)`` marker per verified defect, so
the run failed if a defect disappeared without the marker being removed. It is the
implementation schedule in its most honest form - it can only shrink, and a phase that "fixed"
something is forced to prove it by deleting the marker in review. Every marker has now been
deleted that way, and the positives that replaced them are kept here rather than moved, so the
ledger reads as a schedule that was paid off instead of a file that quietly emptied.

Repaired and removed from this ledger, each with a positive test replacing the marker:
kernel memory-at-plan-time, kernel architecture attribution, and the public active-version
accessor (``tests/test_dead_links_closed.py``), unsandboxed runtime tool execution
(``tests/test_sandbox_providers.py``), and - in P4 - the verifier's default-open on an
unrecognised expectation, which is now the positive assertion below
(``evo_agent/verifier.py``, ``docs/evolution/07`` §10 S4).

The isolation entry was not simply deleted, because the specification's rule is not what that
entry asserted. It claimed ``python3 evil.py`` must be *denied*; S2 says an interpreter running a
file inside the task's own write-set is allowed but **confined** - the boundary is the namespace,
and an allowlist that tries to be the boundary is what produced the original defect (a
15-word blocklist beside an unrestricted ``shell=True``). The positive test therefore asserts
confinement and the write-set, not a denial that the design does not promise.
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
from evo_agent.kernel import AgentKernel  # noqa: E402
from evo_agent.model_adapter import RuleBasedAdapter  # noqa: E402
from evo_agent.models import PlanStep, ToolResult, VerificationResult  # noqa: E402
from evo_agent.promotion import PromotionEngine  # noqa: E402
from evo_agent.security import SecurityPolicy  # noqa: E402
from evo_agent.verifier import Verifier  # noqa: E402

def test_verifier_refuses_an_expectation_it_cannot_check(tmp_path: Path):
    """B.5/S4, repaired in P4: an unrecognised expectation fails closed.

    Kept as the same assertion the xfail made, so the ledger shows the promise being met rather than a
    new test written around the new behaviour.
    """
    verifier = Verifier(SecurityPolicy(tmp_path))
    step = PlanStep(step_id="s1", description="write a report", tool_name="workspace_write", verification="report cites at least three sources")
    result = ToolResult(call_id="c1", tool_name="workspace_write", success=True, output="anything at all")
    verdict: VerificationResult = verifier.verify(step, result)
    assert not verdict.success, "an unverifiable expectation must fail closed, not default to pass"
    assert verdict.checks, "the refusal has to be legible in the record, not only in the verdict"


def test_a_failed_tool_is_still_reported_as_a_failed_tool(tmp_path: Path):
    """The fail-closed change must not swallow the earlier, stronger fact."""
    verifier = Verifier(SecurityPolicy(tmp_path))
    step = PlanStep(step_id="s1", description="write", tool_name="workspace_write", verification="nonsense expectation")
    verdict = verifier.verify(step, ToolResult(call_id="c1", tool_name="workspace_write", success=False, error="denied by policy"))
    assert not verdict.success
    assert verdict.summary == "Tool execution failed"
    assert verdict.checks[0]["name"] == "tool_success"
