"""How many stored transactions are cash movements, by what a source states, in counts and dates.

A MEASUREMENT BEFORE A RULE, AND THEN THE RULE'S OWN ACCOUNT OF ITSELF. It reads how many
stored transactions the rule "a cash withdrawal is a transfer to the cash account, and a cash
deposit the transfer back" takes, and how many it leaves and why. Nothing here makes, moves, or
removes a row. The rule's count comes from the reader the rule itself uses
(`cash_withdrawals.read_candidates`), so the measurement says N and the rule makes exactly N.

READ FROM THE STORED WORDS. What a source stated of an item is the coded words kept against its
sighting (`stated_words`, table `sighting_words`), so no landed payload is parsed to measure. A
row keeps the raw of whichever sighting created it, which is why the words are kept per sighting:
a count taken from rows would change with the order the sources arrived in.

THE CROSS-TABULATION is said outright, because a word's meaning is read from what the other
source says of the same payments: of the transactions the feed says are cash machines, what the
aggregator's category is; of those the aggregator says are cash, what the feed's words are.
A coarser statement (a purchase, a card transaction) is shown there and is NOT a disagreement;
a disagreement is two sources each stating a kind that excludes the other (`cash_withdrawals`).

A word a bank states is data and is printed; a size, a payee, and a description never are, and
the guesses a description makes are counted by the pattern's name only.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date

from .accounts import AccountRecord
from .cash_withdrawals import (
    AFTER_CLOSE,
    AGGREGATOR_CASH,
    DEPOSIT,
    DISAGREED,
    FEED_WORDS,
    LEG,
    PENDING,
    WITHDRAWAL,
    WRONG_WAY,
    CashAccountChoice,
    Reading,
    aggregator_excludes_cash_machine,
    by_kind,
    choose_cash_account,
    description_patterns,
    feed_excludes_cash,
    read_candidates,
)
from .core.models import Transaction, TransactionStatus
from .core.namespaces import CASH_LEG_SOURCE
from .core.plural import agree, plural
from .payment_links import AGGREGATORS, FIRST_PARTY_FEEDS
from .store import Store

_CARD_ARTEFACT = "truelayer-card-booked"
_CREDIT_CARD_KIND = "credit-card"

#: The coded fields whose words are listed, so the word a bank uses can be read from the page.
_VOCABULARY = (
    ("The bank's feed", "starling", "source"),
    ("The bank's feed", "starling", "sourceSubType"),
    ("The bank's feed", "starling", "spendingCategory"),
    ("The aggregator", "truelayer", "transaction_category"),
    ("The aggregator", "truelayer", "transaction_classification"),
)


def _transactions(count: int) -> str:
    return plural(count, "stored transaction")


def _counted(words: Counter[str]) -> str:
    return ", ".join(f"{name} {count}" for name, count in sorted(words.items()))


def _span(days: list[date]) -> str:
    years = Counter(d.year for d in days)
    return (
        f"Earliest {min(days).isoformat()}, latest {max(days).isoformat()}. By year: "
        + ", ".join(f"{year}: {count}" for year, count in sorted(years.items()))
        + "."
    )


def _decided(reading: Reading, movement: str) -> bool:
    """Whether a source says this row is this movement, whatever the precedence makes of it."""
    feed, aggregator = by_kind(reading.words)
    if FEED_WORDS[movement] in feed:
        return True
    return AGGREGATOR_CASH in aggregator and (reading.row.amount_minor < 0) == (
        movement == WITHDRAWAL
    )


@dataclass
class AccountCashFigures:
    account: str
    credit_card: bool = False
    readings: list[Reading] = field(default_factory=list)
    #: Entities some source that states no coded kind (an export, a statement) also sighted.
    no_kind_sources: set[str] = field(default_factory=set)
    on_card: set[str] = field(default_factory=set)
    #: Rows no source states that a description's guess would have taken, by the guess's name.
    guessed: Counter[str] = field(default_factory=Counter)

    @property
    def withdrawals(self) -> list[Reading]:
        return [r for r in self.readings if _decided(r, WITHDRAWAL)]

    @property
    def deposits(self) -> list[Reading]:
        return [r for r in self.readings if _decided(r, DEPOSIT)]

    def made(self, movement: str) -> list[Reading]:
        """The rows of this movement the rule makes a leg for."""
        return [
            r
            for r in self.readings
            if r.judgement.outcome == LEG and r.judgement.movement == movement
        ]

    def sentences(self, *, chosen: bool) -> list[str]:
        lines: list[str] = []
        withdrawals, deposits = self.withdrawals, self.deposits
        if withdrawals:
            lines += self._withdrawal_sentences(withdrawals)
        if deposits:
            lines += self._deposit_sentences(deposits)
        lines += self._cross_tabulation()
        lines += self._disagreement_sentences()
        lines += self._rule_sentences(chosen=chosen)
        if self.guessed:
            count = sum(self.guessed.values())
            lines.append(
                f"Where no source states it, {_transactions(count)} {agree(count, 'has')} a "
                f"description that looks like a cash machine ({_counted(self.guessed)}). The "
                "description is a guess, and the rule does not act on it."
            )
        return lines

    def _withdrawal_sentences(self, rows: list[Reading]) -> list[str]:
        feed_rows = [r for r in rows if FEED_WORDS[WITHDRAWAL] in by_kind(r.words)[0]]
        aggregator_rows = [r for r in rows if AGGREGATOR_CASH in by_kind(r.words)[1]]
        both = [r for r in feed_rows if r in aggregator_rows]
        noun = "a cash withdrawal" if len(rows) == 1 else "cash withdrawals"
        money_in = sum(1 for r in rows if r.row.amount_minor > 0)
        pending = sum(1 for r in rows if r.row.status is TransactionStatus.PENDING)
        foreign = sum(
            1
            for r in rows
            if any(
                field_name == "sourceAmount.currency" and word.upper() != "GBP"
                for source in FIRST_PARTY_FEEDS
                for field_name, word in r.words.get(source, ())
            )
        )
        lines = [
            f"{_transactions(len(rows))} that {agree(len(rows), 'is')} not history "
            f"{agree(len(rows), 'is')} {noun} by what a source states.",
            f"The bank's feed says so for {len(feed_rows)} "
            f"({FEED_WORDS[WITHDRAWAL][0]} {FEED_WORDS[WITHDRAWAL][1]}).",
            f"The aggregator says so for {len(aggregator_rows)} "
            f"({AGGREGATOR_CASH[0]} {AGGREGATOR_CASH[1]}, money out).",
            f"Of those, {len(both)} {agree(len(both), 'is')} said by both, "
            f"{len(feed_rows) - len(both)} by the feed alone, and "
            f"{len(aggregator_rows) - len(both)} by the aggregator alone.",
            "Also reported by a source that states no kind of payment: "
            f"{sum(1 for r in rows if r.row.entity_id in self.no_kind_sources)}.",
            f"Out of the account: {len(rows) - money_in}. Into the account: {money_in}"
            + (
                " (money back from a cash machine, which the rule leaves alone)."
                if money_in
                else "."
            ),
            f"Pending: {pending}. Booked: {len(rows) - pending}.",
            _span([r.row.value_date for r in rows]),
            (
                f"The feed states an original amount in another currency for {foreign}; the leg "
                "would be the account's own debit, in pounds."
                if foreign
                else "None states an original amount in another currency."
            ),
        ]
        if self.credit_card:
            lines.append(
                "This account is a credit card, so its cash withdrawals are cash advances: "
                "still a transfer to the cash account, and counted apart for that reason."
            )
        return lines

    def _deposit_sentences(self, rows: list[Reading]) -> list[str]:
        noun = "a cash deposit" if len(rows) == 1 else "cash deposits"
        money_out = sum(1 for r in rows if r.row.amount_minor < 0)
        pending = sum(1 for r in rows if r.row.status is TransactionStatus.PENDING)
        return [
            f"{_transactions(len(rows))} that {agree(len(rows), 'is')} not history "
            f"{agree(len(rows), 'is')} {noun} by what a source states "
            f"({FEED_WORDS[DEPOSIT][0]} {FEED_WORDS[DEPOSIT][1]}).",
            f"Into the account: {len(rows) - money_out}. Out of the account: {money_out}"
            + (" (odd for a deposit, which the rule leaves alone)." if money_out else "."),
            f"Pending: {pending}. Booked: {len(rows) - pending}.",
            _span([r.row.value_date for r in rows]),
        ]

    def _cross_tabulation(self) -> list[str]:
        lines: list[str] = []
        feed_says = [r for r in self.readings if FEED_WORDS[WITHDRAWAL] in by_kind(r.words)[0]]
        if feed_says:
            seen: Counter[str] = Counter()
            for r in feed_says:
                aggregator = sorted(
                    word for name, word in by_kind(r.words)[1] if name == AGGREGATOR_CASH[0]
                )
                seen[", ".join(aggregator) if aggregator else "not reported by the aggregator"] += 1
            lines.append(
                f"Of the {_transactions(len(feed_says))} the feed says "
                f"{agree(len(feed_says), 'is')} a cash machine, the aggregator's category "
                f"is: {_counted(seen)}."
            )
        aggregator_says = [r for r in self.readings if AGGREGATOR_CASH in by_kind(r.words)[1]]
        if aggregator_says:
            seen = Counter()
            for r in aggregator_says:
                stated = dict(by_kind(r.words)[0])
                feed = [stated[name] for name in ("source", "sourceSubType") if name in stated]
                seen[" ".join(feed) if feed else "not reported by the feed"] += 1
            money_in = sum(1 for r in aggregator_says if r.row.amount_minor > 0)
            lines.append(
                f"Of the {_transactions(len(aggregator_says))} the aggregator says "
                f"{agree(len(aggregator_says), 'is')} cash, {len(aggregator_says) - money_in} "
                f"{agree(len(aggregator_says) - money_in, 'is')} money out and {money_in} money "
                f"in, and the feed's source and sourceSubType are: {_counted(seen)}."
            )
        return lines

    def _disagreement_sentences(self) -> list[str]:
        pairs: Counter[str] = Counter()
        rows = 0
        for r in self.readings:
            feed, aggregator = by_kind(r.words)
            pair = ""
            machine = " ".join(FEED_WORDS[WITHDRAWAL])
            cash = " ".join(AGGREGATOR_CASH)
            if FEED_WORDS[WITHDRAWAL] in feed and (
                kind := aggregator_excludes_cash_machine(aggregator)
            ):
                pair = f"{machine} against {AGGREGATOR_CASH[0]} {kind}"
            elif AGGREGATOR_CASH in aggregator and (kind := feed_excludes_cash(feed)):
                pair = f"{cash} against {kind}"
            if pair:
                pairs[pair] += 1
                rows += 1
        if not rows:
            return ["No two sources state kinds that exclude each other."]
        return [
            f"Two sources state kinds that exclude each other for {rows} "
            f"({_counted(pairs)}). A coarser statement, such as a purchase, is not one."
        ]

    def _rule_sentences(self, *, chosen: bool) -> list[str]:
        withdrawals, deposits = self.made(WITHDRAWAL), self.made(DEPOSIT)
        left: Counter[str] = Counter()
        for r in self.readings:
            if r.judgement.outcome != LEG:
                left[r.judgement.outcome] += 1
        undecided = sum(
            1
            for r in self.readings
            if r.judgement.outcome != LEG and r.judgement.movement == ""
        )
        names = {
            PENDING: "pending",
            WRONG_WAY: "the wrong way round",
            DISAGREED: "disagreed",
            AFTER_CLOSE: "dated after the cash account closed",
        }
        said = [f"{names[kind]} {left[kind]}" for kind in names if left[kind]]
        if undecided:
            said.append(
                f"stated only by a source that does not decide this account {undecided}"
            )
        residue = f" Left out: {', '.join(said)}." if said else ""
        if not chosen:
            return [
                "The rule would make none, because no cash account is chosen. "
                f"It would take {_transactions(len(withdrawals) + len(deposits))} if one were "
                "chosen." + residue
            ]
        total = len(withdrawals) + len(deposits)
        days = sorted(r.row.value_date for r in [*withdrawals, *deposits])
        when = f", dated {days[0].isoformat()} to {days[-1].isoformat()}" if days else ""
        return [
            f"The rule would make {plural(total, 'transfer')} with the cash account: "
            f"{plural(len(withdrawals), 'withdrawal')} and {plural(len(deposits), 'deposit')}"
            f"{when}." + residue
        ]


@dataclass
class CashWithdrawalReport:
    choice: CashAccountChoice
    accounts: list[AccountCashFigures] = field(default_factory=list)
    #: Account -> (source name, field) -> word -> how many stored transactions state it.
    vocabulary: dict[str, dict[str, Counter[str]]] = field(default_factory=dict)
    #: The legs the rule has made and the cash account holds.
    held: list[Transaction] = field(default_factory=list)

    def _held_sentence(self) -> str:
        """What the rule has already made, to be read beside what it says it would make."""
        if not self.held:
            return "No transfer made by the rule is held."
        withdrawals = sum(1 for t in self.held if t.raw.get("movement") == WITHDRAWAL)
        deposits = len(self.held) - withdrawals
        days = sorted(t.value_date for t in self.held)
        return (
            f"{plural(len(self.held), 'transfer')} made by the rule "
            f"{agree(len(self.held), 'is')} held: {plural(withdrawals, 'withdrawal')} and "
            f"{plural(deposits, 'deposit')}, dated {days[0].isoformat()} to {days[-1].isoformat()}."
        )

    @property
    def would_make(self) -> list[Reading]:
        """Every row the rule makes a leg for, in date order: the rule's own count."""
        if self.choice.ref is None:
            return []
        return sorted(
            (r for a in self.accounts for r in a.readings if r.judgement.outcome == LEG),
            key=lambda r: (r.row.value_date, r.row.entity_id),
        )

    def sentences(self) -> list[str]:
        lines: list[str] = []
        choice = self.choice
        if choice.ref is not None and choice.until is not None:
            lines.append(
                f"{choice.ref} is the one account declared as the place cash goes, and it closed "
                f"on {choice.until.isoformat()}, so the rule would make no leg dated after that."
            )
        elif choice.ref is not None:
            # At the head of the line, so a page that writes an account's name writes this one's.
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
        lines.append(self._held_sentence())
        if not self.accounts:
            lines.append(
                "No stored transaction that is not history is a cash movement by what a source "
                "states, and none has a description that looks like a cash machine."
            )
        for figures in self.accounts:
            lines.append(f"{figures.account}:")
            lines.extend(
                f"  {sentence}" for sentence in figures.sentences(chosen=choice.ref is not None)
            )
        lines.append(
            "The words each source states in its coded fields, counted over the stored "
            "transactions that are not history it reported, so the word a bank uses is read "
            "from here:"
        )
        if not self.vocabulary:
            lines.append(
                "  No stored transaction was reported by a source that states a coded field."
            )
        for account, by_field in sorted(self.vocabulary.items()):
            lines.append(f"{account}:")
            for name, words in sorted(by_field.items()):
                lines.append(f"  {name}: {_counted(words)}")
        return lines


def cash_withdrawal_report(store: Store, records: list[AccountRecord]) -> CashWithdrawalReport:
    """The cash movements the stored transactions hold, by the words their sightings stated."""
    choice = choose_cash_account(records)
    report = CashWithdrawalReport(choice)
    kinds = {str(r.ref): r.kind.strip().casefold() for r in records}
    by_account: dict[str, AccountCashFigures] = {}

    def figures_for(account: str) -> AccountCashFigures:
        if account not in by_account:
            by_account[account] = AccountCashFigures(account)
        return by_account[account]

    readings = read_candidates(
        store, str(choice.ref) if choice.ref is not None else None, choice.until
    )
    entities = [r.row.entity_id for r in readings]
    stating_no_kind = _sighted_by_sources_stating_no_kind(store, entities)
    on_card = _on_a_card_artefact(store, entities)
    for reading in readings:
        figures = figures_for(reading.row.account_id)
        figures.readings.append(reading)
        if reading.row.entity_id in stating_no_kind:
            figures.no_kind_sources.add(reading.row.entity_id)
        if reading.row.entity_id in on_card:
            figures.on_card.add(reading.row.entity_id)
    stated = {r.row.entity_id for r in readings}
    for account, count in _guessed(store, stated, choice).items():
        figures_for(account).guessed.update(count)
    for account, figures in by_account.items():
        figures.credit_card = kinds.get(account) == _CREDIT_CARD_KIND or bool(figures.on_card)
    report.accounts = [by_account[a] for a in sorted(by_account)]
    report.vocabulary = _vocabulary(store)
    report.held = sorted(
        store.transactions_with_source(CASH_LEG_SOURCE), key=lambda t: (t.value_date, t.entity_id)
    )
    return report


def _sighted_by_sources_stating_no_kind(store: Store, entities: list[str]) -> set[str]:
    found: set[str] = set()
    stating = sorted(FIRST_PARTY_FEEDS | AGGREGATORS)
    for start in range(0, len(entities), 400):
        chunk = entities[start : start + 400]
        marks = ",".join("?" for _ in chunk)
        source_marks = ",".join("?" for _ in stating)
        found.update(
            str(row[0])
            for row in store.connection.execute(
                "SELECT DISTINCT entity_id FROM transaction_sources "  # noqa: S608
                f"WHERE entity_id IN ({marks}) AND source NOT IN ({source_marks})",
                (*chunk, *stating),
            )
        )
    return found


def _on_a_card_artefact(store: Store, entities: list[str]) -> set[str]:
    found: set[str] = set()
    for start in range(0, len(entities), 400):
        chunk = entities[start : start + 400]
        marks = ",".join("?" for _ in chunk)
        found.update(
            str(row[0])
            for row in store.connection.execute(
                "SELECT DISTINCT s.entity_id FROM transaction_sources s "  # noqa: S608
                "JOIN raw_artefacts a ON a.digest = s.artefact_digest "
                f"WHERE a.source = ? AND s.entity_id IN ({marks})",
                (_CARD_ARTEFACT, *chunk),
            )
        )
    return found


def _guessed(
    store: Store, stated: set[str], choice: CashAccountChoice
) -> dict[str, Counter[str]]:
    """Per account, the rows no source states whose description looks like a cash machine."""
    cash = str(choice.ref) if choice.ref is not None else None
    likes = " OR ".join("description LIKE ?" for _ in range(3))
    found: dict[str, Counter[str]] = defaultdict(Counter)
    for entity, account, status, description in store.connection.execute(
        "SELECT entity_id, account_id, status, description FROM transactions "  # noqa: S608
        f"WHERE {likes}",
        ("%atm%", "%cash%", "%machine%"),
    ):
        if str(entity) in stated or str(account) == cash:
            continue
        if TransactionStatus(str(status)).is_history:
            continue
        for name in description_patterns(str(description)):
            found[str(account)][name] += 1
    return found


def _vocabulary(store: Store) -> dict[str, dict[str, Counter[str]]]:
    """Account -> "source name, field" -> word -> stored transactions, not history, stating it."""
    history = ",".join(f"'{s.value}'" for s in TransactionStatus if s.is_history)
    labelled = {(source, name): f"{label}, {name}" for label, source, name in _VOCABULARY}
    found: dict[str, dict[str, Counter[str]]] = defaultdict(lambda: defaultdict(Counter))
    for account, source, name, word, count in store.connection.execute(
        "SELECT t.account_id, w.source, w.field, w.word, COUNT(DISTINCT t.entity_id) "  # noqa: S608
        "FROM sighting_words w JOIN transactions t ON t.entity_id = w.entity_id "
        f"WHERE t.status NOT IN ({history}) GROUP BY t.account_id, w.source, w.field, w.word"
    ):
        label = labelled.get((str(source), str(name)))
        if label is not None:
            found[str(account)][label][str(word)] = int(count)
    return {account: dict(fields) for account, fields in found.items()}


__all__ = ["AccountCashFigures", "CashWithdrawalReport", "cash_withdrawal_report"]
