"""The explorer keeps its shape analysis and loses its account numbers."""

from __future__ import annotations

import pytest

from obdi.classification import (
    PROVIDER_PARTIAL,
    RANGE_ONLY,
    SHAPE_ONLY,
    SHOW,
    UNCLASSIFIED,
    classify,
    redact_summary,
)


class TestTheDefaultIsToWithhold:
    def test_AFieldNobodyHasClassified_IsWithheld_AndSaysSo(self):
        """The whole reason for an allowlist: a field a provider adds
        tomorrow must not render itself in full because nobody thought
        about it yet."""
        assert classify("some_new_provider_field") == UNCLASSIFIED

    def test_AnUnclassifiedFieldLosesItsValues_ButKeepsItsShape(self):
        summary = {
            "fields": [
                {
                    "path": "surprise_field",
                    "present": 3,
                    "types": ["string"],
                    "distinct": 3,
                    "values": [{"value": "a-real-secret", "count": 1}],
                    "length": {"min": 13, "max": 13},
                }
            ]
        }

        field = redact_summary(summary)["fields"][0]

        assert "values" not in field
        assert field["present"] == 3
        assert field["types"] == ["string"]
        assert field["length"] == {"min": 13, "max": 13}
        assert field["disclosure"] == UNCLASSIFIED
        assert "not yet classified" in field["note"]

    def test_TheSummaryCountsWhatItWithheld_SoTheGapIsVisible(self):
        summary = {
            "fields": [
                {"path": "account_number.number", "values": [{"value": "1", "count": 1}]},
                {"path": "brand_new_field", "values": [{"value": "2", "count": 1}]},
                {"path": "currency", "values": [{"value": "GBP", "count": 1}]},
            ]
        }

        result = redact_summary(summary)

        assert result["withheld_fields"] == 2
        assert result["unclassified_fields"] == 1


class TestIdentifiersNeverRenderAValue:
    @pytest.mark.parametrize(
        "path",
        [
            "account_number.number",
            "account_number.sort_code",
            "account_number.iban",
            "accountIdentifier",
            "bankIdentifier",
            "account_id",
            "accountUid",
            "transaction_id",
            "counterPartyUid",
        ],
    )
    def test_IdentifierPathsAreShapeOnly(self, path):
        assert classify(path) == SHAPE_ONLY

    def test_TheCommonPrefixIsDropped_BecauseForOneAccountItIsTheNumber(self):
        """The subtle leak: a 'common prefix' computed over a single
        account number is the account number."""
        summary = {
            "fields": [
                {
                    "path": "account_number.number",
                    "present": 1,
                    "types": ["string"],
                    "prefix": "12345678",
                    "min": "12345678",
                    "max": "12345678",
                    "values": [{"value": "12345678", "count": 1}],
                    "format": "8 digits",
                }
            ]
        }

        field = redact_summary(summary)["fields"][0]

        rendered = str(field)
        assert "12345678" not in rendered
        # The shape a person actually reads the page for survives.
        assert field["format"] == "8 digits"
        assert field["types"] == ["string"]

    @pytest.mark.parametrize(
        "path", ["display_name", "name_on_card", "counterPartyName", "description"]
    )
    def test_PeopleAndNarrativeAreShapeOnlyToo(self, path):
        assert classify(path) == SHAPE_ONLY


class TestTheProvidersOwnRedactionIsLabelledAsTheirs:
    def test_APartialCardNumberIsShown_AndAttributedToTheProvider(self):
        """It arrived partial. Masking it like something we chose to hide
        would misrepresent what the provider actually sent, which is the
        one thing this page exists to show."""
        assert classify("partial_card_number") == PROVIDER_PARTIAL

        summary = {
            "fields": [
                {
                    "path": "partial_card_number",
                    "values": [{"value": "8484", "count": 1}],
                }
            ]
        }

        field = redact_summary(summary)["fields"][0]

        assert field["values"] == [{"value": "8484", "count": 1}]
        assert "not redacted by obdi" in field["note"]


class TestMoneyKeepsItsRangeAndLosesItsItems:
    def test_AmountsShowTheirSpanButNotEachTransaction(self):
        assert classify("amount") == RANGE_ONLY

        summary = {
            "fields": [
                {
                    "path": "amount",
                    "min": -120.5,
                    "max": 2400.0,
                    "values": [{"value": "-120.5", "count": 1}],
                }
            ]
        }

        field = redact_summary(summary)["fields"][0]

        assert field["min"] == -120.5
        assert field["max"] == 2400.0
        assert "values" not in field
        assert "individual amounts withheld" in field["note"]


class TestDescriptiveFieldsSurviveIntact:
    @pytest.mark.parametrize(
        "path",
        [
            "currency",
            "account_type",
            "card_type",
            "transaction_category",
            "status",
            "direction",
            "provider.provider_id",
            "timestamp",
        ],
    )
    def test_TheFieldsTheExplorerExistsToShow_AreShownInFull(self, path):
        assert classify(path) == SHOW

    def test_AnEnumFieldKeepsItsValuesAndCounts(self):
        summary = {
            "fields": [
                {
                    "path": "transaction_category",
                    "values": [
                        {"value": "DIRECT_DEBIT", "count": 12},
                        {"value": "PURCHASE", "count": 40},
                    ],
                }
            ]
        }

        field = redact_summary(summary)["fields"][0]

        assert len(field["values"]) == 2
        assert field["disclosure"] == SHOW
        assert "note" not in field


class TestNestingIsRespected:
    def test_ANestedIdentifierIsClassifiedWithoutClaimingEveryLeafOfThatName(self):
        """account_number.number is an identifier; a bare 'number'
        elsewhere has not been classified and must not inherit its
        treatment by accident."""
        assert classify("account_number.number") == SHAPE_ONLY
        assert classify("page.number") == UNCLASSIFIED

    def test_ACurrencyInsideANestedBalanceIsStillJustACurrency(self):
        assert classify("running_balance.currency") == SHOW
        assert classify("running_balance.amount") == RANGE_ONLY
