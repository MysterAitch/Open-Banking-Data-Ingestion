"""A test must not be able to read the machine it runs on.

The suite exercises the command line, and the command line loads a `.env`. That
call writes straight into the process environment, where it outlives the test
that caused it - so without a guard, a test's behaviour depends on which tests
ran before it and on what the person running it happens to have configured.

That is not hypothetical: a migration probe added on 2026-08-12 read a real
accounts file through a leaked OBDI_ACCOUNT_MAP and reported a migration as
reachable that the shipped shapes cannot reach. The same leak carries
OBDI_DB_PATH, which names the real financial store.

These check the guard rather than trusting it, because its failure mode is
silence: nothing goes wrong until a test quietly reads the wrong thing.
"""

from __future__ import annotations

import os

import pytest

from obdi.store import Store


def _configured(prefixes: tuple[str, ...]) -> dict[str, str]:
    return {
        name: value
        for name, value in os.environ.items()
        if name.startswith(prefixes)
    }


class TestTheEnvironmentATestSees:
    def test_ATest_SeesNoConfigurationItDidNotSetItself(self, configuration_prefixes):
        leaked = _configured(configuration_prefixes)
        assert not leaked, (
            f"the environment carries configuration this test did not set: "
            f"{sorted(leaked)}. A test reading those is measuring the machine."
        )

    def test_ATestFollowingOneThatLoadedTheEnvFile_StillSeesNothing(self, tmp_path):
        """The order-dependence in its exact shape: one test loads the file, the
        next one must not inherit it."""
        from dotenv import load_dotenv

        env_file = tmp_path / ".env"
        env_file.write_text(
            "OBDI_DB_PATH=/somewhere/real/store.sqlite3\n"
            "TRUELAYER_CLIENT_ID=not-a-real-id\n",
            encoding="utf-8",
        )
        load_dotenv(env_file)
        assert os.environ.get("OBDI_DB_PATH") == "/somewhere/real/store.sqlite3"

    def test_ExercisingTheCommandLine_DoesNotLoadTheMachinesConfiguration(
        self, tmp_path, monkeypatch
    ):
        """The hole clearing the variables leaves open.

        main() calls load_dotenv(), so a test that runs a command re-loads the
        file DURING the test - after the guard has cleared everything, and in
        time for whatever that test does next.

        The file is written HERE rather than relied upon, and the working
        directory moved to it, so this fails the same way on a machine that has
        no .env of its own - CI has none, which is how the original leak stayed
        invisible there while being live locally.

        Asserted on a variable the test never set. load_dotenv does not overwrite
        what is already in the environment, so a variable the test DID set proves
        nothing - which the first version of this test did, passing happily with
        the guard removed.
        """
        from obdi import cli

        store = tmp_path / "store.sqlite3"
        Store(store).close()
        (tmp_path / ".env").write_text(
            "OBDI_ACCOUNT_MAP=/somebody/elses/accounts.json\n", encoding="utf-8"
        )
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("OBDI_DB_PATH", str(store))

        cli.main(["status"])

        assert "OBDI_ACCOUNT_MAP" not in os.environ, (
            "running a command loaded configuration from a file beside the "
            "working directory, and it will outlive this test"
        )

    def test_TheTestAfterThat_SeesNoneOfIt(self):
        # Deliberately a separate test rather than a cleanup assertion inside the
        # one above: the guard being checked runs BETWEEN tests, so it can only
        # be observed from the next one.
        assert "OBDI_DB_PATH" not in os.environ, (
            "configuration loaded by the previous test survived into this one"
        )


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
