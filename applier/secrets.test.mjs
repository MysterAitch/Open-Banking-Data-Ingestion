/**
 * Where the applier finds a secret that is allowed to be absent.
 *
 * The budget file's encryption password is the case: most budgets have none.
 * `.env.example` documents it in the file form, as it does every secret, and
 * the applier used to read only the plain variable - so an encrypted budget
 * configured as documented was opened with no password at all.
 */

import test from 'node:test';
import assert from 'node:assert/strict';

const NAME = 'ACTUAL_ENCRYPTION_PASSWORD';

function reader(files) {
  return async (path) => {
    if (!(path in files)) throw new Error(`ENOENT: no such file, open '${path}'`);
    return files[path];
  };
}

test('an optional secret given as a file is read from the file, trimmed', async () => {
  const { optionalSecret } = await import('./lib.mjs');

  const secret = await optionalSecret(
    NAME,
    { [`${NAME}_FILE`]: '/run/secrets/enc' },
    reader({ '/run/secrets/enc': 'correct horse\n' }),
  );

  assert.equal(secret, 'correct horse');
});

test('an optional secret given as a plain variable is used as it is, trimmed', async () => {
  const { optionalSecret } = await import('./lib.mjs');

  assert.equal(await optionalSecret(NAME, { [NAME]: ' plain one ' }, reader({})), 'plain one');
});

test('an optional secret given neither way is empty, and nothing is read', async () => {
  const { optionalSecret } = await import('./lib.mjs');
  const read = async () => {
    throw new Error('nothing should be read');
  };

  assert.equal(await optionalSecret(NAME, {}, read), '');
});

test('when both forms are set the file wins, as it does for a required secret', async () => {
  const { optionalSecret } = await import('./lib.mjs');

  const secret = await optionalSecret(
    NAME,
    { [NAME]: 'from-variable', [`${NAME}_FILE`]: '/run/secrets/enc' },
    reader({ '/run/secrets/enc': 'from-file' }),
  );

  assert.equal(secret, 'from-file');
});

test('a file that is named and missing is an error, not an absent secret', async () => {
  const { optionalSecret } = await import('./lib.mjs');

  await assert.rejects(
    optionalSecret(NAME, { [`${NAME}_FILE`]: '/run/secrets/gone' }, reader({})),
    /ACTUAL_ENCRYPTION_PASSWORD_FILE.*\/run\/secrets\/gone/s,
  );
});

test('a file that is named and empty is an error, not an absent secret', async () => {
  const { optionalSecret } = await import('./lib.mjs');

  await assert.rejects(
    optionalSecret(
      NAME,
      { [`${NAME}_FILE`]: '/run/secrets/enc' },
      reader({ '/run/secrets/enc': '  \n' }),
    ),
    /ACTUAL_ENCRYPTION_PASSWORD_FILE.*empty/s,
  );
});
