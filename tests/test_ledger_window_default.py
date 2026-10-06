"""The window every account's page opens on is the owner's to set, and is kept.

The day is fixed at 2026-10-06. Two accounts: the first holds payments dated 2026-10-01,
2026-09-01 and 2026-08-01, the second one dated 2026-08-20. Until a default is set the page
opens on RECENT (the last 30 days or 50 transactions, whichever is wider), which for accounts
this small is every payment they hold; set to the last 30 days, the first lists one payment
(2026-10-01) and the second none, and the second then falls back to the newest month.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, date, datetime, tzinfo
from pathlib import Path

import httpx
import pytest

import obdi.web_ledger as web_ledger
from obdi.errors import DataError
from obdi.ledger_scope import DEFAULT_KEY, default_key, set_default_key
from obdi.rebuild import rebuild_from_raw
from obdi.store import SCHEMA_VERSION, Store
from page_dom import elements, parse
from served_store import environment_for, served_store
from test_ledger_window import _both, _on, days_listed, forms_holding, heading, submitted
from test_space_attribution import FEED, Household, pay

FIRST, SECOND = "starling-default-first", "starling-default-second"
TODAY = date(2026, 10, 6)


class _Fixed(datetime):
    @classmethod
    def now(cls, tz: tzinfo | None = None) -> datetime:
        return datetime(TODAY.year, TODAY.month, TODAY.day, 12, tzinfo=UTC)


def _arrive(store: Store) -> None:
    home = Household(store)
    home.arrive(*_both(FIRST, "a", -100, date(2026, 10, 1), "Firsta"))
    home.arrive(*_both(FIRST, "b", -200, date(2026, 9, 1), "Firstb"))
    home.arrive(_on(pay(FIRST, FEED, "f-c", -300, 1, "Firstc"), date(2026, 8, 1)))
    home.arrive(*_both(SECOND, "d", -400, date(2026, 8, 20), "Secondd"))


@pytest.fixture(scope="module")
def root(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return tmp_path_factory.mktemp("window-default")


@pytest.fixture(scope="module", autouse=True)
def _day() -> Iterator[None]:
    patch = pytest.MonkeyPatch()
    patch.setattr(web_ledger, "datetime", _Fixed)
    yield
    patch.undo()


@pytest.fixture(scope="module")
def base(root: Path) -> Iterator[str]:
    with served_store(root, _arrive, bound=[FIRST]) as address:
        yield address


@pytest.fixture(autouse=True)
def _environment(base: str, root: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    for name, value in environment_for(root).items():
        monkeypatch.setenv(name, value)
    yield
    with Store(root / "store.sqlite3") as store:
        store.connection.execute("DELETE FROM preferences")
        store.connection.commit()


def page(base: str, ref: str = FIRST, **params: str) -> str:
    response = httpx.get(f"{base}/ledger", params={"ref": ref, **params}, timeout=60)
    assert response.status_code == 200, response.text[:300]
    return response.text


def ask_to_set(base: str, key: str, *, confirmed: bool, ref: str = FIRST) -> httpx.Response:
    data = {"ref": ref, "month": "", "default": key, **({"confirmed": "yes"} if confirmed else {})}
    return httpx.post(f"{base}/ledger-window-default", data=data, timeout=60)


def set_default(base: str, key: str) -> None:
    done = ask_to_set(base, key, confirmed=True)
    assert done.status_code == 200, done.text[:300]


def default_line(text: str) -> str:
    (line,) = [
        n.text() for n in elements(parse(text), "p") if n.text().startswith("The default is")
    ]
    return line


class TestTheDefaultUntilOneIsSet:
    def test_Page_WhenNoDefaultWasSet_OpensOnTheLastThirtyDaysOrFiftyTransactions(self, base):
        assert heading(page(base)).startswith("Last 3 transactions, 2026-08-01 to 2026-10-06")
        assert days_listed(page(base)) == ["2026-10-01", "2026-09-01", "2026-08-01"]

    def test_Control_WhenNoDefaultWasSet_SaysWhichIsTheDefaultAndThatItIsOnShow(self, base):
        assert default_line(page(base)) == (
            "The default is: last 30 days or 50 transactions, whichever is wider. "
            "It is the one on show."
        )

    def test_Control_OnTheDefaultsOwnPage_OffersNoButtonToMakeItTheDefault(self, base):
        assert not forms_holding(page(base), "Use this as the default")


class TestSettingTheDefault:
    def test_Default_WhenAskedToChange_AsksFirstAndChangesNothing(self, base, root):
        asked = ask_to_set(base, "d30", confirmed=False)

        assert "Are you sure?" in asked.text
        assert "last 30 days, whenever" in asked.text
        with Store(root / "store.sqlite3") as store:
            assert store.preference("window.account") is None

    def test_Default_WhenConfirmed_AppliesToEveryAccountsPageWithNoWindowInTheAddress(self, base):
        set_default(base, "d30")

        assert heading(page(base)) == "Last 30 days, 2026-09-07 to 2026-10-06"
        assert days_listed(page(base)) == ["2026-10-01"]
        assert heading(page(base, ref=SECOND)) == "2026-08", "falls back to the newest month"
        assert "Nothing is dated in the last 30 days" in page(base, ref=SECOND)

    def test_Default_WhenSixtyDaysIsChosen_IsOfferedAndApplies(self, base):
        set_default(base, "d60")

        assert heading(page(base)) == "Last 60 days, 2026-08-08 to 2026-10-06"
        assert heading(page(base, ref=SECOND)) == "Last 60 days, 2026-08-08 to 2026-10-06"

    def test_Default_WhenTheAddressNamesAWindow_TheAddressWins(self, base):
        set_default(base, "d30")

        assert heading(page(base, window="d90")) == "Last 90 days, 2026-07-09 to 2026-10-06"

    def test_Default_WhenTheCalendarMonthIsChosen_PagesOpenOnTheNewestMonth(self, base):
        set_default(base, "month")

        assert heading(page(base)) == "2026-10"
        assert days_listed(page(base)) == ["2026-10-01"]

    def test_Default_WhenSetFromAWindowPage_ComesBackToThatPageWithTheWindowStillOn(self, base):
        masked = page(base, window="d90")
        (form,) = forms_holding(masked, "Use this as the default")
        fields = {**submitted(form), "confirmed": "yes"}
        done = httpx.post(f"{base}/ledger-window-default", data=fields, timeout=60)

        assert heading(done.text) == "Last 90 days, 2026-07-09 to 2026-10-06"
        assert "page now opens on last 90 days" in done.text
        assert heading(page(base)) == "Last 90 days, 2026-07-09 to 2026-10-06"

    def test_Control_AfterTheDefaultChanges_ReadsBackTheNewDefaultQuietly(self, base):
        set_default(base, "d90")

        assert default_line(page(base, window="d90")).endswith("It is the one on show.")
        assert default_line(page(base)).startswith("The default is: last 90 days.")
        assert forms_holding(page(base, window="d30"), "Use this as the default")
        assert not forms_holding(page(base, window="d90"), "Use this as the default")

    def test_Control_OnACustomLengthOrDates_OffersNoButton_SinceTheyAreNotNamedWindows(self, base):
        custom = page(base, window="between", window_from="2026-09-01", window_to="2026-09-30")

        assert not forms_holding(custom, "Use this as the default")


class TestAnUnknownDefaultIsRefusedLoudly:
    def test_Default_WhenTheValueIsNotAWindow_IsRefusedAndNothingChanges(self, base, root):
        for confirmed in (False, True):
            refused = ask_to_set(base, "fortnight", confirmed=confirmed)

            assert refused.status_code == 400
            assert "not a window the page can open on" in refused.text
        with Store(root / "store.sqlite3") as store:
            assert store.preference("window.account") is None

    def test_Default_WhenAFigureIsSent_IsRefusedAndTheFigureIsNotEchoed(self, base):
        refused = ask_to_set(base, "98765.43", confirmed=True)

        assert refused.status_code == 400
        assert "98765.43" not in refused.text

    def test_SetDefault_WithAnUnknownKey_RaisesAndKeepsWhatWasThere(self, root):
        with Store(root / "store.sqlite3") as store:
            set_default_key(store, "d60")
            with pytest.raises(DataError, match="fortnight"):
                set_default_key(store, "fortnight")
            assert default_key(store) == "d60"

    def test_Page_WhenTheStoredDefaultIsOneThisReleaseDoesNotKnow_SaysSoAndDoesNotGuess(
        self, base, root
    ):
        with Store(root / "store.sqlite3") as store:
            store.set_preference("window.account", "d7")

        failed = httpx.get(f"{base}/ledger", params={"ref": FIRST}, timeout=60)

        assert failed.status_code == 500
        assert "d7" in failed.text and "is not one this page offers" in failed.text


class TestTheDefaultIsKept:
    def test_Default_AfterTheStoreIsClosedAndOpenedAgain_IsStillSet(self, base, root):
        set_default(base, "d60")

        with Store(root / "store.sqlite3") as reopened:
            assert default_key(reopened) == "d60"

    def test_Default_AfterARebuildFromRaw_IsStillSet(self, base, root):
        set_default(base, "d90")

        with Store(root / "store.sqlite3") as store:
            rebuild_from_raw(store)
            assert default_key(store) == "d90"

    def test_Default_WhenNeverSet_IsTheLastThirtyDaysOrFiftyTransactions(self, root):
        with Store(root / "store.sqlite3") as store:
            assert default_key(store) == DEFAULT_KEY == "r30"


class TestAStoreFromBeforeThePreferencesTable:
    def test_Store_StampedVersion20WithoutTheTable_GrowsItOnOpenAndTakesAPreference(self, tmp_path):
        path = tmp_path / "old.sqlite3"
        with Store(path) as store:
            store.connection.execute("DROP TABLE preferences")
            store.connection.execute(
                "UPDATE obdi_meta SET value = '20' WHERE key = 'schema_version'"
            )
            store.connection.commit()

        with Store(path) as store:
            version = store.connection.execute(
                "SELECT value FROM obdi_meta WHERE key = 'schema_version'"
            ).fetchone()
            assert str(version[0]) == str(SCHEMA_VERSION) == "21"
            set_default_key(store, "d60")
            assert default_key(store) == "d60"

    def test_Store_StampedVersion19_ReachesTheCurrentVersionWithTheTable(self, tmp_path):
        path = tmp_path / "older.sqlite3"
        with Store(path) as store:
            store.connection.execute("DROP TABLE preferences")
            store.connection.execute("DROP TABLE disregarded_balances")
            store.connection.execute(
                "UPDATE obdi_meta SET value = '19' WHERE key = 'schema_version'"
            )
            store.connection.commit()

        with Store(path) as store:
            store.set_preference("window.account", "d90")
            assert store.preference("window.account") == "d90"
