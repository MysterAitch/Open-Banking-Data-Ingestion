"""Beside what is declared, the fold says what the sources state: the rate a kept statement prints
against the declared window its date falls in, and the names it prints. Each stated fact names its
source as code and its day.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date
from pathlib import Path

import pytest

from account_about_world import (
    BARE_RATE,
    DIFFERS,
    ISSUER_NAMED,
    OLD_DIFFERS,
    SAME,
    ahead,
    fold_of,
    masked,
    serve,
    set_environment,
    shown,
    stated_of,
    words,
)
from obdi.account_about import AS_DECLARED, NOT_DECLARED, StatedRate, check_rate
from obdi.account_about import DIFFERS as DIFFERENT
from obdi.accounts import AccountRecord, AccountRef, RateWindow
from page_dom import elements


@pytest.fixture(scope="module")
def root(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return tmp_path_factory.mktemp("account-about")


@pytest.fixture(scope="module")
def base(root: Path) -> Iterator[str]:
    with serve(root) as address:
        yield address


@pytest.fixture(autouse=True)
def _environment(base: str, root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    set_environment(root, monkeypatch)


class TestRatesAStatementPrints:
    def test_Fold_AStatementPrintingADifferentRate_NamesTheDifference(self, base):
        said = words(shown(base, DIFFERS))

        assert f"differs: the statement of {ahead(-30).isoformat()} prints 19.9%" in said

    def test_Fold_AStatementPrintingTheSameRate_SaysAsDeclared(self, base):
        said = words(shown(base, SAME))

        assert "as declared" in said and "differs" not in said

    def test_Fold_AStatementRateNoWindowCovers_SaysStatedNotDeclared(self, base):
        said = words(shown(base, BARE_RATE))

        assert "Cash rate 27.4% - stated, not declared" in said

    def test_Fold_StatedRatesAreSealedWhenMasked(self, base):
        for ref in (DIFFERS, BARE_RATE):
            page = masked(base, ref)

            assert "19.9" not in page and "27.4" not in page
            sealed = [n for n in elements(fold_of(page), "span") if {"fig", "sealed"} <= n.classes]
            assert sealed
            assert any("£•••" in line for line in stated_of(page))

    def test_Fold_NamesTheSourceAsCodeAndTheDayOfTheStatement(self, base):
        page = masked(base, DIFFERS)

        assert [c.text() for c in elements(fold_of(page), "code")] == ["synthetic-card"]
        assert f"statement of {ahead(-30).isoformat()}" in words(page)

    def test_Fold_EarlierStatementsPrintingTheSameRateAreOneLine(self, base):
        lines = stated_of(shown(base, OLD_DIFFERS))

        assert len(lines) == 2
        assert any(
            "as declared" in line
            and f"statement of {ahead(-10).isoformat()} (and 2 earlier)" in line
            for line in lines
        )
        assert any(
            f"differs: the statement of {ahead(-300).isoformat()} prints 6.1%" in line
            for line in lines
        )


class TestWhichWindowAStatementIsSetAgainst:
    """The pure rule, at the edges the served household does not reach."""

    CARD = AccountRecord(
        ref=AccountRef("card"),
        rates=(
            RateWindow("purchases", date(2026, 1, 1), date(2026, 3, 31), 20.0),
            RateWindow("cash", date(2026, 1, 1), None, 30.0),
        ),
    )

    @staticmethod
    def printed(kind: str, percent: float, day: date) -> StatedRate:
        return StatedRate(kind, percent, day, "synthetic-card")

    def test_Statement_OnTheWindowsLastDay_IsSetAgainstThatWindow(self):
        outcome, _ = check_rate(self.printed("purchases", 20.0, date(2026, 3, 31)), self.CARD)

        assert outcome == AS_DECLARED

    def test_Statement_TheDayAfterTheWindowEnds_IsStatedNotDeclared(self):
        outcome, window = check_rate(self.printed("purchases", 20.0, date(2026, 4, 1)), self.CARD)

        # The open-ended cash window still holds the day but is of another kind: a purchases rate
        # is not a difference from the cash rate, it is a rate nothing declares.
        assert (outcome, window) == (NOT_DECLARED, None)

    def test_Statement_OfADeclaredKindThatDiffers_DiffersFromThatKindsWindow(self):
        outcome, window = check_rate(self.printed("purchases", 19.9, date(2026, 2, 1)), self.CARD)

        assert outcome == DIFFERENT and window is not None and window.kind == "purchases"

    def test_Statement_OfTheCashKind_IsSetAgainstTheCashWindowAndNotThePurchasesOne(self):
        outcome, window = check_rate(self.printed("cash", 30.0, date(2026, 2, 1)), self.CARD)

        assert outcome == AS_DECLARED and window is not None and window.kind == "cash"

    def test_Statement_OfAKindNoWindowIsNamedFor_IsAsDeclaredWhenAnyWindowHoldingTheDayAgrees(self):
        outcome, _ = check_rate(self.printed("interest", 20.0, date(2026, 2, 1)), self.CARD)

        assert outcome == AS_DECLARED

    def test_Statement_WithNoDeclaredAccount_IsStatedNotDeclared(self):
        outcome, window = check_rate(self.printed("purchases", 20.0, date(2026, 2, 1)), None)

        assert (outcome, window) == (NOT_DECLARED, None)

    def test_Rates_PrintedToTwoPlacesThatAgreeToThatPrecision_AreTheSame(self):
        outcome, _ = check_rate(self.printed("cash", 30.001, date(2026, 2, 1)), self.CARD)

        assert outcome == AS_DECLARED


class TestNamesAStatementPrints:
    def test_Fold_TheAccountLabelAStatementPrints_HasItsDigitsMaskedUntilValuesAreShown(self, base):
        assert "Account label CARD 9999" in words(masked(base, BARE_RATE))
        assert "CARD 1234" not in masked(base, BARE_RATE)
        assert "Account label CARD 1234" in words(shown(base, BARE_RATE))

    def test_Fold_TheIssuerNameAStatementPrints_IsShown(self, base):
        said = words(masked(base, ISSUER_NAMED))

        assert "Issuer named Santander" in said
        assert "Account label EVERYDAY SAVER 9999" in said
