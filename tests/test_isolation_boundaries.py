"""What the confined environment actually cannot do, measured rather than assumed.

The P2 sandbox claims a boundary; this file is the P3 baseline that checks the claim from the
outside, by trying to cross it. Three kinds of escape are attempted through the same code paths the
agent and the evolution engine use - a write beyond the writable surface, a packet leaving the
network namespace, and a look at a process the namespace was supposed to hide. A provider that
survives only the polite version of these tests is not confining anything: the interesting cases
are the absolute path, the symlink planted in advance, and the PID that was still alive when the
child started.

One file also states what this design does *not* promise, because an unspoken limit becomes an
assumed one. The ``unshare`` fallback cannot mask the host filesystem the way ``--ro-bind / /``
does: a user namespace may make its own mounts private and read-only re-bind the paths it declares,
but remounting the host's own superblock read-only needs privilege the namespace does not have
(``mount -o remount,ro /`` answers "permission denied"). The fallback therefore guarantees
namespaced network, namespaced PIDs, private scratch, and read-only-ness for the *declared* roots,
and nothing more. ``test_the_fallback_states_its_own_residual_breadth`` pins that statement so the
difference cannot quietly become a marketing claim.
"""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import time

import pytest

from test_sandbox_providers import confined, isolated_provider_or_skip, request_for  # noqa: F401  (shared helpers)

from evo_agent.sandbox_providers import run_confined
from evo_agent.sandbox_providers.base import BASE_ENVIRONMENT

ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = ROOT / "evo_agent"


def run_pytest_snippet(workspace: Path, snippet: str, *args: str) -> str:
    """Run ``snippet`` confined, with ``args`` as argv extras, and return stdout verbatim."""
    result = confined(
        request_for(
            workspace,
            sys.executable,
            "-I",
            "-S",
            "-c",
            snippet,
            *args,
            read_only=(SOURCE_ROOT,),
        )
    )
    assert result.returncode == 0, f"the confined probe itself failed: {result.output}"
    return result.output


# --- filesystem ------------------------------------------------------------------------


def test_a_write_outside_the_declared_surface_is_denied(tmp_path: Path):
    """The absolute-path version of "stay in your workspace".

    ``SecurityPolicy.sandbox_read_only_paths`` is how a caller widens the read-only set, so the
    assertion is made against a path that *is* declared, not against an arbitrary host file that
    the fallback simply does not cover. That distinction is the whole point of the next two tests.
    """
    isolated_provider_or_skip(tmp_path)
    target = SOURCE_ROOT / "PWNED_BY_BOUNDARY_TEST.txt"
    assert not target.exists(), "a previous run left a file in the source tree"
    output = run_pytest_snippet(
        tmp_path,
        "import pathlib, sys\n"
        "p = pathlib.Path(sys.argv[1])\n"
        "try:\n"
        "    p.write_text('escaped')\n"
        "    print('WROTE')\n"
        "except OSError as exc:\n"
        "    print('DENIED', type(exc).__name__)\n",
        str(target),
    )
    assert "DENIED" in output, f"write into a read-only root succeeded: {output}"
    assert not target.exists()


def test_a_symlink_planted_in_the_workspace_does_not_widen_it(tmp_path: Path):
    """Escape attempt #2: reach the read-only root through a link the workspace already contains.

    Mount-based confinement protects paths, not names, so a symlink is only denied because the
    target's *mount* is read-only. If the write lands, the boundary was implemented as a string
    prefix check, and a prefix check is the defect this repository already has one scar from.
    """
    isolated_provider_or_skip(tmp_path)
    link = tmp_path / "hook"
    target = SOURCE_ROOT / "PWNED_VIA_SYMLINK.txt"
    link.symlink_to(target)
    output = run_pytest_snippet(
        tmp_path,
        "import pathlib, sys\n"
        "try:\n"
        "    pathlib.Path(sys.argv[1]).write_text('escaped')\n"
        "    print('WROTE')\n"
        "except OSError as exc:\n"
        "    print('DENIED', type(exc).__name__)\n",
        str(link),
    )
    assert "DENIED" in output, f"the symlink escaped the read-only mount: {output}"
    assert not target.exists()


def test_the_confined_child_can_write_where_it_is_told_to(tmp_path: Path):
    """The positive half. A boundary that denies everything is a broken tool, not a secure one."""
    isolated_provider_or_skip(tmp_path)
    output = run_pytest_snippet(
        tmp_path,
        "import os, pathlib\n"
        "scratch = pathlib.Path(os.environ['TMPDIR'])\n"
        "(scratch / 'ok.txt').write_text('written')\n"
        "print('SCRATCH', (scratch / 'ok.txt').read_text())\n"
        "pathlib.Path(os.getcwd(), 'w.txt').write_text('x')\n"
        "print('INSIDE_WORKSPACE', pathlib.Path(os.getcwd(), 'w.txt').read_text())\n",
    )
    assert "SCRATCH written" in output
    assert "INSIDE_WORKSPACE x" in output


# --- network ---------------------------------------------------------------------------


def test_no_interface_reaches_beyond_loopback(tmp_path: Path):
    """Network isolation is asserted on the namespace, not on the policy string.

    ``EVO_NETWORK_POLICY=denied`` is a convention the environment carries; the guarantee is that
    there is nothing to route through. So the child reads the interfaces the *kernel* says its
    network namespace owns (``/proc/net/dev``, which is per-namespace) and then tries to open a
    socket to a public address. ``/sys/class/net`` is deliberately not consulted here - the next but
    one test is about why.
    """
    isolated_provider_or_skip(tmp_path)
    output = run_pytest_snippet(
        tmp_path,
        "import socket\n"
        "dev = open('/proc/net/dev').read().splitlines()[2:]\n"
        "print('NETNS', ' '.join(sorted(line.split(':')[0].strip() for line in dev if ':' in line)))\n"
        "try:\n"
        "    s = socket.create_connection(('8.8.8.8', 53), timeout=3)\n"
        "    s.close()\n"
        "    print('EGRESS_OK')\n"
        "except OSError as exc:\n"
        "    print('EGRESS_DENIED', type(exc).__name__)\n"
    )
    assert "EGRESS_DENIED" in output, f"a confined child reached the network: {output}"
    recorded = [line for line in output.splitlines() if line.startswith("NETNS")]
    assert recorded, output
    assert recorded[0].split(" ", 1)[1].split() == ["lo"], f"the namespace exposed more than loopback: {recorded[0]}"


@pytest.mark.xfail(
    strict=True,
    reason=(
        "known gap, closed by the P3 mount_set seam: neither provider remounts sysfs, so the child "
        "reads the host's interface list. Measured fix is `mount -t sysfs sysfs /sys` inside the "
        "unshare child (verified to work on this kernel) plus the equivalent mask for bwrap. When "
        "that lands this test must flip to a plain pass - strictly, so a silently re-widened "
        "boundary is caught by the xfail becoming XPASS."
    ),
)
def test_the_childs_view_of_interfaces_matches_its_namespace(tmp_path: Path):
    """The stale-sysfs finding, kept in front of everyone until the seam that fixes it lands.

    ``/sys/class/net`` is host sysfs: neither provider remounts it, so a confined child sees the
    host's interface names and can read ``/sys/class/net/eth0/address`` even though the namespace
    owns no such device. Nothing can be *sent* (the previous test proves that), so this is
    information disclosure and a misleading self-inspection rather than an escape - a tool that
    decides "I have network" by looking at sysfs decides wrong. Fixing it is mount_set work: a fresh
    ``sysfs`` inside the unshare child (measured to work: ``mount -t sysfs sysfs /sys`` succeeds in
    the same namespace) and the equivalent masking under bwrap, applied to both so the parity
    assertion in P2 keeps holding.
    """
    isolated_provider_or_skip(tmp_path)
    output = run_pytest_snippet(
        tmp_path,
        "import os\n"
        "print('SYS_IFACES', ' '.join(sorted(os.listdir('/sys/class/net'))))\n"
    )
    line = [row for row in output.splitlines() if row.startswith("SYS_IFACES")][0]
    assert set(line.split(" ", 1)[1].split()) <= {"lo"}, (
        f"the child's sysfs still describes the host's network: {line}"
    )


def test_the_denial_is_a_convention_the_child_can_act_on(tmp_path: Path):
    """The marker variable must be present, so a tool that wants to respect it can.

    This is not the security control - the namespace is. It is the record the child carries, and
    without it a refusal downstream is unexplainable.
    """
    isolated_provider_or_skip(tmp_path)
    output = run_pytest_snippet(tmp_path, "import os; print('POLICY', os.environ.get('EVO_NETWORK_POLICY'))")
    assert "POLICY denied" in output
    assert BASE_ENVIRONMENT["EVO_NETWORK_POLICY"] == "denied"


# --- processes -------------------------------------------------------------------------


def test_host_processes_are_invisible_inside_the_namespace(tmp_path: Path):
    """A long-lived host PID must not appear in the child's /proc.

    Process isolation is what stops a confined run from signalling, inspecting, or killing the agent
    that launched it. Checking "few PIDs" would pass on a namespace that leaks just one, so the
    assertion names a process that is definitely alive on the host at that moment.
    """
    isolated_provider_or_skip(tmp_path)
    sentinel = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        time.sleep(0.2)
        assert sentinel.poll() is None, "the sentinel died before the confined child could look for it"
        output = run_pytest_snippet(
            tmp_path,
            "import os, sys\n"
            f"host_pid = {sentinel.pid}\n"
            "seen = {int(p) for p in os.listdir('/proc') if p.isdigit()}\n"
            "print('HOST_PID_VISIBLE', host_pid in seen)\n"
            "print('COUNT', len(seen))\n"
            "print('SELF_IS_NOT_ONE', os.getpid() != 1)\n",
        )
    finally:
        sentinel.terminate()
        sentinel.wait(timeout=10)
    assert "HOST_PID_VISIBLE False" in output, f"a host process leaked into the sandbox: {output}"
    counts = [line for line in output.splitlines() if line.startswith("COUNT")]
    assert counts and int(counts[0].split()[1]) <= 6, f"the namespace did not isolate PIDs: {output}"


# --- the residual, stated --------------------------------------------------------------


def test_the_fallback_states_its_own_residual_breadth(tmp_path: Path):
    """Where bwrap is not installed, the record must not imply a filesystem boundary it lacks.

    This test encodes the *measured* behaviour of the ``unshare`` fallback on this class of host: the
    namespaces are real, the network is gone, the PIDs are private, and a write to an undeclared
    host path still lands on the host because remounting the host superblock read-only needs
    privilege a user namespace does not have. If a future provider masks the whole tree, this test
    is the one to invert - and it must be inverted deliberately, not by deleting it, which is how a
    narrowed guarantee becomes invisible.
    """
    from evo_agent.sandbox_providers import LocalBwrapProvider, UnshareProvider

    bwrap_usable = LocalBwrapProvider().probe().usable
    marker = tmp_path.parent / f"evo-residual-{os.getpid()}.txt"
    if marker.exists():
        marker.unlink()

    result = run_confined(
        request_for(tmp_path, "touch", str(marker)),
        providers=[UnshareProvider()] if not bwrap_usable else None,
    )
    if not bwrap_usable:
        if not UnshareProvider().probe().usable:
            pytest.skip("no usable namespace provider on this host")
        assert result.isolated is True, "the fallback is still a namespace sandbox, and must say so"
        assert result.returncode == 0, result.refusal
        # bwrap's own shape is the fix for this gap, which is why the engines prefer it: a masked
        # root is the difference between "declared paths are read-only" and "nothing else is writable".
        availability = UnshareProvider().probe()
        assert availability.usable
        assert "read-only set" in availability.reason.lower(), (
            "the fallback's narrower filesystem guarantee must be stated where callers can see it"
        )
    else:
        assert result.returncode != 0, "bwrap must deny a write outside the writable surface"
        assert not marker.exists()
    if marker.exists():
        marker.unlink()


def test_an_absolute_path_and_a_relative_one_are_denied_identically(tmp_path: Path):
    """Confinement that depends on how a path was spelled is a path filter, not a mount.

    The tree here is invented (``repo/evo_agent`` read-only, ``repo/ws`` the workspace) rather than
    the real checkout, because a test that *tries* to write into the source tree and succeeds would
    leave the repo dirty; the shape of the mount is what is under test, not the path's fame.
    """
    repo = tmp_path / "repo"
    guarded = repo / "evo_agent"
    workspace = repo / "ws"
    guarded.mkdir(parents=True)
    workspace.mkdir(parents=True)
    name = "written.txt"
    (guarded / "keep.txt").write_text("original")

    from evo_agent.ports.contracts import ExecRequest
    from evo_agent.sandbox_providers import run_confined

    # No "$0" filler here: for ``python -c`` the first item after the code is sys.argv[0], so an
    # extra element silently shifts every path the child is handed. (It cost a debugging cycle to
    # learn that, and the failure looked like a sandbox leak.)
    snippet = (
        "import os, pathlib, sys\n"
        "results = []\n"
        "for p in (sys.argv[1], os.path.join('..', 'evo_agent', sys.argv[2])):\n"
        "    try:\n"
        "        pathlib.Path(p).write_text('escaped')\n"
        "        results.append('WROTE')\n"
        "    except OSError:\n"
        "        results.append('DENIED')\n"
        "print(' '.join(results))\n"
    )
    result = run_confined(
        ExecRequest(
            argv=(sys.executable, "-I", "-S", "-c", snippet, str(guarded / name), name),
            cwd=workspace,
            read_only=(guarded,),
        )
    )
    if not result.isolated:
        pytest.skip("no usable namespace provider on this host")
    assert result.returncode == 0, result.output
    assert output_denied(result.output), result.output
    assert not (guarded / name).exists()
    assert (guarded / "keep.txt").read_text() == "original"


def output_denied(text: str) -> bool:
    """Both spellings refused, and the line is the child's own verdict rather than an absence."""
    last = text.strip().splitlines()[-1] if text.strip() else ""
    return last == "DENIED DENIED"


# --- the seam this file exists to inform -----------------------------------------------


@pytest.mark.xfail(
    strict=True,
    reason=(
        "P3 closes this: the engines and LocalBwrapProvider resolve bwrap with shutil.which to *probe* "
        "it, then exec the bare name 'bwrap', which the child resolves again against its sanitized "
        "PATH. A bwrap outside /usr/local/sbin../bin is therefore selected-but-unspawnable (or, worse, a "
        "different build is exec'd than was probed). The fix is to exec the path that was probed, which "
        "is the same resolution the mount_set seam is meant to unify."
    ),
)
def test_the_binary_that_was_probed_is_the_binary_that_runs(tmp_path: Path, monkeypatch):
    """Probe and exec must agree on *which* binary they mean."""
    from evo_agent.ports.contracts import ExecRequest
    from evo_agent.sandbox_providers import LocalBwrapProvider
    import evo_agent.sandbox_providers.local_bwrap as module

    fake = tmp_path / "prefix" / "bin" / "bwrap"
    fake.parent.mkdir(parents=True)
    fake.write_text("#!/bin/sh\nexit 0\n")
    fake.chmod(0o755)
    monkeypatch.setattr(module.shutil, "which", lambda name: str(fake) if name == "bwrap" else None)

    argv = LocalBwrapProvider().command_for(ExecRequest(argv=("true",), cwd=tmp_path), tmp_path / "scratch")
    assert argv[0] == str(fake), f"probed {fake} but would exec {argv[0]!r}"
