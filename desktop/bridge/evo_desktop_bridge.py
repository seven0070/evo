#!/usr/bin/env python3
"""Local JSON-lines bridge for the Evo Tauri desktop shell.

The bridge is deliberately narrow: it reuses AgentRuntime and never exposes
arbitrary Python, shell, filesystem, provider, or approval operations.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any


def build_runtime(workspace: Path):
    from evo_agent import AgentRuntime, PersonalOperatingProfile, RuleBasedAdapter

    profile = PersonalOperatingProfile.load(workspace=workspace)
    if profile.model != "offline":
        raise RuntimeError("desktop bridge currently supports the offline adapter only")
    policy = profile.build_security_policy(workspace)
    return AgentRuntime(
        workspace,
        model=RuleBasedAdapter(),
        limits=profile.to_runtime_limits(),
        approval_callback=lambda _call, _reason: False,
        safe_mode=profile.safe_mode_default,
        security_policy=policy,
    )


def _task_summary(task: Any) -> dict[str, Any]:
    value = task.to_dict() if hasattr(task, "to_dict") else dict(task)
    metadata = value.get("metadata") if isinstance(value.get("metadata"), dict) else {}
    return {
        "task_id": value.get("task_id"),
        "goal": value.get("goal"),
        "status": value.get("status"),
        "progress": value.get("progress", "not_started"),
        "last_error": value.get("last_error"),
        "verified": bool(metadata.get("verified", False)),
        "created_at": value.get("created_at"),
        "updated_at": value.get("updated_at"),
        "plan_id": value.get("plan_id"),
        "metadata": {"verified": bool(metadata.get("verified", False))},
    }


def _approval_summary(approval: dict[str, Any]) -> dict[str, Any]:
    return {
        "approval_id": approval.get("approval_id"),
        "task_id": approval.get("task_id"),
        "status": approval.get("status"),
        "actor": approval.get("actor"),
        "scope_hash": approval.get("scope_hash", ""),
        "reason": approval.get("reason", ""),
        "created_at": approval.get("created_at"),
        "updated_at": approval.get("updated_at"),
    }


def handle(request: dict[str, Any], workspace: Path) -> Any:
    command = str(request.get("command", "")).strip()
    if command == "get_profile":
        from evo_agent import PersonalOperatingProfile
        return PersonalOperatingProfile.load(workspace=workspace).to_dict()
    runtime = build_runtime(workspace)
    if command == "get_status":
        status = runtime.status()
        status["pending_approvals_list"] = [
            _approval_summary(item)
            for item in runtime.store.find_runtime_approvals(status="pending", limit=50)
        ]
        return status
    if command == "list_tasks":
        limit = min(max(int(request.get("limit", 30)), 1), 100)
        return [_task_summary(item) for item in runtime.tasks(limit=limit)]
    if command == "submit_goal":
        goal = str(request.get("goal", "")).strip()
        if not goal:
            raise ValueError("goal must not be empty")
        runtime.start()
        task = runtime.enqueue_task(goal, approval_requirement=bool(request.get("approvalRequired", False)))
        return {"task_id": task.task_id, "status": task.status.value, "message": "Goal admitted to the bounded Runtime queue."}
    if command == "run_cycle":
        return runtime.run_cycle().to_dict()
    if command == "set_safe_mode":
        runtime.start()
        return runtime.set_safe_mode(bool(request.get("enabled", True)), "desktop operator control").to_dict()
    if command == "kill_switch":
        return runtime.kill_switch("desktop operator emergency stop").to_dict()
    if command == "approve_task":
        task_id = str(request.get("taskId", "")).strip()
        if not task_id:
            raise ValueError("taskId must not be empty")
        approval = runtime.approve_task(task_id, actor="human", scope_hash=str(request.get("scopeHash", "")) or None, reason="desktop operator approval")
        return approval.to_dict()
    raise ValueError(f"unsupported desktop command: {command}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", required=True)
    args = parser.parse_args()
    workspace = Path(args.workspace).expanduser().resolve()
    workspace.mkdir(parents=True, exist_ok=True)
    for line in sys.stdin:
        if not line.strip():
            continue
        try:
            result = handle(json.loads(line), workspace)
            print(json.dumps({"ok": True, "value": result}, sort_keys=True), flush=True)
        except Exception as exc:
            print(json.dumps({"ok": False, "error": f"{type(exc).__name__}: {exc}"}, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
