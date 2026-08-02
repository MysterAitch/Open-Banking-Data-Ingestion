"""The founding promise made executable: derived layers regenerate from raw.

The decisive property is idempotence against the live pipeline: a store
built by pulls and imports, wiped and rebuilt, must resolve to the same
transactions - same entities, same counts - because both routes run the
same rules over the same bytes in the same order.
"""

from __future__ import annotations

import json

from obdi.accounts import AccountMap
from obdi.connections import Connection, ConnectionStore
from obdi.pull import pull_truelayer
from obdi.rebuild import rebuild_from_raw
from obdi.store import Store


def _connection():
    return Connection(
        connection_id="halifax",
        provider="halifax",
        access_token="a",
        refresh_token="r",
        access_expires_at="2099-01-01T00:00:00+00:00",
        consent_expires_at="2099-01-01T00:00:00+00:00",
        scopes="",
    )


def _fake_provider(monkeypatch, records):
    def fake_accounts(_token, **_kwargs):
        return (
            [{"account_id": "acc-1", "display_name": "Current", "account_type": "T"}],
            b'{"results": []}',
        )

    def fake_transactions(_token, _account_id, **kwargs):
        if kwargs.get("pending"):
            return [], b'{"results": [], "status": "Succeeded"}', "pending"
        body = json.dumps({"results": records, "status": "Succeeded"}).encode()
        return records, body, "from=2026-05-04&to=2026-08-02"

    monkeypatch.setattr("obdi.pull.truelayer.fetch_accounts", fake_accounts)
    monkeypatch.setattr("obdi.pull.truelayer.fetch_transactions", fake_transactions)
    monkeypatch.setattr("obdi.pull.truelayer.fetch_balance", lambda *a, **k: ([], b"{}"))


class TestRebuildFromRaw:
    def test_Rebuild_ReproducesThePipelineExactly(self, tmp_path, monkeypatch):
        records = [
            {
                "transaction_id": "t-1",
                "normalised_provider_transaction_id": "txn-aaa",
                "timestamp": "2026-07-01T00:00:00Z",
                "amount": -12.34,
                "currency": "GBP",
                "description": "COFFEE SHOP",
            },
            {
                "transaction_id": "t-2",
                "normalised_provider_transaction_id": "txn-bbb",
                "timestamp": "2026-07-02T00:00:00Z",
                "amount": 2500.00,
                "currency": "GBP",
                "description": "SALARY",
            },
        ]
        _fake_provider(monkeypatch, records)

        with Store(tmp_path / "s.sqlite3") as store:
            pull_truelayer(
                store,
                _connection(),
                client_id="i",
                client_secret="s",
                connection_store=ConnectionStore(tmp_path / "c.json"),
                account_map=AccountMap(),
            )
            query = (
                "SELECT account_id, amount_minor, value_date, description "
                "FROM transactions ORDER BY value_date, amount_minor"
            )
            before = store.connection.execute(query).fetchall()
            assert len(before) == 2

            report = rebuild_from_raw(store)

            after = store.connection.execute(query).fetchall()

        # Every observable fact about every payment reproduces exactly.
        # Entity ids are deliberately NOT compared: they are minted at first
        # sighting and a rebuild re-mints them - which is why downstream
        # consumers must key on content, never on stored entity ids.
        assert [tuple(r) for r in after] == [tuple(r) for r in before]
        assert report.transactions == 2
        assert report.problems == []

    def test_Rebuild_KeepsEvidenceAndLearntFacts(self, tmp_path, monkeypatch):
        _fake_provider(monkeypatch, [])

        with Store(tmp_path / "s.sqlite3") as store:
            pull_truelayer(
                store,
                _connection(),
                client_id="i",
                client_secret="s",
                connection_store=ConnectionStore(tmp_path / "c.json"),
                account_map=AccountMap(),
            )
            store.record_provider_fact("truelayer", "halifax", "sca_window_minutes", "5")
            artefacts_before = store.counts()["raw_artefacts"]
            attempts_before = len(store.attempts())

            rebuild_from_raw(store)

            assert store.counts()["raw_artefacts"] == artefacts_before
            assert len(store.attempts()) == attempts_before
            assert (
                store.provider_fact("truelayer", "halifax", "sca_window_minutes") == "5"
            )

    def test_Rebuild_IsIdempotent(self, tmp_path, monkeypatch):
        _fake_provider(
            monkeypatch,
            [
                {
                    "transaction_id": "t-1",
                    "normalised_provider_transaction_id": "txn-ccc",
                    "timestamp": "2026-07-01T00:00:00Z",
                    "amount": -5.00,
                    "currency": "GBP",
                    "description": "BUS",
                }
            ],
        )

        with Store(tmp_path / "s.sqlite3") as store:
            pull_truelayer(
                store,
                _connection(),
                client_id="i",
                client_secret="s",
                connection_store=ConnectionStore(tmp_path / "c.json"),
                account_map=AccountMap(),
            )
            first = rebuild_from_raw(store)
            second = rebuild_from_raw(store)
            count = store.counts()["transactions"]

        assert first.transactions == second.transactions == 1
        assert count == 1


class TestStarlingReplay:
    """The gap that let a live rebuild silently drop every Starling row:
    no test replayed a starling-feed artefact, and provider errors
    (RuntimeError subclasses) aborted the loop instead of skipping the
    one bad artefact."""

    def _feed_artefact(self, account_ref, items):
        import json as _json

        from obdi.providers.starling import artefact_for

        body = _json.dumps({"feedItems": items}).encode("utf-8")
        return artefact_for(
            body,
            account_id=account_ref,
            kind="feed",
            origin="https://api.example.com/feed?changesSince=x",
        )

    def _item(self, uid, minor_units, currency="GBP"):
        return {
            "feedItemUid": uid,
            "amount": {"currency": currency, "minorUnits": minor_units},
            "direction": "OUT",
            "transactionTime": "2026-03-14T09:15:00.000Z",
            "source": "MASTER_CARD",
            "status": "SETTLED",
            "counterPartyName": "Tesco",
            "reference": "TESCO STORES",
        }

    def test_StarlingFeedArtefacts_ReplayIntoTransactions(self, tmp_path):
        from obdi.rebuild import rebuild_from_raw
        from obdi.store import Store

        with Store(tmp_path / "s.sqlite3") as store:
            store.land_artefact(
                self._feed_artefact(
                    "starling:uid-1",
                    [self._item("f-1", 1499), self._item("f-2", 250)],
                )
            )

            report = rebuild_from_raw(store)

            assert report.transactions == 2
            assert report.problems == []
            rows = store.connection.execute(
                "SELECT account_id, amount_minor FROM transactions ORDER BY amount_minor"
            ).fetchall()
        assert [tuple(r) for r in rows] == [
            ("starling:uid-1", -1499),
            ("starling:uid-1", -250),
        ]

    def test_PoisonArtefact_IsRecordedAndSkipped_TheRestReplays(self, tmp_path):
        """One non-GBP item once aborted the whole rebuild mid-loop -
        after the wipe. It must cost exactly its own artefact, loudly."""
        from obdi.rebuild import rebuild_from_raw
        from obdi.store import Store

        with Store(tmp_path / "s.sqlite3") as store:
            store.land_artefact(
                self._feed_artefact(
                    "starling:uid-1", [self._item("f-bad", 900, currency="EUR")]
                )
            )
            store.land_artefact(
                self._feed_artefact("starling:uid-2", [self._item("f-good", 1499)])
            )

            report = rebuild_from_raw(store)

            assert report.transactions == 1
            assert len(report.problems) == 1
            assert "EUR" in report.problems[0]
            count = store.connection.execute(
                "SELECT COUNT(*) FROM transactions"
            ).fetchone()[0]
        assert count == 1


class TestRebuildProgress:
    def test_Progress_IsMonotonic_AndEndsComplete(self, tmp_path):
        """A rebuild takes minutes; "running" with no number reads as hung
        to anyone watching the page."""
        import json as _json

        from obdi.providers.starling import artefact_for
        from obdi.rebuild import rebuild_from_raw
        from obdi.store import Store

        calls = []
        with Store(tmp_path / "s.sqlite3") as store:
            for n in range(3):
                body = _json.dumps({"feedItems": []}).encode("utf-8")
                store.land_artefact(
                    artefact_for(
                        body + str(n).encode(),
                        account_id=f"starling:uid-{n}",
                        kind="feed",
                        origin=f"https://api.example.com/feed?n={n}",
                    )
                )

            rebuild_from_raw(
                store, progress=lambda done, total, report: calls.append((done, total))
            )

        assert calls[0] == (1, 3)
        assert calls[-1] == (3, 3)
        assert [c[0] for c in calls] == sorted(c[0] for c in calls)

    def test_FailingProgressCallback_NeverBreaksTheRebuild(self, tmp_path):
        from obdi.rebuild import rebuild_from_raw
        from obdi.store import Store

        def explode(done, total, report):
            raise RuntimeError("reporting must never break the work")

        with Store(tmp_path / "s.sqlite3") as store:
            report = rebuild_from_raw(store, progress=explode)

        assert report.problems == []


class TestRebuildReconciliation:
    '''"Rebuild finished" must come with "and here is what changed": the
    aborted live rebuild silently dropped every Starling row, and a
    before/after diff would have announced it instead.'''

    def test_VanishedAndNewAccounts_AreNamedInTheReport(self, tmp_path):
        import json as _json

        from obdi.providers.starling import artefact_for
        from obdi.rebuild import rebuild_from_raw
        from obdi.store import Store

        with Store(tmp_path / "s.sqlite3") as store:
            store.connection.execute(
                "INSERT INTO transactions (entity_id, account_id, amount_minor, "
                "value_date, booking_date, description, source, currency, tier, "
                "status, content_key, occurrence, first_seen_at, last_seen_at) "
                "VALUES ('e-old', 'orphan-account', -100, '2026-07-01', "
                "'2026-07-01', 'X', 'truelayer', 'GBP', 'authoritative', "
                "'booked', 'ck-old', 0, '2026-07-01T00:00:00', "
                "'2026-07-01T00:00:00')"
            )
            store.connection.commit()
            body = _json.dumps(
                {
                    "feedItems": [
                        {
                            "feedItemUid": "f-1",
                            "amount": {"currency": "GBP", "minorUnits": 1499},
                            "direction": "OUT",
                            "transactionTime": "2026-03-14T09:15:00.000Z",
                            "source": "MASTER_CARD",
                            "status": "SETTLED",
                            "counterPartyName": "Tesco",
                            "reference": "TESCO",
                        }
                    ]
                }
            ).encode("utf-8")
            store.land_artefact(
                artefact_for(
                    body,
                    account_id="starling:uid-1",
                    kind="feed",
                    origin="https://api.example.com/feed?x=1",
                )
            )

            report = rebuild_from_raw(store)

        described = report.describe()
        assert "orphan-account: 1 -> 0 (VANISHED" in described
        assert "starling:uid-1: 0 -> 1 (new)" in described

    def test_FaithfulReplay_SaysSo(self, tmp_path):
        import json as _json

        from obdi.providers.starling import artefact_for
        from obdi.rebuild import rebuild_from_raw
        from obdi.store import Store

        with Store(tmp_path / "s.sqlite3") as store:
            body = _json.dumps(
                {
                    "feedItems": [
                        {
                            "feedItemUid": "f-1",
                            "amount": {"currency": "GBP", "minorUnits": 1499},
                            "direction": "OUT",
                            "transactionTime": "2026-03-14T09:15:00.000Z",
                            "source": "MASTER_CARD",
                            "status": "SETTLED",
                            "counterPartyName": "Tesco",
                            "reference": "TESCO",
                        }
                    ]
                }
            ).encode("utf-8")
            store.land_artefact(
                artefact_for(
                    body,
                    account_id="starling:uid-1",
                    kind="feed",
                    origin="https://api.example.com/feed?x=1",
                )
            )
            rebuild_from_raw(store)
            report = rebuild_from_raw(store)

        assert "unchanged" in report.describe()


class TestRebuildAppliesTheMap:
    '''The button says "through the current account map" and for a while
    that was false: artefacts landed before a bind replayed under the raw
    ref while coverage sat under the canonical - one real account, two
    rows, and a bind box offered for an account the map already named.'''

    def _feed_body(self, uid):
        import json as _json

        return _json.dumps(
            {
                "feedItems": [
                    {
                        "feedItemUid": uid,
                        "amount": {"currency": "GBP", "minorUnits": 1499},
                        "direction": "OUT",
                        "transactionTime": "2026-03-14T09:15:00.000Z",
                        "source": "MASTER_CARD",
                        "status": "SETTLED",
                        "counterPartyName": "Tesco",
                        "reference": "TESCO",
                    }
                ]
            }
        ).encode("utf-8")

    def test_QualifiedRefs_ResolveThroughTheMap(self, tmp_path):
        from obdi.accounts import AccountBinding, AccountMap
        from obdi.providers.starling import artefact_for
        from obdi.rebuild import rebuild_from_raw
        from obdi.store import Store

        bound = AccountMap(
            [
                AccountBinding(
                    canonical_id="starling-space-bills",
                    source="starling",
                    provider_account_id="uid-1",
                )
            ]
        )
        with Store(tmp_path / "s.sqlite3") as store:
            store.land_artefact(
                artefact_for(
                    self._feed_body("f-1"),
                    account_id="starling:uid-1",
                    kind="feed",
                    origin="https://api.example.com/feed?x=1",
                )
            )

            rebuild_from_raw(store, account_map=bound)

            rows = store.connection.execute(
                "SELECT DISTINCT account_id FROM transactions"
            ).fetchall()
        assert [r[0] for r in rows] == ["starling-space-bills"]

    def test_RefAndGhostArtefacts_ConsolidateUnderOneName(self, tmp_path):
        '''The blob-era state: the same feed item landed once under the
        raw ref and once under the canonical. Resolution plus tier-1
        identity must yield ONE row under the canonical, not two rows
        under two names.'''
        from obdi.accounts import AccountBinding, AccountMap
        from obdi.providers.starling import artefact_for
        from obdi.rebuild import rebuild_from_raw
        from obdi.store import Store

        bound = AccountMap(
            [
                AccountBinding(
                    canonical_id="starling-space-bills",
                    source="starling",
                    provider_account_id="uid-1",
                )
            ]
        )
        with Store(tmp_path / "s.sqlite3") as store:
            store.land_artefact(
                artefact_for(
                    self._feed_body("f-1"),
                    account_id="starling:uid-1",
                    kind="feed",
                    origin="https://api.example.com/feed?x=1",
                )
            )
            store.land_artefact(
                artefact_for(
                    self._feed_body("f-1") + b" ",
                    account_id="starling-space-bills",
                    kind="feed",
                    origin="https://api.example.com/feed?x=2",
                )
            )

            rebuild_from_raw(store, account_map=bound)

            rows = store.connection.execute(
                "SELECT account_id, COUNT(*) FROM transactions GROUP BY account_id"
            ).fetchall()
        assert [tuple(r) for r in rows] == [("starling-space-bills", 1)]

    def test_UnboundRefs_StayQualified_AndKeepTheirBindBoxEligibility(
        self, tmp_path
    ):
        from obdi.providers.starling import artefact_for
        from obdi.rebuild import rebuild_from_raw
        from obdi.store import Store

        with Store(tmp_path / "s.sqlite3") as store:
            store.land_artefact(
                artefact_for(
                    self._feed_body("f-1"),
                    account_id="starling:uid-9",
                    kind="feed",
                    origin="https://api.example.com/feed?x=1",
                )
            )

            rebuild_from_raw(store, account_map=None)

            rows = store.connection.execute(
                "SELECT DISTINCT account_id FROM transactions"
            ).fetchall()
        assert [r[0] for r in rows] == ["starling:uid-9"]


class TestStarlingFeedIdentityFromOrigin:
    '''The blob: three accounts' history landed under one canonical label
    because the then-map said so. The origin URL records the request that
    actually happened, so replay identity comes from there - the stored
    label never decides.'''

    def _feed(self, store, *, label, account_uid, category_uid, item_uid, minor):
        import json as _json

        from obdi.providers.starling import artefact_for

        body = _json.dumps(
            {
                "feedItems": [
                    {
                        "feedItemUid": item_uid,
                        "amount": {"currency": "GBP", "minorUnits": minor},
                        "direction": "OUT",
                        "transactionTime": "2026-03-14T09:15:00.000Z",
                        "source": "MASTER_CARD",
                        "status": "SETTLED",
                        "counterPartyName": "Tesco",
                        "reference": "TESCO",
                    }
                ]
            }
        ).encode("utf-8")
        store.land_artefact(
            artefact_for(
                body,
                account_id=label,
                kind="feed",
                origin=(
                    "https://api.example.com/api/v2/feed/account/"
                    f"{account_uid}/category/{category_uid}?changesSince=x"
                ),
            )
        )

    def _accounts_artefact(self, store, account_uid, default_category):
        import json as _json

        from obdi.providers.starling import artefact_for

        body = _json.dumps(
            {
                "accounts": [
                    {"accountUid": account_uid, "defaultCategory": default_category}
                ]
            }
        ).encode("utf-8")
        store.land_artefact(
            artefact_for(
                body,
                account_id="starling",
                kind="accounts",
                origin="https://api.example.com/api/v2/accounts",
            )
        )

    def test_MislabelledBlobArtefact_ReplaysUnderItsTrueAccounts(self, tmp_path):
        '''Two feeds for two different Spaces, both landed under ONE lying
        label. Identity from origin splits them back apart.'''
        from obdi.rebuild import rebuild_from_raw
        from obdi.store import Store

        with Store(tmp_path / "s.sqlite3") as store:
            self._feed(
                store,
                label="starling-space-bills",
                account_uid="acct-1",
                category_uid="cat-bills",
                item_uid="f-1",
                minor=900,
            )
            self._feed(
                store,
                label="starling-space-bills",
                account_uid="acct-1",
                category_uid="cat-money",
                item_uid="f-2",
                minor=202,
            )

            rebuild_from_raw(store)

            rows = store.connection.execute(
                "SELECT account_id, COUNT(*) FROM transactions "
                "GROUP BY account_id ORDER BY account_id"
            ).fetchall()
        assert [tuple(r) for r in rows] == [
            ("starling:cat-bills", 1),
            ("starling:cat-money", 1),
        ]

    def test_MainAccountFeed_KeysByAccountUid_ViaDefaultCategory(self, tmp_path):
        from obdi.accounts import AccountBinding, AccountMap
        from obdi.rebuild import rebuild_from_raw
        from obdi.store import Store

        bound = AccountMap(
            [
                AccountBinding(
                    canonical_id="starling-personal",
                    source="starling",
                    provider_account_id="acct-1",
                )
            ]
        )
        with Store(tmp_path / "s.sqlite3") as store:
            self._accounts_artefact(store, "acct-1", "cat-default")
            self._feed(
                store,
                label="starling:acct-1",
                account_uid="acct-1",
                category_uid="cat-default",
                item_uid="f-1",
                minor=1499,
            )

            rebuild_from_raw(store, account_map=bound)

            rows = store.connection.execute(
                "SELECT DISTINCT account_id FROM transactions"
            ).fetchall()
        assert [r[0] for r in rows] == ["starling-personal"]

    def test_DuplicateEvidence_CollapsesOncePerTrueAccount(self, tmp_path):
        '''The raw-ref artefact and the blob-labelled artefact carry the
        same feed item; origin identity puts both under one account and
        tier-1 identity keeps one row.'''
        from obdi.rebuild import rebuild_from_raw
        from obdi.store import Store

        with Store(tmp_path / "s.sqlite3") as store:
            self._feed(
                store,
                label="starling:cat-bills",
                account_uid="acct-1",
                category_uid="cat-bills",
                item_uid="f-1",
                minor=900,
            )
            self._feed(
                store,
                label="starling-space-bills",
                account_uid="acct-1",
                category_uid="cat-bills",
                item_uid="f-1",
                minor=900,
            )

            report = rebuild_from_raw(store)

            rows = store.connection.execute(
                "SELECT account_id, COUNT(*) FROM transactions GROUP BY account_id"
            ).fetchall()
        assert [tuple(r) for r in rows] == [("starling:cat-bills", 1)]
        assert report.problems == []


class TestRecordCountMetadata:
    """Artefact-count progress lies as overlapping pulls accumulate: one
    artefact can carry 5,000 rows, the next 3. Counts are landed once as
    metadata (or backfilled by the first rebuild that parses them) and
    never recalculated."""

    def _feed(self, store, uid, items):
        import json as _json

        from obdi.providers.starling import artefact_for

        body = _json.dumps(
            {
                "feedItems": [
                    {
                        "feedItemUid": f"{uid}-{n}",
                        "amount": {"currency": "GBP", "minorUnits": 100 + n},
                        "direction": "OUT",
                        "transactionTime": "2026-03-14T09:15:00.000Z",
                        "source": "MASTER_CARD",
                        "status": "SETTLED",
                        "counterPartyName": "Tesco",
                        "reference": "TESCO",
                    }
                    for n in range(items)
                ]
            }
        ).encode("utf-8")
        store.land_artefact(
            artefact_for(
                body,
                account_id=f"starling:{uid}",
                kind="feed",
                origin=f"https://api.example.com/feed/account/a/category/{uid}?x=1",
            )
        )

    def test_FirstRebuild_BackfillsCounts_SecondKnowsTotalsUpFront(self, tmp_path):
        from obdi.rebuild import rebuild_from_raw
        from obdi.store import Store

        with Store(tmp_path / "s.sqlite3") as store:
            self._feed(store, "uid-1", 3)
            self._feed(store, "uid-2", 2)

            first = rebuild_from_raw(store)
            assert first.artefacts_uncounted == 2

            second = rebuild_from_raw(store)
            assert second.artefacts_uncounted == 0
            assert second.records_total_known == 5
            counts = store.connection.execute(
                "SELECT record_count FROM raw_artefacts WHERE source = "
                "'starling-feed' ORDER BY record_count"
            ).fetchall()
        assert [r[0] for r in counts] == [2, 3]


class TestCardReplay:
    '''Signs wired against the landed evidence: DEBIT arrives positive
    (spending), CREDIT arrives negative (payments) - statement language,
    negated into the store's outflow-negative canon, with the type column
    verifying every row.'''

    def _card_artefact(self, records):
        import json as _json

        from obdi.providers.truelayer import artefact_for

        return artefact_for(
            _json.dumps({"results": records}).encode("utf-8"),
            account_id="d000b07d",
            kind="card-booked",
            requested="from=2026-05-04&to=2026-08-02",
        )

    def _record(self, amount, kind, uid):
        return {
            "amount": amount,
            "currency": "GBP",
            "description": "PURCHASE" if kind == "DEBIT" else "PAYMENT RECEIVED",
            "timestamp": "2026-07-01T00:00:00Z",
            "transaction_type": kind,
            "transaction_id": uid,
            "normalised_provider_transaction_id": f"txn-{uid}",
            "provider_transaction_id": uid,
        }

    def test_CardRows_ReplayNegated_PurchasesOut_PaymentsIn(self, tmp_path):
        from obdi.rebuild import rebuild_from_raw
        from obdi.store import Store

        with Store(tmp_path / "s.sqlite3") as store:
            store.land_artefact(
                self._card_artefact(
                    [
                        self._record(335.64, "DEBIT", "c-1"),
                        self._record(-1500.0, "CREDIT", "c-2"),
                    ]
                )
            )

            report = rebuild_from_raw(store)

            rows = store.connection.execute(
                "SELECT amount_minor FROM transactions ORDER BY amount_minor"
            ).fetchall()
        assert report.problems == []
        assert [r[0] for r in rows] == [-33564, 150000]

    def test_ConventionChange_FailsTheArtefactLoudly(self, tmp_path):
        '''A DEBIT arriving negative means the statement convention this
        mapping was verified against has changed - the artefact is
        recorded as a problem, never guessed at.'''
        from obdi.rebuild import rebuild_from_raw
        from obdi.store import Store

        with Store(tmp_path / "s.sqlite3") as store:
            store.land_artefact(
                self._card_artefact([self._record(-9.99, "DEBIT", "c-9")])
            )

            report = rebuild_from_raw(store)

        assert len(report.problems) == 1
        assert "convention" in report.problems[0]
