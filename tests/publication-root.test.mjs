import test from 'node:test';
import assert from 'node:assert/strict';
import { cpSync, mkdirSync, mkdtempSync, readFileSync, realpathSync, renameSync, rmSync, symlinkSync, writeFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { tmpdir } from 'node:os';
import { fileURLToPath } from 'node:url';
import { spawnSync } from 'node:child_process';
import { publicDataRoot } from '../apps/web/scripts/public-data-root.mjs';

const repository = fileURLToPath(new URL('../', import.meta.url));
const commands = ['sync-news-publication', 'sync-news-processing', 'check-data'];

function checkout(t) {
  const parent = mkdtempSync(join(realpathSync(tmpdir()), 'mdh-publication-root-'));
  t.after(() => rmSync(parent, { recursive: true, force: true }));
  const root = join(parent, 'publication');
  mkdirSync(root);
  cpSync(join(repository, 'data'), join(root, 'data'), { recursive: true });
  mkdirSync(join(root, 'apps/web'), { recursive: true });
  cpSync(join(repository, 'apps/web/public'), join(root, 'apps/web/public'), { recursive: true });
  return root;
}

function run(command, root) {
  return spawnSync(process.execPath, [join(repository, 'apps/web/scripts', `${command}.mjs`), '--root', root],
    { cwd: root, encoding: 'utf8' });
}

test('approved validators and reconcilers read external data without executing external scripts or imports', t => {
  const root = checkout(t);
  const traps = [...commands.map(command => `apps/web/scripts/${command}.mjs`),
    'apps/web/scripts/export-official-statistics.mjs', 'shared/news-dossier.mjs',
    'shared/news-processing.mjs', 'apps/web/scripts/public-data-root.mjs'];
  for (const name of traps) {
    const path = join(root, name);
    mkdirSync(dirname(path), { recursive: true });
    writeFileSync(path, 'throw new Error("UNTRUSTED_MAIN_CODE_EXECUTED");\n');
  }
  for (const command of commands) {
    const result = run(command, root);
    assert.equal(result.status, 0, result.stderr || result.stdout);
    assert.doesNotMatch(result.stderr + result.stdout, /UNTRUSTED_MAIN_CODE_EXECUTED/);
  }
});

test('processing reconciliation writes only the explicitly supplied data root', t => {
  const root = checkout(t);
  const name = 'apps/web/public/archive/processing.json';
  const original = readFileSync(join(repository, name));
  const archive = join(root, 'apps/web/public/archive/news.json');
  const value = JSON.parse(readFileSync(archive));
  value.records = [];
  writeFileSync(archive, JSON.stringify(value));
  const result = run('sync-news-processing', root);
  assert.equal(result.status, 0, result.stderr);
  assert.deepEqual(JSON.parse(readFileSync(join(root, name))).entries, []);
  assert.deepEqual(readFileSync(join(repository, name)), original);
});

test('check-data checks every class of external input, including independent statistics exports', t => {
  const root = checkout(t);
  for (const name of ['data/reviewed/publications.json', 'data/reviewed/news_dossiers.json',
    'data/automated/news_dossiers.json', 'data/selection/news.json', 'data/selection/news_exceptions.json',
    'data/history/co-latino.json', 'data/official-statistics/homicides.json',
    'apps/web/public/data/catalog.json', 'apps/web/public/archive/news.json',
    'apps/web/public/archive/dossiers.json', 'apps/web/public/archive/processing.json',
    'apps/web/public/archive/locations.json', 'apps/web/public/statistics/homicides.json']) {
    const target = join(root, name);
    const before = readFileSync(target);
    writeFileSync(target, 'not JSON');
    const result = run('check-data', root);
    assert.notEqual(result.status, 0, `Ignored external input: ${name}`);
    writeFileSync(target, before);
  }
});

test('every CLI rejects a symlinked root or root ancestor', t => {
  const root = checkout(t);
  const parent = dirname(root);
  const alias = join(parent, 'alias');
  symlinkSync(root, alias, 'dir');
  const outerAlias = join(parent, 'outer-alias');
  symlinkSync(parent, outerAlias, 'dir');
  for (const path of [alias, join(outerAlias, 'publication')]) {
    for (const command of commands) {
      const result = run(command, path);
      assert.notEqual(result.status, 0, command);
      assert.match(result.stderr, /Unsafe public data path/);
    }
  }
});

test('reconcilers reject linked leaf inputs or ancestors before changing any output', t => {
  const root = checkout(t);
  const archive = join(root, 'apps/web/public/archive');
  const before = readFileSync(join(archive, 'processing.json'));
  const target = join(archive, 'news.json');
  renameSync(target, `${target}.real`);
  symlinkSync(`${target}.real`, target);
  for (const command of commands) {
    const result = run(command, root);
    assert.notEqual(result.status, 0, command);
    assert.match(result.stderr, /Unsafe public data path/);
  }
  rmSync(target);
  renameSync(`${target}.real`, target);
  renameSync(archive, `${archive}.real`);
  symlinkSync(`${archive}.real`, archive, 'dir');
  for (const command of commands) {
    const result = run(command, root);
    assert.notEqual(result.status, 0, command);
    assert.match(result.stderr, /Unsafe public data path/);
  }
  assert.deepEqual(readFileSync(join(archive, 'processing.json')), before);
});

test('data root rejects unknown CLI arguments and traversal outside the checkout', t => {
  const root = checkout(t);
  assert.throws(() => publicDataRoot(['--root', root, '--other']), /Usage/);
  assert.throws(() => publicDataRoot(['--root']), /Usage/);
  const data = publicDataRoot(['--root', root]);
  assert.throws(() => data.path('../elsewhere.json'), /within its root/);
  assert.throws(() => data.path('/etc/passwd'), /within its root/);
});
