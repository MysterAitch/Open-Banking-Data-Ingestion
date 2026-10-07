"""The Accounts page shows which accounts need a look, and says the same as Today.

The owner followed Today's Verification line (then "9 of 12 accounts in agreement through their
latest known balance; 1 held back; 2 cannot be verified.") to the Accounts page and found
every account at one weight under a green tick that said "declared": the one that did not add
up and the two that could not be checked were sentences in the body text of rows that looked
like all the others.

The invented household, with what each account must read as, decided before the first run:

    agreeing    100.00 known on 2026-09-10, 10.00 out on 09-14, 90.00 known on 09-20
                the transactions reproduce the second balance      adds up
    held        100.00 known on 2026-09-10, 10.00 out on 09-14, 70.00 known on 09-20
                the transactions say 90.00                         does not add up
    unproven    one row, no known balance                          nothing to check against
    single      one row, one known balance: nothing tests it       nothing to check against
    empty       declared, no rows                                  not counted
    old         one row, archived on 2026-09-30                    not counted

so four accounts are counted: 1 adds up, 1 does not, 2 have nothing to check against.
"""

from __future__ import annotations

import json
import re
import threading
from datetime import date
from http.server import HTTPServer

import httpx
import pytest

from obdi.cli import build_web_config
from obdi.ingest.accounts import AccountRecord, AccountRef
from obdi.ingest.store import Store
from obdi.pages.web import AuthorisationSession, ConnectionHandler
from obdi.verify.balance_anchors import record_stated_anchor
from test_ledger import land, txn

D = date
TODAY = D(2026, 10, 5)
SUMMARY = (
    "1 of 4 accounts adds up to their latest known balance; 1 does not add up; "
    "2 have nothing to check against."
)


def household(store: Store, *, only_agreeing: bool = False) -> None:
    wanted = ("agreeing",) if only_agreeing else (
        "agreeing", "held", "unproven", "single", "empty", "old",
    )
    for ref in wanted:
        store.declare_account(
            AccountRecord(
                ref=AccountRef(ref),
                label=f"Card {ref}",
                closed=D(2026, 9, 30) if ref == "old" else None,
            )
        )
    for ref in wanted:
        if ref == "empty":
            continue
        land(store, f"d-{ref}", txn(ref, "s", f"{ref}-1", D(2026, 9, 14), -1000, "KETTLE"))
    record_stated_anchor(store, "agreeing", "2026-09-10", "100.00", today=TODAY)
    record_stated_anchor(store, "agreeing", "2026-09-20", "90.00", today=TODAY)
    if only_agreeing:
        return
    record_stated_anchor(store, "held", "2026-09-10", "100.00", today=TODAY)
    record_stated_anchor(store, "held", "2026-09-20", "70.00", today=TODAY)
    record_stated_anchor(store, "single", "2026-09-20", "55.00", today=TODAY)


def _serve(tmp_path, monkeypatch, **kwargs):
    db = tmp_path / "accounts.sqlite3"
    with Store(db) as store:
        household(store, **kwargs)
    accounts = tmp_path / "accounts.json"
    accounts.write_text(json.dumps({"actual": []}), encoding="utf-8")
    monkeypatch.setenv("OBDI_CONNECTION_STORE", str(tmp_path / "connections.json"))
    monkeypatch.setenv("OBDI_ACCOUNT_MAP", str(accounts))
    for variable in ("TRUELAYER_CLIENT_ID", "TRUELAYER_CLIENT_SECRET_FILE"):
        monkeypatch.delenv(variable, raising=False)
    config = build_web_config(db)
    assert config is not None
    handler = type(
        "H", (ConnectionHandler,), {"config": config, "session": AuthorisationSession()}
    )
    httpd = HTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, f"http://127.0.0.1:{httpd.server_port}"


@pytest.fixture
def pages(tmp_path, monkeypatch):
    """(the Accounts page, Today) of the household of six."""
    httpd, base = _serve(tmp_path, monkeypatch)
    try:
        yield httpx.get(f"{base}/accounts", timeout=30).text, httpx.get(base, timeout=30).text
    finally:
        httpd.shutdown()


@pytest.fixture
def all_agree(tmp_path, monkeypatch):
    """(the Accounts page, Today) where the one account held is in agreement."""
    httpd, base = _serve(tmp_path, monkeypatch, only_agreeing=True)
    try:
        yield httpx.get(f"{base}/accounts", timeout=30).text, httpx.get(base, timeout=30).text
    finally:
        httpd.shutdown()


def row_of(page: str, ref: str) -> str:
    """The markup of one account's row, found by its anchor."""
    found = re.search(rf'<div class="row"[^>]*id="account-{ref}".*?</div>', page, re.S)
    assert found, f"no row for {ref}"
    return found.group(0)


def text_of(markup: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]*>", " ", markup)).strip()


class TestAnAccountsOwnPageIsHeadedByItsName:
    """The owner opened an account he had named and found its page headed by its reference.

    The page asked only for the name a provider gave the account, so a label declared by him
    was ignored there while the Accounts page showed it.
    """

    @pytest.fixture
    def page(self, tmp_path, monkeypatch) -> str:
        httpd, base = _serve(tmp_path, monkeypatch)
        try:
            return httpx.get(f"{base}/ledger?ref=agreeing", timeout=30).text
        finally:
            httpd.shutdown()

    def test_Heading_OfADeclaredAccount_IsItsLabelAndNotItsReference(self, page):
        (heading,) = re.findall(r"<h1[^>]*>(.*?)</h1>", page, re.S)

        assert "Card agreeing" in text_of(heading)
        assert text_of(heading) != "agreeing"

    def test_Reference_UnderTheHeading_IsSetAsCodeInTheOneMutedLine(self, page):
        assert re.search(r'<p class="meta"><code>agreeing</code> &middot; ', page)

    def test_SourcesThatFeedTheAccount_AreSetAsCodeWhereTheyAreSaid(self, page):
        """The sources are the strip's lanes and, as evidence, the matching in "How this was
        checked"; wherever a source is named it is code."""
        named = re.findall(r"<code>([^<]+)</code>", page)

        assert "agreeing" in named and "s" in named


class TestTheSummary:
    def test_Accounts_SaysTheSentenceTodaySays(self, pages):
        accounts, today = pages

        assert SUMMARY in text_of(accounts)
        assert SUMMARY not in text_of(today), "Today's bars say it per account; the count is cut"

    def test_Accounts_NamesTheAccountsThatNeedALook_AsLinksToTheirRows(self, pages):
        accounts, _ = pages
        section = accounts.split('id="needs-a-look"')[1].split("</p>")[0]

        assert re.findall(r'href="#account-([a-z]+)"', section) == ["held", "single", "unproven"]
        assert "Does not add up: " in text_of(section)
        assert "Nothing to check against: " in text_of(section)

    def test_Accounts_WhenEveryAccountAddsUp_SaysSoAndNamesNothing(self, all_agree):
        accounts, today = all_agree
        said = "The account adds up to its latest known balance."

        assert said not in text_of(today)
        assert said in text_of(accounts)
        assert 'id="needs-a-look"' not in accounts

    def test_Today_WhenSomethingNeedsALook_SaysItOnTheAccountsOwnRowAndNotInACount(self, pages):
        _, today = pages

        assert 'href="/accounts#needs-a-look"' not in today
        assert "Does not add up from" in text_of(today)

    def test_Today_WhenEveryAccountAddsUp_LinksToTheList(self, all_agree):
        _, today = all_agree

        assert 'href="/accounts"' in today
        assert "#needs-a-look" not in today


class TestEachAccountsChip:
    def test_AnAccountThatAddsUp_CarriesTheGreenChip(self, pages):
        row = row_of(pages[0], "agreeing")

        assert '<span class="pill pill-ok">adds up</span>' in row

    def test_AnAccountThatDoesNotAddUp_CarriesTheAmberChip(self, pages):
        row = row_of(pages[0], "held")

        assert '<span class="pill pill-warn">does not add up</span>' in row
        assert "pill-ok" not in row

    @pytest.mark.parametrize("ref", ["unproven", "single"])
    def test_AnAccountNothingTests_CarriesTheAmberChip(self, pages, ref):
        row = row_of(pages[0], ref)

        assert '<span class="pill pill-warn">nothing to check against</span>' in row
        assert "pill-ok" not in row

    @pytest.mark.parametrize("ref", ["empty", "old"])
    def test_AnAccountThatIsNotCounted_CarriesNoVerificationChip(self, pages, ref):
        row = row_of(pages[0], ref)

        for word in ("adds up</span>", "does not add up</span>", "nothing to check against</span>"):
            assert word not in row

    def test_BeingDeclared_IsNotAGreenTick(self, pages):
        """Declared is the ordinary state of an account; a tick for it read as "verified"."""
        assert ">declared</span>" not in pages[0]


class TestWhatStandsOut:
    @pytest.mark.parametrize(
        ("ref", "reason"),
        [
            ("held", "The transactions do not add up to the known balance for 2026-09-20"),
            ("unproven", "No known balance"),
            ("single", "a second is needed before the transactions can be checked"),
        ],
    )
    def test_ARowThatNeedsALook_IsMarkedAndSaysWhyInBold(self, pages, ref, reason):
        row = row_of(pages[0], ref)

        assert "data-look" in row
        (emphasised,) = re.findall(r'<strong class="warn">(.*?)</strong>', row, re.S)
        assert reason in emphasised

    @pytest.mark.parametrize("ref", ["agreeing", "empty", "old"])
    def test_ARowThatNeedsNothing_IsNotMarked(self, pages, ref):
        row = row_of(pages[0], ref)

        assert "data-look" not in row
        assert 'class="warn"' not in row

    def test_ARowThatNeedsALook_SaysWhatWouldSettleIt(self, pages):
        assert "State a known balance on its page, or upload a statement" in text_of(
            row_of(pages[0], "unproven")
        )
        assert "Open its page to see where it stops adding up and why" in text_of(
            row_of(pages[0], "held")
        )

    def test_Rows_AreOrderedNotAddingUpThenNothingToCheckThenTheRest_WithArchivedLast(
        self, pages
    ):
        order = re.findall(r'<div class="row"[^>]*id="account-([a-z]+)"', pages[0])

        assert order == ["held", "single", "unproven", "agreeing", "empty", "old"]
