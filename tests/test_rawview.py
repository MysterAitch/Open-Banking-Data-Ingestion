"""Computed metadata over a raw payload: what fields exist, and their spread.

The browsing question is rarely "show me everything" - it is "what shape is
this, which fields are actually populated, and what range do they cover?".
Min and max per field answer the recurring questions of this project directly:
the date span a window actually returned, the amount extremes, which optional
fields this provider really sends.
"""

from __future__ import annotations

import json

from obdi.rawview import summarise


def _payload(*items) -> bytes:
    return json.dumps({"results": list(items), "status": "Succeeded"}).encode()


class TestSummarisingAJsonPayload:
    def test_Summary_CountsItemsAndFindsFieldRanges(self):
        payload = _payload(
            {"timestamp": "2024-08-02T00:00:00Z", "amount": -25.0, "description": "A"},
            {"timestamp": "2026-08-01T00:00:00Z", "amount": 1200.5, "description": "B"},
        )

        summary = summarise(payload, "application/json")

        assert summary["kind"] == "json"
        assert summary["items"] == 2
        fields = {f["path"]: f for f in summary["fields"]}
        assert fields["timestamp"]["min"] == "2024-08-02T00:00:00Z"
        assert fields["timestamp"]["max"] == "2026-08-01T00:00:00Z"
        assert fields["amount"]["min"] == -25.0
        assert fields["amount"]["max"] == 1200.5

    def test_Summary_ReportsPartialPresence_WhichIsHowOptionalFieldsShow(self):
        payload = _payload(
            {"amount": 1, "running_balance": {"amount": 100.0}},
            {"amount": 2},
        )

        summary = summarise(payload, "application/json")

        fields = {f["path"]: f for f in summary["fields"]}
        # An optional field present on some rows: presence count is the direct
        # answer to "does this provider actually send it?"
        assert fields["running_balance.amount"]["present"] == 1
        assert fields["amount"]["present"] == 2

    def test_Summary_NestedKeysAreDottedOneLevelDeep(self):
        payload = _payload({"meta": {"provider_category": "GROCERIES"}})

        summary = summarise(payload, "application/json")

        assert any(f["path"] == "meta.provider_category" for f in summary["fields"])

    def test_Summary_MixedTypesSkipRangeRatherThanInventingOne(self):
        payload = _payload({"odd": 1}, {"odd": "two"})

        summary = summarise(payload, "application/json")

        field = next(f for f in summary["fields"] if f["path"] == "odd")
        assert field["min"] is None and field["max"] is None
        assert sorted(field["types"]) == ["number", "string"]

    def test_Summary_NonJson_ReportsKindAndSizeOnly(self):
        summary = summarise(b"Date,Amount\n01/02/2026,-5.00\n", "text/csv")

        assert summary["kind"] == "text/csv"
        assert summary["bytes"] == 29
        assert summary["fields"] == []

    def test_Summary_MalformedJson_DoesNotRaise(self):
        summary = summarise(b"{not json", "application/json")

        assert summary["kind"] == "unparseable"


class TestFieldCardinalityAndValueTallies:
    """Min and max mean little for categories and nothing for identifiers.

    A field with five values wants those five values counted; an opaque
    reference wants its shape described - how many, how long, common prefix,
    recognisable format - because that is what parsing decisions rest on.
    """

    def test_LowCardinalityField_EnumeratesValuesWithTallies(self):
        payload = _payload(
            {"transaction_type": "DEBIT"},
            {"transaction_type": "DEBIT"},
            {"transaction_type": "CREDIT"},
        )

        summary = summarise(payload, "application/json")

        field = next(f for f in summary["fields"] if f["path"] == "transaction_type")
        assert field["distinct"] == 2
        assert {"value": "DEBIT", "count": 2} in field["values"]
        assert {"value": "CREDIT", "count": 1} in field["values"]

    def test_IdLikeStrings_GetShapeNotRange(self):
        payload = _payload(
            *[
                {"ref": f"txn-{(index * 0x9E3779B97F4A7C15) % (1 << 64):016x}"}
                for index in range(1, 21)
            ]
        )

        summary = summarise(payload, "application/json")

        field = next(f for f in summary["fields"] if f["path"] == "ref")
        # A lexicographic range over identifiers is noise, not information.
        assert field["min"] is None and field["max"] is None
        assert field["distinct"] == 20
        assert field["length"] == {"min": 20, "max": 20}
        assert field["prefix"] == "txn-"

    def test_TimestampStrings_KeepTheirRange_TheWindowEvidence(self):
        payload = _payload(
            *[{"timestamp": f"2026-07-{day:02d}T00:00:00Z"} for day in range(1, 15)]
        )

        summary = summarise(payload, "application/json")

        field = next(f for f in summary["fields"] if f["path"] == "timestamp")
        assert field["min"] == "2026-07-01T00:00:00Z"
        assert field["max"] == "2026-07-14T00:00:00Z"

    def test_UuidValues_AreRecognisedAsAFormat(self):
        payload = _payload(
            *[
                {"account_id": f"0123abcd-1111-2222-3333-{index:012d}"}
                for index in range(12)
            ]
        )

        summary = summarise(payload, "application/json")

        field = next(f for f in summary["fields"] if f["path"] == "account_id")
        assert field["format"] == "uuid"


class TestCrossFieldInsights:
    def test_AmountSign_IsCrossTabulatedAgainstEachCategoricalField(self):
        payload = _payload(
            {"transaction_type": "CREDIT", "amount": 100.0},
            {"transaction_type": "CREDIT", "amount": 55.5},
            {"transaction_type": "DEBIT", "amount": -20.0},
        )

        summary = summarise(payload, "application/json")

        rows = {
            (row["field"], row["value"]): row for row in summary["sign_by"]
        }
        assert rows[("transaction_type", "CREDIT")]["positive"] == 2
        assert rows[("transaction_type", "CREDIT")]["negative"] == 0
        assert rows[("transaction_type", "DEBIT")]["negative"] == 1

    def test_PresencePattern_AbsentForOneCategoryValue_IsSurfaced(self):
        items = [
            {"category": "PAYMENT", "provider_reference": f"r{index}"}
            for index in range(6)
        ] + [{"category": "TRANSFER"} for _ in range(6)]

        summary = summarise(_payload(*items), "application/json")

        links = {
            (link["field"], link["by"], link["value"]): link
            for link in summary["presence_links"]
        }
        transfer = links[("provider_reference", "category", "TRANSFER")]
        assert transfer["present"] == 0 and transfer["total"] == 6

    def test_ItemsPerMonth_TalliedFromTheTimestampField_GapsVisibleAsMissingMonths(self):
        payload = _payload(
            {"timestamp": "2026-05-01T00:00:00Z"},
            {"timestamp": "2026-05-20T00:00:00Z"},
            {"timestamp": "2026-07-03T00:00:00Z"},
        )

        summary = summarise(payload, "application/json")

        assert summary["by_month"] == [
            {"month": "2026-05", "count": 2},
            {"month": "2026-07", "count": 1},
        ]


class TestSettlementLag:
    def test_LagsAndBoundaryCrossings_AreCounted(self):
        """The Thursday-tap-Saturday-settle case, measured: a 2-day lag
        inside one week crosses nothing; the same lag over a Sunday or a
        month end files the payment into the wrong period."""
        from obdi.rawview import settlement_lag_report

        rows = [
            {
                "transactionTime": "2026-07-16T10:00:00Z",
                "settlementTime": "2026-07-18T03:00:00Z",
            },
            {
                "transactionTime": "2026-07-18T10:00:00Z",
                "settlementTime": "2026-07-20T03:00:00Z",
            },
            {
                "transactionTime": "2026-07-31T22:00:00Z",
                "settlementTime": "2026-08-02T03:00:00Z",
            },
            {
                "transactionTime": "2026-07-21T09:00:00Z",
                "settlementTime": "2026-07-21T18:00:00Z",
            },
            {"transactionTime": "2026-07-21T09:00:00Z"},
        ]

        report = settlement_lag_report(rows)

        assert report["measured"] == 4
        assert report["lags"] == {"2d": 3, "same-day": 1}
        # Jul 16 (Thu) -> 18 (Sat) stays in its week; Jul 18 (Sat) ->
        # 20 (Mon) crosses one; Jul 31 (Fri) -> Aug 2 (Sun) crosses the
        # MONTH while staying inside its ISO week - the two boundary
        # kinds are genuinely independent.
        assert report["week_crossings"] == 1
        assert report["month_crossings"] == 1


class TestBalanceWalk:
    def test_CleanChain_NewestFirstProviderOrder_VerifiesWithZeroBreaks(self):
        """The provider returns newest-first and one row lacks a balance;
        the walk detects the direction, bridges the gap and verifies."""
        from obdi.rawview import balance_walk_report

        rows = [
            {"amount": 5.0, "running_balance": {"amount": 85.0}},
            {"amount": -30.0},
            {"amount": 10.0, "running_balance": {"amount": 110.0}},
            {"amount": -20.0, "running_balance": {"amount": 100.0}},
        ]

        report = balance_walk_report(
            [{"ref": "truelayer:acc-1", "label": "a1", "rows": rows}]
        )

        account = report["accounts"]["truelayer:acc-1"]
        assert account["breaks"] == 0
        assert account["checks"] == 2
        assert report["rows_with_balance"] == 3
        assert "reversed" in next(iter(account["conventions"]))

    def test_MissingTransaction_SurfacesAsOneLocalisedBreak(self):
        """A transaction the bank counted but the store never held: the
        balances jump by an unexplained 3.45, the chain re-anchors, and
        later rows stay clean rather than cascading."""
        from obdi.rawview import balance_walk_report

        rows = [
            {"amount": -20.0, "running_balance": {"amount": 100.0}},
            {"amount": 10.0, "running_balance": {"amount": 110.0}},
            {"amount": -30.0, "running_balance": {"amount": 76.55}},
            {"amount": 5.0, "running_balance": {"amount": 81.55}},
        ]

        report = balance_walk_report(
            [{"ref": "truelayer:acc-1", "label": "a1", "rows": rows}]
        )

        account = report["accounts"]["truelayer:acc-1"]
        assert account["checks"] == 3
        assert account["breaks"] == 1
        example = account["examples"][0]
        assert example["delta"] == -345
        assert example["artefact"] == "a1"

    def test_NoBalancesAtAll_AccountIsOmittedNotFabricated(self):
        from obdi.rawview import balance_walk_report

        rows = [{"amount": 5.0}, {"amount": -3.0}]

        report = balance_walk_report(
            [{"ref": "truelayer:acc-1", "label": "a1", "rows": rows}]
        )

        assert report["accounts"] == {}
        assert report["rows_with_balance"] == 0
