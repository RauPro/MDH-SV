"""Generated-archive conflicts rebuild from merged inputs; unknown work is preserved."""
import json
from pathlib import Path
import subprocess

import pytest

from mdh_pipeline.news_git_publication import (
    NEWS_ARCHIVE, PublicationRebaseError, rebase_generated_archive,
)

SELECTION = "data/selection/news.json"
HISTORY = "data/history/source.json"


def git(folder, *arguments):
    result = subprocess.run(["git", *arguments], cwd=folder, check=True, capture_output=True, text=True)
    return result.stdout.strip()


def write(folder, path, value):
    target = folder / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(value, sort_keys=True, indent=2) + "\n")


def commit(folder, message):
    git(folder, "add", "--all")
    git(folder, "commit", "-m", message)


@pytest.fixture
def repositories(tmp_path, monkeypatch):
    # Tests use local file transports only and do not inherit user signing/hooks.
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", "/dev/null")
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    origin, upstream, local = (tmp_path / name for name in ("origin.git", "upstream", "publication"))
    git(tmp_path, "init", "--bare", "--initial-branch=main", str(origin))
    git(tmp_path, "clone", str(origin), str(upstream))
    git(upstream, "config", "user.name", "Publication fixture")
    git(upstream, "config", "user.email", "fixture@example.invalid")
    write(upstream, NEWS_ARCHIVE, {"history": "base", "selection": "base"})
    write(upstream, SELECTION, {"selection": "base"})
    write(upstream, HISTORY, {"history": "base"})
    write(upstream, "README.md", "base docs")
    commit(upstream, "Base public fixture")
    git(upstream, "push", "origin", "main")
    git(tmp_path, "clone", str(origin), str(local))
    git(local, "config", "user.name", "Publication fixture")
    git(local, "config", "user.email", "fixture@example.invalid")
    return origin, upstream, local


def concurrent_archive_updates(repositories, *, other_conflict=False):
    origin, upstream, local = repositories
    write(local, NEWS_ARCHIVE, {"history": "base", "selection": "local-new"})
    write(local, SELECTION, {"selection": "local-new"})
    if other_conflict:
        write(local, "README.md", "local docs")
    commit(local, "Publish newly selected sources")
    write(upstream, HISTORY, {"history": "upstream-new"})
    write(upstream, NEWS_ARCHIVE, {"history": "upstream-new", "selection": "base"})
    write(upstream, "README.md", "upstream docs")
    commit(upstream, "Extend the bibliography and update docs")
    git(upstream, "push", "origin", "main")
    return git(origin, "rev-parse", "main")


def regenerate(repository):
    inputs = {}
    for name in (HISTORY, SELECTION):
        inputs.update(json.loads((repository / name).read_text()))
    write(repository, NEWS_ARCHIVE, inputs)


def test_only_generated_news_conflict_rebuilds_using_upstream_history_and_local_selection(repositories):
    origin, _, local = repositories
    upstream_head = concurrent_archive_updates(repositories)
    (local / "untracked-note.txt").write_text("Do not alter local work")
    calls = []
    def checked_rebuild(repository):
        calls.append(repository)
        # ours during rebase must be the upstream archive before regeneration.
        assert json.loads((repository / NEWS_ARCHIVE).read_text()) == {
            "history": "upstream-new", "selection": "base"}
        regenerate(repository)
    assert rebase_generated_archive(local, checked_rebuild) is True
    assert calls == [local]
    assert json.loads((local / NEWS_ARCHIVE).read_text()) == {
        "history": "upstream-new", "selection": "local-new"}
    assert json.loads((local / "README.md").read_text()) == "upstream docs"
    assert (local / "untracked-note.txt").read_text() == "Do not alter local work"
    assert git(local, "status", "--porcelain", "--untracked-files=no") == ""
    assert git(local, "rev-parse", "HEAD^") == upstream_head
    assert git(origin, "rev-parse", "main") == upstream_head  # Helper never pushes.
    assert not (local / ".git/rebase-merge").exists()


def test_unrelated_conflict_is_not_resolved_or_overwritten(repositories):
    origin, _, local = repositories
    upstream_head = concurrent_archive_updates(repositories, other_conflict=True)
    calls = []
    with pytest.raises(PublicationRebaseError, match="publication_rebase_conflict_not_supported"):
        rebase_generated_archive(local, lambda repository: calls.append(repository))
    assert calls == []
    assert set(git(local, "diff", "--name-only", "--diff-filter=U").splitlines()) == {NEWS_ARCHIVE, "README.md"}
    assert "<<<<<<<" in (local / "README.md").read_text()
    assert (local / ".git/rebase-merge").exists()
    assert git(origin, "rev-parse", "main") == upstream_head


@pytest.mark.parametrize("staged", [False, True])
def test_dirty_tracked_work_is_rejected_before_pull_and_preserved(repositories, staged):
    _, _, local = repositories
    before = git(local, "rev-parse", "HEAD")
    write(local, "README.md", "uncommitted user work")
    if staged:
        git(local, "add", "README.md")
    calls = []
    with pytest.raises(PublicationRebaseError, match="publication_repository_dirty"):
        rebase_generated_archive(local, lambda repository: calls.append(repository))
    assert calls == [] and git(local, "rev-parse", "HEAD") == before
    assert json.loads((local / "README.md").read_text()) == "uncommitted user work"
    assert not (local / ".git/rebase-merge").exists()


def test_preexisting_rebase_is_rejected_without_rebuilding_or_aborting(repositories):
    _, _, local = repositories
    concurrent_archive_updates(repositories)
    result = subprocess.run(["git", "pull", "--rebase", "origin", "main"], cwd=local, capture_output=True)
    assert result.returncode != 0 and (local / ".git/rebase-merge").exists()
    before = (local / NEWS_ARCHIVE).read_bytes()
    with pytest.raises(PublicationRebaseError, match="publication_operation_already_active"):
        rebase_generated_archive(local, regenerate)
    assert (local / NEWS_ARCHIVE).read_bytes() == before
    assert (local / ".git/rebase-merge").exists()


def test_rebuild_failure_does_not_continue_the_rebase_or_push(repositories):
    origin, _, local = repositories
    upstream_head = concurrent_archive_updates(repositories)
    def fails(repository):
        raise ValueError("invalid current bibliography")
    with pytest.raises(PublicationRebaseError, match="publication_archive_rebuild_failed"):
        rebase_generated_archive(local, fails)
    assert (local / ".git/rebase-merge").exists()
    assert git(origin, "rev-parse", "main") == upstream_head


@pytest.mark.parametrize("outside", ["README.md", "new-unrelated.txt"])
def test_rebuild_changes_outside_the_generated_allowlist_are_never_committed(repositories, outside):
    origin, _, local = repositories
    upstream_head = concurrent_archive_updates(repositories)
    def unsafe_rebuild(repository):
        regenerate(repository)
        write(repository, outside, "unrelated callback change")
    with pytest.raises(PublicationRebaseError, match="publication_changes_outside_generated_files"):
        rebase_generated_archive(local, unsafe_rebuild)
    assert (local / ".git/rebase-merge").exists()
    assert git(origin, "rev-parse", "main") == upstream_head
    assert json.loads((local / outside).read_text()) == "unrelated callback change"


def test_replayed_unrelated_change_is_rejected_before_rebuild_even_if_it_merged_cleanly(repositories):
    _, upstream, local = repositories
    write(local, "local-only.md", "committed unrelated work")
    write(local, NEWS_ARCHIVE, {"selection": "local-new"})
    commit(local, "Local changes outside publication scope")
    write(upstream, NEWS_ARCHIVE, {"history": "upstream-new"})
    commit(upstream, "Concurrent archive update")
    git(upstream, "push", "origin", "main")
    calls = []
    with pytest.raises(PublicationRebaseError, match="publication_changes_outside_generated_files"):
        rebase_generated_archive(local, lambda repository: calls.append(repository))
    assert calls == [] and (local / ".git/rebase-merge").exists()
    assert json.loads((local / "local-only.md").read_text()) == "committed unrelated work"


@pytest.mark.parametrize("upstream_change", [False, True])
def test_clean_pull_or_fast_forward_requires_no_rebuild(repositories, upstream_change):
    origin, upstream, local = repositories
    if upstream_change:
        write(upstream, HISTORY, {"history": "upstream-new"})
        commit(upstream, "Only an upstream bibliography update")
        git(upstream, "push", "origin", "main")
    calls = []
    assert rebase_generated_archive(local, lambda repository: calls.append(repository)) is False
    assert calls == []
    assert git(local, "rev-parse", "HEAD") == git(origin, "rev-parse", "main")


def test_nonconflict_pull_failure_does_not_run_rebuild_or_change_head(repositories, tmp_path):
    _, _, local = repositories
    git(local, "remote", "set-url", "origin", str(tmp_path / "not-a-repository"))
    before = git(local, "rev-parse", "HEAD")
    calls = []
    with pytest.raises(PublicationRebaseError, match="publication_rebase_conflict_not_supported"):
        rebase_generated_archive(local, lambda repository: calls.append(repository))
    assert calls == [] and git(local, "rev-parse", "HEAD") == before
    assert not (local / ".git/rebase-merge").exists()


def test_cli_rebuild_command_is_export_only_and_binds_all_merged_inputs(tmp_path, monkeypatch, capsys):
    from mdh_pipeline import news_git_publication as module
    calls = []
    repository = tmp_path / "publication"; repository.mkdir()
    excluded = tmp_path / "excluded.json"; excluded.write_text("[]")
    monkeypatch.setattr("sys.argv", ["news_git_publication", "--repository", str(repository), "--excluded-ids", str(excluded)])
    def rebase(path, callback):
        assert path == repository
        callback(repository)
        return True
    monkeypatch.setattr(module, "rebase_generated_archive", rebase)
    monkeypatch.setattr(module.subprocess, "run", lambda command, **options: calls.append((command, options)))
    module.main()
    command, options = calls[0]
    assert command[1:4] == ["-m", "mdh_pipeline.history", "--export-only"]
    assert options["cwd"] == repository and options["check"] is True
    assert command[command.index("--excluded-ids") + 1] == str(excluded.resolve())
    for option, path in {"--data-dir": "data/history", "--selection": SELECTION,
        "--reviewed-news": "data/reviewed/news_dossiers.json",
        "--exceptions": "data/selection/news_exceptions.json", "--output": NEWS_ARCHIVE}.items():
        assert command[command.index(option) + 1] == path
    assert "publication_archive_rebuilt" in capsys.readouterr().out
