/**
 * Envelope handling, pure and separately testable.
 *
 * Two shapes arrive. Version 1 is the original flat payload:
 * { actualAccountId: [transactions] }. Version 2 wraps it and adds
 * provisioning: { version: 2, provision: [{canonical_id, label}],
 * accounts: {actualAccountId: [transactions]} }.
 *
 * Provisioning exists so account creation is automated rather than
 * point-and-click: each provision entry becomes an Actual account, and the
 * minted binding flows back to the Python side, which merges it into the
 * account map before building the next envelope - so a provisioned
 * account's transactions ride the following push, not this one.
 */

export function parseEnvelope(payload) {
  if (payload && typeof payload === 'object' && payload.version === 2) {
    const provision = Array.isArray(payload.provision) ? payload.provision : [];
    const accounts =
      payload.accounts && typeof payload.accounts === 'object'
        ? payload.accounts
        : {};
    return {
      // 'audit' asks for a read-back-and-compare instead of an import;
      // anything else is a push, so an unknown kind cannot silently
      // become a write.
      kind: payload.kind === 'audit' ? 'audit' : 'push',
      provision: provision.filter(
        (entry) => entry && typeof entry.canonical_id === 'string' && entry.canonical_id,
      ),
      accounts,
    };
  }
  // Version 1: the whole payload IS the accounts map.
  return {
    kind: 'push',
    provision: [],
    accounts: payload && typeof payload === 'object' ? payload : {},
  };
}

export function mergeBindings(existing, minted) {
  // Dedupe by canonical id; the newest mint wins. Order is stable so the
  // pending-bindings file diffs cleanly between runs.
  const byCanonical = new Map();
  for (const entry of [...(existing ?? []), ...(minted ?? [])]) {
    if (entry && typeof entry.canonical_id === 'string' && entry.canonical_id) {
      byCanonical.set(entry.canonical_id, entry);
    }
  }
  return [...byCanonical.values()].sort((a, b) =>
    a.canonical_id.localeCompare(b.canonical_id),
  );
}
