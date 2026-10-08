"""When a second sighting of one payment is folded onto the row, whose party details survive.

A payment seen by two sources is one row. The other party's name and identifiers
(`counterparty`, `party_account`, `party_source_id`) are filled by the first sighting that states
them, unless a later sighting of a higher fidelity (`SourceTier`: the feed's own identifiers above
a file read off a document, a typed entry below both) states them, which then replaces them. The
row's content key and entity id are not touched either way: the party is not part of identity.

The answers below were decided before the code: each case names the winner by the sighting it
came from.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date

import pytest

from obdi.core.models import SourceTier, Transaction
from obdi.ingest.identity import content_key, entity_id_for
from obdi.ingest.matching import supersede

DAY = date(2026, 3, 14)


def sighting(
    source: str,
    tier: SourceTier,
    *,
    counterparty: str = "",
    party_account: str = "",
    party_source_id: str = "",
) -> Transaction:
    base = Transaction(
        account_id="current",
        amount_minor=-1499,
        value_date=DAY,
        booking_date=DAY,
        description="CARD PAYMENT",
        source=source,
        tier=tier,
        counterparty=counterparty,
        party_account=party_account,
        party_source_id=party_source_id,
    )
    key = content_key(
        amount_minor=base.amount_minor, value_date=base.value_date, description=base.description
    )
    minted = entity_id_for(
        account_id=base.account_id,
        source=source,
        source_id=None,
        content_key_value=key,
        occurrence=0,
        first_artefact_digest="digest",
    )
    return replace(base, content_key=key, entity_id=minted)


def folded(first: Transaction, second: Transaction) -> Transaction:
    return supersede(first, second)


def test_Fold_WhenTheFirstSightingStatesNoPartyAndALaterOneDoes_TheLaterPartyIsTaken():
    pdf = sighting("statement", SourceTier.SYNTHETIC)
    csv = sighting("starling-csv", SourceTier.SYNTHETIC, counterparty="Tesco")

    assert folded(pdf, csv).counterparty == "Tesco"


@pytest.mark.parametrize("field", ["counterparty", "party_account", "party_source_id"])
def test_Fold_WhenALowerTierStatedAPartyFirstAndAHigherTierStatesOneLater_TheHigherTierWins(field):
    reader = sighting("statement", SourceTier.SYNTHETIC, **{field: "from the layout"})
    feed = sighting("truelayer", SourceTier.AUTHORITATIVE, **{field: "from the feed"})

    assert getattr(folded(reader, feed), field) == "from the feed"


@pytest.mark.parametrize("field", ["counterparty", "party_account", "party_source_id"])
def test_Fold_WhenAHigherTierStatedAPartyFirstAndALowerTierStatesOneLater_TheHigherTierStays(field):
    feed = sighting("truelayer", SourceTier.AUTHORITATIVE, **{field: "from the feed"})
    reader = sighting("statement", SourceTier.SYNTHETIC, **{field: "from the layout"})

    assert getattr(folded(feed, reader), field) == "from the feed"


@pytest.mark.parametrize("field", ["counterparty", "party_account", "party_source_id"])
def test_Fold_WhenTwoSightingsOfOneTierEachStateAParty_TheFirstStays(field):
    first = sighting("file-one", SourceTier.SYNTHETIC, **{field: "first"})
    second = sighting("file-two", SourceTier.SYNTHETIC, **{field: "second"})

    assert getattr(folded(first, second), field) == "first"


def test_Fold_WhenALaterSightingStatesNoParty_TheHeldPartyStaysWhateverTheTiers():
    feed = sighting("truelayer", SourceTier.AUTHORITATIVE, counterparty="Tesco")
    for later_tier in SourceTier:
        later = sighting("statement", later_tier)
        assert folded(feed, later).counterparty == "Tesco", later_tier


def test_Fold_WhenATypedEntryStatesAPartyAfterAFeedDid_TheFeedsPartyStays():
    feed = sighting("truelayer", SourceTier.AUTHORITATIVE, counterparty="Tesco")
    typed = sighting("manual", SourceTier.MANUAL, counterparty="shop")

    assert folded(feed, typed).counterparty == "Tesco"


def test_Fold_EachPartyFieldIsJudgedOnItsOwn_ACloserFieldFromTheLowerTierIsKept():
    # The feed states a name but no account; the reader states an account but no name.
    feed = sighting("truelayer", SourceTier.AUTHORITATIVE, counterparty="Tesco")
    reader = sighting("statement", SourceTier.SYNTHETIC, party_account="12345678")

    merged = folded(feed, reader)

    assert (merged.counterparty, merged.party_account) == ("Tesco", "12345678")


@pytest.mark.parametrize(
    ("first_tier", "second_tier"),
    [
        (SourceTier.SYNTHETIC, SourceTier.AUTHORITATIVE),
        (SourceTier.AUTHORITATIVE, SourceTier.SYNTHETIC),
        (SourceTier.SYNTHETIC, SourceTier.SYNTHETIC),
    ],
)
def test_Fold_WhicheverPartyWins_TheRowKeepsItsEntityIdAndContentKey(first_tier, second_tier):
    first = sighting("a", first_tier, counterparty="one")
    second = sighting("b", second_tier, counterparty="two")

    merged = folded(first, second)

    assert merged.entity_id == first.entity_id
    assert merged.content_key == first.content_key == second.content_key
