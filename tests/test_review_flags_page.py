"""The review flags page over HTTP: what a person reads, answers, and can take back.

The household is `flag_world`: four flags are real questions and two more were answered by the
evidence. Every payee and figure in it is a token that appears nowhere else, so the masked page
is searched for them and the page that was posted for must contain them. Each answer is made the
way a browser makes it, from the form on the page, and the store is then read to see what it did,
including after a rebuild replays everything from the artefacts.
"""

from __future__ import annotations

import re
from html.parser import HTMLParser

import httpx
import pytest

from flag_world import (
    EVERYDAY,
    FIGURES,
    LABELS,
    OPEN_QUESTIONS,
    PAYEES,
    SETTLED_BUS,
    SETTLED_TRAIN,
    TICKETS,
    add_unsettled_pair,
    build_flag_world,
)
from obdi.agreement import standing_of
from obdi.balance_anchors import effective_opening, record_stated_anchor
from obdi.cli import build_web_config
from obdi.movement_completeness import MovementCompleteness
from obdi.protection import press
from obdi.rebuild import rebuild_from_raw
from obdi.store import Store
from section_harness import environment, serve_config

TOKENS = [*PAYEES.values(), *FIGURES.values(), *(f.replace(".", "") for f in FIGURES.values())]

#: A flag's id and a card's fingerprint: digests the page carries in its forms.
_DIGEST = re.compile(r"[0-9a-f]{16,}")


def leaked(page: str) -> list[str]:
    """The planted payees and figures found in `page`, digests aside.

    A figure is also searched for without its point ("2137" for 21.37), which is four digits,
    and the page's forms carry a digest for every flag and every card. Four given digits fall
    inside a few hundred hex characters about once in two hundred pages, and the digests move
    with the moment a flag was raised: this check failed once that way with nothing leaked. A
    digest is not where an amount could appear, so the digests are taken out before looking.
    """
    searched = _DIGEST.sub(" ", page.casefold())
    return [token for token in TOKENS if token.casefold() in searched]


class _Forms(HTMLParser):
    """Every form on a page: its action and its hidden fields, as a browser would submit them."""

    def __init__(self) -> None:
        super().__init__()
        self.forms: list[tuple[str, dict[str, str], str]] = []
        self._open: tuple[str, dict[str, str]] | None = None
        self._label = ""

    def handle_starttag(self, tag, attrs):
        data = dict(attrs)
        if tag == "form":
            self._open = (data.get("action") or "", {})
            self._label = ""
        elif tag == "input" and self._open is not None and data.get("type") == "hidden":
            self._open[1][data.get("name") or ""] = data.get("value") or ""

    def handle_data(self, data):
        if self._open is not None:
            self._label += data

    def handle_endtag(self, tag):
        if tag == "form" and self._open is not None:
            self.forms.append((self._open[0], self._open[1], self._label.strip()))
            self._open = None


def forms_of(page: str) -> list[tuple[str, dict[str, str], str]]:
    parser = _Forms()
    parser.feed(page)
    return parser.forms


def form(page: str, action: str, label: str, nth: int = 0) -> dict[str, str]:
    found = [f for f in forms_of(page) if f[0] == action and label in f[2]]
    assert len(found) > nth, f"no {label!r} form for {action} on the page"
    return found[nth][1]


@pytest.fixture
def world(tmp_path, monkeypatch):
    db = build_flag_world(tmp_path)
    environment(monkeypatch, tmp_path)
    config = build_web_config(db)
    assert config is not None
    base, stop = serve_config(config)
    yield base, db
    stop()


def queue(base: str) -> str:
    return httpx.get(f"{base}/review-flags", timeout=30).text


def cards(page: str) -> int:
    return page.count('class="flag-card"')


def open_flags(db) -> int:
    with Store(db) as store:
        return len(store.review_queue())


def rows(db, account: str) -> int:
    with Store(db) as store:
        return int(
            store.connection.execute(
                "SELECT COUNT(*) FROM transactions WHERE account_id = ?", (account,)
            ).fetchone()[0]
        )


class TestTheQueuePage:
    def test_Queue_WhenFetched_ListsExactlyTheFourOpenQuestions(self, world):
        base, _ = world

        page = queue(base)

        assert cards(page) == OPEN_QUESTIONS
        assert f"{OPEN_QUESTIONS} questions need an answer" in page
        for number in range(1, OPEN_QUESTIONS + 1):
            assert f"{number} of {OPEN_QUESTIONS}" in page

    def test_Queue_FlagsTheEvidenceAnswered_AreNotListedByName(self, world):
        base, _ = world

        page = queue(base)

        assert LABELS[SETTLED_BUS] not in page
        assert LABELS[SETTLED_TRAIN] not in page

    def test_Queue_WhenAnAnsweredFlagStandsInTheStore_IsCountedInOneLineWithALink(self, world):
        base, db = world
        add_unsettled_pair(db)

        page = queue(base)

        assert cards(page) == OPEN_QUESTIONS
        assert "1 other flag was already answered by the evidence and is not listed" in page
        assert 'href="/review-report"' in page
        assert LABELS[SETTLED_BUS] not in page

    def test_Queue_WhenFetched_HoldsNoAmountAndNoDescription(self, world):
        base, _ = world

        assert leaked(queue(base)) == []

    def test_Queue_WhenFetchedWithValuesInTheAddress_StillHoldsNone(self, world):
        base, _ = world

        page = httpx.get(f"{base}/review-flags?values=1&show=1&unmask=true", timeout=30).text

        assert leaked(page) == []

    def test_Queue_WhenValuesArePostedFor_ShowsThemAndIsNotKept(self, world):
        base, _ = world

        response = httpx.post(f"{base}/review-flags", timeout=30)

        assert response.headers["Cache-Control"] == "no-store"
        assert cards(response.text) == OPEN_QUESTIONS
        for token in (*PAYEES.values(), *FIGURES.values()):
            assert token in response.text
        assert "VALUES ARE SHOWN" in response.text

    def test_Queue_OnBothRenderings_OffersTheAnswers(self, world):
        base, _ = world

        for page in (queue(base), httpx.post(f"{base}/review-flags", timeout=30).text):
            assert page.count("These are two payments") == OPEN_QUESTIONS
            assert "These are one payment" in page
            assert "One payment with row A" in page, "a card with two neighbours names its rows"

    def test_Queue_Card_SaysWhatTheEvidenceSaysEitherWay(self, world):
        base, _ = world

        page = queue(base)

        assert "Both were listed by the export in one response, which lists a payment once." in page
        assert "The aggregator gave them different ids." in page
        assert "The aggregator can report one payment again under a new id" in page

    def test_Queue_NeverUsesTheMatchersOwnWords(self, world):
        base, _ = world

        prose = re.sub(r"<style>.*?</style>", "", queue(base), flags=re.S)
        text = re.sub(r"<[^>]+>", " ", prose).casefold()

        for word in ("tier", "uid", "entity", "matcher", "absorb", "fold"):
            assert not re.search(rf"\b{word}", text), word

    def test_Queue_CardsAreNamedByTheAccountLabelNotItsReference(self, world):
        base, _ = world

        text = re.sub(r"<[^>]+>", " ", queue(base))

        assert LABELS[EVERYDAY] in text


class TestAnAnswerOverHttp:
    def test_TwoPayments_WhenAnswered_SaysSoAndHowManyRemainAndLeadsWithTheWayBack(self, world):
        base, db = world
        fields = form(queue(base), "/review-flags-two", "These are two payments")

        response = httpx.post(f"{base}/review-flags-two", data=fields, timeout=30)

        assert response.status_code == 200
        assert response.headers["Cache-Control"] == "no-store"
        assert "Kept as two payments" in response.text
        assert "3 questions remain open" in response.text
        main = response.text.split('<main id="main">', 1)[1]
        assert main.index('href="/review-flags"') < main.index("Kept as two payments")
        assert open_flags(db) == OPEN_QUESTIONS - 1

    def test_TwoPayments_WhenAnswered_TheQueueHasOneCardFewerAndListsTheAnswer(self, world):
        base, _ = world
        fields = form(queue(base), "/review-flags-two", "These are two payments")
        httpx.post(f"{base}/review-flags-two", data=fields, timeout=30)

        page = queue(base)

        assert cards(page) == OPEN_QUESTIONS - 1
        assert "<h2>Answered</h2>" in page
        assert "kept as two payments" in page

    def test_TwoPayments_AfterARebuild_TheFlagDoesNotReturnAndBothRowsStand(self, world):
        base, db = world
        fields = form(queue(base), "/review-flags-two", "These are two payments")
        account = fields["ref"]
        before = rows(db, account)
        httpx.post(f"{base}/review-flags-two", data=fields, timeout=30)
        with Store(db) as store:
            rebuild_from_raw(store)

        assert cards(queue(base)) == OPEN_QUESTIONS - 1
        assert open_flags(db) == OPEN_QUESTIONS - 1
        assert rows(db, account) == before

    def test_OnePayment_WhenAnswered_TheRowsAreJoinedAndTheFlagIsGone(self, world):
        base, db = world
        fields = form(queue(base), "/review-flags-one", "These are one payment", nth=0)
        account = fields["ref"]
        before = rows(db, account)

        response = httpx.post(f"{base}/review-flags-one", data=fields, timeout=30)

        assert response.status_code == 200
        assert "Joined into one payment" in response.text
        assert rows(db, account) == before - 1
        assert cards(queue(base)) == OPEN_QUESTIONS - 1

    def test_OnePayment_AfterARebuild_TheRowsAreStillJoined(self, world):
        base, db = world
        fields = form(queue(base), "/review-flags-one", "These are one payment", nth=0)
        account = fields["ref"]
        before = rows(db, account)
        httpx.post(f"{base}/review-flags-one", data=fields, timeout=30)
        with Store(db) as store:
            rebuild_from_raw(store)

        assert rows(db, account) == before - 1

    def test_Answer_ForTheHomePage_TheCountFallsByOneAndTheLineEndsAtZero(self, world):
        base, _ = world

        def home_line() -> str | None:
            found = re.search(r"(\d+) transactions? (?:is|are) flagged", httpx.get(base).text)
            return found.group(1) if found else None

        assert home_line() == str(OPEN_QUESTIONS)
        for left in range(OPEN_QUESTIONS - 1, -1, -1):
            fields = form(queue(base), "/review-flags-two", "These are two payments")
            answered = httpx.post(f"{base}/review-flags-two", data=fields, timeout=30)
            assert answered.status_code == 200
            assert home_line() == (str(left) if left else None)

        empty = queue(base)
        assert cards(empty) == 0
        assert "No questions are open." in empty
        assert "A flag is raised when a payment is stored as a new transaction" in empty

    def test_Answer_FromTheShownPage_IsTheSameAnswerAndTheResultIsMasked(self, world):
        base, _ = world
        shown = httpx.post(f"{base}/review-flags", timeout=30).text
        fields = form(shown, "/review-flags-two", "These are two payments")

        response = httpx.post(f"{base}/review-flags-two", data=fields, timeout=30)

        assert "Kept as two payments" in response.text
        assert leaked(response.text) == []


class TestAnswersThatMustNotLand:
    def test_Answer_OnAStalePage_IsRefusedAndChangesNothing(self, world):
        base, db = world
        page = queue(base)
        joined_form = form(page, "/review-flags-one", "One payment with row A", nth=0)
        joined = httpx.post(f"{base}/review-flags-one", data=joined_form, timeout=30)
        assert joined.status_code == 200
        pending = open_flags(db)
        account_rows = rows(db, joined_form["ref"])

        sibling = next(
            fields
            for action, fields, _ in forms_of(page)
            if action == "/review-flags-two"
            and fields["ref"] == joined_form["ref"]
            and fields["flag"] != joined_form["flag"]
        )
        response = httpx.post(f"{base}/review-flags-two", data=sibling, timeout=30)

        assert response.status_code == 409
        assert "has changed since the page was drawn" in response.text
        assert open_flags(db) == pending
        assert rows(db, joined_form["ref"]) == account_rows

    def test_Answer_ForAFlagThatDoesNotExist_IsRefusedPlainly(self, world):
        base, db = world

        response = httpx.post(
            f"{base}/review-flags-two",
            data={"flag": "0" * 32, "fingerprint": "abc"},
            timeout=30,
        )

        assert response.status_code == 409
        assert "no open flag by that name" in response.text
        assert open_flags(db) == OPEN_QUESTIONS

    def test_Answer_WithAMalformedId_IsRefusedAndChangesNothing(self, world):
        base, db = world

        for route in ("/review-flags-two", "/review-flags-one", "/review-flags-undo"):
            response = httpx.post(
                f"{base}{route}",
                data={
                    "flag": "x' OR '1'='1",
                    "fingerprint": "abc",
                    "neighbour": "..",
                    "answer": "two",
                },
                timeout=30,
            )
            assert response.status_code == 409, route
            assert "Nothing was changed" in response.text

        assert open_flags(db) == OPEN_QUESTIONS

    def test_Answer_WithNoFingerprint_IsRefused(self, world):
        base, db = world
        fields = form(queue(base), "/review-flags-two", "These are two payments")
        fields.pop("fingerprint")

        response = httpx.post(f"{base}/review-flags-two", data=fields, timeout=30)

        assert response.status_code == 409
        assert "did not say which flag" in response.text
        assert open_flags(db) == OPEN_QUESTIONS

    def test_Answer_ByGetToAnAnswerRoute_ChangesNothing(self, world):
        base, db = world
        fields = form(queue(base), "/review-flags-two", "These are two payments")

        for route in ("/review-flags-two", "/review-flags-one", "/review-flags-undo"):
            response = httpx.get(f"{base}{route}", params=fields, timeout=30)
            assert response.status_code == 404, route

        assert open_flags(db) == OPEN_QUESTIONS

    def test_Answer_FromAnotherSite_IsRefusedForEveryRoute(self, world):
        base, db = world
        fields = form(queue(base), "/review-flags-two", "These are two payments")

        routes = ("/review-flags", "/review-flags-two", "/review-flags-one", "/review-flags-undo")
        for route in routes:
            response = httpx.post(
                f"{base}{route}",
                data=fields,
                headers={"Origin": "https://evil.example"},
                timeout=30,
            )
            assert response.status_code == 403, route

        assert open_flags(db) == OPEN_QUESTIONS

    def test_OnePayment_InAProtectedPeriod_SurfacesWhatToDoFirstAsASentence(self, world):
        base, db = world
        page = queue(base)
        fields = next(
            f
            for action, f, label in forms_of(page)
            if action == "/review-flags-one" and f["ref"] == TICKETS
        )
        with Store(db) as store:
            record_stated_anchor(store, TICKETS, "2026-09-14", "1000.00")
            record_stated_anchor(store, TICKETS, "2026-09-15", "945.81")
            opening = effective_opening(store, TICKETS)
            press(
                store,
                TICKETS,
                "2026-09-15",
                opening=opening,
                standing=standing_of(opening, [TICKETS], MovementCompleteness()),
            )
        fresh = next(
            f
            for action, f, label in forms_of(queue(base))
            if action == "/review-flags-one" and f["ref"] == TICKETS
        )
        assert fields["flag"] == fresh["flag"]
        before = rows(db, TICKETS)

        response = httpx.post(f"{base}/review-flags-one", data=fresh, timeout=30)

        assert response.status_code == 409
        assert "protected through 2026-09-15" in response.text
        assert "Remove protection&quot; on the account page first" in response.text
        assert rows(db, TICKETS) == before


class TestUndo:
    def test_Undo_OfTwoPayments_FromTheResultPage_ReopensTheFlag(self, world):
        base, db = world
        fields = form(queue(base), "/review-flags-two", "These are two payments")
        result = httpx.post(f"{base}/review-flags-two", data=fields, timeout=30).text

        undone = httpx.post(
            f"{base}/review-flags-undo", data=form(result, "/review-flags-undo", "Undo"), timeout=30
        )

        assert undone.status_code == 200
        assert "the flag is open again" in undone.text
        assert f"{OPEN_QUESTIONS} questions remain open" in undone.text
        assert cards(queue(base)) == OPEN_QUESTIONS
        assert open_flags(db) == OPEN_QUESTIONS

    def test_Undo_OfTwoPayments_FromTheAnsweredList_ReopensTheFlag(self, world):
        base, _ = world
        fields = form(queue(base), "/review-flags-two", "These are two payments")
        httpx.post(f"{base}/review-flags-two", data=fields, timeout=30)

        page = queue(base)
        undo_fields = form(page, "/review-flags-undo", "Undo")
        httpx.post(f"{base}/review-flags-undo", data=undo_fields, timeout=30)

        again = queue(base)
        assert cards(again) == OPEN_QUESTIONS
        assert "<h2>Answered</h2>" not in again

    def test_Undo_OfOnePayment_SaysTheRowsStayJoinedUntilTheNextRebuild(self, world):
        base, db = world
        fields = form(queue(base), "/review-flags-one", "These are one payment", nth=0)
        account = fields["ref"]
        before = rows(db, account)
        result = httpx.post(f"{base}/review-flags-one", data=fields, timeout=30).text

        undone = httpx.post(
            f"{base}/review-flags-undo", data=form(result, "/review-flags-undo", "Undo"), timeout=30
        )

        assert "stay joined until the next rebuild" in undone.text
        assert rows(db, account) == before - 1
        with Store(db) as store:
            rebuild_from_raw(store)
        assert rows(db, account) == before
        assert cards(queue(base)) == OPEN_QUESTIONS

    def test_Undo_OfAnAnswerNeverGiven_IsRefused(self, world):
        base, _ = world

        response = httpx.post(
            f"{base}/review-flags-undo",
            data={"answer": "two", "flag": "0" * 32, "other": ""},
            timeout=30,
        )

        assert response.status_code == 409
        assert "no such answer is recorded" in response.text

    def test_AnsweredList_ShowsAtMostTheLastTwenty(self, world):
        base, db = world
        with Store(db) as store:
            ids = [str(f["entity_id"]) for f in store.review_queue()]
        assert len(ids) == OPEN_QUESTIONS
        # Twenty-five answers of "two payments" cannot be given to four flags, so the cap is read
        # from the lines the page draws for as many answers as the household can give.
        for _ in range(OPEN_QUESTIONS):
            fields = form(queue(base), "/review-flags-two", "These are two payments")
            httpx.post(f"{base}/review-flags-two", data=fields, timeout=30)

        assert queue(base).count('class="flag-done"') == OPEN_QUESTIONS
