"""Run the suite as if it were some other date.

Loaded on demand (`-p conftest_timetravel`) rather than automatically, because it
answers a different question from the ordinary suite: not "is this correct" but
"does this stay correct as the calendar moves". Two ways to say where to go:

    OBDI_TRAVEL_DAYS=365                 an offset from now
    OBDI_TRAVEL_TO=2028-02-29T12:00:00Z  an absolute destination

Both exist because they reach different faults. An OFFSET is the right tool for
things that expire - a fixture ages out of a window after so many days, whatever
the date happens to be. It cannot land on a calendar FEATURE, though: no offset
reliably arrives on a leap day, a month end, a clock change or a year boundary,
because where it lands depends on when it was launched. Those need an absolute
destination, which is also more reproducible - the same leg means the same instant
every week, so a failure is a failure and not a coincidence of the launch date.

What both hunt is a fixture carrying a literal date that is measured against a
window starting at the current time. Such a test passes until the day the fixture
ages out, then fails on a commit that was green hours earlier - which reads as
infrastructure flakiness, and re-running "fixes" it right up until it does not.
This has happened once here, to a seven-day filter.

A literal date is not itself the defect: compared against another literal it is
stable forever, and clearer than anything computed. Only the comparison against NOW
makes it perishable, and that is not visible by reading either side alone.

Ticking rather than frozen. A stopped clock breaks anything that measures a
duration, which would bury the class being hunted under failures that say nothing
about it.
"""

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from os import environ

import pytest
import time_machine

#: Where the plugin actually went, published so the probe's self-check can assert
#: the clock moved WITHOUT recomputing this logic. A second implementation of "where
#: should we be" is a second thing to get wrong, and it would agree with the first
#: one precisely when both are wrong.
DESTINATION_VAR = "OBDI_TRAVEL_DESTINATION"


def _destination() -> datetime | None:
    absolute = environ.get("OBDI_TRAVEL_TO", "").strip()
    if absolute:
        parsed = datetime.fromisoformat(absolute.replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
    days = int(environ.get("OBDI_TRAVEL_DAYS", "").strip() or "0")
    return datetime.now(UTC) + timedelta(days=days) if days else None


@pytest.fixture(autouse=True, scope="session")
def _travel() -> Iterator[None]:
    destination = _destination()
    if destination is None:
        yield
        return
    environ[DESTINATION_VAR] = destination.isoformat()
    with time_machine.travel(destination, tick=True):
        yield
