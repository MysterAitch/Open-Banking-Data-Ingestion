"""An "all accounts" credit union document is read one account at a time.

The issuer's export prints every account one after another with the page
numbering restarting for each, so one document is many statements. The single
reader refuses such a document rather than filing every account's rows under
one; these tests are about reading it SECTION by section instead, where each
section carries its own opening and closing balances and so is checked by the
same arithmetic a single statement is, independently of the others.

Every document is invented (`credit_union_documents`), and each section's
answer - its rows and its closing balance - was decided before the document
existed, so a disagreement is a fault in the reader rather than a judgement.
The masked shape dumps this rests on showed two things the fixtures imitate:
the page marker opens each account's first page, and the labels can reach the
reader with no space inside, because the word grid delivers them that way.
"""

from __future__ import annotations

from datetime import date

import pytest

from credit_union_documents import (
    NINE,
    NINE_CLOSINGS,
    NINE_ROWS,
    Move,
    document,
    grid,
    nine_accounts,
    pages,
    pdf,
    section,
)
from obdi.ingest.parsers.base import ParseError
from obdi.ingest.parsers.credit_union_pdf import (
    read_document,
    read_statement,
    section_key,
)
from obdi.ingest.parsers.pdf_statements import CreditUnionStatementPdfParser

SAVER = ("Regular Saver", 80000, [Move("04/05/2025", "DD Lodgement", 2500)])


def _sections(lines: list[str]):  # type: ignore[no-untyped-def]
    found = read_document(grid(lines))
    assert found is not None, "the document was read as one account"
    return found


class TestANineAccountDocument:
    def test_NineAccounts_AreReadAsNineSections_InDocumentOrder(self):
        found = _sections(nine_accounts())

        assert [item.label for item in found] == [label for label, *_ in NINE]

    def test_EachSection_CarriesItsOwnRows(self):
        found = _sections(nine_accounts())

        assert [len(item.reading.transactions) for item in found] == NINE_ROWS

    def test_EachSection_CarriesItsOwnOpeningBalance_NotTheFirstAccounts(self):
        found = _sections(nine_accounts())

        assert [item.reading.opening_balance_minor for item in found] == [
            opening for _, opening, _, _ in NINE
        ]

    def test_EachSection_ClosesAtItsOwnBalance_AndAllNineReconcile(self):
        found = _sections(nine_accounts())

        assert [item.reading.closing_balance_minor for item in found] == NINE_CLOSINGS
        assert all(item.reading.reconciles for item in found)
        assert not any(item.reading.notes for item in found)

    def test_ALoanSection_IsHeldAsWhatIsOwed_AndARepaymentMovesItTowardZero(self):
        found = _sections(nine_accounts())
        personal = found[7]

        assert personal.reading.closing_balance_minor == -34251
        assert [row.amount_minor for row in personal.reading.transactions] == [15500, 249]
        assert personal.reading.rates == {"personal": 9.50}

    def test_ASectionWithNoRows_IsAnOrdinaryAccount_NotAFault(self):
        found = _sections(nine_accounts())

        rainy = found[3]
        assert rainy.label == "Rainy Day"
        assert rainy.reading.transactions == []
        assert rainy.reading.reconciles

    def test_ASectionSpanningTwoPages_IsStillOneSection(self):
        lines = document(
            section(*SAVER, pages=2),
            section("Christmas Club", 15000, [Move("05/05/2025", "DD Lodgement", 1000)]),
        )

        found = _sections(lines)

        assert [item.label for item in found] == ["Regular Saver", "Christmas Club"]
        assert len(found[0].reading.transactions) == 1
        assert found[0].reading.reconciles


class TestASectionBeginsWhereItsPageDoes:
    """The issuer prints the period and the date of issue ABOVE the page-number
    box. A cut made at the page marker hands those lines to the account before,
    whose closing balance then has a period and whose neighbour's has none - and
    a closing balance with no period cannot be dated, so on the real store a
    loan's balances never became known balances and its whole life read as a
    hole.
    """

    LOAN = ("Personal -9.50%", -400000, [Move("04/06/2025", "DD Lodgement", 15500)])

    def _document(self) -> list[str]:
        return document(
            section(*SAVER, period="01/05/2025 to 31/05/2025", header_above_marker=True),
            section(
                *self.LOAN, period="01/06/2025 to 30/06/2025", loan=True, header_above_marker=True
            ),
        )

    def test_EverySection_FindsItsOwnPeriod_WhenTheHeaderIsAboveTheMarker(self):
        lines = self._document()

        found = read_document(grid(lines), pages(lines))

        assert found is not None
        assert [item.label for item in found] == ["Regular Saver", "Personal -9.50%"]
        assert [(item.reading.period_start, item.reading.statement_date) for item in found] == [
            (date(2025, 5, 1), date(2025, 5, 31)),
            (date(2025, 6, 1), date(2025, 6, 30)),
        ], "a section's period must be its own page's, not its neighbour's"
        assert all(item.reading.produced == date(2026, 10, 6) for item in found)

    def test_ThroughTheParser_ARealPagedFile_DatesEverySection(self):
        payload = pdf(self._document(), paged=True)

        found = CreditUnionStatementPdfParser().sections(payload)

        assert found is not None
        assert [item.reading.statement_date for item in found] == [
            date(2025, 5, 31),
            date(2025, 6, 30),
        ]
        assert all(item.reading.reconciles for item in found)

    def test_TheEarlierLayout_WithTheMarkerOpeningThePage_StillReadsEverySection(self):
        lines = document(
            section(*SAVER, period="01/05/2025 to 31/05/2025"),
            section(*self.LOAN, period="01/06/2025 to 30/06/2025", loan=True),
        )

        found = read_document(grid(lines), pages(lines))

        assert found is not None
        assert [item.reading.statement_date for item in found] == [
            date(2025, 5, 31),
            date(2025, 6, 30),
        ]

    def test_WithoutPages_TheMarkerStillCuts_SoAOnePageFixtureReadsAsBefore(self):
        lines = self._document()

        found = read_document(grid(lines))

        assert found is not None
        assert [len(item.reading.transactions) for item in found] == [1, 1]
        assert all(item.reading.reconciles for item in found)


class TestEachSectionIsGatedOnItsOwn:
    def test_OneSectionAPennyOut_IsRefusedWithItsReason_AndTheOtherEightAreUsable(self):
        found = CreditUnionStatementPdfParser().sections(pdf(nine_accounts(penny_out=4), step=5.5))

        assert found is not None
        refused = [item for item in found if item.refusal]
        usable = [item for item in found if not item.refusal]
        assert [item.label for item in refused] == ["Junior Saver"]
        assert "unexplained" in refused[0].refusal
        assert len(usable) == 8

    def test_ASectionMissingARow_IsRefused_AndNeighboursStillRead(self):
        moves = list(NINE[0][2])
        document_lines = document(
            section("Regular Saver", 80000, moves[:2], printed_closing_minor=52749),
            section("Christmas Club", 15000, [Move("05/05/2025", "DD Lodgement", 1000)]),
        )
        found = CreditUnionStatementPdfParser().sections(pdf(document_lines))

        assert found is not None
        assert found[0].refusal
        assert not found[1].refusal

    def test_ARefusedSection_YieldsNoRows_ThroughTheImportDoor(self):
        payload = pdf(nine_accounts(penny_out=0), step=5.5)
        parser = CreditUnionStatementPdfParser()

        with pytest.raises(ParseError):
            list(parser.parse_section(payload, "regularsaver", account_id="x"))

    def test_AUsableSection_YieldsItsRowsUnderTheAccountGiven(self):
        payload = pdf(nine_accounts(), step=5.5)
        parser = CreditUnionStatementPdfParser()

        rows = list(parser.parse_section(payload, "regularsaver", account_id="credit-union-saver"))

        assert [row.amount_minor for row in rows] == [2500, 249, -30000]
        assert {row.account_id for row in rows} == {"credit-union-saver"}
        assert {row.source for row in rows} == {"credit-union-pdf"}

    def test_ASectionKeyThatIsNotInTheDocument_IsRefused(self):
        payload = pdf(nine_accounts(), step=5.5)

        with pytest.raises(ParseError, match="no section"):
            list(
                CreditUnionStatementPdfParser().parse_section(
                    payload, "nosuchaccount", account_id="x"
                )
            )


class TestAmbiguousBoundaries:
    """Where the document cannot say what the sections are, it is refused whole.

    A guess here files an account's rows under another account, which the
    arithmetic gate cannot catch because both walks can balance on their own.
    """

    def test_ASectionWhoseLastPageIsMissing_RefusesTheWholeDocument(self):
        # "Page 1 of 2" followed directly by the next account's "Page 1 of 1":
        # either page 2 was lost or the numbering is misread.
        lines = document(
            section("Regular Saver", 80000, [Move("04/05/2025", "DD Lodgement", 2500)], pages=2),
            section("Christmas Club", 15000, []),
        )
        damaged = [line for line in lines if "Page 2 of 2" not in line]

        with pytest.raises(ParseError, match="page numbering"):
            read_document(grid(damaged))

    def test_AMarkerLostBetweenTwoAccounts_RefusesTheWholeDocument(self):
        # With the restart marker gone the two accounts run together and one
        # section names two accounts: the boundary is not knowable.
        lines = document(
            section("Regular Saver", 80000, []),
            section("Christmas Club", 15000, []),
            section("Holiday Fund", 0, []),
        )
        second_marker = [i for i, line in enumerate(lines) if "Page 1 of 1" in line][1]
        damaged = [line for i, line in enumerate(lines) if i != second_marker]

        with pytest.raises(ParseError, match="more than one account"):
            read_document(grid(damaged))

    def test_ASectionWithNoAccountName_RefusesTheWholeDocument(self):
        lines = document(
            section("Regular Saver", 80000, []), section("Christmas Club", 15000, [])
        )
        damaged = [line for line in lines if line != "|Christmas Club"]

        with pytest.raises(ParseError, match="name"):
            read_document(grid(damaged))

    def test_APageNumberedOutOfSequence_RefusesTheWholeDocument(self):
        lines = document(
            section("Regular Saver", 80000, [], pages=2), section("Christmas Club", 15000, [])
        )
        damaged = [line.replace("Page 2 of 2", "Page 3 of 2") for line in lines]

        with pytest.raises(ParseError, match="page numbering"):
            read_document(grid(damaged))

    def test_TheWholeDocumentRefusal_ReachesTheParser_NotJustTheGridReader(self):
        lines = document(
            section("Regular Saver", 80000, [], pages=2), section("Christmas Club", 15000, [])
        )
        damaged = [line for line in lines if "Page 2 of 2" not in line]

        with pytest.raises(ParseError):
            CreditUnionStatementPdfParser().sections(pdf(damaged))


class TestASingleAccountDocumentIsUnchanged:
    def test_OneAccount_IsNotSectioned(self):
        lines = section(*SAVER)

        assert read_document(grid(lines)) is None
        assert CreditUnionStatementPdfParser().sections(pdf(lines)) is None

    def test_OneAccount_IsReadAndImportedExactlyAsBefore(self):
        lines = section(*SAVER)

        rows = list(
            CreditUnionStatementPdfParser().parse(pdf(lines), account_id="credit-union-saver")
        )

        assert [row.amount_minor for row in rows] == [2500]

    def test_AMultiAccountDocument_StillRefusesToBeImportedAsOneAccount(self):
        payload = pdf(nine_accounts(), step=5.5)

        with pytest.raises(ParseError, match="9 accounts"):
            list(CreditUnionStatementPdfParser().parse(payload, account_id="x"))

        assert "9 accounts" in " ".join(read_statement(grid(nine_accounts())).notes)


class TestTheSectionKey:
    def test_TheSameAccount_GetsTheSameKey_InTwoDifferentDocuments(self):
        everything = _sections(nine_accounts())
        reordered = _sections(
            document(
                section("Personal -9.50%", -50000, [], loan=True),
                section("Regular Saver", 80000, []),
            )
        )

        assert {item.label: item.key for item in everything}["Regular Saver"] == {
            item.label: item.key for item in reordered
        }["Regular Saver"]

    def test_ALoan_KeepsItsKey_WhenItsRateChanges(self):
        before = _sections(
            document(section("Personal -9.50%", -50000, [], loan=True), section("Rainy Day", 0, []))
        )
        after = _sections(
            document(section("Personal -8.75%", -50000, [], loan=True), section("Rainy Day", 0, []))
        )

        assert before[0].key == after[0].key
        assert before[0].label != after[0].label, "the label is kept as printed"

    def test_TheKey_IgnoresCaseAndSpacing(self):
        assert section_key("Regular Saver") == section_key("regular  SAVER")
        assert section_key("Regular Saver") == section_key("RegularSaver")

    def test_TwoSectionsSharingALabel_GetOrdinals_SoNeitherCollidesWithTheOther(self):
        found = _sections(
            document(
                section("Regular Saver", 80000, []),
                section("Rainy Day", 0, []),
                section("Regular Saver", 5000, []),
            )
        )

        keys = [item.key for item in found]
        assert len(set(keys)) == 3
        assert keys[0] != keys[2]
        assert keys[1] == section_key("Rainy Day"), "a label held once carries no ordinal"

    def test_TwoDifferentLabels_NeverShareAKey(self):
        found = _sections(nine_accounts())

        assert len({item.key for item in found}) == 9

    def test_AFusedLabel_GivesTheSameKeyAndTheSameReading_AsASpacedOne(self):
        spaced = _sections(nine_accounts())
        fused = _sections(nine_accounts(fused=True))

        assert [item.key for item in fused] == [item.key for item in spaced]
        assert [len(item.reading.transactions) for item in fused] == NINE_ROWS
        assert [item.reading.closing_balance_minor for item in fused] == NINE_CLOSINGS
        assert all(item.reading.reconciles for item in fused)

    def test_AFusedDocument_ThroughARealPage_ReadsAsNineReconcilingSections(self):
        found = CreditUnionStatementPdfParser().sections(
            pdf(nine_accounts(fused=True), step=5.5)
        )

        assert found is not None
        assert len(found) == 9
        assert not [item for item in found if item.refusal]
