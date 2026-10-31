import { expect, test } from '@playwright/test';

// Spec 9: the theme toggle persists the choice across a reload.
test.describe('theme', () => {
  test('toggle persists across reload', async ({ page }) => {
    await page.goto('/');
    const html = page.locator('html');
    const initial = await html.getAttribute('class');

    const toggle = page.getByRole('button', { name: /Activar tema/ });
    await toggle.click();
    const toggled = await html.getAttribute('class');
    expect(toggled).not.toBe(initial);

    await page.reload();
    await expect(page.locator('.archive-record').first()).toBeVisible();
    expect(await html.getAttribute('class')).toBe(toggled);
  });
});
