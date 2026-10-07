"""Secrets by indirection: config holds paths, not values.

Every secret can be supplied two ways:

    TRUELAYER_CLIENT_SECRET       the value itself
    TRUELAYER_CLIENT_SECRET_FILE  a path to a file containing the value

The file form is preferred and takes precedence. It means `.env` holds only
paths, so the config file can be read, pasted into a chat, or attached to a bug
report without leaking anything. It is the same convention Docker and
Kubernetes use for mounted secrets, so it composes with those later.

Nothing here logs or echoes a secret value.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


class SecretError(RuntimeError):
    """Raised when a secret is missing, unreadable or empty."""


def read_secret(name: str, *, required: bool = True) -> str:
    """Resolve a secret from `<NAME>_FILE` if set, otherwise `<NAME>`.

    Trailing newlines are stripped, because an editor that helpfully adds one
    would otherwise produce a credential that fails authentication with no
    visible difference between the correct and incorrect value.
    """
    path_variable = f"{name}_FILE"
    path_value = os.getenv(path_variable, "").strip()

    if path_value:
        path = Path(path_value)
        try:
            if not path.is_file():
                raise SecretError(f"{path_variable} points at {path}, which does not exist")
            value = path.read_text(encoding="utf-8").strip()
        except OSError as exc:
            # Being refused is a configuration fault like any other here, and it
            # must arrive as one. Left to escape, it surfaces as a traceback
            # through pathlib internals - which names the failing library call
            # rather than the path, and reads like a bug in this code rather
            # than a file the process is not allowed to open. Containers make
            # this the commonest deployment fault: the path is right, the file
            # is right, and the uid is not.
            raise SecretError(
                f"{path_variable} points at {path}, which could not be read: "
                f"{exc.strerror or exc}. Check the file and its parent directory "
                "are readable by the user this process runs as - under a "
                "container that is the image's user, not the one that owns the "
                "files on the host."
            ) from exc
        if not value:
            raise SecretError(f"{path_variable} points at {path}, which is empty")
        return value

    value = os.getenv(name, "").strip()
    if value:
        return value

    if required:
        raise SecretError(
            f"Set {path_variable} to a file containing the secret (preferred), "
            f"or {name} to the value itself."
        )
    return ""


@dataclass(frozen=True)
class Readiness:
    """Whether a provider is configured, and what is wrong if it is half done.

    Three states, because two would force the interesting case into the wrong one:

        ready         everything needed is present and readable
        absent        nothing is configured - a decision, not a fault
        misconfigured something is configured and something else is missing
    """

    state: str
    problem: str | None = None

    @property
    def usable(self) -> bool:
        return self.state == "ready"


def truelayer_readiness() -> Readiness:
    """Decide whether bank authorisation can be offered, and say so precisely.

    The rule is the one an MOT uses: if it is fitted, it has to work. Absent means
    the deployment has said it does not want this, and everything that needs no bank
    connection - statements, imports, matching, categorisation, coverage - carries
    on without it.

    INTENT IS READ FROM THE IDENTIFIERS, not from the secret. The client id and
    redirect URI are inert on their own, they are what a person sets when deciding
    to use a provider, and they live in plain configuration. The secret cannot serve
    as the signal, because deployments point every instance at the same secret PATH
    from one shared template - so the pointer's existence says nothing about intent,
    and only the file behind it says the intent was acted on. Judging by the pointer
    refuses to start an instance that was deliberately given no credentials at all.

    Once the identifiers ARE set, the secret must be present and readable. A
    half-applied rotation is a fault, and degrading quietly would surface it only
    when somebody tried to authorise a bank - the worst possible moment to find out.
    """
    client_id = os.getenv("TRUELAYER_CLIENT_ID", "").strip()
    redirect_uri = os.getenv("TRUELAYER_REDIRECT_URI", "").strip()

    if not client_id and not redirect_uri:
        return Readiness("absent")

    missing = [
        name
        for name, value in (
            ("TRUELAYER_CLIENT_ID", client_id),
            ("TRUELAYER_REDIRECT_URI", redirect_uri),
        )
        if not value
    ]
    if missing:
        return Readiness(
            "misconfigured",
            f"{' and '.join(missing)} is not set, but the other half of the "
            "provider's configuration is. The redirect URI must be reachable from "
            "the phone AND registered with the provider byte for byte. Set both to "
            "use a bank connection, or neither to run without one.",
        )

    try:
        read_secret("TRUELAYER_CLIENT_SECRET")
    except SecretError as exc:
        return Readiness(
            "misconfigured",
            f"{exc} The identifiers are set, so this deployment means to use a bank "
            "connection - clear them both to run without one instead.",
        )

    return Readiness("ready")


def describe_source(name: str) -> str:
    """Say where a secret would be read from, without revealing it.

    Useful in diagnostics: the commonest configuration failure is a secret
    resolving from an unexpected place, and that can be reported safely.
    """
    path_value = os.getenv(f"{name}_FILE", "").strip()
    if path_value:
        return f"file {path_value}"
    return "inline environment variable" if os.getenv(name, "").strip() else "unset"
