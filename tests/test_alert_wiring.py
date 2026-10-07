"""The faults that ran for seven weeks unannounced now reach `obdi alert`.

Two faults each ran for seven weeks on the live deployment with nothing saying
so. Three credit cards landed nothing for about sixty days: the scheduler was
not asking, and the stale-feed alert needs a SECOND source to prove an account
behind while a card has one. And the push to Actual was refused on every
cycle: the refusal went to the container log and nowhere else, and the last
successful apply grew seven weeks old without a page or a notification
saying so.

`coverage.silent_feeds` and `identity_health.shared` existed and nothing
called them. These scenarios drive the assembly the `alert` command runs, with
constructed stores whose expected findings were decided before the first run.

Every finding is read from a phone, so each one is also asserted to carry no
amount, payee, description, or provider id, using values distinctive enough
that the assertion cannot pass by coincidence.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from obdi.cli import collect_alert_findings, main
from obdi.ingest.connections import Connection, ConnectionStore
from obdi.ingest.identity_health import identity_health
from obdi.ingest.store import Store
from obdi.read.alerts import Finding, process

NOW = datetime(2026, 2, 1, 12, 0, tzinfo=UTC)

#: Distinctive so that none of them can appear in a message by coincidence.
PRIVATE_MINOR = 87123
PRIVATE_DESCRIPTION = "Quokka Hardware Emporium"
PRIVATE_SOURCE_ID = "tl-quokka-7781"
PRIVATE_FORMS = (str(PRIVATE_MINOR), "871.23", PRIVATE_DESCRIPTION, PRIVATE_SOURCE_ID)

SILENT = "silent-feed:"
REFUSED = "push-refused"
STALE = "push-stale"
SHARED = "shared-identity:"


def _keys(findings: list[Finding], prefix: str) -> list[str]:
    return sorted(f.key for f in findings if f.key.startswith(prefix))


def _message(findings: list[Finding], key: str) -> str:
    return next(f.message for f in findings if f.key == key)


def _db(tmp_path: Path) -> Path:
    return tmp_path / "store.sqlite3"


def _schedule_truelayer(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "connections.json"
    ConnectionStore(path).put(
        Connection(
            connection_id="halifax",
            provider="halifax",
            access_token="a",
            refresh_token="r",
            access_expires_at="2099-01-01T00:00:00+00:00",
            consent_expires_at="2099-01-01T00:00:00+00:00",
            scopes="",
        )
    )
    monkeypatch.setenv("OBDI_CONNECTION_STORE", str(path))


def _account_map_file(
    tmp_path: Path, monkeypatch, *, bindings=(), actual=()
) -> Path:
    path = tmp_path / "accounts.json"
    path.write_text(
        json.dumps({"bindings": list(bindings), "actual": list(actual)}), encoding="utf-8"
    )
    monkeypatch.setenv("OBDI_ACCOUNT_MAP", str(path))
    return path


def _ask(store: Store, ref: str, when: datetime, outcome: str = "landed") -> None:
    store.record_attempt(
        source="truelayer-card-booked",
        connection_id="halifax",
        account_ref=ref,
        asked="routine",
        request_meta=json.dumps({"trigger": "scheduled"}),
        outcome=outcome,
        now=when,
    )


def _row(store: Store, land, account: str, *, day: str = "2026-01-01", tag: str = "") -> None:
    land(
        store,
        description=f"{PRIVATE_DESCRIPTION}{tag}",
        amount_minor=-PRIVATE_MINOR,
        account=account,
        value_date=day,
        source_id=f"{PRIVATE_SOURCE_ID}{tag}",
    )


def _three_cards(store: Store, land) -> None:
    """Known answers with now = 2026-02-01 and the three-day threshold.

    card-1  rows to 01-01, landed ask 01-31                 healthy, quiet
    card-2  rows to 01-01, landed ask 01-05, refused 01-31  silent 27 days
    card-3  rows to 01-01, never asked                      silent 31 days
    """
    for card in ("card-1", "card-2", "card-3"):
        _row(store, land, f"truelayer:{card}", tag=card)
    _ask(store, "truelayer:card-1", datetime(2026, 1, 31, 6, tzinfo=UTC))
    _ask(store, "truelayer:card-2", datetime(2026, 1, 5, 6, tzinfo=UTC))
    _ask(store, "truelayer:card-2", datetime(2026, 1, 31, 6, tzinfo=UTC), "refused")


class TestASilentSingleSourceFeedReachesTheAlert:
    def test_Alert_WhenOneCardIsAskedAndTwoAreNot_NamesExactlyTheTwoSilentOnes(
        self, tmp_path, monkeypatch, land_transaction
    ):
        _schedule_truelayer(tmp_path, monkeypatch)
        with Store(_db(tmp_path)) as store:
            _three_cards(store, land_transaction)

        findings = collect_alert_findings(_db(tmp_path), now=NOW)

        assert _keys(findings, SILENT) == [
            "silent-feed:truelayer:card-2:truelayer",
            "silent-feed:truelayer:card-3:truelayer",
        ]
        assert "2026-01-05" in _message(findings, "silent-feed:truelayer:card-2:truelayer")
        assert "no successful ask" in _message(
            findings, "silent-feed:truelayer:card-3:truelayer"
        )

    def test_Alert_WhenTheLastGoodAskIsBuriedUnderThousandsOfNewerAsks_StillReadsItsRealDate(
        self, tmp_path, monkeypatch, land_transaction
    ):
        """The ledger read once stopped at the newest thousand rows, so a card
        last landed long ago fell outside it and was misread as never asked."""
        _schedule_truelayer(tmp_path, monkeypatch)
        with Store(_db(tmp_path)) as store:
            _row(store, land_transaction, "truelayer:card-2", tag="card-2")
            _row(store, land_transaction, "truelayer:acc-1", day="2026-01-31", tag="acc-1")
            _ask(store, "truelayer:card-2", datetime(2026, 1, 5, 6, tzinfo=UTC))
            for minute in range(1100):
                _ask(
                    store,
                    "truelayer:acc-1",
                    datetime(2026, 1, 20, 0, 0, tzinfo=UTC) + timedelta(minutes=minute),
                )

        findings = collect_alert_findings(_db(tmp_path), now=NOW)

        assert _keys(findings, SILENT) == ["silent-feed:truelayer:card-2:truelayer"]
        message = _message(findings, "silent-feed:truelayer:card-2:truelayer")
        assert "2026-01-05" in message
        assert "no successful ask" not in message

    def test_Alert_WhenTheFeedIsBoundToANamedAccount_TheLedgerRefIsTranslated(
        self, tmp_path, monkeypatch, land_transaction
    ):
        """Rows carry the canonical name and the ledger carries the provider's
        own ref; without translation a healthy bound card reads as never asked."""
        _schedule_truelayer(tmp_path, monkeypatch)
        _account_map_file(
            tmp_path,
            monkeypatch,
            bindings=[
                {
                    "canonical_id": "halifax-card",
                    "source": "truelayer",
                    "provider_account_id": "prov-9",
                }
            ],
        )
        with Store(_db(tmp_path)) as store:
            _row(store, land_transaction, "halifax-card")
            _ask(store, "truelayer:prov-9", datetime(2026, 1, 31, 6, tzinfo=UTC))

        assert _keys(collect_alert_findings(_db(tmp_path), now=NOW), SILENT) == []

    def test_Alert_WhenTheBoundFeedHasNotBeenAskedForWeeks_NamesTheCanonicalAccount(
        self, tmp_path, monkeypatch, land_transaction
    ):
        _schedule_truelayer(tmp_path, monkeypatch)
        _account_map_file(
            tmp_path,
            monkeypatch,
            bindings=[
                {
                    "canonical_id": "halifax-card",
                    "source": "truelayer",
                    "provider_account_id": "prov-9",
                }
            ],
        )
        with Store(_db(tmp_path)) as store:
            _row(store, land_transaction, "halifax-card")
            _ask(store, "truelayer:prov-9", datetime(2026, 1, 5, 6, tzinfo=UTC))

        findings = collect_alert_findings(_db(tmp_path), now=NOW)

        assert _keys(findings, SILENT) == ["silent-feed:halifax-card:truelayer"]
        assert "halifax-card" in _message(findings, "silent-feed:halifax-card:truelayer")

    def test_Alert_WhenTheSilentAccountIsDeclaredClosed_IsNotAFault(
        self, tmp_path, monkeypatch, land_transaction
    ):
        """A closed account is, correctly, never asked about again.
        Reporting it would be a finding nothing could ever clear."""
        from datetime import date

        from obdi.ingest.accounts import AccountRecord, AccountRef

        _schedule_truelayer(tmp_path, monkeypatch)
        with Store(_db(tmp_path)) as store:
            _three_cards(store, land_transaction)
            store.declare_account(
                AccountRecord(ref=AccountRef("truelayer:card-3"), closed=date(2026, 1, 15))
            )

        findings = collect_alert_findings(_db(tmp_path), now=NOW)

        assert _keys(findings, SILENT) == ["silent-feed:truelayer:card-2:truelayer"]

    @pytest.mark.parametrize(
        "closing",
        [None, "2026-03-01"],
        ids=["declared-and-open", "closing-next-month"],
    )
    def test_Alert_WhenTheSilentAccountIsDeclaredButNotYetClosed_StillNamesIt(
        self, tmp_path, monkeypatch, land_transaction, closing
    ):
        """Declaring an account does not excuse its feed, and neither does a
        closing date that has not arrived."""
        from datetime import date

        from obdi.ingest.accounts import AccountRecord, AccountRef

        _schedule_truelayer(tmp_path, monkeypatch)
        with Store(_db(tmp_path)) as store:
            _three_cards(store, land_transaction)
            store.declare_account(
                AccountRecord(
                    ref=AccountRef("truelayer:card-3"),
                    closed=date.fromisoformat(closing) if closing else None,
                )
            )

        findings = collect_alert_findings(_db(tmp_path), now=NOW)

        assert _keys(findings, SILENT) == [
            "silent-feed:truelayer:card-2:truelayer",
            "silent-feed:truelayer:card-3:truelayer",
        ]

    def test_Message_SaysWhatToDoIfTheAccountIsInFactClosed(
        self, tmp_path, monkeypatch, land_transaction
    ):
        _schedule_truelayer(tmp_path, monkeypatch)
        with Store(_db(tmp_path)) as store:
            _three_cards(store, land_transaction)

        findings = collect_alert_findings(_db(tmp_path), now=NOW)

        assert "declare it closed" in _message(
            findings, "silent-feed:truelayer:card-3:truelayer"
        )

    def test_Alert_WhenNothingIsScheduled_ASilentFileOnlyAccountIsNotAFault(
        self, tmp_path, land_transaction
    ):
        """No connection store and no first-party token: nothing is pulled on
        a schedule, so nothing can have stopped being pulled."""
        with Store(_db(tmp_path)) as store:
            _three_cards(store, land_transaction)

        assert _keys(collect_alert_findings(_db(tmp_path), now=NOW), SILENT) == []

    def test_Alert_ForAnAccountThatNeverLandedARow_IsInvisibleToThisDetector(
        self, tmp_path, monkeypatch
    ):
        """The known limitation, pinned so it stays stated: an account holding
        no rows has no known source, so only refusals in the ledger mark it."""
        _schedule_truelayer(tmp_path, monkeypatch)
        with Store(_db(tmp_path)) as store:
            _ask(store, "truelayer:card-9", datetime(2026, 1, 5, 6, tzinfo=UTC), "refused")

        assert _keys(collect_alert_findings(_db(tmp_path), now=NOW), SILENT) == []

    def test_Alert_OnAnEmptyStore_RaisesNothing(self, tmp_path, monkeypatch):
        _schedule_truelayer(tmp_path, monkeypatch)
        with Store(_db(tmp_path)):
            pass

        findings = collect_alert_findings(_db(tmp_path), now=NOW)

        assert _keys(findings, SILENT) == []

    @pytest.mark.parametrize("content", ["this is not json", '{"bindings": [{"unexpected": 1}]}'])
    def test_Alert_WhenTheAccountMapCannotBeRead_SaysTheCheckCouldNotRun(
        self, tmp_path, monkeypatch, land_transaction, content
    ):
        """A map that cannot be read must not pass as a feed that is healthy,
        and must not take the scheduler cycle down either."""
        _schedule_truelayer(tmp_path, monkeypatch)
        with Store(_db(tmp_path)) as store:
            _three_cards(store, land_transaction)
        (tmp_path / "accounts.json").write_text(content, encoding="utf-8")
        monkeypatch.setenv("OBDI_ACCOUNT_MAP", str(tmp_path / "accounts.json"))

        findings = collect_alert_findings(_db(tmp_path), now=NOW)

        assert _keys(findings, "check-failed:silent-feeds") == ["check-failed:silent-feeds"]
        assert _keys(findings, SILENT) == []

    def test_Alert_WhenTheCardIsAskedAgain_TheFindingClearsAndIsAnnouncedOnce(
        self, tmp_path, monkeypatch, land_transaction
    ):
        _schedule_truelayer(tmp_path, monkeypatch)
        with Store(_db(tmp_path)) as store:
            _row(store, land_transaction, "truelayer:card-2")
            _ask(store, "truelayer:card-2", datetime(2026, 1, 5, 6, tzinfo=UTC))
        state = tmp_path / "state.json"
        sent: list[str] = []

        def send(message: str) -> bool:
            sent.append(message)
            return True

        process(collect_alert_findings(_db(tmp_path), now=NOW), state, send)
        process(collect_alert_findings(_db(tmp_path), now=NOW), state, send)
        with Store(_db(tmp_path)) as store:
            _ask(store, "truelayer:card-2", datetime(2026, 1, 31, 18, tzinfo=UTC))
        process(collect_alert_findings(_db(tmp_path), now=NOW), state, send)

        silent = [m for m in sent if "card-2" in m]
        assert len(silent) == 2
        assert silent[1].startswith("resolved: ")

    def test_Message_NamesTheAccount_AndCarriesNoAmountPayeeOrProviderId(
        self, tmp_path, monkeypatch, land_transaction
    ):
        _schedule_truelayer(tmp_path, monkeypatch)
        with Store(_db(tmp_path)) as store:
            _three_cards(store, land_transaction)

        findings = collect_alert_findings(_db(tmp_path), now=NOW)

        silent = [f for f in findings if f.key.startswith(SILENT)]
        assert silent
        for finding in silent:
            for private in PRIVATE_FORMS:
                assert private not in finding.message

    def test_Command_PrintsTheSilentFeedAndRemembersItForTheEdgeTrigger(
        self, tmp_path, monkeypatch, capsys, land_transaction
    ):
        _schedule_truelayer(tmp_path, monkeypatch)
        monkeypatch.setenv("OBDI_ALERT_STATE", str(tmp_path / "state.json"))
        with Store(_db(tmp_path)) as store:
            _row(store, land_transaction, "truelayer:card-2")
            # Older than any real clock the command could be run on.
            _ask(store, "truelayer:card-2", datetime(2020, 1, 5, 6, tzinfo=UTC))

        exit_code = main(["--db", str(_db(tmp_path)), "alert"])

        assert exit_code == 0
        assert "card-2" in capsys.readouterr().out
        remembered = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
        assert "silent-feed:truelayer:card-2:truelayer" in remembered


def _duplicate_identities(store: Store, land, account: str, *, tag: str = "") -> None:
    """The state ingest prevents: two payments on one identity."""
    for index in range(2):
        land(
            store,
            description=f"{PRIVATE_DESCRIPTION}{tag}",
            amount_minor=-PRIVATE_MINOR,
            account=account,
            value_date="2026-01-10",
            source_id=f"{PRIVATE_SOURCE_ID}{tag}-{index}",
        )
    store.connection.execute(
        "UPDATE transactions SET occurrence = 0 WHERE account_id = ?", (account,)
    )
    store.connection.commit()


def _configure_actual(tmp_path: Path, monkeypatch, *, account: str = "halifax-current") -> Path:
    monkeypatch.setenv("ACTUAL_SYNC_ID", "sync-1")
    return _account_map_file(
        tmp_path,
        monkeypatch,
        actual=[{"canonical_id": account, "actual_account_id": "act-1"}],
    )


class TestARefusedPushReachesTheAlert:
    def test_Alert_WhenThePushWouldBeRefused_CarriesTheRefusalNamingTheAccount(
        self, tmp_path, monkeypatch, land_transaction
    ):
        _configure_actual(tmp_path, monkeypatch)
        with Store(_db(tmp_path)) as store:
            _duplicate_identities(store, land_transaction, "halifax-current")

        findings = collect_alert_findings(_db(tmp_path), now=NOW)

        assert _keys(findings, REFUSED) == [REFUSED]
        message = _message(findings, REFUSED)
        assert "halifax-current" in message
        assert "duplicate imported id" in message
        assert "Rebuild from raw" in message

    def test_Message_CarriesNoAmountPayeeProviderIdOrContentKey(
        self, tmp_path, monkeypatch, land_transaction
    ):
        _configure_actual(tmp_path, monkeypatch)
        with Store(_db(tmp_path)) as store:
            _duplicate_identities(store, land_transaction, "halifax-current")
            content_key = store.all_transactions()[0].content_key

        message = _message(collect_alert_findings(_db(tmp_path), now=NOW), REFUSED)

        assert content_key
        for private in (*PRIVATE_FORMS, content_key[:8]):
            assert private not in message

    def test_Alert_WhenNoRowsShareAnIdentity_RaisesNoRefusal(
        self, tmp_path, monkeypatch, land_transaction
    ):
        _configure_actual(tmp_path, monkeypatch)
        with Store(_db(tmp_path)) as store:
            _row(store, land_transaction, "halifax-current")

        assert _keys(collect_alert_findings(_db(tmp_path), now=NOW), REFUSED) == []

    def test_Alert_WhenActualIsNotConfigured_AFaultThePushWouldHitIsNotAFinding(
        self, tmp_path, monkeypatch, land_transaction
    ):
        """An instance with no Actual is a deliberate configuration."""
        _account_map_file(
            tmp_path,
            monkeypatch,
            actual=[{"canonical_id": "halifax-current", "actual_account_id": "act-1"}],
        )
        with Store(_db(tmp_path)) as store:
            _duplicate_identities(store, land_transaction, "halifax-current")

        assert _keys(collect_alert_findings(_db(tmp_path), now=NOW), REFUSED) == []

    def test_Alert_WhenActualIsConfiguredWithoutAnAccountMap_SaysNoPushCanBeBuilt(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.setenv("ACTUAL_SYNC_ID", "sync-1")
        with Store(_db(tmp_path)):
            pass

        findings = collect_alert_findings(_db(tmp_path), now=NOW)

        assert _keys(findings, REFUSED) == [REFUSED]
        assert "OBDI_ACCOUNT_MAP" in _message(findings, REFUSED)

    def test_Alert_NeverQueuesAPush_NorWritesTheActualDirectoryOrTheMap(
        self, tmp_path, monkeypatch, land_transaction
    ):
        map_path = _configure_actual(tmp_path, monkeypatch)
        before = map_path.read_bytes()
        with Store(_db(tmp_path)) as store:
            _row(store, land_transaction, "halifax-current")

        collect_alert_findings(_db(tmp_path), now=NOW)

        assert not (tmp_path / "actual").exists()
        assert map_path.read_bytes() == before

    def test_Alert_WhileARebuildIsReplaying_DoesNotJudgeAHalfPopulatedStore(
        self, tmp_path, monkeypatch, land_transaction
    ):
        _configure_actual(tmp_path, monkeypatch)
        with Store(_db(tmp_path)) as store:
            _duplicate_identities(store, land_transaction, "halifax-current")
        (tmp_path / "rebuild-status.json").write_text(
            json.dumps({"state": "running", "started_at": "2026-02-01T11:00:00Z"}),
            encoding="utf-8",
        )

        assert _keys(collect_alert_findings(_db(tmp_path), now=NOW), REFUSED) == []

    def test_Alert_WhenTheRefusalIsFixed_TheFindingClearsAndIsAnnouncedOnce(
        self, tmp_path, monkeypatch, land_transaction
    ):
        _configure_actual(tmp_path, monkeypatch)
        with Store(_db(tmp_path)) as store:
            _duplicate_identities(store, land_transaction, "halifax-current")
        state = tmp_path / "state.json"
        sent: list[str] = []

        def send(message: str) -> bool:
            sent.append(message)
            return True

        process(collect_alert_findings(_db(tmp_path), now=NOW), state, send)
        process(collect_alert_findings(_db(tmp_path), now=NOW), state, send)
        with Store(_db(tmp_path)) as store:
            store.connection.execute("UPDATE transactions SET occurrence = rowid")
            store.connection.commit()
        process(collect_alert_findings(_db(tmp_path), now=NOW), state, send)

        refusals = [m for m in sent if "cannot be built" in m]
        assert len(refusals) == 2
        assert refusals[1].startswith("resolved: ")


def _result(tmp_path: Path, name: str, finished: datetime, **fields: object) -> None:
    results = tmp_path / "actual" / "results"
    results.mkdir(parents=True, exist_ok=True)
    body = {"ok": True, "finished_at": finished.strftime("%Y-%m-%dT%H:%M:%S.000Z"), **fields}
    (results / name).write_text(json.dumps(body), encoding="utf-8")


def _hours_ago(hours: float) -> datetime:
    return NOW - timedelta(hours=hours)


class TestAStalePushReachesTheAlert:
    @pytest.fixture
    def configured(self, tmp_path, monkeypatch):
        _configure_actual(tmp_path, monkeypatch)
        with Store(_db(tmp_path)):
            pass

    def _findings(self, tmp_path):
        return collect_alert_findings(_db(tmp_path), now=NOW)

    def test_Alert_WhenTheNewestApplyIsRecent_RaisesNothing(self, tmp_path, configured):
        _result(tmp_path, "push-1.json", _hours_ago(2))

        assert _keys(self._findings(tmp_path), STALE) == []

    def test_Alert_WhenTheNewestApplyIsAboveADayOld_NamesHowLong(self, tmp_path, configured):
        _result(tmp_path, "push-1.json", _hours_ago(30))

        findings = self._findings(tmp_path)

        assert _keys(findings, STALE) == [STALE]
        assert "30 hours" in _message(findings, STALE)

    @pytest.mark.parametrize(("hours", "expected"), [(23, []), (25, [STALE])])
    def test_Alert_AroundTheThreshold_FiresOnlyBeyondIt(
        self, tmp_path, configured, hours, expected
    ):
        _result(tmp_path, "push-1.json", _hours_ago(hours))

        assert _keys(self._findings(tmp_path), STALE) == expected

    def test_Alert_WhenOnlyFailuresAreRecent_TheOldSuccessStillDecides(
        self, tmp_path, configured
    ):
        _result(tmp_path, "push-1.json", _hours_ago(30))
        _result(tmp_path, "push-2.json", _hours_ago(1), ok=False, error="refused")

        assert _keys(self._findings(tmp_path), STALE) == [STALE]

    def test_Alert_WhenOnlyAnAuditIsRecent_AnAuditIsNotAnApply(self, tmp_path, configured):
        _result(tmp_path, "push-1.json", _hours_ago(30))
        _result(tmp_path, "audit-1.json", _hours_ago(1), kind="audit")

        assert _keys(self._findings(tmp_path), STALE) == [STALE]

    def test_Alert_WhenNothingHasEverBeenApplied_SaysSo(self, tmp_path, configured):
        findings = self._findings(tmp_path)

        assert _keys(findings, STALE) == [STALE]
        assert "no applied push on record" in _message(findings, STALE)

    def test_Alert_WhenTheNewestResultCannotBeRead_AnOlderSuccessStillDecides(
        self, tmp_path, configured
    ):
        _result(tmp_path, "push-1.json", _hours_ago(30))
        (tmp_path / "actual" / "results" / "push-2.json").write_text("{broken", encoding="utf-8")

        findings = self._findings(tmp_path)

        assert _keys(findings, STALE) == [STALE]
        assert "1 result file could not be read" in _message(findings, STALE)

    def test_Alert_WhenAnUnreadableFileSitsBesideARecentApply_RaisesNothing(
        self, tmp_path, configured
    ):
        _result(tmp_path, "push-1.json", _hours_ago(2))
        (tmp_path / "actual" / "results" / "push-2.json").write_text("{broken", encoding="utf-8")

        assert _keys(self._findings(tmp_path), STALE) == []

    def test_Alert_WhenTheOnlySuccessHasNoReadableTime_CountsAsNothingApplied(
        self, tmp_path, configured
    ):
        results = tmp_path / "actual" / "results"
        results.mkdir(parents=True)
        (results / "push-1.json").write_text(
            json.dumps({"ok": True, "finished_at": "yesterday-ish"}), encoding="utf-8"
        )

        assert _keys(self._findings(tmp_path), STALE) == [STALE]

    def test_Alert_NamesWhenTheApplierWasLastSeen_AndSaysSoWhenNeverSeen(
        self, tmp_path, configured
    ):
        _result(tmp_path, "push-1.json", _hours_ago(30))
        unseen = _message(self._findings(tmp_path), STALE)
        (tmp_path / "actual" / "heartbeat.json").write_text(
            json.dumps({"at": "2026-02-01T11:59:00.000Z"}), encoding="utf-8"
        )
        seen = _message(self._findings(tmp_path), STALE)

        assert "never been seen" in unseen
        assert "2026-02-01T11:59" in seen

    def test_Alert_WhenAnApplyLands_TheFindingClearsAndIsAnnouncedOnce(
        self, tmp_path, configured
    ):
        _result(tmp_path, "push-1.json", _hours_ago(30))
        state = tmp_path / "state.json"
        sent: list[str] = []

        def send(message: str) -> bool:
            sent.append(message)
            return True

        process(self._findings(tmp_path), state, send)
        process(self._findings(tmp_path), state, send)
        _result(tmp_path, "push-2.json", _hours_ago(1))
        process(self._findings(tmp_path), state, send)

        stale = [m for m in sent if "Actual" in m]
        assert len(stale) == 2
        assert stale[1].startswith("resolved: ")

    def test_Alert_WhenActualIsNotConfiguredAtAll_OldResultsAreNotAFault(
        self, tmp_path, monkeypatch
    ):
        with Store(_db(tmp_path)):
            pass
        _result(tmp_path, "push-1.json", _hours_ago(500))

        assert _keys(collect_alert_findings(_db(tmp_path), now=NOW), STALE) == []

    def test_Alert_WhenTheSyncIdIsSetButNothingIsBoundYet_SetUpIsNotAFault(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.setenv("ACTUAL_SYNC_ID", "sync-1")
        _account_map_file(tmp_path, monkeypatch)
        with Store(_db(tmp_path)):
            pass

        assert _keys(collect_alert_findings(_db(tmp_path), now=NOW), STALE) == []

    def test_Alert_WhenBindingsExistButTheSyncIdIsEmpty_ActualIsOff(
        self, tmp_path, monkeypatch
    ):
        _account_map_file(
            tmp_path,
            monkeypatch,
            actual=[{"canonical_id": "halifax-current", "actual_account_id": "act-1"}],
        )
        with Store(_db(tmp_path)):
            pass

        assert _keys(collect_alert_findings(_db(tmp_path), now=NOW), STALE) == []

    def test_Message_NamesNeitherAmountPayeeNorProviderIdNorTheFailureText(
        self, tmp_path, configured
    ):
        """A failed result's own error can quote anything the applier saw."""
        _result(tmp_path, "push-1.json", _hours_ago(30))
        _result(
            tmp_path,
            "push-2.json",
            _hours_ago(1),
            ok=False,
            error=f"rejected {PRIVATE_DESCRIPTION} {PRIVATE_MINOR} {PRIVATE_SOURCE_ID}",
        )

        message = _message(self._findings(tmp_path), STALE)

        for private in PRIVATE_FORMS:
            assert private not in message


class TestRowsSharingAnIdentityReachTheAlert:
    def test_Alert_ForEachAccountWithSharedRows_RaisesOneFindingNamingIt(
        self, tmp_path, land_transaction
    ):
        """Two accounts affected, one clean: exactly the affected two."""
        with Store(_db(tmp_path)) as store:
            _duplicate_identities(store, land_transaction, "halifax-current", tag="a")
            _duplicate_identities(store, land_transaction, "starling:main", tag="b")
            _row(store, land_transaction, "monzo-pot", tag="c")

        findings = collect_alert_findings(_db(tmp_path), now=NOW)

        assert _keys(findings, SHARED) == [
            "shared-identity:halifax-current",
            "shared-identity:starling:main",
        ]
        message = _message(findings, "shared-identity:halifax-current")
        assert "halifax-current" in message
        assert "Rebuild from raw" in message

    def test_Alert_WhenIdentitiesAreDistinct_RaisesNothing(self, tmp_path, land_transaction):
        with Store(_db(tmp_path)) as store:
            _row(store, land_transaction, "halifax-current", tag="a")
            _row(store, land_transaction, "halifax-current", day="2026-01-20", tag="b")

        assert _keys(collect_alert_findings(_db(tmp_path), now=NOW), SHARED) == []

    def test_Alert_WhenOnlyAFoldedPaymentIsMeasured_RaisesNothing(
        self, tmp_path, land_transaction
    ):
        """The folded count is a measurement awaiting a design decision, and
        alerting on it now would be permanent noise."""
        from dataclasses import replace

        with Store(_db(tmp_path)) as store:
            for index in range(3):
                _row(
                    store,
                    land_transaction,
                    "halifax-current",
                    day=f"2026-01-{10 + index * 5}",
                    tag=str(index),
                )
            survivor = next(t for t in store.all_transactions() if t.source_id.endswith("0"))
            store.record_source(
                replace(
                    survivor,
                    source_id="tl-absorbed",
                    artefact_digest="digest-of-the-response-carrying-tl-absorbed",
                )
            )
            store.connection.commit()
            assert identity_health(store).folded == 1, "the fixture must plant a fold"

        findings = collect_alert_findings(_db(tmp_path), now=NOW)

        assert _keys(findings, SHARED) == []
        assert not [f for f in findings if "folded" in f.message or "fold" in f.key]

    def test_Message_CarriesNoAmountPayeeOrProviderId(self, tmp_path, land_transaction):
        with Store(_db(tmp_path)) as store:
            _duplicate_identities(store, land_transaction, "halifax-current")

        findings = collect_alert_findings(_db(tmp_path), now=NOW)

        message = _message(findings, "shared-identity:halifax-current")
        for private in PRIVATE_FORMS:
            assert private not in message
