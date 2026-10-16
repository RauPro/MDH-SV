"""Transfer an already validated public projection across isolated workflow jobs.

This module uses only the standard library and never imports code from the data
checkout. Hashes bind transport bytes; they do not replace the policy validators
run by the approved code before packing the snapshot.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import tempfile
from pathlib import Path

PUBLIC_FILES = (
    "data/selection/news.json",
    "data/automated/news_dossiers.json",
    "apps/web/public/archive/news.json",
    "apps/web/public/archive/dossiers.json",
    "apps/web/public/archive/locations.json",
    "apps/web/public/archive/processing.json",
)
MAX_FILE_BYTES = 64 * 1024 * 1024
MAX_MANIFEST_BYTES = 64 * 1024


def _safe_path(path: Path, *, directory: bool = False) -> Path:
    """Reject symlinks in the root, ancestors and leaf without resolving them."""
    path = Path(os.path.abspath(path))
    for component in [*reversed(path.parents), path]:
        info = component.lstat()
        expected_directory = component != path or directory
        if not (stat.S_ISDIR(info.st_mode) if expected_directory else stat.S_ISREG(info.st_mode)):
            raise ValueError("Unsafe publication path: expected regular file/directory")
    return path


def _read(path: Path, limit: int = MAX_FILE_BYTES) -> bytes:
    path = _safe_path(path)
    if path.stat().st_size > limit:
        raise ValueError("Publication file exceeds size limit")
    with path.open("rb") as stream:
        value = stream.read(limit + 1)
    if len(value) > limit:
        raise ValueError("Publication file exceeds size limit")
    return value


def _json(value: bytes) -> dict:
    def unique(pairs):
        result = {}
        for key, item in pairs:
            if key in result:
                raise ValueError("Duplicate JSON key in publication snapshot")
            result[key] = item
        return result

    def invalid_constant(_):
        raise ValueError("Non-JSON numeric constant in publication snapshot")

    parsed = json.loads(
        value.decode("utf-8"), object_pairs_hook=unique, parse_constant=invalid_constant
    )
    if not isinstance(parsed, dict):
        raise ValueError("Publication files must contain JSON objects")  # noqa: TRY004
    return parsed


def _git(repository: Path, *args: str) -> bytes:
    return subprocess.run(
        ["git", "-C", str(repository), *args], check=True, capture_output=True
    ).stdout


def _head(repository: Path) -> str:
    root = _git(repository, "rev-parse", "--show-toplevel").decode().strip()
    if Path(root) != repository:
        raise ValueError("Publication repository must be its checkout root")
    head = _git(repository, "rev-parse", "--verify", "HEAD").decode().strip()
    if not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", head):
        raise ValueError("Invalid publication base commit")
    return head


def _tree_files(root: Path) -> set[str]:
    _safe_path(root, directory=True)
    files = set()
    for directory, subdirectories, names in os.walk(root, followlinks=False):
        for name in subdirectories:
            _safe_path(Path(directory) / name, directory=True)
        for name in names:
            path = _safe_path(Path(directory) / name)
            files.add(path.relative_to(root).as_posix())
    return files


def check_inputs(repository: Path) -> None:
    """Preflight all checkout data before the private job reads any of it."""
    repository = _safe_path(repository, directory=True)
    _head(repository)
    roots = ("data", "apps/web/public")
    for root in roots:
        _tree_files(repository / root)
    entries = _git(repository, "ls-files", "--stage", "-z", "--", *roots).split(b"\0")
    for entry in filter(None, entries):
        metadata, _ = entry.split(b"\t", 1)
        mode, _, stage = metadata.split()
        if mode != b"100644" or stage != b"0":
            raise ValueError("Public data must be tracked as ordinary non-executable files")


def pack(repository: Path, output: Path) -> dict:
    repository = _safe_path(repository, directory=True)
    base_sha = _head(repository)
    # Read and check every allowed input before creating the artifact directory.
    contents = {name: _read(repository / name) for name in PUBLIC_FILES}
    for value in contents.values():
        _json(value)
    manifest = {
        "schema_version": 1,
        "base_sha": base_sha,
        "files": {
            name: {"sha256": hashlib.sha256(value).hexdigest(), "bytes": len(value)}
            for name, value in contents.items()
        },
    }
    output = Path(os.path.abspath(output))
    _safe_path(output.parent, directory=True)
    # Never reuse an artifact directory that might contain additional content.
    output.mkdir()
    try:
        for name, value in contents.items():
            destination = output / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(value)
        (output / "manifest.json").write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
    except BaseException:
        shutil.rmtree(output)
        raise
    return manifest


def _validated_snapshot(snapshot: Path) -> tuple[dict, dict[str, bytes]]:
    snapshot = _safe_path(snapshot, directory=True)
    allowed = {*PUBLIC_FILES, "manifest.json"}
    if _tree_files(snapshot) != allowed:
        raise ValueError("Snapshot contains missing or unexpected files")
    # Empty extra directories also violate the allowlist.
    allowed_directories = {
        parent.as_posix()
        for name in allowed
        for parent in Path(name).parents
        if parent != Path(".")
    }
    for directory, subdirectories, _ in os.walk(snapshot, followlinks=False):
        for name in subdirectories:
            if (Path(directory) / name).relative_to(snapshot).as_posix() not in allowed_directories:
                raise ValueError("Snapshot contains unexpected directories")
    manifest = _json(_read(snapshot / "manifest.json", MAX_MANIFEST_BYTES))
    if (
        set(manifest) != {"schema_version", "base_sha", "files"}
        or type(manifest["schema_version"]) is not int
        or manifest["schema_version"] != 1
        or not isinstance(manifest["base_sha"], str)
        or not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", manifest["base_sha"])
        or not isinstance(manifest["files"], dict)
        or set(manifest["files"]) != set(PUBLIC_FILES)
    ):
        raise ValueError("Invalid publication manifest")
    contents = {}
    for name in PUBLIC_FILES:
        metadata = manifest["files"][name]
        if (
            not isinstance(metadata, dict)
            or set(metadata) != {"sha256", "bytes"}
            or type(metadata["bytes"]) is not int
            or not 0 < metadata["bytes"] <= MAX_FILE_BYTES
            or not isinstance(metadata["sha256"], str)
            or not re.fullmatch(r"[0-9a-f]{64}", metadata["sha256"])
        ):
            raise ValueError("Invalid publication file metadata")
        value = _read(snapshot / name)
        if (
            len(value) != metadata["bytes"]
            or hashlib.sha256(value).hexdigest() != metadata["sha256"]
        ):
            raise ValueError("Publication snapshot checksum/size mismatch")
        _json(value)
        contents[name] = value
    return manifest, contents


def _stage(destination: Path, value: bytes) -> Path:
    _safe_path(destination.parent, directory=True)
    descriptor, temporary = tempfile.mkstemp(prefix=".publication-", dir=destination.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(value)
        os.chmod(temporary, 0o644)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise
    return Path(temporary)


def apply(repository: Path, snapshot: Path) -> dict:
    repository = _safe_path(repository, directory=True)
    manifest, contents = _validated_snapshot(snapshot)
    if _head(repository) != manifest["base_sha"]:
        raise ValueError(
            "Publication HEAD differs from snapshot base_sha; prepare a fresh snapshot"
        )
    # Validate every destination and retain rollback bytes before the first write.
    before = {name: _read(repository / name) for name in PUBLIC_FILES}
    staged = {}
    replaced = []
    try:
        for name, value in contents.items():
            if value != before[name]:
                staged[name] = _stage(repository / name, value)
        if _head(repository) != manifest["base_sha"]:
            raise ValueError("Publication HEAD changed while applying snapshot")
        for name, temporary in staged.items():
            _safe_path(repository / name)
            os.replace(temporary, repository / name)
            replaced.append(name)
    except BaseException:
        for name in reversed(replaced):
            temporary = _stage(repository / name, before[name])
            try:
                os.replace(temporary, repository / name)
            finally:
                temporary.unlink(missing_ok=True)
        raise
    finally:
        for temporary in staged.values():
            temporary.unlink(missing_ok=True)
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for command in ("check-inputs", "pack", "apply"):
        subparser = commands.add_parser(command)
        subparser.add_argument("--repository", required=True, type=Path)
        if command == "pack":
            subparser.add_argument("--output", required=True, type=Path)
        elif command == "apply":
            subparser.add_argument("--snapshot", required=True, type=Path)
    args = parser.parse_args()
    if args.command == "check-inputs":
        check_inputs(args.repository)
        print("Verified public checkout contains ordinary data files only.")
    else:
        manifest = (
            pack(args.repository, args.output)
            if args.command == "pack"
            else apply(args.repository, args.snapshot)
        )
        print(json.dumps({"base_sha": manifest["base_sha"], "files": len(PUBLIC_FILES)}))


if __name__ == "__main__":
    main()
