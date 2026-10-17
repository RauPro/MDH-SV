import { sourceId } from '../../../tests/source-identity-fixture.mjs';
import { afterEach, expect, it, vi } from 'vitest';
import registryDocument from '../../../shared/source-registry.json';
import fixture from './__fixtures__/newsArchive.json';

// An isolated registry dependency simulates a reviewed JSON-only registration.
// The live source registry and production feeds remain untouched.
afterEach(() => {
  vi.doUnmock('../../../shared/source-registry.mjs');
  vi.resetModules();
});

it('accepts a new registered publisher in the archive and primary URL policy without parser edits', async () => {
  vi.resetModules();
  const registry = structuredClone(registryDocument);
  registry.publishers.push({ id: 'synthetic-outlet', name: 'Synthetic Outlet', kind: 'wordpress',
    endpoint: 'https://synthetic.example.org/wp-json/wp/v2/posts', hosts: ['synthetic.example.org'],
    feeds: [{ url: 'https://synthetic.example.org/feed/', consumers: ['private_queue'] }] });
  vi.doMock('../../../shared/source-registry.mjs', async importOriginal => {
    const actual = await importOriginal<typeof import('../../../shared/source-registry.mjs')>();
    const derived = actual.deriveSourceRegistry(registry);
    return { ...actual, NEWS_PUBLISHERS: derived.publishers, NEWS_SOURCES: derived.publishers, NEWS_HOSTS: derived.newsHosts,
      PRIMARY_SOURCE_HOSTS: derived.primarySourceHosts, PUBLISHER_HOSTS: derived.publisherHosts,
      HTML_PUBLISHERS: derived.htmlPublishers, SOURCE_FEEDS: derived.feeds };
  });
  const { parseNewsArchive } = await import('./newsArchive');
  const { safeSource } = await import('../../../shared/news-dossier.mjs');
  const value = structuredClone(fixture);
  const reference = value.records[0];
  const previousId = reference.source_id;
  const source = value.coverage.sources.find(row => row.id === previousId)!;
  source.id = reference.source_id = 'synthetic-outlet';
  source.publisher = reference.publisher = 'Synthetic Outlet';
  source.endpoint = reference.discovery.endpoint = 'https://synthetic.example.org/wp-json/wp/v2/posts';
  reference.source_url = 'https://synthetic.example.org/detenciones/';
  reference.id = sourceId(reference.source_url);
  for (const month of value.coverage.months) {
    for (const entry of month.sources) if (entry.source_id === previousId) entry.source_id = source.id;
  }
  expect(parseNewsArchive(value).records.find(row => row.source_id === source.id)?.publisher).toBe(source.publisher);
  expect(safeSource(reference.source_url)).toBe(true);
  expect(safeSource('https://child.synthetic.example.org/detenciones/')).toBe(false);
  reference.source_url = 'https://unregistered.example.org/detenciones/';
  expect(() => parseNewsArchive(value)).toThrow();
});
