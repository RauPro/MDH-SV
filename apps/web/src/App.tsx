import { useEffect, useMemo, useState } from 'react';
import { ArrowUpRight, BookOpen, Download, Moon, Search, Shield, ShieldAlert, FileText, Users, Sun } from 'lucide-react';
import { Dialog } from './components/Dialog';
import { StructuredDossier } from './components/StructuredDossier';
import { NewsArchive } from './components/NewsArchive';
import { SalvadorMap } from './components/SalvadorMap';
import { DEPARTMENTS } from './geography/elSalvador';
import { matchesTerritory, summarizeTerritory, UNKNOWN_DEPARTMENT } from './geography/territory';
import { citation, eventDate, filterRecords, formatDate, parseCatalog, type Catalog } from './catalog';
import './App.css';

const REPO = 'https://github.com/RauPro/MDH-SV';
const typeLabel = { incident: 'Reporte individual', aggregate: 'Reporte colectivo', publication: 'Informe de contexto' };
const informationViews = new Set(['metodologia', 'datos', 'privacidad']);
function currentHash() {
  try { return decodeURIComponent(location.hash.slice(1)); } catch { return ''; }
}
function isRecordRoute(hash: string) {
  return Boolean(hash && !['archivo', 'contenido'].includes(hash) && !informationViews.has(hash));
}
function initialTheme(): 'dark' | 'light' {
  try { return localStorage.getItem('mdh_theme') === 'dark' ? 'dark' : 'light'; } catch { return 'light'; }
}

export function App() {
  const [catalog, setCatalog] = useState<Catalog | null>(null);
  const [error, setError] = useState(false);
  const [attempt, setAttempt] = useState(0);
  const [theme, setTheme] = useState(initialTheme);
  const [query, setQuery] = useState('');
  const [category, setCategory] = useState('');
  const [department, setDepartment] = useState('');
  const [kind, setKind] = useState('incident');
  const [from, setFrom] = useState('');
  const [to, setTo] = useState('');
  const [prisonOnly, setPrisonOnly] = useState(false);
  const [page, setPage] = useState(1);
  const [modal, setModal] = useState<string | null>(() => { const hash = currentHash(); return hash && !['archivo', 'reportes', 'contenido'].includes(hash) ? hash : null; });
  const [view, setView] = useState<'records' | 'archive'>(() => location.hash === '#archivo' ? 'archive' : 'records');
  const [collection, setCollection] = useState<'news' | 'reports'>(() => isRecordRoute(currentHash()) ? 'reports' : 'news');
  const [copyStatus, setCopyStatus] = useState('');

  useEffect(() => {
    document.documentElement.className = theme;
    try { localStorage.setItem('mdh_theme', theme); } catch { /* Storage may be unavailable in private contexts. */ }
  }, [theme]);
  useEffect(() => {
    const controller = new AbortController();
    const timer = window.setTimeout(() => controller.abort(), 12000);
    let active = true;
    fetch('/data/catalog.json', { signal: controller.signal }).then(async r => {
      if (!r.ok) throw new Error('Dataset unavailable');
      return parseCatalog(await r.json());
    }).then(data => { if (active) setCatalog(data); }).catch(() => { if (active) setError(true); }).finally(() => clearTimeout(timer));
    return () => { active = false; clearTimeout(timer); controller.abort(); };
  }, [attempt]);
  useEffect(() => {
    const change = () => {
      const hash = currentHash();
      if (hash === 'archivo') setView('archive');
      else if (hash === '' || isRecordRoute(hash)) {
        setView('records');
        setCollection(hash ? 'reports' : 'news');
      }
      setModal(hash && !['archivo', 'reportes', 'contenido'].includes(hash) ? hash : null);
      setCopyStatus('');
    };
    window.addEventListener('hashchange', change);
    return () => window.removeEventListener('hashchange', change);
  }, []);
  const open = (id: string) => { setCopyStatus(''); location.hash = id; setModal(id); };
  const close = () => { history.replaceState(null, '', location.pathname + location.search + (view === 'archive' ? '#archivo' : collection === 'reports' ? '#reportes' : '')); setModal(null); setCopyStatus(''); };
  const selectCollection = (next: 'news' | 'reports') => {
    setView('records'); setCollection(next); setModal(null); setCopyStatus('');
    history.replaceState(null, '', location.pathname + location.search + (next === 'reports' ? '#reportes' : ''));
  };
  const openRelatedRecord = (id: string) => { setView('records'); setCollection('reports'); open(id); };
  const reset = () => { setQuery(''); setCategory(''); setDepartment(''); setKind('incident'); setFrom(''); setTo(''); setPrisonOnly(false); setPage(1); };
  const records = catalog?.records ?? [];
  const invalidDates = Boolean(from && to && from > to);
  const recordsInContext = useMemo(() => invalidDates ? [] : filterRecords(catalog?.records ?? [], query, category, '')
    .filter(r => (!prisonOnly || r.penitentiary_related)
      && (!from || (r.incident_date_end || r.incident_date || '') >= from)
      && (!to || Boolean(r.incident_date && r.incident_date <= to))),
    [catalog, query, category, prisonOnly, from, to, invalidDates]);
  const filtered = useMemo(() => recordsInContext
    .filter(r => (!kind || r.record_type === kind) && matchesTerritory(r, department))
    .sort((a,b) => (b.incident_date || b.published_date).localeCompare(a.incident_date || a.published_date) || a.id.localeCompare(b.id)),
    [recordsInContext, department, kind]);
  const territory = useMemo(() => summarizeTerritory(recordsInContext), [recordsInContext]);
  const selectMapDepartment = (value: string) => { setDepartment(value); setKind('incident'); setPage(1); };
  const activeRecord = records.find(r => modal === r.id);
  const categories = [...new Set(records.map(r => r.category))].sort();
  const departments = DEPARTMENTS.map(d => d.name).toSorted((a,b) => a.localeCompare(b, 'es'));
  const totalIndividual = records.filter(r => r.record_type === 'incident').length;
  const totalCollective = records.filter(r => r.record_type === 'aggregate').length;
  const documents = new Set(records.flatMap(r => [r.source_url, ...r.related_sources.map(s => s.url)])).size;
  const update = (setter: (v: string) => void) => (value: string) => { setter(value); setPage(1); };
  async function copy(value: string) {
    try { await navigator.clipboard.writeText(value); setCopyStatus('Cita copiada.'); }
    catch { setCopyStatus('No se pudo copiar. Puedes seleccionar la cita y copiarla manualmente.'); }
  }

  return <>
    <a className="skip-link" href="#contenido">Saltar al contenido</a>
    <header className="header"><a className="brand" href="/" aria-label="MDH-SV, inicio"><ShieldAlert size={24} aria-hidden="true"/><span>MDH<span className="brand-muted">—SV</span></span></a>
      <nav aria-label="Navegación principal"><button onClick={() => open('metodologia')}>Metodología</button><button onClick={() => open('datos')}>Datos y API</button><button className="icon-button" aria-label={theme === 'dark' ? 'Activar tema claro' : 'Activar tema oscuro'} onClick={() => setTheme(theme === 'dark' ? 'light' : 'dark')}>{theme === 'dark' ? <Sun size={18}/> : <Moon size={18}/>}</button></nav>
    </header>
    <main id="contenido" tabIndex={-1}>
      <section className="hero" aria-labelledby="title"><div className="eyebrow">EL SALVADOR <span>MONITOR INDEPENDIENTE · FUENTES ABIERTAS</span></div>
        <h1 id="title">Monitor de Derechos Humanos<br/><span>de El Salvador</span></h1>
        <p className="hero-description">Monitor de presuntas violaciones de derechos humanos en El Salvador. Consulta reportes, contrasta sus fuentes y reutiliza los datos para investigación y seguimiento institucional.</p>
        <div className="hero-bottom"><span>Acceso abierto, sin registro</span><span>{catalog ? `Última revisión del catálogo de reportes: ${formatDate(catalog.updated_at)}` : 'Cargando conjunto documental…'}</span></div>
      </section>
      <aside className="notice"><Shield size={19} aria-hidden="true"/><div><strong>La fuente es el punto de partida.</strong> Cobertura inicial y parcial. Cada ficha atribuye lo reportado a su fuente; no es una determinación judicial ni una verificación independiente. El estado de revisión se indica en cada ficha. <button className="text-button" onClick={() => open('metodologia')}>Leer criterios y límites <ArrowUpRight size={13}/></button></div></aside>
      <nav className="workspace-nav" aria-label="Colecciones del monitor"><button aria-pressed={view === 'records'} onClick={() => selectCollection('news')}>Fichas y mapa</button><button aria-pressed={view === 'archive'} onClick={() => { setView('archive'); close(); history.replaceState(null, '', '#archivo'); }}>Archivo de noticias</button><span>De la fuente al análisis documentado</span></nav>
      {view === 'archive' && <NewsArchive key="archive" records={records} onOpenRecord={openRelatedRecord}/>}
      {view === 'records' && <nav className="monitor-collections" aria-label="Contenido de fichas y mapa">
        <button aria-pressed={collection === 'news'} onClick={() => selectCollection('news')}>Noticias</button>
        <button aria-pressed={collection === 'reports'} onClick={() => selectCollection('reports')}>Reportes documentados{catalog ? ` (${records.length})` : ''}</button>
        <p>Consulta todas las noticias y sus fichas. Cada colección mantiene sus propios recuentos en el mapa.</p>
      </nav>}
      {view === 'records' && collection === 'news' && <NewsArchive key="monitor" view="monitor" records={records} onOpenRecord={openRelatedRecord}/>}
      {view === 'records' && collection === 'reports' && (error ? <section className="empty" role="alert"><h2>No pudimos cargar los datos</h2><p>No mostraremos cifras incompletas. Intenta nuevamente.</p><button className="primary" onClick={() => { setError(false); setAttempt(n => n + 1); }}>Reintentar</button></section>
        : !catalog ? <p role="status" className="empty">Cargando los reportes y sus fuentes…</p>
        : <>
          <section className="metrics" aria-label="Cobertura del monitor"><Metric number={totalIndividual} label="Reportes individuales" tone="red" icon={<ShieldAlert size={18}/>}/><Metric number={totalCollective} label="Reportes colectivos" tone="amber" icon={<Users size={18}/>}/><Metric number={documents} label="Documentos fuente" tone="green" icon={<FileText size={18}/>}/><div className="metric metric-note"><BookOpen aria-hidden="true" size={21}/><p>Los registros no equivalen a personas únicas. No se suman cifras de víctimas.</p></div></section>
          <SalvadorMap selected={department} onSelect={selectMapDepartment} counts={territory.counts}
            located={territory.located} unlocated={territory.unlocated} invalidDates={invalidDates}/>
          <section className="records-section" aria-labelledby="records-title"><div className="section-heading"><div className="eyebrow">CONSULTA DOCUMENTAL</div><h2 id="records-title">Explorar el monitor</h2><p>Filtra por tipo de reporte, derecho, departamento o fecha del hecho.</p></div>
            <div className="filters"><label className="search-label"><span>Buscar</span><div className="search-box"><Search aria-hidden="true" size={18}/><input type="search" value={query} onChange={e => update(setQuery)(e.target.value)} placeholder="Hecho, tema, fuente o departamento"/></div></label>
              <label>Tipo de registro<select value={kind} onChange={e => update(setKind)(e.target.value)}><option value="">Todos los registros</option><option value="incident">Reportes individuales</option><option value="aggregate">Reportes colectivos</option><option value="publication">Informes de contexto</option></select></label>
              <label>Derecho o temática<select value={category} onChange={e => update(setCategory)(e.target.value)}><option value="">Todas las temáticas</option>{categories.map(c => <option key={c}>{c}</option>)}</select></label>
              <label>Departamento<select value={department} onChange={e => update(setDepartment)(e.target.value)}><option value="">Todos / alcance nacional</option><option value={UNKNOWN_DEPARTMENT}>Sin departamento (individuales)</option>{departments.map(d => <option key={d}>{d}</option>)}</select></label>
              <label>Hecho desde<input type="date" value={from} onChange={e => update(setFrom)(e.target.value)} aria-invalid={invalidDates}/></label><label>Hecho hasta<input type="date" value={to} onChange={e => update(setTo)(e.target.value)} aria-invalid={invalidDates}/></label>
              <label className="checkbox"><input type="checkbox" checked={prisonOnly} onChange={e => { setPrisonOnly(e.target.checked); setPage(1); }}/>Custodia o reclusión</label>
              <button className="reset" onClick={reset}>Restablecer filtros</button>
            </div>
            {(from || to) && <p className="filter-note">Al filtrar por fecha del hecho se excluyen las fichas sin fecha conocida y los informes de contexto. Los intervalos se incluyen si se solapan con la consulta.</p>}
            {invalidDates && <p role="alert">La fecha inicial debe ser anterior o igual a la final.</p>}
            <div className="results-heading"><span role="status">{invalidDates ? 0 : filtered.length} {filtered.length === 1 && !invalidDates ? 'resultado' : 'resultados'}</span><span>Más recientes primero · sin fecha del hecho, por publicación</span></div>
            {!invalidDates && filtered.length > 0 ? <div className="record-list">{filtered.slice(0, page * 20).map(r => <article className="record" key={r.id}>
              <div className="record-top"><span className={`tag tag-${r.record_type}`}>{typeLabel[r.record_type]}</span><span className="record-id">{r.id.toUpperCase()}</span></div>
              <div className="record-body"><div><p className="record-category">{r.category} · {r.department || (r.record_type === 'publication' || r.record_type === 'aggregate' ? 'Alcance nacional' : 'Ubicación no determinada')}</p><h3><button onClick={() => open(r.id)}>{r.title}</button></h3><p>{r.summary}</p></div><button className="open-record" onClick={() => open(r.id)} aria-label={`Abrir ficha: ${r.title}`}><ArrowUpRight size={24}/></button></div>
              <div className="record-footer"><span>{r.incident_date ? `Hecho: ${eventDate(r)}` : r.record_type === 'publication' ? `Publicación: ${formatDate(r.published_date)}` : 'Fecha del hecho no determinada'}</span><a href={r.source_url} target="_blank" rel="noopener noreferrer">{r.publisher} <ArrowUpRight size={13} aria-label="abre en otra pestaña"/></a></div>
            </article>)}</div> : <div className="empty"><h3>{invalidDates ? 'Revisa el rango de fechas' : 'No hay registros con estos filtros'}</h3><p>La ausencia de registros en este conjunto no implica ausencia de violaciones.</p><button onClick={reset}>Restablecer filtros</button></div>}
            {!invalidDates && filtered.length > page * 20 && <button className="load-more" onClick={() => setPage(p => p + 1)}>Mostrar más registros</button>}
          </section>
          <section className="coverage"><h2>Leer los datos en su contexto</h2><p>Este monitor empieza con un conjunto documental acotado. Las fechas de publicación no son fechas de los hechos. La ubicación se limita al departamento; no se publican domicilios ni coordenadas personales. Los informes nacionales no se asignan a San Salvador ni se distribuyen artificialmente en un mapa.</p><button onClick={() => open('datos')}><Download size={16}/> Descargar el conjunto y su metodología</button></section>
        </>)}
    </main>
    <footer><div><strong>MDH—SV</strong><p>Información trazable para la investigación y la defensa de derechos.</p><p>Proyecto independiente. Las organizaciones citadas no patrocinan ni validan este monitor.</p></div><nav aria-label="Información del proyecto"><button onClick={() => open('metodologia')}>Metodología y límites</button><button onClick={() => open('privacidad')}>Privacidad y correcciones</button><a href={REPO} target="_blank" rel="noopener noreferrer">Código y cambios <ArrowUpRight size={13}/></a></nav></footer>
    {activeRecord && <Dialog title={activeRecord.title} onClose={close}><div className="dossier-meta"><span className="tag">{typeLabel[activeRecord.record_type]}</span><code>{activeRecord.id}</code></div><p className="dossier-review">{activeRecord.human_reviewed ? `Revisión editorial por ${activeRecord.reviewed_by}` : "Fuente consultada · revisión humana pendiente"}. Hechos atribuidos a {activeRecord.publisher}.</p><StructuredDossier record={activeRecord}/><details className="dossier-provenance"><summary>Procedencia, revisión y protección de personas</summary><dl><dt>Estado de revisión</dt><dd>{activeRecord.human_reviewed ? `Revisión editorial por ${activeRecord.reviewed_by} el ${formatDate(activeRecord.reviewed_at!)}. ${activeRecord.review_notes}` : `Fuente consultada · revisión humana pendiente. Hechos reportados por ${activeRecord.publisher}; sin corroboración independiente por MDH-SV.`}</dd><dt>Publicación de la fuente</dt><dd>{formatDate(activeRecord.published_date)}</dd><dt>Localizador en el documento</dt><dd>{activeRecord.source_locator}</dd><dt>Protección de personas</dt><dd>{activeRecord.privacy_note}</dd><dt>Fuente consultada el</dt><dd>{formatDate(activeRecord.source_checked_at)}. {activeRecord.source_check_method}</dd></dl></details><a className="primary" href={activeRecord.source_url} target="_blank" rel="noopener noreferrer">Consultar documento original <ArrowUpRight size={16}/></a>{activeRecord.related_sources.length > 0 && <section className="related-sources"><h3>Actualizaciones y fuentes complementarias</h3>{activeRecord.related_sources.map(source => <div key={source.url}><a href={source.url} target="_blank" rel="noopener noreferrer">{source.title} <ArrowUpRight size={14}/></a><p>{formatDate(source.published_date)} · {source.locator}</p></div>)}</section>}<h3>Citar y contrastar</h3><p className="citation">{citation(activeRecord)}</p><button onClick={() => copy(citation(activeRecord))}>Copiar cita</button><p role="status">{copyStatus}</p><button className="text-button" onClick={() => open('privacidad')}>Solicitar una corrección</button></Dialog>}
    {modal === 'metodologia' && <Dialog title="Metodología y límites" onClose={close}><h3>Qué registra el monitor</h3><p>Reportes públicos de presuntas violaciones, con atribución a la organización o medio que los documenta. Cada ficha conserva una referencia localizable. Un reporte individual, un conjunto de casos y un informe de contexto son unidades diferentes.</p><h3>Qué significa «fuente consultada»</h3><p>Se ha consultado el documento y elaborado una ficha mediante asistencia automatizada. Esto no prueba por sí solo los hechos descritos. Las fichas iniciales tienen revisión humana pendiente. No se asigna certeza jurídica por palabras clave, número de medios ni hashes.</p><h3>Archivo de noticias y relación con WSDS</h3><p>El archivo bibliográfico tiene como objetivo recuperar noticias desde el 1 de enero de 2010. El periodo de búsqueda y su avance se muestran en el archivo por medio y mes; la cobertura es parcial y puede haber periodos pendientes o sin referencias. El artículo original en IEEE es un antecedente de la investigación, no el inicio del archivo. Se recopilan metadatos de los medios registrados en el archivo. La cobertura indica los límites de cada fuente, incluidos los accesos restringidos. La selección automática por título o URL puede omitir noticias relevantes o incluir contexto de otros países. Todas las noticias se pueden consultar en Fichas y mapa. El mapa de noticias sólo asigna departamentos cuando existe una ficha pública revisada con jurisdicción salvadoreña y ubicación explícita. Las demás siguen en la lista, sin ubicación pública. Las noticias mantienen un recuento separado de los reportes documentados y no se cuentan como violaciones. La extracción y revisión de sus hechos es una etapa separada.</p><a href="https://ieeexplore.ieee.org/document/10933836" target="_blank" rel="noopener noreferrer">Artículo de investigación original</a><h3>Selección y cobertura</h3><p>El conjunto reúne publicaciones de HRW, Cristosal, CIDH, Corte IDH, Naciones Unidas, Amnistía Internacional y Citizen Lab / Access Now. No es representativa de todo el país ni de todos los periodos. El registro más reciente no indica la fecha del último hecho ocurrido. Hay sesgos de acceso, publicación y selección.</p><h3>Protección y conteos</h3><p>Se omiten identidades de menores, domicilios y coordenadas personales. En casos ya ampliamente públicos pueden conservarse el nombre del defensor o el seudónimo judicial necesario para identificar la fuente. Los documentos externos pueden contener información sensible. No se suman víctimas de distintas fuentes. Una misma publicación puede respaldar varios casos; eso no constituye corroboración multifuente.</p><h3>Revisión y cambios</h3><p>La recolección cada seis horas registra candidatas en una cola privada. Aprobar una candidata exige revisión humana identificada y prepara una propuesta; no la publica automáticamente. Incorporar una ficha pública exige una fuente accesible, localizador, revisión de fechas y protección de identidad. Los cambios se registran en Git. Los datos generados durante el prototipo fueron excluidos por falta de procedencia fiable.</p><p>Para uso institucional, contraste la fuente original y realice una revisión humana especializada antes de formular conclusiones o tomar decisiones sobre personas.</p><a href={`${REPO}/blob/main/docs/METHODOLOGY.md`} target="_blank" rel="noopener noreferrer">Consultar protocolo completo</a></Dialog>}
    {modal === 'datos' && <Dialog title="Datos abiertos y acceso programático" onClose={close}><p>Estas descargas contienen el catálogo de reportes documentados, con su tipo de registro y estado de revisión. Las noticias tienen su propio archivo bibliográfico y fichas. Las descargas incluyen cada conjunto completo, sin aplicar los filtros de pantalla.</p><div className="downloads">{[['JSON','catalog.json'],['CSV','records.csv'],['GeoJSON','records.geojson'],['Parquet','records.parquet'],['Manifest de integridad','manifest.json']].map(([name,file]) => <a key={file} href={`/data/${file}`} download><Download size={16}/>{name}</a>)}</div><p>GeoJSON conserva geometrías nulas: no se inventan puntos para sucesos sin ubicación publicable. Los hashes del manifest comprueban los bytes de las descargas; no verifican la veracidad de los hechos ni el contenido de las fuentes.</p><h3>API pública para otros proyectos</h3><p>Acceso abierto y de solo lectura, sin clave. Consulta fichas con sus campos estructurados, estadísticas y el archivo bibliográfico. Las respuestas conservan la atribución y el estado de revisión.</p><pre><code>{`curl --fail '${location.origin}/api/v1/records?limit=20'\ncurl --fail '${location.origin}/api/v1/news?limit=20'`}</code></pre><div className="downloads"><a href="/api/v1/openapi.json" target="_blank" rel="noopener noreferrer">Especificación OpenAPI</a><a href={`${REPO}/blob/main/docs/API.md`} target="_blank" rel="noopener noreferrer">Guía y ejemplos</a></div><h3>MCP remoto</h3><p>Añade esta URL en un cliente compatible con Streamable HTTP. Permite buscar noticias, consultar fichas y obtener estadísticas. Conserva también el servidor local del repositorio.</p><pre><code>{`${location.origin}/mcp`}</code></pre><p>Herramientas: <code>search_records</code>, <code>get_record</code>, <code>get_statistics</code> y <code>search_news</code>. Las noticias pendientes no son hechos corroborados.</p><p>Los metadatos y resúmenes propios se ofrecen bajo CC BY 4.0. Los documentos originales conservan sus derechos y condiciones. Cita a sus autores y a la versión del conjunto consultada.</p></Dialog>}
    {modal === 'privacidad' && <Dialog title="Privacidad y correcciones" onClose={close}><h3>Consulta sin registro</h3><p>No pedimos nombres, correo ni documentos. Las búsquedas se procesan en tu navegador; no se envían a un motor de IA. La preferencia de tema se guarda localmente. El proveedor de alojamiento puede tratar datos técnicos de las solicitudes, como la dirección IP, conforme a sus políticas.</p><p>Los enlaces externos abren sitios con sus propias políticas. Las tipografías se sirven desde este mismo sitio. No se cargan servicios de analítica ni rastreadores de terceros.</p><h3>Corregir una ficha</h3><p>Para errores no sensibles, usa el registro de incidencias del proyecto e indica únicamente el identificador de la ficha y la referencia pública que permite corregirla. GitHub es público: no adjuntes nombres de víctimas, testimonios privados ni documentos personales.</p><a href={`${REPO}/issues/new?title=Correcci%C3%B3n%20de%20registro`} target="_blank" rel="noopener noreferrer">Registrar corrección no sensible <ArrowUpRight size={14}/></a><h3>Información sensible y solicitudes de retiro</h3><p>Este sitio todavía no dispone de un canal confidencial para recibir denuncias o documentación sensible. No las envíes por GitHub. Puedes solicitar el retiro de una ficha indicando solo su identificador, sin explicar públicamente circunstancias privadas. El responsable del proyecto revisará la solicitud; no hay atención de emergencias ni plazo de respuesta garantizado.</p></Dialog>}
    {modal && !activeRecord && !['metodologia','datos','privacidad','contenido','archivo','reportes'].includes(modal) && catalog && <Dialog title="Ficha no disponible" onClose={close}><p>El identificador no existe en esta versión o el registro fue retirado. Consulta el monitor o el historial del proyecto.</p></Dialog>}
  </>;
}
function Metric({ number, label, tone, icon }: { number: number; label: string; tone: string; icon: React.ReactNode }) { return <div className={`metric metric-${tone}`}><div className="metric-heading"><span>{label}</span>{icon}</div><strong>{number.toLocaleString('es-SV')}</strong><small>Registros con referencia consultable</small></div>; }
export default App;
