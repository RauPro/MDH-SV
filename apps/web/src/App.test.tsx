import { beforeEach, expect, it, vi } from 'vitest';
import { render, screen, within, fireEvent } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import axe from 'axe-core';
import data from './__fixtures__/catalog.json';
import archive from './__fixtures__/newsArchive.json';
import App from './App';

const publicResponse = (input: unknown, catalog = data) => ({ ok: true, json: async () =>
  String(input).endsWith('/archive/dossiers.json') ? { schema_version: 1, dossiers: [] }
    : String(input).endsWith('/archive/news.json') ? archive : catalog }) as Response;
beforeEach(() => { vi.restoreAllMocks(); localStorage.clear(); vi.stubGlobal('fetch', vi.fn().mockImplementation(async input => publicResponse(input))); });
async function ready() {
  render(<App/>);
  await userEvent.click(screen.getByRole('button', { name: /^Reportes documentados/ }));
  await screen.findByText('4 resultados');
}
it('filters departments and accent-insensitive text', async () => {
  await ready();
  await userEvent.selectOptions(screen.getByLabelText('Departamento'), 'Sonsonate');
  expect(screen.getByText('2 resultados')).toBeInTheDocument();
  await userEvent.click(screen.getByRole('button', { name:'Restablecer filtros' }));
  await userEvent.type(screen.getByRole('searchbox'), 'ahuachapan');
  expect(screen.getByText('1 resultado')).toBeInTheDocument();
});
it('separates individual, collective and context records', async () => {
  await ready();
  await userEvent.selectOptions(screen.getByLabelText('Tipo de registro'), 'aggregate');
  expect(screen.getByText('1 resultado')).toBeInTheDocument();
  expect(screen.getByRole('button', {name:'Reporte colectivo de vigilancia digital con Pegasus'})).toBeInTheDocument();
  await userEvent.selectOptions(screen.getByLabelText('Tipo de registro'), 'publication');
  expect(screen.getByText('4 resultados')).toBeInTheDocument();
});
it('validates date ranges and excludes unknown dates', async () => {
  await ready();
  fireEvent.change(screen.getByLabelText('Hecho desde'), {target:{value:'2022-01-01'}});
  expect(screen.getByText('3 resultados')).toBeInTheDocument();
  fireEvent.change(screen.getByLabelText('Hecho hasta'), {target:{value:'2021-01-01'}});
  expect(screen.getByRole('alert')).toHaveTextContent('fecha inicial');
  expect(screen.getByText('0 resultados')).toBeInTheDocument();
});
it('opens a source-linked dossier and closes it', async () => {
  await ready();
  await userEvent.click(screen.getByRole('button', {name:'Reporte de detención de una adolescente en Sonsonate'}));
  const dialog = await screen.findByRole('dialog');
  expect(dialog).toHaveTextContent(/revisión humana pendiente/i);
  expect(within(dialog).getByRole('link', {name:/Consultar documento original/})).toHaveAttribute('href', data.records.find(r => r.id === 'mdh-2022-001')?.source_url);
  await userEvent.click(within(dialog).getByRole('button',{name:'Cerrar'}));
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
});
it('handles a failed fetch and retries', async () => {
  let failed = false;
  vi.mocked(fetch).mockImplementation(async input => {
    if (String(input) === '/data/catalog.json' && !failed) { failed = true; throw new Error('offline'); }
    return publicResponse(input);
  });
  render(<App/>);
  await userEvent.click(screen.getByRole('button', { name: /^Reportes documentados/ }));
  await screen.findByRole('alert');
  await userEvent.click(screen.getByRole('button',{name:'Reintentar'}));
  await screen.findByText('4 resultados');
});
it('fails closed for an invalid source URL', async () => {
  vi.mocked(fetch).mockImplementation(async input => publicResponse(input, {...data,records:[{...data.records[0],source_url:'javascript:alert(1)'}]}));
  render(<App/>);
  await userEvent.click(screen.getByRole('button', { name: /^Reportes documentados/ }));
  await screen.findByRole('alert');
  expect(screen.queryByText('4 resultados')).not.toBeInTheDocument();
});
it('works when local storage is denied', async () => {
  vi.spyOn(Storage.prototype,'getItem').mockImplementation(()=>{throw new Error('denied');});
  vi.spyOn(Storage.prototype,'setItem').mockImplementation(()=>{throw new Error('denied');});
  await ready();
  await userEvent.click(screen.getByRole('button',{name:'Activar tema oscuro'}));
  expect(document.documentElement).toHaveClass('dark');
});
it('offers same-origin downloads and describes MCP accurately', async () => {
  await ready();
  await userEvent.click(screen.getByRole('button',{name:'Datos y API'}));
  const dialog = await screen.findByRole('dialog');
  expect(within(dialog).getByRole('link',{name:'CSV'})).toHaveAttribute('href','/data/records.csv');
  expect(dialog).toHaveTextContent('MCP remoto');
  expect(dialog).toHaveTextContent('/api/v1/news?limit=20');
  expect(dialog).not.toHaveTextContent('date_from=2025-03-26');
  expect(within(dialog).getByRole('link',{name:'Especificación OpenAPI'})).toHaveAttribute('href','/api/v1/openapi.json');
});
it('describes 2010 as the historical objective without equating it to the IEEE paper or completed coverage', async () => {
  await ready();
  await userEvent.click(screen.getByRole('button', { name: 'Metodología' }));
  const dialog = await screen.findByRole('dialog');
  expect(dialog).toHaveTextContent('objetivo recuperar noticias desde el 1 de enero de 2010');
  expect(dialog).toHaveTextContent('la cobertura es parcial');
  expect(dialog).toHaveTextContent('no el inicio del archivo');
  expect(within(dialog).getByRole('link', { name: 'Artículo de investigación original' })).toHaveAttribute('href', 'https://ieeexplore.ieee.org/document/10933836');
});
it('has no detectable structural accessibility violations', async () => {
  await ready();
  const result = await axe.run(document.body, { rules: { 'color-contrast': { enabled:false } } });
  expect(result.violations.map(v => v.id)).toEqual([]);
});
it('opens a directly linked record', async () => {
  history.replaceState(null,'','/#mdh-2022-001');
  render(<App/>);
  await screen.findByRole('dialog');
  expect(screen.getByRole('dialog')).toHaveTextContent('Sonsonate');
});

it('shows supporting updates and authentic editorial review separately', async () => {
  const revised=structuredClone(data);
  revised.records[0]={...revised.records[0],human_reviewed:true,review_status:'editorially_reviewed',reviewed_by:'Editor',reviewed_at:'2026-10-09',review_notes:'Revisión de la fuente, fechas y privacidad completada.',related_sources:[{url:'https://www.amnesty.org/es/latest/news/2025/12/example/',title:'Actualización del caso',published_date:'2025-12-17',locator:'Comunicado de actualización'}]} as unknown as typeof revised.records[0];
  vi.mocked(fetch).mockResolvedValue({ok:true,json:async()=>revised} as Response);
  history.replaceState(null,'',`/#${revised.records[0].id}`);
  render(<App/>);
  const dialog=await screen.findByRole('dialog');
  expect(dialog).toHaveTextContent('Revisión editorial por Editor');
  expect(within(dialog).getByRole('link',{name:/Actualización del caso/})).toBeInTheDocument();
});

it('restores the map and synchronizes native department buttons with the filters', async () => {
  await ready();
  const map = screen.getByRole('region',{name:'Mapa de El Salvador'});
  const sonsonate = within(map).getByRole('button',{name:'Sonsonate: 2 reportes individuales'});
  sonsonate.focus();
  await userEvent.keyboard('{Enter}');
  expect(screen.getByLabelText('Departamento')).toHaveValue('Sonsonate');
  expect(screen.getByText('2 resultados')).toBeInTheDocument();
  expect(sonsonate).toHaveAttribute('aria-pressed','true');
  await userEvent.click(within(map).getByRole('button',{name:'Quitar filtro del mapa'}));
  expect(screen.getByText('4 resultados')).toBeInTheDocument();
});

it('filters from the SVG and preserves the other search constraints', async () => {
  await ready();
  await userEvent.type(screen.getByRole('searchbox'), 'detencion');
  fireEvent.click(screen.getByTestId('map-region-SVSO'));
  expect(screen.getByText('1 resultado')).toBeInTheDocument();
  expect(screen.getByRole('button',{name:'Sonsonate: 1 reporte individual'})).toHaveAttribute('aria-pressed','true');
  await userEvent.click(screen.getByRole('button',{name:'La Unión: 0 reportes individuales'}));
  expect(screen.getByText('0 resultados')).toBeInTheDocument();
  expect(screen.getByText(/La ausencia de registros en este conjunto/)).toBeInTheDocument();
});

it('keeps unlocated individual reports outside the map and makes them consultable', async () => {
  const revised=structuredClone(data);
  const incident=revised.records.find(r=>r.record_type==='incident')!;
  incident.department=null;
  incident.dossier.location.department={value:null,status:'not_reported',evidence_refs:[],note:'Departamento no consignado.'};
  vi.mocked(fetch).mockResolvedValue({ok:true,json:async()=>revised} as Response);
  await ready();
  await userEvent.click(screen.getByRole('button',{name:'Sin departamento identificado 1'}));
  expect(screen.getByText('1 resultado')).toBeInTheDocument();
  expect(screen.getByLabelText('Departamento')).toHaveValue('__unknown__');
});

it('opens the linked archive and returns to news in the main map while retaining access to reports', async () => {
  history.replaceState(null,'','/#archivo');
  render(<App/>);
  await screen.findByRole('region',{name:'Archivo de noticias'});
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  expect(screen.queryByLabelText('Vínculo territorial')).not.toBeInTheDocument();
  expect(screen.getByText('3 noticias seleccionadas')).toBeInTheDocument();
  await userEvent.click(screen.getByRole('button',{name:'Fichas y mapa'}));
  await screen.findByRole('region',{name:'Mapa de El Salvador'});
  expect(screen.getByRole('button', {name:'Noticias'})).toHaveAttribute('aria-pressed','true');
  expect(screen.queryByLabelText('Vínculo territorial')).not.toBeInTheDocument();
  expect(screen.getByText('3 fichas de noticias')).toBeInTheDocument();
  await userEvent.click(screen.getByRole('button', { name: /^Reportes documentados/ }));
  expect(screen.getByText('4 resultados')).toBeInTheDocument();
});

it('opens news and their full dossier directly from Fichas y mapa on the home page', async () => {
  render(<App/>);
  await screen.findByRole('region', { name:'Fichas de noticias' });
  expect(screen.getByRole('button', { name:'Fichas y mapa' })).toHaveAttribute('aria-pressed','true');
  expect(screen.getByText('3 fichas de noticias')).toBeInTheDocument();
  expect(screen.getByRole('region', { name:'Mapa de El Salvador' })).toBeInTheDocument();
  await userEvent.click(screen.getByRole('button', { name:archive.records[0].title }));
  const dialog = await screen.findByRole('dialog');
  expect(dialog).toHaveTextContent('Ficha estructurada de la noticia');
  expect(dialog).toHaveTextContent('Víctima o personas afectadas');
  expect(within(dialog).getAllByText('Sin dato revisado disponible').length).toBeGreaterThanOrEqual(18);
  await userEvent.click(within(dialog).getByRole('button', { name:'Cerrar' }));
  expect(screen.getByRole('button', { name:'Noticias' })).toHaveAttribute('aria-pressed','true');
});

it('keeps news visible when the independent reports catalogue fails', async () => {
  vi.mocked(fetch).mockImplementation(async input => {
    if (String(input) === '/data/catalog.json') throw new Error('catalogue offline');
    return publicResponse(input);
  });
  render(<App/>);
  await screen.findByText('3 fichas de noticias');
  expect(screen.queryByRole('alert')).not.toBeInTheDocument();
  await userEvent.click(screen.getByRole('button', { name: /^Reportes documentados/ }));
  expect(await screen.findByRole('alert')).toHaveTextContent('No pudimos cargar los datos');
  await userEvent.click(screen.getByRole('button', { name:'Noticias' }));
  await screen.findByText('3 fichas de noticias');
});

it('preserves report routes when closing a directly linked dossier', async () => {
  history.replaceState(null,'','/#mdh-2022-001');
  render(<App/>);
  const dialog = await screen.findByRole('dialog');
  expect(screen.getByRole('button', { name: /^Reportes documentados/ })).toHaveAttribute('aria-pressed','true');
  await userEvent.click(within(dialog).getByRole('button',{name:'Cerrar'}));
  expect(location.hash).toBe('#reportes');
  expect(screen.getByText('4 resultados')).toBeInTheDocument();
});

it('restores the report collection from its URL without opening a missing-record dialog', async () => {
  history.replaceState(null,'','/#reportes');
  render(<App/>);
  await screen.findByText('4 resultados');
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  expect(screen.getByRole('button', { name: /^Reportes documentados/ })).toHaveAttribute('aria-pressed','true');
});

it('has no detectable structural accessibility violations on the news landing view', async () => {
  render(<App/>);
  await screen.findByText('3 fichas de noticias');
  const result = await axe.run(document.body, { rules: { 'color-contrast': { enabled:false } } });
  expect(result.violations.map(v => v.id)).toEqual([]);
});
