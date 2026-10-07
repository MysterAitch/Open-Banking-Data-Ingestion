"""Telling whose statement a document is, by counting the issuer names in it.

The count is the evidence: an issuer prints its name on every page and a payee
is named once per payment. Each expectation below is counted by hand from the
lines given.
"""

from __future__ import annotations

from obdi.ingest.statement_names import ISSUER_NAMES, names_found


class TestTheIssuerIsTheNameFoundMost:
    def test_Statement_NamingItsIssuerOftenAndAPayeeOnce_RanksTheIssuerFirst(self):
        lines = [
            "Halifax Clarity Credit Card",
            "Your Halifax statement",
            "12 MAY   SANTANDER CARDS PAYMENT        20.00",
            "Halifax is a division of Bank of Scotland plc",
        ]

        assert names_found(lines) == [("Halifax", 3), ("Bank of Scotland", 1), ("Santander", 1)]

    def test_Statement_NamingNoListedName_FindsNone(self):
        assert names_found(["Statement of account", "Opening balance 10.00"]) == []

    def test_NoLines_FindsNone(self):
        assert names_found([]) == []


class TestOnlyWholeNamesCount:
    def test_AShortNameInsideALongerWord_IsNotFound(self):
        """"Chase" is in "purchase" and "Visa" is in "advisable"."""
        assert names_found(["Your purchase is advisable", "PURCHASES this month"]) == []

    def test_ANameBesidePunctuation_IsFound(self):
        assert names_found(["(Santander) Santander, Santander."]) == [("Santander", 3)]

    def test_Case_DoesNotMatter(self):
        assert names_found(["VIRGIN MONEY", "virgin money", "Virgin Money"]) == [
            ("Virgin Money", 3)
        ]

    def test_ATwoWordName_SplitByAWideGapInTheTextLayer_IsStillFound(self):
        assert names_found(["Virgin      Money credit card"]) == [("Virgin Money", 1)]

    def test_AnIssuerThatSharesAWordWithAnother_IsCountedOnItsOwn(self):
        """"Barclaycard" must not also count as "Barclays", nor the reverse."""
        assert names_found(["Barclaycard", "Barclays Bank UK PLC"]) == [
            ("Barclaycard", 1),
            ("Barclays", 1),
        ]


class TestOnlyListedNamesAreEverReported:
    def test_AWordThatIsNotOnTheList_IsNeverReported(self):
        """The page this feeds is served on a GET: a payee must not appear."""
        found = names_found(["ZEBRAQUARTZ HOLDINGS LONDON GB   12.00", "Halifax"])

        assert found == [("Halifax", 1)]
        assert all(name in ISSUER_NAMES for name, _ in found)
