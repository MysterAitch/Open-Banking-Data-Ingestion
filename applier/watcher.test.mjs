import test from 'node:test';
import assert from 'node:assert/strict';
import process from 'node:process';


test('a running request keeps beating until it finishes, so a long import never reads as a dead applier', async () => {
  const { startKeepalive } = await import('./watcher.mjs');
  const beats = [];
  const stop = startKeepalive(async () => beats.push(Date.now()), 5);

  await new Promise((resolve) => setTimeout(resolve, 40));
  const whileRunning = beats.length;
  stop();
  await new Promise((resolve) => setTimeout(resolve, 30));

  assert.ok(whileRunning >= 2, `expected repeated beats, saw ${whileRunning}`);
  assert.equal(beats.length, whileRunning, 'beats continued after the request finished');
});

test('a request declaring an unknown envelope version is refused and recorded as a failed result', async () => {
  const { mkdtemp, mkdir, rm, writeFile } = await import('node:fs/promises');
  const { tmpdir } = await import('node:os');
  const { join } = await import('node:path');
  const base = await mkdtemp(join(tmpdir(), 'obdi-watcher-'));
  const previous = process.env.OBDI_ACTUAL_DIR;
  process.env.OBDI_ACTUAL_DIR = base;
  try {
    await mkdir(join(base, 'requests'), { recursive: true });
    await writeFile(
      join(base, 'requests', 'push-20260901T000000000000Z.json'),
      JSON.stringify({ version: 9, provision: [], accounts: {} }),
    );
    // BASE is read when the module loads, so this import must follow the
    // environment change; a cached module from an earlier test would
    // aim at the wrong directory.
    const { processRequest, failedResult } = await import('./watcher.mjs?refusal');

    await assert.rejects(
      () => processRequest('push-20260901T000000000000Z.json'),
      /unsupported envelope version 9/,
    );
    const recorded = failedResult('push-x.json', new Error('unsupported envelope version 9'));
    assert.equal(recorded.ok, false);
    assert.match(recorded.error, /unsupported envelope version 9/);
  } finally {
    if (previous === undefined) delete process.env.OBDI_ACTUAL_DIR;
    else process.env.OBDI_ACTUAL_DIR = previous;
    await rm(base, { recursive: true, force: true });
  }
});

test('a failing beat does not stop the keepalive: the next renewal still happens', async () => {
  const { startKeepalive } = await import('./watcher.mjs');
  let attempts = 0;
  const stop = startKeepalive(async () => {
    attempts += 1;
    throw new Error('transient write failure');
  }, 5);

  await new Promise((resolve) => setTimeout(resolve, 30));
  stop();

  assert.ok(attempts >= 2, `expected retries after a failure, saw ${attempts}`);
});

async function withRequest(name, body, work) {
  const { mkdtemp, mkdir, rm, writeFile } = await import('node:fs/promises');
  const { tmpdir } = await import('node:os');
  const { join } = await import('node:path');
  const base = await mkdtemp(join(tmpdir(), 'obdi-watcher-empty-'));
  const previous = process.env.OBDI_ACTUAL_DIR;
  process.env.OBDI_ACTUAL_DIR = base;
  try {
    await mkdir(join(base, 'requests'), { recursive: true });
    await writeFile(join(base, 'requests', name), JSON.stringify(body));
    // BASE is read at load, so each case imports under its own query string.
    return await work(await import(`./watcher.mjs?${name}`));
  } finally {
    if (previous === undefined) delete process.env.OBDI_ACTUAL_DIR;
    else process.env.OBDI_ACTUAL_DIR = previous;
    await rm(base, { recursive: true, force: true });
  }
}

// A budget with accounts that records what is asked of it.
function budgetOf(accounts) {
  const calls = [];
  const live = new Map(accounts.map((a) => [a.id, a]));
  const client = {
    getAccounts: async () => [...live.values()],
    getTransactions: async (id) => (live.has(id) ? live.get(id).rows : []),
    reopenAccount: async (id) => calls.push(['reopen', id]),
    deleteAccount: async (id) => {
      calls.push(['delete', id]);
      live.delete(id);
    },
  };
  return { calls, withBudget: async (work) => work(client) };
}

const row = (id) => ({ id, is_child: false });

test('an empty request is answered as an empty, with what was removed, and never as a push', async () => {
  const name = 'empty-20260901T000000000000Z.json';
  await withRequest(
    name,
    { version: 3, kind: 'empty', empty_accounts: { a: 2, b: 1 } },
    async ({ processRequest }) => {
      const { calls, withBudget } = budgetOf([
        { id: 'a', name: 'Current', rows: [row('1'), row('2')] },
        { id: 'b', name: 'Savings', closed: true, rows: [row('3')] },
      ]);
      const beats = [];

      const result = await processRequest(name, (progress) => beats.push(progress), withBudget);

      assert.equal(result.ok, true);
      assert.equal(result.kind, 'empty');
      assert.equal(result.complete, true);
      assert.equal(result.rows_removed, 3);
      assert.deepEqual(calls, [['delete', 'a'], ['reopen', 'b'], ['delete', 'b']]);
      assert.deepEqual(beats, [
        { phase: 'emptying', done: 1, total: 2 },
        { phase: 'emptying', done: 2, total: 2 },
      ]);
    },
  );
});

test('an empty request that was refused is an answered request with complete false and nothing deleted', async () => {
  const name = 'empty-20260901T000001000000Z.json';
  await withRequest(
    name,
    { version: 3, kind: 'empty', empty_accounts: { a: 1 } },
    async ({ processRequest }) => {
      const { calls, withBudget } = budgetOf([
        { id: 'a', name: 'Current', rows: [row('1'), row('2')] },
      ]);

      const result = await processRequest(name, () => {}, withBudget);

      assert.equal(result.ok, true);
      assert.equal(result.complete, false);
      assert.match(result.refused, /more than the 1 told/);
      assert.deepEqual(calls, []);
    },
  );
});

test('an empty request without the counts it was shown is refused before any budget is opened', async () => {
  const name = 'empty-20260901T000002000000Z.json';
  await withRequest(name, { version: 3, kind: 'empty' }, async ({ processRequest }) => {
    let opened = false;
    await assert.rejects(
      () =>
        processRequest(name, () => {}, async () => {
          opened = true;
        }),
      /empty_accounts/,
    );
    assert.equal(opened, false);
  });
});
