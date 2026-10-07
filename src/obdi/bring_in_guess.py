"""What obdi can tell about whose a freshly kept statement is, before it asks.

THE HEADING LEADS. Where a document prints the label of an account (a credit union section, or
a whole document that covers the one account still open), the label is what an earlier
assignment was made on, so the account every earlier assignment of that label chose is the guess
(`GuessBasis.HEADING`). A label given two accounts makes no guess and names both, and a
document's siblings (same reader, same issuer) are never consulted for it: a sibling is a
document of some other account of the issuer's as often as of this one, and a guess made from
it filed a year's rows of one account under a closed loan.

A STATEMENT WITH NO HEADING is guessed from a kept statement already filed under an account: the
same reader that names the same issuers (`GuessBasis.READER`), or a file name that is an
earlier file's with the year changed (`GuessBasis.NAME`). Where the evidence points at two
accounts, or the reader and the name point at different ones, there is no guess.

A guess selects an account in a chooser and says why; it never reads anything in, because a name
is a habit, not a fact. An unreadable evidence listing is the caller's to say, not a guess.

Nothing here reads a figure. The inputs are the kept-statement listing's file names, reader
names, issuer names, days listed, and section tokens.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum

from .core.namespaces import UNASSIGNED_ACCOUNT

#: A year as a file name writes it: four digits, bounded by anything but a digit.
_YEAR = re.compile(r"(?<!\d)(?:19|20)\d{2}(?!\d)")
_EXTENSION = re.compile(r"\.[A-Za-z0-9]{1,5}$")
_SEPARATORS = re.compile(r"[^a-z0-9#]+")

Entry = Mapping[str, object]


class GuessBasis(StrEnum):
    READER = "reader"
    NAME = "name"
    BOTH = "both"
    HEADING = "heading"


@dataclass(frozen=True)
class Guess:
    """An account a statement is probably for, and the earlier statement that says so."""

    #: The account to pre-select; "" where the heading was given several (`candidates`).
    account: str
    basis: GuessBasis
    #: The file name of the earlier statement that was matched (the newest, where several are).
    match_origin: str = ""
    #: The year the earlier statement lists up to, or its file name's year, or "".
    match_year: str = ""
    #: Where a heading was given more than one account: all of them, sorted.
    candidates: tuple[str, ...] = ()


def file_shape(origin: str) -> str:
    """A file name with its extension, case, separators, and years taken out of the comparison."""
    stem = _EXTENSION.sub("", origin.strip().lower())
    return _SEPARATORS.sub("-", _YEAR.sub("#", stem)).strip("-")


def _sections(entry: Entry) -> list[Mapping[str, object]]:
    raw = entry.get("sections")
    return [part for part in raw if isinstance(part, Mapping)] if isinstance(raw, list) else []


def _names(entry: Entry) -> frozenset[str]:
    raw = entry.get("names")
    if not isinstance(raw, list):
        return frozenset()
    return frozenset(str(item[0]) for item in raw if isinstance(item, list | tuple) and item)


def _year_of(entry: Entry) -> str:
    days = entry.get("listed_days")
    if isinstance(days, list) and len(days) == 2 and str(days[1])[:4].isdigit():
        return str(days[1])[:4]
    found = _YEAR.search(str(entry.get("origin") or ""))
    return found.group(0) if found else ""


def _newest(entries: Sequence[Entry]) -> Entry:
    return max(entries, key=lambda item: int(str(item["id"])))


def _accounts(entries: Sequence[Entry]) -> set[str]:
    return {str(item["account_ref"]) for item in entries}


def _only(accounts: set[str]) -> str:
    """The one account in `accounts`, or "" where there are none or several."""
    return next(iter(accounts)) if len(accounts) == 1 else ""


def guess_account(entry: Entry, kept: Sequence[Entry]) -> Guess | None:
    """The account `entry` is probably for, from `kept` (the whole kept-statement listing).

    Only statements already filed under one account are evidence; `entry` itself and a document
    of several accounts are neither guessed for nor matched against.
    """
    if _sections(entry):
        return None
    ident = int(str(entry["id"]))
    filed = [
        item
        for item in kept
        if int(str(item["id"])) != ident
        and str(item["account_ref"]) != UNASSIGNED_ACCOUNT
        and not _sections(item)
    ]
    headed = bool(entry.get("heading"))
    if headed and (guess := _by_heading(entry.get("heading_given"))) is not None:
        return guess
    parser = entry.get("parser")
    names = _names(entry)
    # A document with a heading is never matched on its reader: see the module's note.
    by_reader = (
        [item for item in filed if item.get("parser") == parser and _names(item) == names]
        if parser and names and not headed
        else []
    )
    shape = file_shape(str(entry.get("origin") or ""))
    by_name = (
        [item for item in filed if file_shape(str(item.get("origin") or "")) == shape]
        if shape
        else []
    )
    reader = _only(_accounts(by_reader))
    named = _only(_accounts(by_name))
    if reader and named and reader != named:
        return None
    account = reader or named
    if not account:
        return None
    basis = (
        GuessBasis.BOTH if reader and named else GuessBasis.READER if reader else GuessBasis.NAME
    )
    evidence = [
        item
        for item in [*(by_reader if reader else []), *(by_name if named else [])]
        if str(item["account_ref"]) == account
    ]
    example = _newest(evidence)
    return Guess(account, basis, str(example["origin"]), _year_of(example))


def _by_heading(given: object) -> Guess | None:
    """The guess from the accounts a heading was given: one account, or all of them unchosen."""
    accounts = sorted({str(item) for item in given}) if isinstance(given, list) else []
    if len(accounts) == 1:
        return Guess(accounts[0], GuessBasis.HEADING)
    if accounts:
        return Guess("", GuessBasis.HEADING, candidates=tuple(accounts))
    return None


def section_guess(part: Entry) -> Guess | None:
    """The guess for one section of a document of several accounts, from its heading alone."""
    return _by_heading(part.get("given"))


__all__ = [
    "Guess",
    "GuessBasis",
    "file_shape",
    "guess_account",
    "section_guess",
]
