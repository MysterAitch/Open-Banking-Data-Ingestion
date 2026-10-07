"""Today's row for an account says nothing about its terms unless a rate or limit window ends
within the next month, or the newest statement prints a rate that differs from what is declared.
A quiet note in the row's own slot: nothing is asked of the owner.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest

from account_about_world import (
    BARE,
    BARE_RATE,
    BUSY,
    DIFFERS,
    ENDING,
    FAR,
    OLD_DIFFERS,
    QUIET,
    SAME,
    ahead,
    serve,
    set_environment,
)
from page_dom import elements, parse


@pytest.fixture(scope="module")
def root(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return tmp_path_factory.mktemp("account-about")


@pytest.fixture(scope="module")
def base(root: Path) -> Iterator[str]:
    with serve(root) as address:
        yield address


@pytest.fixture(autouse=True)
def _environment(base: str, root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    set_environment(root, monkeypatch)


def flags(base: str) -> dict[str, list[str]]:
    page = parse(httpx.get(f"{base}/", timeout=120).text)
    found: dict[str, list[str]] = {}
    for row in elements(page, "li"):
        link = next((a for a in elements(row, "a") if "arow" in a.classes), None)
        if link is None:
            continue
        ref = re.search(r"ref=([^&]+)", link.attrs["href"])
        assert ref is not None
        # This file is about the terms note. The party note shares the slot when nothing else
        # fills it (`test_party_stated_pages`), and these statement-only accounts earn one.
        found[ref.group(1)] = [
            s.text()
            for s in elements(row, "span")
            if "a-flag" in s.classes and "named by the description only" not in s.text()
        ]
    return found


def test_Row_WhenARateEndsWithinTheMonth_SaysWhenInTheFlagSlot(base):
    assert flags(base)[ENDING] == [f"Rate ends {ahead(20).isoformat()}"]


def test_Row_WhenAStatementPrintsADifferentRate_SaysSo(base):
    assert flags(base)[DIFFERS] == ["Statement rate differs"]


def test_Row_WhenARateEndsLaterThanAMonth_SaysNothing(base):
    assert flags(base)[FAR] == []


def test_Row_WhenTheNewestStatementAgrees_SaysNothingEvenIfAnOldOneDiffered(base):
    found = flags(base)

    assert found[QUIET] == [] and found[OLD_DIFFERS] == [] and found[SAME] == []


def test_Row_AnAccountThatAlreadyWaitsForSomething_SaysThatAndLeavesTheNoteToItsPage(base):
    said = flags(base)[BUSY]

    assert len(said) == 1 and not said[0].startswith("Rate ends")


def test_Row_TheNoteIsQuieterThanAThingToDo(base):
    page = parse(httpx.get(f"{base}/", timeout=120).text)
    spans = [s for s in elements(page, "span") if "a-flag" in s.classes]

    notes = [s for s in spans if s.text().startswith("Rate ends")]
    assert notes and all("quiet" in s.classes for s in notes)
    # The party note ("N transactions named by the description only") is a note too, quiet by
    # the same rule (`test_party_stated_pages`); only the rest must shout.
    things_to_do = [
        s
        for s in spans
        if not s.text().startswith(("Rate ends", "Statement rate"))
        and "named by the description only" not in s.text()
    ]
    assert all("quiet" not in s.classes for s in things_to_do)


def test_Row_AnAccountWithNothingDeclared_SaysNothing(base):
    assert flags(base)[BARE] == []


def test_Row_AStatedRateWithNoWindow_IsNotSomethingToFlag(base):
    assert flags(base)[BARE_RATE] == []
