import { beforeEach, expect, it, vi } from 'vitest';
import { act, fireEvent, render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import axe from 'axe-core';
import fixture from '../__fixtures__/newsArchive.json';
import catalog from '../__fixtures__/catalog.json';
import { formatDate, parseCatalog } from '../catalog';
import { NewsArchive } from './NewsArchive';
import { archiveInstant, type NewsArchiveData } from '../newsArchive';
import municipalityReference from '../../../../apps/pipeline/src/mdh_pipeline/data/el_salvador_municipalities.json';
import { NEWS_DOSSIER_FIELDS, pendingNewsDossier, type NewsDossier } from '../../../../shared/news-dossier.mjs';
import { automaticNewsDossier } from '../__fixtures__/automaticNewsDossier';

const PUBLIC_FIELD_PENDING = 'Dato aún no publicado';
const EMPTY = { schema_version: 1, dossiers: [] };
const processingEntry = (reference = fixture.records[0], status = 'pending') => ({ candidate_id: reference.id, source_url: reference.source_url, publisher: reference.publisher, published_date: reference.published_date, selection_content_sha256: reference.selection.evidence.content_sha256, status });
const processingFixture = (entries = fixture.records.map(reference => processingEntry(reference))) => ({ schema_version: 1, generated_at: '2026-10-10T10:00:00Z', entries });
const EMPTY_LOCATIONS = { schema_version: 1, generated_at: '2026-10-10T10:00:00Z', locations: [] };
const automatic = (reference = fixture.records[0]) => ({ candidate_id: reference.id, source_url: reference.source_url, publisher: reference.publisher, published_date: reference.published_date, department: 'San Salvador', review_status: 'automated_unreviewed', human_reviewed: false, method: 'gemini_explicit_department', extracted_at: '2026-10-10T10:00:00Z', source_checked_at: '2026-10-10T10:02:00Z', evidence: { paragraph_id: 'p1', content_sha256: reference.selection.evidence.content_sha256 } });
function serve(archive: unknown = fixture, dossiers: unknown = EMPTY, locations: unknown = EMPTY_LOCATIONS, processing: unknown = processingFixture()) {
  vi.stubGlobal('fetch', vi.fn(async (url: string) => ({ ok: true, json: async () => url === '/archive/dossiers.json' ? dossiers : url === '/archive/locations.json' ? locations : url === '/archive/processing.json' ? processing : archive })));
}
function approved(): NewsDossier {
  const dossier = pendingNewsDossier(fixture.records[0]);
  dossier.classification = { jurisdiction: 'SV', relevance: 'relevant', record_type: null };
  dossier.extraction_status = 'processed'; dossier.human_reviewed = true; dossier.reviewed_by = 'reviewer'; dossier.reviewed_at = '2026-10-10'; dossier.review_notes = 'Fixture ficticia: revisión editorial explícita de fuente y privacidad.';
  return dossier;
}
beforeEach(() => { vi.restoreAllMocks(); serve(); });
const ready = async () => { render(<NewsArchive records={[]} onOpenRecord={vi.fn()}/>); await screen.findByText('3 noticias seleccionadas'); };
it('lists every department mentioned by one news item while counting and displaying the news only once', async () => {
  const { department, evidence, ...common } = automatic();
  const location = { ...common, method: 'source_administrative_mentions', rule_version: 2,
    evidence: { content_sha256: evidence.content_sha256 }, mentions: [
      { method: 'source_explicit_department', department, paragraph_id: 'p1' },
      { method: 'source_municipality_reference', department: 'Ahuachapán', paragraph_id: 'p2', resolution: {
        type: 'municipality_to_department', municipality: 'Jujutla', municipality_kind: 'district',
        reference_version: municipalityReference.reference_version, source_url: `${municipalityReference.source.url}#page=6`,
      } },
    ],
  };
  serve(fixture, EMPTY, { ...EMPTY_LOCATIONS, schema_version: 5, locations: [location] });
  render(<NewsArchive view="monitor" records={[]} onOpenRecord={vi.fn()}/>);
  const sanSalvador = await screen.findByRole('button', { name: 'San Salvador: 1 noticia' });
  const ahuachapan = screen.getByRole('button', { name: 'Ahuachapán: 1 noticia' });
  expect(screen.getByText('noticia con departamento disponible')).toHaveTextContent('1 noticia con departamento disponible');
  expect(screen.getByText('noticia con menciones automáticas sin revisar')).toHaveTextContent('1 noticia con menciones automáticas sin revisar');
  expect(screen.getByText(/El total cuenta noticias únicas/)).toBeInTheDocument();
  await userEvent.click(sanSalvador);
  expect(screen.getByText('1 noticia seleccionada')).toBeInTheDocument();
  await userEvent.click(ahuachapan);
  expect(screen.getByText('1 noticia seleccionada')).toBeInTheDocument();
  await userEvent.click(screen.getByRole('button', { name: fixture.records[0].title }));
  const panel = screen.getByRole('region', { name: 'Ubicación automática sin revisar' });
  expect(panel).toHaveTextContent('sin asignar una ubicación principal');
  expect(within(panel).getAllByRole('listitem')).toHaveLength(2);
  expect(panel).toHaveTextContent('San Salvador');
  expect(panel).toHaveTextContent('Ahuachapán');
  expect(panel).toHaveTextContent('Distrito citado: Jujutla');
  expect(panel).toHaveTextContent('cuenta una sola vez en el total');
  expect(screen.getByText('Departamento').closest('.dossier-field')).toHaveTextContent(PUBLIC_FIELD_PENDING);
  await userEvent.click(screen.getByRole('button', { name: 'Cerrar' }));
  await userEvent.click(screen.getByRole('button', { name: /Sin ubicación pública en El Salvador/ }));
  expect(screen.getByText('2 noticias seleccionadas')).toBeInTheDocument();
  expect(screen.queryByRole('button', { name: fixture.records[0].title })).not.toBeInTheDocument();
});
it.each(['source_explicit_department', 'source_municipality_reference'])('explains %s independently of Gemini and pending dossier fields', async method => {
  const municipal = method === 'source_municipality_reference';
  const location = { ...automatic(), method, rule_version: 1, department: municipal ? 'Ahuachapán' : 'San Salvador',
    ...(municipal ? { resolution: { type: 'municipality_to_department', municipality: 'Jujutla', municipality_kind: 'district',
      reference_version: municipalityReference.reference_version, source_url: `${municipalityReference.source.url}#page=6` } } : {}),
  };
  serve(fixture, EMPTY, { ...EMPTY_LOCATIONS, schema_version: 4, locations: [location] });
  render(<NewsArchive view="monitor" records={[]} onOpenRecord={vi.fn()}/>);
  await screen.findByRole('button', { name: `${location.department}: 1 noticia` });
  await userEvent.click(screen.getByRole('button', { name: fixture.records[0].title }));
  const panel = screen.getByRole('region', { name: 'Ubicación automática sin revisar' });
  expect(panel).toHaveTextContent('su identificación no depende de que la ficha esté completa');
  expect(panel).toHaveTextContent('Puede corresponder a una referencia secundaria del artículo');
  expect(panel).not.toHaveTextContent('Departamento corroborado');
  expect(panel).toHaveTextContent('no confirma el lugar de una violación');
  if (municipal) {
    expect(panel).toHaveTextContent('Departamento obtenido del municipio o distrito citado: Ahuachapán');
    expect(within(panel).getByRole('link', { name: 'referencia oficial de municipios y distritos' })).toHaveAttribute('href', location.resolution?.source_url);
  }
  const sheet = screen.getByRole('region', { name: 'Ficha estructurada de la noticia' });
  expect(within(sheet).getByText('Departamento').closest('.dossier-field')).toHaveTextContent(PUBLIC_FIELD_PENDING);
});
it('explains a department resolved from a cited municipality without pretending it was explicit or reviewed', async () => {
  const location = { ...automatic(), department: 'Ahuachapán', method: 'gemini_municipality_reference', resolution: {
    type: 'municipality_to_department', municipality: 'Jujutla', municipality_kind: 'district',
    reference_version: municipalityReference.reference_version, source_url: `${municipalityReference.source.url}#page=6`,
  } };
  serve(fixture, EMPTY, { ...EMPTY_LOCATIONS, locations: [location] });
  render(<NewsArchive view="monitor" records={[]} onOpenRecord={vi.fn()}/>);
  await screen.findByRole('button', { name: 'Ahuachapán: 1 noticia' });
  await userEvent.click(screen.getByRole('button', { name: fixture.records[0].title }));
  const panel = screen.getByRole('region', { name: 'Ubicación automática sin revisar' });
  expect(panel).toHaveTextContent('Departamento obtenido del municipio o distrito citado: Ahuachapán');
  expect(panel).toHaveTextContent('Localidad citada: Jujutla');
  expect(within(panel).getByRole('link', { name: 'referencia oficial de municipios y distritos' })).toHaveAttribute('href', location.resolution.source_url);
  const sheet = screen.getByRole('region', { name: 'Ficha estructurada de la noticia' });
  expect(sheet).toHaveTextContent(PUBLIC_FIELD_PENDING);
});
it('labels same-source department corroboration as automatic and retains the pending dossier', async () => {
  const location = { ...automatic(), method: 'gemini_department_corroborated', evidence: { paragraph_id: 'p3', content_sha256: 'a'.repeat(64) },
    corroboration: { strategy: 'same_article_explicit_department', model_paragraph_id: 'p1', explicit_paragraph_id: 'p3', rule_version: 1 },
  };
  serve(fixture, EMPTY, { ...EMPTY_LOCATIONS, schema_version: 3, locations: [location] });
  render(<NewsArchive view="monitor" records={[]} onOpenRecord={vi.fn()}/>);
  await screen.findByRole('button', { name: 'San Salvador: 1 noticia' });
  await userEvent.click(screen.getByRole('button', { name: fixture.records[0].title }));
  const panel = screen.getByRole('region', { name: 'Ubicación automática sin revisar' });
  expect(panel).toHaveTextContent('Departamento corroborado automáticamente en la misma fuente: San Salvador');
  expect(panel).toHaveTextContent('otro párrafo de la misma noticia menciona expresamente este departamento');
  expect(panel).toHaveTextContent('Sin revisar');
  expect(panel).toHaveTextContent('no confirma el lugar de una violación');
  const sheet = screen.getByRole('region', { name: 'Ficha estructurada de la noticia' });
  expect(within(sheet).getByText('Departamento').closest('.dossier-field')).toHaveTextContent(PUBLIC_FIELD_PENDING);
  expect(within(sheet).getAllByText(PUBLIC_FIELD_PENDING).length).toBeGreaterThanOrEqual(18);
});
it('discloses a registered but restricted publisher without inventing available news', async () => {
  const archive = structuredClone(fixture) as unknown as NewsArchiveData;
  archive.coverage.sources.push({ id: 'la-prensa-grafica', publisher: 'La Prensa Gráfica', endpoint: 'https://www.laprensagrafica.com/sitemap.xml', status: 'partial', records: 0, first_published_date: null, last_published_date: null, months: [], collection: { method: 'html_article_metadata', phase: 'restricted', access: 'restricted', notice: 'Pendiente de un canal autorizado.', last_attempt_at: null } });
  serve(archive);
  render(<NewsArchive records={[]} onOpenRecord={vi.fn()}/>);
  await screen.findByText('3 noticias seleccionadas');
  expect(screen.getByText(/Acceso automatizado restringido: La Prensa Gráfica/)).toBeInTheDocument();
  await userEvent.click(screen.getByText('Ver cobertura por medio y mes'));
  await userEvent.click(screen.getByText('La Prensa Gráfica · 0 referencias · Acceso restringido'));
  expect(screen.getByText('Pendiente de un canal autorizado.')).toBeInTheDocument();
});
it('loads only public news, communicates review limits and exposes source/month coverage', async () => {
  await ready();
  expect(fetch).toHaveBeenCalledWith('/archive/news.json', expect.objectContaining({ signal: expect.any(AbortSignal) }));
  expect(screen.getByText(/Una selección automática no confirma los hechos ni una violación/i)).toBeInTheDocument();
  expect(screen.getByRole('link', { name: 'Descargar archivo JSON' })).toHaveAttribute('href', '/archive/news.json');
  await userEvent.click(screen.getByText('Ver cobertura por medio y mes'));
  await userEvent.click(screen.getByText('GatoEncerrado · 1 referencias · API recorrida'));
  expect(screen.getByRole('table', { name: 'Cobertura mensual de GatoEncerrado' })).toBeInTheDocument();
});
it.each(['2010-01-01', '2014-06-01'])('shows the configured search start %s independently of the paper date and incomplete coverage', async start => {
  const archive = structuredClone(fixture);
  archive.coverage.start_date = start;
  serve(archive);
  render(<NewsArchive records={[]} onOpenRecord={vi.fn()}/>);
  await screen.findByText('3 noticias seleccionadas');
  expect(screen.getByText(`Periodo de búsqueda: del ${formatDate(start)} al ${formatDate(archive.coverage.end_date)}.`)).toBeInTheDocument();
  expect(screen.getByText(/Este intervalo indica el alcance solicitado/)).toBeInTheDocument();
  expect(screen.getByText('Cobertura en construcción · 3 medios · fuentes abiertas.')).toBeInTheDocument();
  expect(screen.queryByText(/Noticias localizadas desde la incorporación del paper/)).not.toBeInTheDocument();
  await userEvent.click(screen.getByText('Ver cobertura por medio y mes'));
  expect(screen.getByText(/La fecha del paper no determina el inicio del archivo histórico/)).toHaveTextContent(formatDate(archive.paper.publication_date));
});
it('filters by words without accents, source, topic and publication dates', async () => {
  await ready();
  await userEvent.type(screen.getByRole('searchbox', { name: 'Buscar noticias' }), 'persecucion');
  expect(screen.getByText('1 noticia seleccionada')).toBeInTheDocument();
  await userEvent.selectOptions(screen.getByLabelText('Medio'), 'GatoEncerrado');
  await userEvent.selectOptions(screen.getByLabelText('Tema detectado'), 'defensores_y_sociedad_civil');
  fireEvent.change(screen.getByLabelText('Publicación desde'), { target: { value: '2026-08-18' } });
  fireEvent.change(screen.getByLabelText('Publicación hasta'), { target: { value: '2026-08-18' } });
  expect(screen.getByText('1 noticia seleccionada')).toBeInTheDocument();
  fireEvent.change(screen.getByLabelText('Publicación hasta'), { target: { value: '2026-01-01' } });
  expect(screen.getByRole('alert')).toHaveTextContent('fecha inicial');
  expect(screen.getByText('0 noticias seleccionadas')).toBeInTheDocument();
  await userEvent.click(screen.getByRole('button', { name: 'Restablecer filtros de noticias' }));
  expect(screen.getByText('3 noticias seleccionadas')).toBeInTheDocument();
});
it('shows all selected references regardless of keyword hints and separates candidates from publication', async () => {
  const archive = structuredClone(fixture);
  archive.records[1].scope_hint = 'international_context';
  archive.records[2].scope_hint = 'undetermined';
  serve(archive);
  render(<NewsArchive records={[]} onOpenRecord={vi.fn()}/>);
  await screen.findByText('3 noticias seleccionadas');
  expect(screen.getByText('Cobertura en construcción · 3 medios · fuentes abiertas.')).toBeInTheDocument();
  expect(screen.queryByLabelText('Vínculo territorial')).not.toBeInTheDocument();
  expect(screen.getByText(/no representan el total de noticias pertinentes en El Salvador/)).toBeInTheDocument();
  expect(screen.getAllByText('Pertinencia automática · Sin revisar')).toHaveLength(3);
  expect(screen.getAllByText('Pendiente de extracción')).toHaveLength(3);
  expect(screen.queryByText('Campos pendientes')).not.toBeInTheDocument();
  expect(screen.getByRole('button', { name: fixture.records[1].title })).toBeInTheDocument();
  expect(screen.getByRole('button', { name: fixture.records[2].title })).toBeInTheDocument();
});
it('opens bibliographic metadata, distinguishes publicly unavailable fields and closes with restored focus', async () => {
  await ready();
  const trigger = screen.getByRole('button', { name: fixture.records[0].title });
  await userEvent.click(trigger);
  const dialog = screen.getByRole('dialog');
  expect(within(dialog).getByText(/Los campos estructurados todavía no tienen una versión pública disponible/)).toBeInTheDocument();
  const sheet = within(dialog).getByRole('region', { name: 'Ficha estructurada de la noticia' });
  expect(within(sheet).getAllByRole('definition')).toHaveLength(NEWS_DOSSIER_FIELDS.length + 3);
  expect(within(sheet).getAllByText(PUBLIC_FIELD_PENDING).length).toBeGreaterThanOrEqual(NEWS_DOSSIER_FIELDS.length);
  expect(within(sheet).getByText('País').closest('.dossier-field')).toHaveTextContent(PUBLIC_FIELD_PENDING);
  expect(within(sheet).queryByText('Dato no mencionado')).not.toBeInTheDocument();
  const pertinence = within(sheet).getByRole('region', { name: 'Selección por pertinencia' });
  expect(pertinence).toHaveTextContent('Vínculo con El Salvador identificado en la fuente');
  expect(pertinence).toHaveTextContent('Seleccionada para seguimiento de derechos humanos');
  expect(pertinence).not.toHaveTextContent(PUBLIC_FIELD_PENDING);
  expect(within(dialog).queryByText('Extracción o revisión pendiente')).not.toBeInTheDocument();
  expect(within(dialog).getByText(/La selección por pertinencia no indica que estos campos/)).toBeInTheDocument();
  expect(within(dialog).getByRole('link', { name: /Consultar noticia original/ })).toHaveAttribute('href', fixture.records[0].source_url);
  await userEvent.click(within(dialog).getByRole('button', { name: 'Cerrar' }));
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  expect(trigger).toHaveFocus();
});
it('closes the news dialog before asking the parent to open a linked dossier', async () => {
  const base = parseCatalog(catalog).records[0];
  const linked = { ...base, source_url: fixture.records[0].source_url };
  const onOpenRecord = vi.fn(() => { expect(screen.queryByRole('dialog')).not.toBeInTheDocument(); });
  render(<NewsArchive records={[linked]} onOpenRecord={onOpenRecord}/>);
  await screen.findByText('3 noticias seleccionadas');
  await userEvent.click(screen.getByRole('button', { name: fixture.records[0].title }));
  await userEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: linked.title }));
  expect(onOpenRecord).toHaveBeenCalledWith(linked.id);
});
it('shows failed or malformed loads and allows retry without silent empty results', async () => {
  vi.mocked(fetch).mockResolvedValueOnce({ ok: true, json: async () => ({ ...fixture, coverage: { ...fixture.coverage, records: 999 } }) } as Response);
  render(<NewsArchive records={[]} onOpenRecord={vi.fn()}/>);
  expect(await screen.findByRole('alert')).toHaveTextContent('No pudimos cargar');
  expect(screen.queryByText('0 noticias seleccionadas')).not.toBeInTheDocument();
  await userEvent.click(screen.getByRole('button', { name: 'Reintentar archivo' }));
  expect(await screen.findByText('3 noticias seleccionadas')).toBeInTheDocument();
});
it('renders 30 items per page and resets pagination after filtering', async () => {
  const archive = structuredClone(fixture);
  archive.records = Array.from({ length: 31 }, (_, index) => ({ ...fixture.records[0], id: (index + 1).toString(16).padStart(64, '0'), title: `Referencia ${index}`, source_url: `https://www.diariocolatino.com/test-${index}/` }));
  archive.coverage.records = 31;
  archive.policy.selection_gate.selected = 31; archive.policy.selection_gate.candidates = 31;
  archive.coverage.sources = archive.coverage.sources.map(source => ({ ...source, records: source.id === 'co-latino' ? 31 : 0 }));
  archive.coverage.months = archive.coverage.months.map(month => ({ ...month, records: month.month === '2026-10' ? 31 : 0 }));
  serve(archive);
  render(<NewsArchive records={[]} onOpenRecord={vi.fn()}/>);
  await screen.findByText('31 noticias seleccionadas');
  expect(screen.getAllByRole('article')).toHaveLength(30);
  await userEvent.click(screen.getByRole('button', { name: 'Siguiente' }));
  expect(screen.getAllByRole('article')).toHaveLength(1);
  expect(screen.getByText('Página 2 de 2')).toBeInTheDocument();
  expect(screen.getByLabelText('Resultados de noticias')).toHaveFocus();
  await userEvent.type(screen.getByRole('searchbox', { name: 'Buscar noticias' }), 'Referencia 30');
  expect(screen.getByText('Página 1 de 1')).toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'Anterior' })).toBeDisabled();
});
it('has no automated accessibility violations in the archive and metadata dialog', async () => {
  await ready();
  const options = { rules: { region: { enabled: false }, 'color-contrast': { enabled: false } } };
  expect((await axe.run(document.body, options)).violations).toEqual([]);
  await userEvent.click(screen.getByRole('button', { name: fixture.records[0].title }));
  expect((await axe.run(screen.getByRole('dialog'), options)).violations).toEqual([]);
});

it('keeps a full pending dossier for international news without inferring a country from the geographic hint', async () => {
  const archive = structuredClone(fixture);
  archive.records[0].scope_hint = 'international_context';
  archive.records[0].scope_terms = ['guatemala'];
  serve(archive);
  render(<NewsArchive records={[]} onOpenRecord={vi.fn()}/>);
  await screen.findByText('3 noticias seleccionadas');
  await userEvent.click(screen.getByRole('button', { name: fixture.records[0].title }));
  const sheet = screen.getByRole('region', { name: 'Ficha estructurada de la noticia' });
  expect(within(sheet).getAllByRole('definition')).toHaveLength(21);
  expect(within(sheet).getByText('País').closest('.dossier-field')).toHaveTextContent(PUBLIC_FIELD_PENDING);
  expect(within(sheet).queryByText('Guatemala')).not.toBeInTheDocument();
});

it.each(['other', 'not_relevant', 'unclear'])('excludes a later negative or unresolved human scope decision from lists and map: %s', async scope => {
  const dossier = approved();
  dossier.classification = { jurisdiction: scope === 'other' ? 'other' : 'SV', relevance: scope === 'not_relevant' ? 'not_relevant' : scope === 'unclear' ? 'unclear' : 'relevant', record_type: null };
  dossier.fields.department = { value: 'San Salvador', status: 'reported', evidence_refs: ['bibliography'], note: null };
  serve(fixture, { schema_version: 1, dossiers: [dossier] }, { ...EMPTY_LOCATIONS, locations: [automatic()] });
  render(<NewsArchive view="monitor" records={[]} onOpenRecord={vi.fn()}/>);
  await screen.findByText('2 noticias seleccionadas');
  expect(screen.queryByRole('button', { name: fixture.records[0].title })).not.toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'San Salvador: 0 noticias' })).toBeInTheDocument();
});

it.each(['unreviewed', 'wrong_url', 'wrong_publisher', 'wrong_date', 'private_extra'])('never overlays unapproved or unrelated dossier data (%s)', async kind => {
  const dossier = approved();
  dossier.fields.facts = { value: 'PRIVATE_VALUE_MUST_NOT_RENDER', status: 'reported', evidence_refs: ['bibliography'], note: null };
  if (kind === 'unreviewed') dossier.human_reviewed = false;
  if (kind === 'wrong_url') dossier.source_url = 'https://www.diariocolatino.com/another-story/';
  if (kind === 'wrong_publisher') dossier.publisher = 'Otra editorial';
  if (kind === 'wrong_date') dossier.published_date = '2026-10-07';
  if (kind === 'private_extra') Object.assign(dossier, { private_extraction: 'PRIVATE_VALUE_MUST_NOT_RENDER' });
  serve(fixture, { schema_version: 1, dossiers: [dossier] });
  render(<NewsArchive records={[]} onOpenRecord={vi.fn()}/>);
  await screen.findByText('3 noticias seleccionadas');
  await userEvent.click(screen.getByRole('button', { name: fixture.records[0].title }));
  const sheet = screen.getByRole('region', { name: 'Ficha estructurada de la noticia' });
  expect(within(sheet).getByText('Descripción de los hechos').closest('.dossier-field')).toHaveTextContent(PUBLIC_FIELD_PENDING);
  expect(screen.queryByText('PRIVATE_VALUE_MUST_NOT_RENDER')).not.toBeInTheDocument();
});

it('keeps the complete pending sheet when the public overlay is unavailable, and can retry it independently', async () => {
  let available = false;
  vi.mocked(fetch).mockImplementation(async url => ({ ok: String(url) !== '/archive/dossiers.json' || available, json: async () => String(url) === '/archive/dossiers.json' ? EMPTY : String(url) === '/archive/locations.json' ? EMPTY_LOCATIONS : fixture }) as Response);
  render(<NewsArchive records={[]} onOpenRecord={vi.fn()}/>);
  await screen.findByRole('button', { name: 'Reintentar fichas públicas' });
  expect(screen.getAllByText('Ficha pública no disponible')).toHaveLength(3);
  expect(screen.queryByText('Ficha pendiente de revisión pública')).not.toBeInTheDocument();
  await userEvent.click(screen.getByRole('button', { name: fixture.records[0].title }));
  expect(screen.getByRole('region', { name: 'Ficha estructurada de la noticia' })).toBeInTheDocument();
  expect(screen.getAllByText(PUBLIC_FIELD_PENDING).length).toBeGreaterThan(18);
  expect(screen.getByText(/No se pudo comprobar si hay una ficha pública disponible/)).toBeInTheDocument();
  await userEvent.click(screen.getByRole('button', { name: 'Cerrar' }));
  available = true;
  await userEvent.click(screen.getByRole('button', { name: 'Reintentar fichas públicas' }));
  await screen.findByText('3 noticias seleccionadas');
  expect(vi.mocked(fetch).mock.calls.filter(call => call[0] === '/archive/news.json')).toHaveLength(1);
});

it('lists every territorial scope in Fichas de noticias and keeps all pending sheets accessible with an empty map', async () => {
  const archive = structuredClone(fixture);
  archive.records[1].scope_hint = 'international_context';
  archive.records[2].scope_hint = 'undetermined';
  serve(archive);
  render(<NewsArchive view="monitor" records={[]} onOpenRecord={vi.fn()}/>);
  await screen.findByText('3 noticias seleccionadas');
  expect(screen.getByRole('heading', { name: 'Fichas de noticias' })).toBeInTheDocument();
  expect(screen.queryByLabelText('Vínculo territorial')).not.toBeInTheDocument();
  expect(screen.getAllByRole('article')).toHaveLength(3);
  expect(screen.getByText(/Ninguna noticia tiene un departamento disponible/)).toBeInTheDocument();
  expect(screen.getByText('3 sin ubicación automática disponible ni ubicación revisada.')).toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'San Salvador: 0 noticias' })).toBeInTheDocument();
  expect(screen.queryByText('Ver cobertura por medio y mes')).not.toBeInTheDocument();
  await userEvent.click(screen.getByRole('button', { name: /Sin ubicación pública en El Salvador/ }));
  expect(screen.getAllByRole('article')).toHaveLength(3);
  await userEvent.click(screen.getByRole('button', { name: fixture.records[1].title }));
  const sheet = screen.getByRole('region', { name: 'Ficha estructurada de la noticia' });
  expect(within(sheet).getAllByRole('definition')).toHaveLength(21);
  expect(within(sheet).getAllByText(PUBLIC_FIELD_PENDING).length).toBeGreaterThanOrEqual(18);
  expect(within(sheet).queryByText('Dato no mencionado')).not.toBeInTheDocument();
});

it('filters news with reviewed locations independently of the map counts and keeps unlocated news available', async () => {
  const dossier = approved();
  dossier.classification.jurisdiction = 'SV';
  dossier.fields.department = { value: 'San Salvador', status: 'reported', evidence_refs: ['bibliography'], note: null };
  serve(fixture, { schema_version: 1, dossiers: [dossier] });
  render(<NewsArchive view="monitor" records={[]} onOpenRecord={vi.fn()}/>);
  const sanSalvador = await screen.findByRole('button', { name: 'San Salvador: 1 noticia' });
  expect(screen.getAllByRole('article')).toHaveLength(3);
  await userEvent.click(sanSalvador);
  expect(screen.getByText('1 noticia seleccionada')).toBeInTheDocument();
  expect(screen.getByRole('button', { name: fixture.records[0].title })).toBeInTheDocument();
  expect(screen.getByRole('button', { name: /Sin ubicación pública en El Salvador/ })).toHaveTextContent('2');
  await userEvent.click(screen.getByRole('button', { name: /Sin ubicación pública en El Salvador/ }));
  expect(screen.getByText('2 noticias seleccionadas')).toBeInTheDocument();
  expect(screen.queryByRole('button', { name: fixture.records[0].title })).not.toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'San Salvador: 1 noticia' })).toBeInTheDocument();
  await userEvent.click(screen.getByRole('button', { name: 'Quitar filtro del mapa' }));
  expect(screen.getByText('3 noticias seleccionadas')).toBeInTheDocument();
  await userEvent.selectOptions(screen.getByLabelText('Medio'), fixture.records[1].publisher);
  expect(screen.getByText('1 noticia seleccionada')).toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'San Salvador: 0 noticias' })).toBeInTheDocument();
  await userEvent.click(screen.getByRole('button', { name: 'Restablecer filtros de noticias' }));
  expect(screen.queryByLabelText('Vínculo territorial')).not.toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'San Salvador: 1 noticia' })).toBeInTheDocument();
});

it('updates the news map with search, detected topic, scope and publication dates without inferring locations', async () => {
  const dossier = approved();
  dossier.classification.jurisdiction = 'SV';
  dossier.fields.department = { value: 'San Salvador', status: 'reported', evidence_refs: ['bibliography'], note: null };
  const archive = structuredClone(fixture);
  archive.records[0].scope_hint = 'undetermined';
  serve(archive, { schema_version: 1, dossiers: [dossier] });
  render(<NewsArchive view="monitor" records={[]} onOpenRecord={vi.fn()}/>);
  await screen.findByRole('button', { name: 'San Salvador: 1 noticia' });

  await userEvent.type(screen.getByRole('searchbox', { name: 'Buscar noticias' }), 'persecucion');
  expect(screen.getByRole('button', { name: 'San Salvador: 0 noticias' })).toBeInTheDocument();
  await userEvent.clear(screen.getByRole('searchbox', { name: 'Buscar noticias' }));
  await userEvent.selectOptions(screen.getByLabelText('Tema detectado'), 'defensores_y_sociedad_civil');
  expect(screen.getByRole('button', { name: 'San Salvador: 0 noticias' })).toBeInTheDocument();
  await userEvent.selectOptions(screen.getByLabelText('Tema detectado'), '');
  fireEvent.change(screen.getByLabelText('Publicación hasta'), { target: { value: '2026-08-18' } });
  expect(screen.getByRole('button', { name: 'San Salvador: 0 noticias' })).toBeInTheDocument();
  fireEvent.change(screen.getByLabelText('Publicación desde'), { target: { value: '2026-10-01' } });
  expect(screen.getByRole('button', { name: 'San Salvador: 0 noticias' })).toBeDisabled();
  expect(screen.getByText('0 noticias seleccionadas')).toBeInTheDocument();
});

it('retains every news reference and its pending sheet in the monitor when approved dossiers cannot load', async () => {
  vi.mocked(fetch).mockImplementation(async url => ({ ok: String(url) !== '/archive/dossiers.json', json: async () => fixture }) as Response);
  render(<NewsArchive view="monitor" records={[]} onOpenRecord={vi.fn()}/>);
  await screen.findByRole('button', { name: 'Reintentar fichas públicas' });
  expect(screen.getAllByRole('article')).toHaveLength(3);
  expect(screen.getByRole('button', { name: 'San Salvador: ubicaciones pendientes de comprobar' })).toBeDisabled();
  await userEvent.click(screen.getByRole('button', { name: fixture.records[0].title }));
  expect(screen.getByRole('region', { name: 'Ficha estructurada de la noticia' })).toBeInTheDocument();
  expect(screen.getAllByText(PUBLIC_FIELD_PENDING).length).toBeGreaterThanOrEqual(18);
});

it('keeps a valid result page when a late public dossier response changes which news have reviewed locations', async () => {
  const archive = structuredClone(fixture);
  archive.records = Array.from({ length: 31 }, (_, index) => ({ ...fixture.records[0], id: (index + 1).toString(16).padStart(64, '0'), title: `Referencia ${index}`, source_url: `https://www.diariocolatino.com/test-${index}/` }));
  archive.coverage.records = 31;
  archive.policy.selection_gate.selected = 31; archive.policy.selection_gate.candidates = 31;
  archive.coverage.sources = archive.coverage.sources.map(source => ({ ...source, records: source.id === 'co-latino' ? 31 : 0 }));
  archive.coverage.months = archive.coverage.months.map(month => ({ ...month, records: month.month === '2026-10' ? 31 : 0 }));
  const dossiers = archive.records.slice(0, 30).map(reference => {
    const dossier = { ...approved(), candidate_id: reference.id, source_url: reference.source_url, source_title: reference.title };
    dossier.classification.jurisdiction = 'SV';
    dossier.evidence = [{ id: 'bibliography', url: reference.source_url, locator: 'Metadatos bibliográficos públicos de esta fuente.' }];
    dossier.fields.department = { value: 'San Salvador', status: 'reported', evidence_refs: ['bibliography'], note: null };
    return dossier;
  });
  let finishOverlay: (value: unknown) => void = () => {};
  const overlay = new Promise(resolve => { finishOverlay = resolve; });
  vi.mocked(fetch).mockImplementation(async url => ({ ok: true, json: async () => String(url) === '/archive/dossiers.json' ? overlay : String(url) === '/archive/locations.json' ? EMPTY_LOCATIONS : archive }) as Response);
  render(<NewsArchive view="monitor" records={[]} onOpenRecord={vi.fn()}/>);
  await screen.findByText('31 noticias seleccionadas');
  expect(screen.getByRole('button', { name: /Sin ubicación pública en El Salvador/ })).toBeDisabled();
  await userEvent.selectOptions(screen.getByLabelText('Capa de ubicaciones'), 'reviewed');
  await userEvent.click(screen.getByRole('button', { name: 'Siguiente' }));
  expect(screen.getByText('Página 2 de 2')).toBeInTheDocument();
  await act(async () => { finishOverlay({ schema_version: 1, dossiers }); });
  await screen.findByText('30 noticias seleccionadas');
  expect(screen.getByText('Página 1 de 1')).toBeInTheDocument();
  expect(screen.queryByRole('button', { name: 'Referencia 30' })).not.toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'Anterior' })).toBeDisabled();
  expect(screen.getByRole('button', { name: 'Siguiente' })).toBeDisabled();
});

it('has no automated accessibility violations in the news monitor and map', async () => {
  render(<NewsArchive view="monitor" records={[]} onOpenRecord={vi.fn()}/>);
  await screen.findByText('3 noticias seleccionadas');
  expect((await axe.run(document.body, { rules: { region: { enabled: false }, 'color-contrast': { enabled: false } } })).violations).toEqual([]);
});

it('maps automatic locations in a separate layer without publishing draft dossier fields', async () => {
  const dossier = approved();
  dossier.classification.jurisdiction = 'SV';
  dossier.fields.department = { value: 'La Libertad', status: 'reported', evidence_refs: ['bibliography'], note: null };
  const second = automatic(fixture.records[1]);
  serve(fixture, { schema_version: 1, dossiers: [dossier] }, { ...EMPTY_LOCATIONS, locations: [automatic(), second] });
  render(<NewsArchive view="monitor" records={[]} onOpenRecord={vi.fn()}/>);
  await screen.findByRole('button', { name: 'San Salvador: 1 noticia' });
  expect(screen.getByRole('button', { name: 'La Libertad: 1 noticia' })).toBeInTheDocument();
  expect(screen.getByLabelText('Capa de ubicaciones')).toHaveValue('all');
  expect(screen.getAllByRole('article')).toHaveLength(3);
  expect(screen.getByText('Ubicación automática · Sin revisar')).toBeInTheDocument();
  await userEvent.selectOptions(screen.getByLabelText('Capa de ubicaciones'), 'automated_unreviewed');
  expect(screen.getByText('1 noticia seleccionada')).toBeInTheDocument();
  expect(screen.queryByRole('button', { name: fixture.records[0].title })).not.toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'La Libertad: 0 noticias' })).toBeInTheDocument();
  await userEvent.click(screen.getByRole('button', { name: fixture.records[1].title }));
  const dialog = screen.getByRole('dialog');
  const locationPanel = within(dialog).getByRole('region', { name: 'Ubicación automática sin revisar' });
  expect(locationPanel).toHaveTextContent('San Salvador');
  expect(locationPanel).toHaveTextContent('no confirma el lugar de una violación');
  expect(locationPanel).toHaveTextContent('Fuente comprobada');
  expect(within(locationPanel).getByRole('link')).toHaveAttribute('href', second.source_url);
  const sheet = within(dialog).getByRole('region', { name: 'Ficha estructurada de la noticia' });
  expect(within(sheet).getByText('Departamento').closest('.dossier-field')).toHaveTextContent(PUBLIC_FIELD_PENDING);
  expect(within(sheet).getAllByText(PUBLIC_FIELD_PENDING).length).toBeGreaterThanOrEqual(18);
  await userEvent.click(within(dialog).getByRole('button', { name: 'Cerrar' }));
  await userEvent.selectOptions(screen.getByLabelText('Capa de ubicaciones'), 'reviewed');
  expect(screen.getByText('1 noticia seleccionada')).toBeInTheDocument();
  expect(screen.getByRole('button', { name: fixture.records[0].title })).toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'San Salvador: 0 noticias' })).toBeInTheDocument();
  await userEvent.click(screen.getByRole('button', { name: fixture.records[0].title }));
  expect(screen.queryByRole('region', { name: 'Ubicación automática sin revisar' })).not.toBeInTheDocument();
});

it.each(['unavailable', 'private_extra', 'wrong_binding', 'stale_source'])('keeps the list and suppresses misleading zeroes when automatic locations are %s', async kind => {
  const location = automatic();
  if (kind === 'private_extra') Object.assign(location, { private_summary: 'PRIVATE VALUE' });
  if (kind === 'wrong_binding') location.publisher = 'Otra editorial';
  if (kind === 'stale_source') location.evidence.content_sha256 = 'f'.repeat(64);
  let retried = false;
  vi.mocked(fetch).mockImplementation(async url => ({
    ok: String(url) !== '/archive/locations.json' || kind !== 'unavailable' || retried,
    json: async () => String(url) === '/archive/dossiers.json' ? EMPTY : String(url) === '/archive/locations.json' ? retried ? { ...EMPTY_LOCATIONS, locations: [automatic()] } : { ...EMPTY_LOCATIONS, locations: [location] } : fixture,
  }) as Response);
  render(<NewsArchive view="monitor" records={[]} onOpenRecord={vi.fn()}/>);
  await screen.findByRole('button', { name: 'Reintentar ubicaciones' });
  expect(screen.getAllByRole('article')).toHaveLength(3);
  expect(screen.queryByRole('button', { name: 'San Salvador: 0 noticias' })).not.toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'San Salvador: ubicaciones pendientes de comprobar' })).toBeDisabled();
  expect(screen.queryByText('PRIVATE VALUE')).not.toBeInTheDocument();
  retried = true;
  await userEvent.click(screen.getByRole('button', { name: 'Reintentar ubicaciones' }));
  await screen.findByRole('button', { name: 'San Salvador: 1 noticia' });
  expect(screen.getAllByRole('article')).toHaveLength(3);
  expect(vi.mocked(fetch).mock.calls.filter(call => call[0] === '/archive/news.json')).toHaveLength(1);
});

it('does not use automatic data while the human-review overlay is unavailable', async () => {
  vi.mocked(fetch).mockImplementation(async url => ({
    ok: String(url) !== '/archive/dossiers.json',
    json: async () => String(url) === '/archive/locations.json' ? { ...EMPTY_LOCATIONS, locations: [automatic()] } : fixture,
  }) as Response);
  render(<NewsArchive view="monitor" records={[]} onOpenRecord={vi.fn()}/>);
  await screen.findByRole('button', { name: 'Reintentar fichas públicas' });
  expect(screen.getByRole('button', { name: 'San Salvador: ubicaciones pendientes de comprobar' })).toBeDisabled();
  expect(screen.queryByText('Ubicación automática · Sin revisar')).not.toBeInTheDocument();
  await userEvent.click(screen.getByRole('button', { name: fixture.records[0].title }));
  expect(screen.queryByRole('region', { name: 'Ubicación automática sin revisar' })).not.toBeInTheDocument();
});

it('clamps a later page when a delayed automatic layer finishes loading', async () => {
  const archive = structuredClone(fixture);
  archive.records = Array.from({ length: 31 }, (_, index) => ({ ...fixture.records[0], id: (index + 1).toString(16).padStart(64, '0'), title: `Referencia ${index}`, source_url: `https://www.diariocolatino.com/test-${index}/` }));
  archive.coverage.records = 31;
  archive.policy.selection_gate.selected = 31; archive.policy.selection_gate.candidates = 31;
  archive.coverage.sources = archive.coverage.sources.map(source => ({ ...source, records: source.id === 'co-latino' ? 31 : 0 }));
  archive.coverage.months = archive.coverage.months.map(month => ({ ...month, records: month.month === '2026-10' ? 31 : 0 }));
  let finish: (value: unknown) => void = () => {};
  const locations = new Promise(resolve => { finish = resolve; });
  vi.mocked(fetch).mockImplementation(async url => ({ ok: true, json: async () => String(url) === '/archive/dossiers.json' ? EMPTY : String(url) === '/archive/locations.json' ? locations : archive }) as Response);
  render(<NewsArchive view="monitor" records={[]} onOpenRecord={vi.fn()}/>);
  await screen.findByText('31 noticias seleccionadas');
  expect(screen.getByRole('button', { name: 'San Salvador: ubicaciones pendientes de comprobar' })).toBeDisabled();
  await userEvent.selectOptions(screen.getByLabelText('Capa de ubicaciones'), 'automated_unreviewed');
  await userEvent.click(screen.getByRole('button', { name: 'Siguiente' }));
  expect(screen.getByText('Página 2 de 2')).toBeInTheDocument();
  await act(async () => { finish({ ...EMPTY_LOCATIONS, locations: [automatic(archive.records[0])] }); });
  await screen.findByText('1 noticia seleccionada');
  expect(screen.getByText('Página 1 de 1')).toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'Referencia 0' })).toBeInTheDocument();
});

it.each(['missing', 'foreign', 'irrelevant', 'private'])('refuses to render an archive with invalid selection evidence (%s)', async kind => {
  const archive = structuredClone(fixture);
  if (kind === 'missing') Reflect.deleteProperty(archive.records[0], 'selection');
  if (kind === 'foreign') archive.records[0].selection.jurisdiction = 'other';
  if (kind === 'irrelevant') archive.records[0].selection.relevance = 'not_relevant';
  if (kind === 'private') Object.assign(archive.records[0].selection, { private_article: 'PRIVATE_VALUE' });
  serve(archive);
  render(<NewsArchive view="monitor" records={[]} onOpenRecord={vi.fn()}/>);
  await screen.findByRole('alert');
  expect(screen.queryByRole('button', { name: fixture.records[0].title })).not.toBeInTheDocument();
  expect(screen.queryByRole('region', { name: 'Mapa de El Salvador' })).not.toBeInTheDocument();
  expect(screen.queryByText('PRIVATE_VALUE')).not.toBeInTheDocument();
});

it('shows extraction status per selected news item and only once in the opened sheet', async () => {
  serve(fixture, EMPTY, EMPTY_LOCATIONS, processingFixture([
    processingEntry(fixture.records[0], 'generated'), processingEntry(fixture.records[1], 'pending'), processingEntry(fixture.records[2], 'error'),
  ]));
  render(<NewsArchive view="monitor" records={[]} onOpenRecord={vi.fn()}/>);
  await screen.findByText('Ficha generada · Pendiente de validación automática');
  expect(screen.getByText('3 noticias seleccionadas')).toBeInTheDocument();
  expect(screen.queryByText('3 fichas de noticias')).not.toBeInTheDocument();
  expect(screen.getByRole('button', { name: fixture.records[0].title }).closest('article')).toHaveTextContent('Ficha generada · Pendiente de validación automática');
  expect(screen.getByRole('button', { name: fixture.records[1].title }).closest('article')).toHaveTextContent('Pendiente de extracción');
  expect(screen.getByRole('button', { name: fixture.records[2].title }).closest('article')).toHaveTextContent('Extracción con error');
  await userEvent.click(screen.getByRole('button', { name: fixture.records[0].title }));
  const dialog = screen.getByRole('dialog');
  expect(within(dialog).getAllByText('Ficha generada · Pendiente de validación automática')).toHaveLength(1);
  const summary = within(dialog).getByText('Resumen').closest('.dossier-field')! as HTMLElement;
  expect(within(summary).getAllByText(PUBLIC_FIELD_PENDING)).toHaveLength(1);
  expect(dialog).toHaveTextContent('Sus campos se publicarán cuando superen las comprobaciones automáticas');
});

it.each(['missing', 'stale', 'different_source'])('does not infer pending for %s metadata or discard other matching statuses', async kind => {
  const first = processingEntry(fixture.records[0], 'generated');
  if (kind === 'stale') first.selection_content_sha256 = 'f'.repeat(64);
  if (kind === 'different_source') first.source_url = 'https://www.diariocolatino.com/different-source/';
  serve(fixture, EMPTY, EMPTY_LOCATIONS, processingFixture([
    ...(kind === 'missing' ? [] : [first]), processingEntry(fixture.records[1], 'generated'), processingEntry(fixture.records[2], 'source_unavailable'),
  ]));
  await ready();
  expect(screen.getByRole('button', { name: fixture.records[0].title }).closest('article')).toHaveTextContent('Estado de extracción sin confirmar');
  expect(screen.getByRole('button', { name: fixture.records[1].title }).closest('article')).toHaveTextContent('Ficha generada · Pendiente de validación automática');
  expect(screen.getByRole('button', { name: fixture.records[2].title }).closest('article')).toHaveTextContent('Fuente no accesible para extracción');
});

it.each(['http_error', 'malformed', 'private_field'])('keeps data private and reports unavailable processing metadata on %s, with independent retry', async kind => {
  let recovered = false;
  const payload = processingFixture([processingEntry(fixture.records[0], 'generated')]);
  if (kind === 'malformed') payload.entries[0].status = 'invented';
  if (kind === 'private_field') Object.assign(payload.entries[0], { summary: 'PRIVATE_SUMMARY_SENTINEL' });
  vi.mocked(fetch).mockImplementation(async url => ({
    ok: String(url) !== '/archive/processing.json' || kind !== 'http_error' || recovered,
    json: async () => String(url) === '/archive/dossiers.json' ? EMPTY : String(url) === '/archive/locations.json' ? EMPTY_LOCATIONS
      : String(url) === '/archive/processing.json' ? recovered ? processingFixture([processingEntry(fixture.records[0], 'generated')]) : payload : fixture,
  }) as Response);
  await ready();
  await screen.findByRole('button', { name: 'Reintentar estados de extracción' });
  expect(screen.getAllByText('Estado de extracción no disponible')).toHaveLength(3);
  expect(screen.queryByText('Pendiente de extracción')).not.toBeInTheDocument();
  expect(document.body.textContent).not.toContain('PRIVATE_SUMMARY_SENTINEL');
  recovered = true;
  await userEvent.click(screen.getByRole('button', { name: 'Reintentar estados de extracción' }));
  await screen.findByText('Ficha generada · Pendiente de validación automática');
  expect(vi.mocked(fetch).mock.calls.filter(call => call[0] === '/archive/news.json')).toHaveLength(1);
});

it('does not confuse loading metadata with an unprocessed sheet and updates an open dialog', async () => {
  let finish: (value: unknown) => void = () => {};
  const pendingResponse = new Promise(resolve => { finish = resolve; });
  vi.mocked(fetch).mockImplementation(async url => ({ ok: true, json: async () =>
    String(url) === '/archive/dossiers.json' ? EMPTY : String(url) === '/archive/locations.json' ? EMPTY_LOCATIONS
      : String(url) === '/archive/processing.json' ? pendingResponse : fixture,
  }) as Response);
  await ready();
  expect(screen.getAllByText('Comprobando extracción')).toHaveLength(3);
  expect(screen.queryByText('Pendiente de extracción')).not.toBeInTheDocument();
  await userEvent.click(screen.getByRole('button', { name: fixture.records[0].title }));
  expect(within(screen.getByRole('dialog')).getByText('Comprobando extracción')).toBeInTheDocument();
  await act(async () => { finish(processingFixture([processingEntry(fixture.records[0], 'generated')])); });
  expect(within(screen.getByRole('dialog')).getAllByText('Ficha generada · Pendiente de validación automática')).toHaveLength(1);
});

it('retains the approved sheet even when extraction metadata reports an earlier pending state', async () => {
  const dossier = approved();
  dossier.fields.summary = { value: 'Resumen aprobado para publicar.', status: 'reported', evidence_refs: ['bibliography'], note: null };
  serve(fixture, { schema_version: 1, dossiers: [dossier] });
  await ready();
  const record = screen.getByRole('button', { name: fixture.records[0].title });
  expect(record.closest('article')).toHaveTextContent('Ficha revisada editorialmente');
  expect(record.closest('article')).not.toHaveTextContent('Pendiente de extracción');
  await userEvent.click(record);
  expect(within(screen.getByRole('dialog')).getByText('Resumen aprobado para publicar.')).toBeInTheDocument();
});

it('shows automatic publication in the news list and opens its public fields despite stale pending progress', async () => {
  const dossier = automaticNewsDossier(fixture.records[0]);
  serve(fixture, { schema_version: 1, dossiers: [dossier] });
  await ready();
  const record = screen.getByRole('button', { name: fixture.records[0].title });
  expect(record.closest('article')).toHaveTextContent('Ficha publicada automáticamente');
  expect(record.closest('article')).not.toHaveTextContent('Pendiente de extracción');
  await userEvent.click(record);
  const dialog = screen.getByRole('dialog');
  expect(within(dialog).getByText('Resumen ficticio atribuido al medio para pruebas.')).toBeInTheDocument();
  expect(within(dialog).getAllByText('Ficha publicada automáticamente')).toHaveLength(1);
  expect(within(dialog).getByRole('link', { name: 'Reportar un problema' })).toHaveAttribute('href', expect.stringContaining(`candidate_id=${fixture.records[0].id}`));
  expect(dialog).not.toHaveTextContent('Ficha revisada por');
});


it('shows only a validated processing snapshot timestamp, rather than the fetch time', async () => {
  const generatedAt = '2026-10-09T12:30:00Z';
  const payload = { ...processingFixture(), generated_at: generatedAt };
  serve(fixture, EMPTY, EMPTY_LOCATIONS, payload);
  const mounted = render(<NewsArchive records={[]} onOpenRecord={vi.fn()}/>);
  expect(await screen.findByText(`Estado de fichas actualizado: ${archiveInstant(generatedAt)} (El Salvador).`)).toBeInTheDocument();
  mounted.unmount();
  serve(fixture, EMPTY, EMPTY_LOCATIONS, { ...payload, generated_at: 'not-a-date' });
  render(<NewsArchive records={[]} onOpenRecord={vi.fn()}/>);
  await screen.findByRole('button', { name: 'Reintentar estados de extracción' });
  expect(screen.queryByText(/Estado de fichas actualizado:/)).not.toBeInTheDocument();
});
