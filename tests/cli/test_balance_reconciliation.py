"""Does the sum of what the store holds agree with what the bank says the balance was?

Every scenario fixes its answer before the first run, in the ledger below.
The bank is simulated: a ledger of signed pence is turned into TrueLayer
records whose running balances are what a bank would state, and the store is
then built through the application's own doors (landing artefacts and
replaying them). A fault is planted by omitting a record from the landed
artefact while the surrounding balances still reflect it, which is exactly
what a row lost between the bank and the store looks like.

The ledger, with the bank's balance after each row (starting at 1000.00):

    2026-03-02  a  -12.34  98766    b   -5.00  98266
    2026-03-03  c +250.00 123266
    2026-03-05  d  -20.00 121266    e   -7.77 120489
    2026-03-06  f   -1.00 120389    g   -2.00 120189    h  -3.00 119889

so the opening balance of the account is 100000 (2026-03-02) and the latest
closing balance is 119889 (2026-03-06).
"""

from __future__ import annotations

import json
import threading
from datetime import date
from http.server import HTTPServer

import httpx
import pytest

from obdi.core.models import SourceTier, Transaction, TransactionStatus
from obdi.ingest.connections import ConnectionStore
from obdi.ingest.identity import content_key
from obdi.ingest.pipeline import reconcile_batch
from obdi.ingest.providers import starling, truelayer
from obdi.ingest.rebuild import rebuild_from_raw
from obdi.ingest.store import Store
from obdi.pages.web import AuthorisationSession, ConnectionHandler, WebConfig
from obdi.verify.balance_reconciliation import (
    _chain_ends,
    balance_reconciliation,
)

ACCOUNT = "truelayer:tl-1"

#: What the real web hook tells a reader of the page, which has a button for it.
PAGE_HINT = "press the Show values button on this page"

LEDGER: list[tuple[str, date, int, str]] = [
    ("a", date(2026, 3, 2), -1234, "Alpha Bakery"),
    ("b", date(2026, 3, 2), -500, "Bravo Books"),
    ("c", date(2026, 3, 3), 25000, "Charlie Payroll"),
    ("d", date(2026, 3, 5), -2000, "Delta Rail"),
    ("e", date(2026, 3, 5), -777, "Echo Cafe"),
    ("f", date(2026, 3, 6), -100, "Foxtrot Kiosk"),
    ("g", date(2026, 3, 6), -200, "Golf Garage"),
    ("h", date(2026, 3, 6), -300, "Hotel Hardware"),
]


def _record(
    tid: str, day: date, minor: int, description: str, balance_minor: int, hour: int
) -> dict:
    return {
        "transaction_id": f"id-{tid}",
        "normalised_provider_transaction_id": f"norm-{tid}",
        "timestamp": f"{day.isoformat()}T{hour:02}:00:00Z",
        "description": description,
        "amount": minor / 100,
        "currency": "GBP",
        "transaction_type": "CREDIT" if minor > 0 else "DEBIT",
        "running_balance": {"amount": balance_minor / 100, "currency": "GBP"},
    }


def _bank_records(
    ledger: list[tuple[str, date, int, str]],
    *,
    start: int = 100000,
    omit: tuple[str, ...] = (),
    reverse_within_day: bool = False,
) -> list[dict]:
    """The records a bank would return: balances reflect EVERY ledger row,
    including any the caller then omits from what is landed."""
    records = []
    balance = start
    for position, (tid, day, minor, description) in enumerate(ledger):
        balance += minor
        records.append((tid, _record(tid, day, minor, description, balance, 9 + position % 10)))
    if reverse_within_day:
        ordered: list[tuple[str, dict]] = []
        for day in sorted({r[1]["timestamp"][:10] for r in records}):
            ordered.extend(reversed([r for r in records if r[1]["timestamp"][:10] == day]))
        records = ordered
    return [record for tid, record in records if tid not in omit]


def _land(store: Store, records: list[dict], *, kind: str = "booked", account: str = "tl-1"):
    body = json.dumps({"results": records, "status": "Succeeded"}).encode()
    store.land_artefact(truelayer.artefact_for(body, account_id=account, kind=kind))


def _built(store: Store, **options) -> None:
    _land(store, _bank_records(LEDGER, **options))
    rebuild_from_raw(store)


def _account(store: Store, account_id: str = ACCOUNT):
    report = balance_reconciliation(store)
    return next(a for a in report.accounts if a.account_id == account_id)


def _export_row(
    amount: int, day: date, description: str, *, account: str = ACCOUNT
) -> Transaction:
    """A row a file export sighted: no provider id, no running balance."""
    return Transaction(
        account_id=account,
        amount_minor=amount,
        currency="GBP",
        description=description,
        value_date=day,
        booking_date=day,
        source="qif",
        source_id=None,
        content_key=content_key(amount_minor=amount, value_date=day, description=description),
        tier=SourceTier.SYNTHETIC,
        status=TransactionStatus.BOOKED,
    )


class TestACleanAccount:
    def test_Report_WhenEveryRowIsHeld_EveryDayPassesAndTheOpeningBalanceIsKnown(
        self, tmp_path
    ):
        """Four days, all known. No break, no mismatch. Opening 100000 on
        2026-03-02, latest closing 119889 on 2026-03-06."""
        with Store(tmp_path / "s.sqlite3") as store:
            _built(store)
            account = _account(store)

        assert [d.day for d in account.known_days] == [
            date(2026, 3, 2),
            date(2026, 3, 3),
            date(2026, 3, 5),
            date(2026, 3, 6),
        ]
        assert account.ambiguous_days == []
        assert account.continuity_breaks == []
        assert account.day_mismatches == []
        assert account.opening is not None and account.opening.opening_minor == 100000
        assert account.opening.day == date(2026, 3, 2)
        assert account.latest is not None and account.latest.closing_minor == 119889
        assert account.latest.day == date(2026, 3, 6)

    def test_Report_WhenSameDayRowsAreReturnedInEitherOrder_GivesTheSameAnswer(
        self, tmp_path
    ):
        """The point of the method: no order is assumed, so reversing every
        day's rows changes nothing."""
        with Store(tmp_path / "a.sqlite3") as forward, Store(tmp_path / "b.sqlite3") as backward:
            _built(forward)
            _built(backward, reverse_within_day=True)
            first = _account(forward)
            second = _account(backward)

        assert first.days == second.days
        assert [(d.opening_minor, d.closing_minor) for d in second.days] == [
            (100000, 98266),
            (98266, 123266),
            (123266, 120489),
            (120489, 119889),
        ]

    def test_Report_WhenAPaymentAndItsExactReversalShareADayWithAnotherRow_StillFindsBothEnds(
        self, tmp_path
    ):
        """Day 2026-03-09: +5.00 then -5.00 then -1.00, from 119889. The
        repeated figures must cancel as a multiset: opening 119889, closing
        119789. A set difference would lose the opening."""
        ledger = [
            *LEDGER,
            ("i", date(2026, 3, 9), 500, "India Deposit"),
            ("j", date(2026, 3, 9), -500, "Juliet Return"),
            ("l", date(2026, 3, 9), -100, "Lima Lunch"),
        ]
        with Store(tmp_path / "s.sqlite3") as store:
            _land(store, _bank_records(ledger))
            rebuild_from_raw(store)
            account = _account(store)

        last = account.days[-1]
        assert last.day == date(2026, 3, 9)
        assert (last.opening_minor, last.closing_minor) == (119889, 119789)
        assert account.continuity_breaks == []
        assert account.day_mismatches == []


class TestADayThatCannotBeResolved:
    def test_Report_WhenOnlyAPaymentAndItsReversalAreInTheDay_IsAmbiguousButNetsToZero(
        self, tmp_path
    ):
        """+42.00 then -42.00 alone: the balances could have been visited in
        either order, so opening is genuinely undetermined. The day is
        ambiguous, never guessed, yet its movement (zero) is still compared
        with the store's rows (which sum to zero)."""
        ledger = [
            *LEDGER,
            ("i", date(2026, 3, 9), 4200, "India Deposit"),
            ("j", date(2026, 3, 9), -4200, "Juliet Return"),
        ]
        with Store(tmp_path / "s.sqlite3") as store:
            _land(store, _bank_records(ledger))
            rebuild_from_raw(store)
            account = _account(store)

        loop = account.days[-1]
        assert loop.day == date(2026, 3, 9)
        assert loop.opening_minor is None and loop.closing_minor is None
        assert "closed loop" in loop.ambiguity
        assert loop.net_minor == 0
        assert account.ambiguous_days == [loop]
        assert account.continuity_breaks == []
        assert account.day_mismatches == []

    def test_Report_WhenARowIsMissingMidDay_TheDayIsAmbiguousAndNothingIsGuessed(
        self, tmp_path
    ):
        """Row g is lost from the middle of 2026-03-06, leaving two separate
        chains of balances."""
        with Store(tmp_path / "s.sqlite3") as store:
            _built(store, omit=("g",))
            account = _account(store)

        assert [d.day for d in account.ambiguous_days] == [date(2026, 3, 6)]
        assert "2 separate chains" in account.ambiguous_days[0].ambiguity
        assert len(account.known_days) == 3
        assert account.continuity_breaks == []

    def test_ChainEnds_WhenTwoSeparatePiecesLeaveOneEndEach_IsAmbiguous(self):
        """(10,20) is a chain. (50,60),(60,50) is a separate loop. Counting
        ends alone would read this as one chain from 10 to 20."""
        opening, closing, why = _chain_ends([(10, 20), (50, 60), (60, 50)])

        assert (opening, closing) == (None, None)
        assert "2 separate chains" in why

    def test_ChainEnds_WhenOneRecordIsHeldTwice_IsAmbiguousWithTwoOfEachEnd(self):
        """The same pair twice is connected, yet two chains could start at 10."""
        opening, closing, why = _chain_ends([(10, 20), (10, 20)])

        assert (opening, closing) == (None, None)
        assert why == "2 candidate openings and 2 candidate closings"

    def test_ChainEnds_WhenOneChain_NamesBothEnds(self):
        assert _chain_ends([(10, 20), (20, 35)]) == (10, 35, "")

    def test_ChainEnds_WhenTheOnlyMovementIsZero_OpeningEqualsClosing(self):
        assert _chain_ends([(70, 70)]) == (70, 70, "")


class TestAFaultIsLocalised:
    def test_Report_WhenTheFirstRowOfADayIsMissing_TheLinkToTheDayBeforeBreaks(self, tmp_path):
        """Row d is lost, so 2026-03-05 opens at 121266 where 2026-03-03
        closed at 123266: one break, 2000 apart. The day still sums
        correctly (-777 against -777), which is why continuity is a separate
        check."""
        with Store(tmp_path / "s.sqlite3") as store:
            _built(store, omit=("d",))
            account = _account(store)

        assert [(b.previous_day, b.next_day) for b in account.continuity_breaks] == [
            (date(2026, 3, 3), date(2026, 3, 5))
        ]
        assert account.continuity_breaks[0].difference_minor == -2000
        assert account.day_mismatches == []

    def test_Report_WhenTheStoreHoldsARowTheBankDoesNotKnow_ThatDayMismatches(self, tmp_path):
        """A file export adds -3.33 on 2026-03-03. The bank moved +250.00;
        the store now holds +246.67: one mismatch, -333 out, one row without
        a bank balance. Nothing else is touched."""
        with Store(tmp_path / "s.sqlite3") as store:
            _built(store)
            reconcile_batch(
                store,
                [_export_row(-333, date(2026, 3, 3), "Zulu Extra")],
                digest="d-export",
            )
            account = _account(store)

        assert [
            (m.day, m.expected_minor, m.held_minor, m.rows_without_balance)
            for m in account.day_mismatches
        ] == [(date(2026, 3, 3), 25000, 24667, 1)]
        assert account.day_mismatches[0].difference_minor == -333
        assert account.continuity_breaks == []

    def test_Report_WhenAFileExportSightsAPaymentTheBankAlreadyReported_NothingMismatches(
        self, tmp_path
    ):
        """The export matches row c after the bank's record. The row keeps
        the bank's record and the day agrees: 8 rows, 4 known days."""
        with Store(tmp_path / "s.sqlite3") as store:
            _built(store)
            reconcile_batch(
                store,
                [_export_row(25000, date(2026, 3, 3), "Charlie Payroll")],
                digest="d-export",
            )
            rows = store.all_transactions()
            account = _account(store)

        assert len(rows) == 8
        assert account.day_mismatches == []
        assert account.continuity_breaks == []
        assert len(account.known_days) == 4

    def test_Report_WhenAFileExportSightedThePaymentBeforeTheBank_TheDayIsStillChecked(
        self, tmp_path
    ):
        """The export creates row c, so the merged row's own record holds no
        running balance, and the bank's record arrives second. The sighting
        still names the artefact, so the day is checked from there: 4 known
        days and no break. Without that, 2026-03-03 would drop out and
        2026-03-02 would be linked straight to 2026-03-05 as a false break."""
        from dataclasses import replace

        body = json.dumps({"results": _bank_records(LEDGER), "status": "Succeeded"}).encode()
        artefact = truelayer.artefact_for(body, account_id="tl-1", kind="booked")
        with Store(tmp_path / "s.sqlite3") as store:
            store.land_artefact(artefact)
            reconcile_batch(
                store,
                [_export_row(25000, date(2026, 3, 3), "Charlie Payroll")],
                digest="d-export",
            )
            reconcile_batch(
                store,
                [
                    replace(
                        truelayer.to_transaction(record, account_id=ACCOUNT),
                        artefact_digest=artefact.digest,
                    )
                    for record in _bank_records(LEDGER)
                ],
                digest=artefact.digest,
            )
            rows = store.all_transactions()
            merged = next(t for t in rows if t.value_date == date(2026, 3, 3))
            account = _account(store)

        assert len(rows) == 8
        assert merged.raw == {}
        assert account.day_mismatches == []
        assert account.continuity_breaks == []
        assert len(account.known_days) == 4

    def test_Report_WhenAPendingRowSharesADay_ItIsLeftOutOfTheSumAndCounted(self, tmp_path):
        """A pending -9.99 on 2026-03-06 is not in the bank's booked balance:
        nothing mismatches, and the report says one pending row was left out."""
        pending = {
            "transaction_id": "pend-1",
            "normalised_provider_transaction_id": "norm-pend-1",
            "timestamp": "2026-03-06T12:00:00Z",
            "description": "Kilo Pending",
            "amount": -9.99,
            "currency": "GBP",
            "transaction_type": "DEBIT",
        }
        with Store(tmp_path / "s.sqlite3") as store:
            _built(store)
            _land(store, [pending], kind="pending")
            rebuild_from_raw(store)
            account = _account(store)

        assert account.pending_excluded == 1
        assert account.day_mismatches == []
        assert account.continuity_breaks == []
        assert len(account.known_days) == 4


class TestAccountsThatCannotBeChecked:
    def test_Report_ForAnAccountWithNoRunningBalances_SaysNotCheckable(self, tmp_path):
        feed = {
            "feedItems": [
                {
                    "feedItemUid": "pay-1",
                    "amount": {"currency": "GBP", "minorUnits": 1234},
                    "direction": "OUT",
                    "transactionTime": "2026-03-01T09:15:00.000Z",
                    "source": "MASTER_CARD",
                    "status": "SETTLED",
                    "counterPartyName": "Mike Market",
                }
            ]
        }
        with Store(tmp_path / "s.sqlite3") as store:
            _built(store)
            store.land_artefact(
                starling.artefact_for(
                    json.dumps(feed).encode(),
                    account_id="starling:cat-1",
                    kind="feed",
                    origin="https://api.example.com/feed/account/a/category/cat-1?c=0",
                )
            )
            rebuild_from_raw(store)
            report = balance_reconciliation(store)
            text = report.describe()

        starling_account = next(a for a in report.accounts if a.account_id == "starling:cat-1")
        assert starling_account.not_checkable
        assert "starling:cat-1: cannot be checked" in text
        assert ACCOUNT in text and "clean where it could be checked" in text

    def test_Report_ForACardWhoseBalanceSignIsUnverified_SaysNotCheckable(self, tmp_path):
        card = {
            "transaction_id": "card-1",
            "normalised_provider_transaction_id": "norm-card-1",
            "timestamp": "2026-03-01T09:15:00Z",
            "description": "November Notions",
            "amount": 12.34,
            "currency": "GBP",
            "transaction_type": "DEBIT",
            "running_balance": {"amount": 12.34, "currency": "GBP"},
        }
        with Store(tmp_path / "s.sqlite3") as store:
            _land(store, [card], kind="card-booked", account="card-1")
            rebuild_from_raw(store)
            account = _account(store, "truelayer:card-1")

        assert "card records" in account.not_checkable
        assert account.days == []

    def test_Report_ForAnAccountOfOnlyPendingRows_SaysNotCheckable(self, tmp_path):
        pending = {
            "transaction_id": "pend-1",
            "normalised_provider_transaction_id": "norm-pend-1",
            "timestamp": "2026-03-06T12:00:00Z",
            "description": "Oscar Pending",
            "amount": -9.99,
            "currency": "GBP",
            "transaction_type": "DEBIT",
        }
        with Store(tmp_path / "s.sqlite3") as store:
            _land(store, [pending], kind="pending", account="tl-9")
            rebuild_from_raw(store)
            account = _account(store, "truelayer:tl-9")

        assert "only pending rows" in account.not_checkable

    def test_Report_OnAnEmptyStore_SaysNothingWasComparedAndIsNotAPass(self, tmp_path):
        with Store(tmp_path / "s.sqlite3") as store:
            text = balance_reconciliation(store).describe()

        assert "nothing was compared" in text
        assert "not a pass" in text
        assert "clean" not in text


PRIVATE_START = 8648201
PRIVATE_PAYEE = "Zebra Crossing Cafe"
PRIVATE_EXTRA = -4217


def _private_store(store: Store) -> None:
    """A mismatch on 2026-03-03 (an export row of -42.17) and a break
    after 2026-03-03 (row d omitted), over distinctive figures."""
    ledger = [
        ("a", date(2026, 3, 2), -73913, PRIVATE_PAYEE),
        ("c", date(2026, 3, 3), 31415, "Charlie Payroll"),
        ("d", date(2026, 3, 5), -2718, "Delta Rail"),
        ("e", date(2026, 3, 5), -1618, "Echo Cafe"),
    ]
    _land(store, _bank_records(ledger, start=PRIVATE_START, omit=("d",)))
    rebuild_from_raw(store)
    reconcile_batch(
        store, [_export_row(PRIVATE_EXTRA, date(2026, 3, 3), "Quagga Extra")], digest="d-x"
    )


class TestTheFiguresArePrivate:
    def test_Describe_WhenMasked_ShowsNoBalanceAmountDifferenceOrPayee(self, tmp_path):
        """Opening 8648201 (86482.01), the planted -42.17 and its payee, the
        payee of the first row, and the break's size must all be absent."""
        with Store(tmp_path / "s.sqlite3") as store:
            _private_store(store)
            report = balance_reconciliation(store)
            masked = report.describe()
            unmasked = report.describe(masked=False)

        assert "1 continuity break" in masked and "1 day mismatch" in masked
        for private in (
            "8648201", "86482.01", "4217", "42.17", PRIVATE_PAYEE, "Quagga",
            "73913", "739.13", "31415", "314.15", "1618", "16.18", "2718", "27.18",
        ):
            assert private not in masked, private
        for shown in ("86482.01", "-£42.17", "Unmasked"):
            assert shown in unmasked, shown
        assert PRIVATE_PAYEE not in unmasked
        assert "Masked: account names" in masked

    def test_Describe_WhenMasked_OffersNoWayToUnmaskThatTheCallerDidNotSupply(
        self, tmp_path
    ):
        """The report cannot know whether it is on a page or a command, so with
        no hint it must not name a mechanism. `?values=1` in particular does
        nothing on the page, and a hint that sends the reader to try it is
        worse than none."""
        with Store(tmp_path / "s.sqlite3") as store:
            _private_store(store)
            masked = balance_reconciliation(store).describe()

        header = masked.splitlines()[1]
        assert header.startswith("Masked: account names")
        assert "values=1" not in masked and "--show-values" not in masked
        assert "(" not in header, "no hint was given, so no parenthesis offers one"

    def test_Describe_WhenMaskedWithACallersHint_ShowsExactlyThatHint(self, tmp_path):
        with Store(tmp_path / "s.sqlite3") as store:
            _private_store(store)
            masked = balance_reconciliation(store).describe(unmask_hint="press the button")

        assert masked.splitlines()[1].endswith("appears (press the button)")

    def test_Describe_WhenUnmasked_NeverRepeatsTheHintBecauseTheFiguresAreAlreadyShown(
        self, tmp_path
    ):
        with Store(tmp_path / "s.sqlite3") as store:
            _private_store(store)
            unmasked = balance_reconciliation(store).describe(
                masked=False, unmask_hint="press the button"
            )

        assert "press the button" not in unmasked

    def test_Describe_ByDefault_IsTheMaskedRendering(self, tmp_path):
        with Store(tmp_path / "s.sqlite3") as store:
            _private_store(store)
            report = balance_reconciliation(store)

        assert report.describe() == report.describe(masked=True)
        assert report.describe() != report.describe(masked=False)


def _serve(config: WebConfig) -> HTTPServer:
    handler = type(
        "H", (ConnectionHandler,), {"config": config, "session": AuthorisationSession()}
    )
    httpd = HTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd


def _config(tmp_path, **hooks) -> WebConfig:
    return WebConfig(
        client_id="client-1",
        client_secret="tlcs_live_abcdefghij1234567890",
        redirect_uri="https://obdi.example.com/callback",
        connection_store=ConnectionStore(tmp_path / "c.json"),
        **hooks,
    )


def _get(config: WebConfig, path: str) -> httpx.Response:
    httpd = _serve(config)
    try:
        return httpx.get(f"http://127.0.0.1:{httpd.server_port}{path}")
    finally:
        httpd.shutdown()


def _post(config: WebConfig, path: str, *, origin: str | None = None) -> httpx.Response:
    httpd = _serve(config)
    try:
        return httpx.post(
            f"http://127.0.0.1:{httpd.server_port}{path}",
            headers={"Origin": origin} if origin else {},
        )
    finally:
        httpd.shutdown()


class TestBalanceReconciliationPage:
    def test_Page_ByDefault_AsksForTheMaskedRenderingAndSaysSo(self, tmp_path):
        config = _config(
            tmp_path,
            balance_reconciliation_text=lambda masked: f"example masked={masked}",
        )
        response = _get(config, "/balance-reconciliation")

        assert response.status_code == 200
        assert "example masked=True" in response.text
        assert "masked rendering" in response.text

    def test_Page_WhenTheFiguresArePostedFor_ShowsTheUnmaskedRenderingAndSaysSo(
        self, tmp_path
    ):
        config = _config(
            tmp_path,
            balance_reconciliation_text=lambda masked: f"example masked={masked}",
        )
        response = _post(config, "/balance-reconciliation")

        assert response.status_code == 200
        assert "example masked=False" in response.text
        assert "Showing the real values" in response.text

    def test_Page_WhenTheFiguresAreShown_TellsTheBrowserNotToKeepThem(self, tmp_path):
        config = _config(
            tmp_path,
            balance_reconciliation_text=lambda masked: f"example masked={masked}",
        )

        assert _post(config, "/balance-reconciliation").headers["Cache-Control"] == "no-store"

    @pytest.mark.parametrize("query", ["values=1", "values=yes", "unmask=1", "show=1", "masked=0"])
    def test_Page_FetchedWithAnyQuery_StaysMasked(self, tmp_path, query):
        """No address shows a figure.
        A link can be followed, shared, cached, and read by anything that can
        read a page, so the figures answer only a request made on purpose."""
        config = _config(
            tmp_path,
            balance_reconciliation_text=lambda masked: f"example masked={masked}",
        )
        response = _get(config, f"/balance-reconciliation?{query}")

        assert "example masked=True" in response.text
        assert "example masked=False" not in response.text

    def test_Page_Masked_OffersTheFiguresThroughAFormAndNotALink(self, tmp_path):
        config = _config(
            tmp_path,
            balance_reconciliation_text=lambda masked: f"example masked={masked}",
        )
        page = _get(config, "/balance-reconciliation").text

        assert '<form method="post" action="/balance-reconciliation">' in page
        assert "?values" not in page

    def test_Page_WhenTheFiguresArePostedForFromAnotherSite_IsRefused(self, tmp_path):
        config = _config(
            tmp_path,
            balance_reconciliation_text=lambda masked: f"example masked={masked}",
        )
        response = _post(config, "/balance-reconciliation", origin="https://evil.example")

        assert response.status_code == 403
        assert "example masked=False" not in response.text

    def test_Page_WhenNotWired_IsNotFound(self, tmp_path):
        assert _get(_config(tmp_path), "/balance-reconciliation").status_code == 404

    def test_Page_WhenTheReportFails_SaysSoRatherThanShowingAnEmptyPass(self, tmp_path):
        def broken(masked: bool) -> str:
            raise RuntimeError("the store would not open")

        config = _config(tmp_path, balance_reconciliation_text=broken)
        response = _get(config, "/balance-reconciliation")

        assert response.status_code == 500
        assert "the store would not open" in response.text

    def test_ReportsIndex_LinksToTheReport(self, tmp_path):
        config = _config(tmp_path, balance_reconciliation_text=lambda masked: "report")

        assert 'href="/balance-reconciliation"' in _get(config, "/reports").text


class TestBalanceReconciliationCommand:
    def test_Command_ByDefault_PrintsTheMaskedReportAndExitsZero(
        self, tmp_path, capsys, monkeypatch
    ):
        from obdi import cli

        db = tmp_path / "store.sqlite3"
        with Store(db) as store:
            _private_store(store)
        monkeypatch.setenv("OBDI_DB_PATH", str(db))

        exit_code = cli.main(["balance-reconciliation"])

        printed = capsys.readouterr().out
        assert exit_code == 0
        assert "1 day mismatch" in printed
        assert "86482.01" not in printed and "42.17" not in printed
        # Only the command has a flag, and the page's `?values=1` is gone.
        assert "add --show-values to see them" in printed
        assert "values=1" not in printed

    def test_Command_WithShowValues_PrintsTheFiguresAndStillExitsZero(
        self, tmp_path, capsys, monkeypatch
    ):
        from obdi import cli

        db = tmp_path / "store.sqlite3"
        with Store(db) as store:
            _private_store(store)
        monkeypatch.setenv("OBDI_DB_PATH", str(db))

        exit_code = cli.main(["balance-reconciliation", "--show-values"])

        printed = capsys.readouterr().out
        assert exit_code == 0
        assert "86482.01" in printed and "42.17" in printed

    def test_WebHook_BuiltFromTheRealConfiguration_ReturnsBothRenderings(
        self, tmp_path, monkeypatch
    ):
        from obdi.cli import build_web_config

        db = tmp_path / "store.sqlite3"
        with Store(db) as store:
            _private_store(store)
            report = balance_reconciliation(store)
            expected = (
                report.describe(masked=True, unmask_hint=PAGE_HINT),
                report.describe(masked=False, unmask_hint=PAGE_HINT),
            )

        monkeypatch.setenv("OBDI_CONNECTION_STORE", str(tmp_path / "connections.json"))
        monkeypatch.setenv("OBDI_ACCOUNT_MAP", str(tmp_path / "accounts.json"))
        for variable in ("TRUELAYER_CLIENT_ID", "TRUELAYER_CLIENT_SECRET_FILE"):
            monkeypatch.delenv(variable, raising=False)
        config = build_web_config(db)

        assert config is not None, "the builder refused a store it should have accepted"
        hook = config.balance_reconciliation_text
        assert hook is not None
        assert (hook(True), hook(False)) == expected

    def test_RealPage_MaskedHeaderNamesTheButtonThePageActuallyHas(
        self, tmp_path, monkeypatch
    ):
        """The hint is only true if the control it names is on the same page,
        and `?values=1` (which the page ignores) is not offered."""
        from obdi.cli import build_web_config

        db = tmp_path / "store.sqlite3"
        with Store(db) as store:
            _private_store(store)
        monkeypatch.setenv("OBDI_CONNECTION_STORE", str(tmp_path / "connections.json"))
        monkeypatch.setenv("OBDI_ACCOUNT_MAP", str(tmp_path / "accounts.json"))
        for variable in ("TRUELAYER_CLIENT_ID", "TRUELAYER_CLIENT_SECRET_FILE"):
            monkeypatch.delenv(variable, raising=False)
        config = build_web_config(db)
        assert config is not None

        page = _get(config, "/balance-reconciliation").text

        assert PAGE_HINT in page
        assert "Show values</button>" in page
        assert "values=1" not in page
