"""What the store keeps of the numbers an account answers to.

KNOWN ANSWERS, decided before the first run (entities.md section 3a). Every number is invented.

  - A held account can answer to MORE THAN ONE number (a sort-code migration gives the same
    balance history a second): adding a second keeps the first, and both lead to the account.
  - One number answers to ONE account: adding it to a second account is refused and leaves both
    accounts as they were, and the refusal names the last four digits and no more.
  - Only a declared, held account can take a number: an undeclared name and an external account
    are refused.
  - A rebuild from raw leaves the numbers; a rename carries them to the new name.
  - A store stamped 31 (the release before) opens, and its external account's number is copied
    across, so a payment stating it is still a transfer to that account.
  - The accounts offered to take a number are the declared held ones, each once, never an
    external one.
"""

from __future__ import annotations

import pathlib
import sqlite3
from dataclasses import replace
from datetime import date

import pytest

from landing import rebuild_from_raw
from obdi.core.errors import DataError
from obdi.ingest.accounts import AccountRecord, AccountRef
from obdi.ingest.store import SCHEMA_VERSION, Store

SNAPSHOT = (
    pathlib.Path(__file__).resolve().parent.parent / "schema_history" / "28-account-identifiers.sql"
)
FIRST = "445566-99887766"
SECOND = "445577-11223344"
EXTERNAL = "778899-11112222"


@pytest.fixture
def store(tmp_path):
    with Store(tmp_path / "store.sqlite3") as opened:
        for ref in ("current-main", "savings-pot"):
            opened.declare_account(AccountRecord(ref=AccountRef(ref), label=ref))
        yield opened


class TestANumberOnAHeldAccount:
    def test_AddIdentifier_WhenNew_LeadsToTheAccount(self, store):
        assert store.add_account_identifier(AccountRef("savings-pot"), FIRST) is True

        assert store.declared_identifiers() == {FIRST: "savings-pot"}

    def test_AddIdentifier_WhenTheSameAccountTakesASecondNumber_KeepsBoth(self, store):
        store.add_account_identifier(AccountRef("savings-pot"), FIRST)

        store.add_account_identifier(AccountRef("savings-pot"), SECOND)

        assert store.declared_identifiers() == {FIRST: "savings-pot", SECOND: "savings-pot"}
        assert store.account_identifiers() == {"savings-pot": [FIRST, SECOND]}

    def test_AddIdentifier_WhenTheSameNumberIsAddedAgain_ChangesNothingAndSaysSo(self, store):
        store.add_account_identifier(AccountRef("savings-pot"), FIRST)

        assert store.add_account_identifier(AccountRef("savings-pot"), FIRST) is False

        assert store.account_identifiers() == {"savings-pot": [FIRST]}

    def test_AddIdentifier_WhenAnotherAccountHasTheNumber_IsRefusedAndBothAreUnchanged(
        self, store
    ):
        store.add_account_identifier(AccountRef("savings-pot"), FIRST)

        with pytest.raises(DataError) as refusal:
            store.add_account_identifier(AccountRef("current-main"), FIRST)

        assert "9887766" not in str(refusal.value) and "7766" in str(refusal.value)
        assert store.account_identifiers() == {"savings-pot": [FIRST]}

    def test_AddIdentifier_WhenTheAccountIsNotDeclared_IsRefusedAndWritesNothing(self, store):
        with pytest.raises(DataError, match="declare it first"):
            store.add_account_identifier(AccountRef("never-declared"), FIRST)

        assert store.declared_identifiers() == {}

    def test_AddIdentifier_WhenTheAccountIsExternal_IsRefused(self, store):
        store.declare_account(
            AccountRecord(
                ref=AccountRef("external-aaaa"), kind="external", label="Partner joint",
                identifier=EXTERNAL, external=True,
            )
        )

        with pytest.raises(DataError):
            store.add_account_identifier(AccountRef("external-aaaa"), SECOND)

        assert store.account_identifiers() == {"external-aaaa": [EXTERNAL]}


class TestANumberTypedByHand:
    def test_Add_WhenTypedWithSpacesAndHyphens_IsKeptInTheCanonicalForm(self, store):
        store.add_account_identifier(AccountRef("savings-pot"), "44 55-66  99887766")

        assert store.declared_identifiers() == {FIRST: "savings-pot"}

    @pytest.mark.parametrize("typed", ["", "4455-66", "445566-9988776", "abcdef-12345678"])
    def test_Add_WhenNotTheDigitsAUkAccountHas_IsRefusedWithTheSayingSentence(self, store, typed):
        with pytest.raises(DataError, match="six-digit sort code"):
            store.add_account_identifier(AccountRef("savings-pot"), typed)

        assert store.declared_identifiers() == {}

    def test_Add_WhenAnotherAccountHasTheNumber_IsRefusedNamingThatAccount(self, store):
        store.add_account_identifier(AccountRef("savings-pot"), FIRST)

        with pytest.raises(DataError, match="savings-pot"):
            store.add_account_identifier(AccountRef("current-main"), FIRST)

    def test_Add_WhenAnIbanOutsideTheUkIsTyped_IsKeptAsTheIbanItself(self, store):
        store.add_account_identifier(AccountRef("savings-pot"), "de89 3704 0044 0532 0130 00")

        assert store.declared_identifiers() == {"DE89370400440532013000": "savings-pot"}


class TestACardsLastFour:
    @pytest.mark.parametrize("typed", ["12345", "123", "12a4", "4111111111111111", ""])
    def test_AddCard_WhenNotExactlyFourDigits_IsRefusedAndNothingIsKept(self, store, typed):
        with pytest.raises(DataError, match="last 4 digits"):
            store.add_account_identifier(AccountRef("savings-pot"), typed, kind="card")

        assert store.identifier_entries(AccountRef("savings-pot")) == []

    def test_AddCard_WhenTwoDatedCardsShareAnAccount_ListsBothInDateOrder(self, store):
        store.add_account_identifier(
            AccountRef("savings-pot"), "9999", kind="card", valid_from=date(2025, 3, 1)
        )
        store.add_account_identifier(
            AccountRef("savings-pot"), "1111", kind="card",
            valid_from=date(2023, 1, 1), valid_to=date(2025, 2, 28),
        )

        entries = store.identifier_entries(AccountRef("savings-pot"))

        assert [(e.value, e.valid_from, e.valid_to) for e in entries] == [
            ("1111", date(2023, 1, 1), date(2025, 2, 28)),
            ("9999", date(2025, 3, 1), None),
        ]

    def test_AddCard_WhenTheSameFourBelongToAnotherAccount_IsAllowedAndJoinsNoRow(self, store):
        store.add_account_identifier(AccountRef("savings-pot"), "1234", kind="card")

        store.add_account_identifier(AccountRef("current-main"), "1234", kind="card")

        assert store.declared_identifiers() == {}
        assert store.account_identifiers() == {}

    def test_Add_WhenTheNumberEndsBeforeItBegins_IsRefused(self, store):
        with pytest.raises(DataError, match="cannot end before"):
            store.add_account_identifier(
                AccountRef("savings-pot"), "1234", kind="card",
                valid_from=date(2025, 3, 1), valid_to=date(2025, 1, 1),
            )

    def test_Remove_WhenTheEntryIsOnTheAccount_ForgetsItAndOnlyThat(self, store):
        store.add_account_identifier(AccountRef("savings-pot"), "1234", kind="card")
        store.add_account_identifier(AccountRef("savings-pot"), FIRST)
        (card, _number) = store.identifier_entries(AccountRef("savings-pot"))[::-1]

        assert store.remove_account_identifier(AccountRef("current-main"), card.id) is False
        assert store.remove_account_identifier(AccountRef("savings-pot"), card.id) is True

        assert [e.value for e in store.identifier_entries(AccountRef("savings-pot"))] == [FIRST]


class TestWhatSurvives:
    def test_Identifiers_WhenCardsAndAccountsAreDeclared_AllSurviveARebuildFromRaw(self, store):
        store.add_account_identifier(AccountRef("savings-pot"), FIRST)
        store.add_account_identifier(
            AccountRef("savings-pot"), "1234", kind="card", valid_from=date(2024, 1, 1)
        )

        rebuild_from_raw(store)

        assert [(e.kind, e.value) for e in store.identifier_entries(AccountRef("savings-pot"))] == [
            ("account", FIRST),
            ("card", "1234"),
        ]

    def test_Identifiers_WhenTheStoreIsRebuiltFromRaw_AreKept(self, store):
        store.add_account_identifier(AccountRef("savings-pot"), FIRST)

        rebuild_from_raw(store)

        assert store.declared_identifiers() == {FIRST: "savings-pot"}

    def test_Identifiers_WhenTheAccountIsRenamed_FollowItToTheNewName(self, store):
        store.add_account_identifier(AccountRef("savings-pot"), FIRST)
        record = store.declared_account(AccountRef("savings-pot"))
        assert record is not None

        store.declare_account(replace(record, ref=AccountRef("pot-renamed")))

        assert store.declared_identifiers() == {FIRST: "pot-renamed"}

    def test_Store_WhenStampedSchema31_OpensAndCopiesTheExternalNumberAcross(self, tmp_path):
        path = tmp_path / "old.sqlite3"
        legacy = sqlite3.connect(path)
        legacy.executescript(SNAPSHOT.read_text(encoding="utf-8"))
        legacy.commit()
        legacy.close()

        with Store(path) as opened:
            found = opened.declared_identifiers()
            stamped = opened.connection.execute(
                "SELECT value FROM obdi_meta WHERE key = 'schema_version'"
            ).fetchone()[0]

        assert found == {EXTERNAL: "external-aaaa"}
        assert stamped == str(SCHEMA_VERSION)


class TestTheAccountsOffered:
    def test_HeldAccountRefs_WhenAnExternalAccountIsDeclared_ListsEachHeldOneOnce(self, store):
        store.declare_account(
            AccountRecord(
                ref=AccountRef("external-aaaa"), kind="external", label="Partner joint",
                identifier=EXTERNAL, external=True,
            )
        )
        store.add_account_identifier(AccountRef("savings-pot"), FIRST)
        store.add_account_identifier(AccountRef("savings-pot"), SECOND)

        assert store.held_account_refs() == ["current-main", "savings-pot"]
