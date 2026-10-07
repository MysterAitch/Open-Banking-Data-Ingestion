"""The Entities pages say what each name was made from, and an attachment to a name no row has now
is listed rather than lost.

KNOWN ANSWERS, decided before the first run. The people are invented.

  - A name made from a stated counterparty is derived "From the bank's merchant name:", showing the
    counterparty as stated, an arrow, and the name.
  - A name whose transactions are named through a link says "through 6 payments seen by both"
    (the count the link was learned from), and "1 payment" for one.
  - Twelve rows to one counterparty (six also stated, six description-only through the link) and
    a lone description-only row elsewhere: the summary says 1 name from the bank's merchant name
    and 1 from the description, and that 6 transactions were named through payments seen by both.
  - A description-only row whose description matches a stated name exactly (after the country
    code is set aside) is "from the description, which matches the bank's merchant name “X”
    exactly"; the summary counts those transactions and prints no name.
  - A hand-attached name that no row has now ("rent", once the rows state a housemate) is listed
    on the entity's page as attached to a name no row has now, with its count on the masked page
    and its text on the unmasked one; a hand-attached name some row still has is not listed.
"""

from __future__ import annotations

from datetime import date
from typing import ClassVar

from obdi.analysis.entities import (
    ALIAS,
    DESCRIPTION,
    MATCHED_NAME,
    STATED_NAME,
    TRUNCATED_NAME,
    Covered,
    Entity,
    derivation_of,
    entity_page_of,
    name_origins,
    names_of,
    view_of,
)
from obdi.pages.web_entities import derivation_html, render_entities, summary_line
from obdi.pages.web_entity import render_entity


def covered(description: str, counterparty: str, kind: str, via: str = "", support: int = 0):
    return Covered(
        date(2026, 9, 10), "current-main", "", -999, "GBP", description, "0123456789ab",
        counterparty=counterparty, kind=kind, via=via, support=support,
    )


class TestTheDerivationSaysWhichKindNamedIt:
    def test_Derivation_ForAStatedCounterparty_IsFromTheBanksMerchantName(self):
        record = derivation_of("alex rowan", (covered("JAN RENT", "Alex ROWAN", STATED_NAME),))

        assert record.source == "bank's merchant name"
        assert record.printed == ("Alex ROWAN",)
        assert record.steps == ("letters are lower-cased and punctuation becomes spaces",)
        assert "From the bank&#x27;s merchant name:" in derivation_html(record)

    def test_Derivation_ForALinkedRow_SaysHowManyPaymentsSeenByBothTheLinkRestsOn(self):
        row = covered("BRAMBLEWICK LEEDS 8841", "", ALIAS, via="bramblewick leeds", support=6)

        record = derivation_of("bramblewick", (row,))

        assert record.source.endswith("through 6 payments seen by both")
        assert record.printed == ("BRAMBLEWICK LEEDS 8841",)

    def test_Derivation_ForALinkRestingOnOnePayment_SaysOnePaymentNotPayments(self):
        row = covered("BRAMBLEWICK LEEDS", "", ALIAS, via="bramblewick leeds", support=1)

        assert "through 1 payment seen by both" in derivation_of("bramblewick", (row,)).source

    def test_Derivation_ForAMixOfStatedAndDescribedRows_SaysEachKindOnce(self):
        rows = (
            covered("X", "Bramblewick", STATED_NAME),
            covered("BRAMBLEWICK LEEDS", "", DESCRIPTION),
        )

        record = derivation_of("bramblewick", rows)

        assert record.source == "bank's merchant name, and from the description"
        assert record.kinds == (STATED_NAME, DESCRIPTION)


class TestAMatchedDescriptionSaysItMatchedTheMerchantNameExactly:
    ROWS = (
        [("DEPOT CLIMB", "Depot Climb Birmingham")] * 4
        + [("DEPOT CLIMB BIRMINGHAM GB", "")] * 3
    )

    def test_Derivation_ForAMatchedRow_NamesTheMerchantNameItMatched(self):
        row = covered(
            "DEPOT CLIMB BIRMINGHAM GB", "", MATCHED_NAME, via="depot climb birmingham gb"
        )

        record = derivation_of("depot climb birmingham", (row,))

        assert record.source == (
            "description, which matches the bank's merchant name “depot climb birmingham” exactly"
        )
        assert record.kinds == (MATCHED_NAME,)

    def test_Derivation_ForStatedAndMatchedRows_SaysEachKindOnce(self):
        rows = (
            covered("X", "Depot Climb Birmingham", STATED_NAME),
            covered("DEPOT CLIMB BIRMINGHAM GB", "", MATCHED_NAME),
        )

        record = derivation_of("depot climb birmingham", rows)

        assert record.source.startswith("bank's merchant name, and from the description, which")
        assert "matches the bank's merchant name" in record.source

    def test_Summary_CountsMatchedTransactionsAndNoName(self):
        origins = name_origins(names_of(self.ROWS))
        counts = {name: origin.rows for name, origin in origins.items()}
        view = view_of(counts, [], origins=origins)

        line = summary_line(view)

        assert counts == {"depot climb birmingham": 7}
        assert "3 transactions named by a description that matches a merchant name exactly" in line
        assert "Names: 1 from the bank's merchant name;" in line

    def test_Page_ListsTheMatchedNamesFoldWithTheSentence(self):
        origins = name_origins(names_of(self.ROWS))
        counts = {name: origin.rows for name, origin in origins.items()}
        covers = {
            "depot climb birmingham": (
                covered("DEPOT CLIMB BIRMINGHAM GB", "", MATCHED_NAME),
                covered("DEPOT CLIMB", "Depot Climb Birmingham", STATED_NAME),
            )
        }
        held = Entity(1, "Depot", None, ("depot climb birmingham",))
        view = view_of(counts, [held], None, covers, origins)

        page = render_entities(view, unmasked=True).decode("utf-8")

        assert "which matches the bank&#x27;s merchant name" in page
        assert "exactly" in page


class TestATruncatedDescriptionSaysItIsTheStartOfTheMerchantName:
    ROWS = (
        [("DCB", "Depot Climb Birmingham")] * 4
        + [("DEPOT CLIMB BIRMINGH", "")] * 3
    )

    def test_Derivation_ForATruncatedRow_NamesTheMerchantNameItOpens(self):
        row = covered("DEPOT CLIMB BIRMINGH", "", TRUNCATED_NAME, via="depot climb birmingh")

        record = derivation_of("depot climb birmingham", (row,))

        assert record.source == (
            "description, a truncation of the bank's merchant name “depot climb birmingham”"
        )
        assert record.kinds == (TRUNCATED_NAME,)

    def test_Summary_CountsTruncatedTransactionsAndNoName(self):
        origins = name_origins(names_of(self.ROWS))
        counts = {name: origin.rows for name, origin in origins.items()}
        view = view_of(counts, [], origins=origins)

        line = summary_line(view)

        assert counts == {"depot climb birmingham": 7}
        assert origins["depot climb birmingham"].truncated == 3
        assert "3 transactions named by a description that is the cut-off start of" in line
        assert "Names: 1 from the bank's merchant name;" in line

    def test_MaskedPage_StillCountsTheTruncatedRowsAndNamesNothing(self):
        origins = name_origins(names_of(self.ROWS))
        counts = {name: origin.rows for name, origin in origins.items()}

        page = render_entities(view_of(counts, [], origins=origins), unmasked=False).decode()

        assert "3 transactions named by a description that is the cut-off start" in page
        assert "depot" not in page.lower()

    def test_Page_ListsTheTruncatedNamesFoldWithTheSentence(self):
        origins = name_origins(names_of(self.ROWS))
        counts = {name: origin.rows for name, origin in origins.items()}
        covers = {
            "depot climb birmingham": (
                covered("DEPOT CLIMB BIRMINGH", "", TRUNCATED_NAME),
                covered("DCB", "Depot Climb Birmingham", STATED_NAME),
            )
        }
        held = Entity(1, "Depot", None, ("depot climb birmingham",))
        view = view_of(counts, [held], None, covers, origins)

        page = render_entities(view, unmasked=True).decode("utf-8")

        assert "a truncation of the bank&#x27;s merchant name" in page


class TestTheSummaryCountsNamesByKind:
    ROWS = (
        [("BRAMBLEWICK LEEDS", "")] * 6
        + [("BRAMBLEWICK LEEDS", "Bramblewick")] * 6
        + [("OAKMERE COFFEE 12", "")]
    )

    def view(self):
        origins = name_origins(names_of(self.ROWS))
        counts = {name: origin.rows for name, origin in origins.items()}
        return view_of(counts, [], origins=origins)

    def test_Summary_SaysHowManyNamesCameFromEachKindAndHowManyRowsWereLinked(self):
        line = summary_line(self.view())

        assert line.startswith("2 payee names across every account;")
        assert "Names: 1 from the bank's merchant name; 1 from the description; " in line
        assert "6 transactions named through payments seen by both." in line

    def test_Summary_WhenTheViewCarriesNoOrigins_SaysNothingAboutKinds(self):
        assert "Names:" not in summary_line(view_of({"oakmere coffee": 2}, []))

    def test_MaskedPage_StillSaysTheCountsAndNoName(self):
        page = render_entities(self.view(), unmasked=False).decode("utf-8")

        assert "Names: 1 from the bank&#x27;s merchant name" in page
        assert "bramblewick" not in page.lower()


class TestAnAttachmentToANameNoRowHasIsListed:
    COUNTS: ClassVar[dict[str, int]] = {"alex rowan": 12, "oakmere coffee": 3}

    def page(self):
        held = Entity(1, "Housemate", None, ("oakmere coffee", "rent"))
        return entity_page_of(1, [held], [], self.COUNTS, {})

    def test_Orphaned_ListsOnlyTheNamesNoRowHas(self):
        assert self.page().orphaned == ("rent",)

    def test_UnmaskedPage_ListsTheOrphanWithItsExplanation(self):
        page = render_entity(self.page(), unmasked=True).decode("utf-8")

        assert "Attached to a name no row has now: 1" in page
        assert ">rent<" in page

    def test_MaskedPage_CountsTheOrphanAndNamesNothing(self):
        page = render_entity(self.page(), unmasked=False).decode("utf-8")

        assert "Attached to a name no row has now: 1" in page
        assert ">rent<" not in page

    def test_Page_WhenEveryAttachmentStillHasRows_ListsNoOrphan(self):
        held = Entity(1, "Housemate", None, ("alex rowan",))
        page = entity_page_of(1, [held], [], self.COUNTS, {})

        assert page is not None
        assert page.orphaned == ()
        assert "no row has now" not in render_entity(page, unmasked=True).decode("utf-8")
