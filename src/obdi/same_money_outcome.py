"""What the same-money rule concluded at each statement closing, in dates and counts.

`same_money_fold` decides which feed rows are the same money as a statement's
rows, and says nothing about WHY it folded nothing at a closing. This is that
account of itself: one outcome per closing per feed, built by the same code path
that makes the decision (never a second derivation of it beside the page), kept
by the pass that ran it, and rendered by the statement-periods page.

An outcome holds dates, counts, and source names only. It holds no amount and no
description, so the masked page can show it.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date
from enum import StrEnum

from .plural import plural

#: How many entries a masked list shows before saying how many more there are,
#: so a long run of dates stays readable on a phone.
LIST_CAP = 20


def dated_list(items: Iterable[object]) -> str:
    """The items, comma separated, at most `LIST_CAP` and then "and N more"."""
    shown = [str(item) for item in items]
    text = ", ".join(shown[:LIST_CAP])
    if len(shown) > LIST_CAP:
        text += f", and {len(shown) - LIST_CAP} more"
    return text


def _with_excuses(dates: Sequence[date], excuses: Sequence[str]) -> str:
    """Each date, followed by why that row is excused where it is. A record
    written before excuses were kept has none, and its dates stand alone."""
    return dated_list(
        f"{day} [excused: {why}]" if why else day
        for day, why in zip(dates, [*excuses, *[""] * len(dates)], strict=False)
    )


class Verdict(StrEnum):
    """What the rule concluded at one statement closing."""

    #: The proof accepted a candidate: its feed rows are folded.
    FOLDED = "folded"
    #: The period's rows already sum to the statement's movement.
    AGREES = "agrees"
    #: The period differs, and no unmatched feed row the rule may fold is dated
    #: in it (a confirmed transfer leg and a row a statement lists never are).
    NO_FEED_ROWS = "no-feed-rows"
    #: The period differs and holds feed rows, but no statement-only row: nothing
    #: a statement itemises for them to be the same money as.
    NO_STATEMENT_ROWS = "no-statement-rows"
    #: No subset of the period's feed rows sums to the period's difference and to
    #: some subset of its statement-only rows.
    NO_MATCH = "no-match"
    #: A candidate was found and the proof refused it.
    REFUSED = "refused"


@dataclass(frozen=True)
class ClosingOutcome:
    """The rule's account of one closing against one feed."""

    closing: date
    feed: str
    verdict: Verdict
    #: The period's first and last day.
    period: tuple[date, date]
    #: Every unmatched feed row dated in the period that the rule may fold.
    feed_dates: tuple[date, ...]
    #: How many of them were combined into sets of more than one row, when bounded.
    feed_searched: int
    #: Every statement-only row of the period the rule may search, in date order.
    statement_dates: tuple[date, ...]
    #: How many of them the search took (the nearest the closing), when bounded.
    statement_searched: int
    #: The rows taken as a candidate (folded, or refused).
    taken_dates: tuple[date, ...] = ()
    #: For a refused candidate, the periods (first and last day) that would
    #: still differ after the fold, the blocking period among them.
    blocking: tuple[tuple[date, date], ...] = ()
    #: Why each of `statement_dates` is excused, in the same order, "" for one
    #: that is not.
    statement_excuses: tuple[str, ...] = ()
    #: The statement-only rows a candidate's sum was made from (date order), and
    #: why each is excused, "" for one that is not.
    matched_dates: tuple[date, ...] = ()
    matched_excuses: tuple[str, ...] = ()

    @property
    def bounds(self) -> tuple[str, ...]:
        reached = []
        if len(self.statement_dates) > self.statement_searched:
            reached.append(
                f"only the nearest {self.statement_searched} of {len(self.statement_dates)} "
                "statement-only rows were searched"
            )
        if len(self.feed_dates) > self.feed_searched:
            reached.append(
                f"each unmatched feed row was tried alone, but only the latest "
                f"{self.feed_searched} of {len(self.feed_dates)} were combined into sets"
            )
        return tuple(reached)

    def describe(self) -> str:
        first, last = self.period
        period = f"the period {first} to {last}"
        statement = (
            f"{plural(len(self.statement_dates), 'statement-only row')} "
            f"(dated {_with_excuses(self.statement_dates, self.statement_excuses)})"
            if self.statement_dates
            else "no statement-only row"
        )
        if self.verdict is Verdict.FOLDED:
            matched = (
                f"{plural(len(self.matched_dates), 'statement-only row')} "
                f"(dated {_with_excuses(self.matched_dates, self.matched_excuses)})"
                if self.matched_dates
                else "statement-only rows"
            )
            text = (
                f"the rule folds {plural(len(self.taken_dates), 'feed row')} from "
                f"{self.feed} (dated {dated_list(self.taken_dates)}) as the same money as "
                f"{matched}, and {period} then agrees."
            )
        elif self.verdict is Verdict.AGREES:
            text = f"{period} already agrees with the statement, so there is nothing to fold."
        elif self.verdict is Verdict.NO_FEED_ROWS:
            text = (
                f"{period} holds no unmatched feed row from {self.feed} that the rule may "
                "fold, so there is nothing to fold."
            )
        elif self.verdict is Verdict.NO_STATEMENT_ROWS:
            text = (
                f"{period} holds {plural(len(self.feed_dates), 'unmatched feed row')} from "
                f"{self.feed} (dated {dated_list(self.feed_dates)}) but no statement-only "
                "row, so none of them can be the same money as a statement row."
            )
        elif self.verdict is Verdict.NO_MATCH:
            text = (
                f"{period} holds {plural(len(self.feed_dates), 'unmatched feed row')} from "
                f"{self.feed} (dated {dated_list(self.feed_dates)}) and {statement}: no subset "
                "of the feed rows sums to the period's difference and to some subset of the "
                "statement-only rows."
            )
        else:
            periods = " and ".join(f"{a} to {b}" for a, b in self.blocking)
            text = (
                f"a candidate was found ({plural(len(self.taken_dates), 'feed row')} from "
                f"{self.feed} dated {dated_list(self.taken_dates)}, summing to the difference "
                f"of {period} and to some of {statement}) but the statements' own arithmetic "
                f"refused it: {'the period' if len(self.blocking) == 1 else 'the periods'} "
                f"{periods} would still differ after folding."
            )
        if self.verdict in (Verdict.NO_MATCH, Verdict.REFUSED):
            for bound in self.bounds:
                text += f" The search was bounded: {bound}."
        return f"Closing {self.closing}: {text}"

    def to_json(self) -> dict[str, object]:
        return {
            "closing": self.closing.isoformat(),
            "feed": self.feed,
            "verdict": self.verdict.value,
            "period": [day.isoformat() for day in self.period],
            "feed_dates": [day.isoformat() for day in self.feed_dates],
            "feed_searched": self.feed_searched,
            "statement_dates": [day.isoformat() for day in self.statement_dates],
            "statement_searched": self.statement_searched,
            "taken_dates": [day.isoformat() for day in self.taken_dates],
            "blocking": [[a.isoformat(), b.isoformat()] for a, b in self.blocking],
            "statement_excuses": list(self.statement_excuses),
            "matched_dates": [day.isoformat() for day in self.matched_dates],
            "matched_excuses": list(self.matched_excuses),
        }

    @staticmethod
    def from_json(found: dict[str, object]) -> ClosingOutcome:
        def days(key: str) -> tuple[date, ...]:
            raw = found[key]
            if not isinstance(raw, list):
                raise TypeError(f"{key} is not a list")
            return tuple(date.fromisoformat(str(day)) for day in raw)

        period = days("period")
        blocking_raw = found["blocking"]
        if not isinstance(blocking_raw, list):
            raise TypeError("blocking is not a list")
        def texts(key: str) -> tuple[str, ...]:
            raw = found.get(key, [])
            if not isinstance(raw, list):
                raise TypeError(f"{key} is not a list")
            return tuple(str(item) for item in raw)

        def optional_days(key: str) -> tuple[date, ...]:
            return days(key) if key in found else ()

        return ClosingOutcome(
            closing=date.fromisoformat(str(found["closing"])),
            feed=str(found["feed"]),
            verdict=Verdict(str(found["verdict"])),
            period=(period[0], period[1]),
            feed_dates=days("feed_dates"),
            feed_searched=int(str(found["feed_searched"])),
            statement_dates=days("statement_dates"),
            statement_searched=int(str(found["statement_searched"])),
            taken_dates=days("taken_dates"),
            blocking=tuple(
                (date.fromisoformat(str(a)), date.fromisoformat(str(b))) for a, b in blocking_raw
            ),
            statement_excuses=texts("statement_excuses"),
            matched_dates=optional_days("matched_dates"),
            matched_excuses=texts("matched_excuses"),
        )


@dataclass(frozen=True)
class AccountOutcome:
    """What the rule did for one account: why it did not run, or each closing."""

    account: str
    #: The sources other than the statements that held rows for the account when
    #: the rule ran, so a reader can tell a record that predates a feed.
    feeds: tuple[str, ...] = ()
    #: Why the rule did not run for the account, or "".
    withheld: str = ""
    #: Feeds that share no days with the statements, so nothing was paired.
    unpaired: tuple[str, ...] = ()
    closings: tuple[ClosingOutcome, ...] = ()

    @property
    def folds(self) -> int:
        """How many feed rows the rule folds for the account."""
        return sum(
            len(closing.taken_dates)
            for closing in self.closings
            if closing.verdict is Verdict.FOLDED
        )

    def describe(self) -> list[str]:
        if self.withheld:
            return [f"The same-money rule did not run: {self.withheld}"]
        lines = [
            f"The feed {feed} shares no days with the statements, so nothing was paired "
            "and the same-money rule had nothing to test."
            for feed in self.unpaired
        ]
        if not self.closings and not self.unpaired:
            lines.append(
                "The same-money rule had no period between two statement closings to test."
            )
        lines.extend(closing.describe() for closing in self.closings)
        return lines

    def to_text(self) -> str:
        return json.dumps(
            {
                "account": self.account,
                "feeds": list(self.feeds),
                "withheld": self.withheld,
                "unpaired": list(self.unpaired),
                "closings": [closing.to_json() for closing in self.closings],
            }
        )

    @staticmethod
    def from_text(text: str) -> AccountOutcome:
        """The outcome `to_text` wrote. Refuses text that is not one, with
        ValueError, KeyError, or TypeError."""
        found = json.loads(text)
        return AccountOutcome(
            account=str(found["account"]),
            feeds=tuple(str(feed) for feed in found["feeds"]),
            withheld=str(found["withheld"]),
            unpaired=tuple(str(feed) for feed in found["unpaired"]),
            closings=tuple(ClosingOutcome.from_json(closing) for closing in found["closings"]),
        )
