"""The review flags page says which known balance would settle a flag it could not settle.

The household and every answer are in `flag_balance_world` and `test_review_balances`: of the
seventeen flags, five are answered by the balances and twelve are cards. Of those twelve, six
are open because a known balance is missing, and each says which one, in dates alone:

    opens-on-day, first, empty    "No known balance before 2026-09-14: a statement covering it
                                  would settle this."
    last                          "No known balance after 2026-09-14 yet: the next statement
                                  will settle this."
    apart-early                   the same, naming 2026-09-15, the later of its two rows
    single                        "Only one known balance (2026-09-20): an earlier statement
                                  would settle this."

The other six say nothing of the kind: the balances were read and disagree, or no source lists
both rows, or the account's balances are followed rather than tested, so a statement would not
settle them and a line saying so would mislead.
"""

from __future__ import annotations

import re
import shutil

import httpx
import pytest

from flag_balance_world import (
    AGGREGATED,
    PAYEE,
    TYPED_ONLY,
    build_balance_world,
    label_of,
)
from obdi.cli import build_web_config
from obdi.review_flags import VERDICT_SETTLE, build_queue
from obdi.store import Store
from section_harness import environment, serve_config

BEFORE = "No known balance before 2026-09-14: a statement covering it would settle this."
AFTER = "No known balance after 2026-09-14 yet: the next statement will settle this."
AFTER_LATER_ROW = "No known balance after 2026-09-15 yet: the next statement will settle this."
SINGLE = "Only one known balance (2026-09-20): an earlier statement would settle this."

SAYS = {
    "opens-on-day": BEFORE,
    "first": BEFORE,
    "empty": BEFORE,
    "last": AFTER,
    "apart-early": AFTER_LATER_ROW,
    "single": SINGLE,
}
SAYS_NOTHING = ["unreproduced", "middle-unmet", "triple-short", AGGREGATED, TYPED_ONLY]

CARDS = 12


@pytest.fixture(scope="module")
def standing(tmp_path_factory):
    return build_balance_world(tmp_path_factory.mktemp("lines"), rebuild=False)


def lines_of(store: Store, account: str) -> list[str]:
    return [
        item.sentence
        for card in build_queue(store, label_of).cards
        if card.account == label_of(account)
        for neighbour in card.neighbours
        for item in neighbour.says
        if item.verdict == VERDICT_SETTLE
    ]


class TestTheCards:
    @pytest.mark.parametrize("account", sorted(SAYS))
    def test_Card_WhenAKnownBalanceIsMissing_SaysWhichOneWouldSettleIt(self, standing, account):
        with Store(standing) as store:
            assert lines_of(store, account) == [SAYS[account]]

    @pytest.mark.parametrize("account", SAYS_NOTHING)
    def test_Card_WhenNoStatementWouldHelp_SaysNothingOfWhatWouldSettleIt(
        self, standing, account
    ):
        with Store(standing) as store:
            assert lines_of(store, account) == []

    def test_Card_WhenFlagsAreAnsweredByTheBalances_IsNotListed(self, standing):
        with Store(standing) as store:
            queue = build_queue(store, label_of)

        listed = {card.account for card in queue.cards}
        for account in ("between", "closes-on-day", "apart", "triple"):
            assert label_of(account) not in listed
        assert len(queue.cards) == CARDS
        assert queue.settled == 5

    def test_Card_WhenAFlagHasTwoNeighbours_SaysItOnceForTheWholeSetAndNamesNoFigure(
        self, standing
    ):
        with Store(standing) as store:
            (card, _) = [c for c in build_queue(store, label_of).cards
                         if c.account == label_of("triple-short")]
            lines = [i.sentence for n in card.neighbours for i in n.says]

        assert not any("20.00" in line or "55.00" in line for line in lines)


@pytest.fixture
def served(standing, tmp_path, monkeypatch):
    copy = tmp_path / "store.sqlite3"
    shutil.copy(standing, copy)
    environment(monkeypatch, tmp_path)
    config = build_web_config(copy)
    assert config is not None
    base, stop = serve_config(config)
    yield base, copy
    stop()


def page(base: str) -> str:
    return httpx.get(f"{base}/review-flags", timeout=30).text


class TestThePage:
    def test_Page_WhenFetched_ListsTheTwelveCardsAndCountsTheFiveAnswered(self, served):
        base, _ = served

        text = page(base)

        assert text.count('class="flag-card"') == CARDS
        assert "5 other flags were already answered by the evidence and are not listed" in text

    def test_Page_WhenFetched_SaysWhichKnownBalanceIsMissingOnEachCardThatLacksOne(self, served):
        base, _ = served

        text = re.sub(r"<[^>]+>", " ", page(base))
        text = re.sub(r"\s+", " ", text)

        assert text.count(BEFORE) == 3
        assert text.count(AFTER) == 1
        assert text.count(AFTER_LATER_ROW) == 1
        assert text.count(SINGLE) == 1
        assert text.count("What is missing.") == 6

    def test_Page_WhenFetched_HoldsNoPayeeAndNoFigure(self, served):
        base, _ = served

        text = page(base).casefold()

        for token in (PAYEE.casefold(), "20.00", "100.00", "55.00", "60.00"):
            assert token not in text

    def test_Page_WhenFetched_UsesTheOneWordForEachThing(self, served):
        base, _ = served

        prose = re.sub(r"<style>.*?</style>", "", page(base), flags=re.S)
        text = re.sub(r"<[^>]+>", " ", prose).casefold()

        for word in ("anchor", "uid", "tier", "the matcher"):
            assert word not in text, word
        assert "(s)" not in text

    def test_Page_AfterTheRebuildTheFlagsAreSettledBy_ListsOnlyTheTwelveAndCountsNone(
        self, served
    ):
        from obdi.rebuild import rebuild_from_raw

        base, db = served
        with Store(db) as store:
            rebuild_from_raw(store)

        text = page(base)

        assert text.count('class="flag-card"') == CARDS
        assert "other flag" not in text
