"""The large store holds the shape it claims, by counts decided from its construction.

Every figure below follows from `large_store_corpus`: the number of payments it plants, the
sources that see each, and the doors it lands them through. A builder that drifts to a smaller
or tidier store disagrees with this record, so the speed guards built on it (`test_ledger_speed`,
`test_identity_health_speed`) cannot quietly start measuring less than they say.

KNOWN ANSWERS

    main account    5,223 booked rows: 4,811 external payments, and one leg of each of the 412
                    transfers and round-ups between it and a Space
    Spaces          5, holding 352 external payments between them and the 412 legs' other sides
    folded          352: the export and the aggregator report every Space payment as the main
                    account's, and each of those copies folds into the Space's own row
    pairs           412 confirmed transfer pairs, one per transfer or round-up
    stated          570 known balances on the main account, 2 more on other accounts
    sources         the main account is seen by the bank's feed, the export, the aggregator, and
                    the statements (all four), each row by every source that covers its date
"""

from __future__ import annotations

import pytest

from large_store_corpus import MAIN, SPACES, LargeStore, cached_large_store
from obdi.ingest.store import Store


@pytest.fixture(scope="module")
def large() -> LargeStore:
    return cached_large_store()


def scalar(large: LargeStore, sql: str, *args: object) -> int:
    with Store(large.path) as store:
        return int(store.connection.execute(sql, args).fetchone()[0])


class TestTheLargeStoreHoldsTheShapeItClaims:
    def test_MainAccount_HoldsAboutFiveThousandBookedRows(self, large):
        booked = scalar(
            large,
            "SELECT COUNT(*) FROM transactions WHERE account_id = ? AND status = 'booked'",
            MAIN,
        )

        assert booked == 5223 == large.main_rows_distinct

    def test_EverySpacePaymentIsSeenAgainAsAMainAccountCopyAndFolded(self, large):
        folded = scalar(
            large,
            "SELECT COUNT(*) FROM transactions WHERE account_id = ? AND status = 'folded'",
            MAIN,
        )

        assert folded == 352

    def test_TransfersAndRoundUps_AreEachOneConfirmedPair(self, large):
        assert scalar(large, "SELECT COUNT(*) FROM transfer_pairs") == 412

    def test_FiveSpacesEachHoldHundredsOfRows(self, large):
        held = [
            scalar(large, "SELECT COUNT(*) FROM transactions WHERE account_id = ?", space)
            for space in SPACES
        ]

        assert len(held) == 5
        assert min(held) >= 100
        assert sum(held) == 352 + 412

    def test_MainAccount_IsSeenByAllFourSources(self, large):
        with Store(large.path) as store:
            sources = {
                str(row[0])
                for row in store.connection.execute(
                    "SELECT DISTINCT s.source FROM transaction_sources s "
                    "JOIN transactions t ON t.entity_id = s.entity_id WHERE t.account_id = ?",
                    (MAIN,),
                )
            }

        assert sources == {"starling", "starling-csv", "truelayer", "starling-statement-pdf"}

    def test_MainAccount_HasKnownBalancesOnFiveHundredAndSeventyDays(self, large):
        assert large.stated_days == 570
        assert scalar(large, "SELECT COUNT(*) FROM valuations") == 570 + 2

    def test_TenOtherAccounts_EachHoldRows(self, large):
        assert len(large.other_accounts) == 10
        for ref in large.other_accounts:
            assert scalar(large, "SELECT COUNT(*) FROM transactions WHERE account_id = ?", ref) > 0

    def test_Sightings_AreManyPerRow_BecauseEveryPullReReadsItsWindow(self, large):
        rows = scalar(large, "SELECT COUNT(*) FROM transactions")
        sightings = scalar(large, "SELECT COUNT(*) FROM transaction_sources")

        assert sightings > 4 * rows


class TestTheFaithfulLargeStoreHoldsTheSameShapeOnRawArtefacts:
    """The faithful form states the same distinct payments, so the same counts follow, except
    for the pairs: it also pairs the other accounts' transfers (36 of them), which the default
    form leaves unmade."""

    @pytest.fixture(scope="class")
    def faithful(self) -> LargeStore:
        return cached_large_store(faithful=True)

    def test_MainAccountAndSpaces_HoldTheRowsTheDefaultFormHolds(self, faithful):
        booked = scalar(
            faithful,
            "SELECT COUNT(*) FROM transactions WHERE account_id = ? AND status = 'booked'",
            MAIN,
        )
        folded = scalar(
            faithful,
            "SELECT COUNT(*) FROM transactions WHERE account_id = ? AND status = 'folded'",
            MAIN,
        )

        assert (booked, folded) == (5223, 352)
        assert faithful.faithful

    def test_EveryFeedAndAggregatorAndExportRow_StandsOnARawArtefact(self, faithful):
        unbacked = scalar(
            faithful,
            "SELECT COUNT(*) FROM transaction_sources s WHERE s.source IN "
            "('starling', 'truelayer', 'starling-csv') AND NOT EXISTS ("
            "SELECT 1 FROM raw_artefacts a WHERE a.digest = s.artefact_digest)",
        )
        backed = scalar(
            faithful,
            "SELECT COUNT(*) FROM raw_artefacts WHERE source IN "
            "('starling-feed', 'truelayer-booked', 'csv')",
        )

        assert unbacked == 0
        assert backed > 1000

    def test_TransferPairs_AreTheDefaultFormsPlusTheOtherAccountsOwn(self, faithful):
        assert scalar(faithful, "SELECT COUNT(*) FROM transfer_pairs") == 412 + 36

    def test_Sightings_AreStillManyPerRow(self, faithful):
        rows = scalar(faithful, "SELECT COUNT(*) FROM transactions")
        sightings = scalar(faithful, "SELECT COUNT(*) FROM transaction_sources")

        assert sightings > 4 * rows
