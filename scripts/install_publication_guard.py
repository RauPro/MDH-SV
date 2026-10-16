"""Install a reviewed pre-push guard independently of the checked-out branch."""
from __future__ import annotations

import argparse
import hashlib
import os
import shlex
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MARKER = "# MDH-SV public history guard v1"
FILES = (
    "scripts/publication_guard.py", "scripts/scan_public_history.py",
    ".security/publication-history.json", ".security/gitleaks.toml",
    ".security/gitleaks-version.json",
)


def git(repo: Path, *args: str) -> str:
    result = subprocess.run(["git", "-C", str(repo), *args], text=True,
                            capture_output=True, check=False)
    if result.returncode:
        raise ValueError("git_guard_installation_failed")
    return result.stdout.strip()


def install(repo: Path, binary: Path) -> Path:
    repo = repo.resolve()
    binary = binary.resolve(strict=True)
    if not binary.is_file() or not os.access(binary, os.X_OK):
        raise ValueError("gitleaks_binary_unavailable")
    configured = subprocess.run(["git", "-C", str(repo), "config", "--get", "core.hooksPath"],
                                text=True, capture_output=True, check=False)
    if configured.returncode != 1:
        raise ValueError("existing_hooks_path_requires_explicit_integration")
    common = Path(git(repo, "rev-parse", "--git-common-dir"))
    common = (common if common.is_absolute() else repo / common).resolve(strict=True)
    hook = common / "hooks/pre-push"
    hook.parent.mkdir(exist_ok=True)
    if hook.is_symlink() or (hook.exists() and MARKER not in hook.read_text()):
        raise ValueError("existing_pre_push_hook_requires_explicit_integration")
    store = common / "mdh-publication-guard"
    if store.is_symlink():
        raise ValueError("unsafe_guard_directory")
    store.mkdir(exist_ok=True)
    contents = {}
    digest = hashlib.sha256()
    for name in FILES:
        source = ROOT / name
        if source.is_symlink() or not source.is_file():
            raise ValueError("guard_source_unavailable")
        contents[name] = source.read_bytes()
        digest.update(name.encode() + b"\0" + contents[name] + b"\0")
    target = store / digest.hexdigest()
    with tempfile.TemporaryDirectory(prefix="mdh-guard-stage-", dir=common) as folder:
        stage = Path(folder)
        for name, content in contents.items():
            destination = stage / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(content)
        if target.exists() or target.is_symlink():
            if target.is_symlink() or not target.is_dir():
                raise ValueError("unsafe_guard_directory")
            files = set()
            for path in target.rglob("*"):
                if path.is_symlink():
                    raise ValueError("unsafe_guard_file")
                if path.is_file():
                    files.add(path.relative_to(target).as_posix())
            if files != set(FILES) or any((target / name).read_bytes() != value
                                         for name, value in contents.items()):
                raise ValueError("installed_guard_snapshot_changed")
        else:
            # Publish the complete immutable snapshot before changing the hook.
            os.replace(stage, target)
            stage.mkdir()  # TemporaryDirectory still owns its original pathname.
    command = (f"exec {shlex.quote(sys.executable)} -I -S "
               f"{shlex.quote(str(target / 'scripts/publication_guard.py'))} "
               f"--gitleaks {shlex.quote(str(binary))} pre-push -- \"$@\"\n")
    descriptor, temporary = tempfile.mkstemp(prefix=".pre-push-", dir=hook.parent)
    try:
        with os.fdopen(descriptor, "w") as stream:
            stream.write("#!/bin/sh\n" + MARKER + "\n" + command)
        os.chmod(temporary, 0o755)
        os.replace(temporary, hook)
    finally:
        Path(temporary).unlink(missing_ok=True)
    return hook


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    parser.add_argument("--gitleaks", required=True, type=Path)
    args = parser.parse_args()
    try:
        install(args.repo, args.gitleaks)
    except (OSError, ValueError, UnicodeError):
        print("guard_installation_failed; existing hooks were not intentionally replaced", file=sys.stderr)
        return 2
    print("Installed branch-independent public history pre-push guard.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
