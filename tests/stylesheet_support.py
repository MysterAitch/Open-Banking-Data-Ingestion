"""Reading the shared stylesheet from outside it.

The stylesheet is one inline block sent with every page, so tests read it the way
a browser does: from a rendered page, with its custom properties resolved per
colour scheme. Nothing here imports the module that holds the text, so moving
the stylesheet does not break the tests that guard it.
"""

from __future__ import annotations

import re

from obdi.callback import render_page

_TOKEN = re.compile(r"(--[a-z0-9-]+)\s*:\s*([^;]+);")
_LIGHT_BLOCK = re.compile(r":root \{([^}]*)\}")
_DARK_BLOCK = re.compile(
    r"@media \(prefers-color-scheme: dark\) \{\s*:root \{([^}]*)\}", re.S
)


def stylesheet() -> str:
    """The inline stylesheet of an ordinary page, and nothing else."""
    page = render_page("t", "").decode()
    return page.split("<style>", 1)[1].split("</style>", 1)[0]


def _tokens(block: str) -> dict[str, str]:
    return {name: value.strip() for name, value in _TOKEN.findall(block)}


def light_tokens(css: str) -> dict[str, str]:
    block = _LIGHT_BLOCK.search(css)
    return _tokens(block.group(1)) if block else {}


def dark_tokens(css: str) -> dict[str, str]:
    """The dark scheme's values; a token it does not restate keeps its light value."""
    block = _DARK_BLOCK.search(css)
    return _tokens(block.group(1)) if block else {}


def without_token_blocks(css: str) -> str:
    """The stylesheet with both token blocks removed: the rules that USE tokens."""
    css = _DARK_BLOCK.sub("", css, count=1)
    return _LIGHT_BLOCK.sub("", css, count=1)


def length_px(css: str, value: str) -> float:
    """A declared length as pixels, resolving `var(--name)` against the light tokens.

    Only px and rem, which are the units the stylesheet uses for a target size.
    """
    tokens = light_tokens(css)
    for _ in range(4):
        match = re.fullmatch(r"var\((--[a-z0-9-]+)\)", value.strip())
        if not match:
            break
        value = tokens[match.group(1)]
    number = re.fullmatch(r"([\d.]+)(px|rem)", value.strip())
    assert number, f"not a length: {value!r}"
    return float(number.group(1)) * (16 if number.group(2) == "rem" else 1)
