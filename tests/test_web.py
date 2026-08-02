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
        for days in (1, 7, 30, 90, 365, 730):
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


    def test_Extend_ResultPages_KeepTheButtons_ForRepeatedPressing(self, tmp_path):
        from obdi.web import ExtendableAccount

        accounts = lambda: [  # noqa: E731
            ExtendableAccount(
                connection="halifax",
                provider_ref="e9f8",
                display="Current Account",
                earliest=date(2022, 8, 3),
            )
        ]

        httpd, base = self._server(tmp_path, accounts, lambda **_: "landed 3")
        try:
            success = httpx.post(
                f"{base}/extend",
                data={"connection": "halifax", "account": "e9f8", "days": "365"},
            ).text
        finally:
            httpd.shutdown()

        def refuse(**_):
            raise RuntimeError("Transaction fetch failed (HTTP 400): invalid_date_range")

        httpd, base = self._server(tmp_path, accounts, refuse)
        try:
            failure = httpx.post(
                f"{base}/extend",
                data={"connection": "halifax", "account": "e9f8", "days": "365"},
            ).text
        finally:
            httpd.shutdown()

        # Press, read, press again - on both outcomes, without a round trip.
        for page in (success, failure):
            assert 'name="days" value="365"' in page
            assert 'name="days" value="1"' in page

    def test_Extend_ResultPage_ShowsOnlyThePressedAccount(self, tmp_path):
        from obdi.web import ExtendableAccount

        httpd, base = self._server(
            tmp_path,
            lambda: [
                ExtendableAccount(
                    connection="halifax",
                    provider_ref="e9f8",
                    display="Current Account",
                    earliest=date(2020, 8, 7),
                ),
                ExtendableAccount(
                    connection="halifax",
                    provider_ref="b532",
                    display="Instant Saver",
                    earliest=date(2021, 7, 7),
                ),
            ],
            lambda **_: "landed 3",
        )
        try:
            page = httpx.post(
                f"{base}/extend",
                data={"connection": "halifax", "account": "e9f8", "days": "7"},
            ).text
        finally:
            httpd.shutdown()

        # Mid-probe, the page repeats ONE account's controls - the one just
        # pressed - because a wall of every account's buttons is where the
        # wrong account gets pressed.
        assert "Current Account" in page
        assert "Instant Saver" not in page

    def test_Extend_Refusal_StatesTheWindowThatWasAsked(self, tmp_path):
        from obdi.providers.truelayer import TrueLayerError

        def refuse(**_):
            exc = TrueLayerError(
                "Transaction fetch failed (HTTP 400): invalid_date_range",
                status=400,
                code="invalid_date_range",
                description="Date range not permitted.",
            )
            exc.asked_window = "since 2011-04-12 until 2013-04-12 (730 day step)"
            raise exc

        httpd, base = self._server(tmp_path, lambda: [], refuse)
        try:
            page = httpx.post(
                f"{base}/extend",
                data={"connection": "halifax", "account": "e9f8", "days": "730"},
            ).text
        finally:
            httpd.shutdown()

        prominent = page.split("<details", 1)[0]
        assert "since 2011-04-12 until 2013-04-12" in prominent
        # The boundary-shaped remedy for a walking-back refusal.
        assert "fixed DATE" in prominent

    def test_ExtendRow_ShowsCoverageFreshness_AndShoutsWhenStale(self, tmp_path):
        from datetime import UTC, datetime, timedelta

        from obdi.web import ExtendableAccount

        today = datetime.now(UTC).date()

        httpd, base = self._server(
            tmp_path,
            lambda: [
                ExtendableAccount(
                    connection="halifax",
                    provider_ref="fresh",
                    display="Fresh Account",
                    earliest=date(2020, 8, 7),
                    covered_to=today,
                    last_landed="2026-08-02T01:27:03+00:00",
                ),
                ExtendableAccount(
                    connection="halifax",
                    provider_ref="stale",
                    display="Stale Account",
                    earliest=date(2020, 8, 7),
                    covered_to=today - timedelta(days=9),
                ),
            ],
            lambda **_: "",
        )
        try:
            page = httpx.get(base).text
        finally:
            httpd.shutdown()

        # Fresh: stated quietly. Stale: a loud pill with the lag in days -
        # the quietly-stopped-scheduler failure made visible from the page.
        assert f"covered to {today.isoformat()}" in page
        assert "stale: 9 days behind" in page
        assert page.count("stale:") == 1

    def test_ExtendRow_ForAnEmptyAccount_ShowsHowFarProbing_HasAlreadyReached(
        self, tmp_path
    ):
        from obdi.web import ExtendableAccount

        httpd, base = self._server(
            tmp_path,
            lambda: [
                ExtendableAccount(
                    connection="halifax",
                    provider_ref="spare",
                    display="Spare Account",
                    earliest=None,
                    probed_back_to=date(2020, 8, 5),
                )
            ],
            lambda **_: "",
        )
        try:
            page = httpx.get(base).text
        finally:
            httpd.shutdown()

        assert "probed back to 2020-08-05" in page

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


class TestProbingGuidanceOnThePage:
    """The page says what pressing will meet: window freshness and walls.

    Advisory, never gates - the provider is the only authority, and a
    boundary is de-emphasised rather than forbidden.
    """

    def _server(self, tmp_path, extendables, extend_window=None, extend_max=None):
        config = WebConfig(
            client_id="client-1",
            client_secret="tlcs_live_abcdefghij1234567890",
            redirect_uri="https://obdi.example.com/callback",
            connection_store=ConnectionStore(tmp_path / "c.json"),
            extendables=extendables,
            extend_window=extend_window or (lambda **_: ""),
            extend_max=extend_max,
        )
        handler = type(
            "H", (ConnectionHandler,), {"config": config, "session": AuthorisationSession()}
        )
        httpd = HTTPServer(("127.0.0.1", 0), handler)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        return httpd, f"http://127.0.0.1:{httpd.server_port}"

    def test_Row_ShowsTheAuthenticationFreshnessNote(self, tmp_path):
        from obdi.web import ExtendableAccount

        httpd, base = self._server(
            tmp_path,
            lambda: [
                ExtendableAccount(
                    connection="halifax",
                    provider_ref="e9f8",
                    display="Current Account",
                    earliest=date(2024, 8, 2),
                    auth_note="deep-history window likely OPEN: about 3 min left",
                )
            ],
        )
        try:
            page = httpx.get(base).text
        finally:
            httpd.shutdown()

        assert "likely OPEN" in page

    def test_Row_WithAKnownBoundary_FoldsTheButtonsAwayButKeepsThem(self, tmp_path):
        from obdi.web import ExtendableAccount

        httpd, base = self._server(
            tmp_path,
            lambda: [
                ExtendableAccount(
                    connection="halifax",
                    provider_ref="e9f8",
                    display="Current Account",
                    earliest=date(2013, 4, 12),
                    boundary=date(2013, 4, 12),
                )
            ],
        )
        try:
            page = httpx.get(base).text
        finally:
            httpd.shutdown()

        assert "Boundary reached" in page and "2013-04-12" in page
        # De-emphasised: inside a fold. Not forbidden: still present.
        folded = page.split("<summary>Probe anyway</summary>", 1)
        assert len(folded) == 2
        assert 'name="days" value="1"' in folded[1]

    def test_ExtendMax_OnePress_RunsTheWalkAndShowsTheTranscript(self, tmp_path):
        calls = []

        def walk(connection, provider_ref, psu_ip):
            calls.append((connection, provider_ref, psu_ip))
            return "+730d: landed\n+1d: refused (invalid_date_range)\nThe boundary is found"

        httpd, base = self._server(tmp_path, lambda: [], extend_max=walk)
        try:
            page = httpx.post(
                f"{base}/extend-max",
                data={"connection": "halifax", "account": "e9f8"},
                headers={"X-Forwarded-For": "100.96.178.101"},
            ).text
        finally:
            httpd.shutdown()

        assert calls == [("halifax", "e9f8", "100.96.178.101")]
        assert "+730d: landed" in page
        assert "The boundary is found" in page
        # Transcript line breaks survive as breaks, not one blurred line.
        assert "<br>" in page


class TestUpdateAwareness:
    """The updater and the humans must not trample each other: starting a
    bank authorisation mid-update risks the five-minute SCA window, so
    the page defers it; a normal connect takes the bank-auth lease so the
    updater defers instead."""

    def _server(self, tmp_path, **hooks):
        config = WebConfig(
            client_id="client-1",
            client_secret="tlcs_live_abcdefghij1234567890",
            redirect_uri="https://obdi.example.com/callback",
            connection_store=ConnectionStore(tmp_path / "c.json"),
            **hooks,
        )
        handler = type(
            "H", (ConnectionHandler,), {"config": config, "session": AuthorisationSession()}
        )
        httpd = HTTPServer(("127.0.0.1", 0), handler)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        return httpd, f"http://127.0.0.1:{httpd.server_port}"

    def test_Connect_WhileStackUpdates_IsDeferredWithAnExplanation(self, tmp_path):
        httpd, base = self._server(tmp_path, update_in_progress=lambda: True)
        try:
            response = httpx.get(f"{base}/connect?name=halifax")
        finally:
            httpd.shutdown()

        assert response.status_code == 503
        assert "SCA" in response.text

    def test_Connect_TakesTheBankAuthLease(self, tmp_path):
        taken = []
        httpd, base = self._server(
            tmp_path,
            update_in_progress=lambda: False,
            auth_lease_take=lambda: taken.append(1),
        )
        try:
            httpx.get(f"{base}/connect?name=halifax", follow_redirects=False)
        finally:
            httpd.shutdown()

        assert taken == [1]

    def test_SchedulerPulse_FreshCycle_ReadsQuietly(self):
        from datetime import UTC, datetime

        from obdi.web import _scheduler_row

        row = _scheduler_row(
            lambda: {"at": "2026-08-02T12:00:00Z", "interval_seconds": 21600},
            now=datetime(2026, 8, 2, 13, 0, 0, tzinfo=UTC),
        )

        assert "12:00" in row
        assert "warn" not in row
        assert "next due" in row

    def test_SchedulerPulse_Overdue_WarnsAndNamesTheContainer(self):
        from datetime import UTC, datetime

        from obdi.web import _scheduler_row

        row = _scheduler_row(
            lambda: {"at": "2026-08-02T00:00:00Z", "interval_seconds": 21600},
            now=datetime(2026, 8, 2, 12, 0, 0, tzinfo=UTC),
        )

        assert "warn" in row
        assert "obdi-pull" in row

    def test_SchedulerPulse_NeverSeen_SaysNothing(self):
        from obdi.web import _scheduler_row

        assert _scheduler_row(lambda: {}) == ""
        assert _scheduler_row(None) == ""


class TestApplierLiveness:
    """A queued push that nobody consumes must diagnose itself: the
    applier stamps a heartbeat every poll, and the page compares it with
    the clock instead of leaving "queued 8 minutes" a mystery."""

    def test_FreshHeartbeat_ReadsAsAQuietFact(self):
        from datetime import UTC, datetime

        from obdi.web import _applier_liveness

        line = _applier_liveness(
            "2026-08-02T13:38:00.000Z",
            queued_count=1,
            now=datetime(2026, 8, 2, 13, 38, 30, tzinfo=UTC),
        )

        assert "13:38:00" in line
        assert "warn" not in line

    def test_StaleHeartbeat_WithWorkQueued_WarnsAndNamesTheContainer(self):
        from datetime import UTC, datetime

        from obdi.web import _applier_liveness

        line = _applier_liveness(
            "2026-08-02T13:30:00.000Z",
            queued_count=1,
            now=datetime(2026, 8, 2, 13, 38, 30, tzinfo=UTC),
        )

        assert "warn" in line
        assert "obdi-applier" in line

    def test_NoHeartbeatEver_WithWorkQueued_Warns(self):
        from datetime import UTC, datetime

        from obdi.web import _applier_liveness

        line = _applier_liveness(
            "", queued_count=1, now=datetime(2026, 8, 2, 13, 38, 30, tzinfo=UTC)
        )

        assert "warn" in line
        assert "never" in line

    def test_NoHeartbeat_NothingQueued_SaysNothing(self):
        from datetime import UTC, datetime

        from obdi.web import _applier_liveness

        assert (
            _applier_liveness(
                "", queued_count=0, now=datetime(2026, 8, 2, 13, 38, 30, tzinfo=UTC)
            )
            == ""
        )


class TestDangerZone:
    """Administrative repairs belong on the page, not in a shell - behind
    an explicit confirmation, with the consequences stated where the
    button is."""

    def _server(self, tmp_path, rebuild=None, forget=None):
        config = WebConfig(
            client_id="client-1",
            client_secret="tlcs_live_abcdefghij1234567890",
            redirect_uri="https://obdi.example.com/callback",
            connection_store=ConnectionStore(tmp_path / "c.json"),
            rebuild_derived=rebuild,
            forget_actual=forget,
        )
        handler = type(
            "H", (ConnectionHandler,), {"config": config, "session": AuthorisationSession()}
        )
        httpd = HTTPServer(("127.0.0.1", 0), handler)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        return httpd, f"http://127.0.0.1:{httpd.server_port}"

    def test_DangerZone_RendersWithConfirmCheckboxes_OnlyWhenWired(self, tmp_path):
        httpd, base = self._server(
            tmp_path, rebuild=lambda: "replayed", forget=lambda: 0
        )
        try:
            page = httpx.get(base).text
        finally:
            httpd.shutdown()

        assert "Danger zone" in page
        assert 'action="/rebuild-derived"' in page
        assert 'action="/forget-actual-bindings"' in page
        assert page.count('name="confirm"') == 2

        httpd, base = self._server(tmp_path)
        try:
            bare = httpx.get(base).text
        finally:
            httpd.shutdown()
        assert "Danger zone" not in bare

    def test_Rebuild_WithoutConfirmation_IsRefused(self, tmp_path):
        calls = []
        httpd, base = self._server(
            tmp_path, rebuild=lambda: calls.append(1) or "done"
        )
        try:
            response = httpx.post(f"{base}/rebuild-derived", data={})
        finally:
            httpd.shutdown()

        assert response.status_code == 400
        assert calls == []

    def test_Rebuild_Confirmed_RunsAndReportsTheSummary(self, tmp_path):
        httpd, base = self._server(
            tmp_path,
            rebuild=lambda: "replayed 42 artefact(s), 999 transaction(s) resolved",
        )
        try:
            response = httpx.post(
                f"{base}/rebuild-derived", data={"confirm": "yes"}
            )
        finally:
            httpd.shutdown()

        assert response.status_code == 200
        assert "999 transaction(s) resolved" in response.text

    def test_ForgetActual_Confirmed_ReportsTheCount(self, tmp_path):
        httpd, base = self._server(tmp_path, forget=lambda: 3)
        try:
            response = httpx.post(
                f"{base}/forget-actual-bindings", data={"confirm": "yes"}
            )
        finally:
            httpd.shutdown()

        assert response.status_code == 200
        assert "3" in response.text
        assert "re-provision" in response.text


class TestActualRoster:
    """The sync plan, readable on the page.

    "0 bound account(s), 2 to provision" was technically true and completely
    opaque - the roster states, per account, whether it imports, gets
    created on the next push, or is skipped for want of a name, and puts
    the remedy (a bind form pre-filled from the display label) in the row.
    """

    def test_Roster_ShowsEachStateWithItsExplanation(self):
        from obdi.web import _actual_rows

        rendered = _actual_rows(
            lambda: [],
            True,
            lambda: [
                {
                    "ref": "halifax-current-account",
                    "label": "halifax-current-account",
                    "state": "syncing",
                    "count": 947,
                },
                {
                    "ref": "halifax-reward-current-account",
                    "label": "halifax-reward-current-account",
                    "state": "provision",
                    "count": 0,
                },
                {
                    "ref": "starling:b2ce",
                    "label": "Personal (starling)",
                    "state": "unnamed",
                    "count": 4690,
                },
            ],
        )

        assert "syncing" in rendered
        assert "947" in rendered
        assert "creates on next push" in rendered
        assert "no transactions yet" in rendered
        assert "needs a name" in rendered
        # The remedy lives in the row: a bind form posting the qualified ref,
        # with the canonical name suggested from the display label.
        assert 'value="starling:b2ce"' in rendered
        assert 'value="starling-personal"' in rendered

    def test_QueuedPushes_AreShownAsInFlight_WithPickupExpectation(self):
        """Between the button press and the applier's answer, the push must
        be visible - with a statement of when it gets picked up."""
        from obdi.web import _actual_rows

        rendered = _actual_rows(
            lambda: [],
            True,
            None,
            lambda: [
                {
                    "name": "push-20260802T112545430836.json",
                    "queued_at": "2026-08-02T11:25:45",
                }
            ],
        )

        assert "11:25:45" in rendered
        assert "queued" in rendered
        assert "every 20 seconds" in rendered
        # The scheduled route is stated too, so "when will it sync by
        # itself" needs no shell either.
        assert "every six hours" in rendered

    def test_AuditResults_RenderPerAccount_DifferencesWarned(self):
        """The report the audit exists for: a clean account reads muted,
        an account with orphans or divergence is flagged, and the person's
        own rows appear as a count labelled theirs - never listed."""
        from obdi.web import _actual_rows

        rendered = _actual_rows(
            lambda: [
                {
                    "ok": True,
                    "kind": "audit",
                    "finished_at": "2026-08-02T13:00:00Z",
                    "accounts": [
                        {
                            "account_id": "act-1",
                            "name": "halifax-current-account",
                            "missing_account": False,
                            "expected": 947,
                            "present": 947,
                            "missing": 0,
                            "orphaned": 0,
                            "human": 2,
                            "diverged": 0,
                        },
                        {
                            "account_id": "act-2",
                            "name": "halifax-instant-saver",
                            "missing_account": False,
                            "expected": 6,
                            "present": 4,
                            "missing": 2,
                            "orphaned": 3,
                            "human": 0,
                            "diverged": 1,
                        },
                    ],
                }
            ],
            True,
        )

        assert "audit: differences" in rendered
        assert "yours 2" in rendered
        assert "orphaned 3" in rendered
        assert 'class="warn">halifax-instant-saver' in rendered
        assert 'class="muted">halifax-current-account' in rendered

    def test_AuditResults_CleanRun_GetsTheOkPill(self):
        from obdi.web import _actual_rows

        rendered = _actual_rows(
            lambda: [
                {
                    "ok": True,
                    "kind": "audit",
                    "finished_at": "2026-08-02T13:00:00Z",
                    "accounts": [
                        {
                            "account_id": "act-1",
                            "name": "halifax-current-account",
                            "missing_account": False,
                            "expected": 947,
                            "present": 947,
                            "missing": 0,
                            "orphaned": 0,
                            "human": 0,
                            "diverged": 0,
                        }
                    ],
                }
            ],
            True,
        )

        assert "audit clean" in rendered

    def test_AuditButton_RendersOnlyWhenWired(self):
        from obdi.web import _actual_rows

        with_button = _actual_rows(lambda: [], True, audit_available=True)
        without = _actual_rows(lambda: [], True, audit_available=False)

        assert 'action="/audit-actual"' in with_button
        assert 'action="/audit-actual"' not in without

    def test_QueuedAudit_IsLabelledDistinctlyFromAPush(self):
        from obdi.web import _actual_rows

        rendered = _actual_rows(
            lambda: [],
            True,
            None,
            lambda: [
                {
                    "name": "audit-20260802T130000000000.json",
                    "kind": "audit",
                    "queued_at": "2026-08-02T13:00:00",
                }
            ],
        )

        assert "queued (audit)" in rendered

    def test_Roster_HookFailure_DoesNotTakeDownTheSection(self):
        from obdi.web import _actual_rows

        def broken():
            raise RuntimeError("map unreadable")

        rendered = _actual_rows(lambda: [], True, broken)

        assert "Push to Actual now" in rendered

    def test_SuggestedName_ComesFromTheDisplayLabel(self):
        from obdi.web import _suggest_slug

        assert _suggest_slug("Personal (starling)", "starling:b2ce") == (
            "starling-personal"
        )
        assert _suggest_slug("mortgage (starling space)", "starling:e6a0") == (
            "starling-mortgage"
        )
        # No label worth suggesting: leave the input empty rather than
        # suggesting a slugified uuid.
        assert _suggest_slug("", "starling:e6a0") == ""
        # A label that IS the raw ref (no display name known) offers
        # nothing either - an empty box beats "starling-343fa965-8bb7-...".
        ref = "starling:343fa965-8bb7-470a-a4b8-c84843627be2"
        assert _suggest_slug(ref, ref) == ""


class TestBindingFromThePage:
    """Naming an account should not need a shell.

    The bind form appears exactly where the unnamed account is listed, and a
    successful bind moves the label across every layer - the hook's job -
    then shows the extend rows again so the new name is immediately visible.
    """

    def _server(self, tmp_path, extendables, bind_account):
        config = WebConfig(
            client_id="client-1",
            client_secret="tlcs_live_abcdefghij1234567890",
            redirect_uri="https://obdi.example.com/callback",
            connection_store=ConnectionStore(tmp_path / "c.json"),
            extendables=extendables,
            bind_account=bind_account,
        )
        handler = type(
            "H", (ConnectionHandler,), {"config": config, "session": AuthorisationSession()}
        )
        httpd = HTTPServer(("127.0.0.1", 0), handler)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        return httpd, f"http://127.0.0.1:{httpd.server_port}"

    def test_UnboundAccount_GetsABindFormInItsRow(self, tmp_path):
        from obdi.web import ExtendableAccount

        httpd, base = self._server(
            tmp_path,
            lambda: [
                ExtendableAccount(
                    connection="halifax",
                    provider_ref="e9f8",
                    display="CURRENT ACCOUNT (TRANSACTION)",
                    earliest=date(2020, 8, 7),
                    canonical="truelayer:e9f8",
                    unbound=True,
                ),
                ExtendableAccount(
                    connection="halifax",
                    provider_ref="b532",
                    display="Instant Saver",
                    earliest=date(2021, 7, 7),
                    canonical="halifax-saver",
                    unbound=False,
                ),
            ],
            lambda *_: "",
        )
        try:
            page = httpx.get(base).text
        finally:
            httpd.shutdown()

        # One form, on the unbound row only.
        assert page.count('action="/bind"') == 1
        assert 'name="account" value="e9f8"' in page

    def test_Bind_CallsTheHook_AndShowsTheResult(self, tmp_path):
        calls = []

        def bind(account, canonical):
            calls.append((account, canonical))
            return "bound e9f8... -> halifax-current: 947 stored row(s) moved"

        httpd, base = self._server(tmp_path, lambda: [], bind)
        try:
            page = httpx.post(
                f"{base}/bind",
                data={"account": "e9f8", "canonical": "halifax-current"},
            ).text
        finally:
            httpd.shutdown()

        assert calls == [("e9f8", "halifax-current")]
        assert "947 stored row(s) moved" in page

    def test_Bind_MovesRowsBeforePersistingTheName(self, tmp_path):
        """A fresh bind: rows keyed by the qualified fallback id move to
        the chosen canonical and the map records the binding."""
        import json as _json

        from obdi.accounts import AccountMap
        from obdi.cli import _apply_bind
        from obdi.store import Store as _Store

        db = tmp_path / "s.sqlite3"
        with _Store(db) as store:
            store.connection.execute(
                "INSERT INTO transactions (entity_id, account_id, amount_minor, "
                "value_date, booking_date, description, source, currency, tier, "
                "status, content_key, occurrence, first_seen_at, last_seen_at) "
                "VALUES ('e-1', 'starling:uid-1', -100, '2026-07-01', "
                "'2026-07-01', 'X', 'starling', 'GBP', 'authoritative', "
                "'booked', 'ck-1', 0, '2026-07-01T00:00:00', "
                "'2026-07-01T00:00:00')"
            )
            store.connection.commit()
        map_file = tmp_path / "accounts.json"

        moved = _apply_bind(
            db, map_file, AccountMap(), "starling", "uid-1", "starling-bills"
        )

        assert moved == 1
        stored = _json.loads(map_file.read_text(encoding="utf-8"))
        assert stored["bindings"][0]["canonical_id"] == "starling-bills"
        with _Store(db) as store:
            rows = store.connection.execute(
                "SELECT account_id FROM transactions"
            ).fetchall()
        assert [r[0] for r in rows] == ["starling-bills"]

    def test_Bind_AfterEarlierHalfAppliedBind_RescuesTheStrandedRows(self, tmp_path):
        """The failure the lock left behind: the map says "starling-bills"
        but the rows still sit under the qualified ref. Re-pressing Bind
        (same name or a new one) must move the stranded rows, not no-op
        because the map already resolves."""
        from obdi.accounts import AccountBinding, AccountMap
        from obdi.cli import _apply_bind
        from obdi.store import Store as _Store

        db = tmp_path / "s.sqlite3"
        with _Store(db) as store:
            store.connection.execute(
                "INSERT INTO transactions (entity_id, account_id, amount_minor, "
                "value_date, booking_date, description, source, currency, tier, "
                "status, content_key, occurrence, first_seen_at, last_seen_at) "
                "VALUES ('e-1', 'starling:uid-1', -100, '2026-07-01', "
                "'2026-07-01', 'X', 'starling', 'GBP', 'authoritative', "
                "'booked', 'ck-1', 0, '2026-07-01T00:00:00', "
                "'2026-07-01T00:00:00')"
            )
            store.connection.commit()
        map_file = tmp_path / "accounts.json"
        half_applied = AccountMap(
            [AccountBinding("starling", "uid-1", "starling-bills")]
        )

        moved = _apply_bind(
            db, map_file, half_applied, "starling", "uid-1", "starling-bills"
        )

        assert moved == 1
        with _Store(db) as store:
            rows = store.connection.execute(
                "SELECT account_id FROM transactions"
            ).fetchall()
        assert [r[0] for r in rows] == ["starling-bills"]

    def test_SourceQualifiedRef_BindsUnderItsOwnSource(self):
        """The holdings and roster forms post "starling:uid"; the extend rows
        post bare TrueLayer refs. Both routes must land in the right column
        of the account map."""
        from obdi.cli import _split_bind_ref

        assert _split_bind_ref("starling:abc-def") == ("starling", "abc-def")
        assert _split_bind_ref("e9f8") == ("truelayer", "e9f8")

    def test_Bind_RejectionsFromTheHook_AreShownNotSwallowed(self, tmp_path):
        def bind(account, canonical):
            raise ValueError("canonical name must be 2-40 characters")

        httpd, base = self._server(tmp_path, lambda: [], bind)
        try:
            response = httpx.post(
                f"{base}/bind",
                data={"account": "e9f8", "canonical": "Not A Slug!"},
            )
        finally:
            httpd.shutdown()

        assert response.status_code == 400
        assert "2-40 characters" in response.text


class TestAccountLevelShape:
    """The merged layer, summarised like an artefact payload.

    A dozen overlapping raw artefacts answer "what arrived"; this answers
    "what does the store believe" - one table per account.
    """

    def _server(self, tmp_path, account_shape):
        config = WebConfig(
            client_id="client-1",
            client_secret="tlcs_live_abcdefghij1234567890",
            redirect_uri="https://obdi.example.com/callback",
            connection_store=ConnectionStore(tmp_path / "c.json"),
            account_shape=account_shape,
        )
        handler = type(
            "H", (ConnectionHandler,), {"config": config, "session": AuthorisationSession()}
        )
        httpd = HTTPServer(("127.0.0.1", 0), handler)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        return httpd, f"http://127.0.0.1:{httpd.server_port}"

    def test_AccountPage_RendersTheMergedShapeWithItsSources(self, tmp_path):
        def shape(ref):
            assert ref == "halifax-current"
            return {
                "ref": ref,
                "count": 2,
                "sources": ["halifax-csv", "truelayer"],
                "summary": {
                    "kind": "json",
                    "items": 2,
                    "bytes": 321,
                    "fields": [
                        {
                            "path": "source",
                            "present": 2,
                            "types": ["string"],
                            "distinct": 2,
                            "min": "halifax-csv",
                            "max": "truelayer",
                            "values": [
                                {"value": "halifax-csv", "count": 1},
                                {"value": "truelayer", "count": 1},
                            ],
                        }
                    ],
                    "sign_by": [],
                    "presence_links": [],
                    "by_month": [{"month": "2026-07", "count": 2}],
                },
            }

        httpd, base = self._server(tmp_path, shape)
        try:
            page = httpx.get(f"{base}/account?ref=halifax-current").text
        finally:
            httpd.shutdown()

        assert "MERGED layer" in page
        assert "halifax-csv, truelayer" in page
        assert "2 distinct: halifax-csv x1, truelayer x1" in page
        assert "Items per month" in page

    def test_AccountPage_WhenNothingHeld_SaysSo(self, tmp_path):
        httpd, base = self._server(tmp_path, lambda ref: None)
        try:
            response = httpx.get(f"{base}/account?ref=unknown")
        finally:
            httpd.shutdown()

        assert response.status_code == 404
        assert "No merged transactions" in response.text


class TestNamesLeadAndDormancySpeaks:
    """The providers told us every name from the first pull; pages use them.

    Ids demote to small print, and an account whose latest transaction is
    over a year old carries a neutral quiet-since chip - the date range
    matters most exactly when it is old.
    """

    def _server(self, tmp_path, holdings, display_labels):
        config = WebConfig(
            client_id="client-1",
            client_secret="tlcs_live_abcdefghij1234567890",
            redirect_uri="https://obdi.example.com/callback",
            connection_store=ConnectionStore(tmp_path / "c.json"),
            holdings=holdings,
            display_labels=display_labels,
        )
        handler = type(
            "H", (ConnectionHandler,), {"config": config, "session": AuthorisationSession()}
        )
        httpd = HTTPServer(("127.0.0.1", 0), handler)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        return httpd, f"http://127.0.0.1:{httpd.server_port}"

    def test_Holdings_ShowNamesWithIdsDemoted_AndQuietAccountsSaySo(self, tmp_path):
        from datetime import UTC, datetime, timedelta

        from obdi.coverage import SourceCoverage

        today = datetime.now(UTC).date()
        rows = [
            SourceCoverage(
                account_id="starling:343fa965-8bb7",
                source="starling",
                count=202,
                earliest=today - timedelta(days=2000),
                latest=today - timedelta(days=1400),
                inflow_minor=0,
                outflow_minor=0,
                with_durable_id=202,
            ),
            SourceCoverage(
                account_id="starling:b2cec056-b0d8",
                source="starling",
                count=4690,
                earliest=today - timedelta(days=2000),
                latest=today,
                inflow_minor=0,
                outflow_minor=0,
                with_durable_id=4690,
            ),
        ]
        labels = {
            "starling:343fa965-8bb7": "Holiday Fund (starling space)",
            "starling:b2cec056-b0d8": "Main (starling)",
        }

        httpd, base = self._server(tmp_path, lambda: rows, lambda: labels)
        try:
            page = httpx.get(base).text
        finally:
            httpd.shutdown()

        assert "Holiday Fund (starling space)" in page
        assert "Main (starling)" in page
        # The id survives as small print, still the link target's query key.
        assert "starling:343fa965-8bb7" in page
        # One quiet chip: the archived space announces itself; the live
        # account carries none.
        assert page.count("quiet since") == 1


class TestTimelineSegments:
    """One comparable axis; segment style is the claim being made.

    Solid only where transactions are HELD; faint where asked and empty
    (which is how a dormant tail becomes a long pale stretch); dotted before
    a known provider boundary; dashed where nothing was ever asked; blank
    future after today.
    """

    def test_HalifaxShape_TruncatedThenHeldThenFuture(self):
        from obdi.web import timeline_segments

        segments = timeline_segments(
            date(2019, 1, 1),
            date(2026, 9, 1),
            earliest=date(2020, 8, 7),
            latest=date(2026, 7, 31),
            today=date(2026, 8, 2),
            boundary=date(2020, 8, 1),
            probed=date(2020, 8, 1),
            covered=date(2026, 8, 2),
        )

        kinds = [kind for kind, _ in segments]
        assert kinds == ["truncated", "empty", "held", "empty", "future"]
        assert abs(sum(width for _, width in segments) - 100) < 0.5

    def test_ArchivedSpace_DormantTailReadsAsALongEmptyStretch(self):
        from obdi.web import timeline_segments

        segments = timeline_segments(
            date(2019, 1, 1),
            date(2026, 9, 1),
            earliest=date(2019, 1, 21),
            latest=date(2022, 9, 27),
            today=date(2026, 8, 2),
            covered=date(2026, 8, 2),
        )

        kinds = dict(segments)
        # The dormancy tail (2022 -> today) dwarfs everything else.
        assert kinds["empty"] > kinds["held"] * 0.8
        assert "truncated" not in kinds

    def test_FutureIsCapped_NotFourYearsOfBlankTape(self):
        from obdi.web import timeline_segments

        segments = timeline_segments(
            date(2026, 1, 1),
            date(2026, 10, 1),
            earliest=date(2026, 2, 1),
            latest=date(2026, 8, 1),
            today=date(2026, 8, 2),
        )

        assert segments[-1][0] == "future"


class TestUploadingAFileFromThePage:
    """Preview commits nothing; confirm lands through the CLI's machinery.

    A wrong file inspected costs nothing - the parse happens in memory,
    the bytes wait in a stash, and only the confirm click stores anything.
    """

    def _server(self, tmp_path, preview_upload, confirm_upload, labels=None):
        config = WebConfig(
            client_id="client-1",
            client_secret="tlcs_live_abcdefghij1234567890",
            redirect_uri="https://obdi.example.com/callback",
            connection_store=ConnectionStore(tmp_path / "c.json"),
            preview_upload=preview_upload,
            confirm_upload=confirm_upload,
            display_labels=(lambda: labels or {}),
        )
        handler = type(
            "H", (ConnectionHandler,), {"config": config, "session": AuthorisationSession()}
        )
        httpd = HTTPServer(("127.0.0.1", 0), handler)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        return httpd, f"http://127.0.0.1:{httpd.server_port}"

    def test_Upload_ShowsAPreview_AndStoresNothingUntilConfirmed(self, tmp_path):
        previews = []
        confirms = []

        def preview(payload, filename):
            previews.append((payload, filename))
            return {
                "parser": "StarlingCsvParser",
                "date_format": "%d/%m/%Y",
                "rows": 42,
                "sample": [
                    {"date": "2026-07-01", "amount": "-12.34", "description": "COFFEE"}
                ],
                "date_ambiguous": False,
                "earliest": "2026-01-01",
                "latest": "2026-07-01",
            }

        httpd, base = self._server(
            tmp_path,
            preview,
            lambda *a: confirms.append(a) or "done",
            labels={"halifax-current": "Current (halifax)"},
        )
        try:
            page = httpx.post(
                f"{base}/upload",
                files={"statement": ("statement.csv", b"Date,Amount\n", "text/csv")},
            ).text
        finally:
            httpd.shutdown()

        assert previews == [(b"Date,Amount\n", "statement.csv")]
        assert confirms == []  # nothing landed from a preview
        assert "StarlingCsvParser" in page
        assert "42 row(s)" in page
        assert "COFFEE" in page
        assert "Current (halifax)" in page  # the account picker is populated
        assert 'name="token"' in page

    def test_Confirm_LandsThePreviewedBytesAgainstTheChosenAccount(self, tmp_path):
        confirms = []

        def confirm(payload, filename, account):
            confirms.append((payload, filename, account))
            return "statement.csv -> halifax-current: 42 parsed"

        httpd, base = self._server(
            tmp_path,
            lambda payload, filename: {
                "parser": "QifParser",
                "date_format": "",
                "rows": 1,
                "sample": [],
                "date_ambiguous": False,
                "earliest": None,
                "latest": None,
            },
            confirm,
        )
        try:
            with httpx.Client() as client:
                preview_page = client.post(
                    f"{base}/upload",
                    files={"statement": ("mine.qif", b"!Type:Bank\n", "text/plain")},
                ).text
                token = preview_page.split('name="token" value="')[1].split('"')[0]
                result = client.post(
                    f"{base}/upload-confirm",
                    data={"token": token, "account": "halifax-current"},
                ).text
        finally:
            httpd.shutdown()

        assert confirms == [(b"!Type:Bank\n", "mine.qif", "halifax-current")]
        assert "42 parsed" in result

    def test_Upload_UnrecognisedFile_IsRefusedWithTheParserVerdict(self, tmp_path):
        def preview(payload, filename):
            raise ValueError("No parser recognised this file's header row.")

        httpd, base = self._server(tmp_path, preview, lambda *a: "")
        try:
            response = httpx.post(
                f"{base}/upload",
                files={"statement": ("junk.bin", b"\x00\x01", "application/octet-stream")},
            )
        finally:
            httpd.shutdown()

        assert response.status_code == 400
        assert "No parser recognised" in response.text


class TestReconnectPinsTheBankPicker:
    def test_Reconnect_CarriesThePinnedProvider_InTheAuthLink(self, tmp_path):
        config = WebConfig(
            client_id="client-1",
            client_secret="tlcs_live_abcdefghij1234567890",
            redirect_uri="https://obdi.example.com/callback",
            connection_store=ConnectionStore(tmp_path / "c.json"),
            pinned_providers=lambda name: "ob-halifax" if name == "halifax" else None,
        )
        handler = type(
            "H", (ConnectionHandler,), {"config": config, "session": AuthorisationSession()}
        )
        httpd = HTTPServer(("127.0.0.1", 0), handler)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        try:
            base = f"http://127.0.0.1:{httpd.server_port}"
            reconnect = httpx.get(f"{base}/connect?name=halifax", follow_redirects=False)
            fresh = httpx.get(f"{base}/connect?name=newbank", follow_redirects=False)
        finally:
            httpd.shutdown()

        # The reconnect shows ONLY the bank this connection already uses;
        # a brand-new name still gets the full picker.
        assert "providers=ob-halifax" in reconnect.headers["location"]
        assert "uk-ob-all" in fresh.headers["location"].replace("+", " ")


class TestBrowsingTheAttemptLedger:
    """Every ask made of a provider, on a page: the quota ledger readable.

    The probing workflow is press, read, decide - and the deciding needs
    "what has already been asked in the last day, and what got refused with
    which code?" answered without a shell.
    """

    def _server(self, tmp_path, attempts_index):
        config = WebConfig(
            client_id="client-1",
            client_secret="tlcs_live_abcdefghij1234567890",
            redirect_uri="https://obdi.example.com/callback",
            connection_store=ConnectionStore(tmp_path / "c.json"),
            attempts_index=attempts_index,
        )
        handler = type(
            "H", (ConnectionHandler,), {"config": config, "session": AuthorisationSession()}
        )
        httpd = HTTPServer(("127.0.0.1", 0), handler)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        return httpd, f"http://127.0.0.1:{httpd.server_port}"

    def test_Ledger_ShowsAttemptsWithOutcomeAndDayCounts(self, tmp_path):
        httpd, base = self._server(
            tmp_path,
            lambda: {
                "rows": [
                    {
                        "attempted_at": "2026-08-02T01:10:00+00:00",
                        "source": "truelayer-booked",
                        "connection_id": "halifax",
                        "account_ref": "halifax-current",
                        "asked": "since=2022-08-03 until=2024-08-03",
                        "request_meta": '{"trigger": "web-extend"}',
                        "outcome": "refused",
                        "http_status": 403,
                        "error_code": "sca_exceeded",
                        "detail": "Transaction fetch failed (HTTP 403): sca_exceeded",
                    },
                    {
                        "attempted_at": "2026-08-02T00:00:00+00:00",
                        "source": "truelayer-booked",
                        "connection_id": "halifax",
                        "account_ref": "halifax-current",
                        "asked": "from=2026-05-04&to=2026-08-02",
                        "request_meta": '{"trigger": "scheduled"}',
                        "outcome": "landed",
                        "http_status": 200,
                        "error_code": "",
                        "detail": "",
                    },
                ],
                "last_day": [
                    {"connection_id": "halifax", "account_ref": "halifax-current", "count": 5}
                ],
            },
        )
        try:
            page = httpx.get(f"{base}/attempts").text
        finally:
            httpd.shutdown()

        assert "sca_exceeded" in page
        assert "refused" in page and "landed" in page
        # The recorded detail is one fold away on refused rows - the page is
        # where a 429's body excerpt gets read, not the database.
        assert "provider detail" in page
        assert "Transaction fetch failed (HTTP 403)" in page
        assert "web-extend" in page and "scheduled" in page
        # The quota view: per-account calls over the last 24 hours.
        assert "halifax-current" in page and ">5<" in page
        # The known under-count is stated, not hidden.
        assert "under-count" in page

    def test_Ledger_WhenNothingRecorded_SaysSoPlainly(self, tmp_path):
        httpd, base = self._server(tmp_path, lambda: {"rows": [], "last_day": []})
        try:
            page = httpx.get(f"{base}/attempts").text
        finally:
            httpd.shutdown()

        assert "No attempts recorded yet" in page


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

    def test_Detail_RendersTalliesSignAgreementPresenceAndMonths(self, tmp_path):
        def detail(_id, with_payload=False):
            return {
                "id": 7,
                "source": "truelayer-booked",
                "account_ref": "halifax-current",
                "fetched_at": "2026-08-02T01:00:00+00:00",
                "origin": "https://api/transactions",
                "request_meta": {"trigger": "web-extend"},
                "summary": {
                    "kind": "json",
                    "items": 3,
                    "bytes": 999,
                    "fields": [
                        {
                            "path": "transaction_type",
                            "present": 3,
                            "types": ["string"],
                            "distinct": 2,
                            "min": "CREDIT",
                            "max": "DEBIT",
                            "values": [
                                {"value": "DEBIT", "count": 2},
                                {"value": "CREDIT", "count": 1},
                            ],
                        },
                        {
                            "path": "ref",
                            "present": 3,
                            "types": ["string"],
                            "distinct": 3,
                            "min": None,
                            "max": None,
                            "length": {"min": 20, "max": 20},
                            "prefix": "txn-",
                            "format": "hex",
                        },
                    ],
                    "sign_by": [
                        {
                            "field": "transaction_type",
                            "value": "DEBIT",
                            "positive": 0,
                            "negative": 2,
                            "zero": 0,
                        }
                    ],
                    "presence_links": [
                        {
                            "field": "provider_reference",
                            "by": "transaction_category",
                            "value": "TRANSFER",
                            "present": 0,
                            "total": 6,
                            "overall_present": 10,
                        }
                    ],
                    "by_month": [
                        {"month": "2026-05", "count": 2},
                        {"month": "2026-07", "count": 1},
                    ],
                },
            }

        httpd, base = self._server(tmp_path, lambda: [], detail)
        try:
            page = httpx.get(f"{base}/artefact?id=7").text
        finally:
            httpd.shutdown()

        # Categories are tallied, identifiers described by shape not range.
        assert "2 distinct: DEBIT x2, CREDIT x1" in page
        assert "3 distinct, length 20-20, prefix txn-, hex" in page
        # Cross-field evidence sections.
        assert "Amount sign by category" in page
        assert "Presence patterns" in page
        assert "0 of 6 items where" in page
        assert "Items per month" in page
        assert "2026-05" in page and "2026-07" in page

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
