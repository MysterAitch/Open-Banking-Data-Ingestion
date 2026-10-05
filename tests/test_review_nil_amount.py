"""A review flag over rows of nil amount asks a question nothing depends on.

A flag is raised when a row is stored as new although another in the account matches it on
amount and date. A card statement lists lines of 0.00 beside its payments - two of them, under
different descriptions, on each statement date of the card this was found on - and nil equals
nil, so every statement raised a flag. Nine of the eleven flags open on the real store were
these. No balance can answer one, because a nil row moves no balance; and none needs to,
because whether such a pair is one line or two changes no figure anywhere.

The invented card, worked out before the first run. Its file lists, in September 2026:

    03   10.00 out   Brass kettle
    14    0.00       Interest charged          the nil pair: one flag
    14    0.00       Default charges
    20    5.00 out   Brass kettle

and the control card lists the same with 20.00 out in place of each 0.00, which is a pair a
person must still answer: one flag, open, because nothing is stated about its balances.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from obdi import ingest
from obdi.accounts import AccountRecord, AccountRef
from obdi.ingest import import_file
from obdi.rebuild import rebuild_from_raw
from obdi.review_report import SETTLED_CLASSES, FlagClass, assess_flags, review_report
from obdi.review_settlement import SettleReport, settle_review_flags
from obdi.store import Store

D = date
NIL_CARD = "nil-lines"
PAID_CARD = "paid-lines"


def _file(root: Path, ref: str, figure: str) -> Path:
    path = root / f"{ref}.qif"
    path.write_text(
        "!Type:CCard\n"
        "D03/09/2026\nT-10.00\nPBrass kettle\n^\n"
        f"D14/09/2026\nT{figure}\nPInterest charged\n^\n"
        f"D14/09/2026\nT{figure}\nPDefault charges\n^\n"
        "D20/09/2026\nT-5.00\nPBrass kettle\n^\n",
        encoding="utf-8",
    )
    return path


def _land(root: Path, ref: str, figure: str) -> Path:
    db = root / f"{ref}.sqlite3"
    with Store(db) as store:
        store.declare_account(AccountRecord(ref=AccountRef(ref), label=f"Card {ref}"))
        import_file(store, _file(root, ref, figure), account_id=ref)
    return db


@pytest.fixture
def unsettled(monkeypatch):
    """Imports that raise their flags and leave them standing, so the classes can be read.

    The door settles what the evidence answers as each file comes in; switched off here, the
    flag a file raises is still there to assess.
    """
    monkeypatch.setattr(ingest, "settle_review_flags", lambda store: SettleReport())


def classes(db: Path) -> list[FlagClass]:
    with Store(db) as store:
        return sorted(item.flag_class for item in assess_flags(store).values())


class TestWhatTheFlagIsClassedAs:
    def test_Flag_OverTwoNilLinesOnOneDay_IsClassedAsNilAmount(self, tmp_path, unsettled):
        assert classes(_land(tmp_path, NIL_CARD, "0.00")) == [FlagClass.NIL_AMOUNT]

    def test_Flag_OverTwoPaymentsOfTheSameSizeOnOneDay_IsStillOpen(self, tmp_path, unsettled):
        assert classes(_land(tmp_path, PAID_CARD, "-20.00")) == [FlagClass.OPEN]

    def test_NilAmount_IsAClassTheEvidenceAnswers(self):
        assert FlagClass.NIL_AMOUNT in SETTLED_CLASSES


class TestSettlingIt:
    def test_Settlement_OfANilPair_ClosesItAndCountsItUnderItsClass(self, tmp_path, unsettled):
        db = _land(tmp_path, NIL_CARD, "0.00")

        with Store(db) as store:
            report = settle_review_flags(store)
            left = store.review_queue()

        assert report.settled == {FlagClass.NIL_AMOUNT: 1}
        assert report.still_open == 0
        assert left == []

    def test_Settlement_OfAPairOfPayments_LeavesItOpen(self, tmp_path, unsettled):
        db = _land(tmp_path, PAID_CARD, "-20.00")

        with Store(db) as store:
            report = settle_review_flags(store)
            left = store.review_queue()

        assert report.settled == {}
        assert report.still_open == 1
        assert len(left) == 1

    def test_Settlement_KeepsBothNilRows(self, tmp_path, unsettled):
        """Closing the flag is not joining the rows: each line the statement lists stays."""
        db = _land(tmp_path, NIL_CARD, "0.00")

        with Store(db) as store:
            settle_review_flags(store)
            nil_rows = [t for t in store.transactions_for_account(NIL_CARD) if t.amount_minor == 0]

        assert len(nil_rows) == 2


class TestThroughTheDoorsAPersonUses:
    def test_Import_OfAFileWithANilPair_LeavesNoFlagOpen(self, tmp_path):
        with Store(_land(tmp_path, NIL_CARD, "0.00")) as store:
            assert store.review_queue() == []

    def test_Import_OfAFileWithAPairOfPayments_LeavesItsFlagOpen(self, tmp_path):
        with Store(_land(tmp_path, PAID_CARD, "-20.00")) as store:
            assert len(store.review_queue()) == 1

    def test_Rebuild_OfAStoreWithANilPair_DoesNotRaiseItAgain(self, tmp_path):
        db = _land(tmp_path, NIL_CARD, "0.00")

        with Store(db) as store:
            rebuild_from_raw(store)
            assert store.review_queue() == []

    def test_Rebuild_OfAStoreWithAPairOfPayments_StillAsks(self, tmp_path):
        db = _land(tmp_path, PAID_CARD, "-20.00")

        with Store(db) as store:
            rebuild_from_raw(store)
            assert len(store.review_queue()) == 1


class TestTheReport:
    def test_Report_WithANilPairStanding_CountsItAndSaysWhyNothingDependsOnIt(
        self, tmp_path, unsettled
    ):
        db = _land(tmp_path, NIL_CARD, "0.00")

        with Store(db) as store:
            text = review_report(store, today=D(2026, 10, 4)).describe()

        assert "nil-amount: 1 - the rows are of nil amount, so no balance depends on" in text
        assert f"nil-amount / {NIL_CARD}: 1" in text

    def test_Report_WithANilPairStanding_HoldsNoDescription(self, tmp_path, unsettled):
        db = _land(tmp_path, NIL_CARD, "0.00")

        with Store(db) as store:
            text = review_report(store, today=D(2026, 10, 4)).describe().casefold()

        for token in ("interest charged", "default charges", "brass kettle"):
            assert token not in text
