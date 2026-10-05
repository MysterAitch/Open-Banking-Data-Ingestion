"""How many stored transactions are cash withdrawals by what a source states.

The household is the late-settlement household (a main account, its Space, an export, and an
aggregator) with seven withdrawals each stated a different way, two payments whose descriptions
only LOOK like a cash machine, and a plain payment. Every name and amount is invented. Each
known answer below was decided before the first run, and each source arrives in each of six
orders, live and then rebuilt from the landed artefacts: the answer may not depend on either.

THE WITHDRAWALS (the main account, September 2026 unless said):

    A   21.00 on the 10th, the feed says sourceSubType ATM and the
        aggregator says transaction_category ATM; the export lists it
    B   33.00 on the 11th, the feed says spendingCategory CASH and the aggregator says
        transaction_category PURCHASE; no export row
    C   50.00 on the 12th, only the aggregator and the export sight it; the aggregator says ATM
    D   11.50 on the 13th, PENDING in the feed, which says source CASH_WITHDRAWAL; nothing else
    E   60.00 on the 14th, the feed says sourceSubType ATM and an original amount in EUR; the
        export lists it
    F   21.00 coming IN on the 15th, the feed says sourceSubType ATM (a returned withdrawal)
    G   40.00 on 2027-01-05, the feed says sourceSubType ATM; nothing else

THE GUESSES (no source states a kind that is a cash machine):
    a payment described as a cashpoint, whose aggregator says PURCHASE   -> pattern cashpoint
    a payment described as a cash withdrawal, whose feed says GENERAL    -> pattern cash withdrawal
    a bakery payment                                                     -> none

KNOWN ANSWERS for the main account:
    7 withdrawals by statement: the feed says so for 6 (sourceSubType ATM 4, spendingCategory
    CASH 1, source CASH_WITHDRAWAL 1), the aggregator for 2 (transaction_category ATM 2), by
    both 1 (A), by the feed alone 5, by the aggregator alone 1 (C); sighted also by a source
    that states no kind 3 (A, C, E, the export); one disagreement (B: spendingCategory CASH against
    transaction_category PURCHASE); 6 out and 1 in; 1 pending and 6 booked; earliest 2026-09-10,
    latest 2027-01-05, 2026: 6 and 2027: 1; 1 states an original amount in another currency;
    not a credit card; and 2 guessed rows, one by each pattern.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Any

import pytest

from late_settlement_corpus import ORDERS, Payment, household
from obdi.accounts import (
    BALANCE_ONLY_KIND,
    CASH_ACCOUNT_KIND,
    AccountBinding,
    AccountRecord,
    AccountRef,
    is_balance_only,
    is_cash_account,
)
from obdi.balance_anchors import effective_opening, record_stated_anchor
from obdi.cash_withdrawals import (
    choose_cash_account,
    description_patterns,
    says_cash_machine,
)
from obdi.exact_rule_measure import exact_rule_report
from obdi.ingest import reconcile_batch
from obdi.providers import truelayer
from obdi.rebuild import parse_artefact_transactions
from obdi.store import Store
from obdi.typed_transactions import record_typed_transaction
from obdi.web_accounts import account_form, account_from_form
from test_family_anchors import land_evidence
from test_space_attribution import MAIN, MAP, household_map

D = date


@dataclass(frozen=True)
class Stated(Payment):
    """A payment whose feed item states coded fields beyond a card payment's."""

    feed_stated: dict[str, Any] = field(default_factory=dict)

    def feed_item(self) -> dict[str, Any]:
        item = super().feed_item()
        item.update(self.feed_stated)
        return item


def withdrawals() -> list[Payment]:
    return [
        Stated("f-a", "Sundries A", 2100, "2026-09-10T10:00:00.000Z", None, D(2026, 9, 10), 10,
               {"transaction_category": "ATM"}, feed_stated={"sourceSubType": "ATM"}),
        Stated("f-b", "Sundries B", 3300, "2026-09-11T10:00:00.000Z", None, None, 11,
               {"transaction_category": "PURCHASE"}, feed_stated={"spendingCategory": "CASH"}),
        Stated("f-c", "Sundries C", 5000, "2026-09-12T10:00:00.000Z", None, D(2026, 9, 12), 12,
               {"transaction_category": "ATM"}, in_feed=False),
        Stated("f-d", "Sundries D", 1150, "2026-09-13T10:00:00.000Z", None, None, None,
               feed_status="PENDING", feed_stated={"source": "CASH_WITHDRAWAL"}),
        Stated("f-e", "Sundries E", 6000, "2026-09-14T10:00:00.000Z", None, D(2026, 9, 14), None,
               feed_stated={"sourceSubType": "ATM",
                            "sourceAmount": {"currency": "EUR", "minorUnits": 7000}}),
        Stated("f-f", "Sundries F", 2100, "2026-09-15T10:00:00.000Z", None, None, None,
               feed_stated={"sourceSubType": "ATM", "direction": "IN"}),
        Stated("f-g", "Sundries G", 4000, "2027-01-05T10:00:00.000Z", None, None, None,
               feed_stated={"sourceSubType": "ATM"}),
    ]


def guesses() -> list[Payment]:
    return [
        Stated("f-h", "Cashpoint High Street", 880, "2026-09-16T10:00:00.000Z", None,
               D(2026, 9, 16), 16, {"transaction_category": "PURCHASE"}),
        Stated("f-i", "Cash Withdrawal Co-op", 920, "2026-09-17T10:00:00.000Z", None, None, None,
               feed_stated={"spendingCategory": "GENERAL"}),
        Stated("f-j", "Bakery", 540, "2026-09-18T10:00:00.000Z", None, None, None),
    ]


def measured(tmp_path, order, *, rebuild: bool = False, records=()):
    store = household(tmp_path, order, [*withdrawals(), *guesses()], rebuild=rebuild)
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


@pytest.mark.parametrize("rebuild", [False, True], ids=["live", "rebuilt"])
@pytest.mark.parametrize("order", ORDERS, ids=["-".join(o) for o in ORDERS])
class TestTheHousehold:
    def test_Withdrawals_WhenEachSourceArrivesInAnyOrder_AreCountedByWhatASourceStates(
        self, tmp_path, order, rebuild
    ):
        figures = main_figures(measured(tmp_path, order, rebuild=rebuild))

        assert len(figures.stated) == 7
        assert sum("feed" in r.says for r in figures.stated) == 6
        assert sum("aggregator" in r.says for r in figures.stated) == 2
        assert sum(len(r.says) == 2 for r in figures.stated) == 1
        assert sum(r.sighted_by_a_source_stating_no_kind for r in figures.stated) == 3
        assert sum(bool(r.disagreements) for r in figures.stated) == 1

    def test_Withdrawals_WhenCounted_AreSplitByDirectionStatusAndDate(
        self, tmp_path, order, rebuild
    ):
        figures = main_figures(measured(tmp_path, order, rebuild=rebuild))

        assert sum(r.transaction.amount_minor > 0 for r in figures.stated) == 1
        assert sum(r.transaction.amount_minor < 0 for r in figures.stated) == 6
        assert sum(r.transaction.status.value == "pending" for r in figures.stated) == 1
        dates = sorted(r.transaction.value_date for r in figures.stated)
        assert (dates[0], dates[-1]) == (D(2026, 9, 10), D(2027, 1, 5))
        assert sum(r.foreign for r in figures.stated) == 1
        assert not figures.credit_card

    def test_Descriptions_WhenNoSourceStatesACashMachine_AreCountedByTheirPatternAndNothingElse(
        self, tmp_path, order, rebuild
    ):
        figures = main_figures(measured(tmp_path, order, rebuild=rebuild))

        assert dict(figures.guessed) == {"cashpoint": 1, "cash withdrawal": 1}

    def test_Text_WhenRenderedOverTheHousehold_StatesEachFigureAndShowsNoValueOrPayee(
        self, tmp_path, order, rebuild
    ):
        text = measured(tmp_path, order, rebuild=rebuild).describe()

        for said in (
            "7 stored transactions that are not history are cash withdrawals by what a source "
            "states.",
            "The bank's feed says so for 6 (source CASH_WITHDRAWAL 1, sourceSubType ATM 4, "
            "spendingCategory CASH 1).",
            "The aggregator says so for 2 (transaction_category ATM 2).",
            "Of those, 1 is said by both, 5 by the feed alone, and 1 by the aggregator alone.",
            "Also sighted by a source that states no kind of payment: 3.",
            "Sources that disagree about the kind: 1 (spendingCategory CASH against "
            "transaction_category PURCHASE 1).",
            "Out of the account: 6. Into the account: 1",
            "Pending: 1. Booked: 6.",
            "Earliest 2026-09-10, latest 2027-01-05. By year: 2026: 6, 2027: 1.",
            "The feed states an original amount in another currency for 1; the leg would be the "
            "account's own debit, in pounds.",
            "Where no source states it, 2 stored transactions have a description that looks like "
            "a cash machine (cash withdrawal 1, cashpoint 1). The description is a guess, and "
            "the rule does not act on it.",
        ):
            assert said in text
        for hidden in ("Sundries", "Cashpoint High", "Bakery", "21.00", "2100", "f-a", "tl-f-"):
            assert hidden not in text


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

    def test_Choice_WhenTheOnlyCashAccountIsClosed_IsNone(self):
        chosen = self.choose(self.declared("cash", CASH_ACCOUNT_KIND, closed=D(2026, 8, 1)))

        assert (chosen.ref, chosen.designated) == (None, 0)

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

    def test_Text_WhenExactlyOneAccountIsDeclaredAsTheCashAccount_NamesIt(self, tmp_path):
        text = measured(
            tmp_path,
            ORDERS[0],
            records=[AccountRecord(ref=AccountRef("cash"), kind=CASH_ACCOUNT_KIND, label="Cash")],
        ).describe()

        assert "cash is the one open account declared as the place cash goes" in text

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


class TestWhatAStatementIs:
    def test_Statement_WhenTheFeedStatesTheWord_IsRecognisedWhateverTheCase(self):
        assert says_cash_machine("starling", {"sourceSubType": "atm"}) == [("sourceSubType", "atm")]

    def test_Statement_WhenOnlyTheDescriptionLooksLikeACashMachine_IsNone(self):
        raw = {"source": "MASTER_CARD", "reference": "ATM HIGH STREET", "counterPartyName": "ATM"}

        assert says_cash_machine("starling", raw) == []
        assert description_patterns("ATM HIGH STREET") == ["atm"]

    def test_Statement_WhenTheAggregatorClassifiesAsCashAndAtm_IsRecognisedWordByWord(self):
        raw = {"transaction_classification": ["Cash & ATM", "Cash & ATM"]}

        said = says_cash_machine("truelayer", raw)

        assert said[0] == ("transaction_classification", "Cash & ATM")

    def test_Statement_WhenTheWordIsNotACandidateOrTheSourceIsAnExport_IsNone(self):
        assert says_cash_machine("starling", {"sourceSubType": "CONTACTLESS"}) == []
        assert says_cash_machine("starling-csv", {"sourceSubType": "ATM"}) == []


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
                "transaction_category": "ATM",
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
            report = exact_rule_report(store, card_map)

        assert report.cash is not None
        (found,) = [f for f in report.cash.accounts if f.account == card]
        assert found.credit_card and len(found.stated) == 1
        assert "its cash withdrawals are cash advances" in report.describe()

    def test_Withdrawal_WhenOnACurrentAccount_IsNotACashAdvance(self, tmp_path):
        figures = main_figures(measured(tmp_path, ORDERS[0]))

        assert not figures.credit_card
        assert "cash advances" not in "\n".join(figures.sentences())


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
    def test_Text_WhenNoTransactionIsACashWithdrawal_SaysSoInWords(self, tmp_path):
        with Store(tmp_path / "empty.sqlite3") as store:
            text = exact_rule_report(store, MAP).describe()

        assert (
            "No stored transaction that is not history is a cash withdrawal by what a source "
            "states, and none has a description that looks like a cash machine."
        ) in text
        assert "No stored transaction was sighted by a source that states a coded field." in text


class TestTheWordsASourceStates:
    def test_Text_ListsEveryCodedWordEachSourceStatesSoTheBanksWordCanBeRead(self, tmp_path):
        text = measured(tmp_path, ORDERS[0]).describe()

        assert "The bank's feed, sourceSubType: ATM 4" in text
        assert "The aggregator, transaction_category: ATM 2, PURCHASE 2" in text


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
            store.declare_account(
                AccountRecord(ref=AccountRef("cash"), kind=CASH_ACCOUNT_KIND, label="Cash")
            )
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
