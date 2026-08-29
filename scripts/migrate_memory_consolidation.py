#!/usr/bin/env python3
"""Fold the deprecated ``memories`` mirror into ``memory_records`` (07 §5, Q6).

Additive, idempotent, and verified by a row-conservation assertion rather than by optimism::

    python3 scripts/migrate_memory_consolidation.py --workspace ./workspace            # apply
    python3 scripts/migrate_memory_consolidation.py --workspace ./workspace --check    # report only
    python3 scripts/migrate_memory_consolidation.py --workspace ./workspace --dry-run  # say what would move

The script never drops or renames a table, never deletes a legacy row, and exits non-zero when a problem is
reported - a migration that prints a failure and returns 0 is a migration someone will put in a deploy
script and never read. ``--check`` exists because the honest question after a migration is not "did the
command run" but "is the schema in the state the application requires", and that is true whether or not any
row moved.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from evo_agent.migrations import consolidate_memories, ensure_memory_scope  # noqa: E402
from evo_agent.storage import SQLiteStore  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--workspace", default="./workspace", help="Workspace whose .evo/agent.sqlite3 is migrated")
    parser.add_argument("--check", action="store_true", help="Report scope state and schema version without moving rows")
    parser.add_argument("--dry-run", action="store_true", help="Compute the move and the assertions, write nothing")
    parser.add_argument("--limit", type=int, default=10_000, help="Maximum legacy rows to consider")
    args = parser.parse_args(argv)

    database = Path(args.workspace).expanduser().resolve() / ".evo" / "agent.sqlite3"
    if not database.exists():
        print(json.dumps({"ok": False, "error": f"no database at {database}", "problems": ["database not found; nothing to migrate"]}, indent=2))
        return 1
    store = SQLiteStore(database)
    if args.check:
        report = ensure_memory_scope(store)
        print(json.dumps(report, indent=2, default=str))
        return 0 if report.get("ok") else 1
    report = consolidate_memories(store, dry_run=args.dry_run, limit=args.limit)
    print(json.dumps({**report, "database": str(database)}, indent=2, default=str))
    return 0 if report.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
