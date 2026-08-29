"""Memory scope, the additive column, and the one migration P5 owes (07 §5, Q6).

The requirement, in the spec's words: a ``scope_key`` column plus an index, back-filled ``local``, with
retrieval filtering by scope; ``memories`` becomes a deprecated write-only mirror for one release, folded
away by ``scripts/migrate_memory_consolidation.py``, which is "additive-only ``ALTER TABLE ADD COLUMN``
guarded by ``schema_version`` + ``ProductionSchemaManager``", idempotent, and "verified by a row-conservation
assertion".

Three of those words are doing the work, and each gets its own test here:

* *additive* - an existing database gains the column without losing a row, and no table is dropped or
  renumbered;
* *idempotent* - the migration can be run twice, and the second run moves nothing because the guard is the
  data (a deterministic ``memory_key``), not a ledger someone could delete;
* *row conservation* - the destination count equals the source count plus what moved, the mirror keeps its
  own rows, and every migrated row is read back through the real record loader, because a migration that
  writes a row the reader cannot parse has migrated nothing.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from evo_agent import migrations
from evo_agent.memory import MemoryManager, RetrievalQuery
from evo_agent.security import SecurityPolicy
from evo_agent.storage import SQLiteStore


@pytest.fixture()
def store(tmp_path: Path) -> SQLiteStore:
    return SQLiteStore(tmp_path / ".evo" / "agent.sqlite3")


class TestSchema:
    def test_the_column_and_index_are_declared_on_a_new_database(self, store: SQLiteStore) -> None:
        with store._connect() as db:
            columns = {row["name"] for row in db.execute("PRAGMA table_info(memory_records)").fetchall()}
            indexes = {row["name"] for row in db.execute("PRAGMA index_list(memory_records)").fetchall()}
        assert "scope_key" in columns
        assert "idx_memory_scope" in indexes

    def test_the_legacy_mirror_is_still_declared(self, store: SQLiteStore) -> None:
        # "Deprecated" is a schedule, not a deletion: the table stays for one release because the kernel's
        # documented fallback reads it, and removing it early would turn a degradation path into a crash.
        with store._connect() as db:
            assert "memories" in {row["name"] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            assert "memory_records" in {row["name"] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}

    def test_the_p5_inventory_tables_exist_and_are_readable(self, store: SQLiteStore) -> None:
        with store._connect() as db:
            names = {row["name"] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert {"skill_packages", "skill_grants", "mcp_servers", "mcp_tools"} <= names
        assert store.list_skill_packages() == [] and store.list_skill_grants() == []
        assert store.list_mcp_servers() == [] and store.list_mcp_tools() == []

    def test_an_older_database_gains_the_column_without_losing_rows(self, tmp_path: Path) -> None:
        # A hand-built pre-P5 table, then opened by the current store: the additive ALTER is the whole
        # upgrade story, so a row written before the column existed must still be there, readable, and
        # scoped `local` - not filtered out of the operator's own agent by an absence.
        path = tmp_path / "old.db"
        with sqlite3.connect(path) as db:
            db.execute(
                "CREATE TABLE memory_records (memory_id TEXT PRIMARY KEY, memory_type TEXT NOT NULL, content TEXT NOT NULL,"
                " summary TEXT NOT NULL, source TEXT NOT NULL, source_id TEXT NOT NULL, provenance TEXT NOT NULL,"
                " confidence TEXT NOT NULL, confidence_score REAL NOT NULL, importance REAL NOT NULL, relevance REAL NOT NULL,"
                " created_at TEXT NOT NULL, updated_at TEXT NOT NULL, last_accessed_at TEXT, access_count INTEGER NOT NULL,"
                " version INTEGER NOT NULL, memory_version TEXT NOT NULL, status TEXT NOT NULL, expiration TEXT,"
                " valid_from TEXT, valid_until TEXT, agent_version TEXT NOT NULL, architecture_version TEXT NOT NULL,"
                " source_version TEXT NOT NULL, environment TEXT, knowledge_kind TEXT, fingerprint TEXT NOT NULL,"
                " memory_key TEXT, source_ids TEXT NOT NULL, first_seen TEXT, last_seen TEXT, occurrence_count INTEGER NOT NULL,"
                " metadata TEXT NOT NULL, payload TEXT NOT NULL DEFAULT '{}')"
            )
            db.execute(
                "INSERT INTO memory_records VALUES ('m-1','episodic','an old note','old','observation','s','{}','medium',0.5,0.5,0.5,"
                "'2026-01-01T00:00:00+00:00','2026-01-01T00:00:00+00:00',NULL,0,1,'memory-v1','ACTIVE',NULL,NULL,NULL,'','', '','{}',"
                "NULL,'fp-1','k-1','[]',NULL,NULL,1,'{}','{}')"
            )
        reopened = SQLiteStore(path)
        with reopened._connect() as db:
            columns = {row["name"] for row in db.execute("PRAGMA table_info(memory_records)").fetchall()}
            row = db.execute("SELECT scope_key, content FROM memory_records WHERE memory_id = 'm-1'").fetchone()
        assert "scope_key" in columns
        assert row is not None and row["scope_key"] == "local" and row["content"] == "an old note"

    def test_the_schema_version_guard_refuses_a_newer_database(self, store: SQLiteStore) -> None:
        from evo_agent.production import ProductionSchemaManager

        assert ProductionSchemaManager(store).ensure_memory_scope() == 1
        with store._connect() as db:
            db.execute("INSERT OR REPLACE INTO production_schema(schema_name, schema_version, updated_at) VALUES ('memory', 99, 'now')")
        with pytest.raises(RuntimeError, match="newer than this application"):
            ProductionSchemaManager(store).ensure_memory_scope()


class TestScoping:
    def test_a_specialist_row_is_invisible_to_the_parents_query_and_visible_in_a_listing(self, store: SQLiteStore, tmp_path: Path) -> None:
        manager = MemoryManager(store, tmp_path / "workspace")
        manager.capture_learning({"affected_component": "planner", "success": True})
        manager.capture_specialist({"specialist_id": "s-1", "specialist_task_id": "t-1", "success": True})
        scoped = manager.memory_store.list(limit=50, scope="local")
        everything = manager.memory_store.list(limit=50)
        assert len(scoped) == 1 and len(everything) == 2
        assert {item.scope_key for item in everything} == {"local", "subagent:s-1"}

    def test_retrieval_defaults_to_local_and_widens_only_on_request(self, store: SQLiteStore, tmp_path: Path) -> None:
        manager = MemoryManager(store, tmp_path / "workspace")
        manager.capture_specialist({"specialist_id": "s-1", "specialist_task_id": "t-1", "success": True, "quality_score": 0.9})
        assert RetrievalQuery(goal="specialist").scope == "local"
        default = manager.retrieval.retrieve(RetrievalQuery(goal="specialist s-1 completed task t-1"))
        assert default == []
        widened = manager.retrieval.retrieve(RetrievalQuery(goal="specialist s-1 completed task t-1", scope="*"))
        assert [item.memory.scope_key for item in widened] == ["subagent:s-1"]

    def test_a_subagent_scope_cannot_be_forged_into_a_local_read(self, store: SQLiteStore, tmp_path: Path) -> None:
        # The filter is `scope_key = ?`, not a LIKE: asking for the parent's own scope must not turn into
        # "everything beginning with subagent", and the wildcard for "all" is one documented value, not a
        # prefix pattern an agent could improvise.
        manager = MemoryManager(store, tmp_path / "workspace")
        manager.capture_specialist({"specialist_id": "s-1", "specialist_task_id": "t-1", "success": True})
        for asked in ("local", "subagent", "%", "subagent%", "LOCAL", ""):
            assert store.find_memories(None, None, 50, asked) == [], asked
        # The two documented ways to ask for everything, and nothing else: `*` for a caller that means it,
        # `None` for a listing. Both are the operator's view; retrieval always passes a real scope.
        assert len(store.find_memories(None, None, 50, "*")) == 1
        assert len(store.find_memories(None, None, 50, None)) == 1

    def test_scope_is_part_of_the_record_and_survives_a_round_trip(self, store: SQLiteStore, tmp_path: Path) -> None:
        manager = MemoryManager(store, tmp_path / "workspace")
        record = manager.capture_learning({"affected_component": "planner", "success": True})
        assert record.scope_key == "local"
        loaded = manager.get(record.memory_id)
        assert loaded is not None and loaded.scope_key == "local"
        assert json.dumps(record.to_dict())

    def test_an_update_does_not_move_a_row_between_scopes(self, store: SQLiteStore, tmp_path: Path) -> None:
        from evo_agent.memory import ProvenanceSource

        manager = MemoryManager(store, tmp_path / "workspace")
        record = manager.capture_specialist({"specialist_id": "s-1", "specialist_task_id": "t-1", "success": True})
        manager.update(record.memory_id, "corrected summary", "operator review", "operator:1", source=ProvenanceSource.USER_INPUT)
        rows = store.find_memories(None, None, 50, "*")
        # Two rows now - the superseded one and the new version - and both stay in the subagent's scope. The
        # property is "no row in this lineage is `local`", which is what the version bump must not break.
        assert rows and {row["scope_key"] for row in rows} == {"subagent:s-1"}
        assert store.find_memories(None, None, 50, "local") == []
        statuses = {row["status"] for row in rows}
        # Lowercase, because the column stores `MemoryStatus.value`: what matters is that the superseded
        # predecessor keeps its own scope too, so no version of the lineage is readable as local.
        assert statuses == {"active", "superseded"}, statuses


class TestConsolidationMigration:
    def test_it_reports_the_state_honestly_before_and_after(self, store: SQLiteStore) -> None:
        state = migrations.memory_scope_state(store)
        assert state["scope_column"] is True and state["memory_records"] == 0 and state["legacy_mirror_rows"] == 0
        store.add_memory("experience", "a note the kernel wrote", "2026-01-01T00:00:00+00:00")
        assert migrations.memory_scope_state(store)["legacy_mirror_rows"] == 1

    def test_rows_are_folded_in_and_conserved(self, store: SQLiteStore) -> None:
        for index in range(3):
            store.add_memory("experience", f"legacy note {index}", f"2026-01-0{index + 1}T00:00:00+00:00")
        report = migrations.consolidate_memories(store)
        assert report["ok"] is True and report["problems"] == []
        assert report["moved"] and len(report["moved"]) == 3
        assert report["before"] == 0 and report["after"] == 3
        assert report["mirror_rows"] == 3, "the deprecated mirror is preserved, not deleted"
        rows = store.find_memories(None, None, 50, "local")
        assert len(rows) == 3 and all(row["source"] == "observation" for row in rows)
        assert all(row["memory_key"].startswith("memories:") for row in rows)

    def test_a_migrated_row_is_readable_as_a_record_not_just_a_row(self, store: SQLiteStore) -> None:
        # This is the assertion the first draft of the migration failed: a hand-rolled INSERT produced a row
        # whose `payload` the record loader could not parse, so the memory was invisible instead of
        # migrated, and nothing in the database noticed.
        store.add_memory("cognitive", json.dumps({"goal_id": "g-1", "outcome": "verified", "summary": "done"}), "2026-01-01T00:00:00+00:00")
        report = migrations.consolidate_memories(store)
        manager = MemoryManager(store, store.path.parents[1])
        loaded = manager.get(report["moved"][0]["memory_id"])
        assert loaded is not None and loaded.scope_key == "local"
        assert "goal_id" in loaded.content
        assert store.memories_by_key("memories:1", limit=1)

    def test_running_it_twice_moves_nothing(self, store: SQLiteStore) -> None:
        store.add_memory("experience", "one note", "2026-01-01T00:00:00+00:00")
        first = migrations.consolidate_memories(store)
        second = migrations.consolidate_memories(store)
        assert first["ok"] and second["ok"]
        assert second["moved"] == [] and second["already_present"] == ["memories:1"]
        assert second["before"] == second["after"] == 1

    def test_dry_run_writes_nothing(self, store: SQLiteStore) -> None:
        store.add_memory("experience", "one note", "2026-01-01T00:00:00+00:00")
        report = migrations.consolidate_memories(store, dry_run=True)
        assert report["ok"] is True and len(report["moved"]) == 1 and report["after"] == 0
        assert store.count_memory_records() == 0
        applied = migrations.consolidate_memories(store)
        assert applied["after"] == 1

    def test_the_row_conservation_problem_is_reported_not_raised_away(self, store: SQLiteStore, monkeypatch) -> None:
        # The assertion is a *reported problem*, because the migration runs inside a deploy step that has
        # to print what happened before it fails; and it fires, because a wrapper that quietly swallows a
        # write would otherwise report success.
        real_count = store.count_memory_records
        calls = {"n": 0}

        def inflated() -> int:
            calls["n"] += 1
            return real_count() + 5 if calls["n"] > 1 else real_count()

        monkeypatch.setattr(store, "count_memory_records", inflated)
        store.add_memory("experience", "one note", "2026-01-01T00:00:00+00:00")
        report = migrations.consolidate_memories(store)
        assert report["ok"] is False
        assert any("row conservation failed" in text for text in report["problems"])

    def test_the_migration_never_emits_a_destructive_statement(self) -> None:
        source = Path(migrations.__file__).read_text(encoding="utf-8")
        for forbidden in ("DROP TABLE", "DROP COLUMN", "DELETE FROM", "TRUNCATE", "ALTER TABLE ... RENAME"):
            assert forbidden not in source.upper(), forbidden

    def test_ensure_memory_scope_is_idempotent_and_an_older_database_converges(self, store: SQLiteStore, tmp_path: Path) -> None:
        report = migrations.ensure_memory_scope(store)
        assert report["ok"] is True and report["schema_version"] == 1
        assert migrations.ensure_memory_scope(store) == report, "the check must be re-runnable"
        # A database whose memory table predates the column converges on the same state, because the store
        # adds the column on open and the version row is written by the guard, not by a script someone has
        # to remember to run.
        old = tmp_path / "old2.db"
        columns = ("memory_id TEXT PRIMARY KEY, memory_type TEXT NOT NULL, content TEXT NOT NULL, summary TEXT NOT NULL,"
                   " source TEXT NOT NULL, source_id TEXT NOT NULL, provenance TEXT NOT NULL, confidence TEXT NOT NULL,"
                   " confidence_score REAL NOT NULL, importance REAL NOT NULL, relevance REAL NOT NULL, created_at TEXT NOT NULL,"
                   " updated_at TEXT NOT NULL, last_accessed_at TEXT, access_count INTEGER NOT NULL, version INTEGER NOT NULL,"
                   " memory_version TEXT NOT NULL, status TEXT NOT NULL, expiration TEXT, valid_from TEXT, valid_until TEXT,"
                   " agent_version TEXT NOT NULL, architecture_version TEXT NOT NULL, source_version TEXT NOT NULL,"
                   " environment TEXT, knowledge_kind TEXT, fingerprint TEXT NOT NULL, memory_key TEXT, source_ids TEXT NOT NULL,"
                   " first_seen TEXT, last_seen TEXT, occurrence_count INTEGER NOT NULL, metadata TEXT NOT NULL,"
                   " payload TEXT NOT NULL DEFAULT '{}'")
        with sqlite3.connect(old) as db:
            db.execute(f"CREATE TABLE memory_records ({columns})")
            db.execute("INSERT INTO memory_records (memory_id, memory_type, content, summary, source, source_id, provenance,"
                       " confidence, confidence_score, importance, relevance, created_at, updated_at, access_count, version,"
                       " memory_version, status, agent_version, architecture_version, source_version, fingerprint,"
                       " occurrence_count, metadata, source_ids) VALUES ('m-9','episodic','an old memory','old','observation','s','{}',"
                       " 'medium',0.5,0.5,0.5,'2026-01-01T00:00:00+00:00','2026-01-01T00:00:00+00:00',0,1,'memory-v1','active',"
                       " '','', '', 'fp-9', 1, '{}', '[]')")
            db.execute("CREATE TABLE memories (memory_id INTEGER PRIMARY KEY, kind TEXT NOT NULL, content TEXT NOT NULL, created_at TEXT NOT NULL)")
            db.execute("INSERT INTO memories(kind, content, created_at) VALUES ('experience','a mirror row','2026-01-01T00:00:00+00:00')")
        upgraded = SQLiteStore(old)
        state = migrations.ensure_memory_scope(upgraded)
        assert state["ok"] is True and state["scopes"] == {"local": 1} and state["legacy_mirror_rows"] == 1
        migrated = migrations.consolidate_memories(upgraded)
        assert migrated["ok"] is True and len(migrated["moved"]) == 1
        assert migrated["scope"]["scopes"] == {"local": 2}

    def test_the_cli_script_reports_and_exits_nonzero_on_a_problem(self, tmp_path: Path, monkeypatch, capsys) -> None:
        import importlib.util

        spec = importlib.util.spec_from_file_location("migrate_script", Path(migrations.__file__).parents[1] / "scripts" / "migrate_memory_consolidation.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        missing = module.main(["--workspace", str(tmp_path / "nowhere")])
        assert missing == 1 and "database not found" in capsys.readouterr().out
        (tmp_path / ".evo").mkdir()
        SQLiteStore(tmp_path / ".evo" / "agent.sqlite3")
        store = SQLiteStore(tmp_path / ".evo" / "agent.sqlite3")
        store.add_memory("experience", "legacy for the script", "2026-01-01T00:00:00+00:00")
        capsys.readouterr()
        assert module.main(["--workspace", str(tmp_path), "--check"]) == 0
        checked = json.loads(capsys.readouterr().out)
        assert checked["scope_column"] is True and checked["ok"] is True
        assert module.main(["--workspace", str(tmp_path)]) == 0
        applied = json.loads(capsys.readouterr().out)
        assert applied["ok"] is True and len(applied["moved"]) == 1
        # A second call is still zero, and still moves nothing: the script is safe in a deploy hook.
        assert module.main(["--workspace", str(tmp_path)]) == 0
        assert json.loads(capsys.readouterr().out)["moved"] == []
