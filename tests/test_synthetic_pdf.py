"""The PDF writer the generator and the fixtures share.

Worth testing on its own because a malformed PDF fails in a way that looks like
a parser bug: the reader returns nothing, and the file that produced nothing is
the last place anybody looks. The writer already carries one such lesson in a
comment - objects need newline delimiters, or a strict reader lexes `endstream`
and `endobj` as one token and reports a page with no content at all.

Multi-page is here because the faults that broke real parsers live at page
boundaries, and none of them can be generated on a single page.
"""

from __future__ import annotations

import pytest
from pypdf import PdfReader

from obdi.statement_shape import pdf_lines
from obdi.synthetic_pdf import build_pdf

LINES = [f"line {number}" for number in range(1, 9)]


@pytest.fixture
def written(tmp_path):
    def write(lines: list[str], **options) -> object:
        path = tmp_path / "statement.pdf"
        path.write_bytes(build_pdf(lines, **options))
        return path

    return write


class TestWhatAReaderGetsBack:
    def test_ByDefault_EverythingIsOnOnePage(self, written):
        """The shape every existing caller depends on. Six test modules and the
        screenshot script build single-page files and would not say so."""
        path = written(LINES)

        assert len(PdfReader(str(path)).pages) == 1
        assert [str(line) for line in pdf_lines(path)] == LINES

    def test_SplitAcrossPages_TheLinesAndTheirOrderSurvive(self, written):
        """Order across a page boundary is the whole point: a statement read
        out of order reconciles to the same total and describes a different
        month, which is the kind of wrong that passes every count."""
        path = written(LINES, per_page=3)

        assert len(PdfReader(str(path)).pages) == 3
        assert [str(line) for line in pdf_lines(path)] == LINES

    @pytest.mark.parametrize("per_page", [1, 2, 3, 7, 8, 20])
    def test_AtEveryPageSize_TheFileIsReadableAndComplete(self, written, per_page):
        """The object numbering and the cross-reference table are COMPUTED from
        the page count, so an off-by-one in either produces a file no reader
        will open. Sizes either side of the line count are included because
        that is where such an error hides: one page exactly, and one more page
        than there are lines to fill it."""
        path = written(LINES, per_page=per_page)

        pages = PdfReader(str(path)).pages
        assert len(pages) == max(1, -(-len(LINES) // per_page))
        assert [str(line) for line in pdf_lines(path)] == LINES

    def test_APageWithNoLines_IsStillAValidPdf(self, written):
        """A statement with no text layer is a scanned one, and the reader
        must be able to say so rather than fail opening the file."""
        path = written([])

        assert len(PdfReader(str(path)).pages) == 1
        assert list(pdf_lines(path)) == []


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
