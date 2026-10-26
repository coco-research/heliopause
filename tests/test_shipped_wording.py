"""Shipped files use the product name in prose and carry no retired ids or internal references."""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Files that ship: the package, the plugin and the public docs (see .public-exclude for the rest).
_SHIPPED = [
    *sorted((ROOT / "atlas").rglob("*.py")),
    *sorted((ROOT / "atlas" / "static").rglob("*.js")),
    *sorted((ROOT / "atlas" / "static").rglob("*.css")),
    *sorted((ROOT / "atlas" / "static").rglob("*.html")),
    *sorted((ROOT / "plugin").rglob("*.sh")),
    *sorted((ROOT / "plugin").rglob("*.md")),
    ROOT / "README.md",
    ROOT / "SECURITY.md",
    ROOT / "docs" / "readme" / "architecture.svg",
]

# The product name in prose. Protocol names (X-Atlas-Token, X-Atlas-UI) are kept as they are on the wire.
_OLD_NAME = re.compile(r"(?<![-\w])Atlas(?![-\w])")
_INTERNAL = re.compile(
    r"ADR-\d|council change|Budget Office's|ask the Budget Office|Design's locked spec"
)


def _hits(pattern: re.Pattern) -> list[str]:
    found = []
    for path in _SHIPPED:
        if not path.is_file():
            continue
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if pattern.search(line):
                found.append(f"{path.relative_to(ROOT)}:{n}: {line.strip()[:120]}")
    return found


def test_prose_and_prompts_say_heliopause() -> None:
    assert _hits(_OLD_NAME) == []


def test_no_internal_references_in_shipped_files() -> None:
    assert _hits(_INTERNAL) == []


def test_architecture_svg_uses_the_queue_folder() -> None:
    svg = (ROOT / "docs" / "readme" / "architecture.svg").read_text(encoding="utf-8")
    assert ">pi/<" not in svg
    assert ">queue/<" in svg


def test_readme_names_the_hooks_and_roles_routes() -> None:
    # Both routes are served (atlas/__main__.py); the README lists them and says hooks are read-only.
    text = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "`GET /api/hooks`, and, in lab mode, `GET /api/roles`." in text
    assert "`atlas hooks-list` only read hook metadata" in text


def test_docs_say_the_scan_never_opens_a_chat() -> None:
    # The adopt scan lists chat files by name, size and time and never opens one.
    for path in (ROOT / "README.md", ROOT / "atlas" / "adopt.py"):
        text = path.read_text(encoding="utf-8")
        assert "chat-title" not in text, path.name
    assert "it never opens a chat" in (ROOT / "README.md").read_text(encoding="utf-8")
