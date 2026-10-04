"""The order raw artefacts arrived in, read from their stamps as instants.

A pull stamps its artefact in UTC and a file import in local time with an
offset, and the stamp is stored as text.
Compared as text, an import made at 11:00 UTC but written "12:00+01:00" sorts
after a pull made at 11:30 UTC, so a replay applied it after a pull it had
preceded.
Last writer wins on a merged row, so the rebuilt store could disagree with the
live one about which source's facts a row carries.

The same fault hid in the tests: a release passed every gate on a machine ahead
of UTC and failed its build on one at UTC, because there an import landed in
the same second as a feed always replayed last, so every "any arrival order"
test ran one order six times.

Ordering in SQL with julianday was rejected: it keeps milliseconds only, and
artefacts landed in the same instant would then be mis-ordered.
A stored stamp is never rewritten, because raw artefacts are immutable
evidence; the order is read from what the stamp means.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any


def arrival_instant(stamp: object) -> datetime:
    """The instant a stored `fetched_at` states; a stamp without an offset is UTC."""
    parsed = datetime.fromisoformat(str(stamp))
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def in_arrival_order(rows: Sequence[Any]) -> list[Any]:
    """Artefact rows (with `fetched_at` and `rowid`) in the order they arrived.

    Equal instants keep landing order, by rowid.
    One sort over the rows, so a rebuild's ordering stays near-linear.
    """
    return sorted(rows, key=lambda row: (arrival_instant(row["fetched_at"]), int(row["rowid"])))
