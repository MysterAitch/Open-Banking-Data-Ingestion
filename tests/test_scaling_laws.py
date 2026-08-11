"""Superlinear growth is a class of defect with a testable signature.

Three superlinear paths reached production before being caught (the
setdefault re-read, the O(n^2) transfer scan, the sibling fan-out that
made a 40s homepage): each was linear at test scale and explosive at
corpus scale. This suite measures the hot read paths at two sizes a
factor of FOUR apart and asserts the growth ratio stays near-linear -
the signature test that catches the next one before the corpus does.

Ratios, not absolute times: absolute bounds break on slow runners,
ratios cancel machine speed. Tiny timings are noise, so anything under
the floor passes outright.
"""

import time
from datetime import UTC, date, datetime

from obdi.identity import artefact_digest
from obdi.matching import pair_transfer_entities
from obdi.models import RawArtefact, Transaction
from obdi.store import Store

SMALL, LARGE = 1500, 6000  # factor of 4
MAX_RATIO = 8.0  # linear with headroom for noise and n log n
FLOOR_SECONDS = 0.02  # below this, timing is noise - pass outright


def _build(tmp_path, n: int) -> Store:
    store = Store(tmp_path / f"scale-{n}.sqlite3")
    payload = b'{"feedItems": []}'
    shared = artefact_digest(payload)
    # A sibling-heavy shared digest, scaled with n - the shape that
    # turned the witness map quadratic.
    for i in range(n // 10):
        store.land_artefact(
            RawArtefact(
                source="starling-feed", account_ref="acct-0",
                fetched_at=datetime(2026, 8, 1, tzinfo=UTC),
                media_type="application/json", digest=shared, payload=payload,
                origin=f"https://x?n={i}", connection_id="starling-api",
            )
        )
    store.begin_batch()
    for i in range(n):
        t = Transaction(
            account_id=f"acct-{i % 6}",
            amount_minor=(-1 if i % 2 else 1) * (100 + i % 900),
            value_date=date(2026, 1 + i % 12, 1 + i % 28),
            booking_date=date(2026, 1 + i % 12, 1 + i % 28),
            description=f"txn {i}", source="starling-feed",
            entity_id=f"e{i:07d}", content_key=f"ck{i}",
            artefact_digest=shared,
        )
        store.upsert_transaction(t, match_tier="exact")
        store.record_source(t)
    store.flush_batch()
    store.connection.commit()
    return store


def _best_of(fn, repeats: int = 3) -> float:
    best = float("inf")
    for _ in range(repeats):
        began = time.perf_counter()
        fn()
        best = min(best, time.perf_counter() - began)
    return best


def _assert_near_linear(name: str, small_s: float, large_s: float) -> None:
    # The floor guards BOTH measurements, not only the larger. A ratio is
    # only as trustworthy as its denominator, and a sub-millisecond small
    # reading is noise: on a loaded machine it shrinks while the large
    # reading grows, and the quotient of the two accuses a linear function
    # of being quadratic. This gate has cried wolf twice that way, both
    # times while something else saturated the CPU, and a gate that cries
    # wolf gets switched off - which costs more than the regression it
    # was meant to catch.
    if large_s < FLOOR_SECONDS or small_s < FLOOR_SECONDS:
        return
    ratio = large_s / max(small_s, 1e-9)
    assert ratio < MAX_RATIO, (
        f"{name} grew {ratio:.1f}x for 4x the data "
        f"({small_s:.4f}s -> {large_s:.4f}s) - superlinear signature"
    )


class TestHotReadPaths_GrowNearLinearly:
    def test_StoreReadPaths_AtFourTimesTheData_StayNearLinear(self, tmp_path):
        with _build(tmp_path, SMALL) as small, _build(tmp_path, LARGE) as large:
            probes = {
                "all_transactions": lambda s: s.all_transactions(),
                "transactions_by_sighting": lambda s: s.transactions_by_sighting(),
                "source_connections": lambda s: s.source_connections(),
                "source_breakdown": lambda s: s.source_breakdown("acct-0"),
            }
            for name, probe in probes.items():
                small_s = _best_of(lambda p=probe: p(small))
                large_s = _best_of(lambda p=probe: p(large))
                _assert_near_linear(name, small_s, large_s)

    def test_TransferPairing_AtFourTimesTheData_StaysNearLinear(self, tmp_path):
        with _build(tmp_path, SMALL) as small, _build(tmp_path, LARGE) as large:
            small_rows = small.all_transactions()
            large_rows = large.all_transactions()
            small_s = _best_of(lambda: pair_transfer_entities(small_rows))
            large_s = _best_of(lambda: pair_transfer_entities(large_rows))
            _assert_near_linear("pair_transfer_entities", small_s, large_s)


class TestSourceBreakdown_UnderSiblingHeavyDigests:
    """The fourth bite of the digest-fan disease, found on the live account
    page: 41s for the account with ~9.6k sightings against 0.7s for one
    with 1.7k. source_breakdown ran two correlated subqueries against
    raw_artefacts BY DIGEST for every sighting row - and rolling-epoch
    feed digests carry hundreds of sibling rows, so the cost was
    sightings x siblings. The general probe missed it because its sibling
    density was too thin; this fixture is deliberately sibling-dense.
    """

    def test_SourceBreakdown_WithDenseSiblings_StaysNearLinear(self, tmp_path):
        def build(n: int) -> Store:
            store = Store(tmp_path / f"dense-{n}.sqlite3")
            payload = b'{"feedItems": []}'
            shared = artefact_digest(payload)
            # Siblings scale WITH the data, as they do live: every cycle
            # lands another byte-identical empty feed under a new origin.
            for i in range(n):
                store.land_artefact(
                    RawArtefact(
                        source="starling-feed", account_ref="acct-dense",
                        fetched_at=datetime(2026, 8, 1, tzinfo=UTC),
                        media_type="application/json", digest=shared,
                        payload=payload, origin=f"https://x?dense={i}",
                        connection_id="starling-api",
                    )
                )
            store.begin_batch()
            for i in range(n):
                t = Transaction(
                    account_id="acct-dense",
                    amount_minor=-100 - i,
                    value_date=date(2026, 1 + i % 12, 1 + i % 28),
                    booking_date=date(2026, 1 + i % 12, 1 + i % 28),
                    description=f"txn {i}", source="starling-feed",
                    entity_id=f"d{i:07d}", content_key=f"dk{i}",
                    artefact_digest=shared,
                )
                store.upsert_transaction(t, match_tier="exact")
                store.record_source(t)
            store.flush_batch()
            store.connection.commit()
            return store

        with build(SMALL) as small, build(LARGE) as large:
            small_s = _best_of(lambda: small.source_breakdown("acct-dense"))
            large_s = _best_of(lambda: large.source_breakdown("acct-dense"))

        _assert_near_linear("source_breakdown dense-siblings", small_s, large_s)
