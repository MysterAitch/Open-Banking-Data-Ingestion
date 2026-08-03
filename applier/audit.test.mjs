import assert from 'node:assert/strict';
import { test } from 'node:test';

import { partitionAccount, summariseAudit } from './audit.mjs';

const expected = [
  { imported_id: 'ck-1:0', date: '2026-07-01', amount: -100 },
  { imported_id: 'ck-2:0', date: '2026-07-02', amount: -250 },
  { imported_id: 'ck-3:0', date: '2026-07-03', amount: 5000 },
];

test('matching rows are present, absent rows are missing', () => {
  const result = partitionAccount(expected, [
    { imported_id: 'ck-1:0', date: '2026-07-01', amount: -100 },
  ]);
  assert.equal(result.present, 1);
  assert.deepEqual(result.missing, ['ck-2:0', 'ck-3:0']);
  assert.equal(result.orphaned.length, 0);
  assert.equal(result.diverged.length, 0);
});

test('an unexpected imported_id is orphaned - provably ours, listed', () => {
  const result = partitionAccount(expected, [
    { imported_id: 'ck-other:0', date: '2026-06-01', amount: -900 },
  ]);
  assert.deepEqual(result.orphaned, [
    { imported_id: 'ck-other:0', date: '2026-06-01', amount: -900 },
  ]);
});

test('rows without imported_id are the person\'s own - counted, never listed', () => {
  const result = partitionAccount(expected, [
    { imported_id: null, date: '2026-07-04', amount: -1234, payee: 'private' },
    { date: '2026-07-05', amount: 999 },
  ]);
  assert.equal(result.human, 2);
  assert.equal(result.orphaned.length, 0);
});

test('an edited amount or date diverges; categories and payees never do', () => {
  const result = partitionAccount(expected, [
    { imported_id: 'ck-1:0', date: '2026-07-01', amount: -101 },
    { imported_id: 'ck-2:0', date: '2026-07-09', amount: -250 },
    { imported_id: 'ck-3:0', date: '2026-07-03', amount: 5000, category: 'x', notes: 'y' },
  ]);
  assert.equal(result.diverged.length, 2);
  assert.equal(result.present, 3);
  assert.deepEqual(result.missing, []);
});

test('split children are skipped - the parent carries the id and the total', () => {
  const result = partitionAccount(expected, [
    { imported_id: 'ck-1:0', date: '2026-07-01', amount: -100, is_parent: true },
    { imported_id: null, date: '2026-07-01', amount: -60, is_child: true },
    { imported_id: null, date: '2026-07-01', amount: -40, is_child: true },
  ]);
  assert.equal(result.present, 1);
  assert.equal(result.human, 0);
});

test('summary carries counts in full and caps the samples', () => {
  const missing = Array.from({ length: 30 }, (_, i) => `ck-${i}:0`);
  const summary = summariseAudit({
    expected: 30,
    present: 0,
    missing,
    orphaned: [],
    human: 2,
    diverged: [],
  });
  assert.equal(summary.missing, 30);
  assert.equal(summary.missing_sample.length, 10);
  assert.equal(summary.human, 2);
});

test('accounts existing in Actual but bound to nothing are named as strays', async () => {
  const { auditAccounts } = await import('./audit.mjs');
  const client = {
    getAccounts: async () => [
      { id: 'act-1', name: 'halifax-current-account' },
      { id: 'act-stray', name: 'Mr Roger Howell (halifax)' },
    ],
    getTransactions: async (id) =>
      id === 'act-stray'
        ? [
            { imported_id: 'ck-1:0', date: '2026-07-01', amount: -100 },
            { imported_id: null, date: '2026-07-01', amount: -60, is_child: true },
          ]
        : [],
  };

  const report = await auditAccounts(client, { 'act-1': [] });

  const stray = report.find((entry) => entry.unbound_in_actual);
  assert.equal(stray.name, 'Mr Roger Howell (halifax)');
  assert.equal(stray.rows, 1);
  assert.ok(report.find((entry) => entry.account_id === 'act-1' && !entry.unbound_in_actual));
});

const hex = (seed) => seed.repeat(64).slice(0, 64);

test('prunable rows are ours-and-unexpected only; yours are untouchable', async () => {
  const { choosePrunable } = await import('./audit.mjs');
  const expected = new Set([`${hex('1')}:0`, `${hex('2')}:0`]);
  const rows = [
    { id: 'a', imported_id: `${hex('1')}:0` },
    { id: 'b', imported_id: `${hex('e')}:0` },
    { id: 'c', imported_id: null },
    { id: 'd', imported_id: `${hex('e')}:1`, is_child: true },
  ];
  const prunable = choosePrunable(expected, rows);
  assert.deepEqual(prunable, [{ id: 'b', imported_id: `${hex('e')}:0` }]);
});

test('an imported id that is not obdi-shaped is untouchable, like no id at all', async () => {
  const { choosePrunable, isObdiImportedId } = await import('./audit.mjs');
  const expected = new Set([`${hex('1')}:0`]);
  const rows = [
    { id: 'a', imported_id: 'FITID-20260803-0001' },
    { id: 'b', imported_id: 'ck-stale:0' },
    { id: 'c', imported_id: `${hex('a')}:0`.toUpperCase() },
    { id: 'd', imported_id: `${hex('e')}:2` },
  ];
  const prunable = choosePrunable(expected, rows);
  assert.deepEqual(prunable, [{ id: 'd', imported_id: `${hex('e')}:2` }]);
  assert.equal(isObdiImportedId(`${hex('e')}:2`), true);
  assert.equal(isObdiImportedId('FITID-20260803-0001'), false);
});

test('prune reports the foreign-id rows it deliberately left alone', async () => {
  const { pruneAccounts } = await import('./audit.mjs');
  const deleted = [];
  const client = {
    getAccounts: async () => [{ id: 'act-1', name: 'halifax-current' }],
    getTransactions: async () => [
      { id: 'a', imported_id: `${hex('1')}:0` },
      { id: 'b', imported_id: `${hex('e')}:0` },
      { id: 'c', imported_id: 'FITID-20260803-0001' },
      { id: 'd', imported_id: null },
    ],
    deleteTransaction: async (id) => deleted.push(id),
  };

  const report = await pruneAccounts(client, {
    'act-1': [{ imported_id: `${hex('1')}:0` }],
  });

  assert.deepEqual(deleted, ['b']);
  assert.equal(report[0].removed, 1);
  assert.equal(report[0].foreign_ids, 1);
});

test('an empty expected set is refused, never pruned blind', async () => {
  const { pruneAccounts } = await import('./audit.mjs');
  const client = {
    getAccounts: async () => [{ id: 'act-1', name: 'halifax-current' }],
    getTransactions: async () => {
      throw new Error('must not even read when refusing');
    },
    deleteTransaction: async () => {
      throw new Error('must not delete');
    },
  };
  const report = await pruneAccounts(client, { 'act-1': [] });
  assert.ok(report[0].skipped);
});


test('a duplicate row sharing one imported id is counted, not collapsed', async () => {
  const { partitionAccount, summariseAudit } = await import('./audit.mjs');
  const expected = [{ imported_id: 'ck-1:0', date: '2026-07-01', amount: -1200 }];
  const rows = [
    { imported_id: 'ck-1:0', date: '2026-07-01', amount: -1200 },
    { imported_id: 'ck-1:0', date: '2026-07-01', amount: -1200 },
  ];
  const summary = summariseAudit(partitionAccount(expected, rows));
  assert.equal(summary.duplicated, 1);
  assert.deepEqual(summary.duplicated_sample, [{ imported_id: 'ck-1:0', copies: 2 }]);
  assert.equal(summary.present, 1);
});
