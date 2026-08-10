"""Accounts are first-class; sources are incidental.

The registry declares accounts into existence - a name, a kind, a lifecycle,
and (captured ahead of any consumer) windows of limits and rates. The ways of
populating an account attach evidence to a declared container; they never
conjure the container. The first consumers are deliberately small: the
declared label wins the picker, and an import whose rows fall outside the
account's open window has to explain itself.
"""

from __future__ import annotations

from datetime import date

from obdi.accounts import AccountBinding, AccountMap, AccountRecord, lifecycle_breach


def _registry() -> AccountMap:
    return AccountMap(
        [
            AccountBinding("starling-personal", "starling", "uid-1"),
        ],
        records=[
            AccountRecord.from_dict(
                {
                    "id": "starling-personal",
                    "kind": "current",
                    "label": "Personal (Starling)",
                    "opened": "2019-01-17",
                }
            ),
            AccountRecord.from_dict(
                {
                    "id": "hsbc-old-current",
                    "kind": "current",
                    "label": "Old HSBC current",
                    "opened": "2008-09-01",
                    "closed": "2016-05-31",
                }
            ),
            AccountRecord.from_dict(
                {
                    "id": "piggy-bank",
                    "kind": "cash",
                    "label": "Piggy bank",
                }
            ),
        ],
    )


class TestTheRegistryDeclaresAccounts:
    def test_ARecord_RoundTrips_WithLifecycleDates(self):
        registry = _registry()

        record = registry.record("hsbc-old-current")

        assert record is not None
        assert record.kind == "current"
        assert record.opened == date(2008, 9, 1)
        assert record.closed == date(2016, 5, 31)

    def test_AFeedlessAccount_ExistsWithNoBindingAtAll(self):
        # A mortgage or cash-in-a-tin has no source and never will; the
        # registry is what lets it exist anyway.
        registry = _registry()

        assert registry.record("piggy-bank") is not None
        assert "piggy-bank" in registry.declared_ids()

    def test_MetadataWindows_AreCapturedAheadOfConsumers(self):
        record = AccountRecord.from_dict(
            {
                "id": "cc",
                "kind": "credit-card",
                "limits": [
                    {"kind": "credit", "from": "2024-01-01", "amount_minor": 500000},
                    {
                        "kind": "credit",
                        "from": "2025-06-01",
                        "to": "2026-01-01",
                        "amount_minor": 750000,
                    },
                ],
                "rates": [
                    {
                        "kind": "purchase",
                        "from": "2024-01-01",
                        "to": "2025-07-01",
                        "annual_percent": 0,
                    }
                ],
            }
        )

        # Nothing consumes these yet - capturing the primitive with its
        # window is the point, because retrofitting capture is impossible.
        assert len(record.limits) == 2
        assert record.limits[1].amount_minor == 750000
        assert record.limits[1].window_to == date(2026, 1, 1)
        assert record.rates[0].annual_percent == 0

    def test_TheDeclaredLabel_WinsTheDisplayName(self):
        registry = _registry()

        labels = registry.registry_labels()

        assert labels["starling-personal"] == "Personal (Starling)"
        assert labels["piggy-bank"] == "Piggy bank"


class TestTheLifecycleGuard:
    def test_RowsBeforeOpening_AreNamedWithTheirDenominator(self):
        registry = _registry()
        record = registry.record("starling-personal")

        breach = lifecycle_breach(
            [date(2018, 12, 30), date(2019, 1, 16), date(2019, 2, 1)], record
        )

        assert breach is not None
        assert "2 of 3" in breach
        assert "before the account opened" in breach
        assert "2019-01-17" in breach

    def test_RowsAfterClosure_AreNamedToo(self):
        registry = _registry()
        record = registry.record("hsbc-old-current")

        breach = lifecycle_breach([date(2016, 6, 15), date(2016, 5, 1)], record)

        assert breach is not None
        assert "1 of 2" in breach
        assert "after the account closed" in breach

    def test_RowsInsideTheLifecycle_RaiseNothing(self):
        registry = _registry()
        record = registry.record("starling-personal")

        assert lifecycle_breach([date(2020, 1, 1)], record) is None

    def test_AnUndeclaredAccount_CannotBreach(self):
        # No registry entry means no lifecycle claim to breach - the guard
        # only speaks where a human has declared the facts it checks.
        assert lifecycle_breach([date(1990, 1, 1)], None) is None
