from __future__ import annotations

from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
import fcntl
import hashlib
import json
from pathlib import Path
import threading
from typing import Any, Callable, Iterable
import uuid

from .benchmark import Benchmark, BenchmarkEngine
from .evaluation import EvaluationEngine
from .experience import Experience, ExperienceEngine
from .evolver import EvolutionFinding, Evolver
from .metamorphosis import MetamorphosisEngine, StructuralChange
from .models import (
    ApprovalType,
    DeduplicationStatus,
    Event,
    EventType,
    MetamorphosisStatus,
    OutcomeType,
    OpportunityStatus,
    OrchestrationPath,
    ProposalRisk,
    QueueItemStatus,
    StructuralChangeType,
    TaskOutcome,
    WorkItemState,
    new_id,
    utc_now,
)
from .promotion import PromotionEngine, PromotionRecord, PromotionRequest
from .storage import SQLiteStore
from .version import __version__


@dataclass
class EvolutionOpportunity:
    opportunity_id: str
    source_experience_ids: list[str]
    source_evaluation_ids: list[str]
    problem: str
    frequency: int
    severity: str
    affected_task_types: list[str]
    affected_components: list[str]
    affected_capabilities: list[str]
    evidence_strength: str
    recommended_change_type: OrchestrationPath
    confidence: float
    status: OpportunityStatus = OpportunityStatus.DETECTED
    created_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)
    fingerprint: str = ""
    architecture_version: str = ""
    classification_reason: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["recommended_change_type"] = self.recommended_change_type.value
        data["status"] = self.status.value
        return data


@dataclass
class EvolutionWorkItem:
    work_item_id: str
    opportunity_id: str
    change_type: OrchestrationPath
    source_ids: list[str]
    current_state: WorkItemState
    target_component: str
    target_capability: str | None
    proposal_id: str | None
    experiment_id: str | None
    benchmark_id: str | None
    evidence_id: str | None
    promotion_id: str | None
    current_version: str
    architecture_version: str
    candidate_version: str | None
    attempt_count: int
    cooldown_until: str | None
    last_error: str | None
    created_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["change_type"] = self.change_type.value
        data["current_state"] = self.current_state.value
        return data


@dataclass
class ApprovalRequest:
    approval_request_id: str
    work_item_id: str
    approval_type: ApprovalType
    status: str = "pending"
    actor: str = "orchestrator"
    reason: str = ""
    created_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["approval_type"] = self.approval_type.value
        return data


@dataclass
class ExperimentQueueItem:
    queue_id: str
    work_item_id: str
    engine: str
    experiment_id: str | None = None
    status: QueueItemStatus = QueueItemStatus.QUEUED
    attempt_count: int = 0
    created_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["status"] = self.status.value
        return data


@dataclass
class PromotionQueueItem:
    queue_id: str
    work_item_id: str
    candidate_version: str
    promotion_id: str | None = None
    status: QueueItemStatus = QueueItemStatus.QUEUED
    created_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["status"] = self.status.value
        return data


@dataclass
class CooldownRecord:
    opportunity_key: str
    opportunity_id: str
    attempt_count: int
    last_attempt: str
    last_result: str
    cooldown_until: str | None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class OrchestrationAuditEvent:
    event_id: str
    work_item_id: str
    opportunity_id: str
    event_name: str
    previous_state: str | None
    current_state: str
    change_type: str
    component: str
    version: str
    actor: str
    reason: str
    result: str
    created_at: str = field(default_factory=utc_now)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ClassificationResult:
    path: OrchestrationPath
    confidence: float
    reason: str
    protected: bool = False


@dataclass
class OrchestrationPolicy:
    minimum_confidence: float = 0.45
    flexibility_repeat_threshold: int = 2
    evolution_repeat_threshold: int = 3
    cooldown_seconds: int = 3600
    max_work_items_per_cycle: int = 5
    max_experiments_per_cycle: int = 1
    max_promotions_per_cycle: int = 1
    max_failed_attempts: int = 3
    max_same_opportunity_attempts: int = 3
    stale_after_seconds: int = 900


@dataclass
class CycleResult:
    cycle_id: str
    observed_experiences: int
    detected_opportunities: int
    created_work_items: int
    processed_work_items: int
    experiments_started: int
    promotions_started: int
    approvals_waiting: int
    failures: list[str] = field(default_factory=list)
    stopped_reason: str = "bounded_cycle_complete"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class OpportunityDetector:
    """Deterministic detector over the existing Experience/Evaluation records."""

    PROTECTED_TERMS = ("governance", "permission", "approval", "sandbox", "rollback", "verification", "audit", "kill switch", "trust boundary", "promotion authorization")
    STRUCTURAL_TERMS = ("architecture", "architectural", "component", "capability", "dependency", "composition", "cannot solve", "structural limitation", "missing capability")

    def __init__(self, policy: OrchestrationPolicy | None = None):
        self.policy = policy or OrchestrationPolicy()

    def detect(self, experiences: Iterable[Experience | dict[str, Any]], architecture_version: str = "", metamorphosis_records: Iterable[dict[str, Any]] | None = None, evolution_records: Iterable[dict[str, Any]] | None = None) -> list[EvolutionOpportunity]:
        records = [self._as_dict(item) for item in experiences]
        opportunities: list[EvolutionOpportunity] = []
        groups: dict[tuple[str, str | None], list[dict[str, Any]]] = {}
        for record in records:
            groups.setdefault((str(record.get("task_type", "general")), record.get("selected_strategy")), []).append(record)
        for (task_type, strategy), group in sorted(groups.items()):
            failures = [item for item in group if str(item.get("final_outcome", "")).lower() in {"failure", "timeout", "aborted", "blocked"}]
            recovery = [item for item in group if item.get("recovery_attempts") or item.get("strategy_changes")]
            low_scores = [item for item in group if self._score(item) is not None and self._score(item) < 60]
            switches = [item for item in group if len(item.get("strategy_changes") or []) > 0]
            if len(failures) >= self.policy.flexibility_repeat_threshold:
                path = OrchestrationPath.EVOLUTION if len(failures) >= self.policy.evolution_repeat_threshold else OrchestrationPath.FLEXIBILITY
                problem = f"Repeated {str(strategy or 'unknown')} strategy failures for task type '{task_type}'."
                opportunities.append(self._opportunity(group, problem, path, "high" if path is OrchestrationPath.EVOLUTION else "medium", [task_type], ["strategy-selection"], ["strategy_selection"], architecture_version, len(failures), metadata={"failure_count": len(failures), "escalation_reason": "repeat_threshold"}))
            elif len(low_scores) >= self.policy.flexibility_repeat_threshold:
                problem = f"Evaluation scores for task type '{task_type}' remain below the deterministic quality threshold."
                opportunities.append(self._opportunity(low_scores, problem, OrchestrationPath.EVOLUTION, "medium", [task_type], ["planning-heuristics"], ["planning"], architecture_version, len(low_scores), metadata={"mean_score": round(sum(self._score(item) or 0 for item in low_scores) / len(low_scores), 2)}))
            elif len(recovery) >= self.policy.flexibility_repeat_threshold or len(switches) >= self.policy.flexibility_repeat_threshold:
                problem = f"Task type '{task_type}' repeatedly requires recovery or strategy switching."
                opportunities.append(self._opportunity(recovery or switches, problem, OrchestrationPath.FLEXIBILITY, "medium", [task_type], ["flexibility"], ["recovery", "strategy_selection"], architecture_version, len(recovery or switches), metadata={"recovery_count": len(recovery), "strategy_switch_count": len(switches)}))
            tools: dict[str, list[dict[str, Any]]] = {}
            for item in group:
                for failure in item.get("failures") or []:
                    tool = str(failure.get("tool", ""))
                    if tool:
                        tools.setdefault(tool, []).append(item)
            for tool, failed_records in sorted(tools.items()):
                if len(failed_records) >= self.policy.flexibility_repeat_threshold:
                    problem = f"Tool '{tool}' fails repeatedly for task type '{task_type}'."
                    opportunities.append(self._opportunity(failed_records, problem, OrchestrationPath.FLEXIBILITY, "medium", [task_type], ["tool-selection"], [tool], architecture_version, len(failed_records), metadata={"tool": tool}))
        strategy_groups: dict[str, dict[str, list[dict[str, Any]]]] = {}
        for record in records:
            strategy = record.get("selected_strategy")
            if strategy:
                strategy_groups.setdefault(str(record.get("task_type", "general")), {}).setdefault(str(strategy), []).append(record)
        for task_type, strategies in sorted(strategy_groups.items()):
            rates = [(sum(item.get("final_outcome") == OutcomeType.SUCCESS.value for item in items) / len(items), strategy, items) for strategy, items in strategies.items() if items]
            rates.sort(reverse=True)
            if len(rates) >= 2 and len(rates[0][2]) >= 2 and rates[0][0] >= rates[1][0] + 0.25 and rates[0][0] >= 0.5:
                problem = f"A successful alternative strategy outperforms the current strategy for task type '{task_type}'."
                records_for_evidence = rates[0][2] + rates[1][2]
                opportunities.append(self._opportunity(records_for_evidence, problem, OrchestrationPath.FLEXIBILITY, "medium", [task_type], ["strategy-selection"], ["strategy_selection"], architecture_version, len(records_for_evidence), metadata={"preferred_strategy": rates[0][1], "current_strategy": rates[1][1], "successful_alternative": True}))
        capability_regressions = [record for record in records if (record.get("evaluation_result") or {}).get("capability_regression") or (record.get("evaluation_result") or {}).get("required_capabilities_lost")]
        if capability_regressions:
            problem = "Recorded evaluation evidence indicates a capability regression."
            opportunities.append(self._opportunity(capability_regressions, problem, OrchestrationPath.METAMORPHOSIS, "high", sorted({str(item.get("task_type", "general")) for item in capability_regressions}), ["architecture"], ["capability_composition"], architecture_version, len(capability_regressions), metadata={"capability_regression": True}))
        structural_records = [record for record in records if any(term in str(record.get("original_goal", "")).lower() for term in self.STRUCTURAL_TERMS)]
        if structural_records:
            problem = "Recorded task evidence indicates an architectural or capability limitation."
            opportunities.append(self._opportunity(structural_records, problem, OrchestrationPath.METAMORPHOSIS, "high", sorted({str(item.get("task_type", "general")) for item in structural_records}), ["architecture"], ["capability_composition"], architecture_version, len(structural_records), metadata={"structural_evidence": True}))
        failed_evolution = [self._payload(record) for record in evolution_records or [] if str(self._payload(record).get("status", record.get("status", ""))) in {"failed", "aborted", "timeout"}]
        if len(failed_evolution) >= self.policy.evolution_repeat_threshold:
            problem = "Repeated controlled evolution failures suggest a possible architectural limitation."
            opportunities.append(self._opportunity([], problem, OrchestrationPath.METAMORPHOSIS, "high", [], ["architecture"], ["capability_composition"], architecture_version, len(failed_evolution), metadata={"failed_evolution_count": len(failed_evolution), "escalated_from_evolution": True}))
        for record in metamorphosis_records or []:
            payload = self._payload(record)
            if str(payload.get("status", "")) in {MetamorphosisStatus.WORSE.value, MetamorphosisStatus.INCONCLUSIVE.value}:
                problem = "A prior structural candidate did not produce reliable improvement; gather evidence before retrying."
                opportunities.append(self._opportunity([], problem, OrchestrationPath.INCONCLUSIVE, "low", [], ["architecture"], [], architecture_version, 1, metadata={"metamorphosis_experiment_id": payload.get("experiment_id")}))
        return self._deduplicate(opportunities)

    def _opportunity(self, records: list[dict[str, Any]], problem: str, path: OrchestrationPath, severity: str, task_types: list[str], components: list[str], capabilities: list[str], architecture_version: str, frequency: int, metadata: dict[str, Any]) -> EvolutionOpportunity:
        experience_ids = list(dict.fromkeys(str(item.get("experience_id")) for item in records if item.get("experience_id")))
        evaluation_ids = list(dict.fromkeys(str(item.get("evaluation_id")) for item in records if item.get("evaluation_id")))
        confidence = min(1.0, round(0.35 + min(0.45, frequency / 10) + (0.15 if len(experience_ids) >= 2 else 0.0) + (0.05 if path is not OrchestrationPath.INCONCLUSIVE else 0.0), 3))
        fingerprint_body = {"problem": problem.lower(), "source_experience_ids": sorted(experience_ids), "source_evaluation_ids": sorted(evaluation_ids), "components": sorted(components), "capabilities": sorted(capabilities), "path": path.value, "architecture_version": architecture_version}
        fingerprint = hashlib.sha256(json.dumps(fingerprint_body, sort_keys=True).encode()).hexdigest()
        now = utc_now()
        return EvolutionOpportunity(new_id("opportunity"), experience_ids, evaluation_ids, problem, frequency, severity, task_types, components, capabilities, "strong" if frequency >= 3 else "moderate", path, confidence, fingerprint=fingerprint, architecture_version=architecture_version, metadata=metadata, created_at=now, updated_at=now)

    def _deduplicate(self, opportunities: list[EvolutionOpportunity]) -> list[EvolutionOpportunity]:
        seen: set[str] = set()
        result: list[EvolutionOpportunity] = []
        for opportunity in opportunities:
            if opportunity.fingerprint not in seen:
                seen.add(opportunity.fingerprint)
                result.append(opportunity)
        return result

    @staticmethod
    def _score(record: dict[str, Any]) -> float | None:
        result = record.get("evaluation_result") or {}
        value = result.get("success_score")
        return float(value) if isinstance(value, (int, float)) else None

    @staticmethod
    def _payload(record: dict[str, Any]) -> dict[str, Any]:
        payload = record.get("payload", record)
        return json.loads(payload) if isinstance(payload, str) else dict(payload)

    @staticmethod
    def _as_dict(record: Experience | dict[str, Any]) -> dict[str, Any]:
        return record.to_dict() if isinstance(record, Experience) else OpportunityDetector._payload(record)


class ChangeClassifier:
    """Rule-first classifier that never selects a stronger path because of uncertainty."""

    PROTECTED_TERMS = OpportunityDetector.PROTECTED_TERMS
    STRUCTURAL_TERMS = OpportunityDetector.STRUCTURAL_TERMS

    def classify(self, opportunity: EvolutionOpportunity) -> ClassificationResult:
        text = f"{opportunity.problem} {' '.join(opportunity.affected_components)} {' '.join(opportunity.affected_capabilities)}".lower()
        if any(term in text for term in self.PROTECTED_TERMS):
            return ClassificationResult(OrchestrationPath.NO_CHANGE, 1.0, "Protected-core concern is not routable to a change engine.", protected=True)
        if opportunity.confidence < 0.45 or opportunity.frequency < 1:
            return ClassificationResult(OrchestrationPath.INCONCLUSIVE, opportunity.confidence, "Evidence is insufficient for a deterministic route.")
        if opportunity.recommended_change_type is OrchestrationPath.NO_CHANGE:
            return ClassificationResult(OrchestrationPath.NO_CHANGE, opportunity.confidence, "Evidence does not justify a controlled change.")
        if any(term in text for term in self.STRUCTURAL_TERMS) or opportunity.recommended_change_type is OrchestrationPath.METAMORPHOSIS:
            return ClassificationResult(OrchestrationPath.METAMORPHOSIS, opportunity.confidence, "Evidence indicates a structural or capability limitation.")
        if opportunity.frequency >= 3 or opportunity.recommended_change_type is OrchestrationPath.EVOLUTION:
            return ClassificationResult(OrchestrationPath.EVOLUTION, opportunity.confidence, "Repeated evidence justifies changing existing behavior.")
        if opportunity.recommended_change_type is OrchestrationPath.FLEXIBILITY:
            return ClassificationResult(OrchestrationPath.FLEXIBILITY, opportunity.confidence, "The smallest effective response is bounded runtime adaptation.")
        return ClassificationResult(OrchestrationPath.INCONCLUSIVE, opportunity.confidence, "No deterministic rule selected a safe change path.")


class EvolutionOrchestrator:
    """Persistent, bounded coordinator; specialized engines retain all authority."""

    TERMINAL_STATES = {WorkItemState.COMPLETED, WorkItemState.REJECTED, WorkItemState.INCONCLUSIVE, WorkItemState.FAILED, WorkItemState.ROLLED_BACK, WorkItemState.BLOCKED, WorkItemState.CANCELLED}
    PROTECTED_TERMS = OpportunityDetector.PROTECTED_TERMS
    TRANSITIONS: dict[WorkItemState, set[WorkItemState]] = {
        WorkItemState.DETECTED: {WorkItemState.ANALYZING, WorkItemState.CANCELLED, WorkItemState.INCONCLUSIVE, WorkItemState.FAILED, WorkItemState.BLOCKED},
        WorkItemState.ANALYZING: {WorkItemState.CLASSIFIED, WorkItemState.FAILED, WorkItemState.INCONCLUSIVE},
        WorkItemState.CLASSIFIED: {WorkItemState.QUEUED, WorkItemState.COMPLETED, WorkItemState.INCONCLUSIVE, WorkItemState.REJECTED},
        WorkItemState.QUEUED: {WorkItemState.PROPOSED, WorkItemState.AWAITING_APPROVAL, WorkItemState.COMPLETED, WorkItemState.INCONCLUSIVE, WorkItemState.REJECTED},
        WorkItemState.PROPOSED: {WorkItemState.AWAITING_APPROVAL, WorkItemState.REJECTED, WorkItemState.FAILED},
        WorkItemState.AWAITING_APPROVAL: {WorkItemState.APPROVED, WorkItemState.REJECTED, WorkItemState.BLOCKED},
        WorkItemState.APPROVED: {WorkItemState.SANDBOXING, WorkItemState.BLOCKED},
        WorkItemState.SANDBOXING: {WorkItemState.BENCHMARKING, WorkItemState.FAILED, WorkItemState.INCONCLUSIVE},
        WorkItemState.BENCHMARKING: {WorkItemState.EVALUATING, WorkItemState.FAILED, WorkItemState.INCONCLUSIVE},
        WorkItemState.EVALUATING: {WorkItemState.DECIDED, WorkItemState.FAILED, WorkItemState.INCONCLUSIVE},
        WorkItemState.DECIDED: {WorkItemState.BETTER, WorkItemState.REJECTED, WorkItemState.INCONCLUSIVE},
        WorkItemState.BETTER: {WorkItemState.AWAITING_PROMOTION_APPROVAL},
        WorkItemState.AWAITING_PROMOTION_APPROVAL: {WorkItemState.PROMOTION_APPROVED, WorkItemState.REJECTED, WorkItemState.BLOCKED},
        WorkItemState.PROMOTION_APPROVED: {WorkItemState.PROMOTING, WorkItemState.BLOCKED},
        WorkItemState.PROMOTING: {WorkItemState.HEALTH_CHECK, WorkItemState.ROLLED_BACK, WorkItemState.FAILED},
        WorkItemState.HEALTH_CHECK: {WorkItemState.COMPLETED, WorkItemState.ROLLED_BACK, WorkItemState.FAILED},
        WorkItemState.COMPLETED: {WorkItemState.ROLLED_BACK},
    }

    def __init__(self, store: SQLiteStore, source_root: Path, policy: OrchestrationPolicy | None = None, promotion_engine: PromotionEngine | None = None, flexibility_handler: Callable[[EvolutionOpportunity], Any] | None = None, sandbox_engine: Any | None = None, benchmark_engine: Any | None = None, metamorphosis_engine: Any | None = None):
        self.store = store
        self.source_root = Path(source_root).expanduser().resolve()
        if not self.source_root.is_dir():
            raise FileNotFoundError(self.source_root)
        self.policy = policy or OrchestrationPolicy()
        self.experiences = ExperienceEngine(store)
        self.evaluations = EvaluationEngine()
        self.evolver = Evolver(store, self.experiences)
        self.metamorphosis = metamorphosis_engine or MetamorphosisEngine(store, self.source_root)
        self.sandbox_engine = sandbox_engine
        self.benchmark_engine = benchmark_engine
        self.detector = OpportunityDetector(self.policy)
        self.classifier = ChangeClassifier()
        self.promotion_engine = promotion_engine
        self.flexibility_handler = flexibility_handler
        self._thread_lock = threading.RLock()
        self._lock_depth = 0
        self.lock_path = store.path.parent / "orchestrator.lock"
        self.lock_path.touch(exist_ok=True)

    @contextmanager
    def _exclusive_lock(self):
        with self._thread_lock:
            if self._lock_depth:
                self._lock_depth += 1
                try:
                    yield
                finally:
                    self._lock_depth -= 1
                return
            with self.lock_path.open("r+") as handle:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
                self._lock_depth = 1
                try:
                    yield
                finally:
                    self._lock_depth = 0
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    def observe(self, limit: int = 1000) -> list[Experience]:
        return self.experiences.retrieve(limit=limit)

    def ingest_experience(self, experience: Experience | TaskOutcome, agent_version: str = __version__, model_identifier: str = "orchestrator") -> Experience | None:
        if isinstance(experience, Experience):
            self.experiences.persist(experience)
            return experience
        record = self.experiences.create(experience, agent_version=agent_version, model_identifier=model_identifier)
        evaluation = self.evaluations.evaluate(record)
        record.evaluation_id = evaluation.evaluation_id
        record.evaluation_result = evaluation.to_dict()
        self.experiences.persist(record)
        self.store.save_evaluation(evaluation)
        return record

    def evaluate_opportunity(self, opportunity: EvolutionOpportunity) -> ClassificationResult:
        opportunity.status = OpportunityStatus.ANALYZING
        self.store.save_opportunity(opportunity)
        result = self.classifier.classify(opportunity)
        opportunity.status = OpportunityStatus.CLASSIFIED
        opportunity.recommended_change_type = result.path
        opportunity.classification_reason = result.reason
        opportunity.updated_at = utc_now()
        self.store.save_opportunity(opportunity)
        return result

    def classify_change(self, opportunity: EvolutionOpportunity) -> ClassificationResult:
        return self.evaluate_opportunity(opportunity)

    def select_path(self, opportunity: EvolutionOpportunity) -> OrchestrationPath:
        return self.classifier.classify(opportunity).path

    def create_work_item(self, opportunity: EvolutionOpportunity) -> EvolutionWorkItem | None:
        with self._exclusive_lock():
            existing = self.store.opportunity_by_fingerprint(opportunity.fingerprint)
            if existing:
                return self._existing_work_item_for_opportunity(existing["opportunity_id"])
            if self._is_protected(opportunity):
                opportunity.status = OpportunityStatus.IGNORED
                opportunity.classification_reason = "Protected-core concern cannot be routed by the Orchestrator."
                self.store.save_opportunity(opportunity)
                return None
            self.store.save_opportunity(opportunity)
            classification = self.classifier.classify(opportunity)
            opportunity.recommended_change_type = classification.path
            opportunity.classification_reason = classification.reason
            opportunity.status = OpportunityStatus.QUEUED
            opportunity.updated_at = utc_now()
            self.store.save_opportunity(opportunity)
            current_version, architecture_version = self._current_versions()
            item = EvolutionWorkItem(new_id("work"), opportunity.opportunity_id, classification.path, list(dict.fromkeys(opportunity.source_experience_ids + opportunity.source_evaluation_ids)), WorkItemState.DETECTED, opportunity.affected_components[0] if opportunity.affected_components else "planning", opportunity.affected_capabilities[0] if opportunity.affected_capabilities else None, None, None, None, None, None, current_version, architecture_version, None, 0, None, None, metadata={"classification_reason": classification.reason, "fingerprint": opportunity.fingerprint})
            self.store.save_work_item(item)
            self._audit(item, None, WorkItemState.DETECTED, EventType.OPPORTUNITY_DETECTED.value, "orchestrator", opportunity.problem, "detected")
            self._audit(item, WorkItemState.DETECTED, WorkItemState.DETECTED, EventType.OPPORTUNITY_CLASSIFIED.value, "classifier", classification.reason, classification.path.value)
            self._audit(item, WorkItemState.DETECTED, WorkItemState.DETECTED, EventType.CHANGE_PATH_SELECTED.value, "classifier", classification.reason, classification.path.value)
            self._audit(item, None, WorkItemState.DETECTED, EventType.WORK_ITEM_CREATED.value, "orchestrator", "Persisted deduplicated work item.", "created")
            return item

    def route_to_engine(self, work_item_id: str) -> EvolutionWorkItem:
        item = self.get_work_item(work_item_id)
        if not item:
            raise KeyError(work_item_id)
        opportunity = self.get_opportunity(item.opportunity_id)
        if not opportunity:
            return self._fail(item, "opportunity is missing")
        if item.current_state is WorkItemState.DETECTED:
            self.transition(item.work_item_id, WorkItemState.ANALYZING, "Analyze persisted opportunity")
            item = self.get_work_item(work_item_id)
        classification = self.classifier.classify(opportunity)
        if item.current_state is WorkItemState.ANALYZING:
            self.transition(item.work_item_id, WorkItemState.CLASSIFIED, classification.reason)
            item = self.get_work_item(work_item_id)
        if classification.protected:
            return self._transition_terminal(item, WorkItemState.REJECTED, classification.reason)
        if classification.path is OrchestrationPath.NO_CHANGE:
            return self._transition_terminal(item, WorkItemState.COMPLETED, classification.reason)
        if classification.path is OrchestrationPath.INCONCLUSIVE:
            return self._transition_terminal(item, WorkItemState.INCONCLUSIVE, classification.reason)
        if item.current_state is WorkItemState.CLASSIFIED:
            self.transition(item.work_item_id, WorkItemState.QUEUED, "Select smallest effective change path")
            item = self.get_work_item(work_item_id)
        if classification.path is OrchestrationPath.FLEXIBILITY:
            result = self._run_flexibility(opportunity)
            item = self.get_work_item(work_item_id)
            item.metadata["flexibility_result"] = result
            self.store.save_work_item(item)
            return self._transition_terminal(item, WorkItemState.COMPLETED, "Delegated bounded runtime adaptation to Flexibility Engine")
        if classification.path is OrchestrationPath.EVOLUTION:
            return self._create_evolution_proposal(item, opportunity)
        if classification.path is OrchestrationPath.METAMORPHOSIS:
            return self._create_metamorphosis_proposal(item, opportunity)
        return self._fail(item, "invalid orchestration route")

    def _create_evolution_proposal(self, item: EvolutionWorkItem, opportunity: EvolutionOpportunity) -> EvolutionWorkItem:
        if item.proposal_id:
            return item
        evidence = self._evidence_for_opportunity(opportunity)
        finding = EvolutionFinding("orchestrated_opportunity", item.target_component or "planning-heuristics", opportunity.affected_task_types[0] if opportunity.affected_task_types else "general", opportunity.problem, evidence, "Apply a bounded, measurable behavior/configuration adjustment for the observed pattern.", "Improve verified outcome while retaining existing bounded controls.", ["Regression on unseen tasks", "Overfitting to available evidence"], opportunity.affected_capabilities or ["planning"], "Run the existing deterministic benchmark and compare verified outcomes.", "Restore the prior behavior/configuration.", risk=ProposalRisk.MEDIUM, confidence=opportunity.confidence)
        proposal = self.evolver.generate_proposal(finding)
        self.evolver.persist_proposal(proposal)
        item.proposal_id = proposal.proposal_id
        self.store.save_work_item(item)
        if proposal.status.value == "rejected":
            return self._transition_terminal(item, WorkItemState.REJECTED, "; ".join(proposal.validation_errors) or "Evolution proposal rejected by Evolver")
        self.transition(item.work_item_id, WorkItemState.PROPOSED, "Existing Controlled Evolver created a proposal")
        self._request_approval(self.get_work_item(item.work_item_id), ApprovalType.EVOLUTION, "Human approval required before ordinary sandbox execution")
        return self.get_work_item(item.work_item_id)

    def _create_metamorphosis_proposal(self, item: EvolutionWorkItem, opportunity: EvolutionOpportunity) -> EvolutionWorkItem:
        if item.proposal_id:
            return item
        target = opportunity.affected_components[0] if opportunity.affected_components else "architecture"
        change = StructuralChange(StructuralChangeType.CHANGE_CONFIGURATION, target, opportunity.affected_components or [target], {"key": "orchestration.candidate", "value": "bounded"}, opportunity.problem)
        proposal = self.metamorphosis.generate_proposal(change, "Test a bounded structural response to the observed limitation", ["Compatibility or capability regression", "Structural change may not generalize"])
        valid, errors = self.metamorphosis.validate_proposal(proposal)
        item.proposal_id = proposal.proposal_id
        self.store.save_work_item(item)
        if not valid:
            return self._transition_terminal(item, WorkItemState.REJECTED, "; ".join(errors))
        self.transition(item.work_item_id, WorkItemState.PROPOSED, "Phase 8 Metamorphosis Engine created a structural proposal")
        self._request_approval(self.get_work_item(item.work_item_id), ApprovalType.METAMORPHOSIS, "Separate metamorphosis approval required before structural sandbox execution")
        return self.get_work_item(item.work_item_id)

    def manage_approval(self, work_item_id: str, approval_type: ApprovalType | str, decision: bool | None = None, reason: str = "", actor: str = "orchestrator") -> ApprovalRequest:
        item = self.get_work_item(work_item_id)
        if not item:
            raise KeyError(work_item_id)
        approval_type = ApprovalType(approval_type)
        request = self._request_approval(item, approval_type, reason or "Explicit human decision required")
        if decision is None:
            return request
        if decision and actor.lower() in {"orchestrator", "system", "autonomous", "agent"}:
            raise PermissionError("The Orchestrator cannot self-approve")
        request.status = "approved" if decision else "rejected"
        request.actor = actor
        request.reason = reason
        request.updated_at = utc_now()
        self.store.save_approval_request(request)
        if not decision:
            self._audit(item, item.current_state, WorkItemState.REJECTED, EventType.APPROVAL_REJECTED.value, actor, reason, "rejected")
            self._transition_terminal(item, WorkItemState.REJECTED, reason or "Approval rejected")
            return request
        self._audit(item, item.current_state, item.current_state, EventType.APPROVAL_RECEIVED.value, actor, reason, "approved")
        if approval_type is ApprovalType.EVOLUTION:
            if not item.proposal_id:
                raise ValueError("Evolution proposal is missing")
            self.evolver.approve(item.proposal_id, reason)
            if item.current_state is WorkItemState.PROPOSED:
                self.transition(item.work_item_id, WorkItemState.AWAITING_APPROVAL, "Approval request recorded")
            self.transition(item.work_item_id, WorkItemState.APPROVED, "Human evolution approval recorded")
        elif approval_type is ApprovalType.METAMORPHOSIS:
            if not item.proposal_id:
                raise ValueError("Metamorphosis proposal is missing")
            self.metamorphosis.approve_proposal(item.proposal_id, reason)
            if item.current_state is WorkItemState.PROPOSED:
                self.transition(item.work_item_id, WorkItemState.AWAITING_APPROVAL, "Approval request recorded")
            self.transition(item.work_item_id, WorkItemState.APPROVED, "Human metamorphosis approval recorded")
        elif approval_type is ApprovalType.PROMOTION:
            if not item.promotion_id:
                raise ValueError("Promotion request is missing")
            self._get_promotion().approve_promotion(item.promotion_id, reason, approved_by=actor)
            queue = PromotionQueueItem(new_id("promotion-queue"), item.work_item_id, item.candidate_version or "", item.promotion_id, metadata={"evidence_required": True, "human_approval": True})
            self.store.save_promotion_queue_item(queue)
            self._audit(self.get_work_item(item.work_item_id), WorkItemState.AWAITING_PROMOTION_APPROVAL, WorkItemState.AWAITING_PROMOTION_APPROVAL, EventType.PROMOTION_QUEUED.value, actor, "Human-approved candidate entered the promotion queue.", "queued")
            self.transition(item.work_item_id, WorkItemState.PROMOTION_APPROVED, "Human promotion approval recorded")
        return request

    def manage_experiment(self, work_item_id: str) -> EvolutionWorkItem:
        item = self.get_work_item(work_item_id)
        if not item:
            raise KeyError(work_item_id)
        if item.current_state is not WorkItemState.APPROVED:
            raise PermissionError("Only explicitly approved work items may enter an experiment")
        self._revalidate_version(item)
        self.transition(work_item_id, WorkItemState.SANDBOXING, "Run only an already-approved bounded experiment")
        item = self.get_work_item(work_item_id)
        queue = ExperimentQueueItem(new_id("experiment-queue"), work_item_id, item.change_type.value, attempt_count=item.attempt_count + 1)
        self.store.save_experiment_queue_item(queue)
        self._audit(item, WorkItemState.APPROVED, WorkItemState.SANDBOXING, EventType.EXPERIMENT_QUEUED.value, "orchestrator", "Queued approved experiment.", "queued")
        self._audit(item, WorkItemState.SANDBOXING, WorkItemState.SANDBOXING, EventType.EXPERIMENT_STARTED.value, "sandbox", "Started bounded isolated experiment.", "running")
        try:
            item.attempt_count += 1
            if item.attempt_count > self.policy.max_same_opportunity_attempts:
                return self._fail(item, "same-opportunity attempt ceiling reached")
            if item.change_type is OrchestrationPath.EVOLUTION:
                experiment = self._sandbox().run_experiment(item.proposal_id or "", retain_sandbox=True)
                queue.experiment_id = experiment.experiment_id
                passed = experiment.status.value == "passed"
                item.experiment_id = experiment.experiment_id
                item.candidate_version = experiment.candidate_version
            elif item.change_type is OrchestrationPath.METAMORPHOSIS:
                experiment = self.metamorphosis.create_structural_candidate(item.proposal_id or "", retain_sandbox=True)
                queue.experiment_id = experiment.experiment_id
                passed = experiment.status is MetamorphosisStatus.SANDBOXED
                item.experiment_id = experiment.experiment_id
                item.candidate_version = experiment.candidate_version
            else:
                return self._fail(item, "unsupported experiment route")
            queue.status = QueueItemStatus.COMPLETED if passed else QueueItemStatus.FAILED
            queue.updated_at = utc_now()
            self.store.save_experiment_queue_item(queue)
            self.store.save_work_item(item)
            if not passed:
                return self._fail(item, "bounded sandbox experiment did not pass")
            self.transition(work_item_id, WorkItemState.BENCHMARKING, "Sandbox passed; benchmark is required before any decision")
            self._audit(self.get_work_item(work_item_id), WorkItemState.SANDBOXING, WorkItemState.BENCHMARKING, EventType.EXPERIMENT_COMPLETED.value, "sandbox", "Approved isolated experiment completed.", "passed")
            return self.get_work_item(work_item_id)
        except Exception as exc:
            queue.status = QueueItemStatus.FAILED
            queue.updated_at = utc_now()
            self.store.save_experiment_queue_item(queue)
            return self._fail(self.get_work_item(work_item_id), f"experiment error: {exc}")

    def collect_evidence(self, work_item_id: str) -> EvolutionWorkItem:
        item = self.get_work_item(work_item_id)
        if not item:
            raise KeyError(work_item_id)
        if item.current_state is not WorkItemState.BENCHMARKING:
            raise PermissionError("Benchmarking is not pending for this work item")
        benchmark_engine = self.benchmark_engine or BenchmarkEngine(self.store, self.source_root)
        self._audit(item, WorkItemState.BENCHMARKING, WorkItemState.BENCHMARKING, EventType.BENCHMARK_QUEUED.value, "orchestrator", "Queued evidence collection through the existing benchmark engine.", "queued")
        try:
            if item.change_type is OrchestrationPath.EVOLUTION:
                benchmark = benchmark_engine.default_benchmark()
                benchmark_engine.save_benchmark(benchmark)
                item.benchmark_id = benchmark.benchmark_id
                evidence = benchmark_engine.run(benchmark.benchmark_id, item.experiment_id or "")
            elif item.change_type is OrchestrationPath.METAMORPHOSIS:
                evidence = self.metamorphosis.benchmark_structural_candidate(item.experiment_id or "")
                item.benchmark_id = getattr(evidence, "benchmark_id", None)
            else:
                return self._fail(item, "invalid benchmark route")
            item.evidence_id = evidence.evidence_id
            self.store.save_work_item(item)
            self._audit(self.get_work_item(work_item_id), WorkItemState.BENCHMARKING, WorkItemState.BENCHMARKING, EventType.BENCHMARK_COMPLETED.value, "benchmark", "Existing benchmark completed.", evidence.decision.value)
            self.transition(work_item_id, WorkItemState.EVALUATING, "Evidence received from existing benchmark engine")
            self._audit(self.get_work_item(work_item_id), WorkItemState.BENCHMARKING, WorkItemState.EVALUATING, EventType.EVIDENCE_RECEIVED.value, "benchmark", "Evidence package persisted.", evidence.decision.value)
            return self.process_decision(work_item_id)
        except Exception as exc:
            return self._fail(item, f"benchmark/evidence error: {exc}")

    def process_decision(self, work_item_id: str) -> EvolutionWorkItem:
        item = self.get_work_item(work_item_id)
        if not item:
            raise KeyError(work_item_id)
        if not item.evidence_id:
            return self._fail(item, "evidence is required before a decision")
        row = self.store.evidence_by_id(item.evidence_id)
        payload = self._payload(row) if row else {}
        decision = str(payload.get("decision", "inconclusive"))
        self._audit(item, item.current_state, item.current_state, EventType.DECISION_RECEIVED.value, "benchmark", f"Evidence decision received: {decision}", decision)
        if item.current_state is WorkItemState.EVALUATING:
            self.transition(work_item_id, WorkItemState.DECIDED, f"Evidence decision received: {decision}")
        item = self.get_work_item(work_item_id)
        if decision == "better":
            self.transition(work_item_id, WorkItemState.BETTER, "Existing benchmark proved BETTER")
            self._request_approval(self.get_work_item(work_item_id), ApprovalType.PROMOTION, "Separate promotion approval required")
            return self.get_work_item(work_item_id)
        if decision == "worse":
            return self._transition_terminal(item, WorkItemState.REJECTED, "Benchmark evidence is WORSE; production must remain unchanged")
        return self._transition_terminal(item, WorkItemState.INCONCLUSIVE, f"Benchmark decision is {decision}; collect more evidence")

    def request_promotion(self, work_item_id: str) -> PromotionRequest:
        item = self.get_work_item(work_item_id)
        if not item:
            raise KeyError(work_item_id)
        if item.current_state is not WorkItemState.AWAITING_PROMOTION_APPROVAL:
            raise PermissionError("Promotion request requires BETTER evidence and pending promotion approval")
        if not item.evidence_id:
            raise PermissionError("Evidence is required before promotion")
        promotion = self._get_promotion()
        if item.change_type is OrchestrationPath.METAMORPHOSIS:
            result = self.metamorphosis.handoff_to_promotion(item.experiment_id or "", item.evidence_id, promotion)
        else:
            version = promotion.register_candidate(item.experiment_id or "", item.evidence_id, item.candidate_version)
            result = promotion.request_promotion(version.version_id, item.evidence_id, "orchestrator")
        item.promotion_id = result.promotion_id
        item.candidate_version = result.candidate_version
        self.store.save_work_item(item)
        self._audit(self.get_work_item(work_item_id), WorkItemState.AWAITING_PROMOTION_APPROVAL, WorkItemState.AWAITING_PROMOTION_APPROVAL, EventType.PROMOTION_QUEUED.value, "orchestrator", "Eligible candidate awaits separate human promotion approval before queue admission.", "awaiting_approval")
        return result

    def promote(self, work_item_id: str) -> PromotionRecord:
        item = self.get_work_item(work_item_id)
        if not item:
            raise KeyError(work_item_id)
        if item.current_state is not WorkItemState.PROMOTION_APPROVED or not item.promotion_id:
            raise PermissionError("Explicit promotion approval is required")
        self._revalidate_version(item, allow_candidate=True)
        self.transition(work_item_id, WorkItemState.PROMOTING, "Invoke existing PromotionEngine only")
        self._audit(self.get_work_item(work_item_id), WorkItemState.PROMOTION_APPROVED, WorkItemState.PROMOTING, EventType.PROMOTION_STARTED.value, "promotion", "Started through the existing Phase 7 PromotionEngine.", "running")
        try:
            record = self._get_promotion().promote(item.promotion_id)
            item = self.get_work_item(work_item_id)
            if record.final_status.value == "active":
                self.transition(work_item_id, WorkItemState.HEALTH_CHECK, "PromotionEngine completed activation and health verification")
                self.transition(work_item_id, WorkItemState.COMPLETED, "Candidate active after authoritative health verification")
                self._audit(self.get_work_item(work_item_id), WorkItemState.HEALTH_CHECK, WorkItemState.COMPLETED, EventType.WORK_ITEM_COMPLETED.value, "promotion", "Promotion completed through Phase 7.", "active")
            elif record.final_status.value == "rolled_back":
                self.transition(work_item_id, WorkItemState.ROLLED_BACK, "PromotionEngine performed native rollback")
                self._audit(self.get_work_item(work_item_id), WorkItemState.PROMOTING, WorkItemState.ROLLED_BACK, EventType.ROLLBACK_COMPLETED.value, "promotion", "Native rollback result recorded.", "rolled_back")
            else:
                self._fail(item, f"promotion finished with status {record.final_status.value}")
            return record
        except Exception as exc:
            current = self.get_work_item(work_item_id)
            self._fail(current, f"promotion error: {exc}")
            raise

    def monitor_health(self, work_item_id: str) -> EvolutionWorkItem:
        item = self.get_work_item(work_item_id)
        if not item:
            raise KeyError(work_item_id)
        if item.promotion_id:
            record = self.store.promotion_record_by_id(item.promotion_id)
            if record:
                payload = self._payload(record)
                final_status = str(payload.get("final_status", ""))
                if final_status == "active" and item.current_state is WorkItemState.HEALTH_CHECK:
                    self.transition(work_item_id, WorkItemState.COMPLETED, "Health verification passed")
                elif final_status == "rolled_back" and item.current_state not in self.TERMINAL_STATES:
                    self.transition(work_item_id, WorkItemState.ROLLED_BACK, "Native rollback restored previous version")
        return self.get_work_item(work_item_id)

    def handle_rollback(self, work_item_id: str, reason: str = "Orchestrated rollback review") -> EvolutionWorkItem:
        item = self.get_work_item(work_item_id)
        if not item or not item.promotion_id:
            raise KeyError(work_item_id)
        promotion = self._get_promotion()
        active = promotion._active_version()
        if not active or active.version_id != item.candidate_version:
            return self._transition_terminal(item, WorkItemState.ROLLED_BACK, "Existing PromotionEngine indicates candidate is no longer active")
        promotion.rollback(item.candidate_version or "", reason, item.promotion_id)
        return self._transition_terminal(self.get_work_item(work_item_id), WorkItemState.ROLLED_BACK, reason)

    def record_outcome(self, work_item_id: str, result: str, outcome: TaskOutcome | None = None) -> EvolutionWorkItem:
        item = self.get_work_item(work_item_id)
        if not item:
            raise KeyError(work_item_id)
        item.metadata["outcome"] = result
        self.store.save_work_item(item)
        if outcome is not None:
            self.ingest_experience(outcome)
        if item.current_state not in self.TERMINAL_STATES and result in {"completed", "rejected", "failed", "rolled_back", "inconclusive"}:
            target = {"completed": WorkItemState.COMPLETED, "rejected": WorkItemState.REJECTED, "failed": WorkItemState.FAILED, "rolled_back": WorkItemState.ROLLED_BACK, "inconclusive": WorkItemState.INCONCLUSIVE}[result]
            item = self._transition_terminal(item, target, "Recorded lifecycle outcome")
        return item

    def resume(self, work_item_id: str | None = None) -> list[EvolutionWorkItem] | EvolutionWorkItem | None:
        with self._exclusive_lock():
            rows = self.store.find_work_items(limit=100) if work_item_id is None else ([self.store.work_item_by_id(work_item_id)] if self.store.work_item_by_id(work_item_id) else [])
            resumed: list[EvolutionWorkItem] = []
            for row in rows:
                if not row:
                    continue
                item = self._work_item_from_row(row)
                if item.current_state in self.TERMINAL_STATES:
                    resumed.append(item)
                    continue
                recovered = self._recover_item(item)
                resumed.append(recovered)
            if work_item_id:
                return resumed[0] if resumed else None
            return resumed

    def run_cycle(self, limit: int | None = None) -> CycleResult:
        with self._exclusive_lock():
            cycle_id = new_id("cycle")
            result = CycleResult(cycle_id, 0, 0, 0, 0, 0, 0, 0)
            self.resume()
            experiences = self.observe(limit=1000)
            result.observed_experiences = len(experiences)
            architecture_version = self.metamorphosis.get_architecture().architecture_version
            raw_opportunities = self.detector.detect(experiences, architecture_version, self.store.find_metamorphosis_experiments(limit=100), self.store.find_experiments(limit=100))
            result.detected_opportunities = len(raw_opportunities)
            max_items = min(limit or self.policy.max_work_items_per_cycle, self.policy.max_work_items_per_cycle)
            for opportunity in raw_opportunities[:max_items]:
                if self._cooldown_active(opportunity.fingerprint):
                    continue
                item = self.create_work_item(opportunity)
                if not item:
                    continue
                result.created_work_items += 1 if item.current_state is WorkItemState.DETECTED else 0
                try:
                    item = self.route_to_engine(item.work_item_id)
                    result.processed_work_items += 1
                    if item.current_state is WorkItemState.AWAITING_APPROVAL:
                        result.approvals_waiting += len(self.store.find_approval_requests(work_item_id=item.work_item_id, status="pending"))
                except Exception as exc:
                    result.failures.append(str(exc))
            authorized = [self._work_item_from_row(row) for row in self.store.find_work_items(limit=100) if row.get("current_state") == WorkItemState.APPROVED.value]
            for item in authorized[: self.policy.max_experiments_per_cycle]:
                try:
                    self.manage_experiment(item.work_item_id)
                    result.experiments_started += 1
                except Exception as exc:
                    result.failures.append(str(exc))
            pending_benchmarks = [self._work_item_from_row(row) for row in self.store.find_work_items(state=WorkItemState.BENCHMARKING.value, limit=self.policy.max_work_items_per_cycle)]
            for item in pending_benchmarks[: self.policy.max_experiments_per_cycle]:
                try:
                    self.collect_evidence(item.work_item_id)
                except Exception as exc:
                    result.failures.append(str(exc))
            result.stopped_reason = "safety_ceiling_reached" if result.experiments_started >= self.policy.max_experiments_per_cycle else "bounded_cycle_complete"
            return result

    def list_opportunities(self, limit: int = 100) -> list[EvolutionOpportunity]:
        return [self._opportunity_from_row(row) for row in self.store.find_opportunities(limit=limit)]

    def get_opportunity(self, opportunity_id: str) -> EvolutionOpportunity | None:
        row = self.store.opportunity_by_id(opportunity_id)
        return self._opportunity_from_row(row) if row else None

    def list_work_items(self, state: WorkItemState | str | None = None, limit: int = 100) -> list[EvolutionWorkItem]:
        value = state.value if isinstance(state, WorkItemState) else state
        return [self._work_item_from_row(row) for row in self.store.find_work_items(value, limit)]

    def get_work_item(self, work_item_id: str) -> EvolutionWorkItem | None:
        row = self.store.work_item_by_id(work_item_id)
        return self._work_item_from_row(row) if row else None

    def list_approval_requests(self, work_item_id: str | None = None, limit: int = 100) -> list[ApprovalRequest]:
        return [self._approval_from_row(row) for row in self.store.find_approval_requests(work_item_id=work_item_id, limit=limit)]

    def transition(self, work_item_id: str, new_state: WorkItemState, reason: str = "", actor: str = "orchestrator") -> EvolutionWorkItem:
        item = self.get_work_item(work_item_id)
        if not item:
            raise KeyError(work_item_id)
        if item.current_state in self.TERMINAL_STATES and not (item.current_state is WorkItemState.COMPLETED and new_state is WorkItemState.ROLLED_BACK):
            raise ValueError(f"Terminal work item cannot transition: {item.current_state.value}")
        if new_state not in self.TRANSITIONS.get(item.current_state, set()):
            raise ValueError(f"Invalid work-item transition {item.current_state.value} -> {new_state.value}")
        previous = item.current_state
        item.current_state = new_state
        item.updated_at = utc_now()
        self.store.save_work_item(item)
        event_name = EventType.WORK_ITEM_COMPLETED.value if new_state is WorkItemState.COMPLETED else EventType.WORK_ITEM_FAILED.value if new_state in {WorkItemState.FAILED, WorkItemState.REJECTED} else EventType.WORK_ITEM_RESUMED.value if new_state is WorkItemState.ANALYZING else EventType.CHANGE_PATH_SELECTED.value if new_state is WorkItemState.QUEUED else "state_transition"
        self._audit(item, previous, new_state, event_name, actor, reason, new_state.value)
        return item

    def _request_approval(self, item: EvolutionWorkItem, approval_type: ApprovalType, reason: str) -> ApprovalRequest:
        existing = self.store.find_approval_requests(work_item_id=item.work_item_id, status="pending", limit=20)
        for row in existing:
            if row.get("approval_type") == approval_type.value:
                return self._approval_from_row(row)
        if approval_type is ApprovalType.PROMOTION:
            target_state = WorkItemState.AWAITING_PROMOTION_APPROVAL
        else:
            target_state = WorkItemState.AWAITING_APPROVAL
        if item.current_state in {WorkItemState.PROPOSED, WorkItemState.BETTER}:
            self.transition(item.work_item_id, target_state, reason)
            item = self.get_work_item(item.work_item_id)
        request = ApprovalRequest(new_id("approval"), item.work_item_id, approval_type, reason=reason)
        self.store.save_approval_request(request)
        self._audit(item, item.current_state, item.current_state, EventType.APPROVAL_REQUESTED.value, "orchestrator", reason, "pending")
        return request

    def _run_flexibility(self, opportunity: EvolutionOpportunity) -> Any:
        if self.flexibility_handler:
            return self.flexibility_handler(opportunity)
        return {"delegated": True, "path": OrchestrationPath.FLEXIBILITY.value, "reason": "No autonomous task execution was requested; runtime Flexibility remains authoritative."}

    def _evidence_for_opportunity(self, opportunity: EvolutionOpportunity) -> list[dict[str, Any]]:
        evidence: list[dict[str, Any]] = []
        for experience_id in opportunity.source_experience_ids:
            row = self.store.experience_by_id(experience_id)
            if not row:
                continue
            payload = self._payload(row)
            evidence.append({"experience_id": experience_id, "evaluation_id": payload.get("evaluation_id"), "task_type": payload.get("task_type"), "strategy": payload.get("selected_strategy"), "outcome": payload.get("final_outcome"), "success_score": (payload.get("evaluation_result") or {}).get("success_score"), "agent_version": payload.get("agent_version"), "evidence_type": "orchestrated_opportunity"})
        return evidence

    def _revalidate_version(self, item: EvolutionWorkItem, allow_candidate: bool = False) -> None:
        current, architecture = self._current_versions()
        if current != item.current_version and not (allow_candidate and current == item.candidate_version):
            message = f"revalidation required: work item targets {item.current_version}, active version is {current}"
            self._block_for_revalidation(item, message)
            raise RuntimeError(message)
        if architecture != item.architecture_version:
            message = f"revalidation required: architecture changed from {item.architecture_version} to {architecture}"
            self._block_for_revalidation(item, message)
            raise RuntimeError(message)

    def _block_for_revalidation(self, item: EvolutionWorkItem, reason: str) -> None:
        item.last_error = reason
        self.store.save_work_item(item)
        if item.current_state in {WorkItemState.APPROVED, WorkItemState.PROMOTION_APPROVED}:
            self.transition(item.work_item_id, WorkItemState.BLOCKED, reason)

    def _current_versions(self) -> tuple[str, str]:
        current = "v0"
        if self.promotion_engine:
            active = self.promotion_engine._active_version()
            if active:
                current = active.version_id
        architecture = self.metamorphosis.get_architecture().architecture_version
        return current, architecture

    def _get_promotion(self) -> PromotionEngine:
        if self.promotion_engine is None:
            self.promotion_engine = PromotionEngine(self.store, self.source_root)
        return self.promotion_engine

    def _sandbox(self):
        if self.sandbox_engine is not None:
            return self.sandbox_engine
        from .sandbox import SandboxEngine
        self.sandbox_engine = SandboxEngine(self.store, self.source_root, self.source_root.parent / ".evo-sandboxes-orchestrator")
        return self.sandbox_engine

    def _cooldown_active(self, opportunity_key: str) -> bool:
        row = self.store.cooldown_by_key(opportunity_key)
        if not row or not row.get("cooldown_until"):
            return False
        try:
            return datetime.fromisoformat(row["cooldown_until"]) > datetime.now(timezone.utc)
        except ValueError:
            return True

    def _set_cooldown(self, item: EvolutionWorkItem, result: str) -> None:
        opportunity = self.get_opportunity(item.opportunity_id)
        key = opportunity.fingerprint if opportunity else item.opportunity_id
        previous = self.store.cooldown_by_key(key)
        attempts = int(previous.get("attempt_count", 0)) + 1 if previous else item.attempt_count
        until = datetime.now(timezone.utc) + timedelta(seconds=self.policy.cooldown_seconds)
        cooldown = CooldownRecord(key, item.opportunity_id, attempts, utc_now(), result, until.isoformat(), {"work_item_id": item.work_item_id})
        self.store.save_cooldown(cooldown)
        item.cooldown_until = cooldown.cooldown_until
        self.store.save_work_item(item)

    def _recover_item(self, item: EvolutionWorkItem) -> EvolutionWorkItem:
        if item.current_state is WorkItemState.SANDBOXING:
            if item.experiment_id:
                experiment = self.store.experiment_by_id(item.experiment_id)
                if experiment and experiment.get("status") == "passed":
                    return self.transition(item.work_item_id, WorkItemState.BENCHMARKING, "Resumed from persisted passed sandbox")
            return self._fail(item, "interrupted sandbox requires review; no safe completed experiment found")
        if item.current_state in {WorkItemState.BENCHMARKING, WorkItemState.EVALUATING}:
            if item.evidence_id and self.store.evidence_by_id(item.evidence_id):
                return self.process_decision(item.work_item_id)
            return self._transition_terminal(item, WorkItemState.INCONCLUSIVE, "Interrupted benchmark has no persisted evidence; no automatic retry")
        if item.current_state in {WorkItemState.PROMOTING, WorkItemState.HEALTH_CHECK}:
            if item.promotion_id:
                record = self.store.promotion_record_by_id(item.promotion_id)
                if record:
                    payload = self._payload(record)
                    if payload.get("final_status") == "active":
                        self.transition(item.work_item_id, WorkItemState.COMPLETED, "Recovered actual active promotion state")
                    elif payload.get("final_status") == "rolled_back":
                        self.transition(item.work_item_id, WorkItemState.ROLLED_BACK, "Recovered native rollback state")
                    else:
                        return self._fail(item, "interrupted promotion requires authoritative review")
            return self.get_work_item(item.work_item_id)
        if item.current_state is WorkItemState.AWAITING_APPROVAL or item.current_state is WorkItemState.AWAITING_PROMOTION_APPROVAL:
            return item
        if (datetime.now(timezone.utc) - datetime.fromisoformat(item.updated_at)).total_seconds() > self.policy.stale_after_seconds:
            return self._fail(item, "stale work item requires review; dangerous operations were not retried")
        return item

    def _is_protected(self, opportunity: EvolutionOpportunity) -> bool:
        text = f"{opportunity.problem} {' '.join(opportunity.affected_components)} {' '.join(opportunity.affected_capabilities)}".lower()
        return any(term in text for term in self.PROTECTED_TERMS)

    def _existing_work_item_for_opportunity(self, opportunity_id: str) -> EvolutionWorkItem | None:
        for row in self.store.find_work_items(limit=1000):
            if row.get("opportunity_id") == opportunity_id:
                return self._work_item_from_row(row)
        return None

    def _transition_terminal(self, item: EvolutionWorkItem, state: WorkItemState, reason: str) -> EvolutionWorkItem:
        item.last_error = reason if state in {WorkItemState.REJECTED, WorkItemState.FAILED, WorkItemState.INCONCLUSIVE, WorkItemState.ROLLED_BACK, WorkItemState.BLOCKED} else item.last_error
        self.store.save_work_item(item)
        if item.current_state is state:
            return item
        if state not in self.TRANSITIONS.get(item.current_state, set()):
            if item.current_state in self.TERMINAL_STATES and not (item.current_state is WorkItemState.COMPLETED and state is WorkItemState.ROLLED_BACK):
                return item
            raise ValueError(f"Invalid terminal transition {item.current_state.value} -> {state.value}")
        item = self.transition(item.work_item_id, state, reason)
        if state in {WorkItemState.REJECTED, WorkItemState.FAILED, WorkItemState.INCONCLUSIVE, WorkItemState.ROLLED_BACK}:
            self._set_cooldown(item, state.value)
        opportunity = self.get_opportunity(item.opportunity_id)
        if opportunity:
            opportunity.status = OpportunityStatus.COMPLETED if state in self.TERMINAL_STATES else opportunity.status
            opportunity.updated_at = utc_now()
            self.store.save_opportunity(opportunity)
        return item

    def _fail(self, item: EvolutionWorkItem, reason: str) -> EvolutionWorkItem:
        item.last_error = reason
        self.store.save_work_item(item)
        if item.current_state in self.TERMINAL_STATES:
            return item
        if WorkItemState.FAILED not in self.TRANSITIONS.get(item.current_state, set()):
            return self._transition_terminal(item, WorkItemState.INCONCLUSIVE, reason)
        return self._transition_terminal(item, WorkItemState.FAILED, reason)

    def _audit(self, item: EvolutionWorkItem, previous: WorkItemState | None, current: WorkItemState, event_name: str, actor: str, reason: str, result: str) -> None:
        event = OrchestrationAuditEvent(new_id("orchestration"), item.work_item_id, item.opportunity_id, event_name, previous.value if previous else None, current.value, item.change_type.value, item.target_component, item.current_version, actor, reason, result, metadata={"candidate_version": item.candidate_version, "proposal_id": item.proposal_id, "experiment_id": item.experiment_id, "evidence_id": item.evidence_id, "promotion_id": item.promotion_id})
        self.store.save_orchestration_event(event)

    @staticmethod
    def _payload(row: dict[str, Any] | None) -> dict[str, Any]:
        if not row:
            return {}
        payload = row.get("payload", row)
        return json.loads(payload) if isinstance(payload, str) else dict(payload)

    @staticmethod
    def _opportunity_from_row(row: dict[str, Any]) -> EvolutionOpportunity:
        data = json.loads(row["payload"]) if isinstance(row.get("payload"), str) else dict(row)
        data["recommended_change_type"] = OrchestrationPath(data["recommended_change_type"])
        data["status"] = OpportunityStatus(data["status"])
        data.setdefault("fingerprint", row.get("fingerprint", ""))
        data.setdefault("updated_at", row.get("updated_at", data.get("created_at", utc_now())))
        return EvolutionOpportunity(**data)

    @staticmethod
    def _work_item_from_row(row: dict[str, Any]) -> EvolutionWorkItem:
        data = json.loads(row["payload"]) if isinstance(row.get("payload"), str) else dict(row)
        data["change_type"] = OrchestrationPath(data["change_type"])
        data["current_state"] = WorkItemState(data["current_state"])
        return EvolutionWorkItem(**data)

    @staticmethod
    def _approval_from_row(row: dict[str, Any]) -> ApprovalRequest:
        data = json.loads(row["payload"]) if isinstance(row.get("payload"), str) else dict(row)
        data["approval_type"] = ApprovalType(data["approval_type"])
        return ApprovalRequest(**data)
