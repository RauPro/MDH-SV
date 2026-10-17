export type { EvidenceField, Dossier, Publication, Catalog } from '../../../shared/catalog.mjs';
export { parseCatalog, validDate, safeSource } from '../../../shared/catalog.mjs';
import type { Publication } from '../../../shared/catalog.mjs';

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
