"""The open review flags as questions a person can answer, and what an answer does.

A flag is raised when a row was stored as new although something in the same account matches it
on amount and date (`review_report` says which flags the evidence has already answered, and
`review_settlement` closes those). What remains is `FlagClass.OPEN`: a real question, put here as
a card per flag naming the row, each neighbour it was weighed against, how each was listed, and
what the evidence says either way.

THE TWO ANSWERS, and how each outlives a rebuild, which replays every artefact and would raise
the same flag again:

  two payments   the flag is RESOLVED (`resolved_at`), which a rebuild keeps and which stops the
                 same flag being raised again (`store._QUEUE_REVIEW_SQL` ignores a row that is
                 already there). A human-rank annotation on the row says it was a person's
                 answer and when, which is what the "Answered" list reads.
  one payment    the two rows are joined by `Store.absorb_entity`, the path an exact rule takes
                 when it finds two stored rows to be one payment. The join is recorded as a
                 human-rank annotation on the surviving row, and `replay_joins` repeats it after
                 a rebuild has replayed the artefacts, before the flags are settled. The
                 annotation table is keyed by entity id, and entity ids are deterministic, so the
                 record re-attaches to the rows it was about. No table was added for this.

UNDO. Withdrawing "two payments" reopens the flag. Withdrawing "one payment" withdraws the
record, and the rows stay joined until the next rebuild, which separates them and raises the flag
again: nothing here can take rows apart without replaying the artefacts, and a page that said
otherwise would be lying about the store.

A card carries a fingerprint of what it showed (`fingerprint_of`), and an answer is refused when
what is held no longer hashes to it, so an answer cannot land on something other than what was
read.

Rejected: a new table of decisions. The schema is versioned and the annotation table already
holds revisable facts about transactions that a rebuild re-attaches.
Rejected: offering "one payment" where either proof on file says two payments (`neighbour_proof`).
An aggregator that reissues an id is the only source whose differing ids the evidence cannot read
as two payments, and a join the evidence contradicts would be undone by the next rebuild.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any

from ..core.errors import DataError
from ..core.masking import Structural
from ..core.models import SourceTier, TransactionStatus
from ..core.page_words import REMOVE_PROTECTION, REMOVE_TYPED_TRANSACTION
from ..ingest.finishers import FlagClass
from ..ingest.join_basis import how_words, sighting_views
from ..ingest.matching import EXACT_RULE_DOUBT
from ..ingest.payment_links import AGGREGATORS
from ..ingest.store import Store
from .review_report import (
    GAP_AFTER,
    GAP_BEFORE,
    BalanceGap,
    assess_flags,
    live_neighbours,
    neighbour_proof,
)

#: The `Evidence.verdict` of a line that says what would settle a flag rather than which way the
#: evidence points.
VERDICT_SETTLE = "settle"

KIND_TWO = "flag-two-payments"
KIND_JOINED = "flag-joined"
HUMAN = "human"

#: How many answers the foot of the queue lists.
ANSWERED_SHOWN = 20

#: What each source is called on the page. A source not named here is shown as it is stored.
SOURCE_WORDS = {
    "starling": "the bank's own feed",
    "truelayer": "the aggregator",
    "starling-csv": "the export",
    "csv": "the export",
    "statement": "a statement",
    "qif": "a QIF file",
    "manual": "a typed entry",
}


def source_word(source: str) -> str:
    return SOURCE_WORDS.get(source, source)


class FlagRefused(DataError):
    """An answer that was not taken. The message is a sentence for the page."""


@dataclass(frozen=True)
class SourceLine:
    source: Structural[str]
    how: Structural[str]


@dataclass(frozen=True)
class RowView:
    entity_id: Structural[str]
    day: Structural[str]
    status: Structural[str]
    sources: Structural[tuple[SourceLine, ...]]
    #: Values: shown only where a person asked for them.
    amount: str
    description: str


@dataclass(frozen=True)
class Evidence:
    #: "two" or "one": which answer the sentence points to. "settle" is no answer: it says what
    #: evidence not yet held would settle the question (`settle_evidence`).
    verdict: Structural[str]
    sentence: Structural[str]


@dataclass(frozen=True)
class NeighbourView:
    row: Structural[RowView]
    days_apart: Structural[int]
    #: A sentence where the evidence already says these two are two payments, else "".
    proof: Structural[str]
    says: Structural[tuple[Evidence, ...]]


@dataclass(frozen=True)
class FlagCard:
    flag_id: Structural[str]
    fingerprint: Structural[str]
    account: Structural[str]
    #: The account's reference, for the form that asks what an answer did to its standing. It is
    #: never printed.
    ref: Structural[str]
    #: "out" or "in".
    direction: Structural[str]
    flagged: Structural[RowView]
    neighbours: Structural[tuple[NeighbourView, ...]]
    #: Where two exact rules disagreed and no neighbour can answer it, what they disagreed on.
    doubt: Structural[str]


@dataclass(frozen=True)
class AnsweredLine:
    #: "two" or "one".
    answer: Structural[str]
    account: Structural[str]
    #: The day of the row, or "" where it is no longer held.
    day: Structural[str]
    #: The day the answer was given.
    answered_on: Structural[str]
    #: What the undo names: the flagged row for "two", and for "one" the surviving row.
    flag_id: Structural[str]
    #: For "one", the row that was joined into the survivor; "" for "two".
    other_id: Structural[str]


@dataclass(frozen=True)
class FlagQueue:
    cards: Structural[tuple[FlagCard, ...]]
    #: Flags the evidence has already answered, which are counted and not listed.
    settled: Structural[int]
    answered: Structural[tuple[AnsweredLine, ...]]


def _sentence_case(text: str) -> str:
    return text[:1].upper() + text[1:]


def _days_phrase(days: int) -> str:
    if days == 0:
        return "on the same day"
    return f"{days} day{'' if days == 1 else 's'} apart"


def _words(sources: set[str]) -> str:
    names = sorted(source_word(s) for s in sources)
    if len(names) <= 2:
        return " and ".join(names)
    return ", ".join(names[:-1]) + ", and " + names[-1]


def evidence_for(
    flagged: set[str],
    neighbour: set[str],
    together: set[str],
    flagged_ids: dict[str, set[str]],
    neighbour_ids: dict[str, set[str]],
    days_apart: int,
) -> tuple[Evidence, ...]:
    """What the sightings say of two rows, either way. Source names and counts only.

    `together` is the sources that listed both rows in one response (a file or a payload); a
    source whose ids prove the pair is two payments never reaches here, so it is a source that
    names no id, which lists each payment once per file. The ids are those each source stated.
    """
    found: list[Evidence] = []
    for source in sorted(together):
        found.append(
            Evidence(
                "two",
                f"Both were listed by {source_word(source)} in one response, which lists a "
                "payment once.",
            )
        )
    shared = flagged & neighbour
    if not shared:
        found.append(
            Evidence(
                "one",
                f"One is listed only by {_words(flagged)} and the other only by "
                f"{_words(neighbour)}, {_days_phrase(days_apart)}: this is the pattern of one "
                "payment seen twice.",
            )
        )
    for source in sorted(shared - together):
        gave = flagged_ids.get(source, set()), neighbour_ids.get(source, set())
        if source in AGGREGATORS and all(gave):
            found.append(
                Evidence("two", f"{_sentence_case(source_word(source))} gave them different ids.")
            )
            found.append(
                Evidence(
                    "one",
                    f"{_sentence_case(source_word(source))} can report one payment again under "
                    "a new id, and "
                    "no response lists both.",
                )
            )
        else:
            found.append(
                Evidence(
                    "one",
                    f"{_sentence_case(source_word(source))} listed each in a response of its "
                    "own, and "
                    "overlapping responses list one payment twice.",
                )
            )
    return tuple(found)


def settle_evidence(gap: BalanceGap | None) -> tuple[Evidence, ...]:
    """The known balance whose absence stopped the balance proof, as one line of dates and no
    figure, or nothing where the proof was not tried or was tried and the balances disagreed.
    What the proof needs is on `ingest.finishers.FlagClass.BALANCES_NEED_BOTH`."""
    if gap is None:
        return ()
    day = gap.day.isoformat()
    if gap.kind == GAP_BEFORE:
        said = f"No known balance before {day}: a statement covering it would settle this."
    elif gap.kind == GAP_AFTER:
        said = f"No known balance after {day} yet: the next statement will settle this."
    else:
        said = f"Only one known balance ({day}): an earlier statement would settle this."
    return (Evidence(VERDICT_SETTLE, said),)


_PROOF_SENTENCES = {
    FlagClass.LISTED_TOGETHER: (
        "One response listed both under different ids, which is the source saying two "
        "payments."
    ),
    FlagClass.IDS_KEPT_FOR_LIFE: (
        "The bank's own feed gave them different ids, and it names a payment by one id for "
        "life: two payments."
    ),
}


def _sighting_sets(
    store: Store, entity_id: str
) -> tuple[set[str], dict[str, set[str]], set[tuple[str, str]]]:
    """The sources that sighted a row, the ids each stated, and its (source, response) pairs."""
    sources: set[str] = set()
    ids: dict[str, set[str]] = {}
    responses: set[tuple[str, str]] = set()
    for row in store.connection.execute(
        "SELECT source, source_id, artefact_digest FROM transaction_sources WHERE entity_id = ?",
        (entity_id,),
    ):
        source = str(row["source"])
        sources.add(source)
        if row["source_id"]:
            ids.setdefault(source, set()).add(str(row["source_id"]))
        responses.add((source, str(row["artefact_digest"])))
    return sources, ids, responses


def fingerprint_of(store: Store, flag_id: str, neighbour_ids: list[str]) -> str:
    """A hash of what a card shows: the flag, its row, its neighbours, and how each was listed.

    Any change to those rows, their sightings, or the set of neighbours changes it. It is built
    from the stored rows, digests, and ids, none of which a page prints, and it is not reversible
    to a value because the digests are of whole payloads.
    """
    parts: list[Any] = []
    flag = store.connection.execute(
        "SELECT reason, created_at, resolved_at FROM review_queue WHERE entity_id = ?",
        (flag_id,),
    ).fetchone()
    parts.append(
        None if flag is None else [flag["reason"], flag["created_at"], flag["resolved_at"]]
    )
    for entity_id in [flag_id, *sorted(neighbour_ids)]:
        row = store.connection.execute(
            "SELECT account_id, amount_minor, value_date, status FROM transactions "
            "WHERE entity_id = ?",
            (entity_id,),
        ).fetchone()
        sightings = sorted(
            tuple(map(str, r))
            for r in store.connection.execute(
                "SELECT source, source_id, artefact_digest FROM transaction_sources "
                "WHERE entity_id = ?",
                (entity_id,),
            )
        )
        parts.append([entity_id, None if row is None else [*map(str, tuple(row))], sightings])
    return hashlib.sha256(json.dumps(parts, sort_keys=True).encode()).hexdigest()[:24]


def _row_view(
    store: Store,
    entity_id: str,
    account: str,
    views: dict[str, list[Any]],
) -> RowView:
    from ..core.money import format_amount

    row = store.connection.execute(
        "SELECT amount_minor, value_date, status, description, currency FROM transactions "
        "WHERE entity_id = ?",
        (entity_id,),
    ).fetchone()
    lines = tuple(
        SourceLine(source_word(str(view.source)), how_words(view))
        for view in views.get(entity_id, [])
    )
    return RowView(
        entity_id=entity_id,
        day=str(row["value_date"]),
        status=str(row["status"]),
        sources=lines,
        amount=format_amount(int(row["amount_minor"]), currency=str(row["currency"] or "GBP")),
        description=str(row["description"]),
    )


def build_queue(
    store: Store, label_of: Callable[[str], str] = lambda ref: ref
) -> FlagQueue:
    """Every open flag still a real question as a card; the rest counted, not listed."""
    assessments = assess_flags(store)
    flags = {str(f["entity_id"]): f for f in store.review_queue()}
    settled = sum(1 for a in assessments.values() if a.flag_class is not FlagClass.OPEN)
    details: dict[str, dict[str, list[Any]]] = {}

    def views_of(account: str) -> dict[str, list[Any]]:
        if account not in details:
            details[account] = {
                entity: list(sighting_views(items))
                for entity, items in store.sighting_details(account).items()
            }
        return details[account]

    cards: list[FlagCard] = []
    for entity_id, assessment in assessments.items():
        if assessment.flag_class is not FlagClass.OPEN:
            continue
        account = assessment.account
        views = views_of(account)
        flagged_row = store.connection.execute(
            "SELECT amount_minor FROM transactions WHERE entity_id = ?", (entity_id,)
        ).fetchone()
        flagged = _row_view(store, entity_id, account, views)
        f_sources, f_ids, f_responses = _sighting_sets(store, entity_id)
        neighbours: list[NeighbourView] = []
        ids = []
        for neighbour_id, _source in live_neighbours(store, entity_id):
            ids.append(neighbour_id)
            row = _row_view(store, neighbour_id, account, views)
            n_sources, n_ids, n_responses = _sighting_sets(store, neighbour_id)
            apart = abs(date.fromisoformat(row.day) - date.fromisoformat(flagged.day)).days
            together = {s for s, d in f_responses & n_responses if d}
            proof = neighbour_proof(store, entity_id, neighbour_id)
            neighbours.append(
                NeighbourView(
                    row=row,
                    days_apart=apart,
                    proof="" if proof is None else _PROOF_SENTENCES[proof],
                    says=(
                        *evidence_for(f_sources, n_sources, together, f_ids, n_ids, apart),
                        *(() if proof is not None else settle_evidence(assessment.balance_gap)),
                    ),
                )
            )
        reason = str(flags[entity_id]["reason"])
        doubt = ""
        if reason.endswith(EXACT_RULE_DOUBT):
            doubt = reason[: -len(EXACT_RULE_DOUBT)].strip()
        cards.append(
            FlagCard(
                flag_id=entity_id,
                fingerprint=fingerprint_of(store, entity_id, ids),
                account=label_of(account),
                ref=account,
                direction="out" if int(flagged_row["amount_minor"]) < 0 else "in",
                flagged=flagged,
                neighbours=tuple(neighbours),
                doubt=doubt,
            )
        )
    cards.sort(key=lambda c: (c.account, c.flagged.day, c.flag_id))
    return FlagQueue(tuple(cards), settled, answered_lines(store, label_of))


# ---------------------------------------------------------------------------
# What was answered
# ---------------------------------------------------------------------------


def _record(store: Store, entity_id: str, kind: str) -> Any:
    row = store.connection.execute(
        "SELECT value FROM annotations WHERE entity_id = ? AND kind = ?", (entity_id, kind)
    ).fetchone()
    if row is None:
        return None
    try:
        return json.loads(str(row["value"]))
    except ValueError:
        return None


def _write_record(store: Store, entity_id: str, kind: str, value: Any, now: str) -> None:
    store.connection.execute(
        "INSERT INTO annotations (entity_id, kind, value, provenance, annotated_at) "
        "VALUES (?, ?, ?, ?, ?) "
        "ON CONFLICT(entity_id, kind) DO UPDATE SET value = excluded.value, "
        "provenance = excluded.provenance, annotated_at = excluded.annotated_at",
        (entity_id, kind, json.dumps(value, sort_keys=True), HUMAN, now),
    )


def _stamp(now: datetime | None) -> str:
    return (now or datetime.now().astimezone()).isoformat()


def answered_lines(
    store: Store, label_of: Callable[[str], str] = lambda ref: ref
) -> tuple[AnsweredLine, ...]:
    """The last `ANSWERED_SHOWN` answers, newest first."""
    found: list[tuple[str, AnsweredLine]] = []
    for row in store.connection.execute(
        "SELECT entity_id, kind, value FROM annotations WHERE kind IN (?, ?)",
        (KIND_TWO, KIND_JOINED),
    ).fetchall():
        entity_id = str(row["entity_id"])
        try:
            value = json.loads(str(row["value"]))
        except ValueError:
            continue
        held_row = store.connection.execute(
            "SELECT account_id, value_date FROM transactions WHERE entity_id = ?", (entity_id,)
        ).fetchone()
        account = "" if held_row is None else label_of(str(held_row["account_id"]))
        day = "" if held_row is None else str(held_row["value_date"])
        entries = [value] if row["kind"] == KIND_TWO else list(value)
        for entry in entries:
            if not entry.get("held", True):
                continue
            at = str(entry.get("at", ""))
            found.append(
                (
                    at,
                    AnsweredLine(
                        answer="two" if row["kind"] == KIND_TWO else "one",
                        account=account,
                        day=day,
                        answered_on=at[:10],
                        flag_id=entity_id,
                        other_id=str(entry.get("gone", "")),
                    ),
                )
            )
    found.sort(key=lambda item: item[0], reverse=True)
    return tuple(line for _, line in found[:ANSWERED_SHOWN])


def open_count(store: Store) -> int:
    return len(store.review_queue())


# ---------------------------------------------------------------------------
# Answering
# ---------------------------------------------------------------------------

_STALE = (
    "Nothing was changed: what this card showed has changed since the page was drawn. "
    "Open the queue again and read it before answering."
)


@dataclass(frozen=True)
class Outcome:
    """What an answer did, as a sentence, and the account it was about."""

    sentence: str
    account: str
    #: What the undo button on the result page names.
    answer: str
    flag_id: str
    other_id: str


def _checked_card(store: Store, flag_id: str, fingerprint: str) -> tuple[str, list[str]]:
    """The flag's account and neighbours, once the flag is open and the fingerprint holds."""
    if not flag_id or not fingerprint:
        raise FlagRefused("Nothing was changed: the answer did not say which flag it was for.")
    open_flag = store.connection.execute(
        "SELECT 1 FROM review_queue WHERE entity_id = ? AND resolved_at IS NULL", (flag_id,)
    ).fetchone()
    if open_flag is None:
        raise FlagRefused(
            "Nothing was changed: there is no open flag by that name. It may have been "
            "answered already, or settled by a rebuild."
        )
    row = store.connection.execute(
        "SELECT account_id FROM transactions WHERE entity_id = ?", (flag_id,)
    ).fetchone()
    if row is None:
        raise FlagRefused(_STALE)
    neighbours = [n for n, _ in live_neighbours(store, flag_id)]
    if fingerprint_of(store, flag_id, neighbours) != fingerprint:
        raise FlagRefused(_STALE)
    return str(row["account_id"]), neighbours


def answer_two_payments(
    store: Store, flag_id: str, fingerprint: str, *, now: datetime | None = None
) -> Outcome:
    """Say these are two payments: the flag closes, both rows stand, and a rebuild keeps it so."""
    account, neighbours = _checked_card(store, flag_id, fingerprint)
    stamp = _stamp(now)
    _write_record(store, flag_id, KIND_TWO, {"at": stamp, "others": sorted(neighbours)}, stamp)
    store.connection.execute(
        "UPDATE review_queue SET resolved_at = ? WHERE entity_id = ?", (stamp, flag_id)
    )
    store.connection.commit()
    return Outcome(
        "Kept as two payments: both stay, and the question will not be asked again."
        if neighbours
        else "Kept as it is: the question will not be asked again.",
        account,
        "two",
        flag_id,
        "",
    )


def join_refusal(store: Store, flag_id: str, neighbour_id: str) -> str:
    """A sentence saying why these two rows cannot be joined into one, or "" where they can."""
    rows = {
        entity: store.connection.execute(
            "SELECT account_id, value_date, status, tier FROM transactions WHERE entity_id = ?",
            (entity,),
        ).fetchone()
        for entity in (flag_id, neighbour_id)
    }
    if any(row is None for row in rows.values()):
        return _STALE
    flagged, other = rows[flag_id], rows[neighbour_id]
    if str(flagged["account_id"]) != str(other["account_id"]):
        return "These two are in different accounts, so they cannot be one payment."
    history = {s.value for s in TransactionStatus if s.is_history}
    if str(flagged["status"]) in history or str(other["status"]) in history:
        return "One of them is void or folded already, so there is nothing to join."
    if SourceTier.MANUAL.value in (str(flagged["tier"]), str(other["tier"])):
        return (
            f'One of them is a typed entry. Use "{REMOVE_TYPED_TRANSACTION}" on the account '
            "page if it is wrong, and the other stands."
        )
    proof = neighbour_proof(store, flag_id, neighbour_id)
    if proof is not None:
        return (
            "They were kept as two payments by the evidence, which a person's answer does not "
            "override: " + _PROOF_SENTENCES[proof]
        )
    paired = store.connection.execute(
        "SELECT 1 FROM transfer_pairs WHERE debit_entity_id IN (?, ?) "
        "OR credit_entity_id IN (?, ?)",
        (flag_id, neighbour_id, flag_id, neighbour_id),
    ).fetchone()
    if paired is not None:
        return "One of them is a leg of a confirmed transfer, which is never joined."
    protection = store.protection_record(str(flagged["account_id"]))
    if protection is not None:
        through = str(protection["through"])
        days = (str(flagged["value_date"]), str(other["value_date"]))
        if any(day <= through for day in days):
            return (
                f"This account is protected through {through}, and joining these would change "
                f'a protected day. Use "{REMOVE_PROTECTION}" on the account page first, answer '
                "here, then protect it again."
            )
    return ""


def answer_one_payment(
    store: Store,
    flag_id: str,
    neighbour_id: str,
    fingerprint: str,
    *,
    now: datetime | None = None,
) -> Outcome:
    """Say these are one payment: the two rows become one, keeping every sighting."""
    account, neighbours = _checked_card(store, flag_id, fingerprint)
    if not neighbour_id or neighbour_id not in neighbours:
        raise FlagRefused(
            "Nothing was changed: that row is not one this flag was weighed against."
        )
    refusal = join_refusal(store, flag_id, neighbour_id)
    if refusal:
        raise FlagRefused("Nothing was changed. " + refusal)
    stamp = _stamp(now)
    kept, gone = _survivor(store, flag_id, neighbour_id)
    _join(store, kept, gone, flag_id)
    held = _record(store, kept, KIND_JOINED) or []
    held.append({"gone": gone, "flag": flag_id, "at": stamp, "held": True})
    _write_record(store, kept, KIND_JOINED, held, stamp)
    store.connection.commit()
    return Outcome(
        "Joined into one payment: every sighting of both is kept on the one transaction.",
        account,
        "one",
        kept,
        gone,
    )


def _survivor(store: Store, first: str, second: str) -> tuple[str, str]:
    """The row kept is the one whose artefact arrived first, the lower id on a tie, as the
    exact rules choose, because it has been annotated, protected, and sent on for longest."""
    furthest = (datetime.max.replace(tzinfo=UTC), 0)

    def arrived(entity_id: str) -> tuple[tuple[datetime, int], str]:
        return (store.arrival_of(entity_id) or furthest, entity_id)

    kept, gone = sorted((first, second), key=arrived)
    return kept, gone


def _join(store: Store, kept: str, gone: str, flag_id: str) -> None:
    """The flag is deleted first: left to the join it would move to the surviving row and stand
    as a question about a pair that no longer exists."""
    store.connection.execute("DELETE FROM review_queue WHERE entity_id = ?", (flag_id,))
    store.absorb_entity(kept, gone)


def replay_joins(store: Store) -> int:
    """Repeat each join a person answered, over rows a rebuild has just derived.

    Runs after the artefacts are replayed and before the flags are settled. A join whose rows
    are not both held now (the rules have since joined them, or the evidence is gone) is left
    alone and counts for nothing. Returns how many rows were joined.
    """
    joined = 0
    for row in store.connection.execute(
        "SELECT entity_id, value FROM annotations WHERE kind = ?", (KIND_JOINED,)
    ).fetchall():
        kept = str(row["entity_id"])
        try:
            entries = json.loads(str(row["value"]))
        except ValueError:
            continue
        for entry in entries:
            if not entry.get("held", True):
                continue
            gone = str(entry.get("gone", ""))
            present = {
                str(r["entity_id"])
                for r in store.connection.execute(
                    "SELECT entity_id FROM transactions WHERE entity_id IN (?, ?)", (kept, gone)
                )
            }
            if present != {kept, gone}:
                continue
            _join(store, kept, gone, str(entry.get("flag", "")))
            joined += 1
    store.connection.commit()
    return joined


# ---------------------------------------------------------------------------
# Undoing
# ---------------------------------------------------------------------------


def undo(store: Store, answer: str, flag_id: str, other_id: str) -> Outcome:
    """Withdraw an answer. Reopens a "two payments" flag; withdraws a join's record, which
    leaves the rows joined until the next rebuild."""
    if answer == "two":
        if _record(store, flag_id, KIND_TWO) is None:
            raise FlagRefused("Nothing was changed: no such answer is recorded.")
        store.connection.execute(
            "DELETE FROM annotations WHERE entity_id = ? AND kind = ?", (flag_id, KIND_TWO)
        )
        store.connection.execute(
            "UPDATE review_queue SET resolved_at = NULL WHERE entity_id = ?", (flag_id,)
        )
        store.connection.commit()
        account = store.connection.execute(
            "SELECT account_id FROM transactions WHERE entity_id = ?", (flag_id,)
        ).fetchone()
        return Outcome(
            "Answer withdrawn: the flag is open again.",
            "" if account is None else str(account["account_id"]),
            "",
            flag_id,
            "",
        )
    if answer == "one":
        entries = _record(store, flag_id, KIND_JOINED) or []
        target = next(
            (e for e in entries if e.get("gone") == other_id and e.get("held", True)), None
        )
        if target is None:
            raise FlagRefused("Nothing was changed: no such answer is recorded.")
        target["held"] = False
        stamp = _stamp(None)
        _write_record(store, flag_id, KIND_JOINED, entries, stamp)
        store.connection.commit()
        account = store.connection.execute(
            "SELECT account_id FROM transactions WHERE entity_id = ?", (flag_id,)
        ).fetchone()
        return Outcome(
            "Answer withdrawn. The two rows stay joined until the next rebuild, which keeps "
            "them as two again and asks the question again.",
            "" if account is None else str(account["account_id"]),
            "",
            flag_id,
            "",
        )
    raise FlagRefused("Nothing was changed: that answer was not one of the two.")
