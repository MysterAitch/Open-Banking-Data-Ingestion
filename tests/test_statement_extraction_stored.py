"""A PDF is read once: what it yields is kept by digest and extractor version.

The owner, 2026-10-06: a statement's text, the names found in it, its sections, and its masked
shape "only need to be created once from pdf reading and this result can be stored. It only
changes if the extractor changes." Until then every restart read every kept PDF again on the first
view of the Statements page, because the readings were held in memory only.

Every scene counts the reads of a document's bytes from a simulated fresh process, where no
in-process memo helps: a text read is a call of `pdf_lines`, a geometry read a call of
`words_from`. The answers are decided before the first run:

  * keeping a statement reads it once, and keeping the same bytes again reads nothing;
  * a rebuild reads nothing for a document whose stored extraction is current, exactly one read
    for each document whose extraction was made by another version, and refills a dropped table
    from the raw artefacts alone;
  * a PDF that cannot be opened is stored as failed with the reason, and is not tried again;
  * the Kept statements listing, the Statements page, Bring in, and a kept statement's masked
    shape read nothing in a fresh process once the rows exist, and say the document is not yet
    extracted, again without reading it, where no row at the current version exists.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from datetime import date
from pathlib import Path

import pytest

import obdi.ingest.statement_columns as statement_columns
import obdi.ingest.statement_extraction as statement_extraction
import obdi.ingest.statement_shape as statement_shape
import obdi.ingest.statement_terms as statement_terms
from obdi.cli import build_web_config
from obdi.ingest.parsers import pdf_statements
from obdi.ingest.pipeline import import_file
from obdi.ingest.rebuild import rebuild_from_raw
from obdi.ingest.store import SCHEMA_VERSION, ExtractionRecord, Store, StoreIsNewer
from served_store import environment_for
from test_bring_in_assign import (
    PLANTED_PAYEE,
    POT_KEY,
    SAVER_KEY,
    credit_union,
    letter,
    santander,
)

D = date
NOT_A_PDF = b"%PDF-1.4\nthis is not a document any reader can open\n"


class Reads:
    """Counts the reads of a document's bytes: text, geometry, and whole extractions."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.text = 0
        self.geometry = 0
        self.attempts = 0
        real_text = statement_shape.pdf_lines
        real_geometry = statement_columns.words_from
        real_read = statement_extraction.read_raw

        def counting_text(path: Path):  # type: ignore[no-untyped-def]
            self.text += 1
            return real_text(path)

        def counting_geometry(path: Path):  # type: ignore[no-untyped-def]
            self.geometry += 1
            return real_geometry(path)

        def counting_read(payload: bytes):  # type: ignore[no-untyped-def]
            self.attempts += 1
            return real_read(payload)

        monkeypatch.setattr(statement_shape, "pdf_lines", counting_text)
        monkeypatch.setattr(statement_columns, "words_from", counting_geometry)
        monkeypatch.setattr(statement_extraction, "read_raw", counting_read)

    @property
    def total(self) -> int:
        return self.text + self.geometry

    def fresh_process(self) -> None:
        """Forget every in-process memo, as a new process starts without them."""
        statement_terms._USABLE_BY_DIGEST.clear()
        pdf_statements._lines.cache_clear()
        pdf_statements._grid_and_pages.cache_clear()
        pdf_statements._table.cache_clear()
        self.text = self.geometry = self.attempts = 0


@pytest.fixture
def reads(monkeypatch: pytest.MonkeyPatch) -> Reads:
    return Reads(monkeypatch)


@pytest.fixture
def root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    for name, value in environment_for(tmp_path).items():
        monkeypatch.setenv(name, value)
    for variable in ("TRUELAYER_CLIENT_ID", "TRUELAYER_CLIENT_SECRET_FILE"):
        monkeypatch.delenv(variable, raising=False)
    return tmp_path


@pytest.fixture
def store(root: Path) -> Iterator[Store]:
    with Store(root / "store.sqlite3") as opened:
        yield opened


def keep(root: Path, payload: bytes, name: str) -> int:
    """Keep a statement through the door the Statement shape page and Bring in use."""
    wired = build_web_config(root / "store.sqlite3")
    assert wired is not None and wired.keep_statement is not None
    return wired.keep_statement(payload, name)[0]


def rows_of(store: Store) -> dict[str, tuple[object, ...]]:
    return {
        str(row["digest"]): tuple(row)
        for row in store.connection.execute("SELECT * FROM statement_extractions")
    }


def only_row(store: Store) -> sqlite3.Row:
    found = store.connection.execute("SELECT * FROM statement_extractions").fetchall()
    assert len(found) == 1
    return found[0]


class TestAStatementIsExtractedWhenItIsKept:
    def test_Statement_WhenKept_StoresItsTextNamesAndMaskedShapeAtTheCurrentVersion(
        self, root, store, reads
    ):
        keep(root, santander(D(2026, 8, 10), 1211), "card.pdf")

        row = only_row(store)
        assert row["extractor_version"] == pdf_statements.EXTRACTOR_VERSION
        assert row["failure"] == ""
        assert PLANTED_PAYEE in row["lines"], "the row holds the document's real text"
        assert statement_extraction.stored(store, str(row["digest"])).names[0][0] == "Santander"
        assert PLANTED_PAYEE not in row["masked_shape"]
        assert "lines across 1 page" in row["masked_shape"]

    def test_DividedStatement_WhenKept_StoresEachSectionWithItsKeyLabelAndReading(
        self, root, store, reads
    ):
        keep(root, credit_union(7), "all-accounts.pdf")

        found = statement_extraction.stored(store, str(only_row(store)["digest"]))
        assert found is not None and found.sections is not None
        assert [item.key for item in found.sections] == [SAVER_KEY, POT_KEY]
        assert [item.label for item in found.sections] == ["Regular Saver", "Holiday Pot"]
        assert [len(item.reading.transactions) for item in found.sections] == [1, 1]
        assert [item.refusal for item in found.sections] == ["", ""]

    def test_Statement_WhenKeptOnceMoreWithTheSameBytes_IsNotReadAgain(
        self, root, store, reads
    ):
        payload = santander(D(2026, 8, 10), 1211)
        keep(root, payload, "card.pdf")
        reads.fresh_process()

        keep(root, payload, "card-copy.pdf")

        assert reads.total == 0

    def test_Statement_WhenKeptWithoutAnyReaderForIt_StillStoresItsNamesAndShape(
        self, root, store, reads
    ):
        keep(root, letter("a topic no bank wrote"), "letter.pdf")

        found = statement_extraction.stored(store, str(only_row(store)["digest"]))
        assert found is not None
        assert found.sections is None and found.sections_error == ""
        assert "lines across 1 page" in found.masked_shape


class TestAPdfImportedToAnAccount:
    def test_Pdf_WhenImportedToAnAccount_IsReadOnceForTheParseAndKept(
        self, root, store, reads, tmp_path
    ):
        file = tmp_path / "card.pdf"
        file.write_bytes(santander(D(2026, 8, 10), 1211))

        summary = import_file(store, file, account_id="santander-cc")

        assert summary.artefact_new
        assert reads.text == 1, "the parse and the keeping share one read of the document"
        assert only_row(store)["failure"] == ""

    def test_Pdf_WhenImportedAgainAtTheSameVersion_IsNotReadAgain(
        self, root, store, reads, tmp_path
    ):
        file = tmp_path / "card.pdf"
        file.write_bytes(santander(D(2026, 8, 10), 1211))
        import_file(store, file, account_id="santander-cc")
        reads.fresh_process()

        import_file(store, file, account_id="santander-cc")

        assert reads.total == 0


class TestAnUnreadablePdf:
    def test_NotAPdf_WhenKept_IsStoredAsFailedWithTheReason(self, root, store, reads):
        keep(root, NOT_A_PDF, "broken.pdf")

        row = only_row(store)
        assert row["failure"].startswith("could not be opened as a PDF")
        assert row["lines"] == "[]"
        assert "could not be read as a PDF" in row["masked_shape"]

    def test_NotAPdf_WhenARebuildFollows_IsNotTriedAgain(self, root, store, reads):
        keep(root, NOT_A_PDF, "broken.pdf")
        assert reads.attempts == 1
        reads.fresh_process()

        rebuild_from_raw(store)
        rebuild_from_raw(store)

        assert reads.attempts == 0
        assert only_row(store)["failure"] != ""


class TestARebuildFillsAndRefreshesExtractions:
    def kept_three(self, root: Path) -> None:
        keep(root, santander(D(2026, 8, 10), 1211), "one.pdf")
        keep(root, santander(D(2026, 9, 10), 1322), "two.pdf")
        keep(root, credit_union(7), "three.pdf")

    def test_Rebuild_WhenEveryExtractionIsCurrent_ReadsNoDocument(self, root, store, reads):
        self.kept_three(root)
        reads.fresh_process()

        rebuild_from_raw(store)

        assert reads.total == 0

    def test_Rebuild_WhenTheExtractorVersionMoved_ReadsEachDocumentExactlyOnce(
        self, root, store, reads, monkeypatch
    ):
        self.kept_three(root)
        before = rows_of(store)
        moved = pdf_statements.EXTRACTOR_VERSION + 1
        monkeypatch.setattr(pdf_statements, "EXTRACTOR_VERSION", moved)
        reads.fresh_process()

        rebuild_from_raw(store)

        assert reads.text == 3, "one text read for each of the three documents, no more"
        assert reads.attempts == 3
        after = rows_of(store)
        assert set(after) == set(before)
        assert {row[1] for row in after.values()} == {pdf_statements.EXTRACTOR_VERSION}
        reads.fresh_process()
        rebuild_from_raw(store)
        assert reads.total == 0

    def test_Rebuild_WhenTheTableWasDroppedAndRefilled_GivesTheSameRowsFromRawAlone(
        self, root, store, reads
    ):
        self.kept_three(root)
        before = rows_of(store)
        store.clear_statement_extractions()
        store.connection.commit()
        reads.fresh_process()

        rebuild_from_raw(store)

        assert rows_of(store) == before
        assert reads.attempts == 3

    def test_Rebuild_WhenAStoredRowIsDamaged_SaysSoAndReadsThatDocumentAgain(
        self, root, store, reads, capsys
    ):
        self.kept_three(root)
        damaged = sorted(rows_of(store))[0]
        store.keep_statement_extraction(
            damaged, pdf_statements.EXTRACTOR_VERSION, ExtractionRecord(cells="not json")
        )
        store.connection.commit()
        reads.fresh_process()
        capsys.readouterr()

        rebuild_from_raw(store)

        assert reads.attempts == 1
        assert "damaged" in capsys.readouterr().err
        assert statement_extraction.stored(store, damaged) is not None


class TestTheStoreGrowsTheTable:
    def test_StoreStampedAtTheOldVersion_GrowsTheExtractionsTableAndKeepsItsRows(
        self, tmp_path
    ):
        path = tmp_path / "old.sqlite3"
        with Store(path) as opened:
            from obdi.ingest.accounts import AccountRecord, AccountRef

            opened.declare_account(AccountRecord(ref=AccountRef("witness"), label="Witness"))
        connection = sqlite3.connect(path)
        connection.execute("DROP TABLE statement_extractions")
        connection.execute(
            "UPDATE obdi_meta SET value = ? WHERE key = 'schema_version'",
            (str(SCHEMA_VERSION - 1),),
        )
        connection.commit()
        connection.close()

        with Store(path) as reopened:
            count = reopened.connection.execute(
                "SELECT COUNT(*) FROM statement_extractions"
            ).fetchone()[0]
            kept = [account.label for account in reopened.declared_accounts()]

        assert count == 0
        assert kept == ["Witness"]

    def test_StoreStampedNewerThanTheCode_IsRefusedRatherThanOpened(self, tmp_path):
        path = tmp_path / "newer.sqlite3"
        with Store(path):
            pass
        connection = sqlite3.connect(path)
        connection.execute(
            "UPDATE obdi_meta SET value = ? WHERE key = 'schema_version'",
            (str(SCHEMA_VERSION + 1),),
        )
        connection.commit()
        connection.close()

        with pytest.raises(StoreIsNewer):
            Store(path)
