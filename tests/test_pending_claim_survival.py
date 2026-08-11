"""An unreadable binding claim is a job half done, not a job finished.

The applier answers a push by minting Actual accounts and writing back the
ids it created. Those ids exist nowhere else: lose the file and the account
sits in the budget with obdi unable to name it, and the next push provisions
a second one beside it. So a claim the merge could not read must stay a
claim - visible, retried, and reported - never renamed as though it had been
folded in.
"""

from __future__ import annotations

import json

from obdi.actual_push import merge_pending_bindings


def _map_with(tmp_path, *canonicals: str):
    map_path = tmp_path / "accounts.json"
    map_path.write_text(
        json.dumps(
            {
                "bindings": [],
                "actual": [
                    {"canonical_id": c, "actual_account_id": f"act-{c}"}
                    for c in canonicals
                ],
            }
        ),
        encoding="utf-8",
    )
    return map_path


class TestUnreadableBindingClaims:
    def test_TruncatedBindingFile_WhenAPushMerges_IsKeptAndReportedNotConsumed(
        self, tmp_path
    ):
        """A write cut short by a container restart: the file names a real
        provisioned account, so the merge must leave it claimable and say
        so rather than filing it under .merged- unread."""
        actual_dir = tmp_path / "actual"
        actual_dir.mkdir()
        map_path = _map_with(tmp_path, "halifax-current")
        (actual_dir / "bindings-pending.json").write_text(
            '[{"canonical_id": "halifax-reward", "actual_acc',
            encoding="utf-8",
        )

        report = merge_pending_bindings(map_path, actual_dir)

        assert not list(actual_dir.glob("bindings-pending.merged-*"))
        retained = list(actual_dir.glob("bindings-pending.merging-*"))
        assert len(retained) == 1
        assert report.unreadable == [retained[0].name]
        assert report.merged == 0
        assert "unreadable claim(s) RETAINED" in report.describe()
        assert retained[0].name in report.describe()

    def test_TruncatedBindingFile_WhenTheNextPushRuns_IsSweptAndReportedAgain(
        self, tmp_path
    ):
        """Retained means retried: the claim is offered to every subsequent
        merge, so repairing the file is all it takes to recover the binding."""
        actual_dir = tmp_path / "actual"
        actual_dir.mkdir()
        map_path = _map_with(tmp_path)
        (actual_dir / "bindings-pending.json").write_text(
            '[{"canonical_id": "halifax-reward", "actual_acc',
            encoding="utf-8",
        )

        first = merge_pending_bindings(map_path, actual_dir)
        second = merge_pending_bindings(map_path, actual_dir)

        assert second.unreadable == first.unreadable
        claim = actual_dir / first.unreadable[0]
        claim.write_text(
            json.dumps(
                [{"canonical_id": "halifax-reward", "actual_account_id": "act-9"}]
            ),
            encoding="utf-8",
        )
        third = merge_pending_bindings(map_path, actual_dir)

        assert third.unreadable == []
        assert third.merged == 1
        stored = json.loads(map_path.read_text(encoding="utf-8"))
        assert {e["canonical_id"] for e in stored["actual"]} == {"halifax-reward"}
        assert list(actual_dir.glob("bindings-pending.merged-*"))

    def test_ReadableClaimBesideAnUnreadableOne_IsMergedAndConsumedNormally(
        self, tmp_path
    ):
        """One bad file must not hold back a good one - and must not ride
        out on the good one's coat-tails either."""
        actual_dir = tmp_path / "actual"
        actual_dir.mkdir()
        map_path = _map_with(tmp_path)
        (actual_dir / "bindings-pending.merging-20260803T000000000000").write_text(
            "{ this was never JSON", encoding="utf-8"
        )
        (actual_dir / "bindings-pending.json").write_text(
            json.dumps([{"canonical_id": "starling-bills", "actual_account_id": "a-2"}]),
            encoding="utf-8",
        )

        report = merge_pending_bindings(map_path, actual_dir)

        assert report.merged == 1
        assert report.unreadable == ["bindings-pending.merging-20260803T000000000000"]
        stored = json.loads(map_path.read_text(encoding="utf-8"))
        assert {e["canonical_id"] for e in stored["actual"]} == {"starling-bills"}
        assert (
            actual_dir / "bindings-pending.merging-20260803T000000000000"
        ).is_file()
        assert len(list(actual_dir.glob("bindings-pending.merged-*"))) == 1

    def test_BindingFileHoldingTheWrongShape_IsKeptRatherThanTreatedAsEmpty(
        self, tmp_path
    ):
        """Valid JSON that is not a list of bindings is just as unread as a
        truncated file - it parsed, but nothing in it was folded in."""
        actual_dir = tmp_path / "actual"
        actual_dir.mkdir()
        map_path = _map_with(tmp_path)
        (actual_dir / "bindings-pending.json").write_text(
            json.dumps({"canonical_id": "halifax-reward"}), encoding="utf-8"
        )

        report = merge_pending_bindings(map_path, actual_dir)

        assert report.merged == 0
        assert len(report.unreadable) == 1
        assert not list(actual_dir.glob("bindings-pending.merged-*"))
        assert list(actual_dir.glob("bindings-pending.merging-*"))


class TestMergeReportCarriesItsDenominator:
    def test_NothingToMerge_ReadsDifferentlyFromAClaimThatCouldNotBeRead(
        self, tmp_path
    ):
        """Zero merged has two very different causes, and the push line must
        distinguish them: silence when there was nothing, a named claim when
        a binding is at risk."""
        actual_dir = tmp_path / "actual"
        actual_dir.mkdir()
        map_path = _map_with(tmp_path)

        quiet = merge_pending_bindings(map_path, actual_dir)

        assert quiet.merged == 0
        assert quiet.offered == 0
        assert quiet.unreadable == []
        assert quiet.describe() == ""

    def test_ClaimHoldingAnUnusableEntry_ReportsMergedAgainstWhatWasOffered(
        self, tmp_path
    ):
        """An entry with no Actual id is offered but not merged; the counts
        show the gap instead of the map quietly gaining one binding."""
        actual_dir = tmp_path / "actual"
        actual_dir.mkdir()
        map_path = _map_with(tmp_path)
        (actual_dir / "bindings-pending.json").write_text(
            json.dumps(
                [
                    {"canonical_id": "halifax-reward", "actual_account_id": "act-9"},
                    {"canonical_id": "halifax-saver"},
                ]
            ),
            encoding="utf-8",
        )

        report = merge_pending_bindings(map_path, actual_dir)

        assert (report.merged, report.offered) == (1, 2)
        assert "merged 1 of 2" in report.describe()
