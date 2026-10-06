"""The fetch attempts page: a summary of what was asked of the providers, then every ask by account.

Known answers, decided before the first run, over an invented ledger (newest first, as the store
reads it). The day is 2026-10-05.

    halifax-current   13 asks: 12 landed, one a day from 2026-09-24 to 2026-10-05, and one refused
                      (403, sca_exceeded) on 2026-10-04 at 12:00
    starling-main     25 asks: 20 landed, and 5 refused by the pull's own range ladder
    card-visa         3 asks: 2 landed and one refused (429, "too many requests")

So 41 asks: 34 landed and 7 refused, 5 of them the ladder narrowing. Two refusals need him (the 403
and the 429), and the page says nothing is wanted of the five. The ledger is therefore three
account folds of 13, 25, and 3 lines, and the summary lists exactly two refusals.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from typing import Any

import pytest

from coverage_page_world import repeated_lines
from obdi.callback import render_page
from obdi.web_attempts import attempts_body
from page_dom import Node, elements, parse
from test_phone_layout import sync_api

RANGE = "QUERY_EXCEEDING_MAX_TIME_RANGE"


def ask(account: str, day: str, time: str, **more: object) -> dict[str, object]:
    base: dict[str, object] = {
        "attempted_at": f"{day}T{time}+00:00",
        "source": "truelayer-booked",
        "connection_id": account.split("-")[0],
        "account_ref": account,
        "asked": f"from={day}&to={day}",
        "request_meta": '{"trigger": "routine"}',
        "outcome": "landed",
        "http_status": 200,
        "error_code": "",
        "detail": "",
        "artefact_id": 7,
    }
    return {**base, **more}


def ledger() -> dict[str, object]:
    rows: list[dict[str, object]] = []
    for n in range(12):
        rows.append(
            ask(
                "halifax-current",
                f"2026-10-{5 - n:02d}" if n < 5 else f"2026-09-{35 - n:02d}",
                "04:00:00",
            )
        )
    rows.append(
        ask(
            "halifax-current",
            "2026-10-04",
            "12:00:00",
            outcome="refused",
            http_status=403,
            error_code="sca_exceeded",
            detail="Transaction fetch failed (HTTP 403): sca_exceeded",
            artefact_id=0,
        )
    )
    for n in range(20):
        rows.append(ask("starling-main", "2026-10-05", f"{23 - n:02d}:10:00", source="starling"))
    for n in range(5):
        rows.append(
            ask(
                "starling-main",
                "2026-10-05",
                f"0{n}:20:00",
                source="starling",
                outcome="refused",
                http_status=400,
                detail=f"Starling call failed: {RANGE}",
                artefact_id=0,
            )
        )
    rows.append(
        ask(
            "card-visa",
            "2026-10-03",
            "09:00:00",
            outcome="refused",
            http_status=429,
            detail="too many requests",
            artefact_id=0,
        )
    )
    rows.append(ask("card-visa", "2026-10-02", "09:00:00"))
    rows.append(ask("card-visa", "2026-10-01", "09:00:00"))
    rows.sort(key=lambda r: str(r["attempted_at"]), reverse=True)
    return {
        "rows": rows,
        "last_day": [{"connection_id": "halifax", "account_ref": "halifax-current", "count": 5}],
    }


def page_of(data: dict[str, object]) -> Node:
    return parse(render_page("Fetch attempts", attempts_body(data)).decode())


def said(node: Node) -> str:
    return re.sub(r" ([.,:;])", r"\1", " ".join(node.text().split()))


def fold(root: Node, start: str) -> Node:
    return next(
        d for d in elements(root, "details") if said(next(elements(d, "summary"))).startswith(start)
    )


class TestTheSummary:
    def test_Summary_SaysHowManyAsksHowTheyWentAndHowManyWereTheLadderNarrowing(self) -> None:
        root = page_of(ledger())

        assert (
            "41 asks recorded: 34 landed, 7 refused. 5 refusals came from the pull narrowing"
            in said(root)
        )

    def test_Summary_ListsOnlyTheRefusalsThatAreNotTheLadder_WithTheProvidersOwnWords(self) -> None:
        root = page_of(ledger())
        summary = next(s for s in elements(root, "section") if "diag-summary" in s.classes)
        listed = [said(li) for li in elements(summary, "li")]

        assert len(listed) == 2
        assert "refused 403 sca_exceeded" in listed[0] and "halifax-current" in listed[0]
        assert "Transaction fetch failed (HTTP 403)" in listed[0]
        assert "refused 429" in listed[1] and "too many requests" in listed[1]

    def test_Summary_SaysOnceThatTheLadderNeedsNoAction(self) -> None:
        text = page_of(ledger()).text()

        assert text.count("need no action") == 1

    def test_Summary_WhenNoRefusalIsTheLadder_SaysNothingAboutTheLadder(self) -> None:
        data = ledger()
        data["rows"] = [r for r in data["rows"] if RANGE not in str(r["detail"])]  # type: ignore[union-attr]
        text = page_of(data).text()

        assert "range refused" not in text and "need no action" not in text
        assert "36 asks recorded: 34 landed, 2 refused." in said(page_of(data))

    def test_Summary_WhenNothingFailed_ListsNoRefusal(self) -> None:
        data = ledger()
        data["rows"] = [r for r in data["rows"] if r["outcome"] == "landed"]  # type: ignore[union-attr]
        root = page_of(data)

        assert "34 asks recorded: 34 landed." in said(root)
        assert 'class="pill pill-bad"' not in render_page("x", attempts_body(data)).decode()

    def test_Summary_SaysOnceThatDeepRowsUnderCountQuotaSpend(self) -> None:
        assert page_of(ledger()).text().count("known under-count") == 1


class TestTheRecordByAccount:
    def test_Ledger_IsOneFoldForEachAccountNewestFirstWithOneLineForEachAsk(self) -> None:
        root = page_of(ledger())

        starling = fold(root, "starling-main")
        halifax = fold(root, "halifax-current")
        visa = fold(root, "card-visa")
        assert [len(list(elements(d, "li"))) for d in (starling, halifax, visa)] == [25, 13, 3]
        assert said(next(elements(starling, "summary"))) == "starling-main - 25 asks, 5 refused"

    def test_EachAsk_IsOneLineSayingWhenHowItWentWhatWasAskedAndWhereToReadMore(self) -> None:
        line = said(next(elements(fold(page_of(ledger()), "card-visa"), "li")))

        assert line.startswith("2026-10-03 09:00:00 refused 429")
        assert "booked - routine - from=2026-10-03&to=2026-10-03" in line

    def test_ALandedAskWithAnArtefact_LinksToIt(self) -> None:
        links = [a.attrs["href"] for a in elements(fold(page_of(ledger()), "card-visa"), "a")]

        assert links == ["/artefact?id=7", "/artefact?id=7"]

    def test_ARefusedAsk_KeepsTheProvidersDetailBehindAFoldTheFirstTimeThePageMeetsIt(self) -> None:
        root = page_of(ledger())
        wording = "Transaction fetch failed (HTTP 403): sca_exceeded"

        assert root.text().count(wording) == 1
        assert root.text().count("provider detail") == 2

    def test_ARefusedAskNotAmongWhatNeedsHim_KeepsItsDetailInItsAccountFold(self) -> None:
        data = ledger()
        rows = [dict(r) for r in data["rows"]]  # type: ignore[union-attr]
        for n in range(7):
            rows.append(
                ask(
                    "card-visa",
                    "2026-09-2" + str(n),
                    "09:00:00",
                    outcome="refused",
                    http_status=500,
                    detail=f"wording {n} not seen before",
                    artefact_id=0,
                )
            )
        rows.sort(key=lambda r: str(r["attempted_at"]), reverse=True)
        root = page_of({"rows": rows, "last_day": []})

        visa = fold(root, "card-visa")
        assert "wording 0 not seen before" in visa.text()
        assert "more are in the account folds below" in root.text()

    def test_RepeatedRefusalsInTheSameWords_ShowThoseWordsOnce(self) -> None:
        data = ledger()
        rows = [dict(r) for r in data["rows"]]  # type: ignore[union-attr]
        for n in range(4):
            rows.append(
                ask(
                    "card-visa",
                    "2026-09-2" + str(n),
                    "09:00:00",
                    outcome="refused",
                    http_status=429,
                    detail="too many requests",
                    artefact_id=0,
                )
            )
        rows.sort(key=lambda r: str(r["attempted_at"]), reverse=True)

        assert page_of({"rows": rows, "last_day": []}).text().count("too many requests") == 1

    def test_CallsInTheLastDay_AreKeptInAFold(self) -> None:
        root = page_of(ledger())

        assert "calls" in fold(root, "Calls in the last 24 hours").text()
        assert ">5<" in render_page("x", attempts_body(ledger())).decode()

    def test_WithNoAttempts_SaysSoPlainly(self) -> None:
        text = page_of({"rows": [], "last_day": []}).text()

        assert "No attempts are recorded yet." in text

    def test_AMalformedLedger_ShowsNothingFalse(self) -> None:
        text = page_of({"rows": "nonsense", "last_day": 3}).text()

        assert "No attempts are recorded yet." in text


class TestNothingIsSaidTwice:
    def test_NoLineOfThreeWordsIsRepeatedMoreThanTwice(self) -> None:
        assert repeated_lines(page_of(ledger())) == {}

    def test_AFortyThreeFoldRepeatOfARoutinePendingAsk_IsOneLineEachBecauseEachSaysWhen(
        self,
    ) -> None:
        rows = [
            ask("halifax-current", "2026-10-05", f"{n // 60:02d}:{n % 60:02d}:00", asked="pending")
            for n in range(43)
        ]
        root = page_of({"rows": rows, "last_day": []})

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


def real_sized() -> dict[str, object]:
    """200 asks over fourteen accounts, a few refused, and the Starling ladder narrowing."""
    rows: list[dict[str, object]] = []
    for n in range(200):
        account = f"truelayer:{n % 14:08d}abcdef12"
        if n % 17 == 0:
            rows.append(
                ask(
                    account,
                    "2026-10-05",
                    f"{n // 60 % 24:02d}:{n % 60:02d}:00",
                    outcome="refused",
                    http_status=429,
                    detail="too many requests",
                )
            )
        else:
            rows.append(
                ask(account, "2026-10-05", f"{n // 60 % 24:02d}:{n % 60:02d}:00", asked="pending")
            )
    return {
        "rows": rows,
        "last_day": [{"connection_id": "c", "account_ref": "a", "count": n} for n in range(14)],
    }


class TestTheAttemptsPageFitsAPhone:
    def test_Attempts_At200Asks_WithEveryFoldClosed_IsUnderThreeScreens(self, browser: Any) -> None:
        page_html = render_page("Fetch attempts", attempts_body(real_sized())).decode()
        page = browser.new_page(viewport={"width": 390, "height": 800})
        try:
            page.set_content(page_html)
            height = float(page.evaluate("document.documentElement.scrollHeight"))
            width = "document.documentElement.scrollWidth - document.documentElement.clientWidth"
            sideways = float(page.evaluate(width))
        finally:
            page.close()

        assert sideways <= 0
        assert height <= 3 * 800, f"{height}px is {height / 800:.2f} screens"
