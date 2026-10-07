"""The Actual sync history: a summary that answers "has the sync been behaving?", then the record
by kind with results that came out the same said once.

Known answers, decided before the first run, over invented results built the way the applier
writes them (`actual_states`). The story ends at 2026-10-04 09:00 UTC:

    audits   24, twice a day for twelve days; the oldest 4 clean, the newest 20 each finding
             orphaned imports in halifax-current-account (3) and starling-main (2), so two of
             seventeen accounts differ
    pushes   12, one a day at 09:30; the third newest failed ("Actual did not answer"), so the
             record is a run of 2 applied, 1 failed, and a run of 9 applied
    aligns   1, complete

The record is therefore 2 audit lines, 3 push lines, and 1 alignment line for 37 results. The
page measured against the same results as the real instance's (200 recorded) in a real browser:
see `TestTheHistoryFitsAPhone`.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from typing import Any

import pytest

from actual_states import align, audit, push
from coverage_page_world import repeated_lines
from obdi.pages.callback import render_page
from obdi.pages.web import _ResultHistory
from obdi.pages.web_actual_history import actual_history_body
from page_dom import Node, elements, parse
from test_phone_layout import sync_api

ORPHANS = {"halifax-current-account": 3, "starling-main": 2}


def story() -> list[dict[str, object]]:
    audits = [audit(-720 * n, orphaned=ORPHANS if n < 20 else None) for n in range(24)]
    pushes = [
        push(30 - 1440 * k, ok=k != 2, error="Actual did not answer" if k == 2 else "")
        for k in range(12)
    ]
    return [*audits, *pushes, align(-100)]


def page_of(results: list[dict[str, object]], *, total: int | None = None, **more: Any) -> Node:
    history = _ResultHistory(results=tuple(results), total=total, **more)
    return parse(render_page("Actual sync history", actual_history_body(history)).decode())


def fold(root: Node, title: str) -> Node:
    return next(d for d in elements(root, "details") if d.children and title in _summary(d))


def _summary(details: Node) -> str:
    return next(c for c in elements(details, "summary")).text()


def said(node: Node) -> str:
    """The words of a node as read: no space is left before a stop, comma, or colon, which markup
    around a time or a name leaves."""
    return re.sub(r" ([.,:;])", r"\1", " ".join(node.text().split()))


def lines_in(details: Node) -> list[str]:
    return [said(li) for li in elements(details, "li")]


class TestTheSummary:
    def test_Summary_CountsEachKindAndSaysHowTheNewestOfEachWent(self) -> None:
        root = page_of(story(), total=37)
        kinds = [said(li) for li in elements(root, "li") if "recorded" in li.text()]

        assert kinds == [
            "Audits: 24 recorded; the newest, 2026-10-04 09:00Z: differed in 2 of 17 accounts.",
            "Pushes: 12 recorded; the newest, 2026-10-04 09:30Z: applied; 1 failed in all.",
            "Alignments: 1 recorded; the newest, 2026-10-04 07:20Z: aligned steps that ran: "
            "push, audit.",
        ]

    def test_Summary_SaysWhatNeedsHimAndWhereToGo(self) -> None:
        root = page_of(story(), total=37)
        needs = [
            li.text()
            for li in elements(root, "li")
            if "bad" in li.classes and "newest audit" in li.text()
        ]

        assert needs == ["The newest audit found differences in 2 of 17 accounts."]
        assert any(a.attrs["href"] == "/actual" for a in elements(root, "a"))

    def test_Summary_WhenTheNewestPushFailed_SaysSo(self) -> None:
        results = [push(30, ok=False, error="Actual did not answer"), audit(0)]
        root = page_of(results, total=2)

        assert any(
            "The newest push failed: Actual did not answer." in li.text()
            for li in elements(root, "li")
        )

    def test_Summary_WhenEverythingWentWell_AsksForNothing(self) -> None:
        root = page_of([push(30), audit(0)], total=2)

        assert not [li for li in elements(root, "li") if "bad" in li.classes]
        assert not any(a.attrs["href"] == "/actual" for a in elements(root, "a"))

    def test_Summary_SaysHowManyOfHowManyAreShown(self) -> None:
        root = page_of(story(), total=205)

        assert "showing 37 of 205 results" in root.text()

    def test_Summary_WhenAResultFileCouldNotBeRead_NamesIt(self) -> None:
        root = page_of(story(), total=38, unreadable=("push-x.json",), unreadable_count=1)

        assert "1 result file could not be read: push-x.json" in root.text()

    def test_History_WithNothingRecorded_SaysSo(self) -> None:
        assert "Nothing recorded yet." in page_of([]).text()


class TestTheRecordByKind:
    def test_Audits_CameOutTheSameForTwentyOfThem_AreOneLine(self) -> None:
        lines = lines_in(fold(page_of(story(), total=37), "Audits (24)"))

        assert len(lines) == 2
        assert lines[0].startswith("20 audits from 2026-09-24 21:00Z to 2026-10-04 09:00Z")
        assert "halifax-current-account: 3 orphaned, balance differs" in lines[0]
        assert "starling-main: 2 orphaned, balance differs" in lines[0]
        assert lines[1].startswith("4 audits from 2026-09-22 21:00Z to 2026-09-24 09:00Z")
        assert "no differences in 17 accounts" in lines[1]

    def test_Pushes_AFailureBetweenTwoRunsOfSuccess_IsItsOwnLineAndKeepsItsReason(self) -> None:
        lines = lines_in(fold(page_of(story(), total=37), "Pushes (12)"))

        assert len(lines) == 3
        assert lines[0].startswith("2 pushes from 2026-10-03 09:30Z to 2026-10-04 09:30Z")
        assert "applied: 12 added, 0 accounts provisioned" in lines[0]
        assert lines[1] == "2026-10-02 09:30Z - failed: Actual did not answer"
        assert lines[2].startswith("9 pushes from 2026-09-23 09:30Z to 2026-10-01 09:30Z")

    def test_EveryResult_IsAccountedForInTheRecord(self) -> None:
        root = page_of(story(), total=37)
        counted = 0
        for title in ("Audits (24)", "Pushes (12)", "Alignments (1)"):
            for line in lines_in(fold(root, title)):
                counted += (
                    int(line.split()[0])
                    if line[0].isdigit() and "from" in line.split(" - ")[0]
                    else 1
                )

        assert counted == 37

    def test_TheNewestResultsAreFirst(self) -> None:
        lines = lines_in(fold(page_of(story(), total=37), "Pushes (12)"))

        assert lines[0].split(" to ")[1].startswith("2026-10-04")
        assert lines[-1].split(" to ")[1].startswith("2026-10-01")

    def test_AResultOfAKindThisBuildCannotRead_IsNamedAndNeverFoldedAway(self) -> None:
        odd = {"ok": True, "kind": "teleport", "finished_at": "2026-10-04T09:45:00.000Z"}
        root = page_of([*story(), odd], total=38)

        assert any(
            "of a kind this build cannot read (teleport)" in li.text()
            for li in elements(root, "li")
        )
        assert "unknown result kind teleport" in fold(root, "Teleport results (1)").text()

    def test_AResultWithNoReadableTime_IsStillListedAtTheEnd(self) -> None:
        broken = {"ok": True, "finished_at": "not a time", "added": 1, "provisioned": 0}
        root = page_of([*story(), broken], total=38)

        lines = lines_in(fold(root, "Pushes (13)"))
        assert lines[-1].endswith("applied: 1 added, 0 accounts provisioned")

    def test_TheWordsOfAnAuditLine_AreExplainedOnce(self) -> None:
        root = page_of(story(), total=37)

        legend = fold(root, "What the words in an audit's line mean")
        assert root.text().count("Actual's balance for the account is not the sum of the rows") == 1
        assert "orphaned" in legend.text()


class TestNothingIsSaidTwice:
    def test_NoLineOfThreeWordsIsRepeatedMoreThanTwice(self) -> None:
        root = page_of(story(), total=37)

        assert repeated_lines(root) == {}

    def test_AFailedAuditAndPushAmongManyOkOnes_KeepTheirReasonsWithoutRepeats(self) -> None:
        results = [audit(-60 * n, ok=n % 5 != 0, error=f"refused {n}") for n in range(30)]
        root = page_of(results, total=30)

        assert repeated_lines(root) == {}


@pytest.fixture(scope="module")
def browser() -> Iterator[Any]:
    with sync_api.sync_playwright() as playwright:
        try:
            launched = playwright.chromium.launch()
        except sync_api.Error as exc:
            pytest.skip(f"no browser available: {exc}")
        yield launched
        launched.close()


def real_sized() -> list[dict[str, object]]:
    """The record at the real instance's size: 200 recorded results over ten weeks, an audit and a
    push every few hours, most audits finding the same orphans, and now and then a failure."""
    results: list[dict[str, object]] = []
    for n in range(100):
        results.append(
            audit(
                -420 * n,
                orphaned=ORPHANS if n % 17 else None,
                ok=n % 23 != 0,
                error="Actual refused the connection",
            )
        )
        results.append(push(-420 * n + 30, ok=n % 29 != 0, error="Actual did not answer"))
    return results


class TestTheHistoryFitsAPhone:
    def test_History_At200Results_WithEveryFoldClosed_IsUnderThreeScreens(
        self, browser: Any
    ) -> None:
        page_html = render_page(
            "Actual sync history",
            actual_history_body(_ResultHistory(results=tuple(real_sized()), total=200)),
        ).decode()
        page = browser.new_page(viewport={"width": 390, "height": 800})
        try:
            page.set_content(page_html)
            height = float(page.evaluate("document.documentElement.scrollHeight"))
            sideways = float(
                page.evaluate(
                    "document.documentElement.scrollWidth - document.documentElement.clientWidth"
                )
            )
        finally:
            page.close()

        assert sideways <= 0
        assert height <= 3 * 800, f"{height}px is {height / 800:.2f} screens"
