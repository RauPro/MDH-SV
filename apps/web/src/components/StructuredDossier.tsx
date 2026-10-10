import { eventDate, type EvidenceField, type Publication } from '../catalog';

const labels = {reported:'Atribuido a la fuente',not_reported:'No consignado',withheld:'Dato protegido',not_applicable:'No aplica'};

export function StructuredDossier({record}: {record: Publication}) {
  const d = record.dossier;
  const field = (title: string, value: EvidenceField, display?: string) => <div className={`dossier-field dossier-${value.status}`} key={title}>
    <dt>{title}<span>{labels[value.status]}</span></dt>
    <dd>{value.status === 'reported' ? (display || (Array.isArray(value.value) ? value.value.join(' · ') : value.value)) : labels[value.status]}
      {value.note && <small>{value.note}</small>}
      {value.evidence_refs.length > 0 && <span className="field-evidence">{value.evidence_refs.map(id => {
        const source = d.evidence.find(e => e.id === id)!;
        return <a key={id} href={source.url} target="_blank" rel="noopener noreferrer" title={source.locator} aria-label={`Evidencia ${d.evidence.indexOf(source) + 1} de ${title}: ${source.locator}`}>Evidencia {d.evidence.indexOf(source) + 1}<span className="sr-only"> de {title}: {source.locator}</span></a>;
      })}</span>}
    </dd>
  </div>;
  return <section aria-label="Ficha estructurada de investigación">
    <h3>Hechos y personas</h3><dl className="dossier-grid">
      {field('Presunto hecho violatorio', d.alleged_violation)}
      {field('Descripción de los hechos', d.facts)}
      {field('Víctima o personas afectadas', d.affected_persons)}
      {field('Actores señalados por la fuente', d.reported_actors)}
    </dl>
    <h3>Lugar y tiempo</h3><dl className="dossier-grid">
      {field('Departamento', d.location.department)}
      {field('Municipio', d.location.municipality)}
      {field('Lugar público', d.location.public_place)}
      {field('Fecha del hecho', d.event_date, eventDate(record))}
      {field('Hora del hecho', d.event_time)}
    </dl><p className="filter-note">{record.date_note}</p>
    <h3>Clasificación y contexto</h3><dl className="dossier-grid">
      {field('Derechos y temáticas', d.themes)}
      {field('Grupos en situación de vulnerabilidad', d.vulnerable_groups)}
      {field('Instituciones y fuentes citadas', d.institutional_sources)}
      {field('Hipótesis atribuida a la fuente', d.hypothesis)}
      {field('Arma o instrumento reportado', d.instrument)}
    </dl>
    <h3>Evidencia documental</h3><ol className="dossier-evidence">{d.evidence.map(e => <li key={e.id}><a href={e.url} target="_blank" rel="noopener noreferrer">{e.locator}</a></li>)}</ol>
    <p className="filter-note">«Atribuido a la fuente» no equivale a corroboración independiente. «No consignado» identifica un vacío de esta ficha, no demuestra la ausencia del dato en el documento original.</p>
  </section>;
}
