"""Real contents of a statement are reached one way only: the values sitting.

These pages are read programmatically as well as by a person, so a surface that returns real
statement contents to a single request is one something can wander into. There used to be two ways
to real contents: a tick on the upload form followed by a typed phrase and a one-time token, and,
since the sitting, the cookie that shows values on every page. The first was never on the page the
owner was looking at (a kept statement's own shape), so a banner saying "values are shown on every
page for this sitting" sat above a masked shape. There is now one rule: a request with no valid
sitting cookie is masked, and with one both the kept statement's address and the answer to an
upload show the real shape, marked not to be stored.

Known answer: the invented statement holds one balance, 1,234.56, and one payee, SAINSBURYS; the
masked shape shows its balance as 9,999.99.
"""

from __future__ import annotations

import threading
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from obdi.ingest.connections import ConnectionStore
from obdi.pages import values_sitting
from obdi.pages.web import AuthorisationSession, ConnectionHandler, WebConfig
from test_statement_shape import build_pdf

STATEMENT = build_pdf(
    [
        "Statement of account",
        "Opening balance 1,234.56",
        "04 Jan SAINSBURYS S/MKTS 21.72",
    ]
)
BALANCE, PAYEE, MASKED_BALANCE = "1,234.56", "SAINSBURYS", "9,999.99"
KEPT = 7


@pytest.fixture
def server(tmp_path):
    kept: dict[int, tuple[str, bytes]] = {}

    def keep(payload: bytes, filename: str) -> tuple[int, bool]:
        was_new = KEPT not in kept
        kept[KEPT] = (filename, payload)
        return KEPT, was_new

    config = WebConfig(
        client_id="client-1",
        client_secret="secret-1",
        redirect_uri="https://obdi.example.com/callback",
        connection_store=ConnectionStore(tmp_path / "c.json"),
        keep_statement=keep,
        statement_payload=kept.get,
    )
    handler = type(
        "GateHandler",
        (ConnectionHandler,),
        {"config": config, "session": AuthorisationSession()},
    )
    httpd = ConnectionHandler.make_server(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{httpd.server_port}"
    finally:
        httpd.shutdown()
        httpd.server_close()


def sitting(ago: timedelta = timedelta(minutes=5)) -> dict[str, str]:
    issued = values_sitting.issue(datetime.now(UTC) - ago)
    return {"Cookie": f"{values_sitting.COOKIE}={issued}"}


def upload(base: str, headers: dict[str, str] | None = None, **fields: str) -> httpx.Response:
    return httpx.post(
        f"{base}/statement-shape",
        files={"file": ("statement.pdf", STATEMENT, "application/pdf")},
        data=fields,
        headers={"Origin": base, **(headers or {})},
        timeout=20,
    )


def kept_page(base: str, headers: dict[str, str] | None = None) -> httpx.Response:
    return httpx.get(f"{base}/statement-shape?artefact={KEPT}", headers=headers or {}, timeout=20)


class TestWithoutASittingEverythingIsMasked:
    def test_AnUpload_ReturnsTheMaskedShape(self, server):
        response = upload(server)

        assert response.status_code == 200
        assert BALANCE not in response.text and PAYEE not in response.text
        assert MASKED_BALANCE in response.text

    def test_AnUpload_AskingForValuesInTheRequest_StillGetsNoneAndIsOfferedNoConfirmation(
        self, server
    ):
        # The single-request shape an automated caller would produce.
        response = upload(server, show_values="1", confirm="SHOW REAL VALUES")

        assert BALANCE not in response.text
        assert "disclose_token" not in response.text
        assert "Show the real contents" not in response.text

    def test_TheUploadForm_OffersNoTickToShowTheRealContents(self, server):
        page = httpx.get(f"{server}/statement-shape", timeout=20)

        assert "show_values" not in page.text
        assert "REAL contents" not in page.text

    def test_AKeptStatementsAddress_IsMaskedAndSaysSo(self, server):
        upload(server)

        page = kept_page(server)

        assert BALANCE not in page.text and PAYEE not in page.text
        assert MASKED_BALANCE in page.text
        assert f"Kept statement {KEPT}, values masked" in page.text

    def test_TheSecondDisclosureDoor_IsGone(self, server):
        response = httpx.post(
            f"{server}/statement-shape-disclose",
            data={"disclose_token": "made-up", "confirm": "SHOW REAL VALUES"},
            headers={"Origin": server},
            timeout=20,
        )

        assert response.status_code == 404
        assert BALANCE not in response.text

    def test_AnExpiredSitting_IsMaskedAgain(self, server):
        upload(server)

        page = kept_page(server, sitting(timedelta(hours=values_sitting.SITTING_HOURS + 1)))

        assert BALANCE not in page.text
        assert MASKED_BALANCE in page.text


class TestInASittingTheRealShapeIsShownAndNotKept:
    def test_AKeptStatementsAddress_ShowsTheRealShapeNoStoreAndNoMaskedWording(self, server):
        upload(server)

        page = kept_page(server, sitting())

        assert page.status_code == 200
        assert page.headers["cache-control"] == "no-store"
        assert BALANCE in page.text and PAYEE in page.text
        assert MASKED_BALANCE not in page.text
        assert f"Kept statement {KEPT}, values shown for this sitting" in page.text
        assert "values masked" not in page.text

    def test_TheAnswerToAnUpload_ShowsTheRealShapeAndIsNotKept(self, server):
        response = upload(server, sitting())

        assert response.headers["cache-control"] == "no-store"
        assert BALANCE in response.text and PAYEE in response.text
        assert MASKED_BALANCE not in response.text
        assert "disclose_token" not in response.text

    def test_AFileKeptInTheSitting_IsStillMaskedOnceTheSittingEnds(self, server):
        upload(server, sitting())

        page = kept_page(server)

        assert BALANCE not in page.text and PAYEE not in page.text

    def test_TheUploadForm_SaysNothingOfValuesBeingMasked(self, server):
        page = httpx.get(f"{server}/statement-shape", headers=sitting(), timeout=20)

        assert "every value masked" not in page.text
