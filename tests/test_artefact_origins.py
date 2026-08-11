"""One document, two names - and a store that kept it twice.

A browser uploading a FOLDER sends every file's relative path as its
name, while the file picker beside it sends the bare name. Both are the
same document, and artefacts were keyed by that name, so the same bytes
landed twice under two keys. The live store was found holding 62 artefact
rows for 31 documents - every one duplicated exactly once - while
land_artefact's own docstring promised it was idempotent on the content
digest.

A name is not identity. It is evidence ABOUT an artefact, and worth
keeping in full: "6_2026" dates a statement, a parent folder often says
which account a file called "statement.pdf" belongs to, and a fetch URL
records the window actually asked for. So bytes already held still record
the name they arrive under, and the migration that collapses the
duplicates carries every one of their names across.
"""

from __future__ import annotations

import pathlib
import sqlite3
from datetime import UTC, datetime

import pytest

from obdi.models import RawArtefact
from obdi.store import Store

SCHEMA_HISTORY = pathlib.Path(__file__).resolve().parent / "schema_history"

#: The shape the live store was found in - the one with the origin still
#: in the primary key, which is what let the duplicates in.
DEFECTIVE_SHAPE = SCHEMA_HISTORY / "17-artefact-record-count.sql"

STATEMENT = b"%PDF-1.4 not a real statement, just bytes that stay identical"
DIGEST = "0f1e2d3c4b5a69788796a5b4c3d2e1f0"
ACCOUNT = "santander-credit"
SOURCE = "statement"

#: The two names one document arrives under: the file picker's, and the
#: folder upload's.
BARE_NAME = "2026.07.11 - Santander CC.pdf"
FOLDER_NAME = "Bank statements/Santander/2026.07.11 - Santander CC.pdf"

FIRST_UPLOAD = datetime(2026, 7, 11, 9, 0, tzinfo=UTC)
SECOND_UPLOAD = datetime(2026, 7, 12, 18, 30, tzinfo=UTC)


def _statement(
    origin: str,
    *,
    account_ref: str = ACCOUNT,
    fetched_at: datetime = FIRST_UPLOAD,
    digest: str = DIGEST,
    payload: bytes = STATEMENT,
) -> RawArtefact:
    return RawArtefact(
        source=SOURCE,
        account_ref=account_ref,
        fetched_at=fetched_at,
        media_type="application/pdf",
        digest=digest,
        payload=payload,
        origin=origin,
    )


@pytest.fixture
def store(tmp_path):
    with Store(tmp_path / "store.sqlite3") as opened:
        yield opened


def _artefact_rows(store: Store) -> list[sqlite3.Row]:
    return store.connection.execute(
        "SELECT digest, account_ref, origin, fetched_at, payload FROM raw_artefacts "
        "ORDER BY account_ref, fetched_at"
    ).fetchall()


class TestTheSameDocumentUnderTwoNames:
    """The upload that made 62 rows out of 31 documents."""

    def test_Statement_WhenUploadedByFilePickerThenByFolder_IsHeldOnce(self, store):
        store.land_artefact(_statement(BARE_NAME))
        store.land_artefact(_statement(FOLDER_NAME, fetched_at=SECOND_UPLOAD))

        assert len(_artefact_rows(store)) == 1

    def test_Statement_WhenUploadedByFilePickerThenByFolder_ReportsBothNames(
        self, store
    ):
        """The names are the reason the column was ever in the key. They
        survive the deduplication rather than being the price of it."""
        store.land_artefact(_statement(BARE_NAME))
        store.land_artefact(_statement(FOLDER_NAME, fetched_at=SECOND_UPLOAD))

        assert store.origins_for_artefact(DIGEST, ACCOUNT, SOURCE) == [BARE_NAME, FOLDER_NAME]

    def test_Statement_WhenAlreadyHeldButTheNameIsNew_RecordsTheNameNotThePayload(
        self, store
    ):
        """"Already held" and "nothing recorded" stopped being the same
        statement the moment names were kept separately, so the landing
        answers both questions rather than one."""
        store.land_artefact(_statement(BARE_NAME))

        landing = store.land_artefact(_statement(FOLDER_NAME, fetched_at=SECOND_UPLOAD))

        assert landing.payload_stored is False
        assert landing.origin_recorded is True

    def test_Statement_WhenTheSameNameArrivesAgain_RecordsNothingAtAll(self, store):
        """A plain re-import: the same bytes under the same name, which is
        what an overlapping export or a repeated pull produces."""
        store.land_artefact(_statement(BARE_NAME))

        landing = store.land_artefact(_statement(BARE_NAME, fetched_at=SECOND_UPLOAD))

        assert landing.payload_stored is False
        assert landing.origin_recorded is False

    def test_Statement_WhenFirstSeen_ReportsBothThePayloadAndTheNameAsNew(self, store):
        landing = store.land_artefact(_statement(BARE_NAME))

        assert landing.payload_stored is True
        assert landing.origin_recorded is True

    def test_Statement_WhenHeldUnderTwoNames_KeepsTheOneItFirstArrivedUnder(
        self, store
    ):
        """The artefact row carries a name so every reader has one to show;
        the complete set is what the origins answer."""
        store.land_artefact(_statement(BARE_NAME))
        store.land_artefact(_statement(FOLDER_NAME, fetched_at=SECOND_UPLOAD))

        assert _artefact_rows(store)[0]["origin"] == BARE_NAME

    def test_Statement_WhenLandedWithNoNameAtAll_RecordsNoOrigin(self, store):
        """An empty name is not a name. Recording it would put a row in the
        set that says nothing about where the bytes came from."""
        store.land_artefact(_statement(""))

        assert store.origins_for_artefact(DIGEST, ACCOUNT, SOURCE) == []


class TestTheSameExportAgainstTwoAccounts:
    """The case the account stays in the key for."""

    def test_Export_WhenLandedAgainstTwoAccounts_IsTwoArtefacts(self, store):
        """One export legitimately covering two accounts is two pieces of
        evidence; the same bytes under two NAMES is one."""
        store.land_artefact(_statement(BARE_NAME, account_ref="santander-credit"))
        store.land_artefact(_statement(BARE_NAME, account_ref="santander-current"))

        assert len(_artefact_rows(store)) == 2

    def test_Export_WhenLandedAgainstTwoAccounts_KeepsEachAccountsNamesApart(
        self, store
    ):
        store.land_artefact(_statement(BARE_NAME, account_ref="santander-credit"))
        store.land_artefact(_statement(FOLDER_NAME, account_ref="santander-current"))

        assert store.origins_for_artefact(DIGEST, "santander-credit", SOURCE) == [
            BARE_NAME
        ]
        assert store.origins_for_artefact(DIGEST, "santander-current", SOURCE) == [
            FOLDER_NAME
        ]


class TestAskedAndEmptyStaysAnswerable:
    """Every empty API body is byte-identical, so the asks that produced
    them were told apart only by the origin in the key. They still must
    be: the extend button walks back from the earliest window ALREADY
    asked, and an account that holds nothing has nothing else to walk
    from.
    """

    EMPTY = b'{"results": [], "status": "Succeeded"}'
    HOST = "https://api.truelayer.com/data/v1/accounts/e9f8/transactions"

    def _asked(self, window: str, fetched_at: datetime) -> RawArtefact:
        return RawArtefact(
            source="truelayer-booked",
            account_ref="halifax-current",
            fetched_at=fetched_at,
            media_type="application/json",
            digest="empty-body-digest",
            payload=self.EMPTY,
            origin=f"{self.HOST}?{window}",
        )

    def test_EmptyWindows_WhenTwoDifferentRangesReturnNothing_BothAsksAreKept(
        self, store
    ):
        store.land_artefact(
            self._asked("from=2024-08-01&to=2024-10-30", FIRST_UPLOAD)
        )
        store.land_artefact(
            self._asked("from=2022-08-01&to=2024-08-02", SECOND_UPLOAD)
        )

        origins = store.origins_for_artefact(
            "empty-body-digest", "halifax-current", "truelayer-booked"
        )
        assert len(origins) == 2
        assert any("from=2022-08-01" in origin for origin in origins)

    def test_ExtendAnchor_WhenEveryWindowCameBackEmpty_StillReachesTheEarliestAsk(
        self, store
    ):
        """The live symptom of losing this: an empty account whose "+730
        days" button re-asked the same span forever, because nothing said
        how far back the asking had already been."""
        from datetime import date

        from obdi.cli import _earliest_asked

        store.land_artefact(
            self._asked("from=2024-08-01&to=2024-10-30", FIRST_UPLOAD)
        )
        store.land_artefact(
            self._asked("from=2022-08-01&to=2024-08-02", SECOND_UPLOAD)
        )

        assert _earliest_asked(store, "halifax-current") == date(2022, 8, 1)

    def test_CoverageEdge_WhenARollingFeedRelandsIdenticalBytes_MovesForward(
        self, store
    ):
        """A rolling feed that returns the same bytes on two days is one
        artefact with two asks. The forward edge is the LATER ask, not the
        artefact's own first fetch - otherwise a scheduler that quietly
        stopped and one that keeps returning nothing look identical."""
        from datetime import date

        from obdi.cli import _latest_asked

        store.land_artefact(
            self._asked("from=2026-04-01&to=2026-07-11", FIRST_UPLOAD)
        )
        store.land_artefact(
            self._asked("from=2026-04-01&to=2026-07-12", SECOND_UPLOAD)
        )

        covered, landed = _latest_asked(store, "halifax-current")
        assert covered == date(2026, 7, 12)
        assert landed.startswith("2026-07-12")


class TestAssigningAKeptStatement:
    """A statement is landed before anyone decides whose it is, then
    assigned - which is a refile. The names it arrived under are the best
    clue to the account it belongs to, so they must survive the move that
    the clue was used to make.
    """

    def test_KeptStatement_WhenAssignedToItsAccount_KeepsEveryNameItArrivedUnder(
        self, store
    ):
        from obdi.namespaces import UNASSIGNED_ACCOUNT

        store.land_artefact(_statement(BARE_NAME, account_ref=UNASSIGNED_ACCOUNT))
        store.land_artefact(
            _statement(
                FOLDER_NAME,
                account_ref=UNASSIGNED_ACCOUNT,
                fetched_at=SECOND_UPLOAD,
            )
        )
        artefact_id = store.connection.execute(
            "SELECT rowid FROM raw_artefacts"
        ).fetchone()[0]

        store.refile_artefact(artefact_id, ACCOUNT)

        assert store.origins_for_artefact(DIGEST, ACCOUNT, SOURCE) == [
            BARE_NAME,
            FOLDER_NAME,
        ]
        assert store.origins_for_artefact(DIGEST, UNASSIGNED_ACCOUNT, SOURCE) == []

    def test_KeptStatement_WhenAssignedWhereTheBytesAlreadyAre_MergesTheNames(
        self, store
    ):
        """The recovery-by-reimport case: the same document is already
        filed under the destination, so the misfiled copy is absorbed -
        and the name it was misfiled under is part of what it brings."""
        from obdi.namespaces import UNASSIGNED_ACCOUNT

        store.land_artefact(_statement(BARE_NAME, account_ref=ACCOUNT))
        store.land_artefact(
            _statement(
                FOLDER_NAME,
                account_ref=UNASSIGNED_ACCOUNT,
                fetched_at=SECOND_UPLOAD,
            )
        )
        misfiled = store.connection.execute(
            "SELECT rowid FROM raw_artefacts WHERE account_ref = ?",
            (UNASSIGNED_ACCOUNT,),
        ).fetchone()[0]

        store.refile_artefact(misfiled, ACCOUNT)

        assert len(_artefact_rows(store)) == 1
        assert store.origins_for_artefact(DIGEST, ACCOUNT, SOURCE) == [
            BARE_NAME,
            FOLDER_NAME,
        ]


class TestTwoEndpointsAnsweringIdentically:
    """An empty answer means different things down different pipes.

    TrueLayer's pending endpoint returns the COMPLETE pending set, so an
    empty one says every held pending has vanished and voids them. An
    empty booked window says a date range held nothing. The two bodies are
    byte-identical, and an account with neither has both - so the pipe
    that delivered a payload stays part of the artefact's identity, or a
    rebuild replays the voiding evidence as a windowful of nothing.
    """

    EMPTY = b'{"results": [], "status": "Succeeded"}'

    def _answer(self, source: str) -> RawArtefact:
        return RawArtefact(
            source=source,
            account_ref="halifax-current",
            fetched_at=FIRST_UPLOAD,
            media_type="application/json",
            digest="empty-body-digest",
            payload=self.EMPTY,
            origin=f"https://api.truelayer.com/data/v1/accounts/e9f8/{source}",
        )

    def test_EmptyAnswers_WhenBookedAndPendingBothReturnNothing_AreKeptApart(
        self, store
    ):
        store.land_artefact(self._answer("truelayer-booked"))
        store.land_artefact(self._answer("truelayer-pending"))

        assert [row["source"] for row in store.connection.execute(
            "SELECT source FROM raw_artefacts ORDER BY source"
        )] == ["truelayer-booked", "truelayer-pending"]

    def test_EmptyAnswers_WhenTheSamePipeAnswersTwice_AreOneArtefact(self, store):
        store.land_artefact(self._answer("truelayer-pending"))

        landing = store.land_artefact(self._answer("truelayer-pending"))

        assert landing.payload_stored is False


def _store_with_the_duplicate(path: pathlib.Path) -> pathlib.Path:
    """A store carrying exactly the defect, in the shape it shipped in.

    The folder upload is inserted FIRST and fetched LAST, so a collapse
    that keeps whichever row it meets first is not mistaken for one that
    keeps the earliest fetch.
    """
    legacy = sqlite3.connect(path)
    legacy.executescript(DEFECTIVE_SHAPE.read_text(encoding="utf-8"))
    for origin, fetched_at in (
        (FOLDER_NAME, SECOND_UPLOAD),
        (BARE_NAME, FIRST_UPLOAD),
    ):
        legacy.execute(
            "INSERT INTO raw_artefacts (digest, source, account_ref, media_type, "
            "origin, fetched_at, payload, request_meta, connection_id) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (
                DIGEST,
                "statement",
                ACCOUNT,
                "application/pdf",
                origin,
                fetched_at.isoformat(),
                STATEMENT,
                "",
                "",
            ),
        )
    legacy.commit()
    legacy.close()
    return path


def _reopen_as_an_upgrade(path: pathlib.Path) -> None:
    """Put the store back on an older schema version, which is what every
    later release does to it: the whole migration ladder runs again."""
    connection = sqlite3.connect(path)
    connection.execute("UPDATE obdi_meta SET value = '0' WHERE key = 'schema_version'")
    connection.commit()
    connection.close()


class TestAStoreThatAlreadyHasTheDuplicates:
    """A migration is only proven on a store that HAS the problem.

    Not on a fresh one, where the collapse has nothing to collapse: the
    previous artefact migration carried its own stale copy of the table
    definition and would have failed on the very store it existed to
    rescue, which is the review finding this class answers.
    """

    def test_Upgrade_WhenOneDocumentWasHeldTwice_LeavesOneArtefact(self, tmp_path):
        path = _store_with_the_duplicate(tmp_path / "live.sqlite3")

        with Store(path) as store:
            rows = _artefact_rows(store)

        assert len(rows) == 1

    def test_Upgrade_WhenOneDocumentWasHeldTwice_KeepsBothNames(self, tmp_path):
        path = _store_with_the_duplicate(tmp_path / "live.sqlite3")

        with Store(path) as store:
            origins = store.origins_for_artefact(DIGEST, ACCOUNT, SOURCE)

        assert origins == [BARE_NAME, FOLDER_NAME]

    def test_Upgrade_WhenOneDocumentWasHeldTwice_KeepsTheEarliestFetch(self, tmp_path):
        """When the bytes first arrived is a fact about the evidence; the
        second landing only re-observed what was already held."""
        path = _store_with_the_duplicate(tmp_path / "live.sqlite3")

        with Store(path) as store:
            row = _artefact_rows(store)[0]

        assert row["fetched_at"] == FIRST_UPLOAD.isoformat()
        assert row["origin"] == BARE_NAME

    def test_Upgrade_WhenOneDocumentWasHeldTwice_LeavesThePayloadUntouched(
        self, tmp_path
    ):
        path = _store_with_the_duplicate(tmp_path / "live.sqlite3")

        with Store(path) as store:
            row = _artefact_rows(store)[0]

        assert bytes(row["payload"]) == STATEMENT

    def test_Upgrade_WhenTheLadderRunsAgainOnALaterRelease_ChangesNothingFurther(
        self, tmp_path
    ):
        """Every schema bump re-runs every migration, so "runs twice"
        is the normal case rather than the exotic one."""
        path = _store_with_the_duplicate(tmp_path / "live.sqlite3")
        with Store(path):
            pass

        _reopen_as_an_upgrade(path)

        with Store(path) as store:
            rows = _artefact_rows(store)
            origins = store.origins_for_artefact(DIGEST, ACCOUNT, SOURCE)

        assert len(rows) == 1
        assert rows[0]["fetched_at"] == FIRST_UPLOAD.isoformat()
        assert bytes(rows[0]["payload"]) == STATEMENT
        assert origins == [BARE_NAME, FOLDER_NAME]

    def test_Upgrade_WhenTheStoreHasNoDuplicates_KeepsEveryArtefact(self, tmp_path):
        """The overwhelming majority of rows are not duplicates, and a
        collapse that took any of them would be far worse than the fault
        it fixes."""
        path = tmp_path / "clean.sqlite3"
        legacy = sqlite3.connect(path)
        legacy.executescript(DEFECTIVE_SHAPE.read_text(encoding="utf-8"))
        legacy.executemany(
            "INSERT INTO raw_artefacts (digest, source, account_ref, media_type, "
            "origin, fetched_at, payload, request_meta, connection_id) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            [
                (
                    f"digest-{index}",
                    "statement",
                    ACCOUNT,
                    "application/pdf",
                    f"statement-{index}.pdf",
                    FIRST_UPLOAD.isoformat(),
                    STATEMENT,
                    "",
                    "",
                )
                for index in range(4)
            ],
        )
        legacy.commit()
        legacy.close()

        with Store(path) as store:
            rows = _artefact_rows(store)

        assert len(rows) == 4

    def test_Upgrade_WhenTheSameBytesWereFiledAgainstTwoAccounts_KeepsBoth(
        self, tmp_path
    ):
        """The account stays in the key, so these are not duplicates of
        each other and nothing may collapse them."""
        path = tmp_path / "two-accounts.sqlite3"
        legacy = sqlite3.connect(path)
        legacy.executescript(DEFECTIVE_SHAPE.read_text(encoding="utf-8"))
        legacy.executemany(
            "INSERT INTO raw_artefacts (digest, source, account_ref, media_type, "
            "origin, fetched_at, payload, request_meta, connection_id) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            [
                (
                    DIGEST,
                    "statement",
                    account,
                    "application/pdf",
                    BARE_NAME,
                    FIRST_UPLOAD.isoformat(),
                    STATEMENT,
                    "",
                    "",
                )
                for account in ("santander-credit", "santander-current")
            ],
        )
        legacy.commit()
        legacy.close()

        with Store(path) as store:
            rows = _artefact_rows(store)

        assert [row["account_ref"] for row in rows] == [
            "santander-credit",
            "santander-current",
        ]


class TestTheAskLedgerStillSeesEverySiblingName:
    """The timeline compares a recorded ask against every origin the
    artefact landed under; an arbitrary single one made it cry wolf 58
    times. Sibling origins now live in their own table, and the ledger
    must read them from there rather than from vanished duplicate rows.
    """

    def test_AttemptRow_WhenItsPayloadLandedUnderSeveralAsks_ListsThemAll(self, store):
        host = "https://api.truelayer.com/data/v1/accounts/e9f8/transactions"
        for window, fetched_at in (
            ("from=2026-05-07&to=2026-08-05", FIRST_UPLOAD),
            ("from=2026-05-08&to=2026-08-06", SECOND_UPLOAD),
        ):
            store.land_artefact(
                RawArtefact(
                    source="truelayer-booked",
                    account_ref="halifax-current",
                    fetched_at=fetched_at,
                    media_type="application/json",
                    digest="rolling-digest",
                    payload=b'{"results": []}',
                    origin=f"{host}?{window}",
                )
            )
        store.record_attempt(
            source="truelayer-booked",
            connection_id="halifax",
            account_ref="halifax-current",
            asked="from=2026-05-08&to=2026-08-06",
            request_meta="{}",
            outcome="landed",
            artefact_digest="rolling-digest",
        )

        origins = str(store.attempts()[0]["artefact_origins"])

        assert "from=2026-05-07" in origins
        assert "from=2026-05-08" in origins
