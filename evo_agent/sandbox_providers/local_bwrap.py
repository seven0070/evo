"""Bubblewrap provider: the strongest confinement available without privileges.

Mirrors the flag set that ``SandboxEngine`` uses for evolution candidates, because two
confinement mechanisms in one codebase must not differ in the security-relevant flags -
``test_isolation_flags_match_the_candidate_sandbox`` asserts they do not drift.

``--unshare-user-try`` (not ``--unshare-user``) is deliberate: on a runner that already holds a
user namespace, requesting a fresh one fails, whereas "-try" reuses what exists. Some hosted
runners ship bwrap but deny uid-map writes; the probe catches that and the registry falls through
to ``unshare`` or refuses, per enforcement level.
"""

from __future__ import annotations

from pathlib import Path
import shutil
import subprocess
import time
from typing import Any, Callable

from ..ports.contracts import ExecRequest, ExecResult, ProviderAvailability
from .base import (
    ConfinedLaunch,
    child_tmp_directory,
    merge_streams,
    format_notes,
    sanitized_environment,
    terminate,
    usable_from_probe,
)

PROBE_TIMEOUT_SECONDS = 5.0


class LocalBwrapProvider:
    """Filesystem and namespace confinement via ``bwrap``."""

    name = "local_bwrap"

    def __init__(self, *, deny_network: bool = True) -> None:
        self.deny_network = deny_network

    def probe(self) -> ProviderAvailability:
        executable = shutil.which("bwrap")
        if not executable:
            return ProviderAvailability(self.name, False, "bwrap is not installed")
        usable, reason = usable_from_probe(
            [
                executable,
                "--die-with-parent",
                "--unshare-user-try",
                "--unshare-net",
                "--unshare-pid",
                "--ro-bind",
                "/",
                "/",
                "--dev",
                "/dev",
                "--proc",
                "/proc",
                "true",
            ],
            timeout=PROBE_TIMEOUT_SECONDS,
        )
        return ProviderAvailability(
            name=self.name,
            usable=usable,
            reason=reason,
            supports_network_denial=True,
            supports_read_only_mounts=True,
            supports_pid_namespace=True,
            detail={"executable": executable, "probe": "bwrap ... true"},
        )

    def command_for(self, request: ExecRequest, scratch: Path) -> list[str]:
        """The bwrap argv for one request. Exposed for tests and for parity assertions."""
        workspace = Path(request.cwd)
        command = [
            "bwrap",
            "--die-with-parent",
            "--unshare-user-try",
            "--unshare-pid",
            "--ro-bind",
            "/",
            "/",
            "--dev",
            "/dev",
            "--proc",
            "/proc",
            "--setenv",
            "HOME",
            str(scratch),
            "--setenv",
            "TMPDIR",
            str(scratch),
            "--setenv",
            "EVO_SANDBOX_PROVIDED",
            self.name,
        ]
        if self.deny_network and not request.network:
            command += ["--unshare-net"]
        for path in (workspace, *request.writable, scratch):
            resolved = Path(path)
            if resolved.exists():
                command += ["--bind", str(resolved), str(resolved)]
        command += ["--chdir", str(workspace), *request.argv]
        return command

    def prepare(self, request: ExecRequest) -> ConfinedLaunch:
        """Wrap the request for a caller-managed process (no shell, explicit mounts)."""
        scratch = child_tmp_directory(Path(request.cwd))
        environment = sanitized_environment({"HOME": str(scratch), "TMPDIR": str(scratch), **request.env})
        return ConfinedLaunch(
            argv=self.command_for(request, scratch),
            env=environment,
            cwd=Path(request.cwd),
            provider=self.name,
            isolated=True,
            scratch=scratch,
        )

    def run(self, request: ExecRequest, on_event: Callable[[str], None] | None = None) -> ExecResult:
        availability = self.probe()
        if not availability.usable:
            return ExecResult(returncode=-1, provider=self.name, refusal=availability.reason)
        if request.network:
            return ExecResult(
                returncode=-1,
                provider=self.name,
                refusal="no network-capable isolation provider is configured; refusing to run an "
                "egress-capable command inside a provider that silently denies it",
            )
        launch = self.prepare(request)
        started = time.monotonic()
        process: subprocess.Popen[Any] | None = None
        try:
            process = subprocess.Popen(
                launch.argv,
                cwd=str(launch.cwd),
                env=launch.env,
                stdin=subprocess.PIPE if request.stdin is not None else subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                start_new_session=True,
            )
        except OSError as exc:
            return ExecResult(
                returncode=-1,
                provider=self.name,
                refusal=f"bwrap could not start: {type(exc).__name__}: {exc}",
                isolated=False,
            )
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
        notes: list[str] = []
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
            isolated=True,
            provider=self.name,
            truncated=truncated,
            duration_ms=duration_ms,
            notes=tuple(notes),
        ) if False else ExecResult(
            returncode=returncode,
            output=output,
            isolated=True,
            provider=self.name,
            truncated=truncated,
            duration_ms=duration_ms,
        )

    def terminate(self, handle: Any, grace_seconds: float = 2.0) -> bool:
        return terminate(handle, grace_seconds)
