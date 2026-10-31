import { expect, test } from '@playwright/test';

// Spec 5: the archive view shows the coverage details table and its download
// links return 200 with a JSON content type.
test.describe('archive view', () => {
  test('coverage details and JSON downloads', async ({ page }) => {
    await page.goto('/#archivo');
    const coverage = page.locator('details.archive-coverage');
    await expect(coverage).toBeVisible();
    await coverage.locator('> summary').click();
    await coverage.locator('details > summary').first().click();
    await expect(coverage.locator('details table').first()).toBeVisible();

    const links = page.locator('.downloads a[download]');
    const hrefs = await links.evaluateAll(anchors =>
      anchors.map(anchor => (anchor as HTMLAnchorElement).getAttribute('href')),
    );
    expect(hrefs.length).toBeGreaterThan(0);
    for (const href of hrefs) {
      const response = await page.request.get(href!);
      expect(response.status()).toBe(200);
      expect(response.headers()['content-type']).toContain('json');
    }
  });
});
