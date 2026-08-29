"""One answer to "which architecture is this process running?".

``architecture_version`` is the key that joins *performance* to *structure*. The
benchmark-to-promotion argument only holds if a measured outcome can be attributed to the
thing that changed, and the audit found the kernel path writing an empty string here while the
runtime path wrote the real manifest version (00 §B.10) - two resolutions of one fact, one of
them a stub. This module is the single resolution; both loops call it.

The fallback is deliberately not empty. An install with no registered architecture manifest
still has an architecture, and it is the one that can be named by content: the version of the
package plus the digest of the protected byte set. That is coarse - it cannot distinguish two
unregistered trees that differ only in unprotected code - and it says so in the returned
provenance rather than pretending to a precision it does not have.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import tempfile
from typing import Any

from ..version import __version__
from .protected import compute_digests

UNREGISTERED_PREFIX = "arch-unregistered"


def content_address(prefix: str = UNREGISTERED_PREFIX) -> str:
    """A deterministic architecture id derived from the protected byte set.

    Used only when no manifest has been registered. Reading the published manifest is
    avoided on purpose: the digest of the *files* is meaningful even when the manifest is
    stale, which is precisely the state an operator debugging a drift would be in.
    """
    digest = hashlib.sha256(json.dumps(compute_digests(), sort_keys=True).encode("utf-8")).hexdigest()
    return f"{prefix}:{__version__}:{digest[:12]}"


def resolve_architecture_version(
    store: Any,
    source_root: Path,
    *,
    agent_version: str = __version__,
    engine: Any | None = None,
) -> str:
    """The registered architecture version, or a content-addressed stand-in. Never empty.

    ``engine`` is accepted so that a caller that already owns a ``MetamorphosisEngine`` can
    hand it over instead of paying for a second one (and for its bootstrap writes).
    """
    if engine is None:
        try:
            from ..metamorphosis import MetamorphosisEngine

            root = Path(source_root)
            if not root.is_dir():
                return content_address()
            engine = MetamorphosisEngine(store, root, agent_version=agent_version)
        except Exception:
            return content_address()
    try:
        manifest = engine.get_architecture()
        version = str(getattr(manifest, "architecture_version", "") or "")
    except Exception:
        version = ""
    return version or content_address()


__all__ = ["content_address", "resolve_architecture_version", "UNREGISTERED_PREFIX"]
