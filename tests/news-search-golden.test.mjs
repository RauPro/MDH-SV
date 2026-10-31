import { test } from 'node:test';
import assert from 'node:assert/strict';
import { parseNewsArchive } from '../shared/news-archive.mjs';
import { NEWS_DOSSIER_PROTECTED_TITLE, newsDossierFor, parsePublishedNewsDossiers } from '../shared/news-dossier.mjs';
import { isSelectedNewsReference } from '../shared/news-selection.mjs';
import { newsLocationFor, parsePublishedNewsLocations } from '../shared/news-location.mjs';
import { newsProcessingFor, parseNewsProcessing } from '../shared/news-processing.mjs';
import { loadNews, loadNewsDossiers, loadNewsLocations, loadNewsProcessing, newsCoverageDetail, newsSearchSchema, PUBLIC_URL, searchNews } from '../server/public-data.mjs';
import fixtureArchive from '../apps/web/src/__fixtures__/newsArchive.json' with { type: 'json' };

const normalize = value => String(value ?? '').normalize('NFD').replace(/[̀-ͯ]/g, '').toLowerCase();

// Reference implementation: the pre-optimization mechanics verbatim — filter
// and normalize on every call, then sort the filtered list. The golden check
// pins the prepared-snapshot path to exactly this behaviour.
function referenceSearch(filters, archive, snapshot, locations, processing, now = new Date()) {
  const normalizedFilters = newsSearchSchema.parse(filters);
  const eligible = archive.records.filter(reference => isSelectedNewsReference(reference, newsDossierFor(reference, snapshot.index))).map(reference => {
    const dossier = newsDossierFor(reference, snapshot.index);
    return dossier.schema_version === 2 && dossier.source_title === NEWS_DOSSIER_PROTECTED_TITLE
      ? { ...reference, title: NEWS_DOSSIER_PROTECTED_TITLE, title_truncated: false } : reference;
  });
  const filtered = eligible.filter(record => {
    const dossier = record.dossier;
    const dossierText = dossier ? [dossier.alleged_violation?.value, dossier.facts?.value, ...(dossier.affected_persons?.value ?? []), ...(dossier.reported_actors?.value ?? []), ...(dossier.vulnerable_groups?.value ?? []), ...(dossier.themes?.value ?? []), dossier.location?.public_place?.value] : [];
    if (normalizedFilters.q && !normalize([record.title, record.summary, record.publisher, record.category, ...(record.tags ?? []), ...(record.topics ?? []), ...dossierText].join(' ')).includes(normalize(normalizedFilters.q))) return false;
    for (const key of ['publisher', 'record_type', 'category', 'review_status', 'scope_hint']) {
      if (normalizedFilters[key] && normalize(record[key]) !== normalize(normalizedFilters[key])) return false;
    }
    if (normalizedFilters.department && normalize(record.department ?? 'unknown') !== normalize(normalizedFilters.department)) return false;
    if (normalizedFilters.date_from && (!record.published_date || record.published_date < normalizedFilters.date_from)) return false;
    if (normalizedFilters.date_to && (!record.published_date || record.published_date > normalizedFilters.date_to)) return false;
    if (normalizedFilters.incident_date_from && (!record.incident_date || (record.incident_date_end ?? record.incident_date) < normalizedFilters.incident_date_from)) return false;
    if (normalizedFilters.incident_date_to && (!record.incident_date || record.incident_date > normalizedFilters.incident_date_to)) return false;
    return true;
  }).sort((a, b) => (b.published_date ?? '').localeCompare(a.published_date ?? '') || a.id.localeCompare(b.id));
  const { limit = 25, offset = 0 } = normalizedFilters;
  const nextOffset = offset + limit < filtered.length ? offset + limit : null;
  const query = new URLSearchParams(Object.entries(normalizedFilters).map(([key, value]) => [key, String(value)]));
  query.set('limit', String(limit));
  if (nextOffset !== null) query.set('offset', String(nextOffset));
  const detail = newsCoverageDetail(normalizedFilters, now);
  const { months: _months, ...rest } = archive.coverage;
  const summary = { ...rest, sources: archive.coverage.sources.map(({ months: _m, ...source }) => source) };
  return {
    data: filtered.slice(offset, offset + limit).map(reference => {
      const dossier = newsDossierFor(reference, snapshot.index);
      return { ...reference, news_dossier: dossier,
        processing_status: newsProcessingFor(reference, processing.index, dossier),
        automatic_location: snapshot.status === 'available' && !dossier.human_reviewed && dossier.fields.department.status !== 'withheld'
          ? newsLocationFor(reference, locations.index) : null };
    }),
    pagination: { total: filtered.length, limit, offset, next_offset: nextOffset, next: nextOffset === null ? null : `${PUBLIC_URL}/api/v1/news?${query}` },
    meta: {
      api_version: '1', schema_version: archive.schema_version, updated_at: archive.updated_at ?? archive.generated_at ?? null,
      license: 'https://creativecommons.org/licenses/by/4.0/', attribution: 'MDH-SV; citar versión/fecha y las fuentes originales.',
      limitations: 'Cobertura parcial de reportes públicos; no es un censo ni una determinación judicial. Conservar fuentes, fechas y estado de revisión. No sumar noticias, informes y casos como víctimas.',
      selection_gate: archive.policy.selection_gate, public_scope: 'human_rights_el_salvador',
      coverage: detail === 'full' ? archive.coverage : summary, coverage_detail: detail,
      paper: archive.paper, policy: archive.policy,
      news_dossiers_status: snapshot.status,
      automatic_locations_status: locations.status,
      news_processing_status: processing.status,
      news_processing_updated_at: processing.generated_at,
      automatic_location_notice: 'Uno o más departamentos mencionados en la fuente o resueltos desde municipios/distritos mediante referencia administrativa oficial, sin revisión humana. Una noticia puede mencionar varios departamentos sin tener una ubicación principal. No confirma el lugar de una violación ni completa la ficha revisada. null no implica ausencia de ubicación en la fuente.',
      review_notice: 'Noticias seleccionadas por pertinencia en derechos humanos de El Salvador con evidencia de la fuente o revisión editorial; no constituyen casos validados. La clasificación automática de pertinencia permanece sin revisión humana. processing_status distingue pending, generated (generada pendiente de validación), automatically_published (publicada tras validación automática) y reviewed (revisión humana). unknown significa que el estado no está disponible, no que esté pendiente. Las fichas automáticas declaran human_reviewed=false y automation con evidencia vinculada a una versión de la fuente; single_source no implica corroboración independiente. Sólo se publican campos validados; generated por sí solo no concede publicación ni expone borradores. La API nunca consulta la cola privada.',
    },
  };
}

const QUERIES = [
  {}, { limit: 1 }, { limit: 100 }, { offset: 30, limit: 7 }, { offset: 5000, limit: 3 },
  { q: 'detención' }, { q: 'DETENCION' }, { q: 'mujer' }, { q: 'fiscalía' }, { q: 'nonexistentterm' },
  { publisher: 'El Faro' }, { scope_hint: 'el_salvador' }, { scope_hint: 'international_context' },
  { scope_hint: 'undetermined' }, { date_from: '2010-01-01', date_to: '2010-12-31' },
  { date_from: '2024-01-01' }, { date_to: '2015-06-30' },
  { q: 'gobierno', publisher: 'El Faro', limit: 5 },
  { coverage: 'summary', limit: 2 }, { coverage: 'full', limit: 2 },
];

function newsFixtures() {
  const archive = loadNews();
  const snapshot = loadNewsDossiers();
  const locations = loadNewsLocations();
  const processing = loadNewsProcessing();
  return { archive, snapshot, locations, processing };
}

for (const [index, filters] of QUERIES.entries()) {
  test(`golden: query ${index} ${JSON.stringify(filters)} keeps identical results and order`, () => {
    const { archive, snapshot, locations, processing } = newsFixtures();
    const expected = referenceSearch(filters, archive, snapshot, locations, processing);
    const actual = searchNews(filters, archive, undefined, undefined, undefined);
    assert.deepEqual(actual, expected);
    assert.equal(JSON.stringify(actual), JSON.stringify(expected));
  });
}

const snapshotOf = (parse, payload) => {
  try { return { index: parse(payload), status: 'available' }; }
  catch { return { index: new Map(), status: 'unavailable' }; }
};
const processingOf = payload => {
  try { return { index: parseNewsProcessing(payload), status: 'available', generated_at: payload.generated_at }; }
  catch { return { index: new Map(), status: 'unavailable', generated_at: null }; }
};

test('golden: an explicit archive follows the same path on the shared fixture', () => {
  const archive = parseNewsArchive(fixtureArchive);
  const snapshot = snapshotOf(parsePublishedNewsDossiers, { schema_version: 1, dossiers: [] });
  const locations = snapshotOf(parsePublishedNewsLocations, { schema_version: 1, locations: [] });
  const processing = processingOf({ schema_version: 1, entries: [] });
  const expected = referenceSearch({ limit: 3 }, archive, snapshot, locations, processing);
  const actual = searchNews({ limit: 3 }, archive, { schema_version: 1, dossiers: [] }, { schema_version: 1, locations: [] }, { schema_version: 1, entries: [] });
  assert.deepEqual(actual, expected);
});
