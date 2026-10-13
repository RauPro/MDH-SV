import { useEffect, useState } from 'react';
import { ArrowUpRight, Download } from 'lucide-react';
import {
  OFFICIAL_STATISTICS_DEPARTMENTS, parseOfficialStatistics,
  type OfficialStatistics as StatisticsData, type OfficialStatisticsPeriod, type OfficialStatisticsSource,
} from '../../../../shared/official-statistics.mjs';
import { formatDate } from '../catalog';
import './OfficialStatistics.css';

const DATA_URL = '/statistics/homicides.json';
const NATIONAL = 'Nacional';
const number = (value: number) => value.toLocaleString('es-SV');
const instantLabel = (value: string) => new Intl.DateTimeFormat('es-SV', { day: 'numeric', month: 'long', year: 'numeric', hour: '2-digit', minute: '2-digit', timeZone: 'America/El_Salvador' }).format(new Date(value));
const monthName = (month: number) => new Intl.DateTimeFormat('es-SV', { month: 'long', timeZone: 'UTC' }).format(new Date(Date.UTC(2020, month - 1, 1)));
const unitLabel = (period: OfficialStatisticsPeriod) => period.unit === 'persons' ? 'personas registradas por la fuente' : 'homicidios reportados por la fuente';
const indicatorLabel = (period: OfficialStatisticsPeriod) => period.indicator === 'intentional_homicides' ? 'Homicidios intencionales' : 'Homicidios';
const periodLabel = (period: OfficialStatisticsPeriod) => `${formatDate(period.start_date)} al ${formatDate(period.end_date)}`;
const completenessLabel = (period: OfficialStatisticsPeriod) => ({ complete: 'Período completo', partial: 'Período parcial', unavailable: 'Sin datos verificados' })[period.completeness];
const observation = (period: OfficialStatisticsPeriod, territory: string) => territory === NATIONAL ? period.national : period.departments.find(row => row.department === territory)!;
const valueLabel = (row: { value: number | null; status: string }) => row.status === 'outside_cutoff' ? 'Fuera del corte' : row.status === 'available' && row.value !== null ? number(row.value) : 'Sin datos';

function SourceLink({ source, locator }: { source: OfficialStatisticsSource | undefined; locator: string | null }) {
  return source ? <><a className="official-statistics-source-link" href={source.url} target="_blank" rel="noopener noreferrer">{source.institution} <ArrowUpRight size={12} aria-label="abre en otra pestaña"/></a>{locator && <small>{locator}</small>}</> : <>Sin fuente disponible</>;
}

function SourceDetails({ source, locators }: { source: OfficialStatisticsSource; locators: string[] }) {
  return <article className="official-statistics-source">
    <h4><a href={source.url} target="_blank" rel="noopener noreferrer">{source.title} <ArrowUpRight size={13} aria-label="abre en otra pestaña"/></a></h4>
    <p>{source.institution} · {source.format.toUpperCase()}</p>
    {locators.length > 0 && <p><strong>Localizador:</strong> {locators.join(' · ')}</p>}
    <p>{source.edition_note}</p>
    <details><summary>Referencia e integridad del documento</summary><dl>
      <dt>Documento consultado el</dt><dd>{instantLabel(source.retrieved_at)} (El Salvador)</dd>
      <dt>Huella SHA-256</dt><dd><code>{source.content_sha256}</code></dd>
    </dl><p>La huella identifica la versión consultada del documento; no acredita por sí sola sus cifras.</p></details>
  </article>;
}

export function OfficialStatistics() {
  const [data, setData] = useState<StatisticsData | null>(null);
  const [error, setError] = useState(false);
  const [attempt, setAttempt] = useState(0);
  const [selectedYear, setSelectedYear] = useState<number | null>(null);
  const [territory, setTerritory] = useState(NATIONAL);
  useEffect(() => {
    const controller = new AbortController();
    const timeout = window.setTimeout(() => controller.abort(), 12000);
    let active = true;
    fetch(DATA_URL, { signal: controller.signal }).then(async response => {
      if (!response.ok) throw new Error('Statistics unavailable');
      return parseOfficialStatistics(await response.json());
    }).then(result => { if (active) setData(result); })
      .catch(() => { if (active) setError(true); })
      .finally(() => window.clearTimeout(timeout));
    return () => { active = false; window.clearTimeout(timeout); controller.abort(); };
  }, [attempt]);

  const periods = data ? [...data.periods].sort((a, b) => a.year - b.year) : [];
  const year = selectedYear ?? periods.at(-1)?.year;
  const period = periods.find(row => row.year === year);
  const selected = period ? observation(period, territory) : undefined;
  const source = data?.sources.find(row => row.id === period?.source_id);
  const latestNational = periods.findLast(row => row.national.status === 'available');
  const sourcesById = new Map(data?.sources.map(row => [row.id, row]));
  const departmentsAvailable = period?.departments.some(row => row.status === 'available');
  const selectedLocators = [...new Set([
    selected?.locator,
    ...(territory === NATIONAL ? period?.months?.filter(row => row.status === 'available').map(row => row.locator) ?? [] : []),
  ].filter((value): value is string => Boolean(value)))];

  return <section className="official-statistics" aria-labelledby="official-statistics-title">
    <div className="section-heading"><div className="eyebrow">SERIES INSTITUCIONALES · EL SALVADOR</div>
      <h2 id="official-statistics-title">Estadísticas oficiales de homicidios</h2>
      <p>Cifras agregadas publicadas por instituciones, con su período, unidad y documento de origen. Se consultan por separado de las noticias y los reportes del monitor; no se suman entre colecciones.</p>
    </div>
    {error ? <div className="empty" role="alert"><h3>No pudimos cargar las estadísticas oficiales</h3><p>Intenta nuevamente para consultar las cifras y sus fuentes.</p><button onClick={() => { setError(false); setAttempt(value => value + 1); }}>Reintentar estadísticas</button></div>
      : !data || !period || !selected ? <p className="empty" role="status">Cargando las estadísticas oficiales y sus fuentes…</p>
        : <>
          <p className="official-statistics-updated">Consulta: {data.coverage_start_year} al {formatDate(data.as_of)}. Actualización del conjunto: {instantLabel(data.generated_at)} (El Salvador).</p>
          {latestNational && <p className="filter-note">Último período nacional reportado: {periodLabel(latestNational)}{latestNational.completeness === 'partial' ? ' (parcial)' : ''}.</p>}
          <div className="official-statistics-filters">
            <label>Año<select value={year} onChange={event => setSelectedYear(Number(event.target.value))}>{[...periods].reverse().map(row => <option key={row.year} value={row.year}>{row.year}</option>)}</select></label>
            <label>Departamento<select value={territory} onChange={event => setTerritory(event.target.value)}><option>{NATIONAL}</option>{OFFICIAL_STATISTICS_DEPARTMENTS.map(department => <option key={department}>{department}</option>)}</select></label>
          </div>
          <section className="official-statistics-summary" aria-label="Resumen de la consulta">
            <div className="official-statistics-value"><h3>{territory} · {year}</h3><strong>{valueLabel(selected)}</strong><p>{unitLabel(period)}</p><p>{indicatorLabel(period)}</p></div>
            <div className="official-statistics-context"><span className="official-statistics-badge" data-coverage={selected.status === 'available' ? period.completeness : 'unavailable'}>{selected.status === 'available' ? completenessLabel(period) : 'Sin datos disponibles'}</span>
              <p><strong>Período de la fuente:</strong> {periodLabel(period)}.</p>
              <p><SourceLink source={source} locator={selected.locator}/></p>
              {selected.status !== 'available' && <p>No hay una cifra verificada para este territorio y período en las fuentes incorporadas. La ausencia de datos no equivale a cero.</p>}
            </div>
          </section>
          <aside className="official-statistics-note"><strong>Lee cada serie con su metodología.</strong> {data.comparability_note} Los períodos parciales no representan un año completo.</aside>
          <section className="official-statistics-section" aria-labelledby="official-methodology-title"><h3 id="official-methodology-title">Qué mide esta cifra</h3><p>{period.methodology}</p>{period.notes.map((note, index) => <p key={index}>{note}</p>)}</section>

          <section className="official-statistics-section" aria-labelledby="official-history-title"><h3 id="official-history-title">Historial · {territory}</h3><p>Selecciona un año para consultar su fuente y metodología. Cada fila conserva la unidad publicada; no se presenta un total entre años.</p>
            <div className="official-statistics-table-wrap"><table><caption>Serie {data.coverage_start_year}–{periods.at(-1)!.year} · {territory}</caption><thead><tr><th scope="col">Año</th><th scope="col">Cifra</th><th scope="col">Unidad</th><th scope="col">Período</th><th scope="col">Fuente y localizador</th></tr></thead><tbody>
              {periods.map(row => { const value = observation(row, territory); return <tr key={row.year} data-selected={row.year === year}><th scope="row"><button className="official-statistics-link" onClick={() => setSelectedYear(row.year)} aria-label={`Consultar ${row.year}`}>{row.year}</button></th><td data-numeric="true">{valueLabel(value)}</td><td>{unitLabel(row)}</td><td>{periodLabel(row)}<small>{completenessLabel(row)}</small></td><td><SourceLink source={sourcesById.get(row.source_id)} locator={value.locator}/></td></tr>; })}
            </tbody></table></div>
          </section>

          <section className="official-statistics-section" aria-labelledby="official-departments-title"><h3 id="official-departments-title">Departamentos · {year}</h3>
            <p>{departmentsAvailable ? 'Desglose publicado para el período seleccionado. El dato nacional se consulta por separado y no se añade al desglose.' : 'La fuente incorporada no ofrece un desglose verificado por departamento para este período. Se conserva «Sin datos» en los 14 departamentos; no se distribuye el total nacional.'}</p>
            <div className="official-statistics-table-wrap"><table><caption>14 departamentos · {periodLabel(period)} · {unitLabel(period)}</caption><thead><tr><th scope="col">Departamento</th><th scope="col">Cifra</th><th scope="col">Localizador</th></tr></thead><tbody>
              {period.departments.map(row => <tr key={row.department} data-selected={row.department === territory}><th scope="row"><button className="official-statistics-link" onClick={() => setTerritory(row.department)}>{row.department}</button></th><td data-numeric="true">{valueLabel(row)}</td><td>{row.locator ?? 'Sin desglose disponible'}</td></tr>)}
            </tbody></table></div>
          </section>

          {period.months && <section className="official-statistics-section" aria-labelledby="official-months-title"><h3 id="official-months-title">Meses · {year} · Nacional</h3><p>Este detalle mensual corresponde al país completo. «Sin datos» indica meses sin cifra disponible; «Fuera del corte» identifica meses posteriores a {formatDate(data.as_of)}. No se calculan proyecciones para completar el año.</p>
            <div className="official-statistics-table-wrap"><table><caption>Detalle mensual nacional · {year} · {unitLabel(period)}</caption><thead><tr><th scope="col">Mes</th><th scope="col">Cifra</th><th scope="col">Localizador</th></tr></thead><tbody>
              {period.months.map(row => <tr key={row.month}><th scope="row">{monthName(row.month)}</th><td data-numeric="true">{valueLabel(row)}</td><td>{row.locator ?? (row.status === 'outside_cutoff' ? 'Posterior a la fecha de corte' : 'Sin publicación incorporada')}</td></tr>)}
            </tbody></table></div>
          </section>}

          <section className="official-statistics-section" aria-labelledby="official-sources-title"><h3 id="official-sources-title">Fuente del período seleccionado</h3><p>Contrasta la cifra con el documento original y su localizador antes de reutilizarla.</p><div className="official-statistics-sources">{source && <SourceDetails source={source} locators={selectedLocators}/>}</div>
            <div className="downloads"><a href={DATA_URL} download><Download size={15}/>Conjunto completo · JSON</a><a href="/statistics/homicides.csv" download><Download size={15}/>Conjunto completo · CSV</a><a href="/statistics/manifest.json" download><Download size={15}/>Integridad de las descargas</a></div>
          </section>
        </>}
  </section>;
}
