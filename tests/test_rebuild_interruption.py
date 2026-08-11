"""A half-built derived layer must never call itself current.

Found by review, and verified there by execution before being believed: a
rebuild commits its wipe and then replays batch by batch, so an
interruption partway - a write failure, a container killed mid-replay -
leaves an arbitrary PREFIX of the corpus in the derived tables. The code
fingerprint was already stamped from the previous rebuild and still
matches the running code, so the store answers "current" while holding
half its rows.

That is the worst shape a fault can take here. Nothing errors, nothing is
missing from any list, and every balance and total is plausible and short.

The fix is an inversion, not a new mechanism: withdraw the claim of
currency BEFORE the wipe and re-stamp only on success, so anything that
kills the process in between leaves a store that admits it needs
rebuilding.
"""

from __future__ import annotations

from datetime import datetime

import pytest

from obdi.fingerprint import code_fingerprint, rebuild_needed, stamp_fingerprint
from obdi.identity import artefact_digest
from obdi.models import RawArtefact
from obdi.rebuild import rebuild_from_raw
from obdi.store import Store


def _feed(uid: str, minor: int, day: int) -> bytes:
    import json

    return json.dumps(
        {
            "feedItems": [
                {
                    "feedItemUid": uid,
                    "amount": {"currency": "GBP", "minorUnits": minor},
                    "direction": "OUT",
                    "transactionTime": f"2026-01-{day:02d}T10:00:00.000Z",
                    "source": "MASTER_CARD",
                    "status": "SETTLED",
                    "counterPartyName": "Shop",
                    "reference": f"SHOP {uid}",
                }
            ]
        }
    ).encode()


def _land(store: Store, count: int) -> None:
    for index in range(count):
        payload = _feed(f"u-{index}", 100 + index, index + 1)
        store.land_artefact(
            RawArtefact(
                source="starling-feed",
                account_ref="starling:acc-1",
                fetched_at=datetime.now().astimezone(),
                media_type="application/json",
                digest=artefact_digest(payload),
                payload=payload,
                origin=f"feed-{index}",
            )
        )


class TestAnInterruptedRebuild:
    def test_ItLeavesAStore_ThatKnowsItNeedsRebuilding(self, tmp_path, monkeypatch):
        with Store(tmp_path / "s.sqlite3") as store:
            _land(store, 4)
            rebuild_from_raw(store)
            stamp_fingerprint(store, code_fingerprint())
            assert not rebuild_needed(store), "a good rebuild reports current"

            # Kill the replay partway, as a write failure or a container
            # stop would.
            import obdi.rebuild as rebuild_module

            calls = {"n": 0}
            original = rebuild_module.reconcile_batch

            def failing(*args, **kwargs):
                calls["n"] += 1
                if calls["n"] > 2:
                    raise RuntimeError("killed mid-replay")
                return original(*args, **kwargs)

            monkeypatch.setattr(rebuild_module, "reconcile_batch", failing)

            with pytest.raises(RuntimeError):
                rebuild_from_raw(store)

            held = len(store.all_transactions())
            assert held < 4, "the derived layer is genuinely partial"
            assert rebuild_needed(store), (
                "a store holding part of its corpus must not certify itself "
                "current - every total computed from it would be plausible "
                "and short"
            )

    def test_ASuccessfulRebuild_StillReportsCurrent(self, tmp_path):
        # The inversion must not leave every store permanently unstamped.
        with Store(tmp_path / "s.sqlite3") as store:
            _land(store, 3)
            rebuild_from_raw(store)
            stamp_fingerprint(store, code_fingerprint())

            assert not rebuild_needed(store)
            assert len(store.all_transactions()) == 3

    def test_ARebuiltStore_IsNotTrustedUntilItIsStamped(self, tmp_path):
        # Between the wipe and the stamp there is no claim of currency,
        # which is the whole point.
        with Store(tmp_path / "s.sqlite3") as store:
            _land(store, 2)
            rebuild_from_raw(store)

            assert rebuild_needed(store), (
                "the rebuild itself does not stamp - the caller does, after "
                "it returns, so an exception on the way out cannot leave a "
                "claim behind"
            )
