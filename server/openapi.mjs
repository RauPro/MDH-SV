import { LICENSE, LIMITATION, PUBLIC_URL } from './public-data.mjs';
import { NEWS_DOSSIER_FIELDS, NEWS_DOSSIER_LIST_FIELDS } from '../shared/news-dossier.mjs';

const parameter = (name, description, schema = { type: 'string', maxLength: 200 }) => ({ name, in: 'query', description, schema });
const common = [
  parameter('q', 'Texto en título, resumen, editorial o etiquetas; sin distinción de tildes y mayúsculas.'),
  parameter('publisher', 'Nombre exacto del medio u organización, sin distinción de tildes y mayúsculas.'),
  parameter('date_from', 'Fecha de publicación mínima, inclusiva; no es la fecha del hecho.', { type: 'string', format: 'date' }),
  parameter('date_to', 'Fecha de publicación máxima, inclusiva.', { type: 'string', format: 'date' }),
];
const recordFilters = [
  ...common,
  parameter('incident_date_from', 'Inicio inclusivo del intervalo de hechos; incluye intervalos de hechos que se solapan. Excluye fechas desconocidas.', { type: 'string', format: 'date' }),
  parameter('incident_date_to', 'Fin inclusivo del intervalo de hechos, según la precisión declarada en la ficha.', { type: 'string', format: 'date' }),
  parameter('record_type', 'Unidad documental; nunca equivale automáticamente a una víctima.', { type: 'string', enum: ['incident', 'aggregate', 'publication'] }),
  parameter('department', 'Departamento exacto o unknown para ubicación no determinada.'),
  parameter('category', 'Categoría documental exacta.'),
  parameter('review_status', 'Revisión documental o editorial humana.', { type: 'string', enum: ['source_checked', 'editorially_reviewed'] }),
];
const pagination = [
  parameter('limit', 'Máximo de resultados.', { type: 'integer', minimum: 1, maximum: 100, default: 25 }),
  parameter('offset', 'Posición de inicio; orden por publicación descendente e id ascendente.', { type: 'integer', minimum: 0, maximum: 1000000, default: 0 }),
];
const ref = name => ({ $ref: `#/components/schemas/${name}` });
const evidenceField = valueSchema => ({ type: 'object', required: ['value', 'status', 'evidence_refs', 'note'], properties: { value: { anyOf: [valueSchema, { type: 'null' }] }, status: { type: 'string', enum: ['reported', 'not_reported', 'withheld', 'not_applicable'] }, evidence_refs: { type: 'array', items: { type: 'string' } }, note: { type: ['string', 'null'] } } });
const newsField = name => {
  const field = evidenceField(NEWS_DOSSIER_LIST_FIELDS.includes(name) ? { type: 'array', maxItems: 40, items: { type: 'string', maxLength: 300 } } : { type: 'string', maxLength: 2000 });
  field.properties.status.enum.unshift('pending');
  field.properties.evidence_refs.maxItems = 100;
  field.properties.note.maxLength = 1000;
  field.additionalProperties = false;
  return field;
};
const json = schema => ({ 'application/json': { schema } });
const errors = Object.fromEntries([400, 404, 405, 414, 500, 503].map(status => [status, { description: 'Error explícito; nunca devuelve HTML ni datos privados.', content: json(ref('Error')) }]));
const operation = (id, summary, parameters, schema) => ({ operationId: id, summary, parameters, responses: { 200: { description: 'Lectura correcta del snapshot público.', content: json(schema) }, ...errors } });
const paged = name => ({ type: 'object', required: ['data', 'pagination', 'meta'], properties: { data: { type: 'array', items: ref(name) }, pagination: ref('Pagination'), meta: ref('Metadata') } });

export const openapi = {
  openapi: '3.1.0',
  info: { title: 'MDH-SV · API pública de investigación', version: '1.0.0', description: `Acceso anónimo de solo lectura al monitor de El Salvador. ${LIMITATION} Las noticias son un archivo bibliográfico separado de las fichas documentadas.`, license: { name: 'CC BY 4.0 para metadatos y resúmenes propios; originales conservan sus derechos.', url: LICENSE } },
  servers: [{ url: PUBLIC_URL }],
  security: [],
  paths: {
    '/api/v1/records': { get: operation('searchRecords', 'Buscar fichas documentadas', [...recordFilters, ...pagination], paged('Record')) },
    '/api/v1/records/{id}': { get: operation('getRecord', 'Consultar ficha completa y evidencia', [{ name: 'id', in: 'path', required: true, schema: { type: 'string', pattern: '^[a-z0-9][a-z0-9-]{0,149}$' } }], { type: 'object', properties: { data: ref('Record'), meta: ref('Metadata') }, required: ['data', 'meta'] }) },
    '/api/v1/stats': { get: operation('getStatistics', 'Conteos documentales separados por unidad y revisión', recordFilters, { type: 'object', required: ['data', 'meta'], properties: { data: { type: 'object', required: ['total_records', 'by_record_type', 'by_review_status', 'human_reviewed'], properties: { total_records: { type: 'integer' }, human_reviewed: { type: 'integer' }, ...Object.fromEntries(['by_record_type', 'by_department', 'by_category', 'by_review_status'].map(key => [key, { type: 'object', additionalProperties: { type: 'integer' } }])) } }, meta: ref('Metadata') } }) },
    '/api/v1/news': { get: operation('searchNews', 'Explorar noticias históricas, sin afirmar validación de los hechos', [...common, parameter('scope_hint', 'Indicio territorial automático por título y URL; no confirma jurisdicción. Omitir devuelve todo el archivo.', { type: 'string', enum: ['el_salvador', 'international_context', 'undetermined'] }), ...pagination], paged('News')) },
    '/api/v1/openapi.json': { get: operation('getOpenApi', 'Contrato OpenAPI 3.1', [], { type: 'object' }) },
  },
  components: { schemas: {
    AutomaticNewsLocation: { type: 'object', additionalProperties: false, description: 'Departamento mencionado en una fuente pública, extraído automáticamente y SIN REVISAR. No confirma el lugar de una violación. Separado de la ficha y de sus aprobaciones.', required: ['candidate_id','source_url','publisher','published_date','department','review_status','human_reviewed','method','extracted_at','source_checked_at','evidence'], properties: {
      candidate_id: { type: 'string', pattern: '^[a-f0-9]{64}$' }, source_url: { type: 'string', format: 'uri' }, publisher: { type: 'string' }, published_date: { type: 'string', format: 'date' }, department: { type: 'string' }, review_status: { type: 'string', const: 'automated_unreviewed' }, human_reviewed: { type: 'boolean', const: false }, method: { type: 'string', const: 'gemini_explicit_department' }, extracted_at: { type: 'string', format: 'date-time' }, source_checked_at: { type: 'string', format: 'date-time' }, evidence: { type: 'object', additionalProperties: false, required: ['paragraph_id','content_sha256'], properties: { paragraph_id: { type: 'string', pattern: '^p[1-9][0-9]{0,2}$' }, content_sha256: { type: 'string', pattern: '^[a-f0-9]{64}$' } } },
    } },
    NewsDossier: { type: 'object', additionalProperties: false, required: ['schema_version', 'kind', 'candidate_id', 'source_url', 'publisher', 'source_title', 'published_date', 'extraction_status', 'classification', 'fields', 'date_precision', 'evidence', 'human_reviewed', 'reviewed_by', 'reviewed_at', 'review_notes'], description: 'Ficha universal de noticia. Sin ficha pública aprobada todos los campos estructurados son pending; esto no significa ausencia en la fuente ni refleja el estado de una cola privada. Una ficha revisada puede ser parcial o declarar no pertinencia. No equivale a un caso ni se suma a estadísticas de violaciones.', properties: {
      schema_version: { type: 'integer', const: 1 }, kind: { type: 'string', const: 'news_dossier' }, candidate_id: { type: 'string', pattern: '^[a-f0-9]{64}$' },
      source_url: { type: 'string', format: 'uri', maxLength: 4096 }, publisher: { type: 'string', minLength: 3, maxLength: 200 }, source_title: { type: 'string', minLength: 3, maxLength: 500 }, published_date: { type: ['string', 'null'], format: 'date' },
      extraction_status: { type: 'string', enum: ['pending', 'processed', 'source_unavailable', 'error'], description: 'La consulta pública devuelve pending si no hay ficha revisada; nunca deduce errores privados.' },
      classification: { type: 'object', additionalProperties: false, required: ['jurisdiction', 'relevance', 'record_type'], properties: { jurisdiction: { type: 'string', enum: ['SV', 'other', 'unclear'] }, relevance: { type: 'string', enum: ['relevant', 'not_relevant', 'unclear'] }, record_type: { type: ['string', 'null'], enum: ['incident', 'aggregate', 'publication', null] } } },
      fields: { type: 'object', additionalProperties: false, required: NEWS_DOSSIER_FIELDS, properties: Object.fromEntries(NEWS_DOSSIER_FIELDS.map(name => [name, newsField(name)])) },
      date_precision: { type: 'string', enum: ['unknown', 'day', 'month_range', 'year_range'] },
      evidence: { type: 'array', minItems: 1, maxItems: 200, items: { type: 'object', required: ['id', 'url', 'locator'], additionalProperties: false, properties: { id: { type: 'string' }, url: { type: 'string', format: 'uri', maxLength: 4096 }, locator: { type: 'string', minLength: 10, maxLength: 1000 } } } },
      human_reviewed: { type: 'boolean' }, reviewed_by: { type: ['string', 'null'] }, reviewed_at: { type: ['string', 'null'], format: 'date' }, review_notes: { type: ['string', 'null'], maxLength: 4000 },
    } },
    EvidenceText: evidenceField({ type: 'string' }),
    EvidenceList: evidenceField({ type: 'array', items: { type: 'string' } }),
    Dossier: { type: 'object', description: 'Campos atribuidos a fuentes con estado explícito de desconocido u omitido. Ver docs/DOSSIER-SCHEMA.md.', required: ['schema_version', 'alleged_violation', 'facts', 'affected_persons', 'location', 'event_date', 'evidence'], properties: {
      schema_version: { type: 'integer', const: 1 },
      ...Object.fromEntries(['alleged_violation', 'facts', 'event_date', 'event_date_end', 'event_time', 'hypothesis'].map(key => [key, ref('EvidenceText')])),
      ...Object.fromEntries(['affected_persons', 'reported_actors', 'institutional_sources', 'vulnerable_groups', 'themes', 'instrument'].map(key => [key, ref('EvidenceList')])),
      date_precision: { type: 'string' }, location: { type: 'object', properties: Object.fromEntries(['department', 'municipality', 'public_place'].map(key => [key, ref('EvidenceText')])) },
      evidence: { type: 'array', items: { type: 'object', required: ['id', 'url', 'locator'], properties: { id: { type: 'string' }, url: { type: 'string', format: 'uri' }, locator: { type: 'string' } } } },
    } },
    Record: { type: 'object', required: ['id', 'record_type', 'title', 'summary', 'source_url', 'published_date', 'review_status', 'human_reviewed', 'dossier'], additionalProperties: true, description: 'Ficha pública completa. Los campos adicionales del catálogo conservan trazabilidad, precisión de fecha, ubicación y estructura de la ficha. null significa desconocido, no una ausencia de violación.', properties: {
      dossier: ref('Dossier'),
      id: { type: 'string' }, record_type: { type: 'string', enum: ['incident', 'aggregate', 'publication'] }, title: { type: 'string' }, summary: { type: 'string' }, publisher: { type: 'string' }, source_url: { type: 'string', format: 'uri' }, source_title: { type: 'string' }, source_locator: { type: 'string' }, published_date: { type: 'string', format: 'date' }, incident_date: { type: ['string', 'null'] }, incident_date_end: { type: ['string', 'null'] }, date_precision: { type: 'string' }, date_note: { type: 'string' }, department: { type: ['string', 'null'] }, coverage: { type: 'string' }, category: { type: 'string' }, tags: { type: 'array', items: { type: 'string' } }, review_status: { type: 'string', enum: ['source_checked', 'editorially_reviewed'] }, human_reviewed: { type: 'boolean' }, source_checked_at: { type: 'string', format: 'date' }, source_check_method: { type: 'string' }, related_sources: { type: 'array', items: { type: 'object', additionalProperties: true } }, reported_actor: { type: ['string', 'null'] }, privacy_note: { type: 'string' },
    } },
    News: { type: 'object', required: ['id', 'title', 'publisher', 'source_url', 'published_date', 'review_status', 'scope_hint', 'scope_terms', 'news_dossier', 'automatic_location'], additionalProperties: true, description: 'Referencia bibliográfica recolectada; no equivale a caso verificado. No contiene el cuerpo del artículo ni información de la cola privada.', properties: { automatic_location: { anyOf: [ref('AutomaticNewsLocation'), { type: 'null' }] }, news_dossier: ref('NewsDossier'), scope_hint: { type: 'string', enum: ['el_salvador', 'international_context', 'undetermined'] }, scope_terms: { type: 'array', items: { type: 'string' } }, jurisdiction_status: { type: 'string', const: 'not_reviewed' }, id: { type: 'string' }, title: { type: 'string' }, publisher: { type: 'string' }, source_url: { type: 'string', format: 'uri' }, published_date: { type: 'string', format: 'date' }, review_status: { type: 'string' }, topics: { type: 'array', items: { type: 'string' } }, discovery: { type: 'object', additionalProperties: true } } },
    Pagination: { type: 'object', required: ['total', 'limit', 'offset', 'next_offset', 'next'], properties: { total: { type: 'integer' }, limit: { type: 'integer' }, offset: { type: 'integer' }, next_offset: { type: ['integer', 'null'] }, next: { type: ['string', 'null'] } } },
    Metadata: { type: 'object', required: ['api_version', 'schema_version', 'updated_at', 'license', 'limitations'], additionalProperties: true, properties: { api_version: { type: 'string', const: '1' }, schema_version: { type: 'integer' }, updated_at: { type: ['string', 'null'] }, license: { type: 'string', format: 'uri' }, attribution: { type: 'string' }, limitations: { type: 'string' }, coverage: { type: 'object', additionalProperties: true }, paper: { type: 'object', additionalProperties: true }, review_notice: { type: 'string' }, news_dossiers_status: { type: 'string', enum: ['available', 'unavailable'] } } },
    Error: { type: 'object', required: ['error'], properties: { error: { type: 'object', required: ['code', 'message'], properties: { code: { type: 'string' }, message: { type: 'string' } } } } },
  } },
};
