"""Hardening for the privately exposed service.

Reachable without authenticating by anything on the network it is published to,
and it can begin a bank authorisation, so the bar is higher than for a local
script. None of these are exploitable by a stranger from the internet - the
service is not public - but "only reachable by devices I trust" is a weaker
guarantee than it sounds once a phone, a laptop and a Docker host all qualify.
"""

import threading
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from obdi.connections import ConnectionStore, build_connection
from obdi.web import AuthorisationSession, ConnectionHandler, WebConfig, render_index

TOKENS = {"access_token": "a", "refresh_token": "r", "expires_in": 3600}


class TestSessionDoesNotGrowWithoutBound:
    def test_Session_WhenAuthorisationsAreAbandoned_ExpiredStatesAreEvicted(self):
        # /connect needs no authentication, so anything reaching it could
        # mint states indefinitely; abandoned ones also accumulate in normal
        # use, since starting an authorisation and not finishing it is common.
        session = AuthorisationSession()
        stale = datetime.now(UTC) - timedelta(hours=2)
        for index in range(50):
            session.begin(f"bank-{index}", now=stale)

        session.begin("current")

        assert len(session) == 1

    def test_Session_WhenStatesAreStillFresh_NoneAreEvicted(self):
        session = AuthorisationSession()
        session.begin("halifax")
        session.begin("nationwide")
        assert len(session) == 2

    def test_Session_WhenClaimed_EntryIsReleased(self):
        session = AuthorisationSession()
        state = session.begin("halifax")
        session.claim(state)
        assert len(session) == 0


class TestConnectionNamesRoundTripSafely:
    def test_Link_WhenNameContainsAnAmpersand_ReconnectsTheSameConnection(self, tmp_path):
        # HTML-escaping protects the page but not the query string: an
        # unencoded ampersand truncates the name and reconnects something else,
        # which would silently create a second connection to one bank.
        store = ConnectionStore(tmp_path / "c.json")
        store.put(build_connection(connection_id="m&s bank", provider="p", token_response=TOKENS))

        page = render_index(store).decode()

        assert "name=m%26s" in page
        assert "name=m&s" not in page

    def test_Link_WhenNameContainsASpace_Encoded(self, tmp_path):
        store = ConnectionStore(tmp_path / "c.json")
        store.put(
            build_connection(connection_id="virgin money", provider="p", token_response=TOKENS)
        )
        assert "virgin%20money" in render_index(store).decode()

    def test_Link_WhenNameIsOrdinary_LeftReadable(self, tmp_path):
        store = ConnectionStore(tmp_path / "c.json")
        store.put(build_connection(connection_id="halifax", provider="p", token_response=TOKENS))
        assert "name=halifax" in render_index(store).decode()


@pytest.fixture
def server(tmp_path):
    config = WebConfig(
        client_id="client-1",
        client_secret="secret-1",
        redirect_uri="https://obdi.example.internal/callback",
        connection_store=ConnectionStore(tmp_path / "c.json"),
    )
    handler = type(
        "TestHandler",
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


class TestOneClientCannotBlockTheService:
    def test_Service_WhenAConnectionIsOpenedAndLeftIdle_StillAnswersOthers(self, server):
        # A single-threaded server with no timeout wedges completely on one
        # idle socket - including the OAuth callback, so an authorisation
        # already in flight could never complete.
        import socket

        idle = socket.create_connection(("127.0.0.1", int(server.rsplit(":", 1)[1])))
        try:
            response = httpx.get(f"{server}/", timeout=5.0)
            assert response.status_code == 200
        finally:
            idle.close()

    def test_Service_WhenSeveralRequestsArriveAtOnce_AllAnswered(self, server):
        results = []

        def fetch():
            results.append(httpx.get(f"{server}/", timeout=5.0).status_code)

        threads = [threading.Thread(target=fetch) for _ in range(5)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)

        assert results == [200] * 5


class TestServerConstruction:
    def test_Server_WhenBuilt_HandlesRequestsConcurrently(self):
        from http.server import ThreadingHTTPServer

        server = ConnectionHandler.make_server(("127.0.0.1", 0), ConnectionHandler)
        try:
            assert isinstance(server, ThreadingHTTPServer)
        finally:
            server.server_close()

    def test_Server_WhenBuilt_HandlerCarriesASocketTimeout(self):
        # Without one, a socket that opens and says nothing holds its handler
        # indefinitely.
        assert ConnectionHandler.timeout is not None
        assert ConnectionHandler.timeout > 0
