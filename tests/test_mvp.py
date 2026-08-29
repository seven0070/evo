from pathlib import Path

from evo_agent.checkpoints import CheckpointManager
from evo_agent.kernel import AgentKernel
from evo_agent.model_adapter import RuleBasedAdapter
from evo_agent.models import RiskLevel, TaskStatus, ToolCall
from evo_agent.security import SecurityPolicy
from evo_agent.storage import SQLiteStore
from evo_agent.tools import ToolRegistry


def test_workspace_path_cannot_escape(tmp_path: Path):
    policy = SecurityPolicy(tmp_path)
    try:
        policy.resolve_workspace_path("../outside.txt")
    except PermissionError:
        pass
    else:
        raise AssertionError("workspace traversal must be rejected")


def test_shell_is_allowlisted_and_restricted(tmp_path: Path):
    registry = ToolRegistry(SecurityPolicy(tmp_path))
    # ``approved=True`` is not ceremony. Since P2 the execution mediator, not the caller, decides,
    # and a HIGH-risk command must carry approval evidence (the kernel records it after the
    # operator answers) or it is refused - "nobody was asked" is not consent.
    safe = registry.execute(ToolCall(tool_name="shell", arguments={"command": "printf hello"}, risk=RiskLevel.HIGH, approved=True))
    assert safe.success
    assert safe.output == "hello"
    unsafe = registry.execute(ToolCall(tool_name="shell", arguments={"command": "rm -rf x"}, risk=RiskLevel.HIGH, approved=True))
    assert not unsafe.success
    assert any(token in (unsafe.error or "") for token in ("restricted", "not allowlisted"))


def test_shell_without_approval_evidence_is_refused(tmp_path: Path):
    registry = ToolRegistry(SecurityPolicy(tmp_path))
    blocked = registry.execute(ToolCall(tool_name="shell", arguments={"command": "printf hello"}, risk=RiskLevel.HIGH))
    assert blocked.success is False
    assert "approv" in (blocked.error or "").lower()


def test_kernel_blocks_medium_risk_without_approval(tmp_path: Path):
    kernel = AgentKernel(tmp_path, RuleBasedAdapter(), approval_callback=lambda call, reason: False)
    outcome = kernel.run("write this goal")
    assert outcome.status is TaskStatus.BLOCKED
    assert not (tmp_path / "agent_goal.txt").exists()


def test_kernel_runs_approved_task_and_persists_memory(tmp_path: Path):
    kernel = AgentKernel(tmp_path, RuleBasedAdapter(), approval_callback=lambda call, reason: True)
    outcome = kernel.run("list the files in the workspace")
    assert outcome.status is TaskStatus.SUCCEEDED
    assert outcome.steps_completed == 1
    assert kernel.store.recent_memories(1)[0]["kind"] == "experience"
    assert any(event.event_type.value == "verification" for event in outcome.events)


def test_checkpoint_and_rollback_restore_workspace(tmp_path: Path):
    store = SQLiteStore(tmp_path / ".evo" / "agent.sqlite3")
    manager = CheckpointManager(tmp_path, store)
    (tmp_path / "note.txt").write_text("before", encoding="utf-8")
    checkpoint = manager.create("task_test", "before-change")
    (tmp_path / "note.txt").write_text("after", encoding="utf-8")
    (tmp_path / "new.txt").write_text("new", encoding="utf-8")
    manager.rollback(checkpoint)
    assert (tmp_path / "note.txt").read_text(encoding="utf-8") == "before"
    assert not (tmp_path / "new.txt").exists()
