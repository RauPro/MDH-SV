import { beforeEach, expect, it, vi } from 'vitest';
import { render, screen, within, fireEvent } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import axe from 'axe-core';
import data from './__fixtures__/catalog.json';
import archive from './__fixtures__/newsArchive.json';
import App from './App';

beforeEach(() => { vi.restoreAllMocks(); localStorage.clear(); vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true, json: async () => data })); });
async function ready() { render(<App/>); await screen.findByText('4 resultados'); }
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
  vi.mocked(fetch).mockRejectedValueOnce(new Error('offline'));
  render(<App/>);
  await screen.findByRole('alert');
  await userEvent.click(screen.getByRole('button',{name:'Reintentar'}));
  await screen.findByText('4 resultados');
});
it('fails closed for an invalid source URL', async () => {
  vi.mocked(fetch).mockResolvedValueOnce({ok:true,json:async()=>({...data,records:[{...data.records[0],source_url:'javascript:alert(1)'}]})} as Response);
  render(<App/>);
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
  expect(within(dialog).getByRole('link',{name:'Especificación OpenAPI'})).toHaveAttribute('href','/api/v1/openapi.json');
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

it('opens the linked news archive and returns to the map without losing the catalogue', async () => {
  vi.mocked(fetch).mockImplementation(async input => ({ok:true,json:async()=>String(input).includes('/archive/') ? archive : data}) as Response);
  history.replaceState(null,'','/#archivo');
  render(<App/>);
  await screen.findByRole('region',{name:'Archivo de noticias'});
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  expect(screen.getByLabelText('Vínculo territorial')).toHaveValue('el_salvador');
  await userEvent.click(screen.getByRole('button',{name:'Fichas y mapa'}));
  await screen.findByRole('region',{name:'Mapa de El Salvador'});
  expect(screen.getByText('4 resultados')).toBeInTheDocument();
});
