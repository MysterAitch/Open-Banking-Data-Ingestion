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
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from enum import StrEnum

#: How many entries a masked list shows before saying how many more there are,
#: so a long run of dates stays readable on a phone.
LIST_CAP = 20


def plural(count: int, singular: str, plural_form: str | None = None) -> str:
    return f"{count} {singular if count == 1 else plural_form or singular + 's'}"


def dated_list(items: Iterable[object]) -> str:
    """The items, comma separated, at most `LIST_CAP` and then "and N more"."""
    shown = [str(item) for item in items]
    text = ", ".join(shown[:LIST_CAP])
    if len(shown) > LIST_CAP:
        text += f", and {len(shown) - LIST_CAP} more"
    return text


class Verdict(StrEnum):
    """What the rule concluded at one statement closing."""

    #: The proof accepted a candidate: its feed rows are folded.
    FOLDED = "folded"
    #: No unclaimed unmatched feed row was dated within the band of the closing.
    NO_BAND_ROWS = "no-band-rows"
    #: Band rows existed, but no subset of them summed to any subset of the
    #: period's statement-only rows (which may be none).
    NO_MATCH = "no-match"
    #: A candidate was found and the proof refused it.
    REFUSED = "refused"


@dataclass(frozen=True)
class ClosingOutcome:
    """The rule's account of one closing against one feed."""

    closing: date
    feed: str
    verdict: Verdict
    #: The band's first and last day: the closing's day either side by the boundary.
    band: tuple[date, date]
    #: Every unclaimed unmatched feed row dated in the band.
    band_dates: tuple[date, ...]
    #: How many of them the search took (the nearest), when bounded.
    band_searched: int
    #: Band rows an earlier closing had already claimed.
    claimed_dates: tuple[date, ...]
    #: Every statement-only row of the period.
    statement_dates: tuple[date, ...]
    #: How many of them the search took (the nearest), when bounded.
    statement_searched: int
    #: The rows taken as a candidate (folded, or refused).
    taken_dates: tuple[date, ...] = ()
    #: For a refused candidate, the periods (first and last day) that would
    #: still differ after the fold, the blocking period among them.
    blocking: tuple[tuple[date, date], ...] = ()

    @property
    def bounds(self) -> tuple[str, ...]:
        reached = []
        if len(self.statement_dates) > self.statement_searched:
            reached.append(
                f"only the nearest {self.statement_searched} of {len(self.statement_dates)} "
                "statement-only rows were searched"
            )
        if len(self.band_dates) > self.band_searched:
            reached.append(
                f"only the nearest {self.band_searched} of {len(self.band_dates)} "
                "unmatched feed rows in the band were searched"
            )
        return tuple(reached)

    def describe(self) -> str:
        first, last = self.band
        band = f"the band {first} to {last}"
        statement = (
            f"{plural(len(self.statement_dates), 'statement-only row')} in the period "
            f"(dated {dated_list(self.statement_dates)})"
            if self.statement_dates
            else "no statement-only row in the period"
        )
        if self.verdict is Verdict.FOLDED:
            text = (
                f"the rule folds {plural(len(self.taken_dates), 'feed row')} from "
                f"{self.feed} (dated {dated_list(self.taken_dates)}) as the same money as "
                "statement-only rows."
            )
        elif self.verdict is Verdict.NO_BAND_ROWS:
            text = (
                f"no unmatched feed row from {self.feed} within {band}, so there is "
                "nothing to fold."
            )
        elif self.verdict is Verdict.NO_MATCH:
            text = (
                f"{plural(len(self.band_dates), 'unmatched feed row')} from {self.feed} in "
                f"{band} (dated {dated_list(self.band_dates)}), and {statement}: no subset of "
                "the one sums to any subset of the other."
            )
        else:
            periods = " and ".join(f"{a} to {b}" for a, b in self.blocking)
            text = (
                f"a candidate was found ({plural(len(self.taken_dates), 'feed row')} from "
                f"{self.feed} dated {dated_list(self.taken_dates)}, summing to some of "
                f"{statement}) but the statements' own arithmetic refused it: "
                f"{'the period' if len(self.blocking) == 1 else 'the periods'} {periods} "
                "would still differ after folding."
            )
        if self.claimed_dates:
            text += (
                f" {plural(len(self.claimed_dates), 'feed row')} dated "
                f"{dated_list(self.claimed_dates)} in the band had been claimed by an "
                "earlier closing."
            )
        for bound in self.bounds:
            text += f" The search was bounded: {bound}."
        return f"Closing {self.closing}: {text}"

    def to_json(self) -> dict[str, object]:
        return {
            "closing": self.closing.isoformat(),
            "feed": self.feed,
            "verdict": self.verdict.value,
            "band": [day.isoformat() for day in self.band],
            "band_dates": [day.isoformat() for day in self.band_dates],
            "band_searched": self.band_searched,
            "claimed_dates": [day.isoformat() for day in self.claimed_dates],
            "statement_dates": [day.isoformat() for day in self.statement_dates],
            "statement_searched": self.statement_searched,
            "taken_dates": [day.isoformat() for day in self.taken_dates],
            "blocking": [[a.isoformat(), b.isoformat()] for a, b in self.blocking],
        }

    @staticmethod
    def from_json(found: dict[str, object]) -> ClosingOutcome:
        def days(key: str) -> tuple[date, ...]:
            raw = found[key]
            if not isinstance(raw, list):
                raise TypeError(f"{key} is not a list")
            return tuple(date.fromisoformat(str(day)) for day in raw)

        band = days("band")
        blocking_raw = found["blocking"]
        if not isinstance(blocking_raw, list):
            raise TypeError("blocking is not a list")
        return ClosingOutcome(
            closing=date.fromisoformat(str(found["closing"])),
            feed=str(found["feed"]),
            verdict=Verdict(str(found["verdict"])),
            band=(band[0], band[1]),
            band_dates=days("band_dates"),
            band_searched=int(str(found["band_searched"])),
            claimed_dates=days("claimed_dates"),
            statement_dates=days("statement_dates"),
            statement_searched=int(str(found["statement_searched"])),
            taken_dates=days("taken_dates"),
            blocking=tuple(
                (date.fromisoformat(str(a)), date.fromisoformat(str(b))) for a, b in blocking_raw
            ),
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
