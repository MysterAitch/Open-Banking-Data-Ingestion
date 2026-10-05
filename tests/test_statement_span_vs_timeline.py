"""Where the coverage timeline's derivation of a statement's days and `statement_span` differ.

THIS TEST DOCUMENTS; it does not judge which is right. Each invented account is built through the
real doors, the timeline is asked what its statement lane covers (`build_account_timeline`, the
public derivation as it stands), `statement_span` is asked what each statement covers, and every
statement whose first or last day differs is listed in EXPECTED. A difference that is not listed,
or a listed one that has stopped differing, fails - so switching the timeline to `statement_span`
is made knowingly, and a change to either derivation that moves a day is noticed.

The accounts, with the answer decided before the first run:

  five    the hand-worked account of test_statement_span. Two statements differ. The fourth
          (closing 04-10, opening not the second's closing): the timeline begins it at its first
          row, 04-02; `statement_span` places the missing statement's expected close at 03-10 and
          begins it 03-11. The sixth was received (06-02) before its closing day (06-10), so it
          is partial and covers to 06-02; the timeline gives every statement a complete last day.
  nilfar  statements closing 11-10, 12-10, 01-10 and then 03-10, whose opening balance equals
          01-10's closing (the statement between netted to nil). The timeline treats equal balances
          as contiguous and starts 03-10 on 01-11; `statement_span` does not, because the closing
          days are two periods apart: it expects the missing statement to have closed 02-10 and
          begins 03-10 on 02-11.
  stated  statements closing 01-10 and 03-10, the later printing "Previous balance as at" 02-10 and
          a different opening balance. The timeline knows no printed start and begins it at its
          first row, 03-02; `statement_span` begins it on 02-11, as printed.
  near    the meeting that looks one period apart (closing 12-10, 01-10 and 02-10, openings equal).
          The two agree, and it is here as the control: nothing is expected to differ.
  pair    the same with only two statements (01-10 and 02-10). With fewer than three no cadence is
          held, so `statement_span` does not infer that they meet and begins 02-10 at its first
          row, 02-04; the timeline chains it from 01-11. (First written as the control; the
          first run showed that two statements are not enough for the inference.)
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

#: (account, closing day) -> (timeline first, timeline last, span first, span last)
EXPECTED = {
    ("five", D(2026, 6, 10)): (D(2026, 5, 11), D(2026, 6, 10), D(2026, 5, 11), D(2026, 6, 2)),
    ("five", D(2026, 4, 10)): (D(2026, 4, 2), D(2026, 4, 10), D(2026, 3, 11), D(2026, 4, 10)),
    ("nilfar", D(2026, 3, 10)): (D(2026, 1, 11), D(2026, 3, 10), D(2026, 2, 11), D(2026, 3, 10)),
    ("pair", D(2026, 2, 10)): (D(2026, 1, 11), D(2026, 2, 10), D(2026, 2, 4), D(2026, 2, 10)),
    ("stated", D(2026, 3, 10)): (D(2026, 3, 2), D(2026, 3, 10), D(2026, 2, 11), D(2026, 3, 10)),
}


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


def test_TimelineAndStatementSpan_OnTheInventedAccounts_DifferExactlyWhereDocumented(tmp_path):
    with Store(tmp_path / "store.sqlite3") as store:
        _build(store, tmp_path)
        keep_statement_readings(store)
        store.connection.commit()
        spans = statement_spans(store, TODAY)
        differing = {}
        for ref in ("five", "nilfar", "stated", "near", "pair"):
            timeline = build_account_timeline(store, ref, today=TODAY)
            lanes = [lane for lane in timeline.lanes if lane.kind == STATEMENT]
            assert len(lanes) == 1
            held = {capture.last: capture for capture in lanes[0].captures}
            for span in spans[ref].statements:
                capture = held[span.closing]
                if (capture.first, capture.last) != (span.first, span.last):
                    differing[(ref, span.closing)] = (
                        capture.first,
                        capture.last,
                        span.first,
                        span.last,
                    )

    assert differing == EXPECTED
