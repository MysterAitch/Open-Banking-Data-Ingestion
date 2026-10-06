# ruff: noqa: F401, F811
# The `served` fixture is imported from the file that built it; used by name it reads to the
# linter as unused and then as redefined.
"""The answer to "Read it in" says what the upload was FOR, not what the matcher counted.

The owner read in one statement and was shown "parsed 33, new 3, matched 30, superseded 0, for
review 1, folded 1 feed row as the same money a statement itemises ...": a counts dump that never
said the one thing the upload was for (it covered the period that was wanted, so nothing more is
wanted for that account), and whose "for review 1" was a count taken mid-import - the same import
then settled that flag, and the review report showed none open.

The scenes use the invented household of `test_bring_in_assign`. Up card holds statements closing
2025-08-10 and 2025-09-10 that do not join (the second opens on a balance the first did not end
on), so Bring in wants the statement covering 2025-08-11 to 2025-09-04. `middle_statement` is that
statement: opening on the first's closing balance and closing on the second's opening one, with
one credit on 2025-08-30. Every answer below was decided before the first run:

  * pressing "Read it in" for it says it covers the wanted 2025-08-11 to 2025-09-04, that nothing
    more is wanted for Up card, and that 1 transaction is new; the wanted period is gone from the
    list beneath;
  * a statement for an account nothing was wanted for says "Nothing was wanted for it";
  * the matcher's own counts are in a closed fold, "What was counted", and nowhere else;
  * how many transactions still need a decision is read from the open flags AFTER the import, so
    a flag the import settled is never reported (zero open: no word of review), and one still
    open is reported with where to decide it;
  * where the open flags cannot be read, the page says so rather than saying nothing.
"""

from __future__ import annotations

from types import SimpleNamespace

import httpx
import pytest

from obdi.synthetic_pdf import build_pdf
from page_dom import Node, elements, parse
from test_bring_in_assign import (
    PLANTED_PAYEE,
    D,
    flat,
    kept,
    part,
    santander,
    served,
)

COVERS = "Covers the wanted 2025-08-11 to 2025-09-04; nothing more is wanted for Up card"


def middle_statement() -> bytes:
    """The statement closing 2025-09-04 that joins Up card's 2025-08-10 and 2025-09-10 ones."""
    return build_pdf([
        "Santander UK plc. Registered Office: 2 Triton Square",
        "Statement Date: 4th September 2025      Page No: 1 / 1",
        "Previous balance as at 10th August 2025: 109.00",
        "Account credit limit:            3,000.00",
        "Balance brought forward from previous statement          109.00",
        f"30th Aug {PLANTED_PAYEE}   CR 9.00",
        "Your new balance:                                        100.00",
    ])


def keep(base: str, name: str, payload: bytes) -> None:
    """Upload `name` with no account, so it is kept and waits for one."""
    httpx.post(f"{base}/bring-in", files=[part(name, payload)], timeout=300)


def press(base: str, root, name: str, account: str) -> httpx.Response:
    """Press the one form with `account` chosen for the kept file `name`."""
    held = kept(root)
    return httpx.post(
        f"{base}/statements-assign", data={f"account-{held[name]['id']}": account}, timeout=300
    )


def outcome_lines(page: Node) -> list[str]:
    return [flat(li) for ul in elements(page, "ul") if "bi-outcomes" in ul.classes
            for li in elements(ul, "li")]


def without_counted_fold(page: Node) -> Node:
    for node in list(elements(page, "details")):
        if any(s.text() == "What was counted" for s in elements(node, "summary")):
            node.children.clear()
    return page


def wanted_text(page: Node) -> str:
    return " ".join(
        flat(s) for s in elements(page, "section") if s.attrs.get("aria-label") == "Wanted"
    )


class TestWhatTheUploadWasFor:
    def test_Press_ForTheStatementThatCoversWhatWasWanted_SaysSoAndThatNothingMoreIsWanted(
        self, served
    ):
        base, root = served
        keep(base, "Up-card-2025-09-04.pdf", middle_statement())

        page = parse(press(base, root, "Up-card-2025-09-04.pdf", "up-card").text)
        lines = outcome_lines(page)

        assert len(lines) == 1
        assert COVERS in lines[0]
        assert "1 transaction is new" in lines[0]

    def test_Press_ForTheStatementThatCoversWhatWasWanted_RemovesItFromWhatIsStillWanted(
        self, served
    ):
        base, root = served
        keep(base, "Up-card-2025-09-04.pdf", middle_statement())

        page = parse(press(base, root, "Up-card-2025-09-04.pdf", "up-card").text)

        assert "2025-08-11 to 2025-09-04" not in wanted_text(page)

    def test_Upload_ScopedToTheAccount_SaysTheSameAsThePressOfTheOneForm(self, served):
        base, _ = served

        response = httpx.post(
            f"{base}/bring-in", data={"account": "up-card"},
            files=[part("Up-card-2025-09-04.pdf", middle_statement())], timeout=300,
        )

        assert COVERS in " ".join(outcome_lines(parse(response.text)))

    def test_Press_ForAStatementOfAnAccountNothingWasWantedFor_SaysNothingWasWanted(self, served):
        base, root = served
        keep(base, "Other-card-2026-09.pdf", santander(D(2026, 9, 11), 1433))

        page = parse(press(base, root, "Other-card-2026-09.pdf", "other-card").text)
        lines = outcome_lines(page)

        assert len(lines) == 1
        assert "Nothing was wanted for it" in lines[0]
        assert "Covers the wanted" not in lines[0]

    def test_Press_ForAStatementThatCoversNoneOfWhatIsWanted_SaysItCoversNone(self, served):
        base, root = served
        keep(base, "Up-card-2026-09.pdf", santander(D(2026, 9, 10), 1322))

        page = parse(press(base, root, "Up-card-2026-09.pdf", "up-card").text)
        lines = outcome_lines(page)

        assert len(lines) == 1
        assert "covers none of what is wanted for Up card" in lines[0]
        assert "Nothing was wanted for it" not in lines[0]


class TestTheMatchersCountsAreFolded:
    def test_Answer_KeepsTheCountsLineInAClosedFoldAndNowhereElse(self, served):
        base, root = served
        keep(base, "Up-card-2025-09-04.pdf", middle_statement())

        page = parse(press(base, root, "Up-card-2025-09-04.pdf", "up-card").text)
        folds = [
            node for node in elements(page, "details")
            if any(s.text() == "What was counted" for s in elements(node, "summary"))
        ]

        assert len(folds) == 1
        assert "open" not in folds[0].attrs
        assert "parsed 1" in flat(folds[0])
        outside = flat(without_counted_fold(page))
        assert "parsed" not in outside
        assert "matched" not in outside
        assert "superseded" not in outside

    def test_Answer_StatesNoAmountNoPayeeAndNoFigure(self, served):
        base, root = served
        keep(base, "Up-card-2025-09-04.pdf", middle_statement())

        response = press(base, root, "Up-card-2025-09-04.pdf", "up-card")

        assert PLANTED_PAYEE not in response.text
        assert "9.00" not in response.text


class _Queue:
    """A stand-in for the open-flags queue, recording the state of the store when it was read."""

    def __init__(self, root, cards: list[object] | Exception) -> None:
        self.root = root
        self.cards = cards
        self.filed_when_read: list[str] = []

    def __call__(self, store: object, label_of: object = None) -> SimpleNamespace:
        self.filed_when_read.append(str(kept(self.root)["Up-card-2025-09-04.pdf"]["account_ref"]))
        if isinstance(self.cards, Exception):
            raise self.cards
        return SimpleNamespace(cards=tuple(self.cards), settled=0, answered=())


class TestHowManyStillNeedADecision:
    def test_Answer_WhenNoFlagIsOpenAfterTheImport_SaysNothingAboutReview(
        self, served, monkeypatch
    ):
        base, root = served
        queue = _Queue(root, [])
        monkeypatch.setattr("obdi.review_flags.build_queue", queue)
        keep(base, "Up-card-2025-09-04.pdf", middle_statement())

        page = parse(press(base, root, "Up-card-2025-09-04.pdf", "up-card").text)
        said = flat(without_counted_fold(page)).lower()

        assert "needs a decision" not in said
        assert "need a decision" not in said
        assert "for review" not in said
        assert queue.filed_when_read == ["up-card"], "read after the import, not before"

    def test_Answer_WhenAFlagIsStillOpenForTheAccount_SaysHowManyAndWhereToDecide(
        self, served, monkeypatch
    ):
        base, root = served
        cards = [SimpleNamespace(ref="up-card"), SimpleNamespace(ref="other-card")]
        monkeypatch.setattr("obdi.review_flags.build_queue", _Queue(root, cards))
        keep(base, "Up-card-2025-09-04.pdf", middle_statement())

        page = parse(press(base, root, "Up-card-2025-09-04.pdf", "up-card").text)
        lines = outcome_lines(page)

        assert "1 transaction needs a decision" in lines[0]
        links = [a for a in elements(page, "a") if a.text() == "Decide"]
        assert [a.attrs["href"] for a in links] == ["/review-flags"]

    def test_Answer_WhenTheOpenFlagsCannotBeRead_SaysSoInsteadOfSayingNothing(
        self, served, monkeypatch
    ):
        base, root = served
        monkeypatch.setattr(
            "obdi.review_flags.build_queue", _Queue(root, RuntimeError("held"))
        )
        keep(base, "Up-card-2025-09-04.pdf", middle_statement())

        page = parse(press(base, root, "Up-card-2025-09-04.pdf", "up-card").text)

        assert "could not be read just now" in " ".join(outcome_lines(page))
