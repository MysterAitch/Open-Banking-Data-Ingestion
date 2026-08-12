"""Run the suite as if it were a chosen number of days from now.

Loaded on demand (`-p conftest_timetravel`) rather than automatically, because it
answers a different question from the ordinary suite: not "is this correct" but
"does this stay correct as the calendar moves". Set OBDI_TRAVEL_DAYS to choose the
horizon; unset or zero leaves the clock alone.

What it hunts is a fixture carrying a literal date that is measured against a
window starting at the current time. Such a test passes until the day the fixture
ages out of the window, then fails on a commit that was green hours earlier -
which reads as infrastructure flakiness, and re-running "fixes" it right up until
it does not. This has happened once here, to a seven-day filter.

A literal date is not itself the defect: compared against another literal it is
stable forever, and clearer than anything computed. Only the comparison against
NOW makes it perishable, and that is not visible by reading either side alone.

Ticking rather than frozen. A stopped clock breaks anything that measures a
duration, which would bury the class being hunted under failures that say nothing
about it.
"""

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from os import environ

import pytest
import time_machine


@pytest.fixture(autouse=True, scope="session")
def _travel() -> Iterator[None]:
    days = int(environ.get("OBDI_TRAVEL_DAYS", "0"))
    if not days:
        yield
        return
    with time_machine.travel(datetime.now(UTC) + timedelta(days=days), tick=True):
        yield
