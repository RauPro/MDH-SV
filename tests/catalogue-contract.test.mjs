import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import { parseCatalog } from '../shared/catalog.mjs';
import { loadContractCases } from './contract-fixtures.mjs';

for (const { name, valid, document } of loadContractCases(new URL('./fixtures/contracts/catalogue.json', import.meta.url))) {
  test(`public catalogue contract: ${name}`, () => {
    const original = structuredClone(document);
    if (valid) assert.equal(parseCatalog(document).records.length, document.records.length);
    else assert.throws(() => parseCatalog(document));
    assert.deepEqual(document, original);
  });
}

test('all current public catalogue snapshots remain valid', () => {
  for (const path of ['../data/reviewed/publications.json', '../apps/web/public/data/catalog.json',
    './fixtures/catalog.json', '../apps/web/src/__fixtures__/catalog.json']) {
    const value = JSON.parse(readFileSync(new URL(path, import.meta.url), 'utf8'));
    assert.equal(parseCatalog(value).records.length, value.records.length);
  }
});

test('returned dossiers do not share references with the input', () => {
  const document = JSON.parse(readFileSync(new URL('./fixtures/catalog.json', import.meta.url), 'utf8'));
  const original = structuredClone(document);
  const parsed = parseCatalog(document);
  parsed.records[0].dossier.facts.value = 'mutation in caller';
  parsed.records[0].related_sources.push({});
  assert.deepEqual(document, original);
});
