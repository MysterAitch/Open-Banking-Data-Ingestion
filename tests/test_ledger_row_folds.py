"""Each ledger transaction opens to everything the store knows of it, and one leg of a transfer
links to the other.

The invented household, over real HTTP:

    CHECK   2026-03-31  -75.00  "TO SAVINGS POT", an internal transfer, seen by the feed
                                (`feed-a`, artefact `dig-a`) and by an export (`export-b`,
                                artefact `dig-b`), both captured 2026-04-02
    CHECK   2026-03-12  -9.00   "CORNER SHOP", seen by the feed alone, with an open review flag
    SAVINGS 2026-04-01  +75.00  "FROM CHECK POT", the other leg, in the NEXT month

KNOWN ANSWERS, decided before the first run: the transfer's fold names its booked date 2026-03-31,
both sources as code each with its capture date and a link to its artefact, and a link to
SAVINGS at month 2026-04 carrying the fragment `#t-` and the other leg's own row key; SAVINGS's
2026-04 page holds a row whose id is exactly that key. Masked, the same fold keeps its dates,
sources, and links and holds no payee, amount, or reference. The closed row's own line is the
date, the description, the amount, the balance after, and its mark, and none of the fold's words.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import replace
from datetime import UTC, date, datetime
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

from obdi.core.models import RawArtefact
from obdi.ingest import pair_transfers_across_store
from obdi.ledger import row_anchor
from obdi.store import Store
from page_dom import Node, elements, parse
from served_store import environment_for, served_store
from test_ledger import land, row_id, txn

CHECK = "check-account"
SAVINGS = "savings-pot-account"
CAPTURED = datetime(2026, 4, 2, 9, 0, tzinfo=UTC)

SECRET_TEXT = ("TO SAVINGS POT", "CORNER SHOP", "FROM CHECK POT", "75.00", "9.00")


def _build(store: Store) -> None:
    for digest, source in (("dig-a", "feed-a"), ("dig-b", "export-b")):
        store.land_artefact(
            RawArtefact(
                source=source,
                account_ref=CHECK,
                fetched_at=CAPTURED,
                media_type="application/json",
                digest=digest,
                payload=b"{}",
            )
        )
    posted = date(2026, 4, 1)
    land(
        store,
        "dig-a",
        replace(
            txn(CHECK, "feed-a", "c1", date(2026, 3, 31), -7500, "TO SAVINGS POT", internal=True),
            booking_date=posted,
        ),
        txn(CHECK, "feed-a", "c2", date(2026, 3, 12), -900, "CORNER SHOP"),
    )
    land(
        store,
        "dig-b",
        replace(
            txn(CHECK, "export-b", "e1", date(2026, 3, 31), -7500, "TO SAVINGS POT", internal=True),
            booking_date=posted,
        ),
    )
    land(
        store,
        "dig-s",
        txn(SAVINGS, "feed-a", "s1", date(2026, 4, 1), 7500, "FROM CHECK POT", internal=True),
    )
    pair_transfers_across_store(store)
    store.queue_for_review(row_id(store, "c2"), "kept apart by the same-source rule")
    store.connection.commit()


@pytest.fixture(scope="module")
def root(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return tmp_path_factory.mktemp("row-folds")


@pytest.fixture(scope="module")
def base(root: Path) -> Iterator[str]:
    with served_store(root, _build, bound=[CHECK, SAVINGS]) as address:
        yield address


@pytest.fixture(autouse=True)
def _environment(base: str, root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    for name, value in environment_for(root).items():
        monkeypatch.setenv(name, value)


def masked(base: str, ref: str, month: str) -> Node:
    response = httpx.get(f"{base}/ledger", params={"ref": ref, "month": month}, timeout=60)
    assert response.status_code == 200
    return parse(response.text)


def shown(base: str, ref: str, month: str) -> Node:
    response = httpx.post(f"{base}/ledger", data={"ref": ref, "month": month}, timeout=60)
    assert response.status_code == 200
    return parse(response.text)


def links_in(node: Node, *, startswith: str = "", contains: str = "") -> list[Node]:
    return [
        e
        for e in node.descendants()
        if e.tag == "a" and e.attrs["href"].startswith(startswith) and contains in e.attrs["href"]
    ]


def row_with(root_node: Node, description: str) -> Node:
    """The listed row whose text holds `description`."""
    for item in elements(root_node, "li"):
        if "txn" in item.classes and description in item.text():
            return item
    raise AssertionError(f"no row for {description}")


def fold_of(item: Node) -> Node:
    return next(e for e in item.descendants() if "t-extra" in e.classes)


def summary_of(item: Node) -> Node:
    return next(e for e in item.descendants() if e.tag == "summary")


def anchor_of(root: Path, description: str) -> str:
    """The row key of the one stored row with this description (a row two sources report is one)."""
    with Store(root / "store.sqlite3") as store:
        (found,) = {t.entity_id for t in store.all_transactions() if t.description == description}
        return row_anchor(found)


class TestAnOpenedTransfer:
    def test_Fold_WhenOpenedOnATransfer_NamesTheBookedDateAndEverySourceWithItsCapture(
        self, base
    ):
        fold = fold_of(row_with(shown(base, CHECK, "2026-03"), "TO SAVINGS POT"))

        text = fold.text()
        assert "Booked 2026-04-01" in text
        codes = {e.text() for e in fold.descendants() if e.tag == "code"}
        assert {"feed-a", "export-b"} <= codes
        assert text.count("captured 2026-04-02") == 2
        assert len(links_in(fold, startswith="/artefact")) == 2

    def test_Fold_WhenOneLegOfATransfer_LinksToTheOtherAccountAtTheOtherLegsMonthAndRow(
        self, base, root
    ):
        fold = fold_of(row_with(shown(base, CHECK, "2026-03"), "TO SAVINGS POT"))

        links = links_in(fold, contains="#t-")
        assert len(links) == 1
        target = urlsplit(links[0].attrs["href"])
        assert target.path == "/ledger"
        assert parse_qs(target.query) == {"ref": [SAVINGS], "month": ["2026-04"]}
        assert target.fragment == f"t-{anchor_of(root, 'FROM CHECK POT')}"

    def test_OtherLeg_WhenTheLinkIsFollowed_TheRowItNamesIsOnThatPage(self, base, root):
        page = masked(base, SAVINGS, "2026-04")

        ids = {e.attrs.get("id") for e in elements(page, "li")}
        assert f"t-{anchor_of(root, 'FROM CHECK POT')}" in ids

    def test_Fold_WhenTheBankBookedItOnTheDayOnTheLine_SaysNothingOfBooking(self, base):
        fold = fold_of(row_with(shown(base, CHECK, "2026-03"), "CORNER SHOP"))

        assert "Booked" not in fold.text()

    def test_Fold_WhenTheTransferIsNotPaired_HasNoLinkToAnotherAccount(self, base):
        fold = fold_of(row_with(shown(base, CHECK, "2026-03"), "CORNER SHOP"))

        assert not links_in(fold, contains="#t-")


class TestAFlaggedRow:
    def test_Fold_WhenTheRowHasAnOpenReviewFlag_LinksToWhereItIsDecided(self, base):
        fold = fold_of(row_with(shown(base, CHECK, "2026-03"), "CORNER SHOP"))

        assert len(links_in(fold, startswith="/review-flags")) == 1

    def test_Fold_WhenTheRowHasNoReviewFlag_OffersNoLinkToDecideIt(self, base):
        fold = fold_of(row_with(shown(base, CHECK, "2026-03"), "TO SAVINGS POT"))

        assert not links_in(fold, startswith="/review-flags")


class TestMasked:
    def test_Folds_WhenMasked_HoldNoPayeeAmountOrReference(self, base):
        page = masked(base, CHECK, "2026-03")

        folds = [fold_of(item) for item in elements(page, "li") if "txn" in item.classes]
        text = " ".join(fold.text() for fold in folds)
        assert folds
        for secret in SECRET_TEXT:
            assert secret not in text

    def test_Folds_WhenMasked_StillNameDatesSourcesAndTheOtherAccount(self, base, root):
        page = masked(base, CHECK, "2026-03")

        wanted = f"t-{anchor_of(root, 'TO SAVINGS POT')}"
        item = next(li for li in elements(page, "li") if li.attrs.get("id") == wanted)
        fold = fold_of(item)
        assert "Booked 2026-04-01" in fold.text()
        assert {"feed-a", "export-b"} <= {e.text() for e in fold.descendants() if e.tag == "code"}
        assert links_in(fold, contains="#t-")


class TestTheClosedRow:
    def test_ClosedRow_WhenValuesShown_ReadsAsItAlwaysDidWithNoFoldWordsAdded(self, base):
        summary = summary_of(row_with(shown(base, CHECK, "2026-03"), "CORNER SHOP"))

        assert summary.text() == (
            "2026-03-12 CORNER SHOP out £9.00 \N{WHITE CIRCLE} not cleared yet "
            "What each source reported"
        )

    def test_ClosedRow_WhenValuesShown_DoesNotRepeatTheFoldsWords(self, base):
        summary = summary_of(row_with(shown(base, CHECK, "2026-03"), "TO SAVINGS POT"))

        for word in ("Booked", "captured", "Other leg"):
            assert word not in summary.text()


class TestFollowingALinkToTheOtherLeg:
    """A fragment never reaches the server, so a small script opens the row it names: a closed
    `details` cannot be opened by a style (skipped where no browser is available)."""

    def test_OtherLeg_WhenTheLinkIsFollowedInABrowser_ItsRowIsOpen(self, base, root):
        sync_api = pytest.importorskip("playwright.sync_api")
        wanted = f"t-{anchor_of(root, 'FROM CHECK POT')}"
        with sync_api.sync_playwright() as playwright:
            try:
                browser = playwright.chromium.launch()
            except sync_api.Error as exc:
                pytest.skip(f"no browser available: {exc}")
            page = browser.new_page(viewport={"width": 390, "height": 800})
            page.goto(f"{base}/ledger?ref={SAVINGS}&month=2026-04#{wanted}")
            opened = page.evaluate(
                "Array.from(document.querySelectorAll('li.txn')).map("
                "li => [li.id, li.querySelector('details').open])"
            )
            browser.close()

        assert opened == [[wanted, True]]

    def test_Row_WhenNoFragmentNamesIt_StaysShut(self, base):
        sync_api = pytest.importorskip("playwright.sync_api")
        with sync_api.sync_playwright() as playwright:
            try:
                browser = playwright.chromium.launch()
            except sync_api.Error as exc:
                pytest.skip(f"no browser available: {exc}")
            page = browser.new_page(viewport={"width": 390, "height": 800})
            page.goto(f"{base}/ledger?ref={SAVINGS}&month=2026-04")
            opened = page.evaluate(
                "Array.from(document.querySelectorAll('li.txn details')).map(d => d.open)"
            )
            browser.close()

        assert opened == [False]


class TestAnOtherLegAccountWithALabel:
    def test_Fold_WhenTheOtherAccountHasNoLabel_NamesItByItsReferenceAsCode(self, base):
        fold = fold_of(row_with(shown(base, CHECK, "2026-03"), "TO SAVINGS POT"))

        link = links_in(fold, contains="#t-")[0]
        assert [e.text() for e in link.descendants() if e.tag == "code"] == [SAVINGS]
