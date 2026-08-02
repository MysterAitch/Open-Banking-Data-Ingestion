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
