"""Resolve the path:line references a factory contract makes about code.

A factory contract is mostly prose ABOUT code, pinned by `path/to/file.go:214`.
Those pins rot on the same timescale as the code, and nothing else in this kit
looks at them: `reconcile` compares declared EFFECTS against a scan of call
sites, and a cite lives in a comment where it cannot see it.

WHAT THIS CANNOT DO, stated here rather than implied by the output. It cannot
verify that a cited line still SAYS the thing the contract claims. A line-
pinned claim invalidates its own refutation the moment anything above it moves,
so a check that reported "line 214 no longer matches" would be wrong more often
than the cite is. What is checkable without false positives is narrower and
still worth having: the file exists, and the line is inside it.

That narrowness is the design, not a shortcut. A check that claimed to verify
cite content and could not would be worse than no check, because a green run
would read as "the contract's claims about the code were confirmed."
"""

import re
from pathlib import Path

# A cite is a path-ish token, a colon, a line, optionally a range.
#
# This started with a `(?<![\w:/-])` lookbehind meant to reject URLs and YAML
# keys. It was measured and removed: none of the three cases it was written for
# reach it (`example.com:8080` dies on the suffix list, `timeout: 30` has no
# dot, `v1.2.3` has no letter after the dot), and on the case it DID reach it
# produced a mangled path -- `github.com/org/repo/.../a.go:12` came back as
# `com/org/repo/.../a.go:12` -- while silently dropping a legitimate absolute
# path cite. A guard that survives its own removal is not a guard, and this one
# was actively wrong on the two inputs it touched.
#
# What replaced it: a leading `/` is now part of the path (an absolute cite is
# a real thing people write), and URLs are suppressed by looking at the text
# before the match rather than by a character class. Known and accepted false
# positive: a Go module path like `module@v1.4.0/pkg/thing.go:31` matches from
# `v1.4.0` and reports MISSING rather than being skipped.
_CITE = re.compile(r"""
    # A leading dot is allowed so a dot-directory cite keeps its name:
    # without it `.beads/formulas/x.toml:118` matched from `beads/` and
    # was reported MISSING under a path the contract never wrote.
    (?P<path>/?\.?[A-Za-z0-9_][A-Za-z0-9_./-]*\.[A-Za-z][A-Za-z0-9]{0,7})
    :(?P<start>\d+)
    (?:-(?P<end>\d+))?
    # Not `(?![\w.])`, which was the first version and rejected every cite
    # that ENDS A SENTENCE: `handler.go:99.` failed the lookahead on its
    # own full stop and was reported as no cite at all. What the guard is
    # actually for is a dotted version like `v1.2.3`, so it rejects a
    # following dot only when a digit comes after it.
    (?!\w)(?!\.\d)
""", re.VERBOSE)

# URLs are blanked out before the scan rather than filtered after it. The
# filter version was written first and was wrong in a way worth recording: a
# leading-`/` path can begin INSIDE the scheme, so the text before the match
# was `https:/` and no "does this look like a URL" test on it could fire. The
# offsets are preserved so line numbers stay honest; only column positions,
# which are not reported, move.
# Case-insensitive: an uppercase scheme is rare in prose and produced a
# FABRICATED path rather than a missed one -- `HTTPS://example.com/path/
# internal.go:77` yielded the cite `/example.com/path/internal.go:77`,
# a path no contract ever wrote. Found by cross-family review.
_URL = re.compile(r"\b[a-z][a-z0-9+.-]*://\S*", re.IGNORECASE)


def _blank_urls(line):
    return _URL.sub(lambda m: " " * len(m.group(0)), line)

# Extensions that name source files. A cite to `factory.yaml:12` inside a
# contract is usually the contract talking about itself and resolves fine; the
# list exists to keep version strings (`v1.2.3`) and hostnames out.
_SOURCE_SUFFIXES = {
    ".go", ".py", ".rs", ".ts", ".tsx", ".js", ".jsx", ".java", ".kt", ".rb",
    ".sh", ".bash", ".c", ".h", ".cc", ".cpp", ".hpp", ".cs", ".php", ".pl",
    ".swift", ".scala", ".yaml", ".yml", ".toml", ".json", ".md", ".sql",
    ".proto", ".tf", ".mjs", ".cjs",
}


class Cite:
    """One path:line reference, with where it was written and how it resolved."""

    def __init__(self, path, start, end, line_number, raw):
        self.path = path
        self.start = start
        self.end = end          # None for a single-line cite
        self.line_number = line_number
        self.raw = raw
        self.status = None      # resolved | resolved_via_contract
                                # | missing | out_of_range | ambiguous
        self.via_contract = False
        self.resolved = None    # the file it resolved to, when it did
        self.detail = ""

    @property
    def last(self):
        return self.end if self.end is not None else self.start

    def __repr__(self):
        return f"<Cite {self.raw} {self.status}>"


# A comment line that ends mid-path, because the author wrapped it at the
# column limit:
#
#     # ... the ship gate re-resolves the branch head (assets/scripts/
#     # gascity-ship-gate.sh:198-210). That is a correct check.
#
# Measured on our own contract, this accounts for 2 of the 4 cites the first
# version reported broken -- half its findings were its own extraction bug,
# which is the rate at which a checker stops being run. Narrow on purpose: the
# line must be a COMMENT and must end on a bare `/`, so a YAML value that
# happens to end in a slash is untouched.
_COMMENT = re.compile(r"^\s*#")
# What the NEXT line must start with for each way a cite can be cut in half.
# Deliberately specific: a comment line ending in a period is an ordinary
# sentence far more often than it is a path cut before its extension, so the
# period case is allowed only when what follows looks like a bare extension
# and a line number.
_AFTER_DOT = re.compile(r"^[A-Za-z][A-Za-z0-9]{0,7}:\d")
_AFTER_COLON = re.compile(r"^\d")
_AFTER_HYPHEN = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.-]*\.[A-Za-z][A-Za-z0-9]{0,7}:\d")


def _continues(token, follow):
    """Is this comment line cut mid-cite, with `follow` the rest of it?

    Measured on our own contract, which wraps at four different characters in
    one paragraph. Getting this wrong in the permissive direction produces a
    visible bad cite; getting it wrong in the strict direction produces
    SILENCE, and a cite the tool never saw is indistinguishable from one it
    checked. That asymmetry is why the slash and hyphen cases need no
    corroboration from the following line and the period case does.
    """
    if not token:
        return False
    if token.endswith("/"):
        return True
        # KNOWN and not fixed, with the input that shows it: prose ending in a
        # directory stitches into a citation nobody wrote --
        # `# keep files under /tmp/` followed by `# cache.go:42` yields
        # `/tmp/cache.go:42`. Corroborating from the following line does not
        # separate the cases (`cache.go:42` is exactly what a real wrap looks
        # like), and requiring an inner slash in the token would drop the real
        # `# see internal/` form. The result is a VISIBLE false MISSING on the
        # line that produced it, and the alternative is silence about a real
        # broken cite -- a checker that misses is worse than one that is
        # occasionally wrong out loud. Raised by cross-family review 2026-08-19
        # and answered here rather than in code.
    if token.endswith("-") and _AFTER_HYPHEN.match(follow):
        # Corroboration comes from the other side of the break: the next line
        # begins with a filename and a line number. Prose hyphenation does not
        # ("# well-\n# known problem" stays two lines).
        #
        # This replaced a rule that looked at the TOKEN instead -- stitch when
        # it already held a `/` or an earlier `-`. That rule missed the
        # single-hyphen name `api-` + `service.go:120`, which cross-family
        # review supplied; and once this one exists it is redundant, because
        # every token-shaped case it caught also has a corroborating follow
        # line. Removed rather than kept beside this one: a clause that cannot
        # change an outcome reads like a second guard and is not one.
        return True
    if token.endswith(".") and _AFTER_DOT.match(follow):
        return True
    if token.endswith(":") and _AFTER_COLON.match(follow):
        return True
    return False


def _rejoin_wrapped(lines):
    """Yield (line_number, text) with wrapped comment paths stitched back.

    The line number reported is the one where the cite STARTS, which is where
    a reader would look. The continuation line is emptied rather than dropped
    so the numbering of everything after it stays true. Joins chain, because
    a path can be cut twice.
    """
    joined = list(lines)
    for index in range(len(joined) - 1):
        # `nxt` walks past lines already pulled up, so a path cut twice
        # stitches in one pass. Chaining off `index + 1` instead reads the
        # line it just emptied and stops after the first join -- which is a
        # silent partial fix, the worst of the three outcomes here.
        nxt = index + 1
        while nxt < len(joined):
            current = joined[index]
            if not _COMMENT.match(current):
                break
            follow_raw = joined[nxt].lstrip()
            if not follow_raw.startswith("#"):
                break
            follow = follow_raw[1:].lstrip()
            if not follow:
                # An emptied continuation line, or a bare `#`. Without
                # this the loop rejoins the same line forever: the
                # slash case needs no corroboration from what follows,
                # so an empty follow satisfies it and the join is a
                # no-op. Found by hanging on a real contract while all
                # 114 tests passed, which is what a suite with no case
                # for a wrap at the end of a comment block looks like.
                break
            token = current.rstrip().rsplit(" ", 1)[-1]
            if not _continues(token, follow):
                break
            joined[index] = current.rstrip() + follow
            joined[nxt] = "#"
            nxt += 1
    return list(enumerate(joined, 1))


def extract(text):
    """Every cite in the text, in the order written, with its source line."""
    found = []
    for number, raw_line in _rejoin_wrapped(text.splitlines()):
        line = _blank_urls(raw_line)
        for match in _CITE.finditer(line):
            path = match.group("path")
            if Path(path).suffix.lower() not in _SOURCE_SUFFIXES:
                continue
            end = match.group("end")
            found.append(Cite(path, int(match.group("start")),
                              int(end) if end is not None else None,
                              number, match.group(0)))
    return found


# Directories that hold a COPY of code that lives somewhere else. Walking into
# them turns every bare filename ambiguous against its own duplicate, which is
# not a fact about the contract. Measured on our own installation: a stray
# gascity checkout and a bead worktree, both sitting inside the city root, put
# `cmd_hook_claim.go` and six other files at "2 files share this name".
#
# The general rule is derived rather than listed -- a subdirectory that is
# itself a git checkout is a different repository, whatever it is called -- and
# the names below cover the copies that carry no `.git` of their own.
_VENDORED = {"node_modules", "vendor", "target", "build", "dist", "__pycache__"}


def _is_nested_checkout(path, root):
    return path != root and (path / ".git").exists()


def _index(roots):
    """{basename: [paths]} across every root, for resolving bare filenames.

    Bare filenames are how real contracts are written -- a paragraph that has
    already given the full path says `tmux.go:2208` for the next four cites --
    so refusing to resolve them would report noise on the common case.
    """
    by_name = {}
    for root in roots:
        root = Path(root)
        stack = [root]
        while stack:
            current = stack.pop()
            try:
                entries = list(current.iterdir())
            except OSError:
                continue
            for entry in entries:
                if entry.name.startswith(".") or entry.name in _VENDORED:
                    continue
                if entry.is_symlink():
                    continue
                if entry.is_dir():
                    if not _is_nested_checkout(entry, root):
                        stack.append(entry)
                elif entry.is_file():
                    by_name.setdefault(entry.name, []).append(entry)
    return by_name


def _count_lines(path):
    try:
        with open(path, "rb") as handle:
            return sum(1 for _ in handle)
    except OSError:
        return None


def resolve(cites, roots):
    """Classify each cite against the given installation roots. Mutates in place.

    Two passes, because the second depends on the first. A contract that gives
    the full path once and then says `tmux.go:2208` four more times is the
    normal way these are written, and treating every short form as ambiguous
    buried the real findings: measured on our own contract, 24 of 45 cites came
    back ambiguous, against 4 broken. So a full path that resolved in pass one
    disambiguates its own basename in pass two.

    This is an inference and is counted as one -- `resolved_via_contract` is a
    distinct status, not folded into `resolved`, so a reader can see how much
    of a green run rests on the document's own earlier text rather than on a
    path that was actually written out at the cite.
    """
    roots = [Path(r) for r in roots]
    by_name = _index(roots)
    explicit = {}
    for cite in cites:
        if "/" not in cite.path:
            continue
        hits = [root / cite.path for root in roots if (root / cite.path).is_file()]
        if len(hits) == 1:
            explicit.setdefault(Path(cite.path).name, set()).add(hits[0])
    # Only a basename the contract pinned to exactly ONE full path can stand in
    # for the short form. Two different full paths sharing a basename leave the
    # short form genuinely ambiguous, and guessing there would be the failure
    # this whole status exists to avoid.
    from_contract = {name: next(iter(paths))
                     for name, paths in explicit.items() if len(paths) == 1}

    for cite in cites:
        candidates = []
        if "/" in cite.path:
            candidates = [root / cite.path for root in roots
                          if (root / cite.path).is_file()]
            if not candidates:
                # A SUFFIX of the path is the third form contracts use, and it
                # is the one that reads most like a full path while being
                # neither: `issueops/lease.go` for a file that lives at
                # `internal/storage/issueops/lease.go`. Without this it is
                # reported MISSING even with the right repository passed as a
                # root, which sends a reader looking for a deletion that never
                # happened. Ambiguity is handled below, identically.
                suffix = "/" + cite.path
                candidates = [c for c in by_name.get(Path(cite.path).name, [])
                              if str(c).endswith(suffix)]
        elif cite.path in from_contract:
            cite.via_contract = True
            candidates = [from_contract[cite.path]]
        else:
            candidates = by_name.get(cite.path, [])
        if not candidates:
            cite.status = "missing"
            cite.detail = ("no such file under any root (dot-directories, "
                           "symlinks, vendored copies and nested checkouts "
                           "are not searched)")
            continue
        if len(candidates) > 1:
            # A file sitting directly AT a root is what a bare name means when
            # one exists: `city.toml` in a contract about an installation whose
            # root holds a `city.toml` is that file, not one of the six copies
            # further down. This is not a tiebreak among equals, so it is a
            # plain resolve rather than an inference -- the root-relative path
            # and the bare name are the same string, which is the only reason
            # the full-path pass above cannot see it.
            at_root = [c for c in candidates if c.parent in roots]
            if len(at_root) == 1:
                candidates = at_root
        if len(candidates) > 1:
            # Reported, never failed. The contract is not wrong; the check
            # cannot tell which file was meant, and guessing would make a
            # green run mean less than it does.
            cite.status = "ambiguous"
            cite.resolved = candidates[0]
            cite.detail = (f"{len(candidates)} files share this name; "
                           f"write the path from the root to pin it")
            continue
        target = candidates[0]
        total = _count_lines(target)
        cite.resolved = target
        if total is None:
            cite.status = "missing"
            cite.detail = "file could not be read"
        elif cite.last > total:
            cite.status = "out_of_range"
            cite.detail = f"file has {total} lines"
        else:
            cite.status = "resolved_via_contract" if cite.via_contract else "resolved"
    return cites
