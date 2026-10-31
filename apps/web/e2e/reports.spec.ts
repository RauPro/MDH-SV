import { expect, test } from '@playwright/test';
import { catalog } from './helpers';

// Spec 4: «Reportes documentados» — the records list renders, the map filters
// by department, a URL hash opens the record dialog, the copy-citation button
// gives feedback, and reloading the deep link keeps the dialog open.
test.describe('documented reports', () => {
  test('list, map filter, deep link and copy citation', async ({ page }) => {
    const records = catalog().records;
    const incidents = records.filter(record => record.record_type === 'incident');
    const status = page.locator('.results-heading [role="status"]');

    await page.goto('/#reportes');
    await expect(page.locator('.record').first()).toBeVisible();
    await expect(status).toContainText(`${incidents.length} resultado`);

    // The map filters results to one department.
    const counts = new Map<string, number>();
    for (const record of incidents) {
      if (record.department) {
        counts.set(record.department, (counts.get(record.department) ?? 0) + 1);
      }
    }
    const [department, departmentCount] = [...counts.entries()].sort((a, b) => b[1] - a[1])[0] ?? [];
    test.skip(!department, 'no incident carries a public department');
    await page.getByRole('button', { name: new RegExp(`^${department}`) }).first().click();
    await expect(status).toContainText(`${departmentCount} resultado`);
    await page.getByRole('button', { name: 'Quitar filtro del mapa' }).click();
    await expect(status).toContainText(`${incidents.length} resultado`);

    // A deep link opens the record dialog.
    const record = records[0];
    await page.goto(`/#${record.id}`);
    const dialog = page.getByRole('dialog');
    await expect(dialog).toBeVisible();
    await expect(dialog.getByRole('heading', { level: 2 })).toContainText(record.title.slice(0, 20));

    // The copy-citation button reports its outcome through a live region.
    await dialog.getByRole('button', { name: 'Copiar cita' }).click();
    await expect(dialog.getByRole('status').first()).not.toBeEmpty();

    // Reloading the deep link keeps the dialog open.
    await page.reload();
    await expect(page.getByRole('dialog')).toBeVisible();
  });
});
