"""Account facts as dated observations, projected rather than assigned.

A statement does not state what an account IS; it states what was true when
it was issued - this rate, this limit, this promotional period and when it
reverts. Storing those as account properties would make the answer depend
on which PDF happened to be imported last, so a July statement filed ahead
of a February one would erase a promotional window expiring in March,
silently and with no way back.

So nothing here assigns. Observations accumulate, and the view is a pure
function of the set - which makes import order irrelevant by construction
rather than by care, the same guarantee transactions already get from being
derived rather than mutated. Provenance decides ties the way it does
everywhere else: a fact a person declared outranks one a statement implied,
regardless of which is newer.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

#: Rank order borrowed wholesale from the annotation ladder, for the same
#: reason: what a person asserts is not up for revision by a machine.
_RANKS = {"rule": 1, "statement": 1, "model": 2, "human": 3}


@dataclass(frozen=True, order=True)
class Observation:
    """One dated reading of one account fact.

    `observed_at` is when the fact was WITNESSED (the statement's date);
    `window_from`/`window_to` are when the fact APPLIES, which is a
    different thing entirely - a July statement can witness a promotional
    rate that expires the following March.
    """

    account_id: str
    fact: str
    kind: str
    observed_at: date
    value: str
    window_from: date | None = None
    window_to: date | None = None
    source: str = ""
    provenance: str = "statement"

    @property
    def rank(self) -> int:
        return _RANKS.get(self.provenance.split(":", 1)[0], 0)

    def applies_on(self, day: date) -> bool:
        if self.window_from is not None and day < self.window_from:
            return False
        return not (self.window_to is not None and day > self.window_to)


def project(observations: list[Observation]) -> list[Observation]:
    """Every distinct fact-window on the record, in a stable order.

    One entry per (account, fact, kind, window): where several statements
    witness the same window, the newest reading wins, and a higher
    provenance wins over any reading however recent. Everything else is
    kept - an expired promotional rate is history, and history is evidence.
    """
    best: dict[tuple[str, str, str, date | None, date | None], Observation] = {}
    for observation in observations:
        key = (
            observation.account_id,
            observation.fact,
            observation.kind,
            observation.window_from,
            observation.window_to,
        )
        held = best.get(key)
        if held is None or (observation.rank, observation.observed_at) > (
            held.rank,
            held.observed_at,
        ):
            best[key] = observation
    return sorted(best.values())


def current_view(
    observations: list[Observation], *, on: date
) -> dict[str, str]:
    """What the account looks like on one day.

    A window that has not begun or has already ended describes some other
    day, not this one - so it is absent here while remaining on the record
    that `project` returns.
    """
    view: dict[str, tuple[int, date, str]] = {}
    for observation in project(observations):
        if not observation.applies_on(on):
            continue
        name = (
            f"{observation.fact}:{observation.kind}"
            if observation.kind
            else observation.fact
        )
        held = view.get(name)
        if held is None or (observation.rank, observation.observed_at) > held[:2]:
            view[name] = (observation.rank, observation.observed_at, observation.value)
    return {name: value for name, (_, _, value) in view.items()}
