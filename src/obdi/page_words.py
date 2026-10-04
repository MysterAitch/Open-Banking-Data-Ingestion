"""The words the pages use for the three things the owner named, and the words they retired.

A KNOWN BALANCE is what a source says an account's balance was on a date. An account's rows are
IN AGREEMENT with it when they reproduce it: worked out, never declared. Two SOURCES, by contrast,
MATCH or DO NOT MATCH each other; "agree" is never said of two sources. A period is PROTECTED when
a person has decided it must not change silently; the page says "protected period", never "span",
and "not protected", never "protected through nowhere". "Reconciled" is kept for Actual's own
sense. A person STATES a balance (the verb), and a known balance came "from you" when they did.

Internal names (functions, classes, modules, CSS classes, URLs) keep their old words; only text a
reader sees is held to this. `RETIRED_ON_PAGES` is what the page walk fails on, so a page written
later is held to the same vocabulary the day it exists.
"""

from __future__ import annotations

#: Phrases no page may show, compared without regard to case against a page's visible text.
RETIRED_ON_PAGES: tuple[str, ...] = (
    "anchor",
    "stated figure",
    "protected through nowhere",
    "protected span",
    "family balance",
    "cross-source agreement",
)
