"""What the Actual page says, in the order it says it, for invented results.

The layout is measured in a browser (`test_actual_page_layout`); this holds the content: the
verdict first, the pre-push check above the push, only the differing accounts listed open, the
earlier results one line each, and the history summarised the same way.
"""

from __future__ import annotations

import re

import pytest

from actual_states import STATES, audit, push, render_state
from obdi import web_actual
from obdi.export.actual_verdict import State


def _page(state: str) -> str:
    return render_state(state)


class TestOrder:
    def test_Page_OpensWithTheVerdictBeforeAnyPressOrStep(self):
        page = _page("push_after_audit")

        assert page.index('data-state="unchecked"') < page.index("Push to Actual now")
        assert page.index("Push to Actual now") < page.index("Step by step")
        assert page.index("Step by step") < page.index("Newest audit")

    def test_Page_PutsThePrePushCheckAboveThePushButton(self):
        page = _page("agrees")

        assert page.index("A push will send rows to 17 bound accounts") < page.index(
            "Push to Actual now"
        )
        assert page.index("Accounts and what a push does to them") < page.index(
            "Push to Actual now"
        )

    def test_Page_PutsTheDangerZoneLastAndTheRemedyBesideTheVerdict(self):
        page = _page("differs_three_orphans")

        assert page.index('data-state="differs"') < page.index('action="/align-actual"')
        assert page.index('action="/align-actual"') < page.index("Step by step")
        assert page.index("Newest audit") < page.index("Danger zone")
        assert page.rindex("Danger zone") > page.rindex("How the sync works")


class TestResults:
    def test_Page_WhenOneAccountDiffers_NamesItOpenAndFoldsTheSixteenThatAgree(self):
        page = _page("differs_three_orphans")

        assert page.count('class="differ"') == 1
        assert "<summary>16 accounts agree</summary>" in page
        assert "1 account differs; 16 agree; 1,434 of 1,434 transfer pairs linked." in page

    def test_Page_WhenEverythingAgrees_SummarisesInOneSentenceAndListsNoAccountOpen(self):
        page = _page("agrees")

        assert "17 accounts agree; 1,434 of 1,434 transfer pairs linked." in page
        assert 'class="differ"' not in page
        assert "<summary>17 accounts agree</summary>" in page

    def test_Page_ListsAtMostThreeEarlierResultsAndTheNewestPushOneLineEach(self):
        results = [push(n) for n in range(10, 80, 10)] + [audit(85)]

        body = web_actual.actual_rows(lambda: results, True)

        earlier = re.search(r'<ul class="recent">(.*?)</ul>', body, flags=re.S)
        assert earlier is not None
        assert earlier.group(1).count("<li>") == 3

    def test_Page_LinksToTheFullHistoryWhateverTheState(self):
        for state in ("agrees", "push_failed"):
            assert 'href="/actual-history"' in _page(state)

    def test_Page_WhenTheNewestPushIsOlderThanThreeLaterResults_StillListsIt(self):
        results = [push(5)] + [audit(10 + n) for n in range(6)]

        body = web_actual.actual_rows(lambda: results, True)

        assert "<strong>Push</strong>" in body


class TestWords:
    @pytest.mark.parametrize("state", list(STATES))
    def test_Page_InEachState_NeverWritesAnOptionalPlural(self, state):
        body = _page(state)
        body = body[body.index("</style>") :]

        assert "(s)" not in body

    @pytest.mark.parametrize("state", list(STATES))
    def test_Page_InEachState_NeverNamesAnEnvironmentVariableOrAnAmount(self, state):
        body = _page(state)
        body = body[body.index("</style>") :]

        assert "ACTUAL_SYNC_ID" not in body
        assert "£" not in body

    def test_Page_WhenNotConfigured_KeepsTheSentenceAndTheDisabledButtons(self):
        page = _page("not_configured")

        assert "Actual is not configured on this instance" in page
        assert page.count("Off: Actual is not configured.") == 3


class TestCurrentVerdict:
    """The home page asks for the same verdict through `current_verdict`."""

    def test_CurrentVerdict_ReadsTheSameHooksThePageReads(self):
        results = [push(10), audit(20)]

        verdict = web_actual.current_verdict(lambda: results)

        assert verdict.state is State.AGREES

    def test_CurrentVerdict_WhenTheResultsCannotBeRead_IsItsOwnVerdictNotAnEmptyHistory(self):
        def broken() -> list[dict[str, object]]:
            raise OSError("disk")

        assert web_actual.current_verdict(broken).state is State.UNREADABLE
        assert web_actual.current_verdict(None).state is State.UNREADABLE

    def test_CurrentVerdict_WhenTheQueueCannotBeRead_IsTreatedAsEmpty(self):
        def broken() -> list[dict[str, object]]:
            raise OSError("disk")

        verdict = web_actual.current_verdict(lambda: [push(10), audit(20)], broken)

        assert verdict.state is State.AGREES

    def test_CurrentVerdict_WhenNotConfigured_SaysSo(self):
        verdict = web_actual.current_verdict(lambda: [], actual_configured=lambda: False)

        assert verdict.state is State.NOT_CONFIGURED


class TestHistory:
    def test_History_SummarisesEachAuditTheWayThePageDoes(self):
        from obdi import web

        row = web._result_row(audit(30, orphaned={"halifax-current-account": 3}))

        assert 'class="differ"' in row
        assert "<summary>16 accounts agree</summary>" in row
        assert "1 account differs; 16 agree" in row

    def test_History_LeavesOtherKindsAsTheirOwnRows(self):
        from obdi import web

        assert "applied" in web._result_row(push(30))
