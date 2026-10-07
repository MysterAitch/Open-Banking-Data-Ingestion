"""The account page says where the transactions stop adding up, once, in days.

THE HOUSEHOLD is `test_statement_opening_measure`'s. `april` is missing its April statement, so the
one stretch from 2026-03-10 (March's closing) to 2026-05-10 (May's) fails, and the stretch from
2026-05-10 to 2026-06-10 adds up. `five` has every statement. Decided before the first run: the
page for `april` says the failing stretch once, with the folded list of what it can mean; the page
for `five` says nothing fails and carries no such list; neither names a figure.
"""

from __future__ import annotations

import threading
from html.parser import HTMLParser
from http.server import HTTPServer

import httpx
import pytest

from obdi.cli import build_web_config
from obdi.ingest.accounts import AccountRecord, AccountRef
from obdi.ingest.statement_terms import keep_statement_readings
from obdi.ingest.store import Store
from obdi.verify.agreement import STRETCH_MEANINGS
from obdi.web import AuthorisationSession, ConnectionHandler
from test_statement_opening_measure import build

FAILS = (
    "The transactions held between 2026-03-10 and 2026-05-10 do not add up to the change "
    "between the two known balances."
)
SUMMARY = "What a stretch that does not add up can mean"


class Page(HTMLParser):
    """Paragraphs, list items, the summaries of folds, and the headings of the parts of a fold, as
    the page's own words."""

    def __init__(self) -> None:
        super().__init__()
        self.paragraphs: list[str] = []
        self.items: list[str] = []
        self.summaries: list[str] = []
        self.headings: list[str] = []
        self._in: str | None = None
        self._text = ""

    def handle_starttag(self, tag, attrs):
        if tag in ("p", "li", "summary", "h3"):
            self._in, self._text = tag, ""

    def handle_endtag(self, tag):
        if tag == self._in:
            said = " ".join(self._text.split())
            {
                "p": self.paragraphs,
                "li": self.items,
                "summary": self.summaries,
                "h3": self.headings,
            }[tag].append(said)
            self._in = None

    def handle_data(self, data):
        if self._in:
            self._text += data


@pytest.fixture(scope="module")
def served(tmp_path_factory):
    root = tmp_path_factory.mktemp("stretch-page")
    db = root / "store.sqlite3"
    with Store(db) as store:
        build(store, root)
        for ref in ("april", "five"):
            store.declare_account(AccountRecord(ref=AccountRef(ref)))
        keep_statement_readings(store)
        store.connection.commit()
    with pytest.MonkeyPatch.context() as patch:
        patch.setenv("OBDI_CONNECTION_STORE", str(root / "connections.json"))
        patch.delenv("OBDI_ACCOUNT_MAP", raising=False)
        for variable in ("TRUELAYER_CLIENT_ID", "TRUELAYER_CLIENT_SECRET_FILE"):
            patch.delenv(variable, raising=False)
        config = build_web_config(db)
        assert config is not None
        handler = type(
            "H", (ConnectionHandler,), {"config": config, "session": AuthorisationSession()}
        )
        httpd = HTTPServer(("127.0.0.1", 0), handler)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        try:
            yield f"http://127.0.0.1:{httpd.server_port}"
        finally:
            httpd.shutdown()


def page_of(base: str, ref: str, month: str) -> Page:
    response = httpx.get(f"{base}/ledger", params={"ref": ref, "month": month}, timeout=60)
    assert response.status_code == 200
    parsed = Page()
    parsed.feed(response.text)
    return parsed


class TestTheStretchThatFails:
    def test_Page_WhenAprilIsMissing_SaysWhichStretchDoesNotAddUpOnce(self, served):
        assert page_of(served, "april", "2026-05").paragraphs.count(FAILS) == 1

    def test_Page_WhenAprilIsMissing_SaysNothingOfTheStretchAfterItBeyondTheRule(self, served):
        said = " ".join(page_of(served, "april", "2026-05").paragraphs)

        assert "from 2026-05-10 to 2026-06-10" not in said, "no later span is carried as adding up"

    def test_Page_WhenAprilIsMissing_PutsWhatAFailingStretchCanMeanInsideTheKnownBalancesFold(
        self, served
    ):
        page = page_of(served, "april", "2026-05")

        assert SUMMARY in page.headings
        assert SUMMARY not in page.summaries, "a part of the fold, not a fold of its own"
        assert set(STRETCH_MEANINGS) <= set(page.items)

    def test_Page_WhenEveryStatementIsHeld_SaysNothingFailsAndOffersNoList(self, served):
        page = page_of(served, "five", "2026-03")

        assert not any("do not add up to the change" in p for p in page.paragraphs)
        assert SUMMARY not in page.headings

    def test_Page_ForTheStretchSentences_NamesNoFigureAndNoRetiredPhrase(self, served):
        page = page_of(served, "april", "2026-05")
        said = " ".join(p for p in page.paragraphs if "add up" in p) + " ".join(page.items)

        assert said
        for figure in ("7.00", "100.00", "700", "10000"):
            assert figure not in said
        for retired in ("held back", "in agreement", "match", "anchor"):
            assert retired not in said
