"""What the Actual page says to a person about an audit and the account roster.

The applier decides what each audit category means (applier/audit.mjs);
these tests hold the page to saying that meaning and the action it
leads to, in words, instead of printing seven bare numbers. Counts of rows
are fine on a GET; no figure and no monetary value ever is.
"""

from obdi.pages import web


def _audit(*accounts: dict[str, object], transfers: object = None) -> dict[str, object]:
    result: dict[str, object] = {
        "ok": True,
        "kind": "audit",
        "finished_at": "2026-10-02T09:00:00Z",
        "accounts": list(accounts),
    }
    if transfers is not None:
        result["transfers"] = transfers
    return result


def _account(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "account_id": "act-1",
        "name": "example-current",
        "missing_account": False,
        "expected": 947,
        "present": 947,
        "missing": 0,
        "orphaned": 0,
        "human": 0,
        "diverged": 0,
        "duplicated": 0,
    }
    base.update(overrides)
    return base


def _render(*accounts: dict[str, object]) -> str:
    return web._audit_result_row(_audit(*accounts))


class TestRosterSaysBoundNotSyncing:
    def _roster(self) -> list[dict[str, object]]:
        return [
            {
                "ref": "example-current",
                "label": "example-current",
                "state": "syncing",
                "count": 947,
            },
            {"ref": "example-saver", "label": "example-saver", "state": "provision", "count": 0},
            {"ref": "bank:ab12", "label": "Unnamed (bank)", "state": "unnamed", "count": 5},
        ]

    def test_BoundAccount_IsCalledBoundAndSaidToBeInEachPush_NeverSyncing(self):
        page = web._actual_rows(lambda: [], True, lambda: self._roster())

        assert "bound" in page
        assert "included in each push" in page
        assert "syncing" not in page

    def test_RosterTally_UsesTheBoundWording(self):
        page = web._actual_rows(lambda: [], True, lambda: self._roster())

        assert (
            "A push will send rows to 1 bound account, create 1 account in Actual, "
            "and leave 1 account waiting for a name." in page
        )

    def test_UnnamedAccount_IsNotClaimedToBeSynced(self):
        page = web._actual_rows(lambda: [], True, lambda: self._roster())

        assert "not bound - needs a name" in page
        assert "not synced" not in page


class TestAuditSaysWhatEachDifferenceMeansAndWhatToDo:
    def test_CleanAccount_IsOneLineWithNoZeroCategoriesListed(self):
        page = _render(_account())

        assert "example-current: agrees - 947 rows compared" in page
        for word in ("missing", "orphaned", "diverged", "duplicated", "expected 947"):
            assert word not in page
        assert page.count('class="muted"') + page.count('class="warn"') == 1

    def test_AccountWithRowsToPush_SaysTheNextPushAddsThem(self):
        page = _render(_account(missing=37, present=910))

        assert 'pill-bad">differs</span> example-current' in page
        assert "37 expected rows are not in Actual - the next push adds them" in page

    def test_AccountWithOrphans_PointsToRemoveOrphanedImportsAndItsLimits(self):
        page = _render(_account(orphaned=3))

        assert "3 rows in Actual carry an imported id this account does not expect" in page
        assert "Remove orphaned imports" in page
        assert "linked transfer" in page

    def test_AccountExpectingNothingButHoldingOrphans_SaysNothingIsSentAndThePruneSkipsIt(self):
        page = _render(_account(expected=0, present=0, orphaned=212))

        assert "nothing is sent for this account now" in page
        assert "earlier binding or mapping" in page
        assert "will not clear them" in page
        assert "212 rows in Actual carry an imported id this account does not expect" not in page

    def test_YoursRows_AreInformationNeverADifference(self):
        page = web._actual_rows(lambda: [_audit(_account(human=12))], True)

        assert "12 rows entered by hand in Actual are never compared or touched" in page
        assert "audit clean" in page
        assert "audit: differences" not in page

    def test_DivergedRows_SayOnlyOpeningBalanceRowsAreCorrectedAndNoActionIsOffered(self):
        page = _render(_account(diverged=2))

        assert "2 rows in Actual differ in date or value from what obdi expects" in page
        assert "only opening-balance rows" in page
        assert "no action is offered for the rest yet" in page

    def test_SingleDivergedRow_ReadsAsASingularSentence(self):
        page = _render(_account(diverged=1))

        assert "1 row in Actual differs in date or value" in page

    def test_DuplicatedIds_SayNoActionIsOfferedYet(self):
        page = _render(_account(duplicated=1))

        assert "1 imported id appears on more than one row in Actual" in page
        assert "No action is offered for this yet" in page

    def test_BalanceThatDiffers_AccountWithHandEntries_NamesThemAsAPossibleCause(self):
        page = _render(_account(human=4, balance={"agrees": False}))

        assert "balance differs" in page
        assert "entered by hand count in Actual's balance" in page

    def test_BalanceThatAgrees_IsStatedAndNotAnExplanation(self):
        page = _render(_account(balance={"agrees": True}))

        assert "example-current: agrees - 947 rows compared, balance agrees" in page

    def test_UnlinkedTransferPairs_SayWhatThePushDoesAboutThem(self):
        result = _audit(
            _account(),
            transfers={"pairs": 3, "linked": 2, "unlinked": 1, "leg_missing": 0,
                       "by_account": {"act-1": {"pairs": 3, "linked": 2}}},
        )
        page = web._audit_result_row(result)

        assert (
            "1 transfer pair not linked - a push links what it can and names each pair it skips"
            in page
        )
        assert "transfers linked 2 of 3 pairs" in page
        assert "(s)" not in page

    def test_OrphansTheRemovalWillTake_AreSaidInWords_NotAsAnUnknownCategory(self):
        """Met on the deployed instance: the audit began reporting how many
        orphans a removal would take, and the page read that count as "not a
        category this page knows" beside a sentence saying linked transfer
        legs are never removed, which had stopped being true."""
        page = _render(
            _account(orphaned=727, orphaned_will_go=727, orphaned_will_stay={})
        )

        assert "727 rows in Actual carry an imported id this account does not expect" in page
        assert "Remove orphaned imports will remove 727 and leave none" in page
        assert "unlinked first" in page
        assert "not a category this page knows" not in page
        assert "orphaned_will_go" not in page
        assert "except legs of linked transfers" not in page

    def test_OrphansTheRemovalWillLeave_AreCountedWithTheirReasons(self):
        page = _render(
            _account(
                orphaned=10,
                orphaned_will_go=7,
                orphaned_will_stay={"partner_not_ours": 2, "reconciled": 1},
            )
        )

        assert "Remove orphaned imports will remove 7 and leave 3" in page
        assert "2 because the other leg of its transfer is not an obdi import" in page
        assert "1 because a leg of its transfer is reconciled" in page

    def test_AnAuditWithNoSplit_ClaimsNone(self):
        """An audit taken by an older applier, or a split that does not add up
        to the orphaned count, must not put a figure on the page."""
        older = _render(_account(orphaned=5))
        wrong = _render(
            _account(orphaned=5, orphaned_will_go=9, orphaned_will_stay={})
        )

        for page in (older, wrong):
            assert "will remove" not in page
            assert "Remove orphaned imports deletes those that are obdi's own" in page
            assert "not a category this page knows" not in page

    def test_OnlyOrphansThatWillGo_DoNotMakeACleanAccountDiffer(self):
        page = web._actual_rows(
            lambda: [_audit(_account(orphaned_will_go=0, orphaned_will_stay={}))], True
        )

        assert "audit clean" in page

    def test_CategoryThePageHasNeverHeardOf_StillReadsAsADifference(self):
        page = web._actual_rows(lambda: [_audit(_account(wrong_sign=37))], True)

        assert "audit: differences" in page
        assert "audit clean" not in page
        assert "wrong_sign 37" in page
        assert "not a category this page knows" in page

    def test_Page_NeverPrintsTheWordAmountOrAFigureInTheNewWording(self):
        page = _render(
            _account(missing=1, orphaned=1, diverged=1, duplicated=1, human=1,
                     balance={"agrees": False})
        )

        assert "amount" not in page.lower()


class TestSharedLabelsAreToldApart:
    def test_AuditAccountsSharingALabel_EachShowsItsReference(self):
        page = _render(
            _account(account_id="act-aaa", name="Joint account"),
            _account(account_id="act-bbb", name="Joint account"),
        )

        assert "<code>act-aaa</code>" in page
        assert "<code>act-bbb</code>" in page

    def test_AuditAccountsWithUniqueLabels_AddNoReferenceNoise(self):
        page = _render(
            _account(account_id="act-aaa", name="example-current"),
            _account(account_id="act-bbb", name="example-saver"),
        )

        assert "<code>" not in page

    def test_RosterEntriesSharingALabel_EachShowsItsCanonicalReference(self):
        page = web._actual_rows(
            lambda: [],
            True,
            lambda: [
                {"ref": "halifax-main", "label": "Household", "state": "syncing", "count": 3},
                {"ref": "halifax-joint", "label": "Household", "state": "provision", "count": 0},
            ],
        )

        assert "<code>halifax-main</code>" in page
        assert "<code>halifax-joint</code>" in page

    def test_RosterEntriesWithUniqueLabels_AddNoReferenceNoise(self):
        page = web._actual_rows(
            lambda: [],
            True,
            lambda: [
                {"ref": "halifax-main", "label": "Main", "state": "syncing", "count": 3},
                {"ref": "halifax-joint", "label": "Joint", "state": "provision", "count": 0},
            ],
        )

        assert "<code>" not in page
