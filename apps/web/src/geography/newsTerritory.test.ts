import { describe, expect, it } from 'vitest';
import { pendingNewsDossier, type NewsDossier, type NewsDossierFieldStatus } from '../../../../shared/news-dossier.mjs';
import fixture from '../__fixtures__/newsArchive.json';
import { parseNewsArchive, type NewsReference } from '../newsArchive';
import { DEPARTMENTS } from './elSalvador';
import { buildNewsTerritoryItems, matchesNewsTerritory, newsTerritoryItem, summarizeNewsTerritory } from './newsTerritory';
import { UNKNOWN_DEPARTMENT } from './territory';

const reference = parseNewsArchive(fixture).records[0];
function approved(source: NewsReference = reference): NewsDossier {
  const dossier = pendingNewsDossier(source);
  dossier.extraction_status = 'processed';
  dossier.human_reviewed = true;
  dossier.reviewed_by = 'reviewer';
  dossier.reviewed_at = '2026-10-10';
  dossier.review_notes = 'Fixture: fuente, ubicación y privacidad revisadas expresamente por una persona.';
  dossier.classification.jurisdiction = 'SV';
  dossier.fields.department = { value: 'San Salvador', status: 'reported', evidence_refs: ['bibliography'], note: null };
  return dossier;
}
const index = (dossier: NewsDossier) => new Map([[dossier.candidate_id, dossier]]);

describe('news territory with a reviewed public source', () => {
  it('keeps every unreviewed news reference listable without deriving a location from discovery metadata', () => {
    const source: NewsReference = { ...reference, title: 'Una noticia en San Salvador, El Salvador', scope_hint: 'el_salvador', scope_terms: ['San Salvador', 'El Salvador'] };
    const item = newsTerritoryItem(source);
    expect(item.department).toBeNull();
    expect(item.reason).toBe('pending_review');
    expect(item.dossier.human_reviewed).toBe(false);
    expect(matchesNewsTerritory(item, '')).toBe(true);
    expect(matchesNewsTerritory(item, UNKNOWN_DEPARTMENT)).toBe(true);
    expect(matchesNewsTerritory(item, 'San Salvador')).toBe(false);
  });

  it('counts a reviewed Salvadoran news location independently of case type or relevance', () => {
    const dossier = approved();
    dossier.classification = { jurisdiction: 'SV', relevance: 'not_relevant', record_type: 'publication' };
    const item = newsTerritoryItem(reference, index(dossier));
    expect(item.department).toBe('San Salvador');
    expect(item.reason).toBe('located');
    expect(matchesNewsTerritory(item, 'San Salvador')).toBe(true);
    expect(matchesNewsTerritory(item, 'La Libertad')).toBe(false);
    expect(matchesNewsTerritory(item, UNKNOWN_DEPARTMENT)).toBe(false);
    expect(item.reference.record_type).toBe('news_reference');
  });

  it.each(['pending', 'not_reported', 'withheld', 'not_applicable'] as NewsDossierFieldStatus[])('does not map a department with status %s', status => {
    const dossier = approved();
    dossier.fields.department = { value: null, status, evidence_refs: [], note: 'Fixture: el departamento no está disponible públicamente.' };
    dossier.fields.municipality = { value: 'San Salvador', status: 'reported', evidence_refs: ['bibliography'], note: null };
    const item = newsTerritoryItem(reference, index(dossier));
    expect(item.department).toBeNull();
    expect(item.reason).toBe('department_unknown');
  });

  it.each([
    ['other', 'outside_el_salvador'], ['unclear', 'jurisdiction_unknown'],
  ] as const)('does not assign a Salvadoran department when jurisdiction is %s', (jurisdiction, reason) => {
    const dossier = approved();
    dossier.classification.jurisdiction = jurisdiction;
    const item = newsTerritoryItem(reference, index(dossier));
    expect(item.department).toBeNull();
    expect(item.reason).toBe(reason);
  });

  it.each(['Alcance nacional', 'San Salvador, El Salvador', 'Departamento por determinar', 'Guatemala'])('does not geocode arbitrary source text: %s', value => {
    const dossier = approved();
    dossier.fields.department.value = value;
    expect(newsTerritoryItem(reference, index(dossier)).reason).toBe('department_unknown');
  });

  it('requires bibliography binding and rejects an unapproved extraction passed as a public dossier', () => {
    const mismatched = approved();
    mismatched.publisher = 'Otro medio';
    expect(newsTerritoryItem(reference, index(mismatched)).reason).toBe('pending_review');
    const privateDraft = approved();
    privateDraft.human_reviewed = false;
    expect(() => newsTerritoryItem(reference, index(privateDraft))).toThrow();
  });

  it('separates counts and reasons without changing the source data or inferring from hints', () => {
    const references = Array.from({ length: 6 }, (_, offset): NewsReference => ({
      ...reference, id: String(offset).repeat(64), source_url: `${reference.source_url}?fixture=${offset}`,
    }));
    const dossiers = references.slice(0, 5).map(source => approved(source));
    dossiers[1].fields.department.value = 'Sonsonate';
    dossiers[2].classification.jurisdiction = 'other';
    dossiers[3].classification.jurisdiction = 'unclear';
    dossiers[4].fields.department = { value: null, status: 'not_reported', evidence_refs: [], note: null };
    const before = structuredClone({ references, dossiers });
    const items = buildNewsTerritoryItems(references, new Map(dossiers.map(dossier => [dossier.candidate_id, dossier])));
    const summary = summarizeNewsTerritory(items);
    expect(Object.keys(summary.counts)).toEqual(DEPARTMENTS.map(department => department.name));
    expect(summary.counts['San Salvador']).toBe(1);
    expect(summary.counts.Sonsonate).toBe(1);
    expect(summary.counts['La Unión']).toBe(0);
    expect(summary.located).toBe(2);
    expect(summary.unlocated).toBe(4);
    expect(summary.reasons).toEqual({ located: 2, pending_review: 1, jurisdiction_unknown: 1, outside_el_salvador: 1, department_unknown: 1 });
    expect(items.filter(item => matchesNewsTerritory(item, UNKNOWN_DEPARTMENT))).toHaveLength(4);
    expect({ references, dossiers }).toEqual(before);
  });
});
