"""Unit tests for prose-check heuristics.

The single-line paragraph stack check exists to catch dramatic
fragmentation (stacks of punchy one-liners). A document whose paragraphs
are written unwrapped, one physical line per paragraph, must not trip it.
"""

import importlib.util
from pathlib import Path

_SPEC = importlib.util.spec_from_file_location(
    "prose_check",
    Path(__file__).resolve().parent.parent / "scripts" / "prose-check.py",
)
prose_check = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(prose_check)


def run_check(lines):
    warnings = []
    fence_flags = [False] * len(lines)
    prose_check.check_single_line_runs("test.md", lines, fence_flags, warnings)
    return warnings


def test_short_fragment_stack_warns():
    lines = []
    for i in range(5):
        lines.append(f"Punchy fragment number {i}.")
        lines.append("")
    warnings = run_check(lines)
    assert [w[2] for w in warnings] == ["one-line-paragraph-stack"]


def test_unwrapped_full_paragraphs_do_not_warn():
    paragraph = (
        "This is a full paragraph written on a single unwrapped line, long "
        "enough that no reader would mistake it for a dramatic fragment, and "
        "the checker should treat it as ordinary hard-unwrapped prose."
    )
    assert len(paragraph) > prose_check.FRAGMENT_MAX_CHARS
    lines = []
    for _ in range(6):
        lines.append(paragraph)
        lines.append("")
    assert run_check(lines) == []


def test_isolated_short_paragraph_between_long_ones_does_not_warn():
    long_paragraph = (
        "A long unwrapped paragraph that provides surrounding context for a "
        "deliberate one-sentence beat, which on its own is fine and should "
        "only be flagged when stacked with more than three of its kind."
    )
    lines = [
        long_paragraph,
        "",
        "The original agent was still running.",
        "",
        long_paragraph,
        "",
    ]
    assert run_check(lines) == []
