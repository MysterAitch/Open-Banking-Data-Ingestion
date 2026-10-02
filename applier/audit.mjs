/**
 * The audit: read back what Actual holds and compare it with what obdi
 * believes, without changing anything.
 *
 * The push pipe is one-way by design, which means obdi cannot see a wrong
 * copy on the Actual side (a mis-provisioned merge, a hand-deleted import,
 * an edited amount). The audit closes that gap as a REPORT, not a repair:
 * every row in a bound account is partitioned by ownership, and the
 * boundary is the imported_id - rows without one are the person's own
 * entries (manual transactions, starting balances, scheduled postings) and
 * are counted but never listed or touched.
 *
 * Partitions:
 * - present:  carries an expected imported_id and matches the bank-owned
 *             facts (amount, date). Categories, payees, notes and splits
 *             are Actual's domain and deliberately not compared.
 * - missing:  expected but absent from Actual (deleted there, or simply
 *             not pushed yet).
 * - orphaned: carries an imported_id obdi does not expect in this account
 *             - the residue of a mis-binding, provably ours.
 * - human:    no imported_id; yours, counted only.
 * - diverged: expected and present, but amount or date differ.
 *
 * Beside the partition, each account carries its balance verdict (Actual's
 * own figure against the sum of the expected rows), and the envelope's
 * transfer pairs are judged linked, unlinked, or leg-missing by
 * transfers.mjs, which the linking push shares.
 */

export function partitionAccount(expectedRows, actualRows) {
  const expectedById = new Map(expectedRows.map((row) => [row.imported_id, row]));
  const seen = new Set();
  const orphaned = [];
  const diverged = [];
  let human = 0;
  const copies = new Map();
  for (const row of actualRows) {
    // Split children belong to their parent, which keeps the imported_id
    // and the total amount.
    if (row.is_child) continue;
    const id = row.imported_id ?? null;
    if (!id) {
      human += 1;
      continue;
    }
    const expected = expectedById.get(id);
    if (!expected) {
      orphaned.push({ imported_id: id, date: row.date, amount: row.amount });
      continue;
    }
    // A second Actual row carrying the same imported id is a duplicate:
    // it inflates the balance while set-based comparison stays blind.
    copies.set(id, (copies.get(id) ?? 0) + 1);
    seen.add(id);
    if (row.amount !== expected.amount || row.date !== expected.date) {
      diverged.push({
        imported_id: id,
        actual: { date: row.date, amount: row.amount },
        store: { date: expected.date, amount: expected.amount },
      });
    }
  }
  const missing = expectedRows
    .filter((row) => !seen.has(row.imported_id))
    .map((row) => row.imported_id);
  const duplicated = [...copies.entries()]
    .filter(([, n]) => n > 1)
    .map(([imported_id, n]) => ({ imported_id, copies: n }));
  return {
    expected: expectedRows.length,
    present: seen.size,
    missing,
    orphaned,
    human,
    diverged,
    duplicated,
  };
}

export function summariseAudit(partition) {
  // Counts in full, detail capped: the result file renders on a page and
  // a thousand-row divergence list helps nobody there - the counts say
  // how bad it is, the samples say where to start looking.
  const cap = (list) => list.slice(0, 10);
  return {
    expected: partition.expected,
    present: partition.present,
    human: partition.human,
    missing: partition.missing.length,
    orphaned: partition.orphaned.length,
    diverged: partition.diverged.length,
    duplicated: (partition.duplicated ?? []).length,
    duplicated_sample: cap(partition.duplicated ?? []),
    missing_sample: cap(partition.missing),
    orphaned_sample: cap(partition.orphaned),
    diverged_sample: cap(partition.diverged),
  };
}

export async function readAccountRows(client, accountId) {
  // Wide explicit bounds rather than trusting an unbounded default.
  return client.getTransactions(accountId, '1900-01-01', '2999-12-31');
}

export function expectedBalance(expectedRows) {
  // Integer minor units throughout: the expected balance is exactly the
  // sum of what obdi says the account holds.
  return expectedRows.reduce((sum, row) => sum + row.amount, 0);
}

export async function auditAccounts(client, accounts) {
  const known = await client.getAccounts();
  const nameOf = new Map(known.map((account) => [account.id, account.name]));
  const report = [];
  // The blind spot the first live audit proved: an account that EXISTS in
  // Actual but is bound to nothing was invisible - audits read only bound
  // accounts, so the abandoned collision-era account escaped every clean
  // verdict. Strays are named with their row counts; deleting or binding
  // them is the human's call.
  for (const account of known) {
    if (Object.prototype.hasOwnProperty.call(accounts, account.id)) continue;
    const rows = await readAccountRows(client, account.id);
    report.push({
      account_id: account.id,
      name: account.name,
      unbound_in_actual: true,
      rows: rows.filter((row) => !row.is_child).length,
    });
  }
  for (const [accountId, expectedRows] of Object.entries(accounts)) {
    if (!nameOf.has(accountId)) {
      report.push({
        account_id: accountId,
        name: null,
        missing_account: true,
        expected: expectedRows.length,
      });
      continue;
    }
    const rows = await readAccountRows(client, accountId);
    // The balance is Actual's own figure against the sum of the rows obdi
    // expects. Rows the person entered by hand count in Actual's figure and
    // not in the expected sum, so an account holding any differs by exactly
    // their total; the partition above says whether that is the cause.
    const expected = expectedBalance(expectedRows);
    const actual = await client.getAccountBalance(accountId);
    report.push({
      account_id: accountId,
      name: nameOf.get(accountId),
      missing_account: false,
      ...summariseAudit(partitionAccount(expectedRows, rows)),
      balance: { expected, actual, agrees: expected === actual },
    });
  }
  return report;
}

// obdi's imported ids have exactly two shapes. A payment's is the 64-hex
// sha256 content key, a colon, and the occurrence counter. An account's
// opening balance row is the reserved prefix below plus the canonical account
// reference, which may hold any character a reference can (colons and spaces
// included), so only the prefix and a non-empty remainder are checked. The
// prefix is the one OPENING_IMPORTED_ID_PREFIX names in src/obdi/replay.py,
// and a test holds the two together.
// Actual's own importers (OFX/QIF/CSV file import, its bank sync) also
// populate imported_id - from FITIDs and the like - so "has an imported id"
// is NOT "is ours". Recognising the opening shape is also what makes the row
// prunable once its account stops having an opening balance: an orphan is
// only ever deleted if it is provably ours.
const OBDI_PAYMENT_IMPORTED_ID = /^[0-9a-f]{64}:\d+$/;
const OBDI_OPENING_IMPORTED_ID = /^obdi-opening:.+$/s;

export function isOpeningImportedId(value) {
  return typeof value === 'string' && OBDI_OPENING_IMPORTED_ID.test(value);
}

export function isObdiImportedId(value) {
  return (
    typeof value === 'string' &&
    (OBDI_PAYMENT_IMPORTED_ID.test(value) || OBDI_OPENING_IMPORTED_ID.test(value))
  );
}

function ourOrphans(expectedIds, rows) {
  // Rows carrying one of OUR imported ids that the expected set no longer
  // contains. An id that is not obdi-shaped is somebody else's bookkeeping
  // and is treated exactly like no id at all: untouchable.
  // Split children ride with their parent.
  return rows
    .filter((row) => !row.is_child)
    .filter((row) => isObdiImportedId(row.imported_id))
    .filter((row) => !expectedIds.has(row.imported_id));
}

export function choosePrunable(expectedIds, rows) {
  // An orphan that is one leg of a linked transfer is NOT prunable.
  // Deleting one leg makes Actual delete the other as well (measured on the
  // pinned library, 26.7.0, where the partner vanished shortly after the
  // delete resolved), and the other leg is a row obdi still expects.
  // A prune must not remove an expected row as a side effect, so the orphan
  // is left and counted.
  // The cost is that it stays, linked, until somebody unlinks it: the proper
  // remedy is to unlink first and then delete, which has not been run
  // against the engine.
  // An earlier version of this comment said a deleted row's id would never
  // be imported again. That came from reading the library and was wrong for
  // at least one case: a pruned row WAS created again by a later import,
  // once the delete had settled.
  return ourOrphans(expectedIds, rows)
    .filter((row) => !row.transfer_id)
    .map((row) => ({ id: row.id, imported_id: row.imported_id }));
}

export function countLinkedOrphans(expectedIds, rows) {
  return ourOrphans(expectedIds, rows).filter((row) => row.transfer_id).length;
}

const hasOwn = (object, key) => Object.prototype.hasOwnProperty.call(object, key);

// What the person was shown and confirmed, as a ceiling on one account.
// `clear_empty` names an account that expects nothing and is the person's
// explicit request to empty it; `confirmed` is the orphan count shown for an
// ordinary account. Neither is trusted beyond being a ceiling: the count that
// is checked is the count that is deleted, on one read of the account.
function shownCeiling(accountId, clearEmpty, confirmed) {
  if (hasOwn(clearEmpty, accountId)) return { named: true, shown: clearEmpty[accountId] };
  if (hasOwn(confirmed, accountId)) return { named: false, shown: confirmed[accountId] };
  return { named: false, shown: undefined };
}

export async function pruneAccounts(client, accounts, options = {}) {
  const clearEmpty = options.clear_empty ?? {};
  const confirmed = options.confirmed ?? {};
  // A request naming accounts to clear is the press of one clearing form and
  // nothing else: pruning every other account in the same stroke would
  // delete rows the person never saw a count for.
  const clearing = Object.keys(clearEmpty).length > 0;
  const known = await client.getAccounts();
  const nameOf = new Map(known.map((account) => [account.id, account.name]));
  const report = [];
  for (const [accountId, expectedRows] of Object.entries(accounts)) {
    if (!nameOf.has(accountId)) continue;
    if (clearing && !hasOwn(clearEmpty, accountId)) continue;
    const expectedIds = new Set(expectedRows.map((row) => row.imported_id));
    const { named, shown } = shownCeiling(accountId, clearEmpty, confirmed);
    if (expectedIds.size === 0 && !named) {
      // An empty expected set would authorise deleting every one of our
      // rows in the account - correct by definition, catastrophic by
      // accident (a wiped store, a broken push). Only a clear_empty entry,
      // the person's confirmed count for exactly this account, lifts it.
      report.push({
        account_id: accountId,
        name: nameOf.get(accountId),
        skipped: 'expected set empty - refusing to prune blind',
      });
      continue;
    }
    const malformed =
      shown !== undefined &&
      (!Number.isInteger(shown) || (expectedIds.size === 0 && shown < 1));
    if (malformed) {
      report.push({
        account_id: accountId,
        name: nameOf.get(accountId),
        refused: `confirmed count ${JSON.stringify(shown)} is not a positive whole number - nothing deleted`,
      });
      continue;
    }
    const rows = await readAccountRows(client, accountId);
    const prunable = choosePrunable(expectedIds, rows);
    if (shown !== undefined && prunable.length > shown) {
      report.push({
        account_id: accountId,
        name: nameOf.get(accountId),
        refused: `holds ${prunable.length}, more than the ${shown} confirmed - run the audit again`,
        holds: prunable.length,
        confirmed: shown,
      });
      continue;
    }
    for (const target of prunable) {
      await client.deleteTransaction(target.id);
    }
    const foreign = rows.filter(
      (row) => !row.is_child && row.imported_id && !isObdiImportedId(row.imported_id)
    ).length;
    const entry = {
      account_id: accountId,
      name: nameOf.get(accountId),
      removed: prunable.length,
    };
    if (foreign > 0) {
      // Say what was deliberately left alone - silence would read as
      // "nothing else was there".
      entry.foreign_ids = foreign;
    }
    const linked = countLinkedOrphans(expectedIds, rows);
    if (linked > 0) {
      // Left for the same reason, and said for the same reason.
      entry.linked_left = linked;
    }
    report.push(entry);
  }
  return report;
}
