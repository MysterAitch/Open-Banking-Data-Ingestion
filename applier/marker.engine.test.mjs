/**
 * The sync marker against the REAL budget engine, offline: the pinned library
 * is initialised with no server and each test gets its own temporary budget.
 * Nothing here touches a network.
 *
 * The household is invented. MAIN is on budget, opened with 12345 and holding
 * two imported rows (-1000 and -2500), so its balance is 8845. POT is off
 * budget, opened with 500. Expectations fixed before the first run:
 *   - on-budget total 8845, off-budget total 500, whatever the marker does
 *   - the marker account's own balance is 0 and it holds no rows
 *   - an audit of MAIN expecting exactly those two rows is clean: one report
 *     entry (MAIN), no stray, balance agrees
 */

import assert from 'node:assert/strict';
import { mkdtemp, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { test } from 'node:test';

import * as api from '@actual-app/api';

import { auditAccounts, pruneAccounts } from './audit.mjs';
import { applyAccounts, linkTransfers, provisionAccounts } from './lib.mjs';
import { readMarker, writeMarker } from './marker.mjs';

async function quietly(work) {
  const log = console.log;
  console.log = () => {};
  try {
    return await work();
  } finally {
    console.log = log;
  }
}

let budgets = 0;

async function withBudget(work, { mainOpening = 12345, potOpening = 500 } = {}) {
  const dataDir = await mkdtemp(join(tmpdir(), 'obdi-marker-'));
  try {
    return await quietly(async () => {
      await api.init({ dataDir });
      try {
        budgets += 1;
        await api.runImport(`marker-test-${budgets}`, async () => {});
        const main = await api.createAccount({ name: 'MAIN' }, mainOpening);
        const pot = await api.createAccount({ name: 'POT', offbudget: true }, potOpening);
        return await work({ main, pot });
      } finally {
        await api.shutdown();
      }
    });
  } finally {
    await rm(dataDir, { recursive: true, force: true });
  }
}

const hexId = (digit, n = 0) => `${digit.repeat(64)}:${n}`;
const row = (importedId, date, amount, payee) => ({
  imported_id: importedId,
  date,
  amount,
  payee_name: payee,
  cleared: true,
});
const P1 = row(hexId('1'), '2026-09-01', -1000, 'Shop one');
const P2 = row(hexId('2'), '2026-09-02', -2500, 'Shop two');

const NOW1 = new Date('2026-10-02T20:41:09Z');
const NOW2 = new Date('2026-10-03T01:02:00Z');
const NAME1 = '02 Oct 20:41Z obdi marker';
const NAME2 = '03 Oct 01:02Z obdi marker';

const markers = async () =>
  (await api.getAccounts()).filter((account) => account.name.endsWith(' obdi marker'));
const rowsOf = (id) => api.getTransactions(id, '1900-01-01', '2999-12-31');

async function totals() {
  const accounts = await api.getAccounts();
  const balances = {};
  let onBudget = 0;
  let offBudget = 0;
  for (const account of accounts.filter((a) => !a.name.endsWith(' obdi marker'))) {
    const balance = await api.getAccountBalance(account.id);
    balances[account.name] = balance;
    if (account.offbudget) offBudget += balance;
    else onBudget += balance;
  }
  const month = await api.getBudgetMonth('2026-10');
  return { balances, onBudget, offBudget, toBudget: month.toBudget };
}

test('With no marker in the budget, writing one creates an off-budget account holding no transactions, named for the instant', async () => {
  await withBudget(async () => {
    const written = await writeMarker(api, NOW1);

    assert.equal(written.name, NAME1);
    assert.equal(written.action, 'created');
    assert.equal(written.found, 0);
    const found = await markers();
    assert.equal(found.length, 1);
    assert.equal(found[0].name, NAME1);
    assert.equal(found[0].id, written.id);
    assert.equal(found[0].offbudget, true);
    assert.equal(found[0].closed, false);
    assert.equal((await rowsOf(written.id)).length, 0);
    assert.equal(await api.getAccountBalance(written.id), 0);
  });
});

test('With one marker already there, writing again renames that same account, so there is still exactly one', async () => {
  await withBudget(async () => {
    const first = await writeMarker(api, NOW1);

    const second = await writeMarker(api, NOW2);

    assert.equal(second.action, 'renamed');
    assert.equal(second.found, 1);
    assert.equal(second.id, first.id);
    assert.equal(second.name, NAME2);
    const found = await markers();
    assert.equal(found.length, 1);
    assert.equal(found[0].id, first.id);
    assert.equal(found[0].name, NAME2);
  });
});

test('With two markers (two devices, or a sync conflict) the first is renamed, both are kept, and the result says two were found', async () => {
  await withBudget(async () => {
    const one = await api.createAccount({ name: '01 Oct 08:00Z obdi marker', offbudget: true }, 0);
    const two = await api.createAccount({ name: '01 Oct 09:00Z obdi marker', offbudget: true }, 0);

    const written = await writeMarker(api, NOW1);

    assert.equal(written.found, 2);
    assert.equal(written.id, one);
    assert.equal(written.name, NAME1);
    assert.match(written.note, /2 marker accounts were found/);
    assert.match(written.note, /1 other was left alone/);
    const byId = new Map((await markers()).map((account) => [account.id, account.name]));
    assert.equal(byId.size, 2);
    assert.equal(byId.get(one), NAME1);
    assert.equal(byId.get(two), '01 Oct 09:00Z obdi marker');
  });
});

test('Writing a marker changes no balance and no budget figure: on-budget and off-budget totals and every other account are as they were', async () => {
  await withBudget(async ({ main, pot }) => {
    await applyAccounts(api, { [main]: [P1, P2] });
    const before = await totals();
    assert.equal(before.onBudget, 8845);
    assert.equal(before.offBudget, 500);
    assert.deepEqual(before.balances, { MAIN: 8845, POT: 500 });

    await writeMarker(api, NOW1);
    await writeMarker(api, NOW2);

    assert.deepEqual(await totals(), before);
    assert.equal((await rowsOf(main)).length, 3, 'MAIN holds its opening row and the two imports');
    assert.equal((await rowsOf(pot)).length, 1, 'POT holds its opening row');
  });
});

test('Reading the marker reports none, then its name, and counts two without choosing for the caller', async () => {
  await withBudget(async () => {
    assert.deepEqual(await readMarker(api), { found: 0, name: null, names: [], accounts: [] });

    const written = await writeMarker(api, NOW1);
    assert.deepEqual(await readMarker(api), {
      found: 1,
      name: NAME1,
      names: [NAME1],
      accounts: [{ account_id: written.id, name: NAME1, rows: 0 }],
    });

    await api.createAccount({ name: '01 Oct 09:00Z obdi marker', offbudget: true }, 0);
    const two = await readMarker(api);
    assert.equal(two.found, 2);
    assert.equal(two.name, NAME1);
    assert.equal(two.names.length, 2);
  });
});

test('An audit of a correct budget with a marker present is clean: the marker is neither a stray nor a difference', async () => {
  await withBudget(
    async ({ main, pot }) => {
      await applyAccounts(api, { [main]: [P1, P2] });
      await writeMarker(api, NOW1);

      const report = await auditAccounts(api, { [main]: [P1, P2], [pot]: [] });

      assert.equal(report.filter((entry) => entry.unbound_in_actual).length, 0);
      assert.equal(report.length, 2, 'MAIN and POT only');
      for (const entry of report) {
        assert.equal(entry.missing, 0);
        assert.equal(entry.orphaned, 0);
        assert.equal(entry.diverged, 0);
        assert.equal(entry.balance.agrees, true);
      }
      assert.equal(report.some((entry) => /obdi marker/.test(entry.name ?? '')), false);
    },
    { mainOpening: 0, potOpening: 0 },
  );
});

test('An audit still names a real stray, and an unbound account that only mentions the words mid-name is a stray too', async () => {
  await withBudget(async ({ main }) => {
    await writeMarker(api, NOW1);
    await api.createAccount({ name: 'Old joint' }, 0);
    await api.createAccount({ name: 'obdi marker notes' }, 0);

    const report = await auditAccounts(api, { [main]: [P1, P2] });

    const strays = report.filter((entry) => entry.unbound_in_actual).map((entry) => entry.name).sort();
    assert.deepEqual(strays, ['Old joint', 'POT', 'obdi marker notes'].sort());
  });
});

test('An audit neither creates a marker where there is none nor renames the one there is', async () => {
  await withBudget(async ({ main }) => {
    await auditAccounts(api, { [main]: [] });
    assert.equal((await markers()).length, 0);

    const written = await writeMarker(api, NOW1);
    await auditAccounts(api, { [main]: [] });

    const found = await markers();
    assert.equal(found.length, 1);
    assert.equal(found[0].id, written.id);
    assert.equal(found[0].name, NAME1);
  });
});

test('A prune with a marker present leaves it alone and reports only the accounts it was given', async () => {
  await withBudget(async ({ main }) => {
    await applyAccounts(api, { [main]: [P1, P2] });
    const written = await writeMarker(api, NOW1);

    const report = await pruneAccounts(api, { [main]: [P1, P2] });

    assert.deepEqual(report.map((entry) => entry.account_id), [main]);
    assert.equal(report[0].removed, 0);
    const found = await markers();
    assert.equal(found.length, 1);
    assert.equal(found[0].id, written.id);
    assert.equal(found[0].name, NAME1);
  });
});

test('Provisioning with a marker present creates and reuses obdi accounts by name exactly as before, and never touches the marker', async () => {
  await withBudget(async () => {
    const written = await writeMarker(api, NOW1);
    const provision = [{ canonical_id: 'savings', label: 'Rainy day' }];

    const first = await provisionAccounts(api, provision);
    const again = await provisionAccounts(api, provision);

    assert.deepEqual(first.lines, ['Rainy day: created']);
    assert.deepEqual(again.lines, ['Rainy day: already exists, reused']);
    assert.equal(first.bindings[0].actual_account_id, again.bindings[0].actual_account_id);
    assert.notEqual(first.bindings[0].actual_account_id, written.id);
    const found = await markers();
    assert.equal(found.length, 1);
    assert.equal(found[0].name, NAME1);
  });
});

test('Provisioning refuses a label shaped like a marker, before creating anything, so obdi\'s own account can never be taken for the marker', async () => {
  await withBudget(async () => {
    const before = (await api.getAccounts()).length;

    await assert.rejects(
      () =>
        provisionAccounts(api, [
          { canonical_id: 'fine', label: 'Fine account' },
          { canonical_id: 'odd', label: 'Household obdi marker' },
        ]),
      /reserved for the sync marker/,
    );

    assert.equal((await api.getAccounts()).length, before, 'not even the first, fine, account was created');
  });
});

test('Transfer linking with a marker present links the pair exactly as before', async () => {
  await withBudget(async ({ main, pot }) => {
    const out = row(hexId('a'), '2026-09-07', -150000, 'Savings pot');
    const into = row(hexId('b'), '2026-09-07', 150000, 'Current account');
    await applyAccounts(api, { [main]: [out], [pot]: [into] });
    await writeMarker(api, NOW1);
    const leg = (account, r) => ({
      account,
      imported_id: r.imported_id,
      date: r.date,
      amount: r.amount,
    });

    const { counts } = await linkTransfers(api, [
      { debit: leg(main, out), credit: leg(pot, into) },
    ]);

    assert.equal(counts.linked, 1);
    assert.equal(counts.failed, 0);
    const mainRow = (await rowsOf(main)).find((r) => r.imported_id === out.imported_id);
    const potRow = (await rowsOf(pot)).find((r) => r.imported_id === into.imported_id);
    assert.equal(mainRow.transfer_id, potRow.id);
    assert.equal(potRow.transfer_id, mainRow.id);
    assert.equal((await markers()).length, 1);
  });
});
