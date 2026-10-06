"""What obdi can tell about whose a freshly kept statement is, before it asks.

Every answer was decided before the first run, over invented kept-statement listings (the shape
`kept_statements` gives, with the keys the guess reads):

  * one earlier Santander statement went to `up-card`; a new one read by the same reader that
    names the same issuer is guessed as `up-card`, on the reader alone;
  * two earlier ones went to `up-card` and `other-card`: the reader cannot choose, so a new file
    is guessed only by its name - `Up-card-2026-09.pdf` is the earlier `Up-card-2025-09.pdf`
    with another year, so `up-card`; `Santander-2026-09.pdf` matches neither, so no guess;
  * a reader and a name that disagree about the account make no guess at all;
  * a document with no issuer name found is never matched on its reader, however many earlier
    documents share the reader, because a reader alone says nothing about whose it is;
  * a statement that holds several accounts is planned section by section: a section already
    held is left, a section whose label an earlier document assigned is planned to that account,
    and a section nobody has assigned (or one refused) makes the whole document unplanned.
"""

from __future__ import annotations

from obdi.bring_in_guess import (
    GuessBasis,
    file_shape,
    guess_account,
    plan_sections,
)

UNASSIGNED = "(unassigned)"


def kept(
    ident: int,
    origin: str,
    account: str = UNASSIGNED,
    *,
    parser: str | None = "santander-card",
    names: tuple[str, ...] = ("Santander",),
    last_day: str = "2025-09-10",
    sections: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    return {
        "id": ident,
        "origin": origin,
        "account_ref": account,
        "parser": parser,
        "names": [[name, 3] for name in names],
        "listed_days": ["2025-08-11", last_day],
        "sections": sections or [],
    }


class TestTheShapeOfAFileName:
    def test_FileName_WhenOnlyTheYearDiffers_ShapesTheSame(self):
        assert file_shape("Up-card-2025-09.pdf") == file_shape("UP-CARD-2026-09.PDF")

    def test_FileName_WhenAMonthOrAWordDiffers_ShapesDifferently(self):
        assert file_shape("Up-card-2025-09.pdf") != file_shape("Up-card-2025-10.pdf")
        assert file_shape("Up-card-2025-09.pdf") != file_shape("Other-card-2025-09.pdf")

    def test_FileName_WithSeparatorsInsteadOfDashes_ShapesLikeDashes(self):
        assert file_shape("Up card 2025_09.pdf") == file_shape("Up-card-2026-09.pdf")


class TestGuessingByTheReader:
    def test_Statement_WhenOneEarlierReadingWentToOneAccount_IsGuessedForThatAccount(self):
        new = kept(10, "June.pdf")

        guess = guess_account(new, [kept(1, "May.pdf", "up-card")])

        assert guess is not None
        assert (guess.account, guess.basis) == ("up-card", GuessBasis.READER)
        assert guess.match_origin == "May.pdf"
        assert guess.match_year == "2025"

    def test_Statement_WhenTheEarlierOnesWentToTwoAccounts_IsNotGuessedByTheReader(self):
        earlier = [kept(1, "May.pdf", "up-card"), kept(2, "April.pdf", "other-card")]

        assert guess_account(kept(10, "June.pdf"), earlier) is None

    def test_Statement_WhenTheEarlierOneIsReadByAnotherReader_IsNotGuessed(self):
        earlier = [kept(1, "May.pdf", "up-card", parser="starling-pdf")]

        assert guess_account(kept(10, "June.pdf"), earlier) is None

    def test_Statement_WhenTheEarlierOneNamesAnotherIssuer_IsNotGuessed(self):
        earlier = [kept(1, "May.pdf", "up-card", names=("Barclaycard",))]

        assert guess_account(kept(10, "June.pdf"), earlier) is None

    def test_Statement_WhenNoIssuerNameWasFoundInEither_IsNeverMatchedOnItsReaderAlone(self):
        new = kept(10, "June.pdf", names=())
        earlier = [kept(1, "May.pdf", "up-card", names=())]

        assert guess_account(new, earlier) is None

    def test_Statement_WhenNoReaderReadsIt_IsNotGuessedByTheReader(self):
        new = kept(10, "June.pdf", parser=None)
        earlier = [kept(1, "May.pdf", "up-card", parser=None)]

        assert guess_account(new, earlier) is None

    def test_Statement_WhenTheEarlierOneIsStillWaitingForAnAccount_IsNotAGuide(self):
        assert guess_account(kept(10, "June.pdf"), [kept(1, "May.pdf")]) is None

    def test_Statement_WhenSeveralEarlierOnesWentToTheSameAccount_NamesTheNewestOfThem(self):
        earlier = [kept(1, "May.pdf", "up-card"), kept(3, "July.pdf", "up-card")]

        guess = guess_account(kept(10, "June.pdf"), earlier)

        assert guess is not None and guess.match_origin == "July.pdf"


class TestGuessingByTheFileName:
    def two_cards(self) -> list[dict[str, object]]:
        return [
            kept(1, "Up-card-2025-09.pdf", "up-card"),
            kept(2, "Other-card-2025-09.pdf", "other-card"),
        ]

    def test_Statement_WhenTheReaderCannotChoose_ItsNameAnEarlierFileSharesChooses(self):
        guess = guess_account(kept(10, "Up-card-2026-09.pdf"), self.two_cards())

        assert guess is not None
        assert (guess.account, guess.basis) == ("up-card", GuessBasis.NAME)
        assert guess.match_origin == "Up-card-2025-09.pdf"

    def test_Statement_WhenItsNameMatchesNothing_IsNotGuessed(self):
        assert guess_account(kept(10, "Santander-2026-09.pdf"), self.two_cards()) is None

    def test_Statement_WhenNoReaderReadsIt_ItsNameIsStillEnough(self):
        new = kept(10, "Up-card-2026-09.pdf", parser=None, names=())

        guess = guess_account(new, self.two_cards())

        assert guess is not None and guess.basis is GuessBasis.NAME

    def test_Statement_WhenTheNameMatchesFilesOfTwoAccounts_IsNotGuessed(self):
        earlier = [
            kept(1, "Statement-2025-09.pdf", "up-card"),
            kept(2, "Statement-2024-09.pdf", "other-card"),
        ]

        assert guess_account(kept(10, "Statement-2026-09.pdf"), earlier) is None

    def test_Statement_WhenReaderAndNameAgree_SaysBothAndKeepsTheAccount(self):
        earlier = [kept(1, "Up-card-2025-09.pdf", "up-card")]

        guess = guess_account(kept(10, "Up-card-2026-09.pdf"), earlier)

        assert guess is not None
        assert (guess.account, guess.basis) == ("up-card", GuessBasis.BOTH)

    def test_Statement_WhenReaderAndNameDisagree_IsNotGuessedAtAll(self):
        earlier = [
            kept(1, "May.pdf", "up-card"),
            kept(2, "Other-card-2025-09.pdf", "other-card", parser="starling-pdf"),
        ]

        assert guess_account(kept(10, "Other-card-2026-09.pdf"), earlier) is None

    def test_Statement_WhenItIsItsOwnEarlierSelf_IsNotItsOwnEvidence(self):
        new = kept(10, "Up-card-2026-09.pdf", "up-card")

        assert guess_account(new, [new]) is None

    def test_Statement_WhenItIsAllAccountsDocument_IsNeverGuessedAsOneAccount(self):
        section = {"token": "t1", "label": "Saver", "account": "", "suggested": "", "refusal": ""}
        new = kept(10, "Up-card-2026-09.pdf", sections=[section])

        assert guess_account(new, self.two_cards()) is None


def part(token: str, *, account: str = "", suggested: str = "", refusal: str = "") -> dict:
    return {
        "token": token,
        "label": token.upper(),
        "account": account,
        "suggested": suggested,
        "refusal": refusal,
    }


class TestPlanningADocumentOfSeveralAccounts:
    def test_Document_WhenEverySectionWasAssignedBefore_IsPlannedToThoseAccounts(self):
        new = kept(10, "All.pdf", sections=[
            part("a", suggested="saver"), part("b", suggested="loan"),
        ])

        plan = plan_sections(new)

        assert plan is not None
        assert plan.to_assign == (("a", "saver"), ("b", "loan"))
        assert plan.complete

    def test_Document_WhenOneSectionIsAlreadyHeld_LeavesItAndPlansTheRest(self):
        new = kept(10, "All.pdf", sections=[
            part("a", account="saver"), part("b", suggested="loan"),
        ])

        plan = plan_sections(new)

        assert plan is not None
        assert plan.to_assign == (("b", "loan"),)
        assert plan.held_accounts == ("saver",)

    def test_Document_WhenASectionHasNeverBeenAssigned_IsNotPlannedAtAll(self):
        new = kept(10, "All.pdf", sections=[part("a", suggested="saver"), part("b")])

        plan = plan_sections(new)

        assert plan is not None
        assert plan.to_assign == ()
        assert not plan.complete

    def test_Document_WhenASectionIsRefused_IsNotPlannedAtAll(self):
        new = kept(10, "All.pdf", sections=[
            part("a", suggested="saver"), part("b", suggested="loan", refusal="does not add up"),
        ])

        plan = plan_sections(new)

        assert plan is not None
        assert plan.to_assign == ()
        assert not plan.complete

    def test_Document_WhenEverySectionIsAlreadyHeld_NeedsNothingAndIsComplete(self):
        new = kept(10, "All.pdf", sections=[part("a", account="saver"), part("b", account="loan")])

        plan = plan_sections(new)

        assert plan is not None
        assert plan.to_assign == () and plan.complete
        assert plan.held_accounts == ("loan", "saver")

    def test_Statement_WhenItHasNoSections_HasNoPlan(self):
        assert plan_sections(kept(10, "One.pdf")) is None
