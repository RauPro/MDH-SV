import type { BibliographicReference } from './news-dossier.mjs';
export interface NewsLocation {
  candidate_id: string; source_url: string; publisher: string; published_date: string; department: string;
  review_status: 'automated_unreviewed'; human_reviewed: false; method: 'gemini_explicit_department' | 'gemini_municipality_reference';
  resolution?: { type: 'municipality_to_department'; municipality: string; municipality_kind: 'district' | 'municipality'; reference_version: string; source_url: string };
  extracted_at: string; source_checked_at: string; evidence: { paragraph_id: string; content_sha256: string };
}
export const NEWS_LOCATION_DEPARTMENTS: readonly string[];
export function validatePublishedNewsLocation(value: unknown): NewsLocation;
export function parsePublishedNewsLocations(payload: unknown): Map<string, NewsLocation>;
export function validateLocationBindings(index: Map<string, NewsLocation>, references: readonly BibliographicReference[]): Map<string, NewsLocation>;
export function newsLocationFor(reference: BibliographicReference, locations?: Map<string, NewsLocation>): NewsLocation | null;
