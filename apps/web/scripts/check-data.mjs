import { readFileSync, readdirSync } from 'node:fs';
import { createHash } from 'node:crypto';
import assert from 'node:assert/strict';
import { assertSelectionProvenance } from './selection-provenance.mjs';
import { assertSelectedNewsArchive, isSelectedNewsReference } from '../../../shared/news-selection.mjs';
import { NEWS_DOSSIER_PROTECTED_TITLE, parsePublishedNewsDossiers, validateDossierBindings } from '../../../shared/news-dossier.mjs';
import { parsePublishedNewsLocations, validateLocationBindings } from '../../../shared/news-location.mjs';
import { checkOfficialStatisticsExports } from './export-official-statistics.mjs';
import { parseNewsProcessing, validateProcessingBindings } from '../../../shared/news-processing.mjs';
import { parseNewsExceptions } from '../../../shared/news-exceptions.mjs';
import { publicDataRoot } from './public-data-root.mjs';

const data = publicDataRoot();
const source = data.path('data/reviewed/publications.json');
const publicDir = data.path('apps/web/public/data', 'directory');
const hash = data => createHash('sha256').update(data).digest('hex');
const manifest = JSON.parse(readFileSync(data.path('apps/web/public/data/manifest.json')));
assert.equal(hash(readFileSync(source)), manifest.source_sha256, 'Regenerate public data after changing source');
const allowed = ['catalog.json', 'records.csv', 'records.geojson', 'records.parquet'];
assert.deepEqual(Object.keys(manifest.files).sort(), [...allowed].sort());
assert.deepEqual(readdirSync(publicDir).sort(), [...allowed, 'manifest.json'].sort(), 'Unexpected public data file');
for (const name of allowed) {
  const bytes = readFileSync(data.path(`apps/web/public/data/${name}`));
  assert.equal(hash(bytes), manifest.files[name].sha256, `Stale/tampered export: ${name}`);
  assert.equal(bytes.length, manifest.files[name].bytes);
}
const catalog = JSON.parse(readFileSync(data.path('apps/web/public/data/catalog.json')));
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

const approvedNews = JSON.parse(readFileSync(data.path('data/reviewed/news_dossiers.json')));
const automatedNews = JSON.parse(readFileSync(data.path('data/automated/news_dossiers.json')));
const publicNews = JSON.parse(readFileSync(data.path('apps/web/public/archive/dossiers.json')));
const newsArchive = JSON.parse(readFileSync(data.path('apps/web/public/archive/news.json')));
const exceptions = parseNewsExceptions(JSON.parse(readFileSync(data.path('data/selection/news_exceptions.json'))));
const withdrawn = id => exceptions.get(id)?.withdrawn === true;
for (const reference of newsArchive.records) assert.ok(!withdrawn(reference.id), 'Withdrawn news must not remain in the public archive');
// Verify discovery coverage independently of classification or publication.
const candidateIds = new Set();
for (const id of ['co-latino', 'gatoencerrado', 'factum', 'el-faro', 'el-mundo', 'la-prensa-grafica', 'el-diario-de-hoy']) {
  const checkpoint = JSON.parse(readFileSync(data.path(`data/history/${id}.json`)));
  for (const item of checkpoint.records) if (item.published_date >= newsArchive.coverage.start_date && item.published_date <= newsArchive.coverage.end_date) candidateIds.add(item.id);
}
assert.equal(newsArchive.policy.selection_gate.candidates, candidateIds.size, 'Discovery denominator must match the candidate checkpoints');
assertSelectedNewsArchive(newsArchive);
const allApproved = parsePublishedNewsDossiers(approvedNews);
for (const dossier of allApproved.values()) assert.equal(dossier.human_reviewed, true, 'Reviewed source must contain only human decisions');
const automaticIndex = parsePublishedNewsDossiers(automatedNews);
for (const dossier of automaticIndex.values()) assert.equal(dossier.schema_version, 2, 'Automated source must contain only validated automatic publications');
const selectionManifest = JSON.parse(readFileSync(data.path('data/selection/news.json')));
assertSelectionProvenance(newsArchive, selectionManifest, allApproved);
// Human decisions replace automatic content, including a decision to withhold
// the reference from the public scope. Source stores remain separate.
const mergedIndex = new Map([...automaticIndex, ...allApproved].filter(([id]) => !withdrawn(id)));
const approvedIndex = validateDossierBindings(new Map([...mergedIndex].filter(([, dossier]) => dossier.classification.jurisdiction === 'SV' && dossier.classification.relevance === 'relevant')), newsArchive.records);
const publicIndex = validateDossierBindings(parsePublishedNewsDossiers(publicNews), newsArchive.records);
assert.deepEqual([...publicIndex].sort(), [...approvedIndex].sort(), 'Regenerate public news dossiers after changing reviewed or automated source');
for (const reference of newsArchive.records) assert.ok(isSelectedNewsReference(reference, allApproved.get(reference.id)), 'Human scope decisions must remove out-of-scope public references');
console.log(`Validated ${newsArchive.records.length} selected news references from ${newsArchive.policy.selection_gate.candidates} discovery candidates.`);
for (const dossier of publicIndex.values()) {
  assert.ok(!withdrawn(dossier.candidate_id), 'Withdrawn news must not retain a public dossier');
  if (dossier.schema_version === 2 && dossier.source_title === NEWS_DOSSIER_PROTECTED_TITLE) {
    const reference = newsArchive.records.find(row => row.id === dossier.candidate_id);
    assert.equal(reference.title, NEWS_DOSSIER_PROTECTED_TITLE, 'Protected dossier title must also be protected in the archive');
    assert.equal(reference.title_truncated, false, 'Protected title is a complete public placeholder');
  }
  assert.equal(hash(dossier.source_url), dossier.candidate_id, 'News dossier identity must match its source URL');
  if (dossier.human_reviewed) assert.ok(dossier.reviewed_at <= new Date().toISOString().slice(0, 10), 'News dossier review cannot be in the future');
}
console.log(`Validated ${publicIndex.size} public news dossiers with explicit publication provenance and source bindings.`);

const processing = JSON.parse(readFileSync(data.path('apps/web/public/archive/processing.json')));
const processingIndex = validateProcessingBindings(parseNewsProcessing(processing), newsArchive.records);
for (const entry of processingIndex.values()) {
  assert.equal(hash(entry.source_url), entry.candidate_id, 'Processing identity must match its public source URL');
}
console.log(`Validated ${processingIndex.size} processing statuses without exposing unreviewed fields.`);

const automaticLocations = JSON.parse(readFileSync(data.path('apps/web/public/archive/locations.json')));
const locationsIndex = validateLocationBindings(parsePublishedNewsLocations(automaticLocations), newsArchive.records);
for (const location of locationsIndex.values()) {
  assert.ok(!withdrawn(location.candidate_id), 'Withdrawn news must not retain a public location');
  assert.notEqual(publicIndex.get(location.candidate_id)?.fields.department.status, 'withheld', 'Protected department must not remain in location downloads');
  assert.equal(hash(location.source_url), location.candidate_id, 'Automatic location must match its source URL');
  assert.equal(location.human_reviewed, false);
  assert.equal(location.review_status, 'automated_unreviewed');
}
console.log(`Validated ${locationsIndex.size} unreviewed automatic news locations; no dossier approvals implied.`);

const statisticsDirectory = data.path('apps/web/public/statistics', 'directory');
// The exporter reads its own imports; validate every external data leaf first.
for (const name of readdirSync(statisticsDirectory)) data.path(`apps/web/public/statistics/${name}`);
const officialStatistics = checkOfficialStatisticsExports(
  data.path('data/official-statistics/homicides.json'), statisticsDirectory,
);
console.log(`Validated ${officialStatistics.period_count} separate official statistical periods, provenance and download checksums.`);
