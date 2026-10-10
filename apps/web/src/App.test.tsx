import { beforeEach, expect, it, vi } from 'vitest';
import { render, screen, within, fireEvent } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import axe from 'axe-core';
import data from '../public/data/catalog.json';
import App from './App';

beforeEach(() => { vi.restoreAllMocks(); vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true, json: async () => data })); });
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
  expect(within(dialog).getByText(/revisión humana pendiente/i)).toBeInTheDocument();
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
  await userEvent.click(screen.getByRole('button',{name:'Activar tema claro'}));
  expect(document.documentElement).toHaveClass('light');
});
it('offers same-origin downloads and describes MCP accurately', async () => {
  await ready();
  await userEvent.click(screen.getByRole('button',{name:'Datos y API'}));
  const dialog = await screen.findByRole('dialog');
  expect(within(dialog).getByRole('link',{name:'CSV'})).toHaveAttribute('href','/data/records.csv');
  expect(dialog).toHaveTextContent('No hay un servidor remoto');
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
