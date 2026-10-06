"""What is still to fetch, once a statement is tested by what it lists (`agreement`, R1).

A statement that adds up by its own listing tests the days it lists, so nothing is asked of the
owner for those days: not an earlier statement, not a second balance, not a balance near a
flagged transaction. Each scenario has its opposite beside it: the same account where the
statement does not add up, or where another source holds days the statement does not reach.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

from fetch_gaps_world import Household, feed, load_household, santander_statements
from obdi.accounts import AccountRecord, AccountRef
from obdi.fetch_gaps import GapKind, fetch_report, gather_evidence
from obdi.standing_data import standings_for
from obdi.store import Store

D = date
TODAY = D(2026, 10, 5)


def _report(db: Path):
    with Store(db) as store:
        refs = [
            str(row[0])
            for row in store.connection.execute("SELECT DISTINCT account_id FROM transactions")
        ]
        standings = standings_for(store, refs, families=None, movement=None)
        return fetch_report(gather_evidence(store), standings, TODAY)


def _kinds(report, ref: str) -> set[GapKind]:
    return {g.kind for o in report.accounts if o.account == ref for g in o.gaps}


def _declare(store: Store, ref: str) -> None:
    store.declare_account(AccountRecord(ref=AccountRef(ref), label=ref))


def test_FirstStatement_WhenItAddsUpByWhatItLists_RaisesNoEarlierStatementGapForItsOwnDays(
    tmp_path,
):
    db = tmp_path / "store.sqlite3"
    house = Household()
    with Store(db) as store:
        _declare(store, "listed-first")
        santander_statements(
            store,
            tmp_path,
            "listed-first",
            [D(2026, 9, 10)],
            house,
            rows_for={D(2026, 9, 10): [(D(2026, 8, 20), "Listed Zeppelin A", 1500)]},
        )
    assert GapKind.NOTHING_BEFORE not in _kinds(_report(db), "listed-first")


def test_FirstStatement_WhenAnotherSourceHoldsEarlierDays_StillAsksForAnEarlierStatement(
    tmp_path,
):
    db = tmp_path / "store.sqlite3"
    house = Household()
    with Store(db) as store:
        _declare(store, "listed-late")
        feed(
            store,
            "listed-late",
            [(D(2026, 6, 1), -1900, "Early Feed Zeppelin")],
            digest="late",
        )
        santander_statements(
            store,
            tmp_path,
            "listed-late",
            [D(2026, 9, 10)],
            house,
            rows_for={D(2026, 9, 10): [(D(2026, 8, 20), "Listed Zeppelin B", 1500)]},
        )
    report = _report(db)
    (gap,) = [
        g
        for o in report.accounts
        if o.account == "listed-late"
        for g in o.gaps
        if g.kind is GapKind.NOTHING_BEFORE
    ]
    assert gap.first_day == D(2026, 6, 1)
    assert gap.last_day < D(2026, 8, 20)


def test_FlaggedPair_WhenTheOneStatementListingBothAddsUp_RaisesNoBalanceNeededGap(tmp_path):
    db = tmp_path / "store.sqlite3"
    house = Household()
    pair = [(D(2026, 9, 5), "Pair Zeppelin", 2000), (D(2026, 9, 5), "Pair Zeppelin", 2000)]
    with Store(db) as store:
        _declare(store, "listed-pair")
        santander_statements(
            store, tmp_path, "listed-pair", [D(2026, 9, 10)], house, rows_for={D(2026, 9, 10): pair}
        )
        assert store.review_queue(), "the world must hold a flag for the test to mean anything"
    report = _report(db)
    assert _kinds(report, "listed-pair") == set()


def test_FlaggedPair_WhenTheOneStatementDoesNotAddUp_StillAsksForABalanceNearTheFlag(tmp_path):
    db = tmp_path / "store.sqlite3"
    house = Household()
    pair = [(D(2026, 9, 5), "Pair Zeppelin", 2000), (D(2026, 9, 5), "Pair Zeppelin", 2000)]
    with Store(db) as store:
        _declare(store, "unlisted-pair")
        santander_statements(
            store,
            tmp_path,
            "unlisted-pair",
            [D(2026, 9, 10)],
            house,
            rows_for={D(2026, 9, 10): pair},
        )
        # A feed row the statement does not list: it cannot add up to what the account holds.
        feed(store, "unlisted-pair", [(D(2026, 9, 7), -900, "Unlisted Zeppelin")], digest="u")
        assert store.review_queue()
    assert GapKind.FLAG_SETTLE in _kinds(_report(db), "unlisted-pair")


def test_Account_WhenItsOneStatementAddsUp_RaisesNoOneBalanceGap(tmp_path):
    world = load_household(tmp_path)
    assert GapKind.ONE_BALANCE not in {g.kind for g in world.gaps("card-single")}


def test_Account_WhenItsOneStatementCannotSay_StillAsksForAnotherBalance(tmp_path):
    """The statement lists nothing the account holds, so it adds up to nothing: one balance, and
    rows (from the feed) it does not list."""
    db = tmp_path / "store.sqlite3"
    house = Household()
    with Store(db) as store:
        _declare(store, "listed-silent")
        santander_statements(
            store, tmp_path, "listed-silent", [D(2026, 9, 10)], house, rows_for={D(2026, 9, 10): []}
        )
        feed(
            store,
            "listed-silent",
            [(D(2026, 9, 2), -1900, "Unlisted Feed Zeppelin")],
            digest="silent",
        )
    kinds = _kinds(_report(db), "listed-silent")
    assert kinds & {GapKind.ONE_BALANCE, GapKind.NOTHING_BEFORE, GapKind.NEWER_STATEMENT}
