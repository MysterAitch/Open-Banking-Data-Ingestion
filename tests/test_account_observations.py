"""Account facts change over time, so statements must not overwrite - they
must ACCUMULATE, and the order they arrive in must not matter.

A statement is a dated observation: "as at 11 July, the purchase rate was
X, the credit limit was Y, and a promotional rate ran until Z". Treating
those as mutable account properties would make the answer depend on which
PDF happened to be imported last - so a July statement filed before a
February one would erase a promotional window expiring in March, silently
and irrecoverably.

They are therefore stored as observations and PROJECTED, never assigned.
The projection is a pure function of the set of observations, which is what
makes import order irrelevant by construction rather than by care - the
same guarantee transactions already get from being derived rather than
mutated. This is the ladder applied to account facts: a fact a person
declared outranks one a statement implied.
"""

from __future__ import annotations

from datetime import date
from itertools import permutations

from obdi.account_observations import Observation, current_view, project


def rate(observed: date, kind: str, percent: float, *, until: date | None = None,
         since: date | None = None) -> Observation:
    return Observation(
        account_id="santander-cc",
        fact="rate",
        kind=kind,
        observed_at=observed,
        value=str(percent),
        window_from=since,
        window_to=until,
        source="2026-07 statement",
    )


def limit(observed: date, amount: str) -> Observation:
    return Observation(
        account_id="santander-cc",
        fact="credit_limit",
        kind="",
        observed_at=observed,
        value=amount,
        source="statement",
    )


PROMOTIONAL = rate(date(2025, 10, 11), "balance_transfer", 0.0, until=date(2027, 3, 11))
PURCHASES_OLD = rate(date(2025, 10, 11), "purchases", 23.9)
PURCHASES_NEW = rate(date(2026, 7, 11), "purchases", 24.9)
LIMIT_OLD = limit(date(2025, 10, 11), "1200.00")
LIMIT_NEW = limit(date(2026, 7, 11), "3000.00")

EVERY_STATEMENT = [PROMOTIONAL, PURCHASES_OLD, PURCHASES_NEW, LIMIT_OLD, LIMIT_NEW]


class TestImportOrderCannotMatter:
    def test_EveryPossibleOrder_ProducesTheSameResult(self):
        # Exhaustive rather than sampled: with five statements there are
        # 120 arrival orders and no reason to check only some of them.
        expected = project(EVERY_STATEMENT)

        for arrival in permutations(EVERY_STATEMENT):
            assert project(list(arrival)) == expected

    def test_APromotionalWindow_SurvivesALaterStatementFiledFirst(self):
        # The case that motivated the question: July imported, then
        # February, then April - the March expiry must still be recorded.
        late_first = project([PURCHASES_NEW, LIMIT_NEW, PROMOTIONAL])

        promotional = [
            entry for entry in late_first if entry.kind == "balance_transfer"
        ]
        assert len(promotional) == 1
        assert promotional[0].window_to == date(2027, 3, 11)
        assert promotional[0].value == "0.0"

    def test_ReimportingTheSameStatement_ChangesNothing(self):
        once = project(EVERY_STATEMENT)
        twice = project([*EVERY_STATEMENT, *EVERY_STATEMENT])

        assert once == twice


class TestTheLatestObservationWinsPerWindow:
    def test_ANewerStatement_SupersedesAnOlderOne_ForTheSameFact(self):
        view = current_view(EVERY_STATEMENT, on=date(2026, 8, 1))

        assert view["credit_limit"] == "3000.00"
        assert view["rate:purchases"] == "24.9"

    def test_AnOlderStatement_NeverOverwritesANewerOne(self):
        # Same set, but the old statement observed last - the answer must
        # not change.
        reordered = [PURCHASES_NEW, LIMIT_NEW, PURCHASES_OLD, LIMIT_OLD]

        view = current_view(reordered, on=date(2026, 8, 1))

        assert view["credit_limit"] == "3000.00"
        assert view["rate:purchases"] == "24.9"

    def test_AnExpiredWindow_IsRetained_ButNotCurrent(self):
        # History is evidence: the promotional rate existed and its record
        # must survive its expiry, while no longer describing today.
        after_expiry = current_view(EVERY_STATEMENT, on=date(2027, 6, 1))

        assert "rate:balance_transfer" not in after_expiry
        assert any(
            entry.kind == "balance_transfer" for entry in project(EVERY_STATEMENT)
        ), "the expired window is still on the record"

    def test_AFutureWindow_IsNotYetCurrent(self):
        reversion = rate(
            date(2026, 7, 11), "balance_transfer", 24.9, since=date(2027, 3, 12)
        )

        during = current_view([PROMOTIONAL, reversion], on=date(2026, 8, 1))
        after = current_view([PROMOTIONAL, reversion], on=date(2027, 4, 1))

        assert during["rate:balance_transfer"] == "0.0"
        assert after["rate:balance_transfer"] == "24.9"


class TestWhatIsDeclaredOutranksWhatIsInferred:
    def test_ADeclaredFact_BeatsAStatementDerivedOne(self):
        declared = Observation(
            account_id="santander-cc",
            fact="credit_limit",
            kind="",
            observed_at=date(2025, 1, 1),
            value="5000.00",
            source="registry",
            provenance="human",
        )

        view = current_view([*EVERY_STATEMENT, declared], on=date(2026, 8, 1))

        assert view["credit_limit"] == "5000.00", (
            "a person's declaration outranks a machine's reading, even when "
            "the machine's is more recent"
        )
