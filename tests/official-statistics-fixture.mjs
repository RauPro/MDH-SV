import { OFFICIAL_STATISTICS_DEPARTMENTS } from '../shared/official-statistics.mjs';

/** Synthetic aggregates only; these values are not institutional statistics. */
export function officialStatisticsFixture() {
  const missing = () => ({ value: null, status: 'not_available', locator: null });
  const available = (value = 1) => ({ value, status: 'available', locator: 'Tabla sintética, fila de prueba' });
  return {
    schema_version: 1, dataset_id: 'sv-official-homicides', title: 'Fixture sintético de estadísticas oficiales',
    as_of: '2026-10-12', generated_at: '2026-10-12T20:00:00Z', coverage_start_year: 2010,
    comparability_note: 'Datos sintéticos; no mezclar unidades ni sumar instituciones o ámbitos.',
    sources: [{ id: 'fixture-source', institution: 'Institución de prueba', title: 'Documento sintético',
      url: 'https://transparencia.oj.gob.sv/es/descargar/3/1/fixture.pdf', format: 'pdf',
      retrieved_at: '2026-10-12T19:00:00Z', content_sha256: 'a'.repeat(64), edition_note: 'Edición de prueba, sin cifras reales.' }],
    periods: Array.from({ length: 17 }, (_, index) => {
      const year = 2010 + index;
      return { year, start_date: `${year}-01-01`, end_date: `${year}-${year === 2026 ? '08-31' : '12-31'}`,
        completeness: year === 2012 ? 'unavailable' : year === 2026 ? 'partial' : 'complete', source_id: 'fixture-source',
        indicator: 'homicides', unit: 'persons', methodology: 'Método sintético para pruebas de contrato.', notes: [],
        national: year === 2012 ? missing() : available(year === 2026 ? 8 : 12),
        departments: OFFICIAL_STATISTICS_DEPARTMENTS.map((department, position) => ({ department, ...(year === 2012 || position > 0 ? missing() : available(0)) })),
        months: year < 2022 ? null : Array.from({ length: 12 }, (_, monthIndex) => ({ month: monthIndex + 1,
          ...(year !== 2026 || monthIndex < 8 ? available() : monthIndex < 10 ? missing() : { value: null, status: 'outside_cutoff', locator: null }) })),
      };
    }),
  };
}
