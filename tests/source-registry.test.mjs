import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import { loadContractCases } from './contract-fixtures.mjs';
import { deriveSourceRegistry, validateSourceRegistry, NEWS_HOSTS, PRIMARY_SOURCE_HOSTS,
  NEWS_SOURCES, OFFICIAL_STATISTICS_HOSTS, OFFICIAL_STATISTICS_DISCOVERY_HOSTS,
  SOURCE_FEEDS, PUBLISHER_HOSTS } from '../shared/source-registry.mjs';

for (const example of loadContractCases(new URL('./fixtures/source-registry-contract.json', import.meta.url))) {
  test(`source registry contract: ${example.name}`, () => {
    if (example.valid) assert.doesNotThrow(() => validateSourceRegistry(example.document));
    else assert.throws(() => validateSourceRegistry(example.document), /^Error: Invalid source registry\.$/);
  });
}

test('host roles and RSS consumers preserve the existing separate capabilities', () => {
  assert.equal(NEWS_SOURCES.length, 7);
  assert.equal(NEWS_HOSTS.length, 14);
  assert.equal(PRIMARY_SOURCE_HOSTS.length, 21);
  assert.deepEqual(SOURCE_FEEDS.private_queue.map(feed => feed.name), ['Diario Co Latino', 'GatoEncerrado', 'Revista Factum']);
  assert.deepEqual(SOURCE_FEEDS.legacy_scraper.map(feed => feed.name), ['Diario Co Latino', 'GatoEncerrado', 'El Faro']);
  assert.deepEqual(OFFICIAL_STATISTICS_DISCOVERY_HOSTS.filter(host => !OFFICIAL_STATISTICS_HOSTS.includes(host)), ['transparencia.pnc.gob.sv']);
  assert.ok(!PRIMARY_SOURCE_HOSTS.some(host => OFFICIAL_STATISTICS_DISCOVERY_HOSTS.includes(host)));
  assert.ok(!NEWS_HOSTS.includes('evil.elsalvador.com'));
  assert.ok(!PRIMARY_SOURCE_HOSTS.includes('child.www.hrw.org'));
  assert.equal(PUBLISHER_HOSTS.constructor, undefined);
  assert.throws(() => NEWS_HOSTS.push('untrusted.example.org'));
  assert.throws(() => PUBLISHER_HOSTS['co-latino'].push('untrusted.example.org'));
});

test('one synthetic registration reaches every declared JS projection without broadening other roles', () => {
  const registry = JSON.parse(readFileSync(new URL('../shared/source-registry.json', import.meta.url), 'utf8'));
  registry.publishers.push({ id: 'synthetic-outlet', name: 'Synthetic Outlet', kind: 'wordpress',
    endpoint: 'https://synthetic.example.org/wp-json/wp/v2/posts', hosts: ['synthetic.example.org'],
    feeds: [{ url: 'https://synthetic.example.org/feed/', consumers: ['private_queue'] }] });
  const result = deriveSourceRegistry(registry);
  assert.ok(result.newsHosts.includes('synthetic.example.org'));
  assert.ok(result.primarySourceHosts.includes('synthetic.example.org'));
  assert.equal(result.publishers.at(-1).id, 'synthetic-outlet');
  assert.deepEqual(result.publisherHosts['synthetic-outlet'], ['synthetic.example.org']);
  assert.equal(result.feeds.private_queue.at(-1).name, 'Synthetic Outlet');
  assert.ok(!result.feeds.legacy_scraper.some(feed => feed.name === 'Synthetic Outlet'));
  assert.ok(!result.officialStatisticsDiscoveryHosts.includes('synthetic.example.org'));
  assert.ok(!NEWS_HOSTS.includes('synthetic.example.org'));
});
