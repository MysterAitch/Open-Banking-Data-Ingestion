"""A section of an "all accounts" statement overlapping an annual statement.

The credit union also issues single-account annual statements of the savings
account, and the savings section of an "all accounts" document covers dates the
annual one covers too. Assigned to the same account, the two list some of the
same rows. The question is what the account holds afterwards, and the answer
has to come from the mechanism that already merges overlapping id-less files
(content key plus occurrence, in `matching`), not from a second one invented
for sections.

THE KNOWN ANSWER was decided before any document existed. The account's
activity is the fourteen moves in `ACTIVITY`; the annual statement prints the
first eleven (January to June) and the all-accounts section prints the last
eleven (April to September), so eight rows are in both. Whichever way round the
two are assigned, and whichever way round they arrived, the account must hold
exactly the fourteen, each once; its total must equal the later statement's
closing balance less its opening; every statement anchor must agree; and no row
both documents list identically may leave a review flag open.

The same-day twins are the case the mechanism is known to find hard: two
identical payments on one day are indistinguishable from one payment reported
twice, so the matcher flags them whatever they arrive in. They are tested
separately, and what they leave open is stated, not hidden.
"""

from __future__ import annotations

from itertools import product
from pathlib import Path

import pytest

from credit_union_documents import Move, document, pdf, section
from obdi.balance_anchors import effective_opening
from obdi.ingest.parsers.credit_union_pdf import section_key
from obdi.ingest.rebuild import rebuild_from_raw
from obdi.ingest.store import Store
from section_harness import (
    config,
    environment,
    holdings,
    keep,
    open_flags,
    total,
)

ACCOUNT = "credit-union-saver"
LABEL = "Regular Saver"
OPENING = 100000

#: (day, how the money moved, store-convention amount, payee).
ACTIVITY = [
    Move("10/01/2025", "DD Lodgement", 2500),
    Move("15/01/2025", "Div - Regular Saver", 120),
    Move("10/02/2025", "DD Lodgement", 2500),
    Move("14/02/2025", "Internet Transfer", -5000, "J SMITH"),
    Move("10/03/2025", "DD Lodgement", 2500),
    Move("10/04/2025", "DD Lodgement", 2500),
    Move("15/04/2025", "Div - Regular Saver", 130),
    Move("10/05/2025", "DD Lodgement", 2500),
    Move("22/05/2025", "Internet Transfer", -4000, "J SMITH"),
    Move("10/06/2025", "DD Lodgement", 2500),
    Move("28/06/2025", "Div - Regular Saver", 140),
    Move("10/07/2025", "DD Lodgement", 2500),
    Move("10/08/2025", "DD Lodgement", 2500),
    Move("12/09/2025", "Internet Transfer", -3000, "J SMITH"),
]

ANNUAL_MOVES = ACTIVITY[:11]
ALL_ACCOUNTS_MOVES = ACTIVITY[5:]
BOTH = ACTIVITY[5:11]


def _held(moves: list[Move]) -> dict[tuple[str, int, str], int]:
    out: dict[tuple[str, int, str], int] = {}
    for move in moves:
        year = move.day[6:]
        key = (
            f"{year}-{move.day[3:5]}-{move.day[:2]}",
            move.minor,
            f"{move.source} {move.payee}".strip(),
        )
        out[key] = out.get(key, 0) + 1
    return out


def _annual(moves: list[Move] = ANNUAL_MOVES) -> bytes:
    return pdf(
        section(LABEL, OPENING, moves, period="01/01/2025 to 30/06/2025"), step=15
    )


def _opening_before(first: Move) -> int:
    earlier = [move for move in ACTIVITY if ACTIVITY.index(move) < ACTIVITY.index(first)]
    return OPENING + sum(move.minor for move in earlier)


def _all_accounts(moves: list[Move] = ALL_ACCOUNTS_MOVES) -> bytes:
    return pdf(
        document(
            section(
                LABEL,
                _opening_before(moves[0]) if moves else 0,
                moves,
                period="01/04/2025 to 30/09/2025",
            ),
            section("Personal -9.50%", -50000, [Move("12/05/2025", "tx", 15500)], loan=True),
            section("Rainy Day", 22050, []),
        ),
        step=8,
    )


@pytest.fixture
def db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    environment(monkeypatch, tmp_path)
    path = tmp_path / "store.sqlite3"
    with Store(path):
        pass
    return path


def _assign(
    db: Path,
    *,
    landing: tuple[str, str],
    assignment: tuple[str, str],
    annual: bytes | None = None,
    everything: bytes | None = None,
) -> None:
    """Land both documents in `landing` order, assign them in `assignment` order."""
    documents = {
        "annual": annual if annual is not None else _annual(),
        "all": everything if everything is not None else _all_accounts(),
    }
    ids: dict[str, int] = {}
    with Store(db) as store:
        for order, name in enumerate(landing):
            ids[name] = keep(store, documents[name], f"{name}.pdf", order=order)
    wired = config(db)
    for name in assignment:
        if name == "annual":
            wired.assign_kept_statement(ids[name], ACCOUNT)
        else:
            wired.assign_statement_section(ids[name], section_key(LABEL), ACCOUNT)


ORDERS = [
    pytest.param(("annual", "all"), ("annual", "all"), id="annual-first-landed-and-assigned"),
    pytest.param(("annual", "all"), ("all", "annual"), id="annual-landed-first-all-assigned-first"),
    pytest.param(("all", "annual"), ("annual", "all"), id="all-landed-first-annual-assigned-first"),
    pytest.param(("all", "annual"), ("all", "annual"), id="all-first-landed-and-assigned"),
]


class TestTheUnionOfTwoOverlappingDocuments:
    @pytest.mark.parametrize(("landing", "assignment"), ORDERS)
    def test_TheAccountHoldsTheUnionOfTheRows_EachOnce(self, db, landing, assignment):
        _assign(db, landing=landing, assignment=assignment)

        assert dict(holdings(db, ACCOUNT)) == _held(ACTIVITY)
        assert sum(holdings(db, ACCOUNT).values()) == 14

    @pytest.mark.parametrize(("landing", "assignment"), ORDERS)
    def test_TheTotal_EqualsTheLaterClosingBalanceLessItsOpening(self, db, landing, assignment):
        _assign(db, landing=landing, assignment=assignment)

        later_closing = OPENING + sum(move.minor for move in ACTIVITY)
        assert total(db, ACCOUNT) == later_closing - OPENING

    @pytest.mark.parametrize(("landing", "assignment"), ORDERS)
    def test_EveryStatementAnchor_AgreesWithTheRowsHeld(self, db, landing, assignment):
        _assign(db, landing=landing, assignment=assignment)

        with Store(db) as store:
            opening = effective_opening(store, ACCOUNT)

        assert opening.opening_minor == OPENING
        assert len(opening.readings) == 2, "one anchor from each document"
        assert not opening.differing

    @pytest.mark.parametrize(("landing", "assignment"), ORDERS)
    def test_NoReviewFlagIsLeftOpen_ForRowsBothDocumentsListIdentically(
        self, db, landing, assignment
    ):
        _assign(db, landing=landing, assignment=assignment)

        assert open_flags(db) == []

    @pytest.mark.parametrize(("landing", "assignment"), ORDERS)
    def test_AfterARebuild_TheAccountHoldsTheSameRows_AndStillNoFlag(
        self, db, landing, assignment
    ):
        _assign(db, landing=landing, assignment=assignment)
        before = holdings(db, ACCOUNT)

        with Store(db) as store:
            rebuild_from_raw(store)

        assert holdings(db, ACCOUNT) == before
        assert dict(holdings(db, ACCOUNT)) == _held(ACTIVITY)
        assert open_flags(db) == []

    @pytest.mark.parametrize(("landing", "assignment"), ORDERS)
    def test_ARebuildDoneTwice_ChangesNothingFurther(self, db, landing, assignment):
        _assign(db, landing=landing, assignment=assignment)

        with Store(db) as store:
            rebuild_from_raw(store)
            first = sorted(
                (row.entity_id, row.content_key, row.occurrence)
                for row in store.all_transactions()
            )
            rebuild_from_raw(store)
            second = sorted(
                (row.entity_id, row.content_key, row.occurrence)
                for row in store.all_transactions()
            )

        assert first == second

    def test_TheEightRowsInBoth_AreTheOnesTheFixtureSaysAreShared(self):
        # Guards the fixture, not the code: were the overlap not several
        # months deep, every case above would pass for the wrong reason.
        assert len(BOTH) == 6
        assert {move.day[3:5] for move in BOTH} == {"04", "05", "06"}
        assert len(set(map(str, ANNUAL_MOVES)) & set(map(str, ALL_ACCOUNTS_MOVES))) == 6


class TestTheOtherSectionsOfTheSameDocument:
    def test_TheLoanSection_IsUnaffectedByTheSavingsOverlap(self, db):
        _assign(db, landing=("annual", "all"), assignment=("annual", "all"))
        ids = {}
        with Store(db) as store:
            row = store.connection.execute(
                "SELECT rowid FROM raw_artefacts WHERE origin = 'all.pdf'"
            ).fetchone()
            ids["all"] = int(row["rowid"])

        config(db).assign_statement_section(ids["all"], section_key("Personal -9.50%"), "loan")

        assert total(db, "loan") == 15500
        assert dict(holdings(db, ACCOUNT)) == _held(ACTIVITY)


#: A payment that belongs to no rhythm: an occasional lodgement, so that
#: repeating it on one day is the genuinely ambiguous shape rather than the
#: next instalment of something regular (the matcher stays quiet about those).
ONE_OFF = Move("20/05/2025", "Cheque Lodgement", 1720)
ONE_OFF_KEY = ("2025-05-20", 1720, "Cheque Lodgement")


def _repeating_documents(*, annual_lists: int, all_accounts_lists: int) -> tuple[bytes, bytes]:
    """Both documents carry the one-off payment, repeated on its day as stated.

    The union is the fourteen moves plus the larger of the two repeat counts.
    """
    annual_moves = [*ACTIVITY[:8], *[ONE_OFF] * annual_lists, *ACTIVITY[8:11]]
    all_moves = [ACTIVITY[7], *[ONE_OFF] * all_accounts_lists, *ACTIVITY[8:]]
    opening = OPENING + sum(move.minor for move in ACTIVITY[:7])
    annual = pdf(
        section(LABEL, OPENING, annual_moves, period="01/01/2025 to 30/06/2025"), step=15
    )
    everything = pdf(
        document(
            section(LABEL, opening, all_moves, period="01/05/2025 to 30/09/2025"),
            section("Rainy Day", 0, []),
        ),
        step=8,
    )
    return annual, everything


class TestAPostingOneDocumentSawLate:
    """Two identical same-day payments in one document and three in the other.

    A payment that posted after the earlier export was taken: the later
    document lists a third identical row on the day. Occurrence numbers each
    repeat from zero within a document, so the first two merge pairwise and
    the third is new.
    """

    @pytest.mark.parametrize(("landing", "assignment"), ORDERS)
    def test_TheAccountHoldsAllThreeOfTheDaysPayments_AndEverythingElseOnce(
        self, db, landing, assignment
    ):
        annual, everything = _repeating_documents(annual_lists=2, all_accounts_lists=3)

        _assign(db, landing=landing, assignment=assignment, annual=annual, everything=everything)

        held = holdings(db, ACCOUNT)
        assert held[ONE_OFF_KEY] == 3
        assert sum(held.values()) == 14 + 3

    @pytest.mark.parametrize(("landing", "assignment"), ORDERS)
    def test_TheEarlierDocumentListingMore_IsNotShortenedByTheLaterOne(
        self, db, landing, assignment
    ):
        annual, everything = _repeating_documents(annual_lists=3, all_accounts_lists=2)

        _assign(db, landing=landing, assignment=assignment, annual=annual, everything=everything)

        assert holdings(db, ACCOUNT)[ONE_OFF_KEY] == 3

    @pytest.mark.parametrize(("landing", "assignment"), ORDERS)
    @pytest.mark.parametrize(("annual_lists", "all_lists"), [(2, 3), (3, 2)])
    def test_TheFlagsRaisedForTheRepeat_AreTheSameInEveryOrder_AndAfterARebuild(
        self, db, landing, assignment, annual_lists, all_lists
    ):
        # Measured, not chosen: three identical same-day payments are two
        # questions for a person however the documents meet, and a rebuild
        # re-derives exactly those two.
        annual, everything = _repeating_documents(
            annual_lists=annual_lists, all_accounts_lists=all_lists
        )
        _assign(db, landing=landing, assignment=assignment, annual=annual, everything=everything)
        live = len(open_flags(db))

        with Store(db) as store:
            rebuild_from_raw(store)

        assert live == 2
        assert len(open_flags(db)) == live

    @pytest.mark.parametrize(("landing", "assignment"), ORDERS)
    def test_AfterARebuild_TheThreeAreStillThree(self, db, landing, assignment):
        annual, everything = _repeating_documents(annual_lists=2, all_accounts_lists=3)
        _assign(db, landing=landing, assignment=assignment, annual=annual, everything=everything)
        before = holdings(db, ACCOUNT)

        with Store(db) as store:
            rebuild_from_raw(store)

        assert holdings(db, ACCOUNT) == before


class TestTheCaseTheMechanismCannotSettle:
    """Identical same-day payments, which both documents list.

    Two identical payments on one day are the ambiguous shape the matcher
    exists to flag: a repeated payment and a duplicate report look the same.
    That flag is raised by the FIRST document alone, before any overlap, so it
    is not something sections introduce - and it stays open, for a person,
    exactly as an annual statement with the same two rows would leave it.
    The rows themselves are right: two, once each, however the documents meet.
    """

    @pytest.mark.parametrize(("landing", "assignment"), ORDERS)
    def test_TheTwinsAreHeldTwice_EachDocumentsRowsMergedPairwise(self, db, landing, assignment):
        annual, everything = _repeating_documents(annual_lists=2, all_accounts_lists=2)

        _assign(db, landing=landing, assignment=assignment, annual=annual, everything=everything)

        held = holdings(db, ACCOUNT)
        assert held[ONE_OFF_KEY] == 2
        assert sum(held.values()) == 14 + 2

    def test_TheTwinsAreFlaggedByTheFirstDocumentAlone_AndTheSecondAddsNoSecondQuestion(
        self, db
    ):
        annual, everything = _repeating_documents(annual_lists=2, all_accounts_lists=2)
        _assign(
            db,
            landing=("annual", "all"),
            assignment=("annual",),
            annual=annual,
            everything=everything,
        )
        alone = len(open_flags(db))

        config(db).assign_statement_section(
            _artefact(db, "all.pdf"), section_key(LABEL), ACCOUNT
        )

        assert alone == 1, "the twins are a question for a person in a single statement"
        assert len(open_flags(db)) == alone

    def test_AnAnnualStatementWithTheTwins_LeavesTheSameFlagWithNoSectionInvolved(self, db):
        annual, _ = _repeating_documents(annual_lists=2, all_accounts_lists=2)
        with Store(db) as store:
            artefact = keep(store, annual, "annual.pdf")

        config(db).assign_kept_statement(artefact, ACCOUNT)

        assert len(open_flags(db)) == 1, "the flag is the matcher's, not the sections'"


def _artefact(db: Path, name: str) -> int:
    with Store(db) as store:
        row = store.connection.execute(
            "SELECT rowid FROM raw_artefacts WHERE origin = ?", (name,)
        ).fetchone()
    return int(row["rowid"])


def test_EveryOrderCombination_IsExercised() -> None:
    # The matrix is the claim: four ways the two documents can meet.
    assert len(list(product(("annual", "all"), repeat=2))) == len(ORDERS)
