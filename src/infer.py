"""Derive a factory reliability contract from a real installation.

The review rules read a declaration. A declaration is hand-written, so it can
be edited to a decided value while the installation stays exactly as it was --
findings go green and nothing is fixed. This module closes that gap by reading
the installation itself and reporting what it can actually establish.

What is derivable and what is not, stated plainly, because the split is the
whole point:

  derivable   the inventory of external effect call sites, and whether every
              one of them carries a stable effect identity. Both are properties
              of the code on disk.
  not         how a destination behaves when the same identity arrives twice,
              and what the caller does when an outcome is unknown. Those are
              facts about the destination and about a decision nobody wrote
              down. They stay "unknown" and are asked for.

An effect identity that is present at some call sites and absent at others is
reported as absent, with the count. Partial coverage is the common real state
and it is not a partial pass: a duplicate arrives through whichever site was
missed.

Call sites come in three kinds and conflating them produces a number that means
nothing. A SCRIPTED site is a line of code: a static check can bind it, and a
missing identity there is a fixable defect. An INSTRUCTED site is a sentence in
a prompt or a formula telling an agent to perform the effect: no static marker
can bind it, because there is no argument list until an agent writes one at run
time. An effect with any instructed site cannot have a derived identity at all,
and saying so is more useful than reporting the scripted fraction as if it were
coverage. A HARNESS site is a test or a checker exercising the verb; it is
subtracted from the derivation and still printed, because a harness that
performs a real effect is a harness that can perform it against the wrong
destination, and silently dropping it would hide that.

Harness paths are declared per installation in the probe pack rather than
guessed from a filename. A rule that keys on the word "test" classifies by
naming convention, and every instrument in this kit that classified by name has
inverted on the case it existed to catch.
"""

import fnmatch
import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

PROBE_VERSION = "factory.probes/v1"

# A generated line that is only a comment is not a call site. Every scanned
# language in scope uses "#", and a probe can override per language.
DEFAULT_COMMENT_PREFIX = "#"


@dataclass
class CallSite:
    path: str
    line: int
    text: str
    kind: str = "scripted"
    has_identity: bool = False
    identity_evidence: str = ""


@dataclass
class EffectEvidence:
    name: str
    destination: str
    identity_name: str
    sites: list = field(default_factory=list)

    @property
    def scripted(self):
        return [s for s in self.sites if s.kind == "scripted"]

    @property
    def instructed(self):
        return [s for s in self.sites if s.kind == "instructed"]

    @property
    def harness(self):
        return [s for s in self.sites if s.kind == "harness"]

    @property
    def missing_identity(self):
        return [s for s in self.scripted if not s.has_identity]

    def derived_identity(self):
        """The identity name, only when every SCRIPTED site carries it and no
        instructed site exists.

        Returns (value, reason). The reason is recorded whether or not the
        value is decided, so a generated contract can say why it reads as it
        does. An instructed site is reported before a missing marker, because
        it is the stronger fact: no amount of editing the scripted sites can
        make the effect statically enforceable while one remains.
        """
        if not self.sites:
            return "unknown", "no call site found; the effect may not be performed here"
        if not self.scripted and not self.instructed:
            return (
                "unknown",
                "the only %d call site(s) are harness code; nothing in the "
                "factory itself performs this effect" % len(self.harness),
            )
        if self.instructed:
            return (
                "unknown",
                "%d call site(s) are agent instructions, not code, so no static "
                "marker can bind them (%d scripted site(s) alongside)"
                % (len(self.instructed), len(self.scripted)),
            )
        if self.missing_identity:
            return (
                "unknown",
                "%d of %d scripted call sites carry no %s"
                % (len(self.missing_identity), len(self.scripted), self.identity_name),
            )
        return (
            self.identity_name,
            "all %d scripted call sites carry it" % len(self.scripted),
        )


def load_probes(path):
    doc = yaml.safe_load(Path(path).read_text())
    if not isinstance(doc, dict):
        raise ValueError("probe pack %s is not a mapping" % path)
    version = doc.get("version")
    if version != PROBE_VERSION:
        raise ValueError(
            "probe pack %s declares version %r, expected %r" % (path, version, PROBE_VERSION)
        )
    if not isinstance(doc.get("effects"), list) or not doc["effects"]:
        raise ValueError("probe pack %s declares no effects" % path)
    return doc


def _language_of(path, text):
    suffix = Path(path).suffix
    if suffix == ".py":
        return "python"
    if suffix in {".sh", ".bash"}:
        return "shell"
    first = text.split("\n", 1)[0] if text else ""
    if first.startswith("#!"):
        if "python" in first:
            return "python"
        if "sh" in first:
            return "shell"
    if suffix in {".toml", ".yaml", ".yml", ".md"}:
        return suffix.lstrip(".")
    return "unknown"


def _matches_any(rel, globs):
    """fnmatch with "**/" allowed to match zero path segments.

    Plain fnmatch has no "**": it expands "*" to match anything including a
    slash, so "hooks/**/*" requires at least one intermediate segment and
    silently skips "hooks/post". A probe that lists a directory and misses the
    files directly inside it produces a confirmed reading off a population it
    never looked at.
    """
    for glob in globs:
        if fnmatch.fnmatch(rel, glob):
            return True
        if "**/" in glob and fnmatch.fnmatch(rel, glob.replace("**/", "")):
            return True
    return False


def scan_files(root, scan):
    """Yield (relative path, text, language) for every in-scope readable file."""
    root = Path(root)
    include = scan.get("include_globs") or ["**"]
    exclude = scan.get("exclude_globs") or []
    max_bytes = int(scan.get("max_file_bytes", 2_000_000))
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.is_symlink():
            continue
        rel = str(path.relative_to(root))
        if not _matches_any(rel, include) or _matches_any(rel, exclude):
            continue
        try:
            if path.stat().st_size > max_bytes:
                continue
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            # A binary or unreadable file is not evidence of absence, but it is
            # also not a call site. Skipping is recorded by the caller's count.
            continue
        yield rel, text, _language_of(rel, text)


# Shell metacharacters that end one command and begin another. Splitting on
# them is what binds a flag to the invocation it belongs to: without it, a flag
# on the NEXT command in the same pipeline satisfies this one.
_SHELL_SEPARATORS = re.compile(r"\|\||&&|[;|&]")

COMMENT_PREFIX_BY_LANGUAGE = {
    "shell": "#",
    "python": "#",
    "toml": "#",
    "yaml": "#",
    "unknown": "#",
    # "#" opens a heading in Markdown, not a comment. Treating it as one drops
    # every instruction written as a heading, and a dropped instructed site is
    # a confirmed identity that should have been withdrawn.
    "md": None,
}


def _normalize(segment):
    """Collapse whitespace runs in a reconstructed segment.

    Joining a continuation leaves the indentation of the following line in the
    middle of the command, so "gc slack \\\n    publish" reconstructs with a
    run of spaces and a probe pattern written with single spaces misses it.
    The segment is a reconstruction, not a verbatim line, so normalizing it is
    the honest place to absorb that rather than making every probe author
    remember to write \\s+.
    """
    return re.sub(r"[ \t]+", " ", segment)


def _strip_trailing_comment(segment, prefix):
    """Remove a trailing comment so a marker inside it cannot count.

    Quoting is not tracked. A "#" inside a quoted string ends the segment
    early, which can only ever hide a marker and never invent one, so the
    error direction is toward reporting a missing identity rather than a
    present one.
    """
    if not prefix:
        return segment
    index = segment.find(prefix)
    return segment if index < 0 else segment[:index]


def logical_units(text, language):
    """Yield (start_line, segment) for each command-sized unit of the file.

    A unit is the text a flag can belong to. Getting this wrong in either
    direction produces a wrong answer: too wide and a neighbouring command's
    flag satisfies this call, too narrow and a legitimately continued
    invocation is missed.

      shell    backslash continuations are joined, then the joined line is
               split on the separators that end a command.
      python   lines are joined while brackets are unbalanced, so a call
               spanning several lines is one unit.
      other    one line, one unit.
    """
    lines = text.split("\n")
    prefix = COMMENT_PREFIX_BY_LANGUAGE.get(language, "#")

    if language in ("shell", "unknown"):
        index = 0
        while index < len(lines):
            start = index + 1
            joined = lines[index]
            while joined.rstrip().endswith("\\") and index + 1 < len(lines):
                joined = joined.rstrip()[:-1] + " " + lines[index + 1]
                index += 1
            index += 1
            if joined.lstrip().startswith(prefix or "\0"):
                continue
            for segment in _SHELL_SEPARATORS.split(_normalize(joined)):
                segment = _strip_trailing_comment(segment, prefix)
                if segment.strip():
                    yield start, segment
        return

    if language == "python":
        index = 0
        while index < len(lines):
            start = index + 1
            joined = lines[index]
            depth = _bracket_depth(joined)
            while depth > 0 and index + 1 < len(lines):
                index += 1
                joined += " " + lines[index]
                depth += _bracket_depth(lines[index])
            index += 1
            if joined.lstrip().startswith(prefix or "\0"):
                continue
            yield start, _strip_trailing_comment(_normalize(joined), prefix)
        return

    for number, line in enumerate(lines, start=1):
        if prefix and line.lstrip().startswith(prefix):
            continue
        yield number, line


def _bracket_depth(line):
    return sum(line.count(o) - line.count(c) for o, c in ("()", "[]", "{}"))


def find_sites(text, language, matchers):
    """Yield (start_line, segment) for every unit matching any matcher.

    The segment returned IS the scope a marker must appear in. Returning the
    segment rather than a line number is the whole correction: a line number
    forces the caller back to a window, and a window cannot tell this
    invocation's flags from the next one's.
    """
    for start_line, segment in logical_units(text, language):
        for matcher in matchers:
            langs = matcher.get("languages")
            if langs and language not in langs:
                continue
            if not re.search(matcher["regex"], segment):
                continue
            # An exclusion is not an optimisation. Without one, a read verb
            # sharing a prefix with a write verb ("nudge poll" under "nudge")
            # and a --help probe both land in the population as effects, and
            # the resulting count describes nothing.
            if any(re.search(r, segment) for r in (matcher.get("not_regex") or [])):
                continue
            # A required companion pattern separates an invocation from a data
            # declaration that merely names the verb.
            required = matcher.get("require_regex")
            if required and not re.search(required, segment):
                continue
            yield start_line, segment
            break


def probe_effect(effect_probe, files):
    """Collect every call site for one effect, and test each for identity."""
    identity = effect_probe.get("identity") or {}
    evidence = EffectEvidence(
        name=effect_probe["name"],
        destination=effect_probe.get("destination", "unknown"),
        identity_name=identity.get("name", "unknown"),
    )
    markers = identity.get("markers") or []
    call_site = effect_probe.get("call_site") or {}

    groups = []
    for kind in ("scripted", "instructed", "harness"):
        group = call_site.get(kind) or {}
        matchers = group.get("any_of") or []
        if matchers:
            groups.append((kind, matchers, group.get("path_globs")))
    if not groups and call_site.get("any_of"):
        groups.append(("scripted", call_site["any_of"], None))

    for kind, matchers, path_globs in groups:
        _collect(evidence, files, kind, matchers, path_globs, markers)

    harness_globs = call_site.get("harness_globs") or []
    if harness_globs:
        for site in evidence.sites:
            if site.kind == "scripted" and _matches_any(site.path, harness_globs):
                site.kind = "harness"
    return evidence


def _collect(evidence, files, kind, matchers, path_globs, markers):
    for rel, text, language in files:
        if path_globs and not _matches_any(rel, path_globs):
            continue
        for line_no, segment in find_sites(text, language, matchers):
            # The marker must be inside this invocation's own segment. A marker
            # anywhere else -- the next command in the pipeline, a trailing
            # comment, a nearby unrelated call -- is not evidence about this
            # one, and counting it is exactly how a false confirmed is minted.
            found = next((m for m in markers if m in segment), "")
            evidence.sites.append(
                CallSite(
                    path=rel,
                    line=line_no,
                    text=segment.strip()[:200],
                    kind=kind,
                    has_identity=bool(found),
                    identity_evidence=found,
                )
            )


def derive(root, probes):
    """Return (contract dict, evidence list) derived from the installation."""
    files = list(scan_files(root, probes.get("scan") or {}))
    evidence = [probe_effect(e, files) for e in probes["effects"]]

    effects = []
    for item in evidence:
        value, reason = item.derived_identity()
        effects.append(
            {
                "name": item.name,
                "destination": item.destination,
                "effect_identity": value,
                # Not derivable from call sites. How a destination behaves on a
                # repeat is a property of the destination, and what the caller
                # does with an ambiguous outcome is a decision, not code.
                "retry_contract": "unknown",
                "unknown_state_policy": "unknown",
                "_reason": reason,
            }
        )

    contract = {
        "version": "factory.reliability/v1",
        "factory": {"name": probes.get("factory_name", Path(root).name)},
        "effects": effects,
        "_scanned_files": len(files),
    }
    return contract, evidence
