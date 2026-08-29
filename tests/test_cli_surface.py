"""Every CLI report flag has to be in the inspection gate, or it becomes a request that is silently ignored.

This file exists because of a concrete regression: adding ``--agent-mode`` to ``inspect_command``'s
gate expression by *replacing* its tail clause dropped ``args.show_profile`` from it. Every assertion in
``tests/test_personal_profile.py`` that ran the CLI still passed - it checked the JSON payload of a
subprocess - except the one that checked the exit code, and that one failed with ``2`` and the text
``Provide a goal, for example``. The bug's signature is that a flag stops being a command: the parser still
accepts it, the help still lists it, and the run path treats the invocation as a goal request.

So the property is checked structurally rather than verb by verb. ``inspect_command`` decides with one
boolean expression over ``args``; every ``store_true`` flag is a command, so every ``store_true`` flag must
appear in that expression. A new verb that is not in the gate is a new verb that does nothing, and that is
exactly the kind of thing no one notices until an operator types it.
"""

from __future__ import annotations

import inspect
import json
from argparse import SUPPRESS
from pathlib import Path

import pytest

from evo_agent import cli


def _gate_expression() -> str:
    source = inspect.getsource(cli.inspect_command)
    lines = [line for line in source.splitlines() if line.strip()]
    # The gate is a single logical line: `if not (args.a or args.b or ...):` followed by `return False`.
    for line in lines:
        if line.strip().startswith("if not ("):
            return line
    raise AssertionError("inspect_command no longer decides with a single gate expression")


#: Flags that change *how* a recognised command behaves, so they are legitimately absent from the gate.
#: Kept as data with a named companion rather than as a `not in (...)` ignore: the assertion below requires
#: each companion to be a command itself, which is what makes this list auditable instead of convenient.
_MODIFIERS = {
    "approve_unbound": "approve_promotion",
    "retain_sandbox": "sandbox_proposal",
    "production_backup": "production_run",
    "runtime_approval": "runtime_start",
}


def _store_true_dests() -> list[str]:
    parser = cli.build_parser()
    return [action.dest for action in parser._actions if action.dest != "help" and action.const is True and action.default is False]


class TestInspectionGate:
    def test_a_flag_the_inspection_body_acts_on_is_recognised_by_the_gate(self) -> None:
        # The precise rule that catches the regression. ``inspect_command`` opens with one gate expression
        # and then dispatches; a flag the body reads but the gate does not mention is *unreachable code* -
        # which is what ``--show-profile`` became, and which looks from the parser's side like a working
        # verb. Unreachable dispatch is the failure mode, not merely an unwired flag.
        gate = _gate_expression()
        body = inspect.getsource(cli.inspect_command)
        mentioned = {dest for dest in _store_true_dests() if f"args.{dest}" in body}
        orphans = sorted(dest for dest in mentioned if f"args.{dest}" not in gate and dest not in _MODIFIERS)
        assert not orphans, f"dispatched inside inspect_command but not recognised as a command: {orphans}"
        # And a modifier is only exempt while the command it modifies is itself recognised: `--json` beside no
        # command is a no-op, and this is the half that stops the allow-list from becoming an escape hatch.
        for dest, companion in _MODIFIERS.items():
            assert f"args.{companion}" in gate, f"{dest} modifies {companion}, which is not a command either"

    def test_every_flag_is_read_by_something(self) -> None:
        # The companion rule: a flag accepted by the parser and read by no code at all is a configuration an
        # operator believes they set. Checked over the whole module, because the legitimate homes differ -
        # `--json` and `--legacy-kernel` modify a run and belong to `main`, while `--skill-name` is consumed
        # two frames inside the inspection path.
        body = inspect.getsource(cli)
        unread = []
        for action in cli.build_parser()._actions:
            if action.dest in {"help", "request"}:
                continue
            if f"args.{action.dest}" not in body:
                unread.append(action.dest)
        assert not unread, f"flags no code reads: {unread}"

    def test_the_gate_is_one_expression_over_args_not_a_scattered_check(self) -> None:
        # Asserted because the property above only holds while the decision is in one place. Splitting it
        # across helpers would let a flag slip in exactly the way the regression did.
        gate = _gate_expression()
        assert gate.count("if not (") == 1 and " or args." in gate
        assert "args." in gate

    def test_the_p5_verbs_are_present_and_documented(self) -> None:
        parser = cli.build_parser()
        actions = {action.dest: action for action in parser._actions}
        for dest in ("agent_mode", "skills_list", "skill_install", "skill_show", "skill_name"):
            assert dest in actions, dest
            assert actions[dest].help not in (None, "", SUPPRESS), dest
        assert actions["agent_mode"].choices == ("build", "plan")
        assert actions["skill_name"].default is None, "a skill name is derived from the directory unless given"

    def test_the_new_flags_do_not_widen_anything(self) -> None:
        # `--agent-mode` accepts exactly the two modes, and there is no `--agent-mode auto`: a mode the
        # agent could pick for itself is not a phase restriction (06 §3.6).
        with pytest.raises(SystemExit) as exitinfo:
            parser = cli.build_parser()
            parser.parse_args(["--workspace", "/tmp/whatever", "--agent-mode", "auto"])
        assert exitinfo.value.code == 2


class TestPolicyApplication:
    def test_the_mode_is_applied_to_a_built_policy_and_normalised(self, tmp_path: Path) -> None:
        from evo_agent.security import SecurityPolicy

        policy = SecurityPolicy(tmp_path, agent_mode="build")
        assert cli._with_agent_mode(policy, "plan").agent_mode == "plan"  # noqa: SLF001 - the wiring is the unit
        assert cli._with_agent_mode(policy, "plna").agent_mode == "plan"  # noqa: SLF001
        assert cli._with_agent_mode(policy, "build") is policy, "no rebuild when nothing changes"
        # The original is untouched: the profile's policy object is shared with other consumers, and a
        # mutation would leak the phase into a path that was never asked to be read-only.
        assert policy.agent_mode == "build"

    def test_a_refused_startup_still_exits_nonzero_after_the_gate_change(self, tmp_path: Path, monkeypatch, capsys) -> None:
        import sys

        config = tmp_path / "memory.json"
        config.write_text(json.dumps({"retrieval_weights": {"topic_relevance": 5000}}), encoding="utf-8")
        monkeypatch.setattr(sys, "argv", ["evo", "run", "--workspace", str(tmp_path), "--show-profile", "--memory-config", str(config)])
        with pytest.raises(SystemExit) as exitinfo:
            cli.main()
        assert exitinfo.value.code == 1
        assert "memory policy is not valid" in capsys.readouterr().out
