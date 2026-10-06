"""The Connections page: is everything connected, and when did each last answer?

Known answers, decided before the page was built. The day is 2026-10-06 12:00 UTC.

    halifax    consent ends 2026-10-31, answered 2026-10-06, feeds Halifax Current and Halifax Saver
    virgin     consent ends 2026-10-08 (in 2 days), answered 2026-10-05, feeds Virgin Card
    barclays   consent ended 2026-10-01, answered 2026-09-01, feeds Barclays Current

Only virgin and barclays need him, and each has one Reconnect. halifax has none, and when every
consent is fine nothing on the page asks for anything. The Actual page's own verdict is read for
each state it can be in, with the press the Actual page offers for it.
"""

from __future__ import annotations

import re
from collections import Counter
from datetime import UTC, datetime, timedelta

import pytest

from actual_states import NOW as ACTUAL_NOW
from actual_states import STATES
from obdi.account_names import AccountShown, AccountsShown
from obdi.connections import Connection, ConnectionStore
from obdi.web_sections import render_connections
from page_dom import Node, elements, parse

NOW = datetime(2026, 10, 6, 12, 0, tzinfo=UTC)

ACCOUNTS = AccountsShown(
    [
        AccountShown.named("halifax-current", "Halifax Current"),
        AccountShown.named("halifax-saver", "Halifax Saver"),
        AccountShown.named("virgin-card", "Virgin Card"),
        AccountShown.named("barclays-current", "Barclays Current"),
    ]
)

FED = {
    ("halifax-current", "truelayer"): ["halifax"],
    ("halifax-saver", "truelayer"): ["halifax"],
    ("virgin-card", "truelayer"): ["virgin"],
    ("barclays-current", "truelayer"): ["barclays"],
}

ANSWERED = {
    "halifax": "2026-10-06T04:00:00+00:00",
    "virgin": "2026-10-05T04:00:00+00:00",
    "barclays": "2026-09-01T04:00:00+00:00",
}


def connection(name: str, expires: datetime | None) -> Connection:
    return Connection(
        connection_id=name,
        provider="p",
        refresh_token="r",
        consent_expires_at=expires.isoformat() if expires else "",
    )


def store_of(tmp_path, *connections: Connection) -> ConnectionStore:
    store = ConnectionStore(tmp_path / "c.json")
    for one in connections:
        store.put(one)
    return store


FINE = connection("halifax", datetime(2026, 10, 31, 12, 0, tzinfo=UTC))
EXPIRING = connection("virgin", datetime(2026, 10, 8, 12, 0, tzinfo=UTC))
LAPSED = connection("barclays", datetime(2026, 10, 1, 12, 0, tzinfo=UTC))


def page_of(store: ConnectionStore, **hooks) -> Node:
    hooks.setdefault("connection_last_answered", lambda: ANSWERED)
    hooks.setdefault("source_connections", lambda: FED)
    hooks.setdefault("account_names", lambda: ACCOUNTS)
    hooks.setdefault("now", NOW)
    return parse(render_connections(store, **hooks).decode())


def actual_hooks(state: str, **extra) -> dict:
    wanted = STATES[state]
    results = wanted["results"]
    return {
        "actual_status": lambda: results,
        "actual_queue": lambda: wanted.get("queue", []),
        "actual_heartbeat": lambda: str(wanted.get("heartbeat", "")),
        "actual_configured": lambda: bool(wanted.get("configured", True)),
        "now": ACTUAL_NOW,
        **extra,
    }


def section(root: Node, heading: str) -> Node:
    """The block of the page that follows the heading `heading`, up to the next h2."""
    holder = next(h for h in elements(root, "h2") if h.text() == heading).parent
    assert holder is not None
    return holder


def quiet_reconnects(root: Node) -> list[str]:
    """The Reconnect links on a bank's own line: there at any time, never a thing to do."""
    return [
        node.attrs["href"]
        for node in elements(root, "a")
        if node.attrs.get("href", "").startswith("/connect?")
        and any("source" in up.classes for up in node.ancestors())
    ]


def todo_reconnects(root: Node) -> list[str]:
    """The Reconnect links among what needs him: only for a consent running out or ended."""
    return [
        node.attrs["href"]
        for node in elements(root, "a")
        if node.attrs.get("href", "").startswith("/connect?")
        and any("todo" in up.classes for up in node.ancestors())
    ]


def row_of(root: Node, name: str) -> Node:
    return next(li for li in elements(root, "li") if "source" in li.classes and name in li.text())


class TestWhenEverythingIsConnected:
    def test_Connections_WhenEveryConsentIsFine_AsksForNothingAndOffersReconnectOnlyQuietly(
        self, tmp_path
    ):
        root = page_of(store_of(tmp_path, FINE))

        assert todo_reconnects(root) == []
        assert quiet_reconnects(root) == ["/connect?name=halifax"]
        assert not [n for n in elements(root, "ul", "ol") if "todos" in n.classes]

    def test_Connections_EveryBankLine_CarriesItsOwnQuietReconnectWhateverItsConsentSays(
        self, tmp_path
    ):
        root = page_of(store_of(tmp_path, FINE, EXPIRING, LAPSED))

        assert sorted(quiet_reconnects(root)) == [
            "/connect?name=barclays",
            "/connect?name=halifax",
            "/connect?name=virgin",
        ]
        for name in ("halifax", "virgin", "barclays"):
            links = [a for a in elements(row_of(root, name), "a") if a.text() == "Reconnect"]
            assert len(links) == 1, name

    def test_Connections_AConnectionWithNoConsentClock_StillCanBeReconnected(self, tmp_path):
        root = page_of(store_of(tmp_path, connection("odd", None)))

        assert quiet_reconnects(root) == ["/connect?name=odd"]
        assert todo_reconnects(root) == []

    def test_Connections_TheBankOwnFeed_HasNoReconnectBecauseItHasNoConsent(self, tmp_path):
        root = page_of(store_of(tmp_path, FINE), starling_status=lambda: {"ok": True})

        assert quiet_reconnects(root) == ["/connect?name=halifax"]

    def test_Connections_WhenEveryConsentIsFine_SaysWhatEachBankFeedsAndWhenItAnswered(
        self, tmp_path
    ):
        row = row_of(page_of(store_of(tmp_path, FINE)), "halifax").text()

        assert "Halifax Current" in row and "Halifax Saver" in row
        assert "last answered 2026-10-06" in row
        assert "expires 2026-10-31 (in 25 days)" in row

    def test_Connections_NamesTheAccountsByNameAndNotByReference(self, tmp_path):
        row = row_of(page_of(store_of(tmp_path, FINE)), "halifax").text()

        assert "halifax-current" not in row

    def test_Connections_AnAccountWithNoNameYet_IsShownByItsReferenceAsCode(self, tmp_path):
        root = page_of(store_of(tmp_path, FINE), account_names=lambda: AccountsShown())

        codes = [c.text() for c in elements(row_of(root, "halifax"), "code")]
        assert "halifax-current" in codes


class TestTwoAccountsSharingALabel:
    def test_Connections_AFeedOfTwoAccountsWithTheSameProviderLabel_TellsThemApartByReference(
        self, tmp_path
    ):
        names = AccountsShown(
            [
                AccountShown.named("halifax-a", "Mr Roger Howell"),
                AccountShown.named("halifax-b", "Mr Roger Howell"),
                AccountShown.named("halifax-cc", "Halifax CC"),
            ]
        )
        fed = {(ref, "truelayer"): ["halifax"] for ref in ("halifax-a", "halifax-b", "halifax-cc")}
        root = page_of(
            store_of(tmp_path, FINE), account_names=lambda: names, source_connections=lambda: fed
        )

        row = row_of(root, "halifax")
        codes = [c.text() for c in elements(row, "code")]
        assert codes == ["halifax-a", "halifax-b"]
        assert "Halifax CC" in row.text()

    def test_Connections_WhenOneConsentEndsInTwoDays_OffersItsReconnectAmongWhatNeedsYou(
        self, tmp_path
    ):
        root = page_of(store_of(tmp_path, FINE, EXPIRING))

        assert todo_reconnects(root) == ["/connect?name=virgin"]
        needed = [li for li in elements(root, "li") if "todo" in li.classes]
        assert len(needed) == 1
        assert "virgin" in needed[0].text()
        assert "expires 2026-10-08 (in 2 days)" in needed[0].text()

    def test_Connections_WhenOneConsentHasLapsed_SaysWhenAndOffersItsReconnectAsUrgent(
        self, tmp_path
    ):
        root = page_of(store_of(tmp_path, FINE, LAPSED))

        needed = [li for li in elements(root, "li") if "todo" in li.classes]
        assert [li.text() for li in needed if "barclays" in li.text()]
        assert "now" in needed[0].classes
        assert "expired 2026-10-01" in needed[0].text()
        assert todo_reconnects(root) == ["/connect?name=barclays"]

    def test_Connections_WithOneFineOneExpiringAndOneLapsed_PutsExactlyTwoAmongWhatNeedsYou(
        self, tmp_path
    ):
        root = page_of(store_of(tmp_path, FINE, EXPIRING, LAPSED))

        assert sorted(todo_reconnects(root)) == ["/connect?name=barclays", "/connect?name=virgin"]

    def test_Connections_AReconnectNameWithAnAmpersand_IsEncodedSoItNamesTheSameConnection(
        self, tmp_path
    ):
        odd = connection("a&b #1", datetime(2026, 10, 7, 12, 0, tzinfo=UTC))
        root = page_of(store_of(tmp_path, odd))

        assert todo_reconnects(root) == ["/connect?name=a%26b%20%231"]
        assert quiet_reconnects(root) == ["/connect?name=a%26b%20%231"]

    def test_Connections_WhenAConsentEndsFifteenDaysOut_IsNotAThingToDoButCanStillBeReconnected(
        self, tmp_path
    ):
        later = connection("virgin", NOW + timedelta(days=15, hours=1))
        root = page_of(store_of(tmp_path, later))

        assert todo_reconnects(root) == []
        assert quiet_reconnects(root) == ["/connect?name=virgin"]

    def test_Connections_WhenAConsentEndsFourteenDaysOut_IsAThingToDoAndStillHasItsQuietLink(
        self, tmp_path
    ):
        edge = connection("virgin", NOW + timedelta(days=14))
        root = page_of(store_of(tmp_path, edge))

        assert todo_reconnects(root) == ["/connect?name=virgin"]
        assert quiet_reconnects(root) == ["/connect?name=virgin"]


class TestOddConnections:
    def test_Connections_WithNoConsentClock_SaysSoAndNeverPrintsNone(self, tmp_path):
        root = page_of(store_of(tmp_path, connection("odd", None)))

        text = root.text()
        assert "no consent expiry recorded" in text and "None" not in text
        assert todo_reconnects(root) == []

    def test_Connections_AConnectionThatHasNeverAnswered_SaysSo(self, tmp_path):
        root = page_of(store_of(tmp_path, FINE), connection_last_answered=lambda: {})

        assert "has never answered" in row_of(root, "halifax").text()

    def test_Connections_WhenTheAnswersCannotBeRead_SaysNothingFalseOfWhenItAnswered(
        self, tmp_path
    ):
        def boom() -> dict[str, str]:
            raise RuntimeError("locked")

        root = page_of(store_of(tmp_path, FINE), connection_last_answered=boom)

        assert "never answered" not in root.text()
        assert "expires 2026-10-31" in root.text()

    def test_Connections_WhenNoBankIsConnected_SaysSoOnceAndStillOffersToAddOne(self, tmp_path):
        text = page_of(store_of(tmp_path)).text()

        assert "No bank is connected through the aggregator." in text
        assert "Add a bank" in text

    def test_Connections_WhenTheFeedHooksAreNotWired_StillListsEachBankWithItsConsent(
        self, tmp_path
    ):
        root = parse(render_connections(store_of(tmp_path, FINE), now=NOW).decode())

        assert "expires 2026-10-31 (in 25 days)" in row_of(root, "halifax").text()

    def test_Connections_AHookThatRaises_LeavesTheRestOfThePage(self, tmp_path):
        def boom():
            raise RuntimeError("locked")

        root = page_of(
            store_of(tmp_path, FINE),
            account_names=boom,
            source_connections=boom,
            starling_status=boom,
            provider_knowledge=boom,
            extendables=boom,
            scheduler_heartbeat=boom,
            actual_status=boom,
        )

        assert "expires 2026-10-31 (in 25 days)" in row_of(root, "halifax").text()


class TestTheBanksOwnFeed:
    def test_Connections_WhenTheBankFeedIsConfigured_ItIsASourceWithNoConsentClock(self, tmp_path):
        fed = {**FED, ("starling-main", "starling"): ["starling-api"]}
        root = page_of(
            store_of(tmp_path, FINE),
            starling_status=lambda: {"accounts": [{"name": "Main", "accountUid": "abcdef123456"}]},
            source_connections=lambda: fed,
            account_names=lambda: AccountsShown(
                [*ACCOUNTS, AccountShown.named("starling-main", "Starling Main")]
            ),
            connection_last_answered=lambda: {**ANSWERED, "starling-api": "2026-10-06T08:00:00Z"},
        )

        row = row_of(root, "own feed").text()
        assert "Starling Main" in row
        assert "no consent clock" in row
        assert "last answered 2026-10-06" in row
        assert "abcdef12" not in root.text()

    def test_Connections_WhenTheBankFeedIsNotConfigured_ItIsNotListed(self, tmp_path):
        assert "own feed" not in page_of(store_of(tmp_path, FINE)).text()


class TestWhereDataGoesOut:
    @pytest.mark.parametrize(
        ("state", "said"),
        [
            ("agrees", "agree"),
            ("differs_three_orphans", "differ"),
            ("nothing_pushed", "pushed"),
        ],
    )
    def test_Connections_SaysTheActualPagesOwnVerdictInTheWordsItUses(self, tmp_path, state, said):
        from obdi.web_actual import current_verdict

        wanted = STATES[state]
        verdict = current_verdict(
            lambda: wanted["results"],
            lambda: wanted.get("queue", []),
            lambda: str(wanted.get("heartbeat", "")),
            lambda: True,
            ACTUAL_NOW,
        )
        root = page_of(store_of(tmp_path, FINE), **actual_hooks(state))
        out = section(root, "Where data goes out")

        assert verdict.headline in out.text()
        assert said in out.text().lower()
        assert any(a.attrs.get("href") == "/actual" for a in elements(out, "a"))

    def test_Connections_WhenActualAgrees_OffersNoPressAndSaysWhenItWasPushedAndAudited(
        self, tmp_path
    ):
        out = section(
            page_of(store_of(tmp_path, FINE), **actual_hooks("agrees", push_available=True)),
            "Where data goes out",
        )

        assert not list(elements(out, "form"))
        assert "Last pushed 2026-10-04" in out.text()
        assert "audited 2026-10-04" in out.text()

    def test_Connections_WhenNothingHasBeenPushed_OffersThePushAsTheOnePress(self, tmp_path):
        root = page_of(
            store_of(tmp_path, FINE), **actual_hooks("nothing_pushed", push_available=True)
        )

        forms = [f.attrs["action"] for f in elements(root, "form") if "action" in f.attrs]
        assert "/push-actual" in forms
        assert "Never pushed" in section(root, "Where data goes out").text()

    def test_Connections_WhenActualDiffers_OffersToBringItIntoLine(self, tmp_path):
        root = page_of(
            store_of(tmp_path, FINE),
            **actual_hooks("differs_three_orphans", align_available=True),
        )

        forms = [f.attrs["action"] for f in elements(root, "form") if "action" in f.attrs]
        assert "/align-actual" in forms

    def test_Connections_WhenAPressIsNotWired_OffersNoFormForIt(self, tmp_path):
        root = page_of(store_of(tmp_path, FINE), **actual_hooks("nothing_pushed"))

        forms = [f.attrs["action"] for f in elements(root, "form") if "action" in f.attrs]
        assert "/push-actual" not in forms

    def test_Connections_WhenActualIsNotWired_SaysSoAndStillLinksToItsPage(self, tmp_path):
        out = section(page_of(store_of(tmp_path, FINE)), "Where data goes out")

        assert "Not wired on this instance." in out.text()
        assert any(a.attrs.get("href") == "/actual" for a in elements(out, "a"))

    def test_Connections_WhenActualIsNotConfigured_OffersNoPress(self, tmp_path):
        root = page_of(
            store_of(tmp_path, FINE),
            **actual_hooks("not_configured", push_available=True, audit_available=True),
        )

        assert not [f for f in elements(root, "form") if "/actual" in f.attrs.get("action", "")]


class TestWhatIsKeptButFolded:
    def test_Connections_AddABankStaysAndIsBehindAFold(self, tmp_path):
        root = page_of(store_of(tmp_path, FINE))

        form = next(f for f in elements(root, "form") if f.attrs.get("action") == "/connect")
        assert any(a.tag == "details" for a in form.ancestors())

    def test_Connections_RenameIsOfferedPerConnectionOnlyWhereItIsWired(self, tmp_path):
        wired = page_of(store_of(tmp_path, FINE, EXPIRING), rename_connection=lambda a, b: "")
        bare = page_of(store_of(tmp_path, FINE, EXPIRING))

        def renames(root: Node) -> list[str]:
            return [
                f.attrs["action"]
                for f in elements(root, "form")
                if f.attrs.get("action") == "/rename-connection"
            ]

        assert len(renames(wired)) == 2
        assert renames(bare) == []

    def test_Connections_FetchNowIsOfferedForEachConnectionWhereItIsWired(self, tmp_path):
        root = page_of(store_of(tmp_path, FINE), fetch_now_available=True)

        assert "Fetch now" in root.text()

    def test_Connections_TheSchedulerIsInTheEvidenceFold_AndOpensWhenItHasFailed(self, tmp_path):
        quiet = page_of(store_of(tmp_path, FINE))
        folds = [d for d in elements(quiet, "details") if "evidence" in d.classes]

        assert len(folds) == 1 and "open" not in folds[0].attrs

    def test_Connections_EveryDetailPageItLinksToIsLinkedOnce(self, tmp_path):
        root = page_of(store_of(tmp_path, FINE))
        hrefs = Counter(a.attrs.get("href") for a in elements(root, "a"))

        assert hrefs["/fetch-timeline"] == 1
        assert hrefs["/attempts"] == 1
        coming_from = section(root, "Where data comes from")
        assert [a.attrs.get("href") for a in elements(coming_from, "a")].count("/bring-in") == 1


class TestTheFilesYouBringIn:
    def test_Connections_NamesTheFilesYouBringInYourselfInOneLineLinkingToBringIn(self, tmp_path):
        root = page_of(store_of(tmp_path, FINE))

        coming_from = section(root, "Where data comes from")
        link = next(a for a in elements(coming_from, "a") if a.attrs.get("href") == "/bring-in")
        line = link.parent.text() if link.parent is not None else ""
        assert "statements" in line and "exports" in line


class TestWordsAndMasking:
    def test_Connections_NeverSaysRowsAndNeverRepeatsAThreeWordLineMoreThanTwice(self, tmp_path):
        root = page_of(
            store_of(tmp_path, FINE, EXPIRING, LAPSED),
            rename_connection=lambda a, b: "",
            fetch_now_available=True,
            **actual_hooks("agrees"),
        )
        lines = []
        for node in elements(root, "p", "li", "summary", "h2", "h3", "a", "button", "label"):
            text = node.text()
            if len(text.split()) >= 3:
                lines.append(text)
        repeated = {text: n for text, n in Counter(lines).items() if n > 2}

        assert repeated == {}
        assert not re.search(r"\brows?\b", root.text(), re.IGNORECASE)
