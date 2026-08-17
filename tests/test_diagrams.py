"""The rendered diagram page must not drift from its Mermaid sources.

GitHub renders Mermaid inside a fenced block in Markdown and does not render a
standalone .mmd file, so docs/diagrams/README.md carries a copy of each source.
Two copies of the same text drift. These tests are the mechanism that notices.
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DIAGRAMS = ROOT / "docs" / "diagrams"
PAGE = DIAGRAMS / "README.md"

FENCE_RE = re.compile(r"^```mermaid\n(.*?)^```$", re.MULTILINE | re.DOTALL)
REGENERATE = "regenerate the fences in docs/diagrams/README.md from the .mmd sources"


def _sources():
    return sorted(DIAGRAMS.glob("*.mmd"))


def _fences():
    return FENCE_RE.findall(PAGE.read_text())


def test_every_source_is_rendered_on_the_page():
    fences = _fences()
    for source in _sources():
        text = source.read_text()
        assert text in fences, (
            f"{source.name} has no matching mermaid fence in {PAGE.name}; {REGENERATE}"
        )


def test_every_fence_comes_from_a_source_file():
    sources = {source.read_text(): source.name for source in _sources()}
    for fence in _fences():
        assert fence in sources, (
            f"{PAGE.name} has a mermaid fence matching no .mmd file; {REGENERATE}"
        )


def test_the_page_links_each_source_file():
    page = PAGE.read_text()
    for source in _sources():
        assert f"({source.name})" in page, (
            f"{PAGE.name} does not link {source.name}"
        )


def test_there_is_at_least_one_diagram():
    assert _sources(), "docs/diagrams holds no .mmd sources"
