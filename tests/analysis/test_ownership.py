"""The arithmetic and the words of a shared account, worked out by hand before the first run.

A share of an odd amount rounds to the nearest minor unit with halves away from zero, so a
negative balance rounds as its positive twin does. Each co-owner's half of an odd amount is
therefore rounded up on its own page, and the two halves can exceed the whole by a penny: the
rounding is stated here and not hidden, and only the owner's share is ever counted.
"""

from __future__ import annotations

import pytest

from obdi.analysis.ownership import owners_sentence, scaled, share_of, share_words, your_share_of
from obdi.ingest.ownership_records import OwnerShare


@pytest.mark.parametrize(
    ("minor", "percent", "expected"),
    [
        (100000, 50, 50000),
        (100000, 100, 100000),
        (100001, 50, 50001),
        (-100001, 50, -50001),
        (1, 50, 1),
        (0, 40, 0),
        (999, 25, 250),
        (12000, 40, 4800),
    ],
)
def test_Scaled_WhenTakingAShareOfAnAmount_RoundsHalvesAwayFromZeroForEitherSign(
    minor, percent, expected
):
    assert scaled(minor, percent) == expected


@pytest.mark.parametrize(
    ("percent", "said"),
    [
        (50, "your half of the balance"),
        (25, "your quarter of the balance"),
        (75, "your three quarters of the balance"),
        (40, "your 40% of the balance"),
    ],
)
def test_YourShareOf_WhenSaidAsAPhrase_ReadsAsEnglish(percent, said):
    assert your_share_of(percent, "the balance") == said
    assert share_words(percent) in said


def test_ShareOf_WhenNothingIsDeclared_IsTheWholeAccount():
    assert share_of({}, "current-main") == 100


def test_ShareOf_WhenAnAccountIsDeclaredAndAnotherIsNot_ReadsOnlyTheDeclaredOne():
    owners = {
        "joint": (OwnerShare(1, "Me", 50, True), OwnerShare(2, "Casey Wintermute", 50, False))
    }

    assert share_of(owners, "joint") == 50
    assert share_of(owners, "other") == 100


def test_OwnersSentence_WhenNobodyIsDeclared_SaysYouSolely():
    assert owners_sentence(()) == "You, solely"


def test_OwnersSentence_WhenSharedAndHidden_PutsYouFirstAndMasksTheOtherName():
    owners = (OwnerShare(2, "Casey Wintermute", 60, False), OwnerShare(1, "Me", 40, True))

    shown = owners_sentence(owners)
    hidden = owners_sentence(owners, hide_names=True)

    assert shown == "You (40%) and Casey Wintermute (60%)"
    assert hidden.startswith("You (40%) and ")
    assert "Casey" not in hidden
    assert "(60%)" in hidden
