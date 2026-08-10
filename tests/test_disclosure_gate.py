"""Real contents must be reached deliberately, never stumbled into.

The web pages are read by agents as well as by people - the workstation
curls this service over the tailnet routinely - so a surface that returns
real statement contents to a single POST is a surface something can wander
into while looking for something else. A checkbox is not enough: it is one
field in one request, which is exactly the shape an automated request takes
by accident.

So disclosure takes two requests and a phrase typed by hand. The first
request never returns values no matter what it asks for; it returns the
masked shape plus a single-use token. Only a second request carrying that
token AND the typed phrase discloses anything, and it says so loudly when
it does. The same two-step, server-enforced shape as the wrong-destination
override, for the same reason: a guard the browser merely requires is
decoration.
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

PHRASE = "SHOW REAL VALUES"


@pytest.fixture
def server(tmp_path):
    config = WebConfig(
        client_id="client-1",
        client_secret="secret-1",
        redirect_uri="https://obdi.example.com/callback",
        connection_store=ConnectionStore(tmp_path / "c.json"),
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


def _upload(base: str, **fields: str) -> httpx.Response:
    return httpx.post(
        f"{base}/statement-shape",
        files={"file": ("statement.pdf", STATEMENT, "application/pdf")},
        data=fields,
        headers={"Origin": base},
        timeout=20,
    )


def _token_from(html_text: str) -> str:
    marker = 'name="disclose_token" value="'
    start = html_text.index(marker) + len(marker)
    return html_text[start : html_text.index('"', start)]


class TestOneRequestNeverDiscloses:
    def test_APlainUpload_ReturnsTheMaskedShape(self, server):
        response = _upload(server)

        assert response.status_code == 200
        assert "1,234.56" not in response.text
        assert "9,999.99" in response.text

    def test_AskingForValuesInTheSameRequest_StillReturnsNone(self, server):
        # The single-request shape an automated caller would produce.
        response = _upload(server, show_values="1", confirm=PHRASE)

        assert response.status_code == 200
        assert "1,234.56" not in response.text, "one request must never disclose"
        assert "9,999.99" in response.text

    def test_TheFirstRequest_OffersASingleUseToken_AndSaysWhatItIsFor(self, server):
        response = _upload(server, show_values="1")

        assert 'name="disclose_token"' in response.text
        assert PHRASE in response.text


class TestTheSecondRequestNeedsBothHalves:
    def test_TokenAndTypedPhrase_Disclose_AndTheOutputSaysSo(self, server):
        first = _upload(server, show_values="1")
        token = _token_from(first.text)

        second = httpx.post(
            f"{server}/statement-shape-disclose",
            data={"disclose_token": token, "confirm": PHRASE},
            headers={"Origin": server},
            timeout=20,
        )

        assert second.status_code == 200
        assert "1,234.56" in second.text
        assert "Real values shown" in second.text

    def test_TheTokenAlone_IsRefused(self, server):
        first = _upload(server, show_values="1")
        token = _token_from(first.text)

        second = httpx.post(
            f"{server}/statement-shape-disclose",
            data={"disclose_token": token},
            headers={"Origin": server},
            timeout=20,
        )

        assert second.status_code == 400
        assert "1,234.56" not in second.text

    def test_TheWrongPhrase_IsRefused(self, server):
        first = _upload(server, show_values="1")
        token = _token_from(first.text)

        second = httpx.post(
            f"{server}/statement-shape-disclose",
            data={"disclose_token": token, "confirm": "yes"},
            headers={"Origin": server},
            timeout=20,
        )

        assert second.status_code == 400
        assert "1,234.56" not in second.text

    def test_AToken_WorksOnce_ThenIsSpent(self, server):
        first = _upload(server, show_values="1")
        token = _token_from(first.text)
        body = {"disclose_token": token, "confirm": PHRASE}

        assert "1,234.56" in httpx.post(
            f"{server}/statement-shape-disclose",
            data=body,
            headers={"Origin": server},
            timeout=20,
        ).text
        replay = httpx.post(
            f"{server}/statement-shape-disclose",
            data=body,
            headers={"Origin": server},
            timeout=20,
        )

        assert replay.status_code == 400
        assert "1,234.56" not in replay.text

    def test_AnInventedToken_IsRefused(self, server):
        response = httpx.post(
            f"{server}/statement-shape-disclose",
            data={"disclose_token": "made-up", "confirm": PHRASE},
            headers={"Origin": server},
            timeout=20,
        )

        assert response.status_code == 400

    def test_ACrossSiteDisclosure_IsRefused(self, server):
        first = _upload(server, show_values="1")
        token = _token_from(first.text)

        response = httpx.post(
            f"{server}/statement-shape-disclose",
            data={"disclose_token": token, "confirm": PHRASE},
            headers={"Origin": "https://elsewhere.example"},
            timeout=20,
        )

        assert response.status_code == 403
