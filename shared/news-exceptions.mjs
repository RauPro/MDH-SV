// Public record of maintainer decisions only. Request text and private dossiers
// never enter this projection; an issue alone cannot grant publication rights.
const object = value => Boolean(value && typeof value === 'object' && !Array.isArray(value));
const exact = (value, keys) => object(value) && Object.keys(value).length === keys.length && keys.every(key => Object.hasOwn(value, key));
const day = value => typeof value === 'string' && !value.startsWith('0000-') && /^\d{4}-\d{2}-\d{2}$/.test(value) && Number.isFinite(Date.parse(value)) && new Date(value).toISOString().slice(0, 10) === value;
const utcInstant = value => typeof value === 'string' && /^\d{4}-\d{2}-\d{2}T(?:[01]\d|2[0-3]):[0-5]\d:[0-5]\d(?:\.\d{1,6})?(?:Z|\+00:00)$/.test(value) && day(value.slice(0, 10)) && Number.isFinite(Date.parse(value));
const fail = () => { throw new Error('Decisiones públicas de noticias inválidas'); };

export function parseNewsExceptions(payload) {
  if (!exact(payload, ['schema_version', 'decisions']) || payload.schema_version !== 1
    || !Array.isArray(payload.decisions) || payload.decisions.length > 100000) fail();
  try { if (new TextEncoder().encode(JSON.stringify(payload)).byteLength > 32 * 1024 * 1024) fail(); } catch { fail(); }
  const index = new Map();
  for (const value of payload.decisions) {
    if (!exact(value, ['candidate_id', 'withdrawn', 'request_generation', 'issue_number', 'decided_by', 'decided_at'])
      || typeof value.candidate_id !== 'string' || !/^[a-f0-9]{64}$/.test(value.candidate_id)
      || typeof value.withdrawn !== 'boolean' || !Number.isSafeInteger(value.request_generation) || value.request_generation < 0
      || !Number.isSafeInteger(value.issue_number) || value.issue_number < 1
      || typeof value.decided_by !== 'string' || !/^[A-Za-z0-9][A-Za-z0-9-]{0,38}$/.test(value.decided_by)
      || !utcInstant(value.decided_at) || Date.parse(value.decided_at) > Date.now() + 300000
      || index.has(value.candidate_id)) fail();
    index.set(value.candidate_id, structuredClone(value));
  }
  return index;
}
