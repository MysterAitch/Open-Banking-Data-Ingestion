"""What each source can prove about WHICH account it is talking about.

Sources know different subsets: a TrueLayer account payload carries a sort
code and a full account number; its card payload carries only the last
four digits, because the provider never returns a card number in full; a
CSV export often carries an account number and nothing else; a statement
may carry the full number in a header. So identity cannot be one
composite key - a composite that any source is missing part of can never
be compared at all, and the source that knows least would match nothing.

Each source contributes CLAIMS, compared individually, and stronger
claims DERIVE the weaker ones they contain: a full account number also
proves its own last four digits, so the source that knows everything
still meets the source that knows almost nothing.

Two properties are enforced by the TYPES rather than by convention,
because both were already broken once by careful people writing careful
code:

CONFLATION. Four digits off a card number and four digits off an account
number are unrelated numbers. They are separate classes, so comparing
them is False at runtime and mixing them is an error under the type
checker - not a rule to remember, a thing that cannot be said. Open
Banking makes the distinction concrete: only card ACCOUNTS appear on the
cards endpoint, and a debit card is not an account at all, so the two
namespaces never describe the same identifier.

DISPLAY. A full account number has no readable form. str() and repr()
both yield the mask, so an f-string, a log line and a traceback all leak
four digits rather than an account. Reading the real value takes an
explicit .reveal(), which is one grep away from an audit.
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


class Sensitive:
    """A value that must be transformed before it can be shown.

    The masking is not politeness - it is the only rendering that exists.
    A subclass supplies masked(); everything that prints goes through it,
    so the unmasked value cannot reach a page, a log or a traceback by
    accident. It can still be read deliberately, by name, via reveal().
    """

    _value: str

    def masked(self) -> str:
        raise NotImplementedError

    def reveal(self) -> str:
        """The real value. Deliberately awkward and deliberately greppable:
        every place that needs the number says so in one word."""
        return self._value

    def __str__(self) -> str:
        return self.masked()

    def __repr__(self) -> str:
        return f"{type(self).__name__}({self.masked()})"

    def __format__(self, spec: str) -> str:
        return format(self.masked(), spec)


@dataclass(frozen=True, repr=False)
class AccountNumber(Sensitive):
    """A full bank account number."""

    _value: str

    def masked(self) -> str:
        return f"account ending {self._value[-4:]}"


@dataclass(frozen=True, repr=False)
class UkAccount(Sensitive):
    """A sort code and account number together - unique by construction."""

    _value: str

    def masked(self) -> str:
        return f"UK account ending {self._value[-4:]}"


@dataclass(frozen=True, repr=False)
class Iban(Sensitive):
    """An international account number."""

    _value: str

    def masked(self) -> str:
        return f"IBAN ending {self._value[-4:]}"


@dataclass(frozen=True, repr=False)
class AccountLastFour(Sensitive):
    """The last four digits of an ACCOUNT number."""

    _value: str

    def masked(self) -> str:
        return f"account ending {self._value}"


@dataclass(frozen=True, repr=False)
class CardLastFour(Sensitive):
    """The last four digits of a CARD number.

    A different class from AccountLastFour on purpose, so the two can
    never compare equal: a credit card ending 5678 and a current account
    ending 5678 are unrelated, and proposing them as one account is the
    single failure this module exists to prevent.
    """

    _value: str

    def masked(self) -> str:
        return f"card ending {self._value}"


#: Anything a source can claim. The TYPE carries what kind of identifier
#: it is, so no separate "kind" string can drift out of step with it.
Identifier = (
    UkAccount | AccountNumber | Iban | AccountLastFour | CardLastFour
)

_STRENGTH: dict[type, str] = {
    UkAccount: STRONG,
    AccountNumber: STRONG,
    Iban: STRONG,
    AccountLastFour: WEAK,
    CardLastFour: WEAK,
}


@dataclass(frozen=True)
class Claim:
    """One thing a source can prove about which account this is."""

    identifier: Identifier

    @property
    def strength(self) -> str:
        return _STRENGTH.get(type(self.identifier), WEAK)

    @property
    def kind(self) -> str:
        return type(self.identifier).__name__

    def masked(self) -> str:
        return self.identifier.masked()


def _account_last_four(digits: str) -> Claim | None:
    if len(digits) < 4:
        return None
    return Claim(AccountLastFour(digits[-4:]))


def derive(claims: list[Claim]) -> list[Claim]:
    """Add the weaker claims that the stronger ones already contain.

    The reason a full-number source can match a last-four source at all.
    Deduplicated, strongest first, so the best available evidence for a
    match is the one reported.
    """
    out: dict[Identifier, Claim] = {}
    for claim in claims:
        out[claim.identifier] = claim
        if isinstance(claim.identifier, UkAccount | AccountNumber):
            # The account number is the tail of a uk-account value.
            number = claim.identifier.reveal().split(":")[-1]
            implied = _account_last_four(number)
            if implied is not None:
                out.setdefault(implied.identifier, implied)
    return sorted(
        out.values(),
        key=lambda c: (0 if c.strength == STRONG else 1, c.kind, c.masked()),
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
        claims.append(Claim(UkAccount(f"{sort_code}:{number}")))
    elif number:
        claims.append(Claim(AccountNumber(number)))
    iban = str(raw.get("iban") or "").replace(" ", "").upper()
    if iban:
        claims.append(Claim(Iban(iban)))
    return derive(claims)


def claims_from_truelayer_card(item: dict[str, object]) -> list[Claim]:
    """A TrueLayer cards entry: four digits, and nothing stronger ever.

    The provider never returns a card number in full, so a card can only
    make a weak claim - which is why a card match is always a proposal.
    """
    partial = _digits(item.get("partial_card_number"))
    if len(partial) < 4:
        return []
    return [Claim(CardLastFour(partial[-4:]))]


def claims_from_starling_identifiers(payload: dict[str, object]) -> list[Claim]:
    """Starling's identifiers endpoint: sort code, number, IBAN.

    Starling's accounts call carries none of these - they live on a
    separate endpoint - which is why the first-party view of an account
    could not be matched to any other view of it before this was fetched.
    """
    claims: list[Claim] = []
    number = _digits(payload.get("accountIdentifier"))
    sort_code = _digits(payload.get("bankIdentifier"))
    if number and sort_code:
        claims.append(Claim(UkAccount(f"{sort_code}:{number}")))
    elif number:
        claims.append(Claim(AccountNumber(number)))
    iban = str(payload.get("iban") or "").replace(" ", "").upper()
    if iban:
        claims.append(Claim(Iban(iban)))
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
        claims.append(Claim(UkAccount(f"{sort_code}:{number}")))
    elif number and len(number) > 4:
        claims.append(Claim(AccountNumber(number)))
    elif number:
        claims.append(Claim(AccountLastFour(number)))
    partial = _digits(hints.get("card_last_four"))
    if len(partial) >= 4:
        claims.append(Claim(CardLastFour(partial[-4:])))
    return derive(claims)


def best_match(left: list[Claim], right: list[Claim]) -> Claim | None:
    """The strongest claim two sources share, or None if they share none.

    Returning the CLAIM rather than a boolean is deliberate: "same
    account" resting on four digits is a different statement from "same
    account" resting on a sort code and number, and a reader deserves to
    know which one they are being asked to believe.
    """
    theirs = {claim.identifier for claim in derive(right)}
    shared = [claim for claim in derive(left) if claim.identifier in theirs]
    if not shared:
        return None
    return sorted(shared, key=lambda c: (0 if c.strength == STRONG else 1, c.kind))[0]
