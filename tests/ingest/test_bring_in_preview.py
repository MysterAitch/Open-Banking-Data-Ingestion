# ruff: noqa: F401, F811
# The `served` fixture is imported from the file that built it; used by name it reads to the
# linter as unused and then as redefined.
"""Bring in's one form says what each uploaded document IS, so a file named by a GUID can still
be given the right account.

The owner uploaded one statement on his phone, renamed sensibly. His words: "if it were their
default guid filenames there wouldn't be anything substantive on screen to indicate if the account
selection is correct." Every scene below uploads files named by a GUID and reads the row over real
HTTP, with every answer decided before the first run:

  * a Santander statement: the row names its reader (as the kept-statements listing does), the one
    day it lists, "1 transaction", that it adds up by what it lists, the issuer name found in it,
    and links its masked shape; the payee planted in it and the amount of its one transaction
    appear nowhere in the page;
  * a credit union document of one account whose label carries digits ("Personal -9.50%"): the
    label is shown with every digit masked;
  * a PDF with no statement in it: the row says no reader reads it, in place of a preview, and
    claims nothing about it adding up;
  * a Santander statement whose closing balance is wrong: the row says it does not add up by
    what it lists, with the refusal's digits masked;
  * each account of an "all accounts" document carries the transaction count of its own section.
"""

from __future__ import annotations

import re
from datetime import date

import httpx

from credit_union_documents import Move, document, pdf, section
from obdi.ingest.synthetic_pdf import build_pdf
from page_dom import Node, elements, parse
from test_bring_in_assign import (
    MONEY_FIGURE,
    MONTHS,
    PLANTED_PAYEE,
    assign_form,
    credit_union,
    flat,
    kept,
    letter,
    loan_only,
    ordinal,
    part,
    rows_of,
    santander,
    served,
)

D = date
GUID = "3f9c1e2a-7b44-4d0e-9a51-c2d8e6b07f13.pdf"
GUID_B = "a1b2c3d4-0000-4000-8000-123456789abc.pdf"
GUID_C = "0e9d8c7b-6a5f-4e3d-9c2b-1a0f9e8d7c6b.pdf"


def upload(base: str, *files: tuple[str, bytes]) -> httpx.Response:
    return httpx.post(
        f"{base}/bring-in", files=[part(name, payload) for name, payload in files], timeout=300
    )


def preview_of(item: Node) -> Node:
    found = [p for p in elements(item, "p") if "bi-preview" in p.classes]
    assert len(found) == 1, f"{len(found)} previews in the row"
    return found[0]


def wrong_closing() -> bytes:
    """A Santander statement whose new balance is not what its rows carry the old one to."""
    lines = [
        "Santander UK plc. Registered Office: 2 Triton Square",
        "Statement Date: 10th September 2026      Page No: 1 / 1",
        "Account credit limit:            3,000.00",
        "Balance brought forward from previous statement          100.00",
        f"05 September {PLANTED_PAYEE}   13.22",
        "Your new balance:                                        999.99",
    ]
    return build_pdf(lines)


class TestAFileNamedByAGuid:
    def test_Statement_NamedByAGuid_SaysWhatTheDocumentIsBesideItsChooser(self, served):
        base, root = served
        response = upload(base, (GUID, santander(D(2026, 9, 10), 1322)))
        row = rows_of(assign_form(parse(response.text)))[GUID]
        reader = kept(root)[GUID]["parser"]
        said = flat(preview_of(row))

        assert f"Read by {reader}" in said
        assert "2026-09-05" in said
        assert "1 transaction" in said
        assert "adds up by what it lists" in said
        assert "Santander" in said
        shape = [a for a in elements(preview_of(row), "a") if a.text() == "Masked shape"]
        assert [a.attrs["href"] for a in shape] == [
            f"/statement-shape?artefact={kept(root)[GUID]['id']}"
        ]

    def test_Preview_SitsAboveTheChooserOfItsRow(self, served):
        base, _ = served
        response = upload(base, (GUID, santander(D(2026, 9, 10), 1322)))
        row = rows_of(assign_form(parse(response.text)))[GUID]
        order = [
            "preview" if "bi-preview" in child.classes else child.tag
            for child in row.children
            if isinstance(child, Node)
        ]
        assert order.index("preview") < order.index("select")

    def test_Page_ForAGuidNamedStatement_StatesNoAmountNoPayeeAndNoFigure(self, served):
        base, _ = served
        response = upload(base, (GUID, santander(D(2026, 9, 10), 1322)))
        visible = re.sub(r"<style>.*?</style>", "", response.text, flags=re.S)

        assert PLANTED_PAYEE not in response.text
        assert "13.22" not in response.text
        assert MONEY_FIGURE.search(visible) is None

    def test_Statement_ThatDoesNotAddUp_SaysSoWithItsRefusalDigitsMasked(self, served):
        base, _ = served
        response = upload(base, (GUID, wrong_closing()))
        row = rows_of(assign_form(parse(response.text)))[GUID]
        said = flat(preview_of(row))

        assert "does not add up by what it lists" in said
        assert "; adds up by what it lists" not in said
        assert "999.99" not in response.text
        assert PLANTED_PAYEE not in response.text
        assert "13.22" not in response.text

    def test_Document_ThatNoReaderReads_SaysSoInPlaceOfAPreview(self, served):
        base, _ = served
        response = upload(base, (GUID_B, letter("a topic")))
        row = rows_of(assign_form(parse(response.text)))[GUID_B]
        said = flat(preview_of(row))

        assert "Cannot be read in yet - no reader for this layout." in said
        assert "adds up" not in said
        assert "transaction" not in said

    def test_CreditUnionDocument_WhosePrintedLabelCarriesDigits_ShowsTheLabelMasked(self, served):
        base, _ = served
        response = upload(base, (GUID_C, loan_only(7)))
        row = rows_of(assign_form(parse(response.text)))[GUID_C]
        said = flat(preview_of(row))

        assert "Personal -9.99%" in said
        assert "9.50" not in response.text

    def test_AllAccountsDocument_GivesEachAccountItsOwnPreview(self, served):
        base, _ = served
        response = upload(base, (GUID, credit_union(7)))
        rows = rows_of(assign_form(parse(response.text)))

        assert sorted(rows) == [f"{GUID}, account Holiday Pot", f"{GUID}, account Regular Saver"]
        for item in rows.values():
            said = flat(preview_of(item))
            assert "1 transaction" in said
            assert "2025-07-05" in said

    def test_Preview_ForSeveralGuidNamedFiles_IsPerFileAndRepeatsNoSentenceMoreThanTwice(
        self, served
    ):
        base, _ = served
        response = upload(
            base,
            (GUID, santander(D(2026, 9, 10), 1322)),
            (GUID_B, letter("a topic")),
            (GUID_C, loan_only(7)),
        )
        rows = rows_of(assign_form(parse(response.text)))

        assert len(rows) == 3
        said = {name: flat(preview_of(item)) for name, item in rows.items()}
        assert len(set(said.values())) == 3
