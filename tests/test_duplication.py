"""Measuring how much of the corpus is the same payment arriving again.

The question this answers is whether a replay could skip work, and the
answer depends entirely on WHICH comparison is used. A provider that
re-renders one field per request makes two byte-different records out of
one payment, so a measurement that stops at the bytes reports no
redundancy and is wrong about all of it.
"""

from __future__ import annotations

import json

from obdi.duplication import analyse
from obdi.providers import starling, truelayer
from obdi.store import Store


def _feed(store, items):
    store.land_artefact(
        starling.artefact_for(
            json.dumps({"feedItems": items}).encode(),
            account_id="starling:cat-1",
            kind="feed",
            origin=f"https://api.example.com/feed/account/a/category/cat-1?n={len(items)}",
        )
    )


def _item(uid, minor=1234, party="Tesco", when="2026-03-14T09:15:00.000Z"):
    return {
        "feedItemUid": uid,
        "amount": {"currency": "GBP", "minorUnits": minor},
        "direction": "OUT",
        "transactionTime": when,
        "source": "MASTER_CARD",
        "status": "SETTLED",
        "counterPartyName": party,
        "reference": "REF",
    }


def _truelayer(store, records, marker):
    store.land_artefact(
        truelayer.artefact_for(
            json.dumps({"results": records}).encode(),
            account_id="tl-1",
            kind="booked",
            requested=f"from=2026-05-04&to=2026-08-02&n={marker}",
        )
    )


def _tl_record(durable, volatile, amount=-12.34):
    """A TrueLayer record, whose transaction_id changes between requests.

    Not an invention for the test: the provider documents transaction_id
    as changing per request, which is why matching rests on
    normalised_provider_transaction_id instead.
    """
    return {
        "transaction_id": volatile,
        "normalised_provider_transaction_id": durable,
        "timestamp": "2026-07-01T00:00:00Z",
        "amount": amount,
        "currency": "GBP",
        "description": "COFFEE SHOP",
    }


class TestRedundancyIsMeasuredAtEveryTierBecauseTheTiersDisagree:
    def test_AWindowFetchedTwiceUnchanged_IsReportedAsWhollyRedundant(
        self, tmp_path
    ):
        """The sliding-window case: ask again, get the same answer.

        Two fetches of a quiet period deliver the same payments, so the
        second artefact is entirely redundant - and since nothing about
        the records differs, every tier agrees.
        """
        with Store(tmp_path / "s.sqlite3") as store:
            items = [_item(f"uid-{n}", minor=100 + n) for n in range(5)]
            _feed(store, items)
            _feed(store, [*items, _item("uid-new", minor=999)])

            report = analyse(store)

        assert report.records == 11
        assert report.distinct == 6
        assert report.identities_repeated == 5
        assert report.identities_varied == 0

    def test_AProviderThatRerendersOneFieldPerRequest_DefeatsByteComparison(
        self, tmp_path
    ):
        """The trap, and the reason this is measured rather than assumed.

        Every one of these is one payment fetched twice. Comparing bytes
        finds no repetition at all and would conclude there is nothing to
        skip; comparing durable identity finds that half the corpus is a
        repeat. The difference is a single field the provider regenerates.
        """
        with Store(tmp_path / "s.sqlite3") as store:
            first = [_tl_record(f"ntl-{n}", f"first-{n}") for n in range(6)]
            second = [_tl_record(f"ntl-{n}", f"second-{n}") for n in range(6)]
            _truelayer(store, first, "a")
            _truelayer(store, second, "b")

            report = analyse(store)

        pipe = next(s for s in report.sources if s.records)
        assert pipe.records == 12
        assert pipe.distinct_bytes == 12, "byte comparison should see no repeats"
        assert pipe.distinct_identity == 6, "identity should see half as repeats"
        assert pipe.redundancy() == 0.5

        # Every variation is cosmetic: nothing about the payments changed.
        assert report.identities_varied == 6
        assert report.identities_amended == 0
        assert report.cosmetic_variation == 6
        assert report.churn[0][0] == "transaction_id"

    def test_APaymentThatGenuinelyChanges_IsNotCountedAsCosmetic(self, tmp_path):
        """An amendment is real information and must not be filed as noise.

        A pending amount that settles differently, or a description the
        bank rewrites, changes what is true about the payment. Skipping
        that record would discard the correction - so the two kinds of
        variation are counted apart.
        """
        with Store(tmp_path / "s.sqlite3") as store:
            _feed(store, [_item("uid-1", minor=1000)])
            _feed(store, [_item("uid-1", minor=1250)])

            report = analyse(store)

        assert report.identities_varied == 1
        assert report.identities_amended == 1
        assert report.cosmetic_variation == 0

    def test_RecordsWithoutADurableIdentity_AreCountedRatherThanAssumedUnique(
        self, tmp_path
    ):
        """Silence about them would overstate what a skip could achieve.

        A record carrying no durable id cannot be shown to be a repeat,
        so it is reported separately instead of being folded into either
        answer.
        """
        with Store(tmp_path / "s.sqlite3") as store:
            _truelayer(
                store,
                [
                    {
                        "timestamp": "2026-07-01T00:00:00Z",
                        "amount": -5.00,
                        "currency": "GBP",
                        "description": "NO ID HERE",
                    }
                ],
                "c",
            )

            report = analyse(store)

        pipe = next(s for s in report.sources if s.records)
        assert pipe.without_identity == 1
        assert pipe.distinct_identity == 0

    def test_NonTransactionalArtefacts_AreLeftOutOfTheMeasurement(self, tmp_path):
        """Balances and account lists are evidence, not records to replay.

        Counting them would inflate the redundancy figure with artefacts
        a rebuild never processes, making the case for skipping look
        stronger than it is.
        """
        with Store(tmp_path / "s.sqlite3") as store:
            _feed(store, [_item("uid-1")])
            store.land_artefact(
                truelayer.artefact_for(
                    json.dumps({"results": [{"account_id": "a", "x": 1}]}).encode(),
                    account_id="tl-1",
                    kind="accounts",
                    requested="accounts",
                )
            )

            report = analyse(store)

        assert [s.source for s in report.sources] == ["starling-feed"]

    def test_TheReportReadsAsATableRatherThanRequiringTheCaller(self, tmp_path):
        with Store(tmp_path / "s.sqlite3") as store:
            items = [_item(f"uid-{n}") for n in range(3)]
            _feed(store, items)
            _feed(store, items)

            text = analyse(store).describe()

        assert "SOURCE" in text and "REDUNDANT" in text
        assert "starling-feed" in text
        assert "TIMES SEEN" in text
