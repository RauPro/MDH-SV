"""Deterministic release build, verification and notes; stdlib plus git only.

A release is reproducible: `build` twice from the same commit produces
byte-identical artifacts, and `verify` rebuilds them and compares every hash.
Run `.venv/bin/python scripts/release.py build --tag vX.Y.Z --output DIR`.
"""
import argparse
import gzip
import tomllib
import hashlib
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA_PATHS = ('data', 'apps/web/public/archive', 'apps/web/public/data', 'apps/web/public/statistics')
VERSION_PATHS = ('package.json', 'apps/web/package.json', 'pyproject.toml', 'server/version.mjs')
DEPRECATIONS = [
    {"id": "news-implicit-full-coverage", "announced": "2026-11-01",
     "sunset": "2027-02-01T00:00:00Z",
     "replacement": "coverage=full o GET /api/v1/news/coverage", "changelog": "2.1.0"},
]


class ReleaseError(Exception):
    """A refusal with an actionable message."""


def git(root, *args):
    return subprocess.run(['git', '-C', str(root), *args], check=True,
                          capture_output=True, text=True).stdout


def tag_version(tag):
    match = re.fullmatch(r'v(\d+\.\d+\.\d+)', tag)
    if not match:
        raise ReleaseError(f'tag must look like vX.Y.Z, got {tag!r}')
    return match.group(1)


def resolve_commit(root, tag, commit):
    if commit is not None:
        return git(root, 'rev-parse', f'{commit}^{{commit}}').strip()
    try:
        tagged = git(root, 'rev-parse', '--verify', f'{tag}^{{commit}}').strip()
    except subprocess.CalledProcessError:
        raise ReleaseError(f'tag {tag} does not exist; pass --commit or create it first')
    head = git(root, 'rev-parse', 'HEAD').strip()
    if tagged != head:
        raise ReleaseError(f'HEAD is not {tag}^{{commit}}; pass --commit to build from another commit')
    return tagged


def show(root, commit, path):
    result = subprocess.run(['git', '-C', str(root), 'show', f'{commit}:{path}'],
                            capture_output=True)
    if result.returncode != 0:
        raise ReleaseError(f'{path} is not tracked at {commit}')
    return result.stdout


def versions_at(root, commit):
    root_pkg = json.loads(show(root, commit, 'package.json'))['version']
    web_pkg = json.loads(show(root, commit, 'apps/web/package.json'))['version']
    pyproject = tomllib.loads(show(root, commit, 'pyproject.toml').decode())['project']['version']
    match = re.search(rb"CODE_VERSION = '([0-9.]+)'", show(root, commit, 'server/version.mjs'))
    if not match:
        raise ReleaseError('server/version.mjs has no CODE_VERSION')
    return {'root': root_pkg, 'web': web_pkg, 'pyproject': pyproject, 'code': match.group(1).decode()}


def preconditions(root, tag, commit):
    if git(root, 'status', '--porcelain').strip():
        raise ReleaseError('working tree is not clean; commit or stash first')
    resolved = resolve_commit(root, tag, commit)
    version = tag_version(tag)
    found = versions_at(root, resolved)
    wrong = {key: value for key, value in found.items() if value != version}
    if wrong:
        raise ReleaseError(f'versions at {resolved[:12]} differ from {version}: {wrong}')
    changelog = show(root, resolved, 'CHANGELOG.md').decode()
    if f'## [{version}]' not in changelog:
        raise ReleaseError(f'CHANGELOG.md at {resolved[:12]} has no ## [{version}] section')
    return resolved, version, changelog


def data_paths_at(root, commit):
    # git archive refuses a pathspec that matches nothing at the commit.
    return [path for path in DATA_PATHS
            if git(root, 'ls-tree', '--name-only', commit, '--', path).strip()]


def artifact(root, commit, name, paths):
    raw = subprocess.run(['git', '-C', str(root), 'archive', '--format=tar',
                          f'--prefix={name}/', commit, *paths],
                         check=True, capture_output=True).stdout
    tar_sha256 = hashlib.sha256(raw).hexdigest()
    buffer = tempfile.SpooledTemporaryFile()
    with gzip.GzipFile(filename='', mode='wb', fileobj=buffer, mtime=0) as stream:
        stream.write(raw)
    buffer.seek(0)
    blob = buffer.read()
    return blob, {'sha256': hashlib.sha256(blob).hexdigest(), 'bytes': len(blob), 'tar_sha256': tar_sha256}


def datasets(root, commit):
    paths = [path for path in git(root, 'ls-tree', '-r', '-z', '--name-only', commit, '--', *DATA_PATHS).split('\0') if path]
    entries = []
    for path in sorted(paths):
        blob = show(root, commit, path)
        schema_version = generated_at = None
        try:
            document = json.loads(blob)
        except (json.JSONDecodeError, UnicodeDecodeError):
            document = None
        if isinstance(document, dict):
            candidate = document.get('schema_version')
            if isinstance(candidate, int):
                schema_version = candidate
            generated_at = document.get('generated_at') or document.get('updated_at')
        entries.append({'path': path, 'sha256': hashlib.sha256(blob).hexdigest(),
                        'bytes': len(blob), 'schema_version': schema_version,
                        'generated_at': generated_at})
    return entries


def build(root, tag, output, commit):
    resolved, version, _ = preconditions(root, tag, commit)
    out = Path(output).resolve()
    root_resolved = Path(root).resolve()
    if out == root_resolved or root_resolved in out.parents:
        raise ReleaseError(f'output directory {out} is inside the checkout {root_resolved}; '
                           'the dirty tree would fail the next build or enter the artifacts')
    out.mkdir(parents=True, exist_ok=True)
    source, source_meta = artifact(root, resolved, f'mdh-sv-{version}', [])
    data, data_meta = artifact(root, resolved, f'mdh-sv-{version}-data', ['--', *data_paths_at(root, resolved)])
    files = {f'mdh-sv-{version}-source.tar.gz': (source, source_meta),
             f'mdh-sv-{version}-data.tar.gz': (data, data_meta)}
    written = {}
    for name, (blob, meta) in files.items():
        (out / name).write_bytes(blob)
        written[name] = meta
    commit_date = git(root, 'show', '-s', '--format=%cI', resolved).strip()
    manifest = {
        'version': version, 'tag': tag, 'commit': resolved, 'commit_date': commit_date,
        'artifacts': written,
        'datasets': datasets(root, resolved),
        'api_version': '1', 'mcp_version': versions_at(root, resolved)['code'],
        'deprecations': DEPRECATIONS,
    }
    (out / 'release-manifest.json').write_text(json.dumps(manifest, indent=2, sort_keys=True) + '\n')
    return manifest


def mismatches(expected, actual, prefix=''):
    for key in sorted(set(expected) | set(actual)):
        here = f'{prefix}{key}'
        if key not in expected or key not in actual:
            yield f'{here}: missing on one side'
        elif isinstance(expected.get(key), dict) and isinstance(actual.get(key), dict):
            yield from mismatches(expected[key], actual[key], prefix=f'{here}.')
        elif isinstance(expected.get(key), list) and isinstance(actual.get(key), list):
            for index, (left, right) in enumerate(zip(expected[key], actual[key])):
                if isinstance(left, dict) and isinstance(right, dict):
                    yield from mismatches(left, right, prefix=f'{here}[{index}].')
                elif left != right:
                    yield f'{here}[{index}]: {left!r} != {right!r}'
            if len(expected[key]) != len(actual[key]):
                yield f'{here}: length {len(expected[key])} != {len(actual[key])}'
        elif expected[key] != actual[key]:
            yield f'{here}: {expected[key]!r} != {actual[key]!r}'


def verify(root, tag, manifest_path, commit=None):
    manifest = json.loads(Path(manifest_path).read_text())
    with tempfile.TemporaryDirectory() as temporary:
        rebuilt = build(root, tag, temporary, commit or manifest['commit'])
    errors = list(mismatches(manifest, rebuilt))
    artifact_dir = Path(manifest_path).parent
    for name, expected in manifest['artifacts'].items():
        target = artifact_dir / name
        if not target.exists():
            errors.append(f'{name}: artifact file missing next to the manifest')
            continue
        blob = target.read_bytes()
        actual = {'sha256': hashlib.sha256(blob).hexdigest(), 'bytes': len(blob)}
        try:
            actual['tar_sha256'] = hashlib.sha256(gzip.decompress(blob)).hexdigest()
        except Exception:
            actual['tar_sha256'] = 'unreadable'
        errors += list(mismatches(expected, actual, prefix=f'{name}.'))
    for error in errors:
        print(f'mismatch: {error}', file=sys.stderr)
    if errors:
        raise ReleaseError(f'{len(errors)} mismatch(es) between manifest and rebuild')
    print(f'verified {manifest["tag"]} at {manifest["commit"][:12]}')


def notes(root, tag, commit):
    version = tag_version(tag)
    ref = commit if commit else tag
    try:
        changelog = show(root, f'{ref}', 'CHANGELOG.md').decode()
    except ReleaseError:
        changelog = (root / 'CHANGELOG.md').read_text()
    match = re.search(rf'^## \[{re.escape(version)}\][^\n]*\n(.*?)(?=^## \[|\Z)',
                      changelog, re.MULTILINE | re.DOTALL)
    if not match:
        raise ReleaseError(f'CHANGELOG.md has no ## [{version}] section')
    return match.group(1).strip()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=ROOT)
    commands = parser.add_subparsers(dest='command', required=True)
    for name in ('build', 'verify', 'notes'):
        sub = commands.add_parser(name)
        sub.add_argument('--tag', required=True)
        sub.add_argument('--commit')
        if name == 'build':
            sub.add_argument('--output', required=True)
        if name == 'verify':
            sub.add_argument('--manifest', required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        if args.command == 'build':
            build(args.root, args.tag, args.output, args.commit)
        elif args.command == 'verify':
            verify(args.root, args.tag, args.manifest, args.commit)
        else:
            print(notes(args.root, args.tag, args.commit))
    except ReleaseError as error:
        print(f'release: {error}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
