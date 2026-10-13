import { beforeEach, expect, it, vi } from 'vitest';
import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import axe from 'axe-core';
import { officialStatistics } from '../__fixtures__/officialStatistics';
import publicStatistics from '../../public/statistics/homicides.json';
import { OfficialStatistics } from './OfficialStatistics';

beforeEach(() => {
  vi.restoreAllMocks();
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true, json: async () => structuredClone(officialStatistics) }));
});
async function ready() {
  render(<OfficialStatistics/>);
  return screen.findByRole('region', { name: 'Resumen de la consulta' });
}

it('defaults to the latest year and distinguishes its partial period from a full annual total', async () => {
  const summary = await ready();
  expect(screen.getByLabelText('Año')).toHaveValue('2026');
  expect(screen.getByLabelText('Departamento')).toHaveValue('Nacional');
  expect(summary).toHaveTextContent('8');
  expect(summary).toHaveTextContent('homicidios reportados por la fuente');
  expect(summary).toHaveTextContent('Período parcial');
  expect(summary).toHaveTextContent('31 de agosto de 2026');
  expect(screen.getByText(/Las fuentes emplean definiciones distintas/)).toBeInTheDocument();
  expect(within(screen.getByLabelText('Año')).getAllByRole('option')).toHaveLength(17);
});

it('shows all 14 missing departments without inventing zeroes or a geographic distribution', async () => {
  await ready();
  const table = screen.getByRole('table', { name: /^14 departamentos/ });
  expect(within(table).getAllByRole('row')).toHaveLength(15);
  expect(within(table).getAllByText('Sin datos')).toHaveLength(14);
  expect(within(table).queryByText('0')).not.toBeInTheDocument();
  expect(screen.getByText(/esto no demuestra que no existan registros oficiales por departamento/)).toBeInTheDocument();
  await userEvent.selectOptions(screen.getByLabelText('Departamento'), 'Sonsonate');
  const summary = screen.getByRole('region', { name: 'Resumen de la consulta' });
  expect(summary).toHaveTextContent('Sin datos');
  expect(summary).not.toHaveTextContent('Período completo');
  expect(summary).toHaveTextContent('La ausencia de datos no equivale a cero');
  const history = screen.getByRole('table', { name: 'Serie 2010–2026 · Sonsonate' });
  const historicalRow = within(history).getByRole('button', { name: 'Consultar 2010' }).closest('tr')!;
  expect(within(historicalRow).getByText('0')).toBeInTheDocument();
  expect(historicalRow).toHaveTextContent('personas registradas por la fuente');
});

it('preserves a genuine zero and changes the source and methodology with the year', async () => {
  await ready();
  await userEvent.selectOptions(screen.getByLabelText('Departamento'), 'Sonsonate');
  await userEvent.click(screen.getByRole('button', { name: 'Consultar 2010' }));
  const summary = screen.getByRole('region', { name: 'Resumen de la consulta' });
  expect(summary).toHaveTextContent('0');
  expect(summary).toHaveTextContent('Período completo');
  expect(summary).toHaveTextContent('personas registradas por la fuente');
  expect(summary).toHaveTextContent('Página 4, cuadro por departamento');
  expect(within(summary).getByRole('link', { name: /Instituto de Medicina Legal/ })).toHaveAttribute('href', officialStatistics.sources[0].url);
  expect(screen.getByText('Personas examinadas por el instituto, conforme a la definición del documento.')).toBeInTheDocument();
  expect(screen.queryByRole('table', { name: /^Detalle mensual/ })).not.toBeInTheDocument();
  const source = screen.getByRole('link', { name: /Documento de prueba IML/ }).closest('article')!;
  await userEvent.click(within(source).getByText('Referencia e integridad del documento'));
  expect(within(source).getByText('a'.repeat(64))).toBeVisible();
  await userEvent.click(within(screen.getByRole('table', { name: /^14 departamentos/ })).getByRole('button', { name: 'San Salvador' }));
  expect(screen.getByLabelText('Departamento')).toHaveValue('San Salvador');
  expect(screen.getByRole('region', { name: 'Resumen de la consulta' })).toHaveTextContent('10');
});

it('marks an unverifiable year as unavailable without substituting another institution or year', async () => {
  await ready();
  await userEvent.selectOptions(screen.getByLabelText('Año'), '2012');
  const summary = screen.getByRole('region', { name: 'Resumen de la consulta' });
  expect(summary).toHaveTextContent('Nacional · 2012');
  expect(summary).toHaveTextContent('Sin datos');
  expect(summary).not.toHaveTextContent('Período completo');
  expect(screen.getByText('Documento incompleto: no se incorporó un total verificable.')).toBeInTheDocument();
});

it('separates missing months from months outside the cutoff and labels monthly figures as national', async () => {
  await ready();
  await userEvent.selectOptions(screen.getByLabelText('Departamento'), 'Sonsonate');
  const table = screen.getByRole('table', { name: /^Detalle mensual nacional · 2026/ });
  const row = (name: string) => within(table).getByRole('rowheader', { name }).closest('tr')!;
  expect(row('agosto')).toHaveTextContent('1');
  expect(row('septiembre')).toHaveTextContent('Sin datos');
  expect(row('octubre')).toHaveTextContent('Sin datos');
  expect(row('noviembre')).toHaveTextContent('Fuera del corte');
  expect(row('diciembre')).toHaveTextContent('Fuera del corte');
  expect(within(table).getAllByText('Sin datos')).toHaveLength(2);
  expect(within(table).getAllByText('Fuera del corte')).toHaveLength(2);
});

it('loads only its own dataset, offers complete downloads and does not poll', async () => {
  await ready();
  expect(fetch).toHaveBeenCalledTimes(1);
  expect(fetch).toHaveBeenCalledWith('/statistics/homicides.json', expect.objectContaining({ signal: expect.any(AbortSignal) }));
  expect(screen.getByRole('link', { name: 'Conjunto completo · CSV' })).toHaveAttribute('href', '/statistics/homicides.csv');
  expect(screen.getByRole('link', { name: 'Integridad de las descargas' })).toHaveAttribute('href', '/statistics/manifest.json');
});

it('shows update instants in El Salvador while retaining calendar dates for the reporting period', async () => {
  vi.mocked(fetch).mockResolvedValueOnce({ ok: true, json: async () => ({ ...officialStatistics, generated_at: '2026-10-13T03:00:00Z' }) } as Response);
  const summary = await ready();
  expect(screen.getByText(/Actualización del conjunto: 12 de octubre de 2026/)).toHaveTextContent('(El Salvador)');
  expect(summary).toHaveTextContent('31 de agosto de 2026');
});

it('renders the published UNODC source for 2012 and preserves real missing departmental data', async () => {
  vi.mocked(fetch).mockResolvedValueOnce({ ok: true, json: async () => publicStatistics } as Response);
  await ready();
  await userEvent.selectOptions(screen.getByLabelText('Año'), '2012');
  const summary = screen.getByRole('region', { name: 'Resumen de la consulta' });
  expect(summary).toHaveTextContent((2594).toLocaleString('es-SV'));
  expect(summary).toHaveTextContent('personas registradas por la fuente');
  expect(summary).toHaveTextContent('Homicidios intencionales');
  expect(summary).toHaveTextContent('L3321 = 2594');
  const source = publicStatistics.sources.find(row => row.id === publicStatistics.periods.find(row => row.year === 2012)!.source_id)!;
  expect(within(summary).getByRole('link', { name: /UNODC/ })).toHaveAttribute('href', source.url);
  const sourceCard = screen.getByRole('link', { name: /Victims of intentional homicide/ }).closest('article')!;
  expect(sourceCard).toHaveTextContent('XLSX');
  await userEvent.click(within(sourceCard).getByText('Referencia e integridad del documento'));
  expect(within(sourceCard).getByText(source.content_sha256)).toBeVisible();
  expect(within(screen.getByRole('table', { name: /^14 departamentos/ })).getAllByText('Sin datos')).toHaveLength(14);
  await userEvent.selectOptions(screen.getByLabelText('Departamento'), 'San Salvador');
  expect(summary).toHaveTextContent('Sin datos');
  expect(summary).not.toHaveTextContent((2594).toLocaleString('es-SV'));
  await userEvent.selectOptions(screen.getByLabelText('Año'), '2026');
  expect(summary).toHaveTextContent('Sin datos');
  expect(summary).toHaveTextContent('Fiscalía General');
  expect(within(screen.getByRole('table', { name: /^14 departamentos/ })).getAllByText('Sin datos')).toHaveLength(14);
  expect(screen.getByRole('table', { name: /^Detalle mensual nacional · 2026/ })).toBeInTheDocument();
});

it('fails closed on invalid statistics and allows retry without showing malformed values', async () => {
  vi.mocked(fetch).mockResolvedValueOnce({ ok: true, json: async () => ({ ...officialStatistics, periods: [{ ...officialStatistics.periods[0], national: { value: -999, status: 'available', locator: 'x' } }] }) } as Response);
  render(<OfficialStatistics/>);
  expect(await screen.findByRole('alert')).toHaveTextContent('No pudimos cargar las estadísticas oficiales');
  expect(screen.queryByText('-999')).not.toBeInTheDocument();
  await userEvent.click(screen.getByRole('button', { name: 'Reintentar estadísticas' }));
  await screen.findByRole('region', { name: 'Resumen de la consulta' });
});

it('handles unavailable files without displaying placeholder counts', async () => {
  vi.mocked(fetch).mockResolvedValueOnce({ ok: false, json: async () => officialStatistics } as Response);
  render(<OfficialStatistics/>);
  await screen.findByRole('alert');
  expect(screen.queryByRole('table')).not.toBeInTheDocument();
  expect(screen.queryByRole('region', { name: 'Resumen de la consulta' })).not.toBeInTheDocument();
});

it('has no detectable structural accessibility violations', async () => {
  await ready();
  const result = await axe.run(document.body, { rules: { 'color-contrast': { enabled: false }, region: { enabled: false } } });
  expect(result.violations.map(value => value.id)).toEqual([]);
});
