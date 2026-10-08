"""A held statement is read once; every later pass over the store reads the answer.

The same-money pass, the statement-periods page, and the anchor checks all need
what each held statement says (its dates, balances, and rows). A statement is a
raw artefact and never changes, so what it says never changes either, yet every
pass used to extract every document's text again: the in-process memo helped
only the closing balances, a scheduled pull is a fresh process each cycle, and
the extraction cache keeps two documents. Measured on the invented card: one
extraction per held statement on every pass, however many passes ran.

The tests count text extractions (`pdf_lines`, the expensive step of reading a
document) from a simulated fresh process, where no in-process memo helps.
"""

from __future__ import annotations

import sqlite3
import time
from datetime import date
from pathlib import Path

import pytest

import obdi.ingest.statement_shape as statement_shape
import obdi.ingest.statement_terms as statement_terms
from card_chain_corpus import CLOSINGS, build_card
from landing import rebuild_from_raw
from obdi.ingest.accounts import AccountRecord, AccountRef
from obdi.ingest.parsers import pdf_statements
from obdi.ingest.parsers.statement_reading import (
    RateWindow,
    StatementReading,
    StatementRow,
    reading_from_json,
    reading_to_json,
)
from obdi.ingest.same_money_fold import fold_same_money
from obdi.ingest.store import SCHEMA_VERSION, Store
from obdi.verify.period_reconciliation import gather_evidence, period_reconciliation

STATEMENTS = len(CLOSINGS)


@pytest.fixture
def store(tmp_path: Path):
    with Store(tmp_path / "cache.sqlite3") as opened:
        yield opened


class Extractions:
    """Counts text extractions and can make each one slow."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.count = 0
        self.delay = 0.0
        real = statement_shape.pdf_lines

        def counting(path: Path):
            self.count += 1
            time.sleep(self.delay)
            return real(path)

        monkeypatch.setattr(statement_shape, "pdf_lines", counting)

    def fresh_process(self) -> None:
        """Forget every in-process memo, as a new `obdi pull` starts without them."""
        statement_terms._USABLE_BY_DIGEST.clear()
        pdf_statements._lines.cache_clear()
        pdf_statements._grid_and_pages.cache_clear()
        pdf_statements._table.cache_clear()
        self.count = 0


@pytest.fixture
def extractions(monkeypatch: pytest.MonkeyPatch) -> Extractions:
    return Extractions(monkeypatch)


def stored_readings(store: Store) -> int:
    return int(store.connection.execute("SELECT COUNT(*) FROM statement_readings").fetchone()[0])


def forget_readings(store: Store) -> None:
    """Back to a store that held statements before anything was kept of them: no reading, and
    no extraction either, so the documents themselves are the only input left."""
    store.clear_statement_readings()
    store.clear_statement_extractions()
    store.connection.commit()


def kept_readings(store: Store) -> dict[str, str]:
    return {
        str(row["digest"]): str(row["reading"])
        for row in store.connection.execute("SELECT digest, reading FROM statement_readings")
    }


class TestAStatementHeldAndReadOnce:
    def test_FoldPass_InAFreshProcessAfterEveryStatementWasImported_ExtractsNoDocument(
        self, store, tmp_path, extractions
    ):
        build_card(store, tmp_path)
        extractions.fresh_process()

        fold_same_money(store)

        assert extractions.count == 0

    def test_PeriodsPage_InAFreshProcessAfterEveryStatementWasImported_ExtractsNoDocument(
        self, store, tmp_path, extractions
    ):
        """The page shares the fold's evidence, so it shares the saving."""
        build_card(store, tmp_path)
        extractions.fresh_process()

        period_reconciliation(store, sibling_accounts={})

        assert extractions.count == 0

    def test_GatherEvidence_CalledRepeatedly_NeverExtractsADocumentAfterTheFirstTime(
        self, store, tmp_path, extractions
    ):
        build_card(store, tmp_path)
        extractions.fresh_process()

        for _ in range(3):
            gather_evidence(store, sibling_accounts={})

        assert extractions.count == 0

    def test_Import_OfEachStatement_StoresOneReadingPerHeldDocument(self, store, tmp_path):
        build_card(store, tmp_path)

        assert stored_readings(store) == STATEMENTS

    def test_StoredReading_ReadBack_GivesTheSameFiguresAsReadingTheDocument(
        self, store, tmp_path, extractions
    ):
        """What the pass reads must be what the parser said: compared by the
        closing balances and the first-row dates the pass actually uses."""
        build_card(store, tmp_path)
        extractions.fresh_process()
        from_store = sorted(
            (account, reading.statement_date, reading.closing_balance_minor,
             reading.opening_balance_minor, len(reading.transactions))
            for account, reading in statement_terms.held_statement_readings(store)
        )
        assert extractions.count == 0
        forget_readings(store)
        extractions.fresh_process()

        from_documents = sorted(
            (account, reading.statement_date, reading.closing_balance_minor,
             reading.opening_balance_minor, len(reading.transactions))
            for account, reading in statement_terms.held_statement_readings(store)
        )

        assert extractions.count == STATEMENTS
        assert from_store == from_documents


class TestAStoreThatKeptExtractionsButNoReadings:
    def test_FoldPass_WhenOnlyTheReadingsAreForgotten_ParsesTheStoredExtractionsAndOpensNoDocument(
        self, store, tmp_path, extractions
    ):
        """The extraction is what reading a PDF costs; the parse over it is not, so a reading
        lost (a new parser, a store before readings) is made again without opening a file."""
        build_card(store, tmp_path)
        store.clear_statement_readings()
        store.connection.commit()
        extractions.fresh_process()

        fold_same_money(store)

        assert extractions.count == 0
        assert stored_readings(store) == STATEMENTS


class TestAStoreThatHeldStatementsBeforeReadingsWereKept:
    def test_FoldPass_WhenNoReadingIsStored_ReadsEachDocumentOnceAndNeverAgain(
        self, store, tmp_path, extractions
    ):
        build_card(store, tmp_path)
        forget_readings(store)
        extractions.fresh_process()

        fold_same_money(store)
        first_pass = extractions.count
        extractions.fresh_process()
        fold_same_money(store)

        assert first_pass == STATEMENTS
        assert extractions.count == 0
        assert stored_readings(store) == STATEMENTS

    def test_PeriodsPage_WhenNoReadingIsStored_StillAnswersAndStoresNothing(
        self, store, tmp_path, extractions
    ):
        """A page view is a reader: it falls back to the documents and leaves the
        store alone, so a view never waits on the pull's write lock."""
        build_card(store, tmp_path)
        forget_readings(store)
        extractions.fresh_process()

        report = period_reconciliation(store, sibling_accounts={})

        assert [len(item.periods) > 0 for item in report.accounts] == [True]
        assert stored_readings(store) == 0

    def test_StoreOpenedAtTheOldVersion_GrowsTheReadingsTableAndKeepsItsRows(self, tmp_path):
        path = tmp_path / "old.sqlite3"
        with Store(path) as opened:
            opened.declare_account(AccountRecord(ref=AccountRef("witness"), label="Witness"))
        connection = sqlite3.connect(path)
        connection.execute("DROP TABLE statement_readings")
        connection.execute(
            "UPDATE obdi_meta SET value = ? WHERE key = 'schema_version'",
            (str(SCHEMA_VERSION - 1),),
        )
        connection.commit()
        connection.close()

        with Store(path) as reopened:
            assert stored_readings(reopened) == 0
            kept = [account.label for account in reopened.declared_accounts()]
        assert kept == ["Witness"]


class TestAStoredReadingThatCannotBeTrusted:
    def test_FoldPass_WhenAStoredReadingIsCorrupt_SaysSoReadsTheDocumentAndRepairsIt(
        self, store, tmp_path, extractions, capsys
    ):
        build_card(store, tmp_path)
        damaged = sorted(kept_readings(store))[0]
        store.keep_statement_reading(damaged, "santander", "not json")
        store.connection.commit()
        extractions.fresh_process()
        capsys.readouterr()

        report = fold_same_money(store)

        assert report.folded == 8
        # Read again from the stored extraction: the document's bytes are not opened for it.
        assert extractions.count == 0
        assert "stored reading" in capsys.readouterr().err
        extractions.fresh_process()
        fold_same_money(store)
        assert extractions.count == 0

    def test_Rebuild_AfterAStoredReadingWentStale_RereadsEveryDocument(
        self, store, tmp_path, extractions
    ):
        """A new parser takes effect at a rebuild, which re-derives everything
        from the raw documents: a reading kept from the old parser must not
        survive it."""
        build_card(store, tmp_path)
        honest = kept_readings(store)
        for digest in honest:
            store.keep_statement_reading(digest, "santander", reading_to_json(StatementReading()))
        store.connection.commit()
        extractions.fresh_process()

        rebuild_from_raw(store)

        assert kept_readings(store) == honest


class TestTheRepeatedExtractionCostsWhatTheCacheSaves:
    def test_PeriodsPage_WhenExtractionIsSlow_SecondViewCostsNoExtractionTime(
        self, store, tmp_path, extractions
    ):
        build_card(store, tmp_path)
        forget_readings(store)
        extractions.fresh_process()
        extractions.delay = 0.05
        fold_same_money(store)

        extractions.fresh_process()
        started = time.perf_counter()
        period_reconciliation(store, sibling_accounts={})
        warm = time.perf_counter() - started

        assert extractions.count == 0
        assert warm < STATEMENTS * extractions.delay / 2


class TestTheStepsOfThePassAreTimed:
    def test_Rebuild_WithTimingsOn_RecordsEachStepOfTheSameMoneyPassAsASubPhase(
        self, store, tmp_path
    ):
        from obdi.core import instrumentation

        build_card(store, tmp_path)
        instrumentation.configure(True)
        try:
            report = rebuild_from_raw(store)
        finally:
            instrumentation.configure(None)

        steps = {name for name in report.timings if name.startswith("same-money-fold/")}
        assert steps == {
            f"same-money-fold/{step}"
            for step in (
                "reading-statements",
                "reading-sightings",
                "membership",
                "pairing",
                "search",
                "writing",
            )
        }
        assert "same-money-fold" in report.timings


class TestTheReadingRoundTrip:
    def test_ReadingToJson_WithEveryFieldFilled_RoundTripsToAnEqualReading(self):
        reading = StatementReading(
            statement_date=date(2026, 1, 12),
            opening_balance_minor=-10000,
            closing_balance_minor=-12345,
            credit_limit_minor=300000,
            account_name="Invented Card",
            transactions=[StatementRow(date(2026, 1, 3), "Invented Shop", -2345)],
            end_of_day_minor=[(date(2026, 1, 3), -12345)],
            rates={"purchase": 21.9},
            rate_windows=[RateWindow(0.0, date(2026, 6, 1))],
            notes=["a note"],
        )

        assert reading_from_json(reading_to_json(reading)) == reading

    def test_ReadingToJson_WithNothingFilled_RoundTripsToAnEqualReading(self):
        assert reading_from_json(reading_to_json(StatementReading())) == StatementReading()

    @pytest.mark.parametrize("damaged", ["", "[]", '{"statement_date": "yesterday"}', "{}x"])
    def test_ReadingFromJson_WhenTheTextIsDamaged_RefusesRatherThanGuessing(self, damaged):
        with pytest.raises((ValueError, KeyError, TypeError)):
            reading_from_json(damaged)
