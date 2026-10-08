"""A joint account in Position, and the account page's press that declares one.

The world is `free_position_world`'s, whose answers were decided before the first run: the
everyday account holds 1,000.00, gas of 120.00 leaves it before the salary, so alone it has
1,000.00 held, 120.00 committed, and 880.00 free. Declared as shared, only the owner's share is
counted, and the arithmetic below was written down before the first run:

    50%  held 500.00   committed 60.00   free 440.00
    25%  held 250.00   committed 30.00   free 220.00
    40%  held 400.00   committed 48.00   free 352.00

The co-owner's name and the shares' money never reach a masked page.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import httpx
import pytest

from free_position_world import CURRENT, populate
from obdi.cli import build_web_config
from obdi.core.masking import MASKED_TOTAL
from obdi.ingest.ownership_records import OwnershipRefused
from obdi.ingest.store import Store
from section_harness import environment, serve_config
from test_position_free import accounts_of, masked, shown

PARTNER = "Casey Wintermute"


@contextmanager
def served_shared(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, percent: int | None
) -> Iterator[tuple[str, Path]]:
    db = tmp_path / "store.sqlite3"
    with Store(db) as store:
        populate(store, tmp_path)
        if percent is not None:
            owner = store.owner_entity()
            partner = store.create_empty_entity(PARTNER)
            store.declare_ownership(CURRENT, [(owner, percent), (partner, 100 - percent)])
    environment(monkeypatch, tmp_path)
    config = build_web_config(db)
    assert config is not None
    base, stop = serve_config(config)
    try:
        yield base, db
    finally:
        stop()


class TestAJointAccountInPosition:
    def test_Position_WhenAccountIsSharedHalf_CountsHalfOfHeldCommittedAndFree(
        self, tmp_path, monkeypatch
    ):
        with served_shared(tmp_path, monkeypatch, 50) as (base, _):
            mine = accounts_of(shown(base))["everyday"]

        assert "Held in credit £500.00" in mine
        assert "Committed before the next income £60.00" in mine
        assert "Free £440.00" in mine

    def test_Position_WhenAccountIsSharedHalf_SaysItCountsYourHalfOfTheBalance(
        self, tmp_path, monkeypatch
    ):
        with served_shared(tmp_path, monkeypatch, 50) as (base, _):
            mine = accounts_of(shown(base))["everyday"]

        assert "your half of the balance, stated by you on" in mine

    @pytest.mark.parametrize(
        ("percent", "held", "committed", "free", "said"),
        [
            (25, "£250.00", "£30.00", "£220.00", "your quarter of the balance"),
            (40, "£400.00", "£48.00", "£352.00", "your 40% of the balance"),
        ],
    )
    def test_Position_WhenAccountIsSharedOtherThanHalf_ScalesEveryFigureAndSaysTheShare(
        self, tmp_path, monkeypatch, percent, held, committed, free, said
    ):
        with served_shared(tmp_path, monkeypatch, percent) as (base, _):
            mine = accounts_of(shown(base))["everyday"]

        assert f"Held in credit {held}" in mine
        assert f"Committed before the next income {committed}" in mine
        assert f"Free {free}" in mine
        assert said in mine

    def test_Position_WhenNothingIsDeclared_CountsTheWholeAccountAndSaysNothingOfShares(
        self, tmp_path, monkeypatch
    ):
        with served_shared(tmp_path, monkeypatch, None) as (base, _):
            mine = accounts_of(shown(base))["everyday"]

        assert "Held in credit £1,000.00" in mine
        assert "your half" not in mine
        assert "of the balance" not in mine

    def test_Position_WhenAccountIsSharedAndMasked_ShowsNoFigureAndNoCoOwnerName(
        self, tmp_path, monkeypatch
    ):
        with served_shared(tmp_path, monkeypatch, 50) as (base, _):
            page = masked(base)

        assert PARTNER not in page
        assert "500.00" not in page
        assert "60.00" not in page
        assert MASKED_TOTAL in page


class TestDeclaringOwnershipFromTheAccountPage:
    def press(self, base: str, **fields: str) -> httpx.Response:
        return httpx.post(
            f"{base}/account-ownership", data={"ref": CURRENT, "month": "", **fields}, timeout=60
        )

    def test_Press_WhenSharedWithAPartner_CountsYourShareFromThenOn(self, tmp_path, monkeypatch):
        with served_shared(tmp_path, monkeypatch, None) as (base, db):
            response = self.press(base, action="joint", partner=PARTNER, share="50")
            page = shown(base)

            assert response.status_code == 200
            with Store(db) as store:
                (declared,) = store.account_owners()[CURRENT][:1]
                assert declared.percent == 50

        assert "Held in credit £500.00" in accounts_of(page)["everyday"]

    def test_Press_WhenSharedAccountIsMadeYoursAlone_CountsTheWholeAgain(
        self, tmp_path, monkeypatch
    ):
        with served_shared(tmp_path, monkeypatch, 50) as (base, _):
            self.press(base, action="sole")
            page = shown(base)

        assert "Held in credit £1,000.00" in accounts_of(page)["everyday"]

    @pytest.mark.parametrize("share", ["0", "100", "-5", "half", ""])
    def test_Press_WhenYourShareIsNotFromOneToNinetyNine_IsRefusedAndNothingIsDeclared(
        self, tmp_path, monkeypatch, share
    ):
        with served_shared(tmp_path, monkeypatch, None) as (base, db):
            response = self.press(base, action="joint", partner=PARTNER, share=share)

            assert response.status_code == 400
            with Store(db) as store:
                assert store.account_owners() == {}

    def test_Press_WhenNoCoOwnerIsNamed_IsRefused(self, tmp_path, monkeypatch):
        with served_shared(tmp_path, monkeypatch, None) as (base, db):
            response = self.press(base, action="joint", partner="  ", share="50")

            assert response.status_code == 400
            with Store(db) as store:
                assert store.account_owners() == {}

    def test_Press_WhenTheAccountIsNotHeld_IsRefusedAndNoEntityIsMade(
        self, tmp_path, monkeypatch
    ):
        with served_shared(tmp_path, monkeypatch, None) as (base, db):
            response = httpx.post(
                f"{base}/account-ownership",
                data={
                    "ref": "no-such-account",
                    "action": "joint",
                    "partner": PARTNER,
                    "share": "50",
                },
                timeout=60,
            )

            assert response.status_code == 400
            with Store(db) as store:
                assert store.entity_named(PARTNER) is None

    def test_Press_AnswerHoldsNeitherTheCoOwnerNorAnAmount(self, tmp_path, monkeypatch):
        with served_shared(tmp_path, monkeypatch, None) as (base, _):
            response = self.press(base, action="joint", partner=PARTNER, share="50")

        assert PARTNER not in response.text
        assert "500.00" not in response.text

    def test_AccountPage_WhenSharedAndMasked_SaysTheSharesAndMasksTheCoOwner(
        self, tmp_path, monkeypatch
    ):
        with served_shared(tmp_path, monkeypatch, 40) as (base, _):
            page = httpx.get(f"{base}/ledger", params={"ref": CURRENT}, timeout=60).text

        assert "You (40%)" in page
        assert "(60%)" in page
        assert PARTNER not in page


class TestTheStoreRefusesWhatIsNotOwnership:
    def test_Declare_WhenSharesDoNotAddToAHundred_IsRefused(self, tmp_path):
        with Store(tmp_path / "s.sqlite3") as store:
            owner = store.owner_entity()
            other = store.create_empty_entity(PARTNER)
            with pytest.raises(OwnershipRefused):
                store.declare_ownership("current-main", [(owner, 50), (other, 40)])
            assert store.account_owners() == {}

    def test_Declare_WhenTheOwnerIsNotAmongTheOwners_IsRefused(self, tmp_path):
        with Store(tmp_path / "s.sqlite3") as store:
            store.owner_entity()
            one = store.create_empty_entity("Ainsley Fenwick")
            two = store.create_empty_entity(PARTNER)
            with pytest.raises(OwnershipRefused):
                store.declare_ownership("current-main", [(one, 50), (two, 50)])

    def test_Declare_WhenAnOwnerHasNoShare_IsRefused(self, tmp_path):
        with Store(tmp_path / "s.sqlite3") as store:
            owner = store.owner_entity()
            other = store.create_empty_entity(PARTNER)
            with pytest.raises(OwnershipRefused):
                store.declare_ownership("current-main", [(owner, 100), (other, 0)])

    def test_Declare_WhenAnEntityIsNamedTwice_IsRefused(self, tmp_path):
        with Store(tmp_path / "s.sqlite3") as store:
            owner = store.owner_entity()
            with pytest.raises(OwnershipRefused):
                store.declare_ownership("current-main", [(owner, 50), (owner, 50)])

    def test_Declare_WhenAnEntityDoesNotExist_IsRefused(self, tmp_path):
        with Store(tmp_path / "s.sqlite3") as store:
            owner = store.owner_entity()
            with pytest.raises(OwnershipRefused):
                store.declare_ownership("current-main", [(owner, 50), (9999, 50)])

    def test_OwnerEntity_WhenAskedTwice_IsTheSameEntityAndNamedMeUnlessTaken(self, tmp_path):
        with Store(tmp_path / "s.sqlite3") as store:
            taken = store.create_empty_entity("Me")
            first = store.owner_entity()
            assert store.owner_entity() == first
            assert first != taken
            assert store.entity_named("Me (owner)") == first
