import { newsDossierFor, safeSource } from './news-dossier.mjs';

// This projection describes processing only. It never contains draft fields,
// model output, private review notes or a claim of human approval.
export const NEWS_PROCESSING_STATUSES = Object.freeze(['pending', 'generated', 'source_unavailable', 'error', 'unknown']);
const object = value => Boolean(value && typeof value === 'object' && !Array.isArray(value));
const exact = (value, keys) => object(value) && Object.keys(value).length === keys.length && keys.every(key => Object.hasOwn(value, key));
const sha = value => typeof value === 'string' && /^[a-f0-9]{64}$/.test(value);
const date = value => typeof value === 'string' && !value.startsWith('0000-') && /^\d{4}-\d{2}-\d{2}$/.test(value) && Number.isFinite(Date.parse(value)) && new Date(value).toISOString().slice(0, 10) === value;
const instant = value => typeof value === 'string' && /^\d{4}-\d{2}-\d{2}T(?:[01]\d|2[0-3]):[0-5]\d:[0-5]\d(?:\.\d{1,6})?(?:Z|[+-](?:[01]\d|2[0-3]):[0-5]\d)$/.test(value) && date(value.slice(0, 10)) && Number.isFinite(Date.parse(value));
const fail = () => { throw new Error('Estado público de procesamiento de noticias inválido'); };

export function validateNewsProcessingEntry(value) {
  if (!exact(value, ['candidate_id', 'source_url', 'publisher', 'published_date', 'selection_content_sha256', 'status'])
    || !sha(value.candidate_id) || !safeSource(value.source_url) || value.source_url.length > 4096 || new URL(value.source_url).pathname.length < 5
    || typeof value.publisher !== 'string' || value.publisher.trim().length < 3 || value.publisher.length > 200
    || !date(value.published_date)
    || (value.selection_content_sha256 !== null && !sha(value.selection_content_sha256))
    || !NEWS_PROCESSING_STATUSES.includes(value.status)
    || (value.status === 'generated' && value.selection_content_sha256 === null)) fail();
  return structuredClone(value);
}

export function parseNewsProcessing(payload) {
  if (!exact(payload, ['schema_version', 'generated_at', 'entries']) || payload.schema_version !== 1
    || !instant(payload.generated_at) || !Array.isArray(payload.entries) || payload.entries.length > 100000) fail();
  try { if (new TextEncoder().encode(JSON.stringify(payload)).byteLength > 32 * 1024 * 1024) fail(); } catch { fail(); }
  const index = new Map();
  const urls = new Set();
  for (const raw of payload.entries) {
    const entry = validateNewsProcessingEntry(raw);
    if (index.has(entry.candidate_id) || urls.has(entry.source_url)) fail();
    index.set(entry.candidate_id, entry);
    urls.add(entry.source_url);
  }
  return index;
}

export function processingMatchesReference(entry, reference) {
  if (!object(entry)) return false;
  if (!reference || entry.candidate_id !== reference.id || entry.source_url !== reference.source_url
    || entry.publisher !== reference.publisher || entry.published_date !== (reference.published_date ?? null)) return false;
  if (reference.selection?.method === 'gemini_grounded_scope') {
    return sha(reference.selection.evidence?.content_sha256) && entry.selection_content_sha256 === reference.selection.evidence.content_sha256;
  }
  return reference.selection?.method === 'editorial_scope' && entry.selection_content_sha256 === null;
}

// Publication checks reject orphaned or stale entries. Runtime lookup is local
// to each reference so a newly discovered article remains unknown, not pending.
export function validateProcessingBindings(index, references) {
  const byId = new Map(references.map(reference => [reference.id, reference]));
  if (byId.size !== references.length) fail();
  for (const entry of index.values()) {
    validateNewsProcessingEntry(entry);
    if (!processingMatchesReference(entry, byId.get(entry.candidate_id))) fail();
  }
  return index;
}

export function newsProcessingFor(reference, index = new Map(), approvedDossier) {
  if (approvedDossier) {
    try {
      const published = newsDossierFor(reference, new Map([[reference.id, approvedDossier]]));
      if (published.human_reviewed) return 'reviewed';
      if (published.schema_version === 2) return 'automatically_published';
    } catch { /* An invalid dossier cannot grant publication status. */ }
  }
  const entry = index.get(reference.id);
  if (!entry || !processingMatchesReference(entry, reference)) return 'unknown';
  try { return validateNewsProcessingEntry(entry).status; } catch { return 'unknown'; }
}
