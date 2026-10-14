import { test } from 'node:test';
import assert from 'node:assert/strict';
import { NEWS_PROCESSING_STATUSES, newsProcessingFor, parseNewsProcessing, processingMatchesReference, validateNewsProcessingEntry, validateProcessingBindings } from '../shared/news-processing.mjs';
import { pendingNewsDossier } from '../shared/news-dossier.mjs';
import { parseNewsExceptions } from '../shared/news-exceptions.mjs';
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

function automaticallyPublished() {
  const dossier = pendingNewsDossier(reference);
  return { ...dossier, schema_version: 2, extraction_status: 'processed',
    classification: { jurisdiction: 'SV', relevance: 'relevant', record_type: 'publication' },
    fields: { ...dossier.fields, summary: { value: 'Según la fuente, se presentó un informe de derechos humanos.', status: 'reported', evidence_refs: ['bibliography'], note: null } },
    automation: { policy_version: 'auto-news-v1', source_content_sha256: reference.selection.evidence.content_sha256,
      validated_at: '2026-10-10T12:00:00Z', method: 'gemini_evidence_review', evaluator_model: 'gemini-test', corroboration: 'single_source' } };
}

test('only a bound validated automatic dossier grants automatically_published, independent of processing feed', () => {
  const dossier = automaticallyPublished();
  for (const status of NEWS_PROCESSING_STATUSES) {
    assert.equal(newsProcessingFor(reference, parseNewsProcessing(collection([entry(status)])), dossier), 'automatically_published');
  }
  assert.equal(newsProcessingFor(reference, new Map(), dossier), 'automatically_published');
  for (const mutate of [
    value => { value.schema_version = 1; },
    value => { delete value.automation; },
    value => { value.automation.policy_version = 'invented-policy'; },
    value => { value.automation.method = 'no_validation'; },
    value => { value.automation.corroboration = 'independently_verified'; },
    value => { value.automation.source_content_sha256 = 'f'.repeat(64); },
    value => { value.automation.source_content_sha256 = 'invalid'; },
    value => { value.automation.evaluator_model = ''; },
    value => { value.automation.evaluator_model = 'x'.repeat(101); },
    value => { value.automation.evaluator_model = 'gemini\nsecret'; },
    value => { value.automation.validated_at = '2999-01-01T00:00:00Z'; },
    value => { value.automation.validated_at = '2000-01-01T00:00:00Z'; },
    value => { value.automation.validated_at = '2026-02-30T00:00:00Z'; },
    value => { value.automation.validated_at = '2026-10-10T12:00:00-06:00'; },
    value => { value.automation.validated_at = '2026-10-10'; },
    value => { value.automation.private_reasoning = 'PRIVATE_SENTINEL'; },
    value => { value.private_model_output = 'PRIVATE_SENTINEL'; },
    value => { value.classification.jurisdiction = 'other'; },
    value => { value.classification.relevance = 'unclear'; },
    value => { value.extraction_status = 'pending'; },
    value => { value.human_reviewed = true; },
    value => { value.reviewed_by = 'reviewer'; },
    value => { value.reviewed_at = '2026-10-10'; },
    value => { value.review_notes = 'Invented human approval record.'; },
    value => { value.fields.summary = { value: null, status: 'pending', evidence_refs: [], note: null }; },
  ]) {
    const invalid = structuredClone(dossier); mutate(invalid);
    assert.equal(newsProcessingFor(reference, new Map(), invalid), 'unknown');
  }
  assert.equal(newsProcessingFor({ ...reference, selection: { method: 'editorial_scope' } }, new Map(), dossier), 'unknown');
  assert.throws(() => parseNewsProcessing(collection([{ ...entry(), status: 'automatically_published' }])));
});

test('exception records bind maintainer decisions without public request text or private content', () => {
  const decision = { candidate_id: reference.id, withdrawn: true, request_generation: 0, issue_number: 123, decided_by: 'maintainer', decided_at: '2026-10-14T12:00:00Z' };
  const payload = { schema_version: 1, decisions: [decision] };
  const index = parseNewsExceptions(payload);
  assert.deepEqual(index.get(reference.id), decision);
  decision.withdrawn = false;
  assert.equal(index.get(reference.id).withdrawn, true);
  assert.equal(parseNewsExceptions({ ...payload, decisions: [] }).size, 0);
  for (const mutate of [
    value => { value.schema_version = 2; },
    value => { value.issue_body = 'PRIVATE_SENTINEL'; },
    value => { value.decisions.push(structuredClone(value.decisions[0])); },
    value => { value.decisions[0].candidate_id = 'bad'; },
    value => { value.decisions[0].withdrawn = 'true'; },
    value => { value.decisions[0].request_generation = -1; },
    value => { value.decisions[0].request_generation = 1.5; },
    value => { value.decisions[0].issue_number = 0; },
    value => { value.decisions[0].issue_number = Number.MAX_SAFE_INTEGER + 1; },
    value => { value.decisions[0].decided_by = 'invalid/name'; },
    value => { value.decisions[0].decided_at = '2026-02-30T00:00:00Z'; },
    value => { value.decisions[0].decided_at = '2999-01-01T00:00:00Z'; },
    value => { value.decisions[0].decided_at = '2026-10-14T06:00:00-06:00'; },
    value => { value.decisions[0].private_notes = 'PRIVATE_SENTINEL'; },
  ]) {
    const unsafe = structuredClone(payload); mutate(unsafe);
    assert.throws(() => parseNewsExceptions(unsafe));
  }
});
