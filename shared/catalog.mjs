// Public catalogue contract, used by the browser, API and publication checks.
// Candidate/private schemas are deliberately not valid catalogue records.
import { safeSource } from './news-dossier.mjs';
export { safeSource };

const object = value => value !== null && typeof value === 'object' && !Array.isArray(value);
const fail = () => { throw new Error('Catálogo público inválido o sin evidencia estructurada válida'); };
const departments = new Set(['Ahuachapán', 'Cabañas', 'Chalatenango', 'Cuscatlán', 'La Libertad', 'La Paz', 'La Unión', 'Morazán', 'San Miguel', 'San Salvador', 'San Vicente', 'Santa Ana', 'Sonsonate', 'Usulután']);
const scalarFields = ['alleged_violation', 'facts', 'event_date', 'event_date_end', 'event_time', 'hypothesis'];
const listFields = ['affected_persons', 'reported_actors', 'institutional_sources', 'vulnerable_groups', 'themes', 'instrument'];
const locationFields = ['department', 'municipality', 'public_place'];
const precision = ['day', 'unknown', 'month_range', 'year_range'];
const recordKeys = ['dossier', 'id', 'record_type', 'incident_date', 'incident_date_end', 'date_precision', 'review_status', 'human_reviewed', 'related_sources', 'reported_actor', 'source_record_key', 'privacy_note', 'penitentiary_related', 'title', 'publisher', 'source_title', 'published_date', 'date_note', 'category', 'tags', 'summary', 'source_url', 'source_locator', 'source_checked_at', 'source_check_method', 'coverage', 'department', 'geometry', 'language'];
const reviewDefaults = { reviewed_by: null, reviewed_at: null, review_notes: null, origin_candidate_id: null, related_sources: [] };

function shape(value, required, optional = {}) {
  if (!object(value) || required.some(key => !Object.hasOwn(value, key) && !Object.hasOwn(optional, key))
      || Object.keys(value).some(key => !required.includes(key) && !Object.hasOwn(optional, key))) fail();
  return { ...structuredClone(optional), ...value };
}

export const validDate = value => typeof value === 'string' && !value.startsWith('0000-')
  && /^\d{4}-\d{2}-\d{2}$/.test(value) && Number.isFinite(Date.parse(value))
  && new Date(value).toISOString().slice(0, 10) === value;

function text(value, minimum = 0, maximum = Infinity) {
  if (typeof value !== 'string') fail();
  const clean = value.trim();
  if ([...clean].length < minimum || [...clean].length > maximum) fail();
  return clean;
}

function primarySource(value) {
  const clean = text(value);
  // Compare the explicit authority too: WHATWG normalizes default ports and
  // percent-encoded hosts, while the reviewed Python boundary rejects them.
  const parts = /^https:\/\/([^/?#]+)([^?#]*)(?:\?[^#]*)?(?:#.*)?$/i.exec(clean);
  if (!parts || /[\u0000-\u0020\u007f\\]/.test(clean) || !safeSource(clean)
      || parts[1].toLowerCase() !== new URL(clean).hostname || parts[2].length < 5) fail();
  return clean;
}

function evidenceField(raw, list, refs, calendar = false) {
  const value = shape(raw, ['value', 'status'], { evidence_refs: [], note: null });
  if (!['reported', 'not_reported', 'withheld', 'not_applicable'].includes(value.status)
      || !Array.isArray(value.evidence_refs)) fail();
  value.evidence_refs = value.evidence_refs.map(item => text(item));
  if (new Set(value.evidence_refs).size !== value.evidence_refs.length
      || value.evidence_refs.some(id => !refs.has(id))) fail();
  if (value.note !== null) value.note = text(value.note);
  if (value.status === 'reported') {
    if (!value.evidence_refs.length) fail();
    if (list) {
      if (!Array.isArray(value.value) || !value.value.length) fail();
      value.value = value.value.map(item => text(item, 1));
    } else {
      value.value = text(value.value, 1);
      if (calendar && !validDate(value.value)) fail();
    }
  } else if (value.value !== null) fail();
  if (['withheld', 'not_applicable'].includes(value.status) && [...(value.note ?? '')].length < 10) fail();
  return value;
}

function dossier(raw, record) {
  const value = shape(raw, ['schema_version', ...scalarFields, ...listFields, 'location', 'date_precision', 'evidence']);
  if (value.schema_version !== 1 || !precision.includes(value.date_precision)
      || !Array.isArray(value.evidence) || !value.evidence.length) fail();
  const sources = new Set([record.source_url, ...record.related_sources.map(item => item.url)]);
  const refs = new Set();
  value.evidence = value.evidence.map(rawEvidence => {
    const item = shape(rawEvidence, ['id', 'url', 'locator']);
    item.id = text(item.id);
    item.url = primarySource(item.url);
    item.locator = text(item.locator, 10);
    if (!/^[a-z][a-z0-9-]*$/.test(item.id) || refs.has(item.id) || !sources.has(item.url)) fail();
    refs.add(item.id);
    return item;
  });
  for (const name of scalarFields) value[name] = evidenceField(value[name], false, refs, ['event_date', 'event_date_end'].includes(name));
  for (const name of listFields) value[name] = evidenceField(value[name], true, refs);
  value.location = shape(value.location, locationFields);
  for (const name of locationFields) value.location[name] = evidenceField(value.location[name], false, refs);
  if (value.event_date.value !== record.incident_date || value.event_date_end.value !== record.incident_date_end
      || value.date_precision !== record.date_precision || value.location.department.value !== record.department) fail();
  if (record.record_type === 'publication'
      && [value.event_date, value.event_date_end, value.event_time, ...Object.values(value.location)]
        .some(field => field.status !== 'not_applicable')) fail();
  return value;
}

function publication(raw, updatedAt, today) {
  const value = shape(raw, recordKeys, reviewDefaults);
  for (const [name, minimum, maximum] of [
    ['id', 1], ['title', 3, 300], ['publisher', 3], ['source_title', 3], ['date_note', 10], ['category', 3],
    ['summary', 20, 1000], ['source_locator', 10], ['source_check_method', 20], ['coverage', 0],
    ['source_record_key', 4], ['privacy_note', 10],
  ]) value[name] = text(value[name], minimum, maximum);
  for (const name of ['reported_actor', 'department', 'reviewed_by', 'review_notes', 'origin_candidate_id']) {
    if (value[name] !== null) value[name] = text(value[name]);
  }
  if (!/^[a-z0-9]+(?:-[a-z0-9]+)*$/.test(value.id)
      || !['publication', 'incident', 'aggregate'].includes(value.record_type)
      || !precision.includes(value.date_precision) || value.language !== 'es' || value.geometry !== null
      || typeof value.human_reviewed !== 'boolean' || typeof value.penitentiary_related !== 'boolean'
      || !Array.isArray(value.tags) || !value.tags.length
      || !Array.isArray(value.related_sources)
      || !['source_checked', 'editorially_reviewed'].includes(value.review_status)) fail();
  value.tags = value.tags.map(item => text(item));
  value.source_url = primarySource(value.source_url);
  if (value.origin_candidate_id !== null && !/^[a-f0-9]{64}$/.test(value.origin_candidate_id)) fail();
  for (const name of ['published_date', 'source_checked_at']) if (!validDate(value[name])) fail();
  for (const name of ['incident_date', 'incident_date_end', 'reviewed_at']) {
    if (value[name] !== null && !validDate(value[name])) fail();
  }
  if (value.published_date > value.source_checked_at || value.source_checked_at > today || value.source_checked_at > updatedAt
      || (value.incident_date && value.incident_date > value.published_date)
      || (value.incident_date_end && (!value.incident_date || value.incident_date_end < value.incident_date || value.incident_date_end > value.published_date))
      || ((value.date_precision === 'unknown') !== (value.incident_date === null))
      || (['month_range', 'year_range'].includes(value.date_precision) && !value.incident_date_end)
      || (value.record_type === 'publication' && (value.incident_date || value.department))
      || (value.department && !departments.has(value.department))) fail();
  if (value.human_reviewed) {
    if (value.review_status !== 'editorially_reviewed' || !/^[A-Za-z0-9][A-Za-z0-9-]{0,38}$/.test(value.reviewed_by ?? '')
        || !value.reviewed_at || value.reviewed_at > today || value.reviewed_at < value.source_checked_at
        || [...(value.review_notes ?? '')].length < 20) fail();
  } else if (value.review_status !== 'source_checked' || value.reviewed_by || value.reviewed_at || value.review_notes || value.origin_candidate_id) fail();
  value.related_sources = value.related_sources.map(rawSource => {
    const source = shape(rawSource, ['url', 'title', 'published_date', 'locator']);
    for (const name of Object.keys(source)) source[name] = text(source[name], 1);
    source.url = primarySource(source.url);
    if (!validDate(source.published_date) || source.published_date > value.source_checked_at) fail();
    return source;
  });
  value.dossier = dossier(value.dossier, value);
  return value;
}

export function parseCatalog(raw) {
  try {
    const value = shape(structuredClone(raw), ['schema_version', 'updated_at', 'scope', 'records']);
    if (value.schema_version !== 1 || !validDate(value.updated_at) || typeof value.scope !== 'string' || !Array.isArray(value.records)) fail();
    const today = new Date().toISOString().slice(0, 10);
    value.records = value.records.map(record => publication(record, value.updated_at, today));
    for (const name of ['id', 'source_record_key']) {
      if (new Set(value.records.map(record => record[name])).size !== value.records.length) fail();
    }
    value.records.sort((a, b) => b.published_date.localeCompare(a.published_date) || a.id.localeCompare(b.id));
    return value;
  } catch { return fail(); }
}
