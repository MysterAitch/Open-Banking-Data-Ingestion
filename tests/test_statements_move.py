"""A kept statement given the wrong account is moved from the Statements page.

On the real store a statement was read in to the wrong account by a wrong pre-selection. The only
way to move it was the "Landed under the wrong account?" form on the artefact's own page, an
address nothing led to. The scenes below are that mistake over invented statements: assign to the
wrong account, see it on the Statements page, move it there.
"""

# ruff: noqa: F401, F811
# The page's own fixtures (`db`, `serve`) are imported from the file that built them, and a
# fixture used by name reads to the linter as unused and then as redefined.

from __future__ import annotations

import re
from pathlib import Path

import httpx
import pytest

from coverage_page_world import repeated_lines
from obdi.ingest.accounts import AccountRecord, AccountRef
from obdi.ingest.store import Store
from obdi.read.account_names import AccountShown, AccountsShown
from obdi.web_statements import statements_body
from page_dom import Node, elements, parse
from test_kept_statements_page import (
    DISTINCTIVE,
    SHORT_MONTH,
    UNASSIGNED,
    _account_of,
    _id_of,
    _keep,
    _post,
    db,
    serve,
)
from test_pdf_import import SANTANDER_AS_WRITTEN

WRONG, RIGHT = "wrong-card", "right-card"
FIGURE = re.compile(r"\d[\d,]*\.\d\d(?![\d%a-z])")


@pytest.fixture
def declared(db: Path) -> Path:
    with Store(db) as store:
        for ref, label in ((WRONG, "Wrong card"), (RIGHT, "Right card")):
            store.declare_account(AccountRecord(ref=AccountRef(ref), label=label))
    return db


def move_forms(page: Node) -> list[Node]:
    return [f for f in elements(page, "form") if f.attrs.get("action") == "/refile-artefact"]


def field(form: Node, name: str) -> Node:
    return next(n for n in elements(form, "input") if n.attrs.get("name") == name)


def form_for(page: Node, ident: int) -> Node:
    return next(f for f in move_forms(page) if field(f, "id").attrs["value"] == str(ident))


def assigned_wrongly(db: Path, name: str = "2026.05 - Example Card.pdf") -> int:
    with Store(db) as store:
        _keep(store, SANTANDER_AS_WRITTEN, name)
    return _id_of(db, name)


class TestMovingFromTheStatementsPage:
    def test_Statement_AssignedToTheWrongAccount_CanBeMovedFromTheStatementsPage(
        self, serve, declared
    ):
        ident = assigned_wrongly(declared)
        base = serve(declared)
        said = _post(base, "/statement-assign", {"artefact": str(ident), "account": WRONG})
        assert said.status_code == 200
        assert _account_of(declared, "2026.05 - Example Card.pdf") == WRONG

        page = parse(httpx.get(f"{base}/statements", timeout=60).text)
        form = form_for(page, ident)
        moved = _post(base, "/refile-artefact", {
            "id": field(form, "id").attrs["value"], "account": RIGHT, "confirm": "yes",
        })

        assert moved.status_code == 200
        assert "Rebuild from raw" in moved.text
        assert _account_of(declared, "2026.05 - Example Card.pdf") == RIGHT

    def test_MoveForm_OnTheStatementsPage_HasTheFieldsTheMoveAnswers(self, serve, declared):
        ident = assigned_wrongly(declared)
        base = serve(declared)
        _post(base, "/statement-assign", {"artefact": str(ident), "account": WRONG})

        form = form_for(parse(httpx.get(f"{base}/statements", timeout=60).text), ident)

        assert form.attrs["method"] == "post"
        select = next(elements(form, "select"))
        assert select.attrs["name"] == "account"
        offered = {o.attrs.get("value", "") for o in elements(select, "option")}
        assert {WRONG, RIGHT} <= offered
        assert field(form, "confirm").attrs["type"] == "checkbox"
        assert "required" in field(form, "confirm").attrs

    def test_Move_WithoutTheConfirmation_IsRefusedAndTheStatementStaysWhereItWas(
        self, serve, declared
    ):
        ident = assigned_wrongly(declared)
        base = serve(declared)
        _post(base, "/statement-assign", {"artefact": str(ident), "account": WRONG})

        refused = _post(base, "/refile-artefact", {"id": str(ident), "account": RIGHT})

        assert refused.status_code == 400
        assert _account_of(declared, "2026.05 - Example Card.pdf") == WRONG

    def test_StatementsPage_WithAStatementStillWaitingForAnAccount_OffersNoMoveForIt(
        self, serve, declared
    ):
        with Store(declared) as store:
            _keep(store, SANTANDER_AS_WRITTEN, "2026.05 - Example Card.pdf")

        page = parse(httpx.get(f"{serve(declared)}/statements", timeout=60).text)

        assert move_forms(page) == []
        assert _account_of(declared, "2026.05 - Example Card.pdf") == UNASSIGNED

    def test_StatementsPage_OffersAMoveForEachAssignedStatementAndOnlyThose(
        self, serve, declared
    ):
        with Store(declared) as store:
            _keep(store, SANTANDER_AS_WRITTEN, "2026.05 - Example Card.pdf", account=WRONG)
            _keep(store, SHORT_MONTH, "2026.04 - Example Card.pdf", account=RIGHT)
            _keep(store, DISTINCTIVE, "2026.08 - Example Card.pdf")

        page = parse(httpx.get(f"{serve(declared)}/statements", timeout=60).text)

        assert sorted(field(f, "id").attrs["value"] for f in move_forms(page)) == sorted(
            str(_id_of(declared, name))
            for name in ("2026.05 - Example Card.pdf", "2026.04 - Example Card.pdf")
        )

    def test_StatementsPage_WhenViewed_HoldsNoFigureAndNoPayeeBesideTheMoveForm(
        self, serve, declared
    ):
        with Store(declared) as store:
            _keep(store, DISTINCTIVE, "August card.pdf", account=WRONG)

        response = httpx.get(f"{serve(declared)}/statements", timeout=60)
        visible = re.sub(r"<style>.*?</style>", "", response.text, flags=re.S)

        assert len(move_forms(parse(response.text))) == 1
        assert "ZEBRAQUARTZ" not in response.text
        assert "7,654.32" not in response.text
        assert FIGURE.search(visible) is None
        assert response.headers.get("cache-control") != "public"

    def test_StatementsPage_WhereMovingIsNotWired_OffersNoMoveForm(self):
        entry = {
            "id": 1, "origin": "a.pdf", "fetched_at": "2026-09-01T09:00:00", "account_ref": WRONG,
            "parser": "santander-cc-pdf", "rows": 3, "refusal": "", "listed_days": [],
            "names": [], "sections": [],
        }
        names = AccountsShown([AccountShown.named(WRONG, "Wrong card")])

        body = statements_body(
            [entry], names=names, options={WRONG: "Wrong card"}, can_assign=True,
            can_section_assign=True, can_move=False,
        )

        assert move_forms(parse(body)) == []

    def test_StatementsPage_WithManyAssigned_SaysTheMoveExplanationOnceNotOnEachStatement(self):
        entries = [
            {
                "id": n, "origin": f"2025.{n:02d} - Card.pdf",
                "fetched_at": "2026-09-01T09:00:00", "account_ref": WRONG,
                "parser": "santander-cc-pdf", "rows": 3, "refusal": "", "listed_days": [],
                "names": [], "sections": [],
            }
            for n in range(1, 9)
        ]
        names = AccountsShown([AccountShown.named(WRONG, "Wrong card")])

        body = statements_body(
            entries, names=names, options={WRONG: "Wrong card"}, can_assign=True,
            can_section_assign=True, can_move=True,
        )
        page = parse(body)
        assert len(move_forms(page)) == 8
        # A chooser lists the same options each time; that is data, not a sentence said again.
        for node in list(elements(page, "select")):
            node.children.clear()

        assert repeated_lines(page) == {}

    def test_StatementsPage_ForADocumentOfSeveralAccounts_OffersNoWholeDocumentMove(self):
        entry = {
            "id": 1, "origin": "all.pdf", "fetched_at": "2026-09-01T09:00:00",
            "account_ref": UNASSIGNED, "parser": "credit-union-pdf", "rows": None,
            "refusal": "", "listed_days": [], "names": [],
            "sections": [
                {"token": "a", "label": "Saver", "rows": 2, "refusal": "", "account": WRONG,
                 "listed_days": ["2025-01-01", "2025-01-02"], "suggested": "", "given": []},
            ],
        }
        names = AccountsShown([AccountShown.named(WRONG, "Wrong card")])

        body = statements_body(
            [entry], names=names, options={WRONG: "Wrong card"}, can_assign=True,
            can_section_assign=True, can_move=True,
        )

        assert move_forms(parse(body)) == []
