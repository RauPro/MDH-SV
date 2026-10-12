import type { BibliographicReference } from './news-dossier.mjs';
interface NewsLocationBase {
  candidate_id: string; source_url: string; publisher: string; published_date: string; department: string;
  review_status: 'automated_unreviewed'; human_reviewed: false;
  extracted_at: string; source_checked_at: string; evidence: { paragraph_id: string; content_sha256: string };
}
export type NewsLocation = NewsLocationBase & (
  | { method: 'gemini_explicit_department'; resolution?: never; corroboration?: never }
  | { method: 'gemini_municipality_reference'; resolution: { type: 'municipality_to_department'; municipality: string; municipality_kind: 'district' | 'municipality'; reference_version: string; source_url: string }; corroboration?: never }
  | { method: 'gemini_department_corroborated'; resolution?: never; corroboration: { strategy: 'same_article_explicit_department'; model_paragraph_id: string; explicit_paragraph_id: string; rule_version: 1 } }
);
export type NewsLocationCollection = { generated_at: string } & (
  | { schema_version: 1 | 2; locations: Exclude<NewsLocation, { method: 'gemini_department_corroborated' }>[] }
  | { schema_version: 3; locations: NewsLocation[] }
);
export const NEWS_LOCATION_DEPARTMENTS: readonly string[];
export function validatePublishedNewsLocation(value: unknown): NewsLocation;
export function parsePublishedNewsLocations(payload: unknown): Map<string, NewsLocation>;
export function validateLocationBindings(index: Map<string, NewsLocation>, references: readonly BibliographicReference[]): Map<string, NewsLocation>;
export function newsLocationFor(reference: BibliographicReference, locations?: Map<string, NewsLocation>): NewsLocation | null;
