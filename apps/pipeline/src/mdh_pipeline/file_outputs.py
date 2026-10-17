"""Recoverable replacement of validated local outputs, one atomic file at a time.

This is not a filesystem-wide transaction or a lock between independent writers.
Expected snapshots detect observed concurrent changes; rollback never knowingly
overwrites bytes written by somebody else after this operation.
"""
from __future__ import annotations

import os
import stat
import tempfile
from pathlib import Path


class FileOutputConflict(ValueError):
    """A source or destination changed after the caller prepared its outputs."""


def _path(value):
    path = Path(value)
    if path.is_symlink():
        raise ValueError('Output paths cannot be symlinks')
    return path.resolve()


def _snapshot(path):
    try:
        info = path.lstat()
    except FileNotFoundError:
        return None, 0o644
    if not stat.S_ISREG(info.st_mode):
        raise ValueError('Output paths must be regular files')
    # Do not follow a symlink substituted after the preliminary lstat.
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, 'rb') as stream:
        actual = os.fstat(stream.fileno())
        if not stat.S_ISREG(actual.st_mode):
            raise ValueError('Output paths must be regular files')
        return stream.read(), stat.S_IMODE(actual.st_mode)


def _check(path, expected):
    if _snapshot(path)[0] != expected:
        raise FileOutputConflict('Publication inputs or outputs changed during preparation')


def _stage(target, content, mode):
    descriptor, name = tempfile.mkstemp(dir=target.parent, prefix='.' + target.name + '.mdh-')
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, 'wb') as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.chmod(mode)
        return temporary
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def write_outputs(outputs, *, expected=None):
    """Write ``(Path, bytes)`` pairs; return whether any bytes actually changed.

    All outputs and backups are staged before the first replacement. On caught
    failures, restoration is best effort for every replaced file, including when
    another restoration fails. Callers should place their authoritative source
    last and regenerate projections on a retry after any interrupted operation.
    """
    values = {}
    for target, content in outputs:
        path = _path(target)
        if path in values:
            raise ValueError('Publication output paths must be distinct')
        if not isinstance(content, bytes):
            raise TypeError('Publication outputs must be encoded bytes')
        values[path] = content
    prerequisites = {}
    for target, content in (expected or {}).items():
        path = _path(target)
        if path in prerequisites:
            raise ValueError('Expected publication paths must be distinct')
        if content is not None and not isinstance(content, bytes):
            raise TypeError('Expected snapshots must be bytes or None')
        prerequisites[path] = content
    previous = {path: _snapshot(path) for path in values}
    for path, content in prerequisites.items():
        _check(path, content)
    changed = {path: content for path, content in values.items() if content != previous[path][0]}
    if not changed:
        return False
    staged, backups, replaced = {}, {}, []
    try:
        for path, content in changed.items():
            old, mode = previous[path]
            path.parent.mkdir(parents=True, exist_ok=True)
            staged[path] = _stage(path, content, mode)
            if old is not None:
                backups[path] = _stage(path, old, mode)
        # Check all snapshots together after expensive serialization/staging.
        for path, (content, _) in previous.items():
            _check(path, content)
        for path, content in prerequisites.items():
            _check(path, content)
        for path, content in changed.items():
            _check(path, previous[path][0])
            staged[path].replace(path)
            replaced.append(path)
        # A read-only source can change while its derived files are replaced.
        for path, content in prerequisites.items():
            if path not in changed:
                _check(path, content)
    except (OSError, ValueError):
        for path in reversed(replaced):
            try:
                if _snapshot(path)[0] != changed[path]:
                    continue
                if previous[path][0] is None:
                    path.unlink(missing_ok=True)
                else:
                    backups[path].replace(path)
            except (OSError, ValueError):
                # Try every other restoration and preserve the original failure.
                continue
        raise
    finally:
        for temporary in (*staged.values(), *backups.values()):
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                # An unavailable filesystem cannot be repaired by hiding the
                # original error; a retry regenerates the authoritative outputs.
                pass
    return True
