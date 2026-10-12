import { describe, expect, it } from 'vitest';
import { assertSelectedNewsArchive, isSelectedNewsReference, validateNewsSelection } from '../../../shared/news-selection.mjs';
import fixture from './__fixtures__/newsArchive.json';
import { filterNews, parseNewsArchive } from './newsArchive';

const auto = () => structuredClone(fixture.records[0].selection);
const human = () => ({ method: 'editorial_scope', review_status: 'editorially_reviewed', human_reviewed: true, jurisdiction: 'SV', relevance: 'relevant', reviewed_by: 'editor', reviewed_at: '2026-10-10' });

describe('public news selection boundary', () => {
  it('requires a grounded selection and records candidate versus published counts', () => {
    expect(validateNewsSelection(auto())).toEqual(auto());
    expect(validateNewsSelection({ ...auto(), prompt_version: 'mdh-news-scope-1' })).toMatchObject({ prompt_version: 'mdh-news-scope-1' });
    expect(validateNewsSelection(human())).toEqual(human());
    expect(assertSelectedNewsArchive(fixture)).toBe(fixture);
    expect(parseNewsArchive(fixture).records).toHaveLength(3);
    expect(fixture.policy.selection_gate.candidates).toBe(12);
  });
  it.each([
    { jurisdiction: 'other' }, { jurisdiction: 'unclear' }, { relevance: 'not_relevant' }, { relevance: 'unclear' },
    { method: 'title_keywords' }, { human_reviewed: true }, { review_status: 'editorially_reviewed' },
    { classified_at: '2026-10-10T10:00:00' }, { classified_at: '2026-02-30T10:00:00Z' },
    { model: 'unknown' }, { model: `gemini-${'a'.repeat(91)}` }, { prompt_version: 8 }, { prompt_version: 'unknown' },
    { prompt_version: 'mdh-universal-news-grounded-5' }, { prompt_version: 'mdh-universal-news-grounded-7-field-privacy' }, { prompt_version: 'mdh-universal-news-grounded-9' },
    { private_article: 'PRIVATE_VALUE' },
  ])('rejects an invalid or misleading automatic selection: %j', patch => {
    expect(() => validateNewsSelection({ ...auto(), ...patch })).toThrow();
  });
  it.each([
    { content_sha256: 'bad' }, { jurisdiction_paragraph_ids: [] }, { relevance_paragraph_ids: [] },
    { jurisdiction_paragraph_ids: ['p1', 'p1'] }, { relevance_paragraph_ids: ['p1000'] },
    { relevance_paragraph_ids: ['p0'] }, { relevance_paragraph_ids: Array.from({ length: 26 }, (_, index) => `p${index + 1}`) },
    { quote: 'PRIVATE_QUOTE' },
  ])('rejects missing, invalid or private evidence: %j', patch => {
    const selection = auto();
    expect(() => validateNewsSelection({ ...selection, evidence: { ...selection.evidence, ...patch } })).toThrow();
  });
  it.each([{ human_reviewed: false }, { reviewed_by: 'invalid login' }, { reviewed_at: '2026-02-30' }, { evidence: auto().evidence }, { classified_at: '2026-10-10T10:00:00Z' }])('rejects invalid editorial decisions: %j', patch => {
    expect(() => validateNewsSelection({ ...human(), ...patch })).toThrow();
  });
  it('rejects archive records with only title or scope hints, even if they sound relevant', () => {
    const archive = structuredClone(fixture);
    const { selection: _, ...withoutSelection } = archive.records[0];
    const raw = { ...archive, records: [{ ...withoutSelection, title: 'Derechos humanos en El Salvador', scope_hint: 'el_salvador' }, ...archive.records.slice(1)] };
    expect(() => assertSelectedNewsArchive(raw)).toThrow();
    expect(() => parseNewsArchive(raw)).toThrow();
    expect(isSelectedNewsReference(withoutSelection)).toBe(false);
    expect(filterNews(raw.records as unknown as ReturnType<typeof parseNewsArchive>['records'], { query: '', publisher: '', topic: '', scope: '', from: '', to: '' })).toHaveLength(2);
  });
  it.each([{ selected: 2 }, { selected: '3' }, { candidates: 2 }, { scope: 'all_news' }, { version: 2 }, { extra: true }])('rejects false selection gate metadata: %j', patch => {
    expect(() => assertSelectedNewsArchive({ ...fixture, policy: { ...fixture.policy, selection_gate: { ...fixture.policy.selection_gate, ...patch } } })).toThrow();
  });
  it('rejects an archive without the public gate and honors later human scope decisions', () => {
    const { selection_gate: _, ...policy } = fixture.policy;
    expect(() => assertSelectedNewsArchive({ ...fixture, policy })).toThrow();
    expect(isSelectedNewsReference(fixture.records[0], { human_reviewed: true, classification: { jurisdiction: 'other', relevance: 'relevant' } })).toBe(false);
    expect(isSelectedNewsReference(fixture.records[0], { human_reviewed: true, classification: { jurisdiction: 'SV', relevance: 'not_relevant' } })).toBe(false);
    expect(isSelectedNewsReference(fixture.records[0], { human_reviewed: true, classification: { jurisdiction: 'SV', relevance: 'unclear' } })).toBe(false);
    expect(isSelectedNewsReference(fixture.records[0], { human_reviewed: true, classification: { jurisdiction: 'SV', relevance: 'relevant' } })).toBe(true);
  });
});
