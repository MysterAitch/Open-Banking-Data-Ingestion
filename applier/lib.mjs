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

import { indexPairRows, judgePair } from './transfers.mjs';

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

export async function withBudget(work) {
  const serverURL = required('ACTUAL_SERVER_URL');
  const password = await readSecret('ACTUAL_PASSWORD');
  const syncId = required('ACTUAL_SYNC_ID');
  const filePassword = (process.env.ACTUAL_ENCRYPTION_PASSWORD ?? '').trim();

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
  for (const entry of provision) {
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
export async function linkTransfers(client, transfers) {
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
  for (const pair of transfers) {
    const { verdict, a, b } = judgePair(pair, rowsByAccount);
    if (verdict === 'linked') {
      counts.already_linked += 1;
      continue;
    }
    if (verdict !== 'linkable') {
      skip(verdict, `${named(pair)}: skipped, ${PAIR_REFUSALS[verdict] ?? verdict}`);
      continue;
    }
    const payeeForA = transferPayeeOf.get(pair.credit.account);
    const payeeForB = transferPayeeOf.get(pair.debit.account);
    if (!payeeForA || !payeeForB) {
      skip('no_transfer_payee', `${named(pair)}: skipped, an account has no transfer payee`);
      continue;
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

  if (attempted.length) {
    const after = await indexPairRows(client, attempted);
    for (const pair of attempted) {
      if (judgePair(pair, after).verdict === 'linked') {
        counts.linked += 1;
      } else {
        counts.failed += 1;
        lines.push(`${named(pair)}: FAILED verification - the rows do not point at each other`);
      }
    }
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
