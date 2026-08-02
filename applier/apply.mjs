/**
 * Applies an obdi replay payload to Actual Budget - the one-shot CLI.
 *
 * The resident form of this process is watcher.mjs (the stack container,
 * driven by the request queue); this entry point exists for manual runs
 * and first-time verification from a workstation. Both routes share
 * lib.mjs, so they cannot drift.
 *
 * Three behaviours of Actual's importer shape everything here.
 *
 * Use importTransactions, never addTransactions. The raw insert skips
 * reconciliation entirely and silently duplicates on any re-run - and
 * re-runs are the normal case, since replay is meant to be repeatable.
 *
 * imported_id is the idempotency key. Ours is the content identity (content
 * key + occurrence), deliberately not the internal entity id: entity ids
 * re-mint on a store rebuild, while the content key is deterministic over
 * the payment itself. A payment therefore maps to the same Actual row on
 * every replay - across re-runs, across sources, and across rebuilds.
 *
 * On a match, existing values win. Actual keeps a payee, category or note
 * set by hand and never touches a reconciled transaction, which is what
 * makes replaying safe to do casually rather than a thing to be nervous
 * about.
 */

import { readFile } from 'node:fs/promises';
import process from 'node:process';
import { pathToFileURL } from 'node:url';

import { parseEnvelope } from './envelope.mjs';
import { applyAccounts, provisionAccounts, withBudget } from './lib.mjs';

// The Python side loads .env, so this must too - otherwise a correctly
// configured project reports a missing setting, which reads as a config
// error when the config is fine.
function loadEnvFile() {
  for (const candidate of ['.env', '../.env']) {
    try {
      process.loadEnvFile(candidate);
      return candidate;
    } catch {
      // Absent or unreadable: fall through to the next, then to real env
      // vars, which is how the container supplies these.
    }
  }
  return null;
}

async function main() {
  const [payloadPath] = process.argv.slice(2);
  if (!payloadPath) {
    console.error('Usage: node apply.mjs <payload.json>');
    console.error('Produce the payload with:  obdi replay --out payload.json');
    process.exit(2);
  }

  loadEnvFile();

  const raw = JSON.parse(await readFile(payloadPath, 'utf8'));
  const { provision, accounts } = parseEnvelope(raw);
  if (!provision.length && Object.keys(accounts).length === 0) {
    console.log('Payload is empty - nothing to apply.');
    return;
  }

  const outcome = await withBudget(async (client) => {
    const provisioned = await provisionAccounts(client, provision);
    const applied = await applyAccounts(client, accounts);
    return { provisioned, applied };
  });

  for (const line of [...outcome.provisioned.lines, ...outcome.applied.lines]) {
    console.log(line);
  }
  if (outcome.provisioned.bindings.length) {
    console.log('\nMinted bindings (the watcher/push cycle merges these');
    console.log('automatically; for manual runs, add them to accounts.json "actual"):');
    console.log(JSON.stringify(outcome.provisioned.bindings, null, 2));
  }
  console.log(`\nApplied ${outcome.applied.added} new transaction(s).`);
  console.log('Re-running this is safe: matching imported ids are never added twice.');
}

// Guarded like the watcher: the image build imports this module to prove
// the dependency graph resolves, and must not trigger a one-shot apply.
if (import.meta.url === pathToFileURL(process.argv[1] ?? '').href) {
  main().catch((error) => {
    console.error(`Failed: ${error.message}`);
    if (/encrypt/i.test(error.message)) {
      console.error(
        'If the budget file is end-to-end encrypted, set ACTUAL_ENCRYPTION_PASSWORD.',
      );
    }
    process.exit(1);
  });
}
