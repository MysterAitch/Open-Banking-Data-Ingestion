"""One account of an "all accounts" statement, given the wrong account, is moved from the
Statements page.

A whole statement could be moved from there since 0.4.346; a section could only be assigned once,
because the store refused a second answer and nothing took back the rows it had contributed. The
scenes are that mistake over the invented nine-account document: assign the Regular Saver to the
wrong account, see the move beside it, move it, and find its rows and its declared assignment
under the right one - and still there after the store is rebuilt from the raw evidence.
"""

from __future__ import annotations

import re
from pathlib import Path

import httpx
import pytest

from credit_union_documents import nine_accounts, pdf
from landing import rebuild_from_raw
from obdi.ingest.accounts import AccountRecord, AccountRef
from obdi.ingest.parsers.credit_union_pdf import section_key
from obdi.ingest.store import Store
from obdi.verify.statement_sections import section_token
from page_dom import Node, elements, parse
from section_harness import UNASSIGNED, config, environment, holdings, keep, serve_config, total

WRONG, RIGHT = "wrong-saver", "right-saver"
SAVER_KEY = section_key("Regular Saver")
CLUB_KEY = section_key("Christmas Club")
SAVER_ROWS_TOTAL = 2500 + 249 - 30000
FIGURE = re.compile(r"\d[\d,]*\.\d\d(?![\d%a-z])")


@pytest.fixture
def db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    environment(monkeypatch, tmp_path)
    path = tmp_path / "store.sqlite3"
    with Store(path) as store:
        for ref, label in ((WRONG, "Wrong saver"), (RIGHT, "Right saver")):
            store.declare_account(AccountRecord(ref=AccountRef(ref), label=label))
    return path


@pytest.fixture
def nine(db: Path) -> tuple[Path, int]:
    with Store(db) as store:
        return db, keep(store, pdf(nine_accounts(), step=5.5), "all accounts.pdf")


@pytest.fixture
def serve(db: Path):
    stops = []

    def start() -> str:
        base, stop = serve_config(config(db))
        stops.append(stop)
        return base

    yield start
    for stop in stops:
        stop()


def assignments(db: Path) -> dict[str, str]:
    with Store(db) as store:
        return {a.section_key: a.account_ref for a in store.statement_section_assignments()}


def move(base: str, artefact: int, key: str, **fields: str) -> httpx.Response:
    return httpx.post(
        f"{base}/statement-section-move",
        data={"artefact": str(artefact), "section": section_token(key), **fields},
        headers={"Origin": base},
        timeout=60,
    )


def move_forms(page: str) -> list[Node]:
    return [
        f
        for f in elements(parse(page), "form")
        if f.attrs.get("action") == "/statement-section-move"
    ]


def wrongly_assigned(db: Path, artefact: int) -> None:
    config(db).assign_statement_section(artefact, SAVER_KEY, WRONG)
    assert total(db, WRONG) == SAVER_ROWS_TOTAL


def test_SectionAssignedToTheWrongAccount_IsMovedAndItsRowsFollow(nine, serve):
    db, artefact = nine
    wrongly_assigned(db, artefact)
    held = holdings(db, WRONG)

    response = move(serve(), artefact, SAVER_KEY, account=RIGHT, confirm="yes")

    assert response.status_code == 200
    assert "Rebuild from raw" in response.text
    assert assignments(db) == {SAVER_KEY: RIGHT}
    assert holdings(db, RIGHT) == held
    assert holdings(db, WRONG) == {}
    assert total(db, RIGHT) == SAVER_ROWS_TOTAL


def test_MovedSection_AfterARebuildFromRaw_StillFillsTheAccountItWasMovedTo(nine, serve):
    db, artefact = nine
    wrongly_assigned(db, artefact)
    held = holdings(db, WRONG)
    move(serve(), artefact, SAVER_KEY, account=RIGHT, confirm="yes")

    with Store(db) as store:
        rebuild_from_raw(store)

    assert holdings(db, RIGHT) == held
    assert holdings(db, WRONG) == {}


def test_MovingASection_LeavesTheOtherSectionsOfTheDocumentWhereTheyWere(nine, serve):
    db, artefact = nine
    wired = config(db)
    wired.assign_statement_section(artefact, SAVER_KEY, WRONG)
    wired.assign_statement_section(artefact, CLUB_KEY, "other-account-of-the-pair")
    club = holdings(db, "other-account-of-the-pair")

    move(serve(), artefact, SAVER_KEY, account=RIGHT, confirm="yes")

    assert assignments(db) == {SAVER_KEY: RIGHT, CLUB_KEY: "other-account-of-the-pair"}
    assert holdings(db, "other-account-of-the-pair") == club


def test_MoveWithoutTheTick_IsRefusedAndTheSectionStaysWhereItWas(nine, serve):
    db, artefact = nine
    wrongly_assigned(db, artefact)

    response = move(serve(), artefact, SAVER_KEY, account=RIGHT)

    assert response.status_code == 400
    assert assignments(db) == {SAVER_KEY: WRONG}
    assert total(db, WRONG) == SAVER_ROWS_TOTAL


def test_MoveToANewTypedName_AsksBeforeItBecomesAnAccountAndMovesNothing(nine, serve):
    db, artefact = nine
    wrongly_assigned(db, artefact)

    response = move(serve(), artefact, SAVER_KEY, account_other="rite-saver", confirm="yes")

    assert response.status_code == 409
    assert assignments(db) == {SAVER_KEY: WRONG}


def test_MoveOfASectionNobodyAssigned_IsRefusedRatherThanReadingItIn(nine, serve):
    db, artefact = nine

    response = move(serve(), artefact, SAVER_KEY, account=RIGHT, confirm="yes")

    assert response.status_code == 400
    assert assignments(db) == {}
    assert holdings(db, RIGHT) == {}


def test_MoveToTheAccountItAlreadyHas_ChangesNothingAndSaysSo(nine, serve):
    db, artefact = nine
    wrongly_assigned(db, artefact)
    held = holdings(db, WRONG)

    response = move(serve(), artefact, SAVER_KEY, account=WRONG, confirm="yes")

    assert response.status_code == 200
    assert "already" in response.text
    assert holdings(db, WRONG) == held


def test_MoveOfASectionThatSharesItsAccountWithAnother_IsRefusedAndNothingMoves(nine, serve):
    db, artefact = nine
    wired = config(db)
    wired.assign_statement_section(artefact, SAVER_KEY, WRONG)
    wired.assign_statement_section(artefact, CLUB_KEY, WRONG)
    held = holdings(db, WRONG)

    response = move(serve(), artefact, SAVER_KEY, account=RIGHT, confirm="yes")

    assert response.status_code == 400
    assert assignments(db) == {SAVER_KEY: WRONG, CLUB_KEY: WRONG}
    assert holdings(db, WRONG) == held
    assert holdings(db, RIGHT) == {}


@pytest.mark.parametrize(
    "data",
    [
        {"section": "x", "account": RIGHT, "confirm": "yes"},
        {"artefact": "1", "account": RIGHT, "confirm": "yes"},
        {"artefact": "1", "section": "x", "confirm": "yes"},
        {"artefact": "abc", "section": "x", "account": RIGHT, "confirm": "yes"},
    ],
)
def test_MoveWithAPartMissing_IsRefused(nine, serve, data):
    base = serve()

    response = httpx.post(
        f"{base}/statement-section-move", data=data, headers={"Origin": base}, timeout=60
    )

    assert response.status_code == 400


def test_StatementsPage_OffersAMoveBesideEachAssignedSectionAndOnlyThose(nine, serve):
    db, artefact = nine
    config(db).assign_statement_section(artefact, SAVER_KEY, WRONG)

    page = httpx.get(f"{serve()}/statements", timeout=60).text

    (form,) = move_forms(page)
    fields = {n.attrs.get("name"): n for n in form.descendants() if n.attrs.get("name")}
    assert fields["artefact"].attrs["value"] == str(artefact)
    assert fields["section"].attrs["value"] == section_token(SAVER_KEY)
    assert fields["confirm"].attrs["type"] == "checkbox" and "required" in fields["confirm"].attrs
    assert fields["account"].tag == "select"


def test_StatementsPage_WithNothingAssigned_OffersNoSectionMove(nine, serve):
    page = httpx.get(f"{serve()}/statements", timeout=60).text

    assert move_forms(page) == []
    assert UNASSIGNED not in assignments(nine[0])


def test_StatementsPage_WhereMovingIsNotWired_OffersNoSectionMove(nine):
    db, artefact = nine
    config(db).assign_statement_section(artefact, SAVER_KEY, WRONG)
    wired = config(db)
    from dataclasses import replace

    base, stop = serve_config(replace(wired, move_statement_section=None))
    try:
        page = httpx.get(f"{base}/statements", timeout=60).text
    finally:
        stop()

    assert move_forms(page) == []


def test_StatementsPage_WithAMovableSection_HoldsNoFigureNoPayeeAndNoBalance(nine, serve):
    db, artefact = nine
    config(db).assign_statement_section(artefact, SAVER_KEY, WRONG)

    response = httpx.get(f"{serve()}/statements", timeout=60)
    visible = re.sub(r"<style>.*?</style>", "", response.text, flags=re.S)

    assert len(move_forms(response.text)) == 1
    for hidden in ("J SMITH", "527.49", "52749", "342.51", "34251", "800.00", "1,200.00"):
        assert hidden not in response.text, hidden
    assert FIGURE.search(visible) is None
