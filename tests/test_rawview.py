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
