"""The coverage timeline's statement lane draws exactly what `statement_span` says.

This began as a record of where the timeline's own derivation of a statement's days differed from
`statement_span` over five invented accounts (a partial last day ending where the statement was
produced or received; equal balances across a doubled interval being a probable net-nil hole
and not contiguous; unequal balances a stated hole with an inferred end; a printed start winning
over balances; nothing guessed from two statements; the cadence the lower median). The timeline
now reads `statement_span` through `coverage_timeline.capture_of` and derives nothing, so the
differences are gone, and this holds that: for every statement of every account, the days the
lane covers are the days `statement_span` gives, and a second derivation growing in the timeline
would show here as a difference.

The accounts are the five the record was made over, built through the real doors:

  five    the hand-worked account of `test_statement_span`
  nilfar  statements closing 11-10, 12-10, 01-10 and then 03-10 whose opening equals 01-10's closing
  stated  statements closing 01-10 and 03-10, the later printing the day the earlier closed
  near    closings 12-10, 01-10 and 02-10 with equal balances: the control
  pair    only two statements, 01-10 and 02-10: no cadence, so no meeting is inferred
"""

from __future__ import annotations

from datetime import date

from obdi.coverage_timeline import STATEMENT, build_account_timeline
from obdi.statement_span import statement_spans
from obdi.statement_terms import keep_statement_readings
from obdi.store import Store
from statement_span_world import Spend, statement
from test_statement_span import _five_and_a_half

D = date
TODAY = D(2026, 6, 20)
ACCOUNTS = ("five", "nilfar", "stated", "near", "pair")


def _held(store, root, ref: str, closings, owed: int, *, as_at=None):
    for index, closing in enumerate(closings):
        owed = statement(
            store,
            root,
            ref,
            closing,
            owed,
            [Spend(D(closing.year, closing.month, 4), f"{ref} shop {index}", 1000)],
            received=closing.replace(day=11),
            previous_close=None if as_at is None else as_at.get(closing),
        )
    return owed


def _build(store, root):
    _five_and_a_half(store, root)
    owed = _held(store, root, "nilfar", [D(2025, 11, 10), D(2025, 12, 10), D(2026, 1, 10)], 6000)
    statement(
        store,
        root,
        "nilfar",
        D(2026, 3, 10),
        owed,
        [Spend(D(2026, 3, 2), "nilfar later", 1000)],
        received=D(2026, 3, 11),
    )
    _held(store, root, "stated", [D(2026, 1, 10)], 5000)
    statement(
        store,
        root,
        "stated",
        D(2026, 3, 10),
        9999,
        [Spend(D(2026, 3, 2), "stated later", 1000)],
        received=D(2026, 3, 11),
        previous_close=D(2026, 2, 10),
    )
    _held(store, root, "near", [D(2025, 12, 10), D(2026, 1, 10), D(2026, 2, 10)], 5000)
    _held(store, root, "pair", [D(2026, 1, 10), D(2026, 2, 10)], 5000)


def test_StatementLane_OnTheInventedAccounts_CoversExactlyTheDaysStatementSpanGives(tmp_path):
    checked = 0
    with Store(tmp_path / "store.sqlite3") as store:
        _build(store, tmp_path)
        keep_statement_readings(store)
        store.connection.commit()
        spans = statement_spans(store, TODAY)
        for ref in ACCOUNTS:
            timeline = build_account_timeline(store, ref, today=TODAY, spans=spans[ref])
            lanes = [lane for lane in timeline.lanes if lane.kind == STATEMENT]
            assert len(lanes) == 1, ref
            captures = sorted(lanes[0].captures, key=lambda c: (c.last, c.first))
            held = sorted(spans[ref].statements, key=lambda s: s.closing)
            assert len(captures) == len(held), ref
            for capture, span in zip(captures, held, strict=True):
                assert (capture.first, capture.last) == (span.first or span.last, span.last), (
                    ref, span.closing
                )
                checked += 1
    assert checked >= 15


def test_StatementLane_WhenTheTimelineAsksTheStoreItself_GivesTheSameAnswer(tmp_path):
    with Store(tmp_path / "store.sqlite3") as store:
        _build(store, tmp_path)
        keep_statement_readings(store)
        store.connection.commit()
        given = build_account_timeline(
            store, "five", today=TODAY, spans=statement_spans(store, TODAY)["five"]
        )
        asked = build_account_timeline(store, "five", today=TODAY)
    assert [lane.captures for lane in asked.lanes] == [lane.captures for lane in given.lanes]
