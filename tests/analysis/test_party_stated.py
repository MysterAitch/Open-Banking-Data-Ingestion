"""Which accounts of a household state their other party and which are named by the description,
over `party_stated_world`, whose answers are written in its docstring before any run."""

from __future__ import annotations

from datetime import date

import pytest

from obdi.analysis.party_stated import party_stated_by_account
from obdi.ingest.store import Store
from obdi.read.party_coverage import Stretch
from party_stated_world import (
    LINKED,
    MIXED,
    MIXED_DESCRIBED_FIRST,
    MIXED_DESCRIBED_LAST,
    MIXED_DESCRIBED_ROWS,
    MIXED_STATED_ROWS,
    PDFONLY,
    PDFONLY_ROWS,
    STATED,
    build_linked,
    build_party_household,
)


@pytest.fixture(scope="module")
def found(tmp_path_factory: pytest.TempPathFactory) -> dict:
    with Store(tmp_path_factory.mktemp("party") / "store.sqlite3") as store:
        build_party_household(store)
        return party_stated_by_account(store)


class TestAnAccountWhoseFeedNamesEveryMerchant:
    def test_Stated_IsOneUnbrokenStatedRunAndSaysNothing(self, found):
        party = found[STATED]

        assert party.stated_runs == ((date(2026, 8, 3), date(2026, 9, 28)),)
        assert party.described_runs == ()
        assert party.stretches == ()
        assert party.transactions == 9

    def test_Stated_IsAskedForNothing(self, found):
        assert not found[STATED].said


class TestAnAccountWhoseStatementOnlyMonthsAreNamedByDescription:
    def test_Mixed_IsHollowOverExactlyTheStatementOnlyMonths(self, found):
        party = found[MIXED]

        assert party.stated_runs == ((date(2026, 1, 5), date(2026, 4, 27)),)
        assert party.described_runs == ((MIXED_DESCRIBED_FIRST, MIXED_DESCRIBED_LAST),)
        assert party.transactions == MIXED_STATED_ROWS + MIXED_DESCRIBED_ROWS

    def test_Mixed_HasOneStretchWithTheRightCountAndDates(self, found):
        assert found[MIXED].stretches == (
            Stretch(MIXED_DESCRIBED_FIRST, MIXED_DESCRIBED_LAST, MIXED_DESCRIBED_ROWS),
        )

    def test_Mixed_CouldBeAskedForAnExportFileBecauseItHasAFeed(self, found):
        assert found[MIXED].askable


class TestADescriptionThatIsExactlyAStatedPartysName:
    def test_Linked_IsNotDescribedBecauseTheLadderTakesItToTheStatedParty(self, tmp_path):
        with Store(tmp_path / "linked.sqlite3") as store:
            build_linked(store)
            party = party_stated_by_account(store)[LINKED]

        assert party.described == 0
        assert party.described_runs == ()
        assert party.stretches == ()
        assert party.transactions == 5


class TestAnAccountWhoseOnlySourceIsAStatementReaderThatStatesNoParty:
    def test_PdfOnly_IsHollowThroughoutAndSaysSo(self, found):
        party = found[PDFONLY]

        assert party.stated_runs == ()
        assert party.described == PDFONLY_ROWS
        assert len(party.stretches) == 1

    def test_PdfOnly_IsNotAskedForAFileThatWouldNotHelp(self, found):
        assert not found[PDFONLY].askable
