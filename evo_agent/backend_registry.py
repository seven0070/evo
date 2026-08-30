"""
Backend Registry - Authoritative runtime backend selection and routing.

This module provides the BackendRegistry abstraction required by M1 specification.
All model/backend execution routes through this registry, enabling pluggable
backend implementations including DeerFlow and DeepSeek Harness.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable

from .models import Event, EventType, Goal, Plan, ToolResult, new_id
from .storage import SQLiteStore


class BackendStatus(str, Enum):
    """Lifecycle status for registered backends."""
    REGISTERED = "registered"
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"
    DISABLED = "disabled"


class BackendType(str, Enum):
    """Categories of backends supported by the registry."""
    MODEL_PROVIDER = "model_provider"
    SPECIALIST = "specialist"
    CAPABILITY = "capability"
    EXTERNAL = "external"


@dataclass
class BackendDescriptor:
    """Describes a registered backend's capabilities and metadata."""
    backend_id: str
    name: str
    backend_type: BackendType
    provider: str  # e.g., "DeerFlow", "DeepSeek", "OpenAI", "native"
    version: str
    status: BackendStatus = BackendStatus.REGISTERED
    capabilities: list[str] = field(default_factory=list)
    models: list[str] = field(default_factory=list)
    endpoints: dict[str, str] = field(default_factory=dict)
    health_check_url: str | None = None
    last_health_check: str | None = None
    registered_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["status"] = self.status.value
        data["backend_type"] = self.backend_type.value
        return data


@dataclass
class BackendSelection:
    """Result of backend selection process."""
    backend_id: str
    name: str
    provider: str
    reason: str
    confidence: float
    alternatives: list[str] = field(default_factory=list)
    selected_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class BackendProvider(ABC):
    """
    Abstract interface that all backends must implement.
    
    This is the provider pattern abstraction that allows different
    backend implementations (DeerFlow, DeepSeek, OpenAI, etc.) to
    plug into the Evo runtime through a common interface.
    """

    @property
    @abstractmethod
    def provider_name(self) -> str:
        """Return the unique provider name (e.g., 'DeerFlow', 'DeepSeek')."""
        raise NotImplementedError

    @property
    @abstractmethod
    def backend_type(self) -> BackendType:
        """Return the type of this backend."""
        raise NotImplementedError

    @abstractmethod
    def get_capabilities(self) -> list[str]:
        """Return list of capabilities this backend provides."""
        raise NotImplementedError

    @abstractmethod
    def get_models(self) -> list[str]:
        """Return list of models/endpoints available."""
        raise NotImplementedError

    @abstractmethod
    def execute_plan(self, goal: Goal, plan: Plan, context: dict[str, Any]) -> dict[str, Any]:
        """Execute a plan using this backend."""
        raise NotImplementedError

    @abstractmethod
    def health_check(self) -> tuple[bool, str]:
        """Perform health check, return (healthy, message)."""
        raise NotImplementedError

    @abstractmethod
    def get_descriptor(self, backend_id: str, version: str) -> BackendDescriptor:
        """Create a descriptor for registry registration."""
        raise NotImplementedError


class BackendRegistry:
    """
    Authoritative registry for backend selection and routing.
    
    The BackendRegistry is the single point of truth for:
    - Backend registration and lifecycle management
    - Backend selection based on capability requirements
    - Health monitoring and failover
    - Routing execution requests to appropriate backends
    
    All model/provider execution MUST route through this registry.
    No direct backend instantiation is permitted outside this module.
    """

    def __init__(self, store: SQLiteStore):
        self.store = store
        self._backends: dict[str, BackendProvider] = {}
        self._descriptors: dict[str, BackendDescriptor] = {}
        self._selection_history: list[BackendSelection] = []
        self._health_callbacks: dict[str, Callable[[str, BackendStatus], None]] = {}

    def register(self, provider: BackendProvider, backend_id: str | None = None) -> str:
        """
        Register a backend provider with the registry.
        
        Args:
            provider: The backend provider instance
            backend_id: Optional explicit ID; if None, generated from provider
            
        Returns:
            The backend_id used for registration
            
        Raises:
            ValueError: If backend_id already exists
        """
        if backend_id is None:
            backend_id = f"{provider.provider_name.lower()}_{new_id('be')}"
        
        if backend_id in self._backends:
            raise ValueError(f"Backend {backend_id} already registered")
        
        # Get descriptor from provider
        version = provider.get_descriptor(backend_id, "1.0.0").version
        descriptor = provider.get_descriptor(backend_id, version)
        
        # Validate uniqueness
        if descriptor.provider in ["DeerFlow", "DeepSeek"]:
            # Ensure only one instance per major provider
            for existing in self._descriptors.values():
                if existing.provider == descriptor.provider and existing.backend_type == descriptor.backend_type:
                    raise ValueError(f"Provider {descriptor.provider} already registered")
        
        self._backends[backend_id] = provider
        self._descriptors[backend_id] = descriptor
        
        # Persist registration event
        self._emit_event(EventType.EXTERNAL_INTEGRATION_REGISTERED, {
            "backend_id": backend_id,
            "provider": descriptor.provider,
            "type": descriptor.backend_type.value,
            "capabilities": descriptor.capabilities,
        })
        
        # Initial health check
        healthy, message = provider.health_check()
        descriptor.status = BackendStatus.HEALTHY if healthy else BackendStatus.UNHEALTHY
        descriptor.last_health_check = datetime.now(timezone.utc).isoformat()
        
        return backend_id

    def unregister(self, backend_id: str) -> None:
        """Remove a backend from the registry."""
        if backend_id not in self._backends:
            raise KeyError(f"Backend {backend_id} not found")
        
        descriptor = self._descriptors[backend_id]
        descriptor.status = BackendStatus.DISABLED
        
        del self._backends[backend_id]
        del self._descriptors[backend_id]
        
        self._emit_event(EventType.PROVIDER_STATE_CHANGED, {
            "backend_id": backend_id,
            "status": BackendStatus.DISABLED.value,
        })

    def select_backend(
        self,
        required_capabilities: list[str] | None = None,
        preferred_provider: str | None = None,
        backend_type: BackendType | None = None,
    ) -> BackendSelection:
        """
        Select the best backend for given requirements.
        
        Selection criteria (in priority order):
        1. Required capabilities must be satisfied
        2. Preferred provider if specified and healthy
        3. Health status (healthy > degraded > unhealthy)
        4. Capability match score
        5. Registration recency (tiebreaker)
        
        Args:
            required_capabilities: List of required capability names
            preferred_provider: Optional preferred provider name
            backend_type: Optional filter by backend type
            
        Returns:
            BackendSelection with chosen backend and rationale
            
        Raises:
            RuntimeError: If no suitable backend found
        """
        candidates = []
        
        for backend_id, descriptor in self._descriptors.items():
            # Skip disabled/unhealthy unless no alternatives
            if descriptor.status in [BackendStatus.DISABLED, BackendStatus.UNHEALTHY]:
                continue
            
            # Filter by type if specified
            if backend_type is not None and descriptor.backend_type != backend_type:
                continue
            
            # Calculate capability match
            if required_capabilities:
                matched = sum(1 for cap in required_capabilities if cap in descriptor.capabilities)
                if matched == 0:
                    continue  # No capability match
                match_score = matched / len(required_capabilities)
            else:
                match_score = 1.0
            
            # Provider preference bonus
            provider_bonus = 0.2 if preferred_provider and descriptor.provider == preferred_provider else 0.0
            
            # Health bonus
            health_scores = {
                BackendStatus.HEALTHY: 1.0,
                BackendStatus.DEGRADED: 0.5,
                BackendStatus.REGISTERED: 0.3,
            }
            health_bonus = health_scores.get(descriptor.status, 0.0)
            
            total_score = match_score + provider_bonus + health_bonus
            candidates.append((backend_id, descriptor, total_score))
        
        if not candidates:
            raise RuntimeError(
                f"No backend available for capabilities={required_capabilities}, "
                f"provider={preferred_provider}, type={backend_type}"
            )
        
        # Sort by score descending
        candidates.sort(key=lambda x: x[2], reverse=True)
        
        best_id, best_desc, best_score = candidates[0]
        alternatives = [c[0] for c in candidates[1:3]]  # Top 3 alternatives
        
        selection = BackendSelection(
            backend_id=best_id,
            name=best_desc.name,
            provider=best_desc.provider,
            reason=f"Best match for capabilities={required_capabilities or 'any'}",
            confidence=min(best_score, 1.0),
            alternatives=alternatives,
        )
        
        self._selection_history.append(selection)
        return selection

    def get_backend(self, backend_id: str) -> BackendProvider:
        """Retrieve a registered backend by ID."""
        if backend_id not in self._backends:
            raise KeyError(f"Backend {backend_id} not found")
        return self._backends[backend_id]

    def get_descriptor(self, backend_id: str) -> BackendDescriptor:
        """Get the descriptor for a registered backend."""
        if backend_id not in self._descriptors:
            raise KeyError(f"Backend {backend_id} not found")
        return self._descriptors[backend_id]

    def list_backends(
        self,
        backend_type: BackendType | None = None,
        status: BackendStatus | None = None,
        provider: str | None = None,
    ) -> list[BackendDescriptor]:
        """List registered backends with optional filters."""
        results = []
        for desc in self._descriptors.values():
            if backend_type and desc.backend_type != backend_type:
                continue
            if status and desc.status != status:
                continue
            if provider and desc.provider != provider:
                continue
            results.append(desc)
        return results

    def update_health(self, backend_id: str, status: BackendStatus, message: str = "") -> None:
        """Update backend health status."""
        if backend_id not in self._descriptors:
            raise KeyError(f"Backend {backend_id} not found")
        
        descriptor = self._descriptors[backend_id]
        old_status = descriptor.status
        descriptor.status = status
        descriptor.last_health_check = datetime.now(timezone.utc).isoformat()
        
        if old_status != status:
            self._emit_event(EventType.PROVIDER_STATE_CHANGED, {
                "backend_id": backend_id,
                "old_status": old_status.value,
                "new_status": status.value,
                "message": message,
            })
            
            # Notify callbacks
            if backend_id in self._health_callbacks:
                self._health_callbacks[backend_id](backend_id, status)

    def run_health_checks(self) -> dict[str, tuple[bool, str]]:
        """Run health checks on all registered backends."""
        results = {}
        for backend_id, provider in self._backends.items():
            healthy, message = provider.health_check()
            status = BackendStatus.HEALTHY if healthy else BackendStatus.DEGRADED
            self.update_health(backend_id, status, message)
            results[backend_id] = (healthy, message)
        return results

    def register_health_callback(self, backend_id: str, callback: Callable[[str, BackendStatus], None]) -> None:
        """Register a callback for health status changes."""
        self._health_callbacks[backend_id] = callback

    def _emit_event(self, event_type: EventType, payload: dict[str, Any]) -> None:
        """Emit an audit event to the store."""
        try:
            event = Event(
                event_id=new_id("evt"),
                event_type=event_type,
                timestamp=datetime.now(timezone.utc).isoformat(),
                payload=payload,
            )
            self.store.append_event(event)
        except Exception:
            # Non-fatal: event logging should not break registry operations
            pass

    def get_selection_history(self, limit: int = 100) -> list[BackendSelection]:
        """Get recent backend selection history."""
        return self._selection_history[-limit:]

    def to_dict(self) -> dict[str, Any]:
        """Export registry state for inspection."""
        return {
            "backend_count": len(self._backends),
            "backends": {bid: desc.to_dict() for bid, desc in self._descriptors.items()},
            "recent_selections": [asdict(s) for s in self._selection_history[-10:]],
        }


# ============================================================================
# Native Backend Provider - Default implementation using ModelAdapter
# ============================================================================

class NativeBackendProvider(BackendProvider):
    """
    Native backend provider wrapping the existing ModelAdapter pattern.
    
    This provides backward compatibility while routing through BackendRegistry.
    """

    def __init__(self, adapter, model_name: str = "default"):
        self._adapter = adapter
        self._model_name = model_name

    @property
    def provider_name(self) -> str:
        return "Native"

    @property
    def backend_type(self) -> BackendType:
        return BackendType.MODEL_PROVIDER

    def get_capabilities(self) -> list[str]:
        return ["planning", "recovery", "text_completion"]

    def get_models(self) -> list[str]:
        return [self._model_name]

    def execute_plan(self, goal: Goal, plan: Plan, context: dict[str, Any]) -> dict[str, Any]:
        # Delegate to the wrapped adapter
        result_plan = self._adapter.create_plan(goal, [], context.get("tool_schemas", []))
        return {
            "plan": result_plan,
            "model": self._model_name,
            "provider": "Native",
        }

    def health_check(self) -> tuple[bool, str]:
        # Native adapter is always healthy (no external dependencies)
        return True, "Native adapter operational"

    def get_descriptor(self, backend_id: str, version: str) -> BackendDescriptor:
        return BackendDescriptor(
            backend_id=backend_id,
            name="Native Model Adapter",
            backend_type=BackendType.MODEL_PROVIDER,
            provider="Native",
            version=version,
            capabilities=self.get_capabilities(),
            models=self.get_models(),
        )


__all__ = [
    "BackendRegistry",
    "BackendProvider",
    "BackendDescriptor",
    "BackendSelection",
    "BackendStatus",
    "BackendType",
    "NativeBackendProvider",
]
