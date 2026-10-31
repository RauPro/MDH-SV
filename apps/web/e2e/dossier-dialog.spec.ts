import { expect, test } from '@playwright/test';
import { dossierBackedReference } from './helpers';

// Spec 3: the dossier dialog opens from a news card, Escape closes it and
// focus returns to the button that opened it.
test.describe('news dossier dialog', () => {
  test('opens, closes with Escape and restores focus', async ({ page }) => {
    const reference = dossierBackedReference();
    await page.goto('/');
    // Truncated titles end with an ellipsis, so match the title as a prefix.
    const opener = page.getByRole('button', { name: reference.title }).first();
    await expect(opener).toBeVisible();
    await opener.click();

    const dialog = page.getByRole('dialog');
    await expect(dialog).toBeVisible();
    await expect(dialog.getByRole('heading', { level: 2 })).toBeVisible();

    await page.keyboard.press('Escape');
    await expect(dialog).toBeHidden();
    await expect(opener).toBeFocused();
  });
});
