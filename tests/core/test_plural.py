"""Counts meet nouns and verbs in one helper, so a page never says "(s)" or "1 rows"."""

from __future__ import annotations

import pytest

from obdi.core.plural import IRREGULARS, agree, plural, word


class TestCountWithNoun:
    @pytest.mark.parametrize(
        ("count", "expected"),
        [(0, "0 rows"), (1, "1 row"), (2, "2 rows"), (1204, "1,204 rows")],
    )
    def test_Plural_ForRegularNoun_AgreesAndSeparatesThousands(self, count, expected):
        assert plural(count, "row") == expected

    def test_Plural_ForIrregularNoun_UsesTheNamedPlural(self):
        assert plural(1, "match", "matches") == "1 match"
        assert plural(3, "match", "matches") == "3 matches"

    def test_Word_WhenCountPrintedElsewhere_GivesTheNounAlone(self):
        assert word(1, "pair") == "pair"
        assert word(0, "pair") == "pairs"


class TestVerbsAndDeterminers:
    @pytest.mark.parametrize("form", sorted(IRREGULARS))
    def test_Agree_ForEveryListedForm_DiffersBetweenOneAndMany(self, form):
        assert agree(1, form) == IRREGULARS[form][0]
        assert agree(2, form) == IRREGULARS[form][1]
        assert agree(0, form) == IRREGULARS[form][1]

    def test_Agree_ForTheFormsThePagesNeed_ReadsAsEnglish(self):
        assert f"1 check {agree(1, 'differs')}" == "1 check differs"
        assert f"2 checks {agree(2, 'differs')}" == "2 checks differ"
        assert agree(1, "this") == "this"
        assert agree(5, "this") == "these"

    def test_Agree_ForAnUnlistedForm_FailsLoudly(self):
        with pytest.raises(KeyError):
            agree(1, "wibbles")
