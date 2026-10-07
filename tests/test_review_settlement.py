"""A flag the evidence already answers is closed by a rule, not left for a person.

The queue flags a stored-as-new row when something in the same account matches
it on amount and date and only the same-source rule kept the two apart. Often
the evidence then proves them two payments: one response named both under two
provider ids, or the source never reuses an id. Such a flag asks a question
that has been answered, and nothing resolves it.

Every scenario fixes its answer before the first run, in the docstring.
Provider ids and responses are invented. Responses are landed as raw artefacts
and replayed by a rebuild, which is how the deployed store arrives at its flags.
"""

from __future__ import annotations

import itertools
import json
from datetime import date

import pytest

from obdi.core.models import SourceTier, Transaction, TransactionStatus
from obdi.ingest.identity import content_key
from obdi.ingest.pipeline import reconcile_batch
from obdi.ingest.providers import starling, truelayer
from obdi.ingest.rebuild import rebuild_from_raw
from obdi.ingest.store import Store
from obdi.review_report import FlagClass, classify_flags
from obdi.review_settlement import settle_review_flags

STARLING_ACCOUNT = "starling:cat-1"
TRUELAYER_ACCOUNT_REF = "tl-1"


def _fare(uid: str, *, day: int = 14) -> dict:
    return {
        "feedItemUid": uid,
        "amount": {"currency": "GBP", "minorUnits": 250},
        "direction": "OUT",
        "transactionTime": f"2026-09-{day:02}T09:15:00.000Z",
        "source": "MASTER_CARD",
        "status": "SETTLED",
        "counterPartyName": "Example Buses",
        "reference": "REF",
    }


def _starling_response(store: Store, items: list[dict], cycle: int) -> None:
    store.land_artefact(
        starling.artefact_for(
            json.dumps({"feedItems": items}).encode(),
            account_id=STARLING_ACCOUNT,
            kind="feed",
            origin=f"https://api.example.com/feed/account/a/category/cat-1?c={cycle}",
        )
    )


def _record(tid: str, *, day: int = 14) -> dict:
    return {
        "transaction_id": tid,
        "normalised_provider_transaction_id": tid,
        "timestamp": f"2026-09-{day:02}T09:15:00Z",
        "amount": -2.50,
        "currency": "GBP",
        "description": "EXAMPLE BUSES",
    }


def _truelayer_response(store: Store, records: list[dict], cycle: int, *, kind: str) -> None:
    store.land_artefact(
        truelayer.artefact_for(
            json.dumps({"results": records, "status": "Succeeded"}).encode(),
            account_id=TRUELAYER_ACCOUNT_REF,
            kind=kind,
            requested=f"c={cycle}",
        )
    )


def _booked(store: Store, ids: list[str], cycle: int) -> None:
    _truelayer_response(store, [_record(i) for i in ids], cycle, kind="booked")


def _open_ids(store: Store) -> set[str]:
    return {str(row["entity_id"]) for row in store.review_queue()}


def _open_source_ids(store: Store) -> set[str]:
    """The provider ids of the rows still flagged."""
    ids = _open_ids(store)
    return {
        str(row["source_id"])
        for row in store.connection.execute("SELECT entity_id, source_id FROM transactions")
        if str(row["entity_id"]) in ids
    }


def _txn(
    *,
    account: str,
    source: str = "truelayer",
    source_id: str,
    amount: int = -250,
    when: date = date(2026, 9, 14),
    status: TransactionStatus = TransactionStatus.BOOKED,
    description: str = "EXAMPLE BUSES",
) -> Transaction:
    return Transaction(
        account_id=account,
        amount_minor=amount,
        value_date=when,
        booking_date=when,
        description=description,
        source=source,
        source_id=source_id,
        tier=SourceTier.AUTHORITATIVE,
        status=status,
        content_key=content_key(amount_minor=amount, value_date=when, description=description),
    )


def _land(store: Store, digest: str, *transactions: Transaction) -> None:
    reconcile_batch(store, list(transactions), digest=digest)


@pytest.fixture
def store(tmp_path):
    with Store(tmp_path / "s.sqlite3") as opened:
        yield opened


class TestPaymentsTheProviderReportedAsTwoAreNotLeftFlagged:
    def test_TwoIdenticalFares_ListedTogetherInOneResponse_NoFlagRemainsOpen(self, store):
        """One response naming two ids is the provider saying two payments."""
        _booked(store, ["fare-1", "fare-2"], cycle=0)
        rebuild_from_raw(store)

        assert _open_ids(store) == set()

    def test_FareHeldAlone_ThenResponseListingNewcomerAndIt_NoFlagRemainsOpen(self, store):
        _booked(store, ["fare-1"], cycle=0)
        _booked(store, ["fare-2", "fare-1"], cycle=1)
        rebuild_from_raw(store)

        assert _open_ids(store) == set()

    def test_TwoFaresEachAlone_StayFlaggedUntilAResponseListsBoth(self, store):
        """1 open after the second response alone; 0 once a third lists both.
        Proof can arrive later, which is why the flag is not suppressed when
        it is raised."""
        _booked(store, ["fare-1"], cycle=0)
        _booked(store, ["fare-2"], cycle=1)
        rebuild_from_raw(store)
        assert len(_open_ids(store)) == 1

        _booked(store, ["fare-1", "fare-2"], cycle=2)
        rebuild_from_raw(store)
        assert _open_ids(store) == set()

    def test_TwoFares_NeverListedTogether_StayFlagged(self, store):
        """No response ever named both, so a duplicate report is still possible."""
        _booked(store, ["fare-1"], cycle=0)
        _booked(store, ["fare-2"], cycle=1)
        rebuild_from_raw(store)

        assert len(_open_ids(store)) == 1

    def test_TwoFares_ListedTogetherOnlyInAPendingSnapshot_StayFlagged(self, store):
        """A pending snapshot lists whatever is pending, and reissues ids, so
        two ids in one of them prove nothing about two payments."""
        _booked(store, ["fare-1"], cycle=0)
        _booked(store, ["fare-2"], cycle=1)
        _truelayer_response(
            store, [_record("fare-1"), _record("fare-2")], 2, kind="pending"
        )
        rebuild_from_raw(store)

        assert len(_open_ids(store)) == 1

    def test_FlaggedFare_WithOneProvenNeighbourAndOneUnproven_StaysFlagged(self, store):
        """Fares 1 and 2 are listed together; fare 3 is listed with fare 1
        only. Fares 2 and 3 resemble each other and no response names both, so
        each is still a possible duplicate of the other: 2 open, fares 2 and 3.
        Fare 1 is flagged by neither arrival."""
        _booked(store, ["fare-1", "fare-2"], cycle=0)
        _booked(store, ["fare-3", "fare-1"], cycle=1)
        rebuild_from_raw(store)

        assert _open_source_ids(store) == {"fare-2", "fare-3"}

    def test_PendingPaymentVoided_ThenBookedRecordArrives_NoFlagRemainsOpen(self, store):
        """The only near-miss is history: the pending row was voided when it
        left the pending set, so the booked record is not a second payment."""
        _truelayer_response(store, [_record("pend-1")], 0, kind="pending")
        _truelayer_response(store, [], 1, kind="pending")
        _booked(store, ["book-1"], cycle=2)
        rebuild_from_raw(store)

        assert _open_ids(store) == set()

    def test_StarlingFares_WithTwoIdsNeverListedTogether_NoFlagRemainsOpen(self, store):
        """Starling names a payment by one id for its whole life, so a second
        id is a second payment however the responses were cut."""
        _starling_response(store, [_fare("fare-1")], cycle=0)
        _starling_response(store, [_fare("fare-2")], cycle=1)
        rebuild_from_raw(store)

        assert _open_ids(store) == set()


class TestARebuildSaysWhatItSettled:
    def test_Rebuild_WhenFlagsWereSettled_DescribesHowManyAndHowManyRemain(self, store):
        _booked(store, ["fare-1", "fare-2"], cycle=0)

        report = rebuild_from_raw(store)

        assert report.review_settled == {FlagClass.LISTED_TOGETHER: 1}
        assert "1 review flag settled" in report.describe()
        assert "0 remain open" in report.describe()

    def test_Rebuild_WhenNothingWasSettled_SaysNothingAboutIt(self, store):
        _booked(store, ["fare-1"], cycle=0)

        assert "review flag" not in rebuild_from_raw(store).describe()


class TestAFoldedRowsFlagClosesAndItsTwinsDoesNot:
    def test_FlaggedMainAccountRow_WhenFoldedIntoASpaceRow_ItsFlagCloses(self, store):
        """Main-account f2 and Space s2 are each flagged against an unproven
        twin. f2 is then folded into s2: f2's flag closes (it is history), and
        s2's stays (its neighbour s1 is live and unproven)."""
        _land(store, "m1", _txn(account="main", source_id="f1"))
        _land(store, "m2", _txn(account="main", source_id="f2"))
        _land(store, "s1", _txn(account="space", source_id="s1"))
        _land(store, "s2", _txn(account="space", source_id="s2"))
        by_id = {t.source_id: t.entity_id for t in store.all_transactions()}
        assert _open_ids(store) == {by_id["f2"], by_id["s2"]}

        store.replace_space_folds({by_id["f2"]: by_id["s2"]})
        report = settle_review_flags(store)

        assert _open_ids(store) == {by_id["s2"]}
        assert report.settled[FlagClass.ROW_IS_HISTORY] == 1


class TestTheRulesNeverTouchAPersonsDecision:
    def test_ResolvedFlag_OnAProvenPair_IsUntouchedByThePassAndByARebuild(self, store):
        _booked(store, ["fare-1"], cycle=0)
        _booked(store, ["fare-2"], cycle=1)
        rebuild_from_raw(store)
        (flagged,) = _open_ids(store)
        store.resolve_review(flagged)
        _booked(store, ["fare-1", "fare-2"], cycle=2)

        settle_review_flags(store)
        rebuild_from_raw(store)

        (kept,) = store.review_queue(include_resolved=True)
        assert kept["entity_id"] == flagged
        assert kept["resolved_at"] is not None

    def test_ResolvedFlag_WhoseRowIsHistory_IsKeptNotDeleted(self, store):
        _land(store, "d1", _txn(account="acct", source_id="a1"))
        _land(store, "d2", _txn(account="acct", source_id="a2"))
        (flagged,) = _open_ids(store)
        store.resolve_review(flagged)
        store.connection.execute("UPDATE transactions SET status = 'void'")
        store.connection.commit()

        report = settle_review_flags(store)

        assert len(store.review_queue(include_resolved=True)) == 1
        assert sum(report.settled.values()) == 0


class TestTheSettledStateIsDeterministic:
    def test_TwoRebuilds_LeaveTheSameOpenFlags(self, store):
        _booked(store, ["fare-1"], cycle=0)
        _booked(store, ["fare-2"], cycle=1)
        _booked(store, ["fare-3", "fare-1"], cycle=2)
        rebuild_from_raw(store)
        first = _open_ids(store)
        rebuild_from_raw(store)

        assert _open_ids(store) == first

    def test_ThePassRunTwice_SettlesNothingTheSecondTime(self, store):
        _land(store, "d1", _txn(account="acct", source_id="a1"))
        _land(store, "d1", _txn(account="acct", source_id="a2"))
        assert sum(settle_review_flags(store).settled.values()) == 1

        assert sum(settle_review_flags(store).settled.values()) == 0

    @pytest.mark.parametrize("order", list(itertools.permutations(range(3))))
    def test_TwoFaresAndAResponseListingBoth_InAnyArrivalOrder_LeaveNoFlagOpen(
        self, store, order
    ):
        responses = [["fare-1"], ["fare-2"], ["fare-1", "fare-2"]]
        for cycle, which in enumerate(order):
            _booked(store, responses[which], cycle=cycle)
        rebuild_from_raw(store)

        assert _open_ids(store) == set()

    @pytest.mark.parametrize("order", list(itertools.permutations(range(3))))
    def test_ThreeFaresEachAlone_InAnyArrivalOrder_LeaveTwoFlagsOpen(self, store, order):
        """The first to arrive has nothing to be flagged against; each later
        one is. Two, whichever fare was first."""
        for cycle, which in enumerate(order):
            _booked(store, [f"fare-{which}"], cycle=cycle)
        rebuild_from_raw(store)

        assert len(_open_ids(store)) == 2


class TestEveryOpenFlagIsGivenExactlyOneClass:
    """One store with a flag of each class, each in its own account."""

    def _build(self, store: Store) -> dict[str, str]:
        _land(store, "g1", _txn(account="gone", source_id="g1"))
        _land(store, "g2", _txn(account="gone", source_id="g2"))
        _land(store, "h1", _txn(account="history", source_id="h1"))
        _land(store, "h2", _txn(account="history", source_id="h2"))
        _land(store, "n1", _txn(account="nolive", source_id="n1"))
        _land(store, "n2", _txn(account="nolive", source_id="n2"))
        _land(
            store,
            "t1",
            _txn(account="together", source_id="t1"),
            _txn(account="together", source_id="t2"),
        )
        _land(store, "k1", _txn(account="kept", source="starling", source_id="k1"))
        _land(store, "k2", _txn(account="kept", source="starling", source_id="k2"))
        _land(store, "o1", _txn(account="open", source_id="o1"))
        _land(store, "o2", _txn(account="open", source_id="o2"))
        _land(store, "o3", _txn(account="open", source_id="o3"))
        by_id = {t.source_id: t.entity_id for t in store.all_transactions()}
        execute = store.connection.execute
        execute("DELETE FROM transactions WHERE entity_id = ?", (by_id["g2"],))
        execute("UPDATE transactions SET status = 'void' WHERE entity_id = ?", (by_id["h2"],))
        execute("UPDATE transactions SET status = 'void' WHERE entity_id = ?", (by_id["n1"],))
        store.connection.commit()
        return by_id

    def test_Classify_GivesEachFlagItsStrongestProof(self, store):
        by_id = self._build(store)
        classes = classify_flags(store)

        assert classes[by_id["g2"]] is FlagClass.ROW_GONE
        assert classes[by_id["h2"]] is FlagClass.ROW_IS_HISTORY
        assert classes[by_id["n2"]] is FlagClass.NO_LIVE_NEIGHBOUR
        assert classes[by_id["t2"]] is FlagClass.LISTED_TOGETHER
        assert classes[by_id["k2"]] is FlagClass.IDS_KEPT_FOR_LIFE
        assert classes[by_id["o2"]] is FlagClass.OPEN
        assert classes[by_id["o3"]] is FlagClass.OPEN
        assert len(classes) == 7

    def test_Settle_ClosesEveryClassButOpen_AndCountsEach(self, store):
        by_id = self._build(store)
        report = settle_review_flags(store)

        assert report.settled == {
            FlagClass.ROW_GONE: 1,
            FlagClass.ROW_IS_HISTORY: 1,
            FlagClass.NO_LIVE_NEIGHBOUR: 1,
            FlagClass.LISTED_TOGETHER: 1,
            FlagClass.IDS_KEPT_FOR_LIFE: 1,
        }
        assert _open_ids(store) == {by_id["o2"], by_id["o3"]}
        assert report.still_open == 2

    def test_Classify_WhenAProofIsPartial_FallsThroughToOpen(self, store):
        """Flag on o3 has two live neighbours; list one with it and it stays."""
        by_id = self._build(store)
        _land(
            store,
            "t-o",
            _txn(account="open", source_id="o3"),
            _txn(account="open", source_id="o1"),
        )

        assert classify_flags(store)[by_id["o3"]] is FlagClass.OPEN
