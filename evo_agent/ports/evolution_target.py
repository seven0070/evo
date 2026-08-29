"""The shape of a thing the evolution spine may change, and the mount set that carries it.

Why a seam at all: an approved payload has to become a file the runtime reads, and the two sides -
the spine that proposes and the substrate that loads - must not have to know each other. Without
this boundary the natural shortcut is for the promotion engine to start importing the runtime (or
the reverse), and then "what may change" and "what is running" live in one module again, which is
the condition 07 §4 exists to prevent. So the fragment is data, the materializer is a declared
shape, and neither can reach a verdict.

This module deliberately contains **no policy**. The allow-list of *keys*, the clamps, and which
kinds have a loader are governance, and they live in :mod:`evo_agent.active_version` (imported by
the materializers, never the other way round). What lives here is only what has to be shared to be
understood: the fragment shape, the subpath allow-list it must fall inside, the mount set, and the
digest rule that makes "these are the same capabilities" a computable question.

The strictest thing here is ``OverlayFragment.__post_init__``. A fragment that cannot represent a
path outside the allow-list means a buggy or hostile materializer still cannot write one, and the
check does not have to be remembered at every call site.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
from typing import Any, Protocol

from .contracts import additive


#: Bumped when the *meaning* of a digest changes, not when a document's content does. Two digests
#: from different overlay versions must never be comparable, because the failure would be silent:
#: the post-activation check would pass on a digest of a different set of things.
OVERLAY_SCHEMA_VERSION = "evo-overlay-v2"

#: Directory inside a version that holds the materialized overlay.
OVERLAY_DIRNAME = "overlay"

#: The file the materializer writes alongside the fragments: what was materialized, from which
#: proposal, and with which digest. Read by ``active_version`` and by the promotion health check.
OVERLAY_MANIFEST = "manifest.json"

#: The only subpaths of an overlay that any loader may read, and who reads them. A subpath is a
#: *directory prefix*, never a filename, so that adding a file to a sanctioned directory does not
#: require a governance change while adding a directory does. Everything else in the overlay is
#: ignored by the resolver and recorded as a warning - the warning is the point, since a silently
#: dropped file is how a shadowed default becomes invisible (07 §8, S11).
ALLOWED_SUBPATHS: dict[str, str] = {
    "config/": "the runtime's own documents, read at cycle start (evo_agent/active_version.py)",
    "capabilities/skills/installed/": "skill bundles, read by the skill catalog when it exists (P5)",
}

#: Per-fragment and per-overlay ceilings. A limit that is only enforced by the writer can be
#: bypassed by any future writer, so the shape enforces it too.
MAX_FRAGMENT_BYTES = 65_536
MAX_OVERLAY_BYTES = 1_048_576
MAX_RELDEPTH = 8

#: Never materialized, whatever the directory: source and executable payloads. This is the
#: structural half of "Evo does not evolve by editing its own source" (07 §4, FORBIDDEN_PAYLOADS) -
#: a blocklist of *names* would only reject the names someone anticipated.
SOURCE_SUFFIXES: frozenset[str] = frozenset({
    ".py", ".pyc", ".pyo", ".so", ".dll", ".dylib", ".sh", ".bash", ".zsh", ".fish",
    ".rb", ".pl", ".js", ".mjs", ".ts", ".exe", ".com", ".bat", ".cmd",
})

#: Path elements that make a relative path unsuitable no matter how it was produced.
FORBIDDEN_PATH_PARTS: frozenset[str] = frozenset({"..", "", "~", ".git", ".evo", "__pycache__", ".pytest_cache", "node_modules"})

_SAFE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


def relpath_is_allow_listed(relpath: str) -> tuple[bool, str]:
    """Whether ``relpath`` may exist in an overlay. Returns ``(ok, reason)`` and never raises.

    Kept as a function rather than an exception so a caller can *report* a refused fragment (which
    is a governance event) instead of crashing a cycle over it.
    """
    text = str(relpath or "").replace("\\", "/").strip()
    if not text:
        return False, "empty relative path"
    if text.startswith("/") or re.match(r"^[A-Za-z]:", text):
        return False, "absolute paths are not permitted in an overlay"
    parts = PurePosixPath(text).parts
    if not parts:
        return False, "no path components"
    bad = [part for part in parts if part in FORBIDDEN_PATH_PARTS]
    if bad:
        return False, f"forbidden path component(s): {', '.join(sorted(set(bad)))}"
    if len(parts) > MAX_RELDEPTH:
        return False, f"deeper than {MAX_RELDEPTH} path components"
    suffix = PurePosixPath(text).suffix.lower()
    if suffix in SOURCE_SUFFIXES:
        return False, f"a {suffix or 'extensionless'} payload cannot be materialized (source is not an evolution target)"
    for allowed in ALLOWED_SUBPATHS:
        if text.startswith(allowed) and len(text) > len(allowed):
            return True, ""
    return False, f"not under an allow-listed subpath ({', '.join(sorted(ALLOWED_SUBPATHS))})"


@dataclass(frozen=True)
class OverlayFragment:
    """One file of a materialized overlay: a path, the bytes, and the digest of exactly those bytes.

    The digest is of ``content`` as written, per the rule in :mod:`evo_agent.ports.contracts` that a
    digest is never taken of a re-rendered view. Re-serializing JSON before hashing would make the
    same document produce different digests under two different writers, and the post-activation
    comparison would then be noise.
    """

    kind: str
    relpath: str
    content: str
    notes: tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        ok, reason = relpath_is_allow_listed(self.relpath)
        if not ok:
            raise ValueError(f"overlay fragment {self.relpath!r} is not allowed: {reason}")
        if not isinstance(self.content, str):
            raise ValueError("an overlay fragment is text; bytes must be decoded by the materializer")
        if "\x00" in self.content:
            raise ValueError("an overlay fragment may not contain NUL bytes")
        size = len(self.content.encode("utf-8"))
        if size > MAX_FRAGMENT_BYTES:
            raise ValueError(f"overlay fragment {self.relpath!r} is {size} bytes, over the {MAX_FRAGMENT_BYTES} limit")
        if self.relpath.endswith(".json"):
            try:
                parsed = json.loads(self.content)
            except ValueError as exc:
                raise ValueError(f"overlay fragment {self.relpath!r} is not valid JSON: {exc}") from exc
            if not isinstance(parsed, dict):
                raise ValueError(f"overlay fragment {self.relpath!r} must contain a JSON object, not {type(parsed).__name__}")
        if not self.kind or not _SAFE_NAME.match(self.kind):
            raise ValueError(f"unusable fragment kind: {self.kind!r}")

    @property
    def digest(self) -> str:
        return hashlib.sha256(self.content.encode("utf-8", errors="strict")).hexdigest()

    @property
    def size_bytes(self) -> int:
        return len(self.content.encode("utf-8"))

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "relpath": self.relpath,
            "digest": self.digest,
            "size_bytes": self.size_bytes,
            "notes": list(self.notes),
        }

    @classmethod
    def json_document(cls, kind: str, relpath: str, payload: dict[str, Any], notes: tuple[str, ...] = ()) -> "OverlayFragment":
        """Serialize a validated document with a stable rendering (sorted keys, two-space indent).

        The stability is load-bearing: ``write_candidate`` is called by the sandbox and again at
        activation, and if the two renderings differed by whitespace the digests would differ and
        the activation check would report tampering that never happened.
        """
        return cls(kind=kind, relpath=relpath, content=json.dumps(payload, indent=2, sort_keys=True) + "\n", notes=notes)


@dataclass(frozen=True)
class MountSet:
    """What a confined child may write, what it may read read-only, and what it may not see.

    The third field is the one that makes ``unshare`` and ``bwrap`` describable in the same terms:
    bwrap masks by *not binding*, and a namespace can mask by mounting an empty filesystem over a
    path. Without ``masked`` the honest translation of "the host root is not bound" is "everything
    is readable", which no provider wants recorded about itself.
    """

    #: absolute paths bound read-only
    read_only: tuple[str, ...] = field(default_factory=tuple)
    #: absolute paths bound writable
    writable: tuple[str, ...] = field(default_factory=tuple)
    #: absolute paths the child cannot see at all
    masked: tuple[str, ...] = field(default_factory=tuple)
    #: per-namespace capabilities that are not mounts: network is the interesting one
    deny_network: bool = True
    deny_host_pids: bool = True

    def validate(self) -> list[str]:
        """Cross-checks a caller can display instead of debugging an ``execvp`` failure."""
        problems: list[str] = []
        for label, paths in (("read_only", self.read_only), ("writable", self.writable), ("masked", self.masked)):
            for path in paths:
                if not str(path).startswith("/"):
                    problems.append(f"{label}: {path!r} is not absolute")
                if PurePosixPath(str(path)) != PurePosixPath(str(path)):
                    problems.append(f"{label}: {path!r} is not normalized")
                suffix = PurePosixPath(str(path)).suffix.lower()
                if suffix in SOURCE_SUFFIXES and suffix not in {".so"} and label != "masked":
                    problems.append(f"{label}: {path!r} names an executable artifact")
        for path in set(self.writable) & set(self.masked):
            problems.append(f"a path cannot be both writable and masked: {path}")
        # Nesting is not reported as a problem in either direction, and the reason is worth keeping
        # next to the code that would otherwise guess. A read-only path inside a writable parent looks
        # escapable - rewrite the parent, recreate the child - except the read-only path is itself a
        # mount point, and unlinking a mount point answers EBUSY. A writable path inside a read-only
        # root is how both providers are built (bind, then remount the bind). What makes either case
        # safe is not the arithmetic of prefixes: it is that a provider which could not apply a mount
        # it promised refuses to run. That guarantee lives in the sentinel handling in the providers,
        # and no check here should pretend to replace it.
        overlap = set(self.read_only) & set(self.writable)
        if overlap:
            problems.append(f"paths cannot be both read-only and writable: {', '.join(sorted(overlap))}")
        return problems

    @classmethod
    def for_execution(cls, workspace: Path, *, read_only: tuple[str, ...] = (), scratch: Path | None = None) -> "MountSet":
        """The mount set of one confined command: the task's own tree writable, everything else not.

        ``workspace`` is writable because a tool that cannot write its scratch directory is not a
        tool. ``read_only`` is the caller's declaration of what must additionally be pinned
        (``SecurityPolicy.source_read_only`` supplies the source root), and the masked set is where a
        provider is *expected* to add host paths it cannot otherwise hide.
        """
        scratch_path = Path(scratch or workspace)
        return cls(
            read_only=tuple(dict.fromkeys(str(Path(item).resolve()) for item in read_only)),
            writable=tuple(dict.fromkeys([str(Path(workspace).resolve()), str(scratch_path.resolve())])),
            masked=(),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "read_only": list(self.read_only),
            "writable": list(self.writable),
            "masked": list(self.masked),
            "deny_network": self.deny_network,
            "deny_host_pids": self.deny_host_pids,
        }


@additive
class Materializer(Protocol):
    """Turns an approved payload into a fragment, or refuses.

    ``validate`` returns reasons rather than raising so a proposal can be rejected with the list
    shown to a human: the reason *is* the audit. ``write_candidate`` may return ``None`` when the
    payload is a documented no-op, which is different from refusal and must not be reported as
    either. ``digest`` is a member rather than a free function because a materializer that stores
    more than one file has to decide what its fragment's digest covers - and if the spine decided
    that for it, every materializer would have to agree with a rule it cannot see.
    """

    #: The eligibility-registry name this materializer owns (``sovereign/eligibility.py``).
    target_kind: str
    #: Overlay-relative paths this kind writes. Declared as data so a test can assert the resolver
    #: and the writer agree on them without importing either.
    documents: tuple[str, ...] = ()

    def validate(self, payload: dict[str, Any]) -> list[str]:
        ...

    def write_candidate(self, payload: dict[str, Any], destination: Path) -> OverlayFragment | None:
        ...

    def digest(self, fragment: OverlayFragment) -> str:
        ...


def materializer_obligations(candidate: Any) -> list[str]:
    """Missing obligations of a would-be materializer, in the ports package's own format."""
    from .contracts import validate_implementation

    return validate_implementation(candidate, Materializer)


def overlay_digest(fragments: tuple[OverlayFragment, ...] | list[OverlayFragment]) -> str:
    """The digest of an effective capability set: the sorted fragments, plus the schema version.

    Order-independent by construction (sorted by relpath), content-sensitive (each fragment's own
    digest), and empty-overlay-digests-to-a-constant so "nothing is overlaid" is a value that can be
    compared rather than a ``None`` that each caller interprets on its own. Duplicates collapse: an
    overlay is a set of paths, and two entries for the same path would otherwise imply an order.
    """
    rows = sorted({(fragment.relpath, fragment.digest) for fragment in fragments})
    # ``kind`` is metadata about who wrote the fragment, and is deliberately *not* in the digest:
    # the writer knows the eligibility kind ("strategy_params") while the reader walking the tree
    # only sees a directory name ("config"). Hashing a field the two sides derive differently would
    # make the post-activation comparison fail on a difference that is only in how each describes it.
    payload = {"schema": OVERLAY_SCHEMA_VERSION, "fragments": [{"relpath": relpath, "digest": digest} for relpath, digest in rows]}
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def verify_fragment_tree(root: Path) -> tuple[list[OverlayFragment], list[str]]:
    """Read an on-disk overlay directory back into fragments, and report what was refused.

    Used by both sides of the digest comparison (the active version and a candidate), so that the
    question "did activation change what the runtime would load?" is answered by walking the same
    tree the loader would walk, not by trusting a manifest that a failed write could have left
    behind. Files outside the allow-list come back as warnings rather than fragments.
    """
    fragments: list[OverlayFragment] = []
    warnings: list[str] = []
    root = Path(root)
    if not root.is_dir():
        return fragments, [f"overlay root does not exist: {root}"]
    #: Warnings are reported, not exhausted. A caller that points this at a source tree would otherwise
    #: turn every non-overlay file into an event payload, and a truncated event is an unhelpful one.
    max_warnings = 24
    total = 0
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        relpath = path.relative_to(root).as_posix()
        if relpath == OVERLAY_MANIFEST:
            continue
        ok, reason = relpath_is_allow_listed(relpath)
        if not ok:
            if len(warnings) < max_warnings:
                warnings.append(f"ignored {relpath}: {reason}")
            elif len(warnings) == max_warnings:
                warnings.append("further ignored paths suppressed")
            continue
        try:
            content = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            warnings.append(f"unreadable {relpath}: {exc}")
            continue
        total += len(content.encode("utf-8"))
        if total > MAX_OVERLAY_BYTES:
            warnings.append(f"overlay exceeds {MAX_OVERLAY_BYTES} bytes; fragments after the limit are ignored")
            break
        try:
            fragments.append(OverlayFragment(kind=PurePosixPath(relpath).parts[0], relpath=relpath, content=content))
        except ValueError as exc:
            warnings.append(f"refused {relpath}: {exc}")
    return fragments, warnings
