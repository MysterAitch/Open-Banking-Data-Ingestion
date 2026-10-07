"""What Bring in says, as data: which files are wanted, how they are counted, which door an upload
goes through, and what an upload settled.

Every answer is decided here in dates before the first run. The household is `fetch_gaps_world`'s
(TODAY 2026-10-05), whose gaps are decided in its docstring: nine statements and two exports are
wanted for eight accounts (card-behind two statements, card-late-one, card-hole, card-virgin,
card-early, opens-on-day and between one each, opens-on-day a second for its flag, main two
exports); card-feed-only and card-qif want a balance, which is not a file.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date

import pytest

from fetch_gaps_world import TODAY, Loaded, load_household
from obdi.read.bring_in import (
    UploadKind,
    by_account,
    files_wanted,
    newly_lockable,
    settled_sentence,
    upload_kind,
    wanted_heading,
)
from obdi.read.fetch_gaps import AccountOutlook, Basis, FetchGap, FetchReport, GapKind
from obdi.verify.standing_data import AccountStanding

D = date


@pytest.fixture(scope="module")
def world(tmp_path_factory) -> Loaded:
    return load_household(tmp_path_factory.mktemp("bring-in-model"))


class TestTheFilesWanted:
    def test_Household_WhenFilesAreMissing_WantsNineStatementsAndTwoExportsForEightAccounts(
        self, world
    ):
        files = files_wanted(world.report)

        assert wanted_heading(files) == "Wanted: 9 statements and 2 exports for 8 accounts"
        assert len(by_account(files)) == 8

    def test_Household_WhenAnAccountWantsOnlyABalance_ListsNoFileForIt(self, world):
        accounts = by_account(files_wanted(world.report))

        assert "card-feed-only" not in accounts and "card-qif" not in accounts
        assert "card-quiet" not in accounts, "an account that needs nothing is not listed"

    def test_Account_WhenSeveralStatementsAreWaiting_IsOneFileForEachWithItsOwnDays(self, world):
        behind = by_account(files_wanted(world.report))["card-behind"]

        assert [(f.first, f.last) for f in behind] == [
            (D(2026, 7, 11), D(2026, 8, 10)),
            (D(2026, 8, 11), D(2026, 9, 10)),
        ]
        assert [f.since for f in behind] == [D(2026, 8, 10), D(2026, 9, 10)]

    def test_Account_WhenItsExportStops_WantsAnExportAgainstTheExportsOwnSource(self, world):
        main = by_account(files_wanted(world.report))["main"]

        assert all(f.export for f in main)
        assert {f.source for f in main} == {"starling-csv"}

    def test_Statement_WhenOneIsWanted_CarriesNoSourceForASetAsideToBeMadeAgainst(self, world):
        virgin = by_account(files_wanted(world.report))["card-virgin"]

        assert [(f.first, f.last, f.source) for f in virgin] == [
            (D(2026, 6, 5), D(2026, 7, 4), "")
        ]

    def test_Hole_WhenInferred_IsToldFromOneThatIsStatedInAFewWords(self, world):
        accounts = by_account(files_wanted(world.report))

        assert accounts["card-hole"][0].words == "probably missing"
        assert accounts["card-virgin"][0].words == "no statement lists it"

    def test_Space_WhenAGapIsGivenForIt_IsNeverListedBecauseItIsFetchedWithItsParent(self):
        gap = FetchGap(
            "pot", GapKind.NEWER_STATEMENT, D(2026, 8, 1), D(2026, 9, 1), Basis.STATED, "", ""
        )
        report = FetchReport((AccountOutlook("pot", (gap,), space_of="main"),), TODAY)

        assert files_wanted(report) == ()

    def test_Report_WhenItCouldNotBeWorkedOut_WantsNothingAndSaysNothingFalse(self):
        assert files_wanted(None) == ()


class TestTheCount:
    @pytest.mark.parametrize(
        ("statements", "exports", "accounts", "said"),
        [
            (5, 1, 4, "Wanted: 5 statements and 1 export for 4 accounts"),
            (1, 0, 1, "Wanted: 1 statement for 1 account"),
            (0, 2, 1, "Wanted: 2 exports for 1 account"),
        ],
    )
    def test_Heading_AgreesInNumberAndLeavesOutAKindWithNone(
        self, statements, exports, accounts, said
    ):
        gap = FetchGap(
            "a", GapKind.HOLE_BETWEEN, D(2026, 6, 1), D(2026, 6, 30), Basis.STATED, "", ""
        )
        sample = files_wanted(FetchReport((AccountOutlook("a", (gap,)),), TODAY))[0]
        files = [sample] * statements + [replace(sample, kind=GapKind.EXPORT_STOPS)] * exports
        # Spread the files over exactly `accounts` accounts, whatever kind they are.
        spread = [replace(item, account=f"a{n % accounts}") for n, item in enumerate(files)]

        assert wanted_heading(spread) == said

    def test_Heading_AfterAnUpload_SaysStillWanted(self, world):
        files = files_wanted(world.report)[:1]

        assert wanted_heading(files, still=True) == "Still wanted: 1 statement for 1 account"


class TestWhichDoorAFileGoesThrough:
    @pytest.mark.parametrize(
        ("name", "payload", "kind"),
        [
            ("Statement-2026-08.pdf", b"anything", UploadKind.STATEMENT),
            ("STATEMENT.PDF", b"anything", UploadKind.STATEMENT),
            ("download", b"%PDF-1.7 ...", UploadKind.STATEMENT),
            ("transactions.csv", b"Date,Amount", UploadKind.EXPORT),
            ("export.qif", b"!Type:Bank", UploadKind.EXPORT),
            ("export.txt", b"Date,Amount", UploadKind.EXPORT),
        ],
    )
    def test_Upload_ByItsNameOrItsFirstBytes_IsAStatementOrAnExport(self, name, payload, kind):
        assert upload_kind(name, payload) is kind


def _standing(world: Loaded, through: date | None) -> AccountStanding:
    """The agreeing account's standing, with its `through` day set to what a scenario needs."""
    base = world.standings["card-quiet"]
    own = replace(base.standing.own, through=through)
    return replace(base, standing=replace(base.standing, own=own))


class TestWhatAnUploadSettled:
    def test_File_WhenItMovesTheAddingUpDayOn_SaysItInTheTrustSentencesTerms(self, world):
        said = settled_sentence(
            "Everyday card", _standing(world, D(2026, 7, 10)), _standing(world, D(2026, 9, 10))
        )

        assert said == (
            "Everyday card now adds up to the known balances to 2026-09-10; it was 2026-07-10."
        )

    def test_File_WhenTheAccountHadNothingToCheckAgainst_SaysSo(self, world):
        base = world.standings["card-feed-only"]

        said = settled_sentence("Feed card", base, _standing(world, D(2026, 9, 10)))

        assert said == (
            "Feed card now adds up to the known balances to 2026-09-10; "
            "it had nothing to check against."
        )

    def test_File_WhenNothingMoved_SaysItQuietlyAndAsBefore(self, world):
        same = _standing(world, D(2026, 9, 10))

        assert settled_sentence("Everyday card", same, same) == (
            "Everyday card adds up to the known balances to 2026-09-10, as before."
        )

    def test_File_WhenItLeavesTheAccountWithNothingToCheck_SaysThatAndNotAFalseAddingUp(
        self, world
    ):
        said = settled_sentence("Feed card", None, world.standings["card-feed-only"])

        assert said == "Feed card has nothing to check against."

    def test_File_WhenTheStandingCouldNotBeRead_SaysNothing(self, world):
        assert settled_sentence("Everyday card", _standing(world, D(2026, 7, 10)), None) == ""

    def test_Accounts_WhenAnUploadLeavesDaysToLockIn_NamesOnlyThoseThatHadNone(self, world):
        none_before = {"card-quiet": world.standings["card-feed-only"]}
        after = {"card-quiet": world.standings["card-quiet"]}

        assert newly_lockable(none_before, none_before) == ()
        assert set(newly_lockable(none_before, after)) <= {"card-quiet"}
        assert newly_lockable(after, after) == ()
