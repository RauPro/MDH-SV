import { expect, it, vi } from 'vitest';
import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { SalvadorMap } from './SalvadorMap';
import { UNKNOWN_DEPARTMENT } from '../geography/territory';

it('labels the news map separately and keeps unlocated news discoverable by keyboard', async () => {
  const onSelect = vi.fn();
  render(<SalvadorMap unit="news" selected="" onSelect={onSelect} counts={{ 'San Salvador': 1 }} located={1} unlocated={1812} invalidDates={false}/>);
  const map = screen.getByRole('region', { name: 'Mapa de El Salvador' });
  expect(map).toHaveTextContent('1 noticia con departamento disponible');
  expect(map).toHaveTextContent('departamentos con revisión humana y menciones extraídas automáticamente, sin revisar');
  expect(map).toHaveTextContent('las fechas corresponden a la publicación');
  expect(map).toHaveTextContent('Las noticias no se suman al recuento de casos');
  expect(map).not.toHaveTextContent('reportes individuales');
  const department = within(map).getByRole('button', { name: 'San Salvador: 1 noticia' });
  department.focus();
  await userEvent.keyboard('{Enter}');
  expect(onSelect).toHaveBeenLastCalledWith('San Salvador');
  await userEvent.click(within(map).getByRole('button', { name: 'Sin ubicación pública en El Salvador 1812' }));
  expect(onSelect).toHaveBeenLastCalledWith(UNKNOWN_DEPARTMENT);
});

it('keeps the default individual-report terminology and selection behavior', async () => {
  const onSelect = vi.fn();
  render(<SalvadorMap selected="San Salvador" onSelect={onSelect} counts={{ 'San Salvador': 2 }} located={2} unlocated={1} invalidDates={false}/>);
  expect(screen.getByRole('button', { name: 'San Salvador: 2 reportes individuales' })).toHaveAttribute('aria-pressed', 'true');
  expect(screen.getByRole('button', { name: 'Sin departamento identificado 1' })).toBeInTheDocument();
  await userEvent.click(screen.getByRole('button', { name: 'San Salvador: 2 reportes individuales' }));
  expect(onSelect).toHaveBeenCalledWith('');
});

it('prevents department selection when the news publication date range is invalid', () => {
  render(<SalvadorMap unit="news" selected="" onSelect={vi.fn()} counts={{ 'San Salvador': 1 }} located={1} unlocated={2} invalidDates/>);
  expect(screen.getByRole('button', { name: 'San Salvador: 1 noticia' })).toBeDisabled();
  expect(screen.getByRole('button', { name: 'Sin ubicación pública en El Salvador —' })).toBeDisabled();
  expect(screen.getByText('Corrige el rango de fechas para consultar los recuentos del mapa.')).toBeInTheDocument();
});

it('identifies automatic and reviewed map values with text as well as distinct styling', () => {
  render(<SalvadorMap unit="news" selected="" onSelect={vi.fn()} counts={{ 'San Salvador': 3 }} reviewedCounts={{ 'San Salvador': 1 }} automaticCounts={{ 'San Salvador': 2 }} located={3} reviewed={1} automated={2} unlocated={4} invalidDates={false}/>);
  expect(screen.getByRole('button', { name: 'San Salvador: 3 noticias' })).toHaveTextContent('1 revisadas2 automáticas');
  expect(screen.getByTestId('map-region-SVSS')).toHaveClass('mixed-location');
  expect(screen.getByText('Ambas capas')).toBeInTheDocument();
  expect(screen.getByText('automáticas sin revisar')).toHaveTextContent('2 automáticas sin revisar');
});

it('uses unavailable counts rather than zero for a failed public location load', () => {
  const onSelect = vi.fn();
  render(<SalvadorMap unit="news" selected="" onSelect={onSelect} counts={{}} located={0} unlocated={0} invalidDates={false} available={false}/>);
  expect(screen.getByRole('button', { name: 'San Salvador: ubicaciones pendientes de comprobar' })).toBeDisabled();
  expect(screen.getByRole('button', { name: 'Sin ubicación pública en El Salvador —' })).toBeDisabled();
  expect(screen.queryByText('0 en esta consulta')).not.toBeInTheDocument();
  expect(screen.getByRole('status')).toHaveTextContent('Recuentos del mapa no disponibles');
});
