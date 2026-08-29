"""Verification: check what can be checked, and refuse what cannot (07 §10 S4, P4).

The defect this module replaces was a default that passed. An expectation the verifier did not
recognise fell through to "the tool returned, so the postcondition holds", which means a plan author
could obtain a green verification by writing a sentence no code evaluates - and every downstream
consumer of that verdict (experience ranking, the promotion gate, metamorphosis eligibility) inherits
the optimism. Silence was not neutral: a check that cannot run reported the same thing as a check that
ran and passed.

So the recognisable expectations are data now (``CHECKS``), matched on the *plan step*, and an
unrecognised one is a **failure with the alternatives named**. Fail-closed is not pessimism here: the
verifier's job is to say "I know" or "I do not know", and the second answer has to be visible in the
ledger or it is the first answer wearing a disguise.

Advisory plugins (``VerifierPlugin``, 07 §4 E3) may add checks and may tighten. They cannot loosen: a
plugin verdict of "pass" never overrides a failing built-in check, because an evolution target that
could attach a permissive verifier would be able to promote anything (which is why the verifier is one
of the protected components).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Sequence

from .models import PlanStep, ToolResult, VerificationResult
from .security import SecurityPolicy


@dataclass(frozen=True)
class CheckSpec:
    """One postcondition this build knows how to evaluate, and how it is spelled in a plan."""

    name: str
    #: Substrings (case-folded) that select this check, in declaration order: the first match wins.
    matches: tuple[str, ...]
    description: str

    def selects(self, expectation: str) -> bool:
        text = expectation.lower()
        return any(marker in text for marker in self.matches)


#: The recognised expectations. Deliberately short: every entry here is a promise that the verifier can
#: keep, and the honest set is small. ``result is non-empty`` stays the default because the planner
#: emits it for every step it has no stronger opinion about (``RuleBasedAdapter.create_plan``).
CHECKS: tuple[CheckSpec, ...] = (
    CheckSpec(
        "valid_json",
        ("valid json", "json object", "json array", "parses as json"),
        "the output parses as JSON",
    ),
    CheckSpec(
        "file_exists",
        ("file exists", "path exists", "was written", "exists on disk"),
        "the step's ``path`` argument names a file inside the workspace",
    ),
    CheckSpec(
        "result_empty",
        ("result is empty", "no output", "returns nothing"),
        "the output is empty after stripping",
    ),
    CheckSpec(
        "result_non_empty",
        ("non-empty", "not empty", "has content", "some output"),
        "the output has content",
    ),
)

#: What a step with no stated expectation is checked for. Not "nothing": a plan step that says nothing
#: still has to have produced something.
DEFAULT_EXPECTATION = "result is non-empty"


def recognised_expectations() -> tuple[str, ...]:
    """The spellings a plan author may use. Quoted in every refusal so the fix is in the message."""
    return tuple(marker for spec in CHECKS for marker in spec.matches)


def select_check(expectation: str) -> CheckSpec | None:
    for spec in CHECKS:
        if spec.selects(expectation):
            return spec
    return None


@dataclass
class Verifier:
    """Evaluates a step's postcondition. Never decides whether the *task* succeeded - that is ``orchestrator``."""

    policy: SecurityPolicy
    plugins: Sequence[Any] = field(default_factory=tuple)

    def verify(self, step: PlanStep, result: ToolResult) -> VerificationResult:
        checks: list[dict[str, object]] = []
        if not result.success:
            checks.append({"name": "tool_success", "passed": False, "detail": result.error or "tool failed"})
            return VerificationResult(False, "Tool execution failed", checks)

        checks.append({"name": "tool_success", "passed": True, "detail": "tool returned success"})
        expectation = str(step.verification or DEFAULT_EXPECTATION).strip()
        spec = select_check(expectation)
        if spec is None:
            checks.append(
                {
                    "name": "check_selection",
                    "passed": False,
                    "detail": (
                        f"'{expectation[:120]}' is not a postcondition this build can evaluate; "
                        "refusing rather than passing an unchecked expectation. Recognised: "
                        + ", ".join(recognised_expectations())
                    ),
                }
            )
            return self._finish(step, result, checks, passed=False, summary="Verification refused: the expectation is not checkable")

        passed, detail = self._evaluate(spec, step, result)
        checks.append({"name": spec.name, "passed": passed, "detail": detail})
        return self._finish(step, result, checks, passed=passed, summary="Verification passed" if passed else "Verification failed")

    # -- the checks themselves ------------------------------------------------
    def _evaluate(self, spec: CheckSpec, step: PlanStep, result: ToolResult) -> tuple[bool, str]:
        if spec.name == "valid_json":
            try:
                json.loads(result.output)
                return True, "output is valid JSON"
            except json.JSONDecodeError as exc:
                return False, f"output is not valid JSON: {exc}"
        if spec.name == "file_exists":
            raw_path = str((step.arguments or {}).get("path", ""))
            if not raw_path:
                return False, "the step names no path, so file_exists cannot be evaluated"
            try:
                path = self.policy.resolve_workspace_path(raw_path)
            except Exception as exc:
                return False, f"'{raw_path}' is not a workspace path: {exc}"
            return path.exists(), f"{path.name} exists" if path.exists() else f"{path.name} does not exist"
        if spec.name == "result_empty":
            return (not result.output.strip()), "result is empty" if not result.output.strip() else "result is not empty"
        present = bool(result.output.strip()) or bool(result.success and result.metadata)
        return present, "tool returned a usable result" if present else "tool returned nothing to check"

    # -- advisory plugins -----------------------------------------------------
    def _finish(self, step: PlanStep, result: ToolResult, checks: list[dict[str, object]], *, passed: bool, summary: str) -> VerificationResult:
        for plugin in self.plugins:
            verdict = self._consult(plugin, step, result, checks)
            if verdict is None:
                continue
            checks.append(verdict)
            if not verdict["passed"]:
                # A plugin may only tighten. The failing verdict is reported, and the built-in result is
                # not allowed to rescue it - the opposite direction would mean a plugin could be chosen
                # for how little it checks.
                return VerificationResult(False, f"advisory check '{verdict['name']}' refused the step", checks)
        return VerificationResult(passed, summary, checks)

    def _consult(self, plugin: Any, step: PlanStep, result: ToolResult, checks: Sequence[dict[str, object]]) -> dict[str, object] | None:
        """Run one plugin. A plugin that raises is a failed check, never a skipped one (R9's mirror)."""
        assess = getattr(plugin, "assess", None)
        if not callable(assess):
            return None
        name = str(getattr(plugin, "name", plugin.__class__.__name__))
        expects = getattr(plugin, "expects", None)
        payload = {
            "step": dict(getattr(step, "__dict__", {}) or {}),
            "result": dict(getattr(result, "__dict__", {}) or {}),
        }
        if callable(expects):
            try:
                if not tuple(expects(payload) or ()):
                    return None
            except Exception as exc:
                return {"name": name, "passed": False, "detail": f"plugin '{name}' failed to declare expectations: {type(exc).__name__}: {exc}"}
        try:
            verdict = dict(assess(payload, result, ()) or {})
        except Exception as exc:
            return {"name": name, "passed": False, "detail": f"plugin '{name}' raised {type(exc).__name__}: {exc}"}
        return {
            "name": name,
            "passed": bool(verdict.get("passed", False)),
            "detail": str(verdict.get("detail", "no detail supplied")),
        }
