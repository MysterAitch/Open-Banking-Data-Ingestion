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

import { isMarkerName } from './marker.mjs';
import { makeYielder } from './turn.mjs';

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

/**
 * Why obdi no longer expects an orphan, from what the request itself says.
 *
 * - elsewhere: another account's expected rows carry the id, so a bind or a
 *   Space fold moved the payment and the row here is the old copy.
 * - history: the id is one of `history`, the imported ids of stored rows that
 *   are no longer money (reversed, void, or folded).
 * - unknown: neither, so obdi holds no row with that identity (re-identified by
 *   a matching change, or never obdi's; another importer's ids land here too).
 *
 * Only the Python side's `explained_orphans` says what "explained" is worth to
 * the removal's size guard. An absent `history` list classifies nothing as
 * history, which only ever makes the guard stricter.
 */
export function explainOrphans(orphans, accountId, accounts, history) {
  const owners = new Map();
  for (const [owner, rows] of Object.entries(accounts)) {
    for (const row of rows) if (!owners.has(row.imported_id)) owners.set(row.imported_id, owner);
  }
  const counts = { history: 0, elsewhere: 0, unknown: 0 };
  for (const { imported_id: id } of orphans) {
    const owner = owners.get(id);
    if (owner !== undefined && owner !== accountId) counts.elsewhere += 1;
    else if (history.has(id)) counts.history += 1;
    else counts.unknown += 1;
  }
  return counts;
}

export async function auditAccounts(client, accounts, options = {}) {
  const history = new Set(options.history ?? []);
  const known = await client.getAccounts();
  const nameOf = new Map(known.map((account) => [account.id, account.name]));
  const report = [];
  const ctx = createLinkContext(client);
  // The blind spot the first live audit proved: an account that EXISTS in
  // Actual but is bound to nothing was invisible - audits read only bound
  // accounts, so the abandoned collision-era account escaped every clean
  // verdict. Strays are named with their row counts; deleting or binding
  // them is the human's call.
  for (const account of known) {
    if (Object.prototype.hasOwnProperty.call(accounts, account.id)) continue;
    // The sync marker is obdi's own, holds no rows by design, and is not a
    // difference between the budget and the store (see marker.mjs).
    if (isMarkerName(account.name)) continue;
    const rows = await ctx.rowsOf(account.id);
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
    const rows = await ctx.rowsOf(accountId);
    const { prunable, left } = await choosePrunable(
      ctx,
      accountId,
      new Set(expectedRows.map((row) => row.imported_id)),
      rows
    );
    // The balance is Actual's own figure against the sum of the rows obdi
    // expects. Rows the person entered by hand count in Actual's figure and
    // not in the expected sum, so an account holding any differs by exactly
    // their total; the partition above says whether that is the cause.
    const expected = expectedBalance(expectedRows);
    const actual = await client.getAccountBalance(accountId);
    const partition = partitionAccount(expectedRows, rows);
    report.push({
      account_id: accountId,
      name: nameOf.get(accountId),
      missing_account: false,
      ...summariseAudit(partition),
      // The three classes add up to `orphaned`.
      orphaned_explained: explainOrphans(partition.orphaned, accountId, accounts, history),
      // Every top-level row the account holds, whoever owns it: the partition
      // above counts a duplicated imported id once, so its numbers cannot add
      // up to this, and an empty of the whole budget is confirmed against it.
      rows: rows.filter((row) => !row.is_child).length,
      // What a removal would do with the orphans counted above: the two add
      // up to `orphaned`, which is the ceiling the person confirms.
      orphaned_will_go: prunable.length,
      orphaned_will_stay: left,
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

// Why an orphan that is one leg of a linked transfer stays, with the words a
// result line says it in. `foreign` is the audit's own: a row carrying another
// importer's id is counted as orphaned but is never ours to remove.
export const LEFT_REASONS = {
  partner_missing: 'the other leg could not be found where its transfer payee says it is',
  partner_not_ours:
    "the other leg is not an obdi import (a row of yours, or another importer's), and is never touched, not even to unlink it",
  reconciled: 'a leg is reconciled, which the library will not change',
  split: 'a leg is a split parent or child',
  partner_linked_elsewhere: 'the other leg is linked to a different row',
  changed_during_removal: 'the rows changed while the removal ran',
  foreign: "the row carries another importer's id",
};

// What stops a linked orphan being unlinked and deleted, judged from the two
// legs as they are now; null when nothing does. The partner of an obdi orphan
// may be an expected row or another orphan: either way it is only ever
// unlinked, never deleted by this account's removal.
function whyLeft(orphan, partner) {
  if (!isObdiImportedId(partner.imported_id)) return 'partner_not_ours';
  if (orphan.is_parent || orphan.is_child || partner.is_parent || partner.is_child) {
    return 'split';
  }
  if (orphan.reconciled || partner.reconciled) return 'reconciled';
  if (partner.transfer_id && partner.transfer_id !== orphan.id) return 'partner_linked_elsewhere';
  return null;
}

/**
 * Reads shared by one audit or one account's removal: each account's rows
 * once, and which account a transfer payee points at. `prime` hands in rows
 * the caller has already read so they are not read twice.
 */
export function createLinkContext(client) {
  const rowsByAccount = new Map();
  let accountOfPayee = null;
  return {
    prime(accountId, rows) {
      rowsByAccount.set(accountId, rows);
    },
    async rowsOf(accountId) {
      if (!rowsByAccount.has(accountId)) {
        rowsByAccount.set(accountId, await readAccountRows(client, accountId));
      }
      return rowsByAccount.get(accountId);
    },
    async transferAccountOf(payeeId) {
      if (!payeeId) return null;
      if (!accountOfPayee) {
        accountOfPayee = new Map();
        for (const payee of await client.getPayees()) {
          if (payee.transfer_acct) accountOfPayee.set(payee.id, payee.transfer_acct);
        }
      }
      return accountOfPayee.get(payeeId) ?? null;
    },
  };
}

async function assessLinkedOrphan(ctx, orphan) {
  // The transfer payee of a linked leg names the account the other leg lives
  // in, so only that account is searched. The two legs of a transfer need not
  // share a date, which is why the other leg is found by id and not by day.
  const partnerAccount = await ctx.transferAccountOf(orphan.payee);
  const partner = partnerAccount
    ? (await ctx.rowsOf(partnerAccount)).find((row) => row.id === orphan.transfer_id)
    : undefined;
  if (!partner) return { reason: 'partner_missing' };
  const reason = whyLeft(orphan, partner);
  return reason ? { reason } : { partnerAccount, partner };
}

/**
 * Every orphan in one account, sorted into those a removal will take and
 * those it will leave, by reason. The two add up to the audit's `orphaned`
 * count, which is the ceiling the person confirms.
 *
 * Rule for an orphan that is one leg of a linked transfer: it IS removable,
 * but only by unlinking first. Deleting a leg makes Actual delete its
 * partner as well - measured on the pinned library (26.7.0), where the
 * partner vanished shortly after the delete resolved, whether or not the
 * partner still pointed back - and the partner is usually a row obdi still
 * expects. So the partner is unlinked, then the orphan, then only the orphan
 * is deleted, and the partner is read again to prove it survived unchanged.
 * An orphan is left, and counted under its reason, when that cannot be done
 * cleanly: the other leg is not an obdi import (never touch somebody else's
 * row), either leg is reconciled or split, the other leg is linked to a
 * third row, or it cannot be found. When the partner is itself an orphan in
 * another account it is only unlinked here and is deleted by that account's
 * own removal: each account deletes within the count shown for it.
 * An earlier version of this comment said a deleted row's id would never be
 * imported again. That came from reading the library and was wrong for at
 * least one case: a pruned row WAS created again by a later import, once the
 * delete had settled.
 */
export async function choosePrunable(ctx, accountId, expectedIds, rows) {
  const prunable = [];
  const left = {};
  const leave = (reason) => {
    left[reason] = (left[reason] ?? 0) + 1;
  };
  for (const row of rows) {
    if (row.is_child) continue;
    if (!row.imported_id || expectedIds.has(row.imported_id)) continue;
    if (!isObdiImportedId(row.imported_id)) {
      leave('foreign');
      continue;
    }
    const target = { id: row.id, imported_id: row.imported_id };
    if (!row.transfer_id) {
      prunable.push(target);
      continue;
    }
    const verdict = await assessLinkedOrphan(ctx, row);
    if (verdict.reason) {
      leave(verdict.reason);
      continue;
    }
    prunable.push({
      ...target,
      date: row.date,
      link: { partner: verdict.partner, partnerAccount: verdict.partnerAccount },
    });
  }
  return { prunable, left };
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

// Re-reads of a row after a change, and the pause between them. The engine
// resolves updateTransaction and deleteTransaction before the change is
// readable, and applies a delete's cascade after that, so each step is waited
// for by reading and the partner is read again after `holdMs` more.
const SETTLE = { timeoutMs: 10000, intervalMs: 50, holdMs: 250 };

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

// A removal that went wrong after it had started changing rows. Everything
// the result needs to say loudly is in the message.
class RemovalStopped extends Error {
  constructor(message, { deleted = false } = {}) {
    super(message);
    this.deleted = deleted;
  }
}

// One row by id, read for its own date only: the account's whole history is
// read once for the plan, and a removal of hundreds must not read it again
// for each.
async function readRowNow(client, accountId, row) {
  const rows = await client.getTransactions(accountId, row.date, row.date);
  return rows.find((candidate) => candidate.id === row.id) ?? null;
}

async function waitFor(settle, read, done) {
  const deadline = Date.now() + settle.timeoutMs;
  for (;;) {
    const now = await read();
    if (done(now)) return now;
    if (Date.now() >= deadline) return undefined;
    await sleep(settle.intervalMs);
  }
}

// Clearing both link fields is the only unlink the engine was measured to
// perform without side effects. Clearing the link alone made it manufacture a
// second copy of the other leg, and clearing the payee alone deleted the other
// leg. The payee is left empty: the next push sets the transfer payee when it
// links the row to its new partner.
const UNLINK = { transfer_id: null, payee: null };

/**
 * Unlink one leg of a transfer from its partner, and read both back as
 * unlinked; throws, naming which still reads as linked, when either does not.
 * Shared by the removal and by a push that re-links a leg, so both unlink by
 * the one sequence the engine was measured to survive.
 *
 * The partner is unlinked first, so that a failure between the two unlinks
 * leaves the leg still pointing at it: a state the next run recognises and
 * finishes. The other order would leave the partner pointing at a row nothing
 * identifies as linked.
 */
export async function unlinkLeg(client, leg, partner, settle = SETTLE) {
  const fullSettle = { ...SETTLE, ...settle };
  if (partner.row.transfer_id) {
    await client.updateTransaction(partner.row.id, UNLINK);
    const read = await waitFor(
      fullSettle,
      () => readRowNow(client, partner.account, partner.row),
      (row) => !row || !row.transfer_id
    );
    if (read === undefined) throw new Error('the partner still reads as linked');
  }
  await client.updateTransaction(leg.row.id, UNLINK);
  const read = await waitFor(
    fullSettle,
    () => readRowNow(client, leg.account, leg.row),
    (row) => !row || !row.transfer_id
  );
  if (read === undefined) throw new Error('the leg still reads as linked');
}

const bankFacts = (row) =>
  JSON.stringify([row.account, row.amount, row.date, row.imported_id, row.cleared]);

async function removeOrphan(client, accountId, target, settle) {
  if (!target.link) {
    await client.deleteTransaction(target.id);
    return {};
  }
  const { partner, partnerAccount } = target.link;
  const orphan = await readRowNow(client, accountId, target);
  // Gone since the plan was read (an earlier removal in this run took it).
  if (!orphan) return { gone: true };
  if (!orphan.transfer_id) {
    // Unlinked since the plan: the partner of an earlier removal in this run.
    await client.deleteTransaction(orphan.id);
    return {};
  }
  const partnerNow = await readRowNow(client, partnerAccount, partner);
  if (!partnerNow) return { left: 'partner_missing' };
  if (orphan.transfer_id !== partnerNow.id) return { left: 'changed_during_removal' };
  const reason = whyLeft(orphan, partnerNow);
  if (reason) return { left: reason };

  const named = `${target.imported_id} -> ${partnerNow.imported_id}`;
  const expected = bankFacts(partnerNow);
  try {
    await unlinkLeg(
      client,
      { account: accountId, row: orphan },
      { account: partnerAccount, row: partnerNow },
      settle
    );
  } catch (error) {
    throw new RemovalStopped(
      `${named}: unlinking failed (${error?.message ?? error}); the account was stopped before anything was deleted`
    );
  }
  try {
    await client.deleteTransaction(orphan.id);
    const gone = await waitFor(
      settle,
      () => readRowNow(client, accountId, orphan),
      (row) => !row
    );
    if (gone !== null) throw new Error('the orphan is still there after the delete');
  } catch (error) {
    throw new RemovalStopped(
      `${named}: unlinked, but deleting the orphan failed (${error?.message ?? error}); its partner was left unlinked and the account was stopped`
    );
  }
  await sleep(settle.holdMs);
  const after = await readRowNow(client, partnerAccount, partnerNow);
  if (!after) {
    throw new RemovalStopped(
      `${named}: the partner row is missing after the orphan was deleted; the account was stopped`,
      { deleted: true }
    );
  }
  if (bankFacts(after) !== expected || after.transfer_id) {
    throw new RemovalStopped(
      `${named}: the partner row changed after the orphan was deleted (${
        after.transfer_id ? 'it is still linked' : 'its amount, date, account, or id differs'
      }); the account was stopped`,
      { deleted: true }
    );
  }
  return { unlinked: true };
}

export async function pruneAccounts(client, accounts, options = {}) {
  const settle = { ...SETTLE, ...(options.settle ?? {}) };
  const clearEmpty = options.clear_empty ?? {};
  const confirmed = options.confirmed ?? {};
  const onProgress = options.onProgress;
  const maybeYield = makeYielder();
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
    const ctx = createLinkContext(client);
    ctx.prime(accountId, rows);
    const { prunable, left } = await choosePrunable(ctx, accountId, expectedIds, rows);
    // `foreign` is the audit's word for rows this removal never considers;
    // the result reports them under `foreign_ids`, below.
    delete left.foreign;
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
    // The total is this account's: the next account is not read until this
    // one is done, and reading them all first would only buy a prettier
    // number at the cost of a second pass over the budget.
    let done = 0;
    let removed = 0;
    let unlinked = 0;
    let stopped = null;
    for (const target of prunable) {
      try {
        const outcome = await removeOrphan(client, accountId, target, settle);
        if (outcome.left) left[outcome.left] = (left[outcome.left] ?? 0) + 1;
        else if (!outcome.gone) removed += 1;
        if (outcome.unlinked) unlinked += 1;
      } catch (error) {
        if (!(error instanceof RemovalStopped)) throw error;
        // What was removed before the stop is counted; the next orphan is
        // not attempted, because whatever went wrong is not known to be
        // confined to the row that showed it.
        removed += error.deleted ? 1 : 0;
        stopped = error.message;
        break;
      }
      done += 1;
      onProgress?.({
        account_id: accountId,
        name: nameOf.get(accountId),
        done,
        total: prunable.length,
      });
      await maybeYield();
    }
    const foreign = rows.filter(
      (row) => !row.is_child && row.imported_id && !isObdiImportedId(row.imported_id)
    ).length;
    const entry = {
      account_id: accountId,
      name: nameOf.get(accountId),
      removed,
    };
    if (unlinked > 0) {
      // Said because each of these changed a row outside this account too:
      // its partner lost its link.
      entry.unlinked = unlinked;
    }
    if (foreign > 0) {
      // Say what was deliberately left alone - silence would read as
      // "nothing else was there".
      entry.foreign_ids = foreign;
    }
    const linkedLeft = Object.values(left).reduce((sum, n) => sum + n, 0);
    if (linkedLeft > 0) {
      // Left for the reasons named, and said with them.
      entry.linked_left = linkedLeft;
      entry.left = left;
      entry.lines = Object.entries(left).map(
        ([reason, n]) =>
          `${n} linked orphan${n === 1 ? '' : 's'} left: ${LEFT_REASONS[reason] ?? reason}`
      );
    }
    if (stopped) entry.stopped = stopped;
    report.push(entry);
  }
  return report;
}
