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
from datetime import date

import pytest

from obdi.accounts import AccountBinding, AccountMap
from obdi.balance_anchors import FAMILY, effective_opening
from obdi.balance_anchors import STATEMENT as STATEMENT_BASIS
from obdi.family_anchors import Families, families_of, family_anchors
from obdi.ingest import import_file, reconcile_batch
from obdi.ledger import (
    ANCHOR_QUERIES,
    FAMILY_DISCOVERY_QUERIES,
    FAMILY_QUERIES,
    QUERIES_PER_PAGE,
    build_ledger,
)
from obdi.providers import truelayer
from obdi.rebuild import parse_artefact_transactions, rebuild_from_raw
from obdi.store import Store
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
        assert [(r.day.day, r.balance_minor) for r in opening.family.readings] == [
            (1, 100000),
            (4, 143000),
        ]
        assert opening.family.sources == ("truelayer",)
        assert opening.opening_minor == 100000
        assert opening.readings[1].agrees is True

    def test_BankBalances_WhenTreatedAsMainsOwn_GaveTheWrongOpening(self, store):
        # Without the account map every anchor is main's own: the measured old behaviour.
        self.build(store)

        opening = effective_opening(store, MAIN)

        assert opening.opening_minor == 100000 + 20000
        assert opening.readings[1].difference_minor == -5000


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

        # Two for the family's own listings, then one read of each Space's rows.
        assert cost == (
            QUERIES_PER_PAGE + ANCHOR_QUERIES + FAMILY_QUERIES + len(found.spaces_of(MAIN))
        )

    def test_SpaceAccount_CostsNoMoreThanItDidWithoutFamilies(self, family):
        found = families(family)

        assert self.statements(family.store, BILLS, found) == self.statements(
            family.store, BILLS, None
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
