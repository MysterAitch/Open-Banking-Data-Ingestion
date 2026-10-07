"""Where each recovered Space stands: declared, bound, and asked for.

A recovered Space is useful to a pull only when three things hold: it has an
account (declared), its category resolves to that account (bound), and the pull
has then asked for its own feed. Declaring alone leaves a Space that no pull
will ever fetch, so the states are kept apart and shown, and one press finishes
whatever is unfinished.

Nothing here writes. The press that acts on these states lives in `cli.py`,
beside the bind it must share with the pages.

A category bound to a DIFFERENT account is a disagreement, left alone for the
same reason `known_accounts` leaves a disagreeing parent alone: overwriting
either side would decide a question only a person can.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

from .accounts import AccountMap
from .space_windows import HistoryProgress, history_progress, span_of
from .spaces import HistoricalSpace, account_for
from .store import Store

PROVIDER = "starling"

UNBOUND = "unbound"
BOUND = "bound"
ELSEWHERE = "elsewhere"

#: Said once, wherever a press has bound a Space, because the press itself
#: fetches nothing and a person who stops reading here would otherwise wait for
#: history that nobody has asked for. There is no page that starts a pull, so
#: the sentence names the command and does not link to one.
WHAT_HAPPENS_NEXT = (
    "The next pull asks for each Space's history. The scheduled pull does that "
    "by itself; to ask at once, run obdi pull starling on the host (this app "
    "has no page that starts a pull)."
)


@dataclass(frozen=True)
class SpaceState:
    uid: str
    #: The canonical ref its recovered account is declared under.
    ref: str
    name: str
    declared: bool
    binding: str
    #: The account its category resolves to when that is not `ref`.
    bound_to: str
    #: Where its windowed history stands; None until it is declared and bound.
    history: HistoryProgress | None

    @property
    def disagrees(self) -> bool:
        return self.binding == ELSEWHERE

    @property
    def unfinished(self) -> bool:
        """Needs a declaration, a binding, or both - and is not in dispute."""
        if self.disagrees:
            return False
        return not self.declared or self.binding == UNBOUND

    def describe(self) -> str:
        """The state in words, with no figure in it."""
        if self.disagrees:
            return (
                f"{'declared' if self.declared else 'not declared'}; category "
                f"bound to {self.bound_to}, which disagrees - left alone"
            )
        if not self.declared:
            bound = "; category already bound" if self.binding == BOUND else ""
            return f"not declared{bound}"
        if self.binding == UNBOUND:
            return "declared, category not bound"
        return f"declared and bound; {self._feed_words()}"

    def _feed_words(self) -> str:
        history = self.history
        if history is None or (history.asked == 0 and history.too_long == 0):
            return "its own feed has not been asked for yet"
        return f"its own {history.describe()}"


@dataclass(frozen=True)
class SpacesPress:
    """What one press did, and what it left alone."""

    declared: tuple[str, ...]
    bound: tuple[str, ...]
    #: (ref, the account its category is bound to instead).
    disagreeing: tuple[tuple[str, str], ...]
    #: (ref, why): declared, but the bind was refused. Repaired by pressing again.
    failed: tuple[tuple[str, str], ...]

    @property
    def acted(self) -> bool:
        return bool(self.declared or self.bound or self.failed)

    def lines(self) -> list[str]:
        """One line per Space touched or left alone, in a fixed order."""
        out: list[str] = []
        failed = dict(self.failed)
        for ref in sorted(set(self.declared) | set(self.bound) | set(failed)):
            if ref in failed:
                out.append(f"{ref} - declared, but could not bind: {failed[ref]}")
            elif ref in self.declared and ref in self.bound:
                out.append(f"{ref} - declared and bound")
            elif ref in self.declared:
                out.append(f"{ref} - declared (its category was already bound)")
            else:
                out.append(f"{ref} - bound (already declared)")
        out += [
            f"{ref} - category bound to {other}, which disagrees - left alone"
            for ref, other in self.disagreeing
        ]
        return out

    def summary(self) -> str:
        done = (
            f"Declared {len(self.declared)} account{'' if len(self.declared) == 1 else 's'} "
            f"and bound {len(self.bound)} categor{'y' if len(self.bound) == 1 else 'ies'}."
        )
        return done if self.acted else NOTHING_TO_DO


NOTHING_TO_DO = "Nothing to do - no recovered Space is left to declare or bind."

RETRY_NOTE = (
    "A Space listed as declared but not bound stays declared; press again once "
    "the cause is dealt with and only those are retried."
)


def space_states(
    store: Store, account_map: AccountMap, found: Sequence[HistoricalSpace]
) -> list[SpaceState]:
    """The state of each recovered Space, in the order `found` gives them."""
    declared = {str(record.ref) for record in store.declared_accounts()}
    now = datetime.now(UTC)
    states: list[SpaceState] = []
    for space in found:
        ref = str(account_for(space).ref)
        resolved = str(account_map.resolve(PROVIDER, space.uid))
        if resolved == f"{PROVIDER}:{space.uid}":
            binding, bound_to = UNBOUND, ""
        elif resolved == ref:
            binding, bound_to = BOUND, ""
        else:
            binding, bound_to = ELSEWHERE, resolved
        history = (
            history_progress(
                store, (f"{PROVIDER}:{space.uid}", ref), span_of(space, now), now
            )
            if ref in declared and binding == BOUND
            else None
        )
        states.append(
            SpaceState(
                uid=space.uid,
                ref=ref,
                name=space.name,
                declared=ref in declared,
                binding=binding,
                bound_to=bound_to,
                history=history,
            )
        )
    return states
