"""What each source can prove about WHICH account it is talking about.

Sources know different subsets: a TrueLayer account payload carries a sort
code and a full account number; its card payload carries only the last
four digits; a CSV export often carries an account number and nothing
else; a statement PDF may carry the full number in a header. So identity
cannot be one composite key - a composite that any source is missing part
of can never be compared at all.

Instead each source contributes CLAIMS, compared individually, and
stronger claims DERIVE the weaker ones they contain: a full account
number also proves its own last four digits, so the source that knows
everything still meets the source that knows almost nothing.

Strength is carried explicitly and never flattened away:

  strong  a sort code and account number together, or an IBAN. Unique by
          construction, so a match IS an identity.
  weak    four digits alone. Two cards at different banks can share them,
          so a match is a PROPOSAL for a person to confirm, never a merge.

Values are held in the clear because this is one person's own financial
data on their own machine, and the evidence they came from is already
stored verbatim one layer down. What is refused is DISPLAY: nothing here
renders an account or card number to a page, only its last four digits.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

#: A claim strong enough to assert identity on its own.
STRONG = "strong"
#: A claim that may collide between institutions - propose, never merge.
WEAK = "weak"

_DIGITS = re.compile(r"\D+")


def _digits(value: object) -> str:
    return _DIGITS.sub("", str(value or ""))


@dataclass(frozen=True)
class Claim:
    """One thing a source can prove about which account this is."""

    kind: str
    value: str
    strength: str

    def masked(self) -> str:
        """How this claim may appear on a page: never the number itself."""
        if self.kind == "iban":
            return f"IBAN ending {self.value[-4:]}"
        if self.kind == "uk-account":
            return f"UK account ending {self.value[-4:]}"
        if self.kind == "account-number":
            return f"account ending {self.value[-4:]}"
        if self.kind == CARD_LAST_4:
            return f"card ending {self.value[-4:]}"
        if self.kind == ACCOUNT_LAST_4:
            return f"account ending {self.value[-4:]}"
        return f"ending {self.value[-4:]}"


#: Four digits off an ACCOUNT number and four digits off a CARD number are
#: unrelated numbers in unrelated namespaces. A credit card ending 5678
#: and a current account ending 5678 are not the same thing, and a single
#: "last four" kind would have matched them - a false identity, which is
#: the one failure this module exists to prevent.
ACCOUNT_LAST_4 = "account-last-4"
CARD_LAST_4 = "card-last-4"


def _last_four(digits: str, kind: str) -> Claim | None:
    if len(digits) < 4:
        return None
    return Claim(kind, digits[-4:], WEAK)


def derive(claims: list[Claim]) -> list[Claim]:
    """Add the weaker claims that the stronger ones already contain.

    The reason a full-number source can match a last-four source at all.
    Deduplicated and ordered strongest first, so the best available
    evidence for a match is the one reported.
    """
    out: dict[tuple[str, str], Claim] = {}
    for claim in claims:
        out[(claim.kind, claim.value)] = claim
        if claim.kind in ("uk-account", "account-number"):
            # The account number is the tail of a uk-account claim.
            number = claim.value.split(":")[-1]
            implied = _last_four(number, ACCOUNT_LAST_4)
            if implied is not None:
                out.setdefault((implied.kind, implied.value), implied)
    return sorted(
        out.values(), key=lambda c: (0 if c.strength == STRONG else 1, c.kind, c.value)
    )


def claims_from_truelayer_account(item: dict[str, object]) -> list[Claim]:
    """A TrueLayer accounts entry: sort code, number and IBAN when present."""
    raw = item.get("account_number")
    if not isinstance(raw, dict):
        return []
    claims: list[Claim] = []
    number = _digits(raw.get("number"))
    sort_code = _digits(raw.get("sort_code"))
    if number and sort_code:
        claims.append(Claim("uk-account", f"{sort_code}:{number}", STRONG))
    elif number:
        claims.append(Claim("account-number", number, STRONG))
    iban = str(raw.get("iban") or "").replace(" ", "").upper()
    if iban:
        claims.append(Claim("iban", iban, STRONG))
    return derive(claims)


def claims_from_truelayer_card(item: dict[str, object]) -> list[Claim]:
    """A TrueLayer cards entry: the last four digits, and nothing stronger.

    The provider deliberately never returns a full card number, so a card
    can only ever make a weak claim - which is exactly why a card match
    has to be confirmed rather than applied.
    """
    partial = _digits(item.get("partial_card_number"))
    claim = _last_four(partial, CARD_LAST_4)
    return [claim] if claim is not None else []


def claims_from_starling_identifiers(payload: dict[str, object]) -> list[Claim]:
    """Starling's identifiers endpoint: sort code, number, IBAN.

    Starling's accounts call does NOT carry these - they live on a
    separate endpoint - which is why the first-party side could not be
    matched to anything before it was fetched.
    """
    claims: list[Claim] = []
    number = _digits(payload.get("accountIdentifier"))
    sort_code = _digits(payload.get("bankIdentifier"))
    if number and sort_code:
        claims.append(Claim("uk-account", f"{sort_code}:{number}", STRONG))
    elif number:
        claims.append(Claim("account-number", number, STRONG))
    iban = str(payload.get("iban") or "").replace(" ", "").upper()
    if iban:
        claims.append(Claim("iban", iban, STRONG))
    return derive(claims)


def claims_from_file_hints(hints: dict[str, object]) -> list[Claim]:
    """Whatever an imported export happened to carry in its header.

    Exports are inconsistent by nature - some name an account number,
    credit-card exports usually name four digits - so this takes what is
    offered and claims nothing about what is absent.
    """
    claims: list[Claim] = []
    number = _digits(hints.get("account_number"))
    sort_code = _digits(hints.get("sort_code"))
    if number and sort_code:
        claims.append(Claim("uk-account", f"{sort_code}:{number}", STRONG))
    elif number and len(number) > 4:
        claims.append(Claim("account-number", number, STRONG))
    elif number:
        claims.append(Claim(ACCOUNT_LAST_4, number, WEAK))
    partial = _digits(hints.get("card_last_four"))
    weak = _last_four(partial, CARD_LAST_4)
    if weak is not None:
        claims.append(weak)
    return derive(claims)


def best_match(left: list[Claim], right: list[Claim]) -> Claim | None:
    """The strongest claim two sources share, or None if they share none.

    Returning the CLAIM rather than a boolean is deliberate: the page has
    to say what matched, because "same account" resting on four digits is
    a different statement from "same account" resting on a sort code and
    number, and a reader deserves to know which one they are being asked
    to believe.
    """
    theirs = {(claim.kind, claim.value) for claim in right}
    shared = [claim for claim in derive(left) if (claim.kind, claim.value) in theirs]
    if not shared:
        return None
    return sorted(shared, key=lambda c: (0 if c.strength == STRONG else 1, c.kind))[0]
