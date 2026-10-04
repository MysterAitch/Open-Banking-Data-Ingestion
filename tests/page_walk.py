"""Every page the dispatcher can render over the invented store, fetched once per test module.

The wording rules (plurals, dates, and so on) are properties of every page, so they are tested
by walking the same routes the "no stored value on a GET" test walks, over the same invented
store, and reading the text of each answer. A page added later is held to the rules the day it
exists. `WALKED` is the fixture to depend on: it maps each URL to the page text it returned.
"""

from __future__ import annotations

import httpx
import pytest

# The store and the server are the ones the masking walk builds; importing the fixtures here
# makes them available to any module that imports `walked_pages` or `WALKED`.
from test_get_routes_hold_no_stored_values import (  # noqa: F401
    accounts_of,
    artefact_ids,
    dispatcher_routes,
    invented,
    served,
)


def walked_urls(db) -> list[str]:
    """Each route bare, with an artefact, and with every account the store holds."""
    ids = artefact_ids(db)
    refs = accounts_of(db)
    assert len(ids) >= 3 and refs, "the invented store is not the store this walk describes"
    urls: list[str] = []
    for route in dispatcher_routes():
        urls.append(route)
        urls.append(f"{route}?id={ids[0]}&artefact={ids[0]}&view=payload")
        for ref in refs:
            urls.append(f"{route}?ref={ref}&month=2026-09")
    for artefact in ids:
        urls += [
            f"/artefact?id={artefact}",
            f"/statement-shape?artefact={artefact}",
        ]
    return urls


@pytest.fixture(scope="module")
def walked_pages(served, invented) -> dict[str, str]:  # noqa: F811
    db, _ = invented
    pages: dict[str, str] = {}
    for url in walked_urls(db):
        response = httpx.get(f"{served}{url}", timeout=60)
        pages[url] = response.text
    assert len(pages) > 50, "the walk read too few pages to mean anything"
    return pages
