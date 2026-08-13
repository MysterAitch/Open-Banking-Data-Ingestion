"""Reading a statement's SHAPE without disclosing its contents.

Writing a parser for a bank's statement layout needs the layout: column
order, header wording, date format, how the opening and closing balance
lines are phrased. It does not need anybody's actual money. So the
inspector redacts by default - every digit and every word that is not
recognisable statement furniture is masked, preserving length and shape so
the structure survives - and disclosing real values takes an explicit flag.

The masking is the load-bearing part, so it is tested far harder than the
PDF plumbing that feeds it: a leak here is not a bug, it is a disclosure.
"""

from __future__ import annotations

import pytest

from obdi.statement_shape import mask_line, pdf_lines, shape_report

# Re-exported where six test modules already import it from. The
# implementation moved to src on 2026-08-13 because the synthetic world
# generator needs it and a module under src cannot import from tests; keeping
# the name here means that move cost one line rather than six edits.
from obdi.synthetic_pdf import build_pdf

FAKE_PDF = build_pdf(
    [
        "Statement of account",
        "01 Jan 2026 to 31 Jan 2026",
        "Opening balance 1,234.56",
        "04 Jan ACME LTD 12.34",
    ]
)


class TestMaskingProtectsValuesAndKeepsShape:
    def test_AmountsAndDates_AreMaskedButTheirShapeSurvives(self):
        assert mask_line("Opening balance 1,234.56") == "Opening balance 9,999.99"
        assert mask_line("04 Jan 2026") == "99 Jan 9999"

    def test_StatementFurniture_SurvivesSoTheLayoutIsReadable(self):
        # The words a parser keys on must come through verbatim, or the
        # report cannot describe the layout it exists to describe.
        line = "Date Description Paid out Paid in Balance"
        assert mask_line(line) == line

    def test_APayeeName_IsMasked_KeepingWordCountAndLength(self):
        masked = mask_line("04 Jan SAINSBURYS S/MKTS 21.72")
        assert "SAINSBURYS" not in masked
        assert masked == "99 Jan XXXXXXXXXX X/XXXX 99.99"

    def test_MixedAlphanumericReferences_AreFullyMasked(self):
        assert "90481679" not in mask_line("DAP90481679")
        assert mask_line("DAP90481679") == "XXX99999999"

    def test_CaseIsPreserved_SoCasingConventionsStayVisible(self):
        assert mask_line("Roger Howell") == "Xxxxx Xxxxxx"

    def test_CurrencyAndPunctuation_Survive(self):
        assert mask_line("Balance: -£1,234.56") == "Balance: -£9,999.99"

    def test_AnAccountNumber_LeaksNothing(self):
        masked = mask_line("Account 12345678 sort code 01-02-03")
        assert "12345678" not in masked
        assert "01-02-03" not in masked
        assert masked == "Account 99999999 sort code 99-99-99"

    @pytest.mark.parametrize(
        "line",
        [
            "Mr R Howell, 1 Example Street, Birmingham B1 1AA",
            "SAINSBURYS S/MKTS 5223 SELLY OAK 21.72",
            "Interest charged on cash advances 24.9% APR",
        ],
    )
    def test_NoOriginalWord_LongerThanTwoCharacters_SurvivesUnlessItIsFurniture(
        self, line
    ):
        masked = mask_line(line)
        for word in line.replace(",", " ").split():
            stripped = word.strip(".:%")
            if len(stripped) > 2 and not _is_furniture(stripped):
                assert stripped not in masked, f"{stripped} leaked"


def _is_furniture(word: str) -> bool:
    from obdi.statement_shape import FURNITURE

    return word.casefold() in FURNITURE


class TestTheReport:
    def test_ItReadsAPdfsTextLines(self, tmp_path):
        path = tmp_path / "statement.pdf"
        path.write_bytes(FAKE_PDF)

        lines = pdf_lines(path)

        assert any("Statement of account" in line for line in lines)
        assert any("Opening balance" in line for line in lines)

    def test_TheReportMasksByDefault_AndSaysHowManyLinesItSaw(self, tmp_path):
        path = tmp_path / "statement.pdf"
        path.write_bytes(FAKE_PDF)

        report = shape_report(path)

        assert report.masked is True
        assert report.line_count >= 4
        text = report.describe()
        assert "1,234.56" not in text, "a real amount must never appear"
        assert "9,999.99" in text
        assert "Opening balance" in text
        assert "ACME" not in text

    def test_DisclosingRealValues_RequiresAnExplicitAsk(self, tmp_path):
        path = tmp_path / "statement.pdf"
        path.write_bytes(FAKE_PDF)

        report = shape_report(path, mask=False)

        assert report.masked is False
        assert "1,234.56" in report.describe()

    def test_ANonPdf_IsRefusedClearly_RatherThanCrashing(self, tmp_path):
        path = tmp_path / "not.pdf"
        path.write_text("this is not a pdf", encoding="utf-8")

        report = shape_report(path)

        assert report.line_count == 0
        assert "could not be read" in report.describe().lower()

    def test_AScannedPage_IsNamedAsSuch_NotSilentlyEmpty(self, tmp_path):
        # A page with no text layer yields nothing; saying "no text" is the
        # difference between "park this for OCR" and "the parser is broken".
        path = tmp_path / "scanned.pdf"
        path.write_bytes(build_pdf([]))

        report = shape_report(path)

        assert report.line_count == 0
        assert "no text" in report.describe().lower()


class TestLedgerMarkersSurvive:
    """A credit marker is the one fact a parser cannot afford to lose.

    Masking rendered CR and GB identically as two capitals, which made a
    payment indistinguishable from a purchase in a country - and getting
    that wrong inverts every payment on the statement. Ledger markers and
    country codes are structural, name nobody, and now come through.
    """

    def test_ACreditMarker_IsLegible(self):
        assert mask_line("29th Jun Direct Payment CR 99.00") == (
            "99xx Jun Xxxxxx Payment CR 99.99"
        )

    def test_ACountryCode_IsLegible_AndDistinctFromACredit(self):
        purchase = mask_line("29th Jun SOME SHOP GB 12.34")
        payment = mask_line("29th Jun Direct Payment CR 12.34")

        assert "GB" in purchase and "CR" not in purchase
        assert "CR" in payment and "GB" not in payment

    def test_AMerchantIsStillMasked_BesideItsCountryCode(self):
        masked = mask_line("29th Jun SAINSBURYS SELLY OAK GB 21.72")

        assert "SAINSBURYS" not in masked
        assert "SELLY" not in masked
        assert "21.72" not in masked
        assert "GB" in masked


class TestLayoutExtraction:
    """A statement is a table, and a table needs its horizontal positions.

    Plain extraction emitted one TOKEN per line on a real statement - 2,714
    lines of what layout mode renders as a few hundred - which destroyed
    the columns and pushed the transaction table past the display limit.
    Layout mode is tried first for that reason, with plain kept as the
    fallback because some documents defeat it and a worse shape beats none.
    """

    def test_LayoutModeIsPreferred(self, tmp_path):
        seen = {}

        class Page:
            def extract_text(self, **kwargs):
                seen.update(kwargs)
                return "Date Description Amount"

        from obdi.statement_shape import _page_text

        assert _page_text(Page()) == "Date Description Amount"
        assert seen.get("extraction_mode") == "layout"

    def test_WhenLayoutModeFails_PlainExtractionStillAnswers(self, tmp_path):
        class Page:
            def extract_text(self, **kwargs):
                if kwargs.get("extraction_mode") == "layout":
                    raise RuntimeError("this document defeats layout mode")
                return "fallback text"

        from obdi.statement_shape import _page_text

        assert _page_text(Page()) == "fallback text"

    def test_WhenLayoutModeReturnsNothing_PlainExtractionIsTried(self, tmp_path):
        class Page:
            def extract_text(self, **kwargs):
                return "" if kwargs.get("extraction_mode") == "layout" else "plain"

        from obdi.statement_shape import _page_text

        assert _page_text(Page()) == "plain"


class TestTheColumnViewIsMaskedOnTheSameTerms:
    """The coordinate reading is a SECOND way out of the same document.

    It was added beside a masking door that already worked, and a new
    surface does not inherit the guard on the one beside it - so the door
    is tested here on its own terms. The geometry is supplied directly
    rather than through a PDF, because what is under test is whether the
    report masks what it is handed, not whether a library can parse a
    fixture.
    """

    @staticmethod
    def _with_geometry(monkeypatch, table) -> None:
        import obdi.statement_columns as columns

        monkeypatch.setattr(columns, "rows", lambda path, **kwargs: table)

    @staticmethod
    def _table() -> list:
        from obdi.statement_columns import Fragment, rows_from_words

        return rows_from_words(
            [
                Fragment(text="ACME", x=60.0, y=-100.0, page=1, x_end=82.0),
                Fragment(text="1,234.56", x=400.0, y=-100.0, page=1, x_end=444.0),
                Fragment(text="SAINSBURYS", x=60.0, y=-120.0, page=1, x_end=115.0),
                Fragment(text="21.72", x=400.0, y=-120.0, page=1, x_end=427.0),
            ]
        )

    def test_AReadableTable_ReachesThePageWithItsValuesMasked(
        self, tmp_path, monkeypatch
    ):
        self._with_geometry(monkeypatch, self._table())
        path = tmp_path / "statement.pdf"
        path.write_bytes(FAKE_PDF)

        text = shape_report(path).describe()

        assert "read as 2 row(s) of 2 column(s)" in text
        assert "ACME" not in text
        assert "1,234.56" not in text
        assert "SAINSBURYS" not in text
        assert "9,999.99" in text, "the shape of an amount must survive"

    def test_DisclosingTheTable_RequiresTheSameExplicitAsk_AsTheLines(
        self, tmp_path, monkeypatch
    ):
        self._with_geometry(monkeypatch, self._table())
        path = tmp_path / "statement.pdf"
        path.write_bytes(FAKE_PDF)

        text = shape_report(path, mask=False).describe()

        assert "1,234.56" in text

    def test_AnEmptyCell_IsVisibleAsAnEmptyCell(self, tmp_path, monkeypatch):
        # Which side of the ledger a row fell on is carried by the blank.
        # Rendered with separators for exactly this reason: laid out with
        # spacing, the blank would be unreadable again.
        from obdi.statement_columns import Fragment, rows_from_words

        table = rows_from_words(
            [
                Fragment(text="99.99", x=60.0, y=-100.0, page=1, x_end=87.0),
                Fragment(text="11.11", x=400.0, y=-100.0, page=1, x_end=427.0),
                Fragment(text="22.22", x=400.0, y=-120.0, page=1, x_end=427.0),
            ]
        )
        self._with_geometry(monkeypatch, table)
        path = tmp_path / "statement.pdf"
        path.write_bytes(FAKE_PDF)

        text = shape_report(path).describe()

        assert "99.99 | 99.99" in text
        assert " | 99.99" in text, "the missing first cell must stay missing"

    def test_GeometryThatCannotBeRead_IsNamed_NotSilentlyOmitted(
        self, tmp_path, monkeypatch
    ):
        # The report still answers - the line view is the older and better
        # proven of the two - but a reader must not mistake an absent
        # column reading for a document with no columns. A page with a text
        # layer whose geometry yields nothing is the real case here: a
        # scanned page is named separately and never reaches this branch.
        self._with_geometry(monkeypatch, [])
        path = tmp_path / "statement.pdf"
        path.write_bytes(FAKE_PDF)

        text = shape_report(path).describe()

        assert "NO column reading" in text

    def test_GeometryThatRaises_LeavesTheRestOfTheReportIntact(
        self, tmp_path, monkeypatch
    ):
        import obdi.statement_columns as columns

        def explode(path, **kwargs):
            raise RuntimeError("geometry unavailable")

        monkeypatch.setattr(columns, "rows", explode)
        path = tmp_path / "statement.pdf"
        path.write_bytes(FAKE_PDF)

        report = shape_report(path)

        assert report.line_count >= 4
        assert report.rows == []
        assert "NO column reading" in report.describe()
