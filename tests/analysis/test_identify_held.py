"""Telling obdi that a number payments state is a HELD account's, and what that explains.

KNOWN ANSWERS, decided before the first run (entities.md section 3a). Every number is invented.

  - "current" pays 4 times to 445566-99887766 with no confirmed pair: ONE unheld account, 4
    payments, ending 7766, because "savings" has never stated its own number.
  - Pressing "It is this account" for "savings" removes it from the list, and the four rows are
    then named for "savings" (held:savings), paired or not.
  - A second number for the same account is added beside the first; a number another account
    already answers to is refused and neither account changes; an account that is not declared is
    refused and nothing is written.
  - The fold under an account lists the newest ten payments, newest first, and the count says how
    many more there are (13 payments: ten listed).
  - "current" has 24 months of rows and "newco" 5 (the last five). A transfer in month 10 stating
    newco's number, unpaired, is a transfer to newco and says newco's rows begin later, so the
    other leg is not held; a paired one in month 22 and an unpaired one in month 22 say nothing.
"""

from __future__ import annotations

from datetime import date
from typing import ClassVar

import pytest

from obdi.analysis.entities import HELD_PREFIX, name_rows
from obdi.analysis.external_accounts import (
    PAID_SHOWN,
    held_choices,
    identify_held,
    other_leg_notes,
    unheld_accounts,
)
from obdi.core.models import SourceTier, Transaction
from obdi.ingest.accounts import AccountRecord, AccountRef
from obdi.ingest.entity_records import EntityRefused
from obdi.ingest.store import Store
from obdi.read.account_names import AccountShown, AccountsShown

NUMBER = "445566-99887766"
SECOND_NUMBER = "445577-11223344"
MONTHS = [date(2025 + (m // 12), m % 12 + 1, 5) for m in range(24)]


def leg(
    entity: str, account: str, amount: int, when: date, text: str, party: str = ""
) -> Transaction:
    return Transaction(
        account_id=account,
        amount_minor=amount,
        value_date=when,
        booking_date=when,
        description=text,
        party_account=party,
        source="starling",
        tier=SourceTier.SYNTHETIC,
        entity_id=entity,
    )


def payments(count: int, number: str = NUMBER) -> list[Transaction]:
    return [
        leg(f"p{i}", "current", -1000 - i, MONTHS[i], f"TRANSFER {i}", number) for i in range(count)
    ]


@pytest.fixture
def store(tmp_path):
    with Store(tmp_path / "store.sqlite3") as opened:
        for ref in ("current", "savings"):
            opened.declare_account(AccountRecord(ref=AccountRef(ref), label=ref.title()))
        yield opened


def named(rows, store=None):
    external = store.declared_identifiers() if store is not None else None
    return name_rows(rows, [], external=external)[2]


def press(store, rows, ref):
    before = named(rows, store)
    offered, _declined = unheld_accounts(rows, before)
    return identify_held(store, rows, before, {"account": [offered[0].key], "held": [ref]})


class TestPressingItIsThisAccount:
    def test_UnheldAccounts_WhenFourPaymentsStateANumberNoAccountOwns_ListsOneAccountEnding7766(
        self,
    ):
        rows = payments(4)

        (offered,), declined = unheld_accounts(rows, named(rows))

        assert (offered.payments, offered.ending, declined) == (4, "7766", 0)

    def test_IdentifyHeld_WhenPressed_NamesTheFourRowsForTheAccountAndStopsOfferingThem(
        self, store
    ):
        rows = payments(4)

        said = press(store, rows, "savings")

        after = named(rows, store)
        assert {item.name for item in after} == {HELD_PREFIX + "savings"}
        assert unheld_accounts(rows, after) == ((), 0)
        assert "ending 7766" in said and "99887766" not in said

    def test_IdentifyHeld_WhenTheAccountTakesASecondNumber_BothNamePaymentsForIt(self, store):
        rows = [
            *payments(2),
            *(leg(f"s{i}", "current", -50, MONTHS[i], "OTHER", SECOND_NUMBER) for i in range(2)),
        ]

        press(store, rows, "savings")
        press(store, rows, "savings")

        assert sorted(store.account_identifiers()["savings"]) == [NUMBER, SECOND_NUMBER]
        assert {item.name for item in named(rows, store)} == {HELD_PREFIX + "savings"}

    def test_IdentifyHeld_WhenTheAccountIsNotDeclared_IsRefusedAndWritesNothing(self, store):
        rows = payments(4)

        with pytest.raises(EntityRefused, match="declare it first"):
            press(store, rows, "never-declared")

        assert store.declared_identifiers() == {}

    def test_IdentifyHeld_WhenNoAccountIsSaid_IsRefusedAndWritesNothing(self, store):
        rows = payments(4)
        before = named(rows, store)
        (offered,), _ = unheld_accounts(rows, before)

        with pytest.raises(EntityRefused):
            identify_held(store, rows, before, {"account": [offered.key]})

        assert store.declared_identifiers() == {}

    def test_IdentifyHeld_WhenAnotherAccountAlreadyHasTheNumber_IsRefusedAndNothingMoves(
        self, store
    ):
        rows = payments(4)
        press(store, rows, "savings")
        stale = unheld_accounts(rows, named(rows))[0][0].key

        with pytest.raises(EntityRefused):
            identify_held(
                store, rows, named(rows, store), {"account": [stale], "held": ["current"]}
            )

        assert store.account_identifiers() == {"savings": [NUMBER]}


class TestTheChoices:
    def test_HeldChoices_WhenAnExternalAccountExists_ListsEveryHeldOneOnceAndNoExternalOne(
        self, store
    ):
        store.declare_account(
            AccountRecord(
                ref=AccountRef("external-aaaa"), kind="external", label="Partner joint",
                identifier="778899-11112222", external=True,
            )
        )
        names = AccountsShown([AccountShown.named("current", "Current")])

        choices = held_choices(store, names)

        assert choices == (("current", "Current"), ("savings", "savings"))


class TestTheFold:
    def test_UnheldAccounts_WhenThirteenPaymentsStateANumber_ListsTheNewestTenNewestFirst(self):
        rows = payments(13)

        (offered,), _ = unheld_accounts(rows, named(rows))

        assert offered.payments == 13
        assert len(offered.paid) == PAID_SHOWN == 10
        assert [p.description for p in offered.paid] == [f"TRANSFER {i}" for i in range(12, 2, -1)]

    def test_UnheldAccounts_WhenFewPaymentsStateANumber_ListsThemAll(self):
        rows = payments(4)

        (offered,), _ = unheld_accounts(rows, named(rows))

        assert [p.description for p in offered.paid] == [f"TRANSFER {i}" for i in (3, 2, 1, 0)]


class TestWhenTheOtherLegCannotBeHeld:
    """Current has 24 months; newco's rows begin in month 20 (so 5 months, the last five)."""

    def setup(self, paired_late: bool):
        current_rows = [leg(f"c{m}", "current", -10, MONTHS[m], "PAY", "") for m in range(24)]
        early = leg("early", "current", -2500, MONTHS[10], "TO NEWCO", NUMBER)
        late = leg("late", "current", -2500, MONTHS[22], "TO NEWCO", NUMBER)
        arriving = leg("in", "newco", 2500, MONTHS[22], "FROM CURRENT")
        rows = [*current_rows, early, late, arriving]
        pairs = [("late", "in")] if paired_late else []
        names = name_rows(rows, pairs, external={NUMBER: "newco"})[2]
        return rows, names, pairs

    FIRST_ROWS: ClassVar[dict[str, date]] = {"current": MONTHS[0], "newco": MONTHS[19]}
    LABELS = AccountsShown([AccountShown.named("newco", "Newco")])

    def test_OtherLegNotes_WhenTheTransferIsBeforeTheOtherAccountsRows_SaysWhy(self):
        rows, names, pairs = self.setup(paired_late=True)

        notes = other_leg_notes(rows, names, pairs, self.FIRST_ROWS, self.LABELS)

        assert notes == {
            "early": f"Newco's rows begin {MONTHS[19].isoformat()}, so the other leg is not held"
        }

    def test_OtherLegNotes_WhenTheTransferIsPairedInsideTheOtherAccountsRows_SaysNothing(self):
        rows, names, pairs = self.setup(paired_late=True)

        assert "late" not in other_leg_notes(rows, names, pairs, self.FIRST_ROWS, self.LABELS)

    def test_OtherLegNotes_WhenTheTransferIsUnpairedButInsideTheOtherAccountsRows_SaysNothing(
        self,
    ):
        rows, names, pairs = self.setup(paired_late=False)

        assert "late" not in other_leg_notes(rows, names, pairs, self.FIRST_ROWS, self.LABELS)

    def test_OtherLegNotes_WhenTheOtherAccountHasNoRowsAtAll_SaysNothing(self):
        rows, names, pairs = self.setup(paired_late=False)

        assert other_leg_notes(rows, names, pairs, {"current": MONTHS[0]}, self.LABELS) == {}
