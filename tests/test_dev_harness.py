"""The development harness serves invented data, and says nothing in it can contact a bank.

Two things were wrong. Its store directory defaulted to one fixed path that every run deleted, so a
second run rewrote the store under a server that was still up (a reviewer saw three pages disagree
for six seconds). And `isolated()` claimed that nothing could contact a bank from the harness while
pressing Connect posted the dummy credentials to the provider's real token endpoint, because
overwriting a credential makes a request fail and does not stop it being made.
"""

from __future__ import annotations

import importlib.util
import json
import os
import socket
import sqlite3
import subprocess
import sys
import threading
from http.server import HTTPServer
from pathlib import Path

import httpx
import pytest

from obdi import outbound
from obdi.cli import build_web_config
from obdi.store import Store
from obdi.web import AuthorisationSession, ConnectionHandler

REPO = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="module")
def harness():
    spec = importlib.util.spec_from_file_location(
        "dev_corpus_ui_under_test", REPO / "scripts" / "dev_corpus_ui.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def dead_pid() -> int:
    """The id of a process that has finished, which nothing now holds."""
    process = subprocess.Popen([sys.executable, "-c", "pass"])
    process.wait()
    return process.pid


class TestTwoRunsDoNotShareADirectory:
    def test_DefaultRoot_ForTwoRuns_DiffersAndNamesThePort(self, harness):
        first, second = harness.default_root(38080), harness.default_root(38080)

        assert first != second
        assert "38080" in first.name

    def test_Help_SaysTwoRunsMustNotShareADirectory(self, harness, monkeypatch, capsys):
        monkeypatch.setattr(sys, "argv", ["dev_corpus_ui.py", "--help"])

        with pytest.raises(SystemExit):
            harness.main()

        assert "TWO RUNS MUST NOT SHARE" in capsys.readouterr().out

    def test_KeepWithoutAt_IsRefusedBecauseTheDefaultDirectoryIsNewEveryRun(
        self, harness, monkeypatch
    ):
        monkeypatch.setattr(sys, "argv", ["dev_corpus_ui.py", "--keep"])

        with pytest.raises(SystemExit) as refused:
            harness.main()

        assert "--keep needs --at" in str(refused.value)

    def test_LiveServerIn_WhenTheLockNamesALiveProcess_SaysWhichAndRefuses(
        self, harness, tmp_path
    ):
        (tmp_path / harness.LOCK_NAME).write_text(
            json.dumps({"pid": os.getpid(), "port": 1}), encoding="utf-8"
        )

        held = harness.live_server_in(tmp_path)

        assert held is not None
        assert f"process {os.getpid()} is serving it" in held

    def test_LiveServerIn_WhenTheLockedPortStillAnswers_Refuses(self, harness, tmp_path):
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            listener.listen()
            port = listener.getsockname()[1]
            (tmp_path / harness.LOCK_NAME).write_text(
                json.dumps({"pid": dead_pid(), "port": port}), encoding="utf-8"
            )

            held = harness.live_server_in(tmp_path)

        assert held is not None
        assert f"something answers on port {port}" in held

    def test_LiveServerIn_WhenTheLockIsStale_IsIgnored(self, harness, tmp_path):
        with socket.socket() as spare:
            spare.bind(("127.0.0.1", 0))
            free_port = spare.getsockname()[1]
        (tmp_path / harness.LOCK_NAME).write_text(
            json.dumps({"pid": dead_pid(), "port": free_port}), encoding="utf-8"
        )

        assert harness.live_server_in(tmp_path) is None

    def test_LiveServerIn_WhenTheLockCannotBeRead_RefusesRatherThanGuessing(
        self, harness, tmp_path
    ):
        (tmp_path / harness.LOCK_NAME).write_text("not json", encoding="utf-8")

        held = harness.live_server_in(tmp_path)

        assert held is not None
        assert "cannot be read" in held

    def test_LiveServerIn_WhenAnotherConnectionHoldsTheStoresWriteLock_Refuses(
        self, harness, tmp_path
    ):
        with Store(tmp_path / "store.sqlite3"):
            pass
        holder = sqlite3.connect(tmp_path / "store.sqlite3", timeout=0.2)
        holder.execute("BEGIN EXCLUSIVE")
        try:
            held = harness.live_server_in(tmp_path)
        finally:
            holder.rollback()
            holder.close()

        assert held is not None
        assert "locked by another process" in held

    def test_LiveServerIn_WhenNothingHoldsTheDirectory_ReturnsNone(self, harness, tmp_path):
        with Store(tmp_path / "store.sqlite3"):
            pass

        assert harness.live_server_in(tmp_path) is None
        assert harness.live_server_in(tmp_path / "does-not-exist-yet") is None

    def test_Main_WhenALiveServerHoldsTheDirectory_DeletesNothingAndSaysWhy(
        self, harness, tmp_path, monkeypatch
    ):
        (tmp_path / harness.LOCK_NAME).write_text(
            json.dumps({"pid": os.getpid(), "port": 1}), encoding="utf-8"
        )
        kept = tmp_path / "corpus-file.txt"
        kept.write_text("still here", encoding="utf-8")
        monkeypatch.setattr(sys, "argv", ["dev_corpus_ui.py", "--at", str(tmp_path)])

        with pytest.raises(SystemExit) as refused:
            harness.main()

        assert "refusing to use" in str(refused.value)
        assert "leave --at out" in str(refused.value)
        assert kept.read_text(encoding="utf-8") == "still here"


class TestTheGuardRefusesWhatLeavesTheMachine:
    @pytest.fixture(autouse=True)
    def _guard_off_afterwards(self):
        yield
        outbound.uninstall()

    def test_Guard_WhenAnAddressIsNotThisMachine_RefusesTheConnectionAndTheLookup(self):
        outbound.install()

        with pytest.raises(outbound.OutboundRefused), socket.socket() as stranger:
            stranger.connect(("93.184.216.34", 443))
        with pytest.raises(outbound.OutboundRefused):
            socket.getaddrinfo("auth.truelayer.com", 443)

    def test_Guard_WhenTheAddressIsThisMachine_StillConnects(self):
        outbound.install()
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            listener.listen()
            with socket.socket() as client:
                client.connect(listener.getsockname())

    def test_Guard_WhenInstalledTwice_IsInstalledOnce_AndUninstallRestoresTheSockets(self):
        before = socket.socket.connect

        assert outbound.install() is True
        assert outbound.install() is False
        outbound.uninstall()

        assert socket.socket.connect is before

    @pytest.mark.parametrize("value", ["", "0"])
    def test_InstallIfRequested_WhenTheSwitchIsOffOrZero_DoesNothing(self, monkeypatch, value):
        monkeypatch.setenv(outbound.REFUSE_ENV, value)

        assert outbound.install_if_requested() is False

    def test_InstallIfRequested_WhenTheSwitchIsSet_Installs(self, monkeypatch):
        monkeypatch.setenv(outbound.REFUSE_ENV, "1")

        assert outbound.install_if_requested() is True

    def test_CliMain_StartsTheGuardBeforeAnythingElse(self, monkeypatch):
        from obdi import cli

        asked: list[bool] = []
        monkeypatch.setattr(
            cli, "install_outbound_refusal_if_requested", lambda: asked.append(True) or False
        )
        monkeypatch.setattr(cli, "load_dotenv", lambda: None)

        with pytest.raises(SystemExit):
            cli.main(["--help"])

        assert asked == [True]


class Watched:
    """Every host a socket was opened to or looked up for, with nothing allowed to leave."""

    def __init__(self) -> None:
        self.hosts: list[str] = []

    def install(self, monkeypatch: pytest.MonkeyPatch) -> None:
        real_connect = socket.socket.connect
        real_connect_ex = socket.socket.connect_ex
        real_lookup = socket.getaddrinfo
        watched = self

        def note(host: object) -> None:
            watched.hosts.append(str(host))
            if not outbound.is_loopback(host):
                raise OSError(f"the test refuses to let {host} be reached")

        def connect(sock: socket.socket, address: object) -> None:
            note(address[0] if isinstance(address, tuple) else "")
            real_connect(sock, address)  # type: ignore[arg-type]

        def connect_ex(sock: socket.socket, address: object) -> int:
            note(address[0] if isinstance(address, tuple) else "")
            return real_connect_ex(sock, address)  # type: ignore[arg-type]

        def lookup(host: object, *args: object, **kwargs: object) -> object:
            note(host)
            return real_lookup(host, *args, **kwargs)  # type: ignore[arg-type]

        monkeypatch.setattr(socket.socket, "connect", connect)
        monkeypatch.setattr(socket.socket, "connect_ex", connect_ex)
        monkeypatch.setattr(socket, "getaddrinfo", lookup)

    @property
    def outside(self) -> list[str]:
        return [host for host in self.hosts if not outbound.is_loopback(host)]


@pytest.fixture
def harness_server(harness, tmp_path, monkeypatch):
    """The real handler over the harness's own environment, and a watch on every socket."""
    root = tmp_path / "harness"
    root.mkdir()
    with Store(root / "store.sqlite3"):
        pass

    def start(*, refuse: bool):
        for name, value in harness.isolated(root).items():
            if name != "PYTHONPATH":
                monkeypatch.setenv(name, value)
        if not refuse:
            monkeypatch.delenv(outbound.REFUSE_ENV, raising=False)
        watched = Watched()
        watched.install(monkeypatch)
        if refuse:
            assert outbound.install_if_requested() is True
        config = build_web_config(root / "store.sqlite3")
        assert config is not None
        handler = type(
            "H", (ConnectionHandler,), {"config": config, "session": AuthorisationSession()}
        )
        httpd = HTTPServer(("127.0.0.1", 0), handler)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        servers.append(httpd)
        return f"http://127.0.0.1:{httpd.server_port}", watched

    servers: list[HTTPServer] = []
    yield start
    for httpd in servers:
        httpd.shutdown()
    outbound.uninstall()


class TestPressingConnectInTheHarness:
    def test_Connect_InTheHarness_OpensNoSocketToAnythingButThisMachine(self, harness_server):
        base, watched = harness_server(refuse=True)

        answer = httpx.get(f"{base}/connect", params={"name": "probe"}, timeout=60)

        assert answer.status_code in (200, 302)
        assert watched.outside == [], f"the harness reached {watched.outside}"

    def test_Connect_WithoutTheHarnessGuard_DoesReachForTheProvider(self, harness_server):
        """The watch can fail: with the switch off, the same press does ask for the provider."""
        base, watched = harness_server(refuse=False)

        httpx.get(f"{base}/connect", params={"name": "probe"}, timeout=60)

        assert any("truelayer" in host for host in watched.outside), watched.hosts
