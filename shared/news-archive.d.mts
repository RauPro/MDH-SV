import type { NewsSelection, NewsSelectionGate } from './news-selection.mjs';
export type CollectionStatus = 'enumerated' | 'partial' | 'failed' | 'not_started';
export type ScopeHint = 'el_salvador' | 'international_context' | 'undetermined';
export interface NewsReference {
  selection: NewsSelection;
  id: string;
  title: string;
  title_truncated: boolean;
  publisher: string;
  source_id: string;
  source_url: string;
  published_date: string;
  review_status: 'not_reviewed';
  jurisdiction_status: 'not_reviewed';
  scope_hint: ScopeHint;
  scope_terms: string[];
  record_type: 'news_reference';
  topics: string[];
  discovery: { method: 'wordpress_rest_title_screen' | 'html_article_metadata'; endpoint: string; source_post_id: number | null; retrieved_at: string };
}
export interface SourceMonth {
  month: string;
  status: CollectionStatus;
  records: number;
  posts_scanned: number;
  source_total_posts: number | null;
  pages_fetched: number;
  error: string | null;
  invalid_posts?: number;
  last_attempt_at?: string | null;
  screen_version?: number | null;
}
export interface ArchiveSource {
  id: string;
  publisher: string;
  endpoint: string;
  status: CollectionStatus;
  records: number;
  first_published_date: string | null;
  last_published_date: string | null;
  months: SourceMonth[];
  collection?: { method: 'html_article_metadata'; phase: string; access: 'public' | 'restricted'; notice: string; last_attempt_at: string | null };
}
export interface NewsArchiveData {
  schema_version: 1;
  generated_at: string;
  policy: { selection_gate: NewsSelectionGate; record_type: 'news_reference'; review_status: 'not_reviewed'; selection_method: string; notice: string; coverage_limitations: string[]; rights: string; screen_version?: number; scope_version?: number; scope_method?: string };
  paper: { title: string; publication_date: string; publication_date_basis: string; url: string; doi?: string; conference_start_date?: string; conference_end_date?: string; crossref_bibliographic_date?: string; crossref_registered_at?: string; metadata_source_url?: string; checked_at?: string; date_note?: string };
  coverage: {
    start_date: string;
    end_date: string;
    timezone: 'America/El_Salvador';
    status: 'partial';
    records: number;
    sources: ArchiveSource[];
    months: { month: string; start_date: string; end_date: string; records: number; sources: { source_id: string; status: CollectionStatus; records: number }[] }[];
  };
  records: NewsReference[];
}

export function validArchiveDate(value: unknown): value is string;
export type CandidateNewsReference = Omit<NewsReference, 'selection'> & { selection?: NewsSelection };
export function validateNewsReference(value: unknown): CandidateNewsReference;
export function parseNewsArchive(value: unknown): NewsArchiveData;
