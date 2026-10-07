"""One press that brings Actual into line with obdi.

After an audit that found differences the Actual page offers one press that runs, in the
applier and in order, a push (which re-links transfers whose partner changed), an audit, the
removal of the orphans obdi can explain, a second push if the removal unlinked anything, and a
final audit. It stops at the first failure and says which step.

Known answers, written down before the first run. What the press may remove per account is
decided from the newest audit by the same thresholds the removal form uses (`web_prune`):

    107 orphans, 92 history and 15 unknown, 905 present  -> every orphan, ceiling 107
    107 orphans, all unknown                             -> explained only, ceiling 0, 107 kept
    150 orphans, 50 history and 100 unknown              -> explained only, ceiling 50, 100 kept
    99 unknown and 80 history                            -> every orphan (one under the rule)
    an audit that does not classify, orphans under the thresholds -> every orphan
    an audit that does not classify, orphans over them   -> the account is left out and said so
    three accounts of 300 history and 84 unknown each    -> explained only in all three (total rule)
    an account that expects nothing                      -> never in scope
    an account with no orphans                           -> in scope with ceiling 0
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

from obdi import web
from obdi.actual_push import (
    build_align_envelope,
    empty_pending_note,
    latest_results,
    latest_results_with_totals,
    queued_requests,
)
from obdi.cli import queue_actual_align
from obdi.core.namespaces import QUEUE_KINDS
from obdi.store import Store
from obdi.web_prune import align_plan, counts_from_audit
from test_orphan_classes import classed, total_counts
from test_prune_clear import account, audit, ordinary, page_of, serve  # noqa: F401
from test_transfer_pairs_payload import BOUND_BOTH, MAIN, POT, _household

ONE_ACCOUNT_MAP = {
    "bindings": [],
    "actual": [{"canonical_id": MAIN, "actual_account_id": "act-main"}],
}


def plan_of(*accounts: dict[str, object]):
    counts = counts_from_audit(audit(*accounts))
    assert counts is not None
    return align_plan(counts)


class TestWhatThePressMayRemove:
    def test_Plan_When107OrphansOf92AreHistoryAnd15Unknown_MayTakeEveryOrphan(self):
        plan = plan_of(classed("p", "Personal", history=92, unknown=15))

        assert plan.scope == {"p": "all"}
        assert plan.confirmed == {"p": 107}
        assert plan.kept_back == {}

    def test_Plan_When107OrphansAreAllUnknown_MayTakeNoneAndSaysAll107AreKeptBack(self):
        plan = plan_of(classed("p", "Personal", unknown=107))

        assert plan.scope == {"p": "explained"}
        assert plan.confirmed == {"p": 0}
        assert plan.kept_back == {"Personal": 107}

    def test_Plan_WhenUnknownReachesTheStaticRule_TakesOnlyTheExplainedOnes(self):
        plan = plan_of(classed("p", "Personal", history=50, unknown=100))

        assert plan.scope == {"p": "explained"}
        assert plan.confirmed == {"p": 50}
        assert plan.kept_back == {"Personal": 100}

    def test_Plan_WhenUnknownIsOneUnderTheStaticRule_MayTakeEveryOrphan(self):
        plan = plan_of(classed("p", "Personal", history=80, unknown=99))

        assert plan.scope == {"p": "all"}
        assert plan.confirmed == {"p": 179}

    def test_Plan_WhenTheAuditDoesNotClassifyAndTheOrphansAreFew_MayTakeEveryOrphan(self):
        plan = plan_of(ordinary("a", "Alpha", present=50, orphaned=7))

        assert plan.scope == {"a": "all"}
        assert plan.confirmed == {"a": 7}

    def test_Plan_WhenTheAuditDoesNotClassifyAndTheOrphansAreMany_LeavesTheAccountOutAndSaysSo(
        self,
    ):
        plan = plan_of(ordinary("a", "Alpha", present=900, orphaned=150))

        assert plan.scope == {}
        assert plan.confirmed == {}
        assert plan.uncovered == {"Alpha": 150}

    def test_Plan_WhenTheTotalOfUnknownReachesTheTotalRule_EveryAccountIsExplainedOnly(self):
        plan = plan_of(*total_counts(300, 84))

        assert set(plan.scope.values()) == {"explained"}
        assert plan.confirmed == {f"id{n}": 300 for n in range(3)}
        assert plan.kept_back == {f"Account {n}": 84 for n in range(3)}

    def test_Plan_WhenTheTotalOfUnknownIsJustUnderTheTotalRule_EveryAccountMayTakeEverything(
        self,
    ):
        plan = plan_of(*total_counts(300, 83))

        assert set(plan.scope.values()) == {"all"}
        assert plan.confirmed == {f"id{n}": 383 for n in range(3)}

    def test_Plan_ForAnAccountThatExpectsNothing_IsNeverInScope(self):
        old = account("old", "Old Joint", expected=0, present=0, orphaned=12)

        plan = plan_of(old, classed("p", "Personal", history=3))

        assert plan.scope == {"p": "all"}

    def test_Plan_ForAnAccountWithNoOrphans_IsInScopeWithACeilingOfZero(self):
        plan = plan_of(classed("p", "Personal", present=50))

        assert plan.scope == {"p": "all"}
        assert plan.confirmed == {"p": 0}


#: A push that applied before the audits the tests serve (their audits finish on 1 October at
#: 13:00), so the audit is the newer and its differences are what the verdict reports.
EARLIER_PUSH: dict[str, object] = {
    "ok": True,
    "request": "push-x.json",
    "finished_at": "2026-10-01T12:00:00Z",
    "added": 1,
    "provisioned": 0,
}


def with_differences(*extra: dict[str, object]) -> dict[str, object]:
    return audit(classed("p-id", "Personal (starling)", history=92, unknown=15), *extra)


def clean_audit() -> dict[str, object]:
    return audit(classed("p-id", "Personal (starling)", present=50))


class AlignCalls:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def __call__(self, **kwargs: object) -> str:
        self.calls.append(kwargs)
        return "queued align"


def align_form(page: str) -> str:
    import re

    for form in re.findall(r"<form.*?</form>", page, flags=re.S):
        if 'action="/align-actual"' in form:
            return form
    raise AssertionError("no align form")


def post_align(base: str, **fields: object) -> httpx.Response:
    return httpx.post(f"{base}/align-actual", data=fields, timeout=20)


class TestThePageOffersOnePress:
    def test_Page_AfterAnAuditThatFoundDifferences_OffersOnePressSayingWhatItDoesInOrder(
        self, serve  # noqa: F811
    ):
        base = serve([EARLIER_PUSH, with_differences()], align_actual=AlignCalls())

        form = align_form(page_of(base))

        assert "Bring Actual into line" in form
        for earlier, later in (
            ("push", "audit"),
            ("audit", "remove"),
            ("remove", "push again"),
            ("push again", "audit again"),
        ):
            assert form.index(earlier) < form.index(later), (earlier, later)
        assert "re-links transfers whose partner changed" in form
        assert "stops at the first step that fails" in form
        assert "I understand rows carrying obdi's imported ids" in form

    def test_Page_NamesHowManyRowsItWillRemoveAndHowManyItKeepsBack(self, serve):  # noqa: F811
        audited = audit(
            classed("p-id", "Personal (starling)", history=50, unknown=100),
            classed("q-id", "Joint", history=4, elsewhere=1, present=60),
        )
        base = serve([EARLIER_PUSH, audited], align_actual=AlignCalls())

        form = align_form(page_of(base))

        assert "55 rows" in form
        assert "100 rows in Personal (starling) that obdi cannot explain are left alone" in form
        assert "extra tick" in form

    def test_Page_WhenAnAccountCannotBeJudged_SaysItIsLeftOut(self, serve):  # noqa: F811
        audited = audit(ordinary("a", "Alpha", present=900, orphaned=150))
        base = serve([EARLIER_PUSH, audited], align_actual=AlignCalls())

        form = align_form(page_of(base))

        assert "Alpha" in form
        assert "no breakdown" in form

    def test_Page_WhenTheAuditWasClean_OffersNoPress(self, serve):  # noqa: F811
        page = page_of(serve([clean_audit()], align_actual=AlignCalls()))

        assert "/align-actual" not in page

    def test_Page_WhenThereIsNoAudit_OffersNoPress(self, serve):  # noqa: F811
        page = page_of(serve([], align_actual=AlignCalls()))

        assert "/align-actual" not in page

    def test_Page_WhenTheNewestAuditFailed_OffersNoPress(self, serve):  # noqa: F811
        page = page_of(serve([audit(ok=False)], align_actual=AlignCalls()))

        assert "/align-actual" not in page

    def test_Page_WhenNoAlignHookIsWired_OffersNoPress(self, serve):  # noqa: F811
        page = page_of(serve([with_differences()]))

        assert "/align-actual" not in page

    def test_Page_ShowsCountsOnlyAndReadsAtPhoneWidth(self, serve):  # noqa: F811
        page = page_of(serve([EARLIER_PUSH, with_differences()], align_actual=AlignCalls()))
        form = align_form(page)

        assert "£" not in page
        assert "amount" not in form.lower()
        assert 'class="button' in form, "a control the stylesheet sizes for a thumb"

    def test_Page_WhenAPushFollowedTheAudit_OffersNoPressUntilTheNextAudit(self, serve):  # noqa: F811
        later_push = {**EARLIER_PUSH, "finished_at": "2026-10-01T14:00:00Z"}

        page = page_of(serve([with_differences(), later_push], align_actual=AlignCalls()))

        assert "/align-actual" not in page

    def test_Page_WhenTheAuditDiffered_PutsThePressBesideTheVerdictAsTheFilledButton(
        self, serve  # noqa: F811
    ):
        page = page_of(serve([EARLIER_PUSH, with_differences()], align_actual=AlignCalls()))

        form = align_form(page)
        assert page.index('data-state="differs"') < page.index(form)
        assert '<button class="button" type="submit">Bring Actual into line' in form


class TestThePostThatQueuesTheJob:
    def test_Post_WithTheTick_QueuesTheScopeAndCeilingsComputedFromTheNewestAudit(
        self, serve  # noqa: F811
    ):
        calls = AlignCalls()
        base = serve([with_differences()], align_actual=calls)

        response = post_align(base, confirm="yes")

        assert response.status_code == 200
        assert calls.calls == [{"scope": {"p-id": "all"}, "confirmed": {"p-id": 107}}]

    def test_Post_WithForgedScopeAndCeilingFields_IgnoresThemAndUsesTheAudit(
        self, serve  # noqa: F811
    ):
        calls = AlignCalls()
        base = serve(
            [audit(classed("p-id", "Personal", unknown=107))], align_actual=calls
        )

        response = post_align(
            base, confirm="yes", scope="p-id:all", confirmed="9999:p-id", checked="yes"
        )

        assert response.status_code == 200
        assert calls.calls == [{"scope": {"p-id": "explained"}, "confirmed": {"p-id": 0}}]

    def test_Post_WithoutTheTick_IsRefusedAndQueuesNothing(self, serve):  # noqa: F811
        calls = AlignCalls()
        base = serve([with_differences()], align_actual=calls)

        response = post_align(base)

        assert response.status_code == 400
        assert calls.calls == []
        assert 'href="/actual"' in response.text

    def test_Post_WhenThereIsNoAudit_IsRefusedAndQueuesNothing(self, serve):  # noqa: F811
        calls = AlignCalls()
        base = serve([], align_actual=calls)

        response = post_align(base, confirm="yes")

        assert response.status_code == 400
        assert "audit" in response.text
        assert calls.calls == []

    def test_Post_WhenTheNewestAuditWasClean_IsRefusedAndQueuesNothing(self, serve):  # noqa: F811
        calls = AlignCalls()
        base = serve([clean_audit()], align_actual=calls)

        response = post_align(base, confirm="yes")

        assert response.status_code == 400
        assert "no differences" in response.text
        assert calls.calls == []

    def test_Post_WhenTheNewestAuditFailed_IsRefusedAndQueuesNothing(self, serve):  # noqa: F811
        calls = AlignCalls()
        base = serve([audit(ok=False)], align_actual=calls)

        response = post_align(base, confirm="yes")

        assert response.status_code == 400
        assert calls.calls == []

    def test_Post_WhenNoHookIsWired_AnswersNotAvailable(self, serve):  # noqa: F811
        base = serve([with_differences()])

        assert post_align(base, confirm="yes").status_code == 404

    def test_Post_WhenTheHookRefuses_SaysWhyAndNothingIsClaimedQueued(self, serve):  # noqa: F811
        def refusing(**_: object) -> str:
            raise ValueError("an empty of Actual is pending")

        base = serve([with_differences()], align_actual=refusing)

        response = post_align(base, confirm="yes")

        assert response.status_code == 409
        assert "an empty of Actual is pending" in response.text
        assert "Align queued" not in response.text
        assert "Nothing was queued" in response.text

    def test_Post_FromAnotherSite_IsRefusedAndQueuesNothing(self, serve):  # noqa: F811
        calls = AlignCalls()
        base = serve([with_differences()], align_actual=calls)

        response = httpx.post(
            f"{base}/align-actual",
            data={"confirm": "yes"},
            headers={"Origin": "https://evil.example"},
            timeout=20,
        )

        assert response.status_code == 403
        assert calls.calls == []

    def test_Get_StartsNothing(self, serve):  # noqa: F811
        calls = AlignCalls()
        base = serve([with_differences()], align_actual=calls)

        response = httpx.get(f"{base}/align-actual", timeout=20)

        assert response.status_code in (404, 405)
        assert calls.calls == []
        page_of(base)
        assert calls.calls == []


def stamp(minute: int) -> str:
    return datetime(2026, 10, 4, 12, minute, tzinfo=UTC).isoformat()


def align_result(*, complete: bool = True, stopped_at: str | None = None, **extra: object) -> dict:
    steps: list[dict[str, object]] = [
        {
            "step": "push",
            "result": {
                "kind": "push",
                "ok": True,
                "finished_at": stamp(1),
                "added": 0,
                "provisioned": 0,
                "transfers": {"linked": 0, "relinked": 20, "already_linked": 1411, "skipped": {}},
            },
        },
        {
            "step": "audit",
            "result": {
                "kind": "audit",
                "ok": True,
                "finished_at": stamp(2),
                "accounts": [classed("p-id", "Personal (starling)", history=92, unknown=15)],
            },
        },
        {
            "step": "prune",
            "result": {
                "kind": "prune",
                "ok": True,
                "finished_at": stamp(3),
                "accounts": [{"account_id": "p-id", "name": "Personal (starling)", "removed": 92}],
            },
        },
        {"step": "audit_final", "result": {
            "kind": "audit",
            "ok": True,
            "finished_at": stamp(4),
            "accounts": [classed("p-id", "Personal (starling)", unknown=15)],
        }},
    ]
    return {
        "kind": "align",
        "ok": True,
        "request": "align-20261004T120000000000Z.json",
        "finished_at": stamp(5),
        "complete": complete,
        "stopped_at": stopped_at,
        "stopped": None if complete else "1 transfer pair(s) failed to link",
        "steps": steps if complete else steps[:1],
        **extra,
    }


class TestTheResultShowsEachStepAndWhereItStopped:
    def test_Row_WhenEveryStepRan_SaysSoAndNamesTheSteps(self):
        row = web._result_row(align_result())

        assert "aligned" in row
        assert "push, audit, remove, audit again" in row
        assert "stopped" not in row

    def test_Row_WhenItStoppedAtTheFirstStep_NamesTheStepAndTheReasonAndIsNotASuccess(self):
        row = web._result_row(align_result(complete=False, stopped_at="push"))

        assert "pill-bad" in row
        assert "stopped at the push step" in row
        assert "1 transfer pair(s) failed to link" in row
        assert "aligned" not in row

    def test_Row_WhenStoppedAtThePushAgainStep_NamesItInWords(self):
        row = web._result_row(
            {**align_result(complete=False, stopped_at="push_again"), "stopped": "x"}
        )

        assert "stopped at the second push step" in row

    def test_Row_WhenAStepWasSkipped_SaysWhy(self):
        result = align_result()
        steps = result["steps"]
        assert isinstance(steps, list)
        steps[2] = {"step": "prune", "skipped": "the audit found no orphan in an account in scope"}

        row = web._result_row(result)

        assert "remove: skipped - the audit found no orphan" in row

    def test_Row_WhenTheResultCarriesNoCompleteFlag_IsReportedAsUnreadableNotAsSuccess(self):
        row = web._result_row({"kind": "align", "ok": True, "finished_at": stamp(5)})

        assert "not understood" in row
        assert "aligned" not in row

    def test_Row_WhenTheRequestFailedOutright_ShowsItsErrorAsAFailure(self):
        row = web._result_row(
            {"kind": "align", "ok": False, "finished_at": stamp(5), "error": "applier crashed"}
        )

        assert "failed" in row
        assert "applier crashed" in row

    def test_Row_EscapesAStopReasonAndNamesNoFigure(self):
        result = align_result(complete=False, stopped_at="prune")
        result["stopped"] = "<script>x</script> 871.23"

        row = web._result_row(result)

        assert "<script>" not in row


class TestEachStepAppearsInTheHistoryAsItsOwnResult:
    def write(self, directory: Path, name: str, payload: dict) -> None:
        results = directory / "results"
        results.mkdir(parents=True, exist_ok=True)
        (results / name).write_text(json.dumps(payload), encoding="utf-8")

    def test_LatestResults_ForAnAlign_ListsItsStepsAsOrdinaryResultsBesideTheSummary(
        self, tmp_path
    ):
        self.write(tmp_path, "align-1.json", align_result())

        results = latest_results(tmp_path, limit=50)

        kinds = sorted(str(r["kind"]) for r in results)
        assert kinds == ["align", "audit", "audit", "prune", "push"]
        newest_audit = max(
            (r for r in results if r["kind"] == "audit"), key=lambda r: str(r["finished_at"])
        )
        assert newest_audit["finished_at"] == stamp(4)
        assert all(r["request"] == "align-20261004T120000000000Z.json" for r in results)

    def test_LatestResults_ForAStoppedAlign_ListsOnlyTheStepsThatRan(self, tmp_path):
        self.write(tmp_path, "align-1.json", align_result(complete=False, stopped_at="push"))

        results = latest_results(tmp_path, limit=50)

        assert sorted(str(r["kind"]) for r in results) == ["align", "push"]

    def test_LatestResults_ForASkippedStep_ListsNoResultForIt(self, tmp_path):
        result = align_result()
        steps = result["steps"]
        assert isinstance(steps, list)
        steps[2] = {"step": "prune", "skipped": "nothing"}
        self.write(tmp_path, "align-1.json", result)

        results = latest_results(tmp_path, limit=50)

        assert "prune" not in {r["kind"] for r in results}

    def test_LatestResults_ForAnAlignWhoseStepsAreMalformed_KeepsTheSummaryAndDropsTheRest(
        self, tmp_path
    ):
        self.write(
            tmp_path,
            "align-1.json",
            {**align_result(), "steps": ["nope", {"step": "push"}, {"result": 5}, None]},
        )

        results = latest_results(tmp_path, limit=50)

        assert [r["kind"] for r in results] == ["align"]

    def test_LatestResultsWithTotals_CountsTheStepsAsResultsAndTheDenominatorSaysSo(self, tmp_path):
        self.write(tmp_path, "align-1.json", align_result())
        self.write(
            tmp_path,
            "push-1.json",
            {"kind": "push", "ok": True, "finished_at": stamp(9), "added": 0, "provisioned": 0},
        )

        results, total, unreadable = latest_results_with_totals(tmp_path, limit=3)

        assert len(results) == 3
        assert total == 6
        assert unreadable == []

    def test_EmptyPendingNote_WhenTheOnlyAuditSinceAPartialEmptyIsAnAlignsOwn_TheBlockEnds(
        self, tmp_path
    ):
        self.write(
            tmp_path,
            "empty-1.json",
            {
                "kind": "empty",
                "ok": True,
                "complete": False,
                "finished_at": stamp(0),
                "accounts_removed": 1,
            },
        )
        assert empty_pending_note(tmp_path) is not None

        self.write(tmp_path, "align-1.json", align_result())

        assert empty_pending_note(tmp_path) is None

    def test_StepsFlattenedOnTheActualPage_FeedTheSummaryAndTheRemovalCounts(self, serve):  # noqa: F811
        flat = latest_results_of(align_result())
        base = serve(flat, align_actual=AlignCalls(), prune_actual=AlignCalls())

        page = page_of(base)

        assert "audit: differences" in page
        assert "Personal (starling): 15 rows" in page


def latest_results_of(result: dict) -> list[dict[str, object]]:
    from obdi.actual_push import expand_align

    return sorted(expand_align(result), key=lambda r: str(r["finished_at"]), reverse=True)


class TestTheEnvelopeAndTheQueue:
    @pytest.fixture
    def store(self, tmp_path):
        with Store(tmp_path / "align.sqlite3") as opened:
            _household(opened)
            yield opened

    def test_AlignEnvelope_CarriesAPushAndAnAuditAndTheScopeAndCeilings(self, store):
        envelope = build_align_envelope(
            store, BOUND_BOTH, {}, scope={"act-main": "all"}, confirmed={"act-main": 4}
        )

        assert envelope["kind"] == "align"
        assert envelope["version"] == 3
        assert envelope["scope"] == {"act-main": "all"}
        assert envelope["confirmed"] == {"act-main": 4}
        assert envelope["history"] == []
        assert envelope["transfers"], "the pair a push would link"
        assert "provision" in envelope
        assert "opening_balances" in envelope

    def test_AlignEnvelope_IncludesABoundAccountThatHoldsNothing(self, store):
        from obdi.replay import ActualAccountBinding

        bound = [*BOUND_BOTH, ActualAccountBinding("household-empty", "act-empty")]

        envelope = build_align_envelope(store, bound, {}, scope={}, confirmed={})

        accounts = envelope["accounts"]
        assert isinstance(accounts, dict)
        assert accounts["act-empty"] == []

    def test_QueueAlign_WritesAnAlignRequestWithTheScopeAndTheMergedBindings(
        self, tmp_path, monkeypatch
    ):
        map_path = tmp_path / "accounts.json"
        map_path.write_text(
            json.dumps(
                {
                    "bindings": [],
                    "actual": [
                        {"canonical_id": MAIN, "actual_account_id": "act-main"},
                        {"canonical_id": POT, "actual_account_id": "act-pot"},
                    ],
                }
            ),
            encoding="utf-8",
        )
        monkeypatch.setenv("ACTUAL_SYNC_ID", "sync-1")
        monkeypatch.setenv("OBDI_ACCOUNT_MAP", str(map_path))
        monkeypatch.setenv("OBDI_ACTUAL_DIR", str(tmp_path / "actual"))
        db = tmp_path / "s.sqlite3"
        with Store(db) as opened:
            _household(opened)

        summary = queue_actual_align(db, scope={"act-main": "explained"}, confirmed={"act-main": 0})

        (request,) = (tmp_path / "actual" / "requests").glob("align-*.json")
        sent = json.loads(request.read_text(encoding="utf-8"))
        assert sent["kind"] == "align"
        assert sent["scope"] == {"act-main": "explained"}
        assert sent["confirmed"] == {"act-main": 0}
        assert sorted(sent["accounts"]) == ["act-main", "act-pot"]
        assert "queued align-" in summary

    def test_QueueAlign_WhenAnEmptyIsPending_RefusesWithTheSentenceAndQueuesNothing(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.setenv("ACTUAL_SYNC_ID", "sync-1")
        monkeypatch.setenv("OBDI_ACCOUNT_MAP", str(tmp_path / "accounts.json"))
        monkeypatch.setenv("OBDI_ACTUAL_DIR", str(tmp_path / "actual"))
        (tmp_path / "accounts.json").write_text(
            json.dumps(
                ONE_ACCOUNT_MAP
            ),
            encoding="utf-8",
        )
        requests = tmp_path / "actual" / "requests"
        requests.mkdir(parents=True)
        (requests / "empty-20261004T110000000000Z.json").write_text("{}", encoding="utf-8")
        db = tmp_path / "s.sqlite3"
        with Store(db) as opened:
            _household(opened)

        with pytest.raises(ValueError, match="empty of Actual is pending"):
            queue_actual_align(db, scope={}, confirmed={})

        assert [q["kind"] for q in queued_requests(tmp_path / "actual")] == ["empty"]

    def test_QueueAlign_WhenAnotherRequestIsQueued_RefusesSoTwoJobsNeverRaceOverOneBudget(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.setenv("ACTUAL_SYNC_ID", "sync-1")
        monkeypatch.setenv("OBDI_ACCOUNT_MAP", str(tmp_path / "accounts.json"))
        monkeypatch.setenv("OBDI_ACTUAL_DIR", str(tmp_path / "actual"))
        (tmp_path / "accounts.json").write_text(
            json.dumps(
                ONE_ACCOUNT_MAP
            ),
            encoding="utf-8",
        )
        requests = tmp_path / "actual" / "requests"
        requests.mkdir(parents=True)
        (requests / "push-20261004T110000000000Z.json").write_text("{}", encoding="utf-8")
        db = tmp_path / "s.sqlite3"
        with Store(db) as opened:
            _household(opened)

        with pytest.raises(ValueError, match="queued or being worked on"):
            queue_actual_align(db, scope={}, confirmed={})

        assert [q["kind"] for q in queued_requests(tmp_path / "actual")] == ["push"]

    def test_QueueAlign_WhenActualIsNotConfigured_RefusesAndQueuesNothing(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.delenv("ACTUAL_SYNC_ID", raising=False)
        monkeypatch.setenv("OBDI_ACTUAL_DIR", str(tmp_path / "actual"))

        with pytest.raises(ValueError, match="not configured"):
            queue_actual_align(tmp_path / "s.sqlite3", scope={}, confirmed={})

        assert not (tmp_path / "actual" / "requests").exists()


def test_Align_IsARegisteredQueueKindWithItsOwnRenderer():
    assert "align" in QUEUE_KINDS
    assert "align" in web._RESULT_ROWS
