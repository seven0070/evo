"""Tests for InvariantRegistry - M1 Phase 1 core architecture."""
import pytest
from evo_agent.invariant_registry import (
    InvariantRegistry,
    InvariantDefinition,
    InvariantViolation,
    InvariantStatus,
    InvariantCategory,
    GLOBAL_INVARIANT_REGISTRY,
    create_core_invariants,
)


@pytest.fixture
def registry():
    """Create a fresh invariant registry for testing."""
    return InvariantRegistry()


class TestInvariantRegistry:
    """Test InvariantRegistry core functionality."""
    
    def test_create_registry(self):
        """Test registry creation."""
        registry = InvariantRegistry()
        assert registry is not None
        assert len(registry.list_invariants()) == 0
    
    def test_register_invariant(self, registry):
        """Test invariant registration."""
        inv = registry.register(
            invariant_id="INV-TEST-001",
            name="Test Invariant",
            statement="This is a test invariant",
            category=InvariantCategory.SECURITY,
            severity="critical",
            test_fn=lambda: (True, "Test passed"),
        )
        
        assert inv.invariant_id == "INV-TEST-001"
        assert inv.name == "Test Invariant"
        assert inv.status == InvariantStatus.ACTIVE
        
        invariants = registry.list_invariants()
        assert len(invariants) == 1
    
    def test_duplicate_registration_rejected(self, registry):
        """Test that duplicate registrations are rejected."""
        registry.register(
            invariant_id="INV-TEST-001",
            name="Test Invariant",
            statement="Test",
            category=InvariantCategory.SECURITY,
            severity="high",
            test_fn=lambda: (True, "OK"),
        )
        
        with pytest.raises(ValueError, match="already registered"):
            registry.register(
                invariant_id="INV-TEST-001",
                name="Duplicate",
                statement="Dup",
                category=InvariantCategory.SECURITY,
                severity="high",
                test_fn=lambda: (True, "OK"),
            )
    
    def test_get_invariant(self, registry):
        """Test retrieving invariant by ID."""
        registry.register(
            invariant_id="INV-TEST-001",
            name="Test",
            statement="Test",
            category=InvariantCategory.SECURITY,
            severity="high",
            test_fn=lambda: (True, "OK"),
        )
        
        inv = registry.get("INV-TEST-001")
        assert inv is not None
        assert inv.name == "Test"
    
    def test_verify_invariant_pass(self, registry):
        """Test verifying an invariant that passes."""
        registry.register(
            invariant_id="INV-TEST-001",
            name="Passing Test",
            statement="Always passes",
            category=InvariantCategory.SECURITY,
            severity="high",
            test_fn=lambda: (True, "Verification successful"),
        )
        
        passed, message = registry.verify("INV-TEST-001")
        assert passed is True
        assert "successful" in message
    
    def test_verify_invariant_fail(self, registry):
        """Test verifying an invariant that fails."""
        registry.register(
            invariant_id="INV-TEST-001",
            name="Failing Test",
            statement="Always fails",
            category=InvariantCategory.SECURITY,
            severity="high",
            test_fn=lambda: (False, "Verification failed"),
        )
        
        passed, message = registry.verify("INV-TEST-001")
        assert passed is False
        assert "failed" in message
        
        # Check violation was recorded
        violations = registry.get_violations()
        assert len(violations) == 1
    
    def test_verify_nonexistent_invariant(self, registry):
        """Test error on nonexistent invariant."""
        passed, message = registry.verify("NONEXISTENT")
        assert passed is False
        assert "not found" in message
    
    def test_verify_all(self, registry):
        """Test verifying all invariants."""
        registry.register(
            invariant_id="INV-TEST-001",
            name="Test 1",
            statement="Test",
            category=InvariantCategory.SECURITY,
            severity="high",
            test_fn=lambda: (True, "OK"),
        )
        registry.register(
            invariant_id="INV-TEST-002",
            name="Test 2",
            statement="Test",
            category=InvariantCategory.GOVERNANCE,
            severity="high",
            test_fn=lambda: (True, "OK"),
        )
        
        results = registry.verify_all()
        assert len(results) == 2
        assert all(passed for passed, _ in results.values())
    
    def test_list_invariants_filtered(self, registry):
        """Test listing invariants with filters."""
        registry.register(
            invariant_id="INV-SEC-001",
            name="Security Test",
            statement="Test",
            category=InvariantCategory.SECURITY,
            severity="critical",
            test_fn=lambda: (True, "OK"),
        )
        registry.register(
            invariant_id="INV-GOV-001",
            name="Governance Test",
            statement="Test",
            category=InvariantCategory.GOVERNANCE,
            severity="high",
            test_fn=lambda: (True, "OK"),
        )
        
        # Filter by category
        sec_only = registry.list_invariants(category=InvariantCategory.SECURITY)
        assert len(sec_only) == 1
        assert sec_only[0].category == InvariantCategory.SECURITY
        
        # Filter by severity
        critical_only = registry.list_invariants(severity="critical")
        assert len(critical_only) == 1
    
    def test_resolve_violation(self, registry):
        """Test resolving a violation."""
        registry.register(
            invariant_id="INV-TEST-001",
            name="Failing Test",
            statement="Test",
            category=InvariantCategory.SECURITY,
            severity="high",
            test_fn=lambda: (False, "Failed"),
        )
        
        # Trigger violation
        registry.verify("INV-TEST-001")
        
        violations = registry.get_violations(resolved=False)
        assert len(violations) == 1
        
        # Resolve
        result = registry.resolve_violation(violations[0].violation_id, "Fixed")
        assert result is True
        
        resolved = registry.get_violations(resolved=True)
        assert len(resolved) == 1
        assert resolved[0].resolution_notes == "Fixed"
    
    def test_coverage_report(self, registry):
        """Test coverage report generation."""
        registry.register(
            invariant_id="INV-TEST-001",
            name="Test",
            statement="Test",
            category=InvariantCategory.SECURITY,
            severity="critical",
            test_fn=lambda: (True, "OK"),
        )
        
        report = registry.get_coverage_report()
        
        assert "summary" in report
        assert report["summary"]["total_invariants"] == 1
        assert report["summary"]["active"] == 1
        assert "by_category" in report
        assert "by_severity" in report
    
    def test_test_mapping(self, registry):
        """Test test mapping generation."""
        registry.register(
            invariant_id="INV-TEST-001",
            name="Test",
            statement="Test",
            category=InvariantCategory.SECURITY,
            severity="critical",
            test_fn=lambda: (True, "OK"),
        )
        
        mapping = registry.get_test_mapping()
        assert "INV-TEST-001" in mapping
        assert len(mapping["INV-TEST-001"]) == 1
        assert "test_invariant_inv_test_001" in mapping["INV-TEST-001"][0]


class TestInvariantDefinition:
    """Test InvariantDefinition dataclass."""
    
    def test_to_dict(self):
        """Test invariant serialization."""
        inv = InvariantDefinition(
            invariant_id="INV-TEST-001",
            name="Test",
            statement="Test statement",
            category=InvariantCategory.SECURITY,
            severity="critical",
            test_fn=lambda: (True, "OK"),
        )
        
        d = inv.to_dict()
        assert d["invariant_id"] == "INV-TEST-001"
        assert d["category"] == "security"
        assert d["status"] == "active"
        assert "test_fn" not in d  # Not serializable


class TestInvariantViolation:
    """Test InvariantViolation dataclass."""
    
    def test_to_dict(self):
        """Test violation serialization."""
        from datetime import datetime, timezone
        viol = InvariantViolation(
            violation_id="viol-001",
            invariant_id="INV-TEST-001",
            invariant_name="Test Invariant",
            violation_message="Something went wrong",
            context={"key": "value"},
            detected_at=datetime.now(timezone.utc).isoformat(),
            severity="high",
        )
        
        d = viol.to_dict()
        assert d["violation_id"] == "viol-001"
        assert d["resolved"] is False


class TestGlobalRegistry:
    """Test global invariant registry singleton."""
    
    def test_global_registry_exists(self):
        """Test that global registry exists."""
        assert GLOBAL_INVARIANT_REGISTRY is not None
    
    def test_core_invariants_registered(self):
        """Test that core invariants are pre-registered."""
        invariants = GLOBAL_INVARIANT_REGISTRY.list_invariants()
        assert len(invariants) > 0
        
        # Check categories are represented
        categories = set(inv.category for inv in invariants)
        assert InvariantCategory.SECURITY in categories
        assert InvariantCategory.GOVERNANCE in categories
        assert InvariantCategory.EXECUTION in categories
    
    def test_critical_invariants_present(self):
        """Test that critical invariants are present."""
        inv_ids = [inv.invariant_id for inv in GLOBAL_INVARIANT_REGISTRY.list_invariants()]
        
        # Core security invariants
        assert "INV-SEC-001" in inv_ids  # Sandbox Isolation
        assert "INV-SEC-004" in inv_ids  # Protected Core Unmodifiable
        
        # Core governance invariants
        assert "INV-GOV-001" in inv_ids  # No Self-Approval
        assert "INV-GOV-003" in inv_ids  # METAMORPHOSIS_REQUEST Separation
        
        # Core execution invariants
        assert "INV-EXE-001" in inv_ids  # Single Execution Loop
        
        # Core evolution invariants
        assert "INV-EVO-001" in inv_ids  # Benchmark Before Promotion


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
