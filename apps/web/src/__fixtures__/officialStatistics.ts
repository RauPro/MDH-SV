import { OFFICIAL_STATISTICS_DEPARTMENTS, type OfficialStatistics, type OfficialStatisticsValue } from '../../../../shared/official-statistics.mjs';

const observed = (value: number | null, locator: string): OfficialStatisticsValue => value === null
  ? { value: null, status: 'not_available', locator: null }
  : { value, status: 'available', locator };

// Synthetic counts for interaction tests; these are never published as official data.
export const officialStatistics: OfficialStatistics = {
  schema_version: 1,
  dataset_id: 'sv-official-homicides',
  title: 'Estadísticas oficiales de homicidios',
  as_of: '2026-10-12',
  generated_at: '2026-10-12T12:00:00Z',
  coverage_start_year: 2010,
  comparability_note: 'Las fuentes emplean definiciones distintas. No son una serie homogénea.',
  sources: [
    { id: 'iml-fixture', institution: 'Instituto de Medicina Legal', title: 'Documento de prueba IML', url: 'https://transparencia.oj.gob.sv/es/descargar/3/1/fixture.pdf', format: 'pdf', retrieved_at: '2026-10-12T10:00:00Z', content_sha256: 'a'.repeat(64), edition_note: 'Edición sintética para pruebas de interfaz.' },
    { id: 'fgr-fixture', institution: 'Fiscalía General de la República', title: 'Documento de prueba FGR', url: 'https://www.fiscalia.gob.sv/estadistica-prueba/', format: 'html', retrieved_at: '2026-10-12T10:00:00Z', content_sha256: 'b'.repeat(64), edition_note: 'Edición sintética para pruebas de interfaz.' },
  ],
  periods: Array.from({ length: 17 }, (_, index) => {
    const year = 2010 + index;
    const unavailable = year === 2012;
    const fgr = year >= 2022;
    const current = year === 2026;
    return {
      year,
      start_date: `${year}-01-01`,
      end_date: `${year}-${current ? '08-31' : '12-31'}`,
      completeness: unavailable ? 'unavailable' : current ? 'partial' : 'complete',
      source_id: fgr ? 'fgr-fixture' : 'iml-fixture',
      indicator: fgr ? 'intentional_homicides' : 'homicides',
      unit: fgr ? 'source_reported_homicides' : 'persons',
      methodology: fgr ? 'Conteo de homicidios intencionales comunicado por la institución.' : 'Personas examinadas por el instituto, conforme a la definición del documento.',
      notes: unavailable ? ['Documento incompleto: no se incorporó un total verificable.'] : current ? ['El período disponible termina en agosto; no es un total anual.'] : [],
      national: observed(unavailable ? null : fgr ? current ? 8 : 12 : 10, fgr ? 'Tabla mensual, fila total' : 'Página 4, total nacional'),
      departments: OFFICIAL_STATISTICS_DEPARTMENTS.map(department => ({ department, ...observed(unavailable || fgr ? null : department === 'San Salvador' ? 10 : 0, 'Página 4, cuadro por departamento') })),
      months: fgr ? Array.from({ length: 12 }, (_, monthIndex) => current && monthIndex >= 10
        ? { month: monthIndex + 1, value: null, status: 'outside_cutoff', locator: null }
        : { month: monthIndex + 1, ...observed(current && monthIndex >= 8 ? null : 1, 'Tabla mensual, columna mes') }) : null,
    };
  }),
};
