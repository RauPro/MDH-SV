import { sourceId } from '../../../tests/source-identity-fixture.mjs';
import { expect, it } from 'vitest';
import fixture from './__fixtures__/newsArchive.json';
import { NEWS_DOSSIER_FIELDS, newsDossierFor, parsePublishedNewsDossiers, pendingNewsDossier, validateDossierBindings, validatePublishedNewsDossier, type NewsDossier } from '../../../shared/news-dossier.mjs';

const reference = fixture.records[0];
function approved(): NewsDossier {
  const value = pendingNewsDossier(reference);
  value.extraction_status = 'processed'; value.human_reviewed = true;
  value.reviewed_by = 'reviewer'; value.reviewed_at = '2026-10-10';
  value.review_notes = 'Fixture: fuente y privacidad revisadas expresamente por una persona.';
  return value;
}

it('constructs a complete neutral worksheet for every reference without inspecting private state or territorial hints', () => {
  const value = pendingNewsDossier(reference);
  expect(value.source_title).toBe(reference.title);
  expect(value.publisher).toBe(reference.publisher);
  expect(value.published_date).toBe(reference.published_date);
  expect(Object.keys(value.fields)).toEqual(NEWS_DOSSIER_FIELDS);
  expect(Object.values(value.fields).every(field => field.status === 'pending' && field.value === null && field.evidence_refs.length === 0)).toBe(true);
  expect(value.classification).toEqual({ jurisdiction: 'unclear', relevance: 'unclear', record_type: null });
  expect(value.human_reviewed).toBe(false);
  expect(value.reviewed_by).toBeNull();
  expect(value.evidence[0].url).toBe(reference.source_url);
});

it('accepts an editorially reviewed but incomplete or non-relevant news dossier, including a privacy-redacted title', () => {
  const value = approved();
  value.source_title = 'Título ficticio minimizado por privacidad';
  value.classification = { jurisdiction: 'other', relevance: 'not_relevant', record_type: null };
  value.fields.summary = { status: 'reported', value: 'Resumen ficticio de prueba.', evidence_refs: ['bibliography'], note: null };
  const index = parsePublishedNewsDossiers({ schema_version: 1, dossiers: [value] });
  expect(validateDossierBindings(index, fixture.records)).toBe(index);
  expect(newsDossierFor(reference, index)).toEqual(value);
  expect(newsDossierFor(reference, index).fields.country.status).toBe('pending');
});

it('keeps planned future dates as source-attributed news information without applying case-only temporal restrictions', () => {
  const value = approved();
  value.fields.event_date = { value: '2026-10-20', status: 'reported', evidence_refs: ['bibliography'], note: 'Fecha ficticia de una actividad anunciada por la fuente.' };
  value.date_precision = 'day';
  expect(validatePublishedNewsDossier(value).fields.event_date.value).toBe('2026-10-20');
});

it.each([
  (value: NewsDossier) => { value.human_reviewed = false; },
  (value: NewsDossier) => { value.extraction_status = 'pending'; },
  (value: NewsDossier) => { value.extraction_status = 'source_unavailable'; },
  (value: NewsDossier) => { value.review_notes = 'Listo'; },
  (value: NewsDossier) => { value.reviewed_at = '2026-02-30'; },
  (value: NewsDossier) => { value.reviewed_at = '2099-01-01'; },
  (value: NewsDossier) => { value.reviewed_at = '2026-10-01'; },
  (value: NewsDossier) => { value.review_notes = 'r'.repeat(4001); },
  (value: NewsDossier) => { value.fields.summary = { value: 'x'.repeat(2001), status: 'reported', evidence_refs: ['bibliography'], note: null }; },
  (value: NewsDossier) => { value.fields.themes = { value: Array(41).fill('Tema'), status: 'reported', evidence_refs: ['bibliography'], note: null }; },
  (value: NewsDossier) => { value.fields.themes = { value: ['x'.repeat(301)], status: 'reported', evidence_refs: ['bibliography'], note: null }; },
  (value: NewsDossier) => { value.fields.country.note = 'x'.repeat(1001); },
  (value: NewsDossier) => { value.evidence[0].locator = 'x'.repeat(1001); },
  (value: NewsDossier) => { value.fields.facts.value = 'No puede aparecer como pendiente'; },
  (value: NewsDossier) => { value.fields.country.evidence_refs = ['bibliography']; },
  (value: NewsDossier) => { value.fields.facts = { value: 'Hecho ficticio', status: 'reported', evidence_refs: ['missing'], note: null }; },
  (value: NewsDossier) => { value.fields.affected_persons = { value: 'No es una lista', status: 'reported', evidence_refs: ['bibliography'], note: null }; },
  (value: NewsDossier) => { value.fields.country = { value: null, status: 'withheld', evidence_refs: [], note: 'Corto' }; },
  (value: NewsDossier) => { value.evidence[0].url = 'https://unapproved.example/private'; },
  (value: NewsDossier) => { value.evidence[0].url = 'https://user:password@www.diariocolatino.com/source/'; },
  (value: NewsDossier) => { Object.assign(value, { private_grounding: 'PRIVATE' }); },
  (value: NewsDossier) => { Object.assign(value.fields, { secret: 'PRIVATE' }); },
])('rejects unapproved, misleading or unsafe public overlay data (%#)', change => {
  const value = approved(); change(value);
  expect(() => parsePublishedNewsDossiers({ schema_version: 1, dossiers: [value] })).toThrow();
});

it.each(['source_url', 'publisher', 'published_date', 'candidate_id'] as const)('requires exact bibliography binding for %s', key => {
  const value = approved();
  if (key === 'source_url') { value.source_url = 'https://www.diariocolatino.com/other-article/'; value.evidence[0].url = value.source_url; value.candidate_id = sourceId(value.source_url); }
  if (key === 'publisher') value.publisher = 'Otra editorial';
  if (key === 'published_date') value.published_date = '2026-10-07';
  if (key === 'candidate_id') {
    value.candidate_id = 'a'.repeat(64);
    expect(() => parsePublishedNewsDossiers({ schema_version: 1, dossiers: [value] })).toThrow();
    return;
  }
  const index = parsePublishedNewsDossiers({ schema_version: 1, dossiers: [value] });
  expect(() => validateDossierBindings(index, fixture.records)).toThrow();
  expect(newsDossierFor(reference, index).human_reviewed).toBe(false);
});

it('rejects duplicate or private collection fields and does not share mutable placeholders', () => {
  const value = approved();
  expect(() => parsePublishedNewsDossiers({ schema_version: 1, dossiers: [value, value] })).toThrow();
  expect(() => parsePublishedNewsDossiers({ schema_version: 1, dossiers: [], private_queue: [] })).toThrow();
  const a = pendingNewsDossier(reference); const b = pendingNewsDossier(reference);
  a.fields.country.note = 'Only the first object changes';
  expect(b.fields.country.note).toBeNull();
});

it('bounds the complete public dossier even when individual evidence fields are within their limits', () => {
  const value = approved();
  value.evidence = Array.from({ length: 200 }, (_, index) => ({ id: `source-${index}`, url: value.source_url, locator: 'Referencia ficticia. '.repeat(40) }));
  expect(() => validatePublishedNewsDossier(value)).toThrow();
});
