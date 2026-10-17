import { sourceId } from '../../../tests/source-identity-fixture.mjs';
import { describe, expect, it } from 'vitest';
import { NEWS_LOCATION_DEPARTMENTS, newsLocationFor, parsePublishedNewsLocations, validateLocationBindings, validatePublishedNewsLocation, type NewsLocation } from '../../../shared/news-location.mjs';
import { DEPARTMENTS } from './geography/elSalvador';
import fixture from './__fixtures__/newsArchive.json';
import municipalityReference from '../../../apps/pipeline/src/mdh_pipeline/data/el_salvador_municipalities.json';

const reference = fixture.records[0];
const locationFixture = (reference = fixture.records[0]): Extract<NewsLocation, { method: 'gemini_explicit_department' }> => ({
  candidate_id: reference.id, source_url: reference.source_url, publisher: reference.publisher, published_date: reference.published_date,
  department: 'San Salvador', review_status: 'automated_unreviewed', human_reviewed: false, method: 'gemini_explicit_department',
  extracted_at: '2026-10-10T10:00:00Z', source_checked_at: '2026-10-10T10:02:00+00:00', evidence: { paragraph_id: 'p1', content_sha256: reference.selection.evidence.content_sha256 },
});
const collection = (locations: unknown[] = [locationFixture()]) => ({ schema_version: 1, generated_at: '2026-10-10T10:03:00Z', locations });

describe('public automatic location contract', () => {
  const corroborated = (): Extract<NewsLocation, { method: 'gemini_department_corroborated' }> => ({
    ...locationFixture(), method: 'gemini_department_corroborated', evidence: { paragraph_id: 'p3', content_sha256: 'a'.repeat(64) },
    corroboration: { strategy: 'same_article_explicit_department', model_paragraph_id: 'p1', explicit_paragraph_id: 'p3', rule_version: 1 },
  });
  it.each([3, 4, 5])('accepts a corroborated department in schema %i with distinct and bound paragraph IDs', schema_version => {
    const location = corroborated();
    const index = parsePublishedNewsLocations({ ...collection([location]), schema_version });
    expect(newsLocationFor(reference, validateLocationBindings(index, fixture.records))).toEqual(location);
    expect(location.human_reviewed).toBe(false);
    for (const schema_version of [1, 2]) expect(() => parsePublishedNewsLocations({ ...collection([location]), schema_version })).toThrow();
  });
  it.each([1, 2, 3, 4, 5])('keeps direct and municipal locations compatible with schema %i', schema_version => {
    expect(parsePublishedNewsLocations({ ...collection(), schema_version }).size).toBe(1);
    expect(parsePublishedNewsLocations({ ...collection([municipal()]), schema_version }).size).toBe(1);
  });
  it.each([
    { strategy: 'other_article' }, { rule_version: 2 }, { rule_version: true }, { model_paragraph_id: 'p0' },
    { explicit_paragraph_id: 'p1000' }, { model_paragraph_id: 'p3' }, { model_paragraph_id: null },
    { explicit_paragraph_id: 'p4' }, { quote: 'PRIVATE VALUE' },
  ])('rejects invalid corroboration provenance: %j', patch => {
    const location = corroborated();
    expect(() => validatePublishedNewsLocation({ ...location, corroboration: { ...location.corroboration, ...patch } })).toThrow();
  });
  it('requires corroboration only on its own method and rejects mixed provenance', () => {
    const { corroboration, ...withoutProof } = corroborated();
    expect(() => validatePublishedNewsLocation(withoutProof)).toThrow();
    expect(() => validatePublishedNewsLocation({ ...locationFixture(), corroboration })).toThrow();
    expect(() => validatePublishedNewsLocation({ ...municipal(), corroboration })).toThrow();
    expect(() => validatePublishedNewsLocation({ ...corroborated(), resolution: municipal().resolution })).toThrow();
    expect(() => validatePublishedNewsLocation({ ...corroborated(), evidence: { paragraph_id: 'p1', content_sha256: 'a'.repeat(64) } })).toThrow();
  });
  const municipal = (): NewsLocation => ({ ...locationFixture(), department: 'Ahuachapán', method: 'gemini_municipality_reference', resolution: {
    type: 'municipality_to_department', municipality: 'Jujutla', municipality_kind: 'district',
    reference_version: municipalityReference.reference_version, source_url: `${municipalityReference.source.url}#page=6`,
  } });
  const sourceDepartment = (): Extract<NewsLocation, { method: 'source_explicit_department' }> => ({
    ...locationFixture(), method: 'source_explicit_department', rule_version: 1,
  });
  const sourceMunicipality = (): Extract<NewsLocation, { method: 'source_municipality_reference' }> => ({
    ...locationFixture(), department: 'Ahuachapán', method: 'source_municipality_reference', rule_version: 1,
    resolution: { type: 'municipality_to_department', municipality: 'Jujutla', municipality_kind: 'district',
      reference_version: municipalityReference.reference_version, source_url: `${municipalityReference.source.url}#page=6` },
  });
  const plural = (): Extract<NewsLocation, { method: 'source_administrative_mentions' }> => {
    const { department, evidence, ...common } = locationFixture();
    return { ...common, method: 'source_administrative_mentions', rule_version: 2,
      evidence: { content_sha256: evidence.content_sha256 }, mentions: [
        { method: 'source_explicit_department', department, paragraph_id: 'p1' },
        { method: 'source_municipality_reference', department: 'Ahuachapán', paragraph_id: 'p2', resolution: sourceMunicipality().resolution },
      ] };
  };
  it('accepts plural source mentions only in schema 5 without inventing a main department', () => {
    const location = plural();
    const index = parsePublishedNewsLocations({ ...collection([location]), schema_version: 5 });
    expect(newsLocationFor(reference, validateLocationBindings(index, fixture.records))).toEqual(location);
    expect(location).not.toHaveProperty('department');
    expect(location.evidence).not.toHaveProperty('paragraph_id');
    for (const schema_version of [1, 2, 3, 4]) {
      expect(() => parsePublishedNewsLocations({ ...collection([location]), schema_version })).toThrow();
    }
    const allDepartments = { ...location, mentions: NEWS_LOCATION_DEPARTMENTS.map((department, index) => ({
      method: 'source_explicit_department', department, paragraph_id: `p${index + 1}`,
    })) };
    expect(validatePublishedNewsLocation(allDepartments).mentions).toHaveLength(14);
  });
  it('requires the plural rule, unique canonical departments and exact nested provenance', () => {
    for (const change of [
      (value: Record<string, unknown>) => { value.rule_version = 1; },
      (value: Record<string, unknown>) => { value.department = 'San Salvador'; },
      (value: Record<string, unknown>) => { value.resolution = sourceMunicipality().resolution; },
      (value: Record<string, unknown>) => { value.corroboration = corroborated().corroboration; },
      (value: Record<string, unknown>) => { value.evidence = { content_sha256: 'a'.repeat(64), paragraph_id: 'p1' }; },
      (value: Record<string, unknown>) => { value.mentions = []; },
      (value: Record<string, unknown>) => { value.mentions = [plural().mentions[0]]; },
      (value: Record<string, unknown>) => { value.mentions = Array(15).fill(plural().mentions[0]); },
      (value: Record<string, unknown>) => { value.mentions = [plural().mentions[0], { ...plural().mentions[0], paragraph_id: 'p3' }]; },
      (value: Record<string, unknown>) => { value.mentions = [plural().mentions[0], { ...plural().mentions[1], department: 'San Miguel' }]; },
      (value: Record<string, unknown>) => { value.mentions = [plural().mentions[0], { ...plural().mentions[1], private_quote: 'PRIVATE' }]; },
      (value: Record<string, unknown>) => { value.mentions = [plural().mentions[0], { ...plural().mentions[1], method: 'gemini_municipality_reference' }]; },
    ]) {
      const location: Record<string, unknown> = { ...structuredClone(plural()) };
      change(location);
      expect(() => validatePublishedNewsLocation(location)).toThrow();
    }
  });
  it('allows source rule 2 for a single department only in schema 5', () => {
    for (const location of [sourceDepartment(), sourceMunicipality()]) {
      const current = { ...location, rule_version: 2 };
      expect(parsePublishedNewsLocations({ ...collection([current]), schema_version: 5 }).size).toBe(1);
      for (const schema_version of [1, 2, 3, 4]) {
        expect(() => parsePublishedNewsLocations({ ...collection([current]), schema_version })).toThrow();
      }
    }
  });
  it.each([4, 5])('accepts source rule 1 in schema %i without attributing it to a model', schema_version => {
    for (const location of [sourceDepartment(), sourceMunicipality()]) {
      const index = parsePublishedNewsLocations({ ...collection([location]), schema_version });
      expect(newsLocationFor(reference, validateLocationBindings(index, fixture.records))).toEqual(location);
      expect(location.rule_version).toBe(1);
      expect(location.human_reviewed).toBe(false);
      expect(location).not.toHaveProperty('corroboration');
      for (const schema_version of [1, 2, 3]) {
        expect(() => parsePublishedNewsLocations({ ...collection([location]), schema_version })).toThrow();
      }
    }
  });
  it.each([undefined, null, 0, 3, true, '1'])('requires an integer source rule_version of 1 or 2, rejecting %j', rule_version => {
    for (const location of [sourceDepartment(), sourceMunicipality()]) {
      expect(() => validatePublishedNewsLocation({ ...location, rule_version })).toThrow();
    }
  });
  it('rejects mixed provenance and private cache fields on source-only locations', () => {
    for (const location of [sourceDepartment(), sourceMunicipality()]) {
      const { rule_version, ...missingRule } = location;
      expect(rule_version).toBe(1);
      expect(() => validatePublishedNewsLocation(missingRule)).toThrow();
      expect(() => validatePublishedNewsLocation({ ...location, corroboration: corroborated().corroboration })).toThrow();
      expect(() => validatePublishedNewsLocation({ ...location, full_text: 'PRIVATE' })).toThrow();
    }
    expect(() => validatePublishedNewsLocation({ ...locationFixture(), rule_version: 1 })).toThrow();
    expect(() => validatePublishedNewsLocation({ ...sourceDepartment(), resolution: sourceMunicipality().resolution })).toThrow();
    const { resolution, ...missingResolution } = sourceMunicipality();
    expect(resolution.municipality).toBe('Jujutla');
    expect(() => validatePublishedNewsLocation(missingResolution)).toThrow();
    expect(() => validatePublishedNewsLocation({ ...sourceMunicipality(), department: 'San Salvador' })).toThrow();
    for (const field of ['source_cache', 'counters', 'source_attempts']) {
      expect(() => parsePublishedNewsLocations({ ...collection([sourceDepartment()]), schema_version: 4, [field]: {} })).toThrow();
    }
  });
  it('requires every automatic method to use the same source text as automatic selection', () => {
    for (const location of [locationFixture(), municipal(), corroborated(), sourceDepartment(), sourceMunicipality(), plural()]) {
      const stale = { ...location, evidence: { ...location.evidence, content_sha256: 'f'.repeat(64) } };
      const index = parsePublishedNewsLocations({ ...collection([stale]), schema_version: 5 });
      expect(() => validateLocationBindings(index, fixture.records)).toThrow();
      expect(newsLocationFor(reference, index)).toBeNull();
    }
    const reviewedReference = { ...reference, selection: { method: 'editorial_scope', human_reviewed: true,
      review_status: 'editorially_reviewed', jurisdiction: 'SV', relevance: 'relevant', reviewed_by: 'reviewer', reviewed_at: '2026-10-10' } };
    const location = sourceDepartment();
    const index = parsePublishedNewsLocations({ ...collection([location]), schema_version: 4 });
    expect(validateLocationBindings(index, [reviewedReference])).toBe(index);
    expect(newsLocationFor(reviewedReference, index)).toEqual(location);
  });
  it('validates municipality-to-department provenance against the bundled official reference', () => {
    expect(validatePublishedNewsLocation(municipal())).toEqual(municipal());
    const wrong = municipal(); wrong.department = 'San Salvador';
    expect(() => validatePublishedNewsLocation(wrong)).toThrow();
  });
  it.each([
    { municipality: 'Lugar inventado' }, { municipality_kind: 'municipality' },
    { reference_version: 'unverified' }, { source_url: 'https://example.com/geography' },
    { source_url: `${municipalityReference.source.url}#page=13` }, { private_quote: 'SENSITIVE' },
  ])('rejects a forged municipal reference: %j', patch => {
    const location = municipal();
    expect(() => validatePublishedNewsLocation({ ...location, resolution: { ...location.resolution, ...patch } })).toThrow();
  });
  it('requires resolution only for the municipal method and does not add it to legacy department entries', () => {
    const location = municipal(); delete location.resolution;
    expect(() => validatePublishedNewsLocation(location)).toThrow();
    expect(() => validatePublishedNewsLocation({ ...municipal(), method: 'gemini_explicit_department' })).toThrow();
  });
  it('accepts only the narrow unreviewed contract and matches map departments', () => {
    const index = parsePublishedNewsLocations(collection());
    expect(newsLocationFor(reference, validateLocationBindings(index, fixture.records))).toEqual(locationFixture());
    expect([...NEWS_LOCATION_DEPARTMENTS].sort()).toEqual(DEPARTMENTS.map(department => department.name).sort());
    expect(newsLocationFor(fixture.records[1], index)).toBeNull();
  });
  it.each([
    { human_reviewed: true }, { review_status: 'reviewed' }, { method: 'inferred_from_city' }, { department: 'Ciudad de San Salvador' },
    { candidate_id: 'bad' }, { published_date: '2026-02-30' }, { extracted_at: 'yesterday' }, { extracted_at: '2026-10-10T24:00:00Z' }, { published_date: '0000-01-01' }, { source_checked_at: '2026-02-30T10:00:00Z' },
    { source_url: 'https://evil.example/source' }, { private_extraction: 'Do not publish' }, { evidence: { paragraph_id: 'p1', content_sha256: 'bad' } },
    { evidence: { paragraph_id: 'p1', content_sha256: 'a'.repeat(64), quote: 'PRIVATE QUOTE' } },
  ])('rejects invalid or private fields: %j', patch => {
    expect(() => validatePublishedNewsLocation({ ...locationFixture(), ...patch })).toThrow();
  });
  it('rejects duplicates, unexpected collection keys and malformed timestamps', () => {
    expect(() => parsePublishedNewsLocations(collection([locationFixture(), locationFixture()]))).toThrow();
    expect(() => parsePublishedNewsLocations({ ...collection(), drafts: [] })).toThrow();
    expect(() => parsePublishedNewsLocations({ ...collection(), generated_at: '2026-10-10' })).toThrow();
  });
  it.each(['source_url', 'publisher', 'published_date', 'candidate_id'] as const)('requires exact bibliography binding for %s', key => {
    const changed = { ...locationFixture(), [key]: key === 'published_date' ? '2026-10-01' : key === 'candidate_id' ? 'b'.repeat(64) : key === 'publisher' ? 'Otro medio' : 'https://www.diariocolatino.com/otra-noticia/' };
    if (key === 'candidate_id' || key === 'publisher') {
      expect(() => parsePublishedNewsLocations(collection([changed]))).toThrow();
      return;
    }
    if (key === 'source_url') changed.candidate_id = sourceId(changed.source_url);
    const index = parsePublishedNewsLocations(collection([changed]));
    expect(() => validateLocationBindings(index, fixture.records)).toThrow();
    expect(newsLocationFor(reference, index)).toBeNull();
  });
});
