"""Rotating a secret must take effect without restarting anything.

The failure this prevents was observed live: the corrected secret sat on disk,
a fresh doctor process approved it, and the serving process carried on using
the mangled value it had read at startup - the fix looked applied everywhere
anyone checked and was applied nowhere it mattered. The cure is not restart
choreography but not caching: the secret is used only at code exchange, a
handful of times a quarter, so reading the file at USE costs nothing and makes
rotation atomic with the write.
"""

from __future__ import annotations

import threading
from http.server import HTTPServer

import httpx

from obdi.connections import ConnectionStore
from obdi.web import AuthorisationSession, ConnectionHandler, WebConfig


class TestTheSecretIsReadAtUse:
    def test_Exchange_AfterTheSecretFileChanges_UsesTheNewValueWithoutRestart(
        self, monkeypatch, tmp_path
    ):
        secret_file = tmp_path / "client-secret"
        secret_file.write_text("tlcs_live_the_old_value_0000000000", encoding="utf-8")

        seen: list[str] = []

        def capture(**kwargs):
            seen.append(kwargs["client_secret"])
            return {"access_token": "a", "refresh_token": "r", "expires_in": 3600}

        monkeypatch.setattr("obdi.web.exchange_code", capture)

        config = WebConfig(
            client_id="client-1",
            client_secret=lambda: secret_file.read_text(encoding="utf-8").strip(),
            redirect_uri="https://obdi.example.com/callback",
            connection_store=ConnectionStore(tmp_path / "c.json"),
        )
        handler = type(
            "H", (ConnectionHandler,), {"config": config, "session": AuthorisationSession()}
        )
        httpd = HTTPServer(("127.0.0.1", 0), handler)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        try:
            base = f"http://127.0.0.1:{httpd.server_port}"
            state = handler.session.begin("first-bank")
            httpx.get(f"{base}/callback", params={"code": "c1", "state": state})

            # The rotation: new value on disk, no restart, no signal.
            secret_file.write_text("tlcs_live_the_new_value_1111111111", encoding="utf-8")

            state = handler.session.begin("second-bank")
            httpx.get(f"{base}/callback", params={"code": "c2", "state": state})
        finally:
            httpd.shutdown()

        assert seen == [
            "tlcs_live_the_old_value_0000000000",
            "tlcs_live_the_new_value_1111111111",
        ], "the exchange must see the file as it is NOW, not as it was at startup"

    def test_Config_WithAPlainString_StillWorksForCallersThatHaveNoFile(self, tmp_path):
        config = WebConfig(
            client_id="client-1",
            client_secret="a-literal-value",
            redirect_uri="https://obdi.example.com/callback",
            connection_store=ConnectionStore(tmp_path / "c.json"),
        )

        assert config.current_client_secret() == "a-literal-value"
