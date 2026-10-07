"""Today's verdict, evidence line, things to do, and account rows, with answers decided up front.

The household is `home_world`: twenty accounts built through the real doors, whose standings are
known from their construction. What is asserted is what a person reads, and the one place each
sentence is decided (`verdict_of`, the trust sentence) is also held to a table.
"""

from __future__ import annotations

import re
from dataclasses import replace
from datetime import UTC, date, datetime

import pytest

import home_world as world
from obdi.ingest.store import Store
from obdi.overview import (
    _KINDS,
    HOUSEKEEPING,
    INFORMATION,
    NOW,
    SOON,
    AttentionItem,
    Overview,
    build_overview,
)
from obdi.verify.agreement import standing_of
from obdi.verify.balance_anchors import effective_opening, record_stated_anchor
from obdi.verify.movement_completeness import MovementCompleteness
from obdi.verify.protection import press
from obdi.web_overview import (
    arrange,
    band_phrase,
    overview_html,
    plural,
    row_reading,
    serial,
    verdict_of,
    verification_counts,
)
from test_ledger import build_household, land, txn

NOW_TIME = datetime(2026, 10, 1, 14, 2, tzinfo=UTC)


def overview_of(db, **options):
    settings = {
        "now": NOW_TIME,
        "findings": lambda: [],
        "canonical_for_ref": lambda ref: ref,
        "watched": set(),
        "actual_bound": None,
        "rebuild_status": {},
    }
    settings.update(options)
    with Store(db) as store:
        return build_overview(store, **settings)


@pytest.fixture(scope="module")
def troubled(tmp_path_factory):
    db = tmp_path_factory.mktemp("home-troubled") / "world.sqlite3"
    world.build_scale_world(db, trouble=True)
    return overview_of(db)


@pytest.fixture(scope="module")
def clear(tmp_path_factory):
    db = tmp_path_factory.mktemp("home-clear") / "world.sqlite3"
    world.build_scale_world(db, trouble=False)
    return overview_of(db)


def account(overview, ref):
    return next(a for a in overview.accounts if a.ref == ref)


def page_of(overview) -> str:
    return overview_html(lambda fresh: overview, now=NOW_TIME)


def verdict_in(page: str) -> str:
    found = re.search(r'id="verdict"><span>(.*?)</span>', page)
    assert found is not None
    return found.group(1)


class TestTheVerdict:
    @pytest.mark.parametrize(
        ("counts", "sentence", "tone"),
        [
            ({}, "Everything checked is in order.", "ok"),
            ({NOW: 0, SOON: 0, HOUSEKEEPING: 0}, "Everything checked is in order.", "ok"),
            ({NOW: 1}, "1 fault to look at now.", "bad"),
            (
                {NOW: 1, HOUSEKEEPING: 4},
                "1 fault to look at now and 4 things when convenient.",
                "bad",
            ),
            (
                {NOW: 2, SOON: 1, HOUSEKEEPING: 4},
                "2 faults to look at now, 1 thing to look at soon, and 4 things when convenient.",
                "bad",
            ),
            ({SOON: 2}, "No faults. 2 things to look at soon.", "warn"),
            (
                {SOON: 1, HOUSEKEEPING: 3},
                "No faults. 1 thing to look at soon and 3 things when convenient.",
                "warn",
            ),
            ({HOUSEKEEPING: 4}, "No faults. 4 things when convenient.", "ok"),
        ],
    )
    def test_Verdict_ForTheseCounts_SaysExactlyWhatTheyAre(self, counts, sentence, tone):
        verdict = verdict_of(counts)

        assert (verdict.sentence, verdict.tone) == (sentence, tone)

    def test_Verdict_WhenNothingIsWrong_NeverMentionsFaultsOrThingsToDo(self):
        assert "fault" not in verdict_of({}).sentence
        assert "to look at" not in verdict_of({}).sentence
        assert "convenient" not in verdict_of({}).sentence

    @pytest.mark.parametrize(
        ("failing", "sentence"),
        [
            (1, "No faults. 1 account does not add up."),
            (3, "No faults. 3 accounts do not add up."),
        ],
    )
    def test_Verdict_WhenNoItemIsRaisedButAnAccountDoesNotAddUp_DoesNotSayAllIsInOrder(
        self, failing, sentence
    ):
        verdict = verdict_of({}, not_adding_up=failing)

        assert (verdict.sentence, verdict.tone) == (sentence, "warn")

    def test_Verdict_WhenItemsAreRaisedAndAnAccountDoesNotAddUp_CountsTheItemsAsBefore(self):
        verdict = verdict_of({HOUSEKEEPING: 4}, not_adding_up=1)

        assert verdict.sentence == "No faults. 4 things when convenient."

    def test_Verdict_WhenEveryAccountAddsUpAndNoItemIsRaised_SaysAllIsInOrder(self):
        assert verdict_of({}, not_adding_up=0).sentence == "Everything checked is in order."

    def test_Verdict_OnTheTroubledWorld_CountsTheItemsBeneathIt(self, troubled):
        faults = [i for i in troubled.attention if i.severity == NOW]
        later = [i for i in troubled.attention if i.severity == HOUSEKEEPING]

        assert len(faults) == 2, "the two held-back accounts, each a fault"
        assert len(later) == 1, "the three accounts with rows after their balance, one reminder"
        # The one reminder names three accounts, and each is a thing to do of its own.
        assert verdict_in(page_of(troubled)) == (
            "2 faults to look at now and 3 things when convenient."
        )

    def test_Verdict_OnTheClearWorld_IsThePositiveStatement(self, clear):
        page = page_of(clear)

        assert 'class="verdict ok" id="verdict"><span>Everything checked is in order.' in page
        assert clear.attention == ()


class TestTheWordsAreShared:
    def test_Plural_ForOneAndMany_NeverWritesAnOptionalS(self):
        assert plural(1, "fault") == "1 fault"
        assert plural(2, "fault") == "2 faults"
        assert plural(0, "thing") == "0 things"
        assert plural(2, "archived Space") == "2 archived Spaces"
        assert plural(2, "match", "matches") == "2 matches"

    def test_Serial_FromThreeUp_UsesTheSerialComma(self):
        assert serial(["a"]) == "a"
        assert serial(["a", "b"]) == "a and b"
        assert serial(["a", "b", "c"]) == "a, b, and c"

    @pytest.mark.parametrize(
        ("severity", "count", "phrase"),
        [
            (NOW, 1, "1 fault to look at now"),
            (NOW, 3, "3 faults to look at now"),
            (SOON, 1, "1 thing to look at soon"),
            (HOUSEKEEPING, 4, "4 things when convenient"),
        ],
    )
    def test_BandPhrase_ForEachBand_IsTheOneWordingTheHeadingsAndFoldsShare(
        self, severity, count, phrase
    ):
        assert band_phrase(severity, count) == phrase


#: Every kind and the band it is raised in, decided here and not read back from the table.
EXPECTED_BANDS = {
    "rebuild-running": INFORMATION,
    "rebuild:abandoned": NOW,
    "rebuild:empty": NOW,
    "check-failed": NOW,
    "protection-broken": NOW,
    "silent-feed": NOW,
    "refusals": NOW,
    "stale-feed": NOW,
    "uncovered-span": NOW,
    "shared-identity": NOW,
    "identity-health": NOW,
    "movement-completeness": NOW,
    "balance": NOW,
    "rebuild-problems": NOW,
    "push-refused": NOW,
    "push-stale": NOW,
    "scheduler-overdue": NOW,
    "scheduler-failed": NOW,
    "scheduler-stuck": SOON,
    "scheduler-late-wait": INFORMATION,
    "consent": SOON,
    "disk": SOON,
    "review": INFORMATION,
    "spaces": HOUSEKEEPING,
    "known-balances-disagree": SOON,
    "agreement-lapsed": NOW,
    "statement-fault": NOW,
    "statement-due": HOUSEKEEPING,
}

_EMPTY = Overview(NOW_TIME, 0, 0, (), ())


class TestEveryKindHasABandAndAnAct:
    def test_Kinds_EachIsInTheBandItsConsequenceCalls_AndNoKindIsLeftUnlisted(self):
        assert {kind: band for kind, (band, _) in _KINDS.items()} == EXPECTED_BANDS

    @pytest.mark.parametrize(
        "kind", ["balance", "protection-broken", "movement-completeness", "push-refused"]
    )
    def test_Faults_AreTheMisreportedAndTheBroken(self, kind):
        assert _KINDS[kind][0] == NOW

    def test_AReminder_IsNeverAFault(self):
        assert _KINDS["statement-due"][0] == HOUSEKEEPING

    @pytest.mark.parametrize("kind", sorted(_KINDS))
    def test_ToDo_ForEveryKindOfItem_HasAControlThatSaysWhatItDoesAndNeverABareOpen(self, kind):
        from obdi.todo import build_todos

        item = AttentionItem(kind, _KINDS[kind][0], "m", "r", "/x", accounts=("a", "b"))
        overview = replace(_EMPTY, items=(item,))

        todos = build_todos(overview, None, lambda ref: ref)

        if kind in ("rebuild-running", "scheduler-late-wait"):
            assert todos == (), "information with nothing to do is not a thing to do"
        else:
            assert todos
            assert all(t.control.label not in ("", "Open") for t in todos)
            assert all(t.control.href.startswith("/") for t in todos)


class TestAccountRows:
    def test_Row_ForEachKindOfStanding_SaysItsChipAndClause(self, troubled):
        def said(ref):
            reading = row_reading(account(troubled, ref))
            return reading.word, reading.clause

        assert said(world.AGREE[1]) == ("adds up", "every known balance up to 2026-03-20")
        assert said(world.PROTECTED) == (
            "protected",
            "every known balance up to 2026-03-20; protected through 2026-03-20",
        )
        assert said(world.LATER_ROWS[0]) == ("adds up", "every known balance up to 2026-03-10")
        assert said(world.HELD[0]) == ("does not add up", "stops adding up at 2026-03-15")
        assert said(world.UNPROVEN[0]) == ("nothing to check against", "no known balance")

    def test_Row_WhenProtectionHasBroken_SaysSoInsteadOfProtected(self, tmp_path):
        db = tmp_path / "broken.sqlite3"
        with Store(db) as store:
            world._state(store, "p", world.MET)
            opening = effective_opening(store, "p")
            press(
                store,
                "p",
                "2026-03-10",
                opening=opening,
                standing=standing_of(opening, ["p"], MovementCompleteness()),
            )
            land(store, "d-new", txn("p", "src-a", "r6", date(2026, 3, 7), -111, "NEW"))

        reading = row_reading(account(overview_of(db), "p"))

        assert (reading.word, reading.css, reading.group) == ("protection broken", "pill-bad", 0)
        assert reading.clause == "protected period has changed"

    def test_Rows_AreOrderedHeldBackThenUnprovenThenInAgreementThenQuietThenArchived(
        self, troubled
    ):
        order = [parent.ref for parent, _ in arrange(troubled.accounts)]
        groups = [row_reading(account(troubled, ref)).group for ref in order]

        assert groups == sorted(groups), "no row sorts above one that needs more looking at"
        assert set(order[:2]) == set(world.HELD)
        assert set(order[2:4]) == set(world.UNPROVEN)

    def test_Spaces_SitUnderTheirParent_AndNeverBesideIt(self, troubled):
        family = {p.ref: [s.ref for s in spaces] for p, spaces in arrange(troubled.accounts)}

        assert sorted(family[world.PARENT]) == sorted(world.SPACES)
        for space in world.SPACES:
            assert space not in family
        assert len(family) == world.TWENTY - len(world.SPACES)

    def test_Spaces_WhenOneIsHeldBack_LiftItsFamilyAboveTheOthers(self, tmp_path):
        db = tmp_path / "family.sqlite3"
        world.build_scale_world(db, trouble=False)
        with Store(db) as store:
            record_stated_anchor(store, world.LIVE_SPACES[0], "2026-03-25", "1.00")

        assert arrange(overview_of(db).accounts)[0][0].ref == world.PARENT

    def test_Spaces_WhenNoneIsHeldBack_DoNotLiftTheirFamily(self, clear):
        assert arrange(clear.accounts)[0][0].ref != world.PARENT

    def test_Row_ForAnArchivedSpace_IsArchivedWithItsDate(self, troubled):
        reading = row_reading(account(troubled, world.ARCHIVED_SPACES[0]))

        assert (reading.word, reading.clause, reading.group) == (
            "archived",
            "archived since 2026-04-30",
            4,
        )

    def test_ArchivedSpaces_AreFoldedBehindACount_AndLiveOnesAreNot(self, troubled):
        page = page_of(troubled)

        assert "<summary>4 archived accounts</summary>" in page
        folded = page.split("<summary>4 archived accounts</summary>")[1].split("</ul></details>")[0]
        for ref in world.ARCHIVED_SPACES:
            assert f'<a class="tap arow" href="/ledger?ref={ref}">' in folded
        for ref in world.LIVE_SPACES:
            assert f'<a class="tap arow" href="/ledger?ref={ref}">' not in folded
            assert f'<a class="tap arow" href="/ledger?ref={ref}">' in page

    def test_Counts_OnTheTroubledWorld_AreTheKnownAnswer(self, troubled):
        # Twenty accounts, four of them archived Spaces: sixteen are live. Two do not add up,
        # two have no balance, and the other twelve add up.
        assert verification_counts(troubled.accounts) == (16, 12, 2, 2)

    def test_Counts_WhenEveryAccountIsArchivedOrEmpty_AreNone(self, troubled):
        archived = [a for a in troubled.accounts if a.state == "archived"]

        assert len(archived) == 4
        assert verification_counts(archived) == (0, 0, 0, 0)

    def test_Rows_EachLiveAccountDrawsOneBarWhoseWordsAreItsTrustSentence(self, troubled):
        page = page_of(troubled)

        # Every account draws one, the archived ones inside their fold.
        assert page.count('<span class="bar"') == world.TWENTY

        def row(ref):
            opening = f'<a class="tap arow" href="/ledger?ref={ref}">'
            return page.split(opening)[1].split("</a>")[0]

        assert "Does not add up from 2026-03-15." in row("held-1")
        assert "Nothing to check against." in row("no-balance-1")
        assert "Locked in to 2026-03-20." in row(world.PROTECTED)
        assert "Adds up to the known balances to 2026-03-20." in row(world.AGREE[1])

    def test_Rows_AnAccountThatNeedsAFile_SaysWhatItWaitsForBesideItsName(self, troubled):
        page = page_of(troubled)

        row = page.split('<a class="tap arow" href="/ledger?ref=later-rows-1">')[1].split("</a>")[0]
        assert '<span class="a-flag">Statement wanted</span>' in row

    def test_Rows_OnTheClearWorld_AskNothingAndSayNothingBesideTheName(self, clear):
        page = page_of(clear)

        assert 'class="a-flag' not in page


def with_more_items(base):
    """The troubled world with findings added, so that the bands outnumber five items."""
    extra = (
        *(AttentionItem("spaces", HOUSEKEEPING, f"extra {n}", "r", "/spaces") for n in range(3)),
        AttentionItem("disk", SOON, "disk", "r", "/admin"),
    )
    return replace(base, items=(*base.items, *extra))


class TestTheFirstScreenIsInOrder:
    def test_Page_SaysVerdictThenEvidenceThenThingsToDoThenAccounts(self, troubled):
        page = page_of(troubled)

        names = ('id="verdict"', 'class="evidence"', 'class="todos"', 'id="accounts"')
        positions = [page.index(name) for name in names]
        assert positions == sorted(positions)

    def test_Page_SaysNoVerificationCountThatTheBarsBeneathItAlreadySay(self, troubled):
        page = page_of(troubled)

        assert "add up to their latest known balance" not in page
        assert 'id="status"' not in page

    def test_Evidence_OnTheTroubledWorld_NamesTheChecksThatRanAndFoldsTheRest(self, troubled):
        page = page_of(troubled)

        summary = re.search(r'<details class="evidence"><summary>(.*?)</summary>', page)
        assert summary is not None
        assert summary.group(1) == f"{troubled.checks_total} checks ran at 15:02"
        assert "Last scheduled cycle" not in summary.group(1)
        assert "No scheduled cycle recorded." in page.split('class="evidence"')[1].split(
            "</details>"
        )[0]

    def test_ThingsToDo_WithFewerThanFourOpen_FoldTheRestBehindACountOfWhatIsWhenConvenient(
        self, troubled
    ):
        page = page_of(troubled)

        # Two faults are always open, so one when-convenient thing fits and two are folded.
        assert "<summary>2 more when convenient</summary>" in page

    def test_ThingsToDo_WithManyUrgentOnes_NeverFoldAFaultAndFoldEveryOtherThing(self, troubled):
        page = page_of(with_more_items(troubled))

        assert "<summary>6 more when convenient</summary>" in page
        opened = page.split("<summary>6 more when convenient</summary>")[0]
        assert opened.count('class="todo now"') == 2
        assert opened.count('class="todo soon"') == 1


def push(stamp: str, *, ok: bool = True) -> dict[str, object]:
    return {"kind": "push", "ok": ok, "finished_at": stamp}


def audit(stamp: str, *, ok: bool = True, differing: int = 0, total: int = 3) -> dict[str, object]:
    accounts = [
        {"account_id": f"a{n}", "missing": 2 if n < differing else 0} for n in range(total)
    ]
    return {"kind": "audit", "ok": ok, "finished_at": stamp, "accounts": accounts}


PUSHED = "2026-10-01T09:30:00Z"
AFTER = "2026-10-01T09:45:00Z"
BEFORE = "2026-10-01T09:00:00Z"


class TestTheActualLine:
    """The line says the Actual page's own verdict: one reading of the results, not two."""

    @pytest.mark.parametrize(
        ("results", "word", "css", "begins"),
        [
            ([], "no push", "pill-warn", "Nothing has been pushed yet."),
            ([push(PUSHED, ok=False)], "push failed", "pill-bad", "The last push failed."),
            (
                [push(PUSHED)],
                "not checked",
                "pill-warn",
                "Actual has not been checked since the last push. A push applied at "
                "2026-10-01 09:30. No audit has been run since.",
            ),
            (
                [push(PUSHED), audit(BEFORE)],
                "not checked",
                "pill-warn",
                "Actual has not been checked since the last push. A push applied at "
                "2026-10-01 09:30. The newest audit (2026-10-01 09:00) ran before it",
            ),
            (
                [push(PUSHED), audit(AFTER, ok=False)],
                "audit failed",
                "pill-bad",
                "The last audit failed.",
            ),
            (
                [push(PUSHED), audit(AFTER, differing=2)],
                "differs",
                "pill-bad",
                "Actual differs from obdi in 2 accounts.",
            ),
            (
                [push(PUSHED), audit(AFTER)],
                "agrees with obdi",
                "pill-ok",
                "Actual agrees with obdi. The audit of 2026-10-01 09:45, after the push applied "
                "2026-10-01 09:30, found no differences in 3 accounts.",
            ),
        ],
    )
    def test_Line_ForTheseResults_SaysTheActualPagesVerdict(self, results, word, css, begins):
        from obdi.web_actual import current_verdict
        from obdi.web_overview import actual_line

        line = actual_line(lambda: results, NOW_TIME)
        verdict = current_verdict(lambda: results, now=NOW_TIME)

        assert (line.word, line.css) == (word, css)
        assert line.sentence.startswith(begins)
        assert line.sentence == f"{verdict.headline}. {verdict.detail}"
        assert line.href == "/actual"

    def test_Line_WhenAnAuditReadActualAfterAFailedPush_SaysWhatTheAuditFound(self):
        """An earlier form of this line read the results for itself and said "push failed"
        here, while the Actual page said they agree: the audit had read Actual after it."""
        from obdi.web_overview import actual_line

        line = actual_line(lambda: [push(BEFORE), push(PUSHED, ok=False), audit(AFTER)], NOW_TIME)

        assert line.word == "agrees with obdi"

    def test_Line_WhenAPushFailedAfterTheNewestAudit_SaysThePushFailed(self):
        from obdi.web_overview import actual_line

        line = actual_line(lambda: [push(BEFORE), audit(PUSHED), push(AFTER, ok=False)], NOW_TIME)

        assert line.word == "push failed"

    def test_Line_WhenActualIsNotConfigured_SaysSoAsTheActualPageDoes(self):
        """Read without the page's other hooks, the line said "Nothing has been pushed yet"
        of an instance the Actual page called not configured."""
        from obdi.web_overview import actual_line

        line = actual_line(lambda: [], NOW_TIME, configured=lambda: False)

        assert (line.word, line.css) == ("not configured", "pill-quiet")
        assert line.sentence.startswith("Actual is not configured on this instance.")

    def test_Line_WhenActualIsConfiguredAndNothingIsPushed_SaysNothingIsPushed(self):
        from obdi.web_overview import actual_line

        line = actual_line(lambda: [], NOW_TIME, configured=lambda: True)

        assert line.word == "no push"

    def test_Chips_ForEveryStateTheVerdictCanBeIn_HaveAWordOfTheirOwn(self):
        from obdi.actual_verdict import State
        from obdi.web_overview import _ACTUAL_CHIPS

        assert {state.value for state in State} == set(_ACTUAL_CHIPS)

    def test_Line_WhenNotWiredOrUnreadable_SaysSoInsteadOfInventingAnAnswer(self):
        from obdi.web_overview import actual_line

        def unreadable():
            raise RuntimeError("secret detail")

        assert actual_line(None, NOW_TIME).word == "not wired"
        failed = actual_line(unreadable, NOW_TIME)
        assert (failed.word, failed.css) == ("unreadable", "pill-bad")
        assert "secret detail" not in failed.sentence

    def test_Line_WhenThePushWasOnAnotherDay_NamesTheDateAndNeverABareZone(self):
        from obdi.web_overview import actual_line

        line = actual_line(lambda: [push("2026-09-29T22:05:00Z")], NOW_TIME)

        assert "2026-09-29 22:05" in line.sentence
        assert "Z" not in line.sentence


class TestTheDataLine:
    def test_Line_WhenTheSchedulerFinishedAndNoFeedIsLate_IsCurrentWithTheTime(self, clear):
        from obdi.web_overview import data_line

        line = data_line(clear, lambda: {"at": "2026-10-01T13:50:00Z"}, NOW_TIME)

        assert (line.word, line.css) == ("current", "pill-ok")
        assert line.sentence == (
            "Last scheduled cycle finished 14:50; no feed is stale or silent."
        )

    def test_Line_WhenFeedsAreStaleOrSilent_CountsThemFromTheChecksAlreadyRun(self, clear):
        from obdi.web_overview import data_line

        items = tuple(
            AttentionItem(kind, NOW, "m", "r", "/x", accounts=("a",))
            for kind in ("silent-feed", "stale-feed", "stale-feed")
        )

        beat = {"at": "2026-10-01T13:50:00Z"}

        line = data_line(replace(clear, items=items), lambda: beat, NOW_TIME)

        assert (line.word, line.css) == ("silent", "pill-bad")
        assert line.sentence.endswith("1 feed silent and 2 stale.")

    def test_Line_WhenNoCycleIsRecorded_IsUnprovenAndSaysSo(self, clear):
        from obdi.web_overview import data_line

        assert data_line(clear, None, NOW_TIME).word == "unknown"
        assert data_line(clear, None, NOW_TIME).sentence == "No scheduled cycle recorded."

    def test_Line_WhenNothingWasChecked_SaysSoRatherThanCurrent(self):
        from obdi.web_overview import data_line

        assert data_line(None, None, NOW_TIME).sentence == "Nothing was checked."


class TestFlaggedTransactionsAreSomethingToDecide:
    def test_Page_WhenTransactionsAreFlagged_SaysThemOnceAsAThingToDecideWithItsControl(
        self, tmp_path
    ):
        db = tmp_path / "h.sqlite3"
        with Store(db) as store:
            build_household(store)

        overview = overview_of(db)
        page = page_of(overview)

        assert [i.kind for i in overview.notes] == ["review"]
        assert (
            "1 transaction is flagged for a decision that could not be made automatically"
            in page
        )
        assert page.count("flagged for a decision") == 1
        assert 'href="/review-flags">Decide</a>' in page
        assert "matcher" not in page.lower()

    def test_Page_WhenNothingIsFlagged_HasNoDecisionToMake(self, clear):
        assert "Decide" not in page_of(clear)
