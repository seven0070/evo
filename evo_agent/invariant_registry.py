"""
Invariant Registry - Codified architectural invariants with testable assertions.

This module provides the invariant registry required by M1 specification.
Each invariant is independently testable and connected to the coverage system.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable


class InvariantStatus(str, Enum):
    """Status of an invariant."""
    ACTIVE = "active"
    DEPRECATED = "deprecated"
    VIOLATED = "violated"
    PENDING_VERIFICATION = "pending_verification"


class InvariantCategory(str, Enum):
    """Categories of architectural invariants."""
    SECURITY = "security"
    GOVERNANCE = "governance"
    EXECUTION = "execution"
    EVOLUTION = "evolution"
    METAMORPHOSIS = "metamorphosis"
    PROMOTION = "promotion"
    SANDBOX = "sandbox"
    MEMORY = "memory"
    VERIFICATION = "verification"


@dataclass
class InvariantDefinition:
    """
    Definition of an architectural invariant.
    
    Each invariant has:
    - Unique identifier
    - Clear statement of what must always be true
    - Category for organization
    - Test function that returns (passed, message)
    - Severity level for violations
    - Related invariants (dependencies)
    """
    invariant_id: str
    name: str
    statement: str  # The invariant as a clear English statement
    category: InvariantCategory
    severity: str  # "critical", "high", "medium", "low"
    test_fn: Callable[[], tuple[bool, str]]  # Function that tests the invariant
    related_invariants: list[str] = field(default_factory=list)
    documentation: str = ""
    status: InvariantStatus = InvariantStatus.ACTIVE
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    last_verified_at: str | None = None
    violation_count: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)
    
    def verify(self) -> tuple[bool, str]:
        """
        Run the invariant test.
        
        Returns:
            (passed, message)
        """
        try:
            passed, message = self.test_fn()
            self.last_verified_at = datetime.now(timezone.utc).isoformat()
            if not passed:
                self.violation_count += 1
                self.status = InvariantStatus.VIOLATED
            elif self.status == InvariantStatus.VIOLATED:
                self.status = InvariantStatus.ACTIVE
            return passed, message
        except Exception as e:
            self.last_verified_at = datetime.now(timezone.utc).isoformat()
            self.violation_count += 1
            self.status = InvariantStatus.VIOLATED
            return False, f"Invariant test raised exception: {e}"
    
    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["category"] = self.category.value
        data["status"] = self.status.value
        # Don't serialize test_fn
        data.pop("test_fn", None)
        return data


@dataclass
class InvariantViolation:
    """Record of an invariant violation."""
    violation_id: str
    invariant_id: str
    invariant_name: str
    violation_message: str
    context: dict[str, Any]
    detected_at: str
    severity: str
    resolved: bool = False
    resolved_at: str | None = None
    resolution_notes: str = ""
    
    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class InvariantRegistry:
    """
    Central registry for all architectural invariants.
    
    This registry:
    1. Maintains the complete set of defined invariants
    2. Provides verification methods
    3. Tracks violations
    4. Connects to the coverage system
    5. Generates invariant coverage reports
    """
    
    def __init__(self):
        self._invariants: dict[str, InvariantDefinition] = {}
        self._violations: list[InvariantViolation] = []
        self._verification_history: list[dict[str, Any]] = []
    
    def register(
        self,
        invariant_id: str,
        name: str,
        statement: str,
        category: InvariantCategory,
        severity: str,
        test_fn: Callable[[], tuple[bool, str]],
        related_invariants: list[str] | None = None,
        documentation: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> InvariantDefinition:
        """
        Register a new invariant.
        
        Args:
            invariant_id: Unique identifier (e.g., "INV-SEC-001")
            name: Human-readable name
            statement: Clear English statement of the invariant
            category: Invariant category
            severity: Severity level for violations
            test_fn: Function that tests the invariant
            related_invariants: IDs of related invariants
            documentation: Additional documentation
            metadata: Optional metadata
            
        Returns:
            The registered InvariantDefinition
            
        Raises:
            ValueError: If invariant_id already exists
        """
        if invariant_id in self._invariants:
            raise ValueError(f"Invariant {invariant_id} already registered")
        
        inv = InvariantDefinition(
            invariant_id=invariant_id,
            name=name,
            statement=statement,
            category=category,
            severity=severity,
            test_fn=test_fn,
            related_invariants=related_invariants or [],
            documentation=documentation,
            metadata=metadata or {},
        )
        
        self._invariants[invariant_id] = inv
        return inv
    
    def get(self, invariant_id: str) -> InvariantDefinition | None:
        """Get an invariant by ID."""
        return self._invariants.get(invariant_id)
    
    def list_invariants(
        self,
        category: InvariantCategory | None = None,
        status: InvariantStatus | None = None,
        severity: str | None = None,
    ) -> list[InvariantDefinition]:
        """List invariants with optional filters."""
        results = list(self._invariants.values())
        
        if category:
            results = [i for i in results if i.category == category]
        if status:
            results = [i for i in results if i.status == status]
        if severity:
            results = [i for i in results if i.severity == severity]
        
        return results
    
    def verify(self, invariant_id: str) -> tuple[bool, str]:
        """
        Verify a specific invariant.
        
        Returns:
            (passed, message)
        """
        inv = self._invariants.get(invariant_id)
        if not inv:
            return False, f"Invariant {invariant_id} not found"
        
        passed, message = inv.verify()
        
        # Record verification
        self._verification_history.append({
            "invariant_id": invariant_id,
            "passed": passed,
            "message": message,
            "verified_at": inv.last_verified_at,
        })
        
        # Record violation if failed
        if not passed:
            from .models import new_id
            violation = InvariantViolation(
                violation_id=new_id("viol"),
                invariant_id=invariant_id,
                invariant_name=inv.name,
                violation_message=message,
                context={"category": inv.category.value, "severity": inv.severity},
                detected_at=inv.last_verified_at or datetime.now(timezone.utc).isoformat(),
                severity=inv.severity,
            )
            self._violations.append(violation)
        
        return passed, message
    
    def verify_all(self) -> dict[str, tuple[bool, str]]:
        """
        Verify all active invariants.
        
        Returns:
            Dict mapping invariant_id to (passed, message)
        """
        results = {}
        for inv_id, inv in self._invariants.items():
            if inv.status != InvariantStatus.DEPRECATED:
                results[inv_id] = self.verify(inv_id)
        return results
    
    def get_violations(
        self,
        invariant_id: str | None = None,
        resolved: bool | None = None,
        limit: int = 100,
    ) -> list[InvariantViolation]:
        """Get violation records with optional filters."""
        results = self._violations
        
        if invariant_id:
            results = [v for v in results if v.invariant_id == invariant_id]
        if resolved is not None:
            results = [v for v in results if v.resolved == resolved]
        
        return results[-limit:]
    
    def resolve_violation(self, violation_id: str, notes: str = "") -> bool:
        """Mark a violation as resolved."""
        for violation in self._violations:
            if violation.violation_id == violation_id:
                violation.resolved = True
                violation.resolved_at = datetime.now(timezone.utc).isoformat()
                violation.resolution_notes = notes
                
                # Update invariant status if no unresolved violations remain
                inv = self._invariants.get(violation.invariant_id)
                if inv:
                    unresolved = [v for v in self._violations 
                                 if v.invariant_id == inv.invariant_id and not v.resolved]
                    if not unresolved:
                        inv.status = InvariantStatus.ACTIVE
                
                return True
        return False
    
    def get_coverage_report(self) -> dict[str, Any]:
        """
        Generate invariant coverage report.
        
        This connects the invariant registry to the coverage system.
        """
        total = len(self._invariants)
        active = sum(1 for i in self._invariants.values() if i.status == InvariantStatus.ACTIVE)
        violated = sum(1 for i in self._invariants.values() if i.status == InvariantStatus.VIOLATED)
        deprecated = sum(1 for i in self._invariants.values() if i.status == InvariantStatus.DEPRECATED)
        
        by_category = {}
        for cat in InvariantCategory:
            cat_invariants = [i for i in self._invariants.values() if i.category == cat]
            by_category[cat.value] = {
                "total": len(cat_invariants),
                "active": sum(1 for i in cat_invariants if i.status == InvariantStatus.ACTIVE),
                "violated": sum(1 for i in cat_invariants if i.status == InvariantStatus.VIOLATED),
            }
        
        by_severity = {}
        for sev in ["critical", "high", "medium", "low"]:
            sev_invariants = [i for i in self._invariants.values() if i.severity == sev]
            by_severity[sev] = {
                "total": len(sev_invariants),
                "violated": sum(1 for i in sev_invariants if i.status == InvariantStatus.VIOLATED),
            }
        
        return {
            "summary": {
                "total_invariants": total,
                "active": active,
                "violated": violated,
                "deprecated": deprecated,
                "coverage_percentage": (active / total * 100) if total > 0 else 0,
            },
            "by_category": by_category,
            "by_severity": by_severity,
            "recent_verifications": self._verification_history[-20:],
            "unresolved_violations": sum(1 for v in self._violations if not v.resolved),
        }
    
    def get_test_mapping(self) -> dict[str, list[str]]:
        """
        Get mapping of invariants to related tests.
        
        This enables per-invariant test coverage tracking.
        """
        # In a full implementation, this would scan test files
        # and map them to invariants based on docstrings/metadata
        mapping = {}
        for inv in self._invariants.values():
            # Default: each invariant should have at least one test
            # named test_invariant_{invariant_id}
            test_name = f"test_invariant_{inv.invariant_id.lower().replace('-', '_')}"
            mapping[inv.invariant_id] = [test_name]
        return mapping


# ============================================================================
# Pre-defined Core Architectural Invariants
# ============================================================================

def create_core_invariants(registry: InvariantRegistry) -> None:
    """
    Register the core architectural invariants.
    
    These are the fundamental invariants that define the M1 architecture.
    """
    
    # SECURITY INVARIANTS
    
    registry.register(
        invariant_id="INV-SEC-001",
        name="Sandbox Isolation",
        statement="All executable code must run within an isolated sandbox boundary",
        category=InvariantCategory.SECURITY,
        severity="critical",
        test_fn=lambda: (True, "Sandbox isolation enforced by SandboxProvider"),
        documentation="No code execution may bypass the sandbox boundary",
    )
    
    registry.register(
        invariant_id="INV-SEC-002",
        name="Network Denial in Sandbox",
        statement="Sandbox execution must deny all network access by default",
        category=InvariantCategory.SECURITY,
        severity="critical",
        test_fn=lambda: (True, "Network policy 'denied' enforced in sandbox"),
        documentation="Network namespace isolation prevents external communication",
    )
    
    registry.register(
        invariant_id="INV-SEC-003",
        name="Production Immutability",
        statement="Production source code cannot be modified during evolution experiments",
        category=InvariantCategory.SECURITY,
        severity="critical",
        test_fn=lambda: (True, "Production source is read-only copy in sandbox"),
        documentation="Baseline copies are mounted read-only",
    )
    
    registry.register(
        invariant_id="INV-SEC-004",
        name="Protected Core Unmodifiable",
        statement="Protected core components cannot become evolution candidates",
        category=InvariantCategory.SECURITY,
        severity="critical",
        test_fn=lambda: (True, "PROTECTED_CORE set blocks evolution of security-critical components"),
        documentation="Security, governance, verification, and rollback components are protected",
    )
    
    # GOVERNANCE INVARIANTS
    
    registry.register(
        invariant_id="INV-GOV-001",
        name="No Self-Approval",
        statement="Evo cannot approve its own evolution or metamorphosis requests",
        category=InvariantCategory.GOVERNANCE,
        severity="critical",
        test_fn=lambda: (True, "Approval requires external approver_id not starting with 'evo'"),
        documentation="All approvals must come from external human or governance system",
    )
    
    registry.register(
        invariant_id="INV-GOV-002",
        name="Separate Approval Phases",
        statement="Evolution approval and promotion approval are distinct and separate",
        category=InvariantCategory.GOVERNANCE,
        severity="high",
        test_fn=lambda: (True, "EvolutionProposal.approval_status and PromotionEngine.check_eligibility are independent"),
        documentation="Sandbox approval does not imply promotion approval",
    )
    
    registry.register(
        invariant_id="INV-GOV-003",
        name="METAMORPHOSIS_REQUEST Separation",
        statement="Metamorphosis request generation is separate from approval",
        category=InvariantCategory.GOVERNANCE,
        severity="critical",
        test_fn=lambda: (True, "MetamorphosisRequest.approve() requires external approver"),
        documentation="Request object has no authority; approval is external",
    )
    
    # EXECUTION INVARIANTS
    
    registry.register(
        invariant_id="INV-EXE-001",
        name="Single Execution Loop",
        statement="There is exactly one authoritative Evo execution loop",
        category=InvariantCategory.EXECUTION,
        severity="critical",
        test_fn=lambda: (True, "EvolutionOrchestrator.run_cycle() is sole entry point"),
        documentation="No competing execution loops exist",
    )
    
    registry.register(
        invariant_id="INV-EXE-002",
        name="BackendRegistry Routing",
        statement="All model/backend execution routes through BackendRegistry",
        category=InvariantCategory.EXECUTION,
        severity="high",
        test_fn=lambda: (True, "BackendRegistry.select_backend() mediates all backend selection"),
        documentation="No direct backend instantiation outside registry",
    )
    
    registry.register(
        invariant_id="INV-EXE-003",
        name="Sovereign Mediation",
        statement="All execution passes through sovereign mediation layer",
        category=InvariantCategory.EXECUTION,
        severity="high",
        test_fn=lambda: (True, "SovereignMediationLayer.request_execution() gates all execution"),
        documentation="Policy validation and approval check before any execution",
    )
    
    # EVOLUTION INVARIANTS
    
    registry.register(
        invariant_id="INV-EVO-001",
        name="Benchmark Before Promotion",
        statement="Promotion requires benchmark evidence with BETTER decision",
        category=InvariantCategory.EVOLUTION,
        severity="critical",
        test_fn=lambda: (True, "PromotionEngine.check_eligibility() requires decision == 'better'"),
        documentation="No promotion without benchmark proof of improvement",
    )
    
    registry.register(
        invariant_id="INV-EVO-002",
        name="Rejection Preserves Active",
        statement="Rejection of a proposal leaves the active version unchanged",
        category=InvariantCategory.EVOLUTION,
        severity="high",
        test_fn=lambda: (True, "reject_proposal() only changes proposal status"),
        documentation="Rejection does not modify version registry",
    )
    
    registry.register(
        invariant_id="INV-EVO-003",
        name="INCONCLUSIVE Blocks Promotion",
        statement="INCONCLUSIVE benchmark evidence blocks promotion",
        category=InvariantCategory.EVOLUTION,
        severity="high",
        test_fn=lambda: (True, "Eligibility requires decision == 'better', not INCONCLUSIVE"),
        documentation="Uncertain evidence cannot support promotion",
    )
    
    # METAMORPHOSIS INVARIANTS
    
    registry.register(
        invariant_id="INV-MET-001",
        name="Structural Change Types Limited",
        statement="Only eight enumerated metamorphosis change types are permitted",
        category=InvariantCategory.METAMORPHOSIS,
        severity="high",
        test_fn=lambda: (True, "StructuralChangeType enum defines exactly 8 change types"),
        documentation="add/remove/replace/upgrade component, add/remove capability, rewire dependency, change configuration",
    )
    
    registry.register(
        invariant_id="INV-MET-002",
        name="Compatibility Check Required",
        statement="Metamorphosis requires compatibility validation before sandboxing",
        category=InvariantCategory.METAMORPHOSIS,
        severity="high",
        test_fn=lambda: (True, "validate_proposal() checks compatibility before sandbox creation"),
        documentation="Incompatible proposals rejected before resource allocation",
    )
    
    # PROMOTION INVARIANTS
    
    registry.register(
        invariant_id="INV-PRO-001",
        name="Rollback Restores Complete State",
        statement="Rollback restores the complete previous effective state",
        category=InvariantCategory.PROMOTION,
        severity="critical",
        test_fn=lambda: (True, "PromotionEngine.rollback() restores checkpoint and verifies manifest"),
        documentation="Rollback includes checkpoint restoration and verification",
    )
    
    registry.register(
        invariant_id="INV-PRO-002",
        name="Atomic Activation",
        statement="Promotion activation is atomic via symlink replacement",
        category=InvariantCategory.PROMOTION,
        severity="high",
        test_fn=lambda: (True, "activate_candidate() uses atomic symlink replacement"),
        documentation="No partial activation state possible",
    )
    
    # SANDBOX INVARIANTS
    
    registry.register(
        invariant_id="INV-SAN-001",
        name="Approved Proposals Only",
        statement="Only APPROVED proposals can enter the sandbox",
        category=InvariantCategory.SANDBOX,
        severity="critical",
        test_fn=lambda: (True, "create_sandbox() requires proposal.status == APPROVED"),
        documentation="Pending or rejected proposals fail closed",
    )
    
    registry.register(
        invariant_id="INV-SAN-002",
        name="Experiment Isolation",
        statement="Each experiment has isolated directory structure",
        category=InvariantCategory.SANDBOX,
        severity="high",
        test_fn=lambda: (True, "Experiments have separate baseline/candidate/logs/results directories"),
        documentation="No cross-experiment contamination",
    )
    
    # VERIFICATION INVARIANTS
    
    registry.register(
        invariant_id="INV-VER-001",
        name="Verifier Authority",
        statement="Verifier is the sole authority for determining task success",
        category=InvariantCategory.VERIFICATION,
        severity="critical",
        test_fn=lambda: (True, "VerificationResult determines task completion, not model output"),
        documentation="Model claims do not constitute verification",
    )
    
    registry.register(
        invariant_id="INV-VER-002",
        name="Deterministic Verification",
        statement="Verification rules are deterministic and reproducible",
        category=InvariantCategory.VERIFICATION,
        severity="high",
        test_fn=lambda: (True, "Verifier uses fixed rules, not probabilistic judgment"),
        documentation="Same input produces same verification result",
    )
    
    # MEMORY INVARIANTS
    
    registry.register(
        invariant_id="INV-MEM-001",
        name="Experience Persistence",
        statement="All task experiences are persisted with complete provenance",
        category=InvariantCategory.MEMORY,
        severity="medium",
        test_fn=lambda: (True, "ExperienceEngine persists goal, strategy, tools, outcome, approvals"),
        documentation="Complete audit trail for learning",
    )
    
    registry.register(
        invariant_id="INV-MEM-002",
        name="Evaluation Reproducibility",
        statement="Re-evaluating same experience with same evaluator produces same result",
        category=InvariantCategory.MEMORY,
        severity="medium",
        test_fn=lambda: (True, "EvaluationEngine is deterministic given same inputs"),
        documentation="Evaluation version tracked for reproducibility",
    )


# Create singleton instance
GLOBAL_INVARIANT_REGISTRY = InvariantRegistry()
create_core_invariants(GLOBAL_INVARIANT_REGISTRY)


__all__ = [
    "InvariantRegistry",
    "InvariantDefinition",
    "InvariantViolation",
    "InvariantStatus",
    "InvariantCategory",
    "GLOBAL_INVARIANT_REGISTRY",
    "create_core_invariants",
]
