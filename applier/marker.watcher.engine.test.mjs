/**
 * Which jobs write the sync marker, through the watcher's own dispatch and the
 * REAL budget engine, offline. The budget session is injected (a temporary
 * offline budget in place of a server download) and so is the clock, so the
 * marker's name is known before the run.
 *
 * Expectations fixed before the first run:
 *   - a marker request, an applied push -> a marker named for the injected
 *     instant, reported in the result
 *   - an audit, a prune                  -> no marker created, none renamed;
 *     the audit reports the name it found (or that it found none)
 *   - a push that throws                 -> no marker, and the job rejects
 */

import assert from 'node:assert/strict';
import { mkdir, mkdtemp, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import process from 'node:process';
import { test } from 'node:test';

import * as api from '@actual-app/api';

const NOW = new Date('2026-10-02T20:41:09Z');
const NAME = '02 Oct 20:41Z obdi marker';
const EARLIER = '01 Oct 08:00Z obdi marker';

const hexId = (digit) => `${digit.repeat(64)}:0`;
const P1 = {
  imported_id: hexId('1'),
  date: '2026-09-01',
  amount: -1000,
  payee_name: 'Shop one',
  cleared: true,
};

async function quietly(work) {
  const log = console.log;
  console.log = () => {};
  try {
    return await work();
  } finally {
    console.log = log;
  }
}

// One offline budget, one watcher module pointed at one request directory.
// `observe` runs inside the budget after the job, so a test can look at what
// the job left behind even when the job threw.
async function scenario(requests, run) {
  const base = await mkdtemp(join(tmpdir(), 'obdi-marker-watch-'));
  const dataDir = await mkdtemp(join(tmpdir(), 'obdi-marker-budget-'));
  const previous = process.env.OBDI_ACTUAL_DIR;
  process.env.OBDI_ACTUAL_DIR = base;
  try {
    await mkdir(join(base, 'requests'), { recursive: true });
    for (const [name, payload] of Object.entries(requests)) {
      await writeFile(join(base, 'requests', name), JSON.stringify(payload));
    }
    // BASE is read when the module loads, so the import follows the
    // environment change and gets a specifier no earlier test used.
    const { processRequest: process_ } = await import(`./watcher.mjs?marker-${Math.random()}`);
    return await quietly(async () => {
      await api.init({ dataDir });
      try {
        await api.runImport(`marker-watch-${Math.random()}`, async () => {});
        const main = await api.createAccount({ name: 'MAIN' }, 0);
        const after = {};
        const deps = {
          now: () => NOW,
          withBudget: async (work) => {
            try {
              return await work(api);
            } finally {
              after.accounts = await api.getAccounts();
            }
          },
        };
        // The tests hand `deps` on as the third argument, as they read best;
        // the watcher takes the session and the clock as two.
        const processRequest = (name, progress, handed = deps) =>
          process_(name, progress, handed.withBudget, handed.now);
        let result;
        let thrown;
        try {
          result = await run({ processRequest, deps, main, base });
        } catch (error) {
          thrown = error;
        }
        const accounts = after.accounts ?? (await api.getAccounts());
        return { result, thrown, accounts, main };
      } finally {
        await api.shutdown();
      }
    });
  } finally {
    if (previous === undefined) delete process.env.OBDI_ACTUAL_DIR;
    else process.env.OBDI_ACTUAL_DIR = previous;
    await rm(base, { recursive: true, force: true });
    await rm(dataDir, { recursive: true, force: true });
  }
}

const markersIn = (accounts) => accounts.filter((a) => a.name.endsWith(' obdi marker'));

test('A marker request writes the marker named for the clock and says so in its result', async () => {
  const { result, thrown, accounts } = await scenario(
    { 'marker-20261002T204109000000Z.json': { version: 3, kind: 'marker' } },
    ({ processRequest, deps }) =>
      processRequest('marker-20261002T204109000000Z.json', () => {}, deps),
  );

  assert.equal(thrown, undefined);
  assert.equal(result.ok, true);
  assert.equal(result.kind, 'marker');
  assert.equal(result.finished_at, NOW.toISOString());
  assert.equal(result.marker.name, NAME);
  assert.equal(result.marker.action, 'created');
  assert.equal(result.marker.at, NOW.toISOString());
  assert.deepEqual(markersIn(accounts).map((a) => a.name), [NAME]);
});

test('A marker request that names accounts and rows still touches none of them', async () => {
  const { result, accounts, main } = await scenario(
    {
      'marker-20261002T204109000000Z.json': {
        version: 3,
        kind: 'marker',
        accounts: {},
        provision: [{ canonical_id: 'x', label: 'Should not exist' }],
      },
    },
    ({ processRequest, deps }) =>
      processRequest('marker-20261002T204109000000Z.json', () => {}, deps),
  );

  assert.equal(result.ok, true);
  assert.deepEqual(accounts.map((a) => a.name).sort(), [NAME, 'MAIN'].sort());
  assert.ok(accounts.some((a) => a.id === main));
});

test('An applied push writes the marker last and reports its name in the result', async () => {
  const { result, thrown, accounts } = await scenario(
    {
      'push-20261002T204109000000Z.json': {
        version: 3,
        provision: [{ canonical_id: 'savings', label: 'Rainy day' }],
        accounts: {},
        transfers: [],
        opening_balances: [],
      },
    },
    ({ processRequest, deps }) =>
      processRequest('push-20261002T204109000000Z.json', () => {}, deps),
  );

  assert.equal(thrown, undefined);
  assert.equal(result.ok, true);
  assert.equal(result.provisioned, 1);
  assert.equal(result.marker.name, NAME);
  assert.deepEqual(markersIn(accounts).map((a) => a.name), [NAME]);
  assert.ok(accounts.some((a) => a.name === 'Rainy day'), 'the push itself still ran');
});

test('A push that fails writes no marker and the job rejects', async () => {
  const { result, thrown, accounts } = await scenario(
    {
      'push-20261002T204109000000Z.json': {
        version: 3,
        provision: [{ canonical_id: 'odd', label: 'Household obdi marker' }],
        accounts: {},
        transfers: [],
        opening_balances: [],
      },
    },
    ({ processRequest, deps }) =>
      processRequest('push-20261002T204109000000Z.json', () => {}, deps),
  );

  assert.equal(result, undefined);
  assert.match(String(thrown?.message), /reserved for the sync marker/);
  assert.deepEqual(markersIn(accounts), []);
});

test('An audit with no marker on the server reports finding none and creates none', async () => {
  const { result, accounts } = await scenario(
    {
      'audit-20261002T204109000000Z.json': {
        version: 3,
        kind: 'audit',
        accounts: {},
        transfers: [],
      },
    },
    ({ processRequest, deps }) =>
      processRequest('audit-20261002T204109000000Z.json', () => {}, deps),
  );

  assert.equal(result.ok, true);
  assert.equal(result.kind, 'audit');
  assert.deepEqual(result.marker, { found: 0, name: null, names: [], accounts: [] });
  assert.deepEqual(markersIn(accounts), []);
});

test('An audit with a marker on the server reports the name it found and leaves it as it was', async () => {
  const { result, accounts } = await scenario(
    {
      'audit-20261002T204109000000Z.json': {
        version: 3,
        kind: 'audit',
        accounts: {},
        transfers: [],
      },
    },
    async ({ processRequest, deps }) => {
      await api.createAccount({ name: EARLIER, offbudget: true }, 0);
      return processRequest('audit-20261002T204109000000Z.json', () => {}, deps);
    },
  );

  assert.equal(result.marker.found, 1);
  assert.equal(result.marker.name, EARLIER);
  assert.deepEqual(result.marker.names, [EARLIER]);
  assert.deepEqual(result.marker.accounts.map((a) => [a.name, a.rows]), [[EARLIER, 0]]);
  assert.deepEqual(markersIn(accounts).map((a) => a.name), [EARLIER]);
  assert.equal(
    result.accounts.some((entry) => entry.unbound_in_actual && /obdi marker/.test(entry.name)),
    false,
  );
});

test('A prune with a marker on the server neither renames it nor reports one', async () => {
  const { result, accounts } = await scenario(
    {
      'prune-20261002T204109000000Z.json': {
        version: 3,
        kind: 'prune',
        accounts: {},
        transfers: [],
      },
    },
    async ({ processRequest, deps }) => {
      await api.createAccount({ name: EARLIER, offbudget: true }, 0);
      return processRequest('prune-20261002T204109000000Z.json', () => {}, deps);
    },
  );

  assert.equal(result.ok, true);
  assert.equal(result.kind, 'prune');
  assert.equal('marker' in result, false);
  assert.deepEqual(markersIn(accounts).map((a) => a.name), [EARLIER]);
});

test('A prune with no marker on the server does not create one', async () => {
  const { accounts } = await scenario(
    {
      'prune-20261002T204109000000Z.json': {
        version: 3,
        kind: 'prune',
        accounts: {},
        transfers: [],
      },
    },
    ({ processRequest, deps }) =>
      processRequest('prune-20261002T204109000000Z.json', () => {}, deps),
  );

  assert.deepEqual(markersIn(accounts), []);
});
