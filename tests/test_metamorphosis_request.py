"""Tests for MetamorphosisRequest - M1 Phase 1 core architecture."""
import pytest
from evo_agent.metamorphosis_request import (
    MetamorphosisRequest,
    MetamorphosisRequestStatus,
    MetamorphosisRequestManager,
)
from evo_agent.models import StructuralChangeType


@pytest.fixture
def sample_request():
    """Create a sample request for testing."""
    return MetamorphosisRequest.create(
        change_type=StructuralChangeType.ADD_COMPONENT,
        target_component="new_capability",
        current_state={"components": ["existing"]},
        proposed_state={"components": ["existing", "new_capability"]},
        rationale="Need new capability for feature X",
        expected_benefit="Improved feature X support",
        risks=["Integration complexity"],
        risk_classification="medium",
        evidence_ids=["ev-001", "ev-002"],
        requested_by="test-user",
    )


class TestMetamorphosisRequestCreation:
    """Test MetamorphosisRequest creation."""
    
    def test_create_request(self, sample_request):
        """Test request creation."""
        assert sample_request.request_id is not None
        assert sample_request.change_type == StructuralChangeType.ADD_COMPONENT
        assert sample_request.target_component == "new_capability"
        assert sample_request.status == MetamorphosisRequestStatus.PENDING
    
    def test_request_has_all_fields(self, sample_request):
        """Test that request has all required fields."""
        assert sample_request.request_id.startswith("mreq_")
        assert sample_request.rationale != ""
        assert len(sample_request.evidence_ids) == 2
        assert sample_request.risk_classification == "medium"
        assert sample_request.expires_at is not None


class TestMetamorphosisRequestLifecycle:
    """Test MetamorphosisRequest lifecycle transitions."""
    
    def test_submit_request(self, sample_request):
        """Test submitting a request."""
        sample_request.submit()
        assert sample_request.status == MetamorphosisRequestStatus.SUBMITTED
    
    def test_start_review(self, sample_request):
        """Test starting review."""
        sample_request.submit()
        sample_request.start_review()
        assert sample_request.status == MetamorphosisRequestStatus.UNDER_REVIEW
        assert sample_request.review_started_at is not None
    
    def test_approve_request_external(self, sample_request):
        """Test approving with external approver."""
        sample_request.submit()
        sample_request.start_review()
        sample_request.approve("human-user-001", "Approved for implementation")
        
        assert sample_request.status == MetamorphosisRequestStatus.APPROVED
        assert sample_request.approver_id == "human-user-001"
        assert sample_request.decision_reason == "Approved for implementation"
        assert sample_request.decided_at is not None
    
    def test_reject_request_external(self, sample_request):
        """Test rejecting with external approver."""
        sample_request.submit()
        sample_request.start_review()
        sample_request.reject("human-user-001", "Not enough evidence")
        
        assert sample_request.status == MetamorphosisRequestStatus.REJECTED
        assert sample_request.approver_id == "human-user-001"
    
    def test_cancel_request(self, sample_request):
        """Test cancelling a pending request."""
        sample_request.cancel("No longer needed")
        assert sample_request.status == MetamorphosisRequestStatus.CANCELLED
        assert sample_request.decision_reason == "No longer needed"


class TestMetamorphosisRequestSecurity:
    """Test security constraints on MetamorphosisRequest."""
    
    def test_evo_cannot_self_approve(self, sample_request):
        """Test that Evo cannot approve its own requests."""
        sample_request.submit()
        sample_request.start_review()
        
        with pytest.raises(PermissionError, match="cannot self-approve"):
            sample_request.approve("evo", "Self approval")
        
        with pytest.raises(PermissionError, match="cannot self-approve"):
            sample_request.approve("evo_system", "Self approval")
        
        # Status should still be UNDER_REVIEW
        assert sample_request.status == MetamorphosisRequestStatus.UNDER_REVIEW
    
    def test_evo_cannot_self_reject(self, sample_request):
        """Test that Evo cannot reject its own requests."""
        sample_request.submit()
        
        with pytest.raises(PermissionError, match="cannot self-reject"):
            sample_request.reject("evo", "Self rejection")
        
        with pytest.raises(PermissionError, match="cannot self-reject"):
            sample_request.reject("evo_agent", "Self rejection")
    
    def test_requires_external_approver(self, sample_request):
        """Test that external approver is required."""
        sample_request.submit()
        sample_request.start_review()
        
        # Empty approver rejected
        with pytest.raises(PermissionError):
            sample_request.approve("", "Empty approver")
        
        # Status unchanged
        assert sample_request.status == MetamorphosisRequestStatus.UNDER_REVIEW


class TestMetamorphosisRequestValidation:
    """Test MetamorphosisRequest validation."""
    
    def test_validate_valid_request(self, sample_request):
        """Test validating a valid request."""
        valid, issues = sample_request.validate()
        assert valid is True
        assert len(issues) == 0
    
    def test_validate_missing_rationale(self):
        """Test validation catches missing rationale."""
        req = MetamorphosisRequest.create(
            change_type=StructuralChangeType.ADD_COMPONENT,
            target_component="test",
            current_state={},
            proposed_state={},
            rationale="",  # Missing
            expected_benefit="Benefit",
            risks=[],
            risk_classification="low",
            evidence_ids=["ev-001"],
            requested_by="user",
        )
        
        valid, issues = req.validate()
        assert valid is False
        assert "Missing rationale" in issues
    
    def test_validate_no_evidence(self):
        """Test validation catches missing evidence."""
        req = MetamorphosisRequest.create(
            change_type=StructuralChangeType.ADD_COMPONENT,
            target_component="test",
            current_state={},
            proposed_state={},
            rationale="Good rationale",
            expected_benefit="Benefit",
            risks=[],
            risk_classification="low",
            evidence_ids=[],  # Empty
            requested_by="user",
        )
        
        valid, issues = req.validate()
        assert valid is False
        assert "No evidence provided" in issues
    
    def test_validate_invalid_risk_classification(self):
        """Test validation catches invalid risk classification."""
        req = MetamorphosisRequest.create(
            change_type=StructuralChangeType.ADD_COMPONENT,
            target_component="test",
            current_state={},
            proposed_state={},
            rationale="Rationale",
            expected_benefit="Benefit",
            risks=[],
            risk_classification="invalid_level",  # Invalid
            evidence_ids=["ev-001"],
            requested_by="user",
        )
        
        valid, issues = req.validate()
        assert valid is False
        assert "Invalid risk classification" in issues[0]


class TestMetamorphosisRequestExpiration:
    """Test MetamorphosisRequest expiration handling."""
    
    def test_expiration_check(self, sample_request):
        """Test expiration checking."""
        # Request created with 72 hour expiration
        # Should not be expired immediately
        assert sample_request.is_expired() is False
    
    def test_expiration_marking(self):
        """Test marking request as expired."""
        req = MetamorphosisRequest.create(
            change_type=StructuralChangeType.ADD_COMPONENT,
            target_component="test",
            current_state={},
            proposed_state={},
            rationale="Rationale",
            expected_benefit="Benefit",
            risks=[],
            risk_classification="low",
            evidence_ids=["ev-001"],
            requested_by="user",
            expires_in_hours=-1,  # Already expired (negative hours)
        )
        
        assert req.is_expired() is True
        
        # Check expiration should mark it
        result = req.check_expiration()
        assert result is True
        assert req.status == MetamorphosisRequestStatus.EXPIRED
    
    def test_link_to_proposal(self, sample_request):
        """Test linking approved request to proposal."""
        sample_request.submit()
        sample_request.start_review()
        sample_request.approve("human-001", "Approved")
        
        sample_request.link_to_proposal("prop-001")
        assert sample_request.related_proposal_id == "prop-001"
    
    def test_cannot_link_unapproved_request(self, sample_request):
        """Test that unapproved requests cannot be linked."""
        with pytest.raises(RuntimeError, match="Only approved"):
            sample_request.link_to_proposal("prop-001")


class TestMetamorphosisRequestManager:
    """Test MetamorphosisRequestManager."""
    
    def test_create_request(self):
        """Test creating request through manager."""
        from evo_agent.storage import SQLiteStore
        import tempfile
        from pathlib import Path
        
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        
        try:
            store = SQLiteStore(db_path)
            manager = MetamorphosisRequestManager(store)
            
            req = manager.create_request(
                change_type=StructuralChangeType.ADD_COMPONENT,
                target_component="test_comp",
                current_state={"a": 1},
                proposed_state={"a": 1, "b": 2},
                rationale="Need it",
                expected_benefit="Better",
                risks=["Risk"],
                risk_classification="low",
                evidence_ids=["ev-001"],
                requested_by="user",
            )
            
            assert req is not None
            assert req.request_id in manager._requests
        finally:
            Path(db_path).unlink(missing_ok=True)
    
    def test_get_request(self):
        """Test retrieving request through manager."""
        from evo_agent.storage import SQLiteStore
        import tempfile
        from pathlib import Path
        
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        
        try:
            store = SQLiteStore(db_path)
            manager = MetamorphosisRequestManager(store)
            
            req = manager.create_request(
                change_type=StructuralChangeType.ADD_COMPONENT,
                target_component="test",
                current_state={},
                proposed_state={},
                rationale="Rationale",
                expected_benefit="Benefit",
                risks=[],
                risk_classification="low",
                evidence_ids=["ev-001"],
                requested_by="user",
            )
            
            retrieved = manager.get_request(req.request_id)
            assert retrieved is req
            assert retrieved.request_id == req.request_id
        finally:
            Path(db_path).unlink(missing_ok=True)
    
    def test_list_requests_filtered(self):
        """Test listing requests with filter."""
        from evo_agent.storage import SQLiteStore
        import tempfile
        from pathlib import Path
        
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        
        try:
            store = SQLiteStore(db_path)
            manager = MetamorphosisRequestManager(store)
            
            # Create multiple requests
            for i in range(3):
                manager.create_request(
                    change_type=StructuralChangeType.ADD_COMPONENT,
                    target_component=f"test_{i}",
                    current_state={},
                    proposed_state={},
                    rationale="Rationale",
                    expected_benefit="Benefit",
                    risks=[],
                    risk_classification="low",
                    evidence_ids=[f"ev-{i:03d}"],
                    requested_by="user",
                )
            
            all_reqs = manager.list_requests()
            assert len(all_reqs) == 3
            
            # Filter by status (all PENDING)
            pending = manager.list_requests(status=MetamorphosisRequestStatus.PENDING)
            assert len(pending) == 3
        finally:
            Path(db_path).unlink(missing_ok=True)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
