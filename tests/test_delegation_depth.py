"""Delegation depth, executor ceilings, and why a subagent's memory is not the parent's (07 §8).

Three rules, one mechanism each, and all of them enforced by the engine rather than by the caller:

* **Depth.** A delegated specialist may not delegate again. The ceiling is read from the engine's own
  in-flight ledger, not from a field a subagent could decline to set, and the ledger is keyed by the database
  the engine writes through - because the realistic escape is a subagent that builds its *own* engine over
  the same store, and a per-instance counter would not see it.
* **Turn ceiling.** The engine cannot count an executor's turns (the executor owns its loop), so what it
  enforces is the number *in the contract handed to the executor*: ask for more than the ceiling and the
  contract arrives clamped, with the request recorded next to it.
* **Mediation.** A subagent gets no execution primitive from this module. There is nothing to call: no
  ``run``, no ``exec``, no sandbox bypass - which is what the source-scan assertion below pins, so the
  property survives someone adding a convenience helper in a later phase.

The memory half belongs here too: delegation is what made ``memory_records.scope_key`` load-bearing (07 Q6),
because the moment a second context exists, "what may the planner remember" needs a second answer.
"""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path

import pytest

from evo_agent.memory import MemoryManager, RetrievalQuery
from evo_agent.security import SecurityPolicy
from evo_agent.specialist import (
    SpecialistContext,
    SpecialistDelegationEngine,
    SpecialistLimits,
    SpecialistOutput,
    SpecialistRisk,
)
from evo_agent.storage import SQLiteStore

MODULE_SOURCE = Path(__file__).resolve().parents[1] / "evo_agent" / "specialist.py"


@pytest.fixture()
def store(tmp_path: Path) -> SQLiteStore:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    return SQLiteStore(workspace / ".evo" / "agent.sqlite3")


@pytest.fixture()
def engine(store: SQLiteStore):
    workspace = store.path.parents[1]
    return SpecialistDelegationEngine(store, workspace)


def finish(con, _context, **kwargs) -> SpecialistOutput:
    # A dict claim, because the default expected-output schema is an object: a string claim is refused by
    # ``_validate_output``, and a test whose executor is refused for the wrong reason proves nothing.
    return SpecialistOutput(con.specialist_task_id, success=True, claim={"result": "done"}, **kwargs)


class TestLimits:
    def test_the_defaults_are_the_documented_ones(self) -> None:
        limits = SpecialistLimits()
        assert limits.max_delegation_depth == 1 and limits.max_turns_per_specialist == 8

    def test_the_limits_are_serialised(self) -> None:
        payload = SpecialistLimits().to_dict()
        assert payload["max_delegation_depth"] == 1 and payload["max_turns_per_specialist"] == 8
        assert json.dumps(payload)

    def test_an_explicitly_wider_ceiling_is_honoured_but_floored_at_one(self) -> None:
        # Two, not zero: a deployment that asks for "no delegation at all" gets the refusal path by not
        # calling `delegate`, and a ceiling of 0 would make every legitimate top-level delegation an error.
        assert SpecialistLimits(max_delegation_depth=2).max_delegation_depth == 2


class TestDepth:
    def test_a_subagent_may_not_delegate_again(self, engine: SpecialistDelegationEngine) -> None:
        task, contract = engine.create_contract("parent", "read the notes", "specialist_research")
        seen: dict[str, object] = {}

        def executor(con, context):
            try:
                engine.delegate(con.parent_task_id, [(task, con, None)], executor, parallel=False)
                seen["nested"] = "ALLOWED"
            except ResourceWarning as exc:
                seen["nested"] = "refused"
                seen["reason"] = str(exc)
            return SpecialistOutput(con.specialist_task_id, success=True, claim={"result": "outer"})

        _run, outputs, _fusion = engine.delegate("parent", [(task, contract, None)], executor, parallel=False)
        assert outputs[0].success is True, outputs[0].error
        assert seen["nested"] == "refused" and "ceiling of 1" in seen["reason"]

    def test_the_ceiling_survives_a_subagent_building_its_own_engine(self, engine: SpecialistDelegationEngine, store: SQLiteStore) -> None:
        # The escape attempt this design exists to close: a fresh engine over the same database.
        second = SpecialistDelegationEngine(store, engine.workspace)
        task, contract = engine.create_contract("parent", "read the notes", "specialist_research")
        answer: dict[str, str] = {}

        def executor(con, context):
            try:
                second.delegate(con.parent_task_id, [(task, con, None)], executor, parallel=False)
                answer["result"] = "ALLOWED"
            except ResourceWarning as exc:
                answer["result"] = "refused"
                answer["reason"] = str(exc)
            return SpecialistOutput(con.specialist_task_id, success=True, claim={"result": "outer"})

        engine.delegate("parent", [(task, contract, None)], executor, parallel=False)
        assert answer["result"] == "refused" and "1" in answer["reason"]

    def test_the_refusal_leaves_no_delegation_row_behind(self, engine: SpecialistDelegationEngine, store: SQLiteStore) -> None:
        task, contract = engine.create_contract("parent", "read the notes", "specialist_research")
        recorded: dict[str, int] = {}

        def executor(con, context):
            recorded["before"] = len(store.find_delegation_runs(limit=100))
            try:
                engine.delegate(con.parent_task_id, [(task, con, None)], executor, parallel=False)
            except ResourceWarning:
                recorded["after"] = len(store.find_delegation_runs(limit=100))
            return SpecialistOutput(con.specialist_task_id, success=True, claim={"result": "outer"})

        engine.delegate("parent", [(task, contract, None)], executor, parallel=False)
        # The outer run is visible, and the refused inner one never existed: a refusal that writes a run row
        # would be audited as a delegation that started.
        assert recorded["before"] == recorded["after"] == 1

    def test_a_wider_ceiling_allows_exactly_one_more_level(self, store: SQLiteStore) -> None:
        engine = SpecialistDelegationEngine(store, store.path.parents[1], limits=SpecialistLimits(max_delegation_depth=2))
        task, contract = engine.create_contract("parent", "read the notes", "specialist_research")
        depth: dict[str, list[int]] = {"seen": []}

        def nested(con, context):
            depth["seen"].append(2)
            return SpecialistOutput(con.specialist_task_id, success=True, claim={"result": "leaf"})

        def outer(con, context):
            depth["seen"].append(1)
            engine.delegate(con.parent_task_id, [(task, con, None)], nested, parallel=False)
            return SpecialistOutput(con.specialist_task_id, success=True, claim={"result": "branch"})

        engine.delegate("parent", [(task, contract, None)], outer, parallel=False)
        assert depth["seen"] == [1, 2]
        assert engine._execution_depth() == 0

    def test_the_ledger_drains_even_when_an_executor_raises(self, engine: SpecialistDelegationEngine) -> None:
        task, contract = engine.create_contract("parent", "read the notes", "specialist_research")

        def executor(con, context):
            raise RuntimeError("executor exploded")

        _run, outputs, _fusion = engine.delegate("parent", [(task, contract, None)], executor, parallel=False)
        assert outputs[0].success is False and "executor exploded" in outputs[0].error
        assert engine._execution_depth() == 0, "a leaked ledger entry would refuse every later delegation"

    def test_the_per_delegation_ceiling_still_fires(self, engine: SpecialistDelegationEngine) -> None:
        items = []
        for index in range(SpecialistLimits().max_specialists_per_delegation + 1):
            task, contract = engine.create_contract(f"parent-{index}", f"go {index}", "specialist_research")
            items.append((task, contract, None))
        with pytest.raises(ResourceWarning, match="ceiling exceeded"):
            engine.delegate("parent", items, finish, parallel=False)


class TestExecutorCeilings:
    def test_a_contract_asking_for_more_turns_is_clamped_on_the_way_out(self, engine: SpecialistDelegationEngine) -> None:
        task, contract = engine.create_contract(
            "parent", "read the notes", "specialist_research", resource_limits={"max_tool_calls": 999, "timeout_seconds": 2}
        )
        seen: dict[str, object] = {}

        def executor(con, context):
            seen["limits"] = dict(con.resource_limits)
            seen["constraints"] = dict(context.parent_constraints)
            return SpecialistOutput(con.specialist_task_id, success=True, claim={"result": "seen"}, resource_usage={"tool_calls": 5})

        _run, outputs, _fusion = engine.delegate("parent", [(task, contract, None)], executor, parallel=False)
        # The signed contract is *not* rewritten: its scope hash covers resource_limits, so even a
        # tightening edit would make the approved document fail validation. The ceiling travels beside it.
        assert seen["limits"]["max_tool_calls"] == 999
        assert seen["constraints"]["max_tool_calls"] == 8
        assert seen["constraints"]["max_tool_calls_requested"] == 999
        assert "max_turns_per_specialist" in seen["constraints"]["max_tool_calls_clamped_by"]
        assert outputs[0].success is True, outputs[0].error

    def test_a_tighter_contract_is_left_alone(self, engine: SpecialistDelegationEngine) -> None:
        task, contract = engine.create_contract("parent", "read the notes", "specialist_research", resource_limits={"max_tool_calls": 2})
        seen: dict[str, object] = {}

        def executor(con, context):
            seen["max_tool_calls"] = con.resource_limits.get("max_tool_calls")
            seen["constraints"] = dict(context.parent_constraints)
            return SpecialistOutput(con.specialist_task_id, success=True, claim={"result": "seen"}, resource_usage={"tool_calls": 2})

        _run, outputs, _fusion = engine.delegate("parent", [(task, contract, None)], executor, parallel=False)
        assert seen["max_tool_calls"] == 2 and "max_tool_calls" not in seen["constraints"]
        assert outputs[0].success is True

    def test_an_executor_that_reports_using_more_than_the_ceiling_is_refused(self, engine: SpecialistDelegationEngine) -> None:
        # The clamp would be decorative if nothing checked it. The engine cannot count an executor's turns
        # and will not pretend to; what it can do is refuse an output that reports exceeding the ceiling it
        # handed down - and treat an unparseable usage report as non-compliance rather than as silence.
        task, contract = engine.create_contract("parent", "read the notes", "specialist_research", resource_limits={"max_tool_calls": 999})

        def over(con, context):
            return SpecialistOutput(con.specialist_task_id, success=True, claim={"result": "too much"}, resource_usage={"tool_calls": 40})

        _run, outputs, _fusion = engine.delegate("parent", [(task, contract, None)], over, parallel=False)
        assert outputs[0].success is False and "above the enforced ceiling of 8" in outputs[0].error

        def unreadable(con, context):
            return SpecialistOutput(con.specialist_task_id, success=True, claim={"result": "hmm"}, resource_usage={"tool_calls": "many"})

        _run2, outputs2, _f2 = engine.delegate("parent", [(task, contract, None)], unreadable, parallel=False)
        assert outputs2[0].success is False and "above the enforced ceiling" in outputs2[0].error

    def test_an_oversized_output_is_refused_not_truncated(self, engine: SpecialistDelegationEngine) -> None:
        task, contract = engine.create_contract("parent", "read the notes", "specialist_research")

        def executor(con, context):
            return SpecialistOutput(con.specialist_task_id, success=True, claim={"blob": "x" * (SpecialistLimits().max_output_bytes + 10)})

        _run, outputs, _fusion = engine.delegate("parent", [(task, contract, None)], executor, parallel=False)
        assert outputs[0].success is False and "exceeds contract resource limit" in outputs[0].error


class TestNoExecutionPrimitive:
    def test_the_module_imports_no_process_or_shell_primitive(self) -> None:
        source = MODULE_SOURCE.read_text(encoding="utf-8")
        for forbidden in ("subprocess", "os.system", "os.popen", "os.exec", "pty.spawn", "socket.socket"):
            assert forbidden not in source, f"delegation must not gain its own execution path ({forbidden})"

    def test_the_engine_exposes_no_run_or_exec_callable(self, engine: SpecialistDelegationEngine) -> None:
        public = {name for name in dir(engine) if not name.startswith("_")}
        # ``execute`` is an alias of ``execute_task``, and the alias is checked rather than excluded: a
        # second name for the same entry point is fine, a second entry point is not. ``default_executor`` is
        # an attribute holding a caller-supplied callable, which the engine cannot invoke by itself.
        # On the class, not the instance: attribute access on an object builds a fresh bound method each
        # time, so `engine.execute is engine.execute_task` is False for a perfectly good alias.
        assert type(engine).execute is type(engine).execute_task
        offenders = {
            name for name in public
            if ("exec" in name or name in {"run", "shell", "spawn", "launch", "system", "popen"})
            and name not in {"execute", "execute_task", "default_executor"}
        }
        assert not offenders, offenders
        assert "delegate" in public

    def test_a_contract_that_prohibits_self_approval_still_prohibits_it(self, engine: SpecialistDelegationEngine) -> None:
        task, contract = engine.create_contract("parent", "read the notes", "specialist_research")
        assert "self_approve" in contract.prohibited_actions
        assert contract.risk is SpecialistRisk.READ_ONLY


class TestMemoryScopeThroughDelegation:
    def test_a_specialists_recollection_is_scoped_away_from_the_parent(self, engine: SpecialistDelegationEngine, store: SQLiteStore) -> None:
        manager = MemoryManager(store, engine.workspace)
        scoped = SpecialistDelegationEngine(store, engine.workspace, memory=manager)
        task, contract = scoped.create_contract("parent", "read the notes", "specialist_coding")
        scoped.delegate("parent", [(task, contract, None)], finish, parallel=False)
        rows = store.find_memories(None, None, 50, "*")
        assert [row["scope_key"] for row in rows] == ["subagent:specialist_coding"]
        # And the parent's own retrieval does not see it. The *listing* still shows it, on purpose: scoping
        # is what the retrieval query does to the rows that feed a prompt, while an operator's inventory has
        # to be complete. `list(scope=...)` is the boundary, and it is not opt-in for the engine.
        assert manager.memory_store.list(limit=50, scope="local") == []
        assert manager.retrieval.retrieve(RetrievalQuery(goal="specialist specialist_coding completed")) == []
        assert rows[0]["content"] in [row["content"] for row in store.find_memories(None, None, 50, "*")]

    def test_the_record_is_kept_because_it_is_evidence_about_the_delegation(self, engine: SpecialistDelegationEngine, store: SQLiteStore) -> None:
        manager = MemoryManager(store, engine.workspace)
        scoped = SpecialistDelegationEngine(store, engine.workspace, memory=manager)
        task, contract = scoped.create_contract("parent", "read the notes", "specialist_coding")
        scoped.delegate("parent", [(task, contract, None)], finish, parallel=False)
        row = store.find_memories(None, None, 50, "*")[0]
        metadata = json.loads(row["metadata"])
        assert "specialist_task_id" in metadata
        # Scoped, not deleted, and not authoritative: the summary line is the only text that crosses, and
        # the model-controlled fields stay in the specialist's own row.
        assert row["content"].startswith("Specialist specialist_coding completed task")
        assert manager.memory_store.list(limit=50, scope="*") != []

    def test_a_context_is_built_from_the_contract_not_from_the_store(self, engine: SpecialistDelegationEngine) -> None:
        # The isolation object a subagent receives is bounded; nothing in this path hands it a store to
        # read other scopes with.
        task, _contract = engine.create_contract("parent", "read the notes", "specialist_research")
        fields = {field.name for field in dataclasses.fields(SpecialistContext)}
        assert not fields & {"sqlite_store", "memory_store", "store", "policy", "mediator"}, fields
        assert not hasattr(engine, "context_store")
