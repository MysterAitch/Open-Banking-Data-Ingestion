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
import html
import json
import re
import threading
from datetime import date, timedelta
from pathlib import Path

import httpx
import pytest

from credit_union_documents import Move, document, pdf, section
from obdi.core.models import SourceTier, Transaction
from obdi.ingest.connections import ConnectionStore
from obdi.ingest.identity import content_key
from obdi.ingest.parsers.credit_union_pdf import section_key
from obdi.ingest.parsers.uk_banks import detect
from obdi.ingest.pipeline import ImportSummary, preview_reconcile, reconcile_batch
from obdi.ingest.store import Store
from obdi.pages.web import AuthorisationSession, ConnectionHandler, WebConfig
from obdi.verify.coverage import (
    LOW_OVERLAP_MIN_ROWS,
    LOW_OVERLAP_THRESHOLD,
    MATCHER_AGREES_THRESHOLD,
    DoubtReport,
    agreements,
    assignment_corroboration,
    assignment_doubt,
)
from obdi.verify.statement_sections import read_sections
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
from test_pdf_import import SANTANDER_AS_WRITTEN, SANTANDER_PLAIN

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
    lag: int = 0,
    relabel: bool = False,
    tag: str = "",
) -> None:
    """Land the witness's rows for `account`: copies of `rows`, or shifted ones.

    `lag` dates each copy that many days after the statement's row, and
    `relabel` words it differently: together they are a feed that posts a card
    purchase days after the statement dates it, under the merchant's own name.
    The agreements pair such a row only within two days or on an identical
    description; the matcher pairs on amount within a week. `tag` keeps the
    provider ids of a second landing apart from the first's: the same id is
    the same row to the matcher.
    """
    landed = []
    for index, row in enumerate(rows):
        if only is not None and index not in only:
            continue
        amount = row.amount_minor if copy else row.amount_minor - SHIFT - index * 7
        dated = row.value_date + timedelta(days=lag)
        words = f"CARD PURCHASE {index}" if relabel else row.description
        landed.append(
            dataclasses.replace(
                row,
                description=words,
                account_id=account,
                source=WITNESS,
                source_id=f"fb-{account}-{tag}{index}",
                tier=SourceTier.AUTHORITATIVE,
                amount_minor=amount,
                value_date=dated,
                booking_date=row.booking_date + timedelta(days=lag),
                content_key=content_key(
                    amount_minor=amount,
                    value_date=dated,
                    description=words,
                ),
            )
        )
    with Store(db) as store:
        reconcile_batch(store, landed, digest=f"feed-{account}-{copy}-{tag}")


def _edges(db: Path, account: str, rows: list[Transaction]) -> None:
    """Two witness rows that bracket the statement's dates and match nothing.

    An account with no witness rows over the period has no agreement to read,
    so a mis-tap into it could not be noticed; the Space of the original misfile
    held a feed row at each end of the month, which is what these stand for.
    """
    first = min(rows, key=lambda row: row.value_date)
    last = max(rows, key=lambda row: row.value_date)
    _feed(db, account, [first, last], copy=False, tag="edge")


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
        assert MATCHER_AGREES_THRESHOLD == 0.8

    def test_ALowOverlapTheMatcherAgreesWith_IsNotDoubted(self):
        found = _file_against_witness(matching=1, other=5)

        assert _doubt(found) is not None
        assert (
            assignment_doubt(
                found, source="stmt-pdf", account="acct", matcher_agrees=lambda: True
            )
            is None
        )

    def test_ALowOverlapTheMatcherDoesNotAgreeWith_IsStillDoubted(self):
        found = _file_against_witness(matching=1, other=5)

        assert (
            assignment_doubt(
                found, source="stmt-pdf", account="acct", matcher_agrees=lambda: False
            )
            is not None
        )

    def test_TheMatcherIsNotAskedWhenNothingIsDoubtedOnOverlap(self):
        """Asking is a dry run over the account's history; a statement that
        corroborates plainly must not pay for one."""

        def asked() -> bool:
            raise AssertionError("the matcher was asked although nothing was in doubt")

        found = _file_against_witness(matching=6, other=0)

        assert (
            assignment_doubt(found, source="stmt-pdf", account="acct", matcher_agrees=asked)
            is None
        )

    def test_AWrongDestinationDoubt_IsNotWithdrawnByTheMatcherAgreeing(self):
        found = _file_against_witness(matching=1, other=5, siblings=4)

        doubt = assignment_doubt(
            found, source="stmt-pdf", account="acct", matcher_agrees=lambda: True
        )

        assert doubt is not None and "OTHER accounts" in doubt

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


class TestTheBulkRouteWithOneDoubtedAmongThree:
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

    def test_TheOthersAreReadIn_TheDoubtedOneStaysWaiting_AndEachResultIsListed(
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
        assert "2 read in, 0 refused, 1 needing confirmation" in page
        assert "Needs confirming, and was not read in: only 0 of the" in page
        assert "7 rows between 2026-06-29 and 2026-07-11" in page
        assert "The matcher would merge 0 of 7 rows onto rows this account already holds" in page
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


    def test_TheDoubtedStatementsOwnForm_ReadsItInOnceConfirmed(self, db, three):
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
            action, fields = _form_with(page, "read it in anyway")
            assert action == "/statement-assign"
            assert fields["artefact"] == str(three["2026.05 - Full.pdf"])
            followed = httpx.post(
                f"{base}{action}", data=fields, headers={"Origin": base}, timeout=60
            )
        finally:
            stop()

        assert "<h2>Read in</h2>" in followed.text
        assert "assigned over a stated doubt: only 0 of the statement's 7 rows" in html.unescape(
            followed.text
        )
        assert _account_of(db, three["2026.05 - Full.pdf"]) == ACCOUNT_C


def _form_with(page: str, label: str) -> tuple[str, dict[str, str]]:
    """The action and hidden fields of the form whose button says `label`."""
    for form in re.findall(r"<form .*?</form>", page, flags=re.DOTALL):
        if label in html.unescape(form):
            action = re.search(r'action="([^"]*)"', form)
            assert action is not None
            fields = {
                html.unescape(name): html.unescape(value)
                for name, value in re.findall(
                    r'<input type="hidden" name="([^"]*)" value="([^"]*)"', form
                )
            }
            return html.unescape(action.group(1)), fields
    raise AssertionError(f"no form labelled {label!r} in the page")


def _dump(db: Path) -> str:
    """Every row of every table, so 'nothing was written' is a comparison."""
    with Store(db) as store:
        return "\n".join(store.connection.iterdump())


ANYWAY = "read it in anyway"


class TestTheSingleRouteAsksBeforeReadingInADoubtedStatement:
    @pytest.fixture
    def doubted(self, db):
        _feed(db, ACCOUNT_C, _statement_rows(SANTANDER_AS_WRITTEN), copy=False)
        return _wired_with_statement(db)

    def test_ADoubtedStatement_IsAskedAbout_AndNothingIsReadIn(self, db, doubted):
        wired, artefact = doubted
        before = _dump(db)
        base, stop = serve_config(wired)
        try:
            response = httpx.post(
                f"{base}/statement-assign",
                data={"artefact": str(artefact), "account": ACCOUNT_C},
                headers={"Origin": base},
                timeout=60,
            )
        finally:
            stop()

        page = response.text
        assert response.status_code == 409
        assert "Is this the right account?" in page
        assert (
            "only 0 of the statement&#x27;s 7 rows between 2026-06-29 and 2026-07-11 "
            f"match what {WITNESS} holds for {ACCOUNT_C}" in page
        )
        assert page.index("only 0 of the") < page.index("The matcher would merge 0 of 7 rows")
        assert (
            "The matcher would merge 0 of 7 rows onto rows this account already "
            "holds and add 7 new." in page
        )
        assert "<h2>Read in</h2>" not in page
        assert _account_of(db, artefact) == UNASSIGNED
        assert _dump(db) == before

    def test_TheConfirmationPage_OffersBackAsThePrimaryControl_AndAPostToProceed(
        self, db, doubted
    ):
        wired, artefact = doubted
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

        assert '<a class="button" href="/statements">Back to kept statements</a>' in page
        assert "It is this account&#x27;s - read it in anyway" in page
        action, fields = _form_with(page, ANYWAY)
        assert action == "/statement-assign"
        assert fields["artefact"] == str(artefact)
        assert fields["account"] == ACCOUNT_C
        assert "doubt_acknowledged" in fields
        links = re.findall(r'href="([^"]*)"', page)
        assert not [link for link in links if "assign" in link]
        assert page.index("Back to kept statements") < page.index(
            '<form method="post" action="/statement-assign">'
        )

    def test_AcknowledgingTheDoubt_ReadsTheStatementIn_AndTheSentenceQuotesIt(
        self, db, doubted
    ):
        wired, artefact = doubted
        base, stop = serve_config(wired)
        try:
            asked = httpx.post(
                f"{base}/statement-assign",
                data={"artefact": str(artefact), "account": ACCOUNT_C},
                headers={"Origin": base},
                timeout=60,
            ).text
            action, fields = _form_with(asked, ANYWAY)
            response = httpx.post(
                f"{base}{action}", data=fields, headers={"Origin": base}, timeout=60
            )
        finally:
            stop()

        page = response.text
        assert response.status_code == 200
        assert "<h2>Read in</h2>" in page
        assert f"assigned to {ACCOUNT_C} and read by {STATEMENT_SOURCE}" in page
        assert "assigned over a stated doubt: only 0 of the" in page
        assert _account_of(db, artefact) == ACCOUNT_C

    def test_AnAcknowledgementMadeForAnotherStatement_DoesNotReadThisOneIn(self, db):
        # Two kept documents with the same rows, so both are doubted alike and
        # only the acknowledgement's own artefact tells them apart.
        _feed(db, ACCOUNT_C, _statement_rows(SANTANDER_AS_WRITTEN), copy=False)
        with Store(db) as store:
            first = keep(store, SANTANDER_AS_WRITTEN, "first.pdf")
            second = keep(store, SANTANDER_PLAIN, "second.pdf")
        base, stop = serve_config(config(db))
        try:
            asked = httpx.post(
                f"{base}/statement-assign",
                data={"artefact": str(first), "account": ACCOUNT_C},
                headers={"Origin": base},
                timeout=60,
            ).text
            _, fields = _form_with(asked, ANYWAY)
            replayed = httpx.post(
                f"{base}/statement-assign",
                data={**fields, "artefact": str(second)},
                headers={"Origin": base},
                timeout=60,
            )
        finally:
            stop()

        assert replayed.status_code == 409
        assert "Is this the right account?" in replayed.text
        assert _account_of(db, second) == UNASSIGNED
        assert _account_of(db, first) == UNASSIGNED

    def test_AnAcknowledgementMadeForAnotherAccount_DoesNotReadItIntoThisOne(self, db):
        rows = _statement_rows(SANTANDER_AS_WRITTEN)
        _feed(db, ACCOUNT_C, rows, copy=False)
        _feed(db, "acct-d", rows, copy=False)
        wired, artefact = _wired_with_statement(db)
        base, stop = serve_config(wired)
        try:
            asked = httpx.post(
                f"{base}/statement-assign",
                data={"artefact": str(artefact), "account": ACCOUNT_C},
                headers={"Origin": base},
                timeout=60,
            ).text
            _, fields = _form_with(asked, ANYWAY)
            replayed = httpx.post(
                f"{base}/statement-assign",
                data={**fields, "account": "acct-d"},
                headers={"Origin": base},
                timeout=60,
            )
        finally:
            stop()

        assert replayed.status_code == 409
        assert _account_of(db, artefact) == UNASSIGNED

    def test_ThePage_CarriesCountsAndDates_ButNoAmountOrPayee(self, db, doubted):
        wired, artefact = doubted
        base, stop = serve_config(wired)
        try:
            asked = httpx.post(
                f"{base}/statement-assign",
                data={"artefact": str(artefact), "account": ACCOUNT_C},
                headers={"Origin": base},
                timeout=60,
            ).text
            action, fields = _form_with(asked, ANYWAY)
            read_in = httpx.post(
                f"{base}{action}", data=fields, headers={"Origin": base}, timeout=60
            ).text
        finally:
            stop()

        for page in (asked, read_in):
            for hidden in ("ZEBRAQUARTZ", "7,654.32", "EXAMPLE SHOP", "Some Merchant"):
                assert hidden not in page, hidden

    def test_ACrossSitePost_IsRefused_AndReadsNothingIn(self, db, doubted):
        wired, artefact = doubted
        base, stop = serve_config(wired)
        try:
            asked = httpx.post(
                f"{base}/statement-assign",
                data={"artefact": str(artefact), "account": ACCOUNT_C},
                headers={"Origin": base},
                timeout=60,
            ).text
            _, fields = _form_with(asked, ANYWAY)
            forged = httpx.post(
                f"{base}/statement-assign",
                data=fields,
                headers={"Origin": "https://evil.example"},
                timeout=60,
            )
        finally:
            stop()

        assert forged.status_code == 403
        assert _account_of(db, artefact) == UNASSIGNED


class TestAStatementTheMatcherWouldMergeInFull:
    """The measured case: the agreements pair rows within two days and a card
    statement dates a purchase days before the feed posts it, so every row
    merges by the matcher while the strict comparison matches almost none."""

    LAG = 4

    @pytest.fixture
    def lagging(self, db):
        rows = _statement_rows(SANTANDER_AS_WRITTEN)
        _feed(db, ACCOUNT_A, rows, copy=True, lag=self.LAG, relabel=True)
        _edges(db, ACCOUNT_A, rows)
        return _wired_with_statement(db), rows

    def test_TheStrictComparisonAlone_WouldDoubtIt(self, db, lagging):
        """The premise: without the matcher's word this statement is doubted,
        so the tests below prove the matcher is what lets it through."""
        _, rows = lagging
        incoming = [dataclasses.replace(row, account_id=ACCOUNT_A) for row in rows]
        with Store(db) as store:
            found = agreements(store.transactions_by_sighting() + incoming, sibling_accounts={})

        assert "only " in (
            assignment_doubt(found, source=STATEMENT_SOURCE, account=ACCOUNT_A) or ""
        )

    def test_ItIsReadIn_WithoutAnyPrompt_BecauseTheMatcherAgrees(self, db, lagging):
        (wired, artefact), _ = lagging

        outcome = wired.assign_kept_statement(artefact, ACCOUNT_A)

        assert f"assigned to {ACCOUNT_A} and read by {STATEMENT_SOURCE}" in outcome
        assert "new 0, matched 7" in outcome
        assert "the matcher would merge 7 of 7 rows onto rows this account already holds" in outcome
        assert "doubt" not in outcome
        assert _account_of(db, artefact) == ACCOUNT_A

    def test_TheRouteReadsItInStraightAway(self, db, lagging):
        (wired, artefact), _ = lagging
        base, stop = serve_config(wired)
        try:
            response = httpx.post(
                f"{base}/statement-assign",
                data={"artefact": str(artefact), "account": ACCOUNT_A},
                headers={"Origin": base},
                timeout=60,
            )
        finally:
            stop()

        assert response.status_code == 200
        assert "<h2>Read in</h2>" in response.text
        assert _account_of(db, artefact) == ACCOUNT_A

    @pytest.mark.parametrize(
        ("merging", "prompted"),
        [(7, False), (6, False), (5, True), (3, True), (0, True)],
    )
    def test_TheMatcherAgreeingOnAtLeastFourFifths_IsWhatDecidesWhetherToAsk(
        self, db, merging, prompted
    ):
        """6 of 7 is 86% and goes through; 5 of 7 is 71% and asks."""
        rows = _statement_rows(SANTANDER_AS_WRITTEN)
        _feed(
            db,
            ACCOUNT_A,
            rows,
            copy=True,
            lag=self.LAG,
            relabel=True,
            only=tuple(range(merging)),
        )
        _edges(db, ACCOUNT_A, rows)
        wired, artefact = _wired_with_statement(db)

        report = wired.review_kept_statement(artefact, ACCOUNT_A)

        if prompted:
            assert report is not None
            assert f"The matcher would merge {merging} of 7 rows" in report.evidence
            assert f"add {7 - merging} new" in report.evidence
        else:
            assert report is None


class TestTheMatcherDryRun:
    def test_ItCountsWhatTheRealBatchThenDoes_AndWritesNothing(self, db):
        rows = _statement_rows(SANTANDER_AS_WRITTEN)
        _feed(db, ACCOUNT_A, rows, copy=True, lag=4, relabel=True, only=(0, 1, 2))
        incoming = [dataclasses.replace(row, account_id=ACCOUNT_A) for row in rows]
        before = _dump(db)

        with Store(db) as store:
            preview = preview_reconcile(store, incoming)

        assert (preview.merged, preview.new) == (3, 4)
        assert _dump(db) == before
        with Store(db) as store:
            real = reconcile_batch(
                store, incoming, digest="real", summary=ImportSummary(artefact_new=False)
            )
        assert (real.matched + real.superseded, real.inserted) == (3, 4)

    def test_TheReviewHook_LeavesStoreSightingsFlagsAndFilingUntouched(self, db):
        _feed(db, ACCOUNT_C, _statement_rows(SANTANDER_AS_WRITTEN), copy=False)
        wired, artefact = _wired_with_statement(db)
        before = _dump(db)
        flags = open_flags(db)

        report = wired.review_kept_statement(artefact, ACCOUNT_C)

        assert isinstance(report, DoubtReport)
        assert _dump(db) == before
        assert open_flags(db) == flags
        assert _account_of(db, artefact) == UNASSIGNED

    def test_TwoIdenticalRowsInABatch_CannotBothMergeOntoOneHeldRow(self, db):
        """A held row answers one incoming row: the second of two identical
        payments is a new row, exactly as in the real batch."""
        row = _statement_rows(SANTANDER_AS_WRITTEN)[0]
        _feed(db, ACCOUNT_A, [row], copy=True)
        twice = [dataclasses.replace(row, account_id=ACCOUNT_A)] * 2

        with Store(db) as store:
            preview = preview_reconcile(store, twice)

        assert (preview.merged, preview.new) == (1, 1)

    def test_AnEmptyAccount_MergesNothing(self, db):
        rows = _statement_rows(SANTANDER_AS_WRITTEN)
        incoming = [dataclasses.replace(row, account_id="acct-empty") for row in rows]

        with Store(db) as store:
            preview = preview_reconcile(store, incoming)

        assert (preview.merged, preview.new) == (0, 7)

    def test_RowsRepeatedWithinABatch_AreSeparatePayments_NotOneSeenTwice(self, db):
        """Rows of one batch see each other, as in the real loop: a repeat of
        the same content from the same source is a second payment."""
        rows = [
            dataclasses.replace(row, account_id=ACCOUNT_A)
            for row in _statement_rows(SANTANDER_AS_WRITTEN)
        ]

        with Store(db) as store:
            preview = preview_reconcile(store, rows + rows[:2])

        assert (preview.merged, preview.new) == (0, 9)


class TestADestinationDoubtSurvivesTheMatcherAgreeing:
    def test_RowsHeldUnderASibling_StillPromptEvenIfTheMatcherWouldMergeThemAll(
        self, db, tmp_path
    ):
        _bind(tmp_path, pa=ACCOUNT_A, pb=ACCOUNT_B)
        rows = _statement_rows(SANTANDER_AS_WRITTEN)
        _feed(db, ACCOUNT_A, rows, copy=True)
        _feed(db, ACCOUNT_B, rows, copy=True, lag=4, relabel=True)
        _edges(db, ACCOUNT_B, rows)
        wired, artefact = _wired_with_statement(db)
        base, stop = serve_config(wired)
        try:
            response = httpx.post(
                f"{base}/statement-assign",
                data={"artefact": str(artefact), "account": ACCOUNT_B},
                headers={"Origin": base},
                timeout=60,
            )
        finally:
            stop()

        page = response.text
        assert response.status_code == 409
        assert f"filed under OTHER accounts: {ACCOUNT_A} (7)" in page
        assert "The matcher would merge 7 of 7 rows onto rows this account already holds" in page
        assert _account_of(db, artefact) == UNASSIGNED


# --- the two confirmations together ------------------------------------------


class _Calls:
    def __init__(self) -> None:
        self.declared: list[str] = []
        self.assigned: list[tuple[int, str, bool]] = []


def _stub_server(tmp_path: Path, calls: _Calls, *, doubt: bool):
    """A handler whose assignment is always doubted (or never), over a registry
    that knows no account, so every typed name needs its own confirmation."""

    def declare(record):
        calls.declared.append(str(record.ref))
        return record

    def assign(artefact_id: int, account_id: str, *, doubt_acknowledged: bool = False) -> str:
        calls.assigned.append((artefact_id, account_id, doubt_acknowledged))
        return f"assigned to {account_id} and read by stub"

    def review(artefact_id: int, account_id: str) -> DoubtReport | None:
        if not doubt:
            return None
        return DoubtReport(doubt="only 0 of the statement's 9 rows match.", evidence="")

    wired = WebConfig(
        client_id="client-1",
        client_secret="tlcs_live_abcdefghij1234567890",
        redirect_uri="https://obdi.example.com/callback",
        connection_store=ConnectionStore(tmp_path / "c.json"),
        declared_accounts=lambda: [],
        declare_account=declare,
        assign_kept_statement=assign,
        review_kept_statement=review,
    )
    handler = type(
        "StubHandler",
        (ConnectionHandler,),
        {"config": wired, "session": AuthorisationSession()},
    )
    httpd = ConnectionHandler.make_server(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{httpd.server_port}"

    def stop() -> None:
        httpd.shutdown()  # type: ignore[attr-defined]
        httpd.server_close()  # type: ignore[attr-defined]

    return base, stop


def _post(base: str, path: str, data: dict[str, str]) -> httpx.Response:
    return httpx.post(f"{base}{path}", data=data, headers={"Origin": base}, timeout=30)


class TestADoubtAskedFromBringInLeadsBackToBringIn:
    def test_ThroughTheDoubtPage_TheAnswerIsStillBringIn(self, tmp_path):
        calls = _Calls()
        base, stop = _stub_server(tmp_path, calls, doubt=True)
        try:
            asked = httpx.post(
                f"{base}/statement-assign",
                data={"artefact": "4", "account": "acct-new"},
                headers={"Origin": base, "Referer": f"{base}/bring-in"},
                timeout=30,
            )
            action, fields = _form_with(asked.text, ANYWAY)
            # The press that follows has no Referer worth the name: the form's own fields carry
            # where the person came from.
            answered = _post(base, action, fields)
        finally:
            stop()

        assert asked.status_code == 409
        assert fields["back"] == "/bring-in"
        assert 'href="/bring-in">Back to Bring in</a>' in asked.text
        assert "Back to kept statements" not in asked.text
        assert answered.status_code == 200
        assert 'action="/bring-in"' in answered.text
        assert "Read another" not in answered.text

    def test_FromAnyOtherPage_TheDoubtPageCarriesNoBackAndKeepsTheKeptStatementsWay(self, tmp_path):
        calls = _Calls()
        base, stop = _stub_server(tmp_path, calls, doubt=True)
        try:
            asked = httpx.post(
                f"{base}/statement-assign",
                data={"artefact": "4", "account": "acct-new"},
                headers={"Origin": base, "Referer": f"{base}/statements"},
                timeout=30,
            )
            _, fields = _form_with(asked.text, ANYWAY)
        finally:
            stop()

        assert "back" not in fields
        assert "Back to kept statements" in asked.text

    def test_AForgedBack_IsNeverFollowedToAnywhereButBringIn(self, tmp_path):
        calls = _Calls()
        base, stop = _stub_server(tmp_path, calls, doubt=True)
        try:
            asked = _post(
                base,
                "/statement-assign",
                {"artefact": "4", "account": "acct-new", "back": "https://evil.example/x"},
            )
            _, fields = _form_with(asked.text, ANYWAY)
        finally:
            stop()

        assert "back" not in fields
        assert "evil.example" not in asked.text


class TestANewAccountAndADoubtTogether:
    def test_TheDoubtIsAnsweredFirst_ThenTheNewAccount_AndNeitherAnswerIsLost(self, tmp_path):
        calls = _Calls()
        base, stop = _stub_server(tmp_path, calls, doubt=True)
        try:
            first = _post(base, "/statement-assign", {"artefact": "4", "account_other": "acct-new"})
            action, fields = _form_with(first.text, ANYWAY)
            second = _post(base, action, fields)
            action, fields = _form_with(second.text, "and continue")
            third = _post(base, action, fields)
        finally:
            stop()

        assert "Is this the right account?" in first.text
        assert "No such account" in second.text
        assert calls.assigned == [(4, "acct-new", True)]
        assert calls.declared == ["acct-new"]
        assert third.status_code == 200

    def test_TheNewAccountIsConfirmedFirst_ThenTheDoubt_AndNeitherAnswerIsLost(self, tmp_path):
        calls = _Calls()
        base, stop = _stub_server(tmp_path, calls, doubt=True)
        try:
            first = _post(
                base,
                "/statement-assign",
                {
                    "artefact": "4",
                    "account_other": "acct-new",
                    "confirm_new_account": "acct-new",
                },
            )
            action, fields = _form_with(first.text, ANYWAY)
            assert fields["confirm_new_account"] == "acct-new"
            assert calls.declared == []
            second = _post(base, action, fields)
        finally:
            stop()

        assert "Is this the right account?" in first.text
        assert calls.declared == ["acct-new"]
        assert calls.assigned == [(4, "acct-new", True)]
        assert second.status_code == 200

    def test_AWalkedAwayDoubt_DeclaresNoAccount(self, tmp_path):
        calls = _Calls()
        base, stop = _stub_server(tmp_path, calls, doubt=True)
        try:
            _post(
                base,
                "/statement-assign",
                {"artefact": "4", "account_other": "acct-new", "confirm_new_account": "acct-new"},
            )
        finally:
            stop()

        assert calls.declared == []
        assert calls.assigned == []

    def test_WithNoDoubt_TheNewAccountConfirmationIsTheOnlyQuestion(self, tmp_path):
        calls = _Calls()
        base, stop = _stub_server(tmp_path, calls, doubt=False)
        try:
            first = _post(base, "/statement-assign", {"artefact": "4", "account_other": "acct-new"})
            action, fields = _form_with(first.text, "and continue")
            _post(base, action, fields)
        finally:
            stop()

        assert "No such account" in first.text
        assert calls.assigned == [(4, "acct-new", False)]


# --- sections of an "all accounts" statement ----------------------------------


class TestASectionAskedAboutBeforeItIsReadIn:
    @pytest.fixture
    def kept(self, db: Path) -> tuple[bytes, int]:
        payload = _two_accounts()
        with Store(db) as store:
            return payload, keep(store, payload, "all accounts.pdf")

    def test_ADoubtedSection_IsAskedAbout_NothingIsRecorded_ThenReadInOnceAcknowledged(
        self, db, kept
    ):
        payload, artefact = kept
        _feed(db, SAVER, _section_rows(payload), copy=False)
        before = _dump(db)
        base, stop = serve_config(config(db))
        try:
            asked = _post(
                base,
                "/statement-section-assign",
                {"artefact": str(artefact), "section": SAVER_KEY, "account": SAVER},
            )
            unchanged = _dump(db)
            action, fields = _form_with(asked.text, ANYWAY)
            done = _post(base, action, fields)
        finally:
            stop()

        assert asked.status_code == 409
        assert "only 0 of the statement&#x27;s 6 rows" in asked.text
        assert "The matcher would merge 0 of 6 rows" in asked.text
        assert unchanged == before
        assert fields["section"] == SAVER_KEY
        assert "<h2>Read in</h2>" in done.text
        assert "assigned over a stated doubt: only 0 of the statement's 6 rows" in html.unescape(
            done.text
        )
        assert _assignments(db) == [(SAVER_KEY, SAVER)]

    def test_AnAcknowledgementMadeForAnotherSection_DoesNotRecordThisOne(self, db):
        payload = pdf(
            document(
                section("Regular Saver", 80000, SIX_MOVES),
                section("Christmas Club", 15000, CLUB_MOVES),
            ),
            step=5.5,
        )
        with Store(db) as store:
            artefact = keep(store, payload, "busy.pdf")
        read = read_sections(payload)
        assert read is not None
        parser, _ = read
        for key in (SAVER_KEY, section_key("Christmas Club")):
            rows = list(parser.parse_section(payload, key, account_id="probe"))
            _feed(db, SAVER, rows, copy=False, tag=key)
        base, stop = serve_config(config(db))
        try:
            asked = _post(
                base,
                "/statement-section-assign",
                {"artefact": str(artefact), "section": SAVER_KEY, "account": SAVER},
            )
            _, fields = _form_with(asked.text, ANYWAY)
            replayed = _post(
                base,
                "/statement-section-assign",
                {**fields, "section": section_key("Christmas Club")},
            )
        finally:
            stop()

        assert replayed.status_code == 409
        assert "Is this the right account?" in replayed.text
        assert _assignments(db) == []

    def test_AcknowledgedAtTheHook_TheSectionIsRecorded_AndTheSentenceQuotesTheDoubt(
        self, db, kept
    ):
        payload, artefact = kept
        _feed(db, SAVER, _section_rows(payload), copy=False)
        wired = config(db)

        refused = wired.assign_statement_section(artefact, SAVER_KEY, SAVER)
        outcome = wired.assign_statement_section(
            artefact, SAVER_KEY, SAVER, doubt_acknowledged=True
        )

        assert refused.startswith("Not assigned:")
        assert f"assigned to {SAVER} and read by" in outcome
        assert "assigned over a stated doubt: only 0 of the statement's 6 rows" in outcome
        assert _assignments(db) == [(SAVER_KEY, SAVER)]

    def test_ASectionTheMatcherWouldMergeInFull_IsRecordedWithoutAPrompt(self, db, kept):
        payload, artefact = kept
        rows = _section_rows(payload)
        _feed(db, SAVER, rows, copy=True, lag=4, relabel=True)
        _edges(db, SAVER, rows)

        outcome = config(db).assign_statement_section(artefact, SAVER_KEY, SAVER)

        assert f"assigned to {SAVER} and read by" in outcome
        assert "the matcher would merge 6 of 6 rows" in outcome
        assert _assignments(db) == [(SAVER_KEY, SAVER)]


class TestAcknowledgedAssignmentOfAWholeStatement:
    def test_AcknowledgedAtTheHook_TheStatementIsFiledAndReadIn_QuotingTheDoubt(self, db):
        _feed(db, ACCOUNT_C, _statement_rows(SANTANDER_AS_WRITTEN), copy=False)
        wired, artefact = _wired_with_statement(db)

        outcome = wired.assign_kept_statement(artefact, ACCOUNT_C, doubt_acknowledged=True)

        assert f"assigned to {ACCOUNT_C} and read by {STATEMENT_SOURCE}" in outcome
        assert outcome.endswith(
            "assigned over a stated doubt: only 0 of the statement's 7 rows between "
            f"2026-06-29 and 2026-07-11 match what {WITNESS} holds for {ACCOUNT_C}; "
            "this is probably another account's statement."
        )
        assert _account_of(db, artefact) == ACCOUNT_C

    def test_AcknowledgingWhereNothingIsDoubted_ReadsItInWithoutAnyDoubtSentence(self, db):
        wired, artefact = _wired_with_statement(db)

        outcome = wired.assign_kept_statement(artefact, "acct-new", doubt_acknowledged=True)

        assert "doubt" not in outcome
        assert _account_of(db, artefact) == "acct-new"


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


CLUB_MOVES = [
    Move("04/05/2025", "Club Lodgement", 1200),
    Move("05/05/2025", "Club Standing Order", 1300),
    Move("06/05/2025", "Club Transfer In", 1400),
    Move("07/05/2025", "Club Cash Deposit", 1500),
    Move("08/05/2025", "Club Lodgement", 1600),
    Move("09/05/2025", "Club Lodgement Two", 1700),
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
