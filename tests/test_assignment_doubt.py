"""Does a kept statement belong to the account it is being given?

Assigning a kept statement used to have one safeguard, the parser's own
arithmetic: it proves the document is internally consistent and says nothing
about whose it is. A statement of one account given to another balances just
as well, so the only thing standing between a mis-tap and a wrong ledger was
a refile-and-rebuild afterwards.

The upload preview already asks the question of a file, against what other
sources hold for the account it was sent to. These tests hold the assignment
to the same standard, from the point of view of the person tapping the button.

Everything here is invented. The statements are the ones other test modules
already build; the witness is a feed called `feedbank`, whose rows are copies
of the statement's rows (they match), or shifted by an amount no statement row
carries (they match nothing). Each scenario's answer is therefore decided
before the run: 7 rows copied is "7 of 7", 7 rows shifted is "0 of 7".
"""

from __future__ import annotations

import dataclasses
import json
from datetime import date
from pathlib import Path

import httpx
import pytest

from credit_union_documents import Move, document, pdf, section
from obdi.coverage import (
    LOW_OVERLAP_MIN_ROWS,
    LOW_OVERLAP_THRESHOLD,
    agreements,
    assignment_corroboration,
    assignment_doubt,
)
from obdi.identity import content_key
from obdi.ingest import reconcile_batch
from obdi.models import SourceTier, Transaction
from obdi.parsers.credit_union_pdf import section_key
from obdi.parsers.uk_banks import detect
from obdi.statement_sections import read_sections
from obdi.store import Store
from section_harness import (
    UNASSIGNED,
    config,
    environment,
    holdings,
    keep,
    open_flags,
    serve_config,
)
from test_coverage import txn
from test_kept_statements_page import DISTINCTIVE, SHORT_MONTH
from test_pdf_import import SANTANDER_AS_WRITTEN

WITNESS = "feedbank"
STATEMENT_SOURCE = "santander-cc-pdf"

ACCOUNT_A = "acct-a"
ACCOUNT_B = "acct-b"
ACCOUNT_C = "acct-c"

#: Added to a statement row's amount to make a feed row that matches no
#: statement row: larger than any amount the invented statements carry, so no
#: shifted amount can collide with a real one in either sign.
SHIFT = 1_000_000


@pytest.fixture
def db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    environment(monkeypatch, tmp_path)
    path = tmp_path / "store.sqlite3"
    with Store(path):
        pass
    return path


def _bind(tmp_path: Path, **provider_to_account: str) -> None:
    """Declare which accounts the witness feeds, as the account map file does."""
    bindings = [
        {"canonical_id": account, "source": WITNESS, "provider_account_id": provider}
        for provider, account in provider_to_account.items()
    ]
    (tmp_path / "accounts.json").write_text(
        json.dumps({"bindings": bindings}), encoding="utf-8"
    )


def _statement_rows(payload: bytes) -> list[Transaction]:
    return list(detect(payload).parse(payload, account_id="probe"))


def _feed(
    db: Path,
    account: str,
    rows: list[Transaction],
    *,
    copy: bool,
    only: tuple[int, ...] | None = None,
) -> None:
    """Land the witness's rows for `account`: copies of `rows`, or shifted ones."""
    landed = []
    for index, row in enumerate(rows):
        if only is not None and index not in only:
            continue
        amount = row.amount_minor if copy else row.amount_minor - SHIFT - index * 7
        landed.append(
            dataclasses.replace(
                row,
                account_id=account,
                source=WITNESS,
                source_id=f"fb-{account}-{index}",
                tier=SourceTier.AUTHORITATIVE,
                amount_minor=amount,
                content_key=content_key(
                    amount_minor=amount,
                    value_date=row.value_date,
                    description=row.description,
                ),
            )
        )
    with Store(db) as store:
        reconcile_batch(store, landed, digest=f"feed-{account}-{copy}")


def _edges(db: Path, account: str, rows: list[Transaction]) -> None:
    """Two witness rows that bracket the statement's dates and match nothing.

    An account with no witness rows over the period has no agreement to read,
    so a mis-tap into it could not be noticed; the Space of the original misfile
    held a feed row at each end of the month, which is what these stand for.
    """
    first = min(rows, key=lambda row: row.value_date)
    last = max(rows, key=lambda row: row.value_date)
    _feed(db, account, [first, last], copy=False)


def _account_of(db: Path, artefact: int) -> str:
    with Store(db) as store:
        row = store.connection.execute(
            "SELECT account_ref FROM raw_artefacts WHERE rowid = ?", (artefact,)
        ).fetchone()
    return str(row["account_ref"])


def _everything(db: Path) -> list[tuple[str, str, int, str]]:
    with Store(db) as store:
        return sorted(
            (row.account_id, row.source, row.amount_minor, row.status.value)
            for row in store.all_transactions()
        )


# --- the pure reading -------------------------------------------------------


def _file_against_witness(*, matching: int, other: int, siblings: int = 0):
    """A statement's rows (source `stmt-pdf`) beside a witness's, in `acct`.

    `matching` file rows have a copy in the witness's `acct`; `other` have
    none; `siblings` have a copy in the witness's `sib` instead (those rows
    are counted in `other` too - they are file rows nothing in `acct` holds).
    The witness also holds a row on days 1 and 28, outside the file's own
    dates, so its window is wider than the file's and the file's window rules.
    """
    rows = [txn(WITNESS, 1, -9001, account="acct", desc="edge a")]
    rows.append(txn(WITNESS, 28, -9002, account="acct", desc="edge b"))
    day = 2
    for index in range(matching + other):
        amount = -(100 + index * 13)
        rows.append(txn("stmt-pdf", day, amount, account="acct", desc=f"row {index}"))
        if index < matching:
            rows.append(txn(WITNESS, day, amount, account="acct", desc=f"row {index}"))
        elif index < matching + siblings:
            rows.append(txn(WITNESS, day, amount, account="sib", desc=f"row {index}"))
        day += 1
    return agreements(rows, sibling_accounts={WITNESS: ["acct", "sib"]})


def _doubt(found):
    return assignment_doubt(found, source="stmt-pdf", account="acct")


class TestAssignmentDoubtReadFromTheAgreements:
    def test_Constants_ArePinnedToTheValuesTheDocumentationJustifies(self):
        assert LOW_OVERLAP_MIN_ROWS == 5
        assert LOW_OVERLAP_THRESHOLD == 0.5

    def test_AStatementWhoseRowsAllMatch_RaisesNoDoubt_AndSaysSo(self):
        found = _file_against_witness(matching=6, other=0)

        assert _doubt(found) is None
        assert assignment_corroboration(found, source="stmt-pdf", account="acct") == (
            f"{WITNESS}: 6 of 6 rows match over 2026-01-02 to 2026-01-07"
        )

    def test_AStatementWithFewMatches_IsDoubted_WithTheCountsAPersonSees(self):
        found = _file_against_witness(matching=2, other=4)

        assert _doubt(found) == (
            "only 2 of the statement's 6 rows between 2026-01-02 and 2026-01-07 "
            f"match what {WITNESS} holds for acct; this is probably another "
            "account's statement"
        )

    def test_AStatementWithExactlyHalfMatching_IsNotDoubted(self):
        """Half is not "below half": the boundary is pinned here so a change
        to the comparison is a decision rather than an accident."""
        found = _file_against_witness(matching=3, other=3)

        assert _doubt(found) is None

    def test_AStatementJustBelowHalfMatching_IsDoubted(self):
        found = _file_against_witness(matching=3, other=4)

        assert _doubt(found) is not None

    @pytest.mark.parametrize("rows", [1, 2, 3, 4])
    def test_FewerThanFiveRowsInTheWindow_AreNeverDoubtedOnOverlapAlone(self, rows):
        found = _file_against_witness(matching=0, other=rows)

        assert _doubt(found) is None

    def test_ExactlyFiveRowsInTheWindow_AreEnoughToDoubt(self):
        found = _file_against_witness(matching=0, other=5)

        assert _doubt(found) is not None

    def test_WhenNoWitnessCoversThePeriod_NothingIsDoubted_AndItIsSaidUncorroborated(self):
        rows = [txn("stmt-pdf", day, -100 - day, account="acct") for day in range(2, 9)]
        found = agreements(rows, sibling_accounts={})

        assert _doubt(found) is None
        assert assignment_corroboration(found, source="stmt-pdf", account="acct") == (
            "no other source covers this period; the statement stands uncorroborated"
        )

    def test_AWitnessOnlyForAnotherAccount_IsNoWitnessForThisOne(self):
        rows = [txn("stmt-pdf", day, -100 - day, account="acct") for day in range(2, 9)]
        rows += [txn(WITNESS, day, -100 - day, account="elsewhere") for day in range(2, 9)]
        found = agreements(rows, sibling_accounts={})

        assert _doubt(found) is None

    def test_RowsMatchedToASiblingAccount_CountAsExplained_NotAsMatched(self):
        """Six rows, one in place and two filed by the witness under a sibling:
        three of six are "explained" but only one MATCHES, and one of six is
        below half. Counting the explained rows as matched would make it half,
        and let a mostly-foreign statement through."""
        found = _file_against_witness(matching=1, other=5, siblings=2)

        assert "only 1 of the statement's 6 rows" in (_doubt(found) or "")

    def test_AStatementMostlyHeldUnderASibling_IsDoubtedAsAWrongDestination(self):
        found = _file_against_witness(matching=1, other=5, siblings=4)

        doubt = _doubt(found)

        assert doubt is not None
        assert "OTHER accounts" in doubt
        assert "sib" in doubt

    def test_TheStatementOnEitherSideOfThePair_IsReadTheSame(self):
        rows = [
            txn("aaa-stmt", day, -100 - day, account="acct", desc=f"r{day}")
            for day in range(2, 8)
        ]
        rows += [
            txn("zzz-feed", 1, -9001, account="acct"),
            txn("zzz-feed", 28, -9002, account="acct"),
        ]
        found = agreements(rows, sibling_accounts={})

        assert "only 0 of the statement's 6 rows" in (
            assignment_doubt(found, source="aaa-stmt", account="acct") or ""
        )

    def test_TheWitnessWithTheWorstShare_IsTheOneNamed(self):
        days = range(2, 8)
        rows = [txn("stmt-pdf", d, -100 - d, account="acct", desc=f"r{d}") for d in days]
        rows += [txn("good", d, -100 - d, account="acct", desc=f"r{d}") for d in days]
        rows += [txn("bad", 1, -9001, account="acct"), txn("bad", 28, -9002, account="acct")]
        found = agreements(rows, sibling_accounts={})

        doubt = assignment_doubt(found, source="stmt-pdf", account="acct")

        assert doubt is not None and "what bad holds" in doubt

    def test_ADateOutsideTheSharedWindow_DoesNotCountAgainstTheStatement(self):
        """The witness holds rows on days 1 and 28 that the statement does not
        reach; they are outside the window and must not read as unmatched."""
        found = _file_against_witness(matching=6, other=0)

        assert [item.overlap_to for item in found] == [date(2026, 1, 7)]
        assert _doubt(found) is None


# --- the kept statement, whole ----------------------------------------------


def _wired_with_statement(db: Path, payload: bytes = SANTANDER_AS_WRITTEN):
    with Store(db) as store:
        artefact = keep(store, payload, "statement.pdf")
    return config(db), artefact


class TestAssigningAKeptStatement:
    def test_AStatementTheFeedAlreadyHoldsForTheAccount_IsReadIn_NamingTheWitness(
        self, db
    ):
        rows = _statement_rows(SANTANDER_AS_WRITTEN)
        _feed(db, ACCOUNT_A, rows, copy=True)
        wired, artefact = _wired_with_statement(db)

        outcome = wired.assign_kept_statement(artefact, ACCOUNT_A)

        assert f"assigned to {ACCOUNT_A} and read by {STATEMENT_SOURCE}" in outcome
        assert outcome.endswith(f"{WITNESS}: 7 of 7 rows match over 2026-06-29 to 2026-07-11")
        assert _account_of(db, artefact) == ACCOUNT_A

    def test_AStatementGivenToASiblingOfTheAccountTheFeedFiledItUnder_IsRefused_AndNothingChanges(
        self, db, tmp_path
    ):
        _bind(tmp_path, pa=ACCOUNT_A, pb=ACCOUNT_B)
        rows = _statement_rows(SANTANDER_AS_WRITTEN)
        _feed(db, ACCOUNT_A, rows, copy=True)
        _edges(db, ACCOUNT_B, rows)
        wired, artefact = _wired_with_statement(db)
        before = (_everything(db), open_flags(db), holdings(db, ACCOUNT_B))

        outcome = wired.assign_kept_statement(artefact, ACCOUNT_B)

        assert outcome.startswith("Not assigned: 7 of 7 of this file's rows match rows")
        assert f"{WITNESS} filed under OTHER accounts: {ACCOUNT_A} (7)" in outcome
        assert outcome.endswith(" Nothing was read in; choose the account again.")
        assert _account_of(db, artefact) == UNASSIGNED
        assert (_everything(db), open_flags(db), holdings(db, ACCOUNT_B)) == before

    def test_AStatementGivenToAnAccountWhoseFeedRowsAllDiffer_IsRefused_WithTheCounts(
        self, db
    ):
        rows = _statement_rows(SANTANDER_AS_WRITTEN)
        _feed(db, ACCOUNT_C, rows, copy=False)
        wired, artefact = _wired_with_statement(db)
        before = (_everything(db), open_flags(db))

        outcome = wired.assign_kept_statement(artefact, ACCOUNT_C)

        assert outcome == (
            "Not assigned: only 0 of the statement's 7 rows between 2026-06-29 and "
            f"2026-07-11 match what {WITNESS} holds for {ACCOUNT_C}; this is probably "
            "another account's statement. Nothing was read in; choose the account again."
        )
        assert _account_of(db, artefact) == UNASSIGNED
        assert (_everything(db), open_flags(db)) == before

    def test_AStatementForAnAccountNothingElseCovers_IsReadIn_Uncorroborated(self, db):
        wired, artefact = _wired_with_statement(db)

        outcome = wired.assign_kept_statement(artefact, "acct-new")

        assert "assigned to acct-new and read by" in outcome
        assert outcome.endswith(
            "no other source covers this period; the statement stands uncorroborated"
        )
        assert _account_of(db, artefact) == "acct-new"

    def test_AStatementOfFewerThanFiveRows_IsNotRefusedForOverlapAlone(self, db):
        short = _statement_rows(SHORT_MONTH)
        assert len(short) < LOW_OVERLAP_MIN_ROWS
        _feed(db, ACCOUNT_C, short, copy=False)
        wired, artefact = _wired_with_statement(db, SHORT_MONTH)

        outcome = wired.assign_kept_statement(artefact, ACCOUNT_C)

        assert "assigned to acct-c" in outcome
        assert outcome.endswith(f"{WITNESS}: 0 of 2 rows match over 2026-06-02 to 2026-06-04")

    def test_AfterARefusal_TheSameStatementCanBeGivenToTheRightAccount(self, db):
        rows = _statement_rows(SANTANDER_AS_WRITTEN)
        _feed(db, ACCOUNT_A, rows, copy=True)
        _feed(db, ACCOUNT_C, rows, copy=False)
        wired, artefact = _wired_with_statement(db)

        refused = wired.assign_kept_statement(artefact, ACCOUNT_C)
        accepted = wired.assign_kept_statement(artefact, ACCOUNT_A)

        assert refused.startswith("Not assigned:")
        assert f"assigned to {ACCOUNT_A} and read by" in accepted
        assert _account_of(db, artefact) == ACCOUNT_A

    def test_AReUploadedMonthOfTheSameSource_IsNotComparedWithItself(self, db):
        """A statement of the same source already read into the account must
        not stand in as the witness for the next one, or every month would be
        "corroborated" by its neighbour and none by an independent source."""
        with Store(db) as store:
            first = keep(store, SANTANDER_AS_WRITTEN, "first.pdf")
        wired = config(db)
        wired.assign_kept_statement(first, ACCOUNT_A)
        with Store(db) as store:
            second = keep(store, DISTINCTIVE, "second.pdf")

        outcome = wired.assign_kept_statement(second, ACCOUNT_A)

        assert outcome.endswith("the statement stands uncorroborated")


class TestTheBulkRouteWithOneRefusedAmongThree:
    @pytest.fixture
    def three(self, db: Path) -> dict[str, int]:
        _feed(db, ACCOUNT_C, _statement_rows(SANTANDER_AS_WRITTEN), copy=False)
        with Store(db) as store:
            return {
                "2026.03 - Short.pdf": keep(store, SHORT_MONTH, "2026.03 - Short.pdf", order=1),
                "2026.05 - Full.pdf": keep(
                    store, SANTANDER_AS_WRITTEN, "2026.05 - Full.pdf", order=2
                ),
                "2026.08 - Distinctive.pdf": keep(
                    store, DISTINCTIVE, "2026.08 - Distinctive.pdf", order=3
                ),
            }

    def test_TheOthersAreReadIn_TheRefusedOneStaysWaiting_AndEachResultIsListed(
        self, db, three
    ):
        base, stop = serve_config(config(db))
        try:
            response = httpx.post(
                f"{base}/statements-assign",
                data={
                    "artefacts": ",".join(str(i) for i in three.values()),
                    "account": ACCOUNT_C,
                },
                headers={"Origin": base},
                timeout=60,
            )
        finally:
            stop()

        page = response.text
        assert response.status_code == 200
        assert "2 read in, 1 refused" in page
        assert "Not assigned: only 0 of the" in page
        assert "7 rows between 2026-06-29 and 2026-07-11" in page
        assert page.index("2026.03") < page.index("2026.05") < page.index("2026.08")
        assert _account_of(db, three["2026.05 - Full.pdf"]) == UNASSIGNED
        assert _account_of(db, three["2026.03 - Short.pdf"]) == ACCOUNT_C
        assert _account_of(db, three["2026.08 - Distinctive.pdf"]) == ACCOUNT_C

    def test_TheResultPage_CarriesCountsAndDates_ButNoAmountOrPayee(self, db, three):
        base, stop = serve_config(config(db))
        try:
            page = httpx.post(
                f"{base}/statements-assign",
                data={
                    "artefacts": ",".join(str(i) for i in three.values()),
                    "account": ACCOUNT_C,
                },
                headers={"Origin": base},
                timeout=60,
            ).text
        finally:
            stop()

        for hidden in ("ZEBRAQUARTZ", "7,654.32", "EXAMPLE SHOP", "Some Merchant"):
            assert hidden not in page, hidden


class TestTheSingleRouteShowsARefusalAsOne:
    def test_ARefusedStatement_IsShownAsNotReadIn_NotAsReadIn(self, db):
        _feed(db, ACCOUNT_C, _statement_rows(SANTANDER_AS_WRITTEN), copy=False)
        wired, artefact = _wired_with_statement(db)
        base, stop = serve_config(wired)
        try:
            page = httpx.post(
                f"{base}/statement-assign",
                data={"artefact": str(artefact), "account": ACCOUNT_C},
                headers={"Origin": base},
                timeout=60,
            ).text
        finally:
            stop()

        assert "<h2>Not read in</h2>" in page
        assert "Not assigned: only 0 of the" in page
        assert "<h2>Read in</h2>" not in page
        assert _account_of(db, artefact) == UNASSIGNED


# --- a section of an "all accounts" statement --------------------------------

SAVER = "credit-union-saver"
OTHER_SAVER = "credit-union-other-saver"
SAVER_KEY = section_key("Regular Saver")

SIX_MOVES = [
    Move("04/05/2025", "DD Lodgement", 2500),
    Move("05/05/2025", "Standing Order", -300, "A PAYEE"),
    Move("06/05/2025", "Transfer In", 1100),
    Move("07/05/2025", "Cash Deposit", 700),
    Move("08/05/2025", "Withdrawal", -450),
    Move("09/05/2025", "DD Lodgement", 900),
]


def _two_accounts() -> bytes:
    return pdf(
        document(
            section("Regular Saver", 80000, SIX_MOVES),
            section("Christmas Club", 15000, []),
        ),
        step=5.5,
    )


def _section_rows(payload: bytes) -> list[Transaction]:
    read = read_sections(payload)
    assert read is not None
    parser, _ = read
    return list(parser.parse_section(payload, SAVER_KEY, account_id="probe"))


def _assignments(db: Path) -> list[tuple[str, str]]:
    with Store(db) as store:
        return [(a.section_key, a.account_ref) for a in store.statement_section_assignments()]


class TestAssigningASectionOfAnAllAccountsStatement:
    @pytest.fixture
    def kept(self, db: Path) -> tuple[bytes, int]:
        payload = _two_accounts()
        with Store(db) as store:
            return payload, keep(store, payload, "all accounts.pdf")

    def test_ASectionTheFeedAlreadyHolds_IsReadIn_NamingTheWitness(self, db, kept):
        payload, artefact = kept
        _feed(db, SAVER, _section_rows(payload), copy=True)

        outcome = config(db).assign_statement_section(artefact, SAVER_KEY, SAVER)

        assert f"assigned to {SAVER} and read by" in outcome
        assert outcome.endswith(f"{WITNESS}: 6 of 6 rows match over 2025-05-04 to 2025-05-09")
        assert _assignments(db) == [(SAVER_KEY, SAVER)]

    def test_ASectionGivenToASiblingOfTheAccountTheFeedFiledItUnder_IsRefused_AndRecordsNothing(
        self, db, kept, tmp_path
    ):
        payload, artefact = kept
        rows = _section_rows(payload)
        _bind(tmp_path, pa=SAVER, pb=OTHER_SAVER)
        _feed(db, SAVER, rows, copy=True)
        _edges(db, OTHER_SAVER, rows)
        before = (_everything(db), open_flags(db), holdings(db, OTHER_SAVER))

        outcome = config(db).assign_statement_section(artefact, SAVER_KEY, OTHER_SAVER)

        assert outcome.startswith("Not assigned: 6 of 6 of this file's rows match rows")
        assert f"filed under OTHER accounts: {SAVER} (6)" in outcome
        assert outcome.endswith(" Nothing was read in; choose the account again.")
        assert _assignments(db) == []
        assert (_everything(db), open_flags(db), holdings(db, OTHER_SAVER)) == before

    def test_ASectionGivenToAnAccountWhoseFeedRowsAllDiffer_IsRefused_WithTheCounts(
        self, db, kept
    ):
        payload, artefact = kept
        _feed(db, SAVER, _section_rows(payload), copy=False)
        before = _everything(db)

        outcome = config(db).assign_statement_section(artefact, SAVER_KEY, SAVER)

        assert outcome == (
            "Not assigned: only 0 of the statement's 6 rows between 2025-05-04 and "
            f"2025-05-09 match what {WITNESS} holds for {SAVER}; this is probably "
            "another account's statement. Nothing was read in; choose the account again."
        )
        assert _assignments(db) == []
        assert _everything(db) == before

    def test_ASectionForAnAccountNothingElseCovers_IsReadIn_Uncorroborated(self, db, kept):
        _, artefact = kept

        outcome = config(db).assign_statement_section(artefact, SAVER_KEY, SAVER)

        assert outcome.endswith(
            "no other source covers this period; the statement stands uncorroborated"
        )
        assert _assignments(db) == [(SAVER_KEY, SAVER)]

    def test_AfterARefusal_TheSameSectionCanBeGivenToTheRightAccount(self, db, kept):
        payload, artefact = kept
        rows = _section_rows(payload)
        _feed(db, SAVER, rows, copy=True)
        _feed(db, OTHER_SAVER, rows, copy=False)
        wired = config(db)

        refused = wired.assign_statement_section(artefact, SAVER_KEY, OTHER_SAVER)
        accepted = wired.assign_statement_section(artefact, SAVER_KEY, SAVER)

        assert refused.startswith("Not assigned:")
        assert f"assigned to {SAVER} and read by" in accepted
        assert _assignments(db) == [(SAVER_KEY, SAVER)]
