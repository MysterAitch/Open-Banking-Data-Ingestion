"""A protection is an alarm on change, and what each kind of change says, on invented accounts.

The account is `everyday` from test_balance_anchors: five rows, src-a, March 2026.

    r1  03-02    -1,250        r4  03-15    -3,300
    r2  03-05   +10,000        r5  03-20      +500
    r3  03-10    -2,000

Stated 1,000.00 for the end of 03-05, 980.00 for 03-10, and 952.00 for 03-20, the account opens at
100,000 - 8,750 = 91,250 and every later balance is met, so it is in agreement through 03-20 and a
protection may be pressed through 03-10 or 03-20 (03-05 defines the opening and tests nothing).
A span through 03-10 holds r1, r2, and r3: three rows, starting 03-02.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, date, datetime

import pytest

from obdi.agreement import standing_of
from obdi.balance_anchors import effective_opening, record_stated_anchor
from obdi.cli import collect_alert_findings
from obdi.ingest import import_file, pair_transfers_across_store
from obdi.ledger import build_ledger
from obdi.models import TransactionStatus
from obdi.movement_completeness import MovementCompleteness
from obdi.overview import NOW as NOW_BAND
from obdi.overview import build_overview
from obdi.protection import (
    ProtectionRefused,
    accept,
    broken_protections,
    check_span,
    press,
    protection_line,
    protection_view,
    recheck,
    withdraw,
)
from obdi.rebuild import rebuild_from_raw
from obdi.store import SCHEMA_VERSION, Store
from obdi.typed_transactions import record_typed_transaction, withdraw_typed_transaction
from test_balance_anchors import ACCOUNT, everyday
from test_ledger import land, txn

D = date
T0 = datetime(2026, 4, 1, 9, 0, tzinfo=UTC)
T1 = datetime(2026, 4, 2, 9, 0, tzinfo=UTC)
T2 = datetime(2026, 4, 3, 9, 0, tzinfo=UTC)


@pytest.fixture
def store(tmp_path):
    with Store(tmp_path / "protection.sqlite3") as opened:
        yield opened


def stated(store: Store, *pairs: tuple[str, str], account: str = ACCOUNT) -> None:
    for day, amount in pairs:
        record_stated_anchor(store, account, day, amount)


def met_account(store: Store) -> None:
    everyday(store)
    stated(store, ("2026-03-05", "1000.00"), ("2026-03-10", "980.00"), ("2026-03-20", "952.00"))


def protect(store: Store, through: str, *, now: datetime = T0, account: str = ACCOUNT) -> None:
    opening = effective_opening(store, account)
    standing = standing_of(opening, [account], MovementCompleteness())
    press(store, account, through, opening=opening, standing=standing, now=now)


def view(store: Store, account: str = ACCOUNT):
    opening = effective_opening(store, account)
    standing = standing_of(opening, [account], MovementCompleteness())
    return protection_view(
        store, account, opening, store.transactions_for_account(account), standing
    )


def says(store: Store, account: str = ACCOUNT) -> tuple[str, ...]:
    record = store.protection_record(account)
    assert record is not None
    return check_span(store, record).change.says()


def add_r6(store: Store) -> None:
    """A new row inside the span through 03-10."""
    land(store, "d-new", txn(ACCOUNT, "src-a", "r6", D(2026, 3, 7), -111, "NEW"))


def amend_r3(store: Store, minor: int = -2100, day: date | None = None) -> None:
    """The provider restates r3, which is dated 03-10 and 20.00 out."""
    land(store, "d-amend", txn(ACCOUNT, "src-a", "r3", day or D(2026, 3, 10), minor, "RAIL"))


class TestPressing:
    def test_Press_ThroughAKnownBalanceTheAccountIsInAgreementThrough_RecordsTheSpan(self, store):
        met_account(store)

        protect(store, "2026-03-10")

        found = view(store)
        assert found.state == "intact"
        assert (found.through, found.span_start, found.rows) == (D(2026, 3, 10), D(2026, 3, 2), 3)
        assert (found.verified_source, found.verified_day) == ("stated", D(2026, 3, 10))
        assert found.pressed_on == D(2026, 4, 1)
        assert protection_line(found) == (
            "Protected through 2026-03-10: 3 rows, verified against stated's balance of "
            "2026-03-10 on 2026-04-01."
        )

    def test_Press_RecordsTheFigureItWasVerifiedAgainstWhereOnlyTheStoreCanSeeIt(self, store):
        met_account(store)

        protect(store, "2026-03-10")

        record = store.protection_record(ACCOUNT)
        assert record is not None
        assert record["verified_minor"] == 98000
        assert record["opening_minor"] == 91250
        assert record["opening_day"] == "2026-03-01"

    @pytest.mark.parametrize(
        "through",
        [
            "2026-03-05",  # defines the opening and tests nothing
            "2026-03-12",  # not a known balance
            "2026-03-21",  # beyond what the account is in agreement through
            "2026-02-30",  # not a date
        ],
    )
    def test_Press_ForADateTheAccountIsNotInAgreementThrough_IsRefusedAndWritesNothing(
        self, store, through
    ):
        met_account(store)

        with pytest.raises(ProtectionRefused):
            protect(store, through)

        assert store.protection_record(ACCOUNT) is None
        assert store.protection_events(ACCOUNT) == []

    def test_Press_WhenABalanceIsUnmet_OffersNothingBeyondTheLastAgreeingOne(self, store):
        everyday(store)
        stated(store, ("2026-03-05", "1000.00"), ("2026-03-10", "980.00"), ("2026-03-20", "999.00"))

        protect(store, "2026-03-10")
        with pytest.raises(ProtectionRefused):
            protect(store, "2026-03-20", now=T1)

    def test_Press_WhenNoBalanceIsKnown_IsRefused(self, store):
        everyday(store)

        with pytest.raises(ProtectionRefused):
            protect(store, "2026-03-10")

    def test_Extend_ThroughALaterDate_ReplacesTheProtectionAndKeepsItsHistory(self, store):
        met_account(store)
        protect(store, "2026-03-10")

        protect(store, "2026-03-20", now=T1)

        found = view(store)
        assert (found.through, found.rows) == (D(2026, 3, 20), 5)
        assert [e["event"] for e in store.protection_events(ACCOUNT)] == ["pressed", "extended"]
        assert len(store.protection_records()) == 1

    def test_Press_ForAnEarlierOrEqualDate_IsRefusedWhileTheLaterProtectionStands(self, store):
        met_account(store)
        protect(store, "2026-03-20")

        with pytest.raises(ProtectionRefused):
            protect(store, "2026-03-10", now=T1)
        with pytest.raises(ProtectionRefused):
            protect(store, "2026-03-20", now=T1)

        assert view(store).through == D(2026, 3, 20)

    def test_Withdraw_RemovesTheProtectionAndRecordsThatItWasWithdrawn(self, store):
        met_account(store)
        protect(store, "2026-03-10")

        withdraw(store, ACCOUNT, now=T1)

        assert view(store).state == "none"
        assert [e["event"] for e in store.protection_events(ACCOUNT)] == ["pressed", "withdrawn"]
        with pytest.raises(ProtectionRefused):
            withdraw(store, ACCOUNT, now=T2)


class TestWhatABreakSays:
    def setup_method(self):
        self.moves: dict[str, object] = {}

    def broken(self, store: Store, *changes) -> tuple[str, ...]:
        met_account(store)
        protect(store, "2026-03-10")
        assert recheck(store, now=T1)[0].intact
        for change in changes:
            change()
        return says(store)

    def test_Span_WhenARowIsAddedInsideIt_SaysOneRowAddedAndItsDate(self, store):
        found = self.broken(store, lambda: add_r6(store))

        assert found == ("1 row added to the span (dated 2026-03-07)",)

    def test_Span_WhenARowIsWithdrawnFromIt_SaysOneRowGone(self, store):
        everyday(store)
        typed = record_typed_transaction(
            store, ACCOUNT, "2026-03-06", "out", "5.00", "TYPED", today=D(2026, 4, 1)
        )
        stated(store, ("2026-03-05", "1000.00"), ("2026-03-10", "975.00"))
        protect(store, "2026-03-10")

        withdraw_typed_transaction(store, ACCOUNT, typed)

        assert says(store) == ("1 row gone from it (dated 2026-03-06)",)

    def test_Span_WhenARowChangesSize_SaysItsSizeChanged(self, store):
        found = self.broken(store, lambda: amend_r3(store))

        assert found == ("1 row's size changed (dated 2026-03-10)",)

    def test_Span_WhenARowChangesDate_SaysWhichDayItMovedTo(self, store):
        found = self.broken(store, lambda: amend_r3(store, -2000, D(2026, 3, 9)))

        assert found == ("1 row's date changed (2026-03-10 to 2026-03-09)",)

    def test_Span_WhenARowChangesStatus_SaysFromAndTo(self, store):
        found = self.broken(
            store,
            lambda: land(
                store,
                "d-void",
                txn(ACCOUNT, "src-a", "r3", D(2026, 3, 10), -2000, "RAIL",
                    status=TransactionStatus.VOID),
            ),
        )

        assert found == ("1 row's status changed (booked to void on 2026-03-10)",)

    def test_Span_WhenARowIsAddedAfterTheThroughDate_NothingBreaks(self, store):
        met_account(store)
        protect(store, "2026-03-10")

        land(store, "d-later", txn(ACCOUNT, "src-a", "r7", D(2026, 3, 25), -900, "LATER"))
        land(store, "d-amend", txn(ACCOUNT, "src-a", "r4", D(2026, 3, 16), -3300, "GARAGE"))

        assert broken_protections(store) == []
        assert view(store).state == "intact"

    def test_Span_WhenALegsPartnerIsRePairedToAnotherAccount_NamesTheAccountItIsNowIn(self, store):
        """A's transfer leg (03-10) is paired with B's. B is then corrected: its leg is amended to
        another size, so the pairing pass pairs A's leg with C's instead."""
        land(
            store, "d-a",
            txn("A", "src-a", "a1", D(2026, 3, 2), 50000, "OPENING MONEY"),
            txn("A", "src-a", "a2", D(2026, 3, 10), -2000, "TO B", internal=True),
        )
        land(store, "d-b", txn("B", "src-b", "b1", D(2026, 3, 10), 2000, "FROM A", internal=True))
        land(store, "d-c", txn("C", "src-c", "c1", D(2026, 3, 10), 2000, "FROM A", internal=True))
        pair_transfers_across_store(store)
        stated(store, ("2026-03-02", "500.00"), ("2026-03-12", "480.00"), account="A")
        protect(store, "2026-03-12", account="A")
        assert recheck(store, now=T1)[0].intact

        land(store, "d-b2", txn("B", "src-b", "b1", D(2026, 3, 10), 2100, "FROM A", internal=True))
        pair_transfers_across_store(store)

        assert says(store, "A") == ("1 leg's partner changed (now in C)",)

    def test_Recheck_WhenTheSpanChanged_RecordsTheBreakOnceAndKeepsItBroken(self, store):
        self.broken(store, lambda: add_r6(store))

        assert not recheck(store, now=T1)[0].intact
        assert not recheck(store, now=T2)[0].intact

        record = store.protection_record(ACCOUNT)
        assert record is not None and record["broken_at"] == T1.isoformat()
        assert [e["event"] for e in store.protection_events(ACCOUNT)] == ["pressed", "broken"]
        assert view(store).state == "broken"

    def test_Broken_WhenTheCauseIsFixed_HealsByItselfAndSaysSo(self, store):
        self.broken(store, lambda: amend_r3(store))
        recheck(store, now=T1)

        land(store, "d-restore", txn(ACCOUNT, "src-a", "r3", D(2026, 3, 10), -2000, "RAIL"))
        assert recheck(store, now=T2)[0].intact

        found = view(store)
        assert found.state == "intact"
        assert (found.broken_on, found.healed_on) == (D(2026, 4, 2), D(2026, 4, 3))
        assert [e["event"] for e in store.protection_events(ACCOUNT)] == [
            "pressed", "broken", "healed",
        ]

    def test_Accept_WhenTheSpanChanged_RecordsTheNewStateBesideTheOriginal(self, store):
        self.broken(store, lambda: amend_r3(store))
        recheck(store, now=T1)

        accept(store, ACCOUNT, now=T2)

        found = view(store)
        assert found.state == "intact" and found.accepted_on == D(2026, 4, 3)
        events = store.protection_events(ACCOUNT)
        assert [e["event"] for e in events] == ["pressed", "broken", "accepted"]
        assert "1 row's size changed" in events[-1]["detail"]
        assert broken_protections(store) == []

    def test_Accept_WhenNothingChanged_IsRefused(self, store):
        met_account(store)
        protect(store, "2026-03-10")

        with pytest.raises(ProtectionRefused):
            accept(store, ACCOUNT, now=T1)

    def test_Press_WhenTheProtectionIsBroken_IsRefusedUntilItIsAcceptedOrFixed(self, store):
        self.broken(store, lambda: add_r6(store))

        with pytest.raises(ProtectionRefused):
            protect(store, "2026-03-20", now=T1)


class TestEarlierHistoryIsNotABreak:
    """Rows dated before the span arrive, with an older known balance of their own.

    The span through 03-10 starts 03-02 from an opening of 91,250 at the end of 03-01. An older row
    of -700 (02-20) is added. A known balance of 912.50 at the end of 02-28 puts the account at
    91,250 at the end of 03-01, which fits; one of 900.00 puts it at 90,000, which does not.
    """

    def older(self, store: Store, figure: str) -> None:
        met_account(store)
        protect(store, "2026-03-10")
        land(store, "d-older", txn(ACCOUNT, "src-a", "r0", D(2026, 2, 20), -700, "OLDER"))
        stated(store, ("2026-02-28", figure))

    def test_EarlierRows_WhenTheyArriveAtTheVerifiedOpening_AreReportedAsFitting(self, store):
        self.older(store, "912.50")

        found = view(store)

        assert found.state == "intact"
        assert found.earlier_rows == 1 and found.earlier_fits is True and found.earlier_said == ""

    def test_EarlierRows_WhenTheyArriveAtAnotherBalance_AreAFaultOfTheNewDataAndNotABreak(
        self, store
    ):
        self.older(store, "900.00")

        found = view(store)

        assert found.state == "intact", "the protected rows themselves are unchanged"
        assert found.earlier_fits is False
        assert found.earlier_said == (
            "1 row dated before the protected span arrives at a different balance from the one "
            "it was verified from."
        )
        assert broken_protections(store) == []

    def test_EarlierRows_AreNotInTheFingerprint(self, store):
        self.older(store, "900.00")

        record = store.protection_record(ACCOUNT)
        assert record is not None and check_span(store, record).intact


class TestARebuildThatChangesNothing:
    STARLING = (
        b"Date,Counter Party,Reference,Type,Amount (GBP),Balance (GBP)\n"
        b"02/03/2026,CAFE,CAFE ONE,CARD,-12.50,0\n"
        b"05/03/2026,EMPLOYER,PAYMENT IN,FASTER,100.00,0\n"
        b"10/03/2026,RAIL,RAIL FARE,CARD,-20.00,0\n"
    )
    MONZO = (
        b"Transaction ID,Date,Time,Type,Name,Description,Amount,Currency\n"
        b"tx_1,02/03/2026,09:15:00,Card,CAFE,CAFE ONE,-12.50,GBP\n"
        b"tx_2,05/03/2026,09:00:00,Faster,EMPLOYER,PAYMENT IN,100.00,GBP\n"
        b"tx_3,10/03/2026,18:00:00,Card,RAIL,RAIL FARE,-20.00,GBP\n"
    )

    @pytest.mark.parametrize("first", ["starling", "monzo"])
    def test_Rebuild_WhenNothingChanges_LeavesTheProtectionIntactInEitherArrivalOrder(
        self, tmp_path, first
    ):
        """Two exports list the same three payments; they land in each order in turn.
        Opening 1,000.00 - 87.50 = 912.50 at 03-05 predicts 980.00 at 03-10."""
        files = {"starling": self.STARLING, "monzo": self.MONZO}
        order = [first, "monzo" if first == "starling" else "starling"]
        with Store(tmp_path / f"{first}.sqlite3") as store:
            for name in order:
                path = tmp_path / f"{first}-{name}.csv"
                path.write_bytes(files[name])
                import_file(store, path, account_id=ACCOUNT)
            stated(store, ("2026-03-05", "1000.00"), ("2026-03-10", "980.00"))
            protect(store, "2026-03-10")
            before = store.protection_record(ACCOUNT)
            assert before is not None

            rebuild_from_raw(store)

            after = store.protection_record(ACCOUNT)
            assert after is not None, "a rebuild must not wipe a declared protection"
            assert after["fingerprint"] == before["fingerprint"]
            assert after["broken_at"] is None
            assert broken_protections(store) == []
            assert [e["event"] for e in store.protection_events(ACCOUNT)] == ["pressed"]

    def test_Rebuild_WhenItLosesTheProtectedRows_CompletesAndRecordsTheBreak(self, store):
        """Rows landed without an artefact are not in layer 0, so a rebuild cannot reproduce them:
        the rebuild still completes and produces what the rules say, and the protection says so."""
        met_account(store)
        protect(store, "2026-03-10")

        rebuild_from_raw(store)

        assert store.transactions_for_account(ACCOUNT) == []
        found = view(store)
        assert found.state == "broken"
        assert found.changes == ("3 rows gone from it (dated 2026-03-02, 2026-03-05, 2026-03-10)",)
        assert store.protection_record(ACCOUNT) is not None


class TestABrokenProtectionIsLoud:
    NOW = datetime(2026, 4, 5, 12, 0, tzinfo=UTC)

    def prepare(self, tmp_path, *, change: bool):
        db = tmp_path / "loud.sqlite3"
        with Store(db) as store:
            met_account(store)
            protect(store, "2026-03-10")
            if change:
                add_r6(store)
        return db

    def test_Alert_WhenAProtectedSpanChanged_HasAFindingNamingTheAccountAndTheChange(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.setenv("OBDI_CONNECTION_STORE", str(tmp_path / "none.json"))
        db = self.prepare(tmp_path, change=True)

        findings = collect_alert_findings(db, now=self.NOW)

        found = [f for f in findings if f.key.startswith("protection-broken:")]
        assert [f.key for f in found] == [f"protection-broken:{ACCOUNT}"]
        assert "1 row added to the span (dated 2026-03-07)" in found[0].message

    def test_Alert_WhenNothingInAProtectedSpanChanged_HasNoSuchFinding(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.setenv("OBDI_CONNECTION_STORE", str(tmp_path / "none.json"))
        db = self.prepare(tmp_path, change=False)

        findings = collect_alert_findings(db, now=self.NOW)

        assert [f for f in findings if f.key.startswith("protection-broken")] == []

    def test_Overview_WhenAProtectedSpanChanged_ShowsItAtTheTopSeverityAndLinksTheLedger(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.setenv("OBDI_CONNECTION_STORE", str(tmp_path / "none.json"))
        db = self.prepare(tmp_path, change=True)
        with Store(db) as store:
            overview = build_overview(
                store,
                now=self.NOW,
                findings=lambda: collect_alert_findings(db, now=self.NOW),
                canonical_for_ref=lambda ref: ref,
                watched=(),
                labels={},
                actual_bound=None,
                rebuild_status={},
            )

        items = [i for i in overview.items if i.kind == "protection-broken"]
        assert len(items) == 1
        assert items[0].severity == NOW_BAND
        assert items[0].accounts == (ACCOUNT,)
        assert items[0].href == f"/ledger?ref={ACCOUNT}"
        assert overview.items[0].kind == "protection-broken", "it sorts above every other item"


class TestTheSchemaUpgrade:
    def test_Store_OpenedAtTheVersionBeforeTheTables_GrowsThemOnOpenAndKeepsWhatWasThere(
        self, tmp_path
    ):
        path = tmp_path / "old.sqlite3"
        with Store(path) as fresh:
            met_account(fresh)
        connection = sqlite3.connect(path)
        connection.execute("DROP TABLE protections")
        connection.execute("DROP TABLE protection_history")
        connection.execute(
            "UPDATE obdi_meta SET value = ? WHERE key = 'schema_version'",
            (str(SCHEMA_VERSION - 1),),
        )
        connection.commit()
        connection.close()

        with Store(path) as reopened:
            assert reopened.protection_records() == []
            assert len(reopened.transactions_for_account(ACCOUNT)) == 5
            protect(reopened, "2026-03-10")
            assert reopened.protection_record(ACCOUNT) is not None

    def test_Store_Fresh_HasTheTablesAtTheCurrentVersion(self, store):
        assert store.protection_records() == []
        assert store.protection_events(ACCOUNT) == []
        row = store.connection.execute(
            "SELECT value FROM obdi_meta WHERE key = 'schema_version'"
        ).fetchone()
        assert row["value"] == str(SCHEMA_VERSION)


class TestTheLedgerReadsTheProtection:
    def test_Ledger_WhenAskedForProtection_CarriesTheViewAndLeavesOtherLedgersAlone(self, store):
        met_account(store)
        protect(store, "2026-03-10")

        plain = build_ledger(store, ACCOUNT, "2026-03", bound=False)
        with_it = build_ledger(store, ACCOUNT, "2026-03", bound=False, with_protection=True)

        assert plain.protection is None
        assert with_it.protection is not None and with_it.protection.state == "intact"
