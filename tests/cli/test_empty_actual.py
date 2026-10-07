"""Emptying the whole Actual budget: the page, the refusals, the request, and
what obdi does when the applier says it is done.

Everything is invented. The household the page is built from:

    Current Account   bound   120 rows, 7 entered by hand
    Savings Pot       bound    40 rows, 0 by hand
    Old Joint         not bound to an obdi account, 15 rows

so the page must say 3 accounts and 175 rows, 7 by hand in bound accounts, and
15 rows in one unbound account that the audit does not sort. These figures were
chosen before the first run; none of them is a money figure.
"""

from __future__ import annotations

import json
import re
import threading
from http.server import HTTPServer
from pathlib import Path

import httpx
import pytest

from obdi import cli
from obdi.cli import queue_actual_empty
from obdi.export.actual_push import (
    EMPTY_SETTLED_FILE,
    build_empty_envelope,
    queue_push,
    settle_emptied_budgets,
    valid_progress,
)
from obdi.ingest.connections import ConnectionStore
from obdi.ingest.store import Store
from obdi.pages.web import AuthorisationSession, ConnectionHandler, WebConfig
from obdi.pages.web_empty import (
    EMPTY_PHRASE,
    empty_result_row,
    empty_section,
    plan_from_audit,
)
from test_transfer_pairs_payload import MAIN, POT, _household


def bound(account_id: str, name: str, *, rows: int, human: int = 0) -> dict[str, object]:
    return {
        "account_id": account_id,
        "name": name,
        "missing_account": False,
        "expected": rows - human,
        "present": rows - human,
        "human": human,
        "missing": 0,
        "orphaned": 0,
        "diverged": 0,
        "duplicated": 0,
        "rows": rows,
    }


def stray(account_id: str, name: str, *, rows: int) -> dict[str, object]:
    return {"account_id": account_id, "name": name, "unbound_in_actual": True, "rows": rows}


def audit(
    *accounts: dict[str, object], finished: str = "2026-10-01T13:00:00Z"
) -> dict[str, object]:
    return {"kind": "audit", "ok": True, "finished_at": finished, "accounts": list(accounts)}


HOUSEHOLD = audit(
    bound("act-current", "Current Account", rows=120, human=7),
    bound("act-pot", "Savings Pot", rows=40),
    stray("act-joint", "Old Joint", rows=15),
)

SHOWN = {"act-current": 120, "act-pot": 40, "act-joint": 15}


class Calls:
    def __init__(self, refusal: str | None = None) -> None:
        self.calls: list[dict[str, int]] = []
        self.refusal = refusal

    def __call__(self, shown: dict[str, int]) -> str:
        if self.refusal:
            raise ValueError(self.refusal)
        self.calls.append(dict(shown))
        return "queued empty-1.json"


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


def section_of(page: str) -> str:
    match = re.search(
        r"<details><summary><strong>Empty Actual completely</strong>.*?</details>", page, re.S
    )
    assert match, "no empty section on the page"
    return match.group(0)


def empty_form(page: str) -> str:
    for form in re.findall(r"<form.*?</form>", page, flags=re.S):
        if 'action="/empty-actual"' in form:
            return form
    raise AssertionError("no empty form")


def good_form(**overrides: object) -> dict[str, object]:
    fields: dict[str, object] = {
        "confirm": "yes",
        "phrase": EMPTY_PHRASE,
        "account": [f"{rows}:{account_id}" for account_id, rows in SHOWN.items()],
    }
    fields.update(overrides)
    return fields


def press(base: str, **fields: object) -> httpx.Response:
    return httpx.post(f"{base}/empty-actual", data=good_form(**fields), timeout=20)


def text_of(html_text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html_text))


class TestThePageBeforeThePress:
    def test_EmptySection_WithNoAudit_SaysToRunOneFirstAndOffersNoForm(self, serve):
        base = serve([], empty_actual=Calls())

        section = section_of(page_of(base))

        assert "Run an audit first" in section
        assert "<form" not in section

    def test_EmptySection_WhenTheNewestAuditFailed_SaysToRunOneAndOffersNoForm(self, serve):
        failed = {"kind": "audit", "ok": False, "finished_at": "2026-10-01T13:00:00Z", "error": "x"}
        base = serve([failed], empty_actual=Calls())

        section = section_of(page_of(base))

        assert "Run an audit first" in section
        assert "<form" not in section

    def test_EmptySection_WhenTheAuditDoesNotCountEveryAccountsRows_SaysRunANewOneNoForm(
        self, serve
    ):
        old = bound("act-current", "Current Account", rows=5)
        del old["rows"]
        base = serve([audit(old)], empty_actual=Calls())

        section = section_of(page_of(base))

        assert "older applier" in section
        assert "<form" not in section

    def test_EmptySection_WhenNoEmptyHookIsWired_IsAbsent(self, serve):
        base = serve([HOUSEHOLD])

        assert "Empty Actual completely" not in page_of(base)

    def test_EmptySection_WithAnAudit_NamesEveryAccountWithItsRowsAndTheTotal(self, serve):
        base = serve([HOUSEHOLD], empty_actual=Calls())

        said = text_of(section_of(page_of(base)))

        assert "3 accounts holding 175 rows" in said
        assert "Current Account: 120 rows" in said
        assert "Savings Pot: 40 rows" in said
        assert "Old Joint: 15 rows" in said
        assert "Old Joint: 15 rows" in said and "not bound to an obdi account" in said

    def test_EmptySection_SaysHandEnteredRowsGoToo_AndThatAPushCannotBringThemBack(self, serve):
        base = serve([HOUSEHOLD], empty_actual=Calls())

        said = text_of(section_of(page_of(base)))

        assert "7 rows in accounts obdi is bound to were entered by hand in Actual" in said
        assert "15 rows sit in 1 account obdi is not bound to" in said
        assert "Rows entered by hand are deleted too, and a push cannot re-create them" in said

    def test_EmptySection_SaysWhatItIsForAndWhatItWillNotFix(self, serve):
        base = serve([HOUSEHOLD], empty_actual=Calls())

        said = text_of(section_of(page_of(base)))

        assert "starting Actual again from nothing" in said
        assert "will not fix anything wrong in obdi itself" in said

    def test_EmptySection_SaysWhatStaysAndThatItDoesNotPushByItself(self, serve):
        base = serve([HOUSEHOLD], empty_actual=Calls())

        said = text_of(section_of(page_of(base)))

        assert "What stays: categories and category groups, rules, schedules" in said
        assert "does not push by itself" in said
        assert "Push to Actual now" in said
        assert "closed ones included" in said

    def test_EmptySection_WithNoHandEnteredRowsAnywhere_SaysSo(self, serve):
        only = audit(bound("act-pot", "Savings Pot", rows=40))
        base = serve([only], empty_actual=Calls())

        said = text_of(section_of(page_of(base)))

        assert "found no rows entered by hand" in said

    def test_EmptySection_WithOneAccountAndOneRow_UsesSingularWords(self, serve):
        only = audit(bound("act-pot", "Savings Pot", rows=1, human=1))
        base = serve([only], empty_actual=Calls())

        said = text_of(section_of(page_of(base)))

        assert "1 account holding 1 row" in said
        assert "1 row in accounts obdi is bound to was entered by hand" in said

    def test_EmptySection_WhenAuditFoundNoAccountsInActual_SaysThereIsNothingToEmpty(self, serve):
        gone = {
            "account_id": "act-x",
            "name": None,
            "missing_account": True,
            "expected": 4,
        }
        base = serve([audit(gone)], empty_actual=Calls())

        section = section_of(page_of(base))

        assert "nothing to empty" in section
        assert "<form" not in section

    def test_EmptySection_WhenAnEmptyFinishedAfterTheAudit_SaysRunAnotherAuditNoForm(
        self, serve
    ):
        done = {
            "kind": "empty",
            "ok": True,
            "complete": True,
            "finished_at": "2026-10-02T09:00:00Z",
            "removed": [],
        }
        base = serve([HOUSEHOLD, done], empty_actual=Calls())

        section = section_of(page_of(base))

        assert "before Actual was last emptied" in section
        assert "<form" not in section

    def test_EmptySection_WhenAnAuditIsNewerThanTheLastEmpty_OffersTheForm(self, serve):
        done = {
            "kind": "empty",
            "ok": True,
            "complete": True,
            "finished_at": "2026-09-30T09:00:00Z",
            "removed": [],
        }
        base = serve([HOUSEHOLD, done], empty_actual=Calls())

        assert "<form" in section_of(page_of(base))

    def test_EmptySection_LeavesOutABoundAccountActualDoesNotHold(self, serve):
        gone = {"account_id": "act-x", "name": "Vanished", "missing_account": True, "expected": 4}
        base = serve([audit(bound("act-pot", "Savings Pot", rows=40), gone)], empty_actual=Calls())

        form = empty_form(page_of(base))

        assert "Vanished" not in section_of(page_of(base))
        assert 'value="40:act-pot"' in form
        assert "act-x" not in form

    def test_EmptySection_WithAccountNamesContainingMarkup_EscapesThem(self, serve):
        hostile = audit(bound("act-1", "<script>alert(1)</script>", rows=3))
        base = serve([hostile], empty_actual=Calls())

        section = section_of(page_of(base))

        assert "<script>" not in section
        assert "&lt;script&gt;" in section

    def test_EmptySection_Page_ShowsNoAmountAndNoMoneyFigure(self, serve):
        base = serve([HOUSEHOLD], empty_actual=Calls())

        page = page_of(base)

        assert "amount" not in section_of(page).lower()
        assert "£" not in section_of(page)
        assert not re.search(r"\d+\.\d\d\b", section_of(page))

    def test_EmptySection_SitsBelowRemoveOrphanedImports(self, serve):
        base = serve([HOUSEHOLD], empty_actual=Calls(), prune_actual=Calls())

        page = page_of(base)

        assert page.index("Remove orphaned imports") < page.index("Empty Actual completely")

    def test_EmptyButton_IsADangerControlAndNotThePrimaryButton(self, serve):
        base = serve([HOUSEHOLD], empty_actual=Calls())

        form = empty_form(page_of(base))

        button = re.search(r"<button.*?</button>", form, re.S)
        assert button
        assert 'class="button"' not in button.group(0)
        assert 'class="button danger"' in button.group(0)
        assert "Empty Actual completely" in button.group(0)

    def test_EmptyForm_RequiresATickAndATypedPhrase(self, serve):
        base = serve([HOUSEHOLD], empty_actual=Calls())

        form = empty_form(page_of(base))

        assert 'name="confirm" value="yes" required' in form
        assert 'name="phrase"' in form and "required" in form
        assert EMPTY_PHRASE in form


class TestThePostThatQueuesAnEmpty:
    def test_Empty_WithTickPhraseAndTheNewestAuditsCounts_QueuesExactlyThoseCounts(self, serve):
        calls = Calls()
        base = serve([HOUSEHOLD], empty_actual=calls)

        response = press(base)

        assert response.status_code == 200
        assert calls.calls == [SHOWN]
        assert "Push to Actual now" in response.text
        assert "Nothing is pushed automatically" in response.text

    def test_Empty_WithThePhraseInOtherCaseAndSpacing_Queues(self, serve):
        calls = Calls()
        base = serve([HOUSEHOLD], empty_actual=calls)

        response = press(base, phrase="  EMPTY   actual ")

        assert response.status_code == 200
        assert calls.calls == [SHOWN]

    @pytest.mark.parametrize("missing", ["confirm", "phrase"])
    def test_Empty_WithoutTheTickOrThePhrase_IsRefusedAndQueuesNothing(self, serve, missing):
        calls = Calls()
        base = serve([HOUSEHOLD], empty_actual=calls)
        fields = good_form()
        del fields[missing]

        response = httpx.post(f"{base}/empty-actual", data=fields, timeout=20)

        assert response.status_code == 400
        assert calls.calls == []

    @pytest.mark.parametrize(
        "typed", ["", "empty", "empty actual please", "emptyActual", "SHOW REAL VALUES"]
    )
    def test_Empty_WithAWrongPhrase_IsRefusedAndQueuesNothing(self, serve, typed):
        calls = Calls()
        base = serve([HOUSEHOLD], empty_actual=calls)

        response = press(base, phrase=typed)

        assert response.status_code == 400
        assert "Phrase not typed" in response.text
        assert calls.calls == []

    def test_Empty_WhenTheTickIsAnythingButYes_IsRefused(self, serve):
        calls = Calls()
        base = serve([HOUSEHOLD], empty_actual=calls)

        response = press(base, confirm="on")

        assert response.status_code == 400
        assert calls.calls == []

    @pytest.mark.parametrize(
        "accounts",
        [
            ["121:act-current", "40:act-pot", "15:act-joint"],
            ["119:act-current", "40:act-pot", "15:act-joint"],
            ["120:act-current", "40:act-pot"],
            ["120:act-current", "40:act-pot", "15:act-joint", "1:act-extra"],
            ["120:act-current", "40:act-pot", "15:act-other"],
            [],
        ],
        ids=["more", "fewer", "account-dropped", "account-added", "account-swapped", "none"],
    )
    def test_Empty_WhenTheCountsPostedAreNotTheNewestAudits_IsRefusedAsOutOfDate(
        self, serve, accounts
    ):
        calls = Calls()
        base = serve([HOUSEHOLD], empty_actual=calls)

        response = press(base, account=accounts)

        assert response.status_code == 400
        assert "Out of date" in response.text
        assert calls.calls == []

    @pytest.mark.parametrize(
        "accounts",
        # The last is a fullwidth digit, which str.isdecimal() accepts and int() reads.
        [
            ["abc:act-current"],
            ["-1:act-current"],
            ["120"],
            ["120:"],
            ["1" + chr(0xFF12) + "0:act-current"],
        ],
    )
    def test_Empty_WithACountThatCannotBeRead_IsRefused(self, serve, accounts):
        calls = Calls()
        base = serve([HOUSEHOLD], empty_actual=calls)

        response = press(base, account=accounts)

        assert response.status_code == 400
        assert calls.calls == []

    def test_Empty_WithTheSameAccountPostedTwice_IsRefused(self, serve):
        calls = Calls()
        base = serve([HOUSEHOLD], empty_actual=calls)

        response = press(base, account=[*good_form()["account"], "120:act-current"])  # type: ignore[misc]

        assert response.status_code == 400
        assert calls.calls == []

    def test_Empty_WhenThereIsNoAudit_IsRefused(self, serve):
        calls = Calls()
        base = serve([], empty_actual=calls)

        response = press(base)

        assert response.status_code == 400
        assert "Run an audit first" in response.text
        assert calls.calls == []

    def test_Empty_WhenTheNewestAuditFailed_IsRefused(self, serve):
        calls = Calls()
        failed = {"kind": "audit", "ok": False, "finished_at": "2026-10-01T13:00:00Z", "error": "x"}
        base = serve([failed], empty_actual=calls)

        response = press(base)

        assert response.status_code == 400
        assert calls.calls == []

    def test_Empty_WhenAnEmptyFinishedAfterTheAudit_IsRefused(self, serve):
        calls = Calls()
        done = {
            "kind": "empty",
            "ok": True,
            "complete": True,
            "finished_at": "2026-10-02T09:00:00Z",
        }
        base = serve([HOUSEHOLD, done], empty_actual=calls)

        response = press(base)

        assert response.status_code == 400
        assert calls.calls == []

    def test_Empty_WhenTheAuditFoundNoAccounts_IsRefused(self, serve):
        calls = Calls()
        base = serve([audit()], empty_actual=calls)

        response = press(base, account=[])

        assert response.status_code == 400
        assert "Nothing to empty" in response.text
        assert calls.calls == []

    def test_Empty_WhenTheHookRefuses_ShowsItsSentenceAndSaysNothingWasQueued(self, serve):
        calls = Calls(refusal="a rebuild is replaying the store - try again later")
        base = serve([HOUSEHOLD], empty_actual=calls)

        response = press(base)

        assert response.status_code == 409
        assert "a rebuild is replaying the store" in response.text
        assert "Not queued" in response.text

    def test_Empty_WhenNoHookIsWired_Is404(self, serve):
        base = serve([HOUSEHOLD])

        assert press(base).status_code == 404

    def test_Empty_FromAnotherSite_IsRefusedAndQueuesNothing(self, serve):
        calls = Calls()
        base = serve([HOUSEHOLD], empty_actual=calls)

        response = httpx.post(
            f"{base}/empty-actual",
            data=good_form(),
            headers={"Origin": "https://evil.example", "Sec-Fetch-Site": "cross-site"},
            timeout=20,
        )

        assert response.status_code in {400, 403}
        assert calls.calls == []


@pytest.fixture
def actual_env(tmp_path, monkeypatch):
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
    (tmp_path / "actual" / "results").mkdir(parents=True)
    db = tmp_path / "s.sqlite3"
    with Store(db) as opened:
        _household(opened)
    return db, map_path, tmp_path / "actual"


def read_map(map_path: Path) -> dict[str, object]:
    return json.loads(map_path.read_text(encoding="utf-8"))


def write_result(actual_dir: Path, name: str, **fields: object) -> None:
    body = {"kind": "empty", "ok": True, "finished_at": "2026-10-02T09:00:00Z", **fields}
    (actual_dir / "results" / name).write_text(json.dumps(body), encoding="utf-8")


class TestTheQueuedRequest:
    def test_QueuedEmpty_CarriesOnlyWhatWasShownAndNamesNoObdiAccount(self, actual_env):
        db, _map, actual_dir = actual_env

        queue_actual_empty(db, SHOWN)

        (request,) = (actual_dir / "requests").glob("empty-*.json")
        sent = json.loads(request.read_text(encoding="utf-8"))
        assert sent == {"version": 3, "kind": "empty", "empty_accounts": SHOWN}

    def test_EmptyEnvelope_IsItsOwnKindAndDoesNotCarryAPushsPayload(self):
        envelope = build_empty_envelope({"a": 1})

        assert envelope["kind"] == "empty"
        assert "accounts" not in envelope and "transfers" not in envelope

    def test_QueuedEmpty_WhileARebuildIsInFlight_IsRefusedAndQueuesNothing(
        self, actual_env, monkeypatch
    ):
        db, _map, actual_dir = actual_env
        monkeypatch.setattr(cli, "rebuild_in_progress_note", lambda _p: "a rebuild is replaying")

        with pytest.raises(ValueError, match="a rebuild is replaying"):
            queue_actual_empty(db, SHOWN)

        assert not list((actual_dir / "requests").glob("*.json"))

    def test_QueuedEmpty_WhileAnotherRequestIsQueuedOrInProgress_IsRefusedAndQueuesNothing(
        self, actual_env
    ):
        db, _map, actual_dir = actual_env
        queue_push({"version": 3, "kind": "audit", "accounts": {}}, actual_dir, prefix="audit")

        with pytest.raises(
            ValueError, match=r"1 request to the process that applies requests to Actual is queued"
        ):
            queue_actual_empty(db, SHOWN)

        assert not list((actual_dir / "requests").glob("empty-*.json"))

    def test_QueuedEmpty_WhenAnEmptyIsAlreadyWaiting_RefusesTheSecondPress(self, actual_env):
        db, _map, actual_dir = actual_env
        queue_actual_empty(db, SHOWN)

        with pytest.raises(ValueError, match="queued or being worked on"):
            queue_actual_empty(db, SHOWN)

        assert len(list((actual_dir / "requests").glob("empty-*.json"))) == 1

    def test_QueuedEmpty_WhenActualIsNotConfigured_IsRefused(self, actual_env, monkeypatch):
        db, _map, actual_dir = actual_env
        monkeypatch.setenv("ACTUAL_SYNC_ID", "")

        with pytest.raises(ValueError, match="not configured"):
            queue_actual_empty(db, SHOWN)

        assert not (actual_dir / "requests").exists()

    def test_QueuedEmpty_WhenNothingWasShown_IsRefused(self, actual_env):
        db, _map, actual_dir = actual_env

        with pytest.raises(ValueError, match="No accounts"):
            queue_actual_empty(db, {})

        assert not (actual_dir / "requests").exists()


class TestWhatObdiDoesWhenTheApplierReportsAnEmpty:
    def test_SettleEmptied_AfterACompleteEmpty_ForgetsEveryActualLinkAndKeepsTheSourceBindings(
        self, actual_env
    ):
        _db, map_path, actual_dir = actual_env
        payload = read_map(map_path)
        payload["bindings"] = [{"account": "x", "canonical_id": MAIN}]
        map_path.write_text(json.dumps(payload), encoding="utf-8")
        write_result(actual_dir, "empty-1.json", complete=True)

        forgotten = settle_emptied_budgets(map_path, actual_dir)

        assert forgotten == 2
        after = read_map(map_path)
        assert after["actual"] == []
        assert after["bindings"] == [{"account": "x", "canonical_id": MAIN}]

    def test_SettleEmptied_WhenTheSameResultIsReadTwice_DoesNotForgetWhatALaterPushBound(
        self, actual_env
    ):
        _db, map_path, actual_dir = actual_env
        write_result(actual_dir, "empty-1.json", complete=True)
        first = settle_emptied_budgets(map_path, actual_dir)
        payload = read_map(map_path)
        payload["actual"] = [{"canonical_id": MAIN, "actual_account_id": "fresh-id"}]
        map_path.write_text(json.dumps(payload), encoding="utf-8")

        second = settle_emptied_budgets(map_path, actual_dir)

        assert first == 2
        assert second is None
        assert read_map(map_path)["actual"] == [
            {"canonical_id": MAIN, "actual_account_id": "fresh-id"}
        ]

    def test_SettleEmptied_WhenASecondCompleteEmptyArrives_ForgetsAgainOnceForIt(self, actual_env):
        _db, map_path, actual_dir = actual_env
        write_result(actual_dir, "empty-1.json", complete=True)
        settle_emptied_budgets(map_path, actual_dir)
        payload = read_map(map_path)
        payload["actual"] = [{"canonical_id": MAIN, "actual_account_id": "fresh-id"}]
        map_path.write_text(json.dumps(payload), encoding="utf-8")
        write_result(actual_dir, "empty-2.json", complete=True)

        again = settle_emptied_budgets(map_path, actual_dir)
        third = settle_emptied_budgets(map_path, actual_dir)

        assert again == 1
        assert third is None
        assert read_map(map_path)["actual"] == []

    @pytest.mark.parametrize(
        "fields",
        [
            {"complete": False, "stopped": "the server went away", "removed": [{"name": "A"}]},
            {"complete": False, "refused": "holds more than told"},
            {"ok": False, "error": "could not download"},
            {"complete": "yes"},
            {},
        ],
        ids=["partial", "refused", "failed", "complete-not-true", "no-verdict"],
    )
    def test_SettleEmptied_AfterAnEmptyThatWasNotComplete_KeepsEveryLink(
        self, actual_env, fields
    ):
        _db, map_path, actual_dir = actual_env
        before = read_map(map_path)
        write_result(actual_dir, "empty-1.json", **fields)

        forgotten = settle_emptied_budgets(map_path, actual_dir)

        assert forgotten is None
        assert read_map(map_path) == before
        assert not (actual_dir / EMPTY_SETTLED_FILE).exists()

    def test_SettleEmptied_AfterAPartialEmptyThenACompleteOne_ForgetsOnlyOnTheComplete(
        self, actual_env
    ):
        _db, map_path, actual_dir = actual_env
        write_result(actual_dir, "empty-1.json", complete=False, stopped="x")
        assert settle_emptied_budgets(map_path, actual_dir) is None
        assert len(read_map(map_path)["actual"]) == 2  # type: ignore[arg-type]

        write_result(actual_dir, "empty-2.json", complete=True)

        assert settle_emptied_budgets(map_path, actual_dir) == 2

    def test_SettleEmptied_WhenTheApplierMintedLinksNotYetMerged_ForgetsThoseToo(
        self, actual_env
    ):
        _db, map_path, actual_dir = actual_env
        (actual_dir / "bindings-pending.json").write_text(
            json.dumps([{"canonical_id": "extra", "actual_account_id": "act-extra"}]),
            encoding="utf-8",
        )
        write_result(actual_dir, "empty-1.json", complete=True)

        forgotten = settle_emptied_budgets(map_path, actual_dir)

        assert forgotten == 3
        assert read_map(map_path)["actual"] == []
        assert not (actual_dir / "bindings-pending.json").exists()

    def test_SettleEmptied_WhenTheRecordOfSettledResultsIsUnreadable_FailsLoudlyAndKeepsLinks(
        self, actual_env
    ):
        _db, map_path, actual_dir = actual_env
        before = read_map(map_path)
        write_result(actual_dir, "empty-1.json", complete=True)
        (actual_dir / EMPTY_SETTLED_FILE).write_text("{not json", encoding="utf-8")

        with pytest.raises(ValueError, match="cannot tell which emptied budgets"):
            settle_emptied_budgets(map_path, actual_dir)

        assert read_map(map_path) == before

    def test_SettleEmptied_WithNoResultsDirectory_DoesNothing(self, tmp_path):
        map_path = tmp_path / "accounts.json"
        map_path.write_text(json.dumps({"bindings": [], "actual": []}), encoding="utf-8")

        assert settle_emptied_budgets(map_path, tmp_path / "actual") is None

    def test_NextPush_AfterACompleteEmpty_ProvisionsEveryNamedAccountAfreshAndSaysSo(
        self, actual_env
    ):
        db, map_path, actual_dir = actual_env
        payload = read_map(map_path)
        payload["bindings"] = [
            {"canonical_id": MAIN, "source": "starling", "provider_account_id": "main-cat"},
            {"canonical_id": POT, "source": "starling", "provider_account_id": "pot-cat"},
        ]
        map_path.write_text(json.dumps(payload), encoding="utf-8")
        write_result(actual_dir, "empty-1.json", complete=True)

        summary = cli.queue_actual_push(db)

        (request,) = (actual_dir / "requests").glob("push-*.json")
        sent = json.loads(request.read_text(encoding="utf-8"))
        assert sorted(p["canonical_id"] for p in sent["provision"]) == [MAIN, POT]
        assert sent["accounts"] == {}
        assert "forgot 2 links" in summary
        assert read_map(map_path)["actual"] == []

    def test_NextPush_AfterAPartialEmptyAndAnAudit_StillUsesTheLinksItHad(self, actual_env):
        db, map_path, actual_dir = actual_env
        write_result(actual_dir, "empty-1.json", complete=False, stopped="stopped")
        (actual_dir / "results" / "audit-1.json").write_text(
            json.dumps(
                {"kind": "audit", "ok": True, "finished_at": "2026-10-02T10:00:00Z", "accounts": []}
            ),
            encoding="utf-8",
        )

        cli.queue_actual_push(db)

        (request,) = (actual_dir / "requests").glob("push-*.json")
        sent = json.loads(request.read_text(encoding="utf-8"))
        assert sorted(sent["accounts"]) == ["act-main", "act-pot"]
        assert len(read_map(map_path)["actual"]) == 2  # type: ignore[arg-type]

    def test_Audit_AfterACompleteEmpty_FindsNoDeadLinksToAudit(self, actual_env):
        db, map_path, actual_dir = actual_env
        write_result(actual_dir, "empty-1.json", complete=True)

        summary = cli.queue_actual_audit(db)

        assert "no Actual-bound accounts" in summary
        assert read_map(map_path)["actual"] == []

    def test_ActualStatusHook_ReadingAnEmptyResult_ForgetsTheLinksBeforeThePageShowsIt(
        self, actual_env, tmp_path, monkeypatch
    ):
        db, map_path, actual_dir = actual_env
        monkeypatch.setenv("OBDI_CONNECTION_STORE", str(tmp_path / "connections.json"))
        write_result(actual_dir, "empty-1.json", complete=True)
        config = cli.build_web_config(db)
        assert config is not None and config.actual_status is not None

        results = config.actual_status()

        assert [r["kind"] for r in results] == ["empty"]
        assert read_map(map_path)["actual"] == []


class TestAPushWhileAnEmptyIsPending:
    """Every push request is written by queue_actual_push (the CLI's push-actual,
    which the scheduler runs after a pull cycle, and the page's button both end
    there), so these go through it. A skipped push is a returned sentence, not
    an error: the scheduler's cycle must carry on."""

    def pushes(self, actual_dir: Path) -> list[Path]:
        return list((actual_dir / "requests").glob("push-*.json"))

    def test_Push_WhileAnEmptyIsQueued_IsSkippedWithASentenceAndWritesNoRequest(self, actual_env):
        db, map_path, actual_dir = actual_env
        before = read_map(map_path)
        queue_actual_empty(db, SHOWN)

        said = cli.queue_actual_push(db)

        assert "empty of Actual is pending" in said
        assert "push was skipped" in said
        assert self.pushes(actual_dir) == []
        assert read_map(map_path) == before

    def test_Push_WhileAnEmptyIsBeingWorkedOn_IsSkippedAndWritesNoRequest(self, actual_env):
        db, _map, actual_dir = actual_env
        queue_actual_empty(db, SHOWN)
        (name,) = [p.name for p in (actual_dir / "requests").glob("empty-*.json")]
        (actual_dir / "processing.json").write_text(
            json.dumps({"name": name, "started_at": "2026-10-02T09:00:00"}), encoding="utf-8"
        )

        said = cli.queue_actual_push(db)

        assert "empty of Actual is pending" in said
        assert self.pushes(actual_dir) == []

    def test_Push_AfterACompleteEmptyThatIsNotYetSettled_SettlesFirstThenProvisionsAfresh(
        self, actual_env
    ):
        db, map_path, actual_dir = actual_env
        payload = read_map(map_path)
        payload["bindings"] = [
            {"canonical_id": MAIN, "source": "starling", "provider_account_id": "main-cat"},
            {"canonical_id": POT, "source": "starling", "provider_account_id": "pot-cat"},
        ]
        map_path.write_text(json.dumps(payload), encoding="utf-8")
        write_result(actual_dir, "empty-1.json", complete=True)

        said = cli.queue_actual_push(db)

        (request,) = self.pushes(actual_dir)
        sent = json.loads(request.read_text(encoding="utf-8"))
        assert sorted(p["canonical_id"] for p in sent["provision"]) == [MAIN, POT]
        assert sent["accounts"] == {}
        assert "forgot 2 links" in said

    def test_Push_AfterACompleteEmptyWhileAnotherEmptyIsQueued_SettlesTheOldOneButIsSkipped(
        self, actual_env
    ):
        db, map_path, actual_dir = actual_env
        write_result(actual_dir, "empty-1.json", complete=True)
        queue_actual_empty(db, SHOWN)

        said = cli.queue_actual_push(db)

        assert "empty of Actual is pending" in said
        assert self.pushes(actual_dir) == []
        assert read_map(map_path)["actual"] == []

    def test_Push_AfterAPartialEmpty_IsRefusedAsAPartialStateAndKeepsTheLinks(self, actual_env):
        db, map_path, actual_dir = actual_env
        before = read_map(map_path)
        write_result(
            actual_dir,
            "empty-1.json",
            complete=False,
            stopped="Current Account: the delete failed",
            removed=[{"account_id": "act-pot", "name": "Savings Pot", "rows": 3}],
        )

        said = cli.queue_actual_push(db)

        assert "partial state" in said
        assert "run an audit" in said.lower()
        assert self.pushes(actual_dir) == []
        assert read_map(map_path) == before

    def test_Push_AfterAFailedEmpty_IsRefusedUntilAnAuditHasLookedAgain(self, actual_env):
        db, _map, actual_dir = actual_env
        write_result(actual_dir, "empty-1.json", ok=False, error="could not download")

        said = cli.queue_actual_push(db)

        assert "partial state" in said
        assert self.pushes(actual_dir) == []

    def test_Push_AfterAPartialEmptyAndALaterAudit_IsQueuedAsBefore(self, actual_env):
        db, _map, actual_dir = actual_env
        write_result(actual_dir, "empty-1.json", complete=False, stopped="x")
        (actual_dir / "results" / "audit-1.json").write_text(
            json.dumps(
                {
                    "kind": "audit",
                    "ok": True,
                    "finished_at": "2026-10-02T10:00:00Z",
                    "accounts": [],
                }
            ),
            encoding="utf-8",
        )

        cli.queue_actual_push(db)

        assert len(self.pushes(actual_dir)) == 1

    def test_Push_AfterARefusedEmpty_IsQueuedBecauseNothingChanged(self, actual_env):
        db, _map, actual_dir = actual_env
        write_result(actual_dir, "empty-1.json", complete=False, refused="holds more than told")

        cli.queue_actual_push(db)

        assert len(self.pushes(actual_dir)) == 1

    def test_Push_WithNoEmptyAnywhere_IsQueuedAsBefore(self, actual_env):
        db, _map, actual_dir = actual_env

        cli.queue_actual_push(db)

        assert len(self.pushes(actual_dir)) == 1

    def test_PushActualCommand_WhenSkippedForAPendingEmpty_PrintsTheSentenceAndExitsCleanly(
        self, actual_env, capsys
    ):
        db, _map, actual_dir = actual_env
        queue_actual_empty(db, SHOWN)

        code = cli._push_actual(db)

        assert code == 0
        assert "empty of Actual is pending" in capsys.readouterr().out
        assert self.pushes(actual_dir) == []


class TestTheResultRow:
    def test_ResultRow_ForACompleteEmpty_SaysWhatWentThatLinksWereForgottenAndAPushRebuilds(
        self,
    ):
        row = empty_result_row(
            {
                "kind": "empty",
                "ok": True,
                "complete": True,
                "finished_at": "2026-10-02T09:00:00Z",
                "accounts_removed": 2,
                "rows_removed": 160,
                "removed": [
                    {"account_id": "a", "name": "Current Account", "rows": 120},
                    {"account_id": "b", "name": "Savings Pot", "rows": 40},
                ],
            }
        )

        said = text_of(row)
        assert "Actual emptied (2 accounts, 160 rows removed)" in said
        assert "Current Account: 120 rows removed" in said
        assert "links to Actual accounts were forgotten" in said
        assert "Push to Actual now" in said
        assert "pill-ok" in row

    def test_ResultRow_ForAStoppedEmpty_SaysItIsPartialNamesWhatIsLeftAndKeepsTheLinks(self):
        row = empty_result_row(
            {
                "kind": "empty",
                "ok": True,
                "complete": False,
                "finished_at": "2026-10-02T09:00:00Z",
                "accounts_removed": 1,
                "rows_removed": 40,
                "removed": [{"account_id": "b", "name": "Savings Pot", "rows": 40}],
                "remaining": [{"account_id": "a", "name": "Current Account"}],
                "stopped": "Current Account (120 rows): the delete failed (server went away)",
            }
        )

        said = text_of(row)
        assert "partial state (1 account removed)" in said
        assert "still in Actual: Current Account" in said
        assert "server went away" in said
        assert "kept its links" in said
        assert "run an audit" in said.lower()
        assert "pill-bad" in row
        assert "forgotten" not in said

    def test_ResultRow_ForARefusedEmpty_SaysNothingChangedAndKeepsTheLinks(self):
        row = empty_result_row(
            {
                "kind": "empty",
                "ok": True,
                "complete": False,
                "finished_at": "2026-10-02T09:00:00Z",
                "refused": "nothing was changed: the budget holds more than you were shown",
                "removed": [],
            }
        )

        said = text_of(row)
        assert "empty refused: nothing was changed" in said
        assert "kept its links" in said
        assert "forgotten" not in said

    def test_ResultRow_ForAFailedEmpty_ShowsTheError(self):
        row = empty_result_row(
            {"kind": "empty", "ok": False, "finished_at": "2026-10-02T09:00:00Z", "error": "boom"}
        )

        assert "empty failed" in row and "boom" in row

    def test_ResultRow_WithMarkupInAnAccountName_EscapesIt(self):
        row = empty_result_row(
            {
                "kind": "empty",
                "ok": True,
                "complete": True,
                "finished_at": "2026-10-02T09:00:00Z",
                "removed": [{"account_id": "a", "name": "<b>x</b>", "rows": 1}],
            }
        )

        assert "<b>x</b>" not in row

    def test_Progress_WhenTheApplierIsEmptying_IsBelieved(self):
        assert valid_progress({"phase": "emptying", "done": 1, "total": 4}) == {
            "phase": "emptying",
            "done": 1,
            "total": 4,
        }

    def test_EmptyJobInTheQueue_IsNamedAsAnEmptyNotAnUnknownKind(self, serve, actual_env):
        _db, _map, actual_dir = actual_env
        queue_push(build_empty_envelope({"a": 1}), actual_dir, prefix="empty")
        base = serve(
            [],
            empty_actual=Calls(),
            actual_queue=lambda: __import__(
                "obdi.export.actual_push", fromlist=["x"]
            ).queued_requests(actual_dir),
        )

        page = page_of(base)

        assert "queued (empty)" in page
        assert "unknown kind" not in page


class TestThePlanFromTheAudit:
    def test_PlanFromAudit_CountsEveryAccountAndSplitsHandRowsFromUnsortedOnes(self):
        plan, why = plan_from_audit(HOUSEHOLD)

        assert why is None and plan is not None
        assert plan.total_rows == 175
        assert plan.human_rows == 7
        assert plan.unclassified_rows == 15
        assert plan.shown() == SHOWN

    def test_PlanFromAudit_WithABoundAccountThatLacksItsHandCount_GivesNoPlan(self):
        entry = bound("act-1", "A", rows=3)
        del entry["human"]

        plan, why = plan_from_audit(audit(entry))

        assert plan is None and why

    def test_PlanFromAudit_WithAnUnusableRowCount_GivesNoPlan(self):
        for rows in (None, "12", True, -1, 1.5):
            entry = bound("act-1", "A", rows=3)
            entry["rows"] = rows

            plan, why = plan_from_audit(audit(entry))

            assert plan is None and why, rows

    def test_EmptySection_RenderedWithManyAccounts_ListsThemAll(self):
        many = audit(*[bound(f"act-{n}", f"Account {n}", rows=n + 1) for n in range(40)])
        plan, why = plan_from_audit(many)

        html_text = empty_section(plan, why)

        assert html_text.count("<li") == 40
        assert html_text.count('name="account"') == 40
