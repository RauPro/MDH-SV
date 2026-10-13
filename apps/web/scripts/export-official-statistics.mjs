import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { mkdirSync, readFileSync, readdirSync, writeFileSync } from 'node:fs';
import { pathToFileURL } from 'node:url';
import { parseOfficialStatistics } from '../../../shared/official-statistics.mjs';

const canonical = new URL('../../../data/official-statistics/homicides.json', import.meta.url);
const destination = new URL('../public/statistics/', import.meta.url);
const sha256 = bytes => createHash('sha256').update(bytes).digest('hex');
const json = value => Buffer.from(`${JSON.stringify(value, null, 2)}\n`);

// Quotes alone do not prevent spreadsheet formulas when a text field is opened as CSV.
export function csvCell(value) {
  let text = value === null ? '' : String(value);
  if (/^\s*[=+@-]|^[\t\r\n]/u.test(text)) text = `'${text}`;
  return `"${text.replaceAll('"', '""')}"`;
}

/** Deterministic, independent exports. Monthly observations remain in the JSON. */
export function buildOfficialStatisticsExports(sourceBytes) {
  const data = parseOfficialStatistics(JSON.parse(sourceBytes.toString('utf8')));
  const sources = new Map(data.sources.map(source => [source.id, source]));
  const columns = ['year', 'territory_level', 'department', 'period_start', 'period_end',
    'completeness', 'indicator', 'unit', 'value', 'status', 'institution', 'source_title',
    'source_url', 'source_locator', 'source_retrieved_at', 'source_sha256', 'methodology', 'notes'];
  const rows = [];
  for (const period of [...data.periods].sort((a, b) => a.year - b.year)) {
    const source = sources.get(period.source_id);
    for (const row of [{ department: 'national', ...period.national }, ...period.departments]) {
      rows.push([period.year, row.department === 'national' ? 'national' : 'department', row.department,
        period.start_date, period.end_date, period.completeness, period.indicator, period.unit,
        row.value, row.status, source.institution, source.title, source.url, row.locator,
        source.retrieved_at, source.content_sha256, period.methodology, period.notes.join(' | ')]);
    }
  }
  const files = {
    'homicides.json': json(data),
    'homicides.csv': Buffer.from(`${[columns, ...rows].map(row => row.map(csvCell).join(',')).join('\n')}\n`),
  };
  const manifest = {
    schema_version: 1, dataset_id: data.dataset_id, as_of: data.as_of, generated_at: data.generated_at,
    source_sha256: sha256(sourceBytes), period_count: data.periods.length, csv_rows_count: rows.length,
    csv_scope: 'Una fila nacional y 14 departamentales por período. No sumar ambos ámbitos. Detalle mensual disponible en JSON. Los valores ausentes quedan vacíos y conservan status=not_available.',
    files: Object.fromEntries(Object.entries(files).map(([name, bytes]) => [name, { sha256: sha256(bytes), bytes: bytes.length }])),
  };
  return { ...files, 'manifest.json': json(manifest) };
}

export function checkOfficialStatisticsExports(source = canonical, directory = destination) {
  const expected = buildOfficialStatisticsExports(readFileSync(source));
  assert.deepEqual(readdirSync(directory).sort(), Object.keys(expected).sort(), 'Unexpected official statistics file');
  for (const [name, bytes] of Object.entries(expected)) {
    assert.ok(bytes.equals(readFileSync(new URL(name, directory))), `Regenerate official statistics export: ${name}`);
  }
  return JSON.parse(expected['manifest.json']);
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  assert.ok(process.argv.slice(2).every(arg => arg === '--check'), 'Usage: export-official-statistics.mjs [--check]');
  if (process.argv.includes('--check')) {
    const manifest = checkOfficialStatisticsExports();
    console.log(`Verified ${manifest.period_count} official statistical periods and ${manifest.csv_rows_count} exported rows.`);
  } else {
    const files = buildOfficialStatisticsExports(readFileSync(canonical));
    mkdirSync(destination, { recursive: true });
    for (const [name, bytes] of Object.entries(files)) writeFileSync(new URL(name, destination), bytes);
    console.log('Exported official statistics JSON, CSV and integrity manifest.');
  }
}
