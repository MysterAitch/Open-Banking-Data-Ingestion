"""Does the data we hold actually agree with itself?

The store is deliberately fed the same account by several routes - an
aggregator, the bank's own API, a CSV export - because two independent sources
agreeing is real evidence, and where they disagree the disagreement IS the
finding. None of that is worth anything unless something checks.

The comparison is windowed to the OVERLAP on purpose. A CSV covering three
months and an API feed covering two years will always differ in total, and
reporting that as a discrepancy would bury the real ones under arithmetic that
was never going to match.
"""

from __future__ import annotations

from datetime import date
from typing import ClassVar

from obdi.coverage import Agreement, agreements, coverage, gaps, transpositions
from obdi.models import SourceTier, Transaction


def txn(source, day, amount, *, account="current", source_id=None, month=1, desc=None,
        tier=SourceTier.SYNTHETIC):
    return Transaction(
        account_id=account,
        amount_minor=amount,
        currency="GBP",
        value_date=date(2026, month, day),
        booking_date=date(2026, month, day),
        description=desc or f"txn {day}",
        source=source,
        source_id=source_id,
        tier=tier,
        content_key=f"k{month}{day}{amount}",
    )


class TestWhatWeHold:
    def test_Coverage_WhenSeveralSourcesFeedOneAccount_ReportsEachSeparately(self):
        rows = coverage(
            [txn("truelayer", 1, -500), txn("truelayer", 5, 2000), txn("halifax-qif", 1, -500)]
        )

        by_source = {row.source: row for row in rows}
        assert set(by_source) == {"truelayer", "halifax-qif"}
        assert by_source["truelayer"].count == 2
        assert by_source["truelayer"].earliest == date(2026, 1, 1)
        assert by_source["truelayer"].latest == date(2026, 1, 5)

    def test_Coverage_SeparatesMoneyInFromMoneyOut_NotJustTheNet(self):
        rows = coverage([txn("truelayer", 1, -500), txn("truelayer", 2, 2000)])

        row = rows[0]
        # A net figure hides the case where both sides are wrong by the same
        # amount, which is exactly what a sign-convention bug looks like.
        assert row.outflow_minor == 500
        assert row.inflow_minor == 2000
        assert row.net_minor == 1500

    def test_Coverage_ReportsHowMuchOfItCarriesADurableId(self):
        rows = coverage(
            [
                txn("truelayer", 1, -500, source_id="stable-1", tier=SourceTier.AUTHORITATIVE),
                txn("truelayer", 2, -600),
            ]
        )

        # Tells you how much of the store rests on the provider's own identity
        # versus on content matching - which is what determines how much a
        # matching change could disturb.
        assert rows[0].with_durable_id == 1
        assert rows[0].count == 2


class TestWhetherSourcesAgree:
    def test_Agreement_WhenTwoSourcesSeeTheSamePeriodIdentically_ReportsAgreement(self):
        found = agreements(
            [
                txn("truelayer", 1, -500),
                txn("truelayer", 2, 2000),
                txn("halifax-qif", 1, -500),
                txn("halifax-qif", 2, 2000),
            ]
        )

        assert len(found) == 1
        assert found[0].agrees
        assert found[0].left_count == found[0].right_count == 2

    def test_Agreement_WhenOneSourceIsMissingATransaction_SaysSo(self):
        found = agreements(
            [
                txn("truelayer", 1, -500),
                txn("truelayer", 2, 2000),
                txn("halifax-qif", 1, -500),
                txn("halifax-qif", 2, 2000),
                txn("halifax-qif", 2, -750),
            ]
        )

        assert not found[0].agrees

    def test_Agreement_WhenTheSourcesCoverDifferentPeriods_ComparesOnlyTheOverlap(self):
        # The CSV covers days 1-2; the feed covers 1-9. Comparing wholesale would
        # always "disagree" and teach the reader to ignore the report.
        found = agreements(
            [
                txn("truelayer", 1, -500),
                txn("truelayer", 2, 2000),
                txn("truelayer", 9, -9999),
                txn("halifax-qif", 1, -500),
                txn("halifax-qif", 2, 2000),
            ]
        )

        assert found[0].overlap_from == date(2026, 1, 1)
        assert found[0].overlap_to == date(2026, 1, 2)
        assert found[0].agrees, "the day-9 transaction is outside the overlap and irrelevant"

    def test_Agreement_WhenSourcesNeverOverlap_IsNotReportedAsADisagreement(self):
        found = agreements([txn("truelayer", 1, -500), txn("halifax-qif", 20, -500)])

        # Nothing to compare is not the same as comparing and differing.
        assert found == []

    def test_Agreement_OnlyComparesWithinOneAccount(self):
        found = agreements(
            [txn("truelayer", 1, -500, account="a"), txn("halifax-qif", 1, -500, account="b")]
        )

        assert found == []


class TestHolesInWhatWeHold:
    """A missing month usually means a missing file, not a quiet month.

    Only months ENCLOSED by data count. An account that stopped being used has
    empty months at the end, and flagging those would produce a permanent
    complaint about something that is simply true - the fastest way to make a
    report ignored.
    """

    def test_Gaps_WhenAMonthIsMissingBetweenTwoWithData_IsReportedAsAGap(self):
        found = gaps(
            [
                txn("halifax-qif", 1, -500),
                txn("halifax-qif", 1, -500, month=3),
            ]
        )

        assert [(g.source, g.month) for g in found] == [("halifax-qif", "2026-02")]

    def test_Gaps_WhenTheAccountSimplyStopsBeingUsed_ReportsNothing(self):
        # Two consecutive months then silence: nothing encloses the silence, so
        # there is no evidence anything is missing.
        found = gaps([txn("halifax-qif", 1, -500), txn("halifax-qif", 2, -500)])

        assert found == []

    def test_Gaps_AreReportedPerSource_SoTheMissingFileCanBeIdentified(self):
        found = gaps(
            [
                txn("halifax-qif", 1, -500),
                txn("halifax-qif", 1, -500, month=3),
                txn("truelayer", 1, -500),
                txn("truelayer", 1, -500, month=2),
                txn("truelayer", 1, -500, month=3),
            ]
        )

        # The feed is complete; only the file import has a hole, and that is
        # the one you can actually go and download.
        assert [g.source for g in found] == ["halifax-qif"]

    def test_Gaps_WhenEverySourceAgreesTheMonthIsEmpty_IsNotTreatedAsMissingData(self):
        found = gaps(
            [
                txn("halifax-qif", 1, -500),
                txn("halifax-qif", 1, -500, month=3),
                txn("truelayer", 1, -500),
                txn("truelayer", 1, -500, month=3),
            ]
        )

        # Both routes say February was quiet. Corroborated absence is evidence
        # the account was idle, not evidence that a file is missing.
        assert all(not g.contradicted for g in found)

    def test_Gaps_WhenAnotherSourceHasTheMonth_TheAbsenceIsContradictedAndNamed(self):
        found = gaps(
            [
                txn("halifax-qif", 1, -500),
                txn("halifax-qif", 1, -500, month=3),
                txn("truelayer", 1, -500),
                txn("truelayer", 1, -500, month=2),
                txn("truelayer", 1, -500, month=3),
            ]
        )

        missing = [g for g in found if g.contradicted]
        assert [(g.source, g.month, g.seen_in) for g in missing] == [
            ("halifax-qif", "2026-02", ("truelayer",))
        ]


class TestDatesReadTheWrongWayRound:
    """A transposed date is the quietest corruption available.

    Nothing looks wrong: the amount is right, the payee is right, the date is a
    real date. It only shows when a second source disagrees about WHICH day -
    and count-and-total checks are blind to it, because moving a transaction
    between months changes neither.

    Only days 1-12 can transpose; 13 upwards is unambiguous. So the classic
    signature is a file where some rows moved and others did not.
    """

    def test_Transposition_WhenTwoSourcesSwapDayAndMonth_IsDetected(self):
        found = transpositions(
            [
                txn("truelayer", 3, -2500, month=5, desc="RENT"),
                txn("halifax-qif", 5, -2500, month=3, desc="RENT"),
            ]
        )

        assert len(found) == 1
        assert {found[0].left_date, found[0].right_date} == {date(2026, 5, 3), date(2026, 3, 5)}

    def test_Transposition_WhenTheSameSourceHasBothDates_IsNotFlagged(self):
        # Two genuine payments of equal value within one source. Suspicious
        # only across sources, where the same payment cannot be in two places.
        found = transpositions(
            [
                txn("halifax-qif", 3, -2500, month=5, desc="RENT"),
                txn("halifax-qif", 5, -2500, month=3, desc="RENT"),
            ]
        )

        assert found == []

    def test_Transposition_WhenAmountsDiffer_IsNotFlagged(self):
        found = transpositions(
            [
                txn("truelayer", 3, -2500, month=5, desc="RENT"),
                txn("halifax-qif", 5, -9900, month=3, desc="RENT"),
            ]
        )

        assert found == []

    def test_Transposition_WhenDatesAreSimplyTheSame_IsNotFlagged(self):
        found = transpositions(
            [
                txn("truelayer", 5, -2500, month=5, desc="RENT"),
                txn("halifax-qif", 5, -2500, month=5, desc="RENT"),
            ]
        )

        # Day equals month here, so a swap is undetectable AND harmless.
        assert found == []

    def test_Transposition_CatchesTheMixedCase_WhereOnlyAmbiguousRowsMoved(self):
        found = transpositions(
            [
                # Day 20 cannot transpose, and agrees.
                txn("truelayer", 20, -100, month=3, desc="BILL"),
                txn("halifax-qif", 20, -100, month=3, desc="BILL"),
                # Day 4 can, and does not agree.
                txn("truelayer", 4, -700, month=9, desc="GYM"),
                txn("halifax-qif", 9, -700, month=4, desc="GYM"),
            ]
        )

        assert len(found) == 1, "the unambiguous row is fine; the ambiguous one moved"
        assert found[0].amount_minor == -700


class TestAgainstTheRealStore:
    """The reports must be right against a store fed through the real pipeline.

    Every earlier test here hand-builds Transaction lists, which encodes the
    pre-merge model: one row per source. The store does not work like that -
    supersession leaves ONE row whose source is the last writer - and the
    coverage reports were wrong against it while 356 hand-model tests passed.
    This class exists so that mistake cannot come back.
    """

    def test_Coverage_AfterACrossSourceMerge_CreditsBothSources(self, tmp_path):
        from obdi.ingest import reconcile_batch
        from obdi.store import Store

        def real(source, source_id=None):
            return Transaction(
                account_id="current",
                amount_minor=-2500,
                currency="GBP",
                value_date=date(2026, 3, 5),
                booking_date=date(2026, 3, 5),
                description="RENT",
                source=source,
                source_id=source_id,
                tier=SourceTier.AUTHORITATIVE if source_id else SourceTier.SYNTHETIC,
                content_key="shared-key",
            )

        with Store(tmp_path / "s.sqlite3") as store:
            reconcile_batch(store, [real("halifax-qif")], digest="d-csv")
            reconcile_batch(store, [real("truelayer", "tl-1")], digest="d-api")

            held = store.transactions_by_sighting()
            rows = coverage(held)

            by_source = {row.source: row for row in rows}
            # One payment, two witnesses: each source is credited with it. The
            # stored row alone would say only the last writer ever saw it.
            assert by_source["halifax-qif"].count == 1
            assert by_source["truelayer"].count == 1

    def test_Agreement_AfterACrossSourceMerge_ReportsAgreementNotDisagreement(self, tmp_path):
        from obdi.ingest import reconcile_batch
        from obdi.store import Store

        def real(source, day, amount, source_id=None):
            return Transaction(
                account_id="current",
                amount_minor=amount,
                currency="GBP",
                value_date=date(2026, 3, day),
                booking_date=date(2026, 3, day),
                description=f"PAYEE {day}",
                source=source,
                source_id=source_id,
                tier=SourceTier.AUTHORITATIVE if source_id else SourceTier.SYNTHETIC,
                content_key=f"key-{day}-{amount}",
            )

        with Store(tmp_path / "s.sqlite3") as store:
            reconcile_batch(
                store, [real("halifax-qif", 5, -2500), real("halifax-qif", 9, 1000)], digest="d1"
            )
            reconcile_batch(
                store,
                [real("truelayer", 5, -2500, "t1"), real("truelayer", 9, 1000, "t2")],
                digest="d2",
            )

            found = agreements(store.transactions_by_sighting())

            assert len(found) == 1
            assert found[0].agrees, (
                "two sources that corroborated every payment must be reported as "
                "agreeing - describing agreement as disagreement is the failure "
                "this view exists to prevent"
            )

    def test_Gaps_AfterACrossSourceMerge_DoesNotInventMissingMonths(self, tmp_path):
        from obdi.ingest import reconcile_batch
        from obdi.store import Store

        def real(source, month, source_id=None):
            return Transaction(
                account_id="current",
                amount_minor=-100 * month,
                currency="GBP",
                value_date=date(2026, month, 5),
                booking_date=date(2026, month, 5),
                description=f"BILL {month}",
                source=source,
                source_id=source_id,
                tier=SourceTier.AUTHORITATIVE if source_id else SourceTier.SYNTHETIC,
                content_key=f"key-{month}",
            )

        with Store(tmp_path / "s.sqlite3") as store:
            for month in (1, 2, 3):
                reconcile_batch(store, [real("halifax-qif", month)], digest=f"csv-{month}")
                reconcile_batch(
                    store, [real("truelayer", month, f"t{month}")], digest=f"api-{month}"
                )

            found = gaps(store.transactions_by_sighting())

            assert found == [], (
                "every month is covered by both sources; a MISSING report here "
                "would be the stored-source undercount, not a real gap"
            )


class TestSiblingAttribution:
    """A statement shows the MAIN account's view of the world: a bill paid
    directly from a savings space appears on the statement while the API files
    it under the space, and a space top-up appears as the main account's leg
    while the API holds the space's opposite leg. Both are one movement seen
    through two doors that disagree about WHERE it belongs.

    So a disagreement those rows cause can be EXPLAINED - by matching them to
    the other source's rows in SIBLING accounts - but never silently: every
    attribution names the sibling it matched (a nonsense match must be visible
    on sight), and whatever stays unmatched is shown, because the residue is
    the finding.
    """

    SIBLINGS: ClassVar[dict[str, list[str]]] = {
        "starling": [
            "starling-personal",
            "starling-space-bills",
            "starling-space-savings",
        ]
    }

    @staticmethod
    def _bracket() -> list[Transaction]:
        # Rows both sources agree on, spanning days 1..6, so the overlap
        # window encloses the interesting day and the comparison is about
        # the case rows rather than about coverage.
        return [
            txn("starling", 1, -500, account="starling-personal"),
            txn("starling", 6, 2000, account="starling-personal"),
            txn("starling-csv", 1, -500, account="starling-personal"),
            txn("starling-csv", 6, 2000, account="starling-personal"),
        ]

    @staticmethod
    def _personal(found) -> Agreement:
        return next(a for a in found if a.account_id == "starling-personal")

    def test_Agreement_BillPaidFromASpace_IsAttributedToTheSiblingNotLost(self):
        found = agreements(
            [
                *self._bracket(),
                # The statement shows the council tax leaving the account...
                txn(
                    "starling-csv", 2, -7000,
                    account="starling-personal", desc="COUNCIL TAX",
                ),
                # ...the API filed the same payment under the Bills space.
                txn(
                    "starling", 2, -7000,
                    account="starling-space-bills", desc="COUNCIL TAX",
                ),
            ],
            sibling_accounts=self.SIBLINGS,
        )

        agreement = self._personal(found)
        assert not agreement.agrees
        assert len(agreement.attributed) == 1
        match = agreement.attributed[0]
        assert match.sibling_account == "starling-space-bills"
        assert match.opposite_sign is False
        assert agreement.unexplained == ()
        text = agreement.describe()
        assert "agree once sibling attribution is counted" in text
        assert "starling-space-bills" in text

    def test_Agreement_SpaceTopUp_TheOppositeSpaceLegAccountsForTheMainLeg(self):
        found = agreements(
            [
                *self._bracket(),
                txn(
                    "starling-csv", 2, -5000,
                    account="starling-personal", desc="TO SAVINGS SPACE",
                ),
                txn(
                    "starling", 2, 5000,
                    account="starling-space-savings", desc="FROM PERSONAL",
                ),
            ],
            sibling_accounts=self.SIBLINGS,
        )

        agreement = self._personal(found)
        assert len(agreement.attributed) == 1
        assert agreement.attributed[0].opposite_sign is True
        assert agreement.attributed[0].sibling_account == "starling-space-savings"
        assert agreement.unexplained == ()

    def test_Agreement_WhenNoSiblingHoldsAMatch_TheRowIsShownUnexplained(self):
        found = agreements(
            [
                *self._bracket(),
                txn(
                    "starling-csv", 2, -450,
                    account="starling-personal", desc="NETFLIX",
                )
            ],
            sibling_accounts=self.SIBLINGS,
        )

        agreement = self._personal(found)
        assert agreement.attributed == ()
        assert len(agreement.unexplained) == 1
        assert agreement.unexplained[0].description == "NETFLIX"
        text = agreement.describe()
        assert "DISAGREE" in text
        assert "unexplained" in text
        assert "NETFLIX" in text

    def test_Agreement_EachSiblingRowExplainsOnlyOneRow(self):
        found = agreements(
            [
                *self._bracket(),
                txn(
                    "starling-csv", 2, -7000,
                    account="starling-personal", desc="COUNCIL TAX A",
                ),
                txn(
                    "starling-csv", 3, -7000,
                    account="starling-personal", desc="COUNCIL TAX B",
                ),
                txn("starling", 2, -7000, account="starling-space-bills"),
            ],
            sibling_accounts=self.SIBLINGS,
        )

        agreement = self._personal(found)
        assert len(agreement.attributed) == 1
        assert len(agreement.unexplained) == 1

    def test_Agreement_SameSignSiblingMatch_RespectsTheTwoDayWindow(self):
        found = agreements(
            [
                *self._bracket(),
                txn(
                    "starling-csv", 2, -7000,
                    account="starling-personal", desc="COUNCIL TAX",
                ),
                # Three days away: outside the same-sign window.
                txn("starling", 5, -7000, account="starling-space-bills"),
            ],
            sibling_accounts=self.SIBLINGS,
        )

        agreement = self._personal(found)
        assert agreement.attributed == ()
        assert len(agreement.unexplained) == 1

    def test_Agreement_OppositeSignSiblingMatch_RespectsTheTighterOneDayWindow(self):
        found = agreements(
            [
                *self._bracket(),
                txn(
                    "starling-csv", 2, -5000,
                    account="starling-personal", desc="TO SAVINGS SPACE",
                ),
                # Two days away: inside the same-sign window but outside the
                # opposite-sign one - an internal move lands same-day, so a
                # distant opposite row is NOT evidence of the same movement.
                txn("starling", 4, 5000, account="starling-space-savings"),
            ],
            sibling_accounts=self.SIBLINGS,
        )

        agreement = self._personal(found)
        assert agreement.attributed == ()
        assert len(agreement.unexplained) == 1

    def test_Agreement_WithoutSiblingScope_TheReportIsUnchanged(self):
        rows = [
            *self._bracket(),
            txn(
                "starling-csv", 2, -7000,
                account="starling-personal", desc="COUNCIL TAX",
            ),
            txn("starling", 2, -7000, account="starling-space-bills"),
        ]

        agreement = self._personal(agreements(rows))

        assert agreement.reconciled is False
        text = agreement.describe()
        assert "DISAGREE" in text
        assert "sibling" not in text
        assert "unexplained" not in text
