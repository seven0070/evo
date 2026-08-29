"""The single authority that decides whether anything in Evo may act (07 §6 mediation).

``ApprovalMediator`` exists because a unified system that grew two entry points to the same
capability would end up with two policies, and the weaker one would always be the effective one.
Before this, the kernel gated a tool call through ``SecurityPolicy`` plus an operator approval,
while every integrated harness would have brought its own notion of "is this tool allowed"
(DeerFlow's is a 1,700-line configuration section); a merge would have had to pick one, and picks
decay. So the rules are:

* the native tool path and every bridge call ``execute``/``authorize`` here - nothing else decides
  whether a process may start;
* the decision is always recorded, including denials, because a denial that leaves no trace cannot
  be distinguished from a request that was never made, which is how a capability silently
  disappears during an integration;
* approval is a fact the request must carry (``approved=True``, set by the kernel after the
  operator answered, or an approver callback), and the absence of that fact is a denial;
* when enforcement is ``strict`` and no isolation provider is usable the answer is *no* - the
  absence of a sandbox is never treated as permission.

``SecurityPolicy`` stays the schema for the rules; this class is the decision point. The split is
deliberate: an audit can read what is allowed without running anything, while enforcement of it
lives in exactly one place.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
import shlex
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence

from ..models import RiskLevel, ToolCall
from ..ports.contracts import ExecRequest, ExecResult
from ..sandbox_providers import IsolationSettings, run_confined
from ..security import SecurityPolicy


#: Approver signature: ``(tool_name, arguments) -> bool``. ``None`` means only automatic rules
#: apply, which is the correct default for an unattended run: an absent human is not consent.
ApprovalCallback = Callable[[str, dict[str, Any]], bool]
EventCallback = Callable[[str, dict[str, Any]], None]


@dataclass(frozen=True)
class MediationDecision:
    """One authorize decision, carrying the rule that produced it.

    ``rule`` is not decoration. When an operator later asks "why was that blocked", the answer has
    to distinguish *policy denied* (a rule fired) from *no sandbox available* (a capability is
    missing) from *not approved* (nobody consented) - the remedies differ, and a log that says only
    "denied" invites someone to relax the wrong thing.
    """

    allowed: bool
    rule: str
    reason: str
    source: str = "mediator"
    isolated: bool = False
    details: dict[str, Any] = field(default_factory=dict)

    @property
    def text(self) -> str:
        return self.reason if self.allowed else f"denied ({self.rule}): {self.reason}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "allowed": self.allowed,
            "rule": self.rule,
            "reason": self.reason,
            "source": self.source,
            "isolated": self.isolated,
            "details": dict(self.details),
        }


class ApprovalMediator:
    """Decides and enforces: policy rules, then approval evidence, then isolation.

    The order matters. Approval is asked only for a request the policy has not already rejected, so
    an operator is never prompted to bless something that will be refused anyway; isolation is
    checked last, so a refusal that really means "this host cannot confine" is not mislabelled as
    a policy violation.
    """

    def __init__(
        self,
        policy: SecurityPolicy | None = None,
        *,
        approver: ApprovalCallback | None = None,
        on_event: EventCallback | None = None,
        source_root: Path | str | None = None,
        providers: Sequence[Any] | None = None,
    ) -> None:
        self.policy = policy if policy is not None else SecurityPolicy(Path.cwd())
        self.approver = approver
        self.on_event = on_event
        self.providers = tuple(providers) if providers is not None else None
        self.source_root = self._resolve_source_root(source_root)

    @property
    def workspace_root(self) -> Path:
        return Path(getattr(self.policy, "workspace", Path.cwd()))

    @staticmethod
    def _resolve_source_root(source_root: Path | str | None) -> Path | None:
        """What the child must not write to. Defaults to Evo's own package directory.

        This is the mechanical half of "self-modification is controlled": a tool cannot edit
        ``evo_agent/sovereign/protected.py`` and then have the running process agree with it,
        because that path is not writable inside the namespace. An explicit value wins, so a
        self-hosting run whose workspace *is* the checkout can decide that trade-off itself.
        """
        if source_root is not None:
            path = Path(source_root).expanduser()
            return path.resolve() if path.exists() else None
        candidate = Path(__file__).resolve().parents[1]
        return candidate if candidate.is_dir() else None

    def settings(self) -> IsolationSettings:
        return IsolationSettings(
            enforcement=getattr(self.policy, "sandbox_enforcement", "auto"),
            preferred=getattr(self.policy, "sandbox_provider", "auto"),
            host_permitted=False,
            host_permit_reason="not permitted through the mediator",
        )

    def read_only_roots(self, workspace_root: Path | str | None = None) -> tuple[Path, ...]:
        """The source tree, plus whatever else the policy declares read-only."""
        if not getattr(self.policy, "source_read_only", True):
            return ()
        workspace = Path(workspace_root or self.workspace_root)
        roots: list[Path] = []
        try:
            resolved_workspace = workspace.resolve()
        except OSError:
            resolved_workspace = workspace
        candidate = self.source_root
        if candidate is not None and candidate.exists() and candidate != resolved_workspace:
            roots.append(candidate)
        for extra in tuple(getattr(self.policy, "sandbox_read_only_paths", ()) or ()):
            path = Path(extra)
            if path.exists() and path not in roots:
                roots.append(path)
        return tuple(roots)

    def evaluate(self, request: ExecRequest, *, tool_name: str = "shell", arguments: dict[str, Any] | None = None, risk: RiskLevel | None = None, approved: bool = False) -> MediationDecision:
        """Decide without acting and without recording. Safe for tests and dry runs."""
        decision, _amended = self._decide(
            request,
            tool_name=tool_name,
            arguments=arguments,
            risk=risk,
            approved=approved,
            record=False,
        )
        return decision

    def authorize(self, request: ExecRequest, *, tool_name: str = "shell", arguments: dict[str, Any] | None = None, risk: RiskLevel | None = None, approved: bool = False) -> tuple[MediationDecision, ExecRequest]:
        """Evaluate, record the decision, and return the request as the mediator amended it."""
        return self._decide(request, tool_name=tool_name, arguments=arguments, risk=risk, approved=approved, record=True)

    def execute(self, request: ExecRequest, *, tool_name: str = "shell", arguments: dict[str, Any] | None = None, risk: RiskLevel | None = None, approved: bool = False) -> ExecResult:
        """The only path from a model-visible request to a running process.

        Denials return a refusal :class:`ExecResult` instead of raising, because the tool layer turns
        results into observations for the model and an exception there would surface as a crash
        report rather than a legible refusal the model can act on.
        """
        decision, amended = self._decide(
            request,
            tool_name=tool_name,
            arguments=arguments,
            risk=risk,
            approved=approved,
            record=True,
        )
        if not decision.allowed:
            return ExecResult(returncode=-1, output="", isolated=False, provider="mediator", refusal=decision.text)
        return run_confined(amended, settings=self.settings(), providers=self.providers, on_event=self._event_for(amended))

    def authorize_infrastructure(self, request: ExecRequest, *, program: Path | str, tool_name: str) -> tuple[MediationDecision, ExecRequest]:
        """Mediate the launch of Evo's own child program (a bridge driver, a configured CLI).

        This is not a bypass. The command allowlist and the workspace path rule exist to constrain
        *what a model asked for*; an infrastructure launch is a program the operator configured, so
        those rules would reject the bridge's own driver - a path inside the install - for the same
        reason they reject a model's ``cat /etc/passwd``. What replaces them is stricter for this
        case, not looser: the request must name the configured program and nothing else. A bridge
        that could choose its own argv would be a bridge that could run anything, which is the exact
        capability this phase is meant to keep on the Evo side.

        Isolation, network denial, and output bounds still apply, and any command the *child* wants
        to run must still come back through :meth:`execute`.
        """
        expected = str(program)
        actual = str(request.argv[0]) if request.argv else ""
        if not request.argv:
            decision = MediationDecision(False, "empty_request", "no program was supplied")
            return self._finish(decision, request, True)
        if Path(actual).name != Path(expected).name and actual != expected:
            return self._finish(
                MediationDecision(
                    False,
                    "infrastructure_argv_mismatch",
                    f"a bridge may only launch its configured program '{expected}', not '{actual}'",
                    details={"tool": tool_name, "expected": expected, "actual": actual},
                ),
                request,
                True,
            )
        amended = ExecRequest(
            argv=tuple(str(item) for item in request.argv),
            cwd=Path(request.cwd),
            timeout_seconds=request.timeout_seconds,
            max_output_bytes=request.max_output_bytes,
            read_only=request.read_only or self.read_only_roots(request.cwd),
            env=dict(request.env),
            network=False,
            stdin=request.stdin,
            label=request.label or f"infrastructure.{tool_name}",
        )
        enforcement = getattr(self.policy, "sandbox_enforcement", "auto")
        if request.network:
            return self._finish(MediationDecision(False, "policy", "infrastructure launches never get network access"), amended, True)
        if enforcement == "strict":
            from ..sandbox_providers import select
            from ..sandbox_providers.base import IsolationUnavailable

            try:
                selected = select(self.settings(), self.providers)
            except IsolationUnavailable as exc:
                return self._finish(MediationDecision(False, "no_isolation", str(exc), details={"enforcement": enforcement}), amended, True)
            if getattr(selected, "name", "") == "host":
                return self._finish(MediationDecision(False, "no_isolation", "strict enforcement cannot run a bridge outside the sandbox", details={"provider": "host"}), amended, True)
        return self._finish(
            MediationDecision(
                True,
                "allowed",
                "configured program, confined, no egress; every command the child asks for is mediated separately",
                isolated=enforcement != "off",
                details={"enforcement": enforcement, "argv": list(amended.argv)[:2]},
            ),
            amended,
            True,
        )

    def execute_infrastructure(self, request: ExecRequest, *, program: Path | str, tool_name: str) -> ExecResult:
        """Run a configured child program. Refusals return, never raise."""
        decision, amended = self.authorize_infrastructure(request, program=program, tool_name=tool_name)
        if not decision.allowed:
            return ExecResult(returncode=-1, output="", isolated=False, provider="mediator", refusal=decision.text)
        return run_confined(amended, settings=self.settings(), providers=self.providers, on_event=self._event_for(amended))

    def prepare_infrastructure(self, request: ExecRequest, *, program: Path | str, tool_name: str) -> tuple[Any, MediationDecision]:
        """Wrap a long-lived child launch, after the same identity and isolation checks.

        Returns ``(launch, decision)``; ``launch`` is ``None`` when the decision denied it. A
        separate entry point exists because :meth:`execute_infrastructure` cannot serve a child the
        bridge has to keep talking to - and the alternative, letting bridges call
        ``prepare_launch`` directly, would leave the identity rule unenforced for exactly the
        integration that most needs it.
        """
        decision, amended = self.authorize_infrastructure(request, program=program, tool_name=tool_name)
        if not decision.allowed:
            return None, decision
        from ..sandbox_providers import prepare_launch

        try:
            return prepare_launch(amended, settings=self.settings(), providers=self.providers), decision
        except Exception as exc:
            return None, MediationDecision(False, "launch_failed", f"{type(exc).__name__}: {exc}")

    def _decide(self, request: ExecRequest, *, tool_name: str, arguments: dict[str, Any] | None, risk: RiskLevel | None, approved: bool, record: bool) -> tuple[MediationDecision, ExecRequest]:
        """The single place the three rules are applied, in order.

        One function, with ``evaluate`` and ``authorize`` as thin wrappers, because two copies of a
        policy decision is how the pre-merge state came to exist in the first place.
        """
        payload = dict(arguments or {})
        if not request.argv:
            return self._finish(MediationDecision(False, "empty_request", "no command was supplied", details={"tool": tool_name}), request, record)
        if request.network:
            # Refused before anything else, and before the provider's own check, so the reason is
            # "this phase grants no egress" rather than a confusing capability complaint later.
            return self._finish(
                MediationDecision(False, "policy", "network access is not granted by any provider in this build", details={"tool": tool_name}),
                request,
                record,
            )
        command_text = _join(request.argv)
        try:
            allowed_by_policy, reason = self.policy.validate_command(command_text)
        except Exception as exc:  # a broken rule must never read as permission
            return self._finish(MediationDecision(False, "policy_error", f"policy check raised {type(exc).__name__}: {exc}"), request, record)
        if not allowed_by_policy:
            return self._finish(MediationDecision(False, "policy", str(reason), details={"tool": tool_name}), request, record)
        amended = ExecRequest(
            argv=tuple(request.argv),
            cwd=Path(request.cwd),
            timeout_seconds=request.timeout_seconds,
            max_output_bytes=request.max_output_bytes,
            read_only=request.read_only or self.read_only_roots(request.cwd),
            env=dict(request.env),
            network=False,
            stdin=request.stdin,
            label=request.label or f"tool.{tool_name}",
        )
        gate = ToolCall(tool_name=tool_name, arguments=payload, risk=risk if risk is not None else RiskLevel.MEDIUM, approved=approved)
        if self.policy.requires_approval(gate) and not approved:
            if self.approver is None:
                return self._finish(
                    MediationDecision(
                        False,
                        "unapproved",
                        f"'{tool_name}' is classified as {gate.risk.value}-risk and carries no approval evidence; "
                        "an unattended run does not imply consent",
                        details={"tool": tool_name, "risk": gate.risk.value},
                    ),
                    amended,
                    record,
                )
            if not bool(self.approver(tool_name, payload)):
                return self._finish(MediationDecision(False, "unapproved", f"operator declined '{tool_name}'", details={"tool": tool_name}), amended, record)
            amended = replace(amended, env={**amended.env, "EVO_APPROVED": "1"})
        enforcement = getattr(self.policy, "sandbox_enforcement", "auto")
        if enforcement == "strict":
            from ..sandbox_providers import select
            from ..sandbox_providers.base import IsolationUnavailable

            try:
                selected = select(self.settings(), self.providers)
            except IsolationUnavailable as exc:
                return self._finish(MediationDecision(False, "no_isolation", str(exc), details={"enforcement": enforcement}), amended, record)
            if getattr(selected, "name", "") == "host":
                return self._finish(
                    MediationDecision(
                        False,
                        "no_isolation",
                        "strict enforcement cannot run through the unconfined host provider",
                        details={"provider": getattr(selected, "name", "?")},
                    ),
                    amended,
                    record,
                )
        decision = MediationDecision(
            True,
            "allowed",
            "policy, approval, and isolation requirements satisfied",
            isolated=enforcement != "off",
            details={
                "enforcement": enforcement,
                "argv_count": len(amended.argv),
                "read_only": [str(item) for item in amended.read_only],
                "approval_evidence": "carried" if approved else ("callback" if self.approver is not None else "not_required"),
            },
        )
        return self._finish(decision, amended, record)

    def _finish(self, decision: MediationDecision, request: ExecRequest, record: bool) -> tuple[MediationDecision, ExecRequest]:
        if record:
            self._record(decision, request)
        return decision, request

    def _event_for(self, request: ExecRequest) -> EventCallback | None:
        if self.on_event is None:
            return None

        def forward(kind: str, payload: dict[str, Any]) -> None:
            try:
                self.on_event(kind, {"label": request.label, "argv": list(request.argv)[:4], **payload})
            except Exception:
                pass  # R9: an audit hook that raises must never break the action it audits

        return forward

    def _record(self, decision: MediationDecision, request: ExecRequest) -> None:
        if self.on_event is None:
            return
        try:
            self.on_event(
                "mediation_decision",
                {
                    "allowed": decision.allowed,
                    "rule": decision.rule,
                    "reason": decision.reason,
                    "source": decision.source,
                    "label": request.label,
                    "argv": list(request.argv)[:4],
                },
            )
        except Exception:
            pass

    def grant_approval(self, tool_name: str, arguments: dict[str, Any] | None = None, *, risk: RiskLevel | None = None) -> MediationDecision:
        """The approval path a bridge uses when it holds an approval *request*, not a command.

        ``approval_required`` capabilities reach here. A bridge may not decide on the user's behalf
        and may not proceed as if approved: a denial is returned and must be surfaced verbatim.
        """
        payload = dict(arguments or {})
        gate = ToolCall(tool_name=tool_name, arguments=payload, risk=risk if risk is not None else RiskLevel.MEDIUM)
        if not self.policy.requires_approval(gate):
            return MediationDecision(True, "not_required", f"'{tool_name}' needs no approval")
        if self.approver is None:
            return MediationDecision(False, "unapproved", f"'{tool_name}' needs approval and no approver is wired")
        approved = bool(self.approver(tool_name, payload))
        return MediationDecision(
            approved,
            "approved" if approved else "unapproved",
            "operator approved" if approved else "operator declined",
            details={"tool": tool_name, "risk": gate.risk.value},
        )


def _join(argv: Iterable[str]) -> str:
    """Render argv as shell text for the policy's rules, which are written against command lines.

    Quoting on the way in is what keeps that check honest: an unquoted join would let a path with
    spaces re-tokenise into something the allowlist never saw.
    """
    return " ".join(shlex.quote(str(item)) for item in argv)
