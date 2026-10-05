"""Setting a period aside on the What to fetch next page, from the gap to the folded list.

Known answers (over `fetch_marks_world`, TODAY 2026-10-05; the HTTP tests make marks over days
well before any real clock):

  * Marking card-virgin's hole "nothing to fetch": "12 things to fetch for 10 accounts; 3 accounts
    need nothing; 1 with nothing to fetch." Acknowledging it as a known gap instead says "1 known
    gap acknowledged" and never claims anything about the data.
  * The same mark over a hole the feed lists two rows in is contradicted, shown above the
    accounts, and counted: "13 things to fetch for 11 accounts; 2 accounts need nothing; 1 mark is
    contradicted by what is held."
  * main's aggregator history begins 2026-03-15 and an ask for days from 2026-01-01 came back
    empty, so one offer is made; card-behind's begins 2026-08-02 and was never asked about, so it
    is not.
"""

from __future__ import annotations

import html
import re
import threading
from http.server import HTTPServer

import httpx
import pytest

from fetch_gaps_world import TODAY, D
from fetch_marks_world import (
    add_feed_rows_in_virgin_hole,
    household,
    mark,
    marks_read,
    read_at,
    report_with,
    scope,
)
from obdi.cli import build_web_config
from obdi.store import Store
from obdi.web import AuthorisationSession, ConnectionHandler
from obdi.web_gaps import render_gaps, verdict_sentence
from obdi.web_marks import evidence_text

VIRGIN = (D(2026, 6, 5), D(2026, 7, 4))
NAMES = {"card-virgin": "Virgin card", "main": "Main account", "card-hole": "Hole card"}


@pytest.fixture
def db(tmp_path):
    return household(tmp_path)


def page(db, today=TODAY) -> str:
    return render_gaps(report_with(db, read_at(db, today), today), NAMES).decode()


def words(markup: str) -> str:
    plain = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html.unescape(markup))).strip()
    return re.sub(r" ([.,;:?])", r"\1", plain)


class TestTheVerdictCountsEachKindApart:
    def test_Verdict_WhenAHoleIsMarkedNothingToFetch_SaysSoAndCountsOnlyWhatIsStillToFetch(
        self, db
    ):
        mark(db, account="card-virgin", kind="nothing-to-fetch",
             first_day=VIRGIN[0], last_day=VIRGIN[1])

        assert verdict_sentence(report_with(db, marks_read(db))) == (
            "12 things to fetch for 10 accounts; 3 accounts need nothing; "
            "1 with nothing to fetch."
        )

    def test_Verdict_WhenTheSameHoleIsAcknowledgedInstead_SaysKnownGapNotNothingToFetch(self, db):
        mark(db, account="card-virgin", kind="known-gap", first_day=VIRGIN[0], last_day=VIRGIN[1])

        said = verdict_sentence(report_with(db, marks_read(db)))

        assert said.endswith("; 1 known gap acknowledged.")
        assert "nothing to fetch." not in said.split("; ")[-1]

    def test_Verdict_WhenSeveralKindsAreSetAside_NamesEachWithItsOwnCount(self, db):
        mark(db, account="card-virgin", kind="known-gap", first_day=VIRGIN[0], last_day=VIRGIN[1])
        mark(db, account="card-late-one", kind="nothing-to-fetch",
             first_day=D(2026, 6, 11), last_day=D(2026, 7, 31))
        scope(db, first_day=D(2026, 4, 1))

        said = verdict_sentence(report_with(db, marks_read(db)))

        assert "1 known gap acknowledged" in said
        assert "1 before your record begins" in said

    def test_Verdict_WhenNothingIsDecided_IsExactlyWhatItWas(self, db):
        assert verdict_sentence(report_with(db, marks_read(db))) == (
            "13 things to fetch for 11 accounts; 2 accounts need nothing."
        )

    def test_Verdict_WhenAMarkIsContradicted_CountsItAndTheGapStillCounts(self, db):
        add_feed_rows_in_virgin_hole(db)
        mark(db, account="card-virgin", kind="nothing-to-fetch",
             first_day=VIRGIN[0], last_day=VIRGIN[1])

        assert verdict_sentence(report_with(db, marks_read(db))) == (
            "13 things to fetch for 11 accounts; 2 accounts need nothing; "
            "1 mark is contradicted by what is held."
        )


class TestEachGapOffersTwoWaysToSetItAside:
    def test_Gap_OffersOneTapAcknowledgementAndALinkThatOpensTheForm(self, db):
        markup = page(db)
        section = markup.split('aria-label="Virgin card"')[1].split("</section>")[0]

        assert "Acknowledge this gap" in section
        assert 'action="/gaps-mark"' in section
        assert (
            'href="/gaps-mark?account=card-virgin&amp;first=2026-06-05&amp;last=2026-07-04"'
            in section
        )
        assert "Nothing to fetch for this period..." in section

    def test_Gap_WhenSplitByAMark_SaysWhatRemainsOfTheOriginal(self, db):
        mark(db, account="card-hole", kind="nothing-to-fetch",
             first_day=D(2026, 3, 11), last_day=D(2026, 4, 10))

        said = words(page(db).split('aria-label="Hole card"')[1].split("</section>")[0])

        assert "2026-04-11 to 2026-05-09" in said
        assert (
            "This is what remains of 2026-03-11 to 2026-05-09 after the part you set aside."
        ) in said


class TestAContradictedMarkStandsOut:
    def test_Page_WhenTheFeedListsRowsInAMarkedHole_SaysSoAboveTheAccountsWithCountsAndDates(
        self, db
    ):
        add_feed_rows_in_virgin_hole(db)
        mark(db, account="card-virgin", kind="nothing-to-fetch",
             first_day=VIRGIN[0], last_day=VIRGIN[1])

        markup = page(db)
        warned = markup.split('class="gaps-contradictions"')[1].split("</ul>")[0]

        assert markup.index("gaps-contradictions") < markup.index('class="gaps-account"')
        assert "You marked this period as having no transactions; the aggregator lists 2 rows" in (
            words(warned))
        assert "from 2026-06-20 to 2026-07-01" in words(warned)
        assert "The gap stays in the list until you change or remove the mark." in words(warned)
        assert "Hole Feed" not in markup, "counts and dates only"


class TestEvidenceWords:
    def evidence(self, db, account, kind, first, last, source=""):
        from fetch_marks_world import world_of
        from obdi.fetch_gaps import STATEMENT_SOURCES
        from obdi.fetch_marks import MarkKind, gather_evidence, judge

        world = world_of(db)
        found = gather_evidence(world, account, source, first, last,
                                statement_sources=STATEMENT_SOURCES)
        standing, reason = judge(MarkKind(kind), source, first, last, found,
                                 statement_sources=STATEMENT_SOURCES)
        from obdi.fetch_marks import MarkKind as K

        return evidence_text(K(kind), source, first, last, found, standing, reason, marked=False)

    def test_NothingToFetch_WhenNoRowIsListed_SaysSupportedInNoStrongerWords(self, db):
        said = self.evidence(db, "card-virgin", "nothing-to-fetch", *VIRGIN)

        assert said.startswith("No source lists a row in this period.")
        assert "does not prove it" in said

    def test_NothingToFetch_WhenRowsAreListed_SaysItWouldDisagreeBeforeItIsMade(self, db):
        add_feed_rows_in_virgin_hole(db)

        said = self.evidence(db, "card-virgin", "nothing-to-fetch", *VIRGIN)

        assert said.startswith(
            "Saying there were no transactions would disagree with what is held:"
        )
        assert "the aggregator lists 2 rows in it, from 2026-06-20 to 2026-07-01" in said

    def test_BeforeHistory_WhenNeverAsked_SaysNothingYetShowsItHoldsNone(self, db):
        said = self.evidence(db, "card-behind", "before-history", None, D(2026, 8, 1),
                             source="truelayer")

        assert "has not been asked for days this early" in said
        assert "never been asked" in said

    def test_NoLongerProvided_WhenAnotherSourceReachesThePeriod_NamesIt(self, db):
        said = self.evidence(db, "main", "no-longer-provided", D(2026, 3, 1), D(2026, 3, 31),
                             source="starling-csv")

        assert said == (
            "the aggregator still lists rows in this period, so it can still be had from there."
        )

    def test_KnownGap_SaysNothingAboutTheData(self, db):
        assert self.evidence(db, "main", "known-gap", D(2026, 3, 1), D(2026, 3, 31)) == ""


class TestTheAggregatorsReach:
    def test_Page_WhenAnAskCameBackEmpty_OffersTheMarkReadyMadeWithItsReason(self, db):
        said = words(page(db).split('class="gaps-reach"')[1].split("</section>")[0])

        assert "Main account main: the aggregator's history begins 2026-03-15" in said
        assert "a request for days back to 2026-01-01 came back with no rows" in said
        assert "Mark everything before 2026-03-15 as before its history?" in said

    def test_Page_WhenNothingWasAskedFurtherBack_SaysSoAndOffersNothing(self, db):
        section = page(db).split('class="gaps-reach"')[1].split("</section>")[0]

        assert "Behind card" not in section.split('class="gaps-offer"')[-1].split("</form>")[0]
        assert "It has not been asked for earlier days, so nothing is offered." in words(section)

    def test_Page_WhenTheOfferIsTaken_ItIsNotOfferedAgainAndTheMarkSaysWhoMadeIt(self, db):
        mark(db, account="main", source="truelayer", kind="before-history",
             last_day=D(2026, 3, 14), origin="source")

        markup = page(db)

        assert 'class="gaps-offer"' not in markup
        assert "from the source&#x27;s own answer" in markup


class TestTheFoldedSection:
    def test_Section_ListsEachMarkWithItsKindEvidenceNoteAndUndo(self, db):
        mark(db, account="card-virgin", kind="nothing-to-fetch", first_day=VIRGIN[0],
             last_day=VIRGIN[1], note="the card was frozen <b>that</b> month")

        folded = page(db).split('class="gaps-aside"')[1]
        said = words(folded)

        assert "Set aside by your decision (1)" in said
        assert "Nothing to fetch" in said and "supported by what is held" in said
        assert "2026-06-05 to 2026-07-04" in said
        assert "No source lists a row in this period." in said
        assert "your decision" in said
        assert "the card was frozen &lt;b&gt;that&lt;/b&gt; month" in folded
        assert "<b>that</b>" not in folded, "the note is shown as typed text, never as markup"
        assert 'action="/gaps-mark-undo"' in folded and ">Undo<" in folded

    def test_Section_WhenAKnownGapIsListed_ShowsNoEvidenceAndNoClaim(self, db):
        mark(db, account="card-virgin", kind="known-gap", first_day=VIRGIN[0], last_day=VIRGIN[1])

        folded = words(page(db).split('class="gaps-aside"')[1])

        assert "Known gap" in folded
        assert "supported by" not in folded and "contradicted by" not in folded

    def test_Section_WhenAMarkLiesBeforeTheRecordBegins_SaysItIsKeptAndDoesNothing(self, db):
        scope(db, months=6, account="")
        mark(db, account="main", source="starling-csv", kind="known-gap",
             first_day=D(2026, 3, 1), last_day=D(2026, 3, 31))

        folded = words(page(db).split('class="gaps-aside"')[1])

        assert "kept and does nothing until your record reaches back to it" in folded

    def test_Section_WhenAKnownGapHasADayToLookAgainOn_SaysSo(self, db):
        mark(db, account="card-virgin", kind="known-gap", first_day=VIRGIN[0],
             last_day=VIRGIN[1], review_on=D(2026, 11, 1))

        assert "Look again on 2026-11-01." in words(page(db).split('class="gaps-aside"')[1])


class TestScopeIsSaidOnceQuietly:
    def test_Page_WhenARollingScopeIsKept_SaysWhereTheRecordBeginsAndThatRowsAreStillTested(
        self, db
    ):
        scope(db, months=24)

        said = words(page(db).split('class="gaps-scope-lines')[1].split("</ul>")[0])

        assert (
            "You keep Main account main from 2024-10-05 (the last 24 months); earlier days "
        ) in said
        assert "Rows before it are still held and still tested." in said

    def test_Page_WhenTheFirstKnownBalanceIsBeforeTheScope_SaysItStillCarriesTheRows(self, db):
        scope(db, first_day=D(2026, 6, 1))

        said = words(page(db).split('class="gaps-scope-lines')[1].split("</ul>")[0])

        assert (
            "first known balance, 2026-01-11, is before that day and still carries the rows"
        ) in said

    def test_Page_WhenARollingScopeHasMovedPastAGap_SaysItLeftUnfilledOnTheDay(self, db):
        scope(db, months=7)

        early = words(page(db, TODAY))
        late = words(page(db, D(2026, 11, 2)))

        # The part of March before the scope's first day (03-05) left it today; the rest leaves
        # on the first of November.
        assert "2026-03-01 to 2026-03-04 - left your 7 months on 2026-10-05 unfilled" in early
        assert "left your 7 months on 2026-11-01 unfilled" not in early
        assert "2026-03-01 to 2026-03-31 - left your 7 months on 2026-11-01 unfilled" in late


@pytest.fixture
def served(db, tmp_path, monkeypatch):
    monkeypatch.setenv("OBDI_CONNECTION_STORE", str(tmp_path / "connections.json"))
    monkeypatch.delenv("OBDI_ACCOUNT_MAP", raising=False)
    for variable in ("TRUELAYER_CLIENT_ID", "TRUELAYER_CLIENT_SECRET_FILE"):
        monkeypatch.delenv(variable, raising=False)
    config = build_web_config(db)
    assert config is not None
    handler = type("H", (ConnectionHandler,), {"config": config, "session": AuthorisationSession()})
    httpd = HTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{httpd.server_port}"
    finally:
        httpd.shutdown()


def post(served, path, **fields):
    return httpx.post(f"{served}{path}", data=fields, timeout=30)


def get(served, path, **params):
    return httpx.get(f"{served}{path}", params=params, timeout=30)


class TestMakingAndUndoingOverHttp:
    def test_Form_ShowsWhatTheStoreHoldsBesideEachClaimBeforeAnythingIsConfirmed(self, served):
        response = get(served, "/gaps-mark", account="card-virgin", first="2026-06-05",
                       last="2026-07-04")

        said = words(response.text)
        assert response.status_code == 200
        assert "No source lists a row in this period." in said
        assert "Known gap" in said and "Other" in said
        assert "changes nothing about what is verified" in said
        assert response.text.count('type="radio" name="kind"') == 6
        assert "Check what is held for these dates" in said

    def test_Check_WhenDatesAreEdited_ShowsTheEvidenceForTheNewDatesAndMakesNothing(
        self, served, db
    ):
        response = post(served, "/gaps-mark", account="card-virgin", source="", first="2026-04-01",
                        last="2026-05-04", kind="nothing-to-fetch", step="check")

        assert response.status_code == 200
        assert "lists 1 row" in words(response.text) or "No source lists a row" in words(
            response.text)
        assert marks_read(db).readings == ()

    @pytest.mark.parametrize(
        ("kind", "label", "extra"),
        [
            ("known-gap", "known gap", {}),
            ("nothing-to-fetch", "nothing to fetch", {}),
            ("no-longer-provided", "no longer provided", {}),
            ("not-open", "account not open", {}),
            ("other", "other", {"note": "the bank archive was closed"}),
        ],
    )
    def test_Mark_OfEachKindMadeThroughThePage_IsReadBackAsThatKindWithItsOwnWords(
        self, served, db, kind, label, extra
    ):
        response = post(served, "/gaps-mark", account="card-virgin", source="", first="2026-06-05",
                        last="2026-07-04", kind=kind, step="mark", **extra)

        assert response.status_code == 200, response.text
        assert f"Set aside as {label}: Virgin card (card-virgin)" in words(response.text) or (
            f"Set aside as {label}: card-virgin" in words(response.text))
        assert 'href="/gaps"' in response.text
        (made,) = marks_read(db).readings
        assert made.mark.kind.value == kind
        listed = get(served, "/gaps").text
        assert "Set aside by your decision" in listed

    def test_Mark_WhenAKnownGapIsAcknowledged_TheGapLeavesTheListAndUndoBringsItBack(
        self, served
    ):
        before = get(served, "/gaps").text
        post(served, "/gaps-mark", account="card-virgin", source="", first="2026-06-05",
             last="2026-07-04", kind="known-gap", step="mark")
        during = get(served, "/gaps").text
        mark_id = re.search(r'name="id" value="(\d+)"', during).group(1)
        undone = post(served, "/gaps-mark-undo", id=mark_id)
        after = get(served, "/gaps").text

        assert "2026-06-05 to 2026-07-04" in words(
            before.split('aria-label="Virgin card"')[1].split("</section>")[0]
        )
        assert 'aria-label="Virgin card"' not in during
        assert "Whatever it set aside is listed again." in words(undone.text)
        assert 'aria-label="Virgin card"' in after

    @pytest.mark.parametrize(
        ("fields", "why"),
        [
            ({"last": "2026-07-04", "first": "2026-08-01"}, "first day is after the last day"),
            ({"last": "2099-01-01"}, "has not ended yet"),
            ({"last": "not a day"}, "must be written like 2026-10-05"),
            ({"last": ""}, "A last day is needed"),
            ({"last": "2026-07-04", "kind": "other"}, "needs a note"),
            ({"last": "2026-07-04", "kind": "nonsense"}, "not one of the kinds"),
            ({"last": "2026-07-04", "account": "nobody"}, "not an account"),
            ({"last": "2026-07-04", "source": "nowhere"}, "not a source that feeds"),
        ],
    )
    def test_Mark_WhenInvalid_IsRefusedWithTheReasonAndNothingIsChanged(
        self, served, db, fields, why
    ):
        sent = {"account": "card-virgin", "source": "", "first": "", "kind": "known-gap",
                "step": "mark", **fields}

        response = post(served, "/gaps-mark", **sent)

        assert response.status_code == 409
        assert why in words(response.text)
        assert marks_read(db).readings == ()

    def test_Mark_WhenItOverlapsAnother_IsRefusedNamingWhatIsInTheWay(self, served, db):
        post(served, "/gaps-mark", account="card-virgin", source="", first="2026-06-05",
             last="2026-07-04", kind="known-gap", step="mark")

        response = post(served, "/gaps-mark", account="card-virgin", source="", first="2026-07-01",
                        last="2026-07-10", kind="nothing-to-fetch", step="mark")

        assert response.status_code == 409
        assert "Remove that one first" in words(response.text)
        assert len(marks_read(db).readings) == 1

    def test_Mark_WhenForeignSiteDrivesThePost_IsRefused(self, served, db):
        response = httpx.post(
            f"{served}/gaps-mark",
            data={"account": "card-virgin", "source": "", "last": "2026-07-04",
                  "kind": "known-gap", "step": "mark"},
            headers={"Origin": "http://evil.example"},
        )

        assert response.status_code == 403
        assert marks_read(db).readings == ()

    def test_Undo_WhenTheMarkIsAlreadyGone_IsRefusedWithoutAFault(self, served):
        response = post(served, "/gaps-mark-undo", id="9999")

        assert response.status_code == 409
        assert "not there to remove" in words(response.text)

    def test_Undo_WhenTheIdIsNotANumber_IsRefused(self, served):
        assert post(served, "/gaps-mark-undo", id="1; DROP").status_code == 409


class TestScopeOverHttp:
    def test_Scope_WhenRollingMonthsAreSaved_IsKeptAndSaidOnThePage(self, served):
        response = post(served, "/gaps-scope", account="main", mode="rolling", months="24")

        assert response.status_code == 200
        assert "You now keep Main account (main) from " in words(response.text) or (
            "You now keep main from " in words(response.text))
        assert "(the last 24 months)" in words(response.text)
        assert "earlier days are not looked for" in words(get(served, "/gaps").text)

    @pytest.mark.parametrize(
        ("fields", "why"),
        [
            ({"mode": "rolling", "months": ""}, "whole number"),
            ({"mode": "rolling", "months": "0"}, "between 1 and 600"),
            ({"mode": "fixed", "first": ""}, "A first day is needed"),
            ({"mode": "fixed", "first": "2026-13-45"}, "must be written like"),
            ({"mode": "sideways"}, "Choose how far back"),
        ],
    )
    def test_Scope_WhenInvalid_IsRefused(self, served, fields, why):
        response = post(served, "/gaps-scope", account="main", **fields)

        assert response.status_code == 409
        assert why in words(response.text)

    def test_Scope_WhenCleared_KeepsEverythingAgain(self, served):
        post(served, "/gaps-scope", account="", mode="rolling", months="3")
        cleared = post(served, "/gaps-scope", account="", mode="clear")

        assert "You now keep all of the past for every account." in words(cleared.text)
        assert "earlier days are not looked for" not in words(get(served, "/gaps").text)


class TestTodaysItemFollowsTheDecisionOverHttp:
    def test_Today_WhenTheAwaitedPeriodIsAcknowledged_StopsNamingTheAccount(self, served, db):
        before = words(get(served, "/").text)
        with Store(db) as store:
            epoch = store.standing_epoch()
        post(served, "/gaps-mark", account="card-late-one", source="", first="2026-06-11",
             last="2026-08-01", kind="known-gap", step="mark")
        after = words(get(served, "/").text)

        assert "Behind card (since 2026-07-10) and Late-one card (since 2026-06-10)" in before
        assert "Late-one card (since" not in after, "Today must not ask for a period he set aside"
        assert "Behind card (since 2026-07-10)" in after
        with Store(db) as store:
            assert store.standing_epoch() > epoch, "a mark moves what the held pages key on"
