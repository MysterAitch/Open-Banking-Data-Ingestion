"""Pulling one account from two sources at once.

The point is cross-validation and redundancy: two independent routes agreeing
is evidence the data is right, disagreement is a finding worth seeing, and
either route surviving an outage or a consent expiry keeps the record intact.

It only works if identity resolution treats the two sources as observations of
the same events rather than as separate ledgers, which is what these cover.
"""

from datetime import date

from obdi.accounts import AccountBinding, AccountMap
from obdi.identity import content_key
from obdi.matching import MatchTier, resolve
from obdi.models import Transaction


def txn(
    *,
    source: str,
    source_id: str | None,
    account: str = "current-account",
    amount: int = -1499,
    day: int = 14,
    description: str = "TESCO STORES",
) -> Transaction:
    when = date(2026, 3, day)
    return Transaction(
        account_id=account,
        amount_minor=amount,
        value_date=when,
        booking_date=when,
        description=description,
        source=source,
        source_id=source_id,
        content_key=content_key(
            account_id=account, amount_minor=amount, value_date=when, description=description
        ),
    )


class TestCanonicalAccountIdentity:
    def test_Account_WhenReportedByTwoSources_ResolvesToOneCanonicalId(self):
        account_map = AccountMap(
            [
                AccountBinding("current-account", "truelayer", "tl-acc-1"),
                AccountBinding("current-account", "starling", "st-acc-9"),
            ]
        )
        assert account_map.resolve("truelayer", "tl-acc-1") == "current-account"
        assert account_map.resolve("starling", "st-acc-9") == "current-account"

    def test_Account_WhenUnmapped_FallsBackToSourceQualifiedIdRatherThanColliding(self):
        # An unknown account must stay visibly separate. The cost is that
        # cross-source matching will not happen until it is bound, which is
        # the intended prompt to declare the binding.
        assert AccountMap().resolve("truelayer", "tl-acc-1") == "truelayer:tl-acc-1"

    def test_Account_WhenBoundToSeveralSources_ReportedAsMultiSource(self):
        account_map = AccountMap(
            [
                AccountBinding("current-account", "truelayer", "tl-acc-1"),
                AccountBinding("current-account", "starling", "st-acc-9"),
            ]
        )
        assert account_map.is_multi_source("current-account")
        assert account_map.sources_for("current-account") == ["starling", "truelayer"]


class TestMatchingAcrossSources:
    def test_Payment_WhenSeenByBothSourcesWithSameDescription_MatchedOnContent(self):
        from_aggregator = txn(source="truelayer", source_id="tl-tx-1")
        from_bank = txn(source="starling", source_id="st-tx-77")

        result = resolve(from_bank, [from_aggregator])

        assert result.tier is MatchTier.CONTENT_KEY
        assert result.existing is from_aggregator

    def test_Payment_WhenSourcesDescribeItDifferently_StillMatchedFuzzily(self):
        # This is the case that would silently double-count: both sides carry
        # a provider id, and the ids differ because the sources differ.
        from_aggregator = txn(source="truelayer", source_id="tl-tx-1", description="TESCO STORES")
        from_bank = txn(
            source="starling", source_id="st-tx-77", description="Tesco Stores 4912 Birmingham"
        )

        result = resolve(from_bank, [from_aggregator])

        assert result.tier is MatchTier.FUZZY
        assert result.existing is from_aggregator

    def test_Payment_WhenSameSourceReportsTwoDifferentIds_NotCollapsed(self):
        # Within one source, differing ids mean genuinely different payments,
        # and merging them would be a false positive.
        first = txn(source="truelayer", source_id="tl-tx-1", description="TESCO")
        second = txn(source="truelayer", source_id="tl-tx-2", day=15, description="SAINSBURYS")

        assert resolve(second, [first]).tier is MatchTier.UNRESOLVED

    def test_Payment_WhenSameProviderIdArrivesTwice_MatchedAtHighestTier(self):
        stored = txn(source="truelayer", source_id="tl-tx-1")
        assert resolve(txn(source="truelayer", source_id="tl-tx-1"), [stored]).tier is (
            MatchTier.SOURCE_ID
        )

    def test_Payment_WhenTwoSourcesShareAnIdByCoincidence_NotMatchedOnIdAlone(self):
        # Provider ids are only unique within a provider's namespace, so an
        # identical string from two sources proves nothing.
        stored = txn(source="truelayer", source_id="shared-id", description="TESCO")
        incoming = txn(source="starling", source_id="shared-id", day=25, description="ALDI")

        assert resolve(incoming, [stored]).tier is MatchTier.UNRESOLVED

    def test_Payment_WhenOnlyOneSourceHasSeenIt_ReportedAsNew(self):
        # The redundancy case: one route sees a transaction the other has not
        # yet published. It must be stored, not discarded.
        from_bank = txn(source="starling", source_id="st-tx-77")
        assert resolve(from_bank, []).is_new
