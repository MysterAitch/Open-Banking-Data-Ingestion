"""The filename's date range is the file import's "asked".

Every API ask records what was asked versus what landed, and a whole
machinery consumes the pair. File imports had no "asked" - coverage derived
from row dates alone - which is why "does the statement start at account
opening or first transaction?" needed a human squinting at a filename. The
claimed window closes the symmetry: a head or tail of the claim with no
rows is the bank AFFIRMING quiet, and rows outside the claim mean the
filename and the content disagree - both findings no row-derived window
can express.
"""

from __future__ import annotations

from datetime import date

from obdi.ingest import claimed_window, claimed_window_note


class TestReadingTheClaim:
    def test_AStarlingFilename_YieldsItsDayFirstWindow(self):
        window = claimed_window("StarlingStatement_17-01-2019_31-12-2019.csv")

        assert window == (date(2019, 1, 17), date(2019, 12, 31))

    def test_AnIsoNamedFile_YieldsItsWindow(self):
        window = claimed_window("export_2020-01-01_2020-06-30.csv")

        assert window == (date(2020, 1, 1), date(2020, 6, 30))

    def test_AnAmbiguousName_PrefersTheReadingContainingTheRows(self):
        # 09-08 could be 9 Aug or 8 Sep in both tokens. The rows know.
        window = claimed_window(
            "StarlingStatement_09-08-2025_09-08-2026.csv",
            rows_from=date(2025, 8, 20),
            rows_to=date(2026, 8, 1),
        )

        assert window == (date(2025, 8, 9), date(2026, 8, 9))

    def test_ANameWithNoDates_ClaimsNothing(self):
        assert claimed_window("statement.csv") is None
        assert claimed_window("mine.qif") is None

    def test_AnUnorderableReading_IsDiscarded(self):
        # Day-first reads as 31 Dec .. 17 Jan (backwards) - the month-first
        # reading fails outright (month 31). Nothing defensible remains.
        assert claimed_window("Statement_31-12-2019_17-01-2019.csv") is None


class TestTheClaimNote:
    def test_QuietHeadAndTail_AreAffirmedByTheDocument(self):
        note = claimed_window_note(
            "StarlingStatement_17-01-2019_31-12-2019.csv",
            earliest=date(2019, 1, 20),
            latest=date(2019, 12, 29),
        )

        assert note is not None
        assert "claims 2019-01-17 .. 2019-12-31" in note
        assert "first 3 day(s)" in note
        assert "last 2 day(s)" in note
        assert "affirmed quiet by the document" in note

    def test_RowsOutsideTheClaim_AreADisagreementNotAQuietNote(self):
        note = claimed_window_note(
            "StarlingStatement_01-02-2019_28-02-2019.csv",
            earliest=date(2019, 1, 25),
            latest=date(2019, 2, 20),
        )

        assert note is not None
        assert "OUTSIDE the claimed window" in note

    def test_AClaimMatchingTheRowsExactly_IsStatedPlainly(self):
        note = claimed_window_note(
            "StarlingStatement_01-02-2019_28-02-2019.csv",
            earliest=date(2019, 2, 1),
            latest=date(2019, 2, 28),
        )

        assert note is not None
        assert "claims 2019-02-01 .. 2019-02-28" in note
        assert "affirmed quiet" not in note

    def test_NoClaim_NoNote(self):
        assert claimed_window_note("statement.csv", earliest=None, latest=None) is None
