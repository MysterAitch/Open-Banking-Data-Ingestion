"""Branches that state one merchant name are told apart by what their own descriptions say.

KNOWN ANSWERS, decided before the first run (every name, uid, and town invented). Each uid below
states the merchant "Tesco" and prints its own descriptions; shapes drop the store numbers.

  Three uids whose descriptions differ by town ("TESCO STORES 5223 BIRMINGHAM GB", "TESCO STORES
  17 LEEDS", "TESCO EXPRESS 9 YORK"): labelled "tesco - stores birmingham", "tesco - stores leeds",
  "tesco - express york" (the trailing country code, the payment methods, and the name itself are
  never part of the distinguishing text).

  Two uids whose descriptions are identical ("TESCO STORES LEEDS" on both): nothing tells them
  apart, so "tesco (location 1)" and "tesco (location 2)", the one with more payments first, then
  by id, whatever the order of the rows.

  Two uids where only one has a part of its own: that one is labelled by it and the other, which
  has nothing of its own, falls back to its number.

  Two ACCOUNTS stating one name are two people and keep one label; one uid alone keeps the name.
"""

from __future__ import annotations

from obdi.analysis.entities import Fields, display_names, names_of

A, B, C = "starling:uid-a", "starling:uid-b", "starling:uid-c"


def labels(rows: list[Fields]) -> dict[str, str]:
    return display_names(rows, names_of(rows))


def rows_of(uid: str, description: str, count: int = 3, name: str = "Tesco") -> list[Fields]:
    return [Fields(description, name, "", uid)] * count


class TestBranchesToldApartByTheirDescriptions:
    def world(self) -> list[Fields]:
        return [
            *rows_of(A, "TESCO STORES 5223 BIRMINGHAM GB"),
            *rows_of(B, "TESCO STORES 17 LEEDS"),
            *rows_of(C, "TESCO EXPRESS 9 YORK"),
        ]

    def test_ThreeBranchesDifferingByTown_AreLabelledByTheirOwnPart(self):
        found = labels(self.world())

        assert found == {
            A: "tesco - stores birmingham",
            B: "tesco - stores leeds",
            C: "tesco - express york",
        }

    def test_TheAnswer_DoesNotDependOnTheOrderOfTheRows(self):
        assert labels(self.world()[::-1]) == labels(self.world())

    def test_TheLabelNeverCarriesTheStoreNumberOrTheCountryCode(self):
        for label in labels(self.world()).values():
            assert not any(ch.isdigit() for ch in label)
            assert " gb" not in label


class TestBranchesNothingTellsApart:
    def test_IdenticalDescriptions_FallBackToNumberedLocationsByUsage(self):
        rows = [
            *rows_of(A, "TESCO STORES LEEDS", count=2),
            *rows_of(B, "TESCO STORES LEEDS", count=5),
        ]

        assert labels(rows) == {B: "tesco (location 1)", A: "tesco (location 2)"}

    def test_OneBranchWithAPartOfItsOwn_IsLabelledByItAndTheOtherFallsBack(self):
        rows = [*rows_of(A, "TESCO BIRMINGHAM", count=4), *rows_of(B, "TESCO", count=2)]

        assert labels(rows) == {A: "tesco - birmingham", B: "tesco (location 2)"}

    def test_TwoBranchesWithTheSameTown_BothFallBackRatherThanReadAlike(self):
        rows = [
            *rows_of(A, "TESCO STORES LEEDS", count=4),
            *rows_of(B, "TESCO EXPRESS LEEDS", count=3),
            *rows_of(C, "TESCO EXPRESS LEEDS", count=2),
        ]

        found = labels(rows)

        assert found[A] == "tesco - stores"
        assert found[B] == "tesco (location 2)" and found[C] == "tesco (location 3)"


class TestWhatIsLeftAlone:
    def test_OneIdAlone_KeepsTheStatedName(self):
        assert labels(rows_of(A, "TESCO STORES LEEDS")) == {A: "tesco"}

    def test_TwoAccountsStatingOneName_StayAlike(self):
        rows = [
            Fields("RENT", "Sam Okafor", "20-00-00 11111111"),
            Fields("RENT", "Sam Okafor", "20-00-00 22222222"),
        ]

        assert set(labels(rows).values()) == {"sam okafor"}
