"""A file whose dates are all 1-12 cannot confirm its own format.

Every file parser PINS its date format rather than guessing, and that is the
main defence: strptime rejects 03/15/2026 under %d/%m/%Y outright, so an export
in the wrong convention normally fails at the first day above the twelfth.

Normally. A short statement whose every date happens to fall in the first
twelve days of a month passes cleanly under either reading, and is silently
wrong under one of them. Nothing in the file can settle it - which is precisely
when someone should be told rather than reassured.
"""

from __future__ import annotations

from datetime import date

from obdi.ingest import dates_cannot_confirm_format


class TestWhenAFileCannotVouchForItsOwnDates:
    def test_Import_WhenEveryDayIsTwelveOrLower_ReportsTheFormatAsUnconfirmable(self):
        assert dates_cannot_confirm_format([date(2026, 3, 5), date(2026, 4, 11), date(2026, 5, 2)])

    def test_Import_WhenAnyDayExceedsTwelve_TheFormatIsProvenByTheData(self):
        # A single day-13 reading correctly under the pinned format rules out
        # the transposed interpretation for the whole file.
        assert not dates_cannot_confirm_format([date(2026, 3, 5), date(2026, 4, 13)])

    def test_Import_WhenThereAreNoDates_MakesNoClaimEitherWay(self):
        assert not dates_cannot_confirm_format([])


class TestTheWarningActuallyReachesTheUser:
    """The helper being right is worthless if the wiring never fires.

    These go through import_file - the user-recognisable scenario - so deleting
    or breaking the warning glue in ingest.py fails a test, not just an intent.
    """

    def _csv(self, *dates: str) -> bytes:
        rows = "\n".join(f"{d},SHOP,ref,PAYMENT,-4.50,100.00" for d in dates)
        return (
            "Date,Counter Party,Reference,Type,Amount (GBP),Balance (GBP)\n" + rows + "\n"
        ).encode()

    def test_Import_WhenEveryDateIsAmbiguous_WarnsOnStderr(self, tmp_path, capsys):
        from obdi.ingest import import_file
        from obdi.store import Store

        statement = tmp_path / "ambiguous.csv"
        statement.write_bytes(self._csv("05/03/2026", "11/04/2026"))

        with Store(tmp_path / "s.sqlite3") as store:
            import_file(store, statement, account_id="current")

        err = capsys.readouterr().err
        assert "WARNING" in err
        assert "ambiguous.csv" in err

    def test_Import_WhenADateProvesTheFormat_StaysQuiet(self, tmp_path, capsys):
        from obdi.ingest import import_file
        from obdi.store import Store

        statement = tmp_path / "provable.csv"
        statement.write_bytes(self._csv("05/03/2026", "27/04/2026"))

        with Store(tmp_path / "s.sqlite3") as store:
            import_file(store, statement, account_id="current")

        assert "WARNING" not in capsys.readouterr().err
