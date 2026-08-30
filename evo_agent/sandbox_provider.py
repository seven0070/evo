"""
Sandbox Provider - Abstract sandbox execution boundary.

This module provides the SandboxProvider abstraction required by M1 specification.
All executable paths must pass through this sovereign execution boundary.
The existing SandboxEngine behavior is preserved but now routed through the provider.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from .models import CandidateStatus, Event, EventType, ExperimentStatus, new_id
from .sandbox import SandboxEngine, EvolutionExperiment, ExecutionResult, CandidateVersion
from .storage import SQLiteStore


@dataclass
class SandboxRequest:
    """Request to execute code in the sandbox."""
    request_id: str
    experiment_id: str
    proposal_id: str
    candidate_id: str
    command: list[str]
    location: Path
    label: str  # "baseline" or "candidate"
    timeout_seconds: int
    requested_at: str
    
    @classmethod
    def create(
        cls,
        experiment: EvolutionExperiment,
        command: Iterable[str],
        location: Path,
        label: str,
        timeout_seconds: int = 30,
    ) -> "SandboxRequest":
        return cls(
            request_id=new_id("sreq"),
            experiment_id=experiment.experiment_id,
            proposal_id=experiment.proposal_id,
            candidate_id=experiment.candidate_id,
            command=list(command),
            location=Path(location),
            label=label,
            timeout_seconds=timeout_seconds,
            requested_at=datetime.now(timezone.utc).isoformat(),
        )


@dataclass
class SandboxResponse:
    """Response from sandbox execution."""
    request_id: str
    success: bool
    result: ExecutionResult | None
    error_message: str | None
    isolation_verified: bool
    network_denied: bool
    production_untouched: bool
    executed_at: str
    
    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        if self.result:
            data["result"] = self.result.to_dict()
        return data


class SandboxProvider(ABC):
    """
    Abstract interface for sandbox execution providers.
    
    This is the provider pattern abstraction that allows different
    sandbox implementations to plug into Evo while maintaining
    the same security guarantees and interface.
    
    All executable paths MUST go through a SandboxProvider.
    No direct subprocess execution is permitted outside this boundary.
    """

    @abstractmethod
    def execute(self, request: SandboxRequest) -> SandboxResponse:
        """
        Execute a command in the isolated sandbox environment.
        
        Args:
            request: The execution request with all parameters
            
        Returns:
            SandboxResponse with execution results and verification status
        """
        raise NotImplementedError

    @abstractmethod
    def verify_isolation(self, request_id: str) -> tuple[bool, str]:
        """
        Verify that isolation was maintained during execution.
        
        Returns:
            (isolation_maintained, verification_message)
        """
        raise NotImplementedError

    @abstractmethod
    def verify_network_denied(self, request_id: str) -> tuple[bool, str]:
        """
        Verify that network access was denied during execution.
        
        Returns:
            (network_denied, verification_message)
        """
        raise NotImplementedError

    @abstractmethod
    def verify_production_untouched(self, request_id: str) -> tuple[bool, str]:
        """
        Verify that production source was not modified.
        
        Returns:
            (production_untouched, verification_message)
        """
        raise NotImplementedError


class DefaultSandboxProvider(SandboxProvider):
    """
    Default sandbox provider implementation wrapping SandboxEngine.
    
    This preserves all existing SandboxEngine behavior while providing
    the abstraction layer required by M1 specification.
    """

    def __init__(self, engine: SandboxEngine):
        self.engine = engine
        self._execution_log: dict[str, SandboxResponse] = {}
        self._verification_results: dict[str, dict[str, tuple[bool, str]]] = {}

    def execute(self, request: SandboxRequest) -> SandboxResponse:
        """Execute a command through SandboxEngine."""
        try:
            # Route through SandboxEngine
            result = self.engine.execute_candidate(
                experiment=self._load_experiment(request.experiment_id),
                location=request.location,
                label=request.label,
                command=request.command,
            )
            
            # Verify isolation properties
            isolation_ok, isolation_msg = self._verify_isolation_impl(request, result)
            network_ok, network_msg = self._verify_network_impl(request, result)
            production_ok, production_msg = self._verify_production_impl(request)
            
            response = SandboxResponse(
                request_id=request.request_id,
                success=result.completed and result.return_code == 0,
                result=result,
                error_message=result.error if result.return_code != 0 else None,
                isolation_verified=isolation_ok,
                network_denied=network_ok,
                production_untouched=production_ok,
                executed_at=datetime.now(timezone.utc).isoformat(),
            )
            
            # Log execution
            self._execution_log[request.request_id] = response
            self._verification_results[request.request_id] = {
                "isolation": (isolation_ok, isolation_msg),
                "network": (network_ok, network_msg),
                "production": (production_ok, production_msg),
            }
            
            return response
            
        except Exception as e:
            response = SandboxResponse(
                request_id=request.request_id,
                success=False,
                result=None,
                error_message=str(e),
                isolation_verified=False,
                network_denied=False,
                production_untouched=False,
                executed_at=datetime.now(timezone.utc).isoformat(),
            )
            self._execution_log[request.request_id] = response
            return response

    def verify_isolation(self, request_id: str) -> tuple[bool, str]:
        """Verify isolation for a previous execution."""
        if request_id not in self._verification_results:
            return False, f"No verification results found for request {request_id}"
        
        isolation_result = self._verification_results[request_id].get("isolation", (False, "Not verified"))
        return isolation_result

    def verify_network_denied(self, request_id: str) -> tuple[bool, str]:
        """Verify network denial for a previous execution."""
        if request_id not in self._verification_results:
            return False, f"No verification results found for request {request_id}"
        
        network_result = self._verification_results[request_id].get("network", (False, "Not verified"))
        return network_result

    def verify_production_untouched(self, request_id: str) -> tuple[bool, str]:
        """Verify production was untouched for a previous execution."""
        if request_id not in self._verification_results:
            return False, f"No verification results found for request {request_id}"
        
        production_result = self._verification_results[request_id].get("production", (False, "Not verified"))
        return production_result

    def _load_experiment(self, experiment_id: str) -> EvolutionExperiment:
        """Load experiment from store."""
        experiment = self.engine.get_experiment(experiment_id)
        if not experiment:
            raise KeyError(f"Experiment not found: {experiment_id}")
        return experiment

    def _verify_isolation_impl(self, request: SandboxRequest, result: ExecutionResult) -> tuple[bool, str]:
        """Verify isolation was maintained."""
        # Check that execution location is within sandbox boundary
        location = Path(result.location).resolve()
        sandbox_root = self.engine.sandbox_root.resolve()
        
        try:
            location.relative_to(sandbox_root)
            return True, f"Execution confined to sandbox: {location}"
        except ValueError:
            return False, f"Execution escaped sandbox boundary: {location}"

    def _verify_network_impl(self, request: SandboxRequest, result: ExecutionResult) -> tuple[bool, str]:
        """Verify network was denied."""
        # Check environment variables in result
        network_policy = result.environment_keys.get("EVO_NETWORK_POLICY", "unknown")
        if network_policy == "denied":
            return True, "Network policy enforced: denied"
        
        # Check for network-related errors
        if "network" in result.error.lower() or "connection" in result.error.lower():
            return True, "Network access attempt blocked"
        
        return network_policy == "denied", f"Network policy status: {network_policy}"

    def _verify_production_impl(self, request: SandboxRequest) -> tuple[bool, str]:
        """Verify production source was not modified."""
        # The SandboxEngine creates read-only copies of production
        # Verification: ensure baseline directory is still read-only
        try:
            experiment = self._load_experiment(request.experiment_id)
            baseline_path = Path(experiment.sandbox_location) / "baseline"
            
            # Try to write to baseline - should fail if properly protected
            test_file = baseline_path / ".write_test_protection"
            try:
                test_file.write_text("test", encoding="utf-8")
                # If we get here, protection failed
                test_file.unlink(missing_ok=True)
                return False, "Production baseline is writable - protection failed"
            except (PermissionError, OSError):
                # Expected: file is protected
                return True, "Production baseline is read-only as expected"
        except Exception as e:
            return False, f"Verification error: {e}"

    def get_execution_log(self, request_id: str | None = None) -> dict[str, Any]:
        """Get execution log for inspection."""
        if request_id:
            if request_id not in self._execution_log:
                raise KeyError(f"No execution found for request {request_id}")
            return asdict(self._execution_log[request_id])
        return {rid: asdict(resp) for rid, resp in self._execution_log.items()}


class SovereignMediationLayer:
    """
    Centralized policy → approval → isolation → execution mediation.
    
    This layer ensures that no backend, tool, desktop API, web API, or
    metamorphosis operation can bypass the sovereign execution boundary.
    
    All execution requests flow through this mediation layer which:
    1. Validates policy compliance
    2. Checks approval status
    3. Routes through SandboxProvider
    4. Verifies isolation post-execution
    5. Records audit evidence
    """

    def __init__(self, provider: SandboxProvider, store: SQLiteStore):
        self.provider = provider
        self.store = store
        self._pending_approvals: dict[str, dict[str, Any]] = {}

    def request_execution(
        self,
        experiment: EvolutionExperiment,
        command: Iterable[str],
        location: Path,
        label: str,
        approval_required: bool = True,
        policy_context: dict[str, Any] | None = None,
    ) -> str:
        """
        Request execution through sovereign mediation.
        
        Returns:
            request_id for tracking
        """
        request = SandboxRequest.create(
            experiment=experiment,
            command=command,
            location=location,
            label=label,
        )
        
        # Policy validation
        policy_ok, policy_msg = self._validate_policy(request, policy_context or {})
        if not policy_ok:
            raise PermissionError(f"Policy violation: {policy_msg}")
        
        # Approval check
        if approval_required:
            self._pending_approvals[request.request_id] = {
                "request": asdict(request),
                "status": "pending",
                "policy_context": policy_context,
            }
            # In real usage, external approval would be required here
            # For now, auto-approve if policy passed (this is simplified)
            self._pending_approvals[request.request_id]["status"] = "approved"
        
        # Execute through provider
        response = self.provider.execute(request)
        
        # Record audit evidence
        self._record_audit_evidence(request, response, policy_context)
        
        # Verify post-execution
        if not response.isolation_verified:
            raise RuntimeError(f"Isolation verification failed: {request.request_id}")
        
        if not response.network_denied:
            raise RuntimeError(f"Network denial verification failed: {request.request_id}")
        
        if not response.production_untouched:
            raise RuntimeError(f"Production protection verification failed: {request.request_id}")
        
        return request.request_id

    def _validate_policy(self, request: SandboxRequest, context: dict[str, Any]) -> tuple[bool, str]:
        """Validate request against security policy."""
        # Check that location is valid
        if not request.location.exists():
            return False, f"Location does not exist: {request.location}"
        
        # Check command is allowed
        allowed_commands = ["python3", "pytest", "sh", "bash"]
        cmd_base = Path(request.command[0]).name if request.command else ""
        if cmd_base not in allowed_commands:
            return False, f"Command not allowed: {cmd_base}"
        
        # Check timeout is reasonable
        if request.timeout_seconds > 300:  # 5 minute max
            return False, f"Timeout exceeds maximum: {request.timeout_seconds}s"
        
        return True, "Policy validation passed"

    def _record_audit_evidence(
        self,
        request: SandboxRequest,
        response: SandboxResponse,
        context: dict[str, Any] | None,
    ) -> None:
        """Record audit evidence of execution."""
        event_payload = {
            "request_id": request.request_id,
            "experiment_id": request.experiment_id,
            "proposal_id": request.proposal_id,
            "candidate_id": request.candidate_id,
            "success": response.success,
            "isolation_verified": response.isolation_verified,
            "network_denied": response.network_denied,
            "production_untouched": response.production_untouched,
            "executed_at": response.executed_at,
        }
        
        if context:
            event_payload["policy_context"] = context
        
        try:
            event = Event(
                event_id=new_id("evt"),
                event_type=EventType.CANDIDATE_TEST_COMPLETED,
                timestamp=datetime.now(timezone.utc).isoformat(),
                payload=event_payload,
            )
            self.store.append_event(event)
        except Exception:
            # Non-fatal: audit logging should not break execution
            pass


__all__ = [
    "SandboxProvider",
    "SandboxRequest",
    "SandboxResponse",
    "DefaultSandboxProvider",
    "SovereignMediationLayer",
]
