"""A payment made from a Starling Space is one payment, whoever reports it.

Starling's own feed files each payment under the category it was really paid
from, so a bill paid from the Bills Space is held under the Bills account. An
aggregator and the Starling CSV export cannot see Spaces: they report the same
bill as a payment from the MAIN account. Both rows were kept, so the bill was
counted in the Space and again in the main account. Measured on the deployed
store: 711 aggregator rows (and 691 from the export) in one main account
matched rows the feed had filed under the Bills Space, and the main account's
rows summed to a net outflow of tens of thousands that never happened.

Every scenario below states how many payments were really made and where. That
decides the number of counted rows expected, before the first run. "Counted"
is what the ledger counts: history (void or folded) is in no sum.

The household: a main account, two Spaces, an aggregator bound to the main
account only, and an export bound to nothing.
"""

from __future__ import annotations

import itertools
import json
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from typing import ClassVar

import pytest

from landing import import_file, rebuild_from_raw
from obdi.core.models import RawArtefact, SourceTier, Transaction, TransactionStatus
from obdi.export.replay import ActualAccountBinding, build_payload, withheld_reason
from obdi.ingest.accounts import AccountBinding, AccountMap, AccountRecord, AccountRef
from obdi.ingest.identity import artefact_digest, content_key
from obdi.ingest.identity_health import identity_health
from obdi.ingest.pipeline import (
    ImportSummary,
    pair_transfers_across_store,
    reconcile_batch,
)
from obdi.ingest.providers import starling, truelayer
from obdi.ingest.rebuild import (
    parse_artefact_transactions,
    resolve_artefact_ref,
)
from obdi.ingest.space_attribution import fold_space_copies, plan_folds, space_parents
from obdi.ingest.store import Store
from obdi.read.ledger import build_ledger
from obdi.read.overview import held_by_account
from obdi.read.position import read_position
from obdi.verify.balance_anchors import effective_opening, record_stated_anchor
from obdi.verify.balance_reconciliation import balance_reconciliation
from obdi.verify.coverage import agreements

MAIN = "starling-personal"
BILLS = "starling-space-bills"
HOLIDAY = "starling-space-holiday"

BOOKED = TransactionStatus.BOOKED
PENDING = TransactionStatus.PENDING
FOLDED = TransactionStatus.FOLDED

FEED, AGGREGATOR, EXPORT = "starling", "truelayer", "starling-csv"

BINDINGS = [
    AccountBinding(MAIN, "starling", "acc-main"),
    AccountBinding(BILLS, "starling", "cat-bills"),
    AccountBinding(HOLIDAY, "starling", "cat-holiday"),
    AccountBinding(MAIN, "truelayer", "tl-main"),
]


HALIFAX = "halifax-current"
JOINT = "starling-joint"
JOINT_SPACE = "starling-space-joint"


def household_map(
    *,
    spaces_declared: bool = False,
    extra_bindings: tuple[AccountBinding, ...] = (),
    extra_records: tuple[AccountRecord, ...] = (),
) -> AccountMap:
    """The household's map. Declared, the Spaces say whose they are in the
    registry; undeclared, nothing but the provider's own structure can."""
    records = [
        AccountRecord(ref=AccountRef(BILLS), kind="starling-space", parent=AccountRef(MAIN)),
        AccountRecord(ref=AccountRef(HOLIDAY), kind="starling-space", parent=AccountRef(MAIN)),
    ]
    return AccountMap(
        [*BINDINGS, *extra_bindings],
        records=[*(records if spaces_declared else []), *extra_records],
    )


#: Spaces known from the registry alone.
MAP = household_map(spaces_declared=True)
#: Spaces known only from the feed artefacts' own account and category.
PROVIDER_MAP = household_map()
#: A second bank on the same aggregator as the main account, no parents anywhere.
TWO_BANKS = household_map(
    extra_bindings=(AccountBinding(HALIFAX, "truelayer", "tl-halifax"),)
)


def pay(
    account: str,
    source: str,
    source_id: str | None,
    minor: int,
    day: int,
    description: str,
    *,
    status: TransactionStatus = BOOKED,
    internal: bool = False,
) -> Transaction:
    when = date(2026, 9, day)
    return Transaction(
        account_id=account,
        amount_minor=minor,
        value_date=when,
        booking_date=when,
        description=description,
        source=source,
        source_id=source_id,
        content_key=content_key(amount_minor=minor, value_date=when, description=description),
        tier=SourceTier.AUTHORITATIVE if source_id else SourceTier.SYNTHETIC,
        status=status,
        is_internal_transfer=internal,
    )


class Household:
    """Arrivals through the door a pull uses, each followed by the settling pass."""

    def __init__(self, store: Store, account_map: AccountMap = MAP) -> None:
        self.store = store
        self.account_map = account_map
        self._arrivals = 0

    def arrive(self, *items: Transaction, settle: bool = True):
        self._arrivals += 1
        reconcile_batch(self.store, list(items), digest=f"arrival-{self._arrivals}")
        return fold_space_copies(self.store, self.account_map) if settle else None

    def settle(self):
        return fold_space_copies(self.store, self.account_map)

    def counted(self, ref: str) -> int:
        """How many rows the account's ledger counts as money, September."""
        return self._ledger(ref).position.rows_counted

    def balance(self, ref: str) -> int:
        """The account's balance by its own counted rows plus any stated opening."""
        return self._ledger(ref).position.store_balance.minor

    def _ledger(self, ref: str):
        return build_ledger(self.store, ref, "2026-09", bound=False, label=ref)

    def rows(self, ref: str) -> list[Transaction]:
        return self.store.transactions_for_account(ref)

    def statuses(self, ref: str) -> list[str]:
        return sorted(str(t.status) for t in self.rows(ref))

    def sources_of(self, ref: str, minor: int) -> list[str]:
        (row,) = [t for t in self.rows(ref) if t.amount_minor == minor]
        return self.store.sources_for(row.entity_id)


@pytest.fixture
def store(tmp_path):
    with Store(tmp_path / "household.sqlite3") as opened:
        yield opened


@pytest.fixture
def home(store):
    return Household(store)


def bill_from_the_space(day: int = 5, minor: int = -5000):
    """The feed's report under the Space, and the aggregator's under the main account."""
    return (
        pay(BILLS, FEED, f"uid-bill-{day}", minor, day, "Water Co"),
        pay(MAIN, AGGREGATOR, f"tl-bill-{day}", minor, day, "WATER CO DD"),
    )


class TestABillPaidFromASpace:
    """One payment of 50.00, from the Bills Space."""

    @pytest.mark.parametrize("feed_first", [True, False], ids=["feed-first", "aggregator-first"])
    def test_BillSeenUnderTheSpaceAndUnderMain_IsOneCountedRowInTheSpace(self, home, feed_first):
        in_space, in_main = bill_from_the_space()
        # The main account holds other money so the account exists either way.
        home.arrive(pay(MAIN, FEED, "uid-salary", 50000, 1, "Employer"))
        for item in (in_space, in_main) if feed_first else (in_main, in_space):
            home.arrive(item)

        assert home.counted(BILLS) == 1
        assert home.counted(MAIN) == 1
        assert home.balance(MAIN) == 50000
        assert home.balance(BILLS) == -5000
        assert home.statuses(MAIN) == ["booked", "folded"]

    @pytest.mark.parametrize("feed_first", [True, False], ids=["feed-first", "aggregator-first"])
    def test_BillSeenUnderTheSpaceAndUnderMain_SettledOnceAtTheEnd_IsTheSameAsSettlingEachTime(
        self, home, feed_first
    ):
        in_space, in_main = bill_from_the_space()
        home.arrive(pay(MAIN, FEED, "uid-salary", 50000, 1, "Employer"), settle=False)
        for item in (in_space, in_main) if feed_first else (in_main, in_space):
            home.arrive(item, settle=False)

        report = home.settle()

        assert report.folded == 1
        assert home.counted(BILLS) == 1
        assert home.balance(MAIN) == 50000

    def test_BillSeenUnderTheSpaceAndUnderMain_TheSpaceRowRecordsThatBothSourcesSawIt(self, home):
        in_space, in_main = bill_from_the_space()
        home.arrive(in_space, in_main)

        assert home.sources_of(BILLS, -5000) == ["starling", "truelayer"]

    def test_BillSeenUnderTheSpaceAndUnderMain_TheMainCopyStaysRecoverableAsHistory(self, home):
        in_space, in_main = bill_from_the_space()
        home.arrive(in_space, in_main)

        (copy,) = [t for t in home.rows(MAIN) if t.status is FOLDED]
        assert copy.amount_minor == -5000
        assert copy.source_id == "tl-bill-5"
        assert home.store.sources_for(copy.entity_id) == ["truelayer"]

    def test_SettlingAgain_ChangesNothing(self, home):
        in_space, in_main = bill_from_the_space()
        home.arrive(in_space, in_main)

        again = home.settle()

        assert (again.folded, again.newly_folded, again.released) == (1, 0, 0)
        assert home.counted(BILLS) == 1
        assert home.sources_of(BILLS, -5000) == ["starling", "truelayer"]

    def test_TheAggregatorReportsItAgainOnALaterPull_ItIsStillOneCountedRowInTheSpace(self, home):
        in_space, in_main = bill_from_the_space()
        home.arrive(in_space, in_main)

        home.arrive(in_main)
        home.arrive(pay(MAIN, AGGREGATOR, "tl-other", 5000, 8, "REFUND"))

        assert home.counted(BILLS) == 1
        assert home.statuses(MAIN) == ["booked", "folded"]
        assert home.sources_of(BILLS, -5000) == ["starling", "truelayer"]

    def test_TheAggregatorReportsItAgainStillPending_TheFoldedRowIsNotPutBackToPending(
        self, home
    ):
        in_space, in_main = bill_from_the_space()
        home.arrive(in_space, in_main)

        home.arrive(
            pay(MAIN, AGGREGATOR, "tl-bill-pending", -5000, 5, "WATER CO DD", status=PENDING)
        )

        assert home.statuses(MAIN) == ["folded"]
        assert home.counted(BILLS) == 1

    def test_AnAggregatorRowStillPending_IsLeftInTheMainAccountUntilItSettles(self, home):
        """Pending rows are not folded, so the double count lasts only until
        settlement; once the row settles under its new id it is folded."""
        home.arrive(pay(BILLS, FEED, "uid-bill-5", -5000, 5, "Water Co"))
        home.arrive(
            pay(MAIN, AGGREGATOR, "tl-pending", -5000, 5, "WATER CO DD", status=PENDING)
        )
        assert home.statuses(MAIN) == ["pending"]
        assert home.balance(MAIN) == -5000

        home.arrive(pay(MAIN, AGGREGATOR, "tl-settled", -5000, 5, "WATER CO DD"))

        assert home.statuses(MAIN) == ["folded"]
        assert home.balance(MAIN) == 0
        assert home.counted(BILLS) == 1


class TestTheSameBillWhenTheExportIsTheSpaceBlindSource:
    """One payment of 50.00 from the Bills Space; the CSV export, bound to no
    account at all, reports it under the main account."""

    @pytest.mark.parametrize("feed_first", [True, False], ids=["feed-first", "export-first"])
    def test_BillSeenUnderTheSpaceAndInTheExport_IsOneCountedRowInTheSpace(self, home, feed_first):
        in_space = pay(BILLS, FEED, "uid-bill", -5000, 5, "Water Co")
        in_export = pay(MAIN, EXPORT, None, -5000, 5, "Water Co")
        home.arrive(pay(MAIN, FEED, "uid-salary", 50000, 1, "Employer"))
        for item in (in_space, in_export) if feed_first else (in_export, in_space):
            home.arrive(item)

        assert home.counted(BILLS) == 1
        assert home.counted(MAIN) == 1
        assert home.balance(MAIN) == 50000
        assert home.sources_of(BILLS, -5000) == ["starling", "starling-csv"]

    def test_BillSeenByBothSpaceBlindSources_IsStillOneCountedRowInTheSpace(self, home):
        """The export and the aggregator agree with each other under the main
        account, so they are one row there; that row is one payment, in the Space."""
        home.arrive(pay(BILLS, FEED, "uid-bill", -5000, 5, "Water Co"))
        home.arrive(pay(MAIN, AGGREGATOR, "tl-bill", -5000, 5, "WATER CO DD"))
        home.arrive(pay(MAIN, EXPORT, None, -5000, 5, "Water Co"))

        assert home.counted(BILLS) == 1
        assert home.statuses(MAIN) == ["folded"]
        assert home.sources_of(BILLS, -5000) == ["starling", "starling-csv", "truelayer"]


class TestAPaymentReallyMadeFromTheMainAccount:
    """Whatever else resembles it, a row the bank's own feed also reports under
    the main account is that payment's own row."""

    PERMUTATIONS: ClassVar[list[tuple[str, ...]]] = list(
        itertools.permutations(["feed-main", "aggregator-main", "feed-space"])
    )

    @pytest.mark.parametrize("order", PERMUTATIONS, ids=["-".join(o) for o in PERMUTATIONS])
    def test_PaymentSeenByFeedAndAggregatorUnderMain_StaysCountedInMain_InAnyArrivalOrder(
        self, home, order
    ):
        """Two payments of 50.00 on one day: one from the main account (reported
        under main by the feed and the aggregator) and one from the Bills Space
        (reported under the Space by the feed alone)."""
        arrivals = {
            "feed-main": pay(MAIN, FEED, "uid-main", -5000, 5, "Garage"),
            "aggregator-main": pay(MAIN, AGGREGATOR, "tl-main", -5000, 5, "GARAGE"),
            "feed-space": pay(BILLS, FEED, "uid-space", -5000, 5, "Water Co"),
        }
        for name in order:
            home.arrive(arrivals[name])

        assert home.counted(MAIN) == 1
        assert home.counted(BILLS) == 1
        assert home.statuses(MAIN) == ["booked"]
        assert home.sources_of(MAIN, -5000) == ["starling", "truelayer"]
        assert home.sources_of(BILLS, -5000) == ["starling"]

    def test_PaymentSeenByTheFeedUnderMain_AfterTheAggregatorsCopyWasFolded_IsCountedInMainAgain(
        self, home
    ):
        """The aggregator's main-account row was first taken for the Space's
        bill. The feed then reports a payment of the same amount under the main
        account: that is a second payment, and the main row is real."""
        home.arrive(pay(BILLS, FEED, "uid-space", -5000, 5, "Water Co"))
        home.arrive(pay(MAIN, AGGREGATOR, "tl-main", -5000, 5, "GARAGE"))
        assert home.statuses(MAIN) == ["folded"]

        home.arrive(pay(MAIN, FEED, "uid-main", -5000, 5, "Garage"))

        assert home.statuses(MAIN) == ["booked"]
        assert home.counted(MAIN) == 1
        assert home.counted(BILLS) == 1
        assert home.sources_of(BILLS, -5000) == ["starling"]


class TestTwoIdenticalBills:
    def test_TwoIdenticalBillsOnOneDay_BothSeenByBothSources_AreTwoRowsInTheSpace(self, home):
        """Two payments of 50.00 from the Bills Space on one day, each reported
        twice: the feed under the Space, the aggregator under the main account."""
        home.arrive(pay(MAIN, FEED, "uid-salary", 50000, 1, "Employer"))
        home.arrive(
            pay(BILLS, FEED, "uid-bill-1", -5000, 5, "Water Co"),
            pay(BILLS, FEED, "uid-bill-2", -5000, 5, "Water Co"),
        )
        report = home.arrive(
            pay(MAIN, AGGREGATOR, "tl-bill-1", -5000, 5, "WATER CO DD"),
            pay(MAIN, AGGREGATOR, "tl-bill-2", -5000, 5, "WATER CO DD"),
        )

        assert report.folded == 2
        assert home.counted(BILLS) == 2
        assert home.counted(MAIN) == 1
        assert home.balance(MAIN) == 50000
        assert home.balance(BILLS) == -10000
        for row in home.rows(BILLS):
            assert home.store.sources_for(row.entity_id) == ["starling", "truelayer"]

    def test_TwoIdenticalBillsOnOneDay_TheAggregatorFirst_AreTwoRowsInTheSpace(self, home):
        home.arrive(
            pay(MAIN, AGGREGATOR, "tl-bill-1", -5000, 5, "WATER CO DD"),
            pay(MAIN, AGGREGATOR, "tl-bill-2", -5000, 5, "WATER CO DD"),
        )
        home.arrive(
            pay(BILLS, FEED, "uid-bill-1", -5000, 5, "Water Co"),
            pay(BILLS, FEED, "uid-bill-2", -5000, 5, "Water Co"),
        )

        assert home.counted(BILLS) == 2
        assert home.statuses(MAIN) == ["folded", "folded"]

    def test_ManyDifferentBills_AreEachFoldedIntoTheirOwnSpaceRow(self, home):
        """Forty payments of different amounts from the Bills Space, one a day,
        each reported twice."""
        in_space = [pay(BILLS, FEED, f"uid-{n}", -1000 - n, n, "Bill") for n in range(1, 29)]
        in_main = [pay(MAIN, AGGREGATOR, f"tl-{n}", -1000 - n, n, "BILL DD") for n in range(1, 29)]
        home.arrive(*in_space)
        report = home.arrive(*in_main)

        assert report.folded == 28
        assert home.counted(BILLS) == 28
        assert home.statuses(MAIN) == ["folded"] * 28


class TestWhatIsLeftAloneIsCounted:
    def test_ARowOnlyTheAggregatorHolds_WithNoSpaceRowToMatch_StaysCountedInMain(self, home):
        """One payment of 7.77, perhaps a main-account payment the feed has not
        delivered yet. Nothing says otherwise, so it stays."""
        report = home.arrive(pay(MAIN, AGGREGATOR, "tl-mystery", -777, 6, "CORNER SHOP"))

        assert home.counted(MAIN) == 1
        assert home.statuses(MAIN) == ["booked"]
        assert (report.folded, report.unmatched, report.ambiguous) == (0, 1, 0)

    def test_OneMainRowAndTwoMatchingSpaceRows_IsNotGuessed_AndIsCounted(self, home):
        """Two payments of 50.00 from the Bills Space on one day, but the
        aggregator reported one 50.00 payment under main. It may be a copy of
        either Space payment or a real main payment, and nothing says which, so
        it is left in the main account and counted as ambiguous."""
        home.arrive(
            pay(BILLS, FEED, "uid-bill-1", -5000, 5, "Water Co"),
            pay(BILLS, FEED, "uid-bill-2", -5000, 5, "Water Co"),
        )
        report = home.arrive(pay(MAIN, AGGREGATOR, "tl-bill", -5000, 5, "WATER CO DD"))

        assert (report.folded, report.ambiguous) == (0, 1)
        assert home.statuses(MAIN) == ["booked"]
        assert home.counted(BILLS) == 2
        for row in home.rows(BILLS):
            assert home.store.sources_for(row.entity_id) == ["starling"]

    def test_TwoMainRowsAndOneMatchingSpaceRow_NeitherIsFolded_AndBothAreCounted(self, home):
        """One Space row cannot absorb two main rows. One payment of 50.00 from
        the Space is reported; the aggregator holds two 50.00 rows under main,
        so at most one is its copy and nothing says which."""
        home.arrive(pay(BILLS, FEED, "uid-bill", -5000, 5, "Water Co"))
        report = home.arrive(
            pay(MAIN, AGGREGATOR, "tl-bill-1", -5000, 5, "WATER CO DD"),
            pay(MAIN, AGGREGATOR, "tl-bill-2", -5000, 5, "WATER CO DD"),
        )

        assert (report.folded, report.ambiguous) == (0, 2)
        assert home.statuses(MAIN) == ["booked", "booked"]
        assert home.sources_of(BILLS, -5000) == ["starling"]

    def test_ASpaceRowTooFarInTimeToBeTheSamePayment_IsNotAMatch(self, home):
        home.arrive(pay(BILLS, FEED, "uid-bill", -5000, 5, "Water Co"))
        report = home.arrive(pay(MAIN, AGGREGATOR, "tl-bill", -5000, 9, "WATER CO DD"))

        assert (report.folded, report.unmatched) == (0, 1)
        assert home.statuses(MAIN) == ["booked"]

    def test_ASpaceRowTwoDaysApart_IsTheSamePayment(self, home):
        """The same window the cross-source report uses for rows filed under
        sibling accounts."""
        home.arrive(pay(BILLS, FEED, "uid-bill", -5000, 5, "Water Co"))
        report = home.arrive(pay(MAIN, AGGREGATOR, "tl-bill", -5000, 7, "WATER CO DD"))

        assert report.folded == 1

    def test_ARowOfAnotherAmount_IsNotAMatch(self, home):
        home.arrive(pay(BILLS, FEED, "uid-bill", -5000, 5, "Water Co"))
        report = home.arrive(pay(MAIN, AGGREGATOR, "tl-bill", -5001, 5, "WATER CO DD"))

        assert report.folded == 0

    def test_PendingSpaceRow_IsNotSomethingToFoldInto(self, home):
        home.arrive(pay(BILLS, FEED, "uid-bill", -5000, 5, "Water Co", status=PENDING))
        report = home.arrive(pay(MAIN, AGGREGATOR, "tl-bill", -5000, 5, "WATER CO DD"))

        assert (report.folded, report.unmatched) == (0, 1)


class TestAnInternalTransferIsNotADuplicate:
    """Topping up the Bills Space with 200.00: one transfer, two real legs."""

    def test_TopUpSeenByFeedAndAggregator_BothLegsStayCounted(self, home):
        home.arrive(
            pay(MAIN, FEED, "uid-out", -20000, 3, "To Bills", internal=True),
            pay(BILLS, FEED, "uid-in", 20000, 3, "From Main", internal=True),
        )
        report = home.arrive(pay(MAIN, AGGREGATOR, "tl-out", -20000, 3, "TO BILLS"))

        assert report.folded == 0
        assert home.counted(MAIN) == 1
        assert home.counted(BILLS) == 1
        assert pair_transfers_across_store(home.store) == 1

    def test_TopUpSeenOnlyByTheAggregatorUnderMain_IsNotFoldedIntoTheSpacesIncomingLeg(
        self, home
    ):
        """The aggregator's outgoing leg and the Space's incoming leg are
        opposite directions of one transfer, not two reports of one payment."""
        home.arrive(pay(BILLS, FEED, "uid-in", 20000, 3, "From Main", internal=True))
        report = home.arrive(pay(MAIN, AGGREGATOR, "tl-out", -20000, 3, "TO BILLS"))

        assert report.folded == 0
        assert home.statuses(MAIN) == ["booked"]
        assert home.counted(BILLS) == 1
        assert pair_transfers_across_store(home.store) == 1

    def test_ACopyOfASpacePayment_IsNotOfferedAsTheLegOfAnyTransfer(self, home):
        """One payment of 50.00 from the Bills Space, and 50.00 arriving in the
        Holiday Space a day later. The main account's copy of the payment sorts
        ahead of the Space's own row, so offered to the pairing pass it would
        take the credit and leave the real payment unpaired."""
        home.arrive(*bill_from_the_space())
        home.arrive(pay(HOLIDAY, FEED, "uid-in", 5000, 6, "From elsewhere"))

        pair_transfers_across_store(home.store)

        ((debit, _credit),) = home.store.confirmed_transfer_pairs()
        (bill,) = home.rows(BILLS)
        assert debit == bill.entity_id

    def test_TopUpAndABillOfTheSameAmount_TheBillFoldsAndTheTransferStays(self, home):
        """Two real movements of 200.00 on one day: the top-up (a transfer, both
        legs real) and a payment from the Space (one payment)."""
        home.arrive(
            pay(MAIN, FEED, "uid-out", -20000, 3, "To Bills", internal=True),
            pay(BILLS, FEED, "uid-in", 20000, 3, "From Main", internal=True),
            pay(BILLS, FEED, "uid-rent", -20000, 3, "Landlord"),
        )
        home.arrive(
            pay(MAIN, AGGREGATOR, "tl-out", -20000, 3, "TO BILLS"),
            pay(MAIN, AGGREGATOR, "tl-rent", -20000, 3, "LANDLORD"),
        )

        # The aggregator's top-up row merges into the feed's own main-account
        # row for it; its rent row has no such twin and is the Space payment's copy.
        assert home.counted(BILLS) == 2
        assert home.counted(MAIN) == 1


class TestBalances:
    """Hand-computed, pence.

    Main: stated 1,000.00 on 08-31, then
        09-01 salary  +500.00
        09-02 shop     -20.00
        09-03 top-up  -100.00  (to Bills)
        09-05 bill     -50.00  paid from Bills; the aggregator shows it under main
    The bill is not a main-account movement, so
        main  = 1,000.00 + 500.00 - 20.00 - 100.00 = 1,380.00 = 138,000
    Bills: stated 200.00 on 08-31, then 09-03 +100.00 (top-up), 09-05 -50.00
        bills = 200.00 + 100.00 - 50.00 = 250.00 = 25,000
    """

    @pytest.fixture
    def settled(self, home):
        home.arrive(
            pay(MAIN, FEED, "uid-1", 50000, 1, "Employer"),
            pay(MAIN, FEED, "uid-2", -2000, 2, "Shop"),
            pay(MAIN, FEED, "uid-3", -10000, 3, "To Bills", internal=True),
            pay(BILLS, FEED, "uid-3b", 10000, 3, "From Main", internal=True),
            pay(BILLS, FEED, "uid-4", -5000, 5, "Water Co"),
        )
        home.arrive(
            pay(MAIN, AGGREGATOR, "tl-1", 50000, 1, "EMPLOYER"),
            pay(MAIN, AGGREGATOR, "tl-2", -2000, 2, "SHOP"),
            pay(MAIN, AGGREGATOR, "tl-3", -10000, 3, "TO BILLS"),
            pay(MAIN, AGGREGATOR, "tl-4", -5000, 5, "WATER CO DD"),
        )
        record_stated_anchor(home.store, MAIN, "2026-08-31", "1000.00")
        record_stated_anchor(home.store, BILLS, "2026-08-31", "200.00")
        return home

    def test_BothAccountsBalances_AreTheHandComputedFigures(self, settled):
        assert settled.balance(MAIN) == 138000
        assert settled.balance(BILLS) == 25000

    def test_ThePositionPage_ShowsTheSameBalances(self, settled):
        position = read_position(settled.store, today=date(2026, 10, 2))
        by_ref = {a.ref: a for g in position.groups for a in g.accounts}

        assert by_ref[MAIN].balance.minor == 138000
        assert by_ref[BILLS].balance.minor == 25000
        assert by_ref[MAIN].rows == 3

    def test_ALaterStatedBalanceOfTheMainAccount_AgreesWithItsRows(self, settled):
        """The bank says the main account holds 1,380.00 on 09-30. A balance
        that still counted the aggregator's copy of the bill would differ."""
        record_stated_anchor(settled.store, MAIN, "2026-09-30", "1380.00")

        opening = effective_opening(settled.store, MAIN)

        assert opening.opening_minor == 100000
        assert [reading.agrees for reading in opening.readings[1:]] == [True]

    def test_TheMonthsSumsOfTheMainAccount_LeaveTheFoldedRowOut_AndSayHowManyWere(self, settled):
        ledger = build_ledger(settled.store, MAIN, "2026-09", bound=True, label=MAIN)

        assert ledger.summary.store_sum.minor == 50000 - 2000 - 10000
        assert ledger.summary.sent_sum.minor == 50000 - 2000 - 10000
        assert ledger.summary.folded == 1
        assert {row.status for row in ledger.rows} == {"booked", "folded"}
        assert len(ledger.rows) == 4


class TestTheOtherPlacesRowsAreCounted:
    def household(self, home):
        home.arrive(
            pay(MAIN, FEED, "uid-1", 50000, 1, "Employer"),
            pay(BILLS, FEED, "uid-4", -5000, 5, "Water Co"),
        )
        home.arrive(
            pay(MAIN, AGGREGATOR, "tl-1", 50000, 1, "EMPLOYER"),
            pay(MAIN, AGGREGATOR, "tl-4", -5000, 5, "WATER CO DD"),
        )

    def test_TheOverviewsRowsHeldAndNewestRow_LeaveTheFoldedCopyOut(self, home):
        self.household(home)

        held, sources = held_by_account(home.store)

        assert held[MAIN] == (1, date(2026, 9, 1))
        assert held[BILLS] == (1, date(2026, 9, 5))
        assert sources[BILLS] == {"starling", "truelayer"}

    def test_TheOverviewAndTheLedger_AgreeOnHowManyRowsAreHeldAndListTheFoldedOneApart(
        self, home
    ):
        """Both count what is money, so both say 1 for the main account; the
        ledger also LISTS the folded row, flagged, as it lists void rows."""
        self.household(home)

        held, _ = held_by_account(home.store)
        ledger = build_ledger(home.store, MAIN, "2026-09", bound=False, label=MAIN)

        assert held[MAIN][0] == ledger.position.rows_counted == 1
        assert ledger.summary.rows == 2
        assert ledger.summary.folded == 1

    def test_TheBankDayCheck_DoesNotCompareTheBanksBalanceWithAFoldedCopy(self, home):
        """The aggregator states a running balance on each main-account row.
        Only the salary's day is a day the main account moved money."""

        def stated(ident: str, minor: int, day: int, running: str) -> Transaction:
            record = {
                "normalised_provider_transaction_id": ident,
                "timestamp": f"2026-09-{day:02}T10:00:00Z",
                "description": ident,
                "amount": f"{minor / 100:.2f}",
                "currency": "GBP",
                "transaction_type": "CREDIT" if minor > 0 else "DEBIT",
                "running_balance": {"amount": running, "currency": "GBP"},
            }
            return replace(
                pay(MAIN, AGGREGATOR, ident, minor, day, ident), raw=record
            )

        home.arrive(pay(BILLS, FEED, "uid-4", -5000, 5, "Water Co"))
        home.arrive(
            stated("tl-1", 50000, 1, "1500.00"), stated("tl-4", -5000, 5, "1450.00")
        )

        (account,) = [
            a for a in balance_reconciliation(home.store, MAIN).accounts if a.account_id == MAIN
        ]

        assert [day.day for day in account.days] == [date(2026, 9, 1)]


class TestWhatIsSentToActual:
    def test_EachPaymentIsSentOnce_UnderTheAccountItWasPaidFrom(self, home):
        home.arrive(
            pay(MAIN, FEED, "uid-1", 50000, 1, "Employer"),
            pay(BILLS, FEED, "uid-4", -5000, 5, "Water Co"),
        )
        home.arrive(
            pay(MAIN, AGGREGATOR, "tl-1", 50000, 1, "EMPLOYER"),
            pay(MAIN, AGGREGATOR, "tl-4", -5000, 5, "WATER CO DD"),
        )
        bindings = [
            ActualAccountBinding(MAIN, "act-main"),
            ActualAccountBinding(BILLS, "act-bills"),
        ]

        payload = build_payload(home.store.all_transactions(), bindings)

        assert sorted(row["amount"] for row in payload["act-main"]) == [50000]
        assert sorted(row["amount"] for row in payload["act-bills"]) == [-5000]

    def test_TheFoldedRowIsWithheldWithTheReason(self, home):
        home.arrive(*bill_from_the_space())
        (copy,) = [t for t in home.rows(MAIN) if t.status is FOLDED]

        reason = withheld_reason(copy, bound=True)

        assert reason is not None
        assert "Space" in reason


class TestTheCrossSourceReport:
    def household(self, home):
        home.arrive(
            pay(MAIN, FEED, "uid-1", 50000, 1, "Employer"),
            pay(MAIN, FEED, "uid-2", -2000, 2, "Shop"),
            pay(MAIN, FEED, "uid-9", -300, 9, "Coffee"),
            pay(BILLS, FEED, "uid-4", -5000, 5, "Water Co"),
        )
        home.arrive(
            pay(MAIN, AGGREGATOR, "tl-1", 50000, 1, "EMPLOYER"),
            pay(MAIN, AGGREGATOR, "tl-2", -2000, 2, "SHOP"),
            pay(MAIN, AGGREGATOR, "tl-4", -5000, 5, "WATER CO DD"),
            pay(MAIN, AGGREGATOR, "tl-9", -300, 9, "COFFEE"),
        )

    def pair(self, home):
        found = agreements(
            home.store.transactions_by_sighting(),
            sibling_accounts=home.account_map.accounts_by_source(),
        )
        (main,) = [a for a in found if a.account_id == MAIN]
        return main

    def test_AFoldedRow_IsNoLongerADifference(self, home):
        self.household(home)

        main = self.pair(home)

        assert main.agrees
        assert main.attributed == ()
        assert main.unexplained == ()
        assert (main.left_count, main.right_count) == (3, 3)

    def test_AGenuinelyUnexplainedRow_IsStillReported(self, home):
        self.household(home)
        home.arrive(pay(MAIN, AGGREGATOR, "tl-mystery", -777, 6, "CORNER SHOP"))

        main = self.pair(home)

        assert not main.agrees
        assert main.attributed == ()
        assert [(row.source, row.amount_minor) for row in main.unexplained] == [
            ("truelayer", -777)
        ]

    def test_TheSpace_GainsNoDisagreeingSecondSourceInTheReport(self, home):
        """The aggregator never delivered anything into the Space, so it is not
        compared against everything the Space holds."""
        self.household(home)
        found = agreements(
            home.store.transactions_by_sighting(),
            sibling_accounts=home.account_map.accounts_by_source(),
        )

        assert [a for a in found if a.account_id == BILLS] == []


class TestADirectionIsNeverReversed:
    def test_ARowInASpaceDeclaredAsOne_IsNeverFoldedIntoTheMainAccount(self, store):
        """Two payments of 30.00 on one day: a gym payment from the Holiday
        Space (in an export of that Space, bound to nothing) and a payment from
        the main account the feed reports under main."""
        home = Household(store, household_map(spaces_declared=True))
        home.arrive(pay(MAIN, FEED, "uid-shop", -3000, 3, "Shop"))
        report = home.arrive(pay(HOLIDAY, EXPORT, None, -3000, 3, "Gym"))

        assert report.folded == 0
        assert home.counted(MAIN) == 1
        assert home.counted(HOLIDAY) == 1

    def test_ASpaceDeclaredAsTheSpaceOfAnotherAccount_IsNotAFoldTarget(self, store):
        """The joint account's own Space holds the bill; the aggregator's row
        under the personal account is not that payment."""
        joint_space = household_map(
            spaces_declared=True,
            extra_bindings=(AccountBinding(JOINT_SPACE, "starling", "cat-joint"),),
            extra_records=(
                AccountRecord(
                    ref=AccountRef(JOINT_SPACE),
                    kind="starling-space",
                    parent=AccountRef(JOINT),
                ),
            ),
        )
        home = Household(store, joint_space)
        home.arrive(pay("starling-space-joint", FEED, "uid-bill", -5000, 5, "Water Co"))
        report = home.arrive(pay(MAIN, AGGREGATOR, "tl-bill", -5000, 5, "WATER CO DD"))

        assert report.folded == 0
        assert home.statuses(MAIN) == ["booked"]


class TestOnlyWhatTheMapSaysIsSpaceBlindIsFolded:
    def test_AMapWithNoSiblings_FoldsNothing(self, store):
        home = Household(store, AccountMap([]))
        home.arrive(*bill_from_the_space())

        assert home.statuses(MAIN) == ["booked"]

    def test_ASourceThatDoesFeedTheSpace_IsNeverTreatedAsBlindToIt(self, store):
        """An aggregator bound to the Space as well sees it, so its main-account
        row is its own report of a main-account payment."""
        sees_spaces = household_map(
            spaces_declared=True,
            extra_bindings=(AccountBinding(BILLS, "truelayer", "tl-bills"),),
        )
        home = Household(store, sees_spaces)
        home.arrive(pay(BILLS, FEED, "uid-bill", -5000, 5, "Water Co"))
        report = home.arrive(pay(MAIN, AGGREGATOR, "tl-bill", -5000, 5, "WATER CO DD"))

        assert report.folded == 0
        assert home.statuses(MAIN) == ["booked"]


class TestRebuiltFromRaw:
    """The same bills, landed as raw artefacts and replayed."""

    def land_accounts(self, store, *, second: bool = False):
        accounts = [{"accountUid": "acc-main", "defaultCategory": "cat-main"}]
        if second:
            accounts.append({"accountUid": "acc-two", "defaultCategory": "cat-two"})
        store.land_artefact(
            starling.artefact_for(
                json.dumps({"accounts": accounts}).encode(),
                account_id="starling",
                kind="accounts",
                origin="https://api.example.com/api/v2/accounts",
            )
        )

    def land_feed(self, store, category: str, *items: dict, account: str = "acc-main"):
        store.land_artefact(
            starling.artefact_for(
                json.dumps({"feedItems": list(items)}).encode(),
                account_id=f"starling:{category}",
                kind="feed",
                origin=(
                    f"https://api.example.com/api/v2/feed/account/{account}/"
                    f"category/{category}?changesSince=2026-09-01T00:00:00Z"
                ),
            )
        )

    def land_the_feed(self, store):
        """The feed's whole account: the bill under the Bills Space, a salary under main."""
        self.land_feed(store, "cat-bills", self.feed_item("uid-bill", 5000, 5, "Water Co"))
        self.land_feed(
            store,
            "cat-main",
            self.feed_item("uid-salary", 50000, 1, "Employer", direction="IN"),
        )

    def land_aggregator(self, store, *records: dict, account: str = "tl-main"):
        store.land_artefact(
            truelayer.artefact_for(
                json.dumps({"results": list(records)}).encode(),
                account_id=account,
                kind="booked",
            )
        )

    def land_export(self, store, *rows: tuple[str, str, str, str]):
        lines = ["Date,Counter Party,Reference,Type,Amount (GBP),Balance (GBP),Notes"]
        lines += [
            f"{day},{who},{ref},FASTER PAYMENT,{amount},0.00,"
            for day, who, ref, amount in rows
        ]
        payload = ("\n".join(lines) + "\n").encode()
        store.land_artefact(
            RawArtefact(
                source="csv",
                account_ref=MAIN,
                fetched_at=datetime.now(UTC),
                media_type="text/csv",
                digest=artefact_digest(payload),
                payload=payload,
                origin="export.csv",
            )
        )

    @staticmethod
    def feed_item(uid: str, pence: int, day: int, who: str, *, direction: str = "OUT") -> dict:
        return {
            "feedItemUid": uid,
            "amount": {"currency": "GBP", "minorUnits": pence},
            "direction": direction,
            "transactionTime": f"2026-09-{day:02}T09:15:00.000Z",
            "source": "FASTER_PAYMENTS_OUT",
            "status": "SETTLED",
            "counterPartyName": who,
            "reference": who,
        }

    @staticmethod
    def aggregator_record(ident: str, amount: str, day: int, who: str) -> dict:
        return {
            "transaction_id": f"volatile-{ident}",
            "normalised_provider_transaction_id": ident,
            "timestamp": f"2026-09-{day:02}T10:00:00Z",
            "description": who,
            "amount": amount,
            "currency": "GBP",
            "transaction_type": "DEBIT" if amount.startswith("-") else "CREDIT",
        }

    def counted(self, store, ref: str) -> int:
        return build_ledger(store, ref, "2026-09", bound=False, label=ref).position.rows_counted

    @pytest.mark.parametrize("feed_first", [True, False], ids=["feed-first", "aggregator-first"])
    def test_BillSeenUnderTheSpaceAndUnderMain_IsOneCountedRowInTheSpace_AfterARebuild(
        self, store, feed_first
    ):
        self.land_accounts(store)

        def feed():
            self.land_the_feed(store)

        def aggregator():
            self.land_aggregator(
                store, self.aggregator_record("tl-bill", "-50.00", 5, "WATER CO DD")
            )

        for step in (feed, aggregator) if feed_first else (aggregator, feed):
            step()

        report = rebuild_from_raw(store, account_map=PROVIDER_MAP)

        assert report.space_folded == 1
        assert "folded into their Space rows" in report.describe()
        assert self.counted(store, BILLS) == 1
        assert self.counted(store, MAIN) == 1

    @pytest.mark.parametrize("feed_first", [True, False], ids=["feed-first", "export-first"])
    def test_BillSeenUnderTheSpaceAndInTheExport_IsOneCountedRowInTheSpace_AfterARebuild(
        self, store, feed_first
    ):
        self.land_accounts(store)

        def feed():
            self.land_the_feed(store)

        def export():
            self.land_export(store, ("05/09/2026", "Water Co", "Water Co", "-50.00"))

        for step in (feed, export) if feed_first else (export, feed):
            step()

        report = rebuild_from_raw(store, account_map=PROVIDER_MAP)

        assert report.problems == []
        assert report.space_folded == 1
        assert self.counted(store, BILLS) == 1
        assert self.counted(store, MAIN) == 1

    def test_ARebuild_ReproducesWhatLiveIngestionSettledOnAfterEveryArrival(self, tmp_path):
        """Live: each artefact is resolved and the settling pass run after it.
        Rebuild: every artefact is replayed and the pass run once at the end.
        The two must hold the same rows in the same states."""
        with Store(tmp_path / "raw.sqlite3") as raw:
            self.land_accounts(raw)
            self.land_aggregator(
                raw,
                self.aggregator_record("tl-bill", "-50.00", 5, "WATER CO DD"),
                self.aggregator_record("tl-rent", "-400.00", 6, "LANDLORD"),
                self.aggregator_record("tl-shop", "-12.00", 7, "SHOP"),
            )
            self.land_feed(
                raw,
                "cat-bills",
                self.feed_item("uid-bill", 5000, 5, "Water Co"),
                self.feed_item("uid-rent", 40000, 6, "Landlord"),
            )
            self.land_feed(raw, "cat-main", self.feed_item("uid-shop", 1200, 7, "Shop"))
            self.land_aggregator(raw, self.aggregator_record("tl-late", "-9.99", 8, "NEW THING"))
            rebuild_from_raw(raw, account_map=MAP)
            rebuilt = signature(raw)
            artefacts = raw.connection.execute(
                "SELECT rowid, source, account_ref, digest, payload, origin "
                "FROM raw_artefacts ORDER BY fetched_at ASC, rowid ASC"
            ).fetchall()
            defaults = {"cat-main": "acc-main"}

        with Store(tmp_path / "live.sqlite3") as live:
            for artefact in artefacts:
                if str(artefact["source"]) not in ("truelayer-booked", "starling-feed"):
                    continue
                ref = resolve_artefact_ref(artefact, MAP, defaults)
                transactions = parse_artefact_transactions(
                    str(artefact["source"]), artefact["payload"], ref, str(artefact["digest"])
                )
                reconcile_batch(live, transactions, digest=str(artefact["digest"]))
                fold_space_copies(live, MAP)
            lived = signature(live)

        assert lived == rebuilt
        assert sorted(state for _, _, state in rebuilt) == [
            "booked", "booked", "booked", "booked", "folded", "folded",
        ]


def signature(store: Store) -> list[tuple[str, int, str]]:
    return sorted((t.account_id, t.amount_minor, str(t.status)) for t in store.all_transactions())


class TestWhatTheOperatorIsTold:
    def test_AnImportOfTheExport_SaysHowManyRowsItFoldedIntoSpaces(self, store, tmp_path):
        home = Household(store)
        home.arrive(pay(BILLS, FEED, "uid-bill", -5000, 5, "Water Co"))
        export = tmp_path / "export.csv"
        export.write_text(
            "Date,Counter Party,Reference,Type,Amount (GBP),Balance (GBP),Notes\n"
            "05/09/2026,Water Co,Water Co,FASTER PAYMENT,-50.00,0.00,\n",
            encoding="utf-8",
        )

        summary = import_file(store, export, account_id=MAIN, account_map=MAP)

        assert isinstance(summary, ImportSummary)
        assert summary.folded == 1
        assert "folded 1 main-account row into their Space rows" in summary.describe()
        assert home.counted(BILLS) == 1

    def test_AnImportWithNothingToFold_SaysNothingAboutIt(self, store, tmp_path):
        export = tmp_path / "export.csv"
        export.write_text(
            "Date,Counter Party,Reference,Type,Amount (GBP),Balance (GBP),Notes\n"
            "05/09/2026,Water Co,Water Co,FASTER PAYMENT,-50.00,0.00,\n",
            encoding="utf-8",
        )

        summary = import_file(store, export, account_id=MAIN, account_map=MAP)

        assert summary.folded == 0
        assert "folded" not in summary.describe()


class TestIdentityHealthIsUnmoved:
    def test_FoldingACopy_LeavesEverySourcesCountOfItsOwnPaymentsAgreeingWithTheRows(self, home):
        """One payment from the Space, reported twice. The aggregator reported
        one payment and its own row still holds it; the Space row's copied
        sighting names no provider id, so the Space gains no phantom feed."""
        home.arrive(*bill_from_the_space())

        health = identity_health(home.store)

        by_feed = {(t.account_id, t.source): (t.reported, t.held) for t in health.tallies}
        assert by_feed == {(BILLS, FEED): (1, 1), (MAIN, AGGREGATOR): (1, 1)}
        assert (health.folded, health.surplus) == (0, 0)


def planned_row(
    entity: str, account: str, source: str, minor: int, day: date, *, status=BOOKED
) -> Transaction:
    return Transaction(
        account_id=account,
        amount_minor=minor,
        value_date=day,
        booking_date=day,
        description=entity,
        source=source,
        entity_id=entity,
        status=status,
    )


FEEDS = {FEED: {MAIN, BILLS}, AGGREGATOR: {MAIN}}


LANDER = TestRebuiltFromRaw()


class TestAnAccountThatIsNotASpaceIsNeverATarget:
    """Sharing an aggregator with the main account does not make another bank's
    account a Space of it. Two unrelated payments of one round figure on one
    day, one at each bank, are two payments."""

    @pytest.mark.parametrize("feed_first", [True, False], ids=["feed-first", "halifax-first"])
    def test_TwoBanksSharingAnAggregator_EachKeepsItsOwnSameDayPayment(self, store, feed_first):
        """Two payments of 50.00 on one day: the garage, from the Starling
        account (reported by the feed alone), and the gym, from the Halifax
        account (reported by the aggregator)."""
        home = Household(store, TWO_BANKS)
        arrivals = [
            pay(MAIN, FEED, "uid-garage", -5000, 5, "Garage"),
            pay(HALIFAX, AGGREGATOR, "tl-gym", -5000, 5, "GYM"),
        ]
        for item in arrivals if feed_first else reversed(arrivals):
            report = home.arrive(item)

        assert report.folded == 0
        assert home.counted(MAIN) == 1
        assert home.counted(HALIFAX) == 1
        assert home.statuses(MAIN) == ["booked"]
        assert home.statuses(HALIFAX) == ["booked"]

    @pytest.mark.parametrize("feed_first", [True, False], ids=["feed-first", "halifax-first"])
    def test_TwoBanksSharingAnAggregator_EachKeepsItsOwnSameDayPayment_AfterARebuild(
        self, store, feed_first
    ):
        LANDER.land_accounts(store)

        def feed():
            LANDER.land_feed(
                store, "cat-main", LANDER.feed_item("uid-garage", 5000, 5, "Garage")
            )
            LANDER.land_feed(
                store, "cat-bills", LANDER.feed_item("uid-other", 700, 20, "Cafe")
            )

        def halifax():
            LANDER.land_aggregator(
                store,
                LANDER.aggregator_record("tl-gym", "-50.00", 5, "GYM"),
                account="tl-halifax",
            )

        for step in (feed, halifax) if feed_first else (halifax, feed):
            step()

        report = rebuild_from_raw(store, account_map=TWO_BANKS)

        assert report.space_folded == 0
        assert LANDER.counted(store, MAIN) == 1
        assert LANDER.counted(store, HALIFAX) == 1

    PERMUTATIONS: ClassVar[list[tuple[str, ...]]] = list(
        itertools.permutations(["space-bill", "main-copy", "halifax-gym"])
    )

    @pytest.mark.parametrize("order", PERMUTATIONS, ids=["-".join(o) for o in PERMUTATIONS])
    def test_ARealSpaceRowAndAnotherBanksRow_OnlyTheSpaceRowIsPaired(self, store, order):
        """Two payments of 50.00 on one day: a water bill from the Bills Space
        (the feed under the Space, the aggregator's copy under main) and a gym
        payment from the Halifax account."""
        home = Household(
            store,
            household_map(
                spaces_declared=True,
                extra_bindings=(AccountBinding(HALIFAX, "truelayer", "tl-halifax"),),
            ),
        )
        arrivals = {
            "space-bill": pay(BILLS, FEED, "uid-bill", -5000, 5, "Water Co"),
            "main-copy": pay(MAIN, AGGREGATOR, "tl-bill", -5000, 5, "WATER CO DD"),
            "halifax-gym": pay(HALIFAX, AGGREGATOR, "tl-gym", -5000, 5, "GYM"),
        }
        for name in order:
            home.arrive(arrivals[name])

        assert home.statuses(MAIN) == ["folded"]
        assert home.statuses(HALIFAX) == ["booked"]
        assert home.sources_of(BILLS, -5000) == ["starling", "truelayer"]
        assert home.sources_of(HALIFAX, -5000) == ["truelayer"]

    def test_ASpaceOfAnotherStarlingAccount_IsNeverATargetForThisOnesMainAccount(self, store):
        """Two Starling accounts, each with a Space. One payment of 50.00 from
        the first account's Bills Space, and one of 50.00 from the second
        account (reported by the aggregator): two payments, two places."""
        joint_map = household_map(
            extra_bindings=(
                AccountBinding(JOINT, "starling", "acc-two"),
                AccountBinding(JOINT_SPACE, "starling", "cat-joint-space"),
                AccountBinding(JOINT, "truelayer", "tl-joint"),
            )
        )
        LANDER.land_accounts(store, second=True)
        LANDER.land_feed(store, "cat-bills", LANDER.feed_item("uid-bill", 5000, 5, "Water Co"))
        LANDER.land_feed(
            store,
            "cat-joint-space",
            LANDER.feed_item("uid-trip", 900, 20, "Hotel"),
            account="acc-two",
        )
        LANDER.land_aggregator(
            store,
            LANDER.aggregator_record("tl-shop", "-50.00", 5, "SHOP"),
            account="tl-joint",
        )

        report = rebuild_from_raw(store, account_map=joint_map)

        assert report.space_folded == 0
        assert LANDER.counted(store, BILLS) == 1
        assert LANDER.counted(store, JOINT) == 1


class TestHowASpaceIsKnownToBeOne:
    def bill_through_the_feed_and_the_aggregator(self, home):
        home.arrive(pay(MAIN, FEED, "uid-salary", 50000, 1, "Employer"))
        home.arrive(*bill_from_the_space())

    def test_ASpaceKnownOnlyFromTheFeedsOwnStructure_StillFolds(self, store):
        LANDER.land_accounts(store)
        LANDER.land_the_feed(store)
        LANDER.land_aggregator(
            store, LANDER.aggregator_record("tl-bill", "-50.00", 5, "WATER CO DD")
        )

        report = rebuild_from_raw(store, account_map=PROVIDER_MAP)

        assert report.space_folded == 1
        assert space_parents(store, PROVIDER_MAP) == {BILLS: MAIN}

    def test_ASpaceKnownOnlyFromTheRegistry_StillFolds(self, store):
        home = Household(store, MAP)

        self.bill_through_the_feed_and_the_aggregator(home)

        assert space_parents(store, MAP) == {BILLS: MAIN, HOLIDAY: MAIN}
        assert home.statuses(MAIN) == ["booked", "folded"]
        assert home.counted(BILLS) == 1

    def test_ASpaceKnownFromNeither_IsNotATarget(self, store):
        home = Household(store, PROVIDER_MAP)

        self.bill_through_the_feed_and_the_aggregator(home)

        assert space_parents(store, PROVIDER_MAP) == {}
        assert home.statuses(MAIN) == ["booked", "booked"]

    def test_TheRegistryAndTheFeedDisagreeingOnWhoseSpaceItIs_MakeItNobodys(self, store):
        disagree = household_map(
            extra_bindings=(AccountBinding(JOINT, "starling", "acc-two"),),
            extra_records=(
                AccountRecord(
                    ref=AccountRef(BILLS), kind="starling-space", parent=AccountRef(JOINT)
                ),
            ),
        )
        LANDER.land_accounts(store, second=True)
        LANDER.land_feed(store, "cat-bills", LANDER.feed_item("uid-bill", 5000, 5, "Water Co"))

        assert space_parents(store, disagree) == {}


class TestChainsOfSameAmountPayments:
    def test_ADailyPaymentForYears_IsPairedOffInFull_WithoutExhaustingTheStack(self):
        """1,500 payments of 5.00, one a day, each from the Space and each
        reported again under main: every window overlaps its neighbours, so
        the whole run is one group."""
        start = date(2022, 1, 1)
        rows = []
        for n in range(1500):
            when = start + timedelta(days=n)
            rows.append(planned_row(f"s{n:04}", BILLS, FEED, -500, when))
            rows.append(planned_row(f"m{n:04}", MAIN, AGGREGATOR, -500, when))

        plan = plan_folds(rows, {}, FEEDS, {BILLS: MAIN})

        assert len(plan.folds) == 1500
        assert set(plan.folds) == {f"m{n:04}" for n in range(1500)}
        assert set(plan.folds.values()) == {f"s{n:04}" for n in range(1500)}
        assert (plan.ambiguous, plan.unmatched) == (0, 0)

    def test_ARunWithOneMoreMainRowThanSpaceRows_FoldsNoneOfIt(self):
        """Five payments from the Space, six main rows in the same run: one
        main row is real or a duplicate of something unseen, and nothing says
        which, so the whole run is left counted."""
        start = date(2026, 9, 1)
        rows = [
            planned_row(f"s{n}", BILLS, FEED, -500, start + timedelta(days=n)) for n in range(5)
        ]
        rows += [
            planned_row(f"m{n}", MAIN, AGGREGATOR, -500, start + timedelta(days=n))
            for n in range(6)
        ]

        plan = plan_folds(rows, {}, FEEDS, {BILLS: MAIN})

        assert plan.folds == {}
        assert plan.ambiguous == 6
