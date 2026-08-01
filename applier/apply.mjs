/**
 * Applies an obdi replay payload to Actual Budget.
 *
 * The only Node in this project, and it exists for one reason: the official
 * client embeds Actual's own budget engine and runs its JavaScript migrations,
 * so it must track the server version exactly. Confining that coupling to one
 * small process keeps the version pin in a single place rather than spread
 * through the ingester.
 *
 * Three behaviours of Actual's importer shape everything here.
 *
 * Use importTransactions, never addTransactions. The raw insert skips
 * reconciliation entirely and silently duplicates on any re-run - and re-runs
 * are the normal case, since replay is meant to be repeatable.
 *
 * imported_id is the idempotency key. Ours is the canonical entity id, so a
 * payment maps to the same row on every replay however many sources observed
 * it, and re-running is a no-op rather than a mess.
 *
 * On a match, existing values win. Actual keeps a payee, category or note set
 * by hand and never touches a reconciled transaction, which is what makes
 * replaying safe to do casually rather than a thing to be nervous about.
 */

import * as api from '@actual-app/api';
import { readFile, mkdtemp } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import process from 'node:process';

// The Python side loads .env, so this must too - otherwise a correctly
// configured project reports a missing setting, which reads as a config error
// when the config is fine.
function loadEnvFile() {
  for (const candidate of ['.env', '../.env']) {
    try {
      process.loadEnvFile(candidate);
      return candidate;
    } catch {
      // Absent or unreadable: fall through to the next, then to real env vars,
      // which is how the container supplies these.
    }
  }
  return null;
}

function required(name) {
  const value = (process.env[name] ?? '').trim();
  if (!value) {
    console.error(`Set ${name}. See .env.example in the repository root.`);
    process.exit(2);
  }
  return value;
}

async function readSecret(name) {
  // Same indirection the Python side uses: config holds a path, not a value,
  // so the file can be read or pasted without leaking a credential.
  const path = (process.env[`${name}_FILE`] ?? '').trim();
  if (path) return (await readFile(path, 'utf8')).trim();
  return required(name);
}

async function main() {
  const [payloadPath] = process.argv.slice(2);
  if (!payloadPath) {
    console.error('Usage: node apply.mjs <payload.json>');
    console.error('Produce the payload with:  obdi replay --out payload.json');
    process.exit(2);
  }

  loadEnvFile();

  const payload = JSON.parse(await readFile(payloadPath, 'utf8'));
  const accounts = Object.entries(payload);
  if (accounts.length === 0) {
    console.log('Payload is empty - nothing to apply.');
    return;
  }

  const serverURL = required('ACTUAL_SERVER_URL');
  const password = await readSecret('ACTUAL_PASSWORD');
  const syncId = required('ACTUAL_SYNC_ID');
  const filePassword = (process.env.ACTUAL_ENCRYPTION_PASSWORD ?? '').trim();

  // The client keeps a local copy of the budget and syncs it, so it needs a
  // writable directory. A temporary one per run keeps this stateless.
  const dataDir = await mkdtemp(join(tmpdir(), 'obdi-actual-'));

  await api.init({ dataDir, serverURL, password });
  try {
    await api.downloadBudget(syncId, filePassword ? { password: filePassword } : undefined);

    let applied = 0;
    for (const [accountId, transactions] of accounts) {
      // The import path, not addTransactions. Rules run, reconciliation runs,
      // and anything already carrying the same imported_id is left alone.
      const result = await api.importTransactions(accountId, transactions);
      const added = result?.added?.length ?? 0;
      const updated = result?.updated?.length ?? 0;
      applied += added;
      console.log(`${accountId}: ${transactions.length} submitted, ${added} added, ${updated} updated`);
    }

    console.log(`\nApplied ${applied} new transaction(s).`);
    console.log('Re-running this is safe: matching imported ids are never added twice.');
  } finally {
    // Always shut down, or the local copy is left mid-sync and the next run
    // starts from an inconsistent cache.
    await api.shutdown();
  }
}

main().catch((error) => {
  console.error(`Failed: ${error.message}`);
  if (/encrypt/i.test(error.message)) {
    console.error('If the budget file is end-to-end encrypted, set ACTUAL_ENCRYPTION_PASSWORD.');
  }
  process.exit(1);
});
