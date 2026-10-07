"""A balance stated by a source that cannot see Spaces is the balance of the whole family.

A Starling account is a main account plus a Space per pot. The certified
statement, the CSV export and the aggregator describe every external payment of
the whole account, Space payments included, and none of the transfers between
main and a Space, so the balance they print moves with every payment from any
of them. Filing that balance as an anchor of the MAIN account alone derived an
opening wrong by whatever the Spaces held at the anchor's date.

THE HOUSEHOLD. One main account and one Space, September 2026, amounts in
pence. The whole account held 800.00 before the 1st, all of it in the main
account (a Space's rows start from nil). Every figure below was worked by hand
before the first run.

    day  what                                   main      Bills     family after
    1    salary into main                     +300000                380000
    2    transfer main -> Bills (both legs)    -40000    +40000      380000
    4    cafe, from main                         -2500                377500
    12   water bill, from Bills                           -5000      372500
    15   grocer, from main                       -7000               365500
    16   transfer main -> Bills (both legs)     -2000     +2000      365500
    20   gym, from Bills                                   -3000      362500
    22   garage, from main                       -9000               353500
    25   refund into main                      +120000               473500

    The certified statement covers 10 to 30 September and prints the WHOLE
    account's balance: it opens at 3775.00 (the family's balance before its
    first row, so at the end of the 11th), prints an end-of-day balance on the
    12th, 15th, 20th, 22nd and 25th, and closes at 4735.00. Its seven family
    anchors are therefore (11th, 377500) (12th, 372500) (15th, 365500)
    (20th, 362500) (22nd, 353500) (25th, 473500) (30th, 473500).

    Main's balance at the end of the 11th is 337500 and Bills' is 40000. Main's
    true opening is 80000. At the end of the 30th the Bills Space holds 34000,
    which is exactly how far the old derivation missed.
"""

from __future__ import annotations

import json
import pathlib
from collections.abc import Iterable
from dataclasses import replace
from datetime import date

import pytest

from obdi.actual_push import opening_balances
from obdi.core.models import TransactionStatus
from obdi.ingest.accounts import (
    BALANCE_ONLY_KIND,
    AccountBinding,
    AccountMap,
    AccountRecord,
    AccountRef,
)
from obdi.ingest.family_anchors import OPENED, Families, families_of, family_anchors
from obdi.ingest.pipeline import import_file, reconcile_batch
from obdi.ingest.providers import starling, truelayer
from obdi.ingest.rebuild import parse_artefact_transactions, rebuild_from_raw
from obdi.ingest.store import Store
from obdi.ingest.typed_transactions import record_typed_transaction
from obdi.read.ledger import (
    ANCHOR_QUERIES,
    FAMILY_DISCOVERY_QUERIES,
    FAMILY_QUERIES,
    FEED_TIME_QUERIES,
    QUERIES_PER_PAGE,
    SPACE_QUERIES,
    STATEMENT_CHECK_QUERIES,
    build_ledger,
)
from obdi.replay import ActualAccountBinding
from obdi.verify.balance_anchors import FAMILY, effective_opening, record_stated_anchor
from obdi.verify.balance_anchors import STATEMENT as STATEMENT_BASIS
from test_space_attribution import (
    BILLS,
    FEED,
    HOLIDAY,
    MAIN,
    MAP,
    Household,
    household_map,
    pay,
)
from test_starling_statement import build_starling_pdf

STATEMENT = [
    "SUMMARY|10/09/2026 - 30/09/2026|3775.00|1200.00|240.00|4735.00",
    "HEAD",
    "OPENING|3775.00",
    "ROW|12/09/2026|FASTER PAYMENT|Water Co||50.00|3725.00",
    "ROW|15/09/2026|FASTER PAYMENT|Grocer||70.00|3655.00",
    "ROW|20/09/2026|FASTER PAYMENT|Gym||30.00|3625.00",
    "ROW|22/09/2026|FASTER PAYMENT|Garage||90.00|3535.00",
    "ROW|25/09/2026|DIRECT CREDIT|Refund|1200.00||4735.00",
    "END",
]

OTHER = "halifax-current"
TRUE_MAIN_OPENING = 80000
BILLS_AT_THE_END = 34000


def feed_rows() -> list:
    """What Starling's own feed reports, each payment under the pot it left."""
    return [
        pay(MAIN, FEED, "f-salary", 300000, 1, "Employer"),
        pay(MAIN, FEED, "f-out-1", -40000, 2, "To Bills", internal=True),
        pay(BILLS, FEED, "f-in-1", 40000, 2, "From Main", internal=True),
        pay(MAIN, FEED, "f-cafe", -2500, 4, "Cafe"),
        pay(BILLS, FEED, "f-water", -5000, 12, "Water Co"),
        pay(MAIN, FEED, "f-grocer", -7000, 15, "Grocer"),
        pay(MAIN, FEED, "f-out-2", -2000, 16, "To Bills", internal=True),
        pay(BILLS, FEED, "f-in-2", 2000, 16, "From Main", internal=True),
        pay(BILLS, FEED, "f-gym", -3000, 20, "Gym"),
    ]


ACCOUNTS_ORIGIN = "https://api.example.com/api/v2/accounts"
FEED_ORIGIN = "https://api.example.com/api/v2/feed/account/acc-main/category/cat-main"

#: The account was created on the 1st, in the morning, and its feed was first
#: asked from midnight that day.
CREATED_AT = "2026-09-01T09:30:00Z"
FIRST_ASKED = "2026-09-01T00:00:00Z"


def land_evidence(
    store: Store,
    *,
    created: str | None = CREATED_AT,
    asked_from: str | None = FIRST_ASKED,
    also_created: str | None = None,
) -> None:
    """What the provider states about the account's creation, and the feed
    requests held for its default category (an empty feed is enough: the
    evidence is the ask, which survives only as the artefact's origin)."""
    entry: dict[str, str] = {"accountUid": "acc-main", "defaultCategory": "cat-main"}
    if created is not None:
        entry["createdAt"] = created
    listings = [entry]
    if also_created is not None:
        listings = [entry, {**entry, "createdAt": also_created}]
    for position, listing in enumerate(listings):
        store.land_artefact(
            starling.artefact_for(
                json.dumps({"accounts": [listing], "n": position}).encode(),
                account_id="starling",
                kind="accounts",
                origin=ACCOUNTS_ORIGIN,
            )
        )
    if asked_from is not None:
        store.land_artefact(
            starling.artefact_for(
                json.dumps({"feedItems": []}).encode(),
                account_id="starling:cat-main",
                kind="feed",
                origin=f"{FEED_ORIGIN}?changesSince={asked_from}",
            )
        )


def opened_feed_rows() -> list:
    """The household's feed with the 800.00 the account was funded with, paid
    in on the day it was created, so the family really did open at nil."""
    return [pay(MAIN, FEED, "f-deposit", 80000, 1, "Opening deposit"), *feed_rows()]


def leg(account: str, minor: int, day: int, uid: str, ident: str):
    """A transfer leg whose counterparty is the category `uid`, as the feed reports it."""
    return replace(
        pay(account, FEED, ident, minor, day, "To a Space", internal=True),
        raw={
            "counterPartyType": "CATEGORY",
            "counterPartyUid": uid,
            "counterPartyName": "Rent",
            "transactionTime": f"2026-09-{day:02}T10:00:00Z",
        },
    )


def import_statement(store: Store, directory: pathlib.Path, lines: Iterable[str]) -> None:
    path = directory / "statement.pdf"
    path.write_bytes(build_starling_pdf(list(lines)))
    import_file(store, path, account_id=MAIN, account_map=MAP)


def families(home: Household) -> Families:
    return families_of(home.store, home.account_map)


@pytest.fixture
def store(tmp_path):
    with Store(tmp_path / "family.sqlite3") as opened:
        yield opened


@pytest.fixture
def family(store, tmp_path):
    """The household with every row right: the feed's rows, then the statement."""
    home = Household(store, MAP)
    home.arrive(*feed_rows())
    import_statement(store, tmp_path, STATEMENT)
    return home


class TestTheMainAccountsOpeningWhenOnlyAWholeAccountStatementStatesABalance:
    def test_MainOpening_WhenOnlyTheStatementStatesABalance_IsTheTrueOpening(self, family):
        # Measured before the change: 114000, which is the true opening plus
        # the 34000 the Bills Space held at the statement's closing date.
        opening = effective_opening(family.store, MAIN, families=families(family))

        assert opening.opening_minor == TRUE_MAIN_OPENING

    def test_MainAnchors_AreFamilyAnchorsNamedAsSuch_NotTheStatementsOwn(self, family):
        opening = effective_opening(family.store, MAIN, families=families(family))

        assert {r.anchor.basis for r in opening.readings} == {FAMILY}

    def test_MainBalanceAtTheFirstAnchor_IsTheFamilyBalanceLessTheSpacesOwnRows(self, family):
        # Family 377500 at the end of the 11th (the statement's opening: its
        # first row is the 12th), less Bills' 40000 (the one transfer in so
        # far) is main's 337500.
        opening = effective_opening(family.store, MAIN, families=families(family))

        first = opening.readings[0]
        assert (first.anchor.day, first.anchor.balance_minor) == (date(2026, 9, 11), 337500)

    def test_MainLaterAnchors_AreChecksOfMainsOwnRows_AndAllAgreeWhenTheRowsAreRight(self, family):
        opening = effective_opening(family.store, MAIN, families=families(family))

        assert [r.agrees for r in opening.readings] == [None, True, True, True, True, True, True]

    def test_SpaceItself_IsUntouchedByFamilyAnchors(self, family):
        # A Space has no Spaces: the statement (filed under main) is not its anchor.
        opening = effective_opening(family.store, BILLS, families=families(family))

        assert opening.readings == ()
        assert opening.opening_minor is None

    def test_AccountWithoutKnownSpaces_KeepsEveryAnchorAsItsOwn(self, store, tmp_path):
        # The same statement, a map that knows no Space: nothing is set aside.
        no_spaces = AccountMap([AccountBinding(MAIN, "starling", "acc-main")])
        Household(store, no_spaces).arrive(pay(MAIN, FEED, "f-salary", 300000, 1, "Employer"))
        import_statement(store, tmp_path, STATEMENT)

        with_families = effective_opening(store, MAIN, families=families_of(store, no_spaces))
        without = effective_opening(store, MAIN)

        assert with_families == without
        assert {r.anchor.basis for r in with_families.readings} == {STATEMENT_BASIS}
        assert with_families.family is None

    def test_SourceThatFeedsASpace_IsNotBlind_AndKeepsItsStatementAnchorsForMain(
        self, store, tmp_path
    ):
        # Decided from the map and not from a name: bind the statement's own
        # source to the Bills Space and it is no longer blind to it.
        seeing = household_map(
            spaces_declared=True,
            extra_bindings=(AccountBinding(BILLS, "starling-statement-pdf", "x"),),
        )
        Household(store, seeing).arrive(*feed_rows())
        import_statement(store, tmp_path, STATEMENT)
        found = families_of(store, seeing)

        opening = effective_opening(store, MAIN, families=found)

        assert not found.blind("starling-statement-pdf", MAIN)
        assert {r.anchor.basis for r in opening.readings} == {STATEMENT_BASIS}
        assert opening.family is None


class TestTheFamilyAnchorsAStatementStates:
    def test_Statement_YieldsItsOpeningEveryPrintedDayAndItsClosing(self, family):
        found = family_anchors(family.store, MAIN, families(family))

        assert [(a.day.day, a.balance_minor) for a in found.anchors] == [
            (11, 377500),
            (12, 372500),
            (15, 365500),
            (20, 362500),
            (22, 353500),
            (25, 473500),
            (30, 473500),
        ]
        assert {a.source for a in found.anchors} == {"starling-statement-pdf"}
        assert found.refused_figures == 0

    def test_PrintedBalance_WhenItDisagreesWithTheStatementsOwnRows_IsRefusedAndCounted(
        self, store, tmp_path
    ):
        # The 15th prints 3656.00 where the rows say 3655.00: a figure read from
        # the wrong place must never become an anchor.
        wrong = [line.replace("3655.00", "3656.00") for line in STATEMENT]
        Household(store, MAP).arrive(*feed_rows())
        import_statement(store, tmp_path, wrong)

        found = family_anchors(store, MAIN, families_of(store, MAP))

        assert 15 not in [a.day.day for a in found.anchors]
        assert found.refused_figures == 1
        assert len(found.anchors) == 6


class TestTheFamilyWalk:
    def walk(self, home: Household):
        walked = effective_opening(home.store, MAIN, families=families(home)).family
        assert walked is not None
        return walked

    def test_Walk_WhenEveryRowIsRight_AgreesOnEveryPrintedDay(self, family):
        walked = self.walk(family)

        assert (walked.anchors, walked.agreeing, len(walked.differing)) == (7, 6, 0)
        assert walked.first_differing is None
        assert walked.last_agreeing is None
        assert walked.constant is None
        assert walked.spaces == (BILLS, HOLIDAY)
        assert walked.sources == ("starling-statement-pdf",)

    @pytest.mark.parametrize(
        ("gone", "first_day", "last_good_day", "difference"),
        [
            # A main-account payment, dated on an anchor day.
            pytest.param((MAIN, -9000, 22), 22, 20, -9000, id="main-payment-on-an-anchor-day"),
            # The same fault in a Space is caught as the family's sum covers both.
            pytest.param((BILLS, -3000, 20), 20, 15, -3000, id="space-payment-on-an-anchor-day"),
            # A payment between anchors is first seen at the next anchor on or after it.
            pytest.param((MAIN, -2000, 16), 20, 15, -2000, id="transfer-leg-between-anchors"),
        ],
    )
    def test_Walk_WhenOneMovementIsMissing_NamesTheFirstDifferingDayAndTheLastGoodOne(
        self, family, gone, first_day, last_good_day, difference
    ):
        drop_row(family.store, *gone)

        walked = self.walk(family)

        assert walked.first_differing is not None
        assert walked.first_differing.day.day == first_day
        assert walked.last_agreeing is not None
        assert walked.last_agreeing.day.day == last_good_day
        assert walked.constant is True
        assert walked.first_differing.difference_minor == difference
        assert {r.difference_minor for r in walked.differing} == {difference}

    def test_Walk_WhenOneTransferLegIsMissing_ReportsAFamilyDifferenceFromThatDay(self, family):
        # The Bills leg of the second transfer is gone: main still shows -2000
        # and nothing receives it, so the family is 2000 short from the 16th.
        drop_row(family.store, BILLS, 2000, 16)

        walked = self.walk(family)

        assert walked.first_differing is not None
        assert walked.first_differing.day.day == 20
        assert walked.first_differing.difference_minor == 2000
        assert walked.constant is True

    def test_Walk_WhenARowIsSurplus_ReportsTheOppositeSign(self, family):
        # A payment of 15.00 the bank never made, on the 18th.
        family.arrive(pay(MAIN, FEED, "f-extra", -1500, 18, "Extra"))

        walked = self.walk(family)

        assert walked.first_differing is not None
        assert walked.first_differing.day.day == 20
        assert walked.first_differing.difference_minor == 1500
        assert walked.constant is True

    def test_Walk_WhenTwoFaultsFallOnDifferentDates_SaysTheDifferenceIsChanging(self, family):
        drop_row(family.store, MAIN, -7000, 15)
        drop_row(family.store, MAIN, -9000, 22)

        walked = self.walk(family)

        assert walked.first_differing is not None
        assert walked.first_differing.day.day == 15
        assert walked.last_agreeing is not None
        assert walked.last_agreeing.day.day == 12
        assert walked.constant is False
        assert [r.difference_minor for r in walked.differing] == [
            -7000, -7000, -16000, -16000, -16000,
        ]

    def test_Walk_WhenTheFaultPredatesTheEarliestAnchor_IsAbsorbedIntoTheOpening(self, family):
        # The weakness, stated rather than hidden: the cafe on the 4th is
        # before the statement, so the earliest anchor absorbs its loss.
        drop_row(family.store, MAIN, -2500, 4)

        walked = self.walk(family)

        assert walked.first_differing is None
        opening = effective_opening(family.store, MAIN, families=families(family))
        assert opening.opening_minor == TRUE_MAIN_OPENING - 2500


@pytest.fixture
def opened(store, tmp_path):
    """The household that really opened at nil on the 1st: the 800.00 arrived
    that day, the provider states the creation, and the feed was asked from it."""
    home = Household(store, MAP)
    home.arrive(*opened_feed_rows())
    import_statement(store, tmp_path, STATEMENT)
    land_evidence(store)
    return home


class TestTheAccountOpenedAtNil:
    """Where the creation date and the feed's reach are both held, the family
    opened at nil and the earliest stated balance is a test, not a definition.

    Hand working: the family is 0 at the end of 31 August, takes 800.00 and
    3000.00 on the 1st (380000), and every figure after that is the household's
    table in the module docstring, so the statement's seven balances all agree.
    """

    def walk(self, home: Household):
        walked = effective_opening(home.store, MAIN, families=families(home)).family
        assert walked is not None
        return walked

    def test_Opening_WhenCreationAndFeedReachAreHeld_IsNilAndEveryStatedBalanceIsATest(
        self, opened
    ):
        opening = effective_opening(opened.store, MAIN, families=families(opened))
        walked = self.walk(opened)

        assert walked.opened is not None
        assert (walked.opened.day, walked.opened.balance_minor) == (date(2026, 8, 31), 0)
        assert [r.defines_opening for r in walked.readings] == [False] * 7
        assert (walked.anchors, walked.agreeing, len(walked.differing)) == (7, 7, 0)
        assert opening.defining is not None
        assert (opening.defining.basis, opening.defining.balance_minor) == (OPENED, 0)
        assert opening.opening_minor == 0
        assert opening.as_at == date(2026, 8, 31)
        assert opening.single_anchor is False

    def test_Walk_WhenAFaultPredatesTheFirstStatedBalance_IsCaughtAndDated(self, opened):
        # The cafe on the 4th is before the statement's first balance (the
        # 11th): with only stated balances it was absorbed into the opening.
        drop_row(opened.store, MAIN, -2500, 4)

        walked = self.walk(opened)

        assert walked.first_differing is not None
        assert walked.first_differing.day == date(2026, 9, 11)
        assert walked.first_differing.difference_minor == -2500
        assert walked.last_agreeing is not None
        assert walked.last_agreeing.day == date(2026, 8, 31)
        assert walked.constant is True

    def test_Walk_WhenOpenedAnchorHeldAndFaultLaterOn_StillNamesTheDifferingDay(self, opened):
        drop_row(opened.store, MAIN, -9000, 22)

        walked = self.walk(opened)

        assert walked.first_differing is not None
        assert walked.first_differing.day == date(2026, 9, 22)
        assert walked.last_agreeing is not None
        assert walked.last_agreeing.day == date(2026, 9, 20)

    def test_MovementOnTheCreationDay_IsCountedNotExcluded(self, opened):
        # The 800.00 deposit and the 3000.00 salary are both dated the 1st, the
        # creation day. A nil anchor ending the creation day itself would
        # exclude them and every balance would differ by 3800.00.
        walked = self.walk(opened)

        assert walked.opened is not None
        assert walked.opened.day < date(2026, 9, 1)
        assert walked.before_opening == 0
        assert walked.differing == []

    def test_Walk_WhenARowIsDatedBeforeTheAccountExisted_SaysSo(self, opened):
        early = replace(
            pay(MAIN, FEED, "f-early", 1000, 1, "Impossible"),
            value_date=date(2026, 8, 31),
            booking_date=date(2026, 8, 31),
        )
        opened.arrive(early)

        walked = self.walk(opened)

        assert walked.before_opening == 1

    def test_Opening_WhenTheFeedWasFirstAskedAfterTheCreation_IsNotNil(self, store, tmp_path):
        home = Household(store, MAP)
        home.arrive(*opened_feed_rows())
        import_statement(store, tmp_path, STATEMENT)
        land_evidence(store, asked_from="2026-09-05T00:00:00Z")
        # A fault before the 11th goes unseen again, as it did before.
        drop_row(store, MAIN, -2500, 4)

        walked = self.walk(home)
        opening = effective_opening(store, MAIN, families=families(home))

        assert walked.opened is None
        assert walked.evidence is not None
        assert walked.evidence.missing == (
            "the feed does not reach back to the opening: the earliest request held asked "
            "from 2026-09-05 and the account opened on 2026-09-01"
        )
        assert walked.readings[0].defines_opening is True
        assert walked.first_differing is None
        assert opening.opening_minor == -2500

    def test_Opening_WhenTheFeedWasAskedFromTheCreationMomentExactly_IsNil(
        self, store, tmp_path
    ):
        home = Household(store, MAP)
        home.arrive(*opened_feed_rows())
        import_statement(store, tmp_path, STATEMENT)
        land_evidence(store, asked_from=CREATED_AT)

        assert self.walk(home).opened is not None

    def test_Opening_WhenTheFeedWasAskedFromAMomentAfterTheCreation_IsNotNil(
        self, store, tmp_path
    ):
        # The same day, but one minute too late to have reached the creation.
        home = Household(store, MAP)
        home.arrive(*opened_feed_rows())
        import_statement(store, tmp_path, STATEMENT)
        land_evidence(store, asked_from="2026-09-01T09:31:00Z")

        assert self.walk(home).opened is None

    def test_Opening_WhenNoFeedRequestIsHeldForTheAccount_IsNotNil(self, store, tmp_path):
        home = Household(store, MAP)
        home.arrive(*opened_feed_rows())
        import_statement(store, tmp_path, STATEMENT)
        land_evidence(store, asked_from=None)

        walked = self.walk(home)

        assert walked.opened is None
        assert walked.evidence is not None
        assert "no feed request is held" in walked.evidence.missing

    def test_Opening_WhenAFeedRequestIsForAnotherCategory_IsNotNil(self, store, tmp_path):
        # A Space's feed reaching back says nothing about the main account's own.
        home = Household(store, MAP)
        home.arrive(*opened_feed_rows())
        import_statement(store, tmp_path, STATEMENT)
        land_evidence(store, asked_from=None)
        store.land_artefact(
            starling.artefact_for(
                json.dumps({"feedItems": []}).encode(),
                account_id="starling:cat-bills",
                kind="feed",
                origin=(
                    FEED_ORIGIN.replace("cat-main", "cat-bills")
                    + "?changesSince=2026-01-01T00:00:00Z"
                ),
            )
        )

        assert self.walk(home).opened is None

    def test_Opening_WhenNoCreationDateIsHeld_IsNotNilAndTheAbsorbedSentenceStays(
        self, store, tmp_path
    ):
        home = Household(store, MAP)
        home.arrive(*opened_feed_rows())
        import_statement(store, tmp_path, STATEMENT)
        land_evidence(store, created=None)

        walked = self.walk(home)

        assert walked.opened is None
        assert walked.evidence is not None
        assert walked.evidence.missing == "no creation date is held for the account"
        assert (
            "A fault dated before 2026-09-11 is absorbed into the opening and cannot be seen."
            in walked.opening_note
        )

    def test_Opening_WhenNothingAtAllIsLanded_IsNotNil(self, family):
        walked = TestTheFamilyWalk().walk(family)

        assert walked.opened is None
        assert "absorbed into the opening" in walked.opening_note

    def test_Opening_WhenTheProviderStatesTwoCreationDates_IsNotNil(self, store, tmp_path):
        home = Household(store, MAP)
        home.arrive(*opened_feed_rows())
        import_statement(store, tmp_path, STATEMENT)
        land_evidence(store, also_created="2026-08-01T09:30:00Z")

        walked = self.walk(home)

        assert walked.opened is None
        assert walked.evidence is not None
        assert "more than one creation date" in walked.evidence.missing

    def test_OpeningNote_WhenNilAnchorHeld_NeverSaysAFaultIsAbsorbed(self, opened):
        note = self.walk(opened).opening_note

        assert note == (
            "The account's history is held from its opening on 2026-09-01, so the opening "
            "is nil and every known balance is tested."
        )
        assert "absorbed" not in note

    def test_Opening_ForAnAccountWithNoSpaces_IsNotDerivedFromTheFamilyEvidence(
        self, store, tmp_path
    ):
        no_spaces = AccountMap([AccountBinding(MAIN, "starling", "acc-main")])
        Household(store, no_spaces).arrive(*opened_feed_rows())
        land_evidence(store)

        opening = effective_opening(store, MAIN, families=families_of(store, no_spaces))

        assert opening.family is None
        assert opening.readings == ()

    def test_Opening_WithTheAnchorAndNoStatedBalance_IsNilAndTestsNothing(self, store):
        home = Household(store, MAP)
        home.arrive(*opened_feed_rows())
        land_evidence(store)

        opening = effective_opening(store, MAIN, families=families(home))

        assert opening.opening_minor == 0
        assert opening.single_anchor is False
        assert opening.family is not None
        assert opening.family.anchors == 0


class TestTypedAndUnitemisedRowsInTheFamilySum:
    """A typed row is an ordinary row and is counted wherever it is typed. Rows
    are derived from stated balances (`derive_unitemised`) for a balance-only
    account alone; a Starling main account is not one, so it has none. Were one
    declared so, its derived rows are counted once, in the family's sum and in
    its own reading."""

    def walk(self, home: Household):
        walked = effective_opening(home.store, MAIN, families=families(home)).family
        assert walked is not None
        return walked

    def test_TypedRowInASpace_IsCountedInTheFamilySum(self, opened):
        record_typed_transaction(opened.store, BILLS, "2026-09-03", "out", "10.00", "Typed")

        walked = self.walk(opened)

        assert walked.first_differing is not None
        assert walked.first_differing.day == date(2026, 9, 11)
        assert walked.first_differing.difference_minor == 1000

    def test_StarlingMainAccount_HasNoUnitemisedRows(self, opened):
        record_stated_anchor(opened.store, MAIN, "2026-09-10", "3375.00")
        record_stated_anchor(opened.store, MAIN, "2026-09-20", "3100.00")

        opening = effective_opening(opened.store, MAIN, families=families(opened))

        assert opening.balance_only is False
        assert opening.unitemised == ()

    def test_MainDeclaredBalanceOnly_CountsItsUnitemisedRowOnceInTheFamilySum(self, opened):
        # Main held 3375.00 on the 10th (by hand, the household's table); stating
        # 3100.00 on the 20th leaves 185.00 unexplained after the grocer and the
        # transfer, derived as one row dated the 20th.
        opened.store.declare_account(
            AccountRecord(ref=AccountRef(MAIN), kind=BALANCE_ONLY_KIND)
        )
        record_stated_anchor(opened.store, MAIN, "2026-09-10", "3375.00")
        record_stated_anchor(opened.store, MAIN, "2026-09-20", "3100.00")

        opening = effective_opening(opened.store, MAIN, families=families(opened))

        assert [t.amount_minor for t in opening.unitemised] == [-18500]
        assert opening.family is not None
        assert opening.family.first_differing is not None
        assert opening.family.first_differing.day == date(2026, 9, 20)
        assert opening.family.first_differing.difference_minor == 18500


class TestSpaceTransfersWhoseOtherLegIsNotHeld:
    """A transfer to a Space whose rows are not held leaves the main account's
    leg with no partner, so the family cannot reach the whole account's balance.

    Hand working on the opened household: a 47.00 leg to a closed Space on the
    8th and an 11.00 leg on the 18th, neither with a held partner (the amounts
    avoid every statement payment, which a matcher would otherwise merge with
    them). The stated balance on the 11th is therefore 47.00 above what the
    rows predict, on the 15th still 47.00, and from the 20th 58.00.
    """

    GONE = "cat-closed"

    def lose(self, home: Household) -> None:
        home.arrive(
            leg(MAIN, -4700, 8, self.GONE, "f-gone-1"),
            leg(MAIN, -1100, 18, self.GONE, "f-gone-2"),
        )

    def walk(self, home: Household):
        walked = effective_opening(home.store, MAIN, families=families(home)).family
        assert walked is not None
        return walked

    def test_Walk_WhenLegsGoToAnUnheldSpace_DiffersFromTheFirstAnchorAfterThem(self, opened):
        self.lose(opened)

        walked = self.walk(opened)

        assert walked.first_differing is not None
        assert walked.first_differing.day == date(2026, 9, 11)
        assert [r.difference_minor for r in walked.differing] == [
            4700, 4700, 4700, 5800, 5800, 5800, 5800,
        ]
        assert walked.constant is False

    def test_Walk_WhenLegsGoToAnUnheldSpace_CountsThemAndDatesTheFirst(self, opened):
        self.lose(opened)

        walked = self.walk(opened)

        assert walked.unheld.legs == 2
        assert walked.unheld.first == date(2026, 9, 8)

    def test_Walk_WhenTheSpaceIsHeldAsAnAccount_ReportsNoUnheldLegs(self, opened):
        # The Bills Space is bound to its category: transfers naming it are held.
        opened.arrive(leg(MAIN, -100, 9, "cat-bills", "f-held"))

        assert self.walk(opened).unheld.legs == 0

    def test_Walk_WhenTheSpaceIsStillListedByTheProvider_ReportsNoUnheldLegs(self, opened):
        opened.store.land_artefact(
            starling.artefact_for(
                json.dumps({"savingsGoals": [{"savingsGoalUid": self.GONE}]}).encode(),
                account_id="starling:acc-main",
                kind="spaces",
                origin="https://api.example.com/api/v2/account/acc-main/spaces",
            )
        )
        self.lose(opened)

        assert self.walk(opened).unheld.legs == 0

    def test_Walk_WhenTheCounterpartyIsTheMainAccountsOwnCategory_ReportsNoUnheldLegs(
        self, opened
    ):
        opened.arrive(leg(MAIN, -100, 9, "cat-main", "f-self"))

        assert self.walk(opened).unheld.legs == 0

    def test_Walk_WhenEveryLegIsHeld_ReportsNoUnheldLegs(self, opened):
        walked = self.walk(opened)

        assert (walked.unheld.legs, walked.unheld.first) == (0, None)

    def test_Walk_WhenAnUnheldLegIsAVoidedRow_IsNotCounted(self, opened):
        opened.arrive(
            replace(
                leg(MAIN, -4700, 8, self.GONE, "f-gone-void"),
                status=TransactionStatus.VOID,
            )
        )

        assert self.walk(opened).unheld.legs == 0


class TestTheOpeningSentToActual:
    """The opening-balance row for the main account, with and without the anchor."""

    def openings(self, home: Household) -> dict[str, tuple[int, date]]:
        bindings = [
            ActualAccountBinding(MAIN, "act-main"),
            ActualAccountBinding(OTHER, "act-other"),
        ]
        return {
            o.canonical_id: (o.amount_minor, o.as_at)
            for o in opening_balances(home.store, bindings, families=families(home))
        }

    @staticmethod
    def add_other_account(home: Household) -> None:
        reconcile_batch(
            home.store,
            [pay(OTHER, "src-o", "o1", -100, 2, "X")],
            digest="other-arrival",
        )
        record_stated_anchor(home.store, OTHER, "2026-09-10", "250.00")

    def test_MainOpening_WithTheAnchor_IsNilAtTheEndOfTheDayBeforeCreation(self, opened):
        self.add_other_account(opened)

        sent = self.openings(opened)

        assert sent[MAIN] == (0, date(2026, 8, 31))

    def test_MainOpening_WithTheAnchor_DoesNotAbsorbAFaultBeforeTheFirstStatedBalance(
        self, opened
    ):
        drop_row(opened.store, MAIN, -2500, 4)

        assert self.openings(opened)[MAIN][0] == 0

    def test_MainOpening_WithoutTheAnchor_AbsorbsThatFaultAsItAlwaysDid(self, store, tmp_path):
        home = Household(store, MAP)
        home.arrive(*opened_feed_rows())
        import_statement(store, tmp_path, STATEMENT)
        drop_row(store, MAIN, -2500, 4)

        assert self.openings(home)[MAIN][0] == -2500

    def test_AnUnrelatedAccount_IsUnchangedWhetherOrNotTheAnchorIsHeld(self, store, tmp_path):
        with_anchor = Household(store, MAP)
        with_anchor.arrive(*opened_feed_rows())
        import_statement(store, tmp_path, STATEMENT)
        self.add_other_account(with_anchor)
        before = self.openings(with_anchor)[OTHER]

        land_evidence(store)
        after = self.openings(with_anchor)[OTHER]

        assert before == after == (25000 + 100, date(2026, 9, 1))


CSV_HEADER = "Date,Counter Party,Reference,Type,Amount (GBP),Balance (GBP),Notes"

#: The export's rows, oldest first, with the whole account's balance after each.
#: The 25th holds two rows, so the day's closing is the row no other row
#: starts from: 4735.00, whichever order the file lists them in.
EXPORT_ROWS = [
    "12/09/2026,Water Co,Water Co,FASTER PAYMENT,-50.00,3725.00,",
    "15/09/2026,Grocer,Grocer,FASTER PAYMENT,-70.00,3655.00,",
    "20/09/2026,Gym,Gym,FASTER PAYMENT,-30.00,3625.00,",
    "22/09/2026,Garage,Garage,FASTER PAYMENT,-90.00,3535.00,",
    "25/09/2026,Refund,Refund,FASTER PAYMENT,700.00,4235.00,",
    "25/09/2026,Bonus,Bonus,FASTER PAYMENT,500.00,4735.00,",
]
EXPORT_ANCHORS = [
    (11, 377500),
    (12, 372500),
    (15, 365500),
    (20, 362500),
    (22, 353500),
    (25, 473500),
]


def import_export(
    store: Store, directory: pathlib.Path, rows: Iterable[str], *, header: str = CSV_HEADER
) -> None:
    path = directory / "export.csv"
    path.write_text("\n".join([header, *rows]) + "\n", encoding="utf-8")
    import_file(store, path, account_id=MAIN, account_map=MAP)


class TestTheFamilyAnchorsACsvExportStates:
    @pytest.mark.parametrize("newest_first", [False, True], ids=["oldest-first", "newest-first"])
    def test_Export_YieldsTheBalanceAfterTheLastRowOfEachDay_WhateverTheFileOrder(
        self, store, tmp_path, newest_first
    ):
        Household(store, MAP).arrive(*feed_rows())
        import_export(
            store, tmp_path, list(reversed(EXPORT_ROWS)) if newest_first else EXPORT_ROWS
        )

        found = family_anchors(store, MAIN, families_of(store, MAP))

        assert [(a.day.day, a.balance_minor) for a in found.anchors] == EXPORT_ANCHORS
        assert {a.source for a in found.anchors} == {"starling-csv"}

    def test_Walk_WhenTheExportIsTheOnlySource_AgreesAndGivesMainItsTrueOpening(
        self, store, tmp_path
    ):
        home = Household(store, MAP)
        home.arrive(*feed_rows())
        import_export(store, tmp_path, EXPORT_ROWS)

        opening = effective_opening(store, MAIN, families=families_of(store, MAP))

        assert opening.family is not None
        assert (opening.family.anchors, len(opening.family.differing)) == (6, 0)
        assert opening.opening_minor == TRUE_MAIN_OPENING

    def test_Walk_WhenAnExportRowIsMissingFromTheStore_NamesTheDay(self, store, tmp_path):
        home = Household(store, MAP)
        home.arrive(*feed_rows())
        import_export(store, tmp_path, EXPORT_ROWS)
        drop_row(store, MAIN, -9000, 22)

        opening = effective_opening(store, MAIN, families=families_of(store, MAP))

        assert opening.family is not None
        assert opening.family.first_differing is not None
        assert opening.family.first_differing.day.day == 22
        assert opening.family.constant is True

    def test_ExportWithNoBalanceColumn_StatesNoAnchorAndStillImports(self, store, tmp_path):
        import_export(
            store,
            tmp_path,
            ["12/09/2026,Water Co,Water Co,FASTER PAYMENT,-50.00,"],
            header="Date,Counter Party,Reference,Type,Amount (GBP),Notes",
        )

        found = family_anchors(store, MAIN, families_of(store, MAP))

        assert found.anchors == ()
        assert len(store.transactions_for_account(MAIN)) == 1

    def test_ExportBalances_NeverChangeAnyRowsContentKey(self, tmp_path):
        """The balance column is read from the artefact on demand. Whether it
        is present or absent cannot move a row's content key, the half of its
        identity the file supplies. (Entity ids are minted per store, so two
        stores' ids are not comparable; the rebuild test below holds the ids of
        one store still.)"""
        with_balances = tmp_path / "a"
        without = tmp_path / "b"
        for directory in (with_balances, without):
            directory.mkdir()
        ids = {}
        for label, directory, header, rows in (
            ("with", with_balances, CSV_HEADER, EXPORT_ROWS),
            (
                "without",
                without,
                "Date,Counter Party,Reference,Type,Amount (GBP),Notes",
                [",".join([*r.split(",")[:5], ""]) for r in EXPORT_ROWS],
            ),
        ):
            with Store(directory / "s.sqlite3") as opened:
                import_export(opened, directory, rows, header=header)
                ids[label] = sorted(
                    (t.content_key, t.occurrence) for t in opened.transactions_for_account(MAIN)
                )

        assert len(ids["with"]) == 6
        assert ids["with"] == ids["without"]

    def test_Rebuild_KeepsEveryEntityIdOfAnExportWithBalances(self, store, tmp_path):
        import_export(store, tmp_path, EXPORT_ROWS)
        before = sorted(t.entity_id for t in store.transactions_for_account(MAIN))

        rebuild_from_raw(store, account_map=MAP)

        after = sorted(t.entity_id for t in store.transactions_for_account(MAIN))
        assert len(before) == 6
        assert after == before


class TestTheAggregatorsBalancesAreTheFamilysToo:
    """The aggregator is blind to the Bills Space in this map, so the bank's
    running balance is the family's.

    The household: the family held 1000.00 (all in main) before the 1st.
    The 1st moves 200.00 into Bills (both legs, from the feed); the 2nd pays in
    500.00; the 3rd pays a 50.00 water bill from Bills (the aggregator reports
    it under main, the feed under Bills, so the aggregator's copy is folded);
    the 4th pays 20.00 from main. The bank's running balance: 1500.00 after the
    2nd and 1430.00 after the 4th, so the family's balance at the end of the
    1st is 100000 and at the end of the 4th 143000.

    Main alone: 800.00 at the end of the 1st (100000 less Bills' 20000 is
    80000) and 1280.00 at the end of the 4th. Main's true opening is 100000.
    The old derivation took both balances as main's own, so main's opening was
    120000 and its check on the 4th differed by the 50.00 bill.
    """

    @staticmethod
    def record(ident: str, amount: str, day: int, who: str, running: str) -> dict:
        return {
            "transaction_id": f"volatile-{ident}",
            "normalised_provider_transaction_id": ident,
            "timestamp": f"2026-09-{day:02}T10:00:00Z",
            "description": who,
            "amount": amount,
            "currency": "GBP",
            "transaction_type": "DEBIT" if amount.startswith("-") else "CREDIT",
            "running_balance": {"amount": running, "currency": "GBP"},
        }

    def build(self, store: Store) -> Household:
        home = Household(store, MAP)
        home.arrive(
            pay(MAIN, FEED, "f-out", -20000, 1, "To Bills", internal=True),
            pay(BILLS, FEED, "f-in", 20000, 1, "From Main", internal=True),
            pay(BILLS, FEED, "f-water", -5000, 3, "Water Co"),
        )
        records = [
            self.record("tl-salary", "500.00", 2, "EMPLOYER", "1500.00"),
            self.record("tl-water", "-50.00", 3, "WATER CO DD", "1450.00"),
            self.record("tl-coffee", "-20.00", 4, "COFFEE", "1430.00"),
        ]
        artefact = truelayer.artefact_for(
            json.dumps({"results": records}).encode(), account_id="tl-main", kind="booked"
        )
        store.land_artefact(artefact)
        transactions = parse_artefact_transactions(
            "truelayer-booked", artefact.payload, MAIN, artefact.digest
        )
        reconcile_batch(store, transactions, digest=artefact.digest)
        home.settle()
        return home

    def test_BankBalances_AreFamilyAnchors_AndGiveMainItsTrueOpening(self, store):
        home = self.build(store)

        opening = effective_opening(store, MAIN, families=families(home))

        assert opening.family is not None
        # The opening before the first day, then the closing of each day the bank's chain
        # has one end for (`AccountReconciliation.balances`): the 2nd and the 4th. The 3rd
        # states nothing, because its only record is the water bill the fold holds as
        # a Space copy, so no booked row of the day is left to chain.
        assert [(r.day.day, r.balance_minor) for r in opening.family.readings] == [
            (1, 100000),
            (2, 150000),
            (4, 143000),
        ]
        assert opening.family.sources == ("truelayer",)
        assert opening.opening_minor == 100000
        assert [r.agrees for r in opening.readings[1:]] == [True, True]

    def test_BankBalances_WhenTreatedAsMainsOwn_GaveTheWrongOpening(self, store):
        # Without the account map every anchor is main's own: the measured old behaviour.
        self.build(store)

        opening = effective_opening(store, MAIN)

        assert opening.opening_minor == 100000 + 20000
        # The 2nd's closing agrees; the 4th's differs by the Space's 50.00 bill.
        assert [r.difference_minor for r in opening.readings[1:]] == [0, -5000]


class TestWhatTheFamilyReadingCosts:
    """The ledger page's statements are pinned (see test_ledger). Asking for the
    family reading adds a documented number for a main account with Spaces and
    nothing for any other account."""

    @staticmethod
    def statements(store: Store, ref: str, found: Families | None) -> int:
        issued: list[str] = []
        store.connection.set_trace_callback(issued.append)
        try:
            build_ledger(store, ref, "2026-09", bound=True, families=found)
        finally:
            store.connection.set_trace_callback(None)
        return sum(1 for sql in issued if sql.lstrip().upper().startswith("SELECT"))

    def test_MainAccountWithSpaces_CostsTheDocumentedExtraStatements(self, family):
        found = families(family)
        self.statements(family.store, MAIN, found)  # the statement is read once per process

        cost = self.statements(family.store, MAIN, found)

        # Two for the family's own listings, then one read of each Space's rows, and the
        # reading of the feed's times that any account the bank's own feed fills pays.
        assert cost == (
            QUERIES_PER_PAGE
            + ANCHOR_QUERIES
            + STATEMENT_CHECK_QUERIES
            + FAMILY_QUERIES
            + len(found.spaces_of(MAIN))
            + FEED_TIME_QUERIES
        )

    def test_SpaceAccount_CostsOnlyTheReadOfItsListingsMoreThanItDidWithoutFamilies(self, family):
        found = families(family)
        # The statement checks are held per store state and per set of Spaces, so each reading is
        # measured with its own held, as the second page of a process is.
        self.statements(family.store, BILLS, found)
        self.statements(family.store, BILLS, None)

        assert self.statements(family.store, BILLS, found) == (
            self.statements(family.store, BILLS, None) + SPACE_QUERIES
        )

    def test_DiscoveringTheFamilies_CostsTheDocumentedNumberOfStatements(self, family):
        issued: list[str] = []
        family.store.connection.set_trace_callback(issued.append)
        try:
            families(family)
        finally:
            family.store.connection.set_trace_callback(None)

        assert sum(1 for s in issued if s.lstrip().upper().startswith("SELECT")) == (
            FAMILY_DISCOVERY_QUERIES
        )


def drop_row(store: Store, account: str, minor: int, day: int) -> None:
    """Remove the one counted row of this amount from the account on that day.

    The row's sightings go with it, so the store holds no trace of the payment,
    which is how a row lost to a bad merge would look.
    """
    found = [
        t
        for t in store.transactions_for_account(account)
        if t.amount_minor == minor
        and t.value_date == date(2026, 9, day)
        and not t.status.is_history
    ]
    assert len(found) == 1, found
    entity = found[0].entity_id
    store.connection.execute("DELETE FROM transaction_sources WHERE entity_id = ?", (entity,))
    store.connection.execute("DELETE FROM transactions WHERE entity_id = ?", (entity,))
    store.connection.commit()
