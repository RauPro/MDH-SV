import { useId, useState } from 'react';
import { MapPin, X } from 'lucide-react';
import { DEPARTMENTS } from '../geography/elSalvador';
import { UNKNOWN_DEPARTMENT } from '../geography/territory';
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
}

const reportLabel = (count: number) => `${count} ${count === 1 ? 'reporte individual' : 'reportes individuales'}`;
const newsLabel = (count: number) => `${count} ${count === 1 ? 'noticia' : 'noticias'}`;

export function SalvadorMap({ selected, onSelect, counts, located, unlocated, invalidDates, unit = 'reports', available = true, loading = false, reviewed = 0, automated = 0, reviewedCounts = {}, automaticCounts = {} }: Props) {
  const [hovered, setHovered] = useState<string | null>(null);
  const isNews = unit === 'news';
  const descriptionId = useId();
  const countLabel = isNews ? newsLabel : reportLabel;
  const unavailable = invalidDates || !available;
  const max = Math.max(1, ...Object.values(counts));
  const choose = (department: string) => { if (!unavailable) onSelect(selected === department ? '' : department); };
  return <section className="territory-map" aria-labelledby="map-title">
    <div className="map-heading">
      <div><h2 id="map-title"><MapPin size={19} aria-hidden="true"/> Mapa de El Salvador</h2>
        <p>{isNews ? 'Selecciona un departamento para consultar sus noticias por ubicación revisada o automática sin revisar.' : 'Selecciona un departamento para consultar sus reportes individuales.'}</p></div>
      {selected && <button className="map-clear" onClick={() => onSelect('')}><X size={14} aria-hidden="true"/> Quitar filtro del mapa</button>}
    </div>
    <p className="map-scope">{isNews ? 'El mapa distingue departamentos con revisión humana y menciones extraídas automáticamente, sin revisar. Pueden ser referencias secundarias del artículo; no confirman el lugar de una violación. Los recuentos respetan los filtros; las fechas corresponden a la publicación.' : 'Los recuentos respetan la búsqueda, la temática y las fechas. No incluyen reportes colectivos ni informes.'}</p>
    <div className="map-layout">
      {/* Equivalent native buttons expose every action to keyboard and assistive technology. */}
      <svg viewBox="0 0 1000 547" className="map-canvas" aria-hidden="true" focusable="false" onMouseLeave={() => setHovered(null)}>
        {DEPARTMENTS.map(dept => {
          const count = counts[dept.name] ?? 0;
          const level = count ? Math.min(4, Math.ceil(count / max * 4)) : 0;
          return <g key={dept.id} data-testid={`map-region-${dept.id}`}
            className={`map-region map-level-${level}${selected === dept.name ? ' selected' : ''}${hovered === dept.name ? ' hovered' : ''}${unavailable ? ' unavailable' : ''}${isNews && automaticCounts[dept.name] ? (reviewedCounts[dept.name] ? ' mixed-location' : ' automatic-location') : ''}`}
            onMouseEnter={() => setHovered(dept.name)} onClick={() => choose(dept.name)}>
            <title>{dept.name}: {invalidDates ? 'rango de fechas inválido' : !available ? 'ubicaciones pendientes de comprobar' : countLabel(count)}</title>
            <path d={dept.d}/>
            <text className="map-name" x={dept.labelX} y={dept.labelY - 5} textAnchor="middle">{dept.name}</text>
            <text className="map-count" x={dept.labelX} y={dept.labelY + 18} textAnchor="middle">{unavailable ? '—' : count}</text>
          </g>;
        })}
      </svg>
      <div className="map-departments" role="group" aria-label="Seleccionar departamento en el mapa">
        {DEPARTMENTS.map(dept => <button key={dept.id}
          aria-label={`${dept.name}: ${!available ? 'ubicaciones pendientes de comprobar' : countLabel(counts[dept.name] ?? 0)}`}
          aria-pressed={selected === dept.name} disabled={unavailable}
          aria-describedby={isNews && available && !invalidDates && (counts[dept.name] ?? 0) > 0 ? `${descriptionId}-${dept.id}` : undefined}
          className={`${selected === dept.name ? 'selected' : ''}${isNews && automaticCounts[dept.name] ? ' has-automatic-location' : ''}`}
          onFocus={() => setHovered(dept.name)} onBlur={() => setHovered(null)}
          onMouseEnter={() => setHovered(dept.name)} onMouseLeave={() => setHovered(null)}
          onClick={() => choose(dept.name)}>
          <span>{dept.name}{isNews && available && !invalidDates && (counts[dept.name] ?? 0) > 0 && <small className="map-location-breakdown" id={`${descriptionId}-${dept.id}`}><span className="reviewed">{reviewedCounts[dept.name] ?? 0} revisadas</span><span className="automatic">{automaticCounts[dept.name] ?? 0} automáticas</span></small>}</span><strong>{unavailable ? '—' : counts[dept.name] ?? 0}</strong>
        </button>)}
      </div>
    </div>
    <div className="map-legend">{available ? <span><i className="map-key map-key-empty"/> 0 en esta consulta</span> : <span>— Ubicaciones pendientes de comprobar</span>}<span><i className="map-key filled"/> {isNews ? 'Ubicaciones revisadas' : 'Con reportes'}</span>{isNews && <><span><i className="map-key automatic"/> Automáticas sin revisar</span><span><i className="map-key mixed"/> Ambas capas</span></>}<span><i className="map-key selected"/> Seleccionado</span></div>
    <div className="map-summary" aria-live="polite">
      {invalidDates ? <p>Corrige el rango de fechas para consultar los recuentos del mapa.</p> : !available ? <p role="status">{loading ? 'Comprobando las ubicaciones del mapa…' : 'Recuentos del mapa no disponibles. Las noticias siguen consultables en la lista.'}</p> : <p><strong>{located}</strong> {isNews ? (located === 1 ? 'noticia con departamento disponible' : 'noticias con departamento disponible') : (located === 1 ? 'reporte con departamento documentado' : 'reportes con departamento documentado')}</p>}
      {isNews && available && !invalidDates && <p className="map-review-summary"><span className="reviewed"><strong>{reviewed}</strong> {reviewed === 1 ? 'noticia' : 'noticias'} con ubicación revisada</span><span className="automatic"><strong>{automated}</strong> {automated === 1 ? 'noticia' : 'noticias'} con menciones automáticas sin revisar</span></p>}
      <button onClick={() => choose(UNKNOWN_DEPARTMENT)} aria-pressed={selected === UNKNOWN_DEPARTMENT} disabled={unavailable}>
        {isNews ? 'Sin ubicación pública en El Salvador' : 'Sin departamento identificado'} <strong>{unavailable ? '—' : unlocated}</strong>
      </button>
    </div>
    <p className="map-note">{isNews ? 'Las noticias sin ubicación pública siguen disponibles en la lista. Pueden tener extracción o validación pendiente, ubicación no determinada, datos protegidos o corresponder a otros países. Las noticias no se suman al recuento de casos. ' : ''}Un departamento sin registros no implica ausencia de violaciones. Los contornos representan departamentos, no ubicaciones de personas.</p>
    <p className="map-attribution">Cartografía simplificada adaptada de <a href="https://simplemaps.com/svg/country/sv" target="_blank" rel="noopener noreferrer">Simplemaps</a> · <a href="https://simplemaps.com/resources/svg-license" target="_blank" rel="noopener noreferrer">Licencia</a></p>
  </section>;
}
