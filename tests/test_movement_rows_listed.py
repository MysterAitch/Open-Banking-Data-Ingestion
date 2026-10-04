"""Every row a source lists is held once, whether or not any balance moves.

The owner's scenario: "100 in / 100 out / 100 in / 100 out. This scenario could
collapse into just one out and one in and the daily totals would stay the same."
A balance cannot see two identical rows held as one, so the rows each artefact
lists are set against the rows the store holds from it, per account, source,
day, direction, and size.

The household is the round-up corpus (a main account, one Space, a Space-blind
export, an aggregator) with two identical Coffee payments on day 3, so a twin is
in every source. Every scenario lands the sources in EVERY order and rebuilds.

KNOWN ANSWERS, decided before the first run:

    (i)   the household as landed
          no row is listed and not held, or held and not listed
    (ii)  the same household, then one of the export's two Coffee rows loses its sighting
          one fault: starling-csv, day 3, out, 2 listed 1 held (collapsed)
    (iii) the export's single Taxi row loses its sighting
          one fault: starling-csv, day 8, out, 1 listed 0 held (missing)
    (iv)  one of the export's Coffee rows is sighted as two rows
          one fault: starling-csv, day 3, out, 2 listed 3 held (surplus)
    (v)   the export's Taxi row is sighted on day 9
          one fault: starling-csv, day 8, out, dated the next day
    (vi)  the same export file landed again, under a second name
          no fault: one digest is one listing
    (vii) two different export files overlapping on days 3 to 5, each listing the
          overlap's rows
          no fault, and the rows listed are the union of the two files
    (viii) the export's Taxi row is held as a void row
          no fault, said to be held as history
"""

from __future__ import annotations

import itertools
import sqlite3
from datetime import date, datetime

import pytest

from card_chain_corpus import CARD, CLOSINGS, build_card
from obdi.ingest import artefact_digest, import_file
from obdi.models import RawArtefact
from obdi.movement_completeness import (
    COLLAPSED,
    DATED_LATER,
    MISSING,
    SURPLUS,
    check_rows,
    movement_completeness,
)
from obdi.store import Store
from round_up_corpus import card_payment, main_feed, space_feed
from test_export_cuts import Row, export_lines
from test_space_attribution import MAIN, MAP
from test_space_blind_rows_and_internal_legs import (
    BASE_EXPORT,
    ORDERS,
    aggregator_record,
    corpus,
    order_id,
)

EXPORT = "starling-csv"
STATEMENTS = len(CLOSINGS)


def canonical(ref: str) -> str:
    return str(MAP.resolve(*ref.split(":", 1))) if ":" in ref else ref


def twin_coffees() -> dict:
    return {
        "main": [
            *main_feed(),
            card_payment("f-coffee-2", "Coffee", 350, 3),
        ],
        "space": space_feed(),
        "export_rows": [*BASE_EXPORT, Row("Coffee", -350, 3, 3)],
        "aggregator": [
            aggregator_record("tl-coffee-1", "-3.50", 3, "COFFEE"),
            aggregator_record("tl-coffee-2", "-3.50", 3, "COFFEE"),
        ],
    }


@pytest.fixture
def stores(tmp_path):
    opened: list[Store] = []

    def build(order, **kwargs) -> Store:
        directory = tmp_path / f"{len(opened)}"
        directory.mkdir()
        store = corpus(directory, order, **(kwargs or twin_coffees()))
        opened.append(store)
        return store

    yield build
    for store in opened:
        store.close()


def faults_of(store: Store):
    return check_rows(store, canonical).row_faults


def export_sightings(store: Store, minor: int, day: int) -> list[sqlite3.Row]:
    return store.connection.execute(
        "SELECT s.entity_id AS entity_id, s.artefact_digest AS digest "
        "FROM transaction_sources s JOIN transactions t ON t.entity_id = s.entity_id "
        "WHERE s.source = ? AND t.amount_minor = ? AND t.value_date = ? "
        "ORDER BY t.occurrence, s.entity_id",
        (EXPORT, minor, f"2026-09-{day:02}"),
    ).fetchall()


def drop_sighting(store: Store, entity_id: str) -> None:
    store.connection.execute(
        "DELETE FROM transaction_sources WHERE entity_id = ? AND source = ?",
        (entity_id, EXPORT),
    )
    store.connection.commit()


ORDER_CASES = [pytest.param(order, id=order_id(order)) for order in ORDERS]


class TestARowListedIsHeldOnce:
    @pytest.mark.parametrize("order", ORDER_CASES)
    def test_RowsListed_WhenEverySourceListsWhatTheStoreHolds_NoFaultInAnyArrivalOrder(
        self, stores, order
    ):
        store = stores(order, **{**twin_coffees(), "export_rows": BASE_EXPORT})

        report = check_rows(store, canonical)

        assert report.row_faults == []
        assert report.rows_listed > 0
        assert report.artefacts_unread == 0

    @pytest.mark.parametrize("order", ORDER_CASES)
    def test_RowsListed_WhenTwoIdenticalPaymentsAreListedByEverySource_NoFaultInAnyArrivalOrder(
        self, stores, order
    ):
        store = stores(order)

        assert faults_of(store) == []
        assert len(export_sightings(store, -350, 3)) == 2

    @pytest.mark.parametrize("order", ORDER_CASES)
    def test_RowsListed_WhenTwoIdenticalRowsAreHeldAsOne_NamesTheDayAccountAndSource(
        self, stores, order
    ):
        store = stores(order)
        first, _second = export_sightings(store, -350, 3)
        drop_sighting(store, first["entity_id"])

        (fault,) = faults_of(store)

        assert (fault.account, fault.source, fault.day, fault.direction) == (
            MAIN,
            EXPORT,
            date(2026, 9, 3),
            "out",
        )
        assert (fault.kind, fault.listed, fault.held) == (COLLAPSED, 2, 1)
        assert "2 rows of one size and direction listed, 1 held" in fault.says()

    @pytest.mark.parametrize("order", ORDER_CASES)
    def test_RowsListed_WhenARowsSightingIsAbsent_ReportsListedOneHeldNone(self, stores, order):
        store = stores(order)
        (only,) = export_sightings(store, -1275, 8)
        drop_sighting(store, only["entity_id"])

        (fault,) = faults_of(store)

        assert (fault.source, fault.day, fault.direction) == (EXPORT, date(2026, 9, 8), "out")
        assert (fault.kind, fault.listed, fault.held) == (MISSING, 1, 0)

    @pytest.mark.parametrize("order", ORDER_CASES)
    def test_RowsListed_WhenOneListedRowIsSightedAsTwoRows_ReportsHeldMoreThanListed(
        self, stores, order
    ):
        store = stores(order)
        first, _second = export_sightings(store, -350, 3)
        store.connection.execute(
            "INSERT INTO transactions SELECT 'extra-twin', account_id, amount_minor, currency, "
            "value_date, booking_date, description, counterparty, status, source, tier, "
            "NULL, content_key, 9, artefact_digest, is_internal_transfer, match_tier, "
            "matched_entity_id, raw, first_seen_at, last_seen_at "
            "FROM transactions WHERE entity_id = ?",
            (first["entity_id"],),
        )
        store.connection.execute(
            "INSERT INTO transaction_sources (entity_id, source, source_id, artefact_digest, "
            "observed_date, first_seen_at) SELECT 'extra-twin', source, source_id, "
            "artefact_digest, observed_date, first_seen_at FROM transaction_sources "
            "WHERE entity_id = ? AND source = ?",
            (first["entity_id"], EXPORT),
        )
        store.connection.commit()

        (fault,) = faults_of(store)

        assert (fault.kind, fault.listed, fault.held) == (SURPLUS, 2, 3)
        assert fault.day == date(2026, 9, 3)

    @pytest.mark.parametrize("order", ORDER_CASES)
    def test_RowsListed_WhenASightingIsDatedTheNextDay_SaysSoRatherThanMissingAndSurplus(
        self, stores, order
    ):
        store = stores(order)
        (only,) = export_sightings(store, -1275, 8)
        store.connection.execute(
            "UPDATE transaction_sources SET observed_date = '2026-09-09' "
            "WHERE entity_id = ? AND source = ?",
            (only["entity_id"], EXPORT),
        )
        store.connection.commit()

        (fault,) = faults_of(store)

        assert (fault.kind, fault.day, fault.listed) == (DATED_LATER, date(2026, 9, 8), 1)
        assert "held dated the next day" in fault.says()

    @pytest.mark.parametrize("order", ORDER_CASES)
    def test_RowsListed_WhenAListedRowIsHeldAsHistory_IsHeldAndSaidToBeSo(self, stores, order):
        store = stores(order)
        (only,) = export_sightings(store, -1275, 8)
        store.connection.execute(
            "UPDATE transactions SET status = 'void' WHERE entity_id = ?", (only["entity_id"],)
        )
        store.connection.commit()

        report = check_rows(store, canonical)

        assert report.row_faults == []
        assert report.held_as_history >= 1
        assert "held as history" in report.describe()


class TestSeveralArtefactsOfOneSource:
    def export_file(self, directory, name, rows):
        path = directory / name
        path.write_text("\n".join(export_lines(rows)) + "\n", encoding="utf-8")
        return path

    def test_RowsListed_WhenTheSameFileIsLandedAgainUnderASecondName_IsOneListing(self, tmp_path):
        rows = [Row("Deposit", 100000, 1, 1), Row("Coffee", -350, 3, 3), Row("Coffee", -350, 3, 3)]
        with Store(tmp_path / "s.sqlite3") as store:
            first = self.export_file(tmp_path, "a.csv", rows)
            second = self.export_file(tmp_path, "copy-of-a.csv", rows)
            import_file(store, first, account_id=MAIN, account_map=MAP)
            import_file(store, second, account_id=MAIN, account_map=MAP)

            report = check_rows(store, canonical)

        assert report.row_faults == []
        assert report.rows_listed == 3
        assert report.rows_held == 3

    def test_RowsListed_WhenTwoDifferentExportsOverlap_ListsTheUnionOfWhatTheySay(self, tmp_path):
        early = [
            Row("Deposit", 100000, 1, 1),
            Row("Coffee", -350, 3, 3),
            Row("Coffee", -350, 3, 3),
            Row("Grocer", -2310, 5, 5),
        ]
        late = [
            Row("Coffee", -350, 3, 3),
            Row("Coffee", -350, 3, 3),
            Row("Grocer", -2310, 5, 5),
            Row("Taxi", -1275, 8, 8),
        ]
        with Store(tmp_path / "s.sqlite3") as store:
            for name, rows in (("a.csv", early), ("b.csv", late)):
                path = self.export_file(tmp_path, name, rows)
                import_file(store, path, account_id=MAIN, account_map=MAP)

            report = check_rows(store, canonical)

        # Deposit, two Coffees, Grocer, Taxi: the overlap is listed by both files, once each.
        assert report.rows_listed == 5
        assert report.rows_held == 5
        assert report.row_faults == []

    def test_RowsListed_WhenTwoOverlappingExportsDisagreeAboutADay_ReportsTheLargerListing(
        self, tmp_path
    ):
        """One file lists two Coffees, the other one; the store holds one, so the
        larger listing is the one the store falls short of."""
        with Store(tmp_path / "s.sqlite3") as store:
            twin = [Row("Coffee", -350, 3, 3), Row("Coffee", -350, 3, 3), Row("Taxi", -1275, 8, 8)]
            single = [Row("Coffee", -350, 3, 3), Row("Taxi", -1275, 8, 8)]
            path = self.export_file(tmp_path, "single.csv", single)
            import_file(store, path, account_id=MAIN, account_map=MAP)
            # The second file is landed and not imported, as when a rebuild has not run.
            payload = self.export_file(tmp_path, "twin.csv", twin).read_bytes()
            store.land_artefact(
                RawArtefact(
                    source="csv",
                    account_ref=MAIN,
                    fetched_at=datetime.now().astimezone(),
                    media_type="text/csv",
                    digest=artefact_digest(payload),
                    payload=payload,
                    origin="twin.csv",
                )
            )

            (fault,) = check_rows(store, canonical).row_faults

        assert (fault.kind, fault.day, fault.listed, fault.held) == (
            COLLAPSED,
            date(2026, 9, 3),
            2,
            1,
        )


class TestRowsAStatementLists:
    """A statement has no ids at all, so its rows are listed from the kept reading.

    KNOWN ANSWERS (the invented card holds `STATEMENTS` statements):
      held as imported                      no fault, no artefact unread
      one statement row loses its sighting  one fault naming the card and the statement's source
      the kept readings forgotten           every statement unread, said so, no fault
    """

    def test_RowsListed_WhenEveryStatementRowIsHeld_NoFaultAndNoDocumentIsReadAgain(
        self, tmp_path, monkeypatch
    ):
        import obdi.statement_shape as statement_shape

        with Store(tmp_path / "card.sqlite3") as store:
            build_card(store, tmp_path)
            extractions: list[object] = []
            real = statement_shape.pdf_lines
            monkeypatch.setattr(
                statement_shape, "pdf_lines", lambda path: extractions.append(path) or real(path)
            )

            report = check_rows(store, canonical)

        assert report.row_faults == []
        assert report.artefacts_listed >= STATEMENTS
        assert report.artefacts_unread == 0
        assert extractions == []

    def test_RowsListed_WhenAStatementRowHasNoSighting_NamesTheCardAndTheStatementsSource(
        self, tmp_path
    ):
        with Store(tmp_path / "card.sqlite3") as store:
            build_card(store, tmp_path)
            source, entity = store.connection.execute(
                "SELECT s.source, s.entity_id FROM transaction_sources s "
                "JOIN raw_artefacts a ON a.digest = s.artefact_digest "
                "WHERE a.media_type = 'application/pdf' ORDER BY s.entity_id LIMIT 1"
            ).fetchone()
            store.connection.execute(
                "DELETE FROM transaction_sources WHERE entity_id = ? AND source = ?",
                (entity, source),
            )
            store.connection.commit()

            (fault,) = check_rows(store, canonical).row_faults

        assert (fault.account, fault.source, fault.kind) == (CARD, source, MISSING)

    def test_RowsListed_WhenNoReadingIsKept_SaysTheStatementsAreUnreadInsteadOfPassing(
        self, tmp_path
    ):
        with Store(tmp_path / "card.sqlite3") as store:
            build_card(store, tmp_path)
            store.clear_statement_readings()
            store.connection.commit()

            report = check_rows(store, canonical)

        assert report.artefacts_unread == STATEMENTS
        assert "not compared - this is not a pass" in report.describe()


class TestTheCombinedReport:
    def test_Report_WhenCleanOrFaulty_NamesNoFigureDescriptionOrPayee(self, stores):
        store = stores(ORDERS[0])
        first, _second = export_sightings(store, -350, 3)
        drop_sighting(store, first["entity_id"])

        text = movement_completeness(store, canonical).describe()

        assert "2 rows of one size and direction listed, 1 held" in text
        for hidden in ("350", "3.50", "Coffee", "COFFEE", "1275"):
            assert hidden not in text

    def test_Report_WhenMoreThanTwentyFaults_NamesTwentyAndCountsTheRest(self, tmp_path):
        rows = [Row(f"Item{n}", -100 - n, 1 + n % 25, 1 + n % 25) for n in range(30)]
        with Store(tmp_path / "s.sqlite3") as store:
            path = tmp_path / "many.csv"
            path.write_text("\n".join(export_lines(rows)) + "\n", encoding="utf-8")
            import_file(store, path, account_id=MAIN, account_map=MAP)
            store.connection.execute("DELETE FROM transaction_sources")
            store.connection.commit()

            text = check_rows(store, canonical).describe()

        assert "... and 10 more" in text


def test_Orders_AreEverySourceArrivalOrder():
    assert len(ORDERS) == len(list(itertools.permutations(("feed", "export", "aggregator"))))
