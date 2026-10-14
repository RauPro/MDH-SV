import type { BibliographicReference, NewsDossier } from './news-dossier.mjs';

export type NewsProcessingEntryStatus = 'pending' | 'generated' | 'source_unavailable' | 'error' | 'unknown';
export type NewsProcessingStatus = NewsProcessingEntryStatus | 'reviewed';
export interface NewsProcessingReference extends BibliographicReference {
  selection?: { method: string; evidence?: { content_sha256?: string } };
}
export interface NewsProcessingEntry {
  candidate_id: string; source_url: string; publisher: string; published_date: string;
  selection_content_sha256: string | null; status: NewsProcessingEntryStatus;
}
export interface NewsProcessingCollection { schema_version: 1; generated_at: string; entries: NewsProcessingEntry[] }
export const NEWS_PROCESSING_STATUSES: readonly NewsProcessingEntryStatus[];
export function validateNewsProcessingEntry(value: unknown): NewsProcessingEntry;
export function parseNewsProcessing(payload: unknown): Map<string, NewsProcessingEntry>;
export function processingMatchesReference(entry: NewsProcessingEntry, reference: NewsProcessingReference): boolean;
export function validateProcessingBindings(index: Map<string, NewsProcessingEntry>, references: readonly NewsProcessingReference[]): Map<string, NewsProcessingEntry>;
export function newsProcessingFor(reference: NewsProcessingReference, index?: Map<string, NewsProcessingEntry>, approvedDossier?: NewsDossier): NewsProcessingStatus;
