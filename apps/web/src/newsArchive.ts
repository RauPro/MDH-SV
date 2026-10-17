import { isSelectedNewsReference } from '../../../shared/news-selection.mjs';
import { normalize, safeSource, type Publication } from './catalog';
import { validArchiveDate, type CollectionStatus, type ScopeHint, type NewsReference } from '../../../shared/news-archive.mjs';
export { parseNewsArchive, validArchiveDate } from '../../../shared/news-archive.mjs';
export type { CollectionStatus, ScopeHint, NewsReference, SourceMonth, ArchiveSource, NewsArchiveData } from '../../../shared/news-archive.mjs';

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
  return records.filter(record => isSelectedNewsReference(record) && (!filters.scope || record.scope_hint === filters.scope) && (!filters.publisher || record.publisher === filters.publisher) && (!filters.topic || record.topics.includes(filters.topic)) && (!filters.from || record.published_date >= filters.from) && (!filters.to || record.published_date <= filters.to) && terms.every(term => normalize([record.title, record.publisher, ...record.topics.map(topicLabel)].join(' ')).includes(term)));
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
