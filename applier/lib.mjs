/**
 * The Actual session and the two operations, shared by the one-shot CLI
 * and the watching container.
 *
 * Provisioning uses createAccount and is idempotent by NAME: an account
 * whose name already exists in the budget is reused rather than
 * duplicated, so a replayed provision request cannot litter the budget
 * with copies. Applying uses importTransactions, never addTransactions -
 * the import path runs reconciliation, and matching imported_ids are
 * never added twice.
 */

import * as api from '@actual-app/api';
import { readFile, mkdtemp } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import process from 'node:process';

import { isOpeningImportedId, readAccountRows } from './audit.mjs';
import { indexPairRows, judgePair } from './transfers.mjs';
import { makeYielder } from './turn.mjs';

function required(name) {
  const value = (process.env[name] ?? '').trim();
  if (!value) {
    throw new Error(`Set ${name}. See .env.example in the repository root.`);
  }
  return value;
}

async function readSecret(name) {
  const path = (process.env[`${name}_FILE`] ?? '').trim();
  if (path) return (await readFile(path, 'utf8')).trim();
  return required(name);
}

/**
 * A secret that may be absent: '' when neither form is set.
 *
 * Absent and misconfigured are kept apart. A `_FILE` that is named but
 * missing or empty is an error here, because the alternative is to carry on
 * with no secret and fail later at a point that cannot say why - for the
 * budget's encryption password, as a download that will not decrypt.
 */
export async function optionalSecret(name, env = process.env, read = readFile) {
  const variable = `${name}_FILE`;
  const path = (env[variable] ?? '').trim();
  if (!path) return (env[name] ?? '').trim();
  let content;
  try {
    content = await read(path, 'utf8');
  } catch (error) {
    throw new Error(`${variable} names ${path}, which could not be read: ${error.message}`);
  }
  const secret = content.trim();
  if (!secret) {
    throw new Error(`${variable} names ${path}, which is empty. Unset it if there is no secret.`);
  }
  return secret;
}

export async function withBudget(work) {
  const serverURL = required('ACTUAL_SERVER_URL');
  const password = await readSecret('ACTUAL_PASSWORD');
  const syncId = required('ACTUAL_SYNC_ID');
  const filePassword = await optionalSecret('ACTUAL_ENCRYPTION_PASSWORD');

  const dataDir = await mkdtemp(join(tmpdir(), 'obdi-actual-'));
  await api.init({ dataDir, serverURL, password });
  try {
    await api.downloadBudget(
      syncId,
      filePassword ? { password: filePassword } : undefined,
    );
    return await work(api);
  } finally {
    await api.shutdown();
  }
}

export async function provisionAccounts(client, provision) {
  if (!provision.length) return { bindings: [], lines: [] };
  const existing = await client.getAccounts();
  const byName = new Map(existing.map((account) => [account.name, account.id]));
  const bindings = [];
  const lines = [];
  const maybeYield = makeYielder();
  for (const entry of provision) {
    await maybeYield();
    const name = (entry.label ?? '').trim() || entry.canonical_id;
    let id = byName.get(name);
    if (id) {
      lines.push(`${name}: already exists, reused`);
    } else {
      id = await client.createAccount({ name, type: 'checking' }, 0);
      byName.set(name, id);
      lines.push(`${name}: created`);
    }
    bindings.push({ canonical_id: entry.canonical_id, actual_account_id: id });
  }
  return { bindings, lines };
}

//: Re-reads of an unlinked pair before it is counted failed, and the pause
//: between them: five seconds of pausing in all.
const LINK_SETTLE = { attempts: 20, intervalMs: 250 };

/**
 * Turn two ordinary rows into one Actual transfer, for each confirmed pair.
 *
 * Runs AFTER every account's rows are imported, and only ever links rows
 * that are already there. A transfer is never created by sending a transfer
 * payee on import: Actual would then manufacture the other leg itself and
 * match an existing one only within seven days, so a counterpart that is
 * already present or arrives later is double-counted (measured on the
 * pinned library, 26.7.0). Linking two existing rows has neither hazard.
 *
 * Each pair takes two updates, one per row, and between them the pair is
 * one-sided; the verify pass re-reads every row and reports any pair that
 * did not end pointing at each other, so a half-done pair is loud and the
 * next push completes it.
 */
export async function linkTransfers(client, transfers, options = {}) {
  const settle = options.settle ?? LINK_SETTLE;
  const sleep = options.sleep ?? ((ms) => new Promise((resolve) => setTimeout(resolve, ms)));
  const counts = {
    pairs: transfers.length,
    linked: 0,
    already_linked: 0,
    skipped: {},
    failed: 0,
  };
  const lines = [];
  if (!transfers.length) return { counts, lines };

  const skip = (reason, line) => {
    counts.skipped[reason] = (counts.skipped[reason] ?? 0) + 1;
    lines.push(line);
  };
  const named = (pair) => `${pair.debit.imported_id} -> ${pair.credit.imported_id}`;

  const transferPayeeOf = new Map();
  for (const payee of await client.getPayees()) {
    if (payee.transfer_acct) transferPayeeOf.set(payee.transfer_acct, payee.id);
  }
  const rowsByAccount = await indexPairRows(client, transfers);

  const attempted = [];
  const maybeYield = makeYielder();
  let done = 0;
  for (const pair of transfers) {
    done += 1;
    await linkOne(pair);
    options.onProgress?.({ done, total: transfers.length });
    await maybeYield();
  }

  async function linkOne(pair) {
    const { verdict, a, b } = judgePair(pair, rowsByAccount);
    if (verdict === 'linked') {
      counts.already_linked += 1;
      return;
    }
    if (verdict !== 'linkable') {
      skip(verdict, `${named(pair)}: skipped, ${PAIR_REFUSALS[verdict] ?? verdict}`);
      return;
    }
    const payeeForA = transferPayeeOf.get(pair.credit.account);
    const payeeForB = transferPayeeOf.get(pair.debit.account);
    if (!payeeForA || !payeeForB) {
      skip('no_transfer_payee', `${named(pair)}: skipped, an account has no transfer payee`);
      return;
    }
    try {
      // Order matters: after the first call alone the pair is one-sided.
      await client.updateTransaction(a.id, { payee: payeeForA, transfer_id: b.id });
      await client.updateTransaction(b.id, { payee: payeeForB, transfer_id: a.id });
      attempted.push(pair);
    } catch (error) {
      counts.failed += 1;
      lines.push(`${named(pair)}: FAILED while linking - ${error?.message ?? error}`);
    }
  }

  // A pair that does not read as linked is read again after a pause, up to
  // `settle.attempts` times, and only then counted failed: updateTransaction
  // resolves before its change is readable (see applyOpeningBalances), and a
  // push of 679 pairs reported one failed that an audit found linked.
  // Bounded by attempts, not a deadline, because one read of a large account
  // is itself seconds long.
  let waiting = attempted;
  for (let reread = 0; waiting.length; reread += 1) {
    const after = await indexPairRows(client, waiting);
    const stillUnlinked = [];
    for (const pair of waiting) {
      if (judgePair(pair, after).verdict === 'linked') counts.linked += 1;
      else stillUnlinked.push(pair);
    }
    waiting = stillUnlinked;
    if (!waiting.length || reread >= settle.attempts) break;
    await sleep(settle.intervalMs);
  }
  for (const pair of waiting) {
    counts.failed += 1;
    lines.push(`${named(pair)}: FAILED verification - the rows do not point at each other`);
  }
  return { counts, lines };
}

/**
 * Keep each account's opening-balance row exact, after the import.
 *
 * Importing creates the row but cannot change it: a re-import of an imported
 * id keeps the existing row's values, so a corrected opening amount or date
 * would never arrive on its own. This finds each row by its imported id and
 * updates amount and date where they differ from the envelope. Everything
 * else about the row (payee, notes, category) is Actual's and is left alone.
 *
 * It never creates a row. A row that is not there is counted as missing and
 * reported, because a creation here would bypass the import's reconciliation
 * and could duplicate a row the person already entered. It also touches only
 * ids of the opening shape: an entry naming anything else is refused, since
 * an update keyed by imported id would otherwise be a way to rewrite a
 * payment.
 *
 * Each correction is read back, and a row that did not end up as asked is
 * counted as failed. updateTransaction resolves BEFORE the change can be read
 * (measured on the pinned library, 26.7.0: an immediate getTransactions still
 * returned the old amount and date, and the new ones appeared within about a
 * tenth of a second), so the read-back polls until the row is right or
 * `settle.timeoutMs` has passed. Without that wait every correction would be
 * reported as a failure.
 */
export async function applyOpeningBalances(
  client,
  openings,
  settle = { timeoutMs: 5000, intervalMs: 50 },
) {
  const counts = {
    entries: openings.length,
    already_right: 0,
    corrected: 0,
    missing: 0,
    ambiguous: 0,
    refused: 0,
    failed: 0,
  };
  const lines = [];
  if (!openings.length) return { counts, lines };

  const named = (entry) => `${entry.account}: ${entry.imported_id}`;
  const readIndex = async (accountId) => {
    const index = new Map();
    for (const row of await readAccountRows(client, accountId)) {
      if (row.is_child || !row.imported_id) continue;
      index.set(row.imported_id, [...(index.get(row.imported_id) ?? []), row]);
    }
    return index;
  };

  const indexes = new Map();
  const corrected = [];
  const maybeYield = makeYielder();
  for (const entry of openings) {
    await maybeYield();
    if (!isOpeningImportedId(entry.imported_id)) {
      counts.refused += 1;
      lines.push(`${named(entry)}: refused, not an opening-balance id; nothing was touched`);
      continue;
    }
    if (!indexes.has(entry.account)) indexes.set(entry.account, await readIndex(entry.account));
    const found = indexes.get(entry.account).get(entry.imported_id) ?? [];
    if (found.length === 0) {
      counts.missing += 1;
      lines.push(`${named(entry)}: opening row is not in Actual; nothing was created`);
      continue;
    }
    if (found.length > 1) {
      counts.ambiguous += 1;
      lines.push(`${named(entry)}: ${found.length} rows carry this id; none was changed`);
      continue;
    }
    const [row] = found;
    if (row.amount === entry.amount && row.date === entry.date) {
      counts.already_right += 1;
      continue;
    }
    try {
      await client.updateTransaction(row.id, { amount: entry.amount, date: entry.date });
      corrected.push(entry);
    } catch (error) {
      counts.failed += 1;
      lines.push(`${named(entry)}: FAILED while correcting - ${error?.message ?? error}`);
    }
  }

  let waiting = corrected;
  const deadline = Date.now() + settle.timeoutMs;
  while (waiting.length) {
    const indexesNow = new Map();
    const stillWrong = [];
    for (const entry of waiting) {
      if (!indexesNow.has(entry.account)) {
        indexesNow.set(entry.account, await readIndex(entry.account));
      }
      const [row] = indexesNow.get(entry.account).get(entry.imported_id) ?? [];
      if (row && row.amount === entry.amount && row.date === entry.date) {
        counts.corrected += 1;
        lines.push(`${named(entry)}: opening row corrected`);
      } else {
        stillWrong.push(entry);
      }
    }
    waiting = stillWrong;
    if (!waiting.length || Date.now() >= deadline) break;
    await new Promise((resolve) => setTimeout(resolve, settle.intervalMs));
  }
  for (const entry of waiting) {
    counts.failed += 1;
    lines.push(`${named(entry)}: FAILED verification - the row is not as asked`);
  }
  return { counts, lines };
}

const PAIR_REFUSALS = {
  leg_missing: 'a leg is not in Actual (leg missing); nothing was created',
  leg_ambiguous: 'a leg matches more than one row',
  same_account: 'both legs are in one Actual account',
  amounts_not_opposite: 'the amounts are not exact opposites; rows untouched',
  reconciled: 'a leg is reconciled, which the library will not change',
  linked_elsewhere: 'a leg is already linked to a different row; not overwritten',
};

export async function applyAccounts(client, accounts) {
  const lines = [];
  let added = 0;
  for (const [accountId, transactions] of Object.entries(accounts)) {
    const result = await client.importTransactions(accountId, transactions);
    const newRows = result?.added?.length ?? 0;
    const updated = result?.updated?.length ?? 0;
    added += newRows;
    lines.push(
      `${accountId}: ${transactions.length} submitted, ${newRows} added, ${updated} updated`,
    );
  }
  return { added, lines };
}
