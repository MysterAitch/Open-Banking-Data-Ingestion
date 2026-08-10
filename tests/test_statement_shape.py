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


def build_pdf(lines: list[str]) -> bytes:
    """A valid single-page PDF containing `lines` - entirely invented
    figures, so the end-to-end path is exercised with no real statement
    anywhere in the repository. Offsets are computed rather than guessed,
    because a PDF without a correct xref is not a PDF."""
    drawn = "\n".join(
        f"BT /F1 12 Tf 50 {700 - index * 20} Td ({line}) Tj ET"
        for index, line in enumerate(lines)
    )
    stream = drawn.encode("latin-1")
    objects = [
        b"<</Type/Catalog/Pages 2 0 R>>",
        b"<</Type/Pages/Kids[3 0 R]/Count 1>>",
        b"<</Type/Page/Parent 2 0 R/MediaBox[0 0 612 792]/Contents 4 0 R"
        b"/Resources<</Font<</F1 5 0 R>>>>>>",
        b"<</Length %d>>stream\n%s\nendstream" % (len(stream), stream),
        b"<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj" % number + body + b"endobj\n"
    xref_at = len(out)
    out += b"xref\n0 %d\n" % (len(objects) + 1)
    out += b"0000000000 65535 f \n"
    for offset in offsets:
        out += b"%010d 00000 n \n" % offset
    out += b"trailer<</Size %d/Root 1 0 R>>\nstartxref\n%d\n%%%%EOF\n" % (
        len(objects) + 1,
        xref_at,
    )
    return bytes(out)


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
