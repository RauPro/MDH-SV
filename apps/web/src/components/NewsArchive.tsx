import { useEffect, useMemo, useRef, useState } from 'react';
import { flushSync } from 'react-dom';
import { ArrowUpRight, Download, Search } from 'lucide-react';
import { formatDate, type Publication } from '../catalog';
import { archiveInstant, archiveMonth, collectionStatusLabel, filterNews, invalidArchiveRange, parseNewsArchive, relatedPublications, scopeLabels, topicLabel, type NewsArchiveData, type NewsFilters, type NewsReference } from '../newsArchive';
import { Dialog } from './Dialog';
import { NewsDossier } from './NewsDossier';
import { SalvadorMap } from './SalvadorMap';
import { buildNewsTerritoryItems, matchesNewsTerritory, newsTerritoryItem, summarizeNewsTerritory } from '../geography/newsTerritory';
import { UNKNOWN_DEPARTMENT } from '../geography/territory';
import { newsDossierFor, parsePublishedNewsDossiers, type NewsDossier as Dossier } from '../../../../shared/news-dossier.mjs';
import { parsePublishedNewsLocations, validateLocationBindings, type NewsLocation } from '../../../../shared/news-location.mjs';

const PAGE_SIZE = 30;
const emptyFilters: NewsFilters = { query: '', publisher: '', topic: '', from: '', to: '', scope: 'el_salvador' };

export function NewsArchive({ onOpenRecord, records, view = 'archive' }: { onOpenRecord: (id: string) => void; records: Publication[]; view?: 'archive' | 'monitor' }) {
  const isMonitor = view === 'monitor';
  const defaultFilters = { ...emptyFilters, scope: isMonitor ? '' : emptyFilters.scope };
  const titleId = isMonitor ? 'news-monitor-title' : 'archive-title';
  const dateHelpId = isMonitor ? 'news-monitor-date-help' : 'archive-date-help';
  const dateErrorId = isMonitor ? 'news-monitor-date-error' : 'archive-date-error';
  const [archive, setArchive] = useState<NewsArchiveData | null>(null);
  const [error, setError] = useState(false);
  const [attempt, setAttempt] = useState(0);
  const [filters, setFilters] = useState<NewsFilters>(defaultFilters);
  const [department, setDepartment] = useState('');
  const [page, setPage] = useState(1);
  const [selected, setSelected] = useState<NewsReference | null>(null);
  const resultsRef = useRef<HTMLDivElement>(null);
  const [dossiers, setDossiers] = useState<Map<string, Dossier>>(new Map());
  const [dossierState, setDossierState] = useState<'loading' | 'ready' | 'unavailable'>('loading');
  const [dossierAttempt, setDossierAttempt] = useState(0);
  const [locations, setLocations] = useState<Map<string, NewsLocation>>(new Map());
  const [locationState, setLocationState] = useState<'loading' | 'ready' | 'unavailable'>('loading');
  const [locationAttempt, setLocationAttempt] = useState(0);
  const [locationLayer, setLocationLayer] = useState<'all' | 'reviewed' | 'automated_unreviewed'>('all');
  useEffect(() => {
    const controller = new AbortController();
    const timer = window.setTimeout(() => controller.abort(), 12000);
    let active = true;
    fetch('/archive/news.json', { signal: controller.signal }).then(async response => {
      if (!response.ok) throw new Error('Archive unavailable');
      return parseNewsArchive(await response.json());
    }).then(data => { if (active) setArchive(data); }).catch(() => { if (active) setError(true); }).finally(() => window.clearTimeout(timer));
    return () => { active = false; controller.abort(); window.clearTimeout(timer); };
  }, [attempt]);
  useEffect(() => {
    const controller = new AbortController();
    const timer = window.setTimeout(() => controller.abort(), 12000);
    let active = true;
    fetch('/archive/dossiers.json', { signal: controller.signal }).then(async response => {
      if (!response.ok) throw new Error('Public dossiers unavailable');
      return parsePublishedNewsDossiers(await response.json());
    }).then(index => { if (active) { setDossiers(index); setDossierState('ready'); } })
      .catch(() => { if (active) { setDossiers(new Map()); setDossierState('unavailable'); } })
      .finally(() => window.clearTimeout(timer));
    return () => { active = false; controller.abort(); window.clearTimeout(timer); };
  }, [dossierAttempt]);
  useEffect(() => {
    const controller = new AbortController();
    const timer = window.setTimeout(() => controller.abort(), 12000);
    let active = true;
    fetch('/archive/locations.json', { signal: controller.signal }).then(async response => {
      if (!response.ok) throw new Error('Public locations unavailable');
      return parsePublishedNewsLocations(await response.json());
    }).then(index => { if (active) { setLocations(index); setLocationState('ready'); } })
      .catch(() => { if (active) { setLocations(new Map()); setLocationState('unavailable'); } })
      .finally(() => window.clearTimeout(timer));
    return () => { active = false; controller.abort(); window.clearTimeout(timer); };
  }, [locationAttempt]);
  const boundLocations = useMemo(() => {
    if (!archive || locationState !== 'ready') return null;
    try { return validateLocationBindings(locations, archive.records); } catch { return null; }
  }, [archive, locations, locationState]);
  const locationsAvailable = locationState === 'ready' && boundLocations !== null;
  // If human decisions cannot be loaded, automated locations cannot safely override them.
  const usableLocations = dossierState === 'ready' && boundLocations ? boundLocations : undefined;
  const mapAvailable = dossierState === 'ready' && (locationLayer === 'reviewed' || locationsAvailable);
  const mapLoading = dossierState === 'loading' || (locationLayer !== 'reviewed' && locationState === 'loading');
  const invalidDates = invalidArchiveRange(filters.from, filters.to);
  const matchingReferences = useMemo(() => filterNews(archive?.records ?? [], filters), [archive, filters]);
  const territoryItems = useMemo(() => isMonitor ? buildNewsTerritoryItems(matchingReferences, dossiers, usableLocations) : [], [isMonitor, matchingReferences, dossiers, usableLocations]);
  const layerItems = useMemo(() => locationLayer === 'all' ? territoryItems : territoryItems.filter(item => item.locationKind === locationLayer), [locationLayer, territoryItems]);
  const territorySummary = useMemo(() => summarizeNewsTerritory(layerItems), [layerItems]);
  const filtered = useMemo(() => isMonitor && mapAvailable ? layerItems.filter(item => matchesNewsTerritory(item, department)).map(item => item.reference) : matchingReferences, [department, isMonitor, mapAvailable, matchingReferences, layerItems]);
  const topics = [...new Set(archive?.records.flatMap(record => record.topics) ?? [])].sort((a, b) => topicLabel(a).localeCompare(topicLabel(b), 'es'));
  const pages = Math.max(1, Math.ceil(filtered.length / PAGE_SIZE));
  const currentPage = Math.min(page, pages);
  const reset = () => { setFilters(defaultFilters); setDepartment(''); setLocationLayer('all'); setPage(1); };
  const update = (key: keyof NewsFilters, value: string) => { setFilters(current => ({ ...current, [key]: value })); setPage(1); };
  const changePage = (next: number) => {
    flushSync(() => setPage(next));
    resultsRef.current?.focus({ preventScroll: true });
    resultsRef.current?.scrollIntoView?.({ block: 'start', behavior: 'smooth' });
  };
  const linked = selected ? relatedPublications(selected, records) : [];
  const selectedDossier = selected ? newsDossierFor(selected, dossiers) : null;
  const selectedLocation = selected ? newsTerritoryItem(selected, dossiers, usableLocations).automaticLocation : null;
  const openRecord = (id: string) => {
    flushSync(() => setSelected(null));
    onOpenRecord(id);
  };

  if (error) return <section className="empty" role="alert"><h2>No pudimos cargar el archivo de noticias</h2><p>No mostraremos referencias ni cifras sin validar el archivo. Puedes volver a intentarlo.</p><button className="primary" onClick={() => { setError(false); setAttempt(value => value + 1); }}>Reintentar archivo</button></section>;
  if (!archive) return <p className="empty" role="status">Cargando el archivo de noticias…</p>;

  return <section className={`records-section archive-section${isMonitor ? ' news-monitor' : ''}`} aria-labelledby={titleId}>
    <div className="section-heading archive-intro"><div className="eyebrow">{isMonitor ? 'NOTICIAS · FICHAS Y UBICACIONES' : 'MEMORIA DOCUMENTAL · ARCHIVO BIBLIOGRÁFICO'}</div><h2 id={titleId}>{isMonitor ? 'Fichas de noticias' : 'Archivo de noticias'}</h2>{isMonitor ? <><p>{archive.records.length} noticias con ficha · {archive.coverage.sources.length} medios · cobertura parcial.</p><p>Puedes consultar todas las noticias, aunque sus campos estén pendientes. El mapa distingue ubicaciones revisadas y departamentos extraídos automáticamente de las fuentes, todavía sin revisar.</p></> : <><p>Periodo de búsqueda: del {formatDate(archive.coverage.start_date)} al {formatDate(archive.coverage.end_date)}.</p><p>{archive.records.length} referencias · {archive.coverage.sources.length} medios · cobertura parcial.</p><p>Este intervalo indica el alcance solicitado. Consulta la cobertura por medio y mes para distinguir lo recorrido, lo pendiente y los fallos.</p></>}</div>
    {isMonitor && <button className="text-button news-results-link" onClick={() => { resultsRef.current?.focus({ preventScroll: true }); resultsRef.current?.scrollIntoView?.({ block: 'start', behavior: 'smooth' }); }}>Ver las fichas de noticias</button>}
    {archive.coverage.sources.some(source => source.collection?.access === 'restricted') && <p className="filter-note">Acceso automatizado restringido: {archive.coverage.sources.filter(source => source.collection?.access === 'restricted').map(source => source.publisher).join(', ')}. Sus noticias no se incorporan automáticamente.</p>}
    <aside className="notice archive-detail-note"><div><strong>Cada noticia tiene una ficha estructurada.</strong> Los datos bibliográficos conocidos se acompañan de campos pendientes o información publicada con revisión humana. Las etiquetas automáticas no confirman pertinencia, jurisdicción ni hechos. Estas noticias no se suman a los casos del monitor.</div></aside>
    {dossierState === 'loading' && <p className="filter-note" role="status">Comprobando fichas públicas revisadas…</p>}
    {dossierState === 'unavailable' && <p className="filter-note" role="status">No se pudieron comprobar las fichas públicas revisadas. Se muestran los datos bibliográficos y campos pendientes. <button className="text-button" onClick={() => { setDossierState('loading'); setDossierAttempt(value => value + 1); }}>Reintentar fichas públicas</button></p>}
    {locationState === 'loading' && <p className="filter-note" role="status">Comprobando ubicaciones automáticas…</p>}
    {(locationState === 'unavailable' || (locationState === 'ready' && archive && !boundLocations)) && <p className="filter-note" role="status">No se pudieron comprobar las ubicaciones automáticas. Las noticias siguen disponibles. <button className="text-button" onClick={() => { setLocationState('loading'); setLocationAttempt(value => value + 1); }}>Reintentar ubicaciones</button></p>}
    {!isMonitor && <details className="archive-coverage"><summary>Ver cobertura por medio y mes</summary><p>{archive.policy.selection_method} «API recorrida» indica que se consultaron las páginas disponibles del medio; no acredita un archivo exhaustivo ni una revisión del contenido.</p><p>Archivo generado: {archiveInstant(archive.generated_at)} (El Salvador). Antecedente de investigación: artículo incorporado a IEEE Xplore el {formatDate(archive.paper.publication_date)}. {archive.paper.publication_date_basis} La fecha del paper no determina el inicio del archivo histórico.</p>
      {archive.coverage.sources.map(source => <details className="archive-source" key={source.id}><summary>{source.publisher} · {source.records} referencias · {source.collection?.access === 'restricted' ? 'Acceso restringido' : collectionStatusLabel(source.status)}</summary>{source.collection && <p>{source.collection.notice}</p>}<div className="archive-table-wrap"><table><caption>Cobertura mensual de {source.publisher}</caption><thead><tr><th scope="col">Mes</th><th scope="col">Referencias</th><th scope="col">Publicaciones recorridas</th><th scope="col">Estado</th></tr></thead><tbody>{source.months.map(item => <tr key={item.month}><th scope="row">{archiveMonth(item.month)}</th><td>{item.records}</td><td>{item.posts_scanned}</td><td>{collectionStatusLabel(item.status)}</td></tr>)}</tbody></table></div></details>)}
      <ul>{archive.policy.coverage_limitations.map(limit => <li key={limit}>{limit}</li>)}</ul>
    </details>}
    <div className="downloads"><a href="/archive/news.json" download="mdh-sv-archivo-noticias.json"><Download size={16} aria-hidden="true"/>Descargar archivo JSON</a></div>
    <div className="filters archive-filters"><label className="search-label"><span>Buscar noticias</span><div className="search-box"><Search size={18} aria-hidden="true"/><input type="search" value={filters.query} onChange={event => update('query', event.target.value)} placeholder="Título, medio o tema"/></div></label><label>Medio<select value={filters.publisher} onChange={event => update('publisher', event.target.value)}><option value="">Todos los medios</option>{archive.coverage.sources.map(source => <option key={source.id}>{source.publisher}</option>)}</select></label><label>Vínculo territorial<select value={filters.scope} onChange={event => update('scope', event.target.value)}><option value="el_salvador">Referencia a El Salvador</option><option value="undetermined">Por determinar</option><option value="international_context">Contexto internacional</option><option value="">Todo el archivo</option></select></label><label>Tema detectado<select value={filters.topic} onChange={event => update('topic', event.target.value)}><option value="">Todos los temas</option>{topics.map(topic => <option key={topic} value={topic}>{topicLabel(topic)}</option>)}</select></label><label>Publicación desde<input type="date" value={filters.from} onChange={event => update('from', event.target.value)} aria-invalid={invalidDates} aria-describedby={invalidDates ? dateErrorId : dateHelpId}/></label><label>Publicación hasta<input type="date" value={filters.to} onChange={event => update('to', event.target.value)} aria-invalid={invalidDates} aria-describedby={invalidDates ? dateErrorId : dateHelpId}/></label><button className="reset" onClick={reset}>Restablecer filtros de noticias</button></div>
    <p className="filter-note">El vínculo territorial se estima con términos en título y URL; puede omitir noticias pertinentes o incluir otras por error. No confirma jurisdicción. {isMonitor ? 'Por defecto se muestran todas las noticias; este indicio automático no determina su ubicación en el mapa.' : 'Por defecto se muestran referencias a El Salvador; puedes consultar todo el archivo.'}</p>
    <p className="filter-note" id={dateHelpId}>Las fechas corresponden a la publicación declarada por el medio; no a la fecha de los hechos.</p>
    {invalidDates && <p role="alert" id={dateErrorId}>La fecha inicial debe ser válida y anterior o igual a la final.</p>}
    {isMonitor && <>
      <label className="news-location-filter">Capa de ubicaciones<select value={locationLayer} onChange={event => { setLocationLayer(event.target.value as typeof locationLayer); setDepartment(''); setPage(1); }}><option value="all">Todas las ubicaciones</option><option value="reviewed">Revisadas</option><option value="automated_unreviewed">Automáticas sin revisar</option></select></label>
      <SalvadorMap unit="news" available={mapAvailable} loading={mapLoading} reviewed={territorySummary.reviewed} automated={territorySummary.automated} reviewedCounts={territorySummary.reviewedCounts} automaticCounts={territorySummary.automaticCounts} selected={department} onSelect={value => { setDepartment(value); setPage(1); }} counts={territorySummary.counts} located={territorySummary.located} unlocated={territorySummary.unlocated} invalidDates={invalidDates}/>
      {!invalidDates && mapAvailable && matchingReferences.length > 0 && territorySummary.located === 0 && <p className="notice news-map-pending">Ninguna noticia tiene un departamento disponible en la capa seleccionada. Puedes consultar todas las noticias al restablecer los filtros; una ubicación pendiente no implica ausencia de hechos.</p>}
      {!invalidDates && mapAvailable && territorySummary.unlocated > 0 && <div className="news-map-reasons"><p>Noticias sin ubicación publicable en este mapa:</p><ul>
        {territorySummary.reasons.pending_review > 0 && <li>{territorySummary.reasons.pending_review} sin ubicación automática disponible ni ubicación revisada.</li>}
        {territorySummary.reasons.jurisdiction_unknown > 0 && <li>{territorySummary.reasons.jurisdiction_unknown} con jurisdicción por determinar.</li>}
        {territorySummary.reasons.outside_el_salvador > 0 && <li>{territorySummary.reasons.outside_el_salvador} fuera de El Salvador.</li>}
        {territorySummary.reasons.department_unknown > 0 && <li>{territorySummary.reasons.department_unknown} sin departamento documentado públicamente.</li>}
      </ul></div>}
      {department && mapAvailable && <p className="filter-note" role="status">Filtro del mapa: {department === UNKNOWN_DEPARTMENT ? 'sin ubicación pública en El Salvador' : department}.</p>}
    </>}
    <div className="results-heading archive-results" ref={resultsRef} tabIndex={-1} aria-label="Resultados de noticias"><span role="status">{filtered.length} {isMonitor ? (filtered.length === 1 ? 'ficha de noticia' : 'fichas de noticias') : (filtered.length === 1 ? 'noticia localizada' : 'noticias localizadas')}</span><span>Más recientes primero · por fecha de publicación</span></div>
    {filtered.length > 0 ? <><div className="record-list">{filtered.slice((currentPage - 1) * PAGE_SIZE, currentPage * PAGE_SIZE).map(reference => <article className="record archive-record" key={reference.id}><div className="record-top"><span className="tag">Referencia de prensa</span>{newsTerritoryItem(reference, dossiers, usableLocations).locationKind === 'automated_unreviewed' && <span className="news-location-badge automatic">Ubicación automática · Sin revisar</span>}{newsTerritoryItem(reference, dossiers, usableLocations).locationKind === 'reviewed' && <span className="news-location-badge reviewed">Ubicación revisada</span>}<span className="record-id">{newsDossierFor(reference, dossiers).human_reviewed ? 'Ficha revisada editorialmente' : 'Campos pendientes'}</span></div><div className="record-body"><div><p className="record-category">{reference.publisher}</p><h3><button onClick={() => setSelected(reference)}>{reference.title}{reference.title_truncated ? '…' : ''}</button></h3><p className="archive-topics">{reference.topics.map(topicLabel).join(' · ')}</p></div><button className="open-record" aria-label={`Consultar noticia: ${reference.title}`} onClick={() => setSelected(reference)}><ArrowUpRight size={22} aria-hidden="true"/></button></div><div className="record-footer"><span>Publicación: {formatDate(reference.published_date)}</span><a href={reference.source_url} target="_blank" rel="noopener noreferrer">Leer en {reference.publisher}<ArrowUpRight size={13} aria-label="abre en otra pestaña"/></a></div></article>)}</div><nav className="archive-pagination" aria-label="Paginación de noticias"><button disabled={currentPage === 1} onClick={() => changePage(currentPage - 1)}>Anterior</button><span>Página {currentPage} de {pages}</span><button disabled={currentPage === pages} onClick={() => changePage(currentPage + 1)}>Siguiente</button></nav></> : <div className="empty"><h3>{invalidDates ? 'Revisa el rango de fechas' : 'No hay noticias con estos filtros'}</h3><p>La ausencia de referencias no implica ausencia de violaciones ni cobertura completa.</p><button onClick={reset}>Restablecer búsqueda de noticias</button></div>}
    {selected && selectedDossier && <Dialog title={selectedDossier.source_title} onClose={() => setSelected(null)}>
      <div className="dossier-meta"><span className="tag">Ficha de noticia</span><span className="tag">{selectedDossier.human_reviewed ? 'Ficha revisada editorialmente' : 'Extracción o revisión pendiente'}</span></div>
      <h3>Referencia bibliográfica</h3><dl>
        <dt>Medio</dt><dd>{selected.publisher}</dd>
        <dt>Título de la fuente</dt><dd>{selectedDossier.source_title}</dd>
        <dt>Fecha de publicación</dt><dd>{formatDate(selected.published_date)}.</dd>
        <dt>Temas detectados automáticamente</dt><dd>{selected.topics.map(topicLabel).join(' · ')}</dd>
        <dt>Vínculo territorial estimado</dt><dd>{scopeLabels[selected.scope_hint]}. {selected.scope_terms.length ? `Términos detectados: ${selected.scope_terms.join(', ')}. ` : 'Sin términos suficientes. '}Este indicio automático no confirma jurisdicción ni pertinencia.</dd>
        <dt>Localización de la referencia</dt><dd>{archiveInstant(selected.discovery.retrieved_at)} (El Salvador). Búsqueda de términos en título y URL a partir de metadatos públicos del medio; no equivale a revisión editorial.</dd>
        <dt>Identificador estable</dt><dd className="citation">{selected.id}</dd>
      </dl>
      {selected.title_truncated && !selectedDossier.human_reviewed && <p>El título está abreviado; consulta su versión íntegra en el medio.</p>}
      <a className="primary" href={selected.source_url} target="_blank" rel="noopener noreferrer">Consultar noticia original <ArrowUpRight size={16} aria-label="abre en otra pestaña"/></a>
      {selectedLocation && <section className="news-automatic-location" aria-label="Ubicación automática sin revisar"><h3>Ubicación automática · Sin revisar</h3><p>{selectedLocation.method === 'gemini_municipality_reference' ? 'Departamento obtenido del municipio o distrito citado:' : selectedLocation.method === 'gemini_department_corroborated' ? 'Departamento corroborado automáticamente en la misma fuente:' : 'Departamento mencionado en la fuente:'} <strong>{selectedLocation.department}</strong>.</p>{selectedLocation.resolution && <p>Localidad citada: <strong>{selectedLocation.resolution.municipality}</strong>. Correspondencia administrativa según la <a href={selectedLocation.resolution.source_url} target="_blank" rel="noopener noreferrer">referencia oficial de municipios y distritos</a>. Se conserva el nombre de la fuente; no se asignan coordenadas personales.</p>}{selectedLocation.corroboration && <p>Se comprobó que otro párrafo de la misma noticia menciona expresamente este departamento. La corroboración automática no equivale a revisión humana.</p>}<p>Esta extracción automática no confirma el lugar de una violación ni ha sido revisada por una persona. La ficha estructurada mantiene su propio estado de revisión.</p><dl><dt>Fuente</dt><dd><a href={selectedLocation.source_url} target="_blank" rel="noopener noreferrer">{selectedLocation.publisher}<ArrowUpRight size={13} aria-label="abre en otra pestaña"/></a></dd><dt>Extracción</dt><dd>{archiveInstant(selectedLocation.extracted_at)} (El Salvador)</dd><dt>Fuente comprobada</dt><dd>{archiveInstant(selectedLocation.source_checked_at)} (El Salvador)</dd></dl></section>}
      <NewsDossier dossier={selectedDossier}/>
      {linked.length > 0 && <><h3>Fichas de casos relacionadas</h3><p>La misma fuente está citada en estas fichas públicas. Su estado de revisión se muestra por separado; el enlace no valida automáticamente esta noticia.</p><div className="archive-linked-records">{linked.map(record => <button key={record.id} onClick={() => openRecord(record.id)}>{record.title} <ArrowUpRight size={14} aria-hidden="true"/></button>)}</div></>}
      <p className="filter-note">{archive.policy.rights}</p>
    </Dialog>}
  </section>;
}
