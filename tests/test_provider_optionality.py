"""Bank authorisation is optional; a half-configured one is not.

The rule, and it is the MOT rule: if a provider is installed or configured at all,
it has to work. If it is not there at all, that is a statement of intent rather than
an oversight, and everything that does not need it carries on.

This matters beyond tidiness. Most of what this application does - reading
statements, importing, matching, categorising, reporting coverage - needs no bank
connection whatever, and an instance may deliberately hold none: a restore target,
a synthetic environment, or simply a deployment that has moved to another provider
or to statements alone. Until now `serve` refused to start without TrueLayer, so
those instances could not run at all.

The distinction that carries the weight is ABSENT versus PARTIAL. Absent is a
decision and degrades. Partial is a mistake - a rotated secret half-deployed, a
redirect URI dropped from an env file - and it must fail loudly, because the
symptom otherwise arrives much later as a bank authorisation that cannot complete.
"""

from __future__ import annotations

import pytest

from obdi.secrets import truelayer_readiness


def _configure(monkeypatch, **values: str | None) -> None:
    """Set or clear the variables that decide whether a provider is configured."""
    for name in (
        "TRUELAYER_CLIENT_ID",
        "TRUELAYER_REDIRECT_URI",
        "TRUELAYER_CLIENT_SECRET",
        "TRUELAYER_CLIENT_SECRET_FILE",
    ):
        monkeypatch.delenv(name, raising=False)
    for name, value in values.items():
        if value is not None:
            monkeypatch.setenv(name, value)


class TestWhetherABankProviderIsConfiguredAtAll:
    def test_BankAuthorisation_WhenNothingIsConfigured_IsAbsentRatherThanBroken(self, monkeypatch):
        _configure(monkeypatch)
        readiness = truelayer_readiness()
        assert readiness.state == "absent"
        assert readiness.problem is None

    def test_BankAuthorisation_WhenTheValuesArePresentButEmpty_IsStillAbsent(self, monkeypatch):
        # How a deployment actually spells "not configured": the env file is
        # written from a template, so the names exist and the values do not.
        _configure(monkeypatch, TRUELAYER_CLIENT_ID="", TRUELAYER_REDIRECT_URI="")
        assert truelayer_readiness().state == "absent"

    def test_BankAuthorisation_WhenTheValuesAreOnlyWhitespace_IsStillAbsent(self, monkeypatch):
        _configure(monkeypatch, TRUELAYER_CLIENT_ID="   ", TRUELAYER_REDIRECT_URI="\t")
        assert truelayer_readiness().state == "absent"

    def test_BankAuthorisation_WhenAStaleSecretPathOutlivesTheConfiguration_IsStillAbsent(
        self, monkeypatch, tmp_path
    ):
        # The case that prompted this. A shared compose file points every instance
        # at the same secret path, so an instance with no provider still inherits
        # the POINTER while the file behind it was never written. Judging by the
        # pointer's existence would call that a misconfiguration and refuse to
        # start - which is how an instance defined as holding no credentials ends
        # up unable to run.
        _configure(
            monkeypatch,
            TRUELAYER_CLIENT_ID="",
            TRUELAYER_REDIRECT_URI="",
            TRUELAYER_CLIENT_SECRET_FILE=str(tmp_path / "never-written"),
        )
        readiness = truelayer_readiness()
        assert readiness.state == "absent"
        assert readiness.problem is None


class TestWhenABankProviderIsOnlyHalfConfigured:
    def test_BankAuthorisation_WhenOnlyTheClientIdIsSet_IsMisconfiguredAndSaysWhichIsMissing(
        self, monkeypatch
    ):
        _configure(monkeypatch, TRUELAYER_CLIENT_ID="an-id", TRUELAYER_CLIENT_SECRET="a-secret")
        readiness = truelayer_readiness()
        assert readiness.state == "misconfigured"
        assert "TRUELAYER_REDIRECT_URI" in (readiness.problem or "")

    def test_BankAuthorisation_WhenOnlyTheRedirectUriIsSet_IsMisconfiguredAndSaysWhichIsMissing(
        self, monkeypatch
    ):
        _configure(
            monkeypatch,
            TRUELAYER_REDIRECT_URI="https://example.test/callback",
            TRUELAYER_CLIENT_SECRET="a-secret",
        )
        readiness = truelayer_readiness()
        assert readiness.state == "misconfigured"
        assert "TRUELAYER_CLIENT_ID" in (readiness.problem or "")

    def test_BankAuthorisation_WhenIdentifiersAreSetButTheSecretIsMissing_IsMisconfigured(
        self, monkeypatch
    ):
        # Intent is stated, so the secret has to be there. This is the rotation
        # half-applied - and degrading here would hide it until somebody tried to
        # authorise a bank, which is the worst moment to discover it.
        _configure(
            monkeypatch,
            TRUELAYER_CLIENT_ID="an-id",
            TRUELAYER_REDIRECT_URI="https://example.test/callback",
        )
        readiness = truelayer_readiness()
        assert readiness.state == "misconfigured"
        assert "TRUELAYER_CLIENT_SECRET" in (readiness.problem or "")

    def test_BankAuthorisation_WhenTheSecretFileIsNamedButAbsent_IsMisconfiguredAndNamesThePath(
        self, monkeypatch, tmp_path
    ):
        missing = tmp_path / "truelayer-client-secret"
        _configure(
            monkeypatch,
            TRUELAYER_CLIENT_ID="an-id",
            TRUELAYER_REDIRECT_URI="https://example.test/callback",
            TRUELAYER_CLIENT_SECRET_FILE=str(missing),
        )
        readiness = truelayer_readiness()
        assert readiness.state == "misconfigured"
        assert str(missing) in (readiness.problem or "")

    def test_BankAuthorisation_WhenTheSecretFileIsEmpty_IsMisconfigured(
        self, monkeypatch, tmp_path
    ):
        blank = tmp_path / "truelayer-client-secret"
        blank.write_text("", encoding="utf-8")
        _configure(
            monkeypatch,
            TRUELAYER_CLIENT_ID="an-id",
            TRUELAYER_REDIRECT_URI="https://example.test/callback",
            TRUELAYER_CLIENT_SECRET_FILE=str(blank),
        )
        assert truelayer_readiness().state == "misconfigured"


class TestWhenABankProviderIsFullyConfigured:
    def test_BankAuthorisation_WhenIdentifiersAndSecretAreAllPresent_IsReady(self, monkeypatch):
        _configure(
            monkeypatch,
            TRUELAYER_CLIENT_ID="an-id",
            TRUELAYER_REDIRECT_URI="https://example.test/callback",
            TRUELAYER_CLIENT_SECRET="a-secret",
        )
        readiness = truelayer_readiness()
        assert readiness.state == "ready"
        assert readiness.problem is None

    def test_BankAuthorisation_WhenTheSecretComesFromAReadableFile_IsReady(
        self, monkeypatch, tmp_path
    ):
        secret = tmp_path / "truelayer-client-secret"
        secret.write_text("a-secret\n", encoding="utf-8")
        _configure(
            monkeypatch,
            TRUELAYER_CLIENT_ID="an-id",
            TRUELAYER_REDIRECT_URI="https://example.test/callback",
            TRUELAYER_CLIENT_SECRET_FILE=str(secret),
        )
        assert truelayer_readiness().state == "ready"


class TestWhatServeDoesWithEachOfThose:
    """The startup gate, which is where the refusal was fatal to a whole instance."""

    @staticmethod
    def _serve_with(monkeypatch, tmp_path, **values) -> tuple[int, dict[str, bool]]:
        from obdi import cli

        _configure(monkeypatch, **values)
        monkeypatch.setenv("OBDI_CONNECTION_STORE", str(tmp_path / "connections.json"))

        reached = {}

        def _stop_before_binding(*args, **kwargs):
            reached["served"] = True
            raise SystemExit(0)

        monkeypatch.setattr(cli, "serve_web", _stop_before_binding)
        try:
            code = cli._serve("127.0.0.1", 0, tmp_path / "store.sqlite3")
        except SystemExit:
            return 0, reached
        return code, reached

    def test_Serve_WhenNoProviderIsConfigured_StartsAnywayForEverythingElse(
        self, monkeypatch, tmp_path
    ):
        code, reached = self._serve_with(
            monkeypatch, tmp_path, TRUELAYER_CLIENT_ID="", TRUELAYER_REDIRECT_URI=""
        )
        assert reached.get("served"), "serve refused to start without a bank provider"
        assert code == 0

    def test_Serve_WhenTheProviderIsHalfConfigured_RefusesToStart(self, monkeypatch, tmp_path):
        code, reached = self._serve_with(monkeypatch, tmp_path, TRUELAYER_CLIENT_ID="an-id")
        assert not reached.get("served"), "serve started on a half-configured provider"
        assert code == 2

    def test_Serve_WhenTheProviderIsConfiguredAndComplete_Starts(self, monkeypatch, tmp_path):
        code, reached = self._serve_with(
            monkeypatch,
            tmp_path,
            TRUELAYER_CLIENT_ID="an-id",
            TRUELAYER_REDIRECT_URI="https://example.test/callback",
            TRUELAYER_CLIENT_SECRET="a-secret",
        )
        assert reached.get("served")
        assert code == 0


class TestWhatThePageSaysWhenThereIsNoProvider:
    """A capability that is switched off must LOOK switched off.

    The failure being avoided is a page that simply omits the way in. Somebody
    arriving at it cannot tell the difference between "this deployment does not do
    banks" and "the bank section is broken today" - and the second reading is the
    one people act on, by restarting things that were never wrong.
    """

    @staticmethod
    def _page(tmp_path, path: str = "/", **overrides) -> str:
        import threading
        from http.server import HTTPServer

        import httpx

        from obdi.connections import ConnectionStore
        from obdi.web import AuthorisationSession, ConnectionHandler, WebConfig

        config = WebConfig(
            client_id=overrides.pop("client_id", "client-1"),
            client_secret="tlcs_live_abcdefghij1234567890",
            redirect_uri=overrides.pop("redirect_uri", "https://obdi.example.com/callback"),
            connection_store=ConnectionStore(tmp_path / "c.json"),
            **overrides,
        )
        handler = type(
            "H", (ConnectionHandler,), {"config": config, "session": AuthorisationSession()}
        )
        httpd = HTTPServer(("127.0.0.1", 0), handler)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        try:
            return httpx.get(f"http://127.0.0.1:{httpd.server_port}{path}").text
        finally:
            httpd.shutdown()

    def test_IndexPage_WhenNoProviderIsConfigured_SaysSoRatherThanOmittingTheSection(
        self, tmp_path
    ):
        page = self._page(tmp_path, bank_authorisation=False)
        assert "bank authorisation" in page.lower()
        assert "not configured" in page.lower()

    def test_IndexPage_WhenNoProviderIsConfigured_OffersNoWayToStartOne(self, tmp_path):
        page = self._page(tmp_path, bank_authorisation=False)
        assert 'action="/connect"' not in page, "a form that cannot possibly complete"

    def test_IndexPage_WhenAProviderIsConfigured_StillOffersToAddABank(self, tmp_path):
        page = self._page(tmp_path)
        assert 'action="/connect"' in page

    def test_IndexPage_WhenNoProviderIsConfigured_RaisesNoAlarmAboutTheMissingSecret(
        self, tmp_path, monkeypatch
    ):
        # The third surface, and the one that was missed. A deployment with no
        # provider points at a secret file that was never written - correctly, since
        # it holds no credentials - and the credential banner read that as a FAULT:
        # "unreadable ... bank authorisation will fail until this is fixed", in red,
        # on an instance where nothing is wrong and nothing is expected to work.
        #
        # Loud enough to send somebody hunting a deployment problem that does not
        # exist, which is the precise failure the switched-off-must-look-switched-off
        # rule exists to prevent.
        _configure(monkeypatch, TRUELAYER_CLIENT_SECRET_FILE=str(tmp_path / "never-written"))
        page = self._page(tmp_path, bank_authorisation=False)
        assert "unreadable" not in page.lower(), "an absent provider reported as a fault"
        assert "will fail until this is fixed" not in page.lower()

    def test_IndexPage_WhenAProviderIsConfiguredButItsSecretIsGone_StillRaisesTheAlarm(
        self, tmp_path, monkeypatch
    ):
        # The other half, and why this is not simply "hide the banner". A secret can
        # go unreadable AFTER startup - rotated badly, unmounted - on a deployment
        # that genuinely uses a provider. That is a real fault and must stay loud.
        _configure(monkeypatch, TRUELAYER_CLIENT_SECRET_FILE=str(tmp_path / "never-written"))
        page = self._page(tmp_path, bank_authorisation=True)
        assert "unreadable" in page.lower()

    def test_ConnectRoute_WhenNoProviderIsConfigured_RefusesAndSaysWhy(self, tmp_path):
        # Reachable by a bookmark or a stale link even with the form gone, and a
        # bare traceback here would read as a fault rather than as a settled
        # configuration.
        page = self._page(tmp_path, path="/connect?name=test", bank_authorisation=False)
        assert "not configured" in page.lower()


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
