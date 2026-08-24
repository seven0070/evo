from __future__ import annotations

import json
from pathlib import Path

from .models import PlanStep, ToolResult, VerificationResult
from .security import SecurityPolicy


class Verifier:
    def __init__(self, policy: SecurityPolicy):
        self.policy = policy

    def verify(self, step: PlanStep, result: ToolResult) -> VerificationResult:
        checks: list[dict[str, object]] = []
        if not result.success:
            checks.append({"name": "tool_success", "passed": False, "detail": result.error or "tool failed"})
            return VerificationResult(False, "Tool execution failed", checks)

        checks.append({"name": "tool_success", "passed": True, "detail": "tool returned success"})
        expectation = (step.verification or "result is non-empty").lower()
        if "valid json" in expectation:
            try:
                json.loads(result.output)
                passed, detail = True, "output is valid JSON"
            except json.JSONDecodeError as exc:
                passed, detail = False, f"output is not valid JSON: {exc}"
        elif "file exists" in expectation:
            raw_path = step.arguments.get("path", "")
            path = self.policy.resolve_workspace_path(str(raw_path))
            passed, detail = path.exists(), f"{path.name} exists" if path.exists() else f"{path.name} does not exist"
        elif "result is empty" in expectation:
            passed, detail = not result.output.strip(), "result is empty"
        else:
            passed, detail = bool(result.output.strip() or result.success), "tool returned a usable result"
        checks.append({"name": "postcondition", "passed": passed, "detail": detail})
        return VerificationResult(passed, "Verification passed" if passed else "Verification failed", checks)
