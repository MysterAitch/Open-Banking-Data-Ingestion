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
