"""The household `/gaps` is tested over, with decisions about it made through the module's doors.

`TODAY` is 2026-10-05 and nothing reads a clock. The gaps are those `fetch_gaps_world` decides;
this adds the evidence a mark is weighed against, each with its answer decided first:

  card-virgin   STATED hole 2026-06-05..2026-07-04 between statements that state their periods.
                No source lists a row inside it, and the later statement opens on the balance
                the earlier one closed on (measured: the first guess, that the chain was
                broken, was wrong), so a mark of "nothing to fetch" over exactly the hole is
                SUPPORTED with the chain joined.
  card-hole     INFERRED hole 2026-03-11..2026-04-10 (its end is the closing day the missing
                April statement is expected to have had). No row is dated in it and the chain
                joins, so a mark over all of it is SUPPORTED.
  main          The aggregator's rows begin 2026-03-15. One ask for days from 2026-01-01 came
                back empty, so "before the aggregator's history" (2026-03-14 and earlier) is
                offered. card-behind's aggregator rows begin 2026-08-02 and nothing was ever
                asked of an earlier day, so nothing is offered there.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from fetch_gaps_world import (
    TODAY,
    Loaded,
    build_household,
    feed,
    santander_statements,
)
from obdi.fetch_gaps import STATEMENT_SOURCES, fetch_report, gather_evidence
from obdi.fetch_marks import MarkSet, MarkWorld, gather_world, make_mark, read_marks, set_scope
from obdi.providers.truelayer import artefact_for
from obdi.standing_data import standings_for
from obdi.store import Store

NOW = "2026-10-05T09:00:00+00:00"


def land_empty_ask(store: Store, ref: str, asked_from: str) -> None:
    """An ask of the aggregator for days from `asked_from` that came back with no rows."""
    store.land_artefact(
        artefact_for(
            json.dumps({"results": [], "status": "Succeeded"}).encode(),
            account_id=f"provider-{ref}",
            kind="booked",
            requested=f"from={asked_from}&to=2026-10-05",
            account_ref=ref,
        )
    )


def world_of(db: Path) -> MarkWorld:
    with Store(db) as store:
        return gather_world(store)


def mark(db: Path, **fields: object):
    """A mark made through the module's door, with sensible defaults."""
    with Store(db) as store:
        world = gather_world(store)
        values: dict[str, object] = {
            "source": "",
            "first_day": None,
            "note": "",
            "review_on": None,
            "origin": "owner",
            "now": NOW,
            "today": TODAY,
            "statement_sources": STATEMENT_SOURCES,
        }
        values.update(fields)
        return make_mark(store, world, **values)  # type: ignore[arg-type]


def marks_read(db: Path) -> MarkSet:
    with Store(db) as store:
        return read_marks(store, gather_world(store), TODAY, statement_sources=STATEMENT_SOURCES)


def standings_of(db: Path):
    with Store(db) as store:
        refs = [
            str(row[0])
            for row in store.connection.execute("SELECT DISTINCT account_id FROM transactions")
        ]
        return standings_for(store, refs, families=None, movement=None)


def report_with(db: Path, marks: MarkSet | None, today: date = TODAY):
    with Store(db) as store:
        refs = [
            str(row[0])
            for row in store.connection.execute("SELECT DISTINCT account_id FROM transactions")
        ]
        standings = standings_for(store, refs, families=None, movement=None)
        return fetch_report(gather_evidence(store), standings, today, marks)


def read_at(db: Path, today: date) -> MarkSet:
    with Store(db) as store:
        return read_marks(store, gather_world(store), today, statement_sources=STATEMENT_SOURCES)


def scope(db: Path, *, months: int | None = None, first_day: date | None = None,
          account: str = "main") -> None:
    with Store(db) as store:
        set_scope(store, gather_world(store), account=account, first_day=first_day,
                  months=months, now=NOW)


def household(root: Path, *, with_reach: bool = True) -> Path:
    db, _ = build_household(root)
    if with_reach:
        with Store(db) as store:
            land_empty_ask(store, "main", "2026-01-01")
    return db


def add_feed_rows_in_virgin_hole(db: Path) -> None:
    with Store(db) as store:
        feed(
            store,
            "card-virgin",
            [
                (date(2026, 6, 20), -500, "Hole Feed Zeppelin A"),
                (date(2026, 7, 1), -600, "Hole Feed Zeppelin B"),
            ],
            digest="virgin-hole",
        )


def april_statement(db: Path, root: Path, house) -> None:
    """The statement the INFERRED hole of card-hole lacked, closing 2026-04-10."""
    with Store(db) as store:
        santander_statements(store, root, "card-hole", [date(2026, 4, 10)], house)


__all__ = ["Loaded"]
