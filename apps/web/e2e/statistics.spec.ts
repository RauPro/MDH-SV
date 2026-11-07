import { expect, test } from '@playwright/test';
import { consoleErrors, readDistJson } from './helpers';
import type { OfficialStatistics } from '../../../shared/official-statistics.mjs';

// Spec 6: the official statistics view renders without errors.
test.describe('official statistics view', () => {
  test('keeps the annual national chart independent from news filters', async ({ page }) => {
    const data = readDistJson<OfficialStatistics>('statistics/homicides.json');
    const peak = data.periods.filter(row => row.completeness === 'complete').sort((a, b) => b.national.value! - a.national.value!)[0];
    await page.goto('/');
    const chart = page.getByRole('region', { name: 'Homicidios reportados por fuentes oficiales' });
    await expect(chart.getByRole('button', { name: new RegExp(`^${peak.year}:`) })).toHaveAttribute('aria-pressed', 'true');
    const map = page.getByRole('region', { name: 'Mapa de El Salvador' });
    const department = map.getByRole('button', { name: /^San Salvador/ });
    const observation = peak.departments.find(row => row.department === 'San Salvador')!;
    await expect(department).toContainText(`Oficial ${peak.year}: ${observation.value!.toLocaleString('es-SV')}`);
    await map.getByRole('checkbox', { name: 'Noticias', exact: true }).uncheck();
    await expect(department).not.toContainText('Noticias:');
    await expect(department).toContainText('Reportes individuales:');
    await map.getByRole('checkbox', { name: 'Noticias', exact: true }).check();
    const partial = data.periods.find(row => row.completeness === 'partial')!;
    const year = chart.getByRole('button', { name: new RegExp(`^${partial.year}:`) });
    await year.focus();
    await page.keyboard.press('Enter');
    await expect(year).toHaveAttribute('aria-pressed', 'true');
    await expect(chart.getByRole('region', { name: 'Detalle del año seleccionado' })).toContainText('Período parcial');
    await expect(department).toContainText(`Oficial ${partial.year}: Sin datos`);
    await map.getByRole('checkbox', { name: 'Homicidios oficiales' }).uncheck();
    await expect(department).not.toContainText('Oficial');
    await expect(department).toContainText('Noticias:');
    await map.getByRole('checkbox', { name: 'Reportes documentados' }).uncheck();
    await map.getByRole('checkbox', { name: 'Noticias', exact: true }).uncheck();
    await expect(department).toBeDisabled();
    await map.getByRole('checkbox', { name: 'Noticias', exact: true }).check();
    await expect(page.getByLabel('Publicación desde')).toHaveValue('');
    await page.getByRole('searchbox').fill('sin coincidencias en el archivo');
    await expect(year).toHaveAttribute('aria-pressed', 'true');
    await expect(chart.getByRole('group', { name: 'Totales nacionales por año' }).getByRole('button')).toHaveCount(data.periods.length);
    await chart.getByRole('link', { name: 'Consultar estadísticas y metodología' }).click();
    await expect(page.locator('.official-statistics')).toBeVisible();
  });

  test('renders the statistics workspace', async ({ page }) => {
    const errors = consoleErrors(page);
    await page.goto('/#estadisticas');
    await expect(page.locator('.official-statistics')).toBeVisible();
    await expect(page.locator('.official-statistics-section').first()).toBeVisible();
    await page.waitForLoadState('networkidle');
    expect(errors).toEqual([]);
  });
});
