"""Three actions say what they do, and the words are declared once.

The deployed pages named a protection's way out and a typed transaction's way out with the same
word ("Withdraw"), an artefact's change of account with a clerk's one ("Refile"), and its
re-derivation with the mechanism ("Replay into store"). The labels are `page_words`' constants,
which the button, its confirmation, its result, and any sentence that points at the button read;
the routes and function names keep their old words.

KNOWN ANSWER: the artefact page shows "Move to another account" and "Rebuild this artefact's
transactions" and neither old label; the result of each is titled with the same verb as its button;
a retired label is caught where it comes back, and "withdraw" as an ordinary word (a cash
withdrawal) is not.
"""

from __future__ import annotations

import re

import httpx
import pytest

from obdi import page_words
from obdi.page_words import RETIRED_ON_PAGES
from page_walk import artefact_ids, invented, served  # noqa: F401
from test_page_wording_vocabulary import text_of


def visible(page: str) -> str:
    return text_of(page).casefold()


def retired_in(page: str) -> list[str]:
    return [phrase for phrase in RETIRED_ON_PAGES if phrase in visible(page)]


class TestTheArtefactPageButtons:
    def test_ArtefactPage_OffersMoveAndRebuildByTheirNewNamesAndNeitherOldLabel(
        self,
        served,  # noqa: F811
        invented,  # noqa: F811
    ):
        db, _ = invented
        page = httpx.get(f"{served}/artefact?id={artefact_ids(db)[0]}", timeout=60).text

        assert re.search(r">Move to another account</button>", page)
        assert re.search(r">Rebuild this artefact&#x27;s transactions</button>", page)
        assert not re.search(r">Refile</button>", page)
        assert "Replay into store" not in page
        assert retired_in(page) == []

    def test_ArtefactPage_StillPostsToTheRoutesTheOldLabelsDid(
        self,
        served,  # noqa: F811
        invented,  # noqa: F811
    ):
        db, _ = invented
        page = httpx.get(f"{served}/artefact?id={artefact_ids(db)[0]}", timeout=60).text

        assert 'action="/refile-artefact"' in page
        assert 'action="/replay-artefact"' in page


class TestTheResultsSayTheVerbOfTheirButton:
    def test_Move_Confirmed_IsTitledWithTheVerbOfItsButton(self, serve_hub):
        base = serve_hub(refile_artefact=lambda artefact_id, account: "starling-space-money")

        page = httpx.post(
            f"{base}/refile-artefact",
            data={"id": "42", "account_other": "starling-personal", "confirm": "yes"},
        ).text

        assert "Artefact moved to another account" in page
        assert "Moved from <strong>starling-space-money</strong> to" in page
        assert retired_in(page) == []

    def test_Move_WithoutConfirmation_SaysMovingAndNotRefiling(self, serve_hub):
        base = serve_hub(refile_artefact=lambda *a: "x")

        response = httpx.post(
            f"{base}/refile-artefact", data={"id": "42", "account": "starling-personal"}
        )

        assert response.status_code == 400
        assert "Moving an artefact changes which account its rows derive into." in response.text
        assert "efiling" not in response.text

    def test_Rebuild_WhenDone_IsTitledWithTheVerbOfItsButton(self, serve_hub):
        base = serve_hub(replay_artefact=lambda artefact_id: "3 parsed, 3 new")

        page = httpx.post(f"{base}/replay-artefact", data={"id": "7"}).text

        assert "Artefact&#x27;s transactions rebuilt" in page
        assert "3 parsed, 3 new" in page
        assert "Artefact replayed" not in page

    def test_Rebuild_WhenItFails_SaysCouldNotRebuild(self, serve_hub):
        def fail(artefact_id: int) -> str:
            raise ValueError("unreadable")

        base = serve_hub(replay_artefact=fail)

        response = httpx.post(f"{base}/replay-artefact", data={"id": "7"})

        assert response.status_code == 400
        assert "Could not rebuild the artefact&#x27;s transactions" in response.text


class TestTheLabelsAreDeclaredOnce:
    def test_Labels_AreTheOnesThePagesShow(self):
        assert page_words.REMOVE_PROTECTION == "Remove protection"
        assert page_words.REMOVE_TYPED_TRANSACTION == "Remove typed transaction"
        assert page_words.MOVE_ARTEFACT == "Move to another account"
        assert page_words.REBUILD_ARTEFACT == "Rebuild this artefact's transactions"

    def test_ResultTitles_ReadTheSameVerbAsTheirButton(self):
        assert page_words.PROTECTION_REMOVED == "Protection removed"
        assert page_words.TYPED_TRANSACTION_REMOVED == "Typed transaction removed"
        assert page_words.ARTEFACT_MOVED.startswith("Artefact moved")
        assert page_words.ARTEFACT_REBUILT.endswith("rebuilt")


class TestARetiredLabelIsCaughtWhereItComesBack:
    @pytest.mark.parametrize(
        "label",
        ["Withdraw protection", "Withdraw this typed transaction", "Refile", "Replay into store"],
    )
    def test_PageWithAnOldButtonLabel_IsCaught(self, label):
        page = f'<form><p><button class="button" type="submit">{label}</button></p></form>'

        assert retired_in(page), label

    @pytest.mark.parametrize(
        "sentence",
        [
            "<p>A cash withdrawal of 20.00 was recorded as a transfer.</p>",
            "<p>The withdrawal from the machine is paired with the deposit.</p>",
            "<p>Answer withdrawn: the flag is open again.</p>",
            "<p>Corrected: refiled from starling-space-money to starling-personal.</p>",
        ],
    )
    def test_PageUsingTheVerbOrNounOrdinarily_IsNotCaught(self, sentence):
        assert retired_in(sentence) == [], sentence

    def test_RetiredList_NamesLabelsAndNotTheBareVerb(self):
        assert "withdraw" not in RETIRED_ON_PAGES
        assert "withdrawal" not in RETIRED_ON_PAGES
