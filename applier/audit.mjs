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
 */

export function partitionAccount(expectedRows, actualRows) {
  const expectedById = new Map(expectedRows.map((row) => [row.imported_id, row]));
  const seen = new Set();
  const orphaned = [];
  const diverged = [];
  let human = 0;
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
  return {
    expected: expectedRows.length,
    present: seen.size,
    missing,
    orphaned,
    human,
    diverged,
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
    missing_sample: cap(partition.missing),
    orphaned_sample: cap(partition.orphaned),
    diverged_sample: cap(partition.diverged),
  };
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
    const rows = await client.getTransactions(account.id, '1900-01-01', '2999-12-31');
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
    // Wide explicit bounds rather than trusting an unbounded default.
    const rows = await client.getTransactions(accountId, '1900-01-01', '2999-12-31');
    report.push({
      account_id: accountId,
      name: nameOf.get(accountId),
      missing_account: false,
      ...summariseAudit(partitionAccount(expectedRows, rows)),
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

export function choosePrunable(expectedIds, rows) {
  // Only rows carrying one of OUR imported ids that the expected set no
  // longer contains. An id that is not obdi-shaped is somebody else's
  // bookkeeping and is treated exactly like no id at all: untouchable.
  // Split children ride with their parent.
  return rows
    .filter((row) => !row.is_child)
    .filter((row) => isObdiImportedId(row.imported_id))
    .filter((row) => !expectedIds.has(row.imported_id))
    .map((row) => ({ id: row.id, imported_id: row.imported_id }));
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
    const rows = await client.getTransactions(accountId, '1900-01-01', '2999-12-31');
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
    report.push(entry);
  }
  return report;
}
