"""Real rendering, replacement failures and deterministic export repair."""
import hashlib
from pathlib import Path

import pyarrow.parquet as pq
import pytest
from mdh_pipeline import publication
from mdh_pipeline.file_outputs import FileOutputConflict
from mdh_pipeline.publication import (
    CATALOG,
    CATALOG_EXPORT_NAMES,
    PUBLIC,
    build_catalog_exports,
    export_catalog,
)


def old_exports(tmp_path):
    source = tmp_path / 'reviewed.json'
    source.write_bytes(CATALOG.read_bytes())
    destination = tmp_path / 'public'
    destination.mkdir()
    previous = {}
    for name in CATALOG_EXPORT_NAMES:
        path = destination / name
        previous[path] = ('previous ' + name).encode()
        path.write_bytes(previous[path])
    return source, destination, previous


def test_pure_render_preserves_existing_export_bytes_and_hashes_the_exact_input():
    raw = CATALOG.read_bytes()
    manifest, outputs = build_catalog_exports(raw)
    assert tuple(outputs) == CATALOG_EXPORT_NAMES
    assert manifest['source_sha256'] == hashlib.sha256(raw).hexdigest()
    for name, content in outputs.items():
        assert content == (PUBLIC / name).read_bytes(), name
    other, padded = build_catalog_exports(raw + b'\n')
    assert other['source_sha256'] == hashlib.sha256(raw + b'\n').hexdigest()
    assert all(padded[name] == outputs[name] for name in CATALOG_EXPORT_NAMES if name != 'manifest.json')


@pytest.mark.parametrize('existing', [False, True])
def test_serialization_failure_never_changes_sources_or_exports(tmp_path, monkeypatch, existing):
    source, destination, previous = old_exports(tmp_path)
    if not existing:
        for path in previous:
            path.unlink()
        destination.rmdir()
    original = source.read_bytes()

    def fail(*_args, **_kwargs):
        raise RuntimeError('Injected Parquet serialization failure')

    monkeypatch.setattr(pq, 'write_table', fail)
    with pytest.raises(RuntimeError, match='Parquet serialization failure'):
        export_catalog(source, destination)
    assert source.read_bytes() == original
    if existing:
        assert {path: path.read_bytes() for path in previous} == previous
    else:
        assert not destination.exists()


@pytest.mark.parametrize('failed_name', CATALOG_EXPORT_NAMES)
def test_output_or_manifest_replace_failure_rolls_back_then_retry_repairs(tmp_path, monkeypatch, failed_name):
    source, destination, previous = old_exports(tmp_path)
    raw = source.read_bytes()
    expected_manifest, expected_files = build_catalog_exports(raw)
    replace, failed = Path.replace, False

    def fail_once(path, target):
        nonlocal failed
        if Path(target) == destination / failed_name and not failed:
            failed = True
            raise OSError('Injected catalogue replacement failure')
        return replace(path, target)

    monkeypatch.setattr(Path, 'replace', fail_once)
    with pytest.raises(OSError, match='catalogue replacement failure'):
        export_catalog(source, destination)
    assert source.read_bytes() == raw
    assert {path: path.read_bytes() for path in previous} == previous
    assert not list(destination.glob('.*.mdh-*'))
    assert export_catalog(source, destination) == expected_manifest
    assert {path.name: path.read_bytes() for path in previous} == expected_files
    times = {path: path.stat().st_mtime_ns for path in previous}
    monkeypatch.setattr(Path, 'replace', lambda *_: pytest.fail('Healthy retry must not rewrite'))
    assert export_catalog(source, destination) == expected_manifest
    assert times == {path: path.stat().st_mtime_ns for path in previous}


def test_missing_corrupt_and_mixed_exports_are_repaired_from_current_source(tmp_path):
    source, destination, _ = old_exports(tmp_path)
    manifest = export_catalog(source, destination)
    expected = {name: (destination / name).read_bytes() for name in CATALOG_EXPORT_NAMES}
    (destination / 'catalog.json').unlink()
    (destination / 'records.parquet').write_bytes(b'interrupted old export')
    (destination / 'manifest.json').write_bytes(b'{"stale":true}')
    assert export_catalog(source, destination) == manifest
    assert {name: (destination / name).read_bytes() for name in CATALOG_EXPORT_NAMES} == expected


def test_concurrent_reviewed_source_edit_is_retained_and_not_exported_as_old_bytes(tmp_path, monkeypatch):
    source, destination, previous = old_exports(tmp_path)
    original = source.read_bytes()
    render = publication.build_catalog_exports

    def interleave(raw):
        result = render(raw)
        source.write_bytes(original + b'\n')
        return result

    monkeypatch.setattr(publication, 'build_catalog_exports', interleave)
    with pytest.raises(FileOutputConflict):
        export_catalog(source, destination)
    assert source.read_bytes() == original + b'\n'
    assert {path: path.read_bytes() for path in previous} == previous


@pytest.mark.parametrize('name', CATALOG_EXPORT_NAMES)
def test_source_cannot_alias_one_of_its_own_export_destinations(tmp_path, name):
    source = tmp_path / name
    original = CATALOG.read_bytes()
    source.write_bytes(original)
    with pytest.raises(ValueError, match='separate from its exports'):
        export_catalog(source, tmp_path)
    assert source.read_bytes() == original
    assert list(tmp_path.iterdir()) == [source]
