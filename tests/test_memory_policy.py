"""Memory *policy* is an evolution target; memory *contents* are not (07 §4, 03 §E).

P5 gave ``config/memory.json`` the thing P3 said it lacked: a consumer. The tests here are therefore
about three separate claims, and each is checked where it is actually decided:

1. **The ranking moves.** A promoted weight changes what ``RetrievalEngine`` returns, in the same
   process, and rollback puts the old order back - including the components the withdrawn overlay never
   mentioned, which is the P3 rule for a rollback rather than a restart.
2. **The lifetime knobs are refused in a candidate payload.** ``retention_days`` and ``staleness_ratio``
   are valid operator configuration and invalid evolution, because expiry writes ``EXPIRED`` onto stored
   rows and no rollback can un-expire them without becoming a writer of memory.
3. **Nothing here touches content.** Refusal reasons, not silence: a payload naming ``memories`` is
   refused with the rule that refuses it.

The resolver and the promotion spine are P3's territory (``test_active_version.py``,
``test_metamorphosis_closed_loop.py``) and are not re-proved here; the overlay this file feeds to the
runtime is built with the resolver's own documented candidate entry point, ``overlay_dir=``.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]

from evo_agent import active_version  # noqa: E402
from evo_agent.active_version import DEFAULT_MEMORY_WEIGHTS, MEMORY_WEIGHT_FIELDS, resolve  # noqa: E402
from evo_agent.materialization import materialize  # noqa: E402
from evo_agent.memory import (  # noqa: E402
    DEFAULT_RETRIEVAL_WEIGHTS,
    MemoryManager,
    MemoryPolicy,
    MemoryPolicyTarget,
    RetrievalQuery,
)
from evo_agent.runtime import AgentRuntime  # noqa: E402
from evo_agent.storage import SQLiteStore  # noqa: E402


def _stage(versions_root: Path, payload: dict[str, Any], *, name: str = "v1") -> Path:
    """Write one version directory holding the overlay, the way staging does.

    Deliberately *not* via ``PromotionEngine``: this file is about the consumer leg, and re-walking the
    staging/manifest/approval path here would only re-run tests that already exist, while making a
    failure here ambiguous about which half broke.
    """
    directory = versions_root / name / active_version.OVERLAY_DIRNAME / "config"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "memory.json").write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
    return versions_root / name


def _link_active(versions_root: Path, target: Path) -> None:
    link = versions_root / "active"
    if link.is_symlink() or link.exists():
        link.unlink()
    os.symlink(os.path.relpath(target, versions_root), link)


class _Engine:
    """A stand-in consumer with the two methods the leg asks of a retrieval engine."""

    def __init__(self) -> None:
        self.policy = MemoryPolicy()
        self.applications = 0

    def plan_policy(self, weights: dict[str, int]) -> tuple[list[dict[str, Any]], list[str]]:
        return MemoryPolicyTarget(self).plan_policy(weights)

    def apply_policy(self, weights: dict[str, int], *, source: str = "overlay") -> dict[str, Any]:
        self.applications += 1
        self.policy = MemoryPolicy(retrieval_weights={**DEFAULT_RETRIEVAL_WEIGHTS, **{k: int(v) for k, v in weights.items()}}, source=source)
        return {"applied": weights, "source": source}


# --- the schema and the consumer are one list, not two ------------------------------------


class TestOneDefinitionOfTheKnobs:
    def test_the_schema_allow_list_is_the_loader_component_list(self):
        """A document field with no consumer is the defect ``00`` §A was written about."""
        assert tuple(MEMORY_WEIGHT_FIELDS) == tuple(
            name for name in ("topic_relevance", "task_similarity", "strategy_similarity", "tool_similarity", "capability_similarity", "importance", "confidence")
        )
        from evo_agent.memory import RETRIEVAL_WEIGHT_FIELDS

        assert MEMORY_WEIGHT_FIELDS == RETRIEVAL_WEIGHT_FIELDS, "active_version and memory disagree about what may be ranked"
        assert set(DEFAULT_MEMORY_WEIGHTS) == set(DEFAULT_RETRIEVAL_WEIGHTS)
        assert DEFAULT_MEMORY_WEIGHTS == DEFAULT_RETRIEVAL_WEIGHTS, "the baseline a candidate is measured against must be the one a running process has"

    def test_the_document_is_declared_with_its_loader(self):
        spec = active_version.DOCUMENTS["config/memory.json"]
        assert spec.loadable and spec.loaded_by == "evo_agent.memory:RetrievalEngine"
        assert "retrieval_weights" in spec.fields

    def test_memory_json_now_passes_the_loader_gate(self, tmp_path: Path):
        """The refusal sentence must be gone from the real path, not reworded in one place only."""
        result = materialize("memory_policy", {"config/memory.json": {"retrieval_weights": {"topic_relevance": 300}}}, tmp_path / "candidate")
        assert result.ok, result.errors
        assert not any("nothing loads it" in text or "not loadable" in text for text in result.errors)


# --- validation refuses, and refuses for a stated reason -----------------------------------


class TestPolicyValidation:
    def test_a_weight_outside_the_range_is_refused(self):
        _policy, problems = MemoryPolicy.from_payload({"retrieval_weights": {"confidence": 5000}})
        assert problems and "outside 0-1000" in problems[0]

    def test_an_unknown_component_is_refused_naming_the_closed_list(self):
        _policy, problems = MemoryPolicy.from_payload({"retrieval_weights": {"sentiment": 10}})
        assert problems and "not a component this build ranks by" in problems[0]

    def test_a_lifetime_field_in_a_candidate_payload_is_refused(self):
        _policy, problems = MemoryPolicy.from_payload({"retention_days": 1}, from_overlay=True)
        assert problems and "retention_days" in problems[0]
        # The refusal has to say why, or the next reader "fixes" it by adding the field to the schema.
        assert "lifetime" in problems[0].lower()

    def test_the_same_field_is_accepted_from_an_operator(self):
        policy, problems = MemoryPolicy.from_payload({"retention_days": 30, "staleness_ratio": 50}, from_overlay=False)
        assert problems == []
        assert (policy.retention_days, policy.staleness_days) == (30, 15.0)

    def test_a_broken_payload_leaves_the_shipped_policy_in_place(self):
        policy, problems = MemoryPolicy.from_payload({"retrieval_weights": {"confidence": "high"}})
        assert problems and policy.retrieval_weights == DEFAULT_RETRIEVAL_WEIGHTS, "a partial policy is the one outcome worse than a refusal"

    def test_the_materializer_refuses_contents_shaped_payloads(self, tmp_path: Path):
        """Contents are refused twice: by a closed schema, and by a rule that names the principle.

        The schema refusal is the one that fires on the write path, because the table lists the legal
        fields and nothing else survives validation. Asserting only that would leave the *reason* -
        memory is evidence, never a payload (07 §4) - expressed nowhere but a comment, so the materializer
        carries the check too and it is exercised directly, exactly as the R7 provider rule is.
        """
        result = materialize("memory_policy", {"config/memory.json": {"memories": [{"content": "the operator is away"}]}}, tmp_path / "candidate")
        assert not result.ok
        assert any("config/memory.json.memories: not an allow-listed field" in text for text in result.errors), result.errors
        from evo_agent.materialization import materializer_for

        problems = materializer_for("memory_policy").extra_checks("config/memory.json", {"memories": [{"content": "x"}]})
        assert problems and "evidence, never an evolution payload" in problems[0]

    def test_the_materializer_refuses_retention_even_though_the_schema_allows_it(self, tmp_path: Path):
        """Two checks, two reasons, both firing: the schema describes the document, the materializer governs it."""
        result = materialize("memory_policy", {"config/memory.json": {"retention_days": 2}}, tmp_path / "candidate")
        assert not result.ok
        text = " ".join(result.errors)
        assert "may not come from a candidate payload" in text and "rollback cannot undo" in text

    def test_the_spine_accepts_the_target_the_loader_belongs_to(self):
        """A loader no candidate can reach is a feature, not an evolution target.

        P4's log recorded that ``config/memory.json`` had a schema row and a materializer but no *target
        name*, so the sandbox would not stage it; this pins the three-table agreement for the new name.
        """
        from evo_agent.materialization import for_target
        from evo_agent.sandbox import SandboxEngine
        from evo_agent.sovereign.eligibility import TARGET_KINDS, consistency_with_sandbox

        assert "memory_policy" in SandboxEngine.SUPPORTED_TARGETS
        assert for_target("memory_policy") is not None and for_target("memory_policy").target_kind == "memory_policy"
        kind = next(item for item in TARGET_KINDS if item.name == "memory_policy")
        assert kind.loadable and kind.sandbox_accepted and kind.phase == "P5"
        assert consistency_with_sandbox() == [], "the registry, the engine, and the loader must agree"


# --- the leg: idempotent, reset-capable, atomic -------------------------------------------


class TestTheApplyLeg:
    def _overlay(self, tmp_path: Path, payload: dict[str, Any], *, name: str = "v1"):
        return resolve(overlay_dir=_stage(tmp_path / "versions", payload, name=name))

    def test_applying_twice_changes_nothing_the_second_time(self, tmp_path: Path):
        engine = _Engine()
        target = MemoryPolicyTarget(engine)
        overlay = self._overlay(tmp_path, {"retrieval_weights": {"topic_relevance": 100}})
        first = active_version.apply_overlays(overlay, memory=target, memory_defaults=dict(DEFAULT_MEMORY_WEIGHTS))
        second = active_version.apply_overlays(overlay, memory=target, memory_defaults=dict(DEFAULT_MEMORY_WEIGHTS))
        assert not first["refused"] and not second["refused"]
        assert first["memory_policy"]["weights"] == second["memory_policy"]["weights"]
        assert engine.policy.weight("topic_relevance") == pytest.approx(0.1)

    def test_a_withdrawn_weight_returns_to_its_default_in_the_same_process(self, tmp_path: Path):
        engine = _Engine()
        target = MemoryPolicyTarget(engine)
        strong = self._overlay(tmp_path, {"retrieval_weights": {"topic_relevance": 900, "confidence": 5}}, name="v1")
        partial = self._overlay(tmp_path, {"retrieval_weights": {"topic_relevance": 900}}, name="v2")
        active_version.apply_overlays(strong, memory=target, memory_defaults=dict(DEFAULT_MEMORY_WEIGHTS))
        assert engine.policy.weight("confidence") == pytest.approx(0.005)
        applied = active_version.apply_overlays(partial, memory=target, memory_defaults=dict(DEFAULT_MEMORY_WEIGHTS))
        assert engine.policy.weight("confidence") == pytest.approx(DEFAULT_RETRIEVAL_WEIGHTS["confidence"] / 1000.0)
        assert "memory.retrieval_weights.confidence" in applied["reset"], "the report must say what it put back"

    def test_an_unknown_component_refuses_the_leg_and_touches_nothing(self, tmp_path: Path):
        engine = _Engine()
        target = MemoryPolicyTarget(engine)
        before = dict(engine.policy.retrieval_weights)
        # Bypassing the schema on purpose: the consumer is the last line, and a resolver that was
        # tightened without tightening this would otherwise be untested.
        overlay = active_version.ActiveOverlay(source="test", version_id=None, overlay_root=None, documents={"config/memory.json": {"retrieval_weights": {"gullibility": 1000}}})
        applied = active_version.apply_overlays(overlay, memory=target, memory_defaults=before)
        assert applied["not_applied"] and any("memory." in item for item in applied["refused"])
        assert engine.policy.retrieval_weights == before
        assert engine.applications == 0

    def test_two_engines_share_one_policy(self, tmp_path: Path):
        """The kernel and the planner rank by the same numbers, or the same query ranks twice."""
        first, second = _Engine(), _Engine()
        target = MemoryPolicyTarget(first, second)
        overlay = self._overlay(tmp_path, {"retrieval_weights": {"importance": 0}})
        active_version.apply_overlays(overlay, memory=target, memory_defaults=dict(DEFAULT_MEMORY_WEIGHTS))
        assert first.policy.retrieval_weights == second.policy.retrieval_weights
        assert second.policy.weight("importance") == pytest.approx(0.0)

    def test_no_engine_means_no_claim(self):
        target = MemoryPolicyTarget()
        _decisions, problems = target.plan_policy({"topic_relevance": 1})
        assert problems == ["no memory retrieval engine is wired"]


# --- the ranking actually changes, and memory state does not ------------------------------


def _seed(store: SQLiteStore, workspace: Path) -> MemoryManager:
    from evo_agent.memory import ConfidenceLevel, MemoryType, ProvenanceSource

    manager = MemoryManager(store, workspace)
    manager.store(manager._record(MemoryType.SEMANTIC, "alpha beta gamma", "alpha beta gamma about widgets", ProvenanceSource.OBSERVATION, "s1", ConfidenceLevel.HIGH, 0.9, 0.1, key="widgets-alpha"))
    manager.store(manager._record(MemoryType.SEMANTIC, "zeta eta", "zeta eta, high importance", ProvenanceSource.OBSERVATION, "s2", ConfidenceLevel.HIGH, 0.9, 1.0, key="widgets-zeta"))
    return manager


class TestRankingIsTheConsumer:
    def test_the_shipped_weights_reproduce_the_pre_p5_order(self, tmp_path: Path):
        """Baseline first: P5 must not have changed behaviour it did not claim to change."""
        store = SQLiteStore(tmp_path / "a.sqlite3")
        manager = _seed(store, tmp_path)
        results = manager.retrieve(RetrievalQuery(goal="alpha beta gamma widgets", max_memories=5))
        assert [item.memory.key for item in results] == ["widgets-alpha", "widgets-zeta"]
        assert results[0].score_breakdown["topic_relevance"] == pytest.approx(round(1.0 * 0.45, 6))

    def test_zeroing_topic_relevance_moves_the_top_result(self, tmp_path: Path):
        store = SQLiteStore(tmp_path / "b.sqlite3")
        manager = _seed(store, tmp_path)
        manager.retrieval.policy = MemoryPolicy(retrieval_weights={**DEFAULT_RETRIEVAL_WEIGHTS, "topic_relevance": 0, "importance": 1000})
        results = manager.retrieve(RetrievalQuery(goal="alpha beta gamma widgets", max_memories=5))
        assert results and results[0].memory.key == "widgets-zeta", "a policy that re-ranks nothing has no consumer"
        assert results[0].score_breakdown["topic_relevance"] == pytest.approx(0.0)

    def test_staleness_is_a_label_and_never_a_status_change(self, tmp_path: Path):
        store = SQLiteStore(tmp_path / "c.sqlite3")
        manager = _seed(store, tmp_path)
        old = (datetime.now(timezone.utc) - timedelta(days=400)).isoformat()
        for record in manager.memory_store.list(limit=10):
            record.updated_at = old
            record.created_at = old
            manager.sqlite_store.save_memory(record)
        # A 100-day retention window at the default ratio: the records are 400 days old, so they are
        # history, and the only legal consequence of that label is the warning the caller reads.
        manager.retrieval.policy = MemoryPolicy(retention_days=100, staleness_ratio=100)
        results = manager.retrieve(RetrievalQuery(goal="alpha widgets", max_memories=5))
        assert results, "a stale record is still retrievable"
        assert all("staleness window" in " ".join(item.warnings) for item in results)
        for record in manager.memory_store.list(limit=10):
            assert record.status.value == "active", "the policy may label history; expiring rows is ForgettingEngine's, and not overlay-writable"


# --- the runtime applies it per cycle, and un-applies it ----------------------------------


class TestTheRuntimeAppliesIt:
    def _runtime(self, tmp_path: Path) -> AgentRuntime:
        versions = tmp_path / "versions"
        runtime = AgentRuntime(tmp_path, source_root=ROOT, versions_root=versions)
        return runtime

    def test_an_active_overlay_reweights_the_engines_without_a_restart(self, tmp_path: Path):
        """Activate by link, and the next cycle ranks differently; withdraw it, and it does not.

        The activation record is written by hand because the spine that normally writes it - stage,
        benchmark, approve, switch - is P3's tested ground (``test_metamorphosis_closed_loop.py``). What
        is under test here is the leg: resolver output to live engine, and back.
        """
        versions = tmp_path / "versions"
        staged = _stage(versions, {"retrieval_weights": {"topic_relevance": 7, "confidence": 900}})
        overlay = resolve(overlay_dir=staged)
        assert overlay.documents, "the staged document has to survive the real resolver"
        versions.mkdir(parents=True, exist_ok=True)
        _link_active(versions, staged)
        (versions / active_version.ACTIVATION_RECORD).write_text(
            json.dumps({"digest": overlay.digest, "version_id": "v1", "documents": list(overlay.relpaths)}, sort_keys=True),
            encoding="utf-8",
        )
        runtime = AgentRuntime(tmp_path, source_root=ROOT, versions_root=versions)
        engines = runtime.memory_policy_target.consumers
        assert len(engines) == 2, "the kernel and the planner must be wired, or the same query ranks twice"
        assert dict(engines[0].policy.retrieval_weights) == DEFAULT_MEMORY_WEIGHTS
        runtime.start()
        runtime.run_cycle()
        assert engines[0].policy.weight("topic_relevance") == pytest.approx(0.007)
        assert engines[1].policy.weight("confidence") == pytest.approx(0.9)
        assert engines[0].policy.source == "overlay"
        report = runtime.overlay_report["applied"]["memory_policy"]
        assert report["weights"]["topic_relevance"] == 7 and report["changes"], "the cycle has to say it moved the ranking"
        # Withdraw the version the way rollback does: the link moves and the activation record goes with
        # it. A link removed while a record still names v1 is not a rollback, it is a tamper shape, and
        # the runtime is right to keep serving the last verified configuration in that case.
        (versions / "active").unlink()
        (versions / active_version.ACTIVATION_RECORD).unlink()
        runtime.run_cycle()
        assert engines[0].policy.weight("topic_relevance") == pytest.approx(0.45)
        reset = runtime.overlay_report["applied"]["reset"]
        assert any(item.startswith("memory.retrieval_weights.") for item in reset), reset
        runtime.stop("test complete")

    def test_an_operator_policy_survives_as_the_baseline(self, tmp_path: Path):
        """Rollback restores how *this agent was started*, which for a customised launch is not the shipped build."""
        operator = MemoryPolicy(retrieval_weights={**DEFAULT_RETRIEVAL_WEIGHTS, "confidence": 250}, source="operator")
        runtime = AgentRuntime(tmp_path, source_root=ROOT, versions_root=tmp_path / "versions", memory_policy=operator)
        assert dict(runtime._memory_defaults)["confidence"] == 250
        engine = next(iter(runtime.memory_policy_target.consumers))
        assert engine.policy.weight("confidence") == pytest.approx(0.25)
        runtime.stop("test complete")


def test_the_report_says_which_memory_policy_is_in_force(tmp_path: Path):
    """A ranking nobody can read out of the runtime is a ranking nobody can audit."""
    runtime = AgentRuntime(tmp_path, source_root=ROOT, versions_root=tmp_path / "versions")
    assert runtime.memory_policy_target.to_dict() == MemoryPolicy().to_dict()
    runtime.start()
    runtime.run_cycle()
    applied = runtime.overlay_report["applied"]
    assert applied["memory_policy"]["weights"] == DEFAULT_MEMORY_WEIGHTS, (
        "an overlay that says nothing about memory still reports the ranking in force, so the absence is "
        "a stated fact rather than a missing key"
    )
    runtime.stop("test complete")


# --- what may never be loaded --------------------------------------------------------------


class TestWhatMayNeverBeLoaded:
    def test_prompt_text_is_not_an_evolution_target_in_any_phase(self):
        spec = active_version.DOCUMENTS["config/prompts.json"]
        assert not spec.loadable and spec.blocked_by
        assert "03 §E" in spec.blocked_by

    def test_the_strategy_document_is_blocked_by_a_fact_about_the_build(self):
        spec = active_version.DOCUMENTS["config/strategy.json"]
        assert not spec.loadable and "one runnable strategy" in spec.blocked_by
        # ...and the allow-list the day it opens is the one the pipeline already refuses against.
        from evo_agent.pipeline import PIPELINE_STRATEGIES

        assert PIPELINE_STRATEGIES == active_version.STRATEGY_NAMES


# --- the operator's entry point ---------------------------------------------------


class TestStartupRefusal:
    """A refused configuration must fail the process, not just the paragraph a human reads.

    ``inspect_command`` answers "was this an inspection command" with a bool, and ``main`` maps ``True`` to
    exit ``0`` - so an error payload printed and returned that way leaves a script, a CI job, and a
    systemd unit believing the agent started with the operator's policy when it refused it. The fix is a
    refusal that raises; the test is the exit code, because that is the half a reviewer cannot see by
    reading the printed JSON.
    """

    def _argv(self, tmp_path: Path, config: Path) -> list[str]:
        return ["evo", "run", "--workspace", str(tmp_path), "--runtime-status", "--memory-config", str(config)]

    def test_an_invalid_operator_policy_exits_nonzero(self, tmp_path: Path, monkeypatch, capsys) -> None:
        import sys

        from evo_agent import cli

        config = tmp_path / "memory.json"
        config.write_text(json.dumps({"retrieval_weights": {"topic_relevance": 5000}}), encoding="utf-8")
        monkeypatch.setattr(sys, "argv", self._argv(tmp_path, config))
        with pytest.raises(SystemExit) as exitinfo:
            cli.main()
        assert exitinfo.value.code == 1
        printed = capsys.readouterr().out
        assert "memory policy is not valid" in printed and "0-1000" in printed

    def test_a_missing_policy_file_is_refused_rather_than_ignored(self, tmp_path: Path, monkeypatch, capsys) -> None:
        import sys

        from evo_agent import cli

        monkeypatch.setattr(sys, "argv", self._argv(tmp_path, tmp_path / "absent.json"))
        with pytest.raises(SystemExit) as exitinfo:
            cli.main()
        assert exitinfo.value.code == 1
        assert "no such file" in capsys.readouterr().out

    def test_a_valid_operator_policy_starts_and_reports_the_leg(self, tmp_path: Path, monkeypatch, capsys) -> None:
        import sys

        from evo_agent import cli

        config = tmp_path / "memory.json"
        config.write_text(json.dumps({"retrieval_weights": {"topic_relevance": 400}}), encoding="utf-8")
        monkeypatch.setattr(sys, "argv", self._argv(tmp_path, config))
        assert cli.main() == 0
        printed = capsys.readouterr().out
        # The operator's own weights, and nothing else, are what the retrieval engines will rank by.
        assert '"topic_relevance": 400' in printed or "topic_relevance" in printed
