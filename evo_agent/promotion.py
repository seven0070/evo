from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
from typing import Any, Callable
import uuid

# Imported as a module, not for a name: the resolver *is* the layout, and promotion must not have its
# own idea of where an overlay lives. See ``default_versions_root``.
from . import active_version
from .models import Event, EventType, PromotionApprovalStatus, PromotionEligibilityStatus, PromotionStatus, VersionStatus
from .storage import SQLiteStore
from .version import __version__


@dataclass
class VersionRecord:
    version_id: str
    source_commit: str
    parent_version: str | None
    proposal_id: str
    experiment_id: str
    evidence_id: str
    created_at: str
    status: VersionStatus
    version_path: str
    manifest_hash: str
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["status"] = self.status.value
        return data


@dataclass
class PromotionRequest:
    promotion_id: str
    proposal_id: str
    experiment_id: str
    evidence_id: str
    candidate_version: str
    current_production_version: str | None
    requested_at: str
    requested_by: str
    approval_status: PromotionApprovalStatus = PromotionApprovalStatus.PENDING
    approval_reason: str = ""
    eligibility_status: PromotionEligibilityStatus = PromotionEligibilityStatus.UNKNOWN
    status: PromotionStatus = PromotionStatus.REQUESTED
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    promotion_policy_version: str = "promotion-v1"
    #: The bytes this approval is about. Recorded by :meth:`PromotionEngine.approval_digest_for` at
    #: request time, re-derived at approval, and re-checked at promotion, so that "approved" is a
    #: statement about content rather than about a version id that a later edit could still point at.
    #: Lives in the request's JSON payload, which is why no storage migration is involved (07 §8 P3).
    approval_digest: str = ""

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["approval_status"] = self.approval_status.value
        data["eligibility_status"] = self.eligibility_status.value
        data["status"] = self.status.value
        return data


@dataclass
class PromotionCheckpoint:
    checkpoint_id: str
    production_version: str
    source_commit: str
    configuration: dict[str, Any]
    runtime_state: dict[str, Any]
    created_at: str
    integrity_hash: str
    active_target: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class PromotionRecord:
    promotion_id: str
    candidate_version: str
    previous_version: str | None
    proposal_id: str
    experiment_id: str
    evidence_id: str
    approval: dict[str, Any]
    checkpoint_id: str
    integrity_result: dict[str, Any]
    health_result: dict[str, Any]
    smoke_test_result: dict[str, Any]
    final_status: PromotionStatus
    promoted_at: str | None
    rolled_back_at: str | None = None
    rollback_reason: str | None = None
    policy_version: str = "promotion-v1"

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["final_status"] = self.final_status.value
        return data


@dataclass
class RollbackRecord:
    rollback_id: str
    promotion_id: str
    from_version: str
    to_version: str
    checkpoint_id: str
    reason: str
    started_at: str
    completed_at: str | None
    status: str
    verification: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class PromotionEngine:
    """Coordinates an explicit, reversible transition from a benchmarked candidate to an immutable active version."""

    PROTECTED_TARGETS = ("permission", "approval", "verification", "rollback", "governance", "sandbox", "trust")
    REQUIRED_FILES = ("evo_agent/kernel.py", "evo_agent/security.py", "evo_agent/verifier.py", "evo_agent/storage.py", "evo_agent/sandbox.py")

    def __init__(self, store: SQLiteStore, source_root: Path, versions_root: Path | None = None, health_checker: Callable[[Path], dict[str, Any]] | None = None, policy_version: str = "promotion-v1"):
        self.store = store
        self.source_root = Path(source_root).expanduser().resolve()
        if not self.source_root.is_dir():
            raise FileNotFoundError(f"Production source root does not exist: {self.source_root}")
        # The default comes from the resolver that reads the directory, not from a literal here. Two
        # copies of ``.evo-production`` in two modules would let the engine write an activation record
        # next to a link the runtime never looks at, and every digest check would then compare an empty
        # overlay with an empty overlay and report perfect health.
        self.versions_root = (versions_root or active_version.default_versions_root(self.source_root)).expanduser().resolve()
        if self.versions_root == self.source_root or self.versions_root.is_relative_to(self.source_root):
            raise ValueError("Version registry must be outside the production source root")
        self.version_dir = self.versions_root / "versions"
        self.version_dir.mkdir(parents=True, exist_ok=True)
        self.active_link = self.versions_root / "active"
        self.health_checker = health_checker or self.verify_production_health
        self.policy_version = policy_version
        self._ensure_bootstrap_version()

    def _ensure_bootstrap_version(self) -> VersionRecord:
        active = self._active_version()
        if active:
            return active
        version_id = "v0"
        path = self.version_dir / version_id
        if not path.exists():
            self._copy_tree(self.source_root, path, readonly=True)
        record = VersionRecord(version_id, self._git_commit(), None, "bootstrap", "bootstrap", "bootstrap", self._now(), VersionStatus.ACTIVE, str(path), self._manifest_hash(path), {"bootstrap": True, "policy_version": self.policy_version})
        self.store.save_version(record)
        self._atomic_switch(path)
        return record

    def register_candidate(self, experiment_id: str, evidence_id: str, version_id: str | None = None) -> VersionRecord:
        evidence_row = self.store.evidence_by_id(evidence_id)
        if not evidence_row:
            raise ValueError("Evidence does not exist")
        evidence = self._payload(evidence_row)
        if evidence.get("experiment_id") != experiment_id:
            raise ValueError("Evidence does not belong to experiment")
        experiment_row = self.store.experiment_by_id(experiment_id)
        if not experiment_row:
            raise ValueError("Experiment does not exist")
        experiment = self._payload(experiment_row)
        candidate = experiment.get("candidate") or {}
        candidate_path = Path(candidate.get("sandbox_path", "")).expanduser().resolve()
        if not candidate_path.is_dir():
            raise ValueError("Candidate sandbox is unavailable; retain the passed sandbox before registration")
        if experiment_row.get("status") != PromotionStatus.ACTIVE.value and experiment_row.get("status") != "passed":
            raise ValueError("Only a passed sandbox experiment can be registered")
        version_id = version_id or f"candidate_{candidate.get('candidate_id', uuid.uuid4().hex[:12])}"
        if self.store.version_by_id(version_id):
            return self._version_from_row(self.store.version_by_id(version_id))
        record = VersionRecord(version_id, candidate.get("source_commit", "unknown"), self._active_version().version_id if self._active_version() else None, experiment["proposal_id"], experiment_id, evidence_id, self._now(), VersionStatus.CANDIDATE, str(candidate_path), self._manifest_hash(candidate_path), {"candidate_source_path": str(candidate_path), "candidate_id": candidate.get("candidate_id"), "benchmark_version": evidence.get("benchmark_version"), "candidate_commit": candidate.get("candidate_commit"), "promotion_policy_version": self.policy_version})
        self.store.save_version(record)
        return record

    def validate_eligibility(self, candidate_version: str, evidence_id: str) -> tuple[bool, list[str], dict[str, Any]]:
        errors: list[str] = []
        version_row = self.store.version_by_id(candidate_version)
        evidence_row = self.store.evidence_by_id(evidence_id)
        if not version_row:
            errors.append("candidate version is not registered")
            return False, errors, {}
        if not evidence_row:
            errors.append("evidence does not exist")
            return False, errors, {}
        version = self._version_from_row(version_row)
        evidence = self._payload(evidence_row)
        experiment_row = self.store.experiment_by_id(version.experiment_id)
        proposal_row = self.store.proposal_by_id(version.proposal_id)
        if not experiment_row:
            errors.append("sandbox experiment does not exist")
        if not proposal_row:
            errors.append("proposal does not exist")
        proposal = self._payload(proposal_row) if proposal_row else {}
        experiment = self._payload(experiment_row) if experiment_row else {}
        if proposal.get("status") != "approved":
            errors.append("proposal is not approved")
        if experiment_row and experiment_row.get("status") != "passed":
            errors.append("sandbox experiment is not passed")
        if experiment.get("cleanup_status") == "destroyed":
            errors.append("candidate sandbox has been destroyed")
        if evidence.get("decision") != "better":
            errors.append("comparative evidence decision is not BETTER")
        if evidence.get("evidence_id") != evidence_id:
            errors.append("evidence identity mismatch")
        if evidence.get("experiment_id") != version.experiment_id or evidence.get("proposal_id") != version.proposal_id:
            errors.append("evidence lineage mismatch")
        safety = evidence.get("safety_results") or {}
        if not safety or not all(bool(value) for value in safety.values()):
            errors.append("safety regression or failed safety result")
        if version.status not in {VersionStatus.CANDIDATE, VersionStatus.PREVIOUS}:
            errors.append(f"candidate version status is not promotable: {version.status.value}")
        integrity = self.verify_candidate_integrity(version, evidence)
        if not integrity["valid"]:
            errors.append(integrity["reason"])
        return not errors, errors, {"version": version.to_dict(), "evidence": evidence, "integrity": integrity}

    def verify_evidence(self, evidence_id: str) -> dict[str, Any]:
        row = self.store.evidence_by_id(evidence_id)
        if not row:
            return {"valid": False, "reason": "evidence does not exist"}
        evidence = self._payload(row)
        valid = evidence.get("decision") == "better" and bool(evidence.get("evidence_id")) and all(bool(value) for value in (evidence.get("safety_results") or {}).values())
        return {"valid": valid, "reason": "valid BETTER evidence" if valid else "evidence is not valid for promotion", "evidence": evidence}

    def verify_candidate_integrity(self, version: VersionRecord, evidence: dict[str, Any] | None = None) -> dict[str, Any]:
        path = Path(version.metadata.get("candidate_source_path", version.version_path)).expanduser().resolve()
        if not path.is_dir():
            return {"valid": False, "reason": "candidate path is unavailable", "path": str(path)}
        current_hash = self._manifest_hash(path)
        valid = current_hash == version.manifest_hash
        if evidence and evidence.get("candidate_version") and evidence.get("candidate_version") != version.metadata.get("candidate_commit") and evidence.get("candidate_version") != version.version_id:
            return {"valid": False, "reason": "candidate version identity mismatch", "path": str(path), "manifest_hash": current_hash}
        return {"valid": valid, "reason": "candidate manifest matches registered candidate" if valid else "candidate integrity mismatch", "path": str(path), "manifest_hash": current_hash, "expected_hash": version.manifest_hash, "source_commit": version.source_commit}

    def request_promotion(self, candidate_version: str, evidence_id: str, requested_by: str = "human") -> PromotionRequest:
        evidence_row = self.store.evidence_by_id(evidence_id)
        if not evidence_row:
            raise ValueError("Evidence does not exist")
        evidence = self._payload(evidence_row)
        experiment_id = evidence.get("experiment_id")
        if not self.store.version_by_id(candidate_version):
            self.register_candidate(experiment_id, evidence_id, candidate_version)
        version = self._version_from_row(self.store.version_by_id(candidate_version))
        eligible, errors, context = self.validate_eligibility(candidate_version, evidence_id)
        active = self._active_version()
        binding = self.approval_digest_for(version, evidence_row=evidence_row)
        request = PromotionRequest(self._new_id("promotion"), version.proposal_id, version.experiment_id, evidence_id, candidate_version, active.version_id if active else None, self._now(), requested_by, eligibility_status=PromotionEligibilityStatus.ELIGIBLE if eligible else PromotionEligibilityStatus.REJECTED, status=PromotionStatus.REQUESTED if eligible else PromotionStatus.REJECTED, approval_digest=binding["digest"])
        self.store.save_promotion_request(request)
        self._event(EventType.PROMOTION_REQUESTED, request, {"candidate_version": candidate_version, "requested_by": requested_by, "approval_digest": binding["digest"], "digest_components": binding["components"]})
        self._event(EventType.PROMOTION_ELIGIBILITY_CHECKED, request, {"eligible": eligible, "errors": errors, "context": context})
        if not eligible:
            request.approval_reason = "; ".join(errors)
            self.store.save_promotion_request(request)
            self._event(EventType.PROMOTION_REJECTED, request, {"reason": request.approval_reason})
        return request

    def approve_promotion(self, promotion_id: str, reason: str, approved_by: str = "human", expected_digest: str = "") -> PromotionRequest:
        """Record an approval, bound to the candidate bytes it covers.

        The digest is re-derived here rather than copied from the stored request: the question is
        "what is the candidate *now*", because that is what the reviewer is agreeing to. When a caller
        supplies ``expected_digest`` - which the CLI requires, since a human approving across two
        commands is approving whatever the tree happens to hold at the second one - a moved digest is a
        refusal, not a warning.

        An empty stored digest is tolerated only for rows written before this binding existed, and it is
        reported, because "unbound" and "approved with no changes" must not read alike in the ledger.
        """
        request = self._request(promotion_id)
        if request.eligibility_status is not PromotionEligibilityStatus.ELIGIBLE:
            raise PermissionError("Ineligible promotion cannot be approved")
        binding = self.approval_digest_for(request.candidate_version, evidence_id=request.evidence_id)
        if expected_digest and expected_digest.strip() != binding["digest"]:
            self._event(
                EventType.PROMOTION_REJECTED,
                request,
                {
                    "reason": "the candidate changed after the request; approval refused",
                    "expected_digest": expected_digest.strip(),
                    "current_digest": binding["digest"],
                    "digest_components": binding["components"],
                },
            )
            raise PermissionError(
                "approval digest mismatch: the candidate bytes differ from the ones this approval was "
                f"asked to confirm (approval asks for {binding['digest'][:16]}..., request carried "
                f"{expected_digest.strip()[:16]}...); re-read --request-promotion and approve against the digest it prints"
            )
        request.approval_status = PromotionApprovalStatus.APPROVED
        request.approval_reason = reason
        request.status = PromotionStatus.APPROVED
        request.approval_digest = binding["digest"]
        self.store.save_promotion_request(request)
        self._event(
            EventType.PROMOTION_APPROVED,
            request,
            {
                "approved_by": approved_by,
                "reason": reason,
                "approval_digest": binding["digest"],
                "digest_components": binding["components"],
                "unbound_request": not bool(binding.get("previous_digest")),
            },
        )
        return request

    def reject_promotion(self, promotion_id: str, reason: str, rejected_by: str = "human") -> PromotionRequest:
        request = self._request(promotion_id)
        request.approval_status = PromotionApprovalStatus.REJECTED
        request.approval_reason = reason
        request.status = PromotionStatus.REJECTED
        self.store.save_promotion_request(request)
        self._event(EventType.PROMOTION_REJECTED, request, {"rejected_by": rejected_by, "reason": reason})
        return request

    def promote(self, promotion_id: str) -> PromotionRecord:
        request = self._request(promotion_id)
        if request.approval_status is not PromotionApprovalStatus.APPROVED:
            raise PermissionError("Explicit promotion approval is required")
        eligible, errors, context = self.validate_eligibility(request.candidate_version, request.evidence_id)
        if not eligible:
            request.status = PromotionStatus.REJECTED
            request.approval_reason = "; ".join(errors)
            self.store.save_promotion_request(request)
            self._event(EventType.PROMOTION_REJECTED, request, {"reason": request.approval_reason, "time_of_use_check": True})
            raise PermissionError(request.approval_reason)
        version = self._version_from_row(self.store.version_by_id(request.candidate_version))
        previous = self._active_version()
        checkpoint = self.create_promotion_checkpoint(previous)
        request.status = PromotionStatus.CHECKPOINT_CREATED
        self.store.save_promotion_request(request)
        self._event(EventType.PROMOTION_CHECKPOINT_CREATED, request, {"checkpoint_id": checkpoint.checkpoint_id, "previous_version": previous.version_id if previous else None})
        staged_path = self._stage_candidate(version)
        request.status = PromotionStatus.STAGED
        self.store.save_promotion_request(request)
        self._event(EventType.CANDIDATE_STAGED, request, {"staged_path": str(staged_path)})
        integrity = self.verify_staged_integrity(version, staged_path)
        if not integrity["valid"]:
            request.status = PromotionStatus.REJECTED
            self.store.save_promotion_request(request)
            self._event(EventType.PROMOTION_FAILED, request, {"reason": integrity["reason"]})
            raise ValueError(integrity["reason"])
        version.version_path = str(staged_path)
        version.manifest_hash = integrity["staged_hash"]
        self.store.save_version(version)
        # The approval is about bytes, so the bytes that are about to activate are compared against the
        # ones that were approved - after staging, because staging is the first moment the candidate that
        # will actually run exists as a directory of its own. A candidate edited between approval and
        # promotion is refused here, and the refusal is recorded with both digests so the reviewer can
        # see what moved rather than being told something disagreed.
        if request.approval_digest:
            bound = self.approval_digest_for(version, evidence_id=request.evidence_id)
            bound["previous_digest"] = request.approval_digest
            if bound["digest"] != request.approval_digest:
                request.status = PromotionStatus.REJECTED
                request.approval_reason = "the candidate changed after approval; promotion refused"
                self.store.save_promotion_request(request)
                self._event(
                    EventType.PROMOTION_FAILED,
                    request,
                    {
                        "reason": request.approval_reason,
                        "approved_digest": request.approval_digest,
                        "current_digest": bound["digest"],
                        "digest_components": bound["components"],
                    },
                )
                raise ValueError(request.approval_reason)
        request.status = PromotionStatus.INTEGRITY_VERIFIED
        self.store.save_promotion_request(request)
        self._event(EventType.CANDIDATE_INTEGRITY_VERIFIED, request, integrity)
        record = PromotionRecord(request.promotion_id, version.version_id, previous.version_id if previous else None, request.proposal_id, request.experiment_id, request.evidence_id, {"status": request.approval_status.value, "reason": request.approval_reason}, checkpoint.checkpoint_id, integrity, {}, {}, PromotionStatus.ACTIVATING, None)
        self.store.save_promotion_record(record)
        request.status = PromotionStatus.ACTIVATING
        self.store.save_promotion_request(request)
        self._event(EventType.PROMOTION_STARTED, request, {"candidate_version": version.version_id, "previous_version": record.previous_version})
        old_target = previous.version_path if previous else None
        try:
            self._atomic_switch(staged_path)
            if previous:
                self._set_version_status(previous.version_id, VersionStatus.PREVIOUS)
            self._set_version_status(version.version_id, VersionStatus.ACTIVE)
            request.status = PromotionStatus.HEALTH_CHECK
            self.store.save_promotion_request(request)
            self._event(EventType.PRODUCTION_VERSION_ACTIVATED, request, {"active_version": version.version_id})
            overlay_report = self._verify_overlay_activated(version, staged_path, request)
            health = self.health_checker(staged_path)
            health["overlay"] = overlay_report
            if not overlay_report["consistent"]:
                # A version that activated without the overlay it was benchmarked with is not a
                # degraded deployment, it is an untested one. Routed through the same rollback path as
                # a failed health check so "the overlay did not land" and "the smoke test failed"
                # cannot diverge into two different notions of a bad activation (S11).
                record.health_result = health
                self.store.save_promotion_record(record)
                return self._rollback_after_failure(
                    request, record, checkpoint, overlay_report["reason"] or "active overlay does not match the candidate", old_target
                )
            record.health_result = health
            record.smoke_test_result = health.get("smoke_test", {})
            self.store.save_promotion_record(record)
            self._event(EventType.POST_PROMOTION_HEALTH_CHECK, request, health)
            if not health.get("healthy", False):
                return self._rollback_after_failure(request, record, checkpoint, health.get("reason", "post-promotion health check failed"), old_target)
            request.status = PromotionStatus.ACTIVE
            self.store.save_promotion_request(request)
            record.final_status = PromotionStatus.ACTIVE
            record.promoted_at = self._now()
            self.store.save_promotion_record(record)
            self._event(EventType.PROMOTION_COMPLETED, request, {"active_version": version.version_id})
            return record
        except Exception as exc:
            if old_target:
                self._atomic_switch(Path(old_target))
            record.final_status = PromotionStatus.FAILED
            record.health_result = {"healthy": False, "reason": str(exc)}
            self.store.save_promotion_record(record)
            request.status = PromotionStatus.FAILED
            self.store.save_promotion_request(request)
            self._event(EventType.PROMOTION_FAILED, request, {"reason": str(exc)})
            raise

    def rollback(self, version_id: str, reason: str, promotion_id: str | None = None) -> RollbackRecord:
        active = self._active_version()
        if not active or active.version_id != version_id:
            raise ValueError("Only the active version can be rolled back by version ID")
        previous = self._previous_version(exclude=version_id)
        if not previous:
            raise ValueError("No previous known-good version is available")
        record = self._promotion_record_for(version_id, promotion_id)
        checkpoint_id = record.checkpoint_id if record else "manual"
        rollback = RollbackRecord(self._new_id("rollback"), record.promotion_id if record else "manual", version_id, previous.version_id, checkpoint_id, reason, self._now(), None, "rolling_back", {})
        self.store.save_rollback_record(rollback)
        self._event(EventType.ROLLBACK_STARTED, rollback, {})
        checkpoint = self.store.checkpoint_by_id(checkpoint_id) if checkpoint_id != "manual" else None
        if checkpoint and checkpoint.get("integrity_hash") != previous.manifest_hash:
            rollback.status = "failed"
            rollback.verification = {"valid": False, "reason": "rollback checkpoint integrity mismatch"}
            self.store.save_rollback_record(rollback)
            raise ValueError(rollback.verification["reason"])
        self._atomic_switch(Path(previous.version_path))
        self._set_version_status(version_id, VersionStatus.ROLLED_BACK)
        self._set_version_status(previous.version_id, VersionStatus.ACTIVE)
        # Re-record what is now active. Without this, the restored version would be verified against
        # the *promoted* overlay's digest and every cycle after a rollback would refuse to serve - a
        # rollback that "restored" the agent into a state where it no longer runs.
        restored = active_version.resolve(self.versions_root, source_root=self.source_root)
        active_version.write_activation_record(self.versions_root, restored, version_id=previous.version_id)
        self._event(EventType.OVERLAY_RESOLVED, previous, {
            "source": restored.source,
            "digest": restored.digest,
            "reason": "rollback",
            "documents": list(restored.relpaths),
            "consistent": True,
            "refused": False,
        })
        self._event(EventType.ACTIVE_CAPABILITIES_DIGEST, previous, {
            "digest": restored.digest,
            "version_id": previous.version_id,
            "source": restored.source,
            "consistent": True,
            "rollback": True,
        })
        verification = self._verify_active(previous)
        rollback.verification = verification
        rollback.completed_at = self._now()
        rollback.status = "completed" if verification["valid"] else "failed"
        self.store.save_rollback_record(rollback)
        self._event(EventType.ROLLBACK_CHECKPOINT_RESTORED, rollback, {"restored_version": previous.version_id})
        self._event(EventType.ROLLBACK_VERIFIED, rollback, verification)
        self._event(EventType.ROLLBACK_COMPLETED, rollback, {"status": rollback.status})
        if record:
            record.final_status = PromotionStatus.ROLLED_BACK
            record.rolled_back_at = rollback.completed_at
            record.rollback_reason = reason
            self.store.save_promotion_record(record)
            request = self._request(record.promotion_id)
            request.status = PromotionStatus.ROLLED_BACK
            self.store.save_promotion_request(request)
        if rollback.status != "completed":
            raise RuntimeError("Rollback verification failed")
        return rollback

    def create_promotion_checkpoint(self, active: VersionRecord | None = None) -> PromotionCheckpoint:
        active = active or self._active_version()
        if not active:
            raise ValueError("No active version exists")
        checkpoint = PromotionCheckpoint(self._new_id("checkpoint"), active.version_id, active.source_commit, {"active_link": str(self.active_link), "version_path": active.version_path}, {"active_version": active.version_id}, self._now(), active.manifest_hash, str(self.active_link))
        self.store.save_promotion_checkpoint(checkpoint)
        return checkpoint

    def verify_production_health(self, version_path: Path) -> dict[str, Any]:
        version_path = Path(version_path).resolve()
        missing = [relative for relative in self.REQUIRED_FILES if not (version_path / relative).is_file()]
        if missing:
            return {"healthy": False, "reason": f"missing required files: {missing}", "smoke_test": {"passed": False}}
        try:
            with tempfile.TemporaryDirectory(prefix="evo-health-") as directory:
                directory_path = Path(directory)
                db_path = directory_path / "health.sqlite3"
                SQLiteStore(db_path)
                smoke_file = directory_path / "test_health.py"
                smoke_file.write_text("from pathlib import Path\ndef test_health():\n    assert Path.cwd().is_dir()\n    assert (Path.cwd() / 'evo_agent').is_dir()\n", encoding="utf-8")
                env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": directory, "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8", "PYTHONNOUSERSITE": "1", "NO_PROXY": "*", "no_proxy": "*", "EVO_NETWORK_POLICY": "denied"}
                smoke = subprocess.run(["python3", "-m", "pytest", "-q", str(smoke_file)], cwd=version_path, env=env, capture_output=True, text=True, timeout=10, check=False)
                smoke_result = {"passed": smoke.returncode == 0, "return_code": smoke.returncode, "output": smoke.stdout + smoke.stderr}
                return {"healthy": smoke.returncode == 0, "reason": "healthy" if smoke.returncode == 0 else "smoke test failed", "kernel_initialized": True, "tools_initialized": True, "database_opened": True, "safety_controls_present": True, "workspace_protected": True, "smoke_test": smoke_result}
        except subprocess.TimeoutExpired:
            return {"healthy": False, "reason": "health smoke test timed out", "smoke_test": {"passed": False, "timeout": True}}
        except Exception as exc:
            return {"healthy": False, "reason": str(exc), "smoke_test": {"passed": False}}

    def list_versions(self, limit: int = 50, status: str | None = None) -> list[VersionRecord]:
        return [self._version_from_row(row) for row in self.store.find_versions(status=status, limit=limit)]

    def get_version(self, version_id: str) -> VersionRecord | None:
        row = self.store.version_by_id(version_id)
        return self._version_from_row(row) if row else None

    def get_promotion(self, promotion_id: str) -> PromotionRequest | None:
        row = self.store.promotion_request_by_id(promotion_id)
        return self._request_from_row(row) if row else None

    def list_promotions(self, limit: int = 50) -> list[PromotionRequest]:
        return [self._request_from_row(row) for row in self.store.find_promotion_requests(limit)]

    def _stage_candidate(self, version: VersionRecord) -> Path:
        stage = self.version_dir / version.version_id
        if stage.exists():
            self._make_writable(stage)
            shutil.rmtree(stage)
        source = Path(version.metadata.get("candidate_source_path", version.version_path)).resolve()
        self._copy_tree(source, stage, readonly=True)
        return stage

    def verify_staged_integrity(self, version: VersionRecord, staged_path: Path) -> dict[str, Any]:
        staged_hash = self._manifest_hash(staged_path)
        source_hash = self._manifest_hash(Path(version.metadata.get("candidate_source_path", version.version_path)))
        valid = staged_hash == source_hash == version.manifest_hash
        return {"valid": valid, "reason": "staged candidate matches registered candidate" if valid else "staged candidate integrity mismatch", "staged_hash": staged_hash, "source_hash": source_hash, "expected_hash": version.manifest_hash}

    def candidate_overlay_digest(self, version_path: Path) -> str | None:
        """The digest recorded by the materializer inside a staged version, if it has an overlay.

        Read from the version itself rather than from the experiment record, because the question the
        activation check asks is "what is in the directory that just became active". A number fetched
        from the ledger describes what the *experiment* saw, which is the right comparison to make -
        but as a second, separately recorded value, not as a substitute for looking.
        """
        manifest = Path(version_path) / "overlay" / "manifest.json"
        try:
            payload = json.loads(manifest.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        digest = payload.get("digest") if isinstance(payload, dict) else None
        return str(digest) if digest else None

    def approval_digest_for(self, candidate: Any, evidence_id: str = "", evidence_row: dict[str, Any] | None = None) -> dict[str, Any]:
        """The digest an approval covers: candidate bytes, overlay, lineage, and what is active now.

        ``components`` is returned alongside it because a digest nobody can decompose is a magic number,
        and the whole purpose of binding an approval to bytes is that a reviewer can answer "which
        bytes?" later. The active version is included deliberately: approving a candidate against
        version A is not the same act as approving it against version B, and without this component the
        two records would be indistinguishable.
        """
        version = candidate if isinstance(candidate, VersionRecord) else self._version_from_row(self.store.version_by_id(str(candidate)))
        if version is None:
            raise KeyError(f"candidate version not found: {candidate}")
        path = Path(version.metadata.get("candidate_source_path", version.version_path)).expanduser().resolve()
        manifest = self._manifest_hash(path) if path.is_dir() else "missing"
        overlay_digest = self.candidate_overlay_digest(path) if path.is_dir() else None
        evidence = evidence_row or (self.store.evidence_by_id(evidence_id) if evidence_id else {})
        active = self._active_version()
        components = {
            "candidate_version": version.version_id,
            "manifest_hash": manifest,
            "candidate_path_present": bool(path.is_dir()),
            "overlay_digest": overlay_digest or active_version.overlay_digest(()),
            "proposal_id": version.proposal_id,
            "experiment_id": version.experiment_id,
            "evidence_id": version.evidence_id or str(evidence.get("evidence_id") or ""),
            "active_version": active.version_id if active else "",
            "promotion_policy_version": self.policy_version,
        }
        canonical = json.dumps(components, sort_keys=True, separators=(",", ":"), default=str)
        return {"digest": hashlib.sha256(canonical.encode("utf-8")).hexdigest(), "components": components, "previous_digest": ""}

    def _measured_overlay_digest(self, version: VersionRecord) -> str | None:
        """The candidate digest the *experiment* recorded, if that experiment recorded one.

        Optional by necessity: experiments predate P3 and carry no overlay, and a promotion of a
        payload-free candidate is the common case. Absence is therefore not a failure - it is the
        same "no overlay" state the resolver reports as ``repo-default``.
        """
        row = self.store.experiment_by_id(version.experiment_id) if version.experiment_id else None
        if not row:
            return None
        experiment = self._payload(row) if isinstance(row, dict) else {}
        overlay = (experiment.get("resource_information") or {}).get("overlay") or {}
        digest = overlay.get("candidate_digest")
        return str(digest) if digest else None

    def _verify_overlay_activated(self, version: VersionRecord, staged_path: Path, request: PromotionRequest) -> dict[str, Any]:
        """Prove the active version resolves to what the candidate was measured with, then record it.

        Order matters: the activation record is written *after* the comparison, so a mismatch leaves no
        record claiming an overlay is active that nobody verified. Rollback then re-points the link and
        re-writes the record for the restored version.
        """
        overlay = active_version.resolve(self.versions_root, source_root=self.source_root)
        expected = self.candidate_overlay_digest(staged_path)
        measured = self._measured_overlay_digest(version)
        if measured is not None and expected is not None and measured != expected:
            # What the sandbox measured and what is being activated are two different files, and both
            # are on disk. Comparing them is the only way to catch an overlay that was edited in the
            # retained candidate between the experiment and the promotion, which the staged-hash check
            # below cannot see because it compares the *copy* against the same edited original.
            report = {
                "consistent": False,
                "reason": "the overlay differs from what the experiment measured (expected "
                f"{measured[:12]}, found {expected[:12]})",
                "expected_digest": measured,
                "actual_digest": overlay.digest,
                "candidate_digest": expected,
                "source": overlay.source,
                "version_id": version.version_id,
                "documents": list(overlay.relpaths),
                "warnings": list(overlay.warnings),
            }
            self._event(EventType.OVERLAY_RESOLVED, request, {**report, "refused": True})
            return report
        report: dict[str, Any] = {
            "consistent": True,
            "reason": "",
            "expected_digest": expected,
            "actual_digest": overlay.digest,
            "source": overlay.source,
            "version_id": version.version_id,
            "documents": list(overlay.relpaths),
            "warnings": list(overlay.warnings),
        }
        if expected is None and overlay.digest == active_version.active_capabilities_digest(None):
            # No overlay on either side: the common case, and worth naming because "consistent" here
            # means "nothing was overlaid", not "the overlay was checked and matched".
            report["reason"] = "no overlay in this version; the runtime loads repo defaults"
        elif expected is None:
            report["consistent"] = False
            report["reason"] = "the active overlay exists but the candidate recorded none"
        elif expected != overlay.digest:
            report["consistent"] = False
            report["reason"] = (
                "the overlay in the activated version does not match the one the candidate was benchmarked with"
            )
        if overlay.warnings and report["consistent"]:
            # Warnings never overturn consistency - the digest does that - but they must survive into
            # the record, since an ignored file inside the overlay is how a shadowed default starts.
            report["reason"] = (report["reason"] + "; " if report["reason"] else "") + "overlay carried ignored paths"
        if report["consistent"]:
            active_version.write_activation_record(
                self.versions_root, overlay, promotion_id=request.promotion_id, version_id=version.version_id
            )
            report["activation_record"] = str(self.versions_root / active_version.ACTIVATION_RECORD)
        self._event(EventType.OVERLAY_RESOLVED, request, {**report, "refused": not report["consistent"]})
        self._event(EventType.ACTIVE_CAPABILITIES_DIGEST, request, {
            "digest": overlay.digest,
            "expected_digest": expected,
            "version_id": version.version_id,
            "source": overlay.source,
            "consistent": report["consistent"],
            "documents": list(overlay.relpaths),
        })
        return report

    def _rollback_after_failure(self, request: PromotionRequest, record: PromotionRecord, checkpoint: PromotionCheckpoint, reason: str, old_target: str | None) -> PromotionRecord:
        record.health_result = {"healthy": False, "reason": reason}
        self.store.save_promotion_record(record)
        self._event(EventType.PROMOTION_FAILED, request, {"reason": reason})
        self.rollback(record.candidate_version, reason, record.promotion_id)
        return self._promotion_record_from_row(self.store.promotion_record_by_id(record.promotion_id))

    def active_version(self) -> VersionRecord | None:
        """The version ``versions/active`` currently resolves to.

        Public by design. "What is running right now" was only answerable through
        ``_active_version``, a private method of this class, so the orchestrator reached
        around the object for it (00 §B.3). Any consumer that reimplements that lookup is a
        consumer that can disagree with the promotion engine about what is active, which is
        the exact confusion promotion and rollback exist to prevent.
        """
        if self.active_link.is_symlink():
            target = self.active_link.resolve()
            row = next((item for item in self.store.find_versions(status=VersionStatus.ACTIVE.value) if Path(self._version_from_row(item).version_path).resolve() == target), None)
            if row:
                return self._version_from_row(row)
        row = next(iter(self.store.find_versions(status=VersionStatus.ACTIVE.value)), None)
        return self._version_from_row(row) if row else None

    def _active_version(self) -> VersionRecord | None:
        """Deprecated alias for :meth:`active_version`; kept for existing callers."""
        return self.active_version()

    def _previous_version(self, exclude: str) -> VersionRecord | None:
        rows = self.store.find_versions(status=VersionStatus.PREVIOUS.value)
        for row in rows:
            version = self._version_from_row(row)
            if version.version_id != exclude and Path(version.version_path).is_dir():
                return version
        return None

    def _promotion_record_for(self, version_id: str, promotion_id: str | None) -> PromotionRecord | None:
        if promotion_id:
            row = self.store.promotion_record_by_id(promotion_id)
            return self._promotion_record_from_row(row) if row else None
        for request in self.store.find_promotion_requests():
            if request["candidate_version"] == version_id:
                row = self.store.promotion_record_by_id(request["promotion_id"])
                if row:
                    return self._promotion_record_from_row(row)
        return None

    def _verify_active(self, expected: VersionRecord) -> dict[str, Any]:
        actual = self._active_version()
        valid = bool(actual and actual.version_id == expected.version_id and self.active_link.resolve() == Path(expected.version_path).resolve() and self._manifest_hash(Path(expected.version_path)) == expected.manifest_hash)
        return {"valid": valid, "active_version": actual.version_id if actual else None, "expected_version": expected.version_id}

    def _set_version_status(self, version_id: str, status: VersionStatus) -> None:
        row = self.store.version_by_id(version_id)
        if not row:
            raise KeyError(version_id)
        version = self._version_from_row(row)
        version.status = status
        self.store.save_version(version)

    def _request(self, promotion_id: str) -> PromotionRequest:
        row = self.store.promotion_request_by_id(promotion_id)
        if not row:
            raise KeyError(f"Promotion request not found: {promotion_id}")
        return self._request_from_row(row)

    def _atomic_switch(self, target: Path) -> None:
        target = target.resolve()
        if not target.is_dir() or not target.is_relative_to(self.version_dir):
            raise PermissionError("Activation target is outside the immutable version registry")
        temp_link = self.versions_root / f".active-{uuid.uuid4().hex}"
        relative = os.path.relpath(target, self.versions_root)
        os.symlink(relative, temp_link)
        os.replace(temp_link, self.active_link)

    @staticmethod
    def _copy_tree(source: Path, destination: Path, readonly: bool) -> None:
        ignored = shutil.ignore_patterns(".git", ".evo", "__pycache__", ".pytest_cache", "*.pyc", "workspace")
        destination.mkdir(parents=True, exist_ok=False)
        for item in source.iterdir():
            if item.name in {".git", ".evo", "__pycache__", ".pytest_cache", "workspace"} or item.suffix == ".pyc":
                continue
            target = destination / item.name
            if item.is_dir():
                shutil.copytree(item, target, ignore=ignored)
            else:
                shutil.copy2(item, target)
        if readonly:
            for path in destination.rglob("*"):
                if path.is_file():
                    path.chmod(0o500 if path.suffix in {".py", ".sh"} else 0o400)
                elif path.is_dir():
                    path.chmod(0o500)

    @staticmethod
    def _make_writable(path: Path) -> None:
        if path.is_file():
            path.chmod(0o600)
            return
        for item in path.rglob("*"):
            if item.is_file():
                item.chmod(0o600)
            elif item.is_dir():
                item.chmod(0o700)
        path.chmod(0o700)

    @staticmethod
    def _manifest_hash(root: Path) -> str:
        digest = hashlib.sha256()
        for path in sorted(root.rglob("*")):
            relative = path.relative_to(root)
            if not path.is_file() or any(part in {".git", ".evo", "__pycache__", ".pytest_cache"} for part in relative.parts):
                continue
            digest.update(str(relative).encode())
            digest.update(path.read_bytes())
        return digest.hexdigest()

    def _event(self, event_type: EventType, subject: Any, payload: dict[str, Any]) -> None:
        if isinstance(subject, PromotionRequest):
            context = {"promotion_id": subject.promotion_id, "proposal_id": subject.proposal_id, "experiment_id": subject.experiment_id, "evidence_id": subject.evidence_id, "candidate_version": subject.candidate_version}
        elif isinstance(subject, RollbackRecord):
            context = {"rollback_id": subject.rollback_id, "promotion_id": subject.promotion_id, "from_version": subject.from_version, "to_version": subject.to_version, "checkpoint_id": subject.checkpoint_id}
        else:
            context = {}
        self.store.append_event(Event("promotion", event_type, {**context, **payload}))

    @staticmethod
    def _payload(row: dict[str, Any] | None) -> dict[str, Any]:
        if not row:
            return {}
        raw = row.get("payload", row)
        return json.loads(raw) if isinstance(raw, str) else raw

    @staticmethod
    def _version_from_row(row: dict[str, Any]) -> VersionRecord:
        if "payload" in row:
            payload = PromotionEngine._payload(row)
        else:
            payload = dict(row)
            if isinstance(payload.get("metadata"), str):
                payload["metadata"] = json.loads(payload["metadata"])
        payload["status"] = VersionStatus(payload["status"])
        return VersionRecord(**{key: payload[key] for key in ("version_id", "source_commit", "parent_version", "proposal_id", "experiment_id", "evidence_id", "created_at", "status", "version_path", "manifest_hash", "metadata")})

    @staticmethod
    def _request_from_row(row: dict[str, Any]) -> PromotionRequest:
        payload = PromotionEngine._payload(row)
        payload["approval_status"] = PromotionApprovalStatus(payload["approval_status"])
        payload["eligibility_status"] = PromotionEligibilityStatus(payload["eligibility_status"])
        payload["status"] = PromotionStatus(payload["status"])
        return PromotionRequest(**payload)

    @staticmethod
    def _promotion_record_from_row(row: dict[str, Any]) -> PromotionRecord:
        payload = PromotionEngine._payload(row)
        payload["final_status"] = PromotionStatus(payload["final_status"])
        return PromotionRecord(**payload)

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    @staticmethod
    def _new_id(prefix: str) -> str:
        return f"{prefix}_{uuid.uuid4().hex[:12]}"

    def _git_commit(self) -> str:
        try:
            result = subprocess.run(["git", "-C", str(self.source_root), "rev-parse", "HEAD"], capture_output=True, text=True, timeout=5, check=False)
            return result.stdout.strip() if result.returncode == 0 else "unknown"
        except Exception:
            return "unknown"
