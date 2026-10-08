"""The Overview hook as the real wiring builds it, over the generated corpus.

The data rules are in test_overview.py with injected checks; this proves the
wiring hands them the right things: the alert's own findings, the store's
accounts, and a held result that says how old it is.
"""

from __future__ import annotations

import pytest

from landing import import_file
from obdi.cli import build_web_config
from obdi.ingest.store import Store
from obdi.ingest.synthetic import build_world, write_corpus

SEED = 20260812


@pytest.fixture(scope="module")
def corpus_store(tmp_path_factory):
    root = tmp_path_factory.mktemp("overview-hook")
    world = build_world(seed=SEED, months=6)
    write_corpus(world, root / "corpus")
    store_path = root / "store.sqlite3"
    with Store(store_path) as store:
        import_file(
            store, root / "corpus" / "synthetic-current.csv", account_id="synthetic-current"
        )
    return store_path


@pytest.fixture
def configured(corpus_store, monkeypatch, tmp_path):
    monkeypatch.setenv("OBDI_CONNECTION_STORE", str(tmp_path / "connections.json"))
    monkeypatch.setenv("OBDI_ACCOUNT_MAP", str(tmp_path / "accounts.json"))
    for variable in ("TRUELAYER_CLIENT_ID", "TRUELAYER_CLIENT_SECRET_FILE", "ACTUAL_SYNC_ID"):
        monkeypatch.delenv(variable, raising=False)
    config = build_web_config(corpus_store)
    assert config is not None and config.overview is not None
    return config


class TestTheWiredOverview:
    def test_Overview_OverTheCorpus_HoldsEveryStoredAccountExactlyOnce(
        self, configured, corpus_store
    ):
        with Store(corpus_store) as store:
            held = {t.account_id for t in store.all_transactions()}

        overview = configured.overview(False)

        assert sorted(a.ref for a in overview.accounts) == sorted(held)
        assert overview.checks_run == overview.checks_total

    def test_Overview_WhenNoSourceIsScheduled_SaysFileOnlyAndNeverAsksActualQuestions(
        self, configured
    ):
        overview = configured.overview(False)

        assert {a.state for a in overview.accounts} == {"file-only"}
        assert {a.bound for a in overview.accounts} == {None}

    def test_Overview_AskedTwice_ReusesTheFirstAndKeepsItsStamp(self, configured):
        first = configured.overview(False)
        second = configured.overview(False)

        assert second is first

    def test_Overview_AskedForFresh_IsAssembledAgainWithALaterStamp(self, configured):
        first = configured.overview(False)
        again = configured.overview(True)

        assert again is not first
        assert again.generated_at >= first.generated_at

    def test_Overview_WhenActualIsConfiguredButNothingIsBound_SaysNotBound(
        self, corpus_store, monkeypatch, tmp_path
    ):
        monkeypatch.setenv("OBDI_CONNECTION_STORE", str(tmp_path / "connections.json"))
        monkeypatch.setenv("OBDI_ACCOUNT_MAP", str(tmp_path / "accounts.json"))
        monkeypatch.setenv("ACTUAL_SYNC_ID", "sync-1")
        for variable in ("TRUELAYER_CLIENT_ID", "TRUELAYER_CLIENT_SECRET_FILE"):
            monkeypatch.delenv(variable, raising=False)
        config = build_web_config(corpus_store)
        assert config is not None and config.overview is not None

        overview = config.overview(True)

        assert {a.bound for a in overview.accounts} == {False}
