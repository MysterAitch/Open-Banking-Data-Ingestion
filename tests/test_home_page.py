"""The home page's verdict, status lines, bands, and account rows, with answers decided up front.

The household is `home_world`: twenty accounts built through the real doors, whose standings are
known from their construction. What is asserted is what a person reads, and the one place each
sentence is decided (`verdict_of`, `row_reading`, `link_words`) is also held to a table.
"""

from __future__ import annotations

import re
from dataclasses import replace
from datetime import UTC, date, datetime

import pytest

import home_world as world
from obdi.agreement import standing_of
from obdi.balance_anchors import effective_opening, record_stated_anchor
from obdi.movement_completeness import MovementCompleteness
from obdi.overview import (
    _KINDS,
    HOUSEKEEPING,
    INFORMATION,
    NOW,
    SOON,
    AttentionItem,
    build_overview,
)
from obdi.protection import press
from obdi.store import Store
from obdi.web_overview import (
    arrange,
    band_phrase,
    link_words,
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
        "labels": {},
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

    def test_Verdict_OnTheTroubledWorld_CountsTheItemsBeneathIt(self, troubled):
        faults = [i for i in troubled.attention if i.severity == NOW]
        later = [i for i in troubled.attention if i.severity == HOUSEKEEPING]

        assert len(faults) == 2, "the two held-back accounts, each a fault"
        assert len(later) == 1, "the three accounts with rows after their balance, one reminder"
        assert verdict_in(page_of(troubled)) == (
            "2 faults to look at now and 1 thing when convenient."
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
    "statement-due": HOUSEKEEPING,
}

#: The kinds whose link goes to the admin page, so that the default is a decision and not a gap.
ADMIN_KINDS = {
    "rebuild-running",
    "rebuild:abandoned",
    "rebuild:empty",
    "check-failed",
    "rebuild-problems",
    "disk",
}


class TestEveryKindHasABandAndALink:
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
    def test_LinkWords_ForEveryKind_SaysWhereItGoesAndNeverABareOpen(self, kind):
        item = AttentionItem(kind, 1, "m", "r", "/x", accounts=("a", "b"))

        said = link_words(item, lambda ref: "Santander CC")

        assert said.startswith("Open ") and said != "Open"
        assert (said == "Open the admin page") == (kind in ADMIN_KINDS)

    def test_LinkWords_ForAnItemAboutOneAccount_NamesItsLedger(self):
        ledger = AttentionItem("agreement-lapsed", 1, "m", "r", "/ledger", accounts=("a",))
        several = AttentionItem("agreement-lapsed", 1, "m", "r", "/ledger", accounts=("a", "b"))
        report = AttentionItem("balance", 1, "m", "r", "/x", accounts=("a",))

        assert link_words(ledger, lambda ref: "Santander CC") == "Open Santander CC's ledger"
        assert link_words(several, lambda ref: "x") == "Open the ledgers"
        assert link_words(report, lambda ref: "x") == "Open the balance reconciliation"

    def test_LinkWords_ForTheMovementChecks_IsTheNameThePageGoesBy(self):
        item = AttentionItem("movement-completeness", 1, "m", "r", "/identity-health")

        assert link_words(item, lambda ref: ref) == "Open the movement checks"


class TestAccountRows:
    def test_Row_ForEachKindOfStanding_SaysItsChipAndClause(self, troubled):
        def said(ref):
            reading = row_reading(account(troubled, ref))
            return reading.word, reading.clause

        assert said(world.AGREE[1]) == ("in agreement", "in agreement through 2026-03-20")
        assert said(world.PROTECTED) == (
            "protected",
            "in agreement through 2026-03-20; protected through 2026-03-20",
        )
        assert said(world.LATER_ROWS[0]) == ("in agreement", "in agreement through 2026-03-10")
        assert said(world.HELD[0]) == ("held back", "held back since 2026-03-15")
        assert said(world.UNPROVEN[0]) == ("unproven", "no known balance")

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
        assert reading.clause == "protected span has changed"

    def test_Row_WhenItsDatesContradictEachOther_SaysSoAndThePageStillRenders(
        self, clear, monkeypatch
    ):
        from obdi import web_overview

        drawn = web_overview.build_rail

        def refusing_one(**dates):
            # The one protected account of the household stands in for a contradiction.
            if dates["protected_through"] is not None:
                raise ValueError("known_to is before known_from")
            return drawn(**dates)

        monkeypatch.setattr(web_overview, "build_rail", refusing_one)

        page = page_of(clear)

        assert page.count("No rail is drawn: this account's dates contradict each other.") == 1
        assert page.count("<svg") >= 10, "every other row keeps its rail"

    def test_Row_WhenItsDatesAreConsistent_SaysNothingOfTheRail(self, clear):
        assert "No rail is drawn" not in page_of(clear)

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

        assert "<summary>4 archived Spaces</summary>" in page
        folded = page.split("<summary>4 archived Spaces</summary>")[1].split("</ul></details>")[0]
        for ref in world.ARCHIVED_SPACES:
            assert f'<a class="tap acct-row" href="/ledger?ref={ref}">' in folded
        for ref in world.LIVE_SPACES:
            assert f'<a class="tap acct-row" href="/ledger?ref={ref}">' not in folded

    def test_Counts_OnTheTroubledWorld_AreTheKnownAnswer(self, troubled):
        # Twenty accounts, four of them archived Spaces: sixteen are live. Two are held back,
        # two have no balance, and the other twelve are in agreement.
        assert verification_counts(troubled.accounts) == (16, 12, 2, 2)

    def test_Counts_WhenEveryAccountIsArchivedOrEmpty_AreNone(self, troubled):
        archived = [a for a in troubled.accounts if a.state == "archived"]

        assert len(archived) == 4
        assert verification_counts(archived) == (0, 0, 0, 0)

    def test_Rows_EachLiveAccountDrawsOneProofRailWhoseTextIsItsVerificationSentence(
        self, troubled
    ):
        page = page_of(troubled)

        assert page.count('<svg class="rail"') == world.TWENTY - len(world.ARCHIVED_SPACES)
        def row(ref):
            opening = f'<a class="tap acct-row" href="/ledger?ref={ref}">'
            return page.split(opening)[1].split("</a>")[0]

        assert "Held back from 2026-03-15." in row("held-1")
        assert "No known balance from 2026-03-02 to 2026-10-01." in row("no-balance-1")
        protected = row(world.PROTECTED)
        assert "Protected through 2026-03-20." in protected
        assert "In agreement with the known balances from 2026-03-05 to 2026-03-20." in protected


def with_more_items(base):
    """The troubled world with findings added, so that the bands outnumber five items."""
    extra = (
        *(AttentionItem("spaces", HOUSEKEEPING, f"extra {n}", "r", "/spaces") for n in range(3)),
        AttentionItem("disk", SOON, "disk", "r", "/admin"),
    )
    return replace(base, items=(*base.items, *extra))


class TestTheFirstScreenIsInOrder:
    def test_Page_SaysVerdictThenFourStatusLinesThenAttentionThenAccounts(self, troubled):
        page = page_of(troubled)

        names = ("verdict", "status", "attention", "accounts")
        positions = [page.index(f'id="{name}"') for name in names]
        assert positions == sorted(positions)
        status = page.split('id="status"')[1].split("</ul>")[0]
        assert re.findall(r'<span class="status-label">(.*?)</span>', status) == [
            "Data",
            "Verification",
            "Actual",
            "Position",
        ]
        for target in ("/connections", "/accounts", "/actual", "/position"):
            assert f'class="tap status-row" href="{target}"' in status

    def test_Verification_OnTheTroubledWorld_SaysHowManyAreInAgreementHeldBackAndUnprovable(
        self, troubled
    ):
        assert (
            "12 of 16 accounts in agreement through their latest known balance; "
            "2 held back; 2 cannot be verified."
        ) in page_of(troubled)

    def test_Verification_OnTheClearWorld_SaysOnlyWhatIsStillUnproven(self, clear):
        assert (
            "14 of 16 accounts in agreement through their latest known balance; "
            "2 cannot be verified."
        ) in page_of(clear)

    def test_Attention_WithMoreThanFiveItems_OpensOnlyTheFirstBandAndFoldsTheRestBehindACount(
        self, troubled
    ):
        page = page_of(with_more_items(troubled))

        assert '<h3 class="tier-title">2 faults to look at now</h3>' in page
        assert "<summary>1 thing to look at soon</summary>" in page
        assert "<summary>4 things when convenient</summary>" in page
        assert '<h3 class="tier-title">4 things when convenient</h3>' not in page

    def test_Attention_WithFiveItemsOrFewer_OpensEveryBand(self, troubled):
        page = page_of(troubled)

        assert '<h3 class="tier-title">2 faults to look at now</h3>' in page
        assert '<h3 class="tier-title">1 thing when convenient</h3>' in page
        assert '<details class="tier' not in page


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
    """What the line says of the newest push and the newest audit after it."""

    @pytest.mark.parametrize(
        ("results", "word", "css", "sentence"),
        [
            ([], "no push", "pill-warn", "No push has been recorded."),
            ([push(PUSHED, ok=False)], "push failed", "pill-bad", "The newest push failed 09:30."),
            (
                [push(PUSHED)],
                "never audited",
                "pill-warn",
                "Last push applied 09:30; no audit has ever run.",
            ),
            (
                [push(PUSHED), audit(BEFORE)],
                "audit is older",
                "pill-warn",
                "Last push applied 09:30; the newest audit (09:00) is older than the push.",
            ),
            (
                [push(PUSHED), audit(AFTER, ok=False)],
                "audit failed",
                "pill-bad",
                "Last push applied 09:30; the newest audit failed.",
            ),
            (
                [push(PUSHED), audit(AFTER, differing=2)],
                "differences",
                "pill-bad",
                "Last push applied 09:30; the audit after it found differences in 2 of 3 accounts.",
            ),
            (
                [push(PUSHED), audit(AFTER)],
                "in agreement",
                "pill-ok",
                "Last push applied 09:30; the audit after it found Actual in agreement.",
            ),
        ],
    )
    def test_Line_ForTheseResults_SaysTheStateOfThePushAndTheAuditAfterIt(
        self, results, word, css, sentence
    ):
        from obdi.web_overview import actual_line

        line = actual_line(lambda: results, NOW_TIME)

        assert (line.word, line.css, line.sentence) == (word, css, sentence)
        assert line.href == "/actual"

    def test_Line_WhenAnOlderAuditFollowsANewerFailedPush_StillSaysThePushFailed(self):
        from obdi.web_overview import actual_line

        line = actual_line(lambda: [push(BEFORE), audit(AFTER), push(PUSHED, ok=False)], NOW_TIME)

        assert line.word == "push failed"

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
            "Last scheduled cycle finished 13:50; no feed is stale or silent."
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

        assert data_line(clear, None, NOW_TIME).word == "unproven"
        assert data_line(clear, None, NOW_TIME).sentence == "No scheduled cycle recorded."

    def test_Line_WhenNothingWasChecked_SaysSoRatherThanCurrent(self):
        from obdi.web_overview import data_line

        assert data_line(None, None, NOW_TIME).sentence == "Nothing was checked."


class TestNothingForAPersonToDoIsInformationAndNotAttention:
    def test_Page_WhenTheReviewFlagsHaveNoPageToResolveThem_SaysThemOnceQuietlyBelow(
        self, tmp_path
    ):
        db = tmp_path / "h.sqlite3"
        with Store(db) as store:
            build_household(store)

        overview = overview_of(db)
        page = page_of(overview)

        assert [i.kind for i in overview.notes] == ["review"]
        attention = page.split('id="attention"')[1].split('id="notes"')[0]
        assert "flagged for a decision" not in attention
        notes = page.split('id="notes"')[1].split("</ul>")[0]
        assert (
            "1 transaction is flagged for a decision that could not be made automatically"
            in notes
        )
        assert page.count("flagged for a decision") == 1
        assert "matcher" not in page.lower()

    def test_Page_WhenNothingIsInformation_HasNoNotes(self, clear):
        assert 'id="notes"' not in page_of(clear)
