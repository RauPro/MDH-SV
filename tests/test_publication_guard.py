"""The installed guard protects exact pushed tips even on an older checkout."""
import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def module(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / (name + ".py"))
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


guard = module("publication_guard")
installer = module("install_publication_guard")
ONE, TWO, ZERO = "1" * 40, "2" * 40, "0" * 40


def test_push_uses_exact_tip_ids_and_scans_both_reference_names():
    data = (f"refs/heads/feat/one {ONE} refs/heads/feat/one {ZERO}\n"
            f"refs/tags/v2.1 {ONE} refs/tags/v2.1 {TWO}\n"
            f"(delete) {ZERO} refs/heads/retired {TWO}\n").encode()
    tips, names = guard.pushed_refs(data)
    assert tips == [ONE]
    assert names == ["refs/heads/feat/one", "refs/tags/v2.1"]


@pytest.mark.parametrize("value", [
    b"bad\n", b"\xff", f"HEAD invalid refs/heads/main {ZERO}".encode(),
    f"HEAD {ONE} refs/private/legacy {ZERO}".encode(),
    f"HEAD {ONE} refs/remotes/origin/main {ZERO}".encode(),
    b"x" * (guard.MAX_INPUT + 1),
])
def test_malformed_or_mirror_records_fail_closed_without_echoing_input(value):
    with pytest.raises(guard.GuardError) as error:
        guard.pushed_refs(value)
    assert str(error.value) in {
        "invalid_push_reference_record", "invalid_push_reference_encoding",
        "invalid_push_object_id", "push_namespace_not_publicable", "push_reference_list_too_large"}


def test_deletion_only_push_needs_no_scanner():
    assert guard.pushed_refs(f"(delete) {ZERO} refs/heads/removed {ONE}\n".encode()) == ([], [])


@pytest.mark.parametrize("value", [
    {}, {"schema_version": True, "required_root": ONE, "rejected_ancestors": [TWO]},
    {"schema_version": 1, "required_root": "--all", "rejected_ancestors": [TWO]},
    {"schema_version": 1, "required_root": ONE, "rejected_ancestors": []},
    {"schema_version": 1, "required_root": ONE, "rejected_ancestors": ["invalid"]},
])
def test_policy_cannot_silently_disable_ancestry_rules(tmp_path, value):
    path = tmp_path / "policy.json"
    path.write_text(json.dumps(value))
    with pytest.raises(guard.GuardError):
        guard.policy_arguments(path)


def test_duplicate_policy_fields_are_invalid(tmp_path):
    path = tmp_path / "policy.json"
    path.write_text('{"schema_version":1,"schema_version":1}')
    with pytest.raises(guard.GuardError):
        guard.policy_arguments(path)


def test_startup_error_does_not_echo_exception_content(monkeypatch, capsys):
    def unavailable(*_args, **_kwargs):
        raise OSError("private diagnostic must not appear in logs")
    monkeypatch.setattr(guard, "check", unavailable)
    assert guard.main(["check"]) == 2
    captured = capsys.readouterr()
    assert captured.err == "publication_guard_unavailable\n"
    assert not captured.out


def test_private_bundle_remains_forbidden_after_deletion(tmp_path):
    repo = tmp_path / "repository"
    repository(repo)
    (repo / "recover.bundle").write_bytes(b"Synthetic compressed backup fixture")
    git(repo, "add", "recover.bundle")
    git(repo, "commit", "-m", "Add prohibited recovery archive")
    git(repo, "rm", "recover.bundle")
    git(repo, "commit", "-m", "Delete recovery archive")
    with pytest.raises(guard.GuardError, match="forbidden_release_path_in_history"):
        guard.check_history_paths(repo, ["HEAD"])


def git(repo, *args, check=True):
    return subprocess.run(["git", "-C", str(repo), *args], check=check, text=True,
                          capture_output=True, env={**os.environ, "GIT_TERMINAL_PROMPT": "0"})


def repository(path):
    path.mkdir()
    git(path, "init", "-b", "main")
    git(path, "config", "user.name", "Guard test")
    git(path, "config", "user.email", "guard@example.invalid")
    (path / "README").write_text("Public fixture\n")
    git(path, "add", "README")
    git(path, "commit", "-m", "Initialize harmless fixture")
    return git(path, "rev-parse", "HEAD").stdout.strip()


def trusted_source(tmp_path, root):
    source = tmp_path / "trusted"
    for name in installer.FILES:
        destination = source / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / name, destination)
    (source / ".security/publication-history.json").write_text(json.dumps({
        "schema_version": 1, "required_root": root, "rejected_ancestors": [ZERO]}))
    return source


def binary():
    value = os.environ.get("GITLEAKS_BIN") or shutil.which("gitleaks")
    if not value:
        pytest.skip("Set GITLEAKS_BIN to run real pre-push integration")
    return Path(value).resolve()


def test_installation_preserves_existing_hook_and_hooks_path(tmp_path, monkeypatch):
    repo = tmp_path / "repository"
    root = repository(repo)
    monkeypatch.setattr(installer, "ROOT", trusted_source(tmp_path, root))
    hook = repo / ".git/hooks/pre-push"
    hook.write_text("#!/bin/sh\nexit 7\n")
    with pytest.raises(ValueError, match="existing_pre_push"):
        installer.install(repo, Path(sys.executable))
    assert hook.read_text() == "#!/bin/sh\nexit 7\n"
    hook.unlink()
    git(repo, "config", "core.hooksPath", "custom-hooks")
    with pytest.raises(ValueError, match="existing_hooks_path"):
        installer.install(repo, Path(sys.executable))
    assert not hook.exists()


def test_installed_snapshot_is_immutable_and_cannot_be_reused_after_tampering(tmp_path, monkeypatch):
    repo = tmp_path / "repository"
    root = repository(repo)
    monkeypatch.setattr(installer, "ROOT", trusted_source(tmp_path, root))
    hook = installer.install(repo, Path(sys.executable))
    original = hook.read_bytes()
    assert "-I -S" in hook.read_text()
    assert installer.install(repo, Path(sys.executable)).read_bytes() == original
    snapshots = list((repo / ".git/mdh-publication-guard").iterdir())
    assert len(snapshots) == 1
    (snapshots[0] / "scripts/publication_guard.py").write_text("raise SystemExit(0)")
    with pytest.raises(ValueError, match="snapshot_changed"):
        installer.install(repo, Path(sys.executable))
    assert hook.read_bytes() == original


def test_pre_push_blocks_deleted_historical_secret_without_executing_checkout_code(tmp_path, monkeypatch):
    executable = binary()
    repo = tmp_path / "repository"
    root = repository(repo)
    monkeypatch.setattr(installer, "ROOT", trusted_source(tmp_path, root))
    installer.install(repo, executable)
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "--bare", str(remote)], check=True, capture_output=True)
    git(repo, "remote", "add", "origin", str(remote))
    assert git(repo, "push", "origin", "main", check=False).returncode == 0

    git(repo, "switch", "-c", "bug/history-test")
    secret = "AI" + "za" + "aB9cD7eF5gH3jK1mN8pQ6rS4tV2wX0yZ5u7"
    assert len(secret) == 39
    (repo / "credential.txt").write_text("credential=" + secret + "\n")
    git(repo, "add", "credential.txt")
    git(repo, "commit", "-m", "Add synthetic credential")
    git(repo, "rm", "credential.txt")
    git(repo, "commit", "-m", "Remove synthetic credential")
    # The committed/working-tree scanner cannot replace the installed snapshot.
    (repo / "scripts").mkdir()
    marker = tmp_path / "untrusted-was-executed"
    (repo / "scripts/publication_guard.py").write_text(
        "from pathlib import Path\nPath(" + repr(str(marker)) + ").touch()\n")
    pushed = git(repo, "push", "origin", "bug/history-test", check=False)
    assert pushed.returncode != 0
    assert not marker.exists()
    assert secret not in pushed.stdout + pushed.stderr
    assert subprocess.run(["git", "--git-dir", str(remote), "show-ref", "--verify",
                           "refs/heads/bug/history-test"], capture_output=True, check=False).returncode != 0


def test_reference_name_secret_blocks_whole_multiref_push(tmp_path, monkeypatch):
    executable = binary()
    repo = tmp_path / "repository"
    root = repository(repo)
    monkeypatch.setattr(installer, "ROOT", trusted_source(tmp_path, root))
    installer.install(repo, executable)
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "--bare", str(remote)], check=True, capture_output=True)
    git(repo, "remote", "add", "origin", str(remote))
    secret = "AI" + "za" + "aB9cD7eF5gH3jK1mN8pQ6rS4tV2wX0yZ5u7"
    ref = "refs/tags/" + secret
    pushed = git(repo, "push", "origin", "HEAD:refs/heads/clean", "HEAD:" + ref, check=False)
    assert pushed.returncode != 0
    assert secret not in pushed.stdout + pushed.stderr
    assert subprocess.run(["git", "--git-dir", str(remote), "show-ref"],
                          capture_output=True, check=False).returncode != 0
