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

import os
from dataclasses import dataclass
from pathlib import Path

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
            read_secret(name)
        except SecretError as exc:
            results.append(CheckResult(f"secret {name}", False, str(exc)))
        else:
            results.append(CheckResult(f"secret {name}", True, f"{name} is readable"))

    return results


def report(results: list[CheckResult]) -> str:
    lines = [("  ok   " if r.ok else "  FAIL ") + f"{r.name}: {r.detail}" for r in results]
    failures = sum(1 for r in results if not r.ok)
    lines.append("")
    lines.append(
        "All checks passed." if not failures else f"{failures} check(s) failed - see above."
    )
    return "\n".join(lines)
