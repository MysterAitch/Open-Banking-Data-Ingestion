"""Marking an account archived, and what the pages say about one that looks it.

The provider's listing OMITS an archived Starling Space rather than marking it,
so obdi shows such an account as "quiet" for ever and the alert reports its feed
as silent until somebody declares it closed. These tests drive the toggle, the
suggestion drawn from the stored listings, the labels, and the check for final
movements, over real HTTP against a store built through the application's own
doors.

THE WORLD every scenario reads, with its answers fixed before the first run.
Parent account `starling:parent-uid`; three Spaces under it.

  Listings of the parent (the artefacts the provider's listing endpoint landed)
    2026-07-15  Bills, Quiet, Keep
    2026-09-28  Keep
  So Bills and Quiet are suggested, bounded by 2026-07-15 and 2026-09-28.

  Rows
    Bills  2026-05-10 and 2026-05-20          newest row 2026-05-20
    Quiet  2026-07-01                          newest row 2026-07-01
    Keep   2026-09-20, and a paired leg 2026-06-05
  Parent-account legs, all in `starling:parent-uid`
    05-15  internal, unpaired  before Bills' newest row         counts for neither
    05-25  internal, unpaired  after Bills' newest row          counts for Bills
    06-02  internal, unpaired  after Bills' newest row          counts for Bills
    06-03  NOT internal                                          counts for neither
    06-04  internal but VOID                                     counts for neither
    06-05  internal and PAIRED with a Keep leg                   counts for neither
  So the final-movement count is 2 for Bills and 0 for Quiet.

Nothing the suggestion computes or any page renders may write to the registry.
"""

from __future__ import annotations

import json
import threading
from dataclasses import fields, replace
from datetime import UTC, date, datetime
from urllib.parse import quote

import httpx
import pytest

from obdi.accounts import (
    ARCHIVE_BASIS_PREFIX,
    AccountRecord,
    AccountRef,
    LimitWindow,
)
from obdi.cli import build_web_config, collect_alert_findings
from obdi.ingest import pair_transfers_across_store
from obdi.ledger import Ledger
from obdi.masking import structural_field_names
from obdi.models import RawArtefact, TransactionStatus
from obdi.spaces import ArchiveNote
from obdi.store import Store
from obdi.web import AuthorisationSession, ConnectionHandler
from obdi.web_accounts import archive_controls
from test_account_pages import assert_tap_targets_are_thumb_sized
from test_alert_wiring import NOW, SILENT, _db, _keys, _schedule_truelayer, _three_cards
from test_ledger import land, txn

PARENT_UID = "parent-uid"
PARENT = f"starling:{PARENT_UID}"
BILLS = "starling:space-bills"
QUIET = "starling:space-quiet"
KEEP = "starling:space-keep"

#: Distinctive, so none can appear on a page by coincidence.
PRIVATE_DESCRIPTIONS = ("ZEBRA LATE TRANSFER", "ZEBRA EARLY TRANSFER", "QUOKKA REFERENCE")
PRIVATE_FIGURES = ("22.22", "2222", "33.33", "3333", "11.11", "1111", "44.44", "4444")

LISTING_ONE = date(2026, 7, 15)
LISTING_TWO = date(2026, 9, 28)


def _at(day: date) -> datetime:
    return datetime(day.year, day.month, day.day, 6, 0, tzinfo=UTC)


def _listing(day: date, *spaces: tuple[str, str]) -> RawArtefact:
    body = {"savingsGoals": [{"savingsGoalUid": uid, "name": name} for uid, name in spaces]}
    return RawArtefact(
        source="starling-spaces",
        account_ref=PARENT,
        fetched_at=_at(day),
        media_type="application/json",
        digest=f"listing-{day.isoformat()}-{len(spaces)}",
        payload=json.dumps(body).encode(),
        origin=f"listing-{day.isoformat()}",
    )


THREE = (("space-bills", "Bills"), ("space-quiet", "Quiet"), ("space-keep", "Keep"))
ONLY_KEEP = (("space-keep", "Keep"),)
NO_BILLS = (("space-quiet", "Quiet"), ("space-keep", "Keep"))


def _build_store(db, listings: list[tuple[date, tuple[tuple[str, str], ...]]]) -> None:
    with Store(db) as store:
        land(
            store,
            "digest-spaces",
            txn(BILLS, "starling", "b1", date(2026, 5, 10), 1500, "BILLS PAY IN"),
            txn(BILLS, "starling", "b2", date(2026, 5, 20), 2500, "BILLS PAY IN LATER"),
            txn(QUIET, "starling", "q1", date(2026, 7, 1), 700, "QUIET PAY IN"),
            txn(KEEP, "starling", "k1", date(2026, 9, 20), 900, "KEEP PAY IN"),
            txn(KEEP, "starling", "k2", date(2026, 6, 5), 4444, "KEEP LEG", internal=True),
        )
        land(
            store,
            "digest-parent",
            txn(
                PARENT, "starling", "p1", date(2026, 5, 15), -1111,
                PRIVATE_DESCRIPTIONS[1], internal=True,
            ),
            txn(
                PARENT, "starling", "p2", date(2026, 5, 25), -2222,
                PRIVATE_DESCRIPTIONS[0], internal=True,
            ),
            txn(
                PARENT, "starling", "p3", date(2026, 6, 2), -3333,
                PRIVATE_DESCRIPTIONS[2], internal=True,
            ),
            txn(PARENT, "starling", "p4", date(2026, 6, 3), -5555, "COFFEE", internal=False),
            txn(
                PARENT, "starling", "p5", date(2026, 6, 4), -6666, "VANISHED",
                internal=True, status=TransactionStatus.VOID,
            ),
            txn(PARENT, "starling", "p6", date(2026, 6, 5), -4444, "TO KEEP", internal=True),
        )
        pair_transfers_across_store(store)
        for day, spaces in listings:
            store.land_artefact(_listing(day, *spaces))


class Lab:
    def __init__(self, base: str, db) -> None:
        self.base = base
        self.db = db

    def get(self, path: str, **params: str) -> httpx.Response:
        return httpx.get(f"{self.base}{path}", params=params, timeout=30)

    def post(self, path: str, data: dict[str, str], **kwargs) -> httpx.Response:
        return httpx.post(f"{self.base}{path}", data=data, timeout=30, **kwargs)

    def declared(self) -> list[AccountRecord]:
        with Store(self.db) as store:
            return store.declared_accounts()

    def declared_ref(self, ref: str) -> AccountRecord | None:
        with Store(self.db) as store:
            return store.declared_account(AccountRef(ref))

    def declare(self, record: AccountRecord) -> AccountRecord:
        with Store(self.db) as store:
            return store.declare_account(record)

    def archive(self, ref: str, **fields: str) -> httpx.Response:
        return self.post("/archive-account", {"ref": ref, **fields})

    def unarchive(self, ref: str) -> httpx.Response:
        return self.post("/unarchive-account", {"ref": ref})

    def home_row(self, ref: str) -> str:
        """The holdings row for one account, so assertions cannot be satisfied
        by a neighbouring row."""
        # The Overview names every account too, with its own ledger link, so
        # the rows are read from the coverage page that holds only them.
        page = self.get("/coverage").text
        assert "Held so far" in page, "the coverage page has lost its holdings"
        marker = f"/ledger?ref={quote(ref, safe='')}"
        pieces = page.split('<div class="row"')
        rows = [piece for piece in pieces if marker in piece]
        assert rows, f"no holdings row for {ref}"
        # A row's own markup nests divs, so it ends where the next row begins
        # or where the page's fixed links do.
        return rows[0].split('<p><a class="button" href="/accounts">')[0]


@pytest.fixture
def make_lab(tmp_path, monkeypatch):
    servers = []

    def make(listings=None) -> Lab:
        db = tmp_path / "store.sqlite3"
        monkeypatch.setenv("OBDI_CONNECTION_STORE", str(tmp_path / "connections.json"))
        for variable in ("TRUELAYER_CLIENT_ID", "TRUELAYER_CLIENT_SECRET_FILE"):
            monkeypatch.delenv(variable, raising=False)
        _build_store(
            db,
            [(LISTING_ONE, THREE), (LISTING_TWO, ONLY_KEEP)] if listings is None else listings,
        )
        config = build_web_config(db)
        assert config is not None
        handler = type(
            "ArchiveHandler",
            (ConnectionHandler,),
            {"config": config, "session": AuthorisationSession()},
        )
        httpd = ConnectionHandler.make_server(("127.0.0.1", 0), handler)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        servers.append(httpd)
        return Lab(f"http://127.0.0.1:{httpd.server_port}", db)

    yield make
    for httpd in servers:
        httpd.shutdown()
        httpd.server_close()


@pytest.fixture
def lab(make_lab) -> Lab:
    return make_lab()


class TestTheBasisWordingHasOneOwner:
    def test_InferredBasis_BeginsWithThePrefixTheArchiveRulesRecognise(self):
        from obdi.spaces import SpaceListing

        space = SpaceListing(
            uid="u", parent_uid="p", first_listed=LISTING_ONE, last_listed=LISTING_ONE,
            listed_now=False, absent_since=LISTING_TWO, absent_responses=1,
            skipped_responses=0,
        )

        assert space.basis_text().startswith(ARCHIVE_BASIS_PREFIX)


class TestArchivingAnAccount:
    def test_Archive_WhenUndeclaredAndHoldingRows_DeclaresItWithTheNewestRowDate(self, lab):
        response = lab.archive(BILLS)

        assert response.status_code == 200
        record = lab.declared_ref(BILLS)
        assert record is not None
        assert record.closed == date(2026, 5, 20)
        assert record.date_basis == ""
        assert record.label == "Bills (starling space)"
        assert "newest row" in response.text
        assert "2026-05-20" in response.text

    def test_Archive_WhenDeclared_KeepsEveryOtherField(self, lab):
        original = lab.declare(
            AccountRecord(
                ref=AccountRef(BILLS),
                kind="savings",
                label="My Bills Pot",
                parent=AccountRef(PARENT),
                opened=date(2025, 1, 1),
                limits=(LimitWindow("overdraft", None, None, 5000),),
            )
        )

        lab.archive(BILLS)

        after = lab.declared_ref(BILLS)
        assert after == replace(original, closed=date(2026, 5, 20))

    def test_Archive_WithNoDate_DefaultsToTheNewestRowOfThatAccountOnly(self, lab):
        lab.archive(QUIET)

        record = lab.declared_ref(QUIET)
        assert record is not None and record.closed == date(2026, 7, 1)

    def test_Archive_WithAnExplicitDate_UsesItAndSaysItWasGiven(self, lab):
        response = lab.archive(BILLS, closed="2026-06-30")

        record = lab.declared_ref(BILLS)
        assert record is not None and record.closed == date(2026, 6, 30)
        assert "date was the one given" in response.text

    def test_Archive_WhenDeclaredWithNoRows_UsesTodayAndSaysSo(self, lab):
        lab.declare(AccountRecord(ref=AccountRef("passbook"), label="Passbook"))
        before = datetime.now(UTC).date()

        response = lab.archive("passbook")

        after = datetime.now(UTC).date()
        record = lab.declared_ref("passbook")
        assert record is not None and record.closed in {before, after}
        assert "holds no rows" in response.text and "today" in response.text

    def test_Archive_WithAMalformedDate_IsRefusedWithAPageThatSaysWhy(self, lab):
        response = lab.archive(BILLS, closed="31/06/2026")

        assert response.status_code == 400
        assert "YYYY-MM-DD" in response.text and "31/06/2026" in response.text
        assert lab.declared() == []

    def test_Archive_ForAnAccountNeitherDeclaredNorHoldingRows_IsRefusedNotCreated(self, lab):
        response = lab.archive("no-such-account")

        assert response.status_code == 404
        assert "no such account" in response.text.lower()
        assert lab.declared() == []

    def test_Archive_WithNoReference_IsRefused(self, lab):
        response = lab.post("/archive-account", {"ref": ""})

        assert response.status_code == 400
        assert lab.declared() == []

    def test_Archive_WithADateBeforeTheAccountOpened_IsRefusedAndChangesNothing(self, lab):
        original = lab.declare(
            AccountRecord(ref=AccountRef(BILLS), label="Bills", opened=date(2026, 1, 1))
        )

        response = lab.archive(BILLS, closed="2025-12-31")

        assert response.status_code == 400
        assert "falls before opened" in response.text
        assert lab.declared_ref(BILLS) == original

    def test_Archive_WithABasisButNoDate_IsRefused(self, lab):
        response = lab.archive(BILLS, date_basis="inferred: no longer listed after 2026-07-15")

        assert response.status_code == 400
        assert lab.declared() == []

    def test_Archive_WithAnOversizedBasis_IsRefused(self, lab):
        response = lab.archive(BILLS, closed="2026-06-30", date_basis="x" * 500)

        assert response.status_code == 400
        assert lab.declared() == []

    def test_Archive_WithAnInferredBasis_RecordsItBesideTheDate(self, lab):
        lab.archive(
            BILLS, closed="2026-07-15", date_basis=f"{ARCHIVE_BASIS_PREFIX}2026-07-15"
        )

        record = lab.declared_ref(BILLS)
        assert record is not None
        assert (record.closed, record.date_basis) == (
            date(2026, 7, 15), "inferred: no longer listed after 2026-07-15",
        )

    def test_Archive_ConfirmationPage_SaysWhatChangedAndLinksBackAndOffersUndo(self, lab):
        page = lab.archive(BILLS).text

        assert "is archived as of" in page
        assert "/unarchive-account" in page
        assert f'href="/ledger?ref={quote(BILLS, safe="")}"' in page
        assert 'href="/"' in page


class TestUnarchivingAnAccount:
    def test_Unarchive_AfterArchivingADeclaredAccount_RestoresItExactly(self, lab):
        original = lab.declare(
            AccountRecord(
                ref=AccountRef(BILLS), kind="savings", label="Bills",
                opened=date(2025, 1, 1),
            )
        )
        lab.archive(BILLS)
        assert lab.declared_ref(BILLS) != original

        response = lab.unarchive(BILLS)

        assert response.status_code == 200
        assert lab.declared_ref(BILLS) == original

    def test_Unarchive_AnArchivedAccountThatHadNoDeclaration_LeavesTheDeclaration(self, lab):
        lab.archive(BILLS)

        lab.unarchive(BILLS)

        record = lab.declared_ref(BILLS)
        assert record is not None and record.closed is None

    def test_Unarchive_ClearsAnInferredBasisThatOnlyDescribedTheClosing(self, lab):
        lab.archive(
            BILLS, closed="2026-07-15", date_basis=f"{ARCHIVE_BASIS_PREFIX}2026-07-15"
        )

        lab.unarchive(BILLS)

        record = lab.declared_ref(BILLS)
        assert record is not None and record.date_basis == ""

    def test_Unarchive_KeepsABasisThatMayDescribeTheOpeningDate(self, lab):
        note = "inferred from the first and last movement in the feed"
        lab.declare(
            AccountRecord(
                ref=AccountRef(BILLS), opened=date(2026, 5, 10),
                closed=date(2026, 5, 20), date_basis=note,
            )
        )

        lab.unarchive(BILLS)

        record = lab.declared_ref(BILLS)
        assert record is not None
        assert (record.closed, record.opened, record.date_basis) == (
            None, date(2026, 5, 10), note,
        )

    def test_Unarchive_WhenNothingIsDeclared_IsRefusedAndCreatesNothing(self, lab):
        response = lab.unarchive(BILLS)

        assert response.status_code == 404
        assert lab.declared() == []

    def test_Unarchive_WhenTheAccountWasNotArchived_SaysNothingChanged(self, lab):
        original = lab.declare(AccountRecord(ref=AccountRef(BILLS), label="Bills"))

        response = lab.unarchive(BILLS)

        assert response.status_code == 200
        assert "nothing changed" in response.text.lower()
        assert lab.declared_ref(BILLS) == original


class TestTheRoutesRefuseOtherSites:
    @pytest.mark.parametrize("route", ["/archive-account", "/unarchive-account"])
    def test_Post_FromAnotherSite_IsRefusedAndWritesNothing(self, lab, route):
        lab.declare(AccountRecord(ref=AccountRef(BILLS), label="Bills", closed=date(2026, 5, 20)))
        before = lab.declared()

        response = lab.post(
            route, {"ref": BILLS}, headers={"Origin": "https://evil.example"}
        )

        assert response.status_code == 403
        assert lab.declared() == before

    @pytest.mark.parametrize("route", ["/archive-account", "/unarchive-account"])
    def test_Post_FromOurOwnOrigin_IsAccepted(self, lab, route):
        lab.declare(AccountRecord(ref=AccountRef(BILLS), label="Bills", closed=date(2026, 5, 20)))

        response = lab.post(route, {"ref": BILLS}, headers={"Origin": lab.base})

        assert response.status_code == 200


class TestTapTargets:
    def test_ArchiveControls_ForEveryState_UseTheThumbSizedHelper(self):
        notes = [
            None,
            ArchiveNote(
                ref=BILLS, state="archived", closed="2026-05-20", inferred=False,
                suggested_closed="", suggested_basis="", listing_note="", space=True,
                parent=PARENT, final_movements=2, final_movements_unavailable="",
            ),
            ArchiveNote(
                ref=BILLS, state="suggested", closed="", inferred=False,
                suggested_closed="2026-07-15",
                suggested_basis="inferred: no longer listed after 2026-07-15",
                listing_note="note", space=True, parent=PARENT, final_movements=0,
                final_movements_unavailable="",
            ),
        ]
        for note in notes:
            markup = archive_controls(BILLS, note, with_date=True)
            assert_tap_targets_are_thumb_sized(markup)

    def test_Pages_TheConfirmationAndRefusalAndLedger_UseTheThumbSizedHelper(self, lab):
        assert_tap_targets_are_thumb_sized(lab.archive(BILLS).text)
        assert_tap_targets_are_thumb_sized(lab.unarchive(BILLS).text)
        assert_tap_targets_are_thumb_sized(lab.archive("no-such-account").text)
        assert_tap_targets_are_thumb_sized(lab.get("/ledger", ref=BILLS).text)


class TestTheLabelWhereTheAccountIsNamed:
    def test_Home_WhenArchivedWithAStatedDate_SaysArchivedAndStatedNotQuiet(self, lab):
        lab.archive(BILLS, closed="2026-06-30")

        row = lab.home_row(BILLS)

        assert "archived 2026-06-30 (stated)" in row
        assert "quiet since" not in row
        assert "/unarchive-account" in row and "/archive-account" not in row

    def test_Home_WhenArchivedWithAnInferredDate_SaysInferred(self, lab):
        lab.archive(BILLS, closed="2026-07-15", date_basis=f"{ARCHIVE_BASIS_PREFIX}2026-07-15")

        row = lab.home_row(BILLS)

        assert "archived 2026-07-15 (inferred)" in row

    def test_Home_WhenOpenAndNotSuggested_ShowsNoLabelAndOffersArchive(self, lab):
        row = lab.home_row(KEEP)

        assert "archived" not in row.replace("Archive this account", "")
        assert "inferred" not in row
        assert "Archive this account" in row and "/unarchive-account" not in row

    def test_Home_WhenTheDeclaredClosingDateIsInTheFuture_ShowsNoArchivedLabel(self, lab):
        lab.declare(AccountRecord(ref=AccountRef(KEEP), label="Keep", closed=date(2099, 1, 1)))

        row = lab.home_row(KEEP)

        assert "archived 2099" not in row
        assert "Archive this account" in row

    def test_Ledger_WhenArchivedWithAStatedDate_HeaderSaysArchivedAndStated(self, lab):
        lab.archive(BILLS, closed="2026-06-30")

        page = lab.get("/ledger", ref=BILLS, month="2026-05").text

        assert "archived 2026-06-30 (stated)" in page
        assert "/unarchive-account" in page and 'action="/archive-account"' not in page

    def test_Ledger_WhenArchivedWithAnInferredDate_HeaderSaysInferred(self, lab):
        lab.archive(BILLS, closed="2026-07-15", date_basis=f"{ARCHIVE_BASIS_PREFIX}2026-07-15")

        page = lab.get("/ledger", ref=BILLS, month="2026-05").text

        assert "archived 2026-07-15 (inferred)" in page

    def test_Ledger_WhenOpen_HeaderCarriesNoLabelAndOffersArchiveWithADateField(self, lab):
        page = lab.get("/ledger", ref=KEEP, month="2026-09").text

        assert "pill-quiet\">archived" not in page
        assert 'action="/archive-account"' in page
        assert 'name="closed"' in page

    def test_Ledger_ForAnUnknownAccount_OffersNoArchive(self, lab):
        response = lab.get("/ledger", ref="no-such-account")

        assert response.status_code == 404
        assert "/archive-account" not in response.text

    def test_Ledger_MaskedAndUnmasked_BothCarryTheLabelAndNeitherLeaksAValue(self, lab):
        lab.archive(BILLS, closed="2026-06-30")

        masked = lab.get("/ledger", ref=BILLS, month="2026-05").text
        unmasked = lab.post("/ledger", {"ref": BILLS, "month": "2026-05"}).text

        assert "archived 2026-06-30 (stated)" in masked
        assert "archived 2026-06-30 (stated)" in unmasked
        for secret in ("BILLS PAY IN", "25.00", "2500", "15.00"):
            assert secret not in masked, secret


class TestDecidingWhichFieldsAreValues:
    def test_ArchiveNote_EveryFieldIsStructuralSoItReadsTheSameOnAMaskedPage(self):
        values = {f.name for f in fields(ArchiveNote)} - structural_field_names(ArchiveNote)

        assert values == set()

    def test_Ledger_TheArchiveFieldIsStructuralAndNoOtherFieldBecameAValue(self):
        values = {f.name for f in fields(Ledger)} - structural_field_names(Ledger)

        assert values == set()


class TestTheSuggestionFromTheListings:
    def test_Home_WhenASpaceIsAbsentFromTheNewestListing_SuggestsItBesideTheAccount(self, lab):
        row = lab.home_row(BILLS)

        assert "no longer lists this Space" in row
        assert "Nothing has been changed" in row
        assert 'name="closed" value="2026-07-15"' in row
        assert f'value="{ARCHIVE_BASIS_PREFIX}2026-07-15"' in row
        assert "2026-09-28" in row

    def test_Home_WhenASpaceIsListedInEveryResponse_SuggestsNothing(self, lab):
        row = lab.home_row(KEEP)

        assert "no longer lists" not in row
        assert "Archive as inferred" not in row

    def test_Home_WhenASpaceIsMissingFromAMiddleListingOnly_SuggestsNothing(self, make_lab):
        lab = make_lab(
            [
                (date(2026, 7, 1), THREE),
                (date(2026, 8, 1), NO_BILLS),
                (date(2026, 9, 1), THREE),
            ]
        )

        row = lab.home_row(BILLS)

        assert "no longer lists" not in row
        assert "Archive as inferred" not in row

    def test_Home_WhenNoListingsAreHeld_SuggestsNothingForAnySpace(self, make_lab):
        lab = make_lab([])

        for ref in (BILLS, QUIET, KEEP):
            assert "Archive as inferred" not in lab.home_row(ref)

    def test_Home_WhenTheNewestListingIsUnreadable_SuggestsNothing(self, make_lab):
        lab = make_lab([(LISTING_ONE, THREE)])
        with Store(lab.db) as store:
            store.land_artefact(
                RawArtefact(
                    source="starling-spaces",
                    account_ref=PARENT,
                    fetched_at=_at(LISTING_TWO),
                    media_type="text/html",
                    digest="gateway-timeout",
                    payload=b"<html>504</html>",
                    origin="timeout",
                )
            )

        assert "Archive as inferred" not in lab.home_row(BILLS)

    def test_Suggestion_PressedAsRendered_ArchivesWithTheInferenceRecorded(self, lab):
        lab.archive(
            BILLS, closed="2026-07-15", date_basis="inferred: no longer listed after 2026-07-15"
        )

        record = lab.declared_ref(BILLS)
        assert record is not None
        assert record.closed == date(2026, 7, 15)
        assert record.date_basis == "inferred: no longer listed after 2026-07-15"
        assert "archived 2026-07-15 (inferred)" in lab.home_row(BILLS)
        assert "Archive as inferred" not in lab.home_row(BILLS)

    def test_Suggestion_ComputedAndRenderedOnEveryPage_WritesNothingToTheRegistry(self, lab):
        def counts() -> tuple[int, int, int]:
            with Store(lab.db) as store:
                one = store.connection.execute("SELECT COUNT(*) FROM declared_accounts")
                two = store.connection.execute("SELECT COUNT(*) FROM transactions")
                three = store.connection.execute("SELECT COUNT(*) FROM raw_artefacts")
                return (one.fetchone()[0], two.fetchone()[0], three.fetchone()[0])

        before = lab.declared()
        counted = counts()

        for _ in range(2):
            lab.get("/")
            lab.get("/coverage")
            lab.get("/ledger", ref=BILLS, month="2026-05")
            lab.get("/ledger", ref=QUIET)

        assert lab.declared() == before == []
        assert counts() == counted

    def test_Ledger_ForASuggestedSpace_CarriesTheSuggestionToo(self, lab):
        page = lab.get("/ledger", ref=BILLS, month="2026-05").text

        assert "Archive as inferred (2026-07-15)" in page


class TestFinalMovementsArePresentOnlyAsACount:
    def test_Home_ForASuggestedSpaceWithLateParentLegs_CountsExactlyThoseLegs(self, lab):
        row = lab.home_row(BILLS)

        assert f"Final movements not held in {PARENT}: <strong>2</strong>" in row

    def test_Home_ForASuggestedSpaceWithNoLaterParentLegs_CountsZero(self, lab):
        row = lab.home_row(QUIET)

        assert "<strong>0</strong>" in row

    def test_Home_TheCount_IsAccompaniedByWhatANonZeroCountMeans(self, lab):
        row = lab.home_row(BILLS)

        assert "A non-zero count means" in row

    def test_Home_ForAnArchivedSpace_ShowsTheCountToo(self, lab):
        lab.archive(BILLS)

        assert "<strong>2</strong>" in lab.home_row(BILLS)

    def test_Home_ForAnOpenSpaceStillListed_ShowsNoCount(self, lab):
        assert "Final movements" not in lab.home_row(KEEP)

    def test_Pages_CarryNoAmountOrDescriptionOfTheCountedLegs(self, lab):
        pages = [
            lab.get("/").text,
            lab.get("/coverage").text,
            lab.get("/ledger", ref=BILLS, month="2026-05").text,
            lab.get("/ledger", ref=QUIET).text,
            lab.archive(BILLS).text,
        ]

        for page in pages:
            for secret in (*PRIVATE_DESCRIPTIONS, *PRIVATE_FIGURES):
                assert secret not in page, secret

    def test_Home_ForAnArchivedSpaceWithNoKnownParent_SaysItCouldNotBeCounted(self, lab):
        # A Space that no listing names and that holds a row: nothing says
        # which account it sits under.
        with Store(lab.db) as store:
            land(
                store, "digest-orphan",
                txn("starling:orphan", "starling", "o1", date(2026, 4, 1), 100, "ORPHAN"),
            )
        lab.declare(
            AccountRecord(
                ref=AccountRef("starling:orphan"), label="Orphan", kind="starling-space",
                closed=date(2026, 4, 1),
            )
        )

        row = lab.home_row("starling:orphan")

        assert "Final movements: not counted - no parent account is known" in row

    def test_Home_ForAnArchivedPlainAccount_ShowsNoFinalMovementCount(self, lab):
        lab.declare(
            AccountRecord(ref=AccountRef(PARENT), label="Main", closed=date(2026, 6, 5))
        )

        assert "Final movements" not in lab.home_row(PARENT)


class TestTheSilentFeedAlertFollowsTheToggle:
    def test_Alert_WhenTheSilentAccountIsArchivedThroughTheToggle_ItIsNotAFaultAndReturnsWhenUndone(
        self, tmp_path, monkeypatch, land_transaction
    ):
        _schedule_truelayer(tmp_path, monkeypatch)
        with Store(_db(tmp_path)) as store:
            _three_cards(store, land_transaction)
        config = build_web_config(_db(tmp_path))
        assert config is not None
        assert config.archive_account is not None and config.unarchive_account is not None
        silent = "silent-feed:truelayer:card-3:truelayer"
        assert silent in _keys(collect_alert_findings(_db(tmp_path), now=NOW), SILENT)

        config.archive_account("truelayer:card-3", None, "")

        assert silent not in _keys(collect_alert_findings(_db(tmp_path), now=NOW), SILENT)
        # Another silent account is untouched: archiving one does not blind the check.
        assert "silent-feed:truelayer:card-2:truelayer" in _keys(
            collect_alert_findings(_db(tmp_path), now=NOW), SILENT
        )

        config.unarchive_account("truelayer:card-3")

        assert silent in _keys(collect_alert_findings(_db(tmp_path), now=NOW), SILENT)
