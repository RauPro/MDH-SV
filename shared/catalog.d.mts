export interface EvidenceField<T = string | string[]> {
  value: T | null;
  status: 'reported' | 'not_reported' | 'withheld' | 'not_applicable';
  evidence_refs: string[];
  note: string | null;
}
export interface Dossier {
  schema_version: 1;
  alleged_violation: EvidenceField<string>;
  facts: EvidenceField<string>;
  affected_persons: EvidenceField<string[]>;
  location: {department: EvidenceField<string>; municipality: EvidenceField<string>; public_place: EvidenceField<string>};
  event_date: EvidenceField<string>;
  event_date_end: EvidenceField<string>;
  date_precision: 'day' | 'unknown' | 'month_range' | 'year_range';
  event_time: EvidenceField<string>;
  reported_actors: EvidenceField<string[]>;
  institutional_sources: EvidenceField<string[]>;
  vulnerable_groups: EvidenceField<string[]>;
  themes: EvidenceField<string[]>;
  hypothesis: EvidenceField<string>;
  instrument: EvidenceField<string[]>;
  evidence: {id: string; url: string; locator: string}[];
}
export interface Publication {
  dossier: Dossier;
  id: string;
  record_type: 'publication' | 'incident' | 'aggregate';
  incident_date: string | null;
  incident_date_end: string | null;
  date_precision: 'day' | 'unknown' | 'month_range' | 'year_range';
  review_status: 'source_checked' | 'editorially_reviewed';
  human_reviewed: boolean;
  reviewed_by: string | null;
  reviewed_at: string | null;
  review_notes: string | null;
  origin_candidate_id: string | null;
  related_sources: {url:string;title:string;published_date:string;locator:string}[];
  reported_actor: string | null;
  source_record_key: string;
  privacy_note: string;
  penitentiary_related: boolean;
  title: string;
  publisher: string;
  source_title: string;
  published_date: string;
  date_note: string;
  category: string;
  tags: string[];
  summary: string;
  source_url: string;
  source_locator: string;
  source_checked_at: string;
  source_check_method: string;
  coverage: string;
  department: string | null;
  geometry: null;
  language: 'es';
}

export interface Catalog {
  schema_version: 1;
  updated_at: string;
  scope: string;
  records: Publication[];
}

export function parseCatalog(value: unknown): Catalog;
export function validDate(value: unknown): value is string;
export function safeSource(value: unknown): boolean;
