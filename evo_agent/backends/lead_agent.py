"""The lead-agent bridge: an optional harness driving turns, Evo still deciding what may happen.

This is the DeerFlow integration in its shipped form (approved decision Q2), and the shape is the
whole point of it. The harness runs as a **separate interpreter process** under the project venv or
a dedicated one, confined by the same sandbox provider as a tool call, speaking line-delimited JSON
over stdin/stdout. It is not imported into Evo's process, because the alternative - an in-process
LangGraph graph - would hand it the same address space as the memory store, the approval gate, and
the promotion engine, and every "must not" in the specification would become a convention.

The protocol has three message kinds from the child:

* ``event`` - progress, forwarded verbatim to the sink so the audit trail is the same one;
* ``tool_request`` - an *ask*, never an action: it goes to :class:`ApprovalMediator`, and the child
  gets back either output or a refusal it must surface to its own model;
* ``final`` / ``error`` - the turn's outcome, marked ``origin="lead_agent"``.

Anything else the child sends is data. Keys that would claim an authority the bridge does not have -
``verdict``, ``satisfied``, ``approved`` - are stripped and recorded as
``bridge_overreach_rejected``, because "the harness said the goal was met" must never reach the
ledger looking like "Evo verified the goal was met". That single distinction is what makes the rest
of this file safe to wire up.

One turn-level loop remains Evo's (``I-single-loop``): the bridge is entered from the planner, it
runs to a bounded number of steps, and it returns. It does not loop over Evo's loop.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
import json
import os
from pathlib import Path
import selectors
import shutil
import subprocess
import time
from typing import Any, Callable, Sequence

from ..tools import canonical_tool_name
from ..ports.contracts import (
    ArtifactRef,
    BackendAvailability,
    BackendPlan,
    CapabilityRequest,
    ExecRequest,
    Receipt,
    TurnContext,
    TurnResult,
)
from ..sandbox_providers import IsolationUnavailable, prepare_launch
from ..sovereign.mediation import ApprovalMediator


#: What the child may not assert. See the module docstring: these are authorities, not opinions.
FORBIDDEN_CHILD_KEYS = ("verdict", "satisfied", "approved", "promotion_allowed")

#: The child's framing limit. An unbounded ``readline`` is a denial of service on the parent: a
#: harness that emits 4 GB of one line would be buffered here, in the process holding the audit
#: trail, before anything noticed.
DEFAULT_MAX_LINE_BYTES = 1 << 20

#: How long a probe may take before the bridge is called unusable. It starts an interpreter and
#: imports a harness, so it is seconds-scale; anything slower than this is not worth waiting for at
#: start-up, and a bridge whose probe needs a minute will be probed from a CLI anyway.
PROBE_TIMEOUT_SECONDS = 30.0


class LeadAgentConfigError(ValueError):
    """The bridge is configured in a way that cannot be honoured, said at startup."""


@dataclass
class _Turn:
    """In-flight turn state. Internal, and short-lived by construction."""

    turn_id: str
    started_at: float
    process: "subprocess.Popen[str] | None" = None
    receipts: list[Receipt] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    overreach: list[str] = field(default_factory=list)
    events: int = 0
    cancelled: bool = False
    text: str = ""

    def finish(self) -> float:
        return time.monotonic() - self.started_at


class LeadAgentBackend:
    """Drives an external lead-agent harness as a confined subprocess.

    ``venv_python`` is the interpreter the harness lives in. When it is ``None`` the current
    interpreter is used, which is the right thing for a test that drives the protocol with a fake
    driver and the wrong thing for a real DeerFlow install (its dependencies are not Evo's).
    """

    name = "lead_agent"

    def __init__(
        self,
        *,
        mediator: ApprovalMediator | None = None,
        workspace: Path | str | None = None,
        venv_python: Path | str | None = None,
        driver: Path | str | None = None,
        advertised_tools: Sequence[str] = (),
        required_imports: Sequence[str] = ("langgraph",),
        enabled: bool = False,
        turn_timeout_seconds: float = 300.0,
        max_line_bytes: int = DEFAULT_MAX_LINE_BYTES,
        environment: dict[str, str] | None = None,
        on_event: Callable[[str, dict[str, Any]], None] | None = None,
    ) -> None:
        if max_line_bytes < 1024:
            raise LeadAgentConfigError("max_line_bytes below 1 KiB cannot carry a tool result; raise it or disable the bridge")
        self.mediator = mediator
        self.workspace = Path(workspace or Path.cwd()).expanduser().resolve()
        self.venv_python = Path(venv_python).expanduser() if venv_python else None
        self.driver = Path(driver).expanduser() if driver else (Path(__file__).with_name("lead_agent_driver.py"))
        self.advertised_tools = tuple(dict.fromkeys(advertised_tools))
        self.required_imports = tuple(required_imports)
        self.enabled = bool(enabled)
        self.turn_timeout_seconds = float(max(5.0, min(turn_timeout_seconds, 3600.0)))
        self.max_line_bytes = int(max_line_bytes)
        self.environment = dict(environment or {})
        self.on_event = on_event
        self._probe_cache: tuple[float, BackendAvailability] | None = None
        self._probe_ttl = 30.0
        self._turns: dict[str, _Turn] = {}

    @property
    def disabled(self) -> bool:
        return not self.enabled

    @property
    def interpreter(self) -> Path:
        return self.venv_python if self.venv_python is not None else Path(shutil.which("python3") or "python3")

    # -- port: probe -------------------------------------------------------
    def probe(self) -> BackendAvailability:
        """Ask the bridge's own driver, in the sandbox, whether the harness is usable here.

        Cached briefly because it starts a process. The check is the harness's *import*, not the
        venv path's existence: a directory that contains a python binary is not evidence that
        DeerFlow works, and reporting it as such would move the failure into a mid-turn traceback.
        """
        now = time.monotonic()
        if self._probe_cache and now - self._probe_cache[0] < self._probe_ttl:
            return self._probe_cache[1]
        availability = self._probe_now()
        self._probe_cache = (now, availability)
        return availability

    def _probe_now(self) -> BackendAvailability:
        detail: dict[str, Any] = {
            "interpreter": str(self.interpreter),
            "driver": str(self.driver),
            "advertised_tools": list(self.advertised_tools),
            "required_imports": list(self.required_imports),
        }
        if not self.enabled:
            return BackendAvailability(self.name, False, "bridge is disabled (construct it with enabled=True); CLI/toml selection is a later phase", detail=detail)
        if not self.interpreter.is_file() and str(self.interpreter) != "python3":
            return BackendAvailability(self.name, False, f"interpreter not found at {self.interpreter}", detail=detail)
        if not self.driver.is_file():
            return BackendAvailability(self.name, False, f"driver script missing at {self.driver}", detail=detail)
        if self.mediator is None:
            return BackendAvailability(
                self.name,
                False,
                "no ApprovalMediator wired; the bridge refuses to run without a single execution authority",
                detail=detail,
            )
        launch, decision = self._launch(
            ExecRequest(
                argv=(str(self.interpreter), str(self.driver), "--probe"),
                cwd=self.workspace,
                timeout_seconds=min(30.0, self.turn_timeout_seconds),
                max_output_bytes=8192,
                env=dict(self.environment),
                label="lead_agent.probe",
            )
        )
        if launch is None:
            return BackendAvailability(self.name, False, f"probe refused: {decision.reason}", detail=detail)
        try:
            completed = subprocess.run(
                launch.argv,
                cwd=str(launch.cwd),
                env=launch.env,
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                timeout=PROBE_TIMEOUT_SECONDS,
                check=False,
            )
        except subprocess.TimeoutExpired:
            return BackendAvailability(self.name, False, "probe timed out", detail=detail)
        except OSError as exc:
            return BackendAvailability(self.name, False, f"probe failed to start: {exc}", detail=detail)
        payload = parse_last_json_line(completed.stdout)
        if payload is None:
            return BackendAvailability(
                self.name,
                False,
                f"driver probe returned no JSON ({(completed.stderr or completed.stdout).strip()[:160]})",
                detail=detail,
            )
        detail["driver_report"] = payload
        usable = bool(payload.get("ok")) and completed.returncode == 0
        if not usable:
            return BackendAvailability(self.name, False, str(payload.get("reason") or "driver reported not usable"), detail=detail)
        reason = "" if launch.isolated else f"running unconfined ({launch.degraded_reason or launch.provider})"
        return BackendAvailability(self.name, True, reason, detail=detail)

    # -- port: plan --------------------------------------------------------
    def plan_capability(self, request: CapabilityRequest) -> BackendPlan:
        """Serve only advertised tools, and only while degrading memory/verification to Evo.

        The degradation string is not a disclaimer. It records the actual asymmetry: this backend
        may produce text and request tools, while memory writes, verification, promotion, and
        rollback stay outside it. A planner that did not see that would route a memory-heavy goal
        here and lose the memories.
        """
        availability = self.probe()
        if not availability.available:
            return BackendPlan(False, availability.reason, degradation="unavailable")
        unknown = tuple(name for name in request.needed if self.advertised_tools and name not in self.advertised_tools)
        if unknown:
            return BackendPlan(
                False,
                f"this lead-agent configuration does not advertise {', '.join(unknown)}",
                degradation="capability not advertised",
            )
        approval = tuple(dict.fromkeys(request.permissions))
        return BackendPlan(
            True,
            "planner-driven sub-turns over an external harness; every execution still passes the mediator",
            estimated_turns=max(1, min(len(request.needed) + 1, 8)),
            requires_approval_for=approval,
            degradation="memory, verification, and promotion authority remain in Evo",
        )

    # -- port: run ---------------------------------------------------------
    def run_turn(self, context: TurnContext, sink: Any = None) -> TurnResult:
        """Run one bounded turn. Returns a :class:`TurnResult`; never a verdict."""
        if self.mediator is None:
            return TurnResult(status="refused", text="no ApprovalMediator wired", origin=self.name)
        request_line = json.dumps(
            {
                "goal": context.goal,
                "turn_id": context.turn_id,
                "task_id": context.task_id,
                "history": [dict(item) for item in context.history],
                "tools": list(context.available_tools or self.advertised_tools),
                "budget": {
                    "turns": context.budget_turns,
                    "deadline_seconds": context.remaining_seconds(),
                    "max_parallel_tool_calls": 1,
                },
                "workspace": str(context.workspace or self.workspace),
            },
            sort_keys=True,
        )
        launch, decision = self._launch(
            ExecRequest(
                argv=(str(self.interpreter), str(self.driver), "--turn"),
                cwd=self.workspace,
                timeout_seconds=self.turn_timeout_seconds,
                max_output_bytes=self.max_line_bytes,
                env=dict(self.environment),
                label="lead_agent.turn",
                task_id=context.task_id,
            )
        )
        if launch is None:
            return TurnResult(status="refused", text=decision.text, origin=self.name, notes=(f"rule={decision.rule}",))
        turn = _Turn(turn_id=context.turn_id, started_at=time.monotonic())
        self._turns[context.turn_id] = turn
        try:
            process = subprocess.Popen(
                launch.argv,
                cwd=str(launch.cwd),
                env=launch.env,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                start_new_session=True,
            )
        except OSError as exc:
            turn.notes.append(f"spawn failed: {type(exc).__name__}: {exc}")
            return TurnResult(status="failed", text=turn.notes[-1], origin=self.name, notes=tuple(turn.notes))
        turn.process = process
        status = self._pump(process, request_line, turn, sink, launch, context)
        duration_ms = turn.finish() * 1000.0
        notes = list(turn.notes)
        if not launch.isolated:
            notes.append(f"unconfined child ({launch.degraded_reason or launch.provider})")
        if turn.overreach:
            notes.append(f"rejected {len(turn.overreach)} child message(s) claiming authority")
        return TurnResult(
            status=status,
            text=turn.text,
            receipts=tuple(turn.receipts),
            artifacts=self._artifacts(context),
            usage={"duration_ms": round(duration_ms, 3), "events": turn.events, "tool_calls": len(turn.receipts)},
            notes=tuple(notes),
            origin=self.name,
        )

    def _pump(self, process: "subprocess.Popen[str]", request_line: str, turn: _Turn, sink: Any, launch: Any, context: TurnContext) -> str:
        """Write the request, then service the child's line protocol until it finishes.

        Reads are ``os.read`` on the raw descriptor with the line splitting done here, not
        ``readline()`` on the text wrapper. The difference is a deadlock: the wrapper happily buffers
        the second line of a two-line burst, after which ``select`` never reports readable again
        because the kernel has nothing left to hand over - so a child that asks for a tool while its
        first reply is still buffered waits forever for an answer that is stuck behind the reader.
        One layer, one buffer, no waiting-on-the-wrong-thing.

        The child's stderr is folded into the same stream: an unread 64 KiB of traceback would
        otherwise block the child writing it, and a hung bridge is harder to diagnose than a noisy
        one.
        """
        deadline = time.monotonic() + self.turn_timeout_seconds
        stdin_fd = process.stdin.fileno() if process.stdin is not None else None
        stdout_fd = process.stdout.fileno()
        selector = selectors.DefaultSelector()
        selector.register(stdout_fd, selectors.EVENT_READ)
        buffer = b""
        status = "failed"
        try:
            if stdin_fd is None:
                turn.notes.append("child stdin was not piped; cannot start the turn")
                return "failed"
            os.write(stdin_fd, request_line.encode("utf-8") + b"\n")
            while True:
                if turn.cancelled:
                    status = "cancelled"
                    break
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    turn.notes.append(f"turn exceeded {self.turn_timeout_seconds}s")
                    status = "timeout"
                    break
                if not selector.select(min(remaining, 0.25)):
                    if process.poll() is not None:
                        status = "completed" if turn.text else "failed"
                        break
                    continue
                try:
                    chunk = os.read(stdout_fd, 65_536)
                except OSError as exc:
                    turn.notes.append(f"child stdout failed: {exc}")
                    status = "failed"
                    break
                if not chunk:
                    status = "completed" if (turn.text or process.poll() == 0) else "failed"
                    break
                buffer += chunk
                aborting = False
                while b"\n" in buffer:
                    raw_line, buffer = buffer.split(b"\n", 1)
                    if len(raw_line) > self.max_line_bytes:
                        turn.notes.append(f"child line exceeded {self.max_line_bytes} bytes; aborting turn")
                        status, aborting = "failed", True
                        buffer = b""
                        break
                    message = decode_json_line(raw_line.decode("utf-8", errors="replace"))
                    if message is None:
                        # Not protocol: the child's own traceback or a warning. Kept briefly, then
                        # dropped - a chatty harness must not push the turn's notes over any bound.
                        if len(turn.notes) < 6:
                            turn.notes.append(f"ignored non-protocol child output: {raw_line[:160].decode('utf-8', errors='replace')}")
                        continue
                    terminal = self._handle_child_message(message, process, turn, sink, context)
                    if terminal is not None:
                        status, aborting = terminal, True
                        break
                if aborting:
                    break
        finally:
            selector.close()
            self._drain_and_close(process)
        return status if status in {"completed", "failed", "timeout", "cancelled"} else "failed"

    def _handle_child_message(self, message: dict[str, Any], process: "subprocess.Popen[str]", turn: _Turn, sink: Any, context: TurnContext) -> str | None:
        """Apply one protocol message. Returns a terminal status, or None to keep going."""
        kind = str(message.get("type") or "")
        stripped = sanitize_child_message(message)
        if stripped:
            turn.overreach.extend(stripped)
            self._emit(
                "bridge_overreach_rejected",
                {"turn_id": turn.turn_id, "keys": stripped, "type": kind, "warning": "a bridge may not grant itself an authority"},
            )
            if kind == "verdict":
                turn.notes.append("child sent a verdict; verification stays in Evo and it was ignored")
        if kind == "event":
            turn.events += 1
            forward_to_sink(sink, str(message.get("event") or "lead_agent_event"), dict(message.get("payload") or {}))
            return None
        if kind == "tool_request":
            self._service_tool_request(process, message, turn, context)
            return None
        if kind == "final":
            turn.text = str(message.get("text") or "")
            return "completed"
        if kind == "error":
            turn.text = str(message.get("message") or "driver reported an error")
            turn.notes.append("child reported an error")
            return "failed"
        if len(turn.notes) < 6:
            turn.notes.append(f"ignored unknown child message kind '{kind or 'missing'}'")
        return None

    def _service_tool_request(self, process: "subprocess.Popen[str]", message: dict[str, Any], turn: _Turn, context: TurnContext) -> None:
        """One ask from the child, through the mediator, answered on stdout.

        The reply is written even for refusals. A silent drop would leave the harness waiting on a
        tool result, and its timeout - not Evo's judgement - would decide what the user sees.
        """
        call_id = str(message.get("id") or len(turn.receipts) + 1)
        tool_name = str(message.get("tool") or "")
        # Resolved before the request is built, so the label the mediator decides under, the label on
        # the receipt, and the label in the audit are one name rather than three spellings of what the
        # child happened to type. An unresolved name is refused: defaulting a child's "write" to
        # "shell" would silently swap a file operation for a process launch, and defaulting
        # unknown-name to the *strictest* rule is only safe until somebody needs the tool.
        canonical, why = canonical_tool_name(tool_name, self.advertised_tools)
        if not canonical:
            reply = {
                "type": "tool_response",
                "id": call_id,
                "ok": False,
                "output": "",
                "error": f"tool name refused at the boundary: {why}",
            }
            turn.notes.append(f"refused tool_request '{tool_name}': {why}")
            self._write(process, reply, turn)
            return
        arguments = dict(message.get("arguments") or {})
        started = time.monotonic()
        argv = message.get("argv")
        if not isinstance(argv, (list, tuple)) or not argv:
            reply = {"type": "tool_response", "id": call_id, "ok": False, "output": "", "error": "tool_request must carry an argv list; there is no shell to interpret a command line"}
            turn.notes.append(f"rejected malformed tool_request for '{tool_name}'")
            self._write(process, reply, turn)
            return
        request = ExecRequest(
            argv=tuple(str(item) for item in argv),
            cwd=Path(str(message.get("cwd") or context.workspace or self.workspace)),
            timeout_seconds=float(message.get("timeout_seconds") or 30.0),
            env=dict(message.get("env") or {}),
            label=f"lead_agent.{tool_name}",
            task_id=context.task_id,
        )
        result = self.mediator.execute(request, tool_name=canonical, arguments=arguments) if self.mediator else None
        if result is None:
            reply = {"type": "tool_response", "id": call_id, "ok": False, "output": "", "error": "no mediator wired"}
        else:
            reply = {
                "type": "tool_response",
                "id": call_id,
                "ok": bool(result.ok) and not result.refusal,
                "output": result.output,
                "error": result.refusal or ("" if result.ok else f"exit {result.returncode}"),
                "isolated": result.isolated,
                "provider": result.provider,
            }
            turn.receipts.append(
                Receipt.record(
                    ledger_seq=len(turn.receipts) + 1,
                    turn_id=turn.turn_id,
                    tool=tool_name,
                    canonical_name=canonical,
                    kind="execute",
                    arguments={"argv": list(request.argv), "cwd": str(request.cwd), **arguments},
                    output=result.output,
                    ok=bool(result.ok) and not result.refusal,
                    duration_ms=(time.monotonic() - started) * 1000.0,
                    isolation=result.provider if result.isolated else f"unconfined:{result.provider}",
                    notes=tuple(list(result.notes) + ([f"refused: {result.refusal}"] if result.refusal else [])),
                )
            )
        self._write(process, reply, turn)

    def _write(self, process: "subprocess.Popen[str]", payload: dict[str, Any], turn: _Turn) -> None:
        """Send one line to the child, on the same raw descriptor the reader uses.

        Buffered and unbuffered writes must not both be aimed at one pipe: the ordering of a
        ``TextIOWrapper.flush`` against an ``os.write`` is not defined, and a reply that arrives
        before its request looks, to the child, like a protocol violation by the parent.
        """
        if process.stdin is None:
            turn.notes.append("child stdin was closed; could not answer")
            return
        try:
            os.write(process.stdin.fileno(), json.dumps(payload, sort_keys=True).encode("utf-8") + b"\n")
        except (BrokenPipeError, OSError) as exc:
            turn.notes.append(f"child stopped accepting input: {exc}")

    def _launch(self, request: ExecRequest) -> tuple[Any, Any]:
        """Wrap a child launch through the mediator's infrastructure path.

        Two things are checked here and neither is negotiable: the program must be the configured
        driver, and the launch must be confined if the enforcement level demands it. Everything the
        child then wants to *run* is a separate request through the mediator, so this function never
        needs to know what the harness is capable of.
        """
        program = str(self.interpreter)
        if self.mediator is None:
            settings = IsolationSettings()
            try:
                return prepare_launch(request, settings=settings), None
            except IsolationUnavailable as exc:
                return None, type("Denial", (), {"allowed": False, "reason": str(exc), "rule": "no_mediator", "text": str(exc)})()
        return self.mediator.prepare_infrastructure(request, program=program, tool_name=self.name)

    def cancel(self, turn_id: str, reason: str = "operator") -> bool:
        """Signal a running turn's process group. Returns False if there is nothing to cancel."""
        turn = self._turns.get(turn_id)
        if turn is None:
            return False
        turn.cancelled = True
        turn.notes.append(f"cancelled ({reason})")
        if turn.process is not None:
            self._kill(turn.process)
        self._emit("bridge_turn_cancelled", {"turn_id": turn_id, "reason": reason})
        return True

    def export_receipts(self, turn_id: str) -> Sequence[Receipt]:
        turn = self._turns.get(turn_id)
        return tuple(turn.receipts) if turn else ()

    def _artifacts(self, context: TurnContext) -> tuple[ArtifactRef, ...]:
        """Files the turn left behind in the workspace, by mtime window.

        Reported so a reviewer can see what a harness touched without diffing the workspace
        afterwards. It is an observation, not an authority: the verifier decides whether any of it
        satisfies the goal.
        """
        root = Path(context.workspace or self.workspace)
        if not root.is_dir():
            return ()
        since = context.metadata.get("started_at") if isinstance(context.metadata, dict) else None
        cutoff = float(since) if since else time.time() - max(1.0, turn_duration_seconds_guess(self.turn_timeout_seconds))
        found: list[ArtifactRef] = []
        try:
            for path in sorted(root.rglob("*")):
                if not path.is_file() or ".evo" in path.parts:
                    continue
                try:
                    stat = path.stat()
                except OSError:
                    continue
                if stat.st_mtime >= cutoff:
                    found.append(ArtifactRef(path=str(path.relative_to(root)), kind="file", size_bytes=stat.st_size))
                if len(found) >= 64:
                    break
        except OSError:
            return tuple(found)
        return tuple(found)

    @staticmethod
    def _kill(process: "subprocess.Popen[str]") -> None:
        from ..sandbox_providers.base import terminate

        terminate(process)

    def _drain_and_close(self, process: "subprocess.Popen[str]") -> None:
        """Kill anything still running, then close. A child that outlives its turn is not confined.

        The kill comes first on purpose: the ordinary exit path leaves the process already reaped,
        while every failure path - timeout, oversized line, unknown message - leaves a live process
        tree that would otherwise keep the workspace writable after Evo reported the turn over.
        """
        try:
            if process.poll() is None:
                self._kill(process)
            for stream in (process.stdout, process.stdin, process.stderr):
                if stream is not None:
                    try:
                        stream.close()
                    except OSError:
                        pass
        except OSError:
            pass

    def _emit(self, event: str, payload: dict[str, Any]) -> None:
        if self.on_event is None:
            return
        try:
            self.on_event(event, payload)
        except Exception:
            pass


def turn_duration_seconds_guess(budget: float) -> float:
    """A generous look-back window for artifact discovery. Deliberately not exact.

    The mtime window is a heuristic for "what this turn may have touched", so it errs wide and lets
    the verifier decide relevance. Pretending it is precise would put false confidence in a field a
    reviewer will read as evidence.
    """
    return float(max(60.0, budget))


def sanitize_child_message(message: dict[str, Any]) -> list[str]:
    """Strip authority claims from a child message, returning what was removed.

    Removal happens at the boundary rather than at the consumers so that no future reader of
    ``TurnResult.usage`` has to remember to ignore a ``satisfied`` key that should never have been
    copied in the first place.
    """
    removed = [key for key in FORBIDDEN_CHILD_KEYS if key in message]
    for key in removed:
        message.pop(key, None)
    return removed


def forward_to_sink(sink: Any, event: str, payload: dict[str, Any]) -> None:
    """Best-effort ``emit`` on the caller's sink, accepting the shapes the runtime actually passes."""
    for name in ("emit", "record"):
        method = getattr(sink, name, None)
        if callable(method):
            try:
                method(event, payload)
            except TypeError:
                try:
                    method({"type": event, "payload": payload})
                except Exception:
                    pass
            except Exception:
                pass
            return
    if isinstance(sink, dict):  # a plain collector, handy in tests and CLI dry runs
        sink.setdefault("events", []).append({"type": event, "payload": payload})


def decode_json_line(line: str) -> dict[str, Any] | None:
    text = line.strip()
    if not text:
        return None
    try:
        payload = json.loads(text)
    except (ValueError, TypeError):
        return None
    return payload if isinstance(payload, dict) else None


def parse_last_json_line(text: str | None) -> dict[str, Any] | None:
    """The last JSON object a child printed. Probes log warnings first, so scan from the end."""
    for line in reversed((text or "").splitlines()):
        decoded = decode_json_line(line)
        if decoded is not None:
            return decoded
    return None
