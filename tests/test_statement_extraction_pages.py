# ruff: noqa: F401, F811
# The fixtures are imported from the file that builds them; used by name they read to the linter
# as unused and then as redefined.
"""No page reads a PDF: the pages read what was extracted when the statement was kept.

The scenes of `test_statement_extraction_stored.py` count the reads of a document's bytes while
statements are kept and rebuilt; these count them while pages are served, from a simulated fresh
process. The answers are decided before the first run:

  * with the rows stored, the Kept statements listing, the Statements page, Bring in, and a kept
    statement's masked shape make no read at all, and still say what a reader found;
  * with no row at the current version (a store from before extractions were kept, or an
    extractor that has since moved) the same pages make no read either, and say of that
    document "not yet extracted; the next rebuild extracts it" rather than "no parser" - and
    once the rebuild has run they say what they said before, still without a read;
  * what a page shows is as masked as it was: the payee planted in each invented document
    appears in no page, and the shape a page shows is the one the file would have given.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import httpx
import pytest

import obdi.statement_extraction as statement_extraction
import obdi.statement_terms as statement_terms
from obdi.bring_in_preview import preview_html
from obdi.cli import build_web_config
from obdi.parsers import pdf_statements
from obdi.rebuild import rebuild_from_raw
from obdi.statement_shape import shape_of_extraction, shape_report
from obdi.store import Store
from served_store import served_store
from test_bring_in_assign import (
    PLANTED_PAYEE,
    SAVER_KEY,
    credit_union,
    letter,
    santander,
)
from test_statement_extraction_stored import Reads, keep, reads, root, store

D = date
NOT_YET = "not yet extracted; the next rebuild extracts it"


def keep_documents(root: Path) -> dict[str, int]:
    """Keep a card statement, an all-accounts document, and a letter no reader reads."""
    return {
        "card": keep(root, santander(D(2026, 8, 10), 1211), "card.pdf"),
        "union": keep(root, credit_union(7), "union.pdf"),
        "letter": keep(root, letter("a topic no bank wrote"), "letter.pdf"),
    }


def listing(root: Path) -> list[dict[str, object]]:
    wired = build_web_config(root / "store.sqlite3")
    assert wired is not None and wired.kept_statements is not None
    return wired.kept_statements()


def first_name(entry: dict[str, object]) -> str:
    names = entry["names"]
    assert isinstance(names, list) and names
    return str(names[0][0])


def section_labels(entry: dict[str, object]) -> list[str]:
    parts = entry["sections"]
    assert isinstance(parts, list)
    return [str(part["label"]) for part in parts]


class TestWhenTheRowsAreStored:
    def test_KeptListing_InAFreshProcess_ReadsNoDocumentAndStillSaysWhatEachIs(
        self, root, store, reads
    ):
        keep_documents(root)
        reads.fresh_process()

        entries = {str(item["origin"]): item for item in listing(root)}

        assert reads.total == 0
        assert entries["card.pdf"]["parser"] == "santander-cc-pdf"
        assert entries["card.pdf"]["rows"] == 1
        assert first_name(entries["card.pdf"]) == "Santander"
        assert section_labels(entries["union.pdf"]) == ["Regular Saver", "Holiday Pot"]
        assert not entries["letter.pdf"]["parser"]
        assert not any(item.get("not_extracted") for item in entries.values())

    def test_StatementsPage_InAFreshProcess_ReadsNoDocumentAndShowsNoPayee(
        self, root, reads
    ):
        keep_documents(root)
        reads.fresh_process()

        with served_store(root, lambda store: None, bound=[]) as base:
            page = httpx.get(f"{base}/statements", timeout=120)

        assert page.status_code == 200
        assert reads.total == 0
        assert "santander-cc-pdf" in page.text
        assert PLANTED_PAYEE not in page.text
        assert NOT_YET not in page.text

    def test_BringIn_WithKeptFilesInAFreshProcess_ReadsNoDocumentAndPreviewsTheReader(
        self, root, reads
    ):
        ids = keep_documents(root)
        reads.fresh_process()

        with served_store(root, lambda store: None, bound=[]) as base:
            page = httpx.get(f"{base}/bring-in", timeout=120)
        entry = next(item for item in listing(root) if item["id"] == ids["card"])
        preview = preview_html(entry)

        assert page.status_code == 200
        assert reads.total == 0
        assert "Read by" in preview and "santander-cc-pdf" in preview
        assert "Names found: Santander" in preview
        assert PLANTED_PAYEE not in page.text + preview

    def test_ShapePage_ForAKeptStatementInAFreshProcess_ReadsNoDocumentAndIsMasked(
        self, root, reads
    ):
        ids = keep_documents(root)
        reads.fresh_process()

        with served_store(root, lambda store: None, bound=[]) as base:
            page = httpx.get(f"{base}/statement-shape?artefact={ids['card']}", timeout=120)

        assert page.status_code == 200
        assert reads.total == 0
        assert "card.pdf: " in page.text
        assert "lines across 1 page" in page.text
        assert "values masked" in page.text
        assert "The whole document" in page.text, "the reader's findings are still given"
        assert PLANTED_PAYEE not in page.text

    def test_ShapePage_ForADividedStatement_ReadsNoDocumentAndGivesEachAccountAFinding(
        self, root, reads
    ):
        ids = keep_documents(root)
        reads.fresh_process()

        with served_store(root, lambda store: None, bound=[]) as base:
            page = httpx.get(f"{base}/statement-shape?artefact={ids['union']}", timeout=120)

        assert reads.total == 0
        assert "Regular Saver" in page.text and "Holiday Pot" in page.text

    def test_AssignedSections_InAFreshProcess_AreReadFromTheStoredRowsWithoutReadingTheDocument(
        self, root, store, reads
    ):
        ids = keep_documents(root)
        wired = build_web_config(root / "store.sqlite3")
        assert wired is not None and wired.assign_statement_section is not None
        said = wired.assign_statement_section(ids["union"], SAVER_KEY, "credit-union-saver")
        assert "assigned to" in said, said
        reads.fresh_process()

        found = list(statement_terms.assigned_sections(store))

        assert reads.total == 0
        assert [section is not None and not section.refusal for _, section in found] == [True]


class TestWhenARowIsMissing:
    def forget(self, store: Store) -> None:
        store.clear_statement_extractions()
        store.connection.commit()

    def test_KeptListing_WhenNoRowIsStored_SaysNotYetExtractedAndReadsNothing(
        self, root, store, reads
    ):
        keep_documents(root)
        self.forget(store)
        reads.fresh_process()

        entries = listing(root)

        assert reads.total == 0
        assert [item["not_extracted"] for item in entries] == [True, True, True]
        assert all(not item["parser"] and not item["sections"] for item in entries)

    def test_StatementsPage_WhenNoRowIsStored_SaysNotYetExtractedInPlaceOfNoParser(
        self, root, store, reads
    ):
        keep_documents(root)
        self.forget(store)
        reads.fresh_process()

        with served_store(root, lambda store: None, bound=[]) as base:
            page = httpx.get(f"{base}/statements", timeout=120)

        assert page.status_code == 200
        assert reads.total == 0
        assert NOT_YET in page.text
        assert "no parser for this layout yet" not in page.text
        assert "3 not yet extracted" in page.text

    def test_BringIn_PreviewOfADocumentWithNoRow_SaysNotYetExtracted(self, root, store, reads):
        ids = keep_documents(root)
        self.forget(store)
        reads.fresh_process()

        entry = next(item for item in listing(root) if item["id"] == ids["card"])

        assert NOT_YET in preview_html(entry)
        assert reads.total == 0

    def test_ShapePage_WhenNoRowIsStored_SaysNotYetExtractedAndReadsNothing(
        self, root, store, reads
    ):
        ids = keep_documents(root)
        self.forget(store)
        reads.fresh_process()

        with served_store(root, lambda store: None, bound=[]) as base:
            page = httpx.get(f"{base}/statement-shape?artefact={ids['card']}", timeout=120)

        assert page.status_code == 200
        assert reads.total == 0
        assert NOT_YET in page.text
        assert "lines across" not in page.text

    def test_AssignedSections_WhenNoRowIsStored_AreUnavailableAndSaySoRatherThanReading(
        self, root, store, reads, capsys
    ):
        ids = keep_documents(root)
        wired = build_web_config(root / "store.sqlite3")
        assert wired is not None and wired.assign_statement_section is not None
        wired.assign_statement_section(ids["union"], SAVER_KEY, "credit-union-saver")
        self.forget(store)
        reads.fresh_process()
        capsys.readouterr()

        found = list(statement_terms.assigned_sections(store))

        assert reads.total == 0
        assert [section for _, section in found] == [None]
        assert NOT_YET in capsys.readouterr().err

    def test_Pages_WhenTheExtractorMovedAndARebuildRuns_ReadEachDocumentOnceThenNeverAgain(
        self, root, store, reads, monkeypatch
    ):
        keep_documents(root)
        moved = pdf_statements.EXTRACTOR_VERSION + 1
        monkeypatch.setattr(pdf_statements, "EXTRACTOR_VERSION", moved)
        reads.fresh_process()

        before = listing(root)
        assert reads.total == 0
        assert all(item["not_extracted"] for item in before)

        rebuild_from_raw(store)
        assert reads.text == 3

        reads.fresh_process()
        after = listing(root)
        with served_store(root, lambda store: None, bound=[]) as base:
            page = httpx.get(f"{base}/statements", timeout=120)
        assert reads.total == 0
        assert not any(item.get("not_extracted") for item in after)
        assert NOT_YET not in page.text


class TestWhatAPageShowsIsWhatTheFileWouldHaveGiven:
    @pytest.mark.parametrize("mask", [True, False])
    def test_ShapeOfAStoredExtraction_ForAKeptStatement_EqualsTheShapeOfItsFile(
        self, root, store, tmp_path, mask
    ):
        payload = credit_union(7)
        keep(root, payload, "union.pdf")
        digest = str(
            store.connection.execute("SELECT digest FROM statement_extractions").fetchone()[0]
        )
        found = statement_extraction.stored(store, digest)
        assert found is not None
        file = tmp_path / "union.pdf"
        file.write_bytes(payload)

        from_row = shape_of_extraction(
            found.raw.lines, found.raw.table, found.raw.page_count, path="union.pdf", mask=mask
        ).describe()

        assert from_row == shape_report(file, mask=mask).describe()
        if mask:
            assert found.masked_shape == shape_of_extraction(
                found.raw.lines, found.raw.table, found.raw.page_count
            ).describe()
