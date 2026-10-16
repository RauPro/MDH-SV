"""Adversarial tests for the only data passed to the credentialed public job."""

import hashlib
import json
import subprocess

import pytest
from mdh_pipeline import publication_snapshot as snapshot


def git(repository, *args):
    return subprocess.run(
        ["git", "-C", str(repository), *args], check=True, capture_output=True, text=True
    ).stdout.strip()


@pytest.fixture
def repository(tmp_path):
    # macOS /var is a symlink; the interface intentionally requires real ancestors.
    repository = tmp_path.resolve() / "public"
    repository.mkdir()
    git(repository, "init", "-q")
    git(repository, "config", "user.name", "Snapshot test")
    git(repository, "config", "user.email", "snapshot@example.invalid")
    for name in snapshot.PUBLIC_FILES:
        path = repository / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('{"version": "before"}\n')
    git(repository, "add", ".")
    git(repository, "commit", "-qm", "Initial public data")
    return repository


def changed_bundle(repository):
    for index, name in enumerate(snapshot.PUBLIC_FILES):
        (repository / name).write_text(json.dumps({"version": f"after-{index}"}) + "\n")
    output = repository.parent / "bundle"
    manifest = snapshot.pack(repository, output)
    git(repository, "checkout", "--", ".")
    return output, manifest


def contents(repository):
    return {name: (repository / name).read_bytes() for name in snapshot.PUBLIC_FILES}


def rewrite_manifest(bundle, manifest):
    (bundle / "manifest.json").write_text(json.dumps(manifest))


def refresh_hash(bundle, manifest, name):
    value = (bundle / name).read_bytes()
    manifest["files"][name] = {"bytes": len(value), "sha256": hashlib.sha256(value).hexdigest()}
    rewrite_manifest(bundle, manifest)


def test_pack_transports_exactly_public_allowlist_and_apply_converges(repository):
    (repository / "PRIVATE.json").write_text('{"draft": "private"}')
    bundle, manifest = changed_bundle(repository)
    assert manifest["base_sha"] == git(repository, "rev-parse", "HEAD")
    assert set(manifest["files"]) == set(snapshot.PUBLIC_FILES)
    assert snapshot._tree_files(bundle) == {*snapshot.PUBLIC_FILES, "manifest.json"}
    snapshot.apply(repository, bundle)
    assert contents(repository) == contents(bundle)
    snapshot.apply(repository, bundle)
    assert contents(repository) == contents(bundle)
    assert (repository / "PRIVATE.json").read_text() == '{"draft": "private"}'


@pytest.mark.parametrize(
    "attack",
    [
        "extra_workflow",
        "empty_extra_directory",
        "missing",
        "checksum",
        "size",
        "boolean_size",
        "oversized",
        "extra_manifest_path",
        "wrong_sha",
        "duplicate_key",
        "duplicate_nested_key",
        "duplicate_manifest_key",
        "invalid_json",
        "nan",
        "json_scalar",
        "symlink",
        "ancestor_symlink",
    ],
)
def test_invalid_bundle_is_rejected_before_any_destination_changes(repository, attack):
    bundle, manifest = changed_bundle(repository)
    before = contents(repository)
    # Tamper the last file so earlier valid files cannot be applied incrementally.
    name = snapshot.PUBLIC_FILES[-1]
    target = bundle / name
    if attack == "extra_workflow":
        extra = bundle / ".github/workflows/inject.yml"
        extra.parent.mkdir(parents=True)
        extra.write_text("run: echo untrusted")
    elif attack == "empty_extra_directory":
        (bundle / ".github").mkdir()
    elif attack == "missing":
        target.unlink()
    elif attack == "checksum":
        target.write_bytes(target.read_bytes().replace(b"after", b"other"))
    elif attack in {"size", "boolean_size", "oversized"}:
        manifest["files"][name]["bytes"] = {
            "size": 1,
            "boolean_size": True,
            "oversized": snapshot.MAX_FILE_BYTES + 1,
        }[attack]
        rewrite_manifest(bundle, manifest)
    elif attack == "extra_manifest_path":
        manifest["files"]["../private.json"] = manifest["files"][name]
        rewrite_manifest(bundle, manifest)
    elif attack == "wrong_sha":
        manifest["base_sha"] = "0" * 40
        rewrite_manifest(bundle, manifest)
    elif attack == "duplicate_manifest_key":
        original = (bundle / "manifest.json").read_text()
        (bundle / "manifest.json").write_text('{"schema_version": 1,' + original[1:])
    elif attack in {"duplicate_key", "duplicate_nested_key", "invalid_json", "nan", "json_scalar"}:
        target.write_text(
            {
                "duplicate_key": '{"a": 1, "a": 2}',
                "duplicate_nested_key": '{"a": {"x": 1, "x": 2}}',
                "invalid_json": "not json",
                "nan": '{"a": NaN}',
                "json_scalar": "null",
            }[attack]
        )
        refresh_hash(bundle, manifest, name)
    elif attack == "symlink":
        target.unlink()
        target.symlink_to(repository / name)
    elif attack == "ancestor_symlink":
        moved = repository.parent / "moved"
        (bundle / "apps").rename(moved)
        (bundle / "apps").symlink_to(moved, target_is_directory=True)
    with pytest.raises((ValueError, FileNotFoundError)):
        snapshot.apply(repository, bundle)
    assert contents(repository) == before
    assert not list(repository.rglob(".publication-*"))


def test_apply_requires_the_exact_prepared_commit(repository):
    bundle, _ = changed_bundle(repository)
    before = contents(repository)
    git(repository, "commit", "--allow-empty", "-qm", "Concurrent main update")
    with pytest.raises(ValueError, match="base_sha"):
        snapshot.apply(repository, bundle)
    assert contents(repository) == before


@pytest.mark.parametrize("where", ["leaf", "ancestor", "root", "root_ancestor"])
def test_apply_rejects_destination_symlinks_before_writing(repository, where):
    bundle, _ = changed_bundle(repository)
    destination = repository
    if where == "leaf":
        path = repository / snapshot.PUBLIC_FILES[-1]
    elif where == "ancestor":
        path = repository / "apps/web/public"
    elif where == "root":
        path = repository
    else:
        outer = repository.parent / "outer"
        outer.mkdir()
        repository.rename(outer / "public")
        repository = outer / "public"
        destination = repository
        path = outer
    moved = path.parent / f"{path.name}-moved"
    path.rename(moved)
    path.symlink_to(moved, target_is_directory=moved.is_dir())
    # Direct reads can follow the link for the assertion; production must reject it.
    before = contents(repository)
    with pytest.raises(ValueError, match="Unsafe"):
        snapshot.apply(destination, bundle)
    assert contents(repository) == before


def test_replace_failure_rolls_back_prior_files_and_removes_staging(repository, monkeypatch):
    bundle, _ = changed_bundle(repository)
    before = contents(repository)
    original = snapshot.os.replace
    calls = 0

    def fail_once(source, destination):
        nonlocal calls
        calls += 1
        if calls == 3:
            raise OSError("simulated disk error")
        return original(source, destination)

    monkeypatch.setattr(snapshot.os, "replace", fail_once)
    with pytest.raises(OSError, match="simulated"):
        snapshot.apply(repository, bundle)
    assert contents(repository) == before
    assert not list(repository.rglob(".publication-*"))


@pytest.mark.parametrize("attack", ["symlink", "ancestor_symlink", "executable", "submodule"])
def test_preflight_rejects_checkout_links_and_git_modes(repository, attack):
    snapshot.check_inputs(repository)
    target = repository / "data/selection/news.json"
    if attack == "symlink":
        target.unlink()
        target.symlink_to(repository / snapshot.PUBLIC_FILES[-1])
    elif attack == "ancestor_symlink":
        moved = repository.parent / "data-moved"
        (repository / "data").rename(moved)
        (repository / "data").symlink_to(moved, target_is_directory=True)
    elif attack == "executable":
        git(repository, "update-index", "--chmod=+x", "data/selection/news.json")
    elif attack == "submodule":
        git(
            repository,
            "update-index",
            "--add",
            "--cacheinfo",
            f"160000,{git(repository, 'rev-parse', 'HEAD')},data/module",
        )
    with pytest.raises(ValueError):
        snapshot.check_inputs(repository)


def test_pack_validates_inputs_before_creating_an_artifact(repository):
    (repository / snapshot.PUBLIC_FILES[-1]).write_text('{"a": 1, "a": 2}')
    output = repository.parent / "bundle"
    with pytest.raises(ValueError, match="Duplicate"):
        snapshot.pack(repository, output)
    assert not output.exists()


def test_pack_does_not_reuse_existing_output(repository):
    output = repository.parent / "bundle"
    output.mkdir()
    (output / "private.txt").write_text("must not be included")
    with pytest.raises(FileExistsError):
        snapshot.pack(repository, output)
    assert (output / "private.txt").read_text() == "must not be included"
