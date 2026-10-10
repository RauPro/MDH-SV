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

const sourceHosts = new Set(['www.hrw.org', 'www.oas.org', 'www.amnesty.org', 'citizenlab.ca', 'www.corteidh.or.cr', 'spcommreports.ohchr.org', 'www.ohchr.org', 'www.diariocolatino.com', 'diariocolatino.com', 'gatoencerrado.news', 'www.revistafactum.com', 'revistafactum.com']);
export function safeSource(value: string): boolean {
  try {
    const url = new URL(value);
    return url.protocol === 'https:' && sourceHosts.has(url.hostname) && !url.username && !url.password && !url.port;
  } catch { return false; }
}

export function parseCatalog(value: unknown): Catalog {
  if (!value || typeof value !== 'object') throw new Error('Catálogo inválido');
  const data = value as Catalog;
  if (data.schema_version !== 1 || !Array.isArray(data.records) || !validDate(data.updated_at) || typeof data.scope !== 'string') {
    throw new Error('Formato de catálogo no compatible');
  }
  const ids = new Set<string>();
  const urls = new Set<string>();
  for (const r of data.records) {
    if (!r || !['publication', 'incident', 'aggregate'].includes(r.record_type) || !/^[a-z0-9]+(?:-[a-z0-9]+)*$/.test(r.id) || ids.has(r.id) || urls.has(r.source_record_key)
      || !safeSource(r.source_url) || !validDate(r.published_date) || !validDate(r.source_checked_at)
      || r.published_date > r.source_checked_at || r.source_checked_at > data.updated_at
      || !Array.isArray(r.tags) || !r.tags.every(t => typeof t === 'string')
      || r.geometry !== null || (r.department !== null && typeof r.department !== 'string')
      || typeof r.human_reviewed !== 'boolean'
      || (r.human_reviewed ? (r.review_status !== 'editorially_reviewed' || !r.reviewed_by || !validDate(r.reviewed_at) || (r.review_notes || '').length < 20) : (r.review_status !== 'source_checked' || Boolean(r.reviewed_by || r.reviewed_at || r.review_notes)))
      || (Boolean(r.origin_candidate_id) && !r.human_reviewed)
      || !Array.isArray(r.related_sources) || !r.related_sources.every(s => safeSource(s.url) && typeof s.title === 'string' && validDate(s.published_date) && typeof s.locator === 'string')
      || (r.incident_date !== null && !validDate(r.incident_date))
      || (r.incident_date_end !== null && !validDate(r.incident_date_end))
      || typeof r.source_record_key !== 'string' || typeof r.privacy_note !== 'string' || typeof r.penitentiary_related !== 'boolean'
      || !['title', 'source_title', 'publisher', 'summary', 'category', 'coverage', 'date_note', 'source_locator', 'source_check_method'].every(k => typeof r[k as keyof Publication] === 'string' && String(r[k as keyof Publication]).trim().length)) {
      throw new Error('Registro no apto para publicación');
    }
    validateDossier(r);
    ids.add(r.id);
    urls.add(r.source_record_key);
  }
  return { ...data, records: [...data.records].sort((a, b) => b.published_date.localeCompare(a.published_date) || a.id.localeCompare(b.id)) };
}

export function validDate(value: unknown): value is string {
  return typeof value === 'string' && /^\d{4}-\d{2}-\d{2}$/.test(value) && Number.isFinite(Date.parse(value)) && new Date(value).toISOString().slice(0, 10) === value;
}

export const normalize = (value: string) => value.normalize('NFD').replace(/[\u0300-\u036f]/g, '').toLocaleLowerCase('es').trim();
export function filterRecords(records: Publication[], query: string, category: string, year: string) {
  const terms = normalize(query).split(/\s+/).filter(Boolean);
  return records.filter(r => (!category || r.category === category) && (!year || r.published_date.startsWith(year))
    && terms.every(term => normalize([r.id, r.title, r.source_title, r.summary, r.publisher, r.category, r.department || '', JSON.stringify(r.dossier), ...r.tags].join(' ')).includes(term)));
}
export function formatDate(value: string) {
  return new Intl.DateTimeFormat('es-SV', { day: 'numeric', month: 'long', year: 'numeric', timeZone: 'UTC' }).format(new Date(`${value}T00:00:00Z`));
}

export function citation(record: Publication) {
  return `${record.publisher} (${record.published_date}). ${record.source_title}. ${record.source_url} · ${record.source_locator} Ficha MDH-SV: ${record.id}. Revisión documental: ${record.source_checked_at}.`;
}

export function eventDate(record: Publication) {
  if (!record.incident_date) return 'No determinada';
  if (record.date_precision === 'year_range') return `${record.incident_date.slice(0,4)} (precisión anual)`;
  if (record.date_precision === 'month_range' && record.incident_date_end) {
    const month = (value: string) => new Intl.DateTimeFormat('es-SV', {month:'long',year:'numeric',timeZone:'UTC'}).format(new Date(value));
    return `${month(record.incident_date)} — ${month(record.incident_date_end)}`;
  }
  return formatDate(record.incident_date);
}

function validateDossier(record: Publication) {
  const d = record.dossier;
  const fail = () => { throw new Error('Ficha sin evidencia estructurada válida'); };
  if (!d || d.schema_version !== 1 || !d.location || !Array.isArray(d.evidence) || !d.evidence.length) return fail();
  const sources = new Set([record.source_url, ...record.related_sources.map(s => s.url)]);
  const refs = new Set<string>();
  for (const e of d.evidence) {
    if (!e || typeof e.id !== 'string' || refs.has(e.id) || !sources.has(e.url) || !safeSource(e.url) || typeof e.locator !== 'string' || !e.locator.trim()) return fail();
    refs.add(e.id);
  }
  const strings = [d.alleged_violation, d.facts, d.location.department, d.location.municipality, d.location.public_place, d.event_date, d.event_date_end, d.event_time, d.hypothesis];
  const arrays = [d.affected_persons, d.reported_actors, d.institutional_sources, d.vulnerable_groups, d.themes, d.instrument];
  for (const [fields, isList] of [[strings, false], [arrays, true]] as const) {
    for (const f of fields) {
      if (!f || !['reported','not_reported','withheld','not_applicable'].includes(f.status) || !Array.isArray(f.evidence_refs) || f.evidence_refs.some(id => !refs.has(id)) || (f.note !== null && typeof f.note !== 'string')) return fail();
      if (f.status === 'reported') {
        if (!f.evidence_refs.length || (isList ? !Array.isArray(f.value) || !f.value.length || !f.value.every(v => typeof v === 'string' && v.trim()) : typeof f.value !== 'string' || !f.value.trim())) return fail();
      } else if (f.value !== null) return fail();
    }
  }
  if (d.event_date.value !== record.incident_date || d.event_date_end.value !== record.incident_date_end || d.date_precision !== record.date_precision || d.location.department.value !== record.department) return fail();
}
