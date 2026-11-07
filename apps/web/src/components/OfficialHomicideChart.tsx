import { useEffect, useId, useState } from 'react';
import { parseOfficialStatistics, type OfficialStatistics, type OfficialStatisticsPeriod, type OfficialStatisticsSource } from '../../../../shared/official-statistics.mjs';
import { formatDate } from '../catalog';
import './OfficialHomicideChart.css';

const number = (value: number) => value.toLocaleString('es-SV');
const unitLabel = (period: OfficialStatisticsPeriod) => period.unit === 'persons' ? 'personas registradas por la fuente' : 'homicidios reportados por la fuente';
const valueLabel = (period: OfficialStatisticsPeriod) => period.national.status === 'available' ? number(period.national.value) : 'Sin datos';
const coverageLabel = (period: OfficialStatisticsPeriod) => ({ complete: 'Período completo', partial: 'Período parcial', unavailable: 'Sin datos verificados' })[period.completeness];

export interface OfficialMapContext {
  period: OfficialStatisticsPeriod;
  source: OfficialStatisticsSource;
  asOf: string;
}

export function OfficialHomicideChart({ onContextChange }: { onContextChange?: (context: OfficialMapContext | null) => void } = {}) {
  const titleId = useId();
  const [data, setData] = useState<OfficialStatistics | null>(null);
  const [error, setError] = useState(false);
  const [attempt, setAttempt] = useState(0);
  const [selectedYear, setSelectedYear] = useState<number | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    const timeout = window.setTimeout(() => controller.abort(), 12000);
    let active = true;
    fetch('/statistics/homicides.json', { signal: controller.signal }).then(async response => {
      if (!response.ok) throw new Error('Statistics unavailable');
      return parseOfficialStatistics(await response.json());
    }).then(result => { if (active) setData(result); })
      .catch(() => { if (active) setError(true); })
      .finally(() => window.clearTimeout(timeout));
    return () => { active = false; window.clearTimeout(timeout); controller.abort(); };
  }, [attempt]);

  const periods = data ? [...data.periods].sort((a, b) => a.year - b.year) : [];
  const maximum = Math.max(1, ...periods.flatMap(period => period.national.status === 'available' ? [period.national.value] : []));
  const peak = periods.filter(period => period.completeness === 'complete' && period.national.status === 'available')
    .reduce<OfficialStatisticsPeriod | undefined>((largest, period) => !largest || period.national.value! > largest.national.value! ? period : largest, undefined);
  const selected = periods.find(period => period.year === selectedYear) ?? peak ?? periods[0];
  const source = data?.sources.find(item => item.id === selected?.source_id);
  useEffect(() => {
    onContextChange?.(data && selected && source && !error ? { period: selected, source, asOf: data.as_of } : null);
  }, [data, selected, source, error, onContextChange]);

  return <section className="homicide-chart" aria-labelledby={titleId}>
    <header className="homicide-chart-heading"><div><div className="eyebrow">CONTEXTO HISTÓRICO · EL SALVADOR</div><h3 id={titleId}>Homicidios reportados por fuentes oficiales</h3></div><a href="#estadisticas">Consultar estadísticas y metodología</a></header>
    <p className="homicide-chart-note">Totales nacionales. No son noticias ni casos del mapa. No cambian con sus filtros de noticias. El año seleccionado sólo cambia la capa de homicidios oficiales.</p>
    {error ? <div role="alert"><p>No pudimos cargar el contexto histórico. El mapa conserva sus propios datos.</p><button onClick={() => { setError(false); setAttempt(value => value + 1); }}>Reintentar contexto histórico</button></div>
      : !data || !selected ? <p role="status">Cargando contexto histórico…</p>
        : <>
          <label className="homicide-chart-select">Año de estadísticas oficiales<select value={selected.year} onChange={event => setSelectedYear(Number(event.target.value))}>{periods.map(period => <option key={period.year} value={period.year}>{period.year}{period.completeness === 'partial' ? ' (parcial)' : ''}</option>)}</select></label>
          <p className="homicide-chart-note">Fuentes y unidades distintas; no se presupone una serie homogénea. Los períodos parciales no son totales anuales. Selecciona un año para consultar su fuente.</p>
          <div className="homicide-chart-scroll" role="group" aria-label="Totales nacionales por año">
            <div className="homicide-chart-columns" style={{ gridTemplateColumns: `repeat(${periods.length}, minmax(44px, 1fr))` }}>
              {periods.map(period => <button key={period.year} className="homicide-chart-year" aria-pressed={selected.year === period.year}
                aria-label={`${period.year}: ${period.completeness === 'partial' ? '* ' : ''}${period.national.status === 'available' ? valueLabel(period) : 'S/D (Sin datos)'} ${period.national.status === 'available' ? unitLabel(period) : ''} · ${coverageLabel(period)}`}
                onClick={() => setSelectedYear(period.year)}>
                <span className="homicide-chart-year-label" aria-hidden="true">{period.year}: {period.completeness === 'partial' ? '*' : ''}</span>{' '}
                <span className="homicide-chart-track" aria-hidden="true">
                  {period.national.status === 'available'
                    ? <span className="homicide-chart-bar" data-partial={period.completeness === 'partial'} style={{ height: `${period.national.value / maximum * 100}%` }}><span className="homicide-chart-value">{number(period.national.value)}</span></span>
                    : <span className="homicide-chart-missing">S/D</span>}
                </span>
              </button>)}
            </div>
          </div>
          <p className="homicide-chart-note">Base: 0 · Año de la fuente · * Período parcial · S/D: sin datos, no equivale a cero.</p>
          <section className="homicide-chart-detail" aria-label="Detalle del año seleccionado" aria-live="polite" aria-atomic="true">
            <p><strong>{selected.year}: {valueLabel(selected)}</strong>{selected.national.status === 'available' && <> {unitLabel(selected)}</>} · {coverageLabel(selected)}.</p>
            <p>{formatDate(selected.start_date)} al {formatDate(selected.end_date)}. {source && <a href={source.url} target="_blank" rel="noopener noreferrer">{source.institution}</a>}</p>
            {selected.national.status !== 'available' && <p>La ausencia de datos no equivale a cero.</p>}
            <details><summary>Fuente, metodología y límites</summary>
              {source && <p>{source.title}. {source.edition_note}</p>}
              {selected.national.locator && <p>{selected.national.locator}</p>}
              <p>{selected.methodology}</p>
              {selected.notes.map((note, index) => <p key={index}>{note}</p>)}
              <p>{data.comparability_note}</p>
            </details>
          </section>
          <p className="homicide-chart-note">Corte del conjunto: {formatDate(data.as_of)}. <a href="/statistics/homicides.json" download>Descargar datos y fuentes</a>.</p>
        </>}
  </section>;
}
