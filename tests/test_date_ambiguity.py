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
