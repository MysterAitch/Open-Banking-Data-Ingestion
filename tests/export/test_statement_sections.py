"""Giving each account of an "all accounts" statement its own account.

The statement is kept as one artefact and stays unassigned as a whole; what a
person decides is made per account within it, and that decision is DECLARED
state - nothing replayed from the raw evidence can reproduce it. So the
questions here are the ones that matter for declared state: does it survive
"Rebuild from raw", is it counted among the irreplaceable work, is it exported
and backed up, and does it follow an account that is renamed.

Every document is invented (`credit_union_documents`). The nine accounts and
their answers - rows per account, closing balances, totals - were written down
before any document existed, so a disagreement is a fault in the code.
"""

from __future__ import annotations

import re
import sqlite3
from datetime import date
from pathlib import Path

import httpx
import pytest

from credit_union_documents import (
    NINE,
    NINE_CLOSINGS,
    NINE_ROWS,
    Move,
    document,
    nine_accounts,
    pdf,
    section,
)
from obdi.export.export_declared import export_declared
from obdi.ingest.accounts import AccountMap, AccountRecord, AccountRef
from obdi.ingest.backup import take_backup
from obdi.ingest.parsers.base import ParseError
from obdi.ingest.parsers.credit_union_pdf import section_key
from obdi.ingest.rebuild import rebuild_from_raw
from obdi.ingest.restore import restore_backup
from obdi.ingest.statement_terms import statement_balances
from obdi.ingest.store import SCHEMA_VERSION, Store
from obdi.read.position import read_position
from obdi.verify.statement_sections import assign_section, section_token
from section_harness import (
    UNASSIGNED,
    config,
    environment,
    holdings,
    keep,
    serve_config,
    total,
)

SAVER = "credit-union-saver"
LOAN = "credit-union-personal-loan"
SAVER_KEY = section_key("Regular Saver")
LOAN_KEY = section_key("Personal -9.50%")


@pytest.fixture
def db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    environment(monkeypatch, tmp_path)
    path = tmp_path / "store.sqlite3"
    with Store(path):
        pass
    return path


@pytest.fixture
def nine(db: Path) -> tuple[Path, int]:
    """The nine-account document, kept and waiting; its artefact id."""
    with Store(db) as store:
        return db, keep(store, pdf(nine_accounts(), step=5.5), "all accounts.pdf")


def _assignments(db: Path) -> list[tuple[str, str]]:
    with Store(db) as store:
        return [(a.section_key, a.account_ref) for a in store.statement_section_assignments()]


class TestAssigningOneAccountOfTheDocument:
    def test_OneSection_ReadsItsOwnRowsIntoItsAccount_AndNothingElse(self, nine):
        db, artefact = nine

        outcome = config(db).assign_statement_section(artefact, SAVER_KEY, SAVER)

        assert "assigned to credit-union-saver" in outcome
        assert sorted(holdings(db, SAVER).values()) == [1, 1, 1]
        assert total(db, SAVER) == 2500 + 249 - 30000
        with Store(db) as store:
            assert store.counts()["transactions"] == NINE_ROWS[0], (
                "no other account's row was read in"
            )

    def test_ASecondSection_IsReadIntoItsOwnAccount_LeavingTheFirstAsItWas(self, nine):
        db, artefact = nine
        wired = config(db)
        wired.assign_statement_section(artefact, SAVER_KEY, SAVER)
        before = holdings(db, SAVER)

        wired.assign_statement_section(artefact, LOAN_KEY, LOAN)

        assert holdings(db, SAVER) == before
        assert total(db, LOAN) == 15500 + 249, "two repayments, each toward zero"
        assert _assignments(db) == [(SAVER_KEY, SAVER), (LOAN_KEY, LOAN)]

    def test_TheStatementItself_StaysUnassignedAsAWhole(self, nine):
        db, artefact = nine

        config(db).assign_statement_section(artefact, SAVER_KEY, SAVER)

        with Store(db) as store:
            row = store.connection.execute(
                "SELECT account_ref FROM raw_artefacts WHERE rowid = ?", (artefact,)
            ).fetchone()
        assert row["account_ref"] == UNASSIGNED

    def test_ASectionThatDoesNotAddUp_IsRefusedAndRecordedNowhere(self, db):
        with Store(db) as store:
            artefact = keep(store, pdf(nine_accounts(penny_out=4), step=5.5), "all.pdf")
        wired = config(db)

        with pytest.raises(ParseError, match="unexplained"):
            wired.assign_statement_section(artefact, section_key("Junior Saver"), SAVER)

        assert _assignments(db) == []
        assert holdings(db, SAVER) == {}
        # The seven other savers and two loans around it are still assignable.
        wired.assign_statement_section(artefact, SAVER_KEY, SAVER)
        assert _assignments(db) == [(SAVER_KEY, SAVER)]

    def test_ASectionAlreadyAssigned_CannotBeMovedToAnotherAccount(self, nine):
        db, artefact = nine
        wired = config(db)
        wired.assign_statement_section(artefact, SAVER_KEY, SAVER)

        with pytest.raises(ValueError, match="already assigned"):
            wired.assign_statement_section(artefact, SAVER_KEY, "some-other-account")

        assert _assignments(db) == [(SAVER_KEY, SAVER)]
        assert holdings(db, "some-other-account") == {}

    def test_ASectionAssignedAgainToTheSameAccount_ChangesNothing(self, nine):
        db, artefact = nine
        wired = config(db)
        wired.assign_statement_section(artefact, SAVER_KEY, SAVER)
        before = holdings(db, SAVER)

        wired.assign_statement_section(artefact, SAVER_KEY, SAVER)

        assert holdings(db, SAVER) == before
        assert len(_assignments(db)) == 1

    def test_AnAccountNameThatIsNotCanonical_AssignsNothing(self, nine):
        db, artefact = nine

        outcome = config(db).assign_statement_section(artefact, SAVER_KEY, "Not A Name!")

        assert outcome.startswith("Not assigned")
        assert _assignments(db) == []

    def test_AKeyThatIsNotInTheDocument_IsRefused(self, nine):
        db, artefact = nine

        with pytest.raises(ValueError, match="no such account"):
            config(db).assign_statement_section(artefact, "nosuchaccount", SAVER)

        assert _assignments(db) == []

    def test_APageToken_FindsTheSameSectionAsItsKey(self, nine):
        db, artefact = nine

        config(db).assign_statement_section(artefact, section_token(LOAN_KEY), LOAN)

        assert _assignments(db) == [(LOAN_KEY, LOAN)]

    def test_AnArtefactThatIsNotAKeptStatement_IsNotAssigned(self, db):
        outcome = config(db).assign_statement_section(9999, SAVER_KEY, SAVER)

        assert outcome == "No kept statement 9999."

    def test_ASingleAccountStatement_IsNotAssignedBySection(self, db):
        with Store(db) as store:
            artefact = keep(
                store,
                pdf(section("Regular Saver", 80000, [Move("04/05/2025", "x", 100)])),
                "one.pdf",
            )

        with pytest.raises(ValueError, match="not divided into accounts"):
            config(db).assign_statement_section(artefact, SAVER_KEY, SAVER)

    def test_TheWholeDocument_StillCannotBeAssignedToOneAccount(self, nine):
        db, artefact = nine

        with pytest.raises(ParseError, match="cannot be assigned to one account"):
            config(db).assign_kept_statement(artefact, SAVER)

        assert holdings(db, SAVER) == {}


class TestASingleAccountStatementBehavesAsBefore:
    def test_AssignedWhole_ItsRowsAreReadAndItsArtefactIsFiled(self, db):
        with Store(db) as store:
            artefact = keep(
                store,
                pdf(
                    section(
                        "Regular Saver",
                        80000,
                        [Move("04/05/2025", "DD Lodgement", 2500)],
                    )
                ),
                "annual.pdf",
            )

        config(db).assign_kept_statement(artefact, SAVER)

        assert total(db, SAVER) == 2500
        with Store(db) as store:
            row = store.connection.execute(
                "SELECT account_ref FROM raw_artefacts WHERE rowid = ?", (artefact,)
            ).fetchone()
            assert row["account_ref"] == SAVER
            assert store.statement_section_assignments() == []


class TestRebuildingFromRaw:
    def test_EveryAssignedSection_IsReplayedUnderItsAccount_ReproducingTheSameRows(self, nine):
        db, artefact = nine
        wired = config(db)
        wired.assign_statement_section(artefact, SAVER_KEY, SAVER)
        wired.assign_statement_section(artefact, LOAN_KEY, LOAN)
        before = (holdings(db, SAVER), holdings(db, LOAN))

        with Store(db) as store:
            report = rebuild_from_raw(store)

        assert (holdings(db, SAVER), holdings(db, LOAN)) == before
        assert report.kept_sections_replayed == 2
        assert report.transactions == NINE_ROWS[0] + NINE_ROWS[7]

    def test_SectionsNobodyAssigned_ContributeNothing_AndAreCounted(self, nine):
        db, artefact = nine
        config(db).assign_statement_section(artefact, SAVER_KEY, SAVER)

        with Store(db) as store:
            report = rebuild_from_raw(store)

        assert report.kept_sections_unassigned == 8
        assert "8 more still have no account" in report.describe()
        with Store(db) as store:
            assert store.counts()["transactions"] == NINE_ROWS[0]

    def _two_accounts(self, db: Path) -> tuple[int, str, str]:
        lines = document(
            section("Regular Saver", 80000, [Move("04/05/2025", "DD Lodgement", 2500)]),
            section("Christmas Club", 15000, [Move("05/05/2025", "DD Lodgement", 1000)]),
        )
        with Store(db) as store:
            artefact = keep(store, pdf(lines, step=10), "two accounts.pdf")
        return artefact, section_key("Regular Saver"), section_key("Christmas Club")

    def test_Rebuild_WhenEverySectionOfADocumentIsAssigned_CountsNoStatementWaitingForAnAccount(
        self, db
    ):
        """The statements page says '0 waiting only for an account, 1 covering
        several accounts'; the rebuild summary must not say '1 kept statement
        with no account yet'."""
        artefact, saver, club = self._two_accounts(db)
        wired = config(db)
        wired.assign_statement_section(artefact, saver, SAVER)
        wired.assign_statement_section(artefact, club, "credit-union-christmas")

        with Store(db) as store:
            report = rebuild_from_raw(store)

        assert (report.kept_unassigned, report.kept_readable) == (0, 0)
        assert report.kept_sections_replayed == 2
        assert report.kept_sections_unassigned == 0
        assert "no account yet" not in report.describe()

    def test_Rebuild_WhenOneSectionOfADocumentIsUnassigned_CountsItAsWaitingForAnAccount(
        self, db
    ):
        artefact, saver, _club = self._two_accounts(db)
        config(db).assign_statement_section(artefact, saver, SAVER)

        with Store(db) as store:
            report = rebuild_from_raw(store)

        assert report.kept_unassigned == 1
        assert report.kept_sections_unassigned == 1
        assert "1 kept statement with no account yet" in report.describe()

    def test_RebuildingTwice_GivesTheSameStore(self, nine):
        db, artefact = nine
        wired = config(db)
        wired.assign_statement_section(artefact, SAVER_KEY, SAVER)
        wired.assign_statement_section(artefact, LOAN_KEY, LOAN)

        with Store(db) as store:
            rebuild_from_raw(store)
            first = {row.entity_id for row in store.all_transactions()}
            rebuild_from_raw(store)
            second = {row.entity_id for row in store.all_transactions()}

        assert first == second
        assert len(first) == NINE_ROWS[0] + NINE_ROWS[7]

    def test_AnAssignedSectionTheDocumentNoLongerHolds_IsReportedNotSwallowed(self, nine):
        db, artefact = nine
        with Store(db) as store:
            digest = str(
                store.connection.execute(
                    "SELECT digest FROM raw_artefacts WHERE rowid = ?", (artefact,)
                ).fetchone()["digest"]
            )
            store.assign_statement_section(digest, "vanishedaccount", SAVER, "Vanished")
            report = rebuild_from_raw(store)

        assert any("no longer in the statement" in problem for problem in report.problems)

    def test_ARebuildThatIsGivenAnAccountMap_ReadsTheSectionsBackAsWell(self, nine):
        db, artefact = nine
        config(db).assign_statement_section(artefact, SAVER_KEY, SAVER)

        with Store(db) as store:
            rebuild_from_raw(store, account_map=AccountMap())

        assert total(db, SAVER) == 2500 + 249 - 30000


class TestTheAssignmentIsDeclaredState:
    def test_TheAssignment_SurvivesARebuild(self, nine):
        db, artefact = nine
        config(db).assign_statement_section(artefact, SAVER_KEY, SAVER)

        with Store(db) as store:
            rebuild_from_raw(store)

        assert _assignments(db) == [(SAVER_KEY, SAVER)]

    def test_TheAssignment_IsCountedAmongTheIrreplaceableWork(self, nine):
        db, artefact = nine
        with Store(db) as store:
            assert store.irreplaceable()["statement section assignments"] == 0
        config(db).assign_statement_section(artefact, SAVER_KEY, SAVER)

        with Store(db) as store:
            assert store.irreplaceable()["statement section assignments"] == 1

    def test_TheAssignment_IsExportedWithTheDigestItBelongsTo(self, nine, tmp_path):
        db, artefact = nine
        config(db).assign_statement_section(artefact, LOAN_KEY, LOAN)
        out = tmp_path / "export"

        with Store(db) as store:
            result = export_declared(store, out)
            digest = store.statement_section_assignments()[0].digest

        import json

        exported = json.loads((out / "statement-sections.json").read_text(encoding="utf-8"))
        assert [(e["digest"], e["section"], e["account"]) for e in exported] == [
            (digest, LOAN_KEY, LOAN)
        ]
        assert result.counts["statement_sections"] == 1

    def test_TheAssignment_SurvivesABackupAndARestore(self, nine, tmp_path):
        db, artefact = nine
        config(db).assign_statement_section(artefact, SAVER_KEY, SAVER)
        take_backup(db, tmp_path / "backup.sqlite3")
        restored = tmp_path / "restored.sqlite3"

        restore_backup(tmp_path / "backup.sqlite3", restored)

        assert _assignments(restored) == [(SAVER_KEY, SAVER)]

    def test_WhenAnAccountIsRebound_TheAssignmentFollowsIt_AndARebuildReadsItThere(self, nine):
        db, artefact = nine
        config(db).assign_statement_section(artefact, SAVER_KEY, SAVER)

        with Store(db) as store:
            store.rebind_account(SAVER, "renamed-saver")
            rebuild_from_raw(store)

        assert _assignments(db) == [(SAVER_KEY, "renamed-saver")]
        assert sorted(holdings(db, "renamed-saver").values()) == [1, 1, 1]
        assert holdings(db, SAVER) == {}, "nothing was re-created under the old name"

    def test_WhenADeclaredAccountIsRenamed_TheAssignmentFollowsIt(self, nine):
        db, artefact = nine
        with Store(db) as store:
            declared = store.declare_account(AccountRecord(ref=AccountRef(SAVER), label="Saver"))
        config(db).assign_statement_section(artefact, SAVER_KEY, SAVER)

        with Store(db) as store:
            store.declare_account(
                AccountRecord(
                    ref=AccountRef("renamed-saver"), label="Saver", stable_id=declared.stable_id
                )
            )

        assert _assignments(db) == [(SAVER_KEY, "renamed-saver")]

    def test_AStoreStampedWithTheOldVersion_GrowsTheTableOnOpen(self, tmp_path):
        path = tmp_path / "old.sqlite3"
        with Store(path):
            pass
        connection = sqlite3.connect(path)
        connection.execute("DROP TABLE statement_sections")
        connection.execute("UPDATE obdi_meta SET value = '10' WHERE key = 'schema_version'")
        connection.commit()
        connection.close()

        with Store(path) as store:
            assert store.statement_section_assignments() == []
            version = store.connection.execute(
                "SELECT value FROM obdi_meta WHERE key = 'schema_version'"
            ).fetchone()
        assert version["value"] == str(SCHEMA_VERSION)


class TestALoanAccount:
    def test_TheLoanSection_IsHeldAsWhatIsOwed_AndThePositionCountsTheLiability(self, nine):
        db, artefact = nine
        wired = config(db)
        wired.assign_statement_section(artefact, SAVER_KEY, SAVER)
        wired.assign_statement_section(artefact, LOAN_KEY, LOAN)

        with Store(db) as store:
            position = read_position(store, today=date(2025, 6, 30))
            accounts = {
                account.ref: account
                for group in position.groups
                for account in group.accounts
            }
        loan = accounts[LOAN]
        saver = accounts[SAVER]
        assert loan.balance is not None and loan.balance.minor == NINE_CLOSINGS[7]
        assert loan.balance.minor < 0, "owed, so a negative position"
        assert saver.balance is not None and saver.balance.minor == NINE_CLOSINGS[0]
        assert position.net_worth is not None
        assert position.net_worth.minor == NINE_CLOSINGS[0] + NINE_CLOSINGS[7]

    def test_EachAssignedSection_StatesItsClosingBalanceAsAnAnchor(self, nine):
        db, artefact = nine
        wired = config(db)
        wired.assign_statement_section(artefact, SAVER_KEY, SAVER)
        wired.assign_statement_section(artefact, LOAN_KEY, LOAN)

        with Store(db) as store:
            balances, _unusable = statement_balances(store)

        held = {(b.account_ref, b.day, b.balance_minor) for b in balances}
        assert (SAVER, date(2025, 5, 31), NINE_CLOSINGS[0]) in held
        assert (LOAN, date(2025, 5, 31), NINE_CLOSINGS[7]) in held

    def test_AnUnassignedSection_StatesNoAnchorForAnyAccount(self, nine):
        db, artefact = nine
        config(db).assign_statement_section(artefact, SAVER_KEY, SAVER)

        with Store(db) as store:
            balances, _ = statement_balances(store)

        assert {b.account_ref for b in balances} == {SAVER}

    def test_TheLoansOpening_IsDerivedFromItsOwnClosingAnchor(self, nine):
        from obdi.verify.balance_anchors import effective_opening

        db, artefact = nine
        config(db).assign_statement_section(artefact, LOAN_KEY, LOAN)

        with Store(db) as store:
            opening = effective_opening(store, LOAN)

        assert opening.opening_minor == NINE[7][1], "what was owed when the section opened"
        assert not opening.differing


class TestAssigningDirectlyThroughTheModule:
    def test_AssignSection_WithNoAccount_NamesNothingAndRecordsNothing(self, nine):
        db, artefact = nine

        with Store(db) as store:
            outcome = assign_section(
                store,
                artefact_id=artefact,
                section_key=SAVER_KEY,
                account="  ",
                account_map=AccountMap(),
            )
            assert store.statement_section_assignments() == []

        assert "No account was named" in outcome


class TestThePage:
    @pytest.fixture
    def serve(self, db: Path):
        stops = []

        def start() -> str:
            base, stop = serve_config(config(db))
            stops.append(stop)
            return base

        yield start
        for stop in stops:
            stop()

    @pytest.mark.usefixtures("nine")
    def test_ANineAccountStatement_ListsEachAccountWithItsRowCountAndItsPicker(self, serve):
        page = httpx.get(f"{serve()}/statements", timeout=60).text

        assert "Covers several accounts (1)" in page
        assert "1 covering several accounts" in page
        assert page.count('action="/statement-section-assign"') == 9
        assert page.count('name="section"') == 9
        for label in ("Regular Saver", "Christmas Club", "Rainy Day"):
            assert label in page
        assert "reads 3 rows and its balances carry" in page
        assert "reads 1 row and its balances carry" in page
        assert "reads 0 rows and its balances carry" in page
        assert page.count('name="account"') >= 9

    def test_ALoansLabel_IsShownWithItsRateMasked(self, nine, serve):
        page = httpx.get(f"{serve()}/statements", timeout=60).text

        assert "Personal -9.99%" in page
        assert "9.50" not in page and "7.25" not in page

    def test_TheListing_ShowsNoFigureNoPayeeAndNoBalance(self, nine, serve):
        page = httpx.get(f"{serve()}/statements", timeout=60).text

        for hidden in ("J SMITH", "527.49", "52749", "342.51", "34251", "800.00", "1,200.00"):
            assert hidden not in page, hidden
        assert "DD Lodgement" not in page

    def test_ASectionThatDoesNotAddUp_IsShownRefusedWithItsMaskedReason_AndNoPicker(
        self, db, serve
    ):
        with Store(db) as store:
            keep(store, pdf(nine_accounts(penny_out=4), step=5.5), "all.pdf")

        page = httpx.get(f"{serve()}/statements", timeout=60).text

        junior = page[page.index("Junior Saver") :]
        junior = junior[: junior.index("</li>")]
        assert "refused:" in junior and "unexplained" in junior
        assert "/statement-section-assign" not in junior
        assert page.count('action="/statement-section-assign"') == 8
        assert re.search(r"-?9 minor units unexplained across 9 row", junior), junior
        assert not re.search(r"[0-8] minor units", page), "digits are masked"

    def test_AnAmbiguousDocument_IsRefusedWhole_AndOffersNoSectionControl(self, db, serve):
        lines = document(
            section("Regular Saver", 80000, [], pages=2), section("Christmas Club", 15000, [])
        )
        damaged = [line for line in lines if "Page 2 of 2" not in line]
        with Store(db) as store:
            keep(store, pdf(damaged), "damaged.pdf")

        page = httpx.get(f"{serve()}/statements", timeout=60).text

        assert "Recognised, but the reading is refused (1)" in page
        assert "page numbering" in page
        assert "/statement-section-assign" not in page
        assert "Covers several accounts" not in page

    def test_AnAssignedSection_ShowsItsAccount_AndOffersNoSecondPicker(self, nine, serve):
        db, artefact = nine
        config(db).assign_statement_section(artefact, SAVER_KEY, SAVER)

        page = httpx.get(f"{serve()}/statements", timeout=60).text

        assert "assigned to" in page and SAVER in page
        assert page.count('action="/statement-section-assign"') == 8

    def test_TheSameAccountInAnotherAllAccountsFile_HasThatAccountPreSelected(self, db, serve):
        other = document(
            section("Regular Saver", 5000, [Move("02/07/2025", "DD Lodgement", 100)]),
            section("Brand New Account", 0, []),
        )
        with Store(db) as store:
            first = keep(store, pdf(nine_accounts(), step=5.5), "first.pdf", order=0)
            keep(store, pdf(other, step=10), "second.pdf", order=1)
        wired = config(db)
        from obdi.ingest.accounts import AccountRecord, AccountRef

        with Store(db) as store:
            store.declare_account(AccountRecord(ref=AccountRef(SAVER), label="Credit union saver"))
        wired.assign_statement_section(first, SAVER_KEY, SAVER)

        page = httpx.get(f"{serve()}/statements", timeout=60).text

        second = page[page.index("second.pdf") :]
        saver_form = second[second.index("Regular Saver") :]
        saver_form = saver_form[: saver_form.index("</li>")]
        assert f'<option value="{SAVER}" selected>' in saver_form
        fresh = second[second.index("Brand New Account") :]
        assert " selected>" not in fresh[: fresh.index("</li>")], "no precedent, no suggestion"

    def test_PostingASectionAssignment_ReadsItIn_AndTheListingThenShowsIt(self, nine, serve):
        db, artefact = nine
        base = serve()

        response = httpx.post(
            f"{base}/statement-section-assign",
            data={
                "artefact": str(artefact),
                "section": section_token(SAVER_KEY),
                "account": "",
                "account_other": SAVER,
                "confirm_new_account": SAVER,
            },
            headers={"Origin": base},
            timeout=60,
        )

        assert response.status_code == 200
        assert "Read in" in response.text
        assert _assignments(db) == [(SAVER_KEY, SAVER)]
        assert total(db, SAVER) == 2500 + 249 - 30000

    def test_PostingANewAccountName_WithoutConfirmation_AsksFirst_AndAssignsNothing(
        self, nine, serve
    ):
        db, artefact = nine
        base = serve()

        response = httpx.post(
            f"{base}/statement-section-assign",
            data={
                "artefact": str(artefact),
                "section": section_token(SAVER_KEY),
                "account_other": "brand-new-saver",
            },
            headers={"Origin": base},
            timeout=60,
        )

        assert response.status_code == 409
        assert 'name="section"' in response.text, "the question carries the section back"
        assert _assignments(db) == []

    def test_PostingASectionThatDoesNotAddUp_SaysWhy_AndRecordsNothing(self, db, serve):
        with Store(db) as store:
            artefact = keep(store, pdf(nine_accounts(penny_out=4), step=5.5), "all.pdf")
        base = serve()

        response = httpx.post(
            f"{base}/statement-section-assign",
            data={
                "artefact": str(artefact),
                "section": section_token(section_key("Junior Saver")),
                "account": SAVER,
            },
            headers={"Origin": base},
            timeout=60,
        )

        assert "unexplained" in response.text
        assert _assignments(db) == []

    @pytest.mark.parametrize(
        "data",
        [
            {"section": "x", "account": "a"},
            {"artefact": "1", "account": "a"},
            {"artefact": "1", "section": "x"},
            {"artefact": "abc", "section": "x", "account": "a"},
        ],
    )
    def test_PostingWithAPartMissing_IsRefused(self, nine, serve, data):
        base = serve()

        response = httpx.post(
            f"{base}/statement-section-assign",
            data=data,
            headers={"Origin": base},
            timeout=60,
        )

        assert response.status_code == 400

    def test_PostingFromAnotherSite_IsRefused(self, nine, serve):
        db, artefact = nine

        response = httpx.post(
            f"{serve()}/statement-section-assign",
            data={"artefact": str(artefact), "section": SAVER_KEY, "account": SAVER},
            headers={"Origin": "https://elsewhere.example"},
            timeout=60,
        )

        assert response.status_code == 403
        assert _assignments(db) == []

    def test_PostingAnArtefactThatIsNotAKeptStatement_IsRefused(self, nine, serve):
        base = serve()

        response = httpx.post(
            f"{base}/statement-section-assign",
            data={"artefact": "4242", "section": SAVER_KEY, "account": SAVER},
            headers={"Origin": base},
            timeout=60,
        )

        assert response.status_code == 400
