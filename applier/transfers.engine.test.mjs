/**
 * Transfer linking against the REAL budget engine.
 *
 * Every other applier test drives a stand-in client, which can only confirm
 * that the code calls what the author believes the library does. These tests
 * run the pinned library offline - init with no server URL, then runImport
 * to create a fresh local budget - so a claim such as "two ordinary rows
 * linked both ways read as one transfer and the balances come right" is
 * checked against Actual's own behaviour. Each test gets a budget in its own
 * temporary directory, removed afterwards; nothing here touches a network.
 *
 * The household is invented: main takes 2000.00 of income, sends 1500.00 to
 * the pot, and spends 100.00 (ends on 400.00); the pot receives the 1500.00
 * and spends 1200.00 (ends on 300.00). Imported as flat rows with no linking
 * both accounts read wrong by exactly the transfer.
 */

import assert from 'node:assert/strict';
import { mkdtemp, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { test } from 'node:test';

import * as api from '@actual-app/api';

import { auditAccounts } from './audit.mjs';
import { applyAccounts, linkTransfers } from './lib.mjs';
import { auditTransfers } from './transfers.mjs';

// The library narrates every operation on stdout; the tests' own verdicts
// are the report, so the narration is dropped for the length of a test.
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
  const dataDir = await mkdtemp(join(tmpdir(), 'obdi-engine-'));
  try {
    return await quietly(async () => {
      await api.init({ dataDir });
      try {
        budgets += 1;
        await api.runImport(`engine-test-${budgets}`, async () => {});
        const main = await api.createAccount({ name: 'MAIN' }, 0);
        const pot = await api.createAccount({ name: 'POT' }, 0);
        return await work({ main, pot });
      } finally {
        await api.shutdown();
      }
    });
  } finally {
    await rm(dataDir, { recursive: true, force: true });
  }
}

const row = (importedId, date, amount, payee) => ({
  imported_id: importedId,
  date,
  amount,
  payee_name: payee,
  cleared: true,
});

const INCOME = row('income:0', '2026-09-01', 200000, 'Employer');
const TO_POT = row('to-pot:0', '2026-09-02', -150000, 'Savings pot');
const SPEND = row('spend:0', '2026-09-03', -10000, 'Shop');
const FROM_MAIN = row('from-main:0', '2026-09-02', 150000, 'Current account');
const POT_SPEND = row('pot-spend:0', '2026-09-04', -120000, 'Holiday');

const leg = (account, r) => ({
  account,
  imported_id: r.imported_id,
  date: r.date,
  amount: r.amount,
});

const pairOf = (debit, credit) => ({ debit, credit });

function household({ main, pot }) {
  return {
    accounts: {
      [main]: [INCOME, TO_POT, SPEND],
      [pot]: [FROM_MAIN, POT_SPEND],
    },
    transfers: [pairOf(leg(main, TO_POT), leg(pot, FROM_MAIN))],
  };
}

async function push(client, envelope) {
  const applied = await applyAccounts(client, envelope.accounts);
  const linked = await linkTransfers(client, envelope.transfers);
  return { applied, linked };
}

const readRows = (accountId) =>
  api.getTransactions(accountId, '1900-01-01', '2999-12-31');

const findRow = async (accountId, importedId) =>
  (await readRows(accountId)).find((r) => r.imported_id === importedId);

// What the budget holds, with Actual's own row ids replaced by the imported
// id of whatever each row points at, so two budgets can be compared.
async function shape(ctx) {
  const out = {};
  const byId = new Map();
  for (const [name, id] of [['main', ctx.main], ['pot', ctx.pot]]) {
    for (const r of await readRows(id)) byId.set(r.id, r.imported_id);
  }
  for (const [name, id] of [['main', ctx.main], ['pot', ctx.pot]]) {
    const rows = (await readRows(id))
      .map((r) => ({
        imported_id: r.imported_id,
        amount: r.amount,
        date: r.date,
        links_to: r.transfer_id ? byId.get(r.transfer_id) ?? 'UNKNOWN ROW' : null,
      }))
      .sort((a, b) => a.imported_id.localeCompare(b.imported_id));
    out[name] = { rows, balance: await api.getAccountBalance(id) };
  }
  return out;
}

const EXPECTED_HOUSEHOLD = {
  main: {
    balance: 40000,
    rows: [
      { imported_id: 'income:0', amount: 200000, date: '2026-09-01', links_to: null },
      { imported_id: 'spend:0', amount: -10000, date: '2026-09-03', links_to: null },
      { imported_id: 'to-pot:0', amount: -150000, date: '2026-09-02', links_to: 'from-main:0' },
    ],
  },
  pot: {
    balance: 30000,
    rows: [
      { imported_id: 'from-main:0', amount: 150000, date: '2026-09-02', links_to: 'to-pot:0' },
      { imported_id: 'pot-spend:0', amount: -120000, date: '2026-09-04', links_to: null },
    ],
  },
};

// A client that counts the calls that would change a transfer.
function spied() {
  const updates = [];
  return {
    updates,
    client: new Proxy(api, {
      get(target, property) {
        if (property === 'updateTransaction') {
          return async (...args) => {
            updates.push(args);
            return target.updateTransaction(...args);
          };
        }
        return target[property];
      },
    }),
  };
}

const transferPayees = async () => {
  const payees = await api.getPayees();
  return (accountId) => payees.find((p) => p.transfer_acct === accountId).id;
};

test('TransferPush_WhenBothLegsImportedPlainlyThenLinked_EachAccountHoldsOneRowAndBalancesAreRight', async () => {
  await withBudget(async (ctx) => {
    const result = await push(api, household(ctx));

    assert.deepEqual(await shape(ctx), EXPECTED_HOUSEHOLD);
    assert.equal(result.linked.counts.linked, 1);
    assert.equal(result.linked.counts.failed, 0);
    // Both ids survive the link, and the cleared flag is not lost.
    const a = await findRow(ctx.main, 'to-pot:0');
    const b = await findRow(ctx.pot, 'from-main:0');
    assert.equal(a.cleared, true);
    assert.equal(b.cleared, true);
  });
});

test('TransferPush_WhenImportedWithoutLinking_BalancesAgreeButTheRowsAreNotYetTransfers', async () => {
  // The balances come right from sending both legs at all; the link is what
  // keeps the pair out of income and spending reports.
  await withBudget(async (ctx) => {
    await applyAccounts(api, household(ctx).accounts);

    assert.equal(await api.getAccountBalance(ctx.main), 40000);
    assert.equal(await api.getAccountBalance(ctx.pot), 30000);
    const a = await findRow(ctx.main, 'to-pot:0');
    assert.equal(a.transfer_id ?? null, null);
  });
});

test('TransferPush_WhenRepeatedTwiceMore_NothingChangesAndNoUpdateIsMade', async () => {
  await withBudget(async (ctx) => {
    const envelope = household(ctx);
    await push(api, envelope);
    const { updates, client } = spied();

    const second = await push(client, envelope);
    const third = await push(client, envelope);

    assert.deepEqual(await shape(ctx), EXPECTED_HOUSEHOLD);
    for (const again of [second, third]) {
      assert.equal(again.applied.added, 0);
      assert.equal(again.linked.counts.linked, 0);
      assert.equal(again.linked.counts.already_linked, 1);
      assert.equal(again.linked.counts.failed, 0);
    }
    assert.deepEqual(updates, [], 'an established link must cost no update call');
  });
});

test('TransferLink_WhenUpdatesAreForcedOntoAnAlreadyLinkedPair_RowsAndBalancesStayPut', async () => {
  // The short-circuit is a choice, not a necessity: this records what the
  // engine does if the two calls are made again regardless.
  await withBudget(async (ctx) => {
    await push(api, household(ctx));
    const a = await findRow(ctx.main, 'to-pot:0');
    const b = await findRow(ctx.pot, 'from-main:0');
    const toPot = (await transferPayees())(ctx.pot);
    const toMain = (await transferPayees())(ctx.main);

    await api.updateTransaction(a.id, { payee: toPot, transfer_id: b.id });
    await api.updateTransaction(b.id, { payee: toMain, transfer_id: a.id });

    assert.deepEqual(await shape(ctx), EXPECTED_HOUSEHOLD);
  });
});

test('TransferPush_WhenOrdinaryRowsReimportedAfterLinking_LinksSurvive', async () => {
  await withBudget(async (ctx) => {
    await push(api, household(ctx));

    // Only the importing half of a push: the rows go in again as ordinary
    // rows with their ordinary payees, exactly as a scheduled pull would.
    const again = await applyAccounts(api, household(ctx).accounts);

    assert.equal(again.added, 0);
    assert.deepEqual(await shape(ctx), EXPECTED_HOUSEHOLD);
  });
});

test('TransferLink_WhenOnlyTheFirstUpdateWasMade_NextLinkCompletesThePair', async () => {
  await withBudget(async (ctx) => {
    const envelope = household(ctx);
    await applyAccounts(api, envelope.accounts);
    const a = await findRow(ctx.main, 'to-pot:0');
    const b = await findRow(ctx.pot, 'from-main:0');
    const toPot = (await transferPayees())(ctx.pot);
    await api.updateTransaction(a.id, { payee: toPot, transfer_id: b.id });
    const oneSided = await shape(ctx);
    assert.equal(oneSided.main.rows.find((r) => r.imported_id === 'to-pot:0').links_to, 'from-main:0');
    assert.equal(oneSided.pot.rows.find((r) => r.imported_id === 'from-main:0').links_to, null);

    const result = await linkTransfers(api, envelope.transfers);

    assert.equal(result.counts.linked, 1);
    assert.equal(result.counts.failed, 0);
    assert.deepEqual(await shape(ctx), EXPECTED_HOUSEHOLD);
  });
});

test('TransferLink_WhenALegIsMissingFromActual_NothingIsCreatedAndThePairIsReported', async () => {
  await withBudget(async (ctx) => {
    const envelope = household(ctx);
    envelope.accounts[ctx.pot] = [POT_SPEND];
    await applyAccounts(api, envelope.accounts);

    const result = await linkTransfers(api, envelope.transfers);

    assert.equal(result.counts.linked, 0);
    assert.equal(result.counts.skipped.leg_missing, 1);
    assert.ok(result.lines.some((line) => line.includes('leg missing')));
    const shaped = await shape(ctx);
    assert.equal(shaped.pot.rows.length, 1, 'no counterpart may be created');
    assert.equal(shaped.main.rows.length, 3);
    assert.equal(shaped.main.rows.find((r) => r.imported_id === 'to-pot:0').links_to, null);
  });
});

test('TransferLink_WhenAmountsAreNotOpposites_PairIsRefusedAndRowsAreUntouched', async () => {
  await withBudget(async (ctx) => {
    const envelope = household(ctx);
    envelope.accounts[ctx.pot] = [{ ...FROM_MAIN, amount: 140000 }, POT_SPEND];
    await applyAccounts(api, envelope.accounts);
    const before = await shape(ctx);
    const { updates, client } = spied();

    const result = await linkTransfers(client, envelope.transfers);

    assert.equal(result.counts.linked, 0);
    assert.equal(result.counts.skipped.amounts_not_opposite, 1);
    assert.deepEqual(updates, []);
    assert.deepEqual(await shape(ctx), before);
  });
});

test('TransferLink_WhenALegIsAlreadyLinkedToADifferentRow_ItIsNotOverwrittenAndIsReported', async () => {
  await withBudget(async (ctx) => {
    const envelope = household(ctx);
    const stranger = row('stranger:0', '2026-09-02', 150000, 'Somebody');
    envelope.accounts[ctx.pot] = [FROM_MAIN, POT_SPEND, stranger];
    await applyAccounts(api, envelope.accounts);
    const a = await findRow(ctx.main, 'to-pot:0');
    const c = await findRow(ctx.pot, 'stranger:0');
    const payee = await transferPayees();
    await api.updateTransaction(a.id, { payee: payee(ctx.pot), transfer_id: c.id });
    await api.updateTransaction(c.id, { payee: payee(ctx.main), transfer_id: a.id });
    const before = await shape(ctx);

    const result = await linkTransfers(api, envelope.transfers);

    assert.equal(result.counts.linked, 0);
    assert.equal(result.counts.skipped.linked_elsewhere, 1);
    assert.deepEqual(await shape(ctx), before);
    assert.equal(
      before.main.rows.find((r) => r.imported_id === 'to-pot:0').links_to,
      'stranger:0',
    );
  });
});

test('TransferLink_WhenTwoIdenticalTransfersShareADay_EachLinksToItsOwnPartner', async () => {
  await withBudget(async (ctx) => {
    const a1 = row('twin-out-1:0', '2026-09-02', -50000, 'Savings pot');
    const a2 = row('twin-out-2:0', '2026-09-02', -50000, 'Savings pot');
    const b1 = row('twin-in-1:0', '2026-09-02', 50000, 'Current account');
    const b2 = row('twin-in-2:0', '2026-09-02', 50000, 'Current account');
    // The credit side arrives in the opposite order, so any pairing by
    // position or by amount-and-date would cross them.
    const accounts = { [ctx.main]: [a1, a2], [ctx.pot]: [b2, b1] };
    const transfers = [
      pairOf(leg(ctx.main, a1), leg(ctx.pot, b1)),
      pairOf(leg(ctx.main, a2), leg(ctx.pot, b2)),
    ];

    const result = await push(api, { accounts, transfers });

    assert.equal(result.linked.counts.linked, 2);
    const shaped = await shape(ctx);
    const links = Object.fromEntries(
      [...shaped.main.rows, ...shaped.pot.rows].map((r) => [r.imported_id, r.links_to]),
    );
    assert.deepEqual(links, {
      'twin-out-1:0': 'twin-in-1:0',
      'twin-out-2:0': 'twin-in-2:0',
      'twin-in-1:0': 'twin-out-1:0',
      'twin-in-2:0': 'twin-out-2:0',
    });
    assert.equal(shaped.main.rows.length, 2);
    assert.equal(shaped.pot.rows.length, 2);
    assert.equal(shaped.main.balance, -100000);
    assert.equal(shaped.pot.balance, 100000);
  });
});

test('TransferPush_WhenOrdinaryRowsAlreadyExistFromAnEarlierPush_EndStateMatchesAFirstPush', async () => {
  // The state of the live budget: non-transfer rows are already there and
  // the transfer rows arrive new.
  await withBudget(async (ctx) => {
    const envelope = household(ctx);
    await applyAccounts(api, {
      [ctx.main]: [INCOME, SPEND],
      [ctx.pot]: [POT_SPEND],
    });

    const result = await push(api, envelope);

    assert.equal(result.applied.added, 2);
    assert.equal(result.linked.counts.linked, 1);
    assert.deepEqual(await shape(ctx), EXPECTED_HOUSEHOLD);
  });
});

test('Audit_WhenHouseholdIsPushedAndLinked_BalancesAgreeAndThePairIsLinked', async () => {
  await withBudget(async (ctx) => {
    const envelope = household(ctx);
    await push(api, envelope);

    const report = await auditAccounts(api, envelope.accounts);
    const pairs = await auditTransfers(api, envelope.transfers);

    const byAccount = Object.fromEntries(report.map((entry) => [entry.account_id, entry]));
    assert.deepEqual(byAccount[ctx.main].balance, { expected: 40000, actual: 40000, agrees: true });
    assert.deepEqual(byAccount[ctx.pot].balance, { expected: 30000, actual: 30000, agrees: true });
    assert.deepEqual(pairs, {
      pairs: 1,
      linked: 1,
      unlinked: 0,
      leg_missing: 0,
      by_account: {
        [ctx.main]: { pairs: 1, linked: 1 },
        [ctx.pot]: { pairs: 1, linked: 1 },
      },
    });
  });
});

test('Audit_WhenPushedButNotYetLinked_PairIsReportedUnlinked', async () => {
  await withBudget(async (ctx) => {
    const envelope = household(ctx);
    await applyAccounts(api, envelope.accounts);

    const pairs = await auditTransfers(api, envelope.transfers);

    assert.deepEqual(pairs, {
      pairs: 1,
      linked: 0,
      unlinked: 1,
      leg_missing: 0,
      by_account: {
        [ctx.main]: { pairs: 1, linked: 0 },
        [ctx.pot]: { pairs: 1, linked: 0 },
      },
    });
  });
});

test('Audit_WhenOneLegWasDeletedFromActual_BalanceDiffersAndThePairHasALegMissing', async () => {
  await withBudget(async (ctx) => {
    const envelope = household(ctx);
    await applyAccounts(api, envelope.accounts);
    const b = await findRow(ctx.pot, 'from-main:0');
    await api.deleteTransaction(b.id);

    const report = await auditAccounts(api, envelope.accounts);
    const pairs = await auditTransfers(api, envelope.transfers);

    const byAccount = Object.fromEntries(report.map((entry) => [entry.account_id, entry]));
    assert.deepEqual(byAccount[ctx.pot].balance, { expected: 30000, actual: -120000, agrees: false });
    assert.equal(byAccount[ctx.main].balance.agrees, true);
    assert.deepEqual(
      { ...pairs, by_account: undefined },
      { pairs: 1, linked: 0, unlinked: 0, leg_missing: 1, by_account: undefined },
    );
  });
});

test('Audit_WhenOneLegOfALinkedPairIsDeleted_EngineRemovesThePartnerTooAndTheAuditSaysLegMissing', async () => {
  // Deleting one leg of a LINKED transfer is a different act from deleting
  // an ordinary row: Actual removes the partner with it. It does so AFTER
  // deleteTransaction has resolved - read straight back, the partner is
  // still there - so the wait below is the engine's behaviour, not slack.
  await withBudget(async (ctx) => {
    const envelope = household(ctx);
    await push(api, envelope);
    const b = await findRow(ctx.pot, 'from-main:0');
    await api.deleteTransaction(b.id);
    const deadline = Date.now() + 10000;
    while (Date.now() < deadline && (await findRow(ctx.main, 'to-pot:0'))) {
      await new Promise((resolve) => setTimeout(resolve, 50));
    }

    const shaped = await shape(ctx);
    const pairs = await auditTransfers(api, envelope.transfers);

    assert.equal(shaped.pot.rows.some((r) => r.imported_id === 'from-main:0'), false);
    assert.equal(shaped.main.rows.some((r) => r.imported_id === 'to-pot:0'), false);
    assert.deepEqual(
      { ...pairs, by_account: undefined },
      { pairs: 1, linked: 0, unlinked: 0, leg_missing: 1, by_account: undefined },
    );
  });
});
