"""The statement-shape page: masked by default, over HTTP, storing nothing.

The CLI proves the masking; this proves the SURFACE honours it - a page
that quietly returned real contents would be the leak the masking exists
to prevent, and a page is what a person will actually reach for from a
phone.
"""

from __future__ import annotations

import threading

import httpx
import pytest

from obdi.connections import ConnectionStore
from obdi.web import AuthorisationSession, ConnectionHandler, WebConfig
from test_statement_shape import build_pdf

STATEMENT = build_pdf(
    [
        "Statement of account",
        "Opening balance 1,234.56",
        "04 Jan SAINSBURYS S/MKTS 21.72",
    ]
)


@pytest.fixture
def server(tmp_path):
    config = WebConfig(
        client_id="client-1",
        client_secret="secret-1",
        redirect_uri="https://obdi.example.com/callback",
        connection_store=ConnectionStore(tmp_path / "c.json"),
    )
    handler = type(
        "ShapeHandler",
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


def _post(base: str, *, show_values: bool = False) -> httpx.Response:
    data = {"show_values": "1"} if show_values else {}
    return httpx.post(
        f"{base}/statement-shape",
        files={"file": ("statement.pdf", STATEMENT, "application/pdf")},
        data=data,
        headers={"Origin": base},
        timeout=20,
    )


class TestThePage:
    def test_TheForm_ExplainsWhatItDoesAndDoesNotStore(self, server):
        page = httpx.get(f"{server}/statement-shape", timeout=20)

        assert page.status_code == 200
        assert "not stored" in page.text.lower()
        assert "masked" in page.text.lower()

    def test_AnUploadedStatement_ComesBackMasked_ByDefault(self, server):
        response = _post(server)

        assert response.status_code == 200
        assert "1,234.56" not in response.text, "a real balance must not appear"
        assert "SAINSBURYS" not in response.text
        assert "9,999.99" in response.text
        assert "Opening balance" in response.text, "layout survives"

    def test_AskingForRealValues_OffersAConfirmation_ButDisclosesNothingYet(
        self, server
    ):
        # Disclosure is a two-request walk - see test_disclosure_gate for
        # the whole of it. Here: asking is not receiving.
        response = _post(server, show_values=True)

        assert response.status_code == 200
        assert "1,234.56" not in response.text
        assert 'name="disclose_token"' in response.text

    def test_ANonPdf_IsAnsweredNotCrashed(self, server):
        response = httpx.post(
            f"{server}/statement-shape",
            files={"file": ("notes.txt", b"hello", "text/plain")},
            headers={"Origin": server},
            timeout=20,
        )

        assert response.status_code == 200
        assert "could not be read" in response.text.lower()

    def test_ACrossSiteUpload_IsRefused(self, server):
        # The page accepts a file and echoes it back; a page on another site
        # must not be able to drive it.
        response = httpx.post(
            f"{server}/statement-shape",
            files={"file": ("statement.pdf", STATEMENT, "application/pdf")},
            headers={"Origin": "https://elsewhere.example"},
            timeout=20,
        )

        assert response.status_code == 403
