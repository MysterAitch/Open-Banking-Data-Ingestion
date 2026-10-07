"""Reading a printed figure, shared by the current-account statement readers.

A statement prints a pound sign and a minus in whichever order its generator
chose, and the text layer sometimes mangles the sign into two characters. A
reader that handled only one placement would read an overdrawn balance as a
credit, which is the failure the arithmetic gate exists to catch late and
this exists to prevent early.
"""

from __future__ import annotations

import re

#: A figure, with the currency symbol the text layer yields. That is "£" on
#: the page and "Â£" where a pound sign's two UTF-8 bytes were read as two
#: characters, so an optional stray capital A-circumflex precedes one symbol.
#: The minus may come before the symbol ("-£9,999.99") or after it
#: ("£-999.99"); both occur on one statement. Never a letter in general: a
#: payee's name among the figures must not parse as money.
AMOUNT = re.compile(r"-?\s*Â?[^\w\s.,-]?\s*-?\d[\d,]*\.\d{2}")


def tidy(text: str) -> str:
    """Whitespace made single, case kept: a description is identity, and a
    re-read must give the same string whatever the spacing the page used."""
    return re.sub(r"\s+", " ", text).strip()


def minor(text: str) -> int:
    """A figure in minor units; a minus anywhere before the digits is negative."""
    stripped = text.strip()
    whole, _, pence = re.sub(r"[^\d.]", "", stripped).partition(".")
    value = int(whole) * 100 + int(pence)
    return -value if "-" in stripped else value


def is_figure(text: str) -> bool:
    return AMOUNT.fullmatch(text.strip()) is not None
