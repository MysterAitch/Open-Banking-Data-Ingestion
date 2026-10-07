"""The account page lists the balances several sources state for one day, and what a person did.

THE ACCOUNT is `test_disregarded_balances.world`: two Santander statements closing 2026-05-10 and
2026-06-10, and a balance typed for 2026-06-10 that differs from June's closing (the figure is
`DIFFERENT`). Decided before the first run, for the page of 2026-06 over that account:

  two sources differing   the day is listed with "The figures differ.", both sources named, a
                          "Disregard" button for each, and no pill;
  one disregarded         the day is listed with the disregarded balance marked "disregarded", a
                          button to use it again, and "Only one of them is in use.";
  no longer applies       the typed balance is disregarded and then removed, so the disregard names
                          nothing held: the page lists it as "no longer applies", with a button
                          that removes the disregard, and no button that would disregard anything.

Whatever the state, a GET page holds no figure: not in its words, its hidden fields, or its button
labels. Whether two figures are equal is said, and what they are is not.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from html.parser import HTMLParser
from http.server import HTTPServer

import httpx
import pytest

from obdi.cli import build_web_config
from obdi.ingest.store import Store
from obdi.pages.web import AuthorisationSession, ConnectionHandler
from obdi.verify.balance_anchors import STATEMENT, disregard_balance, remove_stated_anchor
from test_disregarded_balances import ACCOUNT, DAY, DIFFERENT, SOURCE, world

FIGURES = ("115.00", "120.00", "11500", "12000", "100.00", "10000", "110.00", "11000")
FORBIDDEN_WORDS = ("anchor", "in agreement", "held back", "set aside")


@dataclass
class Form:
    action: str
    fields: dict[str, str] = field(default_factory=dict)
    button: str = ""


class Page(HTMLParser):
    """Texts, every attribute value, and the forms (action, hidden fields, button words)."""

    def __init__(self) -> None:
        super().__init__()
        self.forms: list[Form] = []
        self.texts: list[str] = []
        self.attributes: list[str] = []
        self._button = False
        self._styled = False
        self._details_open = False
        self._summary = False
        #: The summary of each folded section, and whether it is open.
        self.sections: dict[str, bool] = {}

    def handle_starttag(self, tag, attrs):
        values = {k: v or "" for k, v in attrs}
        self.attributes += [v for v in values.values() if v]
        if tag == "details":
            self._details_open = "open" in values
        elif tag == "summary":
            self._summary = True
        if tag == "form":
            self.forms.append(Form(values.get("action", "")))
        elif tag == "input" and self.forms:
            self.forms[-1].fields[values.get("name", "")] = values.get("value", "")
        elif tag == "button":
            self._button = True
        elif tag in ("style", "script"):
            self._styled = True

    def handle_endtag(self, tag):
        if tag == "button":
            self._button = False
        elif tag in ("style", "script"):
            self._styled = False

    def handle_data(self, data):
        if self._styled:
            return
        if self._summary and data.strip():
            self.sections[data.strip()] = self._details_open
            self._summary = False
        if self._button and self.forms:
            self.forms[-1].button += data
        if data.strip():
            self.texts.append(data.strip())

    @property
    def said(self) -> str:
        """The visible words, run together: a sentence is split by the markup inside it."""
        return " ".join(self.texts)

    def to(self, action: str) -> list[Form]:
        return [f for f in self.forms if f.action == action]


@pytest.fixture
def served(tmp_path, monkeypatch):
    monkeypatch.setenv("OBDI_CONNECTION_STORE", str(tmp_path / "connections.json"))
    monkeypatch.delenv("OBDI_ACCOUNT_MAP", raising=False)
    for variable in ("TRUELAYER_CLIENT_ID", "TRUELAYER_CLIENT_SECRET_FILE"):
        monkeypatch.delenv(variable, raising=False)
    path = tmp_path / "store.sqlite3"
    with Store(path) as store:
        world(store, tmp_path, DIFFERENT)
    config = build_web_config(path)
    assert config is not None
    handler = type(
        "H", (ConnectionHandler,), {"config": config, "session": AuthorisationSession()}
    )
    httpd = HTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{httpd.server_port}", path
    finally:
        httpd.shutdown()


def page_of(base: str) -> Page:
    response = httpx.get(
        f"{base}/ledger", params={"ref": ACCOUNT, "month": "2026-06"}, timeout=60
    )
    assert response.status_code == 200
    parsed = Page()
    parsed.feed(response.text)
    return parsed


def known_balances_open(page: Page) -> list[bool]:
    """Whether the folded "Known balances and the opening" section is open, as a list of one."""
    return [open_ for name, open_ in page.sections.items() if name.startswith("Known balances")]


def holds_no_figure(page: Page) -> None:
    visible = " ".join(page.texts + page.attributes)
    for form in page.forms:
        visible += " " + form.button + " " + " ".join(form.fields.values())
    for figure in FIGURES:
        assert figure not in visible, f"{figure} is on the page"


class TestTwoSourcesDiffering:
    def test_Page_ForADayWhereTwoSourcesDiffer_SaysSoAndOffersEachOfThemToBeDisregarded(
        self, served
    ):
        base, _ = served

        page = page_of(base)

        assert "The figures differ." in page.said
        offered = {
            (f.fields["source"], f.fields["basis"])
            for f in page.to("/ledger-balance-disregard")
            if f.fields["day"] == DAY.isoformat()
        }
        assert offered == {(SOURCE, STATEMENT), ("stated", "stated")}
        assert "disregarded" not in page.texts

    def test_Page_ForADayWhereTwoSourcesDiffer_HoldsNoFigureAnywhere(self, served):
        base, _ = served

        holds_no_figure(page_of(base))

    def test_Page_ForTheSeveralSourcesSection_UsesNoRetiredWord(self, served):
        base, _ = served

        said = page_of(base).said.lower()

        for word in FORBIDDEN_WORDS:
            assert word not in said, word


class TestTheRemedyForDisagreeingBalances:
    def test_Overview_WhenKnownBalancesDisagree_PointsAtDisregardingNotRemoving(self):
        from obdi.read.overview import _KINDS

        _, remedy = _KINDS["known-balances-disagree"]

        assert "disregard the known balance that is wrong" in remedy
        assert "remove a known balance" not in remedy


class TestOneDisregarded:
    def test_Page_WhenTheStatementsBalanceIsDisregarded_MarksItAndOffersToUseItAgain(self, served):
        base, path = served
        with Store(path) as store:
            disregard_balance(store, ACCOUNT, DAY.isoformat(), SOURCE, STATEMENT)

        page = page_of(base)

        assert "disregarded" in page.texts
        assert "Only one of them is in use." in page.said
        again = page.to("/ledger-balance-use-again")
        assert [f.fields["source"] for f in again] == [SOURCE]
        assert again[0].button.startswith("Use the")
        holds_no_figure(page)

    def test_Page_WhenTheStatementsBalanceIsDisregarded_OffersToDisregardOnlyTheOtherOne(
        self, served
    ):
        base, path = served
        with Store(path) as store:
            disregard_balance(store, ACCOUNT, DAY.isoformat(), SOURCE, STATEMENT)

        offered = [
            f.fields["source"]
            for f in page_of(base).to("/ledger-balance-disregard")
            if f.fields["day"] == DAY.isoformat()
        ]

        assert offered == ["stated"]


class TestADisregardThatNoLongerApplies:
    def test_Page_WhenTheDisregardedBalanceWasRemoved_SaysSoAndOffersToRemoveTheDisregard(
        self, served
    ):
        base, path = served
        with Store(path) as store:
            assert disregard_balance(store, ACCOUNT, DAY.isoformat(), "stated", "stated")
            assert remove_stated_anchor(store, ACCOUNT, DAY.isoformat())

        page = page_of(base)

        assert "no longer applies" in page.texts
        removal = page.to("/ledger-balance-use-again")
        assert len(removal) == 1
        assert removal[0].button.startswith("Remove the disregard")
        assert all(f.fields["source"] != "stated" for f in page.to("/ledger-balance-disregard"))
        holds_no_figure(page)

    def test_Page_WhenADisregardNoLongerApplies_TheSectionThatRemovesItIsNotFoldedAway(
        self, served
    ):
        base, path = served
        with Store(path) as store:
            disregard_balance(store, ACCOUNT, DAY.isoformat(), "stated", "stated")
            remove_stated_anchor(store, ACCOUNT, DAY.isoformat())

        assert known_balances_open(page_of(base)) == [True]

    def test_Page_WhenNothingIsDisregardedAndAllAddsUp_LeavesTheSectionFolded(
        self, tmp_path, monkeypatch
    ):
        """The control: a quiet account keeps the section folded, so opening it for a stale
        disregard is the stale disregard's doing and not a change to every account."""
        monkeypatch.setenv("OBDI_CONNECTION_STORE", str(tmp_path / "connections.json"))
        monkeypatch.delenv("OBDI_ACCOUNT_MAP", raising=False)
        path = tmp_path / "store.sqlite3"
        with Store(path) as store:
            world(store, tmp_path, "-115.00")
        config = build_web_config(path)
        assert config is not None
        handler = type(
            "H", (ConnectionHandler,), {"config": config, "session": AuthorisationSession()}
        )
        httpd = HTTPServer(("127.0.0.1", 0), handler)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        try:
            page = page_of(f"http://127.0.0.1:{httpd.server_port}")
        finally:
            httpd.shutdown()

        assert known_balances_open(page) == [False]

    def test_Disregard_WhenTheStaleOneIsRemovedFromThePage_TheStoreHoldsNoDisregard(self, served):
        base, path = served
        with Store(path) as store:
            disregard_balance(store, ACCOUNT, DAY.isoformat(), "stated", "stated")
            remove_stated_anchor(store, ACCOUNT, DAY.isoformat())
        form = page_of(base).to("/ledger-balance-use-again")[0].fields

        done = httpx.post(f"{base}/ledger-balance-use-again", data=form, timeout=60)

        assert done.status_code == 200
        with Store(path) as store:
            assert store.disregarded_balance_keys(ACCOUNT) == []
