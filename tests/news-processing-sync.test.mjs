import test from 'node:test';
import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { reconcileNewsProcessing } from '../apps/web/scripts/sync-news-processing.mjs';

const source = 'https://www.diariocolatino.com/noticia-de-prueba/';
const identifier = url => createHash('sha256').update(url).digest('hex');
const reference = { id: identifier(source), source_url: source, publisher: 'Diario Co Latino', published_date: '2015-01-02',
  selection: { method: 'gemini_grounded_scope', evidence: { content_sha256: 'b'.repeat(64) } } };
const entry = { candidate_id: reference.id, source_url: source, publisher: reference.publisher, published_date: reference.published_date,
  selection_content_sha256: 'b'.repeat(64), status: 'generated' };
const snapshot = () => ({ schema_version: 1, generated_at: '2026-10-13T12:00:00Z', entries: [structuredClone(entry)] });

test('public refresh preserves valid processing status without manufacturing a new timestamp', () => {
  const payload = snapshot();
  assert.deepEqual(reconcileNewsProcessing(payload, [reference]), payload);
});

test('new news references do not become pending without a private inventory check', () => {
  const another = { ...reference, id: identifier(source + 'otra/'), source_url: source + 'otra/' };
  assert.deepEqual(reconcileNewsProcessing(snapshot(), [reference, another]), snapshot());
});

test('withdrawn or changed reference receipts are removed without changing the reviewed fields', () => {
  for (const references of [[], [{ ...reference, publisher: 'Otro medio' }], [{ ...reference, published_date: '2015-02-01' }],
    [{ ...reference, selection: { method: 'gemini_grounded_scope', evidence: { content_sha256: 'c'.repeat(64) } } }]]) {
    assert.deepEqual(reconcileNewsProcessing(snapshot(), references, '2026-10-14T12:00:00Z'),
      { schema_version: 1, generated_at: '2026-10-14T12:00:00Z', entries: [] });
  }
});

test('sync rejects unexpected private fields and duplicate reference identities', () => {
  const invalid = snapshot(); invalid.entries[0].summary = 'Private model text';
  assert.throws(() => reconcileNewsProcessing(invalid, [reference]));
  assert.throws(() => reconcileNewsProcessing(snapshot(), [reference, reference]));
});
