# ruff: noqa: F401, F811
# The `served` fixture is imported from the file that built it; used by name it reads to the
# linter as unused and then as redefined.
"""One press, one signpost: the one form and the Kept statements page say the same thing.

The owner uploaded one statement and the page said, beneath the one form that already asked about
it, "1 kept statement is waiting for an account. Give it one", linking to the Kept statements page.
There the same file sat in a closed fold with a chooser that carried no reason, so he took the
longer road. Every answer below was decided before the first run, over the invented household of
`test_bring_in_assign` (`Up-card-2025-08.pdf` and `Up-card-2025-09.pdf` filed under `up-card`):

  * an upload whose one file waits for an account asks in its form and does not also say that a
    kept statement is waiting;
  * a file kept by an EARLIER upload and not listed in this form is still counted in that line,
    for the files the form does not ask about;
  * the Kept statements page's chooser for `Up-card-2026-09.pdf` has `up-card` selected, and says
    why in the words the one form uses; for a file nothing matches it selects nothing and says
    nothing of a guess.
"""

from __future__ import annotations

import httpx

from page_dom import Node, elements, parse
from test_bring_in_assign import (
    D,
    flat,
    letter,
    part,
    santander,
    served,
)

WAITING = "waiting for an account"


def upload(base: str, *files: tuple[str, bytes]) -> Node:
    return parse(httpx.post(
        f"{base}/bring-in", files=[part(name, payload) for name, payload in files], timeout=300
    ).text)


def notices(page: Node) -> list[str]:
    return [flat(p) for p in elements(page, "p") if "bi-notice" in p.classes]


def kept_page(base: str) -> Node:
    return parse(httpx.get(f"{base}/statements", timeout=60).text)


def card_of(page: Node, name: str) -> Node:
    cards = [
        li for li in elements(page, "li")
        if any(s.text().startswith(name) for s in elements(li, "summary"))
    ]
    assert len(cards) == 1, f"{len(cards)} cards for {name}"
    return cards[0]


def selected_of(card: Node) -> str:
    select = next(elements(card, "select"))
    picked = [o.attrs.get("value", "") for o in elements(select, "option") if "selected" in o.attrs]
    return picked[0] if picked else ""


class TestTheKeptLineWhileTheFormAsks:
    def test_Upload_OfOneStatementWaiting_AsksInItsFormAndDoesNotAlsoSayAKeptStatementWaits(
        self, served
    ):
        base, _ = served

        page = upload(base, ("Up-card-2026-09.pdf", santander(D(2026, 9, 10), 1322)))

        assert elements(page, "form")
        assert [n for n in notices(page) if WAITING in n] == []
        assert not [
            a for a in elements(page, "a") if a.attrs.get("href") == "/statements"
            and WAITING in flat(a.parent or a)
        ]

    def test_Upload_WhenAnEarlierUploadLeftAFileKept_StillSaysThatFileWaits(self, served):
        base, _ = served
        upload(base, ("Santander-2026-08.pdf", santander(D(2026, 8, 12), 1544)))

        page = upload(base, ("Up-card-2026-09.pdf", santander(D(2026, 9, 10), 1322)))

        said = [n for n in notices(page) if WAITING in n]
        assert said == ["1 kept statement is waiting for an account. Give it one"]

    def test_Upload_WhenEveryFileInTheFormIsOneOfSeveralKept_CountsOnlyThoseNotInTheForm(
        self, served
    ):
        base, _ = served
        upload(base, ("Santander-2026-08.pdf", santander(D(2026, 8, 12), 1544)))
        upload(base, ("Santander-2026-07.pdf", santander(D(2026, 7, 12), 1433)))

        page = upload(
            base,
            ("Up-card-2026-09.pdf", santander(D(2026, 9, 10), 1322)),
            ("Santander-2026-09.pdf", santander(D(2026, 9, 12), 1655)),
        )

        said = [n for n in notices(page) if WAITING in n]
        assert said == ["2 kept statements are waiting for an account. Give them one"]

    def test_BringInPage_WhenNoUploadIsInFlight_StillSaysKeptStatementsWait(self, served):
        base, _ = served
        upload(base, ("Santander-2026-08.pdf", santander(D(2026, 8, 12), 1544)))

        page = parse(httpx.get(f"{base}/bring-in", timeout=60).text)

        assert [n for n in notices(page) if WAITING in n] == [
            "1 kept statement is waiting for an account. Give it one"
        ]


class TestTheKeptStatementsPageCarriesTheSameGuess:
    def test_Chooser_ForAStatementNamedLikeAnEarlierOne_HasThatAccountSelectedWithItsReason(
        self, served
    ):
        base, _ = served
        upload(base, ("Up-card-2026-09.pdf", santander(D(2026, 9, 10), 1322)))

        card = card_of(kept_page(base), "Up-card-2026-09.pdf")

        assert selected_of(card) == "up-card"
        assert "Named like the 2025 statement Up-card-2025-09.pdf, which went to Up card" in flat(
            card
        )

    def test_Chooser_ForAStatementNothingMatches_SelectsNothingAndSaysNothingOfAGuess(
        self, served
    ):
        base, _ = served
        upload(base, ("Santander-2026-08.pdf", santander(D(2026, 8, 12), 1544)))

        card = card_of(kept_page(base), "Santander-2026-08.pdf")

        assert selected_of(card) == ""
        assert "went to" not in flat(card)

    def test_Chooser_ForAStatementTheFormAndThePageBothShow_AgreeOnAccountAndReason(self, served):
        base, _ = served
        from test_bring_in_assign import assign_form, chosen, rows_of

        page = upload(base, ("Up-card-2026-09.pdf", santander(D(2026, 9, 10), 1322)))
        row = rows_of(assign_form(page))["Up-card-2026-09.pdf"]
        card = card_of(kept_page(base), "Up-card-2026-09.pdf")
        reason = next(p for p in elements(row, "p") if "bi-guess" in p.classes)

        assert chosen(row) == selected_of(card)
        assert flat(reason) in flat(card)
