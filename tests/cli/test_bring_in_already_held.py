# ruff: noqa: F401, F811
# The `served` fixture is imported from the file that built it; used by name it reads to the
# linter as unused and then as redefined.
"""Bring in says, before the press, when a statement would add nothing to the account it is for.

On the real store ten credit union statements were uploaded and five of them were single-account
documents whose every transaction was already held from the sections of "all accounts" documents:
each press answered "parsed 13, new 0, matched 13", and nothing on the form had hinted at it. The
scenes below are that, over the invented credit union documents of `test_bring_in_assign`: month 6
of the "all accounts" document was read in to the saver and the pot, so a saver-only document of
month 6 holds nothing the saver does not; one of month 7 holds a transaction the saver has never
seen.
"""

from __future__ import annotations

import re
import sqlite3
from datetime import date
from pathlib import Path

import httpx

from credit_union_documents import Move, document, pdf, section
from obdi.cli import build_web_config
from page_dom import Node, elements, parse
from test_bring_in_assign import (
    PLANTED_PAYEE,
    SAVER,
    assign_form,
    chosen,
    flat,
    kept,
    letter,
    part,
    rows_of,
    santander,
    saver_only,
    served,
)

MONEY_FIGURE = re.compile(r"[£€$]\s*[-\d]|\d[\d,]*\.\d\d(?![\d%a-z])")
HELD_SAYING = "reading it in adds nothing but the statement's own balances"


def upload(base: str, *files: tuple[str, bytes]) -> Node:
    return parse(httpx.post(
        f"{base}/bring-in", files=[part(name, payload) for name, payload in files], timeout=300
    ).text)


def held_lines(item: Node) -> list[str]:
    return [flat(p) for p in elements(item, "p") if "bi-held" in p.classes]


def table_sizes(root: Path) -> dict[str, int]:
    with sqlite3.connect(root / "store.sqlite3") as connection:
        names = [
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        ]
        return {
            # The names are read from sqlite_master above, never from input.
            name: int(connection.execute(f'SELECT count(*) FROM "{name}"').fetchone()[0])  # noqa: S608
            for name in names
        }


class TestAStatementThatWouldAddNothing:
    def test_Statement_WhoseEveryTransactionIsAlreadyHeld_SaysReadingItInAddsNothing(
        self, served
    ):
        base, _ = served

        item = rows_of(assign_form(upload(base, ("Saver-2025-06.pdf", saver_only(6)))))[
            "Saver-2025-06.pdf"
        ]

        assert chosen(item) == SAVER
        assert held_lines(item) == [
            f"Its one transaction is already held by Regular saver {SAVER}; {HELD_SAYING}."
        ]

    def test_Statement_OfSeveralTransactionsAllHeld_SaysHowManyAndNamesTheAccount(self, served):
        base, root = served
        moves = [Move(f"0{day}/08/2025", "DD Lodgement", 100 * day) for day in range(1, 5)]
        period = "01/08/2025 to 28/08/2025"
        everything = pdf(
            document(section("Regular Saver", 5000, moves, period=period),
                     section("Holiday Pot", 900, [Move("05/08/2025", "Internet Transfer", 50)],
                             period=period)),
            step=5.5,
        )
        saver_alone = pdf(document(section("Regular Saver", 5000, moves, period=period)), step=5.5)
        upload(base, ("All-accounts-2025-08.pdf", everything))
        held = kept(root)["All-accounts-2025-08.pdf"]
        token = next(p["token"] for p in held["sections"] if p["label"] == "Regular Saver")  # type: ignore[union-attr]
        httpx.post(f"{base}/statements-assign", timeout=300,
                   data={f"section-{held['id']}-{token}": SAVER})

        item = rows_of(assign_form(upload(base, ("Saver-2025-08.pdf", saver_alone))))[
            "Saver-2025-08.pdf"
        ]

        assert held_lines(item) == [
            f"Every one of its 4 transactions is already held by Regular saver {SAVER}; "
            f"{HELD_SAYING}."
        ]

    def test_Statement_WithATransactionTheGuessedAccountDoesNotHold_SaysNothingOfIt(
        self, served
    ):
        base, _ = served

        item = rows_of(assign_form(upload(base, ("Saver-2025-07.pdf", saver_only(7)))))[
            "Saver-2025-07.pdf"
        ]

        assert chosen(item) == SAVER
        assert held_lines(item) == []
        assert HELD_SAYING not in flat(item)

    def test_Upload_OfOneHeldStatementAndOneNewOne_SaysItOfTheHeldOneAlone(self, served):
        base, _ = served

        rows = rows_of(assign_form(upload(
            base, ("Saver-2025-06.pdf", saver_only(6)), ("Saver-2025-07.pdf", saver_only(7)),
        )))

        assert len(held_lines(rows["Saver-2025-06.pdf"])) == 1
        assert held_lines(rows["Saver-2025-07.pdf"]) == []

    def test_Statement_WithNoAccountGuessed_SaysNothingOfWhatIsHeld(self, served):
        base, _ = served

        item = rows_of(assign_form(upload(base, ("Notes-a.pdf", letter("a topic")))))[
            "Notes-a.pdf"
        ]

        assert list(elements(item, "select")) == []
        assert held_lines(item) == []

    def test_Statement_ForAnAccountThatHoldsNoneOfItsTransactions_SaysNothingOfWhatIsHeld(
        self, served
    ):
        base, _ = served

        rows = rows_of(assign_form(upload(
            base, ("Other-card-2026-09.pdf", santander(date(2026, 9, 11), 1433)),
        )))

        assert chosen(rows["Other-card-2026-09.pdf"]) == "other-card"
        assert held_lines(rows["Other-card-2026-09.pdf"]) == []


class TestTheWordsAreQuietAndHoldNoValue:
    def test_Page_WithAHeldStatement_NamesTheCountAndNoAmountOrPayee(self, served):
        base, _ = served
        response = httpx.post(
            f"{base}/bring-in", files=[part("Saver-2025-06.pdf", saver_only(6))], timeout=300
        )
        visible = re.sub(r"<style>.*?</style>", "", response.text, flags=re.S)

        assert HELD_SAYING in flat(parse(response.text))
        assert MONEY_FIGURE.search(visible) is None
        assert PLANTED_PAYEE not in response.text
        assert "DD Lodgement" not in response.text


class TestAskingWritesNothing:
    def test_Upload_OfAHeldStatement_ReadsNothingInAndLeavesEveryOtherTableAsItWas(
        self, served
    ):
        base, root = served
        upload(base, ("Saver-2025-06.pdf", saver_only(6)))
        before = table_sizes(root)

        upload(base, ("Saver-2025-06.pdf", saver_only(6)))

        assert table_sizes(root) == before
        assert kept(root)["Saver-2025-06.pdf"]["account_ref"] == "(unassigned)"

    def test_PreviewHook_ForAHeldStatement_CountsWithoutWriting(self, served):
        base, root = served
        upload(base, ("Saver-2025-06.pdf", saver_only(6)))
        wired = build_web_config(root / "store.sqlite3")
        assert wired is not None and wired.preview_kept_statement is not None
        ident = int(str(kept(root)["Saver-2025-06.pdf"]["id"]))
        before = table_sizes(root)

        preview = wired.preview_kept_statement(ident, SAVER)

        assert preview is not None
        assert (preview.merged, preview.new) == (1, 0)
        assert table_sizes(root) == before

    def test_PreviewHook_ForAnAccountThatHoldsNothing_CountsEveryTransactionAsNew(self, served):
        base, root = served
        upload(base, ("Saver-2025-06.pdf", saver_only(6)))
        wired = build_web_config(root / "store.sqlite3")
        assert wired is not None and wired.preview_kept_statement is not None
        ident = int(str(kept(root)["Saver-2025-06.pdf"]["id"]))

        preview = wired.preview_kept_statement(ident, "up-card")

        assert preview is not None
        assert (preview.merged, preview.new) == (0, 1)

    def test_PreviewHook_ForAnIdThatIsNotAKeptStatement_SaysItCannotCount(self, served):
        _, root = served
        wired = build_web_config(root / "store.sqlite3")
        assert wired is not None and wired.preview_kept_statement is not None

        assert wired.preview_kept_statement(999999, SAVER) is None
