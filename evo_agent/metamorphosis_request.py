"""
METAMORPHOSIS_REQUEST - Explicit request object for metamorphosis operations.

This module provides the METAMORPHOSIS_REQUEST dataclass and event type
required by M1 specification, separating request generation from approval.
Evo cannot self-approve its own requests.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from .models import EventType, MetamorphosisStatus, StructuralChangeType, new_id


class MetamorphosisRequestStatus(str, Enum):
    """Lifecycle status for metamorphosis requests."""
    PENDING = "pending"
    SUBMITTED = "submitted"
    UNDER_REVIEW = "under_review"
    APPROVED = "approved"
    REJECTED = "rejected"
    CANCELLED = "cancelled"
    EXPIRED = "expired"


@dataclass
class MetamorphosisRequest:
    """
    Explicit request object for metamorphosis operations.
    
    This separates the REQUEST phase from the APPROVAL phase:
    1. Request is generated with full context and evidence
    2. Request is submitted for external review
    3. External authority approves or rejects (Evo cannot self-approve)
    4. Only after approval can sandboxing proceed
    
    The request contains all necessary information for decision-making
    but has no authority to execute changes itself.
    """
    request_id: str
    change_type: StructuralChangeType
    target_component: str
    affected_components: list[str]
    
    # Context for decision-making
    current_state: dict[str, Any]
    proposed_state: dict[str, Any]
    rationale: str
    expected_benefit: str
    risks: list[str]
    risk_classification: str  # "low", "medium", "high", "critical"
    
    # Evidence backing the request
    evidence_ids: list[str]  # References to experience/evaluation records
    analysis_results: dict[str, Any]
    compatibility_analysis: dict[str, Any]
    
    # Migration and rollback planning
    migration_plan: dict[str, Any]
    rollback_plan: dict[str, Any]
    
    # Governance metadata
    requested_by: str  # Human or system identity
    requested_at: str
    expires_at: str | None  # Requests can expire
    
    # Status tracking
    status: MetamorphosisRequestStatus = MetamorphosisRequestStatus.PENDING
    review_started_at: str | None = None
    decided_at: str | None = None
    decision_reason: str = ""
    approver_id: str | None = None  # External approver identity
    
    # Audit trail
    version: int = 1
    parent_request_id: str | None = None  # For resubmissions
    related_proposal_id: str | None = None  # Links to MetamorphosisProposal after approval
    
    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["change_type"] = self.change_type.value if isinstance(self.change_type, StructuralChangeType) else str(self.change_type)
        data["status"] = self.status.value
        return data
    
    @classmethod
    def create(
        cls,
        change_type: StructuralChangeType,
        target_component: str,
        current_state: dict[str, Any],
        proposed_state: dict[str, Any],
        rationale: str,
        expected_benefit: str,
        risks: list[str],
        risk_classification: str,
        evidence_ids: list[str],
        requested_by: str,
        migration_plan: dict[str, Any] | None = None,
        rollback_plan: dict[str, Any] | None = None,
        expires_in_hours: int = 72,
    ) -> "MetamorphosisRequest":
        """
        Create a new metamorphosis request.
        
        Args:
            change_type: Type of structural change
            target_component: Component being changed
            current_state: Current architecture state
            proposed_state: Proposed new state
            rationale: Why this change is needed
            expected_benefit: Expected improvement
            risks: List of identified risks
            risk_classification: Risk level classification
            evidence_ids: IDs of supporting evidence
            requested_by: Identity of requester
            migration_plan: How to migrate to new state
            rollback_plan: How to revert if needed
            expires_in_hours: Request expiration time
            
        Returns:
            New MetamorphosisRequest in PENDING status
        """
        now = datetime.now(timezone.utc)
        expires_at = None
        if expires_in_hours is not None:
            from datetime import timedelta
            if expires_in_hours <= 0:
                # Already expired - set to past time
                expires_at = (now - timedelta(hours=abs(expires_in_hours) + 1)).isoformat()
            else:
                expires_at = (now + timedelta(hours=expires_in_hours)).isoformat()
        
        affected = list(set([target_component] + list(current_state.get("dependencies", []))))
        
        return cls(
            request_id=new_id("mreq"),
            change_type=change_type,
            target_component=target_component,
            affected_components=affected,
            current_state=current_state,
            proposed_state=proposed_state,
            rationale=rationale,
            expected_benefit=expected_benefit,
            risks=risks,
            risk_classification=risk_classification,
            evidence_ids=evidence_ids,
            analysis_results={},
            compatibility_analysis={},
            migration_plan=migration_plan or {},
            rollback_plan=rollback_plan or {},
            requested_by=requested_by,
            requested_at=now.isoformat(),
            expires_at=expires_at,
        )
    
    def submit(self) -> None:
        """Submit request for review."""
        if self.status != MetamorphosisRequestStatus.PENDING:
            raise RuntimeError(f"Cannot submit request in status {self.status.value}")
        self.status = MetamorphosisRequestStatus.SUBMITTED
    
    def start_review(self) -> None:
        """Mark request as under review."""
        if self.status not in [MetamorphosisRequestStatus.SUBMITTED, MetamorphosisRequestStatus.PENDING]:
            raise RuntimeError(f"Cannot start review for status {self.status.value}")
        self.status = MetamorphosisRequestStatus.UNDER_REVIEW
        self.review_started_at = datetime.now(timezone.utc).isoformat()
    
    def approve(self, approver_id: str, reason: str) -> None:
        """
        Approve the request (must be called by external authority).
        
        CRITICAL: Evo CANNOT call this on its own requests.
        The approver_id must be an external human or governance system.
        """
        if not approver_id or approver_id == "evo" or approver_id.startswith("evo_"):
            raise PermissionError("Evo cannot self-approve metamorphosis requests")
        
        if self.status != MetamorphosisRequestStatus.UNDER_REVIEW:
            raise RuntimeError(f"Cannot approve request in status {self.status.value}")
        
        self.status = MetamorphosisRequestStatus.APPROVED
        self.approver_id = approver_id
        self.decision_reason = reason
        self.decided_at = datetime.now(timezone.utc).isoformat()
    
    def reject(self, approver_id: str, reason: str) -> None:
        """
        Reject the request (must be called by external authority).
        
        CRITICAL: Evo CANNOT call this on its own requests.
        The approver_id must be an external human or governance system.
        """
        if not approver_id or approver_id == "evo" or approver_id.startswith("evo_"):
            raise PermissionError("Evo cannot self-reject metamorphosis requests (must be external)")
        
        if self.status not in [MetamorphosisRequestStatus.UNDER_REVIEW, MetamorphosisRequestStatus.SUBMITTED]:
            raise RuntimeError(f"Cannot reject request in status {self.status.value}")
        
        self.status = MetamorphosisRequestStatus.REJECTED
        self.approver_id = approver_id
        self.decision_reason = reason
        self.decided_at = datetime.now(timezone.utc).isoformat()
    
    def cancel(self, reason: str = "") -> None:
        """Cancel a pending/submitted request."""
        if self.status not in [MetamorphosisRequestStatus.PENDING, MetamorphosisRequestStatus.SUBMITTED]:
            raise RuntimeError(f"Cannot cancel request in status {self.status.value}")
        self.status = MetamorphosisRequestStatus.CANCELLED
        self.decision_reason = reason
        self.decided_at = datetime.now(timezone.utc).isoformat()
    
    def is_expired(self) -> bool:
        """Check if request has expired."""
        if not self.expires_at:
            return False
        return datetime.now(timezone.utc).isoformat() > self.expires_at
    
    def check_expiration(self) -> bool:
        """Check and mark as expired if past expiration."""
        if self.is_expired() and self.status in [MetamorphosisRequestStatus.PENDING, MetamorphosisRequestStatus.SUBMITTED]:
            self.status = MetamorphosisRequestStatus.EXPIRED
            return True
        return False
    
    def link_to_proposal(self, proposal_id: str) -> None:
        """Link approved request to a MetamorphosisProposal."""
        if self.status != MetamorphosisRequestStatus.APPROVED:
            raise RuntimeError("Only approved requests can be linked to proposals")
        self.related_proposal_id = proposal_id
    
    def validate(self) -> tuple[bool, list[str]]:
        """
        Validate request completeness.
        
        Returns:
            (is_valid, list_of_issues)
        """
        issues = []
        
        if not self.request_id:
            issues.append("Missing request_id")
        
        if not self.target_component:
            issues.append("Missing target_component")
        
        if not self.rationale:
            issues.append("Missing rationale")
        
        if not self.evidence_ids:
            issues.append("No evidence provided")
        
        if self.risk_classification not in ["low", "medium", "high", "critical"]:
            issues.append(f"Invalid risk classification: {self.risk_classification}")
        
        if self.is_expired():
            issues.append("Request has expired")
        
        return len(issues) == 0, issues


# Add new event types for metamorphosis requests
# These should be added to models.py EventType enum
METAMORPHOSIS_REQUEST_EVENT_TYPES = {
    "METAMORPHOSIS_REQUEST_CREATED": "metamorphosis_request_created",
    "METAMORPHOSIS_REQUEST_SUBMITTED": "metamorphosis_request_submitted",
    "METAMORPHOSIS_REQUEST_UNDER_REVIEW": "metamorphosis_request_under_review",
    "METAMORPHOSIS_REQUEST_APPROVED": "metamorphosis_request_approved",
    "METAMORPHOSIS_REQUEST_REJECTED": "metamorphosis_request_rejected",
    "METAMORPHOSIS_REQUEST_CANCELLED": "metamorphosis_request_cancelled",
    "METAMORPHOSIS_REQUEST_EXPIRED": "metamorphosis_request_expired",
}


class MetamorphosisRequestManager:
    """
    Manages lifecycle of metamorphosis requests.
    
    This manager ensures proper separation between:
    1. Request creation (Evo can do this)
    2. Request submission (Evo can do this)
    3. Approval/Rejection (EXTERNAL authority only - Evo cannot self-approve)
    4. Proposal generation (only after approval)
    """
    
    def __init__(self, store):
        self.store = store
        self._requests: dict[str, MetamorphosisRequest] = {}
    
    def create_request(self, **kwargs) -> MetamorphosisRequest:
        """Create a new metamorphosis request."""
        request = MetamorphosisRequest.create(**kwargs)
        self._requests[request.request_id] = request
        self._emit_event("METAMORPHOSIS_REQUEST_CREATED", request)
        return request
    
    def get_request(self, request_id: str) -> MetamorphosisRequest | None:
        """Get a request by ID."""
        return self._requests.get(request_id)
    
    def submit_request(self, request_id: str) -> None:
        """Submit a request for review."""
        request = self._requests.get(request_id)
        if not request:
            raise KeyError(f"Request {request_id} not found")
        request.submit()
        self._emit_event("METAMORPHOSIS_REQUEST_SUBMITTED", request)
    
    def start_review(self, request_id: str) -> None:
        """Start review process for a request."""
        request = self._requests.get(request_id)
        if not request:
            raise KeyError(f"Request {request_id} not found")
        request.start_review()
        self._emit_event("METAMORPHOSIS_REQUEST_UNDER_REVIEW", request)
    
    def approve_request(
        self,
        request_id: str,
        approver_id: str,
        reason: str,
    ) -> MetamorphosisRequest:
        """
        Approve a request (external authority only).
        
        SECURITY: This method enforces that Evo cannot self-approve.
        The approver_id must be an external identity.
        """
        request = self._requests.get(request_id)
        if not request:
            raise KeyError(f"Request {request_id} not found")
        
        request.approve(approver_id, reason)
        self._emit_event("METAMORPHOSIS_REQUEST_APPROVED", request)
        return request
    
    def reject_request(
        self,
        request_id: str,
        approver_id: str,
        reason: str,
    ) -> MetamorphosisRequest:
        """
        Reject a request (external authority only).
        """
        request = self._requests.get(request_id)
        if not request:
            raise KeyError(f"Request {request_id} not found")
        
        request.reject(approver_id, reason)
        self._emit_event("METAMORPHOSIS_REQUEST_REJECTED", request)
        return request
    
    def _emit_event(self, event_name: str, request: MetamorphosisRequest) -> None:
        """Emit audit event for request lifecycle."""
        try:
            from .models import Event, EventType
            
            # Map event name to EventType if it exists
            event_type_map = {
                "METAMORPHOSIS_REQUEST_CREATED": EventType.METAMORPHOSIS_PROPOSED,
                "METAMORPHOSIS_REQUEST_SUBMITTED": EventType.METAMORPHOSIS_PROPOSED,
                "METAMORPHOSIS_REQUEST_UNDER_REVIEW": EventType.METAMORPHOSIS_VALIDATED,
                "METAMORPHOSIS_REQUEST_APPROVED": EventType.METAMORPHOSIS_APPROVED,
                "METAMORPHOSIS_REQUEST_REJECTED": EventType.METAMORPHOSIS_REJECTED,
                "METAMORPHOSIS_REQUEST_CANCELLED": EventType.METAMORPHOSIS_REJECTED,
                "METAMORPHOSIS_REQUEST_EXPIRED": EventType.METAMORPHOSIS_REJECTED,
            }
            
            event_type = event_type_map.get(event_name, EventType.METAMORPHOSIS_PROPOSED)
            
            event = Event(
                event_id=new_id("evt"),
                event_type=event_type,
                timestamp=datetime.now(timezone.utc).isoformat(),
                payload={
                    "request_id": request.request_id,
                    "status": request.status.value,
                    "change_type": request.change_type.value if isinstance(request.change_type, StructuralChangeType) else str(request.change_type),
                    "target_component": request.target_component,
                    "approver_id": request.approver_id,
                },
            )
            self.store.append_event(event)
        except Exception:
            # Non-fatal: event logging should not break request management
            pass
    
    def list_requests(
        self,
        status: MetamorphosisRequestStatus | None = None,
        limit: int = 100,
    ) -> list[MetamorphosisRequest]:
        """List requests with optional status filter."""
        results = list(self._requests.values())
        if status:
            results = [r for r in results if r.status == status]
        return results[-limit:]


__all__ = [
    "MetamorphosisRequest",
    "MetamorphosisRequestStatus",
    "MetamorphosisRequestManager",
    "METAMORPHOSIS_REQUEST_EVENT_TYPES",
]
