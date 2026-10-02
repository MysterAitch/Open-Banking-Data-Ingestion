"""The optimised matcher must be indistinguishable from the one it replaces.

Stage 0 of the fold-journal plan replaces linear candidate scans with hash
lookups. A changed match silently merges two real payments or duplicates
one - the named worst outcome of the whole system - so the refactor is
held to behavioural EQUIVALENCE, not plausibility: the pre-optimisation
algorithms are preserved here verbatim as reference oracles, and both
implementations are driven through randomised folds that must agree on
every single resolution, in full detail, across every seed.

The corpora are generated, not curated, on purpose: the failure modes
this guards against are the interactions nobody thinks to write a case
for - a supersession changing an entity's source_id mid-fold, a manual
record widening the window for one candidate but not its neighbour, an
id-less repeat arriving while a settled reissue is in flight. Seeded, so
any disagreement is reproducible by its seed alone.

THE RESOLVE ORACLE IS NO LONGER VERBATIM, and the two amendments are the
whole of the difference. Provider-id continuity changed what the matcher is
meant to decide, so an oracle frozen at the old behaviour would have held
the index to answers that are now known to be wrong (one payment held twice,
a new pending payment replacing a settled one). The amendments are written
here as naive scans, independently of the index that implements them:

  1. Tier 1 finds a row by any id its source has EVER called it, not only
     the id the row carries now.
  2. For a source in SETTLEMENT_KEEPS_ID, a row that source has called by a
     different id is a different payment, at every later tier.

What a row has been called is memory the rows themselves do not hold, so
there are two folds to agree with: one where that memory is only each row's
current id (an index rebuilt from rows alone, which is all `resolve` has
when handed a list), and one where it is kept across the batch (the
persistent index, which is what ingestion runs).
"""

from __future__ import annotations

import random
from dataclasses import replace
from datetime import date, timedelta

from obdi.matching import (
    FUZZY_WINDOW_DAYS,
    MANUAL_WINDOW_DAYS,
    SETTLEMENT_KEEPS_ID,
    MatchResult,
    belongs_to_established_series,
    could_be_one_payment,
    pair_internal_transfers,
    resolve,
    supersede,
)
from obdi.models import MatchTier, SourceTier, Transaction, TransactionStatus

#: Every (source, provider id) each row has been called, by entity id.
Called = dict[str, set[tuple[str, str]]]


#: The id each row has answered to within the batch, by entity and source.
Claimed = dict[str, dict[str, str]]


def _resolve_reference(
    incoming: Transaction,
    existing: list[Transaction],
    called: Called | None = None,
    claimed: Claimed | None = None,
) -> MatchResult:
    """The pre-index resolve() as a linear scan, with the amendments.

    `called` is the memory of what each row has been called.
    Left out, a row is known only by the id it carries now.
    `claimed` is which id each row has answered to in this batch; a row that
    answered to one of a source's ids cannot answer to another of them.
    """

    def names_of(candidate: Transaction) -> set[tuple[str, str]]:
        if called is not None:
            return called.get(candidate.entity_id, set())
        if candidate.source_id:
            return {(candidate.source, candidate.source_id)}
        return set()

    def taken(candidate: Transaction) -> bool:
        if claimed is None or not incoming.source_id:
            return False
        held = claimed.get(candidate.entity_id, {}).get(incoming.source)
        return held is not None and held != incoming.source_id

    def one_payment(candidate: Transaction, *, same_content: bool) -> bool:
        if taken(candidate):
            return False
        if incoming.source in SETTLEMENT_KEEPS_ID and incoming.source_id:
            other_ids = {
                source_id
                for source, source_id in names_of(candidate)
                if source == incoming.source
            } - {incoming.source_id}
            if other_ids:
                return False
        return could_be_one_payment(incoming, candidate, same_content=same_content)

    same_account = [t for t in existing if t.account_id == incoming.account_id]

    if incoming.source_id:
        wanted = (incoming.source, incoming.source_id)
        # A row carrying the id now comes before one that only used to.
        carrying = [
            c for c in same_account if c.source_id and (c.source, c.source_id) == wanted
        ]
        remembering = [
            c for c in same_account if wanted in names_of(c) and c not in carrying
        ]
        for candidate in [*carrying, *remembering]:
            if not taken(candidate):
                return MatchResult(MatchTier.SOURCE_ID, candidate)

    if incoming.content_key:
        for candidate in same_account:
            if candidate.content_key == incoming.content_key and one_payment(
                candidate, same_content=True
            ):
                return MatchResult(MatchTier.CONTENT_KEY, candidate)

    def window_for(candidate: Transaction) -> timedelta:
        if SourceTier.MANUAL in (incoming.tier, candidate.tier):
            return timedelta(days=MANUAL_WINDOW_DAYS)
        return timedelta(days=FUZZY_WINDOW_DAYS)

    similar = [
        t
        for t in same_account
        if t.amount_minor == incoming.amount_minor
        and abs(t.value_date - incoming.value_date) <= window_for(t)
    ]
    near = [
        t
        for t in similar
        if one_payment(t, same_content=t.content_key == incoming.content_key)
    ]
    rejected = tuple(t for t in similar if t not in near)

    if not near:
        return MatchResult(
            MatchTier.UNRESOLVED,
            None,
            near_misses=rejected,
            recurring=belongs_to_established_series(incoming, same_account),
        )

    near.sort(key=lambda t: abs(t.value_date - incoming.value_date))
    return MatchResult(MatchTier.FUZZY, near[0])


def _pair_reference(
    transactions: list[Transaction], *, window_days: int = 1
) -> list[Transaction]:
    """The pre-rewrite pair_internal_transfers(), preserved verbatim."""
    items = sorted(transactions, key=lambda t: (t.value_date, t.account_id))
    window = timedelta(days=window_days)
    paired: set[int] = set()
    result = list(items)

    for i, debit in enumerate(items):
        if i in paired or debit.amount_minor >= 0:
            continue
        for j, credit in enumerate(items):
            if j in paired or j == i or credit.amount_minor <= 0:
                continue
            if credit.account_id == debit.account_id:
                continue
            if credit.amount_minor != -debit.amount_minor:
                continue
            if abs(credit.value_date - debit.value_date) > window:
                continue
            paired.update({i, j})
            result[i] = replace(debit, is_internal_transfer=True)
            result[j] = replace(credit, is_internal_transfer=True)
            break

    return result


_SOURCES = (
    ("starling", SourceTier.AUTHORITATIVE, True),
    ("truelayer", SourceTier.AUTHORITATIVE, True),
    ("qif", SourceTier.SYNTHETIC, False),
    ("manual", SourceTier.MANUAL, False),
)


def _corpus(seed: int, count: int) -> list[Transaction]:
    """A batch dense with deliberate collisions.

    Small pools force the interesting cases: reused amounts inside the
    fuzzy window, repeated descriptions, occasional reuse of an earlier
    source_id (the tier-1 path), settlement reissues (same content, new
    id), and id-less repeats that only occurrence numbering keeps apart.
    """
    rng = random.Random(seed)  # noqa: S311 - seeded determinism is the point
    base = date(2026, 3, 1)
    amounts = [-1250, -1250, -560, -560, -3200, 4400, 990, -990]
    descriptions = ["COFFEE", "GYM", "RENT", "SALARY", "TFR SAVINGS"]
    issued_ids: list[tuple[str, str]] = []
    out: list[Transaction] = []
    for n in range(count):
        source, tier, has_id = rng.choice(_SOURCES)
        account = rng.choice(["a1", "a2"])
        amount = rng.choice(amounts)
        day = base + timedelta(days=rng.randrange(0, 21))
        description = rng.choice(descriptions)
        status = (
            TransactionStatus.PENDING
            if has_id and rng.random() < 0.2
            else TransactionStatus.BOOKED
        )
        if has_id:
            if issued_ids and rng.random() < 0.25:
                source, source_id = rng.choice(issued_ids)
                tier = SourceTier.AUTHORITATIVE
            else:
                source_id = f"{source}-{seed}-{n}"
                issued_ids.append((source, source_id))
        else:
            source_id = None
        content_key = f"{account}|{amount}|{day.isoformat()}|{description}"
        out.append(
            Transaction(
                entity_id=f"e-{seed}-{n}",
                account_id=account,
                amount_minor=amount,
                currency="GBP",
                description=description,
                value_date=day,
                booking_date=day,
                source=source,
                source_id=source_id,
                content_key=content_key,
                tier=tier,
                status=status,
            )
        )
    return out


def _outcome(result: MatchResult) -> tuple:
    return (
        result.tier,
        result.existing.entity_id if result.existing else None,
        tuple(t.entity_id for t in result.near_misses),
        result.recurring,
    )


def _fold(incoming: list[Transaction], resolver, structure) -> list[tuple]:
    """Drive one batch the way reconcile_batch does, recording every outcome.

    Mirrors _reconcile's two mutations exactly: a match REPLACES the
    candidate in place (the pre-merge row must not be claimable twice), a
    miss appends. Occurrence numbering mirrors reconcile_batch.
    """
    seen: dict[tuple[str, str], int] = {}
    outcomes = []
    for transaction in incoming:
        key = (transaction.account_id, transaction.content_key)
        numbered = replace(transaction, occurrence=seen.get(key, 0))
        seen[key] = seen.get(key, 0) + 1

        result = resolver(numbered, structure)
        outcomes.append(_outcome(result))
        if result.existing is None:
            structure.append(numbered)
        else:
            merged = supersede(result.existing, numbered)
            for index, candidate in enumerate(structure):
                if candidate.entity_id == merged.entity_id:
                    structure[index] = merged
                    break
    return outcomes


class TestResolveMatchesItsReferenceOnEveryFold:
    def test_RandomisedFolds_AgreeWithTheReferenceInFullDetail(self):
        for seed in range(30):
            batch = _corpus(seed, 120)
            reference = _fold(list(batch), _resolve_reference, [])
            actual = _fold(list(batch), resolve, [])
            assert actual == reference, f"divergence at seed {seed}"

    def test_TwoAccountsInterleaved_DoNotContaminateEachOther(self):
        """Same amounts and dates on two accounts must resolve separately."""
        for seed in (101, 202, 303):
            batch = _corpus(seed, 160)
            reference = _fold(list(batch), _resolve_reference, [])
            actual = _fold(list(batch), resolve, [])
            assert actual == reference, f"divergence at seed {seed}"


class TestPairingMatchesItsReferenceOnEveryCorpus:
    def _transfer_corpus(self, seed: int) -> list[Transaction]:
        """Dense in the shapes pairing must respect.

        Opposite-amount pairs across accounts on the same and adjacent
        days, repeated standing orders of one value (consumed-once must
        stop chain-matching), same-account decoys, and window-edge pairs
        exactly one day apart.
        """
        rng = random.Random(seed)  # noqa: S311 - seeded determinism is the point
        base = date(2026, 5, 1)
        out = []
        accounts = ["a1", "a2", "a3"]
        for n in range(90):
            amount = rng.choice([-5000, 5000, -5000, 5000, -120, 120, -777])
            out.append(
                Transaction(
                    entity_id=f"p-{seed}-{n}",
                    account_id=rng.choice(accounts),
                    amount_minor=amount,
                    currency="GBP",
                    description="TRANSFER",
                    value_date=base + timedelta(days=rng.randrange(0, 10)),
                    booking_date=base,
                    source="starling",
                    source_id=f"p-{seed}-{n}",
                    content_key=f"ck-{seed}-{n}",
                    tier=SourceTier.AUTHORITATIVE,
                    status=TransactionStatus.BOOKED,
                )
            )
        return out

    def _key(self, transactions: list[Transaction]) -> list[tuple[str, bool]]:
        return [(t.entity_id, t.is_internal_transfer) for t in transactions]

    def test_RandomisedCorpora_ProduceIdenticalPairings(self):
        for seed in range(25):
            corpus = self._transfer_corpus(seed)
            reference = _pair_reference(list(corpus))
            actual = pair_internal_transfers(list(corpus))
            assert self._key(actual) == self._key(reference), f"seed {seed}"

    def test_WiderWindows_ProduceIdenticalPairings(self):
        for seed in (7, 13):
            corpus = self._transfer_corpus(seed)
            reference = _pair_reference(list(corpus), window_days=3)
            actual = pair_internal_transfers(list(corpus), window_days=3)
            assert self._key(actual) == self._key(reference), f"seed {seed}"


def _fold_indexed(incoming: list[Transaction]) -> list[tuple]:
    """The fold as reconcile_batch now actually runs it: one persistent
    CandidateIndex maintained by append/replace across the whole batch.

    Distinct from _fold's rebuild-per-call path on purpose: this is what
    exercises the index's maintenance - stale keys after supersession,
    arrival slots across replacement - rather than its construction.
    """
    from obdi.matching import CandidateIndex

    seen: dict[tuple[str, str], int] = {}
    index = CandidateIndex()
    outcomes = []
    for transaction in incoming:
        key = (transaction.account_id, transaction.content_key)
        numbered = replace(transaction, occurrence=seen.get(key, 0))
        seen[key] = seen.get(key, 0) + 1

        result = resolve(numbered, index)
        outcomes.append(_outcome(result))
        if result.existing is None:
            index.append(numbered)
            taker = numbered.entity_id
        else:
            index.replace(supersede(result.existing, numbered))
            taker = result.existing.entity_id
        # The whole corpus is one batch, so every claim stands to the end.
        index.claim(taker, numbered.source, numbered.source_id)
    return outcomes


def _fold_remembering(incoming: list[Transaction]) -> list[tuple]:
    """The reference fold, keeping what each row has been called.

    The counterpart of `_fold_indexed`: a plain list of rows, and beside it
    every (source, id) each row has arrived under, added to and never
    removed.
    """
    seen: dict[tuple[str, str], int] = {}
    rows: list[Transaction] = []
    called: Called = {}
    claimed: Claimed = {}
    outcomes = []

    def note(entity_id: str, sighting: Transaction) -> None:
        if sighting.source_id:
            called.setdefault(entity_id, set()).add(
                (sighting.source, sighting.source_id)
            )
            claimed.setdefault(entity_id, {}).setdefault(
                sighting.source, sighting.source_id
            )

    for transaction in incoming:
        key = (transaction.account_id, transaction.content_key)
        numbered = replace(transaction, occurrence=seen.get(key, 0))
        seen[key] = seen.get(key, 0) + 1

        result = _resolve_reference(numbered, rows, called, claimed)
        outcomes.append(_outcome(result))
        if result.existing is None:
            rows.append(numbered)
            note(numbered.entity_id, numbered)
        else:
            merged = supersede(result.existing, numbered)
            for position, candidate in enumerate(rows):
                if candidate.entity_id == merged.entity_id:
                    rows[position] = merged
                    break
            note(merged.entity_id, numbered)
    return outcomes


class TestTheMaintainedIndexMatchesTheReferenceFold:
    def test_APersistentIndexAcrossTheWholeBatch_AgreesWithTheReference(self):
        for seed in range(30):
            batch = _corpus(seed, 120)
            reference = _fold_remembering(list(batch))
            actual = _fold_indexed(list(batch))
            assert actual == reference, f"divergence at seed {seed}"

    def test_TheCorpus_ReachesTheCasesWhereMemoryDecides(self):
        """Without this the test above could pass with the memory unused.

        A fold that remembers and one that knows only current ids must
        part company somewhere in the corpus, or the corpus never offers an
        id a row has since stopped carrying.
        """
        parted = [
            seed
            for seed in range(30)
            if _fold_remembering(_corpus(seed, 120))
            != _fold(_corpus(seed, 120), _resolve_reference, [])
        ]
        assert parted, "no seed distinguishes remembered ids from current ones"


class TestIndexMaintenanceInvariants:
    """The four hazards of replacing scans with an index, each pinned.

    An index bug here would not crash - it would quietly match against a
    rendering the store no longer holds, or stop matching one it does,
    and the only symptom would be money counted twice or once too few.
    """

    def _transaction(self, entity, account="a1", source="starling",
                     source_id=None, content_key="ck-1", amount=-500,
                     day=date(2026, 3, 5),
                     status=TransactionStatus.BOOKED):
        return Transaction(
            entity_id=entity, account_id=account, amount_minor=amount,
            currency="GBP", description="SHOP", value_date=day,
            booking_date=day, source=source, source_id=source_id,
            content_key=content_key, tier=SourceTier.AUTHORITATIVE,
            status=status,
        )

    def test_Supersession_WhenIdentityChanges_TheOldIdStillFindsTheRow(self):
        """A pending that settles under a new id is still the row its source
        called by the old one.

        This asserted the opposite until provider-id continuity: the row no
        longer carries the old id, so the old id found nothing. That
        forgetting is how one payment came to be held twice - its source
        reported it again under the id it had always used, found no row,
        and made another.
        """
        from obdi.matching import CandidateIndex

        index = CandidateIndex()
        pending = self._transaction("e1", source="truelayer", source_id="old-id",
                                    content_key="ck-old",
                                    status=TransactionStatus.PENDING)
        index.append(pending)
        settled = replace(pending, source_id="new-id", content_key="ck-new",
                          status=TransactionStatus.BOOKED)
        index.replace(settled)

        probe_old = self._transaction("probe", source="truelayer",
                                      source_id="old-id", content_key="ck-old")
        result = resolve(probe_old, index)
        assert result.tier is MatchTier.SOURCE_ID
        assert result.existing is not None
        assert result.existing.entity_id == "e1"

        probe_new = self._transaction("probe2", source="truelayer",
                                      source_id="new-id")
        assert resolve(probe_new, index).tier is MatchTier.SOURCE_ID

    def test_Supersession_WhenContentChanges_TheOldContentKeyStopsMatching(self):
        """Content is what a row says now, and unlike an id it is not
        remembered: a record with no id and the row's former content must
        not reach it through the content tier."""
        from obdi.matching import CandidateIndex

        index = CandidateIndex()
        pending = self._transaction("e1", source="truelayer", source_id="old-id",
                                    content_key="ck-old",
                                    status=TransactionStatus.PENDING)
        index.append(pending)
        index.replace(replace(pending, source_id="new-id", content_key="ck-new",
                              status=TransactionStatus.BOOKED))

        probe = self._transaction("probe", source="qif", content_key="ck-old")
        assert resolve(probe, index).tier is not MatchTier.CONTENT_KEY

    def test_Replacement_KeepsTheArrivalSlot_SoFirstMatchStaysFirst(self):
        """Tier 2 returns the FIRST arrival-order match. Replacing the
        first candidate must not demote it behind a later twin."""
        from obdi.matching import CandidateIndex
        from obdi.matching import supersede as _supersede

        index = CandidateIndex()
        first = self._transaction("e-first", source="qif", content_key="ck-x",
                                  day=date(2026, 3, 1))
        second = self._transaction("e-second", source="qif", content_key="ck-x",
                                   day=date(2026, 3, 1))
        index.append(replace(first, occurrence=0))
        index.append(replace(second, occurrence=1))

        # Supersede the first (occurrence 0) with a re-import of itself.
        incoming = replace(first, entity_id="ignored", occurrence=0)
        merged = _supersede(replace(first, occurrence=0), incoming)
        index.replace(merged)

        probe = self._transaction("probe", source="qif", content_key="ck-x")
        result = resolve(replace(probe, occurrence=0), index)
        assert result.tier is MatchTier.CONTENT_KEY
        assert result.existing is not None
        assert result.existing.entity_id == "e-first"

    def test_AmountBucket_ServesTheFuzzyTierAndTheSeriesCheck(self):
        """Tier 3 and the recurring-series check both consider only exact
        -amount candidates; the bucket must be a complete set for both."""
        from obdi.matching import CandidateIndex

        index = CandidateIndex()
        for n, day in enumerate([date(2026, 3, 1), date(2026, 3, 8)]):
            index.append(self._transaction(
                f"e{n}", source="starling", source_id=f"s{n}",
                content_key=f"ck{n}", amount=-2500, day=day))

        arriving = self._transaction("probe", source="truelayer",
                                     source_id="t1", content_key="ck-t",
                                     amount=-2500, day=date(2026, 3, 15))
        result = resolve(arriving, index)
        # Cross-source, exact amount, in window: a fuzzy match on the
        # nearest candidate, exactly as the scan produced.
        assert result.tier is MatchTier.FUZZY
        assert result.existing is not None
        assert result.existing.entity_id == "e1"

    def test_CouldBeOnePayment_StillFiltersInsideTheBuckets(self):
        """The index accelerates lookup; it must not bypass the source
        rules. Two same-source records with identical content and
        DIFFERENT occurrences are two payments, bucket or no bucket."""
        from obdi.matching import CandidateIndex

        index = CandidateIndex()
        index.append(replace(
            self._transaction("e0", source="qif", content_key="ck-so"),
            occurrence=0))

        second = replace(
            self._transaction("probe", source="qif", content_key="ck-so"),
            occurrence=1)
        result = resolve(second, index)
        assert result.tier is MatchTier.UNRESOLVED, (
            "a second occurrence must never merge into the first"
        )
