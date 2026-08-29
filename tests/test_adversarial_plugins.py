"""Adversarial plugins: the fixtures, and what each one is there to prove (07 :140, 05 §2.2).

The requirement is not "plugins work". It is that a plugin cannot gain authority - not by claiming it, not by
returning a friendlier verdict, not by raising, not by abstaining, and not by naming a file it would like to
be imported. So each fixture is one attack, and the assertions are about the *mechanism* that stops it:

* ``tighten_ok`` - the only behaviour a plugin is allowed to have, proven against the real ``Verifier``.
* ``loosen_verdict`` - returns success for a step the built-ins failed. ``Verifier._finish`` short-circuits
  only toward failure, so the attempt is recorded and ignored.
* ``raises`` / ``abstains`` - a broken plugin is a *failed* check, never a skipped one; ambiguity is not
  agreement.
* ``claims_authority`` - refused at registration, before anything could run it.

The inventory itself refuses the entry-point moves: a path outside the allow-listed roots, a ``..`` escape,
a name that collides with a built-in, an unattributed source, and executable-code registration (deferred per
:189 with the deferral reason quoted rather than paraphrased).
"""

from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path

import pytest

from evo_agent.models import PlanStep, ToolResult
from evo_agent.plugins import (
    CODE_REGISTRATION_REFUSAL,
    PluginInventory,
    PluginKind,
    PluginLifecycle,
    PluginRecord,
)
from evo_agent.security import SecurityPolicy
from evo_agent.verifier import Verifier

FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "plugins"
ROOT = Path(__file__).resolve().parents[1]


def fixture(name: str):
    if str(FIXTURE_ROOT) not in sys.path:  # the *test* imports these; the inventory has no importer
        sys.path.insert(0, str(FIXTURE_ROOT))
    return importlib.import_module(name)


def step(description: str = "list the files") -> PlanStep:
    return PlanStep(step_id="step-1", description=description, tool_name="workspace_list", arguments={})


def ok_result() -> ToolResult:
    return ToolResult(call_id="call-1", tool_name="workspace_list", success=True, output="note.txt")


def failed_result() -> ToolResult:
    return ToolResult(call_id="call-1", tool_name="workspace_write", success=False, error="sandbox refused")


def registration(name: str, **overrides) -> dict:
    return {"name": name, "kind": "verification", "source": "tests/fixtures/plugins", "entry_point": f"tests/fixtures/plugins/{name}.py", **overrides}


class TestFixturesExist:
    def test_the_documented_fixtures_are_all_present(self) -> None:
        # The gate checks these names by hand, so a fixture deleted as "unused" would quietly reduce the
        # coverage the phase's acceptance criterion claims.
        for name in ("tighten_ok", "loosen_verdict", "raises", "abstains", "claims_authority"):
            assert (FIXTURE_ROOT / f"{name}.py").is_file(), name
        assert (FIXTURE_ROOT / "__init__.py").is_file()

    def test_each_fixture_declares_its_attack(self) -> None:
        assert "passed" in fixture("tighten_ok").assess({"step": {"description": "anything"}}, None, ()).keys()
        assert fixture("loosen_verdict").CLAIMS == ("override_verdict",)
        with pytest.raises(RuntimeError):
            fixture("raises").assess({}, None, ())
        assert fixture("abstains").assess({}, None, ())["passed"] is None
        assert set(fixture("claims_authority").CLAIMS) == {"auto_approve", "grant_permission"}


class TestRegistrationRefusals:
    def test_an_authority_claim_is_refused_with_the_rule_not_a_clamp(self) -> None:
        inventory = PluginInventory()
        record, problems = inventory.register(registration("boss", claims=list(fixture("loosen_verdict").CLAIMS)))
        assert record is None and len(problems) == 1
        assert "claims authority it cannot have" in problems[0]
        assert "override_verdict" in problems[0] and PluginInventory.TIGHTEN_ONLY in problems[0]
        # Not half-registered, and not registered as "quarantined" either: nothing is in the inventory.
        assert inventory.list() == []

    def test_the_authority_vocabulary_is_refused_whatever_kind_it_is_worn_as(self) -> None:
        inventory = PluginInventory()
        for claim in ("auto_approve", "grant_permission", "skip_verification", "writes_governance", "self_register", "bypass_approval"):
            _record, problems = inventory.register(registration(f"x-{claim}", claims=[claim]))
            assert problems and "authority" in problems[0], claim

    def test_executable_code_is_refused_with_the_verbatim_deferral(self) -> None:
        inventory = PluginInventory()
        record, problems = inventory.register({**registration("loaded"), "kind": "executable_code"})
        assert record is None
        assert problems[0].endswith(CODE_REGISTRATION_REFUSAL) or CODE_REGISTRATION_REFUSAL in problems[0]
        assert "2.1" in problems[0] and "plugin-isolation" in problems[0]

    def test_sovereign_is_not_a_root_and_cannot_be_reached_by_escaping(self) -> None:
        inventory = PluginInventory()
        for entry_point in ("evo_agent/sovereign/protected.py", "plugins/../../evo_agent/sovereign/x.py", "sovereign/tools.py"):
            record, problems = inventory.register({**registration("pather"), "kind": "hook", "entry_point": entry_point})
            assert record is None, entry_point
            assert "sovereign" in problems[0] or "escapes its root" in problems[0], problems

    def test_a_plugin_may_not_take_over_a_name_the_build_answers_to(self) -> None:
        inventory = PluginInventory(builtins=("shell", "workspace_write"))
        record, problems = inventory.register({**registration("shim"), "kind": "hook", "provides": ["shell"]})
        assert record is None and "already answers to" in problems[0]
        # An extension may add a name: the same registration under a new capability is accepted.
        accepted, second = inventory.register({**registration("shim"), "kind": "hook", "provides": ["danger-check"]})
        assert accepted is not None and second == []

    def test_an_unattributed_or_nameless_or_opaque_registration_is_refused(self) -> None:
        inventory = PluginInventory()
        assert inventory.register({**registration("ghost"), "source": ""})[1]
        assert inventory.register({**registration("  ")})[1]
        assert inventory.register({**registration("bad/name")})[1]
        record, problems = inventory.register(object())
        assert record is None and "cannot be reviewed" in problems[0]

    def test_reregistration_with_different_content_is_a_conflict_not_an_overwrite(self) -> None:
        inventory = PluginInventory()
        first, _problems = inventory.register(registration("dup"))
        again, problems = inventory.register({**registration("dup"), "entry_point": "plugins/other.py"})
        assert again is None and "already registered with different content" in problems[0]
        # Identical content is not a conflict: restarts must not become errors people learn to silence.
        same, second = inventory.register(registration("dup"))
        assert same is not None and second == [] and same.digest == first.digest

    def test_register_many_separates_accepted_from_refused(self) -> None:
        inventory = PluginInventory()
        report = inventory.register_many([registration("good"), {**registration("evil"), "claims": ["override_verdict"]}])
        assert report["ok"] is False and report["accepted"] == ["good"] and list(report["refused"]) == ["evil"]
        assert json.dumps(report)


class TestLifecycle:
    def test_activation_needs_another_identity_and_a_non_read_only_phase(self, tmp_path: Path) -> None:
        inventory = PluginInventory()
        inventory.register(registration("selfish"))
        assert inventory.activate("nosuch")[1].startswith("'nosuch' is not registered")
        allowed, reason = inventory.activate("selfish")
        assert allowed is False and "approving operator identity" in reason
        assert "registration is a claim, activation is a decision" in reason
        assert inventory.activate("selfish", approved_by="selfish")[0] is False
        assert inventory.activate("selfish", approved_by="operator")[0] is True

    def test_plan_mode_refuses_activation_and_binding(self, tmp_path: Path) -> None:
        policy = SecurityPolicy(tmp_path, agent_mode="plan")
        inventory = PluginInventory(policy=policy)
        inventory.register(registration("hook-one", kind="hook"))
        assert inventory.activate("hook-one", approved_by="operator")[0] is False
        assert "read-only phase" in inventory.activate("hook-one", approved_by="operator")[1]
        assert inventory.bind("hook-one", approved_by="operator", handler=lambda payload: None)[0] is False

    def test_quarantine_and_retirement_are_not_revivable(self) -> None:
        inventory = PluginInventory()
        inventory.register(registration("bad"))
        inventory.activate("bad", approved_by="operator")
        assert inventory.quarantine("bad", reason="flagged") is True
        assert inventory.activate("bad", approved_by="operator")[0] is False
        assert inventory.retire("bad", reason="removed") is True
        assert inventory.activate("bad", approved_by="operator")[1].startswith("'bad' is retired")
        assert [item.lifecycle for item in inventory.list()] == [PluginLifecycle.RETIRED]

    def test_only_active_verification_plugins_reach_the_verifier(self) -> None:
        inventory = PluginInventory()
        inventory.register(registration("candidate-only"))
        assert inventory.verification_plugins() == ()
        inventory.activate("candidate-only", approved_by="operator")
        assert [item.name for item in inventory.verification_plugins()] == ["candidate-only"]
        # A hook is never handed to the verifier as a check, even when active.
        inventory.register({**registration("watcher"), "kind": "hook"})
        inventory.activate("watcher", approved_by="operator")
        assert "watcher" not in [item.name for item in inventory.verification_plugins()]
        assert [item.name for item in inventory.hooks()] == ["watcher"]


class TestVerifierIntegration:
    def _verifier(self, plugin_names: list[str], tmp_path: Path, *, bind: dict | None = None) -> tuple[Verifier, PluginInventory]:
        inventory = PluginInventory()
        for name in plugin_names:
            inventory.register(registration(name))
        for name, callable_ in (bind or {}).items():
            inventory.bind(name, approved_by="operator", assess=callable_)
            inventory.activate(name, approved_by="operator")
        return Verifier(policy=SecurityPolicy(tmp_path), plugins=inventory.verification_plugins()), inventory

    def test_a_bound_plugin_can_only_make_a_step_fail(self, tmp_path: Path) -> None:
        verifier, _inventory = self._verifier(["tighten"], tmp_path, bind={"tighten": fixture("tighten_ok").assess})
        assert verifier.verify(step("list the files"), ok_result()).success is True
        verdict = verifier.verify(step("do the danger thing"), ok_result())
        assert verdict.success is False and "advisory check 'tighten'" in verdict.summary

    def test_a_rescuing_plugin_cannot_pass_a_failed_step(self, tmp_path: Path) -> None:
        # The fixture returns {"passed": True} for a step the built-ins already failed. `_finish` never
        # returns a *passing* result from a plugin verdict, so the attempt is inert - and it is still in the
        # checks list, because a reviewer has to be able to see that someone tried.
        verdict, _inventory = self._verifier(["loosen"], tmp_path, bind={"loosen": fixture("loosen_verdict").assess})
        result = verdict.verify(step(), failed_result())
        assert result.success is False
        assert any(item["name"] == "tool_success" and item["passed"] is False for item in result.checks)

    def test_a_raising_plugin_is_a_failed_check_not_a_skipped_one(self, tmp_path: Path) -> None:
        verdict, _inventory = self._verifier(["raises"], tmp_path, bind={"raises": fixture("raises").assess})
        result = verdict.verify(step(), ok_result())
        assert result.success is False and "raised RuntimeError" in json.dumps([dict(item) for item in result.checks])

    def test_an_abstaining_plugin_is_not_read_as_agreement(self, tmp_path: Path) -> None:
        # `assess` returns passed=None. Through the inventory the non-boolean is a failure; the verifier's
        # own `_consult` records whatever the plugin said but can only ever tighten - both directions agree
        # that "maybe" does not pass a step.
        verdict, inventory = self._verifier(["abstains"], tmp_path, bind={"abstains": fixture("abstains").assess})
        assert verdict.verify(step(), ok_result()).success is False
        direct = inventory.assess("abstains", {"step": {}})
        assert direct["passed"] is False and "not a verdict" in direct["detail"]

    def test_an_unbound_active_plugin_refuses_rather_than_lying_quietly(self, tmp_path: Path) -> None:
        inventory = PluginInventory()
        inventory.register(registration("unbound"))
        inventory.activate("unbound", approved_by="operator")
        result = Verifier(policy=SecurityPolicy(tmp_path), plugins=inventory.verification_plugins()).verify(step(), ok_result())
        assert result.success is False
        # The summary names the plugin; the *detail* says why, and it is in the checks list rather than only
        # in a log line, because the checks list is what an audit reader is handed.
        assert "advisory check 'unbound'" in result.summary
        assert any("registered but not bound" in str(item.get("detail", "")) for item in result.checks)

    def test_a_registered_plugin_is_not_a_bound_one(self, tmp_path: Path) -> None:
        # `bind` refuses to import, so a config entry alone can never become code, and the refusal to bind
        # without an approver is the same rule as activation, stated at the other door.
        inventory = PluginInventory()
        inventory.register(registration("needy"))
        assert inventory.bind("needy", assess=lambda *args: {"passed": True})[0] is False
        assert inventory.bind("needy", approved_by="operator")[0] is False
        assert inventory.bind("nosuch", approved_by="operator", handler=lambda payload: None)[0] is False

    def test_executable_code_cannot_be_bound_even_with_an_approver(self) -> None:
        inventory = PluginInventory()
        # Registration itself refuses, which is the point: there is no record to bind.
        record, _problems = inventory.register({**registration("loaded"), "kind": "executable_code"})
        assert record is None
        assert inventory.bind("loaded", approved_by="operator", assess=lambda *args: {}) == (
            False,
            "'loaded' is not registered; binding is not a way to install",
        )


class TestHookDispatch:
    def test_dispatch_is_side_effects_only_and_failures_are_recorded(self) -> None:
        inventory = PluginInventory()
        inventory.register({**registration("watcher"), "kind": "hook"})
        assert "only active hooks are dispatched" in inventory.dispatch_hook("watcher", {"event": "turn_finished"})["refusal"]
        inventory.activate("watcher", approved_by="operator")
        seen: list[dict] = []
        inventory.bind("watcher", approved_by="operator", handler=seen.append)
        assert inventory.dispatch_hook("watcher", {"event": "turn_finished"}) == {"ok": True, "name": "watcher"}
        assert seen == [{"event": "turn_finished"}]
        assert inventory.dispatch_hook("nosuch", {})["refusal"].startswith("hook 'nosuch' is not registered")

    def test_a_failing_handler_does_not_escape_the_event_path(self) -> None:
        inventory = PluginInventory()
        inventory.register({**registration("crashy"), "kind": "hook"})
        inventory.activate("crashy", approved_by="operator")
        inventory.bind("crashy", approved_by="operator", handler=lambda payload: 1 / 0)
        answer = inventory.dispatch_hook("crashy", {})
        assert answer["ok"] is True and "ZeroDivisionError" in answer["handler_failed"]
        # Recorded, not propagated: a hook that can raise can stop a turn, which is authority by another
        # name. `ok` here means "the event path survived", and the failure sits next to it.

    def test_a_handler_cannot_change_a_verdict_through_the_hook(self) -> None:
        # The hook returns a dict and the inventory throws it away: the return value of a hook is not read.
        inventory = PluginInventory()
        inventory.register({**registration("meddler"), "kind": "hook"})
        inventory.activate("meddler", approved_by="operator")
        inventory.bind("meddler", approved_by="operator", handler=lambda payload: {"passed": True, "approve": True})
        assert inventory.dispatch_hook("meddler", {}) == {"ok": True, "name": "meddler"}


class TestReporting:
    def test_the_report_states_the_deferral_and_the_enforcement_location(self) -> None:
        inventory = PluginInventory()
        inventory.register(registration("reported"))
        payload = inventory.report()
        assert payload["dynamic_import"] == "refused"
        assert "verifier.py::_finish" in payload["tighten_only"]
        assert "override_verdict" in payload["authority_claims_refused"]
        assert "plugins/" in payload["allowed_entry_roots"]
        assert payload["records"][0]["digest"] and payload["counts"]["candidate"] == 1
        assert json.dumps(payload)

    def test_tighten_only_is_a_pointer_not_a_promise(self) -> None:
        # The inventory quotes the verifier's own message instead of restating the rule, so what is asserted
        # is that the quoted location still exists: `evo_agent/verifier.py::_finish`, containing the
        # tighten-only short-circuit. If that logic ever moves, this fails rather than leaving a docstring
        # pointing at a fiction.
        message = PluginInventory.TIGHTEN_ONLY
        assert "evo_agent/verifier.py::_finish" in message
        source = (ROOT / "evo_agent" / "verifier.py").read_text(encoding="utf-8")
        assert "def _finish" in source and "A plugin may only tighten" in source
        assert "advisory check" in source

    def test_record_to_dict_is_json_safe_and_carries_no_callable(self) -> None:
        inventory = PluginInventory()
        record, _problems = inventory.register(registration("serial"))
        payload = record.to_dict()
        assert json.dumps(payload)
        assert "metadata" not in payload and "assess" not in payload
        assert PluginRecord(**{**payload, "metadata": {}}).digest == record.digest
