"""An identifier on a page is set as code, by one helper, and never as a hand-built monospace span.

The owner asked for references and source names "as if surrounded by back ticks in markdown". An
identifier is something the system named: an account's reference, a source's name, a file name, a
raw id. Dates, amounts, counts, and masked figures are not, and keep their present treatment.

KNOWN ANSWERS over `named_household`, decided before the first run: every occurrence of one of its
account references or source names in a page's visible text is inside a `<code>` element, except
where a decision recorded in `EXEMPT` says why it cannot be:

  - a HEADING that is only an account's name (an unlabelled account's name is its reference);
  - an `<option>`, `<textarea>`, `<pre>`, or SVG `<title>`, which hold plain text and cannot hold
    markup, the `<pre>` being a report already set in monospace;
  - the refusal that echoes the reference a request carried for an account that is by definition
    not declared ("No account is declared as ...");
  - attributes (`title`, `aria-label`), which are not visible text and cannot hold markup;
  - the options and descriptions of an account's kind, since `cash` is both a kind and a
    reference.

An account whose reference is an ordinary English word (`cash`) is read only where a page sets it
off on its own, as `test_account_names_on_every_page` does.

LINK TEXT is not exempt: a link that reads as an identifier holds a `<code>` inside the `<a>`.
"""

from __future__ import annotations

import re

import pytest

from named_household import ACCOUNTS, SOURCES
from page_dom import HEADINGS, Node, inside, parse, text_nodes
from page_walk import household, household_pages, household_served  # noqa: F401
from source_tree import source_tree

ORDINARY = re.compile(r"[a-z]+")
PLAIN_TEXT_ELEMENTS = ("option", "textarea", "pre", "title")


def identifiers() -> list[str]:
    return [*(a.ref for a in ACCOUNTS), *SOURCES]


def pattern_for(identifier: str) -> re.Pattern[str]:
    return re.compile(rf"(?<![\w:.-]){re.escape(identifier)}(?![\w-]|:\S)")


def exempt(holder: Node) -> bool:
    if inside(holder, *PLAIN_TEXT_ELEMENTS):
        return True
    if any(a.tag == "select" and a.attrs.get("name") == "kind" for a in holder.ancestors()):
        return True
    return any("kinds" in a.classes for a in holder.ancestors())


def unset_as_code(page: str) -> list[tuple[str, str]]:
    """(identifier, the text it stands in) for each occurrence not inside `<code>`."""
    root = parse(page)
    found: list[tuple[str, str]] = []
    for text, holder in text_nodes(root):
        if inside(holder, "code") or exempt(holder):
            continue
        if text.strip().startswith("No account is declared as"):
            continue
        heading = next((n for n in [holder, *holder.ancestors()] if n.tag in HEADINGS), None)
        for identifier in identifiers():
            if not pattern_for(identifier).search(text):
                continue
            if ORDINARY.fullmatch(identifier) and text.strip() != identifier:
                continue
            if heading is not None and heading.text() == identifier:
                continue
            where = ".".join([holder.tag, *sorted(holder.classes)])
            found.append((identifier, f"<{where}> " + re.sub(r"\s+", " ", text.strip())[:70]))
    return found


@pytest.fixture
def pages(household_pages) -> dict[str, str]:  # noqa: F811
    return household_pages


class TestEveryIdentifierOnEveryPageIsCode:
    def test_AccountReferencesAndSourceNames_OnEveryPage_AreInsideCodeElements(self, pages):
        """Counted once for each page's route, however many addresses reach it."""
        offenders: dict[tuple[str, str], set[str]] = {}
        for url, page in pages.items():
            for identifier, text in unset_as_code(page):
                offenders.setdefault((url.split("?")[0], identifier), set()).add(text)

        assert not offenders, f"{len(offenders)} (page, identifier) pairs:\n" + "\n".join(
            f"{route} {identifier}: {sorted(texts)[:2]}"
            for (route, identifier), texts in sorted(offenders.items())
        )


class TestTheDetectorBites:
    """Planted offenders with known answers, so a pass is known to mean something."""

    def test_Detector_WhenAReferenceIsPlainText_NamesIt(self):
        found = unset_as_code("<p>Fed by monzo-csv, for pocket-money.</p>")

        assert sorted(identifier for identifier, _ in found) == ["monzo-csv", "pocket-money"]

    def test_Detector_WhenAReferenceIsInCode_FindsNothing(self):
        assert unset_as_code("<p>Fed by <code>monzo-csv</code></p>") == []

    def test_Detector_WhenLinkTextIsAPlainReference_NamesIt(self):
        found = unset_as_code('<a href="/ledger?ref=pocket-money">pocket-money</a>')

        assert [identifier for identifier, _ in found] == ["pocket-money"]

    def test_Detector_WhenLinkTextHoldsCode_FindsNothing(self):
        assert unset_as_code('<a href="/x"><code>pocket-money</code></a>') == []

    def test_Detector_WhenHeadingIsOnlyAnUnlabelledAccountsName_FindsNothing(self):
        assert unset_as_code("<h1>plain-ref-7</h1>") == []

    def test_Detector_WhenHeadingMentionsTheReferenceInASentence_NamesIt(self):
        found = unset_as_code("<h2>Fed from plain-ref-7 today</h2>")

        assert [identifier for identifier, _ in found] == ["plain-ref-7"]

    def test_Detector_WhenAnOrdinaryWordIsInASentence_IsNotAnAccount(self):
        assert unset_as_code("<p>Cash withdrawals, cash machines</p>") == []

    def test_Detector_WhenAnOrdinaryWordStandsAloneAsPlainText_NamesIt(self):
        assert [i for i, _ in unset_as_code("<li><a>cash</a></li>")] == ["cash"]

    def test_Detector_InsideAnOption_IsExemptBecauseAnOptionCannotHoldMarkup(self):
        assert unset_as_code("<select><option>pocket-money</option></select>") == []


#: A span set in the monospace class around something the system named is a hand-built `<code>`,
#: in a form that has drifted from the helper. The `mono` class is kept for dates, figures, and
#: times, which are not identifiers.
HAND_BUILT = re.compile(
    r"""<span class=["'][^"']*\bmono\b[^"']*["']>\{\s*(?:html\.)?_?esc(?:ape)?\(\s*"""
    r"""(?:str\(\s*)?[\w.]*(?:ref|source|account|space|main|registry|provider|code|ident)\b"""
    r"""|<span class=["'][^"']*\bmono\b[^"']*["']>\{(?:ref|source|name)\}""",
    re.IGNORECASE,
)


def hand_built_in(sources: dict[str, str]) -> list[str]:
    return sorted(name for name, text in sources.items() if HAND_BUILT.search(text))


class TestNoHandBuiltMonospaceAroundAnIdentifier:
    def test_Source_OfEveryPageModule_HoldsNoMonospaceSpanAroundAnIdentifier(self):
        assert hand_built_in(source_tree()) == []

    @pytest.mark.parametrize(
        "offender",
        [
            """f'<span class="mono">{html.escape(account.ref)}</span>'""",
            """f'<span class="mono muted">{_esc(view.ref)}</span>'""",
            """f'<span class="mono">{_esc(gap.source)}</span>'""",
            """f'<span class="mono">{html.escape(c.space)}</span>'""",
            """f'<span class="mono">{ref}</span>'""",
        ],
    )
    def test_Guard_OnAPlantedOffender_NamesIt(self, offender):
        assert hand_built_in({"web_new.py": offender, "fine.py": "x = 1"}) == ["web_new.py"]

    @pytest.mark.parametrize(
        "fine",
        [
            """f'<span class="mono nowrap">{_esc(line.day)}</span>'""",
            """f'<span class="mono">{_esc(span_words(a, b))}</span>'""",
            """f'<span class="t-fig mono nowrap fig">{figure}</span>'""",
        ],
    )
    def test_Guard_OnADateOrFigure_NamesNothing(self, fine):
        assert hand_built_in({"web_new.py": fine}) == []
