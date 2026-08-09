#!/usr/bin/env python3
"""Prose conventions checker for this repository.

Scans every Markdown file under the repo root (skipping .beads, out,
node_modules, .git and similar) for hard-banned characters and phrases
(failures) and weaker stylistic signals (warnings). Also scans YAML, JSON,
JSONL, and Python files under the content directories for em dashes only.

Usage:
    python3 scripts/prose-check.py [--warn-only]

Exit status 1 if any failure was found (0 with --warn-only), else 0.
Finding format:  file:line: rule: excerpt
"""

import argparse
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

SKIP_DIR_NAMES = {
    ".beads",
    "out",
    "node_modules",
    ".git",
    "__pycache__",
    ".pytest_cache",
    ".venv",
    "venv",
}

# STYLE.md quotes the banned-phrase list, so it is exempt from the phrase
# rules. AGENTS.md and CLAUDE.md carry tool-managed blocks (written by bd
# init and the agent harness) whose wording contributors do not control, so
# the phrase rules are relaxed for them too. The em dash rule applies to all
# three; STYLE.md declares it banned everywhere in the repository.
PHRASE_EXEMPT_RELPATHS = {"STYLE.md", "AGENTS.md", "CLAUDE.md"}

# Files removed from the scan entirely. Empty today; the em dash rule has no
# exemptions.
SKIP_RELPATHS = set()

# Non-Markdown files in these directories are scanned for em dashes only.
CODE_SCAN_DIRS = [
    "schemas",
    "examples",
    "cmd",
    "patterns",
    "drills",
    "adapters",
    "recipes",
    "fixtures",
    "observability",
    "tests",
    "scripts",
    "evidence",
    "diagrams",
]
CODE_SCAN_SUFFIXES = {".yaml", ".yml", ".json", ".jsonl", ".py"}

# Written as escapes so this file passes its own em dash scan.
EM_DASH = "\u2014"
EN_DASH = "\u2013"
CURLY_APOSTROPHE = "\u2019"

BANNED_PHRASES = [
    "delve",
    "it's worth noting",
    "here's the thing",
    "the key insight",
    "the real question",
    "let's unpack",
    "this changes everything",
    "game-changer",
    "honest caveat",
    "to be honest",
    "seamlessly",
]

SENTENCE_ADVERB_RE = re.compile(r"(^|[.!?]\s+)(Crucially|Fundamentally|Importantly),")
ANSWER_STARTS = ("It ", "It'", "The answer")

# Markers whose single-line blocks are structure, not prose paragraphs.
NON_PROSE_LINE_RE = re.compile(r"^\s*(#|\||[-*+]\s|\d+\.\s|>|!\[|\[|`)")

MAX_EXCERPT = 80


def excerpt(line):
    text = line.strip()
    if len(text) > MAX_EXCERPT:
        text = text[: MAX_EXCERPT - 3] + "..."
    return text


def rel(path):
    try:
        return str(path.relative_to(REPO_ROOT))
    except ValueError:
        return str(path)


def iter_files(suffixes, roots):
    for root in roots:
        if not root.exists():
            continue
        for path in sorted(root.rglob("*")):
            if not path.is_file():
                continue
            if path.suffix.lower() not in suffixes:
                continue
            relative = path.relative_to(REPO_ROOT)
            if any(part in SKIP_DIR_NAMES for part in relative.parts):
                continue
            if str(relative) in SKIP_RELPATHS:
                continue
            yield path


def read_lines(path):
    try:
        return path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError as err:
        print(f"{rel(path)}:0: read-error: {err}", file=sys.stderr)
        return []


def check_markdown(path, failures, warnings):
    lines = read_lines(path)
    relpath = rel(path)
    phrase_checks = relpath not in PHRASE_EXEMPT_RELPATHS

    in_fence = False
    fence_flags = []
    for line in lines:
        if line.lstrip().startswith(("```", "~~~")):
            fence_flags.append(True)
            in_fence = not in_fence
        else:
            fence_flags.append(in_fence)

    not_just_count = 0
    for idx, line in enumerate(lines):
        lineno = idx + 1
        normalized = line.replace(CURLY_APOSTROPHE, "'")
        lowered = normalized.lower()
        in_code = fence_flags[idx]

        if EM_DASH in line:
            failures.append((relpath, lineno, "em-dash", excerpt(line)))

        if phrase_checks:
            for phrase in BANNED_PHRASES:
                if phrase in lowered:
                    failures.append((relpath, lineno, f"banned-phrase({phrase})", excerpt(line)))

        if in_code:
            continue

        if EN_DASH in line:
            warnings.append((relpath, lineno, "en-dash", excerpt(line)))

        not_just_count += lowered.count("not just")

        if SENTENCE_ADVERB_RE.search(line):
            warnings.append((relpath, lineno, "sentence-adverb", excerpt(line)))

        if "?" in line:
            for follow_idx in (idx + 1, idx + 2):
                if follow_idx >= len(lines) or fence_flags[follow_idx]:
                    continue
                follow = lines[follow_idx].strip()
                if follow.startswith(ANSWER_STARTS):
                    warnings.append(
                        (relpath, lineno, "rhetorical-question", excerpt(lines[follow_idx]))
                    )
                    break

    if not_just_count > 2:
        warnings.append((relpath, 1, "not-just-overuse", f"{not_just_count} occurrences"))

    check_single_line_runs(relpath, lines, fence_flags, warnings)


# A single-line paragraph longer than this is a full paragraph written on one
# unwrapped line, not a punchy fragment; the stack check ignores it.
FRAGMENT_MAX_CHARS = 120


def check_single_line_runs(relpath, lines, fence_flags, warnings):
    """Warn on more than three consecutive short single-line prose paragraphs."""
    blocks = []
    current = None
    for idx, line in enumerate(lines):
        if fence_flags[idx] or not line.strip():
            if current is not None:
                blocks.append(current)
                current = None
            continue
        if current is None:
            current = {"start": idx + 1, "count": 0, "prose": True, "short": True}
        current["count"] += 1
        if len(line.strip()) > FRAGMENT_MAX_CHARS:
            current["short"] = False
        if NON_PROSE_LINE_RE.match(line):
            current["prose"] = False
    if current is not None:
        blocks.append(current)

    run_start = None
    run_len = 0
    for block in blocks + [{"count": 0, "prose": False, "start": 0, "short": False}]:
        if block["count"] == 1 and block["prose"] and block["short"]:
            if run_len == 0:
                run_start = block["start"]
            run_len += 1
        else:
            if run_len > 3:
                warnings.append(
                    (relpath, run_start, "one-line-paragraph-stack", f"{run_len} in a row")
                )
            run_len = 0
            run_start = None


def check_code_file(path, failures):
    relpath = rel(path)
    for idx, line in enumerate(read_lines(path)):
        if EM_DASH in line:
            failures.append((relpath, idx + 1, "em-dash", excerpt(line)))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--warn-only",
        action="store_true",
        help="print failures but exit 0",
    )
    args = parser.parse_args()

    failures = []
    warnings = []

    for path in iter_files({".md"}, [REPO_ROOT]):
        check_markdown(path, failures, warnings)

    code_roots = [REPO_ROOT / d for d in CODE_SCAN_DIRS]
    for path in iter_files(CODE_SCAN_SUFFIXES, code_roots):
        check_code_file(path, failures)

    for relpath, lineno, rule, text in failures:
        label = "FAIL" if not args.warn_only else "WARN(downgraded)"
        print(f"{relpath}:{lineno}: {label} {rule}: {text}")
    for relpath, lineno, rule, text in warnings:
        print(f"{relpath}:{lineno}: WARN {rule}: {text}")

    counts = {}
    for relpath, _, _, _ in failures:
        counts.setdefault(relpath, [0, 0])[0] += 1
    for relpath, _, _, _ in warnings:
        counts.setdefault(relpath, [0, 0])[1] += 1
    for relpath in sorted(counts):
        nfail, nwarn = counts[relpath]
        print(f"SUMMARY {relpath}: {nfail} failure(s), {nwarn} warning(s)")
    print(f"TOTAL: {len(failures)} failure(s), {len(warnings)} warning(s)")

    if failures and not args.warn_only:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
