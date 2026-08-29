"""The one migration P5 owes the memory model, and the assertions that make it safe (07 §5, Q6).

Two things happen here, and neither is a schema rewrite:

**`ensure_memory_scope`** records that this build's :class:`SQLiteStore` has seen the ``scope_key`` column.
The column itself is added by the store, so a fresh install and an upgraded one converge without a script -
which is the only version that is correct on the day somebody installs rather than upgrades.

**`consolidate_memories`** folds the deprecated ``memories`` mirror into ``memory_records``. The spec calls
that table "write-only, one release, folded away by migration", and the fold has three properties that
matter more than the copy: it is *idempotent* (guarded by a deterministic ``memory_key``, so a re-run moves
nothing), it *conserves rows* (the destination count must equal the source count plus what was moved, and
the mirror is never deleted), and every row it writes is *read back through the record loader* before the
report says it moved.

That third one is here because the first draft of this migration was wrong in the way a hand-rolled INSERT
is always wrong: it wrote a row the reader could not parse, so the migrated memory was invisible rather than
migrated, and nothing in the database noticed. Building the record with the same factory the live capture
paths use, then reading it back, is what turns "an INSERT ran" into "a memory exists".
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

#: The key prefix that makes the fold idempotent. Deterministic from the legacy row's own primary key, so
#: no ledger table and no "already migrated" flag can disagree with the data.
LEGACY_KEY_PREFIX = "memories:"
#: Every unscoped legacy row belongs to the operator's own agent. There is no evidence for a different
#: answer, and inventing one (``"unknown"``, say) would mean the retrieval default silently excludes it.
DEFAULT_SCOPE = "local"


def memory_scope_state(store: Any) -> dict[str, Any]:
    """Whether the scoping column and its index exist, and how the rows are distributed."""
    with store._connect() as db:
        columns = {row["name"] for row in db.execute("PRAGMA table_info(memory_records)").fetchall()}
        indexes = {row["name"] for row in db.execute("PRAGMA index_list(memory_records)").fetchall()}
        try:
            distribution = {str(row["scope_key"]): int(row["n"]) for row in db.execute("SELECT scope_key, COUNT(*) AS n FROM memory_records GROUP BY scope_key").fetchall()}
        except Exception:  # noqa: BLE001 - no column yet, which is the state being reported
            distribution = {}
        mirror = int(db.execute("SELECT COUNT(*) AS n FROM memories").fetchone()["n"])
    return {
        "scope_column": "scope_key" in columns,
        "scope_index": "idx_memory_scope" in indexes,
        "scopes": dict(sorted(distribution.items())),
        "memory_records": sum(distribution.values()),
        "legacy_mirror_rows": mirror,
        "default_scope": DEFAULT_SCOPE,
    }


def ensure_memory_scope(store: Any) -> dict[str, Any]:
    """Verify the additive column exists and record the memory schema version. Refuses a *newer* database."""
    from .production import ProductionSchemaManager

    state = memory_scope_state(store)
    if not state["scope_column"]:
        return {**state, "ok": False, "problems": ["memory_records.scope_key is absent; open the database with a build whose SQLiteStore adds it"]}
    version = ProductionSchemaManager(store).ensure_memory_scope()
    return {**state, "ok": True, "schema_version": version, "problems": []}


def consolidate_memories(store: Any, *, dry_run: bool = False, limit: int = 10_000) -> dict[str, Any]:
    """Fold ``memories`` into ``memory_records`` once, idempotently, with the row count proved.

    The record is built by ``MemoryManager._record`` - a private helper reached on purpose, because it is
    the factory the live capture paths use, and a migration that hand-rolls its own column list produces
    rows the reader cannot parse. The readability check below is what makes that shortcut safe.
    """
    from .memory import ConfidenceLevel, MemoryManager, MemoryType, ProvenanceSource, _summary

    manager = MemoryManager(store)
    before = int(store.count_memory_records())
    legacy_rows = store.legacy_memory_rows(limit=limit)
    moved: list[dict[str, Any]] = []
    already: list[str] = []
    problems: list[str] = []

    for row in legacy_rows:
        key = f"{LEGACY_KEY_PREFIX}{row['memory_id']}"
        if store.memories_by_key(key, limit=1):
            already.append(key)
            continue
        if dry_run:
            moved.append({"memory_key": key, "dry_run": True})
            continue
        content = str(row.get("content") or "")
        record = manager._record(
            MemoryType.EPISODIC,
            content,
            _summary(content),
            ProvenanceSource.OBSERVATION,
            f"legacy:{row.get('kind') or 'memory'}:{row['memory_id']}",
            ConfidenceLevel.LOW,
            0.4,
            0.35,
            key=key,
            metadata={"migrated_from": "memories", "legacy_kind": str(row.get("kind") or ""), "legacy_id": row["memory_id"]},
        )
        record.scope_key = DEFAULT_SCOPE
        manager.store(record)
        moved.append({"memory_key": key, "memory_id": record.memory_id})

    after = int(store.count_memory_records())
    mirror = int(store.count_legacy_memories())
    if not dry_run:
        if after != before + len(moved):
            problems.append(f"row conservation failed: {before} + {len(moved)} moved != {after} present")
        unreadable = [item["memory_id"] for item in moved if manager.memory_store.get(str(item["memory_id"])) is None]
        if unreadable:
            problems.append(f"{len(unreadable)} migrated rows are not readable back as memory records: {unreadable[:4]}")
        scoped = memory_scope_state(store)
        if scoped["memory_records"] != after:
            problems.append(f"scope distribution counts {scoped['memory_records']} rows while the table holds {after}")
        for item in moved:
            record = manager.memory_store.get(str(item["memory_id"])) if item.get("memory_id") else None
            if record is not None and str(getattr(record, "scope_key", DEFAULT_SCOPE)) != DEFAULT_SCOPE:
                problems.append(f"{item['memory_key']} migrated with scope {record.scope_key!r}, expected {DEFAULT_SCOPE!r}")
    if mirror != len(legacy_rows) and limit >= len(legacy_rows):
        problems.append(f"the mirror changed size during migration ({len(legacy_rows)} -> {mirror}); a migration must not write to the source")
    return {
        "ok": not problems,
        "dry_run": dry_run,
        "moved": moved,
        "already_present": already,
        "legacy_rows_seen": len(legacy_rows),
        "before": before,
        "after": after,
        "mirror_rows": mirror,
        "scope": memory_scope_state(store),
        "problems": problems,
        "note": "the deprecated mirror is preserved, not dropped: folding a copy away is a schema decision for a release, and deleting operator history is not part of it",
    }


__all__ = ["DEFAULT_SCOPE", "LEGACY_KEY_PREFIX", "consolidate_memories", "ensure_memory_scope", "memory_scope_state"]
