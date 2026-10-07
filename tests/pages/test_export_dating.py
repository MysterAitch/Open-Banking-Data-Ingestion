"""A Space-blind export's balances are judged with each row at the export's own date.

The invented household opened at nil on 1 September 2026 and is fed by the
bank's own feed and by a CSV export blind to the Spaces. Both list the same
payments, but the export dates some of them a day or two either side of the
feed (settlement against transaction time), and its balance column moves with
every row it lists. Nothing is missing or surplus, so the KNOWN ANSWER is that
every balance the export states is right and every anchor must agree.

The faulty variants each add exactly one defect with a known consequence, recorded
beside it. Differences are stated minus predicted, so a row only the feed counts
(surplus) leaves the stated balance HIGHER than the rows by that amount from its
day on, and a row only the export lists (missing from the count) leaves it LOWER.
Every defect is one change of difference, on the first balance the export states
on or after the row's day.
"""

from __future__ import annotations

import pathlib
from dataclasses import dataclass, replace
from datetime import date

import pytest

from obdi.core.models import Transaction, TransactionStatus
from obdi.ingest.family_anchors import families_of
from obdi.ingest.pipeline import import_file
from obdi.ingest.store import Store
from obdi.pages.web_ledger import render_ledger
from obdi.read.ledger import build_ledger
from obdi.verify.balance_anchors import FAMILY, OPENED, effective_opening
from test_family_anchors import CSV_HEADER, land_evidence, leg
from test_space_attribution import BILLS, FEED, MAIN, MAP, Household, pay


@dataclass(frozen=True)
class Payment:
    """One payment, the day the feed dates it and the day the export dates it."""

    name: str
    minor: int
    feed_day: int
    export_day: int
    #: Paid from the Bills Space, which the export lists as the whole account's movement.
    space: bool = False


#: Money in on the 1st and 2nd, then payments, five of which the export dates
#: differently: later than the feed by one or two days, and earlier by one. Two
#: are Space payments, which is what lets the export's balance be read at all.
DATED_DIFFERENTLY = [
    Payment("Deposit", 80000, 1, 1),
    Payment("Salary", 300000, 2, 2),
    Payment("Cafe", -2500, 4, 5),
    Payment("Rent", -6000, 8, 7, space=True),
    Payment("Grocer", -7000, 8, 7),
    Payment("Gym", -3000, 10, 12),
    Payment("Garage", -9000, 14, 14),
    Payment("Refund", 1500, 15, 14),
    Payment("Water", -5000, 20, 21, space=True),
    Payment("Bonus", 700, 21, 21),
]

#: A small payment on every other day of the month, those on odd days from the
#: Space, so the export states a balance at the end of every day and many of its
#: steps tell the readings apart (one break then does not flip the verdict, as it
#: would not over a real export's hundreds of steps).
PAYMENTS = [
    *DATED_DIFFERENTLY,
    *(
        Payment(f"Day{day}", -(100 + day), day, day, space=day % 2 == 1)
        for day in range(3, 29)
        if day not in {5, 7, 12, 14, 21}
    ),
]
STATED_DAYS = 28
#: Every day's balance, and the nil one before the first row.
STATED_ANCHORS = STATED_DAYS + 1

#: Transfers between main and Bills, a leg in each and never in the export.
TRANSFER_DAYS = [3, 12, 18]
TRANSFER_MINOR = 4000


def feed_rows(payments: list[Payment]) -> list[Transaction]:
    rows = [
        pay(BILLS if p.space else MAIN, FEED, f"f-{p.name}", p.minor, p.feed_day, p.name)
        for p in payments
    ]
    for day in TRANSFER_DAYS:
        rows.append(pay(MAIN, FEED, f"to-{day}", -TRANSFER_MINOR, day, "To Bills", internal=True))
        rows.append(pay(BILLS, FEED, f"in-{day}", TRANSFER_MINOR, day, "From Main", internal=True))
    return rows


def export_lines(payments: list[Payment]) -> list[str]:
    """The export in its own date order, its balance moving with every row."""
    balance = 0
    lines = [CSV_HEADER]
    for item in sorted(payments, key=lambda p: p.export_day):
        balance += item.minor
        lines.append(
            f"{item.export_day:02}/09/2026,{item.name},{item.name},FASTER PAYMENT,"
            f"{item.minor / 100:.2f},{balance / 100:.2f},"
        )
    return lines


def build(
    tmp_path: pathlib.Path,
    *,
    payments: list[Payment] | None = None,
    feed_only: list[Transaction] | None = None,
    newest_first: bool = False,
    feed_last: bool = True,
    created: str | None = None,
) -> Store:
    """The feed and the export both hold `payments`; `feed_only` rows only the feed holds.

    The sighting that arrives last sets the date a merged row is stored under,
    and the bank's feed is pulled again every day, so by default it is the feed's.
    `created` is the creation instant the provider states for the account.
    """
    held = PAYMENTS if payments is None else payments
    store = Store(tmp_path / "household.sqlite3")
    home = Household(store, MAP)
    if created is None:
        land_evidence(store)
    else:
        land_evidence(store, created=created)
    lines = export_lines(held)
    if newest_first:
        lines = [lines[0], *reversed(lines[1:])]
    path = tmp_path / "export.csv"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    items = [*feed_rows(held), *(feed_only or [])]
    if feed_last:
        import_file(store, path, account_id=MAIN, account_map=MAP)
        home.arrive(*items)
    else:
        home.arrive(*items)
        import_file(store, path, account_id=MAIN, account_map=MAP)
        home.settle()
    return store


def opening_of(store: Store):
    return effective_opening(store, MAIN, families=families_of(store, MAP))


def anchor_verdicts(opening) -> list[tuple[date, bool | None]]:
    return [(r.anchor.day, r.agrees) for r in opening.readings]


def walk_verdicts(opening) -> list[tuple[date, bool | None]]:
    return [(r.day, r.agrees) for r in opening.family.readings]


def differences(readings) -> dict[date, int]:
    """Each stated balance's day and its difference from the rows (stated minus predicted)."""
    return {
        getattr(r, "day", None) or r.anchor.day: r.difference_minor or 0 for r in readings
    }


def stepped(from_day: date, amount: int) -> dict[date, int]:
    """Nil before `from_day`, then `amount` at every stated balance: one fault, on that day."""
    opening = date(2026, 8, 31)
    return {
        day: (amount if day >= from_day else 0)
        for day in [opening, *(date(2026, 9, n) for n in range(1, STATED_DAYS + 1))]
    }


def surplus(name: str, minor: int, day: int) -> Transaction:
    """A payment only the feed holds: the export does not list it."""
    return pay(MAIN, FEED, f"f-{name}", minor, day, name)


def set_status(store: Store, name: str, status: TransactionStatus) -> None:
    """The feed reports the payment again with another status, as a later pull would."""
    (payment,) = [p for p in PAYMENTS if p.name == name]
    Household(store, MAP).arrive(
        replace(
            pay(MAIN, FEED, f"f-{name}", payment.minor, payment.feed_day, name), status=status
        )
    )


@pytest.fixture
def make(tmp_path):
    opened: list[Store] = []

    def made(**kwargs) -> Store:
        store = build(tmp_path, **kwargs)
        opened.append(store)
        return store

    yield made
    for store in opened:
        store.close()


class TestAnExportThatDatesTheSamePaymentsDifferently:
    @pytest.mark.parametrize("feed_last", [True, False], ids=["feed-dated", "export-dated"])
    @pytest.mark.parametrize("newest_first", [False, True], ids=["oldest-first", "newest-first"])
    def test_Export_WhenItsDatesDifferFromTheFeedsAndNothingIsMissing_EveryAnchorAgrees(
        self, make, feed_last, newest_first
    ):
        store = make(feed_last=feed_last, newest_first=newest_first)

        opening = opening_of(store)

        assert opening.readings[0].anchor.basis == OPENED
        assert {r.anchor.basis for r in opening.readings[1:]} == {FAMILY}
        assert len(opening.readings) == 1 + STATED_ANCHORS
        assert opening.differing == []

    def test_Walk_WhenTheExportDatesTheSamePaymentsDifferently_EveryFamilyBalanceIsReproduced(
        self, make
    ):
        walk = opening_of(make()).family

        assert walk is not None
        assert (walk.anchors, walk.agreeing, walk.differing) == (
            STATED_ANCHORS, STATED_ANCHORS, [],
        )
        assert walk.changes == ()

    def test_AnchorListAndWalk_WhenTheDatesDiffer_GiveTheSameVerdictForEveryDay(self, make):
        opening = opening_of(make())

        assert anchor_verdicts(opening)[1:] == walk_verdicts(opening)


class TestARowOnlyTheFeedCountsIsASurplus:
    def test_Anchors_WhenOneFeedOnlyRowIsSurplus_DifferFromItsDayByAnUnchangingAmount(self, make):
        store = make(feed_only=[surplus("Phantom", -1234, 14)])

        opening = opening_of(store)

        assert differences(opening.readings[1:]) == stepped(date(2026, 9, 14), 1234)
        assert differences(opening.family.readings) == stepped(date(2026, 9, 14), 1234)
        assert opening.family.constant is True

    def test_Walk_WhenOneFeedOnlyRowIsSurplus_HasExactlyOneChangeOnItsDay(self, make):
        walk = opening_of(make(feed_only=[surplus("Phantom", -1234, 14)])).family

        assert [(c.day, c.unheld) for c in walk.changes] == [(date(2026, 9, 14), False)]

    def test_Walk_WhenTwoFeedOnlyRowsAreSurplus_HasExactlyTwoChangesOnTheirDays(self, make):
        walk = opening_of(
            make(feed_only=[surplus("Phantom", -1234, 12), surplus("Ghost", -777, 21)])
        ).family

        assert [(c.day, c.unheld) for c in walk.changes] == [
            (date(2026, 9, 12), False),
            (date(2026, 9, 21), False),
        ]
        assert walk.constant is False

    def test_AnchorListAndWalk_WhenARowIsSurplus_GiveTheSameVerdictForEveryDay(self, make):
        opening = opening_of(make(feed_only=[surplus("Phantom", -1234, 14)]))

        assert anchor_verdicts(opening)[1:] == walk_verdicts(opening)


class TestARowTheExportListsButTheStoreDoesNotCount:
    @pytest.mark.parametrize("status", [TransactionStatus.VOID, TransactionStatus.FOLDED])
    def test_Anchors_WhenListedRowIsNotCounted_DifferFromItsDayByAnUnchangingAmount(
        self, make, status
    ):
        store = make()
        set_status(store, "Garage", status)

        opening = opening_of(store)

        assert differences(opening.readings[1:]) == stepped(date(2026, 9, 14), -9000)
        walk = opening.family
        assert [(c.day, c.unheld) for c in walk.changes] == [(date(2026, 9, 14), False)]
        assert walk.constant is True
        assert anchor_verdicts(opening)[1:] == walk_verdicts(opening)


class TestAStatedBalanceDatedBeforeTheAccountWasCreated:
    """The provider says the account was created on the 3rd, yet the export (and
    the feed) hold rows from the 1st. The opening is nil at the end of the 2nd, so
    the 3800.00 that moved on or before it is one fault, and every stated balance
    differs from the rows by that same amount, the two earliest included."""

    def test_AnchorListAndWalk_WhenABalancePredatesTheOpening_GiveTheSameVerdictForEveryDay(
        self, make
    ):
        opening = opening_of(make(created="2026-09-03T09:30:00Z"))

        assert anchor_verdicts(opening)[1:] == walk_verdicts(opening)
        assert opening.family.before_opening == 2
        assert {r.difference_minor for r in opening.family.readings} == {380000}
        assert opening.readings[0].anchor.basis == OPENED
        assert [r.agrees for r in opening.readings[1:]] == [False] * STATED_ANCHORS


class TestATransferToASpaceWhoseRowsAreNotHeld:
    def test_Walk_WhenAChangeFollowsALegToAnUnheldSpace_NamesItAsExplained(self, make):
        gone = leg(MAIN, -4700, 12, "cat-closed", "f-gone")

        walk = opening_of(make(feed_only=[gone])).family

        assert [(c.day, c.unheld) for c in walk.changes] == [(date(2026, 9, 12), True)]
        assert walk.unheld.legs == 1

    def test_Walk_WhenOneChangeIsALegAndAnotherIsASurplusRow_OnlyTheLegIsExplained(self, make):
        gone = leg(MAIN, -4700, 12, "cat-closed", "f-gone")

        walk = opening_of(make(feed_only=[gone, surplus("Phantom", -1234, 21)])).family

        assert [(c.day, c.unheld) for c in walk.changes] == [
            (date(2026, 9, 12), True),
            (date(2026, 9, 21), False),
        ]


def render(store: Store) -> str:
    """The ledger page as a GET serves it: masked."""
    ledger = build_ledger(store, MAIN, None, bound=False, families=families_of(store, MAP))
    return render_ledger(ledger, unmasked=False).decode("utf-8")


class TestTheChangesOnThePage:
    def test_Page_WhenOneRowIsSurplus_SaysOneChangeAndNamesItsDay(self, make):
        page = render(make(feed_only=[surplus("Phantom", -1234, 14)]))

        assert "The difference changes on 1 day: " in page
        assert "2026-09-14" in page
        assert "coincide" not in page

    def test_Page_WhenALegGoesToAnUnheldSpace_SaysThatChangeNeedsTheSpaceDeclared(self, make):
        page = render(make(feed_only=[leg(MAIN, -4700, 12, "cat-closed", "f-gone")]))

        assert (
            "1 of those changes coincides with a transfer to a Space whose rows are not held"
            in page
        )
        assert "needs that Space declared and not a row found" in page

    def test_Page_WhenNothingDiffers_SaysNothingAboutChanges(self, make):
        assert "The difference changes" not in render(make())

    def test_Page_WhenMoreThanTwentyDaysChange_NamesTwentyThenCountsTheRest(self, tmp_path):
        phantoms = [surplus(f"Phantom{d}", -(7 + d), d) for d in range(3, 28)]
        store = build(tmp_path, feed_only=phantoms)
        try:
            opening = opening_of(store)
            page = render(store)
        finally:
            store.close()

        assert len(opening.family.changes) == 25
        assert "The difference changes on 25 days: " in page
        assert " and 5 more." in page
        assert page.count("2026-09-") >= 20

    def test_Page_NeverCarriesAFigure(self, make):
        page = render(make(feed_only=[surplus("Phantom", -1234, 14)]))

        for figure in ("12.34", "1,234"):
            assert figure not in page
