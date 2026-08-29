"""Resolving what the agent would actually load, from the version that is active.

``active_version.py`` is the read side of the seam that makes promotion causal, so these tests are
mostly about the two ways a read side goes wrong: it silently accepts things it should not, or it
silently *drops* things and reports success. Both are covered here - refusals by the document schema,
and warnings for a file that exists but will not be loaded.

The digest rules get their own tests because every downstream claim ("the experiment measured these
capabilities", "the agent is now running those") reduces to two digests being equal. A digest that is
order-sensitive, or that covers a re-rendered view instead of the bytes on disk, would make those
claims unfalsifiable in a way no single test would notice.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from evo_agent import active_version
from evo_agent.active_version import (
    ACTIVATION_RECORD,
    DOCUMENTS,
    TARGET_TO_KIND,
    ActiveOverlay,
    Field,
    apply_overlays,
    default_versions_root,
    resolve,
    verify_activation,
    write_activation_record,
)
from evo_agent.ports.evolution_target import OVERLAY_DIRNAME, overlay_digest
from evo_agent.runtime import RuntimeResourceLimits


def build_version(versions_root: Path, name: str, documents: dict[str, object]) -> Path:
    """Create ``versions_root/versions/<name>/overlay/<relpath>`` with the given JSON documents."""
    directory = versions_root / "versions" / name / OVERLAY_DIRNAME
    for relpath, payload in documents.items():
        target = directory / relpath
        target.parent.mkdir(parents=True, exist_ok=True)
        text = payload if isinstance(payload, str) else json.dumps(payload, indent=2, sort_keys=True) + "\n"
        target.write_text(text, encoding="utf-8")
    return directory


def activate(versions_root: Path, name: str) -> Path:
    """Point ``active`` at a version the way ``_atomic_switch`` does: a relative symlink."""
    link = versions_root / "active"
    if link.is_symlink() or link.exists():
        link.unlink()
    target = versions_root / "versions" / name
    os.symlink(os.path.relpath(target, versions_root), link)
    return link


RUNTIME_DOC = {"resource_limits": {"max_tasks_per_cycle": 3, "max_retry_count": 4}}


# --- resolution ------------------------------------------------------------------------


def test_a_fresh_install_resolves_to_repo_defaults_without_complaint(tmp_path: Path):
    """No ``versions/`` at all is the normal state of a new agent, not an error.

    If the resolver required a version directory, the evolution spine would become a boot
    dependency - and the tempting fix for that would be to make the runtime ignore the overlay
    entirely, which is where the pre-P3 world was.
    """
    overlay = resolve(tmp_path / "nothing-here")
    assert overlay.source == "repo-default"
    assert overlay.version_id is None
    assert overlay.documents == {}
    assert overlay.digest == overlay_digest(())
    assert overlay.warnings == ()


def test_the_active_link_selects_the_version_and_its_documents(tmp_path: Path):
    versions = tmp_path / "production"
    build_version(versions, "v1", {"config/runtime.json": RUNTIME_DOC})
    activate(versions, "v1")
    overlay = resolve(versions)
    assert overlay.source == "active"
    assert overlay.version_id == "v1"
    assert overlay.relpaths == ("config/runtime.json",)
    assert overlay.resource_limit_overrides() == {"max_tasks_per_cycle": 3, "max_retry_count": 4}


def test_a_dangling_link_is_repo_default_not_a_crash(tmp_path: Path):
    versions = tmp_path / "production"
    build_version(versions, "v1", {"config/runtime.json": RUNTIME_DOC})
    activate(versions, "v1")
    (versions / "versions" / "v1").rename(versions / "versions" / "v1-moved")
    overlay = resolve(versions)
    assert overlay.source == "repo-default"
    assert overlay.resource_limit_overrides() == {}


def test_only_allow_listed_subpaths_are_read_and_the_rest_are_reported(tmp_path: Path):
    """The S11 rule: a shadowed default must never be silent.

    The file is not loaded *and* the reason is in the overlay - the pair is what makes "we ignored it"
    auditable. A resolver that ignored quietly would be indistinguishable from one that had not seen
    the file at all.
    """
    versions = tmp_path / "production"
    build_version(versions, "v1", {"config/runtime.json": RUNTIME_DOC})
    directory = versions / "versions" / "v1" / OVERLAY_DIRNAME
    (directory / "secrets").mkdir()
    (directory / "secrets" / "note.txt").write_text("shadow the defaults\n", encoding="utf-8")
    (directory / "config" / "sneaky.py").write_text("print('source as config')\n", encoding="utf-8")
    activate(versions, "v1")
    overlay = resolve(versions)
    assert overlay.relpaths == ("config/runtime.json",)
    joined = " ".join(overlay.warnings)
    assert "secrets/note.txt" in joined and "allow-listed subpath" in joined
    assert "sneaky.py" in joined and "cannot be materialized" in joined
    assert overlay.documents.get("config/sneaky.py") is None


def test_a_document_outside_the_table_is_warned_about_not_loaded(tmp_path: Path):
    """A file in an allow-listed *directory* that no spec covers still cannot be loaded."""
    versions = tmp_path / "production"
    build_version(versions, "v1", {"config/whatever.json": {"anything": 1}})
    activate(versions, "v1")
    overlay = resolve(versions)
    assert overlay.documents == {}
    assert any("no document spec" in warning for warning in overlay.warnings)


def test_a_source_tree_is_never_interpreted_as_an_overlay(tmp_path: Path):
    """The resolver's sharpest edge: pointing it at a checkout must not read the checkout.

    ``config/*.json`` exists in ordinary projects; treating a candidate directory's own files as
    materialized state would hand an experiment a digest that looked verified.
    """
    fake_checkout = tmp_path / "candidate"
    (fake_checkout / "config").mkdir(parents=True)
    (fake_checkout / "config" / "settings.json").write_text(json.dumps({"resource_limits": {"max_tasks_per_cycle": 99}}), encoding="utf-8")
    overlay = resolve(overlay_dir=fake_checkout)
    assert overlay.documents == {}
    assert overlay.digest == overlay_digest(())


# --- the document table ----------------------------------------------------------------


def test_unknown_and_out_of_range_fields_are_dropped_with_a_reason(tmp_path: Path):
    versions = tmp_path / "production"
    build_version(
        versions,
        "v1",
        {
            "config/runtime.json": {
                "resource_limits": {
                    "max_tasks_per_cycle": 3,
                    "max_memory_bytes": 1,  # not allow-listed: it bounds the host, not the cycle
                    "max_retry_count": 10**9,  # over the ceiling
                    "sandbox_enforcement": "off",  # a security knob, unreachable from an overlay
                }
            }
        },
    )
    activate(versions, "v1")
    overlay = resolve(versions)
    assert overlay.resource_limit_overrides() == {"max_tasks_per_cycle": 3}
    text = " ".join(overlay.warnings)
    assert "max_memory_bytes" in text and "max_retry_count" in text and "sandbox_enforcement" in text


def test_a_document_whose_fields_all_fail_is_not_loaded_at_all(tmp_path: Path):
    versions = tmp_path / "production"
    build_version(versions, "v1", {"config/cognitive_policy.json": {"nonsense": {"max_subtasks": 1}}})
    activate(versions, "v1")
    assert resolve(versions).documents == {}


@pytest.mark.parametrize(
    "kind,value,ok",
    [
        ("int", 5, True),
        ("int", True, False),
        ("int", "5", False),
        ("int", 0, False),
        ("str", "hello", True),
        ("str", "   ", False),
        ("list_name", ["transient"], True),
        ("list_name", ["made-up"], False),
        ("list_name", [], False),
    ],
)
def test_field_validation_is_per_kind_and_refuses_near_misses(kind: str, value: object, ok: bool):
    rule = Field(kind=kind, minimum=1, maximum=10, allowed=("transient",), max_entries=4)
    _cleaned, problems = rule.validate("doc.field", value)
    assert bool(problems) is not ok, f"{kind} with {value!r}: expected {'accept' if ok else 'refusal'}"


def test_a_bool_is_not_an_integer_even_though_python_thinks_it_is():
    """``True`` satisfies ``isinstance(x, int)``; a limit of ``True`` is a limit of 1.

    Worth its own test because the coercion is silent in the language and invisible in a diff, which
    is the profile of the bugs this file keeps re-checking for.
    """
    cleaned, problems = Field(kind="int", minimum=1, maximum=10).validate("x", True)
    assert cleaned is None and problems


def test_every_target_the_sandbox_accepts_maps_to_a_kind_with_a_materializer():
    """The three tables must agree, or the phase boundary is fiction.

    ``SandboxEngine.SUPPORTED_TARGETS`` (what will run an experiment), ``TARGET_TO_KIND`` (what payload
    shape that name means) and the eligibility registry (what is protected) are maintained in different
    files by different kinds of change. Checked pairwise they pass; the value is in the triple.
    """
    from evo_agent.materialization import for_target
    from evo_agent.sandbox import SandboxEngine
    from evo_agent.sovereign.eligibility import TARGET_KINDS

    for target in SandboxEngine.SUPPORTED_TARGETS:
        assert target in TARGET_TO_KIND, f"{target!r} is accepted by the engine but has no payload kind"
        assert for_target(target) is not None, f"{target!r} maps to a kind nothing materializes"
    registry = {kind.name for kind in TARGET_KINDS}
    for name in TARGET_TO_KIND:
        assert name in registry, f"TARGET_TO_KIND names {name!r}, which the eligibility registry does not declare"
    for relpath, spec in DOCUMENTS.items():
        assert spec.kind in {TARGET_TO_KIND[name] for name in TARGET_TO_KIND}, f"{relpath} belongs to no reachable kind"


# --- digests and the activation record --------------------------------------------------


def test_the_digest_ignores_file_order_and_cares_about_content(tmp_path: Path):
    versions = tmp_path / "production"
    build_version(
        versions,
        "v1",
        {
            "config/runtime.json": RUNTIME_DOC,
            "config/cognitive_policy.json": {"policy": {"max_subtasks": 4}},
        },
    )
    activate(versions, "v1")
    first = resolve(versions)
    second = resolve(versions)
    assert first.digest == second.digest
    (versions / "versions" / "v1" / OVERLAY_DIRNAME / "config" / "cognitive_policy.json").write_text(
        json.dumps({"policy": {"max_subtasks": 5}}, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    assert resolve(versions).digest != first.digest


def test_a_refused_field_changes_the_digest_even_though_nothing_loaded(tmp_path: Path):
    """The digest covers the *files*, so a payload that was silently ignored cannot look like nothing
    happened. That is the difference between "the overlay was empty" and "the overlay was refused"."""
    versions = tmp_path / "production"
    build_version(versions, "v1", {"config/runtime.json": RUNTIME_DOC})
    activate(versions, "v1")
    before = resolve(versions)
    build_version(versions, "v1", {"config/runtime.json": {"resource_limits": {"max_retry_count": 10**9}}})
    after = resolve(versions)
    assert after.digest != before.digest
    assert after.documents == {}


def test_the_activation_record_round_trips_and_lives_outside_the_version_tree(tmp_path: Path):
    versions = tmp_path / "production"
    build_version(versions, "v1", {"config/runtime.json": RUNTIME_DOC})
    activate(versions, "v1")
    overlay = resolve(versions)
    record = write_activation_record(versions, overlay, promotion_id="promo-1", version_id="v1")
    assert record["digest"] == overlay.digest
    payload = json.loads((versions / ACTIVATION_RECORD).read_text(encoding="utf-8"))
    assert payload["promotion_id"] == "promo-1"
    assert not (versions / "versions" / "v1" / OVERLAY_DIRNAME / ACTIVATION_RECORD).exists(), (
        "the record must not live inside the immutable version, which is chmod-ed read-only on purpose"
    )
    assert resolve(versions).activation_digest == overlay.digest


def test_consistency_survives_a_verified_activation_and_fails_after_tampering(tmp_path: Path):
    versions = tmp_path / "production"
    build_version(versions, "v1", {"config/runtime.json": RUNTIME_DOC})
    activate(versions, "v1")
    overlay = resolve(versions)
    write_activation_record(versions, overlay, version_id="v1")
    assert verify_activation(versions, resolve(versions))["consistent"] is True

    tampered = versions / "versions" / "v1" / OVERLAY_DIRNAME / "config" / "runtime.json"
    tampered.chmod(0o600)
    tampered.write_text(
        json.dumps({"resource_limits": {"max_tasks_per_cycle": 500}}, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    report = verify_activation(versions, resolve(versions))
    assert report["consistent"] is False
    assert "no longer matches" in report["reason"]


def test_an_overlay_with_no_record_is_inconsistent_but_repo_default_is_not(tmp_path: Path):
    """"Someone wrote an overlay and nobody activated it" is a different claim from "nothing is overlaid"."""
    versions = tmp_path / "production"
    assert verify_activation(versions, resolve(versions))["consistent"] is True
    build_version(versions, "v1", {"config/runtime.json": RUNTIME_DOC})
    activate(versions, "v1")
    report = verify_activation(versions, resolve(versions))
    assert report["consistent"] is False
    assert "no activation record" in report["reason"]


# --- applying ---------------------------------------------------------------------------


def test_applying_is_idempotent_and_reverts_what_the_overlay_does_not_mention(tmp_path: Path):
    """The two properties a rollback depends on, in one test because they are the same property.

    Applied twice, a value must not move twice; and a knob the new overlay does not name must return to
    the shipped default rather than keep the promoted value. The second half is the one an
    implementation typically forgets, and it is the difference between "rolled back" and "rolled back
    on next restart".
    """
    versions = tmp_path / "production"
    build_version(versions, "v1", {"config/runtime.json": RUNTIME_DOC})
    activate(versions, "v1")
    limits = RuntimeResourceLimits()
    defaults = limits.to_dict()
    overlay = resolve(versions)

    first = apply_overlays(overlay, limits=limits, limits_defaults=defaults)
    assert first["resource_limits"] == {"max_tasks_per_cycle": {"from": 1, "to": 3}, "max_retry_count": {"from": 2, "to": 4}}
    second = apply_overlays(overlay, limits=limits, limits_defaults=defaults)
    assert second["resource_limits"] == {}, "the same overlay moved a counter twice"

    (versions / "versions" / "v1" / OVERLAY_DIRNAME / "config" / "runtime.json").write_text(
        json.dumps({"resource_limits": {"max_retry_count": 2}}, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    reduced = apply_overlays(resolve(versions), limits=limits, limits_defaults=defaults)
    # max_retry_count is named in the reduced document, so it follows the document; max_tasks_per_cycle
    # is not, so it returns to the shipped default. Both directions in one cycle is the whole claim.
    assert reduced["resource_limits"] == {
        "max_retry_count": {"from": 4, "to": 2},
        "max_tasks_per_cycle": {"from": 3, "to": 1},
    }
    assert reduced["reset"] == ["max_tasks_per_cycle"]
    assert limits.to_dict() == defaults


def test_a_refused_field_is_reported_rather_than_applied(tmp_path: Path):
    versions = tmp_path / "production"
    build_version(versions, "v1", {"config/cognitive_policy.json": {"policy": {"max_subtasks": 2}}})
    activate(versions, "v1")
    limits = RuntimeResourceLimits()
    before = limits.to_dict()
    report = apply_overlays(resolve(versions), limits=limits, limits_defaults=limits.to_dict())
    # The cognitive document is applied to the orchestrator, not to the limits table; the point of this
    # test is that a document for one consumer cannot leak into the other and quietly move a number.
    assert report["resource_limits"] == {} and report["policy"] == {}
    assert report["refused"] == []
    assert limits.to_dict() == before


def test_an_overlay_cannot_name_a_field_the_limits_dataclass_does_not_have(tmp_path: Path):
    """Defense in depth: the schema allow-list can be widened by mistake, the dataclass cannot."""
    overlay = ActiveOverlay(
        source="candidate",
        version_id=None,
        overlay_root=None,
        documents={"config/runtime.json": {"resource_limits": {"max_tasks_per_cycle": 2, "not_a_field": 7}}},
        digest="x",
    )
    limits = RuntimeResourceLimits()
    report = apply_overlays(overlay, limits=limits, limits_defaults=limits.to_dict())
    assert not hasattr(limits, "not_a_field")
    assert any("not_a_field" in item for item in report["refused"])
    assert report["not_applied"] is True
    # All-or-nothing, including the half that was fine. A field the consumer does not have means the
    # document table and the dataclass disagree about what this build can run, which is a broken
    # governance pair rather than a rejected candidate - and adopting part of it would mean the agent
    # runs a mixture that no overlay ever described and no experiment measured.
    assert limits.max_tasks_per_cycle == 1
    assert report["resource_limits"] == {}


def test_a_refused_leg_blocks_every_other_leg_of_the_same_overlay(tmp_path: Path):
    """Atomicity across consumers, which is what makes "rollback restores A" mean *exactly* A.

    One overlay carries a legal limit change and a tool preference naming a tool this registry does not
    have. The limits leg alone would have applied. The test asserts the whole commit is skipped, that the
    refusal is attributed, and - the part that is easy to get wrong - that a *second* cycle with the same
    overlay reaches the same conclusion rather than applying the leg the first cycle had left pending.
    """
    class PartialRegistry:
        """A tool registry that does not have one of the names the document table declares.

        Duck-typed on purpose: the whole point is that a *consumer's* refusal must be respected even
        though the resolver's schema accepted the payload, and the only way to construct that
        disagreement without editing governance tables is a consumer that disagrees.
        """

        def __init__(self, names):
            self._names = list(names)
            self.preference = None

        def plan_preference(self, preference):
            wanted = [name for name in preference if name in self._names]
            unknown = [name for name in preference if name not in self._names]
            return wanted, unknown

        def reorder(self, preference):
            if preference is None:
                return []
            unknown = [name for name in preference if name not in self._names]
            self.preference = [name for name in preference if name in self._names]
            return unknown

        def order(self):
            return list(self.preference or self._names)

        def plan_risk_uplift(self, uplift):
            return {}, [f"{name}: this build has no risk floors" for name in uplift]

        def risk_floors(self):
            return {name: "low" for name in self._names}

        def apply_risk_uplift(self, decisions):
            raise AssertionError("must not be reached")

        def reset_risk_floors(self):
            return []

    overlay = ActiveOverlay(
        source="candidate",
        version_id=None,
        overlay_root=None,
        documents={
            "config/runtime.json": {"resource_limits": {"max_tasks_per_cycle": 2}},
            "config/tools.json": {"preference": ["workspace_read", "tool_removed_in_this_build"]},
        },
        digest="x",
    )
    registry = PartialRegistry(["workspace_read", "workspace_write"])
    limits = RuntimeResourceLimits()
    report = apply_overlays(overlay, limits=limits, limits_defaults=limits.to_dict(), tools=registry)
    assert any("tool_removed_in_this_build" in item for item in report["refused"]), report["refused"]
    assert limits.max_tasks_per_cycle == 1
    assert registry.preference is None, "the accepted leg was applied anyway"
    assert report["resource_limits"] == {} and report["tool_preference"] == {}
    second = apply_overlays(overlay, limits=limits, limits_defaults=limits.to_dict(), tools=registry)
    assert second["refused"] == report["refused"], "the second cycle reached a different conclusion"
    assert limits.max_tasks_per_cycle == 1


def test_a_commit_that_raises_partway_leaves_no_leg_applied(tmp_path: Path):
    """Atomicity is only real if it survives a failure *during* the writes, not only a refusal before them.

    Pre-commit checking and committing are separate steps, so any consumer whose state changes in between
    - a descriptor that validates on assignment, a registry that lost a tool mid-cycle - can fail halfway.
    Without the journal that becomes "the agent is running a configuration no overlay ever described",
    which is the one failure mode the resolver cannot recover from by re-reading a file.
    """
    from evo_agent.runtime import FailureClass

    class HalfBroken:
        """A recovery consumer with ``RecoveryManager``'s semantics that raises after its first write.

        Faithful in the one way the journal depends on: ``apply_overlay`` *replaces* the set with the
        overlay's additions plus the class floor, so an undo expressed as "apply the snapshot I read
        before" restores it exactly. A consumer that only unions could not be undone by anything short of
        a restart, which is worth stating because it is the contract that leg is built on.
        """

        FLOOR = frozenset({FailureClass.PERMISSION, FailureClass.APPROVAL})

        def __init__(self):
            self.applied_calls = 0
            self.blocked: set[FailureClass] = set(self.FLOOR)

        def plan_overlay(self, recovery):
            requested = (recovery or {}).get("never_retry") or []
            return self.FLOOR | {FailureClass(str(name)) for name in requested}, []

        def apply_overlay(self, recovery):
            self.applied_calls += 1
            requested = {FailureClass(str(name)) for name in (recovery or {}).get("never_retry") or []}
            self.blocked = set(self.FLOOR) | requested
            if self.applied_calls == 1:
                raise RuntimeError("transient store failure")
            return {
                "added": sorted(item.value for item in requested),
                "removed": [],
                "refused": [],
            }

        @property
        def never_retry_classes(self) -> set[FailureClass]:
            return set(self.blocked)

    broken = HalfBroken()
    overlay = ActiveOverlay(
        source="candidate",
        version_id=None,
        overlay_root=None,
        documents={
            "config/runtime.json": {"recovery": {"never_retry": ["resource"]}, "resource_limits": {}},
        },
        digest="x",
    )
    report = apply_overlays(overlay, limits=None, recovery=broken)
    assert report["not_applied"] is True
    assert any("application aborted" in item for item in report["refused"]), report["refused"]
    assert broken.blocked == set(HalfBroken.FLOOR), "the leg kept the addition it made before raising"
    assert broken.applied_calls == 2, "the undo should have re-applied the snapshot, not given up"
    assert report["recovery"] == {}


def test_a_later_leg_that_raises_undoes_the_earlier_legs_completely(tmp_path: Path):
    """The journal's undo loop has to *finish*, which is a stronger claim than "it exists".

    Found while wiring P5: ``_restore_tools`` - the tools leg's undo - ended in a stray ``return report``
    left by an earlier paste, so it raised ``NameError`` after doing its work. The journal caught that as a
    failed undo, appended "rollback of a partially applied leg also failed", and **broke out of the loop**,
    skipping every remaining leg. The state ended up right for the leg that raised and wrong for whatever
    came before it, and the record said so in a sentence nobody could act on. A test of the final state
    alone could not see it; this one asserts the whole undo sequence ran.
    """
    class Tools:
        def __init__(self) -> None:
            self._order = ["workspace_read", "shell"]
            self.reorders: list[list[str] | None] = []
            self.uplifts: list[dict] = []

        def order(self) -> list[str]:
            return list(self._order)

        def risk_floors(self) -> dict[str, str]:
            return {"shell": "high"}

        def plan_risk_uplift(self, uplift):
            return {name: {"from": self.risk_floors().get(name, "medium"), "to": str(value)} for name, value in uplift.items()}, []

        def plan_preference(self, preference):
            known = [name for name in preference if name in self._order]
            return known, [name for name in preference if name not in self._order]

        def reorder(self, preference):
            self.reorders.append(list(preference) if preference else None)
            if preference:
                self._order = list(preference) + [name for name in self._order if name not in preference]
            return []

        def apply_risk_uplift(self, changes) -> None:
            self.uplifts.append(dict(changes))

    class Memory:
        """A retrieval target that honours the rollback call and refuses the overlay one."""

        def __init__(self) -> None:
            self.calls: list[str] = []
            self.weights = {"topic_relevance": 150}

        def current_weights(self) -> dict[str, int]:
            return dict(self.weights)

        def plan_policy(self, desired):
            return [{"field": name, "from": self.weights.get(name), "to": value} for name, value in desired.items()], []

        def apply_policy(self, weights, *, source: str = "overlay") -> bool:
            self.calls.append(source)
            if source == "overlay":
                raise RuntimeError("engine refuses to rank by a weight it cannot name")
            self.weights.update(weights or {})
            return True

    tools, memory = Tools(), Memory()
    overlay = ActiveOverlay(
        source="candidate",
        version_id=None,
        overlay_root=None,
        documents={
            "config/tools.json": {"preference": ["shell", "workspace_read"]},
            "config/memory.json": {"retrieval_weights": {"topic_relevance": 400}},
        },
        digest="x",
    )
    report = apply_overlays(overlay, limits=None, tools=tools, memory=memory)
    assert report["not_applied"] is True
    assert not any("also failed" in item for item in report["refused"]), report["refused"]
    assert any("application aborted" in item for item in report["refused"])
    # the tools leg was reordered by the overlay and then restored - both halves, in that order
    assert tools.reorders == [["shell", "workspace_read"], ["workspace_read", "shell"]], tools.reorders
    assert tools.order() == ["workspace_read", "shell"]
    # and the memory leg's own undo ran before the tools one, which is the reverse of application order
    assert memory.calls == ["overlay", "rollback"], memory.calls


def test_a_limits_object_the_planner_cannot_reason_about_is_refused_not_raised(tmp_path: Path):
    """A cycle must survive a consumer whose shape it cannot read.

    Planning runs once per cycle in production, so any exception here takes the agent down *while the
    overlay stays in place* - which is the worst combination: broken behaviour and no ledger entry. The
    refusal names the consumer and the reason instead.
    """
    class NotADataclass:
        __dataclass_fields__ = {"max_tasks_per_cycle": object()}
        max_tasks_per_cycle = 1

    overlay = ActiveOverlay(
        source="candidate",
        version_id=None,
        overlay_root=None,
        documents={"config/runtime.json": {"resource_limits": {"max_tasks_per_cycle": 3}}},
        digest="x",
    )
    limits = NotADataclass()
    report = apply_overlays(overlay, limits=limits, limits_defaults={"max_tasks_per_cycle": 1})
    assert report["not_applied"] is True
    assert any("rejected by NotADataclass" in item for item in report["refused"]), report["refused"]
    assert limits.max_tasks_per_cycle == 1


def test_the_tool_legs_are_applied_and_restored_without_a_restart(tmp_path: Path):
    """Preference order and risk floors are the two overlay legs the *tool registry* holds.

    Both directions are asserted because a capability that can be adopted but not withdrawn is the defect
    this phase is named for, and a tool registry is the one consumer whose state a resolver cannot recover
    by re-reading a file - it has to remember what it shipped with.
    """
    from evo_agent.security import SecurityPolicy
    from evo_agent.tools import ToolRegistry

    registry = ToolRegistry(SecurityPolicy(tmp_path / "ws"))
    limits = RuntimeResourceLimits()
    overlay = ActiveOverlay(
        source="candidate",
        version_id=None,
        overlay_root=None,
        documents={"config/tools.json": {"preference": ["shell", "workspace_read"], "risk_floor_uplift": {"workspace_read": 3}}},
        digest="x",
    )
    report = apply_overlays(overlay, limits=limits, limits_defaults=limits.to_dict(), tools=registry)
    assert report["refused"] == [] and not report.get("not_applied")
    assert registry.order()[:2] == ["shell", "workspace_read"]
    assert registry.risk_floors()["workspace_read"] == "critical"
    assert report["risk_floors"] == {"workspace_read": {"from": "low", "to": "critical"}}

    again = apply_overlays(overlay, limits=limits, limits_defaults=limits.to_dict(), tools=registry)
    assert again["risk_floors"] == {} and again["tool_preference"]["applied"] == ["shell", "workspace_read"], "the same overlay moved a floor twice"

    withdrawn = apply_overlays(
        ActiveOverlay(source="repo-default", version_id=None, overlay_root=None, documents={}, digest="y"),
        limits=limits,
        limits_defaults=limits.to_dict(),
        tools=registry,
    )
    assert registry.risk_floors()["workspace_read"] == "low"
    assert registry.order() == ["workspace_list", "workspace_read", "workspace_write", "shell"]
    assert withdrawn["reset"] == ["risk_floor.workspace_read"]


def test_default_versions_root_is_a_sibling_of_the_source_root():
    """``.evo-production`` must stay *outside* the production tree, or staging overwrites itself."""
    root = default_versions_root(Path("/opt/evo"))
    assert root == Path("/opt/.evo-production")
    assert root != Path("/opt/evo") and not str(root).startswith("/opt/evo/")


def test_to_dict_reports_shape_and_not_content(tmp_path: Path):
    """Event payloads carry the digest and the document names, never the values.

    Prompts and thresholds are the interesting data for anyone with read access to the ledger, and the
    digest already lets a reviewer pull the exact content from the version it points at.
    """
    versions = tmp_path / "production"
    build_version(versions, "v1", {"config/runtime.json": RUNTIME_DOC})
    activate(versions, "v1")
    payload = resolve(versions).to_dict()
    assert payload["source"] == "active" and payload["version_id"] == "v1"
    assert payload["loaded"] == ["config/runtime.json"]
    assert json.dumps(payload)  # must stay serialisable for the ledger
    assert "max_tasks_per_cycle" not in json.dumps(payload)
