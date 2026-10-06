"""The account page and Bring in agree about a statement that is not yet due.

The owner's account page said, in warning colour, "Nothing to check against since 2026-09-10 (3
weeks ago)" while Bring in said calmly "Next statement: Santander CC about 2026-10-10". Nothing was
overdue: the rows after the last statement cannot be tested until the next one is issued, and it
is not yet its day.

The scenes below build one card with a monthly statement history and a feed row after the newest
statement, over invented data, dated from today so that the answer does not depend on when the
suite runs:

  * the newest statement closed 26 days ago, so the next is due about a month after it, a few days
    from now: the account page says "Next statement due about D." in the ordinary colour, in place
    of the warning, and D is the day Bring in names;
  * the newest statement closed 60 days ago, so the next was due about a month ago and rows have
    run on without one: the warning wording and its colour return on the account page, and Bring
    in lists the statement as wanted instead of saying when one is expected. (At 31 to 45 days
    Bring in still expects the next one, rolled on a month; the account page says what Bring in
    says, so neither warns.)
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import httpx
import pytest

from fetch_gaps_world import Household, _declare, feed, santander_statements
from obdi.fetch_gaps import add_months
from obdi.store import Store
from page_dom import elements, parse
from served_store import environment_for, served_store

TODAY = datetime.now(UTC).date()
CARD = "card-due"


def world(root: Path, closed_days_ago: int):
    """A card with four monthly statements, the newest closed `closed_days_ago` days ago, and a
    feed row ten days after it (or today, if that is later than today)."""
    newest = TODAY - timedelta(days=closed_days_ago)
    closings = [add_months(newest, -k) for k in (3, 2, 1, 0)]
    after = min(newest + timedelta(days=10), TODAY)

    def build(store: Store) -> None:
        _declare(store, CARD, "Due card")
        santander_statements(store, root, CARD, closings, Household())
        feed(store, CARD, [(after, -1500, "Feed Zeppelin")], digest="due")

    return build, newest


@pytest.fixture
def due_soon(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[tuple[str, date]]:
    build, newest = world(tmp_path, 26)
    with served_store(tmp_path, build, bound=[]) as base:
        for name, value in environment_for(tmp_path).items():
            monkeypatch.setenv(name, value)
        yield base, newest


@pytest.fixture
def overdue(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[tuple[str, date]]:
    build, newest = world(tmp_path, 60)
    with served_store(tmp_path, build, bound=[]) as base:
        for name, value in environment_for(tmp_path).items():
            monkeypatch.setenv(name, value)
        yield base, newest


def words(html_text: str) -> str:
    return re.sub(r"\s+", " ", parse(html_text).text())


def account_page(base: str) -> str:
    response = httpx.get(f"{base}/ledger", params={"ref": CARD}, timeout=120)
    assert response.status_code == 200
    return response.text


def bring_in_page(base: str) -> str:
    return httpx.get(f"{base}/bring-in", timeout=120).text


def trust_line(page: str):
    return next(p for p in elements(parse(page), "p") if "trust" in p.classes)


def said_of(page: str) -> str:
    """The trust sentence as read: a date is set in a span that never breaks, which the tree
    reads as a space before the full stop."""
    return re.sub(r" ([.,:;])", r"\1", trust_line(page).text())


class TestAStatementNotYetDue:
    def test_AccountPage_SaysWhenTheNextStatementIsDueInPlaceOfTheWarning(self, due_soon):
        base, _ = due_soon

        said = said_of(account_page(base))

        assert re.search(r"Next statement due about \d{4}-\d\d-\d\d\.", said)
        assert "Nothing to check against since" not in said

    def test_AccountPage_NamesTheSameDayBringInNames(self, due_soon):
        base, _ = due_soon

        on_account = re.search(
            r"Next statement due about (\d{4}-\d\d-\d\d)\.", said_of(account_page(base))
        )
        on_bring_in = re.search(
            r"Next statement: Due card about (\d{4}-\d\d-\d\d)", words(bring_in_page(base))
        )

        assert on_account is not None and on_bring_in is not None
        assert on_account.group(1) == on_bring_in.group(1)
        day = date.fromisoformat(on_account.group(1))
        assert TODAY < day <= TODAY + timedelta(days=6)

    def test_DueLine_IsInTheOrdinaryColourNotTheWarningSpan(self, due_soon):
        base, _ = due_soon
        page = account_page(base)

        warned = [s.text() for s in elements(parse(page), "span") if "age" in s.classes]

        assert not [text for text in warned if "Next statement due" in text]
        assert not [text for text in warned if "Nothing to check against" in text]

    def test_AccountPage_StillSaysWhatAddsUp(self, due_soon):
        base, _ = due_soon

        said = trust_line(account_page(base)).text()

        assert "Adds up to the known balances to" in said


class TestAStatementThatIsOverdue:
    def test_AccountPage_ReturnsToTheWarningWording(self, overdue):
        base, newest = overdue

        said = trust_line(account_page(base)).text()

        assert f"Nothing to check against since {newest.isoformat()}" in said
        assert "Next statement due" not in said

    def test_WarningClause_IsInTheWarningSpan(self, overdue):
        base, _ = overdue

        spans = elements(parse(account_page(base)), "span")
        warned = [s.text() for s in spans if "age" in s.classes]

        assert any("Nothing to check against since" in text for text in warned)

    def test_BringIn_ListsTheStatementAsWantedAndDoesNotSayOneIsExpected(self, overdue):
        base, _ = overdue

        said = words(bring_in_page(base))

        assert "Wanted: 1 statement for 1 account" in said
        assert "Next statement: Due card" not in said
