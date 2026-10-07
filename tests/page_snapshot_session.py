"""The pytest session `page_snapshot.py` runs: it walks the pages and writes or compares digests.

Not collected by an ordinary run (the name does not start with `test_`); `page_snapshot.py` names
it on the command line. It exists as a pytest module only because the invented stores and their
servers are fixtures.
"""

from __future__ import annotations

import itertools
import json
import os
import secrets
import time
from pathlib import Path

import pytest

from page_snapshot import FILE_VARIABLE, MODE_VARIABLE, differences, load, snapshot
from page_walk import (  # noqa: F401
    dispatcher_routes,
    household,
    household_pages,
    household_served,
    invented,
    served,
    walked_pages,
)


@pytest.fixture(scope="module", autouse=True)
def entry_ids_are_the_same_every_run():
    """A typed transaction's entry id is `secrets.token_hex(8)`, and the ledger prints it (a form
    field) and derives the row's id from it, so the ledger of the store that holds one differs
    on every run. The ids are handed out by a counter instead, so two runs over the same tree
    build the same store; no rule of the normaliser has to hide a figure-shaped id on a page.
    Patched on `secrets` itself so that the patch names no application module, which would break
    the day the module moves.
    """
    counter = itertools.count(1)

    def counted_token_hex(nbytes: int | None = None) -> str:
        return f"{next(counter):0{2 * (nbytes or 32)}x}"

    patch = pytest.MonkeyPatch()
    patch.setattr(secrets, "token_hex", counted_token_hex)
    yield
    patch.undo()


def test_Snapshot_OverEveryRouteOfBothInventedStores_IsWrittenOrMatchesTheBaseline(
    walked_pages, household_pages  # noqa: F811
):
    mode = os.environ.get(MODE_VARIABLE)
    target = os.environ.get(FILE_VARIABLE)
    if mode not in ("write", "compare") or not target:
        pytest.skip("run through tests/page_snapshot.py, which sets the mode and the file")

    started = time.monotonic()
    current = snapshot(
        {"main": walked_pages, "household": household_pages},
        route_count=len(dispatcher_routes()),
    )
    path = Path(target)
    if mode == "write":
        path.write_text(json.dumps(current, indent=1, sort_keys=True) + "\n", encoding="utf-8")
        print(
            f"\nwrote {current['page_count']} pages over {current['route_count']} routes "
            f"to {path} ({time.monotonic() - started:.1f}s digesting)"
        )
        return

    found = differences(load(path), current)
    print(f"\ncompared {current['page_count']} pages: {len(found)} differences")
    for line in found:
        print(f"  {line}")
    assert found == [], f"{len(found)} pages differ from {path}"
