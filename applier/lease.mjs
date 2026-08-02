/**
 * The applier's half of the lease protocol (see src/obdi/leases.py for
 * the whole contract). Cooperative: a lease is one JSON file with a
 * mandatory TTL, and an expired lease reads as absent so a crashed
 * holder can never wedge the stack.
 */

import { readFile, rename, unlink, writeFile } from 'node:fs/promises';
import { join } from 'node:path';

export function isActive(entry, nowMs) {
  if (!entry || typeof entry !== 'object') return false;
  const taken = Date.parse(String(entry.taken_at ?? ''));
  const ttl = Number(entry.ttl_seconds ?? 0);
  if (!Number.isFinite(taken) || !Number.isFinite(ttl) || ttl <= 0) return false;
  return nowMs - taken <= ttl * 1000;
}

export async function leaseHeld(directory, name, nowMs = Date.now()) {
  try {
    const entry = JSON.parse(await readFile(join(directory, `${name}.json`), 'utf8'));
    return isActive(entry, nowMs);
  } catch {
    return false;
  }
}

export async function takeLease(directory, name, holder, ttlSeconds) {
  // Temp-then-rename, mirroring the Python side: a reader must never see
  // a torn lease, because unparseable reads as absent.
  const tmp = join(directory, `.${name}.json.tmp`);
  await writeFile(
    tmp,
    JSON.stringify({
      name,
      holder,
      taken_at: new Date().toISOString(),
      ttl_seconds: ttlSeconds,
    }),
  );
  await rename(tmp, join(directory, `${name}.json`));
}

export async function releaseLease(directory, name) {
  try {
    await unlink(join(directory, `${name}.json`));
  } catch {
    // Already gone: releasing twice is not an error.
  }
}
