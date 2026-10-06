"""Bring in's one form says, a transaction at a time, what reading a statement in would do.

The owner asked, under each uploaded statement, for the matcher's dry run listed per transaction:
which are already held and by what, which are new, which could be the other leg of a transfer, and
which would raise a flag for a decision - so that he can press and decide afterwards, or not
press. Every answer below was decided before the first run, over one invented household:

  * `card-dry` (Dry card) holds an earlier Santander statement closing 2026-09-05 with one
    transaction on 2026-09-03, and two feed rows from `truelayer-booked` dated 2026-09-02 and
    2026-09-04;
  * `pot-dry` (Dry pot) holds one feed row dated 2026-09-06 that moved money in;
  * the uploaded statement (closing 2026-09-10, read by the same reader, so Dry card is guessed)
    lists five transactions:
      2026-09-02  the first feed row's payment                  - already held, by truelayer-booked
      2026-09-04  the second feed row's payment                 - already held, by truelayer-booked
      2026-09-05  a payment the same size as the earlier statement's, two days on
                                                                - would need a decision
      2026-09-06  a payment the size the pot received that day  - new, could be a leg of a
                                                                  transfer with Dry pot
      2026-09-07  a payment nothing resembles                   - new
    so the summary is "5 listed: 2 already held, 1 new, 1 could be one leg of a transfer, 1 would
    need a decision", and the row says it will ask for a decision on 1 transaction, from Today.

On a GET no amount and no payee appears (descriptions are masked as the ledger masks them), the
fold is closed, and rendering the page writes nothing to the store. "Show values" is a press that
answers with the same list, values shown, marked, and not kept by the browser.
"""

from __future__ import annotations

import re
import sqlite3
from collections.abc import Iterator
from datetime import date
from pathlib import Path

import httpx
import pytest

from coverage_page_world import repeated_lines
from fetch_gaps_world import MONTHS, _declare, _pounds, feed, ordinal
from obdi.store import Store
from obdi.synthetic_pdf import build_pdf
from page_dom import Node, elements, parse
from served_store import environment_for, served_store

D = date
DRY, POT = "card-dry", "pot-dry"
MONEY_FIGURE = re.compile(r"[£€$]\s*[-\d]|\d[\d,]*\.\d\d(?![\d%a-z])")
PAYEES = ("Held Zeppelin A", "Held Zeppelin B", "Repeat Zeppelin", "Leg Zeppelin", "Fresh Zeppelin")
AMOUNTS = ("22.00", "33.00", "15.00", "12.50", "44.10")


def statement(closing: date, opening: int, rows: list[tuple[date, str, int]]) -> bytes:
    lines = [
        "Santander UK plc. Registered Office: 2 Triton Square",
        f"Statement Date: {ordinal(closing)} {MONTHS[closing.month - 1]} {closing.year}"
        "      Page No: 1 / 1",
        "Account credit limit:            3,000.00",
        f"Balance brought forward from previous statement          {_pounds(opening)}",
        *(
            f"{ordinal(day)} {MONTHS[day.month - 1]} {payee}   {_pounds(minor)}"
            for day, payee, minor in rows
        ),
        f"Your new balance:                                        "
        f"{_pounds(opening + sum(minor for _, _, minor in rows))}",
    ]
    return build_pdf(lines)


def uploaded() -> bytes:
    return statement(
        D(2026, 9, 10),
        13000,
        [
            (D(2026, 9, 2), "Held Zeppelin A", 2200),
            (D(2026, 9, 4), "Held Zeppelin B", 3300),
            (D(2026, 9, 5), "Repeat Zeppelin", 1500),
            (D(2026, 9, 6), "Leg Zeppelin", 1250),
            (D(2026, 9, 7), "Fresh Zeppelin", 4410),
        ],
    )


def world(root: Path):
    def build(store: Store) -> None:
        _declare(store, DRY, "Dry card")
        _declare(store, POT, "Dry pot")
        feed(store, DRY, [(D(2026, 9, 2), -2200, "Held Zeppelin A"),
                          (D(2026, 9, 4), -3300, "Held Zeppelin B")], digest="dry-feed")
        feed(store, POT, [(D(2026, 9, 6), 1250, "Pot In Zeppelin")], digest="pot-feed")

    return build


@pytest.fixture
def served(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[tuple[str, Path]]:
    with served_store(tmp_path, world(tmp_path), bound=[]) as base:
        for name, value in environment_for(tmp_path).items():
            monkeypatch.setenv(name, value)
        earlier = statement(D(2026, 9, 5), 10000, [(D(2026, 9, 3), "Earlier Zeppelin", 1500)])
        said = httpx.post(
            f"{base}/bring-in",
            data={"account": DRY},
            files=[("file", ("Dry-card-2026-09-05.pdf", earlier, "application/pdf"))],
            timeout=300,
        )
        assert "read in to Dry card" in flat(parse(said.text)), said.text[:300]
        yield base, tmp_path


def upload(base: str) -> httpx.Response:
    return httpx.post(
        f"{base}/bring-in",
        files=[("file", ("Dry-new-2026-09.pdf", uploaded(), "application/pdf"))],
        timeout=300,
    )


def flat(node: Node) -> str:
    return re.sub(r" ([.,:;])", r"\1", node.text())


def row_of(response: httpx.Response) -> Node:
    page = parse(response.text)
    return next(li for li in elements(page, "li") if "bi-assign-file" in li.classes)


def fold_of(item: Node) -> Node:
    return next(d for d in elements(item, "details") if "bi-dry" in d.classes)


def lines_of(item: Node) -> list[str]:
    return [flat(li) for li in elements(fold_of(item), "li")]


def table_sizes(root: Path) -> dict[str, int]:
    with sqlite3.connect(root / "store.sqlite3") as connection:
        tables = connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
        names = [r[0] for r in tables.fetchall()]
        return {
            n: int(connection.execute(f'SELECT count(*) FROM "{n}"').fetchone()[0])  # noqa: S608
            for n in names
        }


def kept_id(root: Path, origin: str) -> int:
    with sqlite3.connect(root / "store.sqlite3") as connection:
        return int(connection.execute(
            "SELECT rowid FROM raw_artefacts WHERE origin = ?", (origin,)
        ).fetchone()[0])


def account_of(root: Path, origin: str) -> str:
    with sqlite3.connect(root / "store.sqlite3") as connection:
        return str(connection.execute(
            "SELECT account_ref FROM raw_artefacts WHERE origin = ?", (origin,)
        ).fetchone()[0])


SAME_SOURCE = (
    "stored as new, but 1 transaction(s) in this account match on amount and date and were "
    "kept apart only by the same source rule - confirm this is a repeated payment and not a "
    "duplicate report"
)


class TestEachTransactionSaysWhatReadingItInWouldDo:
    def test_Fold_ListsEveryTransactionWithItsOutcomeInPlainWords(self, served):
        base, _ = served

        lines = lines_of(row_of(upload(base)))

        assert [re.sub(r"^\S+ out \S+ \S+( \S)? - ", "", line) for line in lines] == [
            "already held - matches a truelayer-booked row",
            "already held - matches a truelayer-booked row",
            f"would need a decision: {SAME_SOURCE}",
            "new - could be one leg of a transfer with Dry pot",
            "new",
        ]
        assert [line[:10] for line in lines] == [
            "2026-09-02", "2026-09-04", "2026-09-05", "2026-09-06", "2026-09-07",
        ]
        assert all(" out " in line for line in lines)

    def test_Fold_AboveItsListSaysHowManyOfEachKind(self, served):
        base, _ = served

        fold = fold_of(row_of(upload(base)))
        said = flat(next(p for p in elements(fold, "p") if "bi-dry-says" in p.classes))

        assert said == (
            "5 listed: 2 already held, 1 new, 1 could be one leg of a transfer, "
            "1 would need a decision."
        )

    def test_Row_WhenAnyTransactionWouldNeedADecision_SaysItWillAskAndThatNothingIsReadInYet(
        self, served
    ):
        base, _ = served

        item = row_of(upload(base))
        asked = [flat(p) for p in elements(item, "p") if "bi-decide" in p.classes]

        assert asked == [
            "Reading it in will ask you to decide on 1 transaction, from Today; nothing is "
            "read in until you press."
        ]
        link = next(a for p in elements(item, "p") if "bi-decide" in p.classes
                    for a in elements(p, "a"))
        assert link.attrs["href"] == "/"

    def test_Row_WhenNoTransactionWouldNeedADecision_SaysNothingOfOne(self, served):
        base, _ = served
        calm = statement(
            D(2026, 9, 10), 13000,
            [(D(2026, 9, 2), "Held Zeppelin A", 2200), (D(2026, 9, 7), "Fresh Zeppelin", 4410)],
        )

        response = httpx.post(
            f"{base}/bring-in",
            files=[("file", ("Dry-calm-2026-09.pdf", calm, "application/pdf"))],
            timeout=300,
        )
        item = row_of(response)

        assert [p for p in elements(item, "p") if "bi-decide" in p.classes] == []
        assert "decision" not in flat(item)
        fold = fold_of(item)
        assert flat(next(p for p in elements(fold, "p") if "bi-dry-says" in p.classes)) == (
            "2 listed: 1 already held, 1 new."
        )

    def test_Fold_IsClosedUntilAsked(self, served):
        base, _ = served

        assert "open" not in fold_of(row_of(upload(base))).attrs

    def test_Row_ForAFileNoAccountIsGuessedFor_HasNoFoldAndNoDecisionLine(self, served):
        base, _ = served
        letter = build_pdf(["An invented letter", "It holds no statement at all."])

        item = row_of(httpx.post(
            f"{base}/bring-in",
            files=[("file", ("Letter-2026-09.pdf", letter, "application/pdf"))],
            timeout=300,
        ))

        assert [d for d in elements(item, "details") if "bi-dry" in d.classes] == []
        assert [p for p in elements(item, "p") if "bi-decide" in p.classes] == []


class TestTheFirstViewHoldsNoValueAndWritesNothing:
    def test_Page_StatesNoAmountNoPayeeAndNoFigure(self, served):
        base, _ = served
        response = upload(base)
        visible = re.sub(r"<style>.*?</style>", "", response.text, flags=re.S)

        for planted in (*PAYEES, *AMOUNTS):
            assert planted not in response.text, planted
        assert MONEY_FIGURE.search(visible) is None
        assert "<script" not in response.text

    def test_Page_RepeatsNoLineOfThreeWordsMoreThanTwice(self, served):
        base, _ = served
        page = parse(upload(base).text)
        for node in list(elements(page, "select")):
            node.children.clear()
        for node in list(elements(page, "p")):
            if "bi-guess" in node.classes or "bi-preview" in node.classes:
                node.children.clear()

        assert repeated_lines(page) == {}

    def test_RenderingThePage_WritesNothingToTheStore(self, served):
        base, root = served
        upload(base)
        before = table_sizes(root)

        upload(base)

        assert table_sizes(root) == before
        assert account_of(root, "Dry-new-2026-09.pdf") == "(unassigned)"


class TestShowValues:
    def press(self, base: str, root: Path, **fields: str) -> httpx.Response:
        ident = kept_id(root, "Dry-new-2026-09.pdf")
        chosen = {k.replace("ID", str(ident)): v for k, v in fields.items()}
        data = {"values-for": str(ident), **chosen}
        return httpx.post(f"{base}/statement-dry-run", data=data, timeout=300)

    def test_Press_AnswersWithTheSameListValuesShownAndMarked(self, served):
        base, root = served
        upload(base)

        response = self.press(base, root, **{"account-ID": DRY})
        said = flat(parse(response.text))

        assert response.status_code == 200
        assert response.headers["cache-control"] == "no-store"
        assert "VALUES ARE SHOWN" in said
        assert "5 listed: 2 already held, 1 new, 1 could be one leg of a transfer, " in said
        for planted in (*PAYEES, *AMOUNTS):
            assert planted in response.text, planted
        assert "already held - matches a truelayer-booked row" in said
        assert "Nothing was read in." in said

    def test_Press_ReadsNothingIn(self, served):
        base, root = served
        upload(base)
        before = table_sizes(root)

        self.press(base, root, **{"account-ID": DRY})

        assert table_sizes(root) == before
        assert account_of(root, "Dry-new-2026-09.pdf") == "(unassigned)"

    def test_Press_ReadsTheListAgainstTheAccountChosenAtThatMoment(self, served):
        base, root = served
        upload(base)

        said = flat(parse(self.press(base, root, **{"account-ID": POT}).text))

        assert "5 listed: 5 new" in said
        assert "already held" not in said

    def test_Press_WithNoAccountChosen_AsksForOneAndShowsNoValue(self, served):
        base, root = served
        upload(base)

        response = self.press(base, root, **{"account-ID": ""})

        assert response.status_code == 400
        assert "Choose an account for that statement first" in flat(parse(response.text))
        for planted in (*PAYEES, *AMOUNTS):
            assert planted not in response.text, planted

    def test_Press_NamingAStatementNotKept_IsRefusedAndShowsNoValue(self, served):
        base, _ = served

        response = httpx.post(
            f"{base}/statement-dry-run",
            data={"values-for": "999999", "account-999999": DRY},
            timeout=300,
        )

        assert response.status_code == 400
        for planted in (*PAYEES, *AMOUNTS):
            assert planted not in response.text, planted
