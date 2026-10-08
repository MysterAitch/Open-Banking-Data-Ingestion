"""The Position page's four figures per account, through the served page.

The world and the answers decided before the first run are in `free_position_world`. The page is
read the way a person does: the masked GET, and the POST that shows values. Every sentence quoted
here is one the page says; the amounts are the world's.
"""

from __future__ import annotations

import re
from datetime import timedelta

import httpx
import pytest

from free_position_world import (
    CARD,
    CURRENT,
    GAS,
    GYM,
    SALARY,
    STREAMING,
    ahead,
    commit,
    served,
    today,
)
from obdi.cli import build_web_config
from obdi.core.masking import MASKED_TOTAL
from obdi.ingest.store import Store
from section_harness import environment, serve_config

HEADING = "<h2>Held, owed, committed, and free</h2>"


def section(page: str) -> str:
    start = page.index(HEADING)
    return page[start : page.index("<h2>", start + len(HEADING))]


def text_of(html: str) -> str:
    spaced = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html)).strip()
    return re.sub(r" ([.,)])", r"\1", spaced).replace("( ", "(")


def accounts_of(page: str) -> dict[str, str]:
    """Each account's four figures as text, by the account's name as the page writes it."""
    body = section(page)
    body = body[body.index('<ul class="pos-left-out">') :]
    found = {}
    for chunk in body.split("</li></ul></li>"):
        if "<li><strong>" not in chunk:
            continue
        name = re.search(r"<li><strong>(.*?)</strong>", chunk)
        assert name, chunk
        found[text_of(name.group(1)).casefold()] = text_of(chunk)
    return found


def shown(base: str) -> str:
    return httpx.post(f"{base}/position", timeout=60).text


def masked(base: str) -> str:
    return httpx.get(f"{base}/position", timeout=60).text


@pytest.fixture
def confirmed(tmp_path, monkeypatch):
    with served(tmp_path, monkeypatch) as (base, db):
        yield base, db


class TestWithTheSalaryConfirmed:
    def test_Committed_WhenOneIsDueBeforeTheSalaryAndOneAfter_HoldsOnlyTheOneBefore(
        self, confirmed
    ):
        base, _ = confirmed

        mine = accounts_of(shown(base))["everyday"]

        assert (
            f"Committed before the next income £120.00. 1 confirmed to leave before "
            f"{ahead(12).isoformat()}." in mine
        )
        assert f"£120.00 due on {ahead(3).isoformat()} to {GAS}" in mine
        assert GYM not in mine

    def test_Free_IsHeldLessCommitted(self, confirmed):
        base, _ = confirmed

        mine = accounts_of(shown(base))["everyday"]

        assert "Held in credit £1,000.00" in mine
        assert "Free £880.00. Held less committed." in mine

    def test_Held_SaysHowTheBalanceIsKnown(self, confirmed):
        base, _ = confirmed

        mine = accounts_of(shown(base))["everyday"]

        assert f"stated by you on {(today() - timedelta(days=2)).isoformat()}" in mine

    def test_Card_ShowsWhatIsOwedAndThatNoLimitIsDeclared(self, confirmed):
        base, _ = confirmed

        card = accounts_of(shown(base))["visa"]

        assert "Owed £250.00 (stated by you on" in card
        assert "No limit declared." in card
        assert "Held nothing" in card

    def test_Card_CountsItsCommitmentsToTheHouseholdsNextIncome(self, confirmed):
        base, _ = confirmed

        card = accounts_of(shown(base))["visa"]

        assert f"£9.99 due on {ahead(5).isoformat()} to {STREAMING}" in card

    def test_CurrentAccount_OwesNothing(self, confirmed):
        base, _ = confirmed

        assert "Owed nothing" in accounts_of(shown(base))["everyday"]


class TestOnTheBoundary:
    def test_Committed_WhenACommitmentFallsOnTheSalaryDay_IsNotCountedBeforeIt(
        self, tmp_path, monkeypatch
    ):
        with served(tmp_path, monkeypatch) as (base, db):
            with Store(db) as store:
                commit(store, "Same Day Rent", CURRENT, "out", 77700, ahead(12).day)

            mine = accounts_of(shown(base))["everyday"]

        assert "Same Day Rent" not in mine
        assert "£777.00" not in mine

    def test_Committed_WhenACommitmentHasEnded_IsNotCounted(self, tmp_path, monkeypatch):
        with served(tmp_path, monkeypatch) as (base, db):
            with Store(db) as store:
                commit(
                    store, "Old Insurance", CURRENT, "out", 55500, ahead(4).day,
                    ended=today() - timedelta(days=30),
                )

            mine = accounts_of(shown(base))["everyday"]

        assert "Old Insurance" not in mine
        assert "£555.00" not in mine


class TestWithTheSalaryOnlyDetected:
    def test_Committed_WhenNoIncomeIsConfirmed_SaysSoAndUsesTheRhythmsDate(
        self, tmp_path, monkeypatch
    ):
        with served(tmp_path, monkeypatch, confirm_salary=False, salary_rows=True) as (base, _):
            mine = accounts_of(shown(base))["everyday"]

        assert (
            f"No income is confirmed; the next expected by rhythm is {ahead(12).isoformat()}."
            in mine
        )
        assert "Committed before the next income £120.00." in mine

    def test_Committed_WhenNoIncomeIsConfirmedOrSeen_SaysWhatIsMissingAndMakesNoFreeFigure(
        self, tmp_path, monkeypatch
    ):
        with served(tmp_path, monkeypatch, confirm_salary=False) as (base, _):
            mine = accounts_of(shown(base))["everyday"]

        assert "No income is confirmed or detected into this account" in mine
        assert "Cannot be worked out: there is no next income" in mine
        assert "£880.00" not in mine


class TestAnAccountWithNoKnownBalance:
    def test_Held_SaysNotKnownSinceTheFirstRowAndMakesNoFreeFigure(self, confirmed):
        base, _ = confirmed

        pot = accounts_of(shown(base))["old-pot"]

        first = (today() - timedelta(days=40)).isoformat()
        assert f"Held not known since {first}" in pot
        assert "Cannot be worked out: the balance is not known." in pot

    def test_Totals_LeaveItOutAndSayHowManyWere(self, confirmed):
        base, _ = confirmed

        totals = text_of(section(shown(base)).split("<details>")[0])

        assert "Held in credit £1,000.00, counting 1 of 2 accounts. 1 left out" in totals
        assert "Owed £250.00, counting 1 of 1 account." in totals
        assert "Free £880.00, counting 1 of 3 accounts. 2 left out" in totals


class TestMasked:
    def test_Get_ShowsLabelsBasesCountsAndDatesAndNoAmount(self, confirmed):
        base, _ = confirmed

        page = masked(base)
        body = text_of(section(page))

        for label in ("Held", "Owed", "Committed before the next income", "Free"):
            assert label in body
        assert "stated by you on" in body
        assert "1 confirmed to leave before" in body
        assert ahead(12).isoformat() in body
        assert MASKED_TOTAL in body
        for amount in ("1,000.00", "120.00", "880.00", "250.00", "9.99", "45.00", "3,000.00"):
            assert amount not in body, amount

    def test_Get_ShowsNoPayeeName(self, confirmed):
        base, _ = confirmed

        body = section(masked(base))

        for payee in (GAS, GYM, SALARY, STREAMING):
            assert payee not in body, payee

    def test_Post_ShowsThePayeeAndTheAmount(self, confirmed):
        base, _ = confirmed

        assert GAS in section(shown(base))


class TestPageHolds:
    def test_Page_WhenThePositionHasNoAccounts_AddsNoSection(self, tmp_path, monkeypatch):
        db = tmp_path / "empty.sqlite3"
        with Store(db):
            pass
        environment(monkeypatch, tmp_path)
        config = build_web_config(db)
        assert config is not None
        base, stop = serve_config(config)
        try:
            page = masked(base)
        finally:
            stop()

        assert HEADING not in page


def test_CardIsNotMistakenForTheCurrentAccount_ByTheirNames(confirmed):
    base, _ = confirmed

    found = accounts_of(shown(base))

    assert CARD != CURRENT
    assert set(found) >= {"everyday", "visa", "old-pot"}
