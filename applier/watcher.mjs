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
import { emptyBudget } from './empty.mjs';
import {
  applyAccounts,
  applyOpeningBalances,
  linkTransfers,
  provisionAccounts,
  withBudget as openBudget,
} from './lib.mjs';
import { readMarker, writeMarker } from './marker.mjs';
import { auditTransfers } from './transfers.mjs';

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

// The one shape a refused or crashed request takes in the results
// directory, so the page shows the reason rather than an unanswered request.
export function failedResult(name, error) {
  return {
    ok: false,
    request: name,
    finished_at: new Date().toISOString(),
    error: String(error?.message ?? error),
  };
}

// `onProgress` receives {phase, done, total} as a long request advances; the
// watcher keeps the latest and writes it into the heartbeat.
// `withBudget` and `now` are parameters only so a test can hand in a budget
// without a server and a clock with a known instant; the watcher always uses
// the real ones.
//
// Which kinds write the sync marker is decided HERE and nowhere else: a push
// writes it as its last step (so a push that failed leaves the old marker),
// the marker kind writes it alone, an audit only reads it (an audit changes
// nothing, and the page says so), a prune neither reads nor writes it, and an
// empty deletes it with every other account (the next push writes it again).
export async function processRequest(
  name,
  onProgress = () => {},
  withBudget = openBudget,
  now = () => new Date(),
) {
  const run = withBudget;
  const requestPath = join(REQUESTS, name);
  const payload = JSON.parse(await readFile(requestPath, 'utf8'));
  const {
    kind,
    provision,
    accounts,
    transfers,
    openings,
    clear_empty,
    confirmed,
    empty_accounts,
  } = parseEnvelope(payload);

  if (kind === 'empty') {
    const outcome = await withBudget((client) =>
      emptyBudget(client, empty_accounts, {
        onProgress: ({ done, total }) => onProgress({ phase: 'emptying', done, total }),
      }),
    );
    // ok means the request ran and answered, not that the budget is empty:
    // `complete` says that, and a refusal or a stop is a complete: false.
    return {
      ok: true,
      kind: 'empty',
      request: name,
      finished_at: new Date().toISOString(),
      ...outcome,
    };
  }

  if (kind === 'marker') {
    const marker = await run((client) => writeMarker(client, now()));
    return {
      ok: true,
      kind: 'marker',
      request: name,
      finished_at: now().toISOString(),
      marker,
    };
  }

  if (kind === 'audit') {
    const { report, pairs, marker } = await run(async (client) => ({
      report: await auditAccounts(client, accounts),
      pairs: await auditTransfers(client, transfers),
      marker: await readMarker(client),
    }));
    return {
      ok: true,
      kind: 'audit',
      request: name,
      finished_at: now().toISOString(),
      accounts: report,
      transfers: pairs,
      marker,
    };
  }

  if (kind === 'prune') {
    const report = await run((client) =>
      pruneAccounts(client, accounts, {
        clear_empty,
        confirmed,
        onProgress: ({ done, total }) => onProgress({ phase: 'removing', done, total }),
      }),
    );
    return {
      ok: true,
      kind: 'prune',
      request: name,
      finished_at: now().toISOString(),
      accounts: report,
    };
  }

  const outcome = await run(async (client) => {
    const provisioned = await provisionAccounts(client, provision);
    const applied = await applyAccounts(client, accounts);
    const opening = await applyOpeningBalances(client, openings);
    const linked = await linkTransfers(client, transfers, {
      onProgress: ({ done, total }) => onProgress({ phase: 'linking', done, total }),
    });
    // Last, and inside the same session, so it reaches the server with the
    // rows it vouches for and is never written by a push that threw.
    const marker = await writeMarker(client, now());
    return { provisioned, applied, opening, linked, marker };
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
    finished_at: now().toISOString(),
    added: outcome.applied.added,
    provisioned: outcome.provisioned.bindings.length,
    transfers: outcome.linked.counts,
    opening_balances: outcome.opening.counts,
    marker: outcome.marker,
    lines: [
      ...outcome.provisioned.lines,
      ...outcome.applied.lines,
      ...outcome.opening.lines,
      ...outcome.linked.lines,
    ],
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
      // Only the latest progress is kept and it rides the next beat: a
      // file write per row would cost more than the work it reports on.
      let progress;
      const stopKeepalive = startKeepalive(async () => {
        await writeFile(
          HEARTBEAT,
          JSON.stringify({ at: new Date().toISOString(), working_on: name, progress }),
        );
        await takeLease(LOCKS, 'actual-apply', 'obdi-applier', 900);
      });
      try {
        result = await processRequest(name, (latest) => {
          progress = latest;
        });
      } finally {
        stopKeepalive();
      }
    } catch (error) {
      result = failedResult(name, error);
    }
    await releaseLease(LOCKS, 'actual-apply');
    await rm(PROCESSING, { force: true });
    await writeFile(join(RESULTS, name), JSON.stringify(result, null, 2));
    await rename(join(REQUESTS, name), join(PROCESSED, name));
    let line = `${name}: FAILED - ${result.error}`;
    if (result.ok && result.kind === 'empty') {
      line =
        `${name}: ${result.complete ? 'emptied' : 'NOT EMPTIED'} ` +
        `(${result.accounts_removed} account(s), ${result.rows_removed} row(s) removed` +
        `${result.refused ? `; refused: ${result.refused}` : ''}` +
        `${result.stopped ? `; stopped: ${result.stopped}` : ''})`;
    } else if (result.ok) {
      if (result.kind === 'audit') {
        line = `${name}: audited ${result.accounts.length} account(s)`;
      } else if (result.kind === 'marker') {
        line = `${name}: marker ${result.marker.action} as "${result.marker.name}"`;
      } else {
        line =
          `${name}: applied (${result.added} added, ${result.provisioned} provisioned, ` +
          `${result.transfers?.linked ?? 0} transfer(s) linked, ` +
          `${result.transfers?.failed ?? 0} failed)`;
      }
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
