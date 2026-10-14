// Shared browser/server contract. Only this module constructs public placeholders.
export const NEWS_DOSSIER_FIELDS = Object.freeze([
  'summary', 'category', 'alleged_violation', 'facts', 'affected_persons', 'country',
  'department', 'municipality', 'public_place', 'event_date', 'event_date_end', 'event_time',
  'reported_actors', 'institutional_sources', 'vulnerable_groups', 'themes', 'hypothesis', 'instrument',
]);
export const NEWS_DOSSIER_PENDING = 'Pendiente de extracción o revisión';
export const NEWS_DOSSIER_LIST_FIELDS = Object.freeze(['affected_persons', 'reported_actors', 'institutional_sources', 'vulnerable_groups', 'themes', 'instrument']);
const listFields = new Set(NEWS_DOSSIER_LIST_FIELDS);
const sourceHosts = new Set(['www.hrw.org', 'www.oas.org', 'www.amnesty.org', 'citizenlab.ca', 'www.corteidh.or.cr', 'spcommreports.ohchr.org', 'www.ohchr.org', 'www.diariocolatino.com', 'diariocolatino.com', 'gatoencerrado.news', 'www.revistafactum.com', 'revistafactum.com', 'elfaro.net', 'www.elfaro.net', 'beta.elfaro.net', 'diario.elmundo.sv', 'www.laprensagrafica.com', 'laprensagrafica.com', 'historico.elsalvador.com', 'www.elsalvador.com', 'elsalvador.com']);
export function safeSource(value) {
  if (typeof value !== 'string') return false;
  try {
    const url = new URL(value);
    return url.protocol === 'https:' && sourceHosts.has(url.hostname) && !url.username && !url.password && !url.port;
  } catch { return false; }
}
const object = value => Boolean(value && typeof value === 'object' && !Array.isArray(value));
const text = value => typeof value === 'string' && Boolean(value.trim());
const date = value => typeof value === 'string' && !value.startsWith('0000-') && /^\d{4}-\d{2}-\d{2}$/.test(value) && Number.isFinite(Date.parse(value)) && new Date(value).toISOString().slice(0, 10) === value;
const sha = value => typeof value === 'string' && /^[a-f0-9]{64}$/.test(value);
const utcInstant = value => typeof value === 'string' && /^\d{4}-\d{2}-\d{2}T(?:[01]\d|2[0-3]):[0-5]\d:[0-5]\d(?:\.\d{1,6})?(?:Z|\+00:00)$/.test(value) && date(value.slice(0, 10)) && Number.isFinite(Date.parse(value));
const exactKeys = (value, keys) => object(value) && Object.keys(value).length === keys.length && keys.every(key => Object.hasOwn(value, key));
const fail = () => { throw new Error('Ficha de noticia pública inválida o sin validación de publicación'); };
const calendar = new Intl.DateTimeFormat('en', { year: 'numeric', month: '2-digit', day: '2-digit', timeZone: 'America/El_Salvador' });
function today() {
  const parts = Object.fromEntries(calendar.formatToParts(new Date()).map(part => [part.type, part.value]));
  return `${parts.year}-${parts.month}-${parts.day}`;
}

export function pendingNewsDossier(reference) {
  return {
    schema_version: 1, kind: 'news_dossier', candidate_id: reference.id, source_url: reference.source_url,
    publisher: reference.publisher, source_title: reference.title, published_date: reference.published_date ?? null,
    extraction_status: 'pending', classification: { jurisdiction: 'unclear', relevance: 'unclear', record_type: null },
    fields: Object.fromEntries(NEWS_DOSSIER_FIELDS.map(name => [name, {
      value: null, status: 'pending', evidence_refs: [], note: null,
    }])), date_precision: 'unknown',
    evidence: [{ id: 'bibliography', url: reference.source_url, locator: 'Referencia bibliográfica pública: título, medio y fecha de publicación. No acredita extracción ni revisión del contenido.' }],
    human_reviewed: false, reviewed_by: null, reviewed_at: null, review_notes: null,
  };
}

export function validatePublishedNewsDossier(value) {
  try {
    if (new TextEncoder().encode(JSON.stringify(value)).byteLength > 180000) fail();
  } catch { fail(); }
  const keys = ['schema_version', 'kind', 'candidate_id', 'source_url', 'publisher', 'source_title', 'published_date',
    'extraction_status', 'classification', 'fields', 'date_precision', 'evidence', 'human_reviewed', 'reviewed_by', 'reviewed_at', 'review_notes', ...(value?.schema_version === 2 ? ['automation'] : [])];
  if (!exactKeys(value, keys) || ![1, 2].includes(value.schema_version) || value.kind !== 'news_dossier'
    || typeof value.candidate_id !== 'string' || !/^[a-f0-9]{64}$/.test(value.candidate_id)
    || !safeSource(value.source_url) || value.source_url.length > 4096 || new URL(value.source_url).pathname.length < 5
    || !text(value.publisher) || value.publisher.length < 3 || value.publisher.length > 200
    || !text(value.source_title) || value.source_title.length < 3 || value.source_title.length > 500
    || (value.published_date !== null && !date(value.published_date))
    || value.extraction_status !== 'processed'
    || (value.published_date && value.published_date > today())) fail();
  if (value.schema_version === 1 && (value.human_reviewed !== true || typeof value.reviewed_by !== 'string'
    || !/^[A-Za-z0-9][A-Za-z0-9-]{0,38}$/.test(value.reviewed_by)
    || !date(value.reviewed_at) || value.reviewed_at > today()
    || (value.published_date && value.reviewed_at < value.published_date)
    || !text(value.review_notes) || value.review_notes.trim().length < 20 || value.review_notes.length > 4000)) fail();
  if (value.schema_version === 2) {
    const receipt = value.automation;
    if (value.human_reviewed !== false || value.reviewed_by !== null || value.reviewed_at !== null || value.review_notes !== null
      || !exactKeys(receipt, ['policy_version', 'source_content_sha256', 'validated_at', 'method', 'evaluator_model', 'corroboration'])
      || receipt.policy_version !== 'auto-news-v1' || receipt.method !== 'gemini_evidence_review' || receipt.corroboration !== 'single_source'
      || !sha(receipt.source_content_sha256) || !utcInstant(receipt.validated_at)
      || Date.parse(receipt.validated_at) > Date.now() + 300000
      || (value.published_date && receipt.validated_at.slice(0, 10) < value.published_date)
      || !text(receipt.evaluator_model) || receipt.evaluator_model.length > 100 || /[\u0000-\u001f\u007f]/.test(receipt.evaluator_model)
      || value.classification?.jurisdiction !== 'SV' || value.classification?.relevance !== 'relevant') fail();
  }
  if (!exactKeys(value.classification, ['jurisdiction', 'relevance', 'record_type'])
    || !['SV', 'other', 'unclear'].includes(value.classification.jurisdiction)
    || !['relevant', 'not_relevant', 'unclear'].includes(value.classification.relevance)
    || ![null, 'incident', 'aggregate', 'publication'].includes(value.classification.record_type)
    || !['unknown', 'day', 'month_range', 'year_range'].includes(value.date_precision)
    || !exactKeys(value.fields, NEWS_DOSSIER_FIELDS) || !Array.isArray(value.evidence) || !value.evidence.length || value.evidence.length > 200) fail();
  const evidenceIds = new Set();
  for (const item of value.evidence) {
    if (!exactKeys(item, ['id', 'url', 'locator']) || typeof item.id !== 'string' || !/^[a-z][a-z0-9-]*$/.test(item.id) || evidenceIds.has(item.id)
      || !safeSource(item.url) || item.url.length > 4096 || new URL(item.url).pathname.length < 5
      || !text(item.locator) || item.locator.trim().length < 10 || item.locator.length > 1000) fail();
    evidenceIds.add(item.id);
  }
  if (!value.evidence.some(item => item.url === value.source_url)) fail();
  for (const name of NEWS_DOSSIER_FIELDS) {
    const field = value.fields[name];
    if (!exactKeys(field, ['value', 'status', 'evidence_refs', 'note'])
      || !['pending', 'reported', 'not_reported', 'withheld', 'not_applicable'].includes(field.status)
      || !Array.isArray(field.evidence_refs) || field.evidence_refs.length > 100 || field.evidence_refs.some(id => !evidenceIds.has(id))
      || new Set(field.evidence_refs).size !== field.evidence_refs.length
      || (field.note !== null && (typeof field.note !== 'string' || field.note.length > 1000))) fail();
    if (value.extraction_status !== 'processed' && !['pending', 'withheld'].includes(field.status)) fail();
    if (field.status === 'reported') {
      if (!field.evidence_refs.length || (listFields.has(name)
        ? !Array.isArray(field.value) || !field.value.length || field.value.length > 40 || !field.value.every(item => text(item) && item.length <= 300)
        : !text(field.value) || field.value.length > 2000)) fail();
      if (['event_date', 'event_date_end'].includes(name) && !date(field.value)) fail();
    } else if (field.value !== null || field.evidence_refs.length) fail();
    if (['withheld', 'not_applicable'].includes(field.status) && (!text(field.note) || field.note.trim().length < 10)) fail();
  }
  const start = value.fields.event_date.value;
  const end = value.fields.event_date_end.value;
  if ((end && (!start || end < start))
    || (value.date_precision === 'unknown' && (start || end))
    || (value.date_precision !== 'unknown' && !start)
    || (['month_range', 'year_range'].includes(value.date_precision) && !end)) fail();
  if (value.schema_version === 2 && !NEWS_DOSSIER_FIELDS.some(name => value.fields[name].status === 'reported')) fail();
  // Copy only the validated public contract, avoiding mutable shared references.
  return structuredClone(value);
}

export function parsePublishedNewsDossiers(payload) {
  if (!exactKeys(payload, ['schema_version', 'dossiers']) || payload.schema_version !== 1 || !Array.isArray(payload.dossiers)) fail();
  const index = new Map();
  const urls = new Set();
  for (const value of payload.dossiers) {
    const dossier = validatePublishedNewsDossier(value);
    if (index.has(dossier.candidate_id) || urls.has(dossier.source_url)) fail();
    index.set(dossier.candidate_id, dossier);
    urls.add(dossier.source_url);
  }
  return index;
}

export function validateDossierBindings(index, references) {
  const byId = new Map(references.map(reference => [reference.id, reference]));
  if (byId.size !== references.length) fail();
  for (const dossier of index.values()) {
    const reference = byId.get(dossier.candidate_id);
    validatePublishedNewsDossier(dossier);
    if (!dossierMatchesReference(dossier, reference)) fail();
  }
  return index;
}

function dossierMatchesReference(value, reference) {
  return Boolean(reference && value.candidate_id === reference.id && value.source_url === reference.source_url
    && value.publisher === reference.publisher && value.published_date === (reference.published_date ?? null)
    && (value.schema_version !== 2 || (reference.selection?.method === 'gemini_grounded_scope'
      && sha(reference.selection.evidence?.content_sha256) && value.automation?.source_content_sha256 === reference.selection.evidence.content_sha256)));
}

export function newsDossierFor(reference, approved = new Map()) {
  const value = approved.get(reference.id);
  if (value && dossierMatchesReference(value, reference)) {
    // Re-check the public contract even if the caller supplied its own Map.
    return validatePublishedNewsDossier(value);
  }
  return pendingNewsDossier(reference);
}
