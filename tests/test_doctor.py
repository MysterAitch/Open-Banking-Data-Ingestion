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
        # Well-formed, not merely present: doctor now checks shape, so a
        # placeholder no provider would issue is no longer "configured".
        secret.write_text("tlcs_live_abcdefghij1234567890", encoding="utf-8")
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


class TestSecretsThatExistButCannotWork:
    """Readable is not usable: a malformed secret passes every presence check.

    Wrapping quotes survive a YAML paste invisibly, because nothing ever prints
    a secret. A value without the provider's prefix is almost certainly the
    secret's IDENTIFIER - shown forever in the console - rather than its value,
    which is shown exactly once at creation. Each produces the same distant
    HTTP 400 hours later; doctor exists to say it now, and to say it without
    revealing a single character of the value.
    """

    def _configured(self, monkeypatch, tmp_path, secret_text: str):
        secret = tmp_path / "client-secret"
        secret.write_text(secret_text, encoding="utf-8")
        monkeypatch.setenv("OBDI_DB_PATH", str(tmp_path / "store.sqlite3"))
        monkeypatch.setenv("OBDI_CONNECTION_STORE", str(tmp_path / "connections.json"))
        monkeypatch.setenv("TRUELAYER_CLIENT_SECRET_FILE", str(secret))

    def test_Doctor_WhenTheSecretIsWrappedInQuotes_FailsNamingTheQuotes(
        self, monkeypatch, tmp_path
    ):
        self._configured(monkeypatch, tmp_path, '"tlcs_live_abcdefghij1234567890"')

        failed = [r for r in run_checks() if not r.ok]

        assert any("quote" in r.detail.casefold() for r in failed)
        assert not any("abcdefghij" in r.detail for r in failed), "never print the value"

    def test_Doctor_WhenTheSecretHasWhitespaceInside_Fails(self, monkeypatch, tmp_path):
        self._configured(monkeypatch, tmp_path, "tlcs_live_abcde fghij1234567890")

        failed = [r for r in run_checks() if not r.ok]

        assert any("whitespace" in r.detail.casefold() for r in failed)

    def test_Doctor_WhenTheValueLacksTheLivePrefix_SuggestsTheIdentifierTrap(
        self, monkeypatch, tmp_path
    ):
        # A UUID-shaped value: exactly what copying the secret's identifier
        # instead of its value produces.
        self._configured(monkeypatch, tmp_path, "9e0f8a2b-1234-5678-9abc-def012345678")

        failed = [r for r in run_checks() if not r.ok]

        assert any("identifier" in r.detail.casefold() for r in failed)
        assert not any("9e0f8a2b" in r.detail for r in failed), "never print the value"

    def test_Doctor_WhenASandboxSecretMeetsALiveClientId_SaysWhichWorldEachIsIn(
        self, monkeypatch, tmp_path
    ):
        self._configured(monkeypatch, tmp_path, "tlcs_sandbox_abcdefghij1234567890")
        monkeypatch.setenv("TRUELAYER_CLIENT_ID", "personaldataaccess-e8326b")

        failed = [r for r in run_checks() if not r.ok]

        assert any("sandbox" in r.detail.casefold() for r in failed)

    def test_Doctor_WhenTheSecretIsWellFormed_PassesTheShapeCheck(
        self, monkeypatch, tmp_path
    ):
        self._configured(monkeypatch, tmp_path, "tlcs_live_abcdefghij1234567890")

        assert all(r.ok for r in run_checks()), [r.detail for r in run_checks() if not r.ok]
