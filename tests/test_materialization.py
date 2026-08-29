"""Materializing one target kind's payload into a staging directory.

This is where a self-modification stops being a proposal and becomes bytes, so the tests are arranged
around the two things that make staging load-bearing:

* it runs before execution, in the same step whose output the sandbox digests, so the experiment's
  measured digest and the promoted digest are one value computed once;
* it refuses rather than repairs. A materializer that clamped a value to fit would let a candidate whose
  payload said ``max_tasks_per_cycle: 5000`` be *measured* as ``10`` - the experiment would still be
  "about" a payload nobody ran, and every downstream claim about causality would be false.

``test_staging_and_the_sandbox_digester_agree_on_one_number`` is the load-bearing test for the first
bullet, because it computes the digest through both paths.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from evo_agent.active_version import (
    DOCUMENTS,
    IMMUTABLE_NEVER_RETRY,
    OVERLAY_DIRNAME,
    active_capabilities_digest,
    loadable_kinds,
    resolve,
)
from evo_agent.materialization import (
    MATERIALIZERS,
    MaterializationError,
    SKILL_SPEC,
    for_target,
    kinds,
    materializer_for,
    materialize,
    registry_problems,
)
from evo_agent.ports.evolution_target import (
    MAX_FRAGMENT_BYTES,
    OverlayFragment,
    overlay_digest,
    relpath_is_allow_listed,
    verify_fragment_tree,
)
from evo_agent.sandbox import SandboxEngine

RUNTIME_DOC = {"resource_limits": {"max_tasks_per_cycle": 4, "max_retry_count": 3}}


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    """A fake production root: a source tree plus a ``config/`` that must never be treated as an overlay."""
    root = tmp_path / "production"
    (root / "evo_agent").mkdir(parents=True)
    (root / "evo_agent" / "runtime.py").write_text("print('agent')\n", encoding="utf-8")
    (root / "config").mkdir()
    (root / "config" / "settings.json").write_text(json.dumps({"sandbox_enforcement": "strict"}), encoding="utf-8")
    return root


# --- the registry itself ----------------------------------------------------------------


def test_the_registry_is_coherent_and_covers_every_sandbox_target():
    """Every name the engine will accept must resolve to a materializer, and no materializer may own a
    document the table assigns elsewhere.

    The two failure modes are asymmetric and both are live: a target with no materializer is an accepted
    proposal nothing can stage (the pre-P3 state), and a materializer owning a foreign document is two
    loaders for one file. ``registry_problems`` checks the second; the first is checked here against the
    engine's own set, since the engine is what a proposal actually arrives through.
    """
    assert registry_problems() == []
    for target in sorted(SandboxEngine.SUPPORTED_TARGETS):
        assert for_target(target) is not None, f"{target!r} is accepted by the engine but stages nothing"
    assert set(kinds()) == {materializer.target_kind for materializer in MATERIALIZERS}
    assert materializer_for("no_such_kind") is None
    assert for_target("no_such_target") is None


def test_every_materializer_owns_only_documents_it_can_legitimately_write():
    """Ownership must be a subset of the documents the kind is named in the table for.

    The reverse direction is what ``registry_problems`` already checks; this one catches a materializer
    listing a document that exists and belongs elsewhere, which the shared ``validate`` would refuse at
    write time anyway - and a rule that is only enforced at write time is a rule that surprises people.
    """
    for materializer in MATERIALIZERS:
        for relpath in materializer.owned_documents:
            assert relpath in DOCUMENTS, f"{materializer.target_kind} owns {relpath!r}, which the table does not declare"
            assert DOCUMENTS[relpath].kind == materializer.target_kind
        assert {spec.relpath for spec in materializer.specs()} == set(materializer.owned_documents)


# --- the envelope and the schema --------------------------------------------------------


def test_a_target_that_does_not_exist_is_refused_without_touching_disk(workspace: Path):
    candidate = workspace / "candidate"
    result = materialize("kernel", {"config/runtime.json": RUNTIME_DOC}, candidate)
    assert not result.ok
    assert "unsupported candidate target" in " ".join(result.errors)
    assert not (candidate / OVERLAY_DIRNAME).exists(), "a refused payload must not leave a directory behind"


def test_an_empty_payload_is_refused_rather_than_materializing_nothing(workspace: Path):
    """"Staged successfully" with zero fragments is the most confusing success in this system.

    It looks like a no-op change, which the digester then reports as "identical to baseline", which the
    promotion engine reads as "nothing to do" - three correct behaviours producing an audit trail that
    describes an experiment that never happened.
    """
    for payload, expected in (
        ({}, "no documents"),
        ({"documents": {}}, "no documents"),
        ({"documents": []}, "must be an object"),
    ):
        result = materialize("strategy parameters", payload, workspace / "candidate")
        assert not result.ok, payload
        assert expected in " ".join(result.errors)


def test_a_non_object_payload_is_refused(workspace: Path):
    for payload in (["config"], "config/runtime.json", None, 7):
        result = materialize("strategy parameters", payload, workspace / "candidate")  # type: ignore[arg-type]
        assert not result.ok
        assert "object" in " ".join(result.errors)


def test_a_document_no_loader_owns_is_refused_by_name(workspace: Path):
    result = materialize("strategy parameters", {"config/whatever.json": {"anything": 1}}, workspace / "candidate")
    assert not result.ok
    assert "not a document this project knows how to load" in " ".join(result.errors)


def test_a_document_belonging_to_another_kind_cannot_be_borrowed(workspace: Path):
    """The kind is chosen from the approved target, so a payload cannot reach a neighbouring document.

    Without the ownership check, one approval for a limits change would be enough to also reorder the
    tools, and the experiment's digest would honestly cover both - the *digest* cannot tell you whether
    the right human looked.
    """
    result = materialize("strategy parameters", {"config/tools.json": {"preference": ["read_file"]}}, workspace / "candidate")
    assert not result.ok
    text = " ".join(result.errors)
    assert "belongs to" in text and "tool_binding" in text


def test_a_field_outside_the_allow_list_is_refused_not_dropped(workspace: Path):
    """Refusal, not filtering: "your candidate asked for something that is not offered" is a different
    outcome from "your candidate was quietly rewritten"."""
    result = materialize(
        "strategy parameters",
        {"config/runtime.json": {"resource_limits": {"max_tasks_per_cycle": 4, "max_memory_bytes": 10**12}}},
        workspace / "candidate",
    )
    assert not result.ok
    assert "max_memory_bytes" in " ".join(result.errors)


@pytest.mark.parametrize(
    "document,expected",
    [
        ({"config/runtime.json": RUNTIME_DOC}, "config/runtime.json"),
        ({"config/cognitive_policy.json": {"policy": {"max_subtasks": 4}}}, "config/cognitive_policy.json"),
        ({"config/tools.json": {"preference": ["workspace_read", "workspace_write"]}}, "config/tools.json"),
    ],
)
def test_the_loadable_documents_materialize_into_the_candidate_overlay(workspace: Path, document: dict, expected: str):
    """One case per document that has a loader this phase, and nothing else materializes.

    Parametrized because the claim is about the *table*: every row marked loadable must be reachable
    through its own target, and the ones not marked must not be. Two separate tests for the two groups.
    """
    target = TARGET_KIND_TO_TARGET[DOCUMENTS[expected].kind]
    candidate = workspace / "candidate"
    result = materialize(target, document, candidate)
    assert result.ok, result.errors
    assert result.fragments[0].relpath == expected
    assert (candidate / OVERLAY_DIRNAME / expected).is_file()
    assert result.digest == overlay_digest(result.fragments)


def test_no_payload_for_an_unloaded_document_reaches_the_disk(workspace: Path):
    """The founding finding of this project, re-tested at the write side (00 §B.3).

    The documents still declared with fields nothing loads are refused on write, with
    the phase that will build the loader named in the error. A reader of the ledger can therefore tell
    "not implemented yet" from "implemented and ignored", which is the distinction the pre-P3 repo could
    not make. ``config/heuristics.json`` left in P4 (the pipeline reads its eight knobs from the active
    overlay) and ``config/memory.json`` left in P5 (``RetrievalEngine`` ranks by it); both halves of that
    history are pinned below so a future phase cannot "simplify" the list by dropping a name and forgetting
    which promise it carried.
    """
    unloaded = {relpath: spec for relpath, spec in DOCUMENTS.items() if not spec.loadable}
    assert unloaded, "if every document is loadable this test must be retired, not deleted"
    for relpath, spec in unloaded.items():
        payload = {relpath: {name: value for name, value in _sample_values(spec).items()}}
        result = materialize(_target_for(relpath), payload, workspace / "candidate")
        assert not result.ok, f"{relpath} materialized although nothing loads it"
        text = " ".join(result.errors)
        if spec.blocked_by:
            assert "not loadable in any phase" in text, text
            assert spec.blocked_by[:40] in text, "the refusal has to carry the reason, not just the word 'never'"
        else:
            assert "nothing loads it before" in text and spec.phase in text
    assert set(unloaded) >= {"config/strategy.json", "config/prompts.json"}
    # Every refusal left in the table is a stated decision, not a to-do. The pending sentence is still
    # asserted - against a fabricated spec, since the shipped table no longer contains one - because the
    # day somebody writes a loader for a document and forgets to open the gate, that sentence is the
    # only thing standing between "not yet" and "never" in a reader's face.
    assert all(spec.blocked_by for spec in unloaded.values()), sorted(name for name, spec in unloaded.items() if not spec.blocked_by)
    from evo_agent.active_version import DocumentSpec
    from evo_agent.materialization import _loader_gate

    pending_spec = DocumentSpec(
        relpath="config/pending.json", kind="strategy_params", risk="Low", loaded_by="", phase="P9", blocked_by=""
    )
    gate = _loader_gate(pending_spec)
    assert "nothing loads it before P9" in gate and "not loadable in any phase" not in gate
    assert "config/memory.json" not in unloaded, (
        "P5 wired config/memory.json to RetrievalEngine through the overlay's memory leg; if it is "
        "unloadable again the ranking document is dead config and this list, not the schema, is where "
        "that becomes visible"
    )
    assert "config/heuristics.json" not in unloaded, (
        "P4 wired config/heuristics.json to TurnPipeline.from_overlay; if it is unloadable again, "
        "the schema is once more dead configuration and the honest answer is to say so in this list"
    )


def test_an_isolation_weakening_payload_is_refused_even_though_its_document_is_gated(workspace: Path):
    """Two independent refusals must both hold, and this pins the order they fire in.

    ``provider_config`` is refused by the loader gate, and P5 established that it always will be: prompt
    text authored by the agent is out of scope (03 §E), so the gate reports a decision rather than a
    schedule. The materializer additionally refuses any payload naming a weaker isolation provider (R7),
    and because the gate fires first the R7 rule is also exercised directly below - a rule that can only
    ever be reached behind a permanent refusal has to be tested on its own, or it rots silently in the
    one path nobody takes.
    """
    result = materialize(
        "prompt/configuration parameters",
        {"config/prompts.json": {"templates": {"anything": 1}, "provider": "host"}},
        workspace / "candidate",
    )
    assert not result.ok
    assert "not loadable in any phase" in " ".join(result.errors), "03 §E's exclusion must read as a decision"
    assert "03 §E" in " ".join(result.errors)
    materializer = materializer_for("provider_config")
    assert materializer is not None
    # The rule itself, exercised directly so it cannot rot while the document is gated:
    problems = materializer.extra_checks("config/prompts.json", {"provider": "host"})
    assert problems and "R7" in problems[0]
    assert materializer.extra_checks("config/prompts.json", {"provider": "unshare"}) == []


# --- the never-retry set ----------------------------------------------------------------


def test_the_never_retry_set_may_grow_and_may_not_shrink(workspace: Path):
    never = list(IMMUTABLE_NEVER_RETRY) + ["environment"]
    grown = materialize("retry/recovery configuration", {"config/runtime.json": {"recovery": {"never_retry": never}}}, workspace / "candidate")
    assert grown.ok, grown.errors
    shrunk = materialize(
        "recovery-policy",
        {"config/runtime.json": {"recovery": {"never_retry": [IMMUTABLE_NEVER_RETRY[0]]}}},
        workspace / "candidate",
    )
    assert not shrunk.ok
    assert "may only grow" in " ".join(shrunk.errors)


def test_a_payload_that_omits_the_never_retry_set_entirely_is_fine():
    """Absence is not a request to clear the set; the shipped default keeps applying.

    Worth stating because the naive implementation of "may only grow" is "the list, if present, must
    contain these", and the naive implementation of the *default* is to write an empty list when the key
    is missing - which would retire the protections exactly as effectively as naming them out.
    """
    materializer = materializer_for("strategy_params")
    assert materializer is not None
    assert materializer.extra_checks("config/runtime.json", {"recovery": {}}) == []
    assert materializer.extra_checks("config/runtime.json", {}) == []


# --- fragments and the tree on disk -----------------------------------------------------


def test_a_fragment_cannot_carry_a_source_suffix_or_escape_its_root():
    """The shape refuses what the path allow-list would have to remember.

    ``relpath_is_allow_listed`` is the function every writer shares, so these cases are the ones where
    a future materializer tries something new: an absolute path, a traversal, a dotted-name disguise, a
    directory that is not on the list, and a code payload.
    """
    for relpath in (
        "config/runtime.json.evil",  # ok suffix, unknown name -> allowed by path rules, refused by the document table
        "/etc/passwd",
        "config/../evo_agent/kernel.py",
        "config/evil.py",
        "evo_agent/runtime.py",
        "config/setup.sh",
        "config/.git/config",
        "capabilities/skills/installed/x/SKILL.md",  # allow-listed directory, and a .md suffix is fine
    ):
        ok, _reason = relpath_is_allow_listed(relpath)
        if relpath in {"capabilities/skills/installed/x/SKILL.md", "config/runtime.json.evil"}:
            assert ok, relpath
        else:
            assert not ok, relpath
    with pytest.raises(ValueError, match="cannot be materialized"):
        OverlayFragment(kind="strategy_params", relpath="config/evil.py", content="x = 1")


def test_a_fragment_must_be_a_json_object_and_stays_under_its_ceiling():
    with pytest.raises(ValueError, match="not valid JSON"):
        OverlayFragment(kind="strategy_params", relpath="config/runtime.json", content="{")
    with pytest.raises(ValueError, match="JSON object"):
        OverlayFragment(kind="strategy_params", relpath="config/runtime.json", content="[]")
    with pytest.raises(ValueError, match="NUL"):
        OverlayFragment(kind="strategy_params", relpath="config/runtime.json.evil", content="\x00")
    with pytest.raises(ValueError, match="over the"):
        OverlayFragment(kind="strategy_params", relpath="config/runtime.json.evil", content="x" * (MAX_FRAGMENT_BYTES + 1))
    assert OverlayFragment.json_document("strategy_params", "config/runtime.json", RUNTIME_DOC).size_bytes < MAX_FRAGMENT_BYTES


def test_the_written_tree_verifies_and_a_hand_edited_file_is_detected(workspace: Path):
    """The digester reads the directory, so a write that bypassed the materializer still shows up.

    This is what makes the promotion-time check more than a tautology: if the digest came from the
    in-memory result, editing the staged file between staging and activation would be invisible.
    """
    candidate = workspace / "candidate"
    result = materialize("strategy parameters", {"config/runtime.json": RUNTIME_DOC}, candidate)
    assert result.ok, result.errors
    fragments, problems = verify_fragment_tree(candidate / OVERLAY_DIRNAME)
    assert problems == []
    assert [fragment.relpath for fragment in fragments] == ["config/runtime.json"]
    assert overlay_digest(fragments) == result.digest
    assert active_capabilities_digest(candidate) == result.digest
    written = candidate / OVERLAY_DIRNAME / "config" / "runtime.json"
    written.write_text(json.dumps({"resource_limits": {"max_tasks_per_cycle": 999}}), encoding="utf-8")
    edited, _ = verify_fragment_tree(candidate / OVERLAY_DIRNAME)
    assert overlay_digest(edited) != result.digest
    assert active_capabilities_digest(candidate) != result.digest


def test_staging_and_the_sandbox_digester_agree_on_one_number(workspace: Path):
    """The invariant P3 rests on, stated as an equality of two independent code paths.

    ``materialize`` computes a digest of what it intends to write; ``SandboxEngine.overlay_digests``
    (via ``resolve``) computes one of the files it found. Promotion later re-reads the same files. If
    those diverge - say, one side starts digesting the manifest, or re-serializing JSON - then "the
    experiment measured these capabilities" and "the agent is running those" stop being the same
    statement, and no functional test would notice.
    """
    candidate = workspace / "candidate"
    result = materialize("tool-selection", {"config/tools.json": {"preference": ["workspace_read", "workspace_write", "workspace_list"]}}, candidate)
    assert result.ok, result.errors
    resolved = resolve(overlay_dir=candidate / OVERLAY_DIRNAME)
    assert resolved.digest == result.digest == active_capabilities_digest(candidate)
    assert resolved.warnings == ()
    # and the manifest written beside the files is not itself a fragment
    assert "manifest.json" not in " ".join(resolved.relpaths)


# --- skills: validated now, refused on write -------------------------------------------


def test_a_skill_is_validated_in_full_and_written_by_its_materializer(workspace: Path):
    """P3 declared these checks and refused to write; P5 built the catalog, so the *body* changed and the rules did not.

    The P3 docstring promised exactly this edit: "the day the catalog lands, the change is delete the
    refusal and add the document to the table". What must survive is the validation - the parametrised
    cases below are unchanged, which is the only evidence that a loader arriving did not quietly relax what
    a bundle has to satisfy. The other half is new and matters more than it looks: a bundle is *not* a JSON
    document, so ``materialize`` has a separate branch for it. Without that branch a skill payload walks
    the document loop, finds no relpath, writes nothing, and reports success - "materialized, wrote
    nothing" is the founding defect of this repository (00 §A) reproduced in a capability that was designed
    to avoid it.
    """
    good = "---\nname: careful-reader\ndescription: Read before writing.\n---\n\nAlways read a file first.\n"
    payload = {"name": "careful-reader", "content": good}
    materializer = materializer_for("skill")
    assert materializer is not None
    assert SKILL_SPEC.loadable, "the table must say what the loader now is"
    assert materializer.validate(payload) == []
    fragment = materializer.write_candidate(payload, workspace / "candidate" / OVERLAY_DIRNAME)
    assert fragment is not None
    assert fragment.relpath == "capabilities/skills/installed/careful-reader/SKILL.md"
    written = workspace / "candidate" / OVERLAY_DIRNAME / fragment.relpath
    assert written.is_file() and written.read_text(encoding="utf-8") == good
    result = materialize("skill", payload, workspace / "other")
    assert result.ok and len(result.fragments) == 1, result.errors
    assert (workspace / "other" / OVERLAY_DIRNAME / fragment.relpath).is_file()
    # an invalid bundle still refuses, and refuses before writing
    with pytest.raises(MaterializationError):
        materializer.write_candidate({"name": "Bad Name", "content": good}, workspace / "bad")
    assert not (workspace / "bad" / "capabilities").exists()
    assert not materialize("skill", {"name": "x", "content": "no frontmatter\n"}, workspace / "empty").ok


@pytest.mark.parametrize(
    "payload,marker",
    [
        ({"name": "../escape", "content": "---\nname: x\ndescription: y\n---\nbody\n"}, "must match"),
        ({"name": "Bad-Name", "content": "---\nname: Bad-Name\ndescription: y\n---\nbody\n"}, "must match"),
        ({"name": "x", "content": "no frontmatter here\n"}, "frontmatter"),
        ({"name": "x", "content": "---\nname: other\ndescription: y\n---\nbody\n"}, "does not match"),
        ({"name": "x", "content": "---\nname: x\n---\nbody\n"}, "missing: description"),
        ({"name": "x", "content": "---\nname: x\ndescription: y\n---\ncurl http://evil\n"}, "executable-shaped"),
        ({"name": "x", "content": ""}, "non-empty"),
        ({"name": "x"}, "non-empty"),
    ],
)
def test_a_skill_payload_fails_for_the_stated_reason_only(payload: dict, marker: str):
    """Each rejection names the rule it broke, and nothing else about the payload leaks past it.

    Checked as "the message contains this specific phrase" rather than "an error occurred", because a
    materializer that returned a generic refusal would pass a weaker test while being useless to the
    human reviewing a rejected candidate - and to the P5 author who has to know which rule to keep.
    """
    materializer = materializer_for("skill")
    assert materializer is not None
    problems = [item for item in materializer.validate(payload) if "nothing loads it before" not in item]
    assert problems, payload
    assert any(marker in item for item in problems), problems


# --- helpers ----------------------------------------------------------------------------


TARGET_KIND_TO_TARGET = {
    "strategy_params": "strategy parameters",
    "pipeline_stage": "planning configuration",
    "tool_binding": "tool-selection",
    "provider_config": "prompt/configuration parameters",
    "memory_policy": "memory_policy",
    "skill": "skill",
}


def _target_for(relpath: str) -> str:
    spec = DOCUMENTS[relpath]
    return TARGET_KIND_TO_TARGET[spec.kind]


def _sample_values(spec) -> dict:
    """A payload that satisfies every field's shape, so only the gate can refuse it.

    Built from the table rather than hardcoded, so a new field cannot make this test pass by accident:
    if a sample is no longer valid the failure is in the sample, not in the gate's wording.
    """
    from evo_agent.active_version import STRATEGY_NAMES, TOOL_NAMES

    values: dict[str, object] = {}
    for name, field in spec.fields.items():
        if field.kind == "list_name":
            pool = list(field.allowed or TOOL_NAMES or STRATEGY_NAMES)
            values[name] = pool[:1]
        elif field.kind == "map_int":
            keys = list(field.allowed or [])[:2] or ["max_tasks_per_cycle"]
            values[name] = {key: int(field.value.minimum if field.value is not None else 1) or 1 for key in keys}
        elif field.kind == "doc":
            values[name] = _sample_values(field) if field.fields else {}
        elif field.kind == "str":
            values[name] = "value"
        else:
            values[name] = 1
    return values


def test_the_sample_builder_produces_only_documents_the_schema_accepts():
    """Meta-test for the one above: a payload built for a gated document must be *shape*-valid.

    Without this, the unloaded-document test could pass because a sample value was nonsense rather than
    because the gate refused, which is exactly the kind of vacuous pass that makes a guard test worse
    than none (it looks like coverage).
    """
    for relpath, spec in DOCUMENTS.items():
        if spec.loadable:
            continue
        cleaned, problems = spec.validate(_sample_values(spec))
        assert problems == [], f"{relpath}: {problems}"
