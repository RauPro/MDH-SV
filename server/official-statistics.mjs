import { readFileSync } from 'node:fs';
import { z } from 'zod';
import { ApiError } from './public-data.mjs';
import { OFFICIAL_STATISTICS_DEPARTMENTS, parseOfficialStatistics } from '../shared/official-statistics.mjs';

export const OFFICIAL_STATISTICS_NOTICE = 'Agregados oficiales independientes de las noticias y fichas de MDH-SV, no casos individuales. Conservar fuente, unidad, período y revisiones; no sumar instituciones, años, total nacional y departamentos. null significa dato no disponible, nunca cero. Un período parcial no es un cierre anual.';
export const officialStatisticsSchema = z.object({
  year: z.number().int().min(2010).max(9999).optional(),
  department: z.enum(['national', ...OFFICIAL_STATISTICS_DEPARTMENTS]).optional(),
}).strict();
const invalidQuery = () => { throw new ApiError(400, 'invalid_query', 'Estadísticas oficiales: use year y department con los valores exactos de /api/v1/openapi.json.'); };

export function parseOfficialStatisticsFilters(params) {
  const input = {};
  for (const [key, value] of params) {
    if (!['year', 'department'].includes(key) || Object.hasOwn(input, key)) invalidQuery();
    if (key === 'year' && !/^\d{4}$/.test(value)) invalidQuery();
    input[key] = key === 'year' ? Number(value) : value;
  }
  const parsed = officialStatisticsSchema.safeParse(input);
  if (!parsed.success) invalidQuery();
  return parsed.data;
}

export function loadOfficialStatistics(read = readFileSync) {
  try {
    const bytes = read(new URL('../apps/web/public/statistics/homicides.json', import.meta.url));
    if (bytes.byteLength > 2_000_000) throw new Error('oversized_snapshot');
    return parseOfficialStatistics(JSON.parse(bytes.toString('utf8')));
  } catch {
    throw new ApiError(503, 'official_statistics_unavailable', 'No se pudo verificar el snapshot de estadísticas oficiales de este despliegue.');
  }
}

export function getOfficialStatistics(filters = {}, payload) {
  const parsed = officialStatisticsSchema.safeParse(filters);
  if (!parsed.success) invalidQuery();
  let snapshot;
  try { snapshot = payload === undefined ? loadOfficialStatistics() : parseOfficialStatistics(payload); }
  catch { throw new ApiError(503, 'official_statistics_unavailable', 'No se pudo verificar el snapshot de estadísticas oficiales de este despliegue.'); }
  const query = parsed.data;
  if (query.year !== undefined && query.year > Number(snapshot.as_of.slice(0, 4))) invalidQuery();
  const sources = new Map(snapshot.sources.map(source => [source.id, source]));
  const rows = [];
  for (const period of [...snapshot.periods].sort((a, b) => a.year - b.year)) {
    if (query.year !== undefined && period.year !== query.year) continue;
    const { national, departments, source_id, months, ...metadata } = period;
    for (const row of [{ department: 'national', ...national }, ...departments]) {
      if (query.department !== undefined && row.department !== query.department) continue;
      rows.push({ ...metadata, ...row, territory_level: row.department === 'national' ? 'national' : 'department',
        months: row.department === 'national' ? months : null, source: sources.get(source_id) });
    }
  }
  return {
    data: rows,
    meta: { api_version: '1', schema_version: snapshot.schema_version, dataset_id: snapshot.dataset_id,
      title: snapshot.title, as_of: snapshot.as_of, generated_at: snapshot.generated_at,
      coverage_start_year: snapshot.coverage_start_year, returned_rows: rows.length,
      comparability_note: snapshot.comparability_note, limitations: OFFICIAL_STATISTICS_NOTICE },
  };
}
