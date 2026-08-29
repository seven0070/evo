from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
import hashlib
import json
import re
import time
from pathlib import Path
from typing import Any, Callable, Iterable

from .experience import ExperienceEngine
from .capability import CapabilityAnalysis, CapabilityAvailability, CapabilityIntelligence
from .flexibility import FlexibilityContext
from .kernel import AgentKernel
from .memory import CognitiveMemoryContext, MemoryManager, MemoryType, RetrievalQuery
from .world import WorldModelEngine, EnvironmentObserver, WorldRefreshEngine, PlanValidationStatus
from .models import Event, EventType, Goal, OutcomeType, PlanStep, RiskLevel, TaskOutcome, TaskStatus, ToolResult, new_id, utc_now
from .orchestrator import EvolutionOpportunity, EvolutionOrchestrator, OrchestrationPath
from .storage import SQLiteStore
from .version import __version__


class RequirementKind(str, Enum):
    EXPLICIT = "explicit"
    INFERRED = "inferred"
    UNKNOWN = "unknown"


class AmbiguityStatus(str, Enum):
    CLEAR = "clear"
    AMBIGUOUS = "ambiguous"
    CRITICAL = "critical"


class CognitiveState(str, Enum):
    INITIALIZING = "initializing"
    UNDERSTANDING = "understanding"
    PLANNING = "planning"
    EXECUTING = "executing"
    OBSERVING = "observing"
    VERIFYING = "verifying"
    REPLANNING = "replanning"
    WAITING_FOR_INPUT = "waiting_for_input"
    WAITING_FOR_APPROVAL = "waiting_for_approval"
    COMPLETED = "completed"
    FAILED = "failed"
    BLOCKED = "blocked"


class SubtaskStatus(str, Enum):
    PENDING = "pending"
    READY = "ready"
    EXECUTING = "executing"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    BLOCKED = "blocked"
    SKIPPED = "skipped"


class TaskGraphType(str, Enum):
    SEQUENTIAL = "sequential"
    DEPENDENT = "dependent"
    CONDITIONAL = "conditional"
    PARALLEL_SAFE = "parallel_safe"
    BLOCKED = "blocked"


class CapabilityAssessment(str, Enum):
    AVAILABLE = "capability_available"
    UNAVAILABLE = "capability_unavailable"
    INCOMPATIBLE = "capability_incompatible"
    RESTRICTED = "capability_restricted"
    UNKNOWN = "capability_unknown"


class FailureKind(str, Enum):
    TOOL_FAILURE = "tool_failure"
    INPUT_FAILURE = "input_failure"
    PERMISSION_FAILURE = "permission_failure"
    ENVIRONMENT_FAILURE = "environment_failure"
    PLANNING_FAILURE = "planning_failure"
    STRATEGY_FAILURE = "strategy_failure"
    CAPABILITY_GAP = "capability_gap"
    VERIFICATION_FAILURE = "verification_failure"
    UNKNOWN_FAILURE = "unknown_failure"


class ConfidenceLevel(str, Enum):
    CONFIDENT = "confident"
    PROBABLE = "probable"
    UNCERTAIN = "uncertain"


class CognitiveOutcome(str, Enum):
    SUCCESS = "success"
    PARTIAL = "partial"
    FAILED = "failed"
    INCONCLUSIVE = "inconclusive"
    BLOCKED = "blocked"
    WAITING_FOR_INPUT = "waiting_for_input"
    WAITING_FOR_APPROVAL = "waiting_for_approval"


@dataclass
class Requirement:
    text: str
    kind: RequirementKind
    critical: bool = False

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["kind"] = self.kind.value
        return data


@dataclass
class SuccessCriterion:
    criterion_id: str
    description: str
    check_type: str
    target: str | None = None
    required: bool = True

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class CognitiveGoal:
    goal_id: str
    original_text: str
    normalized_goal: str
    objective: str
    constraints: list[Requirement]
    resources: list[str]
    expected_outputs: list[str]
    success_criteria: list[SuccessCriterion]
    risks: list[str]
    ambiguity: AmbiguityStatus
    confidence: float
    status: CognitiveState = CognitiveState.INITIALIZING
    created_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)
    missing_requirements: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["constraints"] = [item.to_dict() for item in self.constraints]
        data["success_criteria"] = [item.to_dict() for item in self.success_criteria]
        data["ambiguity"] = self.ambiguity.value
        data["status"] = self.status.value
        return data


@dataclass
class IntentModel:
    goal_id: str
    primary_objective: str
    secondary_objectives: list[str]
    constraints: list[str]
    preferences: list[str]
    required_outputs: list[str]
    success_definition: list[str]
    risk_level: RiskLevel
    time_constraints: list[str]
    resource_constraints: list[str]

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["risk_level"] = self.risk_level.value
        return data


@dataclass
class CognitiveTask:
    task_id: str
    goal_id: str
    parent_task_id: str | None
    description: str
    dependencies: list[str]
    inputs: list[str]
    expected_outputs: list[str]
    success_criteria: list[str]
    required_capabilities: list[str]
    risk: RiskLevel = RiskLevel.LOW
    status: SubtaskStatus = SubtaskStatus.PENDING
    tool_name: str | None = None
    attempt_count: int = 0
    result_task_id: str | None = None
    error: str | None = None
    created_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["risk"] = self.risk.value
        data["status"] = self.status.value
        return data


@dataclass
class TaskGraph:
    graph_id: str
    goal_id: str
    graph_type: TaskGraphType
    nodes: list[CognitiveTask]
    edges: list[tuple[str, str]]
    created_at: str = field(default_factory=utc_now)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["graph_type"] = self.graph_type.value
        data["nodes"] = [node.to_dict() for node in self.nodes]
        data["edges"] = [list(edge) for edge in self.edges]
        return data

    def node(self, task_id: str) -> CognitiveTask | None:
        return next((item for item in self.nodes if item.task_id == task_id), None)

    def ready_nodes(self) -> list[CognitiveTask]:
        ready: list[CognitiveTask] = []
        for node in self.nodes:
            if node.status is not SubtaskStatus.PENDING:
                continue
            dependencies = [self.node(dep) for dep in node.dependencies]
            if all(item and item.status is SubtaskStatus.SUCCEEDED for item in dependencies):
                node.status = SubtaskStatus.READY
                ready.append(node)
        return ready

    def has_failures(self) -> bool:
        return any(node.status in {SubtaskStatus.FAILED, SubtaskStatus.BLOCKED} for node in self.nodes)


@dataclass
class CognitivePlan:
    plan_id: str
    goal_id: str
    steps: list[str]
    dependencies: dict[str, list[str]]
    required_tools: list[str]
    required_capabilities: list[str]
    estimated_cost: float
    estimated_risk: RiskLevel
    expected_result: str
    rationale: str
    plan_version: str = "cognitive-plan-v1"
    agent_version: str = __version__
    architecture_version: str = ""
    selected: bool = False
    created_at: str = field(default_factory=utc_now)
    memory_evidence_ids: list[str] = field(default_factory=list)
    memory_warnings: list[str] = field(default_factory=list)
    capability_requirements: list[dict[str, Any]] = field(default_factory=list)
    capability_selection: list[dict[str, Any]] = field(default_factory=list)
    environment_id: str = ""
    environment_version: str = ""
    environment_hash: str = ""
    environment_context: dict[str, Any] = field(default_factory=dict)
    environment_observation_ids: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["estimated_risk"] = self.estimated_risk.value
        return data


@dataclass
class Observation:
    observation_id: str
    goal_id: str
    task_id: str
    tool: str | None
    output: str
    status: str
    errors: list[str]
    artifacts: list[str]
    duration: float
    side_effects: list[str]
    verification_hints: list[str]
    created_at: str = field(default_factory=utc_now)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class FailureDiagnosis:
    task_id: str
    kind: FailureKind
    confidence: ConfidenceLevel
    reason: str
    retryable: bool
    requires_input: bool = False
    requires_approval: bool = False

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["kind"] = self.kind.value
        data["confidence"] = self.confidence.value
        return data


@dataclass
class VerificationReport:
    goal_id: str
    outcome: CognitiveOutcome
    success: bool
    summary: str
    checks: list[dict[str, Any]]
    completed_count: int
    failed_count: int
    required_count: int

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["outcome"] = self.outcome.value
        return data


@dataclass
class CognitiveStateRecord:
    goal_id: str
    state: CognitiveState
    current_task_id: str | None = None
    replan_count: int = 0
    tool_call_count: int = 0
    last_error: str | None = None
    created_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["state"] = self.state.value
        return data


@dataclass
class CapabilityGap:
    goal_id: str
    task_id: str
    capability: str
    assessment: CapabilityAssessment
    reason: str
    structural: bool = False
    opportunity_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["assessment"] = self.assessment.value
        return data


@dataclass
class CognitiveResult:
    goal: CognitiveGoal
    intent: IntentModel
    plan: CognitivePlan | None
    graph: TaskGraph | None
    state: CognitiveStateRecord
    outcome: CognitiveOutcome
    summary: str
    verification: VerificationReport | None = None
    observations: list[Observation] = field(default_factory=list)
    failures: list[FailureDiagnosis] = field(default_factory=list)
    capability_gaps: list[CapabilityGap] = field(default_factory=list)
    replans: int = 0
    experience_id: str | None = None
    evaluation_id: str | None = None
    memory_context: CognitiveMemoryContext | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "goal": self.goal.to_dict(),
            "intent": self.intent.to_dict(),
            "plan": self.plan.to_dict() if self.plan else None,
            "graph": self.graph.to_dict() if self.graph else None,
            "state": self.state.to_dict(),
            "outcome": self.outcome.value,
            "summary": self.summary,
            "verification": self.verification.to_dict() if self.verification else None,
            "observations": [item.to_dict() for item in self.observations],
            "failures": [item.to_dict() for item in self.failures],
            "capability_gaps": [item.to_dict() for item in self.capability_gaps],
            "replans": self.replans,
            "experience_id": self.experience_id,
            "evaluation_id": self.evaluation_id,
            "memory_context": self.memory_context.to_dict() if self.memory_context else None,
        }


class GoalUnderstandingEngine:
    """Deterministic, fail-closed natural-language goal normalization."""

    CRITICAL_AMBIGUOUS = ("build me an app", "make an app", "do something", "fix it", "handle this", "make it better")
    RISK_TERMS = ("delete", "remove", "deploy", "publish", "send", "credential", "financial", "external")

    def parse(self, text: str, goal_id: str | None = None) -> CognitiveGoal:
        return self.understand(text, goal_id)

    def understand(self, text: str, goal_id: str | None = None) -> CognitiveGoal:
        original = text.strip()
        normalized = re.sub(r"\s+", " ", original).strip().lower()
        goal_id = goal_id or new_id("goal")
        constraints: list[Requirement] = []
        for match in re.findall(r"(?:must|without|within|inside|only|do not|don't)\s+[^,.]+", normalized):
            constraints.append(Requirement(match.strip(), RequirementKind.EXPLICIT, any(term in match for term in ("without", "do not", "don't", "only"))))
        if "workspace" in normalized and not any("workspace" in item.text for item in constraints):
            constraints.append(Requirement("operate inside the configured workspace", RequirementKind.INFERRED, True))
        missing: list[str] = []
        ambiguity = AmbiguityStatus.CLEAR
        if not normalized or len(normalized.split()) < 2:
            ambiguity = AmbiguityStatus.CRITICAL
            missing.append("a concrete objective and expected output")
        elif any(normalized == phrase or (normalized.startswith(phrase) and len(normalized.split()) <= 5) for phrase in self.CRITICAL_AMBIGUOUS):
            ambiguity = AmbiguityStatus.CRITICAL
            missing.extend(["application type or target", "required features", "expected output"])
        elif re.search(r"\b(?:something|it|that)\b", normalized) and len(normalized.split()) < 8:
            ambiguity = AmbiguityStatus.AMBIGUOUS
            missing.append("the specific target or expected output")
        outputs: list[str] = []
        for token in ("report", "summary", "file", "files", "output", "result", "app"):
            if token in normalized and token not in outputs:
                outputs.append(token)
        risks = [f"goal contains {term} operation" for term in self.RISK_TERMS if term in normalized]
        constraints.extend(Requirement(item, RequirementKind.UNKNOWN, True) for item in missing)
        confidence = 0.95 if ambiguity is AmbiguityStatus.CLEAR else 0.35 if ambiguity is AmbiguityStatus.AMBIGUOUS else 0.1
        criteria = SuccessCriteriaEngine().generate(normalized, outputs, ambiguity)
        return CognitiveGoal(goal_id, original, normalized, normalized, constraints, [], outputs, criteria, risks, ambiguity, confidence, CognitiveState.UNDERSTANDING, missing_requirements=missing)


class IntentEngine:
    def build(self, goal: CognitiveGoal) -> IntentModel:
        parts = [item.strip() for item in re.split(r"\bthen\b|\band\b|\bafter\b", goal.normalized_goal) if item.strip()]
        primary = parts[0] if parts else goal.objective
        secondary = parts[1:]
        preferences = [item.text for item in goal.constraints if item.kind is RequirementKind.INFERRED]
        explicit = [item.text for item in goal.constraints if item.kind is RequirementKind.EXPLICIT]
        risk = RiskLevel.CRITICAL if any("critical" in item for item in goal.risks) else RiskLevel.HIGH if goal.risks else RiskLevel.LOW
        return IntentModel(goal.goal_id, primary, secondary, explicit, preferences, goal.expected_outputs, [item.description for item in goal.success_criteria], risk, [], goal.resources)


class SuccessCriteriaEngine:
    def generate(self, normalized: str, outputs: list[str] | None = None, ambiguity: AmbiguityStatus = AmbiguityStatus.CLEAR) -> list[SuccessCriterion]:
        outputs = outputs or []
        criteria: list[SuccessCriterion] = []
        if ambiguity is not AmbiguityStatus.CLEAR:
            return [SuccessCriterion(new_id("criterion"), "All critical missing requirements are clarified", "clarification", required=True)]
        if any(token in normalized for token in ("all", "every", "each")) and "file" in normalized:
            criteria.extend([
                SuccessCriterion(new_id("criterion"), "Every eligible input file is discovered", "observation", "workspace_list"),
                SuccessCriterion(new_id("criterion"), "Every discovered input is handled", "completeness", "all_inputs"),
                SuccessCriterion(new_id("criterion"), "No input file is modified unintentionally", "immutability", "inputs"),
            ])
        if any(token in normalized for token in ("report", "summary", "output", "save", "create", "write")):
            criteria.append(SuccessCriterion(new_id("criterion"), "The requested output exists and is readable", "artifact", outputs[0] if outputs else "workspace_output"))
        criteria.append(SuccessCriterion(new_id("criterion"), "Every executed subtask passes its verification", "subtasks", "all_subtasks"))
        if not criteria:
            criteria.append(SuccessCriterion(new_id("criterion"), "The stated objective is satisfied", "goal", None))
        return criteria


class TaskDecompositionEngine:
    """Creates bounded, dependency-aware subtasks using explicit deterministic patterns."""

    def __init__(self, max_subtasks: int = 12):
        self.max_subtasks = max_subtasks

    def create_graph(self, goal: CognitiveGoal, intent: IntentModel) -> TaskGraph:
        return self.decompose(goal, intent)

    def decompose(self, goal: CognitiveGoal, intent: IntentModel) -> TaskGraph:
        text = goal.normalized_goal
        specs: list[tuple[str, str, list[str], list[str], RiskLevel, str | None]] = []
        if "csv" in text:
            specs = [
                ("Discover eligible CSV files", "discover CSV inputs", ["filesystem"], ["workspace_list"], RiskLevel.LOW, "workspace_list"),
                ("Validate CSV inputs", "validate discovered CSV inputs", ["filesystem"], ["workspace_read"], RiskLevel.LOW, "workspace_read"),
                ("Process CSV files", "process each eligible CSV file", ["filesystem"], ["workspace_read"], RiskLevel.LOW, "workspace_read"),
                ("Generate summaries", "create a summary output for each processed CSV", ["filesystem"], ["workspace_write"], RiskLevel.MEDIUM, "workspace_write"),
                ("Validate outputs", "validate that generated summaries are readable", ["filesystem"], ["workspace_read"], RiskLevel.LOW, "workspace_read"),
            ]
        elif "file" in text and any(token in text for token in ("every", "all", "list")) and any(token in text for token in ("report", "count", "line")):
            specs = [
                ("Discover text files", "List every eligible text file in the workspace", ["filesystem"], ["workspace_list"], RiskLevel.LOW, "workspace_list"),
                ("Count file lines", "Read discovered text files and count their lines", ["filesystem"], ["workspace_read"], RiskLevel.LOW, "workspace_read"),
                ("Generate workspace report", "Write a report containing the discovered files and line counts", ["filesystem"], ["workspace_write"], RiskLevel.MEDIUM, "workspace_write"),
                ("Verify report completeness", "Read the report and verify every discovered file is represented", ["filesystem"], ["workspace_read"], RiskLevel.LOW, "workspace_read"),
            ]
        else:
            clauses = [item.strip() for item in re.split(r"\bthen\b|\bafter that\b", text) if item.strip()]
            if not clauses:
                clauses = [text]
            for clause in clauses[: self.max_subtasks]:
                capabilities = self._capabilities_for(clause)
                tool = self._tool_for(clause)
                risk = RiskLevel.HIGH if any(token in clause for token in ("run", "execute", "shell", "command")) else RiskLevel.MEDIUM if any(token in clause for token in ("write", "create", "save")) else RiskLevel.LOW
                specs.append((clause[:80], clause, capabilities, [tool] if tool else [], risk, tool))
        specs = specs[: self.max_subtasks]
        nodes: list[CognitiveTask] = []
        previous: str | None = None
        for title, description, capabilities, tools, risk, tool in specs:
            task_id = new_id("subtask")
            node = CognitiveTask(task_id, goal.goal_id, None, description, [previous] if previous else [], [], [], ["kernel outcome succeeds", "step verification passes"], capabilities, risk, tool_name=tool)
            nodes.append(node)
            previous = task_id
        edges = [(node.dependencies[0], node.task_id) for node in nodes if node.dependencies]
        graph_type = TaskGraphType.SEQUENTIAL if len(nodes) > 1 else TaskGraphType.DEPENDENT
        return TaskGraph(new_id("graph"), goal.goal_id, graph_type, nodes, edges)

    @staticmethod
    def _capabilities_for(text: str) -> list[str]:
        if any(token in text for token in ("web", "internet", "research", "url", "http")):
            return ["web_research"]
        if any(token in text for token in ("image", "video", "audio", "model", "multimedia", "quantum", "specialist")):
            return ["multimedia_generation"]
        if any(token in text for token in ("run", "execute", "shell", "command", "test")):
            return ["shell"]
        if any(token in text for token in ("read", "list", "file", "write", "create", "save", "report")):
            return ["filesystem"]
        return ["planning"]

    @staticmethod
    def _tool_for(text: str) -> str | None:
        if "list" in text or "files" in text:
            return "workspace_list"
        if "read" in text:
            return "workspace_read"
        if any(token in text for token in ("write", "create", "save", "report")):
            return "workspace_write"
        if any(token in text for token in ("run", "execute", "shell", "command", "test")):
            return "shell"
        return None


class TaskGraphEngine:
    def validate(self, graph: TaskGraph) -> list[str]:
        errors: list[str] = []
        ids = {node.task_id for node in graph.nodes}
        if len(ids) != len(graph.nodes):
            errors.append("task graph contains duplicate task IDs")
        for node in graph.nodes:
            if any(dep not in ids for dep in node.dependencies):
                errors.append(f"task {node.task_id} has an unknown dependency")
            if node.task_id in node.dependencies:
                errors.append(f"task {node.task_id} depends on itself")
        if self._has_cycle(graph):
            errors.append("task graph contains a dependency cycle")
        return errors

    def order(self, graph: TaskGraph) -> list[CognitiveTask]:
        if self.validate(graph):
            raise ValueError("invalid task graph")
        result: list[CognitiveTask] = []
        remaining = {node.task_id: node for node in graph.nodes}
        while remaining:
            ready = [node for node in remaining.values() if all(dep in {item.task_id for item in result} for dep in node.dependencies)]
            if not ready:
                raise ValueError("task graph cannot be ordered")
            result.extend(sorted(ready, key=lambda item: item.task_id))
            for node in ready:
                remaining.pop(node.task_id)
        return result

    @staticmethod
    def _has_cycle(graph: TaskGraph) -> bool:
        visiting: set[str] = set()
        visited: set[str] = set()
        children: dict[str, list[str]] = {}
        for source, target in graph.edges:
            children.setdefault(source, []).append(target)
        def visit(node_id: str) -> bool:
            if node_id in visiting:
                return True
            if node_id in visited:
                return False
            visiting.add(node_id)
            if any(visit(child) for child in children.get(node_id, [])):
                return True
            visiting.remove(node_id)
            visited.add(node_id)
            return False
        return any(visit(node.task_id) for node in graph.nodes)


class PlanningEngine:
    def __init__(self, max_plan_candidates: int = 3):
        self.max_plan_candidates = max_plan_candidates

    def generate_plans(self, goal: CognitiveGoal, graph: TaskGraph, architecture_version: str = "") -> list[CognitivePlan]:
        return self.generate(goal, graph, architecture_version)

    def generate(self, goal: CognitiveGoal, graph: TaskGraph, architecture_version: str = "", memory_context: CognitiveMemoryContext | None = None) -> list[CognitivePlan]:
        ordered = TaskGraphEngine().order(graph)
        evidence_ids = [item.memory.memory_id for item in (memory_context.relevant_episodic_memory + memory_context.relevant_semantic_memory)] if memory_context else []
        warnings = memory_context.memory_warnings if memory_context else []
        procedures = len(memory_context.relevant_procedures) if memory_context else 0
        rationale = "Dependency-aware sequential plan preserves verification after each Kernel task."
        if evidence_ids:
            rationale += f" Considered {len(evidence_ids)} bounded historical memory record(s)."
        if procedures:
            rationale += f" Validated {procedures} candidate procedural memory record(s) against current context."
        if warnings:
            rationale += " Historical memory warnings were retained as evidence, not instructions."
        primary = CognitivePlan(new_id("plan"), goal.goal_id, [node.task_id for node in ordered], {node.task_id: node.dependencies for node in ordered}, sorted({node.tool_name for node in ordered if node.tool_name}), sorted({cap for node in ordered for cap in node.required_capabilities}), float(len(ordered)), max((node.risk for node in ordered), key=lambda risk: [RiskLevel.LOW, RiskLevel.MEDIUM, RiskLevel.HIGH, RiskLevel.CRITICAL].index(risk)), "All subtasks complete and goal criteria pass", rationale, architecture_version=architecture_version, memory_evidence_ids=evidence_ids, memory_warnings=warnings)
        candidates = [primary]
        if len(ordered) > 1:
            candidates.append(CognitivePlan(new_id("plan"), goal.goal_id, list(reversed([node.task_id for node in ordered])), {node.task_id: node.dependencies for node in ordered}, primary.required_tools, primary.required_capabilities, primary.estimated_cost + 1, primary.estimated_risk, primary.expected_result, "Alternative ordering retained only for comparison; dependency validation must pass.", architecture_version=architecture_version))
        return candidates[: self.max_plan_candidates]


class ReasoningEngine:
    def __init__(self, max_reasoning_iterations: int = 4):
        self.max_reasoning_iterations = max_reasoning_iterations

    def select_plan(self, plans: list[CognitivePlan], available_tools: Iterable[str] | None = None) -> CognitivePlan:
        if not plans:
            raise ValueError("no candidate plans")
        available = set(available_tools or [])
        scored: list[tuple[tuple[float, float, float], CognitivePlan]] = []
        for plan in plans[: self.max_reasoning_iterations]:
            unavailable = len(set(plan.required_tools) - available) if available else 0
            risk = [RiskLevel.LOW, RiskLevel.MEDIUM, RiskLevel.HIGH, RiskLevel.CRITICAL].index(plan.estimated_risk)
            scored.append(((float(unavailable), float(risk), plan.estimated_cost), plan))
        selected = sorted(scored, key=lambda item: item[0])[0][1]
        selected.selected = True
        return selected


class CapabilityGapDetector:
    def __init__(self, metamorphosis: Any | None = None):
        self.metamorphosis = metamorphosis

    def assess(self, goal_id: str, task: CognitiveTask, available: Iterable[str] | None = None) -> CapabilityGap | None:
        return self.check(goal_id, task, available)

    def check(self, goal_id: str, task: CognitiveTask, available: Iterable[str] | None = None) -> CapabilityGap | None:
        available_names = set(available or [])
        capability = task.required_capabilities[0] if task.required_capabilities else "planning"
        aliases = {"filesystem": {"filesystem"}, "shell": {"shell", "permissioned_shell"}, "web_research": {"web_research"}, "multimedia_generation": {"multimedia_generation", "image_generation"}, "planning": {"planning"}}
        matches = aliases.get(capability, {capability})
        description = task.description.lower()
        if matches & available_names and "restricted" not in description and "incompatible" not in description:
            return None
        structural = any(term in description for term in ("structural", "architecture", "component", "capability composition"))
        if "restricted" in description:
            assessment = CapabilityAssessment.RESTRICTED
            reason = f"Capability '{capability}' is present but restricted by the current execution policy."
        elif "incompatible" in description:
            assessment = CapabilityAssessment.INCOMPATIBLE
            reason = f"Capability '{capability}' is present but incompatible with the current task context."
        elif capability not in aliases:
            assessment = CapabilityAssessment.UNKNOWN
            reason = f"Capability '{capability}' is not recognized by the active registry."
        else:
            assessment = CapabilityAssessment.UNAVAILABLE
            reason = f"Required capability '{capability}' is not present in the active capability registry."
        return CapabilityGap(goal_id, task.task_id, capability, assessment, reason, structural)


class ContextManager:
    def __init__(self, max_context_size: int = 12000):
        self.max_context_size = max_context_size
        self.current_goal: CognitiveGoal | None = None
        self.current_intent: IntentModel | None = None
        self.current_plan: CognitivePlan | None = None
        self.task_graph: TaskGraph | None = None
        self.current_task: CognitiveTask | None = None
        self.previous_tasks: list[str] = []
        self.observations: list[Observation] = []
        self.tool_results: list[dict[str, Any]] = []
        self.failures: list[FailureDiagnosis] = []
        self.decisions: list[dict[str, Any]] = []
        self.verification_state: dict[str, Any] = {}

    def add_observation(self, observation: Observation) -> None:
        self.observations.append(observation)
        while len(json.dumps([item.to_dict() for item in self.observations])) > self.max_context_size and self.observations:
            self.observations.pop(0)

    def add_failure(self, failure: FailureDiagnosis) -> None:
        self.failures.append(failure)
        while len(json.dumps([item.to_dict() for item in self.failures])) > self.max_context_size and self.failures:
            self.failures.pop(0)

    def to_dict(self) -> dict[str, Any]:
        return {"current_goal": self.current_goal.to_dict() if self.current_goal else None, "current_intent": self.current_intent.to_dict() if self.current_intent else None, "current_plan": self.current_plan.to_dict() if self.current_plan else None, "task_graph": self.task_graph.to_dict() if self.task_graph else None, "current_task": self.current_task.to_dict() if self.current_task else None, "previous_tasks": self.previous_tasks, "observations": [item.to_dict() for item in self.observations], "failures": [item.to_dict() for item in self.failures], "decisions": self.decisions, "verification_state": self.verification_state}


class FailureDiagnosisEngine:
    def diagnose(self, task: CognitiveTask, outcome: TaskOutcome) -> FailureDiagnosis:
        text = (outcome.error or outcome.summary or "").lower()
        if "approval" in text or outcome.status is TaskStatus.BLOCKED:
            return FailureDiagnosis(task.task_id, FailureKind.PERMISSION_FAILURE, ConfidenceLevel.CONFIDENT, "Kernel permission or approval boundary blocked execution.", False, requires_approval=True)
        if "unknown tool" in text or "tool" in text and "failed" in text:
            return FailureDiagnosis(task.task_id, FailureKind.TOOL_FAILURE, ConfidenceLevel.PROBABLE, "Kernel reported a tool failure; one bounded alternative may be attempted.", True)
        if "verify" in text or "incomplete" in text:
            return FailureDiagnosis(task.task_id, FailureKind.VERIFICATION_FAILURE, ConfidenceLevel.PROBABLE, "Execution did not satisfy the requested verification condition.", True)
        if outcome.steps_completed > 0:
            return FailureDiagnosis(task.task_id, FailureKind.STRATEGY_FAILURE, ConfidenceLevel.PROBABLE, "A bounded strategy completed only part of the requested work.", True)
        return FailureDiagnosis(task.task_id, FailureKind.UNKNOWN_FAILURE, ConfidenceLevel.UNCERTAIN, "Failure cause is not certain; do not escalate without more evidence.", False)


class ReplanningEngine:
    def __init__(self, max_replans: int = 1):
        self.max_replans = max_replans

    def replan(self, graph: TaskGraph, failed: CognitiveTask, diagnosis: FailureDiagnosis, count: int) -> tuple[TaskGraph, bool, str]:
        if count >= self.max_replans or not diagnosis.retryable:
            return graph, False, "Replan limit reached or failure is not safely retryable."
        failed.status = SubtaskStatus.PENDING
        failed.attempt_count += 1
        failed.error = diagnosis.reason
        failed.updated_at = utc_now()
        return graph, True, "Preserved completed subtasks and replanned only the failed and remaining dependency suffix."


class CognitiveVerifier:
    def __init__(self, workspace: Path):
        self.workspace = Path(workspace).expanduser().resolve()

    def verify_goal(self, goal: CognitiveGoal, graph: TaskGraph, observations: list[Observation], gaps: list[CapabilityGap] | None = None) -> VerificationReport:
        return self.verify(goal, graph, observations, gaps or [])

    def verify(self, goal: CognitiveGoal, graph: TaskGraph, observations: list[Observation], gaps: list[CapabilityGap]) -> VerificationReport:
        checks: list[dict[str, Any]] = []
        required = len(graph.nodes)
        completed = sum(node.status is SubtaskStatus.SUCCEEDED for node in graph.nodes)
        failed = sum(node.status in {SubtaskStatus.FAILED, SubtaskStatus.BLOCKED} for node in graph.nodes)
        checks.append({"name": "all_subtasks_verified", "success": failed == 0 and completed == required, "completed": completed, "required": required})
        for criterion in goal.success_criteria:
            success = True
            if criterion.check_type == "artifact" and criterion.target:
                candidates = list(self.workspace.glob(f"**/*{criterion.target}*")) if criterion.target != "workspace_output" else []
                success = bool(candidates) or any(criterion.target in item.output for item in observations) or any(item.tool == "workspace_write" and item.status == TaskStatus.SUCCEEDED.value for item in observations)
            elif criterion.check_type == "completeness":
                success = completed == required and failed == 0
            elif criterion.check_type == "clarification":
                success = False
            elif criterion.check_type in {"subtasks", "goal"}:
                success = failed == 0 and completed == required and not gaps
            checks.append({"criterion_id": criterion.criterion_id, "description": criterion.description, "success": success})
        if gaps:
            outcome = CognitiveOutcome.BLOCKED
        elif failed and completed:
            outcome = CognitiveOutcome.PARTIAL
        elif failed:
            outcome = CognitiveOutcome.FAILED
        elif all(bool(item["success"]) for item in checks):
            outcome = CognitiveOutcome.SUCCESS
        else:
            outcome = CognitiveOutcome.FAILED
        summary = f"Verified {completed}/{required} subtasks; {failed} failed." if not gaps else f"Blocked by {len(gaps)} capability gap(s)."
        return VerificationReport(goal.goal_id, outcome, outcome is CognitiveOutcome.SUCCESS, summary, checks, completed, failed, required)


class CognitivePersistence:
    def __init__(self, store: SQLiteStore):
        self.store = store

    def save_goal_bundle(self, goal: CognitiveGoal, intent: IntentModel, graph: TaskGraph | None, plans: list[CognitivePlan], state: CognitiveStateRecord, observations: list[Observation], decisions: list[dict[str, Any]], verification: VerificationReport | None = None) -> None:
        self.store.save_cognitive_goal(goal)
        self.store.save_cognitive_intent(intent)
        if graph:
            self.store.save_cognitive_task_graph(graph)
            for node in graph.nodes:
                self.store.save_cognitive_task(node)
        for plan in plans:
            self.store.save_cognitive_plan(plan)
        self.store.save_cognitive_state(state)
        for observation in observations:
            self.store.save_cognitive_observation(observation)
        for decision in decisions:
            self.store.save_cognitive_decision(decision)
        if verification:
            self.store.save_cognitive_verification(verification)

    def load_goal(self, goal_id: str) -> CognitiveGoal | None:
        row = self.store.cognitive_goal_by_id(goal_id)
        return _goal_from_payload(row) if row else None

    def load_bundle(self, goal_id: str) -> tuple[CognitiveGoal | None, IntentModel | None, TaskGraph | None, CognitiveStateRecord | None, list[Observation], list[FailureDiagnosis]]:
        goal_row = self.store.cognitive_goal_by_id(goal_id)
        if not goal_row:
            return None, None, None, None, [], []
        goal = _goal_from_payload(goal_row)
        intent_row = self.store.cognitive_intent_by_goal(goal_id)
        intent = _intent_from_payload(intent_row) if intent_row else IntentEngine().build(goal)
        graph_row = self.store.cognitive_task_graph_by_goal(goal_id)
        graph = _graph_from_payload(graph_row) if graph_row else None
        state_row = self.store.cognitive_state_by_goal(goal_id)
        state = _state_from_payload(state_row) if state_row else None
        observations = [_observation_from_payload(row) for row in self.store.find_cognitive_observations(goal_id)]
        failures: list[FailureDiagnosis] = []
        for row in self.store.find_cognitive_decisions(goal_id):
            decision = _payload(row)
            if decision.get("decision_type") == "failure":
                diagnosis_payload = decision.get("payload", decision)
                failures.append(_failure_from_payload({"payload": diagnosis_payload}))
        return goal, intent, graph, state, observations, failures


class CognitiveOrchestrator:
    #: The shipped caps, as data. The runtime needs them back on rollback (an overlay that disappears
    #: must restore these numbers, not leave its own behind), and a second literal copy of the table in
    #: ``runtime.py`` would be a third place for a default to disagree with the others.
    DEFAULT_POLICY: dict[str, int] = {
        "max_subtasks": 12,
        "max_plan_candidates": 3,
        "max_reasoning_iterations": 4,
        "max_replans": 1,
        "max_execution_time": 120,
        "max_context_size": 12000,
        "max_tool_calls": 24,
    }


    """Bounded cognitive coordinator. Kernel, security, approvals, and Phase 9 remain authoritative."""

    def __init__(self, workspace: Path, model: Any | None = None, store: SQLiteStore | None = None, kernel: AgentKernel | None = None, kernel_factory: Callable[[Path, SQLiteStore], AgentKernel] | None = None, evolution_orchestrator: EvolutionOrchestrator | None = None, policy: dict[str, int] | None = None, external_integrations: Any | None = None, integration_intelligence: Any | None = None, specialist_delegation: Any | None = None, model_intelligence: Any | None = None, adaptive_learning: Any | None = None, self_model: Any | None = None, meta_reasoning: Any | None = None):
        self.workspace = Path(workspace).expanduser().resolve()
        self.workspace.mkdir(parents=True, exist_ok=True)
        self.store = store or SQLiteStore(self.workspace / ".evo" / "agent.sqlite3")
        self.model = model
        self.kernel = kernel
        self.kernel_factory = kernel_factory
        self.evolution = evolution_orchestrator or EvolutionOrchestrator(self.store, Path(__file__).resolve().parent.parent)
        self.policy = dict(self.DEFAULT_POLICY)
        self.policy.update(policy or {})
        self.goal_engine = GoalUnderstandingEngine()
        self.intent_engine = IntentEngine()
        self.criteria_engine = SuccessCriteriaEngine()
        self.decomposer = TaskDecompositionEngine(self.policy["max_subtasks"])
        self.graph_engine = TaskGraphEngine()
        self.planner = PlanningEngine(self.policy["max_plan_candidates"])
        self.reasoner = ReasoningEngine(self.policy["max_reasoning_iterations"])
        self.gap_detector = CapabilityGapDetector(self.evolution.metamorphosis)
        self.context = ContextManager(self.policy["max_context_size"])
        self.diagnoser = FailureDiagnosisEngine()
        self.replanner = ReplanningEngine(self.policy["max_replans"])
        self.verifier = CognitiveVerifier(self.workspace)
        self.persistence = CognitivePersistence(self.store)
        self.experiences = ExperienceEngine(self.store)
        self.memory = MemoryManager(self.store, self.workspace, max_memories=self.policy["max_context_size"] // 1200, max_memory_bytes=self.policy["max_context_size"])
        self.capability_intelligence: CapabilityIntelligence | None = None
        self._capability_analyses: list[CapabilityAnalysis] = []
        self._memory_context: CognitiveMemoryContext | None = None
        self.world_intelligence: WorldModelEngine | None = None
        self.external_integrations = external_integrations or integration_intelligence
        self.specialist_delegation = specialist_delegation
        self.model_intelligence = model_intelligence
        self.adaptive_learning = adaptive_learning
        self.self_model = self_model
        self.meta_reasoning = meta_reasoning

    #: caps the engines captured at construction, and the attribute to re-bind when one changes.
    #: Without this table an overlay could set ``policy["max_subtasks"]`` while the decomposition
    #: engine went on using the number it was built with - a document that reads as applied and is
    #: inert, which is the failure mode this phase exists to eliminate.
    POLICY_BINDINGS: dict[str, tuple[str, str]] = {
        "max_subtasks": ("decomposer", "max_subtasks"),
        "max_plan_candidates": ("planner", "max_plan_candidates"),
        "max_reasoning_iterations": ("reasoner", "max_reasoning_iterations"),
        "max_replans": ("replanner", "max_replans"),
        "max_context_size": ("context", "max_context_size"),
    }

    def apply_policy(self, overrides: dict[str, int] | None) -> dict[str, Any]:
        """Adopt validated caps from an overlay and report exactly what moved.

        The caller has already run the payload through the schema in :mod:`evo_agent.active_version`;
        this method re-checks membership rather than trusting that, because it is public and will be
        called from somewhere the schema is not. A cap that is not declared in ``policy`` is refused -
        a new policy knob has to be a decision in two places before an evolution candidate can use it
        to change behaviour.
        """
        decisions, refused = self.plan_policy(overrides)
        if refused:
            return {"applied": {}, "refused": refused, "not_applied": True}
        applied: dict[str, dict[str, int]] = {}
        for key, number, binding in decisions:
            if int(self.policy[key]) == number:
                continue
            applied[key] = {"from": int(self.policy[key]), "to": number}
            self.policy[key] = number
            if binding:
                owner_name, attribute = binding
                setattr(getattr(self, owner_name), attribute, number)
        return {"applied": applied, "refused": refused}

    def plan_policy(self, overrides: dict[str, int] | None) -> tuple[list[tuple[str, int, tuple[str, str] | None]], list[str]]:
        """Decide which caps would be adopted, without adopting any. Returns ``(decisions, refusals)``.

        The whole-overlay atomicity in :mod:`evo_agent.active_version` needs every consumer to be able to
        answer "would you accept this?" before anything is written, because a policy that half-applied
        would leave the ledger saying *refused* while a planner ran under a raised cap - and the next
        cycle re-applies from the shipped defaults, so the half-state would persist silently.

        Each decision carries its binding target so the commit cannot re-derive (and so mis-derive) what
        was approved here.
        """
        decisions: list[tuple[str, int, tuple[str, str] | None]] = []
        refused: list[str] = []
        for key, value in (overrides or {}).items():
            if key not in self.policy:
                refused.append(f"{key}: not a policy field this orchestrator declares")
                continue
            try:
                number = int(value)
            except (TypeError, ValueError):
                refused.append(f"{key}: {value!r} is not an integer")
                continue
            if number < 1:
                refused.append(f"{key}: {number} is below the floor of 1")
                continue
            binding = self.POLICY_BINDINGS.get(key)
            if binding:
                owner_name, attribute = binding
                owner = getattr(self, owner_name, None)
                if owner is None or not hasattr(owner, attribute):
                    refused.append(f"{key}: bound to {owner_name}.{attribute}, which is not present")
                    continue
            decisions.append((key, number, binding))
        return decisions, refused

    def run(self, text: str, goal_id: str | None = None) -> CognitiveResult:
        return self.run_goal(text, goal_id)

    def execute_goal(self, text: str, goal_id: str | None = None) -> CognitiveResult:
        return self.run_goal(text, goal_id)

    def run_goal(self, text: str, goal_id: str | None = None) -> CognitiveResult:
        started = time.monotonic()
        goal = self.goal_engine.understand(text, goal_id)
        self.context = ContextManager(self.policy["max_context_size"])
        self.memory.begin_task(goal.goal_id, goal.original_text, [item.text for item in goal.constraints if item.critical])
        self.context.current_goal = goal
        intent = self.intent_engine.build(goal)
        state = CognitiveStateRecord(goal.goal_id, CognitiveState.UNDERSTANDING)
        decisions: list[dict[str, Any]] = []
        if self.meta_reasoning is not None:
            try:
                meta_record = self.meta_reasoning.reason(text, {"clarified": goal.ambiguity is AmbiguityStatus.CLEAR, "missing_requirements": goal.missing_requirements}, goal.goal_id)
                decisions.append({"goal_id": goal.goal_id, "decision_type": "meta_reasoning", "record_id": meta_record.record_id, "recommendation": meta_record.recommendation, "confidence": meta_record.confidence, "execution_authority": "kernel", "verification_authority": "cognitive_verifier", "created_at": utc_now()})
            except Exception as exc:
                decisions.append({"goal_id": goal.goal_id, "decision_type": "meta_reasoning", "recommendation": "unavailable", "reason": f"Meta-reasoning unavailable: {type(exc).__name__}", "execution_authority": "kernel", "created_at": utc_now()})
        self._persist(goal, intent, None, [], state, [], decisions)
        if goal.ambiguity is AmbiguityStatus.CRITICAL:
            state.state = CognitiveState.WAITING_FOR_INPUT
            state.last_error = "; ".join(goal.missing_requirements)
            goal.status = state.state
            self._persist(goal, intent, None, [], state, [], decisions, None)
            self.memory.end_task()
            return CognitiveResult(goal, intent, None, None, state, CognitiveOutcome.WAITING_FOR_INPUT, f"Clarification required: {state.last_error}", memory_context=self._memory_context)
        if goal.ambiguity is AmbiguityStatus.AMBIGUOUS:
            state.state = CognitiveState.WAITING_FOR_INPUT
            state.last_error = "; ".join(goal.missing_requirements)
            goal.status = state.state
            self._persist(goal, intent, None, [], state, [], decisions, None)
            self.memory.end_task()
            return CognitiveResult(goal, intent, None, None, state, CognitiveOutcome.WAITING_FOR_INPUT, f"Non-critical ambiguity requires input: {state.last_error}", memory_context=self._memory_context)
        if time.monotonic() - started > self.policy["max_execution_time"]:
            return self._safe_failure(goal, intent, state, "Goal understanding exceeded the execution limit.")
        state.state = CognitiveState.PLANNING
        goal.status = state.state
        graph = self.decomposer.decompose(goal, intent)
        graph_errors = self.graph_engine.validate(graph)
        if graph_errors:
            return self._safe_failure(goal, intent, state, "; ".join(graph_errors), graph=graph)
        self._memory_context = self.memory.cognitive_context(RetrievalQuery(goal=goal.normalized_goal, max_memories=self.policy["max_context_size"] // 1200, max_memory_bytes=self.policy["max_context_size"], architecture_version=self._architecture_version()))
        world = self._get_world_intelligence()
        world_model = world.observe(goal.normalized_goal, task=graph.nodes[0] if graph.nodes else None)
        environment_snapshot = world.create_snapshot(world_model)
        world.save_observations(world_model)
        environment_context = world.context_for_task(goal.normalized_goal, task=graph.nodes[0] if graph.nodes else None)
        plans = self.planner.generate(goal, graph, self._architecture_version(), self._memory_context)
        plan = self.reasoner.select_plan(plans, self._available_tools())
        if self.external_integrations is not None:
            external_requirements = self.external_integrations.requirements_for_goal(goal.normalized_goal)
            external_candidates = self.external_integrations.discover_for_goal(goal.normalized_goal)
            decisions.append({"goal_id": goal.goal_id, "decision_type": "external_integration_discovery", "requirements": external_requirements, "candidate_integrations": [item.integration_id for item in external_candidates], "execution_authority": "kernel", "created_at": utc_now()})
            if external_candidates:
                plan.rationale += f" External integration discovery found {len(external_candidates)} registered candidate(s); execution remains Kernel-authorized."
        self._capability_analyses = self._analyze_capabilities(goal, graph)
        if self.model_intelligence is not None:
            try:
                model_requirements = [str(item.requirement.capability_id) for item in self._capability_analyses]
                model_selection = self.model_intelligence.select_model(goal.goal_id, goal.normalized_goal, capability_requirements=model_requirements, context_requirements={"min_context_tokens": min(12000, len(goal.normalized_goal) + len(json.dumps(self._memory_context.to_dict() if self._memory_context else {})))}, risk=RiskLevel.LOW, task={"structured_output": False})
                decisions.append({"goal_id": goal.goal_id, "decision_type": "model_selection", "selected_model_id": model_selection.selected_model_id, "alternatives": model_selection.fallback_model_ids, "reason": model_selection.explanation, "confidence": model_selection.confidence, "execution_authority": "kernel", "verification_authority": "cognitive_verifier", "created_at": utc_now()})
                if model_selection.selected_model_id:
                    plan.rationale += f" Model Intelligence selected {model_selection.selected_model_id} as an advisory routing decision; execution remains Kernel-authorized."
            except Exception as exc:
                decisions.append({"goal_id": goal.goal_id, "decision_type": "model_selection", "selected_model_id": None, "reason": f"Model selection unavailable: {type(exc).__name__}", "execution_authority": "kernel", "created_at": utc_now()})
        if self.specialist_delegation is not None and self.specialist_delegation.is_complex_goal(goal.normalized_goal, len(graph.nodes)):
            required = [str(item.requirement.capability_id) for item in self._capability_analyses]
            specialist_candidates = self.specialist_delegation.discover_for_goal(goal.normalized_goal, required_capabilities=required or None)
            decisions.append({"goal_id": goal.goal_id, "decision_type": "specialist_discovery", "complex_goal": True, "candidate_specialists": [item.specialist_id for item in specialist_candidates], "execution_authority": "runtime_specialist_engine", "verification_authority": "cognitive_verifier", "created_at": utc_now()})
            if specialist_candidates:
                plan.rationale += f" Specialist discovery found {len(specialist_candidates)} subordinate candidate(s); delegation remains bounded and advisory until explicitly contracted."
        plan.capability_requirements = [item.requirement.to_dict() for item in self._capability_analyses]
        plan.capability_selection = [item.selection.to_dict() for item in self._capability_analyses]
        plan.environment_id = world_model.environment.environment_id
        plan.environment_version = world_model.environment.environment_version
        plan.environment_hash = environment_context.context_hash
        plan.environment_context = environment_context.to_dict()
        plan.environment_observation_ids = [item.observation_id for item in world_model.observations]
        selected = [item.selection.selected_tool.name for item in self._capability_analyses if item.selection.selected_tool]
        if selected:
            plan.rationale += f" Capability intelligence selected {len(selected)} compatible tool method(s) through the existing authority chain."
        self.memory.add_working(json.dumps(plan.to_dict(), sort_keys=True), "Current selected plan", importance=0.9, critical=True, source_id=goal.goal_id)
        self.context.current_intent = intent
        self.context.task_graph = graph
        self.context.current_plan = plan
        self._persist(goal, intent, graph, [plan], state, [], decisions)
        gaps: list[CapabilityGap] = []
        for node in graph.nodes:
            gap = self.gap_detector.check(goal.goal_id, node, self._available_capabilities())
            if gap:
                gaps.append(gap)
        existing_capabilities = {self._capability_family(item.capability) for item in gaps}
        assessment_map = {CapabilityAvailability.UNKNOWN: CapabilityAssessment.UNKNOWN, CapabilityAvailability.UNAVAILABLE: CapabilityAssessment.UNAVAILABLE, CapabilityAvailability.PARTIAL: CapabilityAssessment.INCOMPATIBLE, CapabilityAvailability.INCOMPATIBLE: CapabilityAssessment.INCOMPATIBLE, CapabilityAvailability.BLOCKED: CapabilityAssessment.RESTRICTED}
        for analysis in self._capability_analyses:
            if analysis.availability is CapabilityAvailability.AVAILABLE:
                continue
            family = self._capability_family(analysis.requirement.capability_id)
            if family in existing_capabilities:
                continue
            gaps.append(CapabilityGap(goal.goal_id, "capability-" + analysis.requirement.requirement_id, family, assessment_map.get(analysis.availability, CapabilityAssessment.UNKNOWN), "; ".join(analysis.reasons), analysis.structural))
            existing_capabilities.add(family)
        plan_validation = world.validate_plan(plan, world_model)
        decisions.append({"goal_id": goal.goal_id, "decision_type": "environment_plan_validation", "decision": plan_validation.status.value, "reason": "; ".join(plan_validation.reasons), "created_at": utc_now(), "payload": plan_validation.to_dict()})
        if plan_validation.status in {PlanValidationStatus.INVALID, PlanValidationStatus.STALE} and not gaps:
            state.state = CognitiveState.BLOCKED
            state.last_error = "; ".join(plan_validation.reasons)
            goal.status = state.state
            self._persist(goal, intent, graph, [plan], state, [], decisions)
            self.memory.end_task()
            return CognitiveResult(goal, intent, plan, graph, state, CognitiveOutcome.BLOCKED, state.last_error, memory_context=self._memory_context)
        if gaps:
            for gap in gaps:
                gap.opportunity_id = self._route_capability_gap(goal, gap)
                decisions.append({"goal_id": goal.goal_id, "decision_type": "capability_gap", "task_id": gap.task_id, "decision": gap.assessment.value, "confidence": "confident", "reason": gap.reason, "created_at": utc_now(), "payload": gap.to_dict()})
            state.state = CognitiveState.BLOCKED
            state.last_error = "; ".join(gap.reason for gap in gaps)
            goal.status = state.state
            self._persist(goal, intent, graph, plans, state, [], decisions)
            self.memory.end_task()
            return CognitiveResult(goal, intent, plan, graph, state, CognitiveOutcome.BLOCKED, state.last_error, capability_gaps=gaps, memory_context=self._memory_context)
        state.state = CognitiveState.EXECUTING
        goal.status = state.state
        result = self._execute(goal, intent, graph, plan, state, decisions, started)
        return result

    def clarify(self, goal_id: str, clarification: str) -> CognitiveResult:
        existing = self.persistence.load_goal(goal_id)
        if not existing:
            raise KeyError(goal_id)
        clarification = clarification.strip()
        if not clarification:
            raise ValueError("clarification must not be empty")
        return self.run_goal(f"{existing.original_text}. Clarification: {clarification}", goal_id=goal_id)

    def provide_clarification(self, goal_id: str, clarification: str) -> CognitiveResult:
        return self.clarify(goal_id, clarification)

    def resume(self, goal_id: str) -> CognitiveResult:
        goal, intent, graph, state, observations, failures = self.persistence.load_bundle(goal_id)
        if not goal or not intent or not graph or not state:
            raise KeyError(goal_id)
        if state.state is CognitiveState.COMPLETED:
            verification = self._stored_verification(goal_id)
            outcome = verification.outcome if verification else CognitiveOutcome.INCONCLUSIVE
            return CognitiveResult(goal, intent, self._stored_plan(goal_id), graph, state, outcome, "Loaded completed cognitive goal.", verification, observations, failures)
        if state.state in {CognitiveState.WAITING_FOR_INPUT, CognitiveState.WAITING_FOR_APPROVAL, CognitiveState.BLOCKED}:
            return CognitiveResult(goal, intent, self._stored_plan(goal_id), graph, state, CognitiveOutcome(state.state.value), "Goal remains waiting or blocked.", observations=observations, failures=failures)
        current = graph.node(state.current_task_id) if state.current_task_id else None
        if current and current.status is SubtaskStatus.EXECUTING:
            if current.tool_name in {"workspace_list", "workspace_read"}:
                current.status = SubtaskStatus.PENDING
                current.attempt_count += 1
                current.error = "Interrupted read-only task safely re-queued after restart."
                state.current_task_id = None
                state.updated_at = utc_now()
                self.persistence.save_goal_bundle(goal, intent, graph, [self._stored_plan(goal_id)] if self._stored_plan(goal_id) else [], state, observations, [], None)
            else:
                current.status = SubtaskStatus.FAILED
                current.error = "Interrupted task execution was not assumed safe to replay."
                state.state = CognitiveState.FAILED
                state.last_error = current.error
                state.updated_at = utc_now()
                self.persistence.save_goal_bundle(goal, intent, graph, [self._stored_plan(goal_id)] if self._stored_plan(goal_id) else [], state, observations, [], None)
                return CognitiveResult(goal, intent, self._stored_plan(goal_id), graph, state, CognitiveOutcome.INCONCLUSIVE, current.error, observations=observations, failures=failures)
        state.state = CognitiveState.EXECUTING
        plan = self._stored_plan(goal_id)
        if plan and plan.architecture_version and plan.architecture_version != self._architecture_version():
            state.state = CognitiveState.FAILED
            state.last_error = "Persisted plan is stale because the architecture version changed; revalidation is required."
            state.updated_at = utc_now()
            self._persist(goal, intent, graph, [plan], state, observations, [], None)
            return CognitiveResult(goal, intent, plan, graph, state, CognitiveOutcome.INCONCLUSIVE, state.last_error, observations=observations, failures=failures)
        if plan:
            world = self._get_world_intelligence()
            current_world = world.observe(goal.normalized_goal)
            validation = world.validate_plan(plan, current_world)
            if validation.status is not PlanValidationStatus.VALID:
                state.state = CognitiveState.FAILED
                state.last_error = "; ".join(validation.reasons)
                state.updated_at = utc_now()
                self._persist(goal, intent, graph, [plan], state, observations, [{"goal_id": goal_id, "decision_type": "environment_plan_validation", "decision": validation.status.value, "reason": state.last_error, "created_at": utc_now(), "payload": validation.to_dict()}], None)
                return CognitiveResult(goal, intent, plan, graph, state, CognitiveOutcome.INCONCLUSIVE, state.last_error, observations=observations, failures=failures)
        return self._execute(goal, intent, graph, plan, state, [], time.monotonic()) if plan else self._safe_failure(goal, intent, state, "Persisted plan is unavailable.", graph=graph)

    def _execute(self, goal: CognitiveGoal, intent: IntentModel, graph: TaskGraph, plan: CognitivePlan, state: CognitiveStateRecord, decisions: list[dict[str, Any]], started: float) -> CognitiveResult:
        observations = list(self.context.observations)
        failures: list[FailureDiagnosis] = []
        ordered = [graph.node(task_id) for task_id in plan.steps]
        ordered = [node for node in ordered if node]
        index = 0
        while index < len(ordered):
            if time.monotonic() - started > self.policy["max_execution_time"] or state.tool_call_count >= self.policy["max_tool_calls"]:
                state.state = CognitiveState.FAILED
                state.last_error = "Cognitive resource limit reached; execution stopped safely."
                return self._finish(goal, intent, graph, plan, state, CognitiveOutcome.INCONCLUSIVE, "Resource limit reached before all subtasks were verified.", observations, failures, decisions)
            node = ordered[index]
            if node.status is SubtaskStatus.SUCCEEDED:
                index += 1
                continue
            if any((graph.node(dep) is None or graph.node(dep).status is not SubtaskStatus.SUCCEEDED) for dep in node.dependencies):
                node.status = SubtaskStatus.BLOCKED
                failures.append(FailureDiagnosis(node.task_id, FailureKind.PLANNING_FAILURE, ConfidenceLevel.CONFIDENT, "Dependency was not satisfied; executor refused to run this task.", False))
                break
            node.status = SubtaskStatus.EXECUTING
            state.current_task_id = node.task_id
            state.updated_at = utc_now()
            self._persist(goal, intent, graph, [plan], state, observations, decisions)
            outcome = self._run_kernel(node, goal)
            state.tool_call_count += 1
            observation = self._observe(node, outcome)
            observations.append(observation)
            self.context.add_observation(observation)
            world = self._get_world_intelligence()
            current_world = world.update_after_action(goal=goal.normalized_goal)
            environment_validation = world.validate_plan(plan, current_world)
            if environment_validation.status is not PlanValidationStatus.VALID:
                decisions.append({"goal_id": goal.goal_id, "decision_type": "environment_change", "task_id": node.task_id, "decision": environment_validation.status.value, "reason": "; ".join(environment_validation.reasons), "created_at": utc_now(), "payload": environment_validation.to_dict()})
                if environment_validation.status is PlanValidationStatus.INVALID or state.replan_count >= self.policy["max_replans"]:
                    state.state = CognitiveState.FAILED
                    state.last_error = "; ".join(environment_validation.reasons)
                    return self._finish(goal, intent, graph, plan, state, CognitiveOutcome.INCONCLUSIVE, state.last_error, observations, failures, decisions)
                flex_outcome = TaskOutcome(new_id("environment"), TaskStatus.FAILED, "Environment changed: " + "; ".join(environment_validation.reasons), 0, [], "environment state changed")
                flex_decision = self._ask_flexibility(goal, node, flex_outcome, state)
                decisions.append({"goal_id": goal.goal_id, "decision_type": "environment_flexibility", "task_id": node.task_id, "decision": getattr(flex_decision, "action", "replan"), "reason": getattr(flex_decision, "reason", "Existing FlexibilityEngine considered the current environment change."), "created_at": utc_now()})
                state.replan_count += 1
                context = world.context_for_task(goal.normalized_goal, task=node)
                plan.environment_id = current_world.environment.environment_id
                plan.environment_version = current_world.environment.environment_version
                plan.environment_hash = context.context_hash
                plan.environment_context = context.to_dict()
                plan.environment_observation_ids = [item.observation_id for item in current_world.observations]
                self._persist(goal, intent, graph, [plan], state, observations, decisions)
            kernel_verified = any(event.event_type is EventType.VERIFICATION and bool(event.payload.get("success")) for event in outcome.events)
            if outcome.status is TaskStatus.SUCCEEDED and kernel_verified:
                node.status = SubtaskStatus.SUCCEEDED
                node.result_task_id = outcome.task_id
                node.updated_at = utc_now()
                index += 1
                state.state = CognitiveState.OBSERVING
                self._persist(goal, intent, graph, [plan], state, observations, decisions)
                state.state = CognitiveState.VERIFYING
                continue
            if outcome.status is TaskStatus.SUCCEEDED and not kernel_verified:
                outcome = TaskOutcome(outcome.task_id, TaskStatus.FAILED, "Kernel returned success without authoritative verification.", outcome.steps_completed, outcome.events, "Goal verification is incomplete")
            diagnosis = self.diagnoser.diagnose(node, outcome)
            failures.append(diagnosis)
            self.context.add_failure(diagnosis)
            decisions.append({"goal_id": goal.goal_id, "decision_type": "failure", "task_id": node.task_id, "decision": diagnosis.kind.value, "confidence": diagnosis.confidence.value, "reason": diagnosis.reason, "created_at": utc_now(), "payload": diagnosis.to_dict()})
            node.status = SubtaskStatus.BLOCKED if diagnosis.requires_approval else SubtaskStatus.FAILED
            node.error = outcome.error or outcome.summary
            if diagnosis.requires_approval:
                state.state = CognitiveState.WAITING_FOR_APPROVAL
                state.last_error = diagnosis.reason
                return self._finish(goal, intent, graph, plan, state, CognitiveOutcome.WAITING_FOR_APPROVAL, diagnosis.reason, observations, failures, decisions)
            state.state = CognitiveState.REPLANNING
            flexibility_decision = self._ask_flexibility(goal, node, outcome, state)
            if flexibility_decision is not None:
                decisions.append({"goal_id": goal.goal_id, "decision_type": "flexibility", "task_id": node.task_id, "decision": getattr(flexibility_decision, "action", "unknown"), "confidence": diagnosis.confidence.value, "reason": getattr(flexibility_decision, "reason", "Existing FlexibilityEngine decision"), "created_at": utc_now()})
                if getattr(flexibility_decision, "action", "") == "stop":
                    diagnosis.retryable = False
            graph, replanned, reason = self.replanner.replan(graph, node, diagnosis, state.replan_count)
            decisions.append({"goal_id": goal.goal_id, "decision_type": "replan", "task_id": node.task_id, "decision": "replan" if replanned else "stop", "confidence": diagnosis.confidence.value, "reason": reason, "created_at": utc_now()})
            if replanned:
                state.replan_count += 1
                node.status = SubtaskStatus.EXECUTING
                outcome = self._run_kernel(node, goal, recovery=True)
                state.tool_call_count += 1
                retry_observation = self._observe(node, outcome)
                observations.append(retry_observation)
                self.context.add_observation(retry_observation)
                retry_verified = any(event.event_type is EventType.VERIFICATION and bool(event.payload.get("success")) for event in outcome.events)
                if outcome.status is TaskStatus.SUCCEEDED and retry_verified:
                    node.status = SubtaskStatus.SUCCEEDED
                    node.result_task_id = outcome.task_id
                    index += 1
                    state.state = CognitiveState.VERIFYING
                    self._persist(goal, intent, graph, [plan], state, observations, decisions)
                    continue
                if outcome.status is TaskStatus.SUCCEEDED and not retry_verified:
                    outcome = TaskOutcome(outcome.task_id, TaskStatus.FAILED, "Recovery returned success without authoritative verification.", outcome.steps_completed, outcome.events, "Goal verification is incomplete")
                node.status = SubtaskStatus.FAILED
                failures.append(self.diagnoser.diagnose(node, outcome))
            break
        verification = self.verifier.verify(goal, graph, observations, [])
        outcome = verification.outcome
        if outcome is CognitiveOutcome.SUCCESS:
            state.state = CognitiveState.COMPLETED
            goal.status = state.state
        elif outcome is CognitiveOutcome.PARTIAL:
            state.state = CognitiveState.FAILED
            goal.status = state.state
        else:
            state.state = CognitiveState.FAILED
            goal.status = state.state
        return self._finish(goal, intent, graph, plan, state, outcome, verification.summary, observations, failures, decisions, verification)

    def _ask_flexibility(self, goal: CognitiveGoal, node: CognitiveTask, outcome: TaskOutcome, state: CognitiveStateRecord) -> Any | None:
        try:
            kernel = self._get_kernel()
            flexibility = getattr(kernel, "flexibility", None)
            if flexibility is None:
                return None
            historical = self.memory.retrieve(RetrievalQuery(goal=goal.normalized_goal, subtask=node.description, failure=outcome.error or outcome.summary, max_memories=4, max_memory_bytes=4000))
            capability_engine = self._get_capability_intelligence()
            fallback = capability_engine.fallback_for(goal.normalized_goal, node, [node.tool_name] if node.tool_name else [], self._architecture_version())
            context = FlexibilityContext(Goal(goal.original_text), failures=[{"task": node.description, "error": outcome.error or outcome.summary}], attempt=state.replan_count, constraints={"historical_memories": [item.to_dict() for item in historical], "capability_fallbacks": [item.to_dict() for item in fallback], "capability_requirements": [item.to_dict() for item in capability_engine.requirements_for(goal.normalized_goal, node)]})
            failed_step = PlanStep(node.task_id, node.description, node.tool_name, {}, node.risk, "kernel verification")
            result = ToolResult(new_id("call"), node.tool_name or "unknown", False, error=outcome.error or outcome.summary)
            return flexibility.recommend_next_action(context, failed_step, result)
        except Exception:
            return None

    def _run_kernel(self, node: CognitiveTask, goal: CognitiveGoal, recovery: bool = False) -> TaskOutcome:
        kernel = self._get_kernel()
        request = node.description
        if node.tool_name == "workspace_list" and "read" in goal.original_text.lower() and "file" in goal.original_text.lower():
            # Planning uses normalized text for deterministic intent matching, but the
            # Kernel must receive the original path spelling for case-sensitive workspaces.
            request = goal.original_text
        if node.tool_name == "workspace_read":
            if "report" in node.description.lower() or "output" in node.description.lower():
                request = "read file report.txt"
            else:
                discovered: list[str] = []
                for observation in reversed(self.context.observations):
                    if observation.tool == "workspace_list" and observation.output:
                        try:
                            discovered = [item for item in json.loads(observation.output) if str(item).lower().endswith((".txt", ".csv"))]
                        except (TypeError, ValueError, json.JSONDecodeError):
                            discovered = []
                        if discovered:
                            break
                request = f"read file {discovered[0]}" if discovered else "read file README.md"
        elif node.tool_name == "workspace_write" and "report" in node.description.lower():
            request = "create a report"
        if recovery:
            request = f"Use a different bounded recovery strategy for this task: {request}"
        return kernel.run(request)

    def _get_kernel(self) -> AgentKernel:
        if self.kernel is not None:
            return self.kernel
        if self.kernel_factory:
            self.kernel = self.kernel_factory(self.workspace, self.store)
            return self.kernel
        if self.model is None:
            from .model_adapter import RuleBasedAdapter
            self.model = RuleBasedAdapter()
        self.kernel = AgentKernel(self.workspace, self.model, store=self.store)
        return self.kernel

    def _observe(self, node: CognitiveTask, outcome: TaskOutcome) -> Observation:
        outputs = [str(event.payload.get("output", "")) for event in outcome.events if event.event_type is EventType.TOOL_COMPLETED and event.payload.get("output")]
        errors = [str(event.payload.get("error", "")) for event in outcome.events if event.event_type is EventType.TOOL_FAILED and event.payload.get("error")]
        tools = [str(event.payload.get("tool")) for event in outcome.events if event.event_type is EventType.TOOL_REQUESTED and event.payload.get("tool")]
        verification = [str(event.payload.get("summary")) for event in outcome.events if event.event_type is EventType.VERIFICATION]
        return Observation(new_id("observation"), node.goal_id, node.task_id, tools[-1] if tools else node.tool_name, "\n".join(outputs)[-2000:], outcome.status.value, errors, [], 0.0, [], verification)

    def _finish(self, goal: CognitiveGoal, intent: IntentModel, graph: TaskGraph, plan: CognitivePlan, state: CognitiveStateRecord, outcome: CognitiveOutcome, summary: str, observations: list[Observation], failures: list[FailureDiagnosis], decisions: list[dict[str, Any]], verification: VerificationReport | None = None) -> CognitiveResult:
        state.updated_at = utc_now()
        state.last_error = None if outcome in {CognitiveOutcome.SUCCESS, CognitiveOutcome.PARTIAL} else state.last_error or (failures[-1].reason if failures else None)
        self._persist(goal, intent, graph, [plan], state, observations, decisions, verification)
        experience_id, evaluation_id = self._record_experience(goal, state, outcome, observations, failures, verification)
        self.memory.end_task()
        return CognitiveResult(goal, intent, plan, graph, state, outcome, summary, verification, observations, failures, [], state.replan_count, experience_id, evaluation_id, self._memory_context)

    def _safe_failure(self, goal: CognitiveGoal, intent: IntentModel, state: CognitiveStateRecord, reason: str, graph: TaskGraph | None = None) -> CognitiveResult:
        state.state = CognitiveState.FAILED
        state.last_error = reason
        goal.status = state.state
        self._persist(goal, intent, graph, [], state, [], [])
        self.memory.end_task()
        return CognitiveResult(goal, intent, None, graph, state, CognitiveOutcome.INCONCLUSIVE, reason, memory_context=self._memory_context)

    def _persist(self, goal: CognitiveGoal, intent: IntentModel, graph: TaskGraph | None, plans: list[CognitivePlan], state: CognitiveStateRecord, observations: list[Observation], decisions: list[dict[str, Any]], verification: VerificationReport | None = None) -> None:
        self.persistence.save_goal_bundle(goal, intent, graph, plans, state, observations, decisions, verification)

    def _record_experience(self, goal: CognitiveGoal, state: CognitiveStateRecord, outcome: CognitiveOutcome, observations: list[Observation], failures: list[FailureDiagnosis], verification: VerificationReport | None) -> tuple[str | None, str | None]:
        if outcome in {CognitiveOutcome.WAITING_FOR_INPUT, CognitiveOutcome.WAITING_FOR_APPROVAL}:
            return None, None
        status = TaskStatus.SUCCEEDED if outcome is CognitiveOutcome.SUCCESS else TaskStatus.FAILED if outcome in {CognitiveOutcome.FAILED, CognitiveOutcome.PARTIAL} else TaskStatus.BLOCKED
        events = [Event(goal.goal_id, EventType.TASK_CREATED, {"goal": goal.original_text}), Event(goal.goal_id, EventType.STRATEGY_SELECTED, {"strategy": {"name": "cognitive-bounded"}})]
        for observation in observations:
            if observation.tool:
                events.append(Event(goal.goal_id, EventType.TOOL_REQUESTED, {"tool": observation.tool}))
            if observation.output:
                events.append(Event(goal.goal_id, EventType.TOOL_COMPLETED, {"tool": observation.tool, "output": observation.output}))
            for error in observation.errors:
                events.append(Event(goal.goal_id, EventType.TOOL_FAILED, {"tool": observation.tool, "error": error}))
        report = verification or VerificationReport(goal.goal_id, outcome, outcome is CognitiveOutcome.SUCCESS, outcome.value, [], 0, len(failures), 0)
        events.append(Event(goal.goal_id, EventType.VERIFICATION, {"success": report.success, "summary": report.summary, "checks": report.checks}))
        events.append(Event(goal.goal_id, EventType.TASK_COMPLETED, {"status": status.value, "summary": report.summary}))
        for analysis in self._capability_analyses:
            selected_event = Event(goal.goal_id, EventType.CAPABILITY_SELECTED, {"analysis": analysis.to_dict(), "requirement_id": analysis.requirement.requirement_id})
            events.append(selected_event)
            self.store.append_event(selected_event)
        task_outcome = TaskOutcome(goal.goal_id, status, report.summary, report.completed_count, events, None if report.success else report.summary)
        experience = self.experiences.create(task_outcome, __version__, "cognitive")
        experience.capability_selection = [analysis.to_dict() for analysis in self._capability_analyses]
        try:
            world_model = self._get_world_intelligence().current
            if world_model:
                experience.environment_id = world_model.environment.environment_id
                experience.environment_version = world_model.environment.environment_version
                experience.architecture_version = self._architecture_version()
                experience.relevant_environment_hash = self.context.current_plan.environment_hash if self.context.current_plan else ""
                experience.tool_environment = {item.get("name", ""): {"version": item.get("version"), "provider": item.get("provider"), "availability": item.get("availability"), "health": item.get("health", {}).get("status")} for item in world_model.environment.available_tools[:50]}
                experience.resource_conditions = dict(world_model.environment.resource_state)
        except Exception:
            pass
        self.experiences.persist(experience)
        evaluation = self.evolution.evaluations.evaluate(experience)
        try:
            self.memory.capture_experience(experience)
            self.memory.capture_evaluation(evaluation)
            world_model = self._get_world_intelligence().current
            if world_model:
                self.memory.capture_environment(world_model.environment, task_id=goal.goal_id, goal=goal.original_text, outcome=outcome.value)
            for observation in observations:
                self.memory.capture_observation(observation)
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            pass
        self.store.save_evaluation(evaluation)
        self.store.update_experience_evaluation(experience.experience_id, evaluation.evaluation_id, evaluation.to_dict())
        self.store.add_memory("cognitive", json.dumps({"goal_id": goal.goal_id, "outcome": outcome.value, "summary": report.summary}), utc_now())
        return experience.experience_id, evaluation.evaluation_id

    def _route_capability_gap(self, goal: CognitiveGoal, gap: CapabilityGap) -> str | None:
        path = OrchestrationPath.METAMORPHOSIS if gap.structural else OrchestrationPath.EVOLUTION
        problem = f"Cognitive capability gap: {gap.capability}. {goal.original_text}"
        fingerprint = hashlib.sha256(json.dumps({"goal": goal.goal_id, "capability": gap.capability, "structural": gap.structural, "problem": problem}, sort_keys=True).encode()).hexdigest()
        opportunity = EvolutionOpportunity(new_id("opportunity"), [], [], problem, 1, "high", ["cognitive_goal"], ["architecture"] if gap.structural else ["planner"], [gap.capability], "strong", path, 0.85, architecture_version=self._architecture_version(), fingerprint=fingerprint, metadata={"cognitive_goal_id": goal.goal_id, "capability_gap": gap.to_dict(), "cognitive_capability_gap": True, "structural": gap.structural})
        item = self.evolution.create_work_item(opportunity)
        if item:
            routed = self.evolution.route_to_engine(item.work_item_id)
            if path is OrchestrationPath.METAMORPHOSIS and routed.proposal_id:
                proposal = self.evolution.metamorphosis.get_proposal(routed.proposal_id)
                if proposal and getattr(proposal.status, "value", proposal.status) == "validated":
                    from .models import MetamorphosisStatus
                    proposal.status = MetamorphosisStatus.PENDING_APPROVAL
                    self.store.save_metamorphosis_proposal(proposal)
        return opportunity.opportunity_id

    def _get_world_intelligence(self) -> WorldModelEngine:
        if self.world_intelligence is None:
            kernel = self._get_kernel()
            observer = EnvironmentObserver(self.workspace, self.store, self._get_capability_intelligence(), getattr(kernel, "policy", None), __version__, self._architecture_version())
            self.world_intelligence = WorldModelEngine(self.store, observer, WorldRefreshEngine(observer, self.store))
        return self.world_intelligence

    def _get_capability_intelligence(self) -> CapabilityIntelligence:
        kernel = self._get_kernel()
        if self.capability_intelligence is None or self.capability_intelligence.tools.runtime_registry is not getattr(kernel, "tools", None):
            self.capability_intelligence = CapabilityIntelligence(self.store, self.workspace, getattr(kernel, "tools", None), getattr(kernel, "policy", None), self.memory, __version__)
        return self.capability_intelligence

    def _analyze_capabilities(self, goal: CognitiveGoal, graph: TaskGraph) -> list[CapabilityAnalysis]:
        engine = self._get_capability_intelligence()
        world = self._get_world_intelligence()
        environment_context = world.context_for_task(goal.normalized_goal, task=graph.nodes[0] if graph.nodes else None)
        analyses: list[CapabilityAnalysis] = []
        seen: set[str] = set()
        for node in graph.nodes:
            for requirement in engine.requirements_for(goal.normalized_goal, node):
                if requirement.capability_id in seen:
                    continue
                seen.add(requirement.capability_id)
                analyses.append(engine.analyze_requirement(requirement, engine.build_context(goal.normalized_goal, node, [requirement], environment=environment_context), self._architecture_version()))
        return analyses

    @staticmethod
    def _capability_family(name: str) -> str:
        return {"media": "multimedia_generation", "shell_execution": "shell", "filesystem_read": "filesystem", "filesystem_write": "filesystem", "file_discovery": "filesystem", "text_processing": "filesystem", "report_generation": "filesystem"}.get(name, name)

    def _available_capabilities(self) -> list[str]:
        try:
            return [item.name for item in self.evolution.metamorphosis.list_capabilities() if getattr(item.status, "value", item.status) == "active"]
        except Exception:
            return ["filesystem", "shell", "planning", "memory", "verification"]

    def _available_tools(self) -> list[str]:
        try:
            return [spec["function"]["name"] for spec in self._get_kernel().tools.schemas()]
        except Exception:
            return ["workspace_list", "workspace_read", "workspace_write", "shell"]

    def _architecture_version(self) -> str:
        try:
            return self.evolution.metamorphosis.get_architecture().architecture_version
        except Exception:
            return "unknown"

    def _stored_plan(self, goal_id: str) -> CognitivePlan | None:
        row = self.store.cognitive_plan_by_goal(goal_id, selected=True) or self.store.cognitive_plan_by_goal(goal_id)
        return _plan_from_payload(row) if row else None

    def _stored_verification(self, goal_id: str) -> VerificationReport | None:
        row = self.store.cognitive_verification_by_goal(goal_id)
        return _verification_from_payload(row) if row else None


def _payload(row: dict[str, Any] | None) -> dict[str, Any]:
    if not row:
        return {}
    value = row.get("payload", row)
    return json.loads(value) if isinstance(value, str) else dict(value)


def _goal_from_payload(row: dict[str, Any]) -> CognitiveGoal:
    data = _payload(row)
    data["constraints"] = [Requirement(item["text"], RequirementKind(item["kind"]), item.get("critical", False)) for item in data.get("constraints", [])]
    data["success_criteria"] = [SuccessCriterion(**item) for item in data.get("success_criteria", [])]
    data["ambiguity"] = AmbiguityStatus(data["ambiguity"])
    data["status"] = CognitiveState(data.get("status", CognitiveState.INITIALIZING.value))
    return CognitiveGoal(**data)


def _intent_from_payload(row: dict[str, Any]) -> IntentModel:
    data = _payload(row)
    data["risk_level"] = RiskLevel(data["risk_level"])
    return IntentModel(**data)


def _task_from_payload(data: dict[str, Any]) -> CognitiveTask:
    data = dict(data)
    data["risk"] = RiskLevel(data.get("risk", RiskLevel.LOW.value))
    data["status"] = SubtaskStatus(data.get("status", SubtaskStatus.PENDING.value))
    return CognitiveTask(**data)


def _graph_from_payload(row: dict[str, Any]) -> TaskGraph:
    data = _payload(row)
    return TaskGraph(data["graph_id"], data["goal_id"], TaskGraphType(data["graph_type"]), [_task_from_payload(item) for item in data.get("nodes", [])], [tuple(item) for item in data.get("edges", [])], data.get("created_at", utc_now()))


def _plan_from_payload(row: dict[str, Any]) -> CognitivePlan:
    data = _payload(row)
    data["estimated_risk"] = RiskLevel(data["estimated_risk"])
    return CognitivePlan(**data)


def _state_from_payload(row: dict[str, Any]) -> CognitiveStateRecord:
    data = _payload(row)
    data["state"] = CognitiveState(data["state"])
    return CognitiveStateRecord(**data)


def _observation_from_payload(row: dict[str, Any]) -> Observation:
    return Observation(**_payload(row))


def _failure_from_payload(row: dict[str, Any]) -> FailureDiagnosis:
    data = _payload(row)
    return FailureDiagnosis(data.get("task_id", ""), FailureKind(data.get("kind", FailureKind.UNKNOWN_FAILURE.value)), ConfidenceLevel(data.get("confidence", ConfidenceLevel.UNCERTAIN.value)), data.get("reason", ""), bool(data.get("retryable", False)), bool(data.get("requires_input", False)), bool(data.get("requires_approval", False)))


def _verification_from_payload(row: dict[str, Any]) -> VerificationReport:
    data = _payload(row)
    data["outcome"] = CognitiveOutcome(data["outcome"])
    return VerificationReport(**data)


__all__ = [
    "AmbiguityStatus", "CapabilityAssessment", "CapabilityGap", "CapabilityGapDetector", "CognitiveGoal", "CognitiveOrchestrator", "CognitiveOutcome", "CognitivePlan", "CognitiveResult", "CognitiveState", "CognitiveStateRecord", "CognitiveTask", "CognitiveVerifier", "ConfidenceLevel", "ContextManager", "FailureDiagnosis", "FailureDiagnosisEngine", "FailureKind", "GoalUnderstandingEngine", "IntentEngine", "IntentModel", "Observation", "PlanningEngine", "ReasoningEngine", "ReplanningEngine", "Requirement", "RequirementKind", "SuccessCriteriaEngine", "SuccessCriterion", "SubtaskStatus", "TaskDecompositionEngine", "TaskGraph", "TaskGraphEngine", "TaskGraphType", "VerificationReport"
]
