"""Today's one line about the month: present only where a commitment is overdue or an account is
short, never with an amount, and a household that confirmed nothing pays no more than a select.

The world is `this_month_world` (clock pinned to 2026-09-15): the insurance is overdue since the
5th, and with `short_account` Bills is short before the 25th.
"""

from __future__ import annotations

import re

import httpx

from this_month_world import served

LINE = re.compile(r'<p class="muted monthline">(.*?)</p>', re.S)


def line_of(base: str) -> str:
    found = LINE.search(httpx.get(f"{base}/", timeout=60).text)
    return re.sub(r"<[^>]+>", "", found.group(1)) if found else ""


class TestTodaysMonthLine:
    def test_Today_WhenACommitmentIsOverdue_SaysSoAndLeadsToThePage(self, tmp_path, monkeypatch):
        with served(tmp_path, monkeypatch) as (base, _):
            page = httpx.get(f"{base}/", timeout=60).text

        assert "1 commitment overdue. " in LINE.search(page).group(1)
        assert 'href="/this-month"' in LINE.search(page).group(1)

    def test_Today_WhenNothingIsOverdueAndEveryAccountIsFunded_SaysNothing(
        self, tmp_path, monkeypatch
    ):
        with served(tmp_path, monkeypatch, insurance_paid=True) as (base, _):
            assert line_of(base) == ""

    def test_Today_WhenAnAccountIsShort_NamesItAndTheDay(self, tmp_path, monkeypatch):
        with served(tmp_path, monkeypatch, short_account=True, insurance_paid=True) as (base, _):
            assert line_of(base) == "Bills is short before 2026-09-25. See this month"

    def test_Today_WhenBothAreSo_SaysBothInOneLine(self, tmp_path, monkeypatch):
        with served(tmp_path, monkeypatch, short_account=True) as (base, _):
            assert line_of(base) == (
                "Bills is short before 2026-09-25; 1 commitment overdue. See this month"
            )

    def test_Today_WhenTheAccountsTransactionsDoNotReachTheDay_SaysNothingOfOverdue(
        self, tmp_path, monkeypatch
    ):
        with served(tmp_path, monkeypatch, reaches_the_12th=False) as (base, _):
            assert line_of(base) == ""

    def test_Today_TheLineHoldsNoAmount(self, tmp_path, monkeypatch):
        with served(tmp_path, monkeypatch, short_account=True) as (base, _):
            assert "£" not in line_of(base)

    def test_Today_WhenNoCommitmentIsConfirmed_SaysNothing(self, tmp_path, monkeypatch):
        with served(tmp_path, monkeypatch, commitments=False) as (base, _):
            assert line_of(base) == ""

    def test_Today_TheLineAndThePageAgreeOnWhatIsOverdue(self, tmp_path, monkeypatch):
        with served(tmp_path, monkeypatch) as (base, _):
            today = line_of(base)
            page = httpx.get(f"{base}/this-month", timeout=60).text

        assert "1 commitment overdue" in today
        assert "1 paid, 1 due, 1 overdue" in re.sub(r"<[^>]+>", " ", page)
