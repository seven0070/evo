from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable

from .experience import Experience, ExperienceEngine
from .models import Event, EventType, ProposalRisk, ProposalStatus
from .storage import SQLiteStore
from .version import __version__


@dataclass
class EvolutionProposal:
    proposal_id: str
    created_at: str
    source_experiences: list[str]
    source_evaluations: list[str]
    agent_version: str
    target_component: str
    observed_problem: str
    evidence: list[dict[str, Any]]
    proposed_change: str
    expected_benefit: str
    risks: list[str]
    affected_capabilities: list[str]
    affected_permissions: list[str]
    confidence: float
    evaluation_method: str
    rollback_plan: str
    status: ProposalStatus = ProposalStatus.GENERATED
    risk: ProposalRisk = ProposalRisk.LOW
    evolver_version: str = __version__
    approval_decision: str | None = None
    approval_reason: str | None = None
    reviewed_at: str | None = None
    validation_errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["status"] = self.status.value
        payload["risk"] = self.risk.value
        return payload


@dataclass
class ProposalValidation:
    valid: bool
    errors: list[str]
    risk: ProposalRisk
    confidence: float
    explanation: list[str]

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["risk"] = self.risk.value
        return payload


@dataclass
class EvolutionFinding:
    finding_type: str
    target_component: str
    task_type: str
    observed_problem: str
    evidence: list[dict[str, Any]]
    proposed_change: str
    expected_benefit: str
    risks: list[str]
    affected_capabilities: list[str]
    evaluation_method: str
    rollback_plan: str
    risk: ProposalRisk
    confidence: float

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["risk"] = self.risk.value
        return payload


class Evolver:
    """Evidence analyzer that stops at a human-reviewable proposal boundary."""

    EVOLVER_TASK_ID = "evolver"
    PROTECTED_TERMS = (
        "permission", "security", "approval", "sandbox", "rollback", "verification", "governance", "kill switch", "trust boundary",
    )
    VAGUE_TERMS = ("make smarter", "make the agent smarter", "improve everything", "optimize", "be better", "enhance intelligence")

    def __init__(self, store: SQLiteStore, experience_engine: ExperienceEngine | None = None, evolver_version: str = __version__):
        self.store = store
        self.experience_engine = experience_engine or ExperienceEngine(store)
        self.evolver_version = evolver_version

    def analyze_experiences(self, experiences: Iterable[Experience] | None = None) -> list[EvolutionFinding]:
        records = list(experiences) if experiences is not None else self.experience_engine.retrieve(limit=1000)
        findings = self.identify_weaknesses(records) + self.identify_opportunities(records)
        return self._deduplicate_findings(findings)

    def identify_weaknesses(self, experiences: list[Experience]) -> list[EvolutionFinding]:
        findings: list[EvolutionFinding] = []
        groups: dict[tuple[str, str | None], list[Experience]] = {}
        for experience in experiences:
            groups.setdefault((experience.task_type, experience.selected_strategy), []).append(experience)

        for (task_type, strategy), group in groups.items():
            failures = [item for item in group if item.final_outcome.value in {"failure", "timeout", "aborted", "blocked"}]
            scores = [self._score(item) for item in group if self._score(item) is not None]
            if len(failures) >= 2:
                evidence = [self._evidence(item, "repeated_failure") for item in failures]
                findings.append(self._finding(
                    "repeated_failure", task_type, "strategy-selection", task_type,
                    f"Strategy '{strategy or 'unknown'}' repeatedly fails for task type '{task_type}' ({len(failures)} of {len(group)} recorded executions).",
                    evidence,
                    f"Change strategy-selection criteria to avoid '{strategy or 'unknown'}' for this task type when the recorded conditions recur.",
                    "Prefer a historically safer alternative strategy for comparable tasks.",
                    ["Regression on other task types", "Overfitting to a small sample"],
                    ["strategy_selection"],
                    "Replay the same task-type benchmark and compare verified success rate against the current selector.",
                    "Restore the previous selector rule and strategy preference.",
                ))
            if len(scores) >= 2 and sum(scores) / len(scores) < 60:
                evidence = [self._evidence(item, "low_evaluation_score") for item in group if self._score(item) is not None]
                findings.append(self._finding(
                    "low_performance", task_type, "planning-heuristics", task_type,
                    f"Recorded evaluations for task type '{task_type}' have a low mean score of {sum(scores) / len(scores):.1f}.",
                    evidence,
                    f"Review planning heuristics for '{task_type}' and add an explicit measurable fallback for the observed failure mode.",
                    "Increase verified completion quality without changing security or approval controls.",
                    ["Additional planning latency", "New heuristic may not generalize"],
                    ["planning"],
                    "Run a fixed benchmark for this task type and compare verified outcomes and scores.",
                    "Remove the added planning heuristic and restore the prior configuration.",
                ))
            inefficient = [item for item in group if self._is_inefficient(item)]
            if len(inefficient) >= 2:
                evidence = [self._evidence(item, "inefficient_execution") for item in inefficient]
                findings.append(self._finding(
                    "inefficient_execution", task_type, "recovery-policy", task_type,
                    f"Successful executions for '{task_type}' repeatedly require retries, replans, or strategy changes.",
                    evidence,
                    f"Tune bounded retry and recovery heuristics for '{task_type}' to reduce repeated work while retaining the existing limits.",
                    "Reduce retries or replans without lowering verified success.",
                    ["Premature stopping", "Reduced recovery opportunity"],
                    ["retry_policy", "recovery"],
                    "Compare verified success and mean retries/replans against a fixed baseline.",
                    "Restore the previous retry and recovery parameters.",
                ))

        tool_groups: dict[str, list[Experience]] = {}
        for experience in experiences:
            for tool in experience.selected_tools:
                tool_groups.setdefault(tool, []).append(experience)
        for tool, group in tool_groups.items():
            failed = [item for item in group if any(item_failure.get("tool") == tool for item_failure in item.failures)]
            if len(failed) >= 2:
                evidence = [self._evidence(item, "tool_failure") for item in failed]
                findings.append(self._finding(
                    "tool_problem", "tool-selection", "tool-selection", tool,
                    f"Tool '{tool}' fails repeatedly across {len(failed)} recorded experiences.",
                    evidence,
                    f"Adjust tool-selection heuristics to prefer an alternative when '{tool}' has the recorded failure conditions.",
                    "Reduce tool failures and unnecessary recovery cycles.",
                    ["Alternative tool may be less capable", "Tool-selection false positives"],
                    ["tool_selection"],
                    "Compare tool failure rate and verified task outcomes on a fixed task set.",
                    "Restore the previous tool-selection heuristic.",
                    risk=ProposalRisk.MEDIUM,
                ))
        return findings

    def identify_opportunities(self, experiences: list[Experience]) -> list[EvolutionFinding]:
        findings: list[EvolutionFinding] = []
        groups: dict[str, dict[str, list[Experience]]] = {}
        for experience in experiences:
            if experience.selected_strategy:
                groups.setdefault(experience.task_type, {}).setdefault(experience.selected_strategy, []).append(experience)
        for task_type, strategies in groups.items():
            if len(strategies) < 2:
                continue
            stats = []
            for strategy, records in strategies.items():
                successes = sum(item.final_outcome.value == "success" for item in records)
                stats.append((successes / len(records), strategy, records))
            stats.sort(reverse=True)
            best_rate, best_strategy, best_records = stats[0]
            for rate, strategy, records in stats[1:]:
                if len(records) >= 2 and best_rate >= rate + 0.25 and best_rate >= 0.5:
                    source = records + best_records
                    evidence = [self._evidence(item, "alternative_strategy") for item in source]
                    findings.append(self._finding(
                        "successful_alternative", task_type, "strategy-selection", task_type,
                        f"Strategy '{strategy}' underperforms '{best_strategy}' for '{task_type}' ({rate:.0%} versus {best_rate:.0%} verified success).",
                        evidence,
                        f"Prefer '{best_strategy}' over '{strategy}' for comparable '{task_type}' tasks when the benchmark conditions match.",
                        "Improve verified success by using the stronger observed strategy.",
                        ["Performance may differ on unseen task variants", "Sample-size bias"],
                        ["strategy_selection"],
                        "Run comparable tasks under both selector choices and compare verified success rates.",
                        f"Restore the previous preference for '{strategy}'.",
                    ))
        return findings

    def generate_proposal(self, finding: EvolutionFinding) -> EvolutionProposal:
        source_experiences = list(dict.fromkeys(item["experience_id"] for item in finding.evidence))
        source_evaluations = list(dict.fromkeys(item["evaluation_id"] for item in finding.evidence if item.get("evaluation_id")))
        proposal = EvolutionProposal(
            proposal_id=f"proposal_{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S%f')}",
            created_at=datetime.now(timezone.utc).isoformat(),
            source_experiences=source_experiences,
            source_evaluations=source_evaluations,
            agent_version=self._agent_version(finding.evidence),
            target_component=finding.target_component,
            observed_problem=finding.observed_problem,
            evidence=finding.evidence,
            proposed_change=finding.proposed_change,
            expected_benefit=finding.expected_benefit,
            risks=finding.risks,
            affected_capabilities=finding.affected_capabilities,
            affected_permissions=[],
            confidence=finding.confidence,
            evaluation_method=finding.evaluation_method,
            rollback_plan=finding.rollback_plan,
            status=ProposalStatus.GENERATED,
            risk=finding.risk,
            evolver_version=self.evolver_version,
        )
        validation = self.evaluate_proposal(proposal)
        proposal.validation_errors = validation.errors
        proposal.risk = validation.risk
        if validation.valid:
            proposal.status = ProposalStatus.PENDING_REVIEW
        else:
            proposal.status = ProposalStatus.REJECTED
        return proposal

    def evaluate_proposal(self, proposal: EvolutionProposal) -> ProposalValidation:
        errors: list[str] = []
        text_fields = {
            "target_component": proposal.target_component,
            "observed_problem": proposal.observed_problem,
            "proposed_change": proposal.proposed_change,
            "expected_benefit": proposal.expected_benefit,
            "evaluation_method": proposal.evaluation_method,
            "rollback_plan": proposal.rollback_plan,
        }
        for field_name, value in text_fields.items():
            if not value or len(value.strip()) < 12:
                errors.append(f"{field_name} is missing or insufficiently specific")
        if not proposal.evidence:
            errors.append("evidence is required")
        if not proposal.risks:
            errors.append("risks are required")
        if not proposal.source_experiences:
            errors.append("source experience IDs are required")
        if any(term in f"{proposal.target_component} {proposal.proposed_change}".lower() for term in self.PROTECTED_TERMS):
            errors.append("protected component cannot receive an executable evolution proposal")
        if any(term in proposal.proposed_change.lower() for term in self.VAGUE_TERMS):
            errors.append("proposed change is too vague")
        risk = self.classify_risk(proposal.target_component)
        if risk is ProposalRisk.PROTECTED:
            errors.append("target component is protected")
        confidence = self.calculate_confidence(proposal.evidence)
        explanation = [f"confidence is based on {len(proposal.source_experiences)} supporting experience(s)", f"classified target risk as {risk.value}"]
        if errors:
            explanation.append(f"proposal rejected or held invalid for {len(errors)} reason(s)")
        else:
            explanation.append("proposal contains evidence, bounded change, benefit, risks, evaluation, and rollback")
        return ProposalValidation(not errors, errors, risk, confidence, explanation)

    def persist_proposal(self, proposal: EvolutionProposal) -> None:
        validation = self.evaluate_proposal(proposal)
        proposal.validation_errors = validation.errors
        proposal.risk = validation.risk
        if validation.valid and proposal.status is ProposalStatus.GENERATED:
            proposal.status = ProposalStatus.PENDING_REVIEW
        elif not validation.valid:
            proposal.status = ProposalStatus.REJECTED
        self.store.save_proposal(proposal)

    def analyze_and_persist(self, experiences: Iterable[Experience] | None = None) -> list[EvolutionProposal]:
        records = list(experiences) if experiences is not None else self.experience_engine.retrieve(limit=1000)
        self.store.append_event(Event(self.EVOLVER_TASK_ID, EventType.EVOLUTION_ANALYSIS_STARTED, {"experience_count": len(records), "evolver_version": self.evolver_version}))
        findings = self.analyze_experiences(records)
        for finding in findings:
            event_type = EventType.WEAKNESS_DETECTED if finding.finding_type != "successful_alternative" else EventType.EVOLUTION_OPPORTUNITY_DETECTED
            self.store.append_event(Event(self.EVOLVER_TASK_ID, event_type, finding.to_dict()))
        proposals: list[EvolutionProposal] = []
        for finding in findings:
            proposal = self.generate_proposal(finding)
            self.store.append_event(Event(self.EVOLVER_TASK_ID, EventType.PROPOSAL_GENERATED, {"proposal_id": proposal.proposal_id, "target_component": proposal.target_component, "risk": proposal.risk.value}))
            validation = self.evaluate_proposal(proposal)
            self.store.append_event(Event(self.EVOLVER_TASK_ID, EventType.PROPOSAL_VALIDATED if validation.valid else EventType.PROPOSAL_REJECTED, {"proposal_id": proposal.proposal_id, **validation.to_dict()}))
            self.persist_proposal(proposal)
            proposals.append(proposal)
        return proposals

    def approve(self, proposal_id: str, reason: str = "") -> EvolutionProposal:
        proposal = self.get_proposal(proposal_id)
        if proposal is None:
            raise KeyError(f"Proposal not found: {proposal_id}")
        if proposal.status is not ProposalStatus.PENDING_REVIEW:
            raise ValueError(f"Only pending proposals can be approved; current status is {proposal.status.value}")
        proposal.status = ProposalStatus.APPROVED
        proposal.approval_decision = "approved_for_future_sandbox"
        proposal.approval_reason = reason
        proposal.reviewed_at = datetime.now(timezone.utc).isoformat()
        self.store.save_proposal(proposal)
        self.store.append_event(Event(self.EVOLVER_TASK_ID, EventType.PROPOSAL_APPROVED, {"proposal_id": proposal_id, "decision": proposal.approval_decision, "reason": reason}))
        return proposal

    def reject(self, proposal_id: str, reason: str = "") -> EvolutionProposal:
        proposal = self.get_proposal(proposal_id)
        if proposal is None:
            raise KeyError(f"Proposal not found: {proposal_id}")
        if proposal.status not in {ProposalStatus.GENERATED, ProposalStatus.PENDING_REVIEW}:
            raise ValueError(f"Only generated or pending proposals can be rejected; current status is {proposal.status.value}")
        proposal.status = ProposalStatus.REJECTED
        proposal.approval_decision = "rejected"
        proposal.approval_reason = reason
        proposal.reviewed_at = datetime.now(timezone.utc).isoformat()
        self.store.save_proposal(proposal)
        self.store.append_event(Event(self.EVOLVER_TASK_ID, EventType.PROPOSAL_REJECTED, {"proposal_id": proposal_id, "decision": proposal.approval_decision, "reason": reason}))
        return proposal

    def get_proposal(self, proposal_id: str) -> EvolutionProposal | None:
        record = self.store.proposal_by_id(proposal_id)
        return self.from_dict(record) if record else None

    def list_proposals(self, status: ProposalStatus | str | None = None, limit: int = 50) -> list[EvolutionProposal]:
        value = status.value if isinstance(status, ProposalStatus) else status
        return [self.from_dict(record) for record in self.store.find_proposals(status=value, limit=limit)]

    @staticmethod
    def from_dict(record: dict[str, Any]) -> EvolutionProposal:
        payload = record.get("payload", record)
        payload = json_loads(payload) if isinstance(payload, str) else dict(payload)
        payload["status"] = ProposalStatus(payload["status"])
        payload["risk"] = ProposalRisk(payload["risk"])
        return EvolutionProposal(**payload)

    @staticmethod
    def classify_risk(target_component: str) -> ProposalRisk:
        target = target_component.lower()
        if any(term in target for term in Evolver.PROTECTED_TERMS):
            return ProposalRisk.PROTECTED
        if any(term in target for term in ("execution", "kernel", "recovery-policy")):
            return ProposalRisk.HIGH
        if any(term in target for term in ("tool", "planning", "prompt", "configuration")):
            return ProposalRisk.MEDIUM
        return ProposalRisk.LOW

    @staticmethod
    def calculate_confidence(evidence: list[dict[str, Any]]) -> float:
        if not evidence:
            return 0.0
        experience_count = len({item.get("experience_id") for item in evidence if item.get("experience_id")})
        outcome_labels = [item.get("outcome") for item in evidence if item.get("outcome")]
        consistency = max(outcome_labels.count("failure"), outcome_labels.count("success"), 1) / max(len(outcome_labels), 1)
        diversity = min(1.0, len({item.get("task_type") for item in evidence if item.get("task_type")}) / 3)
        return round(min(1.0, 0.35 * min(1.0, experience_count / 5) + 0.45 * consistency + 0.20 * diversity), 3)

    @staticmethod
    def _score(experience: Experience) -> float | None:
        result = experience.evaluation_result or {}
        value = result.get("success_score")
        return float(value) if isinstance(value, (int, float)) else None

    @staticmethod
    def _is_inefficient(experience: Experience) -> bool:
        result = experience.evaluation_result or {}
        return bool(result.get("retry_count", 0) or result.get("replan_count", 0) or result.get("strategy_changes", 0)) and experience.final_outcome.value == "success"

    @staticmethod
    def _evidence(experience: Experience, evidence_type: str) -> dict[str, Any]:
        evaluation = experience.evaluation_result or {}
        return {
            "experience_id": experience.experience_id,
            "evaluation_id": experience.evaluation_id,
            "task_type": experience.task_type,
            "strategy": experience.selected_strategy,
            "outcome": experience.final_outcome.value,
            "success_score": evaluation.get("success_score"),
            "agent_version": experience.agent_version,
            "evidence_type": evidence_type,
        }

    def _finding(self, finding_type: str, task_type: str, target_component: str, _unused: str, observed_problem: str, evidence: list[dict[str, Any]], proposed_change: str, expected_benefit: str, risks: list[str], affected_capabilities: list[str], evaluation_method: str, rollback_plan: str, risk: ProposalRisk = ProposalRisk.LOW) -> EvolutionFinding:
        return EvolutionFinding(finding_type, target_component, task_type, observed_problem, evidence, proposed_change, expected_benefit, risks, affected_capabilities, evaluation_method, rollback_plan, risk, self.calculate_confidence(evidence))

    @staticmethod
    def _agent_version(evidence: list[dict[str, Any]]) -> str:
        return str(evidence[0].get("agent_version", __version__)) if evidence else __version__

    @staticmethod
    def _deduplicate_findings(findings: list[EvolutionFinding]) -> list[EvolutionFinding]:
        seen: set[tuple[str, str, str]] = set()
        unique: list[EvolutionFinding] = []
        for finding in findings:
            key = (finding.finding_type, finding.task_type, finding.target_component)
            if key not in seen:
                seen.add(key)
                unique.append(finding)
        return unique


def json_loads(value: str) -> Any:
    import json
    return json.loads(value)
