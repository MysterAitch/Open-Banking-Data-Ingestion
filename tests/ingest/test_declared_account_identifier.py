"""An account of the owner's that obdi holds no source for can be declared, with its own identifier.

KNOWN ANSWERS, decided before the first run (entities.md section 3a). Every sort code and number is
invented.

  - A store made by the release before this one (schema 27, `declared_accounts` without the two
    columns) opens, keeps the account it held, and reads it as an ordinary held account with no
    identifier.
  - An external account declared with an identifier and a label reads back with both, is NOT in
    `declared_accounts()` (the registry every page of held accounts reads), IS in
    `external_accounts()`, and its identifier finds its name through `external_identifiers()`.
  - An ordinary account declared with no identifier reads back with none; one declared with an
    identifier keeps it and is still an ordinary, listed account.
  - Editing a declared account keeps the stable id it was minted with, and an external account
    survives the rebuild from raw.
"""

from __future__ import annotations

import pathlib
import sqlite3
from dataclasses import replace

import pytest

from landing import rebuild_from_raw
from obdi.ingest.accounts import AccountRecord, AccountRef
from obdi.ingest.store import SCHEMA_VERSION, Store

SNAPSHOT = (
    pathlib.Path(__file__).resolve().parent.parent
    / "schema_history"
    / "23-declared-account-identifier.sql"
)
IDENTIFIER = "112233-12345678"
OTHER_IDENTIFIER = "445566-87654321"


@pytest.fixture
def store(tmp_path):
    with Store(tmp_path / "store.sqlite3") as opened:
        yield opened


def external(ref: str, label: str, identifier: str) -> AccountRecord:
    return AccountRecord(
        ref=AccountRef(ref), kind="external", label=label, identifier=identifier, external=True
    )


class TestAStoreFromTheReleaseBeforeThisOne:
    def test_Store_WhenStampedSchema27_OpensAndKeepsTheAccountItHeld(self, tmp_path):
        path = tmp_path / "old.sqlite3"
        legacy = sqlite3.connect(path)
        legacy.executescript(SNAPSHOT.read_text(encoding="utf-8"))
        legacy.commit()
        legacy.close()

        with Store(path) as store:
            held = store.declared_accounts()
            stamped = store.connection.execute(
                "SELECT value FROM obdi_meta WHERE key = 'schema_version'"
            ).fetchone()[0]

        assert [(str(r.ref), r.label, r.identifier, r.external) for r in held] == [
            ("current-main", "Current", "", False)
        ]
        assert stamped == str(SCHEMA_VERSION)


class TestAnExternalAccount:
    def test_ExternalAccount_WhenDeclared_ReadsBackWithItsIdentifierAndLabel(self, store):
        store.declare_account(external("external-aaaa", "Partner joint", IDENTIFIER))

        found = store.external_accounts()

        assert [(str(r.ref), r.label, r.identifier, r.external) for r in found] == [
            ("external-aaaa", "Partner joint", IDENTIFIER, True)
        ]

    def test_ExternalAccount_WhenDeclared_IsNotInTheRegistryOfHeldAccounts(self, store):
        store.declare_account(external("external-aaaa", "Partner joint", IDENTIFIER))
        store.declare_account(AccountRecord(ref=AccountRef("current-main"), label="Current"))

        assert [str(r.ref) for r in store.declared_accounts()] == ["current-main"]

    def test_ExternalAccount_WhenAskedForByName_IsStillFound(self, store):
        store.declare_account(external("external-aaaa", "Partner joint", IDENTIFIER))

        found = store.declared_account(AccountRef("external-aaaa"))

        assert found is not None and found.external and found.identifier == IDENTIFIER

    def test_ExternalIdentifiers_WhenTwoAreDeclared_MapEachIdentifierToItsOwnAccount(self, store):
        store.declare_account(external("external-aaaa", "Partner joint", IDENTIFIER))
        store.declare_account(external("external-bbbb", "Pension pot", OTHER_IDENTIFIER))

        assert store.external_identifiers() == {
            IDENTIFIER: "external-aaaa",
            OTHER_IDENTIFIER: "external-bbbb",
        }

    def test_ExternalIdentifiers_WhenNothingIsExternal_AreEmpty(self, store):
        store.declare_account(
            AccountRecord(ref=AccountRef("current-main"), label="Current", identifier=IDENTIFIER)
        )

        assert store.external_identifiers() == {}

    def test_ExternalAccount_WhenTheStoreIsRebuiltFromRaw_IsUntouched(self, store):
        store.declare_account(external("external-aaaa", "Partner joint", IDENTIFIER))

        rebuild_from_raw(store)

        assert store.external_identifiers() == {IDENTIFIER: "external-aaaa"}

    def test_ExternalAccount_WhenRelabelled_KeepsItsStableIdAndIdentifier(self, store):
        first = store.declare_account(external("external-aaaa", "Partner joint", IDENTIFIER))

        again = store.declare_account(replace(first, label="Joint with partner"))

        assert again.stable_id == first.stable_id
        (found,) = store.external_accounts()
        assert (found.label, found.identifier) == ("Joint with partner", IDENTIFIER)


class TestAnIdentifierOnAHeldAccount:
    def test_HeldAccount_WhenDeclaredWithNoIdentifier_ReadsBackWithNone(self, store):
        store.declare_account(AccountRecord(ref=AccountRef("current-main"), label="Current"))

        (found,) = store.declared_accounts()

        assert found.identifier == "" and not found.external

    def test_HeldAccount_WhenDeclaredWithAnIdentifier_KeepsItAndIsStillListed(self, store):
        store.declare_account(
            AccountRecord(ref=AccountRef("current-main"), label="Current", identifier=IDENTIFIER)
        )

        (found,) = store.declared_accounts()

        assert (found.identifier, found.external) == (IDENTIFIER, False)
