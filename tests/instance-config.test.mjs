import assert from 'node:assert/strict';
import { spawn, spawnSync } from 'node:child_process';
import { once } from 'node:events';
import { mkdtempSync, readFileSync, rmSync, symlinkSync, writeFileSync } from 'node:fs';
import { createServer } from 'node:net';
import { tmpdir } from 'node:os';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import test from 'node:test';
import { parseInstanceConfig, publicInstanceConfig } from '../shared/instance-config.mjs';
import { loadInstanceConfig } from '../server/instance.mjs';

const root = fileURLToPath(new URL('../', import.meta.url));
const fixture = JSON.parse(readFileSync(new URL('./fixtures/instance-config-cases.json', import.meta.url), 'utf8'));
for (const example of fixture.cases) {
  test(`instance validator: ${example.name}`, () => {
    const value = { ...fixture.base, ...example.patch };
    for (const key of example.remove ?? []) delete value[key];
    const parse = () => parseInstanceConfig(example.raw ?? JSON.stringify(value));
    if (example.valid) assert.doesNotThrow(parse);
    else assert.throws(parse);
  });
}

function configFile(t, value = fixture.base) {
  const folder = mkdtempSync(join(tmpdir(), 'mdh-instance-'));
  t.after(() => rmSync(folder, { recursive: true, force: true }));
  const path = join(folder, 'instance.json');
  writeFileSync(path, JSON.stringify(value));
  return path;
}

function isolated(t, source) {
  const path = configFile(t);
  const result = spawnSync(process.execPath, ['--input-type=module', '-e', source], {
    cwd: root, env: { ...process.env, MDH_INSTANCE_FILE: path }, encoding: 'utf8', timeout: 10000,
  });
  assert.equal(result.status, 0, result.stderr);
  return JSON.parse(result.stdout);
}

test('the public projection excludes private repository, IDs and allowed hosts', () => {
  assert.deepEqual(Object.keys(publicInstanceConfig(fixture.base)).sort(), ['editorial_email', 'public_repository', 'public_url', 'reporting_responsible']);
});

test('the explicit instance loader rejects relative, missing, oversized and symlink inputs', t => {
  const path = configFile(t);
  assert.equal(loadInstanceConfig({ MDH_INSTANCE_FILE: path }).public_repository, fixture.base.public_repository);
  for (const value of ['', 'instance.json', `${path}.missing`]) {
    assert.throws(() => loadInstanceConfig({ MDH_INSTANCE_FILE: value }), /^Error: Invalid instance configuration\.$/);
  }
  const link = `${path}.link`;
  symlinkSync(path, link);
  assert.throws(() => loadInstanceConfig({ MDH_INSTANCE_FILE: link }));
  writeFileSync(path, ' '.repeat(32769));
  assert.throws(() => loadInstanceConfig({ MDH_INSTANCE_FILE: path }));
  writeFileSync(path, Buffer.from([0xff]));
  assert.throws(() => loadInstanceConfig({ MDH_INSTANCE_FILE: path }));
  writeFileSync(path, `\ufeff${JSON.stringify(fixture.base)}`);
  assert.throws(() => loadInstanceConfig({ MDH_INSTANCE_FILE: path }));
});

test('the default instance path remains bound to the code checkout when the current directory changes', t => {
  const path = configFile(t);
  const rootConfiguration = JSON.parse(readFileSync(new URL('../instance.json', import.meta.url), 'utf8'));
  const source = `import { loadInstanceConfig } from ${JSON.stringify(new URL('../server/instance.mjs', import.meta.url).href)}; console.log(JSON.stringify(loadInstanceConfig({})));`;
  const result = spawnSync(process.execPath, ['--input-type=module', '-e', source], {
    cwd: dirname(path), env: { ...process.env }, encoding: 'utf8', timeout: 10000,
  });
  assert.equal(result.status, 0, result.stderr);
  assert.equal(JSON.parse(result.stdout).public_repository, rootConfiguration.public_repository);
});

test('custom REST pagination and OpenAPI preserve the configured origin and all filters despite hostile headers', t => {
  const output = isolated(t, `
    import records from './api/v1/records.mjs';
    import news from './api/v1/news.mjs';
    import { openapi } from './server/openapi.mjs';
    import { searchRecords } from './server/public-data.mjs';
    const headers = {host:'attacker.invalid', 'x-forwarded-host':'other.invalid', 'x-forwarded-proto':'http'};
    const invoke = async handler => {
      const res={setHeader(){},end(body){this.body=JSON.parse(body)}};
      await handler({method:'GET',url:'/api/v1/records?limit=1&date_from=1900-01-01&date_to=2100-01-01',headers},res);
      return res.body.pagination.next;
    };
    const filters={q:'evidence',publisher:'Example Press',record_type:'incident',department:'San Salvador',category:'test',review_status:'source_checked',date_from:'2025-01-01',date_to:'2025-12-31',incident_date_from:'2025-01-01',incident_date_to:'2025-12-31',limit:1};
    const item={title:'Evidence',publisher:'Example Press',record_type:'incident',department:'San Salvador',category:'test',review_status:'source_checked',published_date:'2025-04-01',incident_date:'2025-03-01'};
    const synthetic=searchRecords(filters,{records:[{...item,id:'one'},{...item,id:'two'}]});
    console.log(JSON.stringify({records:await invoke(records),news:await invoke(news),synthetic:synthetic.pagination.next,filters,servers:openapi.servers}));
  `);
  for (const key of ['records', 'news', 'synthetic']) assert.equal(new URL(output[key]).origin, fixture.base.public_url);
  const next = new URL(output.synthetic);
  for (const [key, value] of Object.entries(output.filters)) assert.equal(next.searchParams.get(key), String(value));
  assert.equal(next.searchParams.get('offset'), '1');
  assert.deepEqual(output.servers, [{ url: fixture.base.public_url }]);
});

test('MCP accepts configured and trusted deployment hosts, rejecting upstream and forged metadata', t => {
  const output = isolated(t, `
    import { validMcpRequest } from './api/mcp.mjs';
    const prod={VERCEL:'1',VERCEL_URL:'preview.example.org'};
    console.log(JSON.stringify([
      validMcpRequest({host:'monitor.example.org',origin:'https://research.example.org'},prod),
      validMcpRequest({host:'preview.example.org'},prod),
      validMcpRequest({host:'mdh-sv.vercel.app'},prod),
      validMcpRequest({host:'evil.example.org','x-forwarded-host':'monitor.example.org'},prod),
      validMcpRequest({host:'127.0.0.1:3000'},prod),
      validMcpRequest({host:'127.0.0.1:3000',origin:'https://evil.example.org'},{}),
      validMcpRequest({host:'127.0.0.1:3000',origin:'http://localhost:5173'},{}),
      validMcpRequest({host:'user@evil.example.org'}, {VERCEL:'1',VERCEL_URL:'user@evil.example.org'}),
      validMcpRequest({host:'127.1'}, {}),
      validMcpRequest({host:'0x7f000001'}, {})
    ]));
  `);
  assert.deepEqual(output, [true, true, false, false, false, false, true, false, false, false]);
});

test('the local executable advertises its trusted custom port and pagination can be followed', async t => {
  const reservation = createServer();
  reservation.listen(0, '127.0.0.1');
  await once(reservation, 'listening');
  const port = reservation.address().port;
  await new Promise(resolve => reservation.close(resolve));
  const child = spawn(process.execPath, ['server/dev.mjs'], { cwd: root, env: { ...process.env, PORT: String(port), MDH_INSTANCE_FILE: configFile(t) }, stdio: ['ignore', 'pipe', 'pipe'] });
  t.after(async () => { if (child.exitCode === null && child.signalCode === null) { child.kill(); await once(child, 'exit'); } });
  await Promise.race([once(child.stdout, 'data'), once(child, 'exit').then(() => { throw new Error('Local server failed to start.'); })]);
  const origin = `http://127.0.0.1:${port}`;
  const first = await (await fetch(`${origin}/api/v1/records?limit=1&date_from=1900-01-01`, { headers: { host: 'attacker.invalid', 'x-forwarded-host': 'evil.invalid' } })).json();
  const next = new URL(first.pagination.next);
  assert.equal(next.origin, origin);
  assert.equal(next.searchParams.get('date_from'), '1900-01-01');
  const second = await (await fetch(next)).json();
  assert.equal(second.pagination.offset, 1);
  assert.notEqual(first.data[0].id, second.data[0].id);
  const specification = await (await fetch(`${origin}/api/v1/openapi.json`)).json();
  assert.deepEqual(specification.servers, [{ url: origin }]);
});
