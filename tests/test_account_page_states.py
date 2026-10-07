"""The five states an account's page has, read over real HTTP from `account_states_world`.

What each page says was decided in that world's docstring before any page was built: its trust
sentence, its things to do and the control of each, whether locking in is offered and what it would
cover, and what the page keeps quiet. The redesign's prototypes are the specification of layout
and wording; these hold the facts those pictures rest on.
"""

from __future__ import annotations

import html
import re
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest

from account_states_world import (
    EVERYDAY,
    HOLIDAY,
    JOINT,
    JOINT_FAULT_OFFSET,
    LABELS,
    MOVED,
    RAINY,
    build_states,
)
from obdi.account_page import read_account
from obdi.core.page_words import RETIRED_ON_PAGES
from obdi.ingest.store import Store
from obdi.read.ledger import build_ledger
from page_dom import Node, elements, inside, parse, text_nodes
from served_store import environment_for, served_store

TODAY = datetime.now(UTC).date()


def ago(days: int) -> str:
    return (TODAY - timedelta(days=days)).isoformat()


@pytest.fixture(scope="module")
def root(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return tmp_path_factory.mktemp("account-states")


@pytest.fixture(scope="module")
def base(root: Path) -> Iterator[str]:
    with served_store(
        root, lambda store: build_states(store, TODAY), bound=[EVERYDAY, JOINT]
    ) as address:
        yield address


@pytest.fixture(autouse=True)
def _environment(base: str, root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    for name, value in environment_for(root).items():
        monkeypatch.setenv(name, value)


def get(base: str, ref: str) -> str:
    response = httpx.get(f"{base}/ledger", params={"ref": ref}, timeout=120)
    assert response.status_code == 200
    return response.text


def words(page: str) -> str:
    """What a reader reads: the page without its markup, entities read, spacing collapsed. A date
    is set in a span that never breaks, which must not read as a space before the full stop."""
    body = re.sub(r"<(script|style)\b.*?</\1>", "", page, flags=re.S)
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", "", body)))


def state_of(page: str) -> Node:
    return next(n for n in elements(parse(page), "div") if "acct-state" in n.classes)


def todos_of(page: str) -> list[Node]:
    return [n for n in state_of(page).descendants() if n.tag == "li" and "todo" in n.classes]


def titles_of(page: str) -> list[str]:
    return [
        next(p for p in todo.descendants() if "todo-what" in p.classes).text()
        for todo in todos_of(page)
    ]


def controls_of(todo: Node) -> list[Node]:
    return [
        n
        for n in todo.descendants()
        if n.tag in ("a", "button") and "button" in n.classes
    ]


def primary_controls(page: str) -> list[str]:
    return [
        n.text()
        for n in elements(parse(page), "a", "button")
        if "button" in n.classes and "secondary" not in n.classes and "danger" not in n.classes
    ]


class TestAddsUpWithStatementsDue:
    def test_Sentence_SaysWhatAddsUpAndThatNothingTestsTheDaysSince(self, base):
        page = get(base, EVERYDAY)

        assert f"Adds up to the known balances to {ago(80)}." in words(page)
        assert f"Nothing to check against since {ago(80)}" in words(page)
        assert 'class="trust bad"' not in page and 'class="trust none"' not in page

    def test_ThingsToDo_AreTheStatementsWantedEachWithItsUploadControl(self, base):
        page = get(base, EVERYDAY)

        assert titles_of(page) == [
            f"Upload the statement covering {ago(79)} to {TODAY.isoformat()}",
            f"Upload an earlier statement covering {ago(200)} to {ago(140)} (2 months)",
            f"Lock in to {ago(80)}",
        ]
        hrefs = [
            [n.attrs.get("href") for n in controls_of(todo)] for todo in todos_of(page)[:2]
        ]
        scoped = f"/bring-in?account={EVERYDAY}"
        assert hrefs == [[scoped], [scoped]]

    def test_FirstThingToDo_IsTheOnePrimaryControlAndTheRestAreOutlined(self, base):
        page = get(base, EVERYDAY)

        assert primary_controls(page) == ["Upload"]

    def test_LockingIn_SaysHowManyTransactionsWhichMonthsAndWhatItGives(self, base):
        page = get(base, EVERYDAY)

        lock = words(page)
        # Rows every five days from 200 to 80 days ago: 25 of them, none yet locked.
        assert "25 transactions" in lock
        first, last = ago(200)[:7], ago(80)[:7]
        assert f"{first} to {last}, would be locked in." in lock or f"{first}, would" in lock
        assert "Show values to read them first." in lock
        assert "reported loudly and never applied quietly" in lock

    def test_LockingIn_IsAPressThatAsksBeforeItActs(self, base):
        page = get(base, EVERYDAY)

        form = re.search(r'<form method="post" action="/protect">(.*?)</form>', page, re.S)
        assert form is not None
        assert f'name="through" value="{ago(80)}"' in form.group(1)
        assert 'name="confirmed"' not in form.group(1), "the confirmation page carries that"


class TestDoesNotAddUp:
    def test_Sentence_IsTheOneThatNeedsTheReaderAndSaysWhereItStops(self, base):
        page = get(base, JOINT)

        assert '<p class="trust bad">' in page
        assert f"Does not add up from {ago(JOINT_FAULT_OFFSET)}." in words(page)
        assert f"Adds up to the known balances to {ago(100)}." in words(page)

    def test_WhatStopsItIsSaidOnceWithOneControlThatOpensTheExplanation(self, base):
        page = get(base, JOINT)

        faults = [t for t in titles_of(page) if "stopped adding up" in t]
        assert faults == ["Find out why the transactions stopped adding up"]
        first = todos_of(page)[0]
        assert [n.attrs["href"] for n in controls_of(first)] == ["#opening"]
        assert words(page).count("do not add up to the known balance for") == 1

    def test_KnownBalances_AreNotOpenedByThePageThatIsAlreadyAsking(self, base):
        page = get(base, JOINT)

        assert "<details><summary>Known balances (" in page
        assert "<details open>" not in page
        assert '<div id="opening">' in page, "the control's target is inside the fold"

    def test_LockingIn_IsNotOfferedWhileSomethingFails(self, base):
        page = get(base, JOINT)

        assert not [t for t in titles_of(page) if t.startswith("Lock in")]
        assert 'action="/protect"' not in page


class TestNothingToCheckAgainst:
    def test_Sentence_SaysSoWithWhatIsHeldAndNothingElse(self, base):
        page = get(base, HOLIDAY)

        assert '<p class="trust none">Nothing to check against.' in page
        assert "11 transactions held" in words(page)

    def test_ThingToDo_IsToConfirmABalanceForADayWithTheFormInPlace(self, base):
        page = get(base, HOLIDAY)

        assert titles_of(page) == [f"Confirm the balance for {TODAY.isoformat()}"]
        form = controls_of(todos_of(page)[0])
        assert [n.text() for n in form] == ["Confirm this balance"]
        assert page.count('action="/ledger-anchor"') == 1, "the form is not repeated in a fold"
        assert 'name="amount"' in page and 'name="amount" value' not in page, "never pre-filled"

    def test_ConfirmingABalance_IsTheSameDoorStatingOneAlwaysWas(self, base, root):
        response = httpx.post(
            f"{base}/ledger-anchor",
            data={
                "ref": HOLIDAY, "month": "", "currency": "GBP",
                "day": ago(30), "amount": "25.00",
            },
            timeout=60,
        )

        assert response.status_code == 200
        assert f"Saved: a known balance for the end of {ago(30)}." in words(response.text)

    def test_ConfirmingABalance_AnswerSaysNothingOfHowTheAccountStoodBeforeTheSave(self, base):
        response = httpx.post(
            f"{base}/ledger-anchor",
            data={
                "ref": HOLIDAY, "month": "", "currency": "GBP",
                "day": ago(30), "amount": "25.00",
            },
            timeout=60,
        )

        said = words(response.text)
        # The standing read in the same breath as the save is the one held before it, so the
        # answer would have described the state before the save as the state after it. The
        # page beneath the answer is where the new standing is said.
        assert "as before" not in said
        assert "does not yet add up to any known balance" not in said
        assert f"Saved: a known balance for the end of {ago(30)}. Nothing else changed." in said


class TestLockedInWithNothingDue:
    def test_Page_IsQuiet_ASentenceAndNoThingToDo(self, base):
        page = get(base, RAINY)

        assert f"Locked in to {ago(8)}." in words(page)
        assert titles_of(page) == []
        assert 'class="todos"' not in page
        assert 'class="trust bad"' not in page and 'class="trust none"' not in page
        assert "age" not in {c for n in elements(parse(page), "span") for c in n.classes}

    def test_ShowValues_IsTheOnePrimaryControl(self, base):
        assert primary_controls(get(base, RAINY)) == ["Show values"]


class TestALockedStretchChanged:
    def test_Sentence_SaysSoLoudly(self, base):
        page = get(base, MOVED)

        assert '<p class="trust bad">' in page
        assert f"Locked in to {ago(8)}, but that stretch has changed since." in words(page)

    def test_FirstThingToDo_IsToSeeWhatChangedAndLeadsToTheLockingFold(self, base):
        page = get(base, MOVED)

        first = todos_of(page)[0]
        assert titles_of(page)[0] == "See what changed in a locked stretch"
        assert [n.attrs["href"] for n in controls_of(first)] == ["#locking"]
        assert "<summary>Locking in (to " in page and ", changed)</summary>" in page
        assert '<div id="locking">' in page

    def test_TheFold_SaysWhatChangedInCountsAndDatesAndOffersToAcceptIt(self, base):
        page = get(base, MOVED)

        assert "has changed since" in words(page)
        assert "Accept the change and lock in again" in page
        assert ago(150) in words(page), "the date the added transaction is dated"


class TestEveryState:
    @pytest.mark.parametrize("ref", list(LABELS))
    def test_Page_HoldsExactlySixFoldsBesideTheMonth(self, base, ref):
        more = next(n for n in elements(parse(get(base, ref)), "div") if "ledger-more" in n.classes)
        folds = [c for c in more.children if isinstance(c, Node) and c.tag == "details"]

        summaries = [
            summary.text().split(" (")[0]
            for fold in folds
            for summary in fold.children
            if isinstance(summary, Node) and summary.tag == "summary"
        ]
        assert summaries == [
            "What the bars show, and the full timeline",
            "Known balances",
            "About this account",
            "Locking in",
            "How this was checked",
            "Rename or archive",
        ]

    @pytest.mark.parametrize("ref", list(LABELS))
    def test_Page_NamesTheAccountByItsNameAndSetsTheReferenceAsCode(self, base, ref):
        page = get(base, ref)

        assert f"<h1>{LABELS[ref]}</h1>" in page
        assert f"<code>{ref}</code>" in page
        assert re.search(r'<p class="meta">.*(not sent|sent) to Actual</p>', page)

    @pytest.mark.parametrize("ref", list(LABELS))
    def test_Page_RepeatsNoLineOfThreeOrMoreWordsMoreThanTwice(self, base, ref):
        said: dict[str, int] = {}
        for text, holder in text_nodes(parse(get(base, ref))):
            if "visually-hidden" in holder.classes or holder.tag in ("code", "option"):
                continue
            if inside(holder, "details") and holder.tag != "summary":
                continue  # shut until asked for: what a fold lists is not the page repeating itself
            line = " ".join(text.split())
            if len(line.split()) >= 3:
                said[line] = said.get(line, 0) + 1
        assert {line: n for line, n in said.items() if n > 2} == {}

    @pytest.mark.parametrize("ref", list(LABELS))
    def test_Page_UsesNoRetiredPhraseAndHoldsNoRealFigure(self, base, ref):
        page = get(base, ref)
        shown = words(page).lower()

        assert [p for p in RETIRED_ON_PAGES if p in shown] == []
        assert not re.search(r"£[\d,]*[0-8][\d,]*\.\d\d", page), "a masked figure is all nines"

    @pytest.mark.parametrize("ref", [EVERYDAY, JOINT, HOLIDAY, RAINY])
    def test_Page_SaysTransactionsAndNeverRows_BeforeAFoldIsOpened(self, base, ref):
        """Of what is shown on loading, which is the page's own words. Two sources of words are
        another module's and still say "rows": the explanations a fold holds for a balance that
        differs, and `protection.SpanChange.says`, which the changed-lock account's thing to do
        quotes (see the residue in the slice's report), so that account is not held to this."""
        shown = " ".join(
            text
            for text, holder in text_nodes(parse(get(base, ref)))
            if not (inside(holder, "details") and holder.tag != "summary")
        )
        assert not re.search(r"\brows?\b", shown, re.IGNORECASE)

    @pytest.mark.parametrize("ref", [EVERYDAY, HOLIDAY, RAINY])
    def test_Page_WhereNothingDiffers_SaysTransactionsAndNeverRowsEvenInTheFolds(self, base, ref):
        assert not re.search(r"\brows?\b", words(get(base, ref)), re.IGNORECASE)

    @pytest.mark.parametrize("ref", list(LABELS))
    def test_EveryThingToDo_HasExactlyOneControl(self, base, ref):
        for todo in todos_of(get(base, ref)):
            assert len(controls_of(todo)) == 1, todo.text()


class TestWhenWhatTheyNeedCannotBeRead:
    def test_Reading_WhenTheOverviewFails_SaysSoAndStillGivesTheSentence(self, base, root):
        class Config:
            @staticmethod
            def overview(fresh: bool) -> None:
                raise RuntimeError("the store went away")

        with Store(root / "store.sqlite3") as store:
            ledger = build_ledger(store, EVERYDAY, None, bound=True)
        reading = read_account(Config(), ledger, TODAY)

        assert reading.unread == ("What this account needs could not be worked out just now.",)
        assert reading.todos == ()
        assert reading.trust.adds_up_to is not None, "the sentence is still worked out"
