"""The ledger says what each source stated of a row and how its sighting joined it.

A row is one payment seen by several sources. Each row carries, a click away, every date
and instant each source stated (in the source's own field names, instants on London's
clock) and the basis its sighting joined on; the account carries a count of its rows by basis
and the dates of the rows that rest on the matcher's guess.
Every figure here is an invented household's, rendered MASKED: dates, times, field names,
and source names appear, and nothing else does.

KNOWN ANSWERS, worked out from the corpus before the first run:

    the late-settlement household, landed feed, then aggregator (stating the feed's uids),
    then export
        a normal payment's row (Bakery, made 14 September 10:20 UTC, settled 15 September
        03:00 UTC): the feed founded it (transactionTime 11:20 on London's summer clock,
        settlementTime 04:00), the aggregator joined it by id (timestamp 11:00), the export
        joined it by settlement date (Date 2026-09-15)
        a payment settled months later (its row takes the export's January date): the
        settlement is on winter time, so 02:44 UTC is 02:44
    by basis, over the account: six rows joined by settlement date (the six payments), six by
    window and description (the household's own export rows that carry no settlement time:
    1, 3, 5, 5, 8 and 12 September), and five that no second source reached (the Space
    transfer and four round-up legs); none by id alone, because every payment's export row is
    the weaker join, and a row counts under its weakest
    a payment seen only by the feed and the aggregator
        counted by id, and the page says no row rests on the guess
    the Space's row of a payment the aggregator reported under the main account
        a copy of the main account's row, joined by id
"""

from __future__ import annotations

import re

import pytest

from late_settlement_corpus import Payment, aggregator_item, household, late_settlement_payments
from obdi.ingest.family_anchors import families_of
from obdi.ingest.rebuild import rebuild_from_raw
from obdi.ingest.store import Store
from obdi.read.ledger import build_ledger
from obdi.web_ledger import render_ledger
from test_family_anchors import land_evidence
from test_id_tier import aggregator_artefact, arrive, feed_artefact
from test_space_attribution import BILLS, MAIN, MAP

REAL_ORDER = ("feed", "aggregator", "export")


@pytest.fixture
def made(tmp_path):
    opened: list[Store] = []

    def build(order, payments, **kwargs) -> Store:
        directory = tmp_path / f"{len(opened)}"
        directory.mkdir()
        opened.append(household(directory, order, payments, linked=True, **kwargs))
        return opened[-1]

    yield build
    for store in opened:
        store.close()


def page_of(store: Store, ref: str = MAIN, month: str = "2026-09") -> str:
    ledger = build_ledger(store, ref, month, bound=True, families=families_of(store, MAP))
    return render_ledger(ledger, unmasked=False).decode()


class TestWhatEachSourceStated:
    def test_Row_WhenSeenByThreeSources_ListsEachSourcesFieldsAndHowItJoined(self, made):
        page = page_of(made(REAL_ORDER, late_settlement_payments()))

        assert (
            "<code>starling</code> - the first report of this transaction: "
            "transactionTime 2026-09-14 11:20, settlementTime 2026-09-15 04:00"
        ) in page
        assert (
            "<code>truelayer</code> - matched to this transaction by its id: "
            "timestamp 2026-09-14 11:00"
        ) in page
        assert (
            "<code>starling-csv</code> - matched to this transaction by its settlement date: "
            "Date 2026-09-15"
        ) in page

    def test_Row_WhenSettledMonthsLaterInWinter_ShowsTheWinterClockForTheSettlement(self, made):
        # The export dates these two on their settlement day, and a row takes the date of
        # the latest sighting, so they are January's rows.
        page = page_of(made(REAL_ORDER, late_settlement_payments()), month="2027-01")

        assert "transactionTime 2026-09-14 11:18, settlementTime 2027-01-20 02:44" in page

    def test_Page_WhenMasked_ShowsNeitherAFigureNorAPayee(self, made):
        page = page_of(made(REAL_ORDER, late_settlement_payments()))

        for private in ("Bakery", "Tailor", "Abroad Shop", "5.20", "42.10", "Abroad"):
            assert private not in page

    def test_Row_WhenARowHoldsNoSightingDetail_HasNoDatesSection(self, tmp_path):
        with Store(tmp_path / "empty.sqlite3") as store:
            land_evidence(store)
            page = page_of(store)

        assert "What each source reported" not in page


class TestHowTheAccountsRowsJoined:
    def test_Account_CountsItsRowsByTheWeakestJoinOfEach(self, made):
        page = page_of(made(REAL_ORDER, late_settlement_payments()))

        assert (
            "6 transactions matched by settlement date, 6 transactions matched by a guess from "
            "amount, nearby dates, and text, 5 transactions reported by one source only."
        ) in page

    def test_Account_ListsTheDatesOfTheRowsThatRestOnTheGuess(self, made):
        page = page_of(made(REAL_ORDER, late_settlement_payments()))

        assert "6 matched by a guess from amount, date, and description: the dates" in page
        listed = page.split("the dates")[1]
        days = re.findall(r'<span class="mono nowrap">(2026-09-\d\d)</span>', listed)
        assert days[:6] == [
            "2026-09-01", "2026-09-03", "2026-09-05", "2026-09-05", "2026-09-08", "2026-09-12"
        ]

    def test_Account_WhenEveryJoinIsByIdOrSingle_SaysNoRowRestsOnTheGuess(self, tmp_path):
        payment = Payment("f-solo", "Solo", 700, "2026-09-14T10:00:00.000Z", None, None, 14)
        with Store(tmp_path / "ids.sqlite3") as store:
            land_evidence(store)
            arrive(store, feed_artefact([payment.feed_item()]), MAIN)
            arrive(store, aggregator_artefact([aggregator_item(payment, link=True)]), MAIN)
            page = page_of(store)

        assert "1 transaction matched by id" in page
        assert "No transaction was matched by a guess." in page

    def test_Account_WhenRebuiltFromRaw_CountsTheSameJoins(self, made):
        store = made(REAL_ORDER, late_settlement_payments())
        before = page_of(store)

        assert rebuild_from_raw(store, account_map=MAP).problems == []

        assert (
            "6 transactions matched by settlement date, 6 transactions matched by a guess"
            in page_of(store)
        )
        assert "6 transactions matched by settlement date" in before


class TestASpacesCopy:
    def test_SpaceRow_WhenTheAggregatorReportedItUnderTheMainAccount_IsACopyJoinedById(
        self, made
    ):
        payments = [
            Payment("s-hotel", "Hotel", 3000, "2026-09-10T10:00:00.000Z", None, None, 10,
                    in_feed=False),
        ]
        page = page_of(made(REAL_ORDER, payments), BILLS)

        assert (
            "<code>truelayer</code> - copied from the main account&#x27;s transaction, "
            "matched to this transaction by its id"
        ) in page
