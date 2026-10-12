export const NEWS_SELECTION_PROMPTS = Object.freeze(['mdh-universal-news-grounded-8-relevance-scope', 'mdh-universal-news-grounded-9-inclusive-context', 'mdh-news-scope-1', 'mdh-news-scope-2-inclusive-context']);

// Public relevance gate: keyword discovery is never evidence for publishing a reference.
const object = value => Boolean(value && typeof value === 'object' && !Array.isArray(value));
const exact = (value, keys) => object(value) && Object.keys(value).length === keys.length && keys.every(key => Object.hasOwn(value, key));
const date = value => typeof value === 'string' && !value.startsWith('0000-') && /^\d{4}-\d{2}-\d{2}$/.test(value) && Number.isFinite(Date.parse(value)) && new Date(value).toISOString().slice(0, 10) === value;
const instant = value => typeof value === 'string' && /^\d{4}-\d{2}-\d{2}T(?:[01]\d|2[0-3]):[0-5]\d:[0-5]\d(?:\.\d{1,6})?(?:Z|[+-]\d{2}:\d{2})$/.test(value) && date(value.slice(0, 10)) && Number.isFinite(Date.parse(value));
const count = value => Number.isSafeInteger(value) && value >= 0;
const paragraphs = value => Array.isArray(value) && value.length >= 1 && value.length <= 25 && new Set(value).size === value.length && value.every(id => typeof id === 'string' && /^p[1-9][0-9]{0,2}$/.test(id));
const fail = () => { throw new Error('Selección pública de noticias inválida o sin evidencia de pertinencia'); };

export function validateNewsSelection(value) {
  if (!object(value) || value.jurisdiction !== 'SV' || value.relevance !== 'relevant') fail();
  if (value.method === 'gemini_grounded_scope') {
    if (!exact(value, ['method', 'review_status', 'human_reviewed', 'jurisdiction', 'relevance', 'classified_at', 'model', 'prompt_version', 'evidence'])
      || value.review_status !== 'automated_unreviewed' || value.human_reviewed !== false || !instant(value.classified_at)
      || typeof value.model !== 'string' || !/^gemini-[A-Za-z0-9_.-]{1,90}$/.test(value.model)
      || !NEWS_SELECTION_PROMPTS.includes(value.prompt_version)
      || !exact(value.evidence, ['content_sha256', 'jurisdiction_paragraph_ids', 'relevance_paragraph_ids'])
      || typeof value.evidence.content_sha256 !== 'string' || !/^[a-f0-9]{64}$/.test(value.evidence.content_sha256)
      || !paragraphs(value.evidence.jurisdiction_paragraph_ids) || !paragraphs(value.evidence.relevance_paragraph_ids)) fail();
  } else if (value.method === 'editorial_scope') {
    if (!exact(value, ['method', 'review_status', 'human_reviewed', 'jurisdiction', 'relevance', 'reviewed_by', 'reviewed_at'])
      || value.review_status !== 'editorially_reviewed' || value.human_reviewed !== true
      || typeof value.reviewed_by !== 'string' || !/^[A-Za-z0-9][A-Za-z0-9-]{0,38}$/.test(value.reviewed_by) || !date(value.reviewed_at)) fail();
  } else fail();
  return structuredClone(value);
}

export function isSelectedNewsReference(reference, dossier) {
  try { validateNewsSelection(reference?.selection); } catch { return false; }
  // A later human decision always supersedes automated selection, including an unresolved scope.
  return !dossier?.human_reviewed || (dossier.classification?.jurisdiction === 'SV' && dossier.classification?.relevance === 'relevant');
}

export function assertSelectedNewsArchive(payload) {
  if (!object(payload) || payload.schema_version !== 1 || !Array.isArray(payload.records) || !object(payload.policy) || !object(payload.coverage)) fail();
  const gate = payload.policy.selection_gate;
  const progressKeys = ['evaluated', 'relevant', 'excluded', 'uncertain', 'pending', 'evaluated_at'];
  if (!exact(gate, ['version', 'scope', 'candidates', 'selected', ...(gate?.version === 2 ? progressKeys : [])]) || ![1, 2].includes(gate.version) || gate.scope !== 'human_rights_el_salvador'
    || !count(gate.candidates) || !count(gate.selected) || gate.candidates < gate.selected || gate.selected !== payload.records.length
    || payload.coverage.records !== payload.records.length) fail();
  if (gate.version === 2 && (!instant(gate.evaluated_at)
    || progressKeys.slice(0, -1).some(key => !count(gate[key]))
    || gate.evaluated !== gate.relevant + gate.excluded + gate.uncertain
    || gate.candidates !== gate.evaluated + gate.pending)) fail();
  for (const reference of payload.records) if (!isSelectedNewsReference(reference)) fail();
  return payload;
}
