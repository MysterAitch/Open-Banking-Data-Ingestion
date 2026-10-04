"""Where a change equals a row, every source is asked whether it lists that row in the window.

The owner's proposal: once a day is narrowed, "a search in each of the sources can look for
the difference in transactions". The explanation already names the rows a change equals; this
says, per source, whether that source lists them there, so the reader sees at once which
sources agree with which. Source names and counts only: never a figure, a date of a row, or a
recipient.

KNOWN ANSWERS, decided before the first run (the healthy household of `test_export_cuts`,
which the feed and the export both hold, with one counted payment on the 6th that the feed and
the aggregator both hold and the export does not list):

    sources            starling (the feed), starling-csv (the export), truelayer (the aggregator)
    the change         the 6th's, equal to that single payment
    the search         starling lists it, truelayer lists it, starling-csv does not (although
                       it covers the window, listing other rows there)

And for the sightings rule on its own, with the sources invented:

    a source whose sightings all fall after the window       its period does not cover the window
    a source that sighted the row two days after the window  does not list it there, lists it on
                                                             another day
    a sum of three rows, a source that sighted two of them    lists 2 of 3
"""

from __future__ import annotations

from datetime import date
from typing import ClassVar

import pytest

from obdi.balance_anchors import effective_opening
from obdi.family_anchors import families_of
from obdi.fault_explanation import SourceSearch, _SourceSearch
from test_export_cuts import HEALTHY, build
from test_export_dating import render
from test_space_attribution import AGGREGATOR, FEED, MAIN, MAP, Household, pay


@pytest.fixture
def ghosted(tmp_path):
    """The healthy household plus a payment of 12.34 on the 6th the feed and the aggregator hold."""
    store = build(
        tmp_path, HEALTHY, feed_only=[pay(MAIN, FEED, "f-ghost", -1234, 6, "Ghost")]
    )
    Household(store, MAP).arrive(pay(MAIN, AGGREGATOR, "tl-ghost", -1234, 6, "GHOST"))
    yield store
    store.close()


def only_change(store):
    opening = effective_opening(store, MAIN, families=families_of(store, MAP))
    assert opening.family is not None and opening.family.explanation is not None
    (change,) = opening.family.explanation.changes
    return change


class TestEverySourceIsAskedAboutTheRowAChangeEquals:
    def test_Search_WhenTwoSourcesListTheRowAndTheExportDoesNot_SaysSoPerSource(self, ghosted):
        change = only_change(ghosted)

        found = {s.source: (s.listed, s.of, s.elsewhere, s.covers) for s in change.searched}
        assert found == {
            "starling": (1, 1, 0, True),
            "starling-csv": (0, 1, 0, True),
            "truelayer": (1, 1, 0, True),
        }

    def test_Page_WhenTwoSourcesListTheRowAndTheExportDoesNot_SaysWhichAgreeWithWhich(
        self, ghosted
    ):
        page = render(ghosted)

        assert (
            "Searching this window in each source for the row that accounts for the change: "
            "starling lists it; starling-csv does not list it; truelayer lists it."
        ) in page

    def test_Page_NeverCarriesAFigure(self, ghosted):
        page = render(ghosted)

        assert "1234" not in page
        assert "12.34" not in page


class TestTheSightingsRuleOnItsOwn:
    SIGHTINGS: ClassVar[dict[str, dict[str, str]]] = {
        "r1": {"statement": "2026-09-12", "aggregator": "2026-09-06", "export": "2026-09-06"},
        "r2": {"statement": "2026-09-13", "aggregator": "2026-09-08"},
        "r3": {"aggregator": "2026-09-06", "export": "2026-09-06"},
        "other": {"statement": "2026-09-30", "aggregator": "2026-09-01", "export": "2026-09-01"},
    }

    def search(self, entities, after, through) -> dict[str, SourceSearch]:
        found = _SourceSearch(self.SIGHTINGS)(entities, after, through)
        return {s.source: s for s in found}

    def test_Search_WhenASourcesSightingsAllFallAfterTheWindow_ItsPeriodDoesNotCoverIt(self):
        found = self.search(["r1"], date(2026, 9, 4), date(2026, 9, 6))

        # The statement's sightings run from the 12th to the 30th.
        assert found["statement"].covers is False
        assert found["aggregator"].covers is True

    def test_Search_WhenASourceSightedTheRowOnAnotherDay_SaysItListsItElsewhere(self):
        found = self.search(["r2"], date(2026, 9, 4), date(2026, 9, 6))

        assert (found["aggregator"].listed, found["aggregator"].elsewhere) == (0, 1)

    def test_Search_WhenASumOfRowsIsPartlyListed_CountsHowManyEachSourceListed(self):
        found = self.search(["r1", "r2", "r3"], date(2026, 9, 4), date(2026, 9, 6))

        assert (found["aggregator"].listed, found["aggregator"].of) == (2, 3)
        assert (found["export"].listed, found["export"].of) == (2, 3)

    def test_Search_WhenTheWindowHasNoStart_ACoveringSourceIsStillFound(self):
        found = self.search(["r1"], None, date(2026, 9, 6))

        assert found["aggregator"].listed == 1
        assert found["statement"].covers is False
