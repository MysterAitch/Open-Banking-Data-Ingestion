"""How many stored transactions are cash withdrawals, by what a source states, in counts and dates.

A MEASUREMENT BEFORE A RULE. Nothing here makes, moves, or removes a row: it reads how many
stored transactions a rule "a cash withdrawal is a transfer to the cash account" would take,
and how many it would leave, so what the rule is trusted with rests on the evidence
(`cash_withdrawals` says why the words are candidates and what is required of a statement).

READ FROM THE ARTEFACTS, NOT THE ROWS. What a source stated of an item is read from the
landed payloads, parsed by the providers, and joined to a stored transaction by the id on its
sighting. A row keeps the raw of whichever sighting created it, so a count taken from rows would
change with the order the sources arrived in. The export and the statements state no coded
kind, so they are counted as sources that state none and never as disagreeing.

A word a bank states is data and is printed; a size, a payee, and a description never are, and
the guesses a description makes are counted by the pattern's name only.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Mapping
from dataclasses import dataclass, field

from .accounts import AccountRecord
from .cash_withdrawals import (
    CashAccountChoice,
    choose_cash_account,
    coded_words,
    description_patterns,
    is_statement_source,
    kind_word,
    says_cash_machine,
    states_foreign_currency,
)
from .models import Transaction, TransactionStatus
from .payment_links import AGGREGATORS, FIRST_PARTY_FEEDS
from .plural import agree, plural
from .store import FOLDED_SIGHTING_PREFIX, Store

_CARD_ARTEFACT = "truelayer-card-booked"
_CREDIT_CARD_KIND = "credit-card"

#: How a source is named where it is said to state something.
_SOURCE_NAMES = {"feed": "The bank's feed", "aggregator": "The aggregator"}


def _stated_by(source: str) -> str:
    return "feed" if source in FIRST_PARTY_FEEDS else "aggregator"


def _transactions(count: int) -> str:
    return plural(count, "stored transaction")


def _word(pair: tuple[str, str]) -> str:
    return f"{pair[0]} {pair[1]}"


def _counted(words: Counter[str]) -> str:
    return ", ".join(f"{name} {count}" for name, count in sorted(words.items()))


@dataclass
class _Row:
    transaction: Transaction
    #: Which kind of source says a cash machine -> the words it states for it.
    says: dict[str, list[tuple[str, str]]] = field(default_factory=dict)
    #: Which kind of source states another kind of payment -> that (field, value).
    other_kind: dict[str, tuple[str, str]] = field(default_factory=dict)
    #: A source that states no coded kind has sighted it (an export, a statement).
    sighted_by_a_source_stating_no_kind: bool = False
    foreign: bool = False
    on_a_card_artefact: bool = False
    guesses: list[str] = field(default_factory=list)

    @property
    def stated(self) -> bool:
        return bool(self.says)

    @property
    def disagreements(self) -> list[str]:
        pairs: list[str] = []
        for said_by, words in self.says.items():
            for other_by, other in self.other_kind.items():
                if other_by != said_by:
                    pairs.append(f"{_word(words[0])} against {_word(other)}")
        return sorted(pairs)


@dataclass
class AccountCashFigures:
    account: str
    credit_card: bool = False
    stated: list[_Row] = field(default_factory=list)
    #: Rows no source states that a description's guess would have taken, by the guess's name.
    guessed: Counter[str] = field(default_factory=Counter)

    def sentences(self) -> list[str]:
        rows = self.stated
        lines: list[str] = []
        if rows:
            lines += self._stated_sentences(rows)
        if self.guessed:
            lines.append(
                f"Where no source states it, {_transactions(sum(self.guessed.values()))} "
                f"{agree(sum(self.guessed.values()), 'has')} a description that looks like a "
                f"cash machine ({_counted(self.guessed)}). The description is a guess, and the "
                "rule does not act on it."
            )
        return lines

    def _stated_sentences(self, rows: list[_Row]) -> list[str]:
        by_feed = Counter(
            _word(w) for r in rows for w in r.says.get("feed", [])
        )
        by_aggregator = Counter(
            _word(w) for r in rows for w in r.says.get("aggregator", [])
        )
        feed_rows = sum(1 for r in rows if "feed" in r.says)
        aggregator_rows = sum(1 for r in rows if "aggregator" in r.says)
        both = sum(1 for r in rows if len(r.says) == 2)
        noun = "a cash withdrawal" if len(rows) == 1 else "cash withdrawals"
        lines = [
            f"{_transactions(len(rows))} that {agree(len(rows), 'is')} not history "
            f"{agree(len(rows), 'is')} {noun} by what a source states.",
            f"The bank's feed says so for {feed_rows}"
            + (f" ({_counted(by_feed)})." if by_feed else "."),
            f"The aggregator says so for {aggregator_rows}"
            + (f" ({_counted(by_aggregator)})." if by_aggregator else "."),
            f"Of those, {both} {agree(both, 'is')} said by both, "
            f"{feed_rows - both} by the feed alone, and {aggregator_rows - both} by the "
            "aggregator alone.",
            "Also sighted by a source that states no kind of payment: "
            f"{sum(1 for r in rows if r.sighted_by_a_source_stating_no_kind)}.",
        ]
        pairs: Counter[str] = Counter(p for r in rows for p in r.disagreements)
        disagreeing = sum(1 for r in rows if r.disagreements)
        lines.append(
            f"Sources that disagree about the kind: {disagreeing}"
            + (f" ({_counted(pairs)})." if pairs else ".")
        )
        money_in = sum(1 for r in rows if r.transaction.amount_minor > 0)
        lines.append(
            f"Out of the account: {len(rows) - money_in}. Into the account: {money_in}"
            + (
                " (money back from a cash machine, which the rule has to decide what to do with)."
                if money_in
                else "."
            )
        )
        pending = sum(1 for r in rows if r.transaction.status is TransactionStatus.PENDING)
        lines.append(f"Pending: {pending}. Booked: {len(rows) - pending}.")
        dates = sorted(r.transaction.value_date for r in rows)
        years = Counter(d.year for d in dates)
        lines.append(
            f"Earliest {dates[0].isoformat()}, latest {dates[-1].isoformat()}. By year: "
            + ", ".join(f"{year}: {count}" for year, count in sorted(years.items()))
            + "."
        )
        foreign = sum(1 for r in rows if r.foreign)
        lines.append(
            f"The feed states an original amount in another currency for {foreign}; the leg "
            "would be the account's own debit, in pounds."
            if foreign
            else "None states an original amount in another currency."
        )
        if self.credit_card:
            lines.append(
                "This account is a credit card, so its cash withdrawals are cash advances: "
                "still a transfer to the cash account, and counted apart for that reason."
            )
        return lines


@dataclass
class CashWithdrawalReport:
    choice: CashAccountChoice
    accounts: list[AccountCashFigures] = field(default_factory=list)
    #: Per account, the words each source states in each coded field, with counts.
    vocabulary: dict[str, dict[str, Counter[str]]] = field(default_factory=dict)

    def sentences(self) -> list[str]:
        lines: list[str] = []
        choice = self.choice
        if choice.ref is not None:
            lines.append(
                f"{choice.ref} is the one open account declared as the place cash goes, "
                "so the rule would use it."
            )
        elif choice.designated == 0:
            lines.append(
                "No open account is declared as the place cash goes, so the rule would do nothing. "
                f"{plural(choice.could_be_declared, 'open balance-only account')} "
                "could be declared as it by choosing the kind cash-balance-only on its "
                "account page."
            )
        else:
            lines.append(
                f"{choice.designated} open accounts are declared as the place cash goes, so the "
                "rule would do nothing: it does not choose between them."
            )
        if not self.accounts:
            lines.append(
                "No stored transaction that is not history is a cash withdrawal by what a source "
                "states, and none has a description that looks like a cash machine."
            )
        for figures in self.accounts:
            lines.append(f"{figures.account}:")
            lines.extend(f"  {sentence}" for sentence in figures.sentences())
        lines.append(
            "The words each source states in its coded fields, counted over the stored "
            "transactions that are not history it sighted, so the word a bank uses for a cash "
            "machine is read from here:"
        )
        if not self.vocabulary:
            lines.append(
                "  No stored transaction was sighted by a source that states a coded field."
            )
        for account, by_field in sorted(self.vocabulary.items()):
            lines.append(f"{account}:")
            for name, words in sorted(by_field.items()):
                lines.append(f"  {name}: {_counted(words)}")
        return lines


def _state_of(raw: Mapping[str, object] | None) -> Mapping[str, object]:
    return raw if isinstance(raw, Mapping) else {}


def cash_withdrawal_report(
    store: Store,
    records: list[AccountRecord],
    *,
    feed: Mapping[str, Mapping[str, Transaction]],
    aggregator: Mapping[str, Mapping[str, Transaction]],
) -> CashWithdrawalReport:
    """The cash withdrawals the stored transactions hold by what the landed artefacts state.

    `feed` and `aggregator` are the landed items by account and then by their own id, as
    `exact_rule_measure` reads them once for all its sections.
    """
    feed_items = {uid: item for items in feed.values() for uid, item in items.items()}
    aggregator_items = {sid: item for items in aggregator.values() for sid, item in items.items()}
    sightings: dict[str, list[tuple[str, str]]] = defaultdict(list)
    kindless: set[str] = set()
    for entity, source, source_id in store.connection.execute(
        "SELECT entity_id, source, COALESCE(source_id, '') FROM transaction_sources"
    ):
        if is_statement_source(str(source)):
            if str(source_id) and not str(source_id).startswith(FOLDED_SIGHTING_PREFIX):
                sightings[str(entity)].append((str(source), str(source_id)))
        else:
            kindless.add(str(entity))
    on_card = {
        str(row[0])
        for row in store.connection.execute(
            "SELECT DISTINCT s.entity_id FROM transaction_sources s "
            "JOIN raw_artefacts a ON a.digest = s.artefact_digest WHERE a.source = ?",
            (_CARD_ARTEFACT,),
        )
    }
    kinds = {str(r.ref): r.kind.strip().casefold() for r in records}
    accounts = [
        str(row[0])
        for row in store.connection.execute(
            "SELECT DISTINCT account_id FROM transactions ORDER BY account_id"
        )
    ]
    report = CashWithdrawalReport(choose_cash_account(records))
    for account in accounts:
        figures = AccountCashFigures(account)
        words: dict[str, Counter[str]] = defaultdict(Counter)
        for transaction in store.transactions_for_account(account):
            if transaction.status.is_history:
                continue
            row = _read_row(
                transaction, sightings.get(transaction.entity_id, []), feed_items,
                aggregator_items, words,
            )
            row.sighted_by_a_source_stating_no_kind = transaction.entity_id in kindless
            row.on_a_card_artefact = transaction.entity_id in on_card
            if row.stated:
                figures.stated.append(row)
            else:
                for name in description_patterns(transaction.description):
                    figures.guessed[name] += 1
        figures.credit_card = kinds.get(account) == _CREDIT_CARD_KIND or any(
            r.on_a_card_artefact for r in figures.stated
        )
        if words:
            report.vocabulary[account] = dict(words)
        if figures.stated or figures.guessed:
            figures.stated.sort(key=lambda r: (r.transaction.value_date, r.transaction.entity_id))
            report.accounts.append(figures)
    return report


def _read_row(
    transaction: Transaction,
    sighted: list[tuple[str, str]],
    feed_items: Mapping[str, Transaction],
    aggregator_items: Mapping[str, Transaction],
    words: dict[str, Counter[str]],
) -> _Row:
    row = _Row(transaction)
    for source, source_id in sighted:
        item = (
            feed_items.get(source_id) if source in FIRST_PARTY_FEEDS
            else aggregator_items.get(source_id) if source in AGGREGATORS
            else None
        )
        if item is None:
            continue
        raw = _state_of(item.raw)
        for name, value in coded_words(source, raw):
            words[f"{_SOURCE_NAMES[_stated_by(source)]}, {name}"][value] += 1
        said = says_cash_machine(source, raw)
        if said:
            row.says[_stated_by(source)] = said
        elif (kind := kind_word(source, raw)) is not None:
            row.other_kind[_stated_by(source)] = kind
        if source in FIRST_PARTY_FEEDS and states_foreign_currency(raw):
            row.foreign = True
    return row


__all__ = ["AccountCashFigures", "CashWithdrawalReport", "cash_withdrawal_report"]
