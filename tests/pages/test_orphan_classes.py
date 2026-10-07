"""The removal's size guard asks only about the orphans obdi cannot explain.

On the live instance an audit found 107 rows in one account carrying an imported id the
account no longer expects. 92 of them had just become history because a reversed payment is no
longer counted. The removal page said "Unexpectedly large removal" and demanded a tick that
the operator, who cannot see inside Actual, could not honestly give; the only way through was
to empty the budget and push 9,000 rows again, twice in one day.

obdi can say why it no longer expects most orphans, from its own store. The audit sorts each
orphan into history (reversed, void, or folded), held under another account, or unknown. Only
the unknown ones count towards the thresholds (`web_prune`).

Known answers, written down before the first run. Thresholds: 100 rows from one account;
at least 20 rows and a quarter of what obdi imported there; 250 across a general removal.

    107 orphans, 92 history and 15 unknown      -> no second tick
    107 orphans, all unknown                    -> second tick, and the page says so
    150 orphans, 100 unknown and 50 history     -> second tick (100 unknown is the static rule)
    179 orphans,  99 unknown and 80 history     -> no second tick (one under the static rule)
    three accounts of 300 history and 83 unknown each -> no second tick (249 unknown in all)
    three accounts of 300 history and 84 unknown each -> second tick (252 unknown in all)
    classes that do not add up to the orphans   -> every orphan counts, as before
"""

from __future__ import annotations

import json
from dataclasses import replace

from obdi.core.models import TransactionStatus
from obdi.export.actual_push import build_audit_envelope
from obdi.export.replay import history_imported_ids, to_actual_transaction
from obdi.ingest.store import Store
from obdi.pages.web_prune import OrphanCount, counts_from_audit, high_reasons, total_reason
from test_prune_clear import (  # noqa: F401
    Calls,
    account,
    audit,
    clear_form,
    general_form,
    page_of,
    press,
    serve,
)
from test_transfer_pairs_payload import (
    BOUND_BOTH,
    INCOME,
    SPEND,
    TO_POT,
    _household,
    _item,
)

SECOND_TICK = "I have checked this count against Actual"


def classed(
    account_id: str,
    name: str,
    *,
    history: int = 0,
    elsewhere: int = 0,
    unknown: int = 0,
    present: int = 900,
    expected: int | None = None,
) -> dict[str, object]:
    orphaned = history + elsewhere + unknown
    entry = account(
        account_id,
        name,
        expected=present + 5 if expected is None else expected,
        present=present,
        orphaned=orphaned,
    )
    entry["orphaned_explained"] = {"history": history, "elsewhere": elsewhere, "unknown": unknown}
    return entry


class TestTheAuditEnvelopeNamesTheRowsObdiHoldsAsHistory:
    def test_AuditEnvelope_WhenARowIsReversed_ListsItsImportedIdAndNoBookedOne(self, tmp_path):
        reversed_item = {
            **_item("gone", 4200, "OUT", "MASTER_CARD", "05", "Cafe"),
            "status": "REVERSED",
        }
        with Store(tmp_path / "s.sqlite3") as store:
            _household(store, main=[INCOME, TO_POT, SPEND, reversed_item])
            rows = store.all_transactions()
            history = [t for t in rows if t.status is TransactionStatus.REVERSED]
            booked = [t for t in rows if not t.status.is_history]

            envelope = build_audit_envelope(store, BOUND_BOTH)

        assert len(history) == 1, "the reversed row must be in the store for this to mean anything"
        assert envelope["history"] == [to_actual_transaction(history[0])["imported_id"]]
        for row in booked:
            assert to_actual_transaction(row)["imported_id"] not in envelope["history"]
        assert json.dumps(envelope["history"]).count("4200") == 0

    def test_AuditEnvelope_WhenNothingIsHistory_CarriesAnEmptyList(self, tmp_path):
        with Store(tmp_path / "s.sqlite3") as store:
            _household(store)

            envelope = build_audit_envelope(store, BOUND_BOTH)

        assert envelope["history"] == []


class TestWhichImportedIdsCountAsHistory:
    def test_History_ForVoidFoldedAndReversedRows_ListsEachOnceSorted(self, tmp_path):
        with Store(tmp_path / "s.sqlite3") as store:
            _household(store)
            rows = store.all_transactions()
            statuses = [
                TransactionStatus.VOID,
                TransactionStatus.FOLDED,
                TransactionStatus.REVERSED,
            ]
            changed = [replace(t, status=s) for t, s in zip(rows, statuses, strict=False)]
            expected = sorted(str(to_actual_transaction(t)["imported_id"]) for t in changed)

            listed = history_imported_ids([*changed, changed[0], *rows[3:]])

        assert listed == expected

    def test_History_WhenAnIdIsAlsoSentForABookedRow_IsNotListed(self, tmp_path):
        with Store(tmp_path / "s.sqlite3") as store:
            _household(store)
            booked = store.all_transactions()[0]

            listed = history_imported_ids([booked, replace(booked, status=TransactionStatus.VOID)])

        assert listed == []

    def test_History_ForARowWithNoContentKey_IsLeftOutRatherThanInvented(self, tmp_path):
        with Store(tmp_path / "s.sqlite3") as store:
            _household(store)
            keyless = replace(
                store.all_transactions()[0], status=TransactionStatus.VOID, content_key=""
            )

            listed = history_imported_ids([keyless])

        assert listed == []


def total_counts(each_history: int, each_unknown: int) -> list[dict[str, object]]:
    return [
        classed(f"id{n}", f"Account {n}", history=each_history, unknown=each_unknown, present=9000)
        for n in range(3)
    ]


class TestTheGuardCountsOnlyWhatObdiCannotExplain:
    def test_Removal_When107OrphansOf92AreHistoryAnd15Unknown_NeedsNoSecondTick(self, serve):  # noqa: F811
        calls = Calls()
        base = serve(
            [audit(classed("p-id", "Personal (starling)", history=92, unknown=15))],
            prune_actual=calls,
        )

        page = page_of(base)
        response = press(base, confirm="yes", confirmed=["107:p-id"])

        assert "Unexpectedly large removal" not in general_form(page)
        assert response.status_code == 200
        assert calls.calls == [{"confirmed": {"p-id": 107}}]

    def test_Removal_When107OrphansAreAllUnknown_StillDemandsTheSecondTickAndSaysWhy(self, serve):  # noqa: F811
        calls = Calls()
        base = serve(
            [audit(classed("p-id", "Personal (starling)", unknown=107))], prune_actual=calls
        )

        page = page_of(base)
        refused = press(base, confirm="yes", confirmed=["107:p-id"])
        allowed = press(base, confirm="yes", checked="yes", confirmed=["107:p-id"])

        assert "Unexpectedly large removal" in general_form(page)
        assert "107 rows that obdi cannot explain" in general_form(page)
        assert refused.status_code == 400
        assert SECOND_TICK in refused.text
        assert allowed.status_code == 200
        assert calls.calls == [{"confirmed": {"p-id": 107}}]

    def test_Removal_WhenUnknownAloneReachesTheStaticRule_DemandsTheTickDespiteManyExplained(
        self,
        serve,  # noqa: F811
    ):
        calls = Calls()
        base = serve(
            [audit(classed("p-id", "Personal", history=50, unknown=100))], prune_actual=calls
        )

        page = page_of(base)
        refused = press(base, confirm="yes", confirmed=["150:p-id"])

        form = general_form(page)
        assert "Unexpectedly large removal" in form
        assert "100 rows that obdi cannot explain" in form
        assert refused.status_code == 400
        assert calls.calls == []

    def test_Removal_WhenUnknownIsOneUnderTheStaticRule_NeedsNoTickHoweverManyAreExplained(
        self,
        serve,  # noqa: F811
    ):
        calls = Calls()
        base = serve(
            [audit(classed("p-id", "Personal", history=60, elsewhere=20, unknown=99))],
            prune_actual=calls,
        )

        page = page_of(base)
        response = press(base, confirm="yes", confirmed=["179:p-id"])

        assert "Unexpectedly large removal" not in general_form(page)
        assert response.status_code == 200
        assert calls.calls == [{"confirmed": {"p-id": 179}}]

    def test_Removal_WhenExplainedAreHeldElsewhere_AreExcusedLikeHistory(self, serve):  # noqa: F811
        base = serve(
            [audit(classed("p-id", "Personal", elsewhere=200))], prune_actual=Calls()
        )

        assert "Unexpectedly large removal" not in general_form(page_of(base))

    def test_Removal_WhenTheTotalOfUnknownIsJustUnderTheTotalRule_NeedsNoTick(self, serve):  # noqa: F811
        calls = Calls()
        base = serve([audit(*total_counts(300, 83))], prune_actual=calls)
        counts = [f"383:id{n}" for n in range(3)]

        page = page_of(base)
        response = press(base, confirm="yes", confirmed=counts)

        assert "Unexpectedly large removal" not in general_form(page)
        assert response.status_code == 200

    def test_Removal_WhenTheTotalOfUnknownReachesTheTotalRule_DemandsTheTickAndSaysUnknown(
        self,
        serve,  # noqa: F811
    ):
        calls = Calls()
        base = serve([audit(*total_counts(300, 84))], prune_actual=calls)
        counts = [f"384:id{n}" for n in range(3)]

        refused = press(base, confirm="yes", confirmed=counts)

        assert refused.status_code == 400
        assert "252 rows that obdi cannot explain in all" in refused.text
        assert calls.calls == []

    def test_Removal_WhenTheClassesDoNotAddUpToTheOrphans_EveryOrphanCountsAsBefore(self, serve):  # noqa: F811
        broken = classed("p-id", "Personal", history=92, unknown=15)
        broken["orphaned_explained"] = {"history": 92, "elsewhere": 0, "unknown": 14}
        calls = Calls()
        base = serve([audit(broken)], prune_actual=calls)

        page = page_of(base)
        refused = press(base, confirm="yes", confirmed=["107:p-id"])

        assert "Unexpectedly large removal" in general_form(page)
        assert "107 rows from Personal" in general_form(page)
        assert "cannot explain" not in general_form(page)
        assert refused.status_code == 400

    def test_Removal_WhenAnAuditSaysNothingOfClasses_EveryOrphanCountsAsBefore(self, serve):  # noqa: F811
        plain = account("p-id", "Personal", expected=905, present=900, orphaned=107)
        base = serve([audit(plain)], prune_actual=Calls())

        form = general_form(page_of(base))

        assert "Unexpectedly large removal" in form
        assert "it would remove 107 rows from Personal" in form
        assert "of the 107:" not in form

    def test_Removal_WhenAClassCountIsNegativeOrNotWhole_IsTreatedAsNoClassification(self):
        for bad in (-1, 2.5, "7", True, None):
            entry = classed("p-id", "Personal", history=5, unknown=2)
            entry["orphaned_explained"] = {"history": 5, "elsewhere": bad, "unknown": 2}

            (count,) = counts_from_audit(audit(entry)) or []

            assert count.explained is None, bad
            assert count.unexplained == 7, bad


class TestThePageSaysHowObdiExplainsTheOrphans:
    def test_GeneralForm_ShowsTheBreakdownByClassAsCountsOnly(self, serve):  # noqa: F811
        base = serve(
            [audit(classed("p-id", "Personal (starling)", history=92, elsewhere=3, unknown=15))],
            prune_actual=Calls(),
        )

        page = page_of(base)
        form = general_form(page)

        assert "Personal (starling): 110 rows" in form
        assert "of the 110: 92 now history (reversed, void, or folded)" in form
        assert "3 held under another account now" in form
        assert "15 not a row obdi holds" in form
        assert "not counted by the large-removal check" in form
        assert "£" not in page

    def test_ClearForm_ForAnAccountThatExpectsNothing_ShowsTheBreakdownAndTheGuardOnUnknown(
        self,
        serve,  # noqa: F811
    ):
        base = serve(
            [audit(classed("old-id", "Old Joint", history=150, unknown=5, present=0, expected=0))],
            prune_actual=Calls(),
        )

        form = clear_form(page_of(base))

        assert "Of the 155: 150 now history" in form
        assert "Unexpectedly large removal" not in form

    def test_ClearForm_WhenTheUnknownRowsAloneAreManyAndAllOfWhatObdiImported_AsksForTheTick(
        self,
        serve,  # noqa: F811
    ):
        base = serve(
            [audit(classed("old-id", "Old Joint", history=1, unknown=20, present=0, expected=0))],
            prune_actual=Calls(),
        )

        form = clear_form(page_of(base))

        assert "Unexpectedly large removal" in form
        assert "20 rows that obdi cannot explain" in form


class TestTheThresholdFunctionsDirectly:
    def test_HighReasons_CountOnlyUnexplained(self):
        explained = {"history": 500, "elsewhere": 0, "unknown": 3}
        count = OrphanCount("id", "Name", 900, 900, 503, explained=explained)

        assert high_reasons(count) == []
        assert count.unexplained == 3

    def test_TotalReason_CountsOnlyUnexplained(self):
        explained = {"history": 900, "elsewhere": 0, "unknown": 10}
        counts = [OrphanCount(f"a{n}", f"A{n}", 9, 9, 910, explained=explained) for n in range(3)]

        assert total_reason(counts) is None
