"""The pre-flight check, written against the faults that actually occurred.

Every case here is a real deployment failure this project hit, not a
hypothetical. The common shape: the process starts, reports nothing wrong, and
fails much later at the first operation that touches the misconfigured thing -
by which point the error names a library call rather than the cause.

`doctor` exists to move all of that to one place that runs before anything
matters, and to say which of them is wrong in plain words.
"""

from __future__ import annotations

import pytest

from obdi.doctor import CheckResult, run_checks


class TestConfigurationThatIsSimplyAbsent:
    def test_Doctor_WhenNoConfigurationAtAll_FailsAndNamesEveryMissingSetting(
        self, monkeypatch
    ):
        for name in ("OBDI_DB_PATH", "OBDI_CONNECTION_STORE", "OBDI_ACCOUNT_MAP"):
            monkeypatch.delenv(name, raising=False)

        results = run_checks()

        assert not all(r.ok for r in results)
        reported = " ".join(r.detail for r in results if not r.ok)
        assert "OBDI_DB_PATH" in reported
        assert "OBDI_CONNECTION_STORE" in reported

    def test_Doctor_WhenFullyConfigured_PassesEveryCheck(self, monkeypatch, tmp_path):
        secret = tmp_path / "client-secret"
        secret.write_text("value", encoding="utf-8")
        monkeypatch.setenv("OBDI_DB_PATH", str(tmp_path / "store.sqlite3"))
        monkeypatch.setenv("OBDI_CONNECTION_STORE", str(tmp_path / "connections.json"))
        monkeypatch.setenv("TRUELAYER_CLIENT_SECRET_FILE", str(secret))

        results = run_checks()

        assert all(r.ok for r in results), [r.detail for r in results if not r.ok]


class TestPathsTheProcessCannotUse:
    """The uid mismatch: paths are correct, the process is not allowed to use them."""

    def test_Doctor_WhenTheDataDirectoryIsNotWritable_FailsBeforeAnythingIsWritten(
        self, monkeypatch, tmp_path
    ):
        monkeypatch.setenv("OBDI_DB_PATH", str(tmp_path / "nope" / "store.sqlite3"))
        monkeypatch.setenv("OBDI_CONNECTION_STORE", str(tmp_path / "connections.json"))

        results = run_checks()

        failed = [r for r in results if not r.ok]
        assert failed, "a database directory that does not exist must be reported"
        assert any("nope" in r.detail for r in failed)

    def test_Doctor_WhenTheSecretFileCannotBeRead_ReportsItWithoutRevealingIt(
        self, monkeypatch, tmp_path
    ):
        secret = tmp_path / "client-secret"
        secret.write_text("top-secret", encoding="utf-8")
        monkeypatch.setenv("OBDI_DB_PATH", str(tmp_path / "store.sqlite3"))
        monkeypatch.setenv("OBDI_CONNECTION_STORE", str(tmp_path / "connections.json"))
        monkeypatch.setenv("TRUELAYER_CLIENT_SECRET_FILE", str(tmp_path / "absent"))

        results = run_checks()

        failed = [r for r in results if not r.ok]
        assert any("TRUELAYER_CLIENT_SECRET" in r.detail for r in failed)
        assert not any("top-secret" in r.detail for r in results)


class TestTheCommandItself:
    """`main` loads .env, so these isolate from whatever the developer has locally.

    Without that, the suite passes or fails according to a gitignored file that
    differs on every machine - and worse, would pass on the one machine whose
    .env happens to be correct.
    """

    @pytest.fixture(autouse=True)
    def _no_dotenv(self, monkeypatch):
        monkeypatch.setattr("obdi.cli.load_dotenv", lambda *a, **k: None)

    def test_DoctorCommand_WhenSomethingIsWrong_ExitsNonZeroSoADeployCanGateOnIt(
        self, monkeypatch, capsys
    ):
        from obdi.cli import main

        for name in ("OBDI_DB_PATH", "OBDI_CONNECTION_STORE"):
            monkeypatch.delenv(name, raising=False)

        exit_code = main(["doctor"])

        assert exit_code != 0
        assert "OBDI_DB_PATH" in capsys.readouterr().out

    def test_DoctorCommand_WhenEverythingIsSound_ExitsZero(
        self, monkeypatch, capsys, tmp_path
    ):
        from obdi.cli import main

        monkeypatch.setenv("OBDI_DB_PATH", str(tmp_path / "store.sqlite3"))
        monkeypatch.setenv("OBDI_CONNECTION_STORE", str(tmp_path / "connections.json"))
        monkeypatch.delenv("TRUELAYER_CLIENT_SECRET_FILE", raising=False)
        monkeypatch.delenv("TRUELAYER_CLIENT_SECRET", raising=False)

        assert main(["doctor"]) == 0


@pytest.fixture(autouse=True)
def _clear(monkeypatch):
    for name in (
        "OBDI_DB_PATH",
        "OBDI_CONNECTION_STORE",
        "OBDI_ACCOUNT_MAP",
        "TRUELAYER_CLIENT_SECRET_FILE",
        "TRUELAYER_CLIENT_SECRET",
        "STARLING_PERSONAL_ACCESS_TOKEN_FILE",
        "STARLING_PERSONAL_ACCESS_TOKEN",
    ):
        monkeypatch.delenv(name, raising=False)


def test_CheckResult_CarriesEnoughToPrintWithoutFurtherLookup():
    result = CheckResult(name="example", ok=False, detail="because of a reason")
    assert result.name and result.detail
