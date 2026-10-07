"""Finding every kept statement, and giving a month's worth an account at once.

A statement is kept as evidence before anyone decides whose it is, and the
only list of what was kept used to be the newest five hundred artefacts of
any kind - so statements kept a little while ago were not on it at all, and
the one link that promised "every statement kept so far" led to a page that
held none. These tests land the statements, then bury them, then ask.

The statements here are invented: a distinctive payee and figure sit in one
so that "the page shows nothing from the statements" is a claim that can be
wrong, not one that is true because nothing was there to show.
"""

from __future__ import annotations

import re
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest

from obdi.cli import build_web_config
from obdi.core.models import RawArtefact
from obdi.ingest.identity import artefact_digest
from obdi.ingest.statement_extraction import keep_extraction
from obdi.ingest.store import Store
from obdi.pages.web import AuthorisationSession, ConnectionHandler
from test_pdf_import import BROKEN, SANTANDER_AS_WRITTEN, UNKNOWN_BANK
from test_statement_shape import build_pdf

UNASSIGNED = "(unassigned)"
ACCOUNT = "santander-cc"

#: Two rows that carry 500.00 to 420.00, so it balances on its own.
SHORT_MONTH = build_pdf(
    [
        "Santander UK plc. Registered Office: 2 Triton Square",
        "Statement Date: 11th June 2026      Page No: 1 / 1",
        "Balance brought forward from previous statement          500.00",
        "2nd Jun     OTHER SHOP LEEDS GB                             20.00",
        "4th Jun     Direct Payment                     CR          100.00",
        "Your new balance:                                        420.00",
    ]
)

#: A balanced statement carrying a payee and a figure that exist nowhere else.
DISTINCTIVE = build_pdf(
    [
        "Santander UK plc. Registered Office: 2 Triton Square",
        "Statement Date: 11th August 2026      Page No: 1 / 1",
        "Balance brought forward from previous statement          7,654.32",
        "2nd Aug     ZEBRAQUARTZ HOLDINGS LONDON GB                   0.00",
        "Your new balance:                                      7,654.32",
    ]
)


def _keep(
    store: Store,
    payload: bytes,
    name: str,
    *,
    account: str = UNASSIGNED,
    at: datetime | None = None,
) -> None:
    """Land a statement the way the statement-shape page keeps one, and extract it as keeping does
    (a page never reads a PDF, so the listing says only what was extracted)."""
    store.land_artefact(
        RawArtefact(
            source="statement",
            account_ref=account,
            fetched_at=at or datetime.now(UTC),
            media_type="application/pdf",
            digest=artefact_digest(payload),
            payload=payload,
            origin=name,
        )
    )
    keep_extraction(store, artefact_digest(payload), payload)
    store.connection.commit()


def _land_other(store: Store, count: int, *, after: datetime) -> None:
    """Artefacts that are not statements, each newer than `after`."""
    for number in range(count):
        payload = f'{{"results": [], "n": {number}}}'.encode()
        store.land_artefact(
            RawArtefact(
                source="starling",
                account_ref="starling-current",
                fetched_at=after + timedelta(seconds=number + 1),
                media_type="application/json",
                digest=artefact_digest(payload),
                payload=payload,
                origin=f"feed-{number}.json",
            )
        )


def _id_of(db: Path, name: str) -> int:
    with Store(db) as store:
        row = store.connection.execute(
            "SELECT rowid FROM raw_artefacts WHERE origin = ?", (name,)
        ).fetchone()
    assert row is not None, name
    return int(row["rowid"])


def _account_of(db: Path, name: str) -> str:
    with Store(db) as store:
        row = store.connection.execute(
            "SELECT account_ref FROM raw_artefacts WHERE origin = ?", (name,)
        ).fetchone()
    assert row is not None, name
    return str(row["account_ref"])


def _transactions(db: Path) -> list[str]:
    with Store(db) as store:
        return [row.account_id for row in store.all_transactions()]


@pytest.fixture
def db(tmp_path: Path) -> Path:
    path = tmp_path / "store.sqlite3"
    with Store(path):
        pass
    return path


@pytest.fixture
def serve(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """The real handler over the real config, for whichever store is given."""
    monkeypatch.setenv("OBDI_CONNECTION_STORE", str(tmp_path / "connections.json"))
    monkeypatch.setenv("OBDI_ACCOUNT_MAP", str(tmp_path / "accounts.json"))
    monkeypatch.setenv("OBDI_INSTANCE_LABEL", "obdi")
    monkeypatch.setenv("OBDI_INSTANCE_ROLE", "production")
    for variable in ("TRUELAYER_CLIENT_ID", "TRUELAYER_CLIENT_SECRET_FILE"):
        monkeypatch.delenv(variable, raising=False)
    servers: list[tuple[object, threading.Thread]] = []

    def start(store_path: Path) -> str:
        config = build_web_config(store_path)
        assert config is not None
        handler = type(
            "KeptStatementsHandler",
            (ConnectionHandler,),
            {"config": config, "session": AuthorisationSession()},
        )
        httpd = ConnectionHandler.make_server(("127.0.0.1", 0), handler)
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        servers.append((httpd, thread))
        return f"http://127.0.0.1:{httpd.server_port}"

    yield start
    for httpd, _ in servers:
        httpd.shutdown()  # type: ignore[attr-defined]
        httpd.server_close()  # type: ignore[attr-defined]


def _post(base: str, route: str, data: dict[str, str]) -> httpx.Response:
    return httpx.post(f"{base}{route}", data=data, headers={"Origin": base}, timeout=60)


def _group(page: str, heading: str) -> str:
    """The text of one group, from its heading to the next heading."""
    start = page.index(heading)
    following = re.search(r'<details class="kept-group"', page[start + len(heading) :])
    end = start + len(heading) + following.start() if following else len(page)
    return page[start:end]


@pytest.fixture
def one_of_each(db: Path) -> Path:
    with Store(db) as store:
        _keep(store, SANTANDER_AS_WRITTEN, "2026.05 - Example Card.pdf")
        _keep(store, UNKNOWN_BANK, "2026.05 - Other Bank.pdf")
        _keep(store, SHORT_MONTH, "2026.04 - Example Card.pdf", account=ACCOUNT)
    return db


class TestTheKeptStatementsPage:
    def test_KeptStatements_WithOneOfEachKind_ListsEachInItsGroupWithCounts(
        self, serve, one_of_each
    ):
        page = httpx.get(f"{serve(one_of_each)}/statements", timeout=60)

        assert page.status_code == 200
        assert "1 waiting only for an account, 1 with no parser yet, 1 assigned" in (page.text)
        waiting = _group(page.text, "Waiting only for an account (1)")
        assert "2026.05 - Example Card.pdf" in waiting
        assert "santander-cc-pdf" in waiting
        assert "No account yet." in waiting
        nowhere = _group(page.text, "No parser yet (1)")
        assert "2026.05 - Other Bank.pdf" in nowhere
        assert "no parser for this layout yet" in nowhere
        assigned = _group(page.text, "Assigned (1)")
        assert "2026.04 - Example Card.pdf" in assigned
        assert ACCOUNT in assigned
        assert page.text.index("Waiting only") < page.text.index("No parser yet")
        assert page.text.index("No parser yet") < page.text.index("Assigned (1)")

    def test_KeptStatements_EachEntry_SaysWhichIssuerNamesItsTextHolds(self, serve, one_of_each):
        """The masked shape hides every name, so a statement with no parser
        could not be told from any other. The May statement names Santander
        twice: its registered-office line and its card fee."""
        page = httpx.get(f"{serve(one_of_each)}/statements", timeout=60).text

        waiting = _group(page, "Waiting only for an account (1)")
        assert "Names found" in waiting
        assert "Santander 2" in waiting
        nowhere = _group(page, "No parser yet (1)")
        assert "none of the issuer names looked for" in nowhere

    def test_KeptStatements_EachEntry_LinksToItsMaskedShape(self, serve, one_of_each):
        page = httpx.get(f"{serve(one_of_each)}/statements", timeout=60).text

        for name in (
            "2026.05 - Example Card.pdf",
            "2026.05 - Other Bank.pdf",
            "2026.04 - Example Card.pdf",
        ):
            assert f'href="/statement-shape?artefact={_id_of(one_of_each, name)}"' in page, name

    def test_KeptStatements_OnlyReadableUnassignedOnesOfferAnAssignControl(
        self, serve, one_of_each
    ):
        page = httpx.get(f"{serve(one_of_each)}/statements", timeout=60).text

        waiting = _group(page, "Waiting only for an account (1)")
        assert 'action="/statement-assign"' in waiting
        assert (
            f'name="artefact" value="{_id_of(one_of_each, "2026.05 - Example Card.pdf")}"'
            in waiting
        )
        assert "/statement-assign" not in _group(page, "No parser yet (1)")
        assert "/statement-assign" not in _group(page, "Assigned (1)")

    def test_KeptStatements_WithNoneKept_SaysThereAreNone(self, serve, db):
        page = httpx.get(f"{serve(db)}/statements", timeout=60)

        assert page.status_code == 200
        assert "No statements have been kept yet" in page.text
        assert "Waiting only for an account" not in page.text

    def test_KeptStatements_WithSixHundredLaterArtefacts_StillListsEveryOne(self, serve, db):
        kept_at = datetime.now(UTC) - timedelta(days=30)
        names = [f"2026.0{month} - Example Card.pdf" for month in (3, 4, 5)]
        with Store(db) as store:
            _keep(store, SANTANDER_AS_WRITTEN, names[0], at=kept_at)
            _keep(store, SHORT_MONTH, names[1], at=kept_at)
            _keep(store, UNKNOWN_BANK, names[2], at=kept_at)
            _land_other(store, 600, after=kept_at)
        base = serve(db)

        capped = httpx.get(f"{base}/artefacts", timeout=60).text
        page = httpx.get(f"{base}/statements", timeout=60).text

        assert not any(name in capped for name in names), (
            "the fixture no longer pushes statements past the artefact list's cap, "
            "so this test proves nothing"
        )
        for name in names:
            assert name in page, name
        assert "2 waiting only for an account, 1 with no parser yet, 0 assigned" in page

    def test_KeptStatements_WhenViewed_ShowsNoFigureAndNoStatementText(self, serve, db):
        with Store(db) as store:
            _keep(store, DISTINCTIVE, "2026.08 - Example Card.pdf")
        page = httpx.get(f"{serve(db)}/statements", timeout=60).text

        assert "2026.08 - Example Card.pdf" in page, "the statement was not listed"
        assert "ZEBRAQUARTZ" not in page
        assert "7,654.32" not in page and "7654.32" not in page
        assert "Balance brought forward" not in page

    def test_KeptStatements_ViewedTwice_AsksTheParserOncePerStatement(
        self, serve, one_of_each, monkeypatch
    ):
        from obdi.ingest.parsers import uk_banks

        calls: list[int] = []
        real = uk_banks.detect

        def counting(payload: bytes):  # type: ignore[no-untyped-def]
            calls.append(len(payload))
            return real(payload)

        monkeypatch.setattr(uk_banks, "detect", counting)
        base = serve(one_of_each)

        httpx.get(f"{base}/statements", timeout=60)
        first = len(calls)
        httpx.get(f"{base}/statements", timeout=60)

        assert first == 3, "each of the three statements is asked about once"
        assert len(calls) == first, "a second view re-read the statements"

    def test_KeptStatements_WhenNavigatedTo_MarksTheBringInSection(self, serve, one_of_each):
        page = httpx.get(f"{serve(one_of_each)}/statements", timeout=60).text

        assert re.search(r'<a href="/bring-in" aria-current="page">Bring in</a>', page)


class TestTheDocumentsKeptForOneAccount:
    """From an account's own page: only that account's documents, newest first, each opening its
    masked shape and saying its listed days, how many transactions, and whether it adds up by
    what it lists. Known answer over `one_of_each`: the April statement is the one kept for
    `santander-cc`, it lists 2 transactions from 2026-06-02 to 2026-06-04, and it adds up."""

    def test_AccountView_ListsOnlyThatAccountsDocumentWithItsPeriodCountAndVerdict(
        self, serve, one_of_each
    ):
        from page_dom import elements, parse

        page = parse(httpx.get(f"{serve(one_of_each)}/statements?ref={ACCOUNT}", timeout=60).text)
        lines = [" ".join(li.text().split()) for li in elements(page, "li")]
        kept = [line for line in lines if "Example Card.pdf" in line]

        assert len(kept) == 1
        assert kept[0].startswith("2026.04 - Example Card.pdf")
        assert "lists 2026-06-02 to 2026-06-04, 2 transactions, adds up by what it lists" in kept[0]
        assert "Other Bank" not in page.text() and "2026.05" not in page.text()

    def test_AccountView_LinksEachDocumentToItsShape(self, serve, one_of_each):
        page = httpx.get(f"{serve(one_of_each)}/statements?ref={ACCOUNT}", timeout=60).text

        assert (
            f'href="/statement-shape?artefact={_id_of(one_of_each, "2026.04 - Example Card.pdf")}"'
            in page
        )

    def test_AccountView_ForAnAccountWithNothingKept_SaysSo(self, serve, one_of_each):
        page = httpx.get(f"{serve(one_of_each)}/statements?ref=nobody", timeout=60).text

        assert "No statement is kept for this account yet." in page

    def test_AccountView_WithAMarkupReference_EscapesIt(self, serve, one_of_each):
        page = httpx.get(
            f"{serve(one_of_each)}/statements", params={"ref": '"><b>x'}, timeout=60
        ).text

        assert "<b>x" not in page

    def test_FullPage_GroupsTheAssignedByAccountWithALinkToEachAccountsOwnList(
        self, serve, one_of_each
    ):
        page = httpx.get(f"{serve(one_of_each)}/statements", timeout=60).text

        assert f'href="/statements?ref={ACCOUNT}"' in page


class TestAssigningManyAtOnce:
    @pytest.fixture
    def three_months(self, db: Path) -> Path:
        """March balances, April does not, May balances - landed out of order.

        Landed May, March, April so that landing order and file-name order
        disagree: the handler is held to the names.
        """
        with Store(db) as store:
            _keep(store, SANTANDER_AS_WRITTEN, "2026.05 - Example Card.pdf")
            _keep(store, SHORT_MONTH, "2026.03 - Example Card.pdf")
            _keep(store, BROKEN, "2026.04 - Example Card.pdf")
        return db

    def _ids(self, db: Path, *names: str) -> str:
        return ",".join(str(_id_of(db, name)) for name in names)

    def test_PageOffersOneBulkForm_PerParserReadingTwoOrMore(self, serve, three_months):
        """March and May read; April is recognised and refused, so it is not
        offered: a form that names it would promise a reading that fails."""
        page = httpx.get(f"{serve(three_months)}/statements", timeout=60).text

        assert page.count('action="/statements-assign"') == 1
        assert "Give these 2 statements to" in page
        form = page[page.index('action="/statements-assign"') :].split("</form>")[0]
        for name in ("2026.03 - Example Card.pdf", "2026.05 - Example Card.pdf"):
            assert f"{_id_of(three_months, name)}" in form
        ids = re.search(r'name="artefacts" value="([^"]*)"', form)
        assert ids is not None
        assert str(_id_of(three_months, "2026.04 - Example Card.pdf")) not in ids.group(1).split(
            ","
        )

    def test_KeptStatements_AStatementItsParserRefuses_IsListedApartWithTheReason(
        self, serve, three_months
    ):
        """Recognised is not readable. April's rows do not carry its opening
        balance to its closing one, so it is not "waiting only for an account"."""
        page = httpx.get(f"{serve(three_months)}/statements", timeout=60).text

        assert "2 waiting only for an account" in page
        assert "1 recognised but refused" in page
        refused = _group(page, "Recognised, but the reading is refused (1)")
        assert "2026.04 - Example Card.pdf" in refused
        assert "unexplained" in refused, "the parser's own reason"
        assert "/statement-assign" not in refused
        waiting = _group(page, "Waiting only for an account (2)")
        assert "2026.04 - Example Card.pdf" not in waiting

    def test_KeptStatements_ARefusedStatementsReason_CarriesNoFigure(self, serve, three_months):
        """The gate's message states the discrepancy; on a GET it is masked.
        April is out by 1,137.57 (1,234.56 brought forward, 3.00 spent, 99.99
        stated)."""
        page = httpx.get(f"{serve(three_months)}/statements", timeout=60).text

        assert "113757" not in page and "1,137.57" not in page
        assert "1,234.56" not in page and "123456" not in page

    def test_KeptStatements_AReadableStatement_SaysHowManyRowsItReads(self, serve, three_months):
        page = httpx.get(f"{serve(three_months)}/statements", timeout=60).text

        waiting = _group(page, "Waiting only for an account (2)")
        assert "reads 7 rows" in waiting, "May"
        assert "reads 2 rows" in waiting, "March"

    def test_BulkAssign_WhenOneStatementDoesNotBalance_ReadsTheOthersAndNamesTheRefusal(
        self, serve, three_months
    ):
        names = [f"2026.0{month} - Example Card.pdf" for month in (5, 3, 4)]
        response = _post(
            serve(three_months),
            "/statements-assign",
            {"artefacts": self._ids(three_months, *names), "account": ACCOUNT},
        )

        assert response.status_code == 200
        page = response.text
        assert "unexplained" in page, "the gate's own words must reach the page"
        positions = [page.index(f"2026.0{m} - Example Card.pdf") for m in (3, 4, 5)]
        assert positions == sorted(positions), "months go in file-name order"
        assert _transactions(three_months) == [ACCOUNT] * 9, "2 + 7 rows, none from April"
        assert _account_of(three_months, "2026.03 - Example Card.pdf") == ACCOUNT
        assert _account_of(three_months, "2026.05 - Example Card.pdf") == ACCOUNT
        assert _account_of(three_months, "2026.04 - Example Card.pdf") == UNASSIGNED, (
            "a refused statement must stay waiting, not look assigned with no rows"
        )
        assert "new 2" in page and "new 7" in page
        assert 'href="/statements"' in page

    def test_BulkAssign_ResultPage_ShowsCountsAndNoFigures(self, serve, db):
        with Store(db) as store:
            _keep(store, DISTINCTIVE, "2026.08 - Example Card.pdf")
        response = _post(
            serve(db),
            "/statements-assign",
            {"artefacts": self._ids(db, "2026.08 - Example Card.pdf"), "account": ACCOUNT},
        )

        assert "7,654.32" not in response.text and "ZEBRAQUARTZ" not in response.text

    def test_BulkAssign_WithAnIdThatIsNotAKeptStatement_IsRefusedAndAssignsNothing(
        self, serve, three_months
    ):
        with Store(three_months) as store:
            _land_other(store, 1, after=datetime.now(UTC))
            feed = store.connection.execute(
                "SELECT rowid FROM raw_artefacts WHERE source = 'starling'"
            ).fetchone()
        ids = self._ids(three_months, "2026.03 - Example Card.pdf") + f",{feed['rowid']}"

        response = _post(
            serve(three_months),
            "/statements-assign",
            {"artefacts": ids, "account": ACCOUNT},
        )

        assert response.status_code == 400
        assert f"No kept statement {feed['rowid']}" in response.text
        assert _transactions(three_months) == []
        assert _account_of(three_months, "2026.03 - Example Card.pdf") == UNASSIGNED

    def test_BulkAssign_WithNoAccount_IsRefusedAndAssignsNothing(self, serve, three_months):
        response = _post(
            serve(three_months),
            "/statements-assign",
            {"artefacts": self._ids(three_months, "2026.03 - Example Card.pdf")},
        )

        assert response.status_code == 400
        assert _transactions(three_months) == []

    def test_BulkAssign_WithAnInvalidAccountName_IsRefusedPerStatementAndAssignsNothing(
        self, serve, three_months
    ):
        response = _post(
            serve(three_months),
            "/statements-assign",
            {
                "artefacts": self._ids(three_months, "2026.03 - Example Card.pdf"),
                "account": "Not A Canonical Name!",
            },
        )

        assert "Not assigned" in response.text
        assert _transactions(three_months) == []
        assert _account_of(three_months, "2026.03 - Example Card.pdf") == UNASSIGNED

    def test_BulkAssign_WithNoIds_IsRefused(self, serve, three_months):
        response = _post(serve(three_months), "/statements-assign", {"account": ACCOUNT})

        assert response.status_code == 400

    def test_BulkAssign_FromAnotherSite_IsRefused(self, serve, three_months):
        base = serve(three_months)
        response = httpx.post(
            f"{base}/statements-assign",
            data={
                "artefacts": self._ids(three_months, "2026.03 - Example Card.pdf"),
                "account": ACCOUNT,
            },
            headers={"Origin": "https://elsewhere.example"},
            timeout=60,
        )

        assert response.status_code == 403
        assert _transactions(three_months) == []


class TestOnlyKeptStatementsAreServedAndAssigned:
    @pytest.fixture
    def mixed(self, db: Path) -> Path:
        with Store(db) as store:
            _keep(store, SANTANDER_AS_WRITTEN, "2026.05 - Example Card.pdf")
            _land_other(store, 1, after=datetime.now(UTC))
        return db

    def _feed_id(self, db: Path) -> int:
        with Store(db) as store:
            row = store.connection.execute(
                "SELECT rowid FROM raw_artefacts WHERE source = 'starling'"
            ).fetchone()
        return int(row["rowid"])

    def test_StatementShape_ForAnArtefactThatIsNotAStatement_Is404WithNoAssignForm(
        self, serve, mixed
    ):
        page = httpx.get(
            f"{serve(mixed)}/statement-shape?artefact={self._feed_id(mixed)}",
            timeout=60,
        )

        assert page.status_code == 404
        assert "No kept statement with that id" in page.text
        assert "/statement-assign" not in page.text

    def test_StatementShape_ForAKeptStatement_StillOffersTheAssignForm(self, serve, mixed):
        page = httpx.get(
            f"{serve(mixed)}/statement-shape"
            f"?artefact={_id_of(mixed, '2026.05 - Example Card.pdf')}",
            timeout=60,
        )

        assert page.status_code == 200
        assert 'action="/statement-assign"' in page.text

    def test_StatementShape_ForAPdfImportedToAnAccount_IsServedButOffersNoAssignForm(
        self, serve, db, tmp_path
    ):
        from obdi.ingest.pipeline import import_file

        path = tmp_path / "imported.pdf"
        path.write_bytes(SANTANDER_AS_WRITTEN)
        with Store(db) as store:
            import_file(store, path, account_id=ACCOUNT)
            row = store.connection.execute(
                "SELECT rowid FROM raw_artefacts WHERE media_type = 'application/pdf'"
            ).fetchone()
        base = serve(db)

        page = httpx.get(f"{base}/statement-shape?artefact={row['rowid']}", timeout=60)
        bulk = _post(
            base,
            "/statements-assign",
            {"artefacts": str(row["rowid"]), "account": "other-card"},
        )

        assert page.status_code == 200
        assert "/statement-assign" not in page.text
        assert bulk.status_code == 400
        assert _transactions(db) == [ACCOUNT] * 7, "nothing was refiled or re-read"

    def test_SingleAssign_OfAnArtefactThatIsNotAStatement_FilesNothing(self, serve, mixed):
        feed = self._feed_id(mixed)

        response = _post(
            serve(mixed), "/statement-assign", {"artefact": str(feed), "account": ACCOUNT}
        )

        assert f"No kept statement {feed}" in response.text
        with Store(mixed) as store:
            row = store.connection.execute(
                "SELECT account_ref FROM raw_artefacts WHERE rowid = ?", (feed,)
            ).fetchone()
        assert row["account_ref"] == "starling-current"

    def test_SingleAssign_OfAStatementTheGateRefuses_LeavesItWaitingForAnAccount(self, serve, db):
        with Store(db) as store:
            _keep(store, BROKEN, "2026.04 - Example Card.pdf")

        response = _post(
            serve(db),
            "/statement-assign",
            {"artefact": str(_id_of(db, "2026.04 - Example Card.pdf")), "account": ACCOUNT},
        )

        assert "unexplained" in response.text
        assert _account_of(db, "2026.04 - Example Card.pdf") == UNASSIGNED


class TestFindingTheListFromElsewhere:
    def test_StatementShapePage_LinksToTheListOfKeptStatements(self, serve, db):
        page = httpx.get(f"{serve(db)}/statement-shape", timeout=60).text

        assert '<a href="/statements">Every statement kept so far</a>' in page
