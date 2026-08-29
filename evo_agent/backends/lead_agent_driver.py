"""The child half of the lead-agent bridge. Runs in the harness's interpreter, not Evo's.

Deliberately tiny and deliberately dumb. It has exactly one job: translate between a JSON-lines
protocol and whatever API the installed harness offers, and it must not be able to do anything else -
no filesystem writes outside the workspace, no network, no imports of ``evo_agent``. Those limits are
enforced from outside (it runs confined, with the source tree read-only, and it has no credentials in
its environment), so this file needs no policy of its own; keeping policy out of here is what makes
the two halves agree on what the rules are.

Protocol (one JSON object per line, in both directions):

    -> {"goal": ..., "tools": [...], "budget": {...}, "history": [...]}
    <- {"type": "probe", "ok": true, "harness": "langgraph", "version": "..."}
    <- {"type": "event", "event": "step_started", "payload": {...}}
    <- {"type": "tool_request", "id": "1", "tool": "shell", "argv": [...], "cwd": "..."}
    -> {"type": "tool_response", "id": "1", "ok": true, "output": "..."}
    <- {"type": "final", "text": "..."}   or   {"type": "error", "message": "..."}

Exit codes: 0 usable/completed, 2 probe failed (harness not importable), 3 protocol error,
4 the harness itself raised. Each maps to a distinct sentence in the parent's report, because
"not installed", "protocol mismatch", and "the harness crashed" need different fixes.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any, Callable


EXIT_OK = 0
EXIT_UNUSABLE = 2
EXIT_PROTOCOL = 3
EXIT_HARNESS = 4

#: Candidate import paths, in the order a reviewer would want them tried. ``deerflow`` is the
#: upstream package name; ``langgraph`` is the graph runtime its lead agent is built on. Neither is
#: an Evo dependency, and this file must work when neither is present: it reports, it does not fail
#: the parent.
HARNESS_MODULES = ("deerflow", "langgraph")


def emit(payload: dict[str, Any]) -> None:
    """Write one protocol line and flush it. A buffered child would deadlock against a waiting parent."""
    json.dump(payload, sys.stdout, sort_keys=True, default=str)
    sys.stdout.write("\n")
    sys.stdout.flush()


def read_request() -> dict[str, Any] | None:
    line = sys.stdin.readline()
    if not line:
        return None
    try:
        payload = json.loads(line)
    except ValueError as exc:
        emit({"type": "error", "message": f"request is not JSON: {exc}"})
        return None
    return payload if isinstance(payload, dict) else None


def load_harness() -> tuple[Callable[..., Any] | None, str, str]:
    """Find a usable entry point. Returns ``(builder, harness_name, reason)``.

    The builder is a callable that accepts ``(goal, tools, history)`` and returns an iterator of
    steps. Nothing here knows what LangGraph's real API looks like - deliberately: pinning this
    file to an upstream signature would make an upstream release break the bridge silently, where
    reporting "no recognised harness entry point" makes the gap visible at start-up.
    """
    last_reason = "no harness module is importable"
    for module_name in HARNESS_MODULES:
        try:
            module = __import__(module_name, fromlist=["__version__"])
        except Exception as exc:  # ImportError plus anything a broken install raises
            last_reason = f"{module_name} not importable: {type(exc).__name__}: {exc}"
            continue
        version = str(getattr(module, "__version__", "unknown"))
        builder = getattr(module, "build_lead_agent", None) or getattr(module, "make_lead_agent", None)
        if not callable(builder):
            last_reason = (
                f"{module_name} {version} exposes no 'build_lead_agent' entry point; this bridge "
                "adapts a declared interface rather than guessing at an upstream signature"
            )
            continue
        return builder, module_name, version
    return None, "", last_reason


def run_probe() -> int:
    builder, harness_name, detail = load_harness()
    if builder is None:
        emit({"type": "probe", "ok": False, "reason": detail, "searched": list(HARNESS_MODULES)})
        return EXIT_UNUSABLE
    emit({"type": "probe", "ok": True, "harness": harness_name, "version": detail, "protocol": 1})
    return EXIT_OK


def drive_turn(request: dict[str, Any]) -> int:
    builder, harness_name, version = load_harness()
    if builder is None:
        emit({"type": "error", "message": f"harness unusable: {harness_name or version}"})
        return EXIT_UNUSABLE
    goal = str(request.get("goal") or "")
    tools = list(request.get("tools") or [])
    history = list(request.get("history") or [])
    if not goal:
        emit({"type": "error", "message": "request carries no goal"})
        return EXIT_PROTOCOL
    emit({"type": "event", "event": "harness_selected", "payload": {"harness": harness_name, "version": version}})
    try:
        graph = builder(goal=goal, tools=tools, history=history)
    except Exception as exc:
        emit({"type": "error", "message": f"harness could not be built: {type(exc).__name__}: {exc}"})
        return EXIT_HARNESS
    try:
        for step in graph:
            payload = step if isinstance(step, dict) else {"text": str(step)}
            kind = str(payload.get("type") or "event")
            if kind == "tool_request":
                response = exchange_tool_request(payload)
                if response is None:
                    emit({"type": "error", "message": "the parent closed the channel before answering a tool request"})
                    return EXIT_PROTOCOL
                emit({"type": "event", "event": "tool_response_received", "payload": {"id": payload.get("id"), "ok": response.get("ok")}})
                continue
            if kind == "final":
                emit({"type": "final", "text": str(payload.get("text") or "")})
                return EXIT_OK
            emit({"type": "event", "event": kind, "payload": {key: value for key, value in payload.items() if key != "type"}})
    except Exception as exc:
        emit({"type": "error", "message": f"harness raised: {type(exc).__name__}: {exc}"})
        return EXIT_HARNESS
    emit({"type": "error", "message": "harness produced no final answer"})
    return EXIT_HARNESS


def exchange_tool_request(payload: dict[str, Any]) -> dict[str, Any] | None:
    """Ask the parent to run one tool, and wait for its verdict on *that request only*.

    The child never executes anything itself. If it did, the mediator would be advisory, and an
    advisory mediator is not a boundary.
    """
    request = {
        "type": "tool_request",
        "id": str(payload.get("id") or "1"),
        "tool": str(payload.get("tool") or "shell"),
        "argv": list(payload.get("argv") or []),
        "cwd": payload.get("cwd"),
        "arguments": dict(payload.get("arguments") or {}),
    }
    emit(request)
    line = sys.stdin.readline()
    if not line:
        return None
    try:
        response = json.loads(line)
    except ValueError:
        return {"ok": False, "error": "parent sent a non-JSON response"}
    if not isinstance(response, dict) or response.get("type") != "tool_response":
        return {"ok": False, "error": "parent sent an unexpected response kind"}
    return response


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Evo lead-agent bridge driver (child process)")
    parser.add_argument("--probe", action="store_true", help="report whether a harness is usable and exit")
    parser.add_argument("--turn", action="store_true", help="read one turn request from stdin and drive it")
    arguments = parser.parse_args(argv)
    if arguments.probe:
        return run_probe()
    if arguments.turn:
        request = read_request()
        if request is None:
            return EXIT_PROTOCOL
        return drive_turn(request)
    emit({"type": "error", "message": "nothing to do: pass --probe or --turn"})
    return EXIT_PROTOCOL


if __name__ == "__main__":  # pragma: no cover - exercised through the backend, never imported
    raise SystemExit(main())
