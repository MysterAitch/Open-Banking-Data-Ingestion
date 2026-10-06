"""What obdi can tell about whose a freshly kept statement is, before it asks.

Two kinds of knowledge, kept apart because they are trusted differently.

A DOCUMENT OF SEVERAL ACCOUNTS (the credit union's "all accounts" export) is divided by its own
reader into sections, and each section carries the label the issuer prints. A label an earlier
document's section was assigned under names the same account again: that is a recorded decision
of the owner's about the same label, not a resemblance, so `plan_sections` plans those
assignments and the caller makes them without asking. A section nobody has ever assigned, or one
the reader refuses, leaves the whole document for the owner.

A SINGLE STATEMENT is only ever GUESSED (`guess_account`), from a kept statement already filed
under an account: the same reader that names the same issuers (`GuessBasis.READER`), or a file
name that is an earlier file's with the year changed (`GuessBasis.NAME`). A guess selects an
account in a chooser and says why; it never reads anything in, because two accounts at one
issuer read alike and a name is a habit, not a fact. Where the evidence points at two accounts,
or the reader and the name point at different ones, there is no guess: the chooser stays empty.

Nothing here reads a figure. The inputs are the kept-statement listing's file names, reader
names, issuer names, days listed, and section tokens.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum

from .namespaces import UNASSIGNED_ACCOUNT

#: A year as a file name writes it: four digits, bounded by anything but a digit.
_YEAR = re.compile(r"(?<!\d)(?:19|20)\d{2}(?!\d)")
_EXTENSION = re.compile(r"\.[A-Za-z0-9]{1,5}$")
_SEPARATORS = re.compile(r"[^a-z0-9#]+")

Entry = Mapping[str, object]


class GuessBasis(StrEnum):
    READER = "reader"
    NAME = "name"
    BOTH = "both"


@dataclass(frozen=True)
class Guess:
    """An account a statement is probably for, and the earlier statement that says so."""

    account: str
    basis: GuessBasis
    #: The file name of the earlier statement that was matched (the newest, where several are).
    match_origin: str
    #: The year the earlier statement lists up to, or its file name's year, or "".
    match_year: str


@dataclass(frozen=True)
class SectionPlan:
    """What can be done about a document of several accounts without asking."""

    #: (section token, account) for each section an earlier document's label names.
    to_assign: tuple[tuple[str, str], ...]
    #: The accounts sections of it are already assigned to, sorted.
    held_accounts: tuple[str, ...]
    #: Whether every section is, or would be after `to_assign`, assigned. A document with a
    #: section nobody has assigned or one the reader refuses is not complete, and plans nothing.
    complete: bool


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
    parser = entry.get("parser")
    names = _names(entry)
    by_reader = (
        [item for item in filed if item.get("parser") == parser and _names(item) == names]
        if parser and names
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


def plan_sections(entry: Entry) -> SectionPlan | None:
    """What can be assigned without asking in a document of several accounts, or None where
    `entry` is not one."""
    parts = _sections(entry)
    if not parts:
        return None
    held = sorted({str(part["account"]) for part in parts if part.get("account")})
    waiting = [part for part in parts if not part.get("account")]
    known = [
        (str(part["token"]), str(part["suggested"]))
        for part in waiting
        if part.get("suggested") and not part.get("refusal")
    ]
    complete = len(known) == len(waiting)
    return SectionPlan(tuple(known) if complete else (), tuple(held), complete)


__all__ = [
    "Guess",
    "GuessBasis",
    "SectionPlan",
    "file_shape",
    "guess_account",
    "plan_sections",
]
