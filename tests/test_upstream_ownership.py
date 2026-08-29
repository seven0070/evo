"""The ownership boundary and the upstream pins, as data that can be broken (07 §4, §8 P5).

P5's remaining failure mode was not "a feature is missing" but "a feature has two answers": two agents
that loop, two stores, two verifiers, two sandboxes. Those are exactly the mistakes ``00`` recorded and
``06`` rejected, and they are invisible to a test of any single component - you have to ask the whole tree
who owns what. So the answer lives in a table, and the table is checked the way the protected byte set
is: by refusing to start, not by a code review that someone skips.

The vendor checks here run against *this* repository and against a synthetic tree. The first direction is
the assertion that matters today ("we did not copy DeerFlow"); the second is the assertion that will
matter in a year ("if someone does, this fails"), which is why the negative cases build their own tree
instead of trusting the real one to stay clean.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]

from evo_agent.sovereign.invariants import REGISTRY, run_invariants  # noqa: E402
from evo_agent.upstream import (  # noqa: E402
    NEVER_CANDIDATE,
    OWNERSHIP,
    UPSTREAM,
    CapabilityOwner,
    UpstreamComponent,
    authority_exists,
    boundary_problems,
    protected_dependents,
    protected_module_names,
    report,
    upstream_problems,
)


# -- the tables as they stand -----------------------------------------------


def test_the_tables_are_clean_in_this_checkout() -> None:
    assert boundary_problems() == []
    assert upstream_problems(ROOT) == []
    payload = report(ROOT)
    assert payload["ok"], payload["problems"]
    assert len(payload["ownership"]) >= 20
    assert {item["name"] for item in payload["components"]} == {"deer-flow", "deepseek-harness"}


def test_one_capability_one_owner() -> None:
    names = [row.capability for row in OWNERSHIP]
    assert len(names) == len(set(names)), [name for name in names if names.count(name) > 1]
    # and every row is a full statement, not a name with a trailing "TODO"
    for row in OWNERSHIP:
        assert row.reason.strip(), f"{row.capability} has no reason"
        assert row.owner in {"sovereign", "operator", "agent"}


def test_every_authority_resolves_by_import() -> None:
    for row in OWNERSHIP:
        ok, why = authority_exists(row.authority)
        assert ok, f"{row.capability}: {why}"
    for component in UPSTREAM:
        for spec in component.accepted_by:
            ok, why = authority_exists(spec)
            assert ok, f"{component.name}: {why}"


def test_a_pinned_authority_is_enforced_by_protected_code() -> None:
    """The strongest claim in the table, and the one that cannot be satisfied by a comment.

    Every ``sovereign`` row must point at a module that is either in the protected byte set or imported
    by something in it. The second half is not a loophole: the clamps that make ``pipeline/engine.py``
    sovereign-owned live in ``materialization.py`` and ``security.py``, both protected, and demanding
    that every authority be read-only would mean protecting half the package - which is a governance
    change, not a check this table gets to make.
    """
    protected = protected_module_names()
    dependents = protected_dependents()
    assert "evo_agent.runtime" in protected
    assert "evo_agent.materialization" in protected
    assert "evo_agent.pipeline" in dependents
    for row in OWNERSHIP:
        if row.owner != "sovereign":
            continue
        name = row.authority_module
        assert name in protected or any(
            other == name or other.startswith(name + ".") or name.startswith(other + ".") for other in dependents
        ), f"{row.capability}: {name} is claimed sovereign but nothing protected reaches it"


def test_the_non_negotiable_capabilities_are_never_candidate_writable() -> None:
    rows = {row.capability: row for row in OWNERSHIP}
    for capability in NEVER_CANDIDATE:
        assert capability in rows, f"{capability} is listed but unowned"
        assert rows[capability].owner == "sovereign", capability
        assert not rows[capability].candidate_may_change, capability
    # The P5 additions have to be in the list, or the phase has quietly widened what a candidate may touch.
    for capability in ("secrets", "tool-identity", "emergency-shutdown", "rollback", "promotion"):
        assert capability in NEVER_CANDIDATE


def test_the_p5_capabilities_are_present_and_describe_the_real_boundary() -> None:
    rows = {row.capability: row for row in OWNERSHIP}
    assert rows["memory-policy"].candidate_may_change is True  # weights
    assert rows["memory-contents"].candidate_may_change is False  # never a payload
    assert rows["memory-contents"].owner == "agent"
    assert rows["skill-bundles"].candidate_may_change is True
    assert rows["secrets"].candidate_may_change is False
    assert rows["secrets"].authority_attribute == "SkillCatalog"
    assert rows["provider-config"].candidate_may_change is False, "prompt text is not an evolution payload"
    assert rows["backend-routing"].owner == "operator", "the loop is a launch decision, not a promotion"


def test_upstream_components_are_pinned_not_copied() -> None:
    for component in UPSTREAM:
        assert component.pinned_ref and component.licence
        assert component.accepted_by, f"{component.name} records no accepted surface"
        assert component.integration in {"bridge", "adapter"}
        assert not component.vendored
        for relative in component.must_not_exist:
            assert not (ROOT / relative).exists(), f"{relative} exists: the component was vendored"
        assert component.blocked_by, f"{component.name} claims a boundary without recording what blocks deeper work"


def test_no_upstream_project_landed_anywhere_in_the_package() -> None:
    """A vendored tree shows up as a foreign build file before it shows up as a second authority."""
    offenders: list[str] = []
    for path in (ROOT / "evo_agent").rglob("*"):
        if path.is_file() and path.name in {"pyproject.toml", "package.json", "uv.lock", "pnpm-lock.yaml"}:
            offenders.append(str(path.relative_to(ROOT)))
    assert offenders == []
    assert not (ROOT / "vendor").exists()
    assert not (ROOT / "third_party").exists()
    assert not (ROOT / "node_modules").exists()


# -- what breaks them -------------------------------------------------------


def _row(**overrides: Any) -> CapabilityOwner:
    base = {
        "capability": "example",
        "owner": "sovereign",
        "authority": "evo_agent.security:SecurityPolicy",
        "candidate_may_change": False,
        "reason": "a synthetic row used only to prove the checker notices",
    }
    base.update(overrides)
    return CapabilityOwner(**base)


def test_a_row_that_relaxes_an_absolute_is_refused() -> None:
    rows = tuple(row for row in OWNERSHIP if row.capability != "verification") + (
        _row(capability="verification", candidate_may_change=True),
    )
    problems = boundary_problems(rows)
    assert any("non-negotiable" in item for item in problems), problems


def test_a_row_that_claims_an_authority_nothing_enforces_is_refused() -> None:
    # ``evo_agent.version`` is a leaf that imports nothing and is imported by protected code only for its
    # string; using it as an authority for a new capability means the capability is decided nowhere.
    rows = tuple(OWNERSHIP) + (_row(capability="ghostly", authority="evo_agent.skills_not_real:Nope"),)
    problems = boundary_problems(rows)
    assert any("could not be imported" in item for item in problems), problems


def test_a_capability_with_two_owners_is_refused() -> None:
    rows = tuple(OWNERSHIP) + (_row(capability="promotion"),)
    problems = boundary_problems(rows)
    assert any("claim one owner" in item for item in problems), problems


def test_an_unowned_absolute_is_refused() -> None:
    rows = tuple(row for row in OWNERSHIP if row.capability != "rollback")
    problems = boundary_problems(rows)
    assert any("non-negotiable but have no row" in item for item in problems), problems


def test_an_undotted_authority_is_refused() -> None:
    problems = boundary_problems((_row(capability="prose", authority="the security policy"),))
    assert any("not a 'module:attribute' reference" in item for item in problems), problems


def test_a_vendored_tree_is_named_as_such(tmp_path: Path) -> None:
    (tmp_path / "vendor" / "deer-flow").mkdir(parents=True)
    problems = upstream_problems(tmp_path, (UPSTREAM[0],))
    assert any("copied rather than adapted" in item for item in problems), problems


def test_a_component_that_records_nothing_is_a_component_nobody_reviewed() -> None:
    bare = UpstreamComponent(
        name="someone-elses-repo",
        repository="acme/whatever",
        pinned_ref="v1",
        ref_kind="tag",
        licence="MIT",
        integration="adapter",
    )
    problems = upstream_problems(Path("."), (bare,))
    assert any("no accepted_by" in item for item in problems), problems


def test_a_branch_pin_is_advisory_until_it_is_demanded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    harness = next(item for item in UPSTREAM if item.ref_kind == "branch")
    monkeypatch.delenv("EVO_REQUIRE_TAG_PINS", raising=False)
    assert upstream_problems(tmp_path, (harness,)) == []
    monkeypatch.setenv("EVO_REQUIRE_TAG_PINS", "1")
    problems = upstream_problems(tmp_path, (harness,))
    assert problems and "pinned to a branch" in problems[0]


def test_the_vendored_flag_is_checked_rather_than_inferred() -> None:
    """``vendored`` cannot be reached by accident: the claim has to be typed, and is then refused."""
    flag = UpstreamComponent(
        name="copied",
        repository="acme/copied",
        pinned_ref="v1",
        ref_kind="tag",
        licence="MIT",
        integration="adapter",
        accepted_by=("evo_agent.security:SecurityPolicy",),
        vendored=True,
    )
    assert any("marked vendored" in item for item in upstream_problems(Path("."), (flag,)))


# -- it has to be enforced, not just true ----------------------------------


def test_the_boundary_is_a_startup_invariant() -> None:
    codes = [item.code for item in REGISTRY]
    assert "I-ownership-boundary" in codes
    result = next(item for item in run_invariants() if item.code == "I-ownership-boundary")
    assert result.ok, result.detail
    assert "23 capabilities" in result.detail or "capabilities owned" in result.detail


def test_the_invariant_notices_a_broken_table(monkeypatch: pytest.MonkeyPatch) -> None:
    from evo_agent import upstream

    def broken() -> list[str]:
        return ["verification: synthetic breakage"]

    monkeypatch.setattr(upstream, "boundary_problems", broken)
    result = next(item for item in run_invariants() if item.code == "I-ownership-boundary")
    assert not result.ok
    assert "synthetic breakage" in str(result.evidence)


def test_the_table_is_not_writable_at_runtime() -> None:
    """Frozen dataclasses, and tuples rather than lists.

    A candidate cannot edit these tables - they are not overlay-writable documents - but a *consumer* in
    this process could, and an ownership table that a running agent can mutate is a table that will be
    mutated by whichever component most wants a different answer.
    """
    for row in OWNERSHIP:
        with pytest.raises(Exception):
            row.owner = "agent"  # type: ignore[misc]
    for component in UPSTREAM:
        with pytest.raises(Exception):
            component.accepted_by = ()  # type: ignore[misc]
    assert isinstance(OWNERSHIP, tuple) and isinstance(UPSTREAM, tuple)
