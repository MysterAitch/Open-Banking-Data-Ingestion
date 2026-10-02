/**
 * The image copies a hand-written list of files, so a module the entry points
 * import but the list omits only fails once the container is running, as a
 * crash loop (audit.mjs was found missing that way). This reads the list and
 * walks the imports from both entry points, so the omission fails here.
 */

import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { test } from 'node:test';

const here = (name) => new URL(name, import.meta.url);

async function copiedFiles() {
  const dockerfile = await readFile(here('./Dockerfile'), 'utf8');
  const copied = new Set();
  for (const line of dockerfile.split(/\r?\n/)) {
    const match = /^COPY\s+(.+)\s+\.\/\s*$/.exec(line.trim());
    if (!match) continue;
    for (const name of match[1].split(/\s+/)) copied.add(name);
  }
  return copied;
}

async function importedFrom(entry) {
  const seen = new Set();
  const queue = [entry];
  while (queue.length) {
    const name = queue.pop();
    if (seen.has(name)) continue;
    seen.add(name);
    const source = await readFile(here(`./${name}`), 'utf8');
    for (const match of source.matchAll(/(?:from\s+|import\(\s*)['"]\.\/([\w.-]+\.mjs)['"]/g)) {
      queue.push(match[1]);
    }
  }
  return seen;
}

test('Dockerfile_CopyList_CoversEveryModuleTheEntryPointsImportTransitively', async () => {
  const copied = await copiedFiles();
  const needed = new Set([
    ...(await importedFrom('watcher.mjs')),
    ...(await importedFrom('apply.mjs')),
  ]);

  assert.ok(needed.has('empty.mjs'), 'the walk reaches the empty module');
  const missing = [...needed].filter((name) => !copied.has(name)).sort();
  assert.deepEqual(missing, [], `imported but not copied into the image: ${missing.join(', ')}`);
});

test('Dockerfile_CopyList_NamesNoFileThatDoesNotExist', async () => {
  for (const name of await copiedFiles()) {
    if (!name.endsWith('.mjs')) continue;
    await readFile(here(`./${name}`), 'utf8');
  }
});
