"""Today's account list over a household whose declared accounts hold no transactions.

Built through the store and `build_overview`, so what is asserted is what the page does with the
accounts the application assembles, not with accounts written by hand. Each kind of declared
account that holds nothing has its sentence: one tracked by balances stated by hand (a mortgage)
says so, and one with no such kind says nothing is held yet. The page is parsed with a DOM.
"""

from __future__ import annotations

from datetime import UTC, datetime

from obdi.ingest.accounts import BALANCE_ONLY_KIND, AccountRecord, AccountRef
from obdi.ingest.rebuild_hold import RebuildHold
from obdi.ingest.store import Store
from obdi.overview import build_overview
from obdi.verify.balance_anchors import record_stated_anchor
from obdi.web_overview import overview_html
from page_dom import Node, elements, parse

NOW = datetime(2026, 10, 5, 8, 12, tzinfo=UTC)


def _household(path, *, rebuilding: RebuildHold | None = None):
    with Store(path) as store:
        store.declare_account(
            AccountRecord(ref=AccountRef("mortgage"), label="Mortgage", kind=BALANCE_ONLY_KIND)
        )
        store.declare_account(AccountRecord(ref=AccountRef("pot"), label="Holiday pot"))
        record_stated_anchor(store, "mortgage", "2026-09-01", "1000.00", today=NOW.date())
        return build_overview(
            store,
            now=NOW,
            findings=lambda: [],
            canonical_for_ref=lambda ref: ref,
            watched=set(),
            actual_bound=None,
            rebuild_status={},
            rebuilding=rebuilding,
        )


def _rows(root: Node) -> dict[str, str]:
    return {
        str(a.attrs["href"]).removeprefix("/ledger?ref="): " ".join(
            e.text() for e in elements(a, "span") if "a-trust" in e.classes
        )
        for a in elements(root, "a")
        if "arow" in a.classes
    }


def test_Today_WhenADeclaredAccountHoldsNoTransactions_ListsEachKindWithItsOwnSentence(tmp_path):
    overview = _household(tmp_path / "store.sqlite3")

    rows = _rows(parse(overview_html(lambda fresh: overview, now=NOW)))

    assert rows == {
        "mortgage": "Its balance is stated by hand.",
        "pot": "Nothing held yet.",
    }


def test_Today_WhenARebuildRuns_ADeclaredAccountHoldingNothingStillSaysWhichKindItIs(tmp_path):
    """A rebuild marks every account that is not archived as paused, but an account that holds
    no transactions has nothing the rebuild could be pausing the checks of."""
    overview = _household(tmp_path / "store.sqlite3", rebuilding=RebuildHold("2026-10-05 08:00"))

    rows = _rows(parse(overview_html(lambda fresh: overview, now=NOW)))

    assert rows == {
        "mortgage": "Its balance is stated by hand.",
        "pot": "Nothing held yet.",
    }
