"""Pre-flight configuration check: fail here, loudly, rather than later, obscurely.

Every fault this catches shares a shape. The process starts cleanly, reports
nothing wrong, and dies at the first operation that touches the misconfigured
thing - by which point the traceback names a library call rather than the cause.
A container that cannot read its secrets does not say "I cannot read my
secrets"; it exits on a `PermissionError` from inside `pathlib`, restarts, and
does it again, while every layer above reports a successful deployment.

The checks are therefore deliberately about ACCESS, not just presence. Knowing a
variable is set proves nothing: the interesting failures are a path that exists
but is owned by another user, a directory the process cannot write to, a secret
file mounted from a host tree whose ownership does not match the image's uid.
Each is invisible to a configuration parser and obvious to a stat.

Nothing here reveals a secret's value - only whether it can be read at all, so
the output is safe to paste into a bug report.
"""

from __future__ import annotations

import contextlib
import os
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

import httpx

from .secrets import SecretError, read_secret

# Where a secret comes from is checked only when the deployment claims to have
# one. An absent Starling token is a legitimate configuration (not everyone
# banks there), so its absence is not a fault - being unreadable is.
OPTIONAL_SECRETS = ("TRUELAYER_CLIENT_SECRET", "STARLING_PERSONAL_ACCESS_TOKEN")


@dataclass(frozen=True)
class CheckResult:
    name: str
    ok: bool
    detail: str


def _writable_directory(label: str, variable: str, value: str) -> CheckResult:
    """Whether the process can actually create the file it will need to write.

    Checks the PARENT, because the file itself legitimately may not exist yet on
    a first run. A directory that is missing, or present but not writable by
    this user, is the fault worth catching - and the second is invisible to any
    check that only asks whether the path is configured.
    """
    parent = Path(value).expanduser().parent
    if not parent.is_dir():
        return CheckResult(label, False, f"{variable}={value} - directory {parent} does not exist")
    if not os.access(parent, os.W_OK | os.X_OK):
        return CheckResult(
            label,
            False,
            f"{variable}={value} - {parent} is not writable by uid {os.getuid()}"
            if hasattr(os, "getuid")
            else f"{variable}={value} - {parent} is not writable by this user",
        )
    return CheckResult(label, True, f"{variable}={value}")


def run_checks() -> list[CheckResult]:
    """Every check, run in full - never stopping at the first failure.

    Reporting one fault at a time turns a single misconfigured deployment into
    as many restart-and-look cycles as it has mistakes, which on a remote host
    is the difference between one round trip and five.
    """
    results: list[CheckResult] = []

    for variable, label in (
        ("OBDI_DB_PATH", "transaction store"),
        ("OBDI_CONNECTION_STORE", "connection store"),
    ):
        value = os.getenv(variable, "").strip()
        if not value:
            results.append(
                CheckResult(label, False, f"{variable} is not set - the app cannot run without it")
            )
            continue
        results.append(_writable_directory(label, variable, value))

    account_map = os.getenv("OBDI_ACCOUNT_MAP", "").strip()
    if account_map and not Path(account_map).expanduser().exists():
        # Not fatal: an absent map means accounts stay source-qualified rather
        # than cross-checking. Worth saying, because the symptom otherwise is
        # duplicated accounts appearing gradually, which reads as a matching bug.
        results.append(
            CheckResult(
                "account map",
                True,
                f"OBDI_ACCOUNT_MAP={account_map} does not exist yet - accounts will "
                "stay source-qualified and will not cross-check",
            )
        )

    for name in OPTIONAL_SECRETS:
        configured = os.getenv(f"{name}_FILE", "").strip() or os.getenv(name, "").strip()
        if not configured:
            continue
        try:
            value = read_secret(name)
        except SecretError as exc:
            results.append(CheckResult(f"secret {name}", False, str(exc)))
            continue
        problems = shape_problems(name, value)
        if problems:
            results.append(CheckResult(f"secret {name}", False, "; ".join(problems)))
        else:
            results.append(
                CheckResult(f"secret {name}", True, f"{name} is readable and well-formed")
            )

    return results


def shape_problems(name: str, value: str) -> list[str]:
    """What is wrong with a secret's SHAPE, described without its content.

    Readable is not usable. Wrapping quotes survive a YAML paste invisibly -
    nothing ever prints a secret, so no eye catches them - and a value missing
    the provider's prefix is almost certainly the secret's IDENTIFIER, shown
    forever in the console, rather than its value, shown exactly once at
    creation. Both fail hours later as a bare HTTP 400 at the worst possible
    moment: after a bank authorisation, with the single-use code already burnt.

    Every message here names the class of problem and nothing else. Printing
    even a fragment of the value would put a credential in a report designed
    to be pasteable.
    """
    problems = []
    if value != value.strip():
        problems.append("has leading or trailing whitespace")
    if value and (value[0] in "\"'" or value[-1] in "\"'"):
        problems.append(
            "is wrapped in quote characters - remove the quotes in the vault or "
            "secret file; they were stored as part of the value"
        )
    elif any(ch.isspace() for ch in value):
        problems.append(
            "contains whitespace inside the value - most likely a line-wrapped paste"
        )

    if name == "TRUELAYER_CLIENT_SECRET" and value:
        # Judged on the core value: quote and whitespace problems are already
        # reported above; the prefix question is about what the value IS once
        # those wrappers are peeled off.
        stripped = value.strip().strip('"\'')
        if stripped.startswith("tlcs_sandbox_"):
            live_client = not os.getenv("TRUELAYER_CLIENT_ID", "").startswith("sandbox-")
            if live_client:
                problems.append(
                    "is a SANDBOX secret (tlcs_sandbox_) paired with a live client id - "
                    "sandbox and live are separate environments with separate credentials"
                )
        elif not stripped.startswith("tlcs_live_"):
            problems.append(
                "does not start with tlcs_live_ - this looks like the secret's "
                "IDENTIFIER rather than its value. The console shows identifiers "
                "forever but reveals the value only once, at creation; if the value "
                "was not captured then, create a new secret and store that"
            )
    return problems


def live_checks(client: httpx.Client | None = None) -> list[CheckResult]:
    """Ask the provider whether the credentials are actually valid.

    Separate from run_checks and opt-in, because it talks to the network:
    doctor's offline checks must stay runnable anywhere, any time, with no
    side effects beyond a stat.

    The instrument is a client_credentials grant - authentication with no user
    in the loop. The reading is deliberately asymmetric: only an explicit
    invalid_client condemns the pair, because scope and grant refusals happen
    AFTER authentication - a provider that names the scope it dislikes has, in
    the same breath, accepted the secret. And a network failure is reported as
    inconclusive, never as a verdict: condemning a valid credential because the
    connection dropped would send someone off to rotate a secret that works.
    """
    client_id = os.getenv("TRUELAYER_CLIENT_ID", "").strip()
    if not client_id:
        return []
    try:
        secret = read_secret("TRUELAYER_CLIENT_SECRET", required=False)
    except SecretError:
        return []  # the offline checks already report unreadable secrets
    if not secret:
        return []

    http = client or httpx.Client(timeout=15.0)
    label = "TrueLayer credentials (live)"
    try:
        response = http.post(
            "https://auth.truelayer.com/connect/token",
            data={
                "grant_type": "client_credentials",
                "client_id": client_id,
                "client_secret": secret,
                "scope": "info",
            },
        )
    except httpx.HTTPError as exc:
        return [
            CheckResult(
                label,
                True,
                f"inconclusive - the provider could not be reached ({type(exc).__name__}); "
                "this says nothing about the credentials either way",
            )
        ]

    if response.status_code == 200:
        return [CheckResult(label, True, "the provider accepted the id and secret")]

    body = response.text[:200]
    if "invalid_client" in body:
        return [
            CheckResult(
                label,
                False,
                f"the provider rejected the pair: {body} - the secret does not match "
                "this client id. Re-enter the value, or create a fresh secret in the "
                "console (its value is shown exactly once, at creation)",
            )
        ]
    return [
        CheckResult(
            label,
            True,
            f"authenticated - the provider refused only the grant or scope ({body}), "
            "which is decided after the secret was accepted",
        )
    ]


def report(results: list[CheckResult]) -> str:
    lines = [("  ok   " if r.ok else "  FAIL ") + f"{r.name}: {r.detail}" for r in results]
    failures = sum(1 for r in results if not r.ok)
    lines.append("")
    lines.append(
        "All checks passed." if not failures else f"{failures} check(s) failed - see above."
    )
    return "\n".join(lines)


def rebuild_check(runs: Sequence[Mapping[str, object]]) -> CheckResult:
    """Did the last rebuild work? The deploy gates on this.

    A converge asserts the container is healthy, runs the expected image and
    reports its version. It says nothing about the rebuild the deploy itself
    triggers, which starts afterwards in the background - so on 2026-08-13 two
    deploys reported complete success while the instance rebuilt into an empty
    derived layer, and it was found two and a quarter hours later by eye.

    Reporting that to a phone is the WEAKEST available place to catch it. This
    is the strongest: `doctor` exits non-zero on any failed check, a converge
    runs it, and the deploy refuses to finish.

    STRICT ON PURPOSE - any failed rebuild fails, not only one that emptied the
    store. A check earns relaxation from evidence that its strictness costs
    more than it catches; starting lenient pays for that calibration with an
    incident instead. The detail separates an emptied store from a partial one,
    because severity is what a reader needs even when the verdict is the same.

    Only the latest run counts. A failure already recovered from must not fail
    every later deploy, which is how a gate gets routed around rather than
    fixed.
    """
    if not runs:
        return CheckResult(
            name="last rebuild",
            ok=True,
            detail=(
                "no rebuild recorded yet - a fresh store rather than a healthy "
                "one, and the two are different states"
            ),
        )
    latest = runs[0]
    finished = str(latest.get("finished_at") or "")
    build = str(latest.get("build") or "unknown build")
    # The stored columns are nullable and arrive as `object`, and a run that
    # died before counting anything stored None rather than 0 - which is why
    # `or 0` is here rather than a cast that would raise on it.
    replayed = int(str(latest.get("artefacts_replayed") or 0))
    resolved = int(str(latest.get("transactions") or 0))
    if latest.get("ok"):
        return CheckResult(
            name="last rebuild",
            ok=True,
            detail=(
                f"succeeded at {finished} ({build}): {replayed:,} artefact(s) "
                f"replayed, {resolved:,} row resolutions"
            ),
        )
    reason = str(latest.get("summary") or "").strip() or "no reason recorded"
    if not replayed and not resolved:
        state = (
            "the derived layer is EMPTY - a rebuild wipes before replaying and "
            "this run replayed nothing"
        )
    else:
        state = (
            f"it replayed {replayed:,} artefact(s) before failing, so the store "
            "holds part of its data and looks healthy"
        )
    return CheckResult(
        name="last rebuild",
        ok=False,
        detail=f"FAILED at {finished} ({build}): {state}. {reason}",
    )


def collision_checks(
    store: object, connection_ids: Iterable[str] = ()
) -> list[CheckResult]:
    """Look for namespace collisions in the data that is already there.

    Validators refuse new mistakes; they cannot un-write old ones. These
    read the live store for the same classes the registry prevents, so a
    collision that predates the rule is visible rather than assumed
    absent - and so is drift, where evidence carries a source name no
    part of the code declares any more.
    """
    from .namespaces import FIRST_PARTY_CONNECTION_IDS, PROVIDERS, SOURCES

    results: list[CheckResult] = []
    connection = getattr(store, "connection", None)
    if connection is None:
        return results

    unknown: set[str] = set()
    for table in ("raw_artefacts", "fetch_attempts"):
        with contextlib.suppress(Exception):
            unknown |= {
                str(row[0])
                for row in connection.execute(f"SELECT DISTINCT source FROM {table}")  # noqa: S608
                if str(row[0]) not in SOURCES
            }
    results.append(
        CheckResult(
            name="sources are registered",
            ok=not unknown,
            detail=(
                "every source in the store is declared in namespaces.SOURCES"
                if not unknown
                else f"evidence carries undeclared source(s): {sorted(unknown)} - "
                "either the registry is stale or a typo shipped"
            ),
        )
    )

    names = set(connection_ids)
    shared = names & set(FIRST_PARTY_CONNECTION_IDS)
    results.append(
        CheckResult(
            name="connection ids are unshared",
            ok=not shared,
            detail=(
                "no connection carries a first-party ledger id"
                if not shared
                else f"connection(s) {sorted(shared)} share an id with a "
                "first-party path - their ledger rows and quota arithmetic "
                "are mixed. Rename with 'obdi rename-connection'"
            ),
        )
    )

    suspicious: set[str] = set()
    with contextlib.suppress(Exception):
        suspicious = {
            str(row[0])
            for row in connection.execute(
                "SELECT DISTINCT account_id FROM transactions"
            )
            if ":" in str(row[0]) and str(row[0]).split(":")[0] in PROVIDERS
        }
    results.append(
        CheckResult(
            name="accounts are named, not referenced",
            ok=True,
            detail=(
                "every account resolves to a canonical name"
                if not suspicious
                else f"{len(suspicious)} account(s) still hold a provider "
                "reference rather than a name - bind them so the map, not "
                "the provider, decides what they are called"
            ),
        )
    )

    # Hand-work that has been LOST, which is the quietest fault this store can
    # have. An annotation whose transaction no longer exists looks like nothing at
    # all from every other angle: the row simply reads as uncategorised, which is
    # exactly what a row nobody has reached yet looks like. Only this count tells
    # those two apart.
    #
    # The count itself has existed and been tested since it was written, and
    # nothing ever called it - the same fault as a guard that is never registered,
    # and unnoticed for precisely the reason it was built: the symptom is silence.
    #
    # Every table keyed to a transaction, not only annotations. Annotations were
    # where the first one was found, and writing the check for that instance
    # rather than for the class left a review verdict, a confirmed transfer pair
    # and an unsent event able to go the same way unremarked. The registry that
    # carries these rows across an account rename already knows the full set.
    orphans: dict[str, int] = {}
    # Reached the same way as `connection` above: this takes a plain object so
    # that the doctor does not import the store, and so a caller passing
    # something store-shaped still gets every check it can answer.
    count_orphans = getattr(store, "orphaned_entity_rows", None)
    if callable(count_orphans):
        with contextlib.suppress(Exception):
            orphans = dict(count_orphans())
    lost = {column: count for column, count in orphans.items() if count}
    results.append(
        CheckResult(
            name="work attached to transactions points at transactions that exist",
            ok=not lost,
            detail=(
                f"nothing stranded across {len(orphans)} entity-keyed column(s)"
                if not lost
                # Named per table rather than totalled: the number says how much
                # was lost, the name says what KIND of work it was, and they
                # imply different remedies.
                else "; ".join(
                    f"{count} in {column}" for column, count in sorted(lost.items())
                )
                + " - each names a transaction that no longer exists, so the work "
                "reads as never done rather than lost"
            ),
        )
    )
    return results
