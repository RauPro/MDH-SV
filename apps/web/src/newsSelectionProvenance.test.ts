import { describe, expect, it } from 'vitest';
import { assertSelectionProvenance } from '../scripts/selection-provenance.mjs';
import { pendingNewsDossier } from '../../../shared/news-dossier.mjs';
import fixture from './__fixtures__/newsArchive.json';

const setup = () => {
  const reference = structuredClone(fixture.records[0]);
  const archive = { ...structuredClone(fixture), records: [reference], coverage: { ...fixture.coverage, records: 1 },
    policy: { ...fixture.policy, selection_gate: { ...fixture.policy.selection_gate, selected: 1 } } };
  const manifest = { schema_version: 1, generated_at: '2026-10-10T11:00:00Z', selections: [{
    candidate_id: reference.id, source_url: reference.source_url, publisher: reference.publisher, published_date: reference.published_date,
    selection: structuredClone(reference.selection),
  }] };
  const approved = pendingNewsDossier(reference);
  approved.extraction_status = 'processed'; approved.human_reviewed = true;
  approved.reviewed_by = 'editor'; approved.reviewed_at = '2026-10-10';
  approved.review_notes = 'Fixture: revisión editorial completa de pertinencia y privacidad.';
  approved.classification = { jurisdiction: 'SV', relevance: 'relevant', record_type: null };
  const human = { method: 'editorial_scope', review_status: 'editorially_reviewed', human_reviewed: true, jurisdiction: 'SV', relevance: 'relevant', reviewed_by: 'editor', reviewed_at: '2026-10-10' };
  return { archive, manifest, approved, human };
};

describe('build-time selection provenance', () => {
  it('accepts an inline automatic selection only when every provenance field and identity matches the manifest', () => {
    const { archive, manifest } = setup();
    expect(assertSelectionProvenance(archive, manifest)).toBe(archive);
    archive.records[0].selection.model = 'gemini-another-model';
    expect(() => assertSelectionProvenance(archive, manifest)).toThrow();
  });
  it.each(['source_url', 'publisher', 'published_date', 'candidate_id'])('rejects selection metadata bound to another reference: %s', field => {
    const { archive, manifest } = setup();
    Object.assign(manifest.selections[0], { [field]: field === 'published_date' ? '2026-10-01' : field === 'candidate_id' ? 'b'.repeat(64) : field === 'source_url' ? 'https://www.diariocolatino.com/other-fixture/' : 'Otro medio' });
    expect(() => assertSelectionProvenance(archive, manifest)).toThrow();
  });
  it.each(['duplicate', 'unknown_key', 'missing', 'bad_timestamp', 'private_evidence'])('rejects a malformed or incomplete manifest: %s', kind => {
    const { archive, manifest } = setup();
    if (kind === 'duplicate') manifest.selections.push(structuredClone(manifest.selections[0]));
    if (kind === 'unknown_key') Object.assign(manifest, { unapproved: [] });
    if (kind === 'missing') manifest.selections = [];
    if (kind === 'bad_timestamp') manifest.generated_at = '2026-10-10T09:00:00Z';
    if (kind === 'private_evidence') Object.assign(manifest.selections[0].selection.evidence, { quote: 'PRIVATE_VALUE' });
    expect(() => assertSelectionProvenance(archive, manifest)).toThrow();
  });
  it('rejects an old, matching manifest and inline record whose prompt used the superseded scope rules', () => {
    const { archive, manifest } = setup();
    archive.records[0].selection.prompt_version = manifest.selections[0].selection.prompt_version = 'mdh-universal-news-grounded-7-field-privacy';
    expect(() => assertSelectionProvenance(archive, manifest)).toThrow();
  });
  it('requires real positive human approval rather than a human-looking inline selection or manifest entry', () => {
    const { archive, manifest, approved, human } = setup();
    const humanArchive = { ...archive, records: [{ ...archive.records[0], selection: human }] };
    const approvals = new Map([[approved.candidate_id, approved]]);
    expect(() => assertSelectionProvenance(humanArchive, manifest)).toThrow();
    expect(assertSelectionProvenance(humanArchive, manifest, approvals)).toBe(humanArchive);
    expect(() => assertSelectionProvenance(humanArchive, { ...manifest, selections: [{ ...manifest.selections[0], selection: human }] }, approvals)).toThrow();
    expect(() => assertSelectionProvenance(archive, manifest, approvals)).toThrow();
  });
  it.each([{ reviewed_by: 'another-editor' }, { reviewed_at: '2026-10-11' }])('binds human identity and date to the approved source: %j', patch => {
    const { archive, manifest, approved, human } = setup();
    const humanArchive = { ...archive, records: [{ ...archive.records[0], selection: { ...human, ...patch } }] };
    expect(() => assertSelectionProvenance(humanArchive, manifest, new Map([[approved.candidate_id, approved]]))).toThrow();
  });
  it('uses all approved decisions so filtering negative dossiers from the public overlay cannot republish a reference', () => {
    const { archive, manifest, approved } = setup();
    approved.classification.relevance = 'not_relevant';
    const allApproved = new Map([[approved.candidate_id, approved]]);
    expect(() => assertSelectionProvenance(archive, manifest, allApproved)).toThrow();
    const excluded = { ...archive, records: [], coverage: { ...archive.coverage, records: 0 }, policy: { ...archive.policy, selection_gate: { ...archive.policy.selection_gate, selected: 0 } } };
    // The superseded positive entry may remain in the manifest as long as no public reference uses it.
    expect(assertSelectionProvenance(excluded, manifest, allApproved)).toBe(excluded);
  });
});
