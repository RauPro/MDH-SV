import { useState } from 'react';
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
}

const reportLabel = (count: number) => `${count} ${count === 1 ? 'reporte individual' : 'reportes individuales'}`;

export function SalvadorMap({ selected, onSelect, counts, located, unlocated, invalidDates }: Props) {
  const [hovered, setHovered] = useState<string | null>(null);
  const max = Math.max(1, ...Object.values(counts));
  const choose = (department: string) => { if (!invalidDates) onSelect(selected === department ? '' : department); };
  return <section className="territory-map" aria-labelledby="map-title">
    <div className="map-heading">
      <div><h2 id="map-title"><MapPin size={19} aria-hidden="true"/> Mapa de El Salvador</h2>
        <p>Selecciona un departamento para consultar sus reportes individuales.</p></div>
      {selected && <button className="map-clear" onClick={() => onSelect('')}><X size={14} aria-hidden="true"/> Quitar filtro del mapa</button>}
    </div>
    <p className="map-scope">Los recuentos respetan la búsqueda, la temática y las fechas. No incluyen reportes colectivos ni informes.</p>
    <div className="map-layout">
      {/* Equivalent native buttons expose every action to keyboard and assistive technology. */}
      <svg viewBox="0 0 1000 547" className="map-canvas" aria-hidden="true" focusable="false" onMouseLeave={() => setHovered(null)}>
        {DEPARTMENTS.map(dept => {
          const count = counts[dept.name] ?? 0;
          const level = count ? Math.min(4, Math.ceil(count / max * 4)) : 0;
          return <g key={dept.id} data-testid={`map-region-${dept.id}`}
            className={`map-region map-level-${level}${selected === dept.name ? ' selected' : ''}${hovered === dept.name ? ' hovered' : ''}${invalidDates ? ' unavailable' : ''}`}
            onMouseEnter={() => setHovered(dept.name)} onClick={() => choose(dept.name)}>
            <title>{dept.name}: {invalidDates ? 'rango de fechas inválido' : reportLabel(count)}</title>
            <path d={dept.d}/>
            <text className="map-name" x={dept.labelX} y={dept.labelY - 5} textAnchor="middle">{dept.name}</text>
            <text className="map-count" x={dept.labelX} y={dept.labelY + 18} textAnchor="middle">{invalidDates ? '—' : count}</text>
          </g>;
        })}
      </svg>
      <div className="map-departments" role="group" aria-label="Seleccionar departamento en el mapa">
        {DEPARTMENTS.map(dept => <button key={dept.id}
          aria-label={`${dept.name}: ${reportLabel(counts[dept.name] ?? 0)}`}
          aria-pressed={selected === dept.name} disabled={invalidDates}
          className={selected === dept.name ? 'selected' : undefined}
          onFocus={() => setHovered(dept.name)} onBlur={() => setHovered(null)}
          onMouseEnter={() => setHovered(dept.name)} onMouseLeave={() => setHovered(null)}
          onClick={() => choose(dept.name)}>
          <span>{dept.name}</span><strong>{invalidDates ? '—' : counts[dept.name] ?? 0}</strong>
        </button>)}
      </div>
    </div>
    <div className="map-legend"><span><i className="map-key map-key-empty"/> 0 en esta consulta</span><span><i className="map-key filled"/> Con reportes</span><span><i className="map-key selected"/> Seleccionado</span></div>
    <div className="map-summary" aria-live="polite">
      {invalidDates ? <p>Corrige el rango de fechas para consultar los recuentos del mapa.</p> : <p><strong>{located}</strong> {located === 1 ? 'reporte con departamento documentado' : 'reportes con departamento documentado'}</p>}
      <button onClick={() => choose(UNKNOWN_DEPARTMENT)} aria-pressed={selected === UNKNOWN_DEPARTMENT} disabled={invalidDates}>
        Sin departamento identificado <strong>{invalidDates ? '—' : unlocated}</strong>
      </button>
    </div>
    <p className="map-note">Un departamento sin registros no implica ausencia de violaciones. Los contornos representan departamentos, no ubicaciones de personas.</p>
    <p className="map-attribution">Cartografía simplificada adaptada de <a href="https://simplemaps.com/svg/country/sv" target="_blank" rel="noopener noreferrer">Simplemaps</a> · <a href="https://simplemaps.com/resources/svg-license" target="_blank" rel="noopener noreferrer">Licencia</a></p>
  </section>;
}
