"""The callback receiver.

Tested through real HTTP against a loopback server rather than by calling the
handler directly, because the behaviour that matters - status codes, what the
browser is told, and what never reaches a log - only exists at that boundary.
"""

import threading
from http.server import HTTPServer

import httpx
import pytest

from obdi.callback import CallbackHandler


@pytest.fixture
def receiver():
    """A running receiver, plus a record of the codes it was handed."""
    seen: list[tuple[str, str]] = []

    def handle(code: str, state: str) -> str:
        if code == "explode":
            raise RuntimeError("token exchange refused")
        seen.append((code, state))
        return f"Saved connection for code {code}."

    handler = type("TestHandler", (CallbackHandler,), {"code_handler": staticmethod(handle)})
    server = HTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    try:
        yield base, seen
    finally:
        server.shutdown()
        server.server_close()


class TestSuccessfulAuthorisation:
    def test_Authorisation_WhenBankRedirectsWithCode_CodeHandedToExchange(self, receiver):
        base, seen = receiver
        response = httpx.get(f"{base}/callback", params={"code": "abc123", "state": "s1"})
        assert response.status_code == 200
        assert seen == [("abc123", "s1")]

    def test_Authorisation_WhenSucceeded_BrowserShownConfirmation(self, receiver):
        base, _ = receiver
        response = httpx.get(f"{base}/callback", params={"code": "abc123"})
        assert "Connected" in response.text
        assert "Saved connection" in response.text


class TestFailedAuthorisation:
    def test_Authorisation_WhenDeclinedAtBank_ReasonShownNotSilentSuccess(self, receiver):
        # A declined or abandoned authorisation must not look like a working
        # integration, or it is only discovered a quarter later.
        base, seen = receiver
        response = httpx.get(
            f"{base}/callback",
            params={"error": "access_denied", "error_description": "User cancelled"},
        )
        assert response.status_code == 400
        assert "User cancelled" in response.text
        assert seen == []

    def test_Authorisation_WhenNoCodePresent_ReportedRatherThanAccepted(self, receiver):
        base, seen = receiver
        response = httpx.get(f"{base}/callback")
        assert response.status_code == 400
        assert seen == []

    def test_Authorisation_WhenExchangeThrows_ReasonSurfacedToBrowser(self, receiver):
        base, _ = receiver
        response = httpx.get(f"{base}/callback", params={"code": "explode"})
        assert response.status_code == 500
        assert "token exchange refused" in response.text


class TestRouting:
    def test_Receiver_WhenUnknownPathRequested_NotFound(self, receiver):
        base, _ = receiver
        assert httpx.get(f"{base}/anything-else").status_code == 404

    def test_Receiver_WhenPathHasTrailingSlash_StillAccepted(self, receiver):
        base, seen = receiver
        assert httpx.get(f"{base}/callback/", params={"code": "abc"}).status_code == 200
        assert seen == [("abc", "")]


class TestLoggingHygiene:
    def test_Receiver_WhenHandlingCallback_AuthorisationCodeNeverLogged(self, receiver, capfd):
        # The redirect carries the code in its query string, and the default
        # access log would write it to stdout and thence into container logs.
        base, _ = receiver
        httpx.get(f"{base}/callback", params={"code": "secret-code-value"})

        captured = capfd.readouterr()
        assert "secret-code-value" not in captured.out
        assert "secret-code-value" not in captured.err
