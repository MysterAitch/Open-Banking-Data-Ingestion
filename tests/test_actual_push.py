"""The push queue: envelopes out, bindings back, nothing coupled.

The Python side and the applier container share only a directory of JSON.
These tests cover the Python half: envelope building (provisioning named
from labels, source-qualified fallbacks excluded), the atomic queue write,
and the pending-bindings merge that closes the provisioning loop.
"""

from __future__ import annotations

import json

from obdi.actual_push import (
    build_envelope,
    latest_results,
    merge_pending_bindings,
    queue_push,
)
from obdi.replay import ActualAccountBinding
from obdi.store import Store


def _seed(store: Store, account_id: str, entity: str) -> None:
    store.connection.execute(
        "INSERT INTO transactions (entity_id, account_id, amount_minor, "
        "value_date, booking_date, description, source, currency, tier, "
        "status, content_key, occurrence, first_seen_at, last_seen_at) "
        "VALUES (?, ?, -100, '2026-07-01', '2026-07-01', 'X', 'truelayer', "
        "'GBP', 'authoritative', 'booked', ?, 0, '2026-07-01T00:00:00', "
        "'2026-07-01T00:00:00')",
        (entity, account_id, f"ck-{entity}"),
    )
    store.connection.commit()


class TestEnvelope:
    def test_BoundGoesToAccounts_NamedUnboundGoesToProvision(self, tmp_path):
        with Store(tmp_path / "s.sqlite3") as store:
            _seed(store, "halifax-current", "e-1")
            _seed(store, "halifax-reward", "e-2")
            _seed(store, "truelayer:opaque1", "e-3")

            envelope = build_envelope(
                store,
                [ActualAccountBinding("halifax-current", "act-1")],
                {"halifax-reward": "Reward (halifax)"},
            )

        assert envelope["version"] == 2
        assert list(envelope["accounts"].keys()) == ["act-1"]
        # The NAMED unbound account is provisioned with its label; the
        # source-qualified fallback is not - nobody has named it yet, and
        # minting an Actual account called "truelayer:opaque1" helps no one.
        assert envelope["provision"] == [
            {"canonical_id": "halifax-reward", "label": "Reward (halifax)"}
        ]

    def test_NamedButEmptyAccount_StillProvisions(self, tmp_path):
        """Bound in the map means wanted: a freshly opened account with no
        transactions yet must still appear in Actual, not wait offstage
        until money moves."""
        with Store(tmp_path / "s.sqlite3") as store:
            _seed(store, "halifax-current", "e-1")

            envelope = build_envelope(
                store,
                [ActualAccountBinding("halifax-current", "act-1")],
                {},
                named_canonicals={"halifax-current", "halifax-reward"},
            )

        # halifax-current is already bound to an Actual account, so only the
        # empty-but-named account needs creating.
        assert envelope["provision"] == [
            {"canonical_id": "halifax-reward", "label": "halifax-reward"}
        ]

    def test_QueueWrite_IsAtomicAndOrdered(self, tmp_path):
        first = queue_push({"version": 2}, tmp_path / "actual")
        second = queue_push({"version": 2}, tmp_path / "actual")

        names = sorted(p.name for p in (tmp_path / "actual" / "requests").iterdir())
        assert names == sorted([first.name, second.name])
        assert all(not name.startswith(".") for name in names)


class TestBindingsRoundTrip:
    def test_MintedBindings_MergeIntoTheMap_AndTheFileIsConsumed(self, tmp_path):
        actual_dir = tmp_path / "actual"
        actual_dir.mkdir()
        (actual_dir / "bindings-pending.json").write_text(
            json.dumps(
                [{"canonical_id": "halifax-reward", "actual_account_id": "act-9"}]
            ),
            encoding="utf-8",
        )
        map_path = tmp_path / "accounts.json"
        map_path.write_text(
            json.dumps(
                {
                    "bindings": [],
                    "actual": [
                        {"canonical_id": "halifax-current", "actual_account_id": "act-1"}
                    ],
                }
            ),
            encoding="utf-8",
        )

        merged = merge_pending_bindings(map_path, actual_dir)

        assert merged == 1
        stored = json.loads(map_path.read_text(encoding="utf-8"))
        assert {e["canonical_id"] for e in stored["actual"]} == {
            "halifax-current",
            "halifax-reward",
        }
        # Consumed, not deleted: renamed aside so a crash can only re-merge.
        assert not (actual_dir / "bindings-pending.json").exists()
        assert list(actual_dir.glob("bindings-pending.merged-*"))

    def test_LatestResults_NewestFirst(self, tmp_path):
        results = tmp_path / "actual" / "results"
        results.mkdir(parents=True)
        (results / "push-1.json").write_text('{"ok": true, "added": 1}')
        (results / "push-2.json").write_text('{"ok": false, "error": "x"}')

        latest = latest_results(tmp_path / "actual")

        assert latest[0] == {"ok": False, "error": "x"}
        assert latest[1] == {"ok": True, "added": 1}
