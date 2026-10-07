"""The sync marker, as the owner meets it on the Actual page.

Actual shows no "data as of", so a phone that downloaded a stale budget looks
like one that is current. obdi writes an account whose name is the time of its
last write; the page says which marker it last wrote, which the newest audit
found on the server, and what a device's sidebar ought to show.

Known answers, fixed before the first run (results are newest-first by
finished_at, as the page reads them):

    nothing recorded                          -> "no marker yet", unchecked
    push wrote A at 20:41, no audit           -> A written, server unchecked
    push wrote A at 20:41, audit 20:50 found A -> server has it, no "behind"
    push wrote A at 20:41, audit 20:50 found B -> server is behind, both named
    push wrote A at 20:41, audit 20:50 found none -> server is behind
    push wrote A at 20:41, audit 20:30 found B -> audit predates it: never "behind"
    marker request wrote B at 21:00 after push A at 20:41 -> B is the one written
    push failed at 21:00 after push wrote A   -> A is still the one written
"""

from __future__ import annotations

import json
import re
import threading
from http.server import HTTPServer
from pathlib import Path

import httpx
import pytest

from obdi.cli import queue_actual_marker
from obdi.core.namespaces import QUEUE_KINDS
from obdi.export.actual_push import build_marker_envelope
from obdi.ingest import leases
from obdi.ingest.connections import ConnectionStore
from obdi.pages import web, web_actual
from obdi.pages.web import AuthorisationSession, ConnectionHandler, WebConfig

A = "02 Oct 20:41Z obdi marker"
B = "03 Oct 01:02Z obdi marker"
OLDER = "01 Oct 08:00Z obdi marker"

PURPOSE = (
    "A device that has caught up shows an account with exactly that name in its sidebar"
)


def push(finished: str, marker: str | None = A, ok: bool = True) -> dict[str, object]:
    if not ok:
        return {"ok": False, "request": "x", "finished_at": finished, "error": "boom"}
    result: dict[str, object] = {
        "ok": True,
        "request": "x",
        "finished_at": finished,
        "added": 1,
        "provisioned": 0,
    }
    if marker is not None:
        result["marker"] = {"name": marker, "at": finished, "found": 1, "action": "renamed"}
    return result


def marker_request(finished: str, marker: str) -> dict[str, object]:
    return {
        "ok": True,
        "kind": "marker",
        "request": "m",
        "finished_at": finished,
        "marker": {"name": marker, "at": finished, "found": 1, "action": "renamed"},
    }


def audit(finished: str, *found: str | None, with_marker: bool = True) -> dict[str, object]:
    result: dict[str, object] = {
        "ok": True,
        "kind": "audit",
        "request": "a",
        "finished_at": finished,
        "accounts": [],
    }
    if with_marker:
        names = [name for name in found if name]
        result["marker"] = {
            "found": len(names),
            "name": names[0] if names else None,
            "names": names,
        }
    return result


def summary(*results: dict[str, object]) -> str:
    """The page's step-by-step account of the sync, for these results."""
    return web_actual.chain_block(list(results))


def step(text: str, name: str) -> str:
    """The markup of the one step called `name`."""
    for item in re.findall(r'<li class="step.*?</li>', text, flags=re.S):
        if f"<strong>{name}</strong>" in item:
            return item
    raise AssertionError(f"no step called {name!r}")


class TestTheStepsSayWhatWasWrittenAndWhatTheServerHolds:
    def test_Chain_WithNothingRecorded_SaysThereIsNoMarkerYet(self):
        text = summary()

        assert 'pill-quiet">not yet' in step(text, "Marker written")
        assert "No sync marker write appears" in step(text, "Marker written")
        assert PURPOSE in text

    def test_Chain_AfterAPushThatWroteAMarker_NamesItAndSaysTheServerIsUnchecked(self):
        text = summary(push("2026-10-02T20:41:09.000Z"))

        assert f"<strong>{A}</strong>" in step(text, "Marker written")
        assert "2026-10-02 20:41" in step(text, "Marker written")
        assert "No successful audit has looked" in step(text, "Server has the marker")
        assert "behind" not in text

    def test_Chain_WhenTheNewestAuditFoundTheSameMarker_SaysTheServerHasIt(self):
        text = summary(
            push("2026-10-02T20:41:09.000Z"), audit("2026-10-02T20:50:00.000Z", A)
        )

        assert 'pill-ok">done' in step(text, "Server has the marker")
        assert "found that marker on the server" in step(text, "Server has the marker")
        assert "behind" not in text

    def test_Chain_WhenTheNewestAuditFoundAnOlderMarker_SaysTheServerItselfIsBehind(self):
        text = summary(
            push("2026-10-02T20:41:09.000Z"), audit("2026-10-02T20:50:00.000Z", OLDER)
        )

        server = step(text, "Server has the marker")
        assert 'pill-bad">failed' in server
        assert f"<strong>{A}</strong>" in step(text, "Marker written")
        assert OLDER in server
        assert "the server itself is behind" in server

    def test_Chain_WhenTheNewestAuditFoundNoMarkerAccount_SaysTheServerIsBehind(self):
        text = summary(push("2026-10-02T20:41:09.000Z"), audit("2026-10-02T20:50:00.000Z"))

        server = step(text, "Server has the marker")
        assert 'pill-bad">failed' in server
        assert "found no marker account" in server

    def test_Chain_WhenTheAuditRanBeforeTheMarkerWasWritten_NeverClaimsTheServerIsBehind(self):
        text = summary(
            push("2026-10-02T20:41:09.000Z"), audit("2026-10-02T20:30:00.000Z", OLDER)
        )

        server = step(text, "Server has the marker")
        assert "ran before that marker was written" in server
        assert 'pill-warn">stale' in server
        assert "the server itself is behind" not in text
        assert 'pill-ok">done' not in server

    def test_Chain_WhenTheAuditAndTheWriteShareASecond_TheAuditIsTakenToHaveSeenIt(self):
        text = summary(push("2026-10-02T20:41:09Z"), audit("2026-10-02T20:41:09.000Z", A))

        assert 'pill-ok">done' in step(text, "Server has the marker")

    def test_Chain_WhenAnAuditFoundTwoMarkers_SaysSoAndWhatObdiDoesWithThem(self):
        text = summary(
            push("2026-10-02T20:41:09.000Z"), audit("2026-10-02T20:50:00.000Z", A, OLDER)
        )

        assert "2 marker accounts" in text
        assert "renames the first" in text

    def test_Chain_WithAnAuditFromAnApplierThatReportsNoMarker_SaysItDidNotReportOne(self):
        text = summary(
            push("2026-10-02T20:41:09.000Z"),
            audit("2026-10-02T20:50:00.000Z", with_marker=False),
        )

        assert "did not report a marker" in text
        assert "the server itself is behind" not in text

    def test_Chain_WithAnAuditButNoMarkerEverWritten_ReportsWhatTheAuditFoundAndNothingMore(self):
        text = summary(audit("2026-10-02T20:50:00.000Z", OLDER))

        assert "No sync marker write appears" in step(text, "Marker written")
        assert OLDER in step(text, "Server has the marker")
        assert "the server itself is behind" not in text

    def test_Chain_WhenAMarkerRequestWroteLaterThanAPush_ThatIsTheMarkerWritten(self):
        text = summary(
            push("2026-10-02T20:41:09.000Z"), marker_request("2026-10-03T01:02:00.000Z", B)
        )

        assert f"<strong>{B}</strong>" in text
        assert f"<strong>{A}</strong>" not in text

    def test_Chain_WhenTheNewestPushFailed_TheMarkerWrittenIsStillTheLastSuccessfulOne(self):
        text = summary(
            push("2026-10-02T20:41:09.000Z"), push("2026-10-03T01:02:00.000Z", ok=False)
        )

        assert f"<strong>{A}</strong>" in text
        assert 'pill-bad">failed' in step(text, "Push applied")

    def test_Chain_WithAMalformedMarkerField_IsTreatedAsNoMarkerRatherThanBreakingThePage(self):
        broken: dict[str, object] = {**push("2026-10-02T20:41:09.000Z"), "marker": ["x"]}
        also: dict[str, object] = {
            **push("2026-10-02T20:42:09.000Z"),
            "marker": {"name": 7},
        }

        assert "No sync marker write appears" in summary(broken, also)

    def test_Chain_WithAMarkerNameThatIsMarkup_ShowsItAsTextNotAsMarkup(self):
        text = summary(push("2026-10-02T20:41:09.000Z", marker="<b>x</b> obdi marker"))

        assert "&lt;b&gt;x&lt;/b&gt; obdi marker" in text
        assert "<b>x</b>" not in text

    def test_Chain_StatesWhatTheMarkerIsFor_InTheOwnersTerms(self):
        text = summary(push("2026-10-02T20:41:09.000Z"))

        assert PURPOSE in text
        assert "older stamp, or no marker account, has not received the newest changes" in text

    def test_Chain_ShowsNamesAndTimesOnly_NeverAWordThatIntroducesAFigure(self):
        text = summary(
            push("2026-10-02T20:41:09.000Z"), audit("2026-10-02T20:50:00.000Z", OLDER)
        ).lower()

        assert "amount" not in text
        assert "balance" not in text

    def test_Chain_ListsTheFiveStepsInTheOrderTheyHappen(self):
        text = summary(push("2026-10-02T20:41:09.000Z"))

        names = re.findall(r'<p class="step-head"><strong>(.*?)</strong>', text)
        assert names == [
            "Push applied",
            "Audit",
            "Marker written",
            "Server has the marker",
            "Snapshot refreshed",
        ]
        assert text.index("Marker written") < text.index(PURPOSE)

    def test_Chain_SaysTheZoneOnceAndNotOnEveryTime(self):
        text = summary(push("2026-10-02T20:41:09.000Z"), audit("2026-10-02T20:50:00.000Z", A))

        assert text.count("UTC") == 1
        assert not re.search(r"\d\d:\d\dZ", text.replace(A, ""))


def with_snapshot(
    result: dict[str, object], refreshed: object, error: str | None = None
) -> dict[str, object]:
    snapshot: dict[str, object] = {"refreshed": refreshed, "at": "2026-10-02T21:30:00.000Z"}
    if error is not None:
        snapshot["error"] = error
    return {**result, "snapshot": snapshot}


class TestTheSnapshotStepSaysWhetherAFreshDownloadStartsFromNow:
    """A download is the server's stored file plus every change since it, so a
    stale file means a device replays a growing backlog. The applier says in
    each result whether it refreshed the file (applier/lib.mjs owns why)."""

    def test_Chain_AfterARefresh_SaysTheServerSnapshotWasRefreshedAndWhen(self):
        text = summary(with_snapshot(push("2026-10-02T21:30:05.000Z"), True))

        snapshot = step(text, "Snapshot refreshed")
        assert 'pill-ok">done' in snapshot
        assert "2026-10-02 21:30" in snapshot
        assert "not refreshed" not in text

    def test_Chain_AfterARefusedRefresh_WarnsWithTheReasonAndWhatItMeans(self):
        text = summary(
            with_snapshot(
                push("2026-10-02T21:30:05.000Z"),
                False,
                "the upload was refused (network)",
            )
        )

        snapshot = step(text, "Snapshot refreshed")
        assert 'pill-warn">stale' in snapshot
        assert "the upload was refused (network)" in snapshot
        assert "replays every change since the old snapshot" in snapshot
        assert "phones can fail to finish" in snapshot

    def test_Chain_WhenNoResultSaysWhetherItWasRefreshed_SaysItIsUnknown(self):
        text = summary(push("2026-10-02T21:30:05.000Z"))

        snapshot = step(text, "Snapshot refreshed")
        assert 'pill-quiet">not yet' in snapshot
        assert "predate this check" in snapshot
        assert "not refreshed" not in text

    def test_Chain_WithNoResultsAtAll_SaysItIsUnknown(self):
        assert "predate this check" in step(summary(), "Snapshot refreshed")

    def test_Chain_ReadsTheNewestResultThatSaysSo_WhateverItsKind(self):
        older = with_snapshot(push("2026-10-02T20:00:00.000Z"), False, "network")
        newer = with_snapshot(marker_request("2026-10-02T21:30:00.000Z", B), True)

        text = summary(older, newer)

        assert 'pill-ok">done' in step(text, "Snapshot refreshed")
        assert "not refreshed" not in text

    def test_Chain_ANewerResultThatSaysNothing_DoesNotHideAnOlderOneThatDid(self):
        older = with_snapshot(push("2026-10-02T20:00:00.000Z"), True)
        newer = push("2026-10-02T21:30:00.000Z")

        assert 'pill-ok">done' in step(summary(older, newer), "Snapshot refreshed")

    def test_Chain_AnAuditIsNeverASnapshotRefresh_EvenIfItCarriesTheField(self):
        text = summary(with_snapshot(audit("2026-10-02T21:30:00.000Z", A), True))

        assert "predate this check" in step(text, "Snapshot refreshed")

    def test_Chain_AFailedResultIsNotASnapshotRefresh(self):
        failed = {**push("2026-10-02T21:30:00.000Z", ok=False), "snapshot": {"refreshed": True}}

        assert "predate this check" in step(summary(failed), "Snapshot refreshed")

    @pytest.mark.parametrize(
        "snapshot",
        ["yes", 1, {"refreshed": "true"}, {"at": "2026-10-02T21:30:00Z"}, {"refreshed": None}],
    )
    def test_Chain_WithAMalformedSnapshotField_IsUnknownRatherThanBreakingThePage(
        self, snapshot
    ):
        result = {**push("2026-10-02T21:30:05.000Z"), "snapshot": snapshot}

        assert "predate this check" in step(summary(result), "Snapshot refreshed")

    def test_Chain_WithAReasonThatIsMarkup_ShowsItAsText(self):
        text = summary(
            with_snapshot(push("2026-10-02T21:30:05.000Z"), False, "<script>x</script>")
        )

        assert "&lt;script&gt;x&lt;/script&gt;" in text
        assert "<script>" not in text

    def test_Chain_NamesNoAmountAndNoBalance(self):
        text = summary(with_snapshot(push("2026-10-02T21:30:05.000Z"), False, "network")).lower()

        assert "amount" not in text
        assert "balance" not in text

    def test_Chain_PutsTheSnapshotStepAfterTheMarkerSteps(self):
        text = summary(with_snapshot(push("2026-10-02T21:30:05.000Z"), True))

        assert (
            text.index("Marker written")
            < text.index("Server has the marker")
            < text.index("Snapshot refreshed")
            < text.index(PURPOSE)
        )

    def test_Page_SaysTheMarkerButtonIsAlsoTheOnDemandWayToRefreshTheSnapshot(self):
        rendered = web._actual_rows(lambda: [], True, marker_available=True)

        assert "on-demand way to refresh" in rendered


class TestMarkerResultsHaveARowOfTheirOwn:
    def test_ResultRow_ForAMarkerWrite_SaysWhatWasDone_NotThatAPushWasApplied(self):
        row = web._result_row(marker_request("2026-10-02T20:41:09.000Z", A))

        assert A in row
        assert "renamed" in row
        assert "account(s) provisioned" not in row

    def test_ResultRow_ForAMarkerWriteThatFoundTwo_SaysTheOtherWasLeftAlone(self):
        result = marker_request("2026-10-02T20:41:09.000Z", A)
        result["marker"] = {
            "name": A,
            "found": 2,
            "action": "renamed",
            "note": (
                "2 marker accounts were found; the first was renamed "
                "and 1 other was left alone"
            ),
        }

        assert "1 other was left alone" in web._result_row(result)

    def test_ResultRow_ForAFailedMarkerWrite_ShowsTheReason(self):
        row = web._result_row(
            {
                "ok": False,
                "kind": "marker",
                "finished_at": "2026-10-02T20:41:09Z",
                "error": "the marker did not read back",
            }
        )

        assert "failed" in row
        assert "the marker did not read back" in row

    def test_QueueKinds_IncludeTheMarkerKind(self):
        assert "marker" in QUEUE_KINDS

    def test_QueuedMarker_NamesItsOwnKindWhileItWaits(self):
        rendered = web._actual_rows(
            lambda: [],
            True,
            None,
            lambda: [
                {
                    "name": "marker-20261002T204109000000.json",
                    "kind": "marker",
                    "queued_at": "2026-10-02T20:41:09",
                }
            ],
        )

        assert "queued (marker)" in rendered


class TestTheMarkerRequestIsQueued:
    @pytest.fixture
    def configured(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ACTUAL_SYNC_ID", "sync-1")
        monkeypatch.setenv("OBDI_ACTUAL_DIR", str(tmp_path / "actual"))
        return tmp_path / "never-created.sqlite3"

    def test_Envelope_IsTheMarkerKindAndCarriesNothingElseTheApplierReads(self):
        assert build_marker_envelope() == {"version": 3, "kind": "marker"}

    def test_Queue_WhenConfigured_WritesOneMarkerRequestWithoutOpeningTheStore(
        self, configured, tmp_path
    ):
        summary_line = queue_actual_marker(configured)

        (request,) = (tmp_path / "actual" / "requests").glob("marker-*.json")
        assert json.loads(request.read_text(encoding="utf-8")) == {
            "version": 3,
            "kind": "marker",
        }
        assert request.name in summary_line
        assert not configured.exists(), "a marker reads and moves no store rows"

    def test_Queue_WhenActualIsNotConfigured_QueuesNothing(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ACTUAL_SYNC_ID", "")
        monkeypatch.setenv("OBDI_ACTUAL_DIR", str(tmp_path / "actual"))

        message = queue_actual_marker(tmp_path / "s.sqlite3")

        assert "not configured" in message
        assert not (tmp_path / "actual" / "requests").exists()

    def test_Queue_WhileARebuildIsReplayingTheStore_StillQueues(self, configured, tmp_path):
        # Unlike a push, audit, or prune, a marker reads no store row and moves
        # none: it names the time of a write and nothing about what was written.
        leases.acquire(
            leases.locks_dir(configured), "rebuild-derived", holder="rebuild", ttl_seconds=600
        )

        queue_actual_marker(configured)

        assert len(list((tmp_path / "actual" / "requests").glob("marker-*.json"))) == 1


@pytest.fixture
def serve(tmp_path):
    servers: list[HTTPServer] = []

    def start(results, **hooks) -> str:
        config = WebConfig(
            client_id="c",
            client_secret="tlcs_live_abcdefghij1234567890",
            redirect_uri="https://obdi.example.com/callback",
            connection_store=ConnectionStore(Path(tmp_path) / f"c{len(servers)}.json"),
            actual_status=lambda: results,
            **hooks,
        )
        handler = type(
            "H", (ConnectionHandler,), {"config": config, "session": AuthorisationSession()}
        )
        httpd = HTTPServer(("127.0.0.1", 0), handler)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        servers.append(httpd)
        return f"http://127.0.0.1:{httpd.server_port}"

    yield start
    for httpd in servers:
        httpd.shutdown()


def forms(page: str) -> list[str]:
    return re.findall(r"<form.*?</form>", page, flags=re.S)


def marker_form(page: str) -> str:
    for form in forms(page):
        if 'action="/marker-actual"' in form:
            return form
    raise AssertionError("no marker form on the page")


class TestTheButtonAndTheRoute:
    def test_Page_WithTheHookWired_OffersAPostedButtonThatIsNotThePrimaryAction(self, serve):
        base = serve([], push_actual=lambda: "q", marker_actual=lambda: "queued marker")

        page = httpx.get(f"{base}/actual", timeout=20).text

        form = marker_form(page)
        assert 'method="post"' in form
        assert "Write a sync marker now" in form
        push_form = next(f for f in forms(page) if 'action="/push-actual"' in f)
        assert 'class="button secondary"' in form, "styled as a secondary action, like the audit"
        assert 'class="button"' in push_form, "the push stays the page's primary action"

    def test_Page_WithoutTheHook_OffersNoMarkerButton(self, serve):
        base = serve([], push_actual=lambda: "q")

        page = httpx.get(f"{base}/actual", timeout=20).text

        assert "/marker-actual" not in page

    def test_Page_PutsTheMarkerStepsAfterTheButtonsThatActOnThem(self, serve):
        base = serve(
            [push("2026-10-02T20:41:09.000Z")],
            push_actual=lambda: "q",
            marker_actual=lambda: "queued marker",
        )

        page = httpx.get(f"{base}/actual", timeout=20).text

        assert page.index("Push to Actual now") < page.index("Marker written")
        assert page.index("Write a sync marker now") < page.index("Marker written")

    def test_Post_RunsTheHookOnceAndSaysItIsQueued(self, serve):
        calls: list[int] = []

        def hook() -> str:
            calls.append(1)
            return "queued marker-1.json"

        base = serve([], marker_actual=hook)

        response = httpx.post(f"{base}/marker-actual", timeout=20)

        assert response.status_code == 200
        assert calls == [1]
        assert "queued marker-1.json" in response.text
        assert "reads nothing from the store" in response.text

    def test_Post_WithTheHookUnwired_Is404(self, serve):
        base = serve([])

        assert httpx.post(f"{base}/marker-actual", timeout=20).status_code == 404

    def test_Post_WhenTheHookRaises_Is500AndSaysWhy(self, serve):
        def hook() -> str:
            raise RuntimeError("disk full")

        base = serve([], marker_actual=hook)

        response = httpx.post(f"{base}/marker-actual", timeout=20)

        assert response.status_code == 500
        assert "disk full" in response.text

    def test_Post_FromAnotherSite_IsRefusedAndNothingIsQueued(self, serve):
        calls: list[int] = []
        base = serve([], marker_actual=lambda: calls.append(1) or "q")

        response = httpx.post(
            f"{base}/marker-actual", headers={"Origin": "https://evil.example"}, timeout=20
        )

        assert response.status_code == 403
        assert calls == []

    def test_Get_OfTheRoute_DoesNotQueueAnything(self, serve):
        calls: list[int] = []
        base = serve([], marker_actual=lambda: calls.append(1) or "q")

        httpx.get(f"{base}/marker-actual", timeout=20, follow_redirects=False)

        assert calls == []


class TestEmptyingTheBudgetCountsTheMarkerLikeAnyOtherAccount:
    """An empty is checked against the counts the audit gave, and the audit's
    account list leaves the marker out, so the audit reports it beside the
    list and the plan adds it. Known answer: one bound account holding 4 rows
    and a marker holding 0 -> two accounts told, 4 rows, the marker listed
    plainly as what it is, and not counted among the accounts obdi is not
    bound to."""

    def audit_with_marker(self) -> dict[str, object]:
        return {
            "kind": "audit",
            "ok": True,
            "finished_at": "2026-10-02T20:50:00.000Z",
            "accounts": [
                {
                    "account_id": "act-main",
                    "name": "Current",
                    "missing_account": False,
                    "human": 0,
                    "rows": 4,
                }
            ],
            "marker": {
                "found": 1,
                "name": A,
                "names": [A],
                "accounts": [{"account_id": "act-marker", "name": A, "rows": 0}],
            },
        }

    def test_Plan_WhenTheAuditSawAMarker_TellsTheApplierItsIdAndZeroRows(self):
        from obdi.pages.web_empty import plan_from_audit

        plan, why = plan_from_audit(self.audit_with_marker())

        assert plan is not None, why
        assert plan.shown() == {"act-main": 4, "act-marker": 0}
        assert plan.total_rows == 4

    def test_Section_ListsTheMarkerPlainlyAndDoesNotCallItUnbound(self):
        from obdi.pages.web_empty import empty_section, plan_from_audit

        plan, why = plan_from_audit(self.audit_with_marker())
        section = empty_section(plan, why)

        assert f"{A}: 0 rows" in section
        assert "the sync marker, obdi's own account" in section
        assert "not bound to an obdi account" not in section
        assert "sit in" not in section, "the marker is not an account obdi is unbound from"
        assert 'value="0:act-marker"' in section

    def test_Plan_WhenTheAuditPredatesTheMarkerReport_TellsNothingAboutAMarker(self):
        from obdi.pages.web_empty import plan_from_audit

        audit = self.audit_with_marker()
        del audit["marker"]

        plan, _ = plan_from_audit(audit)

        assert plan is not None
        assert plan.shown() == {"act-main": 4}

    def test_Plan_WithAMalformedMarkerEntry_LeavesItOutRatherThanGuessingACount(self):
        from obdi.pages.web_empty import plan_from_audit

        audit = self.audit_with_marker()
        audit["marker"] = {
            "accounts": [
                {"account_id": "act-marker", "name": A, "rows": "none"},
                "junk",
                {"account_id": "", "name": A, "rows": 0},
                {"account_id": "act-main", "name": A, "rows": 0},
            ]
        }

        plan, _ = plan_from_audit(audit)

        assert plan is not None
        assert plan.shown() == {"act-main": 4}
