import { expect, test } from '@playwright/test';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

// Spec 10: HTTP through the replica — API endpoints respond with JSON,
// sensitive and unknown paths get a plain 404 (no HTML fallback), and the
// vercel.json security headers are applied to responses.
test.describe('replica HTTP', () => {
  test('API endpoints answer with JSON', async ({ request }) => {
    const news = await request.get('/api/v1/news?limit=1');
    expect(news.status()).toBe(200);
    expect(news.headers()['content-type']).toContain('json');
    const body = await news.json();
    expect(Array.isArray(body.data)).toBe(true);
    expect(body.data.length).toBe(1);

    const openapi = await request.get('/api/v1/openapi.json');
    expect(openapi.status()).toBe(200);
    expect(openapi.headers()['content-type']).toContain('json');
  });

  test('unknown and sensitive paths return 404 without HTML', async ({ request }) => {
    for (const path of ['/nonexistent-page', '/.env', '/data/mdh_database.sqlite']) {
      const response = await request.get(path);
      expect(response.status(), path).toBe(404);
      expect(response.headers()['content-type'], path).not.toContain('html');
    }
  });

  test('vercel.json security headers reach every response', async ({ request }) => {
    const vercel = JSON.parse(
      readFileSync(fileURLToPath(new URL('../../../vercel.json', import.meta.url)), 'utf8'),
    ) as { headers: { source: string; headers: { key: string; value: string }[] }[] };
    const globalRule = vercel.headers.find(rule => rule.source === '/(.*)');
    expect(globalRule).toBeTruthy();

    for (const path of ['/', '/archive/news.json', '/api/v1/openapi.json']) {
      const response = await request.get(path);
      for (const { key, value } of globalRule!.headers) {
        expect(response.headers()[key.toLowerCase()], `${path} ${key}`).toBe(value);
      }
    }
  });

  test('API links and the OpenAPI origin stay on the replica', async ({ request }) => {
    const news = await request.get('/api/v1/news?limit=1');
    const body = await news.json();
    // The archive has thousands of records, so a next-page link always exists.
    expect(body.pagination?.next).toMatch(/^http:\/\/127\.0\.0\.1:\d+\//);
    expect(JSON.stringify(body)).not.toContain('mdh-sv.vercel.app');

    const openapi = await request.get('/api/v1/openapi.json');
    const doc = await openapi.json();
    expect(JSON.stringify(doc.servers ?? [])).toMatch(/^\[\{"url":"http:\/\/127\.0\.0\.1:\d+/);
    expect(JSON.stringify(doc)).not.toContain('mdh-sv.vercel.app');
  });
});
