import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { OFFICIAL_STATISTICS_DEPARTMENTS, parseOfficialStatistics } from '../shared/official-statistics.mjs';
import { officialStatisticsFixture } from './official-statistics-fixture.mjs';

test('official snapshot preserves zero, null, source provenance and the partial year without mutating input', () => {
  const fixture = officialStatisticsFixture();
  const parsed = parseOfficialStatistics(fixture);
  assert.deepEqual(parsed, fixture);
  assert.notEqual(parsed, fixture);
  assert.equal(parsed.periods[0].departments[0].value, 0);
  assert.equal(parsed.periods[0].departments[1].value, null);
  assert.equal(parsed.periods[2].completeness, 'unavailable');
  assert.equal(parsed.periods.at(-1).end_date, '2026-08-31');
  assert.equal(parsed.periods.at(-1).months[8].status, 'not_available');
  assert.equal(parsed.periods.at(-1).months[10].status, 'outside_cutoff');
  assert.equal(new Set(OFFICIAL_STATISTICS_DEPARTMENTS).size, 14);
});

test('the canonical institutional dataset validates without correcting or aggregating its figures', () => {
  const canonical = JSON.parse(readFileSync(new URL('../data/official-statistics/homicides.json', import.meta.url)));
  assert.deepEqual(parseOfficialStatistics(canonical), canonical);
});

test('the verified UNODC workbook is accepted as a separate official source', () => {
  const fixture = officialStatisticsFixture();
  Object.assign(fixture.sources[0], { format: 'xlsx', url: 'https://data.unodc.org/sites/dataportal.unodc.org/files/2026-07/data_cts_intentional_homicide.xlsx' });
  assert.equal(parseOfficialStatistics(fixture).sources[0].format, 'xlsx');
});

test('complete departmental and monthly breakdowns must agree with their own published total', () => {
  const departments = officialStatisticsFixture();
  departments.periods[0].departments.forEach(row => Object.assign(row, { value: 1, status: 'available', locator: 'Synthetic department table' }));
  assert.throws(() => parseOfficialStatistics(departments), /Estadísticas oficiales inválidas/);
  const months = officialStatisticsFixture();
  months.periods.at(-1).national.value = 9;
  assert.throws(() => parseOfficialStatistics(months), /Estadísticas oficiales inválidas/);
});

test('consistent zero breakdowns remain zero while incomplete breakdowns are not imputed', () => {
  const zeros = officialStatisticsFixture();
  const year = zeros.periods.find(period => period.year === 2022);
  year.national.value = 0;
  year.departments.forEach(row => Object.assign(row, { value: 0, status: 'available', locator: 'Synthetic department table' }));
  year.months.forEach(row => { row.value = 0; });
  assert.deepEqual(parseOfficialStatistics(zeros), zeros);
  const incomplete = officialStatisticsFixture();
  incomplete.periods.at(-1).national.value = 9;
  Object.assign(incomplete.periods.at(-1).months[0], { value: null, status: 'not_available', locator: null });
  assert.deepEqual(parseOfficialStatistics(incomplete), incomplete);
});

test('known components in an incomplete breakdown cannot exceed their own national total', () => {
  const singleDepartment = officialStatisticsFixture();
  singleDepartment.periods.at(-1).departments[0].value = 9;
  assert.throws(() => parseOfficialStatistics(singleDepartment), /Estadísticas oficiales inválidas/);
  const departments = officialStatisticsFixture();
  Object.assign(departments.periods.at(-1).departments[0], { value: 6, status: 'available', locator: 'Synthetic department table' });
  Object.assign(departments.periods.at(-1).departments[1], { value: 3, status: 'available', locator: 'Synthetic department table' });
  assert.throws(() => parseOfficialStatistics(departments), /Estadísticas oficiales inválidas/);
  const months = officialStatisticsFixture();
  months.periods.at(-1).national.value = 6;
  Object.assign(months.periods.at(-1).months[0], { value: null, status: 'not_available', locator: null });
  assert.throws(() => parseOfficialStatistics(months), /Estadísticas oficiales inválidas/);
});

test('a source with available figures must have been read on or after its period ends in El Salvador', () => {
  const fixture = officialStatisticsFixture();
  fixture.sources[0].retrieved_at = '2026-08-30T18:00:00Z';
  assert.throws(() => parseOfficialStatistics(fixture), /Estadísticas oficiales inválidas/);
  // UTC has already crossed midnight, but the Salvadoran civil date has not.
  fixture.sources[0].retrieved_at = '2026-08-31T05:59:59Z';
  assert.throws(() => parseOfficialStatistics(fixture), /Estadísticas oficiales inválidas/);
  fixture.sources[0].retrieved_at = '2026-08-31T06:00:00Z';
  assert.deepEqual(parseOfficialStatistics(fixture), fixture);
});

test('an entirely unavailable period does not claim that an older consulted source covers its end date', () => {
  const fixture = officialStatisticsFixture();
  fixture.sources.push({ ...fixture.sources[0], id: 'earlier-source', retrieved_at: '2011-01-02T18:00:00Z' });
  fixture.periods.find(period => period.year === 2012).source_id = 'earlier-source';
  assert.deepEqual(parseOfficialStatistics(fixture), fixture);
});

const invalid = [
  ['unknown top-level field', v => { v.private_output = 'unexpected'; }],
  ['unsupported version', v => { v.schema_version = 2; }],
  ['wrong dataset', v => { v.dataset_id = 'news'; }],
  ['wrong start year', v => { v.coverage_start_year = 2011; }],
  ['nonexistent cutoff', v => { v.as_of = '2026-02-30'; }],
  ['future generation', v => { v.generated_at = '2999-01-01T00:00:00Z'; }],
  ['missing year', v => { v.periods.pop(); }],
  ['duplicate year', v => { v.periods[1] = structuredClone(v.periods[0]); }],
  ['non-integer year', v => { v.periods[0].year = 2010.5; }],
  ['period crosses years', v => { v.periods[0].end_date = '2011-01-01'; }],
  ['incomplete full calendar', v => { v.periods[0].end_date = '2010-11-30'; }],
  ['partial period beyond cutoff', v => { v.periods.at(-1).end_date = '2026-11-30'; }],
  ['unknown source binding', v => { v.periods[0].source_id = 'missing'; }],
  ['duplicate source', v => { v.sources.push(structuredClone(v.sources[0])); }],
  ['unknown source field', v => { v.sources[0].raw_document = 'unexpected'; }],
  ['source read after generated', v => { v.sources[0].retrieved_at = '2026-10-12T21:00:00Z'; }],
  ['invalid source hash', v => { v.sources[0].content_sha256 = 'pending'; }],
  ['untrusted source URL', v => { v.sources[0].url = 'https://example.com/statistics.pdf'; }],
  ['deceptive source host', v => { v.sources[0].url = 'https://fiscalia.gob.sv.attacker.example/statistics'; }],
  ['non-HTTPS source', v => { v.sources[0].url = 'http://www.fiscalia.gob.sv/statistics'; }],
  ['source credentials', v => { v.sources[0].url = 'https://user:secret@www.fiscalia.gob.sv/statistics'; }],
  ['nonstandard port', v => { v.sources[0].url = 'https://www.fiscalia.gob.sv:1234/statistics'; }],
  ['unsupported source format', v => { v.sources[0].format = 'csv'; }],
  ['unknown metric', v => { v.periods[0].indicator = 'news_articles'; }],
  ['ambiguous unit', v => { v.periods[0].unit = 'cases'; }],
  ['unknown row field', v => { v.periods[0].national.name = 'unexpected'; }],
  ['null presented as available', v => { v.periods[0].national.value = null; }],
  ['zero presented as missing', v => { v.periods[0].departments[1].value = 0; }],
  ['missing locator', v => { v.periods[0].national.locator = null; }],
  ['invented locator for missing cell', v => { v.periods[0].departments[1].locator = 'unknown'; }],
  ['textual number', v => { v.periods[0].national.value = '12'; }],
  ['negative value', v => { v.periods[0].national.value = -1; }],
  ['fractional value', v => { v.periods[0].national.value = 1.5; }],
  ['unsafe integer', v => { v.periods[0].national.value = Number.MAX_SAFE_INTEGER + 1; }],
  ['unavailable period with value', v => { v.periods[2].national = { value: 1, status: 'available', locator: 'Test' }; }],
  ['missing department', v => { v.periods[0].departments.pop(); }],
  ['duplicate department', v => { v.periods[0].departments[1].department = v.periods[0].departments[0].department; }],
  ['noncanonical department', v => { v.periods[0].departments[0].department = 'Ahuachapan'; }],
  ['missing month', v => { v.periods.at(-1).months.pop(); }],
  ['duplicate month', v => { v.periods.at(-1).months[1].month = 1; }],
  ['past month called future', v => { v.periods.at(-1).months[8].status = 'outside_cutoff'; }],
  ['future month called unpublished', v => { v.periods.at(-1).months[10].status = 'not_available'; }],
  ['available month outside reported period', v => { v.periods.at(-1).months[8] = { month: 9, value: 1, status: 'available', locator: 'Test' }; }],
];
for (const [name, mutate] of invalid) test(`official parser rejects ${name}`, () => {
  const fixture = officialStatisticsFixture();
  mutate(fixture);
  assert.throws(() => parseOfficialStatistics(fixture), /Estadísticas oficiales inválidas/);
});
