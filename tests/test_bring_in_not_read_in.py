# ruff: noqa: F401, F811
# The `served` fixture is imported from the file that built it; used by name it reads to the
# linter as unused and then as redefined.
"""Bring in says, first, which file was not read in - never inside a fold.

The owner uploaded two PDFs: one a reader reads, one a credit card statement no reader reads yet.
The answer led with the readable file's outcome and kept the other's "not read in" inside the
closed fold "What each file held": "I had to expand this to find out one of the pdf files wasn't
ingested". The one form also offered a chooser for the unreadable file, which he set and the press
silently ignored.

The scenes are that upload over invented documents (a Santander statement for `up-card`, and a
card statement that prints two issuer names no reader reads), each decided before the first run:

  * the answer's first line is "Not read in: <file> - no reader for its layout yet", with the names
    found in it and a link to its masked shape, above the readable file's outcome and not in a fold;
  * the file's row in the one form says it cannot be read in yet, offers no chooser and no dry run;
  * pressing the form for the readable file reads it in, keeps the other, and says so first.
"""

from __future__ import annotations

import re
from datetime import date

import httpx

from obdi.synthetic_pdf import build_pdf
from page_dom import Node, elements, parse
from test_bring_in_assign import (
    PLANTED_PAYEE,
    D,
    assign_form,
    flat,
    kept,
    part,
    rows_of,
    santander,
    served,
)

CARD = "Card-2026-10-05.pdf"
READABLE = "Up-card-2026-09.pdf"


def card_statement() -> bytes:
    """A credit card statement of an issuer no reader reads: it names two issuers, once each."""
    return build_pdf([
        "Nationwide Building Society",
        "Your credit card statement",
        "Mastercard",
        f"05 October {PLANTED_PAYEE}   13.22",
    ])


def two_files(base: str) -> httpx.Response:
    return httpx.post(
        f"{base}/bring-in",
        files=[part(READABLE, santander(D(2026, 9, 10), 1322)), part(CARD, card_statement())],
        timeout=300,
    )


def what_the_form_posts(page: Node, *, choosing: dict[str, str]) -> dict[str, str]:
    """The fields a browser would send for the one form, with the chooser of each file named in
    `choosing` set to its account (a row with no chooser sends only its hidden field)."""
    form = assign_form(page)
    data: dict[str, str] = {}
    for name, row in rows_of(form).items():
        for field in elements(row, "input"):
            if field.attrs.get("type") == "hidden":
                data[field.attrs["name"]] = field.attrs.get("value", "")
        for select in elements(row, "select"):
            data[select.attrs["name"]] = choosing.get(name, "")
    return data


def lead_of(page: Node) -> Node:
    found = [p for p in elements(page, "p") if "bi-not-read" in p.classes]
    assert found, "the answer names no file as not read in"
    return found[0]


def press(base: str, root_page: Node, root, choosing: dict[str, str]) -> Node:
    data = what_the_form_posts(root_page, choosing=choosing)
    return parse(httpx.post(f"{base}/statements-assign", data=data, timeout=300).text)


class TestTheAnswerLeadsWithAFileNotReadIn:
    def test_Upload_OfAReadableAndAnUnreadableFile_LeadsWithTheUnreadableOneBeforeAnythingElse(
        self, served
    ):
        base, root = served
        page = parse(two_files(base).text)

        lead = lead_of(page)
        said = flat(lead)
        ident = kept(root)[CARD]["id"]

        assert said.startswith(f"Not read in: {CARD} - no reader for its layout yet.")
        assert "Nationwide 1" in said and "Mastercard 1" in said
        assert [a.attrs["href"] for a in elements(lead, "a")] == [
            f"/statement-shape?artefact={ident}"
        ]
        assert "stays kept" in said
        assert not [a for a in lead.ancestors() if a.tag == "details"]
        # Nothing the page says of any file comes before it.
        drop = next(d for d in elements(page, "div") if "bi-drop" in d.classes)
        first = next(c for c in drop.children if isinstance(c, Node) and c.tag != "h2")
        assert first is lead

    def test_Press_OfTheReadableFileAlone_ReadsItInAndLeadsWithTheOneItSkipped(self, served):
        base, root = served
        page = parse(two_files(base).text)

        answer = press(base, page, root, {READABLE: "up-card"})
        said = flat(answer)

        assert kept(root)[READABLE]["account_ref"] == "up-card"
        assert kept(root)[CARD]["account_ref"] == "(unassigned)"
        lead = lead_of(answer)
        assert flat(lead).startswith(f"Not read in: {CARD} - no reader for its layout yet.")
        assert not [a for a in lead.ancestors() if a.tag == "details"]
        assert said.index("Not read in:") < said.index("read in to Up card")

    def test_Press_WhenNoFileIsUnreadable_SaysNothingOfOneNotReadIn(self, served):
        base, root = served
        page = parse(httpx.post(
            f"{base}/bring-in", files=[part(READABLE, santander(D(2026, 9, 10), 1322))],
            timeout=300,
        ).text)

        answer = press(base, page, root, {READABLE: "up-card"})

        assert "Not read in" not in flat(answer)

    def test_Upload_ScopedToAnAccount_WithAnUnreadableFile_LeadsWithItAndItsReason(self, served):
        base, root = served

        page = parse(httpx.post(
            f"{base}/bring-in", data={"account": "up-card"},
            files=[part(CARD, card_statement())], timeout=300,
        ).text)

        assert flat(lead_of(page)).startswith(f"Not read in: {CARD} - ")
        assert kept(root)[CARD]["account_ref"] == "(unassigned)"

    def test_FoldOfWhatEachFileHeld_MayRepeatIt(self, served):
        base, _ = served
        page = parse(two_files(base).text)

        held = next(d for d in elements(page, "details") if "What each file held" in flat(d))

        assert f"{CARD}: not read in" in flat(held)


class TestTheRowOfAFileNoReaderReads:
    def test_Row_SaysItCannotBeReadInYetAndOffersNothingToChooseOrPreview(self, served):
        base, root = served
        page = parse(two_files(base).text)

        row = rows_of(assign_form(page))[CARD]
        said = flat(row)
        ident = kept(root)[CARD]["id"]

        assert "Cannot be read in yet - no reader for this layout." in said
        assert "Nationwide 1" in said and "Mastercard 1" in said
        assert list(elements(row, "select")) == []
        assert "What reading it in would do" not in said
        assert "Show values" not in said
        assert [a.attrs["href"] for a in elements(row, "a") if a.text() == "Masked shape"] == [
            f"/statement-shape?artefact={ident}"
        ]
        assert "No reader reads this file yet" not in said

    def test_Row_AfterAPress_StillSaysItCannotBeReadInAndOffersNoChooser(self, served):
        base, root = served
        page = parse(two_files(base).text)

        answer = press(base, page, root, {READABLE: "up-card"})
        row = rows_of(assign_form(answer))[CARD]

        assert list(elements(row, "select")) == []
        assert "Cannot be read in yet - no reader for this layout." in flat(row)
        assert "Nationwide 1" in flat(row)

    def test_Row_OfAReadableFile_StillOffersItsChooser(self, served):
        base, _ = served
        page = parse(two_files(base).text)

        assert len(list(elements(rows_of(assign_form(page))[READABLE], "select"))) == 1

    def test_Row_PrintsNoPayeeAndNoFigure(self, served):
        base, _ = served
        response = two_files(base)

        assert PLANTED_PAYEE not in response.text
        assert "13.22" not in response.text

    def test_Press_ForcedToNameAnAccountForTheUnreadableFile_StillReadsNothingOfIt(self, served):
        base, root = served
        page = parse(two_files(base).text)
        data = what_the_form_posts(page, choosing={READABLE: "up-card"})
        data[f"account-{kept(root)[CARD]['id']}"] = "up-card"

        answer = parse(httpx.post(f"{base}/statements-assign", data=data, timeout=300).text)

        assert kept(root)[CARD]["account_ref"] == "(unassigned)"
        assert flat(lead_of(answer)).startswith(f"Not read in: {CARD} - no reader")
