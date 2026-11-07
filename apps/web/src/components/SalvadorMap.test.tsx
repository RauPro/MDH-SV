import { expect, it, vi } from 'vitest';
import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { SalvadorMap } from './SalvadorMap';
import { UNKNOWN_DEPARTMENT } from '../geography/territory';
import { officialStatistics } from '../__fixtures__/officialStatistics';

const layers = {
  official: { period: officialStatistics.periods[0], source: officialStatistics.sources[0], asOf: officialStatistics.as_of },
  reports: { counts: { Sonsonate: 2 }, located: 2, unlocated: 1, available: true },
};
const props = { unit: 'news' as const, selected: '', onSelect: vi.fn(), counts: { Sonsonate: 3 }, located: 3, unlocated: 4, invalidDates: false };

it('shows separate official, news and report values on one map with independent checkboxes', async () => {
  render(<SalvadorMap {...props} layers={layers}/>);
  const map = screen.getByRole('region', { name: 'Mapa de El Salvador' });
  const department = within(map).getByRole('button', { name: /^Sonsonate/ });
  expect(department).toHaveTextContent('Noticias: 3');
  expect(department).toHaveTextContent('Reportes individuales: 2');
  expect(department).toHaveTextContent('Oficial 2010: 0');
  expect(screen.getByTestId('map-region-SVSO')).toHaveClass('official-layer', 'map-level-0');
  await userEvent.click(screen.getByRole('checkbox', { name: 'Homicidios oficiales' }));
  expect(department).not.toHaveTextContent('Oficial 2010');
  expect(department).toHaveTextContent('Noticias: 3');
  await userEvent.click(screen.getByRole('checkbox', { name: 'Noticias' }));
  expect(department).not.toHaveTextContent('Noticias:');
  expect(department).toHaveTextContent('Reportes individuales: 2');
  await userEvent.click(screen.getByRole('checkbox', { name: 'Reportes documentados' }));
  expect(screen.getByText('Activa al menos una capa para consultar el mapa.')).toBeInTheDocument();
  expect(department).toBeDisabled();
});

it('never substitutes zero for unavailable official departments or mixes news failures into statistics', () => {
  const period = officialStatistics.periods.at(-1)!;
  render(<SalvadorMap {...props} invalidDates available={false} layers={{ ...layers, official: { ...layers.official, period, source: officialStatistics.sources[1] } }}/>);
  const department = screen.getByRole('button', { name: /^Sonsonate/ });
  expect(department).toHaveTextContent('Oficial 2026: Sin datos');
  expect(department).toHaveTextContent('Noticias: —');
  expect(department).toHaveTextContent('Reportes individuales: 2');
  expect(department).not.toBeDisabled();
  expect(screen.getByText(/No se reparte el total nacional/)).toBeInTheDocument();
});

it('keeps unknown report loads distinct from empty counts and retains news when official loading fails', () => {
  render(<SalvadorMap {...props} layers={{ official: null, reports: { ...layers.reports, available: false } }}/>);
  const department = screen.getByRole('button', { name: /^Sonsonate/ });
  expect(department).toHaveTextContent('Noticias: 3');
  expect(department).toHaveTextContent('Reportes individuales: —');
  expect(department).toHaveTextContent('Oficial: —');
  expect(department).not.toBeDisabled();
});

it('labels the news map separately and keeps unlocated news discoverable by keyboard', async () => {
  const onSelect = vi.fn();
  render(<SalvadorMap unit="news" selected="" onSelect={onSelect} counts={{ 'San Salvador': 1 }} located={1} unlocated={1812} invalidDates={false}/>);
  const map = screen.getByRole('region', { name: 'Mapa de El Salvador' });
  expect(map).toHaveTextContent('1 noticia con departamento disponible');
  expect(map).toHaveTextContent('departamentos con revisión humana y menciones extraídas automáticamente, sin revisar');
  expect(map).toHaveTextContent('las fechas corresponden a la publicación');
  expect(map).toHaveTextContent('Las noticias no se suman al recuento de casos');
  expect(map).not.toHaveTextContent('reportes individuales');
  const department = within(map).getByRole('button', { name: /^San Salvador/ });
  department.focus();
  await userEvent.keyboard('{Enter}');
  expect(onSelect).toHaveBeenLastCalledWith('San Salvador');
  await userEvent.click(within(map).getByRole('button', { name: 'Sin ubicación pública en El Salvador 1812' }));
  expect(onSelect).toHaveBeenLastCalledWith(UNKNOWN_DEPARTMENT);
});

it('keeps the default individual-report terminology and selection behavior', async () => {
  const onSelect = vi.fn();
  render(<SalvadorMap selected="San Salvador" onSelect={onSelect} counts={{ 'San Salvador': 2 }} located={2} unlocated={1} invalidDates={false}/>);
  expect(screen.getByRole('button', { name: /^San Salvador/ })).toHaveAttribute('aria-pressed', 'true');
  expect(screen.getByRole('button', { name: 'Sin departamento identificado 1' })).toBeInTheDocument();
  await userEvent.click(screen.getByRole('button', { name: /^San Salvador/ }));
  expect(onSelect).toHaveBeenCalledWith('');
});

it('prevents department selection when the news publication date range is invalid', () => {
  render(<SalvadorMap unit="news" selected="" onSelect={vi.fn()} counts={{ 'San Salvador': 1 }} located={1} unlocated={2} invalidDates/>);
  expect(screen.getByRole('button', { name: /^San Salvador/ })).toBeDisabled();
  expect(screen.getByRole('button', { name: 'Sin ubicación pública en El Salvador —' })).toBeDisabled();
  expect(screen.getByText('Corrige el rango de fechas para consultar los recuentos del mapa.')).toBeInTheDocument();
});

it('identifies automatic and reviewed map values with text as well as distinct styling', () => {
  render(<SalvadorMap unit="news" selected="" onSelect={vi.fn()} counts={{ 'San Salvador': 3 }} reviewedCounts={{ 'San Salvador': 1 }} automaticCounts={{ 'San Salvador': 2 }} located={3} reviewed={1} automated={2} unlocated={4} invalidDates={false}/>);
  expect(screen.getByRole('button', { name: /^San Salvador/ })).toHaveTextContent('1 revisadas2 automáticas');
  expect(screen.getByTestId('map-region-SVSS')).toHaveClass('mixed-location');
  expect(screen.getByText('Ambas capas')).toBeInTheDocument();
  expect(screen.getByText('noticias con menciones automáticas sin revisar')).toHaveTextContent('2 noticias con menciones automáticas sin revisar');
});

it('uses unavailable counts rather than zero for a failed public location load', () => {
  const onSelect = vi.fn();
  render(<SalvadorMap unit="news" selected="" onSelect={onSelect} counts={{}} located={0} unlocated={0} invalidDates={false} available={false}/>);
  expect(screen.getByRole('button', { name: /^San Salvador/ })).toBeDisabled();
  expect(screen.getByRole('button', { name: 'Sin ubicación pública en El Salvador —' })).toBeDisabled();
  expect(screen.queryByText('0 en esta consulta')).not.toBeInTheDocument();
  expect(screen.getByRole('status')).toHaveTextContent('Recuentos del mapa no disponibles');
});
