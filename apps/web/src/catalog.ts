export interface Publication {
  id: string;
  record_type: 'publication' | 'incident' | 'aggregate';
  incident_date: string | null;
  incident_date_end: string | null;
  date_precision: 'day' | 'unknown' | 'month_range';
  review_status: 'source_checked';
  human_reviewed: false;
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

const sourceHosts = new Set(['www.hrw.org', 'www.oas.org', 'www.amnesty.org', 'citizenlab.ca']);
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
      || r.review_status !== 'source_checked' || r.human_reviewed !== false
      || (r.incident_date !== null && !validDate(r.incident_date))
      || (r.incident_date_end !== null && !validDate(r.incident_date_end))
      || typeof r.source_record_key !== 'string' || typeof r.privacy_note !== 'string' || typeof r.penitentiary_related !== 'boolean'
      || !['title', 'source_title', 'publisher', 'summary', 'category', 'coverage', 'date_note', 'source_locator', 'source_check_method'].every(k => typeof r[k as keyof Publication] === 'string' && String(r[k as keyof Publication]).trim().length)) {
      throw new Error('Registro no apto para publicación');
    }
    ids.add(r.id);
    urls.add(r.source_record_key);
  }
  return { ...data, records: [...data.records].sort((a, b) => b.published_date.localeCompare(a.published_date) || a.id.localeCompare(b.id)) };
}

function validDate(value: unknown): value is string {
  return typeof value === 'string' && /^\d{4}-\d{2}-\d{2}$/.test(value) && Number.isFinite(Date.parse(value)) && new Date(value).toISOString().slice(0, 10) === value;
}

export const normalize = (value: string) => value.normalize('NFD').replace(/[\u0300-\u036f]/g, '').toLocaleLowerCase('es').trim();
export function filterRecords(records: Publication[], query: string, category: string, year: string) {
  const terms = normalize(query).split(/\s+/).filter(Boolean);
  return records.filter(r => (!category || r.category === category) && (!year || r.published_date.startsWith(year))
    && terms.every(term => normalize([r.title, r.summary, r.publisher, r.category, r.department || '', ...r.tags].join(' ')).includes(term)));
}
export function formatDate(value: string) {
  return new Intl.DateTimeFormat('es-SV', { day: 'numeric', month: 'long', year: 'numeric', timeZone: 'UTC' }).format(new Date(`${value}T00:00:00Z`));
}

export function citation(record: Publication) {
  return `${record.publisher} (${record.published_date}). ${record.source_title}. ${record.source_url} · ${record.source_locator} Ficha MDH-SV: ${record.id}. Revisión documental: ${record.source_checked_at}.`;
}

export function eventDate(record: Publication) {
  if (!record.incident_date) return 'No determinada';
  if (record.date_precision === 'month_range' && record.incident_date_end) {
    const month = (value: string) => new Intl.DateTimeFormat('es-SV', {month:'long',year:'numeric',timeZone:'UTC'}).format(new Date(value));
    return `${month(record.incident_date)} — ${month(record.incident_date_end)}`;
  }
  return formatDate(record.incident_date);
}
