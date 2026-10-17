import { safeSource } from './news-dossier.mjs';
import { canonicalNewsSource, sourceIdentityMatches } from './news-identity.mjs';
import municipalityReference from '../apps/pipeline/src/mdh_pipeline/data/el_salvador_municipalities.json' with { type: 'json' };

export const NEWS_LOCATION_DEPARTMENTS = Object.freeze(['Ahuachapán', 'Santa Ana', 'Sonsonate', 'Chalatenango', 'La Libertad', 'San Salvador', 'Cuscatlán', 'La Paz', 'Cabañas', 'San Vicente', 'Usulután', 'San Miguel', 'Morazán', 'La Unión']);
const object = value => Boolean(value && typeof value === 'object' && !Array.isArray(value));
const exact = (value, keys) => object(value) && Object.keys(value).length === keys.length && keys.every(key => Object.hasOwn(value, key));
const sha = value => typeof value === 'string' && /^[a-f0-9]{64}$/.test(value);
const date = value => typeof value === 'string' && !value.startsWith('0000-') && /^\d{4}-\d{2}-\d{2}$/.test(value) && Number.isFinite(Date.parse(value)) && new Date(value).toISOString().slice(0, 10) === value;
const instant = value => typeof value === 'string' && /^\d{4}-\d{2}-\d{2}T(?:[01]\d|2[0-3]):[0-5]\d:[0-5]\d(?:\.\d{1,6})?(?:Z|[+-]\d{2}:\d{2})$/.test(value) && date(value.slice(0, 10)) && Number.isFinite(Date.parse(value));
const fail = () => { throw new Error('Capa pública de ubicaciones automáticas inválida'); };
const municipalityEntries = municipalityReference.municipalities.flatMap(municipality => [
  { ...municipality, kind: 'municipality' },
  ...municipality.districts.map(district => ({ ...district, department: municipality.department, kind: 'district' })),
]);
function validResolution(value) {
  const r = value.resolution;
  if (!exact(r, ['type', 'municipality', 'municipality_kind', 'reference_version', 'source_url'])
    || r.type !== 'municipality_to_department' || r.reference_version !== municipalityReference.reference_version) return false;
  return municipalityEntries.some(entry => entry.name === r.municipality && entry.department === value.department
    && entry.kind === r.municipality_kind && r.source_url === `${municipalityReference.source.url}#page=${entry.source_page}`);
}

function validCorroboration(value) {
  const proof = value.corroboration;
  return exact(proof, ['strategy', 'model_paragraph_id', 'explicit_paragraph_id', 'rule_version'])
    && proof.strategy === 'same_article_explicit_department' && proof.rule_version === 1
    && typeof proof.model_paragraph_id === 'string' && /^p[1-9]\d{0,2}$/.test(proof.model_paragraph_id)
    && typeof proof.explicit_paragraph_id === 'string' && /^p[1-9]\d{0,2}$/.test(proof.explicit_paragraph_id)
    && proof.model_paragraph_id !== proof.explicit_paragraph_id
    && value.evidence?.paragraph_id === proof.explicit_paragraph_id;
}

function validMention(value) {
  const municipal = object(value) && value.method === 'source_municipality_reference';
  return exact(value, ['method', 'department', 'paragraph_id', ...(municipal ? ['resolution'] : [])])
    && ['source_explicit_department', 'source_municipality_reference'].includes(value.method)
    && NEWS_LOCATION_DEPARTMENTS.includes(value.department)
    && typeof value.paragraph_id === 'string' && /^p[1-9]\d{0,2}$/.test(value.paragraph_id)
    && (!municipal || validResolution(value));
}

export function validatePublishedNewsLocation(value) {
  const plural = object(value) && value.method === 'source_administrative_mentions';
  const sourceOnly = object(value) && ['source_explicit_department', 'source_municipality_reference'].includes(value.method);
  const municipal = object(value) && ['gemini_municipality_reference', 'source_municipality_reference'].includes(value.method);
  const corroborated = object(value) && value.method === 'gemini_department_corroborated';
  if (!exact(value, ['candidate_id', 'source_url', 'publisher', 'published_date', 'review_status', 'human_reviewed', 'method', 'extracted_at', 'source_checked_at', 'evidence', ...(plural ? ['rule_version', 'mentions'] : ['department', ...(sourceOnly ? ['rule_version'] : []), ...(municipal ? ['resolution'] : corroborated ? ['corroboration'] : [])])])
    || !sourceIdentityMatches(value.candidate_id, value.source_url) || !safeSource(value.source_url) || value.source_url.length > 4096 || new URL(value.source_url).pathname.length < 5
    || canonicalNewsSource(value.source_url)?.name !== value.publisher
    || typeof value.publisher !== 'string' || value.publisher.trim().length < 3 || value.publisher.length > 200
    || !date(value.published_date) || (!plural && !NEWS_LOCATION_DEPARTMENTS.includes(value.department))
    || value.review_status !== 'automated_unreviewed' || value.human_reviewed !== false || !['gemini_explicit_department','gemini_municipality_reference','gemini_department_corroborated','source_explicit_department','source_municipality_reference','source_administrative_mentions'].includes(value.method)
    || (sourceOnly && ![1, 2].includes(value.rule_version))
    || (plural && (value.rule_version !== 2 || !Array.isArray(value.mentions) || value.mentions.length < 2 || value.mentions.length > 14
      || !value.mentions.every(validMention) || new Set(value.mentions.map(mention => mention.department)).size !== value.mentions.length))
    || (municipal && !validResolution(value)) || (corroborated && !validCorroboration(value))
    || !instant(value.extracted_at) || !instant(value.source_checked_at)
    || !exact(value.evidence, plural ? ['content_sha256'] : ['paragraph_id', 'content_sha256'])
    || (!plural && (typeof value.evidence.paragraph_id !== 'string' || !/^p[1-9]\d{0,2}$/.test(value.evidence.paragraph_id)))
    || !sha(value.evidence.content_sha256)) fail();
  return structuredClone(value);
}

export function parsePublishedNewsLocations(payload) {
  if (!exact(payload, ['schema_version', 'generated_at', 'locations']) || ![1, 2, 3, 4, 5].includes(payload.schema_version) || !instant(payload.generated_at) || !Array.isArray(payload.locations) || payload.locations.length > 100000) fail();
  const index = new Map();
  const urls = new Set();
  for (const raw of payload.locations) {
    const location = validatePublishedNewsLocation(raw);
    if (location.method === 'gemini_department_corroborated' && payload.schema_version < 3) fail();
    if (location.method.startsWith('source_') && payload.schema_version < 4) fail();
    if ((location.method === 'source_administrative_mentions' || location.rule_version === 2) && payload.schema_version !== 5) fail();
    if (index.has(location.candidate_id) || urls.has(location.source_url)) fail();
    index.set(location.candidate_id, location);
    urls.add(location.source_url);
  }
  return index;
}

export function validateLocationBindings(index, references) {
  const byId = new Map(references.map(reference => [reference.id, reference]));
  if (byId.size !== references.length) fail();
  for (const location of index.values()) {
    const reference = byId.get(location.candidate_id);
    if (!reference || !bound(location, reference)) fail();
  }
  return index;
}
const bound = (location, reference) => location.candidate_id === reference.id && location.source_url === reference.source_url && location.publisher === reference.publisher && location.published_date === reference.published_date
  && (reference.selection?.method !== 'gemini_grounded_scope'
    || (sha(reference.selection.evidence?.content_sha256) && location.evidence?.content_sha256 === reference.selection.evidence.content_sha256));

export function newsLocationFor(reference, locations = new Map()) {
  const location = locations.get(reference.id);
  return location && bound(location, reference) ? validatePublishedNewsLocation(location) : null;
}
