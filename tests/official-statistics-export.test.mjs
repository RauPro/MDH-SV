import test from 'node:test';
import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { pathToFileURL } from 'node:url';
import { officialStatisticsFixture } from './official-statistics-fixture.mjs';
import { buildOfficialStatisticsExports, checkOfficialStatisticsExports, csvCell } from '../apps/web/scripts/export-official-statistics.mjs';

test('independent exports preserve provenance, missing values and reported zero', () => {
  const data = officialStatisticsFixture();
  const bytes = Buffer.from(JSON.stringify(data));
  const exports = buildOfficialStatisticsExports(bytes);
  assert.deepEqual(JSON.parse(exports['homicides.json']), data);
  const lines = exports['homicides.csv'].toString().split('\n');
  assert.equal(lines.length, 257); // header + 17 × 15 observations + final newline
  assert.match(lines[1], /^"2010","national","national"/);
  assert.match(lines[2], /"department","Ahuachapán".*"0","available"/);
  assert.match(lines[3], /"department","Santa Ana".*"","not_available"/);
  assert.match(lines[1], /https:\/\/transparencia\.oj\.gob\.sv/);
  assert.match(lines[1], /Tabla sintética, fila de prueba/);
  const manifest = JSON.parse(exports['manifest.json']);
  assert.equal(manifest.csv_rows_count, 255);
  assert.equal(manifest.source_sha256, createHash('sha256').update(bytes).digest('hex'));
  for (const [name, digest] of Object.entries(manifest.files)) {
    assert.equal(digest.bytes, exports[name].length);
    assert.equal(digest.sha256, createHash('sha256').update(exports[name]).digest('hex'));
  }
  assert.deepEqual(buildOfficialStatisticsExports(bytes), exports);
});

test('CSV preserves quoting and neutralizes formulas in downloaded text', () => {
  assert.equal(csvCell('Informe "A", B\nC'), '"Informe ""A"", B\nC"');
  for (const value of ['=HYPERLINK("x")', '+1+1', '-1+1', '@SUM(A1)', '  =1+1', '\tformula', '\rformula']) {
    assert.ok(csvCell(value).startsWith('"\''));
  }
  assert.equal(csvCell(null), '""');
  assert.equal(csvCell(0), '"0"');
});

test('integrity check rejects modified bytes and unexpected public files', () => {
  const temp = mkdtempSync(join(tmpdir(), 'mdh-official-statistics-'));
  try {
    const source = join(temp, 'source.json');
    const data = Buffer.from(JSON.stringify(officialStatisticsFixture()));
    writeFileSync(source, data);
    const folder = new URL('./public/', pathToFileURL(`${temp}/`));
    // Keep canonical source outside the allowlisted publication directory.
    mkdirSync(folder);
    const exports = buildOfficialStatisticsExports(data);
    for (const [name, bytes] of Object.entries(exports)) writeFileSync(new URL(name, folder), bytes);
    assert.equal(checkOfficialStatisticsExports(source, folder).period_count, 17);
    writeFileSync(new URL('homicides.csv', folder), 'tampered');
    assert.throws(() => checkOfficialStatisticsExports(source, folder), /Regenerate/);
    writeFileSync(new URL('homicides.csv', folder), exports['homicides.csv']);
    writeFileSync(new URL('draft.json', folder), '{}');
    assert.throws(() => checkOfficialStatisticsExports(source, folder), /Unexpected/);
  } finally { rmSync(temp, { recursive: true, force: true }); }
});

test('committed statistics downloads exactly match the canonical validated snapshot', () => {
  assert.equal(checkOfficialStatisticsExports().period_count, 17);
});
