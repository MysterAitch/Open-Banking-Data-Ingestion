"""The page snapshot says which pages changed, and nothing about the parts that always change.

KNOWN ANSWERS: walks are planted by hand, so each case has a decided answer before it runs. A
page whose footer, instant, or age differs is the same page; a page whose figure, word, or link
differs is not. A page that vanished, appeared, or was served by a different number of routes is
named, because a move that loses the route reader reads no routes and would otherwise compare
nothing against nothing.
"""

from __future__ import annotations

import pytest

from page_snapshot import differences, snapshot, split_stylesheet, stabilised

PAGE = (
    "<html><head><style>body{color:red}</style></head><body><h1>Today</h1>"
    "<p>1 account adds up</p><footer>build 0.4.357</footer></body></html>"
)


ACCOUNTS = "<p>accounts</p>"


def taken(walks=None, *, routes: int = 3):
    walks = walks or {"main": {"/": PAGE, "/accounts": ACCOUNTS}}
    return snapshot(walks, route_count=routes)


class TestWhatCountsAsADifferentPage:
    def test_Compare_WhenNothingChanged_ListsNothing(self):
        assert differences(taken(), taken()) == []

    def test_Compare_WhenOnlyTheFooterBuildChanged_ListsNothing(self):
        later = PAGE.replace("0.4.357", "0.4.358")

        assert differences(taken(), taken({"main": {"/": later, "/accounts": ACCOUNTS}})) == []

    def test_Compare_WhenAnInstantAndAnAgeChanged_ListsNothing(self):
        before = {"main": {"/": "<p>kept 2026-10-04 15:24 (98 days ago), ran at 13:09</p>"}}
        after = {"main": {"/": "<p>kept 2026-10-07 09:01 (101 days ago), ran at 13:10</p>"}}

        assert differences(taken(before), taken(after)) == []

    def test_Compare_WhenTheFetchTimelinePanLinkCarriesAnotherMoment_ListsNothing(self):
        link = '<a href="/fetch-timeline?days=7&until={}">'
        before = {"main": {"/": link.format("2026-10-04T00:04:24.802116Z")}}
        after = {"main": {"/": link.format("2026-10-07T09:10:11.000001Z")}}

        assert differences(taken(before), taken(after)) == []

    def test_Compare_WhenAWordOnOnePageChanged_NamesThatPageOnly(self):
        changed = {"main": {"/": PAGE.replace("adds up", "does not add up"), "/accounts": ACCOUNTS}}

        assert differences(taken(), taken(changed)) == ["differs: main:/"]

    def test_Compare_WhenOneDigitOfAFigureChanged_NamesThatPage(self):
        before = {"main": {"/": "<p>2 of 5 accounts</p>"}}
        after = {"main": {"/": "<p>2 of 6 accounts</p>"}}

        assert differences(taken(before), taken(after)) == ["differs: main:/"]

    def test_Compare_WhenTheSameUrlIsOverTwoStores_EachStoreIsItsOwnPage(self):
        both = {"main": {"/": "<p>a</p>"}, "household": {"/": "<p>b</p>"}}
        changed = {"main": {"/": "<p>a</p>"}, "household": {"/": "<p>c</p>"}}

        assert differences(taken(both), taken(changed)) == ["differs: household:/"]


class TestWhatCountsAsAMissingOrNewPage:
    def test_Compare_WhenAPageIsNoLongerServed_NamesItAsMissing(self):
        after = {"main": {"/": PAGE}}

        assert differences(taken(), taken(after)) == ["missing: main:/accounts"]

    def test_Compare_WhenAPageAppears_NamesItAsNew(self):
        after = {"main": {"/": PAGE, "/accounts": "<p>accounts</p>", "/extra": "<p>x</p>"}}

        assert differences(taken(), taken(after)) == ["new: main:/extra"]

    def test_Compare_WhenTheDispatcherKnowsADifferentNumberOfRoutes_SaysSo(self):
        said = differences(taken(routes=40), taken(routes=39))

        assert said == ["route count: baseline 40, now 39"]

    def test_Compare_WhenNoRouteWasFoundAndNoPageWasServed_IsRefusedNotMatched(self):
        with pytest.raises(AssertionError, match="no pages"):
            snapshot({"main": {}}, route_count=0)

    def test_Compare_AgainstABaselineWithNoPages_IsRefused(self):
        with pytest.raises(AssertionError, match="no pages"):
            differences({"pages": {}}, taken())


class TestTheStylesheet:
    def test_Compare_WhenTheStylesheetChanged_SaysSoOnceAndNamesNoPage(self):
        restyled = {"main": {"/": PAGE.replace("red", "blue"), "/accounts": "<p>accounts</p>"}}

        assert differences(taken(), taken(restyled)) == ["stylesheet: digest differs"]

    def test_Compare_WhenOnePageAloneCarriesAnotherBlock_NamesHowManyAndTheFirst(self):
        odd = PAGE.replace("red", "blue")
        before = {"main": {"/": PAGE, "/accounts": PAGE}}
        after = {"main": {"/": PAGE, "/accounts": odd}}

        assert differences(taken(before), taken(after)) == [
            "stylesheet: 1 pages carry a different block than before, first main:/accounts"
        ]

    def test_Compare_WhenAPageIsRedirectedAndCarriesNoStylesheet_ItIsNotAStylesheetChange(self):
        walk = {"main": {"/": PAGE, "/gaps": "", "/accounts": "<p>accounts</p>"}}

        assert differences(taken(walk), taken(walk)) == []

    def test_SplitStylesheet_OnAPage_ReturnsThePageWithoutItAndTheBlock(self):
        bare, style = split_stylesheet(PAGE)

        assert "<style></style>" in bare and "color:red" not in bare
        assert style == "<style>body{color:red}</style>"


class TestStabilised:
    @pytest.mark.parametrize(
        "volatile",
        ["19 checks ran at 13:09", "Read at 07:45 from the checks", "Worked out in 0.0 s at 13:09."],  # noqa: E501
    )
    def test_Stabilised_OnAClockTimeAfterAt_FixesIt(self, volatile):
        assert "13:09" not in stabilised(volatile) and "07:45" not in stabilised(volatile)

    def test_Compare_WhenOnlyHowLongAComputationTookChanged_ListsNothing(self):
        line = "<p>computed: reports {}s, summarise {}s; worked out in {} s</p>"
        before = {"main": {"/": line.format("0.00", "0.00", "0.0")}}
        after = {"main": {"/": line.format("0.01", "0.12", "0.3")}}

        assert differences(taken(before), taken(after)) == []

    def test_Stabilised_OnAFigureThatIsNotADuration_LeavesItAlone(self):
        assert stabilised("<p>2.50 pounds, 3.5 sources</p>") == "<p>2.50 pounds, 3.5 sources</p>"

    def test_Stabilised_OnATimeOfDayThatIsNotAfterAt_LeavesItAlone(self):
        assert stabilised("<p>opens 09:00</p>") == "<p>opens 09:00</p>"
