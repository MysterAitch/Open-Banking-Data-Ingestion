"""The provider's history wall must survive being given a friendly name.

Finding the wall costs a real provider request from a scarce daily quota:
the extend button walks backward until the bank refuses a 1-day step, and
that refusal is banked as a boundary fact. Binding the account afterwards
is a labelling act - it must not lose the wall, because a page that has
forgotten it invites the same expensive walk all over again.
"""

from __future__ import annotations

import ast
import json
from datetime import date
from pathlib import Path

from obdi.accounts import AccountBinding, AccountMap
from obdi.cli import _apply_bind, _recorded_boundary
from obdi.store import Store


def _account_with_history(db: Path, account_id: str) -> None:
    with Store(db) as store:
        store.connection.execute(
            "INSERT INTO transactions (entity_id, account_id, amount_minor, "
            "value_date, booking_date, description, source, currency, tier, "
            "status, content_key, occurrence, first_seen_at, last_seen_at) "
            "VALUES ('e-1', ?, -1250, '2026-07-01', '2026-07-01', "
            "'Coffee', 'truelayer', 'GBP', 'authoritative', 'booked', "
            "'ck-1', 0, '2026-07-01T00:00:00', '2026-07-01T00:00:00')",
            (account_id,),
        )
        store.connection.commit()


def _record_wall(db: Path, connection: str, keyed_to: str, when: str) -> None:
    """What a refused 1-day step banks: this provider goes no further back."""
    with Store(db) as store:
        store.record_provider_fact(
            "truelayer", connection, f"history_boundary:{keyed_to}", when
        )


class TestProviderWallSurvivesBinding:
    def test_ProviderWall_WhenTheAccountIsNamedAfterwards_IsStillReported(
        self, tmp_path, monkeypatch
    ):
        """The live sequence: probe an unnamed account to its wall, then
        bind it. The wall was found and paid for - it must still be found
        under the new name, or the extend row re-encourages the walk."""
        db = tmp_path / "s.sqlite3"
        map_file = tmp_path / "accounts.json"
        monkeypatch.setenv("OBDI_ACCOUNT_MAP", str(map_file))
        _account_with_history(db, "truelayer:acct-9")
        _record_wall(db, "halifax", "truelayer:acct-9", "2024-07-01")

        _apply_bind(
            db, map_file, AccountMap(), "truelayer", "acct-9", "halifax-current"
        )

        with Store(db) as store:
            assert _recorded_boundary(store, "halifax", "halifax-current") == date(
                2024, 7, 1
            )

    def test_ProviderWall_WhenProbedAfterBinding_IsReportedUnderTheBoundName(
        self, tmp_path, monkeypatch
    ):
        """The other order - bind first, probe later - was never broken,
        and must stay working now the lookup reads an alias set."""
        db = tmp_path / "s.sqlite3"
        map_file = tmp_path / "accounts.json"
        map_file.write_text(
            json.dumps(
                {
                    "bindings": [
                        {
                            "canonical_id": "halifax-current",
                            "source": "truelayer",
                            "provider_account_id": "acct-9",
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )
        monkeypatch.setenv("OBDI_ACCOUNT_MAP", str(map_file))
        _record_wall(db, "halifax", "halifax-current", "2024-07-01")

        with Store(db) as store:
            assert _recorded_boundary(store, "halifax", "halifax-current") == date(
                2024, 7, 1
            )

    def test_ProviderWall_WhenTheAccountIsStillUnnamed_IsReported(
        self, tmp_path, monkeypatch
    ):
        db = tmp_path / "s.sqlite3"
        map_file = tmp_path / "accounts.json"
        monkeypatch.setenv("OBDI_ACCOUNT_MAP", str(map_file))
        _record_wall(db, "halifax", "truelayer:acct-9", "2024-07-01")

        with Store(db) as store:
            assert _recorded_boundary(
                store, "halifax", "truelayer:acct-9"
            ) == date(2024, 7, 1)

    def test_ProviderWall_WhenNeverProbed_IsAbsentSoTheRowStillInvitesAProbe(
        self, tmp_path, monkeypatch
    ):
        """The wall must not be invented: an account nobody has walked back
        has no boundary, and the row should keep offering the button."""
        db = tmp_path / "s.sqlite3"
        map_file = tmp_path / "accounts.json"
        monkeypatch.setenv("OBDI_ACCOUNT_MAP", str(map_file))
        _record_wall(db, "halifax", "truelayer:other-account", "2024-07-01")

        with Store(db) as store:
            assert _recorded_boundary(store, "halifax", "truelayer:acct-9") is None

    def test_ProviderWall_WhenBothNamesCarryOne_ReportsTheCurrentNamesAnswer(
        self, tmp_path, monkeypatch
    ):
        """A wall probed again after binding is the better-informed answer:
        the old key is a leftover from before the rename, not a rival."""
        db = tmp_path / "s.sqlite3"
        map_file = tmp_path / "accounts.json"
        map_file.write_text(
            json.dumps(
                {
                    "bindings": [
                        {
                            "canonical_id": "halifax-current",
                            "source": "truelayer",
                            "provider_account_id": "acct-9",
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )
        monkeypatch.setenv("OBDI_ACCOUNT_MAP", str(map_file))
        _record_wall(db, "halifax", "truelayer:acct-9", "2024-07-01")
        _record_wall(db, "halifax", "halifax-current", "2023-01-31")

        with Store(db) as store:
            assert _recorded_boundary(store, "halifax", "halifax-current") == date(
                2023, 1, 31
            )

    def test_ProviderWall_WhenTheAccountIsRenamedAgain_IsStillReported(
        self, tmp_path, monkeypatch
    ):
        """Binding is revisable, and the second name must inherit the wall
        the same way the first did."""
        db = tmp_path / "s.sqlite3"
        map_file = tmp_path / "accounts.json"
        monkeypatch.setenv("OBDI_ACCOUNT_MAP", str(map_file))
        _account_with_history(db, "truelayer:acct-9")
        _record_wall(db, "halifax", "truelayer:acct-9", "2024-07-01")

        _apply_bind(
            db, map_file, AccountMap(), "truelayer", "acct-9", "halifax-current"
        )
        bound = AccountMap(
            [
                AccountBinding(
                    canonical_id="halifax-current",
                    source="truelayer",
                    provider_account_id="acct-9",
                )
            ]
        )
        _apply_bind(db, map_file, bound, "truelayer", "acct-9", "halifax-reward")

        with Store(db) as store:
            assert _recorded_boundary(store, "halifax", "halifax-reward") == date(
                2024, 7, 1
            )


    def test_ProviderWall_WhenTheAccountLosesTheNameItWasRecordedUnder_Travels(
        self, tmp_path, monkeypatch
    ):
        """Renaming an already-named account retires the old canonical, and
        a retired name is reachable through no alias set - so the wall has
        to move with the rows rather than be left behind under it."""
        db = tmp_path / "s.sqlite3"
        map_file = tmp_path / "accounts.json"
        monkeypatch.setenv("OBDI_ACCOUNT_MAP", str(map_file))
        _account_with_history(db, "halifax-current")
        _record_wall(db, "halifax", "halifax-current", "2024-07-01")
        bound = AccountMap(
            [
                AccountBinding(
                    canonical_id="halifax-current",
                    source="truelayer",
                    provider_account_id="acct-9",
                )
            ]
        )

        _apply_bind(db, map_file, bound, "truelayer", "acct-9", "halifax-reward")

        with Store(db) as store:
            assert _recorded_boundary(store, "halifax", "halifax-reward") == date(
                2024, 7, 1
            )

    def test_ProviderWall_WhenTheNewNameAlreadyHasOne_KeepsTheNewNamesAnswer(
        self, tmp_path, monkeypatch
    ):
        """Carrying a fact forward must never overwrite one already recorded
        under the destination name."""
        db = tmp_path / "s.sqlite3"
        map_file = tmp_path / "accounts.json"
        monkeypatch.setenv("OBDI_ACCOUNT_MAP", str(map_file))
        _account_with_history(db, "truelayer:acct-9")
        _record_wall(db, "halifax", "truelayer:acct-9", "2024-07-01")
        _record_wall(db, "halifax", "halifax-current", "2023-01-31")

        _apply_bind(
            db, map_file, AccountMap(), "truelayer", "acct-9", "halifax-current"
        )

        with Store(db) as store:
            assert _recorded_boundary(store, "halifax", "halifax-current") == date(
                2023, 1, 31
            )


class TestBoundaryLookupsShareOneReader:
    def test_BoundaryFactKey_WhereverItIsBuiltInTheCli_IsBuiltByTheReaderOrTheRecorder(
        self,
    ):
        """The fault was one lookup composing the fact key from the current
        canonical alone. Reading the source tree rather than keeping a list
        by hand: a new lookup that composes the key itself fails here."""
        source_path = Path(__file__).resolve().parents[1] / "src" / "obdi" / "cli.py"
        source = source_path.read_text(encoding="utf-8")
        spans = [
            (node.lineno, node.end_lineno or node.lineno, node.name)
            for node in ast.walk(ast.parse(source))
            if isinstance(node, ast.FunctionDef)
        ]

        def owner(lineno: int) -> str:
            # Innermost wins: these lookups live in closures inside _serve.
            enclosing = [s for s in spans if s[0] <= lineno <= s[1]]
            if not enclosing:
                return "(module level)"
            return min(enclosing, key=lambda s: s[1] - s[0])[2]

        offenders = {
            owner(lineno)
            for lineno, text in enumerate(source.splitlines(), start=1)
            if "history_boundary:{" in text
        }
        assert offenders, "the boundary fact key vanished - this guard is blind"

        assert offenders <= {
            "_recorded_boundary",
            "_carry_account_facts",
            "extend_window",
        }, (
            "a keyed history_boundary lookup outside the shared reader will "
            "miss the walls recorded under an account's earlier names: "
            f"{sorted(offenders)}"
        )

    def test_EveryRebindInTheCli_AlsoCarriesTheAccountKeyedFacts(self):
        """The rebind moves rows, artefacts and attempts; the account-keyed
        facts have to move with them. A third bind door added later without
        the carry orphans the wall again, so the source tree is read rather
        than a list of doors kept by hand."""
        source_path = Path(__file__).resolve().parents[1] / "src" / "obdi" / "cli.py"
        tree = ast.parse(source_path.read_text(encoding="utf-8"))
        careless = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef):
                continue
            body = ast.dump(node)
            if "'rebind_account'" in body and "'_carry_account_facts'" not in body:
                careless.append(node.name)

        assert careless == [], (
            "these rebind doors move the rows but leave the recorded provider "
            f"boundary behind under the old name: {careless}"
        )
