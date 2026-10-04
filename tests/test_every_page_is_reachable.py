"""No page is reachable only by knowing its address.

The walk starts where a person starts, at the seven destinations of the strip, follows every link
on every page it reaches, and then asks the dispatcher which GET routes it never met. The store
is the twenty-account household built through the real doors, with a kept statement and a
connection, so that the pages that only appear when there is something to list are listed.

KNOWN ANSWER. The routes a link cannot reach are decided here before the walk: the liveness
probe, the bank's redirect, and the two addresses that answer with a destination's own page under
a name they had before the destinations existed. Anything else the walk misses is a page that
nothing links to.
"""

from __future__ import annotations

import re
from collections import deque
from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path
from urllib.parse import parse_qs, urldefrag, urlparse

import httpx
import pytest

from home_world import build_scale_world
from obdi.cli import build_web_config
from obdi.connections import Connection, ConnectionStore
from obdi.navigation import ALIASES, DESTINATIONS
from obdi.store import Store
from obdi.synthetic_pdf import build_pdf
from section_harness import environment, keep, serve_config
from test_navigation import get_routes

#: A statement of invented lines, kept unassigned so the statements page has one to list.
STATEMENT_LINES = [
    "Santander UK plc. Registered Office: 2 Triton Square",
    "Statement Date: 11th August 2026      Page No: 1 / 1",
    "Balance brought forward from previous statement          1,000.00",
    "2nd Aug     INVENTED PAYEE LONDON GB                   0.00",
    "Your new balance:                                      1,000.00",
]

#: Addresses no page links to, each for a stated reason.
NOT_LINKED = {
    "/healthz": "liveness, read by the container's probe",
    "/callback": "the bank's redirect, which no page links to",
    **dict.fromkeys(ALIASES, "a destination's own page, kept for the bookmarks that hold it"),
}

PUSH_RESULT: dict[str, object] = {
    "kind": "push",
    "ok": True,
    "finished_at": "2026-10-01T12:00:00Z",
    "added": 3,
    "provisioned": 0,
}

HREF = re.compile(r'<a\b[^>]*\bhref="([^"]+)"')


@pytest.fixture(scope="module")
def world(tmp_path_factory: pytest.TempPathFactory) -> Iterator[str]:
    root: Path = tmp_path_factory.mktemp("reach")
    db = root / "store.sqlite3"
    build_scale_world(db)
    with Store(db) as store:
        keep(store, build_pdf(STATEMENT_LINES), "march.pdf")
    mp = pytest.MonkeyPatch()
    environment(mp, root)
    ConnectionStore(root / "connections.json").put(
        Connection(
            connection_id="halifax",
            provider="p",
            refresh_token="r",
            consent_expires_at="2099-06-15T00:00:00+00:00",
        )
    )
    config = build_web_config(db)
    assert config is not None
    # The Actual page links its history only once there is a result to have a history of.
    config = replace(config, actual_status=lambda: [PUSH_RESULT])
    base, stop = serve_config(config)
    try:
        yield base
    finally:
        stop()
        mp.undo()


@pytest.fixture(scope="module")
def walked(world: str) -> dict[str, set[str]]:
    return _walk(world)


def _walk(base: str) -> dict[str, set[str]]:
    """Every route a link reached, mapped to the pages that linked to it."""
    linked_from: dict[str, set[str]] = {}
    seen: set[tuple[str, frozenset[str]]] = set()
    queue: deque[str] = deque(href for _, _, href in DESTINATIONS)
    while queue:
        url = queue.popleft()
        parsed = urlparse(url)
        key = (parsed.path or "/", frozenset(parse_qs(parsed.query)))
        if key in seen:
            continue
        seen.add(key)
        response = httpx.get(f"{base}{url}", timeout=60)
        if response.status_code != 200:
            continue
        for href in HREF.findall(response.text):
            target, _ = urldefrag(href.replace("&amp;", "&"))
            if not target.startswith("/") or target.startswith("//"):
                continue
            path = urlparse(target).path.rstrip("/") or "/"
            linked_from.setdefault(path, set()).add(url)
            queue.append(target)
    return linked_from


class TestTheFieldStatisticsPageIsNotAnEqualOfTheAccountPage:
    """It was "Shape", linked beside an account's own page as if it were one."""

    def test_AccountsLedgerPage_LinksFieldStatisticsOnlyFromItsFoot(self, world):
        page = httpx.get(f"{world}/ledger?ref=agree-1", timeout=60).text

        foot = page.split('<div class="foot-links">', 1)[1]
        assert "Field statistics for this account" in foot
        assert page.count("Field statistics") == 1
        assert "Shape of this account" not in page

    def test_HomePage_NamesTheLinkByWhatItIs_NotAsTheAccountPage(self, world):
        page = httpx.get(f"{world}/", timeout=60).text

        assert ">Account page<" not in page
        assert ">Field statistics<" in page


class TestEveryGetRouteIsLinkedFromSomewhere:
    def test_EveryGetRoute_ExceptTheDeclaredFew_IsReachedByFollowingLinksFromTheDestinations(
        self, walked
    ):
        unreached = [r for r in get_routes() if r not in walked and r not in NOT_LINKED]

        assert unreached == [], f"nothing links to: {unreached}"

    def test_TheWalk_ReachesTheDestinationsThemselves(self, walked):
        for _, _, href in DESTINATIONS:
            assert href in walked, href

    def test_TheAliasedAddresses_AreNotLinkedFromAnyPageThisSiteOwns(self, walked):
        # The home page's own facts still link to /connections, which is a page of its own;
        # the retired index addresses are kept for bookmarks and are not offered.
        for alias in ("/reports", "/evidence"):
            assert alias not in walked, alias

    def test_TheWalk_ActuallyFollowsLinksDeep_NotJustTheStrip(self, walked):
        assert "/artefact" in walked and "/statement-shape" in walked and "/ledger" in walked
