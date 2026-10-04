"""The cross-source agreement page is masked unless it is posted for.

Every GET page here shows counts, names, dates, and verdicts only. The figures
a pair of sources disagree about - amounts, nets, payee text - answer a POST
made on purpose and are marked not to be kept. The rows below are invented, and
each private value is distinctive enough that a leak cannot hide as a
coincidence.
"""

from __future__ import annotations

import re
import tempfile
import threading
from datetime import date
from http.server import HTTPServer
from pathlib import Path

import httpx
import pytest

from obdi.connections import ConnectionStore
from obdi.coverage import agreements, transpositions
from obdi.models import SourceTier, Transaction
from obdi.web import AuthorisationSession, ConnectionHandler, WebConfig

ACCOUNT = "starling-personal"
SIBLINGS = {"starling": [ACCOUNT, "starling-space-bills"]}

NETFLIX = "NETFLIX PRIVATE PAYEE"
ACME = "ACME TRANSPOSED PAYEE"
BULK_PAYEE = "BULK PAYEE"

#: Minor units chosen so that no formatted figure is a substring of a count, a
#: date, or the stylesheet.
NETFLIX_MINOR = -48731
TRANSPOSED_MINOR = -123457
LEG_MINOR = -150031
BULK_BASE_MINOR = -90011


def _txn(
    source: str,
    month: int,
    day: int,
    amount: int,
    *,
    account: str = ACCOUNT,
    desc: str | None = None,
    confirmed: bool = False,
) -> Transaction:
    return Transaction(
        account_id=account,
        amount_minor=amount,
        currency="GBP",
        value_date=date(2026, month, day),
        booking_date=date(2026, month, day),
        description=desc or f"txn {month}-{day}",
        source=source,
        source_id=None,
        tier=SourceTier.SYNTHETIC,
        content_key=f"k{source}{month}{day}{amount}",
        is_internal_transfer=False,
        transfer_confirmed=confirmed,
    )


def _held(*, unexplained_bulk: int = 0) -> list[Transaction]:
    rows = [
        _txn("starling", 1, 1, -500),
        _txn("starling", 1, 6, 2000),
        _txn("starling-csv", 1, 1, -500),
        _txn("starling-csv", 1, 6, 2000),
        _txn("starling", 1, 4, NETFLIX_MINOR, desc=NETFLIX),
        _txn("starling", 1, 3, LEG_MINOR, desc="Bills", confirmed=True),
        # One payment the two sources date as each other's day/month swap.
        _txn("starling", 3, 2, TRANSPOSED_MINOR, desc=ACME),
        _txn("starling-csv", 2, 3, TRANSPOSED_MINOR, desc=ACME),
    ]
    rows += [
        _txn("starling", 1, 7 + index, BULK_BASE_MINOR - index, desc=BULK_PAYEE)
        for index in range(unexplained_bulk)
    ]
    return rows


def _report(held: list[Transaction]) -> dict[str, object]:
    by_account: dict[str, list[object]] = {}
    for agreement in agreements(held, sibling_accounts=SIBLINGS):
        by_account.setdefault(agreement.account_id, []).append(agreement.outline())
    return {
        "accounts": [
            {"account": account, "entries": entries}
            for account, entries in sorted(by_account.items())
        ],
        "missing": [],
        "transposed": [item.outline() for item in transpositions(held)],
    }


def _request(report: dict[str, object], method: str, path: str) -> httpx.Response:
    with tempfile.TemporaryDirectory() as directory:
        config = WebConfig(
            client_id="client-1",
            client_secret="tlcs_live_abcdefghij1234567890",
            redirect_uri="https://obdi.example.com/callback",
            connection_store=ConnectionStore(Path(directory) / "c.json"),
            agreement_report=lambda: report,
        )
        handler = type(
            "H",
            (ConnectionHandler,),
            {"config": config, "session": AuthorisationSession()},
        )
        httpd = HTTPServer(("127.0.0.1", 0), handler)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        try:
            return httpx.request(
                method, f"http://127.0.0.1:{httpd.server_port}{path}", timeout=20
            )
        finally:
            httpd.shutdown()
            httpd.server_close()


PRIVATE_FORMATTED = ("487.31", "1234.57", "1500.31", "900.11")
MONEY_FIGURE = re.compile(r"[£€$]\s*[-\d]|\d[\d,]*\.\d\d(?![\d%a-z])")


class TestTheAgreementsPageIsMaskedUnlessPostedFor:
    def test_Page_Fetched_ShowsCountsSourcesDatesAndVerdict(self):
        page = _request(_report(_held()), "GET", "/agreements").text

        assert "starling vs starling-csv" in page
        assert "transactions" in page
        assert "the sources do not match" in page
        assert "2026-01-01" in page
        assert "in starling ONLY" in page
        assert "confirmed internal-transfer leg" in page
        assert "MASKED rendering" in page

    def test_Page_Fetched_ContainsNoAmountPayeeOrMoneyFigureAnywhere(self):
        page = _request(_report(_held(unexplained_bulk=12)), "GET", "/agreements").text

        for private in (NETFLIX, ACME, BULK_PAYEE, *PRIVATE_FORMATTED):
            assert private not in page
        assert MONEY_FIGURE.search(page) is None, MONEY_FIGURE.search(page)

    def test_Page_Fetched_SaysTheNetsWereComparedWithoutStatingThem(self):
        page = _request(_report(_held()), "GET", "/agreements").text

        assert "nets were compared" in page
        assert " net " not in page.replace("nets were compared", "")

    def test_Page_FetchedWithUnexplainedRows_ShowsDateAndMaskedTokenPerSampleRow(self):
        from obdi.masking import MASKED_TOTAL

        page = _request(_report(_held()), "GET", "/agreements").text

        assert f"2026-01-04 {MASKED_TOTAL}" in page

    def test_Page_FetchedWithTransposition_KeepsDatesAndSourcesAndNothingElse(self):
        page = _request(_report(_held()), "GET", "/agreements").text

        assert "Dates do not match" in page
        assert "dated 2026-03-02 by starling" in page
        assert "2026-02-03 by starling-csv" in page
        assert ACME not in page
        assert "1234.57" not in page

    def test_Page_FetchedWithMoreRowsThanShown_KeepsThePlusNMoreLine(self):
        page = _request(_report(_held(unexplained_bulk=12)), "GET", "/agreements").text

        # Thirteen unexplained rows on the feed side, ten of them listed.
        assert "+3 more not shown" in page
        assert BULK_PAYEE not in page

    def test_Page_Fetched_OffersAFormToShowValuesAndNotALink(self):
        page = _request(_report(_held()), "GET", "/agreements").text

        assert '<form method="post" action="/agreements">' in page
        assert "Show values" in page
        assert 'href="/agreements"' not in page

    @pytest.mark.parametrize("query", ["values=1", "unmask=1", "show=1", "masked=0"])
    def test_Page_FetchedWithAnyQuery_StaysMasked(self, query):
        page = _request(_report(_held()), "GET", f"/agreements?{query}").text

        assert NETFLIX not in page
        assert "MASKED rendering" in page

    def test_Page_Fetched_IsNotMarkedNoStore(self):
        response = _request(_report(_held()), "GET", "/agreements")

        assert response.headers.get("Cache-Control") != "no-store"

    def test_Page_WhenPostedFor_ShowsTheFiguresAndPayeesAndIsNotKept(self):
        response = _request(_report(_held()), "POST", "/agreements")

        assert response.status_code == 200
        assert response.headers["Cache-Control"] == "no-store"
        page = response.text
        assert "UNMASKED rendering" in page
        assert NETFLIX in page and "-£487.31" in page
        assert f"-£1234.57 &quot;{ACME}&quot; dated 2026-03-02 by starling" in page
        assert "; net " in page
        assert 'href="/agreements"' in page

    def test_Page_WhenPostedForWithNoOtherSource_StillSaysThereIsNothingToCompare(self):
        report = {"accounts": [], "missing": [], "transposed": []}

        response = _request(report, "POST", "/agreements")

        assert "nothing to compare" in response.text
        assert response.headers["Cache-Control"] == "no-store"

    def test_Page_WhenPostedFor_WithRowsOfAnUnknownShape_RendersWithoutFailing(self):
        report = {
            "accounts": [],
            "missing": [],
            "transposed": ["a plain line from an older caller", {"unexpected": 1}],
        }

        masked = _request(report, "GET", "/agreements")
        unmasked = _request(report, "POST", "/agreements")

        assert masked.status_code == 200 and unmasked.status_code == 200
        assert "a plain line from an older caller" not in masked.text
        assert "a plain line from an older caller" in unmasked.text
