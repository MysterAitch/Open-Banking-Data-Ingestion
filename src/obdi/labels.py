"""Human names for canonical refs, from layer 0 alone.

Extracted from the serve closure so the push-to-Actual envelope builder can
name provisioned accounts the same way the pages do - one naming rule,
however the name is consumed.
"""

from __future__ import annotations

import contextlib
import json

from .accounts import AccountMap
from .store import Store


def collect_display_labels(
    store: Store, account_map: AccountMap, connection_ids: list[str]
) -> dict[str, str]:
    labels: dict[str, str] = {}
    for connection_id in sorted(connection_ids):
        for account in store.accounts_for_connection(connection_id):
            canonical = account_map.resolve("truelayer", account["account_id"])
            labels[canonical] = f"{account['display_name']} ({connection_id})"
    row = store.connection.execute(
        "SELECT payload FROM raw_artefacts WHERE source = 'starling-accounts' "
        "ORDER BY fetched_at DESC LIMIT 1"
    ).fetchone()
    if row is not None:
        with contextlib.suppress(ValueError):
            decoded = json.loads(row["payload"])
            raw = decoded.get("accounts") if isinstance(decoded, dict) else None
            for account in raw if isinstance(raw, list) else []:
                if not isinstance(account, dict):
                    continue
                uid = str(account.get("accountUid", ""))
                name = str(account.get("name", "") or "account")
                if uid:
                    labels[account_map.resolve("starling", uid)] = f"{name} (starling)"
                    default_cat = str(account.get("defaultCategory", ""))
                    if default_cat:
                        labels[
                            account_map.resolve("starling", default_cat)
                        ] = f"{name} (starling)"
    for row in store.connection.execute(
        "SELECT payload FROM raw_artefacts WHERE source = 'starling-spaces' "
        "ORDER BY fetched_at ASC"
    ).fetchall():
        with contextlib.suppress(ValueError):
            decoded = json.loads(row["payload"])
            raw = decoded.get("savingsGoals") if isinstance(decoded, dict) else None
            for goal in raw if isinstance(raw, list) else []:
                if not isinstance(goal, dict):
                    continue
                uid = str(goal.get("savingsGoalUid", ""))
                name = str(goal.get("name", "") or "space")
                if uid:
                    labels[
                        account_map.resolve("starling", uid)
                    ] = f"{name} (starling space)"
    return labels
