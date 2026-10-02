/**
 * Emptying a whole budget, against the REAL budget engine.
 *
 * The same offline setup as clear.engine.test.mjs: the pinned library is
 * initialised with no server and each test gets its own temporary budget.
 * Nothing here touches a network.
 *
 * The household is invented. Row counts are non-child rows (a split's children
 * belong to the parent), and were decided before the first run:
 *   MAIN   on budget:   P1, P2 (obdi imports), LEG_MAIN (linked to SAVE),
 *                       HAND_MAIN, one split parent with two children,
 *                       and the mirror of OLD's closing row            -> 6
 *   SAVE   off budget:  S1 (obdi import, then reconciled), LEG_SAVE
 *                       (linked to MAIN), HAND_SAVE                    -> 3
 *   OLD    CLOSED:      O1 (obdi import) and its own closing row,
 *                       linked to MAIN's mirror                        -> 2
 *   STRAY  unbound:     two rows entered by hand                       -> 2
 * 4 accounts, 13 rows. Beside them: one category group with a category, one
 * rule, one schedule, which an empty must leave alone. (An earlier draft of
 * this table counted the reconciled row as a fourth SAVE row; it is S1.)
 */

import assert from 'node:assert/strict';
import { mkdtemp, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { test } from 'node:test';

import * as api from '@actual-app/api';

import { auditAccounts } from './audit.mjs';
import { emptyBudget } from './empty.mjs';
import { applyAccounts, linkTransfers, provisionAccounts } from './lib.mjs';

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

async function withEngine(work) {
  const dataDir = await mkdtemp(join(tmpdir(), 'obdi-empty-'));
  try {
    return await quietly(async () => {
      await api.init({ dataDir });
      try {
        budgets += 1;
        await api.runImport(`empty-test-${budgets}`, async () => {});
        return await work();
      } finally {
        await api.shutdown();
      }
    });
  } finally {
    await rm(dataDir, { recursive: true, force: true });
  }
}

const pause = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
const readRows = (accountId) => api.getTransactions(accountId, '1900-01-01', '2999-12-31');
const topLevel = (rows) => rows.filter((r) => !r.is_child);

const hexId = (digit, n = 0) => `${digit.repeat(64)}:${n}`;
const imported = (importedId, date, amount, payee) => ({
  imported_id: importedId,
  date,
  amount,
  payee_name: payee,
  cleared: true,
});
const hand = (date, amount, payee) => ({ date, amount, payee_name: payee, cleared: true });

const P1 = imported(hexId('1'), '2026-09-01', -1100, 'Shop');
const P2 = imported(hexId('2'), '2026-09-02', -2200, 'Cafe');
const LEG_MAIN = imported(hexId('c'), '2026-09-07', -150000, 'Savings pot');
const S1 = imported(hexId('3'), '2026-09-03', 3300, 'Interest');
const LEG_SAVE = imported(hexId('f'), '2026-09-07', 150000, 'Current account');
const O1 = imported(hexId('4'), '2026-08-01', -4400, 'Old shop');

const leg = (account, r) => ({
  account,
  imported_id: r.imported_id,
  date: r.date,
  amount: r.amount,
});

/** The household described at the top of the file. */
async function household() {
  const main = await api.createAccount({ name: 'MAIN' }, 0);
  const save = await api.createAccount({ name: 'SAVE', offbudget: true }, 0);
  const old = await api.createAccount({ name: 'OLD' }, 0);
  const stray = await api.createAccount({ name: 'STRAY' }, 0);
  await applyAccounts(api, { [main]: [P1, P2, LEG_MAIN], [save]: [S1, LEG_SAVE], [old]: [O1] });
  const group = await api.createCategoryGroup({ name: 'Household' });
  const category = await api.createCategory({ name: 'Groceries', group_id: group });
  await api.addTransactions(main, [
    hand('2026-09-08', -4242, 'Cash'),
    {
      date: '2026-09-09',
      amount: -900,
      payee_name: 'Split shop',
      subtransactions: [
        { amount: -400, category },
        { amount: -500, category },
      ],
    },
  ]);
  await api.addTransactions(save, [hand('2026-09-10', 777, 'Gift')]);
  await api.addTransactions(stray, [
    hand('2026-09-11', -10, 'Odd job'),
    hand('2026-09-12', 25, 'Refund'),
  ]);
  await linkTransfers(api, [{ debit: leg(main, LEG_MAIN), credit: leg(save, LEG_SAVE) }]);
  const reconciled = (await readRows(save)).find((r) => r.imported_id === S1.imported_id);
  await api.updateTransaction(reconciled.id, { cleared: true, reconciled: true });
  // Closing OLD with a balance moves the balance through a transfer, which
  // adds one row to OLD and a mirror row to MAIN.
  await api.closeAccount(old, main);
  await api.createRule({
    stage: null,
    conditionsOp: 'and',
    conditions: [{ field: 'imported_payee', op: 'is', value: 'Shop' }],
    actions: [{ op: 'set', field: 'category', value: category }],
  });
  await api.createSchedule({
    name: 'Rent',
    account: main,
    amount: -50000,
    amountOp: 'is',
    date: { frequency: 'monthly', start: '2026-10-01' },
    posts_transaction: false,
  });
  await pause(500);
  return { main, save, old, stray, category, group };
}

const COUNTS = { main: 6, save: 3, old: 2, stray: 2 };
const TOLD = (ids) => ({
  [ids.main]: COUNTS.main,
  [ids.save]: COUNTS.save,
  [ids.old]: COUNTS.old,
  [ids.stray]: COUNTS.stray,
});

async function snapshot() {
  const accounts = await api.getAccounts();
  const rowsBy = {};
  for (const account of accounts) {
    rowsBy[account.name] = topLevel(await readRows(account.id)).length;
  }
  return {
    accounts: accounts.map((a) => [a.name, a.closed]).sort(),
    rowsBy,
    categories: (await api.getCategories()).map((c) => c.name).sort(),
    groups: (await api.getCategoryGroups()).map((g) => g.name).sort(),
    rules: (await api.getRules()).length,
    schedules: (await api.getSchedules()).length,
  };
}

const run = (told, options) => quietly(() => emptyBudget(api, told, options));

test('Household_BeforeAnyEmpty_HoldsTheRowsTheTestsWereWrittenAgainst', async () => {
  await withEngine(async () => {
    const ids = await household();
    const before = await snapshot();
    assert.deepEqual(before.rowsBy, { MAIN: 6, SAVE: 3, OLD: 2, STRAY: 2 });
    assert.deepEqual(before.accounts, [
      ['MAIN', false],
      ['OLD', true],
      ['SAVE', false],
      ['STRAY', false],
    ]);
    // The engine learns a rule of its own beside the explicit one, so the
    // rule count is only required to be non-zero; the empty tests compare it
    // before and after.
    assert.ok(before.rules >= 1);
    assert.equal(before.schedules, 1);
    assert.ok(ids.category);
  });
});

// What the library itself does, recorded because the choice made in empty.mjs
// rests on it and reading the library has been wrong here before.

test('DeleteAccount_OnAnAccountWithRows_DeletesItsRowsAndTheAccountAtOnce', async () => {
  await withEngine(async () => {
    const ids = await household();

    await api.deleteAccount(ids.stray);

    // No settling wait: the account and its rows are gone when the call resolves.
    assert.equal((await readRows(ids.stray)).length, 0);
    assert.ok(!(await api.getAccounts()).some((a) => a.id === ids.stray));
  });
});

test('DeleteAccount_OnAnAccountWithALinkedTransfer_KeepsTheOtherLegUnlinkedAndWithNoPayee', async () => {
  await withEngine(async () => {
    const ids = await household();

    await api.deleteAccount(ids.main);

    const partner = (await readRows(ids.save)).find((r) => r.imported_id === LEG_SAVE.imported_id);
    assert.ok(partner, 'the other leg survives in the other account');
    assert.equal(partner.transfer_id ?? null, null);
    assert.equal(partner.payee ?? null, null);
    assert.equal(topLevel(await readRows(ids.save)).length, COUNTS.save);
  });
});

test('DeleteAccount_OnAnAccountWithASplitAndAReconciledRow_DeletesThemToo', async () => {
  await withEngine(async () => {
    const ids = await household();

    await api.deleteAccount(ids.save);
    await api.deleteAccount(ids.main);

    assert.equal((await readRows(ids.save)).length, 0, 'the reconciled row went');
    assert.equal((await readRows(ids.main)).length, 0, 'the split parent and children went');
  });
});

test('DeleteAccount_OnAnAccount_RemovesItsTransferPayeeAndLeavesOtherPayees', async () => {
  await withEngine(async () => {
    const ids = await household();
    const before = await api.getPayees();
    assert.ok(before.some((p) => p.transfer_acct === ids.stray));

    await api.deleteAccount(ids.stray);

    const after = await api.getPayees();
    assert.ok(!after.some((p) => p.transfer_acct === ids.stray));
    assert.ok(after.some((p) => p.transfer_acct === ids.main), 'another account keeps its own');
    assert.ok(after.some((p) => p.name === 'Cash'), 'an ordinary payee stays');
  });
});

test('DeleteAccount_OnAClosedAccount_DoesNothingAndSaysNothing', async () => {
  await withEngine(async () => {
    const ids = await household();

    await api.deleteAccount(ids.old);

    // The library's forced delete returns early for a closed account. The
    // account stays, listed as closed, with every row, and nothing is raised.
    const listed = (await api.getAccounts()).find((a) => a.id === ids.old);
    assert.equal(listed?.closed, true);
    assert.equal(topLevel(await readRows(ids.old)).length, COUNTS.old);
  });
});

test('DeleteAccount_OnAClosedAccountThatWasReopened_DeletesIt', async () => {
  await withEngine(async () => {
    const ids = await household();

    await api.reopenAccount(ids.old);
    await api.deleteAccount(ids.old);

    assert.ok(!(await api.getAccounts()).some((a) => a.id === ids.old));
    assert.equal((await readRows(ids.old)).length, 0);
  });
});

test('DeleteAccount_WhenAScheduleNamesTheAccount_TheScheduleStaysWithNoAccount', async () => {
  await withEngine(async () => {
    const ids = await household();
    const [before] = await api.getSchedules();
    assert.equal(before.account, ids.main);

    await api.deleteAccount(ids.main);

    const [after] = await api.getSchedules();
    assert.equal(after.name, 'Rent');
    assert.equal(after.account ?? null, null);
  });
});

test('DeleteAccount_OnAnAccountOfThousandsOfRows_ResolvesInSecondsNotMinutes', async () => {
  await withEngine(async () => {
    const big = await api.createAccount({ name: 'BIG' }, 0);
    const batch = Array.from({ length: 3000 }, (_, i) => ({
      date: '2026-09-01',
      amount: -(i + 1),
      payee_name: `p${i % 50}`,
      imported_id: `id${i}`,
      cleared: true,
    }));
    await api.addTransactions(big, batch);
    assert.equal((await readRows(big)).length, 3000);

    const started = Date.now();
    await api.deleteAccount(big);
    const took = Date.now() - started;

    assert.equal((await readRows(big)).length, 0);
    // Measured at about 140 ms for 3000 rows. The bound is two orders wider so
    // a slow machine passes, and still far under deleting row by row, which
    // took about twelve minutes for 4,500 rows on the real server.
    assert.ok(took < 15000, `took ${took} ms`);
  });
});

// Emptying.

test('Empty_WhenToldTheTruth_RemovesEveryAccountAndRowAndLeavesCategoriesRulesAndSchedules', async () => {
  await withEngine(async () => {
    const ids = await household();
    const before = await snapshot();
    const progress = [];

    const result = await run(TOLD(ids), { onProgress: (p) => progress.push(p) });

    assert.equal(result.complete, true);
    assert.equal(result.refused, undefined);
    assert.equal(result.stopped, undefined);
    assert.deepEqual(result.remaining, []);
    assert.deepEqual(
      result.removed.map((r) => [r.name, r.rows]).sort(),
      [['MAIN', 6], ['OLD', 2], ['SAVE', 3], ['STRAY', 2]],
    );
    assert.equal(result.rows_removed, 13);
    assert.equal(result.accounts_removed, 4);
    assert.deepEqual(await api.getAccounts(), [], 'no account, closed ones included');
    for (const id of [ids.main, ids.save, ids.old, ids.stray]) {
      assert.equal((await readRows(id)).length, 0, `no row remains readable in ${id}`);
    }
    const after = await snapshot();
    assert.deepEqual(after.categories, before.categories);
    assert.deepEqual(after.groups, before.groups);
    assert.equal(after.rules, before.rules);
    assert.equal(after.schedules, before.schedules);
    assert.deepEqual(
      progress.map((p) => [p.done, p.total]),
      [[1, 4], [2, 4], [3, 4], [4, 4]],
    );
    assert.match(result.left_alone.join(' '), /categories/);
    assert.match(result.left_alone.join(' '), /rules/);
    assert.match(result.left_alone.join(' '), /schedules/);
  });
});

test('Empty_WhenToldMoreThanTheBudgetHolds_StillEmptiesIt', async () => {
  await withEngine(async () => {
    const ids = await household();
    const told = { ...TOLD(ids), [ids.main]: 60, 'an-account-deleted-since': 5 };

    const result = await run(told);

    assert.equal(result.complete, true);
    assert.deepEqual(await api.getAccounts(), []);
  });
});

test('Empty_WhenTheBudgetHoldsOneMoreAccountThanWasTold_RefusesAndChangesNothing', async () => {
  await withEngine(async () => {
    const ids = await household();
    const before = await snapshot();
    const told = TOLD(ids);
    delete told[ids.stray];

    const result = await run(told);

    assert.equal(result.complete, false);
    assert.match(result.refused, /not told about/);
    assert.match(result.refused, /STRAY/);
    assert.deepEqual(result.removed, []);
    await pause(300);
    assert.deepEqual(await snapshot(), before);
  });
});

test('Empty_WhenAnAccountHoldsOneMoreRowThanWasTold_RefusesAndChangesNothing', async () => {
  await withEngine(async () => {
    const ids = await household();
    const before = await snapshot();
    const told = { ...TOLD(ids), [ids.save]: COUNTS.save - 1 };

    const result = await run(told);

    assert.equal(result.complete, false);
    assert.match(result.refused, /SAVE holds 3, more than the 2 told/);
    assert.deepEqual(result.removed, []);
    await pause(300);
    assert.deepEqual(await snapshot(), before);
  });
});

test('Empty_WhenAnAccountWasToldAsNothingButHoldsRows_RefusesAndChangesNothing', async () => {
  await withEngine(async () => {
    const ids = await household();
    const before = await snapshot();

    const result = await run({ [ids.main]: 0 });

    assert.equal(result.complete, false);
    assert.ok(result.refused);
    await pause(300);
    assert.deepEqual(await snapshot(), before);
  });
});

test('Empty_WhenToldNothingAtAll_RefusesAndChangesNothing', async () => {
  await withEngine(async () => {
    await household();
    const before = await snapshot();

    const result = await run({});

    assert.equal(result.complete, false);
    assert.ok(result.refused);
    assert.deepEqual(await snapshot(), before);
  });
});

test('Empty_WhenTheBudgetIsAlreadyEmpty_IsCompleteWithNothingRemoved', async () => {
  await withEngine(async () => {
    const result = await run({ 'gone-id': 3 });

    assert.equal(result.complete, true);
    assert.deepEqual(result.removed, []);
    assert.equal(result.rows_removed, 0);
  });
});

test('Empty_WhenADeleteFailsPartWay_StopsSaysWhatWentAndWhatDidNotAndLeavesTheRestIntact', async () => {
  await withEngine(async () => {
    const ids = await household();
    const everyone = await api.getAccounts();
    // The second account deleted is the one that fails.
    let deletes = 0;
    const failing = new Proxy(api, {
      get(target, prop) {
        if (prop === 'deleteAccount') {
          return async (id) => {
            deletes += 1;
            if (deletes === 2) throw new Error('the server went away');
            return target.deleteAccount(id);
          };
        }
        const value = target[prop];
        return typeof value === 'function' ? value.bind(target) : value;
      },
    });
    const order = everyone.map((a) => a.id);

    const result = await quietly(() => emptyBudget(failing, TOLD(ids)));

    assert.equal(result.complete, false);
    assert.equal(result.refused, undefined);
    assert.match(result.stopped, /the server went away/);
    assert.equal(result.removed.length, 1);
    assert.equal(result.removed[0].account_id, order[0]);
    assert.deepEqual(
      result.remaining.map((r) => r.account_id).sort(),
      order.slice(1).sort(),
    );
    assert.equal(deletes, 2, 'nothing was attempted after the failure');
    const left = await api.getAccounts();
    assert.deepEqual(left.map((a) => a.id).sort(), order.slice(1).sort());
    // What was not removed still holds every row it held, apart from the one
    // leg a deleted partner unlinked (the row stays, the link goes).
    for (const account of left) {
      const wanted = Object.entries(COUNTS).find(
        ([key]) => ids[key] === account.id,
      )[1];
      assert.equal(topLevel(await readRows(account.id)).length, wanted);
    }
  });
});

test('Empty_WhenAClosedAccountCannotBeReopened_StopsBeforeDeletingAnythingFurther', async () => {
  await withEngine(async () => {
    const ids = await household();
    const failing = new Proxy(api, {
      get(target, prop) {
        if (prop === 'reopenAccount') {
          return async () => {
            throw new Error('cannot reopen');
          };
        }
        const value = target[prop];
        return typeof value === 'function' ? value.bind(target) : value;
      },
    });

    const result = await quietly(() => emptyBudget(failing, TOLD(ids)));

    assert.equal(result.complete, false);
    assert.match(result.stopped, /OLD/);
    assert.match(result.stopped, /cannot reopen/);
    assert.ok(result.remaining.some((r) => r.account_id === ids.old));
  });
});

test('Empty_ThenAPush_RecreatesTheAccountsAndRowsAndTheNextAuditIsClean', async () => {
  await withEngine(async () => {
    const ids = await household();
    await run(TOLD(ids));
    assert.deepEqual(await api.getAccounts(), []);

    const provisioned = await provisionAccounts(api, [
      { canonical_id: 'main-ref', label: 'MAIN' },
      { canonical_id: 'save-ref', label: 'SAVE' },
    ]);
    const idOf = Object.fromEntries(
      provisioned.bindings.map((b) => [b.canonical_id, b.actual_account_id]),
    );
    const accounts = {
      [idOf['main-ref']]: [P1, P2, LEG_MAIN],
      [idOf['save-ref']]: [S1, LEG_SAVE],
    };
    const applied = await quietly(() => applyAccounts(api, accounts));
    await linkTransfers(api, [
      { debit: leg(idOf['main-ref'], LEG_MAIN), credit: leg(idOf['save-ref'], LEG_SAVE) },
    ]);
    await pause(300);

    assert.equal(applied.added, 5);
    assert.equal((await api.getAccounts()).length, 2);
    const report = await quietly(() => auditAccounts(api, accounts));
    for (const entry of report) {
      assert.equal(entry.unbound_in_actual, undefined);
      assert.equal(entry.missing, 0, `${entry.name} misses rows`);
      assert.equal(entry.orphaned, 0);
      assert.equal(entry.diverged, 0);
      assert.equal(entry.human, 0);
      assert.equal(entry.balance.agrees, true);
    }
  });
});

test('Empty_OfAnAccountOfThousandsOfRows_FinishesAndReportsTheCount', async () => {
  await withEngine(async () => {
    const big = await api.createAccount({ name: 'BIG' }, 0);
    await api.addTransactions(
      big,
      Array.from({ length: 2500 }, (_, i) => ({
        date: '2026-09-01',
        amount: -(i + 1),
        payee_name: `p${i % 40}`,
        imported_id: `id${i}`,
        cleared: true,
      })),
    );

    const result = await run({ [big]: 2500 });

    assert.equal(result.complete, true);
    assert.equal(result.rows_removed, 2500);
    assert.deepEqual(await api.getAccounts(), []);
  });
});
