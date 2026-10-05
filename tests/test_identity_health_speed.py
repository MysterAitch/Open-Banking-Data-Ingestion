"""Identity health over a store of the size the owner's is, answered without working it out twice.

THE FAULT AS MET. The page took 13 to 19 seconds on every load of the owner's store. Two of its
measurements (the exact rules and the statements by what they list) each worked out the
statement-opening figures whole, which read every account's known balances twice and explained
every change in each reading, and the exact rules were worked out afresh on every visit.

KNOWN ANSWERS. The fast path must say what the slow one said, so the slow path is kept callable
here: the openings worked out with every change explained (the way the measurement read them
before) are compared, as text and as structure, with those it reads now. Nothing here records a
figure.

MEASURED on the large store (`large_store_corpus`, the shape of a main account of 5,223 rows,
five Spaces, four sources, 570 known balances, over 6 years), 2026-10-05, a machine busy with
another build, wall time and statements the page issues:

    before    first visit 6.8 s, 1,078 statements;   later visits 3.0 to 4.5 s, 577 statements
    after     first visit 4.6 s,   709 statements;   later visits 0.15 s,         98 statements

The bounds below are loose on time (a slower machine must not flake) and tight on statements.
"""

from __future__ import annotations

import shutil

import pytest

from large_store_corpus import MAIN, LargeStore, cached_large_store
from large_store_pages import serving
from obdi import statement_opening_measure
from obdi.exact_rule_measure import exact_rule_report
from obdi.family_anchors import families_of
from obdi.statement_listing_measure import statement_listing_report
from obdi.statement_opening_measure import statement_opening_report
from obdi.store import Store

PAGE = "/identity-health"

#: Statements the page issues once its measurements are held: measured 98.
WARM_STATEMENTS = 160
#: The first visit after a start, or after a write: measured 709, against 1,078 before.
COLD_STATEMENTS = 900
#: Seconds. Measured 0.15 warm and 4.6 cold; a slow machine and a busy one have room.
WARM_SECONDS = 4.0
COLD_SECONDS = 40.0


@pytest.fixture(scope="module")
def large() -> LargeStore:
    return cached_large_store()


@pytest.fixture(scope="module")
def pages(large, tmp_path_factory):
    with serving(large, tmp_path_factory.mktemp("identity-health")) as served:
        yield served


def without_clock(page: str) -> str:
    """The page less the line that says when it was worked out."""
    return "\n".join(line for line in page.splitlines() if "Worked out in" not in line)


def read_as_before(monkeypatch) -> None:
    """The openings as the measurement read them before: nothing passed as `explain_after`."""
    real = statement_opening_measure.effective_opening

    def explaining(store, ref, rows=None, **kwargs):
        kwargs.pop("explain_after", None)
        return real(store, ref, rows, **kwargs)

    monkeypatch.setattr(statement_opening_measure, "effective_opening", explaining)


class TestTheSlowPathAndTheFastPathSayTheSame:
    def test_OpeningFigures_WhenChangesAreNotExplained_AreTheSameFigures(
        self, large, monkeypatch
    ):
        with Store(large.path) as store:
            families = families_of(store, large.account_map)
            fast = statement_opening_report(store, families)
            read_as_before(monkeypatch)
            slow = statement_opening_report(store, families)

        assert fast.sentences() == slow.sentences()
        assert [a.account for a in fast.accounts] == [a.account for a in slow.accounts]
        assert MAIN in [a.account for a in fast.accounts]
        for quick, full in zip(fast.accounts, slow.accounts, strict=True):
            assert quick.by_date == full.by_date
            assert quick.stated_by_day == full.stated_by_day
            assert quick.today_sentence == full.today_sentence
            assert quick.rule_sentence == full.rule_sentence
            assert quick.opening.readings == full.opening.readings

    def test_ExactRulesAndListings_WhenGivenSharedOpenings_ReadTheSameAsWorkingThemOutAlone(
        self, large
    ):
        with Store(large.path) as store:
            families = families_of(store, large.account_map)
            alone = exact_rule_report(store, large.account_map).describe()
            openings = statement_opening_report(store, families)
            shared = exact_rule_report(store, large.account_map, openings).describe()
            listing_alone = statement_listing_report(store, families)
            listing_shared = statement_listing_report(store, families, openings=openings)

        assert alone == shared
        assert repr(listing_alone) == repr(listing_shared)


    def test_ExactRulesText_WhenExplanationsAreRead_MatchesTheFastText(self, large, monkeypatch):
        with Store(large.path) as store:
            fast = exact_rule_report(store, large.account_map).describe()
            read_as_before(monkeypatch)
            slow = exact_rule_report(store, large.account_map).describe()

        assert fast == slow
        assert "No account holds a statement" not in fast


class TestTheIdentityHealthPageOverTheLargeStore:
    def test_Page_FirstVisit_ThenLaterVisit_IsHeldAndSaysTheSame(self, pages):
        first = pages.get(PAGE)
        later = pages.get(PAGE)

        assert (first.status, later.status) == (200, 200)
        assert without_clock(first.body) == without_clock(later.body)
        assert first.statements <= COLD_STATEMENTS, first.statements
        assert first.seconds <= COLD_SECONDS, first.seconds
        assert later.statements <= WARM_STATEMENTS, later.statements
        assert later.seconds <= WARM_SECONDS, later.seconds

    def test_Page_AfterTheStoreChanges_WorksTheMeasurementsOutAgain(
        self, large, tmp_path, pages
    ):
        from datetime import date

        from obdi.ingest import reconcile_batch
        from test_ledger import txn

        copy = tmp_path / "copy"
        copy.mkdir()
        shutil.copy2(large.path, copy / "store.sqlite3")
        held = LargeStore(
            copy,
            copy / "store.sqlite3",
            large.account_map,
            large.main_rows_distinct,
            large.space_rows_distinct,
            large.statements,
            large.stated_days,
            large.other_accounts,
        )
        with serving(held, tmp_path) as served:
            served.get(PAGE)
            held_again = served.get(PAGE)
            with Store(held.path) as store:
                reconcile_batch(
                    store,
                    [txn(MAIN, "starling", "late-1", date(2026, 9, 29), -777, "LATE ARRIVAL")],
                    digest="late",
                )
            after = served.get(PAGE)
            then_held = served.get(PAGE)

        assert held_again.statements <= WARM_STATEMENTS
        assert after.statements > 3 * WARM_STATEMENTS, after.statements
        assert then_held.statements <= WARM_STATEMENTS
