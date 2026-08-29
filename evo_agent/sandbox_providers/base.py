"""Shared machinery for the isolation providers (07 §2, isolation layer).

One rule governs this whole package: **it is the only place in Evo that starts a process.**
Enforced by ``I-exec-isolation`` and by ``test_no_unsandboxed_execution``. The reason is not
tidiness. Before this, evolution *candidates* were confined while the agent's own tools ran on
the host - the boundary was on the lesser-risk side of the two (00 §B.7) - and the argv rules in
``SecurityPolicy.validate_command`` were the only thing between a model's tool call and the
machine, which they demonstrably were not (``python3 evil.py`` was allowed).

Confinement is therefore the boundary, and argv rules are advisory hardening on top of it. Both
are kept, because each catches what the other misses: the allowlist stops casual misuse without
paying namespace costs, the provider stops the misuse the allowlist did not anticipate.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
from typing import Any, Iterable, Sequence

from ..ports.contracts import ExecRequest, ExecResult


#: Environment every confined child gets. Inherited secrets are the leak this prevents: a
#: provider that passes ``os.environ`` through hands the child whatever credentials the parent
#: had, which is exactly what a sandboxed process must not be able to read.
BASE_ENVIRONMENT: dict[str, str] = {
    "PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
    "LANG": "C.UTF-8",
    "LC_ALL": "C.UTF-8",
    "PYTHONNOUSERSITE": "1",
    "PYTHONDONTWRITEBYTECODE": "1",
    "NO_PROXY": "*",
    "no_proxy": "*",
    "EVO_NETWORK_POLICY": "denied",
    "GIT_CONFIG_GLOBAL": "/dev/null",
    "GIT_CONFIG_SYSTEM": "/dev/null",
}

#: Keys that may never reach a confined child, even if a caller asks.
SECRET_ENVIRONMENT_PATTERN = ("KEY", "TOKEN", "SECRET", "PASSWORD", "CREDENTIAL", "AUTH")


class IsolationUnavailable(RuntimeError):
    """No provider could satisfy the request at the configured enforcement level."""


def sanitized_environment(extra: dict[str, str] | None = None) -> dict[str, str]:
    """Build the child environment: the baseline, plus vetted additions only.

    Anything that looks like a credential is dropped rather than redacted, and the drop is
    reported by :func:`dropped_secret_names` so a caller can record why its command behaved
    differently than it would have on the host.
    """
    merged = dict(BASE_ENVIRONMENT)
    dropped: list[str] = []
    for key, value in (extra or {}).items():
        if any(marker in key.upper() for marker in SECRET_ENVIRONMENT_PATTERN):
            dropped.append(key)
            continue
        merged[str(key)] = str(value)
    sanitized_environment.dropped = tuple(dropped)  # type: ignore[attr-defined]
    return merged


def dropped_secret_names() -> tuple[str, ...]:
    return getattr(sanitized_environment, "dropped", ())


def bounded_text(text: Any, limit: int) -> tuple[str, bool]:
    """Clip a string to a byte budget, reporting the clip."""
    text = "" if text is None else str(text)
    encoded = text.encode("utf-8", errors="replace")
    if len(encoded) <= limit:
        return text, False
    return encoded[:limit].decode("utf-8", errors="ignore"), True


def bounded_output(stream: Any, limit: int) -> tuple[str, bool]:
    """Read at most ``limit`` bytes, reporting truncation instead of hiding it.

    Truncation must be visible in the result because ``output_sha256`` and any later citation
    resolve against what the caller actually got; silently clipping is how a receipt starts
    describing bytes nobody saw.
    """
    data = stream.read(limit + 1)
    if data is None:
        return "", False
    if isinstance(data, bytes):
        text = data.decode("utf-8", errors="replace")
        truncated = len(data) > limit
        return (text[:limit] if truncated else text), truncated
    text = str(data)
    return text[:limit], len(text) > limit


def terminate(process: "subprocess.Popen[str]", grace_seconds: float = 2.0) -> bool:
    """Signal the whole group, then escalate. A killed child that keeps running is not confined.

    ``start_new_session=True`` at spawn time is what makes this reliable: without it, SIGTERM to
    the leader leaves grandchildren attached to the caller's session.
    """
    if process.poll() is not None:
        return True
    for signal_number, wait_seconds in ((signal.SIGTERM, grace_seconds), (signal.SIGKILL, 1.0)):
        try:
            os.killpg(process.pid, signal_number)
        except (ProcessLookupError, PermissionError, OSError):
            try:
                process.terminate()
            except OSError:
                return False
        except OSError:
            return False
        try:
            process.wait(timeout=wait_seconds)
            return True
        except subprocess.TimeoutExpired:
            continue
    return process.poll() is not None


def resolve_read_only_mounts(request: ExecRequest) -> tuple[Path, ...]:
    """Directories the child must not write to, deduplicated and existence-filtered."""
    seen: list[Path] = []
    for candidate in list(request.read_only):
        path = Path(candidate)
        if not path.exists() or path in seen:
            continue
        seen.append(path)
    return tuple(seen)


def child_tmp_directory(workspace: Path) -> Path:
    """A writable scratch space for the child, inside the task's own area.

    `/tmp` is deliberately not shared: it is the classic channel for a confined process to talk
    to its neighbours, and tools writing scratch files there is normal enough that the sandbox has
    to give them somewhere legal to go instead of failing.
    """
    root = workspace / ".evo" / "sandbox-tmp"
    root.mkdir(parents=True, exist_ok=True)
    handle, name = tempfile.mkstemp(prefix="run-", dir=root)
    os.close(handle)
    scratch = Path(name)
    scratch.unlink(missing_ok=True)
    scratch.mkdir()
    return scratch


@dataclass
class ConfinedLaunch:
    """A spawn-ready command the *caller* will manage, with everything the provider decided.

    This exists because ``run`` cannot serve an interactive child: a bridge that talks JSON lines
    with a harness needs the process to stay open. Handing out the wrapped argv and environment -
    instead of letting the bridge rebuild them - is what keeps one set of isolation flags for both
    the fire-and-forget and the long-lived case, which is exactly where two copies would drift.
    """

    argv: list[str]
    env: dict[str, str]
    cwd: Path
    provider: str
    isolated: bool
    scratch: Path | None = None
    notes: tuple[str, ...] = ()
    degraded_reason: str = ""


@dataclass
class RunOutcome:
    """Internal carrier so providers share timeout/truncation handling exactly."""

    returncode: int
    output: str = ""
    truncated: bool = False
    duration_ms: float = 0.0
    notes: list[str] = field(default_factory=list)

    def to_result(self, *, isolated: bool, provider: str, refusal: str = "", degraded_reason: str = "") -> ExecResult:
        return ExecResult(
            returncode=self.returncode,
            output=self.output,
            isolated=isolated,
            provider=provider,
            refusal=refusal,
            degraded_reason=degraded_reason,
            truncated=self.truncated,
            duration_ms=self.duration_ms,
        )


def merge_streams(completed_stdout: str | None, completed_stderr: str | None, limit: int) -> tuple[str, bool]:
    """stdout then stderr, within the byte bound, matching what the host behaviour exposed.

    Takes the strings ``Popen.communicate`` already returned. Truncation is a *combined*
    decision: clipping stderr must still be reported, or a caller sees a short result and
    assumes the command was quiet.
    """
    first = (completed_stdout or "").strip()
    second = (completed_stderr or "").strip()
    combined = f"{first}\n{second}".strip() if second else first
    return bounded_text(combined, limit)


def platform_supports_namespaces() -> bool:
    """Whether any namespace-based provider could exist here at all (not whether one works).

    ``auto`` enforcement uses this to tell "this platform has nothing to confine with" (degrade,
    loudly) from "the confinement we expect is broken" (refuse, because that is a change in the
    security posture of a machine that previously had one).
    """
    if not sys.platform.startswith("linux"):
        return False
    for knob in (
        Path("/proc/sys/user/max_user_namespaces"),
        Path("/proc/sys/kernel/unprivileged_userns_clone"),
    ):
        try:
            value = knob.read_text().strip()
        except OSError:
            continue
        if value and value != "0":
            return True
    return Path("/proc/sys/user/max_user_namespaces").is_file()


def usable_from_probe(command: Sequence[str], timeout: float = 5.0) -> tuple[bool, str]:
    """Run a provider's self-test. Returns (usable, reason). Never raises."""
    try:
        completed = subprocess.run(
            list(command),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError:
        return False, f"{command[0]} is not installed"
    except subprocess.TimeoutExpired:
        return False, f"{command[0]} probe timed out"
    except (OSError, subprocess.SubprocessError) as exc:
        return False, f"{command[0]} probe failed: {type(exc).__name__}: {exc}"
    if completed.returncode == 0:
        return True, ""
    detail = (completed.stderr or b"").decode("utf-8", errors="replace").strip().splitlines()
    return False, f"{command[0]} refused ({detail[0][:160]})" if detail else f"{command[0]} exited {completed.returncode}"


def format_notes(notes: Iterable[str]) -> tuple[str, ...]:
    return tuple(note for note in notes if note)
