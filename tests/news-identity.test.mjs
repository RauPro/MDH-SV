import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import test from 'node:test';
import { canonicalNewsSource, sourceIdentityMatches } from '../shared/news-identity.mjs';

test('browser source identity agrees with native SHA-256 over UTF-8 without normalizing the input', () => {
  for (const url of ['https://www.diariocolatino.com/fixture/', 'https://gatoencerrado.news/año-📰/', `https://www.revistafactum.com/${'a'.repeat(2000)}`]) {
    const digest = createHash('sha256').update(url).digest('hex');
    assert.equal(sourceIdentityMatches(digest, url), true);
    assert.equal(sourceIdentityMatches(digest, url + '/'), false);
  }
  assert.equal(sourceIdentityMatches('a'.repeat(64), '\ud800'), false);
  assert.equal(sourceIdentityMatches('a'.repeat(64), null), false);
});

test('canonical article identity excludes alternate authorities, tracking parameters and fragments', () => {
  const url = 'https://www.diariocolatino.com/fixture/';
  assert.equal(canonicalNewsSource(url).name, 'Diario Co Latino');
  for (const invalid of [url + '?utm_source=test', url + '#fragment', url.replace('https:', 'http:'),
    url.replace('.com/', '.com:443/'), url.replace('www.', 'unlisted.'), 'https://www.diariocolatino.com/']) {
    assert.equal(canonicalNewsSource(invalid), null);
  }
});
