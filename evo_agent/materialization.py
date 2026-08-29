"""Materializers: the write side of the seam that makes promotion change behaviour.

An approved payload arrives as data and leaves as an :class:`~evo_agent.ports.evolution_target.OverlayFragment`
inside ``<version>/overlay/``. What happens in between is the entire discipline of this phase, so the
refusals are the interesting code:

* A payload naming a document whose loader does not exist yet is **refused**, not written. This
  repository's founding finding is a config file nothing read (00 §B.3); producing another one would
  be the same mistake with a benchmark attached. The refusal names the phase that will build the
  loader, so the reader knows the gap is scheduled rather than forgotten.
* A payload naming a field outside the allow-list is refused *field by field*, and the reasons are
  returned rather than raised, because "why did my candidate not materialize" is a question a human
  has to be able to answer from the ledger.
* A payload that would widen a protection is refused by the schema, not by a policy check downstream:
  removing ``permission`` from the never-retry set, or lowering a risk floor, has no representation
  in the documents at all.
* Source code is not expressible: the fragment shape rejects the suffix before a materializer is even
  consulted (07 §4: "No - structurally impossible").

Six materializers cover the target kinds the phases need: the four from the comparison study's
materialization table (``skill``, ``tool_binding``, ``provider_config``, ``pipeline_stage``) plus the
two the normative document added (``strategy_params``, ``memory_policy``). They are thin by design -
validation is data in :mod:`evo_agent.active_version`, so the runtime's reader and this writer cannot
disagree about what a document means.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import json
from pathlib import Path
import re
from typing import Any

from .active_version import (
    DOCUMENTS,
    IMMUTABLE_NEVER_RETRY,
    TARGET_TO_KIND,
    DocumentSpec,
    SKILL_NAME_MAX,
)
from .ports.evolution_target import (
    MAX_FRAGMENT_BYTES,
    OVERLAY_DIRNAME,
    OVERLAY_MANIFEST,
    OverlayFragment,
    materializer_obligations,
    overlay_digest,
)

#: Frontmatter keys a materialized skill must carry. Mirrors the upstream convention so a skill the
#: project later imports is already valid here, and so a skill without a description cannot be
#: proposed as a "capability" whose purpose nobody can review.
SKILL_REQUIRED_FRONTMATTER: tuple[str, ...] = ("name", "description")
SKILL_MAX_LINES = 400
_SKILL_NAME = re.compile(r"^[a-z0-9][a-z0-9._-]{0,%d}$" % (SKILL_NAME_MAX - 1))
#: Executable-ish content inside a SKILL.md is refused on the text, not only on the suffix: a fenced
#: block of shell is a payload wearing a document's filename.
_SKILL_EXECUTABLE_MARKERS: tuple[str, ...] = ("#!/", "chmod +x", "curl ", "wget ", "eval ", "subprocess")


class MaterializationError(ValueError):
    """A payload that cannot be materialized, with every reason attached."""

    def __init__(self, errors: list[str], *, kind: str = "", payload: dict[str, Any] | None = None) -> None:
        self.errors = list(errors)
        self.kind = kind
        self.payload_digest = overlay_digest(())
        super().__init__("; ".join(self.errors) or f"refused {kind or 'payload'}")


@dataclass(frozen=True)
class MaterializationResult:
    """What a materialization produced: fragments on disk, the digest that identifies them, proof."""

    kind: str
    fragments: tuple[OverlayFragment, ...]
    written: tuple[str, ...] = ()
    errors: tuple[str, ...] = ()
    notes: tuple[str, ...] = field(default_factory=tuple)

    @property
    def ok(self) -> bool:
        return not self.errors

    @property
    def digest(self) -> str:
        return overlay_digest(self.fragments)

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "digest": self.digest,
            "fragments": [fragment.to_dict() for fragment in self.fragments],
            "written": list(self.written),
            "errors": list(self.errors),
            "notes": list(self.notes),
        }


def _documents_payload(payload: Any) -> tuple[dict[str, Any], list[str]]:
    """Accept ``{relpath: body}`` or ``{"documents": {relpath: body}}``. One shape would be enough.

    Two are accepted because the second is what a human writes in a proposal review and the first is
    what an orchestrator builds from an opportunity; forcing one on the other produced, in earlier
    phases, a silent ``{}`` payload that materialized nothing and reported success.
    """
    if not isinstance(payload, dict):
        return {}, ["payload must be an object of overlay documents"]
    body = payload.get("documents") if "documents" in payload else payload
    if not isinstance(body, dict):
        return {}, ["payload['documents'] must be an object"]
    if not body:
        return {}, ["payload carries no documents"]
    return dict(body), []


def _loader_gate(spec: DocumentSpec) -> str:
    """The reason a document may not be written yet, or ``""`` when it may."""
    if spec.loadable:
        return ""
    return (
        f"{spec.relpath}: nothing loads it before {spec.phase} "
        f"(kind {spec.kind!r} is declared for {spec.phase}; refusing rather than writing dead config)"
    )


class BaseMaterializer:
    """Shared machinery: check ownership, validate against the document table, write, digest.

    Subclasses declare which kinds they own and may add a check of their own. They may *not* relax a
    check, which is why there is no hook for it: the schema in :mod:`evo_agent.active_version` is the
    single allow-list, and a per-materializer override would be a second governance surface with fewer
    readers.
    """

    target_kind = ""
    #: relpaths this materializer may write, and the kind a caller must name to reach them.
    owned_documents: tuple[str, ...] = ()

    @property
    def documents(self) -> tuple[str, ...]:  # the port's declared attribute
        return self.owned_documents

    def specs(self) -> tuple[DocumentSpec, ...]:
        return tuple(DOCUMENTS[relpath] for relpath in self.owned_documents if relpath in DOCUMENTS)

    def validate(self, payload: dict[str, Any]) -> list[str]:
        documents, problems = _documents_payload(payload)
        for relpath, body in documents.items():
            spec = DOCUMENTS.get(relpath)
            if spec is None:
                problems.append(f"{relpath}: not a document this project knows how to load")
                continue
            if spec.kind != self.target_kind or relpath not in self.owned_documents:
                problems.append(f"{relpath}: belongs to {spec.kind!r}, not {self.target_kind!r}")
                continue
            gate = _loader_gate(spec)
            if gate:
                problems.append(gate)
                continue
            cleaned, field_problems = spec.validate(body)
            # ``None`` is what "nothing in this document survived" means. Extra checks then run on an
            # empty view rather than skipping, so a caller sees the field problems *and* the rule the
            # payload also violated, not whichever check happened to be first.
            problems.extend(field_problems)
            problems.extend(self.extra_checks(relpath, cleaned or {}))
        return problems

    def extra_checks(self, relpath: str, cleaned: dict[str, Any]) -> list[str]:
        return []

    def write_candidate(self, payload: dict[str, Any], destination: Path) -> OverlayFragment | None:
        documents, _ = _documents_payload(payload)
        errors = self.validate(payload)
        if errors:
            raise MaterializationError(errors, kind=self.target_kind, payload=documents)
        written: list[Path] = []
        fragments: list[OverlayFragment] = []
        root = Path(destination) / OVERLAY_DIRNAME
        for relpath, body in documents.items():
            spec = DOCUMENTS[relpath]
            cleaned, field_problems = spec.validate(body)
            if field_problems:
                raise MaterializationError(field_problems, kind=self.target_kind, payload=documents)
            fragment = OverlayFragment.json_document(self.target_kind, relpath, cleaned, notes=(f"loader={spec.loaded_by or 'none'}",))
            target = root / relpath
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(fragment.content, encoding="utf-8")
            fragments.append(fragment)
            written.append(target)
        self.write_manifest(root, fragments)
        return fragments[0] if len(fragments) == 1 else None

    @staticmethod
    def write_manifest(root: Path, fragments: tuple[OverlayFragment, ...] | list[OverlayFragment]) -> Path:
        """Record what was written and with which digest, next to the files themselves.

        The manifest is not the authority - the digest of the fragments is - but a candidate directory
        that says which proposal produced it and when is the difference between an auditable
        experiment and a directory of mystery files.
        """
        payload = {
            "schema": "evo-overlay-materialization-v1",
            "digest": overlay_digest(tuple(fragments)),
            "fragments": [fragment.to_dict() for fragment in fragments],
        }
        path = Path(root) / OVERLAY_MANIFEST
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return path

    def digest(self, fragment: OverlayFragment) -> str:
        return fragment.digest


class StrategyParametersMaterializer(BaseMaterializer):
    """Budgets, thresholds, retry counts and recovery knobs (``config/runtime.json``).

    ``recovery`` gets one extra rule beyond the schema: the never-retry set may grow. ``permission``
    and ``approval`` failures are the two classes where retrying is the attack, so a payload that
    names a set without them is not being strict about wording - it is asking for them to stop being
    protected.
    """

    target_kind = "strategy_params"
    owned_documents = ("config/runtime.json",)

    def extra_checks(self, relpath: str, cleaned: dict[str, Any]) -> list[str]:
        problems: list[str] = []
        recovery = cleaned.get("recovery") or {}
        never = recovery.get("never_retry")
        if isinstance(never, list):
            missing = [name for name in IMMUTABLE_NEVER_RETRY if name not in never]
            if missing:
                problems.append(
                    "config/runtime.json.recovery.never_retry: may only grow; it omits "
                    + ", ".join(missing)
                    + ", which are never retriable whatever a candidate proposes"
                )
        return problems


class PipelineStageMaterializer(BaseMaterializer):
    """Planner shape and stage parameters from a reviewed allow-list (never stage source)."""

    target_kind = "pipeline_stage"
    owned_documents = ("config/cognitive_policy.json", "config/strategy.json", "config/heuristics.json")


class ToolBindingMaterializer(BaseMaterializer):
    """Preference order over the tools that exist, and risk floors that may only rise."""

    target_kind = "tool_binding"
    owned_documents = ("config/tools.json",)


class ProviderConfigMaterializer(BaseMaterializer):
    """Provider and prompt selection inside the allow-list. Isolation *downgrade* is not expressible."""

    target_kind = "provider_config"
    owned_documents = ("config/prompts.json",)

    def extra_checks(self, relpath: str, cleaned: dict[str, Any]) -> list[str]:
        # R7: a candidate may not select a weaker isolation provider. It is refused here as well as
        # by the schema, because a future document may add a provider field and the schema would
        # allow-list it; this sentence is where "never weaker than the default" lives.
        text = json.dumps(cleaned, sort_keys=True).lower()
        if "host" in text or "no_isolation" in text or "disabled" in text:
            return [f"{relpath}: may not select an isolation provider weaker than the default (R7)"]
        return []


class MemoryPolicyMaterializer(BaseMaterializer):
    """Extraction, retention, staleness and retrieval weights. Policies only, never contents."""

    target_kind = "memory_policy"
    owned_documents = ("config/memory.json",)


#: The skill document, declared outside the table because a skill is not a JSON document: one file
#: per bundle, named by the bundle. Kept as a ``DocumentSpec`` so ``_loader_gate`` answers the same
#: question for it as for every other target instead of a special case that can drift.
SKILL_SPEC = DocumentSpec(
    relpath=f"capabilities/skills/installed/<{SKILL_NAME_MAX}-char-name>/SKILL.md",
    kind="skill",
    risk="Medium",
    loaded_by="",
    phase="P5",
    notes="the catalog that reads it is P5 work; the validation below is the contract it will load against",
)


class SkillMaterializer(BaseMaterializer):
    """One skill bundle directory under the active overlay, validated as a document.

    A skill is the only target whose payload is prose plus structure rather than a key map, so this
    class carries the checks a document does not need: name shape, no path traversal, a bounded
    length, required frontmatter, and a refusal when the body reads like an installer.

    It refuses to *write*, in this phase, for the same reason every unloaded document does: nothing
    loads a skill directory until the catalog exists (P5). That is why the validation is written now
    and in full - the checks are the contract the loader will be built against, and having them
    already tested is what stops P5 from inventing a weaker set under time pressure.
    """

    target_kind = "skill"
    owned_documents: tuple[str, ...] = ()

    def validate(self, payload: dict[str, Any]) -> list[str]:
        if not isinstance(payload, dict):
            return ["skill payload must be an object"]
        gate = _loader_gate(SKILL_SPEC)
        name = payload.get("name")
        body = payload.get("content") or payload.get("skill")
        problems = [gate]
        if not isinstance(name, str) or not _SKILL_NAME.match(name):
            problems.append(f"skill name {name!r} must match [a-z0-9][a-z0-9._-]{{0,{SKILL_NAME_MAX - 1}}}")
        if not isinstance(body, str) or not body.strip():
            problems.append("skill content must be non-empty text")
            return problems
        if len(body.encode("utf-8")) > MAX_FRAGMENT_BYTES:
            problems.append(f"skill content exceeds {MAX_FRAGMENT_BYTES} bytes")
        if body.count("\n") > SKILL_MAX_LINES:
            problems.append(f"skill content exceeds {SKILL_MAX_LINES} lines")
        lines = body.splitlines()
        if not lines or lines[0].strip() != "---":
            problems.append("skill content must open with YAML frontmatter ('---')")
        else:
            front: dict[str, str] = {}
            for line in lines[1:]:
                if line.strip() == "---":
                    break
                key, _, value = line.partition(":")
                if _:
                    front[key.strip()] = value.strip()
            missing = [key for key in SKILL_REQUIRED_FRONTMATTER if not front.get(key)]
            if missing:
                problems.append("skill frontmatter is missing: " + ", ".join(missing))
            elif front.get("name") != name:
                problems.append(f"skill frontmatter name {front.get('name')!r} does not match {name!r}")
        lowered = body.lower()
        found = [marker for marker in _SKILL_EXECUTABLE_MARKERS if marker in lowered]
        if found:
            problems.append("skill content contains executable-shaped instructions: " + ", ".join(found))
        return problems

    def write_candidate(self, payload: dict[str, Any], destination: Path) -> OverlayFragment | None:
        """Refuses, always, until a skill loader exists. The refusal is the feature.

        Kept as a method rather than an absent one so that the day the catalog lands, the change is
        "delete this body and add the document to the table" - a diff a reviewer can read - instead
        of a new class that nobody cross-checked against the rules above.
        """
        errors = self.validate(payload)
        raise MaterializationError(errors or [f"{SKILL_SPEC.relpath}: no skill loader before {SKILL_SPEC.phase}"], kind=self.target_kind)


#: The registry, in the order a report should list them.
MATERIALIZERS: tuple[BaseMaterializer, ...] = (
    StrategyParametersMaterializer(),
    PipelineStageMaterializer(),
    ToolBindingMaterializer(),
    ProviderConfigMaterializer(),
    MemoryPolicyMaterializer(),
    SkillMaterializer(),
)

_BY_KIND: dict[str, BaseMaterializer] = {materializer.target_kind: materializer for materializer in MATERIALIZERS}


def materializer_for(kind: str) -> BaseMaterializer | None:
    return _BY_KIND.get(str(kind or "").strip())


def kinds() -> tuple[str, ...]:
    return tuple(materializer.target_kind for materializer in MATERIALIZERS)


def for_target(target: str) -> BaseMaterializer | None:
    """Resolve a sandbox target string to its materializer, or nothing when the name is unknown."""
    kind = TARGET_TO_KIND.get(str(target or "").strip().lower())
    return materializer_for(kind) if kind else None


def materialize(target: str, payload: dict[str, Any], destination: Path) -> MaterializationResult:
    """Materialize a payload for ``target`` into ``destination/overlay/``, or report why not.

    Returns a result rather than raising for the ordinary refusal cases, because the caller is an
    evolution loop: a rejected payload is a recorded outcome, not a crash. The exception survives only
    for a payload that reached a materializer's own writer and failed its checks, which is a bug in
    the caller's sequencing rather than a governance decision.
    """
    kind = TARGET_TO_KIND.get(str(target or "").strip().lower())
    if not kind:
        return MaterializationResult(kind="", fragments=(), errors=(f"unsupported candidate target: {target!r}",))
    materializer = materializer_for(kind)
    if materializer is None:
        return MaterializationResult(kind=kind, fragments=(), errors=(f"no materializer for target kind {kind!r}",))
    errors = materializer.validate(payload)
    if errors:
        return MaterializationResult(kind=kind, fragments=(), errors=tuple(errors))
    documents, _ = _documents_payload(payload)
    written: list[str] = []
    fragments: list[OverlayFragment] = []
    for relpath, body in documents.items():
        spec = DOCUMENTS[relpath]
        cleaned, field_problems = spec.validate(body)
        if field_problems:
            return MaterializationResult(kind=kind, fragments=(), errors=tuple(field_problems))
        fragment = OverlayFragment.json_document(kind, relpath, cleaned, notes=(f"loader={spec.loaded_by or 'none'}",))
        target_path = Path(destination) / OVERLAY_DIRNAME / relpath
        target_path.parent.mkdir(parents=True, exist_ok=True)
        target_path.write_text(fragment.content, encoding="utf-8")
        fragments.append(fragment)
        written.append(str(target_path))
    if fragments:
        BaseMaterializer.write_manifest(Path(destination) / OVERLAY_DIRNAME, fragments)
    return MaterializationResult(
        kind=kind,
        fragments=tuple(fragments),
        written=tuple(written),
        notes=(f"destination={Path(destination)}", f"target={target}"),
    )


def registry_problems() -> list[str]:
    """Every way the registry itself is broken. Empty means it may be used.

    Checked at import-adjacent time (a test calls it) rather than per materialization: a materializer
    that does not satisfy its port is a build error, and discovering it while writing a candidate
    would leave a half-written version directory.
    """
    problems: list[str] = []
    for materializer in MATERIALIZERS:
        problems.extend(f"{materializer.target_kind or '<unnamed>'}: {item}" for item in materializer_obligations(materializer))
        for relpath in materializer.owned_documents:
            spec = DOCUMENTS.get(relpath)
            if spec is None:
                problems.append(f"{materializer.target_kind}: owns document {relpath!r} that the table does not declare")
            elif spec.kind != materializer.target_kind:
                problems.append(f"{materializer.target_kind}: owns {relpath!r}, which the table assigns to {spec.kind!r}")
    for target in TARGET_TO_KIND:
        if for_target(target) is None:
            problems.append(f"target {target!r} maps to no materializer")
    return problems
