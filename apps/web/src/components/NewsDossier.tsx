import { pendingNewsDossier, type NewsDossier as Dossier, type NewsDossierFieldName } from '../../../../shared/news-dossier.mjs';
import type { NewsSelection } from '../../../../shared/news-selection.mjs';
import { formatDate } from '../catalog';

const fieldLabels: Record<NewsDossierFieldName, string> = {
  summary: 'Resumen', category: 'Clasificación temática', alleged_violation: 'Presunto hecho violatorio',
  facts: 'Descripción de los hechos', affected_persons: 'Víctima o personas afectadas', country: 'País',
  department: 'Departamento', municipality: 'Municipio', public_place: 'Lugar público',
  event_date: 'Fecha del hecho', event_date_end: 'Fin del período del hecho', event_time: 'Hora del hecho',
  reported_actors: 'Actores señalados por la fuente', institutional_sources: 'Instituciones y fuentes citadas',
  vulnerable_groups: 'Grupos en situación de vulnerabilidad', themes: 'Derechos y temáticas',
  hypothesis: 'Hipótesis atribuida a la fuente', instrument: 'Arma o instrumento reportado',
};
const statusLabels = { pending: 'Sin dato revisado disponible', reported: 'Atribuido a la fuente', not_reported: 'Dato no mencionado en el texto analizado', withheld: 'Dato protegido', not_applicable: 'No aplica' };
const extractionLabels = { pending: 'Extracción pendiente', processed: 'Extracción procesada', source_unavailable: 'Fuente no accesible durante la revisión', error: 'Extracción con error' };
const jurisdictionLabels = { SV: 'El Salvador según revisión', other: 'Fuera de El Salvador según revisión', unclear: 'Por determinar' };
const relevanceLabels = { relevant: 'Pertinente para seguimiento', not_relevant: 'Fuera del ámbito de seguimiento', unclear: 'Por determinar' };
const typeLabels = { incident: 'Hecho individual reportado', aggregate: 'Reporte colectivo', publication: 'Informe de contexto' };

export function NewsDossier({ dossier: supplied, selection, reviewState = 'ready' }: {
  dossier: Dossier; selection?: NewsSelection; reviewState?: 'loading' | 'ready' | 'unavailable';
}) {
  // A public selection can explain pertinence, but never unlock private fields.
  const dossier = supplied.human_reviewed ? supplied : pendingNewsDossier({
    id: supplied.candidate_id, source_url: supplied.source_url, title: supplied.source_title,
    publisher: supplied.publisher, published_date: supplied.published_date,
  });
  const jurisdiction = dossier.human_reviewed ? jurisdictionLabels[dossier.classification.jurisdiction]
    : selection ? 'Vínculo con El Salvador identificado en la fuente' : 'Sin evaluación pública disponible';
  const relevance = dossier.human_reviewed ? relevanceLabels[dossier.classification.relevance]
    : selection ? 'Seleccionada para seguimiento de derechos humanos' : 'Sin evaluación pública disponible';
  const field = (name: NewsDossierFieldName) => {
    const value = dossier.fields[name];
    let display = Array.isArray(value.value) ? value.value.join(' · ') : value.value;
    if (value.status === 'reported' && typeof value.value === 'string' && ['event_date', 'event_date_end'].includes(name)) {
      display = dossier.date_precision === 'year_range' ? `${value.value.slice(0, 4)} (precisión anual)`
        : dossier.date_precision === 'month_range' ? `${new Intl.DateTimeFormat('es-SV', { month: 'long', year: 'numeric', timeZone: 'UTC' }).format(new Date(value.value))} (precisión mensual)`
          : formatDate(value.value);
    }
    return <div className={`dossier-field dossier-${value.status}`} key={name}>
      <dt>{fieldLabels[name]}<span>{statusLabels[value.status]}</span></dt>
      <dd>{value.status === 'reported' ? display : statusLabels[value.status]}
        {value.note && <small>{value.note}</small>}
        {value.evidence_refs.length > 0 && <span className="field-evidence">{value.evidence_refs.map(id => {
          const evidence = dossier.evidence.find(item => item.id === id)!;
          return <a key={id} href={evidence.url} target="_blank" rel="noopener noreferrer" title={evidence.locator}>Referencia {dossier.evidence.indexOf(evidence) + 1}<span className="sr-only"> de {fieldLabels[name]}: {evidence.locator}</span></a>;
        })}</span>}
      </dd>
    </div>;
  };
  return <section aria-label="Ficha estructurada de la noticia">
    <h3>Ficha estructurada de la noticia</h3>
    {dossier.human_reviewed ? <><p>Ficha revisada por {dossier.reviewed_by} el {formatDate(dossier.reviewed_at!)}. {extractionLabels[dossier.extraction_status]}.</p><p>{dossier.review_notes}</p></>
      : <p>{reviewState === 'ready' ? 'Ficha pendiente de revisión pública. Los campos estructurados todavía no tienen una versión revisada publicada.'
        : reviewState === 'loading' ? 'Estamos comprobando si hay una ficha pública revisada disponible.'
          : 'No se pudo comprobar si hay una ficha pública revisada disponible.'} La selección por pertinencia no indica que estos campos hayan sido extraídos o revisados. Un campo sin dato publicado no significa que la noticia carezca de esa información.</p>}
    <section aria-label="Selección por pertinencia">
      <h4>Pertinencia de la noticia</h4>
      <p>{dossier.human_reviewed ? 'Evaluación de pertinencia de la ficha revisada editorialmente.'
        : selection?.human_reviewed ? 'La selección de pertinencia fue revisada editorialmente.'
          : selection ? 'Clasificación de pertinencia automática · Sin revisión humana.' : 'La pertinencia pública aún no se ha podido comprobar.'} Esta evaluación no aprueba los demás campos ni acredita una violación.</p>
      <dl className="dossier-grid">
        <div className="dossier-field"><dt>Jurisdicción evaluada</dt><dd>{jurisdiction}</dd></div>
        <div className="dossier-field"><dt>Pertinencia evaluada</dt><dd>{relevance}</dd></div>
      </dl>
    </section>
    <dl className="dossier-grid"><div className="dossier-field"><dt>Tipo de contenido</dt><dd>{dossier.human_reviewed ? (dossier.classification.record_type ? typeLabels[dossier.classification.record_type] : 'Por determinar') : statusLabels.pending}</dd></div></dl>
    <h3>Hechos y personas</h3><dl className="dossier-grid">{(['summary', 'alleged_violation', 'facts', 'affected_persons', 'reported_actors'] as const).map(field)}</dl>
    <h3>Lugar y tiempo</h3><dl className="dossier-grid">{(['country', 'department', 'municipality', 'public_place', 'event_date', 'event_date_end', 'event_time'] as const).map(field)}</dl>
    <p className="filter-note">La fecha de publicación pertenece a la referencia bibliográfica. No se utiliza para completar la fecha del hecho.</p>
    <h3>Clasificación y contexto</h3><dl className="dossier-grid">{(['category', 'themes', 'vulnerable_groups', 'institutional_sources', 'hypothesis', 'instrument'] as const).map(field)}</dl>
    <h3>Referencias de la ficha</h3><ol className="dossier-evidence">{dossier.evidence.map(evidence => <li key={evidence.id}><a href={evidence.url} target="_blank" rel="noopener noreferrer">{evidence.locator}</a></li>)}</ol>
    <p className="filter-note">Una ficha de noticia puede ser parcial o quedar fuera del ámbito del monitor. Su existencia no la convierte en una violación ni incorpora personas al recuento de víctimas. «Dato no mencionado» se limita al texto analizado y revisado; no afirma ausencia en otras versiones, documentos o fuentes. Los campos pendientes todavía no permiten esa conclusión.</p>
  </section>;
}
