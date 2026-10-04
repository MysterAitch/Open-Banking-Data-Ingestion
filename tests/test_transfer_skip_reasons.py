"""A push says why it skipped a transfer pair, and whether another push could ever link it.

On the live instance a push reported "transfers: 0 linked, 1427 already linked, 4 skipped, 0 failed"
twice running, and an audit showed four pairs between two accounts present as rows but not linked.
Nothing said why, and the audit's "the next push links what it can" was untrue of all four:
every reason the applier can give except an absent leg needs a change in Actual first.

The applier's own side is tested in `applier/lib.test.mjs`; these scenarios read what the page makes
of a result that names its skipped pairs.
"""

from __future__ import annotations

from obdi.web import _result_row

AMOUNT_FORMS = ("871.23", "87123")


def _result(skipped_pairs, skipped=None, **extra):
    return {
        "kind": "push",
        "ok": True,
        "finished_at": "2026-10-04T05:00:00Z",
        "added": 0,
        "provisioned": 0,
        "transfers": {
            "pairs": 1431,
            "linked": 0,
            "already_linked": 1427,
            "skipped": skipped if skipped is not None else {"reconciled": len(skipped_pairs)},
            "skipped_pairs": skipped_pairs,
            "failed": 0,
            **extra,
        },
    }


def _pair(reason, debit="main-current", credit="bills-space", date="2026-09-02"):
    return {"debit_account": debit, "credit_account": credit, "date": date, "reason": reason}


class TestAPushCountsAndNamesWhatItRelinked:
    def test_Row_WhenPairsWereRelinked_TheResultLineCountsThemBesideLinked(self):
        row = _result_row(_result([], {}, linked=3, relinked=2))

        assert "transfers: 3 linked, 2 re-linked, 1427 already linked, 0 skipped, 0 failed" in row

    def test_Row_WhenTheApplierPredatesRelinking_NoReLinkedCountIsClaimed(self):
        row = _result_row(_result([], {}, linked=3))

        assert "transfers: 3 linked, 1427 already linked" in row
        assert "re-linked" not in row

    def test_Row_WhenPairsWereRelinked_NamesAccountsAndDate(self):
        named = [
            {
                "debit_account": "starling-personal",
                "credit_account": "starling-space-money",
                "date": "2026-09-02",
            }
        ]
        row = _result_row(_result([], {}, relinked=1, relinked_pairs=named))

        assert "1 transfer pair re-linked" in row
        assert "starling-personal to starling-space-money, 2026-09-02" in row

    def test_Row_WhenMoreWereRelinkedThanTheResultNames_SaysHowManyAreNotListed(self):
        named = [{"debit_account": "a", "credit_account": "b", "date": "2026-09-02"}] * 50
        row = _result_row(_result([], {}, relinked=60, relinked_pairs=named))

        assert "60 transfer pairs re-linked" in row
        assert "and 10 more, not listed" in row

    def test_Row_WhenNothingWasRelinked_ShowsNoReLinkBlock(self):
        row = _result_row(_result([], {}, relinked=0, relinked_pairs=[]))

        assert "pairs re-linked" not in row
        assert "pair re-linked" not in row

    def test_Row_WhenARelinkedRecordCarriesMarkupOrAnAmount_ItIsEscapedAndNoFigureIsShown(self):
        named = [
            {
                "debit_account": "<script>x</script>",
                "credit_account": "b",
                "date": "2026-09-02",
                "amount": 87123,
            }
        ]
        row = _result_row(_result([], {}, relinked=1, relinked_pairs=named))

        assert "<script>" not in row
        for form in AMOUNT_FORMS:
            assert form not in row


class TestAPushNamesWhatItSkipped:
    def test_Row_WhenALegIsLinkedToARowNotReleased_DoesNotSayToUnlinkWhatThePushNowHandles(self):
        row = _result_row(_result([_pair("linked_elsewhere")], {"linked_elsewhere": 1}))

        assert "unlink it in Actual, then push" not in row
        assert "not an obdi import" in row
        assert "a later push will not link these" in row

    def test_Row_WhenPairsWereSkippedForAStuckReason_NamesAccountsDateAndReason(self):
        row = _result_row(_result([_pair("reconciled")] * 4))

        assert "4 transfer pairs skipped" in row
        assert "main-current to bills-space, 2026-09-02" in row
        assert "a leg is reconciled in Actual" in row

    def test_Row_WhenEveryReasonIsStuck_SaysALaterPushWillNotLinkThem(self):
        row = _result_row(_result([_pair("reconciled")] * 4))

        assert "a later push will not link these" in row
        assert "needs a change in Actual first" in row

    def test_Row_WhenTheOnlyReasonIsAMissingLeg_SaysALaterPushMayClearIt(self):
        row = _result_row(_result([_pair("leg_missing")], {"leg_missing": 1}))

        assert "a later push may clear these" in row
        assert "will not link" not in row

    def test_Row_WhenReasonsAreMixed_SaysHowManyMayClearAndHowManyStay(self):
        pairs = [_pair("leg_missing"), _pair("reconciled"), _pair("linked_elsewhere")]
        counts = {"leg_missing": 1, "reconciled": 1, "linked_elsewhere": 1}
        row = _result_row(_result(pairs, counts))

        assert "1 may clear on a later push; 2 stay until changed in Actual" in row

    def test_Row_WhenMorePairsWereSkippedThanTheResultNames_SaysHowManyAreNotListed(self):
        row = _result_row(_result([_pair("reconciled")] * 50, {"reconciled": 60}))

        assert "60 transfer pairs skipped" in row
        assert "and 10 more, not listed" in row

    def test_Row_WhenNothingWasSkipped_ShowsNoSkipBlock(self):
        row = _result_row(_result([], {}))

        assert "skipped -" not in row
        assert "<details>" not in row

    def test_Row_WhenTheApplierPredatesNamingAndGaveOnlyCounts_ShowsTheCountsAndNoBlock(self):
        old = _result([], {"reconciled": 4})
        del old["transfers"]["skipped_pairs"]

        row = _result_row(old)

        assert "4 skipped" in row
        assert "<details>" not in row

    def test_Row_WhenTheReasonIsOneThisPageDoesNotKnow_SaysSoAndDoesNotPromiseAClearance(self):
        row = _result_row(_result([_pair("brand_new_reason")], {"brand_new_reason": 1}))

        assert "unrecognised reason (brand_new_reason)" in row
        assert "may clear" not in row

    def test_Row_WhenAccountNamesCarryMarkup_TheyAreEscaped(self):
        row = _result_row(_result([_pair("reconciled", debit="<script>x</script>")]))

        assert "<script>" not in row

    def test_Row_WhenARecordCarriesAnAmount_NoFigureIsShown(self):
        pair = {**_pair("reconciled"), "amount": 87123, "formatted": "871.23"}

        row = _result_row(_result([pair]))

        for form in AMOUNT_FORMS:
            assert form not in row

    def test_Row_WhenThePushFailed_ShowsItsErrorAndNoSkipBlock(self):
        failed = {
            "kind": "push",
            "ok": False,
            "finished_at": "2026-10-04T05:00:00Z",
            "error": "applier unreachable",
            "transfers": _result([_pair("reconciled")])["transfers"],
        }

        row = _result_row(failed)

        assert "applier unreachable" in row
        assert "<details>" not in row
