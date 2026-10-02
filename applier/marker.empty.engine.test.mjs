/**
 * Where emptying the budget meets the sync marker, against the REAL budget
 * engine, offline. An empty deletes every account, the marker's included, and
 * is checked against the counts the person was shown, which come from the
 * newest audit. The audit leaves the marker out of its account list (it is not
 * a stray), so it reports the marker's id and row count beside that list, and
 * the page adds them to what an empty is told.
 *
 * Expectations fixed before the first run, for a budget holding MAIN (two
 * imported rows), POT (none), and one marker:
 *   - told = the audit's accounts plus the audit's marker accounts
 *       -> complete, three accounts removed, two rows removed, none left
 *   - told = the audit's accounts alone
 *       -> refused, nothing changed, the marker named as the account not told about
 *   - an empty, then a push -> the marker is created again, as new
 */

import assert from 'node:assert/strict';
import { mkdir, mkdtemp, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import process from 'node:process';
import { test } from 'node:test';

import * as api from '@actual-app/api';

import { auditAccounts } from './audit.mjs';
import { emptyBudget } from './empty.mjs';
import { applyAccounts } from './lib.mjs';
import { readMarker, writeMarker } from './marker.mjs';

const NOW = new Date('2026-10-02T20:41:09Z');

const hexId = (digit) => `${digit.repeat(64)}:0`;
const P1 = { imported_id: hexId('1'), date: '2026-09-01', amount: -1000, payee_name: 'One', cleared: true };
const P2 = { imported_id: hexId('2'), date: '2026-09-02', amount: -2500, payee_name: 'Two', cleared: true };

async function quietly(work) {
  const log = console.log;
  console.log = () => {};
  try {
    return await work();
  } finally {
    console.log = log;
  }
}

async function budget(work) {
  const dataDir = await mkdtemp(join(tmpdir(), 'obdi-marker-empty-'));
  try {
    return await quietly(async () => {
      await api.init({ dataDir });
      try {
        await api.runImport(`marker-empty-${Math.random()}`, async () => {});
        const main = await api.createAccount({ name: 'MAIN' }, 0);
        const pot = await api.createAccount({ name: 'POT', offbudget: true }, 0);
        await applyAccounts(api, { [main]: [P1, P2] });
        await writeMarker(api, NOW);
        return await work({ main, pot });
      } finally {
        await api.shutdown();
      }
    });
  } finally {
    await rm(dataDir, { recursive: true, force: true });
  }
}

// What the page sends: every account the audit listed, then the marker
// accounts it reported beside the list.
async function toldBy(main, pot, { withMarker }) {
  const report = await auditAccounts(api, { [main]: [P1, P2], [pot]: [] });
  const told = {};
  for (const entry of report) {
    if (!entry.missing_account) told[entry.account_id] = entry.rows;
  }
  if (withMarker) {
    for (const account of (await readMarker(api)).accounts) told[account.account_id] = account.rows;
  }
  return told;
}

test('Empty_WhenToldWhatTheAuditReported_RemovesTheMarkerWithEveryOtherAccount', async () => {
  await budget(async ({ main, pot }) => {
    const told = await toldBy(main, pot, { withMarker: true });
    assert.equal(Object.keys(told).length, 3, 'MAIN, POT, and the marker');

    const result = await emptyBudget(api, told);

    assert.equal(result.complete, true);
    assert.equal(result.accounts_removed, 3);
    assert.equal(result.rows_removed, 2);
    assert.deepEqual(await api.getAccounts(), []);
  });
});

test('Empty_WhenToldOnlyTheAuditsAccountList_IsRefusedNamingTheMarkerAndChangesNothing', async () => {
  await budget(async ({ main, pot }) => {
    const told = await toldBy(main, pot, { withMarker: false });

    const result = await emptyBudget(api, told);

    assert.equal(result.complete, false);
    assert.match(result.refused, /1 account not told about \(02 Oct 20:41Z obdi marker\)/);
    assert.equal((await api.getAccounts()).length, 3);
  });
});

test('Empty_ThenAPush_WritesTheMarkerAgainAsNew', async () => {
  const base = await mkdtemp(join(tmpdir(), 'obdi-marker-empty-watch-'));
  const previous = process.env.OBDI_ACTUAL_DIR;
  process.env.OBDI_ACTUAL_DIR = base;
  try {
    await mkdir(join(base, 'requests'), { recursive: true });
    const { processRequest } = await import(`./watcher.mjs?marker-empty-${Math.random()}`);
    await budget(async ({ main, pot }) => {
      const session = (work) => work(api);
      const told = await toldBy(main, pot, { withMarker: true });
      await writeFile(
        join(base, 'requests', 'empty-20261002T210000000000Z.json'),
        JSON.stringify({ version: 3, kind: 'empty', empty_accounts: told }),
      );
      await writeFile(
        join(base, 'requests', 'push-20261002T211000000000Z.json'),
        JSON.stringify({
          version: 3,
          provision: [{ canonical_id: 'main', label: 'Main again' }],
          accounts: {},
          transfers: [],
          opening_balances: [],
        }),
      );

      const emptied = await processRequest('empty-20261002T210000000000Z.json', () => {}, session, () => NOW);
      assert.equal(emptied.complete, true);
      assert.deepEqual(await api.getAccounts(), []);

      const later = new Date('2026-10-02T21:10:00Z');
      const pushed = await processRequest('push-20261002T211000000000Z.json', () => {}, session, () => later);

      assert.equal(pushed.marker.action, 'created');
      assert.equal(pushed.marker.name, '02 Oct 21:10Z obdi marker');
      const names = (await api.getAccounts()).map((a) => a.name).sort();
      assert.deepEqual(names, ['02 Oct 21:10Z obdi marker', 'Main again']);
    });
  } finally {
    if (previous === undefined) delete process.env.OBDI_ACTUAL_DIR;
    else process.env.OBDI_ACTUAL_DIR = previous;
    await rm(base, { recursive: true, force: true });
  }
});
