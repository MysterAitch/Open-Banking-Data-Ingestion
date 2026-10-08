"""The Entities page offers a group the payments themselves tie, first, with its reason.

KNOWN ANSWERS, decided before the first run (invented names). One party is paid 3 times with both
its account and the bank's id, 2 times with the id alone, and 2 times with the account alone, and
every payment states "Lena Ford"; two grocers' shapes ("FERNHOLLOW GROCERS LEEDS", "... YORK") are
a text group beside it. The unmasked page shows the evidence group BEFORE the text group, names it
"Lena Ford" (never the account's key or the id), ticks both members, and says "7 payments carry two
of the other party's account number, the bank's own id for the party, and the stated name". The
masked page seals the name but still says how many payments and which kinds of field, which are
counts and not values.
"""

from __future__ import annotations

from obdi.analysis.entities import Fields, display_names, name_origins, names_of, view_of
from obdi.analysis.entity_ties import row_ties
from obdi.pages.web_entities import render_entities
from page_dom import elements, parse

ACCOUNT_NUMBER, PARTY_ID = "20-00-00 11111111", "starling:uid-lena"
SENTENCE = (
    "7 payments carry two of the other party's account number, the bank's own id for the "
    "party, and the stated name"
)


def page(*, unmasked: bool) -> str:
    rows = (
        [Fields("RENT", "Lena Ford", ACCOUNT_NUMBER, PARTY_ID)] * 3
        + [Fields("RENT", "Lena Ford", "", PARTY_ID)] * 2
        + [Fields("RENT", "Lena Ford", ACCOUNT_NUMBER)] * 2
        + [Fields("FERNHOLLOW GROCERS LEEDS"), Fields("FERNHOLLOW GROCERS YORK")]
    )
    named = names_of(rows)
    origins = name_origins(named)
    counts = {name: origin.rows for name, origin in origins.items()}
    view = view_of(
        counts, [], None, origins, None, display_names(rows, named), row_ties(rows, named)
    )
    return render_entities(view, unmasked=unmasked).decode("utf-8")


class TestTheEvidenceGroupLeads:
    def test_Page_WithValues_ShowsTheEvidenceGroupBeforeTheTextGroupAndSaysWhy(self):
        html = page(unmasked=True)

        root = parse(html)
        names = [
            box.attrs.get("value")
            for box in elements(root, "input")
            if box.attrs.get("name") == "name"
        ]
        assert names[:2] == ["Lena Ford", "Fernhollow Grocers"]
        assert SENTENCE in root.text()

    def test_Page_WithValues_TicksBothMembersAndPrintsNeitherTheNumberNorTheId(self):
        html = page(unmasked=True)

        root = parse(html)
        boxes = [b for b in elements(root, "input") if b.attrs.get("name") == "shape"]
        assert len(boxes[:2]) == 2
        assert all("checked" in b.attrs for b in boxes[:2])
        printed = root.text()
        assert "11111111" not in printed and PARTY_ID not in printed
        ticks = [label.text() for label in elements(root, "label") if "tick" in label.classes]
        assert ticks[:2] == ["lena ford", "lena ford"], "both are shown by the name they state"

    def test_Page_Masked_SealsTheNameButStillCountsThePaymentsAndNamesTheKinds(self):
        html = page(unmasked=False)

        assert "Lena Ford" not in html
        assert SENTENCE in parse(html).text()
