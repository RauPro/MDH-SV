import { normalize, safeSource, type Publication } from './catalog';

export type CollectionStatus = 'enumerated' | 'partial' | 'failed' | 'not_started';
export type ScopeHint = 'el_salvador' | 'international_context' | 'undetermined';
export interface NewsReference {
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
  discovery: { method: string; endpoint: string; source_post_id: number; retrieved_at: string };
}
export interface SourceMonth {
  month: string;
  status: CollectionStatus;
  records: number;
  posts_scanned: number;
  source_total_posts: number | null;
  pages_fetched: number;
  error: string | null;
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
}
export interface NewsArchiveData {
  schema_version: 1;
  generated_at: string;
  policy: { record_type: 'news_reference'; review_status: 'not_reviewed'; selection_method: string; notice: string; coverage_limitations: string[]; rights: string };
  paper: { title: string; publication_date: string; publication_date_basis: string; url: string };
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

const object = (value: unknown): value is Record<string, unknown> => Boolean(value && typeof value === 'object' && !Array.isArray(value));
const text = (value: unknown): value is string => typeof value === 'string' && value.trim().length > 0;
const count = (value: unknown): value is number => Number.isSafeInteger(value) && Number(value) >= 0;
const instant = (value: unknown): value is string => typeof value === 'string' && /^\d{4}-\d{2}-\d{2}T/.test(value) && Number.isFinite(Date.parse(value));
const status = (value: unknown): value is CollectionStatus => ['enumerated', 'partial', 'failed', 'not_started'].includes(String(value));
const month = (value: unknown): value is string => typeof value === 'string' && /^\d{4}-(0[1-9]|1[0-2])$/.test(value);
const safeUrl = (value: unknown): value is string => typeof value === 'string' && safeSource(value);
const strings = (value: unknown): value is string[] => Array.isArray(value) && value.every(text);
const fail = (): never => { throw new Error('Archivo de noticias inválido o no compatible'); };
export const validArchiveDate = (value: unknown): value is string => typeof value === 'string' && /^\d{4}-\d{2}-\d{2}$/.test(value) && Number.isFinite(Date.parse(value)) && new Date(value).toISOString().slice(0, 10) === value;

/** Validate the public metadata boundary before rendering any publisher links or counts. */
export function parseNewsArchive(value: unknown): NewsArchiveData {
  if (!object(value) || value.schema_version !== 1 || !instant(value.generated_at) || !object(value.coverage) || !object(value.policy) || !object(value.paper) || !Array.isArray(value.records)) fail();
  const data = value as unknown as NewsArchiveData;
  const coverage = data.coverage;
  if (!validArchiveDate(coverage.start_date) || !validArchiveDate(coverage.end_date) || coverage.start_date > coverage.end_date || coverage.status !== 'partial' || coverage.timezone !== 'America/El_Salvador' || !count(coverage.records) || coverage.records !== data.records.length || !Array.isArray(coverage.sources) || !Array.isArray(coverage.months)) fail();
  if (data.policy.record_type !== 'news_reference' || data.policy.review_status !== 'not_reviewed' || !text(data.policy.notice) || !text(data.policy.selection_method) || !text(data.policy.rights) || !strings(data.policy.coverage_limitations)) fail();
  if (!text(data.paper.title) || !validArchiveDate(data.paper.publication_date) || !text(data.paper.publication_date_basis) || data.paper.url !== 'https://ieeexplore.ieee.org/document/10933836') fail();
  const sourceIds = new Set<string>();
  for (const source of coverage.sources) {
    if (!object(source) || !text(source.id) || sourceIds.has(source.id) || !text(source.publisher) || !safeUrl(source.endpoint) || !count(source.records) || !status(source.status) || !Array.isArray(source.months) || (source.first_published_date !== null && !validArchiveDate(source.first_published_date)) || (source.last_published_date !== null && !validArchiveDate(source.last_published_date))) fail();
    sourceIds.add(source.id);
    const sourceMonths = new Set<string>();
    for (const item of source.months) {
      if (!object(item) || !month(item.month) || sourceMonths.has(item.month) || !status(item.status) || !count(item.records) || !count(item.posts_scanned) || !count(item.pages_fetched) || (item.source_total_posts !== null && !count(item.source_total_posts)) || (item.error !== null && typeof item.error !== 'string')) fail();
      sourceMonths.add(item.month);
    }
  }
  const seenMonths = new Set<string>();
  for (const item of coverage.months) {
    if (!object(item) || !month(item.month) || seenMonths.has(item.month) || !validArchiveDate(item.start_date) || !validArchiveDate(item.end_date) || item.start_date > item.end_date || !count(item.records) || !Array.isArray(item.sources)) fail();
    seenMonths.add(item.month);
    for (const source of item.sources) if (!object(source) || !sourceIds.has(source.source_id) || !status(source.status) || !count(source.records)) fail();
  }
  const ids = new Set<string>();
  const urls = new Set<string>();
  for (const reference of data.records) {
    if (!object(reference) || !/^[a-f0-9]{64}$/.test(reference.id) || ids.has(reference.id) || !text(reference.title) || reference.title.length > 180 || typeof reference.title_truncated !== 'boolean' || !text(reference.publisher) || !sourceIds.has(reference.source_id) || !safeUrl(reference.source_url) || urls.has(reference.source_url) || !validArchiveDate(reference.published_date) || reference.published_date < coverage.start_date || reference.published_date > coverage.end_date || reference.review_status !== 'not_reviewed' || reference.jurisdiction_status !== 'not_reviewed' || reference.record_type !== 'news_reference' || !strings(reference.topics) || !reference.topics.length || !object(reference.discovery) || !text(reference.discovery.method) || !safeUrl(reference.discovery.endpoint) || !count(reference.discovery.source_post_id) || !instant(reference.discovery.retrieved_at)) fail();
    const source = coverage.sources.find(source => source.id === reference.source_id)!;
    if (!['el_salvador', 'international_context', 'undetermined'].includes(reference.scope_hint) || !strings(reference.scope_terms)) fail();
    if (source.publisher !== reference.publisher || source.endpoint !== reference.discovery.endpoint || new URL(source.endpoint).hostname.replace(/^www\./, '') !== new URL(reference.source_url).hostname.replace(/^www\./, '')) fail();
    ids.add(reference.id);
    urls.add(reference.source_url);
  }
  if (coverage.sources.some(source => source.records !== data.records.filter(record => record.source_id === source.id).length) || coverage.months.some(item => item.records !== data.records.filter(record => record.published_date.startsWith(item.month)).length)) fail();
  return { ...data, records: [...data.records].sort((a, b) => b.published_date.localeCompare(a.published_date) || a.id.localeCompare(b.id)) };
}

export const topicLabels: Record<string, string> = {
  detencion_y_debido_proceso: 'Detención y debido proceso',
  condiciones_de_reclusion: 'Condiciones de reclusión',
  libertad_de_expresion: 'Libertad de expresión',
  defensores_y_sociedad_civil: 'Defensores y sociedad civil',
  violencia_y_desapariciones: 'Violencia y desapariciones',
  violencia_sexual_y_discriminacion: 'Violencia sexual y discriminación',
  derechos_sociales_y_ambientales: 'Derechos sociales y ambientales',
  derechos_humanos: 'Derechos humanos',
};
export const topicLabel = (topic: string) => topicLabels[topic] ?? topic.replaceAll('_', ' ');
export const scopeLabels: Record<ScopeHint, string> = { el_salvador: 'Referencia a El Salvador', international_context: 'Contexto internacional', undetermined: 'Por determinar' };
export const collectionStatusLabel = (value: CollectionStatus) => ({ enumerated: 'API recorrida', partial: 'Recorrido parcial', failed: 'Consulta fallida', not_started: 'Sin consultar' })[value];
export const archiveInstant = (value: string) => new Intl.DateTimeFormat('es-SV', { dateStyle: 'medium', timeStyle: 'short', timeZone: 'America/El_Salvador' }).format(new Date(value));
export const archiveMonth = (value: string) => new Intl.DateTimeFormat('es-SV', { month: 'long', year: 'numeric', timeZone: 'UTC' }).format(new Date(`${value}-01T00:00:00Z`));

export interface NewsFilters { query: string; publisher: string; topic: string; from: string; to: string; scope: string }
export function invalidArchiveRange(from: string, to: string) {
  return Boolean((from && !validArchiveDate(from)) || (to && !validArchiveDate(to)) || (from && to && from > to));
}
export function filterNews(records: NewsReference[], filters: NewsFilters) {
  if (invalidArchiveRange(filters.from, filters.to)) return [];
  const terms = normalize(filters.query).split(/\s+/).filter(Boolean);
  return records.filter(record => (!filters.scope || record.scope_hint === filters.scope) && (!filters.publisher || record.publisher === filters.publisher) && (!filters.topic || record.topics.includes(filters.topic)) && (!filters.from || record.published_date >= filters.from) && (!filters.to || record.published_date <= filters.to) && terms.every(term => normalize([record.title, record.publisher, ...record.topics.map(topicLabel)].join(' ')).includes(term)));
}

function canonicalSource(value: string) {
  if (!safeSource(value)) return null;
  const url = new URL(value);
  url.hash = '';
  url.hostname = url.hostname.replace(/^www\./, '');
  url.pathname = url.pathname.replace(/\/+$/, '') || '/';
  for (const key of [...url.searchParams.keys()]) if (key.startsWith('utm_') || ['fbclid', 'gclid'].includes(key)) url.searchParams.delete(key);
  url.searchParams.sort();
  return url.href;
}
export function relatedPublications(reference: NewsReference, records: Publication[]) {
  const target = canonicalSource(reference.source_url);
  return target ? records.filter(record => [record.source_url, ...record.related_sources.map(source => source.url)].some(url => canonicalSource(url) === target)) : [];
}
