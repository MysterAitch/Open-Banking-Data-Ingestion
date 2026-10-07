"""The "Party stated" row, its sentence, and its Bring in want, over `party_stated_world` served by
the real application with the day fixed at 2026-10-05.

The answers are in the world's docstring, decided before any run: STATED is solid and silent,
MIXED is hollow over May and June (18 described transactions, 2026-05-04 to 2026-06-24) and asks
for an export file, PDFONLY is hollow throughout and says its source states no party and asks for
nothing.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta, tzinfo
from pathlib import Path

import httpx
import pytest

import obdi.pages.web_bring_in as web_bring_in
import obdi.pages.web_ledger as web_ledger
from obdi.ingest.store import Store
from party_stated_world import (
    MIXED,
    MIXED_DESCRIBED_FIRST,
    MIXED_DESCRIBED_LAST,
    MIXED_DESCRIBED_ROWS,
    PDFONLY,
    PDFONLY_ROWS,
    STATED,
    TODAY,
    build_party_household,
)
from served_store import environment_for, served_store

WINDOW_START = TODAY - timedelta(days=364)


def where(first: date, last: date) -> tuple[str, str]:
    """Where days `first` to `last` are drawn on the shared scale, as the page writes them."""
    return (
        f"{(first - WINDOW_START).days / 365 * 100:.2f}%",
        f"{((last - first).days + 1) / 365 * 100:.2f}%",
    )


class _Fixed(datetime):
    @classmethod
    def now(cls, tz: tzinfo | None = None) -> datetime:
        return datetime(TODAY.year, TODAY.month, TODAY.day, 12, tzinfo=UTC)


@pytest.fixture(scope="module")
def root(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return tmp_path_factory.mktemp("party-pages")


@pytest.fixture(scope="module", autouse=True)
def _day() -> Iterator[None]:
    patch = pytest.MonkeyPatch()
    patch.setattr(web_ledger, "datetime", _Fixed)
    patch.setattr(web_bring_in, "datetime", _Fixed)
    yield
    patch.undo()


@pytest.fixture(scope="module")
def base(root: Path) -> Iterator[str]:
    def build(store: Store) -> None:
        build_party_household(store)

    with served_store(root, build, bound=[STATED, MIXED, PDFONLY]) as address:
        yield address


@pytest.fixture(autouse=True)
def _environment(base: str, root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    for name, value in environment_for(root).items():
        monkeypatch.setenv(name, value)


def page(base: str, path: str, **params: str) -> str:
    response = httpx.get(f"{base}{path}", params=params, timeout=120)
    assert response.status_code == 200
    return response.text


def strip_of(text: str) -> str:
    return re.search(r'<a class="tap strip".*?</a>', text, re.S).group(0)  # type: ignore[union-attr]


def lanes_of(strip: str) -> dict[str, list[tuple[str, str, str]]]:
    found: dict[str, list[tuple[str, str, str]]] = {}
    for name, bar in re.findall(
        r'<span class="lane(?: first)?">([^<]*)</span>'
        r'<span class="bar" aria-hidden="true">(.*?)</span>',
        strip,
    ):
        found[name] = re.findall(r'<i class="([^"]*)" style="left:([\d.]+%);width:([\d.]+%)"', bar)
    return found


def wanted_sections(text: str) -> dict[str, str]:
    """Each account's wanted files as the words a reader sees, by account."""
    found = {}
    for ref, body in re.findall(
        r'<section class="bi-account" id="account-([^"]*)".*?(<ul class="bi-files">.*?</ul>)',
        text,
        re.S,
    ):
        found[ref] = " ".join(re.sub(r"<[^>]+>", " ", body).split())
    return found


def notes_of(text: str) -> list[str]:
    return re.findall(r'<p class="muted party-note">([^<]*)</p>', text)


class TestTheRowOnTheAccountPage:
    def test_Stated_HasASolidRowOverItsWholeStretchAndNoNote(self, base):
        text = page(base, "/ledger", ref=STATED)

        row = lanes_of(strip_of(text))["Party stated"]
        assert row == [("b-src", *where(date(2026, 8, 3), date(2026, 9, 28)))]
        assert notes_of(text) == []

    def test_Mixed_IsSolidThroughAprilAndHollowOverMayAndJuneOnly(self, base):
        row = lanes_of(strip_of(page(base, "/ledger", ref=MIXED)))["Party stated"]

        assert row == [
            ("b-src", *where(date(2026, 1, 5), date(2026, 4, 27))),
            ("b-desc", *where(MIXED_DESCRIBED_FIRST, MIXED_DESCRIBED_LAST)),
        ]

    def test_Mixed_SaysHowManyAndWhichDaysAndWhatWouldFixIt(self, base):
        assert notes_of(page(base, "/ledger", ref=MIXED)) == [
            f"{MIXED_DESCRIBED_ROWS} transactions from {MIXED_DESCRIBED_FIRST.isoformat()} to "
            f"{MIXED_DESCRIBED_LAST.isoformat()} are named by the description only - an export "
            "file for those months would state the party."
        ]

    def test_PdfOnly_SaysItsSourceStatesNoPartyAndAsksForNoFile(self, base):
        text = page(base, "/ledger", ref=PDFONLY)

        assert notes_of(text) == [
            f"{PDFONLY_ROWS} transactions from 2026-07-06 to 2026-09-14 are named by the "
            "description only - this source states no party."
        ]
        assert "an export file for those months" not in text

    def test_Row_IsTheLastLaneAfterStatementsAndIsNamedOnceInTheKey(self, base):
        text = page(base, "/ledger", ref=PDFONLY)

        assert list(lanes_of(strip_of(text)))[-1] == "Party stated"
        assert text.count("<b>Party stated</b>") == 1

    def test_Notes_CarryNoDescriptionAndNoAmount(self, base):
        notes = notes_of(page(base, "/ledger", ref=MIXED))

        assert notes
        for note in notes:
            assert "LOCAL SERVICE" not in note.upper()
            assert not re.search(r"[£]|\d+\.\d\d", note)


class TestTheNoteOnTheFullTimeline:
    def test_Mixed_SaysTheSameSentenceOnceBeneathTheChart(self, base):
        text = page(base, "/coverage-timeline", ref=MIXED)

        sentence = (
            f"{MIXED_DESCRIBED_ROWS} transactions from {MIXED_DESCRIBED_FIRST.isoformat()} to "
            f"{MIXED_DESCRIBED_LAST.isoformat()} are named by the description only - an export "
            "file for those months would state the party."
        )
        assert text.count(sentence) == 1

    def test_Stated_SaysNothingOfTheKind(self, base):
        assert "named by the description only" not in page(base, "/coverage-timeline", ref=STATED)


class TestTheNoteOnToday:
    """Today's row has one slot: what the account waits for comes first, then a note on its
    declared terms, then this. The fixture's accounts all wait for a balance, so the page shows
    the waiting flag (as it must); the hook that fills the quiet slot is read directly."""

    def test_Mixed_IsGivenTheCountForItsQuietSlot(self, base, root):
        from obdi.cli import build_web_config

        config = build_web_config(root / "store.sqlite3")
        assert config is not None
        notes = config.account_term_notes(TODAY)

        said = f"{MIXED_DESCRIBED_ROWS} transactions named by the description only"
        assert notes == {MIXED: said}

    def test_TodayStillShowsTheWaitingFlagInsteadOfTheNote(self, base):
        text = page(base, "/")

        assert "Balance to confirm" in text
        assert "named by the description only" not in text


class TestTheWantOnBringIn:
    def test_Mixed_IsAskedForAnExportCoveringExactlyItsDescribedMonths(self, base):
        sections = wanted_sections(page(base, "/bring-in"))

        assert sections[MIXED] == (
            f"Export {MIXED_DESCRIBED_FIRST.isoformat()} to {MIXED_DESCRIBED_LAST.isoformat()} "
            f"(2 months) {MIXED_DESCRIBED_ROWS} transactions there are named by the "
            "description only"
        )

    def test_PdfOnlyAndStated_AreAskedForNothing(self, base):
        assert set(wanted_sections(page(base, "/bring-in"))) == {MIXED}

    def test_TheWant_CannotBeSetAside(self, base):
        files = re.findall(r'<li class="bi-file[^"]*">.*?</li>', page(base, "/bring-in"), re.S)
        party = [f for f in files if "named by the description only" in f]

        assert len(party) == 1
        assert "Set aside" not in party[0]
