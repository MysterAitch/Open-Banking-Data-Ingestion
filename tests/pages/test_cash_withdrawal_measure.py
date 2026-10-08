"""How many stored transactions are cash movements by what a source states, and what the rule takes.

The household is the late-settlement household (a main account, its Space, an export, and an
aggregator) with eight withdrawals, three deposits, two payments whose descriptions only LOOK like
a cash machine, and a plain payment. Every name and amount is invented. Each known answer below
was decided before the first run, and each source arrives in each of six orders, live and then
rebuilt from the landed artefacts: the answer may not depend on either.

THE WORDS are those a real store showed: the feed says a cash machine as sourceSubType ATM and
cash paid in as source CASH_DEPOSIT; the aggregator says cash as transaction_category CASH.

THE WITHDRAWALS (the main account, September 2026 unless said; all money out unless said):

    A   21.00 on the 10th: feed ATM; aggregator PURCHASE (coarser, no disagreement); in the export
    B   33.00 on the 11th: feed ATM; aggregator TRANSFER (a kind that excludes a cash machine)
    C   50.00 on the 12th: feed ATM; aggregator CASH; in the export (said by both)
    D   11.50 on the 13th: PENDING in the feed, which says ATM
    E   60.00 on the 14th: feed ATM and an original amount in EUR; in the export
    F   21.00 coming IN on the 15th: feed ATM (money back from a cash machine)
    G   40.00 on 2027-01-05: feed ATM
    H   88.00 on the 19th: aggregator CASH only (not in the feed); in the export

THE DEPOSITS:
    I   120.00 IN on the 20th: feed source CASH_DEPOSIT
    J   15.00 OUT on the 21st: feed source CASH_DEPOSIT (odd, money going out)
    K   70.00 IN on the 22nd: PENDING in the feed, source CASH_DEPOSIT

THE GUESSES (no source states a cash word): a payment described as a cashpoint (aggregator
PURCHASE), one described as a cash withdrawal (feed GENERAL), and a bakery payment.

KNOWN ANSWERS for the main account:
    withdrawals by statement: 8 (A to H). The feed says so for 7, the aggregator for 2 (C, H, both
    money out); by both 1 (C), by the feed alone 6, by the aggregator alone 1 (H); also sighted by
    a source that states no kind 4 (A, C, E, H); out 7, in 1 (F); pending 1 (D), booked 7;
    earliest 2026-09-10, latest 2027-01-05, 2026: 7, 2027: 1; 1 states another currency (E).
    deposits by statement: 3 (I, J, K); in 2, out 1; pending 1, booked 2; 2026-09-20 to 2026-09-22.
    cross-tabulation: of the 7 the feed says are cash machines the aggregator's category is CASH 1
    (C), PURCHASE 1 (A), TRANSFER 1 (B), not sighted by the aggregator 4 (D, E, F, G); of the 2 the
    aggregator says are cash, both are money out, and the feed's words are MASTER_CARD ATM 1 (C)
    and not sighted by the feed 1 (H).
    exclusion: 1 (B, ATM against TRANSFER).
    guesses: 2, one by each pattern (cashpoint, cash withdrawal).
    THE RULE with the cash account chosen: 5 transfers, 4 withdrawals (A, C, E, G) and 1 deposit
    (I), dated 2026-09-10 to 2027-01-05; left out: pending 2 (D, K), the wrong way round 2 (F, J),
    disagreed 1 (B), stated only by a source that does not decide this account 1 (H).
    with no cash account chosen: none, and 5 it would take.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Any, ClassVar

import pytest

from landing import record_typed_transaction
from late_settlement_corpus import ORDERS, Payment, household
from obdi.ingest.accounts import (
    BALANCE_ONLY_KIND,
    CASH_ACCOUNT_KIND,
    AccountBinding,
    AccountRecord,
    AccountRef,
    is_balance_only,
    is_cash_account,
)
from obdi.ingest.cash_withdrawals import (
    DEPOSIT,
    DISAGREED,
    LEG,
    NOT_STATED,
    PENDING,
    WITHDRAWAL,
    WRONG_WAY,
    choose_cash_account,
    description_patterns,
    judge,
)
from obdi.ingest.pipeline import reconcile_batch
from obdi.ingest.providers import truelayer
from obdi.ingest.rebuild import parse_artefact_transactions
from obdi.ingest.store import Store
from obdi.pages.web_accounts import account_form, account_from_form
from obdi.read.account_names import AccountShown, AccountsShown
from obdi.verify.balance_anchors import effective_opening, record_stated_anchor
from obdi.verify.exact_rule_measure import exact_rule_report
from test_family_anchors import land_evidence
from test_space_attribution import MAIN, MAP, household_map

D = date
CASH = AccountRecord(ref=AccountRef("cash"), kind=CASH_ACCOUNT_KIND, label="Cash")


@dataclass(frozen=True)
class Stated(Payment):
    """A payment whose feed item states coded fields beyond a card payment's."""

    feed_stated: dict[str, Any] = field(default_factory=dict)

    def feed_item(self) -> dict[str, Any]:
        item = super().feed_item()
        item.update(self.feed_stated)
        return item


ATM = {"sourceSubType": "ATM"}
DEPOSITED = {"source": "CASH_DEPOSIT"}


def cash_payments() -> list[Payment]:
    return [
        Stated("f-a", "Sundries A", 2100, "2026-09-10T10:00:00.000Z", None, D(2026, 9, 10), 10,
               {"transaction_category": "PURCHASE"}, feed_stated=ATM),
        Stated("f-b", "Sundries B", 3300, "2026-09-11T10:00:00.000Z", None, None, 11,
               {"transaction_category": "TRANSFER"}, feed_stated=ATM),
        Stated("f-c", "Sundries C", 5000, "2026-09-12T10:00:00.000Z", None, D(2026, 9, 12), 12,
               {"transaction_category": "CASH"}, feed_stated=ATM),
        Stated("f-d", "Sundries D", 1150, "2026-09-13T10:00:00.000Z", None, None, None,
               feed_status="PENDING", feed_stated=ATM),
        Stated("f-e", "Sundries E", 6000, "2026-09-14T10:00:00.000Z", None, D(2026, 9, 14), None,
               feed_stated={**ATM, "sourceAmount": {"currency": "EUR", "minorUnits": 7000}}),
        Stated("f-f", "Sundries F", 2100, "2026-09-15T10:00:00.000Z", None, None, None,
               feed_stated={**ATM, "direction": "IN"}),
        Stated("f-g", "Sundries G", 4000, "2027-01-05T10:00:00.000Z", None, None, None,
               feed_stated=ATM),
        Stated("f-h", "Sundries H", 8800, "2026-09-19T10:00:00.000Z", None, D(2026, 9, 19), 19,
               {"transaction_category": "CASH"}, in_feed=False),
        Stated("f-i", "Sundries I", 12000, "2026-09-20T10:00:00.000Z", None, None, None,
               feed_stated={**DEPOSITED, "direction": "IN"}),
        Stated("f-j", "Sundries J", 1500, "2026-09-21T10:00:00.000Z", None, None, None,
               feed_stated=DEPOSITED),
        Stated("f-k", "Sundries K", 7000, "2026-09-22T10:00:00.000Z", None, None, None,
               feed_status="PENDING", feed_stated={**DEPOSITED, "direction": "IN"}),
        Stated("f-l", "Cashpoint High Street", 880, "2026-09-16T10:00:00.000Z", None,
               D(2026, 9, 16), 16, {"transaction_category": "PURCHASE"}),
        Stated("f-m", "Cash Withdrawal Co-op", 920, "2026-09-17T10:00:00.000Z", None, None, None,
               feed_stated={"spendingCategory": "GENERAL"}),
        Stated("f-n", "Bakery", 540, "2026-09-18T10:00:00.000Z", None, None, None),
    ]


def measured(tmp_path, order, *, rebuild: bool = False, records=()):
    store = household(tmp_path, order, cash_payments(), rebuild=rebuild)
    for record in records:
        store.declare_account(record)
    try:
        return exact_rule_report(store, MAP)
    finally:
        store.close()


def main_figures(report):
    assert report.cash is not None
    (found,) = [f for f in report.cash.accounts if f.account == MAIN]
    return found


def outcomes(figures):
    return {
        r.row.description.title(): (r.judgement.movement, r.judgement.outcome)
        for r in figures.readings
    }


@pytest.mark.parametrize("rebuild", [False, True], ids=["live", "rebuilt"])
@pytest.mark.parametrize("order", ORDERS, ids=["-".join(o) for o in ORDERS])
class TestTheHousehold:
    def test_Withdrawals_WhenEachSourceArrivesInAnyOrder_AreCountedByWhatASourceStates(
        self, tmp_path, order, rebuild
    ):
        figures = main_figures(measured(tmp_path, order, rebuild=rebuild))
        withdrawals = figures.withdrawals

        assert len(withdrawals) == 8
        assert sum(1 for r in withdrawals if r.row.amount_minor > 0) == 1
        assert sum(r.row.status.value == "pending" for r in withdrawals) == 1
        assert sum(r.row.entity_id in figures.no_kind_sources for r in withdrawals) == 4
        days = sorted(r.row.value_date for r in withdrawals)
        assert (days[0], days[-1]) == (D(2026, 9, 10), D(2027, 1, 5))
        assert len(figures.deposits) == 3
        assert not figures.credit_card

    def test_Rule_WhenACashAccountIsChosen_TakesExactlyTheBookedWellFormedUndisputedRows(
        self, tmp_path, order, rebuild
    ):
        report = measured(tmp_path, order, rebuild=rebuild, records=[CASH])
        figures = main_figures(report)

        assert sorted(r.row.description.title() for r in figures.made(WITHDRAWAL)) == [
            "Sundries A", "Sundries C", "Sundries E", "Sundries G",
        ]
        assert [r.row.description.title() for r in figures.made(DEPOSIT)] == ["Sundries I"]
        assert len(report.cash.would_make) == 5

    def test_Rule_WhenRowsAreLeftOut_EachIsLeftOutForItsOwnReason(self, tmp_path, order, rebuild):
        held = outcomes(main_figures(measured(tmp_path, order, rebuild=rebuild)))

        assert held["Sundries B"] == (WITHDRAWAL, DISAGREED)
        assert held["Sundries D"] == (WITHDRAWAL, PENDING)
        assert held["Sundries F"] == (WITHDRAWAL, WRONG_WAY)
        assert held["Sundries H"] == ("", NOT_STATED)
        assert held["Sundries J"] == (DEPOSIT, WRONG_WAY)
        assert held["Sundries K"] == (DEPOSIT, PENDING)
        assert held["Sundries A"] == (WITHDRAWAL, LEG)
        assert "Cashpoint High Street" not in held
        assert "Bakery" not in held

    def test_Descriptions_WhenNoSourceStatesACashWord_AreCountedByTheirPatternAndNothingElse(
        self, tmp_path, order, rebuild
    ):
        figures = main_figures(measured(tmp_path, order, rebuild=rebuild))

        assert dict(figures.guessed) == {"cashpoint": 1, "cash withdrawal": 1}

    def test_Text_WhenACashAccountIsChosen_StatesEachFigureAndShowsNoValueOrPayee(
        self, tmp_path, order, rebuild
    ):
        text = measured(tmp_path, order, rebuild=rebuild, records=[CASH]).describe()

        for said in (
            "cash is the one open account declared as the place cash goes, so the rule would "
            "use it.",
            "8 stored transactions that are not history are cash withdrawals by what a source "
            "states.",
            "The bank's feed says so for 7 (sourceSubType ATM).",
            "The aggregator says so for 2 (transaction_category CASH, money out).",
            "Of those, 1 is said by both, 6 by the feed alone, and 1 by the aggregator alone.",
            "Also reported by a source that states no kind of payment: 4.",
            "Out of the account: 7. Into the account: 1 (money back from a cash machine, which "
            "the rule leaves alone).",
            "Pending: 1. Booked: 7.",
            "Earliest 2026-09-10, latest 2027-01-05. By year: 2026: 7, 2027: 1.",
            "The feed states an original amount in another currency for 1; the leg would be the "
            "account's own debit, in pounds.",
            "3 stored transactions that are not history are cash deposits by what a source "
            "states (source CASH_DEPOSIT).",
            "Into the account: 2. Out of the account: 1 (odd for a deposit, which the rule "
            "leaves alone).",
            "Earliest 2026-09-20, latest 2026-09-22. By year: 2026: 3.",
            "Of the 7 stored transactions the feed says are a cash machine, the aggregator's "
            "category is: CASH 1, PURCHASE 1, TRANSFER 1, not reported by the aggregator 4.",
            "Of the 2 stored transactions the aggregator says are cash, 2 are money out and 0 "
            "money in, and the feed's source and sourceSubType are: MASTER_CARD ATM 1, not "
            "reported by the feed 1.",
            "Two sources state kinds that exclude each other for 1 (sourceSubType ATM against "
            "transaction_category TRANSFER 1). A coarser statement, such as a purchase, is "
            "not one.",
            "The rule would make 5 transfers with the cash account: 4 withdrawals and 1 deposit, "
            "dated 2026-09-10 to 2027-01-05. Left out: pending 2, the wrong way round 2, "
            "disagreed 1, stated only by a source that does not decide this account 1.",
            "Where no source states it, 2 stored transactions have a description that looks like "
            "a cash machine (cash withdrawal 1, cashpoint 1). The description is a guess, and "
            "the rule does not act on it.",
        ):
            assert said in text, said
        for hidden in ("Sundries", "Cashpoint High", "Bakery", "21.00", "2100", "f-a", "tl-f-"):
            assert hidden not in text

    def test_Text_WhenNoCashAccountIsChosen_SaysTheRuleWouldMakeNoneAndHowManyItWouldTake(
        self, tmp_path, order, rebuild
    ):
        text = measured(tmp_path, order, rebuild=rebuild).describe()

        assert "The rule would make none, because no cash account is chosen." in text
        assert "It would take 5 stored transactions if one were chosen." in text


class TestWhatTheWordsMake:
    """`judge` on its own, one row at a time, with the precedence in `cash_withdrawals`."""

    FEED_ATM: ClassVar[dict[str, list[tuple[str, str]]]] = {
        "starling": [("sourceSubType", "ATM")]
    }

    def verdict(self, words, *, has_feed=True, amount=-2000, pending=False):
        return judge(words, has_feed=has_feed, amount_minor=amount, pending=pending)

    def test_Row_WhenTheFeedSaysACashMachineAndTheAggregatorOnlyCallsItAPurchase_IsALeg(self):
        words = {**self.FEED_ATM, "truelayer": [("transaction_category", "PURCHASE")]}

        assert self.verdict(words).outcome == LEG

    def test_Row_WhenTheFeedSaysACashMachineAndTheAggregatorCallsItATransfer_IsDisputed(self):
        words = {**self.FEED_ATM, "truelayer": [("transaction_category", "TRANSFER")]}

        found = self.verdict(words)

        assert found.outcome == DISAGREED
        assert found.disagreement == "sourceSubType ATM against transaction_category TRANSFER"

    def test_Row_WhenOnlyTheAggregatorSaysCashOnAnAccountWithTheFeed_IsNotDecidedByIt(self):
        words = {"truelayer": [("transaction_category", "CASH")]}

        assert self.verdict(words).outcome == NOT_STATED

    def test_Row_WhenOnlyTheAggregatorSaysCashOnAnAccountWithNoFeed_IsDecidedByIt(self):
        words = {"truelayer": [("transaction_category", "CASH")]}

        assert self.verdict(words, has_feed=False).movement == WITHDRAWAL
        assert self.verdict(words, has_feed=False).outcome == LEG
        paid_in = self.verdict(words, has_feed=False, amount=5000)
        assert (paid_in.movement, paid_in.outcome) == (DEPOSIT, LEG)

    def test_Row_WhenTheAggregatorCallsItATransferOnAnAccountWithNoFeed_IsNotDisputed(self):
        words = {
            "truelayer": [("transaction_category", "CASH"), ("transaction_category", "TRANSFER")]
        }

        assert self.verdict(words, has_feed=False).outcome == LEG

    def test_Row_WhenTheWordIsOnePlausibleButNeverSeen_IsNotStated(self):
        for field_name, word in (
            ("source", "CASH_WITHDRAWAL"),
            ("spendingCategory", "CASH"),
            ("transaction_category", "ATM"),
        ):
            source = "truelayer" if field_name == "transaction_category" else "starling"
            assert self.verdict({source: [(field_name, word)]}).outcome == NOT_STATED

    def test_Row_WhenMoneyMovesTheWrongWayOrIsPending_IsLeftOutForThatReason(self):
        assert self.verdict(self.FEED_ATM, amount=2000).outcome == WRONG_WAY
        assert self.verdict(self.FEED_ATM, pending=True).outcome == PENDING


class TestTheCashAccount:
    """Exactly one open account declared with the cash kind is the cash account."""

    def choose(self, *records: AccountRecord):
        return choose_cash_account(records)

    def declared(self, ref: str, kind: str, closed: date | None = None) -> AccountRecord:
        return AccountRecord(ref=AccountRef(ref), kind=kind, label=ref.title(), closed=closed)

    def test_Choice_WhenExactlyOneOpenAccountHasTheCashKind_IsThatAccount(self):
        chosen = self.choose(
            self.declared("cash", CASH_ACCOUNT_KIND),
            self.declared("mortgage", BALANCE_ONLY_KIND),
        )

        assert (chosen.ref, chosen.designated, chosen.could_be_declared) == ("cash", 1, 1)

    def test_Choice_WhenNoAccountHasTheCashKind_IsNoneAndCountsTheBalanceOnlyOnesThatCould(self):
        chosen = self.choose(self.declared("cash", BALANCE_ONLY_KIND))

        assert (chosen.ref, chosen.designated, chosen.could_be_declared) == (None, 0, 1)

    def test_Choice_WhenTwoOpenAccountsHaveTheCashKind_IsNone(self):
        chosen = self.choose(
            self.declared("cash", CASH_ACCOUNT_KIND), self.declared("purse", CASH_ACCOUNT_KIND)
        )

        assert (chosen.ref, chosen.designated) == (None, 2)

    def test_Choice_WhenTheOnlyCashAccountIsClosed_IsThatAccountWithTheDayItClosed(self):
        chosen = self.choose(self.declared("cash", CASH_ACCOUNT_KIND, closed=D(2026, 8, 1)))

        assert (chosen.ref, chosen.designated, chosen.until) == ("cash", 0, D(2026, 8, 1))

    def test_Choice_WhenAClosedCashAccountHasAnOpenOne_IsTheOpenOne(self):
        chosen = self.choose(
            self.declared("old-cash", CASH_ACCOUNT_KIND, closed=D(2026, 8, 1)),
            self.declared("cash", CASH_ACCOUNT_KIND),
        )

        assert (chosen.ref, chosen.until) == ("cash", None)

    def test_Choice_WhenSeveralAreClosedAndNoneIsOpen_IsNone(self):
        chosen = self.choose(
            self.declared("old-cash", CASH_ACCOUNT_KIND, closed=D(2026, 8, 1)),
            self.declared("older-cash", CASH_ACCOUNT_KIND, closed=D(2025, 8, 1)),
        )

        assert chosen.ref is None

    def test_Choice_WhenOnlyTheLabelOrReferenceSaysCash_IsNone(self):
        chosen = self.choose(self.declared("cash", "current-account"))

        assert chosen.ref is None

    def test_Kind_WhenCashBalanceOnly_IsBalanceOnlyToo(self):
        assert is_balance_only(CASH_ACCOUNT_KIND) and is_cash_account(CASH_ACCOUNT_KIND)
        assert is_balance_only(BALANCE_ONLY_KIND) and not is_cash_account(BALANCE_ONLY_KIND)
        assert not is_balance_only("cash") and not is_cash_account("cash")

    def test_Text_WhenNoAccountIsDeclaredAsTheCashAccount_SaysTheRuleWouldDoNothing(
        self, tmp_path
    ):
        text = measured(
            tmp_path,
            ORDERS[0],
            records=[AccountRecord(ref=AccountRef("cash"), kind=BALANCE_ONLY_KIND, label="Cash")],
        ).describe()

        assert (
            "No open account is declared as the place cash goes, so the rule would do nothing. "
            "1 open balance-only account could be declared as it"
        ) in text

    def test_Text_WhenTwoAccountsAreDeclaredAsTheCashAccount_SaysTheRuleChoosesNeither(
        self, tmp_path
    ):
        text = measured(
            tmp_path,
            ORDERS[0],
            records=[
                AccountRecord(ref=AccountRef("cash"), kind=CASH_ACCOUNT_KIND),
                AccountRecord(ref=AccountRef("purse"), kind=CASH_ACCOUNT_KIND),
            ],
        ).describe()

        assert "2 open accounts are declared as the place cash goes" in text
        assert "The rule would make none" in text

    def test_Text_WhenTheCashAccountIsNamedByThePage_ReadsOnceAsItsLabel(self, tmp_path):
        text = measured(tmp_path, ORDERS[0], records=[CASH]).describe()

        shown = AccountsShown([AccountShown.named("cash", "Cash")]).in_text(text)

        assert shown.count("Cash (cash)") == 1
        assert "Cash (cash) is the one open account declared as the place cash goes" in shown
        assert "with the Cash (cash) account" not in shown


class TestWhatAStatementIs:
    def test_Statement_WhenOnlyTheDescriptionLooksLikeACashMachine_IsNone(self):
        assert description_patterns("ATM HIGH STREET") == ["atm"]
        assert (
            judge(
                {"starling": [("source", "MASTER_CARD")]},
                has_feed=True,
                amount_minor=-100,
                pending=False,
            ).outcome
            == NOT_STATED
        )


class TestACreditCard:
    def test_Withdrawal_WhenOnACreditCardTheAggregatorFeeds_IsCountedAsACashAdvance(
        self, tmp_path
    ):
        card = "halifax-card"
        card_map = household_map(
            extra_bindings=(AccountBinding(card, "truelayer", "tl-card"),)
        )
        with Store(tmp_path / "card.sqlite3") as store:
            land_evidence(store)
            record = {
                "transaction_id": "volatile-1",
                "normalised_provider_transaction_id": "tl-card-1",
                "timestamp": "2026-09-20T10:00:00Z",
                "description": "SUNDRIES H",
                "amount": "60.00",
                "currency": "GBP",
                "transaction_type": "DEBIT",
                "transaction_category": "CASH",
            }
            artefact = truelayer.artefact_for(
                json.dumps({"results": [record]}).encode(), account_id="tl-card", kind="card-booked"
            )
            store.land_artefact(artefact)
            reconcile_batch(
                store,
                parse_artefact_transactions(
                    artefact.source, artefact.payload, card, artefact.digest
                ),
                digest=artefact.digest,
            )
            store.declare_account(CASH)
            report = exact_rule_report(store, card_map)

        assert report.cash is not None
        (found,) = [f for f in report.cash.accounts if f.account == card]
        assert found.credit_card
        assert [r.judgement.outcome for r in found.readings] == [LEG]
        assert len(found.made(WITHDRAWAL)) == 1
        assert "its cash withdrawals are cash advances" in report.describe()

    def test_Withdrawal_WhenOnACurrentAccount_IsNotACashAdvance(self, tmp_path):
        figures = main_figures(measured(tmp_path, ORDERS[0]))

        assert not figures.credit_card
        assert "cash advances" not in "\n".join(figures.sentences(chosen=True))


class TestTheEditPage:
    """The owner says where cash goes on the account's own page, and the word survives a save."""

    def test_Form_WhenTheAccountIsDeclaredAsTheCashAccount_ShowsTheKindChosenAndSaidInWords(self):
        record = AccountRecord(ref=AccountRef("cash"), kind=CASH_ACCOUNT_KIND, label="Cash")

        form = account_form(record, [record])

        assert f'<option value="{CASH_ACCOUNT_KIND}" selected>' in form
        assert "named as the place cash taken from a cash machine goes" in form

    def test_Form_WhenTheAccountIsPlainBalanceOnly_OffersTheCashKindWithoutChoosingIt(self):
        record = AccountRecord(ref=AccountRef("cash"), kind=BALANCE_ONLY_KIND, label="Cash")

        form = account_form(record, [record])

        assert f'<option value="{CASH_ACCOUNT_KIND}">' in form
        assert f'<option value="{BALANCE_ONLY_KIND}" selected>' in form

    def test_Form_WhenSavedWithTheCashKind_KeepsItAsTheDeclaredKind(self, tmp_path):
        saved = account_from_form({"ref": "cash", "label": "Cash", "kind": CASH_ACCOUNT_KIND})
        with Store(tmp_path / "kind.sqlite3") as store:
            store.declare_account(saved)
            kept = store.declared_account(AccountRef("cash"))

        assert kept is not None and is_cash_account(kept.kind)


class TestAnEmptyStore:
    def test_Text_WhenNoTransactionIsACashMovement_SaysSoInWords(self, tmp_path):
        with Store(tmp_path / "empty.sqlite3") as store:
            text = exact_rule_report(store, MAP).describe()

        assert (
            "No stored transaction that is not history is a cash movement by what a source "
            "states, and none has a description that looks like a cash machine."
        ) in text
        assert "No stored transaction was reported by a source that states a coded field." in text


class TestTheWordsASourceStates:
    def test_Text_ListsEveryCodedWordEachSourceStatesSoTheBanksWordCanBeRead(self, tmp_path):
        text = measured(tmp_path, ORDERS[0]).describe()

        assert "The bank's feed, sourceSubType: ATM 7" in text
        assert "The aggregator, transaction_category: CASH 2, PURCHASE 2, TRANSFER 1" in text


class TestACashAccountIsBalanceOnly:
    """What the cash account does with a withdrawal that lands in it, worked out by hand.

    Cash is stated 20.00 on 2026-09-01, a withdrawal of 50.00 arrives on the 10th, and cash is
    stated 15.00 on the 20th. The change between the two stated balances is 15.00 less 20.00
    less the 50.00 that came in: -55.00, which is what was spent from the purse.
    """

    TODAY = D(2026, 10, 5)

    def test_UnitemisedChange_WhenAWithdrawalLandedBetweenTwoStatedBalances_IsWhatWasSpent(
        self, tmp_path
    ):
        with Store(tmp_path / "cash.sqlite3") as store:
            store.declare_account(CASH)
            record_stated_anchor(store, "cash", "2026-09-01", "20.00", today=self.TODAY)
            record_stated_anchor(store, "cash", "2026-09-20", "15.00", today=self.TODAY)
            before = [t.amount_minor for t in effective_opening(store, "cash").unitemised]
            record_typed_transaction(
                store, "cash", "2026-09-10", "in", "50.00", "Cash from a machine",
                today=self.TODAY, now=datetime(2026, 10, 5, 9, 0, tzinfo=UTC),
                entry_id="c0c0c0c0c0c0c0c0",
            )
            after = [
                (t.value_date.isoformat(), t.amount_minor)
                for t in effective_opening(store, "cash").unitemised
            ]

        assert before == [-500]
        assert after == [("2026-09-20", -5500)]

    def test_Reading_WhenTheKindIsCashBalanceOnly_IsTheSameAsBalanceOnly(self, tmp_path):
        readings = []
        for kind in (BALANCE_ONLY_KIND, CASH_ACCOUNT_KIND):
            with Store(tmp_path / f"{kind}.sqlite3") as store:
                store.declare_account(AccountRecord(ref=AccountRef("cash"), kind=kind))
                record_stated_anchor(store, "cash", "2026-09-01", "20.00", today=self.TODAY)
                record_stated_anchor(store, "cash", "2026-09-20", "15.00", today=self.TODAY)
                opening = effective_opening(store, "cash")
                readings.append((opening.balance_only, opening.opening_minor,
                                 [t.amount_minor for t in opening.unitemised]))

        assert readings[0] == readings[1] == (True, 2000, [-500])
