import assert from 'node:assert/strict';
import { test } from 'node:test';

import { isActive } from './lease.mjs';

const now = Date.parse('2026-08-02T12:00:00Z');

test('a fresh lease is active; past its ttl it reads as absent', () => {
  const entry = { name: 'pull-cycle', taken_at: '2026-08-02T11:50:00Z', ttl_seconds: 1800 };
  assert.equal(isActive(entry, now), true);
  assert.equal(isActive(entry, now + 1801 * 1000), false);
});

test('malformed leases read as absent - crash safety over strictness', () => {
  assert.equal(isActive(null, now), false);
  assert.equal(isActive({ taken_at: 'not a date', ttl_seconds: 600 }, now), false);
  assert.equal(isActive({ taken_at: '2026-08-02T11:50:00Z' }, now), false);
  assert.equal(isActive({ taken_at: '2026-08-02T11:50:00Z', ttl_seconds: 0 }, now), false);
});
