"""Tests for BackendRegistry - M1 Phase 1 core architecture."""
import pytest
from pathlib import Path
import tempfile
from evo_agent.backend_registry import (
    BackendRegistry,
    BackendProvider,
    BackendDescriptor,
    BackendSelection,
    BackendStatus,
    BackendType,
    NativeBackendProvider,
)
from evo_agent.model_adapter import RuleBasedAdapter
from evo_agent.storage import SQLiteStore
from evo_agent.models import Goal


@pytest.fixture
def store():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    store = SQLiteStore(db_path)
    yield store
    Path(db_path).unlink(missing_ok=True)


@pytest.fixture
def registry(store):
    return BackendRegistry(store)


@pytest.fixture
def native_provider():
    adapter = RuleBasedAdapter()
    return NativeBackendProvider(adapter, "test-model")


class TestBackendRegistry:
    """Test BackendRegistry core functionality."""
    
    def test_create_registry(self, store):
        """Test registry creation."""
        registry = BackendRegistry(store)
        assert registry is not None
        assert len(registry.list_backends()) == 0
    
    def test_register_backend(self, registry, native_provider):
        """Test backend registration."""
        backend_id = registry.register(native_provider)
        assert backend_id is not None
        assert backend_id.startswith("native_")
        
        backends = registry.list_backends()
        assert len(backends) == 1
        assert backends[0].provider == "Native"
    
    def test_register_with_explicit_id(self, registry, native_provider):
        """Test registration with explicit backend ID."""
        backend_id = registry.register(native_provider, backend_id="my-backend-001")
        assert backend_id == "my-backend-001"
        
        descriptor = registry.get_descriptor(backend_id)
        assert descriptor.backend_id == "my-backend-001"
    
    def test_duplicate_registration_rejected(self, registry, native_provider):
        """Test that duplicate registrations are rejected."""
        backend_id = registry.register(native_provider)
        
        with pytest.raises(ValueError, match="already registered"):
            registry.register(native_provider, backend_id=backend_id)
    
    def test_unregister_backend(self, registry, native_provider):
        """Test backend unregistration."""
        backend_id = registry.register(native_provider)
        assert len(registry.list_backends()) == 1
        
        registry.unregister(backend_id)
        assert len(registry.list_backends()) == 0
    
    def test_get_backend(self, registry, native_provider):
        """Test retrieving backend by ID."""
        backend_id = registry.register(native_provider)
        
        retrieved = registry.get_backend(backend_id)
        assert retrieved is native_provider
    
    def test_get_nonexistent_backend(self, registry):
        """Test error on nonexistent backend."""
        with pytest.raises(KeyError):
            registry.get_backend("nonexistent")
    
    def test_select_backend_no_requirements(self, registry, native_provider):
        """Test backend selection without requirements."""
        registry.register(native_provider)
        
        selection = registry.select_backend()
        assert selection.backend_id is not None
        assert selection.confidence > 0
        assert selection.provider == "Native"
    
    def test_select_backend_with_capabilities(self, registry, native_provider):
        """Test backend selection with capability requirements."""
        registry.register(native_provider)
        
        selection = registry.select_backend(required_capabilities=["planning"])
        assert selection.confidence > 0
        
        # Should fail with impossible requirements
        with pytest.raises(RuntimeError):
            registry.select_backend(required_capabilities=["impossible_capability"])
    
    def test_select_backend_preferred_provider(self, registry, native_provider):
        """Test backend selection with preferred provider."""
        registry.register(native_provider)
        
        selection = registry.select_backend(preferred_provider="Native")
        assert selection.provider == "Native"
    
    def test_list_backends_filtered(self, registry, native_provider):
        """Test listing backends with filters."""
        registry.register(native_provider)
        
        # Filter by type
        by_type = registry.list_backends(backend_type=BackendType.MODEL_PROVIDER)
        assert len(by_type) == 1
        
        # Filter by status
        by_status = registry.list_backends(status=BackendStatus.HEALTHY)
        assert len(by_status) == 1
        
        # Filter by provider
        by_provider = registry.list_backends(provider="Native")
        assert len(by_provider) == 1
        
        # No match
        no_match = registry.list_backends(provider="NonExistent")
        assert len(no_match) == 0
    
    def test_health_update(self, registry, native_provider):
        """Test health status update."""
        backend_id = registry.register(native_provider)
        
        registry.update_health(backend_id, BackendStatus.DEGRADED, "Test degradation")
        
        descriptor = registry.get_descriptor(backend_id)
        assert descriptor.status == BackendStatus.DEGRADED
    
    def test_run_health_checks(self, registry, native_provider):
        """Test running health checks on all backends."""
        registry.register(native_provider)
        
        results = registry.run_health_checks()
        assert len(results) == 1
        
        backend_id, (healthy, message) = list(results.items())[0]
        assert healthy is True
        assert "operational" in message
    
    def test_selection_history(self, registry, native_provider):
        """Test selection history tracking."""
        registry.register(native_provider)
        
        # Make several selections
        for _ in range(5):
            registry.select_backend()
        
        history = registry.get_selection_history()
        assert len(history) == 5
    
    def test_to_dict_export(self, registry, native_provider):
        """Test registry state export."""
        registry.register(native_provider)
        
        state = registry.to_dict()
        assert "backend_count" in state
        assert state["backend_count"] == 1
        assert "backends" in state
        assert "recent_selections" in state


class TestNativeBackendProvider:
    """Test NativeBackendProvider implementation."""
    
    def test_provider_properties(self, native_provider):
        """Test provider property accessors."""
        assert native_provider.provider_name == "Native"
        assert native_provider.backend_type == BackendType.MODEL_PROVIDER
    
    def test_capabilities(self, native_provider):
        """Test capability reporting."""
        caps = native_provider.get_capabilities()
        assert "planning" in caps
        assert "recovery" in caps
    
    def test_models(self, native_provider):
        """Test model listing."""
        models = native_provider.get_models()
        assert len(models) == 1
        assert models[0] == "test-model"
    
    def test_execute_plan(self, native_provider):
        """Test plan execution."""
        goal = Goal(task_id="test-001", text="List files in workspace")
        from evo_agent.models import Plan
        
        result = native_provider.execute_plan(goal, Plan(goal.task_id, []), {})
        assert "plan" in result
        assert result["provider"] == "Native"
    
    def test_health_check(self, native_provider):
        """Test health check."""
        healthy, message = native_provider.health_check()
        assert healthy is True
        assert "operational" in message
    
    def test_descriptor_generation(self, native_provider):
        """Test descriptor generation."""
        desc = native_provider.get_descriptor("test-id", "1.0.0")
        assert desc.backend_id == "test-id"
        assert desc.version == "1.0.0"
        assert desc.provider == "Native"


class TestBackendDescriptor:
    """Test BackendDescriptor dataclass."""
    
    def test_to_dict(self):
        """Test descriptor serialization."""
        desc = BackendDescriptor(
            backend_id="test-001",
            name="Test Backend",
            backend_type=BackendType.MODEL_PROVIDER,
            provider="TestProvider",
            version="1.0.0",
            capabilities=["cap1", "cap2"],
        )
        
        d = desc.to_dict()
        assert d["backend_id"] == "test-001"
        assert d["status"] == "registered"
        assert d["backend_type"] == "model_provider"


class TestBackendSelection:
    """Test BackendSelection dataclass."""
    
    def test_selection_creation(self):
        """Test selection object creation."""
        sel = BackendSelection(
            backend_id="be-001",
            name="Test Backend",
            provider="TestProvider",
            reason="Best match",
            confidence=0.95,
            alternatives=["be-002", "be-003"],
        )
        
        assert sel.backend_id == "be-001"
        assert sel.confidence == 0.95
        assert len(sel.alternatives) == 2


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
