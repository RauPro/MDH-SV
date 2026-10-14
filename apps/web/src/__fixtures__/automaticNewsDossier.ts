import { pendingNewsDossier, type BibliographicReference, type NewsDossier } from '../../../../shared/news-dossier.mjs';

export function automaticNewsDossier(reference: BibliographicReference): NewsDossier {
  const dossier = pendingNewsDossier(reference);
  dossier.schema_version = 2;
  dossier.extraction_status = 'processed';
  dossier.classification = { jurisdiction: 'SV', relevance: 'relevant', record_type: 'incident' };
  dossier.automation = {
    policy_version: 'auto-news-v1', method: 'gemini_evidence_review', evaluator_model: 'test-evaluator',
    source_content_sha256: reference.selection?.evidence?.content_sha256 ?? 'a'.repeat(64),
    validated_at: '2026-10-10T10:00:00Z', corroboration: 'single_source',
  };
  dossier.evidence = [{ id: 'source-p1', url: reference.source_url, locator: 'Párrafo 1 del texto analizado.' }];
  dossier.fields.summary = { status: 'reported', value: 'Resumen ficticio atribuido al medio para pruebas.', evidence_refs: ['source-p1'], note: null };
  return dossier;
}
