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
from datetime import date, datetime

from .account_observations import Observation
from .parsers.pdf_statements import PDF_PARSERS, PdfStatementParser, _lines
from .store import Store


def _parser_for(lines: list[str]) -> PdfStatementParser | None:
    """The parser whose issuer the document names.

    Chosen from the TEXT rather than from the artefact's source column,
    which records how the file arrived (its extension) rather than which
    bank wrote it - a distinction that silently produced no terms at all
    until a test asked for some.
    """
    for parser_class in PDF_PARSERS:
        marker = parser_class.marker.casefold()
        if any(marker in line.casefold() for line in lines):
            return parser_class()
    return None


def observations_from_statements(store: Store) -> list[Observation]:
    """Every account fact the held statements state, as dated observations.

    Each statement contributes what it witnessed on its own date: the rates
    it quotes, the limit it states, the balance it closes on, and any
    promotional window with the date it ends. Contradictions between
    statements are not resolved here - that is the projection's job, and
    resolving them early is what would make import order matter.
    """
    found: list[Observation] = []
    for row in store.connection.execute(
        "SELECT digest, account_ref, source, payload FROM raw_artefacts "
        "WHERE media_type = 'application/pdf'"
    ):
        digest = str(row["digest"])[:12]
        try:
            lines = _lines(bytes(row["payload"]))
        except Exception as exc:
            # Said aloud rather than skipped quietly: a statement that
            # contributes nothing looks exactly like a statement with
            # nothing to contribute, and only one of those is a fault.
            print(f"artefact {digest}: could not be read - {exc}", file=sys.stderr)
            continue
        parser = _parser_for(lines)
        if parser is None:
            continue
        try:
            reading = parser.reader(lines)
        except Exception as exc:
            print(
                f"artefact {digest}: {parser.source} could not read it - {exc}",
                file=sys.stderr,
            )
            continue
        observed = reading.statement_date
        if observed is None:
            continue
        account = str(row["account_ref"])
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
                f"promotional rate ends in {days} day(s) on "
                f"{observation.window_to}, with "
                f"{format_amount(owed)} still owed as at "
                f"{stamped[0] if stamped else 'unknown'}",
                rung,
            )
        )
    return sorted(found)
