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
  rm,
  writeFile,
} from 'node:fs/promises';
import { join } from 'node:path';
import process from 'node:process';
import { pathToFileURL } from 'node:url';

import { auditAccounts, pruneAccounts } from './audit.mjs';
import { leaseHeld, releaseLease, takeLease } from './lease.mjs';
import { byQueuedStamp, mergeBindings, parseEnvelope } from './envelope.mjs';
import { applyAccounts, provisionAccounts, withBudget } from './lib.mjs';

const BASE = (process.env.OBDI_ACTUAL_DIR ?? '/data/actual').trim();
const POLL_SECONDS = Number(process.env.OBDI_ACTUAL_POLL_SECONDS ?? '20');

const REQUESTS = join(BASE, 'requests');
const RESULTS = join(BASE, 'results');
const PROCESSED = join(BASE, 'processed');
const BINDINGS = join(BASE, 'bindings-pending.json');
const HEARTBEAT = join(BASE, 'heartbeat.json');
const PROCESSING = join(BASE, 'processing.json');
const LOCKS = (process.env.OBDI_LOCKS_DIR ?? '/data/locks').trim();

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

  if (kind === 'prune') {
    const report = await withBudget((client) => pruneAccounts(client, accounts));
    return {
      ok: true,
      kind: 'prune',
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

//: How often a running request renews its heartbeat and its lease.
const KEEPALIVE_MS = 60_000;

export function startKeepalive(beat, intervalMs = KEEPALIVE_MS) {
  // Returns its own stop function so the caller cannot forget which timer
  // belongs to which request. Unref'd: a pending beat must never be the
  // reason the process stays up.
  const timer = setInterval(() => {
    // A missed renewal is retried on the next beat. The rejection is
    // swallowed deliberately: an unhandled one would take the container
    // down mid-import, which is the outcome the lease and the heartbeat
    // exist to prevent.
    Promise.resolve(beat()).catch(() => {});
  }, intervalMs);
  timer.unref?.();
  return () => clearInterval(timer);
}

async function tick() {
  // Stamped every poll, and again on a keepalive while a request runs -
  // the page compares this with the clock, so "queued and nobody is
  // coming" diagnoses itself, and a long import is never mistaken for a
  // dead applier.
  await writeFile(HEARTBEAT, JSON.stringify({ at: new Date().toISOString() }));
  // An update about to recreate this container takes the stack-update
  // lease; starting an import underneath it would be killed half-done.
  // The queue is durable - requests simply wait for the next tick.
  if (await leaseHeld(LOCKS, 'stack-update')) return;
  const entries = (await readdir(REQUESTS))
    .filter((f) => f.endsWith('.json'))
    .sort(byQueuedStamp);
  for (const name of entries) {
    let result;
    try {
      await takeLease(LOCKS, 'actual-apply', 'obdi-applier', 900);
      // The request file stays in the queue until the result is written,
      // so without this marker the page cannot tell "waiting" from
      // "being worked on right now" - a long audit read as stuck.
      await writeFile(
        PROCESSING,
        JSON.stringify({ name, started_at: new Date().toISOString() }),
      );
      // The lease and the heartbeat are both stamped once, above; work
      // longer than their horizon must renew them or it silently loses
      // the protection it is relying on.
      const stopKeepalive = startKeepalive(async () => {
        await writeFile(
          HEARTBEAT,
          JSON.stringify({ at: new Date().toISOString(), working_on: name }),
        );
        await takeLease(LOCKS, 'actual-apply', 'obdi-applier', 900);
      });
      try {
        result = await processRequest(name);
      } finally {
        stopKeepalive();
      }
    } catch (error) {
      result = {
        ok: false,
        request: name,
        finished_at: new Date().toISOString(),
        error: String(error?.message ?? error),
      };
    }
    await releaseLease(LOCKS, 'actual-apply');
    await rm(PROCESSING, { force: true });
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
  for (const dir of [REQUESTS, RESULTS, PROCESSED, LOCKS]) {
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

// Guarded so the image build can import this module to prove the whole
// dependency graph resolves - a COPY list that misses a file otherwise
// ships an image that only fails at runtime, as a crash loop.
if (import.meta.url === pathToFileURL(process.argv[1] ?? '').href) {
  main();
}
