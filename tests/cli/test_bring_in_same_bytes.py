# ruff: noqa: F401, F811
# The `served` fixture is imported from the file that built it; used by name it reads to the
# linter as unused and then as redefined.
"""Identical bytes are one statement, said at the upload, and a second copy of the same days is
said to be a second witness.

The owner uploaded a PDF whose bytes were already held and filed under an account. The answer said
"received" and offered a chooser, and pressing it folded the new row into the one already filed:
the "files kept" count fell by one with no sentence. His words: the duplication should be caught
as part of the upload, before it can be linked to an account. So the upload lands nothing for
bytes already held, under any account, and says so first.

The household is `test_bring_in_assign`'s: `Up-card-2025-09.pdf` is held under `up-card`. The
scenes were decided before the first run:

  * the same bytes under another name, unscoped or scoped to any account, lead with "Already held -
    read in to Up card ... as Up-card-2025-09.pdf", land no row, leave the kept count alone, and
    ask no question;
  * the same bytes as a statement still waiting for an account lead with "Already kept, waiting
    for an account, as <its name>"; the one form lists it once, whether the second copy came in
    a later press or the same one;
  * a statement of other bytes that lists the very days an account's held statement lists says,
    in its row, that it adds a second witness; one that lists other days does not.
"""

from __future__ import annotations

import re
import sqlite3
from datetime import datetime
from pathlib import Path

import httpx

from credit_union_documents import Move, document, pdf, section
from obdi.cli import build_web_config
from page_dom import Node, elements, parse
from test_bring_in_assign import (
    SAVER,
    D,
    assign_form,
    chosen,
    flat,
    kept,
    part,
    rows_of,
    santander,
    served,
)
from test_bring_in_kept_count import link_count, summary_count

HELD_NAME = "Up-card-2025-09.pdf"
HELD_BYTES = santander(D(2025, 9, 10), 950)


def raw_artefacts(root: Path) -> int:
    with sqlite3.connect(root / "store.sqlite3") as connection:
        return int(connection.execute("SELECT count(*) FROM raw_artefacts").fetchone()[0])


def upload(base: str, *files: tuple[str, bytes], account: str = "") -> Node:
    return parse(httpx.post(
        f"{base}/bring-in",
        data={"account": account} if account else None,
        files=[part(name, payload) for name, payload in files],
        timeout=300,
    ).text)


def tight(node: Node) -> str:
    """A paragraph's words, with the space a code span's text leaves inside brackets removed."""
    return flat(node).replace("( ", "(").replace(" )", ")")


def leads(page: Node) -> list[str]:
    return [tight(p) for p in elements(page, "p") if "bi-already" in p.classes]


def forms_asking(page: Node) -> list[Node]:
    return [f for f in elements(page, "form") if f.attrs.get("action") == "/statements-assign"]


def today() -> str:
    return datetime.now().astimezone().date().isoformat()


class TestIdenticalBytesAlreadyFiledUnderAnAccount:
    def test_Upload_OfBytesHeldUnderAnAccount_LeadsWithAlreadyHeldAndLandsNothing(self, served):
        base, root = served
        before_rows, before_kept = raw_artefacts(root), summary_count(base)

        answer = upload(base, ("Copy-of-up-card.pdf", HELD_BYTES))

        assert leads(answer) == [
            f"Copy-of-up-card.pdf: Already held - read in to Up card on {today()} "
            f"as {HELD_NAME}."
        ]
        assert raw_artefacts(root) == before_rows
        assert summary_count(base) == before_kept == link_count(answer)

    def test_Upload_OfBytesHeldUnderAnAccount_AsksNoQuestionAndOffersNoChooser(self, served):
        base, _ = served

        answer = upload(base, ("Copy-of-up-card.pdf", HELD_BYTES))

        assert forms_asking(answer) == []
        assert [
            s for s in elements(answer, "select") if s.attrs.get("name", "").startswith("account-")
        ] == []

    def test_Upload_OfBytesHeldUnderAnAccountAndANewStatement_AsksAboutTheNewOneOnly(self, served):
        base, root = served
        before_rows = raw_artefacts(root)

        answer = upload(
            base,
            ("Copy-of-up-card.pdf", HELD_BYTES),
            ("Up-card-2026-08.pdf", santander(D(2026, 8, 10), 1211)),
        )

        assert list(rows_of(assign_form(answer))) == ["Up-card-2026-08.pdf"]
        assert len(leads(answer)) == 1
        assert raw_artefacts(root) == before_rows + 1

    def test_Upload_ScopedToAnotherAccount_SaysTheSameAndFilesNothingThere(self, served):
        base, root = served
        before_rows = raw_artefacts(root)

        answer = upload(base, ("Copy-of-up-card.pdf", HELD_BYTES), account="other-card")

        assert leads(answer) == [
            f"Copy-of-up-card.pdf: Already held - read in to Up card on {today()} "
            f"as {HELD_NAME}."
        ]
        assert raw_artefacts(root) == before_rows
        assert link_count(answer) == summary_count(base)

    def test_KeptCount_AcrossUploadingHeldBytesAndAPress_NeverChanges(self, served):
        base, _ = served
        before = summary_count(base)

        answer = upload(base, ("Copy-of-up-card.pdf", HELD_BYTES))

        assert link_count(answer) == before
        assert summary_count(base) == before


class TestIdenticalBytesStillWaitingForAnAccount:
    def test_Upload_OfBytesKeptAndUnassigned_LeadsWithAlreadyKeptAndListsTheStatementOnce(
        self, served
    ):
        base, root = served
        fresh = santander(D(2026, 8, 10), 1211)
        first = upload(base, ("Up-card-2026-08.pdf", fresh))
        assert list(rows_of(assign_form(first))) == ["Up-card-2026-08.pdf"]
        before_rows = raw_artefacts(root)

        again = upload(base, ("Same-again.pdf", fresh))

        assert leads(again) == [
            "Same-again.pdf: Already kept, waiting for an account, as Up-card-2026-08.pdf."
        ]
        assert list(rows_of(assign_form(again))) == ["Up-card-2026-08.pdf"]
        assert raw_artefacts(root) == before_rows
        assert link_count(again) == summary_count(base)

    def test_Upload_OfTheSameBytesTwiceInOnePress_ListsOneRowAndSaysTheSecondIsAlreadyKept(
        self, served
    ):
        base, root = served
        before_rows = raw_artefacts(root)
        fresh = santander(D(2026, 8, 10), 1211)

        answer = upload(base, ("First.pdf", fresh), ("Second.pdf", fresh))

        assert list(rows_of(assign_form(answer))) == ["First.pdf"]
        assert leads(answer) == [
            "Second.pdf: Already kept, waiting for an account, as First.pdf."
        ]
        assert raw_artefacts(root) == before_rows + 1

    def test_Upload_OfSeveralWaitingFilesAgain_SaysSoOnceNotOncePerFile(self, served):
        """Re-sending a batch already kept (a folder chosen twice) is ten files already waiting;
        ten lines saying so pushed the form's first chooser off a phone's first screen, and say
        one thing ten times. One line, and the rows beneath are the files."""
        base, root = served
        files = [
            (f"Statement-{index}.pdf", santander(D(2026, 8, 10), 1300 + index))
            for index in range(3)
        ]
        upload(base, *files)
        before_rows = raw_artefacts(root)

        resent = [(f"Again-{index}.pdf", data) for index, (_, data) in enumerate(files)]
        again = upload(base, *resent)

        assert leads(again) == [
            "3 files were already kept, waiting for an account; they are the rows below."
        ]
        assert len(list(rows_of(assign_form(again)))) == 3
        assert raw_artefacts(root) == before_rows

    def test_Upload_OfKeptBytesScopedToAnAccount_ReadsTheKeptStatementInAsAskedWithoutAClaim(
        self, served
    ):
        base, root = served
        fresh = santander(D(2026, 8, 10), 1211)
        upload(base, ("Up-card-2026-08.pdf", fresh))
        before_rows = raw_artefacts(root)

        answer = upload(base, ("Same-again.pdf", fresh), account="up-card")

        assert leads(answer) == []
        assert "Same-again.pdf: read in to" in flat(answer)
        assert raw_artefacts(root) == before_rows
        assert kept(root)["Up-card-2026-08.pdf"]["account_ref"] == "up-card"

    def test_Upload_OfDifferentBytes_IsKeptAndSaysNothingIsAlreadyHeld(self, served):
        base, root = served
        before_rows = raw_artefacts(root)

        answer = upload(base, ("Up-card-2025-09-other.pdf", santander(D(2025, 9, 10), 951)))

        assert leads(answer) == []
        assert raw_artefacts(root) == before_rows + 1


MOVES = [Move(f"0{day}/08/2025", "DD Lodgement", 100 * day) for day in range(1, 5)]


def saver_document(moves: list[Move], period: str) -> bytes:
    return pdf(document(section("Regular Saver", 5000, moves, period=period)), step=5.5)


def hold_saver_august(base: str, root: Path) -> None:
    everything = pdf(
        document(
            section("Regular Saver", 5000, MOVES, period="01/08/2025 to 28/08/2025"),
            section("Holiday Pot", 900, [Move("05/08/2025", "Internet Transfer", 50)],
                    period="01/08/2025 to 28/08/2025"),
        ),
        step=5.5,
    )
    upload(base, ("All-accounts-2025-08.pdf", everything))
    wired = build_web_config(root / "store.sqlite3")
    assert wired is not None and wired.assign_statement_section is not None
    held = kept(root)["All-accounts-2025-08.pdf"]
    token = next(p["token"] for p in held["sections"] if p["label"] == "Regular Saver")  # type: ignore[union-attr]
    said = wired.assign_statement_section(int(str(held["id"])), str(token), SAVER)
    assert "assigned to" in said, said


def witness_lines(item: Node) -> list[str]:
    return [tight(p) for p in elements(item, "p") if "bi-witness" in p.classes]


class TestAnotherCopyOfTheSameStatement:
    def test_Statement_OfOtherBytesListingTheSameDaysAsOneHeld_NamesItAsASecondWitness(
        self, served
    ):
        base, root = served
        hold_saver_august(base, root)

        form = assign_form(
            upload(base, ("Saver-2025-08-certified.pdf",
                          saver_document(MOVES, "01/08/2025 to 29/08/2025")))
        )
        item = rows_of(form)["Saver-2025-08-certified.pdf"]

        assert chosen(item) == SAVER
        assert witness_lines(item) == [
            "A statement for Regular saver credit-union-saver covering 2025-08-01 to 2025-08-04 "
            "is already held (All-accounts-2025-08.pdf, account Regular Saver); reading this "
            "one in adds a second witness to its 4 transactions and its own balances, nothing "
            "new."
        ]

    def test_Statement_ListingOtherDaysWithEveryTransactionHeld_SaysOnlyThatEveryOneIsHeld(
        self, served
    ):
        base, root = served
        hold_saver_august(base, root)

        form = assign_form(
            upload(base, ("Saver-2025-08-part.pdf",
                          saver_document(MOVES[:2], "01/08/2025 to 02/08/2025")))
        )
        item = rows_of(form)["Saver-2025-08-part.pdf"]

        assert witness_lines(item) == []
        assert [flat(p) for p in elements(item, "p") if "bi-held" in p.classes] == [
            f"Every one of its 2 transactions is already held by Regular saver {SAVER}; "
            "reading it in adds nothing but the statement's own balances."
        ]

    def test_Statement_WhenSeveralAccountsHoldStatements_NamesOnlyTheGuessedAccountsTwin(
        self, served
    ):
        base, root = served
        hold_saver_august(base, root)
        # Held under up-card, listing 2025-09-05: a copy of it is guessed for that account, and
        # the credit union's statement beside it is not named as its twin.
        form = assign_form(
            upload(base, ("Up-card-2026-09.pdf", santander(D(2025, 9, 10), 951)))
        )
        item = rows_of(form)["Up-card-2026-09.pdf"]

        assert chosen(item) == "up-card"
        assert len(witness_lines(item)) == 1
        assert "Up-card-2025-09.pdf" in witness_lines(item)[0]
        assert "credit-union-saver" not in witness_lines(item)[0]
