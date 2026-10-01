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

// obdi's imported ids have exactly one shape: the 64-hex sha256 content
// key, a colon, and the occurrence counter. Actual's own importers (OFX/
// QIF/CSV file import, its bank sync) also populate imported_id - from
// FITIDs and the like - so "has an imported id" is NOT "is ours".
const OBDI_IMPORTED_ID = /^[0-9a-f]{64}:\d+$/;

export function isObdiImportedId(value) {
  return typeof value === 'string' && OBDI_IMPORTED_ID.test(value);
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
  // By the library's source a deleted row's imported id is still matched on
  // import, so that row would not come back on the next push either.
  return ourOrphans(expectedIds, rows)
    .filter((row) => !row.transfer_id)
    .map((row) => ({ id: row.id, imported_id: row.imported_id }));
}

export function countLinkedOrphans(expectedIds, rows) {
  return ourOrphans(expectedIds, rows).filter((row) => row.transfer_id).length;
}

export async function pruneAccounts(client, accounts) {
  const known = await client.getAccounts();
  const nameOf = new Map(known.map((account) => [account.id, account.name]));
  const report = [];
  for (const [accountId, expectedRows] of Object.entries(accounts)) {
    if (!nameOf.has(accountId)) continue;
    const expectedIds = new Set(expectedRows.map((row) => row.imported_id));
    if (expectedIds.size === 0) {
      // An empty expected set would authorise deleting every one of our
      // rows in the account - correct by definition, catastrophic by
      // accident (a wiped store). Skip and say so.
      report.push({
        account_id: accountId,
        name: nameOf.get(accountId),
        skipped: 'expected set empty - refusing to prune blind',
      });
      continue;
    }
    const rows = await readAccountRows(client, accountId);
    const prunable = choosePrunable(expectedIds, rows);
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
