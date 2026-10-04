/**
 * One job that brings Actual into line with obdi: push, audit, remove what obdi
 * can explain, push again if the removal unlinked anything, audit.
 *
 * It is ONE queued request and one budget session, not a sequence the web side
 * queues step by step, because a sequence needs something to queue the next step
 * when each result arrives. The page starts nothing on a GET and holds no
 * process of its own, so a half-run sequence of separate requests would sit
 * looking finished, with nothing to say a step was still owed. Here the whole run
 * is one result file, written once, whose `complete` and `stopped_at` say how far
 * it got, and a run that dies midway writes no result at all, which the page
 * reads as a failed request and not as a finished one.
 *
 * It stops at the FIRST failure and says which step and why; nothing later runs
 * on a state the earlier step has just shown to be wrong. A step's result is
 * shaped as the standalone result of the same kind (a push's, an audit's, a
 * prune's), so the sync history shows each as it always has.
 *
 * What it will remove is bounded by the request, not decided here: `scope`
 * names the accounts it may touch and whether it may take rows obdi cannot
 * explain, and `confirmed` is the ceiling per account that the removal itself
 * re-counts against before it deletes anything (audit.mjs, pruneAccounts).
 * Deciding whether a removal is unexpectedly large is the Python side's
 * (web_prune.py); the applier obeys.
 */

import { auditAccounts, pruneAccounts } from './audit.mjs';
import { runPushStage } from './lib.mjs';
import { readMarker } from './marker.mjs';
import { auditTransfers } from './transfers.mjs';

const sum = (list, pick) => list.reduce((total, entry) => total + (pick(entry) ?? 0), 0);

export async function alignBudget(client, request, options = {}) {
  const now = options.now ?? (() => new Date());
  const onProgress = options.onProgress ?? (() => {});
  const steps = [];
  const bindings = [];
  const stamp = (result) => ({ ...result, finished_at: now().toISOString() });
  const record = (step, result) => steps.push({ step, result: stamp(result) });
  const stopped = (step, reason) => ({
    complete: false,
    stopped_at: step,
    stopped: reason,
    steps,
    bindings,
  });

  async function pushStep(step) {
    let outcome;
    try {
      outcome = await runPushStage(client, request, {
        onLinkProgress: ({ done, total }) => onProgress({ phase: 'linking', done, total }),
      });
    } catch (error) {
      const message = String(error?.message ?? error);
      record(step, { kind: 'push', ok: false, error: message });
      return message;
    }
    bindings.push(...outcome.provisioned.bindings);
    record(step, {
      kind: 'push',
      ok: true,
      added: outcome.applied.added,
      provisioned: outcome.provisioned.bindings.length,
      transfers: outcome.linked.counts,
      opening_balances: outcome.opening.counts,
      lines: [
        ...outcome.provisioned.lines,
        ...outcome.applied.lines,
        ...outcome.opening.lines,
        ...outcome.linked.lines,
      ],
    });
    const failedLinks = outcome.linked.counts.failed;
    if (failedLinks > 0) return `${failedLinks} transfer pair(s) failed to link`;
    const failedOpenings = outcome.opening.counts.failed;
    if (failedOpenings > 0) return `${failedOpenings} opening balance(s) failed to be corrected`;
    const created = outcome.provisioned.bindings.length;
    if (created > 0) {
      // Their rows are not in this request, so nothing after this would be a
      // comparison with what obdi holds for them.
      return (
        `${created} account(s) were created in Actual; their rows go in on the next push, ` +
        'so nothing further was run - press again'
      );
    }
    return null;
  }

  async function auditStep(step) {
    try {
      record(step, {
        kind: 'audit',
        ok: true,
        accounts: await auditAccounts(client, request.accounts, { history: request.history }),
        transfers: await auditTransfers(client, request.transfers),
        marker: await readMarker(client),
      });
    } catch (error) {
      const message = String(error?.message ?? error);
      record(step, { kind: 'audit', ok: false, error: message });
      return { message };
    }
    return { report: steps[steps.length - 1].result.accounts };
  }

  const failedPush = await pushStep('push');
  if (failedPush) return stopped('push', failedPush);

  const audited = await auditStep('audit');
  if (audited.message) return stopped('audit', audited.message);

  const inScope = new Set(Object.keys(request.scope));
  const orphans = sum(
    audited.report.filter((entry) => inScope.has(entry.account_id)),
    (entry) => entry.orphaned,
  );
  let unlinked = 0;
  if (orphans === 0) {
    steps.push({ step: 'prune', skipped: 'the audit found no orphan in an account in scope' });
  } else {
    let pruned;
    try {
      pruned = await pruneAccounts(client, request.accounts, {
        confirmed: request.confirmed,
        scope: request.scope,
        history: request.history,
        onProgress: ({ done, total }) => onProgress({ phase: 'removing', done, total }),
      });
    } catch (error) {
      const message = String(error?.message ?? error);
      record('prune', { kind: 'prune', ok: false, error: message });
      return stopped('prune', message);
    }
    record('prune', { kind: 'prune', ok: true, accounts: pruned });
    const trouble = pruned.find((entry) => entry.refused || entry.stopped);
    if (trouble) {
      return stopped('prune', `${trouble.name ?? trouble.account_id}: ${trouble.refused ?? trouble.stopped}`);
    }
    unlinked = sum(pruned, (entry) => entry.unlinked);
  }

  if (unlinked > 0) {
    const failedAgain = await pushStep('push_again');
    if (failedAgain) return stopped('push_again', failedAgain);
  }

  const final = await auditStep('audit_final');
  if (final.message) return stopped('audit_final', final.message);

  return { complete: true, stopped_at: null, stopped: null, steps, bindings };
}
