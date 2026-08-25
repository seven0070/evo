from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
import hashlib
import json
import os
import platform
import re
import sys
from pathlib import Path
from typing import Any, Iterable

from .storage import SQLiteStore
from .version import __version__


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


class MemoryType(str, Enum):
    WORKING = "working"
    EPISODIC = "episodic"
    SEMANTIC = "semantic"
    PROCEDURAL = "procedural"
    USER = "user"


class ProvenanceSource(str, Enum):
    USER_INPUT = "user_input"
    TASK_RESULT = "task_result"
    OBSERVATION = "observation"
    EXPERIENCE = "experience"
    EVALUATION = "evaluation"
    DOCUMENT = "document"
    TOOL_RESULT = "tool_result"
    SYSTEM_GENERATED = "system_generated"
    INFERENCE = "inference"


class ConfidenceLevel(str, Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    UNKNOWN = "unknown"


class MemoryStatus(str, Enum):
    ACTIVE = "active"
    ARCHIVED = "archived"
    EXPIRED = "expired"
    SUPERSEDED = "superseded"
    CONFLICT = "conflict"


class KnowledgeKind(str, Enum):
    OBSERVED_FACT = "observed_fact"
    INFERENCE = "inference"
    GENERALIZATION = "generalization"


class MemoryFeedback(str, Enum):
    RETRIEVED = "retrieved"
    USED = "used"
    NOT_USED = "not_used"
    HELPFUL = "helpful"
    HARMFUL = "harmful"


@dataclass
class EnvironmentSnapshot:
    os_name: str = field(default_factory=platform.system)
    runtime: str = field(default_factory=lambda: platform.python_version())
    tool_versions: dict[str, str] = field(default_factory=dict)
    model_identifier: str = "unknown"
    workspace_type: str = "local"
    configuration: dict[str, Any] = field(default_factory=dict)
    capability_versions: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Provenance:
    source: ProvenanceSource
    source_id: str
    chain: list[str] = field(default_factory=list)
    captured_at: str = field(default_factory=now)
    actor: str = "system"
    validated: bool = False
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["source"] = self.source.value
        return data


@dataclass
class MemoryRecord:
    memory_id: str
    type: MemoryType
    content: str
    summary: str
    source: ProvenanceSource
    source_id: str
    provenance: Provenance
    confidence: ConfidenceLevel = ConfidenceLevel.UNKNOWN
    confidence_score: float = 0.0
    importance: float = 0.5
    relevance: float = 0.0
    created_at: str = field(default_factory=now)
    updated_at: str = field(default_factory=now)
    last_accessed_at: str | None = None
    access_count: int = 0
    version: int = 1
    memory_version: str = "memory-v1"
    status: MemoryStatus = MemoryStatus.ACTIVE
    expiration: str | None = None
    valid_from: str | None = None
    valid_until: str | None = None
    agent_version: str = __version__
    architecture_version: str = ""
    source_version: str = ""
    environment: EnvironmentSnapshot = field(default_factory=EnvironmentSnapshot)
    knowledge_kind: KnowledgeKind | None = None
    key: str = ""
    source_ids: list[str] = field(default_factory=list)
    first_seen: str | None = None
    last_seen: str | None = None
    occurrence_count: int = 1
    metadata: dict[str, Any] = field(default_factory=dict)
    executable: bool = False

    def __post_init__(self) -> None:
        if not self.first_seen:
            self.first_seen = self.created_at
        if not self.last_seen:
            self.last_seen = self.updated_at
        if not self.source_ids:
            self.source_ids = [self.source_id]
        if self.type is MemoryType.SEMANTIC and self.knowledge_kind is None:
            self.knowledge_kind = KnowledgeKind.INFERENCE
        if self.type is MemoryType.USER:
            self.executable = False

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["type"] = self.type.value
        data["source"] = self.source.value
        data["provenance"] = self.provenance.to_dict()
        data["confidence"] = self.confidence.value
        data["status"] = self.status.value
        data["knowledge_kind"] = self.knowledge_kind.value if self.knowledge_kind else None
        data["environment"] = self.environment.to_dict()
        data["executable"] = False
        return data


@dataclass
class ProcedureRecord:
    procedure_id: str
    task_type: str
    name: str
    steps: list[str]
    required_capabilities: list[str]
    required_tools: list[str]
    constraints: list[str]
    success_history: int
    failure_history: int
    confidence: ConfidenceLevel
    confidence_score: float
    source_experiences: list[str]
    version: int = 1
    agent_version: str = __version__
    architecture_version: str = ""
    environment: EnvironmentSnapshot = field(default_factory=EnvironmentSnapshot)
    status: MemoryStatus = MemoryStatus.ACTIVE
    created_at: str = field(default_factory=now)
    updated_at: str = field(default_factory=now)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["confidence"] = self.confidence.value
        data["status"] = self.status.value
        data["environment"] = self.environment.to_dict()
        return data


@dataclass
class RetrievalQuery:
    goal: str = ""
    task_type: str = ""
    subtask: str = ""
    failure: str = ""
    tool: str = ""
    capability: str = ""
    strategy: str = ""
    environment: EnvironmentSnapshot | None = None
    agent_version: str = __version__
    architecture_version: str = ""
    include_types: list[MemoryType] | None = None
    max_memories: int = 10
    max_memory_bytes: int = 12000
    max_retrieval_time_ms: int = 250
    min_confidence: float = 0.0

    def text(self) -> str:
        return " ".join(item for item in (self.goal, self.task_type, self.subtask, self.failure, self.tool, self.capability, self.strategy) if item).lower()


@dataclass
class RetrievedMemory:
    memory: MemoryRecord
    score: float
    score_breakdown: dict[str, float]
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {"memory": self.memory.to_dict(), "score": self.score, "score_breakdown": self.score_breakdown, "warnings": self.warnings}


@dataclass
class CognitiveMemoryContext:
    working_memory: list[MemoryRecord]
    relevant_episodic_memory: list[RetrievedMemory]
    relevant_semantic_memory: list[RetrievedMemory]
    relevant_procedures: list[ProcedureRecord]
    conflicts: list[MemoryRecord]
    memory_warnings: list[str]
    max_context_contribution: int = 12000

    def to_dict(self) -> dict[str, Any]:
        return {"working_memory": [item.to_dict() for item in self.working_memory], "relevant_episodic_memory": [item.to_dict() for item in self.relevant_episodic_memory], "relevant_semantic_memory": [item.to_dict() for item in self.relevant_semantic_memory], "relevant_procedures": [item.to_dict() for item in self.relevant_procedures], "conflicts": [item.to_dict() for item in self.conflicts], "memory_warnings": self.memory_warnings}


@dataclass
class MemoryIntegrityReport:
    valid: bool
    errors: list[str]
    checked_records: int
    schema_valid: bool

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class WorkingMemory:
    def __init__(self, max_items: int = 32, max_bytes: int = 12000):
        self.max_items = max_items
        self.max_bytes = max_bytes
        self._items: list[MemoryRecord] = []
        self.task_id: str | None = None

    def begin(self, task_id: str) -> None:
        self.task_id = task_id
        self._items = []

    def add(self, record: MemoryRecord, critical: bool = False) -> None:
        record.type = MemoryType.WORKING
        record.metadata["critical"] = critical
        record.metadata["task_id"] = self.task_id
        self._items.append(record)
        self._trim()

    def items(self) -> list[MemoryRecord]:
        return list(self._items)

    def end(self) -> list[MemoryRecord]:
        retained = [item for item in self._items if item.metadata.get("critical")]
        self._items = []
        self.task_id = None
        return retained

    def _trim(self) -> None:
        while len(self._items) > self.max_items or len(json.dumps([item.to_dict() for item in self._items])) > self.max_bytes:
            candidates = [item for item in self._items if not item.metadata.get("critical")]
            if not candidates:
                break
            victim = min(candidates, key=lambda item: (item.importance, item.confidence_score, item.last_accessed_at or item.created_at))
            self._items.remove(victim)


class ProvenanceManager:
    def validate(self, record: MemoryRecord) -> list[str]:
        errors: list[str] = []
        if not record.source_id:
            errors.append("source_id is required")
        if record.provenance.source_id != record.source_id:
            errors.append("provenance source_id does not match record source_id")
        if record.type is not MemoryType.WORKING and not record.provenance.chain and record.source is ProvenanceSource.INFERENCE:
            errors.append("derived memory requires a provenance chain")
        if record.type is MemoryType.SEMANTIC and record.knowledge_kind is None:
            errors.append("semantic memory requires knowledge kind")
        return errors

    def chain(self, source_records: Iterable[MemoryRecord]) -> list[str]:
        return [record.memory_id for record in source_records]


class MemoryStore:
    def __init__(self, store: SQLiteStore):
        self.sqlite_store = store

    def save(self, record: MemoryRecord, relation: str | None = None, parent_id: str | None = None) -> None:
        self.sqlite_store.save_memory(record)
        if relation and parent_id:
            self.sqlite_store.save_memory_link(record.memory_id, parent_id, relation)

    def get(self, memory_id: str) -> MemoryRecord | None:
        row = self.sqlite_store.memory_by_id(memory_id)
        if not row:
            return None
        try:
            return _memory_from_row(row)
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            return None

    def list(self, memory_type: MemoryType | None = None, status: MemoryStatus | None = None, limit: int = 100) -> list[MemoryRecord]:
        rows = self.sqlite_store.find_memories(memory_type.value if memory_type else None, status.value if status else None, limit)
        records: list[MemoryRecord] = []
        for row in rows:
            try:
                records.append(_memory_from_row(row))
            except (KeyError, TypeError, ValueError, json.JSONDecodeError):
                continue
        return records

    def history(self, memory_id: str) -> list[MemoryRecord]:
        rows = self.sqlite_store.memory_history(memory_id)
        for link in self.sqlite_store.memory_links(memory_id):
            parent = self.sqlite_store.memory_by_id(link["parent_id"])
            if parent:
                rows.append(parent)
        records: list[MemoryRecord] = []
        for row in rows:
            try:
                records.append(_memory_from_row(row))
            except (KeyError, TypeError, ValueError, json.JSONDecodeError):
                continue
        return records

    def links(self, memory_id: str) -> list[dict[str, Any]]:
        return self.sqlite_store.memory_links(memory_id)

    def conflicts(self, key: str | None = None) -> list[MemoryRecord]:
        rows = self.sqlite_store.find_memory_conflicts(key, 100)
        return [_memory_from_row(row) for row in rows]


class RetrievalEngine:
    def __init__(self, memory_store: MemoryStore, default_max_memories: int = 10, default_max_bytes: int = 12000):
        self.memory_store = memory_store
        self.default_max_memories = default_max_memories
        self.default_max_bytes = default_max_bytes

    def retrieve(self, query: RetrievalQuery) -> list[RetrievedMemory]:
        started = datetime.now(timezone.utc)
        candidates = self.memory_store.list(limit=max(100, query.max_memories * 10))
        results: list[RetrievedMemory] = []
        query_tokens = _tokens(query.text())
        for memory in candidates:
            if len(results) >= query.max_memories * 4:
                break
            warnings = self._filter_warnings(memory, query)
            if memory.status not in {MemoryStatus.ACTIVE, MemoryStatus.CONFLICT} or warnings and "expired" in warnings:
                continue
            if query.include_types and memory.type not in query.include_types:
                continue
            if memory.confidence_score < query.min_confidence:
                continue
            breakdown = self._score(memory, query, query_tokens)
            score = round(sum(breakdown.values()), 6)
            if score <= 0:
                continue
            results.append(RetrievedMemory(memory, score, breakdown, warnings))
            elapsed = (datetime.now(timezone.utc) - started).total_seconds() * 1000
            if elapsed > query.max_retrieval_time_ms:
                break
        results.sort(key=lambda item: (-item.score, item.memory.created_at, item.memory.memory_id))
        bounded: list[RetrievedMemory] = []
        total = 0
        for item in results:
            size = len(json.dumps(item.to_dict()))
            if size > query.max_memory_bytes:
                continue
            if total + size > query.max_memory_bytes:
                break
            item.memory.last_accessed_at = now()
            item.memory.access_count += 1
            item.memory.relevance = item.score
            self.memory_store.sqlite_store.save_memory(item.memory)
            bounded.append(item)
            total += size
            if len(bounded) >= query.max_memories:
                break
        return bounded

    def _score(self, memory: MemoryRecord, query: RetrievalQuery, query_tokens: set[str]) -> dict[str, float]:
        memory_tokens = _tokens(f"{memory.content} {memory.summary} {memory.key} {memory.metadata}")
        overlap = len(query_tokens & memory_tokens) / max(1, len(query_tokens))
        task = 0.2 if query.task_type and query.task_type.lower() in str(memory.metadata).lower() else 0.0
        strategy = 0.15 if query.strategy and query.strategy.lower() in str(memory.metadata).lower() else 0.0
        tool = 0.15 if query.tool and query.tool.lower() in str(memory.metadata).lower() else 0.0
        capability = 0.15 if query.capability and query.capability.lower() in str(memory.metadata).lower() else 0.0
        environment = self._environment_score(memory, query.environment)
        version = 0.15 if not query.architecture_version or not memory.architecture_version or memory.architecture_version == query.architecture_version else -0.2
        recency = self._recency(memory)
        importance = max(0.0, min(0.2, memory.importance * 0.2))
        confidence = max(0.0, min(0.2, memory.confidence_score * 0.2))
        conflict_penalty = -0.15 if memory.status is MemoryStatus.CONFLICT else 0.0
        return {"topic_relevance": round(overlap * 0.45, 6), "task_similarity": task, "strategy_similarity": strategy, "tool_similarity": tool, "capability_similarity": capability, "environment_match": environment, "version_compatibility": version, "recency": recency, "importance": importance, "confidence": confidence, "conflict_penalty": conflict_penalty}

    @staticmethod
    def _recency(memory: MemoryRecord) -> float:
        try:
            age_days = max(0.0, (datetime.now(timezone.utc) - datetime.fromisoformat(memory.updated_at)).total_seconds() / 86400)
            return max(0.0, 0.1 * (1.0 - min(age_days / 30.0, 1.0)))
        except ValueError:
            return 0.0

    @staticmethod
    def _environment_score(memory: MemoryRecord, environment: EnvironmentSnapshot | None) -> float:
        if environment is None:
            return 0.05
        return 0.1 if memory.environment.os_name == environment.os_name and memory.environment.runtime == environment.runtime else -0.1

    @staticmethod
    def _filter_warnings(memory: MemoryRecord, query: RetrievalQuery) -> list[str]:
        warnings: list[str] = []
        if memory.expiration and memory.expiration <= now():
            warnings.append("expired")
        if memory.confidence is ConfidenceLevel.UNKNOWN:
            warnings.append("confidence unknown")
        if memory.status is MemoryStatus.CONFLICT:
            warnings.append("contradictory memory; current authority wins")
        if memory.architecture_version and query.architecture_version and memory.architecture_version != query.architecture_version:
            warnings.append("architecture-version mismatch; historical evidence only")
        if memory.environment.os_name != platform.system():
            warnings.append("environment mismatch")
        if _looks_like_injection(memory.content):
            warnings.append("untrusted content; never executable policy")
        return warnings


class ConsolidationEngine:
    def __init__(self, manager: MemoryManager | None = None, minimum_evidence: int = 3):
        self.manager = manager
        self.minimum_evidence = minimum_evidence

    def consolidate(self, records: list[MemoryRecord]) -> list[MemoryRecord]:
        groups: dict[str, list[MemoryRecord]] = {}
        for record in records:
            key = record.key or _fingerprint_text(record.content)
            groups.setdefault(key, []).append(record)
        created: list[MemoryRecord] = []
        for key, group in groups.items():
            if len(group) < self.minimum_evidence:
                continue
            successes = sum(1 for item in group if item.metadata.get("outcome") in {"success", "succeeded"})
            failures = sum(1 for item in group if item.metadata.get("outcome") in {"failure", "failed", "partial", "blocked"})
            if successes >= self.minimum_evidence:
                kind = KnowledgeKind.GENERALIZATION
                statement = f"Observed successful pattern across {successes} experiences: {group[0].summary}."
            elif failures >= self.minimum_evidence:
                kind = KnowledgeKind.INFERENCE
                statement = f"Observed recurring failure pattern across {failures} experiences: {group[0].summary}."
            else:
                continue
            source_ids = [item.memory_id for item in group]
            provenance = Provenance(ProvenanceSource.INFERENCE, source_ids[0], source_ids, actor="consolidator", validated=False, note="Derived conservatively from repeated evidence")
            record = MemoryRecord(new_memory_id(), MemoryType.SEMANTIC, statement, statement, ProvenanceSource.INFERENCE, source_ids[0], provenance, ConfidenceLevel.MEDIUM if successes >= self.minimum_evidence else ConfidenceLevel.LOW, min(0.9, 0.45 + len(group) * 0.1), 0.75, 0.0, architecture_version=group[0].architecture_version, source_version=group[0].source_version, environment=group[0].environment, knowledge_kind=kind, key=key, source_ids=source_ids, metadata={"evidence_count": len(group), "successes": successes, "failures": failures, "untrusted_as_policy": True})
            created.append(record)
            if self.manager:
                self.manager.store(record)
        return created


class ForgettingEngine:
    def __init__(self, memory_store: MemoryStore):
        self.memory_store = memory_store

    def expire(self, at: str | None = None) -> list[str]:
        at = at or now()
        changed: list[str] = []
        for record in self.memory_store.list(limit=10000):
            if record.expiration and record.expiration <= at and record.status is MemoryStatus.ACTIVE:
                record.status = MemoryStatus.EXPIRED
                record.updated_at = at
                self.memory_store.sqlite_store.save_memory(record)
                changed.append(record.memory_id)
        return changed

    def archive(self, memory_id: str) -> MemoryRecord:
        record = self._required(memory_id)
        if record.type is MemoryType.USER:
            raise PermissionError("User memory requires an explicit user archive action")
        record.status = MemoryStatus.ARCHIVED
        record.updated_at = now()
        self.memory_store.sqlite_store.save_memory(record)
        return record

    def restore(self, memory_id: str) -> MemoryRecord:
        record = self._required(memory_id)
        if record.status not in {MemoryStatus.ARCHIVED, MemoryStatus.EXPIRED}:
            return record
        record.status = MemoryStatus.ACTIVE
        record.updated_at = now()
        self.memory_store.sqlite_store.save_memory(record)
        return record

    def _required(self, memory_id: str) -> MemoryRecord:
        record = self.memory_store.get(memory_id)
        if not record:
            raise KeyError(memory_id)
        return record


class MemoryEvaluator:
    def evaluate(self, record: MemoryRecord, environment: EnvironmentSnapshot | None = None) -> dict[str, Any]:
        provenance = ProvenanceManager().validate(record)
        consistency = not bool(record.status is MemoryStatus.CONFLICT)
        environment_match = environment is None or record.environment.os_name == environment.os_name
        freshness = self._freshness(record)
        return {"accuracy": record.confidence_score, "provenance_completeness": 1.0 if not provenance else 0.0, "relevance": record.relevance, "confidence": record.confidence_score, "freshness": freshness, "consistency": 1.0 if consistency else 0.0, "environment_compatibility": 1.0 if environment_match else 0.0, "errors": provenance}

    @staticmethod
    def _freshness(record: MemoryRecord) -> float:
        try:
            age = max(0, (datetime.now(timezone.utc) - datetime.fromisoformat(record.updated_at)).total_seconds())
            return max(0.0, 1.0 - age / (90 * 86400))
        except ValueError:
            return 0.0


class MemoryManager:
    """The sole Phase 11 memory facade. Memory remains data, never executable authority."""

    def __init__(self, store: SQLiteStore, workspace: Path | None = None, environment: EnvironmentSnapshot | None = None, max_working_items: int = 32, max_working_bytes: int = 12000, max_memories: int = 10, max_memory_bytes: int = 12000):
        self.sqlite_store = store
        self.memory_store = MemoryStore(store)
        self.workspace = Path(workspace).expanduser().resolve() if workspace else None
        self.environment = environment or EnvironmentSnapshot(workspace_type="local")
        self.working = WorkingMemory(max_working_items, max_working_bytes)
        self.retrieval = RetrievalEngine(self.memory_store, max_memories, max_memory_bytes)
        self.provenance = ProvenanceManager()
        self.consolidator = ConsolidationEngine(self)
        self.forgetting = ForgettingEngine(self.memory_store)
        self.evaluator = MemoryEvaluator()

    def begin_task(self, task_id: str, goal: str, constraints: list[str] | None = None, plan: dict[str, Any] | None = None) -> None:
        self.working.begin(task_id)
        self.add_working(goal, "Current goal", importance=1.0, critical=True, source_id=task_id)
        if constraints:
            self.add_working("; ".join(constraints), "Current authoritative constraints", importance=1.0, critical=True, source_id=task_id)
        if plan:
            self.add_working(json.dumps(plan), "Current plan", importance=0.9, critical=True, source_id=task_id)

    def add_working(self, content: str, summary: str, importance: float = 0.5, critical: bool = False, source_id: str = "working") -> MemoryRecord:
        record = self._record(MemoryType.WORKING, content, summary, ProvenanceSource.SYSTEM_GENERATED, source_id, ConfidenceLevel.HIGH, 0.9, importance, expiration=(datetime.now(timezone.utc) + timedelta(hours=2)).isoformat(), metadata={"critical": critical})
        self.working.add(record, critical)
        return record

    def end_task(self) -> list[MemoryRecord]:
        return self.working.end()

    def store(self, record: MemoryRecord, relation: str | None = None, parent_id: str | None = None) -> MemoryRecord:
        errors = self.provenance.validate(record)
        if errors:
            raise ValueError("Invalid memory provenance: " + "; ".join(errors))
        if record.key:
            for prior in self.memory_store.list(limit=10000):
                if prior.key == record.key and prior.status is MemoryStatus.ACTIVE and _normalize(prior.content) != _normalize(record.content):
                    prior.status = MemoryStatus.CONFLICT
                    prior.updated_at = now()
                    prior.metadata["conflict_with"] = record.memory_id
                    self.memory_store.save(prior)
                    record.status = MemoryStatus.CONFLICT
                    record.metadata["conflict_with"] = prior.memory_id
        if _looks_like_injection(record.content):
            record.metadata["untrusted_content"] = True
            record.metadata["untrusted_as_policy"] = True
            record.executable = False
        duplicate = self.store_dedup(record)
        if duplicate:
            return duplicate
        self.memory_store.save(record, relation, parent_id)
        return record

    def store_dedup(self, record: MemoryRecord) -> MemoryRecord | None:
        fingerprint = self.fingerprint(record)
        existing = self.sqlite_store.memory_by_fingerprint(fingerprint)
        if not existing:
            record.metadata["fingerprint"] = fingerprint
            return None
        prior = _memory_from_row(existing)
        prior.occurrence_count += 1
        prior.last_seen = now()
        prior.updated_at = now()
        prior.source_ids = sorted(set(prior.source_ids + [record.source_id] + record.provenance.chain))
        prior.metadata["duplicate_count"] = prior.occurrence_count - 1
        self.memory_store.save(prior)
        self.sqlite_store.save_memory_event(prior.memory_id, "deduplicated", {"source_id": record.source_id, "occurrence_count": prior.occurrence_count})
        return prior

    def update(self, memory_id: str, content: str, reason: str, source_id: str, source: ProvenanceSource = ProvenanceSource.OBSERVATION, confidence: ConfidenceLevel = ConfidenceLevel.UNKNOWN) -> MemoryRecord:
        prior = self.get(memory_id)
        if not prior:
            raise KeyError(memory_id)
        prior.status = MemoryStatus.SUPERSEDED
        prior.updated_at = now()
        self.memory_store.save(prior)
        updated = self._record(prior.type, content, _summary(content), source, source_id, confidence, prior.confidence_score, prior.importance, key=prior.key, architecture_version=prior.architecture_version, source_version=prior.source_version, metadata={"update_reason": reason, "supersedes": memory_id}, provenance_chain=[memory_id] + prior.provenance.chain)
        updated.version = prior.version + 1
        self.store(updated, "supersedes", memory_id)
        return updated

    def retrieve(self, query: RetrievalQuery | str, **kwargs: Any) -> list[RetrievedMemory]:
        if isinstance(query, str):
            query = RetrievalQuery(goal=query, **kwargs)
        return self.retrieval.retrieve(query)

    def cognitive_context(self, query: RetrievalQuery, working: bool = True) -> CognitiveMemoryContext:
        retrieved = self.retrieve(query)
        episodic = [item for item in retrieved if item.memory.type is MemoryType.EPISODIC]
        semantic = [item for item in retrieved if item.memory.type is MemoryType.SEMANTIC]
        procedures = [self.procedure_from_memory(item.memory) for item in retrieved if item.memory.type is MemoryType.PROCEDURAL]
        procedures = [item for item in procedures if item]
        conflicts = [item.memory for item in retrieved if item.memory.status is MemoryStatus.CONFLICT]
        warnings = [warning for item in retrieved for warning in item.warnings]
        return CognitiveMemoryContext(self.working.items() if working else [], episodic, semantic, procedures, conflicts, sorted(set(warnings)))

    def capture_experience(self, experience: Any) -> MemoryRecord:
        payload = experience.to_dict() if hasattr(experience, "to_dict") else dict(experience)
        outcome = payload.get("final_outcome", payload.get("outcome", "unknown"))
        task_type = payload.get("task_type", "general")
        original_goal = payload.get("original_goal", payload.get("goal", ""))
        summary = f"{task_type} ended with {outcome} for goal: {original_goal}."
        record = self._record(MemoryType.EPISODIC, summary, summary, ProvenanceSource.EXPERIENCE, str(payload.get("experience_id", payload.get("task_id", new_memory_id()))), ConfidenceLevel.HIGH if outcome == "success" else ConfidenceLevel.MEDIUM, 0.9 if outcome == "success" else 0.7, 0.8 if outcome in {"failure", "blocked", "partial_success"} else 0.65, architecture_version=payload.get("architecture_version", ""), metadata={"outcome": str(outcome), "task_type": task_type, "goal": original_goal, "strategy": payload.get("selected_strategy"), "tools": payload.get("selected_tools", []), "failures": payload.get("failures", []), "recovery": payload.get("recovery_attempts", []), "verification": payload.get("verification_result", {}), "capability_selection": payload.get("capability_selection", []), "experience_id": payload.get("experience_id")})
        return self.store(record)

    def capture_evaluation(self, evaluation: Any) -> MemoryRecord:
        payload = evaluation.to_dict() if hasattr(evaluation, "to_dict") else dict(evaluation)
        content = f"Evaluation score {payload.get('score', payload.get('overall_score', 'unknown'))}; outcome {payload.get('outcome', 'unknown')}."
        return self.store(self._record(MemoryType.EPISODIC, content, content, ProvenanceSource.EVALUATION, str(payload.get("evaluation_id", new_memory_id())), ConfidenceLevel.MEDIUM, 0.65, 0.6, metadata={"evaluation": payload, "outcome": payload.get("outcome", "unknown")}))

    def capture_observation(self, observation: Any) -> MemoryRecord:
        payload = observation.to_dict() if hasattr(observation, "to_dict") else dict(observation)
        content = str(payload.get("output", "")) or str(payload.get("errors", ""))
        return self.store(self._record(MemoryType.EPISODIC, content[:4000], _summary(content), ProvenanceSource.OBSERVATION, str(payload.get("observation_id", new_memory_id())), ConfidenceLevel.MEDIUM, 0.6, 0.55, metadata={"task_id": payload.get("task_id"), "tool": payload.get("tool"), "status": payload.get("status")}))

    def capture_environment(self, environment: Any, task_id: str = "", goal: str = "", outcome: str = "") -> MemoryRecord:
        payload = environment.to_dict() if hasattr(environment, "to_dict") else dict(environment)
        environment_id = str(payload.get("environment_id", "unknown"))
        environment_version = str(payload.get("environment_version", "unknown"))
        summary = f"Environment {environment_id} version {environment_version} observed for {goal or task_id}."
        metadata = {"environment_id": environment_id, "environment_version": environment_version, "goal": goal, "task_id": task_id, "outcome": outcome, "resource_state": payload.get("resource_state", {}), "tool_state": [{key: item.get(key) for key in ("name", "version", "provider", "availability", "status") if key in item} for item in payload.get("available_tools", [])], "network_state": payload.get("network_state", {}), "filesystem_state": payload.get("filesystem_state", [])[:50]}
        return self.store(self._record(MemoryType.EPISODIC, summary, summary, ProvenanceSource.OBSERVATION, f"environment:{environment_id}:{environment_version}", ConfidenceLevel.MEDIUM, 0.7, 0.65, architecture_version=payload.get("architecture_version", ""), metadata=metadata))

    def capture_external(self, evidence: dict[str, Any]) -> MemoryRecord:
        """Persist bounded external-operation metadata only; external content is never trusted as policy."""
        safe = {key: value for key, value in dict(evidence).items() if key not in {"content", "body", "response", "payload"}}
        operation_id = str(safe.get("operation_id", new_memory_id()))
        integration_id = str(safe.get("integration_id", "unknown"))
        status = str(safe.get("status", "unknown"))
        summary = f"External operation {operation_id} via {integration_id} ended with {status}."
        safe["untrusted_external_evidence"] = True
        safe["executable"] = False
        return self.store(self._record(MemoryType.EPISODIC, summary, summary, ProvenanceSource.OBSERVATION, f"external:{operation_id}", ConfidenceLevel.MEDIUM, 0.6, 0.55, architecture_version=safe.get("architecture_version", ""), metadata=safe))

    def capture_specialist(self, evidence: dict[str, Any]) -> MemoryRecord:
        """Persist bounded specialist metadata only; context and outputs remain isolated and non-authoritative."""
        safe = {key: value for key, value in dict(evidence).items() if key not in {"context", "input", "output", "claim", "observations", "inference", "payload"}}
        specialist_id = str(safe.get("specialist_id", "unknown"))
        task_id = str(safe.get("specialist_task_id", new_memory_id()))
        summary = f"Specialist {specialist_id} completed task {task_id}: success={bool(safe.get('success', False))}, verified={bool(safe.get('verified', False))}."
        return self.store(self._record(MemoryType.EPISODIC, summary, summary, ProvenanceSource.OBSERVATION, f"specialist:{task_id}", ConfidenceLevel.MEDIUM, min(1.0, max(0.0, float(safe.get("quality_score", 0.5)))), 0.55, metadata=safe))

    def capture_user_memory(self, content: str, summary: str = "", key: str = "", source_id: str = "user", importance: float = 0.8, expiration: str | None = None) -> MemoryRecord:
        return self.store(self._record(MemoryType.USER, content, summary or _summary(content), ProvenanceSource.USER_INPUT, source_id, ConfidenceLevel.HIGH, 0.95, importance, expiration=expiration, key=key, metadata={"user_owned": True, "explicit_action_required": True}))

    def delete_user_memory(self, memory_id: str, actor: str = "user", reason: str = "explicit user deletion") -> MemoryRecord:
        record = self.get(memory_id)
        if not record or record.type is not MemoryType.USER:
            raise KeyError(memory_id)
        record.status = MemoryStatus.ARCHIVED
        record.updated_at = now()
        record.metadata["deleted_by"] = actor
        record.metadata["deletion_reason"] = reason
        self.memory_store.save(record)
        self.sqlite_store.save_memory_event(memory_id, "user_deleted", {"actor": actor, "reason": reason})
        return record

    def get(self, memory_id: str) -> MemoryRecord | None:
        return self.memory_store.get(memory_id)

    def list(self, memory_type: MemoryType | None = None, status: MemoryStatus | None = None, limit: int = 100) -> list[MemoryRecord]:
        return self.memory_store.list(memory_type, status, limit)

    def get_provenance(self, memory_id: str) -> dict[str, Any]:
        record = self.get(memory_id)
        if not record:
            raise KeyError(memory_id)
        return {"memory": record.to_dict(), "links": self.memory_store.links(memory_id), "history": [item.to_dict() for item in self.memory_store.history(memory_id)]}

    def get_history(self, memory_id: str) -> list[MemoryRecord]:
        return self.memory_store.history(memory_id)

    def find_conflicts(self, key: str | None = None) -> list[MemoryRecord]:
        return self.memory_store.conflicts(key)

    def consolidate(self, records: list[MemoryRecord] | None = None) -> list[MemoryRecord]:
        return self.consolidator.consolidate(records or self.list(MemoryType.EPISODIC, limit=1000))

    def create_procedure(self, task_type: str, name: str, steps: list[str], required_capabilities: list[str], required_tools: list[str], constraints: list[str], source_experiences: list[str], success_history: int = 0, failure_history: int = 0, confidence_score: float = 0.5, architecture_version: str = "") -> ProcedureRecord:
        confidence = ConfidenceLevel.HIGH if confidence_score >= 0.8 else ConfidenceLevel.MEDIUM if confidence_score >= 0.5 else ConfidenceLevel.LOW
        procedure = ProcedureRecord(new_memory_id("procedure"), task_type, name, steps, required_capabilities, required_tools, constraints, success_history, failure_history, confidence, confidence_score, source_experiences, architecture_version=architecture_version, environment=self.environment)
        self.sqlite_store.save_procedure(procedure)
        record = self._record(MemoryType.PROCEDURAL, json.dumps(procedure.to_dict(), sort_keys=True), name, ProvenanceSource.EXPERIENCE, source_experiences[0] if source_experiences else procedure.procedure_id, confidence, confidence_score, 0.8, key=procedure.name, architecture_version=architecture_version, metadata={"procedure": procedure.to_dict(), "task_type": task_type, "required_tools": required_tools, "required_capabilities": required_capabilities})
        self.store(record)
        return procedure

    def list_procedures(self, task_type: str | None = None, limit: int = 100) -> list[ProcedureRecord]:
        rows = self.sqlite_store.find_procedures(task_type, limit)
        return [_procedure_from_row(row) for row in rows]

    def retrieve_procedures(self, task_type: str, available_capabilities: Iterable[str], available_tools: Iterable[str], architecture_version: str = "", limit: int = 10) -> list[ProcedureRecord]:
        capabilities = set(available_capabilities)
        tools = set(available_tools)
        procedures = []
        for procedure in self.list_procedures(task_type, limit=1000):
            if procedure.status is not MemoryStatus.ACTIVE:
                continue
            if procedure.architecture_version and architecture_version and procedure.architecture_version != architecture_version:
                continue
            if not set(procedure.required_capabilities).issubset(capabilities):
                continue
            if not set(procedure.required_tools).issubset(tools):
                continue
            procedures.append(procedure)
        return procedures[:limit]

    def procedure_from_memory(self, record: MemoryRecord) -> ProcedureRecord | None:
        if record.type is not MemoryType.PROCEDURAL:
            return None
        payload = record.metadata.get("procedure")
        if not payload:
            return None
        return _procedure_from_payload(payload)

    def archive(self, memory_id: str) -> MemoryRecord:
        return self.forgetting.archive(memory_id)

    def restore(self, memory_id: str) -> MemoryRecord:
        return self.forgetting.restore(memory_id)

    def expire(self, at: str | None = None) -> list[str]:
        return self.forgetting.expire(at)

    def feedback(self, memory_id: str, feedback: MemoryFeedback, note: str = "") -> None:
        if not self.get(memory_id):
            raise KeyError(memory_id)
        self.sqlite_store.save_memory_feedback(memory_id, feedback.value, note)

    def statistics(self) -> dict[str, Any]:
        stats = self.sqlite_store.memory_statistics()
        stats["average_retrieval_score"] = self.sqlite_store.average_memory_score()
        stats["conflicts"] = len(self.find_conflicts())
        return stats

    def validate_integrity(self) -> MemoryIntegrityReport:
        errors: list[str] = []
        schema_valid = self.sqlite_store.memory_schema_valid()
        if not schema_valid:
            errors.append("required memory schema is missing")
        raw_rows = self.sqlite_store.find_memories(limit=10000)
        records: list[MemoryRecord] = []
        for row in raw_rows:
            try:
                records.append(_memory_from_row(row))
            except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
                errors.append(f"{row.get('memory_id', 'unknown')}: invalid memory record: {exc}")
        for record in records:
            errors.extend(f"{record.memory_id}: {error}" for error in self.provenance.validate(record))
            if not record.content or not record.memory_id:
                errors.append(f"{record.memory_id}: required content or ID is missing")
        return MemoryIntegrityReport(not errors, errors, len(raw_rows), schema_valid)

    def fingerprint(self, record: MemoryRecord) -> str:
        return hashlib.sha256(json.dumps({"type": record.type.value, "key": record.key, "content": _normalize(record.content)}, sort_keys=True).encode()).hexdigest()

    def _record(self, memory_type: MemoryType, content: str, summary: str, source: ProvenanceSource, source_id: str, confidence: ConfidenceLevel, confidence_score: float, importance: float, expiration: str | None = None, key: str = "", architecture_version: str = "", source_version: str = "", metadata: dict[str, Any] | None = None, provenance_chain: list[str] | None = None) -> MemoryRecord:
        created = now()
        provenance = Provenance(source, source_id, provenance_chain or [], created, "system", source not in {ProvenanceSource.INFERENCE}, "")
        return MemoryRecord(new_memory_id(), memory_type, content, summary, source, source_id, provenance, confidence, confidence_score, importance, 0.0, created, created, None, 0, 1, "memory-v1", MemoryStatus.ACTIVE, expiration, created, None, __version__, architecture_version, source_version, self.environment, KnowledgeKind.OBSERVED_FACT if memory_type is MemoryType.EPISODIC else None, key, [source_id], created, created, 1, metadata or {}, False)


def new_memory_id(prefix: str = "memory") -> str:
    return f"{prefix}_{hashlib.sha256(f'{prefix}:{now()}:{os.getpid()}'.encode()).hexdigest()[:12]}"


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip().lower())


def _tokens(text: str) -> set[str]:
    return {token for token in re.findall(r"[a-z0-9_]{3,}", text.lower()) if token not in {"the", "and", "for", "with", "from", "that", "this"}}


def _summary(text: str) -> str:
    compact = re.sub(r"\s+", " ", text).strip()
    return compact[:240]


def _fingerprint_text(text: str) -> str:
    return hashlib.sha256(_normalize(text).encode()).hexdigest()


def _looks_like_injection(text: str) -> bool:
    lowered = text.lower()
    return any(pattern in lowered for pattern in ("ignore all safety", "ignore previous instructions", "execute unrestricted", "disable verification", "bypass approval"))


def _payload(row: dict[str, Any]) -> dict[str, Any]:
    value = row.get("payload", row)
    return json.loads(value) if isinstance(value, str) else dict(value)


def _memory_from_row(row: dict[str, Any]) -> MemoryRecord:
    data = _payload(row)
    data["type"] = MemoryType(data["type"])
    data["source"] = ProvenanceSource(data["source"])
    provenance = data.get("provenance", {})
    provenance["source"] = ProvenanceSource(provenance.get("source", data["source"].value))
    data["provenance"] = Provenance(**provenance)
    data["confidence"] = ConfidenceLevel(data.get("confidence", ConfidenceLevel.UNKNOWN.value))
    data["status"] = MemoryStatus(data.get("status", MemoryStatus.ACTIVE.value))
    data["knowledge_kind"] = KnowledgeKind(data["knowledge_kind"]) if data.get("knowledge_kind") else None
    data["environment"] = EnvironmentSnapshot(**data.get("environment", {}))
    data.pop("executable", None)
    data["executable"] = False
    return MemoryRecord(**data)


def _procedure_from_payload(data: dict[str, Any]) -> ProcedureRecord:
    data = dict(data)
    data["confidence"] = ConfidenceLevel(data["confidence"])
    data["status"] = MemoryStatus(data["status"])
    data["environment"] = EnvironmentSnapshot(**data.get("environment", {}))
    return ProcedureRecord(**data)


def _procedure_from_row(row: dict[str, Any]) -> ProcedureRecord:
    if "payload" in row:
        return _procedure_from_payload(_payload(row))
    data = dict(row)
    for field_name in ("steps", "required_capabilities", "required_tools", "constraints", "source_experiences", "environment", "metadata"):
        data[field_name] = json.loads(data[field_name]) if isinstance(data.get(field_name), str) else data.get(field_name, [] if field_name != "environment" else {})
    return _procedure_from_payload(data)


__all__ = ["CognitiveMemoryContext", "ConfidenceLevel", "ConsolidationEngine", "EnvironmentSnapshot", "ForgettingEngine", "KnowledgeKind", "MemoryEvaluator", "MemoryFeedback", "MemoryIntegrityReport", "MemoryManager", "MemoryRecord", "MemoryStatus", "MemoryType", "ProcedureRecord", "Provenance", "ProvenanceSource", "RetrievalEngine", "RetrievalQuery", "RetrievedMemory", "WorkingMemory"]
