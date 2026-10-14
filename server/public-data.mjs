import { readFileSync } from 'node:fs';
import { z } from 'zod';
import { newsDossierFor, parsePublishedNewsDossiers } from '../shared/news-dossier.mjs';
import { assertSelectedNewsArchive, isSelectedNewsReference } from '../shared/news-selection.mjs';
import { newsLocationFor, parsePublishedNewsLocations } from '../shared/news-location.mjs';
import { newsProcessingFor, parseNewsProcessing } from '../shared/news-processing.mjs';

export const PUBLIC_URL = 'https://mdh-sv.vercel.app';
export const LICENSE = 'https://creativecommons.org/licenses/by/4.0/';
export const LIMITATION = 'Cobertura parcial de reportes públicos; no es un censo ni una determinación judicial. Conservar fuentes, fechas y estado de revisión. No sumar noticias, informes y casos como víctimas.';

export class ApiError extends Error {
  constructor(status, code, message) { super(message); this.status = status; this.code = code; }
}

const date = z.string().regex(/^\d{4}-\d{2}-\d{2}$/).refine(value => {
  const parsed = new Date(`${value}T00:00:00Z`);
  return !Number.isNaN(parsed.getTime()) && parsed.toISOString().slice(0, 10) === value;
}, 'Fecha inexistente');
const text = z.string().trim().min(1).max(200);
const page = {
  limit: z.coerce.number().int().min(1).max(100).default(25),
  offset: z.coerce.number().int().min(0).max(1000000).default(0),
};
export const commonFilters = {
  q: text.optional(), publisher: text.optional(),
  date_from: date.optional(), date_to: date.optional(),
};
export const recordFilters = {
  ...commonFilters,
  incident_date_from: date.optional(), incident_date_to: date.optional(),
  record_type: z.enum(['incident', 'aggregate', 'publication']).optional(),
  department: text.optional(), category: text.optional(),
  review_status: z.enum(['source_checked', 'editorially_reviewed']).optional(),
};
export const recordSearchSchema = z.object({ ...recordFilters, ...page }).strict();
export const newsSearchSchema = z.object({ ...commonFilters, scope_hint: z.enum(['el_salvador', 'international_context', 'undetermined']).optional(), ...page }).strict();
export const statsSchema = z.object(recordFilters).strict();
export const recordIdSchema = z.string().regex(/^[a-z0-9][a-z0-9-]{0,149}$/);

function validateRange(filters) {
  if (filters.date_from && filters.date_to && filters.date_from > filters.date_to) {
    throw new ApiError(400, 'invalid_query', 'date_from no puede ser posterior a date_to.');
  }
  if (filters.incident_date_from && filters.incident_date_to && filters.incident_date_from > filters.incident_date_to) {
    throw new ApiError(400, 'invalid_query', 'incident_date_from no puede ser posterior a incident_date_to.');
  }
  return filters;
}

export function parseFilters(params, schema) {
  const input = {};
  for (const [key, value] of params) {
    if (Object.hasOwn(input, key)) throw new ApiError(400, 'invalid_query', `Parámetro repetido: ${key}.`);
    // Null-prototype parsing is unnecessary here because inherited keys are rejected by the strict schema.
    Object.defineProperty(input, key, { value, enumerable: true, configurable: true });
  }
  const parsed = schema.safeParse(input);
  if (!parsed.success) throw new ApiError(400, 'invalid_query', 'Filtros inválidos. Consulte /api/v1/openapi.json.');
  return validateRange(parsed.data);
}

let catalogCache;
export function loadCatalog() {
  if (!catalogCache) {
    const catalog = JSON.parse(readFileSync(new URL('../apps/web/public/data/catalog.json', import.meta.url), 'utf8'));
    if (!Array.isArray(catalog.records)) throw new Error('Invalid public catalog');
    // Defense in depth: never expose collection candidates through the documented-case API.
    catalogCache = { ...catalog, records: catalog.records.filter(record => ['source_checked', 'editorially_reviewed'].includes(record.review_status)) };
  }
  return catalogCache;
}

let newsCache;
export function loadNews() {
  if (!newsCache) {
    try {
      const news = JSON.parse(readFileSync(new URL('../apps/web/public/archive/news.json', import.meta.url), 'utf8'));
      assertSelectedNewsArchive(news);
      newsCache = news;
    } catch (error) {
      if (error.code === 'ENOENT') throw new ApiError(503, 'archive_unavailable', 'El archivo de noticias no está disponible en este despliegue.');
      throw new ApiError(503, 'archive_selection_invalid', 'No se pudo verificar la selección pública de noticias.');
    }
  }
  return newsCache;
}

function dossierSnapshot(payload) {
  try { return { index: parsePublishedNewsDossiers(payload), status: 'available' }; }
  catch { return { index: new Map(), status: 'unavailable' }; }
}
let publicNewsDossiers;
export function loadNewsDossiers() {
  if (!publicNewsDossiers) {
    try {
      const payload = JSON.parse(readFileSync(new URL('../apps/web/public/archive/dossiers.json', import.meta.url), 'utf8'));
      publicNewsDossiers = dossierSnapshot(payload);
    } catch { publicNewsDossiers = { index: new Map(), status: 'unavailable' }; }
    if (publicNewsDossiers.status === 'unavailable') console.error('Public news dossier snapshot unavailable; returning pending fields only.');
  }
  return publicNewsDossiers;
}

function locationSnapshot(payload) {
  try { return { index: parsePublishedNewsLocations(payload), status: 'available' }; }
  catch { return { index: new Map(), status: 'unavailable' }; }
}
let publicNewsLocations;
export function loadNewsLocations() {
  if (!publicNewsLocations) {
    try { publicNewsLocations = locationSnapshot(JSON.parse(readFileSync(new URL('../apps/web/public/archive/locations.json', import.meta.url), 'utf8'))); }
    catch { publicNewsLocations = { index: new Map(), status: 'unavailable' }; }
  }
  return publicNewsLocations;
}

function processingSnapshot(payload) {
  try { return { index: parseNewsProcessing(payload), status: 'available', generated_at: payload.generated_at }; }
  catch { return { index: new Map(), status: 'unavailable', generated_at: null }; }
}
let publicNewsProcessing;
export function loadNewsProcessing() {
  if (!publicNewsProcessing) {
    try { publicNewsProcessing = processingSnapshot(JSON.parse(readFileSync(new URL('../apps/web/public/archive/processing.json', import.meta.url), 'utf8'))); }
    catch { publicNewsProcessing = { index: new Map(), status: 'unavailable', generated_at: null }; }
  }
  return publicNewsProcessing;
}

const normalize = value => String(value ?? '').normalize('NFD').replace(/[\u0300-\u036f]/g, '').toLowerCase();
function matching(records, filters) {
  validateRange(filters);
  return records.filter(record => {
    const dossier = record.dossier;
    const dossierText = dossier ? [dossier.alleged_violation?.value, dossier.facts?.value, ...(dossier.affected_persons?.value ?? []), ...(dossier.reported_actors?.value ?? []), ...(dossier.vulnerable_groups?.value ?? []), ...(dossier.themes?.value ?? []), dossier.location?.public_place?.value] : [];
    if (filters.q && !normalize([record.title, record.summary, record.publisher, record.category, ...(record.tags ?? []), ...(record.topics ?? []), ...dossierText].join(' ')).includes(normalize(filters.q))) return false;
    for (const key of ['publisher', 'record_type', 'category', 'review_status', 'scope_hint']) {
      if (filters[key] && normalize(record[key]) !== normalize(filters[key])) return false;
    }
    if (filters.department && normalize(record.department ?? 'unknown') !== normalize(filters.department)) return false;
    if (filters.date_from && (!record.published_date || record.published_date < filters.date_from)) return false;
    if (filters.date_to && (!record.published_date || record.published_date > filters.date_to)) return false;
    // Event ranges overlap inclusively; unknown dates are excluded, never replaced with publication dates.
    if (filters.incident_date_from && (!record.incident_date || (record.incident_date_end ?? record.incident_date) < filters.incident_date_from)) return false;
    if (filters.incident_date_to && (!record.incident_date || record.incident_date > filters.incident_date_to)) return false;
    return true;
  }).sort((a, b) => (b.published_date ?? '').localeCompare(a.published_date ?? '') || a.id.localeCompare(b.id));
}

function metadata(catalog) {
  return { api_version: '1', schema_version: catalog.schema_version, updated_at: catalog.updated_at ?? catalog.generated_at ?? null, license: LICENSE, attribution: 'MDH-SV; citar versión/fecha y las fuentes originales.', limitations: LIMITATION };
}

function paginated(records, filters, source, path) {
  const { limit = 25, offset = 0 } = filters;
  const filtered = matching(records, filters);
  const nextOffset = offset + limit < filtered.length ? offset + limit : null;
  const query = new URLSearchParams(Object.entries(filters).map(([key, value]) => [key, String(value)]));
  query.set('limit', String(limit));
  if (nextOffset !== null) query.set('offset', String(nextOffset));
  return {
    data: filtered.slice(offset, offset + limit),
    pagination: { total: filtered.length, limit, offset, next_offset: nextOffset, next: nextOffset === null ? null : `${PUBLIC_URL}${path}?${query}` },
    meta: metadata(source),
  };
}

export function searchRecords(filters = {}, catalog = loadCatalog()) {
  return paginated(catalog.records, recordSearchSchema.parse(filters), catalog, '/api/v1/records');
}
export function getRecord(id, catalog = loadCatalog()) {
  if (!recordIdSchema.safeParse(id).success) throw new ApiError(400, 'invalid_id', 'Identificador inválido.');
  const record = catalog.records.find(value => value.id === id);
  if (!record) throw new ApiError(404, 'record_not_found', 'Registro no encontrado.');
  return { data: record, meta: metadata(catalog) };
}
export function searchNews(filters = {}, archive = loadNews(), dossierPayload, locationPayload, processingPayload) {
  try { assertSelectedNewsArchive(archive); } catch { throw new ApiError(503, 'archive_selection_invalid', 'No se pudo verificar la selección pública de noticias.'); }
  const snapshot = dossierPayload === undefined ? loadNewsDossiers() : dossierSnapshot(dossierPayload);
  const eligible = archive.records.filter(reference => isSelectedNewsReference(reference, newsDossierFor(reference, snapshot.index)));
  const result = paginated(eligible, newsSearchSchema.parse(filters), archive, '/api/v1/news');
  const locations = locationPayload === undefined ? loadNewsLocations() : locationSnapshot(locationPayload);
  const processing = processingPayload === undefined ? loadNewsProcessing() : processingSnapshot(processingPayload);
  result.data = result.data.map(reference => {
    const dossier = newsDossierFor(reference, snapshot.index);
    return { ...reference, news_dossier: dossier,
      processing_status: newsProcessingFor(reference, processing.index, dossier),
      automatic_location: snapshot.status === 'available' && !dossier.human_reviewed ? newsLocationFor(reference, locations.index) : null };
  });
  result.meta = { ...result.meta, selection_gate: archive.policy.selection_gate, public_scope: 'human_rights_el_salvador', coverage: archive.coverage, paper: archive.paper, policy: archive.policy,
    news_dossiers_status: snapshot.status,
    automatic_locations_status: locations.status,
    news_processing_status: processing.status,
    news_processing_updated_at: processing.generated_at,
    automatic_location_notice: 'Uno o más departamentos mencionados en la fuente o resueltos desde municipios/distritos mediante referencia administrativa oficial, sin revisión humana. Una noticia puede mencionar varios departamentos sin tener una ubicación principal. No confirma el lugar de una violación ni completa la ficha revisada. null no implica ausencia de ubicación en la fuente.',
    review_notice: 'Noticias seleccionadas por pertinencia en derechos humanos de El Salvador con evidencia de la fuente o revisión editorial; no constituyen casos validados. La clasificación automática de pertinencia permanece sin revisión humana. processing_status distingue pending, generated (generada pendiente de validación), automatically_published (publicada tras validación automática) y reviewed (revisión humana). unknown significa que el estado no está disponible, no que esté pendiente. Las fichas automáticas declaran human_reviewed=false y automation con evidencia vinculada a una versión de la fuente; single_source no implica corroboración independiente. Sólo se publican campos validados; generated por sí solo no concede publicación ni expone borradores. La API nunca consulta la cola privada.' };
  return result;
}
export function getStats(filters = {}, catalog = loadCatalog()) {
  const records = matching(catalog.records, statsSchema.parse(filters));
  const counts = key => Object.fromEntries([...new Set(records.map(record => record[key] ?? 'unknown'))].sort().map(value => [value, records.filter(record => (record[key] ?? 'unknown') === value).length]));
  return { data: { total_records: records.length, by_record_type: counts('record_type'), by_department: counts('department'), by_category: counts('category'), by_review_status: counts('review_status'), human_reviewed: records.filter(record => record.human_reviewed === true).length }, meta: metadata(catalog) };
}
