"""The mobile connection interface.

Most of this is about the `state` parameter. It carries which connection is
being authorised through a redirect this service does not control, and
verifying it on return is what stops a code being redeemed into a connection
nobody chose.
"""

import threading
from datetime import UTC, date, datetime, timedelta
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
            start_backfill=lambda name, psu_ip=None: (started.append(name), True)[1],
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


class TestTheHomepageWarnsAboutAnUnusableSecret:
    """A malformed secret must announce itself where the person is.

    Not by refusing to start - the consent clocks and reconnect links owe
    nothing to an online-only credential - and not by waiting for the next
    authorisation to fail with a burnt single-use code. Checked at render, so
    fixing the file clears the banner on refresh with no restart.
    """

    def test_Index_WhenTheSecretIsMalformed_ShowsTheBanner(self, monkeypatch, tmp_path):
        secret = tmp_path / "client-secret"
        secret.write_text('"quoted-and-wrong"', encoding="utf-8")
        monkeypatch.setenv("TRUELAYER_CLIENT_SECRET_FILE", str(secret))

        page = render_index(ConnectionStore(tmp_path / "c.json")).decode()

        assert "looks malformed" in page
        assert "quoted-and-wrong" not in page, "never show the value, even a broken one"

    def test_Index_WhenTheSecretIsWellFormed_ShowsNoBanner(self, monkeypatch, tmp_path):
        secret = tmp_path / "client-secret"
        secret.write_text("tlcs_live_abcdefghij1234567890", encoding="utf-8")
        monkeypatch.setenv("TRUELAYER_CLIENT_SECRET_FILE", str(secret))

        page = render_index(ConnectionStore(tmp_path / "c.json")).decode()

        assert "malformed" not in page

    def test_Index_AfterTheFileIsFixed_TheBannerClearsOnRefreshWithoutRestart(
        self, monkeypatch, tmp_path
    ):
        secret = tmp_path / "client-secret"
        secret.write_text('"quoted-and-wrong"', encoding="utf-8")
        monkeypatch.setenv("TRUELAYER_CLIENT_SECRET_FILE", str(secret))
        store = ConnectionStore(tmp_path / "c.json")

        assert "malformed" in render_index(store).decode()

        secret.write_text("tlcs_live_abcdefghij1234567890", encoding="utf-8")

        assert "malformed" not in render_index(store).decode()


class TestPreflightBeforeTheBank:
    """Concerns are cheaper before the bank than after it.

    The expensive failure is a completed bank login whose code burns against a
    credential that could never exchange it. A quick check between the connect
    click and the redirect converts that into an immediate page - with a way
    to proceed anyway, because a preflight that cannot be overridden becomes a
    gate whenever the check itself is wrong.
    """

    def _server(self, tmp_path, preflight):
        config = WebConfig(
            client_id="client-1",
            client_secret="tlcs_live_abcdefghij1234567890",
            redirect_uri="https://obdi.example.com/callback",
            connection_store=ConnectionStore(tmp_path / "c.json"),
            preflight=preflight,
        )
        handler = type(
            "H", (ConnectionHandler,), {"config": config, "session": AuthorisationSession()}
        )
        httpd = HTTPServer(("127.0.0.1", 0), handler)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        return httpd, handler, f"http://127.0.0.1:{httpd.server_port}"

    def test_Connect_WhenPreflightRaisesConcerns_StopsBeforeTheBankAndOffersOverride(
        self, tmp_path
    ):
        httpd, handler, base = self._server(
            tmp_path, lambda: ["the secret does not start with tlcs_live_"]
        )
        try:
            response = httpx.get(f"{base}/connect", params={"name": "halifax"})
        finally:
            httpd.shutdown()

        assert response.status_code == 200, "a concern page, not a redirect"
        assert "tlcs_live_" in response.text
        assert "force=1" in response.text, "the override must be offered"
        assert len(handler.session) == 0, "no state minted for a journey not started"

    def test_Connect_WhenForced_ProceedsToTheBankDespiteConcerns(self, tmp_path):
        httpd, handler, base = self._server(tmp_path, lambda: ["definitely broken"])
        try:
            response = httpx.get(
                f"{base}/connect", params={"name": "halifax", "force": "1"}
            )
        finally:
            httpd.shutdown()

        assert response.status_code == 302
        assert "auth.truelayer.com" in response.headers["location"]
        assert len(handler.session) == 1

    def test_Connect_WhenPreflightIsClear_RedirectsExactlyAsBefore(self, tmp_path):
        httpd, handler, base = self._server(tmp_path, lambda: [])
        try:
            response = httpx.get(f"{base}/connect", params={"name": "halifax"})
        finally:
            httpd.shutdown()

        assert response.status_code == 302
        assert len(handler.session) == 1

    def test_Connect_WithNoPreflightConfigured_BehavesAsBefore(self, tmp_path):
        httpd, _handler, base = self._server(tmp_path, None)
        try:
            response = httpx.get(f"{base}/connect", params={"name": "halifax"})
        finally:
            httpd.shutdown()

        assert response.status_code == 302


class TestTheHomepageShowsWhatIsHeld:
    """The answer to "did the backfill work?" belongs on the page, not in a shell.

    The whole interface exists so the quarterly chores happen from a phone;
    sending someone to docker exec to learn what a connection fetched defeats
    that at the moment of greatest curiosity - right after connecting.
    """

    def test_Index_WhenHoldingsAreProvided_ShowsCountAndRangePerAccount(self, tmp_path):
        from obdi.coverage import SourceCoverage

        holdings = [
            SourceCoverage(
                account_id="halifax-current",
                source="truelayer",
                count=1042,
                earliest=date(2024, 8, 2),
                latest=date(2026, 8, 1),
                inflow_minor=100,
                outflow_minor=200,
                with_durable_id=1042,
            )
        ]

        page = render_index(
            ConnectionStore(tmp_path / "c.json"), holdings=lambda: holdings
        ).decode()

        assert "halifax-current" in page
        assert "1,042" in page
        assert "2024-08-02" in page and "2026-08-01" in page

    def test_Index_WithNoHoldingsHook_RendersExactlyAsBefore(self, tmp_path):
        page = render_index(ConnectionStore(tmp_path / "c.json")).decode()

        assert "Held so far" not in page

    def test_Index_WhenTheHoldingsHookFails_ThePageStillRenders(self, tmp_path):
        def boom():
            raise RuntimeError("store locked")

        page = render_index(ConnectionStore(tmp_path / "c.json"), holdings=boom).decode()

        # A reporting extra must never take down the page that manages
        # connections - the store may legitimately be mid-write during a
        # backfill, which is exactly when someone is refreshing.
        assert "Bank connections" in page


class TestTheAuthorisersAddressIsTheRealOne:
    """Behind a reverse proxy, the socket peer is the proxy, not the person.

    The TLS-terminating layer proxies from loopback, so the connection's own
    address is 127.0.0.1 - and declaring THAT as the customer's address to a
    regulated counterparty is worse than declaring nothing. The proxy forwards
    the true client address in X-Forwarded-For; use it, and when neither source
    is credible, stay silent rather than assert garbage.
    """

    def _authorise(self, monkeypatch, tmp_path, headers):
        received: list[object] = []
        config = WebConfig(
            client_id="client-1",
            client_secret="tlcs_live_abcdefghij1234567890",
            redirect_uri="https://obdi.example.com/callback",
            connection_store=ConnectionStore(tmp_path / "c.json"),
            start_backfill=lambda name, psu_ip=None: (received.append(psu_ip), True)[1],
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
            httpx.get(
                f"{base}/callback", params={"code": "c", "state": state}, headers=headers
            )
        finally:
            httpd.shutdown()
        return received

    def test_Backfill_BehindTheProxy_GetsTheForwardedAddress(self, monkeypatch, tmp_path):
        received = self._authorise(
            monkeypatch, tmp_path, {"X-Forwarded-For": "100.96.178.101"}
        )

        assert received == ["100.96.178.101"]

    def test_Backfill_WithOnlyALoopbackPeer_DeclaresNothing(self, monkeypatch, tmp_path):
        received = self._authorise(monkeypatch, tmp_path, {})

        assert received == [None], "silence beats asserting 127.0.0.1 to a bank"


class TestExtendingHistoryFromThePage:
    """Window probing as buttons: verifiably human, one click per window.

    Every extend is a real person pressing a real button on their own device -
    the forwarded address rides along as the attended declaration, and the
    landed artefact carries it as permanent provenance. The CLI stays for
    scripting; the page is where a human belongs.
    """

    def _server(self, tmp_path, extendables, extend_window):
        config = WebConfig(
            client_id="client-1",
            client_secret="tlcs_live_abcdefghij1234567890",
            redirect_uri="https://obdi.example.com/callback",
            connection_store=ConnectionStore(tmp_path / "c.json"),
            extendables=extendables,
            extend_window=extend_window,
        )
        handler = type(
            "H", (ConnectionHandler,), {"config": config, "session": AuthorisationSession()}
        )
        httpd = HTTPServer(("127.0.0.1", 0), handler)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        return httpd, f"http://127.0.0.1:{httpd.server_port}"

    def test_Index_ListsExtendableAccountsWithWindowButtons(self, tmp_path):
        from obdi.web import ExtendableAccount

        httpd, base = self._server(
            tmp_path,
            lambda: [
                ExtendableAccount(
                    connection="halifax",
                    provider_ref="e9f8",
                    display="Current Account",
                    earliest=date(2024, 8, 2),
                )
            ],
            lambda **_: "",
        )
        try:
            page = httpx.get(base).text
        finally:
            httpd.shutdown()

        assert "Current Account" in page
        assert "2024-08-02" in page
        for days in (7, 30, 90, 365, 730):
            assert f'name="days" value="{days}"' in page

    def test_Extend_CallsTheHookWithTheForwardedAddress(self, tmp_path):
        captured = {}

        def extend(connection, provider_ref, days, psu_ip):
            captured.update(
                connection=connection, ref=provider_ref, days=days, psu_ip=psu_ip
            )
            return "landed 12 transactions; window now reaches 2024-01-01"

        httpd, base = self._server(tmp_path, lambda: [], extend)
        try:
            response = httpx.post(
                f"{base}/extend",
                data={"connection": "halifax", "account": "e9f8", "days": "365"},
                headers={"X-Forwarded-For": "100.96.178.101"},
            )
        finally:
            httpd.shutdown()

        assert captured == {
            "connection": "halifax", "ref": "e9f8", "days": 365,
            "psu_ip": "100.96.178.101",
        }
        assert "2024-01-01" in response.text

    def test_Extend_WhenTheProviderRefuses_ShowsTheReasonAndTheWayHome(self, tmp_path):
        def refuse(**_):
            raise RuntimeError("Transaction fetch failed (HTTP 400): invalid_date_range")

        httpd, base = self._server(tmp_path, lambda: [], refuse)
        try:
            response = httpx.post(
                f"{base}/extend",
                data={"connection": "halifax", "account": "e9f8", "days": "365"},
            )
        finally:
            httpd.shutdown()

        assert response.status_code == 502
        assert "invalid_date_range" in response.text
        assert "Back to connections" in response.text


    def test_Extend_ProviderErrorParts_AreRenderedSeparately_NotAsOneJsonBlob(self, tmp_path):
        from obdi.providers.truelayer import TrueLayerError

        def refuse(**_):
            raise TrueLayerError(
                "Transaction fetch failed (HTTP 403): sca_exceeded",
                status=403,
                code="sca_exceeded",
                description="SCA exemption has expired. The PSU should re-authenticate.",
                provider_details="403 access_denied: SCA exemption has expired",
                raw='{"error":"sca_exceeded","error_description":"SCA exemption has expired."}',
            )

        httpd, base = self._server(tmp_path, lambda: [], refuse)
        try:
            response = httpx.post(
                f"{base}/extend",
                data={"connection": "halifax", "account": "e9f8", "days": "365"},
            )
        finally:
            httpd.shutdown()

        page = response.text
        prominent = page.split("<details", 1)[0]
        # The machine code stands alone, the prose stands alone, and the
        # matching remedy is in the prominent part - not lost in a blob.
        assert "<code>sca_exceeded</code>" in prominent
        assert "SCA exemption has expired." in prominent
        assert "Re-authorise" in prominent
        # The acronyms the provider throws around are defined on the page.
        assert "Strong Customer Authentication" in prominent
        assert "Payment Services User" in prominent
        # The raw body is available, but folded away, pretty at display time.
        assert "<details><summary>Full provider response</summary>" in page
        assert "error_description" in page.split("Full provider response", 1)[1]

    def test_Extend_OtherProviderErrors_DoNotClaimTheScaRemedy(self, tmp_path):
        from obdi.providers.truelayer import TrueLayerError

        def refuse(**_):
            raise TrueLayerError(
                "Transaction fetch failed (HTTP 400): invalid_date_range",
                status=400,
                code="invalid_date_range",
                description="The requested window is not valid.",
            )

        httpd, base = self._server(tmp_path, lambda: [], refuse)
        try:
            response = httpx.post(
                f"{base}/extend",
                data={"connection": "halifax", "account": "e9f8", "days": "365"},
            )
        finally:
            httpd.shutdown()

        page = response.text
        prominent = page.split("<details", 1)[0]
        assert "<code>invalid_date_range</code>" in prominent
        # The SCA remedy exists on the page only inside the folded full list.
        assert "Re-authorise" not in prominent
        assert "Re-authorise" in page


class TestBrowsingRawArtefactsFromThePage:
    """The store's evidence, browsable where the person already is.

    A listing of what was landed, then per artefact: the static circumstances
    (origin, range asked, trigger, attended declaration) beside the computed
    shape (fields, presence, min and max) - and the payload itself rendered
    pretty at DISPLAY time, never modified at rest.
    """

    def _server(self, tmp_path, artefact_index, artefact_detail):
        config = WebConfig(
            client_id="client-1",
            client_secret="tlcs_live_abcdefghij1234567890",
            redirect_uri="https://obdi.example.com/callback",
            connection_store=ConnectionStore(tmp_path / "c.json"),
            artefact_index=artefact_index,
            artefact_detail=artefact_detail,
        )
        handler = type(
            "H", (ConnectionHandler,), {"config": config, "session": AuthorisationSession()}
        )
        httpd = HTTPServer(("127.0.0.1", 0), handler)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        return httpd, f"http://127.0.0.1:{httpd.server_port}"

    def test_Listing_ShowsEachArtefactWithItsCircumstances(self, tmp_path):
        httpd, base = self._server(
            tmp_path,
            lambda: [
                {
                    "id": 7,
                    "source": "truelayer-booked",
                    "account_ref": "halifax-current",
                    "fetched_at": "2026-08-01T22:30:00+00:00",
                    "bytes": 412530,
                    "origin": "https://api/transactions?from=2024-08-02&to=2026-08-01",
                    "trigger": "post-auth-backfill",
                }
            ],
            lambda _id, with_payload=False: None,
        )
        try:
            page = httpx.get(f"{base}/artefacts").text
        finally:
            httpd.shutdown()

        assert "truelayer-booked" in page
        assert "post-auth-backfill" in page
        assert "from=2024-08-02" in page
        assert 'href="/artefact?id=7"' in page

    def test_Detail_ShowsStaticAndComputedMetadata(self, tmp_path):
        detail = {
            "id": 7,
            "source": "truelayer-booked",
            "account_ref": "halifax-current",
            "fetched_at": "2026-08-01T22:30:00+00:00",
            "origin": "https://api/transactions?from=2024-08-02",
            "request_meta": {"trigger": "web-extend", "attended_from": "100.96.178.101"},
            "summary": {
                "kind": "json",
                "items": 660,
                "bytes": 412530,
                "fields": [
                    {
                        "path": "timestamp",
                        "present": 660,
                        "types": ["string"],
                        "min": "2024-08-02T00:00:00Z",
                        "max": "2026-08-01T00:00:00Z",
                    }
                ],
            },
        }
        httpd, base = self._server(tmp_path, lambda: [], lambda _id, with_payload=False: detail)
        try:
            page = httpx.get(f"{base}/artefact", params={"id": "7"}).text
        finally:
            httpd.shutdown()

        assert "web-extend" in page
        assert "100.96.178.101" in page
        assert "660" in page
        assert "2024-08-02T00:00:00Z" in page
        assert 'href="/artefact?id=7&view=payload"' in page

    def test_PayloadView_RendersPrettyAndEscaped(self, tmp_path):
        detail = {
            "id": 7,
            "source": "truelayer-booked",
            "payload_pretty": '{\n  "note": "<script>alert(1)</script>"\n}',
        }
        httpd, base = self._server(tmp_path, lambda: [], lambda _id, with_payload=False: detail)
        try:
            page = httpx.get(f"{base}/artefact", params={"id": "7", "view": "payload"}).text
        finally:
            httpd.shutdown()

        assert "&lt;script&gt;" in page, "payload content must never execute in the page"
        assert "<script>alert" not in page

    def test_Detail_UnknownId_IsANotFoundWithTheWayHome(self, tmp_path):
        httpd, base = self._server(tmp_path, lambda: [], lambda _id, with_payload=False: None)
        try:
            response = httpx.get(f"{base}/artefact", params={"id": "999"})
        finally:
            httpd.shutdown()

        assert response.status_code == 404
        assert "Back to connections" in response.text
