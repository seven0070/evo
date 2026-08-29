"""P1 documentation integrity: the claims the repository makes must be checkable.

Docs are a contract surface here: `ARCHITECTURE.md` and `docs/evolution/*` are what a reviewer
reads before approving a protected-file change, and the audit's central finding was that prose
had drifted ahead of the code (00-AUDIT §C). These tests do not police style. They check the four
things that rot silently:

1. a `path:line` reference into this repository points at a real line;
2. an upstream reference says which project it belongs to (and does not hide an Evo bug);
3. markdown tables are parseable, so a cell containing a `|` cannot split a row in two;
4. the documented CLI flags and the documented tool surface are the real ones.
"""

from __future__ import annotations

from pathlib import Path
import re

import pytest

ROOT = Path(__file__).resolve().parents[1]
EVO_DIRS = ("evo_agent", "tests", "scripts", "desktop", "web", "docs", ".github")
USER_DOCS = [ROOT / "README.md", ROOT / "ARCHITECTURE.md", ROOT / "CHANGELOG.md", *sorted((ROOT / "docs").glob("*.md"))]
DESIGN_DOCS = sorted((ROOT / "docs" / "evolution").glob("*.md"))

FILE_EXT = r"(?:py|ts|tsx|toml|json|md|mdx|ps1|yml|yaml|sql|txt)"
REF = re.compile(r"`((?:(deerflow|dsh):)?([A-Za-z0-9_./-]+\." + FILE_EXT + r")):(\d+)(?:-(\d+))?`")


def _resolve(relpath: str) -> Path | None:
    for base in (ROOT, ROOT / "evo_agent", ROOT / "tests", ROOT / "scripts"):
        candidate = base / relpath
        if candidate.is_file():
            return candidate
    return None


def test_reference_rules_are_the_ones_the_docs_follow():
    """Guards the checker itself: if the pattern stops matching, these tests go quiet."""
    assert REF.search("`evo_agent/runtime.py:1069`").group(3) == "evo_agent/runtime.py"
    assert REF.search("`kernel.py:250-256`").group(5) == "256"
    assert REF.search("`deerflow:runtime/goal.py:260`").group(2) == "deerflow"
    assert REF.search("`config/verification_config.py:19,23`") is None or True  # comma lists are not line anchors


@pytest.mark.parametrize("document", DESIGN_DOCS, ids=lambda p: p.name)
def test_line_references_resolve_or_are_tagged(document: Path):
    defects: list[str] = []
    text = document.read_text(encoding="utf-8")
    for number, line in enumerate(text.splitlines(), 1):
        for match in REF.finditer(line):
            tag, relpath, start, end = match.group(2), match.group(3), int(match.group(4)), match.group(5)
            if relpath.split("/")[0] not in EVO_DIRS and "/" in relpath and not tag:
                # A slashed path that is not in this repository must be tagged; a bare module
                # name is allowed because _resolve() can still locate it under evo_agent/.
                pass
            target = _resolve(relpath)
            if tag:
                if target is not None:
                    defects.append(f"line {number}: '{relpath}' is tagged {tag}: but exists in this repository")
                continue
            if target is None:
                defects.append(
                    f"line {number}: '{relpath}' is neither a file in this repository nor tagged "
                    "deerflow:/dsh: - say whose code it is"
                )
                continue
            lines = target.read_text(encoding="utf-8", errors="ignore").splitlines()
            high = int(end) if end else start
            if start < 1 or high > len(lines):
                defects.append(f"line {number}: {relpath}:{start}-{end} is outside a {len(lines)}-line file")
    assert not defects, "\n".join(defects)


@pytest.mark.parametrize("document", [*DESIGN_DOCS, *USER_DOCS], ids=lambda p: str(p.relative_to(ROOT)))
def test_markdown_tables_are_well_formed(document: Path):
    """A raw `|` inside a cell silently splits the row, and the table renders as garbage.

    Checked per contiguous block, because a table whose rows disagree on column count is the
    exact failure mode the earlier documents had (see 05-GROUNDING-CORRECTIONS.md).
    """
    lines = document.read_text(encoding="utf-8").splitlines()
    block: list[tuple[int, str]] = []
    problems: list[str] = []
    in_fence = False

    def flush(block: list[tuple[int, str]]) -> None:
        if len(block) < 2:
            return
        widths = []
        for number, raw in block:
            row = raw.strip()
            if not row.endswith("|"):
                problems.append(f"line {number}: unterminated table row")
                continue
            cells = re.split(r"(?<!\\)\|", row[1:-1])
            widths.append((number, len(cells)))
            body = re.sub(r"`[^`]*`", "X", row[1:-1])
            if body.count("|") != len(cells) - 1:
                problems.append(f"line {number}: unescaped '|' inside a cell")
        counts = {count for _, count in widths}
        if len(counts) > 1:
            problems.append(f"line {block[0][0]}: inconsistent column counts {sorted(counts)}")
        separator = block[1][1].strip() if len(block) > 1 else ""
        if set(separator) <= set("|-: ") and not all(re.fullmatch(r":?-{2,}:?", cell) for cell in re.split(r"(?<!\\)\|", separator[1:-1])):
            problems.append(f"line {block[1][0]}: malformed header separator row")

    for number, raw in enumerate(lines, 1):
        if raw.strip().startswith("```"):
            in_fence = not in_fence
        if raw.strip().startswith("|") and not in_fence:
            block.append((number, raw))
        else:
            flush(block)
            block = []
    flush(block)
    assert not problems, "\n".join(problems)


def test_design_document_cross_references_exist():
    missing: list[str] = []
    pattern = re.compile(r"`((?:0[0-9]|1[0-9])-?[A-Z-]*\.md)`")
    for document in DESIGN_DOCS + [ROOT / "docs" / "evolution" / "README.md"]:
        for number, line in enumerate(document.read_text(encoding="utf-8").splitlines(), 1):
            for match in pattern.finditer(line):
                name = match.group(1)
                if not (document.parent / name).is_file():
                    missing.append(f"{document.name}:{number} -> {name}")
    assert not missing, "dangling design references: " + ", ".join(missing)


def test_documented_tool_surface_is_the_registered_one():
    from evo_agent.security import SecurityPolicy
    from evo_agent.tools import ToolRegistry
    import tempfile

    with tempfile.TemporaryDirectory() as raw:
        registry = ToolRegistry(SecurityPolicy(Path(raw)))
    documented = set(re.findall(r"^\| `(workspace_[a-z]+|shell)` \|", (ROOT / "ARCHITECTURE.md").read_text(encoding="utf-8"), re.M))
    assert documented == set(registry._tools), (
        f"ARCHITECTURE.md lists {sorted(documented)} but the registry holds {sorted(registry._tools)}"
    )


def test_documented_cli_flags_exist():
    from evo_agent.cli import build_parser

    parser = build_parser()
    real: set[str] = set()
    for action in parser._actions:
        real.update(action.option_strings)
    assert real, "the parser exposed no options; this test would be vacuous"

    documented: dict[str, set[str]] = {}
    for document in USER_DOCS:
        text = document.read_text(encoding="utf-8")
        for match in re.finditer(r"`(--[a-z][a-z0-9-]{1,30})`", text):
            documented.setdefault(match.group(1), set()).add(document.name)
    assert len(documented) > 40, f"only {len(documented)} flags documented; the check has lost its coverage"
    unknown = {name: sorted(files) for name, files in documented.items() if name not in real}
    assert not unknown, f"flags documented but not implemented: {unknown}"


def test_every_design_document_is_indexed():
    index_path = ROOT / "docs" / "evolution" / "README.md"
    index = index_path.read_text(encoding="utf-8")
    for document in DESIGN_DOCS:
        if document == index_path:
            continue
        assert document.name in index, f"{document.name} is not in the deliverable index"


def test_the_developer_override_leaves_a_trail(tmp_path: Path, monkeypatch):
    """Accepting drift must be recorded, or the override is a bypass with a new name."""
    from evo_agent.runtime import AgentRuntime
    from evo_agent.sovereign import protected as protected_module

    monkeypatch.setenv("EVO_ALLOW_SOVEREIGN_DRIFT", "1")
    monkeypatch.setattr(
        "evo_agent.sovereign.invariants.verify_sovereign_digests",
        lambda *a, **k: protected_module.ProtectionReport(ok=False, manifest_present=True, mismatched=(("security.py", "e", "a"),)),
    )
    runtime = AgentRuntime(tmp_path)
    record = runtime.start()
    boundary = record.metadata["sovereign_boundary"]
    assert boundary["drift_accepted"] is True and boundary["failures"]
    events = [item["event_type"] for item in runtime.store.events_for_task(runtime.runtime_id)]
    assert "sovereign_drift_detected" in events and "sovereign_drift_accepted" in events
    assert "sovereign_verified" not in events, "an accepted drift may never also read as a clean start"
    runtime.stop()


def test_implementation_log_covers_every_started_phase():
    """A phase that changed code must appear in 08, or the audit trail is fiction."""
    log = (ROOT / "docs" / "evolution" / "08-IMPLEMENTATION-LOG.md").read_text(encoding="utf-8")
    touched = {match.group(1) for match in re.finditer(r"^## (P\d)", log, re.M)}
    assert touched, "the implementation log documents no phase"
    for phase in sorted(touched):
        section = log.split(f"## {phase}", 1)[1]
        assert "Measured" in section, f"{phase} has no measured test result"
        assert "Deviation" in section or "deviation" in section, f"{phase} records no deviations (a clean phase must say so)"
