"""The equivalence harness holds what it claims, before any slice relies on it.

KNOWN ANSWERS, decided before the first run. Pages differing only in a build name, an instant,
or an age compare equal; pages differing in a figure, a word, or a date do not. Over the large
store, each of Today, the main account, and a card renders to the same body on two loads: a harness
that was not deterministic would report every later slice as a difference.
"""

from __future__ import annotations

import pytest

from large_store_corpus import LargeStore, cached_large_store
from large_store_pages import serving
from page_equivalence import PAGES, normalised, rendered


def page(footer: str = "obdi 0.4.1", line: str = "<p>Held 2026-10-01 (today).</p>") -> str:
    return f"<main>{line}<p>Balance 12.34 at Tesco</p></main><footer>{footer}</footer>"


class TestNormalising:
    def test_Footer_NamingAnotherBuild_ComparesEqual(self):
        assert normalised(page(footer="obdi 0.4.1")) == normalised(page(footer="obdi 0.9.9 abc"))

    @pytest.mark.parametrize(
        ("first", "second"),
        [
            ("seen 2026-10-04 15:24", "seen 2026-10-05 09:01"),
            ("Last scheduled cycle finished 09:15", "Last scheduled cycle finished 21:40"),
            ("Held 2026-01-01 (98 days ago)", "Held 2026-01-01 (99 days ago)"),
            ("Held 2026-01-01 (today)", "Held 2026-01-01 (yesterday)"),
            ("Held 2026-01-01 (3 weeks ago)", "Held 2026-01-01 (4 weeks ago)"),
            ("Held 2026-01-01 (over a year ago)", "Held 2026-01-01 (over 2 years ago)"),
            ("asked just now", "asked 5 minutes ago"),
        ],
    )
    def test_InstantOrAge_ThatMovesWithTheClock_ComparesEqual(self, first, second):
        assert normalised(page(line=f"<p>{first}</p>")) == normalised(page(line=f"<p>{second}</p>"))

    @pytest.mark.parametrize(
        ("first", "second"),
        [
            ("Held 2026-10-01", "Held 2026-10-02"),
            ("Balance 12.34", "Balance 12.35"),
            ("adds up", "does not add up"),
            ("Held 2026-01-01 (98 days ago)", "Held 2026-01-02 (98 days ago)"),
        ],
    )
    def test_FigureWordOrDate_ThatDiffers_ComparesUnequal(self, first, second):
        assert normalised(page(line=f"<p>{first}</p>")) != normalised(page(line=f"<p>{second}</p>"))


@pytest.fixture(scope="module")
def large() -> LargeStore:
    return cached_large_store(faithful=True)


@pytest.fixture(scope="module")
def pages(large, tmp_path_factory):
    with serving(large, tmp_path_factory.mktemp("equivalence")) as served:
        yield served


class TestEachPageIsDeterministic:
    @pytest.mark.parametrize("name", sorted(PAGES))
    def test_PageRenderedTwiceOverTheSameStore_HasTheSameNormalisedBody(self, pages, name):
        first = rendered(pages, PAGES[name])
        second = rendered(pages, PAGES[name])

        assert first == second
        assert len(first) > 1000
