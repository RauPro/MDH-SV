import { after, before, test } from 'node:test';
import assert from 'node:assert/strict';
import { Client } from '@modelcontextprotocol/sdk/client/index.js';
import { StreamableHTTPClientTransport } from '@modelcontextprotocol/sdk/client/streamableHttp.js';
import { createPublicServer } from '../server/dev.mjs';
import { ApiError, getStats } from '../server/public-data.mjs';
import { getOfficialStatistics, loadOfficialStatistics, parseOfficialStatisticsFilters } from '../server/official-statistics.mjs';
import { rest } from '../server/http.mjs';
import { officialStatisticsFixture } from './official-statistics-fixture.mjs';

let server, base;
before(async () => {
  server = createPublicServer();
  await new Promise((resolve, reject) => { server.once('error', reject); server.listen(0, '127.0.0.1', resolve); });
  base = `http://127.0.0.1:${server.address().port}`;
});
after(async () => { server.closeAllConnections(); await new Promise(resolve => server.close(resolve)); });
const errorWith = status => error => error instanceof ApiError && error.status === status;

test('official API returns 255 separate provenance-bound rows, never a sum across years or territories', () => {
  const fixture = officialStatisticsFixture();
  const documentaryBaseline = getStats();
  const result = getOfficialStatistics({}, fixture);
  assert.equal(result.data.length, 255);
  assert.equal(result.meta.returned_rows, 255);
  assert.equal(result.data.filter(row => row.territory_level === 'national').length, 17);
  assert.equal(result.data.filter(row => row.territory_level === 'department').length, 238);
  for (const row of result.data) {
    const period = fixture.periods.find(period => period.year === row.year);
    const source = fixture.sources.find(source => source.id === period.source_id);
    const observation = row.department === 'national' ? period.national : period.departments.find(item => item.department === row.department);
    assert.equal(row.value, observation.value);
    assert.equal(row.locator, observation.locator);
    assert.deepEqual(row.source, source);
    assert.equal(row.unit, period.unit);
    assert.equal(row.end_date, period.end_date);
  }
  assert.ok(!Object.hasOwn(result, 'total_homicides'));
  assert.match(result.meta.limitations, /no sumar instituciones, años, total nacional y departamentos/);
  assert.deepEqual(getStats(), documentaryBaseline);
});

test('official filters preserve unavailable department and year rows and distinguish a reported zero', () => {
  const fixture = officialStatisticsFixture();
  assert.equal(getOfficialStatistics({ year: 2010, department: 'Ahuachapán' }, fixture).data[0].value, 0);
  const unavailable = getOfficialStatistics({ year: 2012 }, fixture);
  assert.equal(unavailable.data.length, 15);
  assert.ok(unavailable.data.every(row => row.value === null && row.status === 'not_available'));
  const department = getOfficialStatistics({ year: 2026, department: 'Santa Ana' }, fixture).data[0];
  assert.equal(department.value, null);
  assert.equal(department.months, null);
  assert.equal(department.territory_level, 'department');
  const national = getOfficialStatistics({ year: 2026, department: 'national' }, fixture).data[0];
  assert.equal(national.completeness, 'partial');
  assert.equal(national.end_date, '2026-08-31');
  assert.equal(national.months[8].status, 'not_available');
  assert.equal(national.months[10].status, 'outside_cutoff');
});

test('official query validation rejects unknown, duplicated, noncanonical and future filters', () => {
  assert.deepEqual(parseOfficialStatisticsFilters(new URLSearchParams('year=2020&department=La+Uni%C3%B3n')), { year: 2020, department: 'La Unión' });
  for (const query of ['year=2020&year=2021', 'department=national&department=Santa+Ana', 'year=2020.0', 'year=2e3', 'year=02020', 'year=+2020',
    'year=', 'year=2009', 'q=test', 'limit=1', 'offset=0', '__proto__=test', 'department=unknown', 'department=San+Salvador+', 'department=Ahuachapan']) {
    assert.throws(() => parseOfficialStatisticsFilters(new URLSearchParams(query)), errorWith(400), query);
  }
  for (const query of [{ year: 2027 }, { year: '2020' }, { year: 2020.5 }, { department: 'national', private: true }]) {
    assert.throws(() => getOfficialStatistics(query, officialStatisticsFixture()), errorWith(400));
  }
});

test('official loader fails closed on unavailable, malformed, oversized or extra-field snapshots', async () => {
  for (const read of [
    () => { throw new Error('PRIVATE_FILE_PATH'); },
    () => Buffer.from('{'),
    () => Buffer.from(JSON.stringify({ ...officialStatisticsFixture(), private_model_response: 'PRIVATE_FIELD' })),
    () => Buffer.alloc(2_000_001),
  ]) {
    assert.throws(() => loadOfficialStatistics(read), error => errorWith(503)(error) && error.code === 'official_statistics_unavailable' && !/PRIVATE/.test(error.message));
  }
  assert.deepEqual(loadOfficialStatistics(() => Buffer.from(JSON.stringify(officialStatisticsFixture()))), officialStatisticsFixture());
  const handler = rest(() => getOfficialStatistics({}, { secret: 'PRIVATE_FIELD' }));
  const response = { statusCode: 0, headers: {}, setHeader(key, value) { this.headers[key] = value; }, end(value) { this.body = JSON.parse(value); } };
  await handler({ method: 'GET', url: '/api/v1/official-statistics' }, response);
  assert.equal(response.statusCode, 503);
  assert.equal(response.headers['Cache-Control'], 'no-store');
  assert.equal(response.body.error.code, 'official_statistics_unavailable');
  assert.ok(!JSON.stringify(response.body).includes('PRIVATE_FIELD'));
});

test('official REST serves the public snapshot anonymously with strict filters and read-only methods', async () => {
  const expected = getOfficialStatistics({ year: 2026, department: 'national' });
  const response = await fetch(`${base}/api/v1/official-statistics?year=2026&department=national`);
  assert.equal(response.status, 200);
  assert.equal(response.headers.get('access-control-allow-origin'), '*');
  assert.equal(response.headers.get('access-control-allow-credentials'), null);
  assert.deepEqual(await response.json(), expected);
  for (const query of ['year=2009', 'year=2027', 'department=unknown', 'year=2020&year=2021', 'q=homicidios', 'limit=10']) {
    assert.equal((await fetch(`${base}/api/v1/official-statistics?${query}`)).status, 400);
  }
  const head = await fetch(`${base}/api/v1/official-statistics`, { method: 'HEAD' });
  assert.equal(head.status, 200);
  assert.equal(await head.text(), '');
  assert.equal((await fetch(`${base}/api/v1/official-statistics`, { method: 'OPTIONS' })).status, 204);
  assert.equal((await fetch(`${base}/api/v1/official-statistics`, { method: 'POST', body: '{}' })).status, 405);
});

test('official OpenAPI and MCP distinguish aggregate statistics from the documentary counts', async () => {
  const spec = await (await fetch(`${base}/api/v1/openapi.json`)).json();
  const operation = spec.paths['/api/v1/official-statistics'].get;
  assert.deepEqual(operation.parameters.map(parameter => parameter.name), ['year', 'department']);
  assert.equal(operation.responses['503'].content['application/json'].schema.$ref, '#/components/schemas/Error');
  assert.ok(operation.parameters[1].schema.enum.includes('national'));
  assert.equal(operation.parameters[1].schema.enum.length, 15);
  assert.equal(spec.components.schemas.OfficialStatisticsSource.additionalProperties, false);
  assert.ok(spec.components.schemas.OfficialStatisticsSource.properties.format.enum.includes('xlsx'));
  assert.ok(spec.components.schemas.OfficialStatisticsRow.required.includes('territory_level'));
  const client = new Client({ name: 'official-statistics-test', version: '1.0.0' });
  await client.connect(new StreamableHTTPClientTransport(new URL(`${base}/mcp`)));
  try {
    const tools = await client.listTools();
    const tool = tools.tools.find(tool => tool.name === 'get_official_statistics');
    assert.equal(tool.annotations.readOnlyHint, true);
    assert.equal(tool.annotations.destructiveHint, false);
    assert.match(tool.description, /no casos individuales|No consulta casos individuales/);
    const result = await client.callTool({ name: 'get_official_statistics', arguments: { year: 2026, department: 'national' } });
    assert.equal(result.isError, undefined);
    assert.deepEqual(result.structuredContent, getOfficialStatistics({ year: 2026, department: 'national' }));
    assert.equal((await client.callTool({ name: 'get_official_statistics', arguments: { year: 2027 } })).isError, true);
    assert.equal((await client.callTool({ name: 'get_official_statistics', arguments: { q: 'victims' } })).isError, true);
    const counts = await client.callTool({ name: 'get_statistics', arguments: {} });
    assert.deepEqual(counts.structuredContent, getStats());
  } finally { await client.close(); }
});
