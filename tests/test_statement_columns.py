"""Reading a statement as the table it is, by position rather than by spacing.

Two kinds of test, deliberately separated.

Most of them place words at coordinates DIRECTLY, because everything that
decides what geometry means is a pure function and that is where the risk
lives - the majority rule for columns, the measured gap that ends a cell,
the blank that must stay blank. Supplying coordinates by hand states the
case exactly and cannot be confounded by a reader's opinion of a file.

The last class reads a real PDF, because the geometry reader is otherwise
a claim rather than a fact - and the claim it makes, that a word's own
coordinates cannot be fused, is the one the module rests on. Building that
fixture took three attempts, and the failures are worth knowing: a page
written as one text object per string is read differently from one holding
many positioned runs, and a file whose objects are not newline-delimited
is malformed in a way one reader tolerates and another rejects outright.
A fixture only one reader accepts measures that reader's tolerance.

The scenario throughout is the one that defeated whitespace reconstruction:
a wide page whose columns sit far apart, where two amounts arrived fused
into one token and an amount arrived glued to the description beside it.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from obdi.statement_columns import (
    Fragment,
    aligned,
    column_edges,
    rows_from_words,
)

#: Points per character and per space at statement type sizes. Approximate
#: on purpose: the code must not depend on the exact figures, only on a
#: space being far narrower than the band between columns.
CHARACTER = 5.5
SPACE = 3.0


def flow(x: float, y: float, text: str, page: int = 1) -> list[Fragment]:
    """Words set left to right from x, each following the last after a space.

    How a generator actually lays out a description, and the reason a
    reader cannot tell a wrapped description from a new column by looking
    at one word's position alone.
    """
    placed: list[Fragment] = []
    at = x
    for word in text.split():
        width = len(word) * CHARACTER
        placed.append(Fragment(text=word, x=at, y=y, page=page, x_end=at + width))
        at += width + SPACE
    return placed


HEADER_Y = -100.0
FIRST_Y = -140.0
SECOND_Y = -160.0

#: Six columns spread across a landscape page, the rightmost more than 1300
#: points from the leftmost. Whitespace reconstruction is at its worst here
#: and position at its most reliable.
WIDE = [
    *flow(60.0, HEADER_Y, "Date"),
    *flow(300.0, HEADER_Y, "Source"),
    *flow(700.0, HEADER_Y, "Payee"),
    *flow(1000.0, HEADER_Y, "Debit"),
    *flow(1180.0, HEADER_Y, "Credit"),
    *flow(1360.0, HEADER_Y, "Balance"),
    *flow(60.0, FIRST_Y, "04/04/2025"),
    *flow(300.0, FIRST_Y, "DD"),
    *flow(700.0, FIRST_Y, "Lodgement"),
    *flow(1180.0, FIRST_Y, "25.00"),
    *flow(1360.0, FIRST_Y, "868.41"),
    *flow(60.0, SECOND_Y, "30/04/2025"),
    *flow(300.0, SECOND_Y, "Internet"),
    *flow(700.0, SECOND_Y, "Transfer"),
    *flow(1000.0, SECOND_Y, "367.61"),
    *flow(1360.0, SECOND_Y, "500.80"),
]


class TestReadingByPosition:
    def test_CellsFarApartOnAPage_StillFormOneRow(self) -> None:
        table = rows_from_words(WIDE)

        assert len(table) == 3
        assert table[0].texts() == [
            "Date",
            "Source",
            "Payee",
            "Debit",
            "Credit",
            "Balance",
        ]

    def test_TheColumnsThePageUses_AreDiscoveredNotAssumed(self) -> None:
        edges = column_edges(rows_from_words(WIDE))

        assert edges == [60.0, 300.0, 700.0, 1000.0, 1180.0, 1360.0]

    def test_ABlankCell_StaysBlank_RatherThanCollapsing(self) -> None:
        # The row has no debit, and which side of the ledger a row falls on
        # is the most consequential fact on a statement line. A reader that
        # closes the gap turns a credit into a debit.
        table = aligned(rows_from_words(WIDE))

        assert table[1] == ["04/04/2025", "DD", "Lodgement", "", "25.00", "868.41"]
        assert table[2] == [
            "30/04/2025",
            "Internet",
            "Transfer",
            "367.61",
            "",
            "500.80",
        ]

    def test_TwoAmountsPrintedHardAgainstEachOther_StayTwoAmounts(self) -> None:
        # The pathology that prompted this module: reconstruction fused a
        # debit and a balance printed close together into one token. Their
        # own coordinates cannot be fused.
        touching = [
            Fragment(text="9.99", x=1000.0, y=FIRST_Y, page=1, x_end=1022.0),
            Fragment(text="9.99", x=1040.0, y=FIRST_Y, page=1, x_end=1062.0),
            Fragment(text="8.25", x=1000.0, y=SECOND_Y, page=1, x_end=1022.0),
            Fragment(text="8.25", x=1040.0, y=SECOND_Y, page=1, x_end=1062.0),
        ]

        assert aligned(rows_from_words(touching)) == [
            ["9.99", "9.99"],
            ["8.25", "8.25"],
        ]

    def test_ADescriptionOfSeveralWords_IsOneCell_NotSeveralColumns(self) -> None:
        # Words within a column are one cell. What separates a cell from
        # the next column is a measured band of empty space, so a
        # description that happens to wrap where another row's does cannot
        # invent a column between them.
        wrapped = [
            *flow(300.0, -140.0, "A PERSON LTD"),
            *flow(1000.0, -140.0, "12.00"),
            *flow(300.0, -160.0, "ANOTHER PAYEE"),
            *flow(1000.0, -160.0, "8.50"),
            *flow(300.0, -180.0, "SOMEONE ELSE"),
            *flow(1000.0, -180.0, "4.25"),
            *flow(300.0, -200.0, "SHOP"),
            *flow(1000.0, -200.0, "30.00"),
        ]

        assert aligned(rows_from_words(wrapped)) == [
            ["A PERSON LTD", "12.00"],
            ["ANOTHER PAYEE", "8.50"],
            ["SOMEONE ELSE", "4.25"],
            ["SHOP", "30.00"],
        ]

    def test_AColumnUsedByFewRows_StillGetsItsOwnCell(self) -> None:
        # A running-balance column printed only where the balance changed
        # appears on a minority of rows, so the majority rule discards it.
        # Its cells must not then be folded into a neighbour: two fields
        # arriving as one is the failure this module exists to remove, and
        # a row holding two cells is proof the column is real.
        sparse = [
            *flow(60.0, -100.0, "01/04/2025"),
            *flow(400.0, -100.0, "10.00"),
            *flow(60.0, -120.0, "02/04/2025"),
            *flow(400.0, -120.0, "20.00"),
            *flow(60.0, -140.0, "03/04/2025"),
            *flow(400.0, -140.0, "30.00"),
            *flow(700.0, -140.0, "868.41"),
        ]

        table = aligned(rows_from_words(sparse))

        assert table == [
            ["01/04/2025", "10.00", ""],
            ["02/04/2025", "20.00", ""],
            ["03/04/2025", "30.00", "868.41"],
        ]

    def test_ACellLeftOfEveryKnownColumn_OpensAColumn_RatherThanJoiningOne(
        self,
    ) -> None:
        # The pathology the sparse case hides: a word starting before every
        # discovered edge has no column at or before it. Rounding it into
        # the first column fuses two fields silently.
        leading = [
            *flow(60.0, -100.0, "99.99"),
            *flow(400.0, -100.0, "11.11"),
            *flow(400.0, -120.0, "22.22"),
        ]

        assert aligned(rows_from_words(leading)) == [
            ["99.99", "11.11"],
            ["", "22.22"],
        ]

    def test_WidthsUnknown_YieldsAWordPerCell_RatherThanAWrongGuess(self) -> None:
        # A source that cannot supply right edges makes every neighbour
        # look far away. Splitting is the safe failure: a parser can see
        # that a description arrived in pieces, whereas a wrong join is
        # indistinguishable from a correct one.
        unmeasured = [
            Fragment(text="A", x=300.0, y=FIRST_Y, page=1),
            Fragment(text="PERSON", x=316.0, y=FIRST_Y, page=1),
        ]

        assert rows_from_words(unmeasured)[0].texts() == ["A", "PERSON"]

    def test_ARowDriftingBelowItsBaseline_StaysOneRow(self) -> None:
        # Real pages place a line's words within a point or two of each
        # other rather than exactly on one baseline.
        drifting = [
            Fragment(text="04/04/2025", x=60.0, y=-140.0, page=1, x_end=115.0),
            Fragment(text="25.00", x=1180.0, y=-141.4, page=1, x_end=1207.0),
        ]

        assert len(rows_from_words(drifting)) == 1

    def test_TheSameBaselineOnAnotherPage_IsAnotherRow(self) -> None:
        # Page two starts its own table; a row cannot span pages however
        # closely the baselines agree.
        across = [
            Fragment(text="04/04/2025", x=60.0, y=FIRST_Y, page=1, x_end=115.0),
            Fragment(text="05/04/2025", x=60.0, y=FIRST_Y, page=2, x_end=115.0),
        ]

        assert len(rows_from_words(across)) == 2

    def test_APageWithNoText_YieldsNoRows_RatherThanOneEmptyOne(self) -> None:
        assert rows_from_words([]) == []
        assert aligned([]) == []


def build_positioned_pdf(placements: list[tuple[float, float, str]]) -> bytes:
    """A valid wide single-page PDF placing each string at an absolute point.

    Wide on purpose - a landscape MediaBox with text out past x=1300 - so
    the fixture reproduces the layout that defeated whitespace
    reconstruction rather than a comfortable one.

    One text object holding many positioned runs, which is what a real
    generator emits: a Tm before each Tj sets the text matrix absolutely.
    A file written as one text object PER string reads differently, and a
    fixture shaped unlike any real document proves nothing about real
    documents.
    """
    placed = "\n".join(
        f"1 0 0 1 {x:.2f} {y:.2f} Tm ({text}) Tj" for x, y, text in placements
    )
    drawn = (f"BT /F1 10 Tf\n{placed}\nET" if placements else "").encode("latin-1")
    objects = [
        b"<</Type/Catalog/Pages 2 0 R>>",
        b"<</Type/Pages/Kids[3 0 R]/Count 1>>",
        b"<</Type/Page/Parent 2 0 R/MediaBox[0 0 1684 792]/Contents 4 0 R"
        b"/Resources<</Font<</F1 5 0 R>>>>>>",
        b"<</Length %d>>stream\n%s\nendstream" % (len(drawn), drawn),
        b"<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        # Newline-delimited. Run `endstream` into `endobj` and a strict
        # reader lexes one token, never closes the stream and reports an
        # empty page - a malformed file that a lenient reader will accept,
        # which is the worst kind because it looks like it works.
        out += b"%d 0 obj\n" % number + body + b"\nendobj\n"
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


class TestTheGeometryReaderAgainstARealFile:
    """The one part that cannot be pure: reading positions out of a PDF.

    Thin by design, but untested it is a claim rather than a fact - and
    the claim it makes ("a word's coordinates cannot be fused") is the one
    the whole module rests on.
    """

    @staticmethod
    def _written(tmp_path, placements) -> Path:
        path = tmp_path / "wide.pdf"
        path.write_bytes(build_positioned_pdf(placements))
        return path

    def test_AWordsOwnPosition_SurvivesBeingReadOutOfThePage(self, tmp_path):
        from obdi.statement_columns import words_from

        path = self._written(
            tmp_path,
            [(60.0, 700.0, "Date"), (300.0, 700.0, "Source"), (1360.0, 700.0, "Bal")],
        )

        found = {word.text: word for word in words_from(path)}

        assert set(found) == {"Date", "Source", "Bal"}
        assert found["Date"].x == pytest.approx(60.0, abs=1.0)
        assert found["Source"].x == pytest.approx(300.0, abs=1.0)
        assert found["Bal"].x == pytest.approx(1360.0, abs=1.0)

    def test_EachWordCarriesAWidth_SoGapsCanBeMeasured(self, tmp_path):
        # Without widths the gap between two words is unknowable and every
        # word becomes its own cell. This is the fact that makes cells
        # possible at all.
        from obdi.statement_columns import words_from

        path = self._written(tmp_path, [(60.0, 700.0, "Date")])

        word = words_from(path)[0]

        assert word.x_end > word.x, "a word with no width cannot be measured"

    def test_AWideTable_IsReadAsColumns_NotAsRunTogetherText(self, tmp_path):
        # The end-to-end claim: columns 1300 points apart, one row missing
        # its debit, read back as a table with the blank preserved.
        from obdi.statement_columns import aligned, rows

        path = self._written(
            tmp_path,
            [
                (60.0, 700.0, "04/04/2025"),
                (300.0, 700.0, "Lodgement"),
                (1180.0, 700.0, "25.00"),
                (1360.0, 700.0, "868.41"),
                (60.0, 680.0, "30/04/2025"),
                (300.0, 680.0, "Transfer"),
                (1000.0, 680.0, "367.61"),
                (1360.0, 680.0, "500.80"),
            ],
        )

        assert aligned(rows(path)) == [
            ["04/04/2025", "Lodgement", "", "25.00", "868.41"],
            ["30/04/2025", "Transfer", "367.61", "", "500.80"],
        ]

    def test_AnUnreadableFile_YieldsNoWords_RatherThanRaising(self, tmp_path):
        # The report treats absent geometry as a fact to state, which only
        # works if the reader declines rather than throws.
        from obdi.statement_columns import words_from

        path = tmp_path / "not.pdf"
        path.write_text("this is not a pdf", encoding="utf-8")

        assert words_from(path) == []
