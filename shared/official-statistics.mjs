/** Official aggregate statistics are a separate dataset, never documentary case counts. */
export const OFFICIAL_STATISTICS_DEPARTMENTS = Object.freeze([
  'Ahuachapán', 'Santa Ana', 'Sonsonate', 'Chalatenango', 'La Libertad', 'San Salvador',
  'Cuscatlán', 'La Paz', 'Cabañas', 'San Vicente', 'Usulután', 'San Miguel', 'Morazán', 'La Unión',
]);

const hosts = new Set(['transparencia.oj.gob.sv', 'fiscalia.gob.sv', 'www.fiscalia.gob.sv', 'data.unodc.org']);
const object = value => value !== null && typeof value === 'object' && !Array.isArray(value);
const exact = (value, keys) => object(value) && Object.keys(value).length === keys.length && keys.every(key => Object.hasOwn(value, key));
const text = (value, maximum = 4000) => typeof value === 'string' && value.trim().length > 0 && value.length <= maximum && !/[\u0000-\u0008\u000b\u000c\u000e-\u001f]/u.test(value);
const date = value => typeof value === 'string' && /^\d{4}-\d{2}-\d{2}$/.test(value) && !value.startsWith('0000-')
  && Number.isFinite(Date.parse(value)) && new Date(value).toISOString().slice(0, 10) === value;
const instant = value => typeof value === 'string' && /^\d{4}-\d{2}-\d{2}T(?:[01]\d|2[0-3]):[0-5]\d:[0-5]\d(?:\.\d{1,6})?(?:Z|[+-](?:[01]\d|2[0-3]):[0-5]\d)$/.test(value)
  && date(value.slice(0, 10)) && Number.isFinite(Date.parse(value));
const fail = () => { throw new Error('Estadísticas oficiales inválidas'); };
const localDate = value => new Intl.DateTimeFormat('en-CA', { timeZone: 'America/El_Salvador', year: 'numeric', month: '2-digit', day: '2-digit' }).format(value);

function source(value, generatedAt) {
  if (!exact(value, ['id', 'institution', 'title', 'url', 'format', 'retrieved_at', 'content_sha256', 'edition_note'])
      || typeof value.id !== 'string' || !/^[a-z][a-z0-9_-]{0,99}$/.test(value.id)
      || !text(value.institution, 200) || !text(value.title, 600) || !text(value.edition_note)
      || !['pdf', 'html', 'xlsx'].includes(value.format) || !instant(value.retrieved_at)
      || Date.parse(value.retrieved_at) > Date.parse(generatedAt)
      || typeof value.content_sha256 !== 'string' || !/^[a-f0-9]{64}$/.test(value.content_sha256)
      || typeof value.url !== 'string' || value.url.length > 4096) fail();
  let url;
  try { url = new URL(value.url); } catch { fail(); }
  if (url.protocol !== 'https:' || !hosts.has(url.hostname) || url.username || url.password || url.port || url.pathname === '/') fail();
}

function observation(value, extraKeys = [], month = false) {
  if (!exact(value, [...extraKeys, 'value', 'status', 'locator'])
      || !['available', 'not_available', ...(month ? ['outside_cutoff'] : [])].includes(value.status)) fail();
  if (value.status === 'available') {
    if (!Number.isSafeInteger(value.value) || value.value < 0 || !text(value.locator, 1200)) fail();
  } else if (value.value !== null || value.locator !== null) fail();
}

/** Parse the complete public snapshot; no partial salvage, coercion or missing-to-zero conversion. */
export function parseOfficialStatistics(value) {
  if (!exact(value, ['schema_version', 'dataset_id', 'title', 'as_of', 'generated_at', 'coverage_start_year', 'comparability_note', 'sources', 'periods'])
      || value.schema_version !== 1 || value.dataset_id !== 'sv-official-homicides'
      || !text(value.title, 600) || !text(value.comparability_note, 8000)
      || !date(value.as_of) || !instant(value.generated_at) || value.coverage_start_year !== 2010
      || Date.parse(value.generated_at) > Date.now() + 60_000
      || value.as_of > localDate(new Date(value.generated_at))
      || !Array.isArray(value.sources) || value.sources.length < 1 || value.sources.length > 256
      || !Array.isArray(value.periods)) fail();
  const lastYear = Number(value.as_of.slice(0, 4));
  const cutoffMonth = Number(value.as_of.slice(5, 7));
  if (lastYear < 2010 || lastYear > new Date().getUTCFullYear() || value.periods.length !== lastYear - 2009) fail();
  const sourceIds = new Set();
  for (const entry of value.sources) {
    source(entry, value.generated_at);
    if (sourceIds.has(entry.id)) fail();
    sourceIds.add(entry.id);
  }
  const years = new Set();
  for (const period of value.periods) {
    if (!exact(period, ['year', 'start_date', 'end_date', 'completeness', 'source_id', 'indicator', 'unit', 'methodology', 'notes', 'national', 'departments', 'months'])
        || !Number.isInteger(period.year) || period.year < 2010 || period.year > lastYear || years.has(period.year)
        || !date(period.start_date) || !date(period.end_date) || period.start_date > period.end_date
        || !period.start_date.startsWith(`${period.year}-`) || !period.end_date.startsWith(`${period.year}-`)
        || period.end_date > value.as_of || !['complete', 'partial', 'unavailable'].includes(period.completeness)
        || !sourceIds.has(period.source_id) || !['homicides', 'intentional_homicides'].includes(period.indicator)
        || !['persons', 'source_reported_homicides'].includes(period.unit) || !text(period.methodology, 8000)
        || !Array.isArray(period.notes) || period.notes.length > 30 || !period.notes.every(note => text(note))
        || !Array.isArray(period.departments) || period.departments.length !== 14) fail();
    years.add(period.year);
    if (period.completeness === 'complete' && (period.start_date !== `${period.year}-01-01` || period.end_date !== `${period.year}-12-31`)) fail();
    observation(period.national);
    const departments = new Set();
    for (const row of period.departments) {
      observation(row, ['department']);
      if (!OFFICIAL_STATISTICS_DEPARTMENTS.includes(row.department) || departments.has(row.department)) fail();
      departments.add(row.department);
    }
    if (period.months !== null) {
      if (!Array.isArray(period.months) || period.months.length !== 12) fail();
      const months = new Set();
      for (const row of period.months) {
        observation(row, ['month'], true);
        if (!Number.isInteger(row.month) || row.month < 1 || row.month > 12 || months.has(row.month)) fail();
        months.add(row.month);
        const future = period.year === lastYear && row.month > cutoffMonth;
        if (future !== (row.status === 'outside_cutoff')) fail();
        if (row.status === 'available' && (row.month < Number(period.start_date.slice(5, 7)) || row.month > Number(period.end_date.slice(5, 7)))) fail();
      }
    }
    if (period.completeness === 'unavailable' && [period.national, ...period.departments, ...(period.months ?? [])].some(row => row.value !== null)) fail();
    if (period.completeness !== 'unavailable' && period.national.status !== 'available') fail();
    // Compare complete components within this same source and period only.
    // Incomplete breakdowns remain incomplete; never fill or replace a value.
    if (period.national.status === 'available' && period.departments.every(row => row.status === 'available')
        && period.departments.reduce((sum, row) => sum + row.value, 0) !== period.national.value) fail();
    if (period.months !== null && period.national.status === 'available') {
      const firstMonth = Number(period.start_date.slice(5, 7));
      const lastMonth = Number(period.end_date.slice(5, 7));
      const lastDay = new Date(Date.UTC(period.year, lastMonth, 0)).getUTCDate();
      const wholeMonths = period.start_date.endsWith('-01') && Number(period.end_date.slice(8, 10)) === lastDay;
      const coveredMonths = period.months.filter(row => row.month >= firstMonth && row.month <= lastMonth);
      if (wholeMonths && coveredMonths.every(row => row.status === 'available')
          && coveredMonths.reduce((sum, row) => sum + row.value, 0) !== period.national.value) fail();
    }
  }
  return structuredClone(value);
}
