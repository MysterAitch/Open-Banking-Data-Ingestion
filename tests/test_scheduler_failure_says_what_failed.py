"""A failed scheduler step says what it was for, what it puts at risk, and where it broke.

The owner's phone showed: "the scheduler's export-raw step failed in its last cycle
(JSONDecodeError (message withheld, as it may quote the data that broke it - see the
container log))". He cannot read the container log from a phone, and the message said
neither what to do nor whether money was at risk. It was not: the raw export is a
copy for browsing.

Known answers, decided before the first run. Each step declares what its failure puts
at risk, and the Overview's band follows that declaration:

    pull             nothing new fetched, the store unaffected      will break soon (2)
    pair-transfers   transfers unpaired until the next pairing      will break soon (2)
    export-raw       a copy for browsing, the store unaffected      housekeeping (3)
    push-actual      Actual behind until the next push              data at risk (1)
    alert            nothing was announced                          data at risk (1)

A step that raises a type whose text is withheld says where instead, in terms that
cannot carry data: the exception's type, the module and function of the innermost
frame in obdi's own code, and, for a step that works through items, which one.
Every payee and amount below is invented.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from obdi import cli
from obdi.alerts import Finding
from obdi.identity import artefact_digest, content_key
from obdi.models import RawArtefact
from obdi.overview import _alert_item
from obdi.scheduler_status import (
    CYCLE_STEPS,
    ItemPosition,
    describe_error,
    findings,
    run_step,
    strip_sentence,
)
from obdi.store import Store
from obdi.web import _scheduler_row
from obdi.web_scheduler import scheduler_section
from test_scheduler_status import (  # noqa: F401 - _clean_env is an autouse fixture
    LOOP,
    PRIVATE_AMOUNT,
    PRIVATE_PAYEE,
    SCHEDULED,
    Clock,
    _clean_env,
    reading,
    step,
    whole_cycle,
)

BANDS = {"now": 1, "soon": 2, "housekeeping": 3}

DECLARED = {
    "pull": ("nothing new was fetched this cycle", "soon"),
    "pair-transfers": ("were not paired this cycle", "soon"),
    "export-raw": ("a copy for browsing", "housekeeping"),
    "push-actual": ("Actual is behind", "now"),
    "alert": ("no notification was sent", "now"),
}


@pytest.fixture
def db(tmp_path) -> Path:
    path = tmp_path / "store.sqlite3"
    with Store(path):
        pass
    return path


def beat(db: Path):
    from obdi.scheduler_status import read_record

    return lambda: read_record(db)


class TestEachStepSaysWhatItsFailureRisks:
    @pytest.mark.parametrize("name", CYCLE_STEPS)
    def test_Finding_WhenAStepFails_CarriesThatStepsOwnSentence(self, db, name):
        clock = Clock()
        whole_cycle(db, clock, failing=name)

        [(key, message)] = findings(reading(db, clock.now))

        phrase, _ = DECLARED[name]
        assert key == f"scheduler-failed:{name}"
        assert phrase in message
        for other, (other_phrase, _) in DECLARED.items():
            if other != name:
                assert other_phrase not in message

    @pytest.mark.parametrize("name", CYCLE_STEPS)
    def test_Severity_WhenAStepFails_FollowsTheRiskItDeclares(self, db, name):
        clock = Clock()
        whole_cycle(db, clock, failing=name)
        [(key, message)] = findings(reading(db, clock.now))

        item = _alert_item(Finding(key, message), lambda ref: ref)

        assert item.severity == BANDS[DECLARED[name][1]]

    def test_Severity_WhenAFailedBrowsingCopy_IsHousekeepingNotDataAtRisk(self, db):
        clock = Clock()
        whole_cycle(db, clock, failing="export-raw")
        [(key, message)] = findings(reading(db, clock.now))

        item = _alert_item(Finding(key, message), lambda ref: ref)

        assert item.severity_word == "Housekeeping"

    def test_Severity_WhenTheStepIsNotOneTheSchedulerDeclares_StaysDataAtRisk(self):
        item = _alert_item(Finding("scheduler-failed:unheard-of", "failed"), lambda ref: ref)

        assert item.severity == 1

    @pytest.mark.parametrize("name", CYCLE_STEPS)
    def test_Pages_WhenAStepFails_ShowTheSentenceBesideTheError(self, db, name):
        clock = Clock()
        whole_cycle(db, clock, failing=name)

        section = scheduler_section(beat(db), clock.now)
        row = _scheduler_row(beat(db), clock.now)
        sentence, _ = strip_sentence(reading(db, clock.now))

        phrase, _ = DECLARED[name]
        for page in (section, row, sentence):
            assert phrase in page


class TestWhereAWithheldFailureArose:
    def test_Error_WhenTextIsWithheld_NamesTheTypeTheModuleAndTheFunction(self):
        error = None
        try:
            content_key(
                amount_minor=100,
                value_date="not a date",  # type: ignore[arg-type]
                description=PRIVATE_PAYEE,
            )
        except AttributeError as raised:
            error = describe_error(raised)

        assert error is not None
        assert error["type"] == "AttributeError"
        assert error["withheld"] is True
        assert "obdi.identity.content_key" in str(error["where"])
        assert PRIVATE_PAYEE not in json.dumps(error)
        assert "isoformat" not in json.dumps(error)

    def test_Error_WhenKeptTextReadsAsAnAmount_IsWithheldAndStillSaysWhere(self):
        error = None
        try:
            raise RuntimeError(f"transaction of {PRIVATE_AMOUNT} could not be sent")
        except RuntimeError as raised:
            error = describe_error(raised)

        assert error["withheld"] is True
        assert PRIVATE_AMOUNT not in json.dumps(error)
        assert "where" in error

    def test_Error_WhenNoFrameIsObdisOwn_SaysSoInsteadOfNamingAnotherLibrary(self):
        error = None
        try:
            int("quokka")
        except ValueError as raised:
            error = describe_error(raised)

        assert error is not None
        assert "outside obdi's own code" in str(error["where"])
        assert "quokka" not in json.dumps(error)

    def test_Error_WhenTheTypeIsOneWhoseTextIsKept_StillShowsTheText(self):
        error = None
        try:
            raise PermissionError("denied")
        except PermissionError as raised:
            error = describe_error(raised)

        assert error["message"] == "denied"
        assert error["withheld"] is False

    def test_Pages_WhenAStepRaisedOverAPayee_NeverShowThePayeeOrTheAmount(self, db):
        clock = Clock()
        step(db, "pull", clock)

        def broken(_handle):
            content_key(
                amount_minor=100,
                value_date="x",  # type: ignore[arg-type]
                description=f"{PRIVATE_PAYEE} {PRIVATE_AMOUNT}",
            )
            return 0

        with pytest.raises(AttributeError):
            step(db, "pair-transfers", clock, broken)

        pages = [
            scheduler_section(beat(db), clock.now),
            _scheduler_row(beat(db), clock.now),
            *(message for _, message in findings(reading(db, clock.now))),
        ]
        for page in pages:
            assert PRIVATE_PAYEE not in page
            assert PRIVATE_AMOUNT not in page
        assert "obdi.identity.content_key" in pages[0]


class TestTheItemPositionIsWordedWithoutData:
    @pytest.mark.parametrize(
        ("position", "words"),
        [(1, "1st"), (2, "2nd"), (3, "3rd"), (4, "4th"), (11, "11th"), (12, "12th"),
         (13, "13th"), (21, "21st"), (22, "22nd"), (111, "111th"), (112, "112th")],
    )
    def test_Position_IsGivenAsAnOrdinal(self, position, words):
        assert ItemPosition("artefact", position).words() == f"the {words} artefact"

    def test_Source_WhenItIsPlainlyAName_IsNamed(self):
        words = ItemPosition("artefact", 2, source="truelayer-booked").words()

        assert words == "the 2nd artefact, of source truelayer-booked"

    @pytest.mark.parametrize("source", [f"{PRIVATE_PAYEE}", "pay 871.23 now", "a/b", ""])
    def test_Source_WhenItIsNotPlainlyAName_IsLeftOut(self, source):
        words = ItemPosition("artefact", 2, source=source).words()

        assert words == "the 2nd artefact"


def landed(store: Store, source: str, number: int, media_type: str) -> None:
    payload = json.dumps({"results": [], "number": number}).encode()
    store.land_artefact(
        RawArtefact(
            source=source,
            account_ref="acct",
            fetched_at=datetime(2026, 9, number, tzinfo=UTC),
            media_type=media_type,
            digest=artefact_digest(payload),
            payload=payload,
            origin=f"origin-{number}",
        )
    )


class Tripwire(dict):
    """A media-type table that breaks on one type, quoting what it must not."""

    def get(self, key, default=None):
        if key == "application/x-tripwire":
            raise ValueError(f"cannot read {PRIVATE_PAYEE} {PRIVATE_AMOUNT}")
        return super().get(key, default)


class TestTheRawExportSaysWhichArtefactItWasOn:
    def test_RawExport_WhenTheThirdArtefactBreaksIt_SaysWhichOneAndOfWhichSource(
        self, db, tmp_path, monkeypatch
    ):
        with Store(db) as store:
            landed(store, "demo-source", 1, "application/json")
            landed(store, "demo-source", 2, "application/json")
            landed(store, "demo-source", 3, "application/x-tripwire")
        monkeypatch.setattr(cli, "_MEDIA_EXTENSIONS", Tripwire(cli._MEDIA_EXTENSIONS))
        clock = Clock()
        step(db, "pull", clock)
        step(db, "pair-transfers", clock)

        with pytest.raises(ValueError):
            run_step(
                db,
                "export-raw",
                lambda handle: cli._export_raw(db, tmp_path / "raw", handle),
                environ=SCHEDULED,
                clock=clock,
                parent_pid=lambda: LOOP,
                keepalive_seconds=3600,
            )

        [(key, message)] = findings(reading(db, clock.now))
        assert key == "scheduler-failed:export-raw"
        assert "ValueError" in message
        assert "obdi.cli._export_raw" in message
        assert "the 3rd artefact, of source demo-source, out of 3" in message
        assert "a copy for browsing" in message
        for text in (message, scheduler_section(beat(db), clock.now)):
            assert PRIVATE_PAYEE not in text
            assert PRIVATE_AMOUNT not in text

    def test_RawExport_WhenItSucceeds_SaysNothingOfWhereOrRisk(self, db, tmp_path):
        with Store(db) as store:
            landed(store, "demo-source", 1, "application/json")
        clock = Clock()
        step(db, "pull", clock)
        step(db, "pair-transfers", clock)

        code = run_step(
            db,
            "export-raw",
            lambda handle: cli._export_raw(db, tmp_path / "raw", handle),
            environ=SCHEDULED,
            clock=clock,
            parent_pid=lambda: LOOP,
            keepalive_seconds=3600,
        )

        assert code == 0
        assert findings(reading(db, clock.now)) == []
