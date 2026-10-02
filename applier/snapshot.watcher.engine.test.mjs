/**
 * Which jobs refresh the server's snapshot, and when, through the watcher's
 * own dispatch and the REAL budget engine, offline. The budget session is a
 * stand-in whose `refreshSnapshot` records what the budget looked like at the
 * moment it was called, so "last step" and "never after a failure" are read
 * from what happened, not from the order of lines in the source.
 *
 * Expectations fixed before the first run:
 *   push, marker                        -> refreshed once, the marker already present
 *   prune that removed 1 orphan         -> refreshed once
 *   prune that removed nothing          -> not refreshed
 *   empty that completed                -> refreshed once, the budget already empty
 *   empty that was refused              -> not refreshed
 *   audit                               -> never refreshed
 *   push that threw                     -> not refreshed, job rejects
 *   refresh that reports failure        -> the job is still ok, the failure is in its result
 */

import assert from 'node:assert/strict';
import { mkdir, mkdtemp, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import process from 'node:process';
import { test } from 'node:test';

import * as api from '@actual-app/api';

import { applyAccounts } from './lib.mjs';

const NOW = new Date('2026-10-02T21:30:00Z');
const hexId = (digit) => `${digit.repeat(64)}:0`;
const row = (digit, amount) => ({
  imported_id: hexId(digit),
  date: '2026-09-01',
  amount,
  payee_name: `Shop ${digit}`,
  cleared: true,
});

async function quietly(work) {
  const log = console.log;
  console.log = () => {};
  try {
    return await work();
  } finally {
    console.log = log;
  }
}

// Runs `jobs` (file name -> request body, written when the budget exists and
// the account ids are known) in order, returning what each gave or threw, and
// what the budget held each time the session's refresh was called.
async function scenario(jobs, { refreshAnswer } = {}) {
  const base = await mkdtemp(join(tmpdir(), 'obdi-snap-watch-'));
  const dataDir = await mkdtemp(join(tmpdir(), 'obdi-snap-budget-'));
  const previous = process.env.OBDI_ACTUAL_DIR;
  process.env.OBDI_ACTUAL_DIR = base;
  try {
    await mkdir(join(base, 'requests'), { recursive: true });
    const { processRequest } = await import(`./watcher.mjs?snap-${Math.random()}`);
    return await quietly(async () => {
      await api.init({ dataDir });
      try {
        await api.runImport(`snap-${Math.random()}`, async () => {});
        const main = await api.createAccount({ name: 'MAIN' }, 0);
        await applyAccounts(api, { [main]: [row('1', -1000), row('2', -2500)] });
        const refreshes = [];
        const session = {
          refreshSnapshot: async () => {
            refreshes.push({ accounts: (await api.getAccounts()).map((a) => a.name) });
            return refreshAnswer ?? { refreshed: true, at: NOW.toISOString() };
          },
        };
        const withBudget = async (work) => work(api, session);
        const outcomes = [];
        for (const [fileName, makeBody] of Object.entries(jobs)) {
          await writeFile(join(base, 'requests', fileName), JSON.stringify(await makeBody({ main })));
          try {
            outcomes.push({
              result: await processRequest(fileName, () => {}, withBudget, () => NOW),
            });
          } catch (error) {
            outcomes.push({ thrown: error });
          }
        }
        return { outcomes, refreshes, main };
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

const body = (kind, extra = {}) => ({ version: 3, kind, accounts: {}, transfers: [], ...extra });
const MARKER = '02 Oct 21:30Z obdi marker';

test('A marker request refreshes the snapshot once, after the marker is in the budget', async () => {
  const { outcomes, refreshes } = await scenario({
    'marker-20261002T213000000001Z.json': () => body('marker'),
  });

  assert.equal(refreshes.length, 1);
  assert.ok(refreshes[0].accounts.includes(MARKER), 'the marker was written before the refresh');
  assert.deepEqual(outcomes[0].result.snapshot, { refreshed: true, at: NOW.toISOString() });
});

test('An applied push refreshes the snapshot once, as its last step', async () => {
  const { outcomes, refreshes } = await scenario({
    'push-20261002T213000000001Z.json': () => ({
      ...body('push'),
      provision: [{ canonical_id: 'new', label: 'Rainy day' }],
      opening_balances: [],
    }),
  });

  assert.equal(refreshes.length, 1);
  assert.ok(refreshes[0].accounts.includes('Rainy day'), 'provisioning had run');
  assert.ok(refreshes[0].accounts.includes(MARKER), 'the marker had been written');
  assert.equal(outcomes[0].result.snapshot.refreshed, true);
});

test('A push that fails refreshes nothing and the job rejects', async () => {
  const { outcomes, refreshes } = await scenario({
    'push-20261002T213000000001Z.json': () => ({
      ...body('push'),
      provision: [{ canonical_id: 'odd', label: 'Household obdi marker' }],
    }),
  });

  assert.equal(refreshes.length, 0);
  assert.match(String(outcomes[0].thrown?.message), /reserved for the sync marker/);
});

test('An audit never refreshes the snapshot and reports no snapshot', async () => {
  const { outcomes, refreshes } = await scenario({
    'audit-20261002T213000000001Z.json': ({ main }) => ({ ...body('audit'), accounts: { [main]: [] } }),
  });

  assert.equal(refreshes.length, 0);
  assert.equal('snapshot' in outcomes[0].result, false);
});

test('A prune that removed an orphan refreshes the snapshot once', async () => {
  const { outcomes, refreshes } = await scenario({
    'prune-20261002T213000000001Z.json': ({ main }) => ({
      ...body('prune'),
      accounts: { [main]: [row('1', -1000)] },
    }),
  });

  assert.equal(outcomes[0].result.accounts[0].removed, 1);
  assert.equal(refreshes.length, 1);
  assert.equal(outcomes[0].result.snapshot.refreshed, true);
});

test('A prune that removed nothing refreshes nothing, because it changed nothing', async () => {
  const { outcomes, refreshes } = await scenario({
    'prune-20261002T213000000001Z.json': ({ main }) => ({
      ...body('prune'),
      accounts: { [main]: [row('1', -1000), row('2', -2500)] },
    }),
  });

  assert.equal(outcomes[0].result.accounts[0].removed, 0);
  assert.equal(refreshes.length, 0);
  assert.equal('snapshot' in outcomes[0].result, false);
});

test('An empty that completed refreshes the snapshot once, with the budget already empty', async () => {
  const { outcomes, refreshes } = await scenario({
    'empty-20261002T213000000001Z.json': ({ main }) => ({
      ...body('empty'),
      empty_accounts: { [main]: 2 },
    }),
  });

  assert.equal(outcomes[0].result.complete, true);
  assert.equal(refreshes.length, 1);
  assert.deepEqual(refreshes[0].accounts, []);
});

test('An empty that was refused refreshes nothing', async () => {
  const { outcomes, refreshes } = await scenario({
    'empty-20261002T213000000001Z.json': ({ main }) => ({
      ...body('empty'),
      empty_accounts: { [main]: 1 },
    }),
  });

  assert.equal(outcomes[0].result.complete, false);
  assert.equal(refreshes.length, 0);
  assert.equal('snapshot' in outcomes[0].result, false);
});

test('A refresh that reports failure leaves the job ok and puts the failure in its result', async () => {
  const failure = { refreshed: false, at: NOW.toISOString(), error: 'the upload was refused (network)' };
  const { outcomes } = await scenario(
    { 'marker-20261002T213000000001Z.json': () => body('marker') },
    { refreshAnswer: failure },
  );

  assert.equal(outcomes[0].thrown, undefined);
  assert.equal(outcomes[0].result.ok, true);
  assert.deepEqual(outcomes[0].result.snapshot, failure);
  assert.equal(outcomes[0].result.marker.name, MARKER, 'the job itself still did its work');
});

test('A stand-in session with no refresh leaves a result without a snapshot field', async () => {
  const base = await mkdtemp(join(tmpdir(), 'obdi-snap-bare-'));
  const dataDir = await mkdtemp(join(tmpdir(), 'obdi-snap-bare-budget-'));
  const previous = process.env.OBDI_ACTUAL_DIR;
  process.env.OBDI_ACTUAL_DIR = base;
  try {
    await mkdir(join(base, 'requests'), { recursive: true });
    await writeFile(
      join(base, 'requests', 'marker-20261002T213000000001Z.json'),
      JSON.stringify(body('marker')),
    );
    const { processRequest } = await import(`./watcher.mjs?snap-bare-${Math.random()}`);
    const result = await quietly(async () => {
      await api.init({ dataDir });
      try {
        await api.runImport(`snap-bare-${Math.random()}`, async () => {});
        return await processRequest(
          'marker-20261002T213000000001Z.json',
          () => {},
          (work) => work(api),
          () => NOW,
        );
      } finally {
        await api.shutdown();
      }
    });

    assert.equal(result.ok, true);
    assert.equal('snapshot' in result, false);
  } finally {
    if (previous === undefined) delete process.env.OBDI_ACTUAL_DIR;
    else process.env.OBDI_ACTUAL_DIR = previous;
    await rm(base, { recursive: true, force: true });
    await rm(dataDir, { recursive: true, force: true });
  }
});
