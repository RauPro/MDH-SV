"""Actual filesystem failures must remain recoverable without clobbering edits."""
from pathlib import Path

import pytest
from mdh_pipeline import file_outputs
from mdh_pipeline.file_outputs import FileOutputConflict, write_outputs


def files(tmp_path):
    paths = [tmp_path / name for name in ('a.json', 'b.json', 'source.json')]
    for path in paths:
        path.write_bytes(b'old-' + path.name.encode())
        path.chmod(0o640)
    return paths, {path: path.read_bytes() for path in paths}


def assert_no_staging(tmp_path):
    assert not list(tmp_path.rglob('*.mdh-*'))


def test_writer_preserves_modes_and_does_not_replace_identical_outputs(tmp_path, monkeypatch):
    paths, old = files(tmp_path)
    outputs = [(path, b'new-' + path.name.encode()) for path in paths]
    assert write_outputs(outputs, expected=old)
    assert all(path.stat().st_mode & 0o777 == 0o640 for path in paths)
    timestamps = {path: path.stat().st_mtime_ns for path in paths}
    monkeypatch.setattr(Path, 'replace', lambda *_: pytest.fail('A no-op must not replace files'))
    assert not write_outputs(outputs)
    assert timestamps == {path: path.stat().st_mtime_ns for path in paths}
    assert_no_staging(tmp_path)


@pytest.mark.parametrize('failure_index', [0, 1, 2])
def test_replace_failure_rolls_back_and_identical_retry_converges(tmp_path, monkeypatch, failure_index):
    paths, old = files(tmp_path)
    replace = Path.replace
    failed = False

    def once(path, target):
        nonlocal failed
        if Path(target) == paths[failure_index] and not failed:
            failed = True
            raise OSError('Injected output replacement failure')
        return replace(path, target)

    monkeypatch.setattr(Path, 'replace', once)
    outputs = [(path, b'new-' + path.name.encode()) for path in paths]
    with pytest.raises(OSError, match='Injected output replacement failure'):
        write_outputs(outputs, expected=old)
    assert {path: path.read_bytes() for path in paths} == old
    assert_no_staging(tmp_path)
    assert write_outputs(outputs, expected=old)
    assert not write_outputs(outputs)
    assert_no_staging(tmp_path)


def test_staging_sync_failure_leaves_every_destination_unchanged(tmp_path, monkeypatch):
    paths, old = files(tmp_path)
    attempts = 0

    def fail_sync(_):
        nonlocal attempts
        attempts += 1
        if attempts == 3:
            raise OSError('Injected staging sync failure')

    monkeypatch.setattr(file_outputs.os, 'fsync', fail_sync)
    with pytest.raises(OSError, match='staging sync failure'):
        write_outputs([(path, b'new') for path in paths])
    assert {path: path.read_bytes() for path in paths} == old
    assert_no_staging(tmp_path)


def test_rollback_continues_after_one_restore_fails_and_retry_repairs(tmp_path, monkeypatch):
    paths, old = files(tmp_path)
    replace = Path.replace

    def fail(path, target):
        if Path(target) == paths[2]:
            raise OSError('Original commit failure')
        if Path(target) == paths[1] and path.read_bytes() == old[paths[1]]:
            raise OSError('Later rollback failure')
        return replace(path, target)

    outputs = [(path, b'new-' + path.name.encode()) for path in paths]
    monkeypatch.setattr(Path, 'replace', fail)
    with pytest.raises(OSError, match='^Original commit failure$'):
        write_outputs(outputs)
    assert paths[0].read_bytes() == old[paths[0]]
    assert paths[1].read_bytes() == outputs[1][1]
    assert paths[2].read_bytes() == old[paths[2]]
    assert_no_staging(tmp_path)
    monkeypatch.setattr(Path, 'replace', replace)
    assert write_outputs(outputs)
    assert not write_outputs(outputs)


def test_new_destination_is_removed_on_rollback(tmp_path, monkeypatch):
    first, last = tmp_path / 'new.json', tmp_path / 'source.json'
    last.write_bytes(b'old')
    replace = Path.replace

    def fail(path, target):
        if Path(target) == last:
            raise OSError('Source replacement failed')
        return replace(path, target)

    monkeypatch.setattr(Path, 'replace', fail)
    with pytest.raises(OSError):
        write_outputs([(first, b'new'), (last, b'new')])
    assert not first.exists() and last.read_bytes() == b'old'
    assert_no_staging(tmp_path)


@pytest.mark.parametrize('kind', ['duplicate', 'symlink', 'directory'])
def test_invalid_destinations_fail_before_any_replacement(tmp_path, kind):
    target = tmp_path / 'valid.json'
    target.write_bytes(b'original')
    other = tmp_path / 'other.json'
    if kind == 'duplicate':
        other = tmp_path / '.' / 'valid.json'
    elif kind == 'symlink':
        other.symlink_to(target)
    else:
        other.mkdir()
    with pytest.raises(ValueError):
        write_outputs([(target, b'new'), (other, b'other')])
    assert target.read_bytes() == b'original'
    assert_no_staging(tmp_path)


def test_expected_read_snapshot_rejects_earlier_concurrent_source_update(tmp_path):
    source, output = tmp_path / 'source.json', tmp_path / 'export.json'
    source.write_bytes(b'another accepted update')
    output.write_bytes(b'old export')
    with pytest.raises(FileOutputConflict):
        write_outputs([(output, b'new export')], expected={source: b'original input'})
    assert output.read_bytes() == b'old export'
    assert source.read_bytes() == b'another accepted update'
    assert_no_staging(tmp_path)


@pytest.mark.parametrize('include_source', [False, True])
def test_late_change_to_read_only_source_rolls_back_outputs_without_clobbering_source(tmp_path, monkeypatch, include_source):
    source, output = tmp_path / 'source.json', tmp_path / 'export.json'
    source.write_bytes(b'approved')
    output.write_bytes(b'old export')
    replace = Path.replace

    def interleave(path, target):
        result = replace(path, target)
        if Path(target) == output and output.read_bytes() == b'new export':
            source.write_bytes(b'newer approved source')
        return result

    monkeypatch.setattr(Path, 'replace', interleave)
    outputs = [(output, b'new export')] + ([(source, b'approved')] if include_source else [])
    with pytest.raises(FileOutputConflict):
        write_outputs(outputs, expected={source: b'approved'})
    assert output.read_bytes() == b'old export'
    assert source.read_bytes() == b'newer approved source'
    assert_no_staging(tmp_path)


def test_rollback_preserves_a_newer_external_output(tmp_path, monkeypatch):
    paths, old = files(tmp_path)
    replace = Path.replace

    def interleave(path, target):
        if Path(target) == paths[1]:
            paths[0].write_bytes(b'external newer output')
            raise OSError('Stop after external output changed')
        return replace(path, target)

    monkeypatch.setattr(Path, 'replace', interleave)
    with pytest.raises(OSError):
        write_outputs([(path, b'new') for path in paths])
    assert paths[0].read_bytes() == b'external newer output'
    assert paths[1].read_bytes() == old[paths[1]]
    assert paths[2].read_bytes() == old[paths[2]]
    assert_no_staging(tmp_path)
