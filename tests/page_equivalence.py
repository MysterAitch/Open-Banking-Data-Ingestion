"""Two renderings of a page, compared as the reader sees them.

A change that moves where a page gets its readings from (computed on the request, or read from
stored rows) must not change what the page says. The comparison is of the page's body with every
part that legitimately differs between two loads set to a fixed word, so that what is left
differing is a difference in what the page states.

WHAT IS NORMALISED, and nothing else: the footer (the build it names), an instant (`2026-10-04
15:24`, or a bare `HH:MM` after the scheduler's "finished"), and an age (`(98 days ago)`,
`(3 weeks ago)`, `just now`, `5 minutes ago`). Each is one rule below with the shape it matches;
a figure, a word, or a date is never touched, which `tests/test_page_equivalence.py` holds with
constructed pages whose answer is known.

Used by `tests/test_ledger_speed.py` over the large store and by the equivalence test beside this
file; the pages a comparison covers are `PAGES`.
"""

from __future__ import annotations

import re

from large_store_corpus import MAIN
from large_store_pages import Pages

#: The pages a reading change must leave as they are: Today, the main account, and a card.
PAGES: dict[str, str] = {
    "today": "/",
    "account": f"/ledger?ref={MAIN}",
    "card": "/ledger?ref=card-1",
}

#: (shape, what it becomes). Ordered: the footer goes first so its text is never matched by
#: the rules after it.
_VOLATILE: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"<footer>.*?</footer>", re.DOTALL), "<footer></footer>"),
    (re.compile(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}"), "<instant>"),
    (re.compile(r"(finished )\d{2}:\d{2}"), r"\1<instant>"),
    (
        re.compile(
            r"\((?:today|yesterday|[\d,]+ days ago|\d+ weeks? ago|\d+ months? ago"
            r"|over (?:a year|\d+ years) ago)\)"
        ),
        "(<age>)",
    ),
    (re.compile(r"\bjust now\b|\b\d+ (?:minutes?|hours?) ago\b"), "<age>"),
)


def normalised(body: str) -> str:
    """The page with the parts that differ between two loads of the same store fixed."""
    for shape, replacement in _VOLATILE:
        body = shape.sub(replacement, body)
    return body


def rendered(pages: Pages, path: str) -> str:
    """The normalised body of a page served over the store, refusing any answer but a page."""
    served = pages.get(path)
    assert served.status == 200, (path, served.status)
    return normalised(served.body)
