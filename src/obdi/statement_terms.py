"""Account terms, derived from the statements already held.

Nothing new is stored. The statements are in the raw layer, so their terms
are DERIVED the way transactions are - re-read on demand, from evidence
that never changes - which is what makes import order irrelevant without
anybody having to be careful about it.

The point of having them is the one thing no feed exposes: a promotional
rate carries the date it reverts, so an account can be warned BEFORE a
balance starts costing what it did not cost yesterday.
"""

from __future__ import annotations

import sys
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from .account_observations import Observation
from .parsers.base import ParseError
from .parsers.pdf_statements import (
    PdfStatementParser,
    SectionReading,
    _lines,
    pdf_parser_for,
)
from .parsers.statement_reading import (
    READING_FORMAT,
    StatementReading,
    kept_format,
    reading_from_json,
    reading_to_json,
)
from .plural import plural
from .store import SectionAssignment, Store


def _parser_for(payload: bytes) -> PdfStatementParser | None:
    """The parser that claims this document, via the shared door.

    Chosen from the document's TEXT rather than from the artefact's source
    column, which records how the file arrived (its extension) rather than
    which bank wrote it - a distinction that silently produced no terms at
    all until a test asked for some.

    This used to walk the registry itself, applying a looser rule than the
    import door: any parser whose issuer was named, first match wins. Two
    places choosing a parser are two places that can choose differently,
    and the one reached by a person uploading a file was the stricter of
    them. One door now, so an ambiguous document is refused here too.
    """
    try:
        return pdf_parser_for(payload)
    except ParseError as exc:
        # Terms are derived in a sweep over everything held, so one
        # ambiguous statement must not stop the rest - but it says so
        # rather than contributing nothing quietly.
        print(f"ambiguous statement, no terms taken: {exc}", file=sys.stderr)
        return None


def _read_with_source(payload: bytes, digest: str) -> tuple[str, StatementReading] | None:
    """One held PDF, read, with the parser's source name, or None - with the
    reason said aloud on stderr.

    A statement that cannot be read must not be contributed as an empty
    reading: the two look identical downstream.
    """
    try:
        # Read for its own sake: a payload that cannot be turned into
        # text at all is a document nothing downstream can use, and
        # finding that out here keeps the reason attached to the
        # artefact it belongs to.
        _lines(payload)
    except Exception as exc:
        # Said aloud rather than skipped quietly: a statement that
        # contributes nothing looks exactly like a statement with
        # nothing to contribute, and only one of those is a fault.
        print(f"artefact {digest}: could not be read - {exc}", file=sys.stderr)
        return None
    parser = _parser_for(payload)
    if parser is None:
        return None
    try:
        # The payload rather than the lines: a format whose table is
        # only legible by coordinate reads the page for itself, and the
        # parser is the one that knows which reading its document needs.
        return parser.source, parser.read(payload)
    except Exception as exc:
        print(
            f"artefact {digest}: {parser.source} could not read it - {exc}",
            file=sys.stderr,
        )
        return None


def _held_pdfs(store: Store, account_ref: str | None) -> list[tuple[str, str]]:
    """(digest, account filed under) of every held PDF, or those under `account_ref`.

    Distinct, because the same bytes may be held under more than one source
    label and one document is one statement. The payload is fetched separately,
    only when somebody needs it: a page that has already read a statement
    should not pull its bytes out of the store again.
    """
    scope = "" if account_ref is None else " AND account_ref = ?"
    return [
        (str(row["digest"]), str(row["account_ref"]))
        for row in store.connection.execute(
            "SELECT DISTINCT digest, account_ref FROM raw_artefacts "  # noqa: S608
            f"WHERE media_type = 'application/pdf'{scope}",
            () if account_ref is None else (account_ref,),
        )
    ]


def _payload_of(store: Store, digest: str, account_ref: str) -> bytes:
    row = store.connection.execute(
        "SELECT payload FROM raw_artefacts "
        "WHERE digest = ? AND account_ref = ? AND media_type = 'application/pdf' LIMIT 1",
        (digest, account_ref),
    ).fetchone()
    return b"" if row is None else bytes(row["payload"])


def _reading_of(
    store: Store, digest: str, account_ref: str
) -> tuple[str, StatementReading] | None:
    """What a held PDF says: the reading the store kept for its digest, else the
    document read afresh (and not kept - see `keep_statement_readings`).

    A kept reading that cannot be decoded is said aloud and read again: it is
    derived data, so the document is the authority, and a pass that trusted
    half a reading would be wrong without saying so.
    """
    kept = _kept_reading(store, digest)
    if kept is not None:
        return kept
    return _read_with_source(_payload_of(store, digest, account_ref), digest[:12])


def _kept_reading(store: Store, digest: str) -> tuple[str, StatementReading] | None:
    stored = store.stored_statement_reading(digest)
    if stored is None:
        return None
    try:
        return stored[0], reading_from_json(stored[1])
    except (ValueError, KeyError, TypeError) as exc:
        print(
            f"artefact {digest[:12]}: stored reading is unusable ({exc}), "
            "reading the document again",
            file=sys.stderr,
        )
        return None


def _keeps_period(store: Store, digest: str) -> bool:
    """Whether the kept reading was written by a version that reads everything now read.

    A reading kept before a parser learnt to read a field says nothing about it, which is not
    the same as saying the document states none: only reading the document again tells the two
    apart (`READING_FORMAT`). Until then `statement_spans` gives the weaker answer its evidence
    supports and never a wrong one.
    """
    stored = store.stored_statement_reading(digest)
    if stored is None:
        return False
    try:
        return kept_format(stored[1]) >= READING_FORMAT
    except (ValueError, TypeError):
        return False


@dataclass(frozen=True)
class StatementPeriod:
    """A held statement's closing day, and its own first day where the document states one."""

    account_ref: str
    closing: date
    #: The first day the statement itself says it covers; None where the format states only a
    #: closing date, or the reading kept does not hold one yet (`_keeps_period`).
    opens: date | None
    #: The parser's source name, "" for a section of an "all accounts" statement.
    source: str
    #: The date of the earliest row the statement lists, from the kept reading; None where there
    #: is no kept reading or the statement lists nothing.
    first_row: date | None = None

    @property
    def covers_from(self) -> date | None:
        """The first day the statement is known to account for: its stated start, else the
        day of its first row."""
        return self.opens or self.first_row


def statement_periods(store: Store) -> list[StatementPeriod]:
    """Each trusted statement's closing day with the period it states, from kept readings.

    The statements are the ones `statement_balances` trusts, so a period is never offered for a
    document whose closing balance is not. The period is read from the reading the store kept
    and never from the document: a view must not extract text, so a statement with no kept
    reading is listed with no stated start.
    """
    found: list[StatementPeriod] = []
    balances, _ = statement_balances(store)
    for balance in balances:
        kept = _kept_reading(store, balance.digest) if balance.source else None
        reading = None if kept is None else kept[1]
        found.append(
            StatementPeriod(
                balance.account_ref,
                balance.day,
                None if reading is None else reading.period_start,
                balance.source,
                None
                if reading is None
                else min((row.value_date for row in reading.transactions), default=None),
            )
        )
    return found


def keep_statement_readings(store: Store) -> int:
    """Read every held PDF that has no usable kept reading, keep what it says,
    and commit; returns how many documents were read.

    Called by the pass that already writes (`same_money_fold.fold_same_money`),
    never by a page view, so a view stays a reader and never waits for the
    write lock a pull holds. A document that cannot be read keeps nothing and is
    said aloud each time, as it was before readings were kept.
    """
    read = 0
    for digest, account in _held_pdfs(store, None):
        if _kept_reading(store, digest) is not None and _keeps_period(store, digest):
            continue
        found = _read_with_source(_payload_of(store, digest, account), digest[:12])
        read += 1
        if found is not None:
            store.keep_statement_reading(digest, found[0], reading_to_json(found[1]))
    store.connection.commit()
    return read


#: Artefact digest -> the sections of that statement, for a multi-account one.
#: Same reason as `_BALANCE_BY_DIGEST`: the bytes never change, and re-reading a
#: wide page's geometry on every view of a ledger would make it unusable.
_SECTIONS_BY_DIGEST: dict[str, list[SectionReading]] = {}


def _sections_of(store: Store, digest: str) -> list[SectionReading]:
    """The sections of a held multi-account statement, empty when it has none
    or cannot be read - said aloud on stderr, like every other unreadable one."""
    if digest not in _SECTIONS_BY_DIGEST:
        found: list[SectionReading] = []
        row = store.connection.execute(
            "SELECT payload FROM raw_artefacts "
            "WHERE digest = ? AND media_type = 'application/pdf' LIMIT 1",
            (digest,),
        ).fetchone()
        parser = None if row is None else _parser_for(bytes(row["payload"]))
        if row is not None and parser is not None:
            try:
                found = parser.sections(bytes(row["payload"])) or []
            except Exception as exc:
                print(
                    f"artefact {digest[:12]}: {parser.source} could not read its "
                    f"sections - {exc}",
                    file=sys.stderr,
                )
        _SECTIONS_BY_DIGEST[digest] = found
    return _SECTIONS_BY_DIGEST[digest]


def assigned_sections(
    store: Store, account_ref: str | None = None
) -> Iterator[tuple[SectionAssignment, SectionReading | None]]:
    """Each assigned section of a held statement, with its reading if it can be found.

    None means the declared assignment names a section the statement no longer
    holds, which a caller counts as unusable rather than skipping.
    """
    for assignment in store.statement_section_assignments():
        if account_ref is not None and assignment.account_ref != account_ref:
            continue
        found = next(
            (
                item
                for item in _sections_of(store, assignment.digest)
                if item.key == assignment.section_key
            ),
            None,
        )
        yield assignment, found


def held_statement_readings(store: Store) -> Iterator[tuple[str, StatementReading]]:
    """(account the statement is filed under, its reading) for each readable one.

    An assigned section of an "all accounts" statement is one of them, filed
    under the account it was assigned to.
    """
    for digest, account in _held_pdfs(store, None):
        found = _reading_of(store, digest, account)
        if found is not None:
            yield account, found[1]
    for assignment, section in assigned_sections(store):
        if section is not None and not section.refusal:
            yield assignment.account_ref, section.reading


@dataclass(frozen=True)
class StatementBalance:
    """A held statement's closing balance, in the store's own sign convention."""

    account_ref: str
    day: date
    balance_minor: int
    #: The parser's source name, "" for a section of an "all accounts"
    #: statement. Not part of equality: a balance is the same fact whichever
    #: way it is asked for.
    source: str = field(default="", compare=False)
    #: The artefact digest the statement is held under, which every row it lists
    #: carries on its sighting. Not part of equality, like `source`.
    digest: str = field(default="", compare=False)


@dataclass(frozen=True)
class StatementDayBalance:
    """A balance a held statement states for the END of one day."""

    account_ref: str
    source: str
    day: date
    balance_minor: int


@dataclass(frozen=True)
class _Usable:
    source: str
    closing: tuple[date, int]
    #: The statement's opening (the end of the day before its first row), each
    #: printed end-of-day balance that agrees with its own rows, and its closing.
    days: tuple[tuple[date, int], ...]
    #: Printed end-of-day balances that disagreed with the statement's own rows
    #: and were left out.
    rejected: int


def _day_balances(
    reading: StatementReading, opening: int, closing: int, closing_day: date
) -> tuple[tuple[tuple[date, int], ...], int]:
    """The stated balances of a statement that reconciles, and how many printed
    ones were refused.

    A printed end-of-day balance is accepted only when it equals the
    statement's opening balance plus every row dated on or before that day, so
    a figure read from the wrong row or misplaced by the page never becomes an
    anchor. The opening is the balance at the end of the day before the first
    row, whatever date the period starts on, because nothing moves in between.
    """
    found: dict[date, int] = {closing_day: closing}
    if reading.transactions:
        first = min(row.value_date for row in reading.transactions)
        found.setdefault(first - timedelta(days=1), opening)
    rejected = 0
    for day, printed in reading.end_of_day_minor:
        walked = opening + sum(
            row.amount_minor for row in reading.transactions if row.value_date <= day
        )
        # A figure that disagrees with the rows, or with another figure already
        # taken for the same day, is refused.
        if printed != walked or found.setdefault(day, printed) != printed:
            rejected += 1
    return tuple(sorted(found.items())), rejected


#: Artefact digest -> what it states, or None when the document states no figure
#: that can be trusted. A document's bytes never change, so a reading is valid
#: for the life of the process, and a ledger page that re-extracted every held
#: statement's text on each view would be unusable.
#:
#: Only a reading that WORKED is held for good. A document that yielded nothing is held with the
#: standing epoch it was tried at and tried again once the store has moved, because the nothing
#: can be a failure that is not a property of the bytes (an unreadable payload at that moment, a
#: parser that raised) and a failure held for the life of the process is never seen to be one.
_USABLE_BY_DIGEST: dict[str, tuple[int, _Usable | None]] = {}


def _usable(store: Store, digest: str, account: str) -> _Usable | None:
    held_before = _USABLE_BY_DIGEST.get(digest)
    if held_before is not None and held_before[1] is not None:
        # A reading that worked is a fact about the bytes, and asks the store nothing.
        return held_before[1]
    epoch = store.standing_epoch()
    if held_before is None or held_before[0] != epoch:
        found = _reading_of(store, digest, account)
        held: _Usable | None = None
        if found is not None:
            source, reading = found
            opening = reading.opening_balance_minor
            closing = reading.closing_balance_minor
            if (
                opening is not None
                and closing is not None
                and reading.statement_date is not None
                and not reading.notes
                and reading.reconciles
            ):
                days, rejected = _day_balances(reading, opening, closing, reading.statement_date)
                held = _Usable(source, (reading.statement_date, closing), days, rejected)
        _USABLE_BY_DIGEST[digest] = (epoch, held)
    return _USABLE_BY_DIGEST[digest][1]


def statement_day_balances(
    store: Store, account_ref: str
) -> tuple[list[StatementDayBalance], int]:
    """Every end-of-day balance the account's held PDFs state, and how many
    printed ones were refused for disagreeing with their own statement's rows.

    Each is a fact about the day's END, in the store's sign convention: the
    same SIGN RULE as `statement_balances`, which limits this to statements
    whose rows carry their opening to their closing balance. The "all
    accounts" statements' assigned sections are left to `statement_balances`.
    """
    balances: list[StatementDayBalance] = []
    rejected = 0
    for digest, account in _held_pdfs(store, account_ref):
        held = _usable(store, digest, account)
        if held is None:
            continue
        rejected += held.rejected
        balances += [
            StatementDayBalance(account, held.source, day, minor) for day, minor in held.days
        ]
    return balances, rejected


def statement_balances(
    store: Store, account_ref: str | None = None
) -> tuple[list[StatementBalance], int]:
    """The closing balances the held statements can be trusted to state, and
    how many held PDFs could not supply one.

    SIGN RULE. A balance is taken only from a statement whose own rows carry
    its opening balance to its closing balance (`StatementReading.reconciles`),
    and those rows are signed the store's way: spending negative, money in
    positive. Because the walk from opening to closing succeeds in that
    convention, the closing balance is in it too, which for a card makes money
    owed a NEGATIVE position. A statement that does not reconcile, carries
    notes, states no closing figure or date, or cannot be read at all is
    counted and left out - an anchor with the wrong sign would derive a
    confident wrong opening, and absence is safe where a guess is not.
    """
    usable: list[StatementBalance] = []
    unusable = 0
    for digest, account in _held_pdfs(store, account_ref):
        known = _usable(store, digest, account)
        if known is None:
            unusable += 1
        else:
            usable.append(
                StatementBalance(
                    account, known.closing[0], known.closing[1], known.source, digest
                )
            )
    # An assigned section states its own closing balance, judged by the same
    # rule: its own rows carry its own opening balance to it. A loan's is
    # already negative, because the reader holds what is owed as a negative
    # position, so a Position that sums balances counts the liability.
    for assignment, section in assigned_sections(store, account_ref):
        if (
            section is not None
            and not section.refusal
            and section.reading.statement_date is not None
            and section.reading.closing_balance_minor is not None
        ):
            usable.append(
                StatementBalance(
                    assignment.account_ref,
                    section.reading.statement_date,
                    section.reading.closing_balance_minor,
                    digest=assignment.digest,
                )
            )
        else:
            unusable += 1
    return usable, unusable


def observations_from_statements(store: Store) -> list[Observation]:
    """Every account fact the held statements state, as dated observations.

    Each statement contributes what it witnessed on its own date: the rates
    it quotes, the limit it states, the balance it closes on, and any
    promotional window with the date it ends. Contradictions between
    statements are not resolved here - that is the projection's job, and
    resolving them early is what would make import order matter.
    """
    found: list[Observation] = []
    for account, reading in held_statement_readings(store):
        observed = reading.statement_date
        if observed is None:
            continue
        source = f"statement {observed}"

        def add(
            fact: str,
            value: str,
            *,
            kind: str = "",
            window_from: date | None = None,
            window_to: date | None = None,
            _account: str = account,
            _observed: date = observed,
            _source: str = source,
        ) -> None:
            found.append(
                Observation(
                    account_id=_account,
                    fact=fact,
                    kind=kind,
                    observed_at=_observed,
                    value=value,
                    window_from=window_from,
                    window_to=window_to,
                    source=_source,
                )
            )

        for kind, percent in reading.rates.items():
            add("rate", str(percent), kind=kind)
        if reading.credit_limit_minor is not None:
            add("credit_limit", str(reading.credit_limit_minor))
        if reading.closing_balance_minor is not None:
            add("balance", str(reading.closing_balance_minor))
        for window in reading.rate_windows:
            # The window a promotional rate applies over: witnessed on the
            # statement date, applying until the date the bank named.
            add(
                "rate",
                str(window.percent),
                kind="promotional",
                window_from=observed,
                window_to=window.until,
            )
    return found


#: How near a reversion has to be before it is worth saying, and how loudly.
#: Longer notice than the consent ladder because the useful response - move
#: the balance, or clear it - takes weeks rather than an afternoon.
REVERSION_RUNGS = ((7, 4), (14, 3), (30, 2), (60, 1))


def reversion_findings(
    observations: list[Observation], *, today: date | None = None
) -> list[tuple[str, str, int]]:
    """(key, message, rung) for every promotional rate about to end on an
    account that still owes something.

    A reversion with nothing outstanding is not news - the rate applies to
    a balance of zero - so the balance is part of the condition rather than
    decoration on the message.
    """
    now = today or datetime.now().astimezone().date()
    balances: dict[str, tuple[date, int]] = {}
    for observation in observations:
        if observation.fact != "balance":
            continue
        held = balances.get(observation.account_id)
        if held is None or observation.observed_at > held[0]:
            balances[observation.account_id] = (
                observation.observed_at,
                int(float(observation.value)),
            )

    found = []
    for observation in observations:
        if observation.fact != "rate" or observation.window_to is None:
            continue
        days = (observation.window_to - now).days
        if days < 0:
            continue
        rung = next((rung for limit, rung in REVERSION_RUNGS if days <= limit), 0)
        if not rung:
            continue
        stamped = balances.get(observation.account_id)
        owed = -stamped[1] if stamped else 0
        if owed <= 0:
            continue
        from .money import format_amount

        found.append(
            (
                f"reversion:{observation.account_id}:{observation.window_to}",
                f"{observation.account_id}: the {observation.value}% "
                f"promotional rate ends in {plural(days, 'day')} on "
                f"{observation.window_to}, with "
                f"{format_amount(owed)} still owed as at "
                f"{stamped[0] if stamped else 'unknown'}",
                rung,
            )
        )
    return sorted(found)
