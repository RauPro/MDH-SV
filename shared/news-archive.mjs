// The selected public bibliography, including every metadata object returned by
// the API. Unknown keys are rejected before records or metadata can be spread.
import { NEWS_SOURCES } from './source-registry.mjs';
import { assertSelectedNewsArchive, validateNewsSelection } from './news-selection.mjs';
import { canonicalNewsSource, sourceIdentityMatches } from './news-identity.mjs';

const object = value => Boolean(value && typeof value === 'object' && !Array.isArray(value));
const exact = (value, required, optional = []) => object(value)
  && required.every(key => Object.hasOwn(value, key))
  && Object.keys(value).every(key => required.includes(key) || optional.includes(key));
const text = value => typeof value === 'string' && Boolean(value.trim());
const count = value => Number.isSafeInteger(value) && value >= 0;
const status = value => ['enumerated', 'partial', 'failed', 'not_started'].includes(value);
const month = value => typeof value === 'string' && /^(?!0000)\d{4}-(0[1-9]|1[0-2])$/.test(value);
const strings = value => Array.isArray(value) && value.every(text);
const fail = () => { throw new Error('Archivo de noticias inválido o no compatible'); };
export const validArchiveDate = value => typeof value === 'string' && !value.startsWith('0000-')
  && /^\d{4}-\d{2}-\d{2}$/.test(value) && Number.isFinite(Date.parse(value))
  && new Date(value).toISOString().slice(0, 10) === value;
const instant = value => typeof value === 'string'
  && /^\d{4}-\d{2}-\d{2}T(?:[01]\d|2[0-3]):[0-5]\d:[0-5]\d(?:\.\d{1,6})?(?:Z|[+-](?:[01]\d|2[0-3]):[0-5]\d)$/.test(value)
  && validArchiveDate(value.slice(0, 10)) && Number.isFinite(Date.parse(value));
const topics = new Set(['detencion_y_debido_proceso', 'condiciones_de_reclusion', 'libertad_de_expresion',
  'defensores_y_sociedad_civil', 'violencia_y_desapariciones', 'violencia_sexual_y_discriminacion',
  'derechos_sociales_y_ambientales', 'derechos_humanos']);
const referenceKeys = ['id', 'title', 'title_truncated', 'publisher', 'source_id', 'source_url', 'published_date',
  'review_status', 'jurisdiction_status', 'scope_hint', 'scope_terms', 'record_type', 'topics', 'discovery'];

function referenceCheck(value) {
  if (!exact(value, referenceKeys, ['selection']) || !sourceIdentityMatches(value.id, value.source_url)
      || !text(value.title) || [...value.title].length > 180 || value.title.trim().split(/\s+/u).length > 24
      || typeof value.title_truncated !== 'boolean' || !validArchiveDate(value.published_date)
      || value.review_status !== 'not_reviewed' || value.jurisdiction_status !== 'not_reviewed'
      || value.record_type !== 'news_reference' || !Array.isArray(value.topics) || !value.topics.length
      || value.topics.some(item => !topics.has(item))
      || !['el_salvador', 'international_context', 'undetermined'].includes(value.scope_hint)
      || !strings(value.scope_terms) || value.scope_terms.some(item => [...item].length > 80)
      || ((value.scope_hint === 'undetermined') !== (value.scope_terms.length === 0))) fail();
  const source = canonicalNewsSource(value.source_url);
  if (!source || source.id !== value.source_id || source.name !== value.publisher) fail();
  const discovery = value.discovery;
  const html = source.kind === 'html_archive';
  if (!exact(discovery, ['method', 'endpoint', 'source_post_id', 'retrieved_at'])
      || discovery.method !== (html ? 'html_article_metadata' : 'wordpress_rest_title_screen')
      || discovery.endpoint !== source.endpoint || !instant(discovery.retrieved_at)
      || (!(html && discovery.source_post_id === null) && (!count(discovery.source_post_id) || discovery.source_post_id < 1))) fail();
  if (Object.hasOwn(value, 'selection')) validateNewsSelection(value.selection);
}

/** Candidate metadata can omit selection; the public archive below cannot. */
export function validateNewsReference(value) {
  referenceCheck(value);
  return structuredClone(value);
}

function metadata(data) {
  if (!exact(data.policy, ['selection_gate', 'record_type', 'review_status', 'selection_method', 'notice', 'coverage_limitations', 'rights'],
      ['screen_version', 'scope_version', 'scope_method'])
      || data.policy.record_type !== 'news_reference' || data.policy.review_status !== 'not_reviewed'
      || !text(data.policy.notice) || !text(data.policy.selection_method) || !text(data.policy.rights)
      || !strings(data.policy.coverage_limitations)) fail();
  for (const key of ['screen_version', 'scope_version']) {
    if (Object.hasOwn(data.policy, key) && !count(data.policy[key])) fail();
  }
  if (Object.hasOwn(data.policy, 'scope_method') && !text(data.policy.scope_method)) fail();
  if (!exact(data.paper, ['title', 'publication_date', 'publication_date_basis', 'url'],
      ['doi', 'conference_start_date', 'conference_end_date', 'crossref_bibliographic_date', 'crossref_registered_at', 'metadata_source_url', 'checked_at', 'date_note'])
      || !text(data.paper.title) || !validArchiveDate(data.paper.publication_date)
      || !text(data.paper.publication_date_basis) || data.paper.url !== 'https://ieeexplore.ieee.org/document/10933836') fail();
  for (const key of ['conference_start_date', 'conference_end_date', 'crossref_bibliographic_date', 'checked_at']) {
    if (Object.hasOwn(data.paper, key) && !validArchiveDate(data.paper[key])) fail();
  }
  for (const key of ['doi', 'metadata_source_url', 'date_note']) {
    if (Object.hasOwn(data.paper, key) && !text(data.paper[key])) fail();
  }
  if (Object.hasOwn(data.paper, 'crossref_registered_at') && !instant(data.paper.crossref_registered_at)) fail();
}

/** Validate the complete public projection before rendering or API output. */
export function parseNewsArchive(data) {
  if (!exact(data, ['schema_version', 'generated_at', 'policy', 'paper', 'coverage', 'records'])
      || data.schema_version !== 1 || !instant(data.generated_at) || !Array.isArray(data.records)) fail();
  metadata(data);
  assertSelectedNewsArchive(data);
  const coverage = data.coverage;
  if (!exact(coverage, ['start_date', 'end_date', 'timezone', 'status', 'records', 'sources', 'months'])
      || !validArchiveDate(coverage.start_date) || !validArchiveDate(coverage.end_date) || coverage.start_date > coverage.end_date
      || coverage.status !== 'partial' || coverage.timezone !== 'America/El_Salvador'
      || !count(coverage.records) || coverage.records !== data.records.length
      || !Array.isArray(coverage.sources) || !Array.isArray(coverage.months)) fail();
  const sourceIds = new Set();
  for (const source of coverage.sources) {
    if (!exact(source, ['id', 'publisher', 'endpoint', 'status', 'records', 'first_published_date', 'last_published_date', 'months'], ['collection'])
        || sourceIds.has(source.id) || !count(source.records) || !status(source.status) || !Array.isArray(source.months)
        || (source.first_published_date !== null && !validArchiveDate(source.first_published_date))
        || (source.last_published_date !== null && !validArchiveDate(source.last_published_date))) fail();
    const registered = NEWS_SOURCES.find(item => item.id === source.id);
    if (!registered || source.publisher !== registered.name || source.endpoint !== registered.endpoint) fail();
    if (source.collection !== undefined && (!exact(source.collection, ['method', 'phase', 'access', 'notice', 'last_attempt_at'])
        || source.collection.method !== 'html_article_metadata' || !text(source.collection.phase)
        || !['public', 'restricted'].includes(source.collection.access) || !text(source.collection.notice)
        || (source.collection.last_attempt_at !== null && !instant(source.collection.last_attempt_at)))) fail();
    sourceIds.add(source.id);
    const months = new Set();
    for (const item of source.months) {
      if (!exact(item, ['month', 'status', 'records', 'posts_scanned', 'source_total_posts', 'pages_fetched', 'error'],
          ['invalid_posts', 'last_attempt_at', 'screen_version'])
          || !month(item.month) || months.has(item.month) || !status(item.status) || !count(item.records)
          || !count(item.posts_scanned) || !count(item.pages_fetched)
          || (item.source_total_posts !== null && !count(item.source_total_posts))
          || (item.error !== null && typeof item.error !== 'string')
          || (Object.hasOwn(item, 'invalid_posts') && !count(item.invalid_posts))
          || (Object.hasOwn(item, 'screen_version') && item.screen_version !== null && !count(item.screen_version))
          || (Object.hasOwn(item, 'last_attempt_at') && item.last_attempt_at !== null && !instant(item.last_attempt_at))) fail();
      months.add(item.month);
    }
  }
  const seenMonths = new Set();
  for (const item of coverage.months) {
    if (!exact(item, ['month', 'start_date', 'end_date', 'records', 'sources'])
        || !month(item.month) || seenMonths.has(item.month) || !validArchiveDate(item.start_date) || !validArchiveDate(item.end_date)
        || item.start_date > item.end_date || !count(item.records) || !Array.isArray(item.sources)) fail();
    seenMonths.add(item.month);
    const seenSources = new Set();
    for (const source of item.sources) {
      if (!exact(source, ['source_id', 'status', 'records']) || !sourceIds.has(source.source_id)
          || seenSources.has(source.source_id) || !status(source.status) || !count(source.records)) fail();
      seenSources.add(source.source_id);
    }
  }
  const ids = new Set(), urls = new Set(), counts = new Map();
  for (const reference of data.records) {
    referenceCheck(reference);
    if (ids.has(reference.id) || urls.has(reference.source_url) || !sourceIds.has(reference.source_id)
        || reference.published_date < coverage.start_date || reference.published_date > coverage.end_date) fail();
    ids.add(reference.id);
    urls.add(reference.source_url);
    const key = reference.source_id + ':' + reference.published_date.slice(0, 7);
    counts.set(key, (counts.get(key) ?? 0) + 1);
  }
  if (coverage.sources.some(source => source.records !== data.records.filter(record => record.source_id === source.id).length)
      || coverage.months.some(item => item.records !== data.records.filter(record => record.published_date.startsWith(item.month)).length)) fail();
  for (const source of coverage.sources) {
    for (const item of source.months) if (item.records !== (counts.get(source.id + ':' + item.month) ?? 0)) fail();
  }
  for (const item of coverage.months) {
    for (const source of item.sources) if (source.records !== (counts.get(source.source_id + ':' + item.month) ?? 0)) fail();
  }
  const result = structuredClone(data);
  result.records.sort((a, b) => b.published_date.localeCompare(a.published_date) || a.id.localeCompare(b.id));
  return result;
}
