"""What the store keeps and refuses where the legs of a commitment's flow are concerned.

KNOWN ANSWERS, decided before the first run: a leg is declared on a commitment and comes back with
it, in the order declared; a leg is an amount or a share and never both; an account at either end
must be a held account (a declared external account is not one); a leg needs somewhere it comes
from and somewhere it goes; removal is a stamp; legs survive the rebuild from raw. A leg with a
held account at one end only is incoming or outgoing by which end it is; at both ends it is a move
between accounts; at neither it is external. Every name is invented.
"""

from __future__ import annotations

from datetime import date

import pytest

from landing import rebuild_from_raw
from obdi.ingest.accounts import AccountRecord, AccountRef
from obdi.ingest.commitment_records import CommitmentRefused, WindowTerms
from obdi.ingest.store import Store

TERMS = WindowTerms(90000, "GBP", "monthly", 1, 0, 4, "invented for a test")


@pytest.fixture
def store(tmp_path):
    with Store(tmp_path / "store.sqlite3") as opened:
        for ref in ("current-main", "bills-pot"):
            opened.declare_account(AccountRecord(ref=AccountRef(ref), kind="current", label=ref))
        opened.declare_account(
            AccountRecord(
                ref=AccountRef("external-abc"), kind="external", label="Elsewhere",
                identifier="12345678", external=True,
            )
        )  # fmt: skip
        yield opened


def rent(store: Store) -> int:
    return store.declare_commitment(
        "Rent", kind="scheduled", account="current-main", direction="out", entity_id=None,
        name_key="rent", from_day=date(2026, 10, 1), to_day=None, terms=TERMS,
    )  # fmt: skip


class TestDeclaringLegs:
    def test_Commitments_WhenLegsAreDeclared_ReturnThemInTheOrderDeclared(self, store):
        made = rent(store)
        partner = store.create_empty_entity("Casey Wintermute")
        store.declare_leg(
            made, from_account="current-main", to_account="bills-pot", share_percent=50,
            day=28, months_before=1,
        )  # fmt: skip
        store.declare_leg(
            made, from_entity=partner, to_account="current-main", amount_minor=45000, day=30,
            months_before=1, tolerance_days=2,
        )  # fmt: skip

        (found,) = store.commitments()

        assert [leg.position for leg in found.legs] == [1, 2]
        stash, incoming = found.legs
        assert stash.between_accounts and not stash.incoming and not stash.external
        assert incoming.incoming and incoming.party == partner and incoming.tolerance_days == 2
        assert (stash.share_percent, stash.amount_minor) == (50, None)
        assert (incoming.share_percent, incoming.amount_minor) == (None, 45000)

    def test_Leg_WhenNeitherEndIsAHeldAccount_IsExternal(self, store):
        made = rent(store)
        one = store.create_empty_entity("Casey Wintermute")
        two = store.create_empty_entity("Landlord Ltd")

        store.declare_leg(made, from_entity=one, to_entity=two, share_percent=100, day=1)

        (leg,) = store.commitments()[0].legs
        assert leg.external and leg.held_account == ""

    def test_Commitments_WhenNoLegIsDeclared_HaveNone(self, store):
        rent(store)

        assert store.commitments()[0].legs == ()

    def test_AccountHasLegs_WhenALegStartsOrEndsThere_IsTrueAndOtherwiseFalse(self, store):
        made = rent(store)
        store.declare_leg(
            made, from_account="current-main", to_account="bills-pot", share_percent=50, day=28
        )

        assert store.account_has_legs("bills-pot") and store.account_has_legs("current-main")
        assert not store.account_has_legs("external-abc")

    def test_RemoveLeg_WhenRemoved_LeavesTheOthersAndKeepsTheRowAsAStamp(self, store):
        made = rent(store)
        first = store.declare_leg(
            made, from_account="current-main", to_account="bills-pot", share_percent=50, day=28
        )
        store.declare_leg(
            made, from_account="current-main", to_entity=store.create_empty_entity("L"),
            share_percent=100, day=1,
        )  # fmt: skip

        store.remove_leg(first)

        assert [leg.position for leg in store.commitments()[0].legs] == [2]
        assert store.connection.execute("SELECT COUNT(*) FROM commitment_legs").fetchone()[0] == 2
        with pytest.raises(CommitmentRefused):
            store.remove_leg(first)

    def test_Legs_WhenTheStoreIsRebuiltFromRaw_AreKept(self, store):
        made = rent(store)
        store.declare_leg(
            made, from_account="current-main", to_account="bills-pot", share_percent=50, day=28
        )

        rebuild_from_raw(store)

        assert len(store.commitments()[0].legs) == 1


class TestRefusingWhatIsNotALeg:
    @pytest.mark.parametrize(
        "bad",
        [
            {"amount_minor": 100, "share_percent": 50},
            {},
            {"amount_minor": 0},
            {"amount_minor": -5},
            {"share_percent": 0},
            {"share_percent": 101},
        ],
    )
    def test_Declare_WhenTheAmountOrShareIsNotExactlyOneSensibleThing_IsRefused(self, store, bad):
        made = rent(store)

        with pytest.raises(CommitmentRefused):
            store.declare_leg(
                made, from_account="current-main", to_account="bills-pot", day=28, **bad
            )

        assert store.commitments()[0].legs == ()

    @pytest.mark.parametrize("fields", [{"day": 0}, {"day": 32}, {"months_before": 2},
                                        {"tolerance_days": 15}, {"tolerance_days": -1}])
    def test_Declare_WhenTheTimingIsOutOfRange_IsRefused(self, store, fields):
        made = rent(store)
        timing = {"day": 28, **fields}

        with pytest.raises(CommitmentRefused):
            store.declare_leg(
                made, from_account="current-main", to_account="bills-pot", share_percent=50,
                **timing,
            )  # fmt: skip

    def test_Declare_WhenAnAccountIsNotHeld_IsRefused(self, store):
        made = rent(store)

        for ref in ("no-such-account", "external-abc"):
            with pytest.raises(CommitmentRefused):
                store.declare_leg(
                    made, from_account="current-main", to_account=ref, share_percent=50, day=28
                )

    def test_Declare_WhenBothEndsAreTheSameAccount_IsRefused(self, store):
        with pytest.raises(CommitmentRefused):
            store.declare_leg(
                rent(store), from_account="bills-pot", to_account="bills-pot", share_percent=50,
                day=28,
            )  # fmt: skip

    def test_Declare_WhenAnEndIsNobody_IsRefused(self, store):
        made = rent(store)

        with pytest.raises(CommitmentRefused):
            store.declare_leg(made, from_account="current-main", share_percent=50, day=28)
        with pytest.raises(CommitmentRefused):
            store.declare_leg(made, to_account="current-main", share_percent=50, day=28)

    def test_Declare_WhenTheEntityDoesNotExist_IsRefused(self, store):
        with pytest.raises(CommitmentRefused):
            store.declare_leg(
                rent(store), from_account="current-main", to_entity=9999, share_percent=50, day=1
            )

    def test_Declare_WhenTheCommitmentDoesNotExist_IsRefused(self, store):
        with pytest.raises(CommitmentRefused):
            store.declare_leg(
                4242, from_account="current-main", to_account="bills-pot", share_percent=50, day=1
            )
