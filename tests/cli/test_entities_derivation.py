"""The Entities page says where each name came from and by what steps, and what a rule matches.

KNOWN ANSWERS, decided before the first run.

  - A planted transaction printed "UBER *ONE MEMBERSHIP UBER.COM/BILL ENG" has the name "uber one
    membership uber com bill eng". Its name's fold begins with "From the description:", the
    printed text, an arrow, and that name; the one step that changed it is the lower-casing and
    punctuation step, and no other sentence is listed.
  - "UBER TRIP 8841 ON 12 APR" has the name "uber trip": the lower-casing step, the digit step,
    and the printed-date step all changed it, listed in the order they are applied.
  - "CAFÉ NERO 12" loses its accent first, so the accent step is listed.
  - The steps the page states are exactly the steps the code applies, in order, and `shape_of` of
    any text is those steps applied in turn.
  - The source of a name is a field of its record: a record that says "bank's merchant name" is
    shown as "From the bank's merchant name", and the page of today says "description" because
    that is what the record says.
  - A rule reads as what it does and names the field it reads. The comparison lists on the page
    are the code's own constants.
  - The masked page holds the steps and the lists, and no printed text.
"""

from __future__ import annotations

from datetime import date
from functools import reduce

import pytest

from obdi.analysis.entities import (
    BEGINS,
    CONTAINS,
    DESCRIPTION_SOURCE,
    SHAPE_STEPS,
    Covered,
    Derivation,
    Entity,
    count_shapes,
    derivation_of,
    rule_phrase,
    shape_of,
    view_of,
)
from obdi.analysis.entity_tokens import IGNORED_TRAILING
from obdi.analysis.payment_methods import METHODS
from obdi.ingest.identity import NORMALISATION_STEPS
from obdi.pages.web_entities import derivation_html, render_entities
from page_dom import elements, parse

UBER = "UBER *ONE MEMBERSHIP UBER.COM/BILL ENG"
TRIP = "UBER TRIP 8841 ON 12 APR"
CAFE = "CAFÉ NERO 12"


def _view(*printed: str):
    counts = count_shapes(printed)
    covers = {
        shape_of(text): (
            Covered(date(2026, 9, 10), "current-main", "", -999, "GBP", text, "0123456789ab"),
        )
        for text in printed
    }
    # The names sit under an entity, where each is listed with the fold of its transactions.
    held = Entity(1, "Held", None, tuple(sorted(counts)))
    return view_of(counts, [held], covers)


def _fold_for(page: str, name: str):
    for fold in elements(parse(page), "details"):
        if "ent-rows" in fold.classes:
            derive = [d for d in elements(fold, "div") if "ent-derive" in d.classes]
            if derive and name in derive[0].text():
                return derive[0]
    raise AssertionError(f"no fold derives {name}")


def _steps_listed(node) -> list[str]:
    return [
        li.text()
        for ol in elements(node, "ol")
        if "ent-steps" in ol.classes
        for li in elements(ol, "li")
    ]


class TestEachNameShowsItsDerivation:
    def test_Fold_ForAPlantedRow_BeginsWithTheFieldThePrintedTextAndTheName(self):
        page = render_entities(_view(UBER), unmasked=True).decode("utf-8")

        derive = _fold_for(page, "uber one membership")
        assert derive.text().startswith("From the description:")
        assert f"{UBER} → uber one membership uber com bill eng" in derive.text()

    def test_Fold_ForAPlantedRow_ListsOnlyTheStepsThatChangedIt(self):
        page = render_entities(_view(UBER), unmasked=True).decode("utf-8")

        steps = _steps_listed(_fold_for(page, "uber one membership"))

        assert steps == ["letters are lower-cased and punctuation becomes spaces"]

    def test_Fold_ForARowWithADigitAndADate_ListsTheStepsInTheOrderTheyApply(self):
        page = render_entities(_view(TRIP), unmasked=True).decode("utf-8")

        steps = _steps_listed(_fold_for(page, "uber trip"))

        applied = [sentence for sentence, _step in SHAPE_STEPS]
        assert steps == [applied[2], applied[3], applied[4]]

    def test_Fold_ForARowWithAnAccent_ListsTheAccentStepFirst(self):
        page = render_entities(_view(CAFE), unmasked=True).decode("utf-8")

        steps = _steps_listed(_fold_for(page, "cafe nero"))

        assert steps[0] == SHAPE_STEPS[0][0]

    def test_Fold_WhenSeveralPrintedTextsMakeOneName_ShowsThreeAndCountsTheRest(self):
        texts = [f"UBER TRIP {n} {'X' * n}" for n in range(1, 6)]
        covers = {
            "uber trip": tuple(
                Covered(date(2026, 9, 10 - i), "current-main", "", -1, "GBP", text, f"{i:012d}")
                for i, text in enumerate(texts)
            )
        }

        record = derivation_of("uber trip", covers["uber trip"])

        assert record.printed == tuple(texts[:3])
        assert record.more == 2
        assert "and 2 more" in derivation_html(record)


class TestTheSourceIsARecordNotAPhrase:
    def test_Derivation_ForTodaysNames_SaysTheDescriptionBecauseTheRecordDoes(self):
        view = _view(UBER)
        (covered,) = view.covers[shape_of(UBER)]

        record = derivation_of(shape_of(UBER), (covered,))
        page = render_entities(view, unmasked=True).decode("utf-8")

        assert record.source == DESCRIPTION_SOURCE
        assert f"From the {record.source}:" in page

    def test_Derivation_WhenARecordNamesAnotherField_IsShownAsThatField(self):
        record = Derivation(
            source="bank's merchant name",
            printed=("Uber Eats",),
            more=0,
            steps=(),
            name="uber eats",
        )

        html = derivation_html(record)

        assert "merchant name:" in html and "From the bank" in html
        assert "description" not in html

    def test_Rule_WhenTheSourceIsAnotherField_NamesThatField(self):
        phrase = rule_phrase(BEGINS, "uber eats", source="merchant name")

        assert "whose merchant name" in phrase and "description" not in phrase


class TestTheStepsStatedAreTheStepsApplied:
    SAMPLES = (UBER, TRIP, CAFE, "M&S BANK0806249308", "DIRECT DEBIT TESCO 12 ON 3 MAR")

    @pytest.mark.parametrize("text", SAMPLES)
    def test_ShapeOf_IsExactlyTheListedStepsAppliedInTurn(self, text):
        assert shape_of(text) == reduce(lambda acc, step: step[1](acc), SHAPE_STEPS, text)

    def test_Steps_AreTheIdentityNormalisationFollowedByTheShapesOwn(self):
        assert SHAPE_STEPS[: len(NORMALISATION_STEPS)] == NORMALISATION_STEPS
        assert len(SHAPE_STEPS) == len(NORMALISATION_STEPS) + 2

    @pytest.mark.parametrize("unmasked", [True, False])
    def test_Page_StatesEveryStepOnce_InOrder(self, unmasked):
        page = render_entities(_view(UBER, TRIP), unmasked=unmasked).decode("utf-8")

        (fold,) = [d for d in elements(parse(page), "details") if "ent-method" in d.classes]
        listed = [
            li.text()
            for ol in elements(fold, "ol")
            if "ent-steps" in ol.classes
            for li in elements(ol, "li")
        ]
        assert listed == [sentence for sentence, _step in SHAPE_STEPS]
        assert len(listed) == len(SHAPE_STEPS)


class TestHowNamesAreCompared:
    def test_Page_ShowsTheCodeOwnListsOfMethodsAndIgnoredCodes(self):
        page = render_entities(_view(UBER), unmasked=False).decode("utf-8")

        (fold,) = [d for d in elements(parse(page), "details") if "ent-method" in d.classes]
        text = fold.text()
        for method in METHODS:
            for phrase in method.printed:
                assert f"“{phrase}”" in text
        for code in IGNORED_TRAILING:
            assert f"“{code}”" in text
        assert "Initials match initials only" in text

    def test_Page_WhenMasked_HoldsNoPrintedText(self):
        page = render_entities(_view(UBER, TRIP, CAFE), unmasked=False).decode("utf-8")

        for printed in ("UBER", "uber", "NERO", "nero", "8841"):
            assert printed not in page.replace("uber.com", "")


class TestARuleReadsAsWhatItDoes:
    def test_Rule_Begins_NamesTheNameAndItsMaking(self):
        assert rule_phrase(BEGINS, "uber eats") == (
            "any transaction whose name, made as above, begins with “uber eats”"
        )

    def test_Rule_Contains_NamesTheNameAndSaysAnyOrder(self):
        assert rule_phrase(CONTAINS, "uber eats") == (
            "any transaction whose name, made as above, holds the words “uber eats” in any order"
        )
