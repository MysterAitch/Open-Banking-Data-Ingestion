"""Clearing an account that expects nothing, and the warning on a large removal.

The applier refuses to prune an account whose expected set is empty, so an
account obdi no longer sends anything for keeps its old imported rows for ever.
A person can now clear one such account at a time, confirming the count they
were shown; a removal that is unexpectedly large says so and asks for a second,
separate confirmation.

Counts are chosen so that each rule is isolated, and written down here before
the first run. Thresholds: 100 rows from one account (static); at least 20 rows
AND at least a quarter of the rows obdi imported there (dynamic); 250 rows
across a general prune (total).

    expects nothing (present 0), orphaned 12    -> neither rule
    expects nothing, orphaned 19                -> neither (one under the floor)
    expects nothing, orphaned 20                -> dynamic only (the floor, all of them)
    expects nothing, orphaned 60                -> dynamic only (under 100)
    expects nothing, orphaned 150               -> static and dynamic
    ordinary, present 900, orphaned 100         -> static only (a ninth of them)
    ordinary, present 901, orphaned 99          -> neither (one under 100, a tenth)
    ordinary, present 70, orphaned 30           -> dynamic only (30 of 100)
    ordinary, present 75, orphaned 25           -> dynamic only (exactly a quarter)
    ordinary, present 76, orphaned 24           -> neither (24 of 100)
    ordinary, present 5, orphaned 3             -> neither (a big share, under the floor)
    three ordinary accounts of 90 each, present 9000 -> total rule only (270)
    three ordinary accounts of 83 each, present 9000 -> neither (249)
"""

from __future__ import annotations

import json
import re
import threading
from http.server import HTTPServer

import httpx
import pytest

from obdi import web
from obdi.actual_push import build_audit_envelope, build_prune_envelope
from obdi.cli import queue_actual_prune
from obdi.connections import ConnectionStore
from obdi.store import Store
from obdi.web import AuthorisationSession, ConnectionHandler, WebConfig
from obdi.web_prune import (
    STATIC_ROWS,
    TOTAL_ROWS,
    OrphanCount,
    high_reasons,
    total_reason,
)
from test_transfer_pairs_payload import BOUND_BOTH, MAIN, POT, _household


def account(
    account_id: str, name: str, *, expected: int, present: int, orphaned: int
) -> dict[str, object]:
    return {
        "account_id": account_id,
        "name": name,
        "expected": expected,
        "present": present,
        "missing": 0,
        "orphaned": orphaned,
        "human": 0,
        "diverged": 0,
        "duplicated": 0,
    }


def audit(*accounts: dict[str, object], ok: bool = True) -> dict[str, object]:
    if not ok:
        return {"kind": "audit", "ok": False, "finished_at": "2026-10-01T13:00:00Z", "error": "x"}
    return {
        "kind": "audit",
        "ok": True,
        "finished_at": "2026-10-01T13:00:00Z",
        "accounts": list(accounts),
    }


def old_joint(orphaned: int) -> dict[str, object]:
    return account("old-id", "Old Joint", expected=0, present=0, orphaned=orphaned)


def ordinary(
    account_id: str, name: str, *, present: int, orphaned: int
) -> dict[str, object]:
    return account(account_id, name, expected=present + 5, present=present, orphaned=orphaned)


class Calls:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def __call__(self, **kwargs: object) -> str:
        self.calls.append(kwargs)
        return "queued prune"


@pytest.fixture
def serve(tmp_path):
    servers: list[HTTPServer] = []

    def start(results, **hooks) -> str:
        config = WebConfig(
            client_id="c",
            client_secret="tlcs_live_abcdefghij1234567890",
            redirect_uri="https://obdi.example.com/callback",
            connection_store=ConnectionStore(tmp_path / f"c{len(servers)}.json"),
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


def page_of(base: str) -> str:
    response = httpx.get(f"{base}/actual", timeout=20)
    assert response.status_code == 200
    return response.text


def clear_form(page: str, account_id: str = "old-id") -> str:
    """The clearing form for one account, from its opening tag to its close."""
    for form in re.findall(r"<form.*?</form>", page, flags=re.S):
        if f'name="clear_account" value="{account_id}"' in form:
            return form
    raise AssertionError(f"no clearing form for {account_id}")


def general_form(page: str) -> str:
    for form in re.findall(r"<form.*?</form>", page, flags=re.S):
        if 'action="/prune-actual"' in form and 'name="clear_account"' not in form:
            return form
    raise AssertionError("no general prune form")


def press(base: str, **fields: object) -> httpx.Response:
    return httpx.post(f"{base}/prune-actual", data=fields, timeout=20)


class TestTheEnvelopeCarriesWhatThePersonConfirmed:
    @pytest.fixture
    def store(self, tmp_path):
        with Store(tmp_path / "prune.sqlite3") as opened:
            _household(opened)
            yield opened

    def test_PruneEnvelope_WithoutCounts_CarriesNeitherKey(self, store):
        envelope = build_prune_envelope(store, BOUND_BOTH)

        assert envelope["kind"] == "prune"
        assert "clear_empty" not in envelope
        assert "confirmed" not in envelope
        assert envelope == {**build_audit_envelope(store, BOUND_BOTH), "kind": "prune"}

    def test_PruneEnvelope_WithCounts_CarriesExactlyThoseCounts(self, store):
        envelope = build_prune_envelope(
            store, BOUND_BOTH, clear_empty={"act-main": 212}, confirmed={"act-pot": 7}
        )

        assert envelope["clear_empty"] == {"act-main": 212}
        assert envelope["confirmed"] == {"act-pot": 7}
        assert envelope["version"] == 3

    def test_PruneEnvelope_WithEmptyMappings_CarriesNeitherKey(self, store):
        envelope = build_prune_envelope(store, BOUND_BOTH, clear_empty={}, confirmed={})

        assert "clear_empty" not in envelope
        assert "confirmed" not in envelope

    def test_QueuedClear_CarriesOnlyTheNamedAccountsBindingAndItsCount(
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

        queue_actual_prune(db, clear_empty={"act-pot": 4})

        (request,) = (tmp_path / "actual" / "requests").glob("prune-*.json")
        sent = json.loads(request.read_text(encoding="utf-8"))
        assert sent["kind"] == "prune"
        assert sent["clear_empty"] == {"act-pot": 4}
        assert list(sent["accounts"]) == ["act-pot"]

    def test_QueuedPrune_WithConfirmedCounts_KeepsEveryBoundAccount(self, tmp_path, monkeypatch):
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

        queue_actual_prune(db, confirmed={"act-main": 3})

        (request,) = (tmp_path / "actual" / "requests").glob("prune-*.json")
        sent = json.loads(request.read_text(encoding="utf-8"))
        assert sent["confirmed"] == {"act-main": 3}
        assert "clear_empty" not in sent
        assert sorted(sent["accounts"]) == ["act-main", "act-pot"]


class TestWhenARemovalIsUnexpectedlyHigh:
    def count(self, *, expected: int, present: int, orphaned: int) -> OrphanCount:
        return OrphanCount("id", "Name", expected, present, orphaned)

    def test_HighReasons_BelowEveryThreshold_AreEmpty(self):
        assert high_reasons(self.count(expected=0, present=0, orphaned=12)) == []
        assert high_reasons(self.count(expected=0, present=0, orphaned=19)) == []
        assert high_reasons(self.count(expected=906, present=901, orphaned=99)) == []
        assert high_reasons(self.count(expected=81, present=76, orphaned=24)) == []
        assert high_reasons(self.count(expected=10, present=5, orphaned=3)) == []

    def test_HighReasons_OnlyTheStaticRuleTripped_NamesOnlyTheStaticRule(self):
        reasons = high_reasons(self.count(expected=905, present=900, orphaned=STATIC_ROWS))

        assert len(reasons) == 1
        assert "100 rows or more" in reasons[0]

    def test_HighReasons_OnlyTheDynamicRuleTripped_NamesOnlyTheDynamicRule(self):
        for present, orphaned in ((70, 30), (75, 25)):
            reasons = high_reasons(
                self.count(expected=present + 5, present=present, orphaned=orphaned)
            )

            assert len(reasons) == 1, (present, orphaned)
            assert "a quarter" in reasons[0]

    def test_HighReasons_AnAccountThatExpectsNothingAndHoldsTheFloor_TripsTheDynamicRule(self):
        assert len(high_reasons(self.count(expected=0, present=0, orphaned=20))) == 1

    def test_HighReasons_BothRulesTripped_NamesBoth(self):
        assert len(high_reasons(self.count(expected=0, present=0, orphaned=150))) == 2

    def test_TotalReason_AtTheThresholdTripsAndJustUnderDoesNot(self):
        def counts(each: int) -> list[OrphanCount]:
            return [OrphanCount(f"a{n}", f"A{n}", 9005, 9000, each) for n in range(3)]

        assert total_reason(counts(90)) is not None
        assert total_reason(counts(83)) is None
        assert total_reason([OrphanCount("a", "A", 9, 9, TOTAL_ROWS)]) is not None


class TestThePageBeforeAnyAuditHasBeenRun:
    def test_PruneSection_WithNoAudit_SaysToRunOneAndOffersNoClearingForm(self, serve):
        calls = Calls()
        page = page_of(serve([], prune_actual=calls))

        assert "Run an audit first to see what a removal would take" in page
        assert 'name="clear_account"' not in page
        assert 'name="confirmed"' not in page
        assert 'action="/prune-actual"' in page

    def test_PruneSection_WhenTheNewestAuditFailed_SaysToRunOneAndOffersNoClearingForm(self, serve):
        page = page_of(serve([audit(ok=False)], prune_actual=Calls()))

        assert "Run an audit first to see what a removal would take" in page
        assert 'name="clear_account"' not in page


class TestTheClearingForm:
    def test_ClearForm_ForAnAccountThatExpectsNothingBelowTheThresholds_HasOneTickAndNoWarning(
        self, serve
    ):
        page = page_of(serve([audit(old_joint(12))], prune_actual=Calls()))

        form = clear_form(page)
        assert "Clear 12 imported rows from Old Joint" in form
        assert 'name="clear_count" value="12"' in form
        assert 'name="confirm"' in form
        assert 'name="checked"' not in form
        assert "I have checked this count against Actual" not in form
        assert "unexpectedly" not in form

    def test_ClearForm_SaysWhatItDeletesWhatItLeavesAndThatItCannotBeUndone(self, serve):
        form = clear_form(page_of(serve([audit(old_joint(12))], prune_actual=Calls())))

        assert "Nothing is sent for this account now" in form
        assert "deletes obdi's own imported rows from it in Actual" in form
        assert "one leg of a linked transfer is unlinked first" in form
        assert "the other leg stays as an ordinary row" in form
        assert "rows from another importer" in form
        assert "Rows entered by hand are never touched" in form
        assert "cannot be undone from here" in form
        assert "binding the account again would re-send its rows on the next push" in form

    def test_ClearForm_OnePerAccountThatExpectsNothingAndHoldsOrphans(self, serve):
        page = page_of(
            serve(
                [
                    audit(
                        old_joint(12),
                        account("two-id", "Old Savings", expected=0, present=0, orphaned=5),
                        account("empty-id", "Old Empty", expected=0, present=0, orphaned=0),
                        ordinary("main-id", "Main", present=50, orphaned=2),
                    )
                ],
                prune_actual=Calls(),
            )
        )

        assert page.count('name="clear_account"') == 2
        assert "Clear 5 imported rows from Old Savings" in clear_form(page, "two-id")
        assert 'value="empty-id"' not in page
        assert 'name="clear_account" value="main-id"' not in page

    def test_ClearForm_OneRowReadsAsOneRow(self, serve):
        form = clear_form(page_of(serve([audit(old_joint(1))], prune_actual=Calls())))

        assert "Clear 1 imported row from Old Joint" in form

    def test_ClearForm_AtTheDynamicFloorExactly_CarriesTheWarningAndTheSecondTick(self, serve):
        form = clear_form(page_of(serve([audit(old_joint(20))], prune_actual=Calls())))

        assert 'name="checked"' in form
        assert "I have checked this count against Actual" in form
        assert 'class="bad"' in form
        assert "20 rows" in form

    def test_ClearForm_OneUnderTheFloor_HasNoWarning(self, serve):
        form = clear_form(page_of(serve([audit(old_joint(19))], prune_actual=Calls())))

        assert 'name="checked"' not in form

    def test_ClearForm_DynamicRuleOnly_NamesTheDynamicRuleAndNotTheStaticOne(self, serve):
        form = clear_form(page_of(serve([audit(old_joint(60))], prune_actual=Calls())))

        assert "a quarter" in form
        assert "100 rows or more" not in form
        assert 'name="checked"' in form

    def test_ClearForm_StaticAndDynamicRules_NamesBothRules(self, serve):
        form = clear_form(page_of(serve([audit(old_joint(150))], prune_actual=Calls())))

        assert "a quarter" in form
        assert "100 rows or more" in form
        assert "150 rows" in form

    def test_Page_WithAHighRemoval_ShowsNoAmountAndNoMoneyFigure(self, serve):
        page = page_of(serve([audit(old_joint(1234))], prune_actual=Calls()))

        assert "amount" not in page.lower().replace("amounts are shown", "")
        assert "£" not in page


class TestTheGeneralPruneList:
    def test_GeneralForm_ListsEachAccountWithOrphansAndTheTotalAndCarriesTheCounts(self, serve):
        page = page_of(
            serve(
                [
                    audit(
                        ordinary("a-id", "Alpha Current", present=50, orphaned=7),
                        ordinary("b-id", "Beta Savings", present=500, orphaned=4),
                        ordinary("c-id", "Gamma Clean", present=40, orphaned=0),
                        old_joint(300),
                    )
                ],
                prune_actual=Calls(),
            )
        )

        form = general_form(page)
        assert "Alpha Current: 7 rows" in form
        assert "Beta Savings: 4 rows" in form
        assert "Gamma Clean" not in form
        assert "Old Joint" not in form
        assert "Total: 11 rows" in form
        assert 'name="confirmed" value="7:a-id"' in form
        assert 'name="confirmed" value="4:b-id"' in form
        assert 'name="checked"' not in form

    def test_GeneralForm_StaticRuleOnly_WarnsAndNamesTheAccountAndTheRule(self, serve):
        page = page_of(
            serve(
                [audit(ordinary("a-id", "Alpha Current", present=900, orphaned=100))],
                prune_actual=Calls(),
            )
        )

        form = general_form(page)
        assert 'name="checked"' in form
        assert "100 rows or more" in form
        assert "a quarter" not in form
        assert "Alpha Current" in form

    def test_GeneralForm_JustUnderEveryRule_HasNoWarning(self, serve):
        page = page_of(
            serve(
                [
                    audit(
                        ordinary("a-id", "Alpha", present=901, orphaned=99),
                        ordinary("b-id", "Beta", present=76, orphaned=24),
                        ordinary("c-id", "Gamma", present=5, orphaned=3),
                    )
                ],
                prune_actual=Calls(),
            )
        )

        assert 'name="checked"' not in general_form(page)

    def test_GeneralForm_TotalRuleOnly_WarnsAboutTheTotal(self, serve):
        page = page_of(
            serve(
                [
                    audit(
                        *[
                            ordinary(f"id{n}", f"Account {n}", present=9000, orphaned=90)
                            for n in range(3)
                        ]
                    )
                ],
                prune_actual=Calls(),
            )
        )

        form = general_form(page)
        assert 'name="checked"' in form
        assert "270 rows" in form
        assert "250 rows or more" in form
        assert "100 rows or more" not in form

    def test_GeneralForm_TotalJustUnderTheThreshold_HasNoWarning(self, serve):
        page = page_of(
            serve(
                [
                    audit(
                        *[
                            ordinary(f"id{n}", f"Account {n}", present=9000, orphaned=83)
                            for n in range(3)
                        ]
                    )
                ],
                prune_actual=Calls(),
            )
        )

        assert 'name="checked"' not in general_form(page)

    def test_GeneralForm_WithNoOrphansAnywhere_SaysThereIsNothingToRemove(self, serve):
        page = page_of(
            serve([audit(ordinary("a-id", "Alpha", present=50, orphaned=0))], prune_actual=Calls())
        )

        assert "The newest audit found no orphaned imports" in page


class TestThePostThatQueuesAClear:
    def base(self, serve, *accounts: dict[str, object]) -> tuple[str, Calls]:
        calls = Calls()
        return serve([audit(*accounts)], prune_actual=calls), calls

    def test_Clear_ForALowCountWithTheFirstTick_QueuesExactlyThatAccountAndCount(self, serve):
        base, calls = self.base(serve, old_joint(12))

        response = press(base, confirm="yes", clear_account="old-id", clear_count="12")

        assert response.status_code == 200
        assert calls.calls == [{"clear_empty": {"old-id": 12}}]
        assert 'href="/actual"' in response.text

    def test_Clear_WithoutTheFirstTick_IsRefusedAndQueuesNothing(self, serve):
        base, calls = self.base(serve, old_joint(12))

        response = press(base, clear_account="old-id", clear_count="12")

        assert response.status_code == 400
        assert calls.calls == []
        assert 'href="/actual"' in response.text

    def test_Clear_ForAHighCountWithoutTheSecondTick_IsRefusedWithTheReason(self, serve):
        base, calls = self.base(serve, old_joint(60))

        response = press(base, confirm="yes", clear_account="old-id", clear_count="60")

        assert response.status_code == 400
        assert calls.calls == []
        assert "I have checked this count against Actual" in response.text
        assert "a quarter" in response.text
        assert 'href="/actual"' in response.text

    def test_Clear_ForAHighCountWithBothTicks_Queues(self, serve):
        base, calls = self.base(serve, old_joint(60))

        response = press(
            base, confirm="yes", checked="yes", clear_account="old-id", clear_count="60"
        )

        assert response.status_code == 200
        assert calls.calls == [{"clear_empty": {"old-id": 60}}]

    def test_Clear_WhenThePostedCountIsNotTheNewestAuditsCount_IsRefusedAsStale(self, serve):
        base, calls = self.base(serve, old_joint(12))

        for posted in ("13", "11"):
            response = press(base, confirm="yes", clear_account="old-id", clear_count=posted)

            assert response.status_code == 400, posted
            assert "run the audit again" in response.text, posted
            assert 'href="/actual"' in response.text
        assert calls.calls == []

    def test_Clear_ForAnAccountTheAuditDoesNotShow_IsRefused(self, serve):
        base, calls = self.base(serve, old_joint(12))

        response = press(base, confirm="yes", clear_account="nobody-id", clear_count="12")

        assert response.status_code == 400
        assert calls.calls == []

    def test_Clear_ForAnAccountThatStillExpectsRows_IsRefused(self, serve):
        base, calls = self.base(serve, ordinary("main-id", "Main", present=50, orphaned=12))

        response = press(base, confirm="yes", clear_account="main-id", clear_count="12")

        assert response.status_code == 400
        assert "obdi still sends rows for this account" in response.text
        assert calls.calls == []

    def test_Clear_WithAnUnreadableCount_IsRefused(self, serve):
        base, calls = self.base(serve, old_joint(12))

        for posted in ("", "twelve", "-12", "0", "12.5"):
            response = press(base, confirm="yes", clear_account="old-id", clear_count=posted)

            assert response.status_code == 400, posted
        assert calls.calls == []

    def test_Clear_WhenThereIsNoAudit_IsRefused(self, serve):
        calls = Calls()
        base = serve([], prune_actual=calls)

        response = press(base, confirm="yes", clear_account="old-id", clear_count="12")

        assert response.status_code == 400
        assert "Run an audit first" in response.text
        assert calls.calls == []

    def test_Clear_WhenTheNewestAuditFailed_IsRefused(self, serve):
        calls = Calls()
        base = serve([audit(ok=False)], prune_actual=calls)

        response = press(base, confirm="yes", clear_account="old-id", clear_count="12")

        assert response.status_code == 400
        assert calls.calls == []


class TestThePostThatQueuesAGeneralPrune:
    def test_Prune_WithNoAuditAndNoCounts_QueuesAsItAlwaysDid(self, serve):
        calls = Calls()
        base = serve([], prune_actual=calls)

        response = press(base, confirm="yes")

        assert response.status_code == 200
        assert calls.calls == [{}]

    def test_Prune_WithTheCountsShown_PassesThemAsConfirmed(self, serve):
        calls = Calls()
        base = serve(
            [audit(ordinary("a-id", "Alpha", present=50, orphaned=7), old_joint(300))],
            prune_actual=calls,
        )

        response = press(base, confirm="yes", confirmed=["7:a-id"])

        assert response.status_code == 200
        assert calls.calls == [{"confirmed": {"a-id": 7}}]

    def test_Prune_WhenTheCountsAreNotTheNewestAudits_IsRefusedAsStale(self, serve):
        calls = Calls()
        base = serve([audit(ordinary("a-id", "Alpha", present=50, orphaned=8))], prune_actual=calls)

        response = press(base, confirm="yes", confirmed=["7:a-id"])

        assert response.status_code == 400
        assert "run the audit again" in response.text
        assert calls.calls == []

    def test_Prune_WithCountsButNoAudit_IsRefusedAsStale(self, serve):
        calls = Calls()
        base = serve([], prune_actual=calls)

        response = press(base, confirm="yes", confirmed=["7:a-id"])

        assert response.status_code == 400
        assert calls.calls == []

    def test_Prune_WithAnUnreadableCount_IsRefused(self, serve):
        calls = Calls()
        base = serve([audit(ordinary("a-id", "Alpha", present=50, orphaned=7))], prune_actual=calls)

        for posted in ("seven:a-id", "7", ":a-id", "-7:a-id", "7.5:a-id"):
            response = press(base, confirm="yes", confirmed=[posted])

            assert response.status_code == 400, posted
        assert calls.calls == []

    def test_Prune_ForAHighTotalWithoutTheSecondTick_IsRefusedWithTheReason(self, serve):
        calls = Calls()
        accounts = [ordinary(f"id{n}", f"Account {n}", present=9000, orphaned=90) for n in range(3)]
        base = serve([audit(*accounts)], prune_actual=calls)
        counts = [f"90:id{n}" for n in range(3)]

        refused = press(base, confirm="yes", confirmed=counts)
        allowed = press(base, confirm="yes", checked="yes", confirmed=counts)

        assert refused.status_code == 400
        assert "250 rows or more" in refused.text
        assert 'href="/actual"' in refused.text
        assert allowed.status_code == 200
        assert calls.calls == [{"confirmed": {f"id{n}": 90 for n in range(3)}}]

    def test_Prune_WithoutTheFirstTick_IsRefused(self, serve):
        calls = Calls()
        base = serve([audit(ordinary("a-id", "Alpha", present=50, orphaned=7))], prune_actual=calls)

        response = press(base, confirmed=["7:a-id"])

        assert response.status_code == 400
        assert calls.calls == []


class TestPruneResultsReadAsWordsAndARefusalIsAWarning:
    def render(self, *accounts: dict[str, object]) -> str:
        return web._prune_result_row(
            {
                "ok": True,
                "kind": "prune",
                "finished_at": "2026-10-01T14:00:00Z",
                "accounts": list(accounts),
            }
        )

    def test_Removed_SaysHowManyWereRemovedAndWhatWasLeft(self):
        rendered = self.render(
            {
                "account_id": "old-id",
                "name": "Old Joint",
                "removed": 212,
                "linked_left": 3,
                "foreign_ids": 5,
            }
        )

        assert "pruned (212 removed)" in rendered
        assert "Old Joint: removed 212 orphaned imports" in rendered
        assert "3 linked transfer legs left" in rendered
        assert "5 rows carrying another importer's id left" in rendered

    def test_Refused_ReadsAsAWarningWithBothNumbersAndNotAsSuccess(self):
        rendered = self.render(
            {
                "account_id": "old-id",
                "name": "Old Joint",
                "refused": "holds 215, more than the 212 confirmed - run the audit again",
                "holds": 215,
                "confirmed": 212,
            }
        )

        assert "pill-ok" not in rendered
        assert "pill-bad" in rendered
        assert "refused" in rendered
        assert "Old Joint: refused, nothing deleted - holds 215, more than the 212 confirmed" in (
            rendered
        )
        assert "pruned (0 removed)" not in rendered

    def test_Skipped_StaysAWarningLineAndNothingIsClaimedRemoved(self):
        rendered = self.render(
            {
                "account_id": "old-id",
                "name": "Old Joint",
                "skipped": "expected set empty - refusing to prune blind",
            },
            {"account_id": "a-id", "name": "Alpha", "removed": 4},
        )

        assert "Old Joint: expected set empty - refusing to prune blind" in rendered
        assert "pruned (4 removed)" in rendered

    def test_Refused_AndRemovedTogether_TheVerdictStillSaysSomethingWasRefused(self):
        rendered = self.render(
            {"account_id": "a-id", "name": "Alpha", "removed": 4},
            {
                "account_id": "old-id",
                "name": "Old Joint",
                "refused": "holds 9, more than the 2 confirmed - run the audit again",
                "holds": 9,
                "confirmed": 2,
            },
        )

        assert "4 removed" in rendered
        assert "1 refused" in rendered


def split_account(
    account_id: str,
    name: str,
    *,
    present: int,
    will_go: int,
    staying: dict[str, int],
    expected: int | None = None,
) -> dict[str, object]:
    orphaned = will_go + sum(staying.values())
    entry = account(
        account_id,
        name,
        expected=present + 5 if expected is None else expected,
        present=present,
        orphaned=orphaned,
    )
    entry["orphaned_will_go"] = will_go
    entry["orphaned_will_stay"] = staying
    return entry


class TestThePageSaysBeforeThePressHowManyWillGoAndHowManyStay:
    def test_GeneralForm_WhenSomeOrphansAreLinkedToOtherAccounts_SaysHowManyGoAndHowManyStayAndWhy(
        self, serve
    ):
        page = page_of(
            serve(
                [
                    audit(
                        split_account(
                            "main-id",
                            "Main",
                            present=50,
                            will_go=7,
                            staying={"partner_not_ours": 2, "reconciled": 1},
                        )
                    )
                ],
                prune_actual=Calls(),
            )
        )

        form = general_form(page)
        assert "Main: 10 rows (7 will be removed and 3 will stay" in form
        assert "2 because the other leg of its transfer is not an obdi import" in form
        assert "1 because a leg of its transfer is reconciled" in form
        assert "Total: 10 rows, of which 7 will be removed and 3 will stay." in form

    def test_GeneralForm_WhenNothingStays_SaysSoPlainly(self, serve):
        page = page_of(
            serve(
                [audit(split_account("main-id", "Main", present=50, will_go=4, staying={}))],
                prune_actual=Calls(),
            )
        )

        assert "Main: 4 rows (4 will be removed and none will stay)" in general_form(page)

    def test_GeneralForm_CarriesTheWholeOrphanedCountAsTheCeilingNotJustTheRemovableOnes(
        self, serve
    ):
        page = page_of(
            serve(
                [
                    audit(
                        split_account(
                            "main-id", "Main", present=50, will_go=7, staying={"foreign": 3}
                        )
                    )
                ],
                prune_actual=Calls(),
            )
        )

        assert 'name="confirmed" value="10:main-id"' in general_form(page)

    def test_ClearForm_SaysHowManyGoAndHowManyStay(self, serve):
        page = page_of(
            serve(
                [
                    audit(
                        split_account(
                            "old-id",
                            "Old Joint",
                            present=0,
                            expected=0,
                            will_go=12,
                            staying={"foreign": 1},
                        )
                    )
                ],
                prune_actual=Calls(),
            )
        )

        form = clear_form(page)
        assert "13 rows carrying an imported id: 12 will be removed and 1 will stay" in form
        assert "1 because it carries an id from another importer" in form
        assert 'name="clear_count" value="13"' in form

    def test_Page_WhenTheAuditGivesNoSplit_ClaimsNoneAndKeepsTheFewerMayGoWarning(self, serve):
        page = page_of(
            serve(
                [audit(ordinary("a-id", "Alpha", present=50, orphaned=7))],
                prune_actual=Calls(),
            )
        )

        form = general_form(page)
        assert "will be removed" not in form
        assert "so fewer may go" in form

    def test_Page_WhenTheSplitDoesNotAddUpToTheOrphanedCount_ClaimsNoSplit(self, serve):
        entry = split_account("a-id", "Alpha", present=50, will_go=5, staying={"foreign": 2})
        entry["orphaned"] = 9
        page = page_of(serve([audit(entry)], prune_actual=Calls()))

        form = general_form(page)
        assert "will be removed" not in form
        assert "Alpha: 9 rows" in form

    def test_Page_WhenAReasonIsOneThisBuildDoesNotKnow_ItIsShownAsGivenAndEscaped(self, serve):
        page = page_of(
            serve(
                [
                    audit(
                        split_account(
                            "a-id", "Alpha", present=50, will_go=1, staying={"<odd>": 2}
                        )
                    )
                ],
                prune_actual=Calls(),
            )
        )

        form = general_form(page)
        assert "2 because &lt;odd&gt;" in form
        assert "<odd>" not in form

    def test_Page_ShowsCountsOnlyNeverAnAmount(self, serve):
        page = page_of(
            serve(
                [
                    audit(
                        split_account(
                            "main-id", "Main", present=50, will_go=7, staying={"reconciled": 3}
                        )
                    )
                ],
                prune_actual=Calls(),
            )
        )

        assert "amount" not in page.lower().replace("amounts are shown", "")
        assert "£" not in page

    def test_HighCountWarning_StillTripsOnTheOrphanedTotalWhenMostOfThemWillStay(self, serve):
        page = page_of(
            serve(
                [
                    audit(
                        split_account(
                            "a-id", "Alpha", present=900, will_go=40, staying={"reconciled": 60}
                        )
                    )
                ],
                prune_actual=Calls(),
            )
        )

        assert "Unexpectedly large removal" in general_form(page)

    def test_PostedCount_IsStillCheckedAgainstTheOrphanedTotalAndNotTheRemovableOnes(self, serve):
        calls = Calls()
        base = serve(
            [
                audit(
                    split_account("a-id", "Alpha", present=50, will_go=7, staying={"foreign": 3})
                )
            ],
            prune_actual=calls,
        )

        stale = press(base, confirm="yes", confirmed="7:a-id")
        assert stale.status_code == 400
        assert calls.calls == []

        accepted = press(base, confirm="yes", confirmed="10:a-id")
        assert accepted.status_code == 200
        assert calls.calls == [{"confirmed": {"a-id": 10}}]


class TestPruneResultsSayWhatWasUnlinkedLeftAndStopped:
    render = TestPruneResultsReadAsWordsAndARefusalIsAWarning.render

    def test_Removed_SaysHowManyWereUnlinkedFirstAndWhyOthersWereLeft(self):
        rendered = self.render(
            {
                "account_id": "main-id",
                "name": "Main",
                "removed": 711,
                "unlinked": 16,
                "linked_left": 2,
                "left": {"partner_not_ours": 2},
            }
        )

        assert "pruned (711 removed)" in rendered
        assert "16 of them were a linked transfer leg, unlinked first" in rendered
        assert "2 linked transfer legs left (2 because the other leg of its transfer" in rendered

    def test_Stopped_ReadsAsAFaultWithTheReasonAndNotAsSuccess(self):
        rendered = self.render(
            {
                "account_id": "main-id",
                "name": "Main",
                "removed": 3,
                "stopped": "a -> b: the partner row is missing after the orphan was deleted",
            }
        )

        assert "pill-ok" not in rendered
        assert "prune stopped on 1 account: 3 removed" in rendered
        assert "Main: stopped" in rendered
        assert "the partner row is missing after the orphan was deleted" in rendered
