"""Rebuild-on-deploy: the store knows which code derived it.

The gap this closes: a deploy that changed the matching rules left
stored data derived under the OLD rules until somebody remembered the
rebuild button, and nothing anywhere said so. The mechanism is three
small parts - a whole-package fingerprint, a stamp written only by
successful rebuilds, and a startup comparison - each held by its own
test.
"""

from __future__ import annotations

import json

from obdi import fingerprint
from obdi.providers import starling
from obdi.rebuild import rebuild_from_raw
from obdi.store import Store


def _land_one(store: Store) -> None:
    body = json.dumps(
        {
            "feedItems": [
                {
                    "feedItemUid": "uid-1",
                    "amount": {"currency": "GBP", "minorUnits": 120},
                    "direction": "OUT",
                    "transactionTime": "2026-03-14T09:15:00.000Z",
                    "source": "MASTER_CARD",
                    "status": "SETTLED",
                    "counterPartyName": "Tesco",
                    "reference": "T",
                }
            ]
        }
    ).encode()
    store.land_artefact(
        starling.artefact_for(
            body,
            account_id="starling:cat-1",
            kind="feed",
            origin="https://api.example.com/feed/account/a/category/cat-1?x=1",
        )
    )


class TestTheFingerprintTracksTheWholePackage:
    def test_TheSameTree_HashesTheSame_AndAnyByteChangesIt(self, tmp_path):
        (tmp_path / "a.py").write_text("x = 1\n")
        (tmp_path / "sub").mkdir()
        (tmp_path / "sub" / "b.py").write_text("y = 2\n")
        (tmp_path / "notes.txt").write_text("ignored\n")

        first = fingerprint.code_fingerprint(tmp_path)
        assert fingerprint.code_fingerprint(tmp_path) == first

        (tmp_path / "sub" / "b.py").write_text("y = 3\n")
        assert fingerprint.code_fingerprint(tmp_path) != first

    def test_AMovedFile_ChangesTheFingerprint_BecausePlaceIsPartOfTheCode(
        self, tmp_path
    ):
        (tmp_path / "a.py").write_text("x = 1\n")
        first = fingerprint.code_fingerprint(tmp_path)

        (tmp_path / "a.py").rename(tmp_path / "b.py")
        assert fingerprint.code_fingerprint(tmp_path) != first

    def test_NonPythonFiles_DoNotParticipate(self, tmp_path):
        (tmp_path / "a.py").write_text("x = 1\n")
        first = fingerprint.code_fingerprint(tmp_path)

        (tmp_path / "README.md").write_text("docs change\n")
        assert fingerprint.code_fingerprint(tmp_path) == first


class TestTheStampDecidesWhetherARebuildIsNeeded:
    def test_AFreshStore_NeedsARebuild_BecauseNothingProvedItsRules(
        self, tmp_path
    ):
        with Store(tmp_path / "s.sqlite3") as store:
            assert fingerprint.stored_fingerprint(store) is None
            assert fingerprint.rebuild_needed(store) is True

    def test_AStampMatchingCurrentCode_MeansNoRebuild(self, tmp_path):
        with Store(tmp_path / "s.sqlite3") as store:
            fingerprint.stamp_fingerprint(store, fingerprint.code_fingerprint())
            assert fingerprint.rebuild_needed(store) is False

    def test_AStampFromDifferentCode_MeansRebuild(self, tmp_path):
        with Store(tmp_path / "s.sqlite3") as store:
            fingerprint.stamp_fingerprint(store, "some-older-deploy")
            assert fingerprint.rebuild_needed(store) is True


class TestOnlySuccessfulRebuildsStamp:
    def test_ASuccessfulRebuild_ViaTheCliPath_StampsCurrentCode(
        self, tmp_path, monkeypatch
    ):
        """Drive the real background path end to end and wait for it."""
        import time

        from obdi.cli import rebuild_status_for, start_background_rebuild

        monkeypatch.setenv("OBDI_DB_PATH", str(tmp_path / "s.sqlite3"))
        db = tmp_path / "s.sqlite3"
        with Store(db) as store:
            _land_one(store)

        message = start_background_rebuild(db)
        assert "background" in message

        deadline = time.time() + 30
        while time.time() < deadline:
            status = rebuild_status_for(db)
            if status.get("state") == "done":
                break
            time.sleep(0.1)
        assert status.get("ok") is True, status

        with Store(db) as store:
            assert fingerprint.rebuild_needed(store) is False

    def test_AFailedRebuild_LeavesTheStampAbsent_SoTheNextStartRetries(
        self, tmp_path, monkeypatch
    ):
        with Store(tmp_path / "s.sqlite3") as store:
            _land_one(store)

            def explode(*args, **kwargs):
                raise RuntimeError("deliberate")

            monkeypatch.setattr("obdi.rebuild.reconcile_batch", explode)
            # A reconcile failure PROPAGATES - and that is what the stamp
            # discipline rests on: both CLI paths stamp after the call
            # returns, so an exception skips the stamp and the next
            # startup retries rather than believing the store is current.
            import pytest

            with pytest.raises(RuntimeError):
                rebuild_from_raw(store)
            assert fingerprint.stored_fingerprint(store) is None
