"""The learned rules section is read on a phone: the zero-row rules cost one line each, closed.

KNOWN ANSWERS, decided before the first run (every name invented). A view with 3 rules that link
rows and N that link none is rendered at N = 0 and at N = 120. Outside the one closed fold that
holds the zero-row rules the two pages are IDENTICAL (so the section does not grow with them);
inside it there is one list item per rule; the method and the bound's caveat are said once on the
page, never per rule; and the form's labels are sentences.
"""

from __future__ import annotations

import re

from obdi.analysis.entities import view_of
from obdi.analysis.learned_rules import APPLIED, OFFERED, Rule, RuleView
from obdi.pages.web_entities import render_entities
from page_dom import elements, parse


def reaching(count: int) -> list[RuleView]:
    return [
        RuleView(
            Rule(f"starling:uid-r{n}", ("shop", f"branch{n}"), 14, 2310 + n),
            APPLIED if n % 2 == 0 else OFFERED,
            (f"shop branch{n} london",),
            3,
        )
        for n in range(count)
    ]


def silent(count: int) -> list[RuleView]:
    return [
        RuleView(Rule(f"starling:uid-s{n}", ("cafe", f"corner{n}"), 2, 300 + n), APPLIED)
        for n in range(count)
    ]


def page(silent_rules: int, *, unmasked: bool = True) -> str:
    view = view_of({"x": 1}, [], rules=(*reaching(3), *silent(silent_rules)), rules_shared=1)
    return render_entities(view, unmasked=unmasked).decode()


def outside_the_fold(html: str) -> str:
    return re.sub(
        r'<details class="ent-more ent-zero-rules">.*?</details>', "<FOLD/>", html, flags=re.S
    )


def without_counts(html: str) -> str:
    return re.sub(r'<p class="ent-why">At these settings.*?</p>', "", html, flags=re.S)


class TestTheSectionDoesNotGrowWithRulesThatReachNoRow:
    def test_OutsideTheFold_ThePageIsTheSameWith120SilentRulesAsWithNone(self):
        grown = outside_the_fold(page(120))

        assert grown.count("<FOLD/>") == 1
        assert without_counts(grown.replace("<FOLD/>", "")) == without_counts(page(0)), (
            "only the counts line, which says how many rules apply, may differ"
        )

    def test_Fold_IsOneClosedDetailsWithOneLineForEachRule(self):
        folds = [
            node
            for node in elements(parse(page(120)), "details")
            if "ent-zero-rules" in node.classes
        ]

        assert len(folds) == 1 and "open" not in folds[0].attrs
        items = [n for n in elements(folds[0], "li")]
        assert len(items) == 120
        assert "120 rules learned that reach no row" in folds[0].text()

    def test_NoFold_WhenEveryRuleReachesARow(self):
        assert "reach no row" not in page(0)

    def test_RulesThatReachRows_AreOneLineEachWithTheirDetailInAClosedDrillDown(self):
        items = [
            n for n in elements(parse(page(0)), "li") if "ent-learned-rule" in n.classes
        ]

        assert len(items) == 3
        for item in items:
            drill = [d for d in elements(item, "details")]
            assert len(drill) == 1 and "open" not in drill[0].attrs
            summary = [s for s in elements(drill[0], "summary")][0].text()
            assert re.search(r"- taught by 14, tested against [\d,]+, (would link|links) 3", summary)


class TestTheMethodIsSaidOnce:
    def test_BoundAndCaveat_AppearOnceOnThePageWhateverTheNumberOfRules(self):
        text = " ".join(parse(page(120)).text().split())

        assert text.count("which they need not be") == 1
        assert text.count("fewer than 1 in M/3") == 1

    def test_FormLabels_AreSentences(self):
        text = " ".join(parse(page(0)).text().split())

        assert "A rule is learned once" in text and "of a party's rows share an opening" in text
        assert "A rule is applied by default once it has been tested against" in text
        assert "Rows to learn from" not in text and "Other rows to test against" not in text

    def test_MaskedPage_NamesNoPartyAndNoOpening(self):
        html = page(120, unmasked=False)
        text = parse(html).text()

        assert "starling:uid" not in html and "shop" not in text.casefold().replace("shops", "")
        assert "a party - taught by 14" in text
