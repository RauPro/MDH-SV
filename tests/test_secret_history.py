"""The publication guard scans history, metadata, and untrusted suppression attempts."""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("scan_public_history", ROOT / "scripts/scan_public_history.py")
scanner = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = scanner
SPEC.loader.exec_module(scanner)


def git(repo, *args, data=None):
    result = subprocess.run(["git", "-C", str(repo), *args], input=data,
                            capture_output=True, check=True)
    return result.stdout.decode().strip()


@pytest.fixture
def repository(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "--quiet", "-b", "main")
    git(repo, "config", "user.name", "History test")
    git(repo, "config", "user.email", "history@example.invalid")
    commit(repo, "clean.txt", "public data\n")
    return repo


def commit(repo, name, content, message="fixture commit"):
    target = repo / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(content if isinstance(content, bytes) else content.encode())
    git(repo, "add", "--", name)
    git(repo, "commit", "--quiet", "-m", message)
    return git(repo, "rev-parse", "HEAD")


@pytest.fixture
def secret():
    # Assemble synthetic detector input at runtime; never store plausible keys.
    return "AI" + "za" + "aB7_" * 8 + "xY9"


@pytest.fixture
def engine_binary():
    binary = os.environ.get("GITLEAKS_BIN")
    if not binary:
        pytest.skip("Set GITLEAKS_BIN to the pinned engine to run integration cases")
    return binary


def scan(repo, engine_binary, **kwargs):
    return scanner.scan(repo, kwargs.pop("refs", ["HEAD"]), binary=engine_binary, **kwargs)


def test_clean_complete_history(repository, engine_binary):
    result = scan(repository, engine_binary)
    assert result.commits == 1 and not result.findings


def test_deleted_secret_remains_blocked(repository, engine_binary, secret):
    leaked = commit(repository, "historical.txt", secret)
    git(repository, "rm", "--quiet", "historical.txt")
    git(repository, "commit", "--quiet", "-m", "remove fixture")
    result = scan(repository, engine_binary)
    assert any(item.oid == leaked and item.kind == "content" for item in result.findings)


def test_merge_resolution_secret_is_scanned(repository, engine_binary, secret):
    git(repository, "checkout", "--quiet", "-b", "side")
    commit(repository, "side.txt", "side change")
    git(repository, "checkout", "--quiet", "main")
    commit(repository, "main.txt", "main change")
    git(repository, "merge", "--quiet", "--no-ff", "--no-commit", "side")
    merged = commit(repository, "resolution.txt", secret, "merge fixture")
    result = scan(repository, engine_binary)
    assert any(item.oid == merged for item in result.findings)


def test_only_selected_refs_are_scanned_and_orphan_tag_is_blocked(repository, engine_binary, secret):
    git(repository, "checkout", "--quiet", "--orphan", "private-history")
    leaked = commit(repository, "old.txt", secret)
    git(repository, "tag", "archived-version")
    git(repository, "checkout", "--quiet", "main")
    assert not scan(repository, engine_binary).findings
    result = scan(repository, engine_binary, refs=["HEAD", "refs/tags/archived-version"])
    assert any(item.oid == leaked for item in result.findings)


def test_annotated_tag_messages_are_scanned(repository, engine_binary, secret):
    git(repository, "tag", "-a", "release", "-m", secret)
    tag_oid = git(repository, "rev-parse", "release")
    result = scan(repository, engine_binary, refs=["refs/tags/release"])
    assert any(item.oid == tag_oid and item.kind == "tag" for item in result.findings)


def test_commit_message_and_refname_are_scanned(repository, engine_binary, secret):
    value = commit(repository, "clean.txt", "still public\n", message=secret)
    git(repository, "branch", "release-" + secret)
    result = scan(repository, engine_binary, refs=["refs/heads/release-" + secret])
    assert {(item.oid, item.kind) for item in result.findings} >= {
        (value, "commit"), (value, "reference")}


def test_binary_and_attributes_cannot_hide_content(repository, engine_binary, secret):
    commit(repository, ".gitattributes", "*.bin binary\n*.txt -diff\n")
    commit(repository, "payload.bin", b"PK\x03\x04\x00" + secret.encode() + b"\x00")
    binary_blob = git(repository, "rev-parse", "HEAD:payload.bin")
    text_commit = commit(repository, "payload.txt", secret)
    result = scan(repository, engine_binary)
    assert {item.oid for item in result.findings} >= {binary_blob, text_commit}


def test_candidate_suppression_files_and_comments_are_ignored(repository, engine_binary, secret):
    leaked = commit(repository, "leak.txt", secret + " # gitleaks:allow\n")
    commit(repository, ".gitleaks.toml", "[allowlist]\nregexes = ['.*']\n")
    commit(repository, ".gitleaksignore", leaked + ":leak.txt:mdh-google-api-key:1\n")
    result = scan(repository, engine_binary)
    assert any(item.oid == leaked for item in result.findings)


def test_replace_reference_cannot_hide_old_secret(repository, engine_binary, secret):
    clean = git(repository, "rev-parse", "HEAD")
    leaked = commit(repository, "leak.txt", secret)
    git(repository, "replace", leaked, clean)
    result = scan(repository, engine_binary)
    assert any(item.oid == leaked for item in result.findings)


def test_shallow_clone_is_rejected(repository, tmp_path):
    clone = tmp_path / "shallow"
    git(repository, "clone", "--quiet", "--depth=1", repository.as_uri(), str(clone))
    with pytest.raises(scanner.ScanError, match="shallow_history_not_allowed"):
        scan(clone, "unused")


def test_grafts_are_rejected(repository):
    (repository / ".git/info/grafts").write_text(git(repository, "rev-parse", "HEAD") + "\n")
    with pytest.raises(scanner.ScanError, match="grafted_history_not_allowed"):
        scan(repository, "unused")


def test_partial_clone_is_rejected(repository):
    git(repository, "config", "remote.origin.promisor", "true")
    with pytest.raises(scanner.ScanError, match="partial_history_not_allowed"):
        scan(repository, "unused")


@pytest.mark.parametrize("reference", ["--all", "HEAD..main", "HEAD~1", "HEAD:path", "^HEAD", "missing", "HEAD\t"])
def test_invalid_references_fail_closed(repository, reference):
    with pytest.raises(scanner.ScanError):
        scanner.resolve(repository, scanner.environment(), reference)


def test_blob_tags_are_rejected(repository, engine_binary):
    value = git(repository, "rev-parse", "HEAD:clean.txt")
    git(repository, "tag", "blob-tag", value)
    with pytest.raises(scanner.ScanError, match="reference_must_resolve_to_commit"):
        scan(repository, engine_binary, refs=["refs/tags/blob-tag"])


def test_public_root_and_forbidden_ancestor(repository, engine_binary):
    base = git(repository, "rev-parse", "HEAD")
    tip = commit(repository, "next.txt", "next\n")
    assert not scan(repository, engine_binary, require_root=base).findings
    with pytest.raises(scanner.ScanError, match="rejected_ancestor_is_publishable"):
        scan(repository, engine_binary, reject_ancestors=[base])
    with pytest.raises(scanner.ScanError, match="history_outside_public_root"):
        scan(repository, engine_binary, refs=[base], require_root=tip)
    assert not scan(repository, engine_binary, reject_ancestors=["f" * 40]).findings


def test_output_never_echoes_secret_path_or_context(repository, engine_binary, secret, capsys):
    commit(repository, secret + ".txt", secret, message=secret)
    result = scanner.main(["--repo", str(repository), "--gitleaks", engine_binary])
    output = capsys.readouterr()
    assert result == 1
    assert secret not in output.out + output.err
    assert "type=content" in output.out and "value withheld" in output.out


def test_environment_disables_overrides_and_lazy_fetch(monkeypatch):
    for name in ("GIT_DIR", "GIT_CONFIG_COUNT", "GIT_TRACE", "GITLEAKS_CONFIG", "GITLEAKS_CONFIG_TOML"):
        monkeypatch.setenv(name, "untrusted")
    env = scanner.environment()
    assert not any(env.get(name) for name in ("GIT_DIR", "GIT_CONFIG_COUNT", "GIT_TRACE", "GITLEAKS_CONFIG"))
    assert env["GIT_NO_REPLACE_OBJECTS"] == env["GIT_NO_LAZY_FETCH"] == "1"


def test_command_failure_does_not_expose_stderr(tmp_path, monkeypatch):
    def failed(*args, **kwargs):
        return subprocess.CompletedProcess(args[0], 1, b"", b"sensitive provider response")
    monkeypatch.setattr(scanner, "run", failed)
    with pytest.raises(scanner.ScanError, match="git_command_failed") as error:
        scanner.git(tmp_path, {}, "rev-list", "HEAD")
    assert "sensitive" not in str(error.value)


@pytest.mark.parametrize("report", [None, {}, [None], [{"RuleID": "ok", "StartLine": 1, "Commit": "bad"}],
                                    [{"RuleID": "invalid\nrule", "StartLine": 1}],
                                    [{"RuleID": "ok", "StartLine": True}]])
def test_malformed_report_is_not_clean(tmp_path, report):
    report_path = tmp_path / "report.json"
    report_path.write_text(json.dumps(report))
    with pytest.raises(scanner.ScanError, match="invalid_scanner_report"):
        scanner.report_findings(report_path, mode="git", commits=set(), metadata={})


@pytest.mark.parametrize("exit_code,records", [(1, []), (0, [{"RuleID": "ok", "StartLine": 1, "Commit": "a" * 40}]), (7, [])])
def test_scanner_failure_and_inconsistent_reports_fail_closed(tmp_path, monkeypatch, exit_code, records):
    def fake(args, **kwargs):
        Path(args[args.index("--report-path") + 1]).write_text(json.dumps(records))
        return subprocess.CompletedProcess(args, exit_code, b"sensitive", b"sensitive")
    monkeypatch.setattr(scanner, "run", fake)
    with pytest.raises(scanner.ScanError):
        scanner.engine("fake", tmp_path, mode="git", config=tmp_path / "config",
                       scratch=tmp_path, env={}, commits={"a" * 40}, metadata={}, tips=["a" * 40])


def test_scanner_invocation_cannot_skip_history_or_use_candidate_config(tmp_path, monkeypatch):
    calls = []
    def fake(args, **kwargs):
        calls.append(args)
        Path(args[args.index("--report-path") + 1]).write_text("[]")
        return subprocess.CompletedProcess(args, 0, b"", b"")
    monkeypatch.setattr(scanner, "run", fake)
    scanner.engine("fake", tmp_path, mode="git", config=tmp_path / "trusted.toml",
                   scratch=tmp_path, env={}, commits={"a" * 40}, metadata={}, tips=["a" * 40])
    command = calls[0]
    assert "--redact=100" in command and "--ignore-gitleaks-allow" in command
    options = next(arg for arg in command if arg.startswith("--log-opts="))
    assert options == "--log-opts=--full-history --root -m --text --no-ext-diff --no-textconv " + "a" * 40 + " --"
    assert Path(command[command.index("--gitleaks-ignore-path") + 1]).read_bytes() == b""


def test_invalid_cli_input_is_redacted(capsys, secret):
    assert scanner.main(["--unexpected", secret]) == 2
    assert secret not in capsys.readouterr().err
