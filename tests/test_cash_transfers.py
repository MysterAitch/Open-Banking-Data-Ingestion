"""A cash withdrawal is a transfer to the cash account, and a cash deposit a transfer back.

Two worlds, every amount and name invented, every expected figure worked out before the first run.

THE HOUSEHOLD is the one `test_cash_withdrawal_measure` measures (eight withdrawals, three
deposits, the look-alikes), with a cash account declared. Its measurement says 5 transfers: the
withdrawals A (21.00, 09-10), C (50.00, 09-12), E (60.00, 09-14) and G (40.00, 2027-01-05), and
the deposit I (120.00, 09-20). So the cash account must hold exactly five legs:

    +2,100 on 2026-09-10   +5,000 on 2026-09-12   +6,000 on 2026-09-14   +4,000 on 2027-01-05
    -12,000 on 2026-09-20

each the withdrawal's size the other way on the same day, each paired with its own withdrawal and
with no other row, with the same imported id however the sources arrived and whether the store
was built live or rebuilt.

THE SMALL WORLD is one current account and the cash account, in pence. The bank's feed lists a
deposit of 1,000.00 on 2026-09-01, a cash machine withdrawal of 50.00 on the 10th, and a coffee of
3.50 on the 11th. The current account is stated 1,000.00 at the end of the 1st, 950.00 on the 10th
and 946.50 on the 11th, so it opens at nil and agrees throughout, with or without the cash account.
Cash is stated 0.00 on the 1st. With the withdrawal made a leg of cash:

    net worth today = 946.50 in the current account + 50.00 in cash = 996.50
    without it      = 946.50 (the withdrawal looked like spending)
    cash with no known balance: not counted, the net worth is the current account's 946.50
    cash stated 20.00 on the 20th after a 50.00 leg on the 10th: -30.00 was spent, the
    unitemised change the 20th.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, date, datetime

import pytest

from late_settlement_corpus import ORDERS, export_text, household
from obdi.actual_push import build_envelope
from obdi.core.models import TransactionStatus
from obdi.core.namespaces import CASH_LEG_SOURCE
from obdi.ingest.accounts import AccountRef
from obdi.ingest.cash_transfers import reconcile_cash_legs
from obdi.ingest.cash_withdrawals import AFTER_CLOSE, LEG
from obdi.ingest.family_anchors import families_of
from obdi.ingest.identity_health import identity_health
from obdi.ingest.pipeline import import_file, pair_transfers_across_store
from obdi.ingest.rebuild import rebuild_from_raw
from obdi.ingest.store import Store
from obdi.read.ledger import build_ledger
from obdi.read.overview import held_by_account
from obdi.read.position import read_position
from obdi.replay import ActualAccountBinding
from obdi.verify.agreement import standing_of
from obdi.verify.balance_anchors import effective_opening, record_stated_anchor
from obdi.verify.cash_withdrawal_measure import cash_withdrawal_report
from obdi.verify.movement_completeness import MovementCompleteness, movement_completeness
from obdi.verify.protection import ProtectionRefused, check_span, press
from obdi.web_ledger import render_ledger
from round_up_corpus import card_payment, deposit_item
from test_absorbed_rows import arrive
from test_cash_withdrawal_measure import CASH, cash_payments
from test_family_anchors import land_evidence
from test_id_tier import feed_artefact
from test_space_attribution import MAIN, MAP

D = date
T0 = datetime(2026, 10, 1, 9, 0, tzinfo=UTC)
TODAY = D(2026, 10, 5)

EXPECTED_LEGS = {
    (D(2026, 9, 10), 2100),
    (D(2026, 9, 12), 5000),
    (D(2026, 9, 14), 6000),
    (D(2027, 1, 5), 4000),
    (D(2026, 9, 20), -12000),
}


def world(tmp_path, order, *, rebuild: bool, records=(CASH,)):
    """The household arriving in `order`, the cash account declared, and the pairing pass run
    (live, as the scheduled cycle runs it) or the whole store rebuilt from raw."""
    store = household(tmp_path, order, cash_payments(), rebuild=False)
    for record in records:
        store.declare_account(record)
    if rebuild:
        assert rebuild_from_raw(store, account_map=MAP).problems == []
    else:
        pair_transfers_across_store(store, MAP)
    return store


def legs_of(store: Store, ref: str = "cash"):
    return [t for t in store.transactions_for_account(ref) if t.source == CASH_LEG_SOURCE]


def imported_ids(store: Store) -> set[str]:
    return {f"{t.content_key}:{t.occurrence}" for t in legs_of(store)}


@pytest.mark.parametrize("rebuild", [False, True], ids=["live", "rebuilt"])
@pytest.mark.parametrize("order", ORDERS, ids=["-".join(o) for o in ORDERS])
class TestTheHousehold:
    def test_CashAccount_WhenTheSourcesArriveInAnyOrder_HoldsExactlyTheFiveLegsTheMeasurementSaid(
        self, tmp_path, order, rebuild
    ):
        store = world(tmp_path, order, rebuild=rebuild)
        try:
            held = {(t.value_date, t.amount_minor) for t in legs_of(store)}

            assert held == EXPECTED_LEGS
            assert len(legs_of(store)) == 5
        finally:
            store.close()

    def test_EachLeg_WhenMade_IsPairedWithItsOwnWithdrawalAndNoOtherRow(
        self, tmp_path, order, rebuild
    ):
        store = world(tmp_path, order, rebuild=rebuild)
        try:
            rows = {t.entity_id: t for t in store.all_transactions()}
            cash_pairs = [
                (rows[a], rows[b])
                for a, b in store.confirmed_transfer_pairs()
                if CASH_LEG_SOURCE in (rows[a].source, rows[b].source)
            ]

            assert len(cash_pairs) == 5
            for paid, received in cash_pairs:
                assert paid.amount_minor == -received.amount_minor
                assert paid.value_date == received.value_date
                assert "cash" in (paid.account_id, received.account_id)
                assert MAIN in (paid.account_id, received.account_id)
            ends = [e for pair in store.confirmed_transfer_pairs() for e in pair]
            assert len(ends) == len(set(ends)), "a row is in more than one pair"
        finally:
            store.close()

    def test_Withdrawals_WhenLegsAreMade_AreLeftExactlyAsTheBankStatedThem(
        self, tmp_path, order, rebuild
    ):
        (tmp_path / "plain").mkdir()
        plain = household(tmp_path / "plain", order, cash_payments(), rebuild=rebuild)
        try:
            before = sorted(
                (t.description.title(), t.amount_minor, t.value_date, t.status.value)
                for t in plain.transactions_for_account(MAIN)
            )
        finally:
            plain.close()
        store = world(tmp_path, order, rebuild=rebuild)
        try:
            after = sorted(
                (t.description.title(), t.amount_minor, t.value_date, t.status.value)
                for t in store.transactions_for_account(MAIN)
            )

            assert after == before
        finally:
            store.close()

    def test_Measurement_WhenTheLegsAreMade_SaysTheRuleHoldsWhatItSaidItWouldMake(
        self, tmp_path, order, rebuild
    ):
        store = world(tmp_path, order, rebuild=rebuild)
        try:
            report = cash_withdrawal_report(store, store.declared_accounts())
            text = "\n".join(report.sentences())

            assert len(report.would_make) == 5 == len(report.held)
            assert "5 transfers made by the rule are held: 4 withdrawals and 1 deposit" in text
            assert "The rule would make 5 transfers with the cash account" in text
        finally:
            store.close()

    def test_Pass_WhenRunAgain_ChangesNothing(self, tmp_path, order, rebuild):
        store = world(tmp_path, order, rebuild=rebuild)
        try:
            first = sorted((t.entity_id, f"{t.content_key}:{t.occurrence}") for t in legs_of(store))

            again = reconcile_cash_legs(store)

            assert (again.made, again.removed, again.kept) == (0, 0, 5)
            assert first == sorted(
                (t.entity_id, f"{t.content_key}:{t.occurrence}") for t in legs_of(store)
            )
        finally:
            store.close()


class TestTheLegsIdentity:
    def test_ImportedIds_WhateverTheArrivalOrderAndWhetherLiveOrRebuilt_AreTheSame(
        self, tmp_path
    ):
        found = []
        for number, order in enumerate(ORDERS):
            for rebuild in (False, True):
                directory = tmp_path / f"{number}-{rebuild}"
                directory.mkdir()
                store = world(directory, order, rebuild=rebuild)
                found.append(imported_ids(store))
                store.close()

        assert len(found[0]) == 5
        assert all(ids == found[0] for ids in found)

    def test_Leg_WhenAnotherSourceSightsTheWithdrawalLater_KeepsItsIdentityAndIsNotRepeated(
        self, tmp_path
    ):
        store = world(tmp_path, ("feed", "export", "aggregator"), rebuild=False)
        try:
            before = {t.entity_id: f"{t.content_key}:{t.occurrence}" for t in legs_of(store)}
            # A second, overlapping export listing the same withdrawals and one more row, and
            # the feed fetched again with the same items.
            listed = [("Sundries A", -2100, D(2026, 9, 10)), ("Sundries C", -5000, D(2026, 9, 12))]
            second = tmp_path / "second-export.csv"
            second.write_text(export_text([("Deposit", 100000, D(2026, 9, 1)), *listed]),
                              encoding="utf-8")
            import_file(store, second, account_id=MAIN, account_map=MAP)
            items = [p.feed_item() for p in cash_payments() if p.in_feed]
            arrive(store, feed_artefact([*items, card_payment("f-new", "Newsagent", 310, 23)]))
            pair_transfers_across_store(store, MAP)

            after = {t.entity_id: f"{t.content_key}:{t.occurrence}" for t in legs_of(store)}

            assert after == before
        finally:
            store.close()


class TestWhoMakesNothing:
    def test_Rule_WhenNoAccountIsDeclaredAsTheCashAccount_MakesNothingAndBreaksNothing(
        self, tmp_path
    ):
        store = world(tmp_path, ORDERS[0], rebuild=False, records=())
        try:
            assert legs_of(store) == []
            assert store.transactions_with_source(CASH_LEG_SOURCE) == []
            report = movement_completeness(store)
            assert report.leg_faults == []
        finally:
            store.close()

    def test_Rule_WhenTwoAccountsAreDeclaredAsTheCashAccount_MakesNothing(self, tmp_path):
        purse = replace(CASH, ref=AccountRef("purse"), label="Purse")
        store = world(tmp_path, ORDERS[0], rebuild=False, records=(CASH, purse))
        try:
            assert store.transactions_with_source(CASH_LEG_SOURCE) == []
        finally:
            store.close()

    def test_Rule_WhenTheDesignationIsRemoved_TakesTheLegsAwayWithTheirPairs(self, tmp_path):
        store = world(tmp_path, ORDERS[0], rebuild=False)
        try:
            assert len(legs_of(store)) == 5

            store.declare_account(replace(CASH, kind="balance-only"))
            pair_transfers_across_store(store, MAP)

            assert store.transactions_with_source(CASH_LEG_SOURCE) == []
            rows = {t.entity_id: t for t in store.all_transactions()}
            assert not any(
                rows[a].account_id == "cash" or rows[b].account_id == "cash"
                for a, b in store.confirmed_transfer_pairs()
                if a in rows and b in rows
            )
        finally:
            store.close()

    def test_Rule_WhenTheCashAccountIsClosed_MakesNoLegDatedAfterItAndKeepsTheEarlierOnes(
        self, tmp_path
    ):
        closed = replace(CASH, closed=D(2026, 9, 15))
        store = world(tmp_path, ORDERS[0], rebuild=False, records=(closed,))
        try:
            held = {(t.value_date, t.amount_minor) for t in legs_of(store)}

            assert held == {(D(2026, 9, 10), 2100), (D(2026, 9, 12), 5000), (D(2026, 9, 14), 6000)}
            report = cash_withdrawal_report(store, store.declared_accounts())
            text = "\n".join(report.sentences())
            assert "closed on 2026-09-15" in text
            assert "dated after the cash account closed 2" in text
            assert {r.judgement.outcome for r in report.accounts[0].readings} >= {AFTER_CLOSE, LEG}
        finally:
            store.close()

    def test_Rule_WhenTheCashAccountIsOpenAgain_MakesTheLaterLegsToo(self, tmp_path):
        closed = replace(CASH, closed=D(2026, 9, 15))
        store = world(tmp_path, ORDERS[0], rebuild=False, records=(closed,))
        try:
            assert len(legs_of(store)) == 3

            store.declare_account(replace(CASH, closed=None))
            pair_transfers_across_store(store, MAP)

            assert {(t.value_date, t.amount_minor) for t in legs_of(store)} == EXPECTED_LEGS
        finally:
            store.close()


BINDINGS = [ActualAccountBinding(MAIN, "act-main"), ActualAccountBinding("cash", "act-cash")]


def small_world(tmp_path, *, cash: str = "counted", withdrawal: dict | None = None):
    """The small world's feed landed, the accounts' known balances stated, nothing paired yet.

    `cash` is "counted" (stated 0.00 on the 1st), "unknown" (declared as the cash account with
    no known balance), or "plain" (declared balance-only, so not the cash account).
    """
    store = Store(tmp_path / "small.sqlite3")
    land_evidence(store)
    atm = card_payment("f-atm", "Cash Point", 5000, 10, sourceSubType="ATM")
    atm.update(withdrawal or {})
    deposit = deposit_item()
    deposit["amount"]["minorUnits"] = 100000
    arrive(store, feed_artefact([deposit, atm, card_payment("f-coffee", "Coffee", 350, 11)]))
    stated = (("2026-09-01", "1000.00"), ("2026-09-10", "950.00"), ("2026-09-11", "946.50"))
    for day, figure in stated:
        record_stated_anchor(store, MAIN, day, figure, today=TODAY)
    kind = "balance-only" if cash == "plain" else CASH.kind
    store.declare_account(replace(CASH, kind=kind))
    if cash in ("counted", "plain"):
        record_stated_anchor(store, "cash", "2026-09-01", "0.00", today=TODAY)
    return store


class TestTheEffectOnWhatIsKnown:
    def test_NetWorth_WhenTheCashAccountIsCounted_IsNotReducedByTheWithdrawal(self, tmp_path):
        store = small_world(tmp_path)
        try:
            pair_transfers_across_store(store, MAP)

            position = read_position(store, today=TODAY)

            assert position.net_worth is not None
            assert position.net_worth.minor == 99650
            assert position.accounts_counted == 2
        finally:
            store.close()

    def test_NetWorth_WhenCashIsNotTheCashAccount_FallsByTheWithdrawal(self, tmp_path):
        store = small_world(tmp_path, cash="plain")
        try:
            pair_transfers_across_store(store, MAP)

            position = read_position(store, today=TODAY)

            assert legs_of(store) == []
            assert position.net_worth is not None
            assert position.net_worth.minor == 94650
        finally:
            store.close()

    def test_NetWorth_WhenTheCashAccountHasNoKnownBalance_IsTheCurrentAccountsAlone(
        self, tmp_path
    ):
        store = small_world(tmp_path, cash="unknown")
        try:
            pair_transfers_across_store(store, MAP)

            position = read_position(store, today=TODAY)

            assert position.net_worth is not None
            assert position.net_worth.minor == 94650
            (uncounted,) = [a for a in position.uncounted if a.ref == "cash"]
            assert uncounted.state == "no-opening"
            assert uncounted.moved is not None and uncounted.moved.minor == 5000
        finally:
            store.close()

    def test_CurrentAccount_WhenTheLegIsMade_StillAgreesWithEveryKnownBalance(self, tmp_path):
        store = small_world(tmp_path)
        try:
            opening = effective_opening(store, MAIN)
            before = [r.agrees for r in opening.readings]
            pair_transfers_across_store(store, MAP)

            after = effective_opening(store, MAIN)
            standing = standing_of(after, [MAIN], MovementCompleteness())

            assert before == [r.agrees for r in after.readings] == [None, True, True]
            assert after.differing == []
            assert standing.own == standing_of(opening, [MAIN], MovementCompleteness()).own
        finally:
            store.close()

    def test_UnitemisedChange_WhenALegLandedBetweenTwoStatedCashBalances_IsWhatWasSpent(
        self, tmp_path
    ):
        store = small_world(tmp_path)
        try:
            record_stated_anchor(store, "cash", "2026-09-20", "20.00", today=TODAY)
            before = [t.amount_minor for t in effective_opening(store, "cash").unitemised]
            pair_transfers_across_store(store, MAP)

            after = [
                (t.value_date.isoformat(), t.amount_minor)
                for t in effective_opening(store, "cash").unitemised
            ]

            assert before == [2000]
            assert after == [("2026-09-20", -3000)]
        finally:
            store.close()


class TestWhatBecomesOfALegWithItsWithdrawal:
    def test_Leg_WhenTheWithdrawalIsStillPending_IsNotMadeUntilItIsBooked(self, tmp_path):
        store = small_world(tmp_path, withdrawal={"status": "PENDING"})
        try:
            pair_transfers_across_store(store, MAP)
            assert legs_of(store) == []

            arrive(
                store,
                feed_artefact(
                    [card_payment("f-atm", "Cash Point", 5000, 10, sourceSubType="ATM")]
                ),
            )
            pair_transfers_across_store(store, MAP)

            held = [(t.value_date, t.amount_minor) for t in legs_of(store)]

            assert held == [(D(2026, 9, 10), 5000)]
        finally:
            store.close()

    def test_Leg_WhenTheWithdrawalWasBookedFirstAndTheFeedLaterSaysPending_IsNotMade(
        self, tmp_path
    ):
        """The opposite arrival: a payment the feed lists pending alone never gets a leg, and a
        rebuild from the same artefacts reaches the same state."""
        store = small_world(tmp_path, withdrawal={"status": "PENDING"})
        try:
            pair_transfers_across_store(store, MAP)
            rebuild_from_raw(store, account_map=MAP)

            assert legs_of(store) == []
        finally:
            store.close()

    def test_Leg_WhenTheBankLaterDeclinesTheWithdrawal_IsRemovedLiveAndInARebuild(
        self, tmp_path
    ):
        store = small_world(tmp_path)
        try:
            pair_transfers_across_store(store, MAP)
            assert len(legs_of(store)) == 1

            declined = card_payment("f-atm", "Cash Point", 5000, 10, sourceSubType="ATM",
                                    status="DECLINED")
            arrive(store, feed_artefact([declined]))
            from obdi.ingest.declined_items import void_declined_items

            void_declined_items(store)
            pair_transfers_across_store(store, MAP)
            live = legs_of(store)
            rebuild_from_raw(store, account_map=MAP)

            assert live == []
            assert legs_of(store) == []
        finally:
            store.close()

    def test_Leg_WhenTheWithdrawalIsRedated_IsOneLegOnTheNewDay(self, tmp_path):
        store = small_world(tmp_path)
        try:
            pair_transfers_across_store(store, MAP)
            (first,) = legs_of(store)

            moved = card_payment("f-atm", "Cash Point", 5000, 10, sourceSubType="ATM")
            moved["transactionTime"] = "2026-09-12T10:00:00.000Z"
            arrive(store, feed_artefact([moved]))
            pair_transfers_across_store(store, MAP)

            (second,) = legs_of(store)
            assert second.value_date == D(2026, 9, 12)
            assert second.amount_minor == first.amount_minor
        finally:
            store.close()

    def test_Leg_WhenTheWithdrawalIsReversedByTheBank_IsRemoved(self, tmp_path):
        store = small_world(tmp_path)
        try:
            pair_transfers_across_store(store, MAP)
            assert len(legs_of(store)) == 1

            reversed_item = card_payment("f-atm", "Cash Point", 5000, 10, sourceSubType="ATM",
                                         status="REVERSED")
            arrive(store, feed_artefact([reversed_item]))
            pair_transfers_across_store(store, MAP)

            rows = {t.entity_id: t for t in store.transactions_for_account(MAIN)}
            assert any(t.status is TransactionStatus.REVERSED for t in rows.values())
            assert legs_of(store) == []
        finally:
            store.close()


class TestEveryReaderGetsTheLegFromTheStore:
    def test_Pages_WhenTheLegIsMade_ReadAsATransferWithTheOtherAccount(self, tmp_path):
        store = small_world(tmp_path)
        try:
            pair_transfers_across_store(store, MAP)
            families = families_of(store, MAP)

            main = render_ledger(
                build_ledger(store, MAIN, "2026-09", bound=True, families=families),
                unmasked=False,
            ).decode()
            cash = render_ledger(
                build_ledger(store, "cash", "2026-09", bound=True, families=families),
                unmasked=False,
            ).decode()

            assert "transfer with cash" in main
            assert f"transfer with {MAIN}" in cash
        finally:
            store.close()

    def test_Push_WhenTheLegIsMade_SendsBothRowsAndListsThePair(self, tmp_path):
        store = small_world(tmp_path)
        try:
            pair_transfers_across_store(store, MAP)
            bindings = BINDINGS
            envelope =build_envelope(store, bindings, {MAIN: "Current", "cash": "Cash"})

            accounts = envelope["accounts"]
            assert isinstance(accounts, dict)
            cash_rows = [r for r in accounts["act-cash"] if r["amount"] == 5000]
            main_rows = [r for r in accounts["act-main"] if r["amount"] == -5000]
            assert len(cash_rows) == 1 and len(main_rows) == 1
            transfers = envelope["transfers"]
            assert isinstance(transfers, list)
            pair = [
                t for t in transfers
                if t["debit"]["imported_id"] == main_rows[0]["imported_id"]
            ]
            assert len(pair) == 1
            assert pair[0]["credit"]["imported_id"] == cash_rows[0]["imported_id"]
            assert pair[0]["credit"]["account"] == "act-cash"
        finally:
            store.close()

    def test_Push_WhenTheStoreIsRebuilt_SendsTheSameImportedIdsForTheLeg(self, tmp_path):
        store = small_world(tmp_path)
        try:
            pair_transfers_across_store(store, MAP)
            bindings = BINDINGS

            def ids() -> set[str]:
                accounts = build_envelope(store, bindings, {})["accounts"]
                assert isinstance(accounts, dict)
                return {r["imported_id"] for r in accounts["act-cash"] if r["amount"] == 5000}

            first = ids()
            rebuild_from_raw(store, account_map=MAP)

            assert len(first) == 1
            assert ids() == first
        finally:
            store.close()

    def test_Push_WhenTheLegIsMade_SendsItClearedBecauseItsWithdrawalIsBooked(self, tmp_path):
        store = small_world(tmp_path)
        try:
            pair_transfers_across_store(store, MAP)

            accounts = build_envelope(store, BINDINGS, {})["accounts"]

            assert isinstance(accounts, dict)
            (leg,) = [r for r in accounts["act-cash"] if r["amount"] == 5000]
            assert leg["cleared"] is True
        finally:
            store.close()

    def test_SourceCounts_WhenTheLegIsMade_NameNoSourceThatListedIt(self, tmp_path):
        store = small_world(tmp_path)
        try:
            pair_transfers_across_store(store, MAP)

            health = identity_health(store).describe()
            _, sources = held_by_account(store)

            assert "cash-leg" not in health
            assert "cash" not in sources
            assert sources[MAIN] == {"starling"}
        finally:
            store.close()

    def test_MovementChecks_WhenTheLegIsMade_CountThePairCompleteAndReportNoMissingLeg(
        self, tmp_path
    ):
        store = small_world(tmp_path)
        try:
            before = movement_completeness(store)
            pair_transfers_across_store(store, MAP)

            after = movement_completeness(store)

            assert after.leg_faults == []
            assert after.legs_verified == before.legs_verified + 1
            assert after.pairs_unverifiable == before.pairs_unverifiable
            # The leg is no row a source lists, so no listing is said to be missing it or to
            # have a surplus.
            assert [f for f in after.row_faults if f.account == "cash"] == []
            assert len(after.row_faults) == len(before.row_faults)
        finally:
            store.close()


class TestProtection:
    def test_Protection_WhenTheCurrentAccountIsProtectedAndTheLegLands_ReadsUnchanged(
        self, tmp_path
    ):
        store = small_world(tmp_path)
        try:
            opening = effective_opening(store, MAIN)
            standing = standing_of(opening, [MAIN], MovementCompleteness())
            press(store, MAIN, "2026-09-11", opening=opening, standing=standing, now=T0)
            record = store.protection_record(MAIN)
            assert record is not None and check_span(store, record).intact

            pair_transfers_across_store(store, MAP)

            record = store.protection_record(MAIN)
            assert record is not None and check_span(store, record).intact
        finally:
            store.close()

    def test_Protection_WhenAskedForTheCashAccount_IsRefusedBecauseItIsFollowedByItsBalances(
        self, tmp_path
    ):
        """A cash account is balance-only, and a balance-only account cannot be protected, so no
        leg can land inside a protected span of it: the question has no case to test."""
        store = small_world(tmp_path)
        try:
            record_stated_anchor(store, "cash", "2026-09-12", "0.00", today=TODAY)
            opening = effective_opening(store, "cash")
            standing = standing_of(opening, ["cash"], MovementCompleteness())

            with pytest.raises(ProtectionRefused, match="known balances alone"):
                press(store, "cash", "2026-09-12", opening=opening, standing=standing, now=T0)
        finally:
            store.close()

    def test_Protection_WhenTheCurrentAccountsSpanLosesItsCashPartner_StillReadsUnchanged(
        self, tmp_path
    ):
        store = small_world(tmp_path)
        try:
            pair_transfers_across_store(store, MAP)
            opening = effective_opening(store, MAIN)
            standing = standing_of(opening, [MAIN], MovementCompleteness())
            press(store, MAIN, "2026-09-11", opening=opening, standing=standing, now=T0)

            store.declare_account(replace(CASH, kind="balance-only"))
            pair_transfers_across_store(store, MAP)

            record = store.protection_record(MAIN)
            assert record is not None and check_span(store, record).intact
        finally:
            store.close()


class TestCost:
    def test_Pass_WhenTheStoreHoldsTenTimesTheRows_IssuesTheSameNumberOfStatements(
        self, tmp_path
    ):
        def statements(rows: int) -> int:
            directory = tmp_path / f"n{rows}"
            directory.mkdir()
            with Store(directory / "cost.sqlite3") as store:
                land_evidence(store)
                items = [
                    card_payment(f"f-{n}", f"Shop {n}", 100 + n, 1 + n % 28) for n in range(rows)
                ]
                items.append(card_payment("f-atm", "Cash Point", 5000, 10, sourceSubType="ATM"))
                arrive(store, feed_artefact(items))
                store.declare_account(CASH)
                issued: list[str] = []
                store.connection.set_trace_callback(issued.append)
                try:
                    reconcile_cash_legs(store)
                finally:
                    store.connection.set_trace_callback(None)
                assert len(legs_of(store)) == 1
                return sum(1 for sql in issued if sql.lstrip().upper().startswith("SELECT"))

        assert statements(40) == statements(400)

    def test_Pass_WhenNoCashAccountIsDeclared_ReadsNoRowOfAnyAccount(self, tmp_path):
        with Store(tmp_path / "none.sqlite3") as store:
            land_evidence(store)
            arrive(store, feed_artefact([card_payment("f-atm", "Cash Point", 5000, 10,
                                                      sourceSubType="ATM")]))
            issued: list[str] = []
            store.connection.set_trace_callback(issued.append)
            try:
                reconcile_cash_legs(store)
            finally:
                store.connection.set_trace_callback(None)

        assert not any("FROM transactions WHERE account_id" in sql for sql in issued)
