# ruff: noqa: F401, F811
# The fixtures are imported from the file that builds them; used by name they read to the linter
# as unused and then as redefined.
"""A store of forty kept statements: the first load of each page after a restart reads none.

Measured 2026-10-06 over forty invented kept PDFs (thirty-five card statements and five
all-accounts documents), first load in a fresh process, on the machine the suite runs on:

    before  the Kept statements listing 1.00 s with 40 text reads; the Statements page 1.03 s
            with 40 text reads (invented one-page files: a real statement costs far more per
            read, which is the point of reading it once)
    after   the listing 0.02 s with no read; the Statements page 0.29 s with no read, the same
            0.25 s to 0.29 s the page costs when nothing is parsed at all (the server, the
            account names, and the render)

The assertions are the counts, which are exact; the time is only bounded loosely, because a
figure that failed on a slow machine would say nothing about the code. The answers are decided
before the first run: no read on either page; and a rebuild after the extractor moved reads each
of the forty documents exactly once.
"""

from __future__ import annotations

import time
from datetime import date, timedelta
from pathlib import Path

import httpx

from obdi.cli import build_web_config
from obdi.ingest.parsers import pdf_statements
from obdi.ingest.rebuild import rebuild_from_raw
from served_store import served_store
from test_bring_in_assign import credit_union, santander
from test_statement_extraction_stored import Reads, keep, reads, root, store

CARDS = 35
UNIONS = 5
DOCUMENTS = CARDS + UNIONS
#: Far above the measured figure and far below what forty reads cost on a real store.
LOOSE_SECONDS = 20.0


def keep_forty(root: Path) -> None:
    for number in range(CARDS):
        keep(
            root,
            santander(date(2026, 1, 1) + timedelta(days=number), 100 + number),
            f"card-{number}.pdf",
        )
    for month in range(1, UNIONS + 1):
        keep(root, credit_union(month), f"union-{month}.pdf")


class TestFortyKeptStatementsAfterARestart:
    def test_KeptListing_WithFortyKeptStatementsInAFreshProcess_ReadsNoDocument(
        self, root, store, reads
    ):
        keep_forty(root)
        reads.fresh_process()
        wired = build_web_config(root / "store.sqlite3")
        assert wired is not None and wired.kept_statements is not None

        started = time.perf_counter()
        listing = wired.kept_statements()
        took = time.perf_counter() - started

        assert len(listing) == DOCUMENTS
        assert reads.total == 0
        assert not any(item["not_extracted"] for item in listing)
        assert took < LOOSE_SECONDS

    def test_StatementsPage_WithFortyKeptStatementsInAFreshProcess_ReadsNoDocument(
        self, root, store, reads
    ):
        keep_forty(root)
        reads.fresh_process()

        with served_store(root, lambda _store: None, bound=[]) as base:
            started = time.perf_counter()
            page = httpx.get(f"{base}/statements", timeout=120)
            took = time.perf_counter() - started

        assert page.status_code == 200
        assert reads.total == 0
        assert f"{DOCUMENTS} kept" in page.text
        assert took < LOOSE_SECONDS

    def test_Rebuild_AfterTheExtractorMovedWithFortyKeptStatements_ReadsEachExactlyOnce(
        self, root, store, reads, monkeypatch
    ):
        keep_forty(root)
        monkeypatch.setattr(
            pdf_statements, "EXTRACTOR_VERSION", pdf_statements.EXTRACTOR_VERSION + 1
        )
        reads.fresh_process()

        rebuild_from_raw(store)

        assert reads.text == DOCUMENTS
        assert reads.attempts == DOCUMENTS
