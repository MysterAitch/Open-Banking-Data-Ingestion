"""A person answers the review flags the evidence cannot answer, and the answer holds.

The household is built in `flag_world` and every answer is decided there before the first run:
four flags are real questions (one on the everyday account, two on the bakery card, one on the
ticket account) and two more were answered by the evidence and never reach a person. Rows come
in through the importers and the replay of landed responses, and the rebuild at the end of the
build is the one a deployment runs after every release, which replays every artefact from
scratch and would raise the same flags again: an answer is only worth having if it survives it.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from flag_world import (
    BAKERY,
    EVERYDAY,
    LABELS,
    OPEN_QUESTIONS,
    SETTLED_BUS,
    SETTLED_TRAIN,
    TICKETS,
    add_unsettled_pair,
    build_flag_world,
)
from obdi.agreement import standing_of
from obdi.balance_anchors import effective_opening, record_stated_anchor
from obdi.ingest.rebuild import rebuild_from_raw
from obdi.ingest.store import Store
from obdi.movement_completeness import MovementCompleteness
from obdi.protection import press
from obdi.review_flags import (
    FlagRefused,
    answer_one_payment,
    answer_two_payments,
    build_queue,
    evidence_for,
    fingerprint_of,
    undo,
)
from obdi.review_report import live_neighbours


@pytest.fixture
def db(tmp_path):
    return build_flag_world(tmp_path)


def labels(ref: str) -> str:
    return LABELS.get(ref, ref)


def cards_of(store: Store, account: str):
    return [c for c in build_queue(store, labels).cards if c.account == LABELS[account]]


def count(store: Store, account: str) -> int:
    return int(
        store.connection.execute(
            "SELECT COUNT(*) FROM transactions WHERE account_id = ?", (account,)
        ).fetchone()[0]
    )


def sighting_count(store: Store, account: str) -> int:
    return int(
        store.connection.execute(
            "SELECT COUNT(*) FROM transaction_sources s JOIN transactions t USING (entity_id) "
            "WHERE t.account_id = ?",
            (account,),
        ).fetchone()[0]
    )


def neighbours_of(store: Store, card) -> list[str]:
    return [n for n, _ in live_neighbours(store, card.flag_id)]


def answer_one(store: Store, card, neighbour: int = 0):
    return answer_one_payment(
        store, card.flag_id, card.neighbours[neighbour].row.entity_id, card.fingerprint
    )


class TestTheQueueListsTheQuestionsAndOnlyThose:
    def test_Queue_OnAHouseholdWithFourRealQuestions_ListsFourCards(self, db):
        with Store(db) as store:
            queue = build_queue(store, labels)

        assert len(queue.cards) == OPEN_QUESTIONS
        assert sorted(c.account for c in queue.cards) == sorted(
            [LABELS[EVERYDAY], LABELS[BAKERY], LABELS[BAKERY], LABELS[TICKETS]]
        )

    def test_Queue_FlagsTheEvidenceHasAnswered_AreNotListed(self, db):
        with Store(db) as store:
            queue = build_queue(store, labels)

        assert {LABELS[SETTLED_BUS], LABELS[SETTLED_TRAIN]}.isdisjoint(
            c.account for c in queue.cards
        )

    def test_Queue_WhenAnAnsweredFlagStandsInTheQueue_CountsItAndStillDoesNotListIt(self, db):
        add_unsettled_pair(db)

        with Store(db) as store:
            queue = build_queue(store, labels)

        assert queue.settled == 1
        assert len(queue.cards) == OPEN_QUESTIONS
        assert LABELS[SETTLED_BUS] not in {c.account for c in queue.cards}

    def test_Queue_WhenNothingStandsAnswered_CountsNone(self, db):
        with Store(db) as store:
            assert build_queue(store, labels).settled == 0

    def test_Card_ForAFlagWithTwoNeighbours_NamesBothAndHowFarApartEachIs(self, db):
        with Store(db) as store:
            cards = cards_of(store, BAKERY)

        assert [len(c.neighbours) for c in cards] == [2, 2]
        assert all(n.days_apart == 0 for c in cards for n in c.neighbours)

    def test_Card_ForTicketsOneDayApart_SaysSo(self, db):
        with Store(db) as store:
            (card,) = cards_of(store, TICKETS)

        assert card.neighbours[0].days_apart == 1
        assert card.direction == "out"


class TestTheEvidenceIsSaidBothWays:
    def test_Bakery_WhenBothRowsCameInOneFile_PointsToTwoPaymentsAndNamesTheFile(self, db):
        with Store(db) as store:
            card = cards_of(store, BAKERY)[0]

        said = {e.verdict: e.sentence for e in card.neighbours[0].says}
        assert said["two"] == (
            "Both were listed by the export in one response, which lists a payment once."
        )

    def test_Everyday_WhenTheAggregatorGaveTwoIdsInSeparateResponses_PointsBothWays(self, db):
        with Store(db) as store:
            (card,) = cards_of(store, EVERYDAY)

        said = {e.verdict: e.sentence for e in card.neighbours[0].says}
        assert said["two"] == "The aggregator gave them different ids."
        assert "can report one payment again under a new id" in said["one"]

    def test_Evidence_WhenEachSourceListedOnlyOne_IsThePatternOfOnePaymentSeenTwice(self):
        found = evidence_for({"truelayer"}, {"csv"}, set(), {}, {}, 2)

        assert [e.verdict for e in found] == ["one"]
        assert found[0].sentence == (
            "One is listed only by the aggregator and the other only by the export, "
            "2 days apart: this is the pattern of one payment seen twice."
        )

    def test_Evidence_WhenTheSameSourceListedBothInOneResponse_PointsToTwoPayments(self):
        found = evidence_for({"csv"}, {"csv"}, {"csv"}, {}, {}, 0)

        assert [e.verdict for e in found] == ["two"]

    def test_Evidence_OnTheSameDay_SaysSoWithoutCountingDays(self):
        found = evidence_for({"truelayer"}, {"csv"}, set(), {}, {}, 0)

        assert "on the same day" in found[0].sentence
        assert "0 days" not in found[0].sentence


class TestTwoPaymentsIsRememberedAcrossARebuild:
    def test_TwoPayments_WhenAnswered_TheFlagClosesAndBothRowsStand(self, db):
        with Store(db) as store:
            (card,) = cards_of(store, EVERYDAY)
            before = count(store, EVERYDAY)

            outcome = answer_two_payments(store, card.flag_id, card.fingerprint)

            assert card.flag_id not in {f["entity_id"] for f in store.review_queue()}
            assert count(store, EVERYDAY) == before
            assert len(build_queue(store, labels).cards) == OPEN_QUESTIONS - 1
            assert "two payments" in outcome.sentence

    def test_TwoPayments_WhenARebuildReplaysEverything_TheFlagDoesNotReturn(self, db):
        with Store(db) as store:
            (card,) = cards_of(store, EVERYDAY)
            answer_two_payments(store, card.flag_id, card.fingerprint)
            rebuild_from_raw(store)

            assert len(build_queue(store, labels).cards) == OPEN_QUESTIONS - 1
            assert cards_of(store, EVERYDAY) == []
            assert count(store, EVERYDAY) == 2

    def test_AnUnansweredFlag_WhenARebuildReplaysEverything_IsRaisedAgain(self, db):
        with Store(db) as store:
            rebuild_from_raw(store)

            assert len(build_queue(store, labels).cards) == OPEN_QUESTIONS

    def test_TwoPayments_WhenAnswered_AppearsInTheAnsweredListWithItsAnswer(self, db):
        with Store(db) as store:
            (card,) = cards_of(store, EVERYDAY)
            answer_two_payments(store, card.flag_id, card.fingerprint)

            (line,) = build_queue(store, labels).answered

        assert (line.answer, line.account, line.day) == ("two", LABELS[EVERYDAY], "2026-09-14")

    def test_TwoPayments_WhenUndone_TheFlagIsOpenAgainAndSurvivesARebuildAsAQuestion(self, db):
        with Store(db) as store:
            (card,) = cards_of(store, EVERYDAY)
            answer_two_payments(store, card.flag_id, card.fingerprint)

            undo(store, "two", card.flag_id, "")

            assert len(build_queue(store, labels).cards) == OPEN_QUESTIONS
            assert build_queue(store, labels).answered == ()
            rebuild_from_raw(store)
            assert len(build_queue(store, labels).cards) == OPEN_QUESTIONS


class TestTheAnsweredListIsTheLastTwenty:
    def test_AnsweredList_AfterTwentyOneAnswers_ListsTheNewestTwentyAndNotTheFirst(self, tmp_path):
        from obdi.ingest.pipeline import import_file

        path = tmp_path / "bulk.csv"
        path.write_text(
            "Date,Counter Party,Reference,Type,Amount (GBP),Balance (GBP)\n"
            + "".join(f"14/09/2026,Bulk Payee,Bulk ref {n},CARD,-7.77,0\n" for n in range(22)),
            encoding="utf-8",
        )
        with Store(tmp_path / "bulk.sqlite3") as store:
            import_file(store, path, account_id="bulk")
            cards = build_queue(store, labels).cards
            assert len(cards) == 21, "22 equal rows in one file leave the last 21 flagged"
            first = None
            for position, card in enumerate(cards):
                answer_two_payments(
                    store,
                    card.flag_id,
                    card.fingerprint,
                    now=datetime(2026, 10, 1, 9, 0, position, tzinfo=UTC),
                )
                first = first or card.flag_id

            shown = build_queue(store, labels).answered

        assert len(shown) == 20
        assert first not in {line.flag_id for line in shown}
        assert [line.answered_on for line in shown] == ["2026-10-01"] * 20


class TestOnePaymentJoinsTheRowsAndIsRememberedAcrossARebuild:
    def test_OnePayment_WhenAnswered_TheRowsBecomeOneAndKeepEverySighting(self, db):
        with Store(db) as store:
            (card,) = cards_of(store, EVERYDAY)
            rows, seen = count(store, EVERYDAY), sighting_count(store, EVERYDAY)

            outcome = answer_one(store, card)

            assert count(store, EVERYDAY) == rows - 1
            assert sighting_count(store, EVERYDAY) == seen
            assert cards_of(store, EVERYDAY) == []
            assert "one payment" in outcome.sentence

    def test_OnePayment_WhenARebuildReplaysEverything_TheRowsAreStillOne(self, db):
        with Store(db) as store:
            (card,) = cards_of(store, EVERYDAY)
            answer_one(store, card)
            rebuild_from_raw(store)

            assert count(store, EVERYDAY) == 1
            assert sighting_count(store, EVERYDAY) == 2
            assert cards_of(store, EVERYDAY) == []
            assert len(build_queue(store, labels).cards) == OPEN_QUESTIONS - 1

    def test_OnePayment_WhenAnsweredTwice_ASecondRebuildStillHoldsIt(self, db):
        with Store(db) as store:
            (card,) = cards_of(store, EVERYDAY)
            answer_one(store, card)
            rebuild_from_raw(store)
            rebuild_from_raw(store)

            assert count(store, EVERYDAY) == 1

    def test_OnePayment_WhenUndone_TheRowsStayJoinedUntilTheNextRebuildThenSeparate(self, db):
        with Store(db) as store:
            (card,) = cards_of(store, EVERYDAY)
            answer_one(store, card)
            kept = store.connection.execute(
                "SELECT entity_id FROM transactions WHERE account_id = ?", (EVERYDAY,)
            ).fetchone()[0]
            gone = next(
                n.row.entity_id for n in card.neighbours
            ) if kept == card.flag_id else card.flag_id

            said = undo(store, "one", kept, gone).sentence

            assert count(store, EVERYDAY) == 1
            assert "until the next rebuild" in said
            assert build_queue(store, labels).answered == ()
            rebuild_from_raw(store)
            assert count(store, EVERYDAY) == 2
            assert len(cards_of(store, EVERYDAY)) == 1

    def test_OnePayment_WhenAnswered_AppearsInTheAnsweredList(self, db):
        with Store(db) as store:
            (card,) = cards_of(store, EVERYDAY)
            answer_one(store, card)

            (line,) = build_queue(store, labels).answered

        assert (line.answer, line.account) == ("one", LABELS[EVERYDAY])
        assert line.other_id


class TestAnAnswerIsRefusedWhereItCouldLandOnSomethingElse:
    def test_Answer_WhenTheCardHasChangedSinceItWasDrawn_IsRefusedAndChangesNothing(self, db):
        with Store(db) as store:
            first, second = cards_of(store, BAKERY)
            answer_one(store, first)
            rows = count(store, BAKERY)

            with pytest.raises(FlagRefused, match="has changed since the page was drawn"):
                answer_two_payments(store, second.flag_id, second.fingerprint)

            assert count(store, BAKERY) == rows

    def test_Answer_WhenTheCardIsFresh_IsTaken(self, db):
        with Store(db) as store:
            first, second = cards_of(store, BAKERY)
            answer_two_payments(store, first.flag_id, first.fingerprint)

            fresh = {c.flag_id: c for c in build_queue(store, labels).cards}[second.flag_id]
            answer_two_payments(store, fresh.flag_id, fresh.fingerprint)

            assert cards_of(store, BAKERY) == []

    def test_Answer_ForAFlagThatDoesNotExist_IsRefused(self, db):
        with Store(db) as store, pytest.raises(FlagRefused, match="no open flag by that name"):
            answer_two_payments(store, "0" * 32, "anything")

    def test_Answer_ForAMalformedId_IsRefusedAndChangesNothing(self, db):
        with Store(db) as store:
            before = len(store.review_queue())
            for bad in ("'; DROP TABLE review_queue; --", "../../etc", "%00", " "):
                with pytest.raises(FlagRefused):
                    answer_two_payments(store, bad, "abc")

            assert len(store.review_queue()) == before

    def test_Answer_WithNoFingerprint_IsRefused(self, db):
        with Store(db) as store:
            (card,) = cards_of(store, EVERYDAY)
            with pytest.raises(FlagRefused, match="did not say which flag"):
                answer_two_payments(store, card.flag_id, "")

            assert len(cards_of(store, EVERYDAY)) == 1

    def test_Answer_WhenTheFlagWasAlreadyAnswered_IsRefused(self, db):
        with Store(db) as store:
            (card,) = cards_of(store, EVERYDAY)
            answer_two_payments(store, card.flag_id, card.fingerprint)

            with pytest.raises(FlagRefused, match="no open flag by that name"):
                answer_two_payments(store, card.flag_id, card.fingerprint)

    def test_OnePayment_WithARowThatWasNotWeighedAgainstTheFlag_IsRefused(self, db):
        with Store(db) as store:
            (card,) = cards_of(store, EVERYDAY)
            (other,) = cards_of(store, TICKETS)
            rows = count(store, EVERYDAY)

            with pytest.raises(FlagRefused, match="not one this flag was weighed against"):
                answer_one_payment(
                    store, card.flag_id, other.flagged.entity_id, card.fingerprint
                )

            assert count(store, EVERYDAY) == rows

    def test_Undo_OfAnAnswerNeverGiven_IsRefused(self, db):
        with Store(db) as store:
            (card,) = cards_of(store, EVERYDAY)
            for answer in ("two", "one", "maybe"):
                with pytest.raises(FlagRefused, match="Nothing was changed"):
                    undo(store, answer, card.flag_id, "x")


class TestAJoinIsRefusedWhereTheStoredEvidenceOrAProtectionForbidsIt:
    def test_OnePayment_WhenTheBanksFeedGaveTwoIdsForLife_IsRefusedAsTwoPayments(self, db):
        add_unsettled_pair(db)
        with Store(db) as store:
            flagged = [
                str(f["entity_id"])
                for f in store.review_queue()
                if str(f["entity_id"])
                in {
                    str(r[0])
                    for r in store.connection.execute(
                        "SELECT entity_id FROM transactions WHERE account_id = ?", (SETTLED_BUS,)
                    )
                }
            ]
            assert len(flagged) == 1
            (neighbour,) = [n for n, _ in live_neighbours(store, flagged[0])]
            rows = count(store, SETTLED_BUS)

            with pytest.raises(FlagRefused, match="names a payment by one id for life"):
                answer_one_payment(
                    store, flagged[0], neighbour, fingerprint_of(store, flagged[0], [neighbour])
                )

            assert count(store, SETTLED_BUS) == rows

    def test_OnePayment_InAProtectedPeriod_IsRefusedWithWhatToDoFirst(self, db):
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
            (card,) = cards_of(store, TICKETS)
            rows = count(store, TICKETS)

            with pytest.raises(FlagRefused) as refused:
                answer_one(store, card)

            assert "protected through 2026-09-15" in str(refused.value)
            assert 'Use "Remove the lock" on the account page first' in str(refused.value)
            assert count(store, TICKETS) == rows

    def test_TwoPayments_InAProtectedPeriod_IsStillTaken_BecauseNoRowChanges(self, db):
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
            (card,) = cards_of(store, TICKETS)

            answer_two_payments(store, card.flag_id, card.fingerprint)

            assert cards_of(store, TICKETS) == []
            assert store.protection_record(TICKETS) is not None

    def test_OnePayment_OutsideAnyProtectedPeriod_IsTaken(self, db):
        with Store(db) as store:
            (card,) = cards_of(store, TICKETS)

            answer_one(store, card)

            assert cards_of(store, TICKETS) == []
