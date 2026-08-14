"""Spaces that existed once and do not exist now, recovered from the feed.

Starling Spaces become accounts from the `starling-spaces` artefacts - the
savings-goals endpoint, which answers "what Spaces exist NOW". A Space that was
deleted, or folded into another, can never appear there. Its transfers survive
in the feed with nowhere to pair, so they read as unexplained one-sided rows:
212 of them on the live instance in August 2026, mostly labelled 'Rent'.

THE EVIDENCE IS ALREADY HELD. Every feed item is stored whole in the
transaction's `raw`, and a Space transfer carries `counterPartyType: CATEGORY`
with the Space's own uid and name. So the recovery is a replay over bytes
already on disk - no re-fetch, no bank call, no consent, no quota. That is the
raw layer paying for itself.

WHY THE FEED AND NOT A STATEMENT. Starling's certified statement is a
single-account document with one balance thread; it has no per-Space section,
and it would carry a NAME where the feed carries a UID. A renamed Space, or two
Spaces that shared a name over time, is ambiguous by name and unambiguous by
uid - and a deleted Space is exactly where names are least trustworthy.

THE DATES ARE INFERRED AND SAY SO. First and last transaction are not the dates
a Space was opened and closed: they are the first and last time money moved
through it, which bounds its life without dating it. A Space created in January
and first used in March reads as opening in March. That is why what this returns
is carried as inferred rather than stated.
"""

from __future__ import annotations

from datetime import date

import pytest

from obdi.spaces import historical_spaces

BILLS_UID = "b1115000-0000-4000-8000-000000000001"
RENT_UID = "5e117000-0000-4000-8000-000000000002"


def _transfer(uid: str, name: str, when: str, *, amount: int = -5000) -> dict:
    """One feed item moving money to a Space, in the shape the API sends."""
    return {
        "feedItemUid": f"{uid}-{when}",
        "amount": {"currency": "GBP", "minorUnits": abs(amount)},
        "direction": "OUT" if amount < 0 else "IN",
        "transactionTime": f"{when}T09:00:00.000Z",
        "source": "INTERNAL_TRANSFER",
        "counterPartyType": "CATEGORY",
        "counterPartyUid": uid,
        "counterPartyName": name,
        "reference": name,
    }


def _spend(when: str) -> dict:
    """An ordinary purchase - not a Space transfer, and must be ignored."""
    return {
        "feedItemUid": f"spend-{when}",
        "amount": {"currency": "GBP", "minorUnits": 1234},
        "direction": "OUT",
        "transactionTime": f"{when}T09:00:00.000Z",
        "source": "MASTER_CARD",
        "counterPartyType": "MERCHANT",
        "counterPartyUid": "merchant-uid",
        "counterPartyName": "COFFEE REPUBLIC",
    }


@pytest.fixture
def store_with_a_deleted_space(tmp_path):
    """A store holding one live Space, one deleted Space and a purchase.

    Module-level rather than inside a class: both the recovery rule and the
    command that calls it need the same store, and a second copy would drift
    from the first.
    """
    import json
    from datetime import UTC, datetime

    from obdi.models import RawArtefact
    from obdi.store import Store

    def landed(source: str, body: dict, name: str) -> RawArtefact:
        return RawArtefact(
            source=source,
            account_ref="starling-personal",
            fetched_at=datetime.now(UTC),
            media_type="application/json",
            digest=f"digest-{name}",
            payload=json.dumps(body).encode(),
            origin=name,
        )

    path = tmp_path / "store.sqlite3"
    with Store(path) as store:
        store.land_artefact(
            landed(
                "starling-spaces",
                {"savingsGoals": [{"savingsGoalUid": BILLS_UID, "name": "Bills"}]},
                "spaces",
            )
        )
        store.land_artefact(
            landed(
                "starling-feed",
                {
                    "feedItems": [
                        _transfer(BILLS_UID, "Bills", "2025-01-05"),
                        _transfer(RENT_UID, "Rent", "2021-06-01"),
                        _transfer(RENT_UID, "Rent", "2022-11-30"),
                        _spend("2025-02-02"),
                    ]
                },
                "feed",
            )
        )
    return path


class TestRecoveringSpacesFromTheFeed:
    def test_ASpaceStillOpen_IsNotReportedAsHistorical(self):
        """The whole point is the ones that are GONE. A Space the savings-goals
        endpoint still returns needs no recovery and must not be re-declared."""
        found = historical_spaces(
            feed_items=[_transfer(BILLS_UID, "Bills", "2025-03-04")],
            current_space_uids={BILLS_UID},
        )

        assert found == []

    def test_ASpaceTheEndpointNoLongerReturns_IsRecoveredWithItsName(self):
        found = historical_spaces(
            feed_items=[_transfer(RENT_UID, "Rent", "2021-06-01")],
            current_space_uids={BILLS_UID},
        )

        assert len(found) == 1
        assert found[0].uid == RENT_UID
        assert found[0].name == "Rent"

    def test_ItsLifeIsBoundedByTheFirstAndLastMovement(self):
        """Bounded, not dated - see the module docstring. The count travels
        with it because "20 transfers over four years" and "one transfer" are
        different confidences in the same shape of evidence."""
        found = historical_spaces(
            feed_items=[
                _transfer(RENT_UID, "Rent", "2022-11-30"),
                _transfer(RENT_UID, "Rent", "2021-06-01"),
                _transfer(RENT_UID, "Rent", "2022-02-14"),
            ],
            current_space_uids=set(),
        )

        assert found[0].first_seen == date(2021, 6, 1)
        assert found[0].last_seen == date(2022, 11, 30)
        assert found[0].transfers == 3

    def test_TheMainAccountIsNotMistakenForASpace(self):
        """The false positive that reached real data on 2026-08-14.

        A main account's own ledger is a CATEGORY too - `defaultCategory` on
        the accounts payload - so a transfer seen from the SPACE side names the
        main account with counterPartyType CATEGORY, identically to a Space.
        The first run against the live store duly reported 'Current (GBP)',
        1,028 transfers, still active: the current account, offered as a
        deleted Space.

        Excluded by uid rather than by name, because 'Current (GBP)' is a
        label a person could rename or a Space could borrow.
        """
        main_category = "ma1n0000-0000-4000-8000-00000000000a"

        found = historical_spaces(
            feed_items=[_transfer(main_category, "Current (GBP)", "2025-06-01")],
            current_space_uids=set(),
            main_account_categories={main_category},
        )

        assert found == []

    def test_OrdinarySpending_IsNotMistakenForASpace(self):
        """counterPartyType is the discriminator. A merchant has a uid and a
        name too, and treating those as Spaces would declare an account per
        shop."""
        found = historical_spaces(
            feed_items=[_spend("2025-01-05"), _spend("2025-01-06")],
            current_space_uids=set(),
        )

        assert found == []

    def test_SeveralHistoricalSpaces_AreReportedSeparately(self):
        found = historical_spaces(
            feed_items=[
                _transfer(RENT_UID, "Rent", "2021-06-01"),
                _transfer("0ld5-0000-4000-8000-000000000003", "Holiday", "2020-01-02"),
            ],
            current_space_uids=set(),
        )

        assert {space.name for space in found} == {"Rent", "Holiday"}

    def test_ASpaceRenamedPartway_KeepsItsIdentityAndTakesItsLatestName(self):
        """Names are not identity - the uid is. A Space renamed from 'Rent' to
        'Rent + bills' is ONE account, and reporting two would create a
        duplicate that no future pairing could reconcile. The latest name wins
        because it is the one a person will recognise."""
        found = historical_spaces(
            feed_items=[
                _transfer(RENT_UID, "Rent", "2021-06-01"),
                _transfer(RENT_UID, "Rent and bills", "2022-09-30"),
            ],
            current_space_uids=set(),
        )

        assert len(found) == 1, "a rename split one Space into two accounts"
        assert found[0].name == "Rent and bills"
        assert found[0].first_seen == date(2021, 6, 1)
        assert found[0].also_known_as == ("Rent",), (
            "the old name was discarded - two accounts may legitimately show "
            "the same display name, and that is only safe if each says what "
            "it used to be called"
        )

    def test_ASpaceNeverRenamed_ClaimsNoFormerNames(self):
        """The other half, so 'previously ...' never appears on an account
        that has always been called one thing."""
        found = historical_spaces(
            feed_items=[_transfer(RENT_UID, "Rent", "2021-06-01")],
            current_space_uids=set(),
        )

        assert found[0].also_known_as == ()

    def test_AnItemWithNoUid_IsSkippedRatherThanDeclaredAnonymously(self):
        """An account keyed on an empty string would collide with the next one
        like it, silently merging two Spaces."""
        nameless = _transfer("", "Rent", "2021-06-01")

        assert historical_spaces(feed_items=[nameless], current_space_uids=set()) == []


class TestTheCanonicalNameASpaceGets:
    """Two Spaces can share a name; none can share a canonical ref.

    A Space's IDENTITY is its uid, and the account map already resolves an
    unbound one to `starling:<uid>`, which cannot collide. The hazard is the
    readable ref a person sees. Deleting a Space and making a new one with the
    same name, or renaming an existing Space onto a dead one's name, both
    produce two distinct uids wanting one ref - and a ref quietly reused would
    merge two different pots into one account, which no later pairing could
    take apart.
    """

    def test_TheNameLeads_SoAPersonCanReadIt(self):
        from obdi.spaces import canonical_ref

        assert canonical_ref("Rent", uid=RENT_UID).startswith("starling-space-rent-")

    def test_TheSameSpace_AlwaysComputesTheSameRef(self):
        """Idempotence by construction, which is what makes the back-fill safe
        to re-run. The first design suffixed on collision instead, and had to
        recognise its own previous declarations to do it - it got that wrong
        immediately, minting a second account for the same Space on the second
        run."""
        from obdi.spaces import canonical_ref

        assert canonical_ref("Rent", uid=RENT_UID) == canonical_ref(
            "Rent", uid=RENT_UID
        )

    def test_TwoSpacesSharingAName_GetDifferentRefs(self):
        """Delete-and-recreate, and rename-onto-a-dead-name, arrive here
        identically: two distinct uids and one name. A ref built from the name
        alone would hand the second the first's account."""
        from obdi.spaces import canonical_ref

        assert canonical_ref("Rent", uid=RENT_UID) != canonical_ref(
            "Rent", uid=BILLS_UID
        )

    def test_ARenamedSpace_ChangesItsRef_ButKeepsItsIdentity(self):
        """Honest about a real trade. Deriving the ref from the NAME as well as
        the uid means renaming a Space changes its canonical ref, so a re-run
        declares the new name alongside the old. The uid fragment is what says
        they are the same Space, and the alternative - a ref of pure uid -
        would be unreadable in every listing. Recorded so the next reader meets
        the trade rather than the surprise.
        """
        from obdi.spaces import canonical_ref

        before = canonical_ref("Rent", uid=RENT_UID)
        after = canonical_ref("Rent and bills", uid=RENT_UID)

        assert before != after
        assert before.rsplit("-", 1)[-1] == after.rsplit("-", 1)[-1]

    def test_ASpaceWithNoUsableName_StillGetsADistinctRef(self):
        """A Space whose name is empty or entirely punctuation must not
        collapse onto the bare prefix, or every such Space collides."""
        from obdi.spaces import canonical_ref

        first = canonical_ref("", uid=RENT_UID)
        second = canonical_ref("***", uid=BILLS_UID)

        assert first != second
        assert first not in ("starling-space", "starling-space-")

    def test_TheRefIsAcceptedByTheNameValidator(self):
        """The canonical name is checked at every write door, so a ref this
        produces has to pass that check or the back-fill dies at the door."""
        from obdi.namespaces import validate_canonical_name
        from obdi.spaces import canonical_ref

        for name in ("Rent", "Rent & Bills", "  spaced  out  ", "Holiday 2024", ""):
            validate_canonical_name(canonical_ref(name, uid=RENT_UID))


class TestReadingItOutOfAStore:
    """The real door: artefacts already on disk, no fetch.

    Written against a store rather than against the rule alone, because the
    rule being right says nothing about whether anything reaches it - which is
    the fault this project has met twice in a week.
    """

    def test_ItFindsTheDeletedSpace_AndLeavesTheLiveOneAlone(
        self, store_with_a_deleted_space
    ):
        from obdi.spaces import recover
        from obdi.store import Store

        with Store(store_with_a_deleted_space) as store:
            found = recover(store)

        assert [space.name for space in found] == ["Rent"]
        assert found[0].first_seen == date(2021, 6, 1)
        assert found[0].last_seen == date(2022, 11, 30)
        assert found[0].transfers == 2

    def test_TheMainAccountsOwnCategory_IsExcludedFromTheStoreToo(self, tmp_path):
        """The false positive as it actually arrived: through the store.

        The rule-level test proves the exclusion works when told; this proves
        the accounts artefact is READ so it gets told. Without it `recover`
        offered the current account as a deleted Space against real data, with
        1,028 transfers and an end date of last week.
        """
        import json
        from datetime import UTC, datetime

        from obdi.models import RawArtefact
        from obdi.spaces import recover
        from obdi.store import Store

        main_category = "ma1n0000-0000-4000-8000-00000000000a"
        path = tmp_path / "with-accounts.sqlite3"
        with Store(path) as store:
            for source, body, name in (
                (
                    "starling-accounts",
                    {
                        "accounts": [
                            {
                                "accountUid": "acct-uid",
                                "name": "Personal",
                                "defaultCategory": main_category,
                            }
                        ]
                    },
                    "accounts",
                ),
                (
                    "starling-feed",
                    {
                        "feedItems": [
                            _transfer(main_category, "Current (GBP)", "2025-06-01"),
                            _transfer(RENT_UID, "Rent", "2021-06-01"),
                        ]
                    },
                    "feed",
                ),
            ):
                store.land_artefact(
                    RawArtefact(
                        source=source,
                        account_ref="starling-personal",
                        fetched_at=datetime.now(UTC),
                        media_type="application/json",
                        digest=f"digest-{name}",
                        payload=json.dumps(body).encode(),
                        origin=name,
                    )
                )

        with Store(path) as store:
            found = recover(store)

        assert [space.name for space in found] == ["Rent"], (
            "the main account was offered as a deleted Space"
        )

    def test_AStoreWithNoStarlingArtefacts_FindsNothingRatherThanFailing(
        self, tmp_path
    ):
        """Every other provider's store must survive this being run."""
        from obdi.spaces import recover
        from obdi.store import Store

        with Store(tmp_path / "empty.sqlite3") as store:
            assert recover(store) == []


class TestTheCommand:
    """`obdi recover-spaces`, which is what actually declares anything.

    Reporting and applying are separate on purpose: declaring creates accounts
    in a real store from an inference, and a command that did that as a side
    effect of being run once would be the wrong default for a tool holding
    somebody's financial history.
    """

    def test_WithoutApply_ItReportsAndDeclaresNothing(
        self, store_with_a_deleted_space, monkeypatch, capsys
    ):
        from obdi.cli import main
        from obdi.store import Store

        monkeypatch.setenv("OBDI_CONNECTION_STORE", "")
        assert main(["--db", str(store_with_a_deleted_space), "recover-spaces"]) == 0

        printed = capsys.readouterr().out
        assert "starling-space-rent" in printed
        assert "2021-06-01 .. 2022-11-30" in printed
        assert "Nothing was declared" in printed
        with Store(store_with_a_deleted_space) as store:
            assert store.declared_accounts() == []

    def test_WithApply_ItDeclaresTheSpaceAndSaysTheDatesAreInferred(
        self, store_with_a_deleted_space, monkeypatch
    ):
        from obdi.cli import main
        from obdi.store import Store

        monkeypatch.setenv("OBDI_CONNECTION_STORE", "")
        assert (
            main(["--db", str(store_with_a_deleted_space), "recover-spaces", "--apply"])
            == 0
        )

        with Store(store_with_a_deleted_space) as store:
            declared = store.declared_accounts()

        assert len(declared) == 1
        account = declared[0]
        assert account.ref.startswith("starling-space-rent-")
        assert account.kind == "starling-space"
        assert account.opened == date(2021, 6, 1)
        assert account.closed == date(2022, 11, 30)
        assert "inferred" in account.date_basis, (
            "an account whose dates are a guess must say so where the dates "
            "live, not in a note somewhere else"
        )

    def test_TwoArchivedSpacesSharingAName_StayApartAndAreTellableApart(
        self, tmp_path, monkeypatch
    ):
        """Not theoretical: this account holds two archived Spaces both called
        'Rent'. Starling ARCHIVES rather than deletes, so a name can be reused
        freely and the savings-goals endpoint stops returning either.

        Neither was renamed, so 'previously known as' says nothing here. The
        date spans are the only thing that separates them, which is why they
        are in the label and not only in the record.
        """
        import json
        from datetime import UTC, datetime

        from obdi.cli import main
        from obdi.models import RawArtefact
        from obdi.store import Store

        second_rent = "2nd7e117-0000-4000-8000-000000000009"
        path = tmp_path / "two-rents.sqlite3"
        with Store(path) as store:
            store.land_artefact(
                RawArtefact(
                    source="starling-feed",
                    account_ref="starling-personal",
                    fetched_at=datetime.now(UTC),
                    media_type="application/json",
                    digest="digest-two-rents",
                    payload=json.dumps(
                        {
                            "feedItems": [
                                _transfer(RENT_UID, "Rent", "2019-02-04"),
                                _transfer(RENT_UID, "Rent", "2019-04-26"),
                                _transfer(second_rent, "Rent", "2020-10-01"),
                                _transfer(second_rent, "Rent", "2020-10-30"),
                            ]
                        }
                    ).encode(),
                    origin="feed",
                )
            )

        monkeypatch.setenv("OBDI_CONNECTION_STORE", "")
        assert main(["--db", str(path), "recover-spaces", "--apply"]) == 0

        with Store(path) as store:
            declared = store.declared_accounts()

        assert len(declared) == 2, "two Spaces named Rent collapsed into one"
        assert len({str(record.ref) for record in declared}) == 2
        assert len({record.label for record in declared}) == 2, (
            "both accounts carry the same label, so nothing in a picker could "
            "tell a reader which Rent they were choosing"
        )

    def test_RunningItTwice_DoesNotCreateASecondAccount(
        self, store_with_a_deleted_space, monkeypatch
    ):
        """The back-fill is expected to be re-run, and a second account for the
        same Space would split its history in two."""
        from obdi.cli import main
        from obdi.store import Store

        monkeypatch.setenv("OBDI_CONNECTION_STORE", "")
        for _ in range(2):
            main(
                ["--db", str(store_with_a_deleted_space), "recover-spaces", "--apply"]
            )

        with Store(store_with_a_deleted_space) as store:
            assert len(store.declared_accounts()) == 1


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
