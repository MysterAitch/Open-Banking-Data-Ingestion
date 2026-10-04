"""The Actual page and its answers say what is true when there is no Actual to talk to.

With no budget configured, Push answered with a page TITLED "Push queued" over "nothing queued",
followed by the sentence about the applier picking requests up; Audit and Marker the same; and the
Actual page itself never said Actual was unconfigured while offering three live buttons. With
Actual configured but nothing bound, Audit answered "no Actual-bound accounts to audit" under the
title "Audit queued". And pressing a button twice queued two identical requests.
"""

from __future__ import annotations

import json
import re
import threading
from http.server import HTTPServer

import httpx
import pytest

from obdi.cli import build_web_config
from obdi.store import Store
from obdi.web import AuthorisationSession, ConnectionHandler
from test_balance_anchors import ACCOUNT, everyday

#: The setting's name is the code's business: a page that quotes it as the remedy tells a reader
#: of the page to go and edit an environment, which is not what the page is for.
SETTING = "ACTUAL_SYNC_ID"


def serve(tmp_path, monkeypatch, *, configured: bool, bound: bool):
    db = tmp_path / "actual.sqlite3"
    with Store(db) as store:
        everyday(store)
    account_map = tmp_path / "accounts.json"
    account_map.write_text(
        json.dumps(
            {
                "bindings": [],
                "actual": (
                    [{"canonical_id": ACCOUNT, "actual_account_id": "act-everyday"}]
                    if bound
                    else []
                ),
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("OBDI_ACCOUNT_MAP", str(account_map))
    monkeypatch.setenv("OBDI_ACTUAL_DIR", str(tmp_path / "actual"))
    monkeypatch.setenv("OBDI_CONNECTION_STORE", str(tmp_path / "connections.json"))
    # Named, so the page does not open with its own "instance not identified" notice.
    monkeypatch.setenv("OBDI_INSTANCE_LABEL", "obdi")
    monkeypatch.setenv("OBDI_INSTANCE_ROLE", "production")
    if configured:
        monkeypatch.setenv(SETTING, "sync-1")
    else:
        monkeypatch.delenv(SETTING, raising=False)
    for variable in ("TRUELAYER_CLIENT_ID", "TRUELAYER_CLIENT_SECRET_FILE"):
        monkeypatch.delenv(variable, raising=False)
    config = build_web_config(db)
    assert config is not None
    handler = type(
        "H", (ConnectionHandler,), {"config": config, "session": AuthorisationSession()}
    )
    httpd = HTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, f"http://127.0.0.1:{httpd.server_port}", tmp_path / "actual" / "requests", db


@pytest.fixture
def unconfigured(tmp_path, monkeypatch):
    httpd, base, requests, _ = serve(tmp_path, monkeypatch, configured=False, bound=True)
    yield base, requests
    httpd.shutdown()


@pytest.fixture
def unbound(tmp_path, monkeypatch):
    httpd, base, requests, _ = serve(tmp_path, monkeypatch, configured=True, bound=False)
    yield base, requests
    httpd.shutdown()


@pytest.fixture
def bound(tmp_path, monkeypatch):
    httpd, base, requests, db = serve(tmp_path, monkeypatch, configured=True, bound=True)
    yield base, requests, db
    httpd.shutdown()


def press(base: str, route: str) -> httpx.Response:
    return httpx.post(f"{base}{route}", timeout=60)


def title_of(page: str) -> str:
    """The page's title without the instance label the page template puts in front of it."""
    raw = re.search(r"<title>(.*?)</title>", page, re.S)
    assert raw is not None
    return re.sub(r"^\[[^\]]*\]\s*", "", raw.group(1).strip())


def first_paragraph(page: str) -> str:
    found = re.search(r"<p[^>]*>(.*?)</p>", page, re.S)
    assert found is not None
    return re.sub(r"<[^>]+>", "", found.group(1))


def waiting(requests, kind: str) -> list[str]:
    if not requests.is_dir():
        return []
    return sorted(path.name for path in requests.glob(f"{kind}-*.json"))


class TestPressesWhenActualIsNotConfigured:
    @pytest.mark.parametrize(
        ("route", "old_title"),
        [
            ("/push-actual", "Push queued"),
            ("/audit-actual", "Audit queued"),
            ("/marker-actual", "Marker queued"),
        ],
    )
    def test_Answer_SaysNothingWasQueuedInTheTitleAndTheFirstSentence(
        self, unconfigured, route, old_title
    ):
        base, requests = unconfigured

        answer = press(base, route)

        assert answer.status_code == 200
        assert title_of(answer.text).startswith("Nothing queued")
        assert old_title not in answer.text
        assert first_paragraph(answer.text).startswith(
            "Nothing queued: Actual is not configured"
        )
        assert waiting(requests, "push") == waiting(requests, "audit") == []

    @pytest.mark.parametrize("route", ["/push-actual", "/audit-actual", "/marker-actual"])
    def test_Answer_NeverSaysTheApplierWillPickItUp(self, unconfigured, route):
        base, _ = unconfigured

        answer = press(base, route)

        for sentence in ("picks requests up", "reads each bound account back", "renames the sync"):
            assert sentence not in answer.text
        assert SETTING not in answer.text

    def test_ActualPage_SaysFirstThatActualIsNotConfiguredAndOffersNoLivePress(self, unconfigured):
        base, _ = unconfigured

        page = httpx.get(f"{base}/actual", timeout=60).text

        body = page[page.index("</style>") :]
        assert body.index("Actual is not configured on this instance") < body.index(
            "Push to Actual now"
        )
        assert SETTING not in page
        for label in ("Push to Actual now", "Audit Actual now", "Write a sync marker now"):
            button = re.search(rf"<button[^>]*>{label}</button>", page)
            assert button is not None, label
            assert re.search(r"\sdisabled[\s>]", button.group(0)), label
        assert page.count("Off: Actual is not configured.") == 3

    def test_ActualPage_WhenConfigured_SaysNothingOfTheKindAndOffersLivePresses(self, bound):
        base, _, _ = bound

        page = httpx.get(f"{base}/actual", timeout=60).text

        assert "not configured" not in page
        assert not re.search(r"\sdisabled[\s>]", page)


class TestAuditWhenNothingIsBound:
    def test_Answer_SaysNothingWasQueuedAndWhy(self, unbound):
        base, requests = unbound

        answer = press(base, "/audit-actual")

        assert title_of(answer.text).startswith("Nothing queued")
        assert first_paragraph(answer.text).startswith(
            "Nothing queued: no Actual-bound accounts to audit"
        )
        assert "Audit queued" not in answer.text
        assert waiting(requests, "audit") == []


class TestPressingTwice:
    def test_Marker_PressedTwice_QueuesOneAndSaysTheSecondIsWaiting(self, bound):
        base, requests, _ = bound

        first = press(base, "/marker-actual")
        second = press(base, "/marker-actual")

        assert title_of(first.text) == "Marker queued"
        assert title_of(second.text) == "Nothing queued"
        assert "a sync marker request" in second.text
        assert "already waiting" in second.text
        assert len(waiting(requests, "marker")) == 1

    def test_Audit_PressedTwiceWithNothingChanged_QueuesOneAndSaysSo(self, bound):
        base, requests, _ = bound

        first = press(base, "/audit-actual")
        second = press(base, "/audit-actual")

        assert title_of(first.text) == "Audit queued"
        assert title_of(second.text) == "Nothing queued"
        assert "an identical audit" in second.text
        assert len(waiting(requests, "audit")) == 1

    def test_Push_PressedTwiceWithNothingChanged_QueuesOneAndSaysSo(self, bound):
        base, requests, _ = bound

        first = press(base, "/push-actual")
        second = press(base, "/push-actual")

        assert title_of(first.text) == "Push queued"
        assert "picks requests up" in first.text
        assert title_of(second.text) == "Nothing queued"
        assert "an identical push" in second.text
        assert "picks requests up" not in second.text
        assert len(waiting(requests, "push")) == 1

    def test_Push_WhenRowsLandedBetweenThePresses_QueuesTheSecondBehindTheFirst(self, bound):
        """Two pushes can legitimately differ: each is built from the store when it is pressed."""
        from test_ledger import land, txn

        base, requests, db = bound
        press(base, "/push-actual")
        from datetime import date

        with Store(db) as store:
            land(
                store,
                "digest-later",
                txn(ACCOUNT, "src-a", "r-later", date(2026, 3, 21), -500, "LATER"),
            )

        second = press(base, "/push-actual")

        assert title_of(second.text) == "Push queued"
        assert "differs from them" in second.text
        assert len(waiting(requests, "push")) == 2

    def test_Push_AfterTheFirstWasPickedUp_QueuesAgain(self, bound):
        base, requests, _ = bound
        press(base, "/push-actual")
        for path in requests.glob("push-*.json"):
            path.unlink()

        again = press(base, "/push-actual")

        assert title_of(again.text) == "Push queued"
        assert len(waiting(requests, "push")) == 1
