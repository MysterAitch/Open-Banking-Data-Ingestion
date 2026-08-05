"""Whether the code that derives the store is the code that derived it.

Rebuild triggers are deploy-time events: the matching rules, the
parsers, the identity logic all change only when new code arrives. Yet
the rebuild itself was a button - so a deploy that changed the rules
left the stored data derived under the OLD rules until somebody
remembered, and nothing anywhere said so. This module closes that gap:
the package's source is fingerprinted, a successful rebuild stamps the
fingerprint it ran under into the store, and serve compares the two at
startup. A mismatch starts the ordinary background rebuild behind the
ordinary banner; a match does nothing, so restarts and .env tweaks stay
free.

The fingerprint is the WHOLE package, deliberately. A curated list of
"files that affect derivation" is a list that goes stale silently - the
exact failure mode the namespace registry work already met twice - and
the cost of over-triggering is a few seconds of rebuild inside a deploy
that already takes forty. Omission is impossible; the price is noise
that hides inside existing noise.

Fails closed by construction: no stamp reads as "never rebuilt under any
known code", which triggers a rebuild, which writes the stamp.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from .store import Store

_STAMP_KEY = "derived_code_fingerprint"


def code_fingerprint(package_dir: Path | None = None) -> str:
    """One hash over every source file in the package, path-sensitive.

    Renames and deletions change the hash too - a file's PLACE in the
    package is part of what the code is, and matching once broke on
    exactly the kind of moved-logic change a content-only hash misses.
    """
    root = package_dir if package_dir is not None else Path(__file__).parent
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*.py")):
        digest.update(str(path.relative_to(root)).replace("\\", "/").encode())
        digest.update(b"\x00")
        digest.update(path.read_bytes())
        digest.update(b"\x00")
    return digest.hexdigest()


def stored_fingerprint(store: Store) -> str | None:
    row = store.connection.execute(
        "SELECT value FROM obdi_meta WHERE key = ?", (_STAMP_KEY,)
    ).fetchone()
    return str(row[0]) if row is not None else None


def stamp_fingerprint(store: Store, value: str) -> None:
    """Record which code the derived layer was last rebuilt under.

    Written only after a SUCCESSFUL rebuild, by the rebuild itself - a
    failed rebuild leaves the old stamp, so the next startup tries
    again rather than believing the store is current.
    """
    store.connection.execute(
        "INSERT INTO obdi_meta (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (_STAMP_KEY, value),
    )
    store.connection.commit()


def rebuild_needed(store: Store) -> bool:
    """True when the stored data was derived by different code than this.

    No stamp counts as needing one: an unstamped store has never proven
    which rules derived it, and the cost of the answer is one rebuild.
    """
    return stored_fingerprint(store) != code_fingerprint()
