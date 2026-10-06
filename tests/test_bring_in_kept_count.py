# ruff: noqa: F401, F811
# The `served` fixture is imported from the file that built it; used by name it reads to the
# linter as unused and then as redefined.
"""The "N files kept" link under Bring in's wanted list says what the Kept statements page says.

The owner uploaded two PDFs, one a reader reads and one it does not, and the link read 47 on the
answer to the upload and 46 after the press that read one in, while the Statements page listed 46.
The scenes below use the invented household of `test_bring_in_assign`: the number is read off the
link, and the Statements page's own summary ("N kept: ...") is what it must equal, before and
after a press, whatever the files were.
"""

from __future__ import annotations

import re

import httpx

from page_dom import Node, elements, parse
from test_bring_in_assign import (
    D,
    assign_form,
    flat,
    kept,
    part,
    rows_of,
    santander,
    served,
)
from test_bring_in_not_read_in import CARD, READABLE, card_statement, press, two_files


def link_count(page: Node) -> int:
    links = [a for a in elements(page, "a") if a.attrs.get("href") == "/statements"
             and a.text().endswith(" kept")]
    assert len(links) == 1, [a.text() for a in elements(page, "a")]
    found = re.match(r"(\d+) files? kept", links[0].text())
    assert found is not None, links[0].text()
    return int(found.group(1))


def summary_count(base: str) -> int:
    page = parse(httpx.get(f"{base}/statements", timeout=60).text)
    found = re.match(r"(\d+) kept:", flat(page).split("Summary", 1)[1].strip())
    assert found is not None
    return int(found.group(1))


class TestTheKeptCountIsTheStatementsPagesCount:
    def test_Link_OnTheBringInPage_EqualsTheStatementsSummary(self, served):
        base, _ = served

        page = parse(httpx.get(f"{base}/bring-in", timeout=60).text)

        assert link_count(page) == summary_count(base)

    def test_Link_AfterAnUploadAndAfterAPress_EqualsTheStatementsSummaryEachTime(self, served):
        base, root = served
        after_upload = parse(two_files(base).text)

        assert link_count(after_upload) == summary_count(base)

        answer = press(base, after_upload, root, {READABLE: "up-card"})

        assert link_count(answer) == summary_count(base)

    def test_Link_WhenThePressFilesACopyOfAStatementAlreadyHeld_FallsByOneAndStillEqualsTheSummary(
        self, served
    ):
        base, root = served
        copy = santander(D(2025, 9, 10), 950)
        before = summary_count(base)
        answer = parse(httpx.post(
            f"{base}/bring-in", files=[part("Copy-of-up-card.pdf", copy)], timeout=300
        ).text)
        between = summary_count(base)

        pressed = press(base, answer, root, {"Copy-of-up-card.pdf": "up-card"})

        # The copy is kept beside the statement it repeats until the press files it, when the
        # store folds it into that statement: one more kept in the answer, none more after.
        assert link_count(answer) == between == before + 1
        assert link_count(pressed) == summary_count(base) == before
