import test from 'node:test';
import assert from 'node:assert/strict';

test('provisioning reuses an existing account by name and creates the rest', async () => {
  const { provisionAccounts } = await import('./lib.mjs');
  const created = [];
  const client = {
    getAccounts: async () => [{ id: 'act-1', name: 'halifax-current' }],
    createAccount: async (account) => {
      created.push(account.name);
      return `act-new-${created.length}`;
    },
  };

  const result = await provisionAccounts(client, [
    { canonical_id: 'halifax-current', label: 'halifax-current' },
    { canonical_id: 'starling-space-bills', label: 'starling-space-bills' },
  ]);

  assert.deepEqual(created, ['starling-space-bills']);
  assert.deepEqual(result.bindings, [
    { canonical_id: 'halifax-current', actual_account_id: 'act-1' },
    { canonical_id: 'starling-space-bills', actual_account_id: 'act-new-1' },
  ]);
  assert.ok(result.lines[0].includes('reused'));
  assert.ok(result.lines[1].includes('created'));
});

test('provisioning falls back to the canonical id when the label is blank', async () => {
  const { provisionAccounts } = await import('./lib.mjs');
  const client = {
    getAccounts: async () => [],
    createAccount: async (account) => `id-for-${account.name}`,
  };

  const result = await provisionAccounts(client, [
    { canonical_id: 'halifax-card', label: '   ' },
  ]);

  assert.deepEqual(result.bindings, [
    { canonical_id: 'halifax-card', actual_account_id: 'id-for-halifax-card' },
  ]);
});

test('applying imports per account and reports added and updated counts', async () => {
  const { applyAccounts } = await import('./lib.mjs');
  const calls = [];
  const client = {
    importTransactions: async (accountId, transactions) => {
      calls.push({ accountId, count: transactions.length });
      return { added: transactions.map((t) => t.imported_id), updated: [] };
    },
  };

  const result = await applyAccounts(client, {
    'act-1': [{ imported_id: 'a' }, { imported_id: 'b' }],
    'act-2': [{ imported_id: 'c' }],
  });

  assert.deepEqual(calls, [
    { accountId: 'act-1', count: 2 },
    { accountId: 'act-2', count: 1 },
  ]);
  assert.ok(result.lines.length >= 2);
});

function transferClient(rows, { payees, failOn } = {}) {
  const updates = [];
  return {
    updates,
    getPayees: async () =>
      payees ?? [
        { id: 'pay-to-1', transfer_acct: 'act-1' },
        { id: 'pay-to-2', transfer_acct: 'act-2' },
      ],
    getTransactions: async (accountId) => rows[accountId] ?? [],
    updateTransaction: async (id, fields) => {
      if (failOn === id) throw new Error('engine refused');
      updates.push([id, fields]);
      const target = Object.values(rows).flat().find((r) => r.id === id);
      Object.assign(target, fields);
    },
  };
}

const PAIR = [
  {
    debit: { account: 'act-1', imported_id: 'k-a:0', date: '2026-09-02', amount: -500 },
    credit: { account: 'act-2', imported_id: 'k-b:0', date: '2026-09-02', amount: 500 },
  },
];
const freshRows = (extra = {}) => ({
  'act-1': [{ id: 'row-a', imported_id: 'k-a:0', amount: -500, ...extra }],
  'act-2': [{ id: 'row-b', imported_id: 'k-b:0', amount: 500 }],
});

test('linking updates the debit row first, naming the credit account\'s transfer payee', async () => {
  const { linkTransfers } = await import('./lib.mjs');
  const client = transferClient(freshRows());
  const result = await linkTransfers(client, PAIR);
  assert.deepEqual(client.updates, [
    ['row-a', { payee: 'pay-to-2', transfer_id: 'row-b' }],
    ['row-b', { payee: 'pay-to-1', transfer_id: 'row-a' }],
  ]);
  assert.equal(result.counts.linked, 1);
});

test('an account with no transfer payee is skipped and reported, never guessed', async () => {
  const { linkTransfers } = await import('./lib.mjs');
  const client = transferClient(freshRows(), {
    payees: [{ id: 'pay-to-1', transfer_acct: 'act-1' }],
  });
  const result = await linkTransfers(client, PAIR);
  assert.equal(result.counts.skipped.no_transfer_payee, 1);
  assert.deepEqual(client.updates, []);
});

test('a reconciled leg is skipped and reported, not updated', async () => {
  const { linkTransfers } = await import('./lib.mjs');
  const client = transferClient(freshRows({ reconciled: true }));
  const result = await linkTransfers(client, PAIR);
  assert.equal(result.counts.skipped.reconciled, 1);
  assert.deepEqual(client.updates, []);
});

test('an update the engine refuses is counted as failed and named, and the next pair still runs', async () => {
  const { linkTransfers } = await import('./lib.mjs');
  const rows = freshRows();
  rows['act-1'].push({ id: 'row-c', imported_id: 'k-c:0', amount: -700 });
  rows['act-2'].push({ id: 'row-d', imported_id: 'k-d:0', amount: 700 });
  const second = {
    debit: { account: 'act-1', imported_id: 'k-c:0', date: '2026-09-02', amount: -700 },
    credit: { account: 'act-2', imported_id: 'k-d:0', date: '2026-09-02', amount: 700 },
  };
  const client = transferClient(rows, { failOn: 'row-a' });
  const result = await linkTransfers(client, [...PAIR, second]);
  assert.equal(result.counts.failed, 1);
  assert.equal(result.counts.linked, 1);
  assert.ok(result.lines.some((line) => line.includes('engine refused')));
});

test('an update that leaves the rows unlinked is reported as a failed verification', async () => {
  const { linkTransfers } = await import('./lib.mjs');
  const client = transferClient(freshRows());
  client.updateTransaction = async () => {};
  const result = await linkTransfers(client, PAIR);
  assert.equal(result.counts.linked, 0);
  assert.equal(result.counts.failed, 1);
  assert.ok(result.lines.some((line) => line.includes('verification')));
});
