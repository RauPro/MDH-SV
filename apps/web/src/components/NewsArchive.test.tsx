import { beforeEach, expect, it, vi } from 'vitest';
import { act, fireEvent, render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import axe from 'axe-core';
import fixture from '../__fixtures__/newsArchive.json';
import catalog from '../__fixtures__/catalog.json';
import { formatDate, parseCatalog } from '../catalog';
import { NewsArchive } from './NewsArchive';
import type { NewsArchiveData } from '../newsArchive';
import { NEWS_DOSSIER_FIELDS, NEWS_DOSSIER_PENDING, pendingNewsDossier, type NewsDossier } from '../../../../shared/news-dossier.mjs';

const EMPTY = { schema_version: 1, dossiers: [] };
function serve(archive: unknown = fixture, dossiers: unknown = EMPTY) {
  vi.stubGlobal('fetch', vi.fn(async (url: string) => ({ ok: true, json: async () => url === '/archive/dossiers.json' ? dossiers : archive })));
}
function approved(): NewsDossier {
  const dossier = pendingNewsDossier(fixture.records[0]);
  dossier.extraction_status = 'processed'; dossier.human_reviewed = true; dossier.reviewed_by = 'reviewer'; dossier.reviewed_at = '2026-10-10'; dossier.review_notes = 'Fixture ficticia: revisión editorial explícita de fuente y privacidad.';
  return dossier;
}
beforeEach(() => { vi.restoreAllMocks(); serve(); });
const ready = async () => { render(<NewsArchive records={[]} onOpenRecord={vi.fn()}/>); await screen.findByText('3 noticias localizadas'); };
it('discloses a registered but restricted publisher without inventing available news', async () => {
  const archive = structuredClone(fixture) as unknown as NewsArchiveData;
  archive.coverage.sources.push({ id: 'la-prensa-grafica', publisher: 'La Prensa Gráfica', endpoint: 'https://www.laprensagrafica.com/sitemap.xml', status: 'partial', records: 0, first_published_date: null, last_published_date: null, months: [], collection: { method: 'html_article_metadata', phase: 'restricted', access: 'restricted', notice: 'Pendiente de un canal autorizado.', last_attempt_at: null } });
  serve(archive);
  render(<NewsArchive records={[]} onOpenRecord={vi.fn()}/>);
  await screen.findByText('3 noticias localizadas');
  expect(screen.getByText(/Acceso automatizado restringido: La Prensa Gráfica/)).toBeInTheDocument();
  await userEvent.click(screen.getByText('Ver cobertura por medio y mes'));
  await userEvent.click(screen.getByText('La Prensa Gráfica · 0 referencias · Acceso restringido'));
  expect(screen.getByText('Pendiente de un canal autorizado.')).toBeInTheDocument();
});
it('loads only public news, communicates review limits and exposes source/month coverage', async () => {
  await ready();
  expect(fetch).toHaveBeenCalledWith('/archive/news.json', expect.objectContaining({ signal: expect.any(AbortSignal) }));
  expect(screen.getByText(/Las etiquetas automáticas no confirman pertinencia, jurisdicción ni hechos/i)).toBeInTheDocument();
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
  await screen.findByText('3 noticias localizadas');
  expect(screen.getByText(`Periodo de búsqueda: del ${formatDate(start)} al ${formatDate(archive.coverage.end_date)}.`)).toBeInTheDocument();
  expect(screen.getByText(/Este intervalo indica el alcance solicitado/)).toBeInTheDocument();
  expect(screen.getByText('3 referencias · 3 medios · cobertura parcial.')).toBeInTheDocument();
  expect(screen.queryByText(/Noticias localizadas desde la incorporación del paper/)).not.toBeInTheDocument();
  await userEvent.click(screen.getByText('Ver cobertura por medio y mes'));
  expect(screen.getByText(/La fecha del paper no determina el inicio del archivo histórico/)).toHaveTextContent(formatDate(archive.paper.publication_date));
});
it('filters by words without accents, source, topic and publication dates', async () => {
  await ready();
  await userEvent.type(screen.getByRole('searchbox', { name: 'Buscar noticias' }), 'persecucion');
  expect(screen.getByText('1 noticia localizada')).toBeInTheDocument();
  await userEvent.selectOptions(screen.getByLabelText('Medio'), 'GatoEncerrado');
  await userEvent.selectOptions(screen.getByLabelText('Tema detectado'), 'defensores_y_sociedad_civil');
  fireEvent.change(screen.getByLabelText('Publicación desde'), { target: { value: '2026-08-18' } });
  fireEvent.change(screen.getByLabelText('Publicación hasta'), { target: { value: '2026-08-18' } });
  expect(screen.getByText('1 noticia localizada')).toBeInTheDocument();
  fireEvent.change(screen.getByLabelText('Publicación hasta'), { target: { value: '2026-01-01' } });
  expect(screen.getByRole('alert')).toHaveTextContent('fecha inicial');
  expect(screen.getByText('0 noticias localizadas')).toBeInTheDocument();
  await userEvent.click(screen.getByRole('button', { name: 'Restablecer filtros de noticias' }));
  expect(screen.getByText('3 noticias localizadas')).toBeInTheDocument();
});
it('starts with references to El Salvador and lets readers inspect other territorial hints or the entire archive', async () => {
  const archive = structuredClone(fixture);
  archive.records[1].scope_hint = 'international_context';
  archive.records[2].scope_hint = 'undetermined';
  serve(archive);
  render(<NewsArchive records={[]} onOpenRecord={vi.fn()}/>);
  await screen.findByText('1 noticia localizada');
  expect(screen.getByText('3 referencias · 3 medios · cobertura parcial.')).toBeInTheDocument();
  expect(screen.getByLabelText('Vínculo territorial')).toHaveValue('el_salvador');
  expect(screen.getByText(/No confirma jurisdicción. Por defecto/)).toBeInTheDocument();
  await userEvent.selectOptions(screen.getByLabelText('Vínculo territorial'), 'international_context');
  expect(screen.getByRole('button', { name: fixture.records[1].title })).toBeInTheDocument();
  expect(screen.queryByRole('button', { name: fixture.records[0].title })).not.toBeInTheDocument();
  await userEvent.selectOptions(screen.getByLabelText('Vínculo territorial'), 'undetermined');
  expect(screen.getByRole('button', { name: fixture.records[2].title })).toBeInTheDocument();
  await userEvent.selectOptions(screen.getByLabelText('Vínculo territorial'), '');
  expect(screen.getByText('3 noticias localizadas')).toBeInTheDocument();
});
it('opens bibliographic metadata, distinguishes unextracted fields and closes with restored focus', async () => {
  await ready();
  const trigger = screen.getByRole('button', { name: fixture.records[0].title });
  await userEvent.click(trigger);
  const dialog = screen.getByRole('dialog');
  expect(within(dialog).getByText(/No hay una ficha pública revisada asociada/)).toBeInTheDocument();
  const sheet = within(dialog).getByRole('region', { name: 'Ficha estructurada de la noticia' });
  expect(within(sheet).getAllByRole('definition')).toHaveLength(NEWS_DOSSIER_FIELDS.length + 3);
  expect(within(sheet).getAllByText(NEWS_DOSSIER_PENDING).length).toBeGreaterThanOrEqual(NEWS_DOSSIER_FIELDS.length);
  expect(within(sheet).getByText('País').closest('.dossier-field')).toHaveTextContent(NEWS_DOSSIER_PENDING);
  expect(within(sheet).queryByText('Dato no mencionado en el texto analizado')).not.toBeInTheDocument();
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
  await screen.findByText('3 noticias localizadas');
  await userEvent.click(screen.getByRole('button', { name: fixture.records[0].title }));
  await userEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: linked.title }));
  expect(onOpenRecord).toHaveBeenCalledWith(linked.id);
});
it('shows failed or malformed loads and allows retry without silent empty results', async () => {
  vi.mocked(fetch).mockResolvedValueOnce({ ok: true, json: async () => ({ ...fixture, coverage: { ...fixture.coverage, records: 999 } }) } as Response);
  render(<NewsArchive records={[]} onOpenRecord={vi.fn()}/>);
  expect(await screen.findByRole('alert')).toHaveTextContent('No pudimos cargar');
  expect(screen.queryByText('0 noticias localizadas')).not.toBeInTheDocument();
  await userEvent.click(screen.getByRole('button', { name: 'Reintentar archivo' }));
  expect(await screen.findByText('3 noticias localizadas')).toBeInTheDocument();
});
it('renders 30 items per page and resets pagination after filtering', async () => {
  const archive = structuredClone(fixture);
  archive.records = Array.from({ length: 31 }, (_, index) => ({ ...fixture.records[0], id: (index + 1).toString(16).padStart(64, '0'), title: `Referencia ${index}`, source_url: `https://www.diariocolatino.com/test-${index}/` }));
  archive.coverage.records = 31;
  archive.coverage.sources = archive.coverage.sources.map(source => ({ ...source, records: source.id === 'co-latino' ? 31 : 0 }));
  archive.coverage.months = archive.coverage.months.map(month => ({ ...month, records: month.month === '2026-10' ? 31 : 0 }));
  serve(archive);
  render(<NewsArchive records={[]} onOpenRecord={vi.fn()}/>);
  await screen.findByText('31 noticias localizadas');
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
  await screen.findByLabelText('Vínculo territorial');
  await userEvent.selectOptions(screen.getByLabelText('Vínculo territorial'), 'international_context');
  await userEvent.click(screen.getByRole('button', { name: fixture.records[0].title }));
  const sheet = screen.getByRole('region', { name: 'Ficha estructurada de la noticia' });
  expect(within(sheet).getAllByRole('definition')).toHaveLength(21);
  expect(within(sheet).getByText('País').closest('.dossier-field')).toHaveTextContent(NEWS_DOSSIER_PENDING);
  expect(within(sheet).queryByText('Guatemala')).not.toBeInTheDocument();
});

it('shows an approved but out-of-scope partial dossier and labels editorial review separately from raw bibliography', async () => {
  const dossier = approved();
  dossier.source_title = 'Titular ficticio redactado por privacidad';
  dossier.classification = { jurisdiction: 'other', relevance: 'not_relevant', record_type: 'publication' };
  dossier.fields.country = { value: 'Guatemala', status: 'reported', evidence_refs: ['bibliography'], note: 'Dato ficticio para probar la interfaz.' };
  dossier.fields.summary = { value: 'Resumen ficticio aprobado para esta prueba.', status: 'reported', evidence_refs: ['bibliography'], note: null };
  dossier.fields.event_date = { value: null, status: 'not_reported', evidence_refs: [], note: null };
  serve(fixture, { schema_version: 1, dossiers: [dossier] });
  render(<NewsArchive records={[]} onOpenRecord={vi.fn()}/>);
  await screen.findByText('Ficha revisada editorialmente');
  await userEvent.click(screen.getByRole('button', { name: fixture.records[0].title }));
  const dialog = screen.getByRole('dialog', { name: dossier.source_title });
  expect(within(dialog).getByText('Ficha revisada editorialmente')).toBeInTheDocument();
  expect(within(dialog).getByText('Guatemala')).toBeInTheDocument();
  expect(within(dialog).getByText('Fuera del ámbito de seguimiento')).toBeInTheDocument();
  expect(within(dialog).getByText(dossier.fields.summary.value as string)).toBeInTheDocument();
  expect(within(dialog).getByText('Municipio').closest('.dossier-field')).toHaveTextContent(NEWS_DOSSIER_PENDING);
  expect(within(dialog).getByText('Fecha del hecho').closest('.dossier-field')).toHaveTextContent('Dato no mencionado en el texto analizado');
  expect(fixture.records[0].review_status).toBe('not_reviewed');
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
  await screen.findByText('3 noticias localizadas');
  await userEvent.click(screen.getByRole('button', { name: fixture.records[0].title }));
  const sheet = screen.getByRole('region', { name: 'Ficha estructurada de la noticia' });
  expect(within(sheet).getByText('Descripción de los hechos').closest('.dossier-field')).toHaveTextContent(NEWS_DOSSIER_PENDING);
  expect(screen.queryByText('PRIVATE_VALUE_MUST_NOT_RENDER')).not.toBeInTheDocument();
});

it('keeps the complete pending sheet when the public overlay is unavailable, and can retry it independently', async () => {
  let available = false;
  vi.mocked(fetch).mockImplementation(async url => ({ ok: String(url) !== '/archive/dossiers.json' || available, json: async () => String(url) === '/archive/dossiers.json' ? EMPTY : fixture }) as Response);
  render(<NewsArchive records={[]} onOpenRecord={vi.fn()}/>);
  await screen.findByRole('button', { name: 'Reintentar fichas públicas' });
  await userEvent.click(screen.getByRole('button', { name: fixture.records[0].title }));
  expect(screen.getByRole('region', { name: 'Ficha estructurada de la noticia' })).toBeInTheDocument();
  expect(screen.getAllByText(NEWS_DOSSIER_PENDING).length).toBeGreaterThan(18);
  await userEvent.click(screen.getByRole('button', { name: 'Cerrar' }));
  available = true;
  await userEvent.click(screen.getByRole('button', { name: 'Reintentar fichas públicas' }));
  await screen.findByText('3 noticias localizadas');
  expect(vi.mocked(fetch).mock.calls.filter(call => call[0] === '/archive/news.json')).toHaveLength(1);
});

it('lists every territorial scope in Fichas de noticias and keeps all pending sheets accessible with an empty map', async () => {
  const archive = structuredClone(fixture);
  archive.records[1].scope_hint = 'international_context';
  archive.records[2].scope_hint = 'undetermined';
  serve(archive);
  render(<NewsArchive view="monitor" records={[]} onOpenRecord={vi.fn()}/>);
  await screen.findByText('3 fichas de noticias');
  expect(screen.getByRole('heading', { name: 'Fichas de noticias' })).toBeInTheDocument();
  expect(screen.getByLabelText('Vínculo territorial')).toHaveValue('');
  expect(screen.getAllByRole('article')).toHaveLength(3);
  expect(screen.getByText(/Ninguna tiene un departamento de El Salvador respaldado/)).toBeInTheDocument();
  expect(screen.getByText('3 pendientes de revisión pública.')).toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'San Salvador: 0 noticias' })).toBeInTheDocument();
  expect(screen.queryByText('Ver cobertura por medio y mes')).not.toBeInTheDocument();
  await userEvent.click(screen.getByRole('button', { name: /Sin ubicación pública en El Salvador/ }));
  expect(screen.getAllByRole('article')).toHaveLength(3);
  await userEvent.click(screen.getByRole('button', { name: fixture.records[1].title }));
  const sheet = screen.getByRole('region', { name: 'Ficha estructurada de la noticia' });
  expect(within(sheet).getAllByRole('definition')).toHaveLength(21);
  expect(within(sheet).getAllByText(NEWS_DOSSIER_PENDING).length).toBeGreaterThanOrEqual(18);
  expect(within(sheet).queryByText('Dato no mencionado en el texto analizado')).not.toBeInTheDocument();
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
  expect(screen.getByText('1 ficha de noticia')).toBeInTheDocument();
  expect(screen.getByRole('button', { name: fixture.records[0].title })).toBeInTheDocument();
  expect(screen.getByRole('button', { name: /Sin ubicación pública en El Salvador/ })).toHaveTextContent('2');
  await userEvent.click(screen.getByRole('button', { name: /Sin ubicación pública en El Salvador/ }));
  expect(screen.getByText('2 fichas de noticias')).toBeInTheDocument();
  expect(screen.queryByRole('button', { name: fixture.records[0].title })).not.toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'San Salvador: 1 noticia' })).toBeInTheDocument();
  await userEvent.click(screen.getByRole('button', { name: 'Quitar filtro del mapa' }));
  expect(screen.getByText('3 fichas de noticias')).toBeInTheDocument();
  await userEvent.selectOptions(screen.getByLabelText('Medio'), fixture.records[1].publisher);
  expect(screen.getByText('1 ficha de noticia')).toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'San Salvador: 0 noticias' })).toBeInTheDocument();
  await userEvent.click(screen.getByRole('button', { name: 'Restablecer filtros de noticias' }));
  expect(screen.getByLabelText('Vínculo territorial')).toHaveValue('');
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
  await userEvent.selectOptions(screen.getByLabelText('Vínculo territorial'), 'el_salvador');
  expect(screen.getByRole('button', { name: 'San Salvador: 0 noticias' })).toBeInTheDocument();
  await userEvent.selectOptions(screen.getByLabelText('Vínculo territorial'), '');
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
  expect(screen.getByText('0 fichas de noticias')).toBeInTheDocument();
});

it('retains every news reference and its pending sheet in the monitor when approved dossiers cannot load', async () => {
  vi.mocked(fetch).mockImplementation(async url => ({ ok: String(url) !== '/archive/dossiers.json', json: async () => fixture }) as Response);
  render(<NewsArchive view="monitor" records={[]} onOpenRecord={vi.fn()}/>);
  await screen.findByRole('button', { name: 'Reintentar fichas públicas' });
  expect(screen.getAllByRole('article')).toHaveLength(3);
  expect(screen.getByRole('button', { name: 'San Salvador: 0 noticias' })).toBeInTheDocument();
  await userEvent.click(screen.getByRole('button', { name: fixture.records[0].title }));
  expect(screen.getByRole('region', { name: 'Ficha estructurada de la noticia' })).toBeInTheDocument();
  expect(screen.getAllByText(NEWS_DOSSIER_PENDING).length).toBeGreaterThanOrEqual(18);
});

it('keeps a valid result page when a late public dossier response changes which news are unlocated', async () => {
  const archive = structuredClone(fixture);
  archive.records = Array.from({ length: 31 }, (_, index) => ({ ...fixture.records[0], id: (index + 1).toString(16).padStart(64, '0'), title: `Referencia ${index}`, source_url: `https://www.diariocolatino.com/test-${index}/` }));
  archive.coverage.records = 31;
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
  vi.mocked(fetch).mockImplementation(async url => ({ ok: true, json: async () => String(url) === '/archive/dossiers.json' ? overlay : archive }) as Response);
  render(<NewsArchive view="monitor" records={[]} onOpenRecord={vi.fn()}/>);
  await screen.findByText('31 fichas de noticias');
  await userEvent.click(screen.getByRole('button', { name: /Sin ubicación pública en El Salvador/ }));
  await userEvent.click(screen.getByRole('button', { name: 'Siguiente' }));
  expect(screen.getByText('Página 2 de 2')).toBeInTheDocument();
  await act(async () => { finishOverlay({ schema_version: 1, dossiers }); });
  await screen.findByText('1 ficha de noticia');
  expect(screen.getByText('Página 1 de 1')).toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'Referencia 30' })).toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'Anterior' })).toBeDisabled();
  expect(screen.getByRole('button', { name: 'Siguiente' })).toBeDisabled();
});

it('has no automated accessibility violations in the news monitor and map', async () => {
  render(<NewsArchive view="monitor" records={[]} onOpenRecord={vi.fn()}/>);
  await screen.findByText('3 fichas de noticias');
  expect((await axe.run(document.body, { rules: { region: { enabled: false }, 'color-contrast': { enabled: false } } })).violations).toEqual([]);
});
