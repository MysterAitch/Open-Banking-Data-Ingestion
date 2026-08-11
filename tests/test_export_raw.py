"""Raw artefacts as files on disk, for eyes and ordinary tools.

The store keeps layer 0 in SQLite for atomicity and single-file backup - but a
person exploring the data reasonably expects files they can open, grep and
diff. Export is a projection, not a second source of truth: deterministic
names, safe to re-run, delete at will.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

from obdi.cli import main
from obdi.models import RawArtefact
from obdi.store import Store


def _land(store, *, source, digest, payload, origin, meta=""):
    store.land_artefact(
        RawArtefact(
            source=source,
            account_ref="halifax-current",
            fetched_at=datetime(2026, 8, 1, 22, 30, tzinfo=UTC),
            media_type="application/json",
            digest=digest,
            payload=payload,
            origin=origin,
            request_meta=meta,
        )
    )


class TestExportRaw:
    def test_Export_WritesPayloadAndProvenanceSidecar(self, monkeypatch, tmp_path):
        monkeypatch.setenv("OBDI_DB_PATH", str(tmp_path / "store.sqlite3"))
        monkeypatch.setattr("obdi.cli.load_dotenv", lambda *a, **k: None)
        with Store(tmp_path / "store.sqlite3") as store:
            _land(
                store,
                source="truelayer-booked",
                digest="abcdef1234567890",
                payload=b'{"results": []}',
                origin="https://api/transactions?from=2024-08-02&to=2026-08-01",
                meta='{"trigger": "cli-attended"}',
            )

        out = tmp_path / "raw"
        assert main(["export-raw", "--dir", str(out)]) == 0

        written = sorted(p.relative_to(out).as_posix() for p in out.rglob("*") if p.is_file())
        assert written == [
            "truelayer-booked/2026-08-01T2230_abcdef12.json",
            "truelayer-booked/2026-08-01T2230_abcdef12.meta.json",
        ]
        assert (out / written[0]).read_bytes() == b'{"results": []}'
        sidecar = json.loads((out / written[1]).read_text(encoding="utf-8"))
        assert sidecar["origin"].endswith("from=2024-08-02&to=2026-08-01")
        assert sidecar["request_meta"]["trigger"] == "cli-attended"
        assert sidecar["account_ref"] == "halifax-current"

    def test_Export_WhenOneArtefactWasSeenUnderSeveralNames_CarriesThemAll(
        self, monkeypatch, tmp_path
    ):
        """The projection is what a person greps, so it must not know less
        about where the bytes came from than the store does."""
        monkeypatch.setenv("OBDI_DB_PATH", str(tmp_path / "store.sqlite3"))
        monkeypatch.setattr("obdi.cli.load_dotenv", lambda *a, **k: None)
        with Store(tmp_path / "store.sqlite3") as store:
            for origin in ("statement.pdf", "Santander/statement.pdf"):
                _land(
                    store,
                    source="statement",
                    digest="abcdef1234567890",
                    payload=b"%PDF-1.4 bytes",
                    origin=origin,
                )

        out = tmp_path / "raw"
        assert main(["export-raw", "--dir", str(out)]) == 0

        sidecar = json.loads(
            next(out.rglob("*.meta.json")).read_text(encoding="utf-8")
        )
        # Both landings are stamped the same second, so their order is the
        # documented tie-break rather than anything meaningful.
        assert sorted(sidecar["origins"]) == [
            "Santander/statement.pdf",
            "statement.pdf",
        ]

    def test_Export_IsIdempotent_SameNamesOnRerun(self, monkeypatch, tmp_path):
        monkeypatch.setenv("OBDI_DB_PATH", str(tmp_path / "store.sqlite3"))
        monkeypatch.setattr("obdi.cli.load_dotenv", lambda *a, **k: None)
        with Store(tmp_path / "store.sqlite3") as store:
            _land(
                store,
                source="truelayer-balance",
                digest="feedbeef00112233",
                payload=b"{}",
                origin="https://api/balance",
            )

        out = tmp_path / "raw"
        main(["export-raw", "--dir", str(out)])
        first = sorted(p.as_posix() for p in out.rglob("*") if p.is_file())
        main(["export-raw", "--dir", str(out)])
        second = sorted(p.as_posix() for p in out.rglob("*") if p.is_file())

        assert first == second, "a projection re-runs cleanly; it never accumulates"
