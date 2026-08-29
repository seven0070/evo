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
from ..ports.evolution_target import MountSet
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
        if usable:
            # bwrap's boundary is the wide one, but it is not a secret store: the host root is bound
            # read-only, which still exposes every world-readable file to the child.
            reason = "host root is read-only, not hidden; world-readable files remain readable"
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
        """The bwrap argv for one request. Exposed for tests and for parity assertions.

        argv[0] is the executable this provider *probed*, not the name ``bwrap``. Resolving twice -
        once with ``shutil.which`` to decide usability and once by the operating system at exec time,
        against the child's sanitized PATH - allows a different binary to run than the one that was
        tested. When ``which`` finds nothing the bare name is kept, so the argv remains a readable
        description of the intent for callers that build it without running it (``run`` refuses on a
        failed probe long before that name reaches an exec).
        """
        workspace = Path(request.cwd)
        command = [
            shutil.which("bwrap") or "bwrap",
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
            # The host's sysfs describes the host's devices and interfaces. Inside a namespace that
            # sees no network, that is not merely useless - it is stale information a tool may act on,
            # and bwrap has no "--sysfs" to replace it with, so the tree is masked.
            "--tmpfs",
            "/sys",
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
        for path in request.masked:
            # Masks last: an explicit mask must win over an inherited bind of the same path, and
            # bwrap applies its arguments in order, so the later ``--tmpfs`` replaces the earlier view.
            if Path(path).exists():
                command += ["--tmpfs", str(Path(path))]
        command += ["--chdir", str(workspace), *request.argv]
        return command

    def mount_set_for(self, request: ExecRequest) -> MountSet:
        """What this provider's argv actually mounts, as data the caller can audit.

        A self-description, not a decision: the argv above remains the thing that confines. It exists
        because a list of flags is not a record - the P2 parity check between a tool's confinement and
        a candidate's needs to compare *promises* ("what is writable, what is masked"), and comparing
        flag strings for two different binaries is how that check quietly becomes a diff of spelling.
        """
        workspace = Path(request.cwd)
        scratch = child_tmp_directory(workspace)
        masked = [str(Path(item)) for item in request.masked if Path(item).exists()]
        return MountSet(
            read_only=("/",),
            writable=tuple(str(Path(item).resolve()) for item in (workspace, *request.writable, scratch) if Path(item).exists()),
            masked=tuple(dict.fromkeys(["/sys", *masked])),
            deny_network=self.deny_network and not request.network,
            deny_host_pids=True,
        )

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
