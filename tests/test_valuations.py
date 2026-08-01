"""Recording assets that have no transaction stream.

A pension pot, a fund or a property is not a ledger you sum - it is a value you
observe periodically, where the change between observations mixes contributions,
growth and fees and usually cannot be decomposed from the statement.

Two things the tests pin, because both are irreversible if got wrong:

Units and unit price are kept whenever a statement supplies them, even though
nothing consumes them yet. Storing only the total forecloses proper
unit-and-price modelling permanently; storing both costs two columns.

A defined benefit entitlement has no pot at all. It is a promise of income, and
there is no agreed way to capitalise one - the UK alone uses several
incompatible conventions. So the promise is recorded as the fact it is, and any
capital figure is derived from a multiplier held as data, never stored as
though it had been observed.
"""

from datetime import date

import pytest

from obdi.store import Store
from obdi.valuations import (
    Asset,
    AssetKind,
    ValuationError,
    capital_value_of,
    record_observation,
)


@pytest.fixture
def store(tmp_path):
    with Store(tmp_path / "s.sqlite3") as opened:
        yield opened


class TestRecordingObservations:
    def test_Pot_WhenStatementArrives_ValueRecordedAgainstItsDate(self, store):
        asset = Asset(asset_id="workplace-pension", kind=AssetKind.DEFINED_CONTRIBUTION)
        record_observation(
            store, asset, observed_at=date(2026, 4, 5), value_minor=4231700, source="statement"
        )

        held = store.valuations_for("workplace-pension")
        assert len(held) == 1
        assert held[0]["value_minor"] == 4231700

    def test_Pot_WhenObservedAgainLater_BothObservationsKept(self, store):
        # A valuation series, not a current balance: the history is the point.
        asset = Asset(asset_id="workplace-pension", kind=AssetKind.DEFINED_CONTRIBUTION)
        record_observation(
            store, asset, observed_at=date(2025, 4, 5), value_minor=4231700, source="statement"
        )
        record_observation(
            store, asset, observed_at=date(2026, 4, 5), value_minor=4590800, source="statement"
        )

        assert len(store.valuations_for("workplace-pension")) == 2

    def test_Pot_WhenSameStatementRecordedTwice_NotDuplicated(self, store):
        asset = Asset(asset_id="workplace-pension", kind=AssetKind.DEFINED_CONTRIBUTION)
        for _ in range(2):
            record_observation(
                store, asset, observed_at=date(2026, 4, 5), value_minor=4231700, source="statement"
            )

        assert len(store.valuations_for("workplace-pension")) == 1

    def test_Pot_WhenStatementGivesUnits_UnitsAndPriceKept(self, store):
        # Nothing reads these yet. Storing only the total would foreclose
        # unit-and-price modelling permanently, and it costs two columns.
        asset = Asset(asset_id="workplace-pension", kind=AssetKind.DEFINED_CONTRIBUTION)
        record_observation(
            store,
            asset,
            observed_at=date(2026, 4, 5),
            value_minor=4231700,
            source="statement",
            units="24210.55",
            unit_price_minor=17480,
        )

        held = store.valuations_for("workplace-pension")[0]
        assert held["units"] == "24210.55"
        assert held["unit_price_minor"] == 17480

    def test_Pot_WhenRecorded_DocumentReferenceKept(self, store):
        # Provenance: the statement it came from lives in Paperless, and the
        # observation points at it rather than restating it.
        asset = Asset(asset_id="workplace-pension", kind=AssetKind.DEFINED_CONTRIBUTION)
        record_observation(
            store,
            asset,
            observed_at=date(2026, 4, 5),
            value_minor=4231700,
            source="statement",
            document_ref="paperless:1234",
        )

        assert store.valuations_for("workplace-pension")[0]["document_ref"] == "paperless:1234"


class TestDefinedBenefitIsNotAPot:
    def test_Entitlement_WhenRecordedAsAPotValue_Refused(self, store):
        # There is no pot. Recording one invents a number that was never
        # observed and cannot be reconciled against anything.
        asset = Asset(asset_id="final-salary", kind=AssetKind.DEFINED_BENEFIT)
        with pytest.raises(ValuationError, match="no pot"):
            record_observation(
                store, asset, observed_at=date(2026, 4, 5), value_minor=100000, source="statement"
            )

    def test_Entitlement_WhenRecordedAsAnnualIncome_Accepted(self, store):
        # The primitive fact a statement actually supplies.
        asset = Asset(asset_id="final-salary", kind=AssetKind.DEFINED_BENEFIT)
        record_observation(
            store,
            asset,
            observed_at=date(2026, 4, 5),
            annual_income_minor=1240000,
            source="statement",
        )

        assert store.valuations_for("final-salary")[0]["annual_income_minor"] == 1240000

    def test_Entitlement_WhenCapitalised_DerivedFromAMultiplierHeldAsData(self):
        # No agreed convention exists - the UK alone uses several incompatible
        # ones - so the multiplier is configuration, and the result is labelled
        # derived rather than stored as though observed.
        asset = Asset(
            asset_id="final-salary",
            kind=AssetKind.DEFINED_BENEFIT,
            capitalisation_multiplier=20,
        )
        assert capital_value_of(asset, annual_income_minor=1240000) == 24800000

    def test_Entitlement_WhenMultiplierChanged_FigureChangesWithIt(self):
        # The point of holding it as data: 16x, 20x and 25x are all defensible
        # for different purposes, and the answer should be re-runnable.
        asset = Asset(
            asset_id="final-salary",
            kind=AssetKind.DEFINED_BENEFIT,
            capitalisation_multiplier=16,
        )
        assert capital_value_of(asset, annual_income_minor=1240000) == 19840000

    def test_Entitlement_WhenNoMultiplierSet_RefusesToGuess(self):
        asset = Asset(asset_id="final-salary", kind=AssetKind.DEFINED_BENEFIT)
        with pytest.raises(ValuationError, match="multiplier"):
            capital_value_of(asset, annual_income_minor=1240000)


class TestStatePensionIsExcluded:
    def test_StatePension_WhenRecorded_TreatedAsProjectedIncomeNotWealth(self, store):
        # No contractual entitlement exists, so it is a social benefit rather
        # than pension wealth. Tracked as income, never capitalised.
        asset = Asset(asset_id="state-pension", kind=AssetKind.STATE_PENSION)
        record_observation(
            store,
            asset,
            observed_at=date(2026, 4, 5),
            annual_income_minor=1180000,
            source="forecast",
        )

        assert store.valuations_for("state-pension")[0]["annual_income_minor"] == 1180000

    def test_StatePension_WhenCapitalised_Refused(self):
        asset = Asset(
            asset_id="state-pension",
            kind=AssetKind.STATE_PENSION,
            capitalisation_multiplier=20,
        )
        with pytest.raises(ValuationError, match="not wealth"):
            capital_value_of(asset, annual_income_minor=1180000)


class TestValidation:
    def test_Observation_WhenNeitherValueNorIncomeGiven_Refused(self, store):
        asset = Asset(asset_id="workplace-pension", kind=AssetKind.DEFINED_CONTRIBUTION)
        with pytest.raises(ValuationError):
            record_observation(store, asset, observed_at=date(2026, 4, 5), source="statement")

    def test_Observation_WhenDatedInTheFuture_Refused(self, store):
        # A valuation is an observation. One dated ahead is a typo, and it
        # would sort to the end of the series and read as the current value.
        asset = Asset(asset_id="workplace-pension", kind=AssetKind.DEFINED_CONTRIBUTION)
        with pytest.raises(ValuationError, match="future"):
            record_observation(
                store,
                asset,
                observed_at=date(2099, 1, 1),
                value_minor=4231700,
                source="statement",
            )
