"""Verify a prepared checkout locally. --audit also queries npm and Python advisory services.

The default checks need no credentials, external articles, model API or deployment.
Exports are rebuilt in a temporary directory, leaving checkout data untouched.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import bootstrap
import install_gitleaks

ROOT = Path(__file__).resolve().parents[1]
NEWS_INPUTS = (
    "apps/web/public/archive/news.json",
    "apps/web/public/archive/dossiers.json",
    "apps/web/public/archive/locations.json",
    "data/automated/news_dossiers.json",
    "data/reviewed/news_dossiers.json",
    "data/selection/news_exceptions.json",
)


def scanner_binary(root: Path) -> Path:
    """Authenticate/rebuild the already-downloaded scanner without network calls."""
    manifest = install_gitleaks.load_manifest(root / ".security/gitleaks-version.json")
    platform = install_gitleaks.current_platform()
    directory = root / ".local-quarantine/tools/gitleaks" / manifest["version"] / platform
    archive = directory / ".release.tar.gz"
    if not archive.is_file():
        raise bootstrap.SetupError("Pinned Gitleaks is missing; run scripts/bootstrap.py first.")
    install_gitleaks.authenticate_archive(archive, manifest["artifacts"][platform]["sha256"])
    with tempfile.TemporaryDirectory(prefix=".check-", dir=directory) as temporary:
        binary = Path(temporary) / "gitleaks"
        install_gitleaks.extract_binary(archive, binary)
        binary.chmod(0o755)
        binary.replace(directory / "gitleaks")
    return directory / "gitleaks"


def verify_exports(root: Path, python: Path, node: str, environment: dict) -> None:
    with tempfile.TemporaryDirectory(prefix="mdh-check-data-") as temporary:
        staging = Path(temporary).resolve()
        output = staging / "apps/web/public/data"
        bootstrap.run([
            str(python), "-c",
            ("import sys; from mdh_pipeline.publication import export_catalog; "
             "export_catalog(sys.argv[1], sys.argv[2])"),
            str(root / "data/reviewed/publications.json"), str(output),
        ], root=root, env=environment)
        expected = root / "apps/web/public/data"
        actual_files = {path.name for path in output.iterdir() if path.is_file()}
        expected_files = {path.name for path in expected.iterdir() if path.is_file()}
        if actual_files != expected_files or any(
            (output / name).read_bytes() != (expected / name).read_bytes() for name in actual_files
        ):
            raise bootstrap.SetupError("Catalogue exports differ; regenerate them and review the diff.")
        for name in NEWS_INPUTS:
            destination = staging / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(root / name, destination)
        bootstrap.run([node, "apps/web/scripts/sync-news-publication.mjs", "--root", str(staging)],
                      root=root, env=environment)
        if any((staging / name).read_bytes() != (root / name).read_bytes() for name in NEWS_INPUTS):
            raise bootstrap.SetupError("News exports differ; reconcile them and review the diff.")
    print("Publication exports match; checkout data was not modified.", flush=True)


def check(root: Path = ROOT, *, audit: bool = False) -> None:
    python = bootstrap.environment_python(root)
    if not python.is_file() or not (root / ".venv/pyvenv.cfg").is_file():
        raise bootstrap.SetupError("Prepared .venv is missing; run scripts/bootstrap.py first.")
    node, npm = bootstrap.validate_runtime(root, python)
    if not all((root / path).is_dir() for path in ("node_modules", "apps/web/node_modules")):
        raise bootstrap.SetupError("npm dependencies are missing; run scripts/bootstrap.py first.")
    binary = scanner_binary(root)
    environment = os.environ.copy()
    environment["GITLEAKS_BIN"] = str(binary)
    environment["PATH"] = str(python.parent) + os.pathsep + environment.get("PATH", "")
    environment["PIP_DISABLE_PIP_VERSION_CHECK"] = "1"
    commands = [
        [str(python), "-m", "pip", "check"],
        [str(python), "scripts/publication_guard.py", "check", "--ref", "HEAD"],
        [str(python), "scripts/configure_instance.py", "--check"],
        [str(python), "scripts/audit_release.py"],
        [str(python), "-m", "ruff", "check", "--config", "pyproject.toml", "."],
        [npm, "run", "lint"],
        [str(python), "-m", "pytest", "-q"],
    ]
    for command in commands:
        bootstrap.run(command, root=root, env=environment)
    verify_exports(root, python, node, environment)
    for command in (
        [npm, "run", "test:api"],
        [npm, "--prefix", "apps/web", "test"],
        [npm, "--prefix", "apps/web", "run", "lint"],
        [npm, "--prefix", "apps/web", "run", "build"],
        [npm, "--prefix", "apps/web", "run", "test:browser"],
    ):
        bootstrap.run(command, root=root, env=environment)
    if audit:
        for lock in ("requirements.lock.txt", "ops/review-queue/requirements.txt"):
            bootstrap.run([str(python), "-m", "pip_audit", "--strict", "--no-deps",
                           "--disable-pip", "--progress-spinner", "off", "-r", lock],
                          root=root, env=environment)
        for command in ([npm, "audit", "--audit-level=moderate"],
                        [npm, "--prefix", "apps/web", "audit", "--audit-level=moderate"]):
            bootstrap.run(command, root=root, env=environment)
    print("Checkout verification passed.", flush=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audit", action="store_true", help="Also query npm and Python advisory services")
    args = parser.parse_args(argv)
    try:
        check(audit=args.audit)
    except (bootstrap.SetupError, install_gitleaks.InstallError, OSError,
            subprocess.CalledProcessError) as error:
        if isinstance(error, bootstrap.SetupError):
            message = str(error)
        elif isinstance(error, install_gitleaks.InstallError):
            message = f"Pinned Gitleaks validation failed: {error}"
        else:
            message = "Verification failed; see the step above."
        print(message, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
