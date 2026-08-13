"""What the pages will SAY, asserted by calling the hooks they read.

Until 2026-08-13 every hook the interface reads was defined inside the function
that starts the server, so none could be called: a test could only reimplement
one and assert against its own copy, which tests the copy. That is how a wrong
denominator survived on the account page - its shape panel counted each merged
payload once per source that had seen it - until somebody read the rendered page
and noticed.

These are assertions about DATA, not about markup. A page's layout still needs a
browser; the numbers on it do not, and the numbers are where the arithmetic
mistakes live.
"""

from __future__ import annotations

import pytest

from obdi.cli import build_web_config
from obdi.ingest import import_file
from obdi.store import Store
from obdi.synthetic import build_world, write_corpus

SEED = 20260812


@pytest.fixture(scope="module")
def account_covered_by_two_sources(tmp_path_factory):
    """One account described by two pipes, landed through the ordinary door.

    Two sources are the precondition for everything below: with one, a merged
    row and a sighting are the same thing and no denominator can disagree with
    another.
    """
    root = tmp_path_factory.mktemp("page-data")
    world = build_world(seed=SEED, months=6)
    manifest = write_corpus(world, root / "corpus")
    second = next(
        item for item in manifest["deliveries"] if "second door" in item["fault"]
    )
    store_path = root / "store.sqlite3"
    for name in ("synthetic-current.csv", second["name"]):
        with Store(store_path) as store:
            import_file(store, root / "corpus" / name, account_id="synthetic-current")
    return store_path


@pytest.fixture
def configured(account_covered_by_two_sources, monkeypatch, tmp_path):
    """The wiring the pages read, pointed at that store.

    The connection store and account map are required by the builder and are
    given empty scratch paths: these tests are about what the pages compute
    from transactions, and a real connection would put a bank's name in the
    middle of it.
    """
    monkeypatch.setenv("OBDI_CONNECTION_STORE", str(tmp_path / "connections.json"))
    monkeypatch.setenv("OBDI_ACCOUNT_MAP", str(tmp_path / "accounts.json"))
    for variable in ("TRUELAYER_CLIENT_ID", "TRUELAYER_CLIENT_SECRET_FILE"):
        monkeypatch.delenv(variable, raising=False)
    config = build_web_config(account_covered_by_two_sources)
    assert config is not None, "the builder refused a store it should have accepted"
    return config


class TestTheAccountPagesShapePanel:
    def test_ItSummarisesOneItemPerMergedTransaction_NotOnePerSighting(
        self, configured, account_covered_by_two_sources
    ):
        """The denominator bug, pinned where it lived.

        The panel shows the MERGED layer and says so in its own text. Counting
        once per sighting inflated every field's count by the number of pipes
        that had seen the row, and made an item carry one source's NAME beside
        another source's verbatim record.
        """
        assert configured.account_shape is not None
        shape = configured.account_shape("synthetic-current")
        assert shape is not None

        with Store(account_covered_by_two_sources) as store:
            merged = [
                row for row in store.all_transactions()
                if row.account_id == "synthetic-current"
            ]
            sightings = [
                row for row in store.transactions_by_sighting()
                if row.account_id == "synthetic-current"
            ]

        assert len(sightings) > len(merged), (
            "only one pipe landed, so no denominator can disagree with another "
            f"and this asserts nothing (seed {SEED})"
        )

        # "items" is the number the page prints as "N item(s)", so this is the
        # figure a reader actually sees rather than an internal count.
        summary = shape["summary"]
        assert summary["items"] == len(merged), (
            f"the panel summarised {summary['items']} items for {len(merged)} "
            f"merged transactions - it is counting sightings again (seed {SEED})"
        )
        assert summary["items"] != len(sightings), (
            f"the panel is summarising one item per sighting, so every field's "
            f"count is inflated by the number of pipes that saw the row "
            f"(seed {SEED})"
        )

    def test_ItStillNamesEverySourceThatContributed(self, configured):
        """The other denominator, which must NOT become the merged one.

        A merged row keeps a single source after matching, so counting sources
        from merged rows would report one pipe for an account two pipes cover.
        That list is the reason the sighting view is still read here.
        """
        shape = configured.account_shape("synthetic-current")

        assert sorted(shape["sources"]) == ["monzo-csv", "starling-csv"], (
            f"the page names {shape['sources']} as the sources for an account "
            f"two pipes cover (seed {SEED})"
        )

    def test_AnAccountNothingHasLandedFor_IsAbsentRatherThanEmpty(self, configured):
        """A page that renders an empty shell for an account nobody holds looks
        like data loss. Saying nothing is held is a different answer."""
        assert configured.account_shape("no-such-account") is None


class TestTheBuilderRefusesWhatItCannotServe:
    def test_WithoutAConnectionStore_ItRefusesRatherThanServingHalfAPage(
        self, monkeypatch, tmp_path
    ):
        """The failure that used to be an exit code inside the serve function,
        now observable: the builder answers None and says which part is
        missing, and the caller turns that into the exit code."""
        monkeypatch.delenv("OBDI_CONNECTION_STORE", raising=False)
        monkeypatch.setenv("OBDI_ACCOUNT_MAP", str(tmp_path / "accounts.json"))

        assert build_web_config(tmp_path / "store.sqlite3") is None


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
