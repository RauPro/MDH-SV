import type { BibliographicReference } from './news-dossier.mjs';
interface NewsLocationBase {
  candidate_id: string; source_url: string; publisher: string; published_date: string;
  review_status: 'automated_unreviewed'; human_reviewed: false;
  extracted_at: string; source_checked_at: string;
}
interface MunicipalityResolution {
  type: 'municipality_to_department'; municipality: string; municipality_kind: 'district' | 'municipality'; reference_version: string; source_url: string;
}
export type NewsLocationMention = { department: string; paragraph_id: string } & (
  | { method: 'source_explicit_department'; resolution?: never }
  | { method: 'source_municipality_reference'; resolution: MunicipalityResolution }
);
type SingleNewsLocation = NewsLocationBase & { department: string; evidence: { paragraph_id: string; content_sha256: string }; mentions?: never } & (
  | { method: 'gemini_explicit_department'; resolution?: never; corroboration?: never; rule_version?: never }
  | { method: 'gemini_municipality_reference'; resolution: MunicipalityResolution; corroboration?: never; rule_version?: never }
  | { method: 'gemini_department_corroborated'; resolution?: never; rule_version?: never; corroboration: { strategy: 'same_article_explicit_department'; model_paragraph_id: string; explicit_paragraph_id: string; rule_version: 1 } }
  | { method: 'source_explicit_department'; rule_version: 1 | 2; resolution?: never; corroboration?: never }
  | { method: 'source_municipality_reference'; rule_version: 1 | 2; resolution: MunicipalityResolution; corroboration?: never }
);
export type NewsLocation = SingleNewsLocation | (NewsLocationBase & {
  method: 'source_administrative_mentions'; rule_version: 2; mentions: NewsLocationMention[];
  evidence: { content_sha256: string; paragraph_id?: never }; department?: never; resolution?: never; corroboration?: never;
});
export type NewsLocationCollection = { generated_at: string } & (
  | { schema_version: 1 | 2; locations: Exclude<SingleNewsLocation, { method: 'gemini_department_corroborated' | 'source_explicit_department' | 'source_municipality_reference' }>[] }
  | { schema_version: 3; locations: Exclude<SingleNewsLocation, { method: 'source_explicit_department' | 'source_municipality_reference' }>[] }
  | { schema_version: 4; locations: (Exclude<SingleNewsLocation, { method: 'source_explicit_department' | 'source_municipality_reference' }> | (Extract<SingleNewsLocation, { method: 'source_explicit_department' | 'source_municipality_reference' }> & { rule_version: 1 }))[] }
  | { schema_version: 5; locations: NewsLocation[] }
);
export const NEWS_LOCATION_DEPARTMENTS: readonly string[];
export function validatePublishedNewsLocation(value: unknown): NewsLocation;
export function parsePublishedNewsLocations(payload: unknown): Map<string, NewsLocation>;
export function validateLocationBindings(index: Map<string, NewsLocation>, references: readonly BibliographicReference[]): Map<string, NewsLocation>;
export function newsLocationFor(reference: BibliographicReference, locations?: Map<string, NewsLocation>): NewsLocation | null;
