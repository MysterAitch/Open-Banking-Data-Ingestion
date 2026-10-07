"""A flag is answered by the balances when they are reproduced only with both rows counted.

The household is `flag_balance_world`, whose docstring fixes every answer from the construction
before any run. In short: a card holds two payments of 20.00 on 2026-09-14 that one file lists,
so the matcher flagged them, and a balance of 100.00 on 09-10 is followed by 55.00 on 09-20 only
if both are counted (75.00 if one is).

    settled by the balances   between, closes-on-day, apart, and both flags of triple
    open, with a missing known balance named
                              opens-on-day, first, empty (before); last, apart-early (after);
                              single (one known balance)
    open, nothing missing     unreproduced, middle-unmet, triple-short (the balances do not say
                              it), two-responses (no source lists both rows), typed-only (its
                              balances are followed, not tested)

Seventeen flags are raised in all: five are settled and twelve stay open.
"""

from __future__ import annotations

import shutil
import time
from collections import defaultdict
from datetime import date

import pytest

from flag_balance_world import (
    AGGREGATED,
    TYPED_ONLY,
    build_balance_world,
    label_of,
    state_balances,
)
from obdi.ingest.rebuild import rebuild_from_raw
from obdi.ingest.store import Store
from obdi.verify.agreement import DEFINES, MET, UNMET, Known
from obdi.verify.balance_anchors import effective_opening, remove_stated_anchor
from obdi.verify.review_flags import answer_two_payments, answered_lines, fingerprint_of
from obdi.verify.review_report import (
    GAP_AFTER,
    GAP_BEFORE,
    GAP_SINGLE,
    SETTLED_CLASSES,
    BalanceGap,
    FlagClass,
    assess_flags,
    balance_proof,
    live_neighbours,
    review_report,
)
from obdi.verify.review_settlement import settle_review_flags

D = date

SETTLED = FlagClass.BALANCES_NEED_BOTH
OPEN = FlagClass.OPEN

#: Account -> the class of each of its flags, before anything is settled.
EXPECTED: dict[str, list[FlagClass]] = {
    "between": [SETTLED],
    "closes-on-day": [SETTLED],
    "apart": [SETTLED],
    "triple": [SETTLED, SETTLED],
    "opens-on-day": [OPEN],
    "first": [OPEN],
    "empty": [OPEN],
    "last": [OPEN],
    "apart-early": [OPEN],
    "single": [OPEN],
    "unreproduced": [OPEN],
    "middle-unmet": [OPEN],
    "triple-short": [OPEN, OPEN],
    AGGREGATED: [OPEN],
    TYPED_ONLY: [OPEN],
}

#: Account -> the missing known balance the open flag names, where one is named.
GAPS: dict[str, BalanceGap] = {
    "opens-on-day": BalanceGap(GAP_BEFORE, D(2026, 9, 14)),
    "first": BalanceGap(GAP_BEFORE, D(2026, 9, 14)),
    "empty": BalanceGap(GAP_BEFORE, D(2026, 9, 14)),
    "last": BalanceGap(GAP_AFTER, D(2026, 9, 14)),
    "apart-early": BalanceGap(GAP_AFTER, D(2026, 9, 15)),
    "single": BalanceGap(GAP_SINGLE, D(2026, 9, 20)),
}

SETTLED_FLAGS = 5
OPEN_FLAGS = 12


@pytest.fixture(scope="module")
def standing(tmp_path_factory):
    """The household with every flag still standing and every balance stated."""
    root = tmp_path_factory.mktemp("standing")
    return build_balance_world(root, rebuild=False)


@pytest.fixture
def db(standing, tmp_path):
    """A copy of the standing household that a test may settle, rebuild, or change."""
    copy = tmp_path / "copy.sqlite3"
    shutil.copy(standing, copy)
    return copy


def by_account(store: Store) -> dict[str, list[FlagClass]]:
    found: dict[str, list[FlagClass]] = defaultdict(list)
    for item in assess_flags(store).values():
        found[item.account].append(item.flag_class)
    return {account: sorted(classes) for account, classes in found.items()}


def expected() -> dict[str, list[FlagClass]]:
    return {account: sorted(classes) for account, classes in EXPECTED.items()}


class TestEachFlagInTheHousehold:
    def test_Flags_WhenBalancesAreStated_AreClassedAsTheHandWorkedAnswersSay(self, standing):
        with Store(standing) as store:
            assert by_account(store) == expected()

    def test_Flags_WhenPairSitsBetweenTwoReproducedBalances_AreAnsweredByThem(self, standing):
        with Store(standing) as store:
            assert by_account(store)["between"] == [SETTLED]

    def test_Flags_WhenTheLaterBalanceIsTheFigureWithOneRowCounted_StayOpenAndNameNoGap(
        self, standing
    ):
        """75.00 on 09-20 is what one payment of 20.00 would give, so the later balance is
        the account disagreeing and not a missing balance."""
        with Store(standing) as store:
            (item,) = [a for a in assess_flags(store).values() if a.account == "unreproduced"]

        assert item.flag_class is OPEN
        assert item.balance_gap is None

    @pytest.mark.parametrize("account", sorted(GAPS))
    def test_Flags_WhenAKnownBalanceIsMissing_StayOpenAndNameWhichOne(self, standing, account):
        with Store(standing) as store:
            (item,) = [a for a in assess_flags(store).values() if a.account == account]

        assert item.flag_class is OPEN
        assert item.balance_gap == GAPS[account]

    @pytest.mark.parametrize(
        "account", ["middle-unmet", "triple-short", AGGREGATED, TYPED_ONLY, "unreproduced"]
    )
    def test_Flags_WhenTheBalancesCannotSpeakForThem_NameNoMissingBalance(
        self, standing, account
    ):
        with Store(standing) as store:
            items = [a for a in assess_flags(store).values() if a.account == account]

        assert items
        assert all(i.flag_class is OPEN and i.balance_gap is None for i in items)


class TestTheBoundaryOfADay:
    """A known balance is the figure at the END of its day, rows of that day included."""

    def test_KnownBalance_WhenDatedOnThePairsDay_ClosesItAndNeverOpensIt(self, standing):
        with Store(standing) as store:
            found = by_account(store)

        assert found["closes-on-day"] == [SETTLED], "60.00 on 09-14 holds both rows"
        assert found["opens-on-day"] == [OPEN], "the same balance cannot start the span"

    def test_KnownBalance_WhenBetweenThePairsOwnRows_IsNeitherSideOfTheProof(self, standing):
        """Rows on 09-12 and 09-15 with 80.00 on 09-14 between them: with a balance on 09-20 it
        is only a check in the span, and with nothing later there is no closing balance."""
        with Store(standing) as store:
            found = by_account(store)

        assert found["apart"] == [SETTLED]
        assert found["apart-early"] == [OPEN]


class TestWhichKnownBalancesCount:
    def test_Proof_WhenOnlyOneKnownBalanceFollowsThePair_IsNotMade(self):
        """An opening worked out backwards from the first balance absorbs any error."""
        knowns = [Known(D(2026, 9, 20), "stated", DEFINES, 5500)]

        assert balance_proof(knowns, D(2026, 9, 14), D(2026, 9, 14)) == (
            False,
            BalanceGap(GAP_SINGLE, D(2026, 9, 20)),
        )

    def test_Proof_WhenOnlyOneKnownBalancePrecedesThePair_IsNotMadeAndSaysWhatIsAfter(self):
        knowns = [Known(D(2026, 9, 10), "stated", DEFINES, 10000)]

        assert balance_proof(knowns, D(2026, 9, 14), D(2026, 9, 14)) == (
            False,
            BalanceGap(GAP_AFTER, D(2026, 9, 14)),
        )

    def test_Proof_WhenTwoKnownBalancesPrecedeThePair_IsNotMade(self):
        knowns = [
            Known(D(2026, 9, 8), "stated", DEFINES, 10000),
            Known(D(2026, 9, 10), "stated", MET, 10000),
        ]

        assert balance_proof(knowns, D(2026, 9, 14), D(2026, 9, 14)) == (
            False,
            BalanceGap(GAP_AFTER, D(2026, 9, 14)),
        )

    def test_Proof_WhenTheOpeningBalanceDefinesTheEarlierSide_IsMade(self):
        knowns = [
            Known(D(2026, 9, 10), "stated", DEFINES, 10000),
            Known(D(2026, 9, 20), "stated", MET, 5500),
        ]

        assert balance_proof(knowns, D(2026, 9, 14), D(2026, 9, 14)) == (True, None)

    def test_Proof_WhenTheLaterBalanceOnlyDefinesTheOpening_IsNotMade(self):
        """A balance that is not tested cannot close the span."""
        knowns = [
            Known(D(2026, 9, 10), "stated", MET, 10000),
            Known(D(2026, 9, 20), "stated", DEFINES, 5500),
        ]

        assert balance_proof(knowns, D(2026, 9, 14), D(2026, 9, 14)) == (False, None)

    def test_Proof_WhenTwoSourcesStateDifferentFiguresForADayInTheSpan_IsNotMade(self):
        """A conflict between sources says nothing about the rows, so nothing is proven."""
        knowns = [
            Known(D(2026, 9, 10), "stated", DEFINES, 10000),
            Known(D(2026, 9, 17), "stated", MET, 5800),
            Known(D(2026, 9, 17), "statement", MET, 5900),
            Known(D(2026, 9, 20), "stated", MET, 5500),
        ]

        assert balance_proof(knowns, D(2026, 9, 14), D(2026, 9, 14)) == (False, None)

    def test_Proof_WhenTheSameDayIsStatedTwiceAlike_IsMade(self):
        knowns = [
            Known(D(2026, 9, 10), "stated", DEFINES, 10000),
            Known(D(2026, 9, 17), "stated", MET, 5800),
            Known(D(2026, 9, 17), "statement", MET, 5800),
            Known(D(2026, 9, 20), "stated", MET, 5500),
        ]

        assert balance_proof(knowns, D(2026, 9, 14), D(2026, 9, 14)) == (True, None)

    def test_Proof_WhenAnUnreproducedBalanceSitsInTheSpan_IsNotMade(self):
        knowns = [
            Known(D(2026, 9, 10), "stated", DEFINES, 10000),
            Known(D(2026, 9, 16), "stated", UNMET, 9000),
            Known(D(2026, 9, 20), "stated", MET, 5500),
        ]

        assert balance_proof(knowns, D(2026, 9, 14), D(2026, 9, 14)) == (False, None)

    def test_Proof_WhenAnUnreproducedBalanceLiesOutsideTheSpan_IsStillMade(self):
        """The proof is the difference between two reproduced balances: an earlier balance that
        was out, or a later one, does not touch the rows between K1 and K2."""
        knowns = [
            Known(D(2026, 9, 1), "stated", UNMET, 1),
            Known(D(2026, 9, 10), "stated", MET, 10000),
            Known(D(2026, 9, 20), "stated", MET, 5500),
            Known(D(2026, 9, 30), "stated", UNMET, 2),
        ]

        assert balance_proof(knowns, D(2026, 9, 14), D(2026, 9, 14)) == (True, None)

    def test_Proof_WhenABalanceStatedForAMomentBoundsTheSpan_IsNotUsedForEitherSide(self):
        knowns = [
            Known(D(2026, 9, 10), "stated", DEFINES, 10000),
            Known(D(2026, 9, 14), "bank", MET, 6000, instant=True),
        ]

        assert balance_proof(knowns, D(2026, 9, 14), D(2026, 9, 14)) == (
            False,
            BalanceGap(GAP_AFTER, D(2026, 9, 14)),
        )

    def test_Proof_WhenAMomentBalanceInTheSpanIsOut_IsNotMade(self):
        knowns = [
            Known(D(2026, 9, 10), "stated", DEFINES, 10000),
            Known(D(2026, 9, 16), "bank", UNMET, 9000, instant=True),
            Known(D(2026, 9, 20), "stated", MET, 5500),
        ]

        assert balance_proof(knowns, D(2026, 9, 14), D(2026, 9, 14)) == (False, None)


class TestATripleOfEqualRows:
    def test_Triple_WhenTheBalancesNeedAllThree_EveryFlagOfItIsAnswered(self, standing):
        """The rows between 09-10 and 09-20 are 3 x 20.00 + 5.00 = 65.00, so 100.00 is followed
        by 35.00, and dropping any one of the three would leave 55.00."""
        with Store(standing) as store:
            flags = [a for a in assess_flags(store).values() if a.account == "triple"]

        assert [f.flag_class for f in flags] == [SETTLED, SETTLED]
        assert all(f.live_neighbours == 2 for f in flags)

    def test_Triple_WhenTheBalancesNeedOnlyTwo_EveryFlagOfItStaysOpen(self, standing):
        """55.00 is what two of the three would give: the third is not shown to be needed, and
        no flag of the account is closed by a proof that covers only some of its rows."""
        with Store(standing) as store:
            flags = [a for a in assess_flags(store).values() if a.account == "triple-short"]

        assert [f.flag_class for f in flags] == [OPEN, OPEN]


class TestSettlingThem:
    def test_Settlement_WhenTheBalancesAnswerFlags_ClosesExactlyThoseAndNoOthers(self, db):
        with Store(db) as store:
            before = by_account(store)
            report = settle_review_flags(store)
            after = by_account(store)

        assert report.settled == {SETTLED: SETTLED_FLAGS}
        assert report.still_open == OPEN_FLAGS
        assert after == {a: c for a, c in before.items() if c[0] is OPEN}

    def test_Settlement_WhenDescribed_NamesTheClassAndCount(self, db):
        with Store(db) as store:
            sentence = settle_review_flags(store).describe()

        assert f"{SETTLED_FLAGS} balances-need-both" in sentence

    def test_Settlement_WhenRepeated_ChangesNothingFurther(self, db):
        with Store(db) as store:
            settle_review_flags(store)
            again = settle_review_flags(store)

        assert again.settled == {}
        assert again.still_open == OPEN_FLAGS

    def test_Settlement_WhenARowIsImportedLater_ClosesTheFlagThatNewRowRaises(self, tmp_path):
        """The door a person's file comes in by settles on its own, with no rebuild."""
        from obdi.ingest.pipeline import import_file

        build_balance_world(tmp_path, accounts=["single"], rebuild=False)
        extra = tmp_path / "extra.qif"
        extra.write_text("!Type:CCard\nD25/09/2026\nT-1.00\nPOne more\n^\n", encoding="utf-8")
        with Store(tmp_path / "store.sqlite3") as store:
            assert by_account(store) == {"single": [OPEN]}
            state_balances(store, "single", (("2026-09-10", "100.00"),))
            assert by_account(store) == {"single": [SETTLED]}, "nothing has settled it yet"

            import_file(store, extra, account_id="single")

            assert by_account(store) == {}, "09-10 and 09-20 are both stated: the pair is settled"

    def test_Classes_WhenSettled_AreAllInTheSettledSet(self):
        assert SETTLED in SETTLED_CLASSES
        assert OPEN not in SETTLED_CLASSES


class TestAfterARebuild:
    def test_Rebuild_WhenBalancesAnswerFlags_SettlesTheSameFlagsAsTheLivePass(self, db):
        with Store(db) as store:
            report = rebuild_from_raw(store)
            after = by_account(store)

        assert report.review_settled == {SETTLED: SETTLED_FLAGS}
        assert report.review_still_open == OPEN_FLAGS
        assert after == {a: c for a, c in expected().items() if c[0] is OPEN}

    def test_Flag_WhenAStatedBalanceIsRemoved_IsAskedAgainByTheNextRebuild(self, tmp_path):
        """Nothing changes until a rebuild: a settled flag is gone like every other settled
        class, and the rebuild raises it again and closes it only if the proof still holds."""
        build_balance_world(tmp_path, accounts=["between"])
        with Store(tmp_path / "store.sqlite3") as store:
            assert store.review_queue() == []

            assert remove_stated_anchor(store, "between", "2026-09-20")
            assert store.review_queue() == [], "removing a balance does not reopen by itself"
            rebuild_from_raw(store)
            (reopened,) = assess_flags(store).values()
            assert reopened.flag_class is OPEN
            assert reopened.balance_gap == BalanceGap(GAP_AFTER, D(2026, 9, 14)), (
                "only 09-10 is left, which is before the pair, so what is missing is after it"
            )

            state_balances(store, "between", (("2026-09-20", "55.00"),))
            rebuild_from_raw(store)
            assert store.review_queue() == []

    def test_Flag_WhenALaterBalanceStopsBeingReproduced_IsAskedAgainByTheNextRebuild(
        self, tmp_path
    ):
        build_balance_world(tmp_path, accounts=["between"])
        with Store(tmp_path / "store.sqlite3") as store:
            state_balances(store, "between", (("2026-09-20", "75.00"),))
            rebuild_from_raw(store)

            (reopened,) = assess_flags(store).values()

        assert reopened.flag_class is OPEN
        assert reopened.balance_gap is None


class TestWhatAPersonHasAnswered:
    def test_Answer_WhenAPersonSaidTwoPaymentsBeforeTheBalancesDid_IsLeftAlone(self, db):
        with Store(db) as store:
            (flag,) = [
                (e, a) for e, a in assess_flags(store).items() if a.account == "between"
            ]
            flag_id = flag[0]
            neighbours = [n for n, _ in live_neighbours(store, flag_id)]
            answer_two_payments(store, flag_id, fingerprint_of(store, flag_id, neighbours))

            settle_review_flags(store)
            rebuild_from_raw(store)

            resolved = [
                r for r in store.review_queue(include_resolved=True) if r["resolved_at"]
            ]
            assert [str(r["entity_id"]) for r in resolved] == [flag_id]
            assert len(answered_lines(store)) == 1


class TestTheReport:
    def test_Report_WhenFlagsAreAnsweredByBalances_CountsThemLikeTheOtherClasses(self, standing):
        with Store(standing) as store:
            text = review_report(store, today=D(2026, 10, 4)).describe()

        assert (
            "    balances-need-both: 5 - the rows reproduce the known balances before and "
            "after with both counted"
        ) in text
        assert "    open: 12" in text
        assert "    balances-need-both / triple: 2" in text
        assert "    balances-need-both / between: 1" in text

    def test_Report_WhenMasked_HoldsNoPayeeAndNoAmount(self, standing):
        with Store(standing) as store:
            text = review_report(store, today=D(2026, 10, 4)).describe().casefold()

        for token in ("brass kettle", "20.00", "100.00", "55.00"):
            assert token not in text

    def test_Report_WhenTheFlagsAreSettled_NoLongerCountsThem(self, db):
        with Store(db) as store:
            settle_review_flags(store)
            text = review_report(store, today=D(2026, 10, 4)).describe()

        assert "balances-need-both" not in text
        assert "12 open flags" in text


class TestCost:
    """The balances of an account are read once, however many flags it holds."""

    FILLER = 300
    PAIRS = 20

    def _account(self, tmp_path, name: str, pairs: int):
        from obdi.ingest.accounts import AccountRecord, AccountRef
        from obdi.ingest.pipeline import import_file

        root = tmp_path / name
        root.mkdir()
        db = root / "store.sqlite3"
        lines = "".join(
            f"D{1 + n % 27:02}/08/2026\nT-{300 + n}.37\nPFiller {n}\n^\n"
            for n in range(self.FILLER)
        )
        lines += "".join(
            f"D14/09/2026\nT-{21 + n}.00\nPPair {n}\n^\nD14/09/2026\nT-{21 + n}.00\nPPair {n}\n^\n"
            for n in range(pairs)
        )
        (root / "card.qif").write_text("!Type:CCard\n" + lines, encoding="utf-8")
        with Store(db) as store:
            store.declare_account(AccountRecord(ref=AccountRef("card"), label=label_of("card")))
            import_file(store, root / "card.qif", account_id="card")
            filler = sum(30037 + 100 * n for n in range(self.FILLER))
            before = 20_000_000 - filler
            state_balances(store, "card", (("2026-09-01", f"{before / 100:.2f}"),))
            paired = sum(2 * (2100 + 100 * n) for n in range(pairs))
            state_balances(store, "card", (("2026-09-20", f"{(before - paired) / 100:.2f}"),))
        return db

    def test_Assessment_WhenTwentyFlagsShareOneAccount_ReadsItsBalancesOnce(
        self, tmp_path, monkeypatch
    ):
        db = self._account(tmp_path, "twenty", self.PAIRS)
        calls: list[str] = []
        real = effective_opening

        def counted(store, ref, *args, **kwargs):
            calls.append(ref)
            return real(store, ref, *args, **kwargs)

        monkeypatch.setattr("obdi.verify.review_report.effective_opening", counted)
        with Store(db) as store:
            assessed = assess_flags(store)

        assert len(assessed) == self.PAIRS
        assert {a.flag_class for a in assessed.values()} == {SETTLED}, "every pair needs both"
        assert calls == ["card"]

    def test_Assessment_WhenTwentyFlagsShareOneAccount_CostsAboutWhatOneFlagDoes(self, tmp_path):
        """A ratio over the minimum of three runs, as in test_movement_scaling. Reading the
        balances once per flag would make twenty flags cost about twenty times as much."""

        def timed(name: str, pairs: int) -> float:
            db = self._account(tmp_path, name, pairs)
            with Store(db) as store:
                assert len(assess_flags(store)) == pairs
                runs = []
                for _ in range(3):
                    start = time.perf_counter()
                    assess_flags(store)
                    runs.append(time.perf_counter() - start)
                return min(runs)

        one = timed("one", 1)
        twenty = timed("twenty-more", self.PAIRS)

        assert twenty < one * 4, f"1 flag {one:.3f} s, 20 flags {twenty:.3f} s"
