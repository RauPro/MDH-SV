import { useState } from 'react';
import { MapPin, X } from 'lucide-react';
import { DEPARTMENTS } from '../geography/elSalvador';
import { UNKNOWN_DEPARTMENT } from '../geography/territory';
import { formatDate } from '../catalog';
import type { OfficialMapContext } from './OfficialHomicideChart';
import './SalvadorMap.css';

interface Props {
  selected: string;
  onSelect: (department: string) => void;
  counts: Record<string, number>;
  located: number;
  unlocated: number;
  invalidDates: boolean;
  unit?: 'reports' | 'news';
  available?: boolean;
  loading?: boolean;
  reviewed?: number;
  automated?: number;
  reviewedCounts?: Record<string, number>;
  automaticCounts?: Record<string, number>;
  layers?: {
    official: OfficialMapContext | null;
    reports: { counts: Record<string, number>; located: number; unlocated: number; available: boolean };
  };
}

const reportLabel = (count: number) => `${count} ${count === 1 ? 'reporte individual' : 'reportes individuales'}`;
const newsLabel = (count: number) => `${count} ${count === 1 ? 'noticia' : 'noticias'}`;

export function SalvadorMap({ selected, onSelect, counts, located, unlocated, invalidDates, unit = 'reports', available = true, loading = false, reviewed = 0, automated = 0, reviewedCounts = {}, automaticCounts = {}, layers }: Props) {
  const [hovered, setHovered] = useState<string | null>(null);
  const [enabled, setEnabled] = useState({ news: true, reports: true, official: true });
  const isNews = unit === 'news';
  const countLabel = isNews ? newsLabel : reportLabel;
  const primaryVisible = !layers || enabled.news;
  const officialVisible = Boolean(layers && enabled.official);
  const reportsVisible = Boolean(layers && enabled.reports);
  const primaryUnavailable = invalidDates || !available;
  const official = layers?.official;
  const selectedOfficial = official?.period.departments.find(row => row.department === selected);
  const officialCounts = Object.fromEntries(official?.period.departments.map(row => [row.department, row.value]) ?? []);
  const officialValue = (department: string) => !official ? '—' : officialCounts[department] === null || officialCounts[department] === undefined ? 'Sin datos' : officialCounts[department]!.toLocaleString('es-SV');
  const fillLayer = officialVisible && official ? 'official' : primaryVisible && !primaryUnavailable ? 'primary' : reportsVisible && layers?.reports.available ? 'reports' : null;
  const unavailable = fillLayer === null;
  const fillCounts = fillLayer === 'official' ? officialCounts : fillLayer === 'primary' ? counts : fillLayer === 'reports' ? layers!.reports.counts : {};
  const max = Math.max(1, ...Object.values(fillCounts).filter((value): value is number => typeof value === 'number'));
  const mapValue = (department: string) => fillLayer === 'official' ? officialValue(department)
    : fillLayer === 'primary' ? counts[department] ?? 0
      : fillLayer === 'reports' ? layers!.reports.counts[department] ?? 0 : '—';
  const toggle = (layer: keyof typeof enabled) => setEnabled(value => ({ ...value, [layer]: !value[layer] }));
  const choose = (department: string) => { if (!unavailable) onSelect(selected === department ? '' : department); };
  return <section className="territory-map" aria-labelledby="map-title">
    <div className="map-heading">
      <div><h2 id="map-title"><MapPin size={19} aria-hidden="true"/> Mapa de El Salvador</h2>
        <p>{isNews ? 'Selecciona un departamento para consultar sus noticias por ubicación revisada o automática sin revisar.' : 'Selecciona un departamento para consultar sus reportes individuales.'}</p></div>
      {selected && <button className="map-clear" onClick={() => onSelect('')}><X size={14} aria-hidden="true"/> Quitar filtro del mapa</button>}
    </div>
    {layers && <>
      <fieldset className="map-layer-controls"><legend>Capas del mapa</legend>
        <label><input type="checkbox" checked={enabled.news} onChange={() => toggle('news')}/>Noticias</label>
        <label><input type="checkbox" checked={enabled.reports} onChange={() => toggle('reports')}/>Reportes documentados</label>
        <label><input type="checkbox" checked={enabled.official} onChange={() => toggle('official')}/>Homicidios oficiales</label>
      </fieldset>
      <p className="map-scope">Cada capa conserva su unidad: noticias, reportes individuales y agregados oficiales. Nunca se suman. Las casillas sólo cambian la visibilidad; los filtros de noticias no alteran las otras capas. Seleccionar un departamento filtra la lista de noticias.</p>
      {officialVisible && <div className="map-official-context">
        {official ? <><p><strong>Homicidios oficiales · {official.period.year}</strong> · {official.period.unit === 'persons' ? 'personas registradas por la fuente' : 'homicidios reportados por la fuente'} · {official.period.completeness === 'partial' ? 'Período parcial' : official.period.completeness === 'complete' ? 'Período completo' : 'Sin datos verificados'}.</p>
          <p>{formatDate(official.period.start_date)} al {formatDate(official.period.end_date)}. <a href={official.source.url} target="_blank" rel="noopener noreferrer">{official.source.institution}</a>. Corte: {formatDate(official.asOf)}. El año se selecciona en la gráfica.</p>
          <details><summary>Procedencia y límites de la capa oficial</summary><p>{official.source.title}. {official.source.edition_note}</p><p>{official.period.methodology}</p>{selectedOfficial && <p>{selectedOfficial.department}: {officialValue(selectedOfficial.department)}. {selectedOfficial.locator}</p>}{official.period.notes.map((note, index) => <p key={index}>{note}</p>)}<p>Estos agregados no identifican por sí solos violaciones de derechos humanos ni generan reportes individuales.</p></details>
          {!official.period.departments.some(row => row.status === 'available') && <p>Sin datos territoriales para este año. No se reparte el total nacional entre departamentos. «Sin datos» no equivale a cero.</p>}
        </> : <p>Capa oficial pendiente de datos validados. Consulta el estado y el reintento en la gráfica; no se muestran ceros provisionales.</p>}
      </div>}
      {reportsVisible && <p className="map-scope">Reportes documentados: {layers.reports.available ? `${layers.reports.located} individuales con departamento y ${layers.reports.unlocated} sin departamento` : 'datos no disponibles'}. Catálogo completo, sin aplicar el año oficial ni los filtros de noticias. <a href="#reportes">Consultar sus fichas</a>.</p>}
      {!enabled.news && !enabled.reports && !enabled.official && <p role="status">Activa al menos una capa para consultar el mapa.</p>}
      <p className="map-scope">Cifras y relleno del mapa: {fillLayer === 'official' ? `capa oficial de ${official!.period.year}` : fillLayer === 'primary' ? 'noticias' : fillLayer === 'reports' ? 'reportes individuales' : 'ninguna capa disponible'}. Los símbolos sólo indican presencia de las otras capas; sus cantidades se muestran por separado en los botones.</p>
    </>}
    <p className="map-scope">{isNews ? 'El mapa distingue departamentos con revisión humana y menciones extraídas automáticamente, sin revisar. Pueden ser referencias secundarias del artículo; no confirman el lugar de una violación. Los recuentos respetan los filtros; las fechas corresponden a la publicación.' : 'Los recuentos respetan la búsqueda, la temática y las fechas. No incluyen reportes colectivos ni informes.'}</p>
    <div className="map-layout">
      {/* Equivalent native buttons expose every action to keyboard and assistive technology. */}
      <svg viewBox="0 0 1000 547" className="map-canvas" aria-hidden="true" focusable="false" onMouseLeave={() => setHovered(null)}>
        {DEPARTMENTS.map(dept => {
          const count = fillCounts[dept.name] ?? 0;
          const level = count ? Math.min(4, Math.ceil(count / max * 4)) : 0;
          return <g key={dept.id} data-testid={`map-region-${dept.id}`}
            className={`map-region map-level-${level}${selected === dept.name ? ' selected' : ''}${hovered === dept.name ? ' hovered' : ''}${unavailable ? ' unavailable' : ''}${isNews && fillLayer === 'primary' && automaticCounts[dept.name] ? (reviewedCounts[dept.name] ? ' mixed-location' : ' automatic-location') : ''}${fillLayer === 'official' ? ` official-layer${officialCounts[dept.name] == null ? ' official-missing' : ''}` : ''}${fillLayer === 'reports' ? ' report-layer' : ''}`}
            onMouseEnter={() => setHovered(dept.name)} onClick={() => choose(dept.name)}>
            <title>{dept.name}: {layers ? `${primaryVisible ? `Noticias: ${primaryUnavailable ? '—' : counts[dept.name] ?? 0}. ` : ''}${reportsVisible ? `Reportes individuales: ${layers.reports.available ? layers.reports.counts[dept.name] ?? 0 : '—'}. ` : ''}${officialVisible ? `Oficial${official ? ` ${official.period.year}` : ''}: ${officialValue(dept.name)}` : ''}` : invalidDates ? 'rango de fechas inválido' : !available ? 'ubicaciones pendientes de comprobar' : countLabel(count)}</title>
            <path d={dept.d}/>
            <text className="map-name" x={dept.labelX} y={dept.labelY - 5} textAnchor="middle">{dept.name}</text>
            <text className="map-count" x={dept.labelX} y={dept.labelY + 18} textAnchor="middle">{layers ? mapValue(dept.name) === 'Sin datos' ? 'S/D' : mapValue(dept.name) : unavailable ? '—' : count}</text>
            {layers && primaryVisible && !primaryUnavailable && (counts[dept.name] ?? 0) > 0 && <circle className="map-news-marker" cx={dept.labelX - 18} cy={dept.labelY + 34} r="5"/>}
            {reportsVisible && layers?.reports.available && (layers.reports.counts[dept.name] ?? 0) > 0 && <rect className="map-report-marker" x={dept.labelX + 10} y={dept.labelY + 29} width="10" height="10"/>}
          </g>;
        })}
      </svg>
      <div className="map-departments" role="group" aria-label="Seleccionar departamento en el mapa">
        {DEPARTMENTS.map(dept => <button key={dept.id}
          aria-pressed={selected === dept.name} disabled={unavailable}
          className={`${selected === dept.name ? 'selected' : ''}${isNews && automaticCounts[dept.name] ? ' has-automatic-location' : ''}`}
          onFocus={() => setHovered(dept.name)} onBlur={() => setHovered(null)}
          onMouseEnter={() => setHovered(dept.name)} onMouseLeave={() => setHovered(null)}
          onClick={() => choose(dept.name)}>
          <span>{dept.name}{layers && <span className="map-layer-values">
            {primaryVisible && <span>Noticias: {primaryUnavailable ? '—' : counts[dept.name] ?? 0}</span>}
            {reportsVisible && <span>Reportes individuales: {layers.reports.available ? layers.reports.counts[dept.name] ?? 0 : '—'}</span>}
            {officialVisible && <span>Oficial{official ? ` ${official.period.year}` : ''}: {officialValue(dept.name)}</span>}
          </span>}{isNews && primaryVisible && available && !invalidDates && (counts[dept.name] ?? 0) > 0 && <small className="map-location-breakdown"><span className="reviewed">{reviewedCounts[dept.name] ?? 0} revisadas</span><span className="automatic">{automaticCounts[dept.name] ?? 0} automáticas</span></small>}</span>{!layers && <><strong>{unavailable ? '—' : counts[dept.name] ?? 0}</strong>
          <span className="sr-only"> {invalidDates ? 'rango de fechas inválido' : !available ? 'ubicaciones pendientes de comprobar' : countLabel(counts[dept.name] ?? 0).replace(/^\d+\s*/, '')}</span></>}
        </button>)}
      </div>
    </div>
    {layers && <div className="map-legend">{fillLayer === 'official' && <span><i className="map-key official"/>Relleno azul: agregado oficial; intensidad relativa en el año</span>}{primaryVisible && <span><i className="map-key news-marker"/>Círculo: noticias con departamento disponible</span>}{reportsVisible && <span><i className="map-key report-marker"/>Cuadrado: reportes individuales</span>}<span>S/D: sin datos · —: no disponible · 0: cero registrado en la capa</span></div>}
    {(fillLayer === 'primary' || (!layers && primaryVisible)) && <div className="map-legend">{available ? <span><i className="map-key map-key-empty"/> 0 en esta consulta</span> : <span>— Ubicaciones pendientes de comprobar</span>}<span><i className="map-key filled"/> {isNews ? 'Ubicaciones revisadas' : 'Con reportes'}</span>{isNews && <><span><i className="map-key automatic"/> Automáticas sin revisar</span><span><i className="map-key mixed"/> Ambas capas</span></>}<span><i className="map-key selected"/> Seleccionado</span></div>}
    {primaryVisible && <div className="map-summary" aria-live="polite">
      {invalidDates ? <p>Corrige el rango de fechas para consultar los recuentos del mapa.</p> : !available ? <p role="status">{loading ? 'Comprobando las ubicaciones del mapa…' : 'Recuentos del mapa no disponibles. Las noticias siguen consultables en la lista.'}</p> : <p><strong>{located}</strong> {isNews ? (located === 1 ? 'noticia con departamento disponible' : 'noticias con departamento disponible') : (located === 1 ? 'reporte con departamento documentado' : 'reportes con departamento documentado')}</p>}
      {isNews && available && !invalidDates && <p className="map-review-summary"><span className="reviewed"><strong>{reviewed}</strong> {reviewed === 1 ? 'noticia' : 'noticias'} con ubicación revisada</span><span className="automatic"><strong>{automated}</strong> {automated === 1 ? 'noticia' : 'noticias'} con menciones automáticas sin revisar</span></p>}
      <button onClick={() => { if (!primaryUnavailable) onSelect(selected === UNKNOWN_DEPARTMENT ? '' : UNKNOWN_DEPARTMENT); }} aria-pressed={selected === UNKNOWN_DEPARTMENT} disabled={primaryUnavailable}>
        {isNews ? 'Sin ubicación pública en El Salvador' : 'Sin departamento identificado'} <strong>{primaryUnavailable ? '—' : unlocated}</strong>
      </button>
    </div>}
    <p className="map-note">{isNews ? 'Las noticias sin ubicación pública siguen disponibles en la lista. Pueden tener extracción o validación pendiente, ubicación no determinada, datos protegidos o corresponder a otros países. Las noticias no se suman al recuento de casos. ' : ''}Un departamento sin registros no implica ausencia de violaciones. Los contornos representan departamentos, no ubicaciones de personas.</p>
    <p className="map-attribution">Cartografía simplificada adaptada de <a href="https://simplemaps.com/svg/country/sv" target="_blank" rel="noopener noreferrer">Simplemaps</a> · <a href="https://simplemaps.com/resources/svg-license" target="_blank" rel="noopener noreferrer">Licencia</a></p>
  </section>;
}
