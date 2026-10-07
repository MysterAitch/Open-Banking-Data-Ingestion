"""Several sources state a balance for one day, and one of them can be disregarded alone.

THE ACCOUNT. `card` holds two Santander statements, closing 2026-05-10 and 2026-06-10, each opening
on the balance the one before closed on. June's closing is 115.00 owed (a negative position,
-11500). For 2026-06-10 a person then states a balance of their own, the figure in `STATED`:

  equal      115.00 owed: the same as the statement's closing. Two sources on 2026-06-10, equal.
  differs    120.00 owed: five pounds from the statement. Two sources on 2026-06-10, different,
             which is a conflict between sources, held at 2026-06-10.

Disregarding the statement's closing for that day leaves the stated balance in force and the
conflict gone; disregarding the stated one leaves the statement. Neither removes the other, and
neither is deleted: the disregarded balance stays on the page, marked, with a way to use it again.
Every answer was written before the first run.
"""

from __future__ import annotations

import threading
from datetime import date
from html.parser import HTMLParser
from http.server import HTTPServer

import httpx
import pytest

from obdi.cli import build_web_config
from obdi.core.errors import DataError
from obdi.ingest.rebuild import rebuild_from_raw
from obdi.ingest.statement_terms import keep_statement_readings
from obdi.ingest.store import SCHEMA_VERSION, Store
from obdi.pages.web import AuthorisationSession, ConnectionHandler
from obdi.verify.agreement import HELD_CONFLICT, derive_agreement, known_of_opening
from obdi.verify.balance_anchors import (
    STATEMENT,
    AnchorRefused,
    disregard_balance,
    effective_opening,
    record_stated_anchor,
    use_balance_again,
)
from statement_span_world import Spend, statement

D = date
ACCOUNT = "card"
DAY = D(2026, 6, 10)
SOURCE = "santander-cc-pdf"
DIFFERENT = "-120.00"
EQUAL = "-115.00"


def world(store: Store, root, stated: str) -> None:
    may = statement(
        store, root, ACCOUNT, D(2026, 5, 10), 10000, [Spend(D(2026, 5, 4), "Aaa Shop", 1000)],
        received=D(2026, 5, 10), previous_close=D(2026, 4, 10),
    )
    statement(
        store, root, ACCOUNT, DAY, may, [Spend(D(2026, 6, 4), "Bbb Shop", 500)],
        received=DAY, previous_close=D(2026, 5, 10),
    )
    keep_statement_readings(store)
    record_stated_anchor(store, ACCOUNT, DAY.isoformat(), stated)
    store.connection.commit()


@pytest.fixture(params=[DIFFERENT, EQUAL], ids=["differs", "equal"])
def db(request, tmp_path):
    path = tmp_path / "store.sqlite3"
    with Store(path) as store:
        world(store, tmp_path, request.param)
    return path, request.param


def agreement(store: Store):
    return derive_agreement(known_of_opening(effective_opening(store, ACCOUNT)), ())


class Forms(HTMLParser):
    """The forms of a page: action, hidden inputs, and the button's words."""

    def __init__(self) -> None:
        super().__init__()
        self.forms: list[dict[str, object]] = []
        self.texts: list[str] = []
        self._button = False

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if tag == "form":
            self.forms.append({"action": values.get("action"), "fields": {}, "button": ""})
        elif tag == "input" and self.forms:
            fields = self.forms[-1]["fields"]
            assert isinstance(fields, dict)
            fields[values.get("name")] = values.get("value")
        elif tag == "button":
            self._button = True

    def handle_endtag(self, tag):
        if tag == "button":
            self._button = False

    def handle_data(self, data):
        if self._button and self.forms:
            self.forms[-1]["button"] = str(self.forms[-1]["button"]) + data
        if data.strip():
            self.texts.append(data.strip())


def page_of(text: str) -> Forms:
    parsed = Forms()
    parsed.feed(text)
    return parsed


class TestTheDomainRule:
    def test_Disregard_WhenTwoSourcesDisagreeOnADay_OnlyTheNamedOneLeavesTheReading(self, db):
        path, stated = db
        with Store(path) as store:
            if stated == DIFFERENT:
                assert agreement(store).state == HELD_CONFLICT

            assert disregard_balance(store, ACCOUNT, DAY.isoformat(), SOURCE, STATEMENT)

            reading = effective_opening(store, ACCOUNT)
            on_day = [r.anchor.basis for r in reading.readings if r.anchor.day == DAY]
            assert on_day == ["stated"], "the statement's closing is out, the stated balance stays"
            assert [(a.day, a.stating, a.basis) for a in reading.disregarded] == [
                (DAY, SOURCE, STATEMENT)
            ]
            assert agreement(store).conflicts == ()

    def test_Disregard_WhenTheStatedBalanceIsDisregarded_TheStatementStays(self, db):
        path, _ = db
        with Store(path) as store:
            assert disregard_balance(store, ACCOUNT, DAY.isoformat(), "stated", "stated")

            on_day = [
                r.anchor.basis
                for r in effective_opening(store, ACCOUNT).readings
                if r.anchor.day == DAY
            ]

            assert on_day == [STATEMENT]

    def test_Disregard_WhenTheOnlyBalanceOfThatKeyIsAlreadyDisregarded_IsRefused(self, db):
        path, _ = db
        with Store(path) as store:
            assert disregard_balance(store, ACCOUNT, DAY.isoformat(), SOURCE, STATEMENT)
            with pytest.raises(AnchorRefused):
                disregard_balance(store, ACCOUNT, DAY.isoformat(), SOURCE, STATEMENT)
            assert len(store.disregarded_balance_rows(ACCOUNT)) == 1

    def test_UseAgain_RestoresTheBalanceAndTheConflict(self, db):
        path, stated = db
        with Store(path) as store:
            disregard_balance(store, ACCOUNT, DAY.isoformat(), SOURCE, STATEMENT)

            assert use_balance_again(store, ACCOUNT, DAY.isoformat(), SOURCE, STATEMENT)

            assert effective_opening(store, ACCOUNT).disregarded == ()
            assert (agreement(store).state == HELD_CONFLICT) == (stated == DIFFERENT)
            assert not use_balance_again(store, ACCOUNT, DAY.isoformat(), SOURCE, STATEMENT)

    def test_Disregard_AfterARebuildFromRaw_IsStillDisregarded(self, db):
        path, _ = db
        with Store(path) as store:
            disregard_balance(store, ACCOUNT, DAY.isoformat(), SOURCE, STATEMENT)
            rebuild_from_raw(store)
            keep_statement_readings(store)
            store.connection.commit()

            reading = effective_opening(store, ACCOUNT)

            assert [(a.day, a.basis) for a in reading.disregarded] == [(DAY, STATEMENT)]
            assert agreement(store).conflicts == ()

    @pytest.mark.parametrize(
        ("day", "source", "basis"),
        [
            ("2026-06-11", SOURCE, STATEMENT),
            (DAY.isoformat(), "no-such-source", STATEMENT),
            (DAY.isoformat(), SOURCE, "no-such-basis"),
            ("not-a-day", SOURCE, STATEMENT),
        ],
    )
    def test_Disregard_WhenNoSuchBalanceIsHeld_IsRefusedAndWritesNothing(
        self, db, day, source, basis
    ):
        path, _ = db
        with Store(path) as store, pytest.raises(DataError):
            disregard_balance(store, ACCOUNT, day, source, basis)
        with Store(path) as store:
            assert store.disregarded_balance_keys(ACCOUNT) == []

    def test_Disregard_WhenTheAccountIsUnknown_IsRefused(self, db):
        path, _ = db
        with Store(path) as store, pytest.raises(AnchorRefused):
            disregard_balance(store, "no-such-account", DAY.isoformat(), SOURCE, STATEMENT)

    def test_Disregard_MovesTheStandingEpoch_SoCachedStandingsAreRead_Again(self, db):
        path, _ = db
        with Store(path) as store:
            before = store.standing_epoch()

            disregard_balance(store, ACCOUNT, DAY.isoformat(), SOURCE, STATEMENT)

            assert store.standing_epoch() > before

    def test_Disregard_OnlyChangesTheNamedAccount(self, db):
        path, _ = db
        with Store(path) as store:
            disregard_balance(store, ACCOUNT, DAY.isoformat(), SOURCE, STATEMENT)

            assert store.disregarded_balance_keys("another-account") == []


@pytest.fixture
def served(db, tmp_path, monkeypatch):
    monkeypatch.setenv("OBDI_CONNECTION_STORE", str(tmp_path / "connections.json"))
    monkeypatch.delenv("OBDI_ACCOUNT_MAP", raising=False)
    for variable in ("TRUELAYER_CLIENT_ID", "TRUELAYER_CLIENT_SECRET_FILE"):
        monkeypatch.delenv(variable, raising=False)
    config = build_web_config(db[0])
    assert config is not None
    handler = type(
        "H", (ConnectionHandler,), {"config": config, "session": AuthorisationSession()}
    )
    httpd = HTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{httpd.server_port}", db[1]
    finally:
        httpd.shutdown()


def ledger(base: str) -> httpx.Response:
    return httpx.get(f"{base}/ledger", params={"ref": ACCOUNT, "month": "2026-06"}, timeout=60)


def post(base: str, path: str, **fields: str) -> httpx.Response:
    return httpx.post(f"{base}{path}", data=fields, timeout=60)


FIELDS = {
    "ref": ACCOUNT,
    "month": "2026-06",
    "day": DAY.isoformat(),
    "source": SOURCE,
    "basis": STATEMENT,
}


class TestThePageShowsTheSourcesTogether:
    def test_Page_ForADayWithTwoSources_NamesEachSourceAndSaysWhetherTheFiguresAreEqual(
        self, served
    ):
        base, stated = served
        page = page_of(ledger(base).text)

        said = " ".join(page.texts)

        assert DAY.isoformat() in said
        assert (
            "The figures are equal." if stated == EQUAL else "The figures differ."
        ) in said
        assert SOURCE in page.texts and "stated" in page.texts

    def test_Page_OnAGet_NamesNoFigureOfTheDay(self, served):
        base, _ = served
        text = ledger(base).text

        for figure in ("115.00", "120.00", "11500", "12000", "110.00"):
            assert figure not in text

    def test_Page_OffersToDisregardEachBalanceOfThatDayByDaySourceAndBasis(self, served):
        base, _ = served
        forms = [
            f for f in page_of(ledger(base).text).forms
            if f["action"] == "/ledger-balance-disregard"
        ]

        on_day = [f["fields"] for f in forms if f["fields"]["day"] == DAY.isoformat()]
        assert {(f["source"], f["basis"]) for f in on_day} == {
            (SOURCE, STATEMENT),
            ("stated", "stated"),
        }
        assert all(f["ref"] == ACCOUNT for f in on_day)


class TestDisregardingThroughThePage:
    def test_Disregard_WhenFirstPressed_AsksAndChangesNothing(self, served, db):
        base, _ = served

        asked = post(base, "/ledger-balance-disregard", **FIELDS)

        assert asked.status_code == 200 and "Are you sure?" in asked.text
        with Store(db[0]) as store:
            assert store.disregarded_balance_keys(ACCOUNT) == []

    def test_Disregard_Question_NamesTheSourceAndTheDayButNoFigure(self, served):
        base, _ = served

        asked = post(base, "/ledger-balance-disregard", **FIELDS).text

        assert f"the balance {SOURCE} states for the end of {DAY.isoformat()}" in asked
        for figure in ("115.00", "120.00", "11500", "12000"):
            assert figure not in asked

    def test_Disregard_WhenConfirmed_KeepsTheBalanceOnThePageMarkedAndOffersToUseItAgain(
        self, served, db
    ):
        base, _ = served

        done = post(base, "/ledger-balance-disregard", confirmed="yes", **FIELDS)

        assert done.status_code == 200
        assert f"Disregarded: the balance {SOURCE} states for the end of {DAY.isoformat()}." in (
            done.text
        )
        page = page_of(ledger(base).text)
        assert "disregarded" in page.texts
        again = [f for f in page.forms if f["action"] == "/ledger-balance-use-again"]
        assert [f["fields"]["source"] for f in again] == [SOURCE]

    def test_UseAgain_ReadsTheBalanceAsBefore(self, served, db):
        base, _ = served
        post(base, "/ledger-balance-disregard", confirmed="yes", **FIELDS)

        done = post(base, "/ledger-balance-use-again", **FIELDS)

        assert done.status_code == 200
        with Store(db[0]) as store:
            assert store.disregarded_balance_keys(ACCOUNT) == []

    def test_Disregard_WhenTheBalanceIsNotHeld_IsRefused(self, served, db):
        base, _ = served

        refused = post(
            base, "/ledger-balance-disregard", confirmed="yes", **{**FIELDS, "source": "nobody"}
        )

        assert refused.status_code == 400
        with Store(db[0]) as store:
            assert store.disregarded_balance_keys(ACCOUNT) == []

    def test_Disregard_WhenTheDayIsMalformed_IsRefusedWithoutAsking(self, served):
        base, _ = served

        refused = post(base, "/ledger-balance-disregard", **{**FIELDS, "day": "tomorrow"})

        assert refused.status_code == 400 and "Are you sure?" not in refused.text


class TestASchemaVersion19StoreMovesTo20:
    def test_Store_StampedVersion19WithoutTheTable_GrowsItOnOpenAndKeepsItsData(self, tmp_path):
        path = tmp_path / "old.sqlite3"
        with Store(path) as store:
            store.connection.execute("DROP TABLE disregarded_balances")
            store.connection.execute(
                "UPDATE obdi_meta SET value = '19' WHERE key = 'schema_version'"
            )
            store.connection.commit()

        with Store(path) as store:
            version = store.connection.execute(
                "SELECT value FROM obdi_meta WHERE key = 'schema_version'"
            ).fetchone()
            assert str(version[0]) == str(SCHEMA_VERSION)
            assert store.disregard_balance(ACCOUNT, DAY, SOURCE, STATEMENT, -11500)
            assert store.disregarded_balance_keys(ACCOUNT) == [(DAY, SOURCE, STATEMENT)]
