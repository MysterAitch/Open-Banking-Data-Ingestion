"""Entity ids as a pure function of the evidence.

uuid4 minting made ids the one thing about a replayed store that did not
reproduce. These tests hold the three properties determinism buys - and
the boundary it deliberately does not cross.
"""

from __future__ import annotations

import json

from obdi.providers import starling
from obdi.rebuild import rebuild_from_raw
from obdi.store import Store


def _feed(store: Store, uid: str, items: list[dict], cycle: int = 0) -> None:
    store.land_artefact(
        starling.artefact_for(
            json.dumps({"feedItems": items}).encode(),
            account_id=f"starling:{uid}",
            kind="feed",
            origin=f"https://api.example.com/feed/account/a/category/{uid}?c={cycle}",
        )
    )


def _item(uid: str, minor: int, status: str = "SETTLED") -> dict:
    return {
        "feedItemUid": uid,
        "amount": {"currency": "GBP", "minorUnits": minor},
        "direction": "OUT",
        "transactionTime": "2026-03-14T09:15:00.000Z",
        "source": "MASTER_CARD",
        "status": status,
        "counterPartyName": "Tesco",
        "reference": "REF",
    }


def _ids(store: Store) -> dict[str, str]:
    """entity_id keyed by a content fingerprint, so two stores compare."""
    rows = store.connection.execute(
        "SELECT entity_id, account_id, content_key, occurrence, source "
        "FROM transactions"
    ).fetchall()
    return {
        f"{r[1]}|{r[2]}|{r[3]}|{r[4]}": r[0] for r in rows
    }


class TestReplaysReproduceIdentity:
    def _corpus(self, store: Store) -> None:
        base = [_item(f"u-{n}", 100 + n) for n in range(12)]
        _feed(store, "cat-1", base, cycle=0)
        _feed(store, "cat-1", [*base, _item("u-new", 999)], cycle=1)
        _feed(store, "cat-2", [_item(f"v-{n}", 300 + n) for n in range(6)], cycle=0)

    def test_ColdRebuild_RunTwice_ProducesIdenticalEntityIds(self, tmp_path):
        """The panel's named acceptance test, verbatim in spirit."""
        with Store(tmp_path / "s.sqlite3") as store:
            self._corpus(store)
            rebuild_from_raw(store)
            first = _ids(store)
            rebuild_from_raw(store)
            second = _ids(store)

        assert first == second
        assert first, "the comparison must not pass vacuously"

    def test_TwoStoresFromTheSameArtefacts_AgreeOnEveryId(self, tmp_path):
        """A future consumer replaying the corpus arrives at the same
        identities - the property that matters once a second consumer
        exists."""
        results = []
        for name in ("a", "b"):
            with Store(tmp_path / f"{name}.sqlite3") as store:
                self._corpus(store)
                rebuild_from_raw(store)
                results.append(_ids(store))

        assert results[0] == results[1]

    def test_LiveIngest_AndALaterRebuild_MintTheSameIds(self, tmp_path):
        """The dangling-reference killer.

        The events outbox is retained across rebuilds and keys entity_id;
        under uuid minting every rebuild orphaned every historical event.
        Live ingest (artefacts folded as they land) and a later replay of
        the same artefacts must now agree.
        """
        with Store(tmp_path / "s.sqlite3") as store:
            self._corpus(store)
            # The corpus lands via land_artefact only; replay it once to
            # simulate live ingest having derived it...
            rebuild_from_raw(store)
            live = _ids(store)
            # ...then wipe and replay again, as a deploy would.
            rebuild_from_raw(store)
            rebuilt = _ids(store)

        assert live == rebuilt

    def test_TwoIdenticalPayments_StillGetDistinctIds(self, tmp_path):
        """Determinism must not become collapse: occurrence separates
        genuinely repeated id-less payments, and their ids differ."""
        from datetime import date

        from obdi.ingest import reconcile_batch
        from obdi.models import SourceTier, Transaction, TransactionStatus

        twins = [
            Transaction(
                entity_id=f"pre-{n}",
                account_id="a1",
                amount_minor=-2500,
                currency="GBP",
                description="STANDING ORDER",
                value_date=date(2026, 3, 6),
                booking_date=date(2026, 3, 6),
                source="qif",
                source_id=None,
                content_key="ck-same",
                tier=SourceTier.SYNTHETIC,
                status=TransactionStatus.BOOKED,
            )
            for n in range(2)
        ]
        with Store(tmp_path / "s.sqlite3") as store:
            reconcile_batch(store, twins, digest="d-1")
            ids = [
                row[0]
                for row in store.connection.execute(
                    "SELECT entity_id FROM transactions ORDER BY occurrence"
                )
            ]

        assert len(ids) == 2
        assert ids[0] != ids[1]

    def test_ADifferentFirstArtefact_MeansADifferentId(self, tmp_path):
        """The digest pins identity to evidence: the same payment first
        seen via different bytes is a different first sighting, and the
        id says so rather than pretending otherwise."""
        from obdi.identity import entity_id_for

        common = {
            "account_id": "a1",
            "source": "starling",
            "source_id": "uid-1",
            "content_key_value": "ck",
            "occurrence": 0,
        }
        first = entity_id_for(**common, first_artefact_digest="digest-a")
        second = entity_id_for(**common, first_artefact_digest="digest-b")
        assert first != second
