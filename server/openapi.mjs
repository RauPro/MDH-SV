import { LICENSE, LIMITATION, PUBLIC_URL } from './public-data.mjs';
import { NEWS_SELECTION_PROMPTS } from '../shared/news-selection.mjs';
import { NEWS_DOSSIER_FIELDS, NEWS_DOSSIER_LIST_FIELDS } from '../shared/news-dossier.mjs';
import { NEWS_PROCESSING_STATUSES } from '../shared/news-processing.mjs';
import { OFFICIAL_STATISTICS_DEPARTMENTS } from '../shared/official-statistics.mjs';
import { OFFICIAL_STATISTICS_NOTICE } from './official-statistics.mjs';

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
const officialValue = {
  value: { type: ['integer', 'null'], minimum: 0 },
  status: { type: 'string', enum: ['available', 'not_available'] },
  locator: { type: ['string', 'null'], maxLength: 1200 },
};
const officialAvailability = {
  if: { properties: { status: { const: 'available' } }, required: ['status'] },
  then: { properties: { value: { type: 'integer', minimum: 0 }, locator: { type: 'string', minLength: 1 } } },
  else: { properties: { value: { type: 'null' }, locator: { type: 'null' } } },
};

export const openapi = {
  openapi: '3.1.0',
  info: { title: 'MDH-SV · API pública de investigación', version: '1.0.0', description: `Acceso anónimo de solo lectura al monitor de El Salvador. ${LIMITATION} Las noticias son un archivo bibliográfico separado de las fichas documentadas.`, license: { name: 'CC BY 4.0 para metadatos y resúmenes propios; originales conservan sus derechos.', url: LICENSE } },
  servers: [{ url: PUBLIC_URL }],
  security: [],
  paths: {
    '/api/v1/records': { get: operation('searchRecords', 'Buscar fichas documentadas', [...recordFilters, ...pagination], paged('Record')) },
    '/api/v1/records/{id}': { get: operation('getRecord', 'Consultar ficha completa y evidencia', [{ name: 'id', in: 'path', required: true, schema: { type: 'string', pattern: '^[a-z0-9][a-z0-9-]{0,149}$' } }], { type: 'object', properties: { data: ref('Record'), meta: ref('Metadata') }, required: ['data', 'meta'] }) },
    '/api/v1/stats': { get: operation('getStatistics', 'Conteos documentales separados por unidad y revisión', recordFilters, { type: 'object', required: ['data', 'meta'], properties: { data: { type: 'object', required: ['total_records', 'by_record_type', 'by_review_status', 'human_reviewed'], properties: { total_records: { type: 'integer' }, human_reviewed: { type: 'integer' }, ...Object.fromEntries(['by_record_type', 'by_department', 'by_category', 'by_review_status'].map(key => [key, { type: 'object', additionalProperties: { type: 'integer' } }])) } }, meta: ref('Metadata') } }) },
    '/api/v1/official-statistics': { get: { ...operation('getOfficialStatistics', 'Consultar agregados oficiales de homicidios con su fuente y período', [
      parameter('year', 'Año exacto, desde 2010 hasta el año de meta.as_of; años posteriores al corte se rechazan.', { type: 'integer', minimum: 2010, maximum: 9999 }),
      parameter('department', 'national o uno de los 14 departamentos canónicos. Omitirlo devuelve todos los ámbitos, incluidos datos no disponibles.', { type: 'string', enum: ['national', ...OFFICIAL_STATISTICS_DEPARTMENTS] }),
    ], ref('OfficialStatisticsResponse')), description: `${OFFICIAL_STATISTICS_NOTICE} Sin paginación. Una fila nacional y 14 departamentales por año: 255 filas para el corte 2026. Las filas departamentales no heredan meses nacionales. Un snapshot inválido o ausente devuelve 503.` } },
    '/api/v1/news': { get: operation('searchNews', 'Consultar noticias seleccionadas por pertinencia en derechos humanos de El Salvador', [...common, parameter('scope_hint', 'Indicio de búsqueda por título y URL, conservado como metadato; no determina la selección. Todos los resultados ya pasan el filtro de pertinencia en derechos humanos de El Salvador.', { type: 'string', enum: ['el_salvador', 'international_context', 'undetermined'] }), ...pagination], paged('News')) },
    '/api/v1/openapi.json': { get: operation('getOpenApi', 'Contrato OpenAPI 3.1', [], { type: 'object' }) },
  },
  components: { schemas: {
    OfficialStatisticsSource: {
      type: 'object', additionalProperties: false,
      required: ['id', 'institution', 'title', 'url', 'format', 'retrieved_at', 'content_sha256', 'edition_note'],
      properties: {
        id: { type: 'string', pattern: '^[a-z][a-z0-9_-]{0,99}$' }, institution: { type: 'string', minLength: 1, maxLength: 200 },
        title: { type: 'string', minLength: 1, maxLength: 600 },
        url: { type: 'string', format: 'uri', maxLength: 4096, pattern: '^https://(?:transparencia\\.oj\\.gob\\.sv|(?:www\\.)?fiscalia\\.gob\\.sv|data\\.unodc\\.org)/.+' },
        format: { type: 'string', enum: ['pdf', 'html', 'xlsx'] }, retrieved_at: { type: 'string', format: 'date-time' },
        content_sha256: { type: 'string', pattern: '^[a-f0-9]{64}$' }, edition_note: { type: 'string', minLength: 1, maxLength: 4000 },
      },
    },
    OfficialStatisticsMonth: {
      type: 'object', additionalProperties: false, required: ['month', 'value', 'status', 'locator'],
      description: 'Dato mensual nacional, no departamental. Doce meses por período cuando existe esta desagregación. outside_cutoff identifica meses posteriores a as_of; not_available puede identificar meses pasados aún no publicados. Ninguno significa cero.',
      properties: { ...officialValue, month: { type: 'integer', minimum: 1, maximum: 12 }, status: { type: 'string', enum: ['available', 'not_available', 'outside_cutoff'] } },
      allOf: [officialAvailability],
    },
    OfficialStatisticsRow: {
      type: 'object', additionalProperties: false,
      required: ['year', 'start_date', 'end_date', 'completeness', 'indicator', 'unit', 'methodology', 'notes', 'territory_level', 'department', 'value', 'status', 'locator', 'months', 'source'],
      description: 'Agregado oficial independiente de noticias y fichas. Conservar unidad, fuente y versión; no sumar filas nacionales con departamentos ni mezclar instituciones/años. null es ausencia de un valor verificado, no cero.',
      properties: {
        ...officialValue, year: { type: 'integer', minimum: 2010, maximum: 9999 },
        start_date: { type: 'string', format: 'date' }, end_date: { type: 'string', format: 'date' },
        completeness: { type: 'string', enum: ['complete', 'partial', 'unavailable'] },
        indicator: { type: 'string', enum: ['homicides', 'intentional_homicides'] },
        unit: { type: 'string', enum: ['persons', 'source_reported_homicides'] },
        methodology: { type: 'string', minLength: 1, maxLength: 8000 },
        notes: { type: 'array', maxItems: 30, items: { type: 'string', minLength: 1, maxLength: 4000 } },
        department: { type: 'string', enum: ['national', ...OFFICIAL_STATISTICS_DEPARTMENTS] },
        territory_level: { type: 'string', enum: ['national', 'department'], description: 'Las filas nacionales y departamentales se solapan. No sumar ambos niveles.' },
        months: { anyOf: [{ type: 'null' }, { type: 'array', minItems: 12, maxItems: 12, items: ref('OfficialStatisticsMonth') }] },
        source: ref('OfficialStatisticsSource'),
      },
      allOf: [officialAvailability, { if: { properties: { department: { const: 'national' } }, required: ['department'] },
        then: { properties: { territory_level: { const: 'national' } } },
        else: { properties: { months: { type: 'null' }, territory_level: { const: 'department' } } } }],
    },
    OfficialStatisticsMetadata: {
      type: 'object', additionalProperties: false,
      required: ['api_version', 'schema_version', 'dataset_id', 'title', 'as_of', 'generated_at', 'coverage_start_year', 'returned_rows', 'comparability_note', 'limitations'],
      properties: {
        api_version: { type: 'string', const: '1' }, schema_version: { type: 'integer', const: 1 },
        dataset_id: { type: 'string', const: 'sv-official-homicides' }, title: { type: 'string' },
        as_of: { type: 'string', format: 'date' }, generated_at: { type: 'string', format: 'date-time' },
        coverage_start_year: { type: 'integer', const: 2010 }, returned_rows: { type: 'integer', minimum: 0 },
        comparability_note: { type: 'string' }, limitations: { type: 'string' },
      },
    },
    OfficialStatisticsResponse: {
      type: 'object', additionalProperties: false, required: ['data', 'meta'],
      properties: { data: { type: 'array', items: ref('OfficialStatisticsRow') }, meta: ref('OfficialStatisticsMetadata') },
    },
    AutomaticNewsLocationMention: {
      type: 'object', additionalProperties: false,
      required: ['method', 'department', 'paragraph_id'],
      allOf: [{ if: { properties: { method: { const: 'source_municipality_reference' } }, required: ['method'] },
        then: { required: ['resolution'] }, else: { not: { required: ['resolution'] } } }],
      properties: {
        method: { type: 'string', enum: ['source_explicit_department', 'source_municipality_reference'] },
        department: { type: 'string' }, paragraph_id: { type: 'string', pattern: '^p[1-9][0-9]{0,2}$' },
        resolution: { type: 'object', additionalProperties: false, required: ['type','municipality','municipality_kind','reference_version','source_url'], properties: {
          type: { type: 'string', const: 'municipality_to_department' }, municipality: { type: 'string' },
          municipality_kind: { type: 'string', enum: ['district','municipality'] }, reference_version: { type: 'string' }, source_url: { type: 'string', format: 'uri' },
        } },
      },
    },
    AutomaticNewsLocation: {
      type: 'object', additionalProperties: false,
      description: 'Ubicación administrativa automática SIN REVISAR, separada de la ficha. Los métodos source_* leen directamente el texto mediante reglas sin llamar a Gemini ni exigir una ficha completa. Los métodos gemini_* conservan su procedencia anterior. No confirma el lugar de una violación. Para una referencia con selección gemini_grounded_scope, evidence.content_sha256 debe coincidir con el hash de esa selección; las decisiones humanas prevalecen.',
      required: ['candidate_id','source_url','publisher','published_date','review_status','human_reviewed','method','extracted_at','source_checked_at','evidence'],
      allOf: [
        { if: { properties: { method: { enum: ['gemini_municipality_reference', 'source_municipality_reference'] } }, required: ['method'] }, then: { required: ['resolution'] }, else: { not: { required: ['resolution'] } } },
        { if: { properties: { method: { const: 'gemini_department_corroborated' } }, required: ['method'] }, then: { required: ['corroboration'] }, else: { not: { required: ['corroboration'] } } },
        { if: { properties: { method: { enum: ['source_explicit_department', 'source_municipality_reference', 'source_administrative_mentions'] } }, required: ['method'] }, then: { required: ['rule_version'] }, else: { not: { required: ['rule_version'] } } },
        { if: { properties: { method: { const: 'source_administrative_mentions' } }, required: ['method'] },
          then: { required: ['mentions'], properties: { rule_version: { const: 2 }, evidence: { not: { required: ['paragraph_id'] } } }, not: { required: ['department'] } },
          else: { required: ['department'], not: { required: ['mentions'] }, properties: { evidence: { required: ['paragraph_id'] } } } },
      ],
      properties: {
        candidate_id: { type: 'string', pattern: '^[a-f0-9]{64}$' },
        source_url: { type: 'string', format: 'uri', maxLength: 4096 },
        publisher: { type: 'string', minLength: 3, maxLength: 200 },
        published_date: { type: 'string', format: 'date' }, department: { type: 'string' },
        review_status: { type: 'string', const: 'automated_unreviewed' }, human_reviewed: { type: 'boolean', const: false },
        method: { type: 'string', enum: ['gemini_explicit_department','gemini_municipality_reference','gemini_department_corroborated','source_explicit_department','source_municipality_reference','source_administrative_mentions'] },
        rule_version: { type: 'integer', enum: [1, 2], description: 'Obligatoria sólo para métodos source_*. La regla 2 se admite únicamente en colecciones schema_version 5 y es obligatoria para menciones múltiples.' },
        mentions: { type: 'array', minItems: 2, maxItems: 14, items: ref('AutomaticNewsLocationMention'), description: 'Una mención por departamento distinto; el validador impide duplicados y comprueba la correspondencia municipal oficial. No establece un departamento principal. Una noticia puede contarse en varios departamentos y una sola vez en el total de noticias.' },
        resolution: { type: 'object', additionalProperties: false, required: ['type','municipality','municipality_kind','reference_version','source_url'], properties: {
          type: { type: 'string', const: 'municipality_to_department' }, municipality: { type: 'string' },
          municipality_kind: { type: 'string', enum: ['district','municipality'] }, reference_version: { type: 'string' }, source_url: { type: 'string', format: 'uri' },
        } },
        corroboration: {
          type: 'object', additionalProperties: false,
          required: ['strategy','model_paragraph_id','explicit_paragraph_id','rule_version'],
          description: 'Sólo para gemini_department_corroborated en colecciones schema_version 3 o posterior. Los IDs de párrafo deben ser distintos y evidence.paragraph_id coincide con explicit_paragraph_id. No publica el texto de la noticia.',
          properties: {
            strategy: { type: 'string', const: 'same_article_explicit_department' },
            model_paragraph_id: { type: 'string', pattern: '^p[1-9][0-9]{0,2}$' },
            explicit_paragraph_id: { type: 'string', pattern: '^p[1-9][0-9]{0,2}$' },
            rule_version: { type: 'integer', const: 1 },
          },
        },
        extracted_at: { type: 'string', format: 'date-time' }, source_checked_at: { type: 'string', format: 'date-time' },
        evidence: { type: 'object', additionalProperties: false, required: ['content_sha256'], properties: {
          paragraph_id: { type: 'string', pattern: '^p[1-9][0-9]{0,2}$' }, content_sha256: { type: 'string', pattern: '^[a-f0-9]{64}$' },
        } },
      },
    },
    AutomaticNewsLocationCollection: {
      type: 'object', additionalProperties: false,
      description: 'Snapshot público de /archive/locations.json, sin cachés o contadores privados. Los métodos source_* se admiten desde la versión 4; las menciones múltiples y la regla 2 sólo en la versión 5. El método corroborado se admite desde la versión 3. Las versiones anteriores siguen siendo compatibles.',
      required: ['schema_version','generated_at','locations'],
      properties: {
        schema_version: { type: 'integer', enum: [1, 2, 3, 4, 5] }, generated_at: { type: 'string', format: 'date-time' },
        locations: { type: 'array', maxItems: 100000, items: ref('AutomaticNewsLocation') },
      },
      allOf: [
        { if: { properties: { schema_version: { enum: [1, 2] } }, required: ['schema_version'] }, then: { properties: { locations: { items: { not: { properties: { method: { const: 'gemini_department_corroborated' } }, required: ['method'] } } } } } },
        { if: { properties: { schema_version: { enum: [1, 2, 3] } }, required: ['schema_version'] }, then: { properties: { locations: { items: { not: { properties: { method: { enum: ['source_explicit_department', 'source_municipality_reference', 'source_administrative_mentions'] } }, required: ['method'] } } } } } },
        { if: { properties: { schema_version: { enum: [1, 2, 3, 4] } }, required: ['schema_version'] }, then: { properties: { locations: { items: { not: { anyOf: [
          { properties: { method: { const: 'source_administrative_mentions' } }, required: ['method'] },
          { properties: { rule_version: { const: 2 } }, required: ['rule_version'] },
        ] } } } } } },
      ],
    },
    NewsProcessingStatus: {
      type: 'string', enum: [...NEWS_PROCESSING_STATUSES, 'reviewed', 'automatically_published'],
      description: 'Estado operativo publicado: pending = pendiente de procesar; generated = ficha generada pendiente de validación; automatically_published = ficha publicada tras validación automática, sin revisión humana; source_unavailable = no se pudo leer la fuente; error = extracción no completada; unknown = estado no disponible o desactualizado. reviewed sólo procede de una ficha con revisión humana y vinculada a la noticia. automatically_published requiere una ficha versión 2 vinculada al hash de la fuente de la selección. generated no revela campos del borrador ni implica aprobación.',
    },
    NewsProcessingEntry: {
      type: 'object', additionalProperties: false,
      required: ['candidate_id', 'source_url', 'publisher', 'published_date', 'selection_content_sha256', 'status'],
      allOf: [{ if: { properties: { status: { const: 'generated' } }, required: ['status'] }, then: { properties: { selection_content_sha256: { type: 'string' } } } }],
      properties: {
        candidate_id: { type: 'string', pattern: '^[a-f0-9]{64}$' },
        source_url: { type: 'string', format: 'uri', maxLength: 4096 },
        publisher: { type: 'string', minLength: 3, maxLength: 200 },
        published_date: { type: 'string', format: 'date' },
        selection_content_sha256: { type: ['string', 'null'], pattern: '^[a-f0-9]{64}$', description: 'Hash ya público de la selección automática; null para una selección editorial. No es el hash de un borrador privado.' },
        status: { type: 'string', enum: NEWS_PROCESSING_STATUSES },
      },
    },
    NewsProcessingCollection: {
      type: 'object', additionalProperties: false,
      description: 'Snapshot público mínimo /archive/processing.json. No contiene campos, cuerpos, modelos, errores detallados ni notas de la cola privada. Entradas ausentes o sin vinculación vigente se interpretan como unknown, nunca pending.',
      required: ['schema_version', 'generated_at', 'entries'],
      properties: {
        schema_version: { type: 'integer', const: 1 }, generated_at: { type: 'string', format: 'date-time' },
        entries: { type: 'array', maxItems: 100000, items: ref('NewsProcessingEntry') },
      },
    },
    NewsSelection: { description: 'Selección de pertinencia en derechos humanos de El Salvador. El indicio por palabras del título no basta. La selección automática permanece sin revisión humana y no aprueba la ficha ni confirma una violación.', oneOf: [
      { type: 'object', additionalProperties: false, required: ['method', 'review_status', 'human_reviewed', 'jurisdiction', 'relevance', 'classified_at', 'model', 'prompt_version', 'evidence'], properties: {
        method: { type: 'string', const: 'gemini_grounded_scope' }, review_status: { type: 'string', const: 'automated_unreviewed' }, human_reviewed: { type: 'boolean', const: false }, jurisdiction: { type: 'string', const: 'SV' }, relevance: { type: 'string', const: 'relevant' }, classified_at: { type: 'string', format: 'date-time' }, model: { type: 'string', pattern: '^gemini-[A-Za-z0-9_.-]{1,90}$' }, prompt_version: { type: 'string', enum: NEWS_SELECTION_PROMPTS }, evidence: { type: 'object', additionalProperties: false, required: ['content_sha256', 'jurisdiction_paragraph_ids', 'relevance_paragraph_ids'], properties: { content_sha256: { type: 'string', pattern: '^[a-f0-9]{64}$' }, ...Object.fromEntries(['jurisdiction_paragraph_ids', 'relevance_paragraph_ids'].map(key => [key, { type: 'array', minItems: 1, maxItems: 25, uniqueItems: true, items: { type: 'string', pattern: '^p[1-9][0-9]{0,2}$' } }])) } },
      } },
      { type: 'object', additionalProperties: false, required: ['method', 'review_status', 'human_reviewed', 'jurisdiction', 'relevance', 'reviewed_by', 'reviewed_at'], properties: { method: { type: 'string', const: 'editorial_scope' }, review_status: { type: 'string', const: 'editorially_reviewed' }, human_reviewed: { type: 'boolean', const: true }, jurisdiction: { type: 'string', const: 'SV' }, relevance: { type: 'string', const: 'relevant' }, reviewed_by: { type: 'string', pattern: '^[A-Za-z0-9][A-Za-z0-9-]{0,38}$' }, reviewed_at: { type: 'string', format: 'date' } } },
    ] },
    NewsSelectionGate: { type: 'object', additionalProperties: false, required: ['version', 'scope', 'candidates', 'selected'], description: 'selected cuenta noticias disponibles. En v2 evaluated cuenta referencias analizadas, no todo el archivo: evaluated = relevant + excluded + uncertain; candidates = evaluated + pending. selected puede diferir de relevant tras una decisión editorial posterior a evaluated_at. Las pendientes no son descartes ni una estimación de prevalencia nacional.', properties: { version: { type: 'integer', enum: [1, 2] }, scope: { type: 'string', const: 'human_rights_el_salvador' }, candidates: { type: 'integer', minimum: 0 }, selected: { type: 'integer', minimum: 0 }, evaluated: { type: 'integer', minimum: 0 }, relevant: { type: 'integer', minimum: 0 }, excluded: { type: 'integer', minimum: 0 }, uncertain: { type: 'integer', minimum: 0 }, pending: { type: 'integer', minimum: 0 }, evaluated_at: { type: 'string', format: 'date-time' } }, allOf: [{ if: { properties: { version: { const: 2 } } }, then: { required: ['evaluated', 'relevant', 'excluded', 'uncertain', 'pending', 'evaluated_at'] }, else: { not: { anyOf: ['evaluated', 'relevant', 'excluded', 'uncertain', 'pending', 'evaluated_at'].map(key => ({ required: [key] })) } } }] },
    NewsDossierAutomation: { type: 'object', additionalProperties: false, required: ['policy_version', 'source_content_sha256', 'validated_at', 'method', 'evaluator_model', 'corroboration'], properties: { policy_version: { type: 'string', const: 'auto-news-v1' }, source_content_sha256: { type: 'string', pattern: '^[a-f0-9]{64}$' }, validated_at: { type: 'string', format: 'date-time' }, method: { type: 'string', const: 'gemini_evidence_review' }, evaluator_model: { type: 'string', minLength: 1, maxLength: 100 }, corroboration: { type: 'string', const: 'single_source' } } },
    NewsDossier: { type: 'object', additionalProperties: false, allOf: [{ if: { properties: { schema_version: { const: 2 } }, required: ['schema_version'] }, then: { required: ['automation'], properties: { human_reviewed: { const: false }, reviewed_by: { type: 'null' }, reviewed_at: { type: 'null' }, review_notes: { type: 'null' }, extraction_status: { const: 'processed' }, classification: { properties: { jurisdiction: { const: 'SV' }, relevance: { const: 'relevant' } } } } }, else: { not: { required: ['automation'] } } }], required: ['schema_version', 'kind', 'candidate_id', 'source_url', 'publisher', 'source_title', 'published_date', 'extraction_status', 'classification', 'fields', 'date_precision', 'evidence', 'human_reviewed', 'reviewed_by', 'reviewed_at', 'review_notes'], description: 'Ficha universal de noticia. Versión 1: revisión humana o placeholder pendiente; versión 2: publicación automática con automation y human_reviewed=false, atribuida a fuente única sin corroboración independiente. Sin ficha publicada todos los campos estructurados son pending; esto no significa ausencia en la fuente. Consulte processing_status para distinguir procesamiento y revisión según el snapshot público de estados. Una ficha revisada puede ser parcial o declarar no pertinencia. No equivale a un caso ni se suma a estadísticas de violaciones.', properties: {
      schema_version: { type: 'integer', enum: [1, 2] }, kind: { type: 'string', const: 'news_dossier' }, candidate_id: { type: 'string', pattern: '^[a-f0-9]{64}$' },
      source_url: { type: 'string', format: 'uri', maxLength: 4096 }, publisher: { type: 'string', minLength: 3, maxLength: 200 }, source_title: { type: 'string', minLength: 3, maxLength: 500 }, published_date: { type: ['string', 'null'], format: 'date' },
      extraction_status: { type: 'string', enum: ['pending', 'processed', 'source_unavailable', 'error'], description: 'La ficha pública devuelve pending si no hay publicación validada; las publicaciones automáticas y humanas devuelven processed. Para el estado operativo use processing_status, separado del contenido de la ficha.' },
      classification: { type: 'object', additionalProperties: false, required: ['jurisdiction', 'relevance', 'record_type'], properties: { jurisdiction: { type: 'string', enum: ['SV', 'other', 'unclear'] }, relevance: { type: 'string', enum: ['relevant', 'not_relevant', 'unclear'] }, record_type: { type: ['string', 'null'], enum: ['incident', 'aggregate', 'publication', null] } } },
      fields: { type: 'object', additionalProperties: false, required: NEWS_DOSSIER_FIELDS, properties: Object.fromEntries(NEWS_DOSSIER_FIELDS.map(name => [name, newsField(name)])) },
      date_precision: { type: 'string', enum: ['unknown', 'day', 'month_range', 'year_range'] },
      evidence: { type: 'array', minItems: 1, maxItems: 200, items: { type: 'object', required: ['id', 'url', 'locator'], additionalProperties: false, properties: { id: { type: 'string' }, url: { type: 'string', format: 'uri', maxLength: 4096 }, locator: { type: 'string', minLength: 10, maxLength: 1000 } } } },
      human_reviewed: { type: 'boolean' }, reviewed_by: { type: ['string', 'null'] }, reviewed_at: { type: ['string', 'null'], format: 'date' }, review_notes: { type: ['string', 'null'], maxLength: 4000 },
      automation: ref('NewsDossierAutomation'),
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
    News: { type: 'object', required: ['id', 'title', 'publisher', 'source_url', 'published_date', 'review_status', 'scope_hint', 'scope_terms', 'selection', 'news_dossier', 'automatic_location', 'processing_status'], additionalProperties: true, description: 'Referencia bibliográfica seleccionada por pertinencia en derechos humanos de El Salvador con evidencia explícita; no equivale a caso verificado. No contiene el cuerpo del artículo ni borradores privados. processing_status conserva únicamente el estado operativo publicado.', properties: { processing_status: ref('NewsProcessingStatus'), selection: ref('NewsSelection'), automatic_location: { anyOf: [ref('AutomaticNewsLocation'), { type: 'null' }] }, news_dossier: ref('NewsDossier'), scope_hint: { type: 'string', enum: ['el_salvador', 'international_context', 'undetermined'] }, scope_terms: { type: 'array', items: { type: 'string' } }, jurisdiction_status: { type: 'string', const: 'not_reviewed' }, id: { type: 'string' }, title: { type: 'string' }, publisher: { type: 'string' }, source_url: { type: 'string', format: 'uri' }, published_date: { type: 'string', format: 'date' }, review_status: { type: 'string' }, topics: { type: 'array', items: { type: 'string' } }, discovery: { type: 'object', additionalProperties: true } } },
    Pagination: { type: 'object', required: ['total', 'limit', 'offset', 'next_offset', 'next'], properties: { total: { type: 'integer' }, limit: { type: 'integer' }, offset: { type: 'integer' }, next_offset: { type: ['integer', 'null'] }, next: { type: ['string', 'null'] } } },
    Metadata: { type: 'object', required: ['api_version', 'schema_version', 'updated_at', 'license', 'limitations'], additionalProperties: true, properties: { selection_gate: ref('NewsSelectionGate'), public_scope: { type: 'string', const: 'human_rights_el_salvador' }, api_version: { type: 'string', const: '1' }, schema_version: { type: 'integer' }, updated_at: { type: ['string', 'null'] }, license: { type: 'string', format: 'uri' }, attribution: { type: 'string' }, limitations: { type: 'string' }, coverage: { type: 'object', additionalProperties: true }, paper: { type: 'object', additionalProperties: true }, review_notice: { type: 'string' }, news_dossiers_status: { type: 'string', enum: ['available', 'unavailable'] }, news_processing_status: { type: 'string', enum: ['available', 'unavailable'] }, news_processing_updated_at: { type: ['string', 'null'], format: 'date-time', description: 'Fecha del snapshot público de estados; null si no está disponible. No es la fecha de revisión de una ficha.' } } },
    Error: { type: 'object', required: ['error'], properties: { error: { type: 'object', required: ['code', 'message'], properties: { code: { type: 'string' }, message: { type: 'string' } } } } },
  } },
};
