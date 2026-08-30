"""Tests for SandboxProvider - M1 Phase 1 core architecture."""
import pytest
from pathlib import Path
import tempfile
from evo_agent.sandbox_provider import (
    SandboxProvider,
    SandboxRequest,
    SandboxResponse,
    DefaultSandboxProvider,
    SovereignMediationLayer,
)
from evo_agent.sandbox import SandboxEngine
from evo_agent.storage import SQLiteStore


@pytest.fixture
def temp_dir():
    """Create a temporary directory for testing."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


@pytest.fixture
def store(temp_dir):
    """Create SQLite store."""
    db_path = temp_dir / "test.db"
    return SQLiteStore(str(db_path))


@pytest.fixture
def engine(store, temp_dir):
    """Create SandboxEngine."""
    sandbox_root = temp_dir / "sandbox"
    sandbox_root.mkdir()
    return SandboxEngine(store, str(sandbox_root))


class TestSandboxRequest:
    """Test SandboxRequest dataclass."""
    
    def test_create_request(self, engine, temp_dir):
        """Test request creation."""
        from evo_agent.evolver import EvolutionProposal
        from evo_agent.models import ProposalStatus
        
        # First create an approved proposal in the store
        proposal = EvolutionProposal(
            proposal_id="prop-001",
            created_at="2024-01-01T00:00:00Z",
            target_component="planning configuration",
            observed_problem="Test problem",
            evidence=[],
            proposed_change="Update planning timeout from 30s to 60s for complex tasks",
            expected_benefit="Benefit",
            risks=[],
            affected_capabilities=[],
            affected_permissions=[],
            confidence=0.9,
            evaluation_method="Test method",
            rollback_plan="Rollback plan",
            source_experiences=[],
            source_evaluations=[],
            agent_version="1.0.0",
            status=ProposalStatus.APPROVED,
        )
        
        # Store the proposal (required by create_sandbox)
        engine.store.save_proposal(proposal)
        
        # Use create_sandbox which returns (experiment, proposal, baseline_dir, candidate_dir)
        experiment, prop, baseline_dir, candidate_dir = engine.create_sandbox("prop-001")
        location = temp_dir / "test_location"
        location.mkdir()
        
        request = SandboxRequest.create(
            experiment=experiment,
            command=["echo", "test"],
            location=location,
            label="candidate",
            timeout_seconds=30,
        )
        
        assert request.request_id.startswith("sreq_")
        assert request.experiment_id == experiment.experiment_id
        assert request.command == ["echo", "test"]
        assert request.label == "candidate"
        assert request.timeout_seconds == 30


class TestDefaultSandboxProvider:
    """Test DefaultSandboxProvider implementation."""
    
    def test_provider_creation(self, engine):
        """Test provider creation."""
        provider = DefaultSandboxProvider(engine)
        assert provider is not None
        assert provider.engine is engine
    
    def test_execute_logs_result(self, engine, temp_dir):
        """Test that execution results are logged."""
        provider = DefaultSandboxProvider(engine)
        
        # Create a minimal experiment manually for testing
        from evo_agent.evolver import EvolutionProposal
        from evo_agent.models import ProposalStatus
        proposal = EvolutionProposal(
            proposal_id="prop-002",
            created_at="2024-01-01T00:00:00Z",
            target_component="planning configuration",
            observed_problem="Test problem description",
            evidence=[],
            proposed_change="Adjust planning heuristic parameters to improve task decomposition efficiency by ten percent",
            expected_benefit="Improved planning efficiency",
            risks=[],
            affected_capabilities=[],
            affected_permissions=[],
            confidence=0.9,
            evaluation_method="Plan",
            rollback_plan="Restore previous planning configuration parameters",
            source_experiences=[],
            source_evaluations=[],
            agent_version="1.0.0",
            status=ProposalStatus.APPROVED,
        )
        
        # Store and create sandbox
        engine.store.save_proposal(proposal)
        experiment, prop, baseline_dir, candidate_dir = engine.create_sandbox("prop-002")
        location = temp_dir / "test_loc"
        location.mkdir()
        
        request = SandboxRequest.create(
            experiment=experiment,
            command=["true"],
            location=location,
            label="baseline",
        )
        
        response = provider.execute(request)
        
        assert response.request_id == request.request_id
        assert response.executed_at is not None
        
        # Check log was created
        log = provider.get_execution_log(request.request_id)
        assert log is not None


class TestSovereignMediationLayer:
    """Test SovereignMediationLayer."""
    
    def test_mediation_creation(self, engine, store):
        """Test mediation layer creation."""
        provider = DefaultSandboxProvider(engine)
        layer = SovereignMediationLayer(provider, store)
        
        assert layer is not None
        assert layer.provider is provider
        assert layer.store is store
    
    def test_policy_validation(self, engine, store, temp_dir):
        """Test policy validation."""
        provider = DefaultSandboxProvider(engine)
        layer = SovereignMediationLayer(provider, store)
        
        from evo_agent.evolver import EvolutionProposal
        from evo_agent.models import ProposalStatus
        proposal = EvolutionProposal(
            proposal_id="prop-003",
            created_at="2024-01-01T00:00:00Z",
            target_component="planning configuration",
            observed_problem="Test problem description",
            evidence=[],
            proposed_change="Adjust planning heuristic parameters to improve task decomposition efficiency by ten percent",
            expected_benefit="Improved planning efficiency",
            risks=[],
            affected_capabilities=[],
            affected_permissions=[],
            confidence=0.9,
            evaluation_method="Plan",
            rollback_plan="Restore previous planning configuration parameters",
            source_experiences=[],
            source_evaluations=[],
            agent_version="1.0.0",
            status=ProposalStatus.APPROVED,
        )
        
        # Store and create sandbox
        engine.store.save_proposal(proposal)
        experiment, prop, baseline_dir, candidate_dir = engine.create_sandbox("prop-003")
        location = temp_dir / "valid_loc"
        location.mkdir()
        
        # Valid request should pass policy check
        ok, msg = layer._validate_policy(
            SandboxRequest.create(experiment, ["python3"], location, "baseline"),
            {}
        )
        assert ok is True
    
    def test_invalid_command_rejected(self, engine, store, temp_dir):
        """Test that invalid commands are rejected."""
        provider = DefaultSandboxProvider(engine)
        layer = SovereignMediationLayer(provider, store)
        
        from evo_agent.evolver import EvolutionProposal
        from evo_agent.models import ProposalStatus
        proposal = EvolutionProposal(
            proposal_id="prop-004",
            created_at="2024-01-01T00:00:00Z",
            target_component="planning configuration",
            observed_problem="Test problem description",
            evidence=[],
            proposed_change="Adjust planning heuristic parameters to improve task decomposition efficiency by ten percent",
            expected_benefit="Improved planning efficiency",
            risks=[],
            affected_capabilities=[],
            affected_permissions=[],
            confidence=0.9,
            evaluation_method="Plan",
            rollback_plan="Restore previous planning configuration parameters",
            source_experiences=[],
            source_evaluations=[],
            agent_version="1.0.0",
            status=ProposalStatus.APPROVED,
        )
        
        # Store and create sandbox
        engine.store.save_proposal(proposal)
        experiment, prop, baseline_dir, candidate_dir = engine.create_sandbox("prop-004")
        location = temp_dir / "loc"
        location.mkdir()
        
        # Dangerous command should be rejected
        ok, msg = layer._validate_policy(
            SandboxRequest.create(experiment, ["dangerous_cmd"], location, "baseline"),
            {}
        )
        assert ok is False
        assert "not allowed" in msg
    
    def test_excessive_timeout_rejected(self, engine, store, temp_dir):
        """Test that excessive timeouts are rejected."""
        provider = DefaultSandboxProvider(engine)
        layer = SovereignMediationLayer(provider, store)
        
        from evo_agent.evolver import EvolutionProposal
        from evo_agent.models import ProposalStatus
        proposal = EvolutionProposal(
            proposal_id="prop-005",
            created_at="2024-01-01T00:00:00Z",
            target_component="planning configuration",
            observed_problem="Test problem description",
            evidence=[],
            proposed_change="Adjust planning heuristic parameters to improve task decomposition efficiency by ten percent",
            expected_benefit="Improved planning efficiency",
            risks=[],
            affected_capabilities=[],
            affected_permissions=[],
            confidence=0.9,
            evaluation_method="Plan",
            rollback_plan="Restore previous planning configuration parameters",
            source_experiences=[],
            source_evaluations=[],
            agent_version="1.0.0",
            status=ProposalStatus.APPROVED,
        )
        
        # Store and create sandbox
        engine.store.save_proposal(proposal)
        experiment, prop, baseline_dir, candidate_dir = engine.create_sandbox("prop-005")
        location = temp_dir / "loc"
        location.mkdir()
        
        # Excessive timeout should be rejected
        from evo_agent.sandbox_provider import SandboxRequest
        request = SandboxRequest.create(experiment, ["python3"], location, "baseline")
        request.timeout_seconds = 500  # Over 300s limit
        
        ok, msg = layer._validate_policy(request, {})
        assert ok is False
        assert "exceeds maximum" in msg


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
