"""DeepSeek Harness as a process adapter: same confinement, no shared state, no verdict (07 §4).

The harness is a Node/TypeScript monorepo whose authority lives in a session log and a SQLite
persistence layer. Porting that into Evo would duplicate the store Evo already has - and a duplicate
store is worse than no store, because the two copies drift and the audit cannot tell which one a
fact came from. So this adapter stays a *process* adapter: one invocation per turn, its stdout as an
observation, and its own session state left on its own disk.

What the adapter keeps from the upstream is the discipline rather than the code. DeepSeek Harness
treats its invariants as fatal: a violation is an unrecoverable error in the harness's own process.
Preserving that means not letting a violation become a silent partial result here, which is why an
``INVARIANT`` line in the child's output fails the turn and is recorded, even though the child
exited 0. An adapter that quietly swallowed the harness's loudest signal would have removed the
only thing that made it worth imitating.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import json
from pathlib import Path
import re
import shutil
from typing import Any, Callable, Sequence

from ..ports.contracts import (
    BackendAvailability,
    BackendPlan,
    CapabilityRequest,
    ExecRequest,
    Receipt,
    TurnContext,
    TurnResult,
)
from ..sovereign.mediation import ApprovalMediator


#: Markers that mean "the harness considered this fatal", whatever exit code it used.
INVARIANT_MARKERS = ("InvariantFailure", "INVARIANT", "invariant violation")

#: Placeholder syntax in an argument template. Anything else is passed through verbatim, because a
#: template is configuration and must not become a shell.
TEMPLATE_FIELDS = ("goal", "workspace", "turn_id", "task_id")


class HarnessConfigError(ValueError):
    """The configured command line cannot be honoured."""


@dataclass(frozen=True)
class HarnessCommand:
    """A rendered argv, with the substitutions that produced it kept for the record."""

    argv: tuple[str, ...]
    substituted: dict[str, str] = field(default_factory=dict)


def render_template(template: Sequence[str], values: dict[str, str]) -> HarnessCommand:
    """Substitute ``{goal}``-style fields into an argv template. No shell, no eval.

    Rejects an empty result rather than leaving a hole: a template of ``["dsh", "{cwd}"]`` would
    otherwise send a literal ``{cwd}`` to the harness and let it decide what to do about that.
    """
    used: dict[str, str] = {}
    rendered: list[str] = []
    for item in template:
        text = str(item)
        unknown = sorted({name for name in re.findall(r"\{(\w+)\}", text) if name not in TEMPLATE_FIELDS})
        if unknown:
            raise HarnessConfigError(
                f"unknown template placeholder(s) {', '.join(unknown)} in {item!r}; "
                f"the only substitutions are {', '.join(TEMPLATE_FIELDS)}"
            )
        for field_name in TEMPLATE_FIELDS:
            marker = "{" + field_name + "}"
            if marker in text:
                value = str(values.get(field_name) or "")
                if not value:
                    raise HarnessConfigError(f"template field '{field_name}' has no value to substitute")
                text = text.replace(marker, value)
                used[field_name] = value
        rendered.append(text)
    if not rendered or not any(rendered):
        raise HarnessConfigError("rendered command line is empty")
    return HarnessCommand(argv=tuple(rendered), substituted=used)


class DeepSeekHarnessBackend:
    """Runs ``deepseek-harness`` (or any configured CLI) once per turn, confined.

    Disabled by default. That is not shyness about the integration: an adapter whose upstream is a
    tool-use harness will happily execute whatever its model asks for, and until an operator has
    read the confinement and approval wiring for themselves, the safe state is off.
    """

    name = "dsh"

    def __init__(
        self,
        *,
        executable: str = "deepseek-harness",
        arguments_template: Sequence[str] = ("--prompt", "{goal}", "--workspace", "{workspace}"),
        version_argument: str = "--version",
        workspace: Path | str | None = None,
        mediator: ApprovalMediator | None = None,
        enabled: bool = False,
        turn_timeout_seconds: float = 180.0,
        max_output_bytes: int = 200_000,
        on_event: Callable[[str, dict[str, Any]], None] | None = None,
        advertise: Sequence[str] = ("execute", "read", "write"),
    ) -> None:
        self.executable = str(executable)
        self.arguments_template = tuple(str(item) for item in arguments_template)
        self.version_argument = str(version_argument)
        self.workspace = Path(workspace or Path.cwd()).expanduser().resolve()
        self.mediator = mediator
        self.enabled = bool(enabled)
        self.turn_timeout_seconds = float(max(5.0, min(turn_timeout_seconds, 3600.0)))
        self.max_output_bytes = int(max(1024, max_output_bytes))
        self.on_event = on_event
        self.advertise = tuple(advertise)
        self._receipts: dict[str, tuple[Receipt, ...]] = {}

    @property
    def disabled(self) -> bool:
        return not self.enabled

    def probe(self) -> BackendAvailability:
        """Is the CLI on PATH, and does it report a version when asked inside the sandbox?

        The version check is run *confined*, because that is how it will be used. Probing the binary
        directly would report a harness that works on the host and cannot work in the namespace.
        """
        detail: dict[str, Any] = {"executable": self.executable, "template": list(self.arguments_template)}
        if not self.enabled:
            return BackendAvailability(self.name, False, "process adapter is disabled (construct it with enabled=True); CLI/toml selection is a later phase", detail=detail)
        if self.mediator is None:
            return BackendAvailability(self.name, False, "no ApprovalMediator wired; refusing to run a harness without an execution authority", detail=detail)
        found = shutil.which(self.executable)
        if not found:
            return BackendAvailability(
                self.name,
                False,
                f"'{self.executable}' is not on PATH (external, node-based; install or vendor out-of-band)",
                detail=detail,
            )
        detail["path"] = found
        version = self._run((found, self.version_argument), label="dsh.version", program=self.executable)
        if version is None or version.returncode != 0:
            reason = (version.output[:200] if version is not None else "version probe failed") or "version probe failed"
            return BackendAvailability(self.name, True, f"installed but version probe did not succeed: {reason}", detail=detail)
        detail["version"] = version.output.strip().splitlines()[0][:120] if version.output.strip() else ""
        return BackendAvailability(self.name, True, "", detail=detail)

    def plan_capability(self, request: CapabilityRequest) -> BackendPlan:
        """Serve only the handful of verbs a one-shot CLI invocation can honestly cover."""
        availability = self.probe()
        if not availability.available:
            return BackendPlan(False, availability.reason, degradation="unavailable")
        unsupported = tuple(name for name in request.needed if name not in self.advertise)
        if unsupported:
            return BackendPlan(
                False,
                f"a single harness invocation cannot provide {', '.join(unsupported)}; it has no memory or verification authority",
                degradation="outside the adapter's declared verbs",
            )
        return BackendPlan(
            True,
            "one confined CLI invocation per turn, as an external process with its own session state",
            estimated_turns=1,
            requires_approval_for=tuple(dict.fromkeys(request.permissions)),
            degradation="its session log is not Evo's memory; nothing here is retrievable until verified",
        )

    def run_turn(self, context: TurnContext, sink: Any = None) -> TurnResult:
        """One invocation, one result. Marked ``origin="dsh"``, never a verdict."""
        if self.mediator is None:
            return TurnResult(status="refused", text="no ApprovalMediator wired", origin=self.name)
        command = render_template(
            (self.executable, *self.arguments_template),
            {"goal": context.goal, "workspace": str(context.workspace or self.workspace), "turn_id": context.turn_id, "task_id": context.task_id},
        )
        # No pre-flight policy check on the *rendered* command, and that is a decision rather than
        # an omission. The command allowlist exists to constrain what a model asks to execute; here
        # the program is operator configuration and the model only supplies an argument, so the
        # guards that matter are the two that are applied: the launch must be the configured program
        # (mediator, infrastructure rule) and must match the configured template (below). Content
        # rules on the prompt text would be Evo guessing what a foreign harness finds dangerous.
        # Reconstruct the argv against the template before running it: the child may receive the
        # goal as data, but it may not receive a different program or extra operator-unapproved
        # arguments, and "compare the shape back to what was configured" is how that is enforced
        # without trusting the renderer.
        if not self._template_holds(command):
            return TurnResult(status="refused", text="rendered command does not match the configured template", origin=self.name, notes=("rule=template_mismatch",))
        result = self._run(command.argv, label="dsh.turn", timeout=self.turn_timeout_seconds, max_bytes=self.max_output_bytes, program=self.executable)
        if result is None:
            return TurnResult(status="failed", text="harness could not be launched", origin=self.name)
        if result.refusal:
            return TurnResult(status="refused", text=result.refusal, origin=self.name)
        enforcement = getattr(self.mediator.policy, "sandbox_enforcement", "auto")
        if enforcement == "strict" and not result.isolated:
            # Checked again after the fact, because the *launch* is what proves it. A policy that
            # said strict while a provider silently fell back to the host would otherwise be
            # reported as a satisfied requirement.
            return TurnResult(
                status="refused",
                text="strict enforcement requires isolation and the child ran unconfined",
                origin=self.name,
                notes=(f"provider={result.provider}",),
            )
        output = result.output
        violated = [marker for marker in INVARIANT_MARKERS if marker in output]
        receipts = (
            Receipt.record(
                ledger_seq=1,
                turn_id=context.turn_id,
                tool="dsh",
                canonical_name="execute.external_cli",
                kind="execute",
                arguments={"argv": list(command.argv), "template": list(self.arguments_template)},
                output=output,
                ok=result.ok and not violated,
                duration_ms=result.duration_ms,
                isolation=result.provider if result.isolated else f"unconfined:{result.provider}",
                notes=tuple([f"invariant markers: {', '.join(violated)}"] if violated else []),
            ),
        )
        self._receipts[context.turn_id] = receipts
        notes = [
            "process adapter: the harness keeps its own session state",
            f"argv rendered from template with {len(command.substituted)} substitution(s)",
        ]
        if violated:
            notes.append(f"harness reported {', '.join(violated)}; the turn is failed regardless of exit code")
            self._emit("dsh_invariant_violation", {"turn_id": context.turn_id, "markers": violated, "returncode": result.returncode})
        status = "completed" if (result.ok and not violated) else "failed"
        try:
            from ..ports.contracts import call_optional

            call_optional(sink, "emit", "dsh_turn_finished", {"turn_id": context.turn_id, "status": status}, default=None)
        except Exception:
            pass
        return TurnResult(status=status, text=output, receipts=receipts, notes=tuple(notes), origin=self.name)

    def export_receipts(self, turn_id: str) -> Sequence[Receipt]:
        return tuple(self._receipts.get(turn_id, ()))

    def _template_holds(self, command: HarnessCommand) -> bool:
        """Whether the rendered argv is the configured template with only its substitutions filled.

        A renderer bug, or a future change that let a field smuggle in an extra ``--some-flag``,
        shows up here as a failed comparison instead of as a command the operator never wrote. It
        proves nothing was *added*; it is a shape check, not a parser, and the program identity is
        checked separately by the mediator's infrastructure rule.
        """
        # Substitute *back* by substring, longest value first. A whole-element lookup would fail on
        # the common case of a field embedded in a longer argument (``"prompt: {goal}"``), which
        # reads as a mismatch and would make the guard refuse every real invocation.
        rebuilt = list(command.argv)
        for key, value in sorted(command.substituted.items(), key=lambda item: -len(str(item[1]))):
            rebuilt = [str(part).replace(str(value), "{" + key + "}") for part in rebuilt]
        return tuple(rebuilt) == (self.executable, *self.arguments_template)

    def _run(
        self,
        argv: Sequence[str],
        *,
        label: str,
        program: str,
        timeout: float = 30.0,
        max_bytes: int = 8192,
    ) -> Any:
        """Run one configured command confined. Returns ``ExecResult`` or ``None``.

        ``program`` is what the operator configured, and it is passed separately from ``argv`` on
        purpose: comparing the two is the check, so a caller that built argv from the program itself
        would be asserting nothing.
        """
        request = ExecRequest(
            argv=tuple(str(item) for item in argv),
            cwd=self.workspace,
            timeout_seconds=timeout,
            max_output_bytes=max_bytes,
            label=label,
        )
        if self.mediator is None:
            return None
        return self.mediator.execute_infrastructure(request, program=program, tool_name=self.name)

    def _emit(self, event: str, payload: dict[str, Any]) -> None:
        if self.on_event is None:
            return
        try:
            self.on_event(event, payload)
        except Exception:
            pass


def describe_template(template: Sequence[str]) -> str:
    """Render a template for a status report, so the printed command is the one that will run."""
    return " ".join(json.dumps(str(item)) if any(char in str(item) for char in ' "{}') else str(item) for item in template)
