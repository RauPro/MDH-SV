import { expect, test } from '@playwright/test';

// Spec 7: a failing /archive/news.json response shows the error message and
// the retry control restores the list once the file is back.
test.describe('archive failure path', () => {
  test('shows the error and recovers on retry', async ({ page }) => {
    let fail = true;
    await page.route('**/archive/news.json', route => {
      if (fail) {
        void route.fulfill({ status: 500, contentType: 'application/json', body: '{}' });
      } else {
        void route.continue();
      }
    });

    await page.goto('/');
    await expect(page.getByRole('alert')).toBeVisible();
    await expect(page.getByRole('alert')).toContainText('No pudimos cargar el archivo de noticias');

    fail = false;
    await page.getByRole('button', { name: 'Reintentar archivo' }).click();
    await expect(page.locator('.archive-record').first()).toBeVisible();
  });
});
