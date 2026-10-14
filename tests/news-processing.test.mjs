import { test } from 'node:test';
import assert from 'node:assert/strict';
import { NEWS_PROCESSING_STATUSES, newsProcessingFor, parseNewsProcessing, processingMatchesReference, validateNewsProcessingEntry, validateProcessingBindings } from '../shared/news-processing.mjs';
import { pendingNewsDossier } from '../shared/news-dossier.mjs';
import fixtureArchive from '../apps/web/src/__fixtures__/newsArchive.json' with { type: 'json' };

const reference = fixtureArchive.records[0];
const entry = (status = 'generated') => ({
  candidate_id: reference.id, source_url: reference.source_url, publisher: reference.publisher,
  published_date: reference.published_date, selection_content_sha256: reference.selection.evidence.content_sha256, status,
});
const collection = (entries = [entry()]) => ({ schema_version: 1, generated_at: '2026-10-14T12:00:00Z', entries });
const reviewed = () => {
  const dossier = pendingNewsDossier(reference);
  return { ...dossier, extraction_status: 'processed', human_reviewed: true, reviewed_by: 'reviewer', reviewed_at: '2026-10-10', review_notes: 'Fixture de revisión explícita de fuente y datos públicos.' };
};

test('processing metadata distinguishes each operational state without publishing draft fields', () => {
  for (const status of NEWS_PROCESSING_STATUSES) {
    const index = parseNewsProcessing(collection([entry(status)]));
    assert.equal(newsProcessingFor(reference, index), status);
    assert.equal(validateProcessingBindings(index, [reference]), index);
    assert.deepEqual(Object.keys(index.get(reference.id)), ['candidate_id', 'source_url', 'publisher', 'published_date', 'selection_content_sha256', 'status']);
  }
});

test('missing metadata is unknown and cannot be inferred from a placeholder, location, or selection', () => {
  assert.equal(newsProcessingFor(reference), 'unknown');
  assert.equal(newsProcessingFor(reference, parseNewsProcessing(collection([]))), 'unknown');
  assert.equal(newsProcessingFor(reference, new Map(), pendingNewsDossier(reference)), 'unknown');
  assert.equal(validateProcessingBindings(new Map(), [reference]).size, 0);
});

test('a valid reviewed dossier takes priority over unavailable, pending and generated metadata', () => {
  for (const status of NEWS_PROCESSING_STATUSES) {
    assert.equal(newsProcessingFor(reference, parseNewsProcessing(collection([entry(status)])), reviewed()), 'reviewed');
  }
  assert.equal(newsProcessingFor(reference, new Map(), reviewed()), 'reviewed');
  for (const change of [
    value => { value.human_reviewed = false; },
    value => { value.publisher = 'Another publisher'; },
    value => { value.source_url = 'https://www.diariocolatino.com/different-reference/'; },
    value => { value.published_date = '2020-01-01'; },
    value => { value.reviewed_by = null; },
    value => { value.private_model_output = 'PRIVATE_SENTINEL'; },
  ]) {
    const dossier = reviewed(); change(dossier);
    assert.equal(newsProcessingFor(reference, new Map(), dossier), 'unknown');
  }
});

test('stale bibliography or selection hashes invalidate only their own runtime entry', () => {
  for (const change of [
    value => { value.publisher = 'Another publisher'; },
    value => { value.source_url = 'https://www.diariocolatino.com/different-reference/'; },
    value => { value.published_date = '2020-01-01'; },
    value => { value.selection_content_sha256 = 'f'.repeat(64); },
    value => { value.selection_content_sha256 = null; value.status = 'unknown'; },
  ]) {
    const stale = entry(); change(stale);
    const index = parseNewsProcessing(collection([stale]));
    assert.equal(newsProcessingFor(reference, index), 'unknown');
    assert.equal(processingMatchesReference(stale, reference), false);
    assert.throws(() => validateProcessingBindings(index, [reference]));
  }
  const secondReference = fixtureArchive.records[1];
  const secondEntry = { ...entry('pending'), candidate_id: secondReference.id, source_url: secondReference.source_url, publisher: secondReference.publisher, published_date: secondReference.published_date, selection_content_sha256: secondReference.selection.evidence.content_sha256 };
  const index = parseNewsProcessing(collection([entry(), secondEntry]));
  const staleReference = { ...secondReference, published_date: '2020-01-01' };
  assert.equal(newsProcessingFor(reference, index), 'generated');
  assert.equal(newsProcessingFor(staleReference, index), 'unknown');
  assert.throws(() => validateProcessingBindings(index, [reference]));
  assert.throws(() => validateProcessingBindings(index, [reference, reference]));
});

test('editorial selection uses null selection hash and still requires matching bibliography', () => {
  const editorial = { ...reference, selection: { method: 'editorial_scope' } };
  const index = parseNewsProcessing(collection([{ ...entry('pending'), selection_content_sha256: null }]));
  assert.equal(newsProcessingFor(editorial, index), 'pending');
  assert.equal(newsProcessingFor({ ...reference, selection: undefined }, index), 'unknown');
  assert.equal(newsProcessingFor(editorial, parseNewsProcessing(collection())), 'unknown');
});

test('strict collection contract rejects duplicates, malformed metadata and private extra fields', () => {
  for (const change of [
    value => { value.schema_version = 2; },
    value => { value.generated_at = '2026-02-30T12:00:00Z'; },
    value => { value.generated_at = '2026-10-14'; },
    value => { value.generated_at = '2026-10-14T12:00:00+25:99'; },
    value => { value.private_queue = []; },
    value => { value.entries.push(structuredClone(value.entries[0])); },
    value => { value.entries.push({ ...value.entries[0], candidate_id: 'f'.repeat(64) }); },
    value => { value.entries[0].candidate_id = 'not-an-id'; },
    value => { value.entries[0].source_url = 'https://untrusted.example/article'; },
    value => { value.entries[0].source_url = 'https://user:pass@www.diariocolatino.com/article/'; },
    value => { value.entries[0].published_date = '2026-02-30'; },
    value => { value.entries[0].selection_content_sha256 = 'invalid'; },
    value => { value.entries[0].selection_content_sha256 = null; },
    value => { value.entries[0].published_date = null; },
    value => { value.entries[0].publisher = ''; },
    value => { value.entries[0].status = 'reviewed'; },
    value => { value.entries[0].status = 'processed'; },
    value => { value.entries[0].status = 'error: PRIVATE_SENTINEL'; },
    value => { value.entries[0].fields = { summary: 'PRIVATE_SENTINEL' }; },
    value => { value.entries[0].raw_article = 'PRIVATE_SENTINEL'; },
  ]) {
    const payload = collection(); change(payload);
    assert.throws(() => parseNewsProcessing(payload), error => error.message === 'Estado público de procesamiento de noticias inválido');
  }
});

test('validated metadata is copied and direct caller maps cannot smuggle private fields', () => {
  const payload = collection();
  const index = parseNewsProcessing(payload);
  payload.entries[0].status = 'error';
  assert.equal(newsProcessingFor(reference, index), 'generated');
  const unsafe = { ...entry(), raw_article: 'PRIVATE_SENTINEL' };
  assert.equal(newsProcessingFor(reference, new Map([[reference.id, unsafe]])), 'unknown');
  assert.throws(() => validateNewsProcessingEntry(unsafe));
});
