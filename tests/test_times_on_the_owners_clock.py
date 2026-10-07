"""Wherever a page prints a clock time, it is the owner's (London's), with no zone beside it.

The host runs UTC and the owner reads London's clock, which differs by an hour from the last Sunday
of March to the last Sunday of October. KNOWN ANSWERS, worked by hand before the code changed: an
instant of 14:25 UTC on 2026-07-15 is 15:25 on the page, and one of 14:25 UTC on 2026-01-15 is
14:25. The Today and banner lines are held by `test_overview_page`, `test_home_page`, and
`test_values_sitting`; the scenes here are the Statements page and the admin page's rebuild
records, and the one place the rule is said.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from obdi.account_names import AccountShown, AccountsShown
from obdi.page_times import PAGE_ZONE
from obdi.web import _rebuild_history_html
from obdi.web_statements import statements_body
from source_tree import module_text

SUMMER = "2026-07-15T14:25:00+00:00"
WINTER = "2026-01-15T14:25:00+00:00"
ACCOUNT = "right-card"


def kept_entry(fetched_at: str) -> dict[str, object]:
    return {
        "id": 1, "origin": "a.pdf", "fetched_at": fetched_at, "account_ref": ACCOUNT,
        "parser": "santander-cc-pdf", "rows": 3, "refusal": "", "listed_days": [],
        "names": [], "sections": [],
    }


def statements_page(fetched_at: str) -> str:
    names = AccountsShown([AccountShown.named(ACCOUNT, "Right card")])
    return statements_body(
        [kept_entry(fetched_at)], names=names, options={ACCOUNT: "Right card"},
        can_assign=True, can_section_assign=True,
    )


class TestStatementsPage:
    def test_KeptStatement_InSummer_SaysTheTimeItWasKeptOnLondonsClock(self):
        assert "kept 2026-07-15 15:25" in statements_page(SUMMER)

    def test_KeptStatement_InWinter_SaysTheTimeItWasKeptAsUtcBecauseLondonIsOnUtc(self):
        assert "kept 2026-01-15 14:25" in statements_page(WINTER)

    def test_KeptStatement_WhateverTheSeason_NeverWritesAZoneBesideTheTime(self):
        for moment in (SUMMER, WINTER):
            page = statements_page(moment)
            assert "UTC" not in page and "25Z" not in page

    def test_KeptStatement_WhenItsStampCannotBeRead_IsShownAsItCameNotGuessed(self):
        assert "kept not a stamp" in statements_page("not a stamp")


class TestRebuildRecords:
    def run(self, finished: str) -> dict[str, object]:
        return {
            "ok": True, "started_at": finished.replace("14:25", "14:20"),
            "finished_at": finished, "records_total": 5, "transactions": 5, "build": "x",
        }

    def test_RecentRebuilds_InSummer_ListsTheFinishOnLondonsClock(self):
        page = _rebuild_history_html(lambda: [self.run("2026-07-15T14:25:00Z")])

        assert "<td>2026-07-15 15:25</td>" in page
        assert "<td>300s</td>" in page, "the duration is elapsed time and does not move"

    def test_RecentRebuilds_InWinter_ListsTheFinishAsStored(self):
        page = _rebuild_history_html(lambda: [self.run("2026-01-15T14:25:00Z")])

        assert "<td>2026-01-15 14:25</td>" in page


def test_TheZone_IsDeclaredOnceAndIsTheOwners():
    assert PAGE_ZONE == "Europe/London"


def test_TheSlicesNote_OnTodayShowingUtc_IsMarkedDone():
    slices = Path(__file__).parent.parent / "docs/design/2026-10-clean-slate/slices.md"
    lines = [ln for ln in slices.read_text(encoding="utf-8").splitlines() if "shows times in" in ln]

    assert lines, "the note about Today's times is still recorded"
    assert all(ln.startswith("- (Done") for ln in lines), lines


@pytest.mark.parametrize("module", ["web_overview", "values_sitting"])
def test_PagesThatPrintClockTimes_NoLongerSayUtcBesideThem(module):
    source = module_text(f"{module}.py")

    assert "All times are UTC" not in source
    assert "%H:%M} UTC" not in source
