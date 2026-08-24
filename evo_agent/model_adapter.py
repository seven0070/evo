from __future__ import annotations

from abc import ABC, abstractmethod
import json
from typing import Any

from .models import Goal, Plan, PlanStep, RiskLevel, ToolResult, new_id


class ModelAdapter(ABC):
    """Stable kernel-facing interface; provider implementations stay behind it."""

    @abstractmethod
    def create_plan(self, goal: Goal, tool_schemas: list[dict[str, Any]], context: str = "") -> Plan:
        raise NotImplementedError

    @abstractmethod
    def choose_recovery(self, goal: Goal, failed_step: PlanStep, result: ToolResult) -> str:
        raise NotImplementedError


class RuleBasedAdapter(ModelAdapter):
    """Offline adapter used for deterministic tests and development without an API key."""

    def create_plan(self, goal: Goal, tool_schemas: list[dict[str, Any]], context: str = "") -> Plan:
        text = goal.text.lower()
        steps: list[PlanStep] = []
        if "list" in text or "files" in text:
            steps.append(PlanStep(new_id("step"), "Inspect the workspace contents", "workspace_list", {"path": "."}, RiskLevel.LOW, "result is valid JSON"))
        elif "read" in text and "file" in text:
            steps.append(PlanStep(new_id("step"), "Read the requested workspace file", "workspace_read", {"path": "README.md"}, RiskLevel.LOW, "result is non-empty"))
        else:
            steps.append(PlanStep(new_id("step"), "Record the goal for review", "workspace_write", {"path": "agent_goal.txt", "content": goal.text + "\n"}, RiskLevel.MEDIUM, "file exists"))
        return Plan(goal.task_id, steps, "Offline rule-based plan generated for safe local execution")

    def choose_recovery(self, goal: Goal, failed_step: PlanStep, result: ToolResult) -> str:
        return "Stop and report the failure; no automatic recovery strategy is available in offline mode."


class OpenAICompatibleAdapter(ModelAdapter):
    """Adapter for OpenAI-compatible providers, including hosted or local gateways."""

    def __init__(self, model: str, base_url: str | None = None, api_key: str | None = None):
        self.model = model
        try:
            from openai import OpenAI
        except ImportError as exc:
            raise RuntimeError("Install the optional 'llm' dependency to use this adapter") from exc
        kwargs: dict[str, Any] = {}
        if base_url:
            kwargs["base_url"] = base_url
        if api_key:
            kwargs["api_key"] = api_key
        self.client = OpenAI(**kwargs)

    def create_plan(self, goal: Goal, tool_schemas: list[dict[str, Any]], context: str = "") -> Plan:
        system = (
            "You are the planning component of a permissioned local agent. "
            "Return JSON only with keys rationale and steps. Each step must contain "
            "description, tool_name, arguments, risk, and verification. Use only the supplied tools. "
            "Never request tools outside the workspace."
        )
        user = json.dumps({"goal": goal.text, "context": context, "tools": tool_schemas})
        response = self.client.chat.completions.create(
            model=self.model,
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
            response_format={"type": "json_object"},
        )
        payload = json.loads(response.choices[0].message.content)
        steps = [
            PlanStep(
                step_id=new_id("step"),
                description=item["description"],
                tool_name=item.get("tool_name"),
                arguments=item.get("arguments", {}),
                risk=RiskLevel(item.get("risk", "low")),
                verification=item.get("verification"),
            )
            for item in payload.get("steps", [])
        ]
        return Plan(goal.task_id, steps, payload.get("rationale", ""))

    def choose_recovery(self, goal: Goal, failed_step: PlanStep, result: ToolResult) -> str:
        return f"The step failed with {result.error or 'an unknown error'}. Re-plan from the failure without repeating unsafe actions."
