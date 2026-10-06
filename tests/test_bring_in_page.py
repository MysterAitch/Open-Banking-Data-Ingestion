"""The Bring in page: its three states, what it groups, what it must not show, and the upload.

The household and every file wanted in it are decided in `fetch_gaps_world`'s docstring (TODAY
2026-10-05): nine statements and two exports for eight accounts. The page is drawn from data
(`render_bring_in`), so every date is the household's, and once over HTTP (`served_store`) to
show that the routes, the hooks, and the doors behind the one upload target are joined up.

Known answers decided before the first run:

  * card-behind's two statements are 2026-07-11..2026-08-10 (out since 2026-08-10, 8 weeks ago) and
    2026-08-11..2026-09-10; its sentence is "Adds up to the known balances to 2026-07-10.";
  * card-virgin's one is 2026-06-05..2026-07-04, and its set-aside link carries exactly those days;
  * a first card statement uploaded for an account that holds nothing says that account now adds
    up to the known balances to the statement's closing day, "it had nothing to check against";
  * the same statement uploaded with no account is kept, and asks which account for that file.
"""

from __future__ import annotations

import re
from datetime import UTC, date, datetime
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import httpx
import pytest

from coverage_page_world import repeated_lines
from fetch_gaps_world import (
    MONTHS,
    TODAY,
    Loaded,
    _pounds,
    build_household,
    load_household,
    ordinal,
)
from obdi.account_names import AccountsShown, accounts_shown
from obdi.accounts import AccountRecord, AccountRef
from obdi.bring_in import UploadKind, files_wanted
from obdi.connections import Connection, ConnectionStore
from obdi.fetch_gaps import FetchReport
from obdi.namespaces import UNASSIGNED_ACCOUNT as UNASSIGNED
from obdi.page_words import RETIRED_ON_PAGES
from obdi.store import Store
from obdi.synthetic_pdf import build_pdf
from obdi.todo import wanted_days
from obdi.trust import trust_of
from obdi.web_bring_in import (
    BringInData,
    Evidence,
    FileResult,
    Outcome,
    UploadResults,
    _mask,
    render_bring_in,
    source_lines,
)
from page_dom import Node, elements, parse
from served_store import served_store

D = date
MONEY_FIGURE = re.compile(r"[£€$]\s*[-\d]|\d[\d,]*\.\d\d(?![\d%a-z])")
EVIDENCE = Evidence("Every source looked at today at 08:12", ("Quiet card: nothing is due.",))


@pytest.fixture(scope="module")
def world(tmp_path_factory) -> Loaded:
    return load_household(tmp_path_factory.mktemp("bring-in-page"))


def names_of(loaded: Loaded) -> AccountsShown:
    with Store(loaded.db) as store:
        return accounts_shown({}, store.declared_accounts())


def data_of(loaded: Loaded, *, report: FetchReport | None = None, **more) -> BringInData:
    shown = report if report is not None else loaded.report
    wanted = wanted_days(shown)
    refs = {item.account for item in files_wanted(shown)}
    trust = {
        ref: trust_of(
            first=D(2026, 1, 1),
            newest=D(2026, 10, 1),
            standing=loaded.standings.get(ref),
            wanted=wanted.get(ref, ()),
            today=TODAY,
        )
        for ref in refs
    }
    return BringInData(
        today=TODAY,
        report=shown,
        unread="",
        names=names_of(loaded),
        trust=trust,
        evidence=EVIDENCE,
        **more,
    )


def page_of(loaded: Loaded, **more) -> Node:
    return parse(render_bring_in(data_of(loaded, **more)).decode())


def flat(node: Node) -> str:
    """The words of a node as read: no space is left before a stop or a colon, which markup
    around a date or an identifier leaves."""
    return re.sub(r" ([.,:;])", r"\1", node.text())


def account_block(root: Node, label: str) -> Node:
    return next(
        s for s in elements(root, "section") if s.attrs.get("aria-label") == label
    )


def _labels(root: Node) -> list[str]:
    """The name of each account block, in the order the page lists them."""
    return [
        s.attrs["aria-label"] for s in elements(root, "section") if "bi-account" in s.classes
    ]


def _upload_form(root: Node) -> Node:
    return next(f for f in elements(root, "form") if f.attrs.get("action") == "/bring-in")


def rows_of(block: Node) -> list[Node]:
    return [li for li in elements(block, "li") if "bi-file" in li.classes]


class TestWhatIsWanted:
    def test_Page_WhenFilesAreMissing_HeadsTheListWithTheCountsOfStatementsExportsAndAccounts(
        self, world
    ):
        wanted = next(s for s in elements(page_of(world), "section") if "bi-wanted" in s.classes)
        heading = next(elements(wanted, "h2"))

        assert heading.text() == "Wanted: 9 statements and 2 exports for 8 accounts"

    def test_Page_GroupsTheFilesByAccountAndNeverByBank(self, world):
        root = page_of(world)
        blocks = _labels(root)

        assert len(blocks) == len(set(blocks)) == 8
        assert {"Behind card", "Late-one card", "Hole card", "Virgin card", "Main account",
                "Early card"} <= set(blocks)
        assert len(rows_of(account_block(root, "Behind card"))) == 2
        assert len(rows_of(account_block(root, "Main account"))) == 2
        assert sum(len(rows_of(account_block(root, label))) for label in blocks) == 11

    def test_Page_ListsTheMostUrgentAccountFirst(self, world):
        blocks = _labels(page_of(world))
        order = ("Late-one card", "Behind card", "Hole card", "Virgin card", "Main account")

        assert [b for b in blocks if b in order] == list(order)

    def test_Page_ForAccountsThatWantNothingOrOnlyABalance_HasNoBlockAtAll(self, world):
        root = page_of(world)
        labels = {s.attrs.get("aria-label") for s in elements(root, "section")}

        for quiet in ("Quiet card", "Single card", "Hand savings", "Feed-only card", "Qif card"):
            assert quiet not in labels, quiet

    def test_File_WhenAStatementIsWaiting_SaysItsDaysItsReasonAndItsAge(self, world):
        first = rows_of(account_block(page_of(world), "Behind card"))[0]

        assert first.text().startswith("Statement 2026-07-11 to 2026-08-10")
        assert "out since 2026-08-10 (8 weeks ago)" in first.text()
        assert [age.text() for age in elements(first, "span") if "age" in age.classes] == [
            "(8 weeks ago)"
        ]

    def test_File_WhenTheDaysAreAnInference_IsDrawnDashed(self, world):
        root = page_of(world)

        assert "guess" in rows_of(account_block(root, "Hole card"))[0].classes
        assert "guess" not in rows_of(account_block(root, "Virgin card"))[0].classes

    def test_File_EachOffersToBeSetAsideWithTheExactDaysAndSource(self, world):
        root = page_of(world)
        virgin = next(elements(rows_of(account_block(root, "Virgin card"))[0], "a"))
        export = next(elements(rows_of(account_block(root, "Main account"))[0], "a"))

        assert virgin.text() == "Set aside…"
        assert virgin.attrs["href"] == (
            "/gaps-mark?account=card-virgin&first=2026-06-05&last=2026-07-04"
        )
        assert parse_qs(urlparse(export.attrs["href"]).query)["source"] == ["starling-csv"]

    def test_Account_SaysHowFarItCanBeTrustedAndDrawsItsBarWithTheFilesWantedDashedBeneath(
        self, world
    ):
        block = account_block(page_of(world), "Behind card")
        sentence = next(p for p in elements(block, "p") if "bi-trust" in p.classes)
        lanes = [s.text() for s in elements(block, "span") if "lane" in s.classes]
        wanted_cells = [i for i in elements(block, "i") if "b-want" in i.classes]

        assert flat(sentence) == "Adds up to the known balances to 2026-07-10."
        assert lanes == ["Trust", "Statements"]
        assert len(wanted_cells) >= 2

    def test_Account_WhenOnlyExportsAreWanted_DrawsAnExportsLaneAndNoStatementsLane(self, world):
        lanes = [
            s.text()
            for s in elements(account_block(page_of(world), "Main account"), "span")
            if "lane" in s.classes
        ]

        assert lanes == ["Trust", "Exports"]

    def test_Account_NamesItselfAsALinkToItsPageAndOffersAnUploadScopedToIt(self, world):
        block = account_block(page_of(world), "Virgin card")
        links = {a.text(): a for a in elements(block, "a")}

        assert links["Virgin card"].attrs["href"] == "/ledger?ref=card-virgin"
        assert links["Upload"].attrs["href"] == "/bring-in?account=card-virgin#upload"
        assert links["Upload"].attrs["aria-label"] == "Upload for Virgin card"

    def test_Account_WhenAConnectionNamesTheBank_SaysItBeside(self, world):
        root = page_of(world, banks={"card-virgin": ("virgin-money",)})

        assert "virgin-money" in account_block(root, "Virgin card").text()
        assert "virgin-money" not in account_block(root, "Hole card").text()

    def test_Account_WhenAConnectionNamesNoBank_SaysNothingOfOne(self, world):
        heading = next(elements(account_block(page_of(world), "Virgin card"), "h3"))

        assert heading.text() == "Virgin card Upload"


class TestWhenNothingIsWanted:
    def quiet(self, world: Loaded) -> FetchReport:
        return FetchReport(tuple(o for o in world.report.accounts if not o.gaps), TODAY)

    def test_Page_WhenNothingIsWanted_SaysSoInOneLineWithTheTargetAndTheEvidenceLine(
        self, world
    ):
        root = page_of(world, report=self.quiet(world))
        text = root.text()

        assert "Nothing is wanted." in text
        assert "Wanted:" not in text
        assert next(elements(root, "input", "form"), None) is not None
        assert any(f.attrs.get("enctype") == "multipart/form-data" for f in elements(root, "form"))
        assert next(
            s for s in elements(root, "summary")
        ).text() == "Every source looked at today at 08:12"
        assert not [s for s in elements(root, "section") if "bi-account" in s.classes]

    def test_Page_WhenWhatIsWantedCouldNotBeWorkedOut_SaysSoAndNeverThatNothingIsWanted(
        self, world
    ):
        data = BringInData(
            today=TODAY, report=None, unread="What is wanted could not be worked out just now.",
            names=names_of(world), evidence=EVIDENCE,
        )
        text = parse(render_bring_in(data).decode()).text()

        assert "could not be worked out just now" in text
        assert "Nothing is wanted." not in text


class TestTheTarget:
    def test_Target_TakesStatementsAndExportsTogetherSeveralAtOnce(self, world):
        form = _upload_form(page_of(world))
        box = next(elements(form, "input"))

        assert form.attrs["method"] == "post"
        assert form.attrs["enctype"] == "multipart/form-data"
        assert box.attrs["type"] == "file" and "multiple" in box.attrs
        assert ".pdf" in box.attrs["accept"] and ".csv" in box.attrs["accept"]
        assert ".qif" in box.attrs["accept"]

    def test_Target_ComesBeforeTheFirstFileWanted(self, world):
        page = render_bring_in(data_of(world)).decode()

        assert page.index('action="/bring-in"') < page.index('class="bi-file')

    def test_Target_WhenScopedToAnAccount_SaysSoAndCarriesTheAccountWithTheUpload(self, world):
        root = page_of(world, scoped="card-virgin")
        form = next(f for f in elements(root, "form") if f.attrs.get("action") == "/bring-in")
        hidden = [i for i in elements(form, "input") if i.attrs.get("name") == "account"]

        assert hidden[0].attrs["value"] == "card-virgin"
        assert "For Virgin card." in form.text()
        assert any(a.text() == "Any account" for a in elements(form, "a"))

    def test_Page_WhenKeptStatementsWaitForAnAccount_SaysSoOnceWithItsControl(self, world):
        root = page_of(world, kept_waiting=2, kept=5)
        notice = next(p for p in elements(root, "p") if "bi-notice" in p.classes)

        assert notice.text() == "2 kept statements are waiting for an account. Give them one"
        assert next(elements(notice, "a")).attrs["href"] == "/statements"

    def test_Page_WhenNoKeptStatementWaits_SaysNothingOfThem(self, world):
        assert not [p for p in elements(page_of(world, kept=5), "p") if "bi-notice" in p.classes]


class TestAfterAnUpload:
    def results(self) -> UploadResults:
        return UploadResults(
            files=(
                FileResult("Statement-2026-08.pdf", UploadKind.STATEMENT, Outcome.PLACED,
                           account="card-behind", artefact=4),
                FileResult("transactions.csv", UploadKind.EXPORT, Outcome.HELD, token="tok-1"),
                FileResult("unreadable.pdf", UploadKind.STATEMENT, Outcome.REFUSED,
                           note="It could not be read as a PDF"),
            ),
            settled=(
                "Behind card now adds up to the known balances to 2026-09-10; it was 2026-07-10.",
            ),
            lockable=("card-behind",),
            picker='<p><select name="account"><option value="">choose</option></select></p>',
        )

    def test_Results_SayWhatEachFileSettledInTheTrustSentencesTerms(self, world):
        root = page_of(world, results=self.results())

        assert "3 files received" in root.text()
        assert (
            "Behind card now adds up to the known balances to 2026-09-10; it was 2026-07-10."
        ) in root.text()

    def test_Results_OfferTheLockOnTheAccountsPageAndNeverLockFromHere(self, world):
        root = page_of(world, results=self.results())
        lock = next(
            li for li in elements(root, "li") if li.text().startswith("Lock in Behind card")
        )
        link = next(elements(lock, "a"))

        assert link.attrs["href"] == "/ledger?ref=card-behind"
        assert not list(elements(lock, "form"))

    def test_Results_AskForTheAccountOfAnExportThatIsHeldForThatFileAlone(self, world):
        root = page_of(world, results=self.results())
        ask = next(
            li for li in elements(root, "li") if "Say which account this export" in li.text()
        )
        form = next(elements(ask, "form"))
        fields = {i.attrs.get("name"): i.attrs.get("value") for i in elements(form, "input")}

        assert form.attrs["action"] == "/upload-preview"
        assert fields["token"] == "tok-1"
        assert next(elements(form, "select")).attrs["name"] == "account"
        assert len([li for li in elements(root, "li") if "Say which account" in li.text()]) == 1

    def test_Results_ForAFileThatCouldNotBeRead_SayWhyAndAskNothing(self, world):
        root = page_of(world, results=self.results())
        held = next(d for d in elements(root, "details") if "What each file held" in d.text())

        assert "unreadable.pdf: not read in: It could not be read as a PDF." in flat(held)

    def test_Page_AfterAnUpload_ListsWhatIsStillWantedUnderItsOwnHeading(self, world):
        root = page_of(world, results=self.results())

        assert "Still wanted: 9 statements and 2 exports for 8 accounts" in root.text()


class TestEvidence:
    def sources(self, tmp_path: Path, answered: dict[str, str], *names: str, feed: bool = False):
        store = ConnectionStore(tmp_path / "banks.json")
        for name in names:
            store.put(
                Connection(
                    connection_id=name, provider="p", refresh_token="r",
                    consent_expires_at="2099-06-15T00:00:00+00:00",
                )
            )
        now = datetime(2026, 10, 5, 12, 0, tzinfo=UTC)
        return source_lines(store, answered, feed_configured=feed, now=now)

    def test_Line_WhenEverySourceAnsweredToday_SaysTheTimeTheStalestDid(self, tmp_path):
        found = self.sources(
            tmp_path,
            {"halifax": "2026-10-05T07:55:00+00:00", "monzo": "2026-10-05T08:12:00+00:00"},
            "halifax", "monzo",
        )

        assert found.summary == "Every source looked at today at 07:55"
        assert "consent lasts until 2099-06-15" in " ".join(found.lines)

    def test_Line_WhenASourceHasNotAnsweredToday_SaysHowManyHaveNot(self, tmp_path):
        found = self.sources(
            tmp_path,
            {"halifax": "2026-10-05T07:55:00+00:00", "monzo": "2026-10-03T08:12:00+00:00"},
            "halifax", "monzo",
        )

        assert found.summary == "1 source not looked at today"

    def test_Line_WhenASourceHasNeverAnswered_SaysSoAmongItsLines(self, tmp_path):
        found = self.sources(tmp_path, {}, "halifax")

        assert found.summary == "1 source not looked at today"
        assert any("has never answered" in line for line in found.lines)

    def test_Line_WhenNoBankIsConnected_SaysSoAndNeverThatEverySourceWasLookedAt(self, tmp_path):
        found = self.sources(tmp_path, {})

        assert found.summary == "No bank is connected"

    def test_Line_WithTheBanksOwnFeed_CountsItAsASourceAndNamesItsLastAnswer(self, tmp_path):
        found = self.sources(
            tmp_path, {"starling-api": "2026-10-05T07:00:00+00:00"}, feed=True
        )

        assert found.summary == "Every source looked at today at 07:00"
        assert "The bank's own feed: last answered 2026-10-05 07:00." in found.lines


FEED_ANSWER = "2026-10-04T15:57:12.345678+00:00"


def _bank(name: str, consent: str = "2099-06-15T00:00:00+00:00") -> Connection:
    return Connection(
        connection_id=name, provider="p", refresh_token="r", consent_expires_at=consent
    )


def _banks(tmp_path: Path, *names: str) -> ConnectionStore:
    store = ConnectionStore(tmp_path / "banks.json")
    for name in names:
        store.put(_bank(name))
    return store


def _kept(account: str, *, parser: str | None = "uk", refusal: str = "") -> dict[str, object]:
    return {
        "id": 1, "account_ref": account, "parser": parser, "refusal": refusal, "origin": "x.pdf",
    }


class TestTheEvidenceLineOverHttp:
    """The hooks the old hub read - the connections, the bank's own feed, the kept statements -
    now say their state inside the one evidence line, and the page still answers when one fails."""

    @pytest.fixture(autouse=True)
    def live_instance(self, monkeypatch):
        monkeypatch.setenv("OBDI_INSTANCE_LABEL", "obdi")
        monkeypatch.setenv("OBDI_INSTANCE_ROLE", "production")

    def page(self, serve_hub, tmp_path, *names: str, **hooks) -> str:
        base = serve_hub(_banks(tmp_path, *names), **hooks)
        return httpx.get(f"{base}/bring-in", timeout=20).text

    def test_Page_WithNoBank_SaysNoBankIsConnected(self, serve_hub, tmp_path):
        page = parse(self.page(serve_hub, tmp_path))

        assert next(elements(page, "summary")).text() == "No bank is connected"

    def test_Page_WithTwoBanks_SaysWhoLastAnsweredWhenAndWhoNever(self, serve_hub, tmp_path):
        page = self.page(
            serve_hub, tmp_path, "halifax", "monzo",
            connection_last_answered=lambda: {"halifax": "2026-10-01T09:15:00+00:00"},
        )
        said = flat(parse(page))

        assert (
            "halifax, through the aggregator: last answered 2026-10-01 09:15; consent lasts "
            "until 2099-06-15."
        ) in said
        assert "monzo, through the aggregator: has never answered; consent lasts" in said
        assert "2 sources not looked at today" in said
        assert page.count("Times are UTC.") == 1, "the zone is said once, never as a trailing Z"
        assert "09:15Z" not in page

    def test_Page_WithTheBanksOwnFeed_NamesItsLastAnswerAndNeverAToken(self, serve_hub, tmp_path):
        page = self.page(
            serve_hub, tmp_path,
            starling_probe=lambda cutoff: None,
            connection_last_answered=lambda: {"starling-api": FEED_ANSWER},
        )

        assert "The bank's own feed: last answered 2026-10-04 15:57." in flat(parse(page))
        assert "OBDI_" not in page and "STARLING_PERSONAL" not in page
        assert not re.search(r"\d{2}:\d{2}Z|\d{2}:\d{2}:\d{2}", page.split("<body")[1])

    def test_Page_WhenTheLastAnsweredHookRaises_StillListsTheBanksAsNeverAnswered(
        self, serve_hub, tmp_path
    ):
        def boom() -> dict[str, str]:
            raise OSError("locked")

        page = self.page(serve_hub, tmp_path, "halifax", connection_last_answered=boom)

        assert "has never answered" in page and "locked" not in page

    def test_Page_WhenStatementsAreKept_CountsThemAndSaysHowManyWaitForAnAccount(
        self, serve_hub, tmp_path
    ):
        kept = [
            _kept("acct-1"),
            _kept(UNASSIGNED),
            _kept(UNASSIGNED, parser=None),
            _kept("acct-2", refusal="balances do not carry"),
        ]
        root = parse(self.page(serve_hub, tmp_path, kept_statements=lambda: kept))

        assert "4 files kept" in root.text()
        assert (
            "1 kept statement is waiting for an account. Give it one" in flat(root)
        )

    def test_Page_WhenEveryKeptStatementIsFiled_SaysNothingOfWaiting(self, serve_hub, tmp_path):
        kept = [_kept("acct-1"), _kept("acct-2")]
        root = parse(self.page(serve_hub, tmp_path, kept_statements=lambda: kept))

        assert "2 files kept" in root.text() and "waiting for an account" not in root.text()

    def test_Page_WhenKeptStatementsCannotBeRead_StillAnswersAndSaysNothingOfThem(
        self, serve_hub, tmp_path
    ):
        def boom() -> list[dict[str, object]]:
            raise OSError("locked")

        base = serve_hub(_banks(tmp_path), kept_statements=boom)
        response = httpx.get(f"{base}/bring-in", timeout=20)

        assert response.status_code == 200 and "locked" not in response.text

    def test_Page_LinksToTheConnectionsPageInOneLine(self, serve_hub, tmp_path):
        main = next(elements(parse(self.page(serve_hub, tmp_path)), "main"))
        links = [a for a in elements(main, "a") if a.attrs.get("href") == "/connections"]

        assert [a.text() for a in links] == ["Bank connections"]

    def test_Page_ShowsNoAmountOrDescription(self, serve_hub, tmp_path):
        page = self.page(serve_hub, tmp_path, kept_statements=lambda: [_kept("acct-1")])
        main = re.sub(r"<style>.*?</style>", "", page.split("<main", 1)[1], flags=re.S)

        assert not re.search(r"\d[\d,]*\.\d{2}\b", main)


class TestWhatThePageMustNotHold:
    def test_Page_HoldsNoAmountNoDescriptionAndNoScript(self, world):
        page = render_bring_in(data_of(world, results=TestAfterAnUpload().results())).decode()
        visible = re.sub(r"<style>.*?</style>", "", page, flags=re.S)

        assert MONEY_FIGURE.search(visible) is None
        for figure in world.house.figures:
            assert figure not in page, figure
        for payee in world.house.payees:
            assert payee not in page, payee
        assert "<script" not in page

    def test_Page_UsesNoRetiredPhraseAndNeverCallsATransactionARow(self, world):
        text = page_of(world, results=TestAfterAnUpload().results()).text().lower()

        for phrase in RETIRED_ON_PAGES:
            assert phrase not in text, phrase
        assert not re.search(r"\brows?\b", text)

    def test_Page_SaysNoLineOfThreeOrMoreWordsMoreThanTwice(self, world):
        assert repeated_lines(page_of(world)) == {}

    def test_Refusal_OfAnUploadThatStatesFigures_IsMaskedAndKeepsItsWords(self):
        said = _mask("lines sum to 12.50 but the statement says 13.00")

        assert said == "lines sum to 99.99 but the statement says 99.99"


# ----------------------------------------------------------------------------------------- HTTP


def _statement_pdf(closing: date, payee: str, minor: int, owed: int = 10000) -> bytes:
    """A Santander statement: one transaction five days before its closing, from 100.00 owed."""
    when = date.fromordinal(closing.toordinal() - 5)
    lines = [
        "Santander UK plc. Registered Office: 2 Triton Square",
        f"Statement Date: {ordinal(closing)} {MONTHS[closing.month - 1]} {closing.year}"
        "      Page No: 1 / 1",
        "Account credit limit:            3,000.00",
        f"Balance brought forward from previous statement          {_pounds(owed)}",
        f"{ordinal(when)} {MONTHS[when.month - 1]} {payee}   {_pounds(minor)}",
        f"Your new balance:                                        {_pounds(owed + minor)}",
    ]
    return build_pdf(lines)


CSV = (
    b"Date,Counter Party,Reference,Type,Amount (GBP),Balance (GBP)\n"
    b"12/08/2026,Invented Export Payee,ref,CARD,-12.34,0\n"
)


def _pdf_part(name: str = "Statement-2026-09.pdf") -> tuple[str, tuple[str, bytes, str]]:
    pdf = _statement_pdf(D(2026, 9, 10), "Up Zeppelin", 1500)
    return ("file", (name, pdf, "application/pdf"))


def _csv_part() -> tuple[str, tuple[str, bytes, str]]:
    return ("file", ("transactions.csv", CSV, "text/csv"))


def _land_household(root: Path) -> None:
    build_household(root)
    with Store(root / "store.sqlite3") as store:
        store.declare_account(AccountRecord(ref=AccountRef("up-card"), label="Up card"))


@pytest.fixture
def served(tmp_path):
    _land_household(tmp_path)
    with served_store(tmp_path, lambda store: None, bound=[]) as base:
        yield base


def text_of(response: httpx.Response) -> str:
    return flat(parse(response.text))


class TestOneUploadTargetOverHttp:
    def test_Gaps_WhenAskedFor_RedirectsToBringInKeepingTheAccountAnchor(self, served):
        plain = httpx.get(f"{served}/gaps", follow_redirects=False)
        scoped = httpx.get(f"{served}/gaps?ref=card-virgin", follow_redirects=False)

        assert plain.status_code == scoped.status_code == 302
        assert plain.headers["location"] == "/bring-in"
        assert scoped.headers["location"] == "/bring-in#account-card-virgin"

    def test_Page_WhenServed_IsUnderBringInAndHoldsTheTargetAndTheListOfFiles(self, served):
        page = httpx.get(f"{served}/bring-in", timeout=60)

        assert page.status_code == 200
        assert 'aria-current="page">Bring in<' in page.text
        assert 'action="/bring-in"' in page.text
        assert "Wanted:" in text_of(page)
        assert 'id="account-card-virgin"' in page.text

    def test_Upload_OfAPdfAndACsvTogetherScopedToAnAccount_ReadsInTheOneAndHoldsTheOther(
        self, served
    ):
        response = httpx.post(
            f"{served}/bring-in",
            data={"account": "up-card"},
            files=[_pdf_part(), _csv_part()],
            timeout=60,
        )
        said = text_of(response)

        assert response.status_code == 200
        assert response.headers["cache-control"] == "no-store"
        assert "2 files received" in said
        assert (
            "Up card now adds up to the known balances to 2026-09-10; "
            "it had nothing to check against."
        ) in said
        assert "Statement-2026-09.pdf: read in to Up card." in said
        assert "transactions.csv: held, ready to check against Up card." in said
        held = next(
            f for f in parse(response.text).descendants()
            if f.tag == "form" and f.attrs.get("action") == "/upload-preview"
        )
        assert {i.attrs.get("name") for i in elements(held, "input")} == {"token", "account"}
        rows = elements(parse(response.text), "li")
        asked = " ".join(li.text() for li in rows if "Say which account" in li.text())
        assert "Statement-2026-09.pdf" not in asked

    def test_Upload_WhenNoAccountIsGiven_KeepsTheStatementAndAsksForThatFileAlone(self, served):
        response = httpx.post(
            f"{served}/bring-in",
            files=[_pdf_part(), _csv_part()],
            timeout=60,
        )
        root = parse(response.text)
        asks = [li for li in elements(root, "li") if "Say which account" in li.text()]

        assert [a.text().split(" ")[4] for a in asks] == ["statement", "export"]
        assert {next(elements(a, "form")).attrs["action"] for a in asks} == {
            "/statement-assign", "/upload-preview",
        }
        assert "kept, waiting for an account" in root.text()

    def test_Upload_OfAKeptStatementThroughTheAskedForm_IsReadInByTheExistingDoor(self, served):
        kept = httpx.post(
            f"{served}/bring-in",
            files=[("file", ("s.pdf", _statement_pdf(D(2026, 9, 10), "Up Zeppelin", 1500),
                             "application/pdf"))],
            timeout=60,
        )
        form = next(f for f in elements(parse(kept.text), "form")
                    if f.attrs.get("action") == "/statement-assign")
        artefact = next(i for i in elements(form, "input") if i.attrs.get("name") == "artefact")

        answer = httpx.post(
            f"{served}/statement-assign",
            data={"artefact": artefact.attrs["value"], "account": "up-card"},
            timeout=60,
        )

        assert answer.status_code == 200
        assert "Read in" in text_of(answer)

    def test_Upload_OfAKeptStatementThroughTheAskedForm_AnswersWithBringInAndWhatItSettled(
        self, served
    ):
        kept = httpx.post(
            f"{served}/bring-in",
            files=[("file", ("s.pdf", _statement_pdf(D(2026, 9, 10), "Up Zeppelin", 1500),
                             "application/pdf"))],
            timeout=60,
        )
        form = next(f for f in elements(parse(kept.text), "form")
                    if f.attrs.get("action") == "/statement-assign")
        artefact = next(i for i in elements(form, "input") if i.attrs.get("name") == "artefact")

        answer = httpx.post(
            f"{served}/statement-assign",
            data={"artefact": artefact.attrs["value"], "account": "up-card"},
            headers={"Referer": f"{served}/bring-in"},
            timeout=60,
        )

        root = parse(answer.text)
        said = flat(root)
        assert answer.status_code == 200
        assert answer.headers["cache-control"] == "no-store"
        assert "1 file received" in said
        assert "read in to Up card." in said
        assert any(f.attrs.get("action") == "/bring-in" for f in elements(root, "form"))
        assert "Read another" not in {a.text() for a in elements(root, "a")}

    def test_Upload_OfAKeptStatementThroughTheKeptStatementsPage_KeepsTheOldAnswer(self, served):
        kept = httpx.post(
            f"{served}/bring-in",
            files=[("file", ("s.pdf", _statement_pdf(D(2026, 9, 10), "Up Zeppelin", 1500),
                             "application/pdf"))],
            timeout=60,
        )
        form = next(f for f in elements(parse(kept.text), "form")
                    if f.attrs.get("action") == "/statement-assign")
        artefact = next(i for i in elements(form, "input") if i.attrs.get("name") == "artefact")

        answer = httpx.post(
            f"{served}/statement-assign",
            data={"artefact": artefact.attrs["value"], "account": "up-card"},
            headers={"Referer": f"{served}/statements"},
            timeout=60,
        )

        assert "Read another" in {a.text() for a in elements(parse(answer.text), "a")}

    def test_Upload_OfAHeldExportThroughTheAskedForm_PreviewsAndImportsAndLeadsBackToBringIn(
        self, served
    ):
        held = httpx.post(
            f"{served}/bring-in",
            data={"account": "up-card"},
            files=[("file", ("transactions.csv", CSV, "text/csv"))],
            timeout=60,
        )
        form = next(f for f in elements(parse(held.text), "form")
                    if f.attrs.get("action") == "/upload-preview")
        fields = {i.attrs["name"]: i.attrs["value"] for i in elements(form, "input")}

        preview = httpx.post(
            f"{served}/upload-preview",
            data=fields,
            headers={"Referer": f"{served}/bring-in"},
            timeout=60,
        )

        previewed = parse(preview.text)
        back = [a.text() for a in elements(previewed, "a")]
        assert back.count("Back to Bring in") == 1 and "Back to import" not in back
        confirm = next(f for f in elements(previewed, "form")
                       if f.attrs.get("action") == "/upload-confirm")
        confirming = {i.attrs["name"]: i.attrs["value"] for i in elements(confirm, "input")
                      if "name" in i.attrs and "value" in i.attrs}
        imported = httpx.post(f"{served}/upload-confirm", data=confirming, timeout=60)

        assert imported.status_code == 200
        done = [a.text() for a in elements(parse(imported.text), "a")]
        assert done.count("Back to Bring in") == 1 and "Back to import" not in done

    def test_Upload_OfAHeldExportFromTheImportPage_StillLeadsBackToImport(self, served):
        held = httpx.post(
            f"{served}/bring-in",
            data={"account": "up-card"},
            files=[("file", ("transactions.csv", CSV, "text/csv"))],
            timeout=60,
        )
        form = next(f for f in elements(parse(held.text), "form")
                    if f.attrs.get("action") == "/upload-preview")
        fields = {i.attrs["name"]: i.attrs["value"] for i in elements(form, "input")}

        preview = httpx.post(
            f"{served}/upload-preview",
            data=fields,
            headers={"Referer": f"{served}/import"},
            timeout=60,
        )

        said = [a.text() for a in elements(parse(preview.text), "a")]
        assert "Back to import" in said

    def test_Upload_OfAHeldExportThroughTheAskedForm_IsPreviewedByTheExistingDoor(self, served):
        held = httpx.post(
            f"{served}/bring-in",
            data={"account": "up-card"},
            files=[("file", ("transactions.csv", CSV, "text/csv"))],
            timeout=60,
        )
        form = next(f for f in elements(parse(held.text), "form")
                    if f.attrs.get("action") == "/upload-preview")
        fields = {i.attrs["name"]: i.attrs["value"] for i in elements(form, "input")}

        preview = httpx.post(f"{served}/upload-preview", data=fields, timeout=60)

        assert preview.status_code == 200
        said = text_of(preview)
        assert "parsed as" in said and "Nothing has been stored yet." in said

    def test_Upload_OfAFileThatIsNotAPdfWithAPdfsName_IsRefusedAndSaysItWasNotKept(self, served):
        response = httpx.post(
            f"{served}/bring-in",
            data={"account": "up-card"},
            files=[("file", ("bad.pdf", b"not a pdf at all", "application/pdf"))],
            timeout=60,
        )
        said = text_of(response)

        assert response.status_code == 200
        assert "bad.pdf: not read in: It could not be read as a PDF with text in it" in said
        assert "Say which account" not in said

    def test_Upload_ToAnAccountThatDoesNotExist_IsRefusedBeforeAnythingIsRead(self, served):
        response = httpx.post(
            f"{served}/bring-in",
            data={"account": "nobody"},
            files=[("file", ("transactions.csv", CSV, "text/csv"))],
            timeout=60,
        )

        assert response.status_code == 400
        assert "That is not an account obdi knows." in text_of(response)

    def test_Upload_WithNoFile_AsksForOneAndChangesNothing(self, served):
        response = httpx.post(
            f"{served}/bring-in", data={"account": ""}, files={"file": ("", b"", "text/csv")},
            timeout=60,
        )

        assert response.status_code == 400
        assert "No file was chosen." in text_of(response)

    def test_Upload_FromAnotherSite_IsRefused(self, served):
        response = httpx.post(
            f"{served}/bring-in", headers={"Origin": "http://evil.example"},
            files=[("file", ("transactions.csv", CSV, "text/csv"))],
        )

        assert response.status_code == 403


class TestSettingAFileAsideRoundTrip:
    def test_SetAside_AKnownGap_TakesTheFileOffTheListAndUndoBringsItBack(self, served):
        def virgin_rows() -> int:
            root = parse(httpx.get(f"{served}/bring-in", timeout=60).text)
            return sum(
                len(rows_of(s)) for s in elements(root, "section")
                if s.attrs.get("aria-label") == "Virgin card"
            )

        before = virgin_rows()
        httpx.post(f"{served}/gaps-mark", data={
            "account": "card-virgin", "source": "", "first": "2026-06-05", "last": "2026-07-04",
            "kind": "known-gap", "step": "mark",
        }, timeout=60)
        during = httpx.get(f"{served}/bring-in", timeout=60).text
        mark_id = re.search(r'name="id" value="(\d+)"', during).group(1)
        done = httpx.post(f"{served}/gaps-mark-undo", data={"id": mark_id}, timeout=60)

        assert before == 1
        assert "Set aside by your decision (1)" in during
        assert 'aria-label="Virgin card"' not in during
        assert 'href="/bring-in"' in done.text and "Back to Bring in" in text_of(done)
        assert virgin_rows() == 1

    def test_SetAside_LinkFromTheList_OpensTheFormForExactlyThatFilesDays(self, served):
        root = parse(httpx.get(f"{served}/bring-in", timeout=60).text)
        link = next(elements(rows_of(account_block(root, "Virgin card"))[0], "a"))

        form = httpx.get(f"{served}{link.attrs['href']}", timeout=60)
        said = text_of(form)

        assert form.status_code == 200
        assert "Virgin card" in said and "2026-06-05" in form.text and "2026-07-04" in form.text
        assert form.text.count('type="radio" name="kind"') == 6
        assert "Back to Bring in" in said
