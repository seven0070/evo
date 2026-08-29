"""Shared test fixtures. There was no conftest before the bwrap baseline work, and a fixture is
the only place a stub that must never be mistaken for a real sandbox belongs.

Why this file exists: two tests in ``tests/test_sandbox.py`` assert that the bwrap branch of
``_isolated_command`` is chosen when bubblewrap is installed. They monkeypatched
``shutil.which``, which is only half of the decision - ``_bwrap_usable()`` then *runs* the binary,
so on a machine without bubblewrap the probe failed and the tests reported the branch as broken when
it was merely unexercised. The fixture supplies a binary that answers the probe, which makes an
assertion about command construction independent of the host image.

The stub's one job is to be *distinguishable* from bubblewrap: it exits 0 for a probe invocation and
42 with a marker for anything else. That is deliberate. A stub that ran payloads would let a
confinement test pass while confining nothing - the exact failure mode this audit exists to catch -
so "the stub was asked to execute" is a test bug the fixture exposes rather than hides.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import stat
import textwrap

import pytest


#: The stub's refusal code. A test that ends up with this exit status tried to *execute* through the
#: stub and therefore wanted real confinement; it should run against a real provider or skip.
STUB_REFUSAL_EXIT = 42

#: Marker the stub prints instead of running a payload.
STUB_REFUSAL_MARKER = "EVO_STUB_BWRAP_REFUSAL"

_STUB_SOURCE = textwrap.dedent(
    '''\
    #!/usr/bin/env python3
    """Stand-in for bubblewrap, used only to make branch selection deterministic.

    A probe invocation is the one ending in the literal argument ``true``: it answers 0, so the
    caller's usability check proceeds. Anything else is a real execution request, and this program
    refuses it. Silently running a payload would let an isolation test pass without isolating
    anything, which is worse than not running the test.

    The call log sits next to this file rather than in an environment variable because a confined
    child is handed a sanitized environment by design; a variable set in the test process would not
    survive into it, and a stub that quietly stopped logging would look exactly like a stub nobody
    called.
    """
    import json
    import os
    import sys

    argv = sys.argv[1:]
    here = os.path.dirname(os.path.abspath(__file__))
    try:
        with open(os.path.join(here, "calls.jsonl"), "a", encoding="utf-8") as handle:
            handle.write(json.dumps({"argv": argv}) + chr(10))
    except OSError:
        pass
    if argv and argv[-1] == "true":
        raise SystemExit(0)
    sys.stderr.write("EVO_STUB_BWRAP_REFUSAL: this stub does not execute payloads" + chr(10))
    raise SystemExit(42)
    '''
)


@dataclass(frozen=True)
class StubBwrap:
    """What the fixture hands back: the fake binary, and where it recorded its calls."""

    path: Path
    log: Path

    def calls(self) -> list[list[str]]:
        import json

        if not self.log.exists():
            return []
        rows: list[list[str]] = []
        for line in self.log.read_text(encoding="utf-8").splitlines():
            try:
                rows.append([str(item) for item in json.loads(line)["argv"]])
            except (ValueError, KeyError, TypeError):
                continue
        return rows

    @property
    def probe_calls(self) -> list[list[str]]:
        return [argv for argv in self.calls() if argv and argv[-1] == "true"]

    @property
    def executed_payloads(self) -> list[list[str]]:
        """Any invocation that was not a probe. Non-empty means the test wanted a real sandbox."""
        return [argv for argv in self.calls() if not (argv and argv[-1] == "true")]


@pytest.fixture
def stub_bwrap(tmp_path: Path) -> StubBwrap:
    """An executable stub bwrap, ready for a monkeypatched ``shutil.which`` to point at."""
    # The stub's literals are written by hand inside the generated script, so this is the seam that
    # keeps the exported constants and the script from drifting apart.
    if f"raise SystemExit({STUB_REFUSAL_EXIT})" not in _STUB_SOURCE or STUB_REFUSAL_MARKER not in _STUB_SOURCE:
        raise RuntimeError("the stub bwrap source no longer matches STUB_REFUSAL_* in this file")
    directory = tmp_path / "stub-bin"
    directory.mkdir(parents=True, exist_ok=True)
    script = directory / "bwrap"
    script.write_text(_STUB_SOURCE, encoding="utf-8")
    script.chmod(script.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return StubBwrap(path=script, log=directory / "calls.jsonl")


@pytest.fixture
def real_bwrap() -> str:
    """Path to a genuine, *working* bubblewrap. Guards tests that must not use the stub.

    Skips when bubblewrap is absent or its user namespace does not work, which keeps "this host
    cannot exercise the branch" distinct from "the branch works". Presence is not enough: the repo's
    own CI notes that a distro bwrap without setuid mode cannot set up its user namespace on some
    runners, which is exactly why the engine probes rather than assumes - so this fixture runs the
    same probe the engine runs, and skips if it fails.
    """
    import shutil
    import subprocess

    found = shutil.which("bwrap")
    if not found:
        pytest.skip("bubblewrap is not installed here, so the bwrap branch cannot be exercised for real")
    probe = subprocess.run(
        [
            found,
            "--die-with-parent",
            "--unshare-user-try",
            "--unshare-net",
            "--unshare-pid",
            "--ro-bind",
            "/",
            "/",
            "true",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=15,
        check=False,
    )
    if probe.returncode != 0:
        pytest.skip("bubblewrap is installed but cannot set up its namespaces on this kernel")
    return found
