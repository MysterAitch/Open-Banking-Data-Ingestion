"""Confirming a recurring series as a commitment, and finding it again on a later read.

The detector (`recurring`) measures and keeps nothing; a commitment (`ingest.commitment_records`)
is the owner's word that a series is a recurring payment, with the terms it ran on. This module
is the join between them and holds the presses that make it.

A COMMITMENT FINDS ITS SERIES by who is paid and where the money leaves: the entity the payee was
gathered under, or the name the detector called the payee by when it was confirmed, whichever the
series carries now (`Series.party_entity`, `Series.name_keys`); and the account, direction,
currency, and cadence of its current window. Gathering the name into an entity afterwards
therefore changes nothing, and neither does a name that stops being printed. Two commitments to
one payee (two products) are told apart by amount: each series goes to the commitment whose
current amount is nearest its own, nearest pair first, and a commitment takes one series at most.

A PRESS NAMES ITS SERIES BY `series_refs`, which is made from what the masked page already shows
(the account, the cadence and day, the first sighting) and never from the payee or an amount, so
that a masked page can carry the press without carrying a thing to be unmasked. A press arriving
for a series that has since changed shape is refused rather than applied to the wrong one.
"""

from __future__ import annotations

import hashlib
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date

from ..ingest.commitment_records import (
    Commitment,
    CommitmentRefused,
    Dismissal,
    Window,
    WindowTerms,
)
from ..ingest.store import Store
from .recurring import CHANGE_PERCENT, Series, tolerance_days

#: How a Confirm press settles the open question of a stopped series: it was a commitment that
#: ended at its last occurrence, or one that has gone missing and is still expected.
CONFIRM = "confirm"
ENDED = "ended"
MISSING = "missing"
HOWS = (CONFIRM, ENDED, MISSING)


#: The presses the Recurring page makes, said once here and by name everywhere else.
ACT_CONFIRM = "confirm"
ACT_PRICE = "price"
ACT_DISMISS = "dismiss"
ACT_RESTORE = "restore"


@dataclass(frozen=True)
class PriceOffer:
    """A new window the series' newest payments ask for: the amount they are at, from the first
    day of the run of payments at it."""

    amount_minor: int
    from_day: date


@dataclass(frozen=True)
class Confirmation:
    """The commitment a series belongs to, its current window, and the new price to offer."""

    commitment: Commitment
    window: Window | None
    offer: PriceOffer | None = None


def _structure(series: Series) -> str:
    """What identifies a series on the masked page: nothing of the payee and nothing of the amount.

    The amount is left out because a hash that held it could be run against every plausible amount
    by anyone who has the page, which would unmask it; the first sighting is shown by the page
    already ("over 14 months", "last 2026-07-03")."""
    return "|".join(
        (
            series.account,
            series.direction,
            series.currency,
            series.cadence,
            str(series.usual_day),
            str(series.usual_month),
            str(series.weekday),
            series.kind,
            series.first_seen.isoformat(),
        )
    )


def series_refs(found: Sequence[Series]) -> list[str]:
    """An opaque reference for each series, in step with `found`, that a press can carry.

    Series with the same structure (two prices to one payee that began the same day) are told
    apart by their order of amount, which reveals the order and not the amounts."""
    groups: dict[str, list[int]] = defaultdict(list)
    for index, series in enumerate(found):
        groups[_structure(series)].append(index)
    refs = [""] * len(found)
    for structure, members in groups.items():
        digest = hashlib.sha256(structure.encode("utf-8")).hexdigest()[:16]
        ordered = sorted(members, key=lambda i: (found[i].usual_minor, found[i].shape, i))
        for ordinal, index in enumerate(ordered):
            refs[index] = f"{digest}.{ordinal}"
    return refs


def series_index(found: Sequence[Series], ref: str) -> int:
    """The position in `found` of the series a press named, or a refusal in the words the page
    shows."""
    for index, mine in enumerate(series_refs(found)):
        if mine == ref:
            return index
    raise CommitmentRefused(
        "That payment is not in the list any more, or has changed since the page was drawn. "
        "Reload the page and press again."
    )


def same_payee(entity_id: int | None, name_key: str, series: Series) -> bool:
    """Whether a series is paid to the payee a commitment (or a dismissal) was made about."""
    if entity_id is not None and series.party_entity == entity_id:
        return True
    return bool(name_key) and name_key in series.name_keys


def _distance(series: Series, window: Window) -> float:
    """How far the series' amount is from the window's, as a fraction of the window's: the nearer
    of its usual amount and its latest (a price rise leaves the usual behind for a while)."""
    nearest = min(
        abs(series.usual_minor - window.amount_minor),
        abs(series.latest_minor - window.amount_minor),
    )
    return nearest / window.amount_minor


def match_series(
    found: Sequence[Series], commitments: Sequence[Commitment]
) -> list[Confirmation | None]:
    """The commitment each series belongs to, in step with `found`; None for one that is none.

    A pair is a candidate when the commitment's current window is in the series' currency and
    cadence, it leaves the same account in the same direction, and the payee is the same
    (`same_payee`). The nearest pairs by amount are taken first, so that the series and the
    commitment of two products to one payee meet their own match, and each side is taken once.
    """
    candidates: list[tuple[float, int, int]] = []
    for si, series in enumerate(found):
        for ci, commitment in enumerate(commitments):
            window = commitment.current
            if (
                window is None
                or commitment.account != series.account
                or commitment.direction != series.direction
                or window.currency != series.currency
                or window.cadence != series.cadence
                or not same_payee(commitment.entity_id, commitment.name_key, series)
            ):
                continue
            candidates.append((_distance(series, window), si, ci))
    candidates.sort()
    matched: list[Confirmation | None] = [None] * len(found)
    used: set[int] = set()
    for _nearness, si, ci in candidates:
        if matched[si] is None and ci not in used:
            commitment = commitments[ci]
            matched[si] = Confirmation(
                commitment, commitment.current, price_offer(found[si], commitment)
            )
            used.add(ci)
    return matched


def match_dismissals(
    found: Sequence[Series], dismissals: Sequence[Dismissal]
) -> list[Dismissal | None]:
    """The dismissal each series falls under, in step with `found`; None for one that is none.

    A dismissal covers every series of the same payee (`same_payee`), account, direction, and
    cadence, whatever the amount: it answers "is this payee's rhythm a commitment", and two prices
    to one payee on one rhythm are not separately asked about."""
    return [
        next(
            (
                d
                for d in dismissals
                if d.account == series.account
                and d.direction == series.direction
                and d.cadence == series.cadence
                and same_payee(d.entity_id, d.name_key, series)
            ),
            None,
        )
        for series in found
    ]


@dataclass(frozen=True)
class Tally:
    """How the series found have been answered. The measured precision of the detector is
    `dismissed` over `found`: the share the owner said are not commitments at all."""

    found: int
    confirmed: int
    dismissed: int

    @property
    def to_look_at(self) -> int:
        """Series not yet answered either way."""
        return self.found - self.confirmed - self.dismissed

    @property
    def answered(self) -> bool:
        """Whether any series has been answered, which is when the page says so."""
        return bool(self.confirmed or self.dismissed)


def tally_of(
    confirmations: Sequence[Confirmation | None], dismissals: Sequence[Dismissal | None]
) -> Tally:
    """Count the series by answer. A confirmed series is counted as confirmed even where a
    dismissal also covers it (the later answer is the commitment: a dismissal is only ever
    made on a series not yet confirmed)."""
    confirmed = sum(c is not None for c in confirmations)
    dismissed = sum(
        d is not None and c is None for c, d in zip(confirmations, dismissals, strict=True)
    )
    return Tally(len(confirmations), confirmed, dismissed)


def price_offer(series: Series, commitment: Commitment) -> PriceOffer | None:
    """The new window to offer where the series' newest payments are at a price the open window
    does not hold, or None.

    The detector's `changed` is not what is asked: it compares the latest amount with the
    series' USUAL amount, which becomes the new price itself once enough months have passed, so
    it stops saying so while the window still holds the old one. The window is what a price is
    compared with, by the detector's own tolerance (`CHANGE_PERCENT`).

    Nothing is offered for a window already closed (the commitment ended), a series that varies
    every time (`steady` is false, so there is no price to move to), a stopped series (its last
    payment is old news), or a run of newest payments that began no later than the window did
    (the window was written after them, and is the owner's).
    """
    window = commitment.current
    if window is None or window.to_day is not None or series.stopped or not series.steady:
        return None
    if abs(series.latest_minor - window.amount_minor) * 100 <= CHANGE_PERCENT * window.amount_minor:
        return None
    if series.latest_from is None or series.latest_from <= window.from_day:
        return None
    return PriceOffer(series.latest_minor, series.latest_from)


def terms_of(series: Series, basis: str) -> WindowTerms:
    """The terms a series' history gives a window: its usual amount, cadence, and usual day (the
    weekday for a cadence counted in days), and the tolerance the fit allowed."""
    return WindowTerms(
        amount_minor=series.usual_minor,
        currency=series.currency,
        cadence=series.cadence,
        usual_day=series.weekday if series.weekday is not None else series.usual_day,
        usual_month=series.usual_month,
        tolerance_days=tolerance_days(series.cadence),
        basis=basis,
    )


def confirm(
    store: Store, found: Sequence[Series], ref: str, how: str, *, today: date
) -> str:
    """Make the series named by `ref` a commitment, and commit; returns the name it was given.

    The first window is the series' history: its usual amount, cadence, and day, from the day it
    was first seen. `how` is `CONFIRM` (open), `ENDED` (closed at the last occurrence), or
    `MISSING` (open, so that a later build can say it is overdue). Refused where `how` is none of
    these, the series is not in `found`, or it is a commitment already (a second press on a page
    that has been answered).
    """
    if how not in HOWS:
        raise CommitmentRefused("Confirm it, or say it ended, or say it is missing.")
    index = series_index(found, ref)
    series = found[index]
    if match_series(found, store.commitments())[index] is not None:
        raise CommitmentRefused("That payment is already a commitment.")
    name = series.shape or series.label
    keys = sorted(series.name_keys)
    store.declare_commitment(
        name,
        kind=series.kind,
        account=series.account,
        direction=series.direction,
        entity_id=series.party_entity or None,
        name_key=keys[0] if keys else "",
        from_day=series.first_seen,
        to_day=series.last_seen if how == ENDED else None,
        terms=terms_of(series, f"confirmed from the series on {today.isoformat()}"),
    )
    return name


def change_price(store: Store, found: Sequence[Series], ref: str, *, today: date) -> None:
    """Close the confirmed series' open window the day before its newest price began and open
    another at that price from that day, and commit. The offer is worked out again here from
    what is held, never taken from the form, so a press made on a page that has gone stale can
    only record what is true now. Refused where the series is not a commitment or has no new
    price to record, which includes a second press on one answered.
    """
    index = series_index(found, ref)
    match = match_series(found, store.commitments())[index]
    if match is None or match.offer is None or match.window is None:
        raise CommitmentRefused("There is no new price to record for that payment.")
    old = match.window
    store.change_commitment_window(
        match.commitment.id,
        match.offer.from_day,
        WindowTerms(
            amount_minor=match.offer.amount_minor,
            currency=old.currency,
            cadence=old.cadence,
            usual_day=old.usual_day,
            usual_month=old.usual_month,
            tolerance_days=old.tolerance_days,
            basis=(
                f"price changed from {match.offer.from_day.isoformat()}, "
                f"confirmed on {today.isoformat()}"
            ),
        ),
    )


def dismiss(store: Store, found: Sequence[Series], ref: str) -> None:
    """Keep that the series named by `ref` is not a commitment, and commit. Refused where it is a
    commitment already (it is not set aside behind the owner's back) or is set aside already."""
    index = series_index(found, ref)
    series = found[index]
    if match_series(found, store.commitments())[index] is not None:
        raise CommitmentRefused("That payment is already a commitment.")
    keys = sorted(series.name_keys)
    store.dismiss_series(
        entity_id=series.party_entity or None,
        name_key=keys[0] if keys else "",
        account=series.account,
        direction=series.direction,
        cadence=series.cadence,
    )


def restore(store: Store, found: Sequence[Series], ref: str) -> None:
    """Put the dismissed series named by `ref` back among those to look at, and commit. Refused
    where it is not dismissed."""
    index = series_index(found, ref)
    dismissal = match_dismissals(found, store.dismissals())[index]
    if dismissal is None:
        raise CommitmentRefused("That payment is not set aside; it may have been put back.")
    store.restore_dismissal(dismissal.id)


def _field(form: dict[str, list[str]], name: str) -> str:
    return (form.get(name) or [""])[0].strip()


#: What each way of confirming says it did. No payee and no amount: the same sentence leads a
#: masked page.
_SAID = {
    CONFIRM: "Confirmed as a commitment.",
    ENDED: "Confirmed as a commitment that has ended.",
    MISSING: "Confirmed as a commitment that is missing, so it is overdue until it is paid.",
}


def apply_press(
    store: Store,
    found: Sequence[Series],
    action: str,
    form: dict[str, list[str]],
    *,
    today: date,
) -> str:
    """Do what one press on the Recurring page asks, and say what was done in a sentence that
    holds no payee and no amount. Refused with `CommitmentRefused` for an action that is not
    one, and for whatever the action itself refuses."""
    ref = _field(form, "ref")
    if action == ACT_CONFIRM:
        how = _field(form, "how") or CONFIRM
        confirm(store, found, ref, how, today=today)
        return _SAID[how]
    if action == ACT_PRICE:
        change_price(store, found, ref, today=today)
        return "New price recorded; the earlier price is kept as it was."
    if action == ACT_DISMISS:
        dismiss(store, found, ref)
        return "Set aside as not a commitment."
    if action == ACT_RESTORE:
        restore(store, found, ref)
        return "Put back among the payments to look at."
    raise CommitmentRefused("That is not something this page does.")
