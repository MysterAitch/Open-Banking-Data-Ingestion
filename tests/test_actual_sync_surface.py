"""What the Actual sync surface tells a person who is standing in front
of it with a phone.

Every test here is about a page saying LESS than it knows: evidence the
applier computed and shipped and the page dropped, a count with no
denominator, a kind rendered as another kind's outcome, and a banner
answering "is a rebuild in flight" from a different source than the
buttons that refuse for that reason.
"""

import threading
from http.server import HTTPServer

import httpx

from obdi import web
from obdi.connections import ConnectionStore
from obdi.namespaces import QUEUE_KINDS
from obdi.web import AuthorisationSession, ConnectionHandler, WebConfig


def _serve(config: WebConfig, path: str) -> str:
    handler = type(
        "H", (ConnectionHandler,), {"config": config, "session": AuthorisationSession()}
    )
    httpd = HTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        return httpx.get(f"http://127.0.0.1:{httpd.server_port}{path}").text
    finally:
        httpd.shutdown()


def _config(tmp_path, **hooks) -> WebConfig:
    return WebConfig(
        client_id="client-1",
        client_secret="tlcs_live_abcdefghij1234567890",
        redirect_uri="https://obdi.example.com/callback",
        connection_store=ConnectionStore(tmp_path / "c.json"),
        **hooks,
    )


AUDIT_WITH_SAMPLES = {
    "ok": True,
    "kind": "audit",
    "finished_at": "2026-08-09T13:00:00Z",
    "accounts": [
        {
            "account_id": "act-2",
            "name": "halifax-instant-saver",
            "expected": 947,
            "present": 910,
            "human": 2,
            "missing": 37,
            "orphaned": 1,
            "diverged": 1,
            "duplicated": 1,
            "missing_sample": ["ck-aaa:0", "ck-bbb:0"],
            "orphaned_sample": [
                {"imported_id": "ck-ccc:0", "date": "2026-07-02", "amount": -1234}
            ],
            "diverged_sample": [
                {
                    "imported_id": "ck-ddd:0",
                    "actual": {"date": "2026-07-03", "amount": -500},
                    "store": {"date": "2026-07-03", "amount": -550},
                }
            ],
            "duplicated_sample": [{"imported_id": "ck-eee:0", "copies": 2}],
        }
    ],
}


class TestAuditEvidence:
    """An audit that says "missing 37" and nothing else cannot be acted
    on without a shell - and the applier already caps and ships the rows
    behind every count."""

    def test_AuditReport_WhenRowsAreMissing_NamesTheSampledRowsNotJustTheCount(self):
        rendered = web._actual_rows(lambda: [AUDIT_WITH_SAMPLES], True)

        assert "ck-aaa:0" in rendered
        assert "ck-bbb:0" in rendered
        # The sample is capped upstream, so it carries what it was drawn from.
        assert "missing - showing 2 of 37" in rendered

    def test_AuditReport_EverySampleTheApplierShips_ReachesThePage(self):
        """Read from the result rather than from a list of the sample
        categories known today: a category the applier adds must arrive
        on the page rather than being dropped in silence."""
        account = dict(AUDIT_WITH_SAMPLES["accounts"][0])
        account["wrong_sign"] = 3
        account["wrong_sign_sample"] = [{"imported_id": "ck-fff:0", "amount": 700}]
        rendered = web._actual_rows(
            lambda: [{**AUDIT_WITH_SAMPLES, "accounts": [account]}], True
        )

        sampled_ids = []
        for key, value in account.items():
            if not key.endswith("_sample"):
                continue
            for item in value:
                sampled_ids.append(
                    item["imported_id"] if isinstance(item, dict) else item
                )
        assert sampled_ids
        for imported_id in sampled_ids:
            assert imported_id in rendered, f"{imported_id} was computed and dropped"

    def test_AuditReport_SampledAmounts_ReadAsMoneyNotAsMinorUnits(self):
        rendered = web._actual_rows(lambda: [AUDIT_WITH_SAMPLES], True)

        assert "12.34" in rendered
        assert "-1234" not in rendered

    def test_AuditVerdict_WithADifferenceCategoryThePageDoesNotKnow_IsNotClean(self):
        """The applier chooses the category names on its own side of a
        file boundary. One this page has never heard of must read as a
        difference to look at, never as a clean audit."""
        rendered = web._actual_rows(
            lambda: [
                {
                    "ok": True,
                    "kind": "audit",
                    "finished_at": "2026-08-09T13:00:00Z",
                    "accounts": [
                        {
                            "account_id": "act-1",
                            "name": "halifax-current-account",
                            "expected": 100,
                            "present": 100,
                            "human": 0,
                            "wrong_sign": 37,
                        }
                    ],
                }
            ],
            True,
        )

        assert "audit clean" not in rendered
        assert "audit: differences" in rendered
        assert "wrong_sign 37" in rendered

    def test_AuditVerdict_WithNothingWrong_StillReadsAsClean(self):
        rendered = web._actual_rows(
            lambda: [
                {
                    "ok": True,
                    "kind": "audit",
                    "finished_at": "2026-08-09T13:00:00Z",
                    "accounts": [
                        {
                            "account_id": "act-1",
                            "name": "halifax-current-account",
                            "expected": 947,
                            "present": 947,
                            "human": 12,
                            "missing": 0,
                            "orphaned": 0,
                            "diverged": 0,
                            "duplicated": 0,
                        }
                    ],
                }
            ],
            True,
        )

        assert "audit clean" in rendered


class TestResultKinds:
    """A result file names its own kind. The page must render the one it
    names, or say that it cannot."""

    def _result_for(self, kind: str) -> dict[str, object]:
        base: dict[str, object] = {
            "ok": True,
            "kind": kind,
            "finished_at": "2026-08-09T09:00:00Z",
        }
        if kind == "push":
            return {**base, "added": 5, "provisioned": 1}
        return {
            **base,
            "accounts": [
                {
                    "account_id": "act-1",
                    "name": "halifax-current-account",
                    "expected": 5,
                    "present": 5,
                    "human": 0,
                    "removed": 4,
                }
            ],
        }

    def test_ResultRow_ForEveryQueueKind_HasARendererOfItsOwn(self):
        """Every kind the applier is allowed to emit is a kind the page
        can read. Bound to the registry so a kind added there cannot
        quietly borrow another kind's renderer."""
        unrendered = QUEUE_KINDS - set(web._RESULT_ROWS)
        assert not unrendered, f"no result renderer for {sorted(unrendered)}"

        rendered = {
            kind: web._result_row(self._result_for(kind)) for kind in QUEUE_KINDS
        }
        assert len(set(rendered.values())) == len(QUEUE_KINDS)

    def test_ResultRow_ForAPrune_IsNotReportedAsASuccessfulPush(self):
        rendered = web._result_row(self._result_for("prune"))

        assert "removed" in rendered
        assert "account(s) provisioned" not in rendered

    def test_ResultRow_WithAKindThisBuildCannotRender_SaysSoAndShowsIt(self):
        """The applier and the page are deployed separately. A result
        this build has no renderer for must not be announced as an
        applied push - the fallback that did exactly that."""
        rendered = web._result_row(
            {
                "kind": "reconcile",
                "ok": True,
                "finished_at": "2026-08-09T09:00:00Z",
                "removed": 42,
            }
        )

        assert "unknown result kind: reconcile" in rendered
        assert "applied" not in rendered
        # The outcome is still worth reading by eye, so it is shown.
        assert "removed 42" in rendered

    def test_ResultRow_WithNoKindAtAll_IsAPush(self):
        """Push results predate the kind field; the ones on disk have no
        kind and are still pushes."""
        rendered = web._result_row(
            {"ok": True, "added": 3, "provisioned": 0, "finished_at": "2026-08-09T09:00:00Z"}
        )

        assert "applied" in rendered
        assert "3 added" in rendered


class TestQueuedRequests:
    def test_QueuedRequest_ForEveryQueueKind_NamesItsOwnKind(self):
        """A prune deletes rows in Actual and a push adds them. While
        they wait, the pill must say which is which."""
        for kind in sorted(QUEUE_KINDS):
            rendered = web._actual_rows(
                lambda: [],
                True,
                None,
                lambda kind=kind: [
                    {
                        "name": f"{kind}-20260809T130000000000.json",
                        "kind": kind,
                        "queued_at": "2026-08-09T13:00:00",
                    }
                ],
            )

            assert f"queued ({kind})" in rendered, f"{kind} does not name itself"

    def test_QueuedRequest_WithAKindTheRegistryDoesNotHold_IsFlaggedNotAssumed(self):
        rendered = web._actual_rows(
            lambda: [],
            True,
            None,
            lambda: [
                {
                    "name": "reconcile-20260809T130000000000.json",
                    "kind": "reconcile",
                    "queued_at": "2026-08-09T13:00:00",
                }
            ],
        )

        assert "unknown kind: reconcile" in rendered


class TestSyncHistoryCompleteness:
    """The history page reads a capped directory and skips files it
    cannot parse. Both must show in the count it prints."""

    def _results(self, count: int) -> list[dict[str, object]]:
        return [
            {
                "ok": True,
                "added": n,
                "provisioned": 0,
                "finished_at": f"2026-08-09T{n:02}:00:00Z",
            }
            for n in range(count)
        ]

    def test_HistoryPage_WhenTheRecordIsCapped_SaysHowManyItIsShowingOfHowMany(
        self, tmp_path
    ):
        page = _serve(
            _config(
                tmp_path,
                actual_history=lambda: {
                    "results": self._results(3),
                    "total": 205,
                    "unreadable": [],
                },
            ),
            "/actual-history",
        )

        assert "showing 3 of 205 result(s)" in page

    def test_HistoryPage_WhenAResultFileIsUnreadable_NamesItRatherThanSkippingQuietly(
        self, tmp_path
    ):
        page = _serve(
            _config(
                tmp_path,
                actual_history=lambda: {
                    "results": self._results(2),
                    "total": 3,
                    "unreadable": ["push-20260809T090000000000.json"],
                },
            ),
            "/actual-history",
        )

        assert "1 result file(s) could not be read" in page
        assert "push-20260809T090000000000.json" in page

    def test_HistoryPage_WhenTheHookCannotCount_DoesNotClaimToBeComplete(
        self, tmp_path
    ):
        page = _serve(
            _config(tmp_path, actual_history=lambda: self._results(12)),
            "/actual-history",
        )

        assert "Every recorded outcome" not in page
        assert "12 result(s)" in page
        assert "reports no total" in page

    def test_HistoryPage_WithNothingRecordedAtAll_SaysSo(self, tmp_path):
        page = _serve(_config(tmp_path, actual_history=lambda: []), "/actual-history")

        assert "Nothing recorded yet." in page


class TestRebuildBanner:
    """The banner and the buttons must answer "is a rebuild in flight"
    from the same authority. When they disagree, the page presents a
    half-populated store as the truth while every action on it is
    refused."""

    def test_Index_WhileTheRebuildLeaseIsHeld_BannersTheMidReplayStore(self, tmp_path):
        from obdi import leases
        from obdi.cli import rebuild_in_progress_note

        db_path = tmp_path / "obdi.sqlite3"
        leases.acquire(
            leases.locks_dir(db_path), "rebuild-derived", holder="rebuild", ttl_seconds=600
        )
        # No rebuild-status.json: the window between taking the lease and
        # the first status write is a real one, and the store is being
        # replayed throughout it.
        assert not (tmp_path / "rebuild-status.json").exists()

        page = web.render_index(
            ConnectionStore(tmp_path / "c.json"),
            rebuild_status=lambda: {},
            rebuild_busy_note=lambda: rebuild_in_progress_note(db_path),
        ).decode()

        assert "a rebuild is replaying the store" in page

    def test_Index_WhenARebuildDiedMidReplay_KeepsWarningUntilItIsRerun(self, tmp_path):
        from obdi.cli import rebuild_in_progress_note

        db_path = tmp_path / "obdi.sqlite3"
        (tmp_path / "rebuild-status.json").write_text(
            '{"state": "running", "started_at": "2026-08-09T08:00:00Z"}',
            encoding="utf-8",
        )

        page = web.render_index(
            ConnectionStore(tmp_path / "c.json"),
            # The status hook answering nothing is the case the file-only
            # banner had no answer for: the gates still refuse.
            rebuild_status=lambda: {},
            rebuild_busy_note=lambda: rebuild_in_progress_note(db_path),
        ).decode()

        assert "did not finish" in page

    def test_Index_WithNoRebuildInFlight_CarriesNoBanner(self, tmp_path):
        from obdi.cli import rebuild_in_progress_note

        db_path = tmp_path / "obdi.sqlite3"

        page = web.render_index(
            ConnectionStore(tmp_path / "c.json"),
            rebuild_status=lambda: {},
            rebuild_busy_note=lambda: rebuild_in_progress_note(db_path),
        ).decode()

        assert "rebuild is replaying" not in page
        assert "did not finish" not in page
