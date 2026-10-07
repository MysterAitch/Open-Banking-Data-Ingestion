"""Each transaction on the ledger shows the balance the account held after it.

KNOWN ANSWERS, decided before the first run, for an invented account RUNNING whose balance is
stated as 100.00 at the end of 2026-02-28 (10,000 pence):

    03-02  -12.50  one row              -> 87.50 after it
    03-05  -20.00  and +50.00 the same day -> the day ends on 117.50; the row listed first on the
                                           day carries 117.50 and the one beneath it is what
                                           the first row's movement undoes
    03-09  +100.00                      -> 217.50
    03-12  -9.87, PENDING               -> 207.63: a person who states a balance means the account
                                           as they see it, pending included. Against a bank's or
                                           a statement's booked balance a pending row is not
                                           counted, so it shows no figure and moves none (the
                                           last test, over `balances_after` with such an anchor)

A masked page holds only the sealed figure. An account no balance anchors shows no figure and says
why in "How this was checked".
"""

from __future__ import annotations

from datetime import date

import pytest

from obdi.core.masking import MASKED_TOTAL
from obdi.core.models import TransactionStatus
from obdi.ingest.store import Store
from obdi.ledger import build_ledger
from obdi.row_balances import balances_after
from obdi.verify.balance_anchors import BANK, Anchor, record_stated_anchor
from obdi.web_ledger import render_ledger
from page_dom import elements, parse
from test_ledger import land, txn

RUNNING = "running-account"
UNANCHORED = "unanchored-account"


@pytest.fixture
def store(tmp_path):
    with Store(tmp_path / "running.sqlite3") as opened:
        land(
            opened,
            "running-1",
            txn(RUNNING, "feed", "r1", date(2026, 3, 2), -1250, "COFFEE"),
            txn(RUNNING, "feed", "r2", date(2026, 3, 5), -2000, "RENT SHARE"),
            txn(RUNNING, "feed", "r3", date(2026, 3, 5), 5000, "REFUND"),
            txn(RUNNING, "feed", "r4", date(2026, 3, 9), 10000, "SALARY"),
            txn(
                RUNNING, "feed", "r5", date(2026, 3, 12), -987, "PARKING",
                status=TransactionStatus.PENDING,
            ),
        )
        record_stated_anchor(opened, RUNNING, "2026-02-28", "100.00", today=date(2026, 4, 1))
        land(
            opened,
            "unanchored-1",
            txn(UNANCHORED, "feed", "u1", date(2026, 3, 2), -1250, "COFFEE"),
        )
        yield opened


def page(store: Store, ref: str, *, unmasked: bool):
    ledger = build_ledger(store, ref, "2026-03", bound=False, label="")
    return parse(render_ledger(ledger, unmasked=unmasked).decode())


def rows_by_description(root) -> dict[str, str | None]:
    """Each listed row's description (as shown) -> its running figure, or None where it has none."""
    found: dict[str, str | None] = {}
    for item in elements(root, "li"):
        if "txn" not in item.classes:
            continue
        description = next(e for e in item.descendants() if "t-desc" in e.classes).text()
        figures = [e.text() for e in item.descendants() if "t-bal" in e.classes]
        found[description] = figures[0] if figures else None
    return found


def listed_order(root) -> list[str]:
    return [
        next(e for e in item.descendants() if "t-desc" in e.classes).text()
        for item in elements(root, "li")
        if "txn" in item.classes
    ]


class TestRunningBalanceShown:
    def test_RunningBalance_WhenValuesShown_EachRowCarriesTheBalanceAfterIt(self, store):
        root = page(store, RUNNING, unmasked=True)

        figures = rows_by_description(root)
        assert figures["COFFEE"] == "£87.50"
        assert figures["SALARY"] == "£217.50"

    def test_RunningBalance_WhenTwoRowsShareADay_TheFirstListedCarriesTheDaysEndAndTheNextUndoesIt(
        self, store
    ):
        root = page(store, RUNNING, unmasked=True)

        figures = rows_by_description(root)
        order = [name for name in listed_order(root) if name in ("RENT SHARE", "REFUND")]
        first, second = order
        movement = {"RENT SHARE": 2000, "REFUND": -5000}
        # The day ends on 117.50 whichever way round the two are listed; the one beneath is the
        # day's end less what the one above moved.
        assert figures[first] == "£117.50"
        undone = 11750 - (-movement[first])
        assert figures[second] == f"£{undone // 100}.{undone % 100:02d}"

    def test_RunningBalance_WhenAPersonStatedTheBalanceAndARowIsPending_ThePendingRowIsCounted(
        self, store
    ):
        root = page(store, RUNNING, unmasked=True)

        assert rows_by_description(root)["PARKING"] == "£207.63"

    def test_RunningBalance_WhenABankStatedTheBalanceAndARowIsPending_ItHasNoFigureAndMovesNone(
        self,
    ):
        def row(source_id: str, day: date, amount: int, status=TransactionStatus.BOOKED):
            return txn(RUNNING, "feed", source_id, day, amount, source_id, status=status)

        pending = row("p", date(2026, 3, 12), -987, TransactionStatus.PENDING)
        booked = row("b", date(2026, 3, 9), 10000)
        earlier = row("e", date(2026, 3, 2), -1250)
        stated = Anchor(date(2026, 2, 28), 10000, BANK, "feed")

        figures = balances_after([pending, booked, earlier], [pending, booked, earlier], stated)

        # Newest first, as the ledger lists them: the pending row has none and the salary day
        # ends on 100.00 - 12.50 + 100.00.
        assert figures == [None, 18750, 8750]

    def test_RunningBalance_WhenValuesAreMasked_OnlyTheSealedFigureIsShown(self, store):
        root = page(store, RUNNING, unmasked=False)

        figures = rows_by_description(root)
        counted = [figure for figure in figures.values() if figure is not None]
        assert counted and set(counted) == {MASKED_TOTAL}
        text = root.text()
        for secret in ("87.50", "117.50", "217.50", "100.00"):
            assert secret not in text

    def test_RunningBalance_WhenValuesShown_IsQuieterThanTheAmount(self, store):
        root = page(store, RUNNING, unmasked=True)

        item = next(
            li for li in elements(root, "li") if "txn" in li.classes and "SALARY" in li.text()
        )
        figure = next(e for e in item.descendants() if "t-bal" in e.classes)
        assert {"mono", "muted"} <= figure.classes


class TestRunningBalanceAbsent:
    def test_RunningBalance_WhenNoKnownBalanceAnchorsTheAccount_ThereIsNoColumn(self, store):
        root = page(store, UNANCHORED, unmasked=True)

        assert rows_by_description(root) == {"COFFEE": None}

    def test_RunningBalance_WhenNoKnownBalanceAnchorsTheAccount_HowCheckedSaysWhy(self, store):
        root = page(store, UNANCHORED, unmasked=False)

        assert "No balance is shown after each transaction" in root.text()
        assert "has no known balance to count from" in root.text()

    def test_RunningBalance_WhenAnAnchorExists_HowCheckedSaysNothingAboutItsAbsence(self, store):
        root = page(store, RUNNING, unmasked=False)

        assert "No balance is shown after each transaction" not in root.text()
