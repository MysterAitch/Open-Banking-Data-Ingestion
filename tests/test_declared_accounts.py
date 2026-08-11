"""The account registry lives in the store, not in a file on the host.

Declaring an account used to mean editing JSON on the Docker host, so the
one thing the system is ABOUT - which accounts exist - was the only thing
it could not be told from its own pages. The registry moves into the
store, where a page can write it inside a transaction, where the schema
ladder covers it, and where the checked-in historical shapes prove an
upgrade carries an existing deployment's accounts across.

Two properties get the hardest tests here, because both fail silently:

DECLARED STATE IS NOT DERIVED STATE. A rebuild wipes the transactions,
the sightings and the review queue and replays layer 0 to re-derive them.
An account cannot be re-derived from artefacts - a mortgage with no feed
and cash in a tin have no artefacts at all - so anything that lets the
wipe reach the registry destroys work no replay can bring back. This
project already has the scar of that class: binding an account re-minted
every entity id and orphaned every hand-entered category.

AN EMPTY REGISTRY IS A STATEMENT. "No accounts are declared" and "the
registry could not be read" look identical from every angle downstream,
and the consequence of confusing them is a statement filed against the
wrong account, or against nothing. So a registry file that cannot be read
refuses loudly instead of reading as empty.
"""

from __future__ import annotations

import json
import pathlib
import sqlite3
import typing
from dataclasses import replace
from datetime import date

import pytest

from obdi.accounts import (
    AccountId,
    AccountRecord,
    AccountRef,
    LimitWindow,
    RateWindow,
    account_id_well_formed,
    mint_account_id,
    read_registry_file,
)
from obdi.errors import DataError
from obdi.store import SCHEMA_VERSION, Store

SCHEMA_HISTORY = pathlib.Path(__file__).resolve().parent / "schema_history"

#: The shape in the wild immediately before the registry moved into the
#: store - the one a deployment that keeps its accounts in a file is
#: actually upgraded from.
SHAPE_BEFORE_THE_MOVE = SCHEMA_HISTORY / "18-artefact-origins.sql"

#: One account with EVERY field populated, several windows of each kind,
#: and both lifecycle dates. A field that silently fails to persist is the
#: failure this exists to catch, so nothing here is left at its default.
FULL_RECORD = AccountRecord(
    ref=AccountRef("halifax-clarity-credit-card"),
    kind="credit-card",
    label="Halifax Clarity",
    parent=AccountRef("halifax-current"),
    opened=date(2025, 7, 1),
    closed=date(2026, 3, 31),
    limits=(
        LimitWindow("credit", date(2025, 7, 1), None, 500000),
        LimitWindow("credit", date(2026, 1, 1), date(2026, 7, 1), 750000),
        LimitWindow("cash", None, date(2026, 7, 1), 100000),
    ),
    rates=(
        RateWindow("purchase", date(2025, 7, 1), date(2026, 7, 1), 0.0),
        RateWindow("cash", date(2025, 7, 1), None, 29.9),
        RateWindow("balance-transfer", None, None, 22.45),
    ),
)

FEEDLESS_RECORD = AccountRecord(
    ref=AccountRef("piggy-bank"),
    kind="cash",
    label="Piggy bank",
)


@pytest.fixture
def store(tmp_path):
    with Store(tmp_path / "store.sqlite3") as opened:
        yield opened


def _without_id(record: AccountRecord) -> AccountRecord:
    """A stored record as it was declared, so the two can be compared."""
    return replace(record, stable_id=None)


class TestDeclaringAnAccount:
    """Declaring is the act the whole page being built next performs."""

    def test_AnAccount_WhenDeclaredWithEveryFieldPopulated_ReadsBackIdentical(
        self, store
    ):
        """The whole record, not a sample of it: a field that quietly fails
        to persist looks exactly like a field nobody filled in."""
        stored = store.declare_account(FULL_RECORD)

        assert store.declared_account(FULL_RECORD.ref) == stored
        assert _without_id(stored) == FULL_RECORD

    def test_AnAccount_WhenDeclaredWithNothingButAName_ReadsBackWithNoWindows(
        self, store
    ):
        """Cash in a tin: no kind, no dates, no limits, no feed and no
        prospect of one. The registry is what lets it exist at all."""
        stored = store.declare_account(AccountRecord(ref=AccountRef("piggy-bank")))

        assert _without_id(stored) == AccountRecord(ref=AccountRef("piggy-bank"))
        assert stored.limits == ()
        assert stored.rates == ()

    def test_TheRegistry_WhenNothingHasBeenDeclared_IsEmpty(self, store):
        assert store.declared_accounts() == []

    def test_TheRegistry_WhenSeveralAccountsAreDeclared_HoldsEveryOne(self, store):
        store.declare_account(FULL_RECORD)
        store.declare_account(FEEDLESS_RECORD)

        assert [record.ref for record in store.declared_accounts()] == [
            "halifax-clarity-credit-card",
            "piggy-bank",
        ]

    def test_AnAccount_WhenAskedForByANameNobodyDeclared_IsAbsentRatherThanBlank(
        self, store
    ):
        store.declare_account(FULL_RECORD)

        assert store.declared_account(AccountRef("never-declared")) is None

    def test_AnAccount_WhenItsWindowsAreEditedDownToOne_DoesNotKeepTheOldOnes(
        self, store
    ):
        """Editing is re-declaring. Windows that accumulate rather than
        replace would show a limit the account no longer has, beside the
        one it does, with nothing to say which is current."""
        store.declare_account(FULL_RECORD)

        stored = store.declare_account(
            replace(
                FULL_RECORD,
                label="Halifax Clarity (closed)",
                limits=(LimitWindow("credit", date(2026, 1, 1), None, 750000),),
                rates=(),
            )
        )

        assert store.declared_account(FULL_RECORD.ref) == stored
        assert len(stored.limits) == 1
        assert stored.rates == ()
        assert stored.label == "Halifax Clarity (closed)"

    def test_AnAccount_WhenForgotten_TakesItsWindowsWithIt(self, store):
        """A window belonging to no account is meaningless, and one left
        behind would attach itself to the next account declared under the
        same name."""
        stored = store.declare_account(FULL_RECORD)

        assert store.forget_account(stored.stable_id) is True

        redeclared = store.declare_account(AccountRecord(ref=FULL_RECORD.ref))
        assert redeclared.limits == ()
        assert redeclared.rates == ()
        # Orphaned windows are invisible from every other angle - the
        # account simply looks as though it never had any - so the only
        # place that can say they went is a count of what is left.
        assert (
            store.connection.execute(
                "SELECT COUNT(*) FROM declared_account_limits"
            ).fetchone()[0]
            == 0
        )

    def test_ForgettingAnAccount_ThatWasNeverDeclared_SaysSoRatherThanPretending(
        self, store
    ):
        assert store.forget_account(mint_account_id()) is False


class TestTheStableIdentity:
    """The id everything will eventually join on, and the name a person
    reads, are different things. Conflating them is why renaming an
    account in this project has historically broken references to it.
    """

    def test_AnAccount_WhenItsDisplayNameIsChanged_KeepsTheIdItWasMintedWith(
        self, store
    ):
        stored = store.declare_account(FULL_RECORD)

        renamed = store.declare_account(replace(stored, label="Clarity card"))

        assert renamed.stable_id == stored.stable_id
        assert renamed.label == "Clarity card"

    def test_AnAccount_WhenItsCanonicalNameIsChanged_KeepsTheIdItWasMintedWith(
        self, store
    ):
        """The canonical name is still what every reference resolves
        through, so changing it has consequences elsewhere - but it must
        not change WHICH account this is."""
        stored = store.declare_account(FULL_RECORD)

        renamed = store.declare_account(
            replace(stored, ref=AccountRef("halifax-clarity"))
        )

        assert renamed.stable_id == stored.stable_id
        assert store.declared_account(AccountRef("halifax-clarity")) == renamed
        assert store.declared_account(FULL_RECORD.ref) is None

    def test_ARedeclaredAccount_WhenOnlyItsNameIsKnown_KeepsTheExistingId(self, store):
        """Re-importing the registry file, which carries no stable ids,
        must not mint a second identity for an account already declared."""
        stored = store.declare_account(FULL_RECORD)

        again = store.declare_account(FULL_RECORD)

        assert again.stable_id == stored.stable_id
        assert len(store.declared_accounts()) == 1

    def test_TwoAccounts_WhenDeclaredSeparately_NeverShareAnId(self, store):
        first = store.declare_account(FULL_RECORD)
        second = store.declare_account(FEEDLESS_RECORD)

        assert first.stable_id != second.stable_id

    def test_AMintedId_CarriesItsPrefixAndPassesItsOwnCheck(self):
        minted = mint_account_id()

        assert minted.startswith("acc_")
        assert account_id_well_formed(minted)

    def test_AMintedId_WhenOneCharacterIsWrong_IsRefusedByTheCheck(self):
        """Nobody types one of these, but plenty of things copy them -
        a URL, a log line, a support message read over the phone. A
        mistyped id that matched nothing would read as "no such account";
        one that matched the WRONG account would be worse."""
        minted = mint_account_id()
        wrong = "0" if minted[4] != "0" else "1"
        corrupted = minted[:4] + wrong + minted[5:]

        assert account_id_well_formed(minted) is True
        assert account_id_well_formed(corrupted) is False

    def test_AMintedId_WhenTwoCharactersAreTransposed_IsRefusedByTheCheck(self):
        """The commonest way a copied identifier goes wrong, and the one a
        plain checksum is blind to."""
        minted = next(
            candidate
            for candidate in (mint_account_id() for _ in range(50))
            if candidate[4] != candidate[5]
        )
        transposed = minted[:4] + minted[5] + minted[4] + minted[6:]

        assert account_id_well_formed(transposed) is False

    def test_AMintedId_WhenTruncated_IsRefusedByItsLength(self):
        assert account_id_well_formed(mint_account_id()[:-1]) is False

    def test_AnIdWithoutThePrefix_IsNotAnAccountIdAtAll(self):
        """The prefix is what says which kind of thing an identifier names
        when one turns up on its own in a log line."""
        minted = mint_account_id()

        assert account_id_well_formed(minted.removeprefix("acc_")) is False

    def test_AnAccount_WhenDeclaredWithAnIdThatFailsItsCheck_IsRefused(self, store):
        """A corrupted id must not quietly declare a SECOND account
        alongside the one it was meant to edit."""
        minted = mint_account_id()
        wrong = "0" if minted[4] != "0" else "1"
        corrupted = AccountId(minted[:4] + wrong + minted[5:])

        with pytest.raises(DataError):
            store.declare_account(replace(FULL_RECORD, stable_id=corrupted))

        assert store.declared_accounts() == []

    def test_AnAccount_WhenRenamedOntoANameAnotherAccountHolds_IsRefused(self, store):
        """Two accounts under one canonical name would send one account's
        rows to the other, and the name is still what everything joins on."""
        store.declare_account(FULL_RECORD)
        piggy = store.declare_account(FEEDLESS_RECORD)

        with pytest.raises(DataError):
            store.declare_account(replace(piggy, ref=FULL_RECORD.ref))

        assert len(store.declared_accounts()) == 2

    def test_MintedIds_AreNotReusedAcrossManyMints(self):
        assert len({mint_account_id() for _ in range(500)}) == 500


class TestTheTwoIdentifiersStayDistinctTypes:
    """The stable id and the canonical name are both account-shaped
    strings, so passing one where the other belongs produces a
    plausible-looking wrong answer rather than an error. They are separate
    types for that reason, and mypy - a gate here - refuses the confusion
    for code nobody has written yet.

    Read off the resolved annotations rather than the source text, so this
    holds a guarantee rather than a formatting habit: loosening any of
    these back to a bare str fails here, which is the drift worth
    catching.
    """

    def test_TheRegistry_DistinguishesTheStableIdFromTheCanonicalName(self):
        hints = typing.get_type_hints(AccountRecord)

        assert hints["ref"] is AccountRef
        assert hints["parent"] == AccountRef | None
        assert hints["stable_id"] == AccountId | None

    def test_TheStore_TakesACanonicalNameWhereItLooksOneUp(self):
        hints = typing.get_type_hints(Store.declared_account)

        assert hints["ref"] is AccountRef

    def test_TheStore_TakesAStableIdWhereItDeletes(self):
        """Forgetting by the renameable name would delete whichever
        account happens to hold that name today."""
        hints = typing.get_type_hints(Store.forget_account)

        assert hints["stable_id"] is AccountId

    def test_TheAccountMap_AnswersInCanonicalNames(self):
        from obdi.accounts import AccountMap

        assert typing.get_type_hints(AccountMap.resolve)["return"] is AccountRef
        assert typing.get_type_hints(AccountMap.record)["ref"] is AccountRef


def _land_a_statement(store: Store) -> None:
    """One artefact's worth of layer 0, so a rebuild has work to do.

    Amounts and names are invented; nothing here is anyone's money.
    """
    from obdi.providers.truelayer import artefact_for

    body = json.dumps(
        {
            "results": [
                {
                    "transaction_id": "t-1",
                    "normalised_provider_transaction_id": "txn-aaa",
                    "timestamp": "2026-07-01T00:00:00Z",
                    "amount": -12.34,
                    "currency": "GBP",
                    "description": "COFFEE SHOP",
                },
                {
                    "transaction_id": "t-2",
                    "normalised_provider_transaction_id": "txn-bbb",
                    "timestamp": "2026-07-02T00:00:00Z",
                    "amount": -40.00,
                    "currency": "GBP",
                    "description": "SUPERMARKET",
                },
            ],
            "status": "Succeeded",
        }
    ).encode()
    store.land_artefact(
        artefact_for(
            body,
            account_id="acc-1",
            kind="booked",
            requested="from=2026-06-01&to=2026-07-31",
            account_ref="halifax-current",
        )
    )


class TestDeclaredAccountsSurviveARebuild:
    """The important one.

    A rebuild wipes the derived layers and replays layer 0. Accounts are
    DECLARED, not derived: there is no artefact to replay them from, so a
    wipe that reached them would destroy the registry outright and the
    rebuild would report success.
    """

    def test_DeclaredAccounts_WhenTheStoreIsRebuiltFromRaw_AreUntouched(self, store):
        from obdi.rebuild import rebuild_from_raw

        store.declare_account(FULL_RECORD)
        store.declare_account(FEEDLESS_RECORD)
        _land_a_statement(store)
        before = store.declared_accounts()

        report = rebuild_from_raw(store)

        # The rebuild really did the work - otherwise this asserts nothing.
        assert report.artefacts_replayed == 1
        assert len(store.all_transactions()) == 2
        assert store.declared_accounts() == before

    def test_DerivedRowsWithNoEvidence_AreWipedWhileTheRegistryIsNot(self, store):
        """The contrast, in one test: a transaction no artefact accounts
        for goes, because the rebuild re-derives the whole layer from raw.
        An account no artefact accounts for stays, because nothing could
        ever re-derive it.
        """
        from obdi.ingest import reconcile_batch
        from obdi.models import SourceTier, Transaction
        from obdi.rebuild import rebuild_from_raw

        store.declare_account(FULL_RECORD)
        _land_a_statement(store)
        reconcile_batch(
            store,
            [
                Transaction(
                    account_id="piggy-bank",
                    amount_minor=-500,
                    currency="GBP",
                    value_date=date(2026, 6, 1),
                    booking_date=date(2026, 6, 1),
                    description="TIN OF COINS",
                    source="manual",
                    source_id="manual-1",
                    tier=SourceTier.SYNTHETIC,
                    content_key="key-tin",
                )
            ],
            digest="digest-with-no-artefact",
        )
        assert any(t.account_id == "piggy-bank" for t in store.all_transactions())

        rebuild_from_raw(store)

        assert not any(t.account_id == "piggy-bank" for t in store.all_transactions())
        assert _without_id(store.declared_account(FULL_RECORD.ref)) == FULL_RECORD

    def test_DeclaredAccounts_WhenTheRebuildIsRunFromTheCommandLine_AreUntouched(
        self, tmp_path, monkeypatch
    ):
        """End to end down the route a person actually presses, which is
        also the route that assembles the account map."""
        from obdi.cli import main

        monkeypatch.delenv("OBDI_ACCOUNT_MAP", raising=False)
        path = tmp_path / "store.sqlite3"
        with Store(path) as opened:
            opened.declare_account(FULL_RECORD)
            opened.declare_account(FEEDLESS_RECORD)
            _land_a_statement(opened)
            before = opened.declared_accounts()

        assert main(["--db", str(path), "rebuild", "--yes"]) == 0

        with Store(path) as reopened:
            assert reopened.declared_accounts() == before
            assert len(reopened.all_transactions()) == 2


def _store_on_the_old_shape(path: pathlib.Path) -> pathlib.Path:
    legacy = sqlite3.connect(path)
    legacy.executescript(SHAPE_BEFORE_THE_MOVE.read_text(encoding="utf-8"))
    legacy.commit()
    legacy.close()
    return path


def _reopen_as_an_upgrade(path: pathlib.Path) -> None:
    """Put the store back on an older schema version, which is what every
    later release does to it: the whole migration ladder runs again."""
    connection = sqlite3.connect(path)
    connection.execute("UPDATE obdi_meta SET value = '0' WHERE key = 'schema_version'")
    connection.commit()
    connection.close()


#: What a deployment's file actually holds - several accounts, populated
#: windows, and the other keys the same file carries.
REGISTRY_FILE = {
    "accounts": [
        {
            "id": "starling-personal",
            "kind": "current",
            "label": "Personal (Starling)",
            "opened": "2019-01-17",
        },
        {
            "id": "halifax-clarity-credit-card",
            "kind": "credit-card",
            "label": "Halifax Clarity",
            "parent": "halifax-current",
            "opened": "2025-07-01",
            "closed": "2026-03-31",
            "limits": [
                {"kind": "credit", "from": "2025-07-01", "amount_minor": 500000},
                {
                    "kind": "credit",
                    "from": "2026-01-01",
                    "to": "2026-07-01",
                    "amount_minor": 750000,
                },
                {"kind": "cash", "to": "2026-07-01", "amount_minor": 100000},
            ],
            "rates": [
                {
                    "kind": "purchase",
                    "from": "2025-07-01",
                    "to": "2026-07-01",
                    "annual_percent": 0,
                },
                {"kind": "cash", "from": "2025-07-01", "annual_percent": 29.9},
                {"kind": "balance-transfer", "annual_percent": 22.45},
            ],
        },
        {
            "id": "hsbc-old-current",
            "kind": "current",
            "label": "Old HSBC current",
            "opened": "2008-09-01",
            "closed": "2016-05-31",
        },
        {"id": "piggy-bank", "kind": "cash", "label": "Piggy bank"},
    ],
    "bindings": [
        {
            "canonical_id": "starling-personal",
            "source": "starling",
            "provider_account_id": "uid-1",
        }
    ],
    "actual": [],
}


@pytest.fixture
def registry_file(tmp_path, monkeypatch) -> pathlib.Path:
    path = tmp_path / "accounts.json"
    path.write_text(json.dumps(REGISTRY_FILE), encoding="utf-8")
    monkeypatch.setenv("OBDI_ACCOUNT_MAP", str(path))
    return path


class TestUpgradingADeploymentThatKeptItsAccountsInAFile:
    """A migration is only proven on a store that HAS the problem.

    Not on a fresh one, where the import has nothing to import: the
    previous artefact migration carried its own stale copy of a table
    definition and would have failed on the very store it existed to
    rescue, which is why the "before" here is built from the shape that
    actually shipped.
    """

    def test_Upgrade_WhenAccountsWereDeclaredInTheFile_BringsEveryOneIntoTheStore(
        self, tmp_path, registry_file
    ):
        path = _store_on_the_old_shape(tmp_path / "live.sqlite3")

        with Store(path) as store:
            declared = store.declared_accounts()

        assert [record.ref for record in declared] == [
            "halifax-clarity-credit-card",
            "hsbc-old-current",
            "piggy-bank",
            "starling-personal",
        ]

    def test_Upgrade_WhenAnAccountCarriedWindowsAndDates_KeepsEveryFieldIntact(
        self, tmp_path, registry_file
    ):
        path = _store_on_the_old_shape(tmp_path / "live.sqlite3")

        with Store(path) as store:
            imported = store.declared_account(
                AccountRef("halifax-clarity-credit-card")
            )

        assert imported is not None
        assert _without_id(imported) == FULL_RECORD

    def test_Upgrade_WhenItImportsAnAccount_MintsAStableIdForIt(
        self, tmp_path, registry_file
    ):
        path = _store_on_the_old_shape(tmp_path / "live.sqlite3")

        with Store(path) as store:
            declared = store.declared_accounts()

        assert all(account_id_well_formed(record.stable_id) for record in declared)
        assert len({record.stable_id for record in declared}) == len(declared)

    def test_Upgrade_WhenTheLadderRunsAgainOnALaterRelease_ChangesNothingFurther(
        self, tmp_path, registry_file
    ):
        """Every schema bump re-runs every migration, so "runs twice" is
        the normal case rather than the exotic one. An import that ran
        again and duplicated would show the same account twice in every
        picker, with a different id each."""
        path = _store_on_the_old_shape(tmp_path / "live.sqlite3")
        with Store(path) as store:
            first = store.declared_accounts()

        _reopen_as_an_upgrade(path)

        with Store(path) as store:
            second = store.declared_accounts()

        assert second == first

    def test_Upgrade_WhenAnImportedAccountWasSinceEdited_DoesNotUndoTheEdit(
        self, tmp_path, registry_file
    ):
        """The file is an import source, not the master copy. Once an
        account is in the store, the store's version is the answer - or
        every upgrade would silently revert whatever the page changed."""
        path = _store_on_the_old_shape(tmp_path / "live.sqlite3")
        with Store(path) as store:
            imported = store.declared_account(AccountRef("piggy-bank"))
            store.declare_account(replace(imported, label="Coin tin, hallway"))

        _reopen_as_an_upgrade(path)

        with Store(path) as store:
            after = store.declared_account(AccountRef("piggy-bank"))

        assert after.label == "Coin tin, hallway"
        assert after.stable_id == imported.stable_id

    def test_Upgrade_LeavesTheFileWhereItIs(self, tmp_path, registry_file):
        """Nothing about this change is allowed to need a manual step, and
        nothing is allowed to delete a person's own configuration."""
        path = _store_on_the_old_shape(tmp_path / "live.sqlite3")
        before = registry_file.read_bytes()

        with Store(path):
            pass

        assert registry_file.read_bytes() == before

    def test_AnAccountRenamedInTheStore_IsNotResurrectedFromTheFile(
        self, tmp_path, registry_file
    ):
        """The import runs ONCE, or renaming becomes a duplicate machine.

        Renaming is the whole point of the page this registry exists for.
        An import that re-reads the file on every open sees a renamed
        account as one the store lacks, and declares the file's original
        entry again - a second account, a second stable id, and a
        statement that could land against either. That is the defect that
        put sixty-two artefacts in the store for thirty-one documents, one
        layer up.
        """
        path = _store_on_the_old_shape(tmp_path / "live.sqlite3")
        with Store(path) as store:
            imported = store.declared_accounts()
            assert imported, "the file's accounts should arrive on upgrade"
            original = imported[0].ref
            store.declare_account(
                replace(imported[0], ref=AccountRef("renamed-by-hand"))
            )

        # The ladder is re-run, which is what a later release does: the
        # import is reached on every schema bump, and this project has had
        # eight. Simply reopening at the same version would prove nothing,
        # so the version is stamped back to force the upgrade path.
        legacy = sqlite3.connect(path)
        legacy.execute(
            "UPDATE obdi_meta SET value = ? WHERE key = 'schema_version'",
            (str(SCHEMA_VERSION - 1),),
        )
        legacy.commit()
        legacy.close()

        with Store(path) as store:
            refs = {record.ref for record in store.declared_accounts()}

        assert "renamed-by-hand" in refs
        assert original not in refs, (
            "the old name came back from the file as a second account"
        )

    def test_AnAccountAddedToTheFileAfterTheImport_DoesNotArriveLater(
        self, tmp_path, registry_file
    ):
        """The consequence of importing once, stated so that it is a
        decision rather than a surprise: hand-editing the file is what
        this move exists to replace, so the file stops being consulted
        once its contents are in."""
        path = _store_on_the_old_shape(tmp_path / "live.sqlite3")
        with Store(path) as store:
            before = {record.ref for record in store.declared_accounts()}

        registry_file.write_text(
            json.dumps(
                {
                    "accounts": [
                        {"id": "added-by-hand", "kind": "current", "label": "Late"}
                    ]
                }
            ),
            encoding="utf-8",
        )

        with Store(path) as store:
            after = {record.ref for record in store.declared_accounts()}

        assert after == before

    def test_Upgrade_WhenTheFileHoldsNoAccountsKeyAtAll_ImportsNothingAndOpens(
        self, tmp_path, monkeypatch
    ):
        """The overwhelming majority of these files predate declared
        accounts entirely and carry bindings alone."""
        map_path = tmp_path / "accounts.json"
        map_path.write_text(json.dumps({"bindings": []}), encoding="utf-8")
        monkeypatch.setenv("OBDI_ACCOUNT_MAP", str(map_path))
        path = _store_on_the_old_shape(tmp_path / "live.sqlite3")

        with Store(path) as store:
            assert store.declared_accounts() == []


class TestARegistryFileThatCannotBeRead:
    """An empty registry looks exactly like "this person has declared no
    accounts", and downstream that means a statement filed against the
    wrong account or against nothing at all. So unreadable refuses.
    """

    def test_TheRegistryFile_WhenAbsent_YieldsNoAccountsRatherThanFailing(
        self, tmp_path
    ):
        assert read_registry_file(tmp_path / "nothing-here.json") == []

    def test_AStore_WhenNoRegistryFileIsConfigured_OpensWithAnEmptyRegistry(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.delenv("OBDI_ACCOUNT_MAP", raising=False)
        path = _store_on_the_old_shape(tmp_path / "live.sqlite3")

        with Store(path) as store:
            assert store.declared_accounts() == []

    def test_TheRegistryFile_WhenItIsNotValidJson_RefusesAndNamesTheFile(
        self, tmp_path
    ):
        path = tmp_path / "accounts.json"
        path.write_text('{"accounts": [', encoding="utf-8")

        with pytest.raises(DataError) as refused:
            read_registry_file(path)

        assert "accounts.json" in str(refused.value)

    def test_TheRegistryFile_WhenTheAccountsKeyIsNotAList_Refuses(self, tmp_path):
        path = tmp_path / "accounts.json"
        path.write_text(json.dumps({"accounts": {"id": "oops"}}), encoding="utf-8")

        with pytest.raises(DataError):
            read_registry_file(path)

    def test_TheRegistryFile_WhenAnAccountIsNotAnObject_Refuses(self, tmp_path):
        """Silently dropping the entries it cannot read is how a registry
        arrives short by two accounts and says nothing."""
        path = tmp_path / "accounts.json"
        path.write_text(
            json.dumps({"accounts": [{"id": "fine"}, "not-an-account"]}),
            encoding="utf-8",
        )

        with pytest.raises(DataError):
            read_registry_file(path)

    def test_TheRegistryFile_WhenAnAccountHasNoName_Refuses(self, tmp_path):
        path = tmp_path / "accounts.json"
        path.write_text(json.dumps({"accounts": [{"kind": "cash"}]}), encoding="utf-8")

        with pytest.raises(DataError):
            read_registry_file(path)

    def test_TheRegistryFile_WhenADateCannotBeRead_Refuses(self, tmp_path):
        path = tmp_path / "accounts.json"
        path.write_text(
            json.dumps({"accounts": [{"id": "a", "opened": "17/01/2019"}]}),
            encoding="utf-8",
        )

        with pytest.raises(DataError):
            read_registry_file(path)

    def test_AStoreUpgrade_WhenTheRegistryFileIsMalformed_RefusesRatherThanDropIt(
        self, tmp_path, monkeypatch
    ):
        """Refusing to open is loud and one edit away from fixed. Opening
        with an empty registry is silent and files the next statement
        against nothing."""
        map_path = tmp_path / "accounts.json"
        map_path.write_text("{ this is not json", encoding="utf-8")
        monkeypatch.setenv("OBDI_ACCOUNT_MAP", str(map_path))
        path = _store_on_the_old_shape(tmp_path / "live.sqlite3")

        with pytest.raises(DataError):
            Store(path)


class TestWhatReadsTheRegistry:
    """The two consumers that exist today, both reached through the
    account map the CLI and the pages assemble."""

    def test_TheDeclaredLabel_WhenTheAccountIsDeclaredInTheStore_WinsTheDisplayName(
        self, store, monkeypatch
    ):
        from obdi.cli import _account_map

        monkeypatch.delenv("OBDI_ACCOUNT_MAP", raising=False)
        store.declare_account(FEEDLESS_RECORD)

        labels = _account_map(store).registry_labels()

        assert labels[AccountRef("piggy-bank")] == "Piggy bank"

    def test_TheLifecycleGuard_WhenTheAccountIsDeclaredInTheStore_StillSpeaks(
        self, store, monkeypatch
    ):
        """Rows before the account opened have to explain themselves. The
        guard reads the registry, so it goes quiet the moment the registry
        stops being found - which is the regression this holds."""
        from obdi.accounts import lifecycle_breach
        from obdi.cli import _account_map

        monkeypatch.delenv("OBDI_ACCOUNT_MAP", raising=False)
        store.declare_account(
            AccountRecord(ref=AccountRef("starling-personal"), opened=date(2019, 1, 17))
        )

        breach = lifecycle_breach(
            [date(2018, 12, 30), date(2019, 1, 16), date(2019, 2, 1)],
            _account_map(store).record(AccountRef("starling-personal")),
        )

        assert breach is not None
        assert "2 of 3" in breach

    def test_AnAccountDeclaredOnlyInTheFile_IsStillReadDuringTheTransition(
        self, store, registry_file
    ):
        """The file keeps working as an import source between the upgrade
        and the page: someone who adds an account to it the way the README
        still documents must not find it silently ignored."""
        from obdi.cli import _account_map

        record = _account_map(store).record(AccountRef("hsbc-old-current"))

        assert record is not None
        assert record.closed == date(2016, 5, 31)

    def test_AnAccountDeclaredInBoth_AnswersWithTheStoresVersion(
        self, store, registry_file
    ):
        """The store is where editing happens, so it is the master copy."""
        from obdi.cli import _account_map

        store.declare_account(
            AccountRecord(ref=AccountRef("piggy-bank"), label="Coin tin, hallway")
        )

        record = _account_map(store).record(AccountRef("piggy-bank"))

        assert record is not None
        assert record.label == "Coin tin, hallway"

    def test_TheAccountMap_WhenTheRegistryFileIsMalformed_RefusesLoudly(
        self, store, tmp_path, monkeypatch
    ):
        from obdi.cli import _account_map

        map_path = tmp_path / "accounts.json"
        map_path.write_text("not json at all", encoding="utf-8")
        monkeypatch.setenv("OBDI_ACCOUNT_MAP", str(map_path))

        with pytest.raises(DataError):
            _account_map(store)

    def test_TheAccountMap_WhenNoFileExists_StillResolvesBindingsAsSourceQualified(
        self, store, monkeypatch
    ):
        from obdi.cli import _account_map

        monkeypatch.delenv("OBDI_ACCOUNT_MAP", raising=False)

        assert _account_map(store).resolve("starling", "uid-1") == "starling:uid-1"
