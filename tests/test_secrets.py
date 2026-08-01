import pytest

from obdi.secrets import SecretError, describe_source, read_secret

NAME = "OBDI_TEST_SECRET"


@pytest.fixture(autouse=True)
def clear_environment(monkeypatch):
    monkeypatch.delenv(NAME, raising=False)
    monkeypatch.delenv(f"{NAME}_FILE", raising=False)


class TestSecretResolution:
    def test_Secret_WhenOnlyInlineValueSet_ReadFromEnvironment(self, monkeypatch):
        monkeypatch.setenv(NAME, "inline-value")
        assert read_secret(NAME) == "inline-value"

    def test_Secret_WhenFilePathSet_ReadFromFile(self, monkeypatch, tmp_path):
        secret_file = tmp_path / "secret.txt"
        secret_file.write_text("value-from-file", encoding="utf-8")
        monkeypatch.setenv(f"{NAME}_FILE", str(secret_file))
        assert read_secret(NAME) == "value-from-file"

    def test_Secret_WhenBothSet_FileWins(self, monkeypatch, tmp_path):
        # The file form is the safer one, so it must not be silently overridden
        # by a stale inline value left in .env.
        secret_file = tmp_path / "secret.txt"
        secret_file.write_text("value-from-file", encoding="utf-8")
        monkeypatch.setenv(NAME, "inline-value")
        monkeypatch.setenv(f"{NAME}_FILE", str(secret_file))
        assert read_secret(NAME) == "value-from-file"

    def test_Secret_WhenFileEndsWithNewline_NewlineStripped(self, monkeypatch, tmp_path):
        # An editor adding a trailing newline would otherwise produce a
        # credential that fails to authenticate for no visible reason.
        secret_file = tmp_path / "secret.txt"
        secret_file.write_text("value-from-file\n", encoding="utf-8")
        monkeypatch.setenv(f"{NAME}_FILE", str(secret_file))
        assert read_secret(NAME) == "value-from-file"

    def test_Secret_WhenInlineValueHasSurroundingSpace_Trimmed(self, monkeypatch):
        monkeypatch.setenv(NAME, "  inline-value  ")
        assert read_secret(NAME) == "inline-value"


class TestSecretFailures:
    def test_Secret_WhenFileMissing_FailsLoudlyNamingThePath(self, monkeypatch, tmp_path):
        missing = tmp_path / "absent.txt"
        monkeypatch.setenv(f"{NAME}_FILE", str(missing))
        with pytest.raises(SecretError, match="does not exist"):
            read_secret(NAME)

    def test_Secret_WhenFileEmpty_FailsRatherThanReturningBlank(self, monkeypatch, tmp_path):
        secret_file = tmp_path / "secret.txt"
        secret_file.write_text("   \n", encoding="utf-8")
        monkeypatch.setenv(f"{NAME}_FILE", str(secret_file))
        with pytest.raises(SecretError, match="empty"):
            read_secret(NAME)

    def test_Secret_WhenNothingConfigured_FailsWithActionableMessage(self):
        with pytest.raises(SecretError, match="_FILE"):
            read_secret(NAME)

    def test_Secret_WhenNothingConfiguredButOptional_ReturnsEmpty(self):
        assert read_secret(NAME, required=False) == ""


class TestSourceDescription:
    def test_Diagnostics_WhenSecretFromFile_ReportsPathNotValue(self, monkeypatch, tmp_path):
        secret_file = tmp_path / "secret.txt"
        secret_file.write_text("top-secret", encoding="utf-8")
        monkeypatch.setenv(f"{NAME}_FILE", str(secret_file))

        description = describe_source(NAME)

        assert str(secret_file) in description
        assert "top-secret" not in description

    def test_Diagnostics_WhenSecretInline_ReportsSourceNotValue(self, monkeypatch):
        monkeypatch.setenv(NAME, "top-secret")

        description = describe_source(NAME)

        assert "environment" in description
        assert "top-secret" not in description

    def test_Diagnostics_WhenUnset_ReportsUnset(self):
        assert describe_source(NAME) == "unset"
