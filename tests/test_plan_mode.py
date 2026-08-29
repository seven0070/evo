"""Plan mode: the read-only phase that approval cannot talk out of its mind (07 :141, :358 Q4).

The requirement identified from the spec is narrower and harder than "ask before writing". A read-only phase
is only worth having if *nothing* can widen it from inside the loop, which means the refusal has to be
enforced at the one choke point every tool crosses (``ApprovalMediator._decide``), folded into what the model
is offered (so the agent does not spend a turn on a call that was never possible), and applied to the
operator-facing verbs that change state (skill staging, promotion). The failure mode it defends against is
subtle: if plan mode were expressed as an approval question, then "approve everything" - which operators turn
on for real work - would silently turn plan mode off, and the phase would become advisory.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from evo_agent.models import ToolCall
from evo_agent.ports.contracts import ExecRequest
from evo_agent.modes import (
    PLAN_FORBIDDEN_PERMISSIONS,
    PLAN_FORBIDDEN_RISKS,
    PLAN_FORBIDDEN_TOOLS,
    AgentMode,
    ModeReport,
    is_plan_mode,
    refuses_in_plan_mode,
    report as mode_report,
)
from evo_agent.security import SecurityPolicy
from evo_agent.sovereign.mediation import ApprovalMediator
from evo_agent.tools import ToolCatalog, ToolRegistry


def call(tool: str, **arguments) -> ToolCall:
    return ToolCall(call_id="call-1", task_id="task-1", step_id="step-1", tool_name=tool, arguments=arguments)


class TestModeParsing:
    def test_unrecognised_values_resolve_to_plan_not_build(self) -> None:
        # The default on garbage is the *smaller* privilege. A typo in `--agent-mode plna` must not read as
        # "run with write access"; the same reasoning makes an unknown sandbox level resolve to `strict`.
        assert AgentMode.parse("plan") is AgentMode.PLAN
        assert AgentMode.parse("BUILD") is AgentMode.BUILD
        for garbage in ("plna", "", None, "read-only", "yolo"):
            assert AgentMode.parse(garbage) is AgentMode.PLAN, garbage

    def test_policy_normalises_and_reports_the_mode(self, tmp_path: Path) -> None:
        assert SecurityPolicy(tmp_path, agent_mode="plan").agent_mode == "plan"
        assert SecurityPolicy(tmp_path, agent_mode="whatever").agent_mode == "plan"
        assert SecurityPolicy(tmp_path).agent_mode == "build"
        # The mode is part of the policy's own report, because every consumer of confinement reads that
        # dict - a mode visible only in Python would be missing from the audit trail operators actually
        # compare between runs.
        assert SecurityPolicy(tmp_path, agent_mode="plan").to_dict()["agent_mode"] == "plan"

    def test_is_plan_mode_tolerates_a_missing_field(self) -> None:
        # The helper is called with objects that are not policies at all (a mediator built without one, a
        # partially-deserialised dict), and "no policy" must mean "not plan mode" rather than an exception
        # inside the refusal logic - the exception path is how a guard becomes a bypass.
        assert is_plan_mode(None) is False

        class Bare:
            pass

        assert is_plan_mode(Bare()) is False
        assert is_plan_mode(type("Pol", (), {"agent_mode": "plan"})()) is True


class TestRefusalClassification:
    def test_the_forbidden_sets_are_the_documented_ones(self) -> None:
        assert {"shell", "workspace_write", "process_spawn", "file_delete", "apply_patch"} == set(PLAN_FORBIDDEN_TOOLS)
        # `medium` is deliberately absent. The risk leg refuses what *no* operator would auto-approve, and
        # the shipped write tools are named anyway; adding medium here would turn the read-only phase into a
        # "nothing but workspace_list" mode for deployments whose reads are classified medium.
        assert set(PLAN_FORBIDDEN_RISKS) == {"high", "critical"}
        assert {"write", "delete", "execute", "install"} <= set(PLAN_FORBIDDEN_PERMISSIONS)

    def test_reads_are_never_refused_by_name(self) -> None:
        for name in ("workspace_read", "workspace_list", "read_file", "search"):
            refused, reason = refuses_in_plan_mode(name)
            assert refused is False and reason == "", name

    def test_writes_are_refused_by_risk_or_permission_even_when_unnamed(self) -> None:
        assert refuses_in_plan_mode("custom_thing", risk="high")[0] is True
        assert refuses_in_plan_mode("custom_thing", permissions=("file:write",))[0] is True
        # `known=False`: the name is not in any list, carries no risk and no permission. An unclassifiable
        # request is refused, because "I could not tell" must not be the answer that lets a write through.
        refused, reason = refuses_in_plan_mode("custom_thing", known=False)
        assert refused is True and "cannot classify" in reason
        # A name the registry has and that classifies as a read is allowed even when unknown to the lists.
        assert refuses_in_plan_mode("custom_read_thing", risk="low", known=True)[0] is False
        # A refusal never comes back without its reason - an audit line saying only "plan mode" is not an
        # explanation of which of the three legs fired.
        assert all(refuses_in_plan_mode(name)[1] for name in ("shell", "workspace_write", "apply_patch"))

    def test_report_names_the_tools_the_phase_refuses(self, tmp_path: Path) -> None:
        policy = SecurityPolicy(tmp_path, agent_mode="plan")
        registry = ToolRegistry(policy)
        payload = mode_report(policy, mediator=registry.mediator)
        assert set(payload) == {"mode", "enforced", "refusals"}
        assert payload["mode"] == "plan" and payload["enforced"] is True
        assert "shell" in payload["refusals"] and "workspace_write" in payload["refusals"]
        assert "workspace_read" not in payload["refusals"]
        build = mode_report(SecurityPolicy(tmp_path, agent_mode="build"), mediator=ToolRegistry(SecurityPolicy(tmp_path, agent_mode="build")).mediator)
        # `enforced` is the only thing that differs: the refusal *list* is empty in build mode because the
        # phase is not what is deciding there, and a reader must be able to tell the two apart.
        assert build["enforced"] is False and build["refusals"] == []


class TestMediationChokePoint:
    def _registry(self, tmp_path: Path, *, agent_mode: str, approver=None) -> ToolRegistry:
        policy = SecurityPolicy(tmp_path, agent_mode=agent_mode)
        mediator = ApprovalMediator(policy, approver=approver or (lambda *approve_args: True))
        return ToolRegistry(policy, mediator=mediator)

    def test_a_mutating_tool_is_refused_in_plan_mode_even_with_an_approving_turn(self, tmp_path: Path) -> None:
        registry = self._registry(tmp_path, agent_mode="plan", approver=lambda *approve_args: True)
        result = registry.execute(call("shell", command="echo hi"))
        assert result.success is False
        assert "plan_mode" in str(result.error) or "plan mode" in str(result.error)
        assert "changes state" in str(result.error)

    def test_build_mode_still_confines_rather_than_allows(self, tmp_path: Path) -> None:
        registry = self._registry(tmp_path, agent_mode="build", approver=lambda *approve_args: True)
        result = registry.execute(call("shell", command="echo hi"))
        # The phase is off, so whatever happens is the sandbox's own answer - a confined run, or a refusal
        # naming the missing provider. Neither may name the phase: that would mean flipping the mode off
        # changed what the boundary enforces rather than what the phase withholds.
        assert "plan mode" not in str(result.error) and "plan_mode" not in str(result.error)
        assert result.success is True or "isolation" in str(result.error).lower() or "sandbox" in str(result.error).lower()

    def test_reads_survive_the_phase(self, tmp_path: Path) -> None:
        (tmp_path / "note.txt").write_text("kept readable", encoding="utf-8")
        registry = self._registry(tmp_path, agent_mode="plan")
        listing = registry.execute(call("workspace_list"))
        writing = registry.execute(call("workspace_read", path="note.txt"))
        assert listing.success is True
        assert writing.success is True and "kept readable" in str(writing.output)

    def test_the_refusal_is_folded_into_what_the_model_is_offered(self, tmp_path: Path) -> None:
        # Not a fourth boolean on Usability that a caller can forget: the same `permitted` flag the
        # approval gate uses, with the phase named in the reason, so an offered set in plan mode simply
        # excludes the write tools instead of listing tools that cannot run.
        policy = SecurityPolicy(tmp_path, agent_mode="plan")
        registry = ToolRegistry(policy, mediator=ApprovalMediator(policy, approver=lambda *approve_args: True))
        catalog = ToolCatalog(registry)
        offered = {item["function"]["name"] for item in catalog.offered()}
        assert "shell" not in offered and "workspace_write" not in offered
        assert {"workspace_list", "workspace_read"} <= offered
        # The reason is in the usability report, not only in the execution path: a reader of `evo status`
        # has to be able to see that the phase, not the operator's grants, is why the tool is missing.
        usability = catalog.usability("shell")
        assert usability.usable is False and usability.permitted is False
        assert any("plan mode" in text for text in usability.reasons)
        build_policy = SecurityPolicy(tmp_path, agent_mode="build")
        # Same policy fields, only the phase differs, and the *reasons* are what is compared. Asserting on
        # the offered sets alone would be a test that passes for the wrong reason: `shell` is withheld in
        # build mode too, because its high risk needs approval evidence first, so the sets can be equal
        # while the phase does nothing. What must differ is which rule speaks.
        build_catalog = ToolCatalog(ToolRegistry(build_policy, mediator=ApprovalMediator(build_policy, approver=lambda *approve_args: True)))
        build_usability = build_catalog.usability("shell")
        assert not any("plan mode" in text for text in build_usability.reasons), build_usability.reasons
        assert any("approval" in text for text in build_usability.reasons)
        # Reads are untouched by the phase in both modes, which is the half that keeps plan mode usable.
        for name in ("workspace_list", "workspace_read"):
            plan_leg = catalog.usability(name)
            assert plan_leg.permitted is True and plan_leg.usable is True, (name, plan_leg.reasons)
            assert catalog.usability(name).reasons == build_catalog.usability(name).reasons
        assert offered <= {item["function"]["name"] for item in build_catalog.offered()}

    def test_a_mediator_without_a_linked_registry_fails_closed(self, tmp_path: Path) -> None:
        # `mediator.registry` is set by ToolRegistry's constructor. An unlinked mediator sees `known=False`
        # for every name, so it refuses everything mutating rather than everything-but-the-names-it-happens
        # to know - the direction the bug would go in if the link were forgotten.
        policy = SecurityPolicy(tmp_path, agent_mode="plan")
        mediator = ApprovalMediator(policy, approver=lambda *approve_args: True)
        mediator.registry = None
        request = ExecRequest(argv=("/bin/echo", "hi"), cwd=tmp_path)
        decision = mediator.evaluate(request, tool_name="some_unknown_mutator")
        assert decision.allowed is False and decision.rule == "plan_mode"
        assert "plan mode" in decision.reason
        # Same request in build mode is not answered by the phase, proving the rule is the mode and not the
        # mediator's usual suspicion of an unknown tool name.
        build = ApprovalMediator(SecurityPolicy(tmp_path, agent_mode="build"), approver=lambda *approve_args: True)
        build.registry = None
        assert build.evaluate(request, tool_name="some_unknown_mutator").rule != "plan_mode"


class TestSkillStagingRefusal:
    def test_plan_mode_refuses_staging_before_writing_anything(self, tmp_path: Path) -> None:
        from evo_agent.skills import SKILL_FILENAME, SkillInstaller

        source = tmp_path / "src" / "note-style"
        source.mkdir(parents=True)
        (source / SKILL_FILENAME).write_text(
            "---\nname: note-style\ndescription: how to write notes\n---\nKeep them short.\n", encoding="utf-8"
        )
        staging = tmp_path / "staging"
        plan = SkillInstaller(staging, policy=SecurityPolicy(tmp_path, agent_mode="plan"))
        report = plan.stage(source)
        assert report["ok"] is False and any("read-only phase" in item for item in report["refusals"])
        assert not staging.exists() or list(staging.rglob("*")) == [], "plan mode must not leave a half-staged tree"
        build = SkillInstaller(staging, policy=SecurityPolicy(tmp_path, agent_mode="build"))
        assert build.stage(source)["ok"] is True


class TestCommandLineSurface:
    """The mode has to reach the CLI, and a refusal there has to leave the process non-zero."""

    def _run(self, monkeypatch, tmp_path: Path, *extra: str) -> tuple[int, str]:
        import sys

        from evo_agent import cli

        monkeypatch.setattr(sys, "argv", ["evo", "noop", "--workspace", str(tmp_path), *extra])
        try:
            cli.main()
        except SystemExit as exitinfo:
            return int(exitinfo.code or 0), ""
        return 0, ""

    def test_promotion_is_refused_in_plan_mode(self, monkeypatch, tmp_path: Path, capsys) -> None:
        import sys

        from evo_agent import cli

        monkeypatch.setattr(sys, "argv", ["evo", "noop", "--workspace", str(tmp_path), "--agent-mode", "plan", "--promote", "promote-1"])
        with pytest.raises(SystemExit) as exitinfo:
            cli.main()
        assert exitinfo.value.code == 1
        printed = capsys.readouterr().out
        assert "promotion is refused in plan mode" in printed and "advisory" in printed

    def test_skill_staging_refusal_exits_nonzero_in_plan_mode(self, monkeypatch, tmp_path: Path, capsys) -> None:
        import sys

        from evo_agent import cli
        from evo_agent.skills import SKILL_FILENAME

        source = tmp_path / "src" / "note-style"
        source.mkdir(parents=True)
        (source / SKILL_FILENAME).write_text("---\nname: note-style\ndescription: d\n---\nbody\n", encoding="utf-8")
        monkeypatch.setattr(sys, "argv", ["evo", "noop", "--workspace", str(tmp_path), "--agent-mode", "plan", "--skill-install", str(source)])
        with pytest.raises(SystemExit) as exitinfo:
            cli.main()
        assert exitinfo.value.code == 1
        assert "read-only phase" in capsys.readouterr().out

    def test_the_staged_bundle_is_not_activated(self, monkeypatch, tmp_path: Path, capsys) -> None:
        import sys

        from evo_agent import cli
        from evo_agent.skills import SKILL_FILENAME

        source = tmp_path / "src" / "note-style"
        source.mkdir(parents=True)
        (source / SKILL_FILENAME).write_text("---\nname: note-style\ndescription: d\n---\nbody\n", encoding="utf-8")
        monkeypatch.setattr(sys, "argv", ["evo", "noop", "--workspace", str(tmp_path), "--skill-install", str(source)])
        cli.main()
        printed = json.loads(capsys.readouterr().out)
        assert printed["ok"] is True and printed["activated"] is False
        # The inventory says "reviewed"; only the overlay says "in force", and this proves they are separate.
        listing = tmp_path / ".evo" / "skills-active"
        assert not listing.exists()

    def test_an_invalid_agent_mode_is_rejected_by_the_parser(self, monkeypatch, tmp_path: Path) -> None:
        import sys

        from evo_agent import cli

        monkeypatch.setattr(sys, "argv", ["evo", "noop", "--workspace", str(tmp_path), "--agent-mode", "explore"])
        with pytest.raises(SystemExit) as exitinfo:
            cli.main()
        assert exitinfo.value.code == 2
