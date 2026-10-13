import { readFileSync, readdirSync } from 'node:fs';
import { createHash } from 'node:crypto';
import assert from 'node:assert/strict';
import { assertSelectionProvenance } from './selection-provenance.mjs';
import { assertSelectedNewsArchive, isSelectedNewsReference } from '../../../shared/news-selection.mjs';
import { parsePublishedNewsDossiers, validateDossierBindings } from '../../../shared/news-dossier.mjs';
import { parsePublishedNewsLocations, validateLocationBindings } from '../../../shared/news-location.mjs';
import { checkOfficialStatisticsExports } from './export-official-statistics.mjs';

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
  assert.ok(['source_checked','editorially_reviewed'].includes(row.review_status));
  assert.equal(row.human_reviewed, row.review_status === 'editorially_reviewed');
  if (row.human_reviewed) assert.ok(row.reviewed_by && row.reviewed_at && row.review_notes?.length >= 20);
  if (row.origin_candidate_id) assert.equal(row.human_reviewed, true);
  assert.equal(row.geometry, null);
  assert.ok(row.source_locator && row.source_record_key);
  assert.ok(!('victims' in row) && !('perpetrators' in row));
}
console.log(`Validated ${catalog.records.length} public records and all download checksums.`);

const approvedNews = JSON.parse(readFileSync(new URL('../../../data/reviewed/news_dossiers.json', import.meta.url)));
const publicNews = JSON.parse(readFileSync(new URL('../public/archive/dossiers.json', import.meta.url)));
const newsArchive = JSON.parse(readFileSync(new URL('../public/archive/news.json', import.meta.url)));
// Verify discovery coverage independently of classification or publication.
const candidateIds = new Set();
for (const id of ['co-latino', 'gatoencerrado', 'factum', 'el-faro', 'el-mundo', 'la-prensa-grafica', 'el-diario-de-hoy']) {
  const checkpoint = JSON.parse(readFileSync(new URL(`../../../data/history/${id}.json`, import.meta.url)));
  for (const item of checkpoint.records) if (item.published_date >= newsArchive.coverage.start_date && item.published_date <= newsArchive.coverage.end_date) candidateIds.add(item.id);
}
assert.equal(newsArchive.policy.selection_gate.candidates, candidateIds.size, 'Discovery denominator must match the candidate checkpoints');
assertSelectedNewsArchive(newsArchive);
const allApproved = parsePublishedNewsDossiers(approvedNews);
const selectionManifest = JSON.parse(readFileSync(new URL('../../../data/selection/news.json', import.meta.url)));
assertSelectionProvenance(newsArchive, selectionManifest, allApproved);
const approvedIndex = validateDossierBindings(new Map([...allApproved].filter(([, dossier]) => dossier.classification.jurisdiction === 'SV' && dossier.classification.relevance === 'relevant')), newsArchive.records);
const publicIndex = validateDossierBindings(parsePublishedNewsDossiers(publicNews), newsArchive.records);
assert.deepEqual([...publicIndex].sort(), [...approvedIndex].sort(), 'Regenerate public news dossiers after changing reviewed source');
for (const reference of newsArchive.records) assert.ok(isSelectedNewsReference(reference, allApproved.get(reference.id)), 'Human scope decisions must remove out-of-scope public references');
console.log(`Validated ${newsArchive.records.length} selected news references from ${newsArchive.policy.selection_gate.candidates} discovery candidates.`);
for (const dossier of publicIndex.values()) {
  assert.equal(hash(dossier.source_url), dossier.candidate_id, 'News dossier identity must match its source URL');
  assert.ok(dossier.reviewed_at <= new Date().toISOString().slice(0, 10), 'News dossier review cannot be in the future');
}
console.log(`Validated ${publicIndex.size} human-reviewed news dossiers and bibliography bindings.`);

const automaticLocations = JSON.parse(readFileSync(new URL('../public/archive/locations.json', import.meta.url)));
const locationsIndex = validateLocationBindings(parsePublishedNewsLocations(automaticLocations), newsArchive.records);
for (const location of locationsIndex.values()) {
  assert.equal(hash(location.source_url), location.candidate_id, 'Automatic location must match its source URL');
  assert.equal(location.human_reviewed, false);
  assert.equal(location.review_status, 'automated_unreviewed');
}
console.log(`Validated ${locationsIndex.size} unreviewed automatic news locations; no dossier approvals implied.`);

const officialStatistics = checkOfficialStatisticsExports();
console.log(`Validated ${officialStatistics.period_count} separate official statistical periods, provenance and download checksums.`);
