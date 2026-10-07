"""The suite runs against what the tests set up, never against this machine.

WHY THIS EXISTS. `main()` calls `load_dotenv()`, so any test that exercises the
command line loads the developer's real `.env` into `os.environ` - and it stays
there for every test that follows, because dotenv writes to the process
environment and pytest's monkeypatch knows nothing about writes it did not make.

On the machine where this was found that meant OBDI_ACCOUNT_MAP pointing at a
real accounts file, which a schema migration duly read into a temporary store,
and OBDI_DB_PATH pointing at the real financial store. Nothing was damaged: the
tests that matter pass their paths explicitly. But the arrangement means the
suite's behaviour depends on which developer runs it and in what order the
modules happen to execute - CI has no `.env` at all, so it was already running a
different suite from the one run locally, and any test that ever falls back to a
configured default would find a real path rather than a temporary one.

The variables are cleared before EVERY test rather than once per session,
because a single test invoking the command line re-loads them mid-run. Cheap:
a dictionary lookup per name.

Not a substitute for the wider question of whether configuration should be
loaded at an entry point at all - see the vault decision on this - but it makes
the suite honest today, which is the part that cannot wait.
"""

from __future__ import annotations

import fnmatch
import functools
import os
from collections.abc import Iterator
from pathlib import Path

import pytest

#: WHICH TESTS A CHANGE RUNS. A test file belongs to the LAYER of the module it tests (`ingest`,
#: `verify`, `analysis`, `pages`, `export`), and is marked with exactly one. A GUARD is a test that
#: enforces a house rule across the tree rather than testing one feature; it is marked `guards`
#: as well as with its layer. `slow` marks the tests that build the large stores. A builder runs
#: `-m "<layer> or guards"` for the layer touched and the CI gate runs everything; docs/BUILDING.md
#: ("Which tests to run") states the routine. The markers are applied here, by file, so that 400
#: test files are not each edited; `pytest_collection_modifyitems` below refuses a test file this
#: table does not place, so a new file must be added here. `--strict-markers` is set in
#: pyproject.toml, so a misspelt marker is an error rather than a test nobody selects.
#:
#: Entries are file names without `test_` and `.py`, and may use `*`. The FIRST layer whose
#: patterns match wins, so an exception is listed in a layer that comes before the general
#: pattern it departs from.
LAYERS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "pages",
        (
            "account_about_*", "account_names", "account_names_on_every_page",
            "account_next_statement_due", "account_page*", "account_pickers",
            "accounts_page_*", "accounts_phone_layout", "actual_audit_wording",
            "actual_history_page", "actual_not_configured", "actual_page*",
            "actual_sync_surface", "agreements_page", "answer_pages", "archive_account",
            "archived_strip", "artefact_page_after_a_move", "attempts_page", "balance_chart_*",
            "breakdown_cost", "bring_in_links", "bring_in_page", "bring_in_scale", "checks_index",
            "connections_page", "connections_position_scale", "coverage_page*",
            "coverage_timeline_collapsed*", "coverage_timeline_page",
            "coverage_timeline_phone_layout", "dangling_annotations_surface", "date_window*",
            "deep_links", "destinations", "dev_harness", "entities_page",
            "entities_phone_layout", "entities_speed", "every_page_is_reachable",
            "family_pages", "fetch_timeline*", "gaps_cost", "gaps_marks_page",
            "good_results_are_said_quietly", "handler_faults", "home_*",
            "identifiers_are_set_as_code", "import_masking", "instance_identity",
            "irreplaceable_work", "kept_statements_page", "ledger_opening_page", "ledger_page",
            "ledger_row_folds", "ledger_sighting_lines", "ledger_window*", "london_clock",
            "masking", "measurement_wording", "movement_pages", "no_row_items", "overview",
            "overview_*", "page_*", "period_reconciliation_scale", "phone_layout", "plural",
            "position_chart_*", "position_page", "position_window_*", "proof_rail",
            "protection_page", "range_length_on_pages", "recurring_page",
            "recurring_phone_layout", "report_page_wording", "review_flags_balance_lines",
            "review_flags_page", "review_page", "same_money_outcomes", "scheduler_*",
            "section_pages", "standing_pages", "statement_extraction_pages",
            "statement_extraction_scale", "statement_listing_rule_pages",
            "statement_reader_findings", "statement_shape_page", "statements_move",
            "statements_page_scale", "stylesheet", "stylesheet_as_served",
            "times_on_the_owners_clock", "timings", "today_*", "typed_phone_layout",
            "upload_script", "values_sitting", "verification_phone_layout", "web", "web_*",
            "window_control", "disclosure_gate", "action_names",
            "get_routes_hold_no_stored_values", "ledger_speed", "navigation",
        ),
    ),
    (
        "export",
        (
            "actual_*", "align_actual", "balance_only_disregard_reaches_the_push", "cleared_push",
            "empty_actual", "opening_payload", "sync_marker", "transfer_categorisation",
            "transfer_pairs_payload", "transfer_skip_reasons",
        ),
    ),
    (
        "analysis",
        (
            "recurring", "recurring_series", "recurring_speed", "entities_grouping",
            "entities_owner", "entities_rules", "entities_teaching", "entity_recurring",
        ),
    ),
    (
        "verify",
        (
            "agreement*", "aggregator_day_anchors", "asked_coverage", "balance_anchors",
            "balance_meaning", "balance_only_accounts", "balance_reconciliation", "bank_balances",
            "card_row_fault_measured", "coverage", "coverage_timeline*", "disregard*",
            "export_cuts", "export_dating", "family_*", "fault_structure", "fetch_gaps*",
            "fetch_marks*", "ledger", "ledger_running_balance", "movement_*",
            "period_reconciliation", "position", "position_trust", "protection",
            "removed_balances", "row_parting", "space_listing_anchors", "standing_*",
            "statement_checks_held", "statement_listing*", "statement_membership",
            "statement_opening_*", "statement_period_holes", "statement_span*", "todo", "trust",
            "valuations", "verdict_words",
        ),
    ),
    (
        "ingest",
        (
            "absorbed_rows", "account_observations", "accounts", "alert_wiring", "alerts",
            "annotations", "arrival_orders_vary", "artefact_origins", "artefact_shape_link",
            "assignment_doubt", "attempts", "backfill", "backup", "bring_in*", "buildinfo",
            "callback", "*_statement", "credit_union_*", "cash_transfers",
            "cash_withdrawal_measure", "changes_probe", "claimed_window", "classification", "cli",
            "clock_travel_probe", "closed_space_*",
            "connection_attribution", "connection_durability", "connections",
            "consecutive_days_nothing_joined", "cross_source", "cross_source_reissue",
            "currency", "date_ambiguity", "declared_accounts", "declined_items", "defer",
            "deterministic_ids", "doctor", "doctor_rebuild_check", "duplication",
            "empty_rebuild_alarm", "entity_store", "equal_payments_close_together",
            "exact_rule_*", "explain",
            "export_declared", "export_raw", "export_rows_dated_after_the_feed",
            "export_verification", "feed_*", "fetch_now", "file_api_dedup", "fingerprint",
            "fixture_write_doors", "historical_spaces", "history_boundary_survival", "id_tier",
            "identifiers", "identity", "identity_health", "identity_health_speed",
            "import_direction",
            "instrumentation", "internal_leg_pairing", "jsontypes", "known_accounts",
            "large_store_*", "leases", "ledger_dates_and_joins", "logs", "lookalike_recipient",
            "matching", "matching_equivalence", "migrations_are_reachable", "money",
            "namespaces", "no_row_status_measurement", "occurrence_allocation", "orphan_classes",
            "orphaned_entity_rows", "own_account_explanations", "parser_*", "parsers", "pdf_*",
            "pending_*", "probing", "propagation", "provenance", "provenance_registry",
            "provider_*", "prune_clear", "pull", "pull_*", "qif", "rawview", "rebind_survival",
            "rebuild*", "refile_leaves_nothing_behind", "repeated_items_in_one_artefact",
            "replay", "replay_order", "restore", "reversed_and_unpaired_legs", "review_*",
            "rolling_cursor", "round_up_*", "round_ups", "rule_orphans", "same_money_fold*",
            "scaling_laws", "scheduled_*", "schema_migrations", "schema_version_gate",
            "scratch_path_sink", "secret_rotation", "secrets", "serving_over_a_newer_store",
            "settlement_*", "sighting_*", "sign_convention", "source_search", "source_tiers",
            "source_tree",
            "space_*", "spaces", "spaces_finish", "starling", "stated_times_every_source",
            "statement_batch_cost", "statement_columns", "statement_extraction_stored",
            "statement_names", "statement_parser_contract", "statement_reading_cache",
            "statement_section*", "statement_shape", "statement_terms", "statement_upload_skip",
            "store_schema_twenty", "suite_markers", "suite_runs_against_itself", "synthetic_*",
            "tiers", "transfer_split", "truelayer_identity", "typed_transactions",
            "upload_filenames", "worklist_*", "write_batching",
        ),
    ),
)

#: The guards: each enforces a house rule across the tree, or holds a speed budget, rather than
#: testing one feature. Chosen by reading each candidate; a test that merely asserts a page's own
#: wording or layout is a feature test and stays out, or `guards` stops being a short list.
GUARDS: frozenset[str] = frozenset(
    {
        "get_routes_hold_no_stored_values", "navigation", "web_hardening", "entities_speed",
        "page_wording_counts", "page_wording_emphasis", "page_wording_internals",
        "page_wording_plain", "page_wording_times", "page_wording_vocabulary",
        "report_page_wording", "measurement_wording", "ledger_speed", "recurring_speed",
        "identity_health_speed", "scaling_laws", "account_names_on_every_page",
        "fixture_write_doors", "statement_parser_contract", "stylesheet_as_served",
        "good_results_are_said_quietly", "identifiers_are_set_as_code",
        "suite_runs_against_itself", "suite_markers", "scratch_path_sink",
        "import_direction", "source_tree", "page_snapshot",
        "every_page_is_reachable", "deep_links", "page_structure", "namespaces",
        "migrations_are_reachable", "schema_version_gate",
        "times_on_the_owners_clock", "handler_faults",
    }
)

#: Tests that build the large stores (`large_store_corpus`) or rebuild and compare whole stores.
SLOW: frozenset[str] = frozenset(
    {
        "large_store_corpus", "large_store_rebuild", "ledger_speed", "recurring_speed",
        "identity_health_speed", "page_equivalence", "matching_equivalence", "scaling_laws",
    }
)


def layer_of(stem: str) -> str | None:
    """The layer a test file (named without `test_` and `.py`) belongs to, or None if unplaced."""
    for layer, patterns in LAYERS:
        if any(fnmatch.fnmatchcase(stem, pattern) for pattern in patterns):
            return layer
    return None


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Mark every collected test by its file, and refuse a file the table does not place."""
    unplaced: set[str] = set()
    for item in items:
        stem = Path(str(item.fspath)).stem.removeprefix("test_")
        layer = layer_of(stem)
        if layer is None:
            unplaced.add(Path(str(item.fspath)).name)
            continue
        item.add_marker(getattr(pytest.mark, layer))
        if stem in GUARDS:
            item.add_marker(pytest.mark.guards)
        if stem in SLOW:
            item.add_marker(pytest.mark.slow)
    if unplaced:
        raise pytest.UsageError(
            "Test files not placed in a layer (add them to LAYERS in tests/conftest.py): "
            + ", ".join(sorted(unplaced))
        )

#: Prefixes of everything obdi reads from the environment. A prefix rather than a
#: list of names: a variable added tomorrow is covered without anyone remembering
#: this file exists, and the cost of clearing one variable too many is zero -
#: a test that needs one sets it.
CONFIGURATION_PREFIXES = ("OBDI_", "TRUELAYER_", "STARLING_", "ACTUAL_", "EB_")

#: Kept because the suite itself uses them rather than the code under test.
KEEP = frozenset({"OBDI_TEST_LEDGER"})


@pytest.fixture
def land_transaction():
    """Put one ordinary transaction in the store THROUGH THE WRITE DOOR.

    Returns the entity id the application minted, which is the whole point: a
    fixture that invents its own ids cannot detect the writer and the reader
    disagreeing about identity, and identity is where this project's expensive
    defects live. Entity ids fold in the account, the source, the content key and
    the occurrence, and every one of the refile and rebind faults found so far
    turned on that.

    The content key is computed with the application's own function rather than
    made up, for the same reason - a hand-written key is a second opinion about
    what makes two rows the same payment.
    """
    from datetime import date as _date

    from obdi.core.models import SourceTier, Transaction, TransactionStatus
    from obdi.identity import content_key as compute_content_key
    from obdi.ingest import reconcile_batch

    def land(
        store,
        *,
        description: str,
        amount_minor: int = -1234,
        account: str = "halifax-current",
        value_date=None,
        source: str = "truelayer",
        source_id: str | None = None,
        raw: dict | None = None,
        digest: str = "fixture-digest",
        tier: object = None,
        status: object = None,
    ) -> str:
        # A string is accepted because most fixtures write dates that way, and
        # making each one import date to use this would be friction pushing them
        # back towards the raw insert this exists to replace.
        when = value_date or _date(2026, 7, 1)
        if isinstance(when, str):
            when = _date.fromisoformat(when)
        transaction = Transaction(
            account_id=account,
            amount_minor=amount_minor,
            currency="GBP",
            value_date=when,
            booking_date=when,
            description=description,
            source=source,
            source_id=source_id if source_id is not None else f"tl-{description}",
            tier=tier or SourceTier.AUTHORITATIVE,
            content_key=compute_content_key(
                amount_minor=amount_minor, value_date=when, description=description
            ),
            raw=raw or {},
            status=status or TransactionStatus.BOOKED,
        )
        reconcile_batch(store, [transaction], digest=digest)
        # Read back rather than derived here: what the door decided is the answer,
        # and recomputing it would be the same second opinion this avoids.
        return next(
            row.entity_id
            for row in store.all_transactions()
            if row.description == description and row.amount_minor == amount_minor
        )

    return land


@pytest.fixture
def serve_hub(tmp_path):
    """Start the real handler over a `WebConfig` of the hooks a test names; returns its address.

    No store is behind it: a hub page is drawn from hooks, and a hook a test leaves out is a
    hook that is not wired, which is the state the pages must also answer in.
    """
    import threading
    from http.server import HTTPServer

    from obdi.connections import ConnectionStore
    from obdi.web import AuthorisationSession, ConnectionHandler, WebConfig

    servers: list[HTTPServer] = []

    def start(store: ConnectionStore | None = None, **hooks) -> str:
        config = WebConfig(
            client_id="c",
            client_secret="tlcs_live_abcdefghij1234567890",
            redirect_uri="https://obdi.example.com/callback",
            connection_store=store or ConnectionStore(tmp_path / f"c{len(servers)}.json"),
            **hooks,
        )
        handler = type(
            "H", (ConnectionHandler,), {"config": config, "session": AuthorisationSession()}
        )
        httpd = HTTPServer(("127.0.0.1", 0), handler)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        servers.append(httpd)
        return f"http://127.0.0.1:{httpd.server_port}"

    yield start
    for httpd in servers:
        httpd.shutdown()


@pytest.fixture(scope="session")
def configuration_prefixes() -> tuple[str, ...]:
    """The prefixes, as a fixture rather than an import.

    `from tests.conftest import ...` works wherever the repository root happens
    to be on sys.path and fails where it is not - it passed locally and broke
    every collection in CI. pytest imports this file for its own reasons, so a
    fixture is the one route that needs no path to resolve.

    Session-scoped because it holds a constant, and because a module-scoped
    fixture cannot depend on a function-scoped one.
    """
    return CONFIGURATION_PREFIXES


@pytest.fixture(scope="session", autouse=True)
def _one_tls_context_for_every_client() -> Iterator[None]:
    """Build httpx's default TLS context once per process instead of per client.

    MEASURED: `httpx.get(...)` builds a client, and a client builds a TLS
    context by loading the whole certificate bundle - 0.71 s of the 0.75 s a
    page test spent on its single request, on the Windows development machine.
    About 5,000 tests make a request, so that was the largest single share of the
    suite. The context is the real one, built the real way, only shared;
    contexts are safe to share across threads and clients. Nothing here is about
    certificates: every page test talks plain HTTP to 127.0.0.1, and a test of
    the providers hands them its own transport.
    """
    from httpx._transports import default

    real = default.create_ssl_context
    cached = functools.cache(real)

    def create(*args, **kwargs):
        try:
            return cached(*args, **kwargs)
        except TypeError:
            # An unhashable argument cannot be a cache key, and building the
            # context afresh is always correct.
            return real(*args, **kwargs)

    default.create_ssl_context = create
    try:
        yield
    finally:
        default.create_ssl_context = real


@pytest.fixture(scope="session", autouse=True)
def _test_servers_stop_promptly() -> Iterator[None]:
    """Make `server.shutdown()` return in milliseconds rather than half a second.

    MEASURED: 691 tests spent 0.5 s each in teardown, 354 s of test time across
    the suite, all of it `shutdown()` waiting for `serve_forever` to wake from
    its default 0.5 s poll. Some 100 test modules start their own server with
    that default, so the interval is set once here rather than at every call.
    A shorter poll costs an idle thread a few wake-ups per second, and no test
    is about how a server idles.
    """
    from socketserver import BaseServer

    real = BaseServer.serve_forever

    def serve_forever(self: BaseServer, poll_interval: float = 0.01) -> None:
        real(self, poll_interval)

    BaseServer.serve_forever = serve_forever  # type: ignore[method-assign]
    try:
        yield
    finally:
        BaseServer.serve_forever = real  # type: ignore[method-assign]


@pytest.fixture(autouse=True)
def _no_card_list_over_the_network(monkeypatch) -> None:
    """A routine TrueLayer pull asks for the card list, so a test that stubs
    the accounts and transactions seams but not this one would make a real
    request. A connection with no cards is the neutral answer; a test about
    cards replaces it. A call carrying its own `client` is a test of the real
    function against a fake transport, and passes straight through."""
    from obdi.providers import truelayer

    real = truelayer.fetch_cards

    def guarded(*args, **kwargs):
        if kwargs.get("client") is not None:
            return real(*args, **kwargs)
        return [], b'{"results": []}'

    monkeypatch.setattr("obdi.pull.truelayer.fetch_cards", guarded)


@pytest.fixture(autouse=True)
def _no_ambient_configuration(monkeypatch) -> None:
    for name in list(os.environ):
        if name in KEEP:
            continue
        if name.startswith(CONFIGURATION_PREFIXES):
            monkeypatch.delenv(name, raising=False)

    # Clearing the variables is not enough on its own: a test that exercises the
    # command line re-loads the file MID-TEST, because main() calls load_dotenv()
    # and that writes into the process environment. Neutralised here rather than
    # in the application, where reading the environment is the point - a
    # container is configured by env vars, and the file is a convenience for
    # whoever is sitting at the machine. A test is neither.
    monkeypatch.setattr("obdi.cli.load_dotenv", lambda *args, **kwargs: False)
