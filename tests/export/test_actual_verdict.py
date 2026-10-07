"""The one verdict on Actual, and which state wins where several hold.

Every case is built from invented results whose answer was decided before the input
existed, so a disagreement is a fault in the verdict and not a matter of judgement. The
precedence is tested pair by pair: each state against the one below it that it must
outrank, and against the one above it that must outrank it.
"""

from __future__ import annotations

import json

import httpx
import pytest

from actual_states import MARKER, align, audit, push, queued
from obdi.export.actual_verdict import (
    APPLIER_STALE_SECONDS,
    Press,
    State,
    Tone,
    Verdict,
    actual_verdict,
    unreadable_verdict,
)
from test_actual_not_configured import serve

ALIVE = {"applier_seen": "10:39:30", "applier_age_seconds": 30.0}
SILENT = {"applier_seen": "10:02:00", "applier_age_seconds": 2280.0}


def _verdict(*results: dict[str, object], **options: object) -> Verdict:
    return actual_verdict(list(results), **options)  # type: ignore[arg-type]


class TestAgrees:
    def test_Verdict_WhenAuditFollowsPushAndFindsNothing_SaysActualAgreesAndNamesNoPress(self):
        verdict = _verdict(push(10), audit(20))

        assert verdict.state is State.AGREES
        assert verdict.headline == "Actual agrees with obdi"
        assert verdict.press is None
        assert verdict.tone is Tone.OK

    def test_Verdict_WhenAgrees_SaysWhenAndHowManyAccountsAndTransferPairs(self):
        verdict = _verdict(push(10), audit(20, accounts=17, pairs=1434))

        assert "2026-10-04 09:20" in verdict.detail
        assert "17 accounts" in verdict.detail
        assert "1,434 of 1,434 transfer pairs linked" in verdict.detail

    def test_Verdict_WhenOneAccountAndOnePair_UsesTheSingularNotAnOptionalPlural(self):
        verdict = _verdict(push(10), audit(20, accounts=1, pairs=1))

        assert "1 account," in verdict.detail or "1 account;" in verdict.detail
        assert "1 of 1 transfer pair linked" in verdict.detail
        assert "(s)" not in verdict.detail

    def test_Verdict_WhenAuditAndPushFinishedInTheSameInstant_DoesNotCallItAgreement(self):
        verdict = _verdict(push(10), audit(10))

        assert verdict.state is State.UNCHECKED

    def test_Verdict_WhenAuditTimeIsUnreadable_DoesNotCallItAgreement(self):
        broken = audit(20)
        broken["finished_at"] = "last Tuesday"

        verdict = _verdict(push(10), broken)

        assert verdict.state is State.UNCHECKED

    def test_Verdict_WhenResultsArriveOutOfOrder_StillReadsTheNewestOfEach(self):
        verdict = _verdict(audit(20), audit(5, orphaned={"halifax-current-account": 3}), push(10))

        assert verdict.state is State.AGREES


class TestUnchecked:
    def test_Verdict_WhenPushAppliedAfterTheNewestAudit_SaysNotCheckedAndNamesAudit(self):
        verdict = _verdict(audit(20), push(30))

        assert verdict.state is State.UNCHECKED
        assert verdict.headline == "Actual has not been checked since the last push"
        assert verdict.press is Press.AUDIT

    def test_Verdict_WhenNoAuditHasEverRun_SaysSoAndNamesAudit(self):
        verdict = _verdict(push(30))

        assert verdict.state is State.UNCHECKED
        assert "No audit has been run since" in verdict.detail
        assert verdict.press is Press.AUDIT

    def test_Verdict_WhenTheOnlyLaterAuditFailed_IsStillNotChecked(self):
        verdict = _verdict(push(10), audit(20, ok=False))

        assert verdict.state is State.AUDIT_FAILED

    def test_Verdict_WhenTheFailedAuditIsOlderThanThePush_IsNotChecked(self):
        verdict = _verdict(audit(5, ok=False), push(10))

        assert verdict.state is State.UNCHECKED


class TestDiffers:
    def test_Verdict_WhenTheNewestAuditDiffersInOneAccount_NamesTheAccountAndTheRemedy(self):
        verdict = _verdict(push(10), audit(20, orphaned={"halifax-current-account": 3}))

        assert verdict.state is State.DIFFERS
        assert verdict.headline == "Actual differs from obdi in 1 account"
        assert verdict.accounts == ("halifax-current-account",)
        assert "halifax-current-account" in verdict.detail
        assert verdict.press is Press.ALIGN
        assert verdict.tone is Tone.BAD

    def test_Verdict_WhenSeveralAccountsDiffer_CountsThemAndLimitsTheNamesItListsInTheSentence(
        self,
    ):
        names = (
            "halifax-current-account",
            "halifax-instant-saver",
            "halifax-regular-saver",
            "halifax-credit-card",
            "starling-main",
            "starling-joint",
        )
        orphans = dict.fromkeys(names, 1)

        verdict = _verdict(push(10), audit(20, orphaned=orphans))

        assert verdict.headline == "Actual differs from obdi in 6 accounts"
        assert len(verdict.accounts) == 6
        assert "and 2 more" in verdict.detail

    def test_Verdict_WhenADifferenceIsOfAKindBringingIntoLineCannotClear_NamesNoPress(self):
        stuck = audit(20)
        stuck["accounts"][0]["diverged"] = 2  # type: ignore[index]

        verdict = _verdict(push(10), stuck)

        assert verdict.state is State.DIFFERS
        assert verdict.press is None
        assert "handles some of these" in verdict.detail

    def test_Verdict_WhenTheOnlyDifferenceIsABalanceWithNoRowDifference_NamesNoPress(self):
        stuck = audit(20)
        stuck["accounts"][0]["balance"] = {"agrees": False}  # type: ignore[index]

        verdict = _verdict(push(10), stuck)

        assert verdict.state is State.DIFFERS
        assert verdict.press is None

    def test_Verdict_WhenTransferPairsAreUnlinked_BringingIntoLineIsNamed(self):
        verdict = _verdict(push(10), audit(20, unlinked=2))

        assert verdict.state is State.DIFFERS
        assert verdict.press is Press.ALIGN

    def test_Verdict_WhenDifferencesRemainAfterAPushThatCameAfterTheAudit_IsNotCheckedInstead(self):
        verdict = _verdict(audit(20, orphaned={"halifax-current-account": 3}), push(30))

        assert verdict.state is State.UNCHECKED


class TestPushFailed:
    def test_Verdict_WhenTheNewestPushFailed_SaysItFailedWithTheReasonAndNamesPush(self):
        verdict = _verdict(push(10), audit(15), push(30, ok=False, error="Actual did not answer"))

        assert verdict.state is State.PUSH_FAILED
        assert verdict.headline == "The last push failed"
        assert "Actual did not answer" in verdict.detail
        assert verdict.press is Press.PUSH

    def test_Verdict_WhenTheNewestPushWasRefused_SaysRefusedNotFailed(self):
        verdict = _verdict(
            push(10), push(30, ok=False, refused=True, error="two rows share an imported id")
        )

        assert verdict.headline == "The last push was refused"
        assert "two rows share an imported id" in verdict.detail

    def test_Verdict_WhenAnAuditRanAfterTheFailedPush_SaysWhatTheAuditFound(self):
        verdict = _verdict(push(10), push(20, ok=False), audit(30))

        assert verdict.state is State.AGREES

    def test_Verdict_WhenNoPushHasEverApplied_AndOneFailed_SaysFailedAndThatNoneApplied(self):
        verdict = _verdict(push(20, ok=False))

        assert verdict.state is State.PUSH_FAILED
        assert "No push has ever applied" in verdict.detail


class TestAlignStopped:
    def test_Verdict_WhenAnAlignmentStoppedAfterTheAudit_SaysSoAndNamesAudit(self):
        verdict = _verdict(push(10), audit(20), align(30, complete=False, stopped="a refusal"))

        assert verdict.state is State.ALIGN_STOPPED
        assert "a refusal" in verdict.detail
        assert verdict.press is Press.AUDIT

    def test_Verdict_WhenAnAuditFollowedTheStoppedAlignment_ReadsTheAuditInstead(self):
        verdict = _verdict(push(10), align(20, complete=False), audit(30))

        assert verdict.state is State.AGREES

    def test_Verdict_WhenAnAlignmentFailedOutright_GivesTheErrorTheApplierRecorded(self):
        verdict = _verdict(push(10), audit(20), align(30, ok=False))

        assert verdict.state is State.ALIGN_STOPPED
        assert "could not reach Actual" in verdict.detail


class TestNothingPushed:
    def test_Verdict_WhenThereAreNoResults_SaysNothingHasBeenPushedAndNamesPush(self):
        verdict = _verdict()

        assert verdict.state is State.NOTHING_PUSHED
        assert verdict.headline == "Nothing has been pushed yet"
        assert verdict.press is Press.PUSH

    def test_Verdict_WhenOnlyAnAuditExists_StillSaysNothingHasBeenPushed(self):
        verdict = _verdict(audit(5))

        assert verdict.state is State.NOTHING_PUSHED


class TestQueue:
    def test_Verdict_WhenARequestWaitsAndTheApplierIsChecking_SaysItIsWaiting(self):
        verdict = _verdict(push(10), audit(20), queue=[queued("audit")], **ALIVE)

        assert verdict.state is State.REQUEST_WAITING
        assert verdict.headline == "A request is waiting for the applier"
        assert "audit" in verdict.detail
        assert verdict.press is None

    @pytest.mark.parametrize(
        ("kind", "opening"),
        [
            ("push", "A push is queued;"),
            ("audit", "An audit is queued;"),
            ("marker", "A sync marker is queued;"),
            ("align", "A request to bring Actual into line is queued;"),
            ("prune", "A removal of orphaned imports is queued;"),
            ("empty", "An emptying of Actual is queued;"),
            ("something-new", "A request of an unknown kind is queued;"),
        ],
    )
    def test_Verdict_WhenOneRequestWaits_NamesItWithTheArticleItsNameTakes(self, kind, opening):
        verdict = _verdict(push(10), audit(20), queue=[queued(kind)], **ALIVE)

        assert verdict.detail.startswith(opening)

    def test_Verdict_WhenOneRequestIsRunning_NamesItWithTheArticleItsNameTakes(self):
        verdict = _verdict(push(10), queue=[queued("audit", running=True)], **ALIVE)

        assert verdict.detail.startswith("An audit is running;")

    def test_Verdict_WhenSeveralRequestsWait_CountsThemAndNamesEachKindWithoutAnArticle(self):
        verdict = _verdict(
            push(10), audit(20), queue=[queued("push"), queued("audit")], **ALIVE
        )

        assert verdict.detail.startswith("2 requests (push, audit) are queued;")

    def test_Verdict_WhenARequestIsRunningAndTheApplierIsChecking_SaysTheApplierIsWorking(self):
        verdict = _verdict(push(10), queue=[queued("push", running=True)], **ALIVE)

        assert verdict.state is State.REQUEST_RUNNING
        assert verdict.headline == "The applier is working on a request"

    def test_Verdict_WhenARequestWaitsAndTheApplierHasGoneQuiet_NamesWhenItLastChecked(self):
        verdict = _verdict(push(10), queue=[queued("push")], **SILENT)

        assert verdict.state is State.APPLIER_SILENT
        assert verdict.headline == "The applier has not checked the queue since 10:02:00"
        assert "obdi-applier" in verdict.detail
        assert verdict.tone is Tone.BAD

    def test_Verdict_WhenARequestWaitsAndTheApplierHasNeverChecked_SaysNever(self):
        verdict = _verdict(push(10), queue=[queued("push")])

        assert verdict.headline == "The applier has never checked the queue"

    def test_Verdict_WhenTheQueueIsEmptyAndTheApplierIsSilent_SaysNothingAboutTheApplier(self):
        verdict = _verdict(push(10), audit(20), queue=[], **SILENT)

        assert verdict.state is State.AGREES

    def test_Verdict_AtTheStalenessThreshold_ThePulseIsStillCountedAsBeating(self):
        beat = {"applier_seen": "10:38:00", "applier_age_seconds": float(APPLIER_STALE_SECONDS)}

        at = _verdict(push(10), queue=[queued("push")], **beat)
        over = _verdict(
            push(10),
            queue=[queued("push")],
            applier_seen="10:38:00",
            applier_age_seconds=APPLIER_STALE_SECONDS + 0.5,
        )

        assert at.state is State.REQUEST_WAITING
        assert over.state is State.APPLIER_SILENT

    def test_Verdict_WhenSeveralRequestsWait_CountsThemAndNamesTheirKinds(self):
        verdict = _verdict(queue=[queued("push"), queued("marker")], **ALIVE)

        assert "2 requests" in verdict.detail
        assert "sync marker" in verdict.detail


class TestNotConfigured:
    def test_Verdict_WhenNotConfigured_SaysSoAndNamesNoPress(self):
        verdict = _verdict(configured=False)

        assert verdict.state is State.NOT_CONFIGURED
        assert verdict.headline == "Actual is not configured on this instance"
        assert verdict.press is None

    def test_Verdict_WhenConfigured_DoesNotSayItIsNot(self):
        assert _verdict().state is not State.NOT_CONFIGURED


class TestResultsUnreadable:
    def test_Verdict_WhenTheResultsCouldNotBeRead_SaysItCannotTell(self):
        verdict = unreadable_verdict()

        assert verdict.state is State.UNREADABLE
        assert "could not be read" in verdict.headline
        assert verdict.press is None


class TestPrecedence:
    """Each state outranks the one beneath it where both hold. Built as one story that holds
    them all and read with the higher state's cause removed, one rung at a time."""

    def test_Verdict_NotConfiguredOutranksAQueuedRequest(self):
        verdict = _verdict(push(10), queue=[queued()], configured=False, **SILENT)

        assert verdict.state is State.NOT_CONFIGURED

    def test_Verdict_ASilentApplierOutranksARequestThatIsWaiting(self):
        quiet = _verdict(push(10), queue=[queued()], **SILENT)
        beating = _verdict(push(10), queue=[queued()], **ALIVE)

        assert (quiet.state, beating.state) == (State.APPLIER_SILENT, State.REQUEST_WAITING)

    def test_Verdict_AWaitingRequestOutranksAFailedPush(self):
        failed = push(30, ok=False)

        waiting = _verdict(push(10), failed, queue=[queued()], **ALIVE)
        idle = _verdict(push(10), failed)

        assert (waiting.state, idle.state) == (State.REQUEST_WAITING, State.PUSH_FAILED)

    def test_Verdict_AWaitingRequestOutranksAnAuditThatIsOlderThanThePush(self):
        waiting = _verdict(audit(5), push(30), queue=[queued()], **ALIVE)
        idle = _verdict(audit(5), push(30))

        assert (waiting.state, idle.state) == (State.REQUEST_WAITING, State.UNCHECKED)

    def test_Verdict_AFailedPushOutranksAnAuditThatIsOlderThanIt(self):
        failed_after = _verdict(push(10), audit(20), push(30, ok=False))
        applied_after = _verdict(push(10), audit(20), push(30))

        assert (failed_after.state, applied_after.state) == (State.PUSH_FAILED, State.UNCHECKED)

    def test_Verdict_AFailedPushOutranksADifferingAuditThatIsOlderThanIt(self):
        old = audit(20, orphaned={"halifax-current-account": 3})

        verdict = _verdict(push(10), old, push(30, ok=False))

        assert verdict.state is State.PUSH_FAILED

    def test_Verdict_AFailedPushOutranksAStoppedAlignment(self):
        verdict = _verdict(push(10), audit(15), align(20, complete=False), push(30, ok=False))

        assert verdict.state is State.PUSH_FAILED

    def test_Verdict_AStoppedAlignmentOutranksAnAuditThatIsOlderThanIt(self):
        stopped = _verdict(push(10), audit(20), align(30, complete=False))
        finished = _verdict(push(10), audit(20), align(30))

        # A finished alignment adds nothing of its own: its pushes and audits are results
        # beside it, so it leaves the verdict to them.
        assert (stopped.state, finished.state) == (State.ALIGN_STOPPED, State.AGREES)

    def test_Verdict_AStoppedAlignmentOutranksAFailedAudit(self):
        verdict = _verdict(push(10), align(20, complete=False), audit(15, ok=False))

        assert verdict.state is State.ALIGN_STOPPED

    def test_Verdict_AFailedAuditOutranksTheDifferencesAnEarlierAuditFound(self):
        earlier = audit(15, orphaned={"halifax-current-account": 3})

        verdict = _verdict(push(10), earlier, audit(20, ok=False))

        assert verdict.state is State.AUDIT_FAILED

    def test_Verdict_NothingPushedOutranksAFailedAudit(self):
        verdict = _verdict(audit(20, ok=False))

        assert verdict.state is State.NOTHING_PUSHED

    def test_Verdict_NotCheckedSinceThePushOutranksTheDifferencesAnEarlierAuditFound(self):
        verdict = _verdict(audit(20, orphaned={"halifax-current-account": 3}), push(30))

        assert verdict.state is State.UNCHECKED

    def test_Verdict_DifferencesOutrankAgreementWhenAnyAccountDiffers(self):
        verdict = _verdict(push(10), audit(20, orphaned={"monzo-main": 1}))

        assert verdict.state is State.DIFFERS

    def test_Verdict_TheMarkerBeingStale_DoesNotMakeActualDisagree(self):
        verdict = _verdict(push(10, marker_name=MARKER), audit(20))

        assert verdict.state is State.AGREES


class TestTheResultsTheLivePageReads:
    """The page reads results from the applier's directory through the web configuration. A
    handful of audits after the one push must not push that push out of what it reads, or the
    page says nothing has been pushed while Actual is full."""

    def _page(self, tmp_path, monkeypatch, results: list[dict[str, object]]) -> str:
        directory = tmp_path / "actual" / "results"
        directory.mkdir(parents=True)
        for number, result in enumerate(results):
            (directory / f"{number:03d}.json").write_text(json.dumps(result), encoding="utf-8")
        httpd, base, _, _ = serve(tmp_path, monkeypatch, configured=True, bound=True)
        try:
            return httpx.get(f"{base}/actual", timeout=60).text
        finally:
            httpd.shutdown()

    def test_Page_WhenSixAuditsFollowTheOnlyPush_StillSaysActualAgrees(self, tmp_path, monkeypatch):
        results = [push(10, marker_name=MARKER)] + [audit(20 + n) for n in range(6)]

        page = self._page(tmp_path, monkeypatch, results)

        assert 'data-state="agrees"' in page
        assert "Nothing has been pushed yet" not in page

    def test_Page_WhenNoResultsExist_SaysNothingHasBeenPushed(self, tmp_path, monkeypatch):
        page = self._page(tmp_path, monkeypatch, [])

        assert 'data-state="nothing-pushed"' in page
