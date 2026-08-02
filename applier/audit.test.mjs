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
