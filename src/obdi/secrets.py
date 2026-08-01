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
        if not path.is_file():
            raise SecretError(f"{path_variable} points at {path}, which does not exist")
        value = path.read_text(encoding="utf-8").strip()
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


def describe_source(name: str) -> str:
    """Say where a secret would be read from, without revealing it.

    Useful in diagnostics: the commonest configuration failure is a secret
    resolving from an unexpected place, and that can be reported safely.
    """
    path_value = os.getenv(f"{name}_FILE", "").strip()
    if path_value:
        return f"file {path_value}"
    return "inline environment variable" if os.getenv(name, "").strip() else "unset"
