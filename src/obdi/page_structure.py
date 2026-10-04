"""Structure a page's data tables for assistive technology, in one place.

Tables are written by some forty call sites, each as a bare `<table>` with bare
`<th>` cells. A screen reader needs two things from them that no sentence
supplies: which cells head a column or a row, and what the table is. Adding both
at each site would leave the next table written without them, so the layout
adds them once, as the page is assembled.

HEADERS. A `<th>` in a row that also holds data cells heads that ROW (a label
beside its value: "Rows in the month" then 11); a `<th>` in a row of only
headers heads a COLUMN. One rule, applied to every table, so a table written
tomorrow is right without anyone remembering.

CAPTION. The table is named by what sits just above it: the nearest heading or
disclosure summary before it, or the page's own title where there is none. The
caption is visually hidden, because that heading is already on screen saying it,
and an announced name is all that is missing. A table that already carries a
caption keeps it.

SCROLLING. A table that cannot fit a narrow screen at enlarged text scrolls
inside its own focusable region rather than widening the page, which is what
reflow asks of tabular data. One that already sits in a scrolling box keeps it.
"""

from __future__ import annotations

import re

_TABLE = re.compile(r"<table\b[^>]*>.*?</table>", re.S)
_ROW = re.compile(r"<tr\b[^>]*>.*?</tr>", re.S)
_BARE_HEADER = re.compile(r"<th(?P<attrs>(?:\s[^>]*)?)>")
_NAMING = re.compile(
    r"<h[1-6]\b[^>]*>(?P<heading>.*?)</h[1-6]>|<summary\b[^>]*>(?P<summary>.*?)</summary>", re.S
)
_TAG = re.compile(r"<[^>]+>")
#: A table already inside its own sideways-scrolling box needs no second one.
_SCROLLS = re.compile(r'<div\b[^>]*(?:overflow-x:\s*auto|class="scroll")[^>]*>\s*$')


def _scoped_row(row: re.Match[str]) -> str:
    text = row.group(0)
    scope = "row" if "<td" in text else "col"

    def add(header: re.Match[str]) -> str:
        attrs = header.group("attrs")
        if "scope=" in attrs:
            return header.group(0)
        return f'<th{attrs} scope="{scope}">'

    return _BARE_HEADER.sub(add, text)


def _name_before(body: str, position: int, fallback: str) -> str:
    """The text of the nearest heading or summary above `position`, already escaped."""
    found = None
    for found in _NAMING.finditer(body, 0, position):
        pass
    if found is not None:
        inner = found.group("heading") or found.group("summary") or ""
        text = " ".join(_TAG.sub("", inner).split())
        if text:
            return text
    return fallback


def structure_tables(body: str, *, fallback_name: str) -> str:
    """`body` with scoped header cells and a hidden caption on every table.

    `fallback_name` is the already-escaped name used where nothing precedes a
    table to name it.
    """
    pieces: list[str] = []
    last = 0
    for table in _TABLE.finditer(body):
        text = _ROW.sub(_scoped_row, table.group(0))
        name = _name_before(body, table.start(), fallback_name)
        if "<caption" not in text:
            opening = re.match(r"<table\b[^>]*>", text)
            assert opening is not None
            caption = f'<caption class="visually-hidden">{name}</caption>'
            text = opening.group(0) + caption + text[opening.end() :]
        if not _SCROLLS.search(body, max(0, table.start() - 120), table.start()):
            text = (
                f'<div class="scroll" role="region" tabindex="0" data-table-scroll '
                f'aria-label="{name}, scrolls sideways">{text}</div>'
            )
        pieces.append(body[last : table.start()])
        pieces.append(text)
        last = table.end()
    pieces.append(body[last:])
    return "".join(pieces)
