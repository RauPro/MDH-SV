export type NewsDossierFieldName = 'summary' | 'category' | 'alleged_violation' | 'facts' | 'affected_persons' | 'country' | 'department' | 'municipality' | 'public_place' | 'event_date' | 'event_date_end' | 'event_time' | 'reported_actors' | 'institutional_sources' | 'vulnerable_groups' | 'themes' | 'hypothesis' | 'instrument';
export type NewsDossierFieldStatus = 'pending' | 'reported' | 'not_reported' | 'withheld' | 'not_applicable';
export interface NewsDossierField { value: string | string[] | null; status: NewsDossierFieldStatus; evidence_refs: string[]; note: string | null }
export interface NewsDossierAutomation {
  policy_version: 'auto-news-v1'; source_content_sha256: string; validated_at: string;
  method: 'gemini_evidence_review'; evaluator_model: string; corroboration: 'single_source';
}
export interface NewsDossier {
  schema_version: 1 | 2; kind: 'news_dossier'; candidate_id: string; source_url: string; publisher: string; source_title: string;
  published_date: string | null; extraction_status: 'pending' | 'processed' | 'source_unavailable' | 'error';
  classification: { jurisdiction: 'SV' | 'other' | 'unclear'; relevance: 'relevant' | 'not_relevant' | 'unclear'; record_type: 'incident' | 'aggregate' | 'publication' | null };
  fields: Record<NewsDossierFieldName, NewsDossierField>; date_precision: 'day' | 'unknown' | 'month_range' | 'year_range';
  evidence: { id: string; url: string; locator: string }[];
  human_reviewed: boolean; reviewed_by: string | null; reviewed_at: string | null; review_notes: string | null;
  automation?: NewsDossierAutomation;
}
export interface BibliographicReference { id: string; source_url: string; title: string; publisher: string; published_date: string | null; selection?: { method: string; evidence?: { content_sha256?: string } } }
export const NEWS_DOSSIER_FIELDS: readonly NewsDossierFieldName[];
export const NEWS_DOSSIER_LIST_FIELDS: readonly NewsDossierFieldName[];
export const NEWS_DOSSIER_PENDING: string;
export const NEWS_DOSSIER_PROTECTED_TITLE: string;
export function safeSource(value: unknown): boolean;
export function pendingNewsDossier(reference: BibliographicReference): NewsDossier;
export function validatePublishedNewsDossier(value: unknown): NewsDossier;
export function parsePublishedNewsDossiers(payload: unknown): Map<string, NewsDossier>;
export function validateDossierBindings(index: Map<string, NewsDossier>, references: BibliographicReference[]): Map<string, NewsDossier>;
export function newsDossierFor(reference: BibliographicReference, approved?: Map<string, NewsDossier>): NewsDossier;
