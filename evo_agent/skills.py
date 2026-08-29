"""Skills: prose and structure the agent may be *given*, never code it may run.

A skill is the one evolution target whose payload is text a model will read. That makes it
simultaneously the most useful and the most dangerous kind of candidate, so this module treats it as
three separate problems rather than one:

**Content validity** is not re-implemented here. :class:`evo_agent.materialization.SkillMaterializer`
holds the contract the writer already enforced in P3 - name shape, frontmatter, size, line count, and
executable-shaped instructions - and every bundle read or staged through this module is validated by
that same class. Two validators for one document is how a loader ends up accepting what the writer
refused, which is the defect ``00`` §A is a catalogue of.

**Install safety** is the hardening trio: path shape (no traversal, no absolute path, no drive letter,
no colon - which is also what rejects an NTFS alternate-stream name on any platform), bundle size
(running total and member count, so a compressed pile of nothing is refused before it is unpacked), and
file *kind* (executables and code are refused: a skill that can run is a skill that does not need this
module). A refusal writes nothing, anywhere, and the report says which rule fired.

**Authority** is deliberately absent. A skill may name the tools it expects, and :meth:`SkillCatalog
.tool_policy` refuses a name that is not canonical and registered rather than clamping the list -
DeerFlow clamps and documents that clamping is not validation. A skill may request secrets, and the
values never enter a prompt: they are names the caller may resolve into a child's environment, and
autonomous use of them requires both the frontmatter flag and an operator grant. None of this makes a
skill able to approve, promote, relax isolation, or decide that a goal was met.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Sequence

#: Where a bundle lives, relative to whichever root is in force. Mirrors ``SKILL_SPEC.relpath`` in
#: :mod:`evo_agent.materialization`; the pin test in ``tests/test_skills.py`` keeps the two identical,
#: because a loader that looks in a different directory from the writer is a loader for nothing.
SKILL_SUBPATH = Path("capabilities") / "skills" / "installed"
SKILL_FILENAME = "SKILL.md"

#: Bundle ceilings. The member count is its own limit because a directory of ten thousand empty files
#: passes any byte budget and still has to be walked, hashed, and copied.
MAX_BUNDLE_BYTES = 8 * 1024 * 1024
MAX_BUNDLE_MEMBERS = 64
#: What a skill file may be. Prose and data, per the module docstring; ``.py`` and ``.sh`` are absent
#: on purpose, and their absence is a refusal with a reason rather than a silent skip.
ALLOWED_SUFFIXES = frozenset({".md", ".txt", ".json", ".yaml", ".yml"})

_NAME_SHAPE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
#: Frontmatter keys. ``required-secrets`` and ``secrets-autonomous`` keep DeerFlow's spellings, since a
#: ported skill should load without being edited; ``enabled`` is Evo's own, and it is what the
#: projection is built from.
MANIFEST_KEYS = ("name", "description", "allowed-tools", "required-secrets", "secrets-autonomous", "enabled")
#: Literal credentials, in the shapes that actually get pasted into a file by mistake.
#:
#: The rule is not a secret *scanner* in the entropy sense - it is a shape list - and it exists because a
#: skill's body is text a model reads, and everything a model reads is something a model can be induced to
#: repeat. Refusing a literal here costs one edit by a human who meant to write ``required-secrets``;
#: accepting one puts the credential in the transcript, the event log, and every later summary of it.
_SECRET_LITERALS = (
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{16,}\b"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b"),
    re.compile(r"""(?i)\b(api[_ -]?key|access[_ -]?token|secret|password)\b\s*[:=]\s*["']?[A-Za-z0-9/+_.:-]{20,}"""),
)
_INJECTION_PATTERNS = (
    "ignore previous instructions",
    "ignore all previous",
    "disregard your instructions",
    "do not tell the user",
    "hide this from",
    "system prompt",
    "exfiltrat",
)


def run_scanner(scanner: "SecurityScanner", text: str) -> list[SkillFinding]:
    """Run a scanner, turning the scanner's own failure into a blocking finding.

    DeerFlow's equivalent treats unparseable scanner output as a block, and the reason transfers: a crash
    that lets a bundle through means the control exists only on the days the control happens to work. A
    free function rather than a method because both the loader and the installer must fail the same way,
    and two copies of a fail-closed rule is one copy too many.
    """
    try:
        return list(scanner.scan(text))
    except Exception as exc:  # noqa: BLE001 - any failure here is a refusal, by the rule above
        return [
            SkillFinding(
                "scanner_unavailable",
                f"the content scanner failed ({type(exc).__name__}); the bundle is refused until it can be reviewed",
            )
        ]


def _split_list(value: str) -> tuple[str, ...]:
    """Parse ``[a, b]`` or ``a, b`` frontmatter. Returns a tuple; nothing here is a list that can drift."""
    text = (value or "").strip()
    if text.startswith("[") and text.endswith("]"):
        text = text[1:-1]
    return tuple(item.strip().strip("'\"") for item in text.split(",") if item.strip())


@dataclass(frozen=True)
class SkillManifest:
    """The frontmatter, as data with a shape. Carries no capability of its own."""

    name: str
    description: str = ""
    allowed_tools: tuple[str, ...] = ()
    required_secrets: tuple[str, ...] = ()
    secrets_autonomous: bool = False
    enabled: bool = True

    @classmethod
    def from_frontmatter(cls, front: dict[str, str], *, directory_name: str = "") -> tuple["SkillManifest | None", list[str]]:
        problems: list[str] = []
        name = (front.get("name") or "").strip()
        if not name:
            problems.append("skill frontmatter is missing 'name'")
        elif directory_name and name != directory_name:
            # The bundle is addressed by its directory, so a second identity inside it is not a
            # description, it is a claim - and a skill named after something else could be staged under
            # a name a reviewer already approved.
            problems.append(f"skill name {name!r} does not match its directory {directory_name!r}")
        flag = (front.get("secrets-autonomous") or "false").strip().lower()
        if flag not in {"true", "false", "yes", "no", "1", "0"}:
            problems.append(f"secrets-autonomous must be a boolean, found {flag!r}")
        enabled = (front.get("enabled") or "true").strip().lower()
        if enabled not in {"true", "false", "yes", "no", "1", "0"}:
            problems.append(f"enabled must be a boolean, found {enabled!r}")
        truthy = {"true", "yes", "1"}
        if problems:
            return None, problems
        return (
            cls(
                name=name,
                description=(front.get("description") or "").strip(),
                allowed_tools=_split_list(front.get("allowed-tools", "")),
                required_secrets=_split_list(front.get("required-secrets", "")),
                secrets_autonomous=flag in truthy,
                enabled=enabled in truthy,
            ),
            [],
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "allowed_tools": list(self.allowed_tools),
            "required_secrets": list(self.required_secrets),
            "secrets_autonomous": self.secrets_autonomous,
            "enabled": self.enabled,
        }


@dataclass(frozen=True)
class SkillFinding:
    """One reason a bundle is not acceptable, or one thing a reader should know about it."""

    rule: str
    detail: str
    blocking: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {"rule": self.rule, "detail": self.detail, "blocking": self.blocking}


class SecurityScanner:
    """Advisory content review, failing closed.

    A skill's prose is model-facing, so this looks for instruction-shaped language rather than for
    malware. It is *not* a filter that can be trusted to make a skill safe: the boundary is that a
    skill executes nothing and authorises nothing, and a bundle that trips a rule here is refused until
    a human reads it. An exception inside the scanner is a refusal, because DeerFlow's equivalent
    treats unparseable output as a block and the failure mode of "scanner crashed, ship it" is precisely
    the one the scan exists to catch.
    """

    def scan(self, text: str) -> list[SkillFinding]:
        try:
            lowered = str(text).lower()
        except Exception:  # pragma: no cover - defensive, mirroring the stated fail-closed rule
            return [SkillFinding("scanner_unavailable", "the scanner could not read the bundle; refusing", True)]
        findings = [
            SkillFinding("instruction_shaped", f"body contains {phrase!r}; skill text is data for a model, not a directive to one")
            for phrase in _INJECTION_PATTERNS
            if phrase in lowered
        ]
        findings.extend(
            SkillFinding(
                "secret_literal",
                "body carries what looks like a literal credential; declare it under 'required-secrets' "
                "and refer to it by name, never by value",
            )
            for pattern in _SECRET_LITERALS
            if pattern.search(text or "")
        )
        return findings


@dataclass(frozen=True)
class SkillBundle:
    """One skill on disk, with the verdicts about it already computed."""

    manifest: SkillManifest
    path: Path
    text: str
    digest: str
    findings: tuple[SkillFinding, ...] = ()

    @property
    def ok(self) -> bool:
        return not any(item.blocking for item in self.findings)

    def to_dict(self) -> dict[str, Any]:
        return {
            "manifest": self.manifest.to_dict(),
            "path": str(self.path),
            "digest": self.digest,
            "findings": [item.to_dict() for item in self.findings],
            "ok": self.ok,
        }


def parse_frontmatter(text: str) -> tuple[dict[str, str], list[str]]:
    """Read the leading ``---`` block. Deliberately tiny, and deliberately not a YAML parser.

    A full YAML parser would be a dependency *and* an attack surface (aliases, tags, multi-document
    streams), and the frontmatter this format needs is flat ``key: value`` lines. A file that does not
    open with ``---`` is a refusal rather than a guess, and :meth:`SkillMaterializer.validate` performs
    the same shape check on the write side, so neither path can accept a bundle the other rejects.
    """
    lines = str(text or "").splitlines()
    if not lines or lines[0].strip() != "---":
        return {}, ["skill content must open with YAML frontmatter ('---')"]
    front: dict[str, str] = {}
    closed = False
    for line in lines[1:]:
        if line.strip() == "---":
            closed = True
            break
        key, separator, value = line.partition(":")
        if separator:
            front[key.strip()] = value.strip()
    if not closed:
        return {}, ["skill frontmatter is not closed by '---'"]
    unknown = sorted(set(front) - set(MANIFEST_KEYS))
    problems = [f"skill frontmatter names {key!r}, which this build does not read" for key in unknown]
    return front, problems


class SkillCatalog:
    """The skills in force, and what may be said about them.

    ``roots`` is ordered by precedence: the *active* version's overlay first, so a promoted bundle
    shadows an installed one, and the workspace's own directory second. A skill set is not a place for
    two authorities, and a shadowing order is auditable while a merge is not.
    """

    def __init__(
        self,
        workspace: Path | str,
        *,
        overlay_root: Path | None = None,
        scanner: SecurityScanner | None = None,
        autonomous_secrets: Sequence[str] = (),
        tool_authority: Any = None,
    ) -> None:
        self.workspace = Path(workspace).expanduser().resolve()
        self.roots: list[Path] = []
        if overlay_root is not None:
            self.roots.append(Path(overlay_root).expanduser().resolve() / SKILL_SUBPATH)
        self.roots.append(self.workspace / SKILL_SUBPATH)
        self.scanner = scanner or SecurityScanner()
        self.autonomous_secrets = frozenset(str(item) for item in autonomous_secrets)
        self._tool_authority = tool_authority

    # -- reading -------------------------------------------------------------

    def _directories(self) -> list[tuple[Path, Path]]:
        found: list[tuple[Path, Path]] = []
        seen: set[str] = set()
        for root in self.roots:
            if not root.is_dir():
                continue
            for entry in sorted(root.iterdir()):
                if not entry.is_dir() or entry.name in seen:
                    continue
                if not _NAME_SHAPE.match(entry.name):
                    found.append((root, entry))  # reported as a refusal, not skipped silently
                    continue
                if not (entry / SKILL_FILENAME).is_file():
                    found.append((root, entry))
                    continue
                seen.add(entry.name)
                found.append((root, entry))
        return found

    def load(self, directory: Path) -> SkillBundle:
        """Read one bundle and return it with its verdicts; never raises on bad content."""
        from .materialization import SkillMaterializer

        skill_file = directory / SKILL_FILENAME
        try:
            text = skill_file.read_text(encoding="utf-8")
        except OSError as exc:
            manifest = SkillManifest(name=directory.name, enabled=False)
            return SkillBundle(manifest, directory, "", "", (SkillFinding("unreadable", f"{SKILL_FILENAME} could not be read: {type(exc).__name__}: {exc}"),))
        problems = SkillMaterializer().validate({"name": directory.name, "content": text})
        front, shape_problems = parse_frontmatter(text)
        problems.extend(shape_problems)
        manifest, manifest_problems = SkillManifest.from_frontmatter(front, directory_name=directory.name)
        problems.extend(manifest_problems)
        findings = [SkillFinding("content", item) for item in problems]
        findings.extend(run_scanner(self.scanner, text))
        return SkillBundle(
            manifest or SkillManifest(name=directory.name, enabled=False),
            directory,
            text,
            hashlib.sha256(text.encode("utf-8")).hexdigest(),
            tuple(findings),
        )

    def bundles(self) -> list[SkillBundle]:
        return [self.load(directory) for _root, directory in self._directories()]

    def enabled(self) -> list[SkillBundle]:
        """The projection is enabled-only, exactly as the mount trees are.

        A skill that is installed but disabled must not appear in a prompt *or* in a child's mounts:
        DeerFlow documents that its projection materialises enabled skills only, and the reason transfers
        - a disabled bundle that is still visible to the model is a bundle that is enabled in every way
        that matters.
        """
        return [bundle for bundle in self.bundles() if bundle.ok and bundle.manifest.enabled]

    def get(self, name: str) -> SkillBundle | None:
        for bundle in self.bundles():
            if bundle.manifest.name == name:
                return bundle
        return None

    # -- consequences --------------------------------------------------------

    def mount_roots(self) -> tuple[Path, ...]:
        return tuple(bundle.path for bundle in self.enabled())

    def tool_policy(self, name: str) -> tuple[tuple[str, ...], list[str]]:
        """Canonical names this skill may use, and the reasons any of them is not available.

        Refused rather than filtered: a list that silently dropped ``teleport`` would leave the model
        told it has a tool the boundary has never heard of, and the disagreement would surface as a
        flaky agent rather than as a bad skill.
        """
        bundle = self.get(name)
        if bundle is None:
            return (), [f"no skill named {name!r} is loaded"]
        from .tools import canonical_tool_name

        # ``None`` is a real answer here: no registry to consult, so the canonical table alone decides.
        # Coercing it to an empty list would make every skill look like it wanted a nonexistent tool,
        # which is the kind of refusal that reads as a broken skill instead of a missing wiring.
        registered = getattr(self._tool_authority, "names", None)
        registry = tuple(registered) if registered is not None else None
        allowed: list[str] = []
        problems: list[str] = []
        for requested in bundle.manifest.allowed_tools:
            canonical, why = canonical_tool_name(requested, registry)
            if not canonical:
                problems.append(f"{name}: allowed-tools {requested!r} is not a tool this build offers ({why})")
                continue
            allowed.append(canonical)
        return tuple(dict.fromkeys(allowed)), problems

    def secret_names(self, name: str) -> tuple[str, ...]:
        return tuple(self.get(name).manifest.required_secrets) if self.get(name) else ()

    def secrets_may_be_autonomous(self, name: str) -> tuple[bool, str]:
        bundle = self.get(name)
        if bundle is None:
            return False, "no such skill"
        if not bundle.manifest.secrets_autonomous:
            return False, f"{name} does not declare secrets-autonomous"
        ungranted = sorted(set(bundle.manifest.required_secrets) - set(self.autonomous_secrets))
        if ungranted:
            return False, f"{name} requests secrets no operator granted for autonomous use: " + ", ".join(ungranted)
        return True, ""

    def instructions(self, name: str, *, granted_tools: Iterable[str] = ()) -> str:
        """The body, with secrets referenced by name and never by value.

        A skill's text is model-facing data. So the ``<secret:NAME>`` placeholders stay placeholders -
        the caller may put the value in a child's environment, and if a prompt carried it the transcript,
        the event log, and every later summariser would too.
        """
        bundle = self.get(name)
        if bundle is None:
            return ""
        body = bundle.text
        if body.startswith("---"):
            lines = body.splitlines()
            for index, line in enumerate(lines[1:], start=1):
                if line.strip() == "---":
                    body = "\n".join(lines[index + 1 :]).strip()
                    break
        for secret in bundle.manifest.required_secrets:
            body = body.replace(secret, f"<secret:{secret}>")
        allowed, problems = self.tool_policy(name)
        granted = set(granted_tools or ())
        if granted and allowed:
            narrowed = tuple(item for item in allowed if item in granted)
        else:
            narrowed = allowed
        header = f"# skill: {name}\n# digest: {bundle.digest[:16]}\n# tools: {', '.join(narrowed) or 'none'}\n"
        if problems:
            header += "# unavailable: " + "; ".join(problems) + "\n"
        return (header + "\n" + body).strip()

    def report(self) -> dict[str, Any]:
        bundles = self.bundles()
        return {
            "roots": [str(item) for item in self.roots],
            "installed": len(bundles),
            "enabled": [bundle.manifest.name for bundle in bundles if bundle.ok and bundle.manifest.enabled],
            "refused": [
                {"name": bundle.path.name, "findings": [item.to_dict() for item in bundle.findings if item.blocking]}
                for bundle in bundles
                if not bundle.ok
            ],
        }


class SkillInstaller:
    """Stage a bundle for review. Writes only into ``staging_root``, and never activates.

    Activation is promotion: the same path every other capability takes (07 §5, P3's spine). An
    installer that could make a skill live would be a second promotion path with fewer readers, and the
    one thing this phase must not add is a door that bypasses the version registry.
    """

    def __init__(self, staging_root: Path | str, *, scanner: SecurityScanner | None = None) -> None:
        self.staging_root = Path(staging_root).expanduser().resolve()
        self.scanner = scanner or SecurityScanner()

    def _path_findings(self, relative: Path) -> list[SkillFinding]:
        text = relative.as_posix()
        findings: list[SkillFinding] = []
        if relative.is_absolute() or text.startswith("/"):
            findings.append(SkillFinding("absolute_path", f"{text}: a bundle may not name an absolute path"))
        if ".." in relative.parts:
            findings.append(SkillFinding("traversal", f"{text}: a bundle may not climb out of its own directory"))
        if ":" in text:
            # One rule for two platforms: a colon is a Windows drive separator and the delimiter that
            # starts an NTFS alternate data stream, and on a POSIX filesystem it is just a filename that
            # will not survive the copy. Rejecting it everywhere is the only version that is not a
            # platform assumption.
            findings.append(SkillFinding("colon_in_name", f"{text}: a bundle name may not contain ':' (drive prefix or alternate data stream)"))
        if "\\" in text:
            findings.append(SkillFinding("backslash_in_name", f"{text}: a bundle name may not contain '\\\\'"))
        # Depth, not a fixed file list: a bundle is SKILL.md plus support files, and support files may sit
        # at the root or under two levels of directory. Anything deeper is either a vendored tree or a
        # bundle assembled from somewhere with different rules, and both are worth a human looking at.
        if len(relative.parts) > 3:
            findings.append(SkillFinding("layout", f"{text}: a bundle is {SKILL_FILENAME} plus support files at most two directories deep"))
        if Path(text).suffix.lower() not in ALLOWED_SUFFIXES:
            findings.append(SkillFinding("file_kind", f"{text}: only {', '.join(sorted(ALLOWED_SUFFIXES))} files are accepted; a skill runs nothing"))
        return findings

    def stage(self, source: Path | str, *, name: str = "") -> dict[str, Any]:
        """Copy one bundle into the staging area, or refuse and write nothing at all."""
        source_dir = Path(source).expanduser().resolve()
        skill_name = name or source_dir.name
        report: dict[str, Any] = {"name": skill_name, "ok": False, "refusals": [], "written": [], "digest": ""}
        if not _NAME_SHAPE.match(skill_name):
            report["refusals"].append(f"skill name {skill_name!r} must match [a-z0-9][a-z0-9._-] and be safe as a path component")
            return report
        if not source_dir.is_dir():
            report["refusals"].append(f"{source_dir} is not a directory")
            return report
        from .materialization import SkillMaterializer, MAX_FRAGMENT_BYTES

        members: list[Path] = []
        total = 0
        findings: list[SkillFinding] = []
        for path in sorted(source_dir.rglob("*")):
            if path.is_symlink():
                findings.append(SkillFinding("symlink", f"{path.name}: a bundle may not contain links; they resolve outside the staging root"))
                continue
            if not path.is_file():
                if path.is_dir():
                    continue
                findings.append(SkillFinding("special_file", f"{path.name}: not a regular file"))
                continue
            relative = path.relative_to(source_dir)
            findings.extend(self._path_findings(relative))
            if path.stat().st_mode & 0o111:
                findings.append(SkillFinding("executable", f"{relative}: an executable file is not a skill file"))
            size = path.stat().st_size
            total += size
            if size > MAX_FRAGMENT_BYTES:
                findings.append(SkillFinding("oversized_member", f"{relative}: {size} bytes exceeds {MAX_FRAGMENT_BYTES}"))
            members.append(path)
            if len(members) > MAX_BUNDLE_MEMBERS:
                findings.append(SkillFinding("member_count", f"more than {MAX_BUNDLE_MEMBERS} files in one bundle"))
                break
        if total > MAX_BUNDLE_BYTES:
            findings.append(SkillFinding("bundle_size", f"{total} bytes exceeds the {MAX_BUNDLE_BYTES} byte bundle limit"))
        blocking = [item for item in findings if item.blocking]
        skill_file = source_dir / SKILL_FILENAME
        if not skill_file.is_file():
            blocking.append(SkillFinding("missing_skill_file", f"{SKILL_FILENAME} is required"))
        else:
            text = skill_file.read_text(encoding="utf-8")
            front, shape_problems = parse_frontmatter(text)
            manifest, manifest_problems = SkillManifest.from_frontmatter(front, directory_name=skill_name)
            content_problems = SkillMaterializer().validate({"name": skill_name, "content": text})
            blocking.extend(SkillFinding("content", item) for item in [*shape_problems, *manifest_problems, *content_problems])
            blocking.extend(run_scanner(self.scanner, text))
        if blocking:
            report["refusals"] = [f"{item.rule}: {item.detail}" for item in blocking]
            return report  # nothing has been written; validation came first
        destination = self.staging_root / SKILL_SUBPATH / skill_name
        staging_parent = destination.parent
        staging_parent.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            report["refusals"].append(f"{destination} already exists; a skill is replaced by promoting a version, not by re-staging over one")
            return report
        destination.mkdir()
        digest = hashlib.sha256()
        for path in members:
            relative = path.relative_to(source_dir)
            target = destination / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            payload = path.read_bytes()
            target.write_bytes(payload)
            digest.update(relative.as_posix().encode("utf-8"))
            digest.update(payload)
            report["written"].append(relative.as_posix())
            os.chmod(target, 0o644)  # the staged copy is never executable, whatever the source said
        report["digest"] = digest.hexdigest()
        report["ok"] = True
        report["path"] = str(destination)
        return report


def catalog_from_policy(
    workspace: Path | str,
    policy: Any = None,
    *,
    overlay_root: Path | None = None,
    tool_authority: Any = None,
) -> SkillCatalog:
    """Build the catalog the way the runtime does, so two callers cannot disagree about the roots.

    The operator grant for autonomous secrets is read off the security policy because that is where
    this build keeps the settings a candidate may not touch; ``tool_authority`` is the registry or catalog
    whose ``names`` bound what a skill may ask for, and is passed separately rather than inferred from
    the policy - the policy decides *confinement*, and mixing the two is how a permission system ends up
    with two answers to one question.
    """
    autonomous = tuple(getattr(policy, "skill_autonomous_secrets", ()) or ()) if policy is not None else ()
    return SkillCatalog(workspace, overlay_root=overlay_root, autonomous_secrets=autonomous, tool_authority=tool_authority)
