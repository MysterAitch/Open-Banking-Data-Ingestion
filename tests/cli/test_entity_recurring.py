"""A payee gathered into an entity is one recurring series, named by the entity.

KNOWN ANSWERS, decided before the first run. An invented subscription changed the name it prints
under: "FERNHOLLOW PLUS 1041" from January to April 2026, "FERNHOLLOW PLUS EU 2209" from May to
September, on the 15th of each month at the same price. Today is 2026-10-07.

  - With no entity the detector finds two monthly series: the first (four times, stopped) and the
    second (five times). Neither is wrong; together they are one subscription.
  - With the two names gathered into "Fernhollow Plus" it finds one, nine times, from January to
    September, named by the entity. Detached again, it is two.

A second subscription is billed alternately under the two names (three times each, every other
month), which no cadence fits as two series: it is not found at all until the names are gathered,
and then it is one monthly series of six.

An unrelated payee found both ways is the same series both ways, and an entity never joins
money in with money out.
"""

from __future__ import annotations

from datetime import date

import httpx
import pytest

from obdi.analysis.entities import shape_of
from obdi.analysis.recurring import find_recurring
from obdi.cli import build_web_config
from obdi.core.models import SourceTier, Transaction
from obdi.ingest.entity_records import DESCRIPTION
from obdi.ingest.pipeline import import_file
from obdi.ingest.store import Store
from page_dom import elements, parse
from section_harness import environment, serve_config

TODAY = date(2026, 10, 7)
FIRST_PRINT = "FERNHOLLOW PLUS 1041"
SECOND_PRINT = "FERNHOLLOW PLUS EU 2209"
FIRST, SECOND = shape_of(FIRST_PRINT), shape_of(SECOND_PRINT)
ENTITY = "Fernhollow Plus"


def described(names: dict[str, str]) -> dict[tuple[str, str], str]:
    """The detector's map of identifiers to entity names, for names these rows are called by their
    description: it is keyed by kind and value (`shape_entities`), and these rows state no party."""
    return {(DESCRIPTION, name): entity for name, entity in names.items()}


BOTH = described({FIRST: ENTITY, SECOND: ENTITY})

_counter = [0]


def tx(day: date, minor: int, description: str, account: str = "acct-a") -> Transaction:
    _counter[0] += 1
    return Transaction(
        account_id=account,
        amount_minor=minor,
        value_date=day,
        booking_date=day,
        description=description,
        source="synthetic",
        tier=SourceTier.SYNTHETIC,
        entity_id=f"e{_counter[0]:06d}",
    )


def renamed_subscription() -> list[Transaction]:
    first = [tx(date(2026, m, 15), -999, FIRST_PRINT) for m in (1, 2, 3, 4)]
    second = [tx(date(2026, m, 15), -999, SECOND_PRINT) for m in (5, 6, 7, 8, 9)]
    return first + second


def alternating_subscription() -> list[Transaction]:
    return [
        tx(date(2026, m, 3), -1250, "ALTERNATE ONE 77" if m % 2 else "ALTERNATE TWO EU 88")
        for m in range(3, 9)
    ]


def unrelated() -> list[Transaction]:
    return [tx(date(2026, m, 21), -4500, "ZEPHYR WATER BOARD 5521") for m in range(1, 10)]


class TestARenamedSubscription:
    def test_Detector_WithNoEntity_FindsTheTwoPrintsAsTwoSeries(self):
        found = find_recurring(renamed_subscription(), [], TODAY)

        assert sorted((s.shape, s.count) for s in found) == sorted([(FIRST, 4), (SECOND, 5)])

    def test_Detector_WhenTheNamesAreGathered_FindsOneSeriesNamedByTheEntity(self):
        found = find_recurring(renamed_subscription(), [], TODAY, entities=BOTH)

        (series,) = found
        assert series.shape == ENTITY
        assert series.count == 9
        assert series.cadence == "monthly" and series.usual_day == 15
        assert series.first_seen == date(2026, 1, 15) and series.last_seen == date(2026, 9, 15)
        assert not series.stopped

    def test_Detector_WhenOneNameIsDetachedAgain_ReturnsTheTwoSeries(self):
        only_first = described({FIRST: ENTITY})

        found = find_recurring(renamed_subscription(), [], TODAY, entities=only_first)

        assert sorted((s.shape, s.count) for s in found) == sorted([(ENTITY, 4), (SECOND, 5)])

    def test_Detector_WhenEveryNameIsDetached_IsExactlyWhatItWasBefore(self):
        rows = renamed_subscription()

        assert find_recurring(rows, [], TODAY, entities={}) == find_recurring(rows, [], TODAY)


class TestASubscriptionBilledUnderAlternatingNames:
    def test_Detector_WithNoEntity_FindsNothing(self):
        assert find_recurring(alternating_subscription(), [], TODAY) == []

    def test_Detector_WhenTheNamesAreGathered_FindsOneMonthlySeries(self):
        names = described(
            dict.fromkeys(
                (shape_of("ALTERNATE ONE 77"), shape_of("ALTERNATE TWO EU 88")), "Alternate"
            )
        )

        (series,) = find_recurring(alternating_subscription(), [], TODAY, entities=names)

        assert series.shape == "Alternate" and series.count == 6 and series.cadence == "monthly"


class TestWhatAnEntityMustNotDo:
    def test_Detector_ForAPayeeWithNoEntity_IsUnchangedByEntitiesElsewhere(self):
        rows = renamed_subscription() + unrelated()

        alone = find_recurring(unrelated(), [], TODAY)
        with_entity = [
            s for s in find_recurring(rows, [], TODAY, entities=BOTH) if s.shape != ENTITY
        ]

        assert alone == with_entity and len(alone) == 1

    def test_Detector_WhenAnEntityHoldsBothMoneyOutAndMoneyIn_KeepsThemApart(self):
        refunds = [tx(date(2026, m, 15), 999, SECOND_PRINT) for m in range(1, 10)]
        payments = [tx(date(2026, m, 15), -999, FIRST_PRINT) for m in range(1, 10)]

        found = find_recurring(payments + refunds, [], TODAY, entities=BOTH)

        assert sorted((s.direction, s.count) for s in found) == [("in", 9), ("out", 9)]

    def test_Detector_WhenTwoPrintsPayTwiceAMonth_IsNotMadeIntoASeries(self):
        twice = [tx(date(2026, m, 15), -999, FIRST_PRINT) for m in range(1, 10)]
        twice += [tx(date(2026, m, 15), -999, SECOND_PRINT) for m in range(1, 10)]

        assert len(find_recurring(twice, [], TODAY)) == 2
        assert find_recurring(twice, [], TODAY, entities=BOTH) == []

    def test_Detector_WhenAShapeIsNamedInDifferentCase_StillJoinsOneSeries(self):
        names = described({FIRST: "fernhollow plus", SECOND: "Fernhollow Plus"})

        (series,) = find_recurring(renamed_subscription(), [], TODAY, entities=names)

        assert series.count == 9


@pytest.fixture
def world(tmp_path, monkeypatch):
    csv = tmp_path / "export.csv"
    lines = ["Date,Counter Party,Reference,Type,Amount (GBP),Balance (GBP)"]
    for month in (1, 2, 3, 4):
        lines.append(f"15/{month:02d}/2026,{FIRST_PRINT},,CARD,-9.99,0")
    for month in (5, 6, 7, 8, 9):
        lines.append(f"15/{month:02d}/2026,{SECOND_PRINT},,CARD,-9.99,0")
    csv.write_text("\n".join(lines) + "\n", encoding="utf-8")
    db = tmp_path / "store.sqlite3"
    with Store(db) as store:
        import_file(store, csv, account_id="current-main")
    environment(monkeypatch, tmp_path)
    config = build_web_config(db)
    assert config is not None
    base, stop = serve_config(config)
    yield base
    stop()


def rows_on_recurring_page(base: str) -> list[str]:
    root = parse(httpx.post(f"{base}/recurring", timeout=60).text)
    return [li.text() for li in elements(root, "li") if "recur-row" in li.classes]


class TestFromTheEntitiesPageToTheRecurringPage:
    def test_RecurringPage_WhenTheOwnerMergesTheTwoNames_ShowsOneRowNamedByTheEntity(self, world):
        assert len(rows_on_recurring_page(world)) == 2

        merged = httpx.post(
            f"{world}/entities-merge", data={"name": ENTITY, "shape": [FIRST, SECOND]}, timeout=60
        )
        assert merged.status_code == 200

        (row,) = rows_on_recurring_page(world)
        assert row.startswith(ENTITY)
        assert "9 times" in row

    def test_RecurringPage_WhenOneNameBecomesAChildEntity_ShowsItAsItsOwnSeriesUnderItsOwnName(
        self, world
    ):
        httpx.post(
            f"{world}/entities-merge", data={"name": ENTITY, "shape": [FIRST, SECOND]}, timeout=60
        )
        (parent,) = [
            e for e in elements(parse(httpx.post(f"{world}/entities", timeout=60).text), "input")
            if e.attrs.get("name") == "entity"
        ][:1]

        made = httpx.post(
            f"{world}/entities-child",
            data={"entity": parent.attrs["value"], "shape": SECOND, "name": "Fernhollow Premium"},
            timeout=60,
        )

        assert made.status_code == 200
        rows = sorted(rows_on_recurring_page(world))
        assert len(rows) == 2
        assert rows[0].startswith(ENTITY) and "4 times" in rows[0]
        assert rows[1].startswith("Fernhollow Premium") and "5 times" in rows[1]
        listing = httpx.post(f"{world}/entities", timeout=60).text
        sections = [s for s in elements(parse(listing), "section") if "ent-entity" in s.classes]
        parents = [s for s in sections if "ent-child" not in s.classes]
        (family,) = parents
        assert [h.text() for h in elements(family, "h4")] == ["Fernhollow Premium"]

    def test_RecurringPage_WhenTheChildsOnlyNameIsSplitApart_TheChildIsGoneAndTheNameFree(
        self, world
    ):
        httpx.post(
            f"{world}/entities-merge", data={"name": ENTITY, "shape": [FIRST, SECOND]}, timeout=60
        )
        parent = next(
            e for e in elements(parse(httpx.post(f"{world}/entities", timeout=60).text), "input")
            if e.attrs.get("name") == "entity"
        ).attrs["value"]
        httpx.post(
            f"{world}/entities-child",
            data={"entity": parent, "shape": SECOND, "name": "Fernhollow Premium"},
            timeout=60,
        )

        httpx.post(f"{world}/entities-split", data={"shape": SECOND}, timeout=60)

        listing = httpx.post(f"{world}/entities", timeout=60).text
        assert "Fernhollow Premium" not in listing
        again = httpx.post(
            f"{world}/entities-merge",
            data={"name": "Fernhollow Premium", "shape": [SECOND]},
            timeout=60,
        )
        assert again.status_code == 200

    def test_RecurringPage_WhenTheOwnerSplitsOneNameApart_ShowsTwoRowsAgain(self, world):
        httpx.post(
            f"{world}/entities-merge", data={"name": ENTITY, "shape": [FIRST, SECOND]}, timeout=60
        )

        httpx.post(f"{world}/entities-split", data={"shape": FIRST}, timeout=60)

        assert len(rows_on_recurring_page(world)) == 2
