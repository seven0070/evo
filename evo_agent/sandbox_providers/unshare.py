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
from ..ports.evolution_target import MountSet
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

#: What this provider does *not* do, phrased so a caller can display it instead of inferring it.
#:
#: A user namespace may make its own mounts private and re-bind declared paths read-only, but it
#: cannot remount the host's superblock read-only - that needs privilege the namespace does not have,
#: and ``mount -o remount,ro /`` answers "permission denied". So the filesystem guarantee here is
#: "the declared read-only set is honoured", not "nothing outside the workspace is writable" the way
#: ``bwrap --ro-bind / /`` provides. Recorded rather than glossed: an unspoken limit becomes an
#: assumed one, and the assumption is what a future reader would get wrong.
RESIDUAL_FILESYSTEM_LIMIT = (
    "read-only set is the declared roots only; a user namespace cannot mask the host filesystem, "
    "so prefer bwrap when a whole-tree boundary is required"
)

_MOUNT_SCRIPT = (
    "set -u; "
    'export HOME="$1"; export TMPDIR="$1"; '
    f'mount --make-rprivate / || {{ echo "{MOUNT_FAILURE_MARKER}"; exit {MOUNT_FAILURE_EXIT}; }}; '
    "i=1; "
    'while [ "$i" -le "$2" ]; do '
    r'eval "p=\${$((2 + i))}"; '
    # Entries arrive prefixed (``ro:`` / ``mask:``) so one loop honours both kinds of promise.
    'kind="${p%%:*}"; path="${p#*:}"; '
    'if [ "$kind" = mask ]; then mount -t tmpfs evo-mask "$path" || {{ echo "{MOUNT_FAILURE_MARKER}"; exit {MOUNT_FAILURE_EXIT}; }}; else '
    f'mount --bind "$path" "$path" || {{ echo "{MOUNT_FAILURE_MARKER}"; exit {MOUNT_FAILURE_EXIT}; }}; '
    f'mount -o remount,bind,ro "$path" || {{ echo "{MOUNT_FAILURE_MARKER}"; exit {MOUNT_FAILURE_EXIT}; }}; fi; '
    "i=$((i + 1)); "
    "done; "
    # The sysfs mask, once, after the read-only binds so a refused mount cannot leave a half-applied
    # read-only set behind. Gated by EVO_SYSFS_MASK because *whether the kernel allows it* is decided
    # by a probe before the child starts (see ``sysfs_maskable``), which keeps the child's output
    # clean: a provider that reports a limitation by printing into the payload's stdout puts the
    # caller in the position of parsing its own tool output to find the caveat.
    '[ "${EVO_SYSFS_MASK:-0}" = 1 ] && mount -t sysfs sysfs /sys 2>/dev/null; '
    'shift $((2 + $2)); '
    'exec "$@"'
)

#: Positional layout of the script: $1 = scratch dir, $2 = count of read-only paths, $3.. =
#: the paths, and after a shift, the payload command.
#:
#: ``EVO_SYSFS_MASK`` (in the child's environment, not argv) tells the script whether to mount a
#: fresh sysfs. Environment rather than a positional because the layout above is already counted-out
#: by a shell loop, and a fifth field would be a second thing to keep in step with the count.

#: What the child is told when the mask could not be applied: the host's ``/sys/class/net`` stays
#: visible, which is a disclosure rather than an escape, and belongs in the record.
SYSFS_NOTE = "host sysfs not remounted: interface names in /sys/class/net are the host's"


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
        if usable:
            # Usable probes normally carry an empty reason. Here the empty string would be a lie by
            # omission, so the residual limit is reported as the reason even on success.
            reason = RESIDUAL_FILESYSTEM_LIMIT
        return ProviderAvailability(
            name=self.name,
            usable=usable,
            reason=reason,
            supports_network_denial=self.deny_network,
            supports_read_only_mounts=True,
            supports_pid_namespace=True,
            detail={"executable": executable, "sysfs_masked": self.sysfs_maskable() if usable else False},
        )

    def sysfs_maskable(self) -> bool:
        """Whether a fresh sysfs can be mounted inside this namespace, decided once per instance.

        Worth a probe of its own because the answer is not "user namespaces work": mounting a new
        sysfs is a separate kernel permission, and on a host that refuses it the correct behaviour is
        to run without the mask *and say so*, not to run a command that promises a mask it did not
        apply. Cached because the answer cannot change within a process and the probe costs ~10ms.
        """
        cached = getattr(self, "_sysfs_maskable", None)
        if cached is not None:
            return cached
        executable = shutil.which("unshare")
        if not executable:
            self._sysfs_maskable = False
            return False
        try:
            probe = subprocess.run(
                [executable, "--user", "--map-root-user", "--mount", "--net", "--pid", "--fork",
                 "sh", "-c", "mount -t sysfs sysfs /sys"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=5.0,
                check=False,
            )
            usable = probe.returncode == 0
        except (OSError, subprocess.SubprocessError):
            usable = False
        self._sysfs_maskable = usable
        return usable

    def command_for(self, request: ExecRequest, scratch: Path) -> list[str]:
        read_only = [str(Path(item)) for item in request.read_only if Path(item).exists()]
        masked = [str(Path(item)) for item in request.masked if Path(item).exists()]
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
        # ``masked`` rides in the same loop as ``read_only`` by passing the count of *both*: the script
        # distinguishes them by a marker prefix in the path list rather than by a second loop, because
        # a second loop is a second place where "a promised mount failed" has to be handled, and the
        # whole point of the sentinel is that there is exactly one.
        promise = [f"ro:{item}" for item in read_only] + [f"mask:{item}" for item in masked]
        command += ["sh", "-c", _MOUNT_SCRIPT, "evo-sandbox", str(scratch), str(len(promise)), *promise, *request.argv]
        return command

    def mount_set_for(self, request: ExecRequest) -> MountSet:
        """The mounts this provider will actually apply, as data.

        Reported separately from the argv because the argv is a shell script with a counted loop: a
        caller that wants to know "is /etc read-only in this run" should not have to parse shell. The
        one asymmetry with bwrap - the host root is *not* read-only here, only the declared set - is
        visible in the fields rather than hidden, which is the whole reason the field list exists.
        """
        workspace = Path(request.cwd)
        scratch = child_tmp_directory(workspace)
        read_only = tuple(str(Path(item)) for item in request.read_only if Path(item).exists())
        masked = [str(Path(item)) for item in request.masked if Path(item).exists()]
        if self.sysfs_maskable():
            masked = list(dict.fromkeys(["/sys", *masked]))
        return MountSet(
            read_only=read_only,
            writable=tuple(str(Path(item).resolve()) for item in (workspace, *request.writable, scratch) if Path(item).exists()),
            masked=tuple(masked),
            deny_network=self.deny_network and not request.network,
            deny_host_pids=True,
        )

    def prepare(self, request: ExecRequest) -> ConfinedLaunch:
        """Wrap the request for a caller-managed process.

        The mount script is passed to ``sh -c`` as argv, with paths in positional parameters rather
        than interpolated into the script text: a workspace path containing a quote must not be able
        to become code, and there is no shell here to sanitise it for us.
        """
        scratch = child_tmp_directory(Path(request.cwd))
        masked = self.sysfs_maskable()
        environment = sanitized_environment(
            {
                "HOME": str(scratch),
                "TMPDIR": str(scratch),
                "EVO_SYSFS_MASK": "1" if masked else "0",
                **request.env,
            }
        )
        return ConfinedLaunch(
            argv=self.command_for(request, scratch),
            env=environment,
            cwd=Path(request.cwd),
            provider=self.name,
            isolated=True,
            scratch=scratch,
            # ``degraded_reason`` is the channel that already exists for "this ran, and it ran
            # differently than the ideal": no new field, and the caller's ledger shows the same shape
            # whether the limitation came from the mount script or from the probe.
            degraded_reason="" if masked else SYSFS_NOTE,
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
        if launch.degraded_reason:
            # Set by ``prepare`` when the provider knows it will run differently than its ideal - today,
            # a host that will not let a user namespace mount sysfs. Reported rather than dropped: an
            # operator reading the ledger should not have to know which probe to re-run.
            notes.append(launch.degraded_reason)
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
