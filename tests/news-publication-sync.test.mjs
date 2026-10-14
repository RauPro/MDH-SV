import test from 'node:test';
import assert from 'node:assert/strict';
import { reconcileNewsPublication } from '../apps/web/scripts/sync-news-publication.mjs';
import { pendingNewsDossier } from '../shared/news-dossier.mjs';
import fixtureArchive from '../apps/web/src/__fixtures__/newsArchive.json' with { type: 'json' };

const reference = fixtureArchive.records[0];
const collection = dossiers => ({ schema_version: 1, dossiers });
function input() {
  const auto = pendingNewsDossier(reference);
  Object.assign(auto, { schema_version: 2, extraction_status: 'processed',
    classification: { jurisdiction: 'SV', relevance: 'relevant', record_type: 'publication' },
    automation: { policy_version: 'auto-news-v1', source_content_sha256: reference.selection.evidence.content_sha256,
      validated_at: '2026-10-10T12:00:00Z', method: 'gemini_evidence_review', evaluator_model: 'gemini-test', corroboration: 'single_source' } });
  auto.fields.summary = { value: 'AUTOMATIC_PUBLIC_VALUE', status: 'reported', evidence_refs: ['bibliography'], note: null };
  return { archive: structuredClone(fixtureArchive), automated: collection([auto]), reviewed: collection([]),
    exceptions: { schema_version: 1, decisions: [] },
    locations: { schema_version: 5, generated_at: '2026-10-10T12:00:00Z', locations: [{
      candidate_id: reference.id, source_url: reference.source_url, publisher: reference.publisher, published_date: reference.published_date,
      review_status: 'automated_unreviewed', human_reviewed: false, method: 'source_explicit_department', rule_version: 2,
      department: 'San Salvador', extracted_at: '2026-10-10T12:00:00Z', source_checked_at: '2026-10-10T12:00:00Z',
      evidence: { paragraph_id: 'p1', content_sha256: reference.selection.evidence.content_sha256 },
    }] } };
}
function reviewed(value) {
  const human = structuredClone(value.automated.dossiers[0]);
  delete human.automation;
  Object.assign(human, { schema_version: 1, human_reviewed: true, reviewed_by: 'reviewer', reviewed_at: '2026-10-10', review_notes: 'Corrección y privacidad revisadas por una persona autorizada.' });
  human.fields.summary = { value: 'HUMAN_CORRECTION', status: 'reported', evidence_refs: ['bibliography'], note: null };
  value.reviewed.dossiers.push(human);
  return human;
}

test('public reconciliation preserves evidence and timestamps without generating new dossiers', () => {
  const before = input();
  const copy = structuredClone(before);
  const result = reconcileNewsPublication(before, '2026-10-14T12:00:00Z');
  assert.deepEqual(result.automated, before.automated);
  assert.deepEqual(result.published, before.automated);
  assert.deepEqual(result.locations, before.locations);
  assert.deepEqual(before, copy);
  const empty = input(); empty.automated.dossiers = [];
  assert.equal(reconcileNewsPublication(empty).published.dossiers.length, 0);
});

test('absent references, changed source versions and bibliography changes discard stale automatic data', () => {
  for (const mutate of [
    value => {
      value.archive.records = value.archive.records.filter(row => row.id !== reference.id);
      value.archive.coverage.records = value.archive.records.length;
      value.archive.policy.selection_gate.selected = value.archive.records.length;
    },
    value => { value.archive.records[0].selection.evidence.content_sha256 = 'f'.repeat(64); },
    value => { value.archive.records[0].publisher = 'Otro medio'; },
    value => { value.archive.records[0].published_date = '2026-01-01'; },
  ]) {
    const before = input(); mutate(before);
    const result = reconcileNewsPublication(before, '2026-10-14T12:00:00Z');
    assert.equal(result.automated.dossiers.length, 0);
    assert.equal(result.published.dossiers.length, 0);
    assert.equal(result.locations.locations.length, 0);
    assert.equal(result.locations.generated_at, '2026-10-14T12:00:00Z');
  }
});

test('human corrections replace and remove the old automatic source, preserving the original human record', () => {
  const before = input(); const human = reviewed(before);
  const savedHuman = structuredClone(before.reviewed);
  const result = reconcileNewsPublication(before);
  assert.deepEqual(result.published.dossiers, [human]);
  assert.equal(result.automated.dossiers.length, 0);
  assert.equal(result.locations.locations.length, 0);
  assert.deepEqual(before.reviewed, savedHuman);
  assert.ok(!JSON.stringify(result).includes('AUTOMATIC_PUBLIC_VALUE'));
});

test('human scope exclusions and maintainer withdrawals remove all public projections without deleting human source', () => {
  for (const exclusion of ['human_scope', 'withdrawal']) {
    const before = input(); const human = reviewed(before);
    if (exclusion === 'human_scope') human.classification.relevance = 'not_relevant';
    else before.exceptions.decisions.push({ candidate_id: reference.id, withdrawn: true, request_generation: 1,
      issue_number: 2, decided_by: 'maintainer', decided_at: '2026-10-10T12:00:00Z' });
    const result = reconcileNewsPublication(before);
    assert.equal(result.published.dossiers.length, 0);
    assert.equal(result.automated.dossiers.length, 0);
    assert.equal(result.locations.locations.length, 0);
    assert.equal(before.reviewed.dossiers.length, 1);
  }
});

test('protected department cannot be re-exposed through an earlier location projection', () => {
  const before = input();
  before.automated.dossiers[0].fields.department = { value: null, status: 'withheld', evidence_refs: [], note: 'Ubicación omitida por protección de personas.' };
  const result = reconcileNewsPublication(before);
  assert.equal(result.published.dossiers.length, 1);
  assert.equal(result.locations.locations.length, 0);
});

test('invalid or private inputs fail before reconciliation rather than silently sanitizing unsafe data', () => {
  for (const mutate of [
    value => { value.automated.dossiers[0].private_prompt = 'PRIVATE_SENTINEL'; },
    value => { value.automated.dossiers[0].automation.policy_version = 'unsafe'; },
    value => { value.locations.locations[0].raw_article = 'PRIVATE_SENTINEL'; },
    value => { value.exceptions.decisions.push({ candidate_id: reference.id, withdrawn: true }); },
    value => { value.reviewed.dossiers.push(structuredClone(value.automated.dossiers[0])); },
    value => { const human = reviewed(value); value.automated.dossiers = [human]; },
    value => {
      value.archive.records.push(structuredClone(value.archive.records[0]));
      value.archive.coverage.records += 1;
      value.archive.policy.selection_gate.selected += 1;
    },
  ]) {
    const before = input(); mutate(before);
    assert.throws(() => reconcileNewsPublication(before));
  }
});
