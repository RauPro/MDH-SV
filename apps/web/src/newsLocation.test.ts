import { describe, expect, it } from 'vitest';
import { NEWS_LOCATION_DEPARTMENTS, newsLocationFor, parsePublishedNewsLocations, validateLocationBindings, validatePublishedNewsLocation, type NewsLocation } from '../../../shared/news-location.mjs';
import { DEPARTMENTS } from './geography/elSalvador';
import fixture from './__fixtures__/newsArchive.json';
import municipalityReference from '../../../apps/pipeline/src/mdh_pipeline/data/el_salvador_municipalities.json';

const reference = fixture.records[0];
const locationFixture = (reference = fixture.records[0]): NewsLocation => ({
  candidate_id: reference.id, source_url: reference.source_url, publisher: reference.publisher, published_date: reference.published_date,
  department: 'San Salvador', review_status: 'automated_unreviewed', human_reviewed: false, method: 'gemini_explicit_department',
  extracted_at: '2026-10-10T10:00:00Z', source_checked_at: '2026-10-10T10:02:00+00:00', evidence: { paragraph_id: 'p1', content_sha256: 'a'.repeat(64) },
});
const collection = (locations: unknown[] = [locationFixture()]) => ({ schema_version: 1, generated_at: '2026-10-10T10:03:00Z', locations });

describe('public automatic location contract', () => {
  const municipal = (): NewsLocation => ({ ...locationFixture(), department: 'Ahuachapán', method: 'gemini_municipality_reference', resolution: {
    type: 'municipality_to_department', municipality: 'Jujutla', municipality_kind: 'district',
    reference_version: municipalityReference.reference_version, source_url: `${municipalityReference.source.url}#page=6`,
  } });
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
    const index = parsePublishedNewsLocations(collection([changed]));
    expect(() => validateLocationBindings(index, fixture.records)).toThrow();
    expect(newsLocationFor(reference, index)).toBeNull();
  });
});
