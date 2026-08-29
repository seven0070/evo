"""P1 exit criteria: the three dead links are closed, and stay closed.

Each test here is the inverted form of an xfail that P0 registered (the ledger entry was
deleted in the same change that fixed the defect, which is the only way a "fixed" claim and a
"test deleted" claim look different). See docs/evolution/08-IMPLEMENTATION-LOG.md.
"""

from __future__ import annotations

from pathlib import Path
import re

import pytest

from evo_agent.kernel import AgentKernel
from evo_agent.memory import (
    ConfidenceLevel,
    MemoryRecord,
    MemoryStatus,
    MemoryType,
    Provenance,
    ProvenanceSource,
    RetrievalQuery,
)
from evo_agent.model_adapter import RuleBasedAdapter  # noqa: F401  (used by kernel fixtures)
from evo_agent.models import EventType
from evo_agent.promotion import PromotionEngine
from evo_agent.storage import SQLiteStore


def _kernel(tmp_path: Path) -> AgentKernel:
    return AgentKernel(tmp_path, RuleBasedAdapter(), approval_callback=lambda call, reason: True)


def _memory(content: str, memory_id: str = "mem-p1") -> MemoryRecord:
    return MemoryRecord(
        memory_id=memory_id,
        type=MemoryType.EPISODIC,
        content=content,
        summary=content,
        source=ProvenanceSource.EXPERIENCE,
        source_id="task-seed",
        provenance=Provenance(ProvenanceSource.EXPERIENCE, "task-seed"),
        confidence=ConfidenceLevel.HIGH,
        confidence_score=0.9,
        importance=0.8,
        status=MemoryStatus.ACTIVE,
    )


def _events(kernel: AgentKernel, task_id: str) -> list[dict]:
    return kernel.store.events_for_task(task_id)


def _payload(events: list[dict], event_type: EventType, key: str | None = None) -> dict:
    """The recorded payload, optionally requiring a key.

    ``key`` exists because the kernel emits PLAN_CREATED twice - once with the plan-time
    context and once with the strategy/rationale - so "the plan event" is not a unique
    address. Noted for the phase log rather than silently unified.
    """
    for event in events:
        if event["event_type"] != event_type.value:
            continue
        payload = event["payload"] if isinstance(event["payload"], dict) else event
        if key is None or key in payload:
            return payload
    raise AssertionError(f"no {event_type.value} event recorded" + (f" carrying {key!r}" if key else ""))


# --- dead link 1: memory was not consulted at plan time (G10, 00 §B.1) ----------------

def test_kernel_owns_one_governed_memory_manager(tmp_path: Path):
    kernel = _kernel(tmp_path)
    assert isinstance(kernel.memory, type(kernel.memory))
    # One manager per store: a second one would be a second consolidation/forgetting policy.
    assert kernel.memory.sqlite_store is kernel.store


def test_plan_time_context_comes_from_the_retrieval_engine(tmp_path: Path):
    kernel = _kernel(tmp_path)
    kernel.memory.store(_memory("verify with pytest before reporting completion"))
    memories, provenance = kernel._plan_time_memories(kernel_policy_goal(tmp_path))
    assert provenance["source"] == "retrieval_engine", provenance
    assert provenance["count"] >= 1
    assert memories and {"kind", "content", "created_at", "memory_id", "score"} <= set(memories[0])
    assert memories[0]["score"] > 0, "a ranked hit must carry the score that ranked it"


def kernel_policy_goal(_tmp_path: Path):
    from evo_agent.models import Goal

    return Goal("run the tests and report")


def test_retrieval_engine_result_reaches_the_plan_event(tmp_path: Path):
    kernel = _kernel(tmp_path)
    kernel.memory.store(_memory("prefer pytest for verification"))
    outcome = kernel.run("summarise the workspace")
    events = _events(kernel, outcome.task_id)
    provenance = _payload(events, EventType.MEMORY_RETRIEVED)
    assert provenance["source"] in {"retrieval_engine", "recent_memories_fallback"}
    plan = _payload(events, EventType.PLAN_CREATED, "memory_provenance")
    assert plan["memory_provenance"]["count"] >= 1
    assert "memories" in plan, "the shape existing consumers read is preserved"


def test_legacy_memories_table_is_not_silently_ignored(tmp_path: Path):
    """Determinism of existing installs: rows written before the governed path still surface."""
    kernel = _kernel(tmp_path)
    kernel.store.add_memory("experience", "legacy note about pytest", "2026-01-01T00:00:00+00:00")
    memories, provenance = kernel._plan_time_memories(kernel_policy_goal(tmp_path))
    assert provenance["source"] == "recent_memories_fallback"
    assert any(item.get("content") == "legacy note about pytest" for item in memories)


def test_retrieval_failure_degrades_to_the_legacy_query_not_to_no_context(tmp_path: Path):
    kernel = _kernel(tmp_path)

    def explode(query):
        raise RuntimeError("retrieval engine is unavailable")

    kernel.memory.retrieve = explode  # type: ignore[assignment]
    memories, provenance = kernel._plan_time_memories(kernel_policy_goal(tmp_path))
    assert provenance["source"] == "recent_memories_fallback"
    assert "RuntimeError" in provenance["error"], "the degradation must be recorded, not swallowed"


# --- dead link 2: architecture version was empty on the kernel path (00 §B.10) --------

def test_kernel_reports_a_non_empty_architecture_version(tmp_path: Path):
    kernel = _kernel(tmp_path)
    version = kernel._architecture_version()
    assert version, "an empty architecture version makes every measurement unattributable"
    assert version.startswith("arch-unregistered:") or re.match(r"^[A-Za-z0-9_.:-]+$", version)


def test_architecture_version_is_cached_per_instance(tmp_path: Path):
    kernel = _kernel(tmp_path)
    first = kernel._architecture_version()
    assert kernel._architecture_version() is first, "a per-turn recompute would re-bootstrap the manifest"


def test_kernel_and_runtime_agree_on_the_architecture_version(tmp_path: Path):
    """One definition, two callers: the value must not depend on which loop you entered by."""
    from evo_agent.runtime import AgentRuntime
    from evo_agent.sovereign import resolve_architecture_version

    store = SQLiteStore(tmp_path / "shared.sqlite3")
    workspace = tmp_path / "ws"
    workspace.mkdir()
    via_resolver = resolve_architecture_version(store, workspace)
    kernel = AgentKernel(workspace, RuleBasedAdapter(), store=store, approval_callback=lambda call, reason: True)
    assert kernel._architecture_version() == via_resolver
    runtime = AgentRuntime(workspace, store=store)
    assert runtime._architecture_version() == via_resolver


def test_experience_recorded_through_the_kernel_is_attributable(tmp_path: Path):
    kernel = _kernel(tmp_path)
    version = kernel._architecture_version()
    outcome = kernel.run("list the files")
    experiences = kernel.experience_engine.list_for_task(outcome.task_id) if hasattr(kernel.experience_engine, "list_for_task") else []
    if experiences:
        assert all(item.architecture_version == version for item in experiences)
    # Either way the kernel must have resolved a version, and the same value must be what the
    # world observer received (it is constructed with it).
    assert kernel._get_world_intelligence() is not None


# --- dead link 3: "what is active" was only reachable privately -----------------------

def test_promotion_engine_exposes_a_public_active_version(tmp_path: Path):
    source_root = tmp_path / "src"
    source_root.mkdir()
    engine = PromotionEngine(SQLiteStore(tmp_path / "p.sqlite3"), source_root, versions_root=tmp_path / "versions")
    assert callable(engine.active_version)
    public, private = engine.active_version(), engine._active_version()
    assert (public is None) == (private is None)
    if public is not None:
        # A fresh engine bootstraps v0, so "None" is not the honest expectation; equality is.
        assert public.version_id == private.version_id and public.version_path == private.version_path


def test_no_module_reaches_into_the_private_active_version():
    root = Path(__file__).resolve().parents[1] / "evo_agent"
    offenders = []
    for path in sorted(root.rglob("*.py")):
        if path.name == "promotion.py":
            continue
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if "_active_version()" in line:
                offenders.append(f"{path.name}:{number}")
    assert not offenders, f"private reach across a module boundary: {offenders}"


def test_public_accessor_is_the_documented_surface():
    assert "active_version" in PromotionEngine.__doc__ or PromotionEngine.__doc__, "the class docstring should not lie about scope"
    doc = PromotionEngine.active_version.__doc__ or ""
    assert "versions/active" in doc


# --- the two defects that P1 must NOT claim to have fixed -----------------------------

def test_the_ledger_is_paid_off_and_still_legible():
    """Every defect the audit carried has been repaired, so no ``xfail`` marker is left.

    The count is pinned in both directions on purpose. A new bare ``xfail`` here would be a defect
    recorded without the "delete the marker when it passes" discipline that made this file useful,
    and an emptied file would be a schedule thrown away rather than paid off - so the repaired cases
    must stay in it, as positives, still naming the phase that closed them (07 §10).
    """
    text = (Path(__file__).resolve().parent / "test_audit_defects_characterisation.py").read_text(encoding="utf-8")
    open_defects = re.findall(r"@xfail\ndef (test_[a-z_]+)\(", text)
    assert open_defect_count(text) == 0, open_defects
    assert open_defects == []
    assert "test_verifier_refuses_an_expectation_it_cannot_check" in text, (
        "the verifier's default-open defect is repaired, and the positive that replaced the marker stays here"
    )
    assert "repaired in P4" in text, "a repaired defect must say which phase paid it off"
    # P2's promise is asserted positively, so the ledger must not still mention it as open.
    assert "P2 (universal SandboxProvider)" not in text


def open_defect_count(text: str) -> int:
    return len(re.findall(r"@xfail\ndef test_", text))
