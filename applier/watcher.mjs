/**
 * The applier as a resident of the stack: watch a request directory on the
 * shared volume, apply each envelope, answer with a result file.
 *
 * The Python side and this process never call each other - the boundary is
 * two directories of JSON on the /data volume. obdi-web drops a request
 * when the button is pressed; obdi-pull drops one after scheduled cycles;
 * this loop notices, provisions any missing Actual accounts, imports the
 * transactions, and writes what happened where the web page can read it.
 * Minted bindings accumulate in bindings-pending.json, which the Python
 * side merges into the account map before building its NEXT envelope - so
 * a newly provisioned account's transactions ride the following push.
 *
 * Polling, not inotify: the volume is shared between containers where
 * filesystem events are unreliable, and a request is not latency-critical.
 */

import {
  mkdir,
  readdir,
  readFile,
  rename,
  writeFile,
} from 'node:fs/promises';
import { join } from 'node:path';
import process from 'node:process';

import { auditAccounts } from './audit.mjs';
import { mergeBindings, parseEnvelope } from './envelope.mjs';
import { applyAccounts, provisionAccounts, withBudget } from './lib.mjs';

const BASE = (process.env.OBDI_ACTUAL_DIR ?? '/data/actual').trim();
const POLL_SECONDS = Number(process.env.OBDI_ACTUAL_POLL_SECONDS ?? '20');

const REQUESTS = join(BASE, 'requests');
const RESULTS = join(BASE, 'results');
const PROCESSED = join(BASE, 'processed');
const BINDINGS = join(BASE, 'bindings-pending.json');

async function readJsonOr(path, fallback) {
  try {
    return JSON.parse(await readFile(path, 'utf8'));
  } catch {
    return fallback;
  }
}

async function processRequest(name) {
  const requestPath = join(REQUESTS, name);
  const payload = JSON.parse(await readFile(requestPath, 'utf8'));
  const { kind, provision, accounts } = parseEnvelope(payload);

  if (kind === 'audit') {
    const report = await withBudget((client) => auditAccounts(client, accounts));
    return {
      ok: true,
      kind: 'audit',
      request: name,
      finished_at: new Date().toISOString(),
      accounts: report,
    };
  }

  const outcome = await withBudget(async (client) => {
    const provisioned = await provisionAccounts(client, provision);
    const applied = await applyAccounts(client, accounts);
    return { provisioned, applied };
  });

  if (outcome.provisioned.bindings.length) {
    const existing = await readJsonOr(BINDINGS, []);
    await writeFile(
      BINDINGS,
      JSON.stringify(mergeBindings(existing, outcome.provisioned.bindings), null, 2),
    );
  }

  return {
    ok: true,
    request: name,
    finished_at: new Date().toISOString(),
    added: outcome.applied.added,
    provisioned: outcome.provisioned.bindings.length,
    lines: [...outcome.provisioned.lines, ...outcome.applied.lines],
  };
}

async function tick() {
  const entries = (await readdir(REQUESTS)).filter((f) => f.endsWith('.json')).sort();
  for (const name of entries) {
    let result;
    try {
      result = await processRequest(name);
    } catch (error) {
      result = {
        ok: false,
        request: name,
        finished_at: new Date().toISOString(),
        error: String(error?.message ?? error),
      };
    }
    await writeFile(join(RESULTS, name), JSON.stringify(result, null, 2));
    await rename(join(REQUESTS, name), join(PROCESSED, name));
    let line = `${name}: FAILED - ${result.error}`;
    if (result.ok) {
      line =
        result.kind === 'audit'
          ? `${name}: audited ${result.accounts.length} account(s)`
          : `${name}: applied (${result.added} added, ${result.provisioned} provisioned)`;
    }
    console.log(line);
  }
}

async function main() {
  for (const dir of [REQUESTS, RESULTS, PROCESSED]) {
    await mkdir(dir, { recursive: true });
  }
  console.log(`watching ${REQUESTS} every ${POLL_SECONDS}s`);
  for (;;) {
    try {
      await tick();
    } catch (error) {
      // The loop must outlive any single bad request or transient outage.
      console.error(`tick failed: ${error?.message ?? error}`);
    }
    await new Promise((resolve) => setTimeout(resolve, POLL_SECONDS * 1000));
  }
}

main();
