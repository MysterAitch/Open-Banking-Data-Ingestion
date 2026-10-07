"""The fetch timeline's filters are one row of choices with the current one marked.

They were fifteen links set out in two sentences ("24 hours | 7 days | ..."), each a separate
address, so the page read as a wall of its own controls and the current choice was told by bold
text alone. Two selects and one button in a GET form say the same with the choice marked by the
control itself and no script.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta

import httpx
import pytest


@pytest.fixture(autouse=True)
def live_instance(monkeypatch):
    monkeypatch.setenv("OBDI_INSTANCE_LABEL", "obdi")
    monkeypatch.setenv("OBDI_INSTANCE_ROLE", "production")


def attempt() -> dict[str, object]:
    """One ask, a day old, so it is inside every row filter however the test runs."""
    attempted = datetime.now(UTC) - timedelta(days=1)
    return {
        "attempted_at": attempted.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "asked": f"from={(attempted - timedelta(days=5)).date()}&to={attempted.date()}",
        "source": "truelayer-booked",
        "outcome": "landed",
        "request_meta": "{}",
    }


@pytest.fixture
def timeline(serve_hub):
    base = serve_hub(recent_attempts=lambda: [attempt()])

    def get(query: str = "") -> str:
        response = httpx.get(f"{base}/fetch-timeline{query}", timeout=20)
        assert response.status_code == 200
        return response.text

    return get


def form_of(page: str) -> str:
    match = re.search(r'<form class="timeline-choices"[^>]*>.*?</form>', page, re.S)
    assert match, "no choices form"
    return match.group(0)


def selected(form: str, name: str) -> list[str]:
    select = re.search(rf'<select name="{name}"[^>]*>(.*?)</select>', form, re.S)
    assert select, name
    return re.findall(r'<option value="([^"]*)" selected', select.group(1))


class TestTheChoicesAreOneRow:
    def test_Timeline_OffersTheChoicesAsOneGetFormWithNoScript(self, timeline):
        page = timeline()

        form = form_of(page)
        assert 'method="get"' in form and 'action="/fetch-timeline"' in form
        assert "<script" not in page

    def test_Timeline_HasNoFilterLinksForTheRangesOrTheSpans(self, timeline):
        page = timeline("?days=7")

        links = re.findall(r'href="(/fetch-timeline\?[^"]*)"', page)
        assert links and all("until=" in link for link in links), links
        assert len(links) <= 3, "only the pan links remain"

    def test_Timeline_WithNoQuery_MarksTheDefaultsSelected(self, timeline):
        form = form_of(timeline())

        assert selected(form, "days") == ["7"]
        assert selected(form, "span") == ["120"]

    def test_Timeline_WithAChosenRangeAndSpan_MarksThoseSelected(self, timeline):
        form = form_of(timeline("?days=30&span=fit"))

        assert selected(form, "days") == ["30"]
        assert selected(form, "span") == ["fit"]

    def test_Timeline_ForEverything_MarksEverythingSelected(self, timeline):
        assert selected(form_of(timeline("?days=all")), "days") == ["all"]

    def test_Timeline_ForARangeNotInTheList_AddsItSelectedRatherThanLosingTheChoice(
        self, timeline
    ):
        form = form_of(timeline("?days=13"))

        assert selected(form, "days") == ["13"]

    def test_Timeline_ForAnUnparseableChoice_FallsBackToTheDefaultAndReflectsNothing(
        self, timeline
    ):
        page = timeline("?days=bananas&span=kumquats&until=pears")

        form = form_of(page)
        assert selected(form, "days") == ["7"] and selected(form, "span") == ["120"]
        for word in ("bananas", "kumquats", "pears"):
            assert word not in page

    def test_Timeline_OffersOneSubmitButton(self, timeline):
        form = form_of(timeline())

        assert form.count("<button") == 1 and 'type="submit"' in form

    def test_Timeline_WhenPanned_KeepsThePanPositionThroughTheForm(self, timeline):
        form = form_of(timeline("?days=7&until=2026-09-01T00:00:00Z"))

        assert '<input type="hidden" name="until" value="2026-09-01T00:00:00Z">' in form

    def test_Timeline_NotPanned_CarriesNoUntilField(self, timeline):
        assert 'name="until"' not in form_of(timeline())

    def test_Timeline_EveryChoiceTheFormOffers_IsOneTheServerAccepts(self, timeline):
        form = form_of(timeline())
        for name in ("days", "span"):
            select = re.search(rf'<select name="{name}"[^>]*>(.*?)</select>', form, re.S)
            assert select
            for value in re.findall(r'<option value="([^"]*)"', select.group(1)):
                query = f"?{name}={value}"
                page = timeline(query)
                assert selected(form_of(page), name) == [value], (name, value)
