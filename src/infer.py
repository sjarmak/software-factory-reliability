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
time. An effect with any instructed site gets no derived effect_identity: the
guarantee is a claim about every route, and this tool cannot establish what an
agent will type. It is NOT a claim that the agent omits the identity -- an
instruction can name the flag, and often does -- only that nothing here can
show that it does. What the scripted sites scored is reported alongside, as
code_lane_identity and instructed_call_sites, because the two questions have
different remedies. A HARNESS site is a test or a checker exercising the verb; it is
subtracted from the derivation and still printed, because a harness that
performs a real effect is a harness that can perform it against the wrong
destination, and silently dropping it would hide that.

Harness paths are declared per installation in the probe pack rather than
guessed from a filename. A rule that keys on the word "test" classifies by
naming convention, and every instrument in this kit that classified by name has
inverted on the case it existed to catch.
"""

import ast
import fnmatch
import functools
import os
import re
import subprocess
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
    # Why this match was set aside from the fix list, or "" if it was not.
    # Not a verdict that it is harmless -- see EffectEvidence.unclassified.
    set_aside: str = ""


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
    def fenced(self):
        """Call sites that invoke a declared fenced command.

        They are neither scripted nor instructed for the purposes of the
        derivation: the identity is not on the call line and never will be,
        because the whole point of a fence is that it lives one layer in. What
        binds them is the fence's own implementation, which IS measured as an
        ordinary scripted site -- so a wrapper that does not carry the marker
        withdraws the identity from every site that calls it.
        """
        return [s for s in self.sites if s.kind == "fenced"]

    @property
    def unclassified(self):
        """Matches that carry no identity marker and are not readable as calls.

        These are set aside from the fix list and from nothing else. The two
        shapes are not distinguishable mechanically, and both are common:

          PUSH_CMD="git push origin main"      a real command, built to run later
          fail "nested git push commands ..."  prose that names the command
          ME=git-push-fenced                   a wrapper naming itself

        What separates them here is only that the second cannot be fixed --
        there is no invocation to add a flag to. Reporting them alongside the
        real unkeyed sites sent a reader to edit a sed replacement string; the
        first version of this scanner reported six such matches on the city it
        was written against, four of which were error text and glob patterns.

        Setting them aside is therefore a reporting change and MUST NOT be a
        scoring one. The identity stays undecided while any remain, and the way
        to decide it is `not_regex` in the probe pack, which the reason names.
        A rule that silently dropped these would be a guard that can only ever
        improve a score: it would hide exactly the dangerous case, a deferred
        command assembled with no identity marker at all.
        """
        return [
            s
            for s in self.scripted + self.fenced
            if s.set_aside and not s.has_identity
        ]

    @property
    def missing_identity(self):
        """Sites that should carry the declared marker and do not.

        Empty when the pack declares no identity, because nothing can lack a
        marker that was never named. derived_identity() already returns "there
        is nothing to look for" for that case; this guard is the same fact one
        layer down, and it was missing -- a scaffolded pack printed that clean
        sentence and then a `no unknown: <path>` line for every call site it
        had just found. The sites are still reported as sites; only the claim
        that they are FAILING is withdrawn.
        """
        if self.identity_name in (None, "", "unknown"):
            return []
        return [
            s
            for s in self.scripted + self.fenced
            if not s.has_identity and not s.set_aside
        ]

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
        if self.identity_name in (None, "", "unknown"):
            # A scaffolded pack leaves every identity undecided on purpose, and
            # the branches below interpolate the identity NAME into their
            # reason. With the name literally "unknown" they produced "1 of 1
            # scripted call sites carry no unknown", which reads as a broken
            # tool rather than as the question it is.
            return (
                "unknown",
                "no identity is declared for this effect in the probe pack, so "
                "there is nothing to look for at its %d call site(s)"
                % len(self.sites),
            )
        if not self.scripted and not self.instructed and not self.fenced:
            return (
                "unknown",
                "the only %d call site(s) are harness code; nothing in the "
                "factory itself performs this effect" % len(self.harness),
            )
        if self.instructed:
            # Both facts go in the reason, because they ask for different work.
            # The instructed site is why the identity is withdrawn and cannot be
            # patched away; how the scripted sites scored is the part someone
            # can fix this afternoon. Reporting only the first taught readers
            # there was nothing to do.
            bindable = self.scripted + self.fenced
            if not bindable:
                alongside = "no scripted site(s) alongside"
            elif self.missing_identity:
                alongside = "%d of %d scripted site(s) carry no %s" % (
                    len(self.missing_identity),
                    len(bindable),
                    self.identity_name,
                )
            else:
                alongside = "%d scripted site(s) carry it" % len(bindable)
            alongside += self._quoted_note()
            return (
                "unknown",
                "%d call site(s) are agent instructions, not code, so no static "
                "marker can bind them; %s"
                % (len(self.instructed), alongside),
            )
        bindable = self.scripted + self.fenced
        if self.missing_identity:
            return (
                "unknown",
                "%d of %d scripted call sites carry no %s%s%s"
                % (len(self.missing_identity), len(bindable), self.identity_name,
                   self._through_a_fence(), self._quoted_note()),
            )
        if self.unclassified:
            return (
                "unknown",
                "every readable scripted call site carries it%s%s"
                % (self._through_a_fence(), self._quoted_note()),
            )
        return (
            self.identity_name,
            "all %d scripted call sites carry it%s"
            % (len(bindable), self._through_a_fence()),
        )

    def code_lane_identity(self):
        """Whether the CODE that performs this effect carries the identity.

        derived_identity() answers a stricter question -- can this effect be
        statically GUARANTEED -- and correctly answers "unknown" the moment one
        agent-instruction site exists, because no edit to the scripted sites can
        bind a sentence in a prompt.

        That strictness has a cost the reconciler was paying in full. Every
        effect in an agent-driven factory has instructed sites; on the city this
        kit was written against, all five declared effects do. So the strict
        answer was "unknown" for all five, every declared identity read as
        DRIFT, and `confirmed` was not merely empty but unreachable -- the same
        uniform answer across inputs that must differ, which is the shape of a
        broken instrument rather than a bad installation.

        This separates the two questions. Returns (value, reason, residual),
        where residual is the number of instructed sites that remain unbindable
        whatever the code does. A caller that hides the residual is back to
        claiming a guarantee nobody has.
        """
        residual = len(self.instructed)
        bindable = self.scripted + self.fenced
        if self.identity_name in (None, "", "unknown"):
            return "unknown", "no identity is declared for this effect", residual
        if not bindable:
            if residual:
                return (
                    "unknown",
                    "no code performs this effect; all %d call site(s) are agent "
                    "instructions" % residual,
                    residual,
                )
            if self.harness:
                return (
                    "unknown",
                    "the only %d call site(s) are harness code" % len(self.harness),
                    residual,
                )
            return "unknown", "no call site found", residual
        if self.missing_identity:
            return (
                "unknown",
                "%d of %d code call site(s) carry no %s%s%s"
                % (len(self.missing_identity), len(bindable), self.identity_name,
                   self._through_a_fence(), self._quoted_note()),
                residual,
            )
        if self.unclassified:
            return (
                "unknown",
                "every readable code call site carries it%s%s"
                % (self._through_a_fence(), self._quoted_note()),
                residual,
            )
        return (
            self.identity_name,
            "all %d code call site(s) carry it%s"
            % (len(bindable), self._through_a_fence()),
            residual,
        )

    def _quoted_note(self):
        """Name the set-aside matches, and how to settle them.

        Without the second half this is a dead end: an effect whose only
        unkeyed matches are quoted can never be decided, and the reader is not
        told there is a lever. `not_regex` in the probe pack is the lever, and
        using it moves the score -- which is the property that makes the fix
        worth doing rather than invisible.
        """
        if not self.unclassified:
            return ""
        return (
            "; %d match(es) set aside for review -- exclude the ones "
            "that are not invocations with not_regex in the probe pack"
            % len(self.unclassified)
        )

    def _through_a_fence(self):
        """Name the fenced share, so a reader can tell WHERE the identity is.

        A count that folds fenced sites into the scripted total reads as though
        every line carried the marker itself, and sends the next person looking
        for a flag that is deliberately not there.
        """
        if not self.fenced:
            return ""
        return " (%d of them through a fenced command)" % len(self.fenced)


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


_PRUNABLE_DIR = re.compile(r"^\*\*/([^/*]+)/\*\*$")


def _pruned_dir_names(exclude):
    """Directory names an exclude glob rules out wholesale.

    Excluding a path only after walking into it means a scan of a repository
    with a large store underneath it reads the whole store before discarding
    it. On this installation that took longer than the command was willing to
    wait, which for an adopter is indistinguishable from a hang.
    """
    names = {".git"}
    for glob in exclude:
        match = _PRUNABLE_DIR.match(glob)
        if match:
            names.add(match.group(1))
    return names


def vcs_ignored_prefixes(root):
    """Paths the installation's own VCS declares to be generated output.

    Returns (prefixes, unavailable_reason). A factory's .gitignore is a
    declaration BY THE INSTALLATION about which of its paths are output rather
    than source, which makes it the one place this can be derived instead of
    hand-written. Measured on the city this kit was written against: the
    scaffold found 401 git_push call sites in 158 files, and the three noisiest
    directories (.gc, graphify-out, a stray worktree) are all ignored -- logs
    and reports in which the command was RECORDED, not run.

    The reason is returned rather than folded into an empty set. An empty set
    means "nothing is ignored"; a missing git means "this was never checked",
    and a scan that reports the first when the second is true is a scan whose
    scope claim is wrong. The caller prints it.
    """
    try:
        out = subprocess.run(
            ["git", "-C", str(root), "ls-files", "-o", "-i",
             "--exclude-standard", "--directory"],
            capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.SubprocessError) as exc:
        return set(), "git could not run (%s)" % exc
    if out.returncode != 0:
        return set(), "not a git repository, or git refused: %s" % (
            (out.stderr or "").strip()[:100] or "exit %d" % out.returncode)
    prefixes = set()
    for line in out.stdout.splitlines():
        line = line.strip().rstrip("/")
        if line:
            prefixes.add(line)
    return prefixes, None


def scan_files(root, scan, notes=None):
    """Yield (relative path, text, language) for every in-scope readable file.

    `notes` collects anything the scan was ASKED to do and could not. A scan
    that quietly proceeds after failing to apply a filter reports a scope it
    never had, which is the failure this whole tool is about.
    """
    root = Path(root)
    include = scan.get("include_globs") or ["**"]
    exclude = scan.get("exclude_globs") or []
    max_bytes = int(scan.get("max_file_bytes", 2_000_000))
    pruned = _pruned_dir_names(exclude)
    prune_nested = bool(scan.get("prune_nested_repos"))
    ignored = set()
    if scan.get("respect_vcs_ignore"):
        ignored, unavailable = vcs_ignored_prefixes(root)
        if unavailable is not None and notes is not None:
            notes.append(
                "the pack asked to skip VCS-ignored paths and that could not be "
                "checked (%s); generated output was scanned as if it were source"
                % unavailable)
    for directory, subdirs, filenames in os.walk(root, followlinks=False):
        subdirs[:] = sorted(d for d in subdirs if d not in pruned)
        if ignored:
            here = Path(directory).relative_to(root)
            subdirs[:] = [
                d for d in subdirs
                if str(here / d if str(here) != "." else Path(d)) not in ignored
            ]
        if prune_nested:
            # A subdirectory that is itself a repository holds another
            # project's code. Counting its call sites as this installation's
            # is not a small overcount: on the installation this was written
            # against, nested checkouts and worktrees turned 40-odd real push
            # sites into 1045, which is a number nobody can act on.
            subdirs[:] = [d for d in subdirs
                          if not (Path(directory) / d / ".git").exists()]
        for filename in sorted(filenames):
            path = Path(directory) / filename
            if not path.is_file() or path.is_symlink():
                continue
            rel = str(path.relative_to(root))
            if rel in ignored:
                continue
            if not _matches_any(rel, include) or _matches_any(rel, exclude):
                continue
            try:
                if path.stat().st_size > max_bytes:
                    continue
                text = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                # A binary or unreadable file is not evidence of absence, but
                # it is also not a call site. Skipping is recorded by the
                # caller's count.
                continue
            yield rel, text, _language_of(rel, text)


# Shell metacharacters that end one command and begin another. Splitting on
# them is what binds a flag to the invocation it belongs to: without it, a flag
# on the NEXT command in the same pipeline satisfies this one.
_SHELL_SEPARATORS = re.compile(r"\|\||&&|[;|&]")


def split_shell_segments(line):
    """Split a shell line on command separators OUTSIDE quoted strings.

    A quote-blind split cuts `--command 'cd /tmp && git push origin main'` in
    half, and both halves are wrong in ways that matter. The tail becomes a
    segment beginning mid-string, so the quoted-mention test cannot see the
    opening quote and reports prose as an invocation. Worse in the other
    direction: a segment that starts inside a quote can pick up a flag that
    belongs to the enclosing command, and a marker collected that way mints a
    call site that reads as keyed when nothing keys it.

    Unlike _strip_trailing_comment above, this is not left quote-blind, because
    its error is not one-directional.

    Backslash escapes are honoured; command substitution and heredoc bodies are
    not tracked. An unbalanced quote leaves the rest of the line in one segment,
    which is the same direction as a continuation: wider, and therefore visible
    as a too-permissive marker rather than a silent miss.
    """
    out, current, quote, index = [], [], None, 0
    while index < len(line):
        char = line[index]
        if quote is None:
            if char == "\\" and index + 1 < len(line):
                current.append(char)
                current.append(line[index + 1])
                index += 2
                continue
            if char in "'\"":
                quote = char
                current.append(char)
                index += 1
                continue
            found = _SHELL_SEPARATORS.match(line, index)
            if found:
                out.append("".join(current))
                current = []
                index = found.end()
                continue
        else:
            if char == "\\" and quote == '"' and index + 1 < len(line):
                current.append(char)
                current.append(line[index + 1])
                index += 2
                continue
            if char == quote:
                quote = None
        current.append(char)
        index += 1
    out.append("".join(current))
    return out

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
            for segment in split_shell_segments(_normalize(joined)):
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


# Mutating methods that add arguments to a command already built. `insert` is
# here because a flag placed before a positional is still on the command.
_ARGV_MUTATORS = ("extend", "append", "insert", "__iadd__")


def _scope_statements(node):
    """Every statement in this scope, not descending into a nested function.

    Scope is half the binding below. A name assigned in one function and
    extended in another names two different lists as far as this call is
    concerned, and treating them as one is how a flag from unrelated code
    confirms an identity that is not there.
    """
    for child in ast.iter_child_nodes(node):
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        yield child
        yield from _scope_statements(child)


def _mutated_name(statement):
    """The variable a statement adds argv to, or None."""
    if isinstance(statement, ast.AugAssign) and isinstance(statement.op, ast.Add):
        if isinstance(statement.target, ast.Name):
            return statement.target.id
        return None
    call = statement.value if isinstance(statement, ast.Expr) else None
    if not isinstance(call, ast.Call):
        return None
    func = call.func
    if not isinstance(func, ast.Attribute) or func.attr not in _ARGV_MUTATORS:
        return None
    return func.value.id if isinstance(func.value, ast.Name) else None


@functools.lru_cache(maxsize=256)
def python_command_assemblies(text):
    """Map an assignment's line to the argv appended to it afterwards.

    A command written as one bracket-balanced statement is one logical unit,
    which is the rule `logical_units` implements and the right rule in general:
    a line window cannot tell this invocation's flags from the next one's. It
    is wrong for a command that is BUILT UP -- a list literal, then two
    `extend` calls -- and that shape is ordinary Python, not a workaround.

    Measured on a real installation: two Slack call sites were reported as
    carrying no idempotency key while both pass one, appended two statements
    after the literal.

    This widens the search by NAME AND SCOPE, never by distance. The later
    statement must mutate the same variable in the same scope, and anything
    after that name is reassigned belongs to a different command. That
    precision is the price of admission, because this is the one direction the
    checker is otherwise not allowed to err in: it can only turn a withdrawn
    identity into a confirmed one, and a false confirmed is the failure this
    whole file is arranged to prevent.

    Returns {assignment_line: appended_source}. A file that does not parse
    yields nothing, which leaves the caller with the unassembled reading.
    """
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError):
        return {}

    assemblies = {}
    scopes = [tree] + [
        node for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    ]
    for scope in scopes:
        statements = sorted(_scope_statements(scope), key=lambda n: getattr(n, "lineno", 0))
        open_assignment = {}
        for statement in statements:
            if isinstance(statement, ast.Assign):
                for target in statement.targets:
                    if isinstance(target, ast.Name):
                        # A reassignment ends the previous command's assembly.
                        open_assignment[target.id] = statement.lineno
                        assemblies.setdefault(statement.lineno, [])
                continue
            name = _mutated_name(statement)
            if name is None or name not in open_assignment:
                continue
            segment = ast.get_source_segment(text, statement)
            if segment:
                assemblies[open_assignment[name]].append(segment)

    return {line: " ".join(parts) for line, parts in assemblies.items() if parts}


_SHELL_VAR_REF = re.compile(r"\$\{?([A-Za-z_][A-Za-z0-9_]*)\}?")
_SHELL_ASSIGN = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)=(.*)$")


def shell_variable_assemblies(text):
    """Map each shell variable name to every value assigned to it in the file.

    The shell analogue of python_command_assemblies, and it exists for the same
    reason: a command can carry its flag in a variable rather than on the call
    line. The shape is not exotic -- it is how a fence computes the very value
    that makes it a fence:

        LEASE="--force-with-lease=$REF:$OBSERVED"
        git push "$LEASE" "$REMOTE" "$INTENDED:$REF"

    A marker search over the second line alone reports that push as unfenced,
    which is the instrument calling the strictest site in the installation the
    offender.

    Bound by variable NAME, never by proximity. File-scoped rather than
    function-scoped, because an unadorned shell assignment has no lexical
    scope and a narrower rule would be a fiction about the language. That
    looseness can credit a value assigned on a branch the call never takes,
    which is the direction that mints a false confirmed -- so the caller
    applies it only to variables the invocation actually REFERENCES, never to
    the file at large.
    """
    assemblies = {}
    for _, segment in logical_units(text, "shell"):
        match = _SHELL_ASSIGN.match(segment)
        if not match:
            continue
        name, value = match.group(1), match.group(2)
        assemblies.setdefault(name, []).append(value)
    return {name: " ".join(values) for name, values in assemblies.items()}


def resolve_shell_markers(segment, assemblies, markers):
    """Return the first marker reachable through a variable this command uses."""
    referenced = " ".join(
        assemblies.get(name, "") for name in _SHELL_VAR_REF.findall(segment)
    )
    if not referenced:
        return ""
    return next((m for m in markers if m in referenced), "")


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

    fences = identity.get("fenced_by") or []
    # A fence declaration has to widen DETECTION, not only reclassify what the
    # bare-verb pattern happened to catch. Measured on this city: the scripted
    # pattern `\bgit\b[^|;&]*\bpush\b` matches `git-push-fenced` by accident
    # of the hyphen, while the instructed pattern `\bgit push\b` (literal
    # space) does not -- so the five formula sites that adopted the fence did
    # not join the unkeyed count, they DISAPPEARED from the effect. Reporting
    # fewer call sites because somebody fixed them is the same inversion as
    # reporting them unkeyed, one step further along: the count that gets
    # reviewed is now wrong about how much of the factory performs the effect.
    fence_matchers = [
        {"regex": fence["command"]} for fence in fences if fence.get("command")
    ]

    groups = []
    for kind in ("scripted", "instructed", "harness"):
        group = call_site.get(kind) or {}
        matchers = group.get("any_of") or []
        if matchers:
            groups.append((kind, matchers + fence_matchers, group.get("path_globs")))
    if not groups and call_site.get("any_of"):
        groups.append(("scripted", call_site["any_of"] + fence_matchers, None))

    for kind, matchers, path_globs in groups:
        _collect(evidence, files, kind, matchers, path_globs, markers)

    evidence.sites = _resolve_overlaps(evidence.sites)

    harness_globs = call_site.get("harness_globs") or []
    if harness_globs:
        for site in evidence.sites:
            if site.kind == "scripted" and _matches_any(site.path, harness_globs):
                site.kind = "harness"

    _apply_fences(evidence, fences)
    return evidence


def _apply_fences(evidence, fences):
    """Credit call sites that route the effect through a declared fence.

    Without this, adopting a fence makes an installation score WORSE. The
    wrapper name matches the bare-verb pattern (`git-push-fenced` contains both
    `git` and `push`), the marker is inside the wrapper rather than on the call
    line, and so every site that did the right thing joins the unkeyed count.
    An instrument that cannot see a fix is worse than no instrument: it tells
    the person who fixed it that nothing happened.

    What is NOT done here is take the fence's word for it. A fence entry names
    the command AND the file that implements it, and the implementation stays
    an ordinary scripted call site that must carry the marker itself. So the
    credit is transitive from one measured place rather than declared:

      wrapper carries the marker  -> every call site of it carries the identity
      wrapper does not           -> every call site of it loses the identity,
                                    and the reason names the wrapper

    That is the real reliability argument for a fence stated as a measurement:
    N call sites collapse to one place worth checking, and the tool checks it.
    """
    for fence in fences:
        command = fence.get("command")
        if not command:
            continue
        implementation = fence.get("implementation") or []
        implementation_sites = [
            s for s in evidence.sites if _matches_any(s.path, implementation)
        ]
        # An empty implementation set is a probe-pack error, not a pass. A
        # fence nobody can point at is a claim, and claims are what the
        # derivation exists to replace -- so it credits nothing.
        #
        # EVERY readable call in the wrapper must pin it. That is the rule a
        # fence's whole argument rests on: N call sites collapse to ONE place
        # worth checking, which is only true if the one place has no unfenced
        # path through it. A wrapper with one leased push and one bare push
        # credits nothing, and the evidence line says which.
        #
        # This was ANY for one commit, and the reason is worth keeping. Before
        # mentions were detected, a wrapper's own name literal (ME=git-push-
        # fenced) and a name inside a sed program counted as unkeyed calls, so
        # ALL made `carried` false for both fences on the city this was written
        # against -- every site that adopted a fence read "does not carry it",
        # and the instrument inverted on the very fix it exists to see. The
        # rules agree now that the wrapper resolves to exactly one readable
        # call, which is what the ANY comment predicted would happen.
        readable = [s for s in implementation_sites if not s.set_aside]
        carrying = [s for s in readable if s.has_identity]
        carried = bool(readable) and len(carrying) == len(readable)
        implementation_paths = {s.path for s in implementation_sites}
        for site in evidence.sites:
            if site.path in implementation_paths:
                continue
            if site.kind == "harness":
                continue
            # A bare assignment names the fence and passes it nothing, so there
            # is no invocation here to credit. A quoted deferred command is a
            # different case and keeps its credit: PUSH_CMD="git-push-fenced
            # --remote origin --branch $B" is the fence being used, arguments
            # and all, on a line that happens to run later.
            if site.set_aside == ASSIGNED_LITERAL:
                continue
            if not re.search(command, site.text):
                continue
            site.kind = "fenced"
            site.has_identity = carried
            label = fence.get("name", command)
            site.identity_evidence = (
                "carried by %s (%d of %d readable site(s) in its "
                "implementation pin it)"
                % (label, len(carrying), len(readable))
                if carried
                else "%s does not carry it" % label
            )


# Which classification wins when a file matches more than one group's
# path_globs. Kept explicit because the alternative is whichever group the
# loop reached last, which is a property of dictionary order rather than of
# the installation.
_KIND_PRECEDENCE = {"harness": 0, "fenced": 1, "instructed": 2, "scripted": 3}


def _resolve_overlaps(sites):
    """Keep one site per invocation.

    Two independent causes, both measured on a real installation:

      Within one group, "any_of" means any of these patterns matched, so an
      invocation matching two of them was appended twice. Three of this
      city's nudge sites were counted double for exactly that reason -- a
      single `gc session nudge mayor "$msg"` line matching two patterns.

      Across groups, collection runs per group, so a directory listed in both
      the scripted and the instructed globs -- the normal case for a tree
      holding code and documents side by side -- produced two sites for one
      invocation.

    Either way the population every ratio is computed over is inflated by the
    overlap, and "20 of 23 sites carry the key" stops being a fact about the
    installation.

    Instructed beats scripted on a tie because it is the stronger claim: an
    invocation something reads rather than runs cannot be bound by a static
    marker, and calling it scripted asserts that it can.
    """
    best = {}
    for site in sites:
        key = (site.path, site.line)
        current = best.get(key)
        if current is None or (_KIND_PRECEDENCE[site.kind]
                               < _KIND_PRECEDENCE[current.kind]):
            best[key] = site
    return [best[key] for key in sorted(best)]


# A whole segment that is one bare shell assignment: NAME=value, nothing else
# on the line. A wrapper naming itself (`ME=git-push-fenced`) has this shape,
# and so does a deferred command with no arguments -- which is why matching it
# sets the site aside for review rather than dropping it.
_BARE_ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=[^\s]*$")
ASSIGNED_LITERAL = "assigned as a bare literal; nothing is invoked on this line"
INSIDE_A_STRING = "inside a quoted string"


def set_aside_reason(segment, matchers, language):
    """Why this match cannot be read as an invocation, or "" if it can be.

    Shell-style quoting only, and deliberately so: the languages in scope here
    (shell, TOML formula bodies, prompt markdown holding shell) all quote the
    same two ways, and Python's triple-quoted and f-string forms would need a
    real parser to get right. An unsupported language sets nothing aside, which
    is the conservative direction: a site wrongly kept is reviewed, a site
    wrongly set aside is not.

    YAML stays out on purpose rather than by omission. A command inside a YAML
    scalar -- `run: "git push origin main"` -- is quoted by YAML and run by the
    shell, so a shell-style walk would read the real invocation as a mention
    and set aside exactly the unfenced push this looks for. Wrong in the
    dangerous direction, which is the one direction this must never be.

    The name below is "md" because that is what _language_of emits. It read
    "markdown" for as long as this function existed, so the markdown case named
    in the paragraph above was excluded by a string that could never match.
    """
    if language not in ("shell", "unknown", "toml", "md"):
        return ""
    if _BARE_ASSIGNMENT.match(segment.strip()):
        return ASSIGNED_LITERAL
    pos = None
    for matcher in matchers:
        langs = matcher.get("languages")
        if langs and language not in langs:
            continue
        found = re.search(matcher["regex"], segment)
        if found:
            pos = found.start()
            break
    if pos is None:
        return ""
    quote, i = None, 0
    while i < pos and i < len(segment):
        char = segment[i]
        if quote is None:
            if char in "\'\"":
                quote = char
            elif char == "\\":
                i += 1
        elif char == quote:
            quote = None
        elif quote == '"' and char == "\\":
            i += 1
        i += 1
    return INSIDE_A_STRING if quote is not None else ""


def _collect(evidence, files, kind, matchers, path_globs, markers):
    for rel, text, language in files:
        if path_globs and not _matches_any(rel, path_globs):
            continue
        shell_vars = None
        for line_no, segment in find_sites(text, language, matchers):
            # The marker must be inside this invocation's own segment. A marker
            # anywhere else -- the next command in the pipeline, a trailing
            # comment, a nearby unrelated call -- is not evidence about this
            # one, and counting it is exactly how a false confirmed is minted.
            found = next((m for m in markers if m in segment), "")
            evidence_text = found
            if not found and language in ("shell", "unknown"):
                if shell_vars is None:
                    shell_vars = shell_variable_assemblies(text)
                found = resolve_shell_markers(segment, shell_vars, markers)
                if found:
                    evidence_text = found + " (assigned to a variable this command uses)"
            if not found and language == "python":
                # The same command, assembled over several statements. Bound by
                # variable and scope, never by proximity.
                assembled = python_command_assemblies(text).get(line_no, "")
                found = next((m for m in markers if m in assembled), "")
                if found:
                    evidence_text = found + " (appended to the same command)"
            evidence.sites.append(
                CallSite(
                    path=rel,
                    line=line_no,
                    text=segment.strip()[:200],
                    kind=kind,
                    has_identity=bool(found),
                    identity_evidence=evidence_text,
                    set_aside=set_aside_reason(segment, matchers, language),
                )
            )


def derive(root, probes, notes=None):
    """Return (contract dict, evidence list) derived from the installation."""
    files = list(scan_files(root, probes.get("scan") or {}, notes))
    evidence = [probe_effect(e, files) for e in probes["effects"]]

    effects = []
    for item in evidence:
        # effect_identity is the STRICT answer, and it stays strict. The field
        # means "this value is carried unchanged across retries" -- a guarantee
        # about every route that performs the effect, not a coverage figure for
        # the ones we can read. code_lane_identity() answers a narrower and very
        # useful question, and an earlier version of this loop wrote ITS answer
        # here. That traded an under-claim for an over-claim: an effect with one
        # scripted site carrying the key and one prompt telling an agent to run
        # the command came out with a decided identity, and every downstream
        # consumer -- the renderer's Effect identity column, the schema, a
        # machine reading the contract -- was told the retry was safe.
        #
        # The two lanes are reported side by side instead. The disagreement that
        # motivated the earlier change was real (reconcile printed CONFIRMED
        # while infer wrote `unknown` for the same effect), and the fix for it
        # is to SHOW both answers, not to promote the weaker one into the field
        # that carries the guarantee.
        value, reason = item.derived_identity()
        code_value, code_reason, residual = item.code_lane_identity()
        effect = {
            "name": item.name,
            "destination": item.destination,
            "effect_identity": value,
            # Not derivable from call sites. How a destination behaves on a
            # repeat is a property of the destination, and what the caller
            # does with an ambiguous outcome is a decision, not code.
            "retry_contract": "unknown",
            "unknown_state_policy": "unknown",
            "_reason": reason,
            "_code_lane_reason": code_reason,
        }
        # Two OBSERVATIONS, never guarantees, and the schema descriptions say
        # so. Together they say what the strict "unknown" above is hiding:
        # whether the code that performs this effect carries the identity, and
        # how many routes are prose that no marker can bind.
        #
        # Both are written whenever they were measured, including zero. An
        # absent field means nobody looked; a zero means somebody looked and
        # found none, and a reviewer has to be able to tell those apart. Writing
        # them ONLY when interesting would reintroduce, one field over, exactly
        # the ambiguity this pair exists to remove.
        effect["code_lane_identity"] = code_value
        effect["instructed_call_sites"] = residual
        effects.append(effect)

    contract = {
        "version": "factory.reliability/v1",
        "factory": {"name": probes.get("factory_name", Path(root).name)},
        "effects": effects,
        "_scanned_files": len(files),
    }
    return contract, evidence
