"""Release versioning and reproducible-build contract."""
import gzip
import hashlib
import importlib.util
import io
import json
import re
import subprocess
import tarfile
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def test_every_versioned_surface_agrees():
    root = json.loads((ROOT / 'package.json').read_text())['version']
    web = json.loads((ROOT / 'apps/web/package.json').read_text())['version']
    python = tomllib.loads((ROOT / 'pyproject.toml').read_text())['project']['version']
    code = re.search(r"CODE_VERSION = '([0-9.]+)'", (ROOT / 'server/version.mjs').read_text())
    assert code and len({root, web, python, code.group(1)}) == 1


SPEC = importlib.util.spec_from_file_location('release', ROOT / 'scripts/release.py')
release = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(release)


def git(root, *args):
    return subprocess.run(['git', '-C', str(root), *args], check=True,
                          capture_output=True, text=True).stdout


def make_repo(tmp_path):
    repo = tmp_path / 'repo'
    (repo / 'apps/web').mkdir(parents=True)
    (repo / 'server').mkdir()
    (repo / 'data/history').mkdir(parents=True)
    (repo / 'apps/web/public/archive').mkdir(parents=True)
    (repo / 'apps/web/public/data').mkdir(parents=True)
    files = {
        'package.json': json.dumps({'version': '2.1.0'}),
        'apps/web/package.json': json.dumps({'version': '2.1.0'}),
        'pyproject.toml': '[project]\nversion = "2.1.0"\n',
        'server/version.mjs': "export const CODE_VERSION = '2.1.0';\n",
        'CHANGELOG.md': ('# Registro de cambios\n\n## [Sin publicar]\n\n## [2.1.0] - 2026-10-31\n'
                         '\n### Añadido\n\n- Trabajo de la versión.\n'),
        '.gitignore': 'data/*.sqlite\n',
        'data/history/co-latino.json': json.dumps({'schema_version': 4, 'generated_at': '2026-10-31T00:00:00Z', 'records': []}),
        'data/secret.sqlite': b'\x00\x01not json',
        'apps/web/public/archive/news.json': json.dumps({'schema_version': 1, 'updated_at': '2026-10-30'}),
        'apps/web/public/data/readme.txt': 'plain text\n',
    }
    for path, content in files.items():
        target = repo / path
        target.parent.mkdir(parents=True, exist_ok=True)
        mode = 'wb' if isinstance(content, bytes) else 'w'
        with open(target, mode) as handle:
            handle.write(content)
    subprocess.run(['git', '-C', str(repo), 'init', '-q'], check=True)
    subprocess.run(['git', '-C', str(repo), 'config', 'user.email', 'test@example.com'], check=True)
    subprocess.run(['git', '-C', str(repo), 'config', 'user.name', 'Test'], check=True)
    subprocess.run(['git', '-C', str(repo), 'add', '-A'], check=True)
    subprocess.run(['git', '-C', str(repo), 'commit', '-qm', 'initial'], check=True)
    return repo


def build(repo, out, commit='HEAD'):
    return release.build(repo, 'v2.1.0', out, commit)


def test_two_builds_are_byte_identical(tmp_path):
    repo = make_repo(tmp_path)
    first, second = tmp_path / 'a', tmp_path / 'b'
    build(repo, first), build(repo, second)
    names = sorted(path.name for path in first.iterdir())
    assert names == sorted(path.name for path in second.iterdir())
    for name in names:
        assert (first / name).read_bytes() == (second / name).read_bytes()


def test_manifest_matches_recomputed_hashes(tmp_path):
    repo = make_repo(tmp_path)
    manifest = build(repo, tmp_path / 'out')
    for name, meta in manifest['artifacts'].items():
        blob = (tmp_path / 'out' / name).read_bytes()
        assert meta['bytes'] == len(blob)
        assert meta['sha256'] == hashlib.sha256(blob).hexdigest()
        assert meta['tar_sha256'] == hashlib.sha256(gzip.decompress(blob)).hexdigest()
    datasets = {entry['path']: entry for entry in manifest['datasets']}
    assert 'data/history/co-latino.json' in datasets
    assert datasets['data/history/co-latino.json']['schema_version'] == 4
    assert datasets['apps/web/public/data/readme.txt']['schema_version'] is None
    assert datasets['apps/web/public/archive/news.json']['generated_at'] == '2026-10-30'
    assert manifest['api_version'] == '1' and manifest['mcp_version'] == '2.1.0'
    assert manifest['deprecations'][0]['id'] == 'news-implicit-full-coverage'


def test_ignored_files_never_enter_the_data_tarball(tmp_path):
    repo = make_repo(tmp_path)
    build(repo, tmp_path / 'out')
    blob = (tmp_path / 'out' / 'mdh-sv-2.1.0-data.tar.gz').read_bytes()
    names = tarfile.open(fileobj=io.BytesIO(gzip.decompress(blob))).getnames()
    assert not any('secret.sqlite' in name for name in names)
    assert any(name.endswith('data/history/co-latino.json') for name in names)


def test_build_refusals(tmp_path):
    repo = make_repo(tmp_path)
    (repo / 'dirty.txt').write_text('dirty')
    with pytest.raises(release.ReleaseError, match='not clean'):
        build(repo, tmp_path / 'a')
    subprocess.run(['git', '-C', str(repo), 'add', '-A'], check=True)
    subprocess.run(['git', '-C', str(repo), 'commit', '-qm', 'dirty'], check=True)
    (repo / 'pyproject.toml').write_text('[project]\nversion = "9.9.9"\n')
    subprocess.run(['git', '-C', str(repo), 'commit', '-qam', 'mismatch'], check=True)
    with pytest.raises(release.ReleaseError, match='differ'):
        build(repo, tmp_path / 'b')
    subprocess.run(['git', '-C', str(repo), 'checkout', '-q', 'HEAD~1', '--', 'pyproject.toml'], check=True)
    subprocess.run(['git', '-C', str(repo), 'commit', '-qm', 'restore'], check=True)
    (repo / 'CHANGELOG.md').write_text('# Registro\n\n## [Sin publicar]\n')
    subprocess.run(['git', '-C', str(repo), 'commit', '-qam', 'no section'], check=True)
    with pytest.raises(release.ReleaseError, match='section'):
        build(repo, tmp_path / 'c')
    subprocess.run(['git', '-C', str(repo), 'tag', 'v2.1.0'], check=True)
    subprocess.run(['git', '-C', str(repo), 'commit', '--allow-empty', '-qm', 'later'], check=True)
    with pytest.raises(release.ReleaseError, match='HEAD is not'):
        release.build(repo, 'v2.1.0', tmp_path / 'd', None)


def test_verify_then_tamper_fails(tmp_path):
    repo = make_repo(tmp_path)
    out = tmp_path / 'out'
    build(repo, out)
    manifest_path = out / 'release-manifest.json'
    release.verify(repo, 'v2.1.0', manifest_path)
    manifest = json.loads(manifest_path.read_text())
    manifest['artifacts']['mdh-sv-2.1.0-source.tar.gz']['sha256'] = '0' * 64
    tampered = tmp_path / 'tampered.json'
    tampered.write_text(json.dumps(manifest, indent=2, sort_keys=True) + '\n')
    with pytest.raises(release.ReleaseError, match='mismatch'):
        release.verify(repo, 'v2.1.0', tampered)


def test_notes_prints_the_section(tmp_path):
    repo = make_repo(tmp_path)
    text = release.notes(repo, 'v2.1.0', 'HEAD')
    assert 'Trabajo de la versión.' in text


def test_build_refuses_an_output_inside_the_checkout(tmp_path):
    repo = make_repo(tmp_path)
    with pytest.raises(release.ReleaseError, match='inside the checkout'):
        build(repo, repo / 'dist-a')
    with pytest.raises(release.ReleaseError, match='inside the checkout'):
        release.build(repo, 'v2.1.0', repo, 'HEAD')


def test_workflow_outputs_and_assets_stay_outside_the_checkout(tmp_path):
    import yaml
    workflow = yaml.safe_load((ROOT / '.github/workflows/release.yml').read_text())
    runs = ' '.join(step.get('run', '') for step in workflow['jobs']['release']['steps'])
    outputs = re.findall(r'release\.py build --tag \S+ --output "?([^\s"]+)', runs)
    assert outputs and all(output.startswith('$RUNNER_TEMP') for output in outputs)
    assets = re.search(r'gh release create \S+ "?([^"\s]+)"?/\*', runs)
    assert assets and assets.group(1).startswith('$RUNNER_TEMP')


def test_changelog_references_are_bidirectional():
    text = (ROOT / 'CHANGELOG.md').read_text()
    used = set(re.findall(r'\[#(\d+)\](?!:)', text))
    defined = set(re.findall(r'^\[#(\d+)\]:', text, re.MULTILINE))
    assert used == defined
