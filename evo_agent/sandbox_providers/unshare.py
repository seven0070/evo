"""``unshare`` fallback provider: namespaces without requiring a setuid helper.

Bubblewrap is the preferred path, but some hosts ship util-linux and not bwrap (and some, like
this project's own CI image, need ``chmod 4755`` on bwrap to make user-namespace uid maps
writable). The namespace set is the same: user, mount, PID, network. What differs is that
read-only-ness has to be created here with a bind + remount, because ``unshare --mount`` gives the
child a private mount namespace whose default is *everything writable* - which is why this provider
mounts explicitly and refuses to run at all if it could not apply the read-only binds it promised.

That last sentence is the difference between this file and a permissive wrapper: the read-only set
is not a hint. ``run`` returns a refusal when a mount fails, so the caller records a blocked
command rather than an unconfined one that looks confined.
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
    format_notes,
    merge_streams,
    sanitized_environment,
    terminate,
    usable_from_probe,
)

#: Exit code and sentinel the script uses when a mount it promised could not be applied. A
#: dedicated signal is the point: inferring "the namespace was set up wrong" from whatever the
#: payload printed turns a legitimate read-only denial (EROFS seen by the *command*) into a false
#: refusal, and those two cases must be distinguishable in the record.
MOUNT_FAILURE_MARKER = "EVO_MOUNT_FAILURE"
MOUNT_FAILURE_EXIT = 97

_MOUNT_SCRIPT = (
    "set -u; "
    'export HOME="$1"; export TMPDIR="$1"; '
    f'mount --make-rprivate / || {{ echo "{MOUNT_FAILURE_MARKER}"; exit {MOUNT_FAILURE_EXIT}; }}; '
    "i=1; "
    'while [ "$i" -le "$2" ]; do '
    r'eval "p=\${$((2 + i))}"; '
    f'mount --bind "$p" "$p" || {{ echo "{MOUNT_FAILURE_MARKER}"; exit {MOUNT_FAILURE_EXIT}; }}; '
    f'mount -o remount,bind,ro "$p" || {{ echo "{MOUNT_FAILURE_MARKER}"; exit {MOUNT_FAILURE_EXIT}; }}; '
    "i=$((i + 1)); "
    "done; "
    'shift $((2 + $2)); '
    'exec "$@"'
)

#: Positional layout of the script: $1 = scratch dir, $2 = count of read-only paths, $3.. =
#: the paths, and after a shift, the payload command.


class UnshareProvider:
    """User/mount/PID/network namespaces via ``unshare(1)``."""

    name = "unshare"

    def __init__(self, *, deny_network: bool = True) -> None:
        self.deny_network = deny_network

    def probe(self) -> ProviderAvailability:
        executable = shutil.which("unshare")
        if not executable:
            return ProviderAvailability(self.name, False, "unshare is not installed")
        usable, reason = usable_from_probe(
            [executable, "--user", "--map-root-user", "--mount", "--pid", "--fork", "--mount-proc", "true"]
            + (["--net"] if self.deny_network else []),
            timeout=5.0,
        )
        return ProviderAvailability(
            name=self.name,
            usable=usable,
            reason=reason,
            supports_network_denial=self.deny_network,
            supports_read_only_mounts=True,
            supports_pid_namespace=True,
            detail={"executable": executable},
        )

    def command_for(self, request: ExecRequest, scratch: Path) -> list[str]:
        read_only = [str(Path(item)) for item in request.read_only if Path(item).exists()]
        command = [
            "unshare",
            "--user",
            "--map-root-user",
            "--mount",
            "--pid",
            "--fork",
            "--mount-proc",
        ]
        if self.deny_network and not request.network:
            command.append("--net")
        command += ["sh", "-c", _MOUNT_SCRIPT, "evo-sandbox", str(scratch), str(len(read_only)), *read_only, *request.argv]
        return command

    def prepare(self, request: ExecRequest) -> ConfinedLaunch:
        """Wrap the request for a caller-managed process.

        The mount script is passed to ``sh -c`` as argv, with paths in positional parameters rather
        than interpolated into the script text: a workspace path containing a quote must not be able
        to become code, and there is no shell here to sanitise it for us.
        """
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
        if request.network and not self.deny_network:
            return ExecResult(returncode=-1, provider=self.name, refusal="this provider never grants network access")
        if request.network:
            return ExecResult(
                returncode=-1,
                provider=self.name,
                refusal="requested network access, which the unshare provider denies by construction",
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
            return ExecResult(returncode=-1, provider=self.name, refusal=f"unshare could not start: {exc}", isolated=False)
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
        # A mount the provider promised but could not apply is a refusal, not a failed command:
        # reporting it as "the tool exited non-zero" would let the caller log an ordinary error
        # while the confinement it negotiated never happened.
        if MOUNT_FAILURE_MARKER in output or returncode == MOUNT_FAILURE_EXIT:
            notes.append("read-only bind could not be applied")
            return ExecResult(
                returncode=-1,
                output=output.replace(MOUNT_FAILURE_MARKER, "").strip(),
                isolated=False,
                provider=self.name,
                refusal="read-only mounts could not be applied inside the namespace; refusing to run unconfined at this level",
                truncated=truncated,
                duration_ms=duration_ms,
                notes=format_notes(notes),
            )
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
            notes=format_notes(notes),
        )

    def terminate(self, handle: Any, grace_seconds: float = 2.0) -> bool:
        return terminate(handle, grace_seconds)
