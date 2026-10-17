import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import { loadContractCases } from './contract-fixtures.mjs';
import { parseNewsArchive, validateNewsReference } from '../shared/news-archive.mjs';
import { searchNews } from '../server/public-data.mjs';

for (const [kind, validate] of [['news-reference', validateNewsReference], ['news-archive', parseNewsArchive]]) {
  for (const { name, valid, document } of loadContractCases(new URL(`./fixtures/contracts/${kind}.json`, import.meta.url))) {
    test(`${kind}: ${name}`, () => {
      const original = structuredClone(document);
      if (valid) assert.ok(validate(document));
      else assert.throws(() => validate(document));
      assert.deepEqual(document, original);
      if (kind === 'news-archive' && !valid) {
        assert.throws(() => searchNews({}, document), error => error.status === 503
          && error.code === 'archive_selection_invalid' && !error.message.includes('PRIVATE_FIXTURE_SENTINEL'));
      }
    });
  }
}

test('the complete current selected archive passes the API/browser boundary', () => {
  const value = JSON.parse(readFileSync(new URL('../apps/web/public/archive/news.json', import.meta.url), 'utf8'));
  assert.equal(parseNewsArchive(value).records.length, value.records.length);
});
