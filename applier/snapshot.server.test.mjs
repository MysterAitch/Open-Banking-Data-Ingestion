/**
 * Refreshing the server's snapshot, against a REAL Actual sync server of the
 * same version as the pinned library, started on localhost on a free port with
 * its own temporary data directory. Nothing leaves this machine, and no real
 * budget is involved: every budget here is invented.
 *
 * What it shows, with the answers decided before the first run:
 *   - a client that downloads afresh replays EVERY change since the stored
 *     file: with N transactions added after the file was stored, it applies at
 *     least N messages
 *   - after a refresh (sync, then upload-budget) a further fresh download
 *     applies none of them, and holds the same data
 *   - the Sync ID (the cloud file id) is unchanged by the refresh
 *   - a client that was already connected before the refresh still syncs
 *     afterwards, in both directions
 *
 * Skipped, with the reason stated, where the server package is not installed
 * (it is a development dependency of the applier only) or will not start.
 */

import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { existsSync } from 'node:fs';
import { mkdtemp, rm } from 'node:fs/promises';
import net from 'node:net';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import process from 'node:process';
import { fileURLToPath } from 'node:url';
import { after, before, describe, test } from 'node:test';

import * as api from '@actual-app/api';

import { refreshSnapshot } from './lib.mjs';

const ENTRY = fileURLToPath(
  new URL('./node_modules/@actual-app/sync-server/build/bin/actual-server.js', import.meta.url),
);
const PASSWORD = 'invented-test-password';
const STARTUP_MS = 60_000;
const N = 400;

const freePort = () =>
  new Promise((resolve) => {
    const probe = net.createServer();
    probe.listen(0, '127.0.0.1', () => {
      const { port } = probe.address();
      probe.close(() => resolve(port));
    });
  });

const removeDir = (dir) => rm(dir, { recursive: true, force: true, maxRetries: 20, retryDelay: 250 });

async function startServer() {
  const dataDir = await mkdtemp(join(tmpdir(), 'obdi-sync-server-'));
  const port = await freePort();
  const child = spawn(process.execPath, [ENTRY], {
    env: {
      ...process.env,
      ACTUAL_DATA_DIR: dataDir,
      ACTUAL_PORT: String(port),
      ACTUAL_HOSTNAME: '127.0.0.1',
    },
    stdio: ['ignore', 'pipe', 'pipe'],
  });
  let output = '';
  child.stdout.on('data', (chunk) => (output += chunk));
  child.stderr.on('data', (chunk) => (output += chunk));
  const exited = new Promise((resolve) => child.once('exit', resolve));
  const url = `http://127.0.0.1:${port}`;
  const stop = async () => {
    child.kill();
    await exited;
    await removeDir(dataDir);
  };
  const deadline = Date.now() + STARTUP_MS;
  for (;;) {
    try {
      if ((await fetch(`${url}/info`)).ok) break;
    } catch {
      // Not listening yet.
    }
    if (child.exitCode !== null || Date.now() > deadline) {
      await stop();
      return { failed: `the server did not start: ${output.slice(-300)}` };
    }
    await new Promise((resolve) => setTimeout(resolve, 250));
  }
  const bootstrap = await fetch(`${url}/account/bootstrap`, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ password: PASSWORD }),
  });
  if (!bootstrap.ok) {
    await stop();
    return { failed: `the server refused its first password (${bootstrap.status})` };
  }
  return { url, stop };
}

// One client session in its own data directory: the messages it applied while
// connecting are counted from the library's own log lines.
async function session(serverURL, dataDir, work) {
  const lines = [];
  const { log, info } = console;
  const grab = (...args) => lines.push(args.map(String).join(' '));
  console.log = grab;
  console.info = grab;
  const applied = () =>
    lines
      .map((line) => /Got messages from server (\d+)/.exec(line))
      .filter(Boolean)
      .reduce((sum, match) => sum + Number(match[1]), 0);
  try {
    const handle = await api.init({ dataDir, serverURL, password: PASSWORD, verbose: true });
    try {
      return await work({ handle, applied });
    } finally {
      await api.shutdown();
    }
  } finally {
    console.log = log;
    console.info = info;
  }
}

const transactions = (from, count) =>
  Array.from({ length: count }, (_, index) => ({
    date: '2026-09-01',
    amount: -(100 + from + index),
    notes: `row ${from + index}`,
  }));

async function held(accountName = 'MAIN') {
  const account = (await api.getAccounts()).find((a) => a.name === accountName);
  const rows = await api.getTransactions(account.id, '1900-01-01', '2999-12-31');
  return {
    id: account.id,
    rows: rows.map((r) => `${r.amount}|${r.date}|${r.notes}`).sort(),
    balance: await api.getAccountBalance(account.id),
  };
}

describe('Snapshot refresh against a real sync server', () => {
  let server;
  let skipReason = null;
  const dirs = {};
  const world = {};

  before(async () => {
    if (!existsSync(ENTRY)) {
      skipReason = 'the sync server package is not installed (a development dependency of the applier)';
      return;
    }
    server = await startServer();
    if (server.failed) {
      skipReason = server.failed;
      server = null;
      return;
    }
    for (const name of ['a', 'b', 'c', 'd', 'e']) {
      dirs[name] = await mkdtemp(join(tmpdir(), `obdi-sync-client-${name}-`));
    }
  });

  after(async () => {
    if (server) await server.stop();
    for (const dir of Object.values(dirs)) await removeDir(dir);
  });

  const need = (t) => {
    if (skipReason) t.skip(skipReason);
    return !skipReason;
  };

  test('A budget stored with a backlog of changes makes a fresh download replay all of them', { timeout: 240_000 }, async (t) => {
    if (!need(t)) return;
    await session(server.url, dirs.a, async () => {
      await api.runImport('snapshot-test-budget', async () => {
        await api.createAccount({ name: 'MAIN' }, 0);
      });
      const remote = (await api.getBudgets()).find((b) => b.cloudFileId);
      // What Actual's settings call the Sync ID, and what downloadBudget is
      // given, is the group id; the cloud file id names the stored file.
      world.syncId = remote.groupId;
      world.fileId = remote.cloudFileId;
      const { id } = await held();
      for (let from = 0; from < N; from += 100) {
        await api.addTransactions(id, transactions(from, 100));
      }
      await api.sync();
    });

    await session(server.url, dirs.b, async ({ applied }) => {
      await api.downloadBudget(world.syncId);
      world.before = await held();
      world.replayedBefore = applied();
    });

    assert.equal(world.before.rows.length, N);
    assert.ok(
      world.replayedBefore >= N,
      `a fresh download applied ${world.replayedBefore} messages for ${N} added transactions`,
    );
  });

  test('After a refresh a fresh download applies no backlog, holds the same data, and the Sync ID is unchanged', { timeout: 240_000 }, async (t) => {
    if (!need(t)) return;
    let outcome;
    await session(server.url, dirs.c, async ({ handle }) => {
      await api.downloadBudget(world.syncId);
      outcome = await refreshSnapshot(handle);
      const remote = (await api.getBudgets()).find((b) => b.cloudFileId);
      world.after = { syncId: remote?.groupId, fileId: remote?.cloudFileId };
    });
    assert.equal(outcome.refreshed, true, outcome.error);
    assert.equal(world.after.syncId, world.syncId, 'the Sync ID is kept');
    assert.equal(world.after.fileId, world.fileId, 'the stored file is replaced, not a second one made');

    let after;
    let replayed;
    await session(server.url, dirs.d, async ({ applied }) => {
      await api.downloadBudget(world.syncId);
      after = await held();
      replayed = applied();
    });

    assert.deepEqual(after.rows, world.before.rows, 'the same data');
    assert.equal(after.balance, world.before.balance);
    assert.ok(
      replayed < world.replayedBefore / 10,
      `before the refresh a download applied ${world.replayedBefore} messages, after it ${replayed}`,
    );
    world.replayedAfter = replayed;
    t.diagnostic(
      `messages applied by a fresh download: ${world.replayedBefore} before the refresh, ${replayed} after`,
    );
  });

  test('Clients connected before the refresh still sync after it, in both directions', { timeout: 240_000 }, async (t) => {
    if (!need(t)) return;
    await session(server.url, dirs.a, async () => {
      const local = (await api.getBudgets()).find((b) => b.id && !b.state);
      await api.loadBudget(local.id);
      const { id } = await held();
      await api.addTransactions(id, transactions(1000, 5));
      const synced = await api.sync();
      assert.equal(synced?.error, undefined);
    });

    let seen;
    await session(server.url, dirs.b, async () => {
      const local = (await api.getBudgets()).find((b) => b.id && !b.state);
      await api.loadBudget(local.id);
      await api.sync();
      seen = await held();
    });
    assert.equal(seen.rows.length, N + 5, 'a client connected before the refresh received the new rows');

    let fresh;
    await session(server.url, dirs.e, async () => {
      await api.downloadBudget(world.syncId);
      fresh = await held();
    });
    assert.deepEqual(fresh.rows, seen.rows, 'a fresh download agrees with the long-connected client');
  });

  test('A marker request through the applier\'s own session writes the marker and leaves the server with a current snapshot', { timeout: 240_000 }, async (t) => {
    if (!need(t)) return;
    const { mkdir, writeFile } = await import('node:fs/promises');
    const base = await mkdtemp(join(tmpdir(), 'obdi-snap-server-watch-'));
    const saved = {
      OBDI_ACTUAL_DIR: process.env.OBDI_ACTUAL_DIR,
      ACTUAL_SERVER_URL: process.env.ACTUAL_SERVER_URL,
      ACTUAL_PASSWORD: process.env.ACTUAL_PASSWORD,
      ACTUAL_SYNC_ID: process.env.ACTUAL_SYNC_ID,
    };
    process.env.OBDI_ACTUAL_DIR = base;
    process.env.ACTUAL_SERVER_URL = server.url;
    process.env.ACTUAL_PASSWORD = PASSWORD;
    process.env.ACTUAL_SYNC_ID = world.syncId;
    try {
      await mkdir(join(base, 'requests'), { recursive: true });
      await writeFile(
        join(base, 'requests', 'marker-20261002T220000000001Z.json'),
        JSON.stringify({ version: 3, kind: 'marker' }),
      );
      const { processRequest } = await import(`./watcher.mjs?snap-server-${Math.random()}`);

      const result = await processRequest(
        'marker-20261002T220000000001Z.json',
        () => {},
        undefined,
        () => new Date('2026-10-02T22:00:30Z'),
      );

      assert.equal(result.marker.name, '02 Oct 22:00Z obdi marker');
      assert.equal(result.snapshot.refreshed, true, result.snapshot.error);
      let names;
      let replayed;
      await session(server.url, dirs.d, async ({ applied }) => {
        const local = (await api.getBudgets()).find((b) => b.id && !b.state);
        await removeDir(join(dirs.d, local.id));
        await api.downloadBudget(world.syncId);
        names = (await api.getAccounts()).map((a) => a.name).sort();
        replayed = applied();
      });
      assert.deepEqual(names, ['02 Oct 22:00Z obdi marker', 'MAIN']);
      assert.equal(replayed, 0, 'the refreshed snapshot already holds everything the job did');
    } finally {
      for (const [key, value] of Object.entries(saved)) {
        if (value === undefined) delete process.env[key];
        else process.env[key] = value;
      }
      await removeDir(base);
    }
  });
});
