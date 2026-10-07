"""The "About this account" fold lists what is declared of an account, in the quiet style of a
transaction's fold, and seals what is a figure until the owner asks to see values.

The household and what each account declares are in `account_about_world`, decided before the
first run.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date
from pathlib import Path

import httpx
import pytest

from account_about_world import (
    ACCOUNTS,
    BARE,
    DATED,
    MAIN,
    TERMS,
    ahead,
    fold_of,
    lines_of,
    masked,
    serve,
    set_environment,
    shown,
    windows_of,
    words,
)
from obdi.account_about import date_bases
from obdi.ingest.accounts import ARCHIVE_BASIS_PREFIX, AccountRecord, AccountRef
from page_dom import Node, elements, parse


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


class TestTheFoldOnTheAccountPage:
    @pytest.mark.parametrize("ref", ACCOUNTS)
    def test_Page_HoldsTheFoldBetweenKnownBalancesAndLockingIn(self, base, ref):
        more = next(
            n for n in elements(parse(masked(base, ref)), "div") if "ledger-more" in n.classes
        )
        summaries = [
            child.text().split(" (")[0]
            for fold in more.children
            if isinstance(fold, Node) and fold.tag == "details"
            for child in fold.children
            if isinstance(child, Node) and child.tag == "summary"
        ]

        assert summaries.index("About this account") == summaries.index("Known balances") + 1
        assert summaries.index("Locking in") == summaries.index("About this account") + 1

    def test_Fold_OnAnAccountDeclaredWithOnlyAName_SaysNothingBeyondItsNameIsDeclared(self, base):
        page = masked(base, BARE)

        assert lines_of(page) == ["Nothing beyond its name is declared. Declare its terms"]
        link = next(iter(elements(fold_of(page), "a")))
        assert link.attrs["href"] == f"/edit-account?ref={BARE}"

    def test_Fold_OnADeclaredCard_NamesItsKindParentAndDatesWithTheirBasisInWords(self, base):
        said = words(masked(base, TERMS))

        assert "Kind credit-card" in said
        assert "Parent Main" in said
        # The registry keeps one note for every date, so an inference is said of both.
        assert "Opened 2020-03-01 - inferred from the first and last movement" in said
        assert "Closed 2031-01-01 - inferred from the first and last movement" in said

    def test_Fold_DatesNobodyInferred_AreSaidToBeStated(self, base):
        said = words(masked(base, DATED))

        assert "Opened 2019-05-01 - stated" in said and "Closed 2031-02-01 - stated" in said

    def test_DateBases_ForAnArchiveInferenceFromTheListings_DescribeTheClosingOnly(self):
        record = AccountRecord(
            ref=AccountRef("old-space"),
            opened=date(2020, 1, 1),
            closed=date(2021, 1, 1),
            date_basis=f"{ARCHIVE_BASIS_PREFIX}2021-01-01",
        )

        assert date_bases(record) == ("stated", f"{ARCHIVE_BASIS_PREFIX}2021-01-01")

    def test_Fold_ParentIsALinkToTheParentAccountsPage(self, base):
        links = [a.attrs["href"] for a in elements(fold_of(masked(base, TERMS)), "a")]

        assert f"/ledger?ref={MAIN}" in links

    def test_Fold_ListsEachWindowInDateOrderWithCurrentAndHowSoonItEnds(self, base):
        items = windows_of(masked(base, TERMS))

        assert [item.split(" - ")[0].split(" £")[0] for item in items] == [
            "Credit limit",
            "Promotional rate",
            "Purchases rate",
        ]
        credit, promotional, standard = items
        assert f"from {ahead(-400).isoformat()}" in credit and "no end date" in credit
        assert credit.endswith("current")
        assert f"{ahead(-100).isoformat()} to {ahead(20).isoformat()}" in promotional
        assert promotional.endswith("current, ends in 20 days")
        assert f"from {ahead(21).isoformat()}" in standard
        assert "current" not in standard and "ends in" not in standard

    def test_Fold_WhenMasked_SealsEveryRateAndLimitButShowsKindsAndDates(self, base):
        page = masked(base, TERMS)

        for figure in ("22.9", "4,321", "4321"):
            assert figure not in page
        sealed = [n for n in elements(fold_of(page), "span") if {"fig", "sealed"} <= n.classes]
        assert [n.text() for n in sealed] == ["£•••", "£•••", "£•••"]
        assert "Promotional rate" in words(page) and ahead(20).isoformat() in words(page)

    def test_Fold_WhenValuesAreShown_ShowsTheRatesAndTheLimit(self, base):
        said = words(shown(base, TERMS))

        assert "22.9%" in said and "0%" in said
        assert "£4321.00" in said

    def test_Fold_AnAccountThatIsNotDeclaredOrHeld_HasNoFold(self, base):
        response = httpx.get(f"{base}/ledger", params={"ref": "no-such-account"}, timeout=60)

        assert "About this account" not in response.text
