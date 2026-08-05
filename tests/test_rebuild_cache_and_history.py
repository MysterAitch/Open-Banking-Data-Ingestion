"""The shared candidate cache and the rebuild history.

The cache exists because production timings showed each account's full
history being reloaded once per artefact batch - a fifth of the rebuild,
growing with corpus times account size. It is sound only if a cached
rebuild is INDISTINGUISHABLE from a reloading one, including across the
one seam where the loop mutates rows outside the fold: vanished-pending
resolution. That seam gets its own test, because a voided row cached as
pending is the kind of wrongness that only shows up as money.
"""

from __future__ import annotations

import json
import time

from obdi.providers import starling, truelayer
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


def _item(uid: str, minor: int, when: str = "2026-03-14T09:15:00.000Z") -> dict:
    return {
        "feedItemUid": uid,
        "amount": {"currency": "GBP", "minorUnits": minor},
        "direction": "OUT",
        "transactionTime": when,
        "source": "MASTER_CARD",
        "status": "SETTLED",
        "counterPartyName": "Tesco",
        "reference": "REF",
    }


def _pending(store: Store, records: list[dict], cycle: int) -> None:
    store.land_artefact(
        truelayer.artefact_for(
            json.dumps({"results": records, "status": "Succeeded"}).encode(),
            account_id="tl-1",
            kind="pending",
            requested=f"pending&c={cycle}",
        )
    )


def _pending_record(pid: str, amount: float) -> dict:
    return {
        "transaction_id": pid,
        "normalised_provider_transaction_id": pid,
        "timestamp": "2026-07-01T00:00:00Z",
        "amount": amount,
        "currency": "GBP",
        "description": "PENDING CARD",
    }


def _dump(store: Store) -> list[tuple]:
    rows = store.connection.execute(
        "SELECT account_id, amount_minor, value_date, description, status, "
        "source, content_key, occurrence, is_internal_transfer "
        "FROM transactions ORDER BY account_id, content_key, occurrence, status"
    ).fetchall()
    return [tuple(row) for row in rows]


class TestTheCachedRebuildIsIndistinguishableFromReloading:
    def _build_corpus(self, store: Store) -> None:
        """Several artefacts per account, so the cache is actually reused,
        plus a pending set that VANISHES - exercising the invalidation."""
        base = [_item(f"u-{n}", 100 + n) for n in range(30)]
        _feed(store, "cat-1", base, cycle=0)
        _feed(store, "cat-1", [*base, _item("u-new", 999)], cycle=1)
        _feed(store, "cat-2", [_item(f"v-{n}", 300 + n) for n in range(10)], cycle=0)
        # Pending snapshot, then the next snapshot WITHOUT one of them:
        # the vanished pending must resolve to VOID, cached or not.
        _pending(store, [_pending_record("p-1", -12.34), _pending_record("p-2", -56.78)], 0)
        _pending(store, [_pending_record("p-2", -56.78)], 1)
        # A later batch touching the same account AFTER the void: this is
        # the read that a stale cache would poison.
        _feed(store, "cat-1", [*base, _item("u-new", 999), _item("u-late", 555)], cycle=2)

    def test_FullRebuild_WithAndWithoutTheCache_ProduceIdenticalStores(
        self, tmp_path, monkeypatch
    ):
        results = {}
        for label in ("cached", "reloading"):
            with Store(tmp_path / f"{label}.sqlite3") as store:
                self._build_corpus(store)
                if label == "reloading":
                    # Force the pre-cache behaviour: every batch reloads.
                    import obdi.rebuild as rebuild_mod

                    original = rebuild_mod.reconcile_batch

                    def per_batch(store_, transactions, _original=original, **kwargs):
                        kwargs.pop("candidate_cache", None)
                        return _original(store_, transactions, **kwargs)

                    monkeypatch.setattr(
                        rebuild_mod, "reconcile_batch", per_batch
                    )
                rebuild_from_raw(store)
                results[label] = _dump(store)
                monkeypatch.undo()

        assert results["cached"] == results["reloading"]

    def test_AVanishedPending_IsVoided_EvenWithTheCacheWarm(self, tmp_path):
        with Store(tmp_path / "s.sqlite3") as store:
            self._build_corpus(store)
            rebuild_from_raw(store)
            statuses = {
                row[0]: row[1]
                for row in store.connection.execute(
                    "SELECT source_id, status FROM transactions "
                    "WHERE source = 'truelayer'"
                )
            }

        assert statuses.get("p-1") == "void", statuses
        assert statuses.get("p-2") == "pending", statuses


class TestEveryRebuildLeavesARow:
    def test_ABackgroundRebuild_RecordsItsRunWithTimingsAndBuild(
        self, tmp_path, monkeypatch
    ):
        from obdi import instrumentation
        from obdi.cli import rebuild_status_for, start_background_rebuild

        monkeypatch.setenv("OBDI_DB_PATH", str(tmp_path / "s.sqlite3"))
        instrumentation.configure(True)
        db = tmp_path / "s.sqlite3"
        with Store(db) as store:
            _feed(store, "cat-1", [_item("u-1", 100)])

        start_background_rebuild(db)
        deadline = time.time() + 30
        while time.time() < deadline:
            if rebuild_status_for(db).get("state") == "done":
                break
            time.sleep(0.1)

        with Store(db) as store:
            runs = store.recent_rebuild_runs()
        instrumentation.configure(None)

        assert len(runs) == 1
        run = runs[0]
        assert run["ok"] == 1
        assert run["transactions"] == 1
        assert isinstance(run["timings"], dict) and run["timings"], (
            "timings were enabled, so the row must carry them"
        )
        assert run["build"], "the build that ran it must be recorded"

    def test_TheHistoryRenders_FromRowsNotLogs(self, tmp_path):
        from obdi.web import _rebuild_history_html

        runs = [
            {
                "ok": 1,
                "started_at": "2026-08-05T06:25:06Z",
                "finished_at": "2026-08-05T06:25:15Z",
                "records_total": 44429,
                "transactions": 42403,
                "timings": {"reconcile": {"seconds": 5.69, "calls": 47}},
                "build": "0.4.89+abc1234",
            },
            {
                "ok": 0,
                "started_at": "2026-08-04T20:53:07Z",
                "finished_at": "2026-08-04T21:47:57Z",
                "summary": "database is locked",
                "timings": {},
                "build": "0.4.78+b7c4dbb",
            },
        ]
        html_out = _rebuild_history_html(lambda: runs)

        assert "44,429 records" in html_out
        assert "9s" in html_out
        assert "reconcile 5.69s" in html_out
        assert "failed" in html_out, "failed runs must be visible, not filtered"
        assert "0.4.89+abc1234" in html_out

    def test_NoRuns_RendersNothingRatherThanAnEmptyTable(self):
        from obdi.web import _rebuild_history_html

        assert _rebuild_history_html(lambda: []) == ""
        assert _rebuild_history_html(None) == ""
