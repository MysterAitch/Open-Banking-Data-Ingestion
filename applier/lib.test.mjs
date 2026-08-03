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
