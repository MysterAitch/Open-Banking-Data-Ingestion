"""An account is named by the name he gave it, on every page, and by nothing else.

The owner opened a card he had named and found its page headed by its bare reference, while the
Accounts page showed his name. Seven hooks in `cli.py` asked for the provider's label alone, so a
label he declared was ignored on those pages. Fixing the one page would have left the class.

KNOWN ANSWERS over `named_household`, decided before the first run: on no page is a heading
the bare reference of an account that has a label, and wherever a labelled account's reference
stands in a page's text its label stands beside it, in the same block of the page.

An account whose reference is an ordinary English word (`cash`) is read only where the page sets
it off on its own, since "cash withdrawals" is a sentence and not an account.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from named_household import LABELLED, Named
from page_dom import HEADINGS, Node, elements, parse, text_nodes
from page_walk import household, household_pages, household_served  # noqa: F401

SOURCE = Path(__file__).resolve().parent.parent / "src" / "obdi"

#: Blocks that hold one thing a reader takes in at once; the label must stand in the same one.
BLOCKS = frozenset(
    {"p", "li", "tr", "div", "td", "th", "dd", "dt", "summary", "section", "article", "details",
     "label", "option", "figcaption", "main", "body", "h1", "h2", "h3", "h4", "h5", "h6"}
)
ORDINARY = re.compile(r"[a-z]+")


def reference_pattern(ref: str) -> re.Pattern[str]:
    return re.compile(rf"(?<![\w:.-]){re.escape(ref)}(?![\w-]|:\S)")


def block_of(node: Node) -> Node:
    """The nearest block around `node`, and the one above it where it holds only the reference."""
    chain = [node, *node.ancestors()]
    blocks = [n for n in chain if n.tag in BLOCKS]
    return blocks[0]


def stands_without_name(account: Named, page: str) -> list[str]:
    """Where the page shows the account's reference with no label in the same block."""
    root = parse(page)
    pattern = reference_pattern(account.ref)
    order = {id(node): n for n, node in enumerate(root.descendants())}
    headings = [node for node in root.descendants() if node.tag in HEADINGS]
    found: list[str] = []
    for text, holder in text_nodes(root):
        if not pattern.search(text):
            continue
        if ORDINARY.fullmatch(account.ref) and text.strip() != account.ref:
            continue
        # DECIDED: the options of a kind (`cash` is both a kind and a reference), and the refusal
        # that echoes the reference a request carried for an account that is by definition not
        # declared, are not mentions of the account.
        if any(
            (a.tag == "select" and a.attrs.get("name") == "kind") or "kinds" in a.classes
            for a in holder.ancestors()
        ):
            continue
        if text.strip().startswith("No account is declared as"):
            continue
        block = block_of(holder)
        # A block that holds only the reference (a table cell, a list item of references) is
        # read together with the block that holds it.
        while block.text() == account.ref and block.parent is not None:
            above = [n for n in block.ancestors() if n.tag in BLOCKS]
            if not above:
                break
            block = above[0]
        if account.label in block.text():
            continue
        # Under a heading that names the account, as a page about one account sets its
        # reference beneath its name.
        here = order.get(id(holder), -1)
        preceding = [h for h in headings if order[id(h)] < here]
        if preceding and account.label in preceding[-1].text():
            continue
        found.append(re.sub(r"\s+", " ", block.text())[:90])
    return found


def headed_by_bare_reference(account: Named, page: str) -> list[str]:
    return [
        heading.text()
        for heading in elements(parse(page), *HEADINGS)
        if heading.text() == account.ref
    ]


@pytest.fixture
def pages(household_pages) -> dict[str, str]:  # noqa: F811
    return household_pages


class TestEveryPageNamesAnAccountByTheNameHeGaveIt:
    def test_Headings_OnEveryPage_AreNeverTheBareReferenceOfALabelledAccount(self, pages):
        offenders = {
            f"{url} -> {account.ref}": found
            for url, page in pages.items()
            for account in LABELLED
            if (found := headed_by_bare_reference(account, page))
        }

        assert not offenders, "\n" + "\n".join(f"{k}: {v}" for k, v in offenders.items())

    def test_References_OnEveryPage_StandBesideTheNameOfALabelledAccount(self, pages):
        """Counted once for each page's route and account, however many addresses reach it."""
        offenders: dict[tuple[str, str], set[str]] = {}
        for url, page in pages.items():
            for account in LABELLED:
                for block in stands_without_name(account, page):
                    offenders.setdefault((url.split("?")[0], account.ref), set()).add(block)

        assert not offenders, f"{len(offenders)} (page, account) pairs:\n" + "\n".join(
            f"{route} {ref}: {sorted(blocks)[:2]}"
            for (route, ref), blocks in sorted(offenders.items())
        )

    def test_AccountPage_OfADeclaredAccount_IsHeadedByTheDeclaredLabelAndNotTheProviders(
        self, pages
    ):
        page = pages["/ledger?ref=starling:uid-main&month=2026-09"]
        (heading,) = elements(parse(page), "h1")

        assert heading.text() == "Joint current"

    def test_Names_OfAnAccountWithAProviderLabelOnly_IsTheProvidersLabel(self, pages):
        page = pages["/ledger?ref=starling:uid-pots&month=2026-09"]
        (heading,) = elements(parse(page), "h1")

        assert heading.text() == "Bills pot (starling)"


class TestTheDetectorsBite:
    """Planted offenders with known answers, so a pass is known to mean something."""

    ACCOUNT = Named("pocket-money", "Kids' pocket money")

    def test_Detector_WhenHeadingIsTheBareReference_Names1(self):
        page = "<h1>pocket-money</h1><h2>Kids' pocket money</h2>"

        assert headed_by_bare_reference(self.ACCOUNT, page) == ["pocket-money"]

    def test_Detector_WhenReferenceStandsAloneInAParagraph_NamesIt(self):
        page = "<p>pocket-money has nothing to fetch.</p><p>Kids' pocket money elsewhere</p>"

        assert len(stands_without_name(self.ACCOUNT, page)) == 1

    def test_Detector_WhenNameIsBesideTheReference_FindsNothing(self):
        page = "<li><strong>Kids' pocket money</strong> <code>pocket-money</code></li>"

        assert stands_without_name(self.ACCOUNT, page) == []

    def test_Detector_WhenReferenceIsBeneathAHeadingThatNamesTheAccount_FindsNothing(self):
        page = "<h1>Kids' pocket money</h1><p><code>pocket-money</code></p>"

        assert stands_without_name(self.ACCOUNT, page) == []

    def test_Detector_WhenReferenceIsBeneathAHeadingOfAnotherAccount_NamesIt(self):
        page = "<h1>Biscuit tin</h1><p><code>pocket-money</code></p>"

        assert len(stands_without_name(self.ACCOUNT, page)) == 1

    def test_Detector_WhenOrdinaryWordIsInASentence_IsNotAnAccount(self):
        wallet = Named("cash", "Wallet")

        assert stands_without_name(wallet, "<p>The cash withdrawals page</p>") == []
        assert len(stands_without_name(wallet, "<p><code>cash</code></p><p>x</p>")) == 1


MODULE_OWNING_NAMES = "account_names.py"
#: Modules that handle provider labels as raw input, never for a page: the scan that reads them
#: out of the store, and the Actual envelope, which names accounts inside Actual. Not covered by
#: this guard, and not shown to be right by it either.
NOT_PAGE_NAMING = frozenset({"labels.py", "actual_push.py"})
#: Deciding what an account is called is that one module's work, and a page is handed the result
#: (`AccountsShown`). What the old way looked like, each form having been met in the source:
#: asking the provider-label hook, a rendering function taking a bare reference-to-label mapping
#: as a parameter, looking an account up in such a mapping by reference, and the helpers that
#: took one.
OFFENDING = (
    re.compile(r"\bdisplay_labels\b"),
    re.compile(r"\b(?:labels|names)\s*:\s*(?:Mapping|dict)\[str, str\]\s*(?:,|\)|=\s*None)"),
    re.compile(r"\b(?:labels|names|merged|named)\.get\(\s*(?:ref|row\.account_id|account)\b"),
    re.compile(r"\b(?:merged_names|name_html|name_text|picker_labels)\b"),
)


def modules_that_decide_a_name(sources: dict[str, str]) -> list[str]:
    return sorted(
        name
        for name, text in sources.items()
        if name not in {MODULE_OWNING_NAMES, *NOT_PAGE_NAMING}
        and any(p.search(text) for p in OFFENDING)
    )


class TestNoModuleOtherThanTheCentralOneDecidesAName:
    def test_Guard_OverTheSource_FindsNoOtherModuleDecidingAnAccountsName(self):
        sources = {p.name: p.read_text(encoding="utf-8") for p in SOURCE.glob("*.py")}

        assert modules_that_decide_a_name(sources) == []

    @pytest.mark.parametrize(
        "offender",
        [
            "label = display_labels().get(ref, '')\n",
            "def render(report, names: Mapping[str, str]) -> str:\n",
            "def render(labels: dict[str, str]) -> str:\n",
            "title = labels.get(ref, ref)\n",
            "from .account_names import name_html\n",
        ],
    )
    def test_Guard_OnAPlantedOffender_NamesIt(self, offender):
        planted = {
            "web_new_page.py": offender,
            "account_names.py": offender,
            "fine.py": "x = 1\n",
        }

        assert modules_that_decide_a_name(planted) == ["web_new_page.py"]

    def test_Guard_OnAPageThatIsHandedTheResult_NamesNothing(self):
        planted = {
            "web_new_page.py": "def render(names: AccountsShown) -> str:\n"
            "    return names.of(ref).inline()\n"
        }

        assert modules_that_decide_a_name(planted) == []
