"""P0 ratchet: the sovereign protected set and the live invariant registry.

These tests are the guard rails the integration phases have to work inside. They are not
documentation of intent - each one can fail, and several are deliberately armed so that a
later phase *must* notice when it fixes something (a tolerated gap that stops offending
fails until the entry is removed in review).
"""

from __future__ import annotations

import ast
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "evo_agent"

from evo_agent.sovereign import (  # noqa: E402
    REGISTRY,
    InvariantConfig,
    InvariantDef,
    InvariantObserver,
    PROTECTED_PATHS,
    compute_digests,
    enforce_invariants,
    file_digest,
    format_report,
    invariant_registry,
    load_manifest,
    run_invariants,
    verify_sovereign_digests,
)
from evo_agent.sovereign.invariants import (  # noqa: E402
    NO_RUNTIME_INVARIANT,
    EXECUTION_SITE_ALLOWLIST,
    LOOP_FORBIDDEN_PACKAGES,
    PERSISTENCE_AUTHORITY_ALLOWLIST,
    TOOL_DISPATCH_LOOP_ALLOWLIST,
    _check_execution_sites,
    _check_import_purity,
    _check_invariant_coverage,
    _check_no_async,
    _check_persistence_authority,
    _check_single_loop,
)
from evo_agent.sovereign.protected import MANIFEST_PATH, SovereignDrift, enforce as enforce_sovereign_digests  # noqa: E402


# --- the protected byte set -----------------------------------------------------------

def test_manifest_is_published_and_covers_the_declared_set():
    published = load_manifest()
    assert published is not None, "run scripts/verify_sovereign_digest.py --write"
    assert set(published) == set(PROTECTED_PATHS), "the manifest and PROTECTED_PATHS must agree exactly"


def test_every_protected_file_exists_and_hashes():
    digests = compute_digests()
    for relative in PROTECTED_PATHS:
        assert (PACKAGE / relative).is_file(), f"protected file {relative} is missing"
        assert digests[relative] == file_digest(PACKAGE / relative)


def test_sovereign_package_protects_itself():
    """A check that lives outside the thing it checks can be deleted by that thing."""
    for name in ("sovereign/protected.py", "sovereign/invariants.py", "sovereign/eligibility.py", "sovereign/__init__.py"):
        assert name in PROTECTED_PATHS


@pytest.mark.parametrize(
    "authority",
    ["security.py", "verifier.py", "promotion.py", "storage.py", "runtime.py", "sandbox.py", "kernel.py"],
)
def test_historical_protection_is_not_regressed(authority):
    """The release gate protected these seven before this work; it must still protect them."""
    assert authority in PROTECTED_PATHS


def test_tampering_with_a_protected_file_is_detected(tmp_path: Path):
    copy = tmp_path / "evo_agent"
    shutil.copytree(PACKAGE, copy, ignore=shutil.ignore_patterns("__pycache__"))
    target = copy / "security.py"
    original = target.read_text(encoding="utf-8")
    target.write_text(original + "\n# quietly relaxed\n", encoding="utf-8")
    report = verify_sovereign_digests(copy, MANIFEST_PATH)
    assert not report.ok
    assert any(name == "security.py" for name, _, _ in report.mismatched)
    with pytest.raises(SovereignDrift):
        enforce_sovereign_digests(copy, MANIFEST_PATH)
    # The documented developer override must be explicit, not ambient.
    assert enforce_sovereign_digests(copy, MANIFEST_PATH, allow_drift=True).ok is False


def test_deleting_a_protected_file_is_not_a_pass(tmp_path: Path):
    copy = tmp_path / "evo_agent"
    shutil.copytree(PACKAGE, copy, ignore=shutil.ignore_patterns("__pycache__"))
    (copy / "verifier.py").unlink()
    report = verify_sovereign_digests(copy, MANIFEST_PATH)
    assert not report.ok and "verifier.py" in report.missing_files


def test_missing_manifest_is_not_a_pass(tmp_path: Path):
    copy = tmp_path / "evo_agent"
    shutil.copytree(PACKAGE, copy, ignore=shutil.ignore_patterns("__pycache__"))
    (copy / "sovereign" / "sovereign.manifest.json").unlink()
    report = verify_sovereign_digests(copy, copy / "sovereign" / "sovereign.manifest.json")
    assert not report.ok and not report.manifest_present


def test_report_is_json_serialisable():
    payload = verify_sovereign_digests().to_dict()
    assert json.loads(json.dumps(payload))["algorithm"] == "sha256"


# --- the invariant registry ------------------------------------------------------------

def test_all_invariants_pass_on_the_current_tree():
    results = run_invariants()
    failures = [item for item in results if not item.ok]
    assert not failures, "invariant failures:\n" + "\n".join(f"{item.code}: {item.detail}" for item in failures)
    assert len(results) >= 9, "the registry must cover R1-R10; a shrunken registry is itself a finding"
    assert "invariant" in format_report(results).lower() or "ok" in format_report(results)


def test_registry_shape_is_declared_completely():
    registry = invariant_registry()
    codes = [item["code"] for item in registry["checks"]]
    assert len(codes) == len(set(codes)), "duplicate invariant code"
    for item in registry["checks"]:
        assert item["rule"], f"{item['code']} has no rule reference"
        assert item["description"], f"{item['code']} has no description"
        assert item["live"] or item["no_invariant_reason"], f"{item['code']} is neither a check nor a reasoned opt-out"


def test_enforce_raises_the_first_failure_and_names_the_code(tmp_path: Path):
    broken = _tree(tmp_path, {"evil.py": "import numpy\n"})
    results = {item.code: item for item in run_invariants(broken)}
    assert not results["I-import-purity"].ok
    with pytest.raises(RuntimeError) as excinfo:
        enforce_invariants(broken)
    assert str(excinfo.value).startswith("I-"), "an InvariantError must name the code that crossed"


def test_blocklist_cannot_silence_the_checks_everything_else_assumes():
    config = InvariantConfig(blocklist=frozenset({"I-sovereign-digest", "I-single-loop", "I-exec-isolation", "I-import-purity"}))
    selected = {item.code for item in config.select(REGISTRY)}
    assert {"I-sovereign-digest", "I-single-loop", "I-exec-isolation"} <= selected
    assert "I-import-purity" not in selected, "an ordinary check must still be blockable for debugging"


def test_disabling_invariants_is_visible_not_silent():
    assert run_invariants(config=InvariantConfig(enabled=False)) == []
    assert "disabled" in format_report(run_invariants(config=InvariantConfig(enabled=False)))


def test_allowlist_selects_only_what_is_named():
    results = run_invariants(only={"I-persistence-authority"})
    codes = {item.code for item in results}
    # NON_BLOCKABLE are always present, which is the point; the rest is exact.
    assert "I-persistence-authority" in codes
    assert "I-eligibility-coherence" not in codes


def test_opt_out_requires_the_stated_reason_form():
    silent = InvariantDef(code="I-test-silent", rule="R?", description="x", check=None)
    assert silent.run(PACKAGE).ok is False

    bare = InvariantDef(code="I-test-bare", rule="R?", description="x", check=None, no_invariant_reason=NO_RUNTIME_INVARIANT)
    assert bare.run(PACKAGE).ok is False, "the sentinel alone is not a reason"

    reasoned = InvariantDef(
        code="I-test-reasoned", rule="R?", description="x", check=None,
        no_invariant_reason=f"{NO_RUNTIME_INVARIANT} this package holds no state to violate",
    )
    result = reasoned.run(PACKAGE)
    assert result.ok and "no state to violate" in result.detail


def test_a_broken_check_never_reads_as_a_pass():
    exploding = InvariantDef(code="I-test-explode", rule="R?", description="x", check=lambda root: (_ for _ in ()).throw(ValueError("boom")))
    result = exploding.run(PACKAGE)
    assert not result.ok and "check raised" in result.detail


def test_observer_hooks_are_prepended_so_a_later_handler_cannot_mute_them():
    seen: list[str] = []
    observer = InvariantObserver()
    observer.attach(lambda event, payload: seen.append("consumer"))
    observer.prepend(lambda event, payload: seen.append("invariant"))
    observer.notify("turn:ended", {})
    assert seen[0] == "invariant", "checks must run before consumer handlers that may short-circuit"
    assert observer.hook_count == 2


# --- detector behaviour: each check must actually be able to fail --------------------

def _tree(tmp_path: Path, files: dict[str, str]) -> Path:
    root = tmp_path / "evo_agent"
    for name, body in files.items():
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body, encoding="utf-8")
    return root


def test_import_purity_detects_module_level_third_party(tmp_path: Path):
    root = _tree(tmp_path, {"bad.py": "import numpy\n", "ok.py": "def f():\n    from openai import OpenAI\n    return OpenAI\n"})
    ok, detail, evidence = _check_import_purity(root)
    assert not ok and any(item["file"] == "bad.py" for item in evidence["offenders"])
    ok2, _, _ = _check_import_purity(_tree(tmp_path / "second", {"ok.py": "def f():\n    from openai import OpenAI\n"}))
    assert ok2, "function-local extras imports are the sanctioned pattern"


def test_async_leak_detector(tmp_path: Path):
    root = _tree(tmp_path, {"a.py": "async def go():\n    return 1\n"})
    ok, _, evidence = _check_no_async(root)
    assert not ok and evidence["offenders"][0]["file"] == "a.py"


def test_loop_detector_finds_a_second_tool_dispatch_loop_and_adapters_that_own_one(tmp_path: Path):
    root = _tree(tmp_path, {
        "second.py": "class X:\n    def __init__(self):\n        self.tools = 1\n\n    def run(self):\n        while True:\n            self.tools.execute(1)\n",
    })
    ok, detail, _ = _check_single_loop(root)
    assert not ok and "second tool-dispatch loop" in detail

    adapter = _tree(tmp_path / "adapter", {"backends/rogue.py": "class B:\n    def run_turn(self, ctx):\n        while ctx:\n            pass\n"})
    ok2, detail2, _ = _check_single_loop(adapter)
    assert not ok2 and "adapter/port code owns a loop" in detail2
    assert "backends" in LOOP_FORBIDDEN_PACKAGES


def test_execution_sites_detect_shell_and_new_files(tmp_path: Path):
    root = _tree(tmp_path, {
        "brandnew.py": "import subprocess\n\ndef go():\n    return subprocess.run(['ls'], shell=True)\n",
    })
    ok, detail, evidence = _check_execution_sites(root)
    assert not ok and "brandnew.py" in detail
    assert "brandnew.py:spawn" in evidence["gaps"]


def test_execution_ratchet_is_disarmed_after_the_p2_closeout():
    """P0 armed this to fire when P2 fixed the defect; it fired, and the bookkeeping followed.

    The assertion is kept rather than deleted because it is the only thing that notices if the tool
    path moves back onto the host: ``tools.py`` must not spawn, must not appear in the allow-list,
    and ``I-exec-isolation`` must carry no tolerated gap. A ratchet that is silently re-armed by a
    future refactor is the failure mode this file was written to catch.
    """
    assert "tools.py" not in EXECUTION_SITE_ALLOWLIST
    definition = next(item for item in invariant_registry()["checks"] if item["code"] == "I-exec-isolation")
    assert definition["known_gaps"] == []
    ok, detail, _ = _check_execution_sites(PACKAGE)
    assert ok, detail
    assert "none uses the shell" in detail
    results = {item.code: item for item in run_invariants()}
    assert results["I-exec-isolation"].ok and "tolerated" not in results["I-exec-isolation"].detail
    # The isolation layer is where a process starts now, which is the claim P2 replaced the gap with.
    files = _check_execution_sites(PACKAGE)[2]["files"]
    assert any(name.startswith("sandbox_providers/") for name in files), files


def test_persistence_detector(tmp_path: Path):
    root = _tree(tmp_path, {"sneaky.py": "import sqlite3\n\nstore = sqlite3.connect('x.db')\n"})
    ok, detail, evidence = _check_persistence_authority(root)
    assert not ok and evidence["offenders"][0]["file"] == "sneaky.py"
    assert "storage.py" in PERSISTENCE_AUTHORITY_ALLOWLIST


def test_coverage_detector_requires_a_reason_for_a_new_subpackage(tmp_path: Path):
    root = _tree(tmp_path, {"newthing/__init__.py": "", "newthing/impl.py": "X = 1\n"})
    ok, detail, evidence = _check_invariant_coverage(root)
    assert not ok and "newthing" in evidence["gaps"], "a subpackage must be guarded or reasoned"


def test_registry_covers_the_sovereign_package_today():
    ok, detail, _ = _check_invariant_coverage(PACKAGE)
    assert ok, detail


# --- runtime enforcement (the check must run inside the process, not only in pytest) --

def test_runtime_start_verifies_the_sovereign_boundary(tmp_path: Path):
    from evo_agent.runtime import AgentRuntime

    runtime = AgentRuntime(tmp_path)
    record = runtime.start()
    boundary = record.metadata["sovereign_boundary"]
    assert boundary["protected_files"] == len(PROTECTED_PATHS)
    assert "I-sovereign-digest" in boundary["checks"]
    assert runtime.sovereign_report()["ok"]
    runtime.stop()
    events = [item["event_type"] for item in runtime.store.events_for_task(runtime.runtime_id)]
    assert "sovereign_verified" in events


def test_runtime_refuses_to_start_on_drift(tmp_path: Path, monkeypatch):
    """Fail closed: an unverifiable protected set must stop the agent, not warn it."""
    from evo_agent.runtime import AgentRuntime
    from evo_agent.sovereign import protected as protected_module

    monkeypatch.setattr(
        "evo_agent.sovereign.invariants.verify_sovereign_digests",
        lambda *a, **k: protected_module.ProtectionReport(
            ok=False, manifest_present=True, mismatched=(("security.py", "expected", "actual"),)
        ),
    )
    runtime = AgentRuntime(tmp_path)
    with pytest.raises(RuntimeError, match="sovereign boundary check failed"):
        runtime.start()
    events = [item["event_type"] for item in runtime.store.events_for_task(runtime.runtime_id)]
    assert "sovereign_drift_detected" in events, "the refusal must be in the audit trail"
    assert runtime.runtime_record.metadata.get("sovereign_boundary") is None, (
        "a failed check must leave no suggestion that it passed"
    )


def test_full_registry_is_not_run_at_startup(tmp_path: Path):
    """Startup affordance: cheap checks only, so the boundary costs milliseconds."""
    from evo_agent.sovereign import run_invariants

    cheap = {item.code for item in run_invariants(cheap_only=True)}
    full = {item.code for item in run_invariants()}
    assert cheap < full
    registry = invariant_registry()["checks"]
    for item in registry:
        if item["code"] in cheap:
            assert item["cheap"]


# --- the scripts the CI actually calls ------------------------------------------------

def _load_script(name: str):
    path = ROOT / "scripts" / name
    spec = importlib.util.spec_from_file_location(path.stem, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_digest_script_verifies_and_reports():
    completed = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "verify_sovereign_digest.py"), "--invariants", "--json"],
        cwd=ROOT, capture_output=True, text=True, check=False,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    payload = json.loads(completed.stdout)
    assert payload["ok"] and len(payload["invariants"]) >= 9


def test_digest_script_fails_after_tampering(tmp_path: Path):
    copy = tmp_path / "repo"
    shutil.copytree(PACKAGE, copy / "evo_agent", ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copytree(ROOT / "scripts", copy / "scripts", ignore=shutil.ignore_patterns("__pycache__"))
    target = copy / "evo_agent" / "verifier.py"
    target.write_text(target.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    completed = subprocess.run(
        [sys.executable, str(copy / "scripts" / "verify_sovereign_digest.py"), "--gate"],
        cwd=copy, capture_output=True, text=True, check=False,
    )
    assert completed.returncode != 0
    assert "sovereign digest mismatch" in completed.stdout + completed.stderr


def test_production_gate_reads_the_manifest():
    gate = _load_script("run_production_gate.py")
    published = gate.protected_paths()
    assert set(published) == set(PROTECTED_PATHS)
    assert gate.digest() == published, "the gate must see the tree as it currently stands"
    assert len(published) >= gate.MINIMUM_PROTECTED_FILES
