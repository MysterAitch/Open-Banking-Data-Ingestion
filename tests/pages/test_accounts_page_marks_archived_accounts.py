"""The accounts page marks an archived account, and nests Spaces under their parent.

The deployed page listed every account as "declared" or "not declared" with its kind,
parent, and row count, and an archived account showed no sign of it: four archived
Spaces looked the same as two live ones.

An invented registry, with the page's known answer worked out before the first run
(today is 2026-10-04):

    main-acct        a current account, live, three rows
      space-live-a   a Space, live
      space-live-b   a Space, live
      space-old-c    a Space archived 2025-03-01, the date stated
      space-old-d    a Space archived 2024-11-15, the date stated
    closing-soon     an account whose closing date is in the future: still live
    loose-one        held and never declared, two rows
    guess-acct       archived 2025-05-05, the date inferred from the provider's listings
    old-card         a credit card archived 2023-06-30

Live accounts come first, each Space follows its parent with the live ones before the
archived, and the archived accounts that have no parent come last. The parent's line
says "4 Spaces (2 live, 2 archived)".
"""

from __future__ import annotations

import html
import re
from datetime import date

import pytest

from obdi.ingest.accounts import ARCHIVE_BASIS_PREFIX, AccountMap, AccountRecord, AccountRef
from obdi.ingest.store import Store
from obdi.pages.web_accounts import accounts_page
from obdi.read.account_names import accounts_shown
from obdi.read.known_accounts import KnownAccount, KnownAccounts, read_known_accounts
from obdi.read.overview import ARCHIVED
from test_ledger import land, txn
from test_phone_layout import LONG_NAME, PHONE_HEIGHT, PHONE_WIDTH, _assert_fits, _measure, sync_api

TODAY = date(2026, 10, 4)
MAIN = "main-acct"
SPACES = ("space-live-a", "space-live-b", "space-old-c", "space-old-d")


def declare(
    store: Store,
    ref: str,
    *,
    kind: str = "",
    parent: str | None = None,
    closed: date | None = None,
    basis: str = "",
    label: str = "",
) -> None:
    store.declare_account(
        AccountRecord(
            ref=AccountRef(ref),
            kind=kind,
            label=label or ref.title(),
            parent=AccountRef(parent) if parent else None,
            closed=closed,
            date_basis=basis,
        )
    )


@pytest.fixture
def store(tmp_path):
    with Store(tmp_path / "archived.sqlite3") as opened:
        declare(opened, MAIN, kind="current-account")
        declare(opened, "space-live-a", kind="starling-space", parent=MAIN)
        declare(opened, "space-live-b", kind="starling-space", parent=MAIN)
        declare(
            opened, "space-old-c", kind="starling-space", parent=MAIN, closed=date(2025, 3, 1)
        )
        declare(
            opened, "space-old-d", kind="starling-space", parent=MAIN, closed=date(2024, 11, 15)
        )
        declare(opened, "closing-soon", kind="savings", closed=date(2099, 1, 1))
        declare(
            opened,
            "guess-acct",
            kind="savings",
            closed=date(2025, 5, 5),
            basis=f"{ARCHIVE_BASIS_PREFIX}2025-05-05",
        )
        declare(opened, "old-card", kind="credit-card", closed=date(2023, 6, 30))
        for number in range(3):
            land(
                opened,
                f"m{number}",
                txn(MAIN, "src-a", f"m{number}", date(2026, 9, 3), -100, "X"),
            )
        for number in range(2):
            land(
                opened,
                f"l{number}",
                txn("loose-one", "src-a", f"l{number}", date(2026, 9, 3), -100, "X"),
            )
        yield opened


def known_of(store: Store) -> KnownAccounts:
    return read_known_accounts(store, AccountMap(), accounts_shown({}, store.declared_accounts()))


def rendered(known: KnownAccounts) -> str:
    return accounts_page([], today=TODAY, known=known).decode()


def rows_of(page: str) -> dict[str, str]:
    """Each account's row, in the order the page lists them."""
    found: dict[str, str] = {}
    for block in re.split(r'(?=<div class="row")', page):
        match = re.search(r"<code>([^<]+)</code>", block)
        if block.startswith('<div class="row"') and match:
            found[html.unescape(match.group(1))] = block.split("</div>")[0]
    return found


class TestWhatTheStoreKnowsOfAnArchivedAccount:
    def test_Known_CarriesEachAccountsClosingDateAndTheBasisOfIt(self, store):
        accounts = {a.ref: a for a in known_of(store).accounts}

        assert accounts["space-old-c"].closed == date(2025, 3, 1)
        assert accounts["space-old-c"].date_basis == ""
        assert accounts["guess-acct"].date_basis.startswith(ARCHIVE_BASIS_PREFIX)
        assert accounts[MAIN].closed is None
        assert accounts["loose-one"].closed is None


class TestArchivedAccountsAreMarked:
    def test_Page_WhenAnAccountIsArchived_SaysArchivedAndTheDateBesideItsName(self, store):
        rows = rows_of(rendered(known_of(store)))

        assert f"{ARCHIVED} 2025-03-01" in rows["space-old-c"]
        assert f"{ARCHIVED} 2024-11-15" in rows["space-old-d"]
        assert f"{ARCHIVED} 2023-06-30" in rows["old-card"]
        assert rows["space-old-c"].index("Space-Old-C") < rows["space-old-c"].index(ARCHIVED)

    def test_Page_WhenTheClosingDateWasInferred_SaysInferredOnlyThere(self, store):
        rows = rows_of(rendered(known_of(store)))

        assert f"{ARCHIVED} 2025-05-05 (inferred)" in rows["guess-acct"]
        for ref in ("space-old-c", "space-old-d", "old-card"):
            assert "inferred" not in rows[ref], ref

    def test_Page_WhenAnAccountIsLiveOrClosesLater_SaysNothingOfBeingArchived(self, store):
        rows = rows_of(rendered(known_of(store)))

        for ref in (MAIN, "space-live-a", "space-live-b", "closing-soon", "loose-one"):
            assert f">{ARCHIVED} " not in rows[ref], ref

    def test_Page_WhenAnAccountIsUndeclaredWithRows_StillSaysSoAndCountsItsRows(self, store):
        rows = rows_of(rendered(known_of(store)))

        assert "not declared" in rows["loose-one"]
        assert "2 rows" in rows["loose-one"]
        assert "not declared" not in rows[MAIN]
        assert "3 rows" in rows[MAIN]


class TestTheListIsOrderedAndNested:
    def test_Page_ListsLiveAccountsFirstThenEachParentsSpacesThenArchivedAccounts(self, store):
        rows = rows_of(rendered(known_of(store)))

        assert list(rows) == [
            "closing-soon",
            "loose-one",
            MAIN,
            "space-live-a",
            "space-live-b",
            "space-old-c",
            "space-old-d",
            "guess-acct",
            "old-card",
        ]

    def test_Page_NestsEachSpaceUnderItsParentAndLeavesTopLevelAccountsFlush(self, store):
        rows = rows_of(rendered(known_of(store)))

        for ref in SPACES:
            assert "margin-left" in rows[ref], ref
        for ref in (MAIN, "closing-soon", "loose-one", "guess-acct", "old-card"):
            assert "margin-left" not in rows[ref], ref

    def test_Page_OnTheParentsLine_SaysHowManySpacesItHasLiveAndArchived(self, store):
        rows = rows_of(rendered(known_of(store)))

        assert "4 Spaces (2 live, 2 archived)" in rows[MAIN]
        assert "Spaces" not in rows["loose-one"]

    def test_Page_WhenAParentHasOneLiveSpace_UsesTheSingular(self, store):
        declare(store, "solo-main", kind="current-account")
        declare(store, "solo-space", kind="starling-space", parent="solo-main")

        rows = rows_of(rendered(known_of(store)))

        assert "1 Space (1 live, 0 archived)" in rows["solo-main"]

    def test_Page_WhenAParentIsArchivedItself_StillListsItsSpacesBeneathIt(self, store):
        declare(store, "gone-main", kind="current-account", closed=date(2024, 1, 31))
        declare(
            store,
            "gone-space",
            kind="starling-space",
            parent="gone-main",
            closed=date(2024, 1, 31),
        )

        rows = rows_of(rendered(known_of(store)))

        assert list(rows) == [
            "closing-soon",
            "loose-one",
            MAIN,
            *SPACES,
            "gone-main",
            "gone-space",
            "guess-acct",
            "old-card",
        ]

    def test_Page_WhenASpaceNamesAParentThatIsNotListed_ShowsItAtTheTopLevelUnderItsName(
        self, store
    ):
        known = known_of(store)
        orphan = KnownAccount(
            ref="orphan-space",
            label="Orphan",
            kind="starling-space",
            kind_reason="",
            parent="missing-main",
            declared=True,
            rows=0,
            closed=None,
            date_basis="",
        )

        rows = rows_of(rendered(KnownAccounts((*known.accounts, orphan), 0)))

        assert "under <code>missing-main</code>" in rows["orphan-space"]
        assert "margin-left" not in rows["orphan-space"]


class TestTheNestedListFitsAPhone:
    def test_Page_WithLongNamesOnArchivedSpaces_AtPhoneWidth_DoesNotScrollSideways(self):
        try:
            playwright = sync_api.sync_playwright().start()
        except Exception as exc:  # pragma: no cover - environment without Playwright
            pytest.skip(f"no browser available: {exc}")
        try:
            try:
                browser = playwright.chromium.launch()
            except sync_api.Error as exc:  # pragma: no cover
                pytest.skip(f"no browser available: {exc}")
            known = KnownAccounts(
                (
                    KnownAccount(
                        "main-acct", LONG_NAME, "current-account", "", "", True, 3, None, ""
                    ),
                    KnownAccount(
                        "space-old-c",
                        LONG_NAME,
                        "starling-space",
                        "",
                        "main-acct",
                        True,
                        0,
                        date(2025, 3, 1),
                        f"{ARCHIVE_BASIS_PREFIX}2025-03-01",
                    ),
                ),
                0,
            )
            page = browser.new_page(viewport={"width": PHONE_WIDTH, "height": PHONE_HEIGHT})
            try:
                page.set_content(rendered(known))
                _assert_fits(_measure(page))
            finally:
                page.close()
                browser.close()
        finally:
            playwright.stop()


