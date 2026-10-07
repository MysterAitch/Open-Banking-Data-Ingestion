"""Accounts tracked by their stated balances alone: a mortgage at another bank.

With no feed there is nothing to check a stated balance against, so a later one
is FOLLOWED, and the difference between two consecutive ones is a movement the
account carries as an "unitemised change" dated at the later balance.

Every expectation was worked out from the scenario before the first run. A
liability is negative, so a mortgage is stated as a minus figure and a payment
towards nil is a movement IN. In minor units:

    stated  2026-01-31   -200,000.00   -20,000,000
    stated  2026-02-28   -199,200.00   -19,920,000    change vs Jan  +80,000  (+800.00)
    stated  2026-03-31   -198,000.00   -19,800,000    change vs Feb  +120,000 (+1,200.00)

so with no rows between them there are two changes, +80,000 dated 2026-02-28
and +120,000 dated 2026-03-31, the opening is -20,000,000 at the end of
2026-01-31, and the balance at each stated date is the stated figure.
"""

from __future__ import annotations

import json
import re
import threading
from datetime import UTC, date, datetime
from http.server import HTTPServer

import httpx
import pytest

from obdi.actual_push import build_envelope, transactions_to_push
from obdi.cli import build_web_config
from obdi.ingest.accounts import BALANCE_ONLY_KIND, AccountRecord, AccountRef
from obdi.ingest.rebuild import rebuild_from_raw
from obdi.ingest.store import Store
from obdi.ingest.typed_transactions import record_typed_transaction
from obdi.ledger import running_balance
from obdi.position import read_position
from obdi.replay import ActualAccountBinding, build_payload
from obdi.verify.balance_anchors import (
    STATED,
    Anchor,
    derive_unitemised,
    effective_opening,
    record_stated_anchor,
    remove_stated_anchor,
)
from obdi.web import AuthorisationSession, ConnectionHandler

MORTGAGE = "mortgage"
D = date
TODAY = D(2026, 4, 15)
NOW = datetime(2026, 4, 15, 9, 0, tzinfo=UTC)
PAYLOAD_ID = re.compile(r"^[0-9a-f]{64}:\d+$")

JAN, FEB, MAR = "2026-01-31", "2026-02-28", "2026-03-31"


def declare(store: Store, ref: str = MORTGAGE, kind: str = BALANCE_ONLY_KIND) -> None:
    store.declare_account(AccountRecord(ref=AccountRef(ref), kind=kind, label=ref.title()))


def state(store: Store, ref: str = MORTGAGE) -> None:
    for day, figure in ((JAN, "-200000.00"), (FEB, "-199200.00"), (MAR, "-198000.00")):
        record_stated_anchor(store, ref, day, figure, today=TODAY)


def changes(store: Store, ref: str = MORTGAGE) -> list[tuple[str, int]]:
    return [
        (t.value_date.isoformat(), t.amount_minor)
        for t in sorted(effective_opening(store, ref).unitemised, key=lambda t: t.value_date)
    ]


def type_in(store: Store, day: str, figure: str, entry: str, direction: str = "in") -> None:
    record_typed_transaction(
        store, MORTGAGE, day, direction, figure, "Overpayment",
        today=TODAY, now=NOW, entry_id=entry,
    )


def balance_at(store: Store, day: date, ref: str = MORTGAGE) -> int:
    opening = effective_opening(store, ref)
    assert opening.opening_minor is not None
    rows = [*store.transactions_for_account(ref), *opening.unitemised]
    return running_balance(opening.opening_minor, rows, day)


@pytest.fixture
def store(tmp_path):
    with Store(tmp_path / "balance-only.sqlite3") as opened:
        declare(opened)
        yield opened


class TestThreeStatedBalances:
    def test_UnitemisedChanges_WhenNoRowsBetweenBalances_AreTheDifferencesWithTheRightSigns(
        self, store
    ):
        state(store)

        assert changes(store) == [(FEB, 80_000), (MAR, 120_000)]

    def test_Balance_AtEachStatedDate_IsTheStatedBalance(self, store):
        state(store)

        assert balance_at(store, D(2026, 1, 31)) == -20_000_000
        assert balance_at(store, D(2026, 2, 28)) == -19_920_000
        assert balance_at(store, D(2026, 3, 31)) == -19_800_000

    def test_Borrowing_WhenTheBalanceMovesFurtherFromNil_IsAnOutwardChange(self, store):
        state(store)
        record_stated_anchor(store, MORTGAGE, "2026-04-14", "-199000.00", today=TODAY)

        assert changes(store)[-1] == ("2026-04-14", -100_000)

    def test_Opening_IsTheFirstStatedBalanceAndNoLaterOneIsACheck(self, store):
        state(store)

        opening = effective_opening(store, MORTGAGE)

        assert (opening.opening_minor, opening.as_at) == (-20_000_000, D(2026, 1, 31))
        assert opening.balance_only
        assert opening.differing == []
        assert [r.agrees for r in opening.readings] == [None, True, True]

    def test_Position_CountsTheAccountFromItsFirstStatedBalanceAndTheMonthSeriesMoves(
        self, store
    ):
        state(store)

        position = read_position(store, today=TODAY)

        counted = {a.ref: a for g in position.groups for a in g.accounts}
        assert counted[MORTGAGE].balance.minor == -19_800_000
        series = {p.month: p.net_worth.minor for p in position.history}
        assert series["2026-01"] == -20_000_000
        assert series["2026-02"] == -19_920_000
        assert series["2026-03"] == -19_800_000
        assert (counted[MORTGAGE].checks_agree, counted[MORTGAGE].checks_differ) == (0, 0)

    def test_Position_WhenOnlyOneBalanceIsStated_CountsItFromThatBalance(self, store):
        record_stated_anchor(store, MORTGAGE, FEB, "-199200.00", today=TODAY)

        position = read_position(store, today=TODAY)

        assert position.accounts_counted == 1
        assert {p.month for p in position.history if p.included} >= {"2026-02", "2026-03"}

    def test_Position_WhenNoBalanceIsStated_DoesNotCountTheAccount(self, store):
        position = read_position(store, today=TODAY)

        assert (position.accounts_counted, position.accounts_uncounted) == (0, 1)


class TestRowsBetweenStatedBalances:
    def test_TypedRow_WhenBetweenTwoBalances_LeavesOnlyTheRemainder(self, store):
        state(store)
        type_in(store, "2026-02-10", "700.00", "a1a1a1a1a1a1a1a1")

        assert changes(store) == [(FEB, 10_000), (MAR, 120_000)]
        assert balance_at(store, D(2026, 2, 28)) == -19_920_000

    def test_TypedRows_WhenTheyExplainTheWholeDifference_LeaveNoChange(self, store):
        state(store)
        type_in(store, "2026-02-15", "800.00", "b2b2b2b2b2b2b2b2")

        assert changes(store) == [(MAR, 120_000)]

    def test_Row_DatedOnTheLaterBalanceDay_IsInThatBalanceButOneOnTheEarlierIsNot(self, store):
        state(store)
        type_in(store, JAN, "300.00", "c3c3c3c3c3c3c3c3")
        type_in(store, FEB, "500.00", "d4d4d4d4d4d4d4d4")

        assert changes(store) == [(FEB, 30_000), (MAR, 120_000)]

    def test_Row_AfterTheLastBalance_IsCountedAndChangesNothingDerived(self, store):
        state(store)
        record_typed_transaction(
            store, MORTGAGE, "2026-04-02", "in", "400.00", "Payment",
            today=TODAY, now=NOW, entry_id="e5e5e5e5e5e5e5e5",
        )

        assert changes(store) == [(FEB, 80_000), (MAR, 120_000)]
        assert balance_at(store, D(2026, 4, 15)) == -19_800_000 + 40_000


class TestChangingStatedBalances:
    def test_RemovingTheMiddleBalance_LeavesOneChangeSpanningBoth(self, store):
        state(store)

        remove_stated_anchor(store, MORTGAGE, FEB)

        assert changes(store) == [(MAR, 200_000)]

    def test_RestatingABalance_ChangesTheDerivedChangesWithoutAnythingBeingStored(self, store):
        state(store)
        stored_before = store.connection.execute("SELECT COUNT(*) FROM transactions").fetchone()[0]

        record_stated_anchor(store, MORTGAGE, MAR, "-197900.00", today=TODAY)

        assert changes(store) == [(FEB, 80_000), (MAR, 130_000)]
        assert store.connection.execute("SELECT COUNT(*) FROM transactions").fetchone()[0] == (
            stored_before
        )

    def test_Rebuild_ChangesNothingBecauseNothingDerivedWasStored(self, store):
        state(store)
        type_in(store, "2026-02-10", "700.00", "a1a1a1a1a1a1a1a1")
        before = (changes(store), balance_at(store, D(2026, 3, 31)))

        rebuild_from_raw(store)

        assert (changes(store), balance_at(store, D(2026, 3, 31))) == before


class TestAnOrdinaryAccountKeepsToday:
    def test_LaterStatedBalance_WhenTheAccountIsOrdinary_IsACheckAndFails(self, store):
        declare(store, "current", "current-account")
        state(store, "current")

        opening = effective_opening(store, "current")

        assert not opening.balance_only
        assert opening.unitemised == ()
        assert [(r.difference_minor, r.agrees) for r in opening.readings[1:]] == [
            (80_000, False),
            (200_000, False),
        ]

    def test_LaterStatedBalance_WhenTheAccountIsNotDeclaredAtAll_IsACheck(self, store):
        assert effective_opening(store, "undeclared-account").balance_only is False

    def test_KindIsMatchedLoosely_ButOnlyThatKindChangesTheBehaviour(self, store):
        declare(store, "loose", "  Balance-Only ")
        declare(store, "near", "balance-only-ish")
        state(store, "loose")
        state(store, "near")

        assert changes(store, "loose") == [(FEB, 80_000), (MAR, 120_000)]
        assert changes(store, "near") == []


class TestPureDerivation:
    def test_Derivation_CountsPendingRowsAsAPersonWouldAndIgnoresVoidOnes(self):
        """A person stating a balance means the account as they see it, pending included."""
        from obdi.core.models import Transaction, TransactionStatus

        anchors = [Anchor(D(2026, 2, 1), 1000, STATED), Anchor(D(2026, 3, 1), 1500, STATED)]

        def row(status: TransactionStatus) -> Transaction:
            return Transaction(
                account_id="a", amount_minor=200, value_date=D(2026, 2, 10),
                booking_date=D(2026, 2, 10), description="x", source="s", status=status,
            )

        pending = derive_unitemised("a", anchors, [row(TransactionStatus.PENDING)])
        void = derive_unitemised("a", anchors, [row(TransactionStatus.VOID)])

        assert [t.amount_minor for t in pending] == [300]
        assert [t.amount_minor for t in void] == [500], "a void row is history, not money"

    def test_Derivation_WhenOnlyOneBalanceExists_DerivesNothing(self):
        assert derive_unitemised("a", [Anchor(D(2026, 2, 1), 1000, STATED)], []) == ()


BINDING = [ActualAccountBinding(MORTGAGE, "act-mortgage")]


def asked_to_create(envelope: dict[str, object]) -> list[dict[str, str]]:
    provision = envelope["provision"]
    assert isinstance(provision, list)
    return provision


class TestADeclaredAccountNothingHasCreatedInActualYet:
    """A declared account is one a person named, so a push asks for it to be created.

    An account reached Actual only by having a row to send or a binding made by hand. A
    declared account whose balance is stated and never itemised - cash, a mortgage at another
    bank - has neither until its second stated balance, and one of an ordinary kind never has,
    so it stayed out of the budget and the budget's net worth stayed short by its balance.
    """

    def test_Envelope_ForADeclaredAccountWithOneKnownBalanceAndNoRows_AsksForItToBeCreated(
        self, store
    ):
        record_stated_anchor(store, MORTGAGE, JAN, "-200000.00", today=TODAY)

        envelope = build_envelope(store, [], {})

        assert asked_to_create(envelope) == [{"canonical_id": MORTGAGE, "label": "Mortgage"}]

    def test_Envelope_ForADeclaredAccountWithNothingStatedYet_StillAsksForItToBeCreated(
        self, store
    ):
        envelope = build_envelope(store, [], {})

        assert asked_to_create(envelope) == [{"canonical_id": MORTGAGE, "label": "Mortgage"}]

    def test_Envelope_ForADeclaredAccountOfAnOrdinaryKindWithNoRows_AsksForItToBeCreated(
        self, tmp_path
    ):
        with Store(tmp_path / "ordinary.sqlite3") as opened:
            declare(opened, "hsbc-mortgage", "mortgage")
            record_stated_anchor(opened, "hsbc-mortgage", JAN, "-200000.00", today=TODAY)

            envelope = build_envelope(opened, [], {})

        assert asked_to_create(envelope) == [
            {"canonical_id": "hsbc-mortgage", "label": "Hsbc-Mortgage"}
        ]

    def test_Envelope_WhenADisplayLabelIsKnownForIt_UsesThatLabelOverTheDeclaredOne(self, store):
        envelope = build_envelope(store, [], {MORTGAGE: "House loan"})

        assert asked_to_create(envelope) == [{"canonical_id": MORTGAGE, "label": "House loan"}]

    def test_Envelope_ForADeclaredAccountAlreadyInActual_DoesNotAskAgain(self, store):
        record_stated_anchor(store, MORTGAGE, JAN, "-200000.00", today=TODAY)

        envelope = build_envelope(store, BINDING, {})

        assert asked_to_create(envelope) == []

    def test_Envelope_OnceTheAccountIsInActual_CarriesItsKnownBalanceAsTheOpening(self, store):
        record_stated_anchor(store, MORTGAGE, JAN, "-200000.00", today=TODAY)

        envelope = build_envelope(store, BINDING, {})

        openings = envelope["opening_balances"]
        assert isinstance(openings, list)
        assert [(o["account"], o["date"], o["amount"]) for o in openings] == [
            ("act-mortgage", JAN, -20_000_000)
        ]

    def test_Envelope_ForAnArchivedDeclaredAccountWithNoRows_DoesNotAskForIt(self, tmp_path):
        with Store(tmp_path / "archived.sqlite3") as opened:
            opened.declare_account(
                AccountRecord(
                    ref=AccountRef("old-tin"), kind=BALANCE_ONLY_KIND, label="Old tin",
                    closed=D(2025, 12, 31),
                )
            )

            envelope = build_envelope(opened, [], {})

        assert asked_to_create(envelope) == []


class TestWhatReachesActual:
    def payload(self, store: Store) -> list[dict[str, object]]:
        from obdi.actual_push import opening_balances

        openings = opening_balances(store, BINDING)
        return build_payload(transactions_to_push(store), BINDING, openings)["act-mortgage"]

    def test_Push_CarriesTheOpeningAndEachChangeSoActualTracksTheStatedBalances(self, store):
        state(store)

        rows = self.payload(store)

        by_date = {str(r["date"]): r for r in rows}
        assert by_date[JAN]["amount"] == -20_000_000
        assert by_date[FEB]["amount"] == 80_000
        assert by_date[MAR]["amount"] == 120_000
        assert sum(int(str(r["amount"])) for r in rows) == -19_800_000
        change = by_date[MAR]
        assert PAYLOAD_ID.match(str(change["imported_id"])), (
            "payment-shaped, so the applier already recognises it as ours"
        )
        assert change["payee_name"] == "Unitemised change"
        assert "not itemised" in str(change["notes"])

    def test_Push_WhenBuiltAgainOrAfterARebuild_UsesTheSameImportedIds(self, store):
        state(store)
        first = sorted(str(r["imported_id"]) for r in self.payload(store))

        rebuild_from_raw(store)

        assert sorted(str(r["imported_id"]) for r in self.payload(store)) == first

    def test_Push_WhenABalanceIsRestated_ReplacesOnlyTheChangeItAltered(self, store):
        state(store)
        before = {str(r["date"]): str(r["imported_id"]) for r in self.payload(store)}

        record_stated_anchor(store, MORTGAGE, MAR, "-197900.00", today=TODAY)

        after = {str(r["date"]): str(r["imported_id"]) for r in self.payload(store)}
        assert after[JAN] == before[JAN]
        assert after[FEB] == before[FEB]
        assert after[MAR] != before[MAR], "a new figure is a new row; the old one is an orphan"

    def test_Envelope_ForABoundAccount_ListsTheChangesAndNeedsNoNewEnvelopeVersion(
        self, store
    ):
        state(store)
        from obdi.actual_push import ENVELOPE_VERSION

        envelope = build_envelope(store, BINDING, {MORTGAGE: "Mortgage"})

        accounts = envelope["accounts"]
        assert isinstance(accounts, dict) and len(accounts["act-mortgage"]) == 3
        assert envelope["version"] == ENVELOPE_VERSION == 3

    def test_Push_WhenTheAccountIsOrdinary_SendsNoDerivedRows(self, store):
        declare(store, "current", "current-account")
        state(store, "current")
        bound = [ActualAccountBinding("current", "act-current")]

        rows = build_payload(transactions_to_push(store), bound)

        assert rows == {}


class Lab:
    def __init__(self, base: str, db) -> None:
        self.base = base
        self.db = db

    def ledger(self, ref: str = MORTGAGE, **params: str) -> httpx.Response:
        return httpx.get(f"{self.base}/ledger", params={"ref": ref, **params}, timeout=20)

    def values(self, ref: str = MORTGAGE, month: str = "") -> httpx.Response:
        return httpx.post(
            f"{self.base}/ledger", data={"ref": ref, "month": month}, timeout=20
        )

    def get(self, path: str) -> httpx.Response:
        return httpx.get(f"{self.base}{path}", timeout=20)


@pytest.fixture
def lab(tmp_path, monkeypatch):
    db = tmp_path / "balance-only-web.sqlite3"
    with Store(db) as opened:
        declare(opened)
        declare(opened, "current", "current-account")
        declare(opened, "tin", "cash")
        state(opened)
        state(opened, "current")
    accounts = tmp_path / "accounts.json"
    accounts.write_text(json.dumps({"actual": []}), encoding="utf-8")
    monkeypatch.setenv("OBDI_CONNECTION_STORE", str(tmp_path / "connections.json"))
    monkeypatch.setenv("OBDI_ACCOUNT_MAP", str(accounts))
    for variable in ("TRUELAYER_CLIENT_ID", "TRUELAYER_CLIENT_SECRET_FILE"):
        monkeypatch.delenv(variable, raising=False)
    config = build_web_config(db)
    assert config is not None
    handler = type(
        "H", (ConnectionHandler,), {"config": config, "session": AuthorisationSession()}
    )
    httpd = HTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        yield Lab(f"http://127.0.0.1:{httpd.server_port}", db)
    finally:
        httpd.shutdown()


class TestThePages:
    FIGURES = ("800.00", "1,200.00", "200,000.00", "199,200.00", "198,000.00", "8,000", "12,000")

    def test_Ledger_Masked_ListsTheChangesByDateAndShowsNoFigure(self, lab):
        page = lab.ledger().text

        assert "<summary>Unitemised changes (" in page
        assert "2026-02-28" in page and "2026-03-31" in page
        assert "since the known balance for the end of 2026-01-31" in page
        assert '<span class="pill pill-quiet">followed</span>' in page
        for figure in self.FIGURES:
            assert figure not in page, figure

    def test_Ledger_WithValuesShown_ListsTheChangesWithTheirFigures(self, lab):
        page = lab.values().text

        assert "in £800.00" in page
        assert "in £1,200.00" in page
        assert "unitemised change" in page

    def test_Ledger_ForABalanceOnlyAccount_SaysWhichWayRoundToTypeALiability(self, lab):
        page = lab.ledger().text

        assert "which a mortgage or any other loan always is" in page
        assert "a payment towards nil" in page

    def test_Ledger_ForAnOrdinaryAccount_StillReportsTheCheckAsDiffering(self, lab):
        page = lab.ledger("current").text

        assert '<span class="pill pill-bad">differs</span>' in page
        assert "Unitemised changes" not in page
        assert "followed" not in page

    def test_Accounts_ExplainsHowToDeclareAnAccountWithNoFeedAndOffersTheKind(self, lab):
        page = lab.get("/accounts").text
        edit = lab.get(f"/edit-account?ref={MORTGAGE}").text

        assert "For an account obdi has no feed for" in page
        assert f"<strong>{BALANCE_ONLY_KIND}</strong>" in page
        # Kind is a choice now, each kind said in a line; the mortgage already has this one.
        assert f'<option value="{BALANCE_ONLY_KIND}" selected>' in edit
        assert "Tracked by the balances you state alone" in edit

    def test_ActualPage_ForADeclaredAccountNotYetInActual_SaysTheNextPushCreatesIt(self, lab):
        page = lab.get("/actual").text

        row = page.split("<strong>Mortgage</strong>")[1].split("</div>")[0]
        assert "creates on next push" in row
        assert "no transactions yet" in row

    def test_Position_ForAFeedlessAccountWithNoBalance_PointsToBothWaysToCountIt(self, lab):
        page = lab.get("/position").text

        assert "may be an account obdi has no feed for" in page
        assert f"declare its kind as {BALANCE_ONLY_KIND}" in page
        assert "Mortgage" in page
