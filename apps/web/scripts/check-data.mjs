import { readFileSync, readdirSync } from 'node:fs';
import { createHash } from 'node:crypto';
import assert from 'node:assert/strict';

const source = new URL('../../../data/reviewed/publications.json', import.meta.url);
const publicDir = new URL('../public/data/', import.meta.url);
const hash = data => createHash('sha256').update(data).digest('hex');
const manifest = JSON.parse(readFileSync(new URL('manifest.json', publicDir)));
assert.equal(hash(readFileSync(source)), manifest.source_sha256, 'Regenerate public data after changing source');
const allowed = ['catalog.json', 'records.csv', 'records.geojson', 'records.parquet'];
assert.deepEqual(Object.keys(manifest.files).sort(), [...allowed].sort());
assert.deepEqual(readdirSync(publicDir).sort(), [...allowed, 'manifest.json'].sort(), 'Unexpected public data file');
for (const name of allowed) {
  const bytes = readFileSync(new URL(name, publicDir));
  assert.equal(hash(bytes), manifest.files[name].sha256, `Stale/tampered export: ${name}`);
  assert.equal(bytes.length, manifest.files[name].bytes);
}
const catalog = JSON.parse(readFileSync(new URL('catalog.json', publicDir)));
const original = JSON.parse(readFileSync(source));
assert.equal(catalog.records.length, manifest.records_count);
assert.deepEqual([...catalog.records].sort((a,b)=>a.id.localeCompare(b.id)), [...original.records].sort((a,b)=>a.id.localeCompare(b.id)));
for (const row of catalog.records) {
  assert.ok(['incident', 'aggregate', 'publication'].includes(row.record_type));
  assert.equal(row.review_status, 'source_checked');
  assert.equal(row.human_reviewed, false);
  assert.equal(row.geometry, null);
  assert.ok(row.source_locator && row.source_record_key);
  assert.ok(!('victims' in row) && !('perpetrators' in row));
}
console.log(`Validated ${catalog.records.length} public records and all download checksums.`);
