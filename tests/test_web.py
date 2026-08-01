"""The mobile connection interface.

Most of this is about the `state` parameter. It carries which connection is
being authorised through a redirect this service does not control, and
verifying it on return is what stops a code being redeemed into a connection
nobody chose.
"""

import threading
from datetime import UTC, datetime, timedelta
from http.server import HTTPServer

import httpx
import pytest

from obdi.connections import ConnectionStore, build_connection
from obdi.web import AuthorisationSession, ConnectionHandler, WebConfig, render_index

TOKENS = {"access_token": "a", "refresh_token": "r", "expires_in": 3600}


class TestAuthorisationState:
    def test_State_WhenBegun_CarriesTheConnectionNameBack(self):
        session = AuthorisationSession()
        state = session.begin("halifax")
        assert session.claim(state) == "halifax"

    def test_State_WhenUnknown_Refused(self):
        # Without this a code could be redeemed into a connection nobody chose.
        with pytest.raises(KeyError):
            AuthorisationSession().claim("made-up-state")

    def test_State_WhenReused_RefusedTheSecondTime(self):
        # Single use, so a code replayed from browser history cannot rebind.
        session = AuthorisationSession()
        state = session.begin("halifax")
        session.claim(state)
        with pytest.raises(KeyError):
            session.claim(state)

    def test_State_WhenStale_Refused(self):
        session = AuthorisationSession()
        long_ago = datetime.now(UTC) - timedelta(hours=2)
        state = session.begin("halifax", now=long_ago)
        with pytest.raises(KeyError, match="expired"):
            session.claim(state)

    def test_State_WhenTwoAuthorisationsInFlight_KeptDistinct(self):
        # Connecting several banks in one sitting is the expected use.
        session = AuthorisationSession()
        first = session.begin("halifax")
        second = session.begin("nationwide")
        assert session.claim(second) == "nationwide"
        assert session.claim(first) == "halifax"

    def test_State_WhenGenerated_NotGuessable(self):
        session = AuthorisationSession()
        states = {session.begin("x") for _ in range(50)}
        assert len(states) == 50
        assert all(len(state) > 20 for state in states)


class TestIndexPage:
    def test_Page_WhenNoConnections_SaysSoRatherThanShowingAnEmptyList(self, tmp_path):
        page = render_index(ConnectionStore(tmp_path / "c.json")).decode()
        assert "No banks connected yet" in page

    def test_Page_WhenConsentHealthy_ShowsDaysRemaining(self, tmp_path):
        store = ConnectionStore(tmp_path / "c.json")
        store.put(build_connection(connection_id="halifax", provider="p", token_response=TOKENS))
        assert "89 days left" in render_index(store).decode()

    def test_Page_WhenConsentNearlyExpired_FlaggedProminently(self, tmp_path):
        store = ConnectionStore(tmp_path / "c.json")
        old = datetime.now(UTC) - timedelta(days=85)
        store.put(
            build_connection(
                connection_id="halifax", provider="p", token_response=TOKENS, now=old
            )
        )
        page = render_index(store).decode()
        assert "expires in" in page and "warn" in page

    def test_Page_WhenConsentExpired_ShownAsExpired(self, tmp_path):
        store = ConnectionStore(tmp_path / "c.json")
        old = datetime.now(UTC) - timedelta(days=95)
        store.put(
            build_connection(
                connection_id="halifax", provider="p", token_response=TOKENS, now=old
            )
        )
        assert "expired" in render_index(store).decode()

    def test_Page_WhenRendered_SizedForAPhone(self, tmp_path):
        # It exists to be used from a phone; without a viewport it renders
        # zoomed out and the tap targets become unusable.
        page = render_index(ConnectionStore(tmp_path / "c.json")).decode()
        assert "viewport" in page

    def test_Page_WhenConnectionExists_OffersReconnectUnderTheSameName(self, tmp_path):
        # A new name would silently create a second connection to one bank.
        store = ConnectionStore(tmp_path / "c.json")
        store.put(build_connection(connection_id="halifax", provider="p", token_response=TOKENS))
        assert "/connect?name=halifax" in render_index(store).decode()


@pytest.fixture
def server(tmp_path):
    config = WebConfig(
        client_id="client-1",
        client_secret="secret-1",
        redirect_uri="https://obdi.example.com/callback",
        connection_store=ConnectionStore(tmp_path / "c.json"),
    )
    handler = type(
        "TestHandler",
        (ConnectionHandler,),
        {"config": config, "session": AuthorisationSession()},
    )
    httpd = HTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{httpd.server_port}", config, handler.session
    finally:
        httpd.shutdown()
        httpd.server_close()


class TestRouting:
    def test_Index_WhenRequested_Served(self, server):
        base, _, _ = server
        assert httpx.get(f"{base}/").status_code == 200

    def test_Connect_WhenNamed_RedirectsToTheBank(self, server):
        base, _, session = server
        response = httpx.get(f"{base}/connect", params={"name": "halifax"}, follow_redirects=False)
        assert response.status_code == 302
        assert "auth.truelayer.com" in response.headers["location"]
        assert len(session) == 1

    def test_Connect_WhenNameMissing_Refused(self, server):
        base, _, _ = server
        assert httpx.get(f"{base}/connect").status_code == 400

    def test_Callback_WhenStateUnrecognised_Refused(self, server):
        base, _, _ = server
        response = httpx.get(f"{base}/callback", params={"code": "abc", "state": "forged"})
        assert response.status_code == 400
        assert "verify" in response.text.lower()

    def test_Callback_WhenBankReportsFailure_ReasonShown(self, server):
        base, _, _ = server
        response = httpx.get(
            f"{base}/callback",
            params={"error": "access_denied", "error_description": "Cancelled at bank"},
        )
        assert response.status_code == 400
        assert "Cancelled at bank" in response.text

    def test_UnknownPath_WhenRequested_NotFound(self, server):
        base, _, _ = server
        assert httpx.get(f"{base}/admin").status_code == 404


class TestDeepHistoryIsFetchedWhileItIsStillReachable:
    """The backfill must start on authorisation, not on the next schedule.

    Beyond ninety days needs strong customer authentication, and the only moment
    one has just happened is the callback. A scheduler running hours later gets
    the ninety-day cap and the remainder is unrecoverable - so "it will be
    picked up on the next run" is not a substitute, it is data loss deferred.
    """

    def test_Authorisation_WhenItSucceeds_StartsTheBackfillImmediately(
        self, monkeypatch, tmp_path
    ):
        started: list[str] = []
        config = WebConfig(
            client_id="client-1",
            client_secret="secret-1",
            redirect_uri="https://obdi.example.com/callback",
            connection_store=ConnectionStore(tmp_path / "c.json"),
            start_backfill=lambda name: (started.append(name), True)[1],
        )
        monkeypatch.setattr(
            "obdi.web.exchange_code",
            lambda **_: {"access_token": "a", "refresh_token": "r", "expires_in": 3600},
        )
        handler = type(
            "H", (ConnectionHandler,), {"config": config, "session": AuthorisationSession()}
        )
        httpd = HTTPServer(("127.0.0.1", 0), handler)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        try:
            base = f"http://127.0.0.1:{httpd.server_port}"
            state = handler.session.begin("nationwide")
            response = httpx.get(f"{base}/callback", params={"code": "c", "state": state})
        finally:
            httpd.shutdown()

        assert response.status_code == 200
        assert started == ["nationwide"], "authorising must trigger its own backfill"

    def test_Authorisation_WhenNoBackfillRuns_SaysSoRatherThanImplyingSuccess(
        self, monkeypatch, tmp_path
    ):
        config = WebConfig(
            client_id="client-1",
            client_secret="secret-1",
            redirect_uri="https://obdi.example.com/callback",
            connection_store=ConnectionStore(tmp_path / "c.json"),
            start_backfill=None,
        )
        monkeypatch.setattr(
            "obdi.web.exchange_code",
            lambda **_: {"access_token": "a", "refresh_token": "r", "expires_in": 3600},
        )
        handler = type(
            "H", (ConnectionHandler,), {"config": config, "session": AuthorisationSession()}
        )
        httpd = HTTPServer(("127.0.0.1", 0), handler)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        try:
            base = f"http://127.0.0.1:{httpd.server_port}"
            state = handler.session.begin("halifax")
            response = httpx.get(f"{base}/callback", params={"code": "c", "state": state})
        finally:
            httpd.shutdown()

        # Silence would be the dangerous outcome: the reader assumes history is
        # being fetched, and only discovers otherwise once it is too late.
        assert "obdi pull halifax" in response.text
