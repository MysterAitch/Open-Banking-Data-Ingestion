"""Which issuers a statement names, and how often.

A statement's masked shape hides every name, the issuer's included, so a
statement no parser recognises could not be told from any other: eight card
statements sat unidentified beside two more of a different layout. Counting
the issuer names in the text answers the question without showing any of it.

The COUNT is the evidence. An issuer prints its own name on every page, in
the header, the footer, and the small print; a payee is named once per
payment. So "Halifax 23, Santander 1" is a Halifax statement with one payment
to Santander, and a name found once is a hint and nothing more.

Only names from the list below are ever reported. This is shown on a page
served on a GET, and a list of every capitalised word would be a list of
payees.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

#: Names looked for, as they are printed. Whole words only: "Chase" must not
#: be found inside "purchase", nor "TSB" inside a reference.
ISSUER_NAMES: tuple[str, ...] = (
    "American Express",
    "Aqua",
    "Bank of Scotland",
    "Barclaycard",
    "Barclays",
    "Capital One",
    "Chase",
    "Co-operative Bank",
    "Credit Union",
    "first direct",
    "Halifax",
    "HSBC",
    "John Lewis",
    "Lloyds",
    "M&S Bank",
    "Marbles",
    "MBNA",
    "Metro Bank",
    "Monzo",
    "Nationwide",
    "NatWest",
    "NewDay",
    "Revolut",
    "Royal Bank of Scotland",
    "Sainsbury's Bank",
    "Santander",
    "Starling",
    "Tesco Bank",
    "TSB",
    "Vanquis",
    "Virgin Money",
    "Zopa",
    # Card schemes, not issuers: which one a card runs on narrows the field.
    "Mastercard",
    "Visa",
)

_PATTERNS = {
    name: re.compile(r"(?<![A-Za-z0-9])" + re.escape(name) + r"(?![A-Za-z0-9])", re.IGNORECASE)
    for name in ISSUER_NAMES
}


def names_found(lines: Iterable[str]) -> list[tuple[str, int]]:
    """(name, times found) for each listed name the text holds, most frequent first.

    Whitespace inside a name is squeezed first, because a PDF's text layer
    can double the gap between two words of one name.
    """
    text = "\n".join(" ".join(line.split()) for line in lines)
    counted = [(name, len(pattern.findall(text))) for name, pattern in _PATTERNS.items()]
    return sorted(
        ((name, count) for name, count in counted if count),
        key=lambda found: (-found[1], found[0].casefold()),
    )
