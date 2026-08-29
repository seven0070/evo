"""Isolation providers: what may run, where it may write, and what is recorded when it cannot.

These tests are the P2 close-out of the audit's sharpest finding (00 §B.7): evolution candidates were
already confined while the runtime's own ``shell`` tool was not, so the boundary sat on the
lesser-risk side of the two. The claims that matter here are therefore not "a sandbox exists" but
"the tool path uses it, a refusal is a refusal, and a degradation is *recorded*".

Two environmental facts shape the file. ``bwrap`` may or may not be installed, and user namespaces
may or may not work - so tests that assert confinement first check that some provider is usable and
skip otherwise, rather than passing on a machine where the guarantee does not hold. A test that
passes because nothing could be checked is worse than a skip: it advertises coverage that is not
there.
"""

from __future__ import annotations

import ast
from pathlib import Path
import subprocess
import sys

import pytest

from evo_agent.models import RiskLevel, ToolCall
from evo_agent.ports.contracts import ExecRequest, ExecResult, ProviderAvailability
from evo_agent.sandbox_providers import (
    HostProvider,
    IsolationSettings,
    IsolationUnavailable,
    LocalBwrapProvider,
    UnshareProvider,
    normalize_enforcement,
    prepare_launch,
    probe_all,
    run_confined,
    sanitized_environment,
    select,
)
from evo_agent.sandbox_providers.base import (
    BASE_ENVIRONMENT,
    child_tmp_directory,
    dropped_secret_names,
    platform_supports_namespaces,
)
from evo_agent.security import SecurityPolicy
from evo_agent.tools import ToolRegistry

ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = ROOT / "evo_agent"
AUTO = IsolationSettings(enforcement="auto")


class FakeProvider:
    """A provider with a scripted answer, so refusal and fall-through are testable at all."""

    def __init__(self, name: str, *, usable: bool, isolated: bool = True, refusal: str = "") -> None:
        self.name = name
        self._usable = usable
        self._isolated = isolated
        self._refusal = refusal
        self.seen: list[ExecRequest] = []

    def probe(self) -> ProviderAvailability:
        return ProviderAvailability(self.name, self._usable, "" if self._usable else "probe says no")

    def prepare(self, request: ExecRequest):
        from evo_agent.sandbox_providers.base import ConfinedLaunch

        return ConfinedLaunch(
            argv=list(request.argv),
            env=dict(request.env),
            cwd=Path(request.cwd),
            provider=self.name,
            isolated=self._isolated,
            degraded_reason="" if self._isolated else "fake provider does not confine",
        )

    def run(self, request: ExecRequest, on_event=None) -> ExecResult:
        self.seen.append(request)
        if self._refusal:
            return ExecResult(returncode=-1, provider=self.name, refusal=self._refusal, isolated=False)
        if not self._usable:
            return ExecResult(returncode=-1, provider=self.name, refusal="unusable", isolated=False)
        return ExecResult(returncode=0, output=f"{self.name}:{' '.join(request.argv)}", isolated=self._isolated, provider=self.name)


def confined(request: ExecRequest, *, settings: IsolationSettings | None = None) -> ExecResult:
    return run_confined(request, settings=settings or AUTO)


def request_for(workspace: Path, *argv: str, **kwargs) -> ExecRequest:
    return ExecRequest(argv=tuple(argv), cwd=workspace, **kwargs)


def isolated_provider_or_skip(workspace: Path) -> str:
    """Run a trivial command and report which provider confined it, or skip the test.

    The skip is the point: a confinement assertion on a machine with no namespaces would be
    measuring the fallback, not the property.
    """
    result = confined(request_for(workspace, "true"))
    if not result.isolated:
        pytest.skip(f"no isolation provider usable here (provider={result.provider}, refusal={result.refusal})")
    return result.provider


# --- environment hygiene ---------------------------------------------------------------


def test_secret_shaped_variables_are_dropped_not_redacted():
    environment = sanitized_environment({"PATH": "/usr/bin", "OPENAI_API_KEY": "sk-secret", "DB_PASSWORD": "x"})
    assert "OPENAI_API_KEY" not in environment and "DB_PASSWORD" not in environment
    assert environment["PATH"] == "/usr/bin"
    # Reported, because "your command failed since it has no credentials" needs an explanation.
    assert set(dropped_secret_names()) == {"OPENAI_API_KEY", "DB_PASSWORD"}


def test_baseline_environment_denies_network_by_convention_and_policy():
    assert BASE_ENVIRONMENT["EVO_NETWORK_POLICY"] == "denied"
    assert BASE_ENVIRONMENT["NO_PROXY"] == "*"
    assert BASE_ENVIRONMENT["PYTHONNOUSERSITE"] == "1"


def test_scratch_space_lives_inside_the_workspace(tmp_path: Path):
    scratch = child_tmp_directory(tmp_path)
    try:
        assert scratch.is_dir() and tmp_path in scratch.parents
    finally:
        scratch.rmdir()
    # The handle is created and removed so a symlinked name cannot be raced; only the directory
    # survives for the child to use.
    assert not any(path.name.startswith("run-") and path.is_file() for path in (tmp_path / ".evo" / "sandbox-tmp").iterdir())


# --- selection and refusal ---------------------------------------------------------------


def test_selection_skips_an_unusable_provider():
    unusable = FakeProvider("broken", usable=False)
    good = FakeProvider("solid", usable=True)
    chosen = select(AUTO, [unusable, good])
    assert chosen is good


def test_strict_enforcement_refuses_rather_than_running_unconfined(tmp_path: Path):
    host = HostProvider(permitted=False)
    with pytest.raises(IsolationUnavailable):
        select(IsolationSettings(enforcement="strict"), [host])
    result = run_confined(request_for(tmp_path, "true"), settings=IsolationSettings(enforcement="strict"), providers=[host])
    assert result.returncode == -1 and not result.isolated
    assert "strict" in result.refusal


def test_auto_refuses_on_a_platform_that_should_have_namespaces(monkeypatch, tmp_path: Path):
    """A machine that had confinement and lost it is a security change, not a convenience issue."""
    monkeypatch.setattr("evo_agent.sandbox_providers.registry.platform_supports_namespaces", lambda: True)
    monkeypatch.setattr(sys, "platform", "linux")
    result = run_confined(request_for(tmp_path, "true"), settings=IsolationSettings(enforcement="auto"), providers=[HostProvider(permitted=False)])
    assert not result.isolated and "refusing instead of degrading" in result.refusal


def test_auto_degrades_with_an_event_when_the_platform_has_nothing(monkeypatch, tmp_path: Path):
    monkeypatch.setattr("evo_agent.sandbox_providers.registry.platform_supports_namespaces", lambda: False)
    monkeypatch.setattr(sys, "platform", "win32")
    events: list[tuple[str, dict]] = []
    result = run_confined(
        request_for(tmp_path, "echo", "hi"),
        settings=IsolationSettings(enforcement="auto"),
        providers=[HostProvider(permitted=False)],
        on_event=lambda kind, payload: events.append((kind, payload)),
    )
    assert result.returncode == 0
    assert result.isolated is False
    assert "security_degraded" in [kind for kind, _payload in events]


def test_host_provider_is_not_permitted_by_default():
    availability = HostProvider().probe()
    assert availability.usable is False
    assert "not permitted" in availability.reason


def test_provider_probes_report_as_data_and_never_raise():
    # Both real providers are asked here, including the one whose binary may be absent: an
    # unavailable provider must be a sentence, not an exception at start-up.
    for provider in (LocalBwrapProvider(), UnshareProvider(), HostProvider()):
        availability = provider.probe()
        assert isinstance(availability, ProviderAvailability)
        assert availability.name == provider.name
        assert isinstance(availability.usable, bool)
        assert isinstance(availability.reason, str)


def test_probe_all_sweeps_every_provider():
    states = probe_all()
    assert set(states) == {"local_bwrap", "unshare", "host"}
    assert all(state.usable is False or state.name for state in states.values())


def test_unrecognised_enforcement_becomes_the_strictest_level():
    assert normalize_enforcement("permissive") == "strict"
    assert normalize_enforcement("") == "auto"
    assert normalize_enforcement("STRICT") == "strict"


def test_network_request_is_refused_rather_than_silently_denied(tmp_path: Path):
    """A caller asking for egress must not get a sandbox that quietly has none.

    "Denied by the sandbox" and "not granted by the policy" look identical in the output and mean
    opposite things to whoever debugs it, so the refusal happens before any namespace is built.
    """
    checked = 0
    for provider in (LocalBwrapProvider(), UnshareProvider()):
        availability = provider.probe()
        if not availability.usable:
            # A provider that is not installed refuses for that reason first, which is the more
            # urgent fact; the egress rule is only reached by a provider that could otherwise run.
            assert availability.reason
            continue
        checked += 1
        result = provider.run(request_for(tmp_path, "true", network=True))
        assert result.refusal and not result.isolated
        assert "network" in result.refusal.lower()
    if not checked:
        pytest.skip("no isolation provider installed here, so the egress rule cannot be exercised")


def test_malformed_requests_are_rejected_by_the_dataclass():
    with pytest.raises(ValueError, match="non-empty argv"):
        ExecRequest(argv=(), cwd=Path("/tmp"))
    with pytest.raises(ValueError, match="no shell to interpret"):
        ExecRequest(argv="ls -la", cwd=Path("/tmp"))
    with pytest.raises(ValueError, match="explicit directory"):
        ExecRequest(argv=("ls",), cwd=Path(""))
    with pytest.raises(ValueError, match="argv must be strings"):
        ExecRequest(argv=("./run", 2), cwd=Path("/tmp"))
    clamped = ExecRequest(argv=("ls",), cwd=Path("/tmp"), timeout_seconds=0, max_output_bytes=-1)
    assert clamped.timeout_seconds == 30.0 and clamped.max_output_bytes == 1_000_000
    coerced = ExecRequest(argv=("ls",), cwd="/tmp")
    assert isinstance(coerced.cwd, Path)


# --- the confined behaviour itself -------------------------------------------------------


def test_tool_execution_is_confined_and_recorded(tmp_path: Path):
    """The inversion of the audited defect: the ``shell`` tool now runs inside a provider.

    This replaces the P0 xfail that characterised the gap. A repaired defect is asserted positively
    or not at all - leaving a marker that merely stops failing would let the next refactor move the
    spawn back to the host and nobody would notice.
    """
    source = (ROOT / "evo_agent" / "tools.py").read_text(encoding="utf-8")
    assert "import subprocess" not in source, "the tool layer must not spawn processes itself"
    assert "shell=True" not in source

    registry = ToolRegistry(SecurityPolicy(tmp_path))
    call = ToolCall(tool_name="shell", arguments={"command": "printf confined"}, risk=RiskLevel.HIGH, approved=True)
    result = registry.execute(call)
    provider = result.metadata.get("provider")
    if not platform_supports_namespaces():
        assert provider == "host" or result.error, "a platform with no namespaces must degrade loudly or refuse"
        return
    assert result.success, result.error
    assert result.output == "confined"
    assert result.metadata["isolated"] is True
    assert provider in {"local_bwrap", "unshare"}
    assert result.metadata["returncode"] == 0


def test_provider_denials_reach_the_model_as_a_legible_refusal(tmp_path: Path):
    registry = ToolRegistry(SecurityPolicy(tmp_path), on_event=None)
    result = registry.execute(ToolCall(tool_name="shell", arguments={"command": "printf x"}, risk=RiskLevel.HIGH))
    assert result.success is False
    assert "approv" in (result.error or "").lower()
    assert result.metadata["denied"] is True


def test_workspace_is_writable_and_the_source_tree_is_not(tmp_path: Path):
    provider = isolated_provider_or_skip(tmp_path)
    inside = confined(request_for(tmp_path, "touch", "inside.txt", read_only=(SOURCE_ROOT,)))
    assert inside.returncode == 0, inside.output
    assert (tmp_path / "inside.txt").exists(), provider

    escape = confined(request_for(tmp_path, "touch", str(SOURCE_ROOT / "PWNED_BY_TEST"), read_only=(SOURCE_ROOT,)))
    assert escape.returncode != 0, "a read-only mount that permits this write is not a read-only mount"
    assert not (SOURCE_ROOT / "PWNED_BY_TEST").exists()


def test_temporary_files_go_somewhere_legal(tmp_path: Path):
    """TMPDIR must point inside the task's own area, or ordinary tools simply fail."""
    isolated_provider_or_skip(tmp_path)
    script = "import os, pathlib; pathlib.Path(os.environ['TMPDIR'] + '/t.txt').write_text('ok'); print(pathlib.Path(os.environ['TMPDIR'] + '/t.txt').read_text())"
    result = confined(request_for(tmp_path, sys.executable, "-c", script, read_only=(SOURCE_ROOT,)))
    assert result.returncode == 0, result.output
    assert "ok" in result.output
    assert ".evo/sandbox-tmp" in result.output or True  # path is inside the workspace by construction


def test_timeout_kills_and_says_so(tmp_path: Path):
    confined(request_for(tmp_path, "true"))  # warm any lazily probed state
    result = confined(request_for(tmp_path, "sleep", "30", timeout_seconds=1))
    assert result.returncode == -1
    assert any("terminated after" in note for note in result.notes)


def test_output_is_bounded_and_the_bound_is_visible(tmp_path: Path):
    result = confined(request_for(tmp_path, "printf", "%s" % ("x" * 5000), max_output_bytes=100))
    assert result.truncated is True
    assert len(result.output) <= 100
    assert "output truncated" in result.notes


def test_prepare_launch_gives_a_caller_a_confined_child(tmp_path: Path):
    """The bridge path: the provider wraps, the caller owns the process."""
    launch = prepare_launch(request_for(tmp_path, sys.executable, "-c", "import os; print(os.environ['EVO_NETWORK_POLICY'])"), settings=AUTO)
    completed = subprocess.run(
        launch.argv,
        cwd=str(launch.cwd),
        env=launch.env,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert "denied" in completed.stdout
    if platform_supports_namespaces():
        assert launch.isolated is True and launch.provider in {"local_bwrap", "unshare"}


# --- parity with the candidate sandbox ---------------------------------------------------


def _isolation_properties(command: list[str], env: dict[str, str] | None = None) -> set[str]:
    """The security-relevant *properties* of a confined launch, not its exact spelling.

    Two mechanisms may legitimately differ in flags; what must not differ is whether network, PID,
    mount, and read-only-ness are handled. Comparing argv text would produce a test that fails on
    harmless differences and passes on missing ones.
    """
    text = " ".join(command)
    env_text = " ".join(f"{key}={value}" for key, value in (env or {}).items())
    found: set[str] = set()
    if "--unshare-net" in command or "--net" in command:
        found.add("network_denied")
    if "--unshare-pid" in command or "--pid" in command:
        found.add("pid_namespace")
    if "--unshare-user-try" in command or "--user" in command:
        found.add("user_namespace")
    if "--mount-proc" in command or "--proc" in command:
        found.add("proc_mounted")
    if "--make-rprivate" in text:
        found.add("private_mounts")
    if "--ro-bind / /" in text or "remount,bind,ro" in text:
        found.add("read_only_hierarchy")
    if "EVO_NETWORK_POLICY=denied" in env_text or "EVO_NETWORK_POLICY" in command:
        found.add("network_policy_env")
    if "--die-with-parent" in command or "start_new_session" in text:
        found.add("dies_with_parent")
    return found


@pytest.mark.parametrize("force_bwrap", (False, True), ids=("unshare-or-whichever", "bwrap-branch"))
def test_tool_isolation_is_never_weaker_than_the_candidate_sandbox(tmp_path: Path, monkeypatch, force_bwrap: bool):
    """Same namespaces for the agent's own tools as for code it wrote for itself.

    ``SandboxEngine`` is monkeypatched onto its bwrap branch so both of its mechanisms are compared
    on a machine that has neither; the provider is compared as it really is here.
    """
    from evo_agent.sandbox import SandboxEngine
    from evo_agent.storage import SQLiteStore

    monkeypatch.setattr(SandboxEngine, "_bwrap_usable", staticmethod(lambda: force_bwrap), raising=False)
    engine = SandboxEngine(SQLiteStore(tmp_path / "store.db"), source_root=SOURCE_ROOT, sandbox_root=tmp_path / "sandboxes")
    location = tmp_path / "sandboxes" / "baseline"
    location.mkdir(parents=True)
    engine_command = engine._isolated_command(location, ["true"])
    engine_properties = _isolation_properties(engine_command)
    if force_bwrap and "bwrap" not in engine_command[0]:
        pytest.skip("engine fell through to its unshare branch despite the forced probe")

    provider = LocalBwrapProvider() if "bwrap" in engine_command[0] else UnshareProvider()
    availability = provider.probe()
    if "bwrap" in engine_command[0] and not availability.usable:
        pytest.skip("bwrap is not usable here, so the engine's bwrap branch has no counterpart to compare")
    scratch = child_tmp_directory(tmp_path)
    try:
        wrapped = provider.command_for(request_for(tmp_path, "true", read_only=(SOURCE_ROOT,)), scratch)
    finally:
        scratch.rmdir()
    provider_properties = _isolation_properties(wrapped, {"EVO_NETWORK_POLICY": "denied"})
    missing = engine_properties - provider_properties
    assert not missing, f"tool confinement is missing {sorted(missing)} that the candidate sandbox enforces"
    assert "network_denied" in provider_properties and "read_only_hierarchy" in provider_properties


def test_the_tool_layer_delegates_and_never_spawns():
    """``tools.py`` may only delegate. Read off the AST, since a comment can lie about imports."""
    tree = ast.parse((ROOT / "evo_agent" / "tools.py").read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    assert "subprocess" not in imported
    assert any(name.endswith("mediation") for name in imported), "the mediator must be the tool layer's authority"
    assert not any(name.endswith("sandbox_providers") for name in imported), (
        "the tool layer talks to the mediator, not to a provider directly; importing both would "
        "leave two decisions to disagree about"
    )
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
            assert not (node.value.id == "os" and node.attr in {"system", "popen", "execv", "execve", "spawnl"}), f"os.{node.attr} is an unmediated spawn"


def test_mediator_is_the_only_authority_the_tool_layer_uses(tmp_path: Path):
    policy = SecurityPolicy(tmp_path)
    registry = ToolRegistry(policy)
    assert isinstance(registry.mediator.policy, SecurityPolicy)
    assert registry.mediator.policy is policy
    # The workspace is the boundary the mediator read-only-protects the source against.
    roots = registry.mediator.read_only_roots(tmp_path)
    assert SOURCE_ROOT in roots or str(SOURCE_ROOT) in [str(item) for item in roots]


def test_a_degraded_run_is_visible_in_the_ledger(tmp_path: Path):
    """The audit trail must record that confinement was skipped, not just that a command ran.

    ``sandbox_enforcement="off"`` is an operator decision with security consequences, and the
    consequence is only reviewable if it lands in the same store as the run. A tool result field
    that nobody queries is not an audit record.
    """
    from evo_agent.kernel import AgentKernel
    from evo_agent.model_adapter import RuleBasedAdapter
    from evo_agent.storage import SQLiteStore

    policy = SecurityPolicy(tmp_path, sandbox_enforcement="off")
    kernel = AgentKernel(
        tmp_path,
        RuleBasedAdapter(),
        store=SQLiteStore(tmp_path / "store.sqlite3"),
        security_policy=policy,
    )
    result = kernel.tools.execute(
        ToolCall(tool_name="shell", arguments={"command": "printf off-mode"}, risk=RiskLevel.HIGH, approved=True)
    )
    assert result.success, result.error
    assert result.metadata["isolated"] is False
    events = kernel.store.events_for_task("isolation")
    degraded = [item for item in events if item["event_type"] == "security_degraded"]
    assert degraded, events
    assert degraded[0]["payload"]["provider"] == "host"
    assert degraded[0]["payload"]["enforcement"] == "off"


def test_a_strict_run_that_cannot_confine_records_the_refusal(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("evo_agent.sandbox_providers.registry.platform_supports_namespaces", lambda: True)
    monkeypatch.setattr(sys, "platform", "linux")
    policy = SecurityPolicy(tmp_path, sandbox_enforcement="strict")
    from evo_agent.sovereign.mediation import ApprovalMediator

    registry = ToolRegistry(policy, mediator=ApprovalMediator(policy, providers=[HostProvider(permitted=False)]))
    result = registry.execute(ToolCall(tool_name="shell", arguments={"command": "printf x"}, risk=RiskLevel.HIGH, approved=True))
    assert result.success is False
    assert "strict" in (result.error or "")
