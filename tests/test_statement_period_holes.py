"""A hole between statements is found from their stated periods where they give no balance.

The real case, in dates alone: an account fed only by two "all accounts" documents whose
assigned sections state the periods

    S1  2022-05-04 to 2023-03-27
    S2  2025-03-11 to 2026-08-10

and no closing balance (the sections yield transactions but state none), so no known balance
exists for the account and the balance chain that finds holes has nothing to chain. The known
answer, written before the first run: the days between are 2023-03-28 to 2025-03-10, a hole
stated by the second statement's own printed start; Bring in lists "a statement covering" them,
the account's verdict stays "nothing to check against", and where neither periods nor balances
exist nothing is found.

The sections are held by planting what a reader returns for an assigned section
(`assigned_sections`), because the one issuer layout built here (`credit_union_documents`) always
prints a closing balance and a section without one is refused by it; everything downstream of the
reading is the application's own.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from obdi.fetch_gaps import GapKind, fetch_report, gather_evidence
from obdi.parsers.pdf_statements import SectionReading
from obdi.parsers.statement_reading import StatementReading, StatementRow
from obdi.statement_span import HoleReason, Known, statement_spans
from obdi.statement_terms import statement_periods
from obdi.store import SectionAssignment, Store
from statement_span_world import Spend, feed

D = date
ACCOUNT = "credit-union-saver"
TODAY = D(2026, 9, 1)


def reading(start: date | None, end: date, *, closing: int | None = None) -> SectionReading:
    body = StatementReading(
        statement_date=end,
        closing_balance_minor=closing,
        transactions=[StatementRow(start or end, "A PAYEE", -100)],
        period_start=start,
    )
    return SectionReading("saver", "Saver", body, "")


def planted(monkeypatch: pytest.MonkeyPatch, *sections: SectionReading) -> None:
    def held(store: Store, account_ref: str | None = None):
        for number, found in enumerate(sections):
            assignment = SectionAssignment(f"digest-{number}", "saver", ACCOUNT, "Saver", "")
            if account_ref is None or account_ref == ACCOUNT:
                yield assignment, found

    monkeypatch.setattr("obdi.statement_terms.assigned_sections", held)


@pytest.fixture
def store(tmp_path: Path) -> Store:
    with Store(tmp_path / "s.sqlite3") as opened:
        feed(opened, ACCOUNT, [Spend(D(2026, 8, 1), "Feed row", 100)], digest="feed")
        yield opened


S1 = (D(2022, 5, 4), D(2023, 3, 27))
S2 = (D(2025, 3, 11), D(2026, 8, 10))


class TestTwoSectionedStatementsWithNoBalances:
    def test_Periods_AreOfferedFromTheStatedPeriodsAlone(self, store, monkeypatch):
        planted(monkeypatch, reading(*S1), reading(*S2))

        periods = sorted(statement_periods(store), key=lambda p: p.closing)

        assert [(p.opens, p.closing) for p in periods] == [S1, S2]
        assert all(p.closing_minor is None and p.opening_minor is None for p in periods)

    def test_TheHole_IsStatedFromTheSecondStatementsOwnStart(self, store, monkeypatch):
        planted(monkeypatch, reading(*S1), reading(*S2))

        [hole] = statement_spans(store, TODAY)[ACCOUNT].holes

        assert (hole.first_day, hole.last_day) == (D(2023, 3, 28), D(2025, 3, 10))
        assert hole.known is Known.STATED and hole.reason is HoleReason.STARTS_AFTER

    def test_BringIn_ListsAStatementCoveringThoseDaysWithTheReason(self, store, monkeypatch):
        planted(monkeypatch, reading(*S1), reading(*S2))

        report = fetch_report(gather_evidence(store), {}, TODAY)

        [gap] = [g for g in report.gaps if g.kind is GapKind.HOLE_BETWEEN]
        assert (gap.account, gap.first_day, gap.last_day) == (
            ACCOUNT,
            D(2023, 3, 28),
            D(2025, 3, 10),
        )
        assert "nothing known covers the days between them" in gap.why

    def test_TwoStatementsThatMeet_FindNoHole(self, store, monkeypatch):
        planted(
            monkeypatch,
            reading(D(2022, 5, 4), D(2023, 3, 27)),
            reading(D(2023, 3, 28), D(2026, 8, 10)),
        )

        assert statement_spans(store, TODAY)[ACCOUNT].holes == ()

    def test_ASectionWithNoStatedPeriod_AndNoBalance_ChangesNothing(self, store, monkeypatch):
        planted(monkeypatch, reading(None, S1[1]), reading(None, S2[1]))

        assert statement_periods(store) == []

    def test_ASectionThatStatesABalance_IsOfferedOnceFromItsBalance_WithItsOwnPeriodAndRows(
        self, store, monkeypatch
    ):
        """Offered once, from its balance - and with the period and rows the section states.
        Read from the whole document's kept reading, which a divided document has none of, a
        section's period was None and its rows unknown, so each loan section on the real store
        was drawn on its closing day alone and the statements lane broke at every turn of the
        year between one document's last payment and the next's first."""
        planted(monkeypatch, reading(*S1, closing=5000))

        periods = statement_periods(store)

        assert [(p.opens, p.closing, p.closing_minor) for p in periods] == [(S1[0], S1[1], 5000)]
        assert [(p.first_row, p.last_row) for p in periods] == [(S1[0], S1[0])]

    def test_ARefusedSection_IsNotOffered(self, store, monkeypatch):
        refused = reading(*S1)
        planted(monkeypatch, SectionReading(refused.key, refused.label, refused.reading, "no"))

        assert statement_periods(store) == []
