import { beforeEach, expect, it, vi } from 'vitest';
import { act, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import axe from 'axe-core';
import publicStatistics from '../../public/statistics/homicides.json';
import { officialStatistics } from '../__fixtures__/officialStatistics';
import { OfficialHomicideChart } from './OfficialHomicideChart';

beforeEach(() => {
  vi.restoreAllMocks();
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true, json: async () => structuredClone(officialStatistics) }));
});

async function ready() {
  render(<OfficialHomicideChart/>);
  return screen.findByRole('group', { name: 'Totales nacionales por año' });
}

it('shares only the selected official period and provenance with the map', async () => {
  const onContextChange = vi.fn();
  render(<OfficialHomicideChart onContextChange={onContextChange}/>);
  const chart = await screen.findByRole('group', { name: 'Totales nacionales por año' });
  await waitFor(() => expect(onContextChange).toHaveBeenLastCalledWith(expect.objectContaining({ period: expect.objectContaining({ year: 2022 }), source: officialStatistics.sources[1] })));
  await userEvent.selectOptions(screen.getByLabelText('Año de estadísticas oficiales'), '2010');
  expect(within(chart).getByRole('button', { name: /^2010:/ })).toHaveAttribute('aria-pressed', 'true');
  expect(onContextChange).toHaveBeenLastCalledWith(expect.objectContaining({ period: expect.objectContaining({ year: 2010 }), source: officialStatistics.sources[0] }));
});

it('loads only validated official statistics and keeps historical context separate from the map', async () => {
  await ready();
  expect(fetch).toHaveBeenCalledTimes(1);
  expect(fetch).toHaveBeenCalledWith('/statistics/homicides.json', expect.objectContaining({ signal: expect.any(AbortSignal) }));
  expect(screen.getByText(/No son noticias ni casos del mapa/)).toBeInTheDocument();
  expect(screen.getByText(/No cambian con sus filtros/)).toBeInTheDocument();
  expect(screen.getByText(/Las fuentes emplean definiciones distintas/)).toBeInTheDocument();
  expect(screen.getByRole('link', { name: 'Consultar estadísticas y metodología' })).toHaveAttribute('href', '#estadisticas');
  expect(screen.getByText(/Corte del conjunto/)).toHaveTextContent('12 de octubre de 2026');
});

it('plots the published national values in chronological order and derives the largest full-year selection', async () => {
  vi.mocked(fetch).mockResolvedValueOnce({ ok: true, json: async () => ({ ...publicStatistics, periods: [...publicStatistics.periods].reverse() }) } as Response);
  const chart = await ready();
  const buttons = within(chart).getAllByRole('button');
  expect(buttons).toHaveLength(publicStatistics.periods.length);
  expect(buttons[0]).toHaveAccessibleName(/^2010:/);
  expect(buttons.at(-1)).toHaveAccessibleName(/^2026:/);
  for (const period of publicStatistics.periods) {
    expect(within(chart).getByRole('button', { name: new RegExp(`^${period.year}:`) })).toHaveAccessibleName(new RegExp(period.national.value!.toLocaleString('es-SV')));
  }
  const peak = within(chart).getByRole('button', { name: /^2015:/ });
  expect(peak).toHaveAttribute('aria-pressed', 'true');
  expect(peak.querySelector('.homicide-chart-bar')).toHaveStyle({ height: '100%' });
  expect(screen.getByRole('region', { name: 'Detalle del año seleccionado' })).toHaveTextContent((6656).toLocaleString('es-SV'));
});

it('does not hard-code 2015 or select a partial period as the largest annual observation', async () => {
  const data = structuredClone(officialStatistics);
  data.periods.find(row => row.year === 2018)!.national.value = 30;
  data.periods.find(row => row.year === 2018)!.departments.find(row => row.department === 'San Salvador')!.value = 30;
  data.periods.find(row => row.year === 2026)!.national.value = 40;
  for (const month of data.periods.find(row => row.year === 2026)!.months!) if (month.status === 'available') month.value = 5;
  vi.mocked(fetch).mockResolvedValueOnce({ ok: true, json: async () => data } as Response);
  const chart = await ready();
  expect(within(chart).getByRole('button', { name: /^2018:/ })).toHaveAttribute('aria-pressed', 'true');
  expect(within(chart).getByRole('button', { name: /^2026:/ })).toHaveAttribute('aria-pressed', 'false');
});

it('provides source, locator, unit, reporting dates and methodology when selecting years', async () => {
  const chart = await ready();
  await userEvent.click(within(chart).getByRole('button', { name: /^2010:/ }));
  const detail = screen.getByRole('region', { name: 'Detalle del año seleccionado' });
  expect(detail).toHaveTextContent('personas registradas por la fuente');
  expect(detail).toHaveTextContent('Página 4, total nacional');
  expect(detail).toHaveTextContent('Personas examinadas por el instituto');
  expect(within(detail).getByRole('link', { name: 'Instituto de Medicina Legal' })).toHaveAttribute('href', officialStatistics.sources[0].url);
  await userEvent.click(within(chart).getByRole('button', { name: /^2026:/ }));
  expect(detail).toHaveTextContent('Período parcial');
  expect(detail).toHaveTextContent('31 de agosto de 2026');
  expect(detail).toHaveTextContent('no es un total anual');
  expect(detail).toHaveTextContent('homicidios reportados por la fuente');
  expect(within(detail).getByRole('link', { name: 'Fiscalía General de la República' })).toHaveAttribute('href', officialStatistics.sources[1].url);
  expect(within(chart).getByRole('button', { name: /^2026:/ })).toHaveAccessibleName(/Período parcial/);
});

it('does not draw missing data as zero and preserves a verified zero', async () => {
  const data = structuredClone(officialStatistics);
  data.periods[0].national.value = 0;
  data.periods[0].departments.find(row => row.department === 'San Salvador')!.value = 0;
  vi.mocked(fetch).mockResolvedValueOnce({ ok: true, json: async () => data } as Response);
  const chart = await ready();
  const missing = within(chart).getByRole('button', { name: /^2012:/ });
  expect(missing).toHaveAccessibleName(/Sin datos/);
  expect(missing.querySelector('.homicide-chart-bar')).toBeNull();
  await userEvent.click(missing);
  const detail = screen.getByRole('region', { name: 'Detalle del año seleccionado' });
  expect(detail).toHaveTextContent('La ausencia de datos no equivale a cero');
  expect(detail).toHaveTextContent('Documento incompleto');
  const zero = within(chart).getByRole('button', { name: /^2010:/ });
  expect(zero).toHaveAccessibleName(/2010: 0 /);
  expect(zero.querySelector('.homicide-chart-bar')).toHaveStyle({ height: '0%' });
  await userEvent.click(zero);
  expect(detail).toHaveTextContent('Período completo');
  expect(detail).not.toHaveTextContent('Sin datos');
});

it('handles all-zero observations without nonfinite bar heights', async () => {
  const data = structuredClone(officialStatistics);
  for (const period of data.periods) {
    if (period.national.status === 'available') period.national.value = 0;
    for (const row of [...period.departments, ...(period.months ?? [])]) if (row.status === 'available') row.value = 0;
  }
  vi.mocked(fetch).mockResolvedValueOnce({ ok: true, json: async () => data } as Response);
  const chart = await ready();
  for (const bar of chart.querySelectorAll('.homicide-chart-bar')) expect(bar).toHaveStyle({ height: '0%' });
});

it('fails closed on malformed data and recovers with an isolated retry', async () => {
  vi.mocked(fetch).mockResolvedValueOnce({ ok: true, json: async () => ({ ...officialStatistics, periods: [] }) } as Response);
  render(<OfficialHomicideChart/>);
  expect(await screen.findByRole('alert')).toHaveTextContent('No pudimos cargar el contexto histórico');
  expect(screen.queryByRole('group', { name: 'Totales nacionales por año' })).not.toBeInTheDocument();
  await userEvent.click(screen.getByRole('button', { name: 'Reintentar contexto histórico' }));
  await screen.findByRole('group', { name: 'Totales nacionales por año' });
  expect(fetch).toHaveBeenCalledTimes(2);
});

it('times out unavailable requests and cancels loading on unmount', async () => {
  vi.useFakeTimers();
  let signal: AbortSignal;
  vi.mocked(fetch).mockImplementationOnce((_url, options) => new Promise((_resolve, reject) => {
    signal = options!.signal as AbortSignal;
    signal.addEventListener('abort', () => reject(new DOMException('Aborted', 'AbortError')));
  }));
  const mounted = render(<OfficialHomicideChart/>);
  expect(screen.getByRole('status')).toHaveTextContent('Cargando contexto histórico');
  try {
    await act(async () => vi.advanceTimersByTime(12000));
    expect(signal!.aborted).toBe(true);
    expect(screen.getByRole('alert')).toBeInTheDocument();
  } finally {
    mounted.unmount();
    vi.useRealTimers();
  }
});

it('supports keyboard selection and has no detectable structural accessibility violations', async () => {
  const chart = await ready();
  const button = within(chart).getByRole('button', { name: /^2010:/ });
  button.focus();
  await userEvent.keyboard('{Enter}');
  expect(button).toHaveAttribute('aria-pressed', 'true');
  const result = await axe.run(document.body, { rules: { 'color-contrast': { enabled: false }, region: { enabled: false } } });
  expect(result.violations.map(value => value.id)).toEqual([]);
});
