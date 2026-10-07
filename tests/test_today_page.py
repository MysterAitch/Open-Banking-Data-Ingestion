"""Today in its three states, over invented households whose every answer is decided here.

An ordinary day (files to fetch, nothing wrong), a bad day (a statement that does not add up and a
consent running out), and a clear day (nothing to do). The page is parsed with a DOM and nothing
is asserted by a pattern over its markup. Today is 2026-10-05, so the shared scale begins on
2025-10-06 and one day is 100 / 365 percent of a bar.
"""

from __future__ import annotations

import re
from dataclasses import replace
from datetime import UTC, date, datetime

from obdi.ingest.rebuild_hold import RebuildHold
from obdi.read.fetch_gaps import AccountOutlook, Basis, FetchGap, FetchReport, GapKind
from obdi.read.overview import (
    CURRENT,
    EMPTY,
    FILE_ONLY,
    HOUSEKEEPING,
    REBUILDING,
    SOON,
    AccountOverview,
    AttentionItem,
    Overview,
)
from obdi.verify.agreement import (
    AGREES,
    HELD_UNMET,
    NONE,
    Agreement,
    HeldBack,
    Known,
    Standing,
)
from obdi.verify.standing_data import AccountStanding
from obdi.web_overview import overview_html
from page_dom import Node, elements, inside, parse

TODAY = date(2026, 10, 5)
NOW = datetime(2026, 10, 5, 8, 12, tzinfo=UTC)


def d(text: str) -> date:
    return date.fromisoformat(text)


def standing(
    *,
    through: str | None,
    known_from: str | None = None,
    locked: str | None = None,
    held: HeldBack | None = None,
    state: str = AGREES,
    lockable_days: tuple[str, ...] = (),
) -> AccountStanding:
    known = tuple(Known(d(day), "bank", "met", 1) for day in lockable_days)
    own = Agreement(
        state,
        d(known_from) if known_from else None,
        d(through) if through else None,
        2 if through else 0,
        1 if through else 0,
        d(through) if through else None,
        held,
        True,
        (),
        tested=tuple(d(day) for day in lockable_days),
        tested_known=known,
        chain_tested=tuple(d(day) for day in lockable_days),
    )
    return AccountStanding(Standing(own, None), d(locked) if locked else None, False)


def account(
    ref: str,
    label: str,
    *,
    first: str | None,
    newest: str | None = "2026-10-05",
    held: AccountStanding | None = None,
    parent: str | None = None,
    state: str = CURRENT,
    closed: str | None = None,
    balance_only: bool = False,
    opened: str | None = None,
) -> AccountOverview:
    return AccountOverview(
        ref=ref,
        label=label,
        sources=("starling",),
        rows=40 if first else 0,
        newest=d(newest) if newest and first else None,
        last_asked=NOW,
        state=state,
        bound=None,
        items=0,
        closed=d(closed) if closed else None,
        declared=True,
        standing=held,
        parent=parent,
        first=d(first) if first else None,
        balance_only=balance_only,
        opened=d(opened) if opened else None,
    )


def overview(accounts: tuple[AccountOverview, ...], *items: AttentionItem) -> Overview:
    return Overview(NOW, 19, 19, items, accounts)


def gap(
    ref: str,
    kind: GapKind,
    first: str,
    last: str,
    *,
    closings: tuple[str, ...] = (),
    basis: Basis = Basis.STATED,
) -> FetchGap:
    return FetchGap(
        ref,
        kind,
        d(first),
        d(last),
        basis,
        "",
        "why",
        probably=len(closings) or None,
        closings=tuple(d(c) for c in closings),
        rows_to=TODAY,
    )


def report(*gaps: FetchGap) -> FetchReport:
    by: dict[str, list[FetchGap]] = {}
    for one in gaps:
        by.setdefault(one.ref if hasattr(one, "ref") else one.account, []).append(one)
    return FetchReport(tuple(AccountOutlook(ref, tuple(g)) for ref, g in by.items()), TODAY)


def page(of: Overview, fetch: FetchReport | None, **options: object) -> Node:
    html = overview_html(lambda fresh: of, now=NOW, fetch=(lambda day: fetch) if fetch else None)
    return parse(html)


def by_class(root: Node, tag: str, name: str) -> list[Node]:
    return [e for e in elements(root, tag) if name in e.classes]


def texts(root: Node, tag: str, name: str) -> list[str]:
    return [e.text() for e in by_class(root, tag, name)]


def row(root: Node, ref: str) -> Node:
    found = [
        e
        for e in elements(root, "a")
        if "arow" in e.classes and e.attrs.get("href") == f"/ledger?ref={ref}"
    ]
    assert found, f"no row for {ref}"
    return found[0]


def cells(root: Node) -> dict[str, str]:
    """The bar of a row: each fill's class to its inline position, as written."""
    return {
        next(iter(c.classes)): c.attrs["style"]
        for c in elements(root, "i")
        if "style" in c.attrs
    }


EVERYDAY = standing(
    through="2026-07-10", known_from="2025-02-01", locked="2026-04-10",
    lockable_days=("2025-02-01", "2026-04-10"),
)
JOINT = standing(through="2026-09-17", known_from="2025-03-01", lockable_days=("2026-09-17",))


def ordinary() -> tuple[Overview, FetchReport]:
    accounts = (
        account("everyday", "Everyday card", first="2025-01-01", held=EVERYDAY),
        account("joint", "Joint current", first="2025-01-01", held=JOINT),
        account(
            "bills",
            "Bills pot",
            first="2025-01-01",
            held=standing(through=None, state=NONE),
            parent="joint",
        ),
        account("old", "Old store card", first="2024-01-01", state="archived", closed="2026-03-01"),
        account("pot", "Holiday pot", first=None, state=EMPTY),
    )
    due = AttentionItem(
        "statement-due", HOUSEKEEPING, "2 accounts have rows after their last known balance.",
        "Upload.", "/gaps", accounts=("everyday", "joint"),
    )
    gaps = report(
        gap("everyday", GapKind.NEWER_STATEMENT, "2026-07-11", "2026-10-05",
            closings=("2026-08-10", "2026-09-10")),
        gap("joint", GapKind.NEWER_STATEMENT, "2026-09-18", "2026-10-05"),
    )
    return overview(accounts, due), gaps


def bad() -> tuple[Overview, FetchReport]:
    held = standing(
        through="2026-08-18", known_from="2025-03-01", state=HELD_UNMET,
        held=HeldBack(HELD_UNMET, d("2026-09-02"), ("starling",), ""),
    )
    accounts = (
        account("everyday", "Everyday card", first="2025-01-01", held=EVERYDAY),
        account("joint", "Joint current", first="2025-01-01", held=held),
    )
    items = (
        AttentionItem(
            "statement-fault",
            1,
            "Joint current: The statement closing on 2026-09-30 does not add up.",
            "Open.",
            "/ledger?ref=joint#opening",
            accounts=("joint",),
        ),
        AttentionItem(
            "consent", SOON, "Brook Bank's consent runs out on 2026-10-08.", "Reconnect.",
            "/connections", accounts=("joint",),
        ),
    )
    gaps = report(
        gap("everyday", GapKind.NEWER_STATEMENT, "2026-07-11", "2026-10-05"),
        gap("everyday", GapKind.HOLE_BETWEEN, "2026-04-11", "2026-05-10"),
    )
    return overview(accounts, *items), gaps


def clear() -> tuple[Overview, FetchReport]:
    done = standing(through="2026-09-30", known_from="2025-02-01", locked="2026-09-30")
    accounts = (
        account("everyday", "Everyday card", first="2025-01-01", held=done),
        account("rainy", "Rainy day saver", first="2025-01-01", held=done, state=FILE_ONLY),
    )
    return overview(accounts), report()


class TestAnOrdinaryDay:
    def root(self) -> Node:
        of, gaps = ordinary()
        return page(of, gaps)

    def test_Verdict_FourFilesWantedAndNothingWrong_SaysNoFaultsAndCountsThemAsThings(self) -> None:
        verdict = by_class(self.root(), "p", "verdict")[0]

        assert verdict.text() == "No faults. 2 things when convenient."
        assert "ok" in verdict.classes

    def test_ThingsToDo_TwoConsecutiveStatementsForOneAccount_AreOneRowSayingHowManyFiles(
        self,
    ) -> None:
        root = self.root()

        assert texts(root, "p", "todo-what") == [
            "Upload 2 statements",
            "Upload the statement covering 2026-09-18 to 2026-10-05",
        ]
        everyday, joint = texts(root, "p", "todo-why")
        # The line says the account, the days with how long they are (and, where the cadence
        # says two files, how many), and when the first fell due, and nothing else.
        assert everyday == (
            "Everyday card · 2026-07-11 to 2026-09-10 (2 months, 2 statements)"
            " · due since 2026-08-10 (8 weeks ago)"
        )
        assert joint == (
            "Joint current · 2026-09-18 to 2026-10-05 (3 weeks)"
            " · due since 2026-09-18 (2 weeks ago)"
        )

    def test_ThingsToDo_TheLengthOfTheDays_IsAMutedSmallSpanNeverTheOrangeBoldAge(self) -> None:
        root = self.root()

        lengths = [span for span in elements(root, "span") if "span-words" in span.classes]
        assert [span.text() for span in lengths] == [
            "(2 months, 2 statements)",
            "(3 weeks)",
        ]
        assert all("age" not in span.classes for span in lengths)
        controls = [a.text() for e in by_class(root, "li", "todo") for a in elements(e, "a")]
        assert controls == ["Upload", "Upload"]

    def test_ThingsToDo_AGapThatIsNotAdjacent_StaysItsOwnRowWithItsShortReason(self) -> None:
        of, _ = ordinary()
        apart = report(
            gap("everyday", GapKind.NEWER_STATEMENT, "2026-07-11", "2026-10-05",
                closings=("2026-08-10", "2026-09-10")),
            gap("everyday", GapKind.HOLE_BETWEEN, "2026-04-11", "2026-05-10"),
        )
        root = page(of, apart)

        assert texts(root, "p", "todo-what") == [
            "Upload 2 statements",
            "Upload the statement covering 2026-04-11 to 2026-05-10 (a month)",
        ]
        assert texts(root, "p", "todo-why")[1] == (
            "Everyday card · No statement held covers these days (5 months ago)"
        )

    def test_ThingsToDo_TheFirstControlLeads_AndTheOthersAreSecondary(self) -> None:
        root = self.root()

        buttons = [a for e in by_class(root, "li", "todo") for a in elements(e, "a")]
        assert [("secondary" in a.classes) for a in buttons] == [False, True]

    def test_Rows_EachLiveAccountIsOneRow_AndTheArchivedOneIsFolded(self) -> None:
        root = self.root()

        links = [
            a
            for a in elements(root, "a")
            if a.attrs.get("class") == "tap arow" and not inside(a, "details")
        ]
        assert [a.attrs["href"] for a in links] == [
            "/ledger?ref=everyday",
            "/ledger?ref=joint",
            "/ledger?ref=bills",
            "/ledger?ref=pot",
        ] or sorted(a.attrs["href"] for a in links) == sorted(
            f"/ledger?ref={ref}" for ref in ("everyday", "joint", "bills", "pot")
        )
        folds = [s.text() for s in elements(root, "summary")]
        assert "1 archived account" in folds

    def test_Rows_ASpaceSitsUnderItsParentAndSaysItIsTestedWithIt(self) -> None:
        root = self.root()

        spaces = [li for li in elements(root, "li") if "space" in li.classes]
        assert len(spaces) == 1
        said = texts(spaces[0], "span", "a-trust")
        assert said == ["A Space of Joint current, tested with it and not on its own."]
        order = [a.attrs["href"] for a in elements(root, "a") if a.attrs.get("class") == "tap arow"]
        assert order.index("/ledger?ref=bills") == order.index("/ledger?ref=joint") + 1

    def test_Bars_ASpaceTestedWithItsParent_IsDrawnAsTheFamilysStretches(self) -> None:
        root = self.root()

        space, parent = cells(row(root, "bills")), cells(row(root, "joint"))
        # The family adds up to 2026-09-17, which is the parent's own agreement here; nothing is
        # locked or wanted for the Space itself.
        assert space["b-adds"] == parent["b-adds"]
        assert "b-lock" not in space and "b-want" not in space

    def test_Rows_ABalanceOnlyAccount_SaysItsBalanceIsStatedByHandAndDrawsNoFill(self) -> None:
        of = overview(
            (account("bonds", "Premium bonds", first=None, state=EMPTY, balance_only=True),)
        )
        root = page(of, None)

        bonds = row(root, "bonds")
        assert texts(bonds, "span", "a-trust") == ["Its balance is stated by hand."]
        assert not list(elements(bonds, "i"))

    def test_Rows_AnAccountWaitingForFiles_SaysWhatItWaitsForBesideItsName(self) -> None:
        root = self.root()

        # Joint's file has been wanted since 2026-09-18 (17 days: 2 whole weeks); Everyday's since
        # 2026-08-10, when its first statement fell due (56 days: 8 whole weeks).
        assert texts(row(root, "joint"), "span", "a-flag") == ["Statement wanted (2 weeks ago)"]
        assert texts(row(root, "everyday"), "span", "a-flag") == [
            "Statement wanted (8 weeks ago)"
        ]

    def test_Rows_EachSaysHowFarItIsTrustedInTheTrustSentence(self) -> None:
        root = self.root()

        assert texts(row(root, "everyday"), "span", "a-trust") == [
            "Locked in to 2026-04-10. Adds up to the known balances to 2026-07-10."
        ]
        assert texts(row(root, "pot"), "span", "a-trust") == ["Nothing held yet."]

    def test_Bars_AnAccountLockedAddingUpAndWaiting_DrawsEachRungOnTheSharedScale(self) -> None:
        bar = row(self.root(), "everyday")
        drawn = cells(bar)

        # Locked 2025-10-06 (the window's first day) to 2026-04-10 is 187 of 365 days.
        assert drawn["b-lock"] == "left:0.00%;width:51.23%"
        # Adds up 2026-04-11 to 2026-07-10: 91 days, from day 187.
        assert drawn["b-adds"] == "left:51.23%;width:24.93%"
        # Waiting 2026-07-11 to today: 87 days, from day 278.
        assert drawn["b-held"] == "left:76.16%;width:23.84%"
        assert [i for i in elements(bar, "i") if "b-edge" in i.classes], (
            "history before the twelve months is held"
        )

    def test_Bars_HistoryBeyondTheLeftEdge_IsAnArrowNotASlice(self) -> None:
        """A vertical slice at the edge reads as a very narrow stretch of some rung; an arrow
        says something lies beyond the edge. The key names it as an arrow, and the stylesheet
        draws the edge cell as a chevron rather than a filled bar."""
        from obdi.stylesheet import SERVED_STYLESHEET
        from obdi.trust_bar import key_html

        assert "An arrow at the left edge" in key_html()
        assert "tick" not in key_html()
        edge_rules = [
            rule for rule in SERVED_STYLESHEET.split("}") if "b-edge" in rule and "::before" in rule
        ]
        assert edge_rules, "the edge cell has no chevron drawn on it"
        assert any("rotate(45deg)" in rule and "border-left" in rule for rule in edge_rules)

    def test_Bars_TwoAccountsWithTheSameDatesButDifferentHistories_DrawTheSameWidths(self) -> None:
        both = (
            account("long", "Long history", first="2020-01-01", held=EVERYDAY),
            account("short", "Short history", first="2025-01-01", held=EVERYDAY),
        )
        root = page(overview(both), None)

        assert cells(row(root, "long"))["b-lock"] == cells(row(root, "short"))["b-lock"]
        assert cells(row(root, "long"))["b-adds"] == cells(row(root, "short"))["b-adds"]
        assert cells(row(root, "long"))["b-held"] == cells(row(root, "short"))["b-held"]

    def test_Bars_AFileWantedForSomeDays_MarksThoseDaysOnTheBar(self) -> None:
        drawn = cells(row(self.root(), "joint"))

        assert "b-want" in drawn

    def test_Axis_NamesTheMonthsOnceAboveTheList(self) -> None:
        root = self.root()

        # The folded archived list carries its own, which is shown when the fold is opened.
        axes = [a for a in by_class(root, "span", "axis") if not inside(a, "details")]
        assert len(axes) == 1
        assert len([a for a in by_class(root, "span", "axis") if inside(a, "details")]) == 1
        assert [s.text() for s in elements(axes[0], "span")][:3] == ["Oct", "Nov", "Dec"]

    def test_LockLine_AnAccountWithDaysThatAddUpAndAreNotLocked_SaysSoWithoutALockButton(
        self,
    ) -> None:
        root = self.root()

        line = by_class(root, "p", "lockline")
        assert len(line) == 1
        assert "has days that add up and are not locked in" in line[0].text()
        assert not [a for a in elements(root, "a") if "Lock in" in a.text()]
        assert not [b for b in elements(root, "button") if "Lock" in b.text()]
        links = [a.attrs["href"] for a in elements(line[0], "a")]
        assert links == ["/ledger?ref=joint"]


class TestABadDay:
    def root(self) -> Node:
        of, gaps = bad()
        return page(of, gaps)

    def test_Verdict_AFaultASoonAndTwoWhenConvenient_CountsEachBand(self) -> None:
        verdict = by_class(self.root(), "p", "verdict")[0]

        assert verdict.text() == (
            "1 fault to look at now, 1 thing to look at soon, and 2 things when convenient."
        )
        assert "bad" in verdict.classes

    def test_ThingsToDo_AFaultComesFirstWithItsControl_AndNothingUrgentIsFolded(self) -> None:
        root = self.root()

        todos = by_class(root, "li", "todo")
        assert [t.classes & {"now", "soon"} for t in todos][:2] == [{"now"}, {"soon"}]
        first = todos[0]
        assert texts(first, "p", "todo-what") == ["Find out why a statement does not add up"]
        (control,) = elements(first, "a")
        assert control.attrs["href"] == "/ledger?ref=joint#opening"
        assert control.text() == "See the statement"
        assert [s.text() for s in elements(root, "summary")].count("1 more when convenient") == 1

    def test_Rows_TheAccountThatDoesNotAddUp_SaysSoInRedAndMarksTheDayOnItsBar(self) -> None:
        joint = row(self.root(), "joint")

        flags = by_class(joint, "span", "a-flag")
        assert [f.text() for f in flags] == ["Does not add up"]
        assert "bad" in flags[0].classes
        assert "b-bad" in cells(joint)
        assert texts(joint, "span", "a-trust") == [
            "Adds up to the known balances to 2026-08-18. Does not add up from 2026-09-02."
        ]

    def test_LockLine_WhileAFaultNeedsLooking_IsNotSaid(self) -> None:
        assert not by_class(self.root(), "p", "lockline")


class TestAClearDay:
    def root(self) -> Node:
        of, gaps = clear()
        return page(of, gaps)

    def test_Verdict_NothingToDo_SaysSoInOneLineAndTheListStops(self) -> None:
        root = self.root()

        verdict = by_class(root, "p", "verdict")[0]
        assert verdict.text() == "Everything checked is in order."
        assert not by_class(root, "ul", "todos")
        assert not by_class(root, "span", "a-flag")

    def test_Evidence_OnAClearDay_StillSaysTheChecksRan(self) -> None:
        summary = next(iter(elements(self.root(), "summary")))

        assert summary.text() == "19 checks ran at 09:12", "08:12 UTC is 09:12 BST"

    def test_Rows_AccountsLockedIn_SayWhereAndNothingElse(self) -> None:
        said = texts(row(self.root(), "everyday"), "span", "a-trust")

        assert said == ["Locked in to 2026-09-30."]


class TestNothingIsSaidTwice:
    def test_Page_OnAnOrdinaryDay_RepeatsNoTitleNoWhyAndNoTrustSentence(self) -> None:
        of, gaps = ordinary()
        root = page(of, gaps)

        said = [
            *texts(root, "p", "todo-what"),
            *texts(root, "p", "todo-why"),
            *texts(root, "span", "a-trust"),
        ]
        assert len(said) == len(set(said))

    def test_Page_OnABadDay_SaysTheFaultOnceAsAToDoAndOnceAsARowFlag(self) -> None:
        of, gaps = bad()
        root = page(of, gaps)

        text = root.text()
        assert text.count("The statement closing on 2026-09-30 does not add up") == 1
        assert text.count("Find out why a statement does not add up") == 1

    def test_Page_Always_HoldsNoRetiredPhraseAndNoBareCheckedLabel(self) -> None:
        from obdi.core.page_words import RETIRED_ON_PAGES

        for build in (ordinary, bad, clear):
            of, gaps = build()
            text = page(of, gaps).text().lower()
            for phrase in RETIRED_ON_PAGES:
                assert phrase not in text, phrase
            assert "not checked" not in text and "checked to" not in text


class TestARebuildIsRunning:
    """While the derived data is replayed no account is checked: Today says so once, above the
    accounts, and the accounts that hold transactions say nothing of their own."""

    def root(self) -> Node:
        accounts = (
            account("everyday", "Everyday card", first="2024-01-01", held=EVERYDAY),
            account("joint", "Joint current", first="2025-03-01", held=JOINT),
            account("bonds", "Premium bonds", first=None, state=EMPTY, balance_only=True),
            account("pot", "Holiday pot", first=None, state=EMPTY),
        )
        # The overview marks every account that is not archived, held or empty alike.
        paused = tuple(replace(a, state=REBUILDING) for a in accounts)
        of = replace(overview(paused), rebuilding=RebuildHold("2026-10-05 08:00"))
        return page(of, None)

    def test_Accounts_DuringARebuild_SayTheChecksArePausedOnceAboveTheList(self) -> None:
        section = next(
            e for e in elements(self.root(), "section") if e.attrs.get("id") == "accounts"
        )

        said = [t for t in texts(section, "p", "paused") if t]
        assert said == ["The checks on these accounts are paused while the rebuild runs."]
        blocks = [c for c in section.children if isinstance(c, Node) and c.tag in ("p", "ul")]
        assert "paused" in blocks[0].classes and "alist" in blocks[1].classes

    def test_Rows_DuringARebuild_DoNotEachSayTheyArePaused(self) -> None:
        root = self.root()

        assert "Paused while the rebuild runs." not in " ".join(
            e.text() for e in by_class(root, "span", "a-trust")
        )
        assert texts(row(root, "everyday"), "span", "a-trust") == [""]
        assert texts(row(root, "joint"), "span", "a-trust") == [""]

    def test_Rows_DuringARebuild_AnAccountHoldingNothingStillSaysWhatKindItIs(self) -> None:
        root = self.root()

        assert texts(row(root, "bonds"), "span", "a-trust") == ["Its balance is stated by hand."]
        assert texts(row(root, "pot"), "span", "a-trust") == ["Nothing held yet."]

    def test_Accounts_WhenNoRebuildRuns_SayNothingOfBeingPaused(self) -> None:
        of, gaps = ordinary()

        assert not by_class(page(of, gaps), "p", "paused")


class TestArchivedAccountsDrawTheirBars:
    """Known answers, written before the first run. Today is 2026-10-05, so the shared twelve
    months begin 2025-10-06 and a day is 100 / 365 percent of a bar.

        "ancient"  held 2022-01-10 to 2024-10-05, closed 2024-10-05: nothing of it lies in the
                   shared months, so its bar runs over its own life (every held day fills it, from
                   0 to 100 percent) and its two end dates are printed under it, once;
        "recent"   held 2025-11-01 to 2026-03-01, closed 2026-03-01: inside the shared months,
                   which are kept, so its held stretch sits at 26 days in and runs 121 days
                   (left 7.12 percent, width 33.15 percent) and no end dates are printed;
        the fold itself stays closed whatever it holds.
    """

    def root(self) -> Node:
        of, fetch = ordinary()
        accounts = (
            *of.accounts,
            account(
                "ancient", "Ancient loan", first="2022-01-10", newest="2024-10-05",
                state="archived", closed="2024-10-05",
            ),
            account(
                "recent", "Recent card", first="2025-11-01", newest="2026-03-01",
                state="archived", closed="2026-03-01",
            ),
        )
        return page(replace(of, accounts=accounts), fetch)

    def fold(self, root: Node) -> Node:
        return next(e for e in elements(root, "details") if "archived account" in e.text())

    def archived_row(self, root: Node, ref: str) -> Node:
        return next(
            a for a in elements(self.fold(root), "a") if a.attrs.get("href") == f"/ledger?ref={ref}"
        )

    def test_Fold_ByDefault_IsClosedAndCountsEveryArchivedAccount(self) -> None:
        fold = self.fold(self.root())

        assert "open" not in fold.attrs
        assert next(elements(fold, "summary")).text() == "3 archived accounts"

    def test_EachArchivedAccount_IsTheSameRowAsALiveOne_NameSentenceAndBar(self) -> None:
        root = self.root()

        for ref, name in (("old", "Old store card"), ("ancient", "Ancient loan"),
                          ("recent", "Recent card")):
            archived = self.archived_row(root, ref)
            assert "arow" in archived.classes
            assert texts(archived, "span", "a-name") == [name]
            assert len(texts(archived, "span", "a-trust")) == 1
            assert len(by_class(archived, "span", "bar")) == 1

    def test_AccountClosedTwoYearsAgo_IsDrawnOverItsOwnLifeWithItsEndsLabelledOnce(self) -> None:
        archived = self.archived_row(self.root(), "ancient")

        first = next(i for i in elements(archived, "i") if "style" in i.attrs)
        assert first.attrs["style"].startswith("left:0.00%;width:100.00%")
        ends = by_class(archived, "span", "a-ends")
        assert [e.text() for e in ends] == ["2022-01-10 2024-10-05"]

    def test_AccountClosedInsideTheTwelveMonths_KeepsTheSharedScaleAndPrintsNoEnds(self) -> None:
        archived = self.archived_row(self.root(), "recent")

        assert cells(archived)["b-held"] == "left:7.12%;width:33.15%"
        assert not by_class(archived, "span", "a-ends")

    def test_TheSharedMonthsAxis_IsPrintedOnceAboveTheFoldedList(self) -> None:
        fold = self.fold(self.root())

        assert len(by_class(fold, "div", "axis-row")) == 1

    def test_ArchivedAccountsSentence_SaysWhenItWasArchived(self) -> None:
        archived = self.archived_row(self.root(), "ancient")

        assert texts(archived, "span", "a-trust")[0].startswith("Archived 2024-10-05.")

    def test_AccountWithAStatedOpening_LifeBeginsThere_NotAtAStatementsEarlierPeriod(self) -> None:
        """A closed loan's first document printed a period beginning four months before the
        loan was opened (a calendar-year statement), so its bar began on 1 January though the
        account opened in May. Where an opening day is stated, the life begins there."""
        of, fetch = ordinary()
        accounts = (
            *of.accounts,
            account(
                "loan", "Old loan", first="2022-01-01", newest="2024-10-05",
                state="archived", closed="2024-10-05", opened="2022-05-04",
            ),
        )
        archived = self.archived_row(page(replace(of, accounts=accounts), fetch), "loan")

        ends = by_class(archived, "span", "a-ends")
        assert [e.text() for e in ends] == ["2022-05-04 2024-10-05"]


class TestNoFigureReachesToday:
    def test_Page_InEveryState_ShowsNoAmountOrCurrencySymbol(self) -> None:
        for build in (ordinary, bad, clear):
            of, gaps = build()
            text = page(of, gaps).text()
            assert not re.search(r"[£$€]", text)
            assert not re.search(r"\b\d+\.\d\d\b", text)

    def test_Page_WhenTheOverviewCouldNotBeBuilt_SaysNothingWasCheckedAndHoldsNoControlToFollow(
        self,
    ) -> None:
        def boom(fresh: bool) -> Overview:
            raise RuntimeError("secret detail")

        root = parse(overview_html(boom, now=NOW))

        assert by_class(root, "p", "verdict")[0].text() == "Nothing was checked."
        assert "secret detail" not in root.text()
        assert not by_class(root, "ul", "todos")
