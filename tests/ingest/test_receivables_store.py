"""What the store keeps and refuses where money owed back on a transaction is concerned.

KNOWN ANSWERS, decided before the first run: a receivable is declared on a transaction by an
entity, with the expected day 28 days after the expense unless given; a second declaration on one
open transaction is refused; closing by hand needs a way and a reason, both kept, and cannot be
done twice; the declarations survive the rebuild from raw. Every name is invented.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from landing import rebuild_from_raw
from obdi.ingest.receivable_records import (
    DEFAULT_EXPECTED_DAYS,
    RECEIVED_ELSEWHERE,
    WRITTEN_OFF,
    ReceivableRefused,
)
from obdi.ingest.store import Store

DAY = date(2026, 10, 3)


@pytest.fixture
def store(tmp_path):
    with Store(tmp_path / "store.sqlite3") as opened:
        yield opened


def declare(store: Store, **kw) -> int:
    debtor = store.entity_named("Brindlewick Volunteers") or store.create_empty_entity(
        "Brindlewick Volunteers"
    )
    fields = {
        "account": "current-main", "row_ref": "row-1", "day": DAY, "debtor": debtor,
        "amount_minor": 420, "label": "volunteering",
    }  # fmt: skip
    fields.update(kw)
    return store.declare_receivable(**fields)


class TestDeclaring:
    def test_Declare_WhenNoExpectedDayIsGiven_ExpectsItTwentyEightDaysAfterTheExpense(self, store):
        declare(store)

        (found,) = store.receivables()

        assert found.expected_day == DAY + timedelta(days=DEFAULT_EXPECTED_DAYS)
        assert (found.amount_minor, found.label, found.closed_how) == (420, "volunteering", "")

    def test_Declare_WhenAnExpectedDayIsGiven_KeepsIt(self, store):
        declare(store, expected_day=date(2026, 11, 30))

        assert store.receivables()[0].expected_day == date(2026, 11, 30)

    def test_Receivables_WhenAskedForOneAccount_ListsOnlyThatAccountsAndOldestFirst(self, store):
        declare(store, row_ref="a", day=date(2026, 10, 9))
        declare(store, row_ref="b", day=date(2026, 10, 2))
        declare(store, row_ref="c", account="other-account")

        mine = store.receivables(account="current-main")

        assert [r.row_ref for r in mine] == ["b", "a"]
        assert len(store.receivables()) == 3

    @pytest.mark.parametrize(
        "bad",
        [
            {"amount_minor": 0},
            {"amount_minor": -1},
            {"debtor": 9999},
            {"row_ref": ""},
            {"account": ""},
            {"label": "x" * 121},
            {"expected_day": DAY - timedelta(days=1)},
        ],
    )
    def test_Declare_WhenTheDeclarationIsNotSound_IsRefusedAndWritesNothing(self, store, bad):
        with pytest.raises(ReceivableRefused):
            declare(store, **bad)

        assert store.receivables() == []

    def test_Declare_WhenTheTransactionIsAlreadyOwedOnAndOpen_IsRefused(self, store):
        declare(store)

        with pytest.raises(ReceivableRefused):
            declare(store)

        assert len(store.receivables()) == 1

    def test_Declare_WhenTheEarlierWasClosedByHand_ADeclarationCanBeMadeAgain(self, store):
        first = declare(store)
        store.close_receivable(first, WRITTEN_OFF, "forgiven")

        declare(store)

        assert len(store.receivables()) == 2


class TestClosingByHand:
    def test_Close_WhenGivenAWayAndAReason_KeepsBoth(self, store):
        made = declare(store)

        store.close_receivable(made, RECEIVED_ELSEWHERE, "  paid in   cash ")

        (found,) = store.receivables()
        assert (found.closed_how, found.closed_reason) == (RECEIVED_ELSEWHERE, "paid in cash")
        assert found.closed_at

    @pytest.mark.parametrize(("how", "reason"), [("lost", "x"), (WRITTEN_OFF, "  "),
                                                 (WRITTEN_OFF, "x" * 121)])
    def test_Close_WhenTheWayOrReasonIsNotSound_IsRefusedAndTheReceivableStaysOpen(
        self, store, how, reason
    ):
        made = declare(store)

        with pytest.raises(ReceivableRefused):
            store.close_receivable(made, how, reason)

        assert store.receivables()[0].closed_how == ""

    def test_Close_WhenAlreadyClosedOrNotThere_IsRefused(self, store):
        made = declare(store)
        store.close_receivable(made, WRITTEN_OFF, "forgiven")

        with pytest.raises(ReceivableRefused):
            store.close_receivable(made, RECEIVED_ELSEWHERE, "again")
        with pytest.raises(ReceivableRefused):
            store.close_receivable(4242, WRITTEN_OFF, "no such thing")


class TestFindingARowByItsAnchor:
    def _selects_of(self, store, key, wanted):
        issued: list[str] = []
        store.connection.set_trace_callback(issued.append)
        found = store.transaction_by_key(key, wanted)
        store.connection.set_trace_callback(None)
        return found, [s for s in issued if s.lstrip().upper().startswith("SELECT")]

    def test_TransactionByKey_WhenTheKeyNamesARow_FindsThatRowInOneSelect(self, store, tmp_path):
        from landing import import_file
        from this_month_world import write_export

        write_export(
            tmp_path / "a.csv",
            [(date(2026, 10, 3), "Corner Coffee", "-4.20"), (date(2026, 10, 4), "Bakery", "-3.20")],
        )
        import_file(store, tmp_path / "a.csv", account_id="current-main")
        rows = {t.description: t for t in store.all_transactions()}
        wanted = "k-" + rows["Corner Coffee"].entity_id

        found, selects = self._selects_of(store, lambda ident: "k-" + ident, wanted)

        assert found is not None and found.description == "Corner Coffee"
        assert len(selects) == 1

    def test_TransactionByKey_WhenTheKeyNamesNothing_IsNoneInOneSelect(self, store):
        found, selects = self._selects_of(store, lambda ident: ident, "nothing-has-this-id")

        assert found is None
        assert len(selects) == 1


class TestKeptAcrossARebuild:
    def test_Receivables_WhenTheStoreIsRebuiltFromRaw_AreKept(self, store):
        declare(store)

        rebuild_from_raw(store)

        assert len(store.receivables()) == 1

    def test_Irreplaceable_WhenAReceivableIsDeclared_CountsItSoAWipeCannotLoseItUnnoticed(
        self, store
    ):
        key = "amounts owed to you on a transaction"
        before = store.irreplaceable()[key]

        declare(store)

        assert store.irreplaceable()[key] == before + 1
