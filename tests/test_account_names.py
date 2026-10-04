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

from obdi.account_names import account_name, merged_names, name_html, name_text
from obdi.accounts import AccountRecord, AccountRef
from obdi.store import Store
from page_walk import invented, served, walked_pages  # noqa: F401

NAMES = {"starling-personal": "Joint current", "halifax-current": "Halifax current"}


class TestTheNameShown:
    def test_AccountName_WhenLabelled_IsTheLabel(self):
        assert account_name("starling-personal", NAMES) == "Joint current"

    def test_AccountName_WhenUnlabelled_IsTheReference(self):
        assert account_name("synthetic-card", NAMES) == "synthetic-card"

    def test_MergedNames_WhenDeclaredAndProviderBothName_TheDeclaredLabelWins(self):
        record = AccountRecord(ref=AccountRef("starling-personal"), label="My own name")

        merged = merged_names({"starling-personal": "Provider name", "other": "Other"}, [record])

        assert merged == {"starling-personal": "My own name", "other": "Other"}

    def test_NameHtml_WhenLabelled_LeadsWithTheLabelAndKeepsTheReferenceSmall(self):
        shown = name_html("starling-personal", NAMES)

        assert shown.index("Joint current") < shown.index("starling-personal")
        assert "muted" in shown

    @pytest.mark.parametrize("names", [{}, {"synthetic-card": "synthetic-card"}])
    def test_NameHtml_WhenThereIsNoLabel_ShowsTheReferenceOnce(self, names):
        assert name_html("synthetic-card", names).count("synthetic-card") == 1


class TestAReportsPlainText:
    def test_NameText_WhenAReferenceIsLabelled_WritesLabelThenReference(self):
        said = name_text("starling-personal via monzo-csv: 3 rows", NAMES)

        assert said == "Joint current (starling-personal) via monzo-csv: 3 rows"

    def test_NameText_WhenAReferenceIsPartOfALongerOne_LeavesTheLongerOneAlone(self):
        said = name_text("starling-personal-joint and starling-personal", NAMES)

        assert said == "starling-personal-joint and Joint current (starling-personal)"

    def test_NameText_WhenTheLabelIsTheReference_ChangesNothing(self):
        assert name_text("synthetic-card: 2 rows", {"synthetic-card": "synthetic-card"}) == (
            "synthetic-card: 2 rows"
        )

    def test_NameText_WhenNoAccountHasALabel_ChangesNothing(self):
        assert name_text("anything at all", {}) == "anything at all"


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
