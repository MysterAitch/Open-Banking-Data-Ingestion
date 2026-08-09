"""Per-file verification: should the parse be BELIEVED, not just displayed.

The upload preview shows what a parser produced; this module asks whether
that output can be trusted, using the strongest witness available - the
bank's own running balances inside the very same file:

  structure  every structural data row became a transaction (a parser
             silently dropping rows is a completeness fault nothing
             downstream can detect)
  walk       the file's balance chain verifies against its own amounts
             (rawview's balance-walk, run on the one uploaded file)
  sign       the walk's winning sign convention fixes the TRUE net
             movement; the parsed net must equal it. This is the check
             that catches a sign inversion (the Amex class) outright -
             a file's balances cannot lie about which way money moved.
  dates      the existing day/month ambiguity test, as a verdict

Verdicts are three-valued on purpose: ok, FAILED, or honestly
unavailable (a file with no balance column cannot have its signs
verified from within - saying "pass" would be a lie, and saying
nothing would hide that a stronger file exists).
"""

from __future__ import annotations

import csv
import io
from collections.abc import Sequence
from dataclasses import dataclass

from .ingest import dates_cannot_confirm_format
from .models import Transaction
from .rawview import balance_walk_report


@dataclass(frozen=True)
class Verdict:
    name: str
    #: True = verified, False = FAILED, None = honestly unavailable.
    ok: bool | None
    detail: str


def _structural_rows(payload: bytes) -> list[dict[str, object]]:
    """The file's data rows read structurally, not semantically.

    Column detection mirrors the parsers' own conventions ("Amount" or
    "Amount (GBP)"-style, likewise Balance) but performs NO sign or
    format interpretation - the whole point is an account of the file
    independent of the parser under verification. Amount arithmetic uses
    the same round(float * 100) the balance walk itself uses, so the two
    instruments cannot disagree by rounding.
    """
    try:
        text = payload.decode("utf-8-sig")
    except UnicodeDecodeError:
        return []
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        return []

    def _column(prefix: str) -> str | None:
        for name in reader.fieldnames or []:
            if name == prefix or name.startswith(f"{prefix} ("):
                return name
        return None

    amount_col = _column("Amount")
    if amount_col is None:
        return []
    balance_col = _column("Balance")

    rows: list[dict[str, object]] = []
    for row in reader:
        entry: dict[str, object] = {"amount": row.get(amount_col, "")}
        if balance_col is not None and (row.get(balance_col) or "").strip():
            entry["running_balance"] = {"amount": row[balance_col]}
        rows.append(entry)
    return rows


def _minor(value: object) -> int | None:
    try:
        return round(float(str(value)) * 100)
    except (TypeError, ValueError):
        return None


def verify_export(
    payload: bytes, parsed: Sequence[Transaction], filename: str
) -> list[Verdict]:
    verdicts: list[Verdict] = []
    structural = _structural_rows(payload)

    if structural:
        ok = len(structural) == len(parsed)
        verdicts.append(
            Verdict(
                "structure",
                ok,
                f"{len(structural)} data row(s) in the file, {len(parsed)} parsed"
                + ("" if ok else " - rows are being silently dropped"),
            )
        )
    else:
        verdicts.append(
            Verdict(
                "structure",
                None,
                "structural read unavailable for this format - row accounting "
                "rests on the parser alone",
            )
        )

    with_balance = [r for r in structural if "running_balance" in r]
    sign = None
    if len(with_balance) >= 2:
        report = balance_walk_report(
            [{"ref": "upload", "label": filename, "rows": structural}]
        )
        accounts = report.get("accounts")
        account = accounts.get("upload") if isinstance(accounts, dict) else None
        if isinstance(account, dict):
            checks = int(str(account.get("checks", 0)))
            breaks = int(str(account.get("breaks", 0)))
            convention = str(account.get("convention", ""))
            verdicts.append(
                Verdict(
                    "balance walk",
                    breaks == 0,
                    f"{checks} balance step(s) verified, {breaks} break(s) "
                    f"under '{convention}'"
                    + ("" if breaks == 0 else " - money moved that the rows do not explain"),
                )
            )
            if "amounts as-is" in convention:
                sign = 1
            elif "amounts negated" in convention:
                sign = -1
        else:
            verdicts.append(
                Verdict(
                    "balance walk",
                    None,
                    "balances present but no consistent chain could be "
                    "established under any convention",
                )
            )
    else:
        verdicts.append(
            Verdict(
                "balance walk",
                None,
                "no running-balance column - the file cannot corroborate "
                "itself; cross-source agreement after import is the only check",
            )
        )

    if sign is not None:
        raw_amounts = [_minor(r.get("amount")) for r in structural]
        if all(a is not None for a in raw_amounts):
            net_true = sign * sum(a for a in raw_amounts if a is not None)
            net_parsed = sum(t.amount_minor for t in parsed)
            ok = net_parsed == net_true
            inversion = (not ok) and net_parsed == -net_true and net_true != 0
            verdicts.append(
                Verdict(
                    "sign",
                    ok,
                    f"balances say net {net_true / 100:+.2f}, "
                    f"parser says {net_parsed / 100:+.2f}"
                    + (
                        " - SIGN INVERSION: every amount is pointing the wrong way"
                        if inversion
                        else ("" if ok else " - magnitudes disagree")
                    ),
                )
            )
        else:
            verdicts.append(
                Verdict("sign", None, "an amount failed the structural read")
            )
    else:
        verdicts.append(
            Verdict(
                "sign",
                None,
                "no balance chain to fix the true direction of movement",
            )
        )

    ambiguous = dates_cannot_confirm_format([t.value_date for t in parsed])
    verdicts.append(
        Verdict(
            "dates",
            None if ambiguous else True,
            (
                "every date falls on the 12th or earlier - nothing rules out "
                "the opposite day/month reading; cross-check after importing"
                if ambiguous
                else "at least one date proves the format for the whole file"
            ),
        )
    )
    return verdicts
