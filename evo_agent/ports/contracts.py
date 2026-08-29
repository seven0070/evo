"""The contracts between Evo and anything it integrates (07 §6).

Three rules shape every object in this module, and they are the reason the seams exist at all:

* **No verdict authority.** Nothing an adapter returns may say the work is *done*.
  ``TurnResult`` therefore has no ``success`` field, only ``status`` plus evidence; the
  sovereign verification authority decides. This is not a naming preference - DeepSeek Harness
  and DeerFlow both own their own completion notions, and importing one would import an
  authority (06 §14 L2).
* **Synchronous by contract.** ``run_turn`` is a blocking call. Async belongs inside a backend's
  own thread or process, never in the loop that calls it, because Evo's core is fully
  synchronous and ``dependencies = []`` (06 §11.1). ``I-sync-contract`` keeps it that way.
* **Additive.** Every optional member has a default, so adding a capability to a port cannot
  orphan an adapter that is already installed (R8). ``validate_implementation`` is how a
  registration proves it, rather than discovering it at the first call.

Stdlib only, no Evo imports: these are the floor everything else stands on.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import time
from typing import Any, Callable, Iterable, Protocol, Sequence, runtime_checkable


def sha256_text(text: str) -> str:
    """Digest of the exact bytes a caller was shown. Never a digest of a re-rendered view."""
    import hashlib

    return hashlib.sha256((text or "").encode("utf-8", errors="replace")).hexdigest()


def canonical_text(value: Any) -> str:
    """Deterministic text for hashing: sorted keys, no locale-dependent formatting."""
    import json

    try:
        return json.dumps(value, sort_keys=True, default=str, separators=(",", ":"))
    except (TypeError, ValueError):
        return repr(value)


#: Applied when a request arrives with a nonsensical bound. Kept next to the clamps below so the
#: number is not folklore.
DEFAULT_EXEC_TIMEOUT_SECONDS = 30.0
DEFAULT_EXEC_OUTPUT_BYTES = 1_000_000


class PortContractError(TypeError):
    """An implementation does not satisfy the port it registered against."""


# --- shared value objects --------------------------------------------------------------


@dataclass(frozen=True)
class CapabilityRequest:
    """What a caller needs, expressed without naming any tool or provider."""

    goal: str
    needed: tuple[str, ...] = ()
    workspace: Path | None = None
    permissions: tuple[str, ...] = ()
    budget_tokens: int | None = None
    deadline_seconds: float | None = None
    task_id: str = ""
    turn_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "goal": self.goal,
            "needed": list(self.needed),
            "workspace": str(self.workspace) if self.workspace else None,
            "permissions": list(self.permissions),
            "budget_tokens": self.budget_tokens,
            "deadline_seconds": self.deadline_seconds,
            "task_id": self.task_id,
            "turn_id": self.turn_id,
        }


@dataclass(frozen=True)
class BackendPlan:
    """A backend's answer to "can you do this, and at what cost"."""

    can_serve: bool
    reason: str
    estimated_turns: int | None = None
    requires_approval_for: tuple[str, ...] = ()
    degradation: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "can_serve": self.can_serve,
            "reason": self.reason,
            "estimated_turns": self.estimated_turns,
            "requires_approval_for": list(self.requires_approval_for),
            "degradation": self.degradation,
        }


@dataclass(frozen=True)
class BackendAvailability:
    """Result of a probe. Probing never raises; unavailability is data (R9)."""

    name: str
    available: bool
    reason: str = ""
    detail: dict[str, Any] = field(default_factory=dict)
    probed_at: float = field(default_factory=time.time)

    @property
    def install_hint(self) -> str:
        return str(self.detail.get("install_hint", ""))

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "available": self.available,
            "reason": self.reason,
            "detail": dict(self.detail),
            "probed_at": self.probed_at,
        }


@dataclass(frozen=True)
class Receipt:
    """One tool call's factual record, derived from the event log.

    Bound to an append id rather than a positional index: DeerFlow documents that compaction
    renumbers positional receipt ids, so a citation recorded before compaction can resolve to a
    different call afterwards (05 §1.1). ``ledger_seq`` is Evo's monotone append id.
    """

    ledger_seq: int
    turn_id: str
    tool: str
    canonical_name: str
    kind: str
    args_sha256: str
    output_sha256: str
    ok: bool
    duration_ms: float
    isolation: str = ""
    notes: tuple[str, ...] = ()

    @classmethod
    def record(
        cls,
        *,
        ledger_seq: int,
        turn_id: str,
        tool: str,
        canonical_name: str,
        kind: str,
        arguments: Any,
        output: str,
        ok: bool,
        duration_ms: float,
        isolation: str = "",
        notes: Iterable[str] = (),
    ) -> "Receipt":
        """Derive a receipt from what happened, hashing arguments and output here.

        The digests are computed in one place because a receipt whose ``output_sha256`` came from a
        different normalisation than the one the verifier recomputes is a receipt that fails for the
        wrong reason - and a failing verification is always assumed to be the payload's fault, not
        the accounting's.
        """
        return cls(
            ledger_seq=int(ledger_seq),
            turn_id=turn_id,
            tool=tool,
            canonical_name=canonical_name,
            kind=kind,
            args_sha256=sha256_text(canonical_text(arguments)),
            output_sha256=sha256_text(output or ""),
            ok=bool(ok),
            duration_ms=float(duration_ms),
            isolation=isolation,
            notes=tuple(notes),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "ledger_seq": self.ledger_seq,
            "turn_id": self.turn_id,
            "tool": self.tool,
            "canonical_name": self.canonical_name,
            "kind": self.kind,
            "args_sha256": self.args_sha256,
            "output_sha256": self.output_sha256,
            "ok": self.ok,
            "duration_ms": self.duration_ms,
            "isolation": self.isolation,
            "notes": list(self.notes),
        }


@dataclass(frozen=True)
class TurnContext:
    """Everything a backend may see about one turn. Derived, never authoritative.

    ``history`` is the assembled message list; the caller assembles it from the append-only
    event log so that a backend cannot keep private state that the audit never saw (R5).
    """

    goal: str
    workspace: Path
    turn_id: str
    task_id: str = ""
    history: tuple[dict[str, Any], ...] = ()
    available_tools: tuple[str, ...] = ()
    permissions: tuple[str, ...] = ()
    budget_turns: int = 1
    deadline_monotonic: float | None = None
    receipts: tuple[Receipt, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)

    def remaining_seconds(self) -> float | None:
        if self.deadline_monotonic is None:
            return None
        return max(0.0, self.deadline_monotonic - time.monotonic())

    def to_dict(self) -> dict[str, Any]:
        return {
            "goal": self.goal,
            "workspace": str(self.workspace),
            "turn_id": self.turn_id,
            "task_id": self.task_id,
            "history": list(self.history),
            "available_tools": list(self.available_tools),
            "permissions": list(self.permissions),
            "budget_turns": self.budget_turns,
            "receipt_count": len(self.receipts),
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True)
class TurnDecision:
    """The single step a turn engine chooses. ``final`` never means *verified*.

    ``request_approval`` exists so that "ask the operator" is a decision the loop makes, not a
    privilege a backend holds: an adapter cannot mint an approval, it can only request one
    (06 §14 L3).
    """

    kind: str  # tool_calls | final | request_approval | abstain
    tool_calls: tuple[dict[str, Any], ...] = ()
    text: str = ""
    approval: dict[str, Any] = field(default_factory=dict)
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "tool_calls": list(self.tool_calls),
            "text": self.text,
            "approval": dict(self.approval),
            "reason": self.reason,
        }


@dataclass(frozen=True)
class ArtifactRef:
    path: str
    kind: str = "file"
    sha256: str = ""
    size_bytes: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {"path": self.path, "kind": self.kind, "sha256": self.sha256, "size_bytes": self.size_bytes}


@dataclass(frozen=True)
class TurnResult:
    """What a backend hands back. Deliberately missing: ``success``.

    ``status`` describes what the *adapter* observed (``completed``, ``needs_approval``,
    ``blocked``, ``failed``, ``cancelled``, ``timeout``). Whether the goal is met is a verdict,
    and verdicts belong to ``sovereign/verification_authority`` (R1). A field named ``success``
    here would be a second authority wearing a dataclass.
    """

    status: str
    text: str = ""
    artifacts: tuple[ArtifactRef, ...] = ()
    receipts: tuple[Receipt, ...] = ()
    usage: dict[str, Any] = field(default_factory=dict)
    notes: tuple[str, ...] = ()
    origin: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "text": self.text,
            "artifacts": [item.to_dict() for item in self.artifacts],
            "receipts": [item.to_dict() for item in self.receipts],
            "usage": dict(self.usage),
            "notes": list(self.notes),
            "origin": self.origin,
        }


# --- isolation, expressed as a port so every execution path shares one boundary ---------


@dataclass(frozen=True)
class ExecRequest:
    """One confined process launch. ``argv`` only: a string command is refused.

    ``shell`` is intentionally absent from this dataclass. The defect it replaced was that
    policy lived in argv parsing alone (`SecurityPolicy.validate_command`) while the command
    reached the host through a shell, so the rules were advisory (00 §B.7). Confinement is the
    boundary; argv rules stay advisory *on top of* it.
    """

    argv: tuple[str, ...]
    cwd: Path
    writable: tuple[Path, ...] = ()
    read_only: tuple[Path, ...] = ()
    #: Paths the child must not see at all. The third leg of a mount set, and the one that was
    #: missing: read-only-ness and writability can both be expressed by a bind, but "the host's view
    #: of this directory is not information you should have" needs a mask (an empty tmpfs, or simply
    #: not binding it). Without a field for it, every provider would have to decide on its own what
    #: to hide, which is how a *confinement* layer becomes a policy layer.
    masked: tuple[Path, ...] = ()
    env: dict[str, str] = field(default_factory=dict)
    network: bool = False
    timeout_seconds: float = 30.0
    stdin: str | None = None
    max_output_bytes: int = 1_000_000
    label: str = ""
    task_id: str = ""

    def __post_init__(self) -> None:
        if isinstance(self.argv, str):
            raise ValueError(
                "ExecRequest.argv must be a sequence of arguments, not a command line: "
                "there is no shell to interpret it, by design"
            )
        if not self.argv:
            raise ValueError("ExecRequest requires a non-empty argv")
        if any(not isinstance(item, str) for item in self.argv):
            raise ValueError("ExecRequest.argv must be strings; pass a list, not a command line")
        # ``Path("")`` normalises to ``.``, which is precisely the implicit case being rejected -
        # so the check is on the rendered text, not on emptiness alone.
        if str(self.cwd).strip() in {"", ".", "./"}:
            raise ValueError(
                "ExecRequest.cwd must be an explicit directory: the workspace is what bounds what a "
                "child may write, so 'inherit from whoever started me' is not a boundary"
            )
        object.__setattr__(self, "cwd", Path(self.cwd))
        object.__setattr__(self, "writable", tuple(Path(item) for item in self.writable))
        object.__setattr__(self, "read_only", tuple(Path(item) for item in self.read_only))
        for field_name in ("writable", "read_only", "masked"):
            for item in getattr(self, field_name):
                if not str(item).startswith("/") and not str(item)[1:2] == ":":
                    raise ValueError(f"ExecRequest.{field_name} entries must be absolute paths, got {item!r}")
        object.__setattr__(self, "masked", tuple(Path(item) for item in self.masked))
        if self.timeout_seconds <= 0:
            # Clamped rather than rejected: this value arrives from a policy a user may edit, and a
            # ceiling that reads "0 means forever" is the failure this whole field exists to prevent.
            object.__setattr__(self, "timeout_seconds", DEFAULT_EXEC_TIMEOUT_SECONDS)
        if self.max_output_bytes <= 0:
            object.__setattr__(self, "max_output_bytes", DEFAULT_EXEC_OUTPUT_BYTES)

    def to_dict(self) -> dict[str, Any]:
        return {
            "argv": list(self.argv),
            "cwd": str(self.cwd),
            "writable": [str(item) for item in self.writable],
            "read_only": [str(item) for item in self.read_only],
            "network": self.network,
            "timeout_seconds": self.timeout_seconds,
            "label": self.label,
            "task_id": self.task_id,
        }


@dataclass(frozen=True)
class ExecResult:
    """The outcome of a confined launch, including *how* confined."""

    returncode: int
    output: str = ""
    isolated: bool = False
    provider: str = "none"
    refusal: str = ""
    degraded_reason: str = ""
    truncated: bool = False
    duration_ms: float = 0.0
    notes: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return self.returncode == 0 and not self.refusal

    def to_dict(self) -> dict[str, Any]:
        return {
            "returncode": self.returncode,
            "output_bytes": len(self.output.encode("utf-8")),
            "isolated": self.isolated,
            "provider": self.provider,
            "refusal": self.refusal,
            "degraded_reason": self.degraded_reason,
            "truncated": self.truncated,
            "duration_ms": self.duration_ms,
            "notes": list(self.notes),
        }


@dataclass(frozen=True)
class ProviderAvailability:
    """What a sandbox provider can promise right now. Never raises; absence is data."""

    name: str
    usable: bool
    reason: str = ""
    supports_network_denial: bool = False
    supports_read_only_mounts: bool = False
    supports_pid_namespace: bool = False
    detail: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "usable": self.usable,
            "reason": self.reason,
            "supports_network_denial": self.supports_network_denial,
            "supports_read_only_mounts": self.supports_read_only_mounts,
            "supports_pid_namespace": self.supports_pid_namespace,
            "detail": dict(self.detail),
        }


# --- the ports --------------------------------------------------------------------------


@runtime_checkable
class EventSink(Protocol):
    """How a backend reports progress. A sink that raises is a bug, not backpressure."""

    def emit(self, event: str, payload: dict[str, Any]) -> None: ...


def emit_event(sink: Any, event: str, payload: dict[str, Any] | None = None) -> None:
    """Emit through either an :class:`EventSink` or a bare callable, without assuming which."""
    if sink is None:
        return
    handler = getattr(sink, "emit", sink)
    handler(event, dict(payload or {}))


@runtime_checkable
class SandboxProvider(Protocol):
    """The only sanctioned path to a subprocess anywhere in Evo (R2 of the isolation model).

    ``run`` is synchronous and never raises for a refused launch: a refusal is a returned
    ``ExecResult`` with ``refusal`` set, so the caller's audit record looks the same whether the
    command ran confined, ran degraded, or was blocked (R7).
    """

    name: str

    def probe(self) -> ProviderAvailability: ...

    def run(self, request: ExecRequest, on_event: Callable[[str], None] | None = None) -> ExecResult: ...

    def prepare(self, request: ExecRequest) -> Any:
        """Wrap a launch the *caller* will manage, returning ``ConfinedLaunch``.

        Obligatory, not optional: a bridge that needs a long-lived child must take the wrapping from
        the provider. If it could build the command itself, the flags that decide whether network and
        writes are denied would exist in two places - which is how the tool path and the candidate
        sandbox drifted apart in the first place.
        """
        ...

    def terminate(self, handle: Any, grace_seconds: float = 2.0) -> bool:
        """Cancel a running launch. Optional: a provider that cannot signal may omit it."""
        return False


@runtime_checkable
class ExecutionBackend(Protocol):
    """An integrated runtime that can serve one turn. It does not own a loop (R2)."""

    name: str

    def probe(self) -> BackendAvailability: ...

    def plan_capability(self, request: CapabilityRequest) -> BackendPlan: ...

    def run_turn(self, context: TurnContext, sink: Any = None) -> TurnResult: ...

    def cancel(self, turn_id: str, reason: str = "operator") -> bool:
        """Optional: backends without a cancellable child may omit it."""
        return False

    def export_receipts(self, turn_id: str) -> Sequence[Receipt]:
        """Optional: replay the receipts a backend recorded for a turn."""
        return ()


@runtime_checkable
class TurnEngine(Protocol):
    """Evo's own step chooser. Kept a port so P4 can swap the implementation without moving
    the authority: the engine decides *what to do next*, never whether the goal is met."""

    def next_turn(self, context: TurnContext) -> TurnDecision: ...

    def compact(self, context: TurnContext, budget: int) -> TurnContext: ...


@runtime_checkable
class VerifierPlugin(Protocol):
    """Advisory verification. May tighten, never loosen (07 §4 E3)."""

    name: str

    def expects(self, step: dict[str, Any]) -> tuple[str, ...]: ...

    def assess(self, step: dict[str, Any], result: dict[str, Any], receipts: Sequence[Receipt]) -> dict[str, Any]: ...


def _annotations_of(protocol: type) -> set[str]:
    names: set[str] = set()
    for namespace in reversed(protocol.__mro__):
        if namespace is object:
            continue
        for key in (getattr(namespace, "__annotations__", None) or {}):
            if not key.startswith("_"):
                names.add(key)
    return names


def _is_stub(function: Any) -> bool:
    """True for a body of only ``...`` or only ``pass`` - a declared obligation, not behaviour."""
    import ast
    import inspect
    import textwrap

    try:
        source = textwrap.dedent(inspect.getsource(function))
    except (OSError, TypeError, IndentationError):
        return True
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return True
    definition = tree.body[0] if tree.body else None
    if not isinstance(definition, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return True
    body = [
        statement
        for statement in definition.body
        if not (isinstance(statement, ast.Expr) and isinstance(statement.value, ast.Constant) and isinstance(statement.value.value, str))
    ]
    if len(body) != 1:
        return False
    statement = body[0]
    if isinstance(statement, ast.Pass):
        return True
    return isinstance(statement, ast.Expr) and isinstance(statement.value, ast.Constant) and statement.value.value is Ellipsis


def required_members(protocol: type) -> tuple[str, ...]:
    """Every member a protocol names - methods and declared attributes alike."""
    names = _annotations_of(protocol)
    for namespace in reversed(protocol.__mro__):
        if namespace is object:
            continue
        for key, value in vars(namespace).items():
            if key.startswith("_") or not callable(value):
                continue
            names.add(key)
    return tuple(sorted(names))


def optional_members(protocol: type) -> tuple[str, ...]:
    """Members that already carry an implementation, i.e. an adapter may omit them.

    This is the R8 rule made checkable: a protocol member with a default body can be added later
    without orphaning the adapters installed before it existed. A stub body (``...``) is an
    obligation instead.
    """
    found: set[str] = set()
    for namespace in reversed(protocol.__mro__):
        for key, value in vars(namespace).items():
            if key.startswith("_") or not callable(value):
                continue
            if getattr(value, "__isabstractmethod__", False) or _is_stub(value):
                continue
            found.add(key)
    return tuple(sorted(found))


def validate_implementation(candidate: Any, protocol: type) -> list[str]:
    """Return the obligations ``candidate`` fails to meet. Empty means it may register.

    Called at registration, not at first use: a backend that discovers a missing method half way
    through a turn has already taken an irreversible action.
    """
    missing: list[str] = []
    for member in required_members(protocol):
        if member in optional_members(protocol):
            continue
        if getattr(candidate, member, None) is None:
            missing.append(member)
            continue
        attribute = getattr(candidate, member)
        if callable(attribute) or not isinstance(attribute, property):
            continue
        missing.append(f"{member} (unresolved property)")
    return missing


def additive(protocol: type) -> type:
    """Declare a port additive (R8) and record its obligations for the registry to check.

    The decorator is not documentation: ``validate_implementation`` and the
    ``I-ports-contract`` invariant read ``__port_required__`` to prove that adding a member to a
    port has not orphaned an installed adapter.
    """
    protocol.__evo_port__ = True
    optional = set(optional_members(protocol))
    protocol.__port_required__ = tuple(member for member in required_members(protocol) if member not in optional)
    protocol.__port_optional__ = tuple(optional)
    return protocol


def call_optional(target: Any, member: str, *args: Any, default: Any = None, **kwargs: Any) -> Any:
    """Invoke an optional port member if the implementation has one, else ``default``."""
    attribute = getattr(target, member, None)
    if attribute is None:
        return default
    try:
        return attribute(*args, **kwargs)
    except TypeError as exc:
        if "unexpected keyword argument" in str(exc) or "takes" in str(exc):
            raise PortContractError(f"{type(target).__name__}.{member} rejected its arguments: {exc}") from exc
        raise


def as_tuple(items: Iterable[Any] | None) -> tuple[Any, ...]:
    return tuple(items or ())


# Declared here rather than as class decorators because the helpers above must exist first.
# Every port is additive: that is what lets a later phase grow an interface without breaking an
# adapter that was installed before the new member existed (R8).
for _protocol in (EventSink, SandboxProvider, ExecutionBackend, TurnEngine, VerifierPlugin):
    additive(_protocol)

PORTS: tuple[type, ...] = (EventSink, SandboxProvider, ExecutionBackend, TurnEngine, VerifierPlugin)
