/**
 * Opening-balance rows against the REAL budget engine.
 *
 * The same offline setup as transfers.engine.test.mjs: the pinned library is
 * initialised with no server, a fresh local budget is created, and each test
 * gets its own in a temporary directory that is removed afterwards. Nothing
 * here touches a network. A claim such as "re-importing an opening row with a
 * new amount changes nothing" is a claim about Actual's own behaviour, so it
 * is measured here rather than assumed from a stand-in.
 *
 * The household is invented. One account, MAIN, holds income of 2000.00 on
 * 2026-09-01 and a 100.00 spend on 2026-09-03, so its rows sum to 1900.00. Its
 * opening balance is 1000.00, applying at the end of 2026-08-31, so a right
 * balance is 2900.00. The expectations were fixed before the first run.
 */

import assert from 'node:assert/strict';
import { mkdtemp, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { test } from 'node:test';

import * as api from '@actual-app/api';

import { auditAccounts, choosePrunable, createLinkContext, pruneAccounts } from './audit.mjs';
import { applyAccounts, applyOpeningBalances } from './lib.mjs';

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

async function withBudget(work) {
  const dataDir = await mkdtemp(join(tmpdir(), 'obdi-opening-'));
  try {
    return await quietly(async () => {
      await api.init({ dataDir });
      try {
        budgets += 1;
        await api.runImport(`opening-test-${budgets}`, async () => {});
        const main = await api.createAccount({ name: 'MAIN' }, 0);
        return await work({ main });
      } finally {
        await api.shutdown();
      }
    });
  } finally {
    await rm(dataDir, { recursive: true, force: true });
  }
}

const OPENING_ID = 'obdi-opening:household-main';

const openingRow = (amount = 100000, date = '2026-08-31') => ({
  imported_id: OPENING_ID,
  date,
  amount,
  payee_name: 'Starting Balance',
  starting_balance_flag: true,
  cleared: true,
});

const entryFor = (account, row) => ({
  account,
  imported_id: row.imported_id,
  date: row.date,
  amount: row.amount,
});

const INCOME = {
  imported_id: `${'a'.repeat(64)}:0`,
  date: '2026-09-01',
  amount: 200000,
  payee_name: 'Employer',
  cleared: true,
};
const SPEND = {
  imported_id: `${'b'.repeat(64)}:0`,
  date: '2026-09-03',
  amount: -10000,
  payee_name: 'Shop',
  cleared: true,
};

function envelopeFor({ main }, opening = openingRow()) {
  const rows = opening ? [opening, INCOME, SPEND] : [INCOME, SPEND];
  return {
    accounts: { [main]: rows },
    openings: opening ? [entryFor(main, opening)] : [],
  };
}

async function push(client, envelope) {
  const applied = await applyAccounts(client, envelope.accounts);
  const opening = await applyOpeningBalances(client, envelope.openings);
  return { applied, opening };
}

const readRows = (accountId) => api.getTransactions(accountId, '1900-01-01', '2999-12-31');

const findOpening = async (accountId) =>
  (await readRows(accountId)).find((r) => r.imported_id === OPENING_ID);

// The engine applies an update or a delete AFTER the call has resolved (an
// immediate read returns the old rows), so every read that follows one waits
// for the condition the test is about, and fails by timeout rather than by
// guessing a delay. Measured on the pinned library, 26.7.0: about 100ms.
async function settled(read, accepts, timeoutMs = 10000) {
  const deadline = Date.now() + timeoutMs;
  let value = await read();
  while (!accepts(value) && Date.now() < deadline) {
    await new Promise((resolve) => setTimeout(resolve, 50));
    value = await read();
  }
  return value;
}

const openingSettledAs = (accountId, accepts) => settled(() => findOpening(accountId), accepts);

// A client that counts the calls that would change a row.
function spied() {
  const calls = { updateTransaction: [], deleteTransaction: [], importTransactions: 0 };
  return {
    calls,
    client: new Proxy(api, {
      get(target, property) {
        if (property === 'updateTransaction' || property === 'deleteTransaction') {
          return async (...args) => {
            calls[property].push(args);
            return target[property](...args);
          };
        }
        if (property === 'importTransactions') {
          return async (...args) => {
            calls.importTransactions += 1;
            return target.importTransactions(...args);
          };
        }
        return target[property];
      },
    }),
  };
}

test('OpeningPush_WhenFirstSent_CreatesTheRowAndTheBalanceIsOpeningPlusRows', async () => {
  await withBudget(async (ctx) => {
    const result = await push(api, envelopeFor(ctx));

    assert.equal(result.applied.added, 3);
    assert.equal(await api.getAccountBalance(ctx.main), 290000);
    const row = await findOpening(ctx.main);
    assert.ok(row, 'the opening row exists under its reserved imported id');
    assert.equal(row.amount, 100000);
    assert.equal(row.date, '2026-08-31');
    assert.equal(row.cleared, true);
    assert.deepEqual(
      { already_right: 1, corrected: 0, missing: 0, failed: 0 },
      {
        already_right: result.opening.counts.already_right,
        corrected: result.opening.counts.corrected,
        missing: result.opening.counts.missing,
        failed: result.opening.counts.failed,
      },
      'a freshly imported row already matches, so nothing is corrected',
    );
  });
});

test('OpeningPush_WhenImportedAsAStartingBalance_TheEngineMarksTheRowAsOne', async () => {
  // Recorded rather than relied on: the flag is what lets Actual show the row
  // as the account's starting balance, so it must survive the import.
  await withBudget(async (ctx) => {
    await push(api, envelopeFor(ctx));

    const row = await findOpening(ctx.main);

    assert.equal(row.starting_balance ?? row.starting_balance_flag, true);
  });
});

test('OpeningPush_WhenRepeated_NothingChangesAndNoUpdateOrDeleteIsMade', async () => {
  await withBudget(async (ctx) => {
    const envelope = envelopeFor(ctx);
    await push(api, envelope);
    const before = await readRows(ctx.main);
    const { calls, client } = spied();

    const again = await push(client, envelope);

    assert.equal(again.applied.added, 0);
    assert.equal(again.opening.counts.already_right, 1);
    assert.equal(again.opening.counts.corrected, 0);
    assert.deepEqual(calls.updateTransaction, [], 'an exact row costs no update call');
    assert.deepEqual(calls.deleteTransaction, []);
    assert.deepEqual(await readRows(ctx.main), before);
    assert.equal(await api.getAccountBalance(ctx.main), 290000);
  });
});

test('OpeningPush_WhenTheAmountChanges_ReimportKeepsTheOldFigureAndTheCorrectionFixesIt', async () => {
  await withBudget(async (ctx) => {
    await push(api, envelopeFor(ctx));
    const changed = envelopeFor(ctx, openingRow(120000));

    // The importing half alone: existing values win, which is why the
    // correction exists at all.
    await applyAccounts(api, changed.accounts);
    assert.equal((await findOpening(ctx.main)).amount, 100000);
    assert.equal(await api.getAccountBalance(ctx.main), 290000);

    const corrected = await applyOpeningBalances(api, changed.openings);

    assert.equal(corrected.counts.corrected, 1);
    assert.equal(corrected.counts.failed, 0);
    assert.equal((await findOpening(ctx.main)).amount, 120000);
    assert.equal(await api.getAccountBalance(ctx.main), 310000);
    assert.equal((await findOpening(ctx.main)).date, '2026-08-31', 'the date was already right');
  });
});

test('OpeningPush_WhenTheDateChanges_TheNextPushCorrectsTheDateAndKeepsTheAmount', async () => {
  await withBudget(async (ctx) => {
    await push(api, envelopeFor(ctx));

    const result = await push(api, envelopeFor(ctx, openingRow(100000, '2026-08-15')));

    assert.equal(result.opening.counts.corrected, 1);
    const row = await findOpening(ctx.main);
    assert.equal(row.date, '2026-08-15');
    assert.equal(row.amount, 100000);
    assert.equal(await api.getAccountBalance(ctx.main), 290000);
  });
});

test('OpeningPush_WhenTheRowWasEditedInActual_TheNextPushPutsItBack', async () => {
  await withBudget(async (ctx) => {
    await push(api, envelopeFor(ctx));
    const row = await findOpening(ctx.main);
    await api.updateTransaction(row.id, { amount: 99999 });
    await openingSettledAs(ctx.main, (r) => r?.amount === 99999);
    assert.equal(await api.getAccountBalance(ctx.main), 289999);

    const result = await push(api, envelopeFor(ctx));

    assert.equal(result.opening.counts.corrected, 1);
    assert.equal(await api.getAccountBalance(ctx.main), 290000);
  });
});

test('OpeningPush_WhenTheRowWasDeletedInActual_NothingIsCreatedAndItIsReportedMissing', async () => {
  // The applier's contract is that it never creates a row itself, so a row
  // deleted in Actual is reported and the import decides whether it returns.
  await withBudget(async (ctx) => {
    await push(api, envelopeFor(ctx));
    await api.deleteTransaction((await findOpening(ctx.main)).id);
    await openingSettledAs(ctx.main, (r) => r === undefined);
    assert.equal(await api.getAccountBalance(ctx.main), 190000);

    const result = await applyOpeningBalances(api, envelopeFor(ctx).openings);

    assert.equal(result.counts.missing, 1);
    assert.equal(result.counts.corrected, 0);
    assert.equal(await findOpening(ctx.main), undefined);
    assert.ok(result.lines.some((line) => line.includes('nothing was created')));
  });
});

test('OpeningPush_WhenAPrunedRowIsStatedAgain_TheImportCreatesItAnew', async () => {
  // Recorded because a prune of an opening row would otherwise be a one-way
  // door: the account stops having an opening balance, the row is pruned, and
  // the person later states one again. Measured on the pinned library, 26.7.0.
  await withBudget(async (ctx) => {
    await push(api, envelopeFor(ctx));
    await quietly(() => pruneAccounts(api, envelopeFor(ctx, null).accounts));
    await openingSettledAs(ctx.main, (r) => r === undefined);
    assert.equal(await api.getAccountBalance(ctx.main), 190000);

    const again = await push(api, envelopeFor(ctx));

    assert.equal(again.applied.added, 1, 'only the opening row returns');
    assert.equal((await findOpening(ctx.main)).amount, 100000);
    assert.equal(await api.getAccountBalance(ctx.main), 290000);
  });
});

test('OpeningPush_WhenTheAccountLosesItsOpening_APushLeavesTheRowAndAPruneRemovesIt', async () => {
  await withBudget(async (ctx) => {
    await push(api, envelopeFor(ctx));
    await api.addTransactions(ctx.main, [
      { date: '2026-09-05', amount: -2500, payee_name: 'Cash', cleared: true },
    ]);
    const without = envelopeFor(ctx, null);

    const pushed = await push(api, without);
    assert.ok(await findOpening(ctx.main), 'a push never removes a row');
    assert.equal(pushed.opening.counts.entries, 0);
    assert.equal(await api.getAccountBalance(ctx.main), 287500);

    const report = await quietly(() => pruneAccounts(api, without.accounts));

    assert.equal(report[0].removed, 1);
    await openingSettledAs(ctx.main, (r) => r === undefined);
    const rows = await readRows(ctx.main);
    assert.equal(rows.length, 3, 'income, spend, and the person\'s own cash row remain');
    assert.equal(rows.filter((r) => r.imported_id == null).length, 1);
    assert.equal(await api.getAccountBalance(ctx.main), 187500);
  });
});

test('OpeningPrune_WhenTheOpeningIsStillExpected_ItIsKept', async () => {
  await withBudget(async (ctx) => {
    const envelope = envelopeFor(ctx);
    await push(api, envelope);

    const report = await quietly(() => pruneAccounts(api, envelope.accounts));

    assert.equal(report[0].removed, 0);
    assert.ok(await findOpening(ctx.main));
    assert.equal(await api.getAccountBalance(ctx.main), 290000);
  });
});

test('OpeningAudit_WhenTheRowIsRight_TheBalanceAgreesAndNothingDiverges', async () => {
  await withBudget(async (ctx) => {
    const envelope = envelopeFor(ctx);
    await push(api, envelope);

    const [report] = await quietly(() => auditAccounts(api, envelope.accounts));

    assert.equal(report.expected, 3);
    assert.equal(report.present, 3);
    assert.equal(report.diverged, 0);
    assert.equal(report.missing, 0);
    assert.equal(report.orphaned, 0);
    assert.deepEqual(report.balance, { expected: 290000, actual: 290000, agrees: true });
  });
});

test('OpeningAudit_WhenTheRowHasBeenAltered_TheBalanceDiffersAndTheRowDiverges', async () => {
  await withBudget(async (ctx) => {
    const envelope = envelopeFor(ctx);
    await push(api, envelope);
    await api.updateTransaction((await findOpening(ctx.main)).id, { amount: 99999 });

    const [report] = await quietly(() => auditAccounts(api, envelope.accounts));

    assert.equal(report.diverged, 1);
    assert.equal(report.diverged_sample[0].imported_id, OPENING_ID);
    assert.deepEqual(report.diverged_sample[0].store, { date: '2026-08-31', amount: 100000 });
    assert.deepEqual(report.balance, { expected: 290000, actual: 289999, agrees: false });
  });
});

test('OpeningAudit_WhenNoOpeningIsExpectedButTheRowIsThere_ItIsOrphanedAsOurs', async () => {
  await withBudget(async (ctx) => {
    await push(api, envelopeFor(ctx));

    const [report] = await quietly(() => auditAccounts(api, envelopeFor(ctx, null).accounts));

    assert.equal(report.orphaned, 1);
    assert.equal(report.orphaned_sample[0].imported_id, OPENING_ID);
    assert.equal(report.balance.agrees, false);
  });
});

test('OpeningPrune_WhenAPersonsOwnStartingBalanceHasNoImportedId_ItIsNeverTouched', async () => {
  await withBudget(async (ctx) => {
    await api.addTransactions(ctx.main, [
      { date: '2026-08-01', amount: 55555, payee_name: 'Starting Balance', cleared: true },
    ]);
    const without = envelopeFor(ctx, null);
    await applyAccounts(api, without.accounts);

    const rows = await readRows(ctx.main);
    const { prunable } = await choosePrunable(
      createLinkContext(api),
      ctx.main,
      new Set(without.accounts[ctx.main].map((r) => r.imported_id)),
      rows,
    );

    assert.deepEqual(prunable, []);
    const report = await quietly(() => pruneAccounts(api, without.accounts));
    assert.equal(report[0].removed, 0);
    assert.equal(await api.getAccountBalance(ctx.main), 245555);
  });
});
