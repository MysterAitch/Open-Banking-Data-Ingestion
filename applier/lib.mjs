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
import { isMarkerName } from './marker.mjs';
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

/**
 * Make the server's copy of the budget file match this session's, so that a
 * device downloading afresh starts from now and not from an old file.
 *
 * Why it is needed: a download is the server's stored FILE plus every sync
 * message since that file was written (read from the pinned library, 26.7.0:
 * downloadBudget, then a full sync from the file's own lastSyncedTimestamp).
 * The file is replaced only when a client uploads it, which the library does
 * on loading a budget not uploaded for seven days, and this applier downloads
 * into a new directory for every job, so it never would. A device then replays
 * a backlog that only grows, in one all-or-nothing transaction.
 *
 * Two internal handlers, in this order: `sync`, because the exported file
 * carries the lastSyncedTimestamp a downloader resumes from, which is only
 * advanced by a sync that completed; then `upload-budget`, which re-sends the
 * local file under the SAME cloud file id and group, so the Sync ID is kept
 * (`sync-reset` would delete the server's messages and issue a new Sync ID,
 * and is never used). Both report failure as `{ error }` rather than throwing,
 * and a failure of either is returned, not raised: the job's changes are
 * already on the server as messages, so a refused refresh must not turn a
 * job that worked into one that failed. It is reported instead.
 */
export async function refreshSnapshot(handle, now = () => new Date()) {
  const failed = (message) => ({ refreshed: false, at: now().toISOString(), error: message });
  const describe = (error) =>
    typeof error === 'string' ? error : (error?.reason ?? error?.message ?? 'unknown error');
  try {
    const synced = await handle.send('sync');
    if (synced?.error) {
      return failed(`the final sync did not complete (${describe(synced.error)}); nothing was uploaded`);
    }
    const uploaded = await handle.send('upload-budget');
    if (uploaded?.error) return failed(`the upload was refused (${describe(uploaded.error)})`);
    return { refreshed: true, at: now().toISOString() };
  } catch (error) {
    return failed(`the refresh threw (${error?.message ?? error})`);
  }
}

/**
 * Open the budget fresh from the server and run `work` against it.
 *
 * `work` receives the client and a session whose `refreshSnapshot()` the job
 * calls once, as its very last step and only when it succeeded and changed
 * something: a job that failed or stopped partway never refreshes, and an
 * audit never does.
 */
export async function withBudget(work) {
  const serverURL = required('ACTUAL_SERVER_URL');
  const password = await readSecret('ACTUAL_PASSWORD');
  const syncId = required('ACTUAL_SYNC_ID');
  const filePassword = await optionalSecret('ACTUAL_ENCRYPTION_PASSWORD');

  const dataDir = await mkdtemp(join(tmpdir(), 'obdi-actual-'));
  const handle = await api.init({ dataDir, serverURL, password });
  try {
    await api.downloadBudget(
      syncId,
      filePassword ? { password: filePassword } : undefined,
    );
    return await work(api, { refreshSnapshot: () => refreshSnapshot(handle) });
  } finally {
    await api.shutdown();
  }
}

export async function provisionAccounts(client, provision) {
  if (!provision.length) return { bindings: [], lines: [] };
  // Accounts are reused BY NAME, so a label that reads as the sync marker
  // would adopt the marker as one of obdi's accounts (or be renamed away by
  // the next marker write). Refused whole, before any account is created.
  const reserved = provision
    .map((entry) => (entry.label ?? '').trim() || entry.canonical_id)
    .filter(isMarkerName);
  if (reserved.length) {
    throw new Error(
      `account name(s) ${reserved.map((n) => JSON.stringify(n)).join(', ')} are reserved ` +
        'for the sync marker (they end with its suffix); nothing was created',
    );
  }
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

//: Skipped pairs named in a result; the counts beside them stay complete.
const SKIPPED_PAIRS_KEPT = 50;

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
    skipped_pairs: [],
    failed: 0,
  };
  const lines = [];
  if (!transfers.length) return { counts, lines };

  const skip = (reason, line, pair) => {
    counts.skipped[reason] = (counts.skipped[reason] ?? 0) + 1;
    lines.push(line);
    // Which pair and why, without a figure: the page names the two accounts, the
    // date, and the reason, because a bare count left four skipped pairs
    // unexplained across two pushes. The names are obdi's canonical ids, which
    // the envelope carries; the Actual ids stand in for an envelope that lacks them.
    if (counts.skipped_pairs.length < SKIPPED_PAIRS_KEPT) {
      counts.skipped_pairs.push({
        debit_account: pair.debit.account_name ?? pair.debit.account,
        credit_account: pair.credit.account_name ?? pair.credit.account,
        date: typeof pair.debit.date === 'string' ? pair.debit.date : null,
        reason,
      });
    }
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
      skip(verdict, `${named(pair)}: skipped, ${PAIR_REFUSALS[verdict] ?? verdict}`, pair);
      return;
    }
    const payeeForA = transferPayeeOf.get(pair.credit.account);
    const payeeForB = transferPayeeOf.get(pair.debit.account);
    if (!payeeForA || !payeeForB) {
      skip(
        'no_transfer_payee',
        `${named(pair)}: skipped, an account has no transfer payee`,
        pair,
      );
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
