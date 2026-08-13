"""The rebuild history's figures say which quantity they are.

The panel showed `43,015 records -> 46,640` under a column headed "volume". An
arrow between two numbers reads as a transformation, so it reads as "43,015
records became 46,640 transactions" - and on the live instance the store held
8,760 transactions, with 17,219 sightings behind them. The figure is neither: it
counts a ROW RESOLUTION per record processed, so a payment reported by five
overlapping fetches is counted five times, and it tracks records_total to within
about eight per cent.

Nobody noticed for as long as the panel existed because no two of those three
numbers were ever on screen together. They came together on 2026-08-13 only
because an incident forced the comparison.

Both figures are honest measures of REPLAY WORK. Neither is a holding. The
panel now says so, which is the whole fix - the arithmetic was never wrong.
"""

from __future__ import annotations

import pytest

from obdi.web import _rebuild_history_html

RUN = {
    "ok": True,
    "started_at": "2026-08-13T13:33:18Z",
    "finished_at": "2026-08-13T13:33:26Z",
    "records_total": 43015,
    "transactions": 46640,
    "build": "0.4.227+677b63ab39e4",
}


def _panel(**overrides) -> str:
    return _rebuild_history_html(lambda: [{**RUN, **overrides}])


class TestWhatTheFiguresClaim:
    def test_NeitherFigure_IsPresentedAsWhatTheStoreHolds(self):
        """The arrow was the whole misreading, so the arrow goes.

        `A -> B` between two counts asserts that A became B. Here A is records
        read and B is resolutions performed, and B exceeds A - which is only
        sensible once both are named as work.
        """
        panel = _panel()

        assert "43,015 -> 46,640" not in panel
        assert "records -> " not in panel, (
            "an arrow between the two figures reads as a transformation into "
            "transactions, which is what made this number look like a holding"
        )

    def test_EachFigure_IsNamedForWhatItCounts(self):
        panel = _panel()

        assert "43,015 records read" in panel
        assert "46,640 row resolutions" in panel

    def test_ThePanel_SaysTheseAreWorkAndNotHoldings(self):
        """The sentence a reader needs, beside the numbers rather than in a
        note somewhere else - the comparison that misled is made ON this
        table."""
        panel = _panel().lower()

        assert "not what the store holds" in panel

    def test_ARunThatRecordedNoFigures_SaysNothingRatherThanZero(self):
        """A failed rebuild stores no counts. Rendering those as `0 records
        read` would claim the replay read nothing, which is a different and
        stronger statement than "it did not get far enough to say" - and it is
        exactly the row a person reads after an incident.
        """
        panel = _panel(ok=False, records_total=None, transactions=None)

        assert "records read" not in panel
        assert "0 records" not in panel
        assert "failed" in panel

    def test_RecordsWithoutResolutions_NamesOnlyWhatItHas(self):
        """A run cut off mid-replay has one figure and not the other. Naming
        the one it has beats suppressing both."""
        panel = _panel(transactions=None)

        assert "43,015 records read" in panel
        assert "row resolutions" not in panel


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
