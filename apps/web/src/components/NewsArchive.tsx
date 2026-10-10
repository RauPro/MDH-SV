import { useEffect, useMemo, useRef, useState } from 'react';
import { flushSync } from 'react-dom';
import { ArrowUpRight, Download, Search } from 'lucide-react';
import { formatDate, type Publication } from '../catalog';
import { archiveInstant, archiveMonth, collectionStatusLabel, filterNews, invalidArchiveRange, parseNewsArchive, relatedPublications, scopeLabels, topicLabel, type NewsArchiveData, type NewsFilters, type NewsReference } from '../newsArchive';
import { Dialog } from './Dialog';

const PAGE_SIZE = 30;
const emptyFilters: NewsFilters = { query: '', publisher: '', topic: '', from: '', to: '', scope: 'el_salvador' };

export function NewsArchive({ onOpenRecord, records }: { onOpenRecord: (id: string) => void; records: Publication[] }) {
  const [archive, setArchive] = useState<NewsArchiveData | null>(null);
  const [error, setError] = useState(false);
  const [attempt, setAttempt] = useState(0);
  const [filters, setFilters] = useState<NewsFilters>(emptyFilters);
  const [page, setPage] = useState(1);
  const [selected, setSelected] = useState<NewsReference | null>(null);
  const resultsRef = useRef<HTMLDivElement>(null);
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
  const invalidDates = invalidArchiveRange(filters.from, filters.to);
  const filtered = useMemo(() => filterNews(archive?.records ?? [], filters), [archive, filters]);
  const topics = [...new Set(archive?.records.flatMap(record => record.topics) ?? [])].sort((a, b) => topicLabel(a).localeCompare(topicLabel(b), 'es'));
  const pages = Math.max(1, Math.ceil(filtered.length / PAGE_SIZE));
  const reset = () => { setFilters(emptyFilters); setPage(1); };
  const update = (key: keyof NewsFilters, value: string) => { setFilters(current => ({ ...current, [key]: value })); setPage(1); };
  const changePage = (next: number) => {
    flushSync(() => setPage(next));
    resultsRef.current?.focus({ preventScroll: true });
    resultsRef.current?.scrollIntoView?.({ block: 'start', behavior: 'smooth' });
  };
  const linked = selected ? relatedPublications(selected, records) : [];
  const openRecord = (id: string) => {
    flushSync(() => setSelected(null));
    onOpenRecord(id);
  };

  if (error) return <section className="empty" role="alert"><h2>No pudimos cargar el archivo de noticias</h2><p>No mostraremos referencias ni cifras sin validar el archivo. Puedes volver a intentarlo.</p><button className="primary" onClick={() => { setError(false); setAttempt(value => value + 1); }}>Reintentar archivo</button></section>;
  if (!archive) return <p className="empty" role="status">Cargando el archivo de noticias…</p>;

  return <section className="records-section archive-section" aria-labelledby="archive-title">
    <div className="section-heading archive-intro"><div className="eyebrow">MEMORIA DOCUMENTAL · ARCHIVO BIBLIOGRÁFICO</div><h2 id="archive-title">Archivo de noticias</h2><p>Periodo de búsqueda: del {formatDate(archive.coverage.start_date)} al {formatDate(archive.coverage.end_date)}.</p><p>{archive.records.length} referencias · {archive.coverage.sources.length} medios · cobertura parcial.</p><p>Este intervalo indica el alcance solicitado. Consulta la cobertura por medio y mes para distinguir lo recorrido, lo pendiente y los fallos.</p></div>
    <aside className="notice archive-detail-note"><div><strong>Son referencias de prensa, con revisión pendiente.</strong> Las etiquetas se asignan mediante búsqueda automática de términos. La pertinencia, la jurisdicción salvadoreña y los hechos todavía no están evaluados. Estas noticias no se suman a los casos del monitor.</div></aside>
    <details className="archive-coverage"><summary>Ver cobertura por medio y mes</summary><p>{archive.policy.selection_method} «API recorrida» indica que se consultaron las páginas disponibles del medio; no acredita un archivo exhaustivo ni una revisión del contenido.</p><p>Archivo generado: {archiveInstant(archive.generated_at)} (El Salvador). Antecedente de investigación: artículo incorporado a IEEE Xplore el {formatDate(archive.paper.publication_date)}. {archive.paper.publication_date_basis} La fecha del paper no determina el inicio del archivo histórico.</p>
      {archive.coverage.sources.map(source => <details className="archive-source" key={source.id}><summary>{source.publisher} · {source.records} referencias · {collectionStatusLabel(source.status)}</summary><div className="archive-table-wrap"><table><caption>Cobertura mensual de {source.publisher}</caption><thead><tr><th scope="col">Mes</th><th scope="col">Referencias</th><th scope="col">Publicaciones recorridas</th><th scope="col">Estado</th></tr></thead><tbody>{source.months.map(item => <tr key={item.month}><th scope="row">{archiveMonth(item.month)}</th><td>{item.records}</td><td>{item.posts_scanned}</td><td>{collectionStatusLabel(item.status)}</td></tr>)}</tbody></table></div></details>)}
      <ul>{archive.policy.coverage_limitations.map(limit => <li key={limit}>{limit}</li>)}</ul>
    </details>
    <div className="downloads"><a href="/archive/news.json" download="mdh-sv-archivo-noticias.json"><Download size={16} aria-hidden="true"/>Descargar archivo JSON</a></div>
    <div className="filters archive-filters"><label className="search-label"><span>Buscar noticias</span><div className="search-box"><Search size={18} aria-hidden="true"/><input type="search" value={filters.query} onChange={event => update('query', event.target.value)} placeholder="Título, medio o tema"/></div></label><label>Medio<select value={filters.publisher} onChange={event => update('publisher', event.target.value)}><option value="">Todos los medios</option>{archive.coverage.sources.map(source => <option key={source.id}>{source.publisher}</option>)}</select></label><label>Vínculo territorial<select value={filters.scope} onChange={event => update('scope', event.target.value)}><option value="el_salvador">Referencia a El Salvador</option><option value="undetermined">Por determinar</option><option value="international_context">Contexto internacional</option><option value="">Todo el archivo</option></select></label><label>Tema detectado<select value={filters.topic} onChange={event => update('topic', event.target.value)}><option value="">Todos los temas</option>{topics.map(topic => <option key={topic} value={topic}>{topicLabel(topic)}</option>)}</select></label><label>Publicación desde<input type="date" value={filters.from} onChange={event => update('from', event.target.value)} aria-invalid={invalidDates} aria-describedby={invalidDates ? 'archive-date-error' : 'archive-date-help'}/></label><label>Publicación hasta<input type="date" value={filters.to} onChange={event => update('to', event.target.value)} aria-invalid={invalidDates} aria-describedby={invalidDates ? 'archive-date-error' : 'archive-date-help'}/></label><button className="reset" onClick={reset}>Restablecer filtros de noticias</button></div>
    <p className="filter-note">El vínculo territorial se estima con términos en título y URL; puede omitir noticias pertinentes o incluir otras por error. No confirma jurisdicción. Por defecto se muestran referencias a El Salvador; puedes consultar todo el archivo.</p>
    <p className="filter-note" id="archive-date-help">Las fechas corresponden a la publicación declarada por el medio; no a la fecha de los hechos.</p>
    {invalidDates && <p role="alert" id="archive-date-error">La fecha inicial debe ser válida y anterior o igual a la final.</p>}
    <div className="results-heading archive-results" ref={resultsRef} tabIndex={-1} aria-label="Resultados de noticias"><span role="status">{filtered.length} {filtered.length === 1 ? 'noticia localizada' : 'noticias localizadas'}</span><span>Más recientes primero · por fecha de publicación</span></div>
    {filtered.length > 0 ? <><div className="record-list">{filtered.slice((page - 1) * PAGE_SIZE, page * PAGE_SIZE).map(reference => <article className="record archive-record" key={reference.id}><div className="record-top"><span className="tag">Referencia de prensa</span><span className="record-id">Revisión pendiente</span></div><div className="record-body"><div><p className="record-category">{reference.publisher}</p><h3><button onClick={() => setSelected(reference)}>{reference.title}{reference.title_truncated ? '…' : ''}</button></h3><p className="archive-topics">{reference.topics.map(topicLabel).join(' · ')}</p></div><button className="open-record" aria-label={`Consultar noticia: ${reference.title}`} onClick={() => setSelected(reference)}><ArrowUpRight size={22} aria-hidden="true"/></button></div><div className="record-footer"><span>Publicación: {formatDate(reference.published_date)}</span><a href={reference.source_url} target="_blank" rel="noopener noreferrer">Leer en {reference.publisher}<ArrowUpRight size={13} aria-label="abre en otra pestaña"/></a></div></article>)}</div><nav className="archive-pagination" aria-label="Paginación de noticias"><button disabled={page === 1} onClick={() => changePage(page - 1)}>Anterior</button><span>Página {page} de {pages}</span><button disabled={page === pages} onClick={() => changePage(page + 1)}>Siguiente</button></nav></> : <div className="empty"><h3>{invalidDates ? 'Revisa el rango de fechas' : 'No hay noticias con estos filtros'}</h3><p>La ausencia de referencias no implica ausencia de violaciones ni cobertura completa.</p><button onClick={reset}>Restablecer búsqueda de noticias</button></div>}
    {selected && <Dialog title={selected.title} onClose={() => setSelected(null)}><div className="dossier-meta"><span className="tag">Referencia bibliográfica</span><span className="tag">Revisión pendiente</span></div><p className="archive-detail-note">La recolección conserva metadatos de prensa. No valida los hechos ni identifica automáticamente víctimas o responsables. La jurisdicción salvadoreña no ha sido evaluada.</p><dl><dt>Medio</dt><dd>{selected.publisher}</dd><dt>Fecha de publicación</dt><dd>{formatDate(selected.published_date)}. No se ha extraído la fecha del hecho.</dd><dt>Temas detectados automáticamente</dt><dd>{selected.topics.map(topicLabel).join(' · ')}</dd><dt>Vínculo territorial estimado</dt><dd>{scopeLabels[selected.scope_hint]}. {selected.scope_terms.length ? `Términos detectados: ${selected.scope_terms.join(', ')}. ` : 'Sin términos suficientes. '}Clasificación automática; no confirma jurisdicción ni pertinencia.</dd><dt>Localización de la referencia</dt><dd>{archiveInstant(selected.discovery.retrieved_at)} (El Salvador). Búsqueda de términos en título y URL mediante la API pública del medio; no equivale a lectura o revisión editorial.</dd><dt>Identificador estable</dt><dd className="citation">{selected.id}</dd></dl>{selected.title_truncated && <p>El título está abreviado; consulta su versión íntegra en el medio.</p>}<a className="primary" href={selected.source_url} target="_blank" rel="noopener noreferrer">Consultar noticia original <ArrowUpRight size={16} aria-label="abre en otra pestaña"/></a><h3>Ficha de investigación</h3>{linked.length ? <><p>La misma fuente está citada en estas fichas públicas. Su estado de revisión se muestra por separado; el enlace no valida automáticamente esta referencia.</p><div className="archive-linked-records">{linked.map(record => <button key={record.id} onClick={() => openRecord(record.id)}>{record.title} <ArrowUpRight size={14} aria-hidden="true"/></button>)}</div></> : <p>Extracción estructurada y revisión pendientes. Esta referencia todavía no tiene una ficha asociada de hechos, personas afectadas y ubicación. El proceso editorial debe evaluar la fuente antes de incorporarla al monitor.</p>}<p className="filter-note">{archive.policy.rights}</p></Dialog>}
  </section>;
}
