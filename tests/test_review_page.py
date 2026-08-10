"""The categorise page over HTTP: evidence shown, answers written once.

The functions are proved elsewhere; what matters here is that the SURFACE
shows what a person needs to judge a group - a real example rather than
the stripped label, and a warning where an answer would be guesswork - and
that answering actually writes, at human rank, through the same
cross-site-refusing door as every other mutating page.
"""

from __future__ import annotations

import threading

import httpx
import pytest

from obdi.connections import ConnectionStore
from obdi.web import AuthorisationSession, ConnectionHandler, WebConfig

OVERVIEW = {
    "covered": 2768,
    "eligible": 7443,
    "transfer_legs": 1314,
    "groups": [
        {
            "label": "DAP",
            "count": 242,
            "example": "DAP90481679",
            "distinct": 2,
            "reference_coded": True,
            "repeating": True,
        },
        {
            "label": "AMAZON --",
            "count": 27,
            "example": "AMAZON* 204-3267781-49",
            "distinct": 27,
            "reference_coded": True,
            "repeating": False,
        },
    ],
}


@pytest.fixture
def server(tmp_path):
    applied: list[tuple[str, str, str]] = []

    def apply(label: str, value: str, kind: str) -> int:
        applied.append((label, value, kind))
        return 242

    config = WebConfig(
        client_id="client-1",
        client_secret="secret-1",
        redirect_uri="https://obdi.example.com/callback",
        connection_store=ConnectionStore(tmp_path / "c.json"),
        categorise_overview=lambda: OVERVIEW,
        categorise_apply=apply,
    )
    handler = type(
        "ReviewHandler",
        (ConnectionHandler,),
        {"config": config, "session": AuthorisationSession()},
    )
    httpd = ConnectionHandler.make_server(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{httpd.server_port}", applied
    finally:
        httpd.shutdown()
        httpd.server_close()


class TestThePageShowsTheEvidence:
    def test_CoverageIsStated_WithItsDenominator(self, server):
        base, _ = server

        page = httpx.get(f"{base}/review", timeout=20)

        assert page.status_code == 200
        assert "2768 of 7443" in page.text
        assert "1314" in page.text, "excluded transfer legs stay visible"

    def test_EachGroup_ShowsARealExample_NotOnlyTheStrippedLabel(self, server):
        base, _ = server

        page = httpx.get(f"{base}/review", timeout=20)

        assert "DAP90481679" in page.text
        assert "AMAZON* 204-3267781-49" in page.text

    def test_ARepeatingReference_IsMarkedAsIdentifiable(self, server):
        base, _ = server

        page = httpx.get(f"{base}/review", timeout=20)

        assert "identify it once" in page.text

    def test_AScatterOfReferences_IsMarkedAsAGuess(self, server):
        base, _ = server

        page = httpx.get(f"{base}/review", timeout=20)

        assert "would be a guess" in page.text

    def test_ThePage_SaysAnswersOutrankLaterSweeps(self, server):
        base, _ = server

        page = httpx.get(f"{base}/review", timeout=20)

        assert "HUMAN rank" in page.text


class TestAnsweringAGroup:
    def test_AnAnswer_ReachesTheStore_AndReportsHowManyRows(self, server):
        base, applied = server

        response = httpx.post(
            f"{base}/review-apply",
            data={"label": "DAP", "value": "Home Bills: Water"},
            headers={"Origin": base},
            timeout=20,
        )

        assert response.status_code == 200
        assert applied == [("DAP", "Home Bills: Water", "category")]
        assert "Answered 242 row(s)" in response.text

    def test_AnEmptyAnswer_WritesNothing_AndSaysSo(self, server):
        base, applied = server

        response = httpx.post(
            f"{base}/review-apply",
            data={"label": "DAP", "value": "   "},
            headers={"Origin": base},
            timeout=20,
        )

        assert response.status_code == 200
        assert applied == []
        assert "Nothing was answered" in response.text

    def test_ACrossSiteAnswer_IsRefused(self, server):
        base, applied = server

        response = httpx.post(
            f"{base}/review-apply",
            data={"label": "DAP", "value": "Anything"},
            headers={"Origin": "https://elsewhere.example"},
            timeout=20,
        )

        assert response.status_code == 403
        assert applied == []
