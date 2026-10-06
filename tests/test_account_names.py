"""A page names an account by its label, with the reference beside it, and never twice.

A review of the interface's words found the reference alone (`starling-personal`) on the
reports, Identity health, the review report, Statement periods, and the agreements page, where
the home page already led with the account's name.

KNOWN ANSWER: an account with a label shows "label (reference)" wherever a report prints its
reference; one without shows the reference once; a longer reference that merely begins with a
shorter one is never rewritten by the shorter one.
"""

from __future__ import annotations

import httpx
import pytest

from obdi.account_names import AccountShown, AccountsShown, accounts_shown, code_html
from obdi.accounts import AccountRecord, AccountRef
from obdi.store import Store
from page_walk import invented, served, walked_pages  # noqa: F401

NAMES = accounts_shown(
    {"starling-personal": "Joint current", "halifax-current": "Halifax current"}, []
)


def shown_as(ref: str, label: str) -> AccountsShown:
    return AccountsShown([AccountShown.named(ref, label)])


class TestTheNameShown:
    def test_Name_WhenLabelled_IsTheLabel(self):
        assert NAMES.of("starling-personal").name == "Joint current"

    def test_Name_WhenUnlabelled_IsTheReference(self):
        assert NAMES.of("synthetic-card").name == "synthetic-card"

    def test_Names_WhenDeclaredAndProviderBothName_TheDeclaredLabelWins(self):
        record = AccountRecord(ref=AccountRef("starling-personal"), label="My own name")

        names = accounts_shown({"starling-personal": "Provider name", "other": "Other"}, [record])

        assert names.of("starling-personal").name == "My own name"
        assert names.of("other").name == "Other"

    def test_Names_WhenADeclaredAccountHasNoLabel_AreItsReferenceAndTheAccountIsKnown(self):
        record = AccountRecord(ref=AccountRef("tin"), label="")

        names = accounts_shown({}, [record])

        assert "tin" in names
        assert names.of("tin").name == "tin"
        assert not names.of("tin").labelled

    def test_Inline_WhenLabelled_LeadsWithTheLabelAndSetsTheReferenceAsCode(self):
        shown = NAMES.of("starling-personal").inline()

        assert shown == "<strong>Joint current</strong> <code>starling-personal</code>"

    @pytest.mark.parametrize("label", ["", "synthetic-card"])
    def test_Inline_WhenThereIsNoLabel_ShowsTheReferenceOnceAsCode(self, label):
        shown = AccountShown.named("synthetic-card", label)

        assert shown.inline() == "<code>synthetic-card</code>"
        assert shown.text() == "synthetic-card"

    def test_Forms_OfALabelledAccount_AreHeadingTextAndCode(self):
        shown = NAMES.of("starling-personal")

        assert shown.heading() == "Joint current"
        assert shown.text() == "Joint current (starling-personal)"
        assert shown.code() == "<code>starling-personal</code>"

    def test_Forms_WhenTheNameHoldsMarkup_AreEscaped(self):
        shown = AccountShown.named("a", "<b>x</b> & y")

        assert "<b>" not in shown.heading()
        assert "<b>" not in shown.inline()
        assert code_html("<i>") == "<code>&lt;i&gt;</code>"


class TestTwoAccountsThatWouldReadTheSame:
    """Two undeclared accounts carry the one label their provider gave both ("Mr Roger Howell"),
    and a third is named differently. The two are told apart by their references, as code,
    wherever a name is given; the third reads as it always did."""

    SHARED = accounts_shown(
        {
            "halifax-a": "Mr Roger Howell",
            "halifax-b": "Mr Roger Howell",
            "halifax-cc": "Halifax CC",
        },
        [],
    )

    def test_AsName_ForEachOfTheTwo_HasTheReferenceBesideTheLabelAsCode(self):
        assert self.SHARED.of("halifax-a").as_name() == ("Mr Roger Howell <code>halifax-a</code>")
        assert self.SHARED.of("halifax-b").as_name() == ("Mr Roger Howell <code>halifax-b</code>")

    def test_PlainNameAndHeading_ForEachOfTheTwo_CarryTheReference(self):
        assert self.SHARED.of("halifax-a").name == "Mr Roger Howell (halifax-a)"
        assert self.SHARED.of("halifax-b").heading() == "Mr Roger Howell (halifax-b)"

    def test_TheDifferentlyNamedAccount_IsUnchanged(self):
        assert self.SHARED.of("halifax-cc").as_name() == "Halifax CC"
        assert self.SHARED.of("halifax-cc").name == "Halifax CC"

    def test_ADeclaredLabel_RemovesTheNeedForTheReference(self):
        declared = accounts_shown(
            {"halifax-a": "Mr Roger Howell", "halifax-b": "Mr Roger Howell"},
            [AccountRecord(ref=AccountRef("halifax-b"), label="Halifax Saver")],
        )

        assert declared.of("halifax-a").as_name() == "Mr Roger Howell"
        assert declared.of("halifax-b").as_name() == "Halifax Saver"

    def test_TwoAccountsWithNoLabelAtAll_AreNotAmbiguousBecauseEachReadsAsItsReference(self):
        bare = AccountsShown([AccountShown("a"), AccountShown("b")])

        assert bare.of("a").as_name() == "<code>a</code>"

    def test_InText_ForEachOfTheTwo_WritesLabelAndReference(self):
        assert self.SHARED.in_text("halifax-a: ok") == "Mr Roger Howell (halifax-a): ok"


class TestAReportsPlainText:
    def test_InText_WhenAReferenceIsLabelled_WritesLabelThenReference(self):
        said = NAMES.in_text("starling-personal via monzo-csv: 3 rows")

        assert said == "Joint current (starling-personal) via monzo-csv: 3 rows"

    def test_InText_WhenAReferenceIsPartOfALongerOne_LeavesTheLongerOneAlone(self):
        said = NAMES.in_text("starling-personal-joint and starling-personal")

        assert said == "starling-personal-joint and Joint current (starling-personal)"

    def test_InText_WhenTheLabelIsTheReference_ChangesNothing(self):
        assert shown_as("synthetic-card", "synthetic-card").in_text("synthetic-card: 2 rows") == (
            "synthetic-card: 2 rows"
        )

    def test_InText_WhenNoAccountHasALabel_ChangesNothing(self):
        assert AccountsShown().in_text("anything at all") == "anything at all"

    def test_InText_WhenAReferenceIsAnOrdinaryWord_LeavesThatWordAloneInASentence(self):
        """An account's reference was the word "cash", and a report then read "Cash (cash)
        withdrawals ... the word a bank uses for a Cash (cash) machine"."""
        names = accounts_shown({"cash": "Cash", "starling-personal": "Joint current"}, [])
        said = names.in_text(
            "19 stored transactions are cash withdrawals. The word for a cash machine is here."
        )

        assert said == (
            "19 stored transactions are cash withdrawals. The word for a cash machine is here."
        )

    @pytest.mark.parametrize(
        ("line", "expected"),
        [
            ("cash:", "Cash (cash):"),
            ("  cash:", "  Cash (cash):"),
            ("cash via truelayer: 4 reported", "Cash (cash) via truelayer: 4 reported"),
            ("    nil-amount / cash: 1", "    nil-amount / Cash (cash): 1"),
        ],
    )
    def test_InText_WhenAnOrdinaryWordReferenceHeadsALine_StillNamesTheAccount(
        self, line, expected
    ):
        assert shown_as("cash", "Cash").in_text(line) == expected

    def test_InText_WhenAReferenceHeadsASectionWithAColon_NamesTheAccount(self):
        said = NAMES.in_text("starling-personal:\n19 rows")

        assert said == "Joint current (starling-personal):\n19 rows"

    def test_InText_WhenAColonJoinsAProviderToItsOwnId_LeavesItAlone(self):
        """`starling:abc123` is a provider's own account id, not the account called starling."""
        said = shown_as("starling", "Starling main").in_text("starling:abc123 holds 2 rows")

        assert said == "starling:abc123 holds 2 rows"


class TestThePagesUseIt:
    def test_IdentityHealth_WhenAnAccountIsDeclaredWithALabel_NamesItByLabelFirst(
        self, served, invented, tmp_path  # noqa: F811
    ):
        db, _ = invented
        with Store(db) as store:
            store.declare_account(
                AccountRecord(ref=AccountRef("tok-current"), label="Tok everyday")
            )

        page = httpx.get(f"{served}/identity-health", timeout=60).text

        assert "Tok everyday (tok-current)" in page
