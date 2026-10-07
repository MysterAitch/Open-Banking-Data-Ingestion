"""The other party's identifiers, as a source states them, in one form a second source can equal.

A transaction row carries two strong identifiers of the other side (`entities.md` section 2):
the party's ACCOUNT, and the source's own id for the PARTY. Both are derived from the landed
record at derive time, so a rebuild fills them, and neither is part of a payment's identity
(the content key and the entity id read neither).

The account has ONE canonical form so that the same account stated by two sources is equal:
a UK account is `<six-digit sort code>-<eight-digit number>`, whether a feed states the two
apart (Starling) or an aggregator states an IBAN that carries them (a `GB` IBAN holds the sort
code and number after its bank code). Any other IBAN is kept as the IBAN, upper case, no spaces.
Nothing here guesses: a sort code or number that is not the expected digits yields no account,
since an identifier built from a misread field would join two parties on nothing.
"""

from __future__ import annotations

import re

#: A `GB` IBAN: check digits, a four-letter bank code, then the sort code and the account number.
_GB_IBAN = re.compile(r"^GB\d{2}[A-Z]{4}(\d{6})(\d{8})$")
_IBAN = re.compile(r"^[A-Z]{2}\d{2}[A-Z0-9]{11,30}$")


def uk_account(sort_code: str, number: str) -> str:
    """`112233-12345678` from a sort code and an account number, or empty where either is not
    the digits a UK account has (hyphens and spaces in the sort code are formatting)."""
    code = re.sub(r"[\s-]", "", sort_code)
    digits = re.sub(r"\s", "", number)
    if re.fullmatch(r"\d{6}", code) and re.fullmatch(r"\d{8}", digits):
        return f"{code}-{digits}"
    return ""


def iban_account(iban: str) -> str:
    """The canonical account of an IBAN: a `GB` one as sort code and number (so a feed that
    states the two apart equals it), any other as the IBAN itself; empty where it is not one."""
    squeezed = re.sub(r"\s", "", iban).upper()
    uk = _GB_IBAN.match(squeezed)
    if uk:
        return uk_account(uk.group(1), uk.group(2))
    return squeezed if _IBAN.fullmatch(squeezed) else ""


def source_party_id(source: str, identifier: str) -> str:
    """A source's own id for a party, prefixed by the source so two sources' ids cannot collide;
    empty where the source stated none."""
    clean = identifier.strip()
    return f"{source}:{clean}" if clean else ""
