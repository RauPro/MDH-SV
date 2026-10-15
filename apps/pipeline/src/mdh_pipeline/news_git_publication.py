"""Rebuild the one derived news archive conflict during a publication rebase.

Conflict recovery accepts changes only to generated publication files. This
helper never pushes, resets or aborts a rebase. Unknown conflicts remain for inspection.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import subprocess
import sys
from typing import Callable

NEWS_ARCHIVE = "apps/web/public/archive/news.json"
GENERATED_FILES = frozenset({
    "data/selection/news.json",
    "data/automated/news_dossiers.json",
    NEWS_ARCHIVE,
    "apps/web/public/archive/locations.json",
    "apps/web/public/archive/dossiers.json",
    "apps/web/public/archive/processing.json",
})


class PublicationRebaseError(ValueError):
    """Safe error codes; raw Git output may contain remote URLs or credentials."""


def _git(repository, *arguments, check=True):
    environment = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "GIT_EDITOR": "true"}
    result = subprocess.run(["git", *arguments], cwd=repository, env=environment,
                            capture_output=True, text=True, encoding="utf-8", errors="strict")
    if check and result.returncode:
        raise PublicationRebaseError("publication_git_command_failed")
    return result


def _paths(output):
    return {value for value in output.split("\0") if value}


def _git_path(repository, name):
    value = Path(_git(repository, "rev-parse", "--git-path", name).stdout.strip())
    return value if value.is_absolute() else repository / value


def _rebase_active(repository):
    return any(_git_path(repository, name).exists() for name in ("rebase-merge", "rebase-apply"))


def _tracked_changes(repository):
    return (_paths(_git(repository, "diff", "--name-only", "--no-renames", "-z").stdout)
            | _paths(_git(repository, "diff", "--cached", "--name-only", "--no-renames", "-z").stdout))


def _untracked(repository):
    return _paths(_git(repository, "ls-files", "--others", "--exclude-standard", "-z").stdout)


def _check_generated_changes(repository, initial_untracked):
    changes = _tracked_changes(repository) | (_untracked(repository) - initial_untracked)
    if not changes <= GENERATED_FILES:
        raise PublicationRebaseError("publication_changes_outside_generated_files")


def rebase_generated_archive(repository, rebuild: Callable[[Path], None]):
    """Return True only when a sole archive conflict was rebuilt successfully.

    ``rebuild`` receives the repository directory after the upstream archive has
    been checked out and the other local changes replayed. It must regenerate
    the archive from those current inputs or raise. No model or source fetch is
    required. Any failure leaves the interrupted rebase unpushed for inspection.
    """
    repository = Path(repository).resolve()
    if not repository.is_dir() or not callable(rebuild):
        raise PublicationRebaseError("invalid_publication_repository")
    root = Path(_git(repository, "rev-parse", "--show-toplevel").stdout.strip()).resolve()
    if root != repository:
        raise PublicationRebaseError("publication_repository_must_be_root")
    if (_rebase_active(repository) or any(_git_path(repository, name).exists()
            for name in ("MERGE_HEAD", "CHERRY_PICK_HEAD", "REVERT_HEAD", "sequencer"))):
        raise PublicationRebaseError("publication_operation_already_active")
    if _git(repository, "status", "--porcelain=v1", "-z", "--untracked-files=no").stdout:
        raise PublicationRebaseError("publication_repository_dirty")
    initial_untracked = _untracked(repository)
    if initial_untracked & GENERATED_FILES:
        raise PublicationRebaseError("publication_generated_file_untracked")

    pulled = _git(repository, "pull", "--rebase", "origin", "main", check=False)
    if pulled.returncode == 0:
        if _rebase_active(repository) or _tracked_changes(repository):
            raise PublicationRebaseError("publication_rebase_incomplete")
        return False

    unmerged = _paths(_git(repository, "diff", "--name-only", "--diff-filter=U", "-z").stdout)
    if not _rebase_active(repository) or unmerged != {NEWS_ARCHIVE}:
        raise PublicationRebaseError("publication_rebase_conflict_not_supported")
    _check_generated_changes(repository, initial_untracked)
    # During rebase, ours is the fetched upstream tree, not the local commit.
    _git(repository, "checkout", "--ours", "--", NEWS_ARCHIVE)
    try:
        rebuild(repository)
    except Exception as error:
        raise PublicationRebaseError("publication_archive_rebuild_failed") from error
    _check_generated_changes(repository, initial_untracked)
    # Never stage a pre-existing untracked file or an unrelated path. Existing
    # tracked generated files may include other outputs in this same commit.
    paths = _tracked_changes(repository) | (_untracked(repository) - initial_untracked)
    _git(repository, "add", "--", *sorted(paths | {NEWS_ARCHIVE}))
    if _paths(_git(repository, "diff", "--name-only", "--diff-filter=U", "-z").stdout):
        raise PublicationRebaseError("publication_unresolved_conflicts")
    _git(repository, "rebase", "--continue")
    if _rebase_active(repository) or _tracked_changes(repository):
        raise PublicationRebaseError("publication_rebase_incomplete")
    return True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", type=Path, default=Path.cwd())
    parser.add_argument("--excluded-ids", type=Path, required=True)
    args = parser.parse_args()
    excluded = args.excluded_ids.resolve()

    def rebuild(repository):
        subprocess.run([
            sys.executable, "-m", "mdh_pipeline.history", "--export-only",
            "--data-dir", "data/history", "--selection", "data/selection/news.json",
            "--reviewed-news", "data/reviewed/news_dossiers.json",
            "--exceptions", "data/selection/news_exceptions.json",
            "--excluded-ids", str(excluded), "--output", NEWS_ARCHIVE,
        ], cwd=repository, check=True, capture_output=True, text=True)

    try:
        recovered = rebase_generated_archive(args.repository, rebuild)
    except (PublicationRebaseError, OSError, UnicodeError) as error:
        code = str(error) if isinstance(error, PublicationRebaseError) else "publication_git_unavailable"
        raise SystemExit(code) from None
    print("publication_archive_rebuilt" if recovered else "publication_rebase_clean")


if __name__ == "__main__":
    main()
