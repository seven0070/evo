from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any
import json

from .models import Event, EventType, Goal, OutcomeType, TaskOutcome, TaskStatus
from .storage import SQLiteStore
from .version import __version__


@dataclass
class Experience:
    experience_id: str
    task_id: str
    original_goal: str
    task_type: str
    task_complexity: str
    selected_strategy: str | None
    selected_tools: list[str]
    execution_steps: list[dict[str, Any]]
    observations: list[str]
    failures: list[dict[str, Any]]
    recovery_attempts: list[dict[str, Any]]
    strategy_changes: list[dict[str, Any]]
    verification_result: dict[str, Any]
    final_outcome: OutcomeType
    duration_ms: int | None
    resource_information: dict[str, Any]
    approval_events: list[dict[str, Any]]
    timestamp: str
    agent_version: str
    model_identifier: str
    evaluation_id: str | None = None
    evaluation_result: dict[str, Any] | None = None
    capability_selection: list[dict[str, Any]] = field(default_factory=list)
    environment_id: str = ""
    environment_version: str = ""
    architecture_version: str = ""
    relevant_environment_hash: str = ""
    tool_environment: dict[str, Any] = field(default_factory=dict)
    resource_conditions: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["final_outcome"] = self.final_outcome.value
        return data


class ExperienceEngine:
    """Turns an observable TaskOutcome and its events into one structured experience."""

    def __init__(self, store: SQLiteStore):
        self.store = store

    def create(
        self,
        outcome: TaskOutcome,
        agent_version: str = __version__,
        model_identifier: str = "unknown",
    ) -> Experience:
        events = outcome.events
        task_created = self._first(events, EventType.TASK_CREATED)
        assessment_event = self._first(events, EventType.PLAN_CREATED, key="assessment")
        strategy_event = self._first(events, EventType.STRATEGY_SELECTED)
        verification_events = [event for event in events if event.event_type is EventType.VERIFICATION]
        tool_requested = [event for event in events if event.event_type is EventType.TOOL_REQUESTED]
        tool_completed = [event for event in events if event.event_type is EventType.TOOL_COMPLETED]
        tool_failed = [event for event in events if event.event_type is EventType.TOOL_FAILED]
        observations = [str(event.payload.get("output", "")) for event in tool_completed if event.payload.get("output")]
        observations.extend(str(event.payload.get("error", "")) for event in tool_failed if event.payload.get("error"))
        goal = str(task_created.payload.get("goal", "")) if task_created else ""
        assessment = (assessment_event.payload.get("assessment", {}) if assessment_event else {})
        selected_strategy = strategy_event.payload.get("strategy", {}).get("name") if strategy_event else None
        selected_tools = list(dict.fromkeys(str(event.payload.get("tool", "")) for event in tool_requested if event.payload.get("tool")))
        verification = verification_events[-1].payload if verification_events else {"success": False, "summary": "No verification event recorded"}
        timestamp = task_created.created_at if task_created else datetime.now(timezone.utc).isoformat()
        duration_ms = self._duration_ms(events)
        capability_selection = [event.payload.get("analysis", event.payload) for event in events if event.event_type is EventType.CAPABILITY_SELECTED]
        capability_events = {EventType.CAPABILITY_SELECTED, EventType.CAPABILITY_REQUIRED, EventType.CAPABILITY_GAP_DETECTED, EventType.TOOL_SELECTED, EventType.TOOL_REJECTED, EventType.TOOL_FALLBACK, EventType.TOOL_HEALTH_CHANGED}
        return Experience(
            experience_id=f"exp_{outcome.task_id}",
            task_id=outcome.task_id,
            original_goal=goal,
            task_type=self._task_type(goal, selected_tools),
            task_complexity=str(assessment.get("complexity", "unknown")),
            selected_strategy=selected_strategy,
            selected_tools=selected_tools,
            execution_steps=[event.payload for event in events if event.event_type in {EventType.TOOL_REQUESTED, EventType.TOOL_COMPLETED, EventType.TOOL_FAILED, EventType.VERIFICATION} | capability_events],
            observations=observations,
            failures=[event.payload for event in events if event.event_type in {EventType.TOOL_FAILED, EventType.STRATEGY_FAILED}],
            recovery_attempts=[event.payload for event in events if event.event_type in {EventType.RECOVERY_ATTEMPTED, EventType.RECOVERY}],
            strategy_changes=[event.payload for event in events if event.event_type is EventType.STRATEGY_CHANGED],
            verification_result=verification,
            final_outcome=self._outcome(outcome),
            duration_ms=duration_ms,
            resource_information={"event_count": len(events), "step_count": outcome.steps_completed, "capability_selection_count": len(capability_selection), "capability_gap_count": sum(1 for event in events if event.event_type is EventType.CAPABILITY_GAP_DETECTED)},
            approval_events=[event.payload for event in events if event.event_type in {EventType.APPROVAL_REQUESTED, EventType.APPROVAL_GRANTED, EventType.APPROVAL_DENIED}],
            timestamp=timestamp,
            agent_version=agent_version,
            model_identifier=model_identifier,
            capability_selection=capability_selection,
        )

    def persist(self, experience: Experience) -> None:
        self.store.save_experience(experience)

    def retrieve(
        self,
        goal: str | None = None,
        task_type: str | None = None,
        outcome: OutcomeType | str | None = None,
        strategy: str | None = None,
        tool: str | None = None,
        failure: str | None = None,
        agent_version: str | None = None,
        limit: int = 20,
    ) -> list[Experience]:
        raw_records = self.store.find_experiences(
            goal=goal,
            task_type=task_type,
            outcome=outcome.value if isinstance(outcome, OutcomeType) else outcome,
            strategy=strategy,
            tool=tool,
            failure=failure,
            agent_version=agent_version,
            limit=limit,
        )
        return [self.from_dict(record) for record in raw_records]

    @staticmethod
    def from_dict(data: dict[str, Any]) -> Experience:
        if "payload" in data:
            payload = data["payload"]
        else:
            payload = data.get("experience", data)
        payload = json.loads(payload) if isinstance(payload, str) else dict(payload)
        payload["final_outcome"] = OutcomeType(payload["final_outcome"])
        return Experience(**payload)

    @staticmethod
    def _first(events: list[Event], event_type: EventType, key: str | None = None) -> Event | None:
        for event in events:
            if event.event_type is event_type and (key is None or key in event.payload):
                return event
        return None

    @staticmethod
    def _duration_ms(events: list[Event]) -> int | None:
        if len(events) < 2:
            return None
        try:
            start = datetime.fromisoformat(events[0].created_at)
            end = datetime.fromisoformat(events[-1].created_at)
            return max(0, int((end - start).total_seconds() * 1000))
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _task_type(goal: str, tools: list[str]) -> str:
        text = goal.lower()
        if "research" in text or "analy" in text or "compare" in text:
            return "research_or_analysis"
        if "shell" in text or "command" in text or "run" in text or "execute" in text:
            return "shell_execution"
        if "write" in text or "create" in text or "save" in text:
            return "workspace_change"
        if "list" in text or "files" in text or "read" in text:
            return "workspace_inspection"
        return tools[0] if tools else "general"

    @staticmethod
    def _outcome(outcome: TaskOutcome) -> OutcomeType:
        verified = any(event.event_type is EventType.VERIFICATION and event.payload.get("success") for event in outcome.events)
        if outcome.status is TaskStatus.SUCCEEDED and verified:
            return OutcomeType.SUCCESS
        if outcome.status is TaskStatus.BLOCKED:
            return OutcomeType.BLOCKED
        if outcome.status is TaskStatus.CANCELLED:
            return OutcomeType.ABORTED
        if outcome.error and "timeout" in outcome.error.lower():
            return OutcomeType.TIMEOUT
        if outcome.steps_completed > 0:
            return OutcomeType.PARTIAL_SUCCESS
        return OutcomeType.FAILURE
