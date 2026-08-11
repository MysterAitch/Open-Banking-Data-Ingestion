"""The push queue: envelopes out, bindings back, nothing coupled.

The Python side and the applier container share only a directory of JSON.
These tests cover the Python half: envelope building (provisioning named
from labels, source-qualified fallbacks excluded), the atomic queue write,
and the pending-bindings merge that closes the provisioning loop.
"""

from __future__ import annotations

import json

from obdi.actual_push import (
    applier_heartbeat,
    build_audit_envelope,
    build_envelope,
    drop_conflicting_bindings,
    forget_actual_bindings,
    latest_results,
    merge_pending_bindings,
    processing_request,
    queue_push,
    queued_requests,
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

    def test_ProvisionLabelsCollide_FallBackToCanonicalNames(self, tmp_path):
        """Two Halifax accounts both display as the account holder's name.

        The applier provisions idempotently BY NAME, so duplicate labels
        silently bind two canonicals to one Actual account - the first
        live push did exactly that. Colliding labels must fall back to
        the canonical names, which are unique by construction."""
        with Store(tmp_path / "s.sqlite3") as store:
            _seed(store, "halifax-current", "e-1")
            _seed(store, "halifax-saver", "e-2")

            envelope = build_envelope(
                store,
                [],
                {
                    "halifax-current": "Mr Roger Howell (halifax)",
                    "halifax-saver": "Mr Roger Howell (halifax)",
                },
            )

        assert envelope["provision"] == [
            {"canonical_id": "halifax-current", "label": "halifax-current"},
            {"canonical_id": "halifax-saver", "label": "halifax-saver"},
        ]

    def test_AuditEnvelope_CoversEveryBoundAccount_EmptyOnesIncluded(self, tmp_path):
        """The audit asks "what should this account hold" for every bound
        account - an empty one can still hold orphans on the Actual side,
        which is precisely what the audit exists to see."""
        with Store(tmp_path / "s.sqlite3") as store:
            _seed(store, "halifax-current", "e-1")

            envelope = build_audit_envelope(
                store,
                [
                    ActualAccountBinding("halifax-current", "act-1"),
                    ActualAccountBinding("halifax-reward", "act-2"),
                ],
            )

        assert envelope["kind"] == "audit"
        accounts = envelope["accounts"]
        assert len(accounts["act-1"]) == 1
        assert accounts["act-2"] == []

    def test_AuditQueue_UsesItsOwnPrefix_AndListsWithItsKind(self, tmp_path):
        queued = queue_push(
            {"version": 2, "kind": "audit"}, tmp_path / "actual", prefix="audit"
        )
        queue_push({"version": 2}, tmp_path / "actual")

        assert queued.name.startswith("audit-")
        listed = queued_requests(tmp_path / "actual")
        assert {entry["kind"] for entry in listed} == {"audit", "push"}
        assert all(entry["queued_at"] for entry in listed)

    def test_AuditSummary_AccountsForEveryFate(self, tmp_path, monkeypatch):
        """"Auditing 7" alone is a mystery number: the summary states what
        was skipped and why - named accounts awaiting provisioning, and
        unnamed ones that need a name before anything can happen."""
        import json as _json

        from obdi.cli import queue_actual_audit

        map_path = tmp_path / "accounts.json"
        map_path.write_text(
            _json.dumps(
                {
                    "bindings": [
                        {
                            "source": "truelayer",
                            "provider_account_id": "uid-b",
                            "canonical_id": "halifax-current",
                        },
                        {
                            "source": "starling",
                            "provider_account_id": "uid-n",
                            "canonical_id": "starling-personal",
                        },
                    ],
                    "actual": [
                        {"canonical_id": "halifax-current", "actual_account_id": "X"}
                    ],
                }
            ),
            encoding="utf-8",
        )
        monkeypatch.setenv("ACTUAL_SYNC_ID", "sync-1")
        monkeypatch.setenv("OBDI_ACCOUNT_MAP", str(map_path))
        monkeypatch.setenv("OBDI_ACTUAL_DIR", str(tmp_path / "actual"))
        db = tmp_path / "s.sqlite3"
        with Store(db) as store:
            _seed(store, "halifax-current", "e-1")
            _seed(store, "starling:uid-u", "e-2")

        summary = queue_actual_audit(db)

        assert "auditing 1 Actual-bound account(s)" in summary
        assert "1 named awaiting provisioning" in summary
        assert "1 unnamed (bind first)" in summary

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

        merged = merge_pending_bindings(map_path, actual_dir).merged

        assert merged == 1
        stored = json.loads(map_path.read_text(encoding="utf-8"))
        assert {e["canonical_id"] for e in stored["actual"]} == {
            "halifax-current",
            "halifax-reward",
        }
        # Consumed, not deleted: renamed aside so a crash can only re-merge.
        assert not (actual_dir / "bindings-pending.json").exists()
        assert list(actual_dir.glob("bindings-pending.merged-*"))

    def test_ConflictingBindings_AreDroppedForReprovisioning(self, tmp_path):
        """Two canonicals sharing one Actual account id is the poisoned
        state a label collision leaves behind. Both sharers are dropped
        (which is ambiguous to resolve, and re-provisioning is cheap) so
        the next push creates them cleanly; unique bindings survive."""
        map_path = tmp_path / "accounts.json"
        map_path.write_text(
            json.dumps(
                {
                    "bindings": [],
                    "actual": [
                        {"canonical_id": "halifax-current", "actual_account_id": "X"},
                        {"canonical_id": "halifax-saver", "actual_account_id": "X"},
                        {"canonical_id": "halifax-reward", "actual_account_id": "Y"},
                    ],
                }
            ),
            encoding="utf-8",
        )

        dropped = drop_conflicting_bindings(map_path)

        assert dropped == ["halifax-current", "halifax-saver"]
        stored = json.loads(map_path.read_text(encoding="utf-8"))
        assert stored["actual"] == [
            {"canonical_id": "halifax-reward", "actual_account_id": "Y"}
        ]

    def test_NoConflicts_LeavesTheMapUntouched(self, tmp_path):
        map_path = tmp_path / "accounts.json"
        map_path.write_text(
            json.dumps(
                {
                    "bindings": [],
                    "actual": [
                        {"canonical_id": "halifax-current", "actual_account_id": "X"}
                    ],
                }
            ),
            encoding="utf-8",
        )

        assert drop_conflicting_bindings(map_path) == []

    def test_QueuedRequests_ListedWithTheirTimes_TmpFilesIgnored(self, tmp_path):
        """A pressed button must be visible as in-flight, not vanish until
        the applier answers. New filenames carry a Z (they are UTC and say
        so); files queued before the Z existed parse identically."""
        requests = tmp_path / "actual" / "requests"
        requests.mkdir(parents=True)
        (requests / "push-20260802T112545430836.json").write_text("{}")
        (requests / "audit-20260802T135454703196Z.json").write_text("{}")
        (requests / ".push-x.json.tmp").write_text("{}")

        queued = queued_requests(tmp_path / "actual")

        assert len(queued) == 2
        by_name = {str(entry["name"]): entry for entry in queued}
        assert (
            by_name["push-20260802T112545430836.json"]["queued_at"]
            == "2026-08-02T11:25:45"
        )
        assert (
            by_name["audit-20260802T135454703196Z.json"]["queued_at"]
            == "2026-08-02T13:54:54"
        )

    def test_QueuedName_CarriesTheZ(self, tmp_path):
        queued = queue_push({"version": 2}, tmp_path / "actual")

        assert queued.stem.endswith("Z")

    def test_ForgetActualBindings_ClearsLinksButKeepsSourceBindings(self, tmp_path):
        """The recovery step after deleting accounts in Actual: the stale
        links go, the source bindings (which name accounts) stay, and the
        next push re-provisions by name."""
        map_path = tmp_path / "accounts.json"
        map_path.write_text(
            json.dumps(
                {
                    "bindings": [
                        {
                            "source": "starling",
                            "provider_account_id": "uid-1",
                            "canonical_id": "starling-personal",
                        }
                    ],
                    "actual": [
                        {"canonical_id": "halifax-current", "actual_account_id": "X"},
                        {"canonical_id": "halifax-saver", "actual_account_id": "Y"},
                    ],
                }
            ),
            encoding="utf-8",
        )

        forgotten = forget_actual_bindings(map_path)

        assert forgotten == 2
        stored = json.loads(map_path.read_text(encoding="utf-8"))
        assert stored["actual"] == []
        assert stored["bindings"][0]["canonical_id"] == "starling-personal"

    def test_ForgetActualBindings_NothingToForget_IsANoOp(self, tmp_path):
        map_path = tmp_path / "accounts.json"
        map_path.write_text(json.dumps({"bindings": [], "actual": []}), encoding="utf-8")

        assert forget_actual_bindings(map_path) == 0

    def test_ApplierHeartbeat_ReadsTheStamp_EmptyWhenNeverSeen(self, tmp_path):
        actual_dir = tmp_path / "actual"
        assert applier_heartbeat(actual_dir) == ""

        actual_dir.mkdir()
        (actual_dir / "heartbeat.json").write_text(
            json.dumps({"at": "2026-08-02T13:38:00.000Z"}), encoding="utf-8"
        )
        assert applier_heartbeat(actual_dir) == "2026-08-02T13:38:00.000Z"

    def test_ProcessingMarker_ReadsBack_EmptyWhenAbsent(self, tmp_path):
        import json as _json

        actual_dir = tmp_path / "actual"
        assert processing_request(actual_dir) == {}

        actual_dir.mkdir()
        (actual_dir / "processing.json").write_text(
            _json.dumps(
                {
                    "name": "audit-20260802T1331.json",
                    "started_at": "2026-08-02T13:31:56Z",
                }
            ),
            encoding="utf-8",
        )
        marker = processing_request(actual_dir)
        assert marker["name"] == "audit-20260802T1331.json"

    def test_LatestResults_NewestFirst(self, tmp_path):
        results = tmp_path / "actual" / "results"
        results.mkdir(parents=True)
        (results / "push-1.json").write_text(
            '{"ok": true, "added": 1, "finished_at": "2026-08-02T10:00:00Z"}'
        )
        (results / "push-2.json").write_text(
            '{"ok": false, "error": "x", "finished_at": "2026-08-02T11:00:00Z"}'
        )

        latest = latest_results(tmp_path / "actual")

        assert latest[0]["ok"] is False
        assert latest[1]["ok"] is True

    def test_AuditResult_RanksByTime_NotByFilenamePrefix(self, tmp_path):
        """Found live: results sorted by filename, and push- outranks
        audit- alphabetically - so the first real audit report sat on disk
        invisible under five push results. Time decides, never the name."""
        results = tmp_path / "actual" / "results"
        results.mkdir(parents=True)
        for hour in (9, 10, 11, 12, 13):
            (results / f"push-20260802T{hour:02}.json").write_text(
                f'{{"ok": true, "finished_at": "2026-08-02T{hour:02}:00:00Z"}}'
            )
        (results / "audit-20260802T1230.json").write_text(
            '{"ok": true, "kind": "audit", "finished_at": "2026-08-02T12:30:00Z"}'
        )

        latest = latest_results(tmp_path / "actual")

        assert latest[0]["finished_at"] == "2026-08-02T13:00:00Z"
        assert latest[1]["kind"] == "audit"


class TestDuplicateIdentityGuard:
    def test_PushEnvelope_RefusesDuplicateImportedIds(self, tmp_path):
        """Two store rows sharing one identity would reach Actual as one
        row - the push refuses loudly instead of letting a real payment
        silently vanish."""
        import pytest

        from obdi.actual_push import ActualAccountBinding, build_envelope
        from obdi.store import Store

        with Store(tmp_path / "s.sqlite3") as store:
            for entity, source_id in (("e-1", "sid-1"), ("e-2", "sid-2")):
                store.connection.execute(
                    "INSERT INTO transactions (entity_id, account_id, "
                    "amount_minor, value_date, booking_date, description, "
                    "source, currency, tier, status, content_key, occurrence, "
                    "source_id, first_seen_at, last_seen_at, raw) "
                    "VALUES (?, 'halifax-current', -1200, '2026-07-01', "
                    "'2026-07-01', 'COFFEE', 'truelayer', 'GBP', "
                    "'authoritative', 'booked', 'ck-same', 0, ?, "
                    "'2026-07-01T00:00:00', '2026-07-01T00:00:00', '{}')",
                    (entity, source_id),
                )
            store.connection.commit()
            bindings = [
                ActualAccountBinding(
                    canonical_id="halifax-current", actual_account_id="act-1"
                )
            ]
            with pytest.raises(ValueError, match="duplicate imported id"):
                build_envelope(store, bindings, {})


class TestMergeClaimsBeforeReading:
    def test_BindingWrittenDuringMerge_LandsInAFreshFile_NeverArchivedUnread(
        self, tmp_path
    ):
        """The pending file is claimed before it is read, so a binding the
        applier writes mid-merge creates a new pending file that the NEXT
        merge folds in - nothing is archived unread."""
        import json as _json

        from obdi.actual_push import merge_pending_bindings

        actual_dir = tmp_path / "actual"
        actual_dir.mkdir()
        map_path = tmp_path / "map.json"
        (actual_dir / "bindings-pending.json").write_text(
            _json.dumps(
                [{"canonical_id": "a", "actual_account_id": "act-1"}]
            ),
            encoding="utf-8",
        )

        assert merge_pending_bindings(map_path, actual_dir).merged == 1
        # The applier writes a NEW binding after the first merge consumed
        # its claim - exactly the mid-merge write, one tick later.
        (actual_dir / "bindings-pending.json").write_text(
            _json.dumps(
                [{"canonical_id": "b", "actual_account_id": "act-2"}]
            ),
            encoding="utf-8",
        )
        assert merge_pending_bindings(map_path, actual_dir).merged == 1

        merged = _json.loads(map_path.read_text(encoding="utf-8"))
        canonicals = sorted(e["canonical_id"] for e in merged["actual"])
        assert canonicals == ["a", "b"]

    def test_CrashedClaim_IsSweptAndMergedByTheNextCall(self, tmp_path):
        import json as _json

        from obdi.actual_push import merge_pending_bindings

        actual_dir = tmp_path / "actual"
        actual_dir.mkdir()
        map_path = tmp_path / "map.json"
        # A claim a crashed merge left behind: claimed, never merged.
        (actual_dir / "bindings-pending.merging-20260803T000000000000").write_text(
            _json.dumps(
                [{"canonical_id": "c", "actual_account_id": "act-3"}]
            ),
            encoding="utf-8",
        )

        assert merge_pending_bindings(map_path, actual_dir).merged == 1
        merged = _json.loads(map_path.read_text(encoding="utf-8"))
        assert merged["actual"][0]["canonical_id"] == "c"
