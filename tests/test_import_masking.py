"""The import preview and result keep values behind a deliberate "Show values" press.

A reviewer saw the preview of an import show five sample rows with their amounts and descriptions
unmasked, the result of re-importing held rows show a full mismatch between two sources with a
pound amount and a description, and Back from the result return to the preview with the values on
it again. Reading values is a deliberate press on a page sent with no-store everywhere else; this
is the same rule, with the same masked shapes the ledger uses.

The invented file below holds a payee and an amount that appear nowhere else, so any trace of
either on a masked page is theirs.
"""

from __future__ import annotations

import threading
from http.server import HTTPServer

import httpx
import pytest

from obdi.accounts import AccountRecord, AccountRef
from obdi.cli import build_web_config
from obdi.store import Store
from obdi.web import AuthorisationSession, ConnectionHandler

PAYEE = "Quillfeather Lutherie"
OTHER_PAYEE = "Brambleknock Cartage"
AMOUNT = "431.77"
OTHER_AMOUNT = "29.18"
MONZO_AMOUNT = "431.99"
ACCOUNT = "import-masking"
MONZO = (
    "Transaction ID,Date,Time,Type,Name,Description,Amount,Currency\n"
    f"tx_1,14/03/2026,09:15:00,Card,{PAYEE},{PAYEE},-{MONZO_AMOUNT},GBP\n"
    f"tx_2,20/03/2026,09:15:00,Card,{OTHER_PAYEE},{OTHER_PAYEE},-{OTHER_AMOUNT},GBP\n"
).encode()
CSV = (
    "Date,Counter Party,Reference,Type,Amount (GBP),Balance (GBP),Notes\n"
    f"14/03/2026,{PAYEE},{PAYEE} invoice,CARD,-{AMOUNT},568.23,\n"
    f"20/03/2026,{OTHER_PAYEE},{OTHER_PAYEE} invoice,CARD,-{OTHER_AMOUNT},539.05,\n"
).encode()


@pytest.fixture
def served(tmp_path, monkeypatch):
    db = tmp_path / "masking.sqlite3"
    with Store(db) as store:
        store.declare_account(AccountRecord(ref=AccountRef(ACCOUNT), kind="current"))
    monkeypatch.setenv("OBDI_CONNECTION_STORE", str(tmp_path / "connections.json"))
    monkeypatch.delenv("OBDI_ACCOUNT_MAP", raising=False)
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
        yield f"http://127.0.0.1:{httpd.server_port}"
    finally:
        httpd.shutdown()


def preview(served: str, **extra: str) -> httpx.Response:
    return httpx.post(
        f"{served}/upload",
        data={"account": ACCOUNT, **extra},
        files={"statement": ("march.csv", CSV, "text/csv")},
        timeout=60,
    )


def token_of(page: str) -> str:
    return page.split('name="token" value="')[1].split('"')[0]


def show_values_of_preview(served: str, page: str) -> httpx.Response:
    return httpx.post(
        f"{served}/upload-preview",
        data={"token": token_of(page), "account": ACCOUNT, "show_values": "yes"},
        timeout=60,
    )


def leaks(page: str) -> list[str]:
    return [
        secret
        for secret in (PAYEE, OTHER_PAYEE, AMOUNT, OTHER_AMOUNT, MONZO_AMOUNT)
        if secret in page
    ]


class TestThePreviewIsMaskedByDefault:
    def test_Preview_ByDefault_ShowsNeitherPayeeNorAmount(self, served):
        page = preview(served)

        assert page.status_code == 200
        assert leaks(page.text) == []

    def test_Preview_ByDefault_StillSaysItParsedTheRightFile(self, served):
        page = preview(served).text

        assert "<strong>march.csv</strong> parsed as StarlingCsvParser" in page
        assert "2 rows" in page
        assert "2026-03-14 .. 2026-03-20" in page
        assert "dates %d/%m/%Y" in page

    def test_Preview_ByDefault_ShowsTheSampleInTheLedgersMaskedShape(self, served):
        page = preview(served).text

        assert "-999.99" in page, "an amount keeps its length and punctuation, as on the ledger"
        assert "Xxxxxxxxxxxx Xxxxxxxx xxxxxxx" in page, "a description keeps its length and case"

    def test_Preview_IsNeverCached(self, served):
        assert "no-store" in preview(served).headers.get("cache-control", "")

    def test_Preview_WhenShowValuesIsPressed_ShowsThemAndIsStillNeverCached(self, served):
        masked = preview(served).text

        shown = show_values_of_preview(served, masked)

        assert shown.status_code == 200
        assert PAYEE in shown.text
        assert f"-{AMOUNT}" in shown.text
        assert "no-store" in shown.headers.get("cache-control", "")

    def test_Preview_WhenValuesAreShown_OffersToMaskThemAgain(self, served):
        shown = show_values_of_preview(served, preview(served).text)

        assert "Hide values" in shown.text
        assert "Show values" not in shown.text

    def test_Preview_ShowValuesWithAnExpiredToken_SaysSoWithoutQuotingIt(self, served):
        gone = httpx.post(
            f"{served}/upload-preview",
            data={"token": "stale-token", "account": ACCOUNT, "show_values": "yes"},
            timeout=60,
        )

        assert gone.status_code == 410
        assert "stale-token" not in gone.text


class TestTheResultIsMaskedByDefault:
    def import_both(self, served: str) -> httpx.Response:
        """Import the Starling file, then a Monzo export of the same payments that disagrees.

        The second file states the first payment as MONZO_AMOUNT, so the answer compares held
        rows and names a payee and two amounts in its mismatch.
        """
        first = preview(served).text
        httpx.post(
            f"{served}/upload-confirm",
            data={"token": token_of(first), "account": ACCOUNT},
            timeout=60,
        )
        second = httpx.post(
            f"{served}/upload",
            data={"account": ACCOUNT},
            files={"statement": ("monzo.csv", MONZO, "text/csv")},
            timeout=60,
        ).text
        return httpx.post(
            f"{served}/upload-confirm",
            data={"token": token_of(second), "account": ACCOUNT},
            timeout=60,
        )

    def test_Result_ByDefault_ShowsNeitherPayeeNorAmount(self, served):
        result = self.import_both(served)

        assert result.status_code == 200
        assert leaks(result.text) == []

    def test_Result_IsNeverCached(self, served):
        assert "no-store" in self.import_both(served).headers.get("cache-control", "")

    def test_Result_ByDefault_StillSaysWhatWasImported(self, served):
        result = self.import_both(served)

        assert "monzo.csv -&gt; import-masking: parsed 2" in result.text

    def test_Result_WhenShowValuesIsPressed_ShowsTheComparisonAndIsNeverCached(self, served):
        result = self.import_both(served)
        token = result.text.split('name="result" value="')[1].split('"')[0]

        shown = httpx.post(
            f"{served}/upload-result", data={"result": token, "show_values": "yes"}, timeout=60
        )

        assert shown.status_code == 200
        assert "no-store" in shown.headers.get("cache-control", "")
        assert "monzo.csv -&gt; import-masking" in shown.text
        assert MONZO_AMOUNT in shown.text, "the mismatch is shown once values are asked for"
        assert "Hide values" in shown.text

    def test_Result_ShowValuesForAnUnknownResult_SaysItIsGoneWithoutQuotingIt(self, served):
        gone = httpx.post(
            f"{served}/upload-result", data={"result": "stale-result"}, timeout=60
        )

        assert gone.status_code == 410
        assert "stale-result" not in gone.text
