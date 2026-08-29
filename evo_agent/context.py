"""Context assembly: the token meter, compaction, and spill that keep a turn bounded (07 §4, P4).

Three upstream lessons are encoded here, and all three are about what happens to *evidence* when the
window fills up.

**Sanitize before truncation.** A cut applied to unsanitized text can end mid-secret and hand the
remainder to the next stage as if it were prose. So the same byte budget is spent on scrubbed text.

**Truncate with a marker, never in silence.** ``output_budget`` exists because an oversized payload
is a real event, and a reader who cannot tell a clipped result from a complete one will verify the
wrong thing. The marker is part of the returned text, and the note is part of the audit record.

**Spill instead of dropping.** A payload too large for the window is written under the workspace and
replaced by a digest plus a preview. The model does not need 4 MB to decide what to do next; the
ledger needs it to prove what happened, and a compaction that discards evidence to save context is a
verification authority wearing a memory-management costume (06 §12.3, where both upstreams landed on
exactly this split).

Nothing in this module plans or judges. The meter measures, and the numbers it reports only ever move
upward within one object: a turn that has consumed 30 000 tokens has consumed them whether or not
the context that produced them is still in the window.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
from typing import Any, Sequence


#: Rough tokens-per-byte ratio. Deliberately crude and deliberately fixed: an estimate that looks
#: precise gets compared against a budget as though it were the provider's own counter, and the
#: provider's counter is not available here. Two upstreams shipped the same approximation; the
#: honest part is the name.
TOKEN_BYTES_PER_TOKEN = 4

#: Anything above this is control noise rather than content. ``\t`` and ``\n`` are excluded, and
#: ``\r`` is normalised rather than removed, because a tool that prints progress on ``\r`` is
#: describing its own output, not smuggling a delimiter.
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_ZERO_WIDTH = re.compile(r"[​-‏⁠⁯]")
#: Directions a model may be steered by, from inside a tool payload. Removal is not a defence
#: against a determined harness - the sandbox is the defence - but a payload that has to spell its
#: instructions in plain text is a payload a reviewer can find in the ledger.
_OBFUSCATED_MARKERS = (
    "ignore previous instructions",
    "ignore all previous",
    "disregard the above",
    "system prompt:",
)
_SECRET_SHAPES = (
    re.compile(r"(?i)(api[_-]?key|token|password|secret|authorization|credential)\s*[:=]\s*[^\s,;]+"),
    re.compile(r"(?i)bearer\s+[A-Za-z0-9._~+/=-]{8,}"),
    re.compile(r"\b(?:sk|ghp|github_pat|xox[baprs])-[A-Za-z0-9_-]{12,}\b"),
)
REDACTION = "[REDACTED]"

#: The truncation marker, with room for the byte count so a reader can tell how much is missing.
TRUNCATION_MARKER = "…[truncated {dropped} of {total} bytes]"


@dataclass(frozen=True)
class CompactReport:
    """What one compaction did. ``dropped`` is never silent: the caller records it."""

    dropped: int = 0
    pinned: int = 0
    kept: int = 0

    def to_dict(self) -> dict[str, int]:
        return {"dropped": self.dropped, "pinned": self.pinned, "kept": self.kept}


@dataclass(frozen=True)
class SpillRecord:
    """One oversized payload moved from the context window to the workspace."""

    path: str
    sha256: str
    bytes_written: int
    preview: str = ""
    turn_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "sha256": self.sha256,
            "bytes": self.bytes_written,
            "preview_bytes": len(self.preview.encode("utf-8", "replace")),
            "turn_id": self.turn_id,
        }


@dataclass(frozen=True)
class TokenEstimate:
    """The meter's reading for one assembly. Estimates are reported with their method attached."""

    goal_tokens: int = 0
    history_tokens: int = 0
    total_tokens: int = 0
    entries: int = 0
    method: str = f"bytes/{TOKEN_BYTES_PER_TOKEN}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "goal_tokens": self.goal_tokens,
            "history_tokens": self.history_tokens,
            "total_tokens": self.total_tokens,
            "entries": self.entries,
            "method": self.method,
        }


@dataclass
class TokenMeter:
    """Cumulative usage for one context assembly. Readings never decrease.

    The monotone rule is the point. A meter that can go down makes compaction look like savings, and
    a system that believes it saved tokens will raise its budget to spend them again; a monotone
    meter says the tokens were spent and the window merely stopped showing them (06 §12.3).
    """

    spent: int = 0
    samples: int = 0
    peak: int = 0

    def observe(self, estimate: TokenEstimate | int) -> int:
        total = estimate.total_tokens if isinstance(estimate, TokenEstimate) else int(estimate)
        total = max(0, total)
        self.spent += total
        self.samples += 1
        self.peak = max(self.peak, total)
        return self.spent

    def over(self, ceiling: int) -> bool:
        return self.spent > max(0, int(ceiling))

    def to_dict(self) -> dict[str, int]:
        return {"spent": self.spent, "samples": self.samples, "peak": self.peak}


def sanitize_text(text: str) -> tuple[str, list[str]]:
    """Strip control noise, neutralise steering phrases, redact credential-shaped runs.

    Returns ``(cleaned, notes)``; the notes are what the ledger gets, so a reader can tell that
    output *changed on the way in* rather than discovering it by diffing two records.
    """
    body = str(text or "")
    notes: list[str] = []
    cleaned = _CONTROL.sub("", body).replace("\r\n", "\n").replace("\r", "\n")
    if cleaned != body:
        notes.append("control characters removed")
    cleaned = _ZERO_WIDTH.sub("", cleaned)
    lowered = cleaned.lower()
    for marker in _OBFUSCATED_MARKERS:
        if marker in lowered:
            cleaned = re.sub(re.escape(marker), "[neutralised instruction]", cleaned, flags=re.IGNORECASE)
            notes.append(f"steering phrase neutralised: {marker}")
            lowered = cleaned.lower()
    for pattern in _SECRET_SHAPES:
        cleaned, hits = pattern.subn(lambda match: _keep_label(match) + REDACTION, cleaned)
        if hits:
            notes.append(f"credential-shaped text redacted ({hits})")
    return cleaned, notes


def _keep_label(match: "re.Match[str]") -> str:
    """Keep the left-hand label of a ``key: value`` secret so the redaction stays auditable.

    The caller appends :data:`REDACTION`, so this returns the label and its separator only. It looks
    like a detail and is one: a replacement that repeats the marker makes the record say the value
    was redacted twice, which reads like an attempt to hide something about the hiding.
    """
    text = match.group(0)
    for separator in (":", "="):
        if separator in text:
            label = text.split(separator, 1)[0].strip()
            if label and len(label) < 40:
                return f"{label}{separator} "
    return ""


def compact_text(text: str, *, limit: int = 65_536) -> tuple[str, list[str]]:
    """Sanitize, then bound to ``limit`` bytes with an explicit marker. ``(text, notes)``."""
    cleaned, notes = sanitize_text(text)
    encoded = cleaned.encode("utf-8", "replace")
    budget = max(0, int(limit))
    if len(encoded) <= budget:
        return cleaned, notes
    head = encoded[:budget].decode("utf-8", "ignore")
    marker = TRUNCATION_MARKER.format(dropped=len(encoded) - budget, total=len(encoded))
    return head + marker, notes + [f"output truncated to {budget} bytes"]


def estimate_tokens(value: Any) -> int:
    """Rough token count of anything JSON-renderable. Never raises (R9)."""
    try:
        text = value if isinstance(value, str) else json.dumps(value, sort_keys=True, default=str)
    except (TypeError, ValueError):
        text = str(value)
    return max(1, len(text.encode("utf-8", "replace")) // TOKEN_BYTES_PER_TOKEN) if text else 0


def meter(goal: str = "", history: Sequence[dict[str, Any]] | None = None) -> TokenEstimate:
    """The meter's reading for one assembly: goal plus history, sized the same way every time."""
    entries = list(history or ())
    goal_tokens = estimate_tokens(goal)
    history_tokens = sum(estimate_tokens(item) for item in entries)
    return TokenEstimate(
        goal_tokens=goal_tokens,
        history_tokens=history_tokens,
        total_tokens=goal_tokens + history_tokens,
        entries=len(entries),
    )


def spill_text(text: str, *, root: Path, turn_id: str = "", preview_bytes: int = 512) -> SpillRecord:
    """Write an oversized payload under ``root`` and return the record a prompt may cite.

    The filename carries a random suffix rather than a digest of the payload alone, because two
    identical payloads from different turns must not collide into one file whose provenance is
    ambiguous; the digest is still what a verifier compares.
    """
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    body = str(text or "")
    encoded = body.encode("utf-8", "replace")
    digest = hashlib.sha256(encoded).hexdigest()
    handle = secrets.token_hex(4)
    target = root / f"spill-{digest[:16]}-{handle}.txt"
    descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(descriptor, encoded)
    finally:
        os.close(descriptor)
    return SpillRecord(
        path=str(target),
        sha256=digest,
        bytes_written=len(encoded),
        preview=encoded[: max(0, int(preview_bytes))].decode("utf-8", "ignore"),
        turn_id=turn_id,
    )


def render_context(
    goal: str,
    history: Sequence[dict[str, Any]] | None = None,
    *,
    limit: int = 16_384,
    tool_names: Sequence[str] = (),
) -> str:
    """The bounded prompt-facing view of a turn. Truncation is marked; nothing is dropped quietly."""
    lines = [f"GOAL: {compact_text(goal, limit=4096)[0]}"]
    if tool_names:
        lines.append("TOOLS: " + ", ".join(str(name) for name in tool_names))
    for item in list(history or ()):
        rendered = compact_text(str(item.get("content", item)), limit=1024)[0]
        lines.append(f"{item.get('role', 'event')}: {rendered}")
    return compact_text("\n".join(lines), limit=limit)[0]


__all__ = [
    "CompactReport",
    "REDACTION",
    "SpillRecord",
    "TOKEN_BYTES_PER_TOKEN",
    "TRUNCATION_MARKER",
    "TokenEstimate",
    "TokenMeter",
    "compact_text",
    "estimate_tokens",
    "meter",
    "render_context",
    "sanitize_text",
    "spill_text",
]
