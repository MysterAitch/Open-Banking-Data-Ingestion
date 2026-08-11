"""A page that fails must say so, not drop the connection.

A handler that raises sends nothing at all, so a fronting proxy answers
502 and the person reading it learns only that something, somewhere, did
not work. That happened in use: one page raised, the rest of the site was
healthy, and the bare gateway error said neither which page nor why.

The trace belongs in the log. What belongs on the page is enough to say
what broke and where, because the person looking at it is the one who has
to decide whether it matters.
"""

from __future__ import annotations

import threading

import httpx
import pytest

from obdi.connections import ConnectionStore
from obdi.web import AuthorisationSession, ConnectionHandler, WebConfig


def _boom() -> list[dict[str, object]]:
    raise ValueError("the store said something unexpected")


@pytest.fixture
def server(tmp_path):
    config = WebConfig(
        client_id="c",
        client_secret="s",
        redirect_uri="https://obdi.example.com/callback",
        connection_store=ConnectionStore(tmp_path / "c.json"),
        artefact_index=_boom,
    )
    handler = type(
        "FaultHandler",
        (ConnectionHandler,),
        {"config": config, "session": AuthorisationSession()},
    )
    httpd = ConnectionHandler.make_server(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{httpd.server_port}"
    finally:
        httpd.shutdown()
        httpd.server_close()


class TestAPageThatRaises:
    def test_ItAnswers_RatherThanDroppingTheConnection(self, server):
        # The difference between a 500 and a dead socket is the difference
        # between "this page is broken" and "something somewhere is
        # broken" - and only one of them can be acted on.
        response = httpx.get(f"{server}/artefacts", timeout=20)

        assert response.status_code == 500

    def test_ItNamesTheRoute_AndWhatWentWrong(self, server):
        response = httpx.get(f"{server}/artefacts", timeout=20)

        assert "/artefacts" in response.text
        assert "ValueError" in response.text
        assert "the store said something unexpected" in response.text

    def test_TheRestOfTheSite_StillAnswers(self, server):
        # One broken page is not a broken site, and a reader who has just
        # met a failure needs somewhere to go from it.
        broken = httpx.get(f"{server}/artefacts", timeout=20)
        healthy = httpx.get(f"{server}/statement-shape", timeout=20)

        assert broken.status_code == 500
        assert healthy.status_code == 200

    def test_TheConnection_SurvivesTheFailure(self, server):
        # A failed response that leaves the socket unusable turns one
        # broken page into a broken session.
        with httpx.Client(timeout=20) as client:
            client.get(f"{server}/artefacts")
            after = client.get(f"{server}/statement-shape")

        assert after.status_code == 200
