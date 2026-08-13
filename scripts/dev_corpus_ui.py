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

Usage:
    python scripts/dev_corpus_ui.py                 # rebuild and serve
    python scripts/dev_corpus_ui.py --keep          # keep an existing store
    python scripts/dev_corpus_ui.py --seed 12345    # a different world

Runs in the foreground; Ctrl-C stops it. Nothing here touches a real store: the
corpus is generated from a seed into the directory given by --at, which defaults
to a scratch path outside the repository.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))

from obdi.synthetic import build_world, write_corpus  # noqa: E402

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
    credentials are overwritten with values that cannot work. Nothing here
    should be able to contact a bank even if a button is pressed by accident.
    `capture_screens.py` has done this since it was written; this did not.
    """
    return {
        **os.environ,
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
        default=Path(tempfile.gettempdir()) / "obdi-dev-corpus",
        help="where to build the corpus and store (never a real store)",
    )
    parser.add_argument(
        "--keep",
        action="store_true",
        help="serve the existing store rather than rebuilding it, so anything "
        "answered in the interface survives",
    )
    arguments = parser.parse_args()

    root = arguments.at
    store = root / "store.sqlite3"

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
    print("\nCtrl-C to stop.\n")
    run(store, "serve", "--port", str(arguments.port))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
