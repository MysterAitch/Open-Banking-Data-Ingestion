"""A request in progress says how far it has got, and only calls for a look
at the container when the applier has genuinely gone quiet.

A removal of 4,519 rows ran for twelve minutes with a silent heartbeat, and
the page said both "in progress" and "look at the obdi-applier container".
These tests drive the page from the files the applier writes.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from obdi.actual_push import queue_with_progress
from obdi.web import _actual_rows

REQUEST = "prune-20261002T101000000000Z.json"


def _stamp(moment: datetime) -> str:
    return moment.strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _applier_dir(
    tmp_path: Path,
    *,
    beat_age: timedelta,
    progress: object = None,
    working: bool = True,
    working_on: str = REQUEST,
) -> Path:
    actual = tmp_path / "actual"
    (actual / "requests").mkdir(parents=True)
    (actual / "requests" / REQUEST).write_text("{}", encoding="utf-8")
    now = datetime.now(UTC)
    beat: dict[str, object] = {"at": _stamp(now - beat_age), "working_on": working_on}
    if progress is not None:
        beat["progress"] = progress
    (actual / "heartbeat.json").write_text(json.dumps(beat), encoding="utf-8")
    if working:
        (actual / "processing.json").write_text(
            json.dumps({"name": REQUEST, "started_at": _stamp(now - timedelta(minutes=12))}),
            encoding="utf-8",
        )
    return actual


def _page(actual: Path) -> str:
    heartbeat = json.loads((actual / "heartbeat.json").read_text(encoding="utf-8"))["at"]
    return _actual_rows(
        lambda: [],
        True,
        None,
        lambda: queue_with_progress(actual),
        actual_heartbeat=lambda: heartbeat,
    )


REMOVING = {"phase": "removing", "done": 1200, "total": 4519}


class TestProgressOnThePage:
    def test_RemovalInProgress_WithRecentHeartbeat_SaysHowFarItHasGot(self, tmp_path):
        page = _page(_applier_dir(tmp_path, beat_age=timedelta(seconds=20), progress=REMOVING))

        assert "removed 1,200 of 4,519 rows so far" in page
        assert "in progress" in page

    def test_RemovalInProgress_WithRecentHeartbeat_DoesNotSendYouToTheContainer(self, tmp_path):
        page = _page(_applier_dir(tmp_path, beat_age=timedelta(seconds=20), progress=REMOVING))

        assert "obdi-applier" not in page
        assert 'class="warn"' not in page

    def test_LinkingInProgress_WithRecentHeartbeat_SaysHowManyPairsAreDone(self, tmp_path):
        page = _page(
            _applier_dir(
                tmp_path,
                beat_age=timedelta(seconds=20),
                progress={"phase": "linking", "done": 300, "total": 679},
            )
        )

        assert "300 of 679 transfer pairs so far" in page

    def test_RequestInProgress_WithStaleHeartbeat_KeepsTheContainerAdviceAndDropsStaleProgress(
        self, tmp_path
    ):
        page = _page(_applier_dir(tmp_path, beat_age=timedelta(minutes=10), progress=REMOVING))

        assert "obdi-applier" in page
        assert "so far" not in page

    def test_RequestInProgress_WithRecentHeartbeatButNoProgress_ShowsNoProgressAndNoAdvice(
        self, tmp_path
    ):
        page = _page(_applier_dir(tmp_path, beat_age=timedelta(seconds=20)))

        assert "in progress" in page
        assert "so far" not in page
        assert "obdi-applier" not in page

    def test_QueuedRequestNobodyHasPickedUp_WithStaleHeartbeat_StillWarns(self, tmp_path):
        page = _page(
            _applier_dir(tmp_path, beat_age=timedelta(minutes=10), working=False)
        )

        assert "queued" in page
        assert "obdi-applier" in page

    def test_ProgressRecordedForAnotherRequest_IsNotShownAgainstThisOne(self, tmp_path):
        page = _page(
            _applier_dir(
                tmp_path,
                beat_age=timedelta(seconds=20),
                progress=REMOVING,
                working_on="prune-20260101T000000000000Z.json",
            )
        )

        assert "so far" not in page

    @pytest.mark.parametrize(
        "progress",
        [
            "removed some",
            [1, 2],
            {},
            {"phase": "removing"},
            {"phase": "removing", "done": "1200", "total": 4519},
            {"phase": "removing", "done": 1200.5, "total": 4519},
            {"phase": "removing", "done": True, "total": 4519},
            {"phase": "removing", "done": 5000, "total": 4519},
            {"phase": "removing", "done": -1, "total": 4519},
            {"phase": "removing", "done": 0, "total": 0},
            {"phase": "shredding", "done": 1, "total": 2},
            {"phase": ["removing"], "done": 1, "total": 2},
        ],
    )
    def test_MalformedProgress_DoesNotCrashThePageAndShowsNoProgress(self, tmp_path, progress):
        page = _page(_applier_dir(tmp_path, beat_age=timedelta(seconds=20), progress=progress))

        assert "in progress" in page
        assert "so far" not in page

    def test_ProgressSentence_NamesCountsOnly(self, tmp_path):
        page = _page(_applier_dir(tmp_path, beat_age=timedelta(seconds=20), progress=REMOVING))

        assert "amount" not in page.lower()

    def test_UnreadableHeartbeatFile_DoesNotCrashTheQueue(self, tmp_path):
        actual = _applier_dir(tmp_path, beat_age=timedelta(seconds=20), progress=REMOVING)
        (actual / "heartbeat.json").write_text("{not json", encoding="utf-8")

        entries = queue_with_progress(actual)

        assert entries[0]["name"] == REQUEST
        assert "progress" not in entries[0]
