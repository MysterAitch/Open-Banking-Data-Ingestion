/**
 * The sync marker's name, how it is recognised, and how a request for one is
 * read. Pure: no budget engine.
 */

import assert from 'node:assert/strict';
import { test } from 'node:test';

import { parseEnvelope } from './envelope.mjs';
import { isMarkerName, markerAccounts, markerName, MARKER_SUFFIX } from './marker.mjs';

test('markerName for an evening in October reads day, month, 24-hour UTC time, then the fixed suffix', () => {
  assert.equal(markerName(new Date('2026-10-02T20:41:09Z')), '02 Oct 20:41Z obdi marker');
});

test('markerName pads a single-digit day and a single-digit hour and minute', () => {
  assert.equal(markerName(new Date('2026-03-05T04:07:00Z')), '05 Mar 04:07Z obdi marker');
});

test('markerName at midnight reads 00:00, never 24:00 or 12:00', () => {
  assert.equal(markerName(new Date('2026-06-15T00:00:00Z')), '15 Jun 00:00Z obdi marker');
});

test('markerName just before and just after a year boundary names the right day and month', () => {
  assert.equal(markerName(new Date('2026-12-31T23:59:59Z')), '31 Dec 23:59Z obdi marker');
  assert.equal(markerName(new Date('2027-01-01T00:00:00Z')), '01 Jan 00:00Z obdi marker');
});

test('markerName names every month by its three-letter English name', () => {
  const expected = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
  const actual = expected.map((_, index) =>
    markerName(new Date(Date.UTC(2026, index, 10, 12, 30))).split(' ')[1],
  );
  assert.deepEqual(actual, expected);
});

test('markerName is UTC whatever offset the instant was written in', () => {
  assert.equal(markerName(new Date('2026-10-02T21:41:00+01:00')), '02 Oct 20:41Z obdi marker');
  assert.equal(markerName(new Date('2026-10-03T01:30:00+05:00')), '02 Oct 20:30Z obdi marker');
});

test('markerName puts the stamp first so a phone sidebar that cuts after about 17 characters still shows it', () => {
  const name = markerName(new Date('2026-10-02T20:41:00Z'));
  assert.ok(name.startsWith('02 Oct 20:41Z'));
  assert.ok(name.slice(0, 17).includes('20:41Z'));
  assert.ok(name.endsWith(MARKER_SUFFIX));
});

test('markerName is plain ASCII with nothing beyond the characters of its format', () => {
  assert.match(markerName(new Date('2026-10-02T20:41:00Z')), /^\d\d [A-Z][a-z]{2} \d\d:\d\dZ obdi marker$/);
});

test('markerName refuses an instant that is not a date', () => {
  assert.throws(() => markerName(new Date('not a date')), /not a valid instant/);
});

test('isMarkerName recognises a name ending in the suffix', () => {
  assert.equal(isMarkerName('02 Oct 20:41Z obdi marker'), true);
  assert.equal(isMarkerName('anything obdi marker'), true);
});

test('isMarkerName does not recognise an account that merely contains the words elsewhere in its name', () => {
  assert.equal(isMarkerName('obdi marker notes'), false);
  assert.equal(isMarkerName('My obdi marker savings'), false);
  assert.equal(isMarkerName('obdi marker 02 Oct 20:41Z'), false);
});

test('isMarkerName needs the separating space, the exact case, and nothing trailing', () => {
  assert.equal(isMarkerName('obdi marker'), false);
  assert.equal(isMarkerName('02 Oct 20:41Z OBDI MARKER'), false);
  assert.equal(isMarkerName('02 Oct 20:41Z obdi marker '), false);
  assert.equal(isMarkerName('02 Oct 20:41Zobdi marker'), false);
});

test('isMarkerName is false for a name that is not text', () => {
  assert.equal(isMarkerName(undefined), false);
  assert.equal(isMarkerName(null), false);
  assert.equal(isMarkerName(42), false);
});

test('markerAccounts keeps only the marker accounts, in the order given', () => {
  const accounts = [
    { id: 'a', name: 'Current' },
    { id: 'b', name: '01 Oct 08:00Z obdi marker' },
    { id: 'c', name: 'obdi marker notes' },
    { id: 'd', name: '02 Oct 20:41Z obdi marker' },
  ];
  assert.deepEqual(markerAccounts(accounts).map((a) => a.id), ['b', 'd']);
});

test('A marker request is read as the marker kind and carries no accounts to touch', () => {
  const parsed = parseEnvelope({ version: 3, kind: 'marker' });
  assert.equal(parsed.kind, 'marker');
  assert.deepEqual(parsed.accounts, {});
  assert.deepEqual(parsed.provision, []);
  assert.deepEqual(parsed.transfers, []);
  assert.deepEqual(parsed.openings, []);
});

test('A marker request with a stray prune ceiling does not read it, so a stray key cannot widen anything', () => {
  const parsed = parseEnvelope({ version: 3, kind: 'marker', clear_empty: { x: 5 } });
  assert.equal(parsed.kind, 'marker');
  assert.equal('clear_empty' in parsed, false);
});

test('A request naming a kind the applier does not know is still not read as the marker kind', () => {
  assert.notEqual(parseEnvelope({ version: 3, kind: 'markers' }).kind, 'marker');
  assert.notEqual(parseEnvelope({ version: 3, kind: 'Marker' }).kind, 'marker');
});
