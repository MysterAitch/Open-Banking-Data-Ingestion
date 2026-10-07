"""What "what to fetch next" says once statements' days come from `statement_span`.

The households here are invented and built through the real doors (`statement_span_world`), with
every receipt time planted and `today` passed in.

THE REAL-SHAPED HOLE ("shaped"): a Santander card whose statements close 2026-01-12, 02-11, 04-10
and 05-12, the April one opening on a balance that is not February's closing balance. Hand
working: intervals 30, 58, 32 days, lower median 32, so the cadence is 32 and a hole is over 48
days; 02-11 to 04-10 is 58 days. The balances that do not meet PROVE something lies between
them (stated); the closing days 58 days apart agree. The hole starts the day after the earlier
close, 02-12 (a fact). The missing statement is expected to have closed one month after 02-11,
03-11, and the April statement prints no start, so it is taken to cover from 03-12, which is
before its first row (04-03): the hole is 02-12 to 03-11, one statement probably missing, its end
inferred. Both pages are to show that one pair of dates.

THE SPACES household ("sp-main" with Spaces): the bank issues no statement for a Space, so a
Space is never asked for one whatever it holds. Four Spaces of `sp-main`: `sp-feed` (the bank's
feed only, no balance: would be AUTOMATIC_ONLY), `sp-qif` (an export and no balance: would be
NO_BALANCE), `sp-one` (one stated balance: would be ONE_BALANCE) and `sp-old` (archived). The
main account holds feed rows and no balance, so it is AUTOMATIC_ONLY. `sp-orphan` is declared
with the Space kind but no parent: nothing says whose Space it is, so it is not recognised as one
and keeps its gap. Gaps: sp-main's and sp-orphan's, two; the three live Spaces are listed as
needing nothing, with the main account named; the archived one is not listed, as no archived
account is.
"""

from __future__ import annotations

from datetime import date

import pytest

from obdi.ingest.accounts import AccountRecord, AccountRef
from obdi.ingest.pipeline import import_file
from obdi.ingest.spaces import SPACE_KIND
from obdi.ingest.statement_terms import keep_statement_readings
from obdi.ingest.store import Store
from obdi.pages.web_bring_in import BringInData, render_bring_in
from obdi.read.account_names import AccountsShown
from obdi.read.fetch_gaps import Basis, GapKind, fetch_report, gather_evidence
from obdi.read.fetch_reasons import gap_lines as _lines
from obdi.verify.balance_anchors import record_stated_anchor
from obdi.verify.standing_data import standings_for
from obdi.verify.statement_span import HoleReason
from page_dom import elements, parse
from statement_span_world import Spend, feed, statement

D = date
TODAY = D(2026, 6, 1)


def _report(store: Store, **evidence_arguments):
    keep_statement_readings(store)
    store.connection.commit()
    refs = [
        str(row[0])
        for row in store.connection.execute("SELECT DISTINCT account_id FROM transactions")
    ]
    standings = standings_for(store, refs, families=None, movement=None)
    return fetch_report(gather_evidence(store, **evidence_arguments), standings, TODAY)


def _shaped(store: Store, root, ref: str = "shaped") -> None:
    first = statement(
        store,
        root,
        ref,
        D(2026, 1, 12),
        10000,
        [Spend(D(2026, 1, 6), f"{ref} A", 1000)],
        received=D(2026, 1, 13),
    )
    second = statement(
        store,
        root,
        ref,
        D(2026, 2, 11),
        first,
        [Spend(D(2026, 2, 5), f"{ref} B", -1000)],
        received=D(2026, 2, 12),
    )
    third = statement(
        store,
        root,
        ref,
        D(2026, 4, 10),
        second + 500,
        [Spend(D(2026, 4, 3), f"{ref} C", 1000)],
        received=D(2026, 4, 11),
    )
    statement(
        store,
        root,
        ref,
        D(2026, 5, 12),
        third,
        [Spend(D(2026, 5, 6), f"{ref} D", 1000)],
        received=D(2026, 5, 13),
    )


class TestARealShapedHole:
    def test_BalancesThatDoNotMeet_AreAStatedHole_WithItsEndInferredFromTheCadence(self, tmp_path):
        with Store(tmp_path / "s.sqlite3") as store:
            _shaped(store, tmp_path)
            report = _report(store)

        (gap,) = [g for g in report.gaps if g.kind is GapKind.HOLE_BETWEEN]
        assert (gap.first_day, gap.last_day) == (D(2026, 2, 12), D(2026, 3, 11))
        assert gap.basis is Basis.STATED
        assert gap.reason is HoleReason.BALANCES_DIFFER
        assert (gap.probably, gap.closings) == (1, (D(2026, 3, 11),))
        assert gap.last_day_inferred is True
        assert (gap.earlier_closing, gap.later_closing) == (D(2026, 2, 11), D(2026, 4, 10))

    def test_TheSentence_SaysBothPiecesOfEvidenceAndWhichDayIsInferred(self, tmp_path):
        with Store(tmp_path / "s.sqlite3") as store:
            _shaped(store, tmp_path)
            (gap,) = [g for g in _report(store).gaps if g.kind is GapKind.HOLE_BETWEEN]

        dates, action, why = _lines(gap)

        assert dates == "2026-02-12 to 2026-03-11"
        assert action == "Fetch the statement closing between 2026-02-11 and 2026-04-10."
        assert "opens on a balance that is not the one" in why
        assert "close 58 days apart" in why
        assert "Probably 1 statement is missing here, closing about 2026-03-11" in why
        assert "Where the gap ends is inferred" in why

    def test_TheSameStatementsWithTheBalancesMeeting_AreOnlyAnInferredHole(self, tmp_path):
        with Store(tmp_path / "s.sqlite3") as store:
            first = statement(
                store,
                tmp_path,
                "met",
                D(2026, 1, 12),
                10000,
                [Spend(D(2026, 1, 6), "met A", 1000)],
                received=D(2026, 1, 13),
            )
            second = statement(
                store,
                tmp_path,
                "met",
                D(2026, 2, 11),
                first,
                [Spend(D(2026, 2, 5), "met B", 1000)],
                received=D(2026, 2, 12),
            )
            third = statement(
                store,
                tmp_path,
                "met",
                D(2026, 4, 10),
                second,
                [Spend(D(2026, 4, 3), "met C", 1000)],
                received=D(2026, 4, 11),
            )
            statement(
                store,
                tmp_path,
                "met",
                D(2026, 5, 12),
                third,
                [Spend(D(2026, 5, 6), "met D", 1000)],
                received=D(2026, 5, 13),
            )
            (gap,) = [g for g in _report(store).gaps if g.kind is GapKind.HOLE_BETWEEN]

        assert gap.basis is Basis.INFERRED
        assert gap.reason is HoleReason.BALANCES_MEET_NET_NIL
        assert (gap.first_day, gap.last_day) == (D(2026, 2, 12), D(2026, 3, 11))
        assert "net to nil" in _lines(gap)[2]

    def test_TheFeedHoldingPaymentsNoStatementLists_MakesAMeetingHoleStated(self, tmp_path):
        with Store(tmp_path / "s.sqlite3") as store:
            first = statement(
                store,
                tmp_path,
                "fed",
                D(2026, 1, 12),
                10000,
                [Spend(D(2026, 1, 6), "fed A", 1000)],
                received=D(2026, 1, 13),
            )
            second = statement(
                store,
                tmp_path,
                "fed",
                D(2026, 2, 11),
                first,
                [Spend(D(2026, 2, 5), "fed B", 1000)],
                received=D(2026, 2, 12),
            )
            third = statement(
                store,
                tmp_path,
                "fed",
                D(2026, 4, 10),
                second,
                [Spend(D(2026, 4, 3), "fed C", 1000)],
                received=D(2026, 4, 11),
            )
            statement(
                store,
                tmp_path,
                "fed",
                D(2026, 5, 12),
                third,
                [Spend(D(2026, 5, 6), "fed D", 1000)],
                received=D(2026, 5, 13),
            )
            feed(
                store,
                "fed",
                [Spend(D(2026, 2, 20), "fed in", -4000), Spend(D(2026, 2, 22), "fed out", 4000)],
                digest="fed-feed",
            )
            (gap,) = [g for g in _report(store).gaps if g.kind is GapKind.HOLE_BETWEEN]

        assert (gap.basis, gap.reason, gap.unlisted_rows) == (
            Basis.STATED,
            HoleReason.UNLISTED_ROWS,
            2,
        )
        assert "Another source holds 2 payments dated in these days" in _lines(gap)[2]


class TestWhenAStatementIsDue:
    def test_AStatementReceivedMonthsAfterItClosed_SaysNothingAboutHowSoonOneAppears(
        self, tmp_path
    ):
        # Three statements uploaded in one sitting months later: counting that wait as the time
        # a statement takes to appear would hide a newest statement that is overdue.
        with Store(tmp_path / "s.sqlite3") as store:
            for month in (1, 2, 3):
                statement(
                    store,
                    tmp_path,
                    "late",
                    D(2026, month, 10),
                    10000 + month * 1000,
                    [Spend(D(2026, month, 4), f"late {month}", 1000)],
                    received=D(2026, 5, 20),
                )
            keep_statement_readings(store)
            from obdi.verify.statement_span import statement_spans

            following = statement_spans(store, TODAY)["late"].next

        assert following is not None
        assert following.lag_days is None
        assert following.due == (D(2026, 4, 10), D(2026, 5, 10))


class TestSpacesAreNotAskedForStatements:
    @pytest.fixture(scope="class")
    def household(self, tmp_path_factory):
        root = tmp_path_factory.mktemp("spaces")
        with Store(root / "s.sqlite3") as store:

            def declare(ref, **fields):
                store.declare_account(AccountRecord(ref=AccountRef(ref), label=ref, **fields))

            declare("sp-main")
            for ref in ("sp-feed", "sp-qif", "sp-one"):
                declare(ref, kind=SPACE_KIND, parent=AccountRef("sp-main"))
            declare("sp-old", kind=SPACE_KIND, parent=AccountRef("sp-main"), closed=D(2026, 3, 1))
            declare("sp-orphan", kind=SPACE_KIND)
            rows = [
                Spend(D(2026, 4, 2), "spaces one", 100),
                Spend(D(2026, 4, 20), "spaces two", 200),
            ]
            for ref in ("sp-main", "sp-feed", "sp-one", "sp-old", "sp-orphan"):
                feed(store, ref, rows, digest=f"{ref}-feed")
            qif = root / "sp-qif.qif"
            qif.write_text(
                "!Type:CCard\nD03/04/2026\nT-1.00\nPSpace Qif A\n^\nD20/04/2026\nT-2.00\n"
                "PSpace Qif B\n^\n",
                encoding="utf-8",
            )
            import_file(store, qif, account_id="sp-qif")
            record_stated_anchor(store, "sp-one", "2026-04-10", "10.00", today=TODAY)
            parents = {
                str(r.ref): str(r.parent) for r in store.declared_accounts() if r.parent is not None
            }
            return _report(store), parents

    def test_ASpaceWhateverItHolds_IsNeverListedAsSomethingToFetch(self, household):
        report, _ = household

        assert {g.account for g in report.gaps} == {"sp-main", "sp-orphan"}
        assert {g.kind for g in report.gaps} == {GapKind.AUTOMATIC_ONLY}
        assert len(report.gaps) == 2

    def test_TheSpaces_AreListedAsNeedingNothing_NamingTheirMainAccount(self, household):
        report, _ = household

        spaces = {o.account: o.space_of for o in report.accounts if o.space_of}

        assert spaces == {"sp-feed": "sp-main", "sp-qif": "sp-main", "sp-one": "sp-main"}
        assert all(not o.gaps for o in report.accounts if o.space_of)

    def test_TheArchivedSpace_IsNotListedAtAll(self, household):
        report, _ = household

        assert "sp-old" not in {o.account for o in report.accounts}

    def test_ASpaceDeclaredWithNoParent_IsNotRecognisedAsOneAndKeepsItsGap(self, household):
        report, _ = household

        orphan = next(o for o in report.accounts if o.account == "sp-orphan")
        assert orphan.space_of == ""
        assert [g.kind for g in orphan.gaps] == [GapKind.AUTOMATIC_ONLY]

    def test_ThePage_NeverListsASpaceAndNeverAsksForAStatementForOne(self, household):
        report, _ = household
        page = render_bring_in(
            BringInData(today=TODAY, report=report, unread="", names=AccountsShown())
        ).decode()
        blocks = {
            s.attrs.get("aria-label")
            for s in elements(parse(page), "section")
            if "bi-account" in s.classes
        }

        assert {"sp-feed", "sp-qif", "sp-one"}.isdisjoint(blocks)
        # The family's one gap is a balance to state (AUTOMATIC_ONLY), an account page's to-do and
        # not a file, so no account of the family has a block here.
        assert "sp-main" not in blocks

    def test_TheMainAccountOfSpaces_KeepsItsOwnGap(self, household):
        report, _ = household

        main = next(o for o in report.accounts if o.account == "sp-main")
        assert [g.kind for g in main.gaps] == [GapKind.AUTOMATIC_ONLY]
