"""Two real payments never share one identity, however they arrived.

A payment's identity downstream is its content key plus an occurrence number:
Actual's imported id, the annotation export, and every decision a person makes
about a row are keyed on that pair. Two bus fares of the same price on the same
day have the same content, so the occurrence is all that separates them.

Every scenario here is the same two or three payments reaching the store by a
different route, and the answer is fixed before the first run: N payments, N
distinct identities, and a payment already held keeps the identity it had.
"""

from __future__ import annotations

import json

import pytest

from obdi.core.jsontypes import rows as json_rows
from obdi.export.actual_push import ActualAccountBinding, build_envelope
from obdi.ingest.providers import starling
from obdi.ingest.rebuild import rebuild_from_raw
from obdi.ingest.store import Store

ACCOUNT = "starling:cat-1"


def _feed(store: Store, items: list[dict], cycle: int) -> None:
    store.land_artefact(
        starling.artefact_for(
            json.dumps({"feedItems": items}).encode(),
            account_id=ACCOUNT,
            kind="feed",
            origin=f"https://api.example.com/feed/account/a/category/cat-1?c={cycle}",
        )
    )


def _fare(uid: str, *, status: str = "SETTLED", day: int = 14) -> dict:
    return {
        "feedItemUid": uid,
        "amount": {"currency": "GBP", "minorUnits": 250},
        "direction": "OUT",
        "transactionTime": f"2026-09-{day:02}T09:15:00.000Z",
        "source": "MASTER_CARD",
        "status": status,
        "counterPartyName": "Example Buses",
        "reference": "REF",
    }


def _imported_ids(store: Store) -> dict[str, str]:
    """What a push would carry: imported id, keyed by the provider's own id."""
    envelope = build_envelope(
        store, [ActualAccountBinding(canonical_id=ACCOUNT, actual_account_id="act-1")], {}
    )
    pushed = {
        str(row["imported_id"])
        for row in json_rows(envelope["accounts"], "act-1")
    }
    held = {
        str(row[0]): f"{row[1]}:{row[2]}"
        for row in store.connection.execute(
            "SELECT source_id, content_key, occurrence FROM transactions "
            "WHERE status != 'void'"
        )
    }
    assert set(held.values()) == pushed, "the push and the store disagree about identities"
    return held


class TestIdenticalPaymentsKeepDistinctIdentities:
    def test_TwoIdenticalFares_ArrivingInOneResponse_PushCarriesTwoIdentities(
        self, tmp_path
    ):
        with Store(tmp_path / "s.sqlite3") as store:
            _feed(store, [_fare("fare-1"), _fare("fare-2")], cycle=0)
            rebuild_from_raw(store)
            ids = _imported_ids(store)

        assert len(ids) == 2
        assert ids["fare-1"] != ids["fare-2"]

    def test_TwoIdenticalFares_ArrivingInSeparateResponses_PushCarriesTwoIdentities(
        self, tmp_path
    ):
        """The rolling-cursor case: the provider is asked only for what changed
        since the last ask, so the second fare arrives without the first."""
        with Store(tmp_path / "s.sqlite3") as store:
            _feed(store, [_fare("fare-1")], cycle=0)
            _feed(store, [_fare("fare-2")], cycle=1)
            rebuild_from_raw(store)
            ids = _imported_ids(store)

        assert len(ids) == 2
        assert ids["fare-1"] != ids["fare-2"]

    def test_TwoIdenticalFares_RefetchedNewestFirst_PushCarriesTwoIdentities(
        self, tmp_path
    ):
        """An overlapping window that lists the newcomer ahead of the fare
        already held."""
        with Store(tmp_path / "s.sqlite3") as store:
            _feed(store, [_fare("fare-1")], cycle=0)
            _feed(store, [_fare("fare-2"), _fare("fare-1")], cycle=1)
            rebuild_from_raw(store)
            ids = _imported_ids(store)

        assert len(ids) == 2
        assert ids["fare-1"] != ids["fare-2"]

    def test_ThreeIdenticalFares_EachAloneInItsResponse_PushCarriesThreeIdentities(
        self, tmp_path
    ):
        with Store(tmp_path / "s.sqlite3") as store:
            for cycle in range(3):
                _feed(store, [_fare(f"fare-{cycle}")], cycle=cycle)
            rebuild_from_raw(store)
            ids = _imported_ids(store)

        assert len(set(ids.values())) == 3

    def test_ThirdFare_ArrivingAfterAnOverlappingRefetch_GetsItsOwnIdentity(
        self, tmp_path
    ):
        """The refetch re-reports the first fare from a different position in
        its response. That must not disturb what the store believes the first
        fare's number is, or the third fare is handed a number already in use."""
        with Store(tmp_path / "s.sqlite3") as store:
            _feed(store, [_fare("fare-1")], cycle=0)
            _feed(store, [_fare("fare-2"), _fare("fare-1")], cycle=1)
            _feed(store, [_fare("fare-3")], cycle=2)
            rebuild_from_raw(store)
            ids = _imported_ids(store)

        assert len(set(ids.values())) == 3


class TestAnIdentityOnceGivenIsKept:
    def test_FareAlreadyHeld_WhenAnIdenticalFareArrivesLater_KeepsItsIdentity(
        self, tmp_path
    ):
        """Actual already holds the first fare under its imported id. A twin
        arriving later must take a new number rather than renumber the first,
        or the next push duplicates one and orphans the other."""
        with Store(tmp_path / "s.sqlite3") as store:
            _feed(store, [_fare("fare-1")], cycle=0)
            rebuild_from_raw(store)
            before = _imported_ids(store)["fare-1"]

            _feed(store, [_fare("fare-2"), _fare("fare-1")], cycle=1)
            rebuild_from_raw(store)
            after = _imported_ids(store)

        assert after["fare-1"] == before
        assert after["fare-2"] != before

    def test_Identities_AfterASecondRebuild_AreUnchanged(self, tmp_path):
        with Store(tmp_path / "s.sqlite3") as store:
            _feed(store, [_fare("fare-1")], cycle=0)
            _feed(store, [_fare("fare-2"), _fare("fare-1")], cycle=1)
            _feed(store, [_fare("fare-3")], cycle=2)
            rebuild_from_raw(store)
            first = _imported_ids(store)
            rebuild_from_raw(store)
            second = _imported_ids(store)

        assert first == second

    def test_Identities_WhetherHistoryIsCachedOrReloadedPerResponse_AreTheSame(
        self, tmp_path, monkeypatch
    ):
        """A rebuild keeps each account's history in memory across responses;
        a live pull reloads it from the store for each one. The two must
        number identically, or a deploy's rebuild renames what the scheduler
        named."""
        import obdi.ingest.rebuild as rebuild_mod

        results = {}
        for label in ("cached", "reloading"):
            with Store(tmp_path / f"{label}.sqlite3") as store:
                _feed(store, [_fare("fare-1")], cycle=0)
                _feed(store, [_fare("fare-2"), _fare("fare-1")], cycle=1)
                _feed(store, [_fare("fare-3")], cycle=2)
                if label == "reloading":
                    original = rebuild_mod.reconcile_batch

                    def per_response(store_, transactions, _original=original, **kwargs):
                        kwargs.pop("candidate_cache", None)
                        return _original(store_, transactions, **kwargs)

                    monkeypatch.setattr(rebuild_mod, "reconcile_batch", per_response)
                rebuild_from_raw(store)
                results[label] = _imported_ids(store)
                monkeypatch.undo()

        assert results["cached"] == results["reloading"]
        assert len(set(results["cached"].values())) == 3


class TestAPaymentWhoseContentChanges:
    def test_PendingFare_SettlingOntoTheDayOfAnIdenticalFare_GetsItsOwnIdentity(
        self, tmp_path
    ):
        """A pending fare dated the 25th settles dated the 14th, where an
        identical fare is already held. Its content has become the other
        fare's content, so it needs a number the other fare is not using.

        The dates sit further apart than the fuzzy window on purpose: this
        is about a row whose content changes, and a pending record inside
        the window of a settled one is a different question."""
        with Store(tmp_path / "s.sqlite3") as store:
            _feed(store, [_fare("fare-1", day=14)], cycle=0)
            _feed(store, [_fare("fare-2", status="PENDING", day=25)], cycle=1)
            _feed(store, [_fare("fare-2", status="SETTLED", day=14)], cycle=2)
            rebuild_from_raw(store)
            ids = _imported_ids(store)

        assert len(ids) == 2
        assert ids["fare-1"] != ids["fare-2"]


class TestTheRefusalStillRefuses:
    def test_PushEnvelope_WhenTwoRowsShareAnIdentity_NamesTheAccountByItsOwnName(
        self, tmp_path
    ):
        """The reader knows their accounts by the names used here, not by the
        identifier Actual assigned."""
        with Store(tmp_path / "s.sqlite3") as store:
            _feed(store, [_fare("fare-1"), _fare("fare-2")], cycle=0)
            rebuild_from_raw(store)
            # Force the fault the allocator prevents, to show the refusal's wording.
            store.connection.execute("UPDATE transactions SET occurrence = 0")
            store.connection.commit()

            with pytest.raises(ValueError, match="duplicate imported id") as refusal:
                build_envelope(
                    store,
                    [ActualAccountBinding(canonical_id=ACCOUNT, actual_account_id="act-1")],
                    {ACCOUNT: "Everyday account"},
                )

        assert "Everyday account" in str(refusal.value)
        assert ACCOUNT in str(refusal.value)
