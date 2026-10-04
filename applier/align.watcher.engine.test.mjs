/**
 * An align request through the watcher's own dispatch and the REAL budget
 * engine, offline. The budget session and the clock are injected, so the
 * marker's name is known before the run.
 *
 * Expectations fixed before the first run:
 *   - a job that ran every step -> kind align, complete, the marker named for
 *     the injected instant written and reported, each step's result beside it
 *   - a job that stopped         -> complete false, the step named, and NO
 *     marker, because a marker would vouch for a budget that is partway
 *   - a request with no scope    -> refused whole before anything is opened
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
const NAME_OF_REQUEST = 'align-20261002T204109000000Z.json';

const hexId = (digit) => `${digit.repeat(64)}:0`;
const P1 = { imported_id: hexId('1'), date: '2026-09-01', amount: -1000, payee_name: 'Shop', cleared: true };
const P2 = { imported_id: hexId('2'), date: '2026-09-02', amount: -2000, payee_name: 'Cafe', cleared: true };

async function quietly(work) {
  const log = console.log;
  console.log = () => {};
  try {
    return await work();
  } finally {
    console.log = log;
  }
}

async function scenario(build, { failImport = false } = {}) {
  const base = await mkdtemp(join(tmpdir(), 'obdi-align-watch-'));
  const dataDir = await mkdtemp(join(tmpdir(), 'obdi-align-budget-'));
  const previous = process.env.OBDI_ACTUAL_DIR;
  process.env.OBDI_ACTUAL_DIR = base;
  try {
    await mkdir(join(base, 'requests'), { recursive: true });
    const { processRequest } = await import(`./watcher.mjs?align-${Math.random()}`);
    return await quietly(async () => {
      await api.init({ dataDir });
      try {
        await api.runImport(`align-watch-${Math.random()}`, async () => {});
        const main = await api.createAccount({ name: 'MAIN' }, 0);
        await writeFile(join(base, 'requests', NAME_OF_REQUEST), JSON.stringify(build(main)));
        const client = failImport
          ? new Proxy(api, {
              get(target, property) {
                if (property === 'importTransactions') {
                  return async () => {
                    throw new Error('the server went away');
                  };
                }
                return target[property];
              },
            })
          : api;
        let result;
        let thrown;
        try {
          result = await processRequest(NAME_OF_REQUEST, () => {}, (work) => work(client), () => NOW);
        } catch (error) {
          thrown = error;
        }
        return { result, thrown, accounts: await api.getAccounts() };
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

const envelope = (main, extra = {}) => ({
  version: 3,
  kind: 'align',
  provision: [],
  accounts: { [main]: [P1, P2] },
  transfers: [],
  opening_balances: [],
  history: [],
  confirmed: { [main]: 0 },
  scope: { [main]: 'explained' },
  ...extra,
});

const markersIn = (accounts) => accounts.filter((a) => a.name.endsWith(' obdi marker'));

test('An align that runs every step writes the marker last and reports every step', async () => {
  const { result, thrown, accounts } = await scenario((main) => envelope(main));

  assert.equal(thrown, undefined);
  assert.equal(result.ok, true);
  assert.equal(result.kind, 'align');
  assert.equal(result.complete, true);
  assert.equal(result.stopped_at, null);
  assert.deepEqual(result.steps.map((s) => s.step), ['push', 'audit', 'prune', 'audit_final']);
  assert.equal(result.steps[0].result.added, 2);
  assert.equal(result.marker.name, NAME);
  assert.deepEqual(markersIn(accounts).map((a) => a.name), [NAME]);
});

test('An align that stops at the push writes no marker and names the step', async () => {
  const { result, accounts } = await scenario((main) => envelope(main), { failImport: true });

  assert.equal(result.ok, true);
  assert.equal(result.complete, false);
  assert.equal(result.stopped_at, 'push');
  assert.equal(result.stopped, 'the server went away');
  assert.equal(result.marker, undefined);
  assert.equal(result.snapshot, undefined);
  assert.deepEqual(markersIn(accounts), []);
});

test('An align with no scope is refused whole and nothing is imported', async () => {
  const { thrown, accounts } = await scenario((main) => {
    const request = envelope(main);
    delete request.scope;
    return request;
  });

  assert.match(thrown.message, /"scope" must be an object/);
  assert.deepEqual(accounts.map((a) => a.name), ['MAIN']);
});

test('An align whose scope names an unknown mode is refused whole', async () => {
  const { thrown } = await scenario((main) => envelope(main, { scope: { [main]: 'everything' } }));

  assert.match(thrown.message, /"scope" must be an object/);
});
