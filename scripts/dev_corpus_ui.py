"""Serve the generated corpus in the real application, for looking at.

Every pattern feature in this project is asserted against the generated corpus,
whose right answers are written down in its manifest. Those assertions all run
at library level, and a passing library test is not evidence that a person can
SEE the right thing. This puts the same corpus behind the real server so the
remaining question can be asked by eye or by a browser harness.

TWO THINGS ARE ENCODED HERE BECAUSE BOTH HAVE ALREADY COST TIME.

THE STORE IS BUILT THROUGH `import`, never by calling the reconcile function.
That is not a style preference. The reconcile path fills the derived layer and
not the raw artefact layer; the application rebuilds from raw at startup; so a
store built the short way is EMPTIED the moment it is served. Measured 2026-08-12:
70 rows to 0, reported as "VANISHED - check problems and layer 0". Seventeen
green tests were building stores that way, and none of them could have noticed.

THE PORT IS FIXED AND UNUSUAL. Browser permissions are granted per origin, so a
port that moves means granting again every session. 8080 collides with
everything; 38080 sits below the Windows ephemeral range (49152+) where nothing
will transiently take it.

TWO RUNS MUST NOT SHARE A DIRECTORY. The store directory used to default to one fixed
path and to be deleted at the start of every run, so a second run rewrote the store under
a server that was still up, and a reviewer saw three pages disagree for six seconds. Each run
now builds in a directory of its own (the port and a random suffix are in its name), and
a directory that a live server holds is refused before anything in it is deleted
(`live_server_in` says how that is detected).

Usage:
    python scripts/dev_corpus_ui.py                 # rebuild and serve, in a new directory
    python scripts/dev_corpus_ui.py --at DIR --keep # serve the store already in DIR
    python scripts/dev_corpus_ui.py --seed 12345    # a different world

Runs in the foreground; Ctrl-C stops it. Nothing here touches a real store: the
corpus is generated from a seed into a new scratch directory outside the repository,
or into the directory given by --at.
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import shutil
import socket
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))

from obdi.ingest.synthetic import build_world, write_corpus  # noqa: E402

#: Fixed on purpose - see the module docstring. Changing it means re-granting
#: browser permissions, so it is plumbing rather than a per-run choice.
PORT = 38080

#: Which artefacts to land, and against which account. The transposed delivery
#: is included so the agreement page has its alarm to lead with; the misfiled
#: one is NOT, because it would make every page open on a fault and the point
#: here is to look at ordinary output as well as at findings.
LANDINGS = [
    ("synthetic-current.csv", "synthetic-current"),
    ("synthetic-savings.csv", "synthetic-savings"),
    ("synthetic-current-transposed.csv", "synthetic-current"),
]

#: The card's statements are landed too, taken from the manifest rather than
#: listed here: there is one a month, so a fixed list would silently stop
#: covering the corpus the moment its length changed. Without them the demo
#: shows no card at all - an account reachable only by statement, which is the
#: whole reason it exists - and the pages that read a statement's balances and
#: terms have nothing to display.


#: Written beside the store while a server runs from it, and removed when it stops. A directory
#: holding one whose process is alive, or whose port answers, is not this run's to delete.
LOCK_NAME = "server.json"


def default_root(port: int) -> Path:
    """A directory no other run shares: the port, and a random suffix for a second run on it."""
    return Path(tempfile.gettempdir()) / f"obdi-dev-corpus-{port}-{secrets.token_hex(4)}"


def pid_alive(pid: int) -> bool:
    """Whether a process with this id is running."""
    if pid <= 0:
        return False
    if sys.platform == "win32":
        import ctypes

        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        handle = kernel32.OpenProcess(0x1000, 0, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return False
        try:
            code = ctypes.c_ulong()
            return bool(kernel32.GetExitCodeProcess(handle, ctypes.byref(code))) and (
                code.value == 259  # STILL_ACTIVE
            )
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def port_answers(port: int) -> bool:
    """Whether something is listening on this port of this machine."""
    with socket.socket() as probe:
        probe.settimeout(0.5)
        return probe.connect_ex(("127.0.0.1", port)) == 0


def live_server_in(root: Path) -> str | None:
    """Why `root` must not be rebuilt or deleted now, or None when nothing holds it.

    Two things are read, because either alone has a way to be wrong. The lock file the running
    harness wrote says which process serves the directory and on which port: a live process or
    an answering port means a server is up, and a lock whose process is gone and whose port is
    silent is stale and ignored. And the store itself is asked for its write lock without
    waiting: a server in the middle of a write, or any other tool holding the store, makes that
    fail even where no lock file was ever written.
    """
    lock = root / LOCK_NAME
    if lock.is_file():
        try:
            held = json.loads(lock.read_text(encoding="utf-8"))
            pid, port = int(held["pid"]), int(held["port"])
        except (ValueError, KeyError, TypeError, OSError):
            return f"{lock} exists and cannot be read, so whether a server holds it is unknown"
        if pid_alive(pid):
            return f"process {pid} is serving it (named in {lock})"
        if port_answers(port):
            return f"something answers on port {port}, which {lock} says serves it"
    store = root / "store.sqlite3"
    if store.is_file():
        probe = sqlite3.connect(store, timeout=0.2)
        try:
            probe.execute("BEGIN EXCLUSIVE")
            probe.execute("ROLLBACK")
        except sqlite3.OperationalError as exc:
            return f"the store is locked by another process ({exc})"
        finally:
            probe.close()
    return None


def isolated(root: Path) -> dict[str, str]:
    """An environment that CANNOT reach a real connection or credential.

    The repository carries a gitignored .env which the command line loads
    automatically, and it names the live connection store, the live account map
    and the paths to real credentials. Passing --db redirects the transactions
    and nothing else, so a demo served without this shows synthetic rows beside
    REAL connection names - and its "reconnect" button starts a real
    authorisation against a real bank. Found by looking at the page: a
    connection nothing in the corpus could explain was sitting at the top of it.

    So every path the app reads is pointed at the scratch directory, and the
    credentials are overwritten with values that cannot work. Overwriting a
    credential makes a request FAIL, though, not not happen: pressing Connect ran
    the credentials' pre-flight check, which posted the dummy id and secret to the
    provider's real token endpoint. So the claim is made true by `obdi.outbound`:
    `OBDI_REFUSE_OUTBOUND` makes every obdi process started from here refuse any
    connection or name lookup that is not to this machine
    (tests/test_dev_harness.py presses Connect and watches the sockets).
    `capture_screens.py` has done the first part since it was written; this did not.
    """
    return {
        **os.environ,
        "OBDI_REFUSE_OUTBOUND": "1",
        # This checkout's code, not whichever copy the interpreter's editable
        # install points at: a demo served from a worktree showed the main
        # checkout's pages, and a page under development was a 404.
        "PYTHONPATH": os.pathsep.join(
            [str(REPO / "src"), *filter(None, [os.environ.get("PYTHONPATH")])]
        ),
        "OBDI_DB_PATH": str(root / "store.sqlite3"),
        "OBDI_CONNECTION_STORE": str(root / "connections.json"),
        "OBDI_ACCOUNT_MAP": str(root / "accounts.json"),
        "OBDI_RAW_DIR": str(root / "raw"),
        # Present but useless. Absent would send the app down its
        # not-configured path, which is a different page from the one being
        # looked at; wrong is more faithful than missing here.
        "TRUELAYER_CLIENT_ID": "dev-corpus",
        "TRUELAYER_CLIENT_SECRET": "dev-corpus",
        "TRUELAYER_CLIENT_SECRET_FILE": "",
        "TRUELAYER_REDIRECT_URI": "http://127.0.0.1/callback",
        "STARLING_PERSONAL_ACCESS_TOKEN_FILE": "",
        "ACTUAL_SERVER_URL": "",
        "ACTUAL_PASSWORD_FILE": "",
        "ACTUAL_SYNC_ID": "",
        "EB_APPLICATION_ID": "",
        "EB_PRIVATE_KEY_PATH": "",
    }


def seed_position(store_path: Path) -> None:
    """Invented balances and assets, so `/position` has something to show.

    The current account is given a stated balance and then a later one that
    does not match its rows, so the page shows a counted account flagged for
    differing checks. The savings account is deliberately left with no balance
    stated, so the page shows an account that is not counted. The two assets
    start in different months, so the history has a partial period before both
    are observed. Every figure is invented.
    """
    from datetime import date

    from obdi.balance_anchors import record_stated_anchor
    from obdi.ingest.store import Store
    from obdi.ingest.valuations import Asset, AssetKind, record_observation

    with Store(store_path) as store:
        record_stated_anchor(store, "synthetic-current", "2026-03-31", "2310.45")
        record_stated_anchor(store, "synthetic-current", "2026-06-30", "3055.10")
        pension = Asset("demo-workplace-pension", AssetKind.DEFINED_CONTRIBUTION)
        for when, pounds in (
            (date(2026, 1, 31), 18200),
            (date(2026, 2, 28), 18650),
            (date(2026, 3, 31), 18140),
            (date(2026, 4, 30), 19020),
            (date(2026, 5, 31), 19480),
            (date(2026, 6, 30), 19910),
        ):
            record_observation(
                store, pension, observed_at=when, source="statement", value_minor=pounds * 100
            )
        fund = Asset("demo-index-fund", AssetKind.INVESTMENT)
        for when, pounds in (
            (date(2026, 3, 31), 6400),
            (date(2026, 5, 31), 6950),
            (date(2026, 6, 30), 6720),
        ):
            record_observation(
                store, fund, observed_at=when, source="statement", value_minor=pounds * 100
            )
        record_observation(
            store,
            Asset("demo-state-pension", AssetKind.STATE_PENSION),
            observed_at=date(2026, 6, 1),
            source="forecast",
            annual_income_minor=1150000,
        )


def run(store: Path, *arguments: str) -> None:
    """One obdi command, through the same door a person uses."""
    completed = subprocess.run(  # noqa: S603 - fixed argv, no shell
        [sys.executable, "-m", "obdi.cli", "--db", str(store), *arguments],
        cwd=str(REPO),
        env=isolated(store.parent),
        check=False,
    )
    if completed.returncode != 0:
        raise SystemExit(f"obdi {' '.join(arguments)} failed ({completed.returncode})")


def main() -> int:
    # Line-buffered, because this script's own output interleaves with the
    # output of the obdi commands it runs. Block buffering sends every print
    # here to the back of the queue, so the first run of this script showed
    # three import summaries before saying what was being built, and the
    # manifest's expected answers never appeared in a readable order.
    sys.stdout.reconfigure(line_buffering=True)

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", type=int, default=20260812)
    parser.add_argument("--months", type=int, default=6)
    parser.add_argument("--port", type=int, default=PORT)
    parser.add_argument(
        "--at",
        type=Path,
        default=None,
        help="where to build the corpus and store (never a real store). Default: a new "
        "directory for this run alone, named for the port. TWO RUNS MUST NOT SHARE ONE: a "
        "directory a live server holds is refused, and is never rebuilt under it",
    )
    parser.add_argument(
        "--keep",
        action="store_true",
        help="serve the existing store in --at rather than rebuilding it, so anything "
        "answered in the interface survives (needs --at: a default directory is new every run)",
    )
    arguments = parser.parse_args()

    if arguments.at is None:
        if arguments.keep:
            raise SystemExit(
                "--keep needs --at: the default directory is new for every run, so there is "
                "no store of an earlier run to keep"
            )
        root = default_root(arguments.port)
    else:
        root = arguments.at
    store = root / "store.sqlite3"

    held = live_server_in(root) if root.exists() else None
    if held is not None:
        raise SystemExit(
            f"refusing to use {root}: {held}. Stop that server, or give this run a directory of "
            "its own (leave --at out)."
        )

    if not arguments.keep:
        if root.exists():
            shutil.rmtree(root)
        root.mkdir(parents=True)
        world = build_world(seed=arguments.seed, months=arguments.months)
        manifest = write_corpus(world, root / "corpus")
        print(f"corpus: seed {arguments.seed}, {manifest['totals']['events']} events")
        landings = [
            *LANDINGS,
            *(
                (statement["name"], statement["account"])
                for statement in manifest["statements"]
            ),
        ]
        for filename, account in landings:
            run(store, "import", str(root / "corpus" / filename), "--account", account)
        seed_position(store)
        expected = manifest["ambiguity"]["expected_flags_total"]
        print(f"\nthe manifest says to expect {expected} review flag(s):")
        for planted in ("standing_order", "duplicate_report"):
            entry = manifest["ambiguity"][planted]
            print(f"  {entry['expected_flags']}  {entry['description']} - {entry['why']}")
        print("\nand one planted date fault:")
        for delivery in manifest["deliveries"]:
            if "transposed" in delivery["fault"]:
                print(f"  {delivery['fault']}")
    elif not store.exists():
        raise SystemExit(f"--keep was given but there is no store at {store}")

    print(f"\nserving {store}")
    print(f"  http://127.0.0.1:{arguments.port}/            connections")
    print(f"  http://127.0.0.1:{arguments.port}/review      the rule-writing worklist")
    print(f"  http://127.0.0.1:{arguments.port}/agreements  cross-source agreement")
    print(f"  http://127.0.0.1:{arguments.port}/position    balances, assets, net worth")
    print("\nCtrl-C to stop.\n")
    lock = root / LOCK_NAME
    lock.write_text(json.dumps({"pid": os.getpid(), "port": arguments.port}), encoding="utf-8")
    try:
        run(store, "serve", "--port", str(arguments.port))
    finally:
        lock.unlink(missing_ok=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
