"""No sentence on a page shouts: emphasis is plain words or the page's own `<strong>`.

A review of the interface's words found MERGED, MASKED, NOT, FIRST, ONLY, END, EMPTY, and UNMASKED
in capitals inside sentences, a house style of shouting that reads as alarm.

KNOWN ANSWER: apart from the acronyms and file formats a reader knows, no word of three or more
capital letters appears in the visible text of any page the dispatcher can render over the
invented store.
"""

from __future__ import annotations

import re

import pytest

from page_walk import invented, served, walked_pages  # noqa: F401

#: Words that are written in capitals everywhere: formats, currencies, protocols, and bank terms.
ACRONYMS = frozenset(
    {
        *("GBP", "UTC", "BST", "CSV", "PDF", "JSON", "API", "URL", "URI", "HTTP", "HTTPS"),
        *("ISO", "OCR", "QIF", "DEBIT", "CREDIT", "FASTER", "CARD", "OUT", "SETTLED", "PENDING"),
        # A status the bank states, named as data where a count of items is given by status.
        "DECLINED",
        # Words a bank or an aggregator states in a coded field, named as data where the cash
        # withdrawal measurement counts them (`cash_withdrawal_measure`).
        *("ATM", "CASH", "PURCHASE", "GENERAL"),
    }
)
SHOUTING = re.compile(r"\b[A-Z]{3,}\b")


def text_of(page: str) -> str:
    page = re.sub(r"<(style|script).*?</\1>", " ", page, flags=re.S)
    page = re.sub(r"<[^>]*>", " ", page)
    return re.sub(r"https?://\S+", " ", page)


@pytest.fixture
def pages(request) -> dict[str, str]:
    return request.getfixturevalue("walked_pages")


#: Pages that quote what a provider or a bank wrote, whose own capitals are evidence.
QUOTING_ROUTES = ("/artefact", "/statement-shape", "/account")


class TestNoPageShouts:
    def test_Pages_WhenWalked_NoSentenceCarriesAShoutedWord(self, pages):
        found: dict[str, list[str]] = {}
        for url, page in pages.items():
            if url.split("?")[0] in QUOTING_ROUTES:
                continue
            words = sorted(
                {
                    w
                    for w in SHOUTING.findall(text_of(page))
                    if w not in ACRONYMS and set(w) != {"X"}  # X-runs are masked text
                }
            )
            if words:
                found[url] = words

        assert not found, "\n" + "\n".join(f"{url}: {words}" for url, words in found.items())

    def test_ShoutingPattern_ForEmphasisInCapitals_IsCaught(self):
        assert SHOUTING.findall("Showing the MASKED rendering, NOT the UNMASKED one") == [
            "MASKED",
            "NOT",
            "UNMASKED",
        ]

    def test_ShoutingPattern_ForPlainWords_IsNotCaught(self):
        assert SHOUTING.findall("Showing the masked rendering in GBP") == ["GBP"]
