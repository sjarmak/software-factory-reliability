"""Scaffold a probe pack by reading an installation.

The kit's own audit of Gas City moved by one finding when four previously
invisible effects were DECLARED, and by zero when a real merge fence shipped.
That is the shape of a checker that reads a hand-written file: the declaration
is the thing being scored, so hand-writing it is the whole exercise, and nobody
adopting the kit is going to hand-author one for a factory they did not build.

This module removes the blank page. It scans an installation for a catalog of
effects a software factory commonly performs on the outside world, and emits a
probe pack containing only the ones it actually found, with the paths where it
found them.

What it deliberately does NOT do is guess an effect identity. An identity is a
claim that repeats are distinguishable at the destination, and no scan can
establish that. The generated pack leaves every identity undecided, so a user
who runs `infer` without editing gets "unknown" for every effect. Filling those
in is the hand-written part, and it is small, specific, and about the user's own
system rather than about this file's vocabulary.
"""

import re
from collections import OrderedDict

import infer


# Effects a factory performs on something outside itself. Each entry names the
# destination it reaches and the invocations that reach it.
#
# The catalog is a starting point and is expected to be incomplete for any real
# installation: an effect performed here and absent from the generated pack is
# invisible to the derivation, which is the same failure as an undeclared effect
# one layer up. `probes-init` prints that warning every run for exactly this
# reason.
CATALOG = [
    {
        "name": "git_push",
        "destination": "code_host",
        "patterns": [r"\bgit\b[^|;&]*\bpush\b"],
        "not_regex": ["--dry-run", "--help"],
        "note": "A push replaces a remote ref. Without an expected prior value "
                "a repeat is a blind overwrite rather than a repeat.",
    },
    {
        "name": "open_pull_request",
        "destination": "code_host",
        "patterns": [r"\bgh pr create\b", r"\bglab mr create\b"],
        "not_regex": ["--help"],
        "note": "Creating a pull request twice makes two pull requests unless "
                "the caller checks first or the forge dedupes.",
    },
    {
        "name": "merge_pull_request",
        "destination": "code_host",
        "patterns": [r"\bgh pr merge\b", r"\bglab mr merge\b"],
        "not_regex": ["--help"],
        "note": "A merge that does not name the commit it tested can land a "
                "different tree than the one that passed.",
    },
    {
        "name": "create_issue",
        "destination": "tracker",
        "patterns": [r"\bgh issue create\b", r"\bglab issue create\b"],
        "not_regex": ["--help"],
        "note": "A retried filing produces a duplicate issue, which is cheap "
                "to create and expensive to reconcile.",
    },
    {
        "name": "publish_release",
        "destination": "registry",
        "patterns": [r"\bgh release create\b", r"\bnpm publish\b",
                     r"\bcargo publish\b", r"\btwine upload\b",
                     r"\bpoetry publish\b"],
        "not_regex": ["--dry-run", "--help"],
        "note": "Most registries refuse a duplicate version, so the failure "
                "mode is a half-published release rather than two.",
    },
    {
        "name": "push_image",
        "destination": "registry",
        "patterns": [r"\bdocker push\b", r"\bpodman push\b",
                     r"\bbuildah push\b"],
        "not_regex": ["--help"],
        "note": "A mutable tag pushed twice points somewhere new; a digest "
                "does not.",
    },
    {
        "name": "deploy",
        "destination": "runtime",
        "patterns": [r"\bkubectl apply\b", r"\bhelm upgrade\b",
                     r"\bterraform apply\b", r"\bpulumi up\b"],
        "not_regex": ["--dry-run", "--help", "-o yaml"],
        "note": "These are the convergent ones: applying twice is usually "
                "safe, which is worth confirming rather than assuming.",
    },
    {
        "name": "post_message",
        "destination": "messaging",
        "patterns": [r"hooks\.slack\.com", r"slack\.com/api/chat\.postMessage",
                     r"discord\.com/api/webhooks", r"\bslack\b[^|;&]*\bpost\b"],
        "not_regex": ["--help"],
        "note": "A duplicate notification is not corruption, but it is the "
                "effect people notice, so it sets the trust level for the rest.",
    },
    {
        "name": "send_mail",
        "destination": "messaging",
        "patterns": [r"\bsendmail\b", r"\bmailx?\b -s", r"api\.sendgrid\.com",
                     r"api\.mailgun\.net"],
        "not_regex": ["--help"],
        "note": "Mail cannot be recalled, so a retry after an ambiguous "
                "failure is a decision about the reader, not about the system.",
    },
    {
        "name": "write_object_store",
        "destination": "object_store",
        "patterns": [r"\baws s3 (cp|sync|mv|rm)\b", r"\bgsutil (cp|rsync|rm)\b",
                     r"\baz storage blob upload\b"],
        "not_regex": ["--dryrun", "--dry-run", "--help"],
        "note": "Overwriting an object is silent unless the write is "
                "conditioned on the version it expects to replace.",
    },
]

# Flags whose names suggest they carry an effect identity. Reported as
# candidates for the user to accept or reject, never applied: a flag named
# --key may be a credential, and one named --id may name the wrong noun.
_CANDIDATE_FLAG = re.compile(
    r"--[a-z0-9][a-z0-9-]*(?:idempot|key|token|sha|digest|checksum|"
    r"expected|lease|generation|revision|etag|version|ref)[a-z0-9-]*")

# Directories that hold code an installation runs, in rough order of how likely
# a factory is to keep executable logic there.
_LIKELY_CODE_DIRS = ["bin", "scripts", "hooks", "tools", "ci", ".github"]

# Directories that hold dependencies, build output, caches, or runtime state
# rather than the installation's own logic. A call site found in any of them is
# a call site in somebody else's code or in a file the factory wrote itself.
_DEFAULT_EXCLUDES = [
    "**/.git/**",
    "**/node_modules/**",
    "**/vendor/**",
    "**/testdata/**",
    "**/.venv/**",
    "**/__pycache__/**",
    "**/.mypy_cache/**",
    "**/.pytest_cache/**",
    "**/.ruff_cache/**",
    "**/.terraform/**",
    "**/target/**",
    "**/dist/**",
    "**/build/**",
    "**/out/**",
]


def _matchers_for(entry):
    return [{"regex": pattern, "not_regex": entry.get("not_regex", [])}
            for pattern in entry["patterns"]]


# One alternation over every catalog pattern, used only to decide whether a
# file is worth the per-segment analysis. Splitting a file into logical units
# and testing each one is the accurate path and it is not cheap; on a real
# installation the overwhelming majority of files contain none of these tokens
# at all, and a scan slow enough to look like a hang does not get run twice.
#
# It is a pure prefilter: anything it admits is still decided by find_sites, so
# it can cost accuracy in only one direction, and that direction is closed by
# construction because it is the disjunction of the same patterns.
_PREFILTER = re.compile("|".join("(?:%s)" % e["patterns"][i]
                                 for e in CATALOG
                                 for i in range(len(e["patterns"]))))


def survey(root, scan=None):
    """Return (effects found, number of files read).

    Each value in the map is a dict with the relative paths that matched and
    the identity flags observed in the matching command segments. The count is
    every file read, not only the ones that survived the prefilter: a header
    that reported the survivors would understate the search by two orders of
    magnitude and read as though the scan had barely looked.
    """
    scan = scan or {
        "include_globs": ["**"],
        "exclude_globs": _DEFAULT_EXCLUDES,
        # Scaffolding walks a whole installation, so it is the command most
        # exposed to nested checkouts and worktrees. `infer` with a
        # hand-written pack names its own directories and rarely is.
        "prune_nested_repos": True,
    }
    # Held in memory once rather than re-walked per effect: the walk is the
    # only part that touches the filesystem, and re-doing it ten times turned a
    # five second scan into a minute.
    files = []
    scanned = 0
    for rel, text, language in infer.scan_files(root, scan):
        scanned += 1
        if _PREFILTER.search(text):
            files.append((rel, text, language))
    found = OrderedDict()
    for entry in CATALOG:
        matchers = _matchers_for(entry)
        paths = OrderedDict()
        flags = OrderedDict()
        for rel, text, language in files:
            for _, segment in infer.find_sites(text, language, matchers):
                paths.setdefault(rel, 0)
                paths[rel] += 1
                for flag in _CANDIDATE_FLAG.findall(segment):
                    flags[flag] = flags.get(flag, 0) + 1
        if paths:
            found[entry["name"]] = {
                "entry": entry,
                "paths": paths,
                "candidate_flags": flags,
                "total": sum(paths.values()),
            }
    return found, scanned


def directory_distribution(found):
    """Call sites per top-level directory, most first.

    Printed rather than acted on. On the installation this was written against,
    62% of the hits were in directories the factory WRITES (runtime state and
    its own reports) rather than directories it RUNS, and no general rule tells
    those apart: the same command in a script is a call site and in a report is
    a description of one. The person who knows which is which is the one
    running the command, so the numbers go to them.
    """
    counts = {}
    for item in found.values():
        for rel, hits in item["paths"].items():
            top = rel.split("/")[0]
            counts[top] = counts.get(top, 0) + hits
    return sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))


# Suffixes whose files are not executed. A command found in one of them was
# read by a person or by an agent, not run by a shell, so it belongs in the
# instructed group where no static marker is expected to bind it.
#
# This is a fact about the file format rather than a judgment about what the
# text means, which is why the scaffold is allowed to act on it. Everything
# else it declines to classify and leaves for the person running it.
_PROSE_SUFFIXES = (".md", ".markdown", ".rst", ".txt")


def _split_by_execution(paths):
    """Partition matched paths into (executed, prose)."""
    executed, prose = OrderedDict(), OrderedDict()
    for rel, hits in paths.items():
        bucket = prose if rel.lower().endswith(_PROSE_SUFFIXES) else executed
        bucket[rel] = hits
    return executed, prose


def _include_globs(found):
    """Derive scan globs from where the hits actually landed.

    Guessing a fixed list of directories produces a pack that silently misses
    an installation laid out differently, and the miss looks like an absence of
    call sites rather than an absence of scanning.
    """
    tops = OrderedDict()
    for item in found.values():
        for rel in item["paths"]:
            if "/" in rel:
                tops.setdefault(rel.split("/")[0], True)
            else:
                # A hit in a top-level file, not a directory. Emitting
                # "CLAUDE.md/*" for it produced a glob that matches nothing, so
                # the generated pack silently dropped the very site that put
                # the entry there.
                tops.setdefault(rel, False)
    if not tops:
        tops = OrderedDict((d, True) for d in _LIKELY_CODE_DIRS)
    globs = []
    for top, is_directory in tops.items():
        if is_directory:
            globs.append("%s/*" % top)
            globs.append("%s/**/*" % top)
        else:
            globs.append(top)
    return globs


def _yaml_list(values, indent):
    if not values:
        return "[]"
    pad = " " * indent
    return "\n" + "\n".join('%s- %s' % (pad, _yaml_str(v)) for v in values)


def _yaml_str(value):
    return "'%s'" % value.replace("'", "''")


def render(root, found, scanned):
    """Render the probe pack as YAML text.

    Written by hand rather than dumped, because the comments are the product:
    a generated file whose every field is a bare value teaches nobody what the
    field means, and this one is meant to be edited.
    """
    name = str(root).rstrip("/").split("/")[-1] or "factory"
    lines = [
        "# Probe pack scaffolded by: factory-check probes-init",
        "#",
        "# Scanned %d file(s) and found %d effect class(es) this installation"
        % (scanned, len(found)),
        "# performs on something outside itself.",
        "#",
        "# THIS FILE IS A STARTING POINT AND IT IS NOT COMPLETE. The scaffold",
        "# knows a catalog of common effects; it does not know yours. An effect",
        "# your factory performs and this file omits is invisible to the",
        "# derivation, which is the same failure as an undeclared effect in a",
        "# hand-written contract, one layer earlier. Read it against what your",
        "# factory actually reaches out and touches.",
        "#",
        "# Every identity below reads 'unknown' on purpose. An effect identity",
        "# is a claim that the DESTINATION can tell a repeat from a new",
        "# request, and no scan of your code can establish that. Deciding it is",
        "# the part that needs you. Until you do, `factory-check infer` reports",
        "# the identity as withdrawn rather than confirmed, which is correct.",
        "",
        "version: %s" % infer.PROBE_VERSION,
        "name: %s-scaffold" % name,
        "factory_name: %s" % name,
        "",
        "# Paths holding tests and checkers that exercise these verbs rather",
        "# than performing the effect. Declared, not guessed from a filename:",
        "# a scan that decides what is a test by pattern-matching the path will",
        "# be wrong about the one file that matters. Add yours here.",
        "harness_globs: &harness_globs []",
        "",
        "scan:",
        "  include_globs:",
    ]
    for glob in _include_globs(found):
        lines.append("    - %s" % _yaml_str(glob))
    lines.append("  exclude_globs:")
    for glob in _DEFAULT_EXCLUDES:
        lines.append("    - %s" % _yaml_str(glob))
    lines.append("")
    lines.append("effects:")

    for effect_name, item in found.items():
        entry = item["entry"]
        lines.append("  - name: %s" % effect_name)
        lines.append("    destination: %s" % entry["destination"])
        for chunk in _wrap(entry["note"], 68):
            lines.append("    # %s" % chunk)
        lines.append("    # Found %d call site(s) in %d file(s): %s"
                     % (item["total"], len(item["paths"]),
                        ", ".join(list(item["paths"])[:6])
                        + (", ..." if len(item["paths"]) > 6 else "")))
        executed, prose = _split_by_execution(item["paths"])

        def _any_of(indent):
            block = ["%sany_of:" % indent]
            for pattern in entry["patterns"]:
                block.append("%s  - regex: %s" % (indent, _yaml_str(pattern)))
                if entry.get("not_regex"):
                    block.append("%s    not_regex: [%s]"
                                 % (indent, ", ".join(_yaml_str(n)
                                                      for n in entry["not_regex"])))
            return block

        lines.append("    call_site:")
        lines.append("      harness_globs: *harness_globs")
        lines.append("      scripted:")
        lines.append("        path_globs:")
        for glob in _include_globs({effect_name: {"paths": executed}}):
            lines.append("          - %s" % _yaml_str(glob))
        lines.extend(_any_of("        "))
        lines.append("      # An effect an agent performs by reading an")
        lines.append("      # instruction has no argument list until run time,")
        lines.append("      # so no static marker can bind it and the identity")
        lines.append("      # must be withdrawn however clean the scripted")
        lines.append("      # sites look.")
        if prose:
            lines.append("      #")
            lines.append("      # These paths are documents, not programs: the")
            lines.append("      # command was read, not run. That is a fact")
            lines.append("      # about the file format, which is why it is")
            lines.append("      # filled in. If any of these are prose ABOUT")
            lines.append("      # the effect rather than an instruction to")
            lines.append("      # perform it, move them out or exclude them.")
            lines.append("      instructed:")
            lines.append("        path_globs:")
            for glob in sorted(prose):
                lines.append("          - %s" % _yaml_str(glob))
            lines.extend(_any_of("        "))
        else:
            lines.append("      # No document in this installation mentions")
            lines.append("      # this effect. If prompts or task templates")
            lines.append("      # elsewhere tell an agent to perform it, add")
            lines.append("      # them here.")
            lines.append("      # instructed:")
            lines.append("      #   path_globs: ['prompts/**/*.md']")
            lines.append("      #   any_of:")
            lines.append("      #     - regex: %s" % _yaml_str(entry["patterns"][0]))
        lines.append("    identity:")
        lines.append("      # Name the value that lets the DESTINATION tell a")
        lines.append("      # repeat from a new request, then list the flags or")
        lines.append("      # argument names that carry it at a call site.")
        if item["candidate_flags"]:
            observed = ", ".join(
                "%s (%dx)" % (flag, count)
                for flag, count in sorted(item["candidate_flags"].items(),
                                          key=lambda kv: -kv[1])[:6])
            lines.append("      # Flags observed at these call sites, offered as")
            lines.append("      # candidates and not applied: %s" % observed)
            lines.append("      # Check each one before using it. A flag named")
            lines.append("      # --key may carry a credential, and one named")
            lines.append("      # --id may name the wrong noun.")
        else:
            lines.append("      # No identity-shaped flags were observed at any")
            lines.append("      # of these call sites. That is a finding: it")
            lines.append("      # means nothing at the call site distinguishes a")
            lines.append("      # repeat, whatever the destination does.")
        lines.append("      name: unknown")
        lines.append("      markers: []")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _wrap(text, width):
    words, line, out = text.split(), "", []
    for word in words:
        if line and len(line) + 1 + len(word) > width:
            out.append(line)
            line = word
        else:
            line = (line + " " + word).strip()
    if line:
        out.append(line)
    return out
