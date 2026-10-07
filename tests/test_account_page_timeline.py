"""The strip of lanes on an account's own page, over the household of `coverage_timeline_world`,
served by the real application with the day fixed at 2026-10-05.

The strip is the trust lane over one lane per way in, on the shared twelve months ending today
(2025-10-06 to 2026-10-05, 365 days). A day is 100 / 365 percent of a lane, from the window's
first day. The household's own docstring says, before any run, what each way in holds:

  FEED         the bank's feed covers 2026-07-01 to 2026-08-20 (two asks that overlap: one run).
  AGGREGATOR   asked for 07-01 to 08-10 and 08-20 to 09-10, with 08-11 to 08-19 never asked.
  EXPORT       files cover 07-02 to 08-25 (four that abut or overlap) and 09-12 to 09-28.
  CARD         statements cover 05-15 to 07-11 and 08-12 to 09-11; the one for 07-12 to 08-11
               is missing, which is a file wanted, drawn dashed.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta, tzinfo
from pathlib import Path

import httpx
import pytest

import obdi.web_ledger as web_ledger
from coverage_timeline_world import (
    CARD,
    LONG,
    MAIN,
    TODAY,
    build_card,
    build_main,
    land_long,
)
from obdi.account_page import read_account, strip_html
from obdi.ingest.statement_terms import keep_statement_readings
from obdi.ingest.store import Store
from obdi.ledger import build_ledger
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
    return tmp_path_factory.mktemp("account-timeline")


@pytest.fixture(scope="module", autouse=True)
def _day() -> Iterator[None]:
    patch = pytest.MonkeyPatch()
    patch.setattr(web_ledger, "datetime", _Fixed)
    yield
    patch.undo()


@pytest.fixture(scope="module")
def base(root: Path) -> Iterator[str]:
    def build(store: Store) -> None:
        build_main(root, store)
        build_card(root, store)
        keep_statement_readings(store)

    with served_store(root, build, bound=[MAIN, CARD]) as address:
        yield address


@pytest.fixture(autouse=True)
def _environment(base: str, root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    for name, value in environment_for(root).items():
        monkeypatch.setenv(name, value)


def page(base: str, ref: str, month: str = "") -> str:
    params = {"ref": ref, **({"month": month} if month else {})}
    response = httpx.get(f"{base}/ledger", params=params, timeout=120)
    assert response.status_code == 200
    return response.text


def strip_of(text: str) -> str:
    return re.search(r'<a class="tap strip".*?</a>', text, re.S).group(0)  # type: ignore[union-attr]


def lanes_of(strip: str) -> dict[str, list[tuple[str, str, str]]]:
    """Each lane's name and the cells in it as (class, left, width)."""
    found: dict[str, list[tuple[str, str, str]]] = {}
    for name, bar in re.findall(
        r'<span class="lane(?: first)?">([^<]*)</span>'
        r'<span class="bar" aria-hidden="true">(.*?)</span>',
        strip,
    ):
        found[name] = re.findall(r'<i class="([^"]*)" style="left:([\d.]+%);width:([\d.]+%)"', bar)
    return found


class TestTheLanes:
    def test_Main_HasTheTrustLaneOverOneLanePerWayInAndNoLaneForANoWayThatHoldsNothing(self, base):
        lanes = lanes_of(strip_of(page(base, MAIN)))

        assert list(lanes) == ["Trust", "Feed", "Aggregator", "Export file"]

    def test_Main_DrawsEachWayInOverTheDaysItHolds(self, base):
        lanes = lanes_of(strip_of(page(base, MAIN)))

        def cells(name: str) -> list[tuple[str, str, str]]:
            return [cell for cell in lanes[name] if cell[0] == "b-src"]

        assert cells("Feed") == [("b-src", *where(date(2026, 7, 1), date(2026, 8, 20)))]
        assert cells("Aggregator") == [
            ("b-src", *where(date(2026, 7, 1), date(2026, 8, 10))),
            ("b-src", *where(date(2026, 8, 20), date(2026, 9, 10))),
        ]
        assert cells("Export file") == [
            ("b-src", *where(date(2026, 7, 2), date(2026, 8, 25))),
            ("b-src", *where(date(2026, 9, 12), date(2026, 9, 28))),
        ]

    def test_Card_DrawsItsStatementsAndDashesTheOneThatIsWanted(self, base):
        lanes = lanes_of(strip_of(page(base, CARD)))

        assert list(lanes) == ["Trust", "Statements"]
        assert lanes["Statements"] == [
            ("b-src", *where(date(2026, 5, 15), date(2026, 7, 11))),
            ("b-src", *where(date(2026, 8, 12), date(2026, 9, 11))),
            ("b-want", *where(date(2026, 7, 12), date(2026, 8, 11))),
        ]
        assert ("b-want", *where(date(2026, 7, 12), date(2026, 8, 11))) in lanes["Trust"]

    def test_Trust_UsesTheSharedScaleSoTwoAccountsWithTheSameDatesDrawTheSameWidths(self, base):
        main = lanes_of(strip_of(page(base, MAIN)))["Feed"][0]
        month_marks = re.findall(
            r'<span style="left:([\d.]+)%">(\w+)</span>', strip_of(page(base, MAIN))
        )

        assert main[2] == where(date(2026, 7, 1), date(2026, 8, 20))[1]
        assert month_marks[0] == ("0.00", "Oct"), "the window begins part-way through October"
        assert [name for _, name in month_marks] == ["Oct", "Dec", "Feb", "Apr", "Jun", "Aug"], (
            "every other month is named, since the strip is narrower than a list's bars"
        )


class TestTheStripIsTheWayToTheFullTimeline:
    def test_Strip_IsALinkToTheFullTimelineOfThisAccountNamedForAReaderWhoCannotSeeIt(self, base):
        strip = strip_of(page(base, MAIN))

        assert strip.startswith('<a class="tap strip" href="/coverage-timeline?ref=main">')
        assert "The full timeline, source by source" in strip
        assert "visually-hidden" in strip

    def test_Strip_SitsBetweenTheTrustSentenceAndTheThingsToDo(self, base):
        text = page(base, CARD)

        assert text.index('class="trust') < text.index('class="tap strip"')
        assert text.index('class="tap strip"') < text.index('class="todos"')
        assert text.index('class="todos"') < text.index(">Show values</button>")


class TestMasking:
    def test_Strip_IsIdenticalMaskedAndWithValuesShown(self, base):
        masked = strip_of(page(base, MAIN, "2026-09"))
        shown = httpx.post(
            f"{base}/ledger", data={"ref": MAIN, "month": "2026-09"}, timeout=120
        )

        assert shown.status_code == 200
        assert strip_of(shown.text) == masked

    def test_Strip_HoldsNoAmountAndNoDescription(self, base):
        strip = strip_of(page(base, MAIN, "2026-09"))

        for private in ("Alpha", "Charlie", "Kilo", "792.00", "925.00", "700.00"):
            assert private not in strip
        positions_removed = re.sub(r' style="[^"]*"', "", strip)
        assert not re.search(r"\d[\d,]*\.\d\d(?!\d)", positions_removed)


class TestWhenTheTimelineCannotBeRead:
    def test_Reading_WhenTheTimelineHookFails_SaysSoAndKeepsTheTrustLane(self, base, root):
        class Config:
            @staticmethod
            def coverage_timeline_compact(ref: str, today: date) -> None:
                raise RuntimeError("the store went away")

        with Store(root / "store.sqlite3") as store:
            ledger = build_ledger(store, MAIN, None, bound=True)
        reading = read_account(Config(), ledger, TODAY)

        assert reading.timeline is None
        assert reading.unread == ("The timeline by source could not be built just now.",)
        assert list(lanes_of(strip_html(reading, MAIN, TODAY))) == ["Trust"], (
            "a lane that could not be read is not drawn as an empty one"
        )

    def test_Reading_WithNoTimelineWired_DrawsTheTrustLaneAloneAndSaysNothingWentWrong(
        self, base, root
    ):
        with Store(root / "store.sqlite3") as store:
            ledger = build_ledger(store, CARD, None, bound=True)
        reading = read_account(None, ledger, TODAY)

        assert reading.unread == ()
        assert list(lanes_of(strip_html(reading, CARD, TODAY))) == ["Trust"]


class TestCost:
    def test_Strip_IsASmallFixedSize(self, base):
        assert len(strip_of(page(base, MAIN, "2026-09")).encode()) < 3000
        assert len(strip_of(page(base, CARD)).encode()) < 3000

    def test_HookStatements_WhenWarm_AreTheSameFewForEveryAccount(self, base, root):
        from obdi.cli import build_web_config

        config = build_web_config(root / "store.sqlite3")
        assert config is not None and config.coverage_timeline_compact is not None
        original = Store.__init__
        counted: list[str] = []

        def traced(self, *args, **kwargs):  # type: ignore[no-untyped-def]
            original(self, *args, **kwargs)
            self.connection.set_trace_callback(counted.append)

        hook = config.coverage_timeline_compact
        hook(MAIN, TODAY)
        hook(CARD, TODAY)
        monkey = pytest.MonkeyPatch()
        monkey.setattr(Store, "__init__", traced)
        try:
            hook(MAIN, TODAY)
            first = len(counted)
            counted.clear()
            hook(CARD, TODAY)
            second = len(counted)
        finally:
            monkey.undo()
        assert first == second
        assert first <= 20, f"{first} statements when warm"


def test_AccountWithOneWayInAndNothingWanted_DrawsOneLaneBesideTheTrustLane(tmp_path) -> None:
    with served_store(tmp_path, lambda store: land_long(tmp_path, store), bound=[LONG]) as address:
        text = httpx.get(f"{address}/ledger", params={"ref": LONG}, timeout=120).text

    lanes = lanes_of(strip_of(text))
    assert list(lanes) == ["Trust", "Export file"]
    assert not any(cell[0] == "b-want" for cells in lanes.values() for cell in cells)


def test_Fixture_Date_IsAsTheDocstringSays() -> None:
    assert date(2026, 10, 5) == TODAY
