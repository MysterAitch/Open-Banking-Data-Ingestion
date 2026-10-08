"""Channels and processors every bank prints are not payees (read on a real store, 2026-10-08).

KNOWN ANSWERS, decided before the first run (every merchant invented). Each of these was offered
as one payee headed by the channel word, because the rows share it and nothing else:

  www                "www alder com", "www birch co uk"                       two different sites
  visa purchase      "visa purchase alder google", "... birch google", ...   three merchants
  zettle             "zettle alder", "zettle birch", "zettle cedar"          three merchants
  sumup              "sumup alder", "sumup birch"                            two merchants
  paypal             "paypal alder", "paypal birch"                          two merchants
  first payment      "direct debit first payment alder", "... birch"         two merchants

so none is offered as a SHAPE group any more, whilst a control phrase that is no channel
("vendorpay alder", "vendorpay birch") still is, which shows the harness would have offered them.
And: "paypal" alone is still PayPal (a top-up is paid to PayPal); a merchant that only ever
appears as "zettle <name>" is named <name>; "visa purchase <name> google" is <name>.
"""

from __future__ import annotations

import pytest

from obdi.analysis.entities import name_of, shape_of, view_of
from obdi.analysis.entity_tokens import core_words, distinctive_words, tokens_of

GROUPS = {
    "www": ["WWW ALDER COM", "WWW BIRCH CO UK", "WWW CEDAR COM"],
    "visa purchase": [
        "VISA PURCHASE ALDER GOOGLE",
        "VISA PURCHASE BIRCH GOOGLE",
        "VISA PURCHASE CEDAR GOOGLE",
    ],
    "zettle": ["ZETTLE ALDER", "ZETTLE BIRCH", "ZETTLE CEDAR"],
    "sumup": ["SUMUP ALDER", "SUMUP BIRCH", "SUMUP CEDAR"],
    "paypal": ["PAYPAL ALDER", "PAYPAL BIRCH", "PAYPAL CEDAR"],
    "first payment": [
        "DIRECT DEBIT FIRST PAYMENT ALDER",
        "DIRECT DEBIT FIRST PAYMENT BIRCH",
        "DIRECT DEBIT FIRST PAYMENT CEDAR",
    ],
    "interest": ["BALANCE TRANSFER INTEREST", "INTEREST", "INTEREST CHARGED"],
}
CONTROL = ["VENDORPAY ALDER", "VENDORPAY BIRCH", "VENDORPAY CEDAR"]


def offered(descriptions: list[str]):
    counts = {shape_of(text): 4 for text in descriptions}
    view = view_of(counts, [])
    return [*view.proposals.groups, *view.proposals.too_broad]


class TestAChannelIsNotAParty:
    @pytest.mark.parametrize("channel", GROUPS)
    def test_RowsSharingOnlyAChannel_AreNotOfferedAsOnePayee(self, channel):
        assert offered(GROUPS[channel]) == []

    def test_ControlPhraseThatIsNoChannel_IsStillOffered(self):
        assert offered(CONTROL) != [], "the harness would have offered the channels"


class TestTheNameIsWhatFollowsTheChannel:
    @pytest.mark.parametrize(
        ("printed", "named"),
        [
            ("ZETTLE ALDER", "alder"),
            ("VISA PURCHASE ALDER GOOGLE", "alder"),
            ("WWW ALDER COM", "alder"),
            ("PAYPAL ALDER", "alder"),
            ("DIRECT DEBIT FIRST PAYMENT ALDER", "alder"),
            ("SUMUP BIRCH", "birch"),
        ],
    )
    def test_RowBehindAChannel_IsComparedByTheMerchant(self, printed, named):
        assert core_words(shape_of(printed)) == [named]

    def test_PayPalAlone_IsStillComparedAsPayPal(self):
        assert core_words(shape_of("PAYPAL")) == ["paypal"]
        assert distinctive_words(tokens_of("paypal")) == {"paypal"}

    def test_AMerchantOnlySeenBehindZettle_IsStillComparedByIt(self):
        assert {tuple(core_words(shape_of(f"ZETTLE ALDER {n}"))) for n in range(3)} == {("alder",)}

    def test_TheShapeKeepsTheChannelSoTheRowsAreStillCountedAsPrinted(self):
        """What a row is called is unchanged (`name_of` keeps the shape); only the comparison that
        proposes groups sets the channel aside."""
        assert name_of("ZETTLE ALDER").name == "zettle alder"
