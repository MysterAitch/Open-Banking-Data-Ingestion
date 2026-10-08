"""The rent paid through a bills space, as the owner reads it on Today, This month, and the
account's page. The world and its known answers are in `flow_world`.

  2026-09-28, Bills holds 450.00: October asks it for 563.50 by 2026-10-01 (rent 450.00, electric
  79.00, gas 34.50). Today says so with the figures sealed; This month says so with them shown.
  Held at 700.00 it is funded, Today is silent, and the account page shows 136.50 surplus.

  2026-10-02, the partner's half never arrived: "Casey Wintermute's half for October has not
  arrived (expected by 2026-09-30)", 450.00 owed to you. With the landlord unpaid instead:
  "October's Rent did not go out on 2026-10-01." The external leg is on no page.
"""

from __future__ import annotations

import re
from datetime import date

import httpx

from flow_world import BILLS, CURRENT, LANDLORD, PARTNER, served
from obdi.core.masking import MASKED_TOTAL

BEFORE_RENT = date(2026, 9, 28)
AFTER_RENT = date(2026, 10, 2)


def text_of(html: str) -> str:
    body = re.sub(r"<style>.*?</style>", "", html, flags=re.S)
    spaced = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", body)).strip()
    return re.sub(r" ([.,;)])", r"\1", spaced).replace("( ", "(")


def today_masked(base: str) -> str:
    return text_of(httpx.get(f"{base}/", timeout=60).text)


def month_shown(base: str) -> str:
    return text_of(httpx.post(f"{base}/this-month", timeout=60).text)


def month_masked(base: str) -> str:
    return text_of(httpx.get(f"{base}/this-month", timeout=60).text)


def account_page(base: str, ref: str, *, shown: bool) -> str:
    if shown:
        return text_of(httpx.post(f"{base}/ledger", data={"ref": ref}, timeout=60).text)
    return text_of(httpx.get(f"{base}/ledger", params={"ref": ref}, timeout=60).text)


class TestTheBillsSpaceBeforeTheRentIsDue:
    def test_ThisMonth_WhenTheSpaceHoldsLessThanOctoberAsks_SaysWhatIsNeededByWhenAndHeld(
        self, tmp_path, monkeypatch
    ):
        with served(tmp_path, monkeypatch, BEFORE_RENT) as (base, _):
            page = month_shown(base)

        assert "Bills: £563.50 needed by 2026-10-01; £450.00 held." in page

    def test_Today_WhenTheSpaceIsShort_SaysSoWithTheFiguresSealed(self, tmp_path, monkeypatch):
        with served(tmp_path, monkeypatch, BEFORE_RENT) as (base, _):
            page = today_masked(base)

        assert f"Bills: {MASKED_TOTAL} needed by 2026-10-01; {MASKED_TOTAL} held." in page
        assert "563.50" not in page
        assert "450.00" not in page

    def test_Today_WhenTheSpaceHoldsWhatIsNeeded_IsSilentAboutIt(self, tmp_path, monkeypatch):
        with served(tmp_path, monkeypatch, BEFORE_RENT, bills_held="700.00") as (base, _):
            page = today_masked(base)

        assert "needed by" not in page

    def test_AccountPage_WhenTheSpaceHoldsMoreThanNeeded_ShowsTheSurplus(
        self, tmp_path, monkeypatch
    ):
        with served(tmp_path, monkeypatch, BEFORE_RENT, bills_held="700.00") as (base, _):
            page = account_page(base, BILLS, shown=True)

        assert "Expected" in page
        assert "£563.50 needed by 2026-10-01; £700.00 held." in page
        assert "Surplus beyond what it needs: £136.50." in page

    def test_AccountPage_WhenTheSpaceIsShort_ListsEachStashAndShowsNoSurplus(
        self, tmp_path, monkeypatch
    ):
        with served(tmp_path, monkeypatch, BEFORE_RENT) as (base, _):
            page = account_page(base, BILLS, shown=True)

        assert "Rent: £450.00 to be here by 2026-10-01" in page
        assert "Electric: £79.00 to be here by 2026-10-25" in page
        assert "Gas: £34.50 to be here by 2026-10-27" in page
        assert "Surplus" not in page

    def test_AccountPage_WhenMasked_CarriesNoFigureFromTheExpectedFold(self, tmp_path, monkeypatch):
        with served(tmp_path, monkeypatch, BEFORE_RENT, bills_held="700.00") as (base, _):
            page = account_page(base, BILLS, shown=False)

        assert "Expected" in page
        for figure in ("563.50", "700.00", "136.50", "79.00", "34.50"):
            assert figure not in page

    def test_AccountPage_WhenNoLegTouchesTheAccount_HasNoExpectedFold(self, tmp_path, monkeypatch):
        with served(tmp_path, monkeypatch, BEFORE_RENT, legs=False) as (base, _):
            page = account_page(base, BILLS, shown=True)

        assert "Expected" not in page


class TestTheLegsAfterTheRentIsDue:
    def test_ThisMonth_WhenEveryLegHappened_SaysNothingAboutLegsOrOwing(
        self, tmp_path, monkeypatch
    ):
        with served(tmp_path, monkeypatch, AFTER_RENT) as (base, _):
            page = month_shown(base)

        assert "has not arrived" not in page
        assert "did not go out" not in page
        assert "Owed to you" not in page

    def test_ThisMonth_WhenThePartnersHalfNeverArrived_SaysSoAndOwesYouThatHalf(
        self, tmp_path, monkeypatch
    ):
        with served(tmp_path, monkeypatch, AFTER_RENT, partner_paid=False) as (base, _):
            page = month_shown(base)

        assert f"{PARTNER}'s half for October has not arrived (expected by 2026-09-30)." in page
        assert f"{PARTNER} owes £450.00 for Rent for October" in page

    def test_Today_WhenThePartnersHalfNeverArrived_NamesNeitherThePersonNorTheAmount(
        self, tmp_path, monkeypatch
    ):
        with served(tmp_path, monkeypatch, AFTER_RENT, partner_paid=False) as (base, _):
            page = today_masked(base)

        assert PARTNER not in page
        assert "450.00" not in page
        assert "has not arrived (expected by 2026-09-30)" in page
        assert f"Owed to you: {MASKED_TOTAL}" in page

    def test_ThisMonth_WhenTheLandlordWasNotPaid_SaysTheRentDidNotGoOut(
        self, tmp_path, monkeypatch
    ):
        with served(tmp_path, monkeypatch, AFTER_RENT, landlord_paid=False) as (base, _):
            page = month_shown(base)

        assert "October's Rent did not go out on 2026-10-01." in page
        assert "Owed to you" not in page

    def test_ThisMonth_WhenMasked_NamesNeitherThePartnerNorTheLandlordNorAnAmount(
        self, tmp_path, monkeypatch
    ):
        with served(
            tmp_path, monkeypatch, AFTER_RENT, partner_paid=False, landlord_paid=False
        ) as (base, _):
            page = month_masked(base)

        assert PARTNER not in page
        assert LANDLORD not in page
        assert "450.00" not in page
        assert "has not arrived (expected by 2026-09-30)" in page

    def test_ThisMonth_WhenTheExternalLegIsDeclared_NoPageEverReportsIt(
        self, tmp_path, monkeypatch
    ):
        with served(
            tmp_path, monkeypatch, AFTER_RENT, partner_paid=False, landlord_paid=False
        ) as (base, _):
            page = month_shown(base)

        assert page.count("has not arrived") == 1
        assert page.count("did not go out") == 1
        assert CURRENT not in page
