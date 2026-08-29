"""The unconfined fallback - kept in the provider set so that it is visible, selectable, and denied.

A provider list that silently has no entry for "no isolation" is how a system ends up running
unconfined by omission: the caller cannot distinguish "I asked and got it" from "nobody offered
it". Here the absence of confinement is a named choice with a reason, and its use is reported
through the same ``on_event`` channel the isolated providers use, so ``SECURITY_DEGRADED`` lands
in the audit trail either way.
"""

from __future__ import annotations

from pathlib import Path
import subprocess
import sys
import time
from typing import Any, Callable

from ..ports.contracts import ExecRequest, ExecResult, ProviderAvailability
from .base import ConfinedLaunch, format_notes, merge_streams, sanitized_environment, terminate


class HostProvider:
    """Runs the command in this process's environment. Refuses unless explicitly permitted."""

    name = "host"

    def __init__(self, *, permitted: bool = False, permit_reason: str = "") -> None:
        self.permitted = bool(permitted)
        self.permit_reason = permit_reason

    def probe(self) -> ProviderAvailability:
        if not self.permitted:
            return ProviderAvailability(
                self.name,
                False,
                "host execution is not permitted; set sandbox_enforcement='degrade' or 'off' explicitly",
                supports_network_denial=False,
                supports_read_only_mounts=False,
                supports_pid_namespace=False,
                detail={"permitted": False, "platform": sys.platform},
            )
        return ProviderAvailability(
            self.name,
            True,
            f"unconfined by design ({self.permit_reason or 'operator override'})",
            supports_network_denial=False,
            supports_read_only_mounts=False,
            supports_pid_namespace=False,
            detail={"permitted": True, "platform": sys.platform},
        )

    def prepare(self, request: ExecRequest) -> ConfinedLaunch:
        """The unconfined launch: same shape, so callers never special-case "no sandbox".

        Returning a launch instead of raising is deliberate. A bridge that has to branch on
        "am I confined here" will get that branch wrong under pressure; taking the same
        :class:`ConfinedLaunch` with ``isolated=False`` and a reason keeps the audit honest.
        """
        environment = sanitized_environment(dict(request.env))
        return ConfinedLaunch(
            argv=list(request.argv),
            env=environment,
            cwd=Path(request.cwd),
            provider=self.name,
            isolated=False,
            notes=(
                "unconfined: read-only mounts are not enforced by the host provider",
                "unconfined: network access is not denied by the host provider",
            ),
            degraded_reason=self.permit_reason or "host execution permitted by policy",
        )

    def run(self, request: ExecRequest, on_event: Callable[[str], None] | None = None) -> ExecResult:
        availability = self.probe()
        if not availability.usable:
            return ExecResult(returncode=-1, provider=self.name, refusal=availability.reason)
        environment = sanitized_environment(dict(request.env))
        notes = [
            "unconfined: read-only mounts are not enforced by the host provider",
            "unconfined: network access is not denied by the host provider",
        ]
        started = time.monotonic()
        process: subprocess.Popen[Any] | None = None
        try:
            process = subprocess.Popen(
                list(request.argv),
                cwd=str(Path(request.cwd)),
                env=environment,
                stdin=subprocess.PIPE if request.stdin is not None else subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                start_new_session=True,
            )
        except FileNotFoundError as exc:
            return ExecResult(returncode=-1, provider=self.name, refusal=f"command not found: {exc}", isolated=False)
        except OSError as exc:
            return ExecResult(returncode=-1, provider=self.name, refusal=f"could not start: {type(exc).__name__}: {exc}", isolated=False)
        timed_out = False
        try:
            stdout, stderr = process.communicate(input=request.stdin, timeout=request.timeout_seconds)
        except subprocess.TimeoutExpired:
            timed_out = True
            terminate(process)
            stdout, stderr = "", ""
        duration_ms = (time.monotonic() - started) * 1000.0
        output, truncated = merge_streams(stdout or "", stderr or "", request.max_output_bytes)
        returncode = process.returncode if process.returncode is not None else -1
        if timed_out:
            notes.append(f"terminated after {request.timeout_seconds}s")
            returncode = -1
        if truncated:
            notes.append("output truncated")
        if on_event is not None:
            for note in notes:
                on_event(note)
        return ExecResult(
            returncode=returncode,
            output=output,
            isolated=False,
            provider=self.name,
            degraded_reason="host execution permitted by policy",
            truncated=truncated,
            duration_ms=duration_ms,
            notes=format_notes(notes),
        )

    def terminate(self, handle: Any, grace_seconds: float = 2.0) -> bool:
        return terminate(handle, grace_seconds)
