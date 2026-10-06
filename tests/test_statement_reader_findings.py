"""What the reader made of a kept statement, said on its masked shape page without any value.

The owner's trouble: a credit union loan's balance and period did not arrive after a fix, and the
shape page showed only masked text, so the cause could not be seen. The page now says, beneath the
shape, for each section (or the whole document where there are none) what the reader concluded.

Known answers, decided before the first run, over the invented credit union documents:

    "Regular Saver"     3 lines, a period 2025-05-01 to 2025-05-31, opening and closing found, the
                        closing read from the label Closing Balance, taken for a saver because
                        neither a rate in the name nor a Closing Loan Position line is there;
                        gate passed
    "Personal -9.50%"   a loan, decided by the rate in its name; gate passed
    "Home Loan"         a loan with no rate in its name, decided by the Closing Loan Position line
    "Out By A Penny"    a saver whose printed closing is 1p high: gate refused, in words that say
                        the sum did not reach the closing balance and carry no figure
    A document the reader does not recognise: says no reader recognised it, and nothing else.

A planted payee and planted amounts must be nowhere on the page, in text or in any attribute.
"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from credit_union_documents import Move, document, pdf, section
from obdi.identity import artefact_digest
from obdi.statement_sections import read_sections
from obdi.store import Store
from page_dom import Node, elements, parse
from section_harness import UNASSIGNED, config, environment, keep, serve_config

PAYEE = "PLANTEDPAYEE ZEBRA"


@pytest.fixture
def db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    environment(monkeypatch, tmp_path)
    path = tmp_path / "store.sqlite3"
    with Store(path):
        pass
    return path


def _moves() -> list[Move]:
    return [
        Move("04/05/2025", "DD Lodgement", 2500, PAYEE),
        Move("09/05/2025", "Div - Regular Saver", 249),
        Move("17/05/2025", "Internet Transfer", -30000, PAYEE),
    ]


def _loan_moves() -> list[Move]:
    return [Move("12/05/2025", "tx", 15500), Move("26/05/2025", "tx", 249)]


def _all_sections() -> bytes:
    return pdf(
        document(
            section("Regular Saver", 80000, _moves()),
            section("Personal -9.50%", -50000, _loan_moves(), loan=True),
            section("Home Loan", -70000, _loan_moves(), loan=True),
            section("Out By A Penny", 10000, [Move("05/05/2025", "tx", 100)],
                    printed_closing_minor=10101),
        ),
        step=14.0,
    )


def _page(db: Path, payload: bytes, name: str, *, assign: dict[str, str] | None = None) -> str:
    with Store(db) as store:
        ident = keep(store, payload, name)
        if assign:
            found = read_sections(payload)
            assert found is not None
            _, readings = found
            for item in readings:
                if item.label in assign:
                    store.assign_statement_section(
                        artefact_digest(payload), item.key, assign[item.label], item.label
                    )
    base, stop = serve_config(config(db))
    try:
        response = httpx.get(f"{base}/statement-shape?artefact={ident}", timeout=60)
    finally:
        stop()
    assert response.status_code == 200
    return response.text


def _findings(root: Node) -> dict[str, list[str]]:
    """Each section's lines of finding, by the (digit-masked) label it is printed under."""
    heading = next(h for h in elements(root, "h3") if h.text() == "What the reader found")
    block = heading.parent
    assert block is not None
    found: dict[str, list[str]] = {}
    for part in elements(block, "section"):
        title = next(elements(part, "h4")).text()
        found[title] = [li.text() for li in elements(part, "li")]
    return found


class TestACreditUnionDocumentOfSeveralAccounts:
    def test_EachSection_SaysWhatTheReaderFoundWithoutAnyValue(self, db):
        page = _page(db, _all_sections(), "cu.pdf", assign={"Regular Saver": "regular-saver"})

        found = _findings(parse(page))

        # A label's digits are shown as 9 on a page served on a GET: a rate is a figure.
        assert list(found) == ["Regular Saver", "Personal -9.99%", "Home Loan", "Out By A Penny"]
        saver = found["Regular Saver"]
        assert saver == [
            "Account: regular-saver",
            "Period found: 2025-05-01 to 2025-05-31 (a month)",
            "Opening balance found: yes",
            "Closing balance found: yes, read from the label Closing Balance",
            "Transactions listed: 3",
            "Taken for: a saver (no rate in the name and no Closing Loan Position line)",
            "Arithmetic gate: passed",
        ]

    def test_ALoanNamedForItsRate_IsTakenForALoanByTheRateAndTheFootBothItPrints(self, db):
        found = _findings(parse(_page(db, _all_sections(), "cu.pdf")))

        loan = next(lines for label, lines in found.items() if label.startswith("Personal"))
        assert (
            "Taken for: a loan (the rate in its name and the Closing Loan Position line)" in loan
        )
        assert "Account: unassigned" in loan
        assert "Arithmetic gate: passed" in loan

    def test_ALoanWithNoRateInItsName_IsTakenForALoanByTheClosingLoanPositionLine(self, db):
        found = _findings(parse(_page(db, _all_sections(), "cu.pdf")))

        assert "Taken for: a loan (the Closing Loan Position line)" in found["Home Loan"]

    def test_ASectionTheGateRefuses_SaysTheSumDidNotReachTheClosingBalanceAndNoFigure(self, db):
        page = _page(db, _all_sections(), "cu.pdf")
        found = _findings(parse(page))

        refused = found["Out By A Penny"][-1]
        assert refused.startswith("Arithmetic gate: refused - ")
        assert "the sum did not reach the closing balance" in refused
        assert "minor units" not in page

    def test_NoPlantedPayeeOrAmount_IsAnywhereOnThePage(self, db):
        page = _page(db, _all_sections(), "cu.pdf")

        for planted in (PAYEE, "ZEBRA", "800.00", "249", "2.49", "300.00", "25.00", "1.01",
                        "101.01", "100.00"):
            assert planted not in page, planted

    def test_AnUnassignedSection_IsSaidUnassignedAndAnAssignedOneNamesItsAccount(self, db):
        page = _page(db, _all_sections(), "cu.pdf", assign={"Home Loan": "home-loan"})
        found = _findings(parse(page))

        assert "Account: home-loan" in found["Home Loan"]
        assert "Account: unassigned" in found["Regular Saver"]
        assert UNASSIGNED not in page


class TestAStatementOfASingleAccount:
    def test_TheWholeDocument_IsReportedAsOneWithTheSameFindings(self, db):
        payload = pdf(document(section("Regular Saver", 80000, _moves())))

        found = _findings(parse(_page(db, payload, "one.pdf")))

        assert list(found) == ["The whole document"]
        lines = found["The whole document"]
        assert "Account: unassigned" in lines
        assert "Opening balance found: yes" in lines
        assert "Closing balance found: yes, read from the label Closing Balance" in lines
        assert "Transactions listed: 3" in lines
        assert "Arithmetic gate: passed" in lines

    def test_ASingleAccountThatDoesNotAddUp_SaysSoWithoutAFigure(self, db):
        payload = pdf(
            document(section("Regular Saver", 80000, _moves(), printed_closing_minor=52750))
        )
        page = _page(db, payload, "one.pdf")

        lines = _findings(parse(page))["The whole document"]
        assert lines[-1].startswith("Arithmetic gate: refused - ")
        assert "the sum did not reach the closing balance" in lines[-1]
        assert "527" not in page and "minor units" not in page


class TestADocumentNoReaderRecognises:
    def test_ItSaysSoAndNothingElse(self, db):
        from test_statement_shape import build_pdf

        payload = build_pdf(["Some unrecognised words", "04 Jan A PAYEE 21.72"])

        page = _page(db, payload, "unknown.pdf")

        said = parse(page).text()
        assert "What the reader found" in said
        assert "No reader recognised this document" in said
        assert "21.72" not in page
