"""The one place a count meets a noun or a verb on a page, so no sentence says "(s)".

`plural(2, "row")` is "2 rows"; `plural(1204, "row")` is "1,204 rows" (thousands separators
from 1,000, so one number reads the same on every page). `word(n, ...)` gives the noun alone
for a sentence that has already printed the number. `agree(n, "is")` picks the verb or
determiner that agrees with a count: the irregulars the pages need are in `IRREGULARS`, and
adding a pair there is the whole of teaching the helper a new one.

A noun with a regular plural takes an "s"; one that does not names its plural
(`plural(2, "match", "matches")`).
"""

from __future__ import annotations

#: Singular and plural of the words that are not nouns, which `agree` picks between.
IRREGULARS: dict[str, tuple[str, str]] = {
    "is": ("is", "are"),
    "was": ("was", "were"),
    "has": ("has", "have"),
    "does": ("does", "do"),
    "moves": ("moves", "move"),
    "changes": ("changes", "change"),
    "shares": ("shares", "share"),
    "holds": ("holds", "hold"),
    "this": ("this", "these"),
    "that": ("that", "those"),
    "it": ("it", "they"),
    "its": ("its", "their"),
    "differs": ("differs", "differ"),
    "agrees": ("agrees", "agree"),
    "disagrees": ("disagrees", "disagree"),
    "matches": ("matches", "match"),
    "needs": ("needs", "need"),
    "lists": ("lists", "list"),
    "states": ("states", "state"),
}


def word(count: int, singular: str, plural_form: str | None = None) -> str:
    """The noun in the number `count` calls for, without the count."""
    return singular if count == 1 else plural_form or singular + "s"


def plural(count: int, singular: str, plural_form: str | None = None) -> str:
    """A count with its noun: "1 row", "17 rows", "1,434 rows"."""
    return f"{count:,} {word(count, singular, plural_form)}"


def agree(count: int, form: str) -> str:
    """The singular or plural of a verb or determiner listed in `IRREGULARS`."""
    singular, plural_form = IRREGULARS[form]
    return singular if count == 1 else plural_form
